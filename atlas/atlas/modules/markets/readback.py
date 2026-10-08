"""Read-back for `markets.paper_rebalance` (RA-D1). The effect is ATLAS's
own `markets_playlist_holdings` row, whose id the run returns as
`evidence.snapshot_id`. It is SELECTed by that id and its weights are
recomputed from the shares and the prices it was bought at, then compared
with the approved `after` weights. A row that is gone, of another kind, or
already superseded by a later row (a rollback) is contradicted. Reads only;
no price is fetched. See docs/verification.md.
"""
from __future__ import annotations

import json
from typing import Any

from ...kernel import state
from ...kernel.contracts import ActionResult
from ...kernel.verify import Verification, contradicted, unknown, verified, verifier

TOLERANCE = 0.05          # percentage points


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


@verifier("markets.paper_rebalance")
async def verify_rebalance(params: dict[str, Any], result: ActionResult) -> Verification:
    try:
        sid = int((result.evidence or {}).get("snapshot_id"))
    except (TypeError, ValueError):
        return unknown("the run recorded no snapshot id")
    row = await state.fetch_one(
        "SELECT id, kind, proposal_id, shares, prices, prices_read_at "
        "FROM markets_playlist_holdings WHERE id = $1", sid)
    if row is None:
        return contradicted(f"playlist snapshot #{sid} is gone", snapshot_id=sid)
    evidence: dict[str, Any] = {"snapshot_id": sid, "kind": row["kind"],
                                "proposal_id": row["proposal_id"],
                                "prices_read_at": row["prices_read_at"].isoformat()}
    if row["kind"] != "rebalance":
        return contradicted(f"snapshot #{sid} is a {row['kind']!r} row", **evidence)
    later = await state.fetch_one(
        "SELECT id, kind FROM markets_playlist_holdings WHERE id > $1 "
        "ORDER BY id LIMIT 1", sid)
    if later is not None:
        return contradicted(f"snapshot #{sid} was superseded by #{later['id']} "
                            f"({later['kind']})", **evidence)
    shares = {k: float(v) for k, v in (_json(row["shares"]) or {}).items()}
    prices = {k: float(v) for k, v in (_json(row["prices"]) or {}).items()}
    if any(t not in prices for t in shares):
        return unknown(f"snapshot #{sid} lacks a price for a holding", **evidence)
    value = sum(n * prices[t] for t, n in shares.items())
    if value <= 0:
        return contradicted(f"snapshot #{sid} is worth nothing", **evidence)
    held = {t: n * prices[t] / value * 100 for t, n in shares.items()}
    want = {k: float(v) for k, v in (params.get("after") or {}).items() if float(v) > 0}
    evidence["weights"] = {t: round(w, 2) for t, w in sorted(held.items())}
    off = [t for t in set(held) | set(want)
           if abs(held.get(t, 0.0) - want.get(t, 0.0)) > TOLERANCE]
    if off:
        return contradicted(f"snapshot #{sid} holds different weights than approved "
                            f"for {', '.join(sorted(off))}", **evidence)
    return verified(**evidence)


@verifier("markets.paper_idea")
async def verify_idea(params: dict[str, Any], result: ActionResult) -> Verification:
    """The idea's own `markets_ideas` row, by `evidence.idea_id`: approved
    (or since booked) with the approved ticker, shares, stop and target.
    Cancelled is contradicted. Reads only."""
    try:
        iid = int((result.evidence or {}).get("idea_id"))
    except (TypeError, ValueError):
        return unknown("the run recorded no idea id")
    row = await state.fetch_one(
        "SELECT id, ticker, shares, stop, target, status, decided_at, entry_day "
        "FROM markets_ideas WHERE id = $1", iid)
    if row is None:
        return contradicted(f"idea #{iid} is gone", idea_id=iid)
    evidence: dict[str, Any] = {
        "idea_id": iid, "status": row["status"], "ticker": row["ticker"],
        "shares": row["shares"],
        "entry_day": row["entry_day"].isoformat() if row["entry_day"] else None}
    if row["status"] not in ("approved", "open", "closed"):
        return contradicted(f"idea #{iid} is {row['status']}", **evidence)
    for key in ("ticker", "shares"):
        if key in params and params[key] != row[key]:
            return contradicted(f"idea #{iid} has a different {key}", **evidence)
    for key in ("stop", "target"):
        if key in params and abs(float(params[key]) - float(row[key])) > 0.005:
            return contradicted(f"idea #{iid} has a different {key}", **evidence)
    return verified(**evidence)
