"""A course product's price and availability, read live from the store, with
the time they were read.

Never hardcoded and never remembered (invariant 6): every draft and every
approval reads two of Shopify's public product endpoints (no credential):

  * `<product url>.json` -- the first variant's `price`, `compare_at_price`
    and currency, and the page's `body_html` (only to see whether it carries
    the age notice "Sold to adults 18+"; the page text never reaches a prompt).
  * `<product url>.js`   -- the first variant's `available` flag and its
    price in cents. The `.json` endpoint does not say whether a product can be
    bought (`available` is absent there), and the `.js` one carries no
    currency, so both are read and must agree on the price.

A read that fails, disagrees or is off-shape is a `Price` that is not read,
with the reason -- and a post whose product was not read is not drafted
(`sellable_refusal`). The guards: https only, the store's own host only, a
catalog handle only, a public address (`_netguard.assert_public`), no
redirects, a strict amount pattern and a USD check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from . import catalog
from .catalog import OPERATOR

TIMEOUT = 10.0
CURRENCY = "USD"
_AMOUNT = re.compile(r"^\d{1,6}(\.\d{1,2})?$")
# "was", per language: "$20, was $49" / "$20, antes $49" / ...
WAS: dict[str, str] = {"en": "was", "es": "antes", "pt": "antes",
                       "ar": "بدلًا من"}
ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Price:
    """One read of the store. `amount` None: the store was not read.
    `available` None: whether it can be bought was not read."""

    amount: Decimal | None
    compare_at: Decimal | None
    read_at: datetime
    why: str | None = None
    available: bool | None = None
    age_notice: bool = False

    @property
    def read(self) -> bool:
        return self.amount is not None

    @property
    def free(self) -> bool:
        return self.amount is not None and self.amount == ZERO

    @property
    def discounted(self) -> bool:
        return (self.amount is not None and self.compare_at is not None
                and self.compare_at > self.amount)

    def words(self, language: str = "en") -> str | None:
        """What a post says: "$20, was $49", "$20", the lead magnet's fixed
        "Free with an email sign-up", or None (not read). The one word
        between the figures is fixed per language here, in code; a model
        never writes a price."""
        if self.amount is None:
            return None
        if self.free:
            return catalog.FREE_WORDS
        if self.discounted and self.compare_at is not None:
            was = WAS.get(language, WAS["en"])
            return f"{money(self.amount)}, {was} {money(self.compare_at)}"
        return money(self.amount)

    def stamp(self) -> str:
        return f"{self.read_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC"

    def card_line(self, product: str = OPERATOR) -> str:
        """What the card says: the price with its read time, or UNKNOWN."""
        if self.amount is None:
            return (f"Price: UNKNOWN ({self.why or 'no reason given'}), tried "
                    f"{self.stamp()} -- not drafted")
        shown = "free (the store reads 0.00)" if self.free else self.words()
        stock = ("available" if self.available else "NOT available"
                 if self.available is False else "availability UNKNOWN")
        return (f"Price: {shown}, {stock}, read from the store at {self.stamp()} "
                f"({json_url(product)})")

    def record(self) -> dict[str, Any]:
        return {"amount": None if self.amount is None else f"{self.amount:.2f}",
                "compare_at": (None if self.compare_at is None
                               else f"{self.compare_at:.2f}"),
                "read_at": self.read_at.astimezone(UTC).isoformat(timespec="seconds"),
                "why": self.why, "available": self.available,
                "age_notice": self.age_notice}

    def same_as(self, other: Price) -> bool:
        """Both read, and the same figures (the read times may differ)."""
        return (self.read and other.read and self.amount == other.amount
                and self.compare_at == other.compare_at)


def not_read(why: str, at: datetime | None = None) -> Price:
    return Price(None, None, at or datetime.now(UTC), why)


def money(value: Decimal) -> str:
    """$20 for 20.00, $19.99 for 19.99."""
    return f"${value:.0f}" if value == value.to_integral_value() else f"${value:.2f}"


def json_url(product: str = OPERATOR) -> str:
    found = catalog.get(product)
    if found is None:
        raise KeyError(f"no product {product!r} in the catalog")
    return f"{found.url}.json"


def sellable_refusal(read: Price, product: str = OPERATOR) -> str | None:
    """Why a post for `product` may not be drafted or approved on this read,
    or None. Pure."""
    p = catalog.get(product)
    if p is None:
        return f"no product {product!r} in the catalog"
    if not read.read:
        return (f"the price of {p.short} could not be read ({read.why or 'no reason'}), "
                f"tried {read.stamp()}; nothing is drafted without a live price")
    if read.available is None:
        return (f"whether {p.short} can be bought could not be read, tried "
                f"{read.stamp()}; not drafted")
    if read.available is False:
        return f"the store lists {p.short} as not available (read {read.stamp()})"
    if p.lead_magnet and not read.free:
        return (f"{p.short} reads {money(read.amount or ZERO)} on the store, not "
                f"free; a lead magnet is only promoted while it is free")
    if not p.lead_magnet and read.free:
        return (f"{p.short} reads 0.00 on the store; a priced product at 0 is "
                f"not drafted until Miro looks")
    if read.compare_at is not None and not p.compare_at:
        return (f"the store shows a compare-at price of {money(read.compare_at)} "
                f"for {p.short}, which has none by Miro's brief; not drafted until "
                f"he looks")
    return None


def _amount(value: Any) -> Decimal | None:
    if not isinstance(value, str) or not _AMOUNT.match(value.strip()):
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def parse_product(payload: Any, at: datetime) -> Price:
    """The first variant's price and compare-at price from Shopify's product
    JSON, and whether the page carries the age notice. Anything off-shape is
    "not read", never a guess."""
    prod = payload.get("product") if isinstance(payload, dict) else None
    variants = prod.get("variants") if isinstance(prod, dict) else None
    if not isinstance(variants, list) or not variants or not isinstance(
            variants[0], dict):
        return not_read("the store's product JSON has no variant", at)
    v = variants[0]
    for key in ("price_currency", "compare_at_price_currency"):
        cur = v.get(key)
        if cur not in (None, "") and cur != CURRENCY:
            return not_read(f"the store's {key} is {str(cur)[:8]!r}, not "
                            f"{CURRENCY}", at)
    amount = _amount(v.get("price"))
    if amount is None:
        return not_read("the store's price is not a plain amount", at)
    raw_was = v.get("compare_at_price")
    was = None
    if raw_was not in (None, ""):
        was = _amount(raw_was)
        if was is None:
            return not_read("the store's compare-at price is not a plain amount", at)
    body = prod.get("body_html") if isinstance(prod, dict) else None
    notice = isinstance(body, str) and catalog.AGE_MARKER in body
    return Price(amount, was, at, age_notice=notice)


def parse_availability(payload: Any, price: Price) -> Price:
    """`price` with `available` from Shopify's `.js` product answer, whose
    first variant's price (in cents) must be the `.json` read's. Pure."""
    variants = payload.get("variants") if isinstance(payload, dict) else None
    v = variants[0] if isinstance(variants, list) and variants else None
    if not isinstance(v, dict) or not isinstance(v.get("available"), bool):
        return not_read("the store's .js answer has no availability", price.read_at)
    cents = v.get("price")
    if (isinstance(cents, bool) or not isinstance(cents, int) or price.amount is None
            or Decimal(cents) != price.amount * 100):
        return not_read("the store's two product reads disagree on the price",
                        price.read_at)
    return Price(price.amount, price.compare_at, price.read_at, None,
                 v["available"], price.age_notice)


async def _get(url: str) -> tuple[Any, str | None]:
    """(payload, None) or (None, why): one guarded GET of the store."""
    import httpx

    from .._http import explain
    from .._netguard import assert_public

    parts = urlsplit(url)
    host = (urlsplit(catalog.STORE).hostname or "").lower()
    if parts.scheme.lower() != "https":
        return None, f"only https is read, not {parts.scheme or 'no scheme'!r}"
    if (parts.hostname or "").lower() != host:
        return None, "only the course's own store is read"
    handles = {f"/products/{p.handle}{ext}" for p in catalog.CATALOG.values()
               for ext in (".json", ".js")}
    if parts.path not in handles:
        return None, "only a catalog product is read"
    if (why := await assert_public(url)) is not None:
        return None, why
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT,
                                     follow_redirects=False) as client:
            r = await client.get(url, headers={"User-Agent": "ATLAS/1.0",
                                               "Accept": "application/json"})
    except Exception as exc:                            # noqa: BLE001
        return None, explain(exc)
    if r.status_code != 200:
        return None, f"the store answered {r.status_code}"
    try:
        return r.json(), None
    except ValueError:
        return None, "the store's answer was not JSON"


async def read_price(url: str | None = None, *, product: str = OPERATOR) -> Price:
    """The product's price and availability, read now. Never raises: a
    failure is a `Price` that is not read, with the reason."""
    if url is None:
        try:
            url = json_url(product)
        except KeyError as exc:
            return not_read(str(exc))
    payload, why = await _get(url)
    at = datetime.now(UTC)
    if why is not None:
        return not_read(why, at)
    first = parse_product(payload, at)
    if not first.read:
        return first
    js_url = url[: -len(".json")] + ".js" if url.endswith(".json") else url + ".js"
    payload, why = await _get(js_url)
    if why is not None:
        return not_read(f"availability: {why}", at)
    return parse_availability(payload, first)


def from_record(rec: Any) -> Price | None:
    """A `Price.record()` read back from a post row; None when unreadable."""
    if not isinstance(rec, dict):
        return None
    try:
        at = datetime.fromisoformat(str(rec.get("read_at")))
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    amount = _amount(rec.get("amount")) if rec.get("amount") else None
    was = _amount(rec.get("compare_at")) if rec.get("compare_at") else None
    available = rec.get("available")
    return Price(amount, was, at, rec.get("why"),
                 available if isinstance(available, bool) else None,
                 bool(rec.get("age_notice")))
