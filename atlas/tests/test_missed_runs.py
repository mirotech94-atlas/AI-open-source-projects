"""Missed-run policy (RA-F2): skip-stale, run-once-on-recovery, bounded
catch-up.

After an outage a recurring job must neither replay a missed week of posts
or reminders nor silently lose the window it still owes. Each job declares
its policy in `scheduler.POLICIES`; each window it gives up on is recorded
'skipped' with the reason.

One exception, by design: the futures module's three jobs -- `futures_tick`,
`futures_daily`, `futures_weekly` (atlas/modules/futures/job.py) -- run on
the module's own clock, a loop started from `MODULE.setup()` through
`kernel.background`, and declare their policies in `futures.job.POLICIES`
rather than `scheduler.POLICIES`. They still go through `missed.due` and
`occurrence.claim_once`, so their windows are recorded in `schedule_runs`
like everyone else's: `futures_tick` skip-stale with a 12-minute grace (a
late tick is skipped; the next one reads the same bars), `futures_daily`
skip-stale with a 6-hour grace (yesterday's close summary is noise today),
`futures_weekly` run-once (one scoreboard on recovery, never one per missed
week). Covered by tests/test_futures_backtest.py (windows and policies) and
tests/test_futures_pg.py (a window claimed once, a late one skipped).
"""
from __future__ import annotations

import inspect
import re
from datetime import UTC, date, datetime, timedelta

import pytest

from atlas.kernel import missed, occurrence, scheduler
from atlas.kernel.missed import JobPolicy, Missed, Window

TZ = UTC


def at(y, m, d, h=0, mi=0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=TZ)


class FakeRuns:
    """schedule_runs in memory, with the same once-only rules as the SQL
    (the SQL itself is proven in test_missed_runs_pg.py)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self.reads = 0
        self.unreadable = False

    def install(self, monkeypatch) -> FakeRuns:
        missed.forget()
        for name in ("claim", "claim_once", "done", "release", "fail", "skip",
                     "history"):
            monkeypatch.setattr(occurrence, name, getattr(self, name))
        return self

    def status(self, job: str) -> dict[str, str]:
        return {w: r["status"] for (j, w), r in sorted(self.rows.items()) if j == job}

    async def claim(self, job, window, scheduled_for=None):
        row = self.rows.get((job, window))
        if row and row["status"] != "failed":
            return False
        self.rows[(job, window)] = {"status": "running", "claimed_at": datetime.now(UTC)}
        return True

    async def claim_once(self, job, window, scheduled_for=None):
        if (job, window) in self.rows:
            return False
        self.rows[(job, window)] = {"status": "running", "claimed_at": datetime.now(UTC)}
        return True

    async def done(self, job, window):
        self.rows[(job, window)]["status"] = "done"

    async def release(self, job, window):
        self.rows[(job, window)]["status"] = "failed"

    async def fail(self, job, window, detail=""):
        self.rows[(job, window)]["status"] = "failed"

    async def skip(self, job, window, scheduled_for, reason):
        row = self.rows.get((job, window))
        if row is None or row["status"] == "failed":
            self.rows[(job, window)] = {"status": "skipped", "claimed_at": None,
                                        "reason": reason}
        return True

    async def history(self, job, windows):
        self.reads += 1
        if self.unreadable:
            return None
        rows = {w: dict(r) for (j, w), r in self.rows.items()
                if j == job and w in windows}
        return rows, any(j == job for j, _ in self.rows)

    def seed(self, job: str, window: str, status: str = "done") -> None:
        self.rows[(job, window)] = {"status": status, "claimed_at": at(2020, 1, 1)}


@pytest.fixture
def runs(monkeypatch) -> FakeRuns:
    # The daily brief is off by default since 2026-10-06 (weekly only); these
    # tests are about its missed-run policy, so it is switched on here.
    monkeypatch.setenv("BRIEF_DAILY", "on")
    return FakeRuns().install(monkeypatch)


async def test_the_daily_brief_is_off_unless_asked_for(monkeypatch):
    """Miro, 2026-10-06: weekly only. With BRIEF_DAILY unset nothing is
    claimed or sent, whatever the hour."""
    monkeypatch.delenv("BRIEF_DAILY", raising=False)

    async def never(*_a, **_k):
        raise AssertionError("the daily brief must not run")
    monkeypatch.setattr(missed, "due", never)
    monkeypatch.setattr(scheduler, "_last_brief_date", None)
    await scheduler._maybe_brief()


def _daily(policy: Missed, **kw) -> JobPolicy:
    return JobPolicy("job", policy, why="t", windows=missed.daily(lambda: 7), **kw)


def _keys(ws) -> list[str]:
    return [w.key if isinstance(w, Window) else w[0].key for w in ws]


# ------------------------------------------------------------ the planner

def test_skip_stale_runs_only_todays_window_and_skips_the_rest():
    p = _daily(Missed.SKIP_STALE)
    now = at(2026, 9, 28, 10)
    cands = p.windows(now, timedelta(days=3))
    run, skip = missed.plan(p, cands, {w.key for w in cands}, now)
    assert _keys(run) == ["2026-09-28"]
    assert _keys(skip) == ["2026-09-25", "2026-09-26", "2026-09-27"]
    assert all("past its grace" in why for _, why in skip)


def test_skip_stale_never_sends_yesterdays_window_after_midnight():
    p = _daily(Missed.SKIP_STALE)
    now = at(2026, 9, 29, 1)                 # before today's 07:00
    cands = p.windows(now, timedelta(days=1))
    assert cands[-1].key == "2026-09-28"
    run, skip = missed.plan(p, cands, {"2026-09-28"}, now)
    assert run == [] and _keys(skip) == ["2026-09-28"]


def test_skip_stale_with_an_explicit_grace():
    p = _daily(Missed.SKIP_STALE, grace=timedelta(hours=2))
    cands = p.windows(at(2026, 9, 28, 10), timedelta(days=0))
    assert missed.plan(p, cands, {"2026-09-28"}, at(2026, 9, 28, 8, 59))[0]
    assert not missed.plan(p, cands, {"2026-09-28"}, at(2026, 9, 28, 9, 1))[0]


def test_run_once_collapses_missed_weeks_into_one_run():
    p = JobPolicy("w", Missed.RUN_ONCE, why="t", horizon=timedelta(weeks=8),
                  windows=missed.weekly(lambda: 0, lambda: 7, key=missed.iso_week))
    now = at(2026, 9, 30, 12)                # a Wednesday
    cands = p.windows(now, p.horizon)
    run, skip = missed.plan(p, cands, {w.key for w in cands[-3:]}, now)
    assert _keys(run) == ["2026-W40"]        # this week's, three days late
    assert _keys(skip) == ["2026-W38", "2026-W39"]
    assert all("superseded by 2026-W40" in why for _, why in skip)


def test_run_once_runs_nothing_when_the_newest_is_settled():
    p = _daily(Missed.RUN_ONCE)
    now = at(2026, 9, 28, 10)
    cands = p.windows(now, timedelta(days=2))
    run, skip = missed.plan(p, cands, {"2026-09-26"}, now)
    assert run == [] and _keys(skip) == ["2026-09-26"]


def test_bounded_catch_up_runs_at_most_max_runs_oldest_first():
    p = _daily(Missed.CATCH_UP, max_runs=2, horizon=timedelta(days=4))
    now = at(2026, 9, 28, 10)
    cands = p.windows(now, p.horizon)
    run, skip = missed.plan(p, cands, {w.key for w in cands}, now)
    assert _keys(run) == ["2026-09-27", "2026-09-28"]
    assert _keys(skip) == ["2026-09-24", "2026-09-25", "2026-09-26"]
    assert all("bounded-catch-up" in why for _, why in skip)


def test_windows_never_include_one_not_yet_due_and_respect_the_horizon():
    p = _daily(Missed.SKIP_STALE)
    ws = p.windows(at(2026, 9, 28, 6, 59), timedelta(days=2))
    assert [w.key for w in ws] == ["2026-09-26", "2026-09-27"]
    assert ws[-1].scheduled_for == at(2026, 9, 27, 7)
    wk = missed.weekly(lambda: 6, lambda: 7)(at(2026, 9, 28, 8), timedelta(weeks=1))
    assert [w.key for w in wk] == ["2026-09-20", "2026-09-27"]   # Sundays


# ------------------------------------------------------------ due()

async def test_unreadable_history_runs_nothing_and_records_nothing(runs):
    runs.unreadable = True
    assert await missed.due(_daily(Missed.RUN_ONCE), at(2026, 9, 28, 10)) is None
    assert runs.rows == {}


async def test_no_history_never_replays_from_before_records_began(runs):
    """The first deploy of this code: nothing is recorded yet, so only the
    newest window may run, and only inside its grace. Nothing older is
    marked skipped -- it may well have run."""
    p = _daily(Missed.RUN_ONCE)
    w = await missed.due(p, at(2026, 9, 28, 10))
    assert w is not None and w.key == "2026-09-28"
    assert runs.rows == {}
    missed.forget()
    # Past the newest window's day, with no history: nothing at all.
    assert await missed.due(p, at(2026, 9, 29, 3)) is None
    assert runs.rows == {}


async def test_a_settled_day_costs_one_read_not_one_per_tick(runs):
    runs.seed("job", "2026-09-28")
    p = _daily(Missed.SKIP_STALE)
    for _ in range(20):
        assert await missed.due(p, at(2026, 9, 28, 10)) is None
    assert runs.reads == 1


async def test_bounded_catch_up_runs_one_window_per_tick_then_stops(runs):
    p = _daily(Missed.CATCH_UP, max_runs=2, horizon=timedelta(days=5))
    runs.seed("job", "2026-09-22")
    now = at(2026, 9, 28, 10)
    ran = []
    for _ in range(6):
        w = await missed.due(p, now)
        if w is None:
            continue
        assert await occurrence.claim_once("job", w.key)
        await occurrence.done("job", w.key)
        ran.append(w.key)
    assert ran == ["2026-09-27", "2026-09-28"]
    st = runs.status("job")
    assert [k for k, v in st.items() if v == "skipped"] == [
        "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26"]


# ------------------------------------------------------ the scheduler's jobs

def _clock(monkeypatch, now: datetime) -> None:
    monkeypatch.setattr(scheduler, "_now", lambda: now)


async def test_brief_after_three_days_down_sends_today_once_and_skips_the_rest(
        runs, monkeypatch):
    from atlas.kernel import brief
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    sent = []

    async def send():
        sent.append(1)
    monkeypatch.setattr(brief, "send", send)
    runs.seed("brief", "2026-09-24")
    _clock(monkeypatch, at(2026, 9, 28, 11))
    for _restart in range(3):
        scheduler._last_brief_date = None
        await scheduler._maybe_brief()
    assert sent == [1]
    assert runs.status("brief") == {
        "2026-09-24": "done", "2026-09-25": "skipped", "2026-09-26": "skipped",
        "2026-09-27": "skipped", "2026-09-28": "done"}


async def test_a_failed_brief_is_retried_that_day_then_skipped(runs, monkeypatch):
    from atlas.kernel import brief
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)

    async def down():
        raise OSError("slack down")
    monkeypatch.setattr(brief, "send", down)
    runs.seed("brief", "2026-09-27")
    _clock(monkeypatch, at(2026, 9, 28, 8))
    scheduler._last_brief_date = None
    await scheduler._maybe_brief()
    await scheduler._maybe_brief()
    assert runs.rows[("brief", "2026-09-28")]["status"] == "failed"
    calls = []

    async def up():
        calls.append(1)
    monkeypatch.setattr(brief, "send", up)
    # Next morning: yesterday's failed brief is past its grace. Today's is
    # sent; yesterday's is recorded skipped, never sent late.
    _clock(monkeypatch, at(2026, 9, 29, 7, 30))
    scheduler._last_brief_date = None
    await scheduler._maybe_brief()
    assert calls == [1]
    assert runs.status("brief") == {"2026-09-27": "done", "2026-09-28": "skipped",
                                    "2026-09-29": "done"}


async def test_weekly_after_three_missed_weeks_sends_one_and_never_twice(
        runs, monkeypatch):
    from atlas.kernel import weekly
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    monkeypatch.setattr(scheduler, "_weekly_day", lambda: 0)
    sent = []

    async def send():
        sent.append(1)
    monkeypatch.setattr(weekly, "send", send)
    runs.seed("weekly", "2026-W36")
    _clock(monkeypatch, at(2026, 9, 30, 12))          # Wednesday of W40
    for _restart in range(3):
        scheduler._last_weekly_week = None
        await scheduler._maybe_weekly()
    assert sent == [1]
    st = runs.status("weekly")
    assert st["2026-W40"] == "done"
    assert [k for k, v in st.items() if v == "skipped"] == [
        "2026-W37", "2026-W38", "2026-W39"]


async def test_a_failed_weekly_is_not_retried_every_cycle_but_is_after_a_restart(
        runs, monkeypatch):
    from atlas.kernel import weekly
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    monkeypatch.setattr(scheduler, "_weekly_day", lambda: 0)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("slack down")
    monkeypatch.setattr(weekly, "send", flaky)
    runs.seed("weekly", "2026-W39")
    _clock(monkeypatch, at(2026, 9, 28, 8))
    scheduler._last_weekly_week = None
    for _ in range(3):                                  # the same process
        await scheduler._maybe_weekly()
    assert calls == [1] and runs.status("weekly")["2026-W40"] == "failed"
    scheduler._last_weekly_week = None                  # a restart
    missed.forget()
    await scheduler._maybe_weekly()
    await scheduler._maybe_weekly()
    assert calls == [1, 1] and runs.status("weekly")["2026-W40"] == "done"


async def _marketing_step(monkeypatch, filed: list) -> None:
    from atlas.kernel.marketing import cycle, experiments

    async def nothing_due(**_kw):
        return []

    async def plan_all():
        filed.append(1)
        return []
    monkeypatch.setattr(experiments, "measure_due", nothing_due)
    monkeypatch.setattr(cycle, "plan_all", plan_all)
    monkeypatch.setattr(cycle, "record_filing", lambda **kw: None)
    scheduler._last_marketing_date = None
    scheduler._last_measure_date = None
    scheduler._marketing_task = None
    await scheduler._maybe_marketing()
    if scheduler._marketing_task is not None:
        await scheduler._marketing_task


async def test_a_missed_marketing_sunday_is_filed_once_after_a_monday_restart(
        runs, monkeypatch):
    """FINDINGS RA-F2: the process-memory date could not recover a restart
    that landed after a missed Sunday. The recorded window can."""
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    monkeypatch.setattr(scheduler, "_marketing_day", lambda: 6)
    runs.seed("marketing", "2026-09-20")                 # last Sunday's, filed
    filed: list = []
    _clock(monkeypatch, at(2026, 9, 28, 8))              # Monday; Sunday missed
    for _restart in range(3):
        await _marketing_step(monkeypatch, filed)
    assert filed == [1]
    assert runs.status("marketing")["2026-09-27"] == "done"


async def test_marketing_with_no_history_files_only_on_the_day(runs, monkeypatch):
    """The first deploy: a Monday restart cannot see whether Sunday's plan
    went out before runs were recorded, so it does not file (the old
    conservative rule). On the day itself it does."""
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    monkeypatch.setattr(scheduler, "_marketing_day", lambda: 6)
    filed: list = []
    _clock(monkeypatch, at(2026, 9, 28, 8))
    await _marketing_step(monkeypatch, filed)
    assert filed == [] and runs.rows == {}
    missed.forget()
    _clock(monkeypatch, at(2026, 9, 27, 8))
    await _marketing_step(monkeypatch, filed)
    assert filed == [1]


async def test_a_marketing_filing_cut_short_is_never_filed_again(runs, monkeypatch):
    from atlas.kernel.marketing import cycle
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    monkeypatch.setattr(scheduler, "_marketing_day", lambda: 6)
    runs.seed("marketing", "2026-09-20")
    _clock(monkeypatch, at(2026, 9, 27, 8))
    calls = []

    async def boom():
        calls.append(1)
        raise RuntimeError("perplexity down")
    filed: list = []
    await _marketing_step(monkeypatch, filed)            # files; plan_all ok
    monkeypatch.setattr(cycle, "plan_all", boom)
    assert runs.status("marketing")["2026-09-27"] == "done"
    runs.rows[("marketing", "2026-09-27")]["status"] = "running"   # a crash
    missed.forget()
    scheduler._last_marketing_date = None
    scheduler._marketing_task = None
    await scheduler._maybe_marketing()
    assert calls == [] and scheduler._marketing_task is None


async def test_the_nightly_retrain_runs_once_after_a_restart(runs, monkeypatch):
    from atlas.kernel.judgment import nightly
    monkeypatch.setattr(scheduler, "_tournament_hour", lambda: 3)
    ran = []

    async def run_all():
        ran.append(1)
        return []
    monkeypatch.setattr(nightly, "run_all", run_all)
    runs.seed("judgment", "2026-09-24")
    _clock(monkeypatch, at(2026, 9, 28, 4))
    for _restart in range(3):
        scheduler._last_tournament_date = None
        await scheduler._maybe_tournament()
    assert ran == [1]
    st = runs.status("judgment")
    assert st["2026-09-28"] == "done"
    assert [k for k, v in st.items() if v == "skipped"] == [
        "2026-09-25", "2026-09-26", "2026-09-27"]


def test_an_agent_shift_missed_for_a_week_runs_once():
    """agent_shifts keep once-only in agent_runs: a week-old last shift is due
    once, and the next is counted from that run, not replayed per day."""
    from atlas.kernel import agents
    name = "missed-run-probe"
    now = datetime(2026, 9, 28, 23, 30).astimezone()
    week_ago = now - timedelta(days=7)
    assert agents.shift_due(name, week_ago, now)
    assert not agents.shift_due(name, now, now + timedelta(hours=1))


# ------------------------------------------------------ every job declares one

def _loop_steps() -> set[str]:
    src = inspect.getsource(scheduler.loop)
    return set(re.findall(r'\("([a-z_]+)",', src)) | {"outbox"}


def test_every_loop_step_declares_a_missed_run_policy():
    steps = _loop_steps()
    assert {"brief", "weekly", "review", "reels", "fixes", "agent_shifts",
            "judgment", "marketing", "calendar", "agency_meeting"} <= steps
    missing = steps - set(scheduler.POLICIES)
    assert not missing, f"no missed-run policy for {sorted(missing)}"
    for extra in ("reels_nudge", "measure"):
        assert extra in scheduler.POLICIES


def test_missions_resume_once_and_are_never_replayed():
    """kernel/missions.py: each pass works the next step out from the agency
    tables, so after an outage one pass resumes every live mission -- there
    is no window to replay, and a mission past its deadline lapses."""
    policy = scheduler.POLICIES["missions"]
    assert "missions" in _loop_steps()
    assert policy.missed is Missed.RUN_ONCE and not policy.recorded
    assert policy.guard == "interval (process memory)"


def test_the_declared_policies_are_the_intended_ones():
    P, M = scheduler.POLICIES, Missed
    assert {k for k, p in P.items() if p.missed is M.SKIP_STALE} == {
        "brief", "review", "reels", "reels_nudge", "ideas", "agency_meeting",
        "funnels", "agency_lead_board", "community", "sales_shift", "content_weekly",
        "bizops_digest", "bizops_find_repeats", "seo_rank", "research_scan",
        "research_weekly", "inbox_shift", "sites_shift", "tutorco_shift",
        "whatsapp_daily", "sage_tutorco_weekly", "youtube_learn"}
    # No reminder or post is ever caught up: bounded catch-up is for
    # per-window work that sends nothing, and no loop job is that today.
    assert not [k for k, p in P.items() if p.missed is M.CATCH_UP]
    assert {k for k, p in P.items() if p.recorded} == {
        "brief", "review", "weekly", "marketing", "judgment", "playlist", "ideas",
        "agency_meeting", "agency_lead_board", "sales_shift", "content_weekly",
        "bizops_digest", "bizops_find_repeats", "seo_rank", "research_scan",
        "research_weekly", "inbox_shift", "sites_shift", "tutorco_shift",
        "whatsapp_daily", "sage_tutorco_weekly", "youtube_learn"}
    for p in P.values():
        assert p.why and (p.recorded or p.guard != "schedule_runs")


def test_the_windows_follow_the_hour_the_gate_uses(monkeypatch):
    """Positive control: the windows read the hour at call time, so a
    changed BRIEF_HOUR (or a test's patch) moves them with the gate."""
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 9)
    ws = scheduler.POLICIES["brief"].windows(at(2026, 9, 28, 10), timedelta(days=0))
    assert ws[-1].scheduled_for.hour == 9
    monkeypatch.setenv("REVIEW_HOUR", "20")
    ws = scheduler.POLICIES["review"].windows(at(2026, 9, 28, 22), timedelta(days=0))
    assert ws[-1].scheduled_for.hour == 20


def test_the_policies_keep_the_windows_the_claims_used_before():
    """brief/review were claimed by local date since RA-F1: the same keys,
    so a row claimed before this deploy is still recognised."""
    now = at(2026, 9, 28, 22)
    for job in ("brief", "review"):
        ws = scheduler.POLICIES[job].windows(now, timedelta(days=0))
        assert ws[-1].key == date(2026, 9, 28).isoformat()
