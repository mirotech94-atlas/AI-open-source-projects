"""Answering ATLAS Academy support tickets, through the one path that acts.

the owner, 2026-10-11: "everything auto unless unsure". The store's Help form,
the support mailbox (`modules/academy/support_mail`, a collector) and the
chat's "send this to the owner" put tickets in `academy_support_tickets`; this
drives the answer the way `kernel.linkedin_replies` drives a page reply:

  claim the ticket (an atomic UPDATE) -> `academy.draft_support_reply`
  through the executor -> `academy.send_support_reply` through the
  executor -> a Slack receipt in the support channel, or the Tier 2 card
  with "Held for you: <reason>" first and the draft whole.

the owner, 2026-10-11 (memory approval-policy): a draft whose only hold is low
retrieval coverage sends when it is a direct quote of public product text
(`support_guard.direct_quote`); that is decided in code at draft time and
changes nothing here -- routes, holds, caps and the switch are the send's.

A **hold** (watch-only, a pause, an hourly ceiling) releases the claim and
stops the pass; the ticket is tried on a later tick. A **refusal** (a rule,
a route to the owner, an ungrounded answer, the daily cap,
`ACADEMY_SUPPORT_AUTOSEND=off`) is an answer: the ticket is marked held and
the card carries the reason and the draft. A draft that could not be
written at all is an alert with their words, so a ticket is never dropped.

A buyer who lost their access page also gets the `academy.resend_access`
card (Tier 2, irreversible) for their newest paid checkout on the same
address -- that action emails the address Stripe holds, never one a
message names. And once a tick, tickets nearing the 2-business-day promise
are posted once each.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("atlas.academy_support")

BATCH = 4
TOPIC = "academy_support"


def interval() -> timedelta:
    try:
        return timedelta(minutes=max(1, int(os.getenv("ACADEMY_SUPPORT_TICK_MIN", "5"))))
    except ValueError:
        return timedelta(minutes=5)


async def _notify(title: str, body: str, kind: str) -> None:
    from .comms import Outbound, notify
    await notify(Outbound(title=title, body=body, topic=TOPIC, kind=kind))


async def tick() -> int:
    """One pass: the channel (if asked for), the mailbox, the tickets, the
    SLA. Returns cards raised."""
    raised = 0
    for name, step in (("the support channel", _channel), ("the support inbox", _sweep)):
        try:
            await step()
        except Exception:
            log.warning("%s could not run", name, exc_info=True)
    try:
        raised += await replies()
    except Exception:
        log.warning("support replies could not run", exc_info=True)
    try:
        await sla()
    except Exception:
        log.warning("the support SLA pass could not run", exc_info=True)
    return raised


async def _channel() -> None:
    from ..modules.academy import support
    from .executor import ExecutionRequest, execute
    if not support.channel_wanted():
        return
    made = await execute(ExecutionRequest(
        "academy.create_support_channel", {},
        reason="ACADEMY_SUPPORT_SLACK_CREATE is on and support cards have no channel"))
    log.info("support channel: %s", made.detail)


async def _sweep() -> None:
    from ..modules.academy import support_mail
    result = await support_mail.sweep()
    if result.get("new"):
        log.info("support inbox: %s new ticket(s)", result["new"])


async def _resend_card(ticket: dict[str, Any]) -> bool:
    """The access link again, for a paid checkout on the ticket's own address."""
    from ..modules.academy import support
    from .approvals import Unrunnable, propose
    purchases = await support.paid_purchases(str(ticket["email"]))
    if not purchases:
        return False
    session_id = str(purchases[0]["session_id"])
    try:
        await propose(
            "academy.resend_access", {"session_id": session_id},
            f"Support ticket {ticket['id']}: {ticket['email']} wrote that they lost or "
            "never got their access page. This emails the access link to the address "
            "on their Stripe checkout (not one named in a message).",
            dedup_key=f"academy.resend_access:{session_id}", automatic=True)
        return True
    except (Unrunnable, ValueError) as exc:
        log.info("resend card for ticket %s not raised: %s", ticket["id"], exc)
        return False


async def replies() -> int:
    from ..modules.academy import support
    from ..modules.academy.support_guard import route
    from .executor import ExecutionRequest, execute
    from .lead_followup import _card, _held
    raised = 0
    for row in await support.awaiting(BATCH):
        ticket_id = int(row["id"])
        if not await support.claim(ticket_id):
            continue
        ticket = await support.get_ticket(ticket_id) or {}
        who = str(ticket.get("email") or "someone")
        words = f"{ticket.get('subject') or ''}\n{ticket.get('message') or ''}".strip()
        rt = route(words)
        if rt.lost_access and await _resend_card(ticket):
            raised += 1
        made = await execute(ExecutionRequest(
            "academy.draft_support_reply", {"ticket_id": ticket_id},
            reason=f"{who} wrote to ATLAS Academy support ({ticket.get('source')}, "
                   f"ticket {ticket_id})"))
        if _held(made):
            await support.release(ticket_id)
            log.info("support replies held: %s", made.detail)
            break
        draft_id = (made.evidence or {}).get("draft_id") if made.ok else None
        if draft_id is None:
            await support.set_status(ticket_id, "held", f"no draft: {made.detail}")
            await _notify(
                f"Academy support: {who} wrote; no reply was drafted",
                f"{made.detail}\n\nTheir words (as read, untrusted):\n{words[:800]}",
                "alert")
            continue
        text = str((made.evidence or {}).get("text") or "")
        reason = f"support reply to {who}, ticket {ticket_id}"
        sent = await execute(ExecutionRequest(
            "academy.send_support_reply", {"draft_id": int(draft_id)}, reason=reason))
        if _held(sent):
            await support.release(ticket_id)
            log.info("support replies held at the send: %s", sent.detail)
            break
        if sent.ok:
            await _notify(
                f"Academy support: answered {who}",
                f"{sent.detail}. Sent without a card (your rule, 2026-10-11).\n\n"
                f"They wrote (untrusted):\n{words[:500]}\n\nReplied:\n{text}",
                "receipt")
            continue
        current = await support.get_ticket(ticket_id) or {}
        if current.get("status") != "suppressed":
            await support.set_status(ticket_id, "held", sent.detail[:500])
        raised += await _card(
            "academy.send_support_reply", {"draft_id": int(draft_id)}, sent.detail,
            f"{reason} ({rt.reason}). They wrote (untrusted): {words[:400]}",
            {"to": who, "text": text, "route": rt.kind,
             "sources": (made.evidence or {}).get("sources")},
            f"academy.send_support_reply:{draft_id}")
    return raised


async def sla() -> int:
    """Post each ticket nearing the 2-business-day promise once."""
    from ..modules.academy import support
    now = datetime.now(UTC)
    posted = 0
    for row in await support.open_tickets():
        if row.get("sla_alerted_at") is not None:
            continue
        age = support.business_days_between(row["received_at"], now)
        if age < support.SLA_WARN:
            continue
        await support.mark_sla_alerted(int(row["id"]))
        await _notify(
            f"Academy support: ticket {row['id']} is {age:.1f} business days old",
            f"From {row['email']} ({row['source']}, {row['route'] or 'unrouted'}), status "
            f"{row['status']}. The promise is a reply within 2 business days. Its card, "
            "if any, is in this channel.", "alert")
        posted += 1
    return posted
