"""The AI flag on the Academy's own Shorts (academy.flag_synthetic_short).

Miro, 2026-10-17: keep the British male AI voice, disclosed, and "yes add
the AI flag for Shorts too". A tutorial carries `status.containsSyntheticMedia`
from its own upload (tutorials.publish_tutorial). The Academy's Shorts --
teaching Shorts, lesson Shorts, concept Shorts -- go out through Postiz
(clips.publish_promo), whose YouTube settings have no such field, so this
sets it afterwards with videos.update on the clips channel.

  * Scope: only Shorts this lane posts, by their recorded slug
    (`tutorial:`, `lesson:`, `concept:` in academy_promo_posts); never the
    store promo reels or the engine's reels.
  * The video id is read, never guessed: the YouTube leg's own record (the
    id and URL Postiz returned, which must agree), else Postiz's post list
    by the post id recorded, matched on its YouTube integration. Unknown
    means skipped and said, and tried again on the next pass.
  * The write resends every writable status field as just read
    (api.set_synthetic): videos.update deletes an omitted one.
  * Tier 1 (Miro's approval policy: routine and reversible): the dry run
    reads the status and says what would change; an UNKNOWN read is a
    refusal, never OK. The rollback sets the flag back to what it was.
  * It runs through the executor only: once after each teaching Short goes
    out (tutorials.teaching_pass) and as a daily catch-up over the last 14
    days (tutorials.teaching_tick). A failure is logged and never holds a post.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult
from ..youtube import api

log = logging.getLogger("atlas.academy.synthetic")

ACTION = "academy.flag_synthetic_short"
CHANNEL = api.CLIPS_CHANNEL           # the channel Postiz posts the Shorts to
SCOPE = ("tutorial:", "lesson:", "concept:")
LOOKBACK_DAYS = 14
# Two Shorts a day go out (CLIPS_PROMO_PER_DAY); four flags a pass drains a
# backlog in days. The hourly cap is one more: the teaching tick runs
# after_post (at most one) and then the catch-up (at most PASS_MAX).
PASS_MAX = 4
MAX_PER_HOUR = PASS_MAX + 1
# Videos this process flagged in the last hour: a status read can lag the
# write, and the catch-up right after after_post must not flag one twice.
_RECENT = timedelta(hours=1)
_flagged: dict[str, datetime] = {}
FLAG = "containsSyntheticMedia"
YT_LEG = "YouTube clips (Short)"      # clips.promo.NAMES["youtube"]
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SAID_ID = re.compile(r"YouTube id ([A-Za-z0-9_-]{11})\b")
_URL_ID = re.compile(r"(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)"
                     r"([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_POSTIZ_ID = re.compile(r"Postiz post (\w+)")


def in_scope(slug: str) -> bool:
    return bool(slug) and str(slug).startswith(SCOPE)


def vet(params: dict) -> str | None:
    slug = str(params.get("slug") or "")
    if not in_scope(slug):
        return (f"{slug!r} is out of scope: only the Academy's own Shorts "
                f"({', '.join(SCOPE)})")
    if not VIDEO_ID.fullmatch(str(params.get("video_id") or "")):
        return "video_id must be an 11-character YouTube video id"
    return None


def _youtube_leg(detail: str) -> str:
    """The YouTube leg's part of a publish_promo detail ("... · YouTube clips
    (Short): posted — Postiz post N, YouTube id X, ..."), or ""."""
    return next((seg for seg in str(detail or "").split(" · ") if YT_LEG in seg), "")


def _ids(text: str) -> set[str]:
    return set(_SAID_ID.findall(text)) | set(_URL_ID.findall(text))


async def video_id_of(row: dict, posts: list[dict] | None = None
                      ) -> tuple[str | None, str | None]:
    """The Short's YouTube id, or (None, why). `posts`: Postiz's list for the
    window, when the caller has read it already; None reads it here."""
    leg = _youtube_leg(str(row.get("detail") or ""))
    if not leg:
        return None, "no YouTube leg recorded"
    said = _ids(leg)
    if len(said) == 1:
        return said.pop(), None
    if len(said) > 1:
        return None, "the record names two different YouTube ids"
    m = _POSTIZ_ID.search(leg)
    if m is None:
        return None, "the YouTube leg names no Postiz post"
    if posts is None:
        from ..content import postiz
        at = row.get("at") or datetime.now(UTC)
        try:
            posts = await postiz.posts_between(at - timedelta(hours=1),
                                               at + timedelta(days=2))
        except Exception as exc:  # noqa: BLE001
            return None, f"Postiz could not be read ({type(exc).__name__})"
    for p in posts:
        ident = str((p.get("integration") or {}).get("providerIdentifier") or "")
        if str(p.get("id")) != m.group(1) or "youtube" not in ident:
            continue
        release = str(p.get("releaseId") or "").strip()
        found = _ids(str(p.get("releaseURL") or ""))
        if VIDEO_ID.fullmatch(release):
            found.add(release)
        if len(found) == 1:
            return found.pop(), None
        if len(found) > 1:
            return None, "Postiz names two different YouTube ids for the post"
    return None, f"Postiz post {m.group(1)}: YouTube has returned no id yet"


async def _slug_rows(slug: str) -> list[dict]:
    """Every post of `slug` on record, newest first: a render re-run for one
    leg (clips.promo.recently_posted) records a newer row with no YouTube leg."""
    from ...kernel.state import fetch_all
    found = await fetch_all(
        """SELECT slug, media_id, detail, at FROM academy_promo_posts
            WHERE slug = $1 ORDER BY at DESC""", slug)
    return [dict(r) for r in found]


async def _recent_rows(since: datetime) -> list[dict]:
    from ...kernel.state import fetch_all
    found = await fetch_all(
        """SELECT slug, media_id, detail, at FROM academy_promo_posts
            WHERE at >= $1 ORDER BY at""", since)
    return [dict(r) for r in found]


async def _check(slug: str, video_id: str) -> str | None:
    """None when `video_id` is the Short recorded for `slug`, read now."""
    if (why := vet({"slug": slug, "video_id": video_id})) is not None:
        return why
    rows = [r for r in await _slug_rows(slug)
            if _youtube_leg(str(r.get("detail") or ""))]
    if not rows:
        return f"no YouTube post of {slug} on record"
    said = []
    for row in rows:
        vid, why = await video_id_of(row)
        if vid == video_id:
            return None
        said.append(vid or f"UNKNOWN: {why}")
    return (f"{video_id} is not the Short recorded for {slug} "
            f"({'; '.join(said)})")


def _kept(status: dict[str, Any]) -> str:
    return ", ".join(f"{k}={status[k]}" for k in api.WRITABLE_STATUS if k in status)


async def flag_dry(slug: str, video_id: str) -> ActionResult:
    if (why := await _check(slug, video_id)) is not None:
        return ActionResult(False, why)
    status, why = await api.video_status(CHANNEL, video_id)
    if status is None:
        return ActionResult(False, f"UNKNOWN: {video_id}'s status was not read: {why}")
    if status.get(FLAG) is True:
        return ActionResult(True, f"{video_id} already carries {FLAG}; nothing would "
                                  f"change")
    return ActionResult(True, f"would set {FLAG} false -> true on {video_id} ({slug}), "
                              f"keeping {_kept(status)}")


async def flag(slug: str, video_id: str) -> ActionResult:
    if (why := await _check(slug, video_id)) is not None:
        return ActionResult(False, why)
    status, why = await api.video_status(CHANNEL, video_id)
    if status is None:
        return ActionResult(False, f"UNKNOWN: {video_id}'s status was not read: {why}")
    if status.get(FLAG) is True:
        return ActionResult(True, f"{video_id} already carries {FLAG}; nothing written")
    previous, why = await api.set_synthetic(CHANNEL, video_id, True)
    if previous is None:
        return ActionResult(False, f"{video_id} not flagged: {why}")
    return ActionResult(True, f"{video_id} ({slug}) now carries {FLAG}",
                        evidence={"video_id": video_id, "previous": previous},
                        rollback_token=json.dumps({"video_id": video_id,
                                                   "previous": previous},
                                                  sort_keys=True))


async def unflag(token: str) -> ActionResult:
    data = json.loads(token)
    vid, previous = str(data["video_id"]), bool(data["previous"])
    if not VIDEO_ID.fullmatch(vid):
        return ActionResult(False, f"bad video id in the rollback token: {vid!r}")
    was, why = await api.set_synthetic(CHANNEL, vid, previous)
    if was is None:
        return ActionResult(False, f"{vid}: {FLAG} not set back: {why}")
    return ActionResult(True, f"{vid}: {FLAG} set back to {str(previous).lower()}")


async def flag_pass(only: str | None = None) -> list[str]:
    """Read first, then flag: each Academy Short of the last LOOKBACK_DAYS
    (or only `only`) whose id is known and whose status reads without the
    flag, at most PASS_MAX, each through the executor. What is not read is
    said as UNKNOWN and tried on the next pass. Raises only if the caller's
    hooks do not catch it (after_post, catch_up do)."""
    from ...kernel.executor import ExecutionRequest, execute
    now = datetime.now(UTC)
    try:
        rows = await _recent_rows(now - timedelta(days=LOOKBACK_DAYS))
    except Exception as exc:  # noqa: BLE001
        log.warning("academy_promo_posts not read: %s", type(exc).__name__)
        return [(f"UNKNOWN: the Shorts record was not read ({type(exc).__name__}); "
                 f"the AI flag is tried again on the next pass")]
    rows = [r for r in rows if in_scope(str(r.get("slug") or ""))
            and (only is None or r.get("slug") == only)]
    said: list[str] = []
    posts: list[dict] | None = None
    postiz_down: str | None = None
    seen: set[str] = set()
    acted = 0
    for row in rows:
        slug = str(row["slug"])
        leg = _youtube_leg(str(row.get("detail") or ""))
        if leg and not _ids(leg) and posts is None:    # read Postiz once a pass
            from ..content import postiz
            try:
                posts = await postiz.posts_between(
                    now - timedelta(days=LOOKBACK_DAYS + 1), now + timedelta(hours=1))
            except Exception as exc:  # noqa: BLE001
                log.warning("Postiz not read: %s", type(exc).__name__)
                posts, postiz_down = [], f"Postiz could not be read ({type(exc).__name__})"
        vid, why = await video_id_of(row, posts=posts or [])
        if vid is None and leg and not _ids(leg) and postiz_down:
            why = postiz_down
        if vid is None:
            if "no YouTube leg" not in str(why):
                said.append(f"{slug}: YouTube id UNKNOWN ({why}); tried again on the "
                            f"next pass")
            continue
        if vid in seen or now - _flagged.get(vid, now - 2 * _RECENT) < _RECENT:
            continue
        seen.add(vid)
        status, why = await api.video_status(CHANNEL, vid)
        if status is None:
            said.append(f"{slug} ({vid}): UNKNOWN, the status was not read ({why}); "
                        f"tried again on the next pass")
            continue
        if status.get(FLAG) is True:
            continue
        if acted >= PASS_MAX:
            said.append(f"{slug} ({vid}): held for the next pass ({PASS_MAX} a pass)")
            continue
        acted += 1
        result = await execute(ExecutionRequest(
            action=ACTION, params={"slug": slug, "video_id": vid},
            reason=f"the AI flag on the Academy Short {slug}"))
        if result.ok:
            _flagged[vid] = now
        said.append(f"{slug} ({vid}): {'flagged' if result.ok else 'not flagged'}: "
                    f"{result.detail}")
        if not result.ok:
            log.warning("academy AI flag on %s refused: %s", vid, result.detail)
    return said


async def after_post(slug: str) -> str:
    """Right after a teaching Short goes out. Never raises, never blocks it:
    an id Postiz has not returned yet is the catch-up's."""
    try:
        return "; ".join(await flag_pass(only=slug))
    except Exception:
        log.exception("academy AI flag after %s failed", slug)
        return f"the AI flag on {slug} failed (see the log); tried again tomorrow"


async def catch_up() -> str:
    """The daily pass over the last LOOKBACK_DAYS. Never raises."""
    try:
        return "; ".join(await flag_pass())
    except Exception:
        log.exception("academy AI flag catch-up failed")
        return "the AI flag catch-up failed (see the log); tried again tomorrow"
