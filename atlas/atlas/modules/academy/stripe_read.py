"""Read one ATLAS Academy checkout from Stripe. A read, like a collector.

    GET /v1/checkout/sessions/{id}
        ?expand[]=line_items&expand[]=payment_intent.latest_charge

with `ACADEMY_STRIPE_READ_KEY`, a restricted key on the ATLAS Academy
Stripe account with READ on Checkout Sessions, PaymentIntents, Charges and
Disputes (since 2026-10-16) and nothing else. It cannot write anything, so it cannot refund, charge or
change a price even if it leaked.

`read_session` never guesses. Every answer is a `Verdict` whose status says
exactly what Stripe said, or why Stripe could not be asked:

    paid           paid, every line item's price is in the catalog, not
                   fully refunded, not disputed
    unpaid         Stripe says the session is not paid
    refunded       the charge was refunded in full
    disputed       the charge is disputed, and not every dispute on it is
                   won or closed (GET /v1/disputes?charge=; a won dispute
                   restores access, Miro 2026-10-16)
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

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import httpx

from .catalog import Catalog, Offer

log = logging.getLogger("atlas.academy.stripe")

API = "https://api.stripe.com/v1"
KEY_ENV = "ACADEMY_STRIPE_READ_KEY"
TIMEOUT_S = 15.0
SESSION_RE = re.compile(r"^cs_(?:live|test)_[A-Za-z0-9]{10,200}$")
EXPAND = ("line_items", "payment_intent.latest_charge")
# Tests swap in an httpx.MockTransport; production leaves it None (the network).
_transport: httpx.AsyncBaseTransport | None = None


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
    invoice: str = ""
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
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=_transport) as client:
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
    v = judge(s, cat, now=now)
    if v.status == "disputed":
        v = await _settle_disputes(s, cat, v, key, now)
    return v


# Miro, 2026-10-16: a won or prevented dispute restores access. Stripe
# keeps a charge's `disputed` flag true after its dispute closes, so the
# disputes themselves are read (GET /v1/disputes?charge=,
# docs.stripe.com/api/disputes/list and /object, read 2026-10-16). Only these
# statuses are settled in the store's favour (`prevented`: stopped before it
# became a formal chargeback); anything else -- needs_response, under_review,
# lost, the warning_ states still open -- stays disputed.
SETTLED_DISPUTES = frozenset({"won", "warning_closed", "prevented"})


async def _settle_disputes(s: dict, cat: Catalog, v: Verdict, key: str,
                           now: datetime) -> Verdict:
    """`v` (disputed) as it stands once the charge's disputes are read: paid
    again (or whatever else judge says) only when every one is won or
    closed; a read that fails says so and is never paid."""
    pi = s.get("payment_intent")
    charge = pi.get("latest_charge") if isinstance(pi, dict) else None
    charge_id = str(charge.get("id") or "") if isinstance(charge, dict) else ""
    if not charge_id.startswith(("ch_", "py_")):
        return v
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=_transport) as client:
            resp = await client.get(f"{API}/disputes",
                                    params=[("charge", charge_id), ("limit", "100")],
                                    headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        log.warning("academy: Stripe unreachable reading disputes (%s)",
                    type(exc).__name__)
        return replace(v, status="unreachable",
                       detail="the payment's dispute could not be read from Stripe")
    if resp.status_code in (401, 403):
        why = _stripe_message(resp)
        log.warning("academy: Stripe refused the dispute read (%s): %s",
                    resp.status_code, why)
        return replace(v, status="refused", detail=f"Stripe refused the dispute read "
                       f"({resp.status_code}): {why}")
    try:
        body = resp.json() if resp.status_code == 200 else None
    except ValueError:
        body = None
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return replace(v, status="unreachable",
                       detail=f"Stripe's dispute list was not read ({resp.status_code})")
    statuses = [str(d.get("status") or "") for d in rows if isinstance(d, dict)]
    if not statuses or body.get("has_more") \
            or any(st not in SETTLED_DISPUTES for st in statuses):
        return replace(v, detail="the payment is disputed: " + (", ".join(statuses)
                                                                 or "no dispute listed"))
    return judge(s, cat, now=now, disputes_settled=True)


def judge(s: dict, cat: Catalog, *, now: datetime | None = None,
          disputes_settled: bool = False) -> Verdict:
    """The verdict on one expanded Checkout Session object. Pure.
    `disputes_settled`: every dispute on the charge was read as won or
    closed (`_settle_disputes`), so the charge's `disputed` flag no longer
    pauses access."""
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
        "invoice": str(s.get("invoice") or ""),
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
    if charge.get("disputed") and not disputes_settled:
        return Verdict("disputed", offers=tuple(offers),
                       detail="the payment is disputed", **base)
    days = cat.store.get("access_valid_days")
    if isinstance(days, int) and created is not None \
            and (now - created).days >= days:
        return Verdict("expired", offers=tuple(offers),
                       detail=f"access lasts {days} days from purchase", **base)
    return Verdict("paid", offers=tuple(offers), **base)


# ------------------------------------------------------------------ recovery
#
# A buyer who lost their access page proves the purchase is theirs with two
# things from their own inbox: the address Stripe's receipt went to and the
# receipt number printed on it. Neither alone is enough.
#
# Stripe's search cannot do this. Charges search has no email or
# receipt_number field, and Customers search misses guest checkouts (a
# Payment Link creates a Customer only "if_required"). What does work is
# the Checkout Sessions LIST, filtered by `customer_details[email]`, with
# each session's charge expanded so its `receipt_number` can be compared
# here, in code (docs.stripe.com/search, /api/checkout/sessions/list,
# /api/charges/object, read 2026-10-16). GET only, with the same read key.
#
# The caller must not be able to tell "no such buyer" from "wrong receipt"
# by what happens: `find_by_receipt` makes the same two list requests on
# every path, malformed input included (it asks about a sentinel address
# that can never match). Only an address with more than 100 complete
# checkouts makes more (up to 3 pages per form), and every answer waits out
# the API's response-time floor.

RECOVER_EXPAND = ("data.payment_intent.latest_charge",)
# Up to 3 pages of 100 checkouts per address form; older ones go to Contact.
RECOVER_MAX_PAGES = 3
SENTINEL_EMAIL = "no-buyer@recover.invalid"
EMAIL_MAX = 254
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
                       r"\.[A-Za-z]{2,24}$")
# Stripe prints "Receipt #1234-5678"; accept it with or without the "#",
# spaces and case. Digits, letters and up to three dashes, nothing else.
_RECEIPT_RE = re.compile(r"^[0-9A-Z]{3,12}(?:-[0-9A-Z]{3,12}){0,3}$")
PI_RE = re.compile(r"^pi_[A-Za-z0-9]{8,200}$")


def normalise_email(raw: object) -> str:
    """The address lowercased and trimmed, or "" when it is not one."""
    if not isinstance(raw, str):
        return ""
    email = raw.strip().lower()
    if len(email) > EMAIL_MAX or not _EMAIL_RE.fullmatch(email):
        return ""
    return email


def normalise_receipt(raw: object) -> str:
    """"#1234-5678", " 1234-5678 " and "receipt #1234-5678" all become
    "1234-5678"; anything else becomes ""."""
    if not isinstance(raw, str) or len(raw) > 64:
        return ""
    text = re.sub(r"\s+", "", raw).upper()
    text = text.removeprefix("RECEIPT")
    text = text.lstrip("#")
    return text if _RECEIPT_RE.fullmatch(text) else ""


def email_hash(email: str) -> str:
    """sha256 of the normalised address: what a token or a limiter holds,
    never the address itself."""
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


@dataclass(frozen=True)
class Lookup:
    """What a list read found. `status` is "found", "none", or why Stripe
    could not be asked ("unconfigured", "refused", "unreachable")."""

    status: str
    session_ids: tuple[str, ...] = ()
    detail: str = ""
    read_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def readable(self) -> bool:
        return self.status in ("found", "none")


async def _list_sessions(params: list[tuple[str, str]], *,
                         max_pages: int = 1) -> tuple[Lookup | None, list]:
    """GET /v1/checkout/sessions with `params`: (None, rows) or (a Lookup
    saying why there are none to give, []). Follows `has_more` with
    `starting_after` for up to `max_pages` pages."""
    rows: list = []
    after = ""
    for _ in range(max_pages):
        page = params + ([("starting_after", after)] if after else [])
        failed, got, more = await _list_page(page)
        if failed is not None:
            return failed, []
        rows += got
        last = got[-1].get("id") if got and isinstance(got[-1], dict) else None
        if not more or not valid_session_id(last):
            break
        after = str(last)
    return None, rows


async def _list_page(params: list[tuple[str, str]]) -> tuple[Lookup | None, list, bool]:
    """One page of the list: (failure or None, rows, has_more)."""
    key = read_key()
    if not key:
        return Lookup("unconfigured", detail=f"{KEY_ENV} is not set"), [], False
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=_transport) as client:
            resp = await client.get(f"{API}/checkout/sessions", params=params,
                                    headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        log.warning("academy: Stripe unreachable (%s)", type(exc).__name__)
        return Lookup("unreachable",
                      detail=f"Stripe could not be reached ({type(exc).__name__})"), [], False
    if resp.status_code in (401, 403):
        why = _stripe_message(resp)
        log.warning("academy: Stripe refused the read key (%s): %s",
                    resp.status_code, why)
        return Lookup("refused", detail=f"Stripe refused the read key "
                      f"({resp.status_code}): {why}"), [], False
    if resp.status_code != 200:
        log.warning("academy: Stripe answered %s to a session list", resp.status_code)
        return Lookup("unreachable", detail=f"Stripe answered {resp.status_code}"), [], False
    try:
        body = resp.json()
    except ValueError:
        return Lookup("unreachable", detail="Stripe's answer was not JSON"), [], False
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return Lookup("unreachable", detail="Stripe's answer had no list"), [], False
    return None, rows, bool(body.get("has_more"))


def _receipt_of(session: dict) -> str:
    pi = session.get("payment_intent")
    charge = pi.get("latest_charge") if isinstance(pi, dict) else None
    if not isinstance(charge, dict):
        return ""
    return normalise_receipt(charge.get("receipt_number") or "")


async def find_by_receipt(typed_email: str, receipt: str) -> Lookup:
    """The complete checkouts paid from the address typed whose charge
    carries receipt number `receipt` (already normalised; "" when malformed).

    Stripe's email filter may match case-sensitively, so the address is
    asked twice: as typed (only trimmed) and lowercased. Always two list
    calls, even when the two forms are the same or the input is malformed
    (the sentinel), and every row is compared; results merge by session id."""
    lower = normalise_email(typed_email)
    typed = typed_email.strip() if lower else ""
    forms = (typed or SENTINEL_EMAIL, lower or SENTINEL_EMAIL)
    failed: Lookup | None = None
    rows: list = []
    for form in forms:
        params = [("customer_details[email]", form), ("status", "complete"),
                  ("limit", "100")]
        params += [("expand[]", e) for e in RECOVER_EXPAND]
        why, got = await _list_sessions(params, max_pages=RECOVER_MAX_PAGES)
        failed = failed or why
        rows += got
    if failed is not None:
        return failed
    want = receipt.encode()
    found: list[str] = []
    for s in rows:
        if not isinstance(s, dict):
            continue
        same = hmac.compare_digest(_receipt_of(s).encode(), want)
        if same and receipt and lower and valid_session_id(s.get("id")) \
                and s["id"] not in found:
            found.append(str(s["id"]))
    return Lookup("found" if found else "none", tuple(found))


async def session_for_payment_intent(payment_intent: str) -> Lookup:
    """The checkout session that took `payment_intent` (a refund or a
    dispute event names the payment, not the checkout)."""
    if not PI_RE.fullmatch(payment_intent or ""):
        return Lookup("none", detail="not a payment intent id")
    failed, rows = await _list_sessions([("payment_intent", payment_intent),
                                         ("limit", "1")])
    if failed is not None:
        return failed
    ids = tuple(str(s.get("id")) for s in rows
                if isinstance(s, dict) and valid_session_id(s.get("id")))
    return Lookup("found" if ids else "none", ids[:1])
