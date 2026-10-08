"""The catalog action version (S08, P04): what it is derived from, and what
it deliberately ignores."""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

from atlas.kernel import registry
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.registry import Registry, action_version, payload_hash

ROOT = Path(__file__).resolve().parent.parent


async def _run(target: str, force: bool = False) -> ActionResult:
    return ActionResult(True, "ran", evidence={"id": target})


async def _dry(target: str, force: bool = False) -> ActionResult:
    return ActionResult(True, "would run")


async def _undo(_token: str) -> ActionResult:
    return ActionResult(True, "undone")


def _action(**over) -> Action:
    base = {"name": "stubver.restart", "tier": Tier.ASK, "summary": "restart it",
            "run": _run, "dry_run": _dry, "rollback": _undo,
            "params": {"target": "which one", "force": "skip the checks"}}
    base.update(over)
    return Action(**base)


def test_the_version_is_a_stable_scheme_tagged_digest():
    v = action_version(_action())
    assert v == action_version(_action())
    scheme, digest = v.split(":")
    assert scheme == registry.VERSION_SCHEME == "av1"
    assert len(digest) == 32 and int(digest, 16) >= 0


def test_wording_does_not_change_the_version():
    """A summary or a parameter description edit is not a new action; it
    must not refuse every held row."""
    assert action_version(_action(summary="restart the thing")) == \
        action_version(_action())
    assert action_version(_action(params={"target": "the box",
                                          "force": "no checks"})) == \
        action_version(_action())


def test_what_the_action_does_changes_the_version():
    base = action_version(_action())
    changed = [
        _action(tier=Tier.AUTO),                          # code-defined tier
        _action(rollback=None, irreversible=True),        # reversibility
        _action(params={"target": "which one"}),          # param schema
        _action(dry_run=None),                            # no preview any more
        _action(principal_only=True),                     # who may run it
        _action(name="stubver.reboot"),
    ]
    versions = {action_version(a) for a in changed}
    assert base not in versions and len(versions) == len(changed)


def test_a_newly_required_parameter_changes_the_version():
    async def stricter(target: str, force: bool) -> ActionResult:
        return ActionResult(True, "ran")

    assert action_version(_action(run=stricter)) != action_version(_action())


def test_the_module_declared_action_version_is_folded_in(monkeypatch):
    """A semantic change the schema cannot see: the module author bumps
    ACTION_VERSION. Found from the module `run` lives in (here, this one);
    a dict bumps single actions."""
    this = sys.modules[__name__]
    base = action_version(_action())
    monkeypatch.setattr(this, "ACTION_VERSION", "2", raising=False)
    bumped = action_version(_action())
    assert bumped != base
    monkeypatch.setattr(this, "ACTION_VERSION", {"stubver.other": "9"},
                        raising=False)
    assert action_version(_action()) == base
    monkeypatch.setattr(this, "ACTION_VERSION", {"stubver.restart": "2"},
                        raising=False)
    assert action_version(_action()) != base


def test_the_registering_module_is_asked_first(monkeypatch):
    import types
    mod = types.ModuleType("stubver_pkg")
    mod.ACTION_VERSION = "7"
    monkeypatch.setitem(sys.modules, "stubver_pkg", mod)
    assert action_version(_action(), "stubver_pkg") != action_version(_action())


def test_the_registry_answers_for_its_catalog_only():
    reg = Registry()
    action = _action()
    reg.actions[action.name] = action
    assert reg.action_version(action.name) == action_version(action)
    assert reg.action_version("stubver.nothing") is None


def test_the_version_is_the_same_in_another_process():
    """Canonical JSON through sha256: no hash(), no id(), no set order."""
    code = (
        "from tests.test_action_version import _action;"
        "from atlas.kernel.registry import action_version;"
        "print(action_version(_action()))")
    seen = set()
    for seed in ("1", "4242"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, check=True)
        seen.add(out.stdout.strip())
    assert seen == {action_version(_action())}


def test_the_payload_hash_reads_params_as_json_gives_them_back():
    params = {"b": [1, 2], "a": {"y": "é", "x": None}}
    assert payload_hash(params) == payload_hash(dict(reversed(params.items())))
    import json
    assert payload_hash(json.dumps(params)) == payload_hash(params)
    assert payload_hash(params) != payload_hash({**params, "a": {"y": "e"}})
    assert payload_hash(None) == payload_hash({})


def test_a_replaced_action_is_versioned_as_it_is_now():
    """The registry computes on every call, so a redeploy (or a test that
    swaps the entry) is seen at once."""
    reg = Registry()
    reg.actions["stubver.restart"] = _action()
    before = reg.action_version("stubver.restart")
    reg.actions["stubver.restart"] = dataclasses.replace(
        _action(), params={"target": "which one"})
    assert reg.action_version("stubver.restart") != before
