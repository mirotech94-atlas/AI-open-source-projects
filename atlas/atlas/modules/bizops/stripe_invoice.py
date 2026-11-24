"""Stripe invoices, TEST MODE ONLY (six agents phase 9).

`MAPFRONT_STRIPE_INVOICE_KEY` is read at call time. Only `sk_test_` and
`rk_test_` keys are accepted; a live key is refused before any call with
"live keys are not enabled in this build" -- going live is a later decision,
made in code, never at runtime. Every answer must say `livemode: false` or
the step is refused. Every call carries `Idempotency-Key:
bizops-inv-<id>-<step>`, so a retried step never makes a second object.
The key goes only into the Authorization header; every reason passes
`_http.redact` before it reaches a result, a card or a log line.

    draft_invoice (Tier 1)      our row + a "TEST MODE" card
    send_invoice  (Tier 2)      customer (found or created), invoice, item,
                                finalize, send; rollback: void, unpaid only
    check_invoice_status        GET each open invoice every 30 minutes
    draft/send_payment_reminder one email per invoice per 7 days
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult, FailureClass, Health, Observation
from .. import _http
from . import store

log = logging.getLogger("atlas.bizops")

KEY_ENV = "MAPFRONT_STRIPE_INVOICE_KEY"
API = "https://api.stripe.com/v1"
LIVE_REFUSAL = "live keys are not enabled in this build"
TEST_PREFIXES = ("sk_test_", "rk_test_")
LIVE_PREFIXES = ("sk_live_", "rk_live_")
PRICES = frozenset({500, 300})
DAYS_DUE = 14
REMIND_EVERY = timedelta(days=7)
SIGNATURE = "ATLAS, assistant to the owner at Mapfront"
SEND_ACTION = "bizops.send_invoice"


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def key() -> tuple[str | None, str | None]:
    """(the test key, None) or (None, why it cannot be used)."""
    raw = os.getenv(KEY_ENV, "").strip()
    if not raw:
        return None, f"{KEY_ENV} is not set (CONFIGURE.md, six agents step 4)"
    if raw.startswith(LIVE_PREFIXES):
        return None, LIVE_REFUSAL
    if not raw.startswith(TEST_PREFIXES):
        return None, f"{KEY_ENV} is not a Stripe test key (sk_test_ or rk_test_)"
    return raw, None


def _live(got: dict[str, Any]) -> bool:
    """True unless Stripe said test mode: an object must carry livemode false;
    a list's items must not carry livemode true."""
    if got.get("object") == "list":
        return any(isinstance(i, dict) and i.get("livemode") is not False
                   for i in got.get("data") or [])
    return got.get("livemode") is not False


async def call(method: str, path: str, secret: str, idem: str, *,
               form: dict[str, Any] | None = None,
               params: dict[str, Any] | None = None) -> tuple[dict | None, str | None]:
    """One Stripe call: (answer, None) or (None, a redacted reason)."""
    headers = {"Authorization": f"Bearer {secret}", "Idempotency-Key": idem}
    if method == "GET":
        got, why = await _http.get_json(API + path, headers=headers, params=params)
    else:
        got, why = await _http.post_json(API + path, headers=headers, data=form)
    if why is not None:
        return None, _http.redact(why)
    if not isinstance(got, dict):
        return None, "Stripe answered with an unexpected shape"
    if _live(got):
        return None, "Stripe answered in live mode or without livemode; refused"
    return got, None


def memo(name: str, amount: int) -> str:
    return (f"Website build for {name}.\nTEST MODE invoice: no real money moves.\n"
            f"Amount: ${amount}.\n\n{SIGNATURE}")


# ------------------------------------------------------------------ drafting

async def _amount(lead_id: int) -> tuple[int | None, str]:
    from ..agency import pricing
    won = await store.won_amount(lead_id)
    if won in PRICES:
        return won, "the won deal"
    price = (await pricing.terms(lead_id)).price
    if price in PRICES:
        return price, "the lead's price"
    return None, (f"no amount of $500 or $300 for this lead (won deal {won}, "
                  f"price {price}); an invoice is never another figure")


async def _draft_refusal(lead_id: int) -> tuple[dict[str, Any] | None, str | None]:
    if not await store.has_won(lead_id):
        return None, "no won deal for this lead; nothing to invoice"
    if await store.agreement_sent(lead_id):
        return None, "an agreement was already sent to this lead; it carries the payment"
    if await store.deal_paid(lead_id):
        return None, "this lead's deal is already paid"
    if (open_id := await store.open_invoice_for(lead_id)) is not None:
        return None, f"invoice {open_id} is already open for this lead"
    amount, why = await _amount(lead_id)
    if amount is None:
        return None, why
    from ..agency import replies
    to, why_to = await replies._recipient(lead_id, "email")
    if to is None:
        return None, str(why_to)
    name = await store.lead_name(lead_id) or f"lead {lead_id}"
    return {"amount": amount, "to": to, "name": name, "basis": why}, None


async def draft_invoice_dry(lead_id: int) -> ActionResult:
    plan, why = await _draft_refusal(int(lead_id))
    if plan is None:
        return _refused(str(why))
    return ActionResult(True, f"TEST MODE: would draft a ${plan['amount']} invoice for "
                              f"{plan['name']} ({plan['basis']})",
                        evidence={"to": plan["to"], "price": plan["amount"]})


async def draft_invoice(lead_id: int) -> ActionResult:
    lead_id = int(lead_id)
    plan, why = await _draft_refusal(lead_id)
    if plan is None:
        return _refused(str(why))
    text = memo(plan["name"], plan["amount"])
    iid = await store.insert_invoice(lead_id, plan["amount"], plan["to"], text)
    carded = await store.raise_card(
        SEND_ACTION, {"invoice_id": iid},
        f"TEST MODE: Stripe test invoice #{iid}, ${plan['amount']} to {plan['to']} "
        f"for {plan['name']}. No real money moves; live keys are refused in code.",
        plan["name"], text, f"{SEND_ACTION}:{iid}")
    return ActionResult(True, f"TEST MODE invoice {iid} drafted (${plan['amount']})"
                              + ("; card raised" if carded else ""),
                        evidence={"invoice_id": iid, "price": plan["amount"]},
                        rollback_token=str(iid))


async def withdraw_invoice(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not an invoice id")
    row = await store.invoice(int(token))
    if row is None or row["status"] != "drafted":
        return _refused(f"invoice {token} is not a draft")
    await store.update_invoice(int(token), status="withdrawn")
    return ActionResult(True, f"invoice {token} withdrawn")


# ------------------------------------------------------------------- sending

def send_refusal(row: dict[str, Any] | None) -> str | None:
    if row is None:
        return "no such invoice"
    if row.get("status") != "drafted":
        return f"invoice {row.get('id')} is {row.get('status')}, not a draft"
    if int(row.get("amount") or 0) not in PRICES:
        return "the amount is not $500 or $300"
    _secret, why = key()
    return why


async def send_invoice_dry(invoice_id: int) -> ActionResult:
    row = await store.invoice(int(invoice_id))
    why = send_refusal(row)
    if why is not None:
        return _refused(why)
    assert row is not None
    return ActionResult(True, f"TEST MODE: would send Stripe test invoice {invoice_id} "
                              f"(${row['amount']}) to {row['to_address']}",
                        evidence={"to": row["to_address"], "subject": "TEST MODE invoice",
                                  "text": row["memo"], "price": row["amount"]})


async def _customer(row: dict[str, Any], secret: str, base: str
                    ) -> tuple[str | None, str | None]:
    found, why = await call("GET", "/customers", secret, f"{base}-search",
                            params={"email": row["to_address"], "limit": 1})
    if found is None:
        return None, why
    for c in found.get("data") or []:
        if isinstance(c, dict) and c.get("id"):
            return str(c["id"]), None
    made, why = await call("POST", "/customers", secret, f"{base}-customer",
                           form={"email": row["to_address"],
                                 "metadata[atlas_lead]": str(row["lead_id"])})
    if made is None or not made.get("id"):
        return None, why or "Stripe returned no customer id"
    return str(made["id"]), None


async def _send_steps(row: dict[str, Any], secret: str
                      ) -> tuple[dict[str, Any] | None, str | None]:
    base = str(row["idempotency_key"])
    iid = int(row["id"])
    cust, why = await _customer(row, secret, base)
    if cust is None:
        return None, f"customer: {why}"
    await store.update_invoice(iid, stripe_customer=cust)
    inv, why = await call("POST", "/invoices", secret, f"{base}-invoice", form={
        "customer": cust, "collection_method": "send_invoice",
        "days_until_due": DAYS_DUE, "auto_advance": "false",
        "description": row["memo"], "metadata[atlas_invoice]": str(iid)})
    if inv is None or not inv.get("id"):
        return None, f"invoice: {why or 'no invoice id'}"
    sid = str(inv["id"])
    await store.update_invoice(iid, stripe_invoice=sid, livemode=False)
    item, why = await call("POST", "/invoiceitems", secret, f"{base}-item", form={
        "customer": cust, "invoice": sid, "amount": int(row["amount"]) * 100,
        "currency": "usd", "description": "Website build"})
    if item is None:
        return None, f"invoice item: {why}"
    for step in ("finalize", "send"):
        inv, why = await call("POST", f"/invoices/{sid}/{step}", secret,
                              f"{base}-{step}")
        if inv is None:
            return None, f"{step}: {why}"
    return inv, None


async def send_invoice(invoice_id: int) -> ActionResult:
    from ...kernel import idempotency
    row = await store.invoice(int(invoice_id))
    why = send_refusal(row)
    if why is not None:
        return _refused(why)
    assert row is not None
    secret, _ = key()
    assert secret is not None
    ckey = f"inv-{int(invoice_id)}"
    state = await idempotency.claim(SEND_ACTION, ckey)
    if state != idempotency.CLAIMED:
        return ActionResult(False, idempotency.refusal(SEND_ACTION, state))
    sent, why = await _send_steps(row, secret)
    if sent is None:
        reason = _http.redact(str(why))
        inner = reason.partition(": ")[2] or reason      # "<step>: <why>"
        await idempotency.finish(SEND_ACTION, ckey, inner)
        if idempotency.uncertain(inner):
            await store.update_invoice(int(invoice_id), status="failed")
        log.info("bizops invoice %s not sent: %s", invoice_id, reason)
        return ActionResult(False, f"TEST MODE invoice {invoice_id} not sent: {reason}")
    due = sent.get("due_date")
    await store.update_invoice(
        int(invoice_id), status="sent", livemode=False,
        hosted_url=str(sent.get("hosted_invoice_url") or "") or None,
        sent_at=datetime.now(UTC),
        due_at=datetime.fromtimestamp(int(due), UTC) if isinstance(due, int) else None)
    await idempotency.settle(SEND_ACTION, ckey, f"stripe {sent.get('id')}")
    return ActionResult(True, f"TEST MODE: Stripe test invoice {invoice_id} sent to "
                              f"{row['to_address']}",
                        evidence={"stripe_invoice": sent.get("id"), "livemode": False,
                                  "hosted_url": sent.get("hosted_invoice_url"),
                                  "to": row["to_address"]},
                        rollback_token=str(int(invoice_id)))


async def void_invoice(token: str) -> ActionResult:
    """The rollback of a send: void at Stripe, only while unpaid. A paid
    invoice is never voided here -- a refund is money out (Tier 3)."""
    if not str(token).isdigit():
        return _refused("not an invoice id")
    iid = int(token)
    row = await store.invoice(iid)
    if row is None:
        return _refused("no such invoice")
    if not row.get("stripe_invoice"):
        await store.update_invoice(iid, status="withdrawn")
        return ActionResult(True, f"invoice {iid} withdrawn before it reached Stripe")
    secret, why = key()
    if secret is None:
        return _refused(str(why))
    base, sid = str(row["idempotency_key"]), str(row["stripe_invoice"])
    got, why = await call("GET", f"/invoices/{sid}", secret, f"{base}-read")
    if got is None:
        return ActionResult(False, f"invoice {iid} could not be read: {why}")
    status = got.get("status")
    if status == "paid":
        return _refused(f"invoice {iid} is paid; it is not voided (a refund is money "
                        "out, Tier 3, never a rollback)")
    if status == "draft":
        await store.update_invoice(iid, status="void")
        return ActionResult(True, f"invoice {iid} was never finalized; the Stripe "
                                  "test draft stays unsent")
    if status != "open":
        return _refused(f"invoice {iid} is {status} at Stripe; nothing to void")
    done, why = await call("POST", f"/invoices/{sid}/void", secret, f"{base}-void")
    if done is None:
        return ActionResult(False, f"invoice {iid} not voided: {why}")
    await store.update_invoice(iid, status="void")
    return ActionResult(True, f"TEST MODE invoice {iid} voided")


# ------------------------------------------------------------------- polling

async def check_invoice_status() -> Observation:
    """The collector: GET every open invoice; a paid one is marked paid."""
    secret, why = key()
    if secret is None:
        return Observation("invoice_status", Health.UNKNOWN, detail=str(why))
    try:
        rows = await store.open_invoices()
    except Exception as exc:                            # noqa: BLE001
        return Observation("invoice_status", Health.UNKNOWN,
                           detail=f"bizops_invoices unreadable: {type(exc).__name__}")
    now = datetime.now(UTC)
    paid, unread = 0, []
    for row in rows:
        got, why = await call("GET", f"/invoices/{row['stripe_invoice']}", secret,
                              f"{row['idempotency_key']}-poll-{now:%Y%m%d%H%M}")
        if got is None:
            unread.append(f"invoice {row['id']}: {why}")
            continue
        status = got.get("status")
        if status == "paid":
            await store.update_invoice(int(row["id"]), status="paid", paid_at=now,
                                       polled_at=now)
            paid += 1
        elif status in ("void", "uncollectible"):
            await store.update_invoice(int(row["id"]), status="void", polled_at=now)
        else:
            await store.update_invoice(int(row["id"]), polled_at=now)
    if rows and len(unread) == len(rows):
        return Observation("invoice_status", Health.UNKNOWN,
                           detail="no open invoice could be read: " + unread[0])
    still = len(rows) - paid
    detail = (f"{still} open test invoice(s), {paid} newly paid "
              f"(read {now:%Y-%m-%d %H:%M} UTC)")
    if unread:
        detail += f"; unread: {'; '.join(unread[:3])}"
    return Observation("invoice_status", Health.DEGRADED if unread else Health.OK,
                       float(still), "open invoices", detail,
                       evidence={"read_at": now.isoformat(), "paid": paid})


# ----------------------------------------------------------------- reminders

def _reminder_body(name: str, amount: int, url: str) -> str:
    from ..agency.replies import _email_finish
    return _email_finish(
        f"Hi {name},\n\nA friendly reminder that the invoice for your website "
        f"(${amount}) is still open. You can pay it here: {url}\n\n"
        "If you have already paid, please ignore this note.")


async def _remind_refusal(invoice_id: int, *, sent_only: bool
                          ) -> tuple[dict[str, Any] | None, str | None]:
    row = await store.invoice(int(invoice_id))
    if row is None:
        return None, "no such invoice"
    if row.get("status") != "sent":
        return None, f"invoice {invoice_id} is {row.get('status')}; no reminder"
    if not row.get("hosted_url"):
        return None, f"invoice {invoice_id} has no payment link"
    last = await store.last_reminder_at(int(invoice_id), sent_only=sent_only)
    if last is not None and datetime.now(UTC) - last < REMIND_EVERY:
        return None, f"invoice {invoice_id} had a reminder in the last 7 days"
    return row, None


async def draft_payment_reminder_dry(invoice_id: int) -> ActionResult:
    row, why = await _remind_refusal(int(invoice_id), sent_only=False)
    if row is None:
        return _refused(str(why))
    return ActionResult(True, f"would draft a payment reminder for invoice {invoice_id}",
                        evidence={"to": row["to_address"]})


async def draft_payment_reminder(invoice_id: int) -> ActionResult:
    row, why = await _remind_refusal(int(invoice_id), sent_only=False)
    if row is None:
        return _refused(str(why))
    name = await store.lead_name(int(row["lead_id"])) or "there"
    subject = "A reminder about your website invoice"
    body = _reminder_body(name, int(row["amount"]), str(row["hosted_url"]))
    rid = await store.insert_reminder(int(invoice_id), str(row["to_address"]), subject,
                                      body)
    carded = await store.raise_card(
        "bizops.send_payment_reminder", {"reminder_id": rid},
        f"Payment reminder for TEST MODE invoice #{invoice_id} to {row['to_address']}",
        name, body, f"bizops.send_payment_reminder:{rid}")
    return ActionResult(True, f"reminder {rid} drafted" + ("; card raised" if carded
                                                            else ""),
                        evidence={"reminder_id": rid}, rollback_token=str(rid))


async def withdraw_reminder(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a reminder id")
    await store.set_reminder(int(token), "withdrawn")
    return ActionResult(True, f"reminder {token} withdrawn")


async def send_payment_reminder_dry(reminder_id: int) -> ActionResult:
    from .mail import guard
    rem = await store.reminder(int(reminder_id))
    if rem is None or rem.get("status") != "drafted":
        return _refused(f"reminder {reminder_id} is not a draft")
    row, why = await _remind_refusal(int(rem["invoice_id"]), sent_only=True)
    if row is None:
        return _refused(str(why))
    why = await guard(int(row["lead_id"]), str(rem["to_address"]), str(rem["subject"]),
                      str(rem["body"]))
    if why is not None:
        return _refused(why)
    return ActionResult(True, f"would email reminder {reminder_id} to {rem['to_address']}",
                        evidence={"to": rem["to_address"], "subject": rem["subject"],
                                  "text": rem["body"]})


async def send_payment_reminder(reminder_id: int) -> ActionResult:
    from .mail import guard, send
    rem = await store.reminder(int(reminder_id))
    if rem is None or rem.get("status") != "drafted":
        return _refused(f"reminder {reminder_id} is not a draft")
    row, why = await _remind_refusal(int(rem["invoice_id"]), sent_only=True)
    if row is None:
        return _refused(str(why))
    to, subject, body = str(rem["to_address"]), str(rem["subject"]), str(rem["body"])
    why = await guard(int(row["lead_id"]), to, subject, body)
    if why is not None:
        return _refused(why)
    got = await send("bizops.send_payment_reminder", f"rem-{int(reminder_id)}", to,
                     subject, body)
    if got.ok:
        await store.set_reminder(int(reminder_id), "sent")
    return got
