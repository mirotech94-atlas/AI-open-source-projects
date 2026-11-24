"""The public video gallery's data: GET /public/academy/videos
(atlas/api/academy_videos.py) answers with what this builds.

Read-only. The list is the channel's tutorials that are PUBLIC on YouTube and
not withdrawn (publish.public_videos: academy_tutorial_posts, `WHERE public
AND NOT withdrawn`), resolved against the tutorial catalog; a private,
unlisted, scheduled or unknown upload is never in it. Every field is made in
code: the title and description are the catalog episode's own text, capped
and stripped of markup characters; the thumbnail and watch addresses are built
from the 11-character video id alone, never from stored text; nothing a
visitor typed is ever involved.

`None` means the source could not be read, which is not the same as an empty
channel: the endpoint answers 503 and the page says the videos are
unavailable (invariant 7: UNKNOWN, never an empty OK). A good answer is
cached for CACHE_SECONDS; a failed read is remembered for FAILURE_SECONDS
(30 s), so a recovering database is not hammered. A failure never shows as an
empty list, and the next rebuild after either window replaces the entry.
Concurrent requests that find the cache expired rebuild it once (a lock).
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from . import publish, site

CACHE_SECONDS = 90.0
FAILURE_SECONDS = 30.0
TITLE_MAX = 100
DESCRIPTION_MAX = 200
THUMB = "https://i.ytimg.com/vi/{id}/hqdefault.jpg"
WATCH = "https://www.youtube.com/watch?v={id}"

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MARKUP = re.compile(r"[<>]")
_cache: dict[str, Any] = {"at": 0.0, "feed": None, "ttl": 0.0}
_lock: dict[str, Any] = {"loop": None, "lock": None}


def clean(text: Any, limit: int) -> str:
    """Untrusted-looking text made safe to show: no control or markup
    characters, whitespace collapsed, at most `limit` characters."""
    if not isinstance(text, str):
        return ""
    out = " ".join(_MARKUP.sub("", _CONTROL.sub(" ", text)).split())
    if len(out) > limit:
        out = out[:limit - 1].rsplit(" ", 1)[0].rstrip() + "…"
    return out[:limit]


def entry(episode: Any, video: site.Video) -> dict[str, str] | None:
    """One gallery card, or None when the id is not a well-formed video id."""
    if not site.VIDEO_ID_RE.fullmatch(video.video_id):
        return None
    return {
        "id": video.video_id,
        "title": clean(episode.title, TITLE_MAX),
        "description": clean(site._first_sentence(episode.summary or ""),
                             DESCRIPTION_MAX),
        "published": video.uploaded.date().isoformat(),
        "thumbnail": THUMB.format(id=video.video_id),
        "watch": WATCH.format(id=video.video_id),
    }


async def build() -> list[dict[str, str]] | None:
    """The gallery, newest first; None when the posts table is unreadable."""
    videos = await publish.public_videos()
    if videos is None:
        return None
    cards = []
    for _series, episode, video in site.published(videos):
        card = entry(episode, video)
        if card is not None:
            cards.append((video.uploaded, card))
    cards.sort(key=lambda pair: pair[0], reverse=True)
    return [card for _at, card in cards]


def clear_cache() -> None:
    _cache.update(at=0.0, feed=None, ttl=0.0)
    _lock.update(loop=None, lock=None)


def fresh(now: float | None = None) -> tuple[bool, list[dict[str, str]] | None]:
    """(True, cached value) while the cache holds an answer, good or a
    remembered failure; (False, None) when a rebuild is needed."""
    t = time.monotonic() if now is None else now
    if _cache["at"] and t - _cache["at"] < _cache["ttl"]:
        return True, _cache["feed"]
    return False, None


def _the_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _lock["loop"] is not loop:
        _lock.update(loop=loop, lock=asyncio.Lock())
    return _lock["lock"]


async def feed(now: float | None = None) -> list[dict[str, str]] | None:
    """`build`, cached; None while the source is unreadable. Concurrent
    callers after an expiry wait on one rebuild."""
    hit, got = fresh(now)
    if hit:
        return got
    async with _the_lock():
        hit, got = fresh(now)
        if hit:
            return got
        t = time.monotonic() if now is None else now
        got = await build()
        _cache.update(at=t, feed=got,
                      ttl=CACHE_SECONDS if got is not None else FAILURE_SECONDS)
        return got
