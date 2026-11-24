"""gitlab.open_issue runs by itself since 2026-10-12 (memory approval-policy),
behind the gates in its dry run: business instance only, the switch, no
near-duplicate open or recently closed, a daily cap per project."""
from __future__ import annotations

import pytest

from atlas.modules import gitlab


@pytest.fixture
def api(monkeypatch):
    calls: list[tuple[str, dict]] = []
    issues = {"opened": [], "closed": [], "today": []}

    async def fake(inst, path, **params):
        calls.append((path, params))
        if path.endswith("/issues"):
            if "created_after" in params:
                return issues["today"]
            return issues[params["state"]]
        return {"id": 1}                      # the project exists
    monkeypatch.setattr(gitlab, "_api", fake)
    return calls, issues


async def test_a_fresh_issue_on_the_business_instance_passes(api):
    calls, _ = api
    got = await gitlab.open_issue_dry("business", "root/atlas",
                                      "FlyCheap Stripe key missing")
    assert got.ok, got.detail
    assert all(p.get("labels") == "atlas" for path, p in calls
               if path.endswith("/issues"))


async def test_the_work_instance_is_never_filed_on(api):
    calls, _ = api
    got = await gitlab.open_issue_dry("work", "team/app", "x")
    assert not got.ok and "colleagues" in got.detail
    assert calls == []                         # not even a read


async def test_a_reworded_duplicate_of_an_open_issue_is_refused(api):
    _, issues = api
    issues["opened"] = [{"iid": 12, "title": "Missing Stripe key for FlyCheap",
                         "state": "opened"}]
    got = await gitlab.open_issue_dry("business", "root/atlas",
                                      "FlyCheap: Stripe key missing")
    assert not got.ok and "#12" in got.detail
    assert got.evidence == {"duplicate_of": 12}


async def test_one_the_rollback_closed_is_not_filed_again(api):
    _, issues = api
    issues["closed"] = [{"iid": 13, "title": "Guardian sensor offline on home",
                         "state": "closed"}]
    got = await gitlab.open_issue_dry("business", "root/atlas",
                                      "Guardian sensor offline (home)")
    assert not got.ok and "#13" in got.detail


async def test_the_daily_cap_per_project(api, monkeypatch):
    _, issues = api
    monkeypatch.setenv("GITLAB_ISSUES_PER_DAY", "2")
    issues["today"] = [{"iid": 1, "title": "a b c"}, {"iid": 2, "title": "d e f"}]
    got = await gitlab.open_issue_dry("business", "root/atlas",
                                      "Runner offline on business instance")
    assert not got.ok and "GITLAB_ISSUES_PER_DAY=2" in got.detail


async def test_the_switch_off_files_nothing(api, monkeypatch):
    monkeypatch.setenv("GITLAB_AUTOFILE", "off")
    got = await gitlab.open_issue_dry("business", "root/atlas", "x y z")
    assert not got.ok and "GITLAB_AUTOFILE" in got.detail


@pytest.mark.parametrize("status", [None, 503, 401])
async def test_gitlab_unreachable_is_a_refusal_never_a_raise(monkeypatch, status):
    import httpx
    request = httpx.Request("GET", "https://aicode.example/api/v4/projects/x")

    async def broken(inst, path, **params):
        if status is None:
            raise httpx.ConnectError("refused", request=request)
        raise httpx.HTTPStatusError(
            "bad", request=request, response=httpx.Response(status, request=request))
    monkeypatch.setattr(gitlab, "_api", broken)
    got = await gitlab.open_issue_dry("business", "root/atlas", "Runner offline")
    assert not got.ok and "nothing is filed" in got.detail


async def test_a_missing_project_is_still_named(monkeypatch):
    import httpx
    request = httpx.Request("GET", "https://aicode.example/api/v4/projects/x")

    async def missing(inst, path, **params):
        raise httpx.HTTPStatusError(
            "404", request=request, response=httpx.Response(404, request=request))
    monkeypatch.setattr(gitlab, "_api", missing)
    got = await gitlab.open_issue_dry("business", "the owner/ai", "x y z")
    assert not got.ok and "no project 'the owner/ai'" in got.detail


@pytest.mark.parametrize(("a", "b", "same"), [
    ("FlyCheap Stripe key missing", "Missing Stripe key for FlyCheap", True),
    ("Runner offline", "Runner offline", True),
    ("Guardian sensor offline", "FlyCheap Stripe key missing", False),
    ("Stripe key missing", "Stripe webhook failing for FlyCheap payouts", False),
])
def test_same_issue(a, b, same):
    assert gitlab.same_issue(a, b) is same
