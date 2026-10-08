"""Caddy's on-demand TLS ask (atlas/api/tls_ask.py): Caddy's own ask only.

The name rules themselves are tested with `hosting.tls_allowed` in
tests/test_mapfront_hosting.py; these pin who may ask at all.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from atlas.modules.agency import hosting


@pytest.fixture
def api(monkeypatch, tmp_path):
    from atlas.api.main import app
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", tmp_path)
    (tmp_path / ".releases" / "joes-7" / "20260929T000000000000Z").mkdir(parents=True)
    os.symlink(".releases/joes-7/20260929T000000000000Z", tmp_path / "joes-7")
    return app


def _ask(app, peer: str, domain: str, headers: dict | None = None) -> int:
    client = TestClient(app, client=(peer, 40000))
    return client.get("/internal/tls-ask", params={"domain": domain},
                      headers=headers or {}).status_code


def test_tls_ask_answers_caddys_own_ask(api):
    assert _ask(api, "172.18.0.3", "joes-7.mapfront.io") == 200
    assert _ask(api, "172.18.0.3", "mapfront.io") == 200
    assert _ask(api, "172.18.0.3", "nope.mapfront.io") == 404
    assert _ask(api, "172.18.0.3", "") == 404


def test_tls_ask_refuses_a_request_caddy_proxied(api):
    """A mesh client's request, proxied by Caddy, carries X-Forwarded-For."""
    assert _ask(api, "172.18.0.3", "joes-7.mapfront.io",
                {"X-Forwarded-For": "100.64.0.9"}) == 404


def test_tls_ask_refuses_any_peer_but_the_docker_bridge(api):
    assert _ask(api, "100.64.0.9", "joes-7.mapfront.io") == 404   # mesh, direct
    assert _ask(api, "127.0.0.1", "joes-7.mapfront.io") == 404
    assert _ask(api, "203.0.113.7", "joes-7.mapfront.io") == 404  # public


def test_tls_ask_is_not_a_public_path():
    from atlas.api import main
    assert not main._is_public("/internal/tls-ask")
