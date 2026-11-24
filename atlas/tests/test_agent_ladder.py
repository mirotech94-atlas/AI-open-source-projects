"""`agents.ladder` -- each agent's rung on the autonomy ladder, counted from
the tiers set in code (Miro, 2026-09-28, from the "37 AI agent company"
reel: human-led, human-assisted, fully autonomous).

It only reads what the registry already decided; it never decides a tier.
"""
from __future__ import annotations

import pytest

from atlas.kernel import agents
from atlas.kernel.contracts import Tier
from atlas.kernel.permissions import Decision
from atlas.kernel.registry import REGISTRY
from atlas.kernel.tools import TOOLS, Tool


async def _noop(**_kw):
    return {}


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(REGISTRY, "decisions", dict(REGISTRY.decisions))
    fresh: dict[str, agents.Agent] = {}
    monkeypatch.setattr(agents, "ROSTER", fresh)

    def tool(name: str, action: str | None = None) -> None:
        monkeypatch.setitem(TOOLS, name, Tool(
            name, "d", {"type": "object", "properties": {}},
            read=None if action else _noop, action=action))

    def decide(action: str, tier: Tier) -> None:
        REGISTRY.decisions[action] = Decision(True, tier, "test")

    return fresh, tool, decide


def test_counts_reads_and_each_tier(setup):
    roster, tool, decide = setup
    tool("read_a")
    tool("read_b")
    tool("do_auto", action="x.auto")
    tool("do_ask", action="x.ask")
    tool("do_never", action="x.never")
    decide("x.auto", Tier.AUTO)
    decide("x.ask", Tier.ASK)
    decide("x.never", Tier.NEVER)
    roster["p"] = agents.Agent(name="p", title="P", brief="b", system="s",
                               tools={"read_a", "read_b", "do_auto", "do_ask",
                                      "do_never"})

    assert agents.ladder("p") == {"watches": 2, "acts_alone": 1, "asks_first": 1,
                                  "never": 1, "unknown": 0}


def test_an_action_the_registry_has_no_decision_for_is_unknown_not_auto(setup):
    roster, tool, _decide = setup
    tool("do_mystery", action="x.mystery")
    roster["p"] = agents.Agent(name="p", title="P", brief="b", system="s",
                               tools={"do_mystery"})

    rung = agents.ladder("p")
    assert rung["unknown"] == 1
    assert rung["acts_alone"] == 0


def test_a_missing_tool_is_unknown(setup):
    roster, _tool, _decide = setup
    roster["p"] = agents.Agent(name="p", title="P", brief="b", system="s",
                               tools={"no_such_tool"})

    assert agents.ladder("p")["unknown"] == 1


def test_an_unknown_agent_has_no_ladder(setup):
    assert agents.ladder("nobody") is None
