"""A public, unauthenticated route for one file: a rendered video Instagram's
own Graph API has to fetch when `instagram.publish` posts it.

No session sits in front of `GET /media/{token}` — there is nothing to sign
the request the way `/webhooks/*` requires (Instagram fetches media the same
way Twilio does, at `/webhooks/twilio/audio/{token}`, and that route is the
model this one follows). The token IS the defence: `content.render_video`
mints it with `secrets.token_urlsafe(32)` only once a render's file is
actually saved to disk, and stores it on `content_media.media_token` with a
24-hour `media_token_expires_at`. An expired or unknown token, a row whose
`status` is no longer `'done'` (a later `render_video_rollback` marks it
`'failed'` without deleting the row or clearing the token, so the query
itself must not treat that row as still good), or a token whose row has no
file behind it, all read the same: 404. A prober must never be able to tell
"wrong token" from "right token, gone" from "path traversal caught" — every
one of those is the same response.

**Path confinement.** The row's own `path` column is the only source of the
file to serve — nothing from the request ever touches the filesystem — but
it is still resolved with `os.path.realpath` and checked to sit inside
`content.DATA_DIR` before it is opened. `path` is written only by
`content.render_video`, always as `f"{DATA_DIR}/{media_id}.mp4"`, so this
check should never trip; it exists for the day a future bug puts something
else there, not because the token itself can smuggle `../` anywhere (the
token is a lookup key, never a filesystem path). A symlink at `path` is
refused outright, even one that resolves back inside `DATA_DIR` — only
`render_video` ever writes here, and always a plain file, so a symlink at
all is already the anomaly a future bug or a compromised disk would look
like, not something to trust just because it happens to resolve safely
today.

**HEAD.** Instagram's fetcher behaviour before a GET has not been
confirmed, so HEAD is answered the same way GET is looked up — same 404s,
same confinement — rather than left to fall through to FastAPI's default
405, which would hand a prober "this path exists, but only GET is allowed"
for free.

**Clips.** A token that starts with `clip.` is a campaign clip's, minted
by `clips.publish_clip` when the owner approves posting it: it is looked up in
`clip_outputs` (unexpired, and only while the clip is still unposted), and
the file must be a plain file inside that campaign's `out/` folder.
`secrets.token_urlsafe` never emits a `.`, so no content token can ever be
read as a clip token or the other way round, and every miss is the same
404.

**Tutorials.** A token that starts with `tut.` is an ATLAS Academy
tutorial's review link, minted by `academy.tutorials.review_tutorial` when
the draft card goes to #atlas-academy-videos (the owner watches it before he
approves): it is looked up in `academy_tutorial_reviews` (unexpired), and
it serves exactly that episode's `video.mp4` -- or, with `.jpg` on the end,
its `thumb.jpg` as an image, for the card's thumbnail -- each a plain file
directly inside its episode directory under the tutorials root. Same 404
for every miss.

**Camera clip videos.** A token that starts with `clipv.` is one home-camera clip
for WhatsApp (`atlas/modules/cameras/clip_share.py`): 256-bit random, five
minutes, gone after the first full download, never listed. The token is hashed to
find the file; no part of the request is a path. Only the exact path
`/media/clipv.<43 characters>` is forwarded by `Caddyfile.public`.

**Camera snapshots.** A token that starts with `cam.` is one home-camera
snapshot (`cam.<event>.<expiry>.<hmac>`, 15 minutes, minted by the camera
alert so WhatsApp can attach the photo). It resolves the file from the event
row, confines it under the snapshot root and serves JPEG only. Same 404 for
every miss.

This file must not import `atlas.kernel.executor` — it only ever reads.
"""
from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from ..kernel.state import fetch_one
from ..modules.content import DATA_DIR

log = logging.getLogger("atlas.api.media")

router = APIRouter()

_ROOT = os.path.realpath(DATA_DIR)


def _confined(path: str) -> str | None:
    """The resolved path, or None if it does not sit inside DATA_DIR."""
    real = os.path.realpath(path)
    if real == _ROOT or real.startswith(_ROOT + os.sep):
        return real
    return None


async def _resolved_path(token: str) -> str:
    """The one real file this token may serve, or an `HTTPException(404)`
    for every other case — unknown token, expired, rolled back, no file on
    the row, a symlink at `path`, or a path that does not confine to
    DATA_DIR. Shared by GET and HEAD so neither can diverge on what counts
    as a hit."""
    if token.startswith(CLIP_PREFIX):
        return await _clip_path(token)
    if token.startswith(TUT_PREFIX):
        return (await _tutorial_path(token))[0]
    if token.startswith(CAM_PREFIX):
        return await _cam_path(token)
    row = await fetch_one(
        """SELECT path FROM content_media
           WHERE media_token = $1 AND media_token_expires_at > now()
             AND status = 'done'""",
        token)
    if row is None:
        raise HTTPException(404, "not found")

    path = row["path"]
    if not path:
        raise HTTPException(404, "not found")

    if os.path.islink(path):
        log.error("content_media.path is a symlink, refusing it outright")
        raise HTTPException(404, "not found")

    real = _confined(path)
    if real is None:
        log.error("content_media.path escaped DATA_DIR: %r", path)
        raise HTTPException(404, "not found")
    if not os.path.isfile(real):
        raise HTTPException(404, "not found")

    return real


CLIP_PREFIX = "clip."


async def _clip_path(token: str) -> str:
    """A campaign clip's file for its `clip.` token, or the same 404."""
    from ..modules.clips import sources
    # Unposted only: waiting to post, or a draft the Mac agent fetches to
    # upload on Whop for the brand's approval (clips.whop.drafts).
    row = await fetch_one(
        """SELECT campaign, filename FROM clip_outputs
           WHERE media_token = $1 AND media_token_expires_at > now()
             AND status IN ('made', 'draft_pending', 'draft_submitted')""",
        token)
    if row is None:
        raise HTTPException(404, "not found")
    try:
        folder = sources.out_dir(str(row["campaign"]))
    except ValueError:
        raise HTTPException(404, "not found") from None
    path = folder / str(row["filename"])
    if os.path.islink(path):
        log.error("clip_outputs file is a symlink, refusing it outright")
        raise HTTPException(404, "not found")
    real = os.path.realpath(path)
    root = os.path.realpath(folder)
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        raise HTTPException(404, "not found")
    return real


CAM_PREFIX = "cam."


async def _cam_path(token: str) -> str:
    """A home-camera snapshot for its `cam.` token (HMAC-signed, 15 minutes,
    event id only), or the same 404. The path comes from the event row, is
    resolved with realpath, must sit under the snapshot root, be a plain `.jpg`
    file (no symlink) that starts with the JPEG magic bytes."""
    from ..modules.cameras import media_token, store, watcher
    event_id = media_token.verify(token)
    if event_id is None:
        raise HTTPException(404, "not found")
    row = await store.event(event_id)
    path = (row or {}).get("snapshot_path")
    if not path or not str(path).lower().endswith(".jpg"):
        raise HTTPException(404, "not found")
    if os.path.islink(path):
        log.error("a camera snapshot is a symlink, refusing it outright")
        raise HTTPException(404, "not found")
    real = os.path.realpath(path)
    root = os.path.realpath(watcher.snapshot_root())
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        raise HTTPException(404, "not found")
    def magic() -> bytes:
        with open(real, "rb") as fh:
            return fh.read(3)

    try:
        head = await asyncio.to_thread(magic)
    except OSError:
        raise HTTPException(404, "not found") from None
    if head != b"\xff\xd8\xff":
        raise HTTPException(404, "not found")
    return real


TUT_PREFIX = "tut."
THUMB_SUFFIX = ".jpg"


async def _tutorial_path(token: str) -> tuple[str, str]:
    """A tutorial review's video (or, `.jpg`, its thumbnail) and its media
    type, or the same 404."""
    from ..modules.academy import tutorials
    thumb = token.endswith(THUMB_SUFFIX)
    key = token[: -len(THUMB_SUFFIX)] if thumb else token
    row = await fetch_one(
        """SELECT series, episode FROM academy_tutorial_reviews
           WHERE media_token = $1 AND token_expires_at > now()""", key)
    if row is None:
        raise HTTPException(404, "not found")
    got = tutorials.resolve(str(row["series"]), int(row["episode"]))
    if isinstance(got, str):
        raise HTTPException(404, "not found")
    path = tutorials.episode_dir(*got) / ("thumb.jpg" if thumb else "video.mp4")
    if os.path.islink(path):
        log.error("a tutorial file is a symlink, refusing it outright")
        raise HTTPException(404, "not found")
    real = os.path.realpath(path)
    root = os.path.realpath(tutorials.root() / "episodes")
    if os.path.dirname(os.path.dirname(real)) != root or not os.path.isfile(real):
        raise HTTPException(404, "not found")
    return real, ("image/jpeg" if thumb else "video/mp4")


CLIPV_PREFIX = "clipv."
_CHUNK = 256 * 1024


async def _clip_share(token: str, request: Request) -> Response:
    """One camera clip for Twilio (`clipv.<token>`): the one-time, five-minute copy
    `clip_share` made. HEAD answers the size and leaves the link alive; GET streams
    the file and removes it once the last byte is sent. Every miss is the same 404."""
    from ..modules.cameras import clip_share
    key = token[len(CLIPV_PREFIX):]
    path = await asyncio.to_thread(clip_share.open_for, key)
    if path is None:
        raise HTTPException(404, "not found")
    headers = {"Cache-Control": "no-store", "Content-Disposition": "inline"}
    try:
        size = os.path.getsize(path)
        headers["Content-Length"] = str(size)
        if request.method == "HEAD":
            return Response(status_code=200, media_type="video/mp4", headers=headers)
        handle = await asyncio.to_thread(open, path, "rb")
    except OSError:
        raise HTTPException(404, "not found") from None

    async def body():
        done = False
        try:
            while chunk := await asyncio.to_thread(handle.read, _CHUNK):
                yield chunk
            done = True
        finally:
            handle.close()
            if done:                       # a dropped connection keeps the link alive
                await asyncio.to_thread(clip_share.consume, key)

    return StreamingResponse(body(), media_type="video/mp4", headers=headers)


@router.api_route("/media/{token}", methods=["GET", "HEAD"])
async def media(token: str, request: Request) -> Response:
    if token.startswith(CLIPV_PREFIX):
        return await _clip_share(token, request)
    kind = "video/mp4"
    if token.startswith(TUT_PREFIX):
        real, kind = await _tutorial_path(token)
    else:
        real = await _resolved_path(token)
        if token.startswith(CAM_PREFIX):
            kind = "image/jpeg"
    if request.method == "HEAD":
        return Response(
            status_code=200, media_type=kind,
            headers={"Content-Length": str(os.path.getsize(real))})
    return FileResponse(real, media_type=kind)
