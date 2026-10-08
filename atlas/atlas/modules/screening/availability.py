"""Pre-approved availability — the only thing a stranger may reach.

`SCREENING_AVAILABILITY` uses exactly the `FOCUS_BLOCKS` grammar
("Mon-Fri 14:00-16:00 calls"), parsed by `calendar.guard.parse_blocks`, so
the two never drift apart. The times are the host's LOCAL wall clock, the
same convention `Block` documents.

The polarity is the opposite of the focus guard's, which is why
`guard.overlaps_any` is not the predicate here: a focus block is a window
an event must NOT touch, an availability window is one a call must sit
entirely INSIDE. Overlap is not containment, and a 15:45 slot that merely
overlaps a window ending at 16:00 runs fifteen minutes past it.

An unset `SCREENING_AVAILABILITY` is the absence of a decision, never a
blanket yes: `windows()` comes back empty and `within_window` is False for
every time there is. Endpoint assumed: Google Calendar v3 `events.list`
(through `calendar._upcoming`) for the busy time slots are checked against.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from ..calendar.guard import Block, inside_block, parse_blocks

log = logging.getLogger("atlas.screening")

HORIZON_DAYS = 7
STEP_MINUTES = 15


def windows() -> list[Block]:
    """The declared call windows. Empty when nothing is declared."""
    return parse_blocks(os.getenv("SCREENING_AVAILABILITY", ""))


def within_window(start: datetime, minutes: int = 30) -> bool:
    """Does a `minutes`-long call beginning at `start` fit inside one window?

    Both ends are checked against the SAME block: a slot may not run out of
    one window and into another, and it may not run past the end of the one
    it began in. A naive `start` is treated as host-local, which is
    `astimezone()`'s own rule and what `Block` means by its times.
    """
    blocks = windows()
    if not blocks:
        return False
    local = start.astimezone()
    block = inside_block(local, blocks)
    if block is None:
        return False
    finish = local + timedelta(minutes=minutes)
    # The call must end on the same local day, inside the same block.
    if finish.date() != local.date():
        return False
    return finish.time() <= block.end


async def _busy(days: int = HORIZON_DAYS) -> list[tuple[datetime, datetime]]:
    """Miro's real busy time over the horizon, from his calendar.

    Read-only, and the only calendar read this module does. Raises rather
    than returning an empty list on failure — "nothing on the calendar" and
    "the calendar could not be read" must not look the same to the caller.
    """
    from ..calendar import _end, _start, _upcoming
    events = await _upcoming(days=days)
    return [(s, e) for ev in events if (s := _start(ev)) and (e := _end(ev))]


async def next_open_slots(n: int = 3, minutes: int = 30) -> list[datetime]:
    """The next `n` slots that are inside a window and clear of his calendar.

    Returns an empty list when the calendar cannot be read, and the caller
    takes a message instead. Invariant 6: an offered slot that turns out not
    to be free is a number ATLAS invented, and offering one to a stranger is
    worse than offering nothing.
    """
    if not windows():
        return []
    try:
        busy = [(s.astimezone(), e.astimezone()) for s, e in await _busy()]
    except Exception as exc:                            # noqa: BLE001
        log.warning("cannot read the calendar, so no times are offered: %s", exc)
        return []

    duration = timedelta(minutes=minutes)
    step = timedelta(minutes=STEP_MINUTES)
    now = datetime.now().astimezone()
    # Start on the next step boundary at or after now.
    cursor = now.replace(second=0, microsecond=0)
    cursor += timedelta(minutes=(-cursor.minute) % STEP_MINUTES)
    horizon = now + timedelta(days=HORIZON_DAYS)

    found: list[datetime] = []
    while len(found) < n and cursor < horizon:
        if within_window(cursor, minutes) and not any(
                cursor < b_end and cursor + duration > b_start
                for b_start, b_end in busy):
            found.append(cursor)
        cursor += step
    return found
