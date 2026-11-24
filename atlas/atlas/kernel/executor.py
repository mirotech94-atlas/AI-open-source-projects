"""The only component that performs actions.

Nothing else in ATLAS may call a module's `run`. Everything funnels through
`execute`, so the tier check, the dry run, the rate limit and the audit entry
cannot be bypassed by a code path someone adds later.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from . import lessons
from .audit import audit
from .contracts import Action, ActionResult, FailureClass, Tier
from .registry import REGISTRY, payload_hash

log = logging.getLogger("atlas.executor")

_recent: dict[str, deque[float]] = defaultdict(deque)

# `ActionResult.evidence` for `execute.finish`: JSON, capped so a large
# blob -- a full readback, say -- cannot inflate one audit row without limit.
_EVIDENCE_CAP_BYTES = 4096


def _fits(candidate: dict[str, Any]) -> bool:
    encoded = json.dumps(candidate, default=str, sort_keys=True)
    return len(encoded.encode("utf-8")) <= _EVIDENCE_CAP_BYTES


def _capped_evidence(evidence: dict[str, Any]) -> Any:
    """`evidence` as it reaches `actions_log`. Unchanged when it already
    fits; otherwise a small object carrying a `truncated` marker, the
    original size, and as much of it as fits -- so a readback diff still
    reaches the audit row instead of being silently dropped for being too
    big to record at all.

    "Fits" means the wrapper itself, re-encoded, is within the cap -- not
    the raw slice taken from the original. JSON-escaping a quote or a
    backslash doubles it, and a non-ASCII character becomes a 6-byte
    `\\uXXXX` escape once `json.dumps` (no `ensure_ascii=False`, matching
    `audit()`'s own call) gets hold of it, so a byte-for-byte slice of the
    original can come back well over budget once it is re-serialised. Binary
    search over how much of the raw bytes to keep, decoding each candidate
    slice permissively since a cut can land mid-character.
    """
    if not evidence:
        return {}
    # Defence in depth: the audit row is permanent and hash-chained, so a
    # secret that rode in on an action's evidence could never be scrubbed
    # afterwards. Every action is meant to keep its evidence clean; the
    # kernel redacts once more before it is written down.
    from ..modules._http import redact
    text = redact(json.dumps(evidence, default=str, sort_keys=True))
    raw = text.encode("utf-8")
    total = len(raw)
    if total <= _EVIDENCE_CAP_BYTES:
        return evidence
    lo, hi, best = 0, min(total, _EVIDENCE_CAP_BYTES), ""
    while lo <= hi:
        mid = (lo + hi) // 2
        head = raw[:mid].decode("utf-8", errors="ignore")
        if _fits({"truncated": True, "bytes": total, "head": head}):
            best = head
            lo = mid + 1
        else:
            hi = mid - 1
    return {"truncated": True, "bytes": total, "head": best}


# The marker `execute` puts in `evidence` on the two refusals that are an
# intentional stop -- watch-only and a pause -- and on nothing else. The
# outbox holds a row without spending an attempt ONLY when it sees this, on a
# result that is also `dispatched=False` and class `disabled`. A dry run's or
# a module's result never reaches a caller with it: a refused dry run gets a
# fresh result below, and whatever `run()` returns is stamped dispatched.
HOLD = "executor_hold"

# The marker `execute` puts in `evidence` when what was approved or queued is
# no longer what would run (S08, P04): the action's catalog version, or the
# params, changed since. Never a HOLD: the row is not waiting for anything.
# Its value says which: "version" or "params". The caller (approvals, the
# outbox) supersedes the card or row and raises a fresh one; nothing is
# re-approved silently.
STALE = "approval_stale"
STALE_VERSION_DETAIL = ("refused: the action changed since it was approved; "
                        "needs a fresh approval")
STALE_PARAMS_DETAIL = ("refused: the parameters changed since they were "
                       "approved; needs a fresh approval")


def stale_of(result: Any) -> str | None:
    """"version" | "params" when `execute` refused because what would run
    is not what was approved or queued (the STALE marker, on a result that
    did not dispatch), else None. What approvals and the outbox branch on,
    never the wording."""
    if getattr(result, "dispatched", None) is not False:
        return None
    evidence = getattr(result, "evidence", None)
    if not isinstance(evidence, dict):
        return None
    what = evidence.get(STALE)
    return str(what) if what else None


# Where a request proves it is the owner (kernel.intent.CAN_APPROVE plus the
# principal check each applies). Not "sms" or an inbound call: caller ID.
# "voice" is set only by kernel.spoken, for the signed-in dashboard, and
# only reaches principal_only actions (the owner, 2026-09-28).
PRINCIPAL_ORIGINS = frozenset({"slack", "call_out", "voice"})


def watch_only() -> bool:
    """Phase 1: ATLAS watches and tells the owner things. It does not act.

    Default ON, and deliberately read at call time rather than captured at
    import, so flipping it is a restart and not a redeploy. It is enforced
    here, in `execute`, rather than in the scheduler or the reasoner: this is
    the single path to the outside world, so a future caller cannot route
    around it.

    Findings are still detected, proposals are still created and still reach
    the owner — that is how he learns which alerts are noise and which thresholds
    are wrong. Nothing executes.
    """
    return os.getenv("ATLAS_WATCH_ONLY", "true").strip().lower() not in (
        "0", "false", "no", "off")


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    action: str
    params: dict[str, Any]
    reason: str
    incident_id: int | None = None
    approved_by: str | None = None      # None means unattended (Tier 1 only)
    # The catalog version (registry.action_version) recorded when the card
    # was raised or the row queued. None: nothing recorded, no check.
    approved_version: str | None = None
    # registry.payload_hash of the params as approved or queued (P04). None:
    # nothing recorded, no check.
    approved_payload_hash: str | None = None


# BUILD_ORDER 9.2 asks for limits per module AND globally, and the per-action
# ceiling is neither. Ten actions at three an hour is thirty writes from one
# integration — which is exactly what a module with a broken collector and a
# confident reasoner produces — and every module running its own allowance at
# once is the blast radius of a correlated incident. These two bound both.
MODULE_MAX_PER_HOUR = int(os.getenv("ATLAS_MODULE_MAX_PER_HOUR", "10"))
GLOBAL_MAX_PER_HOUR = int(os.getenv("ATLAS_GLOBAL_MAX_PER_HOUR", "30"))
# the owner 2026-11-13: the home cameras sent 75 of ~1,200 alerts in 14 h because the shared
# 10-an-hour module ceiling ate them (a car moved on the 2nd driveway and nothing came).
# Cameras get their own, larger allowance; the per-alert caps in cameras/alerts.py and
# clip_alerts.py still bound it. A rate limit, not a permission: tiers are unchanged.
MODULE_CEILINGS: dict[str, int] = {
    "cameras": int(os.getenv("ATLAS_CAMERAS_MAX_PER_HOUR", "60"))}


def _module_of(action: str) -> str:
    """Actions are named `module.verb`, the same split the registry uses."""
    return action.split(".", 1)[0]


def _over_limit(name: str, ceiling: int) -> bool:
    """Has this action already run its hourly allowance?

    Read-only: it does not consume the budget. A fix firing over and over is a
    symptom, not a solution — but a dry run that refuses has not fixed anything
    and must not spend the allowance. `_record_run` is called only once the
    action actually executes.

    Known limitation: this window lives in process memory, so it resets on
    restart and is not shared between workers. A crash-looping container gets a
    fresh budget each time. Moving it to Redis is tracked separately.
    """
    window = _recent[name]
    now = time.monotonic()
    while window and now - window[0] > 3600:
        window.popleft()
    return len(window) >= ceiling


def _record_run(name: str) -> None:
    """Spend all three budgets at once, so they can never disagree about
    whether an action ran."""
    now = time.monotonic()
    module = _module_of(name)
    # a module with its own ceiling (cameras) does not spend the shared global budget,
    # so raising its allowance never lowers or raises anyone else's
    keys = (name, f"module:{module}") if module in MODULE_CEILINGS else (
        name, f"module:{module}", "global:")
    for key in keys:
        _recent[key].append(now)


_REQUIRED_KINDS = (inspect.Parameter.POSITIONAL_ONLY,
                   inspect.Parameter.POSITIONAL_OR_KEYWORD,
                   inspect.Parameter.KEYWORD_ONLY)


def required_params(action: object) -> list[str]:
    """The parameters `action.run` cannot be called without: no default, and
    not `*args` / `**kwargs`. Empty when there is no run or its signature
    cannot be read -- the executor still refuses at run time then."""
    run = getattr(action, "run", None)
    if run is None:
        return []
    try:
        signature = inspect.signature(run)
    except (TypeError, ValueError):
        return []
    return [p.name for p in signature.parameters.values()
            if p.default is inspect.Parameter.empty and p.kind in _REQUIRED_KINDS]


def missing_params(action: object, params: dict | None) -> list[str]:
    """Required parameters absent from `params`. A None or blank string is
    absent too: it is what a model writes for an id it does not know."""
    given = params or {}
    out = []
    for name in required_params(action):
        value = given.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            out.append(name)
    return out


def _validate_params(action: Action, params: dict[str, Any]) -> str | None:
    """Reject parameters the action did not declare, and required ones that
    are missing.

    The action NAME comes from a closed catalog, but the parameter dict is
    produced by the model verbatim. A closed catalog is a much weaker guarantee
    if the arguments are open, so anything the action did not declare in
    `params` is refused rather than forwarded into `run(**params)`.

    A missing required parameter is refused here too, as policy -- the same
    test `approvals.propose()` makes before a card exists. Tier 1 reaches
    `run(**params)` by four paths that never pass through `propose()`
    (tools.invoke, intent.act, the scheduler's diagnose pick, the marketing
    plan); there a missing one raised TypeError inside `run`, which was
    recorded as a `failed_action` lesson -- a policy refusal counted as a
    mistake, the thing the ledger is careful never to learn from.
    """
    declared = set(action.params)
    supplied = set(params or {})
    undeclared = supplied - declared
    if undeclared:
        return f"undeclared parameter(s): {', '.join(sorted(undeclared))}"
    missing = missing_params(action, params)
    if missing:
        return f"missing parameter(s): {', '.join(missing)}"
    return None


def _explain(exc: BaseException) -> str:
    """Why it raised, secret-free (`_http.explain`): status and path for an
    HTTP error, the redacted message otherwise. For the audit and the card."""
    from ..modules._http import explain
    return explain(exc)


def _brief(exc: BaseException) -> str:
    """One line, no URL, no query string, no token."""
    response = getattr(exc, "response", None)
    request = getattr(exc, "request", None)
    if response is not None and hasattr(response, "status_code"):
        host = getattr(getattr(request, "url", None), "host", "") or "the API"
        return f"HTTP {response.status_code} from {host}"
    return type(exc).__name__


async def execute(req: ExecutionRequest) -> ActionResult:
    action = REGISTRY.actions.get(req.action)
    if action is None:
        await audit("execute.refused", req.action, {"why": "not in catalog"})
        return ActionResult(False, "no such action in the catalog",
                            failure_class=FailureClass.VALIDATION,
                            dispatched=False)

    decision = REGISTRY.decisions.get(req.action)
    if decision is None:
        # Registered without a decision should be impossible, but crashing on a
        # KeyError loses the audit record of the refusal.
        await audit("execute.refused", req.action, {"why": "no permission decision"})
        return ActionResult(False, "refused: no permission decision on record",
                            failure_class=FailureClass.POLICY,
                            dispatched=False)
    if not decision.allowed:
        await audit("execute.refused", req.action, {"why": decision.reason})
        return ActionResult(False, f"refused: {decision.reason}",
                            failure_class=FailureClass.POLICY,
                            dispatched=False)

    if getattr(action, "principal_only", False) and (
            (req.approved_by or "").split(":", 1)[0] not in PRINCIPAL_ORIGINS):
        await audit("execute.refused", req.action, {"why": "principal only"})
        return ActionResult(False, "refused: only the owner, asking directly, can run this",
                            failure_class=FailureClass.POLICY,
                            dispatched=False)

    # The dashboard door (kernel.spoken) records "voice:" for a request, not
    # for a card: it is never the approval a Tier 2 action needs.
    if decision.tier is Tier.ASK and (
            req.approved_by or "").split(":", 1)[0] == "voice":
        await audit("execute.refused", req.action, {"why": "tier 2 by voice door"})
        return ActionResult(False, "refused: a Tier 2 action needs its card approved",
                            failure_class=FailureClass.POLICY,
                            dispatched=False)

    if decision.tier is Tier.ASK and req.approved_by is None:
        await audit("execute.refused", req.action, {"why": "tier 2 unapproved"})
        return ActionResult(False, "tier 2 action reached the executor unapproved",
                            failure_class=FailureClass.POLICY,
                            dispatched=False)

    # What runs must be what was approved or queued (S08). Before the hold
    # checks, so a stale request is refused, not parked; deny by default: a
    # version that cannot be computed is a mismatch.
    if req.approved_version is not None:
        try:
            current = REGISTRY.action_version(req.action)
        except Exception:                               # noqa: BLE001
            current = None
        if current != req.approved_version:
            await audit("execute.superseded", req.action, {
                "why": "action version changed",
                "approved_version": req.approved_version,
                "current_version": current, "approved_by": req.approved_by})
            return ActionResult(False, STALE_VERSION_DETAIL,
                                evidence={STALE: "version"},
                                failure_class=FailureClass.POLICY,
                                dispatched=False)
    # The params he approved are the params that run (P04): a card or a row
    # whose params changed after it was recorded is refused the same way.
    if req.approved_payload_hash is not None:
        try:
            now_hash = payload_hash(req.params)
        except Exception:                               # noqa: BLE001
            now_hash = None
        if now_hash != req.approved_payload_hash:
            await audit("execute.superseded", req.action, {
                "why": "params changed",
                "approved_payload_hash": req.approved_payload_hash,
                "current_payload_hash": now_hash,
                "approved_by": req.approved_by})
            return ActionResult(False, STALE_PARAMS_DETAIL,
                                evidence={STALE: "params"},
                                failure_class=FailureClass.POLICY,
                                dispatched=False)

    if watch_only():
        await audit("execute.watch_only", req.action, {
            "params": req.params,
            "reason": req.reason,
            "tier": int(decision.tier),
            "approved_by": req.approved_by,
        })
        log.warning("watch-only: refusing to run %s (%s)", req.action, req.reason)
        return ActionResult(
            False,
            f"ATLAS is in watch-only mode and did not run {req.action}. "
            "Unset ATLAS_WATCH_ONLY to give it hands.",
            evidence={HOLD: "watch_only"},
            failure_class=FailureClass.DISABLED,
            dispatched=False,
        )

    bad_params = _validate_params(action, req.params)
    if bad_params is not None:
        await audit("execute.refused", req.action, {"why": bad_params})
        return ActionResult(False, f"refused: {bad_params}",
                            failure_class=FailureClass.VALIDATION,
                            dispatched=False)

    module = _module_of(req.action)
    # The kill switch (kernel.pause): durable, global, per module, or per
    # account of a module that posts as several (`instagram:clips`), lifted
    # only by the owner. Unreadable state is a stop, not a go.
    from . import pause as _pause
    try:
        stopped = await _pause.active_for(
            module, _pause.account_of(req.action, req.params))
    except _pause.PauseUnreadable as exc:
        await audit("execute.paused", req.action, {"why": f"pause state unreadable: {exc}"})
        return ActionResult(False, "refused: the pause state could not be read, "
                                   "so nothing is dispatched",
                            failure_class=FailureClass.TRANSIENT,
                            dispatched=False)
    if stopped is not None:
        await audit("execute.paused", req.action,
                    {"scope": stopped.scope, "reason": req.reason})
        return ActionResult(False, f"not run: {stopped.says()}",
                            evidence={HOLD: "paused"},
                            failure_class=FailureClass.DISABLED,
                            dispatched=False)
    for key, ceiling, detail in (
        (req.action, action.max_per_hour,
         (f"{req.action} has already run {action.max_per_hour} times this "
          "hour; escalating instead of repeating")),
        (f"module:{module}", MODULE_CEILINGS.get(module, MODULE_MAX_PER_HOUR),
         (f"the {module} module has already run "
          f"{MODULE_CEILINGS.get(module, MODULE_MAX_PER_HOUR)} actions "
          "this hour; escalating instead of repeating")),
        ("global:", GLOBAL_MAX_PER_HOUR,
         (f"ATLAS has run {GLOBAL_MAX_PER_HOUR} actions across everything "
          "this hour; escalating instead of repeating")),
    ):
        if key == "global:" and module in MODULE_CEILINGS:
            continue
        if _over_limit(key, ceiling):
            await audit("execute.rate_limited", req.action,
                        {"reason": req.reason, "ceiling": key})
            return ActionResult(False, detail, failure_class=FailureClass.BUDGET,
                                dispatched=False)

    # The QA hard gate (kernel/qa_gate.py), every tier: a gated action runs
    # only on content with a standing QA pass. Fail closed.
    from . import qa_gate
    if qa_gate.gated(req.action):
        try:
            await qa_gate.require(req.action, req.params)
        except Exception as exc:                        # noqa: BLE001
            await audit("execute.qa_held", req.action, {"why": str(exc)[:300]})
            return ActionResult(False, f"QA: {exc}", failure_class=FailureClass.QUALITY,
                                dispatched=False)

    # Dry run first, always, even for approved Tier 2 work.
    if action.dry_run is not None:
        preview = await action.dry_run(**req.params)
        await audit("execute.dry_run", req.action,
                    {"ok": preview.ok, "detail": preview.detail})
        if not preview.ok:
            # Always VALIDATION, never the dry run's own class: the preview
            # judged this request against the world and said no, which is
            # "block and name what is wrong" (the detail names it). A dry
            # run must not be able to put a caller into a wait or a retry.
            return ActionResult(False, f"dry run refused: {preview.detail}",
                                failure_class=FailureClass.VALIDATION,
                                dispatched=False)

    # Audit BEFORE acting, so a crash mid-action still leaves a record.
    await audit("execute.start", req.action, {
        "params": req.params,
        "reason": req.reason,
        "tier": int(decision.tier),
        "approved_by": req.approved_by,
        "incident": req.incident_id,
    })

    _record_run(req.action)
    try:
        result = await action.run(**req.params)
    except Exception as exc:
        log.exception("action %s raised", req.action)
        # `explain`, never `str(exc)`: an httpx error prints its URL, and a
        # token in a query string written here is in a hash chain for good.
        await audit("execute.error", req.action, {"error": _explain(exc)})
        # The one line this file contributes to the lessons ledger, and it is
        # deliberately on THIS path only. Everything above is policy — watch
        # only, an unapproved Tier 2, a rate limit, an undeclared parameter, a
        # dry run that said no — and policy working as designed is not a
        # mistake to learn from. Recording those would teach ATLAS to stop
        # proposing anything at all, since watch-only refuses everything by
        # default. An action that really ran and really raised is the mistake.
        await lessons.record(
            "failed_action", f"{req.action} failed: {_brief(exc)}",
            action=req.action, params=req.params)
        # Short and secret-free. The full traceback is in the log; what
        # reaches a Slack card is one line a person can act on, not an httpx
        # dump with a URL in it.
        return ActionResult(False, f"action raised: {_brief(exc)}",
                            dispatched=True)
    # run() was invoked: whatever it returned, it may have reached the
    # provider. Stamped here, by the executor, so a caller (the outbox)
    # never has to infer it from the wording of a detail.
    if isinstance(result, ActionResult):
        result = dataclasses.replace(result, dispatched=True)

    await audit("execute.finish", req.action, {
        "ok": result.ok,
        "detail": result.detail,
        "rollback_token": result.rollback_token,
        # Carried through so a reader of the log (kernel/review.py, in
        # particular) can tell a Tier 1 action that ran on its own from a
        # Tier 2 one a human approved live and that never passed through a
        # `proposals` row -- `execute.start` has both, but nothing tied that
        # back to the matching finish until now.
        "approved_by": req.approved_by,
        "tier": int(decision.tier),
        # Whatever the action wants a reader of the log to be able to see
        # about WHY it decided what it did -- a readback diff, a dry-run
        # count -- capped so one action's evidence cannot inflate a row
        # without limit.
        "evidence": _capped_evidence(result.evidence),
    })
    # RA-D1 read-back hook (docs/verification.md). OFF unless
    # ATLAS_VERIFY_AFTER=on. Detached: it only records a `verify.<state>`
    # audit event later, so the result below is returned unchanged and at
    # once, nothing is retried, and neither an error nor a cancellation of
    # the read-back can reach this caller or turn a finished run into a
    # failed one.
    if result.ok and verify_after():
        _spawn_verify(req, result)
    return result


def verify_after() -> bool:
    """The read-back hook's switch, read per call like `watch_only()`.
    Default OFF."""
    return os.getenv("ATLAS_VERIFY_AFTER", "off").strip().lower() in (
        "1", "true", "yes", "on")


def _spawn_verify(req: ExecutionRequest, result: ActionResult) -> None:
    """Start the read-back as a tracked background task (`kernel.background`,
    so shutdown cancels it: a cut-short read-back leaves no `verify.*` row,
    which reads as unknown). Never raises."""
    try:
        from . import background
        background.spawn(_verify_after(req, result), f"verify {req.action}")
    except Exception:
        log.warning("read-back of %s could not be started", req.action,
                    exc_info=True)


async def _verify_after(req: ExecutionRequest, result: ActionResult) -> None:
    """Read the effect back and audit what was seen. Never raises (bar a
    cancellation), never changes `result`, never acts."""
    try:
        from .verify import verify
        seen = await verify(req.action, req.params, result)
        record = seen.as_audit()
        record["evidence"] = _capped_evidence(record["evidence"])
        await audit(f"verify.{seen.state}", req.action, record)
    except Exception:
        log.warning("read-back of %s could not be recorded", req.action,
                    exc_info=True)


async def preview(action_name: str, params: dict[str, Any]) -> ActionResult:
    """Run an action's dry run and nothing else.

    The approvals page must show the dry-run output BEFORE the owner decides, but
    `execute` only dry-runs after approval — at display time there was nothing
    to show. This produces that preview.

    It lives in executor.py on purpose: every code path that touches an
    Action's callables belongs in this file, so there is one place to audit.
    It calls `dry_run` and can never reach `run`, so it is safe during
    watch-only — seeing what WOULD happen is the entire point of that phase.
    """
    action = REGISTRY.actions.get(action_name)
    if action is None:
        return ActionResult(False, "no such action in the catalog")

    decision = REGISTRY.decisions.get(action_name)
    if decision is None or not decision.allowed:
        why = decision.reason if decision else "no permission decision"
        return ActionResult(False, f"refused: {why}")

    bad_params = _validate_params(action, params)
    if bad_params is not None:
        return ActionResult(False, f"refused: {bad_params}")

    if action.dry_run is None:
        return ActionResult(False, "this action declares no dry run")

    try:
        result = await action.dry_run(**params)
    except Exception as exc:
        log.exception("dry run for %s raised", action_name)
        return ActionResult(False, f"dry run raised: {_explain(exc)}")

    await audit("preview", action_name,
                {"ok": result.ok, "detail": result.detail})
    return result


async def rollback(action_name: str, token: str) -> ActionResult:
    """Undo a previous execution.

    Gated like `execute`. This is still a write path that reaches out to the
    world, and it previously took a caller-supplied action name with no
    permission check and no rate limit at all.
    """
    if watch_only():
        await audit("rollback.watch_only", action_name, {"token": token})
        return ActionResult(False, "ATLAS is in watch-only mode; nothing was undone")

    action = REGISTRY.actions.get(action_name)
    if action is None or action.rollback is None:
        await audit("rollback.refused", action_name, {"why": "no rollback available"})
        return ActionResult(False, "no rollback available")

    decision = REGISTRY.decisions.get(action_name)
    if decision is None or not decision.allowed:
        why = decision.reason if decision else "no permission decision"
        await audit("rollback.refused", action_name, {"why": why})
        return ActionResult(False, f"refused: {why}")

    if _over_limit(f"rollback:{action_name}", action.max_per_hour):
        await audit("rollback.rate_limited", action_name, {"token": token})
        return ActionResult(False, "rollback rate limit reached; escalating")

    _record_run(f"rollback:{action_name}")
    await audit("rollback.start", action_name, {"token": token})
    result = await action.rollback(token)
    await audit("rollback.finish", action_name,
                {"ok": result.ok, "detail": result.detail})
    return result
