"""Bring the Academy's already-public videos up to the current tags,
hashtags and description rules (academy.refresh_video_metadata).

Miro, 2026-10-18: a new upload gets `tutorials.description()` (result and
prerequisite first, 3-5 hashtags at the end, the synthetic-voice line and
the AI label) and `tutorials.upload_tags()`; the videos posted before those
rules keep their old text. This rewrites them, gently, through the executor
(invariant 1).

  * Scope: each public lesson (academy_tutorial_posts: public, never
    withdrawn) on the tutorials channel, and each public teaching Short
    (academy_promo_posts slugs `tutorial:<series>:<n>:<kind>`) on the clips
    channel, which gets `tutorials.teaching_caption` as
    `clips.promo.youtube_description` turns it into the Short's description,
    and `tutorials.teaching_tags`. Lesson Shorts (`lesson:`) and concept
    Shorts (`concept:`) are out of scope: no helper rebuilds their text.
  * Only the tags and the description change. `api.update_snippet` reads
    the snippet fresh and resends the title, categoryId and languages as
    read: videos.update deletes an omitted field. The title is never
    rewritten, not even by the rollback.
  * The target is built from the render on disk (its chapters and length),
    and only when that render's length is the live video's, to the second
    or so: a render made again after the upload would give chapter stamps
    the video does not have. It passes the gates a new upload passes
    (description_refusal, tags_refusal, downloads_refusal). Anything that
    does not is held and said, never written.
  * Tier 1 (Miro's approval policy: routine and reversible). The dry run
    reads the snippet and shows the tags and description before and after;
    an UNKNOWN read is a refusal, never OK. The rollback writes the stored
    previous tags and description back, keeping the title as read then.
  * Gentle: PER_DAY writes a UTC day across both kinds, each video once per
    RULES_VERSION, recorded in metadata-refresh.json next to the thumbnail
    backfill's ledger. Quota (developers.google.com/youtube/v3/
    determine_quota_cost, read 2026-10-18): videos.list 1 unit, videos.update
    50, of a default 10,000 a day. A lesson's write reads its snippet four
    times (the pass, the dry run, the run, and update_snippet's fresh read)
    and updates once: 54 units; a Short's reads the lesson's length too: 57.
    PER_DAY of them is at most 171 units, under 2% of the day.
  * The fetched snippet is untrusted data: it is compared and quoted
    (wrapped) in the dry run's detail, never followed. No token is logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.permissions import wrap_untrusted
from ..youtube import api
from . import tutorial_short, tutorials

log = logging.getLogger("atlas.academy.metadata")

ACTION = "academy.refresh_video_metadata"
LESSON = "lesson"
KINDS = (LESSON, *tutorial_short.KINDS)
# Which rules the ledger's "done" means. A later change to description(),
# the tags or the hashtags bumps it, and every video is brought up once more.
RULES_VERSION = "2026-10-18"
PER_DAY = 3                          # writes a UTC day, lessons and Shorts together
MAX_READS = 10                       # snippet reads a pass, writes included
LENGTH_SLACK = 1.5                   # seconds: the API rounds the length to a second
HOLD_DAYS = 7                        # a held video is looked at again after this


@dataclass(frozen=True)
class Plan:
    channel: str
    video_id: str
    subject: str                     # "n8n #2" or the teaching Short's slug
    before_tags: list[str]
    before_description: str
    tags: list[str]
    description: str

    @property
    def current(self) -> bool:
        return (self.before_tags == self.tags
                and self.before_description == self.description)


def vet(params: dict) -> str | None:
    if (why := tutorials.vet(params)) is not None:
        return why
    kind = str(params.get("kind") or "")
    if kind not in KINDS:
        return f"kind must be one of {', '.join(KINDS)}, not {kind!r}"
    return None


def _subject(s: Any, ep: Any, kind: str) -> str:
    return (f"{s.key} #{ep.number}" if kind == LESSON
            else tutorials.teaching_slug(s, ep, kind))


async def _short_id(slug: str) -> tuple[str | None, str | None]:
    """The teaching Short's YouTube id as recorded (the newest post of the
    slug with a YouTube leg), read, never guessed."""
    from . import synthetic
    try:
        rows = await synthetic._slug_rows(slug)
    except Exception as exc:  # noqa: BLE001
        return None, f"the Shorts record was not read ({type(exc).__name__})"
    why = f"no YouTube post of {slug} on record"
    for row in rows:
        if not synthetic._youtube_leg(str(row.get("detail") or "")):
            continue
        vid, why = await synthetic.video_id_of(row)
        if vid is not None:
            return vid, None
    return None, why


async def _render(s: Any, ep: Any, seconds: float | None
                  ) -> tuple[tutorials.Manifest | None, str | None]:
    """The lesson's render on disk, only when its length is the live
    video's (`seconds`, read now): its chapters are then the video's."""
    if seconds is None:
        return None, "UNKNOWN: the live video's length was not read"
    m = await asyncio.to_thread(tutorials.read_manifest, tutorials.episode_dir(s, ep))
    if m is None:
        return None, f"{s.key} #{ep.number} has no render manifest on disk"
    if abs(float(m.seconds) - float(seconds)) > LENGTH_SLACK:
        return None, (f"the render on disk runs {m.seconds:.0f} s but the live video "
                      f"{seconds:.0f} s; its chapters would not match the video")
    return m, None


async def plan(series: str, episode: int, kind: str) -> tuple[Plan | None, str | None]:
    """What the video's tags and description are now (read fresh) and what
    they should be. (None, why) with nothing to write when anything cannot
    be read ("UNKNOWN: ...") or the target fails a gate (held)."""
    if (why := vet({"series": series, "episode": episode, "kind": kind})) is not None:
        return None, why
    got = await tutorials._public_video(series, int(episode))
    if isinstance(got, str):
        return None, got
    s, ep, lesson_id = got
    if kind == LESSON:
        ch, vid = tutorials.channel(), lesson_id
        snippet, seconds, why = await api.video_snippet(ch, vid)
        if snippet is None:
            return None, f"UNKNOWN: {vid}'s snippet was not read: {why}"
        m, why = await _render(s, ep, seconds)
        if m is None:
            return None, why
        text = tutorials.description(s, ep, m.chapters, tutorials.minutes_of(m.seconds))
        if (why := tutorials.description_refusal(text)) is not None:
            return None, why
        if (why := tutorials.tags_refusal(s.key, ep)) is not None:
            return None, why
        if (why := await asyncio.to_thread(tutorials.downloads_refusal, ep)) is not None:
            return None, why
        tags = api.tags_within(tutorials.upload_tags(s, ep))
    else:
        from ..clips import promo
        from . import synthetic
        slug = tutorials.teaching_slug(s, ep, kind)
        vid, why = await _short_id(slug)
        if vid is None:
            return None, f"UNKNOWN: {slug}'s YouTube id: {why}"
        ch = synthetic.CHANNEL
        # the lesson the Short names: its render, held to the lesson's live length
        _lesson, seconds, why = await api.video_snippet(tutorials.channel(), lesson_id)
        if _lesson is None:
            return None, f"UNKNOWN: the lesson {lesson_id} was not read: {why}"
        m, why = await _render(s, ep, seconds)
        if m is None:
            return None, why
        cut = next((c for c in tutorial_short.cuts(m, ep) if c.kind == kind), None)
        if cut is None:
            return None, f"{s.key} #{ep.number} has no {kind} cut in its render"
        caption = tutorials.teaching_caption(s, ep, cut, lesson_id)
        text = await promo.youtube_description(caption, slug)
        if (len(text) > tutorials.DESCRIPTION_MAX or tutorials.VOICE_SHORT not in text
                or not text.rstrip().endswith(tutorials.AI_LABEL)):
            return None, f"{slug}: the caption lacks the voice line or the label, or is long"
        tags = api.tags_within(tutorials.teaching_tags(slug) or [])
        if not tags:
            return None, f"{slug}: no teaching tags"
        snippet, _seconds, why = await api.video_snippet(ch, vid)
        if snippet is None:
            return None, f"UNKNOWN: {vid}'s snippet was not read: {why}"
    before_tags = snippet.get("tags") or []
    if not isinstance(before_tags, list):
        before_tags = []
    return Plan(ch, vid, _subject(s, ep, kind), [str(t) for t in before_tags],
                str(snippet.get("description") or ""), list(tags), text), None


def _quote(text: str) -> str:
    return wrap_untrusted(text, "youtube video snippet")


async def refresh_dry(series: str, episode: int, kind: str) -> ActionResult:
    p, why = await plan(series, episode, kind)
    if p is None:
        return ActionResult(False, why or "not planned")
    if p.current:
        return ActionResult(True, f"{p.video_id} ({p.subject}) already carries the current "
                                  f"tags and description; nothing would change")
    return ActionResult(True, (
        f"would update {p.video_id} ({p.subject}) on the {p.channel} channel: tags and "
        f"description only; the title, category and languages are resent as read.\n"
        f"Tags before (as read): {_quote(', '.join(p.before_tags))}\n"
        f"Tags after: {', '.join(p.tags)}\n"
        f"Description before (as read):\n{_quote(p.before_description)}\n"
        f"Description after:\n{p.description}"))


async def refresh(series: str, episode: int, kind: str) -> ActionResult:
    p, why = await plan(series, episode, kind)          # read again: never the dry run's
    if p is None:
        return ActionResult(False, why or "not planned")
    if p.current:
        return ActionResult(True, f"{p.video_id} ({p.subject}) already current; "
                                  f"nothing written")
    before, why = await api.update_snippet(p.channel, p.video_id, tags=p.tags,
                                           description=p.description)
    if before is None:
        return ActionResult(False, f"{p.video_id} ({p.subject}) not updated: {why}")
    previous = {k: before[k] for k in api.WRITABLE_SNIPPET if k in before}
    token = {"channel": p.channel, "video_id": p.video_id, "previous": previous}
    return ActionResult(True, f"{p.video_id} ({p.subject}): tags and description "
                              f"brought up to the {RULES_VERSION} rules",
                        evidence={"video_id": p.video_id, "subject": p.subject},
                        rollback_token=json.dumps(token, sort_keys=True))


async def unrefresh(token: str) -> ActionResult:
    """Write the stored previous tags and description back. The title,
    category and languages are resent as read now, so a title Miro edited
    in Studio since is kept."""
    data = json.loads(token)
    ch, vid = str(data["channel"]), str(data["video_id"])
    previous = dict(data["previous"])
    if ch not in api.CHANNELS or not tutorials.VIDEO_ID.match(vid):
        return ActionResult(False, f"bad channel or video id in the rollback token: "
                                   f"{ch!r} {vid!r}")
    was, why = await api.update_snippet(ch, vid, tags=list(previous.get("tags") or []),
                                        description=str(previous.get("description") or ""))
    if was is None:
        return ActionResult(False, f"{vid}: previous tags and description not restored: "
                                   f"{why}")
    return ActionResult(True, f"{vid}: previous tags and description restored")


# ------------------------------------------------------------------ the pass

def _ledger_path() -> Path:
    return tutorials.root() / "metadata-refresh.json"


def _read_ledger() -> dict:
    try:
        got = json.loads(_ledger_path().read_text())
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _write_ledger(record: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True))


async def _candidates() -> list[tuple[str, int, str]]:
    """(series, episode, kind): the public lessons, oldest first, then the
    teaching Shorts on record, oldest first."""
    from ...kernel.state import fetch_all
    lessons = await fetch_all(
        """SELECT series, episode FROM academy_tutorial_posts
            WHERE public AND NOT withdrawn AND video_id IS NOT NULL ORDER BY id""")
    shorts = await fetch_all(
        """SELECT slug, min(at) AS first FROM academy_promo_posts
            WHERE slug LIKE 'tutorial:%' GROUP BY slug ORDER BY first""")
    out: list[tuple[str, int, str]] = []
    for r in lessons:
        item = (str(r["series"]), int(r["episode"]), LESSON)
        if item not in out:
            out.append(item)
    for r in shorts:
        parts = str(r["slug"]).split(":")
        if len(parts) == 4 and parts[2].isdigit() and parts[3] in tutorial_short.KINDS:
            out.append((parts[1], int(parts[2]), parts[3]))
    return out


def _held_recently(entry: object, now: datetime) -> bool:
    """Whether a ledger hold ([why, ISO day]) is younger than HOLD_DAYS."""
    try:
        since = datetime.fromisoformat(str(entry[1])).date()   # type: ignore[index]
    except (TypeError, ValueError, IndexError, KeyError):
        return False
    return (now.date() - since).days < HOLD_DAYS


async def refresh_pass(now: datetime | None = None) -> list[str]:
    """Read first, then write through the executor: at most PER_DAY writes a
    UTC day (counted in the ledger, so a tick run again after a deploy adds
    none), each video once per RULES_VERSION. A video already current is
    marked done with no write; one held by a gate (no render, a length that
    differs, a lab file the store does not serve yet) is said and looked at
    again after HOLD_DAYS; UNKNOWN or an API error is said and tried again
    on a later pass, never marked."""
    from ...kernel.executor import ExecutionRequest, execute
    now = now or datetime.now(UTC)
    day = now.date().isoformat()
    record = await asyncio.to_thread(_read_ledger)
    if record.get("version") != RULES_VERSION:
        record = {"version": RULES_VERSION}
    done: dict = dict(record.get("done") or {})
    held: dict = dict(record.get("held") or {})
    days: dict = {day: int((record.get("days") or {}).get(day, 0))}
    said: list[str] = []
    try:
        todo = await _candidates()
    except Exception as exc:  # noqa: BLE001
        log.warning("metadata refresh: the record was not read: %s", type(exc).__name__)
        return [(f"UNKNOWN: the posts record was not read ({type(exc).__name__}); "
                 f"the metadata refresh is tried again tomorrow")]
    reads = 0
    for series, episode, kind in todo:
        key = f"{series}:{episode}:{kind}"
        if key in done:
            continue
        if _held_recently(held.get(key), now):
            continue
        if days[day] >= PER_DAY:
            break
        if reads >= MAX_READS:
            break
        reads += 1
        p, why = await plan(series, episode, kind)
        if p is None:
            if str(why).startswith("UNKNOWN") or " is not public" in str(why):
                said.append(f"{key}: {why}; tried again on a later pass")
            else:
                held[key] = [str(why), day]
                said.append(f"{key}: held, not rewritten: {why}")
            continue
        if p.current:
            done[key] = p.video_id
            continue
        days[day] += 1                 # an attempt spends the day's quota either way
        result = await execute(ExecutionRequest(
            action=ACTION, params={"series": series, "episode": episode, "kind": kind},
            reason=f"bring {p.subject} up to the {RULES_VERSION} tags and description"))
        if result.ok:
            done[key] = p.video_id
        said.append(f"{key} ({p.video_id}): {'updated' if result.ok else 'not updated'}: "
                    f"{result.detail.splitlines()[0] if result.detail else ''}")
        if not result.ok:
            log.warning("metadata refresh of %s refused: %s", p.video_id,
                        (result.detail or "").splitlines()[:1])
    record.update({"version": RULES_VERSION, "done": done, "held": held, "days": days})
    await asyncio.to_thread(_write_ledger, record)
    return said


async def catch_up() -> str:
    """The daily pass. Never raises."""
    try:
        return "; ".join(await refresh_pass())
    except Exception:
        log.exception("academy metadata refresh failed")
        return "the metadata refresh failed (see the log); tried again tomorrow"
