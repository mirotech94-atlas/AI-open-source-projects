"""Travel organiser (`atlas/modules/mail/travel.py`).

What matters: the model runs LOCAL and reads the email wrapped as untrusted;
nothing it says is kept unless the email says it too; trips group by date;
check-in reminders are proposed through the existing `mac.add_reminder` with
parameters that action accepts, and never made directly; passport expiry is
never assumed; and no action is added.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from atlas.kernel import occurrence
from atlas.kernel.llm import Completion, LocalModelUnavailable, Need
from atlas.modules import mac as mac_module
from atlas.modules import mail
from atlas.modules.mail import grounding, travel
from atlas.modules.mail.accounts import Account

EMAIL = """Your booking is confirmed.
Booking reference: QX7P2L
Flight ME 201 Beirut (BEY) to London Heathrow (LHR)
Departs Mon 12 Oct 2026 at 08:40, arrives 12:15.
Lebanon to United Kingdom.
Online check-in opens 30 hours before departure."""

MSG = {"id": "gmail:abc", "account": "me@example.com", "subject": "Booking confirmed",
       "sender": "MEA <noreply@mea.com.lb>", "at": datetime(2026, 9, 1, tzinfo=UTC),
       "body": EMAIL}

GOOD = {"kind": "flight", "confirmation": "QX7P2L", "flight_number": "ME 201",
        "origin": "Beirut", "destination": "London Heathrow",
        "origin_country": "Lebanon", "destination_country": "United Kingdom",
        "start_date": "2026-10-12", "start_time": "08:40", "end_date": "2026-10-12",
        "end_time": "12:15", "location": None, "checkin_opens_hours": 30}


# ---------------------------------------------------------------- grounding

def test_a_booking_the_email_bears_out_is_kept_whole():
    b = travel.validate(GOOD, EMAIL)
    assert b["confirmation"] == "QX7P2L"
    assert b["flight_number"] == "ME 201"
    assert b["start_date"] == date(2026, 10, 12)
    assert b["start_time"] == "08:40" and b["end_time"] == "12:15"
    assert b["checkin_hours"] == 30


def test_nothing_is_kept_on_the_models_word():
    """A stub model that hallucinates: every invented value becomes None."""
    invented = dict(GOOD, confirmation="ZZ9ZZZ", flight_number="BA 117",
                    origin="Paris", start_date="2026-10-13", start_time="09:10",
                    origin_country="France", checkin_opens_hours=24)
    b = travel.validate(invented, EMAIL)
    assert b["confirmation"] is None
    assert b["flight_number"] is None
    assert b["origin"] is None
    assert b["origin_country"] is None
    assert b["start_date"] is None
    assert b["start_time"] is None
    assert b["checkin_hours"] is None


@pytest.mark.parametrize("raw", [{"kind": "cruise"}, {"kind": None}, "flight", None])
def test_not_a_booking_is_dropped(raw):
    assert travel.validate(raw, EMAIL) is None


@pytest.mark.parametrize(("d", "text"), [
    (date(2026, 10, 12), "Departure 12 October 2026"),
    (date(2026, 10, 12), "on Oct 12th"),
    (date(2026, 10, 2), "on 02/10/2026"),
    (date(2026, 10, 12), "2026-10-12T08:40"),
    (date(2026, 10, 12), "le 12 octobre"),
])
def test_dates_are_found_in_their_usual_spellings(d, text):
    assert grounding.date_in_text(d, text)


def test_a_date_that_is_not_written_is_not_found():
    assert not grounding.date_in_text(date(2026, 10, 13), EMAIL)
    assert grounding.time_in_text("20:40", "departs 8:40 PM")
    assert not grounding.time_in_text("20:40", "departs 8:40 AM")


# ----------------------------------------------------------------- extract

async def test_extract_runs_on_the_local_model_with_the_email_wrapped(monkeypatch):
    seen = []

    async def think(need, system, messages, **kw):
        seen.append((need, messages[0].content))
        return Completion('{"bookings": [' + __import__("json").dumps(GOOD) + "]}",
                          "ollama", "local")
    monkeypatch.setattr("atlas.kernel.llm.think", think)

    bookings = await travel.extract(MSG)

    assert [b["confirmation"] for b in bookings] == ["QX7P2L"]
    (need, content), = seen
    assert need is Need.PRIVATE
    assert content.startswith("<untrusted_data")
    assert "QX7P2L" in content


async def test_a_reply_that_is_not_json_is_none_not_a_guess(monkeypatch):
    async def think(need, system, messages, **kw):
        return Completion("I think it is a flight to London", "ollama", "local")
    monkeypatch.setattr("atlas.kernel.llm.think", think)
    assert await travel.extract(MSG) is None


async def test_private_routing_never_reaches_the_cloud(local_model_down):
    with pytest.raises(LocalModelUnavailable):
        await travel.extract(MSG)
    assert local_model_down == []


# ------------------------------------------------------------------- sweep

class _DB:
    def __init__(self, seen=()):
        self.seen = list(seen)
        self.sql: list[tuple[str, tuple]] = []

    async def fetch_all(self, sql, *args):
        assert "mail_extractions" in sql
        return [{"message_id": m} for m in self.seen]

    async def execute_sql(self, sql, *args):
        self.sql.append((sql, args))


@pytest.fixture
def db(monkeypatch):
    fake = _DB()
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fake.fetch_all)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", fake.execute_sql)

    async def remember(*a, **k):
        return 1
    monkeypatch.setattr("atlas.kernel.memory.remember", remember)
    return fake


ME = Account(name="myron", kind="google", email="me@example.com", refresh_token="t")


async def test_sweep_stores_grounded_bookings_and_marks_the_email(monkeypatch, db):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")

    async def find(account, **kw):
        return [MSG]
    monkeypatch.setattr("atlas.modules.mail.search.find", find)

    async def extract(msg):
        return [travel.validate(GOOD, EMAIL)]
    monkeypatch.setattr(travel, "extract", extract)

    result = await travel.sweep([ME])

    assert result.found == 1
    inserts = [a for s, a in db.sql if "INSERT INTO travel_bookings" in s]
    assert len(inserts) == 1 and "QX7P2L" in inserts[0]
    marks = [a for s, a in db.sql if "INSERT INTO mail_extractions" in s]
    assert marks == [("gmail:abc", "me@example.com", "found")]


async def test_local_model_down_stops_the_sweep_and_marks_nothing(monkeypatch, db):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")

    async def find(account, **kw):
        return [MSG]
    monkeypatch.setattr("atlas.modules.mail.search.find", find)

    async def extract(msg):
        raise LocalModelUnavailable("no local model configured")
    monkeypatch.setattr(travel, "extract", extract)

    result = await travel.sweep([ME])

    assert result.model_down
    assert db.sql == []          # read again on the next sweep


async def test_an_email_already_read_is_not_read_again(monkeypatch):
    fake = _DB(seen=["gmail:abc"])
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fake.fetch_all)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", fake.execute_sql)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")

    async def find(account, **kw):
        return [MSG]
    monkeypatch.setattr("atlas.modules.mail.search.find", find)

    async def extract(msg):
        raise AssertionError("already read")
    monkeypatch.setattr(travel, "extract", extract)
    assert (await travel.sweep([ME])).read == 0


# ------------------------------------------------------------------- trips

def _row(key, kind, start, end=None, **kw):
    return {"booking_key": key, "kind": kind, "start_date": start, "end_date": end,
            "start_time": kw.get("start_time"), "confirmation": kw.get("ref"),
            "flight_number": kw.get("flight"), "origin": kw.get("origin"),
            "destination": kw.get("dest"), "origin_country": kw.get("oc"),
            "destination_country": kw.get("dc"), "location": kw.get("loc"),
            "checkin_hours": kw.get("hours"), "subject": "s", "account": "a",
            "end_time": None}


def test_bookings_group_into_trips_by_date():
    rows = [
        _row("1", "flight", date(2026, 10, 12), flight="ME 201", dest="London"),
        _row("2", "hotel", date(2026, 10, 12), date(2026, 10, 16), loc="Hotel A"),
        _row("3", "flight", date(2026, 10, 16), flight="ME 202", dest="Beirut"),
        _row("4", "flight", date(2026, 12, 20), flight="ME 211", dest="Paris"),
        _row("5", "transfer", None, loc="Airport pickup"),
    ]
    trips, undated = travel.trips(rows)
    assert [(t.start, t.end, len(t.bookings)) for t in trips] == [
        (date(2026, 10, 12), date(2026, 10, 16), 3),
        (date(2026, 12, 20), date(2026, 12, 20), 1)]
    assert trips[0].destination == "London"
    assert [b["booking_key"] for b in undated] == ["5"]
    note = travel.render_trip(trips[0])
    assert "ME 201" in note and "Hotel A" in note
    assert "ref unknown" in note


# ---------------------------------------------------------------- check-in

@pytest.mark.parametrize(("kw", "due", "word"), [
    ({"hours": 30}, "2026-10-11 02:40", "states"),
    ({"oc": "Lebanon", "dc": "Lebanon"}, "2026-10-11 08:40", "domestic"),
    ({"oc": "Lebanon", "dc": "United Kingdom"}, "2026-10-10 08:40", "international"),
    ({}, "2026-10-10 08:40", "does not say"),
])
def test_checkin_opens_24h_domestic_48h_international_or_as_stated(kw, due, word):
    b = _row("1", "flight", date(2026, 10, 12), start_time="08:40", **kw)
    got, why = travel.checkin_due(b)
    assert got == due
    assert word in why


def test_checkin_without_a_departure_time_is_the_day_only():
    b = _row("1", "flight", date(2026, 10, 12), oc="X", dc="X")
    due, why = travel.checkin_due(b)
    assert due == "2026-10-11"
    assert "day only" in why


@pytest.fixture
def claims(monkeypatch):
    taken: set = set()

    async def claim(job, window):
        if (job, window) in taken:
            return False
        taken.add((job, window))
        return True

    async def done(job, window):
        return None
    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "done", done)
    return taken


def _stored(monkeypatch, rows):
    async def fetch_all(sql, *args):
        assert "FROM travel_bookings" in sql
        return rows
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)


async def test_checkin_reminder_is_proposed_once_as_mac_add_reminder(monkeypatch,
                                                                     claims):
    monkeypatch.delenv("PASSPORT_EXPIRY", raising=False)
    _stored(monkeypatch, [_row("k1", "flight", date(2026, 10, 12), start_time="08:40",
                               flight="ME 201", origin="Beirut", dest="London",
                               oc="Lebanon", dc="United Kingdom")])
    now = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)

    first = await travel.signals(now)
    again = await travel.signals(now)

    (sig,) = first
    assert again == []
    assert sig.suggests == "mac.add_reminder"
    assert set(sig.params) == {"title", "due"}
    assert sig.params["due"] == "2026-10-10 08:40"
    assert "ME 201" in sig.params["title"]
    assert sig.params["title"].endswith("2026-10-10 08:40 local time")
    assert "Beirut local time" in sig.body
    # The params are exactly what the existing action accepts.
    assert mac_module._reminder_problem(sig.params["title"], sig.params["due"]) is None
    action = next(a for a in mac_module.MODULE.actions()
                  if a.name == "mac.add_reminder")
    assert set(sig.params) <= set(action.params)
    # The card's one visible sentence is the exact reminder.
    assert sig.body.split("?")[0].endswith(f'"{sig.params["title"]}" on your Mac')


async def test_no_reminder_for_a_flight_weeks_away_or_past(monkeypatch, claims):
    monkeypatch.delenv("PASSPORT_EXPIRY", raising=False)
    _stored(monkeypatch, [
        _row("far", "flight", date(2026, 12, 20), start_time="08:40"),
        _row("past", "flight", date(2026, 9, 1), start_time="08:40"),
    ])
    assert await travel.signals(datetime(2026, 10, 8, 9, 0, tzinfo=UTC)) == []


# ---------------------------------------------------------------- passport

def _trip(end):
    return travel.Trip(date(2026, 10, 12), end, [])


def test_passport_is_never_assumed(monkeypatch):
    monkeypatch.delenv("PASSPORT_EXPIRY", raising=False)
    expiry, note = travel.passport_expiry()
    assert expiry is None and "not given" in note
    monkeypatch.setenv("PASSPORT_EXPIRY", "next spring")
    expiry, note = travel.passport_expiry()
    assert expiry is None and "not a date" in note
    assert travel.passport_problem(_trip(date(2026, 10, 16)), None) is None


def test_passport_six_month_boundary(monkeypatch):
    expiry = date(2027, 4, 16)
    assert travel.passport_problem(_trip(date(2026, 10, 16)), expiry) is None
    assert "less than six months" in travel.passport_problem(
        _trip(date(2026, 10, 17)), expiry)
    assert "before this trip ends" in travel.passport_problem(
        _trip(date(2027, 4, 17)), expiry)


async def test_passport_flag_is_a_signal_without_an_action(monkeypatch, claims):
    monkeypatch.setenv("PASSPORT_EXPIRY", "2027-01-01")
    _stored(monkeypatch, [_row("h", "hotel", date(2026, 11, 1), date(2026, 11, 5),
                               loc="Hotel A")])
    (sig,) = await travel.signals(datetime(2026, 10, 1, 9, tzinfo=UTC))
    assert sig.suggests is None and sig.params == {}
    assert "2027" in sig.body


# -------------------------------------------------------------- no actions

def test_the_mail_module_gains_no_action():
    assert sorted(a.name for a in mail.MODULE.actions()) == [
        "mail.apply_label_map", "mail.apply_labels", "mail.draft_nudge",
        "mail.draft_reply"]


def test_travel_imports_nothing_that_acts():
    import pathlib
    source = pathlib.Path(travel.__file__).read_text()
    for word in ("executor", "_dispatch", "propose(", "mac_jobs"):
        assert word not in source


# ------------------------------------------------------------ vault export

async def test_vault_export_writes_one_note_per_trip(monkeypatch):
    from atlas.api import vault_export

    today = datetime.now(UTC).date()
    rows = [_row("1", "flight", today, flight="ME 201", dest="London", ref="QX7P2L")]

    async def fetch_all(sql, *args):
        return rows if "FROM travel_bookings" in sql else []
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)

    docs: list[dict] = []
    line = await vault_export._trips(docs)
    assert line.startswith("- Trips")
    (doc,) = docs
    assert doc["path"].startswith("ATLAS/Trips/") and doc["path"].endswith(".md")
    assert "QX7P2L" in doc["content"]
    assert "data, not instructions" in doc["content"]


async def test_vault_export_survives_unreadable_trips(monkeypatch):
    from atlas.api import vault_export

    async def fetch_all(sql, *args):
        raise RuntimeError("relation does not exist")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    docs: list[dict] = []
    assert "could not be read" in await vault_export._trips(docs)
    assert docs == []


# ------------------------------------------------------------------ search

def test_search_reads_html_mail_as_text():
    from atlas.modules.mail import search
    got = search.plain("<html><body><p>Ref&nbsp;<b>QX7P2L</b></p>"
                       "<style>p{}</style><div>Flight ME 201</div></body></html>")
    assert "Ref QX7P2L" in got and "Flight ME 201" in got and "<" not in got


def test_imap_search_nests_its_ors():
    from atlas.modules.mail import search
    assert search._imap_criteria(("a", "b", "c"), "01-Sep-2026") == (
        '(SINCE 01-Sep-2026 OR SUBJECT "a" OR SUBJECT "b" SUBJECT "c")')


def test_search_is_read_only():
    import pathlib

    from atlas.modules.mail import search
    source = pathlib.Path(search.__file__).read_text()
    assert 'select("INBOX", readonly=True)' in source
    assert "BODY.PEEK[]" in source
    for write in ("STORE", "EXPUNGE", ".post(", ".delete(", "modify"):
        assert write not in source


def test_arrive_three_hours_before_is_not_when_checkin_opens():
    text = ("Booking reference QX7P2L. Please arrive at the airport 3 hours before "
            "departure. Check-in desks close 60 minutes before.")
    b = travel.validate(dict(GOOD, checkin_opens_hours=3), text)
    assert b["checkin_hours"] is None
    stated = text + " Online check-in opens 72 hours before your flight."
    assert travel.validate(dict(GOOD, checkin_opens_hours=72), stated)[
        "checkin_hours"] == 72


def test_the_cards_visible_sentence_holds_the_whole_reminder():
    from atlas.kernel.approvals import _headline

    long_place = "Aeroport International de Xa"          # 28 characters
    b = _row("k" * 32, "flight", date(2026, 10, 12), start_time="08:40",
             flight="ME 2011", origin=long_place + "x", dest=long_place + "y")
    due, _why = travel.checkin_due(b)
    title = travel.reminder_title(b, due)
    first = f'Remind you "{title}" on your Mac?'
    assert len(first) <= 140
    assert _headline(first + " More detail follows.") == first
    assert title.endswith(f"{due} local time")


async def test_two_legs_on_one_day_get_two_cards(monkeypatch, claims):
    monkeypatch.delenv("PASSPORT_EXPIRY", raising=False)
    _stored(monkeypatch, [
        _row("aaaaaa1", "flight", date(2026, 10, 12), start_time="08:40",
             origin="Beirut", dest="Istanbul"),
        _row("bbbbbb2", "flight", date(2026, 10, 12), start_time="14:00",
             origin="Istanbul", dest="London"),
    ])
    sigs = await travel.signals(datetime(2026, 10, 8, 9, 0, tzinfo=UTC))
    assert len(sigs) == 2
    assert len({s.title for s in sigs}) == 2
