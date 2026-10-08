"""Slice 3: one content job, from the rendered file to a reconciled receipt
(reliability plan p. 14; docs/acceptance.md).

With ATLAS_DURABLE_CORE=on and `content` in ATLAS_DURABLE_FLOWS,
`kernel/reels.post_or_card` records each reel it handles as a workflow:

    RECEIVED -> VALIDATING                      the quality gate on the file
    VALIDATING -> NEEDS_HUMAN                   a quality exception (never
                                                a silent pass; D09 / S09)
    VALIDATING -> READY
    autopost (the autopost rule allows it):
      READY -> RUNNING + one outbox intent per platform leg, in one statement
      RUNNING -> VERIFYING / RECONCILING        by the outbox, per leg
      settle(): every leg read back (`kernel.verify`) ->
          SUCCEEDED                             every leg verified
          NEEDS_HUMAN                           a leg failed, or could not
                                                be verified; what posted stays
    card (Tier 2):
      READY -> WAITING_APPROVAL                 tied to the proposal
      sync_cards(): the proposal's own status, read, never assumed ->
          READY (durable.approved: only from a claimed proposal)
          -> RUNNING -> VERIFYING -> settle()   decide() ran it, once
          DECLINED / EXPIRED / FAILED

One workflow per reel (`reel:<dedup>`), so a duplicate delivery of the same
job -- two ticks, two workers -- finds the row: the effect is queued once,
and each leg's outbox key (action, destination, occurrence) makes a second
enqueue return the same row (R01). A copy that finds the row left early by
a dead worker (RECEIVED / VALIDATING / READY, lease lapsed) takes it over
and goes on from there (R03).

A card is never run here. `approvals.decide` claims and executes it, as it
always has; this flow mirrors the claim. So an approved card cannot also be
queued, and a yes cannot be manufactured (P03, P05).

The legs are independent outbox rows. One that succeeded stays delivered
whatever the others do; one that timed out after dispatch is reconciled by
a read-back, never resent blind (D10, R04). When every leg has settled, one
receipt names each leg and what was seen -- through the outbox as a notice
row when a receipt action is registered for the publisher (so a failed
receipt is retried on its own, R07), else through `comms.notify` once, sent
only by the worker whose move ended the workflow.

The production publisher is `content.autopublish_reel`, one leg (the action
posts to all three platforms itself). Tests register a stub publisher with
one leg per platform; it never touches a provider.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..contracts import ActionResult
from ..durable import TERMINAL, DurableUnavailable, State
from ..leases import Fence

log = logging.getLogger("atlas.flows.content")

S = State
WORKFLOW = "content.reel"
LEASE = timedelta(minutes=10)
BATCH = 20
# How long a leg that reads back "unknown" is given before the workflow is
# handed to a person: a fresh post is often still processing.
VERIFY_GRACE = timedelta(minutes=30)
# In a detail post_or_card reads as "wait": the reel is queued or being
# reconciled, and a remake or a card would duplicate it.
HELD = "held by its workflow"


@dataclass(frozen=True, slots=True)
class Publisher:
    """Where an automatic post goes. `legs` are outbox destinations, one
    row each; `leg_param` names the parameter that tells the action which
    leg (None: the action posts everywhere itself)."""

    action: str
    legs: tuple[str, ...] = ("reels",)
    leg_param: str | None = None


PRODUCTION = Publisher("content.autopublish_reel")

# publisher action -> the notice action that carries its receipt. None are
# registered in production: comms.notify is not a catalog action (an owner
# decision, docs/durable-core.md), so the receipt goes through notify.
RECEIPTS: dict[str, str] = {}


def _leg_params(publisher: Publisher, params: dict[str, Any],
                leg: str) -> dict[str, Any]:
    return {**params, publisher.leg_param: leg} if publisher.leg_param else params


def correlation(dedup: str, media_id: int) -> str:
    """One workflow per render of a job: a duplicate delivery of the same
    reel finds it; a remake (a new render after a quality exception) is a
    new job."""
    return f"reel:{dedup}:{media_id}"


@dataclass(slots=True)
class Taken:
    workflow_id: int
    fence: Fence
    state: State


async def _take(dedup: str, media_id: int, params: dict[str, Any]
                ) -> tuple[Taken | None, Any]:
    """(the claimed workflow, the intake). None when another worker holds
    it or it has moved past the point a newcomer could resume it."""
    from .. import durable
    got = await durable.receive(
        WORKFLOW, correlation(dedup, media_id), payload=params,
        input_refs=[f"media:{media_id}", f"dedup:{dedup}"], actor="atlas.reels",
        channel="reels",
        desired_outcome="posted once on every leg, or a reconciled receipt")
    resumable = (S.RECEIVED, S.VALIDATING, S.READY)
    if not got.created and got.state not in resumable:
        return None, got
    fence = await durable.claim(got.workflow_id, LEASE)
    if fence is None:
        return None, got
    row = await durable.get(got.workflow_id)
    state = State(str(row["state"])) if row else got.state
    if state not in resumable:
        await durable.release(fence)
        return None, got
    return Taken(got.workflow_id, fence, state), got


async def _to_ready(taken: Taken, flaw: str | None) -> bool:
    """Walk to READY through the quality gate's verdict; a flaw ends in
    NEEDS_HUMAN with the exception as its summary."""
    from .. import durable
    from . import walk
    wid, fence = taken.workflow_id, taken.fence
    if taken.state is S.RECEIVED:
        if not await walk(wid, fence, (S.RECEIVED, S.VALIDATING),
                          step="quality gate"):
            return False
        taken.state = S.VALIDATING
    if taken.state is S.VALIDATING:
        if flaw is not None:
            from ...modules.content.quality import EXCEPTION
            await durable.transition(
                wid, S.VALIDATING, S.NEEDS_HUMAN, fence=fence,
                step="quality gate", summary=f"{EXCEPTION}: {flaw}"[:500],
                unknowns=["the file failed its quality check"])
            taken.state = S.NEEDS_HUMAN
            return False
        if not await walk(wid, fence, (S.VALIDATING, S.READY),
                          step="quality gate", evidence_refs=["quality:passed"]):
            return False
        taken.state = S.READY
    return taken.state is S.READY


async def gate_failed(params: dict[str, Any], *, dedup: str, media_id: int,
                      flaw: str) -> None:
    """Record a reel the quality gate stopped: NEEDS_HUMAN, the exception
    as its summary. Nothing is queued."""
    try:
        taken, _ = await _take(dedup, media_id, params)
    except DurableUnavailable:
        log.warning("reel %s: quality exception not recorded", media_id)
        return
    if taken is None:
        return
    from .. import durable
    try:
        await _to_ready(taken, flaw)
    finally:
        await durable.release(taken.fence)


async def autopost(params: dict[str, Any], reason: str, *, dedup: str,
                   media_id: int, publisher: Publisher = PRODUCTION,
                   flaw: str | None = None, inline: bool = True) -> ActionResult:
    """The autopost rule's path, through the outbox. What `post_or_card`
    used to get from `execute(content.autopublish_reel)`: ok when a leg
    posted; a HELD detail when the reel is queued, uncertain or already in
    flight (post_or_card waits); otherwise the refusal."""
    from ...modules.content.crosspost import ALREADY_POSTED
    from .. import durable, outbox
    try:
        taken, got = await _take(dedup, media_id, params)
    except DurableUnavailable as exc:
        # Fail closed: an effect is never dispatched without its record.
        return ActionResult(False, f"{HELD}: the record could not be written "
                                   f"({exc}), so nothing was posted")
    if taken is None:
        return _duplicate(got, media_id, ALREADY_POSTED)
    wid = taken.workflow_id
    try:
        if not await _to_ready(taken, flaw):
            if taken.state is S.NEEDS_HUMAN:
                return ActionResult(False, f"quality exception: {flaw}")
            return ActionResult(False, f"{HELD}: workflow #{wid} moved elsewhere")
        occurrence = correlation(dedup, media_id)
        first, *rest = publisher.legs
        oid = await outbox.enqueue_with_transition(
            wid, str(S.READY), str(S.RUNNING), taken.fence, publisher.action,
            _leg_params(publisher, params, first), destination=first,
            occurrence=occurrence, reason=reason[:300])
        if oid is None:
            return ActionResult(False, f"{HELD}: workflow #{wid} was taken over")
        oids = [oid]
        for leg in rest:
            queued = await outbox.enqueue(
                publisher.action, _leg_params(publisher, params, leg),
                destination=leg, occurrence=occurrence, reason=reason[:300],
                workflow_id=wid)
            oids.append(queued.outbox_id)
    except DurableUnavailable as exc:
        return ActionResult(False, f"{HELD}: the intent could not be queued ({exc})")
    finally:
        await durable.release(taken.fence)
    if not inline:
        return ActionResult(False, f"{HELD}: queued as workflow #{wid}")
    done = [await outbox.dispatch_once(only_id=o) for o in oids]
    # Concluded later, by the scheduler's settle pass (`settle_all`): a
    # platform still processing a fresh post reads back unknown for a while.
    return _summary(wid, publisher, done)


def _duplicate(got: Any, media_id: int, already: str) -> ActionResult:
    state = got.state
    if state in (S.FAILED, S.DECLINED, S.EXPIRED, S.CANCELLED):
        return ActionResult(False, f"workflow #{got.workflow_id} ended {state}")
    if state is S.BLOCKED:
        return ActionResult(False, f"{HELD}: workflow #{got.workflow_id} is blocked")
    return ActionResult(False, f"reel {media_id} {already} or in flight "
                               f"(workflow #{got.workflow_id}, {state})")


def _summary(wid: int, publisher: Publisher, done: list[Any]) -> ActionResult:
    lines, posted, open_ = [], False, False
    for d, leg in zip(done, publisher.legs, strict=False):
        if d is None:
            lines.append(f"{leg}: queued")
            open_ = True
            continue
        lines.append(f"{leg}: {d.status}" + (f" ({d.detail})" if d.detail else ""))
        posted |= d.status in ("delivered", "unverified")
        open_ |= d.status in ("pending", "reconciling", "shadowed", "stale")
    detail = "; ".join(lines)
    if posted:
        return ActionResult(True, f"workflow #{wid}: {detail}",
                            evidence={"workflow": wid})
    if open_:
        return ActionResult(False, f"{HELD}: workflow #{wid}: {detail}")
    return ActionResult(False, f"workflow #{wid}: {detail}")


async def carded(params: dict[str, Any], *, dedup: str, media_id: int, pid: int,
                 flaw: str | None = None) -> int | None:
    """A reel that became a card: its workflow waits on that proposal.
    Returns the workflow id, or None when it was not recorded."""
    from .. import durable
    from ..state import fetch_one
    try:
        taken, _ = await _take(dedup, media_id, params)
        if taken is None:
            return None
        try:
            if taken.state is S.RECEIVED:
                await durable.transition(taken.workflow_id, S.RECEIVED,
                                         S.VALIDATING, fence=taken.fence,
                                         step="quality gate")
                taken.state = S.VALIDATING
            if taken.state is S.VALIDATING:
                # A card may carry a quality exception on its first line;
                # he decides with it in front of him. Recorded as an unknown.
                await durable.transition(
                    taken.workflow_id, S.VALIDATING, S.READY, fence=taken.fence,
                    step="card", unknowns=[f"quality exception: {flaw}"] if flaw
                    else None)
            row = await fetch_one("SELECT expires_at FROM proposals WHERE id = $1",
                                  pid)
            expires = row["expires_at"] if row is not None else None
            await durable.await_approval(taken.workflow_id, pid, expires_at=expires,
                                         fence=taken.fence)
            return taken.workflow_id
        finally:
            await durable.release(taken.fence)
    except DurableUnavailable:
        log.warning("reel %s: card #%s not recorded as a workflow", media_id, pid)
        return None


async def sync_cards() -> list[tuple[int, str]]:
    """Mirror each decided card onto its workflow, from `proposals`."""
    from .. import durable
    from ..state import fetch_all
    rows = await fetch_all(
        """SELECT w.id, w.state, w.input_hash, p.id AS pid, p.status, p.params
             FROM workflows w JOIN proposals p ON p.id = w.proposal_id
            WHERE w.workflow_type = $1 AND w.state = ANY($2::text[])
            ORDER BY w.id LIMIT $3""",
        WORKFLOW, [str(S.WAITING_APPROVAL), str(S.READY)], BATCH)
    moved: list[tuple[int, str]] = []
    for r in rows:
        wid, state, status = int(r["id"]), State(str(r["state"])), str(r["status"])
        ref = [f"proposal:{r['pid']}"]
        if status == "superseded":
            # Approved, then refused before it ran: the card no longer
            # matched what would run. A fresh card asks again; this reel
            # needs a person to look at which one to post (P04).
            if await durable.transition(
                    wid, state, S.NEEDS_HUMAN, evidence_refs=ref,
                    summary=f"card #{r['pid']} changed after it was approved "
                            f"and was not run; a fresh card asks again",
                    unknowns=["which version to post"]):
                moved.append((wid, str(S.NEEDS_HUMAN)))
            continue
        if status == "held":
            # Restored from a backup as pending (kernel/restore.py): it may
            # already have run, so a person looks before anything posts.
            if await durable.transition(
                    wid, state, S.NEEDS_HUMAN, evidence_refs=ref,
                    summary=f"card #{r['pid']} came back from a backup restore "
                            f"and is held; it may already have run",
                    unknowns=["whether it already posted"]):
                moved.append((wid, str(S.NEEDS_HUMAN)))
            continue
        if state is S.WAITING_APPROVAL:
            if status in ("declined", "expired"):
                dst = S.DECLINED if status == "declined" else S.EXPIRED
                if await durable.transition(wid, state, dst, evidence_refs=ref,
                                            summary=f"card #{r['pid']} {status}"):
                    moved.append((wid, str(dst)))
                continue
            if status == "failed":
                if await durable.transition(wid, state, S.FAILED, evidence_refs=ref,
                                            summary=f"card #{r['pid']}: the "
                                                    f"approved run failed"):
                    moved.append((wid, str(S.FAILED)))
                continue
            if status not in ("approved", "executed"):
                continue
            params = r["params"]
            if isinstance(params, str):
                params = json.loads(params or "{}")
            if not await durable.approved(wid, durable.digest(params or {})):
                continue
            state = S.READY
            moved.append((wid, str(S.READY)))
            if durable.digest(params or {}) != str(r["input_hash"] or ""):
                # The card's parameters are not the ones this reel prepared.
                # decide() has already run what was approved; say so.
                await durable.transition(
                    wid, S.READY, S.NEEDS_HUMAN, evidence_refs=ref,
                    summary=f"card #{r['pid']} carried other parameters than "
                            f"the reel prepared; review what posted",
                    unknowns=["which version posted"])
                moved.append((wid, str(S.NEEDS_HUMAN)))
                continue
        if state is S.READY and status == "executed":
            if await flows_walk(wid, (S.READY, S.RUNNING), (S.RUNNING, S.VERIFYING),
                                ref=ref):
                moved.append((wid, str(S.VERIFYING)))
                await settle(wid)
        elif (state is S.READY and status == "failed"
              and await durable.transition(wid, S.READY, S.FAILED, evidence_refs=ref,
                                           summary=f"card #{r['pid']}: the approved "
                                                   f"run failed")):
            moved.append((wid, str(S.FAILED)))
    return moved


async def flows_walk(wid: int, *moves: tuple[State, State], ref: list[str]) -> bool:
    from . import walk
    return await walk(wid, None, *moves, evidence_refs=ref)


async def settle_all() -> list[int]:
    from ..state import fetch_all
    rows = await fetch_all(
        """SELECT id FROM workflows
            WHERE workflow_type = $1 AND state = ANY($2::text[])
            ORDER BY id LIMIT $3""",
        WORKFLOW, [str(S.RUNNING), str(S.VERIFYING), str(S.RECONCILING)], BATCH)
    out = []
    for r in rows:
        if await settle(int(r["id"])):
            out.append(int(r["id"]))
    return out


async def settle(workflow_id: int,
                 grace: timedelta | None = None) -> State | None:
    """Conclude one workflow once every leg has settled. Returns the state
    it moved to, or None when nothing moved (a leg still in flight, a read
    back still inside `grace`, or the move was made elsewhere)."""
    from .. import durable
    from ..state import fetch_all
    wf = await durable.get(workflow_id)
    if wf is None or State(str(wf["state"])) in TERMINAL:
        return None
    state = State(str(wf["state"]))
    if state not in (S.RUNNING, S.VERIFYING, S.RECONCILING):
        return None
    rows = await fetch_all(
        """SELECT id, action, destination, status, detail, evidence, params
             FROM outbox WHERE workflow_id = $1 AND kind = 'effect'
            ORDER BY id""", workflow_id)
    if not rows:
        if wf.get("proposal_id") is not None and state is S.VERIFYING:
            return await _settle_card(wf)
        return None
    statuses = [str(r["status"]) for r in rows]
    if any(s in ("pending", "claimed", "dispatching") for s in statuses):
        return None
    if "reconciling" in statuses:
        if state is not S.RECONCILING:
            await durable.transition(workflow_id, state, S.RECONCILING,
                                     step="reconciling a leg")
        return None
    if all(s == "shadowed" for s in statuses):
        if state is S.RUNNING:
            await durable.transition(workflow_id, S.RUNNING, S.BLOCKED,
                                     summary="shadowed: nothing was posted")
            return S.BLOCKED
        return None
    delivered = [r for r in rows if str(r["status"]) in ("delivered", "unverified")]
    if delivered and state is not S.VERIFYING:
        if not await durable.transition(workflow_id, state, S.VERIFYING,
                                        step="read back"):
            return None
        state = S.VERIFYING
    from ..verify import State as V
    lines, missing = [], []
    for r in rows:
        leg, status = str(r["destination"]), str(r["status"])
        if status in ("delivered", "unverified"):
            seen = await _read_back(r)
            line = (f"{leg}: {status}, read back {seen.state}"
                    + (f" ({seen.reason})" if seen.reason else ""))
            good = seen.state is V.VERIFIED
        else:
            line = f"{leg}: {status}" + (
                f" ({str(r['detail'] or '')[:120]})" if r["detail"] else "")
            good = False
        lines.append(line)
        if not good:
            missing.append(line)
    refs = [f"outbox:{r['id']}" for r in rows]
    if (missing and all(", read back unknown" in m for m in missing)
            and await _within(workflow_id,
                              VERIFY_GRACE if grace is None else grace)):
        return None
    dst = S.NEEDS_HUMAN if missing else S.SUCCEEDED
    unknowns = missing
    summary = "; ".join(lines)
    if not await durable.transition(workflow_id, state, dst, evidence_refs=refs,
                                    summary=summary[:1000], unknowns=unknowns):
        return None
    await _receipt(workflow_id, str(rows[0]["action"]), dst, lines)
    return dst


async def _within(workflow_id: int, grace: timedelta) -> bool:
    from ..state import fetch_one
    row = await fetch_one(
        "SELECT now() - updated_at < $2::interval AS fresh FROM workflows "
        "WHERE id = $1", workflow_id, grace)
    return bool(row and row["fresh"])


async def _read_back(row: Any) -> Any:
    from ..verify import verify
    params = row["params"]
    if isinstance(params, str):
        params = json.loads(params or "{}")
    evidence = row["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence or "{}")
    result = ActionResult(True, str(row["detail"] or ""), evidence=dict(evidence or {}))
    return await verify(str(row["action"]), params or {}, result)


async def _settle_card(wf: dict[str, Any]) -> State | None:
    """A card decide() ran: read it back from what that run recorded."""
    from .. import durable
    from ..contracts import ActionResult as Result
    from ..state import fetch_one
    from ..verify import State as V
    from ..verify import verify
    wid = int(wf["id"])
    p = await fetch_one(
        "SELECT action, params, decided_at, decided_via FROM proposals WHERE id = $1",
        int(wf["proposal_id"]))
    if p is None:
        return None
    params = p["params"]
    if isinstance(params, str):
        params = json.loads(params or "{}")
    evidence = await _finish_evidence(str(p["action"]), p["decided_at"],
                                      str(p["decided_via"] or ""))
    seen = await verify(str(p["action"]), params or {},
                        Result(True, "", evidence=evidence))
    dst = S.SUCCEEDED if seen.state is V.VERIFIED else S.NEEDS_HUMAN
    line = f"card #{wf['proposal_id']}: read back {seen.state}" + (
        f" ({seen.reason})" if seen.reason else "")
    if not await durable.transition(
            wid, S.VERIFYING, dst, summary=line,
            unknowns=[] if dst is S.SUCCEEDED else [line]):
        return None
    await _receipt(wid, str(p["action"]), dst, [line])
    return dst


async def _finish_evidence(action: str, since: datetime | None,
                           via: str) -> dict[str, Any]:
    """The evidence the approved run recorded (its `execute.finish`)."""
    from ..state import fetch_all
    if since is None:
        return {}
    rows = await fetch_all(
        """SELECT payload FROM actions_log
            WHERE event = 'execute.finish' AND subject = $1 AND at >= $2
            ORDER BY id LIMIT 5""", action, since)
    for row in rows:
        try:
            data = json.loads(row["payload"]).get("data") or {}
        except (TypeError, ValueError, AttributeError):
            continue
        if str(data.get("approved_by") or "") == via:
            evidence = data.get("evidence") or {}
            return evidence if isinstance(evidence, dict) else {}
    return {}


async def _receipt(workflow_id: int, action: str, state: State,
                   lines: list[str]) -> None:
    """One receipt per workflow, from the worker whose move ended it."""
    verdict = "posted and read back" if state is S.SUCCEEDED else "needs you"
    body = f"Reel workflow #{workflow_id} {verdict}.\n" + "\n".join(
        f"• {line}" for line in lines)
    notice = RECEIPTS.get(action)
    if notice is not None:
        from .. import outbox
        try:
            await outbox.enqueue(notice, {"text": body}, destination="miro",
                                 occurrence=f"receipt:{workflow_id}",
                                 kind="notice", workflow_id=workflow_id,
                                 reason=f"receipt for workflow #{workflow_id}")
        except DurableUnavailable:
            log.warning("receipt for workflow %s not queued", workflow_id)
        return
    from ..comms import Outbound, notify
    from ..contracts import Severity
    try:
        await notify(Outbound(title=f"Reel workflow #{workflow_id}", body=body,
                              topic="instagram_stories", severity=Severity.INFO,
                              kind="receipt"))
    except Exception:                                   # noqa: BLE001
        log.warning("receipt for workflow %s not sent", workflow_id)
