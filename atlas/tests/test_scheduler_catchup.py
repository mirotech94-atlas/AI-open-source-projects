"""T4: the 17:00 UTC tutorial tick catches up after a Mac wakes late.

`_maybe_academy_tutorial` runs on any hour at or after the tutorial hour of
a new UTC date, so a Mac that slept through 17:00 still renders when it
wakes. Not asserted here: restart safety (the date lives in memory; the DB
rows guard a restart).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel import scheduler
from atlas.modules.academy import tutorials as tut


@pytest.fixture
def sched(monkeypatch):
    state = {"now": datetime(2026, 10, 28, 16, 59, tzinfo=UTC), "spawned": []}

    class Task:
        def done(self) -> bool:
            return True

    def spawn(coro, name):
        coro.close()
        state["spawned"].append(name)
        return Task()

    monkeypatch.setattr(scheduler, "_now", lambda: state["now"])
    monkeypatch.setattr(scheduler.background, "spawn", spawn)
    monkeypatch.setattr(scheduler, "_academy_tutorial_task", None)
    monkeypatch.setattr(scheduler, "_last_academy_tutorial_date", None)
    monkeypatch.setattr(tut, "enabled", lambda: True)
    monkeypatch.delenv("ACADEMY_TUTORIALS_HOUR_UTC", raising=False)
    monkeypatch.setattr(tut, "retry_due", lambda now: False)
    return state


def test_default_hour_is_17_utc():
    assert tut.DEFAULT_HOUR_UTC == 17


async def test_before_the_hour_nothing_runs(sched):
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"] == []


async def test_a_late_wake_catches_up_once(sched):
    sched["now"] = datetime(2026, 10, 28, 19, 30, tzinfo=UTC)
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"] == ["the daily Academy tutorial"]
    # Same process, same date: no second run.
    sched["now"] = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"] == ["the daily Academy tutorial"]


async def test_the_next_date_runs_again(sched):
    sched["now"] = datetime(2026, 10, 28, 19, 30, tzinfo=UTC)
    await scheduler._maybe_academy_tutorial()
    sched["now"] = datetime(2026, 10, 29, 16, 59, tzinfo=UTC)
    await scheduler._maybe_academy_tutorial()
    assert len(sched["spawned"]) == 1
    sched["now"] = datetime(2026, 10, 29, 18, 5, tzinfo=UTC)
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"] == ["the daily Academy tutorial"] * 2
