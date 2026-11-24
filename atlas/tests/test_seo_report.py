"""The weekly per-client SEO report, its email draft, and the Academy gaps.

The report is built in code from what the checks recorded (Search Console,
Core Web Vitals), our rank rows and the fixes applied this week: rank moves,
vitals, fixes done and the next three actions, each figure with its read
time, a missing reading "not measured" never 0. The email is a draft row
only (content_drafts kind seo_report_email); nothing here sends. The Academy
gaps compare Search Console queries and the free search results with the
store and the channel's titles, capped, in code (no model). No network."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from atlas.kernel.contracts import Tier
from atlas.modules import _scrape, seo
from atlas.modules.seo import report, store

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)          # a Monday
JOES = {"slug": "joes", "domain": "joes.example", "path": None, "client": "Joe's Bakery"}


def gsc_site(clicks: int, position: float, read_at: datetime, **kw: Any) -> dict:
    return {"slug": "joes", "domain": "joes.example", "status": "ok",
            "read_at": read_at.isoformat(),
            "range": {"start": "2026-10-03", "end": "2026-10-30"},
            "totals": {"clicks": clicks, "impressions": 2000, "ctr": 0.02,
                       "position": position},
            "top_queries": [{"query": "bakery queens", "clicks": 20,
                             "impressions": 300, "ctr": 0.06, "position": 3.1}],
            "striking": kw.get("striking", [{"query": "sourdough near me", "clicks": 1,
                                             "impressions": 400, "ctr": 0.0025,
                                             "position": 12.4}]),
            "losing": kw.get("losing", []),
            "sitemaps": kw.get("sitemaps", [{"path": "/sitemap.xml", "errors": 0}]),
            "inspected": kw.get("inspected", {"n": 3, "indexed": 3, "not_indexed": []}),
            "unread": [], "issues": []}


class Fake:
    def __init__(self) -> None:
        self.gsc: list[dict] = []
        self.cwv: list[dict] = []
        self.moves: list[dict] = []
        self.fixes: list[dict] = []
        self.reports: dict[int, dict] = {}
        self.drafts: dict[int, dict] = {}
        self.kws: list[str] = []
        self.titles: list[str] = []

    async def site(self, slug):
        return {"joes": JOES, "academy-store": dict(store.ACADEMY)}.get(slug)

    async def recent_evidence(self, check, limit=10):
        return {"search_console": self.gsc, "core_web_vitals": self.cwv}[check]

    async def rank_moves(self, slug, since):
        return self.moves

    async def fixes_done(self, slug, since):
        return self.fixes

    async def insert_report(self, slug, kind, week, body, text):
        rid = len(self.reports) + 1
        self.reports[rid] = {"id": rid, "slug": slug, "kind": kind, "week": week,
                             "body": body, "text": text, "status": "active"}
        return rid

    async def withdraw_report(self, rid):
        self.reports[int(rid)]["status"] = "withdrawn"

    async def latest_report(self, slug, kind):
        rows = [r for r in self.reports.values()
                if r["slug"] == slug and r["kind"] == kind and r["status"] == "active"]
        return rows[-1] if rows else None

    async def insert_email_draft(self, slug, week, title, body):
        did = len(self.drafts) + 1
        self.drafts[did] = {"id": did, "title": title, "body": body, "status": "draft",
                            "source": f"seo-report:{slug}:{week}"}
        return did

    async def drop_email_draft(self, did):
        self.drafts[int(did)]["status"] = "dropped"

    async def keywords(self, slug, limit):
        return self.kws[:limit]

    async def channel_titles(self, limit=200):
        return self.titles


@pytest.fixture
def fake(monkeypatch):
    f = Fake()
    for name in dir(Fake):
        if not name.startswith("_"):
            monkeypatch.setattr(store, name, getattr(f, name))
    return f


async def test_the_report_has_rank_moves_vitals_fixes_and_three_actions(fake):
    fake.gsc = [{"sites": [gsc_site(80, 9.5, NOW - timedelta(hours=3))]},
                {"sites": [gsc_site(60, 12.0, NOW - timedelta(days=7, hours=2))]}]
    fake.cwv = [{"sites": [{"slug": "joes", "read_at": (NOW - timedelta(hours=5))
                            .isoformat(),
                            "mobile": {"score": 41, "lab": {"lcp_ms": 5200, "cls": 0.02}},
                            "desktop": {"score": 90, "lab": {"lcp_ms": 1100, "cls": 0.0}},
                            "regressions": ["mobile score 70 -> 41"],
                            "issues": ["mobile poor: score 41"]}]}]
    fake.moves = [{"keyword": "bakery queens", "position": 4, "prior": 9,
                   "at": NOW - timedelta(days=1)}]
    fake.fixes = [{"what": "title on index.html", "at": NOW - timedelta(days=2)}]
    r = await report.build("joes", NOW)
    assert r["slug"] == "joes" and r["week"] == "2026-W45"
    sc = r["search"]
    assert sc["clicks"] == 80 and sc["clicks_before"] == 60
    assert sc["position"] == 9.5 and sc["position_before"] == 12.0
    assert sc["read_at"] == (NOW - timedelta(hours=3)).isoformat()
    assert r["ranks"][0]["keyword"] == "bakery queens" and r["ranks"][0]["prior"] == 9
    assert r["vitals"]["mobile"]["score"] == 41 and r["vitals"]["read_at"]
    assert r["fixes"][0]["what"] == "title on index.html"
    acts = r["next_actions"]
    assert len(acts) == 3
    assert "mobile" in acts[0].lower()                  # the speed regression first
    assert any("sourdough near me" in a for a in acts)


async def test_without_readings_the_report_says_not_measured_never_zero(fake):
    r = await report.build("joes", NOW)
    assert r["search"] is None and "not measured" in r["search_note"]
    assert r["vitals"] is None and "not measured" in r["vitals_note"]
    text = report.render_text(r, "Joe's Bakery")
    assert "not measured" in text and " 0 clicks" not in text


async def test_the_text_carries_every_figure_with_its_read_time(fake):
    fake.gsc = [{"sites": [gsc_site(80, 9.5, NOW - timedelta(hours=3))]}]
    r = await report.build("joes", NOW)
    text = report.render_text(r, "Joe's Bakery")
    assert "80 clicks" in text and "read 2026-11-02 09:00 UTC" in text
    assert "Joe's Bakery" in text and "<" not in text


async def test_weekly_report_action_stores_and_rolls_back(fake):
    fake.gsc = [{"sites": [gsc_site(80, 9.5, NOW - timedelta(hours=3))]}]
    got = await report.weekly_report("joes")
    assert got.ok and got.rollback_token
    (row,) = fake.reports.values()
    assert row["kind"] == "client_weekly" and row["text"]
    assert (await report.withdraw_report(got.rollback_token)).ok
    assert row["status"] == "withdrawn"


async def test_the_email_is_a_draft_row_only_and_withdrawable(fake):
    fake.gsc = [{"sites": [gsc_site(80, 9.5, NOW - timedelta(hours=3))]}]
    await report.weekly_report("joes")
    dry = await report.draft_report_email_dry("joes")
    assert dry.ok and "draft" in dry.detail and "send" not in dry.detail.replace(
        "never sends", "")
    got = await report.draft_report_email("joes")
    assert got.ok
    (d,) = fake.drafts.values()
    assert d["status"] == "draft" and "Joe's Bakery" in d["body"]
    assert (await report.drop_report_email(got.rollback_token)).ok
    assert d["status"] == "dropped"


async def test_no_report_no_email(fake):
    got = await report.draft_report_email("joes")
    assert not got.ok and "seo.weekly_report" in got.detail


async def test_our_own_stores_get_no_client_email(fake):
    got = await report.draft_report_email("academy-store")
    assert not got.ok


async def test_keyword_gaps_compare_search_results_with_the_store_and_titles(
        fake, monkeypatch):
    fake.gsc = [{"sites": [{**gsc_site(10, 14.0, NOW), "slug": "academy-store",
                            "domain": "agency.example.com",
                            "top_queries": [{"query": "n8n tutorial", "clicks": 3,
                                             "impressions": 100, "ctr": 0.03,
                                             "position": 14.0}],
                            "striking": [{"query": "ai automation course",
                                          "clicks": 0, "impressions": 80, "ctr": 0.0,
                                          "position": 11.0}]}]}]
    fake.kws = ["make money with ai"]
    fake.titles = ["N8N Tutorial for Beginners in 10 Minutes"]
    searched: list[str] = []

    async def search(q, *, agent, limit=10):
        searched.append(q)
        hits = [{"url": "https://www.youtube.com/watch?v=x", "title": "t", "snippet": "s"},
                {"url": "https://other.example/", "title": "t", "snippet": "s"}]
        if q == "n8n tutorial":
            hits.insert(0, {"url": "https://agency.example.com/n8n", "title": "t",
                            "snippet": "s"})
        return hits, None

    monkeypatch.setattr(_scrape, "search", search)
    monkeypatch.setenv("BRAVE_API_KEY", "fake")
    got = await report.keyword_gaps()
    assert got.ok
    (row,) = fake.reports.values()
    gaps = {g["keyword"]: g for g in row["body"]["gaps"]}
    assert len(searched) <= report.GAP_QUERIES
    assert gaps["n8n tutorial"]["store_position"] == 1
    assert gaps["n8n tutorial"]["video_gap"] is False      # our title covers it
    assert gaps["ai automation course"]["store_position"] is None
    assert gaps["ai automation course"]["video_gap"] is True
    assert gaps["ai automation course"]["title_idea"]
    assert all(g["read_at"] for g in gaps.values())


async def test_keyword_gaps_without_a_search_provider_are_not_measured(fake,
                                                                       monkeypatch):
    fake.kws = ["make money with ai"]
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    async def search(q, *, agent, limit=10):
        return [], "no free search provider"
    monkeypatch.setattr(_scrape, "search", search)
    got = await report.keyword_gaps()
    (row,) = fake.reports.values()
    (g,) = row["body"]["gaps"]
    assert g["store_position"] is None and "not measured" in g["note"]
    assert got.ok


def test_the_new_actions_are_tier_one_with_dry_runs_and_rollbacks():
    acts = {a.name: a for a in seo.MODULE.actions()}
    for name in ("seo.weekly_report", "seo.draft_report_email", "seo.keyword_gaps"):
        a = acts[name]
        assert a.tier is Tier.AUTO and a.dry_run and a.rollback, name
    assert "send" not in acts["seo.draft_report_email"].summary.lower().replace(
        "never sends", "")


async def test_monday_runs_the_report_and_gaps(monkeypatch):
    ran: list[str] = []

    async def run(action, params, why):
        ran.append(action)
        return "ok"

    async def targets():
        return [JOES, dict(store.ACADEMY)]
    monkeypatch.setattr(seo, "_run", run)
    monkeypatch.setattr(store, "targets", targets)
    await seo.run_daily(NOW)
    assert ran.count("seo.weekly_report") == 1          # client sites only
    assert ran.count("seo.keyword_gaps") == 1
    ran.clear()
    await seo.run_daily(NOW + timedelta(days=1))
    assert "seo.weekly_report" not in ran and "seo.keyword_gaps" not in ran
