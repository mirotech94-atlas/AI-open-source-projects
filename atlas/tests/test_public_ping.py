"""GET /healthz on the public edge: one exact path, one tiny fixed answer.

The public Caddy rewrites exactly `GET /healthz` to the API's /public/ping,
which says only that it is up and the time. The richer /healthz (module and
check counts, heartbeat ages) is never reachable from the internet through it.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (ROOT / "Caddyfile.public").read_text()
MARK = {"X-Atlas-Public": "1"}


def _client(memory_backend):
    from atlas.api.main import app
    return TestClient(app, client=("172.18.0.5", 4000))


def test_ping_answers_status_and_time_and_nothing_else(memory_backend):
    r = _client(memory_backend).get("/public/ping", headers=MARK)
    assert r.status_code == 200
    body = r.json()
    # "ok" is the one key the off-box watchdog's healthz check requires.
    assert set(body) == {"ok", "status", "time"}
    assert body["ok"] is True and body["status"] == "ok"
    assert datetime.fromisoformat(body["time"]).tzinfo is not None
    assert len(r.content) < 80


def test_ping_response_is_hardened(memory_backend):
    r = _client(memory_backend).get("/public/ping", headers=MARK)
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in r.headers


def test_ping_is_exact_not_a_prefix(memory_backend):
    c = _client(memory_backend)
    for path in ("/public/ping/", "/public/ping/x", "/public/pingx",
                 "/public/ping/../api/session"):
        assert c.get(path, headers=MARK).status_code == 404, path


def test_ping_does_not_carry_the_rich_healthz_fields(memory_backend):
    body = _client(memory_backend).get("/public/ping", headers=MARK).text
    for leak in ("modules", "checks", "heartbeat", "service"):
        assert leak not in body


def _healthz_block() -> str:
    m = re.search(r"^\thandle @healthz \{\n(.*?)\n\t\}$", PUBLIC, re.DOTALL | re.MULTILINE)
    assert m, "no handle @healthz block"
    return m.group(1)


def test_caddy_public_opens_exactly_get_healthz():
    m = re.search(r"@healthz \{\n(.*?)\n\t\}", PUBLIC, re.DOTALL)
    assert m
    lines = [x.strip() for x in m.group(1).splitlines()]
    assert lines == ["method GET", "path /healthz"]
    block = _healthz_block()
    assert "rewrite * /public/ping" in block
    assert "header_up X-Atlas-Public 1" in block
    for stripped in ("header_up -Cookie", "header_up -Authorization",
                     "header_up -X-Forwarded-For", "header_up -X-Atlas-Device"):
        assert stripped in block
    assert "request_body" in block and "header_down -Server" in block


def test_caddy_public_healthz_precedes_the_public_404_and_opens_nothing_else():
    assert PUBLIC.index("handle @healthz") < PUBLIC.index("handle /public/*")
    code = "\n".join(x for x in PUBLIC.splitlines() if not x.lstrip().startswith("#"))
    assert len(re.findall(r"/healthz", code)) == 1       # no wildcard, no second path
    assert "/healthz*" not in code and "/healthz/" not in code
