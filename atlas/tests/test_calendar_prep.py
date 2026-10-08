"""Calendar 13.2: prep card, focus guard, booking.

Three properties matter more than the rest of the code:

* `Block` / `parse_blocks` are a contract with the screening module (a later
  task imports them directly from `atlas.modules.calendar.guard`), so the
  grammar is pinned down here in the exact shape the brief specifies.
* Every new action -- offer_slots, book_with, decline_invite -- touches
  someone outside the household (an email, an invite, a decline) and must
  come out of `classify` as Tier ASK, not just be declared that way in code.
* `check_focus_invites` with no `FOCUS_BLOCKS` set is UNKNOWN, not OK: there
  is nothing to guard with, and reporting health anyway hides that fact.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, time, timedelta

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.llm import Completion
from atlas.kernel.permissions import classify
from atlas.modules import calendar
from atlas.modules.calendar import booking, guard, prep

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "calendar"


# ------------------------------------------------------- no executor path

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "calendar"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


# ------------------------------------------------------------- parse_blocks

def test_parse_blocks_parses_two_blocks():
    blocks = guard.parse_blocks(
        "Mon-Fri 08:00-11:00 school and deep work; Sat 09:00-12:00 family")
    assert len(blocks) == 2

    weekdays, saturday = blocks
    assert weekdays.days == frozenset({0, 1, 2, 3, 4})
    assert weekdays.start == time(8, 0)
    assert weekdays.end == time(11, 0)
    assert weekdays.label == "school and deep work"

    assert saturday.days == frozenset({5})
    assert saturday.start == time(9, 0)
    assert saturday.end == time(12, 0)
    assert saturday.label == "family"


# ------------------------------------------------------------- inside_block

def test_inside_block_checks_the_day():
    """Naive datetimes here are HOST-LOCAL wall clock -- the same convention
    `Block` uses -- so this is portable across whatever timezone the test
    runs in (`astimezone()` treats a naive input as already local)."""
    blocks = guard.parse_blocks("Mon-Fri 08:00-11:00 work")
    monday_9am = datetime(2026, 9, 21, 9, 0)     # a Monday, local  # noqa: DTZ001
    saturday_9am = datetime(2026, 9, 26, 9, 0)   # a Saturday, local  # noqa: DTZ001
    assert guard.inside_block(monday_9am, blocks) is blocks[0]
    assert guard.inside_block(saturday_9am, blocks) is None


def test_inside_block_start_is_inclusive_end_is_exclusive():
    blocks = guard.parse_blocks("Mon-Fri 08:00-11:00 work")

    def monday(hour, minute=0):
        return datetime(2026, 9, 21, hour, minute)   # local wall clock  # noqa: DTZ001

    assert guard.inside_block(monday(8, 0), blocks) is blocks[0]     # start
    assert guard.inside_block(monday(10, 59), blocks) is blocks[0]
    assert guard.inside_block(monday(11, 0), blocks) is None         # end
    assert guard.inside_block(monday(7, 59), blocks) is None


def test_inside_block_uses_the_hosts_local_zone_not_utc():
    """Regression: `_parse_time` used to attach `tzinfo=UTC`, which told a
    reader FOCUS_BLOCKS is UTC when it is host-local wall clock. An event at
    13:00Z lands at 09:00 local in America/New_York (EDT, UTC-4 in July) --
    inside a 'Mon-Fri 08:00-11:00' block and outside a '12:00-14:00' one."""
    import os
    import time as time_mod

    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time_mod.tzset()
    try:
        blocks = guard.parse_blocks(
            "Mon-Fri 08:00-11:00 work; Mon-Fri 12:00-14:00 lunch")
        moment = datetime(2026, 7, 6, 13, 0, tzinfo=UTC)   # Monday, 09:00 EDT
        block = guard.inside_block(moment, blocks)
        assert block is not None
        assert block.label == "work"
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time_mod.tzset()


# ------------------------------------------------------------- alternatives

def test_alternatives_never_lands_inside_a_block():
    blocks = guard.parse_blocks("Mon-Fri 08:00-11:00 work")
    start = datetime(2026, 9, 21, 8, 30)  # local, inside the block  # noqa: DTZ001
    slots = guard.alternatives(start, blocks, 30)
    assert len(slots) == 3
    for slot in slots:
        assert guard.inside_block(slot, blocks) is None
        assert guard.inside_block(slot + timedelta(minutes=29), blocks) is None


def test_overlaps_any_catches_a_block_fully_inside_the_candidate():
    """Regression: `booking._clear` used to point-sample only `start` and
    `end - 1 minute` through `inside_block`, which is blind to a block that
    sits entirely inside a longer candidate slot. `guard.overlaps_any` does
    true interval overlap instead."""
    blocks = guard.parse_blocks("Mon-Fri 08:15-08:30 short block")
    monday_8am = datetime(2026, 9, 21, 8, 0)     # a Monday, local  # noqa: DTZ001
    assert guard.overlaps_any(monday_8am, monday_8am + timedelta(hours=1), blocks)


# ------------------------------------------------------------- free_slots

def test_free_slots_respects_busy_and_blocks():
    blocks = guard.parse_blocks("Mon-Fri 08:00-11:00 work")
    now = datetime.now(UTC)
    busy_start = now + timedelta(hours=2)
    busy = [(busy_start, busy_start + timedelta(minutes=30))]

    slots = booking.free_slots(busy, blocks, days=3, minutes=30,
                               work_start=time(9, 0), work_end=time(17, 0))
    assert slots
    for slot in slots:
        end = slot + timedelta(minutes=30)
        assert not (slot < busy[0][1] and end > busy[0][0]), "overlaps busy"
        assert guard.inside_block(slot, blocks) is None
        assert guard.inside_block(end - timedelta(minutes=1), blocks) is None


def test_clear_rejects_a_block_fully_inside_the_candidate_slot():
    """Reproduces the reported bug directly against `booking._clear`: a
    focus block shorter than the meeting and entirely inside the candidate
    slot must make the slot unavailable, not merely the two point samples
    the old implementation checked."""
    blocks = guard.parse_blocks("Mon-Fri 08:15-08:30 short block")
    slot_start = datetime(2026, 9, 21, 8, 0)     # a Monday, local  # noqa: DTZ001
    slot_end = slot_start + timedelta(hours=1)   # 08:00-09:00 contains the block
    assert booking._clear(slot_start, slot_end, [], blocks) is False


def test_clear_rejects_a_busy_interval_fully_inside_the_candidate_slot():
    slot_start = datetime(2026, 9, 21, 8, 0)  # noqa: DTZ001
    slot_end = slot_start + timedelta(hours=1)
    busy_start = slot_start + timedelta(minutes=15)
    busy = [(busy_start, busy_start + timedelta(minutes=10))]   # inside the slot
    assert booking._clear(slot_start, slot_end, busy, []) is False


def test_free_slots_work_hours_are_host_local_not_utc(monkeypatch):
    """Regression: `free_slots` used to anchor the day/work-hour bounds to
    `busy[0][0].tzinfo`, defaulting to UTC when `busy` was empty. On a host
    whose local zone is not UTC, an empty calendar made "09:00-17:00" come
    back as UTC 09:00-17:00 -- 05:00-13:00 local in America/New_York, not
    the 9-to-5 asked for.

    `datetime.now` is frozen (to a fixed Monday, 07:00 local, before work
    hours) rather than left to the real wall clock, so the test is
    deterministic regardless of what time of day it happens to run.
    """
    import os
    import time as time_mod

    real_datetime = datetime

    class _FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            base = real_datetime(2026, 7, 6, 7, 0)   # a Monday, before 09:00
            return base if tz is None else base.astimezone(tz)

    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time_mod.tzset()
    try:
        monkeypatch.setattr(booking, "datetime", _FrozenDatetime)
        slots = booking.free_slots([], [], days=1, minutes=30,
                                   work_start=time(9, 0), work_end=time(17, 0))
        assert slots
        for slot in slots:
            local_hour = slot.astimezone().hour
            assert 9 <= local_hour <= 16, f"{slot} is not host-local 9-to-5"
        assert slots[0].astimezone().hour == 9
        assert slots[0].astimezone().minute == 0
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time_mod.tzset()


# ------------------------------------------------------------- prep_card

async def test_prep_card_names_the_attendee_and_the_empty_history(monkeypatch):
    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        return []

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="They probably want a status update.",
                          provider="test", model="test")

    monkeypatch.setattr(prep, "recall", fake_recall)
    monkeypatch.setattr(prep, "think", fake_think)

    event = {"summary": "Sync with Sam", "attendees": [
        {"email": "sam@example.com", "displayName": "Sam Rivera"},
        {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
    ]}
    card = await prep.prep_card(event)
    assert "Sam Rivera" in card
    assert "no prior interactions on record" in card


async def test_prep_card_surfaces_recalled_history(monkeypatch):
    from atlas.kernel.memory import Kind, Memory

    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        if namespaces == ["commitments"]:
            return [Memory(id=2, kind=Kind.SEMANTIC, namespace="commitments",
                           content="Sam owes us the signed SOW")]
        return [Memory(id=1, kind=Kind.EPISODIC, namespace="threads",
                       content="Sam asked about pricing last month")]

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="Bring the updated SOW.", provider="test", model="test")

    monkeypatch.setattr(prep, "recall", fake_recall)
    monkeypatch.setattr(prep, "think", fake_think)

    event = {"summary": "Sync with Sam",
            "attendees": [{"email": "sam@example.com", "displayName": "Sam Rivera"}]}
    card = await prep.prep_card(event)
    assert "Sam asked about pricing" in card
    assert "Sam owes us the signed SOW" in card
    assert "Bring the updated SOW" in card


async def test_upcoming_within_filters_by_window(monkeypatch):
    now = datetime.now(UTC)
    events = [
        {"id": "too-soon",
         "start": {"dateTime": (now + timedelta(minutes=5)).isoformat()}},
        {"id": "in-window",
         "start": {"dateTime": (now + timedelta(minutes=30)).isoformat()}},
        {"id": "too-late",
         "start": {"dateTime": (now + timedelta(hours=5)).isoformat()}},
    ]

    async def fake_upcoming(days=7):
        return events

    monkeypatch.setattr(calendar, "_upcoming", fake_upcoming)
    result = await prep.upcoming_within(25, 35)
    assert [e["id"] for e in result] == ["in-window"]


# --------------------------------------------------- tiers, per `classify`

@pytest.mark.parametrize("name", [
    "calendar.offer_slots", "calendar.book_with", "calendar.decline_invite",
])
def test_new_actions_are_tier_ask_and_classify_agrees(name):
    action, = [a for a in calendar.MODULE.actions() if a.name == name]
    assert action.tier is Tier.ASK
    decision = classify(action)
    assert decision.allowed
    assert decision.tier is Tier.ASK


def test_offer_slots_is_irreversible():
    action, = [a for a in calendar.MODULE.actions() if a.name == "calendar.offer_slots"]
    assert action.irreversible is True


def test_decline_invite_is_irreversible():
    """The decline notice reaches the organizer immediately and cannot be
    recalled, same as offer_slots -- it should not read as an oversight."""
    action, = [a for a in calendar.MODULE.actions()
              if a.name == "calendar.decline_invite"]
    assert action.irreversible is True


def test_book_with_has_a_cancel_rollback():
    action, = [a for a in calendar.MODULE.actions() if a.name == "calendar.book_with"]
    assert action.rollback is booking.cancel_booking


async def test_signals_do_not_duplicate_the_focus_check(monkeypatch):
    """`check_focus_invites` already reports a pending invite at
    Severity.NORMAL on its own interval. A signal for the same finding on
    every SIGNAL_INTERVAL_S tick is the double notification that gets a
    check muted (see test_linkedin.test_runway_does_not_also_raise_a_signal)."""
    from atlas.kernel.contracts import Health, Observation

    async def fake_upcoming_within(lo, hi):
        return []

    async def fake_check_deadline_collisions():
        return Observation("deadline_collisions", Health.OK, evidence={"days": []})

    monkeypatch.setattr(calendar, "upcoming_within", fake_upcoming_within)
    monkeypatch.setattr(calendar, "check_deadline_collisions",
                        fake_check_deadline_collisions)

    signals = await calendar.MODULE.signals()
    assert all("focus" not in s.title.lower() for s in signals)


# ------------------------------------------------------ check_focus_invites

async def test_check_focus_invites_is_unknown_without_focus_blocks(monkeypatch):
    monkeypatch.delenv("FOCUS_BLOCKS", raising=False)
    obs = await guard.check_focus_invites()
    assert obs.health is Health.UNKNOWN
    assert "FOCUS_BLOCKS" in obs.detail
    assert "nothing to guard with" in obs.detail


async def test_check_focus_invites_flags_a_pending_invite_inside_a_block(monkeypatch):
    monkeypatch.setenv("FOCUS_BLOCKS", "Mon-Fri 08:00-11:00 school and deep work")

    # Google always sends an explicit offset; build one for 09:00 HOST-LOCAL
    # (whatever zone the test happens to run in) so this is TZ-portable.
    monday_9am = datetime(2026, 9, 21, 9, 0).astimezone().isoformat()
    events = [
        {"id": "e1", "summary": "Client call",
         "start": {"dateTime": monday_9am},
         "attendees": [{"email": "me@example.com", "self": True,
                        "responseStatus": "needsAction"},
                       {"email": "client@example.com"}]},
        # already accepted: not still a decision to make
        {"id": "e2", "summary": "Standing sync",
         "start": {"dateTime": monday_9am},
         "attendees": [{"email": "me@example.com", "self": True,
                        "responseStatus": "accepted"},
                       {"email": "team@example.com"}]},
        # solo block, no outside attendee: nothing to decline
        {"id": "e3", "summary": "Deep work",
         "start": {"dateTime": monday_9am},
         "attendees": [{"email": "me@example.com", "self": True,
                        "responseStatus": "needsAction"}]},
    ]

    async def fake_upcoming(days=14):
        return events

    monkeypatch.setattr(calendar, "_upcoming", fake_upcoming)
    obs = await guard.check_focus_invites()
    assert obs.health is Health.DEGRADED
    ids = [e["id"] for e in obs.evidence["events"]]
    assert ids == ["e1"]


# -------------------------------------------------------------- dry runs

async def test_decline_dry_rejects_a_missing_event_id():
    result = await guard.decline_dry("", "tomorrow 2pm")
    assert result.ok is False


async def test_offer_slots_dry_rejects_empty_slots():
    result = await booking.offer_slots_dry("client@example.com", "Time to meet?", "  ")
    assert result.ok is False


async def test_book_dry_rejects_a_bad_datetime():
    result = await booking.book_dry("client@example.com", "Kickoff", "not-a-date")
    assert result.ok is False


async def test_book_dry_accepts_a_good_datetime(monkeypatch):
    from atlas.modules.calendar import zone

    async def ny():
        return "America/New_York"

    monkeypatch.setattr(zone, "calendar_zone", ny)
    result = await booking.book_dry("client@example.com", "Kickoff",
                                    "2026-10-01T09:00:00+00:00")
    assert result.ok is True


# ------------------------------------------------ reasoner-facing wrappers

async def test_free_slots_tool_wraps_the_pure_finder(monkeypatch):
    """`calendar_free_slots`'s read function: pulls busy time off the
    calendar and FOCUS_BLOCKS, returns ISO strings a tool call can carry."""
    monkeypatch.setenv("FOCUS_BLOCKS", "Mon-Fri 08:00-11:00 work")

    async def fake_upcoming(days=7):
        return []

    monkeypatch.setattr(calendar, "_upcoming", fake_upcoming)
    result = await booking.free_slots_tool(days=2, minutes=30)
    assert "slots" in result
    assert all(isinstance(s, str) for s in result["slots"])


async def test_free_slots_tool_reports_a_calendar_error(monkeypatch):
    async def fake_upcoming(days=7):
        raise RuntimeError("calendar unreachable")

    monkeypatch.setattr(calendar, "_upcoming", fake_upcoming)
    result = await booking.free_slots_tool()
    assert "error" in result


async def test_alternatives_tool_wraps_the_pure_finder(monkeypatch):
    monkeypatch.setenv("FOCUS_BLOCKS", "Mon-Fri 08:00-11:00 work")
    result = await guard.alternatives_tool("2026-09-21T08:30:00", 30)
    assert "slots" in result
    assert len(result["slots"]) == 3


async def test_alternatives_tool_rejects_a_bad_datetime():
    result = await guard.alternatives_tool("not-a-date")
    assert "error" in result


async def test_prep_card_says_the_local_model_is_down_and_keeps_the_facts(
        monkeypatch, local_model_down):
    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        return []

    monkeypatch.setattr(prep, "recall", fake_recall)
    event = {"summary": "Sync with Sam", "attendees": [
        {"email": "sam@example.com", "displayName": "Sam Rivera"}]}
    card = await prep.prep_card(event)
    assert "Sam Rivera" in card
    assert "no prior interactions on record" in card
    assert "local model unavailable: " in card
    assert "ConnectError" in card
    assert local_model_down == []


# ------------------------------------------- their company's latest news

def test_company_domain_skips_our_own_domain_and_free_mail():
    event = {"attendees": [
        {"email": "me@mine.example", "self": True},
        {"email": "pat@gmail.com"},
        {"email": "ann@mine.example"},
        {"email": "lee@acme.example"},
    ]}
    assert prep.company_domain(event) == "acme.example"


def test_no_company_domain_when_everyone_is_on_free_mail():
    event = {"attendees": [{"email": "me@x.example", "self": True},
                           {"email": "pat@outlook.com"}]}
    assert prep.company_domain(event) is None


async def test_prep_card_adds_the_companys_news_with_its_read_time(monkeypatch):
    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        return []

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="Status update.", provider="test", model="test")

    async def fake_news(domain):
        assert domain == "acme.example"
        return ("Acme opened a second office.",
                datetime(2026, 9, 28, 14, 5, tzinfo=UTC), None)

    monkeypatch.setattr(prep, "recall", fake_recall)
    monkeypatch.setattr(prep, "think", fake_think)
    monkeypatch.setattr(prep, "company_news", fake_news)
    event = {"summary": "Intro", "attendees": [
        {"email": "me@mine.example", "self": True},
        {"email": "lee@acme.example", "displayName": "Lee"}]}

    card = await prep.prep_card(event)
    assert "acme.example" in card
    assert "Acme opened a second office." in card
    assert "read 14:05 UTC" in card


async def test_prep_card_says_why_there_is_no_news(monkeypatch):
    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        return []

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="Status update.", provider="test", model="test")

    async def fake_news(domain):
        return None, None, "Perplexity is not configured"

    monkeypatch.setattr(prep, "recall", fake_recall)
    monkeypatch.setattr(prep, "think", fake_think)
    monkeypatch.setattr(prep, "company_news", fake_news)
    event = {"summary": "Intro", "attendees": [
        {"email": "me@mine.example", "self": True},
        {"email": "lee@acme.example"}]}

    card = await prep.prep_card(event)
    assert "no news read (Perplexity is not configured)" in card


async def test_company_news_is_off_when_switched_off(monkeypatch):
    monkeypatch.setenv("MEETING_NEWS", "off")
    text, at, why = await prep.company_news("acme.example")
    assert text is None and at is None
    assert why == "switched off (MEETING_NEWS=off)"
