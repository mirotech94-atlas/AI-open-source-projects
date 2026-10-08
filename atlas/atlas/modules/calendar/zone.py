"""The calendar's own timezone, used explicitly on every write and read-back
(RA-E2, plan D11).

One resolver, `resolve`, turns an approved `start` into an instant in the
calendar's zone. The write side (`create_block`, `book_with`, their dry
runs) and the read-back side (`readback`) both call it, so they cannot
disagree about what "09:00" meant on a daylight-saving day.

  * An ISO start with an offset is an instant already; it is kept, and
    shown in the calendar's zone.
  * A naive start is wall-clock time in the calendar's zone -- never the
    host's, never UTC.
  * A naive time inside the spring-forward gap (02:30 on the day New York
    skips from 02:00 to 03:00) does not exist. It is refused with the
    reason; Python would otherwise quietly move it an hour.
  * A naive time inside the repeated fall-back hour (01:30 happens twice)
    is ambiguous. The first occurrence (`fold=0`, still daylight time) is
    used, and the evidence says which one; an offset in the input picks the
    other.
  * The end is start + minutes of real elapsed time, computed in UTC --
    adding a timedelta to a zone-aware datetime is wall-clock arithmetic
    and is an hour off across a transition.

The zone is read from `GET /calendars/primary` (`timeZone`). When it cannot
be read, nothing is written: guessing UTC is how a 09:00 block lands at
05:00.

Duplicates: `find_duplicate` looks for a non-cancelled event with the same
start instant and end, and the same attendee set (for an attendee-less
block, the same title too, so an unrelated personal event in the same slot
is not mistaken for it). A duplicate request returns the existing event and
creates nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ZoneError(ValueError):
    """The calendar's timezone could not be established."""


class NoSuchTime(ValueError):
    """A wall-clock time that does not exist in the zone (the DST gap)."""


@dataclass(frozen=True, slots=True)
class Slot:
    start: datetime         # aware, in the calendar's zone
    end: datetime           # aware, in the calendar's zone
    zone: str
    naive: bool             # the approved start carried no offset
    note: str = ""          # which occurrence of an ambiguous time was used

    def body(self) -> dict[str, dict[str, str]]:
        """The Calendar API start/end, with the offset AND the zone named."""
        return {"start": {"dateTime": self.start.isoformat(), "timeZone": self.zone},
                "end": {"dateTime": self.end.isoformat(), "timeZone": self.zone}}

    def evidence(self) -> dict[str, Any]:
        out: dict[str, Any] = {"timezone": self.zone,
                               "start": self.start.isoformat(),
                               "end": self.end.isoformat(),
                               "approved_naive": self.naive}
        if self.note:
            out["occurrence"] = self.note
        return out


def _zoneinfo(zone: str | None) -> ZoneInfo:
    if not zone:
        raise ZoneError("the calendar's timezone could not be read")
    try:
        return ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ZoneError(f"the calendar's timezone {zone!r} is not a known zone") from exc


def resolve_start(start: str, zone: str | None) -> tuple[datetime, bool, str]:
    """(the start as an aware datetime in `zone`, whether it was naive, the
    occurrence note). ValueError for an unparseable start, NoSuchTime for a
    time in the spring-forward gap, ZoneError when there is no zone."""
    tz = _zoneinfo(zone)
    begin = datetime.fromisoformat(str(start))
    if begin.tzinfo is not None:
        return begin.astimezone(tz), False, ""
    first = begin.replace(tzinfo=tz, fold=0)
    second = begin.replace(tzinfo=tz, fold=1)
    # A wall time that survives the UTC round trip exists; one in the gap
    # comes back an hour off.
    if first.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != begin:
        raise NoSuchTime(
            f"{begin:%H:%M} on {begin:%Y-%m-%d} does not exist in {zone}: the "
            f"clocks spring forward over it. Pick a time outside the gap.")
    note = ""
    if first.utcoffset() != second.utcoffset():
        note = (f"{begin:%H:%M} happens twice on {begin:%Y-%m-%d} in {zone}; "
                f"the first ({first.tzname()}, UTC{first:%z}) was used")
    return first, True, note


def resolve(start: str, minutes: int, zone: str | None) -> Slot:
    begin, naive, note = resolve_start(start, zone)
    tz = _zoneinfo(zone)
    end = (begin.astimezone(UTC) + timedelta(minutes=int(minutes))).astimezone(tz)
    return Slot(begin, end, str(zone), naive, note)


async def slot_for(start: str, minutes: int | str) -> Slot | str:
    """The resolved slot, or why there is none -- the one gate every write
    and dry run goes through, so a dry run cannot pass what the run refuses."""
    try:
        length = int(minutes)
    except (TypeError, ValueError):
        return f"{minutes!r} is not a number of minutes"
    try:
        datetime.fromisoformat(str(start))
    except ValueError:
        return f"{start!r} is not an ISO datetime"
    try:
        return resolve(start, length, await calendar_zone())
    except (ZoneError, NoSuchTime) as exc:
        return str(exc)


async def calendar_zone() -> str:
    """The primary calendar's own timezone, read now. ZoneError when it
    cannot be read -- the caller refuses rather than guesses."""
    from . import _api
    try:
        cal = await _api("/calendars/primary")
    except Exception as exc:
        raise ZoneError(f"the calendar's timezone could not be read ({exc})") from exc
    zone = str((cal or {}).get("timeZone") or "") if isinstance(cal, dict) else ""
    _zoneinfo(zone)
    return zone


def _when(raw: dict[str, Any] | None) -> datetime | None:
    value = (raw or {}).get("dateTime")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def same_instant(a: datetime | None, b: datetime | None) -> bool:
    """Compared in UTC: Python's inter-zone == is always False for a time in
    the repeated hour (PEP 495), which would hide a fall-back duplicate."""
    if a is None or b is None:
        return False
    return a.astimezone(UTC) == b.astimezone(UTC)


def _others(event: dict[str, Any]) -> list[str]:
    return sorted(str(a.get("email") or "").lower()
                  for a in event.get("attendees") or []
                  if not a.get("self") and not a.get("organizer"))


async def find_duplicate(slot: Slot, *, summary: str,
                         attendees: list[str]) -> dict[str, Any] | None:
    """The existing event this request would duplicate, or None. Raises when
    the calendar cannot be searched: no write goes ahead unchecked."""
    from . import _api
    found = await _api("/calendars/primary/events",
                       timeMin=slot.start.isoformat(), timeMax=slot.end.isoformat(),
                       timeZone=slot.zone, singleEvents="true", maxResults=50)
    want = sorted(a.lower() for a in attendees if a)
    for event in (found or {}).get("items", []):
        if event.get("status") == "cancelled":
            continue
        if not (same_instant(_when(event.get("start")), slot.start)
                and same_instant(_when(event.get("end")), slot.end)):
            continue
        if _others(event) != want:
            continue
        if not want and str(event.get("summary") or "") != summary:
            continue
        return event
    return None
