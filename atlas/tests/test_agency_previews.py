"""`/api/agency/previews` and `/api/agency/preview/<slug>/...`: built demo
sites shown inside the portal. Generated HTML is untrusted, so every response
carries a sandbox CSP and nothing outside the slug directory is reachable."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import agency_previews, auth

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.1/32")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51013)) as c:
        yield c


@pytest.fixture
def sites(monkeypatch, tmp_path):
    root = tmp_path / "sites"
    d = root / "joes-barber-7"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<h1>Joe</h1>")
    (d / "x.css").write_text("b{}")
    (d / "notes.txt").write_text("no")
    (d / "evil.php").write_text("no")
    (root / "secret.html").write_text("outside")
    (root / "joes-barber-7.LAUNCH_CHECK.md").write_text("x")
    monkeypatch.setattr(agency_previews, "sites_dir", lambda: root)
    return root


def _login(client) -> None:
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200


def test_unauthenticated_refused(client, sites):
    assert client.get("/api/agency/previews").status_code in (401, 403)
    assert client.get("/api/agency/preview/joes-barber-7/").status_code in (401, 403)


def test_index_served_with_sandbox_csp(client, sites):
    _login(client)
    r = client.get("/api/agency/preview/joes-barber-7/")
    assert r.status_code == 200 and "Joe" in r.text
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts") and "default-src 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    css = client.get("/api/agency/preview/joes-barber-7/x.css")
    assert css.status_code == 200 and "sandbox" in css.headers["content-security-policy"]


@pytest.mark.parametrize("path", [
    "../secret.html", "..%2Fsecret.html", "%2e%2e/secret.html",
    "notes.txt", "evil.php", "missing.html", "sub/../../secret.html"])
def test_traversal_and_disallowed_404(client, sites, path):
    _login(client)
    r = client.get(f"/api/agency/preview/joes-barber-7/{path}")
    assert r.status_code == 404
    if ".." not in path.replace("%2e%2e", "").replace("..%2F", ""):
        assert "sandbox" in r.headers["content-security-policy"]


def test_404_carries_csp(client, sites):
    _login(client)
    r = client.get("/api/agency/preview/joes-barber-7/evil.php")
    assert "sandbox" in r.headers["content-security-policy"]


@pytest.mark.parametrize("slug", ["Joes", "joes_barber", "..", "joes.barber", "nope-1"])
def test_bad_or_unknown_slug_404(client, sites, slug):
    _login(client)
    assert client.get(f"/api/agency/preview/{slug}/").status_code == 404


def test_symlink_escape_404(client, sites):
    (sites / "joes-barber-7" / "link.html").symlink_to(sites / "secret.html")
    _login(client)
    assert client.get("/api/agency/preview/joes-barber-7/link.html").status_code == 404


def test_list_previews(client, sites):
    _login(client)
    r = client.get("/api/agency/previews")
    assert r.status_code == 200
    body = r.json()
    assert [p["slug"] for p in body["previews"]] == ["joes-barber-7"]
    p = body["previews"][0]
    assert p["lead_id"] == 7 and p["built"] is True and p["name"]
    assert body["read_at"]


def test_list_unreadable_is_503_not_empty(client, monkeypatch, tmp_path):
    monkeypatch.setattr(agency_previews, "sites_dir", lambda: tmp_path / "absent")
    _login(client)
    assert client.get("/api/agency/previews").status_code == 503


# --- token-scoped links: a sandboxed frame has an opaque origin and sends no cookie ---
import time

SLUG = "joes-barber-7"


def _tok(slug=SLUG, now=None):
    return agency_previews.mint_token(slug, now=now)


def test_token_valid_serves_without_cookie(client, sites):
    (sites / SLUG / "contact.html").write_text("<h1>Contact</h1>")
    r = client.get(f"/api/agency/preview-t/{_tok()}/{SLUG}/contact.html")
    assert r.status_code == 200 and "Contact" in r.text
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts") and "default-src 'none'" in csp
    assert "allow-same-origin" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert client.get(f"/api/agency/preview-t/{_tok()}/{SLUG}/").status_code == 200


def test_token_expired_refused(client, sites):
    old = _tok(now=time.time() - 3 * 3600 - 10)
    r = client.get(f"/api/agency/preview-t/{old}/{SLUG}/")
    assert r.status_code == 401


def test_token_wrong_slug_refused(client, sites):
    (sites / "other-8").mkdir()
    (sites / "other-8" / "index.html").write_text("x")
    r = client.get(f"/api/agency/preview-t/{_tok()}/other-8/")
    assert r.status_code == 401


@pytest.mark.parametrize("mangle", [
    lambda t: t[:-1] + ("0" if t[-1] != "0" else "1"),
    lambda t: "9999999999." + t.split(".")[1],
    lambda t: t.split(".")[0],
    lambda t: "",
    lambda t: "x.y",
])
def test_token_tampered_refused(client, sites, mangle):
    t = mangle(_tok())
    assert client.get(f"/api/agency/preview-t/{t}/{SLUG}/").status_code in (401, 404)


def test_token_from_other_secret_refused(client, sites, monkeypatch):
    t = _tok()
    monkeypatch.setenv("SESSION_SECRET", "z" * 40)
    assert client.get(f"/api/agency/preview-t/{t}/{SLUG}/").status_code == 401


@pytest.mark.parametrize("path", [
    "../secret.html", "..%2Fsecret.html", "%2e%2e/secret.html",
    "notes.txt", "evil.php", "missing.html", "sub/../../secret.html"])
def test_token_path_traversal_still_refused(client, sites, path):
    r = client.get(f"/api/agency/preview-t/{_tok()}/{SLUG}/{path}")
    # the client collapses a literal "../" before sending: either way, refused
    assert r.status_code in (401, 404) and "outside" not in r.text


def test_token_symlink_escape_refused(client, sites):
    (sites / SLUG / "link.html").symlink_to(sites / "secret.html")
    assert client.get(f"/api/agency/preview-t/{_tok()}/{SLUG}/link.html").status_code == 404


def test_token_route_is_get_only(client, sites):
    r = client.post(f"/api/agency/preview-t/{_tok()}/{SLUG}/")
    assert r.status_code in (403, 405)


def test_no_token_no_cookie_still_401(client, sites):
    assert client.get(f"/api/agency/preview/{SLUG}/contact.html").status_code in (401, 403)
    assert client.get("/api/agency/previews").status_code in (401, 403)


def test_list_carries_slug_token_src(client, sites):
    _login(client)
    p = client.get("/api/agency/previews").json()["previews"][0]
    assert p["src"].startswith("/api/agency/preview-t/") and p["src"].endswith(f"/{SLUG}/")
    tok = p["src"].split("/")[4]
    assert client.get(p["src"]).status_code == 200
    # stable within the hour bucket, so a list refresh does not reload the frame
    assert client.get("/api/agency/previews").json()["previews"][0]["src"] == p["src"]
    assert agency_previews.verify_token(tok, SLUG)


def test_csp_lets_frame_open_tel_and_mailto_only(client, sites):
    _login(client)
    csp = client.get(f"/api/agency/preview/{SLUG}/").headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts allow-top-navigation-to-custom-protocols;")
    for banned in ("allow-same-origin", "allow-top-navigation ", "allow-forms",
                   "allow-popups"):
        assert banned not in csp


def test_access_log_redacts_token(monkeypatch):
    import logging
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0,
                            '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:5", "GET", f"/api/agency/preview-t/{_tok()}/{SLUG}/a.css",
                             "1.1", 200), None)
    assert agency_previews.RedactTokenFilter().filter(rec) is True
    line = rec.getMessage()
    assert _tok() not in line and "preview-t/[token]/" in line
