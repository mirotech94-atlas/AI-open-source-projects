"""Module discovery and lifecycle. This is what makes ATLAS a platform rather
than an application: `atlas/modules/<name>/` containing a `MODULE` object is
all it takes to add a business, a data source or a capability."""
from __future__ import annotations

import functools
import hashlib
import importlib
import json
import logging
import os
import pkgutil
import sys
from dataclasses import dataclass, field
from typing import Any

from .board_state import SWITCHED_OFF
from .contracts import Action, Check, Module, Tier
from .permissions import Decision, classify

log = logging.getLogger("atlas.registry")


@dataclass
class Registry:
    modules: dict[str, Module] = field(default_factory=dict)
    checks: dict[str, Check] = field(default_factory=dict)
    actions: dict[str, Action] = field(default_factory=dict)
    decisions: dict[str, Decision] = field(default_factory=dict)
    disabled: dict[str, str] = field(default_factory=dict)   # name -> why
    # action name -> the dotted module that declared it, for ACTION_VERSION.
    action_modules: dict[str, str] = field(default_factory=dict)

    def load_all(self, package: str = "atlas.modules") -> None:
        """Every module under `package`, except those named in
        `ATLAS_DISABLED_MODULES` (a comma list). A module switched off that
        way is recorded in `disabled` with that reason, so it reads "off by
        choice", never UNKNOWN and never silently absent (the owner, 2026-09-27:
        "we don't need YouTube")."""
        off = {m.strip().lower() for m in
               os.getenv("ATLAS_DISABLED_MODULES", "").split(",") if m.strip()}
        # The committed list (`board_state.SWITCHED_OFF`) is the same switch,
        # kept in code so a decision survives a redeploy of the env file.
        committed = {k.lower(): v for k, v in SWITCHED_OFF.items()}
        pkg = importlib.import_module(package)
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name.startswith("_"):
                continue
            if info.name.lower() in off:
                self.disabled[info.name] = "switched off (ATLAS_DISABLED_MODULES)"
                log.info("module %s switched off by ATLAS_DISABLED_MODULES",
                         info.name)
                continue
            if info.name.lower() in committed:
                self.disabled[info.name] = (
                    f"switched off (committed: {committed[info.name.lower()]})")
                log.info("module %s switched off by the committed list",
                         info.name)
                continue
            self.load(f"{package}.{info.name}", info.name)

    def load(self, dotted: str, short: str) -> None:
        try:
            mod = importlib.import_module(dotted)
        except Exception as exc:
            self.disabled[short] = f"import failed: {exc}"
            log.exception("module %s failed to import", short)
            return

        instance: Module | None = getattr(mod, "MODULE", None)
        if instance is None:
            self.disabled[short] = "no MODULE object exported"
            return

        missing = [v for v in instance.requires_env if not os.getenv(v)]
        if missing:
            # Loud and disabled beats quietly reporting healthy forever.
            self.disabled[short] = f"missing env: {', '.join(missing)}"
            log.warning("module %s disabled — missing env %s", short, missing)
            return

        self.modules[instance.name] = instance

        for check in instance.checks():
            self.checks[f"{instance.name}.{check.name}"] = check

        for action in instance.actions():
            key = action.name
            # Actions are namespaced `module.verb`, and the prefix must be the
            # module that declared it. Checks were already namespaced by the
            # registry; actions were registered under whatever name the module
            # supplied, which meant an action named without a module prefix was
            # stored under a bare name the deny-list could not reason about.
            expected = f"{instance.name}."
            if not key.startswith(expected):
                self.decisions[key] = Decision(
                    False, Tier.NEVER,
                    f"action {key!r} is not namespaced under module "
                    f"{instance.name!r}")
                log.error("action %s refused: wrong namespace for module %s",
                          key, instance.name)
                continue
            decision = classify(action)
            self.decisions[key] = decision
            if not decision.allowed:
                log.warning("action %s refused: %s", key, decision.reason)
                continue
            self.actions[key] = action
            self.action_modules[key] = dotted

        log.info(
            "loaded %s (%d checks, %d actions)",
            instance.name, len(instance.checks()), len(instance.actions()),
        )

    async def start_modules(self) -> None:
        """Run every loaded module's setup().

        Nothing called this before, so a module that authenticates or opens a
        client in setup() ran unconfigured — and an unconfigured collector that
        answers OK is the worst failure this system has. A module whose setup
        raises is disabled outright, along with its checks and actions, rather
        than being left half-initialised.
        """
        for name, instance in list(self.modules.items()):
            try:
                await instance.setup()
            except Exception as exc:
                log.exception("module %s failed setup", name)
                self.disable(name, f"setup failed: {exc}")

    async def stop_modules(self) -> None:
        """Best-effort teardown. One module failing must not block the rest."""
        for name, instance in list(self.modules.items()):
            try:
                await instance.teardown()
            except Exception as exc:                   # noqa: BLE001
                log.warning("module %s failed teardown: %s", name, exc)

    def disable(self, name: str, why: str) -> None:
        """Remove a module and everything it registered."""
        self.disabled[name] = why
        self.modules.pop(name, None)
        prefix = f"{name}."
        for key in [k for k in self.checks if k.startswith(prefix)]:
            del self.checks[key]
        for key in [k for k in self.actions if k.startswith(prefix)]:
            del self.actions[key]
            self.decisions[key] = Decision(
                False, Tier.NEVER, f"module disabled: {why}")
        log.warning("module %s DISABLED: %s", name, why)

    def action_version(self, name: str) -> str | None:
        """The catalog version of `name`, or None when it is not in the
        catalog. See `action_version` below."""
        action = self.actions.get(name)
        if action is None:
            return None
        return action_version(action, self.action_modules.get(name))

    def catalog_for_prompt(self) -> list[dict[str, object]]:
        """What the reasoner is allowed to see. Tier 1 and 2 only, no callables,
        no secrets — just names, summaries and parameter descriptions."""
        return [
            {
                "name": a.name,
                "tier": int(self.decisions[a.name].tier),
                "summary": a.summary,
                "params": a.params,
                "reversible": a.rollback is not None,
            }
            for a in self.actions.values()
            if not getattr(a, "card_only", False)
            and not getattr(a, "principal_only", False)
        ]


# The version scheme. Changing what goes into the digest changes every
# version, which refuses every held row and approval once: bump it on purpose.
VERSION_SCHEME = "av1"


def _declared_version(action: Any, dotted: str | None) -> Any:
    """ACTION_VERSION as the declaring module states it: a string for every
    action in the module, or a dict naming single actions. Looked up in the
    module that registered the action, then its package
    (`atlas.modules.<namespace>`), then the module `run` was defined in."""
    places: list[str] = []
    if dotted:
        places.append(dotted)
    name = str(getattr(action, "name", ""))
    places.append(f"atlas.modules.{name.split('.', 1)[0]}")
    run: Any = getattr(action, "run", None)
    while isinstance(run, functools.partial):
        run = run.func
    run = getattr(run, "__func__", run)
    owner = getattr(run, "__module__", None)
    if owner:
        places.append(owner)
    for place in places:
        mod = sys.modules.get(place)
        if mod is None or not hasattr(mod, "ACTION_VERSION"):
            continue
        declared = mod.ACTION_VERSION
        if isinstance(declared, dict):
            return declared.get(name)
        return declared
    return None


def action_version(action: Any, dotted: str | None = None) -> str:
    """A stable string for what an action DOES, so a row or an approval
    recorded against one version is never run against another (S08, P04).

    Derived from what defines its behaviour and nothing else: the name, the
    tier written in code, reversibility, whether it has a dry run, who may
    run it, its parameter schema (declared names and the ones `run`
    requires) and the module's ACTION_VERSION, which a module author bumps
    for a semantic change the rest cannot see. Not the summary and not the
    parameter descriptions, so a wording edit does not refuse every held
    row; not the source file, so a deploy that does not change an action
    does not either. Canonical JSON through sha256: the same in every
    process, whatever PYTHONHASHSEED."""
    from .executor import required_params
    tier = getattr(action, "tier", None)
    parts = {
        "name": str(getattr(action, "name", "")),
        "tier": int(tier) if tier is not None else None,
        "irreversible": bool(getattr(action, "irreversible", False)),
        "rollback": getattr(action, "rollback", None) is not None,
        "dry_run": getattr(action, "dry_run", None) is not None,
        "principal_only": bool(getattr(action, "principal_only", False)),
        "card_only": bool(getattr(action, "card_only", False)),
        "params": sorted(getattr(action, "params", None) or {}),
        "required": sorted(required_params(action)),
        "declared": _declared_version(action, dotted),
    }
    return f"{VERSION_SCHEME}:" + _sha(parts)[:32]


def _sha(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def payload_hash(params: Any) -> str:
    """What an approval or a queued row records of its parameters, and what
    dispatch compares (P04, S08): sha256 of the canonical JSON of the params
    as JSON gives them back. Hash what the database returned when you have
    it -- JSONB rewrites some numbers (1e+20 comes back an integer)."""
    if isinstance(params, str):
        params = json.loads(params or "{}")
    return _sha(json.loads(json.dumps(params or {}, default=str)))


REGISTRY = Registry()
