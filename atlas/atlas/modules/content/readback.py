"""Read-back for a reel's publish, per platform (RA-D1, RA-D2).

`content.publish_reel` and `content.autopublish_reel` succeed when ANY leg
posted, so ok alone hides a missing leg. This reads each destination back by
the id the run recorded and names the leg that is missing:

  * Instagram (stories): the media by id (`instagram.readback.read_media`);
  * Facebook Page: the video by id, with the Page token;
  * TikTok via Postiz: the Postiz post's state (read only -- never
    `post_tiktok` or `_release`, which writes).

A leg that was not configured at run time is skipped as intended (class
`disabled`, never an outage). A leg that was refused, or whose id does not
read back, is contradicted and named. A queued or id-pending leg is unknown.

Reads only. See docs/verification.md.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    combine,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import crosspost

LEGS = ("instagram", "facebook", "tiktok")


async def _instagram(ref: str, caption: str) -> Verification:
    from ..instagram.readback import read_media
    account, _, media_id = ref.partition(":")
    if not media_id:
        account, media_id = "stories", ref
    return await read_media(account, media_id, caption=caption, kind="reel")


async def _facebook(video_id: str) -> Verification:
    cfg = crosspost._fb_cfg()
    if cfg is None:
        return unknown("the Facebook Page token is not configured, so the video "
                       "cannot be read back", FailureClass.DISABLED)
    _, token = cfg
    from ..instagram import GRAPH, GraphError, _raise_for, _unreachable
    from ..instagram.readback import graph_failure, missing
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{GRAPH}/{video_id}",
                            params={"fields": "id,permalink_url,created_time,status"},
                            headers={"Authorization": f"Bearer {token}"})
        _raise_for(r, token)
        body = r.json()
    except GraphError as exc:
        if missing(exc):
            return contradicted(f"video {video_id} is not on the Page",
                                video_id=video_id)
        return graph_failure(exc)
    except httpx.HTTPError as exc:
        return graph_failure(_unreachable(exc, token))
    status = body.get("status") if isinstance(body, dict) else None
    video_status = str((status or {}).get("video_status") or "").lower() \
        if isinstance(status, dict) else ""
    evidence = {"video_id": body.get("id"), "permalink": body.get("permalink_url"),
                "created": body.get("created_time"), "video_status": video_status or None}
    if str(body.get("id") or "") != str(video_id):
        return contradicted("Graph answered for a different video id", **evidence)
    if video_status in ("error", "expired"):
        return contradicted(f"video {video_id} is {video_status} on the Page",
                            **evidence)
    if video_status and video_status != "ready":
        return unknown(f"video {video_id} is still {video_status}",
                       FailureClass.STALE, **evidence)
    return verified(**evidence)


async def _tiktok(post_id: str) -> Verification:
    from . import postiz
    if not postiz.configured():
        return unknown(f"{postiz.KEY_ENV} unset, so the post cannot be read back",
                       FailureClass.DISABLED)
    async with httpx.AsyncClient(timeout=30) as c:
        state = await postiz._state(c, post_id)     # GET /posts, read only
    evidence = {"postiz_post": post_id, "state": state}
    if state is None:
        return unknown(f"Postiz post {post_id} could not be confirmed",
                       FailureClass.TRANSIENT, **evidence)
    if state in ("ERROR", "FAILED", "DELETED"):
        return contradicted(f"Postiz post {post_id} is {state}", **evidence)
    if state in ("QUEUE", "QUEUED", "DRAFT"):
        return unknown(f"Postiz post {post_id} is still {state}",
                       FailureClass.STALE, **evidence)
    return verified(**evidence)


async def read_leg(leg: str, ref: str, caption: str) -> Verification:
    if leg == "instagram":
        return await _instagram(ref, caption)
    if leg == "facebook":
        return await _facebook(ref)
    return await _tiktok(ref)


async def _legs(caption: str, statuses: dict[str, Any], posted: dict[str, str]
                ) -> dict[str, Verification]:
    out: dict[str, Verification] = {}
    for leg in LEGS:
        info = statuses.get(leg) if isinstance(statuses.get(leg), dict) else {}
        status, said = str(info.get("status") or ""), str(info.get("detail") or "")
        ref = posted.get(leg)
        if status == crosspost.NOT_CONFIGURED:
            continue                    # skipped as intended, not an outage
        if ref:
            try:
                out[leg] = await read_leg(leg, str(ref), caption)
            except Exception as exc:  # noqa: BLE001
                from .._http import explain
                out[leg] = unknown(f"read raised: {explain(exc)}")
        elif status == crosspost.REFUSED:
            out[leg] = contradicted(f"did not post: {said}"[:200] or "refused")
        else:
            out[leg] = contradicted("no id was recorded for this leg")
    return out


async def _verify_reel(params: dict[str, Any], result: ActionResult) -> Verification:
    try:
        posted = json.loads(result.rollback_token or "")
    except ValueError:
        posted = None
    if not isinstance(posted, dict):
        return unknown("the run recorded no per-leg ids")
    legs = await _legs(str(params.get("caption") or ""),
                       dict(result.evidence or {}), posted)
    if not legs:
        return unknown("no leg was configured to post", FailureClass.DISABLED)
    return combine(legs)


verifier("content.publish_reel")(_verify_reel)
verifier("content.autopublish_reel")(_verify_reel)


# ------------------------------------------------------------------ reconcile

async def _recorded(media_id: int) -> dict[str, str]:
    """`reel_posts` rows for this media. Only instagram and tiktok are ever
    written there (`crosspost._record_posts`): a missing facebook row proves
    nothing about the Facebook leg."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        "SELECT platform, post_ref FROM reel_posts WHERE media_id = $1", media_id)
    return {str(r["platform"]): str(r["post_ref"]) for r in rows or []}


async def _facebook_since(caption: str, started: datetime) -> Verification:
    """The Page's recent videos, matched by description, at or after the run
    started. Reads only."""
    cfg = crosspost._fb_cfg()
    if cfg is None:
        return unknown("the Facebook Page is not configured", FailureClass.DISABLED)
    page, token = cfg
    from ..instagram import GRAPH, GraphError, _raise_for, _unreachable
    from ..instagram.readback import graph_failure
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{GRAPH}/{page}/videos",
                            params={"fields": "id,description,created_time,permalink_url",
                                    "limit": 25},
                            headers={"Authorization": f"Bearer {token}"})
        _raise_for(r, token)
        listed = (r.json() or {}).get("data") or []
    except GraphError as exc:
        return graph_failure(exc)
    except httpx.HTTPError as exc:
        return graph_failure(_unreachable(exc, token))
    want = " ".join(caption.split()).lower()[:40]
    for item in listed:
        try:
            when = datetime.fromisoformat(
                str(item.get("created_time") or "").replace("+0000", "+00:00"))
        except ValueError:
            continue
        said = " ".join(str(item.get("description") or "").split()).lower()[:40]
        if when >= started and want and said == want:
            return await _facebook(str(item.get("id")))
    return contradicted("no video with this caption reached the Page after the "
                        "run started")


@reconciler("content.publish_reel")
@reconciler("content.autopublish_reel")
async def reconcile_reel(params: dict[str, Any], started: datetime) -> Verification:
    try:
        media_id = int(params.get("media_id"))
    except (TypeError, ValueError):
        return unknown("the start row names no media id", FailureClass.VALIDATION)
    caption = str(params.get("caption") or "")
    try:
        recorded = await _recorded(media_id)
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return unknown(f"reel_posts could not be read: {explain(exc)}",
                       FailureClass.TRANSIENT)
    legs: dict[str, Verification] = {}
    ig = recorded.get("instagram")
    if ig:
        legs["instagram"] = await _instagram(f"stories:{ig}", caption)
    else:
        from ..instagram.readback import find_recent
        legs["instagram"] = await find_recent("stories", caption, started)
    legs["facebook"] = await _facebook_since(caption, started)
    tt = recorded.get("tiktok")
    legs["tiktok"] = await _tiktok(tt) if tt else unknown(
        "no Postiz post id was recorded for this media")
    # A leg whose platform is not configured is skipped as intended.
    legs = {k: v for k, v in legs.items() if v.failure_class is not FailureClass.DISABLED}
    return combine(legs) if legs else unknown("no leg is configured",
                                              FailureClass.DISABLED)


# ================================================================== drafts, plans,
# research and renders (RA-D1, third batch)
#
# Each reads the row the run wrote, by the id it recorded, with one SELECT
# (never a module helper: some UPDATE, e.g. `series.current`). A render also
# reads its file: it exists, is not empty, and ffprobe finds a video in it
# with a duration. No ffprobe on this box -> unknown, never verified.

def _sha12(text: Any) -> str:
    import hashlib
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:12]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _recorded_id(result: ActionResult, key: str = "id") -> int | None:
    got = _int((result.evidence or {}).get(key))
    return got if got is not None else _int(result.rollback_token)


async def _one(sql: str, *args: Any) -> Any:
    from ...kernel import state
    return await state.fetch_one(sql, *args)


async def _draft_row(draft_id: int, result: ActionResult, *, kind: str | None,
                     source: str | None) -> Verification:
    """The `content_drafts` row by id: there, not dropped, of the approved
    kind and source, and holding the body the run wrote (by hash)."""
    row = await _one("SELECT id, kind, source, body, status FROM content_drafts "
                     "WHERE id = $1", draft_id)
    if row is None:
        return contradicted(f"draft {draft_id} is not in content_drafts",
                            draft_id=draft_id)
    status = str(row["status"] or "")
    body_hash = _sha12(row["body"])
    evidence = {"draft_id": draft_id, "kind": row["kind"], "status": status,
                "body_sha256": body_hash}
    if status == "dropped":
        return contradicted(f"draft {draft_id} was dropped", **evidence)
    if kind is not None and row["kind"] != kind:
        return contradicted(f"draft {draft_id} is a {row['kind']!r}, not a "
                            f"{kind!r}", FailureClass.VALIDATION, **evidence)
    if source is not None and str(row["source"] or "") != source:
        return contradicted(f"draft {draft_id} was drafted from another source",
                            FailureClass.VALIDATION, **evidence)
    want = (result.evidence or {}).get("body_sha256")
    if want and want != body_hash:
        return contradicted(f"draft {draft_id}'s body is not the one drafted",
                            **evidence)
    return verified(**evidence)


@verifier("content.draft")
async def verify_draft(params: dict[str, Any], result: ActionResult) -> Verification:
    draft_id = _recorded_id(result)
    if draft_id is None:
        return unknown("the run recorded no draft id")
    return await _draft_row(draft_id, result, kind=str(params.get("kind") or "")
                            or None, source=str(params.get("source") or "") or None)


@verifier("content.draft_reel")
async def verify_draft_reel(params: dict[str, Any], result: ActionResult
                            ) -> Verification:
    draft_id = _recorded_id(result)
    if draft_id is None:
        return unknown("the run recorded no draft id")
    format_id = _int(params.get("format_id"))
    return await _draft_row(
        draft_id, result, kind="story_script",
        source=f"reel_format:{format_id}" if format_id is not None else None)


@verifier("content.draft_series_part")
async def verify_draft_series_part(params: dict[str, Any], result: ActionResult
                                   ) -> Verification:
    draft_id = _recorded_id(result)
    series_id = _int(params.get("series_id"))
    part = _int((result.evidence or {}).get("part"))
    if draft_id is None or series_id is None or part is None:
        return unknown("the run recorded no draft id, series id or part number")
    got = await _draft_row(draft_id, result, kind="story_script",
                           source=f"series:{series_id}:{part}")
    if not got.ok:
        return got
    row = await _one("SELECT series_id, part_no, draft_id, status FROM "
                     "reel_series_parts WHERE series_id = $1 AND part_no = $2",
                     series_id, part)
    evidence = {**got.evidence, "series_id": series_id, "part": part}
    if row is None:
        return contradicted(f"series {series_id} has no part {part} row",
                            **evidence)
    evidence["part_status"] = row["status"]
    if _int(row["draft_id"]) != draft_id:
        return contradicted(f"series {series_id} part {part} points at another "
                            f"draft ({row['draft_id']})", **evidence)
    return verified(**evidence)


@verifier("content.plan_series")
async def verify_plan_series(params: dict[str, Any], result: ActionResult
                             ) -> Verification:
    series_id = _recorded_id(result)
    if series_id is None:
        return unknown("the run recorded no series id")
    row = await _one("SELECT id, title, format, outline, total_parts, status "
                     "FROM reel_series WHERE id = $1", series_id)
    if row is None:
        return contradicted(f"series {series_id} is not in reel_series",
                            series_id=series_id)
    outline = row["outline"]
    if isinstance(outline, str):
        try:
            outline = json.loads(outline)
        except ValueError:
            outline = None
    evidence = {"series_id": series_id, "title": row["title"],
                "status": row["status"], "total_parts": row["total_parts"],
                "outline_parts": len(outline) if isinstance(outline, list) else None}
    if row["status"] == "dropped":
        return contradicted(f"series {series_id} was dropped", **evidence)
    want_format = str(params.get("format_name") or "")
    if want_format and row["format"] != want_format:
        return contradicted(f"series {series_id} is in another format",
                            FailureClass.VALIDATION, **evidence)
    want_title = (result.evidence or {}).get("title")
    if want_title and row["title"] != want_title:
        return contradicted(f"series {series_id}'s title is not the one planned",
                            **evidence)
    if not isinstance(outline, list) or len(outline) != _int(row["total_parts"]):
        return contradicted(f"series {series_id}'s outline does not have its "
                            f"{row['total_parts']} parts", **evidence)
    return verified(**evidence)


@verifier("content.reel_research")
async def verify_reel_research(params: dict[str, Any], result: ActionResult
                               ) -> Verification:
    filed: Any = (result.evidence or {}).get("filed")
    if not filed and result.rollback_token:
        try:
            filed = json.loads(result.rollback_token)
        except ValueError:
            filed = None
    ids = [i for i in (_int(x) for x in filed or []) if i is not None]
    if not ids:
        return unknown("the run filed no format rows, so there is nothing to "
                       "read back", insights=(result.evidence or {}).get("insights"))
    from ...kernel import state
    rows = await state.fetch_all(
        "SELECT id, source, retired_at FROM reel_formats WHERE id = ANY($1)", ids)
    seen = {_int(r["id"]): r for r in rows or []}
    absent = [i for i in ids if i not in seen]
    retired = [i for i in ids if i in seen and seen[i]["retired_at"] is not None]
    evidence = {"filed": ids, "found": sorted(i for i in seen if i is not None)}
    if absent:
        return contradicted(f"format row(s) {absent} are not in reel_formats",
                            **evidence)
    if retired:
        return contradicted(f"format row(s) {retired} were retired", **evidence)
    return verified(**evidence)


# ------------------------------------------------------------------ renders

async def read_media(media_id: int, draft_id: int | None = None) -> Verification:
    """A finished render: its `content_media` row is done (for this draft),
    and its file exists, is not empty, and ffprobe reads a video with a
    duration from it. Never reads `media_token` or builds a link."""
    import os

    from . import ffmpeg
    row = await _one("SELECT id, draft_id, status, path, provider FROM content_media "
                     "WHERE id = $1", media_id)
    if row is None:
        return contradicted(f"media {media_id} is not in content_media",
                            media_id=media_id)
    evidence: dict[str, Any] = {"media_id": media_id, "status": row["status"],
                                "provider": row["provider"],
                                "draft_id": _int(row["draft_id"])}
    if row["status"] != "done":
        return contradicted(f"media {media_id} is {row['status']}, not done",
                            **evidence)
    if draft_id is not None and _int(row["draft_id"]) != draft_id:
        return contradicted(f"media {media_id} belongs to another draft",
                            **evidence)
    path = row["path"]
    if not path:
        return unknown(f"media {media_id} is done but has no file (the provider "
                       f"gave nothing to download)", **evidence)
    evidence["file"] = os.path.basename(str(path))
    try:
        size = (await asyncio.to_thread(os.stat, str(path))).st_size
    except FileNotFoundError:
        return contradicted(f"media {media_id}'s file is not on disk", **evidence)
    evidence["bytes"] = size
    if size <= 0:
        return contradicted(f"media {media_id}'s file is empty",
                            FailureClass.QUALITY, **evidence)
    if ffmpeg.binary("ffprobe") is None:
        return unknown("ffprobe is not installed, so the file's duration cannot "
                       "be read", **evidence)
    try:
        probed = await ffmpeg.probe(str(path))
    except ffmpeg.FfmpegError as exc:
        return unknown(f"ffprobe could not read the file: {exc}", **evidence)
    seconds = float(probed.get("seconds") or 0.0)
    evidence.update(seconds=round(seconds, 2), width=probed.get("width"),
                    height=probed.get("height"), audio=probed.get("audio"))
    if not probed.get("video") or seconds <= 0:
        return contradicted(f"media {media_id}'s file holds no playable video",
                            FailureClass.QUALITY, **evidence)
    return verified(**evidence)


@verifier("content.render_video")
async def verify_render_video(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    media_id = _recorded_id(result, "media_id")
    if media_id is None:
        return unknown("the run recorded no media id")
    return await read_media(media_id, _int(params.get("draft_id")))


_RENDERED_ON = ("rendered", "carded", "published", "declined")


@verifier("content.render_calendar_job")
async def verify_render_calendar_job(params: dict[str, Any], result: ActionResult
                                     ) -> Verification:
    """A finished day: its calendar row is rendered and points at a media row
    that reads back as a playable file. An unfinished tick: its row is
    rendering (or blocked, when the run said so) and no more clips are
    missing on disk than the run said were left."""
    from . import calendar
    job_id = str(params.get("job_id") or "")
    said = dict(result.evidence or {})
    j = calendar.job(job_id)
    if j is None:
        return unknown(f"no calendar job {job_id!r} to read back",
                       FailureClass.VALIDATION)
    row = await _one("SELECT job_id, status, draft_id, media_id FROM reel_calendar "
                     "WHERE job_id = $1", job_id)
    if row is None:
        return contradicted(f"calendar job {job_id} has no reel_calendar row",
                            job_id=job_id)
    evidence: dict[str, Any] = {"job_id": job_id, "status": row["status"]}
    if said.get("complete"):
        media_id = _int(result.rollback_token) or _int(said.get("media_id"))
        if media_id is None:
            return unknown("the run recorded no media id", **evidence)
        # The card flow (kernel/reels.py) moves a rendered day on to carded,
        # published or declined, keeping its media: all still this render.
        if row["status"] not in _RENDERED_ON or _int(row["media_id"]) != media_id:
            return contradicted(f"calendar job {job_id} is {row['status']} with "
                                f"media {row['media_id']}, not rendered with "
                                f"media {media_id}", **evidence)
        media = await read_media(media_id, _int(row["draft_id"]))
        return Verification(media.state, {**evidence, **media.evidence},
                            failure_class=media.failure_class, reason=media.reason)
    left = _int(said.get("left"))
    if left is None:
        return unknown("the run recorded no count of clips left", **evidence)
    missing = await asyncio.to_thread(calendar.missing, j)
    total = len(calendar.plan(j))
    evidence.update(clips=total, banked=total - len(missing), left=len(missing))
    if len(missing) > left:
        return contradicted(f"{len(missing)} clip(s) are missing on disk, the run "
                            f"said {left} were left", **evidence)
    if said.get("blocked") and row["status"] != "blocked":
        return contradicted(f"calendar job {job_id} was reported blocked but is "
                            f"{row['status']}", **evidence)
    if row["status"] not in ("rendering", "blocked", "rendered"):
        return contradicted(f"calendar job {job_id} is {row['status']}, not "
                            f"rendering", **evidence)
    return verified(**evidence)
