"""The store's contact page (modules/academy/site.py).

ATLAS is local-only (2026-10-29): the contact form opens the visitor's mail
app, and `POST /public/academy/contact` is gone from the API, its public
allow-list and the Caddyfile.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import site

ROOT = Path(__file__).resolve().parents[1]


def test_the_contact_route_is_gone(memory_backend):
    from atlas.api.main import PUBLIC_EXACT, _is_public, app
    path = "/public/academy/contact"
    assert path not in PUBLIC_EXACT and not _is_public(path)
    assert path not in {getattr(r, "path", "") for r in app.routes}
    assert path not in (ROOT / "Caddyfile").read_text()
    client = TestClient(app, client=("127.0.0.1", 4000))
    r = client.post(path, data={"name": "x"}, follow_redirects=False)
    assert r.status_code in (404, 405)


def _without_signup(html: str) -> str:
    """The free-updates signup form is the one allowed post on a store page."""
    import re
    return re.sub(r"<form[^>]*data-sub-form.*?</form>", "", html, flags=re.DOTALL)


def _without_contact(html: str) -> str:
    """The contact form posts to /public/agency/contact (2026-11-14)."""
    import re
    return re.sub(r"<form[^>]*data-contact-form.*?</form>", "", html, flags=re.DOTALL)


def _built():
    return dict(site.render_root(cat_mod.load()))


def test_the_contact_page_posts_to_the_agency_contact_route_and_keeps_the_mail_address():
    files = _built()
    page = _without_signup(files["contact/index.html"].decode())
    assert ('<form class="contact" method="post" action="/public/agency/contact" '
            'data-contact-form>') in page
    for field in ("name", "email", "message", "source", "website"):
        assert f'name="{field}"' in page
    assert 'name="source" value="store-contact"' in page
    assert "mailto:" in page and "owner@example.com" in page      # plain fallback
    assert page.count("<script") == 2 and 'src="../assets/app.js"' in page
    assert 'src="../assets/contact.js"' in page
    assert "mapfront.io" not in page
    assert "contact/sent/index.html" not in files


def test_no_store_page_posts_to_atlas():
    for name, body in _built().items():
        if name.endswith(".html"):
            text = _without_contact(_without_signup(body.decode()))
            if name == "join/index.html":
                # The one sign-in form: posts the receipt to the existing recover route.
                assert text.count('method="post"') == 1, name
                assert text.count("/public/") == 1 and f'action="{site.RECOVER_ACTION}"' in text
                continue
            assert "/public/" not in text, name
            assert 'method="post"' not in text, name
    assert site.CONTACT_ACTION == "/public/agency/contact"


def test_join_page_keeps_the_stripe_links_and_takes_no_card():
    import re
    pages = {n: b.decode() for n, b in _built().items() if n.endswith(".html")}
    join = pages["join/index.html"]
    assert "Join / sign in" in pages["academy/index.html"]
    for field in ("cc-number", "cvc", 'type="password"'):
        assert field not in join
    pat = r'href="(https://buy\.stripe\.com/[^"]+)"'
    on_join = set(re.findall(pat, join))
    elsewhere = set()
    for n, body in pages.items():
        if n != "join/index.html":
            elsewhere |= set(re.findall(pat, body))
    assert on_join and on_join <= elsewhere
    assert "/join/" in _built()["sitemap-academy.xml"].decode()
