"""The agency site's "ask for a free preview" form.

ATLAS is local-only (2026-10-29): the landing page's form opens the
visitor's mail app, and `POST /public/preview-request` is gone from the API,
its public allow-list and the Caddyfile.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "atlas" / "modules" / "agency" / "landing"


def test_the_landing_form_posts_to_the_contact_route_and_keeps_the_mail_address():
    """2026-11-14: the preview request is stored, emailed and sent to WhatsApp
    by /public/agency/contact; the mailto address stays on the page as text."""
    html = (LANDING / "index.html").read_text()
    form = html[html.index('<form class="ask"'):html.index("</form>")]
    assert 'method="post" action="/public/agency/contact"' in form
    assert set(re.findall(r'name="([a-z_]+)"', form)) == {
        "source", "topic", "website", "name", "email", "phone", "message"}
    assert "mailto:owner@example.com" in html and "{{" not in form
    assert "mapfront.io" not in html
    for name in ("Caddyfile.public", "Caddyfile.local"):
        caddy = (ROOT / name).read_text()
        assert "form-action 'self'" in caddy and "form-action mailto:" not in caddy


def test_the_preview_request_route_is_gone(memory_backend):
    from atlas.api.main import PUBLIC_EXACT, _is_public, app
    assert "/public/preview-request" not in PUBLIC_EXACT
    assert not _is_public("/public/preview-request")
    assert "/public/preview-request" not in {getattr(r, "path", "") for r in app.routes}
    assert "/public/preview-request" not in (ROOT / "Caddyfile").read_text()
    assert not (ROOT / "atlas" / "api" / "preview_request.py").exists()
    client = TestClient(app, client=("127.0.0.1", 4000))
    r = client.post("/public/preview-request", data={"email": "a@b.co"},
                    follow_redirects=False)
    assert r.status_code in (404, 405)
