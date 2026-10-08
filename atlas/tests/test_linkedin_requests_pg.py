"""LinkedIn page requests against a real Postgres: the schema applies twice,
an item is recorded once, a replied thread reopens on new words, a held one
too, a suppressed one never; the claim is atomic; the counts read back.

Skipped unless ATLAS_TEST_PG_DSN points at a throwaway database. It deletes
its own rows (external keys under TEST_KEY), nothing else.
"""
from __future__ import annotations

import os

import pytest

from atlas.kernel import state
from atlas.modules.linkedin import requests as lr

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ATLAS_TEST_PG_DSN to a throwaway Postgres to run")

TEST_KEY = "https://www.linkedin.com/company/143909038/admin/inbox/thread/pgtest-"


async def _close_pool() -> None:
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()


async def _clean() -> None:
    await state.execute_sql(
        """DELETE FROM linkedin_request_drafts WHERE request_id IN
             (SELECT id FROM linkedin_requests WHERE external_key LIKE $1)""",
        TEST_KEY + "%")
    await state.execute_sql("DELETE FROM linkedin_requests WHERE external_key LIKE $1",
                            TEST_KEY + "%")


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    await _close_pool()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await cli.init_db()                     # idempotent: a second run changes nothing
    await _clean()
    try:
        yield
    finally:
        await _clean()
        await _close_pool()
        state.set_backend(previous)


def item(n: int, words: str) -> lr.Item:
    key = f"{TEST_KEY}{n}/"
    return lr.Item(page="guardian", source="page_message", surface="guardian-inbox",
                   external_key=key, requester="Sam Ortiz", words=words, url=key)


async def test_an_item_is_recorded_once_and_reopens_only_on_new_words(pg):
    first = await lr.upsert_seen(item(1, "Do you monitor certificates?"),
                                 "network_monitoring", "keyword", "", "")
    assert first["inserted"] is True and first["status"] == "new"
    again = await lr.upsert_seen(item(1, "Do you monitor certificates?"),
                                 "other", "none", "", "")
    assert again["inserted"] is False and again["id"] == first["id"]
    row = await lr.get_request(first["id"])
    assert row["label"] == "network_monitoring", "an update keeps the first label"

    rid = int(first["id"])
    await lr.mark_replied(rid, row["inbound_sha"])
    same = await lr.upsert_seen(item(1, "Do you monitor certificates?"), "other",
                                "none", "", "")
    assert same["status"] == "replied", "no new words, no new reply"
    newer = await lr.upsert_seen(item(1, "And firewalls?"), "other", "none", "", "")
    assert newer["status"] == "new", "they wrote again: one reply per inbound"

    await lr.set_status(rid, "held", "card raised")
    still = await lr.upsert_seen(item(1, "And firewalls?"), "other", "none", "", "")
    assert still["status"] == "held"
    reopened = await lr.upsert_seen(item(1, "Hello?"), "other", "none", "", "")
    assert reopened["status"] == "new", "a held thread reopens on new words"

    await lr.set_status(rid, "suppressed", "rolled back")
    await lr.set_status(rid, "new")
    assert (await lr.get_request(rid))["status"] == "suppressed", "suppressed is final"
    gone = await lr.upsert_seen(item(1, "Anyone?"), "other", "none", "", "")
    assert gone["status"] == "suppressed"


async def test_the_claim_is_atomic_and_the_page_can_close_a_thread(pg):
    rid = int((await lr.upsert_seen(item(2, "Can you set up our wifi?"),
                                    "computer_networking", "keyword", "", ""))["id"])
    assert await lr.claim(rid) is True
    assert await lr.claim(rid) is False, "a second driver cannot take it"
    await lr.release(rid)
    assert await lr.claim(rid) is True
    assert rid in {int(r["id"]) for r in await lr.awaiting_reply(50)}

    ours = item(2, "You: Yes. Miro will follow up here.")
    await lr.mark_answered_on_page(ours)
    row = await lr.get_request(rid)
    assert row["status"] == "replied" and row["replied_sha"] == ours.inbound_sha
    assert row["claimed_at"] is None


async def test_drafts_sends_and_counts(pg):
    rid = int((await lr.upsert_seen(item(3, "We had a phishing incident"),
                                    "cybersecurity", "keyword", "", ""))["id"])
    row = await lr.get_request(rid)
    body = "Thanks for writing." + lr.signoff("guardian")
    did = await lr.insert_draft(rid, row["inbound_sha"], body)
    assert (await lr.latest_draft(rid))["id"] == did
    before = await lr.sent_today("guardian")
    assert await lr.sent_in_thread(rid) == []
    await lr.mark_send_attempt(did)
    await lr.set_draft_status(did, "sent")
    assert await lr.sent_today("guardian") == before + 1
    [sent] = await lr.sent_in_thread(rid)
    assert sent["body"] == body and sent["today"] is True
    assert body in await lr.our_sent_bodies()
    assert (await lr.get_draft(did))["sent_at"] is not None
    assert any(r["id"] == rid for r in await lr.open_requests())
    counts = await lr.demand()
    assert counts["pages"]["guardian"]["30d"]["cybersecurity"] >= 1
    assert counts["read_at"]
