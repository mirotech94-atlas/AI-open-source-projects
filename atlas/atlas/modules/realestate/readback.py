"""Read-back for realestate.check_deal: the `realestate_deals` row the run
said it wrote, by id. Only reads."""
from __future__ import annotations

from typing import Any

from ...kernel import state
from ...kernel.contracts import ActionResult
from ...kernel.verify import Verification, contradicted, unknown, verified, verifier


@verifier("realestate.check_deal")
async def verify_deal(params: dict[str, Any], result: ActionResult) -> Verification:
    raw = (result.evidence or {}).get("deal_id") or result.rollback_token
    try:
        deal_id = int(raw)
    except (TypeError, ValueError):
        return unknown("the run named no deal id")
    row = await state.fetch_one(
        "SELECT id, address, withdrawn_at FROM realestate_deals WHERE id = $1",
        deal_id)
    if row is None:
        return contradicted(f"deal #{deal_id} is not in realestate_deals")
    if row["withdrawn_at"] is not None:
        return contradicted(f"deal #{deal_id} was withdrawn")
    return verified(deal_id=deal_id, address=str(row["address"])[:120])
