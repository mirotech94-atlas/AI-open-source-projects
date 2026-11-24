"""Every tool an agent holds is a tool that exists.

Before this, seven agents named tools nobody registered -- the inbox agent all
five of its own -- and the harness dropped them without a word, so inbox ran
with no tools and answered from nothing. A name held for a tool that does not
exist is also a latent grant: the day someone registers `restart_service`,
ops would hold it without anyone having decided that.

This is the review's `tools_check.py` as a test, over the whole roster: the
role agents in agents.py and the business agents in businesses.py.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil

import pytest

import atlas.modules
from atlas.kernel import agents, businesses
from atlas.kernel.registry import REGISTRY
from atlas.kernel.tools import TOOLS
from atlas.tools import builtin


@pytest.fixture
def everything(monkeypatch, memory_backend):
    """Every module loaded with its required env set to a dummy, every
    builtin tool registered, the roster built; the registry restored."""
    for info in pkgutil.iter_modules(atlas.modules.__path__):
        if info.name.startswith("_"):
            continue
        module = getattr(importlib.import_module(f"atlas.modules.{info.name}"),
                         "MODULE", None)
        for name in getattr(module, "requires_env", ()) or ():
            monkeypatch.setenv(name, "x-test-value-not-real")
    saved = {k: dict(getattr(REGISTRY, k)) for k in
             ("modules", "checks", "actions", "decisions", "disabled")}
    for k in saved:
        getattr(REGISTRY, k).clear()
    REGISTRY.load_all()
    builtin.register_all()
    agents.build_roster()
    try:
        yield
    finally:
        for k, v in saved.items():
            d = getattr(REGISTRY, k)
            d.clear()
            d.update(v)


def test_every_agent_tool_is_registered(everything):
    missing = {name: sorted(t for t in agent.tools if t not in TOOLS)
               for name, agent in agents.ROSTER.items()}
    assert {k: v for k, v in missing.items() if v} == {}


def test_every_business_read_and_write_is_registered(everything):
    for biz in businesses.BUSINESSES:
        assert not (biz.reads | biz.writes) - set(TOOLS), biz.name


def test_every_write_an_agent_holds_names_an_action_a_module_declares(everything):
    """A write tool is only as real as its action. `attendance_nudge` names an
    action the attendance module dropped; no agent may hold a tool like it."""
    held = {(name, t) for name, agent in agents.ROSTER.items()
            for t in agent.tools if t in TOOLS and TOOLS[t].action}
    dead = sorted((name, t, TOOLS[t].action) for name, t in held
                  if TOOLS[t].action not in REGISTRY.actions)
    assert dead == []


def test_the_inbox_agent_has_tools_and_keeps_his_mail_local(everything):
    inbox = agents.ROSTER["inbox"]
    assert inbox.tools and inbox.tools <= set(TOOLS)
    assert not inbox.tools & agents.PERPLEXITY_READS


def test_no_agent_holds_an_action_that_does_not_exist_yet(everything):
    """The names the review found, dropped rather than left as grants."""
    held = set().union(*(a.tools for a in agents.ROSTER.values()))
    for name in ("restart_service", "clear_queue", "rerun_job", "calendar_write",
                 "port_scan_self", "oauth_grants", "linkedin_post", "gmail_draft",
                 "fiverr_draft", "flight_search", "browser_history"):
        assert name not in held


async def test_the_harness_says_so_when_it_drops_a_name(monkeypatch, caplog):
    from atlas.kernel import harness
    from atlas.kernel.agents import Agent
    from atlas.kernel.llm import Completion, Need

    async def nothing(*_a, **_k):
        return []

    async def no_lessons(*_a, **_k):
        return ""

    async def quiet(*_a, **_k):
        return None

    async def answer(*_a, **_k):
        return Completion(text="ok", provider="f", model="f"), False

    for name, fn in (("recall", nothing), ("remember", quiet),
                     ("execute_sql", quiet), ("think_turn", answer)):
        monkeypatch.setattr(harness, name, fn)
    monkeypatch.setattr(harness.lessons, "for_task", no_lessons)
    agent = Agent(name="probe", title="P", brief="b", system="s",
                  tools={"web_search", "not_a_tool"}, need=Need.REASON)
    with caplog.at_level(logging.WARNING, logger="atlas.harness"):
        await harness.run_agent(agent, "anything")
    assert any("not_a_tool" in r.getMessage() for r in caplog.records)
