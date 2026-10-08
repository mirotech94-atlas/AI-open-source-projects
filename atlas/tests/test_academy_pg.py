"""ATLAS Academy purchases against a real Postgres: the schema applies twice,
a session is recorded once (and posted once), and later reads update its
status.

Skipped unless ATLAS_TEST_PG_DSN points at a throwaway database. It deletes
its own test sessions from academy_purchases, nothing else.
"""
from __future__ import annotations

import os

import pytest

from atlas.kernel import state
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import delivery, stripe_read

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ATLAS_TEST_PG_DSN to a throwaway Postgres to run")

SID = "cs_test_pgAcademyTest0001"


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
    await cli.init_db()                     # idempotent: a second run changes nothing
    await state.execute_sql("DELETE FROM academy_purchases WHERE session_id = $1", SID)
    posted: list = []

    async def notify(msg):
        posted.append(msg)
        return ["slack"]
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    try:
        yield posted
    finally:
        await state.execute_sql("DELETE FROM academy_purchases WHERE session_id = $1",
                                SID)
        await _close_pool()
        state.set_backend(previous)


def _verdict(status: str, refunded: int = 0) -> stripe_read.Verdict:
    cat = cat_mod.load()
    return stripe_read.Verdict(status, SID, offers=(cat.offers()[1],),
                               price_ids=("price_TEST123456",), amount_minor=900,
                               currency="usd", refunded_minor=refunded,
                               email="buyer@example.com", payment_intent="pi_1",
                               livemode=False)


async def test_a_purchase_is_recorded_once_and_its_status_follows_stripe(pg):
    assert await delivery.record(_verdict("paid"), "access") is True
    assert await delivery.record(_verdict("paid"), "webhook") is False
    assert len(pg) == 1
    row = await state.fetch_one("SELECT * FROM academy_purchases WHERE session_id = $1",
                                SID)
    assert row["status"] == "paid" and row["amount_minor"] == 900
    assert list(row["offers"]) == ["30-business-lessons"]
    assert row["source"] == "access" and row["livemode"] is False

    assert await delivery.record(_verdict("refunded", 900), "access") is False
    row = await state.fetch_one("SELECT status, refunded_minor FROM academy_purchases "
                                "WHERE session_id = $1", SID)
    assert row["status"] == "refunded" and row["refunded_minor"] == 900
    assert len(pg) == 1


async def test_an_unpaid_checkout_is_never_recorded(pg):
    assert await delivery.record(_verdict("unpaid"), "access") is False
    row = await state.fetch_one("SELECT 1 FROM academy_purchases WHERE session_id = $1",
                                SID)
    assert row is None and not pg
