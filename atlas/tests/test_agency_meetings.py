"""Meetings booked on a lead call (the owner, 2026-09-30): real free slots read
from his calendar, a Tier 1 hold, a Tier 2 invite card, and a reminder."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from atlas.kernel.contracts import ActionResult
from atlas.modules.agency import _store, consent, meetings

NY = ZoneInfo("America/New_York")
# Monday 2026-10-05, 08:00 in New York.
MONDAY_8 = datetime(2026, 10, 5, 8, 0, tzinfo=NY)
LEAD = {"id": 7, "name": "Joe's Plumbing", "category": "plumber"}


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AGENCY_MEETING_HOURS", "AGENCY_MEETING_TZ", "AGENCY_MEETING_MINUTES",
                 "AGENCY_SEND_FROM", "AGENCY_SMTP_USER",
                 "AGENCY_MEETING_NOTICE_MIN", "AGENCY_MEETING_DAYS"):
        monkeypatch.delenv(name, raising=False)


def test_slots_are_business_hours_new_york_half_hours():
    slots = meetings.open_slots([], MONDAY_8, days=7)
    assert slots[0] == datetime(2026, 10, 5, 10, 0, tzinfo=NY)
    assert all(s.astimezone(NY).weekday() < 5 for s in slots)
    assert all(10 <= s.astimezone(NY).hour < 17 for s in slots)
    assert all(s.astimezone(NY).minute in (0, 30) for s in slots)
    assert max(s.astimezone(NY) for s in slots).time().isoformat() == "16:30:00"


def test_a_busy_hour_is_never_offered():
    busy = [(datetime(2026, 10, 5, 10, 15, tzinfo=NY),
             datetime(2026, 10, 5, 11, 0, tzinfo=NY))]
    slots = meetings.open_slots(busy, MONDAY_8, days=1)
    assert datetime(2026, 10, 5, 10, 0, tzinfo=NY) not in slots
    assert datetime(2026, 10, 5, 10, 30, tzinfo=NY) not in slots
    assert datetime(2026, 10, 5, 11, 0, tzinfo=NY) in slots


def test_the_notice_period_and_configured_hours(monkeypatch):
    monkeypatch.setenv("AGENCY_MEETING_HOURS", "Tue-Thu 13:00-15:00")
    now = datetime(2026, 10, 6, 12, 30, tzinfo=NY)            # Tuesday
    slots = meetings.open_slots([], now, days=3)
    assert slots[0] == datetime(2026, 10, 6, 14, 30, tzinfo=NY)   # 2h notice
    assert {s.weekday() for s in slots} <= {1, 2, 3}


def test_free_events_do_not_block():
    events = [{"start": {"dateTime": "2026-10-05T10:00:00-04:00"},
               "end": {"dateTime": "2026-10-05T11:00:00-04:00"},
               "transparency": "transparent"},
              {"start": {"dateTime": "2026-10-05T12:00:00-04:00"},
               "end": {"dateTime": "2026-10-05T13:00:00-04:00"}}]
    busy = meetings.busy_from(events)
    assert len(busy) == 1 and busy[0][0].hour == 12


def test_choose_spreads_the_offer():
    slots = meetings.open_slots([], MONDAY_8, days=7)
    picked = meetings.choose(slots)
    assert len(picked) == 3 and len({p.date() for p in picked}) == 3
    assert [p.hour >= 12 for p in picked] == [False, True, False]


def test_spoken_times():
    assert meetings.spoken(datetime(2026, 10, 5, 14, 30, tzinfo=NY)) == (
        "Monday at 2:30 in the afternoon")


async def test_an_unreadable_calendar_offers_nothing(monkeypatch):
    async def down(**_kw):
        raise RuntimeError("401")
    monkeypatch.setattr("atlas.modules.calendar._upcoming", down)
    slots, why = await meetings.free_times()
    assert slots is None and "could not be read" in why


async def test_the_hold_refuses_a_taken_slot(monkeypatch):
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(meetings, "free_times", _returns((
        [datetime(2026, 10, 5, 11, 0, tzinfo=NY)], None)))
    result = await meetings.hold_meeting_dry(7, "2026-10-05T10:00:00-04:00")
    assert not result.ok and "not an open" in result.detail
    assert (await meetings.hold_meeting_dry(7, "2026-10-05T11:00:00-04:00")).ok
    assert not (await meetings.hold_meeting_dry(7, "2026-10-05T11:00:00")).ok  # naive


class _Http:
    def __init__(self):
        self.calls = []

    def __call__(self, *a, **kw):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _do(self, method, url, **kw):
        self.calls.append((method, url, kw))

        class R:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"id": "evt1"}
        return R()

    async def post(self, url, **kw):
        return await self._do("POST", url, **kw)

    async def patch(self, url, **kw):
        return await self._do("PATCH", url, **kw)

    async def delete(self, url, **kw):
        return await self._do("DELETE", url, **kw)


async def test_the_hold_invites_nobody(monkeypatch):
    http = _Http()
    monkeypatch.setattr(meetings.httpx, "AsyncClient", http)
    monkeypatch.setattr("atlas.modules.calendar._token", _returns("tok"))
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(_store, "lead_links", _returns({"domain": "joes.example"}))
    monkeypatch.setattr(_store, "lead_email", _returns("joe@example.com"))
    monkeypatch.setattr(_store, "insert_meeting", _returns(4))
    slot = datetime(2026, 10, 5, 11, 0, tzinfo=NY)
    monkeypatch.setattr(meetings, "free_times", _returns(([slot], None)))
    result = await meetings.hold_meeting(7, slot.isoformat(), "CA1")
    assert result.ok and result.rollback_token == "evt1"
    method, _url, kw = http.calls[0]
    assert method == "POST" and "attendees" not in kw["json"]
    assert "https://joes.example" in kw["json"]["description"]
    assert result.evidence["meeting_id"] == 4


def _meeting(**over):
    row = {"id": 4, "lead_id": 7, "event_id": "evt1", "status": "held",
           "email": "joe@example.com",
           "start_at": datetime.now(UTC) + timedelta(days=1)}
    row.update(over)
    return row


async def test_no_email_means_no_invite(monkeypatch):
    monkeypatch.setattr(_store, "get_meeting", _returns(_meeting(email=None)))
    result = await meetings.send_invite_dry(4)
    assert not result.ok and "book this one yourself" in result.detail


async def test_the_invite_carries_the_agenda(monkeypatch):
    http = _Http()
    monkeypatch.setattr(meetings.httpx, "AsyncClient", http)
    monkeypatch.setattr("atlas.modules.calendar._token", _returns("tok"))
    monkeypatch.setattr(_store, "get_meeting", _returns(_meeting()))
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(_store, "is_suppressed", _returns(False))
    monkeypatch.setattr(_store, "lead_links", _returns({"domain": "joes.example"}))
    monkeypatch.setattr(_store, "mark_meeting_invited", _returns(True))
    from atlas.modules.agency import verify
    monkeypatch.setattr(verify, "contact_refusal", _returns(None))
    result = await meetings.send_invite(4)
    assert result.ok
    method, _url, kw = http.calls[0]
    assert method == "PATCH" and kw["params"] == {"sendUpdates": "all"}
    assert kw["json"]["attendees"] == [{"email": "joe@example.com"}]
    assert "https://joes.example" in kw["json"]["description"]
    assert "What we'll cover" in kw["json"]["description"]
    assert "Questions before the call: owner@example.com" in kw["json"]["description"]


async def test_reminders_go_to_slack_once(monkeypatch):
    sent = []

    async def notify(msg):
        sent.append(msg)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    monkeypatch.setattr(_store, "claim_reminders", _returns([
        {**_meeting(), "lead_name": "Joe's Plumbing"}]))
    assert await meetings.remind() == 1
    assert "In an hour" in sent[0].title and sent[0].topic == "agency"


def test_the_actions_are_tiered():
    from atlas.kernel.contracts import Tier
    from atlas.kernel.permissions import classify
    from atlas.modules import agency
    acts = {a.name: a for a in agency.MODULE.actions()}
    assert classify(acts["agency.hold_meeting"]).tier is Tier.AUTO
    assert acts["agency.hold_meeting"].rollback is meetings.release_hold
    assert not acts["agency.hold_meeting"].card_only
    # Six agents, 2026-10-27: the invite is a card (Tier 2); its rollback
    # deletes the event, which tells the attendee.
    invite = acts["agency.send_invite"]
    assert classify(invite).tier is Tier.ASK and invite.card_only
    assert not invite.irreversible
    assert invite.rollback is meetings.release_hold


# ------------------------------------------------------------ on the call

def _call(monkeypatch):
    from atlas.api import lead_call
    call = lead_call.LeadCall(5, dict(LEAD), "+17185550101",
                              consent.disclosure("Joe's Plumbing", calling=True), "x")
    return lead_call, call


async def test_offering_reads_real_slots(monkeypatch):
    lead_call, call = _call(monkeypatch)
    slots = meetings.open_slots([], MONDAY_8, days=7)
    monkeypatch.setattr(meetings, "free_times", _returns((slots, None)))
    line = await lead_call._offer(call)
    assert "Monday at 10 in the morning" in line and len(call.offered) == 3


async def test_an_unreadable_calendar_is_said_not_invented(monkeypatch):
    lead_call, call = _call(monkeypatch)
    monkeypatch.setattr(meetings, "free_times", _returns((None, "401")))
    assert await lead_call._offer(call) == lead_call.NO_TIMES
    assert call.offered == []


def _preview(monkeypatch, answer: ActionResult) -> list:
    seen: list = []

    async def preview(action, params):
        seen.append((action, params))
        return answer
    monkeypatch.setattr("atlas.kernel.executor.preview", preview)
    return seen


async def test_picking_holds_then_raises_the_invite_card(monkeypatch):
    """The hold is Tier 1 off the turn; the invite is a card (six agents,
    2026-10-27), dry-run first, never run by the call."""
    lead_call, call = _call(monkeypatch)
    call.offered = meetings.choose(meetings.open_slots([], MONDAY_8, days=7))
    ran, proposed = [], []

    async def run_tier1(action, params, reason):
        ran.append((action, params, None))
        assert action == "agency.hold_meeting", "the invite is never run"
        return ActionResult(True, "held", evidence={"meeting_id": 4,
                                                    "email": "joe@example.com"})
    monkeypatch.setattr("atlas.kernel.unattended.run_tier1", run_tier1)
    seen = _preview(monkeypatch, ActionResult(True, "would invite joe@example.com"))

    async def propose(action, params, reason, **kw):
        proposed.append((action, params, reason, kw))
        return 12
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    line = await lead_call._book(call, "CA1", 2)
    assert ran[0][0] == "agency.hold_meeting" and ran[0][2] is None
    assert "invite" in line and call.meeting
    import asyncio
    for _ in range(3):
        await asyncio.sleep(0)
    assert len(ran) == 1 and seen == [("agency.send_invite", {"meeting_id": 4})]
    [(action, params, reason, kw)] = proposed
    assert action == "agency.send_invite" and params == {"meeting_id": 4}
    assert not reason.startswith("Held for you") and kw["dedup_key"] == "lead-call:CA1:invite"
    assert call.cards == ["#12 agency.send_invite"]
    assert any("waits for your approval" in n for n in call.notes)


async def test_a_refused_invite_dry_run_is_held_for_you_on_the_card(monkeypatch):
    lead_call, call = _call(monkeypatch)
    call.offered = meetings.choose(meetings.open_slots([], MONDAY_8, days=7))
    proposed = []
    monkeypatch.setattr("atlas.kernel.unattended.run_tier1", _returns(
        ActionResult(True, "held", evidence={"meeting_id": 4,
                                             "email": "joe@example.com"})))
    _preview(monkeypatch, ActionResult(False, "dry run refused: joe@example.com is on "
                                              "the suppression list"))

    async def propose(action, params, reason, **kw):
        proposed.append((action, params, reason))
        return 12
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    await lead_call._book(call, "CA1", 2)
    import asyncio
    for _ in range(3):
        await asyncio.sleep(0)
    [(action, params, reason)] = proposed
    assert action == "agency.send_invite" and params == {"meeting_id": 4}
    assert reason.startswith("Held for you: joe@example.com is on the suppression")
    assert call.cards == ["#12 agency.send_invite"]


async def test_no_email_books_the_hold_and_says_so(monkeypatch):
    lead_call, call = _call(monkeypatch)
    call.offered = meetings.choose(meetings.open_slots([], MONDAY_8, days=7))
    monkeypatch.setattr("atlas.kernel.unattended.run_tier1", _returns(
        ActionResult(True, "held", evidence={"meeting_id": 4, "email": None})))
    proposed = []
    monkeypatch.setattr("atlas.kernel.approvals.propose",
                        lambda *a, **k: proposed.append(a))
    await lead_call._book(call, "CA1", 1)
    assert proposed == [] and any("no email" in n for n in call.notes)


async def test_a_choice_outside_the_offer_holds_nothing(monkeypatch):
    lead_call, call = _call(monkeypatch)
    call.offered = meetings.choose(meetings.open_slots([], MONDAY_8, days=7))
    monkeypatch.setattr("atlas.kernel.unattended.run_tier1",
                        lambda *a: (_ for _ in ()).throw(AssertionError("ran")))
    line = await lead_call._book(call, "CA1", 7)
    assert line.startswith("Sorry, which one?")


async def test_texting_the_link_is_off(monkeypatch):
    lead_call, call = _call(monkeypatch)
    monkeypatch.setattr(_store, "lead_returning", _returns(False))
    monkeypatch.setattr(_store, "lead_links", _returns({}))
    from atlas.kernel import llm
    from atlas.kernel.llm import Completion
    monkeypatch.setattr(llm, "think", _returns(Completion(
        '{"move": "send_link", "say": "sure"}', "t", "m")))
    proposed = []
    monkeypatch.setattr("atlas.kernel.approvals.propose",
                        lambda *a, **k: proposed.append(a))
    text, _ = await lead_call.reply(call, "CA1", "can you text me the link")
    assert text == lead_call.LINK_BY_EMAIL and proposed == []


async def test_a_slow_turn_answers_within_twilios_budget(monkeypatch):
    import asyncio
    lead_call, call = _call(monkeypatch)
    monkeypatch.setattr(lead_call, "TURN_BUDGET_S", 0.05)
    finished = []

    async def slow(c, sid, said):
        await asyncio.sleep(0.2)
        finished.append(True)
        return "late", False
    monkeypatch.setattr(lead_call, "reply", slow)
    text, done = await lead_call.reply_in_time(call, "CA1", "hello")
    assert text == lead_call.HOLD and not done
    await asyncio.sleep(0.3)
    assert finished == [True]            # shielded: the work still completes
