"""The SEO panel beside the Agency map (`GET /api/agency/seo`).

Built from the seo checks' last observations and the stored reports: per
site, Search Console clicks, position and counts, Core Web Vitals scores,
issues; each with its read time. A reading that does not exist is null and
named in `unread` (UNKNOWN on the page), never 0. Read only."""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.modules.seo import panel, store

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)

GSC = {"health": "degraded", "at": AT,
       "detail": "80 clicks in 28 days; joes.example: no sitemap submitted",
       "evidence": {"sites": [
           {"slug": "joes", "domain": "joes.example", "status": "issues",
            "read_at": AT.isoformat(), "range": {"start": "2026-10-01", "end": "2026-10-26"},
            "totals": {"clicks": 80, "impressions": 2000, "ctr": 0.04, "position": 9.5},
            "striking": [{"query": "secret query text", "position": 12}],
            "losing": [{"page": "https://joes.example/", "clicks": 2, "prior": 9}],
            "unread": [], "issues": ["no sitemap submitted"]},
           {"slug": "academy-store", "domain": "agency.storefront.test", "status": "unknown",
            "read_at": AT.isoformat(), "totals": None, "striking": None, "losing": None,
            "unread": ["agency.storefront.test is not verified in Search Console"],
            "issues": []}]}}
CWV = {"health": "ok", "at": AT, "detail": "mobile score 41 at worst",
       "evidence": {"sites": [
           {"slug": "joes", "domain": "joes.example", "read_at": AT.isoformat(),
            "mobile": {"score": 41, "lab": {"lcp_ms": 5200, "cls": 0.02}},
            "desktop": {"score": 90, "lab": {"lcp_ms": 1100, "cls": 0.0}},
            "regressions": ["mobile score 70 -> 41"], "issues": [], "unread": []}]}}


def test_summary_per_site_with_read_times_and_unknown_for_missing():
    got = panel.summarize(GSC, CWV, [{"slug": "joes", "kind": "client_weekly",
                                      "week": "2026-W44", "at": AT}])
    joes = next(s for s in got["sites"] if s["slug"] == "joes")
    assert joes["search"] == {"clicks": 80, "impressions": 2000, "ctr": 0.04,
                              "position": 9.5, "striking": 1, "losing": 1,
                              "read_at": AT.isoformat(),
                              "range": {"start": "2026-10-01", "end": "2026-10-26"}}
    assert joes["vitals"]["mobile"] == 41 and joes["vitals"]["desktop"] == 90
    assert joes["vitals"]["regressions"] == 1 and joes["vitals"]["read_at"]
    assert "no sitemap submitted" in joes["issues"]
    assert any("mobile score 70 -> 41" in i for i in joes["issues"])
    store_ = next(s for s in got["sites"] if s["slug"] == "academy-store")
    assert store_["search"] is None and store_["unread"]
    assert store_["vitals"] is None
    assert got["checks"]["search_console"]["health"] == "degraded"
    assert got["reports"][0]["week"] == "2026-W44"
    # Query strings strangers typed are never shipped to the page.
    assert "secret query text" not in repr(got)


def test_no_reading_at_all_is_unknown_never_zero():
    got = panel.summarize(None, None, [])
    assert got["checks"]["search_console"] is None
    assert got["checks"]["core_web_vitals"] is None
    assert got["sites"] == []


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
    with TestClient(app, client=("127.0.0.1", 51013)) as test_client:
        yield test_client


def test_the_panel_needs_a_session(client):
    assert client.get("/api/agency/seo").status_code == 401


def test_a_failed_read_is_named_not_zero(client, monkeypatch):
    async def fails(*_a, **_k):
        raise RuntimeError("database is down")

    monkeypatch.setattr(store, "latest_observation", fails)
    monkeypatch.setattr(store, "recent_reports", fails)
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
    body = client.get("/api/agency/seo").json()
    assert body["checks"]["search_console"] is None
    assert body["errors"]["search_console"] and body["errors"]["reports"]
    assert body["served_at"]


def test_the_panel_module_is_read_only():
    src = (pathlib.Path(__file__).parent.parent / "atlas" / "modules" / "seo"
           / "panel.py").read_text()
    tree = ast.parse(src)
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for forbidden in ("execute", "execute_sql", "insert_report", "get_json", "post_json"):
        assert forbidden not in names, forbidden
    for verb in ("INSERT ", "UPDATE ", "DELETE "):
        assert verb not in src.upper(), verb
