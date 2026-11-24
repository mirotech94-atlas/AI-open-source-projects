"""agency — prospecting, audits, lead scoring (AGENCY.md 2.1-2.3).

Three things are load-bearing:

* No scraping. `search_places` calls the Places API (New) and turns any
  `post_json` failure into `([], reason)`, never a guess.
* A missing field from Places (`rating`, `reviews`) stays `None`. Defaulting
  it to 0 would read as "one star" or "closed" to whatever scores it next.
* The eight audit checks are pure where they can be (`audit_html`) so they
  are testable without a socket, and the two that need one more request
  (dead links, PageSpeed) live only in `audit_site`.
"""
from __future__ import annotations

import json

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import denial_reason
from atlas.modules import agency
from atlas.modules.agency import _store, prospect, score
from atlas.modules.agency import audit as agency_audit


def _async_returns(value):
    async def fake(*args, **kwargs):
        return value
    return fake


# ------------------------------------------------------------- audit_html

CLEAN_HTML = """<!doctype html><html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="application/ld+json">{"@context": "https://schema.org"}</script>
</head><body>
<footer>&copy; 2026 Example Co.</footer>
</body></html>"""

BAD_HTML = """<html><head></head><body>
<marquee>Welcome to our site!</marquee>
<table width="100%"><tr><td>everything lives in here</td></tr></table>
<footer>Copyright 2015 Old Biz</footer>
</body></html>"""


def test_audit_html_flags_every_derivable_check_on_a_bad_page():
    findings = agency_audit.audit_html(
        BAD_HTML, "http://old-biz.example", elapsed_s=6.0, size_bytes=100,
        https=False)
    checks = {f.check for f in findings}
    assert checks == {"no_https", "not_mobile", "slow", "no_schema",
                      "stale_copyright", "ancient_markup"}
    stale, = [f for f in findings if f.check == "stale_copyright"]
    assert "2015" in stale.detail


def test_audit_html_is_silent_on_a_clean_fast_page():
    findings = agency_audit.audit_html(
        CLEAN_HTML, "https://good-biz.example", elapsed_s=0.4, size_bytes=4000,
        https=True)
    assert findings == []


def test_audit_html_never_calls_the_network():
    """Pure means pure: nothing in the module the brief names as pure may
    import httpx at call time. Checked on the source, not just behaviour,
    so a future edit that sneaks in a `_fetch` call fails loudly."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(agency_audit))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "audit_html":
            calls = {getattr(n.func, "id", "") for n in ast.walk(node)
                     if isinstance(n, ast.Call)}
            assert "_fetch" not in calls
            assert "_dead_links" not in calls


# -------------------------------------------------------------- audit_site

async def test_audit_site_with_no_url_is_only_no_website():
    result = await agency_audit.audit_site(None)
    assert result.url is None
    assert len(result.findings) == 1
    assert result.findings[0].check == "no_website"
    assert result.findings[0].severity == 3
    assert result.severity == 3


async def test_audit_site_falls_back_to_http_when_https_fails(monkeypatch):
    async def fake_fetch(url):
        if url.startswith("https://"):
            return None, 0.1, 0, True, "connection refused"
        return CLEAN_HTML, 0.3, 200, False, None
    monkeypatch.setattr(agency_audit, "_fetch", fake_fetch)
    monkeypatch.setattr(agency_audit, "_dead_links", _async_returns([]))
    result = await agency_audit.audit_site("https://flaky.example")
    assert any(f.check == "no_https" for f in result.findings)
    assert result.measured["fetched_via"] == "http://flaky.example"


async def test_audit_site_reports_unreachable_when_nothing_loads(monkeypatch):
    monkeypatch.setattr(agency_audit, "_fetch",
                        _async_returns((None, 1.0, 0, True, "timed out")))
    result = await agency_audit.audit_site("https://gone.example")
    assert result.findings[0].check == "unreachable"
    assert result.severity == 3


async def test_audit_site_counts_dead_links_from_the_probe(monkeypatch):
    monkeypatch.setattr(agency_audit, "_fetch",
                        _async_returns((CLEAN_HTML, 0.3, 200, True, None)))
    monkeypatch.setattr(agency_audit, "_dead_links",
                        _async_returns(["https://good-biz.example/old-page"]))
    result = await agency_audit.audit_site("https://good-biz.example")
    dead, = [f for f in result.findings if f.check == "dead_links"]
    assert "old-page" in dead.detail
    assert result.measured["dead_links"] == ["https://good-biz.example/old-page"]


async def test_audit_site_prefers_pagespeed_when_the_key_is_set(monkeypatch):
    monkeypatch.setenv("GOOGLE_PAGESPEED_API_KEY", "k")
    monkeypatch.setattr(agency_audit, "_fetch",
                        _async_returns((CLEAN_HTML, 0.3, 200, True, None)))
    monkeypatch.setattr(agency_audit, "_dead_links", _async_returns([]))
    monkeypatch.setattr(agency_audit, "get_json",
                        _async_returns(({"lighthouseResult": {"categories": {
                            "performance": {"score": 0.2}}}}, None)))
    result = await agency_audit.audit_site("https://good-biz.example")
    slow, = [f for f in result.findings if f.check == "slow"]
    assert "PageSpeed" in slow.detail
    assert result.measured["slow_source"] == "pagespeed"
    assert result.measured["pagespeed_score"] == 20.0


async def test_audit_site_falls_back_to_the_heuristic_when_pagespeed_fails(monkeypatch):
    monkeypatch.setenv("GOOGLE_PAGESPEED_API_KEY", "k")
    monkeypatch.setattr(agency_audit, "_fetch",
                        _async_returns((CLEAN_HTML, 6.0, 200, True, None)))
    monkeypatch.setattr(agency_audit, "_dead_links", _async_returns([]))
    monkeypatch.setattr(agency_audit, "get_json",
                        _async_returns((None, "rate limited")))
    result = await agency_audit.audit_site("https://good-biz.example")
    assert result.measured["slow_source"] == "heuristic (pagespeed unavailable)"
    assert any(f.check == "slow" for f in result.findings)


# ------------------------------------------------------------- audit cache

async def test_recent_audit_excludes_withdrawn_rows(memory_backend):
    """A rollback sets severity to -1 rather than deleting the row (history
    stays), so the *query* has to be the thing that makes the withdrawal
    stick — otherwise `agency.audit` would keep handing back a withdrawn
    result, or feed severity -1 into `agency.rank`, for up to 30 days."""
    memory_backend.responses["FROM agency_audits"] = None
    await _store.recent_audit(5)
    sql, args = memory_backend.sql_log[-1]
    assert "severity >= 0" in sql
    assert "interval '30 days'" in sql
    assert args == (5,)


async def test_insert_lead_is_a_no_op_on_a_duplicate_place_id(memory_backend):
    memory_backend.responses["INSERT INTO agency_leads"] = None
    place = prospect.Place("p1", "Joe's", "plumber", None, None, None,
                          None, None, None, None)
    lead_id = await _store.insert_lead(place)
    assert lead_id is None
    sql, args = memory_backend.sql_log[-1]
    assert "ON CONFLICT (place_id) DO NOTHING" in sql
    assert args[0] == "p1"


async def test_mark_dropped_is_a_single_statement_over_any(memory_backend):
    await _store.mark_dropped([3, 4])
    sql, args = memory_backend.sql_log[-1]
    assert "= ANY($1)" in sql
    assert args == ([3, 4],)


async def test_mark_dropped_with_no_ids_touches_the_database_not_at_all(memory_backend):
    await _store.mark_dropped([])
    assert memory_backend.sql_log == []


async def test_audit_lead_uses_the_30_day_cache_without_refetching(monkeypatch):
    cached_row = {
        "id": 7, "url": "https://joes.example",
        "findings": [{"check": "no_https", "severity": 2, "detail": "plain http"}],
        "measured": {"elapsed_s": 1.0}, "severity": 2,
    }
    monkeypatch.setattr(_store, "recent_audit", _async_returns(cached_row))

    async def fail_if_called(url):
        raise AssertionError("audit_lead must read the cache, not re-fetch")
    monkeypatch.setattr(agency_audit, "audit_site", fail_if_called)

    result = await agency_audit.audit_lead(5, "https://joes.example")
    assert result.severity == 2
    assert result.findings[0].check == "no_https"


async def test_audit_lead_fetches_fresh_when_nothing_is_cached(monkeypatch):
    monkeypatch.setattr(_store, "recent_audit", _async_returns(None))
    inserted = {}

    async def fake_insert_audit(lead_id, url, findings, severity, measured):
        inserted["lead_id"] = lead_id
        return 99
    monkeypatch.setattr(_store, "insert_audit", fake_insert_audit)
    monkeypatch.setattr(agency_audit, "audit_site",
                        _async_returns(agency_audit.Audit(None, [
                            agency_audit.Finding("no_website", 3, "none")], {}, 3)))

    result = await agency_audit.audit_lead(5, None)
    assert result.severity == 3
    assert inserted["lead_id"] == 5


async def test_audit_action_returns_the_audit_id_as_the_rollback_token(monkeypatch):
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": 5, "website": None}))
    monkeypatch.setattr(_store, "recent_audit", _async_returns(None))
    monkeypatch.setattr(_store, "insert_audit", _async_returns(42))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))

    result = await agency_audit.audit_action(5)
    assert result.ok
    assert result.rollback_token == "42"


async def test_audit_action_on_a_cache_hit_gives_no_rollback_token(monkeypatch):
    """The bug this guards: `audit_action` used to hand back the CACHED
    audit's id as the rollback token, so rolling back a cache-hit execution
    withdrew a row some earlier, unrelated run had written."""
    cached_row = {"id": 7, "at": "2026-08-01T00:00:00+00:00",
                 "url": "https://joes.example", "findings": [], "measured": {},
                 "severity": 2}
    monkeypatch.setattr(_store, "get_lead",
                        _async_returns({"id": 5, "website": "https://joes.example"}))
    monkeypatch.setattr(_store, "recent_audit", _async_returns(cached_row))

    async def fail_if_called(*a, **kw):
        raise AssertionError("a cache hit must not write a new audit row")
    monkeypatch.setattr(_store, "insert_audit", fail_if_called)
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))

    result = await agency_audit.audit_action(5)
    assert result.ok
    assert result.rollback_token == ""
    assert "served from cache" in result.detail
    assert "audit #7" in result.detail
    assert "nothing to undo" in result.detail


async def test_audit_rollback_withdraws_by_setting_severity_negative(monkeypatch):
    calls = []

    async def fake_withdraw(audit_id):
        calls.append(audit_id)
    monkeypatch.setattr(_store, "withdraw_audit", fake_withdraw)
    result = await agency_audit.audit_rollback("42")
    assert result.ok
    assert calls == [42]


async def test_audit_rollback_with_an_empty_token_never_withdraws_anything(monkeypatch):
    """The other half of the cache-hit fix: `audit_action` now hands back an
    empty token on a cache hit, and this is what the executor calls with it —
    it must be a true no-op, not a `withdraw_audit(None)` or a crash."""
    async def fail_if_called(audit_id):
        raise AssertionError("nothing was written; nothing should be withdrawn")
    monkeypatch.setattr(_store, "withdraw_audit", fail_if_called)

    result = await agency_audit.audit_rollback("")
    assert result.ok
    assert "nothing was written" in result.detail


async def test_audit_action_on_a_missing_lead_fails_cleanly(monkeypatch):
    monkeypatch.setattr(_store, "get_lead", _async_returns(None))
    result = await agency_audit.audit_action(999)
    assert not result.ok


# ------------------------------------------------------------- prospecting

async def test_check_prospect_budget_is_unknown_at_the_cap(monkeypatch):
    monkeypatch.setattr(prospect, "spend_this_month", _async_returns(500))
    monkeypatch.setattr(prospect, "cap", lambda: 500)
    obs = await prospect.check_prospect_budget()
    assert obs.health is Health.UNKNOWN
    assert obs.value == 500


async def test_check_prospect_budget_is_unknown_when_the_spend_cannot_be_read(
        monkeypatch):
    """Found on a bare box (Task 17): with no DATABASE_URL the check raised
    KeyError instead of saying what it could not read. The scheduler turns
    a raise into UNKNOWN too, but a check answers for itself."""
    async def unreadable():
        raise KeyError("DATABASE_URL")

    monkeypatch.setattr(prospect, "spend_this_month", unreadable)
    monkeypatch.setattr(prospect, "cap", lambda: 500)
    obs = await prospect.check_prospect_budget()
    assert obs.health is Health.UNKNOWN
    assert obs.value is None
    assert "DATABASE_URL" in obs.detail


async def test_check_prospect_budget_is_ok_below_the_cap(monkeypatch):
    monkeypatch.setattr(prospect, "spend_this_month", _async_returns(10))
    monkeypatch.setattr(prospect, "cap", lambda: 500)
    obs = await prospect.check_prospect_budget()
    assert obs.health is Health.OK
    assert obs.value == 10


def test_cap_defaults_to_500(monkeypatch):
    monkeypatch.delenv("AGENCY_PLACES_MONTHLY_CAP", raising=False)
    assert prospect.cap() == 500


def test_cap_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("AGENCY_PLACES_MONTHLY_CAP", "50")
    assert prospect.cap() == 50


async def test_search_places_turns_a_post_json_failure_into_a_reason(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(prospect, "post_json", _async_returns((None, "rate limited")))
    places, reason = await prospect.search_places(
        "plumber", lat=1.0, lng=2.0, radius_m=500)
    assert places == []
    assert reason == "rate limited"


async def test_search_places_without_a_key_does_not_call_the_network(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)

    async def fail_if_called(*a, **kw):
        raise AssertionError("must not call the API with no key")
    monkeypatch.setattr(prospect, "post_json", fail_if_called)
    places, reason = await prospect.search_places(
        "plumber", lat=1.0, lng=2.0, radius_m=500)
    assert places == []
    assert "GOOGLE_PLACES_API_KEY" in reason


async def test_search_places_never_invents_a_rating_or_review_count(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(prospect, "post_json", _async_returns(({
        "places": [{"id": "p1", "displayName": {"text": "Joe's Plumbing"},
                    "primaryType": "plumber",
                    "formattedAddress": "1 Main St"}],
    }, None)))
    places, reason = await prospect.search_places(
        "plumber", lat=1.0, lng=2.0, radius_m=500)
    assert reason is None
    assert places[0].rating is None
    assert places[0].reviews is None
    assert places[0].name == "Joe's Plumbing"


async def test_search_places_skips_entries_with_no_place_id(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(prospect, "post_json", _async_returns(({
        "places": [{"displayName": {"text": "No Id LLC"}}],
    }, None)))
    places, reason = await prospect.search_places(
        "plumber", lat=1.0, lng=2.0, radius_m=500)
    assert reason is None
    assert places == []


async def test_prospect_action_refuses_at_the_budget_cap(monkeypatch):
    monkeypatch.setattr(_store, "spend_this_month", _async_returns(500))
    monkeypatch.setattr(prospect, "cap", lambda: 500)

    async def fail_if_called(*a, **kw):
        raise AssertionError("must not spend a request over the cap")
    monkeypatch.setattr(prospect, "post_json", fail_if_called)

    result = await prospect.prospect_action("plumber", lat=1.0, lng=2.0, radius_m=500)
    assert not result.ok


async def test_prospect_action_without_a_key_does_not_charge_the_budget(monkeypatch):
    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.setattr(_store, "spend_this_month", _async_returns(0))

    async def fail_if_called(month, n):
        raise AssertionError("nothing reached Google; nothing should be charged")
    monkeypatch.setattr(_store, "charge", fail_if_called)

    result = await prospect.prospect_action("plumber", lat=1.0, lng=2.0, radius_m=500)
    assert not result.ok
    assert "GOOGLE_PLACES_API_KEY" in result.detail


async def test_prospect_action_inserts_leads_and_charges_one_request(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(_store, "spend_this_month", _async_returns(0))
    charged = []

    async def fake_charge(month, n):
        charged.append((month, n))
    monkeypatch.setattr(_store, "charge", fake_charge)

    inserted = []

    async def fake_insert_lead(place, *, distance_km=None):
        inserted.append(place.place_id)
        return len(inserted)
    monkeypatch.setattr(_store, "insert_lead", fake_insert_lead)
    monkeypatch.setattr(prospect, "post_json", _async_returns(({
        "places": [{"id": "p1", "displayName": {"text": "Joe's"},
                    "primaryType": "plumber"}],
    }, None)))

    result = await prospect.prospect_action("plumber", lat=1.0, lng=2.0, radius_m=500)
    assert result.ok
    assert inserted == ["p1"]
    assert len(charged) == 1 and charged[0][1] == 1
    assert result.rollback_token == "1"


async def test_prospect_action_computes_distance_when_the_place_has_a_location(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(_store, "spend_this_month", _async_returns(0))
    monkeypatch.setattr(_store, "charge", _async_returns(None))
    seen = {}

    async def fake_insert_lead(place, *, distance_km=None):
        seen["distance_km"] = distance_km
        return 1
    monkeypatch.setattr(_store, "insert_lead", fake_insert_lead)
    monkeypatch.setattr(prospect, "post_json", _async_returns(({
        "places": [{"id": "p1", "displayName": {"text": "Joe's"},
                    "location": {"latitude": 40.7128, "longitude": -74.0060}}],
    }, None)))

    await prospect.prospect_action("plumber", lat=40.7128, lng=-74.0060, radius_m=500)
    assert seen["distance_km"] == pytest.approx(0.0, abs=1e-6)


async def test_prospect_action_leaves_distance_none_without_a_location(monkeypatch):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "k")
    monkeypatch.setattr(_store, "spend_this_month", _async_returns(0))
    monkeypatch.setattr(_store, "charge", _async_returns(None))
    seen = {}

    async def fake_insert_lead(place, *, distance_km=None):
        seen["distance_km"] = distance_km
        return 1
    monkeypatch.setattr(_store, "insert_lead", fake_insert_lead)
    monkeypatch.setattr(prospect, "post_json", _async_returns(({
        "places": [{"id": "p1", "displayName": {"text": "Joe's"}}],
    }, None)))

    await prospect.prospect_action("plumber", lat=1.0, lng=2.0, radius_m=500)
    assert seen["distance_km"] is None


async def test_prospect_rollback_marks_the_new_leads_dropped(monkeypatch):
    dropped = []

    async def fake_mark_dropped(ids):
        dropped.extend(ids)
    monkeypatch.setattr(_store, "mark_dropped", fake_mark_dropped)
    result = await prospect.prospect_rollback("3,4")
    assert result.ok
    assert dropped == [3, 4]


async def test_prospect_rollback_with_no_leads_is_a_noop(monkeypatch):
    async def fail_if_called(ids):
        raise AssertionError("nothing to drop")
    monkeypatch.setattr(_store, "mark_dropped", fail_if_called)
    result = await prospect.prospect_rollback("")
    assert result.ok


# ------------------------------------------------------------------ scoring

def test_features_never_invents_rating_or_reviews():
    lead = {"rating": None, "reviews": None, "distance_km": None}
    audit = agency_audit.Audit(None, [], {}, 3)
    f = score.features(lead, audit)
    assert f["has_rating"] == 0.0
    assert f["has_reviews"] == 0.0
    assert f["rating"] == 0.0
    assert f["reviews"] == 0.0
    assert f["no_website"] == 1.0


def test_features_records_real_numbers_when_present():
    lead = {"rating": 4.5, "reviews": 12, "distance_km": 3.2}
    audit = agency_audit.Audit("https://x.example", [], {}, 0)
    f = score.features(lead, audit)
    assert f["has_rating"] == 1.0
    assert f["rating"] == 4.5
    assert f["has_reviews"] == 1.0
    assert f["reviews"] == 12.0
    assert f["no_website"] == 0.0


def test_heuristic_score_is_within_bounds():
    f = {"audit_severity": 3.0, "reviews": 20.0, "has_reviews": 1.0,
        "rating": 4.0, "has_rating": 1.0}
    s = score.heuristic_score(f)
    assert 0.0 <= s <= 1.0


def test_heuristic_score_is_monotonic_in_audit_severity():
    base = {"reviews": 20.0, "has_reviews": 1.0, "rating": 4.0, "has_rating": 1.0}
    low = score.heuristic_score({**base, "audit_severity": 0.0})
    mid = score.heuristic_score({**base, "audit_severity": 5.0})
    high = score.heuristic_score({**base, "audit_severity": 10.0})
    assert low < mid < high


async def test_rank_action_scores_every_non_closed_lead(monkeypatch):
    leads = [
        {"id": 1, "website": None, "rating": 4.5, "reviews": 10, "score": 0.2,
         "features": {"audit_severity": 3.0}},
        {"id": 2, "website": "https://x.example", "rating": None,
         "reviews": None, "score": None, "features": {}},
    ]
    monkeypatch.setattr(_store, "list_non_closed_leads", _async_returns(leads))
    monkeypatch.setattr(_store, "recent_audit", _async_returns(None))
    updated = {}

    async def fake_update_lead_score(lead_id, s, feats):
        updated[lead_id] = s
    monkeypatch.setattr(_store, "update_lead_score", fake_update_lead_score)

    result = await score.rank_action()
    assert result.ok
    assert set(updated) == {1, 2}
    token = json.loads(result.rollback_token)
    assert token == {
        "1": {"score": 0.2, "features": {"audit_severity": 3.0}},
        "2": {"score": None, "features": {}},
    }


async def test_rank_rollback_restores_the_previous_scores_and_features(monkeypatch):
    restored = {}

    async def fake_restore(lead_id, s, feats):
        restored[lead_id] = (s, feats)
    monkeypatch.setattr(_store, "restore_lead_score", fake_restore)

    token = json.dumps({
        "1": {"score": 0.2, "features": {"audit_severity": 3.0}},
        "2": {"score": None, "features": {}},
    })
    result = await score.rank_rollback(token)
    assert result.ok
    assert restored == {
        1: (0.2, {"audit_severity": 3.0}),
        2: (None, {}),
    }


# ------------------------------------------------------------------ module

async def test_leads_waiting_check_reports_unknown_on_failure(monkeypatch):
    async def boom():
        raise RuntimeError("db unreachable")
    monkeypatch.setattr(_store, "count_leads_waiting", boom)
    obs = await agency.check_leads_waiting()
    assert obs.health is Health.UNKNOWN


async def test_leads_waiting_check_reports_the_count(monkeypatch):
    monkeypatch.setattr(_store, "count_leads_waiting", _async_returns(4))
    obs = await agency.check_leads_waiting()
    assert obs.health is Health.OK
    assert obs.value == 4


def test_every_tier_auto_action_has_a_dry_run_and_a_rollback():
    """Task 3 adds `agency.send_outreach` and `agency.deploy_site`, both
    Tier ASK (AGENCY.md §3: a stranger's inbox, a stranger's production
    domain) — so this no longer asserts every action IS Tier AUTO, only
    that every Tier AUTO action carries what invariant 8 requires, and that
    an irreversible action (only ever ASK) has no rollback to promise."""
    for action in agency.MODULE.actions():
        if action.tier is Tier.AUTO:
            assert action.dry_run is not None
            assert action.rollback is not None
            assert not action.irreversible
        else:
            assert action.tier is Tier.ASK
            if action.irreversible:
                assert action.rollback is None


def test_action_names_are_namespaced_and_not_denied():
    for action in agency.MODULE.actions():
        assert action.name.startswith("agency.")
        assert denial_reason(action.name) is None


def test_module_declares_no_required_env():
    assert agency.MODULE.requires_env == ()
    assert agency.MODULE.optional_env == (
        "GOOGLE_PLACES_API_KEY", "GOOGLE_PAGESPEED_API_KEY",
        "VERCEL_TOKEN", "VERCEL_TEAM_ID", "GMAIL_APP_PASSWORD")


def test_checks_have_no_baseline_and_run_daily():
    from datetime import timedelta
    for check in agency.MODULE.checks():
        assert check.baseline_sigma is None
        # The Vercel token check runs every 30 minutes (a card waits on it).
        # The contact delivery check runs every 10 minutes (a late message waits on it).
        want = (timedelta(minutes=30) if check.name == "vercel"
                else timedelta(minutes=10) if check.name == "contact_delivery"
                else timedelta(days=1))
        assert check.interval == want


# ------------------------------------------------ the no-executor-path test

def test_no_file_in_the_module_imports_the_executor():
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent / "atlas"
    pkg = root / "modules" / "agency"

    def _imports_of(path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text())
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level:
                    parts = ["atlas", "modules", "agency"][: 3 - (node.level - 1)]
                    mod = ".".join(parts + ([mod] if mod else []))
                names.add(mod)
        return names

    files = list(pkg.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


@pytest.mark.parametrize("name", ["agency.send_outreach", "agency.send_invite",
                                  "agency.send_agreement"])
def test_every_send_to_a_client_is_a_verbose_card_in_agency_decisions(name):
    """Six agents, 2026-10-27: the three sends are Tier 2 cards, shown in
    full (recipient, subject, body or agreement and price)."""
    action = {a.name: a for a in agency.MODULE.actions()}[name]
    assert action.tier is Tier.ASK and action.card_only and action.verbose_card
    assert action.topic_for is not None and action.topic_for({}) == "agency_decisions"
    assert action.card_detail is not None
    assert action.dry_run is not None and action.rollback is not None
