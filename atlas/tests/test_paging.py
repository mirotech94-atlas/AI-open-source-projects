"""A failing HIGH or CRITICAL check reaches Miro on its own.

The case that motivated this: `matrix_rooms.urgent`, a CRITICAL check,
failed sixty-two times through one night. The reasoner had no action for
it, so no proposal, so no card, so no call. Twenty green tiles and the one
that flipped went nowhere.
"""
from __future__ import annotations

import pytest

from atlas.kernel import scheduler
from atlas.kernel.contracts import Health, Observation, Severity
from atlas.kernel.detect import Finding


def finding(severity: Severity, health: Health = Health.FAILING) -> Finding:
    obs = Observation(check="matrix_rooms.urgent", health=health,
                      detail="1 message(s) look urgent: @ai.bot in VPSie Support")
    return Finding("matrix_rooms.urgent", severity,
                   f"matrix_rooms.urgent is {health}", obs.detail, obs, "rule")


@pytest.fixture
def sent(monkeypatch):
    out: list = []

    async def fake_notify(msg):
        out.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    return out


async def test_a_failing_critical_check_is_paged_without_a_proposal(
        memory_backend, sent):
    memory_backend.responses["SELECT id FROM signals"] = None
    assert await scheduler.page(finding(Severity.CRITICAL)) is True
    assert len(sent) == 1
    assert sent[0].severity is Severity.CRITICAL
    assert sent[0].topic == "matrix_rooms"
    assert "look urgent" in sent[0].body
    inserted = [s for s, _ in memory_backend.sql_log if "INSERT INTO signals" in s]
    assert inserted, "the page was not recorded, so a restart would re-page"


async def test_a_failing_high_check_is_paged_too(memory_backend, sent):
    memory_backend.responses["SELECT id FROM signals"] = None
    assert await scheduler.page(finding(Severity.HIGH)) is True
    assert sent[0].severity is Severity.HIGH


async def test_the_same_failure_is_paged_once_a_day(memory_backend, sent):
    memory_backend.responses["SELECT id FROM signals"] = {"id": 7}
    assert await scheduler.page(finding(Severity.CRITICAL)) is False
    assert sent == []


async def test_a_normal_failure_is_not_paged(memory_backend, sent):
    """NORMAL is the brief's business, not a page."""
    memory_backend.responses["SELECT id FROM signals"] = None
    assert await scheduler.page(finding(Severity.NORMAL)) is False
    assert sent == []


async def test_an_unrunnable_critical_check_is_not_paged_from_here(
        memory_backend, sent):
    """UNKNOWN already becomes a card naming its cause; ringing about a
    missing key at 3am is not what the phone is for."""
    memory_backend.responses["SELECT id FROM signals"] = None
    assert await scheduler.page(finding(Severity.CRITICAL, Health.UNKNOWN)) is False
    assert sent == []


async def test_an_unreadable_dedup_pages_anyway(memory_backend, sent):
    """No canned response: the backend raises. Ringing twice beats never."""
    assert await scheduler.page(finding(Severity.CRITICAL)) is True
    assert len(sent) == 1


async def test_a_cycle_pages_before_it_starts_reasoning(memory_backend, sent,
                                                         monkeypatch):
    """The page does not wait on diagnose(), and does not depend on it."""
    import asyncio

    memory_backend.responses["SELECT id FROM signals"] = None

    async def slow_no_idea(f):
        await asyncio.sleep(0.2)

    async def one_finding():
        return [finding(Severity.CRITICAL)]

    monkeypatch.setattr("atlas.kernel.reason.diagnose", slow_no_idea)
    monkeypatch.setattr(scheduler, "run_once", one_finding)
    monkeypatch.setattr(scheduler, "_handling", None)
    await scheduler._run_checks()
    assert len(sent) == 1, "paged before the reasoner had answered"
    task = scheduler._handling
    assert task is not None and not task.done()
    await task


async def test_a_cycle_does_not_stack_reasoning(memory_backend, sent, monkeypatch):
    """Two cycles while the reasoner is busy: one task, not two."""
    import asyncio

    memory_backend.responses["SELECT id FROM signals"] = {"id": 1}
    calls = []

    async def slow(f):
        calls.append(f)
        await asyncio.sleep(0.2)

    async def one_finding():
        return [finding(Severity.CRITICAL)]

    monkeypatch.setattr("atlas.kernel.reason.diagnose", slow)
    monkeypatch.setattr(scheduler, "run_once", one_finding)
    monkeypatch.setattr(scheduler, "_handling", None)
    await scheduler._run_checks()
    await scheduler._run_checks()
    await scheduler._handling
    assert len(calls) == 1
