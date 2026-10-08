"""The site chat's spend ledger against a real Postgres: the month's sum, the
per-site day count, reserve-then-settle, and the cap refusing under the lock.

Skipped unless ATLAS_TEST_PG_DSN points at a throwaway database. It
TRUNCATEs site_chat_usage. Never point it at a database whose rows matter.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import pytest

from atlas.kernel import llm, state
from atlas.kernel.llm import Completion, Need
from atlas.modules.agency import sitechat

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ATLAS_TEST_PG_DSN to a throwaway Postgres to run")

FACTS = sitechat.Facts(
    slug="harbor-dental-7", name="Harbor Dental",
    origins=("https://harbor-dental-7.mapfront.io",), phone="(207) 555-0100",
    lines=("Business name: Harbor Dental", "Phone: (207) 555-0100"))


async def _close_pool() -> None:
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.delenv(sitechat.CAP_ENV, raising=False)
    monkeypatch.delenv(sitechat.SWITCH_ENV, raising=False)
    monkeypatch.setattr(llm, "ROUTES", {Need.TRIVIAL: [object()]})
    await _close_pool()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql("TRUNCATE site_chat_usage RESTART IDENTITY")
    try:
        yield
    finally:
        await _close_pool()
        state.set_backend(previous)


async def test_the_month_and_the_day_are_read_from_the_table(pg):
    assert await sitechat.spent_this_month() == Decimal(0)
    await sitechat.book("harbor-dental-7", Decimal("0.25"), "answered")
    await sitechat.book("harbor-dental-7", Decimal("0.5"), "answered")
    await sitechat.book("other-3", Decimal(1), "answered")
    await state.execute_sql(
        "INSERT INTO site_chat_usage (at, slug, usd, outcome) "
        "VALUES (now() - interval '40 days', 'harbor-dental-7', 9, 'answered')")
    assert await sitechat.spent_this_month() == Decimal("1.75")
    assert await sitechat.requests_today("harbor-dental-7") == 2
    assert await sitechat.requests_today("other-3") == 1


async def test_a_question_is_reserved_then_settled_and_no_words_are_kept(
        pg, monkeypatch):
    async def think(need, system, messages, tools=None, max_tokens=2048, *, model=None):
        return Completion("Call (207) 555-0100.", "anthropic", "claude-haiku-4-5",
                          1000, 20)
    monkeypatch.setattr(sitechat, "think", think)
    reply = await sitechat.converse(FACTS, "my private question about molars", [])
    assert reply.outcome == "answered"
    row = await state.fetch_one("SELECT * FROM site_chat_usage")
    assert row["outcome"] == "answered" and row["slug"] == "harbor-dental-7"
    assert Decimal(str(row["usd"])) == sitechat.price(1000, 20)
    assert row["input_tokens"] == 1000 and row["provider"] == "anthropic"
    assert "molars" not in " ".join(str(v) for v in dict(row).values())


async def test_concurrent_questions_never_pass_the_cap_together(pg, monkeypatch):
    monkeypatch.setenv(sitechat.CAP_ENV, "0.02")
    async def think(need, system, messages, tools=None, max_tokens=2048, *, model=None):
        await asyncio.sleep(0.05)          # all six are in flight at once
        return Completion("Call (207) 555-0100.", "x", "y", 0, 0)   # tokens unknown
    monkeypatch.setattr(sitechat, "think", think)
    replies = await asyncio.gather(*(sitechat.converse(FACTS, "phone?", [])
                                     for _ in range(6)))
    answered = [r for r in replies if r.status == 200]
    rested = [r for r in replies if r.status == 503]
    assert answered and rested
    # Unknown tokens stay booked at worst case: over-counted, never under.
    assert await sitechat.spent_this_month() <= Decimal("0.02")
