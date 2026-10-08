"""Read-back for `mirotech.price_check` (RA-D1): unknown by design.

The action is a read-only eBay search. It writes nothing -- no listing, no
row, no file -- so there is no effect to read back. Searching again would
answer a different question (the market a minute later), not whether this
run happened, and eBay's access token comes from a refresh POST every time
(`_token` keeps no cache). So this makes no network call and never says
verified: it returns unknown with the reason and what the run itself
recorded (how many comparables, low / median / high), labelled as such.

A module that is off (named in `ATLAS_DISABLED_MODULES`, or missing the eBay
env the registry requires) is unknown, class disabled. See
docs/verification.md.
"""
from __future__ import annotations

import os
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import FailureClass, Verification, unknown, verifier

NOTHING_WRITTEN = ("mirotech.price_check is a read-only eBay search: it writes "
                   "nothing, so there is no effect to read back, and a repeat "
                   "search would need a token refresh POST")


def _off() -> str | None:
    off = {m.strip().lower() for m in
           os.getenv("ATLAS_DISABLED_MODULES", "").split(",") if m.strip()}
    if "mirotech" in off:
        return "the mirotech module is switched off (ATLAS_DISABLED_MODULES)"
    from . import MODULE
    missing = [v for v in MODULE.requires_env if not os.getenv(v)]
    if missing:
        return f"the mirotech module is off: missing env {', '.join(missing)}"
    return None


def _recorded(result: ActionResult) -> dict[str, Any]:
    prices = []
    for p in (result.evidence or {}).get("prices") or []:
        try:
            prices.append(float(p))
        except (TypeError, ValueError):
            continue
    if not prices:
        return {"run_recorded": {"comparables": 0}}
    prices.sort()
    return {"run_recorded": {"comparables": len(prices), "low": prices[0],
                             "median": prices[len(prices) // 2],
                             "high": prices[-1]}}


@verifier("mirotech.price_check")
async def verify_price_check(params: dict[str, Any], result: ActionResult
                             ) -> Verification:
    if (why := _off()) is not None:
        return unknown(why, FailureClass.DISABLED)
    return unknown(NOTHING_WRITTEN, **_recorded(result))
