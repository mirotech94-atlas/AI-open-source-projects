"""A record of every call ATLAS places, and a receipt for one he took
without deciding (kernel/callrecord.py)."""
from __future__ import annotations

import json

import pytest

from atlas.kernel import callrecord


@pytest.fixture(autouse=True)
def clean():
    callrecord.reset()
    yield
    callrecord.reset()


async def test_a_placed_call_is_written_with_its_opening_question_and_cards(memory_backend):
    await callrecord.placed("CA1", title="#41 — Restart the worker",
                            opening="Hey Miro, it's ATLAS. The poller is stuck.",
                            ask="Want me to restart the worker, yes or no?", pids=[41],
                            incident_key=None, away=True, night=True, attempt=1)
    [(_sql, args)] = [(s, a) for s, a in memory_backend.sql_log if "INSERT INTO voice_calls" in s]
    assert args[0] == "CA1" and args[1] == 1 and args[5] == [41]
    assert args[3].startswith("Hey Miro") and args[4].endswith("yes or no?")
    assert args[7] is True and args[8] is True
    assert callrecord.get("CA1").pids == [41]


async def test_a_key_press_is_appended_and_names_who_decided(memory_backend):
    await callrecord.placed("CA1", title="t", opening="o", ask="a", pids=[41])
    await callrecord.decided("CA1", 41, "approve", "call_out:+15550100", "Done: restarted.")
    [(_sql, args)] = [(s, a) for s, a in memory_backend.sql_log if "decided = decided ||" in s]
    [entry] = json.loads(args[1])
    assert entry == {"pid": 41, "verdict": "approve", "via": "call_out",
                     "result": "Done: restarted."}
    assert "+15550100" not in args[1], "the number is not copied into the record"


async def test_the_end_is_recorded(memory_backend):
    await callrecord.ended("CA1", "missed", "")
    [(_sql, args)] = [(s, a) for s, a in memory_backend.sql_log if "SET outcome" in s]
    assert list(args[:2]) == ["CA1", "missed"] and list(args[3]) == []


async def test_answered_without_a_key_names_the_card_still_waiting(memory_backend):
    await callrecord.placed("CA1", title="#41 — Restart the worker", opening="o",
                            ask="Want me to restart the worker, yes or no?", pids=[41, 42])
    await callrecord.decided("CA1", 41, "approve", "call_out:x")
    title, body = callrecord.answered_note("CA1")
    assert "nothing was decided" in title
    assert "#42 is still waiting" in body and "#41" not in body.split("still waiting")[0].split("\n")[-1]
    assert "nothing ran and nothing was declined" in body


async def test_no_receipt_when_every_card_was_answered_or_there_was_none(memory_backend):
    await callrecord.placed("CA1", title="t", opening="o", ask="a", pids=[41])
    await callrecord.decided("CA1", 41, "decline", "call_out:x")
    assert callrecord.answered_note("CA1") is None
    await callrecord.placed("CA2", title="flycheap.database is failing", opening="o")
    assert callrecord.answered_note("CA2") is None
    assert callrecord.answered_note("CA-unknown") is None


async def test_a_database_that_is_down_stops_nothing(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.execute_sql", broken)
    await callrecord.placed("CA1", title="t", opening="o", ask="a", pids=[41])
    await callrecord.decided("CA1", 41, "approve", "call_out:x")
    await callrecord.ended("CA1", "answered")
    assert callrecord.get("CA1").decided[0]["verdict"] == "approve"


async def test_turn_times_are_kept_and_summarised(memory_backend):
    await callrecord.placed("CA1", title="t", opening="o")
    for ms in (420.4, 1310.0, 650.6):
        callrecord.turn("CA1", ms)
    callrecord.turn("CA-unknown", 5)                      # a call never placed: ignored
    assert callrecord.timing("CA1") == {"turns": 3, "median_ms": 651, "slowest_ms": 1310}
    await callrecord.ended("CA1", "answered", "human")
    [(_sql, args)] = [(s, a) for s, a in memory_backend.sql_log if "SET outcome" in s]
    assert list(args[3]) == [420, 1310, 651]
    assert callrecord.timing("CA-unknown") is None
