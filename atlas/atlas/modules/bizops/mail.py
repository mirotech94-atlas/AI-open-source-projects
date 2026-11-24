"""The one way BizOps emails a client: the agency's own send internals
(`replies._recipient`, `replies._email_problem`, `outreach._smtp_send`)
behind an effect claim, so an approved card sends at most once."""
from __future__ import annotations

from ...kernel.contracts import ActionResult


async def guard(lead_id: int, to: str, subject: str, body: str) -> str | None:
    """Every rule read again at send time; None when it may go."""
    from ..agency import replies
    current, why = await replies._recipient(int(lead_id), "email")
    if current is None:
        return str(why)
    if current.strip().lower() != (to or "").strip().lower():
        return "the lead's address changed since the draft; draft it again"
    return replies._email_problem(to, subject, body)


async def send(action: str, key: str, to: str, subject: str, body: str) -> ActionResult:
    from email.utils import make_msgid

    from ...kernel import idempotency
    from ..agency import outreach
    state = await idempotency.claim(action, key)
    if state != idempotency.CLAIMED:
        return ActionResult(False, idempotency.refusal(action, state))
    from_address = outreach._send_from_address()
    ok, reason = await outreach._smtp_send(
        from_address, to, subject, body,
        message_id=make_msgid(domain=from_address.rpartition("@")[2] or None))
    await idempotency.finish(action, key, None if ok else str(reason))
    if not ok:
        return ActionResult(False, f"send failed: {reason}")
    return ActionResult(True, f"emailed {to}", evidence={"to": to, "subject": subject,
                                                        "text": body})
