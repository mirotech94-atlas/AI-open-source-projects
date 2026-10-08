"""Google Calendar — conflicts, gaps, prep cards, a focus guard, booking.

The valuable check here is not "what's on today". It is cross-referencing
business deadlines against coursework deadlines, because those two live in
different heads and collide without warning.

Three things were added in 13.2, each in its own file:

  prep.py     a read-only prep card, 30 minutes before every meeting: who,
              the last three interactions, open items, what they probably
              want, one thing to raise. Never an Action -- nothing here
              writes anywhere.
  guard.py    `FOCUS_BLOCKS` ("Mon-Fri 08:00-11:00 school and deep work")
              and the pending invites that land inside one. Declining is
              Tier ASK and carries alternatives in the RSVP comment.
  booking.py  proposing times to a third party by email (Tier ASK,
              irreversible -- a sent email cannot be recalled) and creating
              the event once one is picked (Tier ASK, rollback cancels it).

`signals()` is polled by the kernel every `SIGNAL_INTERVAL_S` (15 minutes by
default), not on a per-event timer, so a prep card lands at the nearest
15-minute tick before the meeting -- "30 minutes out" here means the 25-35
minute window `upcoming_within` checks, not a promise of exactly 30.

Endpoints assumed: Google Calendar v3 (`events.list`, `.insert`, `.patch`,
`.delete`) against the primary calendar, and Gmail v1 `messages.send` for
`offer_slots` -- both against the `GOOGLE_*` refresh token `mail` already
manages, the same way `mail.draft_reply` does for drafts.
"""
from __future__ import annotations

import itertools
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    AgendaItem,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from .booking import book_dry, book_with, cancel_booking, offer_slots, offer_slots_dry
from .guard import check_focus_invites, decline_dry, decline_invite
from .prep import prep_card, upcoming_within

log = logging.getLogger("atlas.calendar")

API = "https://www.googleapis.com/calendar/v3"


async def _token() -> str:
    from ..mail import _access_token
    return await _access_token()


async def _api(path: str, **params):
    tok = await _token()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.get(f"{API}{path}",
                        headers={"Authorization": f"Bearer {tok}"}, params=params)
        r.raise_for_status()
        return r.json()


def _day_start(value: str, event: dict) -> datetime:
    """An all-day date starts at midnight in the CALENDAR's zone (RA-E2), not
    at UTC midnight -- in New York that is 20:00 the evening before."""
    zone = event.get("_zone") or "UTC"
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    return datetime.fromisoformat(value).replace(tzinfo=tz)


def _start(event: dict) -> datetime | None:
    raw = event.get("start", {})
    value = raw.get("dateTime") or raw.get("date")
    if not value:
        return None
    if len(value) == 10:
        return _day_start(value, event)
    return datetime.fromisoformat(value)


def _end(event: dict) -> datetime | None:
    raw = event.get("end", {})
    value = raw.get("dateTime") or raw.get("date")
    if not value:
        return None
    if len(value) == 10:
        return _day_start(value, event)
    return datetime.fromisoformat(value)


def _local_day(event: dict, when: datetime) -> str:
    """The event's day as the calendar sees it, not as UTC sees it."""
    try:
        return when.astimezone(ZoneInfo(event.get("_zone") or "UTC")).date().isoformat()
    except (ZoneInfoNotFoundError, ValueError):
        return when.date().isoformat()


async def _upcoming(days: int = 7) -> list[dict]:
    now = datetime.now(UTC)
    data = await _api("/calendars/primary/events",
                      timeMin=now.isoformat(),
                      timeMax=(now + timedelta(days=days)).isoformat(),
                      singleEvents="true", orderBy="startTime", maxResults=100)
    # One page is 100 events. A list cut at a page would draw the rest of
    # the window as free, so a second page is a failed read, not a short one.
    if data.get("nextPageToken"):
        raise RuntimeError(f"more than 100 events in the next {days} days: "
                           "not paged yet")
    # events.list answers with the calendar's own zone; each event carries
    # it so an all-day date and a day bucket are read in that zone.
    zone = str(data.get("timeZone") or "")
    items = data.get("items", [])
    for ev in items:
        ev.setdefault("_zone", zone)
    return items


async def check_conflicts() -> Observation:
    try:
        events = await _upcoming(7)
    except Exception as exc:                            # noqa: BLE001
        return Observation("conflicts", Health.UNKNOWN, detail=str(exc))

    timed = [(s, e, ev) for ev in events
             if (s := _start(ev)) and (e := _end(ev))]
    timed.sort(key=lambda t: t[0])

    overlaps: list[str] = []
    tight: list[str] = []
    for (s1, e1, a), (s2, e2, b) in itertools.pairwise(timed):
        if s2 < e1:
            overlaps.append(f"{a.get('summary','?')} overlaps {b.get('summary','?')}")
        elif (s2 - e1) < timedelta(minutes=15) and (
                a.get("location") or b.get("location")):
            tight.append(f"{a.get('summary','?')} → {b.get('summary','?')} "
                         f"({int((s2 - e1).total_seconds() / 60)} min, with travel)")

    problems = overlaps + tight
    return Observation(
        "conflicts",
        Health.DEGRADED if problems else Health.OK,
        float(len(problems)), "conflicts",
        f"{len(overlaps)} overlap(s), {len(tight)} with no travel time"
        if problems else "no conflicts in the next 7 days",
        evidence={"overlaps": overlaps, "tight": tight})


async def check_deadline_collisions() -> Observation:
    """Business deadlines against coursework. The one he doesn't see coming."""
    try:
        events = await _upcoming(14)
    except Exception as exc:                            # noqa: BLE001
        return Observation("deadline_collisions", Health.UNKNOWN, detail=str(exc))

    school_words = ("assignment", "exam", "quiz", "lab", "midterm", "final",
                    "due", "submission", "class", "lecture")
    work_words = ("deliver", "launch", "deploy", "client", "demo", "release",
                  "invoice", "meeting")

    by_day: dict[str, dict[str, list[str]]] = {}
    for ev in events:
        start = _start(ev)
        if not start:
            continue
        title = (ev.get("summary") or "").lower()
        day = _local_day(ev, start)
        bucket = by_day.setdefault(day, {"school": [], "work": []})
        if any(w in title for w in school_words):
            bucket["school"].append(ev.get("summary", ""))
        elif any(w in title for w in work_words):
            bucket["work"].append(ev.get("summary", ""))

    collisions = [f"{day}: {', '.join(v['school'])} vs {', '.join(v['work'])}"
                  for day, v in sorted(by_day.items())
                  if v["school"] and v["work"]]

    return Observation(
        "deadline_collisions",
        Health.DEGRADED if collisions else Health.OK,
        float(len(collisions)), "days",
        f"{len(collisions)} day(s) with both a course and a business deadline"
        if collisions else "no school/work collisions in the next 14 days",
        evidence={"days": collisions})


async def create_block(summary: str, start: str, minutes: int = 60) -> ActionResult:
    """Block time in the calendar's own zone (RA-E2). The same block asked
    for twice is one event: the second run returns the first and creates
    nothing, with no rollback token (its rollback would delete an event
    this run did not make)."""
    from .zone import find_duplicate, slot_for
    slot = await slot_for(start, minutes)
    if isinstance(slot, str):
        return ActionResult(False, slot)
    existing = await find_duplicate(slot, summary=summary, attendees=[])
    if existing is not None:
        return ActionResult(True, f"'{summary}' is already blocked at "
                                  f"{slot.start:%a %d %b %H:%M %Z}; not created twice",
                            evidence={**slot.evidence(), "event_id": existing.get("id"),
                                      "duplicate": True})
    tok = await _token()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{API}/calendars/primary/events",
                         headers={"Authorization": f"Bearer {tok}"},
                         json={"summary": summary, **slot.body(),
                               "description": "Blocked by ATLAS"})
        r.raise_for_status()
        event = r.json()
    return ActionResult(True, f"blocked {minutes} min for '{summary}' at "
                              f"{slot.start:%a %d %b %H:%M %Z}",
                        rollback_token=event["id"],
                        evidence={**slot.evidence(), "event_id": event["id"]})


async def create_dry(summary: str, start: str, minutes: int = 60) -> ActionResult:
    from .zone import slot_for
    slot = await slot_for(start, minutes)
    if isinstance(slot, str):
        return ActionResult(False, slot)
    note = f" ({slot.note})" if slot.note else ""
    return ActionResult(True, f"would block {minutes} min at "
                              f"{slot.start:%Y-%m-%d %H:%M %Z} ({slot.zone}) for "
                              f"'{summary}'{note}", evidence=slot.evidence())


async def delete_block(event_id: str) -> ActionResult:
    tok = await _token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.delete(f"{API}/calendars/primary/events/{event_id}",
                           headers={"Authorization": f"Bearer {tok}"})
    return ActionResult(r.status_code < 300, f"removed event {event_id}")


# Event ids a prep card has already gone out for, reset at midnight UTC. A
# module-level set rather than a table: it only needs to survive one day,
# and every SIGNAL_INTERVAL_S tick asks the same question again.
_prepped_today: set[str] = set()
_prepped_date: str | None = None


async def _prep_signals() -> tuple[Signal, ...]:
    global _prepped_date
    today = datetime.now(UTC).date().isoformat()
    if _prepped_date != today:
        _prepped_today.clear()
        _prepped_date = today

    out = []
    for event in await upcoming_within(25, 35):
        event_id = event.get("id")
        if not event_id or event_id in _prepped_today:
            continue
        summary = event.get("summary") or "(no title)"
        try:
            body = await prep_card(event)
        except Exception as exc:                            # noqa: BLE001
            # A card that fails to build (the reasoner is down, memory is
            # unreachable) must not be marked prepped -- retry next tick --
            # and must not take the rest of this tick's signals (deadlines,
            # focus invites) down with it.
            log.warning("prep card failed for event %s: %s", event_id, exc)
            continue
        _prepped_today.add(event_id)
        out.append(Signal(title=f"In 30 min: {summary}", body=body,
                          severity=Severity.NORMAL))
    return tuple(out)


class CalendarModule:
    name = "calendar"
    title = "Calendar"
    requires_env = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                    "GOOGLE_REFRESH_TOKEN")
    # Unlocks the focus guard check; UNKNOWN (not OK) until it is set --
    # there is nothing to guard with, and no default block is invented.
    optional_env = ("FOCUS_BLOCKS",)

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("conflicts", check_conflicts, timedelta(hours=4),
                  Severity.NORMAL, "overlaps and back-to-backs",
                  baseline_sigma=None),
            Check("deadline_collisions", check_deadline_collisions,
                  timedelta(hours=12), Severity.HIGH,
                  "course deadlines colliding with business deadlines",
                  baseline_sigma=None),
            Check("focus_invites", check_focus_invites, timedelta(minutes=30),
                  Severity.NORMAL,
                  "pending invites landing inside a declared focus block",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="calendar.create_block",
                tier=Tier.AUTO,     # his own calendar, no other attendees
                summary="Block time on Miro's own calendar",
                run=create_block, dry_run=create_dry, rollback=delete_block,
                params={"summary": "what the block is for",
                        "start": "ISO datetime",
                        "minutes": "duration, default 60"},
                max_per_hour=6,
            ),
            Action(
                name="calendar.offer_slots",
                tier=Tier.ASK,      # emails someone outside the household
                summary="Email a third party a set of proposed meeting times",
                run=offer_slots, dry_run=offer_slots_dry,
                rollback=None, irreversible=True,
                params={"to": "recipient email address",
                        "subject": "email subject",
                        "slots": "the proposed times, in prose"},
                max_per_hour=10,
            ),
            Action(
                name="calendar.book_with",
                tier=Tier.ASK,      # sends a calendar invite to someone else
                summary="Book a meeting with a third party and send the invite",
                run=book_with, dry_run=book_dry, rollback=cancel_booking,
                params={"to": "attendee email address",
                        "summary": "meeting title",
                        "start": "ISO datetime",
                        "minutes": "duration, default 30"},
                max_per_hour=10,
            ),
            Action(
                name="calendar.decline_invite",
                tier=Tier.ASK,      # visible to the organizer
                summary="Decline an invite landing in a focus block, with alternatives",
                run=decline_invite, dry_run=decline_dry,
                rollback=None, irreversible=True,   # the decline notice can't be recalled
                params={"event_id": "the invite's event id",
                        "alternatives": "alternative times to suggest, in prose"},
                max_per_hour=10,
            ),
        )

    async def signals(self):
        # `check_focus_invites` (below) already reports pending invites at
        # Severity.NORMAL on its own interval -- a signal here too would
        # repeat the same finding every SIGNAL_INTERVAL_S until Miro RSVPs,
        # which is exactly the double notification that gets a check muted.
        out = list(await _prep_signals())

        obs = await check_deadline_collisions()
        days = obs.evidence.get("days", [])
        if days:
            out.append(Signal(
                title="Coursework and business deadlines collide",
                body="\n".join(f"- {d}" for d in days[:5]) +
                     "\n\nWorth moving something now rather than in the week.",
                severity=Severity.HIGH))

        return tuple(out)

    async def agenda(self, within):
        """Today's events and any deadline inside the window.

        Overlap is NOT decided here — the kernel flags conflicts across every
        module's items, so an event clashing with a Fiverr delivery is caught
        the same way as two meetings clashing.
        """
        days = max(1, int(within.total_seconds() // 86400) + 1)
        events = await _upcoming(days=days)
        items = []
        for event in events:
            start = event.get("start", {})
            raw = start.get("dateTime") or start.get("date")
            if not raw:
                continue
            try:
                when = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if when.tzinfo is None:                  # an all-day date
                when = _day_start(raw[:10], event)
            end = (event.get("end", {}) or {}).get("dateTime")
            minutes = None
            if end:
                try:
                    finish = datetime.fromisoformat(end)
                    minutes = max(1, int((finish - when).total_seconds() // 60))
                except ValueError:
                    minutes = None
            summary = (event.get("summary") or "(no title)")[:90]
            where = (event.get("location") or "")[:40]
            items.append(AgendaItem(
                when=when, title=summary, kind="event",
                detail=where, minutes=minutes))
        return tuple(items)

    def brief(self) -> str:
        return "Calendar: conflicts, deadline collisions, focus guard"


MODULE = CalendarModule()
