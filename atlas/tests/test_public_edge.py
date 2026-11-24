"""Traffic from the public-internet proxy (Caddyfile.public) is never mesh.

OrbStack shows every published-port client as the docker gateway (a 192.168.x
address the mesh list trusts), so shop-public marks its traffic and the API
judges it as "public".
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from atlas.api import peers

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ("192.0.2.1", 4000)   # a docker-bridge peer, as the API sees Caddy


def _req(peer, headers):
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": peer}
    return Request(scope)


def test_the_marker_from_the_proxy_makes_the_client_public():
    r = _req(("172.18.0.5", 1), {"x-atlas-public": "1", "x-forwarded-for": "100.64.1.1"})
    assert peers.effective_client(r) == "public"


def test_the_marker_lowers_trust_from_any_peer():
    # Only ever lowers trust, so it is believed from outside PROXY_NETWORK too.
    r = _req(("203.0.113.9", 1), {"x-atlas-public": "1", "x-forwarded-for": "100.64.1.1"})
    assert peers.effective_client(r) == "public"


def test_the_marker_header_name_is_case_insensitive(memory_backend):
    # The server lower-cases header names (ASGI); any casing still lowers trust.
    from atlas.api.main import app
    c = TestClient(app, client=("172.18.0.5", 4000))
    r = c.get("/api/session", headers={"X-ATLAS-PUBLIC": "1"})
    assert r.status_code == 404 and r.text == "not here"


def test_no_marker_keeps_the_forwarded_address_for_the_mesh_proxy():
    r = _req(("172.18.0.5", 1), {"x-forwarded-for": "100.64.1.1"})
    assert peers.effective_client(r) == "100.64.1.1"


def test_a_public_request_is_refused_on_a_mesh_only_path(memory_backend):
    from atlas.api.main import app
    c = TestClient(app, client=("172.18.0.5", 4000))
    ok = c.get("/api/session", headers={"X-Forwarded-For": "100.64.1.1"})
    assert ok.status_code != 404          # mesh client via proxy: reaches the route
    r = c.get("/api/session", headers={"X-Forwarded-For": "100.64.1.1",
                                       "X-Atlas-Public": "1"})
    assert r.status_code == 404 and r.text == "not here"


def test_orbstack_subnet_caddy_is_refused_on_a_mesh_only_path(memory_backend,
                                                             monkeypatch):
    # OrbStack's compose network is 192.0.2.0/24 (outside PROXY_NETWORK)
    # and compose lists 192.168.0.0/16 as mesh: the marker must still win.
    monkeypatch.setenv("MESH_ALLOWED_CIDRS",
                       "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
    from atlas.api.main import app
    c = TestClient(app, client=("192.0.2.5", 4000))
    assert c.get("/api/session").status_code != 404
    r = c.get("/api/session", headers={"X-Atlas-Public": "1"})
    assert r.status_code == 404 and r.text == "not here"


def test_caddyfile_public_overwrites_the_marker_and_strips_forwarded_headers():
    text = (ROOT / "Caddyfile.public").read_text()
    for line in ("header_up X-Atlas-Public 1", "header_up -X-Forwarded-For",
                 "header_up -X-Real-IP", "header_up -X-Atlas-Device"):
        assert line in text
    assert "trusted_proxies" not in text
