"""Where the coding agent on the owner's Mac collects approved work and reports.

Mesh-only by the middleware (`/handoffs` is not in PUBLIC_PATHS) and
authenticated with a bearer token, the same defence in depth as
`/ingest/browser`: an address is not a credential.

Two calls, and neither can create work:

* `POST /handoffs/claim` hands over the oldest queued task, claiming it in
  the same UPDATE that checks it has not expired -- silence is never
  consent, so an expired task is never handed out late.
* `POST /handoffs/{id}/report` closes a claimed task and relays the report to
  the thread he asked in. A done report may carry `artifact_url`, the Claude
  Docs link for a client-site document (`agency.sitedocs`); only a claude.ai
  artifact link is taken, and it is recorded as the agent's word. The report
  is the agent's account, not ATLAS's: it is labelled as such, capped, and
  stored for whatever reads it next to wrap as untrusted (invariant 4).
"""
from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request, Response

log = logging.getLogger("atlas.api.handoffs")

router = APIRouter(prefix="/handoffs")

REPORT_STATUSES = {"done", "failed", "needs_owner"}
MAX_REPORT = 6000


def _authorised(header: str) -> bool:
    expected = os.getenv("HANDOFF_TOKEN", "")
    if len(expected) < 24:
        return False
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    return hmac.compare_digest(supplied, expected)


@router.post("/claim")
async def claim(request: Request, authorization: str = Header("")):
    if not _authorised(authorization):
        raise HTTPException(401, "bad handoff token")
    from ..kernel.audit import audit
    from ..kernel.state import fetch_one

    body = await _json(request)
    who = str(body.get("agent") or "claude-code")[:60]
    await fetch_one(
        """UPDATE handoffs SET status = 'expired'
            WHERE status = 'queued' AND expires_at <= now() RETURNING id""")
    row = await fetch_one(
        # The outer WHERE repeats the test, so two claimers racing for the
        # same row get it once: the loser's UPDATE matches nothing.
        """UPDATE handoffs SET status = 'claimed', claimed_at = now(), claimed_by = $1
            WHERE id = (SELECT min(id) FROM handoffs
                         WHERE status = 'queued' AND expires_at > now())
              AND status = 'queued' AND expires_at > now()
        RETURNING id, brief, target""", who)
    if row is None:
        return Response(status_code=204)
    await audit("handoff.claimed", f"handoff:{row['id']}", {"by": who})
    return {"id": row["id"], "target": row["target"], "brief": row["brief"],
            "report_to": f"/handoffs/{row['id']}/report",
            "statuses": sorted(REPORT_STATUSES)}


@router.post("/{hid}/report")
async def report(hid: int, request: Request, authorization: str = Header("")) -> dict:
    if not _authorised(authorization):
        raise HTTPException(401, "bad handoff token")
    from ..kernel.audit import audit
    from ..kernel.comms import Outbound, notify
    from ..kernel.contracts import Severity
    from ..kernel.state import fetch_one

    body = await _json(request)
    status = str(body.get("status") or "")
    if status not in REPORT_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(REPORT_STATUSES)}")
    text = str(body.get("summary") or "").strip()[:MAX_REPORT]
    if not text:
        raise HTTPException(400, "summary is required")
    # A Claude Docs link for a client-site document (agency.publish_doc).
    # Checked before anything changes; only a claude.ai artifact link, and
    # only on a task reported done.
    from ..modules.agency import sitedocs
    url = str(body.get("artifact_url") or "").strip()
    if url and not sitedocs.artifact_url_ok(url):
        raise HTTPException(400, "artifact_url must be a https://claude.ai/ "
                                 "artifact link")
    if url and status != "done":
        raise HTTPException(400, "artifact_url comes only with status done")
    row = await fetch_one(
        """UPDATE handoffs SET status = $2, report = $3, reported_at = now()
            WHERE id = $1 AND status = 'claimed'
        RETURNING id, request, channel, thread, topic""", hid, status, text)
    if row is None:
        raise HTTPException(409, f"handoff {hid} is not claimed")
    await audit("handoff.reported", f"handoff:{hid}", {"status": status})
    label = {"done": "done", "failed": "could not do it",
             "needs_owner": "needs you"}[status]
    link = ""
    if url:
        try:
            doc = await sitedocs.record_link(hid, url)
        except Exception:
            log.exception("could not record handoff %s's Docs link", hid)
            doc = None
        link = (f"\n\nDocs link for {doc['slug']} \u201c{doc['title']}\u201d "
                f"(reported by the coding agent, not verified): {url}" if doc
                else f"\n\nDocs link reported, but no queued client-site "
                     f"document is on this handoff, so it was not recorded: {url}")
        await audit("handoff.artifact", f"handoff:{hid}",
                    {"recorded": bool(doc), "url": url})
    try:
        await notify(Outbound(
            title=f"Coding agent on handoff #{hid}: {label}",
            body=(f"_Report from the coding agent, in its words — ATLAS has not "
                  f"verified it:_\n\n{text}{link}"),
            severity=Severity.NORMAL, topic=row["topic"] or "decisions",
            thread=row["thread"] or None, channel=row["channel"] or None,
            kind="receipt"))
    except Exception:
        log.exception("could not relay handoff %s's report", hid)
    return {"ok": True}


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:                                   # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}
