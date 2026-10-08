"""TikTok, via the official developer APIs.

Reality check before anyone plans around this:

  * Display API   — read your own profile, videos and basic stats.
  * Content Posting API — upload and publish video. Two modes:
      DIRECT_POST   requires the app to pass audit; until then uploads land
                    in the user's drafts instead of publishing.
      UPLOAD        always lands in drafts for the user to finish in-app.
  * Research/Business APIs give richer analytics but need separate approval.
  * Tokens are short-lived (24h access, 365d refresh) and MUST be refreshed,
    or collection silently stops.

Until the app passes audit, ATLAS uses UPLOAD mode: it prepares the video and
puts it in Miro's TikTok drafts, then tells him it is ready to post. That is a
better default anyway — nothing reaches a public feed unattended.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)

API = "https://open.tiktokapis.com/v2"
_token_cache: dict[str, tuple[str, float]] = {}


async def _access_token() -> str:
    """Access tokens last 24h. Refresh proactively at 23h, because a token that
    expires mid-collection looks exactly like 'nothing happened today'."""
    cached = _token_cache.get("access")
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/oauth/token/", data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"],
        })
        r.raise_for_status()
        data = r.json()
    token = data["access_token"]
    _token_cache["access"] = (token, time.time() + int(data.get("expires_in", 86400)))
    return token


async def check_token() -> Observation:
    try:
        await _access_token()
        return Observation(check="token", health=Health.OK,
                           detail="refresh token still valid", )
    except Exception as exc:                            # noqa: BLE001
        return Observation(
            check="token", health=Health.FAILING,
            detail=f"TikTok refresh failed: {exc}. The 365-day refresh token "
                   "may have expired — re-authorise the app.",
        )


async def check_video_stats() -> Observation:
    token = await _access_token()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(
            f"{API}/video/list/",
            headers={"Authorization": f"Bearer {token}"},
            params={"fields": "id,view_count,like_count,comment_count,create_time"},
            json={"max_count": 20},
        )
        r.raise_for_status()
        body = r.json()
    # TikTok answers 200 with an `error` envelope. Read without it, and with
    # `.get(..., [])` defaults, a refused or renamed response read as "0 views
    # across the last 0 videos", OK (RA-E1).
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code not in (None, "ok"):
        return Observation(
            check="video_stats", health=Health.UNKNOWN,
            detail=f"TikTok /video/list/ answered with error code {code}")
    data = body.get("data") if isinstance(body, dict) else None
    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list):
        return Observation(
            check="video_stats", health=Health.UNKNOWN,
            detail="TikTok /video/list/ carried no data.videos list, so no "
                   "views were read")
    videos = [v for v in videos if isinstance(v, dict)]
    recent = sorted(videos, key=lambda v: v.get("create_time", 0), reverse=True)[:5]
    views = sum(v.get("view_count", 0) for v in recent)
    return Observation(
        check="video_stats", health=Health.OK, value=float(views), unit="views",
        detail=f"{views:,} views across the last {len(recent)} videos",
        evidence={"videos": recent},
    )


async def upload_draft(video_url: str, title: str = "") -> ActionResult:
    token = await _access_token()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{API}/post/publish/inbox/video/init/",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"source_info": {"source": "PULL_FROM_URL",
                                  "video_url": video_url}},
        )
        r.raise_for_status()
        publish_id = r.json()["data"]["publish_id"]
    return ActionResult(
        True,
        f"Uploaded to your TikTok drafts (id {publish_id}). Open the app to "
        f"add the caption and post.",
        rollback_token=publish_id,
    )


async def upload_dry(video_url: str, title: str = "") -> ActionResult:
    if not video_url.startswith("https://"):
        return ActionResult(False, "TikTok requires an https video URL")
    return ActionResult(True, f"would upload {video_url} to drafts")


class TikTokModule:
    name = "tiktok"
    title = "TikTok"
    requires_env = ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET",
                    "TIKTOK_REFRESH_TOKEN")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("token", check_token, timedelta(hours=6), Severity.HIGH,
                  "refresh token still valid", baseline_sigma=None),
            Check("video_stats", check_video_stats, timedelta(hours=6),
                  Severity.INFO, "views across recent videos"),
        )

    def actions(self):
        return (
            Action(
                name="tiktok.upload_draft",
                tier=Tier.ASK,
                summary="Upload a video to TikTok drafts for Miro to finish and post",
                run=upload_draft, dry_run=upload_dry,
                rollback=None, irreversible=True,
                params={"video_url": "public https URL of the video",
                        "title": "working title, for the notification only"},
                max_per_hour=4,
            ),
        )

    def brief(self) -> str:
        return "TikTok: drafts ready to post"


MODULE = TikTokModule()
