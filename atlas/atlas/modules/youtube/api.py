"""youtube.api — YouTube Data API v3, YouTube Analytics API v2, OAuth refresh.

Each channel is a fully separate Google Cloud OAuth client and a fully
separate YouTube channel: `clips` (the clips lane's Shorts channel) and
`ecc`. FlyCheap and Guardian were removed on 2026-10-29. Every credential
is namespaced by an env prefix (`YOUTUBE_CLIPS_` / `YOUTUBE_ECC_`) so a key
pasted into the wrong variable is visibly wrong rather than silently
reading (or writing) another channel.

The `clips` channel uploads only a clip the clips lane cut: `upload`
refuses any other path for it (`clips_path_refusal`), and the
`youtube.upload` action refuses the channel outright -- the lane's own
`clips.publish_clip_youtube` is the one way onto it.

Endpoints this assumes, so the owner can correct them if wrong:
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
import json
import os
import re
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import httpx

from ...kernel import idempotency
from .._http import expect_keys, explain, get_json, number, post_json

DATA_API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3/videos"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# channel name -> env var prefix. CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN and
# CHANNEL_ID hang off each prefix.
CHANNELS: dict[str, str] = {
    "clips": "YOUTUBE_CLIPS_",
    # The ECC bookstore channel (@example_handle). It takes uploads only
    # through `church.schedule_youtube_shorts`, one card per batch, each video
    # reviewed by the owner first (atlas/modules/church).
    "ecc": "YOUTUBE_ECC_",
    # The MMT account (second monitored YouTube account, the owner 2026-11-13).
    "mmt": "YOUTUBE_MMT_",
}
CLIPS_CHANNEL = "clips"
ECC_CHANNEL = "ecc"

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
    them — that distinction is what tells the owner whether to configure
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


async def video_status(channel: str, video_id: str) -> tuple[dict | None, str | None]:
    """One video's whole `status` block, read fresh (videos.list part=status).
    Read-only; (None, why) when it cannot be read."""
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
    if not items or not isinstance(items[0].get("status"), dict):
        return None, f"{channel}: no status read for video {video_id!r}"
    return dict(items[0]["status"]), None


# The status fields videos.update writes. One left out of a part=status update
# is DELETED (privacy reverts to the default), except containsSyntheticMedia
# and selfDeclaredMadeForKids, which "will only update if explicitly set"
# (developers.google.com/youtube/v3/docs/videos/update, read 2026-10-17). So
# a flag change resends every one of them as just read.
WRITABLE_STATUS = ("privacyStatus", "license", "embeddable", "publicStatsViewable",
                   "publishAt", "selfDeclaredMadeForKids")


async def _update_status(channel: str, video_id: str, changes: dict, *,
                         keep_schedule: bool
                         ) -> tuple[dict | None, dict | None, str | None]:
    """The one status write: read the status fresh, resend every
    WRITABLE_STATUS field as read with `changes` over it, PUT part=status.
    `(status as read, response, None)`, or `(None, None, why)` with nothing
    written when the read fails or carries no privacyStatus. publishAt goes
    back only with `keep_schedule` on a video that stays private (YouTube
    takes it on private only)."""
    current, reason = await video_status(channel, video_id)
    if current is None:
        return None, None, reason
    if "privacyStatus" not in current:
        return None, None, (f"{channel}: {video_id} read back with no privacyStatus; "
                            f"not updated, an update without it would reset the "
                            f"privacy")
    status = {k: current[k] for k in WRITABLE_STATUS if k in current}
    status.update(changes)
    if not keep_schedule or status["privacyStatus"] != "private":
        status.pop("publishAt", None)
    tok, reason = await token(channel)
    if reason is not None:
        return None, None, reason
    data, reason = await _put_json(
        f"{DATA_API}/videos",
        headers={"Authorization": f"Bearer {tok}"},
        params={"part": "status"},
        json_body={"id": video_id, "status": status},
    )
    if reason is not None:
        return None, None, f"{channel}: {reason}"
    return current, data, None


async def set_synthetic(channel: str, video_id: str,
                        value: bool) -> tuple[bool | None, str | None]:
    """status.containsSyntheticMedia = `value` on one video, every other
    writable status field resent exactly as read the moment before. Returns
    the flag's previous value (absent reads as False), or (None, why) with
    nothing written when the status cannot be read whole."""
    current, _data, reason = await _update_status(
        channel, video_id, {"containsSyntheticMedia": bool(value)}, keep_schedule=True)
    if current is None:
        return None, reason
    return bool(current.get("containsSyntheticMedia", False)), None


# The snippet fields videos.update writes (developers.google.com/youtube/v3/
# docs/videos/update, read 2026-10-18: title and categoryId are required with
# part=snippet, and "the property's existing value will be deleted" for one a
# request leaves out). defaultAudioLanguage is not on that page's writable
# list, but our uploads set it, so it is resent as read rather than risked.
WRITABLE_SNIPPET = ("title", "categoryId", "description", "tags", "defaultLanguage",
                    "defaultAudioLanguage")
_DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def duration_seconds(text: str) -> int | None:
    """contentDetails.duration ("PT4M31S", "PT1H2M", "P0D") in seconds, or
    None when it is not an ISO 8601 duration of that shape."""
    m = _DURATION.match(str(text or ""))
    if m is None or text in ("P", "PT"):
        return None
    d, h, mn, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mn) * 60 + s


async def video_snippet(channel: str, video_id: str
                        ) -> tuple[dict | None, int | None, str | None]:
    """One video's `snippet` and its length in seconds, read fresh in one
    videos.list call (part=snippet,contentDetails: 1 quota unit). Read-only.
    `(None, None, why)` when it cannot be read, or when the snippet lacks the
    title or categoryId an update must resend. What comes back is the
    channel's own text but arrives from outside: data, never instruction."""
    if channel not in CHANNELS:
        return None, None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, None, reason
    data, reason = await get_json(
        f"{DATA_API}/videos",
        headers={"Authorization": f"Bearer {tok}"},
        params={"part": "snippet,contentDetails", "id": video_id},
    )
    if reason is not None:
        return None, None, f"{channel}: {reason}"
    items = data.get("items") if isinstance(data, dict) else None
    if not items or not isinstance(items[0].get("snippet"), dict):
        return None, None, f"{channel}: no snippet read for video {video_id!r}"
    snippet = dict(items[0]["snippet"])
    if not snippet.get("title") or not snippet.get("categoryId"):
        return None, None, (f"{channel}: {video_id} read back without a title or "
                            f"categoryId; an update without them would fail or clear them")
    seconds = duration_seconds((items[0].get("contentDetails") or {}).get("duration", ""))
    return snippet, seconds, None


def snippet_body(current: dict, *, tags: Sequence[str], description: str) -> dict:
    """What an update sends: every WRITABLE_SNIPPET field exactly as read,
    with only `tags` and `description` replaced. Nothing else: never the
    read-only keys (publishedAt, thumbnails, localized ...)."""
    body = {k: current[k] for k in WRITABLE_SNIPPET if k in current}
    body["tags"] = tags_within(tags)
    body["description"] = description
    return body


async def update_snippet(channel: str, video_id: str, *, tags: Sequence[str],
                         description: str) -> tuple[dict | None, str | None]:
    """videos.update part=snippet (50 quota units), changing only the tags
    and the description: the snippet is read fresh and every other writable
    field (title, categoryId, the languages) resent as read, because an
    omitted one is deleted. Returns the snippet as read the moment before,
    or (None, why) with nothing written when that read fails."""
    current, _seconds, reason = await video_snippet(channel, video_id)
    if current is None:
        return None, reason
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    _data, reason = await _put_json(
        f"{DATA_API}/videos",
        headers={"Authorization": f"Bearer {tok}"},
        params={"part": "snippet"},
        json_body={"id": video_id,
                   "snippet": snippet_body(current, tags=tags, description=description)},
    )
    if reason is not None:
        return None, f"{channel}: {reason}"
    return current, None


async def update_title(channel: str, video_id: str,
                       title: str) -> tuple[dict | None, str | None]:
    """videos.update part=snippet changing only the title (the Academy's
    A/B title test, phase 8): the same path as `update_snippet` -- the
    snippet read fresh and every other writable field, tags and description
    included, resent as read. Returns the snippet as read the moment before,
    or (None, why) with nothing written."""
    title = " ".join(str(title or "").split())
    if not title or len(title) > 100:
        return None, "a YouTube title is 1 to 100 characters"
    current, _seconds, reason = await video_snippet(channel, video_id)
    if current is None:
        return None, reason
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    body = snippet_body(current, tags=current.get("tags") or [],
                        description=str(current.get("description") or ""))
    body["title"] = title
    _data, reason = await _put_json(
        f"{DATA_API}/videos", headers={"Authorization": f"Bearer {tok}"},
        params={"part": "snippet"}, json_body={"id": video_id, "snippet": body})
    if reason is not None:
        return None, f"{channel}: {reason}"
    return current, None


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


async def set_privacy(channel: str, video_id: str, privacy: str, *,
                      synthetic: bool = False) -> tuple[dict | None, str | None]:
    """videos.update, part=status, through `_update_status`: every other
    writable status field is resent as read the moment before, so a
    visibility change no longer resets licence, embeddable or stats.
    `synthetic` (the Academy's AI-voiced videos, the owner 2026-10-17) sends the
    flag again; it is never sent false. Nothing is written when the status
    cannot be read."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    if privacy not in PRIVACY_VALUES:
        return None, f"invalid privacy {privacy!r}; must be one of {', '.join(PRIVACY_VALUES)}"
    changes: dict = {"privacyStatus": privacy}
    if synthetic:
        changes["containsSyntheticMedia"] = True
    # A visibility change is a decision now: any pending publishAt is
    # dropped, as it always was when the field went unsent.
    _current, data, reason = await _update_status(channel, video_id, changes,
                                                  keep_schedule=False)
    if reason is not None:
        return None, reason
    return data or {}, None


# 2026-10-13 (academy growth plan, task 6): the quiz + lab-file comment under
# a tutorial. Called only from academy.pin_comment's run and rollback. The
# API cannot pin (the owner pins it in Studio) and nothing here deletes: deletion
# is Tier 3, so the rollback edits the text instead.
COMMENT_MAX = 9000                    # YouTube's comment limit is 10,000 characters


async def post_comment(channel: str, video_id: str, text: str, *,
                       parent_id: str | None = None) -> tuple[dict | None, str | None]:
    """commentThreads.insert as the channel (youtube.force-ssl scope). The id
    returned is the top-level comment's, which comments.update takes. With
    `parent_id` (academy.send_comment_reply, phase 8) it is comments.insert:
    a reply under that top-level comment instead."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    if parent_id:
        data, reason = await post_json(
            f"{DATA_API}/comments", headers={"Authorization": f"Bearer {tok}"},
            params={"part": "snippet"},
            json_body={"snippet": {"parentId": parent_id,
                                   "textOriginal": text[:COMMENT_MAX]}})
        if reason is not None:
            return None, reason
        if not isinstance(data, dict) or not data.get("id"):
            return None, "expected JSON with an id: comments.insert returned none"
        return {"id": str(data["id"]), "thread_id": str(parent_id)}, None
    data, reason = await post_json(
        f"{DATA_API}/commentThreads", headers={"Authorization": f"Bearer {tok}"},
        params={"part": "snippet"},
        json_body={"snippet": {"videoId": video_id, "topLevelComment": {
            "snippet": {"textOriginal": text[:COMMENT_MAX]}}}})
    if reason is not None:
        # Unprefixed, unlike the other calls: the caller's idempotency.finish
        # reads it (a timeout or a 5xx keeps the claim -- the comment may be up).
        return None, reason
    if not isinstance(data, dict) or not data.get("id"):
        # A 2xx with no id may still have posted: "expected JSON" is what
        # idempotency.uncertain() reads as unknown, so the claim is kept.
        return None, "expected JSON with an id: commentThreads.insert returned none"
    top = ((data.get("snippet") or {}).get("topLevelComment") or {}).get("id")
    return {"id": str(top or data["id"]), "thread_id": str(data["id"])}, None


async def update_comment(channel: str, comment_id: str,
                         text: str) -> tuple[dict | None, str | None]:
    """comments.update: replace a comment's text (the pinned comment's
    rollback -- an edit, never a delete)."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    data, reason = await _put_json(
        f"{DATA_API}/comments", headers={"Authorization": f"Bearer {tok}"},
        params={"part": "snippet"},
        json_body={"id": comment_id, "snippet": {"textOriginal": text[:COMMENT_MAX]}})
    if reason is not None:
        return None, f"{channel}: {reason}"
    return data, None


async def analytics(channel: str, video_id: str, days: int) -> tuple[dict | None, str | None]:
    """youtubeanalytics reports.query, metrics=views,estimatedMinutesWatched,
    likes,comments,averageViewDuration,averageViewPercentage for one video
    over the trailing `days` (a column the API leaves out reads None, never
    0). A 403 here almost always
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
            "metrics": ("views,estimatedMinutesWatched,likes,comments,"
                        "averageViewDuration,averageViewPercentage"),
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
        "comments": values.get("comments"),
        "average_view_duration": values.get("averageViewDuration"),
        "average_view_percentage": values.get("averageViewPercentage"),
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


def academy_video_path(path: str) -> bool:
    """the owner 2026-11-14: Academy lesson, preview and Short renders that passed the
    Academy's own gate. A plain `.mp4` directly inside ACADEMY_STAGING_DIR
    (default data/academy-staging/video), never a symlink. Blocking."""
    try:
        if os.path.islink(path):
            return False
        real = os.path.realpath(path)
        base = os.path.realpath(os.getenv("ACADEMY_STAGING_DIR",
                                          "data/academy-staging/video"))
    except OSError:
        return False
    name = os.path.basename(real)
    if not (os.path.dirname(real) == base and name.endswith(".mp4")
            and not name.startswith(".") and os.path.isfile(real)):
        return False
    # The gate artifact (security review 2026-11-14): `<name>.gate.json` beside the
    # file, {"approved": true, "sha256": <hex of the file>}, written by the Academy
    # render gate. A missing, unapproved or stale (file changed) sidecar refuses.
    import hashlib
    import json
    try:
        gate = json.loads(open(real + ".gate.json", encoding="utf-8").read())
        h = hashlib.sha256()
        with open(real, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except (OSError, ValueError):
        return False
    return (isinstance(gate, dict) and gate.get("approved") is True
            and gate.get("sha256") == h.hexdigest())


def _read_chunk(path: str, offset: int, size: int) -> bytes:
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size)


TAGS_CHARS = 500                      # YouTube's tag limit, as it counts them
EDUCATION = "27"                      # videoCategories: Education
TUTORIAL_LANGUAGE = "en-GB"           # the British male voice


def tags_within(tags: Sequence[str]) -> list[str]:
    """The leading tags that fit YouTube's 500 characters: a tag counts its
    length, plus two when it has a space (YouTube quotes it), plus the comma
    before it -- tutorial_series.tags_cost's arithmetic (final review I4)."""
    out: list[str] = []
    used = 0
    for t in tags:
        cost = len(t) + (2 if " " in t else 0) + (1 if out else 0)
        if used + cost > TAGS_CHARS:
            break
        out.append(t)
        used += cost
    return out


async def upload(
    channel: str,
    path: str,
    title: str,
    description: str,
    tags: Sequence[str],
    privacy: str = "private",
    *,
    category_id: str | None = None,
    language: str | None = None,
    publish_at: datetime | None = None,
    synthetic: bool = False,
) -> tuple[dict | None, str | None]:
    """Resumable upload, 8MB chunks, file reads under `asyncio.to_thread`.

    `privacy` is refused unless it is exactly `"private"` — nothing is
    public on upload, and that is enforced here, not just by a default
    argument a caller could override. Going public is a separate, later,
    Tier ASK decision (`set_visibility`).

    `synthetic` sets `status.containsSyntheticMedia` (the Academy's
    AI-voiced tutorials, the owner 2026-10-17; developers.google.com/youtube/v3/
    docs/videos, read 2026-10-17). Off by default: clips and church are not.

    `publish_at` (timezone-aware, in the future) is YouTube's own scheduled
    publishing: the video still lands private and carries
    `status.publishAt`, and YouTube makes it public at that time. Only the
    church lane passes it, from a card the owner approved for that batch.
    """
    if publish_at is not None and (
            publish_at.tzinfo is None or publish_at <= datetime.now(UTC)):
        return None, "publish_at must be a timezone-aware time in the future"
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    if privacy != "private":
        return None, (
            "youtube.upload always lands private — nothing is public on "
            "upload. Call youtube.set_visibility afterwards to publish.")
    # The clips path check reads the filesystem: off the event loop.
    # The one other file the clips channel takes: a finished Academy
    # tutorial render narrated in the Fish voice (academy/tutorials.py
    # `publishable_render`, the owner 2026-10-09), never a placeholder preview.
    if channel == CLIPS_CHANNEL and (
            why := await asyncio.to_thread(clips_path_refusal, path)) is not None:
        from ..academy.tutorials import publishable_render
        if (no := await asyncio.to_thread(publishable_render, path)) is not None:
            if not await asyncio.to_thread(academy_video_path, path):
                return None, f"{channel}: {why}; and not an Academy tutorial: {no}"

    tok, reason = await token(channel)
    if reason is not None:
        return None, reason

    try:
        size = await asyncio.to_thread(os.path.getsize, path)
    except OSError as exc:
        return None, f"{channel}: cannot read {path!r}: {exc}"
    if size <= 0:
        return None, f"{channel}: {path!r} is empty"

    snippet: dict = {
        "title": title[:100],
        "description": description[:5000],
        "tags": tags_within(tags),
    }
    if category_id:
        snippet["categoryId"] = category_id
    if language:
        snippet["defaultLanguage"] = language
        snippet["defaultAudioLanguage"] = language
    status: dict = {"privacyStatus": "private"}
    if synthetic:
        status["containsSyntheticMedia"] = True
    if publish_at is not None:
        status["publishAt"] = publish_at.astimezone(UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    body = {"snippet": snippet, "status": status}

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
                    # A 2xx whose body cannot be read may still be a video:
                    # "expected JSON" is what idempotency.uncertain() keeps.
                    try:
                        done = resp.json()
                    except ValueError:
                        return None, ("expected JSON from the final upload "
                                      "chunk; YouTube may hold the video")
                    video_id = done.get("id") if isinstance(done, dict) else None
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
        # Final review I1, as post_comment (604317b): a reason whose outcome is
        # unknown (a timeout, a 5xx) stays unprefixed, so the caller's
        # idempotency.finish keeps its claim -- YouTube may hold the video.
        why = explain(exc)
        return None, why if idempotency.uncertain(why) else f"{channel}: {why}"

    if not video_id:
        return None, ("expected JSON with a video id: the resumable upload "
                      "finished without one; YouTube may hold the video")
    return {"video_id": video_id, "channel": channel, "privacy": "private"}, None


# ---------------------------------------------------------------- playlists
#
# The Academy tutorials (academy/tutorials.py) go into playlist series.
# Endpoints, read 2026-10-09 on developers.google.com/youtube/v3/docs:
#   playlists.list    GET  /playlists?part=snippet,status&mine=true
#   playlists.insert  POST /playlists?part=snippet,status
#   playlists.update  PUT  /playlists?part=snippet,status  (privacy back)
#   playlistItems.insert POST /playlistItems?part=snippet
#       {snippet: {playlistId, resourceId: {kind: youtube#video, videoId}}}
#   playlistItems.delete DELETE /playlistItems?id=  (takes an entry out of
#       a playlist; the video itself is untouched)
#   thumbnails.set    POST /upload/youtube/v3/thumbnails/set?videoId=
#       image/jpeg or image/png
# The playlist calls need the `youtube` or `youtube.force-ssl` scope; a
# refresh token granted only `youtube.upload` gets a 403 here, reported as
# such, never as a success. Deleting a playlist or a video is not here at
# all: deletion is Tier 3.

async def channel_report(channel: str, *, start: date, end: date, metrics: str,
                         dimensions: str) -> tuple[dict | None, str | None]:
    """reports.query for the whole channel (ids=channel==MINE: the channel
    the token belongs to), grouped by `dimensions`. Read-only.

    -> ({"rows": [{column: value}, ...], "read_at": datetime}, None). An
    answer with column headers and no rows is a read of nothing happening
    (the API omits empty rows), so it is [] -- but an answer without
    column headers is not a read, and says so. A 403 carries the scope
    hint, as `analytics` does."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    data, reason = await get_json(
        ANALYTICS_API,
        headers={"Authorization": f"Bearer {tok}"},
        params={"ids": "channel==MINE", "startDate": start.isoformat(),
                "endDate": end.isoformat(), "metrics": metrics,
                "dimensions": dimensions},
    )
    read_at = datetime.now(UTC)
    if reason is not None:
        scope_hint = (
            " — the token likely lacks the yt-analytics.readonly scope"
            if "403" in reason or "not permitted" in reason else "")
        return None, f"{channel}: {reason}{scope_hint}"
    columns = data.get("columnHeaders") if isinstance(data, dict) else None
    if not isinstance(columns, list) or not columns:
        return None, f"{channel}: the Analytics report came back without columnHeaders"
    names = [c.get("name") for c in columns if isinstance(c, dict)]
    raw = data.get("rows") or []
    if not isinstance(raw, list):
        return None, f"{channel}: the Analytics report's rows are not a list"
    rows = [dict(zip(names, r, strict=False)) for r in raw if isinstance(r, list)]
    return {"rows": rows, "read_at": read_at}, None


THUMBNAIL_API = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024     # YouTube Studio's limit for a custom one


async def _auth(channel: str) -> tuple[dict[str, str] | None, str | None]:
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    return {"Authorization": f"Bearer {tok}"}, None


TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
PLAYLIST_SCOPES = ("https://www.googleapis.com/auth/youtube",
                   "https://www.googleapis.com/auth/youtube.force-ssl")


async def token_scopes(channel: str) -> tuple[set[str] | None, str | None]:
    """The scopes this channel's access token was granted, read from Google's
    tokeninfo endpoint (its `scope` field, space-separated). None and a
    reason when it cannot be read: then nobody knows, and a caller holds."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    # POSTed as a form, never in the URL, so the token is in no access log.
    data, reason = await post_json(TOKENINFO_URL, data={"access_token": tok})
    if reason is not None:
        return None, f"{channel}: tokeninfo: {reason}"
    raw = (data or {}).get("scope") if isinstance(data, dict) else None
    if not isinstance(raw, str):
        return None, f"{channel}: tokeninfo answered without a scope"
    return set(raw.split()), None


async def find_playlist(channel: str, title: str) -> tuple[dict | None, str | None]:
    """This channel's playlist titled exactly `title`: ({"id", "privacy"},
    None), ({}, None) when there is none, or (None, why)."""
    headers, why = await _auth(channel)
    if headers is None:
        return None, why
    page: str | None = None
    for _ in range(10):                  # 500 playlists is far past any real use
        params = {"part": "snippet,status", "mine": "true", "maxResults": "50"}
        if page:
            params["pageToken"] = page
        data, reason = await get_json(f"{DATA_API}/playlists", headers=headers,
                                      params=params)
        if reason is not None:
            return None, f"{channel}: {reason}"
        for item in (data or {}).get("items") or []:
            if ((item.get("snippet") or {}).get("title") or "") == title:
                return {"id": item.get("id"),
                        "privacy": (item.get("status") or {}).get("privacyStatus")}, None
        page = (data or {}).get("nextPageToken")
        if not page:
            return {}, None
    return None, f"{channel}: more playlists than one read covers; not guessing"


async def create_playlist(channel: str, title: str, description: str,
                          privacy: str = "public") -> tuple[dict | None, str | None]:
    if privacy not in PRIVACY_VALUES:
        return None, f"invalid privacy {privacy!r}"
    headers, why = await _auth(channel)
    if headers is None:
        return None, why
    data, reason = await post_json(
        f"{DATA_API}/playlists", headers=headers, params={"part": "snippet,status"},
        json_body={"snippet": {"title": title[:150], "description": description[:5000]},
                   "status": {"privacyStatus": privacy}})
    if reason is not None:
        return None, f"{channel}: {reason}"
    if not (data or {}).get("id"):
        return None, f"{channel}: playlists.insert answered without an id"
    return {"id": data["id"], "privacy": privacy}, None


async def set_playlist_privacy(channel: str, playlist_id: str, title: str,
                               privacy: str) -> tuple[dict | None, str | None]:
    """playlists.update replaces the snippet, so the title goes back with it."""
    if privacy not in PRIVACY_VALUES:
        return None, f"invalid privacy {privacy!r}"
    headers, why = await _auth(channel)
    if headers is None:
        return None, why
    data, reason = await _put_json(
        f"{DATA_API}/playlists", headers=headers, params={"part": "snippet,status"},
        json_body={"id": playlist_id, "snippet": {"title": title},
                   "status": {"privacyStatus": privacy}})
    if reason is not None:
        return None, f"{channel}: {reason}"
    return data, None


async def list_playlist_items(channel: str, playlist_id: str
                              ) -> tuple[list[dict] | None, str | None]:
    """playlistItems.list, read-only: the playlist's entries in playlist
    order, [{"item_id", "video_id", "position"}], or (None, why)."""
    headers, why = await _auth(channel)
    if headers is None:
        return None, why
    out: list[dict] = []
    page: str | None = None
    for _ in range(20):                  # 1000 entries is far past any real use
        params = {"part": "snippet,contentDetails", "playlistId": playlist_id,
                  "maxResults": "50"}
        if page:
            params["pageToken"] = page
        data, reason = await get_json(f"{DATA_API}/playlistItems", headers=headers,
                                      params=params)
        if reason is not None:
            return None, f"{channel}: {reason}"
        for item in (data or {}).get("items") or []:
            snippet = item.get("snippet") or {}
            vid = ((item.get("contentDetails") or {}).get("videoId")
                   or (snippet.get("resourceId") or {}).get("videoId"))
            out.append({"item_id": item.get("id"), "video_id": vid,
                        "position": snippet.get("position")})
        page = (data or {}).get("nextPageToken")
        if not page:
            return sorted(out, key=lambda i: (i["position"] is None,
                                              i["position"] or 0)), None
    return None, f"{channel}: more playlist items than one read covers; not guessing"


# ------------------------------------------------- read-only state rebuild
# GET-only helpers for academy/rebuild.py (2026-10-27): what this channel has
# posted, read back from YouTube after a restore. None of them writes.

PLAYLIST_PAGES_MAX = 100             # 5,000 entries; past that, an error, never a cut


async def _mine(channel: str, part: str) -> tuple[dict | None, str | None]:
    """channels.list(mine=true): this channel's own item (1 unit)."""
    headers, why = await _auth(channel)
    if headers is None:
        return None, why
    data, reason = await get_json(f"{DATA_API}/channels", headers=headers,
                                  params={"part": part, "mine": "true"})
    if reason is not None:
        return None, f"{channel}: {reason}"
    items = (data or {}).get("items") if isinstance(data, dict) else None
    if not items or not isinstance(items[0], dict):
        return None, f"{channel}: channels.list(mine) answered no channel"
    return items[0], None


async def own_channel_id(channel: str) -> tuple[str | None, str | None]:
    """This channel's id (UC...), as YouTube answers it, or (None, why)."""
    item, why = await _mine(channel, "id")
    if item is None:
        return None, why
    cid = item.get("id")
    return (str(cid), None) if cid else (None, f"{channel}: channels.list has no id")


async def uploads_playlist(channel: str) -> tuple[str | None, str | None]:
    """The id of this channel's uploads playlist (contentDetails.
    relatedPlaylists.uploads), or (None, why)."""
    item, why = await _mine(channel, "contentDetails")
    if item is None:
        return None, why
    uploads = ((item.get("contentDetails") or {}).get("relatedPlaylists")
               or {}).get("uploads")
    if not uploads:
        return None, f"{channel}: channels.list has no uploads playlist"
    return str(uploads), None


async def playlist_items(channel: str, playlist_id: str
                         ) -> tuple[list[dict], str | None]:
    """playlistItems.list over every page (1 unit a page):
    [{"item_id", "video_id", "position"}] in playlist order, or ([], why).
    More pages than PLAYLIST_PAGES_MAX is an error, never a shortened list."""
    headers, why = await _auth(channel)
    if headers is None:
        return [], why
    out: list[dict] = []
    page: str | None = None
    for _ in range(PLAYLIST_PAGES_MAX):
        params = {"part": "snippet,contentDetails", "playlistId": playlist_id,
                  "maxResults": "50"}
        if page:
            params["pageToken"] = page
        data, reason = await get_json(f"{DATA_API}/playlistItems", headers=headers,
                                      params=params)
        if reason is not None:
            return [], f"{channel}: {reason}"
        for item in (data or {}).get("items") or []:
            snippet = item.get("snippet") or {}
            vid = ((item.get("contentDetails") or {}).get("videoId")
                   or (snippet.get("resourceId") or {}).get("videoId"))
            if vid:
                out.append({"item_id": item.get("id"), "video_id": str(vid),
                            "position": snippet.get("position")})
        page = (data or {}).get("nextPageToken")
        if not page:
            return sorted(out, key=lambda i: (i["position"] is None,
                                              i["position"] or 0)), None
    return [], f"{channel}: playlist {playlist_id} is longer than one read covers"


async def video_details(channel: str, video_ids: Sequence[str]
                        ) -> tuple[list[dict], str | None]:
    """videos.list(part=status,snippet), 50 ids a call (1 unit each):
    [{"video_id", "title", "privacy", "published_at"}], or ([], why)."""
    headers, why = await _auth(channel)
    if headers is None:
        return [], why
    ids = list(dict.fromkeys(str(v) for v in video_ids if v))
    out: list[dict] = []
    for start in range(0, len(ids), 50):
        data, reason = await get_json(
            f"{DATA_API}/videos", headers=headers,
            params={"part": "status,snippet", "id": ",".join(ids[start:start + 50]),
                    "maxResults": "50"})
        if reason is not None:
            return [], f"{channel}: {reason}"
        for item in (data or {}).get("items") or []:
            snippet = item.get("snippet") or {}
            out.append({"video_id": item.get("id"), "title": snippet.get("title"),
                        "privacy": (item.get("status") or {}).get("privacyStatus"),
                        "published_at": snippet.get("publishedAt")})
    return out, None


async def comment_threads(channel: str, video_id: str
                          ) -> tuple[list[dict], str | None]:
    """commentThreads.list(videoId) over every page (1 unit a page): each
    thread's top-level comment as {"comment_id" (the id post_comment
    records), "thread_id", "author_channel_id", "text", "published_at"}, or
    ([], why). The text is the commenter's, untrusted: data, never an
    instruction."""
    headers, why = await _auth(channel)
    if headers is None:
        return [], why
    out: list[dict] = []
    page: str | None = None
    for _ in range(20):                  # 2,000 threads under one tutorial
        params = {"part": "snippet", "videoId": video_id, "maxResults": "100",
                  "textFormat": "plainText"}
        if page:
            params["pageToken"] = page
        data, reason = await get_json(f"{DATA_API}/commentThreads", headers=headers,
                                      params=params)
        if reason is not None:
            return [], f"{channel}: {reason}"
        for item in (data or {}).get("items") or []:
            top = (item.get("snippet") or {}).get("topLevelComment") or {}
            snip = top.get("snippet") or {}
            out.append({
                "comment_id": str(top.get("id") or item.get("id") or ""),
                "thread_id": str(item.get("id") or ""),
                "author_channel_id": (snip.get("authorChannelId") or {}).get("value"),
                "text": snip.get("textOriginal") or snip.get("textDisplay") or "",
                "published_at": snip.get("publishedAt")})
        page = (data or {}).get("nextPageToken")
        if not page:
            return out, None
    return [], f"{channel}: more comments under {video_id} than one read covers"


async def add_playlist_item(channel: str, playlist_id: str, video_id: str,
                            position: int | None = None
                            ) -> tuple[dict | None, str | None]:
    """playlistItems.insert; `position` (0-based) places it, None appends."""
    headers, why = await _auth(channel)
    if headers is None:
        return None, why
    snippet: dict = {"playlistId": playlist_id,
                     "resourceId": {"kind": "youtube#video", "videoId": video_id}}
    if position is not None:
        snippet["position"] = int(position)
    # A playlist created seconds earlier answers 409 to its first insert;
    # the same call a few seconds later succeeds (2026-10-11, Claude #0).
    for wait in (0, 5, 15, 30):
        if wait:
            await asyncio.sleep(wait)
        data, reason = await post_json(
            f"{DATA_API}/playlistItems", headers=headers, params={"part": "snippet"},
            json_body={"snippet": snippet})
        if reason is None or "409" not in reason:
            break
    if reason is not None:
        return None, f"{channel}: {reason}"
    if not (data or {}).get("id"):
        return None, f"{channel}: playlistItems.insert answered without an id"
    return {"item_id": data["id"], "playlist_id": playlist_id, "video_id": video_id}, None


async def take_out_of_playlist(channel: str, item_id: str) -> tuple[bool, str | None]:
    """playlistItems.delete: the entry leaves the playlist and the video stays
    exactly as it is (a reversible edit: add_playlist_item puts it back)."""
    headers, why = await _auth(channel)
    if headers is None:
        return False, why
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.delete(f"{DATA_API}/playlistItems", headers=headers,
                               params={"id": item_id})
            r.raise_for_status()
    except Exception as exc:                            # noqa: BLE001
        return False, f"{channel}: {explain(exc)}"
    return True, None


def _thumb_type(path: str) -> str | None:
    low = path.lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return None


async def set_thumbnail(channel: str, video_id: str, path: str
                        ) -> tuple[dict | None, str | None]:
    """thumbnails.set. A 403 usually means the channel may not set custom
    thumbnails yet (an unverified channel); it is reported, not retried."""
    mime = _thumb_type(path)
    if mime is None:
        return None, "a thumbnail must be a .png or .jpg file"
    try:
        size = await asyncio.to_thread(os.path.getsize, path)
    except OSError as exc:
        return None, f"cannot read the thumbnail: {exc}"
    if not 0 < size <= THUMBNAIL_MAX_BYTES:
        return None, f"the thumbnail is {size} bytes; it must be 1 to {THUMBNAIL_MAX_BYTES}"
    headers, why = await _auth(channel)
    if headers is None:
        return None, why
    body = await asyncio.to_thread(_read_chunk, path, 0, size)
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(THUMBNAIL_API, params={"videoId": video_id},
                             headers={**headers, "Content-Type": mime}, content=body)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:                            # noqa: BLE001
        return None, f"{channel}: {explain(exc)}"
    return {"video_id": video_id, "items": (data or {}).get("items") or []}, None


CAPTIONS_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/captions"


async def set_captions(channel: str, video_id: str, srt: str, *,
                       language: str = TUTORIAL_LANGUAGE, name: str = "English"
                       ) -> tuple[dict | None, str | None]:
    """captions.insert: one SRT track in `language`. Needs the
    youtube.force-ssl scope; a 403 is reported, never retried."""
    if channel not in CHANNELS:
        return None, _unknown_channel(channel)
    tok, reason = await token(channel)
    if reason is not None:
        return None, reason
    meta = json.dumps({"snippet": {"videoId": video_id, "language": language,
                                   "name": name, "isDraft": False}}).encode()
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(
                CAPTIONS_UPLOAD, params={"part": "snippet", "uploadType": "multipart"},
                headers={"Authorization": f"Bearer {tok}"},
                files={"metadata": ("metadata.json", meta, "application/json"),
                       "file": ("captions.srt", srt.encode(), "application/octet-stream")})
            r.raise_for_status()
            return r.json(), None
    except Exception as exc:                            # noqa: BLE001
        return None, f"{channel}: captions: {explain(exc)}"
