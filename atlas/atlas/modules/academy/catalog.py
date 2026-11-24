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


# ------------------------------------------------- offer panels (2026-10-11)
#
# Miro's competitor report ("Compete through useful results", 11 Oct 2026):
# lead each page with what the buyer will finish, the free lesson that leads
# to it, what was tested and when, a troubleshooting preview, the approval
# step, real samples, and exactly what is in the box. Every field below is
# optional and defaults to empty, so a product without it renders as before.
# What could not be checked goes in `unverified` (a product's) or a store
# `*_note`, which no page ever prints: it is the list of questions for Miro.

INCLUSION_KINDS = ("video", "guide", "files", "worksheet")
EVIDENCE_KINDS = ("sample-lesson", "worksheet", "page-image", "template", "exercise")
LESSON_STATUSES = ("scripted", "planned", "proposed")
TROUBLE_KINDS = ("setup", "expected", "unsupported")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Inclusion:
    """One labelled thing in the box (the 3D kit scene and its static list)."""

    kind: str                        # INCLUSION_KINDS
    label: str
    detail: str = ""
    count: int | None = None         # videos: must equal the offer's .mp4 files


@dataclass(frozen=True)
class Finish:
    """"What you will finish": the deliverable and a preview of real material."""

    deliverable: str
    preview: str


@dataclass(frozen=True)
class LessonRef:
    """A free lesson, by tutorial series key and episode slug (or a proposal
    in tutorial_proposals.json, series "proposal"). Linked only once `url`
    (the published video) is set."""

    series: str
    slug: str
    status: str                      # LESSON_STATUSES
    url: str = ""


@dataclass(frozen=True)
class Journey:
    free_lessons: tuple[LessonRef, ...]
    exercise: str                    # the small thing to try after the free lesson
    paid_adds: tuple[tuple[str, str], ...]   # (what, detail): "What the full kit adds"
    note: str = ""


@dataclass(frozen=True)
class Check:
    """One check that was actually run, by whom and when ("Tested with")."""

    tool: str
    version: str
    checked_on: str                  # YYYY-MM-DD
    by: str
    what: str


@dataclass(frozen=True)
class Trouble:
    kind: str                        # TROUBLE_KINDS: setup issue, correct refusal, unsupported
    problem: str
    example_input: str
    expected_output: str
    recovery: str


@dataclass(frozen=True)
class ReviewDemo:
    """Request -> draft -> the review step, with the control's limits."""

    request: str
    draft: str
    review_step: str
    on_approve: str
    on_silence: str
    limits: str


@dataclass(frozen=True)
class Evidence:
    kind: str                        # EVIDENCE_KINDS
    label: str
    source: str                      # where the real file is (vault path)
    url: str = ""                    # set once it is published on the store


@dataclass(frozen=True)
class Audience:
    key: str
    name: str
    description: str


@dataclass(frozen=True)
class RouteStep:
    title: str
    who: str
    do: str
    source: str


@dataclass(frozen=True)
class Route:
    """A learn-together route across products (the family offering)."""

    slug: str
    title: str
    products: tuple[str, ...]
    people: str
    steps: tuple[RouteStep, ...]
    printables: tuple[str, ...]
    boundaries: tuple[str, ...]
    overlap: str = ""
    unverified: tuple[str, ...] = ()     # never rendered


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
    inclusions: tuple[Inclusion, ...] = ()

    @property
    def video_count(self) -> int:
        return sum(1 for f in self.files if f.path.endswith(".mp4"))

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
    tags: tuple[str, ...] = ()       # catalog filter / finder keys (site.py PATHS)
    level: str = ""                  # the level as words: "Beginner, no code"
    level_detail: str = ""           # what that level asks of the reader
    finish: Finish | None = None
    journey: Journey | None = None
    tested: tuple[Check, ...] = ()
    troubleshooting: tuple[Trouble, ...] = ()
    audiences: tuple[str, ...] = ()  # keys of store.audiences
    review_demo: ReviewDemo | None = None
    evidence: tuple[Evidence, ...] = ()
    testimonials: tuple[str, ...] = ()   # genuine, with permission; none yet
    unverified: tuple[str, ...] = ()     # Miro's open questions; never rendered


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
    routes: tuple[Route, ...] = ()

    # -------------------------------------------------------------- lookups

    @property
    def audiences(self) -> tuple[Audience, ...]:
        return tuple(Audience(str(k), str(v["name"]), str(v["description"]))
                     for k, v in (self.store.get("audiences") or {}).items())

    def audience(self, key: str) -> Audience | None:
        return next((a for a in self.audiences if a.key == key), None)

    @property
    def support(self) -> dict[str, Any]:
        """The bounded-help policy (store.support). `*_note` keys and null
        values are Miro's to decide and are never rendered."""
        return dict(self.store.get("support") or {})

    def routes_for(self, slug: str) -> tuple[Route, ...]:
        return tuple(r for r in self.routes if slug in r.products)

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
    def support_email(self) -> str:
        """The store's support contact (owner@example.com); the personal
        contact_email when none is set."""
        return str(self.store.get("support_email") or "") or self.contact_email

    @property
    def base_url(self) -> str:
        return f"https://{self.store['domain']}"

    @property
    def store_url(self) -> str:
        """Where the whole store lives: https://<host>/academy (2026-11-16)."""
        return f"{self.base_url}/academy"


# ------------------------------------------------------------------ parsing

def _files(raw: list[dict[str, Any]]) -> tuple[File, ...]:
    return tuple(File(str(f["key"]), str(f["label"]), str(f["path"])) for f in raw)


def _price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CatalogError(f"price {value!r} is not a number or null")
    return float(value)


def _inclusions(raw: list[dict[str, Any]]) -> tuple[Inclusion, ...]:
    out = []
    for i in raw:
        count = i.get("count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int)):
            raise CatalogError(f"inclusion count {count!r} is not a whole number")
        out.append(Inclusion(kind=str(i["kind"]), label=str(i["label"]),
                             detail=str(i.get("detail") or ""), count=count))
    return tuple(out)


def _offer(raw: dict[str, Any], product: str) -> Offer:
    return Offer(
        id=str(raw["id"]), name=str(raw["name"]), price_usd=_price(raw.get("price_usd")),
        payment_link=str(raw.get("payment_link") or "").strip(),
        stripe_price_id=str(raw.get("stripe_price_id") or "").strip(),
        files=_files(raw.get("files") or []), product=product,
        blurb=str(raw.get("blurb") or ""), cover=str(raw.get("cover") or ""),
        price_note=str(raw.get("price_note") or ""),
        inclusions=_inclusions(raw.get("inclusions") or []))


def _strs(raw: Any) -> tuple[str, ...]:
    return tuple(str(x) for x in raw or ())


def _finish(raw: dict[str, Any] | None) -> Finish | None:
    if not raw:
        return None
    return Finish(deliverable=str(raw["deliverable"]), preview=str(raw.get("preview") or ""))


def _journey(raw: dict[str, Any] | None) -> Journey | None:
    if not raw:
        return None
    return Journey(
        free_lessons=tuple(LessonRef(series=str(lr["series"]), slug=str(lr["slug"]),
                                     status=str(lr["status"]), url=str(lr.get("url") or ""))
                           for lr in raw.get("free_lessons") or ()),
        exercise=str(raw.get("exercise") or ""),
        paid_adds=tuple((str(a["what"]), str(a["detail"]))
                        for a in raw.get("paid_adds") or ()),
        note=str(raw.get("note") or ""))


def _review_demo(raw: dict[str, Any] | None) -> ReviewDemo | None:
    if not raw:
        return None
    return ReviewDemo(**{k: str(raw[k]) for k in (
        "request", "draft", "review_step", "on_approve", "on_silence", "limits")})


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
        tags=tuple(str(t) for t in raw.get("tags") or ()),
        level=str(raw.get("level") or ""),
        level_detail=str(raw.get("level_detail") or ""),
        finish=_finish(raw.get("finish")),
        journey=_journey(raw.get("journey")),
        tested=tuple(Check(tool=str(c["tool"]), version=str(c.get("version") or ""),
                           checked_on=str(c["checked_on"]), by=str(c["by"]),
                           what=str(c["what"])) for c in raw.get("tested") or ()),
        troubleshooting=tuple(
            Trouble(kind=str(t["kind"]), problem=str(t["problem"]),
                    example_input=str(t.get("example_input") or ""),
                    expected_output=str(t.get("expected_output") or ""),
                    recovery=str(t["recovery"]))
            for t in raw.get("troubleshooting") or ()),
        audiences=_strs(raw.get("audiences")),
        review_demo=_review_demo(raw.get("review_demo")),
        evidence=tuple(Evidence(kind=str(e["kind"]), label=str(e["label"]),
                                source=str(e["source"]), url=str(e.get("url") or ""))
                       for e in raw.get("evidence") or ()),
        testimonials=_strs(raw.get("testimonials")),
        unverified=_strs(raw.get("unverified")))


def _route(raw: dict[str, Any]) -> Route:
    return Route(
        slug=str(raw["slug"]), title=str(raw["title"]), products=_strs(raw["products"]),
        people=str(raw["people"]),
        steps=tuple(RouteStep(title=str(s["title"]), who=str(s["who"]), do=str(s["do"]),
                              source=str(s["source"])) for s in raw.get("steps") or ()),
        printables=_strs(raw.get("printables")), boundaries=_strs(raw.get("boundaries")),
        overlap=str(raw.get("overlap") or ""), unverified=_strs(raw.get("unverified")))


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
        routes = tuple(_route(r) for r in raw.get("routes") or ())
        cat = Catalog(store=dict(raw["store"]), products=products, bundle=bundle, raw=raw,
                      routes=routes)
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
            out.extend(_inclusion_problems(o))
        out.extend(_panel_problems(cat, p))
    for r in cat.routes:
        if not SLUG_RE.match(r.slug):
            out.append(f"route slug {r.slug!r} is not a URL-safe label")
        for s in r.products:
            if cat.product(s) is None:
                out.append(f"route {r.slug} names unknown product {s!r}")
    for oid in cat.bundle.includes:
        if cat.offer(oid) is None or oid == cat.bundle.slug:
            out.append(f"bundle includes unknown offer {oid!r}")
    terms = all(cat.product(o.product) and cat.product(o.product).refund  # type: ignore[union-attr]
                for o in cat.bundle_offers())
    out.extend(_sale_problems(cat.bundle.offer, has_terms=terms))
    return out


def _inclusion_problems(o: Offer) -> list[str]:
    """A count in the box must be the count of files delivered: the 3D scene
    and its static list may never say 30 videos when 28 ship."""
    out: list[str] = []
    for i in o.inclusions:
        if i.kind not in INCLUSION_KINDS:
            out.append(f"offer {o.id} has an inclusion of unknown kind {i.kind!r}")
    videos = [i for i in o.inclusions if i.kind == "video"]
    if videos and sum(i.count or 0 for i in videos) != o.video_count:
        out.append(f"offer {o.id} lists {sum(i.count or 0 for i in videos)} videos "
                   f"but delivers {o.video_count}")
    if o.inclusions and o.video_count and not videos:
        out.append(f"offer {o.id} delivers videos its inclusions don't list")
    return out


def _panel_problems(cat: Catalog, p: Product) -> list[str]:
    out: list[str] = []
    for a in p.audiences:
        if cat.audience(a) is None:
            out.append(f"{p.slug} names unknown audience {a!r}")
    if p.journey:
        for lr in p.journey.free_lessons:
            if lr.status not in LESSON_STATUSES:
                out.append(f"{p.slug} free lesson {lr.slug} has status {lr.status!r}")
            if lr.url and not lr.url.startswith("https://"):
                out.append(f"{p.slug} free lesson {lr.slug} url is not https")
    for c in p.tested:
        if not DATE_RE.match(c.checked_on):
            out.append(f"{p.slug} check {c.tool!r} date {c.checked_on!r} is not YYYY-MM-DD")
    for t in p.troubleshooting:
        if t.kind not in TROUBLE_KINDS:
            out.append(f"{p.slug} troubleshooting kind {t.kind!r} is unknown")
    for e in p.evidence:
        if e.kind not in EVIDENCE_KINDS:
            out.append(f"{p.slug} evidence kind {e.kind!r} is unknown")
        if e.url and not (e.url.startswith("https://") or FILE_PATH_RE.match(e.url)):
            out.append(f"{p.slug} evidence url {e.url!r} is neither https nor a store path")
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
