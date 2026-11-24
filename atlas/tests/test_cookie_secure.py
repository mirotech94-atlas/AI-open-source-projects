"""The session cookie is `Secure` whenever the dashboard is reached over HTTPS
(audit #16 / ranked issue 4).

Caddy serves the dashboard on 443 and forwards X-Forwarded-Proto; the mesh
port :8080 is plain HTTP, where a Secure cookie would be dropped. Unset or
"auto" follows the request; "true" and "false" are fixed overrides.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.api.peers import PROXY_NETWORK

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
CADDY = str(next(PROXY_NETWORK.hosts()) + 4)
MESH = "100.64.0.7"


@pytest.fixture
def app(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "100.64.0.0/10")
    monkeypatch.delenv("DASHBOARD_HTTPS", raising=False)
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return app


def _login_cookie(app, peer: str, headers: dict[str, str] | None = None,
                  base_url: str = "http://testserver") -> str:
    with TestClient(app, client=(peer, 40000), base_url=base_url) as c:
        r = c.post("/api/login", json={"password": PASSWORD}, headers=headers or {})
        assert r.status_code == 200, r.text
        cookie = r.headers["set-cookie"]
    assert cookie.startswith(auth.COOKIE + "=")
    assert "httponly" in cookie.lower()
    return cookie.lower()


def _secure(cookie: str) -> bool:
    return any(part.strip() == "secure" for part in cookie.split(";"))


def test_https_through_caddy_is_secure_by_default(app):
    cookie = _login_cookie(app, CADDY, {"X-Forwarded-For": MESH,
                                        "X-Forwarded-Proto": "https"})
    assert _secure(cookie)


def test_an_https_request_is_secure_by_default(app):
    cookie = _login_cookie(app, MESH, base_url="https://testserver")
    assert _secure(cookie)


def test_plain_http_on_the_mesh_port_is_not_marked_secure(app):
    """http://<mesh-ip>:8080 -- a Secure cookie would never come back."""
    assert not _secure(_login_cookie(app, MESH))


def test_a_forwarded_proto_is_believed_only_from_the_proxy(app):
    cookie = _login_cookie(app, MESH, {"X-Forwarded-Proto": "https"})
    assert not _secure(cookie)


def test_http_through_caddy_is_not_marked_secure(app):
    cookie = _login_cookie(app, CADDY, {"X-Forwarded-For": MESH,
                                        "X-Forwarded-Proto": "http"})
    assert not _secure(cookie)


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_dashboard_https_true_always_sets_secure(app, monkeypatch, value):
    monkeypatch.setenv("DASHBOARD_HTTPS", value)
    assert _secure(_login_cookie(app, MESH))


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_dashboard_https_false_is_the_local_dev_opt_out(app, monkeypatch, value):
    monkeypatch.setenv("DASHBOARD_HTTPS", value)
    cookie = _login_cookie(app, CADDY, {"X-Forwarded-For": MESH,
                                        "X-Forwarded-Proto": "https"})
    assert not _secure(cookie)


def test_auto_is_the_same_as_unset(app, monkeypatch):
    monkeypatch.setenv("DASHBOARD_HTTPS", "auto")
    assert _secure(_login_cookie(app, CADDY, {"X-Forwarded-For": MESH,
                                              "X-Forwarded-Proto": "https"}))
    assert not _secure(_login_cookie(app, MESH))


def test_logout_clears_the_cookie_with_the_same_flags(app):
    with TestClient(app, client=(CADDY, 40000)) as c:
        headers = {"X-Forwarded-For": MESH, "X-Forwarded-Proto": "https"}
        r = c.post("/api/logout", headers=headers)
        cookie = r.headers["set-cookie"].lower()
        assert cookie.startswith(auth.COOKIE + "=") and _secure(cookie)
        r = c.get("/logout", headers=headers, follow_redirects=False)
        assert r.status_code == 303 and _secure(r.headers["set-cookie"].lower())
