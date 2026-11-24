"""The agency landing page (agency.storefront.test/) claims only what agency.build_site produces.

atlas/modules/agency/landing/index.html is served as-is by Caddy (with
`templates` rendering the postal address). No ratings, client counts,
testimonials, client logos or figures: the only digits in the visible text
are the one public price, the Website Package's $500 (the $300 and $100
prices are not listed publicly). The postal address comes from
AGENCY_POSTAL_ADDRESS at render time and is never written into the file.
The AI chat assistant ships from its own branch; the page may name it only
as part of the Website Package.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

LANDING = (Path(__file__).resolve().parents[1]
           / "atlas" / "modules" / "agency" / "landing" / "index.html")
HTML = LANDING.read_text()
TEMPLATE_ADDRESS = ('{{with env "AGENCY_POSTAL_ADDRESS"}}<address>{{. | html}}</address>'
                    '{{else}}<!--')


class _Text(HTMLParser):
    """Visible text plus the text-bearing attributes a visitor or a search
    result shows (title, meta description, alt, aria-label)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        a = dict(attrs)
        if tag == "meta" and a.get("name") == "description":
            self.parts.append(a.get("content") or "")
        for key in ("alt", "aria-label", "title"):
            if a.get(key):
                self.parts.append(a[key] or "")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _visible() -> str:
    parser = _Text()
    parser.feed(re.sub(r"\{\{.*?\}\}", " ", HTML))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def test_the_only_figure_is_the_website_package_price():
    text = _visible()
    assert re.findall(r"\$\d+", text) == ["$500"]
    # the contact phone number is the one other figure a visitor needs
    rest = (text.replace("$500", "").replace("owner@example.com", "")
            .replace("+1 555 010 0142", "")
            .replace("30 Business Lessons", ""))        # an ebook title, not a figure
    assert not re.search(r"\d", rest), re.findall(r".{20}\d.{20}", rest)


@pytest.mark.parametrize("price", ["$300", "$100"])
def test_the_other_prices_are_not_public(price):
    assert price not in HTML
    assert price[1:] not in _visible()


def test_the_brand():
    lowered = HTML.lower()
    assert "#5b8cff" in lowered and "#0a0d14" in lowered
    assert '<span class="wordmark">ATLAS Agency</span>' in HTML
    assert "mapfront" not in _visible().lower()         # the old name is never shown
    assert (LANDING.parent / "mapfront-icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert 'rel="icon" type="image/png" href="/mapfront-icon.png"' in HTML


@pytest.mark.parametrize("word", [
    "rated", "rating", "stars", "five-star", "clients served", "customers served",
    "happy clients", "trusted by", "award", "testimonial", "as seen", "featured in",
    "guarantee", "best in", "leading", "#", "%", "percent", "years of experience",
    "chatbot", "24/7",
])
def test_no_unverified_claim_words(word):
    assert word not in _visible().lower()


def test_the_chat_assistant_is_named_only_in_the_website_package():
    package = HTML[HTML.index('<section class="band alt" id="package">'):]
    package = package[:package.index("</section>")]
    outside = HTML.replace(package, "")
    assert "AI chat assistant" in package
    body = outside[outside.index("<body>"):]
    assert "chat" not in body.lower()


def test_no_client_logos_images_scripts_or_external_requests():
    lowered = HTML.lower()
    # Two script tags: a schema.org record (data, never run) and the same-origin
    # /contact.js that sends the form with fetch (CSP script-src 'self').
    assert "<img" not in lowered and lowered.count("<script") == 2
    assert '<script src="/contact.js" defer></script>' in HTML
    record = HTML.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    assert json.loads(record)["@context"] == "https://schema.org"
    assert "<iframe" not in lowered
    # The canonical link names the page's own address, not another host.
    assert '<link rel="canonical" href="https://agency.storefront.test/agency/">' in lowered
    own = lowered.replace('<link rel="canonical" href="https://agency.storefront.test/agency/">', "")
    assert not re.search(r"""(src|href)=["']https?://""", own)
    # One form: it posts to the site's own contact route (2026-11-14); the
    # mail address stays on the page as plain text.
    assert lowered.count("<form") == 1
    assert re.findall(r"""action=["']([^"']+)""", lowered) == ["/public/agency/contact"]
    assert 'rel="stylesheet"' not in lowered and "@import" not in lowered


def test_contact_is_miro_at_mapfront():
    assert 'href="mailto:owner@example.com"' in HTML
    assert "owner@example.com" in _visible()
    assert 'href="tel:+1 555 010 0142"' in HTML
    assert "+1 555 010 0142" in _visible()


def test_the_postal_address_is_only_ever_the_escaped_env_value():
    """Never invented: the file carries the template, not an address; the value
    is HTML-escaped where it lands; unset leaves a TODO comment, not text."""
    assert HTML.count(TEMPLATE_ADDRESS) == 1
    assert "TODO(miro): set AGENCY_POSTAL_ADDRESS" in HTML
    assert "<address>" not in HTML.replace(TEMPLATE_ADDRESS, "")


def test_the_only_template_actions_are_the_address():
    """`templates` executes every {{ }} in the file with Caddy's environment
    in reach; anything but the address expression could print a secret."""
    actions = re.findall(r"\{\{.*?\}\}", HTML)
    assert actions == ['{{with env "AGENCY_POSTAL_ADDRESS"}}', "{{. | html}}",
                       "{{else}}", "{{end}}"]


def test_what_it_says_a_site_gets_is_what_sitekit_builds():
    """Each 'What you get' claim maps to something the build writes."""
    from atlas.modules.agency import sitekit
    assert (sitekit.CONTACT, sitekit.PRIVACY, sitekit.TERMS, sitekit.NOT_FOUND) == (
        "contact.html", "privacy.html", "terms.html", "404.html")
    assert sitekit.SHARE_IMAGE == "og-image.png"
    source = Path(sitekit.__file__).read_text()
    for built in ("LocalBusiness", "robots.txt", "og:", "sticky"):
        assert built in source, built
    text = _visible().lower()
    assert "a home page, a contact page, privacy and terms pages" in text
    assert "no online checkout" in text


def test_no_cta_button_is_a_bare_mailto():
    """A mailto button does nothing visible on a phone without a mail app (Miro,
    2026-09-30). Buttons scroll to #contact; the address itself stays a text link."""
    for m in re.finditer(r'<a class="btn[^"]*" href="([^"]+)"', HTML):
        assert not m.group(1).startswith("mailto:"), m.group(0)
    assert 'href="mailto:owner@example.com' in HTML


def test_search_engines_and_ai_assistants_can_read_the_landing():
    """Miro, 2026-10-06: robots names the AI crawlers and the sitemap, and
    llms.txt states only what the page states ($500, a free preview)."""
    robots = (LANDING.parent / "robots.txt").read_text()
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot"):
        assert f"User-agent: {bot}\nAllow: /" in robots
    # ONE robots.txt for the host: it names the landing's sitemap and the store's.
    assert "Sitemap: https://agency.storefront.test/sitemap.xml" in robots
    assert "Sitemap: https://agency.storefront.test/sitemap-academy.xml" in robots
    llms = (LANDING.parent / "llms.txt").read_text()
    assert "$500" in llms and "free preview" in llms and "owner@example.com" in llms
    assert "{{" not in llms and "{{" not in robots   # Caddy templates these


# ---- the premium redesign (2026-11-14): a calm rate card, not a product launch

STYLE = HTML.split("<style>", 1)[1].split("</style>", 1)[0]


def test_contact_is_only_the_gmail_and_the_phone():
    assert set(re.findall(r'href="(tel:[^"]*)"', HTML)) == {"tel:+1 555 010 0142"}
    emails = set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", HTML))
    assert emails == {"owner@example.com"}, emails
    record = HTML.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    assert json.loads(record)["telephone"] == "+1 555 010 0142"
    assert "+1 555 010 0142" in (LANDING.parent / "llms.txt").read_text()


@pytest.mark.parametrize("token", [
    "radial-gradient", "text-shadow", "drop-shadow", "animation-timeline",
    "perspective", "rotateX", "rotateY", "infinite", "backdrop-filter",
    "2fd0ff", "06111a", "--cyan",
])
def test_no_generated_look_css(token):
    assert token.lower() not in STYLE.lower()


ALLOWED_HEX = {
    # ATLAS house palette: ground, panel, royal blue, light blue, text
    "0a0d14", "131a28", "0f1420", "5b8cff", "8fb0ff", "070a12",
    "eef2fa", "c9d2e3", "9aa5ba", "ffffff",
}


def test_dark_only_and_blue():
    assert 'content="dark"' in HTML and '<meta name="theme-color" content="#0a0d14">' in HTML
    assert "prefers-color-scheme" not in STYLE
    assert "f4b942" not in HTML.lower() and "0f3d3e" not in HTML.lower()


def test_one_accent():
    """Every hex colour is brand, neutral, dark-scheme, or one of at most three
    sample-site colours declared in a comment block named `sample-colours:`."""
    block = re.search(r"sample-colours:(.*?)\*/", STYLE, re.DOTALL)
    sample = set(re.findall(r"#([0-9a-fA-F]{3,6})\b", block.group(1)) if block else [])
    sample = {h.lower() for h in sample}
    assert len(sample) <= 3, sample
    used = {h.lower() for h in re.findall(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", HTML)}
    assert used <= ALLOWED_HEX | sample, used - ALLOWED_HEX - sample


def test_motion_is_small_and_optional():
    assert len(re.findall(r"@keyframes\s+([\w-]+)", STYLE)) <= 1
    names = re.findall(r"@keyframes\s+([\w-]+)", STYLE)
    reduced = STYLE[STYLE.index("prefers-reduced-motion: reduce"):]
    for name in names:
        assert re.search(r"animation:\s*none", reduced), name


@pytest.mark.parametrize("phrase", [
    "done properly", "works as hard", "sales pitch", "stated up front", "seamless",
    "elevate", "unlock", "effortless", "stunning", "cutting-edge",
])
def test_plain_copy(phrase):
    assert phrase not in _visible().lower()


BANNED_CLASSES = {"blk", "map", "lane", "shop", "bleed", "street", "loaves"}


def test_structure():
    assert HTML.count("<h1") == 1
    assert len(re.findall(r"<h2[ >]", HTML)) in (3, 4)
    for ident in ("package", "how", "contact"):
        assert f'id="{ident}"' in HTML
    tokens = set()
    for value in re.findall(r'class="([^"]*)"', HTML):
        tokens.update(value.split())
    assert not tokens & BANNED_CLASSES, tokens & BANNED_CLASSES


def test_each_fact_once():
    text = _visible().lower()
    assert text.count("free preview") <= 6
    assert text.count("say yes") <= 2


def test_page_weight():
    assert len(HTML.encode()) < 40 * 1024


def test_every_address_is_agency_storefront_com():
    """Decision 2026-11-14: the old domain is gone from the landing, canonical,
    ld+json, sitemap, robots, llms.txt and the thanks page."""
    here = LANDING.parent
    old = "mapfront" + ".io"        # spelled in two parts so this file stays clean
    for path in ("index.html", "robots.txt", "sitemap.xml", "llms.txt",
                 "thanks/index.html", "DESIGN.md"):
        assert old not in (here / path).read_text(), path
    record = HTML.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    assert json.loads(record)["url"] == "https://agency.storefront.test/"
    assert "<loc>https://agency.storefront.test/</loc>" in (here / "sitemap.xml").read_text()
    assert "https://agency.storefront.test/" in (here / "llms.txt").read_text()
    assert "/academy/" in (here / "llms.txt").read_text()
    thanks = (here / "thanks" / "index.html").read_text()
    assert ">Back to agency.storefront.test</a>" in thanks
    assert "owner@example.com" in thanks and "+1 555 010 0142" in thanks


HEADER_LINKS = [("/agency/", "Agency"), ("/academy/#catalog", "Courses"),
                ("/academy/#catalog", "Ebooks"), ("/academy/blog/", "Blog"),
                ("/academy/videos/", "Free lessons"), ("/academy/help/", "Help")]


def _header_links(html: str) -> list[tuple[str, str]]:
    nav = html.split('<nav aria-label="Main">', 1)[1].split("</nav>", 1)[0] \
        if '<nav aria-label="Main">' in html else html.split('<nav class="site"', 1)[1].split("</nav>", 1)[0]
    return re.findall(r'<a href="([^"]+)">([^<]+)</a>', nav)


@pytest.mark.parametrize("path", ["index.html", "thanks/index.html"])
def test_the_shared_header_links_the_whole_site(path):
    html = (LANDING.parent / path).read_text()
    assert _header_links(html)[:6] == HEADER_LINKS


def test_the_store_header_is_the_same_set_of_links():
    from datetime import UTC, datetime

    from atlas.modules.academy import catalog as cat_mod
    from atlas.modules.academy import site
    files = site.render(cat_mod.parse(json.loads(cat_mod.CATALOG_PATH.read_text())),
                        datetime(2026, 9, 30, tzinfo=UTC))
    for page in ("academy/index.html", "academy/blog/index.html", "academy/help/index.html"):
        html = files[page].decode()
        nav = html.split('<ul class="nav"', 1)[1].split("</ul>", 1)[0]
        assert re.findall(r'href="([^"]+)"[^>]*>([^<]+)</a>', nav) == HEADER_LINKS, page
        assert 'class="brand" href="/" aria-label="ATLAS Agency home"' in html


def test_a_courses_link_stays_visible_at_phone_width():
    # nav links are hidden under 640px; the phone bar keeps one
    bar = HTML.split('<nav class="bar"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/academy/">Courses</a>' in bar


def test_the_landing_product_links_point_into_academy():
    for slug in ("safe-ai-operator", "sell-with-ai", "ai-for-every-age",
                 "30-business-lessons", "family-ai-scam-defence-workbook",
                 "local-business-automation-pack", "claude-obsidian-vault-kit"):
        assert f'href="/academy/{slug}/"' in HTML
        assert f'href="/{slug}/"' not in HTML
