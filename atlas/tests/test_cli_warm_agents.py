"""`python -m atlas.cli warm-agents` -- runs `agents.warmup` over the whole
roster, sequentially, and prints what actually happened for each one. This is
the operator's way to fill in the Agents map's LAST RUN and MODEL without
waiting for Miro to happen to ask each specialist something.
"""
from __future__ import annotations

from atlas import cli
from atlas.kernel import agents as agents_module


async def _no_boot() -> None:
    return None


def _agent(name: str) -> agents_module.Agent:
    return agents_module.Agent(name=name, title=name.title(), brief="b",
                               system="s", tools=set())


async def test_warm_agents_prints_model_and_provider_for_each(
        monkeypatch, capsys):
    monkeypatch.setattr(cli, "boot", _no_boot)
    monkeypatch.setattr(agents_module, "ROSTER",
                        {"ops": _agent("ops"), "inbox": _agent("inbox")})

    async def fake_warmup(name):
        if name == "ops":
            return agents_module.WarmupResult(
                agent=name, outcome="ok", model="claude-x",
                provider="anthropic", seconds=1.23)
        return agents_module.WarmupResult(
            agent=name, outcome="warm-up failed: no LLM provider configured",
            model="", provider="", seconds=0.4)

    monkeypatch.setattr(agents_module, "warmup", fake_warmup)

    await cli.warm_agents()

    out = capsys.readouterr().out
    assert "ops" in out and "anthropic:claude-x" in out and "1.2s" in out
    assert "inbox" in out and "FAILED" in out
    assert "no LLM provider configured" in out


async def test_warm_agents_runs_every_roster_agent_in_sorted_order(
        monkeypatch, capsys):
    monkeypatch.setattr(cli, "boot", _no_boot)
    monkeypatch.setattr(
        agents_module, "ROSTER",
        {"zeta": _agent("zeta"), "alpha": _agent("alpha")})

    seen: list[str] = []

    async def fake_warmup(name):
        seen.append(name)
        return agents_module.WarmupResult(
            agent=name, outcome="ok", model="m", provider="p", seconds=0.1)

    monkeypatch.setattr(agents_module, "warmup", fake_warmup)

    await cli.warm_agents()

    assert seen == ["alpha", "zeta"]
