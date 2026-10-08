"""Read-back for `stripe.retry_invoice` (RA-D1). Reads only: GET the invoice
and its latest payment attempt through the module's own restricted key.

The run POSTs `/invoices/{id}/pay` and reports ok only on HTTP 200, so the
authority is the invoice itself: `paid` is the effect, anything else is not.
The latest attempt is read too, from whichever field the account's API
version carries (`payment_intent`, `charge`, or the `payments` list), so a
failure names the decline rather than just "still open". A `processing`
attempt is not a failure yet: unknown, class stale.

Whether a receipt was sent (the charge's `receipt_number` or
`receipt_email`) is reported in the evidence and never decides the state.
The customer's email is never put in evidence -- the audit chain keeps it
for good; only whether one was set. See docs/verification.md.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    State,
    Verification,
    classify,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import BUSINESSES, NoKey, _get

PAID = "paid"


def _id_of(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return str(value or "")


async def _attempt(business: str, invoice: dict[str, Any]) -> dict[str, Any]:
    """The latest payment attempt, as a small secret-free dict, or {} when
    none can be read. A failed read here is recorded, never guessed."""
    intent = _id_of(invoice.get("payment_intent"))
    charge = _id_of(invoice.get("charge"))
    if not intent:
        payments = invoice.get("payments")
        rows = payments.get("data") if isinstance(payments, dict) else None
        for row in rows or []:
            pay = (row or {}).get("payment") if isinstance(row, dict) else None
            if isinstance(pay, dict):
                intent = _id_of(pay.get("payment_intent"))
                charge = charge or _id_of(pay.get("charge"))
                if intent or charge:
                    break
    try:
        if intent:
            pi = await _get(business, f"payment_intents/{intent}")
            err = pi.get("last_payment_error") or {}
            out = {"payment_intent": pi.get("id"), "status": pi.get("status"),
                   "decline_code": err.get("decline_code") or err.get("code"),
                   "created": pi.get("created")}
            charge = _id_of(pi.get("latest_charge")) or charge
            if charge:
                out["receipt"] = await _receipt(business, charge)
            return out
        if charge:
            ch = await _get(business, f"charges/{charge}")
            return {"charge": ch.get("id"), "status": ch.get("status"),
                    "failure_code": ch.get("failure_code"),
                    "created": ch.get("created"), "receipt": _receipt_of(ch)}
    except (httpx.HTTPError, NoKey) as exc:
        return {"unread": type(exc).__name__}
    return {}


def _receipt_of(charge: dict[str, Any]) -> dict[str, Any]:
    """Whether Stripe sent a receipt for this charge. Evidence only: a paid
    invoice with no receipt is still paid. The address itself is a customer's
    and never goes into the audit chain -- only whether one was set."""
    number = charge.get("receipt_number")
    email_set = bool(charge.get("receipt_email"))
    return {"sent": bool(number) or email_set, "receipt_number": number,
            "receipt_email_set": email_set}


async def _receipt(business: str, charge_id: str) -> dict[str, Any]:
    try:
        return _receipt_of(await _get(business, f"charges/{charge_id}"))
    except (httpx.HTTPError, NoKey) as exc:
        return {"sent": None, "unread": type(exc).__name__}


def _judge(invoice: dict[str, Any], attempt: dict[str, Any],
           invoice_id: str) -> Verification:
    status = str(invoice.get("status") or "")
    evidence = {"invoice": invoice.get("id"), "status": status,
                "amount_paid": invoice.get("amount_paid"),
                "amount_due": invoice.get("amount_due"),
                "attempt_count": invoice.get("attempt_count"),
                "paid_at": (invoice.get("status_transitions") or {}).get("paid_at"),
                "attempt": attempt or None}
    if str(invoice.get("id") or "") != invoice_id:
        return contradicted("Stripe answered for a different invoice", **evidence)
    if status == PAID:
        return verified(**evidence)
    if attempt.get("status") == "processing":
        return unknown(f"invoice {invoice_id} is {status}; its payment is still "
                       "processing", FailureClass.STALE, **evidence)
    if status == "open":
        why = attempt.get("decline_code") or attempt.get("failure_code")
        return contradicted(f"invoice {invoice_id} is still open"
                            + (f"; the latest attempt failed ({why})" if why else ""),
                            **evidence)
    return contradicted(f"invoice {invoice_id} is {status or 'unreadable'}, not paid",
                        **evidence)


async def _read(business: str, invoice_id: str) -> Verification | tuple[dict, dict]:
    try:
        invoice = await _get(business, f"invoices/{invoice_id}")
    except NoKey as exc:
        return unknown(str(exc).split(".")[0], FailureClass.DISABLED)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return contradicted(f"invoice {invoice_id} does not exist on {business}",
                                invoice=invoice_id)
        return unknown(f"could not read invoice {invoice_id}", classify(exc))
    except httpx.HTTPError as exc:
        return unknown(f"could not read invoice {invoice_id}", classify(exc))
    if not isinstance(invoice, dict):
        return unknown("Stripe answered with an unexpected shape")
    return invoice, await _attempt(business, invoice)


def _args(params: dict[str, Any]) -> tuple[str, str] | None:
    business = str(params.get("business") or "")
    invoice_id = str(params.get("invoice_id") or "")
    if business not in BUSINESSES or not invoice_id.startswith("in_"):
        return None
    return business, invoice_id


@verifier("stripe.retry_invoice")
async def verify_retry(params: dict[str, Any], result: ActionResult) -> Verification:
    args = _args(params)
    if args is None:
        return unknown("the run names no business and invoice id",
                       FailureClass.VALIDATION)
    got = await _read(*args)
    if isinstance(got, Verification):
        return got
    return _judge(got[0], got[1], args[1])


@reconciler("stripe.retry_invoice")
async def reconcile_retry(params: dict[str, Any], started: datetime) -> Verification:
    """`/pay` sent and no finish recorded. Paid is paid, whoever paid it; an
    open invoice whose latest attempt was made after the run started is a
    failed retry; an open invoice with no attempt since then was never
    retried (or Stripe never saw the request)."""
    args = _args(params)
    if args is None:
        return unknown("the start row names no business and invoice id",
                       FailureClass.VALIDATION)
    got = await _read(*args)
    if isinstance(got, Verification):
        return got
    invoice, attempt = got
    judged = _judge(invoice, attempt, args[1])
    if judged.state is not State.CONTRADICTED or invoice.get("status") != "open":
        return judged
    created = attempt.get("created")
    if isinstance(created, int) and datetime.fromtimestamp(created, UTC) >= started:
        return judged
    if "unread" in attempt:
        return unknown("the invoice is open and its latest attempt could not be "
                       "read", **judged.evidence)
    return contradicted(f"invoice {args[1]} is open and no payment attempt was made "
                        "after the run started", **judged.evidence)
