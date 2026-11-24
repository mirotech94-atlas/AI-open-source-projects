"""The Websites lane's one approval card (Miro, 2026-10-28): a built preview
reaches its owner only through an `agency.send_outreach` card that shows the
preview link, the owner, the address, the subject and the email; approving
sends that one email through the executor. US leads only; nothing sends on
a timeout; no other path names the link first."""
from __future__ import annotations

import pytest

from atlas.kernel import lead_followup
from atlas.kernel.contracts import ActionResult, FailureClass, Tier
from atlas.kernel.executor import HOLD
from atlas.modules.agency import _store, contacts, preview_card, verify

LEAD = {"id": 7, "name": "Joe's Plumbing <!channel>", "website": "https://joes.example"}
URL = "https://agency.storefront.test/preview/joes-plumbing-7/"
BODY = "Hi Joe,\nWe built you a free preview: " + URL + "\n123 Main St\nReply STOP."


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


class World:
    def __init__(self, monkeypatch, *, email="office@joes.example", qa=True,
                 hold=None, draft_refused=None, published=None):
        self.ran: list[str] = []
        self.cards: list[tuple] = []
        monkeypatch.setattr(contacts, "published_email", _returns(published))

        async def execute(request):
            self.ran.append(request.action)
            if request.action == hold:
                return ActionResult(False, "watch-only", evidence={HOLD: "watch_only"},
                                    failure_class=FailureClass.DISABLED, dispatched=False)
            if request.action == "agency.find_email":
                return ActionResult(True, "read", evidence={"email": email,
                                                            "found": ["/contact"]})
            if request.action == "agency.draft_outreach":
                if draft_refused:
                    return ActionResult(False, draft_refused,
                                        failure_class=FailureClass.VALIDATION)
                return ActionResult(True, "drafted", evidence={
                    "outreach_id": 55, "to": email or (published or {}).get("address"),
                    "subject": "A free website preview for Joe's Plumbing",
                    "text": BODY, "preview_url": URL})
            if request.action == "qa.check_draft":
                assert request.params == {"kind": "email", "ref": "agency_outreach:55"}
                return ActionResult(True, "qa", evidence={"passed": qa})
            raise AssertionError(request.action)
        monkeypatch.setattr("atlas.kernel.executor.execute", execute)
        monkeypatch.setattr("atlas.kernel.executor.preview",
                            _returns(ActionResult(True, "would send")))

        async def propose(action, params, reason, **kw):
            self.cards.append((action, params, reason, kw))
            return 90
        monkeypatch.setattr("atlas.kernel.approvals.propose", propose)


async def test_one_card_with_the_link_the_owner_and_the_email_and_no_send(monkeypatch):
    world = World(monkeypatch)
    assert await lead_followup.pitch_preview(LEAD, "site published", find=True) == "card"
    assert world.ran == ["agency.find_email", "agency.draft_outreach", "qa.check_draft"]
    assert not any(a.startswith("agency.send_") for a in world.ran)
    [(action, params, reason, kw)] = world.cards
    assert action == "agency.send_outreach" and params == {"outreach_id": 55}
    for shown in (URL, "office@joes.example", "A free website preview", "Hi Joe,",
                  "Reply STOP."):
        assert shown in reason, shown
    assert "<!channel>" not in reason             # scraped name shown as plain text
    ev = kw["evidence"]
    assert ev["client"].startswith("Joe's Plumbing") and "agent" not in ev
    assert ev["preview_url"] == URL and ev["to"] == "office@joes.example"
    assert kw["dedup_key"] == "agency.send_outreach:55"


def test_the_card_is_headed_websites_and_the_client():
    from atlas.kernel import board
    header = board.card_header(90, "agency.send_outreach", {"outreach_id": 55}, None,
                               {"client": "Joe's Plumbing"})
    assert header.startswith("[Websites · Joe's Plumbing] #90")


def test_a_long_email_is_cut_to_fit_the_card_and_says_so():
    text = preview_card.card_text(name="Joe", to="a@b.example", subject="S",
                                  body="word " * 600, preview_url=URL, why="w")
    assert len(text) <= preview_card.CARD_LIMIT
    assert "Details shows the whole email" in text and URL in text


async def test_no_published_address_is_no_draft_and_no_card(monkeypatch):
    world = World(monkeypatch, email=None)
    assert await lead_followup.pitch_preview(LEAD, "w", find=True) == "no_email"
    assert world.ran == ["agency.find_email"] and world.cards == []


async def test_a_draft_qa_failed_never_reaches_a_card(monkeypatch):
    world = World(monkeypatch, qa=False)
    assert await lead_followup.pitch_preview(LEAD, "w", find=True) == "qa_failed"
    assert world.cards == []


async def test_watch_only_holds_the_lead_without_a_card(monkeypatch):
    world = World(monkeypatch, hold="agency.draft_outreach")
    assert await lead_followup.pitch_preview(LEAD, "w", find=True) == "held"
    assert world.cards == []


async def test_no_live_preview_hands_the_lead_back_for_a_demo(monkeypatch):
    world = World(monkeypatch, draft_refused="refused: no live preview; deploy first")
    assert await lead_followup.pitch_preview(LEAD, "w", find=True) == "needs_demo"
    assert world.cards == []


# ------------------------------------------------------------ the tier, pinned

def test_the_send_is_tier_2_card_only_in_agency_decisions():
    from atlas.modules import agency
    spec = {a.name: a for a in agency.MODULE.actions()}["agency.send_outreach"]
    assert spec.tier == Tier.ASK and spec.card_only and spec.verbose_card
    assert spec.topic_for({}) == "agency_decisions"


# ------------------------------------------------------------ US leads only

@pytest.mark.parametrize(("lead", "us"), [
    ({"address": "12 Court St, Brooklyn, NY 11201, USA"}, True),
    ({"address": "500 Main St, Austin, TX 78701"}, True),
    ({"address": "1 King St W, Toronto, ON M5H 1A1, Canada"}, False),
    ({"address": "10 Downing St, London SW1A 2AA, UK"}, False),
    ({"address": ""}, False),
    ({}, False),
    ({"segment": "remote_us"}, True),
    ({"source": "csv"}, True),
])
def test_only_a_lead_shown_to_be_in_the_us_is_us(lead, us):
    assert verify.is_us_lead(lead) is us


async def test_a_lead_that_cannot_be_read_is_refused(monkeypatch):
    monkeypatch.setattr(_store, "get_lead", _returns(None))
    assert "no such lead" in await verify.us_refusal(7)

    async def broken(_id):
        raise OSError("db down")
    monkeypatch.setattr(_store, "get_lead", broken)
    assert "could not be read" in await verify.us_refusal(7)


# --------------------------------------------- no link before the card is approved

async def test_an_automatic_reply_gets_no_link_before_the_email_went(monkeypatch):
    from atlas.modules.agency import replies
    from atlas.modules.agency.audit import Audit
    monkeypatch.setattr(_store, "has_outreach", _returns(False))
    monkeypatch.setattr(_store, "lead_links", _returns(
        {"domain": "atlas-agency-joes-plumbing-7.vercel.app", "deployed": True}))
    monkeypatch.setattr(replies, "audit_lead", _returns(Audit(url=None, findings=[], measured={}, severity=0)))
    material = await replies._material({"lead": {"id": 7, "name": "Joe"},
                                        "message": {"body": "hi"}})
    assert "vercel.app" not in material and "do not give one" in material
    monkeypatch.setattr(_store, "has_outreach", _returns(True))
    material = await replies._material({"lead": {"id": 7, "name": "Joe"},
                                        "message": {"body": "hi"}})
    assert "https://atlas-agency-joes-plumbing-7.vercel.app" in material
