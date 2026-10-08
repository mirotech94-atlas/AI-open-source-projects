"""The scheduler's `agent_shifts` step (Task 69, Miro 2026-09-27): "every
agent that is READY ... runs on its schedule, always."

Each cycle: a READY agent whose shift is due gets one real run through its
own `Agent.run()` (not warmup's tool-free clone) with the fixed
`agents.SHIFT_PROMPT`; a READY agent that has never run at all gets the
one-shot self-test (`agents.warmup`) instead, once; a not-ready agent never
runs, and its reasons are audited once a day, not every cycle. At most two
shifts start per cycle, and due/not-ready decisions are read from
`agent_runs` (via `fetch_all`), never from process memory, so they survive a
restart.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel import agents, scheduler


class FakeAgent:
    """A minimal stand-in for `agents.Agent` -- just enough for the
    scheduler step to call `.run(SHIFT_PROMPT)` on."""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[str] = []
        self.raises: Exception | None = None

    async def run(self, task: str, context=None) -> str:
        self.calls.append(task)
        if self.raises is not None:
            raise self.raises
        return "shift done"


@pytest.fixture
def fake_roster(monkeypatch):
    roster: dict[str, FakeAgent] = {}
    monkeypatch.setattr(agents, "ROSTER", roster)
    return roster


@pytest.fixture
def stub_readiness(monkeypatch):
    """Every agent ready by default; a test overrides per-name."""
    table: dict[str, tuple[bool, list[str]]] = {}

    def fake_readiness(name):
        return table.get(name, (True, []))

    monkeypatch.setattr(agents, "readiness", fake_readiness)
    return table


@pytest.fixture
def stub_due(monkeypatch):
    """Every agent due by default; a test overrides per-name."""
    table: dict[str, bool] = {}

    def fake_due(name, last_shift_at, now):
        return table.get(name, True)

    monkeypatch.setattr(agents, "shift_due", fake_due)
    return table


@pytest.fixture
def audits(monkeypatch):
    """Capture every `audit()` call the step makes, without a real chain."""
    calls: list[tuple[str, str, dict]] = []

    async def fake_audit(event, subject, data=None):
        calls.append((event, subject, data or {}))

    monkeypatch.setattr("atlas.kernel.audit.audit", fake_audit)
    return calls


def _agent_rows(*, last_any: dict[str, datetime] | None = None,
                last_shift: dict[str, datetime] | None = None) -> list[dict]:
    last_any = last_any or {}
    last_shift = last_shift or {}
    names = set(last_any) | set(last_shift)
    return [{"agent": n, "last_any": last_any.get(n), "last_shift": last_shift.get(n)}
            for n in names]


@pytest.fixture
def db(monkeypatch):
    """`fetch_all`/`fetch_one` as the step calls them: the combined
    (last_any, last_shift) query, and the once-a-day audit-dedup lookup."""
    state = {"rows": [], "audited_today": set()}

    async def fake_fetch_all(sql, *args):
        if "agent_runs" in sql:
            return state["rows"]
        return []

    async def fake_fetch_one(sql, *args):
        if "actions_log" in sql:
            subject = args[1] if len(args) > 1 else None
            return {"1": 1} if subject in state["audited_today"] else None
        return None

    monkeypatch.setattr(scheduler, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(scheduler, "fetch_one", fake_fetch_one)
    return state


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(scheduler, "_now", lambda: NOW)


async def test_a_ready_due_agent_that_has_run_before_gets_a_shift(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now):
    fake_roster["ops"] = FakeAgent("ops")
    db["rows"] = _agent_rows(last_any={"ops": NOW})

    await scheduler._maybe_agent_shifts()

    assert fake_roster["ops"].calls == [agents.SHIFT_PROMPT]
    assert audits == []


async def test_a_never_run_ready_agent_gets_the_self_test_not_a_shift(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now, monkeypatch):
    fake_roster["ops"] = FakeAgent("ops")
    db["rows"] = []          # no agent_runs row at all for "ops"
    warmed = []

    async def fake_warmup(name):
        warmed.append(name)

    monkeypatch.setattr(agents, "warmup", fake_warmup)

    await scheduler._maybe_agent_shifts()

    assert warmed == ["ops"]
    assert fake_roster["ops"].calls == [], "a never-run agent gets the self-test, not a shift"


async def test_a_not_ready_agent_never_runs_and_is_audited_once(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now):
    fake_roster["fiverr"] = FakeAgent("fiverr")
    stub_readiness["fiverr"] = (False, ["browser disabled — missing env: X"])
    db["rows"] = _agent_rows(last_any={"fiverr": NOW})

    await scheduler._maybe_agent_shifts()

    assert fake_roster["fiverr"].calls == []
    assert audits == [("agent.shift_skipped", "fiverr",
                       {"reasons": ["browser disabled — missing env: X"]})]


async def test_a_not_ready_agent_is_not_audited_twice_the_same_day(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now):
    fake_roster["fiverr"] = FakeAgent("fiverr")
    stub_readiness["fiverr"] = (False, ["reason"])
    db["rows"] = _agent_rows(last_any={"fiverr": NOW})
    db["audited_today"] = {"fiverr"}

    await scheduler._maybe_agent_shifts()

    assert audits == []


async def test_not_due_agents_are_skipped(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now):
    fake_roster["ops"] = FakeAgent("ops")
    stub_due["ops"] = False
    db["rows"] = _agent_rows(last_any={"ops": NOW})

    await scheduler._maybe_agent_shifts()

    assert fake_roster["ops"].calls == []


async def test_at_most_two_shifts_per_cycle(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now):
    for name in ("a1", "a2", "a3"):
        fake_roster[name] = FakeAgent(name)
    db["rows"] = _agent_rows(last_any={n: NOW for n in fake_roster})

    await scheduler._maybe_agent_shifts()

    ran = [n for n, a in fake_roster.items() if a.calls]
    assert len(ran) == 2, f"expected exactly 2 shifts to start, got {ran}"
    # Deterministic: alphabetical, so the same two win every cycle until one
    # of them stops being due.
    assert ran == ["a1", "a2"]


async def test_a_third_agent_still_runs_next_cycle_once_the_first_two_are_not_due(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now):
    for name in ("a1", "a2", "a3"):
        fake_roster[name] = FakeAgent(name)
    stub_due["a1"] = False
    stub_due["a2"] = False
    db["rows"] = _agent_rows(last_any={n: NOW for n in fake_roster})

    await scheduler._maybe_agent_shifts()

    assert fake_roster["a3"].calls == [agents.SHIFT_PROMPT]


async def test_a_failed_shift_still_writes_an_agent_runs_row(
        fake_roster, stub_readiness, stub_due, db, audits, frozen_now, monkeypatch):
    """`Agent.run()` only catches `LocalModelUnavailable`; a plain
    `RuntimeError` (every provider in the chain failed) must not leave the
    agent with no row at all -- it would stay perpetually due and burn the
    per-cycle budget on a dead provider forever."""
    boom = RuntimeError("all providers failed for reason")
    fake_roster["ops"] = FakeAgent("ops")
    fake_roster["ops"].raises = boom
    db["rows"] = _agent_rows(last_any={"ops": NOW})

    recorded = []

    async def fake_record_run(agent_name, task, **kw):
        recorded.append((agent_name, task, kw))

    monkeypatch.setattr("atlas.kernel.harness.record_run", fake_record_run)

    await scheduler._maybe_agent_shifts()

    assert len(recorded) == 1
    name, task, kw = recorded[0]
    assert name == "ops"
    assert task == agents.SHIFT_PROMPT
    assert kw["model"] == "" and kw["provider"] == ""
    assert "all providers failed" in kw["outcome"]


async def test_due_ness_survives_a_restart_via_agent_runs(
        fake_roster, stub_readiness, db, audits, frozen_now, monkeypatch):
    """No process-memory cache: the step's due decision comes from
    `agent_runs` on every call, so a fresh process (nothing in memory) still
    gets the right answer from the database alone."""
    fake_roster["ops"] = FakeAgent("ops")
    seen = {}

    def real_due(name, last_shift_at, now):
        seen["last_shift_at"] = last_shift_at
        return False

    monkeypatch.setattr(agents, "shift_due", real_due)
    stamp = datetime(2026, 9, 26, 7, 0, tzinfo=UTC)
    db["rows"] = _agent_rows(last_any={"ops": stamp}, last_shift={"ops": stamp})

    await scheduler._maybe_agent_shifts()

    assert seen["last_shift_at"] == stamp
