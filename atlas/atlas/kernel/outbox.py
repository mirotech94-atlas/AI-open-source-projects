"""The transactional outbox (reliability plan RA-B3, docs/durable-core.md).

An outbound intent is written down before anything leaves, then a dispatcher
claims it and hands it to `executor.execute` -- the one path out
(invariant 1). The outbox never sends anything itself: no channel, no
provider client, no `run`. Every tier check, dry run, pause, rate limit and
audit row still happens in the executor, exactly as for any other caller.

Idempotency: one row per sha256(action, version, destination, logical
occurrence). The message text is deliberately not in the key -- deduping on
text would swallow a legitimate second "yes, restart it" -- and enqueuing the
same intent twice returns the row it already made.

A row moves through two durable phases before the executor sees it:

    pending --claim--> claimed --fence--> dispatching --execute--> ...

* claimed: a worker holds the lease but has not dispatched. If it dies
  here, the lease lapses and another worker claims the row again. Nothing
  left the building (R03).
* dispatching: written, fenced, immediately before `execute`. If the lease
  lapses here, the effect may or may not have happened, so the row goes to
  `reconciling` -- never back to `pending` (R04).

What `execute` returns decides the rest, and the default is the cautious one:

* ok with evidence            -> delivered (and only then)
* ok without evidence         -> unverified: it says it ran; nothing proves it
* refused before it ran       -> pending again (only a result `execute`
  marked `dispatched=False`, split by its `failure_class`; the refusal
  wording, PRE_DISPATCH_*, is read only when `dispatched` is None).
  Watch-only or a pause is an intentional stop: the row
  waits, rechecked every DISABLED_RECHECK, without spending an attempt. A
  rate ceiling or an unreadable pause state spends one, with backoff, and
  ends in failed. A permanent refusal fails at once.
* refused because what would run is not what was queued -> superseded
  (terminal, S08): the executor found the action's catalog version, or the
  params hash, changed since the row was recorded (`executor.STALE`). No
  attempt is spent, nothing ran, and a fresh card (approvals.fresh_card)
  asks Miro about the action as it is now; the row itself never runs.
  Rows queued before versions were recorded carry NULL and are not checked.
* the provider refused the credential (401/403, a revoked token; R09) ->
  that credential is paused (kernel/pause.block_capability: the module, or
  `module:account` for a module that posts as several accounts; lifted only
  by Miro) so its other queued rows hold instead of each trying the dead
  login, with one short notice. A module whose credential cannot be told
  apart is only told, never paused. A notice ends failed; an effect is
  reconciled as below.
* anything else, for an effect -> reconciling: do not resend blind
* anything else, for a notice  -> pending again with backoff (R07: retry the
  notification, never the action it reports on -- they are separate rows)

The commit of that outcome is fenced in its own WHERE clause: a worker whose
lease lapsed, or was taken over, writes nothing to the row and leaves its
result in the audit log for reconciliation (R05).

After a restore from a backup (R12, kernel/restore.py) no row is claimed
until the restore is fenced: the dump's pending, claimed and dispatching
rows go to reconciling and are read back before anything is sent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from . import idempotency
from .leases import OWNER, Fence

log = logging.getLogger("atlas.outbox")

# Long enough for the slowest action the executor bounds (an approved
# background run is capped at 900 s), so a live worker does not lose its row.
LEASE = timedelta(minutes=20)
# `execute` is cut off before the lease can lapse under it; a run cut off
# is an uncertain effect and is reconciled, never resent.
EXECUTE_BUDGET_S = LEASE.total_seconds() * 0.8

BACKOFF_BASE_S = 30.0
BACKOFF_CAP_S = 3600.0
# How often a row held back by watch-only or a pause is offered to the
# executor again. Each offer is one refusal row in the audit log.
DISABLED_RECHECK = timedelta(minutes=15)

KINDS = ("effect", "notice")

# `executor.execute` says itself whether it invoked the action
# (`ActionResult.dispatched`, RA-B3) and why it refused (`failure_class`,
# RA-D1); `_pre_dispatch_of` reads those. The wording below is the FALLBACK,
# used only when `dispatched` is None -- a result that did not come through
# `execute`. These are the refusals `execute` makes BEFORE it writes
# `execute.start`; anything not matched is treated as possibly dispatched.
# PRISTINE_EXECUTE in tests/test_lessons.py pins the strings, and
# tests/test_durable_core.py checks each is still in executor.py. Matched on
# the START (or, for the ceilings, the END) of the detail.
PRE_DISPATCH_DISABLED_PREFIXES = (
    "ATLAS is in watch-only mode",                     # watch-only
    "not run: ",                                       # paused
)
PRE_DISPATCH_TRANSIENT_PREFIXES = (
    "refused: the pause state could not be read",      # pause unreadable
)
# The three hourly ceilings: per action, per module, global.
PRE_DISPATCH_TRANSIENT_SUFFIX = "; escalating instead of repeating"
PRE_DISPATCH_PERMANENT_PREFIXES = (
    "no such action in the catalog",
    "refused: ",                  # no decision, denied, principal only, params
    "tier 2 action reached the executor unapproved",
    "dry run refused: ",
)


def _pre_dispatch(detail: str) -> str | None:
    """'disabled' | 'transient' | 'permanent' when `execute` refused before
    running, else None. Transient is tested before permanent: 'refused: the
    pause state ...' also starts with 'refused: '."""
    text = detail or ""
    if text.startswith(PRE_DISPATCH_DISABLED_PREFIXES):
        return "disabled"
    if (text.startswith(PRE_DISPATCH_TRANSIENT_PREFIXES)
            or text.endswith(PRE_DISPATCH_TRANSIENT_SUFFIX)):
        return "transient"
    if text.startswith(PRE_DISPATCH_PERMANENT_PREFIXES):
        return "permanent"
    return None


# failure_class -> how a refusal made before dispatch is retried. Only the
# executor's own ceilings and unreadable pause state carry these classes on
# a `dispatched=False` result; a refused dry run is always `validation`.
_CLASS_TRANSIENT = frozenset({"transient", "budget"})
# The executor's intentional stops, by source: `executor.HOLD` in evidence.
_HOLDS = frozenset({"watch_only", "paused"})


def _is_executor_hold(result: Any) -> bool:
    """Watch-only or a pause, as `execute` itself refused: dispatched False,
    class disabled, AND the executor's marker. Class alone is not enough --
    nothing else may put a row into wait-without-an-attempt."""
    from .executor import HOLD
    evidence = getattr(result, "evidence", None) or {}
    return (getattr(result, "dispatched", None) is False
            and str(getattr(result, "failure_class", None) or "") == "disabled"
            and isinstance(evidence, dict) and evidence.get(HOLD) in _HOLDS)


def _pre_dispatch_of(result: Any) -> str | None:
    """'disabled' | 'transient' | 'permanent' when `execute` refused before
    running, else None.

    `dispatched is True`: run() was invoked, so never a refusal, whatever
    the detail says. `dispatched is False`: 'disabled' only for the
    executor's own watch-only / pause hold (`_is_executor_hold`); transient
    and budget back off; anything else, including a bare `disabled` class
    and None, is permanent. `dispatched is None`: the result did not come
    through `execute`, so fall back to the refusal wording."""
    dispatched = getattr(result, "dispatched", None)
    if dispatched is None:
        return _pre_dispatch(str(getattr(result, "detail", "") or ""))
    if dispatched:
        return None
    if _is_executor_hold(result):
        return "disabled"
    if str(getattr(result, "failure_class", None) or "") in _CLASS_TRANSIENT:
        return "transient"
    return "permanent"


def idempotency_key(action: str, version: str, destination: str,
                    occurrence: str) -> str:
    """Action identity + version + destination + logical occurrence. Never
    the message text."""
    for name, value in (("action", action), ("version", version),
                        ("destination", destination), ("occurrence", occurrence)):
        if not str(value).strip():
            raise ValueError(f"an outbox key needs a non-empty {name}")
    raw = "\x1f".join((action, str(version), destination, occurrence))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _params_hash(params: dict[str, Any]) -> str:
    from .durable import digest
    return digest(params)


def _recorded(action: str, params: dict[str, Any]) -> tuple[str | None, str]:
    """(catalog version, params hash) a queued row keeps, so dispatch can
    refuse it once the action or the params are no longer what was queued
    (S08). The version is None for an action not in the catalog -- the
    executor refuses that row anyway."""
    from .registry import REGISTRY, payload_hash
    return REGISTRY.action_version(action), payload_hash(params)


async def _rehash_stored(oid: int, row: Any, approved_hash: str) -> None:
    """The hash of the params as JSONB gave them back, when that differs
    from the one computed before the insert (see registry.payload_hash).
    Best effort: a stale hash can only refuse the row, never run another."""
    stored = row.get("params") if hasattr(row, "get") else None
    if stored is None:
        return
    from .registry import payload_hash
    from .state import execute_sql
    again = payload_hash(stored)
    if again == approved_hash:
        return
    try:
        await execute_sql(
            "UPDATE outbox SET approved_payload_hash = $2 WHERE id = $1",
            oid, again)
    except Exception:  # noqa: BLE001
        log.warning("outbox %s: stored params hash not refreshed", oid)


@dataclass(frozen=True, slots=True)
class Enqueued:
    outbox_id: int
    created: bool
    key: str


async def enqueue(action: str, params: dict[str, Any], *, destination: str,
                  occurrence: str, version: str = "1", kind: str = "effect",
                  reason: str = "", approved_by: str | None = None,
                  workflow_id: int | None = None, incident_id: int | None = None,
                  max_attempts: int = 5) -> Enqueued:
    """Record one outbound intent, once. Raises DurableUnavailable when the
    database cannot take it: then nothing is queued, and the caller must
    not say it is."""
    from .durable import DurableUnavailable
    from .state import fetch_one
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    key = idempotency_key(action, version, destination, occurrence)
    catalog, approved_hash = _recorded(action, params)
    try:
        row = await fetch_one(
            """INSERT INTO outbox
                   (idempotency_key, workflow_id, kind, action, action_version,
                    destination, occurrence, params, params_hash, reason,
                    approved_by, incident_id, max_attempts, catalog_version,
                    approved_payload_hash)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11,
                       $12, $13, $14, $15)
               ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING id, params""",
            key, workflow_id, kind, action, str(version), destination,
            occurrence, json.dumps(params, default=str), _params_hash(params),
            reason[:500], approved_by, incident_id, max_attempts, catalog,
            approved_hash)
        if row is not None:
            await _rehash_stored(int(row["id"]), row, approved_hash)
            return Enqueued(int(row["id"]), True, key)
        row = await fetch_one(
            "SELECT id FROM outbox WHERE idempotency_key = $1", key)
    except Exception as exc:
        raise DurableUnavailable(type(exc).__name__) from exc
    if row is None:
        raise DurableUnavailable("the intent was neither queued nor found")
    return Enqueued(int(row["id"]), False, key)


async def enqueue_with_transition(workflow_id: int, src: str, dst: str,
                                  fence: Fence, action: str,
                                  params: dict[str, Any], *, destination: str,
                                  occurrence: str, version: str = "1",
                                  reason: str = "",
                                  approved_by: str | None = None) -> int | None:
    """Move a workflow and record its outbound intent in ONE statement, so
    neither lands without the other. The move is fenced: a worker whose
    lease lapsed queues nothing. Returns the outbox id, or None when the
    move was refused (stale fence, wrong state) -- or when the intent was
    already queued, in which case the move still happened and the existing
    row stands."""
    from .durable import DurableUnavailable, check
    from .state import fetch_one
    check(src, dst)
    key = idempotency_key(action, version, destination, occurrence)
    catalog, approved_hash = _recorded(action, params)
    try:
        row = await fetch_one(
            """WITH moved AS (
                   UPDATE workflows SET state = $3, updated_at = now(),
                                        current_step = $4
                    WHERE id = $1 AND state = $2
                      AND fencing_token = $5 AND lease_owner = $6
                      AND lease_expiry > now()
                RETURNING id)
               INSERT INTO outbox
                   (idempotency_key, workflow_id, kind, action, action_version,
                    destination, occurrence, params, params_hash, reason,
                    approved_by, catalog_version, approved_payload_hash)
               SELECT $7, moved.id, 'effect', $4, $8, $9, $10, $11::jsonb, $12,
                      $13, $14, $15, $16
                 FROM moved
               ON CONFLICT (idempotency_key) DO NOTHING
               RETURNING id, params""",
            workflow_id, src, dst, action, fence.token, fence.owner, key,
            str(version), destination, occurrence,
            json.dumps(params, default=str), _params_hash(params), reason[:500],
            approved_by, catalog, approved_hash)
    except Exception as exc:
        raise DurableUnavailable(type(exc).__name__) from exc
    if row is None:
        return None
    await _rehash_stored(int(row["id"]), row, approved_hash)
    return int(row["id"])


@dataclass(slots=True)
class Dispatched:
    outbox_id: int
    action: str
    status: str
    detail: str = ""
    committed: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)


def _backoff(attempts: int) -> timedelta:
    delay = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, attempts - 1)))
    return timedelta(seconds=delay * random.uniform(0.8, 1.2))


async def _audit(event: str, outbox_id: int, data: dict[str, Any]) -> None:
    from .audit import audit
    try:
        await audit(event, f"outbox:{outbox_id}", data)
    except Exception:  # noqa: BLE001
        log.warning("%s for outbox %s not audited", event, outbox_id)


async def sweep_lapsed() -> list[int]:
    """Rows whose worker lost its lease mid-dispatch: the effect is
    uncertain, so they go to reconciling. Runs at the start of every pass,
    not at startup, so no new reaper joins the API lifespan."""
    from .state import fetch_all
    try:
        rows = await fetch_all(
            """UPDATE outbox SET status = 'reconciling', finished_at = now(),
                                 detail = 'the worker lost its lease mid-dispatch; '
                                          || 'outcome unknown'
                WHERE status = 'dispatching' AND lease_expiry <= now()
            RETURNING id, action, workflow_id""")
    except Exception as exc:  # noqa: BLE001
        log.warning("lapsed dispatches not swept (%s)", type(exc).__name__)
        return []
    for r in rows:
        await _audit("outbox.lapsed", int(r["id"]), {"action": str(r["action"])})
        if r["workflow_id"] is not None:
            await _advance(int(r["workflow_id"]), "RECONCILING",
                           f"outbox:{r['id']}")
    return [int(r["id"]) for r in rows]


async def _claim(owner: str, lease: timedelta,
                 only_id: int | None = None) -> dict[str, Any] | None:
    """The oldest due row, or -- with `only_id` -- that row and no other
    (a producer dispatching its own intent inline, kernel/flows). The same
    predicate either way: a row that is not due is not taken, and no row is
    taken while a restore is unfenced (kernel/restore.py)."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE outbox
                  SET status = 'claimed', lease_owner = $1,
                      lease_expiry = now() + $2::interval,
                      fencing_token = fencing_token + 1, claimed_at = now()
                WHERE id = (SELECT min(id) FROM outbox
                             WHERE ((status = 'pending' AND next_attempt_at <= now())
                                    OR (status = 'claimed' AND lease_expiry <= now()))
                               AND ($3::bigint IS NULL OR id = $3)
                               -- R12: nothing leaves a restored database
                               -- until kernel/restore.py has fenced it.
                               AND NOT EXISTS (SELECT 1 FROM restores
                                                WHERE fenced_at IS NULL))
                  AND ((status = 'pending' AND next_attempt_at <= now())
                       OR (status = 'claimed' AND lease_expiry <= now()))
            RETURNING id, kind, action, params, reason, approved_by, incident_id,
                      workflow_id, fencing_token, attempts, max_attempts,
                      params_hash, destination, occurrence, catalog_version,
                      approved_payload_hash""",
            owner, lease, only_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("no outbox row claimed (%s); nothing dispatched",
                    type(exc).__name__)
        return None
    return dict(row) if row is not None else None


async def _fence_for_dispatch(row: dict[str, Any], owner: str) -> bool:
    """claimed -> dispatching, only while this worker's lease is current
    and unexpired. False (do not dispatch) on any doubt."""
    from .state import fetch_one
    try:
        got = await fetch_one(
            """UPDATE outbox SET status = 'dispatching', dispatched_at = now(),
                                 attempts = attempts + 1
                WHERE id = $1 AND status = 'claimed' AND lease_owner = $2
                  AND fencing_token = $3 AND lease_expiry > now()
            RETURNING id""",
            int(row["id"]), owner, int(row["fencing_token"]))
    except Exception as exc:  # noqa: BLE001
        log.warning("outbox %s not fenced (%s); not dispatching", row["id"],
                    type(exc).__name__)
        return False
    return got is not None


async def _commit(row: dict[str, Any], owner: str, status: str, detail: str,
                  evidence: dict[str, Any], retry_in: timedelta | None,
                  from_status: str = "dispatching", refund: bool = False) -> bool:
    """Record the outcome, fenced: current token, this owner, lease not
    lapsed, and the row still where this worker left it. `refund` gives
    back the attempt the fence spent (an intentional stop is not one)."""
    from .state import fetch_one
    try:
        got = await fetch_one(
            """UPDATE outbox
                  SET status = $4, detail = $5, evidence = $6::jsonb,
                      finished_at = now(), lease_owner = NULL,
                      lease_expiry = NULL,
                      next_attempt_at = now() + COALESCE($7::interval,
                                                         interval '0'),
                      attempts = attempts - $9
                WHERE id = $1 AND lease_owner = $2 AND fencing_token = $3
                  AND lease_expiry > now() AND status = $8
            RETURNING id""",
            int(row["id"]), owner, int(row["fencing_token"]), status,
            (detail or "")[:1000], json.dumps(evidence or {}, default=str),
            retry_in, from_status, 1 if refund else 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("outbox %s outcome not recorded (%s)", row["id"],
                    type(exc).__name__)
        return False
    return got is not None


async def _advance(workflow_id: int, to: str, ref: str) -> None:
    """Keep the workflow in step with its effect: RUNNING -> VERIFYING on a
    delivery, RUNNING -> RECONCILING on an uncertain one. A compare-and-set;
    a workflow not in RUNNING is left alone."""
    from .durable import DurableUnavailable, transition
    try:
        await transition(workflow_id, "RUNNING", to, evidence_refs=[ref])
    except DurableUnavailable:
        log.warning("workflow %s not advanced to %s", workflow_id, to)


def _auth_refused(result: Any) -> tuple[bool, int | None]:
    """(True, HTTP status or None) when the provider refused the credential
    itself -- 401/403, a revoked token (R09). Only for a result that got past
    the executor's own refusals (`dispatched` True, or None from a test
    double): the action's own `failure_class` first, else the detail's HTTP
    status (`contracts.classify_failure`)."""
    if getattr(result, "ok", False) or getattr(result, "dispatched", None) is False:
        return False, None
    from .contracts import _HTTP_STATUS, FailureClass, classify_failure
    detail = str(getattr(result, "detail", "") or "")
    named = str(getattr(result, "failure_class", None) or "")
    if named != FailureClass.AUTH and (
            classify_failure(detail, after_dispatch=True) is not FailureClass.AUTH):
        return False, None
    match = _HTTP_STATUS.search(detail)
    return True, (int(match.group(1)) if match else None)


def _outcome(kind: str, result: Any, attempts: int,
             max_attempts: int) -> tuple[str, timedelta | None, bool]:
    """(status, retry after, refund the attempt) for what execute returned."""
    ok = bool(getattr(result, "ok", False))
    evidence = dict(getattr(result, "evidence", None) or {})
    if ok:
        return ("delivered" if evidence else "unverified"), None, False
    from .executor import stale_of
    if stale_of(result) is not None:
        # Not what was queued: never runs, never retried, costs no attempt.
        return "superseded", None, True
    before = _pre_dispatch_of(result)
    if before == "disabled":
        # Watch-only or paused on purpose: wait, never fail, never count.
        return "pending", DISABLED_RECHECK, True
    if before == "permanent":
        return "failed", None, False
    if before is None and kind == "notice" and _auth_refused(result)[0]:
        # R09: a refused credential is not retried blind. The module is
        # paused (dispatch_once), and this notice ends here.
        return "failed", None, False
    if before == "transient" or kind == "notice":
        if attempts >= max_attempts:
            return "failed", None, False
        return "pending", _backoff(attempts), False
    return "reconciling", None, False


async def _record_shadowed(row: dict[str, Any], owner: str,
                           scope: str) -> Dispatched:
    """claimed -> shadowed (terminal), then "would have executed X with
    params-hash H" in the audit chain. Terminal on purpose: lifting a
    shadow must not replay intents queued during it. Re-enqueueing the
    same idempotency key returns this row; a canary is a new occurrence."""
    from .shadow import WOULD_EXECUTE
    oid, action = int(row["id"]), str(row["action"])
    phash = str(row.get("params_hash") or "") or _params_hash(
        json.loads(row["params"]) if isinstance(row["params"], str)
        else (row["params"] or {}))
    detail = f"shadow: would have executed {action} (params sha256 {phash[:12]})"
    evidence = {"params_hash": phash, "scope": scope}
    if not await _commit(row, owner, "shadowed", detail, evidence, None,
                         from_status="claimed"):
        return Dispatched(oid, action, "stale", "lease lost before the shadow "
                          "record", committed=False)
    from .audit import audit
    try:
        await audit(WOULD_EXECUTE, action, {
            "layer": "outbox", "outbox": oid, "params_hash": phash,
            "scope": scope, "kind": str(row["kind"]),
            "destination": row.get("destination"),
            "occurrence": row.get("occurrence"),
            "reason": str(row.get("reason") or "")[:300],
            "approved_by": row.get("approved_by")})
    except Exception:  # noqa: BLE001
        log.warning("shadow record for outbox %s not audited", oid)
    if row["workflow_id"] is not None and str(row["kind"]) == "effect":
        await _advance(int(row["workflow_id"]), "BLOCKED", f"outbox:{oid}")
    return Dispatched(oid, action, "shadowed", detail, evidence=evidence)


async def dispatch_once(owner: str = OWNER, lease: timedelta = LEASE,
                        only_id: int | None = None) -> Dispatched | None:
    """Claim one due row (with `only_id`, that row only), dispatch it
    through the executor, record what came back. None when there was
    nothing to do or the outbox could not be read -- in which case nothing
    was dispatched."""
    from .executor import ExecutionRequest, execute
    row = await _claim(owner, lease, only_id)
    if row is None:
        return None
    oid, action = int(row["id"]), str(row["action"])
    # Shadow mode (kernel/shadow.py, RA-G1): checked before the fence, so a
    # shadowed intent spends no attempt and never reaches the executor.
    from . import pause as _pause
    from . import shadow as _shadow
    try:
        shadowed = await _shadow.covering(action)
    except _pause.PauseUnreadable:
        # Fail closed. The claim lapses and the row is offered again.
        return Dispatched(oid, action, "stale",
                          "shadow state unreadable; not dispatched",
                          committed=False)
    if shadowed is not None:
        return await _record_shadowed(row, owner, shadowed.scope)
    if not await _fence_for_dispatch(row, owner):
        return Dispatched(oid, action, "stale", "lease lost before dispatch",
                          committed=False)
    params = row["params"]
    if isinstance(params, str):
        params = json.loads(params or "{}")
    attempts = int(row["attempts"]) + 1
    kind = str(row["kind"])
    result: Any = None
    try:
        async with asyncio.timeout(min(EXECUTE_BUDGET_S,
                                       lease.total_seconds() * 0.8)):
            # The row is the run (RA-B7): a re-dispatch of the same row
            # carries the same key to an action that honours one.
            with idempotency.running(f"outbox:{oid}"):
                result = await execute(ExecutionRequest(
                    action=action, params=params or {},
                    reason=str(row["reason"] or f"outbox #{oid}"),
                    incident_id=row["incident_id"],
                    approved_by=row["approved_by"],
                    approved_version=row.get("catalog_version"),
                    approved_payload_hash=row.get("approved_payload_hash")))
    except Exception as exc:  # noqa: BLE001
        # Raised out of execute, or ran past its budget: it may have reached
        # the provider. Reconcile, never resend.
        status, detail = "reconciling", f"dispatch did not return: {type(exc).__name__}"
        evidence: dict[str, Any] = {}
        retry_in, refund = None, False
    else:
        status, retry_in, refund = _outcome(kind, result, attempts,
                                            int(row["max_attempts"]))
        detail = str(result.detail or "")
        evidence = dict(result.evidence or {})
    committed = await _commit(row, owner, status, detail, evidence, retry_in,
                              refund=refund)
    if not committed:
        # Stale: another worker owns the row now, or the lease lapsed and
        # the sweep will call it uncertain. What this worker saw is kept
        # where reconciliation can read it.
        await _audit("outbox.stale_commit", oid,
                     {"action": action, "saw": status, "detail": detail[:300],
                      "evidence": evidence, "token": int(row["fencing_token"])})
        return Dispatched(oid, action, status, detail, committed=False,
                          evidence=evidence)
    await _audit(f"outbox.{status}", oid, {"action": action, "attempt": attempts})
    refused, code = (_auth_refused(result) if status in ("failed", "reconciling")
                     else (False, None))
    if refused:
        # R09: block the credential -- pause it (`module:account` where the
        # module posts as several accounts), or only tell Miro where the
        # credential cannot be told apart. Once, however many rows fail.
        from . import pause as _pause
        done = await _pause.block_capability(action, params or {}, code)
        if done is not None:
            await _audit("outbox.auth_blocked", oid,
                         {"action": action, "status": code, "response": done})
    if status == "superseded":
        await _fresh_card(row, params or {}, detail)
    if row["workflow_id"] is not None and kind == "effect":
        if status in ("delivered", "unverified"):
            await _advance(int(row["workflow_id"]), "VERIFYING", f"outbox:{oid}")
        elif status == "reconciling":
            await _advance(int(row["workflow_id"]), "RECONCILING", f"outbox:{oid}")
    return Dispatched(oid, action, status, detail, evidence=evidence)


async def _fresh_card(row: dict[str, Any], params: dict[str, Any],
                      detail: str) -> None:
    """A superseded row asks again, as a card, about the action as it is
    now. Keyed on the row, so a second pass cannot raise a second card."""
    from .approvals import fresh_card
    oid, action = int(row["id"]), str(row["action"])
    pid = await fresh_card(
        action, params,
        f"Outbox #{oid} ({action}) was not run: "
        f"{detail.removeprefix('refused: ')}. {row.get('reason') or ''!s}".strip(),
        dedup_key=f"outbox-superseded:{oid}", incident_id=row.get("incident_id"))
    await _audit("outbox.fresh_card", oid, {"action": action, "proposal": pid})


async def dispatch(limit: int = 10, owner: str = OWNER,
                   budget_s: float | None = None) -> list[Dispatched]:
    """One bounded pass: apply any restore fence, sweep lapsed dispatches,
    then up to `limit` rows.
    With `budget_s`, no row is started unless a full EXECUTE_BUDGET_S still
    fits inside it, so a caller's deadline never cuts a dispatch off
    mid-run (which would leave a finished effect looking uncertain)."""
    import time

    from . import restore
    started = time.monotonic()
    try:
        # R12: a database restored from a backup is fenced before anything
        # in it is dispatched. Unreadable: dispatch nothing this pass.
        await restore.apply()
    except restore.FenceUnreadable as exc:
        log.warning("restore fence unreadable (%s); nothing dispatched", exc)
        return []
    await sweep_lapsed()
    out: list[Dispatched] = []
    for _ in range(max(0, limit)):
        if budget_s is not None and (
                time.monotonic() - started + EXECUTE_BUDGET_S > budget_s):
            break
        done = await dispatch_once(owner)
        if done is None:
            break
        out.append(done)
    return out


# ------------------------------------------------------------ reconciliation

# action name -> a READ-ONLY lookup of whether the effect happened:
# ("present" | "absent" | "unknown", evidence). Collectors only read
# (invariant 1): a reconciler may query a provider, never write to one.
# None are registered yet; a row with none goes to needs_human.
Reconciler = Callable[[dict[str, Any]], Awaitable[tuple[str, dict[str, Any]]]]
RECONCILERS: dict[str, Reconciler] = {}


async def reconcile_once(owner: str = OWNER,
                         lease: timedelta = LEASE) -> Dispatched | None:
    """Take one reconciling row and settle it from a read-back: present ->
    delivered, absent -> pending (safe to dispatch again), unknown or no
    reconciler -> needs_human. Nothing is resent on a guess."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE outbox
                  SET lease_owner = $1, lease_expiry = now() + $2::interval,
                      fencing_token = fencing_token + 1
                WHERE id = (SELECT min(id) FROM outbox
                             WHERE status = 'reconciling'
                               AND (lease_expiry IS NULL OR lease_expiry <= now()))
                  AND status = 'reconciling'
                  AND (lease_expiry IS NULL OR lease_expiry <= now())
            RETURNING id, kind, action, params, workflow_id, fencing_token,
                      attempts, max_attempts""",
            owner, lease)
    except Exception as exc:  # noqa: BLE001
        log.warning("no reconciling row claimed (%s)", type(exc).__name__)
        return None
    if row is None:
        return None
    row = dict(row)
    oid, action = int(row["id"]), str(row["action"])
    params = row["params"]
    if isinstance(params, str):
        row["params"] = json.loads(params or "{}")
    lookup = RECONCILERS.get(action)
    verdict, evidence = "unknown", {}
    if lookup is not None:
        try:
            verdict, evidence = await lookup(row)
        except Exception as exc:  # noqa: BLE001
            verdict, evidence = "unknown", {"error": type(exc).__name__}
    status = {"present": "delivered", "absent": "pending"}.get(verdict,
                                                              "needs_human")
    if status == "pending" and int(row["attempts"]) >= int(row["max_attempts"]):
        status = "needs_human"
    detail = f"reconciled: {verdict}" if lookup else "no reconciler: check by hand"
    committed = await _commit(row, owner, status, detail, evidence, None,
                              from_status="reconciling")
    if not committed:
        await _audit("outbox.stale_commit", oid,
                     {"action": action, "saw": status, "evidence": evidence})
        return Dispatched(oid, action, status, detail, committed=False)
    await _audit(f"outbox.reconciled.{status}", oid, {"action": action})
    return Dispatched(oid, action, status, detail, evidence=evidence)
