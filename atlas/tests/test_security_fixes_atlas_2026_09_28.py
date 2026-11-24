"""ATLAS audit 2026-09-28.

1. The old mesh-only `/ask` and `/agents` routes had no sign-in and nothing
   called them: they are gone, so no page the owner visits can drive the agents.
2. The agent tool `http_probe` and the agency site audit never fetch a private,
   loopback or metadata address, including via a redirect.
"""
from __future__ import annotations

import pytest

from atlas.api import main
from atlas.modules.agency import audit
from atlas.tools import builtin


def test_the_unauthenticated_ask_and_agents_routes_are_gone():
    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/ask" not in paths
    assert "/agents" not in paths


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/api/status",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://localhost/",
])
async def test_http_probe_refuses_non_public_addresses(url):
    out = await builtin.http_probe(url)
    assert out["ok"] is False
    assert "status" not in out


async def test_agency_fetch_refuses_non_public_addresses():
    html, _elapsed, size, _https, reason = await audit._fetch("http://127.0.0.1:8080/")
    assert html is None and size == 0 and reason
