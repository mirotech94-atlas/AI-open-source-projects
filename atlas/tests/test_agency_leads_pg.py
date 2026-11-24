"""The lead follow-up SQL against a real Postgres: consent, contacts, deals,
the reply match and the board. Skipped unless ATLAS_TEST_PG_DSN points at a
throwaway database (see tests/test_durable_pg.py). It TRUNCATEs the agency
tables."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from atlas.kernel import state
from atlas.modules.agency import _store, consent, pricing

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ATLAS_TEST_PG_DSN to a throwaway Postgres to run")


async def _close_pool() -> None:
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    await _close_pool()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql(
        "TRUNCATE agency_leads, agency_outreach, agency_lead_messages, "
        "agency_lead_consent, agency_contacts, agency_reply_drafts, agency_deals, "
        "agency_suppression, agency_verifications, agency_lead_meetings, "
        "perplexity_calls, agency_payments, agency_stripe_events "
        "RESTART IDENTITY CASCADE")
    try:
        yield
    finally:
        await _close_pool()
        state.set_backend(previous)


async def _lead(place: str = "p1", phone: str = "(718) 555-0101") -> int:
    place_row = SimpleNamespace(place_id=place, name="Joe's Plumbing", category="plumber",
                                address="1 Main St", phone=phone, website=None,
                                rating=4.1, reviews=12)
    return int(await _store.insert_lead(place_row))


async def test_consent_lifecycle(pg):
    lead_id = await _lead()
    grants = consent.consent_from_email("Call me at 718-555-0101 please")
    assert await consent.record(lead_id, grants, "email", "<m1@x>") == ["call"]
    assert await consent.record(lead_id, grants, "email", "<m1@x>") == []   # live already
    row = await _store.consent_for_lead(lead_id, "call")
    assert row["phone"] == "+17185550101" and "718-555-0101" in row["quoted"]
    assert (await _store.lead_for_phone("+17185550101"))["id"] == lead_id

    await consent.opt_out(lead_id, why="STOP")
    assert await _store.consent_for_lead(lead_id, "call") is None
    assert await _store.is_suppressed("tel:+17185550101")
    # an opt-out is not undone by a later message
    assert await consent.record(lead_id, grants, "email", "<m2@x>") == []


async def test_the_contact_cap_counts_since_their_last_word(pg):
    lead_id = await _lead()
    assert await _store.contacts_since_last_word(lead_id) == 0
    await _store.insert_contact(lead_id, "sms", "+17185550101", "Hi", "Hi")
    cid = await _store.insert_contact(lead_id, "call", "+17185550101", "Hi", "")
    assert await _store.contacts_since_last_word(lead_id) == 2
    await _store.set_contact(cid, status="answered", ended=True)
    assert await _store.contacts_since_last_word(lead_id) == 1
    await _store.insert_lead_message(lead_id, "sms", "SM1", "+17185550101", "", "hi")
    assert await _store.contacts_since_last_word(lead_id) == 0


async def test_messages_are_taken_in_once(pg):
    lead_id = await _lead()
    first = await _store.insert_lead_message(lead_id, "email", "<a@x>", "j@x", "", "hi")
    again = await _store.insert_lead_message(lead_id, "email", "<a@x>", "j@x", "", "hi")
    assert first is not None and again is None
    assert await _store.lead_message_exists("email", "<a@x>")


async def test_a_queued_reply_is_claimed_once(pg):
    lead_id = await _lead()
    mid = await _store.insert_lead_message(lead_id, "sms", "SM9", "+1", "", "how much")
    assert await _store.messages_awaiting_reply(5) == []
    await _store.queue_reply(mid, "sms")
    assert [r["id"] for r in await _store.messages_awaiting_reply(5)] == [mid]
    assert await _store.claim_reply(mid)
    assert not await _store.claim_reply(mid)
    assert await _store.messages_awaiting_reply(5) == []
    await _store.release_reply(mid)
    assert [r["reply_channel"] for r in await _store.messages_awaiting_reply(5)] == ["sms"]


async def test_a_reply_matches_our_outreach(pg):
    lead_id = await _lead()
    oid = await _store.insert_outreach(lead_id, "email", "Joe@Example.com", "About",
                                       "body", None)
    await _store.mark_outreach_sent(oid, "<ours@send.example>")
    by_id = await _store.outreach_for_reply(["<ours@send.example>"], "other@x")
    by_addr = await _store.outreach_for_reply([], "joe@example.com")
    assert by_id["id"] == oid and by_addr["id"] == oid
    assert await _store.outreach_for_reply(["<nope@x>"], "stranger@x") is None
    await _store.mark_outreach_replied(oid)
    assert (await _store.get_outreach(oid))["status"] == "replied"


async def test_deals_close_the_first_sale_price(pg):
    lead_id = await _lead()
    # The first-sale price is retired (500 new / 300 returning): a paid deal
    # makes the lead returning, a lost one does not.
    assert (await pricing.terms(lead_id)).returning is False
    deal = await _store.insert_deal(lead_id, "lost", None, "")
    assert (await pricing.terms(lead_id)).returning is False   # lost is not a sale
    await _store.insert_deal(lead_id, "paid", 300, "")
    terms = await pricing.terms(lead_id)
    assert terms.returning is True and terms.price == terms.floor
    await _store.undo_deal(deal)
    assert await _store.count_closed_deals() == 1


async def test_reply_drafts_and_the_board(pg):
    lead_id = await _lead()
    did = await _store.insert_reply_draft(lead_id, None, "reply", "email", "j@x",
                                          "Re: x", "It's $500.", [500])
    assert (await _store.get_reply_draft(did))["status"] == "draft"
    assert await _store.mark_reply_sent(did)
    assert not await _store.mark_reply_sent(did)
    await _store.discard_reply_draft(did)                 # a sent one stays
    assert await _store.get_reply_draft(did) is not None
    mid = await _store.insert_lead_message(lead_id, "email", "<b@x>", "j@x", "", "how much?")
    await _store.set_message_intent(mid, "price")
    board = await _store.lead_board()
    assert board["leads"] == {"new": 1} and board["replies_24h"] == {"price": 1}
    links = await _store.lead_links(lead_id)
    assert links == {"domain": None, "slug": None, "deployed": False, "webild_url": None,
                     "mockup": None}


async def test_verifications_and_the_contact_gate(pg):
    from datetime import UTC, datetime

    from atlas.modules.agency import verify
    lead_id = await _lead()
    assert "has not been verified" in await verify.contact_refusal(lead_id)
    vid = await _store.insert_verification(
        lead_id, "disqualified", ["a chain"], {"chain": True}, "<wrapped>",
        ["https://x.example"], datetime.now(UTC), "new")
    assert "a chain" in await verify.contact_refusal(lead_id)
    assert await _store.verdicts_for([lead_id]) == {lead_id: "disqualified"}
    await _store.withdraw_verification(vid)
    assert await _store.latest_verification(lead_id) is None
    await _store.insert_verification(lead_id, "verified", [], {}, "", [], None, "new")
    assert await verify.contact_refusal(lead_id) is None
    await state.execute_sql(
        "INSERT INTO perplexity_calls (tool, caller) VALUES "
        "('perplexity_ask', 'agency-verify'), ('perplexity_ask', 'agency')")
    assert await _store.verify_calls_since(datetime(2000, 1, 1, tzinfo=UTC)) == 1


async def test_meetings_are_reminded_once(pg):
    from datetime import UTC, datetime, timedelta
    lead_id = await _lead()
    soon = datetime.now(UTC) + timedelta(minutes=40)
    later = datetime.now(UTC) + timedelta(hours=5)
    mid = await _store.insert_meeting(lead_id, "evt1", soon, soon + timedelta(minutes=30),
                                      "j@x", "CA1")
    await _store.insert_meeting(lead_id, "evt2", later, later + timedelta(minutes=30),
                                None, None)
    due = await _store.claim_reminders(timedelta(hours=1))
    assert [d["event_id"] for d in due] == ["evt1"] and due[0]["lead_name"]
    assert await _store.claim_reminders(timedelta(hours=1)) == []
    assert await _store.mark_meeting_invited(mid)
    assert not await _store.mark_meeting_invited(mid)
    await _store.release_meeting("evt2")
    assert (await _store.get_meeting(mid + 1))["status"] == "released"


async def test_places_business_status_is_kept(pg):
    place = SimpleNamespace(place_id="p9", name="Closed Co", category="bakery",
                            address="1 Main St, Brooklyn, NY 11201", phone=None,
                            website=None, rating=None, reviews=None,
                            business_status="CLOSED_PERMANENTLY")
    lead_id = await _store.insert_lead(place)
    assert (await _store.get_lead(lead_id))["business_status"] == "CLOSED_PERMANENTLY"


async def test_stripe_events_payments_and_the_first_sale_price(pg):
    lead_id = await _lead()
    await _store.insert_outreach(lead_id, "email", "Joe@Example.com", "s", "b", None)
    assert await _store.lead_id_for_email("joe@example.com") == lead_id
    assert await _store.lead_id_for_email("nobody@example.com") is None
    assert await _store.claim_stripe_event("evt_1", "invoice.paid")
    assert not await _store.claim_stripe_event("evt_1", "invoice.paid")
    await _store.release_stripe_event("evt_1")
    assert await _store.claim_stripe_event("evt_1", "invoice.paid")

    assert (await pricing.terms(lead_id)).returning is False
    # An unmatched payment names no lead, so it makes nobody returning.
    await _store.insert_payment(event_id="evt_0", source="invoice", object_id="in_0",
                                matched_by="none", amount_minor=10000, currency="usd")
    assert (await pricing.terms(lead_id)).returning is False
    await state.execute_sql("TRUNCATE agency_payments")
    deal = await _store.insert_paid_deal(lead_id, 50050, "usd", "stripe evt_1")
    pid = await _store.insert_payment(
        event_id="evt_1", source="checkout", object_id="cs_1", payment_intent="pi_1",
        lead_id=lead_id, matched_by="reference", amount_minor=50050,
        currency="usd", deal_id=deal)
    assert (await pricing.terms(lead_id)).returning is True   # a paid deal
    found = await _store.payment_for("pi_1", None)
    assert found["id"] == pid and found["lead_name"] == "Joe's Plumbing"
    assert found["amount_minor"] == 50050
    await _store.set_payment_status(pid, "refunded", 50050)
    await _store.set_deal_status(deal, "refunded")
    assert (await pricing.terms(lead_id)).returning is False   # refunded: not a sale
