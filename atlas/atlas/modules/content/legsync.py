"""Leg sync: an AI reel that posted to some platforms and not others gets
the missing leg later (Miro, 2026-09-29).

Media 12 ("Emergency Room: Code Chaos -- Part 2") auto-posted at 07:32 UTC:
TikTok took it, the Instagram (stories) leg was refused with Graph HTTP 401
(an expired token, fixed since). `content.autopublish_reel` succeeds when
ANY leg posts and nothing retried the one that did not, so Part 2 was
missing on Instagram while Part 3 would post there -- the series out of
order. This closes that gap, for the stories account's lane only
(calendar, series, imports, football, course: every `content_media`
render). Campaign clips never come here: they post through
`clips.publish_clip*` (their own table, one platform per clip by campaign
rule), never through `content.publish_reel` / `content.autopublish_reel`,
which are the only runs this reads.

**What a gap is.** For every reel whose publish run succeeded in the last
`WINDOW_HOURS` (the run's `execute.start` / `execute.finish` rows in
`actions_log`, which carry the exact caption that posted and each leg's
`{status, detail}`), each of the three legs is one of:

  * *present* -- a `reel_posts` row (Instagram, TikTok), the run's own
    evidence says it posted (the only record Facebook has: it is never in
    `reel_posts`), or a settled catch-up claim in `effect_claims`;
  * *not configured then* -- skipped, never back-filled: switching a
    platform on later must not post four days of old reels to it;
  * a *gap*, with the original refusal and whether it is retryable:
    credentials (HTTP 401/403, an expired token), the platform's 5xx, a
    timeout or an unreachable host, a rate limit or a spent hourly
    allowance. A content refusal (the guard, the AI label, a caption rule,
    Instagram rejecting the file) is NOT retried, and neither is a leg that
    was paused -- a pause is Miro's own stop.

**The catch-up** is `content.post_missing_leg` (Tier 1, through the
executor: dry run, audit, watch-only, pause, rate limits), params
`media_id` and `platform` only. It re-derives the gap itself, so nothing
it is handed can make it post a leg that exists, a leg that was refused on
content, or a caption other than the one that posted. It claims
`effect_claims(content.post_missing_leg, "<media>:<platform>")` before the
post: a claim already settled is "posted"; one left 'sending' (an outcome
nobody knows) is never re-posted -- the check shows it for a person. A leg
whose first failure might still have reached the platform (a timeout, a
5xx on the publishing call) is read back first (Instagram, Facebook); if it
is there it is recorded, not re-posted. TikTok has no read-back by caption,
so an uncertain TikTok leg is left for a person. Rollback: that leg's post
is taken back (`crosspost.unpublish_reel`, the same undo as the whole reel).

**Rate.** The Instagram leg shares `instagram.publish`'s own hourly limit
(`kernel.legs`); TikTok and Facebook each get `LEG_MAX_PER_HOUR`, a window
the normal publish spends too. The step posts at most one leg per platform
per tick.

**The daily cap.** A catch-up is the same reel, already counted the day it
first posted: `crosspost.posted_today` counts reels whose FIRST `reel_posts`
row is today, so a leg synced tomorrow never takes tomorrow's slot, and the
catch-up itself is not subject to the cap.

**Series order.** Part N never goes up on a platform that is still missing
part N-1 there for a retryable reason: the sync takes reels oldest first
and refuses N's leg while N-1's is open; `autopublish_check` holds a new
part whole (a "wait", never a remake or a card) while its predecessor has
an open retryable gap. The part before is found from an import's source
(`import:<series>:part N`), `reel_series_parts`, or a calendar job's
dependencies. The Tier 2 card path (automatic posting off) is not held: an
approved card is Miro's own decision about that reel.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult, Health, Observation
from ...kernel.state import fetch_all, fetch_one

log = logging.getLogger("atlas.content.legsync")

ACTION = "content.post_missing_leg"
PUBLISHERS = ("content.autopublish_reel", "content.publish_reel")
# YouTube is NOT a leg (Miro, 2026-09-29): Shorts are curated, three a week
# (`content.shorts`, `content.publish_short`), never a mirror of every reel,
# so a reel missing on YouTube is not a gap and is never back-filled here.
LEGS = ("instagram", "facebook", "tiktok")
# Media 12 posted 2026-09-29 07:32 UTC; 96 h keeps it in reach until
# 2026-10-03 07:32 even if the deploy slips a day.
WINDOW_HOURS = 96
STALE = timedelta(hours=2)
# A catch-up that ran and failed is not tried again for this long: a token
# that is still dead must not spend the platform's hourly allowance every
# tick (the Instagram one is shared with every new reel, which would then
# wait whole -- TikTok included).
BACKOFF = timedelta(hours=2)
# In a held detail post_or_card reads as "wait" (kernel.reels).
SERIES_HELD = "waiting for the part before it"

_FINISH = re.compile(r"^reel (\d+):")


# ------------------------------------------------------------ classification

_RETRYABLE = re.compile(
    r"http (401|403|429|5\d\d)"                       # credentials, limits, 5xx
    r"|timed out|timeout|unreachable|rate limit|too many"
    r"|access token|oauthexception|session has expired|credentials rejected"
    r"|this hour|hourly allowance|still processing"
    r"|the pause state could not be read")
# A content or policy refusal, never retried: the guard / AI label / kind
# rules ("refused: ..."), a pause (Miro's own stop), a parent not allowed.
_POLICY = re.compile(r"^refused:|\bis paused\b|shadow mode|not a parent|is refused:")


def classify(status: str, said: str) -> bool:
    """Whether a leg refused with `said` may be tried again: credentials,
    the platform's own failure, a timeout, a rate limit. Anything else --
    above all a content or guard refusal -- is not."""
    if status != "refused":
        return False
    text = " ".join((said or "").lower().split())
    if _POLICY.search(text):
        return False
    return bool(_RETRYABLE.search(text))


def uncertain(platform: str, said: str) -> bool:
    """Could the first attempt have reached the platform anyway? A timeout
    or an unreachable host mid-call, a 5xx on the call that publishes, or an
    answer that could not be read. A 4xx, a spent allowance or a failed
    upload certainly did not post."""
    text = " ".join((said or "").lower().split())
    if "connecterror" in text:
        return False
    if re.search(r"timed out|timeout|not json|named no post id", text):
        return True
    if platform == "instagram":
        return ("unreachable" in text or
                bool(re.search(r"http 5\d\d on \S*media_publish", text)))
    if platform == "tiktok":
        return "postiz post:" in text and bool(
            re.search(r"http 5\d\d|unreachable", text))
    return bool(re.search(r"http 5\d\d|unreachable", text))


# ------------------------------------------------------------ the gaps

@dataclass(frozen=True, slots=True)
class Gap:
    media_id: int
    platform: str
    posted_at: datetime        # when the reel's own run finished
    caption: str | None        # the caption that posted, from the run
    said: str                  # why the leg is missing, in words
    retryable: bool
    in_doubt: bool = False     # a catch-up claimed and never settled
    last_try: datetime | None = None   # a catch-up that ran and failed

    def line(self) -> str:
        from .crosspost import NAMES
        return f"media {self.media_id} on {NAMES[self.platform]}: {self.said}"


@dataclass(slots=True)
class _Run:
    media_id: int
    at: datetime
    caption: str | None
    legs: dict[str, Any]
    tried: dict[str, datetime] = field(default_factory=dict)


def _payload(row: Any) -> dict[str, Any]:
    raw = row["payload"]
    data = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return data.get("data") or {}


def _aware(at: Any) -> datetime:
    if isinstance(at, datetime):
        return at if at.tzinfo else at.replace(tzinfo=UTC)
    return datetime.now(UTC)


async def _runs(hours: int) -> dict[int, _Run]:
    """The first successful publish run per media in the window, with the
    caption its start row carried and its per-leg evidence."""
    rows = await fetch_all(
        f"""SELECT id, at, event, subject, payload FROM actions_log
             WHERE event IN ('execute.start', 'execute.finish')
               AND subject IN ('content.autopublish_reel', 'content.publish_reel',
                               'content.post_missing_leg')
               AND at >= now() - interval '{int(hours)} hours'
             ORDER BY id""")
    captions: dict[tuple[str, int], str] = {}
    runs: dict[int, _Run] = {}
    tried: dict[tuple[int, str], datetime] = {}
    for row in rows or []:
        try:
            data = _payload(row)
        except (TypeError, ValueError):
            continue
        subject = str(row["subject"])
        if subject == ACTION:
            _tried(row, data, tried)
            continue
        if subject not in PUBLISHERS:
            continue                # never a clip, never another lane
        if row["event"] == "execute.start":
            params = data.get("params") or {}
            try:
                mid = int(params.get("media_id"))
            except (TypeError, ValueError):
                continue
            captions[(subject, mid)] = str(params.get("caption") or "") or ""
            continue
        if not data.get("ok"):
            continue
        match = _FINISH.match(str(data.get("detail") or ""))
        if match is None:
            continue
        mid = int(match.group(1))
        if mid in runs:
            continue
        evidence = data.get("evidence")
        runs[mid] = _Run(mid, _aware(row["at"]), captions.get((subject, mid)),
                         evidence if isinstance(evidence, dict) else {})
    for (mid, platform), at in tried.items():
        if mid in runs:
            runs[mid].tried[platform] = at
    return runs


_CAUGHT = re.compile(r"^reel (\d+) not caught up:")


def _tried(row: Any, data: dict[str, Any], tried: dict[tuple[int, str], datetime]
           ) -> None:
    """A catch-up that ran and failed: its finish row (not a dry-run refusal,
    which spends nothing)."""
    if row["event"] != "execute.finish" or data.get("ok"):
        return
    match = _CAUGHT.match(str(data.get("detail") or ""))
    evidence = data.get("evidence")
    if match is None or not isinstance(evidence, dict):
        return
    for platform in LEGS:
        if platform in evidence:
            tried[(int(match.group(1)), platform)] = _aware(row["at"])


def _configured(platform: str) -> bool:
    from . import crosspost, postiz
    if platform == "facebook":
        return crosspost._fb_cfg() is not None
    if platform == "tiktok":
        return postiz.configured()
    from .. import instagram
    try:
        instagram._cfg("stories")
    except instagram.NoAccount:
        return False
    return True


async def gaps(hours: int = WINDOW_HOURS, *,
               media_ids: list[int] | None = None) -> list[Gap]:
    """Every missing leg of a reel posted in the window, oldest reel first.
    Raises when the record cannot be read: the caller decides what an
    unreadable record means (the check: UNKNOWN; a post: not made)."""
    runs = await _runs(hours)
    if media_ids is not None:
        runs = {m: r for m, r in runs.items() if m in set(media_ids)}
    if not runs:
        return []
    ids = sorted(runs)
    posted = await fetch_all(
        "SELECT media_id, platform FROM reel_posts WHERE media_id = ANY($1::bigint[])",
        ids)
    have = {(int(r["media_id"]), str(r["platform"])) for r in posted or []}
    keys = [f"{m}:{p}" for m in ids for p in LEGS]
    claimed = await fetch_all(
        "SELECT key, status FROM effect_claims WHERE action = $1 AND key = ANY($2)",
        ACTION, keys)
    claims = {str(r["key"]): str(r["status"]) for r in claimed or []}
    out: list[Gap] = []
    for run in sorted(runs.values(), key=lambda r: (r.at, r.media_id)):
        for platform in LEGS:
            gap = _gap(run, platform, have, claims.get(f"{run.media_id}:{platform}"))
            if gap is not None:
                out.append(gap)
    return out


def _gap(run: _Run, platform: str, have: set[tuple[int, str]],
         claim: str | None) -> Gap | None:
    from .crosspost import ID_PENDING, NOT_CONFIGURED, POSTED
    if (run.media_id, platform) in have or claim == "sent":
        return None
    base = {"media_id": run.media_id, "platform": platform,
            "posted_at": run.at, "caption": run.caption,
            "last_try": run.tried.get(platform)}
    if claim is not None:
        return Gap(**base, said="a catch-up post never confirmed; check the "
                               "platform by hand (it is not posted again)",
                   retryable=False, in_doubt=True)
    leg = run.legs.get(platform)
    if not isinstance(leg, dict) or "status" not in leg:
        return Gap(**base, said="the run's record of this leg could not be read, "
                               "so it is not retried", retryable=False,
                   in_doubt=True)
    status, said = str(leg.get("status")), str(leg.get("detail") or "")
    if status in (POSTED, ID_PENDING):
        return None
    if status == NOT_CONFIGURED:
        return None                 # not configured then: never back-filled
    if classify(status, said):
        return Gap(**base, said=f"refused ({said[:160]}); retryable",
                   retryable=True)
    return Gap(**base, said=f"refused ({said[:160]}); not retried -- a content "
                           f"or policy refusal", retryable=False)


# ------------------------------------------------------------ series order

async def predecessors(media_id: int) -> list[int]:
    """The media of the part before this one, however the series was made:
    an import (`import:<series>:part N`), an ATLAS series part, or a
    calendar job's dependencies. Empty when it is not a later part."""
    out: list[int] = []
    source = str(_val(await fetch_one(
        """SELECT d.source AS legsync_source FROM content_media m
             JOIN content_drafts d ON d.id = m.draft_id
            WHERE m.id = $1 AND d.kind = 'story_script'""", media_id),
        "legsync_source") or "")
    series, sep, part = source.rpartition(":part ")
    if source.startswith("import:") and sep and part.strip().isdigit():
        n = int(part.strip())
        if n > 1:
            prev = _val(await fetch_one(
                """SELECT m.id AS legsync_prev FROM content_drafts d
                     JOIN content_media m ON m.draft_id = d.id
                    WHERE d.source = $1 AND m.status = 'done'
                    ORDER BY m.id DESC LIMIT 1""", f"{series}:part {n - 1}"),
                "legsync_prev")
            if prev is not None:
                out.append(int(prev))
    prev = _val(await fetch_one(
        """SELECT p.media_id AS legsync_prev FROM reel_series_parts s
             JOIN reel_series_parts p
               ON p.series_id = s.series_id AND p.part_no = s.part_no - 1
            WHERE s.media_id = $1""", media_id), "legsync_prev")
    if prev is not None:
        out.append(int(prev))
    job = _val(await fetch_one(
        "SELECT job_id AS legsync_job FROM reel_calendar WHERE media_id = $1",
        media_id), "legsync_job")
    if job:
        from . import calendar as cal
        deps = cal.dependencies(cal.job(str(job)) or {"job_id": ""})
        if deps:
            rows = await fetch_all(
                """SELECT media_id AS legsync_prev FROM reel_calendar
                    WHERE job_id = ANY($1::text[]) AND media_id IS NOT NULL""", deps)
            out += [int(v) for r in rows or []
                    if (v := _val(r, "legsync_prev")) is not None]
    return sorted({m for m in out if m != media_id})


def _val(row: Any, key: str) -> Any:
    """A column of a row, or None -- for a row that is not a mapping too."""
    try:
        return row[key] if row is not None else None
    except (KeyError, IndexError, TypeError):
        return None


async def series_hold(media_id: int, platform: str | None = None) -> str | None:
    """Why this reel must wait for the part before it, or None. `platform`
    None: any leg (a whole new post). Only an open, retryable gap on a
    platform that is configured now holds it; a content refusal does not
    hold a series forever. Unreadable -> it waits (never out of order)."""
    try:
        before = await predecessors(media_id)
        if not before:
            return None
        open_ = [g for g in await gaps(media_ids=before)
                 if g.retryable and _configured(g.platform)
                 and (platform is None or g.platform == platform)]
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return (f"{SERIES_HELD}: whether the part before it reached every "
                f"platform could not be read ({explain(exc)})")
    if not open_:
        return None
    from .crosspost import NAMES
    where = ", ".join(sorted({f"media {g.media_id} on {NAMES[g.platform]}"
                              for g in open_}))
    return f"{SERIES_HELD} to be synced first ({where})"


# ------------------------------------------------------------ the action

def _over_limit(platform: str) -> str | None:
    from ...kernel import legs
    from .crosspost import IG_LEG
    if platform == "instagram":
        return legs.over_limit(IG_LEG)
    return legs.platform_over_limit(platform)


def spend(platform: str) -> None:
    """A TikTok or Facebook reel post, by any path, spends that platform's
    hourly window (Instagram's is `instagram.publish`'s, `kernel.legs`)."""
    from ...kernel import legs
    legs.spend_platform(platform)


async def _find(media_id: int, platform: str) -> tuple[Gap | None, str | None]:
    """(the gap, None) when this leg may be posted now; (None, why) when not."""
    if platform not in LEGS:
        return None, f"no such leg {platform!r}; one of {', '.join(LEGS)}"
    try:
        found = [g for g in await gaps(media_ids=[media_id]) if g.platform == platform]
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return None, (f"whether media {media_id} is missing on {platform} could "
                      f"not be read ({explain(exc)}), so nothing is posted")
    if not found:
        return None, (f"media {media_id} has no missing {platform} leg in the last "
                      f"{WINDOW_HOURS} h (posted, never posted, or not configured "
                      f"when it posted); nothing to sync")
    gap = found[0]
    if not gap.retryable:
        return None, f"not synced: {gap.said}"
    if gap.last_try is not None and datetime.now(UTC) - gap.last_try < BACKOFF:
        return None, (f"a catch-up for media {media_id} on {platform} failed at "
                      f"{gap.last_try:%H:%M} UTC; tried again after "
                      f"{BACKOFF.seconds // 3600} h")
    if not gap.caption or "<redacted>" in gap.caption:
        return None, (f"the caption media {media_id} posted with could not be read "
                      f"whole from its run, so nothing is posted")
    if not _configured(platform):
        return None, f"{platform} is not configured now"
    if (why := await series_hold(media_id, platform)) is not None:
        return None, why
    if (why := _over_limit(platform)) is not None:
        from .crosspost import RATE_HELD
        return None, f"{RATE_HELD}: {why}"
    return gap, None


async def post_missing_leg_dry(media_id: int, platform: str) -> ActionResult:
    from . import crosspost
    gap, why = await _find(int(media_id), str(platform))
    if gap is None:
        return ActionResult(False, why or "nothing to sync")
    row, why = await crosspost._media(gap.media_id)
    if row is None:
        return ActionResult(False, why or "no media")
    check = (" (read back first: the first attempt may have reached it)"
             if uncertain(platform, gap.said) else "")
    return ActionResult(True, f"would post media {gap.media_id} to "
                              f"{crosspost.NAMES[platform]} only, with the caption "
                              f"it posted with{check}; {gap.said}")


async def _already_there(gap: Gap) -> tuple[bool | None, str]:
    """For an uncertain first attempt: is it on the platform after all?
    True/False when read; None when it cannot be told."""
    from ...kernel.verify import State
    started = gap.posted_at - timedelta(minutes=30)
    if gap.platform == "instagram":
        from ..instagram.readback import find_recent
        seen = await find_recent("stories", gap.caption or "", started)
    elif gap.platform == "facebook":
        from .readback import _facebook_since
        seen = await _facebook_since(gap.caption or "", started)
    else:
        return None, "TikTok cannot be read back by caption; check it by hand"
    if seen.state is State.VERIFIED:
        ref = seen.evidence.get("media_id") or seen.evidence.get("video_id") or ""
        return True, str(ref)
    if seen.state is State.CONTRADICTED:
        return False, seen.reason
    return None, seen.reason or "the read-back could not tell"


async def post_missing_leg(media_id: int, platform: str) -> ActionResult:
    """Post the one missing leg of a reel, with the caption it posted with."""
    from ...kernel import idempotency
    from . import crosspost
    media_id, platform = int(media_id), str(platform)
    gap, why = await _find(media_id, platform)
    if gap is None:
        return ActionResult(False, why or "nothing to sync")
    caption = gap.caption or ""
    key = f"{media_id}:{platform}"
    state = await idempotency.claim(ACTION, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, f"media {media_id} on {platform}: "
                                   f"{idempotency.refusal(ACTION, state)}")
    name = crosspost.NAMES[platform]
    if uncertain(platform, gap.said):
        there, said = await _already_there(gap)
        if there is True:
            await idempotency.settle(ACTION, key, f"found on the platform: {said}")
            if platform == "instagram" and said:
                await crosspost._record_posts(media_id, {"instagram": f"stories:{said}"})
            return ActionResult(False, f"media {media_id} is already on {name} "
                                       f"(read back: {said}); recorded, not posted "
                                       f"again")
        if there is None:
            await idempotency.release(ACTION, key)
            return ActionResult(False, f"media {media_id} on {name}: the first "
                                       f"attempt may have posted and it cannot be "
                                       f"read back ({said}); not posted -- check "
                                       f"by hand")
    row, why = await crosspost._media(media_id)
    url = None
    if row is not None:
        url, why = await crosspost._public_url(row)
    if row is None or url is None:
        await idempotency.release(ACTION, key)
        return ActionResult(False, why or "no media")
    if platform == "instagram":
        status, said, token = await crosspost._instagram(
            caption, url, parent=ACTION, media_id=media_id)
    elif platform == "facebook":
        status, said, token = await crosspost._facebook(caption, url)
    else:
        status, said, token = await crosspost._tiktok(
            crosspost.platform_caption(caption, "tiktok"), row["path"])
    line = crosspost._line(platform, status, said)
    evidence = {platform: {"status": status, "detail": said}, "caught_up": True}
    if status in (crosspost.POSTED, crosspost.ID_PENDING):
        await idempotency.settle(ACTION, key, said)
        if token:
            await crosspost._record_posts(media_id, {platform: token})
        return ActionResult(True, f"reel {media_id} caught up: {line}",
                            evidence=evidence,
                            rollback_token=(json.dumps({platform: token})
                                            if token else None))
    if uncertain(platform, said):
        log.warning("%s: %s may have posted (%s); its claim stays", ACTION, key, said)
    else:
        await idempotency.release(ACTION, key)
    return ActionResult(False, f"reel {media_id} not caught up: {line}",
                        evidence=evidence)


# ------------------------------------------------------------ the step

async def due() -> list[Gap]:
    """What the reels tick should try now (`kernel.reels.leg_sync`, which
    runs each through the executor): every retryable missing leg on a
    platform configured now, oldest reel first, at most ONE per platform
    per tick -- so part N-1's leg goes before part N's, and N's is refused
    by its own dry run until N-1's is there. Raises when unreadable."""
    out: list[Gap] = []
    tried: set[str] = set()
    for gap in await gaps():
        if not gap.retryable or gap.platform in tried:
            continue
        if not _configured(gap.platform):
            continue
        if gap.last_try is not None and datetime.now(UTC) - gap.last_try < BACKOFF:
            continue                # failed lately: not every tick
        tried.add(gap.platform)
        out.append(gap)
    return out


# ------------------------------------------------------------ the check

async def check_reel_legs_sync() -> Observation:
    """Reels posted in the window that are missing a leg, and why. DEGRADED
    when a retryable leg has been missing over two hours or a leg's state
    cannot be told; UNKNOWN when the record cannot be read; OK when every
    configured leg is there (a content refusal is named, not retried)."""
    try:
        found = await gaps()
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return Observation("reel_legs_sync", Health.UNKNOWN,
                           detail=f"the posted reels could not be read: "
                                  f"{explain(exc)}")
    now = datetime.now(UTC)
    late = [g for g in found
            if (g.retryable or g.in_doubt) and now - g.posted_at > STALE]
    waiting = [g for g in found if g.retryable and g not in late]
    refused = [g for g in found if not g.retryable and not g.in_doubt]
    read = f"read {now:%H:%M} UTC, last {WINDOW_HOURS} h"
    evidence = {"missing": len(late) + len(waiting), "late": len(late),
                "not_retried": len(refused),
                "gaps": [{"media_id": g.media_id, "platform": g.platform,
                          "why": g.said[:200], "retryable": g.retryable}
                         for g in found][:20]}
    parts = []
    if late:
        parts.append("missing over 2 h: " + "; ".join(g.line() for g in late))
    if waiting:
        parts.append("to sync on the next tick: " +
                     "; ".join(g.line() for g in waiting))
    if refused:
        parts.append("not retried: " + "; ".join(g.line() for g in refused))
    if late:
        return Observation("reel_legs_sync", Health.DEGRADED,
                           detail=" · ".join(parts) + f" ({read})",
                           value=float(len(late)), unit="legs", evidence=evidence)
    return Observation("reel_legs_sync", Health.OK,
                       detail=(" · ".join(parts) if parts else
                               "every posted reel is on every configured platform")
                              + f" ({read})",
                       value=0.0, unit="legs", evidence=evidence)
