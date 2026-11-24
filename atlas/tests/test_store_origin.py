"""The store's one origin (atlas/modules/store_origin.py).

The store is ACADEMY_PUBLIC_ORIGIN (https://agency.example.com). Links, the CORS
origin, allow-lists, SEO targets and the spoken address all derive from one
helper."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from atlas.kernel.contracts import Health
from atlas.modules import _http, store_origin
from atlas.modules.qa import browser_service, egress_proxy
from atlas.modules.seo import panel, store, vitals


@pytest.mark.parametrize("raw", ["", "http://agency.example.com", "agency.example.com"])
def test_a_bad_value_falls_back_to_the_default(monkeypatch, raw):
    monkeypatch.setenv(store_origin.ENV, raw)
    assert store_origin.origin() == "https://agency.example.com"
    assert store_origin.host() == "agency.example.com"


def test_the_host_is_said_naturally():
    assert store_origin.spoken("agency.example.com") == "agency dot one eight d n dot com"


def test_captions_show_the_host_not_its_spoken_form():
    from atlas.modules.academy import tutorials
    said = f"there's more at {store_origin.spoken()}."
    assert tutorials.shown(said) == "there's more at agency.example.com."


# ------------------------------------------------------------------- SEO

async def test_seo_targets_are_the_store_and_never_the_retired_row(monkeypatch):
    async def rows(*_a: Any) -> list[dict[str, Any]]:
        return [{"slug": "joes-7", "domain": "joes.example", "path": None, "client": "Joe"},
                {"slug": "mapfront-shop", "domain": "old-shop.example", "path": None,
                 "client": "Mapfront"}]
    monkeypatch.setattr(store, "fetch_all", rows)
    got = await store.targets()
    assert [s["domain"] for s in got] == ["joes.example", "agency.example.com"]
    assert (await store.site("academy-store"))["domain"] == "agency.example.com"
    assert "mapfront-shop" not in store.OWN


def test_the_panel_hides_an_old_reading_of_the_retired_row():
    old = {"health": "unknown", "at": None, "detail": "x", "evidence": {"sites": [
        {"slug": "mapfront-shop", "domain": "old-shop.example", "unread": ["HTTP 400"]},
        {"slug": "academy-store", "domain": "agency.example.com", "unread": []}]}}
    out = panel.summarize(None, old, [])
    assert [s["domain"] for s in out["sites"]] == ["agency.example.com"]


def test_the_schema_withdraws_only_the_retired_rows():
    sql = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()
    block = sql[sql.index("the old shop host is gone"):][:1200]
    assert "DELETE" not in block.split("\n\n")[0]
    for table in ("seo_audits", "seo_keywords", "seo_ranks", "seo_reports", "seo_changes"):
        assert f"UPDATE {table}" in block
    assert block.count("WHERE slug = 'mapfront-shop'") == 5


# -------------------------------------------------------------- PageSpeed

def _mock(monkeypatch, handler) -> None:
    real = httpx.AsyncClient
    monkeypatch.setattr(_http.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


async def test_a_pagespeed_400_says_googles_reason_without_the_key(monkeypatch):
    """2026-10-29: the check said only "HTTP 400 from /pagespeedonline/...".
    Google's own message (Lighthouse could not load the page) is the reason."""
    fake = "AIzaFAKEnot-a-real-key-123456"
    monkeypatch.setenv("GOOGLE_PAGESPEED_API_KEY", fake)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(400, json={"error": {"code": 400, "message": (
            "Lighthouse returned error: FAILED_DOCUMENT_REQUEST. Lighthouse was "
            f"unable to reliably load the page. (Details: net::ERR_TIMED_OUT) {fake}")}})
    _mock(monkeypatch, handler)
    got, why = await vitals.measure("https://old-shop.example/", "mobile")
    assert got is None and why is not None
    assert "HTTP 400" in why and "FAILED_DOCUMENT_REQUEST" in why
    assert fake not in why
    assert "category=performance" in seen["url"] and "strategy=mobile" in seen["url"]


async def test_other_callers_keep_the_short_reason(monkeypatch):
    _mock(monkeypatch, lambda r: httpx.Response(400, json={"error": {"message": "long"}}))
    _, why = await _http.get_json("https://api.example/x")
    assert why == "HTTP 400 from /x"


async def test_an_unreadable_store_is_unknown_with_the_reason(monkeypatch):
    async def targets() -> list[dict[str, Any]]:
        return [store.academy()]

    async def get_json(url: str, **kw: Any) -> tuple[Any, str | None]:
        assert kw.get("detail") is True
        return None, "HTTP 400 from /pagespeedonline/v5/runPagespeed: FAILED_DOCUMENT_REQUEST"

    async def prior(*_a: Any, **_k: Any) -> list[Any]:
        return []
    monkeypatch.setattr(store, "targets", targets)
    monkeypatch.setattr(store, "recent_evidence", prior)
    monkeypatch.setattr(_http, "get_json", get_json)
    obs = await vitals.check()
    assert obs.health is Health.UNKNOWN
    assert "agency.example.com" in obs.detail and "FAILED_DOCUMENT_REQUEST" in obs.detail


# ----------------------------------------------------------- QA allow-lists

@pytest.mark.parametrize("mod", [egress_proxy, browser_service])
def test_qa_allows_only_the_one_agency_host(mod):
    assert "agency.example.com" in mod.ALLOWED
    assert not any(h.endswith("mapfront") or "mapfront" in h for h in mod.ALLOWED)


def test_the_egress_proxy_allows_the_agency_host_only():
    assert egress_proxy.host_allowed("agency.example.com")
    assert not egress_proxy.host_allowed("joes-7.example.net")


def test_the_browser_allows_the_agency_host_only():
    assert browser_service.allowed("https://agency.example.com/preview/joes-7/")
    assert not browser_service.allowed("https://joes-7.example.net/")


def test_store_facing_constants_name_the_new_host():
    from atlas.api import academy, academy_support
    from atlas.modules.academy import lesson_short, promo_copy, reel, tutorial_series
    from atlas.modules.qa import security, video
    assert academy_support.STORE_ORIGIN == "https://agency.example.com"
    assert not hasattr(academy_support, "SENT_URL")       # no help/sent/ page
    assert not hasattr(academy, "CONTACT_SENT_URL")       # no contact/sent/ page
    assert academy.RECOVER_PAGE_URL.startswith("https://agency.example.com/")
    assert reel.STORE == lesson_short.STORE == "agency.example.com"
    assert promo_copy.STORE_URL == tutorial_series.STORE_URL == "https://agency.example.com/academy"
    assert security.FIXED_HOSTS["shop"] == "agency.example.com"
    assert "mapfront" not in " ".join(video.ALLOWED_HOSTS)
