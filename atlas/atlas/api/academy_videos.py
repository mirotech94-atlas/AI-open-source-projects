"""The store's public video gallery: one read-only route.

    GET /public/academy/videos      the channel's public tutorials

`/api/...` is mesh-only, so this lives under `/public/academy/`: one exact
path in `main.PUBLIC_EXACT`, reached through the existing `/public/academy/*`
block of Caddyfile.public (that file is not widened). GET only; it takes no
input at all (no query, no body is read) and returns what
modules/academy/video_feed.py builds: id, title, description, date, and the
thumbnail and watch addresses made from the id. No cookies, nothing stored.

* 200 {"status": "ok", "videos": [...]} -- possibly an empty list: a channel
  with nothing public yet.
* 503 {"status": "unavailable"} -- the list could not be read. Never an empty
  "ok": the page shows "Videos are unavailable right now".
* 429 {"status": "unavailable"} -- too many requests; the page shows the same.

Cached 90 seconds in the process and for the browser (Cache-Control), so the
database is read at most about once a minute whoever asks. Rate limit: applied
only when the cache must be rebuilt, so cached answers are never refused. Behind
the public edge every visitor shares one bucket (peers.PUBLIC_CLIENT), so the
limit is a flood guard for the whole store, not a per-visitor fairness rule,
and is set generously; cached answers are cheap.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..modules.academy import video_feed
from .peers import PerAddressLimiter, effective_client

router = APIRouter()
log = logging.getLogger("atlas.api.academy_videos")

VIDEOS_PATH = "/public/academy/videos"

_limiter = PerAddressLimiter(120, 60.0)

_BASE = {"X-Content-Type-Options": "nosniff"}


def _unavailable(status: int) -> Response:
    return JSONResponse({"status": "unavailable"}, status_code=status,
                        headers={**_BASE, "Cache-Control": "no-store"})


@router.get(VIDEOS_PATH)
async def videos(request: Request) -> Response:
    hit, cards = video_feed.fresh()
    if not hit and not _limiter.allow(effective_client(request)):
        return _unavailable(429)
    try:
        if not hit:
            cards = await video_feed.feed()
    except Exception:                                   # noqa: BLE001
        log.warning("academy videos: the list could not be built")
        return _unavailable(503)
    if cards is None:
        log.warning("academy videos: the posts table is unreadable")
        return _unavailable(503)
    return JSONResponse({"status": "ok", "videos": cards},
                        headers={**_BASE, "Cache-Control":
                                 f"public, max-age={int(video_feed.CACHE_SECONDS)}"})
