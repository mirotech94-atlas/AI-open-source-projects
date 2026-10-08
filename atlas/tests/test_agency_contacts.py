"""agency.contacts: only an address the business published on its own site."""
from __future__ import annotations

import httpx
import pytest

from atlas.modules import _netguard
from atlas.modules.agency import contacts

HOME = """<!doctype html><html><head><title>Joe's Plumbing | Astoria</title>
<script>Sentry.init({dsn:"https://0123456789abcdef0123456789abcdef@o1.ingest.sentry.io/5"})
</script></head><body>
<header><img src="/img/logo@2x.png" alt="Joe's"><nav>
<a href="/">Home</a> <a href="/about-us">About us</a> <a href="/contact">Contact</a>
<a href="https://www.facebook.com/joesplumbing">Facebook</a>
<a href="https://other-site.com/contact">Partner</a></nav></header>
<main><h1>Emergency plumbing in Astoria</h1>
<p>Placeholder from the theme: you@example.com, name@domain.com</p>
<p>Built by Wix: <span>support@wix.com</span></p>
</main>
<footer>Call (718) 555-0101 &middot; <a href="mailto:Office@JoesPlumbing.com?subject=Hi">
Email us</a></footer></body></html>"""

CONTACT = """<html><body><h1>Contact</h1>
<p>Billing: joes.plumbing.nyc&#64;gmail.com</p>
<p>No replies: noreply@joesplumbing.com</p>
<a href="/cdn-cgi/l/email-protection" class="__cf_email__"
   data-cfemail="{cf}">[email&#160;protected]</a>
</body></html>"""


def _cf(address: str, key: int = 0x42) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in address)


def test_extract_reads_mailto_text_and_cloudflare_and_rejects_the_rest():
    found, links = contacts.extract(HOME, "https://joesplumbing.com/")
    assert found == ["office@joesplumbing.com"]
    page = CONTACT.format(cf=_cf("jobs@joesplumbing.com"))
    found, _ = contacts.extract(page, "https://joesplumbing.com/contact")
    assert found == ["jobs@joesplumbing.com", "joes.plumbing.nyc@gmail.com"]
    assert ("https://joesplumbing.com/contact", "Contact") in links


@pytest.mark.parametrize("address", [
    "logo@2x.png", "hero@3x.webp", "you@example.com", "info@example.org",
    "name@domain.com", "email@joesplumbing.com", "your@joesplumbing.com",
    "0123456789abcdef0123456789abcdef@o1.ingest.sentry.io", "support@wix.com",
    "a@sentry-next.wixpress.com", "noreply@joesplumbing.com",
    "do-not-reply@joesplumbing.com", "someone@site.test", "x@y", "no-at-sign",
    "john.doe@yourdomain.com",
])
def test_placeholders_and_platform_addresses_are_rejected(address):
    assert not contacts.plausible(address)


@pytest.mark.parametrize("address", [
    "office@joesplumbing.com", "joes.plumbing.nyc@gmail.com", "maria@salon-nyc.net",
])
def test_real_looking_addresses_pass(address):
    assert contacts.plausible(address)


def test_contact_pages_are_same_site_only():
    _found, links = contacts.extract(HOME, "https://www.joesplumbing.com/")
    pages = contacts.contact_pages("https://www.joesplumbing.com/", links, 3)
    assert pages == ["https://www.joesplumbing.com/about-us",
                     "https://www.joesplumbing.com/contact"]


def test_same_domain_is_preferred():
    ranked = contacts.rank([contacts.Found("a@gmail.com", "u", False),
                            contacts.Found("b@joes.com", "u", True)])
    assert [f.address for f in ranked] == ["b@joes.com", "a@gmail.com"]


def _serve(monkeypatch, pages: dict[str, tuple[int, str]]) -> list[str]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        status, body = pages.get(url, (404, "nope"))
        headers = {"content-type": "text/html; charset=utf-8"}
        if status in (301, 302):
            return httpx.Response(status, headers={"location": body})
        return httpx.Response(status, text=body, headers=headers)

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    async def public(url):
        return None

    monkeypatch.setattr(contacts.httpx, "AsyncClient", client)
    monkeypatch.setattr(_netguard, "assert_public", public)
    return seen


async def test_discover_reads_home_and_contact_pages(monkeypatch):
    seen = _serve(monkeypatch, {
        "https://joesplumbing.com/robots.txt": (404, ""),
        "https://joesplumbing.com/": (200, HOME),
        "https://joesplumbing.com/contact": (200, CONTACT.format(cf="zz")),
        "https://joesplumbing.com/about-us": (200, "<p>About Joe</p>"),
    })
    found, _notes = await contacts.discover("joesplumbing.com")
    assert [f.address for f in found] == ["office@joesplumbing.com",
                                          "joes.plumbing.nyc@gmail.com"]
    assert found[0].same_domain and not found[1].same_domain
    assert found[1].url == "https://joesplumbing.com/contact"
    assert not any("other-site.com" in u or "facebook" in u for u in seen)


async def test_robots_disallow_is_honoured(monkeypatch):
    seen = _serve(monkeypatch, {
        "https://joesplumbing.com/robots.txt": (200, "User-agent: *\nDisallow: /\n"),
        "https://joesplumbing.com/": (200, HOME),
    })
    found, notes = await contacts.discover("https://joesplumbing.com/")
    assert found == [] and "robots" in notes[0]
    assert seen == ["https://joesplumbing.com/robots.txt"]


async def test_unreadable_robots_fetches_nothing(monkeypatch):
    seen = _serve(monkeypatch, {
        "https://joesplumbing.com/robots.txt": (503, ""),
        "https://joesplumbing.com/": (200, HOME),
    })
    found, notes = await contacts.discover("https://joesplumbing.com/")
    assert found == [] and "503" in notes[0]
    assert seen == ["https://joesplumbing.com/robots.txt"]


async def test_a_redirect_off_the_site_is_not_read(monkeypatch):
    _serve(monkeypatch, {
        "https://joesplumbing.com/robots.txt": (404, ""),
        "https://joesplumbing.com/": (302, "https://parked-domains.example.net/"),
        "https://parked-domains.example.net/": (200, "<a href='mailto:sales@parked.net'>"),
    })
    found, notes = await contacts.discover("https://joesplumbing.com/")
    assert found == [] and "off the site" in notes[0]


async def test_no_website_means_no_guess():
    found, notes = await contacts.discover(None)
    assert found == [] and notes == ["no website on record"]
