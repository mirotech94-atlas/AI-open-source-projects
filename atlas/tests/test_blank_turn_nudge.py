"""A reply that is empty or the copied "(calling tools)" placeholder is
nudged (twice at most) instead of being recorded as "No answer"."""
from __future__ import annotations

import pytest

from atlas.kernel import agents
from atlas.kernel.harness import NUDGE, TOOL_TURN, is_blank_turn
from atlas.kernel.llm import Completion, Need


def test_blank_detection():
    assert is_blank_turn("") and is_blank_turn(f" {TOOL_TURN} ")
    assert not is_blank_turn("Shift report: all fine")


@pytest.mark.asyncio
@pytest.mark.parametrize("blanks,expect", [
    (1, "real answer"),
    (2, "real answer"),
    (3, "No answer: the model ended without one"),
])
async def test_agent_run_nudges(monkeypatch, blanks, expect):
    replies = [TOOL_TURN] * blanks + ["real answer"] * 3
    seen: list[list] = []

    async def fake_think_turn(name, need, system, messages, tools, *, model=None,
                              think_fn=None):
        seen.append(list(messages))
        return Completion(text=replies.pop(0), provider="local", model="m",
                          input_tokens=1, output_tokens=1, tool_calls=None), False

    async def fake_record(*a, **k):
        return None

    monkeypatch.setattr(agents, "think_turn", fake_think_turn)
    monkeypatch.setattr(agents, "record_run", fake_record)
    a = agents.Agent(name="t", title="T", brief="b", system="s", tools=set(),
                     need=Need.DRAFT)
    out = await a.run("task")
    assert out.startswith(expect) or expect in out
    if blanks <= 2:
        assert any(m.content == NUDGE for m in seen[-1])
