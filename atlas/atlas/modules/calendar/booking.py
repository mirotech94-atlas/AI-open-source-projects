"""Booking with third parties -- proposing times by email, then creating the
event once one is chosen.

`offer_slots` composes a NEW message through Gmail send (not a reply into an
existing thread the way `mail.draft_reply` writes a draft), so it is Tier ASK
and `irreversible=True`: once sent it cannot be recalled the way a draft can
be deleted. `book_with` creates a real event with an outside attendee and
`sendUpdates=all`, so it is Tier ASK as well; its rollback, `cancel_booking`,
deletes the event and notifies the attendee -- silently removing it would
leave them holding a slot that no longer exists.

Endpoints assumed: Gmail `POST /messages/send` (raw RFC 2822, base64url) for
`offer_slots`, using the same `GOOGLE_*` refresh token `mail._access_token`
already manages; Google Calendar v3 `events.insert` / `events.delete` for
`book_with` / `cancel_booking`, both `sendUpdates=all`.
"""
from __future__ import annotations

import base64
from datetime import datetime, time, timedelta

import httpx

from ...kernel.contracts import ActionResult
from .guard import Block, overlaps_any

CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


async def _mail_token() -> str:
    from ..mail import _access_token  # same GOOGLE_* token as drafts
    return await _access_token()


async def offer_slots(to: str, subject: str, slots: str) -> ActionResult:
    """Email a third party a set of proposed meeting times. Tier ASK,
    irreversible: a sent email cannot be un-sent the way a draft can."""
    tok = await _mail_token()
    body = (f"Hi,\n\nWould any of these work for a quick meeting?\n\n{slots}\n\n"
           f"Best,\nMiro")
    raw = base64.urlsafe_b64encode(
        f"To: {to}\r\nSubject: {subject}\r\n"
        f"MIME-Version: 1.0\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{GMAIL_API}/messages/send",
                         headers={"Authorization": f"Bearer {tok}"},
                         json={"raw": raw})
        r.raise_for_status()
        sent = r.json()
    return ActionResult(True, f"sent proposed times to {to}",
                        evidence={"message_id": sent.get("id", "")})


async def offer_slots_dry(to: str, subject: str, slots: str) -> ActionResult:
    if not slots.strip():
        return ActionResult(False, "no slots to propose")
    return ActionResult(True, f"would email {to} ({subject!r}) proposing: {slots}")


async def book_with(to: str, summary: str, start: str, minutes: int = 30) -> ActionResult:
    """Create the event with the third party as an attendee and notify them.
    Tier ASK; rollback is `cancel_booking`. Times are in the calendar's own
    zone (RA-E2); the same slot with the same attendee asked for twice sends
    no second invite -- it returns the first event, with no rollback token."""
    from .zone import find_duplicate, slot_for
    slot = await slot_for(start, minutes)
    if isinstance(slot, str):
        return ActionResult(False, slot)
    existing = await find_duplicate(slot, summary=summary, attendees=[to])
    if existing is not None:
        return ActionResult(True, f"already booked with {to} at "
                                  f"{slot.start:%a %d %b %H:%M %Z}; no second invite",
                            evidence={**slot.evidence(), "event_id": existing.get("id"),
                                      "duplicate": True})
    tok = await _mail_token()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{CALENDAR_API}/calendars/primary/events",
                         headers={"Authorization": f"Bearer {tok}"},
                         params={"sendUpdates": "all"},
                         json={"summary": summary, **slot.body(),
                               "attendees": [{"email": to}]})
        r.raise_for_status()
        event = r.json()
    return ActionResult(True, f"booked '{summary}' with {to} at "
                              f"{slot.start:%a %d %b %H:%M %Z}, invite sent",
                        rollback_token=event["id"],
                        evidence={**slot.evidence(), "event_id": event["id"]})


async def book_dry(to: str, summary: str, start: str, minutes: int = 30) -> ActionResult:
    from .zone import slot_for
    slot = await slot_for(start, minutes)
    if isinstance(slot, str):
        return ActionResult(False, slot)
    note = f" ({slot.note})" if slot.note else ""
    return ActionResult(True,
                        f"would book '{summary}' with {to} at "
                        f"{slot.start:%Y-%m-%d %H:%M %Z} ({slot.zone}) for "
                        f"{minutes} min and send the invite{note}",
                        evidence=slot.evidence())


async def cancel_booking(event_id: str) -> ActionResult:
    """Rollback for `book_with`: delete the event and tell the attendee."""
    tok = await _mail_token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.delete(f"{CALENDAR_API}/calendars/primary/events/{event_id}",
                           headers={"Authorization": f"Bearer {tok}"},
                           params={"sendUpdates": "all"})
    return ActionResult(r.status_code < 300, f"cancelled booking {event_id}")


def _clear(start: datetime, end: datetime,
          busy: list[tuple[datetime, datetime]], blocks: list[Block]) -> bool:
    """True interval overlap on both busy and blocks -- a fixed regression:
    the previous version point-sampled `start` and `end - 1 minute` against
    blocks via `inside_block`, which missed a block sitting entirely inside
    a longer candidate slot (e.g. blocks="Mon-Fri 08:15-08:30", candidate
    08:00-09:00). `guard.overlaps_any` does the real interval check; this
    reuses it rather than duplicating it."""
    for b_start, b_end in busy:
        if start < b_end and end > b_start:
            return False
    return not overlaps_any(start, end, blocks)


def free_slots(busy: list[tuple[datetime, datetime]], blocks: list[Block], *,
              days: int, minutes: int, work_start: time, work_end: time
              ) -> list[datetime]:
    """Candidate meeting slots inside work hours, clear of `busy` and every
    focus block, stepping by 15 minutes over the next `days` days starting
    today. Never offers a slot already in the past.

    Work hours and the day cursor are HOST-LOCAL (the same convention
    `Block` uses -- see its docstring), regardless of what zone `busy`'s
    datetimes carry. Fixed regression: this used to anchor to
    `busy[0][0].tzinfo`, defaulting to UTC when `busy` was empty, so on a
    host whose local zone is not UTC an empty calendar made "09:00-17:00"
    come back as UTC 09:00-17:00 -- 05:00-13:00 local in America/New_York.
    `busy` is converted with `.astimezone()` before comparing, so it lines
    up with the local cursor regardless of the offset it arrived with.
    """
    duration = timedelta(minutes=minutes)
    now = datetime.now().astimezone()
    local_busy = [(b_start.astimezone(), b_end.astimezone()) for b_start, b_end in busy]
    found: list[datetime] = []
    for offset in range(days):
        day = (now + timedelta(days=offset)).date()
        cursor = datetime.combine(day, work_start).astimezone()
        day_end = datetime.combine(day, work_end).astimezone()
        while cursor + duration <= day_end:
            if cursor >= now and _clear(cursor, cursor + duration, local_busy, blocks):
                found.append(cursor)
            cursor += timedelta(minutes=15)
    return found


async def free_slots_tool(days: int = 3, minutes: int = 30) -> dict:
    """Reasoner-facing wrapper for `free_slots`, registered as the
    `calendar_free_slots` read tool: pulls real busy time off the calendar,
    reads `FOCUS_BLOCKS`, and assumes 09:00-17:00 HOST-LOCAL work hours --
    genuinely local now (see `free_slots`'s docstring for the regression
    this fixed), not UTC-dressed-as-local when the calendar has nothing on
    it."""
    import os
    from datetime import time as _time

    from . import _end, _start, _upcoming
    from .guard import parse_blocks

    try:
        events = await _upcoming(days=max(days, 1))
    except Exception as exc:                            # noqa: BLE001
        return {"error": str(exc)}
    busy = [(s, e) for ev in events if (s := _start(ev)) and (e := _end(ev))]
    blocks = parse_blocks(os.getenv("FOCUS_BLOCKS", ""))
    slots = free_slots(busy, blocks, days=days, minutes=minutes,
                       work_start=_time(9, 0), work_end=_time(17, 0))
    return {"slots": [s.isoformat() for s in slots[:20]]}
