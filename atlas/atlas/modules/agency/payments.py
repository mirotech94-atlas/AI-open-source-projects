"""agency.payments -- what Mapfront's Stripe account says was paid.

Miro, 2026-09-30: payments reach Mapfront's own Stripe account through the
Payment Links in `AGENCY_PAYMENT_LINKS` and through Stripe invoices. Stripe
tells ATLAS over a signed webhook (`api/stripe_mapfront.py`); this module
turns a verified event into rows. It never calls Stripe and holds no key --
it reads and records. Money out (refunds included) stays Tier 3.

  * ``checkout.session.completed`` (payment_status "paid") and
    ``invoice.paid`` record a payment with the amount exactly as Stripe
    states it (`amount_total` / `amount_paid`, in the smallest unit, with
    its currency -- never inferred from a price list), match it to a lead,
    mark that lead's deal paid, and post "Mapfront: <business> paid $X
    (Stripe, read HH:MM)" to Slack. A deal paid is what ends the first-sale
    price (`pricing.terms` reads it); a payment that matches no lead still
    counts as a sale, and Slack says it was unmatched.
  * Matching, in order: the Checkout Session's `client_reference_id`
    ("lead_<id>", which `replies.payment_url` adds to every link ATLAS
    sends), then `metadata.lead_id`, then the customer's email against the
    addresses on the lead's own record. Otherwise unmatched -- never a
    guess.
  * ``charge.refunded`` and ``charge.dispute.created`` update the payment
    (a full refund takes the deal off "paid") and ring Miro through the
    emergency path (`escalate.emergency`): a dispute is an emergency.

Every event id is claimed once (`agency_stripe_events`); a replay records
nothing twice.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import _store

log = logging.getLogger("atlas.agency.payments")

HANDLED = ("checkout.session.completed", "invoice.paid", "charge.refunded",
           "charge.dispute.created")
_REF = re.compile(r"^lead_(\d{1,12})$")
NY = ZoneInfo("America/New_York")


def money(amount_minor: int, currency: str) -> str:
    """Stripe's amount, shown exactly: $500, $500.50, 12.00 EUR."""
    cur = (currency or "").lower()
    whole, cents = divmod(int(amount_minor), 100)
    figure = f"{whole:,}" if cents == 0 else f"{whole:,}.{cents:02d}"
    return f"${figure}" if cur == "usd" else f"{figure} {cur.upper()}"


def _lead_ref(obj: dict[str, Any]) -> int | None:
    match = _REF.match(str(obj.get("client_reference_id") or ""))
    if match:
        return int(match.group(1))
    meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    raw = str(meta.get("lead_id") or "").strip()
    return int(raw) if raw.isdigit() else None


async def _match(obj: dict[str, Any], email: str | None) -> tuple[int | None, str]:
    ref = _lead_ref(obj)
    if ref is not None and await _store.get_lead(ref) is not None:
        return ref, ("reference" if obj.get("client_reference_id") else "metadata")
    if email:
        lead_id = await _store.lead_id_for_email(email)
        if lead_id is not None:
            return lead_id, "email"
    return None, "none"


def _paid_fields(kind: str, obj: dict[str, Any]) -> dict[str, Any] | None:
    """What Stripe says was paid, or None for an event that is not a payment
    yet (an unpaid or async Checkout Session)."""
    if kind == "checkout.session.completed":
        if obj.get("payment_status") != "paid" or obj.get("amount_total") is None:
            return None
        details = obj.get("customer_details") or {}
        return {"source": "checkout", "object_id": str(obj.get("id") or ""),
                "payment_intent": obj.get("payment_intent"), "charge": None,
                "email": details.get("email") or obj.get("customer_email"),
                "amount_minor": int(obj["amount_total"]),
                "currency": str(obj.get("currency") or "")}
    if kind == "invoice.paid":
        if obj.get("amount_paid") is None:
            return None
        return {"source": "invoice", "object_id": str(obj.get("id") or ""),
                "payment_intent": obj.get("payment_intent"), "charge": obj.get("charge"),
                "email": obj.get("customer_email"),
                "amount_minor": int(obj["amount_paid"]),
                "currency": str(obj.get("currency") or "")}
    return None


async def _notify(title: str, body: str) -> None:
    from ...kernel.comms import Outbound, notify
    await notify(Outbound(title=title, body=body, topic="agency", kind="receipt"))
    from . import owner_wa
    await owner_wa.notify_owner("portal", title)          # [Portal] a payment came in


async def _paid(event_id: str, kind: str, obj: dict[str, Any]) -> str:
    fields = _paid_fields(kind, obj)
    if fields is None:
        return "not a completed payment; nothing recorded"
    if fields["amount_minor"] <= 0:
        return "a zero amount; nothing recorded"
    # One payment can arrive twice under two event ids: a Payment Link with
    # post-payment invoices sends checkout.session.completed AND
    # invoice.paid. The same PaymentIntent or charge is recorded once.
    if await _store.payment_for(fields.get("payment_intent"), fields.get("charge")):
        return "already recorded under another event for the same payment"
    lead_id, how = await _match(obj, fields["email"])
    deal_id = None
    if lead_id is not None:
        deal_id = await _store.insert_paid_deal(
            lead_id, fields["amount_minor"], fields["currency"], f"stripe {event_id}")
    await _store.insert_payment(event_id=event_id, lead_id=lead_id, matched_by=how,
                                deal_id=deal_id, **fields)
    lead = await _store.get_lead(lead_id) if lead_id is not None else None
    business = (lead or {}).get("name") or "an unmatched customer"
    read = datetime.now(NY).strftime("%H:%M")
    shown = money(fields["amount_minor"], fields["currency"])
    body = (f"Matched by {how}" if lead_id is not None else
            "No lead matched (no reference, metadata or known email) -- it still "
            "still counts as a sale.")
    await _notify(f"Mapfront: {business} paid {shown} (Stripe, read {read})",
                  f"{body}. Stripe {fields['source']} {fields['object_id']}.")
    return f"recorded {shown} from {business}"


async def _trouble(event_id: str, kind: str, obj: dict[str, Any]) -> str:
    from . import escalate
    if kind == "charge.refunded":
        pi, charge = obj.get("payment_intent"), obj.get("id")
        refunded = int(obj.get("amount_refunded") or 0)
        total = int(obj.get("amount") or 0)
        status = "refunded" if total and refunded >= total else "partially_refunded"
        what = f"a refund of {money(refunded, str(obj.get('currency') or ''))}"
    else:
        pi, charge = obj.get("payment_intent"), obj.get("charge")
        refunded = None
        status = "disputed"
        what = (f"a dispute of {money(int(obj.get('amount') or 0), str(obj.get('currency') or ''))}"
                f" (reason: {str(obj.get('reason') or 'not given')[:60]})")
    payment = await _store.payment_for(pi, charge)
    business = (payment or {}).get("lead_name") or "an unmatched payment"
    if payment is not None:
        await _store.set_payment_status(int(payment["id"]), status, refunded)
        if payment.get("deal_id") and status in ("refunded", "disputed"):
            await _store.set_deal_status(int(payment["deal_id"]), status)
    body = (f"Stripe reports {what} on {business}'s payment"
            + ("" if payment else " -- no recorded payment matches it") + ". "
            "Nothing was done at Stripe: refunds and disputes are yours to handle.")
    await escalate.emergency(str(business), "a refund or a payment dispute", body)
    return f"{status}: {business}"


async def handle(event: dict[str, Any]) -> tuple[bool, str]:
    """One verified Stripe event. (recorded, what happened). Idempotent on
    the event id; a failure releases the claim so Stripe's retry runs it."""
    event_id = str(event.get("id") or "")
    kind = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object")) or {}
    if not event_id or not isinstance(obj, dict):
        return False, "not a Stripe event"
    if kind not in HANDLED:
        return False, f"{kind or 'an event'} is not one ATLAS records"
    if not await _store.claim_stripe_event(event_id, kind):
        return False, "duplicate"
    try:
        if kind in ("charge.refunded", "charge.dispute.created"):
            return True, await _trouble(event_id, kind, obj)
        return True, await _paid(event_id, kind, obj)
    except Exception:
        await _store.release_stripe_event(event_id)
        raise
