"""Answering what people send the Guardian and FlyCheap LinkedIn pages,
through the one path that acts.

Miro, 2026-10-07: "make sure when requests come for either page you will see
it and reply to it". `modules/linkedin/requests.py` reads the requests and
page inboxes into `linkedin_requests` (a collector); this drives the reply,
the way `kernel.lead_followup.replies` drives an agency lead's:

  claim the row (an atomic UPDATE) -> `linkedin.draft_request_reply` through
  the executor -> `linkedin.send_request_reply` through the executor -> a
  Slack receipt in the page's channel, or the Tier 2 card with "Held for
  you: <reason>" first.

A **hold** (watch-only, a pause, an hourly ceiling) releases the claim and
stops the pass; the row is tried on a later tick. A **refusal** (a dry run
that said no -- a service request, a rule the draft breaks, the daily cap,
`LINKEDIN_REPLY_AUTOSEND=off`) is an answer: the row is marked held and the
card carries the reason and the draft. A draft that could not be written at
all is an alert with their words, so a request is never silently dropped.
The receipt is what makes the reply's sign-off ("Miro sees this thread")
true. While the LinkedIn browser is busy (a sweep, a publish, a scan) the
send waits for a later tick rather than queueing behind it.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any

log = logging.getLogger("atlas.linkedin_replies")

BATCH = 3


def interval() -> timedelta:
    try:
        return timedelta(minutes=max(1, int(os.getenv("LINKEDIN_REPLY_TICK_MIN", "10"))))
    except ValueError:
        return timedelta(minutes=10)


async def _notify(title: str, body: str, topic: str, kind: str) -> None:
    from .comms import Outbound, notify
    await notify(Outbound(title=title, body=body, topic=topic, kind=kind))


def _where(row: dict[str, Any]) -> str:
    from ..modules.linkedin.requests import FACTS
    facts = FACTS.get(str(row.get("page")))
    return f"the {facts.name if facts else row.get('page')} page"


async def tick() -> int:
    """One pass over requests waiting for a reply. Returns cards raised."""
    from ..modules.linkedin import requests as lr
    from ..modules.linkedin.browser import lock
    from .executor import ExecutionRequest, execute
    from .lead_followup import _card, _held
    raised = 0
    for row in await lr.awaiting_reply(BATCH):
        request_id = int(row["id"])
        if not await lr.claim(request_id):
            continue
        request = await lr.get_request(request_id) or {}
        who = str(request.get("requester") or "someone")
        page = str(request.get("page") or "")
        where = _where(request)
        draft = await lr.latest_draft(request_id)
        if not (draft and draft.get("status") == "draft"
                and draft.get("inbound_sha") == request.get("inbound_sha")
                and draft.get("send_attempted_at") is None):
            made = await execute(ExecutionRequest(
                "linkedin.draft_request_reply", {"request_id": request_id},
                reason=f"{who} wrote to {where} (request {request_id})"))
            if _held(made):
                await lr.release(request_id)
                log.info("linkedin replies held: %s", made.detail)
                break
            draft_id = (made.evidence or {}).get("draft_id") if made.ok else None
            if draft_id is None:
                await lr.set_status(request_id, "held", f"no draft: {made.detail}"[:500])
                await _notify(
                    f"LinkedIn: {who} wrote to {where}; no reply was drafted",
                    f"{made.detail}\n\nTheir words (as read, untrusted):\n"
                    f"{str(request.get('words') or '')[:600]}\n{request.get('url') or ''}",
                    page, "alert")
                continue
            draft = {"id": int(draft_id), "body": (made.evidence or {}).get("text")}
        draft_id = int(draft["id"])
        if lock().locked():
            # A sweep, a publish or a scan holds the browser: the draft is
            # kept, the claim released, and a later tick sends it.
            await lr.release(request_id)
            log.info("linkedin replies: the browser is busy; send deferred")
            break
        reason = f"reply to {who} on {where}, request {request_id}"
        sent = await execute(ExecutionRequest(
            "linkedin.send_request_reply", {"draft_id": draft_id}, reason=reason))
        if _held(sent):
            await lr.release(request_id)
            log.info("linkedin replies held at the send: %s", sent.detail)
            break
        if sent.ok:
            await _notify(
                f"Replied on LinkedIn to {who} ({where})",
                f"{sent.detail}. Sent without a card (your rule, 2026-10-07); the "
                f"reply says you see the thread.\n\nThey wrote (untrusted):\n"
                f"{str(request.get('words') or '')[:400]}\n\nReplied:\n{draft.get('body')}"
                f"\n{request.get('url') or ''}",
                page, "receipt")
            continue
        current = await lr.get_request(request_id) or {}
        if current.get("status") != "suppressed":
            await lr.set_status(request_id, "held", sent.detail[:500])
        raised += await _card(
            "linkedin.send_request_reply", {"draft_id": draft_id}, sent.detail,
            f"{reason}. They wrote: {str(request.get('words') or '')[:300]}\n"
            f"{request.get('url') or ''}",
            {"text": draft.get("body"), "url": request.get("url"),
             "source": request.get("source"), "page": page},
            f"linkedin.send_request_reply:{draft_id}")
    return raised
