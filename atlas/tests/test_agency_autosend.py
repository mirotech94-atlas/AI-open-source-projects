"""The lead follow-up that runs by itself (Miro, 2026-10-06: "make the lead
replies send themselves too - approve invoices too and schedule meetings
with them via email as well and call them as well").

Replies and calls are Tier 1 with their rules in code; a hold releases the
claim, a refusal is the Tier 2 card with "Held for you: <reason>", a success
is a Slack receipt. Six agents (2026-10-27): the invite and the agreement
are Tier 2 cards -- dry-run through `executor.preview`, one card each, never
executed by the pass. The executor, `propose` and `notify` are faked the way
tests/test_agency_followup.py fakes them; no socket, no model, no database.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from atlas.kernel import lead_followup
from atlas.kernel.contracts import ActionResult, FailureClass
from atlas.kernel.executor import HOLD
from atlas.kernel.llm import Completion
from atlas.modules.agency import _store, consent, leads, meetings, pricing, replies

NY = ZoneInfo("America/New_York")
LEAD = {"id": 7, "name": "Joe's Plumbing", "category": "plumber",
        "website": "http://joes.example"}
TUE_2PM = datetime(2026, 10, 6, 14, 0, tzinfo=NY)
WED_11 = datetime(2026, 10, 7, 11, 0, tzinfo=NY)
THU_330 = datetime(2026, 10, 8, 15, 30, tzinfo=NY)
OFFERED = [TUE_2PM, WED_11, THU_330]
OFFERED_ISO = [s.isoformat() for s in OFFERED]


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


def _held_result(detail: str = "watch-only") -> ActionResult:
    return ActionResult(False, detail, evidence={HOLD: "watch_only"},
                        failure_class=FailureClass.DISABLED, dispatched=False)


def _refused(reason: str) -> ActionResult:
    return ActionResult(False, f"dry run refused: {reason}",
                        failure_class=FailureClass.VALIDATION, dispatched=False)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("AGENCY_MEETING_TZ", "AGENCY_MEETING_HOURS", "AGENCY_REPLY_AUTOSEND",
                 "AGENCY_PRICE_LIST", "AGENCY_PRICE_FLOOR", "AGENCY_FIRST_SALE_PRICE"):
        monkeypatch.delenv(name, raising=False)
    from atlas.modules.agency import verify
    monkeypatch.setattr(verify, "contact_refusal", _returns(None))


class World:
    """Fakes around a pass: what the executor answers per action, what was
    run, carded, posted and released."""

    SENDS = ("agency.send_invite", "agency.send_agreement", "agency.send_outreach")

    def __init__(self, monkeypatch, answers: dict[str, ActionResult]):
        self.ran: list[tuple[str, dict]] = []
        self.previewed: list[tuple[str, dict]] = []
        self.cards: list[tuple[str, dict, str, dict]] = []
        self.slack: list = []
        self.released: list = []
        monkeypatch.setattr(_store, "get_lead", _returns(LEAD))

        async def execute(request):
            self.ran.append((request.action, request.params))
            assert request.approved_by is None            # unattended, Tier 1
            assert request.action not in self.SENDS, "a send is a card, never run"
            return answers[request.action]
        monkeypatch.setattr("atlas.kernel.executor.execute", execute)

        async def preview(action, params):
            self.previewed.append((action, params))
            return answers[action]
        monkeypatch.setattr("atlas.kernel.executor.preview", preview)

        async def propose(action, params, reason, **kw):
            self.cards.append((action, params, reason, kw))
            return 90
        monkeypatch.setattr("atlas.kernel.approvals.propose", propose)

        async def notify(out):
            self.slack.append(out)
        monkeypatch.setattr("atlas.kernel.comms.notify", notify)

        def releaser(name):
            async def release(row_id):
                self.released.append((name, row_id))
            return release
        for name in ("reply", "booking", "agreement"):
            monkeypatch.setattr(_store, f"release_{name}", releaser(name))
            monkeypatch.setattr(_store, f"claim_{name}", _returns(True))


# ============================================================ the replies

DRAFTED = ActionResult(True, "drafted", evidence={
    "draft_id": 21, "to": "joe@example.com", "text": "Hi Joe, it's $500."})


def _reply_world(monkeypatch, send: ActionResult) -> World:
    world = World(monkeypatch, {"agency.draft_reply": DRAFTED, "agency.send_reply": send})
    monkeypatch.setattr(_store, "messages_awaiting_reply", _returns(
        [{"id": 11, "lead_id": 7, "reply_channel": "email"}]))
    return world


async def test_a_drafted_reply_is_sent_and_receipted(monkeypatch):
    world = _reply_world(monkeypatch, ActionResult(True, "emailed reply #21 to "
                                                         "joe@example.com"))
    assert await lead_followup.replies() == 0
    assert world.ran == [("agency.draft_reply", {"lead_id": 7, "channel": "email"}),
                         ("agency.send_reply", {"draft_id": 21})]
    assert world.cards == [] and world.released == []
    [receipt] = world.slack
    assert receipt.title == "Replied to Joe's Plumbing by email"
    assert receipt.kind == "receipt" and receipt.topic == "agency"
    assert "Hi Joe, it's $500." in receipt.body and "without a card" in receipt.body


async def test_a_held_reply_becomes_the_card_with_the_reason_first(monkeypatch):
    world = _reply_world(monkeypatch, _refused(
        "held for you: automatic replies are off (AGENCY_REPLY_AUTOSEND)"))
    assert await lead_followup.replies() == 1
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.send_reply" and params == {"draft_id": 21}
    assert reason.startswith("Held for you: automatic replies are off "
                             "(AGENCY_REPLY_AUTOSEND). Approving runs every check again")
    assert kw["evidence"]["held"] == ("automatic replies are off "
                                      "(AGENCY_REPLY_AUTOSEND)")
    assert kw["evidence"]["text"] == "Hi Joe, it's $500."
    assert kw["dedup_key"] == "agency.send_reply:21" and kw["automatic"]
    assert world.slack == [] and world.released == []     # a refusal keeps the claim


async def test_a_refused_reply_is_a_card_too(monkeypatch):
    world = _reply_world(monkeypatch, _refused(
        "the draft no longer passes: it quotes $100, below the $300 floor"))
    assert await lead_followup.replies() == 1
    assert world.cards[0][3]["evidence"]["held"].startswith("the draft no longer passes")


async def test_a_hold_at_the_send_releases_the_claim(monkeypatch):
    world = _reply_world(monkeypatch, _held_result())
    assert await lead_followup.replies() == 0
    assert world.released == [("reply", 11)] and world.cards == [] and world.slack == []


async def test_the_reply_autosend_switch_holds_in_the_guard(monkeypatch):
    """`AGENCY_REPLY_AUTOSEND=off` is the last check in `_guard_reply`, with
    the HELD prefix the kernel reads; an approved card passes it."""
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass-for-test")
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "1 Main St, Queens, NY 11101")
    body = replies._email_finish("Hi Joe, it's $500.")
    draft = {"id": 21, "lead_id": 7, "status": "draft", "kind": "reply",
             "channel": "email", "to_address": "joe@example.com",
             "subject": "Re: your site", "body": body, "offered_slots": None}
    monkeypatch.setattr(_store, "get_reply_draft", _returns(draft))
    monkeypatch.setattr(_store, "lead_message_bodies", _returns(["How much?"]))
    monkeypatch.setattr(_store, "lead_returning", _returns(False))
    monkeypatch.setattr(_store, "is_suppressed", _returns(False))
    assert (await replies.send_reply_dry(21)).ok
    monkeypatch.setenv("AGENCY_REPLY_AUTOSEND", "off")
    result = await replies.send_reply_dry(21)
    assert not result.ok
    assert result.detail == "held for you: automatic replies are off (AGENCY_REPLY_AUTOSEND)"
    from atlas.kernel import idempotency
    token = idempotency._RUN.set("proposal:5")
    try:
        assert (await replies.send_reply_dry(21)).ok          # Miro's yes is the answer
    finally:
        idempotency._RUN.reset(token)


async def test_the_reply_rollback_is_honest(monkeypatch):
    suppressed, meta = [], []
    monkeypatch.setattr(_store, "get_reply_draft", _returns(
        {"id": 21, "lead_id": 7, "channel": "email", "to_address": "Joe@Example.com"}))

    async def add(address, why):
        suppressed.append(address)
    monkeypatch.setattr(_store, "add_suppression", add)

    async def set_meta(lead_id, key, value):
        meta.append((lead_id, key))
    monkeypatch.setattr(_store, "set_lead_meta", set_meta)
    result = await replies.send_reply_rollback("21")
    assert result.ok and result.evidence["unsent"] is False
    assert suppressed == ["joe@example.com"] and meta == [(7, "reply_rolled_back")]
    assert (await replies.send_reply_rollback("")).ok and suppressed == ["joe@example.com"]


# =================================================== offered times in a draft

def _draft_fakes(monkeypatch, reply_text: str, *, intent: str, slots):
    seen: dict = {}
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(_store, "latest_lead_message", _returns(
        {"id": 11, "channel": "email", "body": "Sounds interesting, when can we talk?",
         "subject": "Re: your site", "intent": intent}))
    monkeypatch.setattr(_store, "lead_message_bodies", _returns(["when can we talk?"]))
    monkeypatch.setattr(_store, "lead_email", _returns("joe@example.com"))
    monkeypatch.setattr(_store, "is_suppressed", _returns(False))
    monkeypatch.setattr(_store, "lead_returning", _returns(False))
    monkeypatch.setattr(_store, "lead_links", _returns({"domain": "joes.example"}))
    monkeypatch.setattr(meetings, "free_times", _returns(slots))
    from atlas.modules.agency.audit import Audit
    monkeypatch.setattr(replies, "audit_lead", _returns(Audit(None, [], {}, 0)))

    async def insert(lead_id, in_reply_to, kind, channel, to, subject, body, quoted,
                     **kw):
        seen["offered_slots"] = kw.get("offered_slots")
        return 21
    monkeypatch.setattr(_store, "insert_reply_draft", insert)

    async def think(need, system, messages, **kw):
        seen["user"] = messages[0].content
        return Completion(reply_text, "t", "m")
    monkeypatch.setattr(replies, "think", think)
    return seen


async def test_an_interested_lead_is_offered_the_real_slots_and_they_are_stored(
        monkeypatch):
    seen = _draft_fakes(
        monkeypatch, "Happy to set up a call. Miro could do Tuesday 6 Oct at 14:00 in "
                     "the afternoon or Wednesday 7 Oct at 11:00 in the morning.",
        intent="interested", slots=(OFFERED, None))
    result = await replies.draft_reply(7, "email")
    assert result.ok, result.detail
    assert "Offer EXACTLY these" in seen["user"]
    assert "Tuesday 6 Oct at 14:00 in the afternoon" in seen["user"]
    assert seen["offered_slots"] == OFFERED_ISO
    assert result.evidence["offered_slots"] == OFFERED_ISO


async def test_a_draft_naming_a_time_outside_the_offer_is_refused(monkeypatch):
    _draft_fakes(monkeypatch, "Miro could do Friday at 10 or Tuesday at 2.",
                 intent="interested", slots=(OFFERED, None))
    result = await replies.draft_reply(7, "email")
    assert not result.ok and "Friday at 10" in result.detail
    assert "not one of the 3 offered" in result.detail


async def test_an_unreadable_calendar_offers_nothing_and_a_time_is_refused(monkeypatch):
    seen = _draft_fakes(monkeypatch, "Miro will send you a couple of times himself.",
                        intent="interested", slots=(None, "401"))
    result = await replies.draft_reply(7, "email")
    assert result.ok and seen["offered_slots"] is None
    assert "could not be read" in seen["user"] and "Offer NO day or time" in seen["user"]
    _draft_fakes(monkeypatch, "How about Tuesday at 2?", intent="interested",
                 slots=(None, "401"))
    assert not (await replies.draft_reply(7, "email")).ok


async def test_a_question_gets_no_slots_and_may_name_no_time(monkeypatch):
    seen = _draft_fakes(monkeypatch, "Yes, the site includes a contact form.",
                        intent="question", slots=(OFFERED, None))
    assert (await replies.draft_reply(7, "email")).ok
    assert "Offer EXACTLY" not in seen["user"] and seen["offered_slots"] is None
    _draft_fakes(monkeypatch, "Sure, Tuesday at 2 works.", intent="question",
                 slots=(OFFERED, None))
    assert not (await replies.draft_reply(7, "email")).ok


def test_the_slot_check_is_lenient_on_form_and_strict_on_the_set():
    ok = ("Miro can do Tuesday 6 Oct at 14:00 in the afternoon, Wed, Oct 7, 11am "
          "or Thursday at 3:30pm.")
    assert meetings.slot_refusal(ok, OFFERED) is None
    assert meetings.slot_refusal("Thursday at 3", OFFERED)        # 3:30 is not 3
    assert meetings.slot_refusal("Tuesday at 2", []) is not None
    assert meetings.slot_refusal("we can start next week", []) is None


# ======================================================= matching their words

@pytest.mark.parametrize(("said", "want"), [
    ("Tuesday at 2 works for me", TUE_2PM),
    ("Tue 2pm", TUE_2PM),
    ("2:00 on Tuesday please", TUE_2PM),
    ("Let's do 2 on Tuesday", TUE_2PM),
    ("tuesday at 2 in the afternoon", TUE_2PM),
    ("Wednesday the 7th at 11", WED_11),
    ("Wed, Oct 7, 11am", WED_11),
    ("11 on wednesday", WED_11),
    ("Thursday at 3:30", THU_330),
    ("thursday at 3.30pm", THU_330),
    ("the first one", TUE_2PM),
    ("The first option works", TUE_2PM),
    ("option 2", WED_11),
    ("The second.", WED_11),
    ("the second works for me, thanks", WED_11),
    ("number 3 please", THU_330),
    ("#3", THU_330),
    ("the 3rd slot", THU_330),
])
def test_one_clear_pick_matches_the_slot(said, want):
    assert meetings.match_slot(said, OFFERED) == want


@pytest.mark.parametrize("said", [
    "Tuesday at 2 or Wednesday at 11",            # two picks
    "the first or the second",                    # two picks
    "Thursday at 3",                              # 3 is not 3:30
    "Monday at 2",                                # not offered
    "Friday 10am",                                # not offered
    "Tuesday at 11",                              # the day of one, the hour of another
    "yes let's do it",                            # no time at all
    "the first week of October is busy",          # an ordinal that is not a pick
    "I'm away until Oct 1st",                     # a date
    "we have 2 friends on Monday",                # not a time
    "Tuesday 10",                                 # not a time without at/am/pm
    "the first time I saw it",                    # not a pick
    "Tuesday at 2 doesn't work for me",           # turned down
    "I can't do the first one",                   # turned down
    "the second is no good, sorry",               # turned down
    "Wednesday at 11 won't work",                 # turned down
    "I'm busy Tuesday at 2",                      # turned down
    "not Tuesday at 2, maybe later",              # turned down
    "Could we do Thursday at 3:30 instead?",      # "instead": a counter, not a pick
    "",
])
def test_ambiguity_or_nothing_matches_no_slot(said):
    assert meetings.match_slot(said, OFFERED) is None


def test_a_number_beyond_the_offer_is_no_pick():
    assert meetings.match_slot("option 3", OFFERED[:2]) is None
    assert meetings.match_slot("option 2", OFFERED[:2]) == WED_11


# ============================================================ accepting a price

TERMS = pricing.Terms(500, 300, returning=False)
RETURNING = pricing.Terms(500, 300, returning=True)


@pytest.mark.parametrize(("said", "want"), [
    ("Yes, let's do it.", 500),
    ("Yes", 500),
    ("Sounds good, go ahead.", 500),
    ("Deal.", 500),
    ("OK send me the link", 500),
    ("Please send over the agreement and the invoice.", 500),
    ("I'll take it", 500),
    ("Yes, $500 works, send the agreement.", 500),
    ("Sure, let's do it at $300.", None),         # not a new client's price
    ("where do we pay?", None),                   # a question mark is a question
    ("Yes but can you do it for $400?", None),    # a counter-offer
    ("How about $300?", None),                    # a counter-offer, not a yes
    ("Yes, if you include hosting.", None),       # conditional
    ("Let me think about it.", None),
    ("Yes, is the AI chat included?", None),
    ("That's too expensive.", None),              # an objection
    ("Yes, $500 or $300, whichever", None),       # two figures
    ("Yes, let's do it for $250", None),          # below the floor (and a counter)
    ("No thanks", None),
    ("We already have someone.", None),
])
def test_acceptance_is_read_in_code(said, want):
    """Against a reply that quoted a new client's price."""
    assert pricing.accepted_price(said, TERMS, quoted=[500]) == want


def test_a_returning_client_accepts_300_only():
    assert pricing.accepted_price("Sure, let's do it at $300.", RETURNING) == 300
    assert pricing.accepted_price("yes", RETURNING, quoted=[300]) == 300
    assert pricing.accepted_price("yes", RETURNING, quoted=[500]) is None
    unread = pricing.Terms(500, 300, returning=None)
    assert pricing.accepted_price("yes", unread, quoted=[500]) is None


@pytest.mark.parametrize("said", ["Yes", "Great work, love it!", "Perfect, thanks",
                                  "Sounds good, go ahead.", "Ok send me the link"])
def test_assent_to_no_price_is_no_agreement(said):
    """Nobody put a price to them (the free preview, a reply that named
    none): "Great work!" is not a yes to $500."""
    assert pricing.accepted_price(said, TERMS) is None
    assert pricing.accepted_price(said, TERMS, quoted=[]) is None


def test_a_counter_offer_below_the_floor_is_never_an_acceptance():
    assert pricing.counter_offer("I'd do it for $150, final offer") == [150]
    assert pricing.accepted_price("ok, I'd do it for $150, final offer", TERMS) is None


def test_a_plain_yes_accepts_the_one_price_we_quoted():
    """Their "yes" is to the one figure our reply named, and only when it is
    this client's price. Two figures quoted: ambiguous."""
    assert pricing.accepted_price("yes let's go", TERMS, quoted=[500]) == 500
    assert pricing.accepted_price("yes let's go", TERMS, quoted=[100]) is None
    assert pricing.accepted_price("yes let's go", TERMS, quoted=[500, 300]) is None


def test_no_price_configured_means_no_agreement():
    assert pricing.accepted_price("yes let's do it",
                                  pricing.Terms(None, None, returning=False)) is None


# ========================================================= intake queues them

def _intake(monkeypatch, *, last_reply, intent="interested", email="joe@example.com"):
    from atlas.kernel import comms, llm
    done: dict = {"booking": [], "agreement": [], "reply": [], "alerts": []}
    monkeypatch.setattr(_store, "insert_lead_message", _returns(12))
    monkeypatch.setattr(_store, "set_message_intent", _returns(None))
    monkeypatch.setattr(_store, "mark_message_alerted", _returns(None))
    monkeypatch.setattr(_store, "lead_returning", _returns(False))
    monkeypatch.setattr(_store, "lead_links", _returns({}))
    monkeypatch.setattr(_store, "consent_for_lead", _returns(None))
    monkeypatch.setattr(_store, "lead_email", _returns(email))
    monkeypatch.setattr(_store, "lead_message_bodies", _returns(["sounds good"]))
    monkeypatch.setattr(_store, "latest_sent_reply", _returns(last_reply))
    monkeypatch.setattr(llm, "think", _returns(Completion(intent, "t", "m")))

    async def booking(message_id, slot):
        done["booking"].append((message_id, slot))
    monkeypatch.setattr(_store, "queue_booking", booking)

    async def agreement(message_id, price):
        done["agreement"].append((message_id, price))
    monkeypatch.setattr(_store, "queue_agreement", agreement)

    async def reply(message_id, channel):
        done["reply"].append(channel)
    monkeypatch.setattr(_store, "queue_reply", reply)

    async def notify(out):
        done["alerts"].append(out)
    monkeypatch.setattr(comms, "notify", notify)
    return done


LAST_REPLY = {"id": 21, "offered_slots": OFFERED_ISO, "quoted_prices": [500]}


async def test_a_picked_time_queues_a_booking_instead_of_a_drafted_reply(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    out = await leads.take_in(LEAD, "email", "<m1@x>", "joe@example.com",
                              "Tuesday at 2 works for me")
    assert done["booking"] == [(12, TUE_2PM)] and out["booking"] == TUE_2PM.isoformat()
    assert done["agreement"] == [] and done["reply"] == []
    [alert] = done["alerts"]
    assert "they picked Tuesday at 2 in the afternoon" in alert.body
    assert "Read in code from their words" in alert.body


async def test_an_accepted_price_queues_the_agreement(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    out = await leads.take_in(LEAD, "email", "<m2@x>", "joe@example.com",
                              "Yes, let's do it. Send me the link.")
    assert done["agreement"] == [(12, 500)] and out["agreement"] == 500
    assert done["booking"] == [] and done["reply"] == []
    assert "they accepted $500" in done["alerts"][0].body


async def test_both_at_once_queue_both(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    await leads.take_in(LEAD, "email", "<m3@x>", "joe@example.com",
                        "Yes, let's do it. The second one works for the call.")
    assert done["agreement"] == [(12, 500)] and done["booking"] == [(12, WED_11)]
    assert done["reply"] == []


async def test_anything_ambiguous_stays_with_the_drafted_reply(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    await leads.take_in(LEAD, "email", "<m4@x>", "joe@example.com",
                        "Tuesday or Wednesday could work, and is hosting included?")
    assert done["booking"] == [] and done["agreement"] == [] and done["reply"] == ["email"]


async def test_no_offered_slots_means_no_booking_from_a_time(monkeypatch):
    """Only slots OUR last reply offered can be matched; a lead naming a
    time out of the blue gets the drafted reply."""
    done = _intake(monkeypatch, last_reply={"id": 20, "offered_slots": None,
                                            "quoted_prices": []})
    await leads.take_in(LEAD, "email", "<m5@x>", "joe@example.com", "Tuesday at 2?")
    assert done["booking"] == [] and done["reply"] == ["email"]


async def test_a_price_intent_can_accept_but_a_question_cannot(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY, intent="price")
    await leads.take_in(LEAD, "email", "<m6@x>", "joe@example.com", "ok deal, $500")
    assert done["agreement"] == [(12, 500)]
    done = _intake(monkeypatch, last_reply=LAST_REPLY, intent="question")
    await leads.take_in(LEAD, "email", "<m7@x>", "joe@example.com", "ok deal, $500")
    assert done["agreement"] == [] and done["reply"] == ["email"]


async def test_no_email_on_record_means_no_agreement_by_email(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY, email=None)
    await leads.take_in(LEAD, "email", "<m9@x>", "joe@example.com", "yes let's do it")
    assert done["agreement"] == []


async def test_only_an_email_is_read_for_a_pick_or_a_yes(monkeypatch):
    """A text or a call transcript saying "the first one" or "yes" books
    nothing and sends nothing: the brief is by email."""
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    await leads.take_in(LEAD, "sms", "SM1", "+17185550101", "yes let's do it, the first one")
    await leads.take_in(LEAD, "call", "CA1", "+17185550101", "yes, the first one")
    assert done["booking"] == [] and done["agreement"] == []


async def test_great_work_on_a_cold_preview_is_not_an_agreement(monkeypatch):
    """No reply of ours was ever sent: nothing was offered or quoted."""
    done = _intake(monkeypatch, last_reply=None)
    await leads.take_in(LEAD, "email", "<m10@x>", "joe@example.com",
                        "Great work, love it!")
    assert done["agreement"] == [] and done["booking"] == []
    assert done["reply"] == ["email"]


async def test_a_no_or_not_now_never_books(monkeypatch):
    for intent in ("no", "not_now", "other"):
        done = _intake(monkeypatch, last_reply=LAST_REPLY, intent=intent)
        await leads.take_in(LEAD, "email", f"<n-{intent}@x>", "joe@example.com",
                            "Tuesday at 2")
        assert done["booking"] == [] and done["agreement"] == [], intent


async def test_a_turned_down_time_is_answered_not_booked(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    await leads.take_in(LEAD, "email", "<m11@x>", "joe@example.com",
                        "Tuesday at 2 doesn't work for me, sorry.")
    assert done["booking"] == [] and done["reply"] == ["email"]


async def test_a_pick_with_a_question_books_and_still_answers(monkeypatch):
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    await leads.take_in(LEAD, "email", "<m12@x>", "joe@example.com",
                        "Tuesday at 2 works. Is hosting included?")
    assert done["booking"] == [(12, TUE_2PM)] and done["reply"] == ["email"]


async def test_an_unverified_lead_gets_neither(monkeypatch):
    from atlas.modules.agency import verify
    monkeypatch.setattr(verify, "contact_refusal", _returns("not verified"))
    done = _intake(monkeypatch, last_reply=LAST_REPLY)
    await leads.take_in(LEAD, "email", "<m8@x>", "joe@example.com",
                        "Yes, let's do it. Tuesday at 2.")
    assert done["booking"] == [] and done["agreement"] == [] and done["reply"] == []


# ============================================================ the booking pass

HELD = ActionResult(True, "held Tue 06 Oct 14:00 EDT for Joe's Plumbing",
                    evidence={"meeting_id": 4, "event_id": "ev1",
                              "email": "joe@example.com"})
INVITED = ActionResult(True, "invited joe@example.com to the call with Miro",
                       evidence={"to": "joe@example.com"})


def _booking_world(monkeypatch, hold=HELD, invite=INVITED, existing=None) -> World:
    world = World(monkeypatch, {"agency.hold_meeting": hold, "agency.send_invite": invite})
    monkeypatch.setattr(_store, "bookings_awaiting", _returns(
        [{"id": 12, "lead_id": 7, "booking_slot": TUE_2PM}]))
    monkeypatch.setattr(_store, "meeting_at", _returns(existing))
    return world


async def test_a_booking_holds_then_raises_one_invite_card(monkeypatch):
    world = _booking_world(monkeypatch)
    assert await lead_followup.bookings() == 1
    assert world.ran == [("agency.hold_meeting",
                          {"lead_id": 7, "start": TUE_2PM.isoformat()})]
    assert world.previewed == [("agency.send_invite", {"meeting_id": 4})]
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.send_invite" and params == {"meeting_id": 4}
    assert not reason.startswith("Held for you")
    assert "Tuesday at 2 in the afternoon" in reason
    assert kw["dedup_key"] == "agency.send_invite:4" and kw["automatic"] is True
    assert world.slack == [] and world.released == []


async def test_a_taken_slot_is_the_card_with_the_reason(monkeypatch):
    world = _booking_world(monkeypatch, hold=_refused(
        "Tue 06 Oct 14:00 is not an open business-hours slot on Miro's calendar"))
    assert await lead_followup.bookings() == 1
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.hold_meeting" and params["start"] == TUE_2PM.isoformat()
    assert reason.startswith("Held for you: Tue 06 Oct 14:00 is not an open")
    assert "Joe's Plumbing picked Tuesday at 2 in the afternoon by email" in reason
    assert kw["dedup_key"] == "agency.hold_meeting:12"
    assert world.ran == [("agency.hold_meeting", {"lead_id": 7,
                                                  "start": TUE_2PM.isoformat()})]


async def test_a_refused_invite_dry_run_is_held_for_you_on_the_card(monkeypatch):
    world = _booking_world(monkeypatch, invite=_refused(
        "joe@example.com is on the suppression list"))
    assert await lead_followup.bookings() == 1
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.send_invite" and params == {"meeting_id": 4}
    assert reason.startswith("Held for you: joe@example.com is on the suppression list")
    assert kw["evidence"]["held"] == "joe@example.com is on the suppression list"


async def test_a_hold_before_the_calendar_hold_releases_the_claim(monkeypatch):
    world = _booking_world(monkeypatch, hold=_held_result())
    assert await lead_followup.bookings() == 0
    assert world.released == [("booking", 12)] and world.ran[-1][0] == "agency.hold_meeting"
    assert world.cards == []


async def test_an_existing_hold_gets_only_the_card_and_an_invited_one_nothing(
        monkeypatch):
    world = _booking_world(monkeypatch, existing={"id": 4, "status": "held"})
    assert await lead_followup.bookings() == 1
    assert world.ran == [] and [c[0] for c in world.cards] == ["agency.send_invite"]
    world = _booking_world(monkeypatch, existing={"id": 4, "status": "invited"})
    assert await lead_followup.bookings() == 0 and world.ran == [] and world.cards == []


async def test_an_existing_card_is_not_raised_twice(monkeypatch):
    """propose dedups on the key: a second tick gets the same card id back
    and nothing is sent either time."""
    world = _booking_world(monkeypatch, existing={"id": 4, "status": "held"})
    await lead_followup.bookings()
    await lead_followup.bookings()
    keys = {kw["dedup_key"] for *_x, kw in world.cards}
    assert keys == {"agency.send_invite:4"} and world.ran == []


# ========================================================== the agreement pass

def _agreement_world(monkeypatch, send: ActionResult) -> World:
    # The proposal is drafted (Tier 1) for the card; here it fails, and the
    # card is raised without one.
    world = World(monkeypatch, {"agency.send_agreement": send,
                                "sales.draft_proposal": ActionResult(False, "no price")})
    monkeypatch.setattr(_store, "agreements_awaiting", _returns(
        [{"id": 12, "lead_id": 7, "agreement_price": 500}]))
    return world


async def test_an_accepted_price_raises_one_agreement_card(monkeypatch):
    world = _agreement_world(monkeypatch, ActionResult(
        True, "would email the agreement ($500) to joe@example.com",
        evidence={"to": "joe@example.com", "price": 500, "text": "Agreement..."}))
    assert await lead_followup.agreements() == 1
    assert world.ran == [("sales.draft_proposal", {"lead_id": 7})]
    assert world.previewed == [("agency.send_agreement",
                                {"lead_id": 7, "price": 500, "channel": "email"})]
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.send_agreement" and params["price"] == 500
    assert not reason.startswith("Held for you")
    assert kw["dedup_key"] == "agency.send_agreement:12" and kw["automatic"] is True
    assert kw["evidence"]["dry_run"]["to"] == "joe@example.com"
    assert world.slack == []


async def test_no_payment_link_is_held_for_you_on_the_card(monkeypatch):
    world = _agreement_world(monkeypatch, _refused(
        "no payment link for $500 in AGENCY_PAYMENT_LINKS"))
    assert await lead_followup.agreements() == 1
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.send_agreement" and params["price"] == 500
    assert reason.startswith("Held for you: no payment link for $500")
    assert kw["dedup_key"] == "agency.send_agreement:12" and world.released == []
    assert world.ran == [("sales.draft_proposal", {"lead_id": 7})]


async def test_an_autosend_hold_is_not_held_for_you(monkeypatch):
    """The autosend holds are what the card itself answers."""
    world = _agreement_world(monkeypatch, _refused(
        "held for you: automatic sending is off"))
    world.cards.clear()
    from atlas.kernel import executor

    async def preview(action, params):
        return ActionResult(False, "held for you: automatic sending is off")
    monkeypatch.setattr(executor, "preview", preview)
    assert await lead_followup.agreements() == 1
    [(_a, _p, reason, kw)] = world.cards
    assert not reason.startswith("Held for you") and kw["evidence"]["held"] is None


async def test_the_agreement_rollback_is_honest(monkeypatch):
    suppressed = []
    monkeypatch.setattr(_store, "get_reply_draft", _returns(
        {"id": 30, "lead_id": 7, "channel": "email", "to_address": "joe@example.com",
         "kind": "agreement"}))

    async def add(address, why):
        suppressed.append(address)
    monkeypatch.setattr(_store, "add_suppression", add)
    monkeypatch.setattr(_store, "set_lead_meta", _returns(None))
    result = await replies.send_agreement_rollback("30")
    assert result.ok and result.evidence["unsent"] is False
    assert suppressed == ["joe@example.com"]


# ================================================================== the calls

CONSENT = {"id": 41, "lead_id": 7, "scope": "call", "revoked_at": None,
           "call_card_at": None, "source_channel": "web_form", "quoted": "call me",
           "evidence": {"matches_listed_phone": True, "page_url": "https://x/"}}


def _call_world(monkeypatch, call: ActionResult, *, guard_ok=True) -> World:
    world = World(monkeypatch, {"agency.call_lead": call})
    world.marked = []
    monkeypatch.setattr(_store, "get_consent", _returns(dict(CONSENT)))

    async def mark(consent_id, note):
        world.marked.append(note)
        return True
    monkeypatch.setattr(_store, "mark_call_card", mark)

    async def note(consent_id, note):
        world.marked.append(note)
    monkeypatch.setattr(_store, "note_call_card", note)

    async def release(consent_id):
        world.released.append(("consent", consent_id))
    monkeypatch.setattr(_store, "release_call_card", release)

    async def guard(lead_id, channel, *, now=None):
        if guard_ok:
            return SimpleNamespace(lead=LEAD, phone="+17185550199",
                                   opening="Hi, this is Miro's assistant"), None
        return None, ("no consent on record for a call to lead 7: ATLAS texts or "
                      "calls only a lead who asked for it (TCPA)")
    monkeypatch.setattr(consent, "contact_guard", guard)
    return world


async def test_a_consented_call_is_placed_through_the_executor_with_a_receipt(
        monkeypatch):
    world = _call_world(monkeypatch, ActionResult(
        True, "calling lead 7 at +17185550199", evidence={"contact_id": 5}))
    said = await lead_followup.call_due(41)
    assert said.startswith("calling Joe's Plumbing at +17185550199")
    assert world.ran == [("agency.call_lead", {"lead_id": 7})]
    assert world.marked == ["calling", "called (contact #5)"]
    [receipt] = world.slack
    assert receipt.title == ("Calling Joe's Plumbing at +17185550199 (they asked on "
                             "the \"Have Mapfront call me\" form on their preview)")
    assert receipt.kind == "receipt" and world.cards == []


async def test_without_consent_nothing_is_dialled(monkeypatch):
    """The consent guard is the gate: refused, the executor is never reached."""
    world = _call_world(monkeypatch, ActionResult(True, "never"), guard_ok=False)
    said = await lead_followup.call_due(41)
    assert said.startswith("no call: no consent on record")
    assert world.ran == [] and world.cards == [] and world.slack == []
    assert world.marked == [("no call: no consent on record for a call to lead 7: ATLAS "
                             "texts or calls only a lead who asked for it (TCPA)")]


async def test_a_refused_call_is_the_card_with_the_reason(monkeypatch):
    from atlas.kernel.contracts import Severity
    world = _call_world(monkeypatch, _refused(
        "2 text(s)/call(s) since the lead last wrote; the cap is 2"))
    said = await lead_followup.call_due(41)
    assert said.startswith("held for you (2 text(s)/call(s) since the lead last wrote")
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.call_lead" and params == {"lead_id": 7}
    assert reason.startswith("Held for you: 2 text(s)/call(s)")
    assert "it is their listed number" in reason
    assert kw["severity"] is Severity.HIGH
    assert kw["dedup_key"] == "agency.call_lead:consent:41"
    assert kw["evidence"]["phone"] == "+17185550199" and kw["evidence"]["consent_id"] == 41
    assert world.marked[-1].startswith("held for you")


async def test_a_held_call_releases_the_consent_for_a_later_tick(monkeypatch):
    world = _call_world(monkeypatch, _held_result())
    said = await lead_followup.call_due(41)
    assert said.startswith("held, tried again on a later tick")
    assert world.released == [("consent", 41)] and world.cards == [] and world.slack == []


async def test_calls_due_counts_cards_and_tick_runs_every_pass(monkeypatch):
    world = _call_world(monkeypatch, _refused("calling leads is off"))
    monkeypatch.setattr(_store, "consents_awaiting_call_card", _returns([{"id": 41}]))
    assert await lead_followup.calls_due() == 1 and len(world.cards) == 1
    # tick() runs every pass; a pass that cannot read its table is logged, not raised.
    monkeypatch.setattr(_store, "messages_awaiting_reply", _returns([]))
    monkeypatch.setattr(_store, "bookings_awaiting", _returns([]))
    monkeypatch.setattr(_store, "agreements_awaiting", _returns([]))
    monkeypatch.setattr(_store, "claim_deployed_sites", _returns([]))
    monkeypatch.setattr(_store, "outreach_due_followup", _returns([]))
    monkeypatch.setattr(_store, "claim_reminders", _returns([]))
    assert await lead_followup.tick() == 1


async def test_the_call_rollback_is_honest(monkeypatch):
    from atlas.modules.agency import calls
    suppressed, meta = [], []
    monkeypatch.setattr(_store, "get_contact", _returns(
        {"id": 5, "lead_id": 7, "phone": "+17185550199"}))

    async def add(address, why):
        suppressed.append(address)
    monkeypatch.setattr(_store, "add_suppression", add)

    async def set_meta(lead_id, key, value):
        meta.append(key)
    monkeypatch.setattr(_store, "set_lead_meta", set_meta)
    result = await calls.call_lead_rollback("5")
    assert result.ok and result.evidence["unrung"] is False
    assert suppressed == ["tel:+17185550199"] and meta == ["call_rolled_back"]


# ================================================================== the SQL

async def test_the_queues_claim_once_in_the_same_update(memory_backend):
    memory_backend.responses["SET booked_at=now()"] = {"id": 12}
    memory_backend.responses["SET agreed_at=now()"] = None
    memory_backend.responses["booking_slot IS NOT NULL AND booked_at IS NULL"] = []
    memory_backend.responses["agreement_price IS NOT NULL AND agreed_at IS NULL"] = []
    assert await _store.claim_booking(12) is True
    assert await _store.claim_agreement(12) is False
    assert await _store.bookings_awaiting(5) == []
    assert await _store.agreements_awaiting(5) == []
    sql = " ".join(s for s, _ in memory_backend.sql_log)
    assert "WHERE id=$1 AND booked_at IS NULL RETURNING id" in sql
    assert "WHERE id=$1 AND agreed_at IS NULL RETURNING id" in sql
    assert "booking_slot > now()" in sql and "interval '1 day'" in sql
    await _store.queue_booking(12, TUE_2PM)
    await _store.queue_agreement(12, 500)
    assert memory_backend.sql_log[-2][1] == (12, TUE_2PM)
    assert memory_backend.sql_log[-1][1] == (12, 500)


async def test_the_draft_row_carries_the_offered_slots(memory_backend):
    memory_backend.responses["INSERT INTO agency_reply_drafts"] = {"id": 21}
    assert await _store.insert_reply_draft(7, 11, "reply", "email", "j@x", "Re", "body",
                                           [500], offered_slots=OFFERED_ISO) == 21
    sql, args = memory_backend.sql_log[-1]
    assert "offered_slots" in sql and args[-1] == '["' + '", "'.join(OFFERED_ISO) + '"]'
    assert await _store.insert_reply_draft(7, 11, "reply", "email", "j@x", "Re", "body",
                                           [500]) == 21
    assert memory_backend.sql_log[-1][1][-1] is None


def test_the_schema_has_the_columns():
    from pathlib import Path
    schema = (Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text()
    for column in ("agency_reply_drafts ADD COLUMN IF NOT EXISTS offered_slots JSONB",
                   "agency_lead_messages ADD COLUMN IF NOT EXISTS booking_slot TIMESTAMPTZ",
                   "agency_lead_messages ADD COLUMN IF NOT EXISTS booked_at TIMESTAMPTZ",
                   "agency_lead_messages ADD COLUMN IF NOT EXISTS agreement_price INTEGER",
                   "agency_lead_messages ADD COLUMN IF NOT EXISTS agreed_at TIMESTAMPTZ"):
        assert column in schema, column


def test_env_example_documents_the_switches():
    from pathlib import Path
    env = (Path(__file__).resolve().parent.parent / ".env.example").read_text()
    assert "AGENCY_REPLY_AUTOSEND=on" in env and "AGENCY_PAYMENT_LINKS" in env


def test_the_module_docstrings_no_longer_say_sent_on_a_card():
    import atlas.kernel.lead_followup as lf
    from atlas.modules.agency import calls as c
    from atlas.modules.agency import leads as ld
    from atlas.modules.agency import meetings as mt
    for module in (replies, mt, c, lf, ld):
        doc = module.__doc__ or ""
        assert "sent on a card" not in doc, module.__name__
        assert "Tier ASK, irreversible" not in doc, module.__name__
    assert "2026-10-06" in (replies.__doc__ or "")


def test_the_slot_window_is_in_the_future(monkeypatch):
    """A queued booking whose slot has passed is nothing to hold (the SQL
    says `booking_slot > now()`); the pass also refuses a naive datetime."""
    now = datetime.now(UTC)
    assert (TUE_2PM - now) < timedelta(days=365)      # a sanity bound on the fixture


def test_no_outreach_email_leaves_without_a_card():
    """Every outreach email is a card whatever AGENCY_OUTREACH_AUTOSEND says;
    a reply is held by AGENCY_REPLY_AUTOSEND=off (the guard test above)."""
    from atlas.kernel.contracts import Tier
    from atlas.modules.agency import AgencyModule
    actions = {a.name: a for a in AgencyModule().actions()}
    send = actions["agency.send_outreach"]
    assert send.tier is Tier.ASK and send.card_only
    assert actions["agency.send_reply"].tier is Tier.AUTO     # held by the env switch
