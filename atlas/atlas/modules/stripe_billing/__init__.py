"""Stripe — Guardian and flycheap.

Read the note on keys before wiring this up, because it is the difference
between a useful monitor and a liability.

ATLAS gets a RESTRICTED Stripe key, not a secret key. Stripe supports
per-resource scoping; grant read on charges, invoices, subscriptions,
customers, disputes, payouts and events, and write on NOTHING by default.

Money leaving an account is Tier 3 in ATLAS and cannot be unlocked at runtime:
refunds, payouts, transfers, price changes and cancellations are all in the
permanent deny-list. That is deliberate. A system that reads your email and
your web pages — both written by other people — must not be one instruction
away from issuing a refund.

What it does instead is catch the failures that quietly cost money:
  * failed payments and past-due subscriptions before the customer churns
  * disputes, which have a response deadline measured in days
  * webhook endpoints failing, which silently breaks provisioning
  * cards expiring in the next 30 days
  * MRR and churn trend per business
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx

from ...kernel import idempotency
from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from .._http import redact

API = "https://api.stripe.com/v1"
BUSINESSES = ("guardian", "flycheap")


# Each business is a separate Stripe account, with its own key. They are
# not interchangeable: the Guardian key reads acct_1UH58V... (guarder.io),
# and flycheap is a different account entirely.
ENV_FOR = {b: f"STRIPE_{b.upper()}_KEY" for b in BUSINESSES}


def _key(business: str) -> str:
    return os.getenv(ENV_FOR[business], "").strip()


class NoKey(Exception):
    """This business's key is not set. Not the same as Stripe refusing it."""


async def _get(business: str, path: str, **params):
    key = _key(business)
    if not key:
        raise NoKey(
            f"no {ENV_FOR[business]} set. Create a RESTRICTED key (rk_live_) "
            f"in that account's dashboard under Developers -> API keys -> "
            f"Create restricted key, with read permissions only. Each "
            f"business is a separate Stripe account and needs its own.")
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.get(f"{API}/{path}", auth=(key, ""), params=params)
        r.raise_for_status()
        return r.json()


class Unreadable(Exception):
    """Stripe answered, but not with a list ATLAS can count."""


def _rows(payload, path: str) -> list:
    """The `data` array of a Stripe list response, or `Unreadable`.

    `payload.get("data", [])` turned a response with no `data` list into
    zero rows, and zero rows into health: "0 disputes", "0 failed invoices"
    (RA-E1). A well-formed empty list is still a real zero.
    """
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        got = (", ".join(sorted(payload)[:6]) if isinstance(payload, dict)
               else type(payload).__name__)
        raise Unreadable(f"Stripe {path} returned no data list (got: {got}), "
                         f"so nothing was counted")
    return rows


def _make_check(business: str, fn):
    async def _wrapped() -> Observation:
        return await fn(business)
    return _wrapped


async def key_scope(business: str) -> Observation:
    """Is this a restricted key, and whose account is it?

    Two things go wrong here and both are silent. A full secret key
    (`sk_live_`) can move money, and ATLAS must never hold one — money out is
    Tier 3 with no override, so the capability should not exist at all rather
    than be denied in code. And the two businesses are separate Stripe
    accounts, so a key pasted into the wrong variable reads the wrong
    business's revenue and reports it under the wrong name.
    """
    name = f"{business}_key"
    key = _key(business)
    if not key:
        return Observation(
            name, Health.UNKNOWN,
            detail=f"no {ENV_FOR[business]} set. Create a RESTRICTED key "
                   f"(rk_live_) in that account under Developers -> API keys, "
                   f"with read permissions only.")

    if key.startswith(("sk_live_", "sk_test_")):
        return Observation(
            name, Health.DEGRADED, None, None,
            f"{ENV_FOR[business]} is a FULL secret key, not a restricted one. "
            f"It can create charges, refunds and payouts. Replace it with an "
            f"rk_live_ restricted key with read permissions only — money out "
            f"is permanently denied here and the capability should not exist.")

    try:
        account = await _get(business, "account")
    except NoKey as exc:
        return Observation(name, Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation(name, Health.UNKNOWN, detail=redact(exc))

    profile = account.get("business_profile") or {}
    label = str(profile.get("name") or profile.get("url") or "?")
    # The account's own name should match the variable it was put in.
    matches = business.lower() in label.lower().replace(" ", "")
    return Observation(
        name, Health.OK if matches else Health.DEGRADED, None, None,
        f"restricted key for {label} ({account.get('id')})"
        + ("" if matches else
           f" — but it is in {ENV_FOR[business]}, so {business} figures would "
           f"be reported from the wrong account. Check which key went where."),
        evidence={"account": account.get("id"), "name": label})


async def failed_payments(business: str) -> Observation:
    try:
        data = await _get(business, "invoices", status="open", limit=100)
        overdue = [i for i in _rows(data, "/invoices") if isinstance(i, dict)
                   and i.get("attempt_count", 0) > 0]
        amount = sum(i.get("amount_due", 0) for i in overdue) / 100
        return Observation(
            f"{business}_failed_payments",
            Health.FAILING if len(overdue) > 3 else
            Health.DEGRADED if overdue else Health.OK,
            float(len(overdue)), "invoices",
            f"{len(overdue)} failed invoices worth ${amount:,.2f}",
            evidence={"customers": [i.get("customer_email") for i in overdue[:10]]},
        )
    except NoKey as exc:
        return Observation(
            f"{business}_failed_payments", Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation(
            f"{business}_failed_payments", Health.UNKNOWN, detail=redact(exc))


async def disputes(business: str) -> Observation:
    """Disputes have a hard response deadline. Missing it loses by default."""
    try:
        data = await _get(business, "disputes", limit=50)
        open_ones = [d for d in _rows(data, "/disputes") if isinstance(d, dict)
                     and d.get("status") in ("warning_needs_response",
                                             "needs_response")]
        return Observation(
            f"{business}_disputes",
            Health.FAILING if open_ones else Health.OK,
            float(len(open_ones)), "disputes",
            f"{len(open_ones)} dispute(s) awaiting a response",
            evidence={"ids": [d["id"] for d in open_ones]},
        )
    except NoKey as exc:
        return Observation(
            f"{business}_disputes", Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation(
            f"{business}_disputes", Health.UNKNOWN, detail=redact(exc))


async def webhook_health(business: str) -> Observation:
    """A failing webhook endpoint means a paying customer silently never got
    access. Nothing about the dashboard makes this obvious."""
    try:
        data = await _get(business, "events", limit=100, type="invoice.payment_succeeded")
        pending = sum(1 for e in _rows(data, "/events") if isinstance(e, dict)
                      and e.get("pending_webhooks", 0) > 0)
        return Observation(
            f"{business}_webhooks",
            Health.FAILING if pending > 5 else
            Health.DEGRADED if pending else Health.OK,
            float(pending), "events",
            f"{pending} events with undelivered webhooks",
        )
    except NoKey as exc:
        return Observation(
            f"{business}_webhooks", Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation(
            f"{business}_webhooks", Health.UNKNOWN, detail=redact(exc))


async def mrr(business: str) -> Observation:
    try:
        data = await _get(business, "subscriptions", status="active", limit=100)
        subs = _rows(data, "/subscriptions")
        total = 0
        currencies: set[str] = set()
        for sub in subs:
            for item in sub.get("items", {}).get("data", []):
                price = item.get("price", {})
                currencies.add(str(price.get("currency") or "usd").lower())
                amount = price.get("unit_amount", 0) * item.get("quantity", 1)
                interval = price.get("recurring", {}).get("interval", "month")
                total += amount / 12 if interval == "year" else amount
        if len(currencies) > 1:
            # Cents of two currencies do not add up to one figure (plan D14).
            # No exchange rate is invented: the reading says what it saw.
            return Observation(
                f"{business}_mrr", Health.UNKNOWN, None, None,
                f"subscriptions are billed in {', '.join(sorted(currencies))}; "
                f"they are not summed into one MRR without an exchange rate",
                evidence={"currencies": sorted(currencies),
                          "subscriptions": len(subs)})
        unit = (next(iter(currencies)) if currencies else "usd").upper()
        return Observation(f"{business}_mrr", Health.OK, total / 100, unit,
                           f"{total/100:,.2f} {unit} MRR across "
                           f"{len(subs)} subscriptions")
    except NoKey as exc:
        return Observation(
            f"{business}_mrr", Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation(
            f"{business}_mrr", Health.UNKNOWN, detail=redact(exc))


async def expiring_cards(business: str) -> Observation:
    """Cards about to expire on active subscriptions.

    Two things were wrong. `expand=["data.default_source"]` serialises as
    `expand=...`, but Stripe requires the bracketed array form `expand[]=...`
    and answers 400 "Invalid array" otherwise -- so this check had never
    returned a measurement. And `default_source` is the legacy Sources field;
    a customer paying by a modern PaymentMethod has none, so even a
    well-formed query would have counted zero forever.

    The current field is invoice_settings.default_payment_method.
    """
    try:
        data = await _get(business, "customers", limit=100,
                          **{"expand[]": "data.invoice_settings."
                                         "default_payment_method"})
        customers = _rows(data, "/customers")
    except NoKey as exc:
        return Observation(f"{business}_expiring_cards", Health.UNKNOWN,
                           detail=str(exc))
    except Exception as exc:                          # noqa: BLE001
        return Observation(f"{business}_expiring_cards", Health.UNKNOWN,
                           detail=redact(exc))

    today = datetime.now(UTC).date()
    # "Expiring soon" is this month or next: a card that dies mid-cycle
    # fails the renewal silently, and the customer hears about it from a
    # failed charge rather than from us.
    horizon = [(today.year, today.month)]
    horizon.append((today.year + 1, 1) if today.month == 12
                   else (today.year, today.month + 1))

    expiring, examined = [], 0
    for cust in customers:
        if not isinstance(cust, dict):
            continue
        settings = cust.get("invoice_settings") or {}
        method = settings.get("default_payment_method")
        card = (method or {}).get("card") if isinstance(method, dict) else None
        if not card:
            # Legacy Sources, or no stored card. Not an expiry.
            continue
        examined += 1
        year, month = card.get("exp_year"), card.get("exp_month")
        if isinstance(year, int) and isinstance(month, int) \
                and (year, month) in horizon:
            expiring.append(str(cust.get("email") or cust.get("id")))

    if not examined and customers:
        # Customers exist and not one card was read off them. A card on a
        # legacy Source, on the subscription instead of the customer, or a
        # payment method Stripe did not expand (it arrives as a bare id)
        # all land here -- the very failure the docstring above describes.
        # "Nothing to expire" was a guess (RA-E1).
        return Observation(
            f"{business}_expiring_cards", Health.UNKNOWN, None, None,
            f"{len(customers)} customer(s) read and no card could be read on "
            f"any of them (invoice_settings.default_payment_method), so "
            f"expiry is not measured")
    if not examined:
        return Observation(
            f"{business}_expiring_cards", Health.OK, 0.0, "cards",
            "no customers on this account — nothing to expire")

    return Observation(
        f"{business}_expiring_cards",
        Health.DEGRADED if expiring else Health.OK,
        float(len(expiring)), "cards",
        f"{len(expiring)} of {examined} stored card(s) expire this month or "
        f"next" + (f": {', '.join(expiring[:6])}" if expiring else ""),
        evidence={"expiring": expiring[:20], "examined": examined})


def retry_key(invoice_id: str, run: str) -> str:
    """Stripe's Idempotency-Key for paying `invoice_id` in `run`. Never the
    time: the same run retried must send the same key."""
    return "atlas-pay-" + idempotency.key_for(
        "stripe.retry_invoice", invoice_id, run)


async def retry_invoice(business: str, invoice_id: str) -> ActionResult:
    """Retry collection on a failed invoice. This charges a card the customer
    already agreed to — it is dunning, not a new purchase — so it is Tier 2,
    not Tier 3. It still requires approval every time: a charge cannot be
    undone, and `contracts.py` refuses an irreversible Tier 1.

    Miro, 2026-09-28: it always charges, and the customer always gets a
    receipt -- once a retry pays, the charge's receipt is emailed
    (`_send_receipt`), once per charge.

    Idempotent per run (RA-B7): the `Idempotency-Key` is derived from the
    invoice and the run -- the approved card, `proposal:<id>` -- so a retry
    of the same run (a timeout, a re-dispatch) is answered by Stripe with
    the first attempt's result instead of a second charge. A new card is a
    new run and may retry the invoice again."""
    run = idempotency.run_key("stripe.retry_invoice",
                              {"business": business, "invoice_id": invoice_id})
    key = retry_key(invoice_id, run)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/invoices/{invoice_id}/pay",
                         auth=(_key(business), ""),
                         headers={"Idempotency-Key": key})
    ok = r.status_code == 200
    if not ok:
        return ActionResult(False, f"retry failed: {r.text[:300]}")
    try:
        invoice = r.json()
    except ValueError:
        invoice = {}
    status = str(invoice.get("status") or r.text)[:300]
    if status != "paid":
        # Collected nothing (still open, or Stripe says otherwise): no
        # charge succeeded, so there is nothing to give a receipt for.
        return ActionResult(True, status)
    receipt = await _send_receipt(business, invoice)
    return ActionResult(True, f"paid; {receipt}",
                        evidence={"invoice": invoice_id, "receipt": receipt})


async def _charge_of(business: str, invoice: dict) -> str | None:
    """The charge that paid `invoice`: its `charge` field, or the payment
    intent's `latest_charge` on API versions that dropped the former."""
    charge = invoice.get("charge")
    if isinstance(charge, dict):
        charge = charge.get("id")
    if charge:
        return str(charge)
    intent = invoice.get("payment_intent")
    if isinstance(intent, dict):
        intent = intent.get("id")
    if not intent:
        return None
    pi = await _get(business, f"payment_intents/{intent}")
    latest = pi.get("latest_charge")
    return str(latest.get("id") if isinstance(latest, dict) else latest or "") or None


async def _send_receipt(business: str, invoice: dict) -> str:
    """Miro, 2026-09-28: a customer charged by a retry always gets a receipt.

    Stripe emails the receipt when a charge's `receipt_email` is set. Sent
    once per charge: a charge that already carries `receipt_email` or a
    `receipt_number` has had its receipt, and the update carries an
    Idempotency-Key from the charge id, so a retried run cannot send a
    second one. Never raises -- the payment already happened; what went
    wrong with the receipt is said in the detail instead."""
    from .._http import explain
    try:
        charge_id = await _charge_of(business, invoice)
        if charge_id is None:
            return "no receipt: the paid invoice names no charge"
        charge = await _get(business, f"charges/{charge_id}")
        if charge.get("receipt_email") or charge.get("receipt_number"):
            return f"receipt already sent for {charge_id}"
        customer = charge.get("customer") or invoice.get("customer")
        if isinstance(customer, dict):
            customer = customer.get("id")
        email = ""
        if customer:
            email = str((await _get(business, f"customers/{customer}"))
                        .get("email") or "").strip()
        email = email or str(invoice.get("customer_email") or "").strip()
        if not email:
            return "no receipt: customer has no email"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                f"{API}/charges/{charge_id}", auth=(_key(business), ""),
                data={"receipt_email": email},
                headers={"Idempotency-Key": receipt_key(charge_id)})
        r.raise_for_status()
    except Exception as exc:                            # noqa: BLE001
        return f"receipt not sent: {explain(exc)}"
    return f"receipt emailed for {charge_id}"


def receipt_key(charge_id: str) -> str:
    """One receipt per charge, whichever run asks."""
    return "atlas-receipt-" + idempotency.key_for("stripe.receipt", charge_id)


async def retry_dry(business: str, invoice_id: str) -> ActionResult:
    try:
        inv = await _get(business, f"invoices/{invoice_id}")
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"cannot read invoice: {exc}")
    if inv.get("status") != "open":
        return ActionResult(False, f"invoice is {inv.get('status')}, not open")
    return ActionResult(
        True,
        f"would retry ${inv['amount_due']/100:,.2f} for "
        f"{inv.get('customer_email', 'unknown')} (attempt "
        f"{inv.get('attempt_count', 0) + 1})")


class StripeModule:
    name = "stripe"
    title = "Payments (Stripe)"
    # Not gated: each business is a separate account, and one key arriving
    # before the other should light up that business rather than keep both
    # dark. A missing key reports UNKNOWN naming the variable.
    requires_env = ()
    optional_env = ("STRIPE_GUARDIAN_KEY", "STRIPE_FLYCHEAP_KEY")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        out = []
        for biz in BUSINESSES:
            out += [
                Check(f"{biz}_key", _make_check(biz, key_scope),
                      timedelta(hours=12), Severity.HIGH,
                      f"{biz}: the Stripe key is restricted and is the right "
                      f"account", baseline_sigma=None),
                Check(f"{biz}_failed_payments", _make_check(biz, failed_payments),
                      timedelta(hours=1), Severity.HIGH,
                      f"{biz}: invoices that failed to collect"),
                Check(f"{biz}_disputes", _make_check(biz, disputes),
                      timedelta(hours=2), Severity.CRITICAL,
                      f"{biz}: disputes with a response deadline",
                      baseline_sigma=None),
                Check(f"{biz}_webhooks", _make_check(biz, webhook_health),
                      timedelta(minutes=30), Severity.HIGH,
                      f"{biz}: webhook delivery"),
                Check(f"{biz}_mrr", _make_check(biz, mrr),
                      timedelta(hours=6), Severity.INFO, f"{biz}: MRR"),
                Check(f"{biz}_expiring_cards", _make_check(biz, expiring_cards),
                      timedelta(days=1), Severity.NORMAL,
                      f"{biz}: cards expiring soon", baseline_sigma=None),
            ]
        return tuple(out)

    def actions(self):
        return (
            Action(
                name="stripe.retry_invoice",
                tier=Tier.ASK,
                summary="Retry collection on a failed invoice (dunning)",
                run=retry_invoice, dry_run=retry_dry,
                rollback=None, irreversible=True,
                params={"business": "guardian or flycheap",
                        "invoice_id": "Stripe invoice id (in_...)"},
                max_per_hour=5,
            ),
        )

    async def signals(self):
        out = []
        for biz in BUSINESSES:
            d = await disputes(biz)
            if d.value:
                out.append(Signal(
                    title=f"{biz}: {int(d.value)} dispute(s) need a response",
                    body="Stripe disputes expire by default if unanswered. "
                         "Evidence must be submitted before the deadline.",
                    severity=Severity.CRITICAL))
            f = await failed_payments(biz)
            if f.value and f.value >= 2:
                out.append(Signal(
                    title=f"{biz}: {int(f.value)} failed payments",
                    body=f.detail + "\n\nI can retry collection on each, "
                         "or draft a card-update email.",
                    severity=Severity.HIGH, suggests="stripe.retry_invoice"))
        return tuple(out)

    def brief(self) -> str:
        return "Stripe: MRR, failed payments, disputes"


MODULE = StripeModule()
