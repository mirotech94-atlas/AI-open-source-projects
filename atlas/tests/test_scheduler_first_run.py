"""After a restart every check is measured soon, not at its next interval.

Miro (2026-11-12) saw ~42 checks read UNKNOWN right after an API restart until
the collectors ran. The scheduler keeps `_next_run` in memory only, so a fresh
process has no schedule and every check is due on the first pass. These tests
pin that, and the budget the first pass is held to (FIRST_PASS_BUDGET_S).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from atlas.kernel import detect, scheduler
from atlas.kernel.contracts import Check, Health, Observation


async def _one() -> int:
    return 1


def _quiet(monkeypatch, checks, recorded):
    monkeypatch.setattr(scheduler.REGISTRY, "checks", checks)
    monkeypatch.setattr(scheduler, "_next_run", {})
    monkeypatch.setattr(scheduler, "_check_id", lambda key: _one())

    async def capture(cid, obs):
        recorded.append(obs)

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(scheduler, "record_observation", capture)
    monkeypatch.setattr(scheduler, "record_schedule", nothing)
    monkeypatch.setattr(scheduler, "record_finding", nothing)
    monkeypatch.setattr(detect, "baseline", nothing)


def _check(key: str, health: Health = Health.OK, interval_h: int = 24,
           delay: float = 0.0, timeout_s: float = 30.0) -> Check:
    async def run() -> Observation:
        if delay:
            await asyncio.sleep(delay)
        return Observation(check=key, health=health, detail="read")
    return Check(name=key.split(".")[1], run=run,
                 interval=timedelta(hours=interval_h),
                 timeout=timedelta(seconds=timeout_s))


async def test_a_fresh_process_runs_every_check_on_its_first_pass(
        monkeypatch, memory_backend):
    """Even a daily check: the schedule is not remembered across a restart."""
    checks = {f"m.c{i}": _check(f"m.c{i}", interval_h=24) for i in range(40)}
    recorded: list = []
    _quiet(monkeypatch, checks, recorded)
    await scheduler.run_once()
    assert len(recorded) == 40
    assert scheduler.FIRST_PASS_BUDGET_S <= 60.0


async def test_a_hanging_check_does_not_hold_back_the_other_readings(
        monkeypatch, memory_backend):
    """Each reading is stored as it lands, so one slow collector cannot keep
    the other checks UNKNOWN for its whole timeout."""
    checks = {"m.fast": _check("m.fast"),
              "m.slow": _check("m.slow", delay=5.0, timeout_s=30.0)}
    recorded: list = []
    _quiet(monkeypatch, checks, recorded)
    task = asyncio.create_task(scheduler.run_once())
    await asyncio.sleep(0.2)
    try:
        assert [o.check for o in recorded] == ["m.fast"]
    finally:
        task.cancel()


async def test_the_first_pass_budget_cuts_a_long_check_and_says_so(
        monkeypatch, memory_backend):
    """A check allowed longer than the first-pass budget is cut at the budget
    on boot and reads UNKNOWN with the reason, never a silent wait."""
    checks = {"m.long": _check("m.long", delay=5.0, timeout_s=300.0)}
    recorded: list = []
    _quiet(monkeypatch, checks, recorded)
    monkeypatch.setattr(scheduler, "FIRST_PASS_BUDGET_S", 0.05)
    monkeypatch.setattr(scheduler, "FIRST_PASS_DONE", False)
    await scheduler.run_once()
    assert recorded[0].health is Health.UNKNOWN
    assert "first run after start" in recorded[0].detail
    assert scheduler.FIRST_PASS_DONE is True
    # The cut is not a verdict: the check is due again on the next pass (with
    # its normal timeout), not a whole interval later.
    assert scheduler._next_run["m.long"] <= __import__("time").monotonic()


async def test_the_loop_runs_checks_before_any_other_step(quiet_loop, monkeypatch):
    order: list[str] = []

    async def checks() -> None:
        order.append("checks")

    async def signals() -> None:
        order.append("signals")

    monkeypatch.setattr(scheduler, "_run_checks", checks)
    monkeypatch.setattr(scheduler, "_maybe_signals", signals)
    await quiet_loop.run(1)
    assert order[0] == "checks"
