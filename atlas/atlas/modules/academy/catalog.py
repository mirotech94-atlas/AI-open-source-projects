"""ATLAS Academy's catalog: one checked-in file, `catalog.json`, read here.

Everything the store says about a product comes from that file, and
everything the access page hands over is named there too. A value that is
Miro's to decide (a price, a refund term, a payment link) is `null` or empty
until he sets it, and the code treats "unset" as "not on sale", never as a
default:

* `buy_live(offer)` is true only when the offer has a Stripe Payment Link
  AND a price. The site and the public catalog.json both use it.
* `validate` refuses a catalog where a payment link is set but the price,
  the Stripe price id (which the access page matches a checkout against)
  or the product's refund terms are not: a Buy button must never lead to a
  purchase the store cannot deliver or has no terms for.
* A file is named by a key and a path relative to the private files
  directory. The path is checked here (no absolute path, no `..`, a short
  safe alphabet) and again when it is served.

Pure: no I/O beyond reading the JSON, no network.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
FILE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# Relative, lowercase, one or more segments of a safe alphabet. No "..", no
# leading slash, no backslash, no hidden file.
FILE_PATH_RE = re.compile(r"^(?:[a-z0-9][a-z0-9._-]{0,80}/)*[a-z0-9][a-z0-9._-]{0,120}$")
PRICE_ID_RE = re.compile(r"^price_[A-Za-z0-9]{6,64}$")
PAYMENT_LINK_PREFIX = "https://buy.stripe.com/"


class CatalogError(ValueError):
    """The catalog cannot be used as written."""


@dataclass(frozen=True)
class File:
    key: str
    label: str
    path: str


@dataclass(frozen=True)
class Offer:
    """One thing a buyer can pay for: one price, one payment link."""

    id: str
    name: str
    price_usd: float | None
    payment_link: str
    stripe_price_id: str
    files: tuple[File, ...]
    product: str                     # the page it is sold on ("" for the bundle)
    blurb: str = ""
    cover: str = ""
    price_note: str = ""

    @property
    def buy_live(self) -> bool:
        return bool(self.payment_link) and self.price_usd is not None


@dataclass(frozen=True)
class Product:
    slug: str
    title: str
    kind: str
    cover: str
    cover_alt: str
    summary: str
    intro: str
    benefits: tuple[str, ...]
    label: tuple[tuple[str, str], ...]
    inside: tuple[tuple[str, tuple[str, ...]], ...]
    audience: tuple[str, ...]
    not_for: tuple[str, ...]
    notice: str
    faq: tuple[tuple[str, str], ...]
    refund: str | None
    licence: str | None
    pairs_with: str
    pairs_reason: str
    offers: tuple[Offer, ...]
    tags: tuple[str, ...] = ()       # catalog filter / finder keys (site.py TAGS)


@dataclass(frozen=True)
class Bundle:
    slug: str
    title: str
    summary: str
    includes: tuple[str, ...]        # offer ids
    offer: Offer                     # the bundle as a sellable offer
    terms_note: str = ""


@dataclass(frozen=True)
class Catalog:
    store: dict[str, Any]
    products: tuple[Product, ...]
    bundle: Bundle
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    # -------------------------------------------------------------- lookups

    def product(self, slug: str) -> Product | None:
        return next((p for p in self.products if p.slug == slug), None)

    def offers(self) -> tuple[Offer, ...]:
        """Every sellable offer, the bundle last."""
        return tuple(o for p in self.products for o in p.offers) + (self.bundle.offer,)

    def offer(self, offer_id: str) -> Offer | None:
        return next((o for o in self.offers() if o.id == offer_id), None)

    def offer_for_price(self, price_id: str) -> Offer | None:
        """The offer a Stripe price id belongs to. An empty id matches nothing."""
        if not price_id:
            return None
        return next((o for o in self.offers() if o.stripe_price_id == price_id), None)

    def file(self, key: str) -> File | None:
        return next((f for o in self.offers() for f in o.files if f.key == key), None)

    def bundle_offers(self) -> tuple[Offer, ...]:
        return tuple(o for o in (self.offer(i) for i in self.bundle.includes) if o)

    def separate_total(self) -> float | None:
        """What the bundle's titles cost bought one by one today, or None
        when any of their prices is unset: never a partial sum."""
        prices = [o.price_usd for o in self.bundle_offers()]
        if not prices or any(p is None for p in prices):
            return None
        return float(sum(p for p in prices if p is not None))

    @property
    def contact_email(self) -> str:
        return str(self.store.get("contact_email") or "")

    @property
    def base_url(self) -> str:
        return f"https://{self.store['domain']}"


# ------------------------------------------------------------------ parsing

def _files(raw: list[dict[str, Any]]) -> tuple[File, ...]:
    return tuple(File(str(f["key"]), str(f["label"]), str(f["path"])) for f in raw)


def _price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CatalogError(f"price {value!r} is not a number or null")
    return float(value)


def _offer(raw: dict[str, Any], product: str) -> Offer:
    return Offer(
        id=str(raw["id"]), name=str(raw["name"]), price_usd=_price(raw.get("price_usd")),
        payment_link=str(raw.get("payment_link") or "").strip(),
        stripe_price_id=str(raw.get("stripe_price_id") or "").strip(),
        files=_files(raw.get("files") or []), product=product,
        blurb=str(raw.get("blurb") or ""), cover=str(raw.get("cover") or ""),
        price_note=str(raw.get("price_note") or ""))


def _product(raw: dict[str, Any]) -> Product:
    slug = str(raw["slug"])
    return Product(
        slug=slug, title=str(raw["title"]), kind=str(raw["kind"]),
        cover=str(raw.get("cover") or ""), cover_alt=str(raw.get("cover_alt") or ""),
        summary=str(raw["summary"]), intro=str(raw["intro"]),
        benefits=tuple(raw.get("benefits") or ()),
        label=tuple((str(r["name"]), str(r["value"])) for r in raw.get("label") or ()),
        inside=tuple((str(g["title"]), tuple(g["items"]))
                     for g in raw.get("inside") or ()),
        audience=tuple(raw.get("audience") or ()),
        not_for=tuple(raw.get("not_for") or ()),
        notice=str(raw.get("notice") or ""),
        faq=tuple((str(f["q"]), str(f["a"])) for f in raw.get("faq") or ()),
        refund=raw.get("refund") or None, licence=raw.get("licence") or None,
        pairs_with=str(raw.get("pairs_with") or ""),
        pairs_reason=str(raw.get("pairs_reason") or ""),
        offers=tuple(_offer(o, slug) for o in raw.get("offers") or ()),
        tags=tuple(str(t) for t in raw.get("tags") or ()))


def parse(raw: dict[str, Any]) -> Catalog:
    try:
        products = tuple(_product(p) for p in raw["products"])
        b = raw["bundle"]
        includes = tuple(str(i) for i in b["includes"])
        by_id = {o.id: o for p in products for o in p.offers}
        files: list[File] = []
        for oid in includes:
            for f in by_id[oid].files if oid in by_id else ():
                if f.key not in {x.key for x in files}:
                    files.append(f)
        offer = _offer({**b, "id": b["slug"], "name": b["title"], "files": []}, "")
        offer = replace(offer, files=tuple(files))
        bundle = Bundle(slug=str(b["slug"]), title=str(b["title"]),
                        summary=str(b["summary"]), includes=includes, offer=offer,
                        terms_note=str(b.get("terms_note") or ""))
        cat = Catalog(store=dict(raw["store"]), products=products, bundle=bundle, raw=raw)
    except (KeyError, TypeError) as exc:
        raise CatalogError(f"catalog is missing or mistypes a field: {exc}") from exc
    problems = validate(cat)
    if problems:
        raise CatalogError("; ".join(problems))
    return cat


def load(path: Path | None = None) -> Catalog:
    return parse(json.loads((path or CATALOG_PATH).read_text(encoding="utf-8")))


# ------------------------------------------------------------------ rules

def validate(cat: Catalog) -> list[str]:
    """Every reason this catalog must not be published. Empty means usable."""
    out: list[str] = []
    store = cat.store
    for key in ("name", "domain", "contact_email", "currency"):
        if not store.get(key):
            out.append(f"store.{key} is empty")
    if "@" not in str(store.get("contact_email") or ""):
        out.append("store.contact_email is not an email address")
    days = store.get("download_link_days")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 30:
        out.append("store.download_link_days must be a whole number of days, 1-30")
    access = store.get("access_valid_days")
    if access is not None and (not isinstance(access, int) or access < 1):
        out.append("store.access_valid_days must be null or a positive whole number")

    slugs = [p.slug for p in cat.products] + [cat.bundle.slug]
    for s in slugs:
        if not SLUG_RE.match(s):
            out.append(f"slug {s!r} is not a URL-safe label")
    if len(set(slugs)) != len(slugs):
        out.append("product slugs are not unique")
    ids = [o.id for o in cat.offers()]
    if len(set(ids)) != len(ids):
        out.append("offer ids are not unique")
    price_ids = [o.stripe_price_id for o in cat.offers() if o.stripe_price_id]
    if len(set(price_ids)) != len(price_ids):
        out.append("two offers share a Stripe price id")

    paths: dict[str, str] = {}
    for o in cat.offers():
        for f in o.files:
            if not FILE_KEY_RE.match(f.key):
                out.append(f"file key {f.key!r} is not a safe key")
            if not FILE_PATH_RE.match(f.path) or ".." in f.path:
                out.append(f"file {f.key!r} has an unsafe path {f.path!r}")
            if paths.setdefault(f.key, f.path) != f.path:
                out.append(f"file key {f.key!r} names two different paths")

    for p in cat.products:
        if not p.offers:
            out.append(f"{p.slug} has no offer")
        if p.pairs_with and (p.pairs_with == p.slug or cat.product(p.pairs_with) is None):
            out.append(f"{p.slug} pairs with {p.pairs_with!r}, "
                       "which is not another product")
        for t in p.tags:
            if not FILE_KEY_RE.match(t):
                out.append(f"{p.slug} has a tag {t!r} that is not a safe key")
        for o in p.offers:
            if not o.files:
                out.append(f"offer {o.id} delivers no file")
            out.extend(_sale_problems(o, has_terms=p.refund is not None))
    for oid in cat.bundle.includes:
        if cat.offer(oid) is None or oid == cat.bundle.slug:
            out.append(f"bundle includes unknown offer {oid!r}")
    terms = all(cat.product(o.product) and cat.product(o.product).refund  # type: ignore[union-attr]
                for o in cat.bundle_offers())
    out.extend(_sale_problems(cat.bundle.offer, has_terms=terms))
    return out


def _sale_problems(o: Offer, *, has_terms: bool) -> list[str]:
    out: list[str] = []
    if o.price_usd is not None and not 0 < o.price_usd <= 5000:
        out.append(f"offer {o.id} has an implausible price {o.price_usd}")
    if o.price_usd is not None and round(o.price_usd * 100) != o.price_usd * 100:
        out.append(f"offer {o.id} has a price with more than two decimals")
    if o.stripe_price_id and not PRICE_ID_RE.match(o.stripe_price_id):
        out.append(f"offer {o.id} has a malformed Stripe price id")
    if not o.payment_link:
        return out
    if not o.payment_link.startswith(PAYMENT_LINK_PREFIX) or any(
            c in o.payment_link for c in "\"'<> "):
        out.append(f"offer {o.id}: payment_link must be a {PAYMENT_LINK_PREFIX} URL")
    if o.price_usd is None:
        out.append(f"offer {o.id} has a payment link but no price")
    if not o.stripe_price_id:
        out.append(f"offer {o.id} has a payment link but no stripe_price_id: "
                   "the access page could not match its checkouts")
    if not has_terms:
        out.append(f"offer {o.id} has a payment link but its refund terms are unset")
    return out


def money(price: float | None) -> str:
    """$9, $19.50; '' for unset. Never rounds a set price away."""
    if price is None:
        return ""
    return f"${price:,.0f}" if price == int(price) else f"${price:,.2f}"
