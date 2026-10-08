"""Read-back for purchasing's prepared rows (RA-D1).

Both actions write one `purchasing_prepared` row and hand back links; neither
pays. The read-back is that row, by the id the run recorded as its rollback
token: the right kind, the approved request, the options the run stored, and
not withdrawn. It never calls eBay (its token exchange is a POST) and never
touches the cart: a prepared cart is read, never bought.

`purchasing.prepare_flight` cannot succeed today: flycheap exposes no
fare-search endpoint (`flights.search_fares` always answers with a reason), so
the run writes no row and there is nothing to read. That is unknown, class
`disabled`, with the reason -- never verified. The row read stays, so the day
flycheap gets a fare search the verifier works unchanged.

Reads only. See docs/verification.md.
"""
from __future__ import annotations

import json
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    unknown,
    verified,
    verifier,
)


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _options(value: Any) -> list[Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return list(value) if isinstance(value, list) else None


async def _read_prepared(row_id: int, kind: str, request: str,
                         result: ActionResult) -> Verification:
    from ...kernel import state
    row = await state.fetch_one(
        "SELECT id, kind, request, options, checkout_url, withdrawn "
        "FROM purchasing_prepared WHERE id = $1", row_id)
    if row is None:
        return contradicted(f"prepared {kind} #{row_id} is not in "
                            f"purchasing_prepared", prepared_id=row_id)
    stored = _options(row["options"])
    evidence: dict[str, Any] = {
        "prepared_id": row_id, "kind": row["kind"], "withdrawn": bool(row["withdrawn"]),
        "options": len(stored) if stored is not None else None,
        "has_checkout_url": bool(row["checkout_url"])}
    if row["kind"] != kind:
        return contradicted(f"prepared #{row_id} is a {row['kind']!r}, not a "
                            f"{kind!r}", FailureClass.VALIDATION, **evidence)
    if row["withdrawn"]:
        return contradicted(f"prepared {kind} #{row_id} was withdrawn", **evidence)
    if request and str(row["request"] or "") != request:
        return contradicted(f"prepared {kind} #{row_id} is for another request",
                            FailureClass.VALIDATION, **evidence)
    said = _options((result.evidence or {}).get("options"))
    if stored is None or not stored:
        return contradicted(f"prepared {kind} #{row_id} holds no options",
                            **evidence)
    if said is not None and len(said) != len(stored):
        return contradicted(f"prepared {kind} #{row_id} holds {len(stored)} "
                            f"option(s), the run stored {len(said)}", **evidence)
    if said is not None:
        want = sorted(str(o.get("url") or "") for o in said if isinstance(o, dict))
        have = sorted(str(o.get("url") or "") for o in stored if isinstance(o, dict))
        if want != have:
            return contradicted(f"prepared {kind} #{row_id}'s listing links are "
                                f"not the ones the run found", **evidence)
    want_link = (result.evidence or {}).get("checkout_url")
    if "checkout_url" in (result.evidence or {}) and \
            (row["checkout_url"] or None) != (want_link or None):
        return contradicted(f"prepared {kind} #{row_id}'s checkout link is not "
                            f"the one the run handed back", **evidence)
    return verified(**evidence)


@verifier("purchasing.prepare_cart")
async def verify_prepare_cart(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    row_id = _int(result.rollback_token)
    if row_id is None:
        return unknown("the run recorded no prepared-cart id")
    return await _read_prepared(row_id, "cart", str(params.get("items") or ""),
                                result)


@verifier("purchasing.prepare_flight")
async def verify_prepare_flight(params: dict[str, Any], result: ActionResult
                                ) -> Verification:
    row_id = _int(result.rollback_token)
    if row_id is None:
        from . import flights
        if getattr(flights, "_NO_FARE_LOOKUP", None):
            return unknown("flycheap exposes no fare-search endpoint, so "
                           "prepare_flight can never prepare a fare and there is "
                           "no row to read back", FailureClass.DISABLED)
        return unknown("the run recorded no prepared-flight id")
    request = (f"{params.get('origin')} -> {params.get('destination')} on "
               f"{params.get('date')}")
    return await _read_prepared(row_id, "flight", request, result)
