"""The loop keeps checking while an expensive step is still working.

The case that motivated this: on 2026-09-24 the watcher went dark for
1h20m, and for 1h46m earlier the same day, with no restart and no error in
the log. Every step in `loop()` was awaited inline, so the one step that
drafts replies with a model — one call per message, in series, over a full
inbox — held the cycle for as long as it took, and no check ran at all.
The API kept serving 200s and `/healthz` stayed green the whole time.

`_run_checks` already learned this lesson for `handle()`: expensive work
goes to a background task, one at a time. These tests hold the rest of the
loop to the same rule.
"""
from __future__ import annotations

import asyncio

import pytest

from atlas.kernel import scheduler


@pytest.fixture
def quiet_loop(monkeypatch):
    """Every step a no-op, so a test only has to make one of them slow."""
    async def noop() -> None:
        return None

    for name in ("_run_checks", "expire_stale", "_maybe_signals",
                 "_maybe_brief", "_maybe_weekly", "_maybe_marketing",
                 "_maybe_reels",
                 "_maybe_review", "_maybe_rhythm", "_maybe_purge",
                 "_maybe_tournament", "_maybe_fixes", "_maybe_agent_shifts"):
        monkeypatch.setattr(scheduler, name, noop)
    scheduler._tasks.clear()
    yield monkeypatch
    # A detached task outliving its test would still be pending in the next
    # one, holding objects bound to an event loop that has since closed.
    for task in scheduler._tasks.values():
        task.cancel()
    scheduler._tasks.clear()


async def _run_loop_briefly(seconds: float = 0.4) -> None:
    task = asyncio.create_task(scheduler.loop(interval=0.01))
    try:
        await asyncio.sleep(seconds)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_a_hanging_step_does_not_stop_the_checks(quiet_loop):
    ticks: list[int] = []
    hanging = asyncio.Event()

    async def slow_signals() -> None:
        hanging.set()
        await asyncio.sleep(3600)

    async def count_checks() -> None:
        ticks.append(1)

    quiet_loop.setattr(scheduler, "_maybe_signals", slow_signals)
    quiet_loop.setattr(scheduler, "_run_checks", count_checks)

    await _run_loop_briefly()

    assert hanging.is_set(), "the slow step never started"
    assert len(ticks) > 2, (
        f"checks ran {len(ticks)} time(s) while one step hung — the loop is "
        "blocked on it")


async def test_an_expensive_step_is_not_started_twice(quiet_loop):
    starts: list[int] = []

    async def slow_signals() -> None:
        starts.append(1)
        await asyncio.sleep(3600)

    quiet_loop.setattr(scheduler, "_maybe_signals", slow_signals)

    await _run_loop_briefly()

    assert starts == [1], (
        f"the step was started {len(starts)} times; one run at a time")
