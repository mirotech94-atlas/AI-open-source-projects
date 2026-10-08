"""The one timed follow-up (kernel.lead_followup.followups): a first email
still unanswered after AGENCY_FOLLOWUP_DAYS gets its single follow-up
through the executor; a reply (status `replied`) or a later message stops
it at the query; a hold releases the claim; a refusal keeps it."""
from __future__ import annotations

from datetime import timedelta

from atlas.kernel import lead_followup
from atlas.kernel.contracts import ActionResult, FailureClass
from atlas.kernel.executor import HOLD
from atlas.modules.agency import _store

FIRST = {"id": 1, "lead_id": 219, "to_address": "stella@estelleny.com",
         "sent_at": "2026-10-05 02:34:46+00"}
LEAD = {"id": 219, "name": "Estelle Hair Studio"}


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


class World:
    def __init__(self, monkeypatch, *, due=(FIRST,), hold=None, send_refused=None,
                 chained=True):
        self.ran, self.cards, self.slack, self.released, self.claimed = [], [], [], [], []
        monkeypatch.setenv("AGENCY_FOLLOWUP_DAYS", "4")
        monkeypatch.setattr(_store, "outreach_due_followup", _returns(list(due)))
        monkeypatch.setattr(_store, "get_lead", _returns(LEAD))

        async def claim(oid):
            self.claimed.append(oid)
            return True

        async def release(oid):
            self.released.append(oid)
        monkeypatch.setattr(_store, "claim_followup", claim)
        monkeypatch.setattr(_store, "release_followup", release)

        async def execute(request):
            self.ran.append(request.action)
            if hold == request.action:
                return ActionResult(False, "paused", evidence={HOLD: "paused"},
                                    failure_class=FailureClass.DISABLED, dispatched=False)
            if request.action == "agency.draft_outreach":
                assert request.params == {"lead_id": 219, "kind": "email"}
                return ActionResult(True, "drafted", evidence={
                    "outreach_id": 2, "to": "stella@estelleny.com", "text": "A short...",
                    "followup_of": 1 if chained else None})
            if request.action == "agency.send_outreach":
                assert request.params == {"outreach_id": 2}
                assert request.approved_by is None
                if send_refused:
                    return ActionResult(False, f"dry run refused: {send_refused}",
                                        failure_class=FailureClass.VALIDATION,
                                        dispatched=False)
                return ActionResult(True, "sent outreach 2 to stella@estelleny.com")
            raise AssertionError(request.action)
        monkeypatch.setattr("atlas.kernel.executor.execute", execute)

        async def propose(action, params, reason, **kw):
            self.cards.append((action, params, reason, kw))
            return 91
        monkeypatch.setattr("atlas.kernel.approvals.propose", propose)

        async def notify(out):
            self.slack.append(out)
        monkeypatch.setattr("atlas.kernel.comms.notify", notify)


async def test_an_unanswered_first_email_gets_its_one_follow_up(monkeypatch):
    world = World(monkeypatch)
    assert await lead_followup.followups() == 0          # sent, no card
    assert world.claimed == [1]
    assert world.ran == ["agency.draft_outreach", "agency.send_outreach"]
    [msg] = world.slack
    assert "Followed up with Estelle Hair Studio" == msg.title
    assert "nothing more goes to them" in msg.body
    assert world.released == []


async def test_a_hold_releases_the_claim_for_a_later_tick(monkeypatch):
    other = {**FIRST, "id": 7, "lead_id": 219}
    world = World(monkeypatch, due=(FIRST, other), hold="agency.send_outreach")
    assert await lead_followup.followups() == 0
    assert world.released == [1, 7]
    assert world.ran == ["agency.draft_outreach", "agency.send_outreach"]


async def test_a_refusal_becomes_the_held_card_and_keeps_the_claim(monkeypatch):
    world = World(monkeypatch, send_refused="AGENCY_OUTREACH_AUTOSEND is off")
    assert await lead_followup.followups() == 1
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.send_outreach" and params == {"outreach_id": 2}
    assert reason.startswith("Held for you: AGENCY_OUTREACH_AUTOSEND is off")
    assert kw["evidence"]["followup_of"] == 1 and kw["automatic"] is True
    assert world.released == []


async def test_a_draft_that_did_not_chain_is_not_sent(monkeypatch):
    """A draft with no followup_of would be a fresh first pitch, not the
    follow-up; it is left unsent and the claim stays."""
    world = World(monkeypatch, chained=False)
    assert await lead_followup.followups() == 0
    assert world.ran == ["agency.draft_outreach"]
    assert world.slack == []


async def test_zero_days_switches_the_pass_off(monkeypatch):
    world = World(monkeypatch)
    monkeypatch.setenv("AGENCY_FOLLOWUP_DAYS", "0")
    assert await lead_followup.followups() == 0
    assert world.ran == [] and world.claimed == []


def test_the_default_wait_is_four_days(monkeypatch):
    monkeypatch.delenv("AGENCY_FOLLOWUP_DAYS", raising=False)
    assert lead_followup.followup_after() == timedelta(days=4)


async def test_the_due_query_stops_on_a_reply_or_a_later_message(memory_backend):
    """The SQL itself carries the stop: only status='sent' first messages
    with no later non-draft message to the lead and no suppression."""
    memory_backend.responses["followup_claimed_at IS NULL"] = []
    assert await _store.outreach_due_followup(timedelta(days=4), 5) == []
    [(sql, args)] = [(s, a) for s, a in memory_backend.sql_log
                     if "followup_claimed_at IS NULL" in s]
    assert "o.status = 'sent'" in sql and "o.followup_of IS NULL" in sql
    assert "f.status <> 'draft' AND f.at > o.at" in sql
    assert "agency_suppression" in sql
    assert args == (timedelta(days=4), 5)
