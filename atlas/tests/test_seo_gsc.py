"""Search Console, read only, per site property (2026-10-29).

Per property (sc-domain: preferred, URL-prefix too): 28-day clicks,
impressions, CTR and position; top queries and pages; striking-distance
queries (position 8-20, impressions, low CTR); pages losing clicks week over
week; sitemaps; URL inspection of the home page and top pages. Google's data
lags, so the window ends three days back. Every figure carries its read
time; a site that cannot be read is UNKNOWN, never OK. No network."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import _http
from atlas.modules.seo import gsc, store

SITES = [{"slug": "joes", "domain": "joes.example", "path": None, "client": "Joe's"},
         {"slug": "academy-store", "domain": "agency.storefront.test", "path": None,
          "client": "ATLAS Academy"}]


class Google:
    """Answers by URL and by the body's dimensions and dates."""

    def __init__(self, entries: list[dict[str, Any]], *, fail: set[str] | None = None,
                 sitemaps: list[dict[str, Any]] | None = None,
                 verdict: str = "PASS") -> None:
        self.entries = entries
        self.fail = fail or set()
        self.sitemaps = sitemaps if sitemaps is not None else [
            {"path": "https://joes.example/sitemap.xml", "errors": "0",
             "warnings": "0", "isPending": False,
             "lastDownloaded": "2026-10-27T10:00:00Z"}]
        self.verdict = verdict
        self.bodies: list[tuple[str, dict]] = []

    async def post(self, url, **kw):
        if "oauth2" in url:
            return {"access_token": "tok", "expires_in": 3600, "scope": gsc.SCOPE}, None
        body = kw.get("json_body") or {}
        self.bodies.append((url, body))
        if "urlInspection" in url:
            if "inspect" in self.fail:
                return None, "HTTP 429 Too Many Requests"
            return {"inspectionResult": {"indexStatusResult": {
                "verdict": self.verdict, "coverageState": "Submitted and indexed"}}}, None
        dims = body.get("dimensions") or []
        if "analytics" in self.fail:
            return None, "HTTP 500 Internal Server Error"
        if dims == []:
            return {"rows": [{"clicks": 40, "impressions": 2000, "ctr": 0.02,
                              "position": 11.5}]}, None
        if dims == ["query"]:
            return {"rows": [
                {"keys": ["bakery queens"], "clicks": 20, "impressions": 300,
                 "ctr": 0.066, "position": 3.1},
                {"keys": ["sourdough near me"], "clicks": 1, "impressions": 400,
                 "ctr": 0.0025, "position": 12.4},
                {"keys": ["cake shop astoria"], "clicks": 0, "impressions": 90,
                 "ctr": 0.0, "position": 9.0},
                {"keys": ["tiny"], "clicks": 0, "impressions": 3, "ctr": 0.0,
                 "position": 15.0}]}, None
        if dims == ["page"]:
            start = body["startDate"]
            if start == self.this_week_start:
                return {"rows": [
                    {"keys": ["https://joes.example/"], "clicks": 4, "impressions": 100},
                    {"keys": ["https://joes.example/menu"], "clicks": 10,
                     "impressions": 100}]}, None
            if start == self.prior_week_start:
                return {"rows": [
                    {"keys": ["https://joes.example/"], "clicks": 15, "impressions": 120},
                    {"keys": ["https://joes.example/menu"], "clicks": 9,
                     "impressions": 90}]}, None
            return {"rows": [
                {"keys": ["https://joes.example/menu"], "clicks": 25, "impressions": 900},
                {"keys": ["https://joes.example/"], "clicks": 15, "impressions": 800}]}, None
        return {"rows": []}, None

    async def get(self, url, **kw):
        if url.endswith("/sites"):
            return {"siteEntry": self.entries}, None
        if url.endswith("/sitemaps"):
            if "sitemaps" in self.fail:
                return None, "HTTP 500 Internal Server Error"
            return {"sitemap": self.sitemaps}, None
        return None, "HTTP 404 Not Found"


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
VERIFIED = [{"siteUrl": "sc-domain:joes.example", "permissionLevel": "siteOwner"},
            {"siteUrl": "https://agency.storefront.test/", "permissionLevel": "siteFullUser"}]


@pytest.fixture
def google(monkeypatch):
    def make(entries=VERIFIED, **kw):
        g = Google(entries, **kw)
        w = gsc.windows(NOW)
        g.this_week_start = str(w["week"][0])
        g.prior_week_start = str(w["prior"][0])
        monkeypatch.setattr(_http, "post_json", g.post)
        monkeypatch.setattr(_http, "get_json", g.get)
        return g
    for key in gsc.ENV:
        monkeypatch.setenv(key, "x")
    monkeypatch.setattr(gsc, "_token", None)

    async def targets():
        return [dict(s) for s in SITES]
    monkeypatch.setattr(store, "targets", targets)
    return make


def test_windows_end_three_days_back_for_googles_lag():
    w = gsc.windows(NOW)
    assert w["d28"] == (date(2026, 9, 29), date(2026, 10, 26))
    assert w["week"] == (date(2026, 10, 20), date(2026, 10, 26))
    assert w["prior"] == (date(2026, 10, 13), date(2026, 10, 19))


def test_property_prefers_a_verified_domain_property_over_an_unverified_first_entry():
    entries = [
        {"siteUrl": "https://joes.example/", "permissionLevel": "siteUnverifiedUser"},
        {"siteUrl": "http://www.joes.example/", "permissionLevel": "siteOwner"},
        {"siteUrl": "sc-domain:joes.example", "permissionLevel": "siteOwner"}]
    assert gsc.property_for("joes.example", entries) == ("sc-domain:joes.example", None)
    entries = entries[:2]
    assert gsc.property_for("www.joes.example", entries) == ("http://www.joes.example/",
                                                              None)
    prop, why = gsc.property_for("joes.example", entries[:1])
    assert prop is None and "not verified" in why


def test_striking_distance_is_position_8_to_20_with_impressions_and_low_ctr():
    rows = [{"keys": ["a"], "impressions": 400, "ctr": 0.0025, "position": 12.4,
             "clicks": 1},
            {"keys": ["b"], "impressions": 300, "ctr": 0.066, "position": 3.1,
             "clicks": 20},
            {"keys": ["c"], "impressions": 90, "ctr": 0.0, "position": 9.0, "clicks": 0},
            {"keys": ["d"], "impressions": 3, "ctr": 0.0, "position": 15.0, "clicks": 0},
            {"keys": ["e"], "impressions": 500, "ctr": 0.2, "position": 10.0,
             "clicks": 100}]
    got = [q["query"] for q in gsc.striking(rows)]
    assert got == ["a", "c"]


def test_losing_pages_compare_this_week_with_the_week_before():
    this = [{"keys": ["/"], "clicks": 4}, {"keys": ["/menu"], "clicks": 10}]
    prior = [{"keys": ["/"], "clicks": 15}, {"keys": ["/menu"], "clicks": 9},
             {"keys": ["/gone"], "clicks": 6}]
    got = gsc.losing(this, prior)
    assert [(p["page"], p["clicks"], p["prior"]) for p in got] == [
        ("/", 4, 15), ("/gone", 0, 6)]


async def test_a_verified_site_reads_every_figure_with_its_read_time(google):
    g = google()
    site = await gsc.read_site(SITES[0], "sc-domain:joes.example",
                               {"Authorization": "Bearer tok"}, NOW)
    assert site["status"] == "ok"
    assert site["read_at"] == NOW.isoformat()
    assert site["range"] == {"start": "2026-09-29", "end": "2026-10-26"}
    t = site["totals"]
    assert (t["clicks"], t["impressions"], t["ctr"], t["position"]) == (40, 2000, 0.02,
                                                                       11.5)
    assert site["top_queries"][0]["query"] == "bakery queens"
    assert site["top_pages"][0]["page"] == "https://joes.example/menu"
    assert [q["query"] for q in site["striking"]] == ["sourdough near me",
                                                     "cake shop astoria"]
    assert site["losing"][0]["page"] == "https://joes.example/"
    assert site["sitemaps"][0]["errors"] == 0
    assert site["inspected"]["n"] >= 1 and site["inspected"]["indexed"] == \
        site["inspected"]["n"]
    # The analytics query carries the property, URL-encoded, and read only.
    assert all("sc-domain%3Ajoes.example" in u for u, _ in g.bodies
               if "searchAnalytics" in u)


async def test_check_is_ok_with_clicks_and_the_window_and_read_time(google):
    google()
    obs = await gsc.check()
    assert obs.health is Health.OK, obs.detail
    assert obs.value == 80 and obs.unit == "clicks"
    assert "28 days" in obs.detail and "(read 20" in obs.detail
    assert {s["slug"] for s in obs.evidence["sites"]} == {"joes", "academy-store"}


async def test_an_unreadable_analytics_answer_is_unknown_never_ok(google):
    google(fail={"analytics"})
    obs = await gsc.check()
    assert obs.health is Health.UNKNOWN
    assert "HTTP 500" in obs.detail


async def test_a_missing_sitemap_or_unindexed_page_is_degraded(google):
    google(sitemaps=[], verdict="FAIL")
    obs = await gsc.check()
    assert obs.health is Health.DEGRADED
    issues = " ".join(i for s in obs.evidence["sites"] for i in s["issues"])
    assert "no sitemap" in issues and "not indexed" in issues


async def test_an_unreadable_sitemap_or_inspection_is_named_not_counted(google):
    google(fail={"sitemaps", "inspect"})
    obs = await gsc.check()
    site = obs.evidence["sites"][0]
    assert site["sitemaps"] is None and site["inspected"] is None
    assert any("sitemaps" in u for u in site["unread"])
    assert any("inspection" in u for u in site["unread"])
    assert obs.health is not Health.OK


async def test_an_unverified_site_is_unknown_and_named(google):
    google(entries=VERIFIED[:1])
    obs = await gsc.check()
    assert obs.health is Health.UNKNOWN and "agency.storefront.test" in obs.detail


def test_the_academy_store_is_a_target():
    assert store.ACADEMY["domain"] == "agency.storefront.test"


def test_a_vercel_preview_host_is_a_preview_and_a_real_domain_is_not():
    assert gsc.is_preview("atlas-agency-barber-shop-nyc-61.vercel.app")
    assert gsc.is_preview("Atlas-Agency-West-Vibe-53.vercel.app.")
    assert not gsc.is_preview("joes.example") and not gsc.is_preview("agency.storefront.test")
    assert not gsc.is_preview("vercel.app.evil.example")


async def test_client_previews_are_not_counted_unknown_and_are_named_as_noindex(
        google, monkeypatch):
    google()

    async def targets():
        return [*SITES, {"slug": "barber-shop-nyc-61", "client": "Barber",
                         "domain": "atlas-agency-barber-shop-nyc-61.vercel.app",
                         "path": None}]
    monkeypatch.setattr(store, "targets", targets)
    obs = await gsc.check()
    assert obs.health is Health.OK, obs.detail
    assert {s["slug"] for s in obs.evidence["sites"]} == {"joes", "academy-store"}
    assert obs.evidence["previews"] == ["atlas-agency-barber-shop-nyc-61.vercel.app"]
    assert "1 client preview(s) skipped (noindex)" in obs.detail


async def test_a_real_domain_without_a_property_still_makes_it_unknown(
        google, monkeypatch):
    google()

    async def targets():
        return [*SITES, {"slug": "tutorco", "client": "TUTORCO", "domain": "exampletutor.com",
                         "path": None},
                {"slug": "p", "client": "P", "path": None,
                 "domain": "atlas-agency-p-1.vercel.app"}]
    monkeypatch.setattr(store, "targets", targets)
    obs = await gsc.check()
    assert obs.health is Health.UNKNOWN
    assert "UNKNOWN for 1 site(s)" in obs.detail and "exampletutor.com" in obs.detail
    assert "atlas-agency-p-1" not in obs.detail


def _inspector(monkeypatch, answers: dict[str, dict | None]):
    seen: list[str] = []

    async def post(url, **kw):
        page = kw["json_body"]["inspectionUrl"]
        seen.append(page)
        res = answers.get(page)
        if res is None:
            return None, "HTTP 500 Internal Server Error"
        return {"inspectionResult": {"indexStatusResult": res}}, None
    monkeypatch.setattr(_http, "post_json", post)
    return seen


async def test_a_page_whose_canonical_is_elsewhere_is_judged_by_the_canonical(monkeypatch):
    seen = _inspector(monkeypatch, {
        "https://a.test/": {"verdict": "NEUTRAL", "userCanonical": "https://a.test/x/",
                            "coverageState": "Crawled - currently not indexed"},
        "https://a.test/x/": {"verdict": "PASS", "coverageState": "Submitted and indexed"}})
    got, why = await gsc._inspect("sc-domain:a.test", {}, ["https://a.test/"])
    assert seen == ["https://a.test/", "https://a.test/x/"]
    assert got["n"] == 1 and got["indexed"] == 1 and got["not_indexed"] == []
    assert why is None


async def test_a_canonical_that_is_itself_not_indexed_is_still_a_problem(monkeypatch):
    _inspector(monkeypatch, {
        "https://a.test/": {"verdict": "NEUTRAL", "userCanonical": "https://a.test/x/"},
        "https://a.test/x/": {"verdict": "NEUTRAL", "coverageState": "Discovered"}})
    got, _ = await gsc._inspect("sc-domain:a.test", {}, ["https://a.test/"])
    assert [b["url"] for b in got["not_indexed"]] == ["https://a.test/x/"]


async def test_an_unreadable_canonical_is_unread_never_fine(monkeypatch):
    _inspector(monkeypatch, {
        "https://a.test/": {"verdict": "NEUTRAL", "userCanonical": "https://a.test/x/"},
        "https://a.test/x/": None,
        "https://a.test/y/": {"verdict": "PASS"}})
    got, why = await gsc._inspect("sc-domain:a.test", {},
                                  ["https://a.test/", "https://a.test/y/"])
    assert got["n"] == 1 and got["unread"] == 1 and why is not None
