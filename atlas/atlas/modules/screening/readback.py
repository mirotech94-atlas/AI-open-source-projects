"""Read-back for `screening.book_slot` (RA-D1).

The effect is a 30-minute block on Miro's primary calendar, and its id is
the run's rollback token. The authority is that event, read by id with the
calendar module's own GET helper (the same token helper the write used):
it exists, is not cancelled, starts at the approved time (a naive start is
resolved in the calendar's zone by `calendar.zone`, as the calendar
verifiers do), runs 30 minutes, and has no attendee but Miro. Then ATLAS's
own record: the `screening_calls` row for the call, read with a SELECT,
must read `booked` with that event id.

The title names the caller's number, a stranger's, so neither the title nor
the caller is ever put in evidence.

Off: named in `ATLAS_DISABLED_MODULES`, or the `GOOGLE_*` env the calendar
write needs is missing -> unknown, class disabled, before any network call.
No reconciler: the params carry only the call sid, and the row's event id
is written after the calendar POST, so its absence proves nothing. See
docs/verification.md.
"""
from __future__ import annotations

import os
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

SLOT_MINUTES = 30


def _off() -> str | None:
    off = {m.strip().lower() for m in
           os.getenv("ATLAS_DISABLED_MODULES", "").split(",") if m.strip()}
    if "screening" in off:
        return "the screening module is switched off (ATLAS_DISABLED_MODULES)"
    from ..calendar import MODULE as CALENDAR
    missing = [v for v in CALENDAR.requires_env if not os.getenv(v)]
    if missing:
        return ("the calendar the slot is booked on is not configured: missing "
                f"env {', '.join(missing)}")
    return None


@verifier("screening.book_slot")
async def verify_book_slot(params: dict[str, Any], result: ActionResult
                           ) -> Verification:
    if (why := _off()) is not None:
        return unknown(why, FailureClass.DISABLED)
    event_id = str(result.rollback_token or (result.evidence or {}).get("event_id")
                   or "")
    call_sid = str(params.get("call_sid") or "")
    if not event_id:
        return unknown("the run recorded no event id")
    from ..calendar.readback import _compare_times, _event, _NoZone, _others
    event, failed = await _event(event_id)
    if failed is not None:
        return failed
    assert event is not None
    try:
        evidence, why = await _compare_times(
            event, {"start": params.get("start"), "minutes": SLOT_MINUTES},
            SLOT_MINUTES)
    except _NoZone:
        return unknown("the calendar's timezone could not be read, so the "
                       "approved start has no instant to compare",
                       event_id=event_id, calendar="primary")
    others = _others(event)
    evidence["attendee_count"] = len(others)
    if why:
        return contradicted(why, **evidence)
    if others:
        return contradicted(f"the hold has {len(others)} attendee(s); it must "
                            "have none", FailureClass.POLICY, **evidence)
    if not str(event.get("summary") or "").startswith("Call: "):
        return contradicted("the event is not a screening hold", **evidence)

    from ...kernel import state
    row = await state.fetch_one(
        "SELECT outcome, event_id FROM screening_calls WHERE call_sid = $1",
        call_sid)
    if row is None:
        return contradicted("the calendar hold exists but no screening_calls "
                            "row records this call", **evidence)
    evidence["outcome"] = row["outcome"]
    if row["outcome"] != "booked" or str(row["event_id"] or "") != event_id:
        return contradicted(f"the call's row reads {row['outcome']!r} and does not "
                            "carry this event id", **evidence)
    return verified(**evidence)
