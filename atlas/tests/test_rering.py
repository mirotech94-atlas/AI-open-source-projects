"""Ringing again after an unanswered CRITICAL call (kernel/rering.py):
bounded, only while something is still waiting on him, never when that
cannot be read, and silent once he answers."""
from __future__ import annotations

import pytest

from atlas.kernel import rering
from atlas.kernel.comms import Button, Outbound
from atlas.kernel.contracts import Severity

CARD = Outbound("#41 — Restart the worker", "Stuck.", Severity.CRITICAL,
                buttons=(Button("Approve", "approve:41"), Button("Decline", "decline:41")))
PAGE = Outbound("flycheap.database is failing", "down", Severity.CRITICAL,
                incident_key="flycheap.database")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    rering.reset()
    monkeypatch.delenv("CALL_RERINGS", raising=False)
    monkeypatch.delenv("ATLAS_NO_RERING", raising=False)
    yield
    rering.reset()


def _dialer():
    calls: list[Outbound] = []

    async def send(msg):
        calls.append(msg)
        return f"CA{len(calls) + 1}"
    return calls, send


def _sleeper():
    waits: list[float] = []

    async def sleep(seconds):
        waits.append(seconds)
    return waits, sleep


async def test_a_missed_call_about_a_pending_card_rings_again_in_two_minutes(memory_backend):
    memory_backend.responses["SELECT count(*) AS n FROM proposals"] = {"n": 1}
    calls, send = _dialer()
    waits, sleep = _sleeper()
    rering.placed("CA1", CARD)
    assert await rering.missed("CA1", send=send, sleep=sleep) == "rang"
    assert waits == [120.0] and calls == [CARD]
    # The second miss waits five minutes; the third is the end of it.
    assert await rering.missed("CA2", send=send, sleep=sleep) == "rang"
    assert waits == [120.0, 300.0] and len(calls) == 2
    assert rering.missed("CA3", send=send, sleep=sleep) is None
    assert len(calls) == 2, "three calls in all, then it stops"


async def test_nothing_rings_once_the_card_is_decided_or_expired(memory_backend):
    memory_backend.responses["SELECT count(*) AS n FROM proposals"] = {"n": 0}
    calls, send = _dialer()
    _, sleep = _sleeper()
    rering.placed("CA1", CARD)
    assert await rering.missed("CA1", send=send, sleep=sleep) == "settled"
    assert calls == []


async def test_a_page_rings_again_only_while_failing_and_unacknowledged(
        memory_backend, monkeypatch):
    calls, send = _dialer()
    _, sleep = _sleeper()
    health = {"now": "failing"}

    async def latest(key):
        return health["now"]
    monkeypatch.setattr("atlas.kernel.acks.latest_health", latest)

    memory_backend.responses["SELECT 1 AS x FROM acks"] = None
    rering.placed("CA1", PAGE)
    assert await rering.missed("CA1", send=send, sleep=sleep) == "rang"

    memory_backend.responses["SELECT 1 AS x FROM acks"] = {"x": 1}     # acknowledged
    rering.placed("CA5", PAGE)
    assert await rering.missed("CA5", send=send, sleep=sleep) == "settled"

    memory_backend.responses["SELECT 1 AS x FROM acks"] = None
    health["now"] = "ok"                                                # recovered
    rering.placed("CA6", PAGE)
    assert await rering.missed("CA6", send=send, sleep=sleep) == "settled"
    assert len(calls) == 1


async def test_an_unreadable_state_does_not_ring(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_one", broken)
    calls, send = _dialer()
    _, sleep = _sleeper()
    rering.placed("CA1", CARD)
    assert await rering.missed("CA1", send=send, sleep=sleep) == "settled"
    assert calls == []


async def test_a_message_with_nothing_to_decide_never_rings_again():
    calls, send = _dialer()
    _, sleep = _sleeper()
    rering.placed("CA1", Outbound("Heads up", "fyi", Severity.CRITICAL))
    assert await rering.missed("CA1", send=send, sleep=sleep) == "settled"
    assert calls == []


async def test_answering_or_an_unknown_call_schedules_nothing(memory_backend):
    calls, send = _dialer()
    _, sleep = _sleeper()
    rering.placed("CA1", CARD)
    rering.answered("CA1")
    assert rering.missed("CA1", send=send, sleep=sleep) is None
    assert rering.missed("CA-never-placed", send=send, sleep=sleep) is None
    assert calls == []


@pytest.mark.parametrize("env,expected", [({}, 2), ({"CALL_RERINGS": "0"}, 0),
                                          ({"CALL_RERINGS": "9"}, 4),
                                          ({"CALL_RERINGS": "x"}, 2),
                                          ({"ATLAS_NO_RERING": "1"}, 0)])
def test_the_number_of_re_rings_is_bounded_and_can_be_turned_off(monkeypatch, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert rering.limit() == expected


async def test_a_failed_dial_is_reported_not_raised(memory_backend):
    memory_backend.responses["SELECT count(*) AS n FROM proposals"] = {"n": 1}

    async def send(msg):
        raise RuntimeError("twilio down")
    _, sleep = _sleeper()
    rering.placed("CA1", CARD)
    assert await rering.missed("CA1", send=send, sleep=sleep) == "failed"
