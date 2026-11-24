"""Slice 2: one operational issue, from detection to recovery or a person
(reliability plan p. 14; docs/acceptance.md).

`kernel/fixes.sweep` still decides WHICH lasting checks are raised (acks,
`quiet_reason`, `MAX_PER_SWEEP`) and still posts what the agent said. With
ATLAS_DURABLE_CORE=on and `ops` in ATLAS_DURABLE_FLOWS, each one it raises
becomes a workflow:

    RECEIVED -> VALIDATING -> READY -> PREPARING    begin(): recorded, claimed,
                                                     an `incidents` row opened
    PREPARING: diagnose()                            bounded in time and size
    prepare(Fix, Tier 1)   PREPARING -> RUNNING      one outbox intent, with
                                                     the move, in one statement
    prepare(Fix, Tier 2)   a card; PREPARING -> NEEDS_HUMAN (the card waits)
    prepare(NextStep)      PREPARING -> NEEDS_HUMAN  one next step, owner Miro
    verify_recovery():
        RUNNING -> VERIFYING                         by the outbox, on delivery
        VERIFYING -> SUCCEEDED                       the check read OK after it
        VERIFYING -> NEEDS_HUMAN                     not OK within VERIFY_WINDOW
        RUNNING -> NEEDS_HUMAN                       the fix never ran
        NEEDS_HUMAN -> SUCCEEDED                     it recovered on its own,
                                                     read after the escalation

Deduplication: one workflow per (check, fingerprint, reminder generation).
The fingerprint is `fixes.fingerprint` (health, severity, last OK, detail
without read-times), so a second sweep of the same unchanged incident --
another process, a restart -- finds the row and does nothing. The
generation counts REMIND_HOURS since the incident began, so the weekly
reminder `quiet_reason` allows is still raised: the record never silences
what RA-D4 decided to repeat.

Diagnostics are bounded: DIAG_S of wall time and DIAG_CHARS of text. An
investigation that stops short (time, the tool-call limit, an error) is
posted as `fixes.incident_body` -- what was seen, when, impact unknown, one
next step -- and the workflow records the cause as an unknown.

Only a Tier 1 fix is queued unattended, through the outbox, so it is
dispatched by `executor.execute` with its dry run, pause and rate limits
(R03-R05 hold for it). Recovery is a READ of the check's own observations,
never a claim: the check must read OK after the fix (or after the
escalation) for the workflow to end SUCCEEDED.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..durable import DurableUnavailable, State
from ..leases import Fence

log = logging.getLogger("atlas.flows.ops")

S = State
WORKFLOW = "ops.incident"
LEASE = timedelta(minutes=35)           # the fixes step's own deadline + margin
DIAG_S = 600.0
DIAG_CHARS = 4000
VERIFY_WINDOW = timedelta(hours=2)
BATCH = 20


@dataclass(frozen=True, slots=True)
class Fix:
    """One catalog action that should clear the issue."""

    action: str
    params: dict[str, Any]
    why: str = ""


@dataclass(frozen=True, slots=True)
class NextStep:
    """What a person should do, when no action can."""

    text: str
    complete: bool = True       # False: the investigation stopped short


Plan = Fix | NextStep


@dataclass(slots=True)
class Taken:
    workflow_id: int
    fence: Fence
    key: str
    incident_id: int | None


def generation(since: datetime, now: datetime) -> int:
    from .. import fixes
    period = fixes._hours("FIX_REMIND_HOURS", fixes.REMIND_HOURS) * 3600
    if period <= 0:
        return 0
    return int(max(0.0, (now - since).total_seconds()) // period)


def correlation(item: Any, now: datetime) -> str:
    from .. import fixes
    return f"{item.key}:{fixes.fingerprint(item)}:{generation(item.since, now)}"


async def begin(item: Any, now: datetime | None = None) -> Taken | None:
    """The workflow for this incident, claimed -- or None when it already
    exists (a duplicate detection) or is held by another worker. Raises
    DurableUnavailable when it cannot be recorded."""
    from .. import durable
    from . import walk
    now = now or datetime.now(UTC)
    got = await durable.receive(
        WORKFLOW, correlation(item, now),
        payload={"key": item.key, "health": item.health, "detail": item.detail,
                 "since": item.since.isoformat(), "severity": item.severity},
        input_refs=[f"check:{item.key}"], actor="atlas.fixes",
        channel=f"module:{item.key.split('.', 1)[0]}",
        desired_outcome="the check reads OK again, or one next step with an owner")
    if not got.created:
        return None
    fence = await durable.claim(got.workflow_id, LEASE)
    if fence is None:
        return None
    if not await walk(got.workflow_id, fence, (S.RECEIVED, S.VALIDATING),
                      (S.VALIDATING, S.READY), (S.READY, S.PREPARING),
                      step="diagnosing"):
        await durable.release(fence)
        return None
    incident = await _open_incident(item, got.workflow_id)
    return Taken(got.workflow_id, fence, item.key, incident)


async def _open_incident(item: Any, workflow_id: int) -> int | None:
    """An `incidents` row for it: the table the brief and the tools already
    read. Best effort -- the workflow is the durable record."""
    from ..contracts import Severity
    from ..state import fetch_one
    try:
        severity = int(Severity[item.severity]) if item.severity else 1
    except KeyError:
        severity = int(Severity.NORMAL)
    try:
        row = await fetch_one(
            """INSERT INTO incidents (severity, title, summary, correlation_key)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            severity, f"{item.key} {item.health}"[:200],
            " ".join((item.detail or "").split())[:500],
            f"workflow:{workflow_id}")
    except Exception as exc:  # noqa: BLE001
        log.warning("incident for %s not recorded (%s)", item.key,
                    type(exc).__name__)
        return None
    return int(row["id"]) if row is not None else None


async def diagnose(produce: Callable[[], Awaitable[str]]) -> tuple[str, bool]:
    """(what was found, complete?). Bounded: DIAG_S of time, DIAG_CHARS of
    text. A timeout or an error is an incomplete investigation, said so."""
    from .. import fixes
    try:
        async with asyncio.timeout(DIAG_S):
            said = await produce()
    except TimeoutError:
        said = (f"could not diagnose it: the investigation passed its "
                f"{DIAG_S:.0f} s limit")
    except Exception as exc:  # noqa: BLE001
        from ...modules._http import explain
        said = f"could not diagnose it: {explain(exc)}"
    said = str(said or "")[:DIAG_CHARS]
    return said, not fixes.incomplete(said) and bool(said.strip())


async def prepare(taken: Taken, plan: Plan, *,
                  post: Callable[[str, str], Awaitable[Any]] | None = None
                  ) -> State:
    """Carry out the one prepared step. Returns the state it left."""
    from .. import durable, outbox
    from ..contracts import Tier
    from ..registry import REGISTRY
    wid, fence = taken.workflow_id, taken.fence
    try:
        if isinstance(plan, Fix):
            decision = REGISTRY.decisions.get(plan.action)
            if decision is not None and decision.allowed and decision.tier is Tier.AUTO:
                oid = await outbox.enqueue_with_transition(
                    wid, str(S.PREPARING), str(S.RUNNING), fence, plan.action,
                    plan.params, destination=taken.key,
                    occurrence=f"workflow:{wid}",
                    reason=(plan.why or f"fix for {taken.key}")[:300])
                if oid is not None:
                    return S.RUNNING
                return await _state(wid)
            if decision is not None and decision.allowed:
                from ..approvals import propose
                pid = await propose(plan.action, plan.params,
                                    plan.why or f"fix for {taken.key}",
                                    incident_id=taken.incident_id,
                                    dedup_key=f"ops:{wid}")
                plan = NextStep(f"Approve or decline card #{pid} ({plan.action}).")
            else:
                why = decision.reason if decision is not None else "not in the catalog"
                plan = NextStep(f"{plan.action} cannot run ({why}); check "
                                f"{taken.key.split('.', 1)[0]} by hand.")
        if post is not None:
            await post(f"{taken.key} — next step", plan.text)
        await durable.transition(
            wid, S.PREPARING, S.NEEDS_HUMAN, fence=fence, step="escalated",
            summary=plan.text[:500],
            unknowns=[] if plan.complete else ["cause", "impact"])
        return S.NEEDS_HUMAN
    finally:
        await durable.release(fence)


async def escalated(taken: Taken, said: str, complete: bool) -> None:
    """The sweep posted what the agent said (or the incomplete-incident
    card): record it as the one next step, owner Miro."""
    from .. import durable
    try:
        await durable.transition(
            taken.workflow_id, S.PREPARING, S.NEEDS_HUMAN, fence=taken.fence,
            step="escalated", summary=" ".join(said.split())[:500],
            unknowns=[] if complete else ["cause", "impact"])
    except DurableUnavailable:
        log.warning("workflow %s: escalation not recorded", taken.workflow_id)
    finally:
        await durable.release(taken.fence)


async def latest_observation(key: str) -> tuple[str | None, datetime | None]:
    """The check's newest reading: (health, when)."""
    from ..state import fetch_all
    module, _, check = key.partition(".")
    rows = await fetch_all(
        """SELECT o.health, o.at FROM observations o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id
            WHERE p.name = $1 AND c.name = $2
            ORDER BY o.at DESC LIMIT 1""", module, check)
    if not rows:
        return None, None
    return str(rows[0]["health"]), rows[0]["at"]


Observe = Callable[[str], Awaitable[tuple[str | None, datetime | None]]]


async def verify_recovery(observe: Observe | None = None,
                          now: datetime | None = None,
                          post: Callable[[str, str], Awaitable[Any]] | None = None
                          ) -> list[tuple[int, str]]:
    """Read each open ops workflow's check back. Returns [(id, new state)]."""
    from ..durable import transition
    from ..state import fetch_all
    observe = observe or latest_observation
    now = now or datetime.now(UTC)
    rows = await fetch_all(
        """SELECT id, correlation_id, state, created_at, updated_at FROM workflows
            WHERE workflow_type = $1 AND state = ANY($2::text[])
            ORDER BY id LIMIT $3""",
        WORKFLOW, [str(S.RUNNING), str(S.VERIFYING), str(S.NEEDS_HUMAN)], BATCH)
    moved: list[tuple[int, str]] = []
    for r in rows:
        wid, state = int(r["id"]), State(str(r["state"]))
        key = str(r["correlation_id"]).split(":", 1)[0]
        if state is S.RUNNING:
            why = await _fix_did_not_run(wid)
            if why and await transition(wid, S.RUNNING, S.NEEDS_HUMAN,
                                        summary=f"the fix did not run: {why}",
                                        unknowns=["cause"]):
                moved.append((wid, str(S.NEEDS_HUMAN)))
                await _post(post, key, f"The fix for {key} did not run: {why}. "
                                       f"Check it by hand.")
            continue
        try:
            health, at = await observe(key)
        except Exception as exc:  # noqa: BLE001
            log.warning("ops workflow %s: %s unreadable (%s)", wid, key,
                        type(exc).__name__)
            continue
        after = r["updated_at"] if state is S.VERIFYING else r["created_at"]
        if health == "ok" and at is not None and at > after:
            if await transition(wid, state, S.SUCCEEDED,
                                summary=f"recovered: {key} read OK at "
                                        f"{at:%Y-%m-%d %H:%M} UTC",
                                evidence_refs=[f"observation:{key}@{at.isoformat()}"],
                                unknowns=[]):
                moved.append((wid, str(S.SUCCEEDED)))
            continue
        if state is S.VERIFYING and now - r["updated_at"] > VERIFY_WINDOW:
            seen = health or "unread"
            if await transition(wid, S.VERIFYING, S.NEEDS_HUMAN,
                                summary=(f"the fix ran but {key} still reads "
                                         f"{seen}; escalated"),
                                unknowns=["why the fix did not clear it"]):
                moved.append((wid, str(S.NEEDS_HUMAN)))
                await _post(post, key, f"The fix for {key} ran, but it still reads "
                                       f"{seen}. Next step: check it by hand.")
    return moved


async def _fix_did_not_run(workflow_id: int) -> str | None:
    """The outbox row's detail when it ended without running the fix."""
    from ..state import fetch_all
    rows = await fetch_all(
        """SELECT status, detail FROM outbox
            WHERE workflow_id = $1 AND kind = 'effect' ORDER BY id""", workflow_id)
    for row in rows:
        if str(row["status"]) in ("failed", "needs_human", "superseded"):
            return str(row["detail"] or row["status"])[:300]
    return None


async def _post(post: Callable[[str, str], Awaitable[Any]] | None, key: str,
                body: str) -> None:
    if post is None:
        post = _notify
    try:
        await post(f"{key} — escalated", body)
    except Exception:  # noqa: BLE001
        log.warning("escalation for %s not posted", key)


async def _notify(title: str, body: str) -> None:
    from ..comms import Outbound, notify
    from ..contracts import Severity
    module = title.split(".", 1)[0]
    await notify(Outbound(title=title, body=body, severity=Severity.NORMAL,
                          topic=module, kind="receipt", presentation="INCIDENT"))


async def _state(workflow_id: int) -> State:
    from ..durable import get
    row = await get(workflow_id)
    return State(str(row["state"])) if row else S.FAILED


def describe(row: dict[str, Any]) -> str:
    """One line for a workflow row (used by tests and the CLI)."""
    unknowns = row.get("remaining_unknowns")
    if isinstance(unknowns, str):
        unknowns = json.loads(unknowns or "[]")
    return (f"#{row['id']} {row['state']}: {row.get('result_summary') or ''}"
            + (f" (unknown: {', '.join(unknowns)})" if unknowns else ""))
