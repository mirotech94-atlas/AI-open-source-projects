"""agency.meetings -- a lead wants to talk to Miro: book it, on a call or by email.

Miro, 2026-09-30. When a call goes well, ATLAS offers the lead two or three
real free half-hours on Miro's calendar, holds the one they pick, and
invites them. Since 2026-10-06 (Miro: "schedule meetings with them via
email as well") the same happens by email: a reply to an interested lead
offers the same real slots (`replies.draft_reply`), their answer is matched
to one slot in code (`match_slot`, never a model), and
`kernel.lead_followup.bookings` holds and invites.

  * **Real slots only.** `free_times` reads Miro's Google Calendar live
    (the calendar module's `_upcoming`, the same GOOGLE_* token) and offers
    only slots inside business hours -- `AGENCY_MEETING_HOURS`, default
    "Mon-Fri 10:00-17:00" in `AGENCY_MEETING_TZ` (America/New_York) --
    `AGENCY_MEETING_MINUTES` long (30), at least `AGENCY_MEETING_NOTICE_MIN`
    (120) from now, clear of every event that is not marked free. If the
    calendar cannot be read there are no slots, and the call or the email
    says Miro will send times -- a slot is never invented. A drafted reply
    that names any time outside the slots it was given is refused
    (`slot_refusal`).
  * **The hold** (`agency.hold_meeting`, Tier AUTO): an event on Miro's own
    calendar with no attendee, so nobody outside is told anything. Its dry
    run reads the calendar again and refuses a slot that is no longer free
    (never double-booked), outside hours, or in the past. Rollback deletes
    the hold.
  * **The invite** (`agency.send_invite`, Tier AUTO since 2026-10-06 under
    Miro's ruling that anything not urgent runs without a card): adds the
    lead as an attendee with a short agenda -- their site preview link and
    what the call covers -- and lets Google send the invitation, past
    `_invite_guard` (a held meeting, an email on record and not suppressed,
    a verified lead, a time still ahead). A refusal is the Tier 2 card with
    the reason. Its rollback is the hold's: the event is deleted and Google
    tells the attendee. A lead with no email gets no invite (the summary
    says so, and Miro books it himself).
  * **The reminder**: one Slack message an hour before (`remind`, claimed
    once per meeting in the same UPDATE), through the ordinary notify path.
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ...kernel.contracts import ActionResult
from . import _store, sms

API = "https://www.googleapis.com/calendar/v3"
_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_HOURS = re.compile(r"^\s*(\w{3})(?:\s*-\s*(\w{3}))?\s+(\d{1,2}):(\d{2})\s*-\s*"
                    r"(\d{1,2}):(\d{2})\s*$")
REMIND_BEFORE = timedelta(hours=1)


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("AGENCY_MEETING_TZ", "America/New_York").strip()
                        or "America/New_York")
    except Exception:                                   # noqa: BLE001
        return ZoneInfo("America/New_York")


def hours() -> tuple[frozenset[int], time, time]:
    """(weekdays, start, end) from AGENCY_MEETING_HOURS; Mon-Fri 10-17 when
    unset or unreadable."""
    match = _HOURS.match(os.getenv("AGENCY_MEETING_HOURS", "Mon-Fri 10:00-17:00"))
    if match and match.group(1).lower()[:3] in _DAYS:
        first = _DAYS[match.group(1).lower()[:3]]
        last = _DAYS.get((match.group(2) or match.group(1)).lower()[:3], first)
        days = frozenset(range(first, last + 1)) if last >= first else frozenset(
            (*range(first, 7), *range(last + 1)))
        try:
            return (days, time(int(match.group(3)), int(match.group(4))),
                    time(int(match.group(5)), int(match.group(6))))
        except ValueError:
            pass
    return frozenset(range(5)), time(10, 0), time(17, 0)


def minutes() -> int:
    return _int_env("AGENCY_MEETING_MINUTES", 30)


def notice() -> timedelta:
    return timedelta(minutes=_int_env("AGENCY_MEETING_NOTICE_MIN", 120))


def lookahead_days() -> int:
    return _int_env("AGENCY_MEETING_DAYS", 7)


# ----------------------------------------------------------- free slots

def busy_from(events: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Every event that occupies time: timed or all-day, unless marked free
    (transparent) or cancelled."""
    from ..calendar import _end, _start
    out = []
    for ev in events:
        if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
            continue
        start, end = _start(ev), _end(ev)
        if start and end:
            out.append((start, end))
    return out


def open_slots(busy: list[tuple[datetime, datetime]], now: datetime, *,
               days: int | None = None) -> list[datetime]:
    """Aware starts, on the half hour, inside business hours in the meeting
    zone, at least `notice()` away, overlapping nothing busy. Pure."""
    zone = tz()
    weekdays, start_t, end_t = hours()
    length = timedelta(minutes=minutes())
    earliest = now + notice()
    local_now = now.astimezone(zone)
    found: list[datetime] = []
    for offset in range(days or lookahead_days()):
        day = (local_now + timedelta(days=offset)).date()
        if day.weekday() not in weekdays:
            continue
        cursor = datetime.combine(day, start_t, tzinfo=zone)
        close = datetime.combine(day, end_t, tzinfo=zone)
        while cursor + length <= close:
            end = cursor + length
            if cursor >= earliest and not any(
                    cursor < b_end and end > b_start for b_start, b_end in busy):
                found.append(cursor)
            cursor += timedelta(minutes=30)
    return found


def choose(slots: list[datetime], k: int = 3) -> list[datetime]:
    """Up to `k` slots, one per day where it can, alternating morning and
    afternoon, so the offer is a real choice rather than three in a row."""
    picked: list[datetime] = []
    days_used: set = set()
    afternoon = False
    for slot in slots:
        if slot.date() in days_used or (slot.hour >= 12) != afternoon:
            continue
        picked.append(slot)
        days_used.add(slot.date())
        afternoon = not afternoon
        if len(picked) == k:
            return picked
    for slot in slots:
        if slot not in picked and len(picked) < k:
            picked.append(slot)
    return sorted(picked)


async def free_times(now: datetime | None = None
                     ) -> tuple[list[datetime] | None, str | None]:
    """Real open slots read from the calendar now, or (None, why)."""
    from ..calendar import _upcoming
    now = now or datetime.now(UTC)
    try:
        events = await _upcoming(days=lookahead_days() + 1)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return None, f"Miro's calendar could not be read: {explain(exc)}"
    return open_slots(busy_from(events), now), None


def spoken(slot: datetime) -> str:
    """"Tuesday at 11" / "Wednesday at 2:30 in the afternoon"."""
    local = slot.astimezone(tz())
    hour12 = local.hour % 12 or 12
    clock = f"{hour12}" + (f":{local.minute:02d}" if local.minute else "")
    part = "in the morning" if local.hour < 12 else "in the afternoon"
    return f"{local:%A} at {clock} {part}"


def offered_line(slot: datetime) -> str:
    """"Tuesday 7 Oct at 11:00 in the morning" -- the form a written reply
    offers, with the date so two Tuesdays are never confused."""
    local = slot.astimezone(tz())
    part = "in the morning" if local.hour < 12 else "in the afternoon"
    return f"{local:%A} {local.day} {local:%b} at {local:%H:%M} {part}"


# -------------------------------------------- times in a lead's own words
#
# 2026-10-06: a reply by email offers the slots; the lead's answer names one
# -- "Tuesday at 2", "Tue 2pm", "2:00 on Tuesday", "the first one", "option
# 2" -- and it is matched here, in code, never by a model. Lenient on how
# a time is written, strict on the set: a time that is none of the offered
# slots matches nothing, and two different matches are no match at all.

_DAY = (r"(mon(?:day)?|tue(?:s|sday)?|wed(?:s|nesday)?|thu(?:r|rs|rsday)?|fri(?:day)?"
        r"|sat(?:urday)?|sun(?:day)?)\b\.?")
_CLOCK = r"(\d{1,2})(?:[:.](\d{2}))?(?!\d)"
_MER = (r"(a\.?m\.?|p\.?m\.?|noon|in\s+the\s+morning|in\s+the\s+afternoon"
        r"|in\s+the\s+evening)")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# An optional date between the weekday and the time: "Tuesday 7 Oct at
# 11:00" (how a reply offers them), "Tuesday the 7th at 2", "Tue, Oct 7, 2pm".
_DATE = (rf"(?:(?:the\s+)?\d{{1,2}}(?:st|nd|rd|th)?(?:\s+(?:of\s+)?{_MONTH})?"
         rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?)")
# "Tuesday at 2", "Tue 2pm", "Tuesday, 2:30": a bare "Monday 10" needs an
# "at", a ":mm" or an am/pm to be a time at all.
_DAY_TIME = re.compile(
    rf"\b{_DAY}\s*,?\s*(?:{_DATE}\s*,?\s*)?(at\s+|@\s*)?{_CLOCK}(?:\s*{_MER})?",
    re.IGNORECASE)
# "2:00 on Tuesday", "2pm Tuesday", "2 on Tuesday".
_TIME_DAY = re.compile(rf"\b{_CLOCK}(?:\s*{_MER})?\s+(on\s+)?{_DAY}", re.IGNORECASE)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3}
_ORDINAL = re.compile(r"\b(?:the\s+)?(first|second|third|1st|2nd|3rd)\b"
                      r"(?:\s+(one|option|time|slot|choice))?", re.IGNORECASE)
_NUMBERED = re.compile(r"\b(?:option|number|choice|slot|time)\s*#?\s*([1-3])\b|#([1-3])\b",
                       re.IGNORECASE)
# What may follow "the second" for it to be a pick and not "the second
# week" or "the first payment".
_PICK_TAIL = frozenset(("works", "work", "is", "would", "please", "sounds", "suits",
                        "then", "for", "it", "if", "ok", "okay", "thanks", "thank",
                        "or", "and"))


def _hours(hour: int, minute_text: str | None, mer: str | None
           ) -> tuple[frozenset[int], int | None] | None:
    if not 0 <= hour <= 23:
        return None
    minute = int(minute_text) if minute_text is not None else None
    if minute is not None and not 0 <= minute <= 59:
        return None
    word = re.sub(r"[\s.]", "", (mer or "").lower())
    if word == "noon":
        return (frozenset({12}) if hour == 12 else None), minute
    if word in ("am", "inthemorning"):
        return frozenset({0 if hour == 12 else hour}), minute
    if word in ("pm", "intheafternoon", "intheevening"):
        return frozenset({hour if hour >= 12 else hour + 12}), minute
    if hour > 12:
        return frozenset({hour}), minute
    return frozenset({hour, (hour % 12) + 12}), minute


def mentioned_times(text: str) -> list[tuple[str, int, frozenset[int], int | None]]:
    """Every (phrase, weekday, possible 24h hours, minute) a text names."""
    out: list[tuple[str, int, frozenset[int], int | None]] = []
    spans: list[tuple[int, int]] = []
    for m in _DAY_TIME.finditer(text or ""):
        day, at, hour, minute, mer = m.groups()
        if not (at or minute is not None or mer):
            continue
        parsed = _hours(int(hour), minute, mer)
        if parsed is None or parsed[0] is None:
            continue
        out.append((m.group(0), _DAYS[day.lower()[:3]], parsed[0], parsed[1]))
        spans.append(m.span())
    for m in _TIME_DAY.finditer(text or ""):
        if any(s <= m.start() < e for s, e in spans):
            continue
        hour, minute, mer, on, day = m.groups()
        if not (on or minute is not None or mer):
            continue
        parsed = _hours(int(hour), minute, mer)
        if parsed is None or parsed[0] is None:
            continue
        out.append((m.group(0), _DAYS[day.lower()[:3]], parsed[0], parsed[1]))
    return out


def _fits(slot: datetime, weekday: int, hours: frozenset[int], minute: int | None) -> bool:
    local = slot.astimezone(tz())
    return (local.weekday() == weekday and local.hour in hours
            and local.minute == (minute if minute is not None else 0))


def slot_refusal(text: str, offered: list[datetime]) -> str | None:
    """Why a drafted reply may not go out: it names a time that is not one
    of the slots it was given (an invented or misread time)."""
    for phrase, weekday, hours, minute in mentioned_times(text):
        if not any(_fits(s, weekday, hours, minute) for s in offered):
            return (f"it names {phrase.strip()!r}, which is not one of the "
                    f"{len(offered)} offered time(s)")
    return None


def picked_numbers(text: str) -> set[int]:
    """"the first one", "option 2", "the second", "#3" -> {1}, {2}, {2}, {3}."""
    found: set[int] = set()
    for m in _ORDINAL.finditer(text or ""):
        word, noun = m.group(1).lower(), (m.group(2) or "").lower()
        before = (text or "")[:m.start()].rstrip().lower()
        if word[0].isdigit() and re.search(rf"{_MONTH}$", before):
            continue                                    # "Oct 1st" is a date
        rest = (text or "")[m.end():].lstrip()
        next_word = re.match(r"[a-z']+", rest.lower())
        if noun in ("one", "option", "slot", "choice") or not rest or (
                not rest[0].isalnum()) or (
                next_word and next_word.group(0) in _PICK_TAIL):
            found.add(_ORDINALS[word])
    for m in _NUMBERED.finditer(text or ""):
        found.add(int(m.group(1) or m.group(2)))
    return found


# "Tuesday at 2 doesn't work", "I can't do the first one", "the second is
# busy": a time named to turn it down is not a pick. Any of these anywhere
# in the message and nothing is matched; the drafted reply answers it.
_NEGATION = re.compile(
    r"\bnot\b|n't\b|n’t\b|\bcannot\b|\bcant\b|\bwont\b|\bbusy\b|\bunavailable\b"
    r"|\binstead\b|\bno\s+good\b|\bneither\b|\bnone\b", re.IGNORECASE)


def match_slot(text: str, offered: list[datetime]) -> datetime | None:
    """The one offered slot a lead's words pick, or None when they pick none,
    more than one, or say no to a time. Code only."""
    if _NEGATION.search(text or ""):
        return None
    picked: set[datetime] = set()
    for _phrase, weekday, hours, minute in mentioned_times(text):
        picked.update(s for s in offered if _fits(s, weekday, hours, minute))
    for n in picked_numbers(text):
        if 1 <= n <= len(offered):
            picked.add(offered[n - 1])
    return next(iter(picked)) if len(picked) == 1 else None


def parse_slots(raw: Any) -> list[datetime]:
    """The `offered_slots` column back into aware datetimes; junk is dropped."""
    out: list[datetime] = []
    for item in _store.parse_json(raw, []) or []:
        when = _parse(str(item))
        if when is not None:
            out.append(when)
    return out


# -------------------------------------------------------------- the hold

def _parse(start: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(start))
    except ValueError:
        return None
    return when if when.tzinfo else None


async def _hold_guard(lead_id: int, start: str
                      ) -> tuple[tuple[dict, datetime] | None, str | None]:
    lead = await _store.get_lead(int(lead_id))
    if lead is None:
        return None, f"no such lead: {lead_id}"
    when = _parse(start)
    if when is None:
        return None, "start must be an ISO time with its offset"
    slots, why = await free_times()
    if slots is None:
        return None, str(why)
    if when not in slots:
        return None, (f"{when.astimezone(tz()):%a %d %b %H:%M} is not an open "
                      "business-hours slot on Miro's calendar (taken, past or "
                      "outside hours)")
    return (lead, when), None


def _contact() -> str:
    from .outreach import _send_from_address
    return _send_from_address()


def agenda(lead: dict[str, Any], links: dict[str, Any]) -> str:
    preview = sms.site_url(links) or "the preview Miro built (he'll share it on the call)"
    return (f"A {minutes()}-minute call with Miro about {lead.get('name')}'s website.\n\n"
            f"Site preview: {preview}\n\n"
            "What we'll cover:\n"
            "- what your current site is costing you, from our review\n"
            "- the new site and what's included\n"
            "- price, timeline and next steps\n\n"
            f"Questions before the call: {_contact()}\n")


async def hold_meeting(lead_id: int, start: str, call_sid: str = "") -> ActionResult:
    """Tier AUTO: an event on Miro's own calendar with no attendee."""
    ready, why = await _hold_guard(int(lead_id), start)
    if ready is None:
        return ActionResult(False, f"refused: {why}")
    lead, when = ready
    end = when + timedelta(minutes=minutes())
    zone = str(tz())
    from ..calendar import _token
    tok = await _token()
    body = {"summary": f"Hold: {lead.get('name')} x Miro (website call)",
            "description": agenda(lead, await _store.lead_links(int(lead_id)))
                           + "\nHeld by ATLAS on a call; the lead is not invited yet.",
            "start": {"dateTime": when.isoformat(), "timeZone": zone},
            "end": {"dateTime": end.isoformat(), "timeZone": zone}}
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{API}/calendars/primary/events",
                         headers={"Authorization": f"Bearer {tok}"}, json=body)
        r.raise_for_status()
        event = r.json()
    event_id = str(event["id"])
    email = await _store.lead_email(int(lead_id))
    meeting_id = await _store.insert_meeting(int(lead_id), event_id, when, end,
                                             email, call_sid or None)
    return ActionResult(True, f"held {when.astimezone(tz()):%a %d %b %H:%M %Z} for "
                              f"{lead.get('name')}",
                        evidence={"event_id": event_id, "meeting_id": meeting_id,
                                  "start": when.isoformat(), "email": email},
                        rollback_token=event_id)


async def hold_meeting_dry(lead_id: int, start: str, call_sid: str = "") -> ActionResult:
    ready, why = await _hold_guard(int(lead_id), start)
    if ready is None:
        return ActionResult(False, str(why))
    lead, when = ready
    return ActionResult(True, f"would hold {when.astimezone(tz()):%a %d %b %H:%M %Z} "
                              f"on Miro's calendar for {lead.get('name')}")


async def release_hold(event_id: str) -> ActionResult:
    """Rollback for `hold_meeting` (and, after an invite, for the meeting
    itself): delete the event, telling any attendee."""
    from ..calendar import _token
    tok = await _token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.delete(f"{API}/calendars/primary/events/{event_id}",
                           headers={"Authorization": f"Bearer {tok}"},
                           params={"sendUpdates": "all"})
    ok = r.status_code < 300 or r.status_code == 410
    if ok:
        await _store.release_meeting(event_id)
    return ActionResult(ok, f"released hold {event_id}")


# ------------------------------------------------------------ the invite

async def _invite_guard(meeting_id: int) -> tuple[dict | None, str | None]:
    meeting = await _store.get_meeting(int(meeting_id))
    if meeting is None:
        return None, f"no such meeting: {meeting_id}"
    if meeting.get("status") != "held":
        return None, f"meeting {meeting_id} is {meeting.get('status')}, not a hold"
    email = (meeting.get("email") or "").strip().lower()
    if not email:
        return None, "the lead gave no email: book this one yourself"
    if await _store.is_suppressed(email):
        return None, f"{email} is on the suppression list"
    from .verify import contact_refusal
    unverified = await contact_refusal(int(meeting["lead_id"]))
    if unverified is not None:
        return None, unverified
    start = meeting.get("start_at")
    if not isinstance(start, datetime) or start <= datetime.now(UTC):
        return None, "the meeting time has passed"
    return meeting, None


async def send_invite(meeting_id: int) -> ActionResult:
    """Tier AUTO (2026-10-06): Google emails the lead the invitation. The
    rollback is `release_hold`: the event is deleted and Google tells the
    attendee it is cancelled -- an invitation withdrawn, not pretended away."""
    meeting, why = await _invite_guard(int(meeting_id))
    if meeting is None:
        return ActionResult(False, f"refused: {why}")
    lead = await _store.get_lead(int(meeting["lead_id"])) or {}
    text = agenda(lead, await _store.lead_links(int(meeting["lead_id"])))
    from ..calendar import _token
    from .consent import agency_name
    tok = await _token()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.patch(
            f"{API}/calendars/primary/events/{meeting['event_id']}",
            headers={"Authorization": f"Bearer {tok}"}, params={"sendUpdates": "all"},
            json={"summary": f"{agency_name() or 'Website'} call with Miro: "
                             f"{lead.get('name')}",
                  "description": text,
                  "attendees": [{"email": meeting["email"]}]})
        r.raise_for_status()
    await _store.mark_meeting_invited(int(meeting_id))
    return ActionResult(True, f"invited {meeting['email']} to the call with Miro",
                        evidence={"event_id": meeting["event_id"],
                                  "to": meeting["email"], "agenda": text,
                                  "start": meeting["start_at"].isoformat()
                                  if isinstance(meeting.get("start_at"), datetime)
                                  else None},
                        rollback_token=str(meeting["event_id"]))


async def send_invite_dry(meeting_id: int) -> ActionResult:
    meeting, why = await _invite_guard(int(meeting_id))
    if meeting is None:
        return ActionResult(False, str(why))
    lead = await _store.get_lead(int(meeting["lead_id"])) or {}
    text = agenda(lead, await _store.lead_links(int(meeting["lead_id"])))
    start = meeting["start_at"].astimezone(tz())
    return ActionResult(True, f"would invite {meeting['email']} to "
                              f"{start:%a %d %b %H:%M %Z}:\n{text}",
                        evidence={"to": meeting["email"], "agenda": text})


# ---------------------------------------------------------- the reminder

async def remind() -> int:
    """Slack, an hour before each booked meeting, once."""
    from ...kernel.comms import Outbound, notify
    due = await _store.claim_reminders(REMIND_BEFORE)
    for meeting in due:
        start = meeting["start_at"].astimezone(tz())
        state = ("invite sent" if meeting.get("status") == "invited"
                 else "held, the lead is not invited yet")
        await notify(Outbound(
            title=f"In an hour: call with {meeting.get('lead_name')}",
            body=(f"{start:%H:%M %Z} with {meeting.get('lead_name')} "
                  f"({meeting.get('email') or 'no email on record'}); {state}."),
            topic="agency", kind="alert"))
    return len(due)
