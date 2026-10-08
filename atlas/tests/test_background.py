"""Shutdown accounts for every background job.

The lifespan used to cancel only the scheduler loop. The weekly marketing
pass, the reasoning over findings, a phone inbox triage and a browser popup
closing all died at loop close with no line in the log -- the week read as
never filed, a promised text never came.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from atlas.kernel import background


@pytest.fixture(autouse=True)
def clean():
    background._TASKS.clear()
    yield
    background._TASKS.clear()


async def test_cancel_all_cuts_short_what_is_running_and_names_it(caplog):
    started = asyncio.Event()
    cancelled: list[str] = []

    async def long_job():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append("marketing")
            raise

    task = background.spawn(long_job(), "weekly marketing plan")
    background.spawn(asyncio.sleep(0), "quick one")
    await started.wait()
    await asyncio.sleep(0)
    with caplog.at_level(logging.WARNING, logger="atlas.background"):
        cut = await background.cancel_all(timeout=1)
    assert cut == ["weekly marketing plan"]
    assert task.cancelled() and cancelled == ["marketing"]
    assert any("weekly marketing plan was still running and is cut short"
               in r.getMessage() for r in caplog.records)
    assert background.running() == []


async def test_a_job_that_raises_is_logged_not_lost(caplog):
    async def boom():
        raise RuntimeError("plan_all fell over")

    with caplog.at_level(logging.ERROR, logger="atlas.background"):
        task = background.spawn(boom(), "weekly marketing plan")
        await asyncio.wait([task])
        await asyncio.sleep(0)
    assert any("weekly marketing plan" in r.getMessage() for r in caplog.records)


async def test_a_job_that_ignores_cancel_is_named_as_not_stopping(caplog):
    async def stubborn():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)        # ignores the first cancel, briefly

    task = background.spawn(stubborn(), "stubborn job")
    await asyncio.sleep(0)
    with caplog.at_level(logging.ERROR, logger="atlas.background"):
        await background.cancel_all(timeout=0.05)
    assert any("stubborn job did not stop" in r.getMessage() for r in caplog.records)
    await asyncio.wait([task], timeout=1)


def test_every_background_start_in_the_branch_is_tracked():
    """The sites the review named, and the untracked create_task calls it
    listed, all go through the registry."""
    import inspect

    from atlas.api import main, phone, slack_events
    from atlas.kernel import scheduler
    from atlas.modules.browser import session

    assert 'background.spawn(file_the_week(), "weekly marketing plan")' in \
        inspect.getsource(scheduler._maybe_marketing)
    assert 'background.spawn(handle(findings)' in inspect.getsource(scheduler._run_checks)
    assert "background.track(task" in inspect.getsource(scheduler._detach)
    assert "track(task" in inspect.getsource(phone._spawn)
    assert "track(" in inspect.getsource(session)
    assert "asyncio.create_task(" not in inspect.getsource(slack_events)
    # The note of a finished call is kept off the request path, tracked.
    assert 'spawn(note_call(' in inspect.getsource(phone.wrap_up)
    source = inspect.getsource(main.lifespan)
    assert "cancel_all()" in source and "task.cancel()" in source


async def test_a_marketing_pass_cut_short_records_what_it_filed(monkeypatch):
    """Cancelled mid-filing, the week says it was cut short -- it does not
    read as never filed."""
    from atlas.kernel import scheduler
    from atlas.kernel.contracts import ActionResult
    from atlas.kernel.marketing import cycle, experiments

    gate = asyncio.Event()
    recorded: list[dict] = []

    async def plans():
        return ["p"]

    async def slow_execute(req):
        if req.action == "second":
            gate.set()
            await asyncio.sleep(60)
        return ActionResult(True, "filed")

    async def nothing_due(**_kw):
        return []

    monkeypatch.setattr(cycle, "plan_all", plans)
    monkeypatch.setattr(cycle, "as_requests",
                        lambda plan: [("first", {}, "r"), ("second", {}, "r")])
    monkeypatch.setattr(cycle, "record_filing", lambda **kw: recorded.append(kw))
    monkeypatch.setattr("atlas.kernel.executor.execute", slow_execute)
    monkeypatch.setattr(experiments, "measure_due", nothing_due)
    _marketing_window_due(monkeypatch, scheduler)
    monkeypatch.setattr(scheduler, "_marketing_task", None)

    await scheduler._maybe_marketing()
    await asyncio.wait_for(gate.wait(), timeout=1)
    await background.cancel_all(timeout=1)
    assert recorded and recorded[0]["filed"] == 1
    assert "cut short by a shutdown" in recorded[0]["reasons"][-1]


def _marketing_window_due(monkeypatch, scheduler):
    """The missed-run policy says this week's plan is due, and the window
    claim succeeds (kernel.missed / kernel.occurrence, tested on their own
    in test_missed_runs.py)."""
    from datetime import datetime

    from atlas.kernel import missed, occurrence

    async def window(now):
        return missed.Window("2026-09-27", datetime(2026, 9, 27, 7).astimezone())

    async def yes(*_a, **_kw):
        return True

    async def nothing(*_a, **_kw):
        return None

    monkeypatch.setattr(scheduler, "_marketing_window", window)
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 0)
    monkeypatch.setattr(scheduler, "_last_marketing_date", None)
    monkeypatch.setattr(occurrence, "claim_once", yes)
    monkeypatch.setattr(occurrence, "done", nothing)
    monkeypatch.setattr(occurrence, "fail", nothing)
