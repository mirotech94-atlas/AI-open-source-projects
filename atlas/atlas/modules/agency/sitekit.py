"""The 20 launch items, rendered (Miro, 2026-09-29, reel DdH5iyOzkZH).

Every client site ATLAS builds — a brochure from a GetLayers mockup or a
one-product store page — leaves `agency.build_site` with the same set of
pages and the same pieces added to its home page:

* pages: `index.html`, `contact.html`, `privacy.html`, `terms.html`,
  `404.html`, `robots.txt`, `sitemap.xml` (when the domain is known),
  `thank-you.html` (only when there is a form to send people there) and a
  1200x630 share image;
* on the home page: a CTA above the fold with the response-time promise
  beside it, the internal links, the sections the client's data supports
  (reviews, case studies, team, FAQ, visit/directions), LocalBusiness and
  FAQPage JSON-LD, a unique title and meta description, og/twitter share
  tags, a sticky call/book bar on small screens, and the analytics snippet
  when PostHog is configured.

Honesty is structural, not a prompt. Nothing in this file writes a claim:
every sentence is either fixed wording about the website itself or a value
from `ClientData` — the lead row plus the client's own `client.json`. A
section whose data is missing (reviews, case studies, team photo) is not
rendered at all, never filled with a placeholder; the response-time
promise without a stated number says no number; FAQ answers are the
client's own or restate a fact already on record (their phone, their
address, their hours). Every value is HTML-escaped where it lands, and
JSON-LD is serialised with `<`, `>` and `&` escaped so a value can never
close its `<script>`.

The mockup is model output, so the home page is never rebuilt from
scratch: `enhance_index` reads it with the stdlib HTML parser and adds only
what is missing, at the edges (end of `<head>`, start and end of `<body>`).
Whatever the mockup itself claims is the launch check's job to catch
(`launchcheck.py`), not this file's to rewrite.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote_plus, urlparse

from .client import ClientData, Faq
from .ogimage import _contrast, _readable, _rgb

INDEX, CONTACT, PRIVACY, TERMS = "index.html", "contact.html", "privacy.html", "terms.html"
THANKS, NOT_FOUND = "thank-you.html", "404.html"
SHARE_IMAGE = "og-image.png"
# The AI chat assistant's one file ($500 and $300 packages, `sitechat.py`).
CHAT_WIDGET = "mapfront-chat.js"
FAQ_TARGET = 5
FAQ_MAX = 8

_DAYS = {"Mo": "Monday", "Tu": "Tuesday", "We": "Wednesday", "Th": "Thursday",
         "Fr": "Friday", "Sa": "Saturday", "Su": "Sunday"}
_PHC = re.compile(r"^phc_[A-Za-z0-9_-]{8,}$")
_CSS_BRAND = re.compile(
    r"--(?:primary|brand|accent)[\w-]*\s*:\s*(#[0-9a-fA-F]{6})\b")


# ------------------------------------------------------------------ analytics

@dataclass(frozen=True, slots=True)
class Analytics:
    """PostHog, configured by env. The project key a page carries (`phc_`)
    is public by design — it can only send events — so it may sit in the
    HTML; the personal key the posthog module reads with never does."""
    key: str
    host: str


def analytics_from_env() -> Analytics | None:
    """Off unless `POSTHOG_PUBLIC_KEY` is a `phc_` project key. The ingest
    host comes from `POSTHOG_HOST`: PostHog Cloud's app hosts
    (`us.posthog.com`, `eu.posthog.com`, which the read module uses) map to
    their documented ingest hosts (`us.i.posthog.com`, `eu.i.posthog.com`);
    any other https host (a proxy, self-hosted) is used as given. Unset
    means PostHog Cloud US, the same default the read module has."""
    key = os.getenv("POSTHOG_PUBLIC_KEY", "").strip()
    if not _PHC.match(key):
        return None
    raw = os.getenv("POSTHOG_HOST", "").strip().rstrip("/") or "https://us.i.posthog.com"
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    host = {"us.posthog.com": "us.i.posthog.com", "app.posthog.com": "us.i.posthog.com",
            "eu.posthog.com": "eu.i.posthog.com"}.get(parsed.netloc.lower(),
                                                      parsed.netloc.lower())
    return Analytics(key, f"https://{host}")


def analytics_snippet(analytics: Analytics) -> str:
    """PostHog's loader stub (it queues calls until `array.js` arrives and
    replays them), set up the privacy-respecting way: memory persistence
    (no cookie, nothing in localStorage), no session recording, no
    autocapture, Do Not Track respected, anonymous page views only."""
    config = {"api_host": analytics.host, "persistence": "memory",
              "person_profiles": "identified_only", "autocapture": False,
              "capture_pageview": True, "disable_session_recording": True,
              "respect_dnt": True}
    return (
        '<script data-atlas="analytics">'
        "(function(d,w){var ph=w.posthog||[];if(ph.__SV)return;w.posthog=ph;ph._i=[];"
        "ph.init=function(k,c,n){function stub(t,m){t[m]=function(){"
        "t.push([m].concat(Array.prototype.slice.call(arguments,0)))}}"
        "var s=d.createElement('script');s.type='text/javascript';s.crossOrigin='anonymous';"
        "s.async=true;s.src=c.api_host.replace('.i.posthog.com','-assets.i.posthog.com')"
        "+'/static/array.js';var f=d.getElementsByTagName('script')[0];"
        "f.parentNode.insertBefore(s,f);var t=ph;if(n!==undefined){t=ph[n]=[]}"
        "else{n='posthog'}t.people=t.people||[];"
        "'capture register opt_in_capturing opt_out_capturing reset'.split(' ')"
        ".forEach(function(m){stub(t,m)});ph._i.push([k,c,n])};ph.__SV=1})(document,window);"
        f"posthog.init({_js(analytics.key)},{_js(config)});"
        "</script>")


def _js(value: object) -> str:
    """JSON that is also safe inside a <script> element."""
    return (json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("&", "\\u0026"))


def json_ld(data: dict) -> str:
    return f'<script type="application/ld+json">{_js(data)}</script>'


# --------------------------------------------------------------------- facts

def hours_text(line: str) -> str:
    """'Mo-Fr 08:00-17:00' -> 'Monday–Friday 08:00–17:00'."""
    days, _, times = line.partition(" ")
    spans = []
    for span in days.split(","):
        a, _, b = span.partition("-")
        spans.append(_DAYS.get(a, a) + (f"–{_DAYS.get(b, b)}" if b else ""))
    return f"{', '.join(spans)} {times.replace('-', '–')}"


def response_time_text(hours: float) -> str:
    if hours < 1:
        minutes = max(1, round(hours * 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    value = int(hours) if float(hours).is_integer() else round(hours, 1)
    return f"{value} hour{'s' if value != 1 else ''}"


def promise_html(client: ClientData) -> str:
    """Item 8. The number is the client's stated one or there is none."""
    if client.response_time_hours:
        line = f"We reply within {response_time_text(client.response_time_hours)}."
    else:
        line = "We'll get back to you as soon as we can."
    return f'<p class="atlas-promise" data-atlas="response-time">{escape(line)}</p>'


def directions_url(address: str) -> str:
    return ("https://www.google.com/maps/dir/?api=1&destination="
            + quote_plus(address))


def map_url(address: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(address)


def primary_cta(client: ClientData, prefix: str = "") -> tuple[str, str] | None:
    """(href, label) for the one action a visitor should take: book online
    when the client has a booking link, else call, else the contact page."""
    if client.booking_url:
        return client.booking_url, "Book online"
    if client.phone:
        return f"tel:{_tel(client.phone)}", f"Call {client.phone}"
    return f"{prefix}{CONTACT}", "Get in touch"


def _tel(phone: str) -> str:
    return re.sub(r"[^0-9+]", "", phone) or phone


def faq_list(client: ClientData, extra: list[Faq] | None = None,
             *, form: bool = False) -> list[Faq]:
    """The client's own FAQs first, then any extra (a store page's filtered
    copy), then questions whose answers restate a fact already on record,
    up to `FAQ_MAX`. A fact the record lacks produces no question."""
    items: list[Faq] = list(client.faqs) + list(extra or [])
    derived: list[Faq] = []
    if client.tagline:
        derived.append(Faq(f"What does {client.name} do?", client.tagline))
    ways = []
    if client.phone:
        ways.append(f"call {client.phone}")
    if client.email:
        ways.append(f"email {client.email}")
    if client.booking_url:
        ways.append("book online from this site")
    if ways:
        line = " or ".join(ways)
        derived.append(Faq(f"How do I get in touch with {client.name}?",
                           line[:1].upper() + line[1:] + "."))
    if client.address:
        derived.append(Faq(f"Where is {client.name}?",
                           f"{client.address}. The contact page has a Get directions "
                           "link."))
    if client.hours:
        derived.append(Faq("When are you open?",
                           "; ".join(hours_text(h) for h in client.hours) + "."))
    if client.response_time_hours:
        derived.append(Faq("How quickly do you reply?",
                           f"We reply within "
                           f"{response_time_text(client.response_time_hours)}."))
    if form:
        derived.append(Faq("Can I send an enquiry online?",
                           "Yes. Use the form on our contact page and we'll reply to "
                           "the details you give."))
    derived.append(Faq("What does this website do with my information?",
                       "Our privacy page explains exactly what this site collects and "
                       "who to contact about it."))
    seen = {f.q.lower() for f in items}
    for f in derived:
        if f.q.lower() not in seen:
            items.append(f)
            seen.add(f.q.lower())
    return items[:FAQ_MAX]


def colours_from_css(markup: str) -> dict[str, str] | None:
    """A brand colour the mockup's own CSS names (`--primary`, `--brand`,
    `--accent`), used for the share image when the client gave none."""
    match = _CSS_BRAND.search(markup or "")
    return {"primary": match.group(1).lower()} if match else None


# ---------------------------------------------------------------------- CSS

def atlas_css(client: ClientData) -> str:
    c = client.colours
    primary = _rgb(c["primary"])
    on_primary = "#ffffff" if _contrast(primary, (255, 255, 255)) >= \
        _contrast(primary, (0, 0, 0)) else "#000000"
    r, g, b = _readable(primary, _rgb(c["background"]))
    link = f"#{r:02x}{g:02x}{b:02x}"
    return (
        '<style data-atlas="launch">'
        f":root{{--atlas-primary:{c['primary']};--atlas-on-primary:{on_primary};"
        f"--atlas-bg:{c['background']};--atlas-text:{c['text']};--atlas-link:{link}}}"
        ".atlas-bar{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1rem;"
        "justify-content:space-between;padding:.6rem 1rem;background:var(--atlas-bg);"
        "color:var(--atlas-text);font-family:system-ui,sans-serif}"
        ".atlas-bar nav a,.atlas-footer a,.atlas-breadcrumbs a{color:var(--atlas-link)}"
        ".atlas-bar nav{display:flex;gap:1rem;flex-wrap:wrap}"
        ".atlas-cta{display:inline-flex;align-items:center;justify-content:center;"
        "min-height:44px;padding:.55rem 1.1rem;border-radius:.4rem;"
        "background:var(--atlas-primary);color:var(--atlas-on-primary);"
        "text-decoration:none;border:0;cursor:pointer;"
        "font:600 1rem/1.2 system-ui,sans-serif}"
        ".atlas-cta:focus-visible,.atlas-bar a:focus-visible{outline:3px solid "
        "var(--atlas-text);outline-offset:2px}"
        ".atlas-promise{margin:.25rem 0;font-size:.95rem}"
        ".atlas-section,.atlas-main{max-width:60rem;margin:2.5rem auto;padding:1rem;"
        "font-family:system-ui,sans-serif;line-height:1.6;color:var(--atlas-text);"
        "background:var(--atlas-bg)}"
        ".atlas-section blockquote{margin:1rem 0;padding:.75rem 1rem;"
        "border-left:4px solid var(--atlas-primary)}"
        ".atlas-section img{max-width:100%;height:auto}"
        ".atlas-faq dt{font-weight:600;margin-top:1rem}.atlas-faq dd{margin:0}"
        ".atlas-form{display:grid;gap:.5rem;max-width:32rem}"
        ".atlas-form input,.atlas-form textarea{font:inherit;padding:.5rem}"
        ".atlas-hp{position:absolute;left:-10000px;width:1px;height:1px;overflow:hidden}"
        ".atlas-breadcrumbs ol{list-style:none;display:flex;gap:.5rem;padding:0;margin:0}"
        ".atlas-breadcrumbs li+li::before{content:'/';margin-right:.5rem}"
        ".atlas-footer{max-width:60rem;margin:3rem auto 1rem;padding:1rem;"
        "font-family:system-ui,sans-serif;color:var(--atlas-text);"
        "background:var(--atlas-bg)}"
        ".atlas-footer nav{display:flex;flex-wrap:wrap;gap:1rem}"
        ".atlas-sticky{display:none}"
        "@media (max-width: 640px){"
        ".atlas-sticky{display:flex;position:fixed;left:0;right:0;bottom:0;z-index:50;"
        "gap:.5rem;padding:.5rem .75rem calc(.5rem + env(safe-area-inset-bottom));"
        "background:var(--atlas-bg);box-shadow:0 -2px 8px rgba(0,0,0,.2)}"
        ".atlas-sticky a{flex:1;min-height:48px}"
        "body{padding-bottom:calc(4.5rem + env(safe-area-inset-bottom))}"
        "}"
        "</style>")


# ------------------------------------------------------------------- chrome

def header_bar(client: ClientData, *, prefix: str = "", home: str = INDEX) -> str:
    """Item 2 (the CTA above the fold, with item 8's promise beside it) and
    item 3 (links to every page). `prefix` is "/" on the 404 page, which can
    be served from any depth."""
    home_href = "/" if prefix == "/" else home
    cta = primary_cta(client, prefix)
    cta_html = (f'<a class="atlas-cta" data-atlas="cta" href="{escape(cta[0])}">'
                f"{escape(cta[1])}</a>") if cta else ""
    return (
        '<header class="atlas-bar" data-atlas="header">'
        f'<a href="{escape(home_href)}"><strong>{escape(client.name)}</strong></a>'
        '<nav aria-label="Main">'
        f'<a href="{escape(home_href)}">Home</a>'
        f'<a href="{prefix}{CONTACT}">Contact</a>'
        f'<a href="{prefix}{INDEX}#faq">FAQ</a>'
        "</nav>"
        f'<div class="atlas-action">{cta_html}{promise_html(client)}</div>'
        "</header>")


def footer(client: ClientData, *, prefix: str = "") -> str:
    home_href = "/" if prefix == "/" else INDEX
    bits = [f'<a href="{escape(home_href)}">Home</a>',
            f'<a href="{prefix}{CONTACT}">Contact</a>',
            f'<a href="{prefix}{PRIVACY}">Privacy</a>',
            f'<a href="{prefix}{TERMS}">Terms</a>']
    if client.address:
        bits.append(f"<address>{escape(client.address)}</address>")
    return ('<footer class="atlas-footer" data-atlas="footer"><nav aria-label="Footer">'
            + "".join(bits) + "</nav></footer>")


def sticky_cta(client: ClientData, *, prefix: str = "") -> str:
    """Item 9: fixed to the bottom on small screens only (see `atlas_css`),
    with body padding so it never sits over the last of the content."""
    links = []
    if client.phone:
        links.append(f'<a class="atlas-cta" href="tel:{escape(_tel(client.phone))}" '
                     f'aria-label="Call {escape(client.name)} on {escape(client.phone)}">'
                     "Call</a>")
    if client.booking_url:
        links.append(f'<a class="atlas-cta" href="{escape(client.booking_url)}">Book</a>')
    if not links:
        links.append(f'<a class="atlas-cta" href="{prefix}{CONTACT}">Get in touch</a>')
    return ('<nav class="atlas-sticky" data-atlas="sticky-cta" aria-label="Quick contact">'
            + "".join(links) + "</nav>")


def chat_tag(client: ClientData, *, prefix: str = "") -> str:
    """The AI chat assistant's script, only on a $500 or $300 package site;
    nothing at all otherwise. The file itself (the widget with this site's
    endpoint and contact details baked in) is written by `launch.finish_site`."""
    if not client.chat:
        return ""
    return f'<script src="{prefix}{CHAT_WIDGET}" defer data-atlas="chat"></script>'


def breadcrumbs(client: ClientData, label: str, filename: str) -> str:
    """Item 5: visible trail plus BreadcrumbList JSON-LD."""
    base = f"{client.website}/" if client.website else ""
    ld = {"@context": "https://schema.org", "@type": "BreadcrumbList",
          "itemListElement": [
              {"@type": "ListItem", "position": 1, "name": "Home",
               "item": base or "/"},
              {"@type": "ListItem", "position": 2, "name": label,
               "item": f"{base}{filename}" if base else f"/{filename}"}]}
    return ('<nav class="atlas-breadcrumbs" aria-label="Breadcrumb"><ol>'
            f'<li><a href="{INDEX}">Home</a></li>'
            f'<li aria-current="page">{escape(label)}</li></ol></nav>'
            + json_ld(ld))


# ----------------------------------------------------------------- sections

def reviews_section(client: ClientData) -> str:
    """Item 15. Only the client's own reviews, word for word; the rating
    and the source link sit in each quote's <footer>, where the page rubric
    reads them as attribution rather than as a free-floating star rating."""
    if not client.reviews:
        return ""
    quotes = []
    for r in client.reviews:
        meta = [f"— {escape(r.author)}"]
        if r.rating is not None:
            rating = int(r.rating) if r.rating.is_integer() else r.rating
            meta.append(f"rated {rating}/5")
        if r.source_url:
            meta.append(f'<a href="{escape(r.source_url)}" rel="noopener">source</a>')
        quotes.append(f"<blockquote>{escape(r.text)}<footer>{', '.join(meta)}"
                      "</footer></blockquote>")
    return ('<section id="reviews" class="atlas-section" data-atlas="reviews" '
            'data-source="client"><h2>What customers say</h2>'
            + "".join(quotes) + "</section>")


def case_studies_section(client: ClientData) -> str:
    """Item 6: the client's own case studies only."""
    if not client.case_studies:
        return ""
    items = []
    for cs in client.case_studies:
        body = f"<h3>{escape(cs.title)}</h3><p>{escape(cs.summary)}</p>"
        if cs.outcome:
            body += f"<p><strong>Result:</strong> {escape(cs.outcome)}</p>"
        if cs.url:
            body += f'<p><a href="{escape(cs.url)}" rel="noopener">Read more</a></p>'
        items.append(f"<article>{body}</article>")
    return ('<section id="case-studies" class="atlas-section" data-atlas="case-studies">'
            "<h2>Our work</h2>" + "".join(items) + "</section>")


def team_section(client: ClientData, team_src: str | None) -> str:
    """Item 20: the client's own photo of their own people, never stock."""
    if not team_src:
        return ""
    alt = client.team_photo_alt or f"The {client.name} team"
    return ('<section id="team" class="atlas-section" data-atlas="team"><h2>Meet the '
            f'team</h2><img src="{escape(team_src)}" alt="{escape(alt)}" '
            'loading="lazy"></section>')


def faq_section(faqs: list[Faq]) -> str:
    if not faqs:
        return ""
    rows = "".join(f"<dt>{escape(f.q)}</dt><dd>{escape(f.a)}</dd>" for f in faqs)
    return ('<section id="faq" class="atlas-section" data-atlas="faq">'
            f'<h2>Questions we get asked</h2><dl class="atlas-faq">{rows}</dl></section>')


def faq_ld(faqs: list[Faq]) -> str:
    if not faqs:
        return ""
    return json_ld({"@context": "https://schema.org", "@type": "FAQPage",
                    "mainEntity": [{"@type": "Question", "name": f.q,
                                    "acceptedAnswer": {"@type": "Answer", "text": f.a}}
                                   for f in faqs]})


def visit_block(client: ClientData) -> str:
    """Item 14: the address with a map link and a Get directions link.
    Links, not an embedded Google map: an iframe would set Google's cookies
    on every visit, which the privacy page would then have to own up to."""
    if not client.address:
        return ""
    links = (f'<p><a href="{escape(map_url(client.address))}" rel="noopener" '
             f'data-atlas="map">View on Google Maps</a> · '
             f'<a href="{escape(directions_url(client.address))}" rel="noopener" '
             f'data-atlas="directions">Get directions</a></p>')
    lines = [f"<address>{escape(client.address)}</address>", links]
    if client.hours:
        lines.append("<ul>" + "".join(f"<li>{escape(hours_text(h))}</li>"
                                      for h in client.hours) + "</ul>")
    return "".join(lines)


def visit_section(client: ClientData) -> str:
    block = visit_block(client)
    if not block:
        return ""
    return ('<section id="visit" class="atlas-section" data-atlas="visit">'
            f"<h2>Visit us</h2>{block}</section>")


def local_business_ld(client: ClientData, image_url: str | None, *,
                      reservations: tuple[str, bool | str] | None = None) -> str:
    """Item 17. Only fields on record: an unknown field is left out, never
    filled; the address stays the single line it was given rather than
    being split into PostalAddress parts ATLAS would have to guess; no
    aggregateRating, ever — an average of reviews the client picked is not
    a rating anyone gave. `reservations` (sitebooking.reservations) is
    (schema.org food type, acceptsReservations), given only when a real
    booking path reaches the business."""
    ld: dict = {"@context": "https://schema.org", "@type": "LocalBusiness",
                "name": client.name}
    if reservations:
        ld["@type"] = ["LocalBusiness", reservations[0]]
        ld["acceptsReservations"] = reservations[1]
    if client.website:
        ld["url"] = f"{client.website}/"
    if client.tagline:
        ld["description"] = client.tagline
    if client.phone:
        ld["telephone"] = client.phone
    if client.email:
        ld["email"] = client.email
    if client.address:
        ld["address"] = client.address
    if client.geo:
        ld["geo"] = {"@type": "GeoCoordinates", "latitude": client.geo[0],
                     "longitude": client.geo[1]}
    if client.hours:
        ld["openingHours"] = list(client.hours)
    if image_url:
        ld["image"] = image_url
    return json_ld(ld)


# -------------------------------------------------------------------- pages

def _meta(name: str, content: str, *, prop: bool = False) -> str:
    attr = "property" if prop else "name"
    return f'<meta {attr}="{name}" content="{escape(content)}">'


def index_description(client: ClientData) -> str:
    first = client.name
    if client.tagline:
        first += f" — {client.tagline}"
    elif client.category:
        first += f", {client.category}"
    parts = [first.rstrip(".") + "."]
    if client.address:
        parts.append(f"Find us at {client.address}.")
    if client.phone:
        parts.append(f"Call {client.phone}.")
    return " ".join(parts)[:300]


def share_image_url(client: ClientData, image_name: str) -> str:
    return f"{client.website}/{image_name}" if client.website else image_name


def page(client: ClientData, *, title: str, description: str, body: str,
         analytics: Analytics | None, noindex: bool = False, prefix: str = "",
         label: str | None = None, filename: str | None = None,
         image_name: str | None = None) -> str:
    """One ATLAS-written sub-page, with the shared chrome -- or, for a site
    built from a designed template (`client.theme`), that template's."""
    head = [f"<title>{escape(title)}</title>", _meta("description", description)]
    if noindex:
        head.append(_meta("robots", "noindex"))
    if image_name:
        head += [_meta("og:title", title, prop=True),
                 _meta("og:description", description, prop=True),
                 _meta("og:image", share_image_url(client, image_name), prop=True),
                 _meta("twitter:card", "summary_large_image")]
    if analytics:
        head.append(analytics_snippet(analytics))
    crumbs = breadcrumbs(client, label, filename) if label and filename else ""
    if client.theme:
        from .sitetemplate import render_subpage
        return render_subpage(client, title=title, description=description, body=body,
                              head_extra="".join(head[2:]), prefix=prefix,
                              crumbs=crumbs, filename=filename)
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            + "".join(head) + atlas_css(client) + "</head><body>"
            + header_bar(client, prefix=prefix)
            + f'<main class="atlas-main">{crumbs}{body}</main>'
            + footer(client, prefix=prefix) + sticky_cta(client, prefix=prefix)
            + chat_tag(client, prefix=prefix) + "</body></html>\n")


def contact_ways(client: ClientData) -> str:
    ways = []
    if client.phone:
        ways.append(f'<li>Phone: <a href="tel:{escape(_tel(client.phone))}">'
                    f"{escape(client.phone)}</a></li>")
    if client.email:
        ways.append(f'<li>Email: <a href="mailto:{escape(client.email)}">'
                    f"{escape(client.email)}</a></li>")
    if client.booking_url:
        ways.append(f'<li><a href="{escape(client.booking_url)}">Book online</a></li>')
    return f"<ul>{''.join(ways)}</ul>" if ways else ""


def form_html(client: ClientData, form_action: str) -> str:
    """Item 4: the enquiry form posts to the client's form service and sends
    the visitor to thank-you.html afterwards (`_next`, the redirect field
    Formspree and FormSubmit read). A honeypot field catches simple bots."""
    thanks = f"{client.website}/{THANKS}" if client.website else THANKS
    return (
        f'<form class="atlas-form" data-atlas="enquiry" action="{escape(form_action)}" '
        'method="post">'
        f'<input type="hidden" name="_next" value="{escape(thanks)}">'
        '<input type="hidden" name="_subject" value="New enquiry from your website">'
        '<label class="atlas-hp" for="f-hp">Leave this empty</label>'
        '<input class="atlas-hp" id="f-hp" type="text" name="_gotcha" tabindex="-1" '
        'autocomplete="off">'
        '<label for="f-name">Your name</label>'
        '<input id="f-name" name="name" autocomplete="name" required>'
        '<label for="f-contact">Your email or phone</label>'
        '<input id="f-contact" name="contact" required>'
        '<label for="f-message">How can we help?</label>'
        '<textarea id="f-message" name="message" rows="5" required></textarea>'
        '<button class="atlas-cta" type="submit">Send enquiry</button>'
        f"{promise_html(client)}</form>")


def contact_page(client: ClientData, analytics: Analytics | None,
                 form_action: str | None) -> str:
    body = (f"<h1>Contact {escape(client.name)}</h1>{promise_html(client)}"
            + contact_ways(client))
    if form_action:
        body += "<h2>Send an enquiry</h2>" + form_html(client, form_action)
    visit = visit_block(client)
    if visit:
        body += f'<h2>Find us</h2><div data-atlas="visit">{visit}</div>'
    known = [w for w, ok in (("phone", client.phone), ("email", client.email),
                             ("directions", client.address),
                             ("opening hours", client.hours)) if ok]
    desc = (f"How to reach {client.name}"
            + (f": {', '.join(known)}." if known else "."))
    return page(client, title=f"Contact | {client.name}", description=desc, body=body,
                analytics=analytics, label="Contact", filename=CONTACT)


def thanks_page(client: ClientData, analytics: Analytics | None) -> str:
    body = ("<h1>Thanks — your request is on its way</h1>" + promise_html(client)
            + f'<p><a href="{INDEX}">Back to the home page</a></p>')
    return page(client, title=f"Thank you | {client.name}",
                description=f"Your enquiry to {client.name} has been sent.",
                body=body, analytics=analytics, noindex=True, label="Thank you",
                filename=THANKS)


def _contact_line(client: ClientData) -> str:
    return " or ".join(escape(c) for c in (client.phone, client.email) if c) or \
        "the details on our home page"


def privacy_page(client: ClientData, analytics: Analytics | None,
                 form_action: str | None) -> str:
    """Item 18. Says only what this site actually does, which depends on two
    switches: analytics on or off, a form or none."""
    biz = escape(client.name)
    if analytics:
        tracking = ("<p>This site counts visits with PostHog, a privacy-respecting "
                    "analytics service, so we can see which pages are useful. It keeps "
                    "nothing on your device (no cookies, no local storage), does not "
                    "record your session, and we don't use it to identify you.</p>")
    else:
        tracking = "<p>This site does not use tracking cookies or analytics.</p>"
    from .sitebooking import PREVIEW, path_for, provider
    booking = path_for(client, form_action)
    if form_action:
        host = escape(urlparse(form_action).netloc)
        what = ("an enquiry or a booking request with a form on this site"
                if client.theme else "an enquiry with the form on our contact page")
        forms = (f"<p>If you send {what}, what "
                 f"you type goes to {host}, the service that delivers it to us, and "
                 "we use it only to reply to you.</p>")
    elif booking == PREVIEW:
        forms = ("<p>This site is a preview. Its booking form is not connected yet: "
                 "it sends nothing, and nothing you type in it is stored.</p>")
    else:
        forms = "<p>It has no forms that store what you type.</p>"
    if client.booking_url and client.theme:
        forms += (f"<p>Online booking happens on "
                  f"{escape(provider(client) or 'a booking service')}'s own site, "
                  "which handles what you give it under its own terms.</p>")
    if client.package is None:
        # A preview carries "Have Mapfront call me" (callme.py): Mapfront's
        # form, not the business's, and this page says so.
        forms = forms.replace("<p>It has no forms", "<p>Apart from the call-back "
                              "request below, it has no forms")
        forms += ("<p data-atlas=\"callme-privacy\">This is a preview Mapfront made "
                  f"for {biz}. If you use \"Have Mapfront call me\" on the home page, "
                  "your name and phone number go to Mapfront, not to "
                  f"{biz}, with the time, the page, your browser's name and a "
                  "one-way hash of your internet address, kept as the record that "
                  "you asked. Mapfront uses them only to call you about this "
                  "website. Say \"don't call\" or reply STOP anytime.</p>")
    chat = ""
    if client.chat:
        chat = ("<p data-atlas=\"chat-privacy\">The chat on this site is an AI "
                "assistant, not a person. What you type in it is sent to Mapfront "
                "(miro@mapfront.io), who runs the assistant, and to an AI provider "
                "to write the answer, which handles them under its own terms. "
                "Mapfront does not store the words: only a count of questions is "
                "kept. The chat uses no cookies and keeps nothing on your device. "
                "Please don't type "
                "card, bank, ID or health details into it; phone or email us "
                "instead.</p>")
    body = (f"<h1>Privacy</h1><p>{biz} runs this website to tell you about our "
            f"business.</p>{tracking}{forms}{chat}"
            "<p>If you phone or email us, we use what you send only to answer you, "
            "and we don't sell or share it.</p>"
            f"<p>Questions about your information: contact us at "
            f"{_contact_line(client)}.</p>")
    return page(client, title=f"Privacy | {client.name}",
                description=f"How the {client.name} website handles your information.",
                body=body, analytics=analytics, label="Privacy", filename=PRIVACY)


def terms_page(client: ClientData, analytics: Analytics | None) -> str:
    """Item 18's companion. Plain terms for a brochure site; no governing
    law, liability cap or anything else a lawyer would have to pick."""
    biz = escape(client.name)
    body = (
        "<h1>Terms of use</h1>"
        f"<p>This website tells you about {biz}. The information on it is general "
        "and can change, so please contact us to confirm details, prices and "
        "availability before relying on them.</p>"
        f"<p>Nothing on this site is an offer of a contract; any work or order is "
        f"agreed directly with {biz}.</p>"
        "<p>Links to other websites are there for convenience; we don't control "
        "those sites and aren't responsible for what they say.</p>"
        f"<p>The text and images on this site belong to {biz} or are used with "
        "permission. Please ask before reusing them.</p>"
        f"<p>Questions about these terms: contact us at {_contact_line(client)}.</p>")
    return page(client, title=f"Terms | {client.name}",
                description=f"The terms for using the {client.name} website.",
                body=body, analytics=analytics, label="Terms", filename=TERMS)


def not_found_page(client: ClientData, analytics: Analytics | None) -> str:
    """Item 1. Root-relative links: a 404 can be served at any depth."""
    body = ("<h1>That page isn't here</h1><p>The link may be old or mistyped.</p>"
            f'<p><a href="/">Go to the {escape(client.name)} home page</a>, or contact '
            f"us at {_contact_line(client)}.</p>")
    return page(client, title=f"Page not found | {client.name}",
                description=f"This page could not be found on the {client.name} "
                            "website.",
                body=body, analytics=analytics, noindex=True, prefix="/")


# The AI assistants' crawlers (Miro, 2026-10-06, from a reel on "AI search
# visibility": customers ask ChatGPT and friends for "best roofer near me").
# A sold site names them and allows them explicitly, so a stricter default
# somewhere upstream never shuts them out; a preview stays noindex and gets
# no such invitation. Names as each vendor publishes them (read 2026-10-06).
AI_CRAWLERS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot",
               "anthropic-ai", "PerplexityBot", "Google-Extended", "Applebot",
               "Bingbot")
LLMS_TXT = "llms.txt"


def robots_txt(client: ClientData) -> str:
    robots = "User-agent: *\nAllow: /\n"
    if client.package is not None:
        robots += "".join(f"\nUser-agent: {bot}\nAllow: /\n" for bot in AI_CRAWLERS)
    if client.website:
        robots += f"\nSitemap: {client.website}/sitemap.xml\n"
    return robots


def llms_txt(client: ClientData) -> str | None:
    """The llmstxt.org convention: a plain-text summary an AI assistant can
    read in one fetch. Only facts the client gave (client.json) or the
    listing carries; nothing is invented, and a preview publishes none
    (its details are not the client's word yet)."""
    if client.package is None:
        return None
    lines = [f"# {client.name}", ""]
    if client.tagline:
        lines += [f"> {client.tagline}", ""]
    facts = []
    if client.category:
        facts.append(f"- Type of business: {client.category}")
    if client.area:
        facts.append(f"- Serves: {client.area}")
    if client.address:
        facts.append(f"- Address: {client.address}")
    if client.phone:
        facts.append(f"- Phone: {client.phone}")
    if client.email:
        facts.append(f"- Email: {client.email}")
    if client.hours:
        facts.append("- Hours: " + "; ".join(client.hours))
    if client.response_time_hours:
        facts.append(f"- Replies to enquiries within about "
                     f"{client.response_time_hours:g} hours")
    if client.booking_url:
        facts.append(f"- Book online: {client.booking_url}")
    if facts:
        lines += facts + [""]
    if client.services:
        lines += ["## Services", ""] + [f"- {x}" for x in client.services] + [""]
    if client.faqs:
        lines += ["## Questions people ask", ""]
        for faq in client.faqs:
            lines += [f"- {faq.q} {faq.a}"]
        lines.append("")
    if client.website:
        pages = [(INDEX, "Home"), (CONTACT, "Contact"), (PRIVACY, "Privacy"),
                 (TERMS, "Terms")]
        lines += ["## Pages", ""] + [
            f"- [{label}]({client.website}/{'' if name == INDEX else name})"
            for name, label in pages] + [""]
    lines.append("Everything above was supplied by the business. Prices, availability "
                 "and bookings are confirmed by the business directly.")
    return "\n".join(lines) + "\n"


def sitemap_xml(client: ClientData, pages: list[str]) -> str | None:
    if not client.website:
        return None
    today = datetime.now(UTC).date().isoformat()
    rows = "".join(
        f"  <url><loc>{client.website}/{'' if p == INDEX else p}</loc>"
        f"<lastmod>{today}</lastmod></url>\n" for p in pages)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{rows}</urlset>\n")


# ------------------------------------------------------------ the home page

class _Scan(HTMLParser):
    """Where things are in the mockup (offsets into the original string)
    and what it already has, so `enhance_index` adds only what is missing."""

    def __init__(self, markup: str) -> None:
        super().__init__(convert_charrefs=True)
        self._starts = [0]
        for line in markup.splitlines(keepends=True):
            self._starts.append(self._starts[-1] + len(line))
        self.head_open_end: int | None = None
        self.head_close: int | None = None
        self.html_open_end: int | None = None
        self.body_open_end: int | None = None
        self.body_close: int | None = None
        self.html_close: int | None = None
        self.body_footer: int | None = None
        self.metas: set[str] = set()
        self.ids: set[str] = set()
        self.markers: set[str] = set()     # data-atlas values already present
        self.title_text = ""
        self._in_title = False
        self._stack: list[str] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self._starts[line - 1] + col

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        start = self._offset()
        end = start + len(self.get_starttag_text() or "")
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and self.html_open_end is None:
            self.html_open_end = end
        elif tag == "head" and self.head_open_end is None:
            self.head_open_end = end
        elif tag == "body" and self.body_open_end is None:
            self.body_open_end = end
        elif tag == "footer" and self._stack[-1:] == ["body"] and self.body_footer is None:
            self.body_footer = start
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.metas.add(key)
            if "charset" in a:
                self.metas.add("charset")
        elif tag == "title":
            self._in_title = True
        if a.get("id"):
            self.ids.add(a["id"].lower())
        if a.get("data-atlas"):
            self.markers.add(a["data-atlas"].lower())
        if tag not in _VOID:
            self._stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._stack and self._stack[-1] == tag:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        start = self._offset()
        if tag == "head" and self.head_close is None:
            self.head_close = start
        elif tag == "body":
            self.body_close = start
        elif tag == "html":
            self.html_close = start
        elif tag == "title":
            self._in_title = False
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text += data


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}


def enhance_index(markup: str, client: ClientData, *, analytics: Analytics | None,
                  faqs: list[Faq], team_src: str | None, image_name: str,
                  reservations: tuple[str, bool | str] | None = None) -> str:
    """The home page with every launch piece it lacks added at its edges.
    Existing title, description, viewport and share tags are kept; ATLAS's
    own sections are added only when the page has no section by that id
    (a store page already renders its FAQ and reviews)."""
    scan = _Scan(markup)
    scan.feed(markup)
    scan.close()

    head: list[str] = []
    if "viewport" not in scan.metas:
        head.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    title = " ".join(scan.title_text.split())
    if not title:
        title = client.name + (f" | {client.category}" if client.category else "")
        head.append(f"<title>{escape(title)}</title>")
    description = index_description(client)
    if "description" not in scan.metas:
        head.append(_meta("description", description))
    image = share_image_url(client, image_name)
    for key, value in (("og:title", title), ("og:description", description),
                       ("og:type", "website"), ("og:image", image)):
        if key not in scan.metas:
            head.append(_meta(key, value, prop=True))
    if client.website and "og:url" not in scan.metas:
        head.append(_meta("og:url", f"{client.website}/", prop=True))
    if "twitter:card" not in scan.metas:
        head.append(_meta("twitter:card", "summary_large_image"))
    head.append(local_business_ld(client, image if client.website else None,
                                  reservations=reservations))
    head.append(faq_ld(faqs))
    if "theme" not in scan.markers:
        # A designed template (`sitetemplate`) brings its own design system;
        # the launch CSS would only override its colours.
        head.append(atlas_css(client))
    if analytics:
        head.append(analytics_snippet(analytics))
    head_html = "".join(head)

    sections = [
        reviews_section(client) if "reviews" not in scan.ids else "",
        case_studies_section(client) if "case-studies" not in scan.ids else "",
        team_section(client, team_src) if "team" not in scan.ids else "",
        faq_section(faqs) if "faq" not in scan.ids else "",
        visit_section(client) if "visit" not in scan.ids else "",
    ]
    before_footer = "".join(sections)
    # A designed template already carries its header, footer and sticky bar
    # (marked data-atlas=...), and the chat script when there is one.
    body_end = ("" if "footer" in scan.markers else footer(client)) + (
        "" if "sticky-cta" in scan.markers else sticky_cta(client)) + (
        "" if "chat" in scan.markers else chat_tag(client))
    body_start = "" if "header" in scan.markers else header_bar(client)

    inserts: list[tuple[int, str]] = []
    if scan.head_close is not None:
        inserts.append((scan.head_close, head_html))
    elif scan.head_open_end is not None:
        inserts.append((scan.head_open_end, head_html))
    elif scan.html_open_end is not None:
        inserts.append((scan.html_open_end, f"<head>{head_html}</head>"))
    else:
        inserts.append((0, f"<head>{head_html}</head>"))
    head_at = inserts[0][0]

    if scan.body_open_end is not None:
        inserts.append((scan.body_open_end, body_start))
    else:
        inserts.append((head_at, body_start))
    end_at = (scan.body_close if scan.body_close is not None
              else scan.html_close if scan.html_close is not None else len(markup))
    if scan.body_footer is not None:
        inserts.append((scan.body_footer, before_footer))
        inserts.append((end_at, body_end))
    else:
        inserts.append((end_at, before_footer + body_end))

    out = markup
    # Apply from the end so earlier offsets stay valid; at a shared offset the
    # insert listed first must end up first, so ties are applied in reverse.
    for _order, (at, snippet) in sorted(enumerate(inserts),
                                        key=lambda p: (p[1][0], p[0]), reverse=True):
        out = out[:at] + snippet + out[at:]
    if not out.lstrip().lower().startswith("<!doctype"):
        out = "<!doctype html>\n" + out
    return out
