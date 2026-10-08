"""The Mapfront landing page claims only what agency.build_site produces.

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
    rest = text.replace("$500", "")
    assert not re.search(r"\d", rest), re.findall(r".{20}\d.{20}", rest)


@pytest.mark.parametrize("price", ["$300", "$100"])
def test_the_other_prices_are_not_public(price):
    assert price not in HTML
    assert price[1:] not in _visible()


def test_the_brand():
    lowered = HTML.lower()
    assert "#0f3d3e" in lowered and "#f4b942" in lowered
    assert '<span class="wordmark">mapfront</span>' in HTML
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
    # One script tag only, and it is a schema.org record (data, never run;
    # the CSP still allows no script at all).
    assert "<img" not in lowered and lowered.count("<script") == 1
    record = HTML.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    assert json.loads(record)["@context"] == "https://schema.org"
    assert "<iframe" not in lowered
    # The canonical link names the page's own address, not another host.
    own = lowered.replace('<link rel="canonical" href="https://mapfront.io/">', "")
    assert not re.search(r"""(src|href)=["']https?://""", own)
    # One form, and it posts to exactly one place: the API's preview-request
    # path (atlas/api/preview_request.py, tests/test_preview_request.py).
    assert lowered.count("<form") == 1
    assert re.findall(r"""action=["']([^"']+)""", lowered) == [
        "https://atlas.example.com/public/preview-request"]
    assert 'rel="stylesheet"' not in lowered and "@import" not in lowered


def test_contact_is_miro_at_mapfront():
    assert 'href="mailto:miro@mapfront.io"' in HTML
    assert "miro@mapfront.io" in _visible()


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
    assert 'href="mailto:miro@mapfront.io' in HTML


def test_search_engines_and_ai_assistants_can_read_the_landing():
    """Miro, 2026-10-06: robots names the AI crawlers and the sitemap, and
    llms.txt states only what the page states ($500, a free preview)."""
    robots = (LANDING.parent / "robots.txt").read_text()
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot"):
        assert f"User-agent: {bot}\nAllow: /" in robots
    assert "Sitemap: https://mapfront.io/sitemap.xml" in robots
    llms = (LANDING.parent / "llms.txt").read_text()
    assert "$500" in llms and "free preview" in llms and "miro@mapfront.io" in llms
    assert "{{" not in llms and "{{" not in robots   # Caddy templates these
