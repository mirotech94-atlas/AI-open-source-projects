"""Core Web Vitals from PageSpeed Insights, mobile and desktop, per site.

Lab figures (score, LCP, CLS, TBT) and field figures (CrUX LCP, INP, CLS)
when Google has them; a field figure Google does not have is "not measured"
(None), never 0. Each reading is compared with the last one the check
recorded (its observations are the snapshots, so this works in watch-only):
a clear drop is a regression and DEGRADED. A site PageSpeed cannot read is
UNKNOWN, never OK. The key goes only to Google; no network here."""
from __future__ import annotations

from typing import Any

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import _http
from atlas.modules.seo import store, vitals

SITES = [{"slug": "joes", "domain": "joes.example", "path": None, "client": "Joe's"}]


def psi(score: float, lcp: float, cls: float, tbt: float = 100.0,
        field: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"lighthouseResult": {
        "categories": {"performance": {"score": score}},
        "audits": {"largest-contentful-paint": {"numericValue": lcp},
                   "cumulative-layout-shift": {"numericValue": cls},
                   "total-blocking-time": {"numericValue": tbt},
                   "first-contentful-paint": {"numericValue": 900.0}}}}
    if field:
        out["loadingExperience"] = {"overall_category": "FAST", "metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2100},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 150},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5}}}
    return out


@pytest.fixture
def env(monkeypatch):
    calls: list[dict[str, Any]] = []
    state: dict[str, Any] = {"answer": {"mobile": psi(0.9, 2000, 0.02),
                                        "desktop": psi(0.98, 900, 0.01)},
                             "prior": []}

    async def get_json(url, **kw):
        calls.append({"url": url, **kw})
        ans = state["answer"].get(kw["params"]["strategy"])
        if isinstance(ans, str):
            return None, ans
        return ans, None

    async def targets():
        return [dict(s) for s in SITES]

    async def prior(name, limit=10):
        return state["prior"]

    monkeypatch.setattr(_http, "get_json", get_json)
    monkeypatch.setattr(store, "targets", targets)
    monkeypatch.setattr(store, "recent_evidence", prior)
    monkeypatch.setenv("GOOGLE_PAGESPEED_API_KEY", "fake-not-a-secret")
    return state, calls


def test_parse_reads_lab_and_field_figures():
    got = vitals.parse(psi(0.73, 3100.4, 0.123, 250.0))
    assert got["score"] == 73
    assert got["lab"] == {"lcp_ms": 3100, "cls": 0.123, "tbt_ms": 250, "fcp_ms": 900}
    assert got["field"] == {"lcp_ms": 2100, "inp_ms": 150, "cls": 0.05,
                            "category": "FAST"}


def test_parse_without_field_data_says_not_measured_not_zero():
    got = vitals.parse(psi(0.5, 4000, 0.3, field=False))
    assert got["field"] is None


def test_parse_of_a_wrong_shape_is_none():
    assert vitals.parse({"nope": 1}) is None


def test_regressions_need_a_clear_drop_not_noise():
    before = {"score": 90, "lab": {"lcp_ms": 2000, "cls": 0.02, "tbt_ms": 100}}
    assert vitals.regressions(before, {"score": 86, "lab": {"lcp_ms": 2200, "cls": 0.03,
                                                            "tbt_ms": 120}}) == []
    got = vitals.regressions(before, {"score": 70, "lab": {"lcp_ms": 3200, "cls": 0.2,
                                                           "tbt_ms": 100}})
    assert any("score 90 -> 70" in g for g in got)
    assert any("LCP" in g for g in got) and any("CLS" in g for g in got)


async def test_both_strategies_are_read_with_the_key_and_a_long_timeout(env):
    _, calls = env
    obs = await vitals.check()
    assert obs.health is Health.OK, obs.detail
    assert sorted(c["params"]["strategy"] for c in calls) == ["desktop", "mobile"]
    assert all(c["params"]["key"] == "fake-not-a-secret" for c in calls)
    assert all(c["timeout"] >= 60 for c in calls)
    site = obs.evidence["sites"][0]
    assert site["mobile"]["score"] == 90 and site["desktop"]["score"] == 98
    assert site["read_at"] and "(read 20" in obs.detail
    assert "fake-not-a-secret" not in obs.detail


async def test_the_older_key_name_also_works(env, monkeypatch):
    _, calls = env
    monkeypatch.delenv("GOOGLE_PAGESPEED_API_KEY")
    monkeypatch.setenv("PAGESPEED_API_KEY", "other-fake")
    await vitals.check()
    assert all(c["params"]["key"] == "other-fake" for c in calls)


async def test_an_unreadable_site_is_unknown_never_ok(env):
    state, _ = env
    state["answer"]["mobile"] = "HTTP 429 Too Many Requests"
    obs = await vitals.check()
    assert obs.health is Health.UNKNOWN
    assert "joes.example" in obs.detail and "429" in obs.detail


async def test_a_regression_against_the_last_reading_is_degraded(env):
    state, _ = env
    state["prior"] = [{"sites": [{"slug": "joes", "read_at": "2026-10-28T07:00:00+00:00",
                                  "mobile": {"score": 99, "lab": {"lcp_ms": 1200,
                                                                  "cls": 0.0,
                                                                  "tbt_ms": 50}},
                                  "desktop": None}]}]
    obs = await vitals.check()
    assert obs.health is Health.DEGRADED
    site = obs.evidence["sites"][0]
    assert site["regressions"] and "mobile" in site["regressions"][0]
    assert site["compared_with"] == "2026-10-28T07:00:00+00:00"


async def test_a_poor_mobile_score_is_degraded_even_without_history(env):
    state, _ = env
    state["answer"]["mobile"] = psi(0.31, 6000, 0.4)
    obs = await vitals.check()
    assert obs.health is Health.DEGRADED
    assert any("poor" in i for i in obs.evidence["sites"][0]["issues"])
