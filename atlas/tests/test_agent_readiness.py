"""`agents.readiness` -- whether a roster agent can actually run, decided in
code from what is already loaded, never guessed at runtime (Miro, 2026-09-27:
"every agent that is READY ... runs on its schedule, always; each gets a
readiness preflight before it runs").

Two things it checks, and nothing else:
  * every tool the agent lists resolves in `TOOLS`, and if that tool's data
    comes from one specific module (`agents.READ_TOOL_MODULE` for a read, the
    action's own namespace for a write), that module is loaded;
  * its model route resolves to at least one provider -- the local model for
    a `Need.PRIVATE` agent, the pin-or-Need chain `think()` itself would try
    for everyone else.

Also: the shift schedule's pure functions (`shift_cadence_hours`,
`shift_hour`, `next_shift_at`, `shift_due`), which the scheduler step and
`/api/agents` both read from, so they can never disagree with each other.
"""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.kernel import agents, llm
from atlas.kernel.registry import REGISTRY
from atlas.kernel.tools import TOOLS, Tool

BUILTIN_PATH = (
    Path(__file__).resolve().parent.parent / "atlas" / "tools" / "builtin.py"
)


# --------------------------------------------------- READ_TOOL_MODULE is complete

def _modules_of_import(node: ast.ImportFrom) -> set[str]:
    """Every module name one `ImportFrom` names, in either of the two forms
    builtin.py actually uses: `from ..modules.youtube import api` (the name
    sits in `node.module`) and `from ..modules import mail` (it sits in
    `node.names` instead). Never a private helper (`_perplexity`, `_web`,
    ...) -- registry.load_all() skips those, so a tool reaching one is never
    module-gated."""
    if node.level != 2 or node.module is None:
        return set()
    parts = node.module.split(".")
    if not parts or parts[0] != "modules":
        return set()
    if len(parts) >= 2:
        return set() if parts[1].startswith("_") else {parts[1]}
    # `from ..modules import <name>[, <name2>, ...]`
    return {alias.name for alias in node.names if not alias.name.startswith("_")}


def _discover_read_tool_modules() -> dict[str, str]:
    """Ground truth, derived from `atlas/tools/builtin.py` itself: for every
    tool registered with `read=`, the one module its underlying function
    imports from -- either a top-level aliased import (`calendar_free_slots`)
    or an import inside the function's own body (`youtube_stats`). A
    function importing from more than one distinct module, or from none,
    contributes nothing here (checked separately below)."""
    tree = ast.parse(BUILTIN_PATH.read_text())
    body = tree.body

    # Every locally-defined function's own imports, keyed by function name.
    local_modules: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            found: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom):
                    found |= _modules_of_import(sub)
            local_modules[node.name] = found

    # Top-level aliased imports, keyed by the LOCAL name bound in this file
    # (the alias `calendar_free_slots`, not the real `free_slots_tool`).
    top_level_modules: dict[str, str] = {}
    for node in body:
        if isinstance(node, ast.ImportFrom):
            modules = _modules_of_import(node)
            if len(modules) == 1:
                for alias in node.names:
                    top_level_modules[alias.asname or alias.name] = next(iter(modules))

    # The `reads = [...]` list literal: each `Tool("name", ..., read=ref)`.
    reads_modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "reads"):
            continue
        assert isinstance(node.value, ast.List), "reads is expected to be a list literal"
        for call in node.value.elts:
            if not isinstance(call, ast.Call):
                continue
            tool_name = None
            if call.args and isinstance(call.args[0], ast.Constant):
                tool_name = call.args[0].value
            read_ref = None
            for kw in call.keywords:
                if kw.arg == "read" and isinstance(kw.value, ast.Name):
                    read_ref = kw.value.id
            if tool_name is None or read_ref is None:
                continue
            modules = local_modules.get(read_ref)
            if modules:
                if len(modules) == 1:
                    reads_modules[tool_name] = next(iter(modules))
                # More than one distinct module: ambiguous, contributes
                # nothing -- none of today's read tools hit this branch.
                continue
            if read_ref in top_level_modules:
                reads_modules[tool_name] = top_level_modules[read_ref]
    return reads_modules


def test_read_tool_module_map_matches_builtin_py_exactly():
    """`agents.READ_TOOL_MODULE` is hand-curated (a `Tool` has no `module`
    field, and builtin.py is off limits to this task) -- this is the guard
    against it silently drifting from what builtin.py actually imports."""
    discovered = _discover_read_tool_modules()
    assert discovered, "the AST scan found nothing; it is probably broken"
    assert agents.READ_TOOL_MODULE == discovered


def test_every_read_tool_module_name_is_a_real_module_folder():
    modules_dir = BUILTIN_PATH.parent.parent / "modules"
    real = {p.name for p in modules_dir.iterdir() if p.is_dir()}
    for tool, module in agents.READ_TOOL_MODULE.items():
        assert module in real, f"{tool!r} names {module!r}, not a real module folder"


# --------------------------------------------------------------- readiness()

@pytest.fixture
def clean_registry(monkeypatch):
    monkeypatch.setattr(REGISTRY, "modules", dict(REGISTRY.modules))
    monkeypatch.setattr(REGISTRY, "disabled", dict(REGISTRY.disabled))
    monkeypatch.setattr(REGISTRY, "actions", dict(REGISTRY.actions))
    monkeypatch.setattr(REGISTRY, "decisions", dict(REGISTRY.decisions))
    return REGISTRY


@pytest.fixture
def roster(monkeypatch):
    # `agents.py` does `from .llm import ROUTES`, a name binding to the SAME
    # dict object `llm.ROUTES` names -- so a test mutates it in place
    # (`monkeypatch.setitem`/`delitem`, both auto-reverted) rather than
    # replacing the object, which would only be visible through whichever
    # name still points at the new one.
    fresh: dict[str, agents.Agent] = {}
    monkeypatch.setattr(agents, "ROSTER", fresh)
    return fresh


def _register_tool(monkeypatch, name: str, *, action: str | None = None) -> None:
    monkeypatch.setitem(
        TOOLS, name,
        Tool(name, "d", {"type": "object", "properties": {}},
             read=(None if action else _noop), action=action))


async def _noop(**_kw):
    return {}


def test_ready_when_every_tool_resolves_and_the_route_resolves(
        clean_registry, roster, monkeypatch):
    monkeypatch.setitem(llm.ROUTES, llm.Need.REASON, [object()])
    _register_tool(monkeypatch, "sql_query")
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools={"sql_query"}, need=llm.Need.REASON)

    ready, reasons = agents.readiness("probe")
    assert ready is True
    assert reasons == []


def test_a_tool_of_a_disabled_module_names_the_module_and_the_reason(
        clean_registry, roster, monkeypatch):
    monkeypatch.setitem(llm.ROUTES, llm.Need.REASON, [object()])
    REGISTRY.disabled["youtube"] = "missing env: YOUTUBE_API_KEY"
    monkeypatch.setitem(
        TOOLS, "youtube_stats",
        Tool("youtube_stats", "d", {"type": "object", "properties": {}},
             read=_noop))
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools={"youtube_stats"}, need=llm.Need.REASON)

    ready, reasons = agents.readiness("probe")
    assert ready is False
    assert any("youtube" in r and "missing env: YOUTUBE_API_KEY" in r
               for r in reasons)


def test_a_write_tools_module_comes_from_the_actions_namespace(
        clean_registry, roster, monkeypatch):
    monkeypatch.setitem(llm.ROUTES, llm.Need.REASON, [object()])
    REGISTRY.disabled["guardian"] = "setup failed: connection refused"
    _register_tool(monkeypatch, "guardian_contain", action="guardian.contain")
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools={"guardian_contain"}, need=llm.Need.REASON)

    ready, reasons = agents.readiness("probe")
    assert ready is False
    assert any("guardian" in r for r in reasons)


def test_an_unregistered_tool_is_named_not_in_tools(clean_registry, roster, monkeypatch):
    monkeypatch.setitem(llm.ROUTES, llm.Need.REASON, [object()])
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools={"no_such_tool"}, need=llm.Need.REASON)

    ready, reasons = agents.readiness("probe")
    assert ready is False
    assert any("no_such_tool" in r for r in reasons)


def test_a_disabled_reason_never_reaches_readiness_unredacted(
        clean_registry, roster, monkeypatch):
    """`REGISTRY.disabled` can hold raw exception text (`import failed:
    {exc}` / `setup failed: {exc}`) -- readiness must never let a secret in
    that text reach a prompt or a screen unredacted."""
    monkeypatch.setitem(llm.ROUTES, llm.Need.REASON, [object()])
    monkeypatch.setattr(
        "atlas.modules._http.secret_values", lambda: {"sk-super-secret"})
    REGISTRY.disabled["mail"] = "setup failed: token sk-super-secret rejected"
    monkeypatch.setitem(
        TOOLS, "mail_suggest_labels",
        Tool("mail_suggest_labels", "d", {"type": "object", "properties": {}},
             read=_noop))
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools={"mail_suggest_labels"}, need=llm.Need.REASON)

    ready, reasons = agents.readiness("probe")
    assert ready is False
    joined = " ".join(reasons)
    assert "sk-super-secret" not in joined
    assert "<redacted>" in joined


def test_no_route_resolves_names_the_need(clean_registry, roster, monkeypatch):
    monkeypatch.delitem(llm.ROUTES, llm.Need.REASON, raising=False)
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools=set(), need=llm.Need.REASON)

    ready, reasons = agents.readiness("probe")
    assert ready is False
    assert any("reason" in r.lower() for r in reasons)


def test_private_agent_needs_the_local_model_named_plainly(
        clean_registry, roster, monkeypatch):
    """A PRIVATE agent is never satisfied by `ROUTES[PRIVATE]` alone: Miro can
    opt a cloud model into that route (`ATLAS_PRIVATE_ALLOW_CLOUD`), and that
    is exactly the case this check exists to catch."""
    class FakeCloud:
        name = "anthropic"

    monkeypatch.setitem(llm.ROUTES, llm.Need.PRIVATE, [FakeCloud()])
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools=set(), need=llm.Need.PRIVATE)

    ready, reasons = agents.readiness("probe")
    assert ready is False
    assert any("local model" in r for r in reasons)


def test_private_agent_ready_when_the_local_model_is_configured(
        clean_registry, roster, monkeypatch):
    class FakeLocal:
        name = "ollama"
        local = True
        model = "llama3.2:3b"

    monkeypatch.setitem(llm.ROUTES, llm.Need.PRIVATE, [FakeLocal()])
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools=set(), need=llm.Need.PRIVATE)

    ready, reasons = agents.readiness("probe")
    assert ready is True
    assert reasons == []


def test_a_pinned_model_that_resolves_is_ready_even_with_no_need_route(
        clean_registry, roster, monkeypatch):
    """Task 53: a business agent pinned to a provider:model. Mirrors
    `think()`'s own resolution -- the pin is tried before the Need's chain,
    so a resolving pin is enough even if REASON itself has nothing."""
    monkeypatch.delitem(llm.ROUTES, llm.Need.REASON, raising=False)
    import os
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    roster["probe"] = agents.Agent(
        name="probe", title="Probe", brief="b", system="s",
        tools=set(), need=llm.Need.REASON,
        model="openrouter:nousresearch/hermes-4-405b")

    ready, reasons = agents.readiness("probe")
    assert ready is True
    assert reasons == []
    del os.environ["OPENROUTER_API_KEY"]


def test_unknown_agent_is_not_ready(clean_registry, roster):
    ready, reasons = agents.readiness("no-such-agent")
    assert ready is False
    assert reasons


# ------------------------------------------------------------- shift schedule

def test_shift_cadence_defaults_to_daily(monkeypatch):
    monkeypatch.delenv("AGENT_SHIFT_PROBE", raising=False)
    assert agents.shift_cadence_hours("probe") == 24.0


def test_shift_cadence_zero_means_off(monkeypatch):
    monkeypatch.setenv("AGENT_SHIFT_PROBE", "0")
    assert agents.shift_cadence_hours("probe") == 0.0


def test_shift_cadence_reads_a_per_agent_override(monkeypatch):
    monkeypatch.setenv("AGENT_SHIFT_PROBE", "6")
    assert agents.shift_cadence_hours("probe") == 6.0


def test_shift_hour_is_stable_across_processes(monkeypatch):
    """Not `hash()`: PYTHONHASHSEED salts it per process, which would move
    every agent's slot on every restart."""
    a = agents.shift_hour("ops")
    b = agents.shift_hour("ops")
    assert a == b
    assert 0 <= a < 24


def test_shift_off_has_no_next_shift(monkeypatch):
    monkeypatch.setenv("AGENT_SHIFT_PROBE", "0")
    now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    assert agents.next_shift_at("probe", None, now) is None
    assert agents.shift_due("probe", None, now) is False


def test_never_shifted_is_not_due_before_its_slot_hour(monkeypatch):
    monkeypatch.delenv("AGENT_SHIFT_PROBE", raising=False)
    hour = agents.shift_hour("probe")
    before = datetime(2026, 9, 27, 0, 0, tzinfo=UTC).replace(
        hour=(hour - 1) % 24 if hour > 0 else 23)
    if hour == 0:
        pytest.skip("probe's slot is midnight; the 'before' case does not apply")
    assert agents.shift_due("probe", None, before) is False


def test_never_shifted_is_due_once_its_slot_hour_passes(monkeypatch):
    monkeypatch.delenv("AGENT_SHIFT_PROBE", raising=False)
    hour = agents.shift_hour("probe")
    now = datetime(2026, 9, 27, 0, 0, tzinfo=UTC).replace(hour=hour)
    assert agents.shift_due("probe", None, now) is True


def test_default_cadence_is_not_due_again_the_same_day(monkeypatch):
    monkeypatch.delenv("AGENT_SHIFT_PROBE", raising=False)
    hour = agents.shift_hour("probe")
    last = datetime(2026, 9, 27, 0, 0, tzinfo=UTC).replace(hour=hour)
    now = last + timedelta(hours=1)
    assert agents.shift_due("probe", last, now) is False


def test_default_cadence_is_due_the_next_day(monkeypatch):
    monkeypatch.delenv("AGENT_SHIFT_PROBE", raising=False)
    hour = agents.shift_hour("probe")
    last = datetime(2026, 9, 27, 0, 0, tzinfo=UTC).replace(hour=hour)
    now = last + timedelta(days=1)
    assert agents.shift_due("probe", last, now) is True


def test_custom_cadence_is_due_after_n_hours_elapsed(monkeypatch):
    monkeypatch.setenv("AGENT_SHIFT_PROBE", "6")
    last = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    assert agents.shift_due("probe", last, last + timedelta(hours=5)) is False
    assert agents.shift_due("probe", last, last + timedelta(hours=6)) is True


def test_next_shift_at_and_shift_due_never_disagree(monkeypatch):
    """One source of truth: `shift_due := next_shift_at <= now`."""
    monkeypatch.setenv("AGENT_SHIFT_PROBE", "6")
    last = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)
    now = last + timedelta(hours=7)
    nxt = agents.next_shift_at("probe", last, now)
    assert nxt is not None
    assert (nxt <= now) == agents.shift_due("probe", last, now)
