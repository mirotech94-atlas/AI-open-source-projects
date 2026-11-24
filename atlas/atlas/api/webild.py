"""Where the Webild agent on the owner's Mac reads its tasks, and says what it did.

    GET  /webild/pending                 approved briefs waiting (read-only)
    POST /webild/claim                   the oldest one, now yours; 204 if none
    POST /webild/{lead_id}/submitted     {"url": "https://<site>.webild.io/"}
    POST /webild/{lead_id}/report        {"status": "needs_owner"|"failed",
                                          "summary": "..."}

the owner, 2026-09-29: a Webild site is built by an agent in his own Chrome, in
his own signed-in Webild account, never by ATLAS. Exactly like the Whop
submitter (`clips.py`): mesh-only by the middleware (`/webild` is not in
PUBLIC_PATHS) and authenticated with the `HANDOFF_TOKEN` bearer, checked
here -- an address is not a credential.

* `submitted` does not write the record itself: it hands the URL through
  `kernel.unattended.run_tier1` to the executor as
  `agency.record_webild_site` (Tier 1, dry run first, audited, refused
  while paused or watch-only), with no approver -- the agent's word is not
  the owner's. That action checks the URL is on a Webild host, records it only
  while the task has not expired, and runs the live launch check.
* `report` is for the agent stopping short (a payment wall, a login prompt,
  new terms). It closes the task with the agent's words, capped, and relays
  them to the owner labelled as the agent's account, not ATLAS's -- the same
  shape as `/handoffs/{id}/report`.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Request, Response

from .handoffs import _authorised, _json

log = logging.getLogger("atlas.api.webild")

router = APIRouter(prefix="/webild")


@router.get("/pending")
async def pending(authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    from ..modules.agency import webild
    tasks = await webild.pending()
    return {"read_at": datetime.now(UTC).isoformat(), "tasks": tasks}


@router.post("/claim")
async def claim(request: Request, authorization: str = Header("")):
    """queued -> claimed in the UPDATE that checks the expiry: two runs that
    overlap never both build the same site, and an expired task is never
    handed out late (invariant 5)."""
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    from ..modules.agency import webild
    body = await _json(request)
    task = await webild.claim(str(body.get("agent") or "webild-agent"))
    if task is None:
        return Response(status_code=204)
    return {"read_at": datetime.now(UTC).isoformat(), "task": task}


@router.post("/{lead_id}/submitted")
async def submitted(lead_id: int, request: Request,
                    authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    body = await _json(request)
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    from ..kernel.unattended import run_tier1
    got = await run_tier1(
        "agency.record_webild_site",
        {"lead_id": int(lead_id), "url": url[:500]},
        "the Webild agent on the owner's Mac published this lead's site")
    if not got.ok:
        raise HTTPException(409, got.detail[:300])
    evidence = got.evidence or {}
    if not evidence.get("already"):
        await _tell(f"Webild site for lead {lead_id}: "
                    f"{'ready' if evidence.get('ready') else 'NOT ready'}",
                    got.detail[:3000])
    return {"ok": True, "detail": got.detail,
            "already": bool(evidence.get("already")),
            "ready": evidence.get("ready")}


@router.post("/{lead_id}/report")
async def report(lead_id: int, request: Request,
                 authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad token")
    from ..modules.agency import webild
    body = await _json(request)
    status = str(body.get("status") or "")
    if status not in webild.REPORT_STATUSES:
        raise HTTPException(400, f"status must be one of {list(webild.REPORT_STATUSES)}")
    text = str(body.get("summary") or "").strip()
    if not text:
        raise HTTPException(400, "summary is required")
    row = await webild.record_report(int(lead_id), status, text)
    if row is None:
        raise HTTPException(409, f"lead {lead_id} has no Webild task waiting")
    label = {"failed": "could not build it", "needs_owner": "needs you"}[status]
    await _tell(f"Webild agent on lead {lead_id}: {label}",
                f"_Report from the Webild agent, in its words — ATLAS has not "
                f"verified it:_\n\n{text[:webild.MAX_REPORT]}")
    return {"ok": True}


async def _tell(title: str, body: str) -> None:
    from ..kernel.comms import Outbound, notify
    from ..kernel.contracts import Severity
    try:
        await notify(Outbound(title=title, body=body, severity=Severity.NORMAL,
                              topic="agency", kind="receipt"))
    except Exception:
        log.exception("could not relay a Webild agent update")
