"""Read-back for the marketing ledger's three actions (RA-D1). Every effect
is a row ATLAS owns, so each verifier SELECTs it by the id the run recorded
and compares it with what was approved. Reads only. See docs/verification.md.

  propose_experiment  the `marketing_experiments` row (business, platform,
                      variant, hypothesis hash) and the `content_drafts` row
                      it drafted. The rollback's marker -- outcome
                      `unmeasured` with the lesson "withdrawn before
                      publishing" -- or a dropped draft is contradicted.
  propose_idea        the `monetization_ideas` row; `dropped` (the rollback)
                      is contradicted.
  record_outcome      the experiment closed with that outcome and lesson,
                      and the `playbook` memory holding the lesson. The
                      rollback deletes the memory and reopens the row, so
                      either missing is contradicted.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from ...kernel import state
from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    combine,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)

WITHDRAWN = "withdrawn before publishing"   # propose_experiment_rollback's lesson


def _sha(text: Any) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:12]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _token_part(token: str | None, index: int) -> int | None:
    parts = str(token or "").split(":")
    return _int(parts[index]) if len(parts) > index else None


# ---------------------------------------------------------- propose_experiment

async def _draft_leg(draft_id: int | None) -> Verification:
    if draft_id is None:
        return unknown("the run recorded no draft id")
    row = await state.fetch_one(
        "SELECT kind, status FROM content_drafts WHERE id = $1", draft_id)
    if row is None:
        return contradicted(f"there is no draft #{draft_id}", draft_id=draft_id)
    evidence = {"draft_id": draft_id, "kind": row["kind"], "status": row["status"]}
    if row["status"] == "dropped":
        return contradicted(f"draft #{draft_id} is dropped", **evidence)
    return verified(**evidence)


@verifier("marketing.propose_experiment")
async def verify_propose_experiment(params: dict[str, Any],
                                    result: ActionResult) -> Verification:
    ev = result.evidence or {}
    eid = _int(ev.get("experiment_id")) or _token_part(result.rollback_token, 0)
    if eid is None:
        return unknown("the run recorded no experiment id")
    draft_id = _int(ev.get("draft_id")) or _token_part(result.rollback_token, 1)
    row = await state.fetch_one(
        """SELECT business, platform, hypothesis, variant, draft_id, outcome, lesson
             FROM marketing_experiments WHERE id = $1""", eid)
    if row is None:
        return contradicted(f"there is no experiment #{eid}", experiment_id=eid)
    evidence = {"experiment_id": eid, "business": row["business"],
                "platform": row["platform"], "outcome": row["outcome"],
                "hypothesis_sha256": _sha(row["hypothesis"])}
    wrong = [k for k in ("business", "platform", "variant", "hypothesis")
             if k in params and str(row[k]) != str(params[k])]
    if wrong:
        leg = contradicted(f"experiment #{eid} differs from the approved "
                           f"{', '.join(wrong)}", **evidence)
    elif row["outcome"] == "unmeasured" and row["lesson"] == WITHDRAWN:
        leg = contradicted(f"experiment #{eid} was withdrawn", **evidence)
    elif draft_id is not None and _int(row["draft_id"]) != draft_id:
        leg = contradicted(f"experiment #{eid} points at draft {row['draft_id']}, "
                           f"not #{draft_id}", **evidence)
    else:
        leg = verified(**evidence)
    return combine({"experiment": leg, "draft": await _draft_leg(draft_id)})


# ---------------------------------------------------------------- propose_idea

@verifier("marketing.propose_idea")
async def verify_propose_idea(params: dict[str, Any],
                              result: ActionResult) -> Verification:
    idea_id = _int((result.evidence or {}).get("id")) or _int(result.rollback_token)
    if idea_id is None:
        return unknown("the run recorded no idea id")
    row = await state.fetch_one(
        """SELECT business, idea, mechanism, status FROM monetization_ideas
            WHERE id = $1""", idea_id)
    if row is None:
        return contradicted(f"there is no monetization idea #{idea_id}", id=idea_id)
    evidence = {"id": idea_id, "business": row["business"],
                "mechanism": row["mechanism"], "status": row["status"],
                "idea_sha256": _sha(row["idea"])}
    wrong = [k for k in ("business", "mechanism") if k in params
             and str(row[k]) != str(params[k])]
    if "idea" in params and _sha(params["idea"]) != _sha(row["idea"]):
        wrong.append("idea")
    if wrong:
        return contradicted(f"idea #{idea_id} differs from the approved "
                            f"{', '.join(wrong)}", **evidence)
    if row["status"] == "dropped":
        return contradicted(f"idea #{idea_id} is dropped", **evidence)
    return verified(**evidence)


# -------------------------------------------------------------- record_outcome

async def _memory_leg(memory_id: int | None, lesson: str) -> Verification:
    from ...kernel.marketing.playbook import NAMESPACE as PLAYBOOK
    if memory_id is None:
        return unknown("the run recorded no memory id")
    row = await state.fetch_one(
        "SELECT namespace, content FROM memories WHERE id = $1", memory_id)
    if row is None:
        return contradicted(f"playbook memory #{memory_id} is gone",
                            memory_id=memory_id)
    evidence = {"memory_id": memory_id, "namespace": row["namespace"],
                "content_sha256": _sha(row["content"])}
    if row["namespace"] != PLAYBOOK:
        return contradicted(f"memory #{memory_id} is in {row['namespace']!r}, not "
                            f"the playbook", **evidence)
    if lesson and _sha(row["content"]) != _sha(lesson):
        return contradicted(f"memory #{memory_id} does not hold the approved "
                            "lesson", **evidence)
    return verified(**evidence)


async def _closed(eid: int, outcome: str, lesson: str,
                  started: datetime | None) -> Verification:
    row = await state.fetch_one(
        """SELECT outcome, lesson, measured_at FROM marketing_experiments
            WHERE id = $1""", eid)
    if row is None:
        return contradicted(f"there is no experiment #{eid}", experiment_id=eid)
    measured = row["measured_at"]
    evidence = {"experiment_id": eid, "outcome": row["outcome"],
                "lesson_sha256": _sha(row["lesson"]),
                "measured_at": measured.isoformat() if measured else None}
    if row["outcome"] != outcome:
        return contradicted(f"experiment #{eid} is {row['outcome']!r}, not "
                            f"{outcome!r}", **evidence)
    if lesson and _sha(row["lesson"]) != _sha(lesson):
        return contradicted(f"experiment #{eid} holds a different lesson", **evidence)
    if measured is None:
        return contradicted(f"experiment #{eid} has no measured time", **evidence)
    if started is not None and measured < started:
        return unknown(f"experiment #{eid} was closed before this run started",
                       FailureClass.STALE, **evidence)
    return verified(**evidence)


@verifier("marketing.record_outcome")
async def verify_record_outcome(params: dict[str, Any],
                                result: ActionResult) -> Verification:
    ev = result.evidence or {}
    eid = (_int(ev.get("experiment_id")) or _token_part(result.rollback_token, 0)
           or _int(params.get("experiment_id")))
    if eid is None:
        return unknown("the run recorded no experiment id")
    outcome = str(params.get("outcome") or ev.get("outcome") or "")
    lesson = str(params.get("lesson") or "")
    memory_id = _int(ev.get("memory_id")) or _token_part(result.rollback_token, 1)
    return combine({"experiment": await _closed(eid, outcome, lesson, None),
                    "playbook": await _memory_leg(memory_id, lesson)})


@reconciler("marketing.record_outcome")
async def reconcile_record_outcome(params: dict[str, Any],
                                   started: datetime) -> Verification:
    """Closed with that outcome and lesson since the run started, or not. The
    memory id died with the process, so only the experiment row is read."""
    eid = _int(params.get("experiment_id"))
    if eid is None:
        return unknown("the start row names no experiment", FailureClass.VALIDATION)
    return await _closed(eid, str(params.get("outcome") or ""),
                         str(params.get("lesson") or ""), started)
