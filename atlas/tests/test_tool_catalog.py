"""Write tools against the action catalog they claim to wrap.

A write tool is a name an agent can call and an action name it forwards to.
Both halves drift independently: a module drops an action (the old
`inroots.nudge_missing`) and the tool keeps pointing at nothing; an action
gains a required parameter and the tool's schema never offers it, so every
call is refused as "undeclared" or fails with a TypeError on approval.

These tests read every module's `actions()` directly, not the booted
registry: `REGISTRY.load_all()` disables a module whose `requires_env` is
unset before recording a single decision, so a registry-based check would
wrongly call `tiktok_post` or `stripe_retry` dangling on a bare machine.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

import pytest

import atlas.modules as modules_pkg
from atlas.kernel.contracts import Action, Tier
from atlas.kernel.tools import TOOLS
from atlas.tools.builtin import register_all


def _declared_actions() -> dict[str, Action]:
    """Every action any module declares, whatever the environment says."""
    out: dict[str, Action] = {}
    for info in pkgutil.iter_modules(modules_pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = getattr(importlib.import_module(f"atlas.modules.{info.name}"),
                         "MODULE", None)
        if module is None:
            continue
        for action in module.actions():
            out[action.name] = action
    return out


@pytest.fixture
def catalog(monkeypatch) -> dict[str, Action]:
    register_all()
    return _declared_actions()


def _write_tools():
    return sorted((t for t in TOOLS.values() if t.action is not None),
                  key=lambda t: t.name)


def test_every_write_tool_names_an_action_some_module_declares(catalog):
    dangling = {t.name: t.action for t in _write_tools() if t.action not in catalog}
    assert not dangling, f"write tools naming no declared action: {dangling}"


def test_no_write_tool_wraps_a_tier_3_action(catalog):
    for tool in _write_tools():
        assert catalog[tool.action].tier in (Tier.AUTO, Tier.ASK), tool.name


def test_every_write_tool_offers_only_declared_parameters(catalog):
    """The executor refuses any parameter the action did not declare
    (`executor._validate_params`); a schema offering one is a tool that can
    only ever be refused."""
    for tool in _write_tools():
        action = catalog[tool.action]
        offered = set(tool.schema["properties"])
        undeclared = offered - set(action.params)
        assert not undeclared, f"{tool.name} offers undeclared {sorted(undeclared)}"
        not_taken = offered - set(inspect.signature(action.run).parameters)
        assert not not_taken, f"{tool.name} offers {sorted(not_taken)} run() never takes"


def test_every_write_tool_requires_what_the_action_cannot_run_without(catalog):
    for tool in _write_tools():
        run = inspect.signature(catalog[tool.action].run)
        needed = {n for n, p in run.parameters.items()
                  if p.default is inspect.Parameter.empty
                  and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
        missing = needed - set(tool.schema.get("required", []))
        assert not missing, f"{tool.name} does not require {sorted(missing)}"


@pytest.mark.parametrize(("tool", "action"), [
    ("mail_draft_reply", "mail.draft_reply"),
    ("mail_draft_nudge", "mail.draft_nudge"),
    ("screening_book_slot", "screening.book_slot"),
])
def test_the_actions_found_without_a_tool_now_have_one(catalog, tool, action):
    assert tool in TOOLS
    assert TOOLS[tool].action == action
    assert set(TOOLS[tool].schema["properties"]) == set(catalog[action].params)


def test_no_agent_holds_the_screening_booking_tool():
    """`screening.book_slot` is Tier 1 and its one door is the screening
    webhook through `unattended.run_tier1`. An agent holding the tool would
    be a second, unattended way onto his calendar."""
    from atlas.kernel.agents import ROSTER, build_roster
    register_all()
    build_roster()
    holders = [a.name for a in ROSTER.values() if "screening_book_slot" in a.tools]
    assert not holders


def test_no_write_tool_wraps_a_card_only_action(catalog):
    """A card-only send (six agents, 2026-10-27: the outreach, the invite,
    the agreement) is raised by its pass and approved by the owner; an agent
    holding a tool for it would be a second way to propose it."""
    holders = {t.name: t.action for t in _write_tools()
               if getattr(catalog[t.action], "card_only", False)}
    assert not holders, f"write tools wrapping card-only actions: {holders}"
    assert "agency_send_outreach" not in TOOLS


def test_the_unattended_door_never_opens_on_a_tier_2_action(catalog):
    from atlas.kernel.unattended import ALLOWED
    asks = sorted(n for n in ALLOWED if n in catalog and catalog[n].tier is not Tier.AUTO)
    assert not asks, f"Tier 2/3 actions in unattended.ALLOWED: {asks}"
