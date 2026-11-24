"""Where the agent on the owner's Mac reads clips to submit to Whop, and says it did.

    GET  /clips/whop/pending               posted clips awaiting submission, and
                                           the drafts to upload for approval
    POST /clips/whop/{clip_id}/draft_submitted   (no body) the draft is uploaded
    POST /clips/whop/{clip_id}/draft_decided     {"approved": bool, "note": "..."}
    POST /clips/whop/{clip_id}/submitted   {"post_url": "..."}
    GET  /clips/links/pending              TikTok clips still without a link
    POST /clips/links/{clip_id}            {"url": "..."}  a TikTok video link

the owner, 2026-09-29: the Whop submission is made by an agent in his own Chrome,
never by ATLAS. Exactly like `/handoffs`: mesh-only by the middleware
(`/clips` is not in PUBLIC_PATHS; `MESH_ALLOWED_CIDRS`, which carries the
`ATLAS_TRUSTED_IPS` addresses Caddy lets in) and authenticated with the
bearer `HANDOFF_TOKEN`, checked by `handoffs._authorised` itself -- an
address is not a credential. The record is not written here: the POST hands
it through `kernel.unattended.run_tier1` (the one door an API surface has)
to the executor as `clips.record_whop_submission` (Tier 1, its dry run
first, audited, rate-limited, refused while ATLAS is paused or watch-only),
with no approver -- the agent's word is not the owner's.

The links route (2026-09-30) takes the same bearer on the same mesh: a
Mac-side task reads the public @example_handle profile in a browser and hands
in candidate video URLs for clips Postiz posted without one. Its word is
not written either: `clips.links.record_tiktok_link` reads TikTok's own
public oEmbed and records the link only when TikTok confirms the account,
the caption and the posting time -- verified platform data, the same as
the reconciler's own writes (invariant 6). It answers 400 for a URL of the
wrong shape, 404 for no such clip, 409 with the reason when the row is not
eligible or TikTok does not confirm, 503 when TikTok could not be read
(nothing written), and 429 past `LINKS_PER_HOUR` checks an hour per token.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request

from .handoffs import _authorised
from .peers import PerAddressLimiter

router = APIRouter(prefix="/clips")

# Each handed-in link costs one read of TikTok: at most this many an hour
# for one bearer, counted whether or not the link is taken.
LINKS_PER_HOUR = 30
_links_per_token = PerAddressLimiter(LINKS_PER_HOUR, 3600.0)


def _token_key(authorization: str) -> str:
    """The limiter's key for a bearer: a digest, never the token itself."""
    return hashlib.sha256(authorization.strip().encode()).hexdigest()[:24]


@router.get("/whop/pending")
async def whop_pending(authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    from ..modules.clips import whop
    clips = await whop.pending()
    # Drafts (2026-10-01): clips of a campaign that wants the brand's
    # approval before posting, each with a signed download link the agent
    # fetches the MP4 from to upload it on Whop.
    drafts = await whop.drafts()
    # Approved drafts not yet posted (2026-10-06): the agent asks for the
    # post (`/post_now`) and submits the link inside Whop's 30-minute window.
    approved = await whop.approved()
    return {"read_at": datetime.now(UTC).isoformat(), "clips": clips,
            "drafts": drafts, "approved": approved}


@router.post("/whop/{clip_id}/draft_submitted")
async def whop_draft_submitted(clip_id: int, authorization: str = Header("")) -> dict:
    """The agent uploaded this clip as a draft on Whop. No body."""
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    from ..kernel.unattended import run_tier1
    got = await run_tier1(
        "clips.record_draft_submitted", {"clip_id": int(clip_id)},
        "the agent on the owner's Mac uploaded this clip's draft on Whop")
    if not got.ok:
        raise HTTPException(409, got.detail[:300])
    return {"ok": True, "detail": got.detail,
            "already": bool((got.evidence or {}).get("already"))}


@router.post("/whop/{clip_id}/draft_decided")
async def whop_draft_decided(clip_id: int, request: Request,
                             authorization: str = Header("")) -> dict:
    """What the brand decided about the draft, as the agent read it on
    Whop: {"approved": true|false, "note": "..."}."""
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    try:
        body = await request.json()
    except Exception:                                   # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    approved = body.get("approved")
    if not isinstance(approved, bool):
        raise HTTPException(400, "approved must be true or false")
    note = " ".join(str(body.get("note") or "").split())[:500]
    from ..kernel.unattended import run_tier1
    got = await run_tier1(
        "clips.record_draft_decision",
        {"clip_id": int(clip_id), "approved": approved, "note": note},
        f"the brand {'approved' if approved else 'rejected'} this clip's draft on "
        f"Whop, as the agent on the owner's Mac read it")
    if not got.ok:
        raise HTTPException(409, got.detail[:300])
    return {"ok": True, "detail": got.detail,
            "already": bool((got.evidence or {}).get("already"))}


@router.post("/whop/{clip_id}/post_now")
async def whop_post_now(clip_id: int, authorization: str = Header("")) -> dict:
    """Post an approved draft to its platform now, so the Mac agent can
    submit its live link inside Whop's window (read live 2026-10-06: a
    link is accepted only within 30 minutes of the post going live). The
    same Tier 1 action the posting pass would run, with every one of its
    gates; the only difference is who asked and when."""
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    from ..kernel.unattended import run_tier1
    from ..modules.clips import posting
    rows = await posting._clips([int(clip_id)])
    row = rows.get(int(clip_id))
    if row is None:
        raise HTTPException(404, f"no clip {clip_id}")
    if str(row.get("status") or "") != "draft_approved":
        raise HTTPException(409, f"clip {clip_id} is {row.get('status')}, not an "
                                 f"approved draft")
    action = posting.auto_action(row)
    if action is None:
        raise HTTPException(409, f"clip {clip_id}'s platform {row.get('platform')} "
                                 f"has no automatic post configured")
    got = await run_tier1(action, {"clip_id": int(clip_id)},
                          "the agent on the owner's Mac is submitting this clip's live "
                          "link on Whop and needs it posted now")
    if not got.ok:
        raise HTTPException(409, got.detail[:300])
    return {"ok": True, "detail": got.detail,
            "post_url": (got.evidence or {}).get("post_url")}


@router.post("/whop/{clip_id}/submitted")
async def whop_submitted(clip_id: int, request: Request,
                         authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    try:
        body = await request.json()
    except Exception:                                   # noqa: BLE001
        body = {}
    post_url = str((body if isinstance(body, dict) else {}).get("post_url") or "")
    if not post_url.strip():
        raise HTTPException(400, "post_url is required")
    # The kernel's one door for a surface with nobody attached: a named
    # Tier 1 action only, no approver, then the executor's own checks.
    from ..kernel.unattended import run_tier1
    got = await run_tier1(
        "clips.record_whop_submission",
        {"clip_id": int(clip_id), "post_url": post_url.strip()[:500]},
        "the agent on the owner's Mac submitted this clip on Whop")
    if not got.ok:
        raise HTTPException(409, got.detail[:300])
    return {"ok": True, "detail": got.detail,
            "already": bool((got.evidence or {}).get("already"))}


@router.get("/links/pending")
async def links_pending(authorization: str = Header("")) -> dict:
    """The TikTok clips with no link yet, for the reader that matches them
    against the public profile before handing a link in."""
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    from ..modules.clips import links
    clips = await links.pending_tiktok()
    return {"read_at": datetime.now(UTC).isoformat(), "clips": clips}


@router.post("/links/{clip_id}")
async def record_link(clip_id: int, request: Request,
                      authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    if not _links_per_token.allow(_token_key(authorization)):
        raise HTTPException(429, f"at most {LINKS_PER_HOUR} link checks an hour")
    try:
        body = await request.json()
    except Exception:                                   # noqa: BLE001
        body = {}
    url = str((body if isinstance(body, dict) else {}).get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    from ..modules.clips import links
    try:
        got = await links.record_tiktok_link(int(clip_id), url[:300])
    except links.Refused as exc:
        raise HTTPException(exc.status, exc.reason[:300]) from exc
    return {"ok": True, "read_at": datetime.now(UTC).isoformat(), **got}
