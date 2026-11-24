"""Read-back for `delegate.claude_code` (RA-D1). The effect is ATLAS's own
row: `queue` moves a handoff from `draft` to `queued`, and the agent on the
Mac later claims and reports on it. So the authority is that row, read with
a SELECT -- never `check_queue`, which UPDATEs expired rows.

  queued, claimed, done, needs_owner, failed   the job was handed over
                                              (a failed report is the
                                              agent's outcome, not a lost
                                              handoff)
  expired                                     handed over, never claimed
                                              (contradicted: nobody ran it)
  draft, cancelled                            not handed over / withdrawn

See docs/verification.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import _hid

HANDED_OVER = frozenset({"queued", "claimed", "done", "needs_owner", "failed"})


async def _read(hid: int, started: datetime | None) -> Verification:
    from ...kernel import state
    row = await state.fetch_one(
        """SELECT status, queued_at, claimed_at, claimed_by, reported_at
             FROM handoffs WHERE id = $1""", hid)
    if row is None:
        return contradicted(f"there is no handoff #{hid}", handoff_id=hid)
    status = str(row["status"])
    evidence = {"handoff_id": hid, "status": status,
                "queued_at": row["queued_at"].isoformat() if row["queued_at"] else None,
                "claimed_by": row["claimed_by"]}
    if status in HANDED_OVER:
        queued = row["queued_at"]
        if started is not None and queued is not None and queued < started:
            return unknown(f"handoff #{hid} was queued before this run started",
                           FailureClass.STALE, **evidence)
        return verified(**evidence)
    if status == "expired":
        return contradicted(f"handoff #{hid} expired unclaimed; the agent never "
                            "ran it", **evidence)
    return contradicted(f"handoff #{hid} is {status}, not queued", **evidence)


@verifier("delegate.claude_code")
async def verify_queue(params: dict[str, Any], result: ActionResult) -> Verification:
    hid = _hid((result.evidence or {}).get("handoff_id") or result.rollback_token)
    if hid is None:
        return unknown("the run recorded no handoff id")
    if _hid(params.get("handoff_id")) not in (None, hid):
        return contradicted("the run queued a different handoff than approved",
                            handoff_id=hid)
    return await _read(hid, None)


@reconciler("delegate.claude_code")
async def reconcile_queue(params: dict[str, Any], started: datetime) -> Verification:
    hid = _hid(params.get("handoff_id"))
    if hid is None:
        return unknown("the start row names no handoff", FailureClass.VALIDATION)
    return await _read(hid, started)
