"""The ATLAS Academy storefront, generated from `catalog.json`.

    python -m atlas.modules.academy build <out_dir>

`render(catalog, now)` is pure: it returns every file of the site as
{relative path: bytes}. `build` writes them to a directory. Publishing that
directory is `academy.publish_shop` (Tier 1). The store shares ONE host with
the agency landing (the owner, 2026-11-14): https://agency.example.com/ is the landing,
and the whole store is https://agency.example.com/academy/ (2026-11-16): every
page and asset is written under academy/ (/academy/join/, /academy/blog/,
/academy/catalog.json, /academy/assets/ ...) by mount.py, which also rewrites
every link to match. The two files the host serves from its root are 404.html
and sitemap-academy.xml. The old bare paths are 308-redirected by Caddy. So
this build writes NO root index.html (the landing owns /), no robots.txt, and
its sitemap is sitemap-academy.xml; see Caddyfile.public.

The home page follows the owner's design brief of 2026-10-11 ("Learn something
useful. Put it to work.") in the order of the shop master plan of 13 Oct
(F01, docs/shop-master-plan-status.md): one promise and two actions ("Find
my starting point", "Explore free lessons") over a product shelf, three
learning paths, the finder, one featured free lesson, the searchable catalog,
the comparison, an approval-gate demonstration, the four-title bundle with its
arithmetic, the owner, buying and access help, and a closing choice. Every old
anchor (#catalog, #finder, #compare, #bundle, #about, #buying) still lands.

What the site will not say, by construction:

* No review, rating, testimonial, sales count, "limited time" or countdown.
  There is no field for any of them in the catalog, and the tests scan the
  output for the words.
* No number that is not in the catalog. Every figure on a page (pages,
  lessons, prices, the count of titles) is catalog text, read from the built
  files, or arithmetic on it done here at build time (the bundle's separate
  total and difference, a count of an offer's files). The one exception is
  a free lesson's title and summary, which are the tutorial catalog's own
  text (tutorial_series.py).
* No Buy link without a payment link AND a price (`Offer.buy_live`). Until
  then the button is a disabled <button> reading "Coming soon".
* No struck-through price and no "New" flag. The bundle shows what its titles
  cost bought one by one, and the difference, only when every one of those
  prices is set; both are summed from the catalog, never typed in.
* "14-day money-back guarantee" only where a product's own refund text says
  it; the store-wide line only when every product's does.
* No absolute safety promise. The courses teach approval gates, restricted
  actions and review; the copy describes those and their limits.

The blog (blog_pages.py, /blog/) follows the same rules: its posts pass the
same scans, and its own script, assets/blog.js, makes no request and keeps
only the lessons a visitor ticks as finished, in that browser's localStorage.

One first-party script, assets/app.js (menu, the product
shelf, the path filter, the "Find your fit" finder, the comparison, the
approval-gate demonstration, edition choice and the kit view), which makes no
request and stores nothing; the home page also carries one inline script that
only assigns `window.FINDER`, the finder's data, generated here from the
catalog. Every scene is plain HTML first: without the script, or with reduced
motion, the same content reads as a static page. No third-party request: the
fonts and the covers are served from the site itself, and a free lesson is a
self-drawn poster with a plain link out to YouTube.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html import escape
from pathlib import Path
from urllib.parse import quote

from .. import store_origin
from . import logo, mount
from . import offer_panels as op
from . import tutorial_series as ts
from .catalog import Bundle, Catalog, Offer, Product, load, money

ASSETS = Path(__file__).with_name("assets")
CURRENCY = "USD"
# The contact form posts to /public/agency/contact (2026-11-14): stored first, then
# emailed and sent to WhatsApp; the mailto address stays on the page as plain text.
# Recovering a purchase is still an email (it needs a Stripe check).
CONTACT_MAIL = store_origin.CONTACT_MAIL
CONTACT_ACTION = "/public/agency/contact"   # the API route, same origin (Caddyfile.public)
RECOVER_SLUG = "recover"
UPDATES_SLUG = "updates"
SUBSCRIBE_ACTION = "/public/academy/subscribe"   # the API route, same origin
HOME_SLUG = "academy"          # the store home: https://agency.example.com/academy/
JOIN_SLUG = "join"
RECOVER_ACTION = "/public/academy/recover"   # the API route, same origin (Caddyfile.public)
RECOVER_SUBJECT = "Recover my ATLAS Academy purchases"
RECOVER_BODY = "My Stripe receipt number: \n"
SUBJECT_MAX = 200
MESSAGE_MIN, MESSAGE_MAX = 10, 4000

# The free video lessons page (the owner, 2026-10-11) and the channel it links.
VIDEOS_SLUG = "videos"
CHANNEL_URL = "https://www.youtube.com/@example_handle"
YT_EMBED = "https://www.youtube-nocookie.com/embed"
# The one lesson-to-product mapping (shop master plan F05, 13 Oct 2026):
# which paid title a free lesson leads to, and why, in words a visitor reads
# beside the recommendation. Every page that pairs a lesson with a product
# reads this table: the home page, the finder, the product pages and the
# /videos/ cards. ("", "") means "no paid recommendation": the page offers
# the finder instead. EPISODE_PRODUCTS overrides a series for one episode.
# A product with nothing on sale is not shown. the owner changes the pairing here.
# (Before 15 Oct two tables disagreed: the home page sent the Claude series
# to AI for Every Age, plan finding [B].)
LESSON_PRODUCTS: dict[str, tuple[str, str]] = {
    "channel": ("local-business-automation-pack",
                ("The free lessons start with n8n. This pack is the next step: ready "
                "n8n automations for a small business that stop for your check.")),
    "claude": ("claude-obsidian-vault-kit",
               ("Put Claude to work on your own business notes: a starter vault with "
               "templates, a weekly review and prompts.")),
    "n8n": ("local-business-automation-pack",
            ("The same n8n editor, with ready automations for a local business and a "
            "guide that explains every node.")),
    "project": ("local-business-automation-pack",
                ("Claude drafts, n8n runs the steps, a person checks: the pack applies "
                "that pattern to four everyday business tasks.")),
    "business": ("local-business-automation-pack",
                 ("The lead reply and follow-ups from these lessons, as importable n8n "
                 "workflows that prepare drafts for you to send.")),
    "agents": ("safe-ai-operator",
               ("Build the kind of agent these lessons explain, with a closed list of "
               "actions and an approval step. Needs some Python.")),
    "local": ("safe-ai-operator",
              ("An agent you build and run yourself, which picks from a closed list "
              "of actions and asks before it acts. Needs some Python.")),
    # the owner 2026-10-18, video system pack: the three new series lead to no
    # paid title until the owner pairs one.
    "explained": ("", ""),
    "security": ("", ""),
    "future": ("", ""),
}
EPISODE_PRODUCTS: dict[tuple[str, int], tuple[str, str]] = {}

# The refund phrase the store repeats only when a product's own terms say it.
GUARANTEE = "14-day money-back guarantee"

# The main navigation, in order: key, label, href relative to the site root.
# "{bundle}" is the bundle's slug. "Free Lessons" is the /videos/ page;
# "Blog" is /blog/ (blog_pages.py); "Help" is the /help/ page, rendered by
# support_site.files (render()).
NAV = (
    ("agency", "Agency", "/agency/"),
    ("catalog", "Courses", "academy/#catalog"),
    ("ebooks", "Ebooks", "academy/#catalog"),
    ("blog", "Blog", "blog/"),
    ("lessons", "Free lessons", "videos/"),
    ("help", "Help", "help/"),
)

# The three learning paths (brief items 05 and 02): key, label, one line, and
# the catalog tags that put a title on the path. A title is on every path
# one of its tags names; the tests check that every title is on at least one.
PATHS = (
    ("work", "Work & Business",
     "Offers, client notes, replies and selling, with AI doing the drafting.",
     frozenset({"business", "start", "money", "local", "organize"})),
    ("build", "Build & Automate",
     "Workflows and agents that prepare the work, then wait for your approval.",
     frozenset({"build", "automate"})),
    ("home", "Home & Family",
     "Everyday AI at home, scam safety, and a large-print route for readers 60 and over.",
     frozenset({"home", "senior"})),
)
# The finder's optional second question: key, the line the visitor picks.
LEVELS = (
    ("new", "I'm new to AI tools"),
    ("apps", "I'm happy installing apps"),
    ("code", "I write some code"),
)
# The finder's optional third question (plan F04/F24, the setup check): can
# the visitor install software on the computer they'd use? Answers only;
# nothing about the device is read.
SETUPS = (
    ("yes", "Yes, I can install software"),
    ("no", "No: a phone, a tablet or a locked work computer"),
)
# Editorial order per path (plan F04: "an explicit editorial order"): the
# gentlest useful start first. A title not listed follows in catalog order.
# the owner changes the order here.
FINDER_ORDER: dict[str, tuple[str, ...]] = {
    "work": ("30-business-lessons", "claude-obsidian-vault-kit", "sell-with-ai",
             "local-business-automation-pack", "safe-ai-operator"),
    "build": ("local-business-automation-pack", "safe-ai-operator"),
    "home": ("ai-for-every-age", "family-ai-scam-defence-workbook"),
}
FINDER_ALTERNATIVES = 2
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

# The Grid A0 mark (the owner, 2026-10-16), edged for the dark store: logo.py.
BRAND_SVG = logo.mark_svg(dark=True, inline=True)
TICK_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M20 6 9 17l-5-5"/></svg>')
CROSS_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
    'stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>')
MENU_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>')
PLAY_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" '
    'fill="currentColor"/></svg>')
KIT_SVGS = {
    "video": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
    "pdf": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
    "zip": '<path d="M4 7h6l2 2h8v10H4z"/><path d="M12 12v4"/>',
}


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _guarantee(p: Product) -> bool:
    return bool(p.refund and GUARANTEE in p.refund)


def _store_guarantee(cat: Catalog) -> bool:
    return all(_guarantee(p) for p in cat.products)


# ------------------------------------------------------------------ chrome

def _nav_links(cat: Catalog, root: str, current: str) -> str:
    return "".join(
        f'<li><a href="{"" if href.startswith("/") else root}{href.format(bundle=cat.bundle.slug)}"'
        f'{" aria-current=\"page\"" if key == current else ""}>{_e(label)}</a></li>'
        for key, label, href in NAV)


def _page(cat: Catalog, *, title: str, description: str, body: str, depth: int,
          current: str = "", root: str | None = None, head: str = "") -> str:
    """The store's page chrome. `head`: extra first-party <link>/<script>
    tags for one page (the blog's own style, script and feed link)."""
    root = "../" * depth if root is None else root
    head = f"{head}\n" if head else ""
    store = _e(cat.store["name"])
    full_title = f"{_e(title)} | {store}" if title != cat.store["name"] else store
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{full_title}</title>
<meta name="description" content="{_e(description)}">
<meta name="theme-color" content="#0A0D14">
<meta name="color-scheme" content="dark">
<link rel="preload" href="{root}assets/fonts/atkinson-next-var.woff" as="font" type="font/woff" crossorigin>
<link rel="preload" href="{root}assets/fonts/cabinet-800.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{root}assets/style.css">
<link rel="icon" href="{root}assets/icon.svg" type="image/svg+xml">
<link rel="icon" href="{root}assets/icon-192.png" type="image/png" sizes="192x192">
<link rel="icon" href="{root}assets/icon-512.png" type="image/png" sizes="512x512">
<link rel="apple-touch-icon" href="{root}assets/apple-touch-icon.png">
<script src="{root}assets/app.js" defer></script>
{head}</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top"><div class="wrap">
<a class="brand" href="/" aria-label="ATLAS Agency home">{BRAND_SVG}<span>ATLAS <small>Agency</small></span></a>
<nav aria-label="Main"><ul class="nav" id="main-nav">{_nav_links(cat, root, current)}</ul></nav>
<div class="top-actions">
<a class="pv-pill pv-explore" href="{root}{HOME_SLUG}/#catalog">Explore courses &#8599;</a>
<a class="btn btn-sm btn-join" href="{root}{JOIN_SLUG}/"{" aria-current=\"page\"" if current == "join" else ""}>Join / sign in</a>
<button class="icon-btn menu-btn" type="button" aria-expanded="false" aria-controls="main-nav" aria-label="Open menu">{MENU_SVG}</button>
</div>
</div></header>
<main id="main">
{body}
</main>
{_footer(cat, root)}
</body>
</html>
"""


def _subscribe_form(source: str, uid: str) -> str:
    """Free updates signup: an email field and a button, a plain form post that
    works with scripts off (the API answers with a redirect to /updates/thanks/).
    `website` is a honeypot a person never sees; app.js only checks the field
    and shows a busy state, and makes no request."""
    return f"""<form class="sub-form" method="post" action="{SUBSCRIBE_ACTION}" novalidate data-sub-form>
<input type="hidden" name="source" value="{source}">
<p class="sub-trap" aria-hidden="true"><label>Leave this empty <input type="text" name="website" tabindex="-1" autocomplete="off"></label></p>
<div class="sub-row">
<label class="sub-l" for="sub-{uid}">Email address</label>
<input id="sub-{uid}" name="email" type="email" inputmode="email" autocomplete="email" autocapitalize="off" spellcheck="false" maxlength="254" placeholder="you@example.com" required aria-describedby="sub-{uid}-m">
<button class="btn btn-buy" type="submit" data-sub-submit><span class="lbl">Get updates</span><span class="spin" aria-hidden="true"></span></button>
</div>
<p class="sub-msg" id="sub-{uid}-m" role="status" aria-live="polite" data-sub-msg></p>
</form>"""


def _subscribe_band(root: str) -> str:
    """The slim band under the hero: free updates, no purchase, no account."""
    return f"""<section class="sub-band" aria-labelledby="sub-h"><div class="wrap">
<div class="sub-copy"><h2 id="sub-h">Free updates, no purchase needed</h2>
<p>New free lessons and store news by email. Just your address: no account, no password. We email a link to confirm, and every email has a one-click unsubscribe. <a href="{root}privacy/#updates">What we keep</a>.</p></div>
{_subscribe_form("hero", "hero")}
</div></section>"""


def _footer(cat: Catalog, root: str) -> str:
    store, author = _e(cat.store["name"]), _e(cat.store["author"])
    email = _e(cat.contact_email)
    shop = "".join(f'<li><a href="{root}{p.slug}/">{_e(p.title)}</a></li>' for p in cat.products)
    shop += f'<li><a href="{root}{cat.bundle.slug}/">{_e(cat.bundle.title)}</a></li>'
    return f"""<footer class="foot"><div class="wrap">
<div class="cols">
<div><a class="brand" href="{root}{HOME_SLUG}/">{BRAND_SVG}<span>ATLAS <small>Academy</small></span></a>
<p class="about-line">{store} is run by {author}. Payments by Stripe. This site sets no cookies and runs no analytics.</p></div>
<div><h2 class="foot-h">Courses &amp; kits</h2><ul>{shop}</ul></div>
<div><h2 class="foot-h">Help</h2><ul>
<li><a href="{root}{VIDEOS_SLUG}/">Free lessons</a></li>
<li><a href="{root}blog/">Blog</a></li>
<li><a href="{root}help/">Help</a></li>
<li><a href="{root}{HOME_SLUG}/#buying">How buying works</a></li>
<li><a href="{root}contact/">Contact</a></li>
<li><a href="{root}{RECOVER_SLUG}/">Lost your access page?</a></li>
</ul></div>
<div><h2 class="foot-h">Policies</h2><ul>
<li><a href="{root}refunds/">Refunds</a></li>
<li><a href="{root}terms/">Terms of sale</a></li>
<li><a href="{root}privacy/">Privacy</a></li>
</ul></div>
</div>
<div class="foot-sub"><h2 class="foot-h">Free updates</h2>
{_subscribe_form("footer", "foot")}
<p class="sub-fine">Email only. We confirm by email first, and every message has a one-click unsubscribe. <a href="{root}privacy/#updates">Privacy</a></p></div>
<div class="legal"><span>{store}, run by {author}</span><span>Prices in USD. <a href="mailto:{email}">{email}</a> · {_phone_link()}</span></div>
</div></footer>"""


def cover_src(p: Product | Offer, root: str) -> str:
    path = p.cover or (f"covers/{p.slug}.svg" if isinstance(p, Product) else "")
    return f"{root}assets/{path}" if path else ""


def _cover(p: Product, root: str, *, cls: str = "frame", eager: bool = False) -> str:
    loading = "eager" if eager else "lazy"
    return (f'<div class="{cls}"><img src="{cover_src(p, root)}" alt="{_e(p.cover_alt)}" '
            f'width="800" height="800" loading="{loading}" decoding="async"></div>')


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



# ------------------------------------------- paths, formats, lessons, bundle
#
# Design brief 2026-10-11, stages 1-3: what a visitor needs to tell what a
# title is, what it asks of them and which offer fits, all derived from the
# catalog so a page can never disagree with the files.

def product_paths(p: Product) -> tuple[str, ...]:
    """The learning paths a title is on, in PATHS order."""
    return tuple(key for key, _l, _d, tags in PATHS if tags & set(p.tags))


def level_fits(p: Product) -> tuple[str, ...]:
    """Which finder levels a title suits, read from its level words."""
    level = p.level.lower()
    if "python" in level:
        return ("code",)
    if "docker" in level or "setup" in level:
        return ("apps", "code")
    return tuple(k for k, _l in LEVELS)


def needs_install(p: Product) -> bool:
    """True when the title's own requirement text says software must be
    installed: Python, Docker or a named app. Read, never guessed."""
    words = f"{p.level} {p.level_detail} {_needs(p)}".lower()
    words = re.sub(r"\b(?:no software|nothing) to install\b", "", words)
    return any(w in words for w in ("python", "docker", " app ", " app.", "install"))


def finder_route(cat: Catalog, goal: str = "", level: str = "",
                 setup: str = "") -> dict[str, object]:
    """The finder's answer for one set of answers (plan F04): eligibility
    first, then the editorial order. {"best": slug | "", "alts": [slug],
    "out": [slug]}: `out` are titles on the chosen path that the answers rule
    out, shown with their own requirement text. Every combination is worked
    out here at build time, so the page only looks the answer up."""
    on_path = [p for p in cat.products if not goal or goal in product_paths(p)]
    order = FINDER_ORDER.get(goal) or tuple(
        dict.fromkeys(s for key in FINDER_ORDER for s in FINDER_ORDER[key]))
    rank = {slug: i for i, slug in enumerate(order)}
    on_path.sort(key=lambda p: rank.get(p.slug, len(rank)))

    def fits(p: Product) -> bool:
        if level and level not in level_fits(p):
            return False
        return not (setup == "no" and needs_install(p))

    ok = [p.slug for p in on_path if fits(p)]
    return {"best": ok[0] if ok else "", "alts": ok[1:1 + FINDER_ALTERNATIVES],
            "out": [p.slug for p in on_path if not fits(p)]}


def finder_routes(cat: Catalog) -> dict[str, dict[str, object]]:
    """Every answer combination, keyed "goal|level|setup" ("" = unanswered)."""
    goals = [""] + [k for k, *_ in PATHS]
    levels = [""] + [k for k, _l in LEVELS]
    setups = [""] + [k for k, _l in SETUPS]
    return {f"{g}|{lv}|{s}": finder_route(cat, g, lv, s)
            for g in goals for lv in levels for s in setups if g or lv or s}


def _kind_of(label: str) -> str:
    """'video', 'pdf' or 'zip': what one delivered file is, from its label."""
    if label.endswith("(video)"):
        return "video"
    if "(zip" in label:
        return "zip"
    return "pdf" if "(PDF" in label else "zip"


def formats(offers: Iterable[Offer]) -> list[str]:
    """The format words for these offers, from the files they deliver:
    'Video lessons', 'PDF', 'Files (zip)'. One vocabulary everywhere."""
    labels = [f.label for o in offers for f in o.files]
    out = []
    if any(_kind_of(x) == "video" for x in labels):
        out.append("Video lessons")
    if any("PDF" in x and _kind_of(x) != "video" for x in labels):
        out.append("PDF")
    if any(_kind_of(x) == "zip" for x in labels):
        out.append("Files (zip)")
    return out


def format_line(p: Product) -> str:
    return ", ".join(formats(p.offers))


def _needs(p: Product) -> str:
    return _row_value(p, "You need", p.level_detail)


def _row_value(p: Product, name: str, default: str = "") -> str:
    return next((v for k, v in p.label if k.lower() == name.lower()), default)


def bundle_math(cat: Catalog) -> tuple[list[float], float, float, float] | None:
    """(each included offer's price, their sum, the bundle price, sum minus
    bundle), or None while any of those prices is unset. Summed here from
    the catalog at build time, never typed into copy."""
    total = cat.separate_total()
    price = cat.bundle.offer.price_usd
    if total is None or price is None:
        return None
    parts = [float(o.price_usd) for o in cat.bundle_offers() if o.price_usd is not None]
    return parts, total, price, round(total - price, 2)


def _bundle_sum(cat: Catalog) -> str:
    """The bundle arithmetic as one sentence pair, or '' while unset."""
    m = bundle_math(cat)
    if m is None:
        return ""
    parts, total, price, diff = m
    terms = " + ".join(money(x) for x in parts)
    if diff > 0:
        tail = f"The bundle is {money(price)}, {money(diff)} less than buying them one by one."
    elif diff == 0:
        tail = f"The bundle is {money(price)}, the same as buying them one by one."
    else:
        tail = (f"The bundle is {money(price)}, {money(-diff)} more than buying them one "
                "by one.")
    return (f'<p class="sum"><span class="sum-terms">{terms} = <b>{money(total)}</b> '
            f"bought separately.</span> <span>{tail}</span></p>")


def _bundle_titles(cat: Catalog) -> tuple[list[tuple[Product, Offer]], list[Product]]:
    """(included (product, offer) pairs, excluded products), catalog order."""
    inc = [(p, o) for o in cat.bundle_offers() if (p := cat.product(o.product)) is not None]
    slugs = {p.slug for p, _o in inc}
    return inc, [p for p in cat.products if p.slug not in slugs]


def _incl_name(p: Product, o: Offer) -> str:
    if len(p.offers) == 1:
        return p.title
    if o.id.endswith("-both"):
        return f"{p.title} (both editions)"
    return f"{p.title} ({o.name})"


def valid_lessons(lessons: Iterable[dict] | None) -> list[dict]:
    """Published tutorials the pages may link to: each has a title and a
    well-formed YouTube id. Order kept (newest first)."""
    out = []
    for r in lessons or ():
        vid = str(r.get("video_id") or "")
        if VIDEO_ID_RE.match(vid) and r.get("title"):
            out.append(r)
    return out


def lesson_url(r: dict) -> str:
    return f"https://www.youtube.com/watch?v={quote(str(r['video_id']))}"


def lesson_pairing(cat: Catalog, series: str,
                   episode: int | None = None) -> tuple[Product, str] | None:
    """(product, why) a free lesson leads to, from LESSON_PRODUCTS; None for
    "no paid recommendation" or a slug the catalog doesn't have."""
    slug, why = (EPISODE_PRODUCTS.get((series, episode)) if episode is not None
                 else None) or LESSON_PRODUCTS.get(series, ("", ""))
    p = cat.product(slug) if slug else None
    return (p, why) if p is not None else None


def _lesson_pair(cat: Catalog, r: dict) -> tuple[Product, str] | None:
    ep = r.get("episode")
    return lesson_pairing(cat, str(r.get("series") or ""),
                          int(ep) if isinstance(ep, int) else None)


def _lesson_next(cat: Catalog, r: dict) -> Product | None:
    pair = _lesson_pair(cat, r)
    return pair[0] if pair else None


def _lesson_paths(cat: Catalog, r: dict) -> tuple[str, ...]:
    nxt = _lesson_next(cat, r)
    return product_paths(nxt) if nxt is not None else tuple(k for k, *_ in PATHS)


def _poster(r: dict, *, cls: str = "poster") -> str:
    """A free lesson's poster, drawn here: nothing loads from YouTube."""
    mark = _e(r.get("mark") or "ATLAS Academy")
    return (f'<div class="{cls}" aria-hidden="true"><span class="poster-mark">{mark}</span>'
            f'<span class="poster-title">{_e(r["title"])}</span>'
            f'<span class="poster-play">{PLAY_SVG}</span></div>')


# ------------------------------------------------------------------ scenes
#
# Stage 4, the signature motion: a product shelf, an approval gate and a kit
# that unfolds. Each is complete HTML first; app.js only adds the motion and
# the controls, and skips the motion under prefers-reduced-motion.

def _gate(cat: Catalog) -> str:
    pack = cat.product("local-business-automation-pack")
    op = cat.product("safe-ai-operator")
    learn = " and ".join(f'<a href="{p.slug}/">{_e(p.title)}</a>' for p in (pack, op) if p)
    return f"""<section id="how-it-works" class="gate-sec" aria-labelledby="gate-h"><div class="wrap gate-wrap">
<div class="gate-copy">
<h2 id="gate-h">See how asks‑first works</h2>
<p>A sample workflow of the kind the courses teach. A customer message comes in, the AI prepares a reply, and the workflow stops at a review step until a person decides.</p>
<p class="muted">A simulation on this page. Nothing is sent, and no AI is called. Learn the real version in {learn}.</p>
<div class="gate-controls"><button class="btn btn-ink" type="button" data-gate-start>Run the example</button><button class="btn btn-ghost" type="button" data-gate-reset hidden>Start again</button></div>
</div>
<ol class="gate" data-gate>
<li class="gate-step" data-step="request"><p class="step-name">Request</p>
<div class="msg msg-in"><p class="msg-from">Customer message</p><p>Hi, do you have time on Saturday morning for a boiler check? Thanks, Sam</p></div></li>
<li class="gate-step" data-step="draft"><p class="step-name">Draft</p>
<div class="msg msg-draft"><p class="msg-from">Draft reply, prepared by the AI</p><p>Hi Sam, thanks for asking. Saturday morning is open. Shall I book you in? We will confirm the time by text.</p></div></li>
<li class="gate-step gate-stop" data-step="review"><p class="step-name">Review</p>
<div class="stop"><p class="stop-head">Paused for review. Nothing has been sent.</p>
<p class="stop-why">Not on this workflow's action list: payments, refunds, deleting records. Those stay with a person.</p>
<div class="stop-actions"><button class="btn btn-gold btn-sm" type="button" data-gate-approve disabled>Approve the draft</button><button class="btn btn-ghost btn-sm" type="button" data-gate-edit disabled>Ask for changes</button></div></div></li>
<li class="gate-step" data-step="done"><p class="step-name">After review</p>
<div class="msg msg-done" data-gate-result><p>Approved: in the real workflow, you send it yourself or let the approved step run. Ask for changes, and it goes back to draft. This page sends nothing.</p></div></li>
</ol>
</div></section>"""


def _kit_tiles(o: Offer) -> str:
    videos = [f.label for f in o.files if _kind_of(f.label) == "video"]
    tiles = []
    if videos:
        first = "".join(
            f"<li>{_e(re.sub(r'^Lesson [^:]*: ', '', v).removesuffix(' (video)'))}</li>"
            for v in videos[:3])
        tiles.append(
            f'<li class="kit-tile" data-kind="video"><svg viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.8" aria-hidden="true">{KIT_SVGS["video"]}</svg>'
            f'<h3>{len(videos)} video lessons</h3><p class="muted">Watch on your access page or '
            f'download. The first ones:</p><ul>{first}</ul></li>')
    for f in o.files:
        kind = _kind_of(f.label)
        if kind == "video":
            continue
        name = "PDF" if kind == "pdf" else "Download (zip)"
        tiles.append(
            f'<li class="kit-tile" data-kind="{kind}"><svg viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.8" aria-hidden="true">{KIT_SVGS[kind]}</svg>'
            f"<h3>{name}</h3><p>{_e(f.label)}</p></li>")
    return "".join(tiles)


def _kit(p: Product) -> str:
    """What one purchase delivers, from the offer's own file list: a cover
    that opens into its video lessons, PDFs and files."""
    multi = len(p.offers) > 1
    kits = "".join(
        f'<div class="kit-offer" data-offer="{_e(o.id)}">'
        + (f'<h3 class="kit-ed">{_e(o.name)}</h3>' if multi else "")
        + f'<ul class="kit-tiles">{_kit_tiles(o)}</ul></div>' for o in p.offers)
    # The scene's data (offer_panels.kit_scene_json) rides on a data-*
    # attribute: the store's one first-party script reads it, so no inline
    # JSON script is added; the In the box list is the no-motion fallback.
    return f"""<section id="kit" aria-labelledby="kit-h" data-kit-scene="{_e(op.kit_scene_json(p))}"><div class="wrap">
<div class="section-head"><div><h2 id="kit-h">What you get</h2>
<p>Every file the purchase delivers, as listed on your access page.{" Choose an edition above to see its files." if multi else ""}</p></div>
<button class="btn btn-ghost btn-sm" type="button" data-kit-toggle hidden aria-expanded="true">Close the kit</button></div>
<div class="kit" data-kit><div class="kit-cover" aria-hidden="true"><img src="../{_e(cover_src(p, ""))}" alt="" width="800" height="800" loading="lazy"></div>
<div class="kit-body">{kits}</div></div>
</div></section>"""


# ------------------------------------------------------------------ home

def _finder_data(cat: Catalog, lessons: list[dict] | None = None) -> dict[str, object]:
    """`window.FINDER`: every title with the words the finder shows (its own
    summary and requirement, never invented copy), the paths and levels it
    fits, and, per path, the newest free lesson. Strings are HTML-escaped
    here because app.js writes them into the page."""
    def rec(p: Product) -> dict[str, object]:
        return {"slug": p.slug, "title": _e(p.title), "why": _e(p.summary),
                "needs": _e(_needs(p)), "level": _e(p.level), "price": _e(_price_text(p)),
                "href": f"/{p.slug}/", "cover": cover_src(p, "/"),
                "paths": list(product_paths(p)), "fits": list(level_fits(p)),
                "install": needs_install(p)}
    first: dict[str, dict[str, str]] = {}
    for r in valid_lessons(lessons):
        for key in _lesson_paths(cat, r):
            first.setdefault(key, {"title": _e(r["title"]), "href": lesson_url(r)})
    return {"products": [rec(p) for p in cat.products], "lessons": first,
            "routes": finder_routes(cat),
            "paths": {k: _e(label) for k, label, *_ in PATHS}}


def _finder(cat: Catalog, lessons: list[dict]) -> str:
    goals = "".join(
        f'<label class="opt-card"><input type="radio" name="goal" value="{k}">'
        f"<span><b>{_e(label)}</b><small>{_e(line)}</small></span></label>"
        for k, label, line, _t in PATHS)
    levels = "".join(
        f'<label class="opt-card"><input type="radio" name="level" value="{k}">'
        f"<span><b>{_e(label)}</b></span></label>" for k, label in LEVELS)
    setups = "".join(
        f'<label class="opt-card"><input type="radio" name="setup" value="{k}">'
        f"<span><b>{_e(label)}</b></span></label>" for k, label in SETUPS)
    free = ('<a class="btn btn-ghost" href="#free-lesson">Watch a free lesson first</a>'
            if lessons else '<a class="btn btn-ghost" href="videos/">Watch a free lesson first</a>')
    data = json.dumps(_finder_data(cat, lessons), ensure_ascii=True).replace("</", "<\\/")
    return f"""<section id="finder" aria-labelledby="finder-h"><div class="wrap">
<div class="finder">
<div class="finder-q">
<h2 id="finder-h">Find your starting point</h2>
<p class="sub">Three optional questions. No email, no account. You get one starting point, up to two alternatives, and the titles your answers rule out, with the reason.</p>
<form class="finder-form" data-finder>
<fieldset><legend>What do you want to do?</legend><div class="opts">{goals}</div></fieldset>
<fieldset><legend>How comfortable are you with tech?</legend><div class="opts opts-row">{levels}</div></fieldset>
<fieldset><legend>Can you install software on the computer you'd use?</legend><div class="opts opts-row">{setups}</div></fieldset>
<p class="finder-actions"><button class="btn btn-ghost btn-sm" type="reset">Clear answers</button>{free}</p>
</form>
</div>
<div class="result" id="finder-result" aria-live="polite"><p class="empty">Answer any of the questions to see where to start and what each title asks of you. Without them, the whole catalog is below.</p></div>
</div>
</div></section>
<script>window.FINDER = {data};</script>"""


def _chips() -> str:
    chips = ['<button class="chip" type="button" data-filter="all" aria-pressed="true">All titles</button>']
    chips += [f'<button class="chip" type="button" data-filter="{k}" aria-pressed="false">'
              f"{_e(label)}</button>" for k, label, *_ in PATHS]
    return "".join(chips)


def _format_key(word: str) -> str:
    """'Files (zip)' -> 'files-zip': a format word as a filter value."""
    return re.sub(r"[^a-z]+", "-", word.lower()).strip("-")


def _catalog_tools(cat: Catalog) -> str:
    """Plan F02/F37: search over the catalog's own words. Hidden until
    app.js runs; without it the whole catalog shows. The query and the
    filters are mirrored into the address so a filtered view can be shared."""
    return """<form class="cat-tools pv-tools" data-cat-tools role="search" aria-label="Search the catalog" hidden>
<label class="cat-q"><span class="sr">Search titles, tools and outcomes</span><input type="search" name="q" autocomplete="off" spellcheck="false" placeholder="Search titles, tools and outcomes&hellip;"></label>
<button class="pv-pill" type="button" data-cat-clear>Clear all</button>
</form>"""


# The path cards' mono tag and the number of digits an index needs.
PATH_TAGS = {"work": "BUSINESS", "build": "SYSTEMS", "home": "EVERYDAY"}


def _idx(i: int, total: int) -> str:
    width = max(2, len(str(total)))
    return f"{i:0{width}d}"


def _card_full(p: Product, root: str = "") -> str:
    """The detailed card (cover, facts, two actions) the lesson pages use to
    recommend one title."""
    paths = " ".join(product_paths(p))
    words = " ".join((p.title, p.summary, p.kind, p.level, p.level_detail, format_line(p),
                      _needs(p), " ".join(p.tags))).lower()
    fmt = " ".join(_format_key(f) for f in formats(p.offers))
    return f"""<article class="card" data-paths="{paths}" data-slug="{p.slug}" data-title="{_e(p.title.lower())}" data-search="{_e(words)}" data-fits="{" ".join(level_fits(p))}" data-formats="{fmt}">
<a class="cover" href="{root}{p.slug}/" tabindex="-1" aria-hidden="true"><img src="{cover_src(p, root)}" alt="" width="800" height="800" loading="lazy" decoding="async"></a>
<div class="body">
<p class="kind">{_e(p.kind)}</p>
<h3><a href="{root}{p.slug}/">{_e(p.title)}</a></h3>
<p class="outcome">{_e(p.summary)}</p>
<dl class="facts"><div><dt>Level</dt><dd><span class="level">{_e(p.level)}</span></dd></div>
<div><dt>Format</dt><dd>{_e(format_line(p))}</dd></div>
<div><dt>You need</dt><dd>{_e(_needs(p))}</dd></div></dl>
<div class="card-foot">{_card_price(p)}<div class="actions"><a class="btn btn-ghost btn-sm" href="{root}{p.slug}/#{'peek' if _peek_what(p.slug) else 'kit'}">{_peek_what(p.slug) or 'What you get'}</a><a class="btn btn-ink btn-sm" href="{root}{p.slug}/">View details</a></div></div>
</div>
</article>"""


def _card(p: Product, root: str = "", n: int = 1, total: int = 1) -> str:
    """One catalog card: kind and level, its index, the title, the outcome in
    three lines, then the price and the link to the page. The words the
    search reads (title, summary, kind, level, format, need, tags) travel in
    data-search."""
    paths = " ".join(product_paths(p))
    words = " ".join((p.title, p.summary, p.kind, p.level, p.level_detail, format_line(p),
                      _needs(p), " ".join(p.tags))).lower()
    fmt = " ".join(_format_key(f) for f in formats(p.offers))
    return f"""<article class="card pv-card" data-paths="{paths}" data-slug="{p.slug}" data-title="{_e(p.title.lower())}" data-search="{_e(words)}" data-fits="{" ".join(level_fits(p))}" data-formats="{fmt}">
<div class="pv-card-top"><span class="pv-tag">{_e(p.kind)} &middot; {_e(p.level)}</span><span class="pv-tag pv-idx" aria-label="Title {n} of {total}">{_idx(n, total)} / {_idx(total, total)}</span></div>
<h3><a href="{root}{p.slug}/">{_e(p.title)}</a></h3>
<p class="pv-desc">{_e(p.summary)}</p>
<div class="pv-card-foot">{_from_price(p)}{_peek_chip(p, root)}<a class="pv-more" href="{root}{p.slug}/" aria-label="View details: {_e(p.title)}">View details &#8599;</a></div>
</article>"""


def _paths_section(cat: Catalog) -> str:
    cards = []
    total = len(PATHS)
    for i, (key, label, line, _tags) in enumerate(PATHS, 1):
        cards.append(f"""<a class="path pv-path" href="#catalog" data-path-card="{key}" data-show-path="{key}" aria-label="{_e(label)}: show these titles in the catalog">
<span class="pv-tag">{_idx(i, total)} / {PATH_TAGS[key]}</span>
<h3>{_e(label)} <span aria-hidden="true">&#8599;</span></h3><p>{_e(line)}</p></a>""")
    return f"""<section id="paths" class="pv-sec" aria-labelledby="paths-h"><div class="wrap">
<div class="pv-head"><div><p class="pv-eyebrow">Choose your direction</p><h2 id="paths-h">Three ways in.</h2></div>
<p class="pv-blurb">Pick the path closest to what you want to do. A title can sit on more than one path.</p></div>
<div class="pv-paths">{"".join(cards)}</div>
</div></section>"""


def _method() -> str:
    """The hero's side card: the three-step approval flow the courses teach."""
    steps = (("Request received", "A real task, with clear context"),
             ("AI prepares a draft", "Work is prepared, not sent"),
             ("Review &amp; approve", "You stay in control of the decision"))
    rows = "".join(f'<li><span class="pv-num">{i:02d}</span><div><b>{h}</b><span>{d}</span></div></li>'
                   for i, (h, d) in enumerate(steps, 1))
    return f"""<aside class="pv-method" aria-labelledby="method-h">
<div class="pv-method-head"><h2 id="method-h" class="pv-eyebrow">The Atlas method</h2><span class="pv-chip">Human-approved</span></div>
<ol class="pv-steps">{rows}</ol>
<div class="pv-approve"><b>Approval required</b><span>Nothing moves forward until a person reviews it.</span></div>
</aside>"""


def _featured_lesson(cat: Catalog, lessons: list[dict]) -> str:
    """Brief step 3: one free lesson with its takeaway and the relevant next
    resource. Hidden entirely when no tutorial is published."""
    if not lessons:
        return ""
    r = lessons[0]
    pair = _lesson_pair(cat, r)
    sentences = re.split(r"(?<=[.!?])\s+", str(r.get("summary") or "").strip())
    takeaway = " ".join(sentences[:2])
    take = f'<p class="takeaway">{_e(takeaway)}</p>' if takeaway else ""
    nxt_html = ""
    if pair is not None:
        nxt, why = pair
        nxt_html = (f'<div class="next"><p class="next-h">Want the structured version?</p>'
                    f'<a class="next-card" href="{nxt.slug}/"><img src="{cover_src(nxt, "")}" alt="" '
                    f'width="72" height="72" loading="lazy"><span><b>{_e(nxt.title)}</b>'
                    f'<small>{_e(nxt.kind)}, {_e(_price_text(nxt))}</small></span></a>'
                    f'<p class="why-pair">{_e(why)}</p></div>')
    else:
        nxt_html = ('<div class="next"><p class="next-h">No paid title follows this lesson '
                    'directly.</p><a href="#finder">Find your starting point</a></div>')
    return f"""<section id="free-lesson" aria-labelledby="free-h"><div class="wrap free">
<a class="free-poster" href="{_e(lesson_url(r))}" rel="noopener">{_poster(r)}<span class="sr">Watch {_e(r["title"])} on YouTube</span></a>
<div class="free-copy">
<h2 id="free-h">Start with a free lesson</h2>
<h3 class="free-title">{_e(r["title"])}</h3>
{take}
<p class="free-actions"><a class="btn btn-ink" href="{_e(lesson_url(r))}" rel="noopener">Watch on YouTube</a><a class="btn btn-ghost" href="videos/">All free lessons</a></p>
<p class="muted small">Opens YouTube in your browser. Nothing from YouTube loads on this page.</p>
{nxt_html}
</div>
</div></section>"""


def _bundle_band(cat: Catalog) -> str:
    b = cat.bundle
    inc, exc = _bundle_titles(cat)
    rows = "".join(
        f"<li><span>{_e(_incl_name(p, o))}</span><span>{_e(money(o.price_usd) or 'Price not set yet')}</span></li>"
        for p, o in inc)
    left_out = ", ".join(_e(p.title) for p in exc)
    guarantee = all(_guarantee(p) for p, _o in inc)
    out_line = (f'<p class="excl"><b>Not included:</b> {left_out}. Each is sold on its own. '
                f"This bundle is {len(inc)} of the {len(cat.products)} titles, "
                "not the whole catalog.</p>") if exc else ""
    return f"""<section id="bundle" class="bundle" aria-labelledby="bundle-h"><div class="wrap">
<div>
<h2 id="bundle-h">{_e(b.title)}</h2>
<p class="bundle-sub">{_e(b.summary)}</p>
<ul class="incl">{rows}</ul>
{_bundle_sum(cat)}
{out_line}
</div>
<div class="offer">
<p class="pr">{_price(b.offer)}</p>
{buy_button(b.offer, label="Buy the bundle", extra="btn-block")}
{_assure(guarantee)}
<p class="fine">{_e(b.terms_note)} <a href="{b.slug}/">Everything in the bundle</a></p>
</div>
</div></section>"""


def last_checked(p: Product) -> str:
    """The newest date in the title's "Tested with" records, as '1 Oct 2026',
    or '' when the catalog records none."""
    days = sorted(c.checked_on for c in p.tested)
    if not days:
        return ""
    d = date.fromisoformat(days[-1])
    return f"{d.day} {d:%b %Y}"


def _refund_short(p: Product) -> str:
    if _guarantee(p):
        return GUARANTEE
    return "On the product page" if p.refund else "Not published yet"


def _compare(cat: Catalog) -> str:
    path_names = {k: label for k, label, *_ in PATHS}
    rows = []
    for p in cat.products:
        goals = ", ".join(path_names[k] for k in product_paths(p))
        rows.append(
            f'<tr data-compare="{p.slug}"><th scope="row"><a href="{p.slug}/">{_e(p.title)}</a></th>'
            f'<td data-label="Goal">{_e(goals)}</td>'
            f'<td data-label="Level">{_e(p.level)}</td>'
            f'<td data-label="Format">{_e(format_line(p))}</td>'
            f'<td data-label="You need">{_e(_needs(p))}</td>'
            f'<td data-label="Last checked">{_e(last_checked(p) or "Not stated")}</td>'
            f'<td data-label="Refunds">{_e(_refund_short(p))}</td>'
            f'<td data-label="Price" class="p">{_e(_price_text(p))}</td></tr>')
    picks = "".join(
        f'<button class="chip" type="button" data-compare-pick="{p.slug}" aria-pressed="false">'
        f"{_e(p.title)}</button>" for p in cat.products)
    return f"""<section id="compare" aria-labelledby="compare-h"><div class="wrap">
<div class="section-head"><div><h2 id="compare-h">Compare titles side by side</h2><p>Choose two or three to narrow the table, or leave it showing all of them; a narrowed table's address can be shared. "Last checked" is the newest date a title's steps were tested against its tools. The help included with a purchase is <a href="help/">the same for every title</a>.</p></div></div>
<div class="filters compare-picks" role="group" aria-label="Titles to compare" hidden>{picks}</div>
<table class="compare">
<caption class="sr">Goal, level, format, requirements, when it was last checked, refund terms and price of each title</caption>
<thead><tr><th scope="col">Title</th><th scope="col">Goal</th><th scope="col">Level</th><th scope="col">Format</th><th scope="col">You need</th><th scope="col">Last checked</th><th scope="col">Refunds</th><th scope="col">Price</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
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
        ("What if it isn't useful?", useful),
        ("I lost my access page. What now?",
         ("Get it back yourself: enter the email your Stripe receipt went to and the "
          'receipt number on it, on the <a href="recover/">recover page</a>. No receipt? '
          '<a href="contact/#lost">Write to us</a> from the address you paid with.')),
        ("A download link stopped working.",
         (f"Each download link works for {days} days. Open your access page again and it "
          "makes fresh ones.")),
        ("Who can use what I buy?",
         ("Each product page states its licence, under Refunds and licence. Please don't "
          "resell the files or share them publicly.")),
        ("Does this site track me?",
         ("No. The site sets no cookies and runs no analytics or third-party scripts. The "
          "fonts and images are served from this site, and its main script (the "
          "menu, the search, the finder and the demonstrations) makes no request "
          "and keeps nothing; a search or comparison is written only into the page "
          "address, so it can be shared. The "
          "Help page's chat sends only the question you type. Stripe "
          "handles payment on its own page. On the Free Lessons page, the pictures come "
          "from this site too; a lesson opens on YouTube, in a new tab, "
          "only when you press it. Elsewhere, a free lesson opens on YouTube "
          "only when you follow its link.")),
        ("Do the courses promise income or results?",
         ("No. They teach methods, including where a person has to check the work. Nothing "
          "here is legal, medical, tax or financial advice.")),
    ]
    items = "".join(f"<details><summary>{_e(q)}</summary><div class=\"ans\"><p>{a}</p></div>"
                    "</details>" for q, a in qa)
    return f'<div class="faq acc">{items}</div>'


GALLERY_SCRIPT = "assets/gallery.js"
GALLERY_UNAVAILABLE = "Videos are unavailable right now"


def _facts(cat: Catalog, videos: Iterable[Video] | None, now: datetime) -> str:
    """The counters under the hero: only numbers the page can prove from its
    own data, each already final in the HTML (the script only counts up to
    it, and not at all under reduced motion). The video count is the same
    source as the gallery (`published(videos)`, uncapped), and the time the
    store was built is printed beside the counters (invariant 6). Nothing
    when there is nothing."""
    items = [(len(cat.products), "titles in the catalog")]
    n_videos = len(published(videos))
    if n_videos:
        items.append((n_videos, "free lessons on the channel"))
    if _store_guarantee(cat):
        items.append((int(cat.store["download_link_days"]), "days each download link works"))
    cells = "".join(f'<li><b class="num" data-count="{n}">{n}</b><span>{_e(label)}</span></li>'
                    for n, label in items if n > 0)
    if not cells:
        return ""
    return (f'<section class="facts" aria-label="The store in numbers"><div class="wrap">'
            f"<ul>{cells}</ul>"
            f'<p class="facts-at muted">As of <time datetime="{now.strftime("%Y-%m-%dT%H:%MZ")}">'
            f'{now.strftime("%Y-%m-%d %H:%M")} UTC</time>, when this page was built.</p>'
            f"</div></section>")


def _gallery() -> str:
    """The channel's video gallery. This is the shell only: the cards are
    drawn by assets/gallery.js from GET /public/academy/videos, so no YouTube
    address is in the HTML. Without the script, or when the list cannot be
    read, it says so plainly and links to the free-lessons page."""
    return f"""<section id="videos" class="gallery" aria-labelledby="videos-h" data-gallery><div class="wrap">
<div class="section-head">
<div><h2 id="videos-h">Watch before you decide</h2><p>Free lessons from the Atlas Academy channel on YouTube. Press one to watch it here.</p></div>
<a class="btn btn-ghost btn-sm" href="{CHANNEL_URL}" rel="noopener">Open the channel</a>
</div>
<p class="gal-state" data-gal-state data-state="unknown" role="status" aria-live="polite">{GALLERY_UNAVAILABLE}</p>
<ul class="gal-grid" data-gal-grid hidden></ul>
<p class="gal-note muted">The pictures load from YouTube's image servers when this section opens. The player loads only after you press a video, in its privacy-enhanced mode. <a href="videos/">All free lessons</a></p>
</div></section>"""


def _founder(cat: Catalog) -> str:
    """Brief item 33. A real photo only when the owner supplies one: set
    store.founder_photo in catalog.json to a path under assets/ (for example
    "founder.jpg") and drop the file in assets/. Until then the emblem
    stands in. No credential, testimonial or count is written here."""
    author, email = _e(cat.store["author"]), _e(cat.contact_email)
    photo = str(cat.store.get("founder_photo") or "")
    if photo and (ASSETS / photo).is_file():
        portrait = (f'<img class="portrait" src="assets/{_e(photo)}" alt="the owner" width="320" '
                    'height="320" loading="lazy">')
    else:
        portrait = f'<div class="portrait monogram" aria-hidden="true">{BRAND_SVG}</div>'
    op = cat.product("safe-ai-operator")
    op_link = f'<a href="{op.slug}/">{_e(op.title)}</a>' if op else "the operator course"
    inspect = "".join(f'<li><a href="{p.slug}/#inside">{_e(p.title)}</a></li>'
                      for p in cat.products)
    return f"""<section id="about" aria-labelledby="about-h"><div class="wrap">
<div class="about">
{portrait}
<div class="about-copy">
<h2 id="about-h">Meet the owner</h2>
<p>I'm {author}, and I write and run {_e(cat.store["name"])}. I run ATLAS, my own AI agent, on the rules I teach in {op_link}: it reads before it acts, picks from a closed list of actions, and stops at the steps marked for review until I decide.</p>
<p>How I teach: every method is shown on a working example, every title says who it's for and who it isn't, and every one lists its contents before you buy. I read the counts on these pages from the files on {_e(cat.store["facts_checked"])}.</p>
<p>Questions before you buy? Write to me at <a href="mailto:{email}">{email}</a> or call {_phone_link()}. I read every message myself.</p>
</div>
<div class="inspect"><h3>Look inside first</h3><ul>{inspect}</ul></div>
</div>
</div></section>"""


_REL_ATTR = re.compile(r'\b(href|src|poster)="(?!/|#|[a-zA-Z][a-zA-Z0-9+.-]*:)([^"]*)"')


def _one_level_up(html: str) -> str:
    """Prefix `../` to every relative href/src/poster in `html` (absolute paths,
    #anchors and any scheme such as https:, mailto:, tel: are left alone)."""
    return _REL_ATTR.sub(lambda m: f'{m.group(1)}="../{m.group(2)}"', html)


def _home(cat: Catalog, lessons: list[dict] | None = None,
          videos: Iterable[Video] | None = None, now: datetime | None = None) -> str:
    lessons = valid_lessons(lessons)
    n = len(cat.products)
    guarantee = _store_guarantee(cat)
    days = cat.store["download_link_days"]
    email = _e(cat.contact_email)
    watch = "#free-lesson" if lessons else "videos/"
    cards = "".join(_card(p, "", i, n) for i, p in enumerate(cat.products, 1))
    trust = "".join(f"<li>{TICK_SVG}{_e(t)}</li>" for t in (
        "One-time pricing", "14-day guarantee" if guarantee else "Refunds per title",
        "Secure Stripe checkout"))
    cycle = "|".join(_e(label) for _k, label, _l, _t in PATHS)
    first = _e(PATHS[0][1])
    names = "".join(f"<li>{_e(p.title)}</li>" for p in cat.products)
    body = f"""<section class="hero pv-hero"><div class="pv-aurora" aria-hidden="true"><i></i><i></i><i></i></div><div class="wrap">
<div class="hero-copy">
<p class="pv-eyebrow">Practical AI that asks first</p>
<h1><span>Learn practical AI.</span> <span>Stay in control.</span></h1>
<p class="lede">Courses, templates and guided workflows for everyday life and small business.</p>
<p class="pv-cycle">Start with <b class="pv-cycle-word" data-cycle="{cycle}">{first}</b></p>
<div class="hero-ctas">
<a class="pv-btn pv-btn-primary" href="#paths">Find my starting point &#8599;</a>
<a class="pv-btn pv-btn-ghost" href="#videos">Explore free lessons</a>
</div>
<ul class="pv-trust" aria-label="Buying here">{trust}</ul>
</div>
{_method()}
</div></section>

<section class="pv-marquee" aria-label="Titles in the catalog"><div class="pv-track"><ul>{names}</ul><ul aria-hidden="true">{names}</ul></div></section>

{_subscribe_band('')}

{_paths_section(cat)}

<section id="catalog" class="pv-sec" aria-labelledby="catalog-h"><div class="wrap">
<div class="pv-head"><div><p class="pv-eyebrow">The catalog</p><h2 id="catalog-h">Useful skills. Clear outcomes.</h2></div>
<p class="pv-blurb">{n} titles, each with one clear outcome. One-time prices, with no subscription.</p></div>
{_catalog_tools(cat)}
<div class="filters pv-chips" role="group" aria-label="Show a learning path">{_chips()}</div>
<p id="filter-status" class="cat-count" aria-live="polite"></p>
<div class="grid pv-grid">{cards}</div>
<div class="cat-empty" data-cat-empty hidden><p><b>No title matches that search.</b> Try a shorter word, such as a tool name, or clear the filters.</p>
<p class="cat-empty-actions"><button class="btn btn-ghost btn-sm" type="button" data-cat-clear>Clear all filters</button><a class="btn btn-ghost btn-sm" href="videos/">Browse the free lessons</a></p></div>
</div></section>

{_facts(cat, videos, now or datetime.now(UTC))}

{_finder(cat, lessons)}

{_featured_lesson(cat, lessons)}

{_compare(cat)}

{_gate(cat)}

{_bundle_band(cat)}

{_gallery()}

{_founder(cat)}

<section id="buying" aria-labelledby="buying-h"><div class="wrap">
<div class="section-head"><div><h2 id="buying-h">Buying and access</h2><p>What happens after you pay, and how to get your files back. More on the <a href="help/">help page</a>.</p></div></div>
<div class="help">
<ol class="steps">
<li><h3>Pay on Stripe</h3><p>Buy opens Stripe's secure checkout. We never see your card.</p></li>
<li><h3>Land on your access page</h3><p>Stripe sends you straight to a private page with every download. Bookmark it.</p></li>
<li><h3>Download, then come back</h3><p>Each download link works for {days} days. Open your access page again for fresh links.</p></li>
<li><h3>Lost the page? Get it back</h3><p>The <a href="recover/">recover page</a> shows how: email us the receipt number from the address your Stripe receipt went to. No receipt? Use the <a href="contact/#lost">contact page</a>.</p></li>
</ol>
<div>{_home_faq(cat)}
<p class="policies">Policies: <a href="refunds/">Refunds</a>, <a href="terms/">Terms of sale</a>, <a href="privacy/">Privacy</a>. Help: <a href="help/">the help page</a> or <a href="mailto:{email}">{email}</a>.</p></div>
</div>
</div></section>

<section class="close"><div class="wrap"><div class="box">
<h2>Watch a lesson, or choose the right resource.</h2>
<p>One-time prices, downloads on your own access page{", " + GUARANTEE if guarantee else ""}.</p>
<div class="hero-ctas"><a class="btn btn-gold" href="{watch}">Watch a free lesson</a><a class="btn btn-ghost" href="#finder">Find my starting point</a></div>
</div></div></section>"""
    # The home is /academy/ (depth 1) but its body is written for the site
    # root: re-point its relative links one level up (#anchors stay).
    return _page(cat, title=cat.store["name"],
                 description="Learn practical AI and stay in control: courses, templates and "
                             "guided workflows for everyday life and small business, by the owner.",
                 body=_one_level_up(body), depth=1,
                 head=_one_level_up(
                     '<link rel="alternate" type="application/rss+xml" '
                     'title="ATLAS Academy blog" href="blog/feed.xml">\n'
                     f'<script src="{GALLERY_SCRIPT}" defer></script>'))


# ------------------------------------------------------------------ product

def _access_note(cat: Catalog, p: Product) -> str:
    """Brief item 16: what happens after checkout, and how access recovers."""
    days = cat.store["download_link_days"]
    if not p.refund:
        return ('<p class="access">Refund terms for this title aren\'t published yet, so '
                'it isn\'t on sale. <a href="../refunds/">Refunds</a></p>')
    return (f'<p class="access">After Stripe checkout you land on your own access page with '
            f"every file. Each download link works for {days} days; open the page again for "
            'fresh links. Lost it? <a href="../recover/">Recover it</a> with your Stripe '
            "receipt.</p>")


def _licence_short(p: Product) -> str:
    if not p.licence:
        return "Terms of sale apply"
    return p.licence.split(":")[0].split(".")[0]


def _summary(cat: Catalog, p: Product, o: Offer) -> str:
    """Brief item 15: the selected edition, its price, delivery, licence and
    refund summary, beside its one Buy action."""
    refund = GUARANTEE if _guarantee(p) else ("See the refund terms" if p.refund
                                              else "Not published yet")
    fmt = ", ".join(formats([o]))
    return f"""<div class="summary" data-offer="{_e(o.id)}">
<p class="sum-name">{_e(o.name)}</p>
<p class="pr">{_price(o)}{' <small>one-time</small>' if o.price_usd is not None else ""}</p>
<dl class="sum-rows"><div><dt>Format</dt><dd>{_e(fmt)}</dd></div>
<div><dt>Delivery</dt><dd>Access page, right after checkout</dd></div>
<div><dt>Licence</dt><dd>{_e(_licence_short(p))}</dd></div>
<div><dt>Refunds</dt><dd>{_e(refund)}</dd></div></dl>
{buy_button(o, extra="btn-block")}
</div>"""


def _buy_box(cat: Catalog, p: Product) -> str:
    if len(p.offers) == 1:
        o = p.offers[0]
        return f"""<div class="buybox" id="buy">
{_summary(cat, p, o)}
{_assure(_guarantee(p))}
{_access_note(cat, p)}
</div>"""
    # Brief item 13: explicit edition cards. Without the script every
    # summary shows, each with its own Buy; with it, the chosen one does.
    # Plan F10: nothing is pre-selected, so a "From" price never sits above
    # a dearer edition already chosen; the visitor's pick (or a link to
    # #<edition id>, as the public catalog.json gives) decides what Buy buys.
    cards = "".join(
        f'<label class="edition" id="{_e(o.id)}"><input type="radio" name="edition" '
        f'value="{_e(o.id)}" data-edition><span class="ed-body">'
        f'<b>{_e(o.name)}</b><small>{_e(o.blurb)}</small></span>'
        f'<span class="ep">{_price(o)}</span></label>'
        for o in p.offers)
    summaries = "".join(_summary(cat, p, o) for o in p.offers)
    return f"""<div class="buybox" id="buy">
<fieldset class="editions"><legend>Choose your edition</legend>{cards}</fieldset>
<div class="summaries"><p class="ed-prompt" data-ed-prompt hidden>Choose an edition above. Its price, its files and its Buy button appear here, and Buy pays for that edition only.</p>{summaries}</div>
{_assure(_guarantee(p))}
{_access_note(cat, p)}
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
    return f"""<section id="reviews" aria-labelledby="reviews-h"><div class="wrap">
<div class="section-head"><div><p class="kind">From buyers</p><h2 id="reviews-h">Written on their access page</h2>
<p>Written by people who bought it, on their access page, and read before they appear here. Nothing else is shown.</p></div></div>
<ul class="reviews">{"".join(items)}</ul>
</div></section>"""


def _sticky_buy(p: Product) -> str:
    o = _single_live(p)
    cta = (buy_button(o, extra="btn-sm") if o is not None else
           '<a class="btn btn-ghost btn-sm" href="#buy">Choose edition</a>')
    sub = _price_text(p) + (f", {GUARANTEE}" if _guarantee(p) else "")
    return f"""<div class="sticky-buy" aria-label="Buy {_e(p.title)}"><div class="wrap">
<img src="{cover_src(p, "../")}" alt="">
<div class="t"><b>{_e(p.title)}</b><span>{_e(sub)}</span></div>
{cta}
</div></div>"""


META_DESCRIPTION_MAX = 160


def meta_description(text: str, limit: int = META_DESCRIPTION_MAX) -> str:
    """A <meta name="description"> that search results show whole: the text
    as is when it fits, else its whole sentences that fit, else cut at the
    last clause (", ") or word that fits with an ellipsis, never mid-word.
    The page itself carries the full text."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    end = head.rfind(". ")
    if end >= limit // 2:                     # whole sentences: no ellipsis
        return head[:end + 1]
    cut = head.rfind(", ")
    if cut < limit // 2:
        cut = head.rfind(" ")
    return head[:cut].rstrip(" ,;:") + "\u2026"


# Course-page parts (the owner, 2026-10-12: "include tables and grids and
# how-tos and videos"). Each is built only from the product's catalog
# entry and the store facts, like every other line on the page.

_ITEM_NO = re.compile(r"^(\d+(?:\.\d+)*)\s+(.*)$")
_PART = re.compile(r"^((?:Module|Part|Chapter|Section|Step|Week)\s+[\w.]+|Bonus)\s*:\s*(.+)$")


def _item(x: str) -> str:
    m = _ITEM_NO.match(x)
    if m:
        return f'<li><span class="no">{_e(m.group(1))}</span><span>{_e(m.group(2))}</span></li>'
    return f'<li><span class="no" aria-hidden="true"></span><span>{_e(x)}</span></li>'


# Sneak peeks (the owner, 2026-10-12: "a small demo preview for each course").
# Cut from the files a buyer gets by tools/academy_previews.py: the first
# minute of lesson 0.1, a few real pages, a kit's real file list and README
# opening. previews.json says what exists; a product without an entry shows
# no peek. Video plays from this site (no third party), only when pressed.

PREVIEWS = Path(__file__).with_name("previews.json")


def _previews() -> dict:
    try:
        return json.loads(PREVIEWS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1_048_576 else f"{n / 1_048_576:.1f} MB"


def _peek(p: Product) -> str:
    entry = _previews().get(p.slug)
    if not entry:
        return ""
    base = f"../assets/previews/{p.slug}/"
    parts = []
    for v in entry.get("videos", []):
        parts.append(
            f'<figure class="peek-video"><video controls preload="none" playsinline '
            f'width="1280" height="720" poster="{base}{_e(v["poster"])}">'
            f'<source src="{base}{_e(v["src"])}" type="video/mp4"></video>'
            f'<figcaption><b>{_e(v["label"])}</b><span>The first {v["seconds"]} seconds '
            f'of a {v["full_minutes"]}-minute lesson, as it plays on your access page</span>'
            f'</figcaption></figure>')
    videos = f'<div class="peek-videos">{"".join(parts)}</div>' if parts else ""
    books = []
    for b in entry.get("books", []):
        nums = [str(pg["page"]) for pg in b["pages"]]
        pages = "".join(
            f'<a class="peek-page" href="{base}{_e(pg["src"])}" target="_blank" rel="noopener">'
            f'<img src="{base}{_e(pg["src"])}" alt="Page {pg["page"]} of {_e(b["label"])}" '
            f'width="{int(pg.get("w") or 1000)}" height="{int(pg.get("h") or 1294)}" '
            f'loading="lazy" decoding="async">'
            f'<span>Page {pg["page"]}</span></a>' for pg in b["pages"])
        books.append(f'<div class="peek-book"><h3>{_e(b["label"])}</h3>'
                     f'<p class="peek-note">Pages {_and(nums)} of {b["total"]}, exactly as '
                     f'they print. Select a page to see it full size.</p>'
                     f'<div class="peek-pages">{pages}</div></div>')
    kit = ""
    if k := entry.get("kit"):
        tree = "".join(f'<li><span>{_e(f["path"])}</span><small>{_size(int(f["bytes"]))}</small></li>'
                       for f in k["tree"])
        excerpt = "\n".join(k.get("excerpt") or [])
        readme = k["readme"].rsplit("/", 1)[-1]
        code = (f'<figure class="peek-readme"><pre><code>{_e(excerpt)}</code></pre>'
                f'<figcaption>The first {len(k["excerpt"])} of {k["readme_lines"]} lines of '
                f'{_e(readme)}</figcaption></figure>') if excerpt else ""
        kit = (f'<div class="peek-kit"><h3>{_e(k["label"])}</h3>'
               f'<p class="peek-note">Every file in the zip, {len(k["tree"])} in all.</p>'
               f'<div class="peek-kit-grid"><ul class="peek-tree">{tree}</ul>{code}</div></div>')
    return f"""<section id="peek" aria-labelledby="peek-h"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Sneak peek</p><h2 id="peek-h">Look inside before you buy</h2>
<p>Cut from the files you get after checkout. Nothing here is a mock-up.</p></div></div>
{videos}{"".join(books)}{kit}
</div></section>"""


def _peek_link(p: Product) -> str:
    entry = _previews().get(p.slug) or {}
    if entry.get("videos"):
        what = "Watch a one-minute preview"
    elif entry.get("books"):
        what = "Look inside: real pages"
    elif entry.get("kit"):
        what = "See every file in the kit"
    else:
        return ""
    return f'<p class="peek-link"><a href="#peek">{what}</a></p>'


def _peek_what(slug: str) -> str:
    """The short label for a title's sneak peek, or '' when it has none."""
    entry = _previews().get(slug) or {}
    if entry.get("videos"):
        return "Watch a preview"
    if entry.get("books"):
        return "Look inside"
    if entry.get("kit"):
        return "See the files"
    return ""


def _peek_chip(p: Product, root: str) -> str:
    """A link to the title's sneak peek, for cards and lists."""
    what = _peek_what(p.slug)
    if not what:
        return ""
    return f'<a class="peek-chip" href="{root}{p.slug}/#peek">{what}</a>'


def _glance(cat: Catalog, p: Product) -> str:
    """The contents label as a visible table, not a folded list."""
    if not p.label:
        return ""
    rows = "".join(f'<tr><th scope="row">{_e(n)}</th><td>{_e(v)}</td></tr>' for n, v in p.label)
    return f"""<section id="glance" aria-labelledby="glance-h"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Contents</p><h2 id="glance-h">Every count, in one table</h2>
<p>Counted from the files on {_e(cat.store["facts_checked"])}.</p></div></div>
<div class="table-card"><table class="glance">{rows}</table></div>
</div></section>"""


def _how(cat: Catalog, p: Product) -> str:
    """How buying and starting works, in order. Step three names the
    product's own first section and its first item."""
    days = cat.store["download_link_days"]
    first = ""
    if p.inside and p.inside[0][1]:
        title, items = p.inside[0]
        m, part = _ITEM_NO.match(items[0]), _PART.match(title)
        first = f"First up: {m.group(2) if m else items[0]} ({part.group(1) if part else title})."
    watch = "video" in format_line(p).lower()
    steps = [
        ("Pay once", "Buy opens Stripe's secure checkout. We never see your card details."),
        ("Open your access page", "You land on it right after checkout, with every file."),
        ("Watch the first lesson" if watch else "Start at the beginning",
         first or "Open the first file on your access page."),
        ("Come back any time",
         f"Each download link works for {days} days. Open the page again for fresh links."),
    ]
    lis = "".join(f"<li><h3>{_e(h)}</h3><p>{_e(s)}</p></li>" for h, s in steps)
    return f"""<section id="how" aria-labelledby="how-h"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">How it works</p><h2 id="how-h">From checkout to your first lesson</h2></div></div>
<ol class="how-steps">{lis}</ol>
</div></section>"""


COST_ROWS = ("claude plan", "costs", "other costs", "running costs")


def _before_you_pay(cat: Catalog, p: Product) -> str:
    """Plan F07/F09 acceptance: before paying, a visitor can answer "What do
    I receive?", "Can I run it?", "What else costs money?" and "How do I
    recover access?". Every answer is the catalog's own text; where the
    catalog states no other cost, the page says so instead of guessing."""
    days = cat.store["download_link_days"]
    receive = _row_value(p, "Format") or format_line(p)
    if len(p.offers) > 1:
        receive += ". Each edition lists its own files."
    run = [f"<li><b>Level:</b> {_e(p.level)}. {_e(p.level_detail)}</li>"]
    if needs := _row_value(p, "You need"):
        run.append(f"<li><b>You need:</b> {_e(needs)}</li>")
    if tested := _row_value(p, "Tested on"):
        run.append(f"<li><b>Tested on:</b> {_e(tested)}</li>")
    elif when := last_checked(p):
        run.append(f"<li><b>Last checked:</b> {_e(when)}</li>")
    if p.not_for:
        run.append(f"<li><b>Not for:</b> {_e(p.not_for[0])}</li>")
    costs = [(k, v) for k, v in p.label if k.lower() in COST_ROWS]
    price = _price_text(p)
    if costs:
        cost = "".join(f"<p><b>{_e(k)}:</b> {_e(v)}</p>" for k, v in costs)
    else:
        # the owner, 2026-10-16: every course is one payment for the course itself,
        # nothing recurring. A third-party tool's own plans are still that
        # company's to price, so the line says so instead of promising "free".
        cost = ("<p>Nothing else from ATLAS Academy: no subscription, no renewal, "
                "no add-ons. If a tool under <b>You need</b> has paid plans, that "
                "company sets the price.</p>")
    once = (f"<p><b>This title:</b> {_e(price)}, paid once. No subscription.</p>"
            if price != "Price not set yet" else
            "<p><b>This title:</b> price not set yet, so it isn't on sale.</p>")
    return f"""<section id="before" class="before" aria-labelledby="before-h"><div class="wrap">
<h2 id="before-h">Before you pay</h2>
<div class="before-grid">
<div class="before-q"><h3>What do I receive?</h3><p>{_e(receive)}</p><p><a href="#kit">Every file, listed</a></p></div>
<div class="before-q"><h3>Can I run it?</h3><ul>{"".join(run)}</ul><p><a href="#fit">Who it's for, and who it isn't</a></p></div>
<div class="before-q"><h3>What else costs money?</h3>{once}{cost}</div>
<div class="before-q"><h3>How do I get my files back later?</h3><p>Checkout ends on your own access page with every file. Each download link works for {days} days; open the access page again for fresh ones.</p><p>Lost the page? <a href="../recover/">Recover it</a> with the email your Stripe receipt went to and the receipt number on it.</p></div>
</div>
</div></section>"""


def _modules(p: Product) -> str:
    cards = []
    for title, items in p.inside:
        m = _PART.match(title)
        k, h = (m.group(1), m.group(2)) if m else ("", title)
        kick = f'<p class="mod-k">{_e(k)}</p>' if k else ""
        cards.append(f'<article class="mod">{kick}<h3>{_e(h)}</h3>'
                     f'<ul class="mod-items">{"".join(_item(x) for x in items)}</ul></article>')
    return "".join(cards)


def _first_lessons(p: Product) -> str:
    """Under the cover of a video course: its first lessons, in order."""
    if "video" not in format_line(p).lower():
        return ""
    items = [x for _t, xs in p.inside for x in xs][:4]
    if len(items) < 2:
        return ""
    return (f'<div class="p-first"><p class="p-first-h">First lessons</p>'
            f'<ol class="mod-items">{"".join(_item(x) for x in items)}</ol></div>')


def _product(cat: Catalog, p: Product, reviews: list[dict] | None = None,
             lessons: list[dict] | None = None) -> str:
    benefits = "".join(f"<li>{TICK_SVG}<span>{_e(b)}</span></li>" for b in p.benefits)
    inside = _modules(p)
    audience = "".join(f"<li>{_e(x)}</li>" for x in p.audience)
    not_for = "".join(f"<li>{_e(x)}</li>" for x in p.not_for)
    faq = "".join(f"<details><summary>{_e(q)}</summary><div class=\"ans\"><p>{_e(a)}</p></div>"
                  "</details>" for q, a in p.faq)
    notice = f'<p class="notice">{_e(p.notice)}</p>' if p.notice else ""
    pair = cat.product(p.pairs_with)
    pair_html = pair_link = ""
    if pair is not None:
        pair_link = '<a href="#pair">Pairs well with</a>'
        pair_html = f"""<section id="pair"><div class="wrap">
<div class="section-head"><div><h2>Pairs well with</h2></div></div>
<div class="pair"><img src="{cover_src(pair, "../")}" alt="" width="140" height="140" loading="lazy">
<div><h3>{_e(pair.title)}</h3><p>{_e(p.pairs_reason)}</p></div>
<div class="side">{_from_price(pair)}<a class="btn btn-ghost btn-sm" href="../{pair.slug}/">See {_e(pair.title)}</a></div></div>
</div></section>"""
    lesson = next((r for r in valid_lessons(lessons) if _lesson_next(cat, r) is p), None)
    lesson_html = ""
    if lesson is not None:
        lesson_html = (f'<p class="p-lesson">Free lesson on this topic: <a href="{_e(lesson_url(lesson))}" '
                       f'rel="noopener">{_e(lesson["title"])}</a> (opens YouTube)</p>')
    refund = _e(p.refund) if p.refund else (
        "Not published yet. This title stays off sale until its refund terms are set.")
    licence = _e(p.licence) if p.licence else (
        'Not published yet. Until it is, the <a href="../terms/">terms of sale</a> apply.')
    # The on-page nav in page order (app.js scroll-spies it): the finish
    # panel sits before #learn, the rest between #kit and #inside.
    finish_link = '<a href="#finish">'
    links = op.nav_links(cat, p)
    op_nav = ((links[:links.index("</a>") + 4], links[links.index("</a>") + 4:])
              if links.startswith(finish_link) else ("", links))
    glance_link = '<a href="#glance">At a glance</a>' if p.label else ""
    peek_link = '<a href="#peek">Sneak peek</a>' if _previews().get(p.slug) else ""
    large = " large-route" if "senior" in p.tags else ""
    route = ('<p class="route-note">The 60+ edition is set in large print. Your '
             "browser's zoom (Ctrl or Cmd and +) enlarges this page too.</p>"
             if "senior" in p.tags else "")
    body = f"""<div class="wrap crumbs"><a href="../academy/#catalog">All courses &amp; kits</a><span aria-hidden="true">/</span><span>{_e(p.title)}</span></div>
<section class="p-hero{large}"><div class="wrap">
<div class="p-media">{_product_cover(p)}{_first_lessons(p)}</div>
<div class="p-info">
<p class="kind">{_e(p.kind)}</p>
<h1>{_e(p.title)}</h1>
<p class="lede">{_e(p.summary)}</p>
<dl class="p-facts"><div><dt>Level</dt><dd><span class="level">{_e(p.level)}</span> {_e(p.level_detail)}</dd></div>
<div><dt>Format</dt><dd>{_e(format_line(p))}</dd></div></dl>
{route}
{notice}
{op.audiences_line(cat, p)}
{lesson_html}
{_peek_link(p)}
{_buy_box(cat, p)}
</div>
</div></section>

<nav class="p-nav" aria-label="On this page"><div class="wrap">
<a href="#before">Before you pay</a>{peek_link}{glance_link}<a href="#how">How it works</a>{op_nav[0]}<a href="#learn">What you'll learn</a><a href="#kit">What you get</a>{op_nav[1]}<a href="#inside">What's inside</a><a href="#fit">Who it's for</a>{pair_link}<a href="#questions">Questions</a><a href="#terms">Refunds &amp; licence</a>
</div></nav>

{_before_you_pay(cat, p)}
{_peek(p)}
{_glance(cat, p)}
{_how(cat, p)}
{op.finish_panel(p)}

<section id="learn"><div class="wrap two">
<div class="intro"><h2>What you'll be able to do</h2><p>{_e(p.intro)}</p></div>
<ul class="outcomes">{benefits}</ul>
</div></section>

{_kit(p)}
{op.journey_panel(p)}
{op.inclusions_panel(p)}
{op.review_demo_panel(p)}
{op.troubleshooting_panel(p)}
{op.tested_panel(p)}
{op.evidence_panel(p)}
{op.family_route_panel(cat, p)}

<section id="inside"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">Curriculum</p><h2>Every section, listed</h2><p>Each lesson, note or file, in the order you'll meet it.</p></div></div>
<div class="mods">{inside}</div>
</div></section>

<section id="fit"><div class="wrap">
<div class="section-head"><div><h2>Is it right for you?</h2></div></div>
<div class="fit"><div class="yes"><h3>{TICK_SVG} Who it's for</h3><ul>{audience}</ul></div><div class="no"><h3>{CROSS_SVG} Who it isn't for</h3><ul>{not_for}</ul></div></div>
</div></section>

{_reviews_section(p, reviews or [])}
{pair_html}
<section id="questions"><div class="wrap two">
<div><h2>Asked before buying</h2><p class="muted">Something else? <a href="../contact/">Write to the owner</a>.</p></div>
<div class="acc">{faq}</div>
</div></section>

<section id="terms"><div class="wrap">
<div class="section-head"><div><h2>Refunds and licence</h2></div></div>
<div class="terms-grid">
<div><h3>Refunds</h3><p>{refund} <a href="../refunds/">Refund policy</a></p></div>
<div><h3>Licence</h3><p>{licence}</p></div>
</div>
</div></section>
{op.support_panel(cat)}
<div class="wrap"><p class="back"><a class="btn btn-ghost" href="../academy/#catalog">Back to all courses &amp; kits</a></p></div>
{_sticky_buy(p)}"""
    return _page(cat, title=p.title, description=meta_description(p.summary), body=body,
                 depth=1)


# ------------------------------------------------------------------ bundle

def _bundle(cat: Catalog) -> str:
    b: Bundle = cat.bundle
    inc, exc = _bundle_titles(cat)
    items = []
    for p, o in inc:
        multi = len(p.offers) > 1
        blurb = o.blurb if multi and o.blurb else p.summary
        items.append(f"""<div class="incl-item"><img src="{cover_src(p, "../")}" alt="" width="110" height="110" loading="lazy">
<div><p class="kind">{_e(p.kind)}, {_e(p.level)}</p><h3><a href="../{p.slug}/">{_e(_incl_name(p, o))}</a></h3><p>{_e(blurb)}</p></div>
<div class="solo">On its own<b>{_price(o)}</b>{_peek_chip(p, "../")}</div></div>""")
    guarantee = all(_guarantee(p) for p, _o in inc)
    solo_cards = []
    for p in exc:
        o = _single_live(p)
        buy = (buy_button(o, extra="btn-sm") if o is not None else
               f'<a class="btn btn-ghost btn-sm" href="../{p.slug}/#buy">Choose edition</a>')
        solo_cards.append(f"""<div class="pair"><img src="{cover_src(p, "../")}" alt="" width="140" height="140" loading="lazy">
<div><p class="kind">Not in the bundle</p><h3>{_e(p.title)}</h3><p>{_e(p.summary)}</p></div>
<div class="side">{_from_price(p)}<a class="btn btn-ghost btn-sm" href="../{p.slug}/">Details</a>{_peek_chip(p, "../")}{buy}</div></div>""")
    separately = ""
    if solo_cards:
        separately = f"""<section><div class="wrap">
<div class="section-head"><div><h2>Not in the bundle</h2><p>This bundle holds {len(inc)} of the {len(cat.products)} titles. These are sold on their own.</p></div></div>
<div class="solo-list">{"".join(solo_cards)}</div>
</div></section>"""
    first = inc[0][0] if inc else cat.products[0]
    body = f"""<div class="wrap crumbs"><a href="../academy/#catalog">All courses &amp; kits</a><span aria-hidden="true">/</span><span>{_e(b.title)}</span></div>
<section class="p-hero bundle-hero"><div class="wrap">
<div>
<p class="kind">Bundle</p>
<h1>{_e(b.title)}</h1>
<p class="lede">{_e(b.summary)} Delivered right after payment, on one access page with every file.</p>
{_bundle_sum(cat)}
<div class="buybox" id="buy"><p class="pr">{_price(b.offer)}{' <small>one-time</small>' if b.offer.price_usd is not None else ""}</p>
{buy_button(b.offer, label="Buy the bundle", extra="btn-block")}
{_assure(guarantee)}
<p class="access">{_e(b.terms_note)} After Stripe checkout you land on one access page with every file; each download link works for {cat.store["download_link_days"]} days, and the page makes fresh ones.</p></div>
</div>
<div><h2 class="incl-h">What's included</h2><div class="incl-list">{"".join(items)}</div></div>
</div></section>
{separately}
<div class="sticky-buy"><div class="wrap"><img src="{cover_src(first, "../")}" alt=""><div class="t"><b>{_e(b.title)}</b><span>{_e(money(b.offer.price_usd) or "Price not set yet")}</span></div>{buy_button(b.offer, label="Buy the bundle", extra="btn-sm")}</div></div>"""
    return _page(cat, title=b.title, description=b.summary, body=body, depth=1,
                 current="bundle")


# ------------------------------------------------------------------ prose pages

CONTACT_SCRIPT = '<script src="../assets/contact.js" defer></script>'


def _prose(cat: Catalog, *, slug: str, title: str, description: str, html: str,
           current: str = "", crumb: str = "", head: str = "") -> tuple[str, str]:
    body = (f'<section class="page"><div class="wrap"><div class="crumbs" style="padding:0">'
            f'<a href="../academy/">Home</a><span>/</span>{_e(crumb or title)}</div>'
            f"<h1>{_e(title)}</h1><div class=\"prose\">{html}</div></div></section>")
    return (f"{slug}/index.html",
            _page(cat, title=title, description=description, body=body, depth=1,
                  current=current, head=head))



# ------------------------------------------------------------------ join
#
# the owner, 2026-11-10: a polished join / sign-in screen. The store has no
# accounts and no subscriptions (every price is one-time), so this page adds
# none: "join" is choosing a title and handing over to the same Stripe
# payment link every product page uses; "sign in" is the existing recover
# route (POST /public/academy/recover: receipt email address + receipt
# number -> the buyer's library). Every step is plain HTML (cards, links, a
# form that posts natively); app.js only adds the stepper and the field
# checks. The page takes no card, stores nothing and runs no request.

def _join_offers(cat: Catalog) -> list[tuple[str, str, Offer, bool, str]]:
    """(name, kind line, offer, guarantee, cover) for every sellable offer, the
    bundle first: the same offers, prices and links as the product pages."""
    rows: list[tuple[str, str, Offer, bool, str]] = []
    b = cat.bundle
    inc = _bundle_titles(cat)[0]
    rows.append((b.title, "Bundle", b.offer, all(_guarantee(p) for p, _o in inc),
                 cover_src(inc[0][0], "../") if inc else ""))
    for p in cat.products:
        for o in p.offers:
            name = p.title if len(p.offers) == 1 else f"{p.title}: {o.name}"
            rows.append((name, p.kind, o, _guarantee(p), cover_src(p, "../")))
    return rows


def _join_option(cat: Catalog, name: str, kind: str, o: Offer, guarantee: bool,
                 cover: str) -> str:
    img = (f'<img src="{_e(cover)}" alt="" width="96" height="96" loading="lazy" '
           f'decoding="async">' if cover else "")
    price = _price(o)
    days = cat.store["download_link_days"]
    label = f"Continue to Stripe: {money(o.price_usd)}" if o.buy_live else None
    refund = _e(GUARANTEE) if guarantee else "See the refund terms"
    return f"""<li class="jopt" data-offer="{_e(o.id)}">
<label class="jopt-pick"><input type="radio" name="join-pick" value="{_e(o.id)}" data-join-pick>
<span class="jopt-media">{img}</span>
<span class="jopt-body"><small>{_e(kind)}</small><b>{_e(name)}</b></span>
<span class="jopt-price">{price}</span></label>
<div class="jopt-detail">
<dl class="sum-rows"><div><dt>Price</dt><dd>{price} <small>one-time, no subscription</small></dd></div>
<div><dt>Delivery</dt><dd>Access page right after checkout; links last {days} days and the page makes fresh ones</dd></div>
<div><dt>Refunds</dt><dd>{refund} (<a href="../refunds/">refund terms</a>)</dd></div></dl>
{buy_button(o, extra="btn-block", label=label)}
</div></li>"""


def _join(cat: Catalog) -> tuple[str, str]:
    opts = "".join(_join_option(cat, *row) for row in _join_offers(cat))
    steps = ("Choose", "Review", "Pay on Stripe")
    ol = "".join(f'<li data-step-dot="{i}"><span>{i}</span>{_e(t)}</li>'
                 for i, t in enumerate(steps, 1))
    body = f"""<section class="join" data-join data-step="1"><div class="wrap">
<div class="join-head">
<p class="brandline">Join ATLAS Academy</p>
<h1>Pick what you want to learn.</h1>
<p class="lede">No account to make and no subscription: every title is a one-time price. You choose, pay on Stripe, and your downloads open on your own access page.</p>
</div>
<div class="join-grid">
<div class="join-card join-new" id="join-new">
<h2 class="join-h">New here</h2>
<ol class="join-steps" aria-label="Steps">{ol}</ol>
<div class="join-stage" data-join-stage>
<fieldset class="join-pick"><legend>1. Choose a course, kit or the bundle</legend>
<ul class="jopts">{opts}</ul></fieldset>
<p class="join-back-row"><button type="button" class="btn btn-ghost btn-sm" data-join-back hidden>Back to the list</button></p>
<p class="join-status" role="status" aria-live="polite" data-join-status></p>
</div>
<p class="join-fine">Stripe asks for your email and card on its own page: we never see the card. <a href="../terms/">Terms of sale</a>, <a href="../privacy/">Privacy</a>. Not sure yet? <a href="../{VIDEOS_SLUG}/">Watch a free lesson</a>.</p>
</div>
<div class="join-card join-return" id="sign-in">
<h2 class="join-h">Already bought? Sign in</h2>
<p>Use the address your Stripe receipt went to and the receipt number on it. We check them with Stripe and open your downloads. Neither is stored.</p>
<form class="join-form" method="post" action="{RECOVER_ACTION}" novalidate data-join-form>
<div class="field"><label for="jf-email">Receipt email address</label>
<input id="jf-email" name="email" type="email" inputmode="email" autocomplete="email" autocapitalize="off" spellcheck="false" maxlength="300" required aria-describedby="jf-email-m">
<p class="field-msg" id="jf-email-m" aria-live="polite"></p></div>
<div class="field"><label for="jf-receipt">Receipt number</label>
<input id="jf-receipt" name="receipt" type="text" inputmode="text" autocomplete="off" autocapitalize="characters" spellcheck="false" maxlength="64" placeholder="#1234-5678" required aria-describedby="jf-receipt-m">
<p class="field-msg" id="jf-receipt-m" aria-live="polite">It starts "Receipt #" near the top of Stripe's email.</p></div>
<button class="btn btn-buy btn-block" type="submit" data-join-submit><span class="lbl">Open my downloads</span><span class="spin" aria-hidden="true"></span></button>
<p class="field-msg form-msg" role="alert" data-join-error></p>
</form>
<p class="join-fine">No receipt? <a href="../{RECOVER_SLUG}/">Other ways to get your access back</a>.</p>
</div>
</div>
</div></section>"""
    return (f"{JOIN_SLUG}/index.html",
            _page(cat, title="Join or sign in",
                  description="Choose an ATLAS Academy course and pay on Stripe, or sign in "
                              "to your downloads with your receipt.",
                  body=body, depth=1, current="join"))

# ------------------------------------------------------------------ start
#
# the owner, 2026-10-12 (the growth plan): a /start/ page for someone arriving
# from a Short -- one free lesson, one free worksheet, and three routes by
# what the visitor does -- and the "Before You Automate" worksheet itself,
# a page that prints to one sheet. Off until the owner has read the copy
# (ACADEMY_START_PAGE); the boot republish would otherwise publish it.

START_SLUG = "start"
WORKSHEET_SLUG = "before-you-automate"
START_ENV = "ACADEMY_START_PAGE"
WORKSHEET_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("The task", "What repeats, in one sentence.",
     "Reply to a new contact-form inquiry."),
    ("The input", "What starts it, and where it comes from.",
     "The form sends the name, contact and message."),
    ("The output", "What should exist when it is done.",
     "A drafted reply in a file, not a sent message."),
    ("The review point", "Who reads the output before a customer sees it, and when.",
     "The owner, before replying the same day."),
    ("The failure check", "What a wrong or missing input looks like, and what happens then.",
     "A lead with missing details is refused, not drafted."),
    ("The owner", "The one person who decides when it changes or stops.",
     "The business owner."),
    ("Stays manual", "The step you are not automating yet, and why.",
     "Sending. A person reads every reply first."),
)
ROUTES: tuple[tuple[str, str], ...] = (
    ("I run a local business", "local-business-automation-pack"),
    ("I write code and want a safe AI agent", "safe-ai-operator"),
    ("I want to sell one small product", "sell-with-ai"),
)


def start_page_enabled() -> bool:
    import os
    return os.getenv(START_ENV, "off").strip().lower() in ("1", "on", "true", "yes")


def _worksheet(cat: Catalog) -> tuple[str, str]:
    rows = "".join(
        f'<tr><th scope="row">{_e(name)}<small>{_e(ask)}</small></th>'
        f'<td class="ex">{_e(example)}</td><td class="blank"></td></tr>'
        for name, ask, example in WORKSHEET_FIELDS)
    return _prose(cat, slug=WORKSHEET_SLUG, title="Before You Automate",
                  crumb="Before You Automate",
                  description="A free one-page worksheet: decide what an automation does, "
                              "who checks it and what stays manual, before you build it.",
                  html=f"""
<p class="lede">Fill this in for one task before you build anything. If a box stays
empty, the task isn't ready to automate yet.</p>
<link rel="stylesheet" href="../assets/worksheet.css">
<table class="sheet"><thead><tr><th>Decide</th><th>Example: a lead-form reply</th>
<th>Your task</th></tr></thead><tbody>{rows}</tbody></table>
<p class="no-print">Print this page, or save it as a PDF from your browser's print
dialog. It's free: no email, no sign-up.</p>
<p class="no-print">The example is how the
<a href="../local-business-automation-pack/">Local Business Automation Pack</a> is built:
it drafts the reply and leaves sending to you.</p>""")


def _start(cat: Catalog, videos: Iterable[Video] | None) -> tuple[str, str]:
    first = next(iter(published(videos)), None)
    lesson = ""
    if first is not None:
        _s, _ep, v = first
        lesson = (f'<p><a class="btn btn-ink" href="https://www.youtube.com/watch?v='
                  f'{quote(v.video_id)}" rel="noopener">Watch the first free lesson</a> '
                  f'<a class="btn btn-ghost" href="../{VIDEOS_SLUG}/">All free lessons</a></p>')
    routes = ""
    for label, slug in ROUTES:
        p = cat.product(slug)
        if p is None:
            continue
        routes += (f'<li><a href="../{p.slug}/"><b>{_e(label)}</b><br>'
                   f'{_e(p.title)}: {_e(p.summary)}</a></li>')
    return _prose(cat, slug=START_SLUG, title="Start here", crumb="Start here",
                  description="Where to begin with ATLAS Academy: a free lesson, a free "
                              "worksheet, and the course or kit that fits what you do.",
                  html=f"""
<p class="lede">Practical AI for your business. See it work, learn the steps, stay in
control.</p>
<h2>Watch a free lesson</h2>{lesson or '<p><a href="../videos/">The free lessons</a></p>'}
<h2>Use the free worksheet</h2>
<p><a href="../{WORKSHEET_SLUG}/">Before You Automate</a>: decide what an automation does, who
checks it, and what stays manual, before you build it.</p>
<h2>Pick what fits what you do</h2>
<link rel="stylesheet" href="../assets/worksheet.css">
<ul class="routes">{routes}</ul>""")


def _mail_link() -> str:
    mail = _e(CONTACT_MAIL)
    return f'<a href="mailto:{mail}">{mail}</a>'


def _phone_link() -> str:
    return (f'<a href="tel:{_e(store_origin.CONTACT_PHONE)}">'
            f'{_e(store_origin.CONTACT_PHONE_DISPLAY)}</a>')


def _legal(cat: Catalog) -> list[tuple[str, str]]:
    email = _e(cat.contact_email)
    mail = f'<a href="mailto:{email}">{email}</a>'
    form_mail = _mail_link()
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
ones. Keep the page bookmarked. If you lose it, recover it on the <a
href="../{RECOVER_SLUG}/">recover page</a> with your Stripe receipt number, or email
{mail} from the address you paid with.</p>
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
and images are served from this site, with one exception: the pictures in the video
gallery on the home page (see below). Its main script runs the menu, the "find your fit" picker and the demonstrations on the pages; it
makes no request and stores nothing. A catalog search, its filters and the titles you
pick to compare are written into the page address, so the view can be reloaded or
shared; like any address, that shows in your browser's history, and nothing else
keeps it. The Help page also runs the help chat's script,
which sends only the question you type to ATLAS Academy support to answer it, with no
cookies, and stores nothing. On the home page, a free lesson opens on
YouTube only when you follow its link.</p>
<p>The blog's script runs its search and topic filter and makes no request. If you
tick a lesson as finished there, it keeps the list of lessons you ticked in your
browser's local storage, on your device only: it is never sent anywhere, and "Clear
my progress" on the blog removes it. Nothing else is stored.</p>
<p>The video gallery on the home page has its own small script. When you scroll near
the gallery it asks this site for the list of free videos (it sends nothing and
stores nothing), then shows each video's picture, which your browser loads from
YouTube's image servers (i.ytimg.com). That is the only request this site makes to
another host, and it is for images only: YouTube can see your address and browser
details for those requests, under YouTube's own privacy policy. Nothing else from
YouTube loads until you press a video, which opens on YouTube in a new tab. Without
the script, the gallery asks YouTube for nothing.</p>
<p>The Free Lessons page shows each lesson's picture from this site: it is copied
when the store is built, so your browser never asks YouTube for it. Only when you
press play does the page load YouTube's player, in its privacy-enhanced mode
(youtube-nocookie.com). That mode is not the same as no tracking: from then on,
YouTube's own privacy policy applies to that player. "Watch on YouTube" and "Subscribe" take you to YouTube itself.</p>
<h2>When you buy</h2>
<p>Stripe processes your payment and shares with us what it needs to: your name and
email address, what you bought, the amount, and whether the payment went through or
was refunded. We keep a record of each purchase (the checkout reference, your email,
the products, the amount and its status) so we can deliver your files and help you if
you lose your link.</p>
<h2>Recovering a purchase</h2>
<p>To recover a purchase you email us the receipt number from the address you paid
with (the <a href="../{RECOVER_SLUG}/">recover page</a> says how). We ask Stripe whether
they match a purchase and send your access link to that address only. Prefer to talk? Call {_phone_link()}.</p>
<h2>Email about a purchase</h2>
<p>We email you about a purchase only when you ask, for example to resend your access
link. Buying does not sign you up for updates or a newsletter: the two are separate,
and nothing you buy adds you to any list.</p>
<h2 id="updates">Free updates (optional)</h2>
<p>You can ask for free updates, with no purchase, by typing your email address in the
form on the home page or in the footer of any page. That address is the only thing the
form collects: no name, no password, no account. We keep the address, when you signed
up, when you confirmed it (and when you unsubscribed, if you do) and which form you
used. We do not keep your device or network address for this.</p>
<p>It is a double opt-in. Right after you submit, we email that address one message
with a link to confirm. Until you open the link you are not on the list, you receive
nothing else, and an address that is not confirmed within 7 days is deleted. If someone
typed your address by mistake, do nothing and it is deleted.</p>
<p>Every update email has a link that unsubscribes you in one click, with no sign-in.
We keep an unsubscribed address, marked so, only so that we never email it again.
Updates are sent by ATLAS Academy itself and your address is not shared with anyone.</p>
<h2>Your choices</h2>
<p>To see or delete what we hold about you, including a subscriber address, email {mail}
and we will delete it. Stripe's own privacy policy covers what Stripe keeps.</p>"""),
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
               head=CONTACT_SCRIPT,
               description=f"How to reach {cat.store['name']}.", html=f"""
<p class="lede">Write below, or email {form_mail}. Messages reach {author} directly, and he
replies by email within 2 business days.</p>
<form class="contact" method="post" action="{_e(CONTACT_ACTION)}" data-contact-form>
<input type="hidden" name="source" value="store-contact">
<input type="hidden" name="topic" value="question">
<p class="hp" aria-hidden="true"><label>Leave this empty <input type="text" name="website" tabindex="-1" autocomplete="off"></label></p>
<label><span>Your name</span>
<input name="name" type="text" required maxlength="80" autocomplete="name"></label>
<label><span>Your email</span>
<input name="email" type="email" required maxlength="254" autocomplete="email" inputmode="email"></label>
<label><span>Your message</span>
<textarea name="message" required minlength="{MESSAGE_MIN}" maxlength="{MESSAGE_MAX}" rows="7"></textarea></label>
<div><button class="btn btn-buy" type="submit" data-contact-submit>Send message</button></div>
<p class="status" role="status" aria-live="polite" data-contact-status></p>
<p class="note">the owner reads every message himself and replies to your email address. Not
working? Write to {form_mail}.</p>
</form>
<div class="cards-2">
<div><h3>About an order</h3><p>Write from the address you paid with and include your Stripe
receipt number if you have it.</p></div>
<div id="lost"><h3>Lost your access page?</h3><p>See <a href="../{RECOVER_SLUG}/">how to
get it back</a>: email us from the address you paid with, and we'll send the link to that
address only.</p></div>
</div>"""),
    ]
    pages.append(_recover_page(cat))
    return pages


def _updates_pages(cat: Catalog) -> list[tuple[str, str]]:
    """/updates/ (the signup on a page of its own, the form's fallback when
    the API says no) and /updates/thanks/ (where every accepted signup lands).
    Only /updates/ is in the sitemap; the thanks page is noindex."""
    store = _e(cat.store["name"])
    page = f"""<section class="page"><div class="wrap"><div class="crumbs" style="padding:0">
<a href="../academy/">Home</a><span>/</span>Free updates</div>
<h1>Free updates, no purchase needed</h1>
<div class="prose"><p class="lede">New free lessons and store news from {store}, by email. Type your address and press the button. We email you a link to confirm; until you open it you are not signed up.</p></div>
<div class="sub-page">{_subscribe_form("page", "page")}</div>
<ul class="sub-points">
<li>Just your email address: no account, no password, no name.</li>
<li>Buying a course never signs you up for this.</li>
<li>Every email has a link that unsubscribes you in one click.</li>
</ul>
<p class="sub-fine">Details in the <a href="../privacy/#updates">privacy policy</a>.</p>
</div></section>"""
    thanks = f"""<section class="page"><div class="wrap sub-thanks">
<p class="sub-mark" aria-hidden="true">{TICK_SVG}</p>
<h1>Check your email</h1>
<div class="prose"><p class="lede">If that address can be signed up, we have sent it one message with a link to confirm. Open the link within 7 days and you are on the list.</p>
<p>Nothing arrives? Look in spam, or <a href="../">go back</a> and try again. If it was a mistake, do nothing and the address is deleted.</p></div>
<p><a class="btn btn-ghost" href="../../academy/">Back to the store</a></p>
</div></section>"""
    return [
        (f"{UPDATES_SLUG}/index.html",
         _page(cat, title="Free updates",
               description="Get free ATLAS Academy lessons and store news by email: "
                           "no purchase, no account, confirm by email first.",
               body=page, depth=1)),
        (f"{UPDATES_SLUG}/thanks/index.html",
         _page(cat, title="Check your email",
               description="Confirm your address to get free ATLAS Academy updates.",
               body=thanks, depth=2,
               head='<meta name="robots" content="noindex">')),
    ]


def _recover_page(cat: Catalog) -> tuple[str, str]:
    """Plan F17: a buyer gets their purchases back with two things from
    their own inbox. 2026-10-29: an email to CONTACT_MAIL (ATLAS is
    local-only), no form and no script."""
    return _prose(cat, slug=RECOVER_SLUG, title="Recover your purchases",
                  current="contact",
                  description="Get your ATLAS Academy downloads back with your Stripe "
                              "receipt number.", html=f"""
<p class="lede">Lost your access page? Email {_mail_link()} from the address your
Stripe receipt was sent to, with the receipt number on it. We check it with Stripe and
send your access link to that address only.</p>
<p><a class="btn" href="{_e(store_origin.mailto(RECOVER_SUBJECT, RECOVER_BODY))}">Email us
to recover my purchases</a></p>
<div class="cards-2">
<div><h3>Where is the receipt number?</h3><p>In the email Stripe sent when you paid
(its subject starts "Your receipt from"), near the top: "Receipt #" followed by two
groups of digits joined by a dash. Copy it into the email.</p></div>
<div><h3>Can't find your receipt?</h3><p>Check spam and other folders for an email from
Stripe. Still nothing? Write to {_mail_link()} from the address you paid with anyway.</p></div>
</div>""")


def _not_found(cat: Catalog) -> str:
    body = ('<section class="page"><div class="wrap"><h1>This page isn\'t here.</h1>'
            '<div class="prose"><p>The link may be old. <a href="/academy/">Go to the catalog</a>.</p>'
            "</div></div></section>")
    # Served for any missing path, so every link on it is absolute.
    return _page(cat, title="Not found", description="Page not found.", body=body,
                 depth=0, root="/")


# ------------------------------------------------------------------ videos
#
# the owner, 2026-10-11: "post all the videos in a separate page in
# agency.example.com so people can buy courses there and also view the videos
# on my website". /videos/ lists every tutorial that is public on YouTube
# and not withdrawn (academy_tutorial_posts, read by publish.public_videos),
# one section per series in catalog order, with the series' course beside
# it. Every word is the series catalog's own (tutorial_series.py) or the
# store catalog's; nothing upcoming is shown as if it were up.
#
# Privacy: the page loads nothing from YouTube until a visitor presses
# play. The poster is the episode's own thumbnail, served from this site
# (or a drawn one when the render is gone); the player is an <a> to
# youtu.be that app.js swaps for a youtube-nocookie.com iframe on click.

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class Video:
    """One published tutorial: its posts row, plus its thumbnail's bytes
    when the render is still on disk."""
    series: str
    episode: int
    video_id: str
    uploaded: datetime
    thumb: bytes | None = None
    # The published render's length (its manifest), None when it isn't on
    # disk: then the card shows no length rather than a guessed one.
    seconds: float | None = None
    # The same manifest's chapters (start seconds, name), voiced opener and
    # narrated lines (start, end, said), for the lesson page
    # (lesson_pages.py); empty without a trusted manifest
    # (publish._with_manifests).
    chapters: tuple[tuple[float, str], ...] = ()
    opener: str = ""
    timings: tuple[tuple[float, float, str], ...] = ()


# Each published lesson's own page (lesson_pages.py, shop master plan F11).
LESSONS_SLUG = "lessons"


def lesson_slug(series: str, episode: int) -> str:
    """"lessons/<series>-<n>": the lesson page's directory under the site."""
    return f"{LESSONS_SLUG}/{series}-{episode}"


def _resolve(v: Video) -> tuple[ts.Series, ts.Episode] | None:
    s = ts.series(v.series)
    ep = s.episode(v.episode) if s is not None else None
    if s is None or ep is None or not VIDEO_ID_RE.fullmatch(v.video_id):
        return None
    return s, ep


def published(videos: Iterable[Video] | None
              ) -> list[tuple[ts.Series, ts.Episode, Video]]:
    """The videos the page shows, in catalog order: known episodes with a
    well-formed id, one per episode (the latest upload wins)."""
    latest: dict[tuple[str, int], Video] = {}
    for v in videos or ():
        if _resolve(v) is None:
            continue
        key = (v.series, v.episode)
        if key not in latest or v.uploaded >= latest[key].uploaded:
            latest[key] = v
    out = []
    for s in ts.SERIES:
        for ep in s.episodes:
            v = latest.get((s.key, ep.number))
            if v is not None:
                out.append((s, ep, v))
    return out


def render_signature(v: Video) -> str:
    """A short digest of what a video's trusted manifest put on its lesson
    page (length, chapters, opener, narrated lines); "" with none."""
    if v.seconds is None and not (v.chapters or v.timings or v.opener):
        return ""
    data = json.dumps([v.seconds, v.chapters, v.opener, v.timings], ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def videos_manifest(videos: Iterable[Video] | None) -> dict[str, object]:
    """videos/videos.json: what the page lists, and nothing that changes by
    itself, so the refresh can compare it with the database and the renders
    on disk. A video with a trusted manifest carries its render_signature,
    so new manifest data republishes its lesson page."""
    out = []
    for s, ep, v in published(videos):
        row: dict[str, object] = {"series": s.key, "episode": ep.number,
                                  "video_id": v.video_id}
        if sig := render_signature(v):
            row["render"] = sig
        out.append(row)
    return {"videos": out}


def _thumb_path(v: Video) -> str:
    return f"assets/videos/{v.video_id}.{'jpg' if v.thumb else 'svg'}"


def video_poster_svg(s: ts.Series, ep: ts.Episode) -> str:
    """A poster for a video whose thumbnail is no longer on disk: its series,
    its level and its title, in the store's colours."""
    lines: list[str] = []
    line = ""
    for w in ep.title.split():
        if line and len(line) + len(w) + 1 > 34:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    rows = "".join(f'<text x="80" y="{300 + i * 62}">{_e(t)}</text>'
                   for i, t in enumerate(lines[:4]))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720" role="img"
 aria-label="{_e(ep.title)}">
<rect width="1280" height="720" fill="#0a0d14"/>
<rect x="80" y="120" width="56" height="8" fill="#5b8cff"/>
<text x="80" y="200" fill="#8e99ae" font-family="Atkinson Hyperlegible, system-ui, sans-serif"
 font-size="34" font-weight="700">{_e(s.mark)} &#183; {_e(ep.level_label)}</text>
<g fill="#eef2fa" font-family="Atkinson Hyperlegible, system-ui, sans-serif" font-size="52"
 font-weight="800">{rows}</g>
</svg>
"""


def _first_sentence(text: str, limit: int = 220) -> str:
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    out = m.group(1) if m else text
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "…"
    return out


def _embed(video_id: str) -> str:
    return f"{YT_EMBED}/{video_id}?autoplay=1&rel=0"


def _topic(s: ts.Series, ep: ts.Episode) -> str:
    return ep.topic_shown or ep.thumb_title.replace("\n", " ")


def _duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    total = round(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _takeaway(ep: ts.Episode, n: int = 3) -> str:
    """What the lesson covers: its first chapters, as the script names them."""
    chapters = [sc.chapter for sc in ep.scenes if sc.chapter][:n]
    return "; ".join(chapters)


def _related(cat: Catalog, s: ts.Series, ep: ts.Episode) -> str:
    """The card's one related title, with its real Buy, only while on sale."""
    pair = lesson_pairing(cat, s.key, ep.number)
    if pair is None or not any(o.buy_live for o in pair[0].offers):
        return ""
    p, why = pair
    o = _single_live(p)
    buy = (buy_button(o, label="Buy", extra="btn-sm") if o is not None else
           f'<a class="btn btn-ghost btn-sm" href="../{p.slug}/#buy">Choose edition</a>')
    return f"""<div class="vrel">
<a class="vrel-cover" href="../{p.slug}/" tabindex="-1" aria-hidden="true"><img src="{cover_src(p, "../")}" alt="" width="800" height="800" loading="lazy" decoding="async"></a>
<div><p class="vrel-k">Related {_e(p.kind.lower())}</p><a class="vrel-t" href="../{p.slug}/">{_e(p.title)}</a><p class="why-pair">{_e(why)}</p></div>
<div class="vrel-buy">{_from_price(p)}{buy}</div>
</div>"""


def _video_card(cat: Catalog, s: ts.Series, ep: ts.Episode, v: Video) -> str:
    vid, title = v.video_id, _e(ep.title)
    day = v.uploaded.astimezone(UTC)
    length = _duration(v.seconds)
    length_html = (f'<span class="vlen"><span class="sr">Length </span>{length}</span>'
                   if length else "")
    takeaway = _takeaway(ep)
    take_html = (f'<p class="vtake"><b>You\'ll learn:</b> {_e(takeaway)}</p>'
                 if takeaway else "")
    return f"""<article class="vcard reveal" id="v-{vid}">
<a class="yt" target="_blank" rel="noopener noreferrer" href="https://youtu.be/{vid}" data-embed="{_e(_embed(vid))}" data-title="{title}" aria-label="Watch on YouTube: {title} (opens in a new tab)">
<img src="../{_thumb_path(v)}" alt="" width="1280" height="720" loading="lazy" decoding="async">
<span class="play" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg></span>
</a>
<p class="vplay"><span>Opens on YouTube in a new tab.</span><a href="https://youtu.be/{vid}" rel="noopener">Watch on YouTube</a></p>
<div class="body">
<p class="vtopic">{_e(_topic(s, ep))}</p>
<p class="vmeta"><span class="tag tag-accent">{_e(ep.level_label)}</span>{length_html}<time datetime="{day.date().isoformat()}">{day.day} {day:%b %Y}</time></p>
<h3>{title}</h3>
{take_html}
<p class="vlinks"><a href="../{lesson_slug(s.key, ep.number)}/">Lesson page</a><a href="{CHANNEL_URL}?sub_confirmation=1" rel="noopener">Subscribe on YouTube</a></p>
{_related(cat, s, ep)}
</div>
</article>"""


def _ladder(s: ts.Series, lit: set[int]) -> str:
    """The series' levels, Basics to Advanced; a level with a published
    episode is lit."""
    levels = sorted({e.level for e in s.episodes})
    steps = "".join(
        f'<li{" class=\"on\"" if lvl in lit else ""}><b>Level {lvl}</b>'
        f"{_e(ts.LEVEL_NAMES[lvl])}</li>" for lvl in levels)
    return f'<ol class="ladder" aria-label="Levels in this series">{steps}</ol>'


def _next_up(s: ts.Series, shown: set[int]) -> str:
    """The next scripted episode of the series, named only: never a card."""
    nxt = next((e for e in s.episodes if e.scripted and e.number not in shown), None)
    if nxt is None:
        return ""
    return f'<p class="next-up muted">Next up in this series: {_e(nxt.title)}</p>'


def _videos(cat: Catalog, videos: Iterable[Video] | None) -> str:
    shown = published(videos)
    sections = []
    for s in ts.SERIES:
        mine = [(ep, v) for s2, ep, v in shown if s2.key == s.key]
        if not mine:
            continue
        sid = f"series-{s.key}"
        ladder = _ladder(s, {ep.level for ep, _v in mine}) if s.playlist else ""
        cards = "".join(_video_card(cat, s, ep, v) for ep, v in mine)
        sections.append(f"""<section class="vseries" id="{sid}" aria-labelledby="{sid}-h"><div class="wrap">
<div class="section-head"><div><p class="eyebrow">{_e(s.mark)}</p><h2 id="{sid}-h">{_e(s.playlist or "Start here")}</h2>{ladder}</div></div>
<div class="vlist">{cards}{_next_up(s, {ep.number for ep, _v in mine})}</div>
</div></section>""")
    if not sections:
        sections.append('<section class="vseries"><div class="wrap"><p class="lede">'
                        "No lesson is published here yet. Subscribe on YouTube to see "
                        "the first one when it goes up.</p></div></section>")
    body = f"""<section class="vhero"><div class="wrap">
<p class="eyebrow">Free Lessons</p>
<h1>Learn it free. <em>Go deeper</em> here.</h1>
<p class="lede">Step-by-step lessons on Claude, n8n and AI automation, from the very basics up, in plain words. Every one is free on YouTube; the courses in this store take you further.</p>
<div class="hero-ctas">
<a class="btn btn-ink" href="{CHANNEL_URL}?sub_confirmation=1" rel="noopener">Subscribe on YouTube</a>
<a class="btn btn-ghost" href="../academy/#catalog">Browse the courses</a>
</div>
<p class="vnote muted">The pictures here are served by this store. Nothing loads from YouTube until you press a lesson; it then opens on YouTube in a new tab, under YouTube's own privacy policy.</p>
</div></section>
{"".join(sections)}
<section class="close"><div class="wrap"><div class="box">
<h2>Go deeper: courses at agency.example.com</h2>
<p>The videos show you how. The courses and kits give you the whole method and the files to keep.</p>
<div class="hero-ctas"><a class="btn btn-gold" href="../academy/#catalog">Browse the courses</a><a class="btn btn-ghost" href="../{cat.bundle.slug}/">See the bundle</a></div>
</div></div></section>"""
    return _page(cat, title="Free Lessons",
                 description="Free step-by-step video lessons on Claude, n8n and AI "
                             "automation by ATLAS Academy, from the basics up, with "
                             "the courses that go deeper.",
                 body=body, depth=1, current="lessons")


def videos_ld(cat: Catalog, videos: Iterable[Video] | None) -> dict:
    """schema.org: the page, and a VideoObject per published lesson."""
    base = cat.base_url
    graph: list[dict] = [{"@type": "CollectionPage", "name": "Free Lessons",
                          "url": f"{base}/{VIDEOS_SLUG}/",
                          "isPartOf": {"@type": "OnlineStore", "name": cat.store["name"],
                                       "url": f"{base}/"}}]
    graph += [video_object(cat, ep, v) for _s, ep, v in published(videos)]
    return {"@context": "https://schema.org", "@graph": graph}


def video_object(cat: Catalog, ep: ts.Episode, v: Video) -> dict:
    """schema.org VideoObject for one published lesson (the /videos/ entry;
    the lesson page sets its own url). A duration only from the manifest."""
    base = cat.base_url
    data: dict = {
        "@type": "VideoObject", "name": ep.title, "description": ep.summary,
        "thumbnailUrl": [f"https://i.ytimg.com/vi/{v.video_id}/hqdefault.jpg",
                         f"{base}/{_thumb_path(v)}"],
        "uploadDate": v.uploaded.astimezone(UTC).isoformat(timespec="seconds")
                      .replace("+00:00", "Z"),
        "embedUrl": f"{YT_EMBED}/{v.video_id}",
        "contentUrl": f"https://youtu.be/{v.video_id}",
        "url": f"{base}/{VIDEOS_SLUG}/#v-{v.video_id}",
        "inLanguage": "en",
    }
    if v.seconds and v.seconds > 0:
        total = round(v.seconds)
        data["duration"] = f"PT{total // 60}M{total % 60}S"
    return data


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
        prefix = f'<tspan fill="#5b8cff">{_e(folder)}</tspan>' if folder else ""
        rows.append(f'<text x="{x}" y="{y0 + i * step}">{prefix}{_e(name)}</text>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" width="800" height="800" role="img"
 aria-label="{_e(p.cover_alt)}">
<defs><radialGradient id="g" cx=".85" cy=".08" r=".7"><stop offset="0" stop-color="#5b8cff" stop-opacity=".3"/><stop offset="1" stop-color="#5b8cff" stop-opacity="0"/></radialGradient>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#8fb0ff" stroke-opacity=".06"/></pattern></defs>
<rect width="800" height="800" fill="#0a0d14"/><rect width="800" height="800" fill="url(#grid)"/><rect width="800" height="800" fill="url(#g)"/>
<rect x="40" y="40" width="720" height="720" rx="14" fill="none" stroke="#5b8cff" stroke-opacity=".45" stroke-width="2"/>
<text x="90" y="130" fill="#eef2fa" font-family="Atkinson Hyperlegible, system-ui, sans-serif"
 font-size="54" font-weight="800" letter-spacing="-1">Claude + Obsidian</text>
<text x="90" y="190" fill="#5b8cff" font-family="Atkinson Hyperlegible, system-ui, sans-serif"
 font-size="54" font-weight="800" letter-spacing="-1">Vault Kit</text>
<g fill="#c9d2e3" font-family="ui-monospace, Menlo, monospace" font-size="27">
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
            "store": cat.store["name"], "url": f"{base}/{HOME_SLUG}/",
            "products": entries}


# ------------------------------------------------- search and AI assistants
#
# the owner, 2026-10-06 ("is the search engine optimization complete?"): the
# store publishes a sitemap, an llms.txt that an AI assistant can read in one
# fetch, robots rules that name and allow the assistants' crawlers, and
# schema.org records for the store and each product. Every fact is the
# catalog's own; no rating, review count or sales figure is ever written
# (none exists to cite), and a price appears only when the offer is on sale.

SITEMAP_FILE = "sitemap-academy.xml"

AI_CRAWLERS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot",
               "anthropic-ai", "PerplexityBot", "Google-Extended", "Applebot",
               "Bingbot")


def _page_urls(cat: Catalog, videos: Iterable[Video] | None = None) -> list[str]:
    base = cat.base_url
    urls = [f"{base}/{HOME_SLUG}/"] + [f"{base}/{p.slug}/" for p in cat.products]
    urls.append(f"{base}/{cat.bundle.slug}/")
    urls.append(f"{base}/{VIDEOS_SLUG}/")
    urls.append(f"{base}/{JOIN_SLUG}/")
    urls.append(f"{base}/{UPDATES_SLUG}/")
    urls += [f"{base}/{lesson_slug(s.key, ep.number)}/" for s, ep, _v in published(videos)]
    from .blog_pages import page_urls as blog_urls
    urls += blog_urls(cat)
    urls.append(f"{base}/help/")
    if start_page_enabled():
        urls += [f"{base}/{START_SLUG}/", f"{base}/{WORKSHEET_SLUG}/"]
    urls += [f"{base}/{path.removesuffix('index.html')}" for path, _ in _legal(cat)]
    return list(dict.fromkeys(urls))


def sitemap_xml(cat: Catalog, now: datetime,
                videos: Iterable[Video] | None = None) -> str:
    day = now.date().isoformat()
    rows = "".join(f"  <url><loc>{_e(u)}</loc><lastmod>{day}</lastmod></url>\n"
                   for u in _page_urls(cat, videos))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{rows}</urlset>\n")


def llms_txt(cat: Catalog, now: datetime, videos: Iterable[Video] | None = None) -> str:
    base = cat.base_url
    lines = [f"# {cat.store['name']}", "",
             f"> {cat.store.get('tagline') or 'Courses, kits and workflows for putting AI to work in a small business or at home.'}",
             "",
             f"- Store: {base}/{HOME_SLUG}/",
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
    shown = published(videos)
    lines += ["", "## Free Lessons (video)", "",
              f"- All lessons: {base}/{VIDEOS_SLUG}/ (YouTube channel: {CHANNEL_URL})"]
    lines += [f"- [{ep.title}](https://youtu.be/{v.video_id}): "
              f"{s.playlist or 'Channel welcome'}, {ep.level_label}"
              for s, ep, v in shown]
    from .blog_pages import live as live_posts
    lines += ["", "## Blog", "", f"- All posts: {base}/blog/ (RSS: {base}/blog/feed.xml)"]
    lines += [f"- [{p.title}]({base}/{p.path}): {p.summary}" for p in live_posts(cat)]
    lines += ["", (f"Prices as published on {now.date().isoformat()}; the store "
                   "page is the source of truth.")]
    return "\n".join(lines) + "\n"


def _ld(data: dict) -> str:
    # `</` cannot close the script early once escaped.
    return ('<script type="application/ld+json">'
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/") + "</script>")


def store_ld(cat: Catalog) -> dict:
    return {"@context": "https://schema.org", "@type": "OnlineStore",
            "name": cat.store["name"], "url": f"{cat.base_url}/{HOME_SLUG}/",
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
           reviews: list[dict] | None = None,
           videos: Iterable[Video] | None = None,
           lessons: list[dict] | None = None) -> dict[str, bytes]:
    """The whole store as it is served: every page under academy/ (mount.py),
    its links, canonicals, sitemap and llms.txt under /academy/. See
    `render_root` for the arguments."""
    return mount.relocate(render_root(cat, now, reviews, videos, lessons),
                          cat.base_url.removeprefix("https://"))


def render_root(cat: Catalog, now: datetime | None = None,
                reviews: list[dict] | None = None,
                videos: Iterable[Video] | None = None,
                lessons: list[dict] | None = None) -> dict[str, bytes]:
    """The store's pages in the layout they are written in, as if the store sat
    at the host root (relative links, bare paths). Tests of a page's content
    read this; `render` is what ships. `reviews`: approved verified-buyer reviews (academy.reviews.approved),
    none by default, so the plain render carries no review at all.
    `videos`: the published tutorials (publish.public_videos), none by
    default, so the plain render's /videos/ lists nothing.
    `lessons`: published free tutorials, newest first (publish.py
    `published_lessons`), each {series, episode, video_id, title, summary,
    mark}; none by default, and the featured-lesson section is then left out."""
    videos = list(videos or ())
    now = now or datetime.now(UTC)
    files: dict[str, bytes] = {
        # No root index.html: https://agency.example.com/ is the landing (Caddy).
        f"{HOME_SLUG}/index.html": _home(cat, lessons, videos, now).encode(),
        f"{cat.bundle.slug}/index.html": _bundle(cat).encode(),
        "404.html": _not_found(cat).encode(),
        "catalog.json": (json.dumps(public_catalog(cat, now), indent=2) + "\n").encode(),
        # The host has ONE robots.txt, sitemap.xml and llms.txt: the landing's
        # (Caddyfile.public). The store adds its own sitemap, named in that
        # robots.txt, and its own llms.txt, linked from the landing's.
        SITEMAP_FILE: sitemap_xml(cat, now, videos).encode(),
        f"{HOME_SLUG}/llms.txt": llms_txt(cat, now, videos).encode(),
        f"{VIDEOS_SLUG}/index.html": _with_ld(_videos(cat, videos),
                                             videos_ld(cat, videos)).encode(),
        f"{VIDEOS_SLUG}/videos.json": (json.dumps(videos_manifest(videos), indent=2)
                                       + "\n").encode(),
    }
    for s, ep, v in published(videos):
        files[_thumb_path(v)] = v.thumb or video_poster_svg(s, ep).encode()
    # A page per published lesson, lessons/<series>-<n>/ (lesson_pages.py):
    # the same list as /videos/, its cards' "Lesson page" links and the
    # sitemap's entries.
    from .lesson_pages import files as lesson_files
    files.update(lesson_files(cat, videos))
    # The tutorials' free lab files (docs/academy-youtube-plan.md rule 12):
    # served from the store itself at /free/<name>, the very strings the
    # videos show (tutorial_series.LAB_FILES).
    for name, text in ts.LAB_FILES.items():
        files[f"{ts.LAB_DIR}/{name}"] = text.encode()
    home = f"{HOME_SLUG}/index.html"
    files[home] = _with_ld(files[home].decode(), store_ld(cat)).encode()
    for p in cat.products:
        files[f"{p.slug}/index.html"] = _with_ld(
            _product(cat, p, reviews, lessons), product_ld(cat, p)).encode()
        if not p.cover:
            files[f"assets/covers/{p.slug}.svg"] = vault_cover_svg(p).encode()
    for path, html in _legal(cat):
        files[path] = html.encode()
    path, html = _join(cat)
    files[path] = html.encode()
    for path, html in _updates_pages(cat):
        files[path] = html.encode()
    if start_page_enabled():
        for path, html in (_start(cat, videos), _worksheet(cat)):
            files[path] = html.encode()
    # style.css, app.js, icon.svg, the fonts and the covers.
    for f in sorted(ASSETS.rglob("*")):
        if f.is_file() and not f.name.startswith("."):
            files[f"assets/{f.relative_to(ASSETS).as_posix()}"] = f.read_bytes()
    # /help/ (FAQ, support policy, form, AI help chat) and its first-party
    # chat script and style, from the support lane.
    from .support_site import files as help_files
    files.update(help_files(cat))
    # /blog/ (blog_pages.py): the owner's posts, the feed and the post index; its
    # "Latest from the channel" is the /videos/ list.
    from .blog_pages import files as blog_files
    files.update(blog_files(cat, videos))
    # Search Console, 2026-10-29: every page the sitemap lists names its own
    # sitemap address as canonical (lesson pages already do); pages left out
    # of the sitemap (404, the "sent" confirmations) claim none.
    base = f"{cat.base_url}/"
    for url in _page_urls(cat, videos):
        path = url.removeprefix(base) + "index.html"
        html = files.get(path)
        if html is not None and b'rel="canonical"' not in html:
            files[path] = _with_canonical(html.decode(), url).encode()
    return files


def _with_canonical(html: str, url: str) -> str:
    return html.replace("</head>", f'<link rel="canonical" href="{_e(url)}">\n</head>',
                        1)


def build(out_dir: Path, cat: Catalog | None = None,
          now: datetime | None = None, reviews: list[dict] | None = None,
          videos: Iterable[Video] | None = None,
          lessons: list[dict] | None = None) -> list[Path]:
    """Write the site into `out_dir` (which must not exist yet, or be empty).
    Blocking; callers on the event loop use asyncio.to_thread."""
    cat = cat or load()
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.iterdir()):
        raise ValueError(f"{out_dir} is not empty")
    written = []
    for rel, data in render(cat, now, reviews=reviews, videos=videos,
                            lessons=lessons).items():
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
