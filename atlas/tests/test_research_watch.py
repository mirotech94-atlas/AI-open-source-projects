"""Research watches (six agents phase 11): the daily scan.

A change is a change of the page's visible text with dates and times
stripped in code, so a date-only edit is not one; three failed reads in a
row make exactly one remediation task; every change and price row keeps its
source and read time; the paid deep dive raises nothing while
`RESEARCH_PAID_CHECKS` is off; the jobs act only through the executor.
No test here reaches the network."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from atlas.modules import _perplexity, _scrape, research
from atlas.modules.research import watch
from tests.research_fake import install


@pytest.fixture
def fake(monkeypatch):
    f = install(monkeypatch)
    monkeypatch.delenv(research.PAID_ENV, raising=False)
    from atlas.modules.projects import store as pstore
    tasks: list[dict[str, Any]] = []

    async def add_task(**kw):
        tasks.append(kw)
        return len(tasks), f"t{len(tasks)}"

    async def open_remediation_for(action, ref):
        return next((i + 1 for i, t in enumerate(tasks)
                     if t["kind"] == "remediation" and t["source_action"] == action
                     and t["source_ref"] == ref), None)
    monkeypatch.setattr(pstore, "add_task", add_task)
    monkeypatch.setattr(pstore, "open_remediation_for", open_remediation_for)
    f.tasks = tasks
    return f


def _pages(monkeypatch, texts: list[str | None]):
    """Each fetch returns the next text; None is a failed read."""
    queue = list(texts)

    async def fetch(url, *, agent, client=""):
        assert agent == "research"
        text = queue.pop(0)
        if text is None:
            return _scrape.Scraped(url, url, None, "", datetime.now(UTC),
                                   blocked="timed out")
        return _scrape.Scraped(url, url, 200, text, datetime.now(UTC))
    monkeypatch.setattr(_scrape, "fetch", fetch)


# ---------------------------------------------------------------- the diff

def test_a_date_only_change_is_not_a_change():
    a = "Plans updated 2026-10-27 10:31. Starter $49/mo. Last updated: Oct 27, 2026"
    b = "Plans updated 2026-10-28 09:02. Starter $49/mo. Last updated: October 28, 2026"
    c = "Plans updated 2026-10-28 09:02. Starter $59/mo. Last updated: October 28, 2026"
    assert watch.text_sha(a) == watch.text_sha(b)
    assert watch.text_sha(a) != watch.text_sha(c)
    assert watch.text_sha("Updated 10/27/2026 at 9:15 pm") == watch.text_sha(
        "Updated 11/02/2026 at 11:40 am")


async def test_the_scan_stores_a_change_with_its_source_and_time(fake, monkeypatch):
    await fake.add_watch("https://rival.example/pricing", "Rival pricing", None)
    _pages(monkeypatch, ["Starter $49/mo. Updated 2026-10-27",
                         "Starter $49/mo. Updated 2026-10-28",
                         "Starter $59/mo. Updated 2026-10-29"])
    first = await watch.daily_scan()
    assert first.ok and not fake.changes          # the first read is the baseline
    assert [p["amount"] for p in fake.prices_.values()] == ["49.00"]
    await watch.daily_scan()
    assert not fake.changes                       # a date-only edit
    third = await watch.daily_scan()
    (change,) = fake.changes.values()
    assert change["source_url"] == "https://rival.example/pricing"
    assert isinstance(change["fetched_at"], datetime) and "59" in change["excerpt"]
    assert "(read 20" in third.detail
    assert all(p["source_url"] and p["fetched_at"] for p in fake.prices_.values())
    undo = await watch.withdraw_scan(third.rollback_token)
    assert undo.ok and change["status"] == "withdrawn"


async def test_three_failures_make_exactly_one_remediation_task(fake, monkeypatch):
    await fake.add_watch("https://rival.example/", "Rival", None)
    _pages(monkeypatch, [None, None, None, None, None])
    for _ in range(5):
        await watch.daily_scan()
    rem = [t for t in fake.tasks if t["kind"] == "remediation"]
    assert len(rem) == 1 and "research_watches:1" == rem[0]["source_ref"]
    assert fake.watch[1]["fail_streak"] == 5


async def test_a_good_read_resets_the_streak(fake, monkeypatch):
    await fake.add_watch("https://rival.example/", "Rival", None)
    _pages(monkeypatch, [None, None, "ok text", None, None])
    for _ in range(5):
        await watch.daily_scan()
    assert not [t for t in fake.tasks if t["kind"] == "remediation"]


async def test_add_watch_refuses_denied_hosts_and_rolls_back(fake):
    assert not (await watch.add_watch("https://www.reddit.com/r/x")).ok
    assert not (await watch.add_watch("ftp://rival.example/")).ok
    got = await watch.add_watch("https://rival.example/pricing", "Rival")
    assert got.ok
    assert (await watch.archive_watch(got.rollback_token)).ok
    assert fake.watch[1]["status"] == "archived"


# ---------------------------------------------------------------- reports

async def test_the_weekly_report_is_stamped_and_withdrawable(fake, monkeypatch):
    await fake.add_watch("https://rival.example/pricing", "Rival pricing", None)
    _pages(monkeypatch, ["Starter $49/mo", "Starter $59/mo"])
    await watch.daily_scan()
    await watch.daily_scan()
    got = await research.weekly_report()
    assert got.ok
    (rep,) = fake.reports.values()
    assert rep["kind"] == "research_weekly" and "(read 20" in rep["body"]
    assert "https://rival.example/pricing" in rep["body"]
    assert (await research.withdraw_weekly(got.rollback_token)).ok
    assert rep["kind"] == "withdrawn"


# --------------------------------------------------------------- paid check

async def test_the_deep_dive_off_raises_nothing_and_calls_nothing(fake, monkeypatch):
    calls: list[str] = []

    async def call(*a, **kw):
        calls.append("call")
        return None, "no"
    monkeypatch.setattr(_perplexity, "call", call)
    from atlas.kernel import approvals
    proposed: list[str] = []

    async def propose(*a, **kw):
        proposed.append(a[0])
        return 1
    monkeypatch.setattr(approvals, "propose", propose)
    assert not (await research.offer_deep_dive("AI course pricing")).ok
    assert not (await research.deep_dive_dry("AI course pricing")).ok
    assert not (await research.deep_dive("AI course pricing")).ok
    assert calls == [] and proposed == [] and fake.cards == []


async def test_the_deep_dive_on_names_its_call(fake, monkeypatch):
    monkeypatch.setenv(research.PAID_ENV, "on")
    got = await research.offer_deep_dive("AI course pricing")
    assert got.ok
    (card,) = fake.cards
    assert card[0] == "research.deep_dive" and "1 paid Perplexity call" in card[2]


# ------------------------------------------------------------------ wiring

def test_research_sits_in_knowledge_with_its_jobs_and_owner():
    from atlas.kernel import departments, missions, scheduler
    dept = {d.key: d for d in departments.DEPARTMENTS}["knowledge"]
    assert "research" in dept.agents and "research" in dept.modules
    assert {"research_scan", "research_weekly"} <= set(dept.jobs)
    assert {"research_scan", "research_weekly"} <= set(departments.JOBS)
    for ask in ("what are our competitors charging", "build a pricing table",
                "any market trends in ai courses"):
        assert missions.owner_for(ask).agent == "research", ask
    for name in ("research_scan", "research_weekly"):
        assert name in scheduler.POLICIES and name in scheduler._DETACHED
    scan = scheduler._research_scan_windows(datetime(2026, 11, 3, 15, tzinfo=UTC),
                                            scheduler.POLICIES["research_scan"].horizon)
    assert scan[-1].scheduled_for.hour == 6
    weekly = scheduler._research_weekly_windows(
        datetime(2026, 11, 3, 15, tzinfo=UTC),
        scheduler.POLICIES["research_weekly"].horizon)
    at = weekly[-1].scheduled_for
    assert at.hour == 18 and at.weekday() == 6 and str(at.tzinfo) == "America/New_York"


async def test_the_jobs_act_only_through_the_executor(fake, monkeypatch):
    from atlas.kernel import executor
    from atlas.kernel.contracts import ActionResult
    seen: list[str] = []

    async def execute(req):
        seen.append(req.action)
        return ActionResult(True, "ok")
    monkeypatch.setattr(executor, "execute", execute)
    await fake.add_competitor("mapfront", "Rival", "https://rival.example/", None, None)
    assert await research.run_scan()
    assert await research.run_weekly()
    assert seen == ["research.daily_scan", "research.refresh_profile",
                    "research.weekly_report"]


def test_the_roster_reads_perplexity_only_with_the_switch(monkeypatch):
    from atlas.kernel.agents import PERPLEXITY_READS, ROSTER, build_roster
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all
    register_all()
    monkeypatch.delenv(research.PAID_ENV, raising=False)
    build_roster()
    agent = ROSTER["research"]
    assert not (agent.tools & PERPLEXITY_READS)
    assert all(TOOLS[t].action is None for t in agent.tools if t in TOOLS)
    monkeypatch.setenv(research.PAID_ENV, "on")
    build_roster()
    assert PERPLEXITY_READS <= ROSTER["research"].tools
    monkeypatch.delenv(research.PAID_ENV)
    build_roster()
