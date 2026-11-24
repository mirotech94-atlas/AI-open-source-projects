"""`academy.resend_access` (Tier 2, irreversible): email a buyer their link.

For a buyer who lost their access page. Miro approves one card; the
executor runs this. The recipient is never a parameter: it is the email
address Stripe holds for that checkout, read fresh, so a message that says
"send my link to this other address" cannot redirect it. It sends only for
a checkout that is paid, not refunded or disputed, and matches the catalog
-- the same verdict the access page itself uses.

An email cannot be unsent, so this is irreversible and can never be Tier 1.
It goes through the same SMTP path as Mapfront's outreach
(`agency.outreach._smtp_send`, Resend by default), from the same address.
"""
from __future__ import annotations

from typing import Any

from ...kernel.contracts import ActionResult
from . import delivery
from .catalog import CatalogError, load
from .stripe_read import Verdict, read_session, valid_session_id

SUBJECT = "Your ATLAS Academy downloads"


def vet(params: dict[str, Any]) -> str | None:
    """Refuse a malformed session id before a card exists."""
    if not valid_session_id(params.get("session_id")):
        return ("session_id must be a Stripe checkout session id "
                "(cs_live_... or cs_test_...)")
    return None


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


def body_for(v: Verdict, contact: str) -> str:
    names = ", ".join(o.name for o in v.offers)
    return (
        f"Hello,\n\nHere is the link to your ATLAS Academy downloads ({names}):\n\n"
        f"{delivery.access_url(v.session_id)}\n\n"
        "Bookmark it: the page makes fresh download links each time you open it, "
        "for as long as your purchase is paid and not refunded.\n\n"
        f"Questions: reply to this email or write to {contact}.\n\n"
        "ATLAS Academy\n"
        "You're receiving this because you asked for your link again. It is the only "
        "email we send about this purchase.\n")


async def _checked(session_id: str) -> tuple[Verdict | None, str | None]:
    if not valid_session_id(session_id):
        return None, "not a checkout session id"
    try:
        cat = load()
    except CatalogError as exc:
        return None, f"the catalog is unreadable: {exc}"
    v = await read_session(session_id, cat)
    if not v.paid:
        return v, f"not sending: Stripe's answer is '{v.status}' ({v.detail})"
    if not v.email or "@" not in v.email:
        return v, "not sending: Stripe has no email address for this checkout"
    return v, None


async def resend_access_dry(session_id: str = "") -> ActionResult:
    v, why = await _checked(session_id)
    if why is not None or v is None:
        return ActionResult(False, why or "not sending")
    from ..agency.outreach import _send_from_address
    names = ", ".join(o.name for o in v.offers)
    return ActionResult(True, (
        f"would email {_mask(v.email)} (the address on the Stripe checkout) the "
        f"access link for {names}, from {_send_from_address()}; paid "
        f"{v.amount_text()}, read from Stripe at {v.read_at:%H:%M UTC}"),
        evidence={"to": _mask(v.email), "offers": [o.id for o in v.offers]})


async def resend_access(session_id: str = "") -> ActionResult:
    v, why = await _checked(session_id)
    if why is not None or v is None:
        return ActionResult(False, why or "not sending")
    from ..agency.outreach import _send_from_address, _smtp_send
    cat = load()
    sender = _send_from_address()
    ok, reason = await _smtp_send(sender, v.email, SUBJECT,
                                  body_for(v, cat.contact_email), html=True)
    if not ok:
        return ActionResult(False, f"send failed: {reason}")
    from ...kernel.audit import audit
    await audit("academy.access_resent", v.session_id[:14], {"to": _mask(v.email)})
    return ActionResult(True, f"emailed {_mask(v.email)} their access link",
                        evidence={"to": _mask(v.email),
                                  "offers": [o.id for o in v.offers]})
