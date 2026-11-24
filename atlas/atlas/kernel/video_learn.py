"""ATLAS learns from YouTube videos: the queue, the daily job, the card.

Two sources, one table (`video_lessons`, one row per video ever):

  * "learn <youtube url>" from the verified owner chat (`chat_commands`), or the
    portal's POST /api/learn. One strict parse (`ytlearn.youtube.parse_video_id`),
    no model. It only QUEUES the video: no fetch and no model call on the
    request path. At most `YOUTUBE_LEARN_OWNER_PER_DAY` (5) new videos a New York
    day, and a video already in the table is never queued twice.
  * the Atlas Academy channel's own public videos (`academy.video_feed`): at most
    `YOUTUBE_LEARN_CHANNEL_PER_DAY` (3) a day, the next ones not yet in the table.

The daily job (`tick`, scheduler job `youtube_learn`) is off unless
YOUTUBE_LEARN=on, does nothing under ATLAS_WATCH_ONLY, drains the queue, then
reads the channel. For each video: transcript (a read-only collector, UNKNOWN
when it cannot be read) -> lessons draft (transcript wrapped as untrusted) -> one
Tier 2 `ytlearn.save_video_notes` card showing every parameter whole. This
module proposes and nothing else: it never decides a card and never calls the
executor. Approving only marks the stored note as approved knowledge.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("atlas.video_learn")

ACTION = "ytlearn.save_video_notes"
OWNER_PER_DAY = 5
CHANNEL_PER_DAY = 3

_LEARN = re.compile(
    r"^\W*(?:(?:hey|hi|please|pls|atlas)[\s,:]+)*learn(?:\s+from)?\s+"
    r"(?P<url>\S*(?://|youtu|\.)\S*)\s*$", re.IGNORECASE)

OFF = "Learning from videos is switched off. Nothing was queued."
NOT_YOUTUBE = ("I only learn from youtube.com or youtu.be video links. "
               "Nothing was queued.")
COULD_NOT = "I couldn't queue that just now. Nothing was changed."


def enabled() -> bool:
    return os.getenv("YOUTUBE_LEARN", "off").strip().lower() in (
        "1", "on", "true", "yes")


def hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("YOUTUBE_LEARN_HOUR", "11"))))
    except ValueError:
        return 11


def _cap(env: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(env, str(default))))
    except ValueError:
        return default


def owner_per_day() -> int:
    return _cap("YOUTUBE_LEARN_OWNER_PER_DAY", OWNER_PER_DAY)


def channel_per_day() -> int:
    return _cap("YOUTUBE_LEARN_CHANNEL_PER_DAY", CHANNEL_PER_DAY)


def is_learn_request(text: str) -> bool:
    return _LEARN.match(text or "") is not None


# ------------------------------------------------------------------ the queue

async def _today_count(source: str) -> int:
    from .state import fetch_one
    from .whatsapp_daily import NY
    midnight = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        "SELECT count(*) AS n FROM video_lessons WHERE source = $1 AND queued_at >= $2",
        source, midnight.astimezone(UTC))
    return int(row["n"]) if row else 0


async def _enqueue(video_id: str, source: str) -> bool:
    """True when the video is newly queued (or an unreadable one is queued again).
    A re-queue takes the new source, so it counts against that source's daily cap
    (an owner re-queue of an unreadable channel video is an owner video)."""
    from ..modules.ytlearn.youtube import WATCH
    from .state import fetch_one
    row = await fetch_one(
        """INSERT INTO video_lessons (video_id, source, source_url)
                VALUES ($1, $2, $3)
           ON CONFLICT (video_id) DO UPDATE
                SET status = 'queued', queued_at = now(), reason = NULL,
                    source = EXCLUDED.source
                WHERE video_lessons.status = 'unreadable'
           RETURNING video_id""", video_id, source, WATCH.format(id=video_id))
    return row is not None


async def queue(text: str) -> str:
    """The plain reply to "learn <url>". Queues at most one video; reads, drafts
    and proposes nothing."""
    from ..modules.ytlearn.youtube import parse_video_id
    found = _LEARN.match(text or "")
    video_id = parse_video_id(found.group("url") if found else text)
    if video_id is None:
        return NOT_YOUTUBE
    if not enabled():
        return OFF
    try:
        if await _today_count("owner") >= owner_per_day():
            return (f"I take {owner_per_day()} videos a day and that's today's "
                    "limit. Send it again tomorrow. Nothing was queued.")
        if not await _enqueue(video_id, "owner"):
            return "I already have that video. Nothing new was queued."
    except Exception as exc:  # noqa: BLE001
        log.warning("video not queued: %s", type(exc).__name__)
        return COULD_NOT
    return ("Queued. I'll listen to it in the next daily pass and send you the "
            "lessons as a card. Nothing is applied until you approve.")


# ------------------------------------------------------------------- the job

async def _queued(limit: int) -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        "SELECT video_id, source FROM video_lessons WHERE status = 'queued' "
        "ORDER BY queued_at LIMIT $1", limit)


async def _mark(video_id: str, status: str, reason: str | None = None) -> None:
    from .state import execute_sql
    await execute_sql(
        "UPDATE video_lessons SET status = $2, reason = $3 WHERE video_id = $1",
        video_id, status, reason)


async def _channel_candidates(room: int) -> tuple[list[dict[str, str]], str | None]:
    """The next channel videos not yet in the table, newest first, or UNKNOWN."""
    if room <= 0:
        return [], None
    from ..modules.academy import video_feed
    from .state import fetch_all
    cards = await video_feed.build()
    if cards is None:
        return [], "the channel's video list could not be read (UNKNOWN)"
    known = {str(r["video_id"]) for r in await fetch_all(
        "SELECT video_id FROM video_lessons WHERE video_id = ANY($1)",
        [c["id"] for c in cards])}
    return [c for c in cards if c["id"] not in known][:room], None


async def process(video_id: str, *, published: str = "unknown", title: str = "",
                  think: Any = None, reader: Any = None, meta: Any = None) -> str:
    """One video, queued -> drafted + card, or unreadable. Returns the outcome."""
    from ..modules.ytlearn import drafter, youtube
    from . import approvals
    from .state import execute_sql
    got = await youtube.fetch(video_id, published=published, title=title,
                              reader=reader, meta=meta)
    if isinstance(got, youtube.Unreadable):
        await _mark(video_id, "unreadable", got.reason)
        return f"unreadable: {got.reason}"
    made, why = await drafter.draft(got, think)
    if made is None:
        await _mark(video_id, "unreadable", why)
        return f"unreadable: {why}"
    params = {"video_id": video_id, "title": got.title, "channel": got.channel,
              "published": got.published, "source_url": got.url,
              "read_at": got.read_at.strftime("%Y-%m-%d %H:%M UTC"),
              "lessons": made.text(), "lessons_sha256": made.sha256}
    await execute_sql(
        """UPDATE video_lessons SET status = 'drafted', title = $2, channel = $3,
                  published = $4, lessons = $5, lessons_sha256 = $6, read_at = $7,
                  reason = NULL WHERE video_id = $1""",
        video_id, got.title, got.channel, got.published, made.text(), made.sha256,
        got.read_at)
    try:
        card = await approvals.propose(
            ACTION, params,
            "ATLAS listened to a YouTube video and drafted lessons; approving only "
            "saves them as a note for the owner to read",
            dedup_key=f"ytlearn:{video_id}",
            rollback_plan="nothing is applied; approval only marks the stored note "
                          "as approved knowledge")
    except Exception as exc:  # noqa: BLE001
        await _mark(video_id, "queued", f"card not raised ({type(exc).__name__})")
        return "card not raised; will retry"
    await execute_sql("UPDATE video_lessons SET card_id = $2 WHERE video_id = $1",
                      video_id, int(card))
    return f"card #{card}"


async def tick(**fakes: Any) -> dict[str, Any]:
    """One daily pass. Off unless YOUTUBE_LEARN=on; nothing under watch-only."""
    from .executor import watch_only
    if not enabled():
        return {"skipped": "off (YOUTUBE_LEARN)"}
    if watch_only():
        return {"held": "ATLAS_WATCH_ONLY is on; nothing was read"}
    status: dict[str, Any] = {"owner": {}, "channel": {}}
    # One pass reads at most owner + channel caps in total (8 by default), queued
    # rows first; the channel only gets what is left of that and of its own cap.
    budget = owner_per_day() + channel_per_day()
    queued = await _queued(budget)
    for row in queued:
        vid = str(row["video_id"])
        status["owner" if row["source"] == "owner" else "channel"][vid] = (
            await process(vid, **fakes))
    room = min(channel_per_day() - await _today_count("channel"),
               budget - len(queued))
    try:
        cards, why = await _channel_candidates(room)
    except Exception as exc:  # noqa: BLE001
        cards, why = [], f"the channel could not be read ({type(exc).__name__})"
    if why:
        status["channel_unknown"] = why
    for card in cards:
        if not await _enqueue(card["id"], "channel"):
            continue
        status["channel"][card["id"]] = await process(
            card["id"], published=card.get("published") or "unknown",
            title=card.get("title") or "", **fakes)
    return status
