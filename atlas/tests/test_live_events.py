"""What the dashboard's live stream is told, so Home changes without a
reload: a check's reading (with its number, unit and time), a card that
expired, an agent run starting and finishing.

Fire-and-forget only: nothing here can act, and an agent's event carries
its name and phase -- never the task or the answer, which for a PRIVATE
agent is his own mail.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlas.kernel import approvals, events, harness, scheduler
from atlas.kernel.contracts import Check, Health, Observation


@pytest.fixture
def stream():
    queue = events.subscribe()
    try:
        yield queue
    finally:
        events.unsubscribe(queue)


def drain(queue) -> list[events.Event]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def test_a_check_event_carries_the_reading_number_unit_and_time(
        stream, monkeypatch):
    obs = Observation("sales_7d", Health.OK, 57.0, "usd", "A: 3 sale(s)")

    async def run() -> Observation:
        return obs

    async def nothing(*_a, **_kw):
        return None

    async def cid(_key):
        return 1

    monkeypatch.setattr(scheduler.REGISTRY, "checks", {
        "shop.sales_7d": Check("sales_7d", run, timedelta(hours=6))})
    monkeypatch.setattr(scheduler, "_next_run", {})
    monkeypatch.setattr(scheduler, "_check_id", cid)
    monkeypatch.setattr(scheduler, "record_observation", nothing)
    monkeypatch.setattr(scheduler, "record_schedule", nothing)
    monkeypatch.setattr(scheduler, "evaluate", nothing)
    await scheduler.run_once()
    [event] = [e for e in drain(stream) if e.kind == "check"]
    assert event.data["key"] == "shop.sales_7d"
    assert (event.data["value"], event.data["unit"]) == (57.0, "usd")
    assert event.data["observed_at"] == obs.at.isoformat()


async def test_an_expired_card_is_announced_and_nothing_runs(stream, monkeypatch):
    async def lapsed(sql, *args):
        assert "status='expired'" in sql and "expires_at < now()" in sql
        return [{"id": 8, "action": "gitlab.open_issue"}]

    async def nothing(*_a, **_kw):
        return None

    monkeypatch.setattr(approvals, "fetch_all", lapsed)
    monkeypatch.setattr(approvals, "audit", nothing)
    monkeypatch.setattr(approvals, "_expiry_notice", nothing)
    monkeypatch.setattr(approvals, "_open_cards_notice", nothing)
    monkeypatch.setattr("atlas.kernel.judgment.record_outcome", nothing)
    assert await approvals.expire_stale() == 1
    [event] = drain(stream)
    assert event.kind == "decision"
    assert event.data == {"id": 8, "action": "gitlab.open_issue", "outcome": "expired"}


async def test_a_finished_run_is_announced_with_its_name_only(stream, memory_backend):
    await harness.record_run("inbox", "read my mail from the bank", model="m",
                             provider="ollama", turns=1, outcome="Your balance is ...")
    [event] = drain(stream)
    assert event.kind == "agent"
    assert event.data == {"agent": "inbox", "phase": "finished"}


async def test_an_agent_run_is_announced_when_it_starts_and_when_it_ends(
        stream, memory_backend, monkeypatch):
    from atlas.kernel import agents as agents_module
    from atlas.kernel.agents import Agent
    from atlas.kernel.llm import Completion, Need

    async def answer(*_a, **_kw):
        return Completion(text="done", provider="groq", model="x"), False

    monkeypatch.setattr(agents_module, "think_turn", answer)
    agent = Agent(name="ops", title="Ops", brief="", system="", tools=set(),
                  need=Need.REASON)
    assert await agent.run("how are the checks?") == "done"
    got = [(e.kind, e.data) for e in drain(stream)]
    assert got == [("agent", {"agent": "ops", "phase": "started"}),
                   ("agent", {"agent": "ops", "phase": "finished"})]
