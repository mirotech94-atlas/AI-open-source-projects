"""After a deploy: the outreach email sent through the executor when the
lead publishes an email (Miro, 2026-10-01: without a card, past the holds
in outreach.auto_hold; a hold is the Tier 2 card with the reason), the
call brief in Slack when not (kernel.lead_followup.after_deploys)."""
from __future__ import annotations

from atlas.kernel import lead_followup
from atlas.kernel.contracts import ActionResult, FailureClass
from atlas.kernel.executor import HOLD
from atlas.modules.agency import _store, contacts

SITE = {"id": 3, "slug": "joes-plumbing-7", "lead_id": 7, "domain": None}
LEAD = {"id": 7, "name": "Joe's Plumbing", "website": "https://joesplumbing.example"}


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


class World:
    def __init__(self, monkeypatch, *, email: str | None, hold: str | None = None,
                 has_outreach: bool = False, send_refused: str | None = None):
        self.ran: list[str] = []
        self.cards: list[tuple] = []
        self.slack: list = []
        self.released: list[str] = []
        monkeypatch.setattr(_store, "has_outreach", _returns(has_outreach))
        monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
        monkeypatch.setattr(contacts, "published_email", _returns(None))

        async def release(slug):
            self.released.append(slug)
        monkeypatch.setattr(_store, "release_site_followup", release)

        async def execute(request):
            self.ran.append(request.action)
            if hold == request.action:
                return ActionResult(False, "watch-only", evidence={HOLD: "watch_only"},
                                    failure_class=FailureClass.DISABLED,
                                    dispatched=False)
            if request.action == "agency.find_email":
                return ActionResult(True, "read", evidence={"email": email, "found": []})
            if request.action == "agency.draft_outreach":
                assert request.params == {"lead_id": 7, "kind": "email"}
                return ActionResult(True, "drafted", evidence={
                    "outreach_id": 55, "to": email or "old@joesplumbing.example",
                    "text": "Hi Joe ...",
                    "preview_url": "https://joes-plumbing-7.mapfront.io/"})
            if request.action == "agency.call_brief":
                return ActionResult(True, "brief", evidence={"text": "# Call brief: Joe"})
            if request.action == "agency.send_outreach":
                assert request.params == {"outreach_id": 55}
                assert request.approved_by is None          # unattended, Tier 1
                if send_refused is not None:
                    return ActionResult(False, f"dry run refused: {send_refused}",
                                        failure_class=FailureClass.VALIDATION,
                                        dispatched=False)
                return ActionResult(True, "sent outreach 55 to "
                                          "office@joesplumbing.example")
            raise AssertionError(request.action)
        monkeypatch.setattr("atlas.kernel.executor.execute", execute)

        async def propose(action, params, reason, **kw):
            self.cards.append((action, params, kw))
            return 90
        monkeypatch.setattr("atlas.kernel.approvals.propose", propose)

        async def notify(out):
            self.slack.append(out)
        monkeypatch.setattr("atlas.kernel.comms.notify", notify)


async def test_a_published_email_is_sent_through_the_executor_without_a_card(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example")
    assert await lead_followup.follow_site(SITE) == "sent"
    assert world.ran == ["agency.find_email", "agency.draft_outreach",
                         "agency.send_outreach"]
    assert world.cards == []
    [receipt] = world.slack
    assert receipt.title == "Emailed Joe's Plumbing" and receipt.kind == "receipt"
    assert "office@joesplumbing.example" in receipt.body
    assert "https://joes-plumbing-7.mapfront.io/" in receipt.body
    assert receipt.topic == "agency"


async def test_a_hold_becomes_the_card_with_the_reason_first(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example",
                  send_refused="held for you: the preview is not deployed yet "
                               "(agency.deploy_site)")
    assert await lead_followup.follow_site(SITE) == "card"
    assert world.ran[-1] == "agency.send_outreach"
    [(action, params, kw)] = world.cards
    assert action == "agency.send_outreach" and params == {"outreach_id": 55}
    assert kw["evidence"]["to"] == "office@joesplumbing.example"
    assert kw["evidence"]["held"] == ("the preview is not deployed yet "
                                      "(agency.deploy_site)")
    assert kw["dedup_key"] == "agency.send_outreach:55" and kw["automatic"]
    assert world.slack == []


async def test_a_refusal_that_is_not_a_hold_is_still_a_card(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example",
                  send_refused="CAN-SPAM: missing postal address line")
    assert await lead_followup.follow_site(SITE) == "card"
    [(_action, _params, kw)] = world.cards
    assert kw["evidence"]["held"] == "CAN-SPAM: missing postal address line"


async def test_a_held_send_releases_the_claim_like_any_hold(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example",
                  hold="agency.send_outreach")
    monkeypatch.setattr(_store, "claim_deployed_sites", _returns([SITE]))
    assert await lead_followup.after_deploys() == 0
    assert world.released == ["joes-plumbing-7"] and world.cards == []


def test_the_card_reason_leads_with_held_for_you(monkeypatch):
    """The exact words `propose` gets, checked against the real call."""
    import asyncio
    captured = {}

    async def propose(action, params, reason, **kw):
        captured["reason"] = reason
        return 1
    world = World(monkeypatch, email="office@joesplumbing.example",
                  send_refused="held for you: automatic sending is off "
                               "(AGENCY_OUTREACH_AUTOSEND)")
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    assert asyncio.run(lead_followup.follow_site(SITE)) == "card"
    assert captured["reason"].startswith(
        "Held for you: automatic sending is off (AGENCY_OUTREACH_AUTOSEND). "
        "Approving runs every check again and sends it if it passes now.\n"
        "email Joe's Plumbing (office@joesplumbing.example)")
    assert world.ran[-1] == "agency.send_outreach"


async def test_no_email_means_the_call_brief_goes_to_slack(monkeypatch):
    world = World(monkeypatch, email=None)
    assert await lead_followup.follow_site(SITE) == "brief"
    assert world.ran == ["agency.find_email", "agency.call_brief"]
    assert world.cards == []
    [post] = world.slack
    assert "Call brief: Joe's Plumbing" in post.title and "# Call brief" in post.body
    assert post.topic == "agency"


async def test_an_email_found_earlier_still_counts(monkeypatch):
    world = World(monkeypatch, email=None)
    monkeypatch.setattr(contacts, "published_email",
                        _returns({"address": "old@joesplumbing.example"}))
    assert await lead_followup.follow_site(SITE) == "sent"
    assert "agency.draft_outreach" in world.ran


async def test_a_lead_with_no_website_goes_straight_to_the_brief(monkeypatch):
    world = World(monkeypatch, email=None)
    monkeypatch.setattr(_store, "get_lead", _returns({**LEAD, "website": None}))
    assert await lead_followup.follow_site(SITE) == "brief"
    assert world.ran == ["agency.call_brief"] and world.slack


async def test_a_redeploy_is_not_a_new_pitch(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example", has_outreach=True)
    assert await lead_followup.follow_site(SITE) == "skipped"
    assert world.ran == [] and world.cards == []


async def test_a_hold_releases_the_claim_for_a_later_tick(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example",
                  hold="agency.find_email")
    other = {**SITE, "slug": "other-9", "lead_id": 9}
    monkeypatch.setattr(_store, "claim_deployed_sites", _returns([SITE, other]))
    assert await lead_followup.after_deploys() == 0
    assert world.released == ["joes-plumbing-7", "other-9"]
    assert world.cards == []


async def test_after_deploys_counts_cards_not_sends(monkeypatch):
    World(monkeypatch, email="office@joesplumbing.example")
    monkeypatch.setattr(_store, "claim_deployed_sites", _returns([SITE]))
    assert await lead_followup.after_deploys() == 0
    World(monkeypatch, email="office@joesplumbing.example",
          send_refused="held for you: x")
    monkeypatch.setattr(_store, "claim_deployed_sites", _returns([SITE]))
    assert await lead_followup.after_deploys() == 1

