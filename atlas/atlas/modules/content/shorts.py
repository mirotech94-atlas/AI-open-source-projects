"""YouTube Shorts: the best AI reel of the week, three times a week
(the owner, 2026-09-29).

YouTube's 2025 policy against "inauthentic / mass-produced" content rewards
quality over volume, so this is curated, not a mirror: `SHORTS_PER_WEEK`
(default 3; Monday, Wednesday and Friday New York -- `course.SLOTS`) Shorts,
each the BEST reel of the AI-stories lane (stories, series, football,
course; never a campaign clip, which lives in its own table, and never a
business-calendar reel) that posted in the last `WINDOW_DAYS` and is not on
YouTube yet. A slot missed (nothing eligible that day) moves to the next
day of the same week; a week's shortfall never carries over.

**Which reel.** The reels' own publish runs (`legsync._runs`: the exact
caption each posted with) in the window, ranked by the views `metrics.py`
last READ for their posts (summed over platforms, each figure with the time
it was read). With no reading at all, the newest finished reel goes first,
and the note says the ranking is by recency, not numbers.

**The quality gate**, all in code, the first ranked reel that passes wins:
a finished render of 1080x1920 with sound that is not silent, at least
`crosspost.MIN_SECONDS` and at most `MAX_SECONDS` (the owner's Shorts limit --
a longer reel is skipped, never cut); the caption guards the reel posted
under (`crosspost._caption_refusal`, run over the new title AND the
description) and its AI label; YouTube's title (2-100) and description
(<= 5000, no angle brackets) limits; and series order -- part N never goes
up before part N-1 is on YouTube.

**The words** are the reel's own, never a model's: the title is the draft
title for a series / calendar / imported reel and the caption's first line
(the hook) otherwise, cut at a word to 100 characters; the description is
the caption it posted with plus `DISCLOSURE`; the tags its hashtags. Made
for kids: "no". Postiz exposes no AI / altered-content field for YouTube
(verified against its schema 2026-09-29), so the disclosure is in the
description -- and the caption's own "Made with AI" line -- and the Studio
toggle is left to a person.

**The action** is `content.publish_short` (Tier 1, through the executor:
dry run, audit, watch-only, pause, rate limit), param `media_id` only: the
title, description and tags are derived here, so nothing handed to it can
change what posts. It re-checks everything, the week's cap included, claims
`effect_claims(content.publish_short, "<media>:youtube")` before the post
(a second run posts nothing), and records a `reel_posts` row with platform
'youtube'. Rollback: the Postiz post is deleted while still queued; a
published Short is a MANUAL note ("remove by hand in YouTube Studio"),
because deleting a published post is Tier 3. With `type: now` the post
publishes at once, so in practice the rollback is that note.

YouTube is not a leg of the reel: `legsync` never looks at it (its runs are
`content.publish_short`'s, not a publisher's, and 'youtube' is not a leg).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult, Health, Observation
from ...kernel.state import execute_sql, fetch_all, fetch_one
from .drafts import AI_LABEL

log = logging.getLogger("atlas.content.shorts")

ACTION = "content.publish_short"
PLATFORM = "youtube"
PER_WEEK_ENV = "SHORTS_PER_WEEK"
PER_WEEK_DEFAULT = 3
WINDOW_DAYS = 7
MAX_SECONDS = 60.0
LANES = ("stories", "football", "course")
MAX_TAGS = 10
DISCLOSURE = ("Made with AI: the story, visuals and voices in this Short are "
              "AI-generated.")
# Draft titles that name the lane for ATLAS, not the story for a viewer.
_INTERNAL_TITLE = re.compile(r"^(reel|course reel|football reel)\s*:\s*",
                             re.IGNORECASE)
_HASHTAG = re.compile(r"(?<![\w#])#(\w{2,40})")
_TITLED = ("series:", "calendar:", "import:")


def per_week() -> int:
    try:
        return max(0, min(7, int(os.getenv(PER_WEEK_ENV, str(PER_WEEK_DEFAULT)))))
    except ValueError:
        return PER_WEEK_DEFAULT


# ------------------------------------------------------------ the words

def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0] or text[:limit - 1]
    return cut.rstrip(" ,;:-—") + "…"


def hook(caption: str) -> str:
    """The caption's first line of words: no hashtags, not the AI label."""
    for line in (caption or "").splitlines():
        words = " ".join(_HASHTAG.sub("", line).split())
        if words and words != AI_LABEL:
            return words
    return ""


def title_for(source: str, draft_title: str, caption: str) -> str:
    """The reel's own title or hook, never rewritten, at most 100
    characters. Pure."""
    from .postiz import YOUTUBE_TITLE_MAX
    own = " ".join((draft_title or "").split())
    if source.startswith(_TITLED) and own:
        base = own
    else:
        base = hook(caption) or _INTERNAL_TITLE.sub("", own)
    return _clip(base, YOUTUBE_TITLE_MAX)


def description_for(caption: str) -> str:
    return f"{caption.rstrip()}\n\n{DISCLOSURE}"


def tags_for(caption: str) -> list[str]:
    out: list[str] = []
    for tag in _HASHTAG.findall(caption or ""):
        tag = tag.lower()
        if tag not in out:
            out.append(tag)
    return out[:MAX_TAGS]


# ------------------------------------------------------------ records

@dataclass(slots=True)
class Candidate:
    media_id: int
    posted_at: datetime
    caption: str | None
    views: float | None = None
    read_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Short:
    media_id: int
    path: str
    lane: str
    title: str
    description: str
    tags: list[str] = field(default_factory=list)


def week_start(day: date) -> datetime:
    from .course import week_start as start
    return start(day)


async def done_this_week(day: date) -> int:
    row = await fetch_one(
        """SELECT count(*) AS n FROM reel_posts
            WHERE platform = 'youtube' AND at >= $1""", week_start(day))
    return int(row["n"]) if row else 0


async def on_youtube(media_ids: list[int]) -> set[int]:
    if not media_ids:
        return set()
    rows = await fetch_all(
        """SELECT media_id FROM reel_posts
            WHERE platform = 'youtube' AND media_id = ANY($1::bigint[])""",
        media_ids)
    return {int(r["media_id"]) for r in rows or []}


async def claimed(media_ids: list[int]) -> dict[int, str]:
    """media -> the status of its `content.publish_short` claim, if any."""
    if not media_ids:
        return {}
    rows = await fetch_all(
        "SELECT key, status FROM effect_claims WHERE action = $1 AND key = ANY($2)",
        ACTION, [f"{m}:{PLATFORM}" for m in media_ids])
    return {int(str(r["key"]).partition(":")[0]): str(r["status"])
            for r in rows or []}


async def in_doubt() -> list[int]:
    """Media whose Short post was claimed and never settled: it may be on
    YouTube; only a person can tell."""
    rows = await fetch_all(
        """SELECT key FROM effect_claims
            WHERE action = $1 AND status = 'sending'
              AND claimed_at < now() - interval '10 minutes'""", ACTION)
    return sorted(int(str(r["key"]).partition(":")[0]) for r in rows or [])


async def _views(media_ids: list[int]) -> dict[int, tuple[float, datetime]]:
    """Each media's latest READ views, summed over its posts, with the
    newest read time. A post never read adds nothing (never 0)."""
    if not media_ids:
        return {}
    rows = await fetch_all(
        """SELECT DISTINCT ON (m.platform, m.post_ref)
                  p.media_id AS shorts_media, m.views, m.at AS read_at
             FROM reel_metrics m
             JOIN reel_posts p ON p.platform = m.platform AND p.post_ref = m.post_ref
            WHERE p.media_id = ANY($1::bigint[]) AND p.platform <> 'youtube'
              AND m.views IS NOT NULL
            ORDER BY m.platform, m.post_ref, m.at DESC""", media_ids)
    out: dict[int, tuple[float, datetime]] = {}
    for r in rows or []:
        mid, views, at = int(r["shorts_media"]), float(r["views"]), r["read_at"]
        have = out.get(mid)
        out[mid] = ((have[0] + views, max(have[1], at)) if have else (views, at))
    return out


def rank(found: list[Candidate]) -> tuple[list[Candidate], str]:
    """Read reels by views, most first; then unread ones, newest first.
    Pure. The note says which it was, with the read time."""
    read = sorted((c for c in found if c.views is not None),
                  key=lambda c: (-(c.views or 0.0), -c.posted_at.timestamp()))
    unread = sorted((c for c in found if c.views is None),
                    key=lambda c: -c.posted_at.timestamp())
    if not read:
        return unread, ("no views read for these reels yet; the newest "
                        "finished reel first")
    newest = max(c.read_at for c in read if c.read_at is not None)
    return read + unread, (f"ranked by views read (latest read "
                           f"{newest:%d %b %H:%M} UTC)")


async def candidates() -> tuple[list[Candidate], str]:
    """The lane's reels posted in the window, not on YouTube yet, ranked.
    Raises when the record cannot be read."""
    from .legsync import _runs
    runs = await _runs(WINDOW_DAYS * 24)
    ids = sorted(runs)
    # On YouTube, or claimed by an earlier post whatever came of it: a
    # post that may have landed is for a person to check, and must not
    # hold every later slot of the week.
    done = await on_youtube(ids) | set(await claimed(ids))
    views = await _views([m for m in ids if m not in done])
    found = []
    for mid in ids:
        if mid in done:
            continue
        run = runs[mid]
        seen = views.get(mid)
        found.append(Candidate(mid, run.at, run.caption,
                               seen[0] if seen else None,
                               seen[1] if seen else None))
    return rank(found)


# ------------------------------------------------------------ the gate

# The clips lane shares the AtlasClips YouTube channel (its own env,
# `POSTIZ_YOUTUBE_CLIPS_INTEGRATION`, is never read here). A Short is only
# ever a `content_media` render (never a `clip_outputs` row: the gate reads
# `content_media` joined to its draft and nothing else), and its resolved
# file never sits under the clips data dir.
CLIPS_DIRS = ("data/clips", "data/app/clips", "/app/data/clips")


def clips_file(path: str) -> bool:
    """Whether `path`, symlinks resolved, lies under the clips lane's data
    dir (`CLIPS_DATA_DIR`, or any of its known homes). Blocking: it reads
    the filesystem. Unresolvable counts as a clip -- never posted blind."""
    try:
        real = os.path.realpath(path)
        roots = {os.path.realpath(d) for d in
                 (os.getenv("CLIPS_DATA_DIR", "").strip(), *CLIPS_DIRS) if d}
    except (OSError, ValueError):
        return True
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep)
               for r in roots)


async def gate(media_id: int, caption: str | None) -> tuple[Short | None, str | None]:
    """(the Short, None) when this reel may go to YouTube now, else (None,
    why). Reads only."""
    from . import crosspost, hashtags, legsync, postiz
    row = await fetch_one(
        """SELECT d.source AS shorts_source, d.title AS shorts_title,
                  f.source AS shorts_format_source
             FROM content_media m
             JOIN content_drafts d ON d.id = m.draft_id
             LEFT JOIN reel_formats f ON d.source = 'reel_format:' || f.id
            WHERE m.id = $1""", media_id)
    if row is None:
        return None, f"media {media_id} has no draft, so its lane is unknown"
    source = str(row["shorts_source"] or "")
    lane, _series = hashtags.lane_of(source, row["shorts_format_source"])
    if lane not in LANES:
        return None, (f"media {media_id} is not an AI-stories-lane reel "
                      f"(lane {lane!r})")
    if not caption or "<redacted>" in caption:
        return None, (f"the caption media {media_id} posted with could not be "
                      f"read whole")
    if (why := crosspost.vet_publish_reel({"caption": caption})) is not None:
        return None, why
    title = title_for(source, str(row["shorts_title"] or ""), caption)
    description = description_for(caption)
    if (why := postiz.short_text_refusal(title, description)) is not None:
        return None, why
    if (why := await crosspost._caption_refusal(
            media_id, f"{title}\n{description}")) is not None:
        return None, why
    before = await legsync.predecessors(media_id)
    if before:
        missing = sorted(set(before) - await on_youtube(before))
        if missing:
            return None, (f"series order: the part before it (media "
                          f"{', '.join(map(str, missing))}) is not on YouTube yet")
    media, why = await crosspost._media(media_id)
    if media is None:
        return None, why or "no media"
    if await asyncio.to_thread(clips_file, str(media["path"])):
        return None, (f"media {media_id}'s file sits under the clips lane's data "
                      f"dir; a clip never goes out as an AI-reels Short")
    if (why := await crosspost.render_refusal(media["path"], MAX_SECONDS)):
        return None, why
    return Short(media_id, str(media["path"]), lane, title, description,
                 tags_for(caption)), None


async def pick() -> tuple[Short | None, str, list[str]]:
    """(the best eligible reel, the ranking note, why each better one was
    skipped). Raises when the record cannot be read."""
    ranked, note = await candidates()
    skipped: list[str] = []
    for cand in ranked:
        short, why = await gate(cand.media_id, cand.caption)
        if short is not None:
            return short, note, skipped
        skipped.append(f"media {cand.media_id}: {why}")
    return None, note, skipped


async def _derive(media_id: int, today: date) -> tuple[Short | None, str | None]:
    """Everything the action re-checks before it posts, from the record."""
    from . import legsync, postiz
    n = per_week()
    if n == 0:
        return None, f"Shorts are off ({PER_WEEK_ENV}=0)"
    if (gap := postiz.youtube_gap()) is not None:
        return None, gap
    try:
        done = await done_this_week(today)
        if done >= n:
            return None, (f"this week's {n} Short(s) are done ({PER_WEEK_ENV}; "
                          f"read {datetime.now(UTC):%H:%M} UTC)")
        runs = await legsync._runs(WINDOW_DAYS * 24)
        if media_id not in runs:
            return None, (f"media {media_id} did not post as an AI reel in the "
                          f"last {WINDOW_DAYS} days")
        if media_id in await on_youtube([media_id]):
            return None, f"media {media_id} is already on YouTube; not posted again"
        return await gate(media_id, runs[media_id].caption)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return None, (f"whether media {media_id} may go to YouTube could not be "
                      f"read ({explain(exc)}), so nothing is posted")


def _today() -> date:
    from .course import local_today
    return local_today()


# ------------------------------------------------------------ the action

async def publish_short_dry(media_id: int) -> ActionResult:
    from . import postiz
    media_id = int(media_id)
    short, why = await _derive(media_id, _today())
    if short is None:
        return ActionResult(False, f"not posted to YouTube: {why}")
    ok, said = await postiz.youtube_readiness(short.title, short.tags)
    if not ok:
        return ActionResult(False, f"not posted to YouTube: {said}")
    return ActionResult(True, f"would post media {media_id} as a YouTube Short "
                              f"titled {short.title!r}; {said}",
                        evidence={"title": short.title, "tags": short.tags})


def _uncertain(said: str) -> bool:
    """Could a failed post still have reached Postiz? Only a failure on the
    post call itself that was not a clean refusal."""
    text = " ".join((said or "").lower().split())
    if "connecterror" in text or "postiz post:" not in text:
        return False
    return bool(re.search(r"http 5\d\d|unreachable|timed out|timeout|not json"
                          r"|named no post id", text))


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


async def publish_short(media_id: int) -> ActionResult:
    """Post one reel as a YouTube Short, through Postiz. Once, never twice."""
    from ...kernel import idempotency
    from . import postiz
    media_id = int(media_id)
    short, why = await _derive(media_id, _today())
    if short is None:
        return ActionResult(False, f"not posted to YouTube: {why}")
    key = f"{media_id}:{PLATFORM}"
    state = await idempotency.claim(ACTION, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, f"media {media_id} on YouTube: "
                                   f"{idempotency.refusal(ACTION, state)}")
    try:
        video = await asyncio.to_thread(_read, short.path)
    except OSError as exc:
        await idempotency.release(ACTION, key)
        return ActionResult(False, f"the render could not be read: {exc.strerror}")
    try:
        post_id, url = await postiz.post_youtube_short(
            video, short.title, short.description, short.tags)
    except postiz.PostizError as exc:
        said = str(exc)
        if _uncertain(said):
            log.warning("%s: %s may have posted (%s); its claim stays", ACTION,
                        key, said)
            said += " -- it may have posted; check YouTube by hand (not retried)"
        else:
            await idempotency.release(ACTION, key)
        return ActionResult(False, f"not posted to YouTube: {said}")
    await idempotency.settle(ACTION, key, f"Postiz post {post_id}")
    try:
        await execute_sql(
            """INSERT INTO reel_posts (media_id, format_id, platform, post_ref)
               VALUES ($1, NULL, 'youtube', $2)""", media_id, post_id)
    except Exception as exc:                            # noqa: BLE001
        log.warning("short %s posted but not recorded: %s", media_id, exc)
    where = url or "its link not returned by Postiz yet"
    return ActionResult(True, f"short {media_id}: posted to YouTube (Postiz post "
                              f"{post_id}, {where}) titled {short.title!r}",
                        evidence={"postiz_post_id": post_id, "url": url,
                                  "title": short.title, "lane": short.lane},
                        rollback_token=json.dumps({PLATFORM: post_id}))


async def unpublish_short(token: str) -> ActionResult:
    """Delete the Postiz post while still queued; a published Short is a
    MANUAL note (deleting a published post is Tier 3)."""
    from . import postiz
    try:
        posted = json.loads(token)
    except ValueError:
        posted = None
    if not isinstance(posted, dict) or not posted.get(PLATFORM):
        return ActionResult(False, f"malformed rollback token: {token!r}")
    ok, said = await postiz.undo(str(posted[PLATFORM]), where="YouTube Studio")
    return ActionResult(ok, f"YouTube: {'deleted' if ok else 'not deleted'} — {said}")


# ------------------------------------------------------------ the check

def next_due(day: date, n: int, done: int) -> date | None:
    """The next New York day a Short is owed: today while one is due, else
    the next slot day (this week, or next week's first). Pure."""
    from .course import entitled
    if n <= 0:
        return None
    this_week = week_start(day)
    for k in range(14):
        d = day + timedelta(days=k)
        have = done if week_start(d) == this_week else 0
        if entitled(d, n) > have:
            return d
    return None


async def check_shorts_lane() -> Observation:
    """The last Short posted, how many this week, and when the next is due.
    UNKNOWN when the integration is unset, Postiz or YouTube cannot be
    reached, or the record cannot be read -- never OK on a guess."""
    from . import postiz
    from .course import entitled, local_today
    n = per_week()
    if n == 0:
        return Observation("shorts_lane", Health.OK,
                           detail=f"Shorts are off ({PER_WEEK_ENV}=0)")
    if (gap := postiz.youtube_gap()) is not None:
        return Observation("shorts_lane", Health.UNKNOWN, detail=gap)
    try:
        _id, name = await postiz.youtube_channel()
    except Exception as exc:                            # noqa: BLE001
        return Observation("shorts_lane", Health.UNKNOWN,
                           detail=f"YouTube through Postiz: {exc}")
    today = local_today()
    try:
        last = await fetch_one(
            """SELECT at, post_ref FROM reel_posts WHERE platform = 'youtube'
                ORDER BY at DESC LIMIT 1""")
        done = await done_this_week(today)
        doubt = await in_doubt()
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return Observation("shorts_lane", Health.UNKNOWN,
                           detail=f"reel_posts unreadable: {explain(exc)}")
    read = datetime.now(UTC)
    owed = entitled(today, n)
    nxt = next_due(today, n, done)
    said_last = (f"last Short {last['at']:%a %d %b %H:%M} UTC (Postiz post "
                 f"{last['post_ref']})" if last else "no Short posted yet")
    said_next = (("next due today" if nxt == today else f"next due {nxt:%a %d %b}")
                 if nxt else "none due")
    detail = (f"YouTube {name or 'channel'} via Postiz; {said_last}; {done} of {n} "
              f"this week ({owed} owed by today); {said_next} (read "
              f"{read:%H:%M} UTC)")
    evidence: dict[str, Any] = {"done_this_week": done, "per_week": n,
                                "owed": owed,
                                "next_due": nxt.isoformat() if nxt else None,
                                "in_doubt": doubt}
    if doubt:
        detail += (f"; a Short post never confirmed for media "
                   f"{', '.join(map(str, doubt))} -- check YouTube by hand "
                   f"(not retried)")
    health = (Health.DEGRADED if doubt or owed - done >= 2 else Health.OK)
    return Observation("shorts_lane", health, detail=detail, value=float(done),
                       unit="shorts this week", evidence=evidence)
