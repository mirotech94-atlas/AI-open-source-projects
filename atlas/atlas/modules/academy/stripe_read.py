"""Read one ATLAS Academy checkout from Stripe. A read, like a collector.

    GET /v1/checkout/sessions/{id}
        ?expand[]=line_items&expand[]=payment_intent.latest_charge

with `ACADEMY_STRIPE_READ_KEY`, a restricted key on the ATLAS Academy
Stripe account with READ on Checkout Sessions, PaymentIntents and Charges
and nothing else. It cannot write anything, so it cannot refund, charge or
change a price even if it leaked.

`read_session` never guesses. Every answer is a `Verdict` whose status says
exactly what Stripe said, or why Stripe could not be asked:

    paid           paid, every line item's price is in the catalog, not
                   fully refunded, not disputed
    unpaid         Stripe says the session is not paid
    refunded       the charge was refunded in full
    disputed       the charge is disputed
    expired        older than the catalog's access window (only when set)
    unknown_price  paid, but a line item's price is not in the catalog
    not_found      Stripe has no such session (for this key's account/mode)
    unconfigured   the read key is not set -- nothing was asked
    refused        Stripe refused the key (401/403): its message is kept,
                   the key never is
    unreachable    Stripe could not be reached or answered an error

Only `paid` grants access. Everything else is shown to the buyer as it is.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from .catalog import Catalog, Offer

log = logging.getLogger("atlas.academy.stripe")

API = "https://api.stripe.com/v1"
KEY_ENV = "ACADEMY_STRIPE_READ_KEY"
TIMEOUT_S = 15.0
SESSION_RE = re.compile(r"^cs_(?:live|test)_[A-Za-z0-9]{10,200}$")
EXPAND = ("line_items", "payment_intent.latest_charge")


@dataclass(frozen=True)
class Verdict:
    status: str
    session_id: str
    detail: str = ""
    offers: tuple[Offer, ...] = ()
    price_ids: tuple[str, ...] = ()
    amount_minor: int | None = None
    currency: str = ""
    refunded_minor: int = 0
    email: str = ""
    payment_intent: str = ""
    livemode: bool = True
    created: datetime | None = None
    read_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def paid(self) -> bool:
        return self.status == "paid"

    @property
    def recordable(self) -> bool:
        """A payment Stripe confirmed, whatever became of it since."""
        return self.status in ("paid", "refunded", "disputed", "unknown_price", "expired")

    def file_keys(self) -> set[str]:
        return {f.key for o in self.offers for f in o.files}

    def amount_text(self) -> str:
        if self.amount_minor is None:
            return "amount not read"
        cur = self.currency.upper() or "?"
        return f"{self.amount_minor / 100:,.2f} {cur}"


def read_key() -> str:
    return os.getenv(KEY_ENV, "").strip()


def valid_session_id(session_id: object) -> bool:
    return isinstance(session_id, str) and bool(SESSION_RE.match(session_id))


def _stripe_message(resp: httpx.Response) -> str:
    """Stripe's own error message (it names a missing permission), with any
    key-looking token cut out. Never the key."""
    try:
        msg = str(resp.json().get("error", {}).get("message") or "")
    except ValueError:
        msg = ""
    msg = re.sub(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9*]+", "<key>", msg)
    return msg[:300]


async def read_session(session_id: str, cat: Catalog, *,
                       now: datetime | None = None) -> Verdict:
    now = now or datetime.now(UTC)
    if not valid_session_id(session_id):
        return Verdict("not_found", session_id, "not a checkout session id", read_at=now)
    key = read_key()
    if not key:
        return Verdict("unconfigured", session_id,
                       f"{KEY_ENV} is not set, so the payment cannot be checked",
                       read_at=now)
    params = [("expand[]", e) for e in EXPAND]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.get(f"{API}/checkout/sessions/{session_id}",
                                    params=params,
                                    headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        log.warning("academy: Stripe unreachable (%s)", type(exc).__name__)
        return Verdict("unreachable", session_id,
                       f"Stripe could not be reached ({type(exc).__name__})", read_at=now)
    if resp.status_code == 404:
        return Verdict("not_found", session_id, "Stripe has no such checkout",
                       read_at=now)
    if resp.status_code in (401, 403):
        why = _stripe_message(resp)
        log.warning("academy: Stripe refused the read key (%s): %s",
                    resp.status_code, why)
        return Verdict("refused", session_id,
                       f"Stripe refused the read key ({resp.status_code}): {why}",
                       read_at=now)
    if resp.status_code != 200:
        return Verdict("unreachable", session_id,
                       f"Stripe answered {resp.status_code}", read_at=now)
    try:
        s = resp.json()
    except ValueError:
        return Verdict("unreachable", session_id, "Stripe's answer was not JSON",
                       read_at=now)
    return judge(s, cat, now=now)


def judge(s: dict, cat: Catalog, *, now: datetime | None = None) -> Verdict:
    """The verdict on one expanded Checkout Session object. Pure."""
    now = now or datetime.now(UTC)
    session_id = str(s.get("id") or "")
    details = s.get("customer_details") or {}
    email = str(details.get("email") or s.get("customer_email") or "")
    created = None
    if isinstance(s.get("created"), int):
        created = datetime.fromtimestamp(s["created"], UTC)
    pi = s.get("payment_intent")
    pi_id = str(pi.get("id") if isinstance(pi, dict) else (pi or ""))
    amount = s.get("amount_total")
    base = {
        "session_id": session_id, "email": email, "payment_intent": pi_id,
        "amount_minor": int(amount) if isinstance(amount, int) else None,
        "currency": str(s.get("currency") or ""), "livemode": bool(s.get("livemode")),
        "created": created, "read_at": now,
    }
    if s.get("payment_status") != "paid":
        return Verdict("unpaid", detail=f"Stripe says payment_status is "
                       f"{s.get('payment_status')!r}", **base)

    items = s.get("line_items") or {}
    rows = items.get("data") if isinstance(items, dict) else None
    if not isinstance(rows, list) or items.get("has_more"):
        return Verdict("unreachable", detail="the checkout's line items were not "
                       "all read", **base)
    price_ids = tuple(str((r.get("price") or {}).get("id") or "") for r in rows)
    offers: list[Offer] = []
    unknown = [p for p in price_ids if cat.offer_for_price(p) is None]
    for p in price_ids:
        o = cat.offer_for_price(p)
        if o is not None and o not in offers:
            offers.append(o)
    base["price_ids"] = price_ids
    if unknown or not price_ids:
        return Verdict("unknown_price", detail="a line item's price is not in the "
                       "catalog: " + (", ".join(unknown) or "no line items"), **base)

    if not isinstance(pi, dict):
        return Verdict("unreachable", offers=tuple(offers),
                       detail="the payment could not be read to check for refunds",
                       **base)
    charge = pi.get("latest_charge")
    if pi.get("status") != "succeeded" or not isinstance(charge, dict):
        return Verdict("unpaid", offers=tuple(offers),
                       detail=f"the payment is {pi.get('status')!r}", **base)
    refunded = int(charge.get("amount_refunded") or 0)
    base["refunded_minor"] = refunded
    if charge.get("refunded"):
        return Verdict("refunded", offers=tuple(offers),
                       detail="the payment was refunded", **base)
    if charge.get("disputed"):
        return Verdict("disputed", offers=tuple(offers),
                       detail="the payment is disputed", **base)
    days = cat.store.get("access_valid_days")
    if isinstance(days, int) and created is not None \
            and (now - created).days >= days:
        return Verdict("expired", offers=tuple(offers),
                       detail=f"access lasts {days} days from purchase", **base)
    return Verdict("paid", offers=tuple(offers), **base)
