"""Per-address throttles on the login and on /deliver (audit #20).

The login limiter used to be one global list: a device guessing on the mesh
locked Miro himself out. /deliver had no limit at all, and every request to it
costs a Stripe API call. Both are now keyed on the address the mesh guard
judges -- the forwarded one from our own proxy, not the proxy's.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, deliver
from atlas.api.peers import PROXY_NETWORK, PerAddressLimiter

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
CADDY = str(next(PROXY_NETWORK.hosts()) + 4)      # a peer on the proxy network


@pytest.fixture
def app(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "100.64.0.0/10")
    auth._attempts.clear()
    deliver._requests.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    yield app
    auth._attempts.clear()
    deliver._requests.clear()


def _via_caddy(app):
    return TestClient(app, client=(CADDY, 40000))


# ------------------------------------------------------------------ limiter

def test_the_limiter_is_per_address(monkeypatch):
    lim = PerAddressLimiter(3, 60.0)
    assert [lim.allow("a") for _ in range(4)] == [True, True, True, False]
    assert lim.allow("b") is True


def test_the_limiter_forgets_after_its_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("atlas.api.peers.time.monotonic", lambda: now[0])
    lim = PerAddressLimiter(2, 60.0)
    assert lim.allow("a") and lim.allow("a") and not lim.allow("a")
    now[0] += 61
    assert lim.allow("a") is True
    # An address with nothing left in its window is dropped, not kept forever.
    now[0] += 61
    assert lim.limited("a") is False and len(lim) == 0


def test_the_limiter_stays_bounded_under_a_spray_of_addresses():
    lim = PerAddressLimiter(1, 600.0, max_addresses=50)
    for i in range(500):
        lim.hit(f"198.51.100.{i % 250}-{i}")
    assert len(lim) <= 50


# -------------------------------------------------------------------- login

def test_login_failures_lock_out_only_the_guessing_address(app):
    with _via_caddy(app) as c:
        for _ in range(auth.MAX_ATTEMPTS):
            r = c.post("/api/login", json={"password": "wrong"},
                       headers={"X-Forwarded-For": "100.84.1.7"})
            assert r.status_code == 401
        r = c.post("/api/login", json={"password": PASSWORD},
                   headers={"X-Forwarded-For": "100.84.1.7"})
        assert r.status_code == 429
        # Miro, on another mesh device, still gets in.
        r = c.post("/api/login", json={"password": PASSWORD},
                   headers={"X-Forwarded-For": "100.84.1.8"})
        assert r.status_code == 200


def test_the_server_rendered_login_is_per_address_too(app):
    with _via_caddy(app) as c:
        for _ in range(auth.MAX_ATTEMPTS):
            c.post("/login", data={"password": "wrong", "next": "/"},
                   headers={"X-Forwarded-For": "100.84.1.7"})
        r = c.post("/login", data={"password": PASSWORD, "next": "/"},
                   headers={"X-Forwarded-For": "100.84.1.7"}, follow_redirects=False)
        assert r.status_code == 429
        r = c.post("/login", data={"password": PASSWORD, "next": "/"},
                   headers={"X-Forwarded-For": "100.84.1.8"}, follow_redirects=False)
        assert r.status_code == 303


def test_a_forged_forwarded_address_does_not_dodge_the_limit(app):
    """X-Forwarded-For is believed only from the proxy, so a direct mesh
    client cannot rotate it to get fresh attempts."""
    with TestClient(app, client=("100.84.1.7", 40000)) as c:
        for i in range(auth.MAX_ATTEMPTS):
            c.post("/api/login", json={"password": "wrong"},
                   headers={"X-Forwarded-For": f"100.84.2.{i}"})
        r = c.post("/api/login", json={"password": PASSWORD},
                   headers={"X-Forwarded-For": "100.84.3.1"})
        assert r.status_code == 429


# ------------------------------------------------------------------ deliver

def test_deliver_is_rate_limited_per_address(app, monkeypatch):
    stripe_calls: list[str] = []

    async def unpaid(sid: str) -> None:
        stripe_calls.append(sid)
    monkeypatch.setattr(deliver, "_paid_product", unpaid)
    sid = "cs_live_" + "a" * 30
    with _via_caddy(app) as c:
        for _ in range(deliver.MAX_REQUESTS):
            r = c.get(f"/deliver/{sid}", headers={"X-Forwarded-For": "203.0.113.9"})
            assert r.status_code == 404
        r = c.get(f"/deliver/{sid}", headers={"X-Forwarded-For": "203.0.113.9"})
        assert r.status_code == 429
        # Refused before Stripe is asked anything.
        assert len(stripe_calls) == deliver.MAX_REQUESTS
        # Another buyer is unaffected.
        r = c.get(f"/deliver/{sid}", headers={"X-Forwarded-For": "203.0.113.10"})
        assert r.status_code == 404
