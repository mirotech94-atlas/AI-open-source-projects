"""After a deploy: the outreach email drafted and raised as one Tier 2 card
when the lead publishes an email (six agents, 2026-10-27: the dry run runs
first through executor.preview and a refusal leads the card with "Held for
you"; nothing is sent by the pass), the call brief in Slack when not
(kernel.lead_followup.after_deploys)."""
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
                 has_outreach: bool = False, send_refused: str | None = None,
                 qa_passed: bool = True,
                 preview_url: str = "https://agency.storefront.test/preview/joes-plumbing-7/"):
        self.reasons: list[str] = []
        self.ran: list[str] = []
        self.previewed: list[str] = []
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
                    "preview_url": preview_url})
            if request.action == "qa.check_draft":
                return ActionResult(True, "qa", evidence={"passed": qa_passed})
            if request.action == "agency.call_brief":
                return ActionResult(True, "brief", evidence={"text": "# Call brief: Joe"})
            raise AssertionError(request.action)
        monkeypatch.setattr("atlas.kernel.executor.execute", execute)

        async def preview(action, params):
            assert action == "agency.send_outreach" and params == {"outreach_id": 55}
            self.previewed.append(action)
            if send_refused is not None:
                return ActionResult(False, f"dry run refused: {send_refused}",
                                    failure_class=FailureClass.VALIDATION,
                                    dispatched=False)
            return ActionResult(True, "would send outreach 55 to "
                                      "office@joesplumbing.example")
        monkeypatch.setattr("atlas.kernel.executor.preview", preview)

        async def propose(action, params, reason, **kw):
            self.reasons.append(reason)
            self.cards.append((action, params, kw))
            return 90
        monkeypatch.setattr("atlas.kernel.approvals.propose", propose)

        async def notify(out):
            self.slack.append(out)
        monkeypatch.setattr("atlas.kernel.comms.notify", notify)


async def test_a_published_email_is_one_card_never_a_send(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example")
    assert await lead_followup.follow_site(SITE) == "card"
    assert world.ran == ["agency.find_email", "agency.draft_outreach", "qa.check_draft"]
    assert world.previewed == ["agency.send_outreach"]
    [(action, params, kw)] = world.cards
    assert action == "agency.send_outreach" and params == {"outreach_id": 55}
    assert kw["evidence"]["to"] == "office@joesplumbing.example"
    assert kw["evidence"]["held"] is None
    assert kw["dedup_key"] == "agency.send_outreach:55" and kw["automatic"]
    assert world.slack == []


async def test_a_refused_dry_run_leads_the_card_with_held_for_you(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example",
                  send_refused="CAN-SPAM: missing postal address line")
    assert await lead_followup.follow_site(SITE) == "card"
    [(_action, _params, kw)] = world.cards
    assert kw["evidence"]["held"] == "CAN-SPAM: missing postal address line"
    assert "agency.send_outreach" not in world.ran


async def test_an_autosend_hold_is_what_the_card_answers(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example",
                  send_refused="held for you: automatic sending is off")
    assert await lead_followup.follow_site(SITE) == "card"
    [(_action, _params, kw)] = world.cards
    assert kw["evidence"]["held"] is None


def test_the_card_reason_leads_with_held_for_you(monkeypatch):
    """The exact words `propose` gets, checked against the real call."""
    import asyncio
    captured = {}

    async def propose(action, params, reason, **kw):
        captured["reason"] = reason
        return 1
    World(monkeypatch, email="office@joesplumbing.example",
          send_refused="CAN-SPAM: missing postal address line")
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    assert asyncio.run(lead_followup.follow_site(SITE)) == "card"
    assert captured["reason"].startswith(
        "Held for you: CAN-SPAM: missing postal address line. Approving runs every "
        "check again and sends it only if it passes then.\n"
        "Email Joe's Plumbing the preview of their site, built by Websites.")
    for shown in ("Preview: https://agency.storefront.test/preview/joes-plumbing-7/",
                  "Owner: Joe's Plumbing, office@joesplumbing.example", "Hi Joe ..."):
        assert shown in captured["reason"], shown


def test_a_clean_card_says_approving_sends_it(monkeypatch):
    import asyncio
    captured = {}

    async def propose(action, params, reason, **kw):
        captured["reason"] = reason
        return 1
    World(monkeypatch, email="office@joesplumbing.example")
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    assert asyncio.run(lead_followup.follow_site(SITE)) == "card"
    assert captured["reason"].startswith("Email Joe's Plumbing the preview")
    assert "Approving sends it, after every check runs again." in captured["reason"]


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
    assert await lead_followup.follow_site(SITE) == "card"
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


async def test_after_deploys_counts_each_card(monkeypatch):
    World(monkeypatch, email="office@joesplumbing.example")
    monkeypatch.setattr(_store, "claim_deployed_sites", _returns([SITE]))
    assert await lead_followup.after_deploys() == 1
    World(monkeypatch, email="office@joesplumbing.example",
          send_refused="held for you: x")
    monkeypatch.setattr(_store, "claim_deployed_sites", _returns([SITE]))
    assert await lead_followup.after_deploys() == 1


async def test_a_draft_that_failed_qa_gets_a_brief_that_says_so(monkeypatch):
    world = World(monkeypatch, email="office@joesplumbing.example", qa_passed=False)
    assert await lead_followup.follow_site(SITE) == "brief"
    assert world.cards == [] and "agency.send_outreach" not in world.ran
    [post] = world.slack
    assert "failed QA" in post.title and "no published email" not in post.title


async def test_a_vercel_preview_is_one_card_naming_its_url_never_a_send(monkeypatch):
    url = "https://mapfront-joes-plumbing-7.vercel.app/"
    world = World(monkeypatch, email="office@joesplumbing.example", preview_url=url)
    site = {**SITE, "domain": "mapfront-joes-plumbing-7.vercel.app"}
    assert await lead_followup.follow_site(site) == "card"
    assert [c[0] for c in world.cards] == ["agency.send_outreach"]
    assert f"Preview: {url}" in world.reasons[0]
    assert "agency.send_outreach" not in world.ran        # raised, never executed
