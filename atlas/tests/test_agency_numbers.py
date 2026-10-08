"""The numbers a lead is given, and the links outreach carries (2026-09-30).

Mapfront's line (TWILIO_FROM, answered by the AI) goes in every outreach
email and reply while RECEPTION_ENABLED is on; Miro's own number
(AGENCY_ESCALATION_PHONE, else TWILIO_TO) only in replies, never in a first
cold email. Every number here is a fake 555 one.
"""
from __future__ import annotations

import pytest

from atlas.kernel.llm import Completion
from atlas.modules.agency import _store, contacts, outreach, pricing, replies
from atlas.modules.agency.audit import Audit

LINE = "+17185550000"
MIRO = "+19175550199"
LEAD = {"id": 7, "name": "Joe's Plumbing", "category": "plumber",
        "website": "https://joesplumbing.example", "phone": "(718) 555-0101"}
LIVE = {"domain": None, "slug": "joes-plumbing-7", "deployed": True,
        "webild_url": None, "mockup": None}


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    from atlas.modules.agency import verify
    monkeypatch.setattr(verify, "contact_refusal", _returns(None))
    monkeypatch.setenv("RECEPTION_ENABLED", "1")
    monkeypatch.setenv("TWILIO_FROM", LINE)
    monkeypatch.setenv("TWILIO_TO", MIRO)
    monkeypatch.delenv("AGENCY_ESCALATION_PHONE", raising=False)
    monkeypatch.delenv("AGENCY_SITES_DOMAIN", raising=False)
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "1 Test Ave, Staten Island, NY 10312")
    for name in ("AGENCY_PRICE_LIST", "AGENCY_PRICE_FLOOR", "AGENCY_FIRST_SALE_PRICE",
                 "AGENCY_AI_CHAT_BUILT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(("raw", "shown"), [
    ("+17185550000", "(718) 555-0000"), ("7185550000", "(718) 555-0000"),
    ("+442071234567", "+442071234567"), ("12", "12")])
def test_pretty_phone(raw, shown):
    assert outreach.pretty_phone(raw) == shown


def test_lines_name_mapfront_and_only_while_the_line_answers(monkeypatch):
    assert outreach.callback_line() == "Questions? Call or text Mapfront at (718) 555-0000."
    assert outreach.more_info_line() == (
        "For more information, call or text Mapfront at (718) 555-0000; our AI "
        "assistant answers any time.")
    monkeypatch.delenv("RECEPTION_ENABLED")
    assert outreach.callback_line() == outreach.more_info_line() == ""


def test_the_escalation_number_prefers_its_own_setting(monkeypatch):
    assert outreach.escalation_line() == "To speak to Miro directly, call (917) 555-0199."
    monkeypatch.setenv("AGENCY_ESCALATION_PHONE", "+12125550123")
    assert outreach.escalation_phone() == "(212) 555-0123"
    monkeypatch.delenv("AGENCY_ESCALATION_PHONE")
    monkeypatch.delenv("TWILIO_TO")
    assert outreach.escalation_line() == ""


def test_links_only_once_live():
    assert outreach.site_lines({**LIVE, "deployed": False}) == []
    assert outreach.site_lines(LIVE) == [
        "See the site we built for you: https://joes-plumbing-7.mapfront.io/",
        ("Rather talk it through? Have us call you: "
         "https://joes-plumbing-7.mapfront.io/#call-me")]


def _outreach_fakes(monkeypatch, *, email=None, links=LIVE):
    seen: dict = {}
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(_store, "latest_mockup_path", _returns("data/x/mockup.html"))
    monkeypatch.setattr(_store, "latest_sent_outreach_id", _returns(None))
    monkeypatch.setattr(_store, "lead_links", _returns(links))
    monkeypatch.setattr(contacts, "published_email", _returns(email))
    monkeypatch.setattr(outreach, "audit_lead", _returns(Audit(None, [], {}, 0)))

    async def think(need, system, messages, max_tokens=800):
        seen["user"] = messages[0].content
        return Completion("Your site takes 11 seconds on a phone.", "t", "m")
    monkeypatch.setattr(outreach, "think", think)

    async def insert(lead_id, kind, to_address, subject, body, followup_of,
                     body_sha=None):
        seen.update(to=to_address, body=body, subject=subject)
        return 5
    monkeypatch.setattr(_store, "insert_outreach", insert)
    return seen


async def test_outreach_carries_the_preview_the_form_and_mapfronts_number(monkeypatch):
    seen = _outreach_fakes(monkeypatch, email={"address": "office@joesplumbing.example"})
    result = await outreach.draft_outreach(7, "email")
    assert result.ok
    body = seen["body"]
    assert "https://joes-plumbing-7.mapfront.io/" in body
    assert "https://joes-plumbing-7.mapfront.io/#call-me" in body
    assert "call Mapfront at (718) 555-0000" in body
    assert "1 Test Ave, Staten Island, NY 10312" in body and "Reply STOP" in body
    assert outreach.can_spam_ok(body, "1 Test Ave, Staten Island, NY 10312") == []
    # Miro's own number is for escalation only, never a cold email.
    assert "555-0199" not in body
    assert seen["to"] == "office@joesplumbing.example"
    assert result.evidence["to"] == "office@joesplumbing.example"
    # The email carries a public URL, never a path on this box (and no
    # model wrote it: `seen["user"]` is never set for the email kind).
    assert "data/x/mockup.html" not in body and "user" not in seen


async def test_outreach_with_no_published_email_has_no_recipient(monkeypatch):
    seen = _outreach_fakes(monkeypatch, email=None)
    assert (await outreach.draft_outreach(7, "email")).ok
    assert seen["to"] is None


async def test_a_reply_always_gives_both_numbers(monkeypatch):
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(_store, "latest_lead_message", _returns(
        {"id": 11, "channel": "email", "body": "What's included?", "subject": "About"}))
    monkeypatch.setattr(_store, "lead_message_bodies", _returns(["What's included?"]))
    monkeypatch.setattr(_store, "lead_email", _returns("joe@example.com"))
    monkeypatch.setattr(_store, "is_suppressed", _returns(False))
    monkeypatch.setattr(_store, "count_closed_deals", _returns(0))
    monkeypatch.setattr(_store, "lead_links", _returns(LIVE))
    monkeypatch.setattr(_store, "insert_reply_draft", _returns(21))
    monkeypatch.setattr(replies, "audit_lead", _returns(Audit(None, [], {}, 0)))
    monkeypatch.setattr(replies, "think", _returns(
        Completion("It's $500: five pages, SEO basics and a call bar.", "t", "m")))
    result = await replies.draft_reply(7, "email")
    assert result.ok, result.detail
    body = result.evidence["text"]
    assert ("For more information, call or text Mapfront at (718) 555-0000; our AI "
            "assistant answers any time.") in body
    assert "To speak to Miro directly, call (917) 555-0199." in body
    assert body.rstrip().endswith(outreach.TRAILER)
    # The numbers are not read as prices.
    assert set(pricing.find_prices(body)) == {500}
    assert replies.refusal(body, pricing.Terms(500, 300, 100, 0), objection=False) is None

