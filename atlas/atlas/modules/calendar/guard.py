"""Focus guard — declared blocks where an invite needs Miro's own decision.

Reads `FOCUS_BLOCKS` (a string like "Mon-Fri 08:00-11:00 school and deep
work; Sat 09:00-12:00 family") and flags any PENDING invite -- someone else
on it, Miro's own attendee entry still `needsAction` -- that lands inside one
of those blocks. Declining is Tier ASK: the organizer sees it happen, and it
carries alternative times in the same RSVP so it is worth something rather
than just a "no". No `FOCUS_BLOCKS` set means there is nothing to guard
with, which is `Health.UNKNOWN`, never a quiet OK.

Endpoint assumed: Google Calendar v3 `events.get` and `events.patch` (the
attendee's own RSVP `responseStatus` and `comment`), against the primary
calendar, `sendUpdates=all` so the organizer is told.

`Block` and `parse_blocks` are also imported directly by the screening
module (a later task) to reuse the same focus-block grammar for calls, not
just invites -- their shape here is a contract, not a private detail.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta

import httpx

from ...kernel.contracts import ActionResult, Health, Observation

_DAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass(frozen=True)
class Block:
    """A recurring weekly window in the host's LOCAL wall-clock time, never
    UTC -- 'Mon-Fri 08:00-11:00' means 8am-11am in whatever zone the host
    process is running in, the same convention the screening module's
    `SCREENING_AVAILABILITY` shares."""

    days: frozenset[int]
    start: time
    end: time
    label: str


def _parse_time(token: str) -> time:
    # Deliberately naive: FOCUS_BLOCKS times are local wall-clock, not any
    # particular zone, so there is no offset to attach.
    return datetime.strptime(token.strip(), "%H:%M").time()  # noqa: DTZ007


def _parse_days(token: str) -> frozenset[int]:
    days: set[int] = set()
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = _DAY_INDEX[a.strip().lower()[:3]]
            end = _DAY_INDEX[b.strip().lower()[:3]]
            if start <= end:
                days.update(range(start, end + 1))
            else:                          # a range that wraps, e.g. Fri-Mon
                days.update(range(start, 7))
                days.update(range(end + 1))
        else:
            days.add(_DAY_INDEX[part.lower()[:3]])
    return frozenset(days)


def parse_blocks(spec: str) -> list[Block]:
    """"Mon-Fri 08:00-11:00 school and deep work; Sat 09:00-12:00 family".

    One `Block` per ';'-separated clause, each shaped
    `<days> <HH:MM-HH:MM> <label...>`. A clause that cannot be split into at
    least a day token and a time token is skipped rather than raising --
    a typo in FOCUS_BLOCKS should shrink the guard, not crash the check.
    The `HH:MM` values are the host's LOCAL wall-clock time (see `Block`).
    """
    blocks: list[Block] = []
    for clause in spec.split(";"):
        clause = clause.strip()
        if not clause:
            continue
        parts = clause.split(None, 2)
        if len(parts) < 2:
            continue
        days_token, time_token = parts[0], parts[1]
        label = parts[2].strip() if len(parts) > 2 else ""
        start_s, _, end_s = time_token.partition("-")
        if not end_s:
            continue
        blocks.append(Block(days=_parse_days(days_token),
                            start=_parse_time(start_s),
                            end=_parse_time(end_s),
                            label=label))
    return blocks


def inside_block(start: datetime, blocks: list[Block]) -> Block | None:
    """The block `start` falls in, or None. Boundary is [start, end): an
    event beginning exactly at a block's end time is not inside it.

    Blocks are the host's LOCAL wall-clock time (see `Block`), so an aware
    `start` is converted with `.astimezone()` (no args: the host's local
    zone) before comparing; a naive `start` is treated as already local,
    which is `astimezone()`'s own rule for naive input.
    """
    local = start.astimezone()
    t = local.time()
    weekday = local.weekday()
    for block in blocks:
        if weekday in block.days and block.start <= t < block.end:
            return block
    return None


def overlaps_any(start: datetime, end: datetime, blocks: list[Block]) -> bool:
    """True interval overlap: whether [start, end) crosses any block on
    `start`'s LOCAL day (see `inside_block`), whichever of the two spans the
    other. `booking._clear` reuses this rather than duplicating it -- the
    previous version there point-sampled `start` and `end - 1 minute`
    through `inside_block`, which missed a block sitting entirely inside a
    longer candidate slot. Meetings that cross midnight are out of scope --
    none of the callers here ever propose one.
    """
    local_start = start.astimezone()
    local_end = end.astimezone()
    day = local_start.date()
    weekday = local_start.weekday()
    for block in blocks:
        if weekday not in block.days:
            continue
        b_start = datetime.combine(day, block.start, tzinfo=local_start.tzinfo)
        b_end = datetime.combine(day, block.end, tzinfo=local_start.tzinfo)
        if local_start < b_end and local_end > b_start:
            return True
    return False


def alternatives(start: datetime, blocks: list[Block], minutes: int) -> list[datetime]:
    """Three slots after `start`, `minutes` long, that clear every block.

    Stepped in 15-minute increments over a 14-day horizon. Fewer than three
    come back only if the blocks leave almost nothing free in that window --
    the caller (`decline_invite`'s prose) is free to say so.
    """
    duration = timedelta(minutes=minutes)
    step = timedelta(minutes=15)
    horizon = start + timedelta(days=14)
    candidate = start
    found: list[datetime] = []
    while len(found) < 3 and candidate < horizon:
        candidate = candidate + step
        if not overlaps_any(candidate, candidate + duration, blocks):
            found.append(candidate)
    return found


async def check_focus_invites() -> Observation:
    """Pending invites -- someone else on the guest list, Miro's own RSVP
    still `needsAction` -- landing inside a declared focus block."""
    spec = os.getenv("FOCUS_BLOCKS")
    if not spec:
        return Observation("focus_invites", Health.UNKNOWN,
                           detail="FOCUS_BLOCKS is not set; nothing to guard with")

    blocks = parse_blocks(spec)
    from . import _start, _upcoming  # lazy: avoid the init cycle
    try:
        events = await _upcoming(14)
    except Exception as exc:                            # noqa: BLE001
        return Observation("focus_invites", Health.UNKNOWN, detail=str(exc))

    hits: list[dict] = []
    for event in events:
        attendees = event.get("attendees") or []
        me = next((a for a in attendees if a.get("self")), None)
        others = [a for a in attendees if not a.get("self")]
        if not others or me is None or me.get("responseStatus") != "needsAction":
            continue
        start = _start(event)
        if start is None:
            continue
        block = inside_block(start, blocks)
        if block is not None:
            hits.append({"id": event.get("id", ""),
                        "summary": event.get("summary") or "(no title)",
                        "block": block.label})

    return Observation(
        "focus_invites",
        Health.DEGRADED if hits else Health.OK,
        float(len(hits)), "invites",
        f"{len(hits)} pending invite(s) land inside a focus block"
        if hits else "no pending invites inside a focus block",
        evidence={"events": hits})


async def decline_invite(event_id: str, alternatives: str) -> ActionResult:
    """Decline on Miro's behalf and hand the organizer alternative times in
    the same RSVP comment. Tier ASK and irreversible -- it is visible to
    them, and cannot be made to look like it never happened.

    The `alternatives` parameter shadows the module-level `alternatives()`
    function for the rest of this body; the name is fixed by the builtin
    tool schema (`atlas/tools/builtin.py`), so it stays as given.
    """
    from . import API, _api, _token  # lazy: avoid the init cycle

    event = await _api(f"/calendars/primary/events/{event_id}")
    updated = []
    found_self = False
    for attendee in event.get("attendees") or []:
        if attendee.get("self"):
            attendee = {**attendee, "responseStatus": "declined",
                       "comment": alternatives}
            found_self = True
        updated.append(attendee)
    if not found_self:
        return ActionResult(False, f"event {event_id} has no attendee marked 'self'")

    tok = await _token()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.patch(f"{API}/calendars/primary/events/{event_id}",
                          headers={"Authorization": f"Bearer {tok}"},
                          params={"sendUpdates": "all"},
                          json={"attendees": updated})
        r.raise_for_status()
    return ActionResult(True, f"declined and offered alternatives: {alternatives}")


async def decline_dry(event_id: str, alternatives: str) -> ActionResult:
    if not event_id:
        return ActionResult(False, "no event id given")
    return ActionResult(True, f"would decline {event_id} and offer: {alternatives}")


async def alternatives_tool(start: str, minutes: int = 30) -> dict:
    """Reasoner-facing wrapper for `alternatives`, registered as the
    `calendar_alternatives` read tool: parses the ISO `start` and reads
    `FOCUS_BLOCKS`."""
    try:
        begin = datetime.fromisoformat(start)
    except ValueError:
        return {"error": f"{start!r} is not an ISO datetime"}
    blocks = parse_blocks(os.getenv("FOCUS_BLOCKS", ""))
    return {"slots": [s.isoformat() for s in alternatives(begin, blocks, minutes)]}
