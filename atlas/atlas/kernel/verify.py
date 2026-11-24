"""Read-back verification: did the action's effect really happen? (RA-D1)

`executor.execute` returning ok is evidence that an operation was ATTEMPTED,
not that the outcome exists. A verifier reads the authoritative record back
from the provider -- the issue by its iid, the media by its id, the event by
its id -- compares it with what was approved, and says one of three things:

    verified      the read-back shows the approved effect, with evidence
    contradicted  the read-back shows it is missing or wrong (names what)
    unknown       it could not be read back, so nobody knows

The contract, enforced here rather than by convention:

  * A verifier only READS. It never posts, sends, retries or changes anything.
    One that cannot read back returns unknown, never verified.
  * No verifier -> unknown "no verifier". Never verified by default.
  * A verifier that raises -> unknown, its error redacted by `_http.explain`.
  * verified with no evidence is downgraded to unknown: "verified" means a
    read-back was seen, and an empty evidence dict is not one.
  * Evidence is short and secret-free: ids, permalinks, read-back fields.
    Keys that look like credentials are dropped, values are redacted.

This file is domain-free, like `contracts.py`. Verifiers live beside the
module they read (`atlas/modules/<module>/readback.py`) and register with
`@verifier("module.action")`; that file is imported lazily, on first use, by
the action's namespace, or by the package its `run` lives in when the two
differ (`stripe.*` is `stripe_billing`). See docs/verification.md.

`reconcile_unknown` is the same idea for a run that has `execute.start` and
no finish (a killed process): it looks the effect up by what was persisted
and never re-sends.
"""
from __future__ import annotations

import asyncio
import enum
import importlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# FailureClass lives in contracts.py (ActionResult carries it); every
# `from ...kernel.verify import FailureClass` keeps working.
from .contracts import ActionResult, FailureClass

log = logging.getLogger("atlas.verify")


class State(enum.StrEnum):
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Verification:
    state: State
    evidence: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    failure_class: FailureClass | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state is State.VERIFIED

    def as_audit(self) -> dict[str, Any]:
        """The shape written to the audit chain as `verify.<state>`."""
        return {"state": str(self.state),
                "failure_class": str(self.failure_class) if self.failure_class else None,
                "reason": self.reason,
                "checked_at": self.checked_at.isoformat(),
                "evidence": self.evidence}


def verified(**evidence: Any) -> Verification:
    return Verification(State.VERIFIED, evidence)


def contradicted(reason: str, failure_class: FailureClass | None = None,
                 **evidence: Any) -> Verification:
    return Verification(State.CONTRADICTED, evidence, failure_class=failure_class,
                        reason=reason)


def unknown(reason: str, failure_class: FailureClass | None = None,
            **evidence: Any) -> Verification:
    return Verification(State.UNKNOWN, evidence, failure_class=failure_class,
                        reason=reason)


# ------------------------------------------------------------------ registry

# (params, result) -> Verification
Verifier = Callable[[dict[str, Any], ActionResult], Awaitable[Verification]]
# (params, started_at) -> Verification
Reconciler = Callable[[dict[str, Any], datetime], Awaitable[Verification]]

_VERIFIERS: dict[str, Verifier] = {}
_RECONCILERS: dict[str, Reconciler] = {}
_LOADED: set[str] = set()

# A read-back is bounded: a hung provider must not hold the executor's
# caller. A LinkedIn read drives a real browser, hence a generous default.
TIMEOUT_S = float(os.getenv("ATLAS_VERIFY_TIMEOUT_S", "90"))


def verifier(action: str) -> Callable[[Verifier], Verifier]:
    """Register `fn(params, result) -> Verification` as the read-back for
    `action`. It may only read."""
    def wrap(fn: Verifier) -> Verifier:
        _VERIFIERS[action] = fn
        return fn
    return wrap


def reconciler(action: str) -> Callable[[Reconciler], Reconciler]:
    """Register `fn(params, started_at) -> Verification` for a run with a
    start and no finish. It looks the effect up; it never re-sends."""
    def wrap(fn: Reconciler) -> Reconciler:
        _RECONCILERS[action] = fn
        return fn
    return wrap


def _import_readback(package: str) -> None:
    """Import `<package>.readback` once. Absent or broken -> nothing
    registered (logged when broken)."""
    if package in _LOADED:
        return
    _LOADED.add(package)
    try:
        importlib.import_module(f"{package}.readback")
    except ModuleNotFoundError as exc:
        # No such package, or a package with no readback.py: no verifier.
        if exc.name and (package == exc.name or package.startswith(exc.name + ".")
                         or exc.name == f"{package}.readback"):
            return
        log.warning("verifiers for %s failed to import: %s", package, exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("verifiers for %s failed to import: %s", package, exc)


def _load(action: str) -> None:
    """Import the readback.py that registers `action`'s verifiers: first
    `atlas/modules/<namespace>/`, then -- when an action's namespace is not
    its package's name (`stripe.*` lives in `stripe_billing`) -- the package
    its `run` was defined in, as the registry holds it."""
    namespace = action.split(".", 1)[0]
    if not namespace.isidentifier():
        return
    base = __package__.rsplit(".", 1)[0] + ".modules"
    _import_readback(f"{base}.{namespace}")
    if action in _VERIFIERS or action in _RECONCILERS:
        return
    from .registry import REGISTRY
    spec = REGISTRY.actions.get(action)
    where = getattr(getattr(spec, "run", None), "__module__", "") or ""
    if where.startswith(base + "."):
        _import_readback(".".join(where.split(".")[:3]))


def has_verifier(action: str) -> bool:
    _load(action)
    return action in _VERIFIERS


def has_reconciler(action: str) -> bool:
    _load(action)
    return action in _RECONCILERS


# ------------------------------------------------------------------ helpers

def _explain(exc: BaseException) -> str:
    from ..modules._http import explain
    return explain(exc)


def classify(exc: BaseException) -> FailureClass | None:
    """A failure class for an exception raised while READING back, or None
    when it says nothing about the cause."""
    import httpx
    status = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status in (401, 403):
        return FailureClass.AUTH
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return FailureClass.TRANSIENT
    if isinstance(exc, httpx.HTTPError) or status == 429 or (
            isinstance(status, int) and status >= 500):
        return FailureClass.TRANSIENT
    return None


_SECRETISH = re.compile(r"(token|secret|password|passwd|credential|api_?key|"
                        r"authorization|cookie|bearer)", re.IGNORECASE)
_MAX_STR = 300
_MAX_ITEMS = 20


def clean_evidence(value: Any, _depth: int = 0) -> Any:
    """Short and secret-free: credential-looking keys dropped, strings
    redacted and cut, lists capped. The audit chain can never be scrubbed."""
    from ..modules._http import redact
    if _depth > 4:
        return "…"
    if isinstance(value, Mapping):
        return {str(k): clean_evidence(v, _depth + 1) for k, v in value.items()
                if not _SECRETISH.search(str(k))}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out = [clean_evidence(v, _depth + 1) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out.append(f"… {len(items) - _MAX_ITEMS} more")
        return out
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = redact(str(value))
    return text if len(text) <= _MAX_STR else text[:_MAX_STR] + "…"


def combine(legs: Mapping[str, Verification]) -> Verification:
    """Several independent read-backs (a reel's platforms, a snapshot per
    instance) as one: any contradicted -> contradicted naming them; else any
    unknown -> unknown naming them; else verified. Never verified on none."""
    if not legs:
        return unknown("nothing to read back")
    evidence = {name: {"state": str(v.state), **({"reason": v.reason} if v.reason else {}),
                       **v.evidence} for name, v in legs.items()}
    bad = [n for n, v in legs.items() if v.state is State.CONTRADICTED]
    if bad:
        classes = {legs[n].failure_class for n in bad} - {None}
        return contradicted(
            "; ".join(f"{n}: {legs[n].reason or 'contradicted'}" for n in bad),
            classes.pop() if len(classes) == 1 else None, **evidence)
    unsure = [n for n, v in legs.items() if v.state is State.UNKNOWN]
    if unsure:
        classes = {legs[n].failure_class for n in unsure} - {None}
        return unknown(
            "; ".join(f"{n}: {legs[n].reason or 'unknown'}" for n in unsure),
            classes.pop() if len(classes) == 1 else None, **evidence)
    return verified(**evidence)


def _finalise(v: Any) -> Verification:
    """Apply the contract to whatever a verifier returned."""
    if not isinstance(v, Verification):
        return unknown(f"verifier returned {type(v).__name__}, not a Verification")
    evidence = clean_evidence(v.evidence or {})
    if v.state is State.VERIFIED and not evidence:
        return unknown("verifier said verified but showed no read-back evidence")
    from ..modules._http import redact
    return Verification(v.state, evidence, v.checked_at, v.failure_class,
                        redact(v.reason)[:_MAX_STR])


async def _bounded(action: str, call: Callable[[], Awaitable[Verification]]
                   ) -> Verification:
    try:
        got = await asyncio.wait_for(call(), timeout=TIMEOUT_S)
    except TimeoutError:
        return unknown(f"read-back timed out after {TIMEOUT_S:.0f}s",
                       FailureClass.TRANSIENT)
    except Exception as exc:
        # Exception, not BaseException: a CancelledError must propagate so a
        # shutdown still cancels the caller (approvals' background receipt).
        log.warning("verifier for %s raised", action, exc_info=True)
        return unknown(f"verifier raised: {_explain(exc)}", classify(exc))
    return _finalise(got)


# ------------------------------------------------------------------ entry points

async def verify(action: str, params: dict[str, Any] | None,
                 result: ActionResult) -> Verification:
    """Read back the effect of a finished `action`. Never raises (except
    cancellation), never acts."""
    if not has_verifier(action):
        return unknown("no verifier")
    fn = _VERIFIERS[action]
    return await _bounded(action, lambda: fn(dict(params or {}), result))


def _row_field(row: Any, name: str) -> Any:
    """A field of an `AuditRow`, a dict, or an asyncpg `Record` -- which is
    what `fetch_all` returns in production, and which is neither a Mapping
    nor has attributes: read by key, then by attribute."""
    if isinstance(row, Mapping):
        return row.get(name)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return getattr(row, name, None)


def started_at(row: Any) -> datetime | None:
    """The start time of an `execute.start` audit row (an `AuditRow` or a
    dict row as `fetch_all` returns it), always aware UTC."""
    at = _row_field(row, "at") or _row_field(row, "started")
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at)
        except ValueError:
            return None
    if not isinstance(at, datetime):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def started_params(row: Any) -> dict[str, Any]:
    """The params recorded on an `execute.start` row, if its payload is
    there to read."""
    payload = _row_field(row, "payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {}
    if isinstance(payload, Mapping):
        data = payload.get("data", payload)
        params = data.get("params") if isinstance(data, Mapping) else None
        return dict(params) if isinstance(params, Mapping) else {}
    return {}


async def reconcile_unknown(action: str, params: dict[str, Any] | None,
                            started_audit_row: Any) -> Verification:
    """For a run with `execute.start` and no finish: look up whether the
    effect exists, by what was persisted and the start time. verified = the
    effect is there; contradicted = it is not; unknown = cannot tell. Never
    re-sends, never retries."""
    at = started_at(started_audit_row)
    if at is None:
        return unknown("the start row carries no readable time")
    if not has_reconciler(action):
        return unknown("no reconciler", started=at.isoformat())
    merged = {**started_params(started_audit_row), **(params or {})}
    fn = _RECONCILERS[action]
    return await _bounded(action, lambda: fn(merged, at))
