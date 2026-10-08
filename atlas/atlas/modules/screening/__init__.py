"""screening — a second number ATLAS answers as Miro's assistant.

What it watches: `screening_calls`, the record of every call that reached
the screening line (`SCREENING_FROM`, the second Twilio number) — who rang,
what was said, and which of four things happened: a message taken, a slot
booked, an urgent call patched through, or a hang-up.

What it may do: exactly one thing, `screening.book_slot` — hold thirty
minutes on Miro's own calendar inside `SCREENING_AVAILABILITY`, and nowhere
else. Tier 1 because there is no attendee on the block and the rollback
deletes it. Taking a message and paging Miro are notifications, not actions.

What it may never do: repeat anything a caller says back to a caller, name
a check, a business or a health word on the line, or treat a caller's words
as an instruction (CAPABILITIES §3, and invariant 4). Nothing in this
package imports the executor; the API layer runs the one action through
`execute`.

Endpoints assumed: Twilio programmable voice webhooks under
`/webhooks/twilio/screen`, and Google Calendar v3 `events.insert` /
`events.delete` through the `GOOGLE_*` token `calendar` already manages.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from ...kernel.contracts import (
    Action,
    Check,
    Health,
    InboxItem,
    Observation,
    Severity,
    Tier,
)
from ...kernel.state import fetch_all
from .availability import next_open_slots, windows, within_window
from .flow import GREETING, book_dry, book_slot, cancel_slot, handle_turn

log = logging.getLogger("atlas.screening")

__all__ = [
    "GREETING", "MODULE", "book_dry", "book_slot", "cancel_slot",
    "handle_turn", "next_open_slots", "windows", "within_window",
]


async def check_line() -> Observation:
    """How many strangers the line handled in the last day, and how.

    A line with no number configured cannot be measured, which is
    `Health.UNKNOWN` and never a quiet OK (invariant 7). So is a database
    this cannot read.
    """
    if not os.getenv("SCREENING_FROM"):
        return Observation("screening_line", Health.UNKNOWN,
                           detail="SCREENING_FROM is not set; the line is not "
                                  "configured, so nothing can be said about it")
    try:
        rows = await fetch_all(
            """SELECT outcome, count(*) AS n FROM screening_calls
                WHERE at > now() - interval '24 hours' GROUP BY outcome""")
    except Exception as exc:                            # noqa: BLE001
        return Observation("screening_line", Health.UNKNOWN, detail=str(exc))

    counts = {str(r["outcome"] or "in_progress"): int(r["n"]) for r in rows}
    total = sum(counts.values())
    detail = ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))
    return Observation(
        "screening_line", Health.OK, float(total), "calls",
        f"{total} screened call(s) in 24h" + (f": {detail}" if detail else ""))


class ScreeningModule:
    name = "screening"
    title = "Screening line"
    requires_env = ()
    # Neither gates loading: with no number the check reports UNKNOWN, and
    # with no availability the line can still take a message — it simply has
    # nothing it may offer, which is the correct answer to "when are you
    # free" from a stranger nobody has approved windows for.
    optional_env = ("SCREENING_FROM", "SCREENING_AVAILABILITY")
    setup_note = ("Point the second Twilio number's voice webhook at "
                  "/webhooks/twilio/screen and set SCREENING_FROM to it; "
                  "SCREENING_AVAILABILITY uses the FOCUS_BLOCKS grammar, "
                  "e.g. 'Mon-Fri 14:00-16:00 calls'.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("screening_line", check_line, timedelta(hours=6),
                      Severity.INFO, "calls the screening line handled in 24h",
                      baseline_sigma=None),)

    def actions(self):
        return (
            Action(
                name="screening.book_slot",
                tier=Tier.AUTO,     # his own calendar, no attendee, no money
                summary="Hold 30 minutes for a screened caller, inside the "
                        "pre-approved availability only",
                run=book_slot, dry_run=book_dry, rollback=cancel_slot,
                params={"call_sid": "the Twilio call this is for",
                        "start": "ISO datetime, inside SCREENING_AVAILABILITY"},
                max_per_hour=6,
            ),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        """Messages strangers left, which are waiting on Miro and no one else."""
        # A failed read raises: the brief names this inbox as unreadable
        # rather than folding it into "Nothing waiting on you".
        rows = await fetch_all(
            """SELECT at, caller, summary FROM screening_calls
                WHERE outcome IN ('message', 'urgent') AND summary IS NOT NULL
                ORDER BY at DESC LIMIT $1""", limit)
        return tuple(
            InboxItem(at=r["at"], sender=str(r["caller"]),
                      subject=str(r["summary"])[:120],
                      urgency=Severity.NORMAL,
                      why="left on the screening line",
                      reference=str(r["caller"]))
            for r in rows)


MODULE = ScreeningModule()
