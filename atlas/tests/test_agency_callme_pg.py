"""The call-me consent, the found emails and the post-deploy claim against a
real Postgres. Skipped unless ATLAS_TEST_PG_DSN points at a throwaway
database; it TRUNCATEs the agency tables."""
from __future__ import annotations

import os
from datetime import timedelta
from types import SimpleNamespace

import pytest

from atlas.kernel import state
from atlas.modules.agency import _store, callme, consent, contacts

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
    monkeypatch.setenv(callme.KEY_ENV, "test-key")
    await _close_pool()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql(
        "TRUNCATE agency_leads, agency_lead_messages, agency_lead_consent, "
        "agency_suppression, agency_sites, agency_lead_emails, agency_outreach "
        "RESTART IDENTITY CASCADE")
    try:
        yield
    finally:
        await _close_pool()
        state.set_backend(previous)


async def _lead(place: str = "p1") -> int:
    row = SimpleNamespace(place_id=place, name="Joe's Plumbing", category="plumber",
                          address="1 Main St, Queens, NY 11101", phone="(718) 555-0101",
                          website="https://joesplumbing.example", rating=4.1, reviews=12)
    return int(await _store.insert_lead(row))


async def _site(lead_id: int, slug: str = "joes-plumbing-7", deployed: str = "now()"
                ) -> None:
    await state.execute_sql(
        f"INSERT INTO agency_sites (lead_id, slug, path, deployed_at) "
        f"VALUES ($1, $2, '/x', {deployed})", lead_id, slug)


async def test_found_emails_rank_same_domain_first_and_roll_back(pg):
    lead_id = await _lead()
    ids = await contacts.store(lead_id, [
        contacts.Found("joe.plumbing@gmail.com", "https://joesplumbing.example/", False),
        contacts.Found("office@joesplumbing.example", "https://joesplumbing.example/c",
                       True)])
    assert len(ids) == 2
    assert await contacts.store(lead_id, [contacts.Found(
        "office@joesplumbing.example", "https://joesplumbing.example/", True)]) == []
    best = await contacts.published_email(lead_id)
    assert best["address"] == "office@joesplumbing.example"
    assert best["found_url"] == "https://joesplumbing.example/c" and best["found_at"]
    await contacts.find_email_rollback(",".join(map(str, ids)))
    assert await contacts.published_email(lead_id) is None


async def test_the_form_writes_one_web_form_consent_with_its_evidence(pg):
    lead_id = await _lead()
    await _site(lead_id)
    sub = await callme.submit("joes-plumbing-7", name="Joe", phone="+17185550101",
                              page_url="https://agency.storefront.test/preview/joes-plumbing-7/",
                              ip="203.0.113.9", user_agent="UA/1",
                              version=callme.CURRENT_VERSION)
    assert sub.recorded and sub.listed
    row = await _store.consent_for_lead(lead_id, "call")
    assert row["source_channel"] == "web_form" and row["quoted"] == callme.CONSENT_TEXT
    evidence = _store.parse_json(row["evidence"], {})
    assert evidence["page_url"].endswith("/preview/joes-plumbing-7/") and evidence["user_agent"] == "UA/1"
    assert evidence["consent_text"] == callme.CONSENT_TEXT
    assert "203.0.113.9" not in str(evidence)
    # A second submission reuses the live consent, and the message is kept once.
    again = await callme.submit("joes-plumbing-7", name="Joe", phone="+17185550101",
                                page_url="https://agency.storefront.test/preview/joes-plumbing-7/",
                                ip="203.0.113.9", user_agent="UA/1",
                                version=callme.CURRENT_VERSION)
    assert again.consent_id == sub.consent_id
    n = await state.fetch_one("SELECT count(*) AS n FROM agency_lead_consent")
    assert n["n"] == 1
    # The lead just wrote to us: the contact cap starts again.
    assert await _store.contacts_since_last_word(lead_id) == 0
    pending = await _store.consents_awaiting_call_card(("web_form",), timedelta(days=3))
    assert [r["id"] for r in pending] == [sub.consent_id]
    assert await _store.mark_call_card(sub.consent_id, "card #1")
    assert not await _store.mark_call_card(sub.consent_id, "card #2")
    assert await _store.consents_awaiting_call_card(("web_form",), timedelta(days=3)) == []


async def test_a_suppressed_number_is_not_re_consented_by_the_form(pg):
    lead_id = await _lead()
    await _site(lead_id)
    await consent.opt_out(lead_id, phone="+17185550199", why="STOP")
    sub = await callme.submit("joes-plumbing-7", name="Joe", phone="+17185550199",
                              page_url="https://agency.storefront.test/preview/joes-plumbing-7/",
                              ip="203.0.113.9", user_agent="UA/1",
                              version=callme.CURRENT_VERSION)
    assert not sub.recorded and sub.why == "suppressed"
    assert await _store.consent_for_lead(lead_id, "call") is None


async def test_an_undeployed_site_takes_no_consent(pg):
    lead_id = await _lead()
    await _site(lead_id, deployed="NULL")
    with pytest.raises(callme.NotHere):
        await callme.site_lead("joes-plumbing-7")


async def test_a_deployed_site_is_followed_up_once_and_only_when_fresh(pg):
    lead_id = await _lead()
    other = await _lead("p2")
    await _site(lead_id)
    await _site(other, "old-site-9", deployed="now() - interval '5 days'")
    claimed = await _store.claim_deployed_sites(timedelta(days=1))
    assert [s["slug"] for s in claimed] == ["joes-plumbing-7"]
    assert await _store.claim_deployed_sites(timedelta(days=1)) == []
    await _store.release_site_followup("joes-plumbing-7")
    assert len(await _store.claim_deployed_sites(timedelta(days=1))) == 1
    assert not await _store.has_outreach(lead_id)
    oid = await _store.insert_outreach(lead_id, "email", None, "s", "b", None)
    # An old unsent draft (every draft before today had no recipient) is not
    # a pitch the lead saw: the follow-up still goes ahead.
    assert not await _store.has_outreach(lead_id)
    await _store.mark_outreach_sent(oid, "<m@x>")
    assert await _store.has_outreach(lead_id)


async def test_the_agreement_can_go_to_the_published_address(pg):
    """A lead known only from the form: `lead_email` falls back to the address
    the business publishes, so an agreed price can still be sent (on a card)."""
    lead_id = await _lead()
    assert await _store.lead_email(lead_id) is None
    await contacts.store(lead_id, [contacts.Found(
        "office@joesplumbing.example", "https://joesplumbing.example/", True)])
    assert await _store.lead_email(lead_id) == "office@joesplumbing.example"
