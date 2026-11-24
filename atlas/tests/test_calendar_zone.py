"""RA-E2 / plan D11: every calendar write and read-back uses the calendar's
own timezone, across daylight-saving transitions, and a duplicate request
gives no second event.

The transition dates are computed from zoneinfo, never hard-coded, so the
tests keep meaning what they say in any year."""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult
from atlas.modules import calendar
from atlas.modules.calendar import booking, zone

NY = "America/New_York"
TZ = ZoneInfo(NY)


def _transition(month: int, year: int = 2027) -> date:
    """The day in `month` whose UTC offset changes in New York."""
    day = date(year, month, 1)
    while day.month == month:
        noon_before = datetime(day.year, day.month, day.day, 0, 0, tzinfo=TZ)
        noon_after = datetime(day.year, day.month, day.day, 23, 0, tzinfo=TZ)
        if noon_before.utcoffset() != noon_after.utcoffset():
            return day
        day += timedelta(days=1)
    raise AssertionError(f"no transition in month {month}")


SPRING = _transition(3)     # 02:00 -> 03:00
FALL = _transition(11)      # 02:00 -> 01:00


class Google:
    """A fake Calendar API: GETs answered by path regex, writes recorded."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, int, object]] = []
        self.writes: list[tuple[str, str, dict]] = []
        self.gets: list[httpx.Request] = []
        self.on(r"/calendars/primary$", body={"timeZone": NY})
        self.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": []})

    def on(self, pattern: str, status: int = 200, body=None) -> None:
        self.routes.insert(0, (pattern, status, body))

    async def send(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method != "GET":
            payload = json.loads(request.content or b"{}")
            self.writes.append((request.method, path, payload))
            return httpx.Response(200, json={"id": "new1", **payload}, request=request)
        self.gets.append(request)
        for pattern, status, body in self.routes:
            if re.search(pattern, path):
                return httpx.Response(status, json=body, request=request)
        return httpx.Response(404, json={"error": "no route"}, request=request)


@pytest.fixture
def google(monkeypatch):
    fake = Google()

    async def send(client, request, **_kw):
        return await fake.send(request)

    async def tok():
        return "test-token"

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setattr(calendar, "_token", tok)
    monkeypatch.setattr(booking, "_mail_token", tok)
    return fake


def _iso(d: date, hh: int, mm: int = 0) -> str:
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00"


# --------------------------------------------------------------- resolution

def test_the_transition_dates_are_the_real_ones():
    assert SPRING.month == 3 and SPRING.weekday() == 6      # second Sunday
    assert FALL.month == 11 and FALL.weekday() == 6         # first Sunday


def test_a_time_in_the_spring_forward_gap_does_not_exist():
    with pytest.raises(zone.NoSuchTime, match="does not exist"):
        zone.resolve(_iso(SPRING, 2, 30), 30, NY)


def test_a_block_across_spring_forward_lasts_its_real_minutes():
    slot = zone.resolve(_iso(SPRING, 1, 30), 60, NY)
    assert slot.start.isoformat().endswith("-05:00")
    # one real hour after 01:30 EST is 03:30 EDT, not 02:30
    assert slot.end.isoformat() == f"{SPRING.isoformat()}T03:30:00-04:00"
    assert slot.end.astimezone(UTC) - slot.start.astimezone(UTC) == timedelta(hours=1)


def test_the_repeated_fall_back_hour_uses_the_first_and_says_so():
    slot = zone.resolve(_iso(FALL, 1, 30), 60, NY)
    assert slot.start.isoformat().endswith("-04:00")        # EDT, the first 01:30
    assert "happens twice" in slot.note and "first" in slot.note
    # an hour later is the SECOND 01:30, in EST
    assert slot.end.isoformat() == f"{FALL.isoformat()}T01:30:00-05:00"
    # an offset in the input picks the other occurrence
    second = zone.resolve(f"{_iso(FALL, 1, 30)}-05:00", 30, NY)
    assert second.start.isoformat().endswith("-05:00") and not second.naive


def test_no_zone_is_a_refusal_not_utc():
    with pytest.raises(zone.ZoneError):
        zone.resolve("2026-10-01T09:00:00", 30, None)


# ------------------------------------------------------------------ writes

async def test_a_write_names_the_calendars_zone_explicitly(google):
    done = await calendar.create_block("Focus", "2026-10-01T09:00:00", 60)
    assert done.ok and done.rollback_token == "new1"
    [(method, _, body)] = google.writes
    assert method == "POST"
    assert body["start"] == {"dateTime": "2026-10-01T09:00:00-04:00", "timeZone": NY}
    assert body["end"] == {"dateTime": "2026-10-01T10:00:00-04:00", "timeZone": NY}


async def test_spring_forward_gap_is_refused_by_the_dry_run_and_the_run(google):
    dry = await calendar.create_dry("Focus", _iso(SPRING, 2, 30), 30)
    assert not dry.ok and "does not exist" in dry.detail
    run = await calendar.create_block("Focus", _iso(SPRING, 2, 30), 30)
    assert not run.ok and google.writes == []
    booked = await booking.book_with("ana@example.com", "Call", _iso(SPRING, 2, 30))
    assert not booked.ok and google.writes == []


async def test_fall_back_booking_writes_the_first_occurrence_and_the_real_end(google):
    done = await booking.book_with("ana@example.com", "Call", _iso(FALL, 1, 30), 60)
    assert done.ok and done.evidence["occurrence"].startswith("01:30 happens twice")
    [(_, _, body)] = google.writes
    assert body["start"]["dateTime"].endswith("T01:30:00-04:00")
    assert body["end"]["dateTime"].endswith("T01:30:00-05:00")
    assert body["start"]["timeZone"] == body["end"]["timeZone"] == NY


async def test_an_unreadable_zone_writes_nothing(google):
    google.on(r"/calendars/primary$", 503, {"error": "busy"})
    done = await calendar.create_block("Focus", "2026-10-01T09:00:00", 60)
    assert not done.ok and "timezone" in done.detail and google.writes == []
    dry = await booking.book_dry("a@b.com", "Call", "2026-10-01T09:00:00")
    assert not dry.ok


# -------------------------------------------------------------- duplicates

def _existing(start: str, end: str, *, attendees=(), summary="Call", status="confirmed"):
    me = {"email": "owner@example.com", "self": True, "organizer": True}
    return {"id": "old7", "status": status, "summary": summary,
            "start": {"dateTime": start}, "end": {"dateTime": end},
            "attendees": [me, *({"email": a} for a in attendees)]}


async def test_a_duplicate_booking_gives_no_second_event(google):
    # the same instant, written by Google in UTC rather than local
    google.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": [
        _existing("2026-10-01T13:00:00Z", "2026-10-01T13:30:00Z",
                  attendees=["Ana@Example.com"], summary="Intro call")]})
    done = await booking.book_with("ana@example.com", "Call", "2026-10-01T09:00:00")
    assert done.ok and done.evidence["duplicate"] is True
    assert done.evidence["event_id"] == "old7"
    assert done.rollback_token is None, "a rollback must not delete his event"
    assert google.writes == []
    [search] = [r for r in google.gets if r.url.path.endswith("/events")]
    assert search.url.params["timeZone"] == NY


async def test_a_duplicate_in_the_repeated_hour_is_still_caught(google):
    """PEP 495: inter-zone == is always False in the fold; compared in UTC."""
    google.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": [
        _existing(f"{FALL.isoformat()}T01:30:00-04:00",
                  f"{FALL.isoformat()}T01:00:00-05:00", attendees=["ana@example.com"])]})
    done = await booking.book_with("ana@example.com", "Call", _iso(FALL, 1, 30), 30)
    assert done.ok and done.evidence.get("duplicate") and google.writes == []


@pytest.mark.parametrize("other", [
    _existing("2026-10-01T09:00:00-04:00", "2026-10-01T10:00:00-04:00",
              attendees=["ana@example.com"]),                   # a different end
    _existing("2026-10-01T09:00:00-04:00", "2026-10-01T09:30:00-04:00",
              attendees=["bob@example.com"]),                   # another attendee
    _existing("2026-10-01T09:00:00-04:00", "2026-10-01T09:30:00-04:00",
              attendees=["ana@example.com", "bob@example.com"]),  # a wider scope
    _existing("2026-10-01T09:00:00-04:00", "2026-10-01T09:30:00-04:00",
              attendees=["ana@example.com"], status="cancelled"),
])
async def test_a_near_duplicate_is_not_deduplicated(google, other):
    google.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": [other]})
    done = await booking.book_with("ana@example.com", "Call", "2026-10-01T09:00:00")
    assert done.ok and done.rollback_token == "new1" and len(google.writes) == 1


async def test_a_block_asked_for_twice_is_one_block(google):
    google.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": [
        _existing("2026-10-01T09:00:00-04:00", "2026-10-01T10:00:00-04:00",
                  summary="Focus")]})
    done = await calendar.create_block("Focus", "2026-10-01T09:00:00", 60)
    assert done.ok and done.evidence["duplicate"] and google.writes == []
    # an unrelated personal event in the same slot is not "the same block"
    other = await calendar.create_block("Deep work", "2026-10-01T09:00:00", 60)
    assert other.ok and other.rollback_token == "new1"


async def test_a_search_that_fails_writes_nothing(google):
    google.on(r"/calendars/primary/events$", 500, {"error": "boom"})
    with pytest.raises(httpx.HTTPStatusError):
        await booking.book_with("ana@example.com", "Call", "2026-10-01T09:00:00")
    assert google.writes == []


# -------------------------------------------------------------- read-back

def _event(start: str, end: str, **over):
    return {"id": "e1", "status": "confirmed", "summary": "Focus",
            "start": {"dateTime": start, "timeZone": NY},
            "end": {"dateTime": end, "timeZone": NY}, **over}


async def test_read_back_on_a_dst_day_agrees_with_the_write(google):
    params = {"summary": "Focus", "start": _iso(SPRING, 1, 30), "minutes": 60}
    google.on(r"/events/e1$", body=_event(f"{SPRING.isoformat()}T01:30:00-05:00",
                                          f"{SPRING.isoformat()}T03:30:00-04:00"))
    got = await v.verify("calendar.create_block", params,
                         ActionResult(True, rollback_token="e1"))
    assert got.state is v.State.VERIFIED, got.reason


async def test_read_back_of_the_second_fall_back_occurrence_is_contradicted(google):
    """The write used the first 01:30; an event at the second is not it."""
    params = {"summary": "Focus", "start": _iso(FALL, 1, 30), "minutes": 30}
    google.on(r"/events/e1$", body=_event(f"{FALL.isoformat()}T01:30:00-05:00",
                                          f"{FALL.isoformat()}T02:00:00-05:00"))
    got = await v.verify("calendar.create_block", params,
                         ActionResult(True, rollback_token="e1"))
    assert got.state is v.State.CONTRADICTED


async def test_a_conflicting_edit_is_contradicted_naming_both_times(google):
    """Someone moved the block after ATLAS wrote it: not 'completed'."""
    params = {"summary": "Focus", "start": "2026-10-01T09:00:00", "minutes": 60}
    google.on(r"/events/e1$", body=_event("2026-10-01T11:00:00-04:00",
                                          "2026-10-01T12:00:00-04:00"))
    got = await v.verify("calendar.create_block", params,
                         ActionResult(True, rollback_token="e1"))
    assert got.state is v.State.CONTRADICTED
    assert "2026-10-01T11:00:00-04:00" in got.reason
    assert "2026-10-01T09:00:00-04:00" in got.reason


async def test_read_back_of_a_duplicate_reads_the_existing_event(google):
    params = {"summary": "Focus", "start": "2026-10-01T09:00:00", "minutes": 60}
    google.on(r"/events/old7$", body={**_event("2026-10-01T09:00:00-04:00",
                                               "2026-10-01T10:00:00-04:00"),
                                      "id": "old7"})
    result = ActionResult(True, "already blocked",
                          evidence={"event_id": "old7", "duplicate": True})
    got = await v.verify("calendar.create_block", params, result)
    assert got.state is v.State.VERIFIED and got.evidence["event_id"] == "old7"


async def test_read_back_without_a_zone_is_unknown_not_utc(google):
    params = {"summary": "Focus", "start": "2026-10-01T09:00:00", "minutes": 60}
    google.on(r"/calendars/primary$", 503, {"error": "busy"})
    google.on(r"/events/e1$", body={**_event("2026-10-01T09:00:00Z",
                                             "2026-10-01T10:00:00Z"),
                                    "start": {"dateTime": "2026-10-01T09:00:00Z"},
                                    "end": {"dateTime": "2026-10-01T10:00:00Z"}})
    got = await v.verify("calendar.create_block", params,
                         ActionResult(True, rollback_token="e1"))
    assert got.state is v.State.UNKNOWN


# ------------------------------------------------------------------- reads

async def test_an_all_day_date_starts_at_midnight_in_the_calendars_zone(google):
    google.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": [
        {"id": "a", "summary": "Exam day", "start": {"date": "2026-10-02"},
         "end": {"date": "2026-10-03"}}]})
    [ev] = await calendar._upcoming(7)
    start = calendar._start(ev)
    assert start.isoformat() == "2026-10-02T00:00:00-04:00"


async def test_a_second_page_of_events_is_a_failed_read_not_a_free_week(google):
    """events.list answers 100 at most. A week cut at a page would draw the
    rest as free on the home page's diary, so the read fails instead."""
    google.on(r"/calendars/primary/events$", body={
        "timeZone": NY, "nextPageToken": "p2",
        "items": [{"id": "a", "summary": "One", "start": {"date": "2026-10-02"},
                   "end": {"date": "2026-10-03"}}]})
    with pytest.raises(RuntimeError, match="not paged"):
        await calendar._upcoming(8)


async def test_deadline_days_are_the_calendars_days(google):
    """21:00 in New York on the 1st is 01:00 UTC on the 2nd; it collides with
    the 1st's exam, not with anything on the 2nd."""
    google.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": [
        {"id": "a", "summary": "Exam", "start": {"dateTime": "2026-10-01T10:00:00-04:00"},
         "end": {"dateTime": "2026-10-01T11:00:00-04:00"}},
        {"id": "b", "summary": "Client launch",
         "start": {"dateTime": "2026-10-02T01:00:00Z"},
         "end": {"dateTime": "2026-10-02T02:00:00Z"}}]})
    obs = await calendar.check_deadline_collisions()
    assert obs.evidence["days"] and obs.evidence["days"][0].startswith("2026-10-01")
