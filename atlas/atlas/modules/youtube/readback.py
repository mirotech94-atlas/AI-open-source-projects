"""Read-back for the YouTube writes (RA-D1).

  * `youtube.upload`: the video by the id the run recorded, from
    `videos.list(part=snippet,status)`: it exists, carries the approved
    title (as `api.upload` sent it, cut to 100 characters), is `private`
    (nothing is public on upload), and its upload was not rejected.
  * `youtube.set_visibility`: the same read; `privacyStatus` equals the
    approved value. A reconciler reads it the same way from the params.
  * `youtube.draft_metadata`: nothing reaches YouTube. The authority is the
    `content_drafts` row the run inserted, read by id with a SELECT: kind,
    source, title, not dropped, and the body hash the run recorded.

The module is off in prod (`ATLAS_DISABLED_MODULES`, the owner 2026-09-27), and a
channel with no credentials is off for that channel. Both give unknown,
class disabled, before any network call. An access token is only taken from
`api`'s own cache: a refresh is a POST, so a cold cache is unknown rather
than a write. Reads only. See docs/verification.md.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    classify,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import api

BAD_UPLOAD = frozenset({"failed", "rejected", "deleted"})


def _module_off() -> str | None:
    """The registry's rule: named in ATLAS_DISABLED_MODULES."""
    off = {m.strip().lower() for m in
           os.getenv("ATLAS_DISABLED_MODULES", "").split(",") if m.strip()}
    if "youtube" in off:
        return "the youtube module is switched off (ATLAS_DISABLED_MODULES)"
    return None


def _channel_off(channel: str) -> str | None:
    try:
        api._require_credentials(channel)       # env read only
    except api.NoCredentials:
        return f"channel {channel!r} has no credentials configured"
    return None


def _cached_token(channel: str) -> str | None:
    """A still-good access token from `api`'s cache, or None. Never
    refreshes: the refresh grant is a POST."""
    cached = api._token_cache.get(channel)
    if cached and cached[1] > time.time():
        return cached[0]
    return None


async def _read_video(channel: str, video_id: str
                      ) -> tuple[dict[str, Any] | None, Verification | None]:
    if (why := _module_off()) is not None:
        return None, unknown(why, FailureClass.DISABLED, video_id=video_id)
    if channel not in api.CHANNELS:
        return None, unknown(f"unknown channel {channel!r}", FailureClass.VALIDATION,
                             video_id=video_id)
    if (why := _channel_off(channel)) is not None:
        return None, unknown(why, FailureClass.DISABLED, channel=channel,
                             video_id=video_id)
    tok = _cached_token(channel)
    if tok is None:
        return None, unknown("no cached access token for this channel; a token "
                             "refresh is a POST, so the video was not read",
                             FailureClass.STALE, channel=channel, video_id=video_id)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{api.DATA_API}/videos",
                            headers={"Authorization": f"Bearer {tok}"},
                            params={"part": "snippet,status", "id": video_id})
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError as exc:
        return None, unknown(f"could not read video {video_id}", classify(exc),
                             channel=channel, video_id=video_id)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None, unknown("videos.list answered with no items list",
                             channel=channel, video_id=video_id)
    if not items:
        # videos.list answers 200 with no items for an id that is not there.
        return None, contradicted(f"video {video_id} is not on the {channel} channel",
                                  channel=channel, video_id=video_id)
    return items[0], None


def _evidence(channel: str, video_id: str, item: dict[str, Any]) -> dict[str, Any]:
    snippet, status = item.get("snippet") or {}, item.get("status") or {}
    return {"channel": channel, "video_id": item.get("id") or video_id,
            "title": str(snippet.get("title") or "")[:100],
            "privacy": status.get("privacyStatus"),
            "upload_status": status.get("uploadStatus")}


def _ids(result: ActionResult) -> tuple[str, str]:
    """(channel, video_id) from the run's evidence, else its rollback token
    (`channel:video_id[:previous]`)."""
    ev = result.evidence or {}
    channel, video_id = str(ev.get("channel") or ""), str(ev.get("video_id") or "")
    if not (channel and video_id) and result.rollback_token:
        parts = str(result.rollback_token).split(":")
        if len(parts) >= 2:
            channel, video_id = channel or parts[0], video_id or parts[1]
    return channel, video_id


@verifier("youtube.upload")
async def verify_upload(params: dict[str, Any], result: ActionResult) -> Verification:
    channel, video_id = _ids(result)
    channel = channel or str(params.get("channel") or "")
    if not video_id:
        return unknown("the run recorded no video id")
    item, failed = await _read_video(channel, video_id)
    if failed is not None:
        return failed
    assert item is not None
    evidence = _evidence(channel, video_id, item)
    if str(evidence["upload_status"] or "") in BAD_UPLOAD:
        return contradicted(f"the upload is {evidence['upload_status']}",
                            FailureClass.QUALITY, **evidence)
    if evidence["privacy"] != "private":
        return contradicted(f"the video is {evidence['privacy']}, not private",
                            FailureClass.POLICY, **evidence)
    want = str(params.get("title") or "")[:100]
    if want and evidence["title"] != want:
        return contradicted("the video carries a different title", **evidence)
    return verified(**evidence)


async def _read_privacy(channel: str, video_id: str, privacy: str) -> Verification:
    item, failed = await _read_video(channel, video_id)
    if failed is not None:
        return failed
    assert item is not None
    evidence = _evidence(channel, video_id, item)
    evidence["approved"] = privacy
    if evidence["privacy"] != privacy:
        return contradicted(f"the video is {evidence['privacy']}, approved {privacy}",
                            **evidence)
    return verified(**evidence)


@verifier("youtube.set_visibility")
async def verify_set_visibility(params: dict[str, Any], result: ActionResult
                                ) -> Verification:
    channel, video_id = _ids(result)
    channel = channel or str(params.get("channel") or "")
    video_id = video_id or str(params.get("video_id") or "")
    privacy = str(params.get("privacy") or "")
    if not video_id:
        return unknown("the run recorded no video id")
    if privacy not in api.PRIVACY_VALUES:
        return unknown("no valid privacy was approved", FailureClass.VALIDATION)
    if params.get("video_id") and str(params["video_id"]) != video_id:
        return contradicted("the run changed a different video than approved",
                            video_id=video_id)
    return await _read_privacy(channel, video_id, privacy)


@reconciler("youtube.set_visibility")
async def reconcile_set_visibility(params: dict[str, Any], started: datetime
                                   ) -> Verification:
    channel = str(params.get("channel") or "")
    video_id = str(params.get("video_id") or "")
    privacy = str(params.get("privacy") or "")
    if not video_id or privacy not in api.PRIVACY_VALUES:
        return unknown("the start row names no video and privacy",
                       FailureClass.VALIDATION)
    return await _read_privacy(channel, video_id, privacy)


def _draft_id(result: ActionResult) -> int | None:
    raw = (result.evidence or {}).get("id") or result.rollback_token
    try:
        return int(raw) if raw is not None and str(raw).strip() else None
    except (TypeError, ValueError):
        return None


@verifier("youtube.draft_metadata")
async def verify_draft_metadata(params: dict[str, Any], result: ActionResult
                                ) -> Verification:
    if (why := _module_off()) is not None:
        return unknown(why, FailureClass.DISABLED)
    draft_id = _draft_id(result)
    if draft_id is None:
        return unknown("the run recorded no draft id")
    from ...kernel import state
    row = await state.fetch_one(
        """SELECT kind, source, title, body, status
             FROM content_drafts WHERE id = $1""", draft_id)
    if row is None:
        return contradicted(f"there is no content_drafts row #{draft_id}",
                            draft_id=draft_id)
    body = str(row["body"] or "")
    evidence = {"draft_id": draft_id, "kind": row["kind"], "status": row["status"],
                "title": str(row["title"] or "")[:100],
                "sha256": hashlib.sha256(body.encode()).hexdigest()[:12]}
    if row["kind"] != "youtube_video":
        return contradicted(f"draft #{draft_id} is a {row['kind']}, not youtube_video",
                            **evidence)
    if row["status"] == "dropped":
        return contradicted(f"draft #{draft_id} was dropped", **evidence)
    if params.get("source") and str(row["source"] or "") != str(params["source"]):
        return contradicted(f"draft #{draft_id} was drafted from a different source",
                            **evidence)
    ev = result.evidence or {}
    if ev.get("title") is not None and str(row["title"] or "") != str(ev["title"]):
        return contradicted(f"draft #{draft_id} carries a different title", **evidence)
    if ev.get("sha256") and ev["sha256"] != evidence["sha256"]:
        return contradicted(f"draft #{draft_id} body differs from what was drafted",
                            **evidence)
    return verified(**evidence)
