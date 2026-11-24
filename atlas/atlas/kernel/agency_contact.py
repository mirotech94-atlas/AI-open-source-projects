"""Queued website contact messages: deliver each channel through the executor.

The public form only writes a row (`modules/agency/contact.py`); this pass, every
minute, drives each waiting (message, channel) pair through the one path that
acts, `agency.deliver_contact` via `execute`, then asks Twilio what became of the
WhatsApp messages it accepted. One channel failing never stops the others: a
refusal on one pair is logged and the pass moves on to the next. Under
ATLAS_WATCH_ONLY the executor refuses, the rows stay queued and nothing is sent.
"""
from __future__ import annotations

import logging
from datetime import timedelta

log = logging.getLogger("atlas.agency_contact")

BATCH = 20


def interval() -> timedelta:
    return timedelta(minutes=1)


async def tick() -> int:
    """One pass. Returns the deliveries made."""
    from ..modules.agency import contact
    from .executor import ExecutionRequest, execute
    made = 0
    try:
        due = await contact._store.due(BATCH, contact._now())
    except Exception:  # noqa: BLE001
        log.warning("agency contact: the queue could not be read")
        return 0
    for contact_id, channel in due:
        result = await execute(ExecutionRequest(
            contact.ACTION, {"contact_id": contact_id, "channel": channel},
            reason="a visitor wrote on the website contact form"))
        if result.ok:
            made += 1
        else:
            log.info("agency contact #%s %s: not delivered: %s", contact_id, channel,
                     result.detail[:120])
    try:
        await contact.poll_whatsapp()
    except Exception:  # noqa: BLE001
        log.warning("agency contact: WhatsApp statuses could not be read")
    try:
        from ..modules.agency import owner_wa
        await owner_wa.drain()
    except Exception:  # noqa: BLE001
        log.warning("agency contact: owner WhatsApp queue could not be drained")
    return made


async def send_owner_notice(msg_id: int) -> bool:
    """One owner WhatsApp notice (`agency.notify_owner`) through the executor."""
    from ..modules.agency import owner_wa
    from .executor import ExecutionRequest, execute
    result = await execute(ExecutionRequest(
        owner_wa.ACTION, {"msg_id": msg_id},
        reason="an agency event Miro asked to hear about"))
    if not result.ok:
        log.info("owner WhatsApp #%s not sent: %s", msg_id, result.detail[:120])
    return result.ok
