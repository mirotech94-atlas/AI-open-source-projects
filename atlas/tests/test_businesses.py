"""One agent per business (Phase 15.1).

The role agents already cover infrastructure, content and money across every
property at once. A business agent is narrower on purpose: it answers for one
name only, and everything here is about proving that narrowness actually
holds — its tools resolve to real, non-Tier-3 capabilities.

Since 2026-10-28 no business agent sits on the roster (eight agents remain,
kernel/agents.py); the Business records stay as data the modules and the
kept agents read, and these tests check the records themselves.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime

import pytest

from atlas.kernel.agents import ROSTER, build_roster, route
from atlas.kernel.businesses import BUSINESSES, STANDING_RULES
from atlas.kernel.llm import Completion
from atlas.kernel.permissions import denial_reason
from atlas.kernel.registry import REGISTRY
from atlas.kernel.tools import TOOLS
from atlas.tools.builtin import module_brief, module_status, register_all

MODULES_ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas" / "modules"
SKILLS_ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas" / "skills"
BUSINESS_NAMES = {b.name for b in BUSINESSES}


@pytest.fixture(autouse=True)
def _fresh_roster_and_tools():
    """Every test needs the real tool catalog and a freshly built roster —
    build_roster() is idempotent (it re-registers by name), so calling it
    again here does not leak state between tests."""
    register_all()
    build_roster()


# -------------------------------------------------------- one agent, each

def test_every_business_is_a_real_module_and_not_a_roster_agent():
    for biz in BUSINESSES:
        assert (MODULES_ROOT / biz.name / "__init__.py").is_file(), (
            f"{biz.name!r} is in BUSINESSES but atlas/modules/{biz.name}/ "
            "does not exist")
        assert biz.name not in ROSTER, f"{biz.name!r} is retired from the roster"


def test_each_business_agents_memory_starts_with_its_own_namespace():
    for biz in BUSINESSES:
        assert biz.memory[0] == biz.name


def test_the_standing_rules_land_verbatim_in_every_system_prompt():
    for biz in BUSINESSES:
        expected = STANDING_RULES.format(title=biz.title)
        assert expected in biz.system()
        assert biz.what in biz.system()


# --------------------------------------------------------- closed tools

def test_every_business_agents_tools_are_registered_tools():
    """Unknown tools are a bug, not a feature: a business agent scoped to a
    tool name that register_all() never created would fail silently the
    first time it tried to call it."""
    for biz in BUSINESSES:
        unknown = (set(biz.reads) | set(biz.writes)) - set(TOOLS)
        assert not unknown, f"{biz.name}: unregistered tools {unknown}"


def test_no_business_write_tool_names_a_tier_3_action():
    for biz in BUSINESSES:
        for tool_name in set(biz.reads) | set(biz.writes):
            tool = TOOLS[tool_name]
            if tool.action is None:
                continue                                  # a read tool
            assert denial_reason(tool.action) is None, (
                f"{biz.name}: {tool_name} names {tool.action!r}, which is "
                "permanently denied")


def test_business_writes_are_read_tool_free_and_vice_versa():
    """Business.reads and Business.writes partition the tool set cleanly —
    a name cannot be both a read tool and a write tool in TOOLS."""
    for biz in BUSINESSES:
        assert biz.reads.isdisjoint(biz.writes)
        for name in biz.writes:
            assert TOOLS[name].action is not None
        for name in biz.reads:
            assert TOOLS[name].read is not None


@pytest.fixture
def loaded_registry(memory_backend):
    """The real registry, freshly loaded from atlas/modules, restored
    afterwards. `load_all()` only imports modules and registers the checks
    and actions they declare — no module's `setup()` runs here, so nothing
    in this touches a database. `memory_backend` is taken anyway, as a
    belt-and-braces guard in case a future module does something at import
    time it should not."""
    saved = {k: dict(getattr(REGISTRY, k)) for k in
             ("modules", "checks", "actions", "decisions", "disabled")}
    for k in saved:
        getattr(REGISTRY, k).clear()
    REGISTRY.load_all()
    try:
        yield REGISTRY
    finally:
        for k, v in saved.items():
            d = getattr(REGISTRY, k)
            d.clear()
            d.update(v)


def test_every_business_write_tool_names_an_action_a_real_module_declares(
        loaded_registry):
    """A write tool a business holds is only as real as the action it names.
    (`agency_send_outreach` was removed 2026-10-27: an outreach email is a
    card raised by the pass, never a tool an agent calls.)"""
    missing = [
        (biz.name, tool_name, TOOLS[tool_name].action)
        for biz in BUSINESSES
        for tool_name in biz.writes
        if TOOLS[tool_name].action not in loaded_registry.actions
        and TOOLS[tool_name].action not in loaded_registry.decisions
    ]
    assert not missing, (
        f"write tools naming actions no loaded module declares: {missing}")


# ------------------------------------------------------------- routing

async def test_route_menu_lists_no_retired_business(monkeypatch):
    seen_system: list[str] = []

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        seen_system.append(system)
        return Completion(text="inbox", provider="fake", model="fake")

    monkeypatch.setattr("atlas.kernel.agents.think", fake_think)
    await route("what's going on today")

    assert seen_system, "route() never called think()"
    menu = seen_system[0]
    for name in BUSINESS_NAMES:
        assert f"\n{name}:" not in menu, f"{name!r} is retired but still offered"



# --------------------------------------------------------- the two tools

async def test_module_status_carries_observed_at_on_every_check(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return [{"check": "api", "health": "ok", "value": 1.0, "unit": None,
                 "detail": "", "observed_at": datetime(2026, 9, 24, 7, 12, tzinfo=UTC)}]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fake_fetch_all)
    result = await module_status("inroots")

    assert result["module"] == "inroots"
    assert result["checks"][0]["observed_at"] == "2026-09-24T07:12:00+00:00"


async def test_module_status_defaults_a_missing_health_to_unknown(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return [{"check": "never_run", "health": None, "value": None,
                 "unit": None, "detail": None, "observed_at": None}]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fake_fetch_all)
    result = await module_status("inroots")

    assert result["checks"][0]["health"] == "unknown"
    assert result["checks"][0]["observed_at"] is None


async def test_module_status_says_so_when_nothing_is_recorded(monkeypatch):
    async def empty(sql, *args):
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", empty)
    result = await module_status("inroots")
    assert "error" in result


async def test_module_brief_reports_an_unknown_module():
    result = await module_brief("not-a-module")
    assert "error" in result


async def test_module_brief_only_calls_agenda_and_inbox_where_defined(monkeypatch):
    from datetime import timedelta

    from atlas.kernel.contracts import AgendaItem, InboxItem, Severity

    class NoAgendaNoInbox:
        def brief(self):
            return "quiet"

    class WithBoth:
        def brief(self):
            return "busy"

        async def agenda(self, within):
            assert within == timedelta(hours=72)
            return (AgendaItem(when=datetime(2026, 9, 24, tzinfo=UTC), title="renewal"),)

        async def inbox(self, limit):
            assert limit == 5
            return (InboxItem(at=datetime(2026, 9, 24, tzinfo=UTC), sender="x",
                              subject="y", urgency=Severity.NORMAL),)

    monkeypatch.setitem(REGISTRY.modules, "quiet_mod", NoAgendaNoInbox())
    monkeypatch.setitem(REGISTRY.modules, "busy_mod", WithBoth())

    quiet = await module_brief("quiet_mod")
    assert quiet["brief"] == "quiet"
    assert "agenda" not in quiet and "inbox" not in quiet

    busy = await module_brief("busy_mod")
    assert busy["brief"] == "busy"
    assert busy["agenda"][0]["title"] == "renewal"
    assert busy["inbox"][0]["subject"] == "y"


# ------------------------------------------------------------ no executor

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


@pytest.mark.parametrize("path", [
    pathlib.Path(__file__).resolve().parent.parent / "atlas" / "kernel" / "businesses.py",
    *sorted((SKILLS_ROOT / f"{n}.py") for n in
            ("exampleco", "fiverr", "agency")),
])
def test_no_file_here_imports_the_executor(path: pathlib.Path):
    """Invariant 1: collectors and skills only read. A business agent
    proposes tools by name; nothing in this task's files may reach the one
    path that acts."""
    assert path.is_file(), path
    imports = _imports_of(path)
    assert not any("executor" in mod for mod in imports), (
        f"{path.name} imports the executor: {imports}")
