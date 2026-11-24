"""The durable workflow record (reliability plan RA-B1, docs/durable-core.md).

A workflow is resumable state for one logical request: one row per
(workflow_type, correlation_id), so a redelivered inbound event finds the row
it already made instead of starting a second workflow (R01). The row holds
references and hashes, never raw payloads -- the mailbox, the Slack event,
the provider response stay where they live, and `input_refs` points at them.

The plan's state machine lives here, in code. `transition` refuses an
illegal move with `IllegalTransition` before any SQL runs, and the UPDATE
that makes a legal one is a compare-and-set on the state it came from, so
two workers racing from the same state move it once. A move made under a
lease carries the fence in the same WHERE clause: a worker whose lease
lapsed commits nothing (R05).

This sits beside the proposal claim, not in place of it. Approval stays the
atomic UPDATE in `kernel.approvals` (expiry in the same statement, silence
never consent); a workflow only records that a proposal it waits on was
claimed, and cannot leave WAITING_APPROVAL for READY unless the proposals
row says so in the same statement.

Nothing here runs unless ATLAS_DURABLE_CORE=on (`enabled()`), read at call
time like ATLAS_WATCH_ONLY.
"""
from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .leases import OWNER, Fence

log = logging.getLogger("atlas.durable")


def enabled() -> bool:
    """ATLAS_DURABLE_CORE, default off. Read per call, never cached, and
    before any SQL: with it off nothing here touches the database."""
    return os.getenv("ATLAS_DURABLE_CORE", "off").strip().lower() in (
        "1", "on", "true", "yes")


class DurableUnavailable(Exception):
    """The record could not be written or read. The caller must not report
    the request as accepted, started or done (R06)."""


class IllegalTransition(ValueError):
    """A move the state machine does not permit. Raised before any SQL."""


class State(enum.StrEnum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    PREPARING = "PREPARING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    RETRY_WAIT = "RETRY_WAIT"
    RECONCILING = "RECONCILING"
    FAILED = "FAILED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


S = State

TERMINAL = frozenset({S.SUCCEEDED, S.FAILED, S.DECLINED, S.EXPIRED, S.CANCELLED})

# The plan's arrows, one for one:
#   RECEIVED -> VALIDATING -> READY
#   READY -> PREPARING -> WAITING_APPROVAL      when policy requires
#   READY / PREPARING -> RUNNING                only when authorized
#   WAITING_APPROVAL -> READY                   on valid approval
#   RUNNING -> VERIFYING -> SUCCEEDED
#   transient safe failure -> RETRY_WAIT -> READY
#   uncertain external effect -> RECONCILING
#   pending proposal -> DECLINED / EXPIRED
#   accepted cancellation -> CANCEL_REQUESTED -> CANCELLED
# plus the "from any nonterminal state" arrows added below.
_EDGES: dict[State, set[State]] = {
    S.RECEIVED: {S.VALIDATING},
    S.VALIDATING: {S.READY},
    S.READY: {S.PREPARING, S.WAITING_APPROVAL, S.RUNNING},
    S.PREPARING: {S.WAITING_APPROVAL, S.RUNNING, S.RETRY_WAIT},
    S.WAITING_APPROVAL: {S.READY, S.DECLINED, S.EXPIRED},
    S.RUNNING: {S.VERIFYING, S.RETRY_WAIT, S.RECONCILING},
    S.VERIFYING: {S.SUCCEEDED, S.RECONCILING},
    S.RETRY_WAIT: {S.READY},
    # What reconciliation finds: the effect happened (verify it), it did
    # not (safe to retry), or nobody can tell (a person decides).
    S.RECONCILING: {S.VERIFYING, S.RETRY_WAIT},
    S.BLOCKED: {S.READY},
    # Too late to stop an effect already in flight: record what happened
    # and reconcile it -- never mark it cancelled as though nothing occurred.
    S.CANCEL_REQUESTED: {S.CANCELLED, S.RECONCILING},
    # A person closes what automation could not.
    S.NEEDS_HUMAN: {S.SUCCEEDED, S.FAILED, S.CANCELLED},
}
# Any applicable nonterminal state -> BLOCKED; exhausted or nonrecoverable
# -> FAILED / NEEDS_HUMAN; an accepted cancellation -> CANCEL_REQUESTED.
for _state in State:
    if _state in TERMINAL or _state is S.NEEDS_HUMAN:
        continue
    _EDGES.setdefault(_state, set())
    _EDGES[_state] |= {S.FAILED, S.NEEDS_HUMAN}
    if _state is not S.CANCEL_REQUESTED:
        _EDGES[_state] |= {S.BLOCKED, S.CANCEL_REQUESTED}
    _EDGES[_state].discard(_state)
for _state in TERMINAL:
    _EDGES[_state] = set()

TRANSITIONS: dict[State, frozenset[State]] = {
    k: frozenset(v) for k, v in _EDGES.items()}


def allowed(src: State | str, dst: State | str) -> bool:
    try:
        return State(dst) in TRANSITIONS[State(src)]
    except ValueError:
        return False


def check(src: State | str, dst: State | str) -> None:
    if not allowed(src, dst):
        raise IllegalTransition(f"{src} -> {dst} is not a permitted transition")


def digest(value: Any) -> str:
    """sha256 of the canonical JSON of `value`: what the record keeps
    instead of the value itself."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Intake:
    """The answer to `receive`: the one workflow for this correlation id,
    and whether this delivery created it."""

    workflow_id: int
    created: bool
    state: State


async def receive(workflow_type: str, correlation_id: str, *,
                  payload: Any = None, input_refs: list[str] | None = None,
                  version: int = 1, actor: str = "", channel: str = "",
                  thread: str = "", desired_outcome: str = "",
                  classification: str = "internal",
                  deadline: datetime | None = None) -> Intake:
    """Record an inbound request once. A redelivery of the same
    (type, correlation id) returns the existing workflow with
    created=False, so the caller does nothing twice (R01). `payload` is
    hashed, never stored. Raises DurableUnavailable when the database
    cannot answer -- the request has NOT been accepted then (R06)."""
    if not workflow_type or not correlation_id:
        raise ValueError("a workflow needs a type and a correlation id")
    from .state import fetch_one
    try:
        row = await fetch_one(
            """INSERT INTO workflows
                   (workflow_type, workflow_version, correlation_id,
                    originating_actor, originating_channel, thread_id,
                    desired_outcome, input_refs, input_hash,
                    data_classification, deadline)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10,
                       $11::timestamptz)
               ON CONFLICT (workflow_type, correlation_id) DO NOTHING
               RETURNING id, state""",
            workflow_type, version, correlation_id, actor[:200], channel[:200],
            thread[:200], desired_outcome[:500],
            json.dumps(list(input_refs or [])),
            digest(payload) if payload is not None else "",
            classification, deadline)
        if row is not None:
            return Intake(int(row["id"]), True, State(row["state"]))
        row = await fetch_one(
            """SELECT id, state FROM workflows
                WHERE workflow_type = $1 AND correlation_id = $2""",
            workflow_type, correlation_id)
    except Exception as exc:
        raise DurableUnavailable(type(exc).__name__) from exc
    if row is None:
        raise DurableUnavailable("the workflow was neither created nor found")
    return Intake(int(row["id"]), False, State(row["state"]))


async def transition(workflow_id: int, src: State | str, dst: State | str, *,
                     fence: Fence | None = None, step: str | None = None,
                     summary: str | None = None,
                     reconciliation: str | None = None,
                     next_attempt_at: datetime | None = None,
                     evidence_refs: list[str] | None = None,
                     provider_request_ids: list[str] | None = None,
                     unknowns: list[str] | None = None,
                     attempt: bool = False) -> bool:
    """Move `workflow_id` from `src` to `dst`. Raises IllegalTransition for a
    move the machine does not permit. Returns False when the row was not in
    `src` any more, or -- with a fence -- when the fence is no longer the
    current, unexpired lease: the stale worker commits nothing."""
    check(src, dst)
    if State(src) is S.WAITING_APPROVAL and State(dst) is S.READY:
        raise IllegalTransition(
            "WAITING_APPROVAL -> READY goes through approved(), which checks "
            "the proposal was claimed")
    from .state import fetch_one
    token = fence.token if fence is not None else None
    owner = fence.owner if fence is not None else None
    try:
        row = await fetch_one(
            """UPDATE workflows
                  SET state = $3, updated_at = now(),
                      current_step = COALESCE($4, current_step),
                      result_summary = COALESCE($5, result_summary),
                      reconciliation_state = COALESCE($6, reconciliation_state),
                      next_attempt_at = COALESCE($7::timestamptz, next_attempt_at),
                      evidence_refs = evidence_refs || $8::jsonb,
                      provider_request_ids = provider_request_ids || $9::jsonb,
                      remaining_unknowns = COALESCE($10::jsonb, remaining_unknowns),
                      attempt_count = attempt_count + $11
                WHERE id = $1 AND state = $2
                  AND ($12::bigint IS NULL
                       OR (fencing_token = $12 AND lease_owner = $13
                           AND lease_expiry > now()))
            RETURNING id""",
            workflow_id, str(src), str(dst), step, summary, reconciliation,
            next_attempt_at, json.dumps(list(evidence_refs or [])),
            json.dumps(list(provider_request_ids or [])),
            json.dumps(unknowns) if unknowns is not None else None,
            1 if attempt else 0, token, owner)
    except Exception as exc:
        raise DurableUnavailable(type(exc).__name__) from exc
    if row is None:
        from .audit import audit
        await _quiet_audit(audit, "workflow.transition_refused", workflow_id,
                           {"from": str(src), "to": str(dst),
                            "fenced": fence is not None})
        return False
    return True


async def await_approval(workflow_id: int, proposal_id: int, *,
                         expires_at: datetime | None,
                         fence: Fence | None = None) -> bool:
    """READY/PREPARING -> WAITING_APPROVAL, tied to the proposal whose atomic
    claim (kernel.approvals.decide) is the only way it can be approved."""
    from .state import fetch_one
    token = fence.token if fence is not None else None
    owner = fence.owner if fence is not None else None
    try:
        row = await fetch_one(
            """UPDATE workflows
                  SET state = 'WAITING_APPROVAL', updated_at = now(),
                      proposal_id = $2, approval_expiry = $3::timestamptz
                WHERE id = $1 AND state IN ('READY', 'PREPARING')
                  AND ($4::bigint IS NULL
                       OR (fencing_token = $4 AND lease_owner = $5
                           AND lease_expiry > now()))
            RETURNING id""",
            workflow_id, proposal_id, expires_at, token, owner)
    except Exception as exc:
        raise DurableUnavailable(type(exc).__name__) from exc
    return row is not None


async def approved(workflow_id: int, payload_hash: str) -> bool:
    """WAITING_APPROVAL -> READY on a valid approval, and only then.

    The approval itself happened in `approvals.decide`'s atomic claim; this
    records it, in one statement that requires the proposals row to have
    been claimed (status approved or executed). A proposal that lapsed,
    was declined, or is still pending leaves the workflow where it is:
    silence is never consent, and this cannot manufacture a yes."""
    check(S.WAITING_APPROVAL, S.READY)
    if not payload_hash:
        raise ValueError("an approval records the hash of what was approved")
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE workflows w
                  SET state = 'READY', updated_at = now(),
                      approved_payload_hash = $2
                WHERE w.id = $1 AND w.state = 'WAITING_APPROVAL'
                  AND EXISTS (SELECT 1 FROM proposals p
                               WHERE p.id = w.proposal_id
                                 AND p.status IN ('approved', 'executed'))
            RETURNING w.id""",
            workflow_id, payload_hash)
    except Exception as exc:
        raise DurableUnavailable(type(exc).__name__) from exc
    return row is not None


async def claim(workflow_id: int, ttl: timedelta,
                owner: str = OWNER) -> Fence | None:
    """Take the lease on one nonterminal workflow whose lease is free or
    lapsed. Bumps the fencing token, so whoever held it before can no
    longer commit. None when held, terminal, missing or unreadable."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE workflows
                  SET lease_owner = $2, lease_expiry = now() + $3::interval,
                      fencing_token = fencing_token + 1, updated_at = now()
                WHERE id = $1
                  AND state <> ALL($4::text[])
                  AND (lease_expiry IS NULL OR lease_expiry <= now())
            RETURNING id, fencing_token, lease_expiry""",
            workflow_id, owner, ttl, sorted(str(s) for s in TERMINAL))
    except Exception as exc:  # noqa: BLE001
        log.warning("workflow %s could not be claimed (%s)", workflow_id,
                    type(exc).__name__)
        return None
    if row is None:
        return None
    return Fence("workflow", str(workflow_id), owner, int(row["fencing_token"]),
                 row["lease_expiry"])


async def claim_next(states: list[State], ttl: timedelta,
                     owner: str = OWNER) -> tuple[Fence, State] | None:
    """The oldest workflow in one of `states` that is due and unleased (or
    whose lease lapsed: its worker died), claimed. This is how another
    worker resumes what a crashed one left (R03)."""
    wanted = [str(s) for s in states if s not in TERMINAL]
    if not wanted:
        return None
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE workflows
                  SET lease_owner = $1, lease_expiry = now() + $2::interval,
                      fencing_token = fencing_token + 1, updated_at = now()
                WHERE id = (SELECT min(id) FROM workflows
                             WHERE state = ANY($3::text[])
                               AND (next_attempt_at IS NULL
                                    OR next_attempt_at <= now())
                               AND (lease_expiry IS NULL
                                    OR lease_expiry <= now()))
                  AND state = ANY($3::text[])
                  AND (lease_expiry IS NULL OR lease_expiry <= now())
            RETURNING id, state, fencing_token, lease_expiry""",
            owner, ttl, wanted)
    except Exception as exc:  # noqa: BLE001
        log.warning("no workflow claimed (%s)", type(exc).__name__)
        return None
    if row is None:
        return None
    return (Fence("workflow", str(row["id"]), owner, int(row["fencing_token"]),
                  row["lease_expiry"]), State(row["state"]))


async def release(fence: Fence) -> bool:
    """Give a workflow's lease back. The token stays, so it keeps growing."""
    from .state import fetch_one
    try:
        row = await fetch_one(
            """UPDATE workflows SET lease_owner = NULL, lease_expiry = NULL
                WHERE id = $1 AND fencing_token = $2 AND lease_owner = $3
            RETURNING id""",
            int(fence.key), fence.token, fence.owner)
    except Exception as exc:  # noqa: BLE001
        log.warning("workflow %s lease not released (%s); it lapses on its own",
                    fence.key, type(exc).__name__)
        return False
    return row is not None


async def get(workflow_id: int) -> dict[str, Any] | None:
    from .state import fetch_one
    try:
        row = await fetch_one(
            """SELECT id, workflow_type, workflow_version, correlation_id, state,
                      current_step, proposal_id, approved_payload_hash,
                      attempt_count, fencing_token, lease_owner, lease_expiry,
                      reconciliation_state, evidence_refs, provider_request_ids,
                      result_summary, remaining_unknowns
                 FROM workflows WHERE id = $1""", workflow_id)
    except Exception as exc:
        raise DurableUnavailable(type(exc).__name__) from exc
    return dict(row) if row is not None else None


async def _quiet_audit(audit_fn: Any, event: str, workflow_id: int,
                       data: dict[str, Any]) -> None:
    try:
        await audit_fn(event, f"workflow:{workflow_id}", data)
    except Exception:  # noqa: BLE001
        log.warning("%s for workflow %s not audited", event, workflow_id)
