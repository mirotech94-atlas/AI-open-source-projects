"""`agents.warmup` -- a Tier-1-safe way to give one roster agent a real run.

Before this, the Agents map's LAST RUN and MODEL stayed "never" for any
agent nobody happened to ask a real question, because the only way a row
landed in `agent_runs` was a real chat/voice turn or a handoff. `warmup` runs
the agent through its own `Agent.run()` -- the exact path `route()` takes for
every turn -- on a fixed, read-only prompt, with its tool list forced empty
so it cannot act even if the model tries. It must never fabricate a row: a
model that could not be reached is recorded as a failure, not left as
"never".
"""
from __future__ import annotations

import pytest

from atlas.kernel import agents
from atlas.kernel.llm import Completion, LocalModelUnavailable
from atlas.kernel.tools import Tool

PROBE = agents.Agent(
    name="probe", title="Probe", brief="a test agent", system="system prompt",
    tools={"web_search"}, need=agents.Need.REASON,
)


@pytest.fixture
def roster(monkeypatch):
    monkeypatch.setattr(agents, "ROSTER", {"probe": PROBE})
    return agents.ROSTER


async def test_unknown_agent_raises(roster):
    with pytest.raises(KeyError):
        await agents.warmup("no-such-agent")


async def test_warmup_offers_no_tool_schema_and_records_the_run(
        roster, monkeypatch, memory_backend):
    """The model is never even offered a tool to call -- `tools=None` reaches
    `think_turn`, not a scoped subset -- and the row it produces looks like
    any other agent_runs row: real model, real provider."""
    seen: dict = {}

    async def fake_think_turn(name, need, system, messages, tools, *,
                              model=None, think_fn=None):
        seen["name"], seen["tools"] = name, tools
        seen["prompt"] = messages[-1].content
        return Completion(text="I watch X. I would reach for Y first.",
                          provider="anthropic", model="claude-x"), False

    monkeypatch.setattr(agents, "think_turn", fake_think_turn)
    memory_backend.responses["SELECT model, provider FROM agent_runs"] = {
        "model": "claude-x", "provider": "anthropic"}

    result = await agents.warmup("probe")

    assert seen["tools"] is None
    assert "Do not call any tool" in seen["prompt"]
    assert result.agent == "probe"
    assert result.model == "claude-x"
    assert result.provider == "anthropic"
    assert "I watch X" in result.outcome

    inserts = [sql for sql, _ in memory_backend.sql_log if "INSERT INTO agent_runs" in sql]
    assert len(inserts) == 1
    _, args = next(a for a in memory_backend.sql_log if "INSERT INTO agent_runs" in a[0])
    assert args[0] == "probe"
    assert args[2] == "claude-x"        # model
    assert args[3] == "anthropic"       # provider


async def test_warmup_never_lets_a_hallucinated_tool_call_run(
        roster, monkeypatch, memory_backend):
    """Even if the model invents a tool call anyway, nothing runs: `allowed`
    is the empty set for a warm-up, and `invoke` refuses anything not in it
    before it ever looks the tool up."""
    called = []

    async def spy_read(**_kw):
        called.append(True)
        return {"never": "called"}

    monkeypatch.setitem(
        agents.TOOLS, "web_search",
        Tool(name="web_search", description="d", schema={}, read=spy_read),
    )

    async def fake_think_turn(name, need, system, messages, tools, *,
                              model=None, think_fn=None):
        return Completion(text="calling a tool", provider="anthropic",
                          model="claude-x",
                          tool_calls=[{"name": "web_search", "input": {}}]), False

    monkeypatch.setattr(agents, "think_turn", fake_think_turn)
    memory_backend.responses["SELECT model, provider FROM agent_runs"] = {
        "model": "claude-x", "provider": "anthropic"}

    result = await agents.warmup("probe")

    assert called == []
    assert "probe" == result.agent


async def test_local_model_unavailable_is_recorded_as_a_failure_not_never(
        roster, monkeypatch, memory_backend):
    async def fake_think_turn(*_a, **_kw):
        raise LocalModelUnavailable("ollama unreachable")

    monkeypatch.setattr(agents, "think_turn", fake_think_turn)
    # `Agent.run()` records this itself, with empty model/provider, and does
    # not raise -- so warmup's own readback runs too, matching what a real
    # deploy's Postgres would hold for that row.
    memory_backend.responses["SELECT model, provider FROM agent_runs"] = {
        "model": "", "provider": ""}

    result = await agents.warmup("probe")

    assert result.model == ""
    assert result.provider == ""
    assert "ollama unreachable" in result.outcome
    inserts = [a for a in memory_backend.sql_log if "INSERT INTO agent_runs" in a[0]]
    assert len(inserts) == 1
    assert inserts[0][1][2] == ""       # model
    assert inserts[0][1][3] == ""       # provider


async def test_a_provider_failure_is_recorded_not_raised(
        roster, monkeypatch, memory_backend):
    """`think()` can raise a plain RuntimeError (no provider configured, or
    every provider in the chain failed) -- `Agent.run()` only catches
    `LocalModelUnavailable`, so warmup has to catch the rest itself. Silently
    letting this escape would leave the row as "never", which is exactly the
    bug this task exists to fix."""
    async def fake_think_turn(*_a, **_kw):
        raise RuntimeError("all providers failed for reason")

    monkeypatch.setattr(agents, "think_turn", fake_think_turn)

    result = await agents.warmup("probe")

    assert result.model == ""
    assert result.provider == ""
    assert "all providers failed" in result.outcome
    inserts = [a for a in memory_backend.sql_log if "INSERT INTO agent_runs" in a[0]]
    assert len(inserts) == 1
