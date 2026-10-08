"""youtube.api — YouTube Data API v3, YouTube Analytics API v2, OAuth refresh.

Three channels, each a fully separate Google Cloud OAuth client and a fully
separate YouTube channel: `flycheap`, `guardian` and `clips` (the clips
lane's Shorts channel). Every credential is namespaced by an env prefix
(`YOUTUBE_FLYCHEAP_` / `YOUTUBE_GUARDIAN_` / `YOUTUBE_CLIPS_`) so a key
pasted into the wrong variable is visibly wrong rather than silently
reading (or writing) another channel.

The `clips` channel uploads only a clip the clips lane cut: `upload`
refuses any other path for it (`clips_path_refusal`), and the
`youtube.upload` action refuses the channel outright -- the lane's own
`clips.publish_clip_youtube` is the one way onto it.

Endpoints this assumes, so Miro can correct them if wrong:
  * OAuth token refresh: `POST https://oauth2.googleapis.com/token`
  * Data API v3:          `https://www.googleapis.com/youtube/v3/...`
      - `channels.list`  (part=statistics)               — subscriber/view counts
      - `search.list`    (forMine=true, type=video)       — this channel's own videos
      - `videos.list`    (part=statistics,status,snippet) — per-video detail
      - `videos.update`  (part=status)                    — change privacyStatus
  * Resumable upload:     `POST .../upload/youtube/v3/videos
                                ?uploadType=resumable&part=snippet,status`
                          then a sequence of `PUT` calls to the `Location`
                          the init response returns, one 8MB chunk at a time.
  * Analytics API v2:      `https://youtubeanalytics.googleapis.com/v2/reports`

Nothing here decides tiers or writes to Postgres — that is `__init__.py`'s
job. Every function returns `(value, reason)` and never raises: a caller
turns a non-None `reason` into `Health.UNKNOWN` (a check) or a failed
`ActionResult` (an action), never into an invented number.

`videos.update` has no GET/POST-only helper in `_http.py` (it is a PUT), and
the resumable-upload init needs the `Location` response header rather than a
parsed body, so both go through `httpx` directly here, with `_http.explain`
for a secret-free failure reason — exactly the escape hatch `_http.py`'s
docstring describes.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import httpx

from .._http import expect_keys, explain, get_json, number, post_json

DATA_API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3/videos"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# channel name -> env var prefix. CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN and
# CHANNEL_ID hang off each prefix.
CHANNELS: dict[str, str] = {
    "flycheap": "YOUTUBE_FLYCHEAP_",
    "guardian": "YOUTUBE_GUARDIAN_",
    "clips": "YOUTUBE_CLIPS_",
}
CLIPS_CHANNEL = "clips"

CHUNK_SIZE = 8 * 1024 * 1024   # 8MB, per the brief; never slurp the whole file
PRIVACY_VALUES = ("private", "unlisted", "public")

# A 308 with no Range header means "retry the same offset", which is correct
# once -- a server that keeps answering bare 308s forever would otherwise
# hang the upload (and hold the open file and the httpx connection) with no
# way out. Three consecutive non-progress responses give up instead.
MAX_STALLED_RETRIES = 3

# channel -> (access_token, epoch-seconds it stops being trusted). Same
# pattern as atlas.modules.tiktok's `_token_cache`: refreshing on every call
# means every check, every upload, every visibility change pays a network
# round trip that a cache avoids for as long as the token is actually good.
# The stored expiry already has the 60s margin subtracted, so a plain
# `> time.time()` comparison at read time is enough.
_TOKEN_MARGIN_SECONDS = 60
_token_cache: dict[str, tuple[str, float]] = {}


class NoCredentials(Exception):
    """This channel's env vars are not set. Not the same as Google refusing
    them — that distinction is what tells Miro whether to configure
    something or to re-authorise something."""


def _env(channel: str, suffix: str) -> str:
    return os.getenv(f"{CHANNELS[channel]}{suffix}", "").strip()


def _unknown_channel(channel: str) -> str:
    return f"unknown channel {channel!r}; must be one of {', '.join(CHANNELS)}"


def _require_credentials(channel: str) -> tuple[str, str, str]:
    client_id = _env(channel, "CLIENT_ID")
    client_secret = _env(channel, "CLIENT_SECRET")
    refresh_token = _env(channel, "REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        prefix = CHANNELS[channel]
        raise NoCredentials(
            f"channel {channel!r} is not configured: no {prefix}CLIENT_ID / "
            f"{prefix}CLIENT_SECRET / {prefix}REFRESH_TOKEN set. Create an "
            f"OAuth client for this channel in Google Cloud Console and put "
            f"its credentials in .env.")
    return client_id, client_secret, refresh_token


async def token(channel: str) -> tuple[str | None, str | None]:
    """Refresh grant against oauth2.googleapis.com/token, cached per channel
    until shortly before it expires. Never logs the secret: failures go
    through `post_json`, whose reason is built by `_http.explain`/`redact`,
    which strips anything env-shaped that looks like a credential before it
    can reach an Observation.

    Without a cache, every check and every action for a channel pays a
    fresh refresh round trip -- `atlas.modules.tiktok` hit exactly this and
    fixed it with a module-level cache; this mirrors that pattern, keyed by
    channel since there are two of them here.
    """
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)

    cached = _token_cache.get(channel)
    if cached and cached[1] > time.time():
        return cached[0], None

    try:
        client_id, client_secret, refresh_token = _require_credentials(channel)
    except NoCredentials as exc:
        return None, str(exc)

    data, reason = await post_json(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if reason is not None:
        return None, f"{channel}: token refresh failed: {reason}"
    missing = expect_keys(data, "access_token", where=f"{channel} token response")
    if missing is not None:
        return None, f"{channel}: {missing}"

    access_token = data["access_token"]
    try:
        expires_in = float(data.get("expires_in", 3600))
    except (TypeError, ValueError):
        expires_in = 3600.0
    _token_cache[channel] = (
        access_token, time.time() + expires_in - _TOKEN_MARGIN_SECONDS)
    return access_token, None


async def channel_stats(channel: str) -> tuple[dict | None, str | None]:
    """channels.list part=statistics -> subscribers, views, videoCount, plus
    when it was read. A missing figure (hidden subscriber count, for one) is
    reported as None, never defaulted to zero."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    channel_id = _env(channel, "CHANNEL_ID")
    if not channel_id:
        return None, f"no {CHANNELS[channel]}CHANNEL_ID set for channel {channel!r}"

    tok, reason = await token(channel)
    if reason is not None:
        return None, reason

    data, reason = await get_json(
        f"{DATA_API}/channels",
        headers={"Authorization": f"Bearer {tok}"},
        params={"part": "statistics", "id": channel_id},
    )
    if reason is not None:
        return None, f"{channel}: {reason}"

    missing = expect_keys(data, "items", where=f"{channel} channels.list")
    if missing is not None:
        return None, missing
    items = data["items"]
    if not items:
        return None, f"{channel}: no channel found for id {channel_id!r}"

    stats = items[0].get("statistics")
    if not isinstance(stats, dict):
        return None, f"{channel}: channels.list item has no statistics block"

    subs, _ = number(stats, "subscriberCount", where=f"{channel} statistics")
    views, _ = number(stats, "viewCount", where=f"{channel} statistics")
    video_count, _ = number(stats, "videoCount", where=f"{channel} statistics")
    read_at = datetime.now(UTC)
    return {
        "channel": channel,
        "subscribers": subs,
        "views": views,
        "video_count": video_count,
        "hidden_subscriber_count": bool(stats.get("hiddenSubscriberCount", False)),
        "read_at": read_at,
    }, None


async def videos(channel: str, limit: int = 25) -> tuple[list[dict], str | None]:
    """search.list(forMine=true) for this channel's own video ids, then
    videos.list(part=statistics,status,snippet) for the detail. Any stat
    YouTube omits (likes/comments disabled, for instance) comes back as
    None — never invented as 0."""
    if channel not in CHANNELS:
        return [], _unknown_channel(channel)

    tok, reason = await token(channel)
    if reason is not None:
        return [], reason

    search_data, reason = await get_json(
        f"{DATA_API}/search",
        headers={"Authorization": f"Bearer {tok}"},
        params={
            "part": "id",
            "forMine": "true",
            "type": "video",
            "order": "date",
            "maxResults": str(max(1, min(limit, 50))),
        },
    )
    if reason is not None:
        return [], f"{channel}: {reason}"

    search_items = search_data.get("items") if isinstance(search_data, dict) else None
    if not isinstance(search_items, list):
        return [], f"{channel}: search.list returned no items list"

    ids = [
        item["id"]["videoId"]
        for item in search_items
        if isinstance(item.get("id"), dict) and item["id"].get("videoId")
    ]
    if not ids:
        return [], None   # no videos yet — not an error

    list_data, reason = await get_json(
        f"{DATA_API}/videos",
        headers={"Authorization": f"Bearer {tok}"},
        params={"part": "statistics,status,snippet", "id": ",".join(ids)},
    )
    if reason is not None:
        return [], f"{channel}: {reason}"

    list_items = list_data.get("items") if isinstance(list_data, dict) else None
    if not isinstance(list_items, list):
        return [], f"{channel}: videos.list returned no items list"

    # One timestamp for the whole batch: they were all read in the same
    # pair of calls, and a measured number (views, likes, comments) with no
    # read time is exactly the invariant-6 failure this stamps shut.
    read_at = datetime.now(UTC)
    out = []
    for item in list_items:
        stats = item.get("statistics") or {}
        snippet = item.get("snippet") or {}
        status = item.get("status") or {}
        views, _ = number(stats, "viewCount", where=f"{channel} videos.list")
        likes, _ = number(stats, "likeCount", where=f"{channel} videos.list")
        comments, _ = number(stats, "commentCount", where=f"{channel} videos.list")
        out.append({
            "video_id": item.get("id"),
            "title": snippet.get("title"),
            "published_at": snippet.get("publishedAt"),
            "privacy": status.get("privacyStatus"),
            "views": views,
            "likes": likes,
            "comments": comments,
            "read_at": read_at,
        })
    return out, None


async def current_privacy(channel: str, video_id: str) -> tuple[str | None, str | None]:
    """The live privacyStatus for one video, read fresh — never trusted from
    a possibly-stale local row. `set_visibility`'s rollback token depends on
    this being the true current value, not a cached one."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    data, reason = await get_json(
        f"{DATA_API}/videos",
        headers={"Authorization": f"Bearer {tok}"},
        params={"part": "status", "id": video_id},
    )
    if reason is not None:
        return None, f"{channel}: {reason}"
    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        return None, f"{channel}: no such video {video_id!r}"
    status = items[0].get("status") or {}
    privacy = status.get("privacyStatus")
    if privacy is None:
        return None, f"{channel}: {video_id} has no privacyStatus in its status block"
    return privacy, None


async def _put_json(
    url: str, *, headers: dict[str, str], params: dict[str, str], json_body: dict
) -> tuple[dict | None, str | None]:
    """`videos.update` is a PUT, which `_http.py` does not wrap (it only
    offers GET/POST). Same never-raises contract, same `explain` for a
    secret-free reason."""
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.put(url, headers=headers, params=params, json=json_body)
            r.raise_for_status()
            return r.json(), None
    except Exception as exc:                            # noqa: BLE001
        return None, explain(exc)


async def set_privacy(channel: str, video_id: str, privacy: str) -> tuple[dict | None, str | None]:
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    if privacy not in PRIVACY_VALUES:
        return None, f"invalid privacy {privacy!r}; must be one of {', '.join(PRIVACY_VALUES)}"
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    data, reason = await _put_json(
        f"{DATA_API}/videos",
        headers={"Authorization": f"Bearer {tok}"},
        params={"part": "status"},
        json_body={"id": video_id, "status": {"privacyStatus": privacy}},
    )
    if reason is not None:
        return None, f"{channel}: {reason}"
    return data, None


async def analytics(channel: str, video_id: str, days: int) -> tuple[dict | None, str | None]:
    """youtubeanalytics reports.query, metrics=views,estimatedMinutesWatched,
    likes for one video over the trailing `days`. A 403 here almost always
    means the token was never granted the analytics readonly scope — that
    surfaces through `_http.explain` as "authenticated but not permitted",
    which the caller turns into Health.UNKNOWN, not FAILING: the channel is
    fine, the scope grant is what is missing."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    channel_id = _env(channel, "CHANNEL_ID")
    if not channel_id:
        return None, f"no {CHANNELS[channel]}CHANNEL_ID set for channel {channel!r}"

    tok, reason = await token(channel)
    if reason is not None:
        return None, reason

    end = datetime.now(UTC).date()
    start = end - timedelta(days=max(days, 1))
    data, reason = await get_json(
        ANALYTICS_API,
        headers={"Authorization": f"Bearer {tok}"},
        params={
            "ids": f"channel=={channel_id}",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "metrics": "views,estimatedMinutesWatched,likes",
            "filters": f"video=={video_id}",
        },
    )
    if reason is not None:
        scope_hint = (
            " — the token likely lacks the yt-analytics.readonly scope"
            if "403" in reason or "not permitted" in reason else "")
        return None, f"{channel}: {reason}{scope_hint}"

    columns = data.get("columnHeaders") if isinstance(data, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    if not columns or not rows:
        return None, f"{channel}: analytics returned no rows for {video_id!r}"

    names = [c.get("name") for c in columns]
    values = dict(zip(names, rows[0], strict=False))
    return {
        "views": values.get("views"),
        "estimated_minutes_watched": values.get("estimatedMinutesWatched"),
        "likes": values.get("likes"),
        "read_at": datetime.now(UTC),
    }, None


def clips_path_refusal(path: str) -> str | None:
    """The clips channel takes only a clip the clips lane cut: a plain
    `.mp4` directly inside `<CLIPS_DATA_DIR>/<campaign>/out/`, never a
    symlink and never anything else on disk. Why not, or None."""
    from ..clips import sources
    if not sources.lane_clip(path):
        return ("the clips channel uploads only a clip the clips lane cut and "
                "checked, through clips.publish_clip_youtube")
    return None


def _read_chunk(path: str, offset: int, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


async def upload(
    channel: str,
    path: str,
    title: str,
    description: str,
    tags: Sequence[str],
    privacy: str = "private",
) -> tuple[dict | None, str | None]:
    """Resumable upload, 8MB chunks, file reads under `asyncio.to_thread`.

    `privacy` is refused unless it is exactly `"private"` — nothing is
    public on upload, and that is enforced here, not just by a default
    argument a caller could override. Going public is a separate, later,
    Tier ASK decision (`set_visibility`).
    """
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    if privacy != "private":
        return None, (
            "youtube.upload always lands private — nothing is public on "
            "upload. Call youtube.set_visibility afterwards to publish.")
    # The clips path check reads the filesystem: off the event loop.
    if channel == CLIPS_CHANNEL and (
            why := await asyncio.to_thread(clips_path_refusal, path)) is not None:
        return None, f"{channel}: {why}"

    tok, reason = await token(channel)
    if reason is not None:
        return None, reason

    try:
        size = await asyncio.to_thread(os.path.getsize, path)
    except OSError as exc:
        return None, f"{channel}: cannot read {path!r}: {exc}"
    if size <= 0:
        return None, f"{channel}: {path!r} is empty"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": list(tags)[:15],
        },
        "status": {"privacyStatus": "private"},
    }

    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c:
            init = await c.post(
                UPLOAD_API,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/*",
                    "X-Upload-Content-Length": str(size),
                },
                json=body,
            )
            init.raise_for_status()
            session_uri = init.headers.get("Location") or init.headers.get("location")
            if not session_uri:
                return None, f"{channel}: upload session had no Location header"

            video_id: str | None = None
            offset = 0
            stalled_retries = 0
            while offset < size:
                chunk = await asyncio.to_thread(_read_chunk, path, offset, CHUNK_SIZE)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                resp = await c.put(
                    session_uri,
                    headers={
                        "Authorization": f"Bearer {tok}",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                    content=chunk,
                )
                if resp.status_code in (200, 201):
                    video_id = resp.json().get("id")
                    break
                if resp.status_code != 308:
                    resp.raise_for_status()

                # "308 Resume Incomplete" reports what it actually has in
                # the `Range` response header (e.g. "bytes=0-1048575"), which
                # is not necessarily "all of what we just sent" -- a partial
                # write on the server side means resuming from `offset +
                # len(chunk)` would skip bytes the server never got. Absent
                # a Range header (nothing persisted yet), or one this parser
                # cannot read, retry from the same offset rather than
                # advancing blind.
                range_header = resp.headers.get("Range") or resp.headers.get("range")
                new_offset = offset
                if range_header and "-" in range_header:
                    try:
                        new_offset = int(range_header.rsplit("-", 1)[-1]) + 1
                    except ValueError:
                        new_offset = offset

                if new_offset == offset:
                    # A single stalled retry is normal (a slow write on the
                    # server's side). A server that never progresses would
                    # otherwise hold this loop, the open file and the httpx
                    # connection open forever.
                    stalled_retries += 1
                    if stalled_retries >= MAX_STALLED_RETRIES:
                        return None, (
                            f"{channel}: resumable upload made no progress "
                            f"after {MAX_STALLED_RETRIES} retries at byte "
                            f"{offset}")
                else:
                    stalled_retries = 0
                offset = new_offset
    except Exception as exc:                            # noqa: BLE001
        return None, f"{channel}: {explain(exc)}"

    if not video_id:
        return None, f"{channel}: resumable upload finished without a video id"
    return {"video_id": video_id, "channel": channel, "privacy": "private"}, None
