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

from atlas.kernel import scheduler

# Enough passes that "the checks kept running" cannot be one lucky pass
# that started before the slow step did.
PASSES = 5


async def test_a_hanging_step_does_not_stop_the_checks(quiet_loop, monkeypatch):
    ticks: list[int] = []
    hanging = asyncio.Event()

    async def slow_signals() -> None:
        hanging.set()
        await asyncio.sleep(3600)

    async def count_checks() -> None:
        ticks.append(1)

    monkeypatch.setattr(scheduler, "_maybe_signals", slow_signals)
    monkeypatch.setattr(scheduler, "_run_checks", count_checks)

    # Fails, naming the passes it got, if the hung step holds the loop.
    await quiet_loop.run(PASSES)

    assert hanging.is_set(), "the slow step never started"
    assert len(ticks) >= PASSES, (
        f"checks ran {len(ticks)} time(s) in {quiet_loop.passes} passes while "
        "one step hung")


async def test_an_expensive_step_is_not_started_twice(quiet_loop, monkeypatch):
    starts: list[int] = []

    async def slow_signals() -> None:
        starts.append(1)
        await asyncio.sleep(3600)

    monkeypatch.setattr(scheduler, "_maybe_signals", slow_signals)

    await quiet_loop.run(PASSES)

    assert starts == [1], (
        f"the step was started {len(starts)} times in {quiet_loop.passes} "
        "passes; one run at a time")
