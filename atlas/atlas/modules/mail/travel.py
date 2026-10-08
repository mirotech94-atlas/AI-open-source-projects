"""Travel organiser: booking emails into one itinerary per trip.

Read-only, and private end to end:

* **Reading.** Flight, hotel and transfer confirmations are found in every
  configured mailbox by `search.find` (GET-only Gmail, read-only IMAP).
* **Extracting.** The LOCAL model reads each one (`Need.PRIVATE`: it runs on
  the box or not at all), with the email wrapped by `wrap_untrusted`. A new
  email is read once; its id is kept in `mail_extractions`.
* **Grounding.** Nothing is kept on the model's word (`grounding`): a code,
  a flight number, a place, a date or a time that is not written in the
  email becomes None, shown as "unknown".
* **Output.** One vault note per trip, through the existing vault export
  (`api/vault_export.py`, pulled by the Mac agent into ATLAS/Trips/).
* **Check-in reminders** are PROPOSED, never made: a Signal naming
  `mac.add_reminder` becomes an approval card (kernel.scheduler
  `collect_signals` -> `approvals.propose`), and nothing reaches his Mac
  until he approves it on Slack. Online check-in is taken to open 24h before
  a domestic flight and 48h before an international one, unless the email
  says otherwise; when the email does not say which it is, the card says so
  and uses 48h, the earlier.
* **Passport.** Only if PASSPORT_EXPIRY=YYYY-MM-DD is set: a trip ending
  less than six months before it is flagged. Unset or malformed, the note
  says so and nothing is flagged or assumed.

This file adds no action. Extraction is slow on a local model, so the sweep
runs as a background task started from `MailModule.signals()`, bounded to
CAP_PER_RUN model calls, and never inside the scheduler's signals deadline.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from ...kernel.contracts import Severity, Signal
from . import grounding as g

log = logging.getLogger("atlas.mail.travel")

KINDS = ("flight", "hotel", "transfer")
GMAIL_QUERY = (
    "{subject:booking subject:confirmation subject:confirmed subject:itinerary "
    "subject:e-ticket subject:eticket subject:reservation subject:trip "
    'subject:"boarding pass"} '
    '{flight airline hotel transfer airport "booking reference" PNR "check-in"}')
IMAP_SUBJECTS = ("booking", "confirmation", "itinerary", "e-ticket",
                 "reservation", "trip")
RELEVANT = ("flight", "airline", "hotel", "transfer", "airport", "pnr",
            "booking reference", "check-in", "check in", "itinerary")
SEARCH_DAYS = 300
SEARCH_LIMIT = 40
CAP_PER_RUN = 8
SWEEP_EVERY = timedelta(hours=6)
TRIP_GAP = timedelta(days=2)
REMIND_AHEAD = timedelta(days=7)
DOMESTIC_H, INTERNATIONAL_H = 24, 48
MIN_CHECKIN_H = 12

_FLIGHT_NO = re.compile(r"^[A-Z0-9]{2}\s?\d{1,4}[A-Z]?$")
_CODE = re.compile(r"^[A-Z0-9-]{3,20}$", re.IGNORECASE)

SYSTEM = (
    "Extract the travel bookings in this email for Miro. Reply with JSON only:\n"
    '{"bookings": [{"kind": "flight" | "hotel" | "transfer", '
    '"confirmation": the booking reference or PNR exactly as written, or null, '
    '"flight_number": exactly as written (e.g. "ME 201"), or null, '
    '"origin": departure city or airport as written, or null, '
    '"destination": arrival city or airport as written, or null, '
    '"origin_country": the departure country ONLY if written in the email, '
    'else null, "destination_country": the same for arrival, '
    '"start_date": "YYYY-MM-DD" of departure, hotel check-in or pickup, or null, '
    '"start_time": "HH:MM" 24-hour, local, or null, '
    '"end_date": "YYYY-MM-DD" of arrival or hotel check-out, or null, '
    '"end_time": "HH:MM" or null, '
    '"location": hotel name or pickup address as written, or null, '
    '"checkin_opens_hours": how many hours before departure online check-in '
    "opens, ONLY if the email states it, else null}]}\n"
    "One entry per flight leg. Copy values exactly as written; never infer a "
    'value that is not in the email. If it is not a confirmation of a booking '
    '(an offer, a newsletter, a price alert), reply {"bookings": []}.')

BOOKINGS_SQL = (
    "SELECT booking_key, message_id, account, subject, kind, confirmation, "
    "flight_number, origin, destination, origin_country, destination_country, "
    "start_date, start_time, end_date, end_time, location, checkin_hours "
    "FROM travel_bookings ORDER BY start_date NULLS LAST, start_time NULLS LAST")

# What the last sweep did, for `brief_line()` and the vault home note.
_state: dict[str, Any] = {}
_task: asyncio.Task | None = None


# ---------------------------------------------------------------- grounding

def validate(raw: object, text: str) -> dict | None:
    """One model-extracted booking, kept only as far as the email bears it
    out. None when it is not a booking at all."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    out: dict[str, Any] = {"kind": raw["kind"]}
    code = raw.get("confirmation")
    out["confirmation"] = (
        code.strip() if isinstance(code, str) and _CODE.match(code.strip())
        and g.appears(code.strip(), text) else None)
    flight = raw.get("flight_number")
    flight = " ".join(flight.upper().split()) if isinstance(flight, str) else ""
    out["flight_number"] = (flight if flight and _FLIGHT_NO.match(flight)
                            and g.appears(flight, text) else None)
    for key in ("origin", "destination", "location"):
        out[key] = g.text_or_none(raw.get(key), text)
    for key in ("origin_country", "destination_country"):
        out[key] = g.text_or_none(raw.get(key), text, limit=40)
    for key in ("start_date", "end_date"):
        d = g.parse_date(raw.get(key))
        out[key] = d if d is not None and g.date_in_text(d, text) else None
    for key in ("start_time", "end_time"):
        t = g.parse_time(raw.get(key))
        out[key] = t if t is not None and g.time_in_text(t, text) else None
    out["checkin_hours"] = _stated_checkin_hours(raw.get("checkin_opens_hours"),
                                                 text)
    return out


def _stated_checkin_hours(hours: object, text: str) -> int | None:
    """The hours before departure that online check-in opens, only when a
    sentence of the email says so: the number of hours, "check-in" and
    "open" together. "Arrive at the airport 3 hours before departure" is
    in every confirmation and is not when check-in opens. Under MIN_CHECKIN_H
    is refused as a misreading of exactly that."""
    if not isinstance(hours, int) or isinstance(hours, bool) \
            or not MIN_CHECKIN_H <= hours <= 168:
        return None
    for sentence in re.split(r"[.!?\n]+", g.norm(text)):
        if (re.search(rf"(?<!\d){hours}\s*(?:h\b|hours?|hrs?)", sentence)
                and re.search(r"check[\s-]?in", sentence) and "open" in sentence):
            return hours
    return None


def _source(msg: dict) -> str:
    return f"From: {msg['sender']}\nSubject: {msg['subject']}\n\n{msg['body']}"


async def extract(msg: dict) -> list[dict] | None:
    """The bookings in one email, grounded. None when the model's reply was
    not JSON (nothing is invented to fill the gap). Raises
    LocalModelUnavailable when there is no local model: this email is then
    not marked read, and is read on a later sweep."""
    from ...kernel.llm import Message, Need, think
    from ...kernel.permissions import wrap_untrusted

    text = _source(msg)
    reply = await think(Need.PRIVATE, SYSTEM,
                        [Message("user", wrap_untrusted(text, "mail:travel"))],
                        max_tokens=1200)
    parsed = g.json_object(reply.text)
    if parsed is None or not isinstance(parsed.get("bookings"), list):
        return None
    return [b for b in (validate(raw, text) for raw in parsed["bookings"]) if b]


# ---------------------------------------------------------------- the sweep

@dataclass
class SweepResult:
    at: datetime
    read: int = 0
    found: int = 0
    unparsed: int = 0
    waiting: int = 0                  # left for the next sweep (the cap)
    model_down: str | None = None
    unreadable: list[str] = field(default_factory=list)


def _key(message_id: str, index: int) -> str:
    return hashlib.sha256(f"{message_id}#{index}".encode()).hexdigest()[:32]


def _relevant(msg: dict) -> bool:
    low = f"{msg['subject']}\n{msg['body']}".lower()
    return any(w in low for w in RELEVANT)


async def sweep(accounts=None, *, cap: int = CAP_PER_RUN) -> SweepResult:
    """Read new confirmations in every mailbox, at most `cap` model calls."""
    from ...kernel.llm import LocalModelUnavailable
    from ...kernel.state import execute_sql, fetch_all
    from .._http import redact
    from .accounts import all_accounts
    from .search import find

    result = SweepResult(at=datetime.now(UTC))
    seen = {r["message_id"] for r in await fetch_all(
        "SELECT message_id FROM mail_extractions WHERE agent = 'travel'")}
    budget = cap
    for account in (all_accounts() if accounts is None else accounts):
        who = account.email or account.name
        if not account.configured:
            result.unreadable.append(f"{who}: not configured")
            continue
        try:
            messages = await find(account, gmail_query=GMAIL_QUERY,
                                  imap_subjects=IMAP_SUBJECTS, days=SEARCH_DAYS,
                                  limit=SEARCH_LIMIT)
        except Exception as exc:                        # noqa: BLE001
            result.unreadable.append(f"{who}: {redact(exc)}"[:160])
            continue
        for msg in messages:
            if msg["id"] in seen:
                continue
            if not _relevant(msg):
                await _mark(msg, "none")
                continue
            if budget <= 0:
                result.waiting += 1
                continue
            budget -= 1
            try:
                bookings = await extract(msg)
            except LocalModelUnavailable as exc:
                result.model_down = exc.reason[:200]
                return result
            result.read += 1
            if bookings is None:
                result.unparsed += 1
                await _mark(msg, "unparsed")
                continue
            for index, b in enumerate(bookings):
                await execute_sql(
                    """INSERT INTO travel_bookings
                         (booking_key, message_id, account, subject, kind,
                          confirmation, flight_number, origin, destination,
                          origin_country, destination_country, start_date,
                          start_time, end_date, end_time, location, checkin_hours)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                               $15,$16,$17)
                       ON CONFLICT (booking_key) DO NOTHING""",
                    _key(msg["id"], index), msg["id"], msg["account"],
                    (msg["subject"] or "")[:200], b["kind"], b["confirmation"],
                    b["flight_number"], b["origin"], b["destination"],
                    b["origin_country"], b["destination_country"], b["start_date"],
                    b["start_time"], b["end_date"], b["end_time"], b["location"],
                    b["checkin_hours"])
                await _remember(b)
            result.found += len(bookings)
            await _mark(msg, "found" if bookings else "none")
    return result


async def _mark(msg: dict, outcome: str) -> None:
    from ...kernel.state import execute_sql
    await execute_sql(
        """INSERT INTO mail_extractions (agent, message_id, account, outcome)
           VALUES ('travel', $1, $2, $3)
           ON CONFLICT (agent, message_id) DO NOTHING""",
        msg["id"], msg["account"], outcome)


async def _remember(b: dict) -> None:
    """One line in the private `travel` memory namespace (the inbox agent's
    scope, which runs on the local model)."""
    from ...kernel.memory import Kind, remember
    try:
        await remember(Kind.EPISODIC, "travel", f"Booking: {describe(b)}",
                       meta={"kind": b["kind"]}, importance=0.6)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not remember a booking: %s", type(exc).__name__)


async def _run_sweep() -> None:
    try:
        result = await sweep()
    except Exception as exc:                            # noqa: BLE001
        from .._http import redact
        log.warning("travel sweep failed: %s", redact(exc))
        _state.update(at=datetime.now(UTC), error=redact(exc)[:200])
        return
    _state.update(at=result.at, error=None, result=result)


def maybe_sweep(now: datetime | None = None) -> bool:
    """Start a sweep in the background if none is running and the last one
    started more than SWEEP_EVERY ago. Returns whether one was started."""
    global _task
    from ...kernel import background

    now = now or datetime.now(UTC)
    if _task is not None and not _task.done():
        return False
    started = _state.get("started")
    if started is not None and now - started < SWEEP_EVERY:
        return False
    _state["started"] = now
    _task = background.spawn(_run_sweep(), "mail travel sweep")
    return True


# ------------------------------------------------------------------ trips

@dataclass
class Trip:
    start: date
    end: date
    bookings: list[dict]

    @property
    def destination(self) -> str:
        for kind, key in (("flight", "destination"), ("hotel", "location"),
                          ("transfer", "destination")):
            for b in self.bookings:
                if b["kind"] == kind and b.get(key):
                    return str(b[key])
        return "trip"

    @property
    def key(self) -> str:
        return f"{self.start:%Y-%m-%d} {self.destination}"

    @property
    def title(self) -> str:
        return f"Trip: {self.destination}, {_span(self.start, self.end)}"


def _span(a: date, b: date) -> str:
    return (f"{a:%a %-d %b %Y}" if a == b
            else f"{a:%a %-d %b} to {b:%a %-d %b %Y}")


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return g.parse_date(value) if isinstance(value, str) else None


def _clean(rows: list[dict]) -> list[dict]:
    """Rows as plain dicts with real dates, and one copy of a booking that
    arrived in more than one email (a confirmation and its reminder)."""
    out: dict[tuple, dict] = {}
    for row in rows:
        b = dict(row)
        b["start_date"], b["end_date"] = _as_date(b.get("start_date")), _as_date(
            b.get("end_date"))
        ident = (b["kind"], b.get("confirmation"), b.get("flight_number"),
                 b["start_date"], b.get("location"))
        if b.get("confirmation") is None and b.get("flight_number") is None:
            ident += (b.get("booking_key"),)
        out[ident] = b
    return list(out.values())


def trips(rows: list[dict]) -> tuple[list[Trip], list[dict]]:
    """Bookings grouped into trips by date: a booking starting more than
    TRIP_GAP after the current trip's last day starts a new one. Bookings
    with no readable date are returned apart, never placed by guesswork."""
    bookings = _clean(rows)
    dated = sorted((b for b in bookings if b["start_date"] is not None),
                   key=lambda b: (b["start_date"], b.get("start_time") or ""))
    undated = [b for b in bookings if b["start_date"] is None]
    out: list[Trip] = []
    for b in dated:
        last = max(b["start_date"], b["end_date"] or b["start_date"])
        if out and b["start_date"] <= out[-1].end + TRIP_GAP:
            out[-1].bookings.append(b)
            out[-1].end = max(out[-1].end, last)
        else:
            out.append(Trip(b["start_date"], last, [b]))
    return out, undated


# -------------------------------------------------------------- check-in

def checkin_due(b: dict) -> tuple[str, str] | None:
    """(due, why) for a flight: when online check-in opens, as wall-clock
    time at the departure airport ('YYYY-MM-DD HH:MM', or the day alone when
    the departure time is unknown). None if it is not a dated flight."""
    if b.get("kind") != "flight" or b.get("start_date") is None:
        return None
    if b.get("checkin_hours"):
        hours, why = int(b["checkin_hours"]), "as the booking email states"
    elif b.get("origin_country") and b.get("destination_country"):
        domestic = g.norm(b["origin_country"]) == g.norm(b["destination_country"])
        hours = DOMESTIC_H if domestic else INTERNATIONAL_H
        why = "domestic flight" if domestic else "international flight"
    else:
        hours = INTERNATIONAL_H
        why = ("the email does not say whether it is domestic or international, "
               "so 48h, the earlier of the two")
    start = b["start_date"]
    if b.get("start_time"):
        hh, mm = (int(x) for x in b["start_time"].split(":"))
        opens = datetime.combine(start, time(hh, mm)) - timedelta(hours=hours)
        return f"{opens:%Y-%m-%d %H:%M}", f"{hours}h before departure: {why}"
    day = start - timedelta(days=math.ceil(hours / 24))
    why = (f"{hours}h before departure: {why}; the departure time is not in "
           f"the email, so the day only")
    return f"{day:%Y-%m-%d}", why


def _one_line(text: str) -> str:
    return " ".join("".join(ch for ch in text if ch.isprintable()).split())


def _route(b: dict) -> str:
    return f"{(b.get('origin') or '?')[:25]}→{(b.get('destination') or '?')[:25]}"


def reminder_title(b: dict, due: str) -> str:
    """Composed here, from grounded fields only, one line. Short enough that
    the card's one visible sentence (`approvals._headline`, 140 characters)
    still carries the whole of it, due time included."""
    title = (f"Check-in opens: {(b.get('flight_number') or 'flight')[:8]} "
             f"{_route(b)}, {due} local time")
    return _one_line(title)[:110]


# -------------------------------------------------------------- passport

def passport_expiry() -> tuple[date | None, str]:
    """(expiry, what the note says about it). Never a default."""
    raw = os.getenv("PASSPORT_EXPIRY", "").strip()
    if not raw:
        return None, ("Passport expiry not given: set PASSPORT_EXPIRY=YYYY-MM-DD "
                      "to have trips checked against it.")
    d = g.parse_date(raw)
    if d is None:
        return None, ("PASSPORT_EXPIRY is not a date in YYYY-MM-DD form, so no "
                      "trip was checked against it.")
    return d, f"Passport expires {d:%-d %b %Y}."


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError(d)


def passport_problem(trip: Trip, expiry: date | None) -> str | None:
    """A sentence when the trip ends less than six months before expiry."""
    if expiry is None:
        return None
    if trip.end > expiry:
        return (f"your passport expires {expiry:%-d %b %Y}, before this trip "
                f"ends ({trip.end:%-d %b %Y})")
    if add_months(trip.end, 6) > expiry:
        return (f"your passport expires {expiry:%-d %b %Y}, less than six months "
                f"after this trip ends ({trip.end:%-d %b %Y}); many countries "
                f"refuse entry on that")
    return None


# ------------------------------------------------------------------ notes

def describe(b: dict) -> str:
    ref = f" · ref {b['confirmation']}" if b.get("confirmation") else " · ref unknown"
    when = (f"{b['start_date']:%a %-d %b %Y}" if b.get("start_date")
            else "date unknown")
    at = f" {b['start_time']}" if b.get("start_time") else " (time unknown)"
    if b["kind"] == "flight":
        arrive = ""
        if b.get("end_time"):
            arrive = f", arrives {b['end_time']}"
            if b.get("end_date") and b["end_date"] != b.get("start_date"):
                arrive += f" {b['end_date']:%a %-d %b}"
        return (f"{when}{at} — {b.get('flight_number') or 'flight'} "
                f"{b.get('origin') or '?'} → {b.get('destination') or '?'}"
                f"{arrive}{ref}")
    if b["kind"] == "hotel":
        until = (f" to {b['end_date']:%a %-d %b}" if b.get("end_date")
                 else " (check-out unknown)")
        return f"{when}{until} — {b.get('location') or 'hotel'}{ref}"
    return (f"{when}{at} — transfer {b.get('location') or b.get('origin') or '?'}"
            f" → {b.get('destination') or '?'}{ref}")


def render_trip(trip: Trip) -> str:
    expiry, passport_note = passport_expiry()
    lines = [f"Dates: {_span(trip.start, trip.end)}", ""]
    problem = passport_problem(trip, expiry)
    lines.append(f"**⚑ Passport:** {problem}." if problem else passport_note)
    for kind, heading in (("flight", "Flights"), ("hotel", "Hotels"),
                          ("transfer", "Transfers")):
        these = [b for b in trip.bookings if b["kind"] == kind]
        if these:
            lines += ["", f"## {heading}", *(f"- {describe(b)}" for b in these)]
    checkins = [(b, checkin_due(b)) for b in trip.bookings]
    checkins = [(b, c) for b, c in checkins if c is not None]
    if checkins:
        lines += ["", "## Online check-in"]
        for b, (due, why) in checkins:
            lines.append(f"- {b.get('flight_number') or 'flight'}: opens {due} "
                         f"({b.get('origin') or 'departure'} time; {why})")
    lines += ["", "Read from booking emails by the local model; anything not "
                  "written in an email is shown as unknown. Sources: "
              + "; ".join(sorted({f"{b.get('subject') or '(no subject)'} "
                                  f"({b.get('account')})" for b in trip.bookings}))]
    return "\n".join(lines)


def render_undated(bookings: list[dict]) -> str:
    return "\n".join(
        [("These bookings were found, but no date could be read from their "
          "emails, so they are not placed in any trip."), "",
         *(f"- {describe(b)} ({b.get('subject') or '(no subject)'})"
           for b in bookings)])


# --------------------------------------------------------------- signals

async def signals(now: datetime | None = None) -> list[Signal]:
    """Check-in reminder proposals and passport flags, from what earlier
    sweeps stored. Each is raised once ever per flight or trip, claimed in
    Postgres (`kernel.occurrence`), so an approved reminder is not offered
    again the next day."""
    from ...kernel import occurrence
    from ...kernel.state import fetch_all

    now = now or datetime.now(UTC)
    today = now.astimezone().date()
    trip_list, _undated = trips(await fetch_all(BOOKINGS_SQL))
    out: list[Signal] = []
    for trip in trip_list:
        if trip.end < today:
            continue
        for b in trip.bookings:
            due_why = checkin_due(b)
            if due_why is None:
                continue
            due, why = due_why
            due_day = g.parse_date(due[:10])
            if due_day is None or not today <= due_day <= today + REMIND_AHEAD:
                continue
            if not await occurrence.claim("mail.travel_checkin", b["booking_key"]):
                continue
            await occurrence.done("mail.travel_checkin", b["booking_key"])
            title = reminder_title(b, due)
            where = b.get("origin") or "the departure airport"
            out.append(Signal(
                # Unique per flight: the signals table drops a repeated title
                # for 24h, and this flight's claim is already spent.
                title=_one_line(f"Check-in reminder: {b.get('flight_number') or 'flight'}"
                                f" {_route(b)} {b['start_date']:%a %-d %b} "
                                f"[{b['booking_key'][:6]}]"),
                # The card shows the first sentence only: it is the exact
                # reminder, due time and time zone included.
                body=(f'Remind you "{title}" on your Mac? {due} is {where} '
                      f"local time. Online check-in opens {why}. "
                      f"Flight: {describe(b)}."),
                severity=Severity.NORMAL,
                suggests="mac.add_reminder",
                params={"title": title, "due": due}))
        expiry, _note = passport_expiry()
        problem = passport_problem(trip, expiry)
        if problem and await occurrence.claim("mail.travel_passport", trip.key):
            await occurrence.done("mail.travel_passport", trip.key)
            out.append(Signal(
                title=f"Passport: {trip.destination}, {trip.start:%-d %b %Y}",
                body=f"{trip.title}: {problem}.",
                severity=Severity.HIGH if trip.end > (expiry or trip.end)
                else Severity.NORMAL))
    return out


def brief_line() -> str:
    """What the last sweep could not do. Nothing when it went normally."""
    at = _state.get("at")
    if at is None:
        return ""
    if _state.get("error"):
        return f"travel: bookings not read ({_state['error']}, {at:%H:%M} UTC)"
    result: SweepResult = _state["result"]
    if result.model_down:
        return (f"travel: bookings not read, local model down ({at:%H:%M} UTC)")
    if result.unreadable:
        return (f"travel: {len(result.unreadable)} mailbox(es) not read "
                f"({at:%H:%M} UTC)")
    return ""
