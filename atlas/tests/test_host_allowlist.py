"""T9: the Host-header allowlist against DNS rebinding of the loopback API.

A page on evil.example that rebinds its name to 127.0.0.1 reaches the API
with `Host: evil.example`; a request typed at the API carries one of the
names it is really served as. The allowlist is switched on by setting
ATLAS_ALLOWED_HOSTS (the local compose file does); unset, nothing changes,
so the server behind Caddy and the test suite keep their own Host names.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, allowed: str | None):
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    if allowed is None:
        monkeypatch.delenv("ATLAS_ALLOWED_HOSTS", raising=False)
    else:
        monkeypatch.setenv("ATLAS_ALLOWED_HOSTS", allowed)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return TestClient(app, client=("127.0.0.1", 51000))


@pytest.fixture
def on(monkeypatch, memory_backend):
    with _client(monkeypatch, "shop.local:8081, Extra.Example:9000") as c:
        yield c


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8080",
                                  "127.0.0.1", "localhost", "127.0.0.1:8081",
                                  "localhost:8080.evil.example", ""])
def test_a_foreign_host_gets_421(on, host):
    r = on.get("/healthz", headers={"Host": host})
    assert r.status_code == 421


@pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8080", "api:8080",
                                  "LOCALHOST:8080"])
def test_the_names_the_api_is_served_as_pass(on, host):
    r = on.get("/healthz", headers={"Host": host})
    assert r.status_code == 200


@pytest.mark.parametrize("host", ["shop.local:8081", "extra.example:9000"])
def test_atlas_allowed_hosts_extends_the_list(on, host):
    r = on.get("/healthz", headers={"Host": host})
    assert r.status_code == 200


def test_the_refusal_comes_before_the_mesh_rule(on):
    # A mesh-only path from a foreign Host is 421, not the mesh's 404 and
    # never the page.
    r = on.get("/api/overview", headers={"Host": "evil.example"})
    assert r.status_code == 421


def test_unset_leaves_hosts_alone(monkeypatch, memory_backend):
    with _client(monkeypatch, None) as c:
        r = c.get("/healthz", headers={"Host": "evil.example"})
    assert r.status_code == 200
