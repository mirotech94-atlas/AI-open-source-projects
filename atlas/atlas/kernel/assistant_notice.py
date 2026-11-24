"""Kernel side of the assistant's owner update: the one way a queued notice is
sent is the Tier 1 action `assistant.update_owner` through the executor. It lives
here so that nothing in `atlas/modules/assistant` imports the executor
(invariant 2; same split as `agency_contact.send_owner_notice`)."""
from __future__ import annotations

import logging

log = logging.getLogger("atlas.assistant.notices")


async def send_notice(notice_id: int) -> bool:
    from ..modules.assistant import notices
    from .executor import ExecutionRequest, execute
    result = await execute(ExecutionRequest(
        notices.ACTION, {"notice_id": notice_id},
        reason="a short update Miro asked to hear about"))
    if not result.ok:
        log.info("owner notice #%s not sent: %s", notice_id, result.detail[:120])
    return result.ok
