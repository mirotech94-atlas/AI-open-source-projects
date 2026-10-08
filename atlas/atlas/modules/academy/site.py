"""The ATLAS Academy storefront, generated from `catalog.json`.

    python -m atlas.modules.academy build <out_dir>

`render(catalog, now)` is pure: it returns every file of the site as
{relative path: bytes}. `build` writes them to a directory. Publishing that
directory at https://shop.mapfront.io/ is `academy.publish_shop` (Tier 2),
which stages it as a release under the Mapfront publish root exactly as a
client site is (atlas/modules/agency/hosting.py): Caddy's `*.mapfront.io`
block serves `/srv/sites/shop`, and on-demand TLS is granted only to a slug
with a live release link.

What the site will not say, by construction:

* No review, rating, testimonial, sales count, "limited time" or countdown.
  There is no field for any of them in the catalog, and the tests scan the
  output for the words.
* No number that is not in the catalog. Every figure on a page (pages,
  lessons, prices, the count of titles) is catalog text, read from the built
  files.
* No Buy link without a payment link AND a price (`Offer.buy_live`). Until
  then the button is a disabled <button> reading "Coming soon".
* No "save $X", no struck-through price, no "New" flag. The bundle shows what
  its titles cost bought one by one only when every one of those prices is
  set.
* "14-day money-back guarantee" only where a product's own refund text says
  it; the store-wide line only when every product's does.

One first-party script, assets/app.js (theme toggle, menu, filter chips, the
"Find your fit" finder), which makes no request and stores nothing; the home
page also carries one inline script that only assigns `window.FINDER`, the
finder's data, generated here from the catalog. No third-party request: the
fonts and the covers are served from the site itself.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from .catalog import Bundle, Catalog, Offer, Product, load, money

ASSETS = Path(__file__).with_name("assets")
CURRENCY = "USD"
# The contact form posts to the API (atlas/api/academy.py `contact`), which
# stores the message, posts it to Miro and redirects to contact/sent/. The
# limits are the API's, repeated here so the browser checks them first.
CONTACT_ACTION = "https://atlas.example.com/public/academy/contact"
NAME_MAX, EMAIL_MAX, ORDER_MAX = 120, 254, 80
MESSAGE_MIN, MESSAGE_MAX = 10, 4000

# The refund phrase the store repeats only when a product's own terms say it.
GUARANTEE = "14-day money-back guarantee"

# Catalog filter chips, in display order: tag -> label. A product's `tags`
# (catalog.json) pick which chips exist; a tag not listed here gets its own
# name as the label.
TAGS = {
    "business": "For business", "automate": "Automation", "build": "For builders",
    "home": "Home & family", "start": "Starting out", "organize": "Organising",
    "money": "Selling online", "local": "Local business", "senior": "60 and over",
}
# The finder's choices: key, the line the visitor picks, the tag it maps to.
# "all" is the bundle followed by whatever the bundle leaves out.
FINDER = (
    ("local", "I run a small local business", "local"),
    ("start", "I'm starting a service business or freelancing", "start"),
    ("build", "I write code and want a safe AI agent", "build"),
    ("home", "I want to use AI safely at home", "home"),
    ("senior", "I'm 60+, or helping a parent who is", "senior"),
    ("all", "I want the whole library", ""),
)

BRAND_SVG = (
    '<svg viewBox="0 0 40 40" fill="none" aria-hidden="true">'
    '<circle cx="20" cy="20" r="17.5" stroke="currentColor" stroke-width="2.5"/>'
    '<path d="M11.5 29.5 20 9.5l8.5 20" stroke="currentColor" stroke-width="3.2" '
    'stroke-linejoin="round" stroke-linecap="round"/>'
    '<path d="M14.6 23h10.8" stroke="#e5a43c" stroke-width="3.2" stroke-linecap="round"/>'
    "</svg>")
TICK_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M20 6 9 17l-5-5"/></svg>')
CROSS_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
    'stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>')
ARROW_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M5 12h14M13 6l6 6-6 6"/></svg>')
MENU_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>')
STRIP_SVGS = {
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "shield": '<path d="M12 3 4 6v6c0 5 3.4 8.3 8 9 4.6-.7 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
    "lock": '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "eye": ('<path d="M3 3l18 18"/><path d="M10.6 6.1A10 10 0 0 1 12 6c5 0 9 6 9 6a17 17 0 0 1'
            '-3 3.4M6.3 7.6C4.2 9.2 3 12 3 12s4 6 9 6a9 9 0 0 0 3.6-.8"/>'),
}


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _guarantee(p: Product) -> bool:
    return bool(p.refund and GUARANTEE in p.refund)


def _store_guarantee(cat: Catalog) -> bool:
    return all(_guarantee(p) for p in cat.products)


# ------------------------------------------------------------------ chrome

def _page(cat: Catalog, *, title: str, description: str, body: str, depth: int,
          current: str = "", root: str | None = None) -> str:
    root = "../" * depth if root is None else root
    store = _e(cat.store["name"])
    nav = [("shop", "Shop", f"{root}#catalog"),
           ("finder", "Find your fit", f"{root}#finder"),
           ("compare", "Compare", f"{root}#compare"),
           ("bundle", "Bundle", f"{root}{cat.bundle.slug}/"),
           ("faq", "FAQ", f"{root}#faq"),
           ("contact", "Contact", f"{root}contact/")]
    links = "".join(
        f'<li><a href="{href}"{" aria-current=\"page\"" if key == current else ""}>'
        f"{label}</a></li>" for key, label, href in nav)
    full_title = f"{_e(title)} | {store}" if title != cat.store["name"] else store
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{full_title}</title>
<meta name="description" content="{_e(description)}">
<meta name="theme-color" content="#f4efe4" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#08131d" media="(prefers-color-scheme: dark)">
<link rel="preload" href="{root}assets/fonts/atkinson-next-var.woff" as="font" type="font/woff" crossorigin>
<link rel="preload" href="{root}assets/fonts/cabinet-800.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{root}assets/style.css">
<link rel="icon" href="{root}assets/icon.svg" type="image/svg+xml">
<script src="{root}assets/app.js" defer></script>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap">
<a class="brand" href="{root}" aria-label="{store} home">{BRAND_SVG}<span>ATLAS <small>Academy</small></span></a>
<nav aria-label="Main"><ul class="nav">{links}</ul></nav>
<div class="top-actions">
<button class="icon-btn" type="button" data-theme-toggle aria-label="Switch theme"></button>
<button class="icon-btn menu-btn" type="button" aria-expanded="false" aria-label="Open menu">{MENU_SVG}</button>
<a class="btn btn-buy btn-sm" href="{root}{cat.bundle.slug}/">Get the bundle</a>
</div>
</div></header>
<main id="main">
{body}
</main>
{_footer(cat, root)}
</body>
</html>
"""


def _footer(cat: Catalog, root: str) -> str:
    store, author = _e(cat.store["name"]), _e(cat.store["author"])
    email = _e(cat.contact_email)
    shop = "".join(f'<li><a href="{root}{p.slug}/">{_e(p.title)}</a></li>' for p in cat.products)
    shop += f'<li><a href="{root}{cat.bundle.slug}/">{_e(cat.bundle.title)} (the bundle)</a></li>'
    return f"""<footer class="foot"><div class="wrap">
<div class="cols">
<div><a class="brand" href="{root}">{BRAND_SVG}<span>ATLAS <small>Academy</small></span></a>
<p class="about-line">{store} is run by {author}. Payments by Stripe. No cookies, no tracking.</p></div>
<div><h4>Shop</h4><ul>{shop}</ul></div>
<div><h4>Help</h4><ul>
<li><a href="{root}#how">How buying works</a></li>
<li><a href="{root}#faq">FAQ</a></li>
<li><a href="{root}contact/">Contact</a></li>
<li><a href="{root}contact/#lost">Lost your access page?</a></li>
</ul></div>
<div><h4>Policies</h4><ul>
<li><a href="{root}refunds/">Refunds</a></li>
<li><a href="{root}terms/">Terms of sale</a></li>
<li><a href="{root}privacy/">Privacy</a></li>
</ul></div>
</div>
<div class="legal"><span>{store} &middot; Run by {author}</span><span>Prices in USD &middot; <a href="mailto:{email}">{email}</a></span></div>
</div></footer>"""


def cover_src(p: Product | Offer, root: str) -> str:
    path = p.cover or (f"covers/{p.slug}.svg" if isinstance(p, Product) else "")
    return f"{root}assets/{path}" if path else ""


def _cover(p: Product, root: str, *, cls: str = "frame", eager: bool = False) -> str:
    loading = "eager" if eager else "lazy"
    return (f'<div class="{cls}"><img src="{cover_src(p, root)}" alt="{_e(p.cover_alt)}" '
            f'width="800" height="800" loading="{loading}" decoding="async"></div>')


def _crumbs(root: str, here: str) -> str:
    return (f'<div class="wrap crumbs"><a href="{root}#catalog">Catalog</a><span>/</span>'
            f"{_e(here)}</div>")


# ------------------------------------------------------------------ buttons

def buy_button(offer: Offer, *, label: str | None = None, extra: str = "") -> str:
    """A live Buy link, or a disabled button that says why there isn't one."""
    cls = f"btn btn-buy {extra}".strip()
    if offer.buy_live:
        text = label or f"Buy for {money(offer.price_usd)}"
        return (f'<a class="{cls}" href="{_e(offer.payment_link)}" '
                f'rel="noopener">{_e(text)}</a>')
    return (f'<button class="{cls}" type="button" disabled aria-disabled="true">'
            f"Coming soon</button>")


def _price(offer: Offer) -> str:
    if offer.price_usd is None:
        return '<span class="price-unset">Price not set yet</span>'
    return f'<span class="price">{money(offer.price_usd)}</span>'


def _price_text(p: Product) -> str:
    """'$9', 'From $19', or 'Price not set yet': plain text, no markup."""
    prices = [o.price_usd for o in p.offers if o.price_usd is not None]
    if not prices:
        return "Price not set yet"
    if len(p.offers) == 1:
        return money(prices[0])
    return f"From {money(min(prices))}"


def _from_price(p: Product) -> str:
    text = _price_text(p)
    if text == "Price not set yet":
        return f'<span class="price-unset">{text}</span>'
    return f'<span class="price">{text}</span>'


def _card_price(p: Product) -> str:
    """The catalog card's price block: a small word above the figure."""
    prices = [o.price_usd for o in p.offers if o.price_usd is not None]
    if not prices:
        return '<span class="price-unset">Price not set yet</span>'
    if len(p.offers) == 1:
        return f'<span class="price"><small>One-time</small>{money(prices[0])}</span>'
    return f'<span class="price"><small>From</small>{money(min(prices))}</span>'


def _single_live(p: Product) -> Offer | None:
    """The one offer a Buy button can point at from outside the product page."""
    return p.offers[0] if len(p.offers) == 1 else None


def _assure(guarantee: bool) -> str:
    """The assurance lines under a Buy; the guarantee line only when the
    refund terms in question say it."""
    items = ([f"<li>{TICK_SVG} {GUARANTEE}</li>"] if guarantee else []) + [
        f"<li>{TICK_SVG} Instant download after payment</li>",
        f"<li>{TICK_SVG} Secure checkout by Stripe</li>"]
    return f'<ul class="assure">{"".join(items)}</ul>'


# ------------------------------------------------------------------ home

def _finder_data(cat: Catalog) -> dict[str, list[dict[str, str]]]:
    """`window.FINDER`: for each choice, the titles to recommend, in catalog
    order. `why` is the product's own summary, never invented copy; `buy`
    is a live single offer's payment link, else '' (app.js then links to the
    product's buy box)."""
    def rec(p: Product) -> dict[str, str]:
        o = _single_live(p)
        return {"cover": cover_src(p, ""), "title": _e(p.title), "why": _e(p.summary),
                "price": _price_text(p),
                "buy": o.payment_link if o is not None and o.buy_live else "",
                "href": f"{p.slug}/"}
    b = cat.bundle
    in_bundle = {o.product for o in cat.bundle_offers()}
    bundle_rec = {"cover": cover_src(cat.products[0], ""), "title": _e(b.title),
                  "why": _e(b.summary),
                  "price": money(b.offer.price_usd) or "Price not set yet",
                  "buy": b.offer.payment_link if b.offer.buy_live else "",
                  "href": f"{b.slug}/"}
    data: dict[str, list[dict[str, str]]] = {}
    for key, _line, tag in FINDER:
        if tag:
            data[key] = [rec(p) for p in cat.products if tag in p.tags]
        else:
            data[key] = [bundle_rec] + [rec(p) for p in cat.products
                                        if p.slug not in in_bundle]
    return data


def _finder(cat: Catalog) -> str:
    choices = "".join(
        f'<button class="choice" type="button" aria-pressed="false" data-key="{key}">'
        f'<span class="dot"></span>{_e(line)}</button>' for key, line, _tag in FINDER)
    # The one inline script: data only. "</" is escaped so no summary can
    # close the tag early.
    data = json.dumps(_finder_data(cat), ensure_ascii=True).replace("</", "<\\/")
    return f"""<section id="finder"><div class="wrap">
<div class="finder reveal">
<div>
<p class="eyebrow">Find your fit</p>
<h2>Which one is for you?</h2>
<p class="sub">Pick the line that sounds most like you. We'll point you to where to start, and what to read next.</p>
<div class="choices" role="group" aria-label="Who are you">{choices}</div>
</div>
<div class="result" id="finder-result" aria-live="polite"><div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/></svg><p>Choose an option to see a recommendation.</p></div></div>
</div>
</div></section>
<script>window.FINDER = {data};</script>"""


def _chips(cat: Catalog) -> str:
    used = {t for p in cat.products for t in p.tags}
    tags = [t for t in TAGS if t in used] + sorted(used - set(TAGS))
    chips = ['<button class="chip" type="button" data-filter="all" aria-pressed="true">All</button>']
    chips += [f'<button class="chip" type="button" data-filter="{_e(t)}" aria-pressed="false">'
              f"{_e(TAGS.get(t, t.capitalize()))}</button>" for t in tags]
    return "".join(chips)


def _facts(p: Product, n: int = 3) -> str:
    return "".join(f"<li><b>{_e(k)}</b>{_e(v)}</li>" for k, v in p.label[:n])


def _card(p: Product, root: str = "") -> str:
    o = _single_live(p)
    if o is not None:
        buy = buy_button(o, label="Buy", extra="btn-sm")
    else:
        buy = f'<a class="btn btn-ghost btn-sm" href="{root}{p.slug}/#buy">Choose edition</a>'
    return f"""<article class="card reveal" data-tags="{_e(" ".join(p.tags))}">
<a class="cover" href="{root}{p.slug}/" tabindex="-1" aria-hidden="true"><img src="{cover_src(p, root)}" alt="" width="800" height="800" loading="lazy" decoding="async"><span class="tag">{_e(p.kind)}</span></a>
<div class="body">
<h3><a href="{root}{p.slug}/">{_e(p.title)}</a></h3>
<p>{_e(p.summary)}</p>
<ul class="facts">{_facts(p)}</ul>
<div class="foot">{_card_price(p)}<div class="actions"><a class="btn btn-ghost btn-sm" href="{root}{p.slug}/">Details</a>{buy}</div></div>
</div>
</article>"""


def _bundle_card(cat: Catalog) -> str:
    b = cat.bundle
    included = [p for p in cat.products if p.slug in {o.product for o in cat.bundle_offers()}]
    tags = sorted({t for p in included for t in p.tags})
    minis = "".join(f'<img src="{cover_src(p, "")}" alt="" width="80" height="80" loading="lazy">'
                    for p in included)
    return f"""<article class="card card-bundle reveal" data-tags="{_e(" ".join(tags))}">
<div><p class="eyebrow">The bundle</p><h3>{_e(b.title)}</h3><p>{_e(b.summary)}</p></div>
<div class="minis">{minis}</div>
<div>{_price(b.offer)}
{buy_button(b.offer, label="Buy the bundle", extra="btn-block")}</div>
</article>"""


def _bundle_band(cat: Catalog) -> str:
    b = cat.bundle
    rows = []
    for o in cat.bundle_offers():
        p = cat.product(o.product)
        if p is None:
            continue
        name = p.title if len(p.offers) == 1 else f"{p.title} ({o.name})"
        rows.append(f"<li><span>{_e(name)}</span><span>{_e(money(o.price_usd) or 'Price not set yet')}</span></li>")
    covers = "".join(
        f'<img src="{cover_src(p, "")}" alt="" width="92" height="92" loading="lazy">'
        for p in cat.products if p.slug in {o.product for o in cat.bundle_offers()})
    total = cat.separate_total()
    compare = (f"Bought one by one today: {money(total)}. " if total is not None else "")
    guarantee = all(_guarantee(cat.product(o.product)) for o in cat.bundle_offers()  # type: ignore[arg-type]
                    if cat.product(o.product) is not None)
    return f"""<section class="bundle" aria-labelledby="bundle-h"><div class="wrap">
<div>
<p class="eyebrow">One checkout</p>
<h2 id="bundle-h">{_e(b.title)}</h2>
<p style="margin-top:12px;max-width:52ch">{_e(b.summary)}</p>
<ul class="incl">{"".join(rows)}</ul>
<div class="covers">{covers}</div>
</div>
<div class="offer">
<p class="pr">{_price(b.offer)}</p>
{buy_button(b.offer, label="Buy the bundle", extra="btn-block")}
{_assure(guarantee)}
<p class="fine">{compare}{_e(b.terms_note)} <a class="fine-link" href="{b.slug}/">What's in the bundle</a></p>
</div>
</div></section>"""


def _row_value(p: Product, name: str, default: str = "") -> str:
    return next((v for k, v in p.label if k.lower() == name.lower()), default)


def _compare(cat: Catalog) -> str:
    rows = []
    for p in cat.products:
        fmt = _row_value(p, "Format", p.label[0][1] if p.label else "")
        need = _row_value(p, "You need", "")
        rows.append(
            f'<tr><th scope="row"><a href="{p.slug}/">{_e(p.title)}</a><br>'
            f'<span class="muted">{_e(p.kind)}</span></th><td>{_e(fmt)}</td>'
            f'<td>{_e(need) or "&mdash;"}</td><td class="p">{_e(_price_text(p))}</td></tr>')
    return f"""<section id="compare" aria-labelledby="compare-h"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Side by side</p><h2 id="compare-h">Compare the titles</h2><p>Format, what you need before you start, and the price, as each product page states them.</p></div></div>
<p class="table-hint">Swipe the table sideways to see every column.</p><div class="table-wrap reveal"><table class="compare">
<thead><tr><th scope="col">Title</th><th scope="col">Format</th><th scope="col">You need</th><th scope="col">Price</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
</div></section>"""


def _home_faq(cat: Catalog) -> str:
    days = cat.store["download_link_days"]
    email = _e(cat.contact_email)
    if _store_guarantee(cat):
        useful = (f"Every title has a {GUARANTEE}: email {email} within 14 days of purchase "
                  "from the address you paid with for a full refund, no questions asked.")
    else:
        useful = ("Refund terms are set per title and listed on the "
                  '<a href="refunds/">refunds page</a>.')
    qa = [
        ("How do I get my files?",
         ("Checkout happens on Stripe. As soon as payment goes through, Stripe sends you "
          "straight to your own access page with every download. Bookmark it: each "
          f"download link works for {days} days, and opening the page again makes fresh "
          "ones.")),
        ("What if it isn't useful?", useful),
        ("I lost my access page. What now?",
         ("Write from the address you paid with. The link is sent to that address, and "
          "only to that address.")),
        ("Who can use what I buy?",
         ("Each product page states its licence, under Refunds and licence. Please don't "
          "resell the files or share them publicly.")),
        ("Does this site track me?",
         ("No. The site sets no cookies and runs no analytics or third-party scripts. The "
          "fonts and images are served from this site, and its one script (the theme "
          "toggle, the menu and this page's finder) makes no request. Stripe handles "
          "payment on its own page.")),
        ("Do the courses promise income or results?",
         "No. They teach methods. Nothing here is legal, medical, tax or financial advice."),
    ]
    items = "".join(f"<details><summary>{_e(q)}</summary><div class=\"ans\"><p>{a}</p></div>"
                    "</details>" for q, a in qa)
    return f"""<section id="faq" aria-labelledby="faq-h" style="padding-top:0"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Questions</p><h2 id="faq-h">Before you buy</h2></div></div>
<div class="faq acc">{items}</div>
</div></section>"""


def _strip_item(icon: str, head: str, sub: str) -> str:
    return (f'<li><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{STRIP_SVGS[icon]}'
            f"</svg><div><b>{head}</b><span>{sub}</span></div></li>")


def _home(cat: Catalog) -> str:
    n = len(cat.products)
    store, author = _e(cat.store["name"]), _e(cat.store["author"])
    email = _e(cat.contact_email)
    guarantee = _store_guarantee(cat)
    stack = "".join(
        f'<a href="{p.slug}/" aria-label="{_e(p.title)}"><img src="{cover_src(p, "")}" alt="" '
        f'width="800" height="800" loading="eager"></a>' for p in cat.products)
    cards = "".join(_card(p) for p in cat.products) + _bundle_card(cat)
    b = cat.bundle
    days = cat.store["download_link_days"]
    refund_item = (_strip_item("shield", "14-day guarantee",
                               "Full refund if it isn't useful, no questions")
                   if guarantee else
                   _strip_item("shield", "Refunds per title",
                               "Each product page states its refund terms"))
    body = f"""<section class="hero"><div class="wrap">
<div>
<p class="eyebrow">{store} &middot; by {author}</p>
<h1>Practical AI that <em>asks first.</em></h1>
<p class="lede">Courses, kits and ready-to-import workflows for putting AI to work in a small business or at home, without handing it your money, your data or the final say.</p>
<div class="hero-ctas">
<a class="btn btn-ink" href="#catalog">Browse the {n} titles {ARROW_SVG}</a>
<a class="btn btn-ghost" href="#finder">Find the right one for you</a>
</div>
{_assure(guarantee)}
</div>
<div class="stack" aria-label="The titles">{stack}</div>
</div></section>

<section class="strip" aria-label="Why buy here"><div class="wrap"><ul>
{_strip_item("bolt", "Instant delivery", "Your access page opens right after checkout")}
{refund_item}
{_strip_item("lock", "Stripe checkout", "We never see your card details")}
{_strip_item("eye", "No tracking", "No cookies, analytics or third-party scripts")}
</ul></div></section>

{_finder(cat)}

<section id="catalog" aria-labelledby="catalog-h" style="padding-top:0"><div class="wrap">
<div class="section-head">
<div><p class="eyebrow">The catalog</p><h2 id="catalog-h">{n} titles, one approach</h2><p>Every count on these pages was read from the files you download: pages, lessons, prompts, workflows.</p></div>
<div class="filters" role="group" aria-label="Filter titles">{_chips(cat)}</div>
</div>
<p id="filter-status" class="sr" aria-live="polite"></p>
<div class="grid">{cards}</div>
<p class="no-results" hidden>No titles match that filter.</p>
</div></section>

{_bundle_band(cat)}

{_compare(cat)}

<section id="how" aria-labelledby="how-h" style="padding-top:0"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">How buying works</p><h2 id="how-h">Three steps, about a minute</h2></div></div>
<ol class="steps">
<li class="reveal"><h3>Pay on Stripe</h3><p>Buy opens Stripe's secure checkout. We never see your card.</p></li>
<li class="reveal"><h3>Land on your access page</h3><p>Stripe sends you straight back to a page with your downloads. Bookmark it.</p></li>
<li class="reveal"><h3>Download</h3><p>Each download link works for {days} days. Open your access page again for fresh ones.</p></li>
</ol>
</div></section>

<section aria-labelledby="about-h" style="padding-top:0"><div class="wrap">
<div class="about reveal">
<div class="monogram">{BRAND_SVG}</div>
<div><p class="eyebrow">Who's behind this</p><h2 id="about-h" style="font-size:var(--text-xl)">Written by Miro, counted from the files</h2>
<p>{store} is run by {author}. Every figure on these pages (pages, lessons, prompts, workflows) was read from the files you download, on {_e(cat.store["facts_checked"])}, and each title says plainly who it is for and who it isn't.</p>
<p>Questions before you buy? Write to <a href="mailto:{email}">{email}</a>. Messages reach Miro directly.</p></div>
</div>
</div></section>

{_home_faq(cat)}

<section class="cta-band" style="padding-top:0"><div class="wrap"><div class="box reveal">
<h2>Start with one. Keep the method for good.</h2>
<p>Instant download{", " + GUARANTEE if guarantee else ""}, and no subscription.</p>
<div class="hero-ctas"><a class="btn btn-ink" href="#catalog">Browse the catalog</a><a class="btn btn-ghost" href="{b.slug}/">See the bundle{" &mdash; " + money(b.offer.price_usd) if b.offer.price_usd is not None else ""}</a></div>
</div></div></section>"""
    return _page(cat, title=cat.store["name"],
                 description="Courses, kits and workflows for using AI safely at work and "
                             "at home, by Miro. Instant download, payments by Stripe.",
                 body=body, depth=0)


# ------------------------------------------------------------------ product

def _spec(cat: Catalog, rows: Iterable[tuple[str, str]], title: str = "Contents") -> str:
    body = "".join(f'<div class="row"><dt>{_e(n)}</dt><dd>{_e(v)}</dd></div>'
                   for n, v in rows)
    return (f'<p class="eyebrow" style="margin-top:24px">{_e(title)}</p>'
            f'<dl class="spec" aria-label="{_e(title)}">{body}</dl>'
            f'<p class="spec-note">Counted from the files on {_e(cat.store["facts_checked"])}.</p>')


def _delivery_note(p: Product) -> str:
    if p.refund:
        return ('<p class="spec-note">Delivered right after payment, on your own access page. '
                '<a href="../refunds/">Refunds</a></p>')
    return ('<p class="spec-note">Refund terms for this title aren\'t published yet, so '
            "it isn't on sale. <a href=\"../refunds/\">Refunds</a></p>")


def _buy_box(p: Product) -> str:
    if len(p.offers) == 1:
        o = p.offers[0]
        return f"""<div class="buybox" id="buy">
<p class="pr">{_price(o)}</p>
{buy_button(o, extra="btn-block")}
{_assure(_guarantee(p))}
{_delivery_note(p)}
</div>"""
    cards = []
    for o in p.offers:
        cards.append(f"""<article class="edition" id="{o.id}">
<div><h3>{_e(o.name)}</h3><p class="muted">{_e(o.blurb)}</p></div>
<span class="ep">{_price(o)}</span>
{buy_button(o, extra="btn-sm")}
</article>""")
    return f"""<div class="buybox" id="buy"><p class="eyebrow" style="margin-bottom:12px">Choose your edition</p>
<div class="editions">{"".join(cards)}</div>
{_assure(_guarantee(p))}
{_delivery_note(p)}
</div>"""


def _product_cover(p: Product) -> str:
    """The cover, plus thumbs to switch between edition covers when the
    offers have their own (app.js swaps the image; without it the first
    cover stays)."""
    covers = [(o.name, o.cover) for o in p.offers if o.cover]
    if len({c for _n, c in covers}) < 2:
        return f'<div class="p-cover">{_cover(p, "../", eager=True)}</div>'
    thumbs = "".join(
        f'<button type="button" aria-pressed="{"true" if i == 0 else "false"}" '
        f'aria-label="{_e(name)} cover" data-src="../assets/{_e(c)}" '
        f'data-alt="Cover of {_e(name)}"><img src="../assets/{_e(c)}" alt="" '
        f'width="72" height="72" loading="lazy"></button>'
        for i, (name, c) in enumerate(covers))
    return (f'<div class="p-cover">{_cover(p, "../", eager=True)}'
            f'<div class="thumbs">{thumbs}</div></div>')


STAR_FULL, STAR_EMPTY = "★", "☆"
_STAR_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _reviews_section(p: Product, reviews: list[dict]) -> str:
    """Approved verified-buyer reviews for this product (reviews.py), or
    nothing: the page never carries a review nobody wrote."""
    mine = [r for r in reviews if r.get("slug") == p.slug]
    if not mine:
        return ""
    items = []
    for r in mine:
        n = max(1, min(5, int(r.get("stars") or 0)))
        stars = (f'<span class="stars" role="img" aria-label="{_STAR_WORDS[n]} of five">'
                 f"{STAR_FULL * n}{STAR_EMPTY * (5 - n)}</span>")
        who = _e(r["name"]) + ", verified buyer" if r.get("name") else "Verified buyer"
        paras = "".join(f"<p>{_e(x)}</p>" for x in str(r.get("text") or "").split("\n\n") if x.strip())
        items.append(f'<li class="review">{stars}<div class="review-body">{paras}</div>'
                     f'<p class="review-who">{who}, {_e(r.get("month") or "")}</p></li>')
    return f"""<section id="reviews" aria-labelledby="reviews-h" style="padding-top:0"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">From buyers</p><h2 id="reviews-h">Written on their access page</h2>
<p>Written by people who bought it, on their access page, and read before they appear here. Nothing else is shown.</p></div></div>
<ul class="reviews">{"".join(items)}</ul>
</div></section>"""


def _sticky_buy(p: Product) -> str:
    o = _single_live(p)
    cta = (buy_button(o, extra="btn-sm") if o is not None else
           '<a class="btn btn-ghost btn-sm" href="#buy">Choose edition</a>')
    sub = _price_text(p) + (f" &middot; {GUARANTEE}" if _guarantee(p) else "")
    return f"""<div class="sticky-buy"><div class="wrap">
<img src="{cover_src(p, "../")}" alt="">
<div class="t"><b>{_e(p.title)}</b><span>{sub}</span></div>
{cta}
</div></div>"""


def _product(cat: Catalog, p: Product, reviews: list[dict] | None = None) -> str:
    benefits = "".join(f"<li>{TICK_SVG}<span>{_e(b)}</span></li>" for b in p.benefits)
    inside = "".join(
        f"<details{' open' if i == 0 else ''}><summary>{_e(t)}</summary><ul class=\"lessons\">"
        + "".join(f"<li>{_e(x)}</li>" for x in items) + "</ul></details>"
        for i, (t, items) in enumerate(p.inside))
    audience = "".join(f"<li>{_e(x)}</li>" for x in p.audience)
    not_for = "".join(f"<li>{_e(x)}</li>" for x in p.not_for)
    faq = "".join(f"<details><summary>{_e(q)}</summary><div class=\"ans\"><p>{_e(a)}</p></div>"
                  "</details>" for q, a in p.faq)
    notice = f'<p class="notice">{_e(p.notice)}</p>' if p.notice else ""
    pair = cat.product(p.pairs_with)
    pair_html = pair_link = ""
    if pair is not None:
        pair_link = '<a href="#pair">Pairs well with</a>'
        pair_html = f"""<section id="pair" style="padding-top:0"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Pairs well with</p><h2>Read it next</h2></div></div>
<div class="pair"><img src="{cover_src(pair, "../")}" alt="" width="140" height="140" loading="lazy">
<div><h3>{_e(pair.title)}</h3><p>{_e(p.pairs_reason)}</p></div>
<div class="side">{_from_price(pair)}<a class="btn btn-ghost btn-sm" href="../{pair.slug}/">See {_e(pair.title)}</a></div></div>
</div></section>"""
    refund = _e(p.refund) if p.refund else (
        "Not published yet. This title stays off sale until its refund terms are set.")
    licence = _e(p.licence) if p.licence else (
        'Not published yet. Until it is, the <a href="../terms/">terms of sale</a> apply.')
    body = f"""{_crumbs("../", p.title)}
<section class="p-hero"><div class="wrap">
{_product_cover(p)}
<div class="p-info">
<span class="tag tag-accent">{_e(p.kind)}</span>
<h1>{_e(p.title)}</h1>
<p class="lede">{_e(p.summary)}</p>
{notice}
{_buy_box(p)}
{_spec(cat, p.label)}
</div>
</div></section>

<nav class="p-nav" aria-label="On this page"><div class="wrap">
<a href="#learn">What you'll learn</a><a href="#inside">What's inside</a><a href="#fit">Who it's for</a>{pair_link}<a href="#questions">Questions</a><a href="#terms">Refunds &amp; licence</a>
</div></nav>

<section id="learn"><div class="wrap two">
<div class="intro"><p class="eyebrow">What you'll be able to do</p><h2>Outcomes, not hype</h2><div style="margin-top:16px"><p>{_e(p.intro)}</p></div></div>
<ul class="outcomes">{benefits}</ul>
</div></section>

<section id="inside" style="padding-top:0"><div class="wrap two">
<div><p class="eyebrow">What's inside</p><h2>Every section, listed</h2><p class="muted" style="margin-top:12px">Open a section to see each lesson, note or file.</p></div>
<div class="acc">{inside}</div>
</div></section>

<section id="fit" style="padding-top:0"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Honest fit</p><h2>Is it right for you?</h2></div></div>
<div class="fit"><div class="yes"><h3>{TICK_SVG} Who it's for</h3><ul>{audience}</ul></div><div class="no"><h3>{CROSS_SVG} Who it isn't for</h3><ul>{not_for}</ul></div></div>
</div></section>

{_reviews_section(p, reviews or [])}
{pair_html}
<section id="questions" style="padding-top:0"><div class="wrap two">
<div><p class="eyebrow">Questions</p><h2>Asked before buying</h2><p class="muted" style="margin-top:12px">Something else? <a href="../contact/">Write to Miro</a>.</p></div>
<div class="acc">{faq}</div>
</div></section>

<section id="terms" style="padding-top:0"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">The small print</p><h2>Refunds and licence</h2></div></div>
<div class="terms-grid">
<div><h3>Refunds</h3><p>{refund} <a href="../refunds/">Refund policy</a></p></div>
<div><h3>Licence</h3><p>{licence}</p></div>
</div>
</div></section>
{_sticky_buy(p)}"""
    return _page(cat, title=p.title, description=p.summary, body=body, depth=1)


# ------------------------------------------------------------------ bundle

def _bundle(cat: Catalog) -> str:
    b: Bundle = cat.bundle
    items = []
    included: set[str] = set()
    for o in cat.bundle_offers():
        p = cat.product(o.product)
        if p is None:
            continue
        included.add(p.slug)
        multi = len(p.offers) > 1
        blurb = o.blurb if multi and o.blurb else p.summary
        solo = f"{_e(o.name)}<b>{_price(o)}</b>" if multi else f"On its own<b>{_price(o)}</b>"
        items.append(f"""<div class="incl-item"><img src="{cover_src(p, "../")}" alt="" width="110" height="110" loading="lazy">
<div><span class="tag">{_e(p.kind)}</span><h3 style="margin-top:8px"><a href="../{p.slug}/" style="color:inherit">{_e(p.title)}</a></h3><p>{_e(blurb)}</p></div>
<div class="solo">{solo}</div></div>""")
    total = cat.separate_total()
    math = ""
    if total is not None:
        math = (f'<div class="math"><span>Bought one by one today: {money(total)}.</span>'
                f"<span>Bundle: <b>{money(b.offer.price_usd)}</b></span></div>")
    guarantee = all(_guarantee(p) for p in cat.products if p.slug in included)
    solo_cards = []
    for p in cat.products:
        if p.slug in included:
            continue
        o = _single_live(p)
        buy = (buy_button(o, extra="btn-sm") if o is not None else
               f'<a class="btn btn-ghost btn-sm" href="../{p.slug}/#buy">Choose edition</a>')
        solo_cards.append(f"""<div class="pair"><img src="{cover_src(p, "../")}" alt="" width="140" height="140" loading="lazy">
<div><span class="tag">Not in the bundle</span><h3 style="margin-top:8px">{_e(p.title)}</h3><p>{_e(p.summary)}</p></div>
<div class="side">{_from_price(p)}<a class="btn btn-ghost btn-sm" href="../{p.slug}/">Details</a>{buy}</div></div>""")
    separately = ""
    if solo_cards:
        separately = f"""<section style="padding-top:0"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Also available</p><h2>Not in the bundle</h2></div></div>
<div class="solo-list">{"".join(solo_cards)}</div>
</div></section>"""
    first = next((cat.product(o.product) for o in cat.bundle_offers()), None) or cat.products[0]
    body = f"""{_crumbs("../", b.title)}
<section class="p-hero"><div class="wrap">
<div>
<span class="tag tag-accent">Bundle</span>
<h1 style="font-size:clamp(2.2rem,1.6rem + 2.6vw,3.8rem);margin-top:12px">{_e(b.title)}</h1>
<p class="lede" style="margin-top:16px">{_e(b.summary)} Delivered right after payment, on one access page with every file.</p>
<div class="buybox" id="buy"><p class="pr">{_price(b.offer)}</p>
{buy_button(b.offer, label="Buy the bundle", extra="btn-block")}
{_assure(guarantee)}
<p class="spec-note">{_e(b.terms_note)}</p></div>
</div>
<div><p class="eyebrow">What's included</p><div class="incl-list">{"".join(items)}</div>
{math}</div>
</div></section>
{separately}
<div class="sticky-buy"><div class="wrap"><img src="{cover_src(first, "../")}" alt=""><div class="t"><b>{_e(b.title)}</b><span>{_e(money(b.offer.price_usd) or "Price not set yet")}</span></div>{buy_button(b.offer, label="Buy the bundle", extra="btn-sm")}</div></div>"""
    return _page(cat, title=b.title, description=b.summary, body=body, depth=1,
                 current="bundle")


# ------------------------------------------------------------------ prose pages

def _prose(cat: Catalog, *, slug: str, title: str, description: str, html: str,
           current: str = "", crumb: str = "") -> tuple[str, str]:
    body = (f'<section class="page"><div class="wrap"><div class="crumbs" style="padding:0">'
            f'<a href="../">Home</a><span>/</span>{_e(crumb or title)}</div>'
            f"<h1>{_e(title)}</h1><div class=\"prose\">{html}</div></div></section>")
    return (f"{slug}/index.html",
            _page(cat, title=title, description=description, body=body, depth=1,
                  current=current))


def _legal(cat: Catalog) -> list[tuple[str, str]]:
    email = _e(cat.contact_email)
    mail = f'<a href="mailto:{email}">{email}</a>'
    name = _e(cat.store["name"])
    author = _e(cat.store["author"])
    days = cat.store["download_link_days"]
    updated = _e(cat.store["facts_checked"])
    refunds = "".join(
        f"<li><b>{_e(p.title)}:</b> "
        + (_e(p.refund) if p.refund else
           "terms not published yet. It isn't on sale until they are.")
        + "</li>" for p in cat.products)
    pages = [
        _prose(cat, slug="terms", title="Terms of sale",
               description=f"Terms for buying from {cat.store['name']}.", html=f"""
<p class="muted">Last updated {updated}.</p>
<p>{name} is run by {author}. These terms cover the digital products sold on this
site.</p>
<h2>What you buy</h2>
<p>Each product is a digital download: PDFs, zip files, videos and the files described on
its page. Nothing is shipped. The product page lists what is inside, and the counts on it
were read from the files themselves.</p>
<h2>Paying</h2>
<p>Prices are in US dollars. Payment is taken by Stripe on its own checkout page; we
never see or store your card details.</p>
<h2>Delivery</h2>
<p>After payment Stripe sends you to your access page, which lists your downloads.
Each download link works for {days} days; opening the access page again makes fresh
ones. Keep the page bookmarked. If you lose it, email {mail} from the address you paid
with and we will send it again.</p>
<h2>Licence</h2>
<p>Each product page states its licence. Unless it says otherwise, the files are for
your own use: please don't resell them or share them publicly.</p>
<h2>Refunds</h2>
<p>Refund terms are set per product and listed on the <a href="../refunds/">refunds
page</a>. When a purchase is refunded, its access page and download links stop
working.</p>
<h2>No promises of results</h2>
<p>The courses teach methods. They don't promise income, grades or accurate AI
answers, and they aren't legal, medical, tax or financial advice.</p>
<h2>Contact</h2>
<p>{mail}</p>"""),
        _prose(cat, slug="privacy", title="Privacy",
               description=f"What {cat.store['name']} collects and why.", html=f"""
<p class="muted">Last updated {updated}.</p>
<h2>This website</h2>
<p>This site sets no cookies and runs no analytics or third-party scripts. The fonts
and images are served from this site. Its one script runs the theme switch, the menu
and the "find your fit" picker on the home page; it sends nothing anywhere and stores
nothing.</p>
<h2>When you buy</h2>
<p>Stripe processes your payment and shares with us what it needs to: your name and
email address, what you bought, the amount, and whether the payment went through or
was refunded. We keep a record of each purchase (the checkout reference, your email,
the products, the amount and its status) so we can deliver your files and help you if
you lose your link.</p>
<h2>Email</h2>
<p>We email you only about your purchase, for example to resend your access link when
you ask. No newsletter is sent from a purchase.</p>
<h2>Your choices</h2>
<p>To see or delete what we hold about you, email {mail}. Stripe's own privacy policy
covers what Stripe keeps.</p>"""),
        _prose(cat, slug="refunds", title="Refunds",
               description=f"Refund terms for {cat.store['name']} products.", html=f"""
<p class="muted">Last updated {updated}.</p>
<p>Refund terms are set per title:</p>
<ul>{refunds}</ul>
<h2>How to ask</h2>
<p>Email {mail} from the address you paid with, and include your Stripe receipt
number if you have it. Refunds go back to the card you paid with, through Stripe.</p>
<p>When a purchase is refunded, its access page and download links stop working.</p>"""),
        _prose(cat, slug="contact", title="Contact", current="contact",
               description=f"How to reach {cat.store['name']}.", html=f"""
<p class="lede">Write here, or email {mail}. Messages reach {author} directly.</p>
<form class="contact" method="post" action="{_e(CONTACT_ACTION)}">
<label><span>Your name</span>
<input name="name" type="text" required maxlength="{NAME_MAX}" autocomplete="name"></label>
<label><span>Your email, so we can reply</span>
<input name="email" type="email" required maxlength="{EMAIL_MAX}" autocomplete="email"></label>
<label><span>Receipt number, if it's about an order <i class="opt">(optional)</i></span>
<input name="order" type="text" maxlength="{ORDER_MAX}" autocomplete="off"></label>
<label><span>Your message</span>
<textarea name="message" required minlength="{MESSAGE_MIN}" maxlength="{MESSAGE_MAX}" rows="7"></textarea></label>
<div class="trap" aria-hidden="true"><label>Leave this field empty
<input name="website" type="text" tabindex="-1" autocomplete="off"></label></div>
<div><button class="btn btn-buy" type="submit">Send message</button></div>
<p class="note">Sending stores your name, email and message so we can reply. Nothing
else, and no newsletter.</p>
</form>
<div class="cards-2">
<div><h3>About an order</h3><p>Write from the address you paid with and include your Stripe
receipt number if you have it.</p></div>
<div id="lost"><h3>Lost your access page?</h3><p>Write from the address you paid with. We'll
send the link to that address, and only to that address.</p></div>
</div>"""),
    ]
    sent = (f'<section class="page"><div class="wrap"><h1>Message sent</h1><div class="prose">'
            f'<p class="lede">Thank you. {author} reads every message and replies by '
            f'email, usually within a day or two.</p>'
            f'<p>If it was about an order, your reply comes to the address you wrote '
            f'from. <a href="../../">Back to the catalog</a>.</p></div></div></section>')
    pages.append(("contact/sent/index.html",
                  _page(cat, title="Message sent", description="Your message was sent.",
                        body=sent, depth=2, current="contact")))
    return pages


def _not_found(cat: Catalog) -> str:
    body = ('<section class="page"><div class="wrap"><h1>This page isn\'t here.</h1>'
            '<div class="prose"><p>The link may be old. <a href="/">Go to the catalog</a>.</p>'
            "</div></div></section>")
    # Served for any missing path, so every link on it is absolute.
    return _page(cat, title="Not found", description="Page not found.", body=body,
                 depth=0, root="/")


# ------------------------------------------------------------------ images

def vault_cover_svg(p: Product) -> str:
    """The Vault Kit has no cover image: draw its real folder tree."""
    lines: list[str] = []
    for _title, items in p.inside[:2]:
        for item in items:
            lines.append(item.split(":")[0])
    y0, step = 250, 44
    rows = []
    for i, line in enumerate(lines):
        depth = 1 if "/" in line else 0
        name = line.split("/")[-1]
        folder = line.split("/")[0] + "/" if depth else ""
        x = 90 + depth * 40
        prefix = f'<tspan fill="#9ea3e8">{_e(folder)}</tspan>' if folder else ""
        rows.append(f'<text x="{x}" y="{y0 + i * step}">{prefix}{_e(name)}</text>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" width="800" height="800" role="img"
 aria-label="{_e(p.cover_alt)}">
<rect width="800" height="800" fill="#0a1a27"/>
<rect x="40" y="40" width="720" height="720" fill="none" stroke="#454a78" stroke-width="2"/>
<text x="90" y="130" fill="#ffffff" font-family="Atkinson Hyperlegible, system-ui, sans-serif"
 font-size="54" font-weight="800" letter-spacing="-1">Claude + Obsidian</text>
<text x="90" y="190" fill="#c9cbf5" font-family="Atkinson Hyperlegible, system-ui, sans-serif"
 font-size="54" font-weight="800" letter-spacing="-1">Vault Kit</text>
<g fill="#eceefe" font-family="ui-monospace, Menlo, monospace" font-size="27">
{"".join(rows)}
</g>
</svg>
"""


# ------------------------------------------------------------------ catalog.json

def public_catalog(cat: Catalog, now: datetime) -> dict[str, object]:
    """What other lanes (the reels) may read: no payment links, no file paths,
    no Stripe ids. `buy_live` is the same predicate as the Buy button."""
    base = cat.base_url
    entries: list[dict[str, object]] = []
    for p in cat.products:
        for o in p.offers:
            anchor = "" if len(p.offers) == 1 else f"#{o.id}"
            cover = o.cover or p.cover or f"covers/{p.slug}.svg"
            entries.append({
                "slug": o.id, "kind": "product", "title": o.name,
                "url": f"{base}/{p.slug}/{anchor}",
                "price_usd": o.price_usd, "currency": CURRENCY,
                "buy_live": o.buy_live,
                "cover": f"{base}/assets/{cover}",
                "summary": o.blurb or p.summary,
            })
    b = cat.bundle
    entries.append({
        "slug": b.slug, "kind": "bundle", "title": b.title,
        "url": f"{base}/{b.slug}/", "price_usd": b.offer.price_usd,
        "currency": CURRENCY, "buy_live": b.offer.buy_live,
        "cover": f"{base}/assets/{cat.products[0].cover or 'icon.svg'}",
        "summary": b.summary,
    })
    return {"generated_at": now.astimezone(UTC).isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "store": cat.store["name"], "url": f"{base}/", "products": entries}


# ------------------------------------------------- search and AI assistants
#
# Miro, 2026-10-06 ("is the search engine optimization complete?"): the
# store publishes a sitemap, an llms.txt that an AI assistant can read in one
# fetch, robots rules that name and allow the assistants' crawlers, and
# schema.org records for the store and each product. Every fact is the
# catalog's own; no rating, review count or sales figure is ever written
# (none exists to cite), and a price appears only when the offer is on sale.

AI_CRAWLERS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot",
               "anthropic-ai", "PerplexityBot", "Google-Extended", "Applebot",
               "Bingbot")


def _page_urls(cat: Catalog) -> list[str]:
    base = cat.base_url
    urls = [f"{base}/"] + [f"{base}/{p.slug}/" for p in cat.products]
    urls.append(f"{base}/{cat.bundle.slug}/")
    urls += [f"{base}/{path.removesuffix('index.html')}" for path, _ in _legal(cat)
             if not path.startswith("contact/sent")]
    return list(dict.fromkeys(urls))


def robots_txt(cat: Catalog) -> str:
    rules = "User-agent: *\nAllow: /\n"
    rules += "".join(f"\nUser-agent: {bot}\nAllow: /\n" for bot in AI_CRAWLERS)
    return rules + f"\nSitemap: {cat.base_url}/sitemap.xml\n"


def sitemap_xml(cat: Catalog, now: datetime) -> str:
    day = now.date().isoformat()
    rows = "".join(f"  <url><loc>{_e(u)}</loc><lastmod>{day}</lastmod></url>\n"
                   for u in _page_urls(cat))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{rows}</urlset>\n")


def llms_txt(cat: Catalog, now: datetime) -> str:
    base = cat.base_url
    lines = [f"# {cat.store['name']}", "",
             f"> {cat.store.get('tagline') or 'Courses, kits and workflows for putting AI to work in a small business or at home.'}",
             "",
             f"- Store: {base}/",
             f"- Contact: {cat.contact_email}",
             ("- Payments: Stripe checkout; downloads delivered on a private page "
              "after payment."), "", "## Titles", ""]
    for p in cat.products:
        live = [o for o in p.offers if o.buy_live]
        price = (", ".join(f"{o.name}: ${o.price_usd:g}" for o in live)
                 if live else "not on sale yet")
        lines.append(f"- [{p.title}]({base}/{p.slug}/): {p.summary} ({price})")
    b = cat.bundle
    if b.offer.buy_live:
        lines.append(f"- [{b.title}]({base}/{b.slug}/): {b.summary} "
                     f"(${b.offer.price_usd:g})")
    lines += ["", (f"Prices as published on {now.date().isoformat()}; the store "
                   "page is the source of truth.")]
    return "\n".join(lines) + "\n"


def _ld(data: dict) -> str:
    # `</` cannot close the script early once escaped.
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>")


def store_ld(cat: Catalog) -> dict:
    return {"@context": "https://schema.org", "@type": "OnlineStore",
            "name": cat.store["name"], "url": f"{cat.base_url}/",
            "email": cat.contact_email}


def product_ld(cat: Catalog, p: Product) -> dict:
    base = cat.base_url
    data: dict = {"@context": "https://schema.org", "@type": "Product",
                  "name": p.title, "description": p.summary,
                  "url": f"{base}/{p.slug}/",
                  "brand": {"@type": "Brand", "name": cat.store["name"]}}
    image = cover_src(p, "")
    if image:
        data["image"] = f"{base}/{image}"
    offers = [{"@type": "Offer", "name": o.name, "price": f"{o.price_usd:g}",
               "priceCurrency": CURRENCY, "availability": "https://schema.org/InStock",
               "url": f"{base}/{p.slug}/"}
              for o in p.offers if o.buy_live]
    if offers:
        data["offers"] = offers
    return data


def _with_ld(html: str, data: dict) -> str:
    return html.replace("</head>", _ld(data) + "\n</head>", 1)


# ------------------------------------------------------------------ assemble

def render(cat: Catalog, now: datetime | None = None,
           reviews: list[dict] | None = None) -> dict[str, bytes]:
    """`reviews`: approved verified-buyer reviews (academy.reviews.approved),
    none by default, so the plain render carries no review at all."""
    now = now or datetime.now(UTC)
    files: dict[str, bytes] = {
        "index.html": _home(cat).encode(),
        f"{cat.bundle.slug}/index.html": _bundle(cat).encode(),
        "404.html": _not_found(cat).encode(),
        "catalog.json": (json.dumps(public_catalog(cat, now), indent=2) + "\n").encode(),
        "robots.txt": robots_txt(cat).encode(),
        "sitemap.xml": sitemap_xml(cat, now).encode(),
        "llms.txt": llms_txt(cat, now).encode(),
    }
    files["index.html"] = _with_ld(files["index.html"].decode(), store_ld(cat)).encode()
    for p in cat.products:
        files[f"{p.slug}/index.html"] = _with_ld(
            _product(cat, p, reviews), product_ld(cat, p)).encode()
        if not p.cover:
            files[f"assets/covers/{p.slug}.svg"] = vault_cover_svg(p).encode()
    for path, html in _legal(cat):
        files[path] = html.encode()
    # style.css, app.js, icon.svg, the fonts and the covers.
    for f in sorted(ASSETS.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            files[f"assets/{f.relative_to(ASSETS).as_posix()}"] = f.read_bytes()
    return files


def build(out_dir: Path, cat: Catalog | None = None,
          now: datetime | None = None, reviews: list[dict] | None = None) -> list[Path]:
    """Write the site into `out_dir` (which must not exist yet, or be empty).
    Blocking; callers on the event loop use asyncio.to_thread."""
    cat = cat or load()
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise ValueError(f"{out_dir} is not empty")
    written = []
    for rel, data in render(cat, now).items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)
    return written


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] != "build":
        print("usage: python -m atlas.modules.academy build <out_dir>",
              file=sys.stderr)
        return 2
    paths = build(Path(argv[2]))
    print(f"wrote {len(paths)} files to {argv[2]}")
    return 0
