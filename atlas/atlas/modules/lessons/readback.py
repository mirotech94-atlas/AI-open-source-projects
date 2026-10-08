"""Read-back for `lessons.clear` (RA-D1). The effect is on ATLAS's own
`lessons` table: no blocking row for the action and its parameters is still
refusing. The query mirrors `kernel.lessons.blocked()`'s WHERE clause but is
its own SELECT -- `blocked()` fails OPEN (a database error reads as "not
blocked"), which here would read as verified with nothing seen.

  verified      no row still refuses; the latest cleared row is evidence
  contradicted  a row still refuses (the block is active), or the run said
                it cleared one and no cleared row exists
  unknown       nothing names the action, or the table cannot be read

Reconciler: the same read, keyed on the approved action and parameters.
Reads only. See docs/verification.md.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...kernel import lessons as ledger
from ...kernel import state
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
from . import _params


async def _read(action: str, params: dict[str, Any], *, cleared: bool
                ) -> Verification:
    key = ledger.params_key(params or {})
    live = await state.fetch_one(
        """SELECT count(*) AS n FROM lessons
            WHERE action = $1 AND params_key = $2 AND kind = ANY($3)
              AND cleared_at IS NULL
              AND times >= $4
              AND last_at > now() - make_interval(days => $5)""",
        action, key, list(ledger.BLOCKING_KINDS), ledger.REPEATS_BEFORE_BLOCK,
        ledger.WINDOW_DAYS)
    if live is None:
        return unknown("the lessons ledger could not be read")
    last = await state.fetch_one(
        """SELECT id, cleared_at FROM lessons
            WHERE action = $1 AND params_key = $2 AND kind = ANY($3)
              AND cleared_at IS NOT NULL
            ORDER BY cleared_at DESC LIMIT 1""",
        action, key, list(ledger.BLOCKING_KINDS))
    evidence = {"action": action, "params_key": key[:12],
                "still_blocking": int(live["n"] or 0),
                "cleared_id": last["id"] if last else None,
                "cleared_at": last["cleared_at"].isoformat()
                if last and last["cleared_at"] else None}
    if evidence["still_blocking"]:
        return contradicted(f"{action} is still blocked by "
                            f"{evidence['still_blocking']} lesson row(s)", **evidence)
    if cleared and last is None:
        return contradicted(f"no lesson row for {action} is marked cleared",
                            **evidence)
    return verified(**evidence)


def _target(params: dict[str, Any], result: ActionResult | None
            ) -> tuple[str, dict[str, Any], bool]:
    """(action, its params, whether the run said it cleared a block). The
    rollback token, when there is one, holds exactly what was cleared."""
    token: Any = {}
    if result is not None and result.rollback_token:
        try:
            token = json.loads(result.rollback_token)
        except ValueError:
            token = {}
    if isinstance(token, dict) and token.get("action"):
        return str(token["action"]), dict(token.get("params") or {}), True
    return (str(params.get("action") or ""),
            _params(str(params.get("params_json") or "{}")), False)


@verifier("lessons.clear")
async def verify_clear(params: dict[str, Any], result: ActionResult) -> Verification:
    action, args, cleared = _target(params, result)
    if not action:
        return unknown("the run names no action", FailureClass.VALIDATION)
    return await _read(action, args, cleared=cleared)


@reconciler("lessons.clear")
async def reconcile_clear(params: dict[str, Any], started: datetime) -> Verification:
    """Blocked or not, now. Nothing is cleared again."""
    action, args, _ = _target(params, None)
    if not action:
        return unknown("the start row names no action", FailureClass.VALIDATION)
    return await _read(action, args, cleared=False)
