"""kernel.occurrence's missed-run SQL against a real Postgres (RA-F2).

The in-suite tests (test_missed_runs.py) use an in-memory schedule_runs; the
once-only rules themselves live in SQL, so they are proven here against the
schema `init-db` applies. Skipped unless ATLAS_TEST_PG_DSN points at a
throwaway database (see test_durable_pg.py for the docker line). It
TRUNCATEs schedule_runs.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import missed, occurrence, state
from atlas.kernel.missed import JobPolicy, Missed

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ATLAS_TEST_PG_DSN to a throwaway Postgres to run")


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql("TRUNCATE schedule_runs")
    missed.forget()
    try:
        yield
    finally:
        state.set_backend(previous)
        pool, state._pool = state._pool, None
        if pool is not None:
            await pool.close()


async def _row(job: str, window: str):
    return await state.fetch_one(
        """SELECT status, attempts, scheduled_for, detail FROM schedule_runs
            WHERE job = $1 AND occurrence = $2""", job, window)


async def test_claim_once_is_never_taken_again(pg):
    t = datetime(2026, 9, 27, 7, tzinfo=UTC)
    assert await occurrence.claim_once("marketing", "2026-09-27", t)
    assert not await occurrence.claim_once("marketing", "2026-09-27", t)
    await occurrence.fail("marketing", "2026-09-27", "perplexity down")
    assert not await occurrence.claim_once("marketing", "2026-09-27", t)
    # ...and the retrying claim does not take it back from a failed at-most-
    # once job either: claim() re-takes 'failed', so a claim_once job must
    # never be claimed with claim(). The row keeps what happened.
    row = await _row("marketing", "2026-09-27")
    assert row["status"] == "failed" and row["detail"] == "perplexity down"
    assert row["scheduled_for"] == t


async def test_skip_records_a_missed_window_and_never_overwrites_a_run(pg):
    t = datetime(2026, 9, 26, 7, tzinfo=UTC)
    assert await occurrence.skip("brief", "2026-09-26", t, "missed: 27h late")
    row = await _row("brief", "2026-09-26")
    assert row["status"] == "skipped" and row["attempts"] == 0
    # A window that ran is left alone.
    assert await occurrence.claim("brief", "2026-09-27", t + timedelta(days=1))
    await occurrence.done("brief", "2026-09-27")
    assert await occurrence.skip("brief", "2026-09-27", t, "late")
    assert (await _row("brief", "2026-09-27"))["status"] == "done"
    # A failed one past its grace becomes skipped.
    assert await occurrence.claim("brief", "2026-09-28", t + timedelta(days=2))
    await occurrence.release("brief", "2026-09-28")
    assert await occurrence.skip("brief", "2026-09-28", t, "past grace")
    row = await _row("brief", "2026-09-28")
    assert row["status"] == "skipped" and row["detail"] == "past grace"
    # A skipped window is not claimable: it never runs late.
    assert not await occurrence.claim("brief", "2026-09-26", t)


async def test_history_reads_the_windows_and_whether_any_exist(pg):
    assert await occurrence.history("weekly", ["2026-W40"]) == ({}, False)
    await occurrence.claim_once("weekly", "2026-W39")
    rows, has = await occurrence.history("weekly", ["2026-W39", "2026-W40"])
    assert has and set(rows) == {"2026-W39"} and rows["2026-W39"]["status"] == "running"


async def test_due_end_to_end_after_an_outage(pg):
    p = JobPolicy("probe", Missed.SKIP_STALE, why="t",
                  windows=missed.daily(lambda: 7))
    await occurrence.claim("probe", "2026-09-24")
    await occurrence.done("probe", "2026-09-24")
    now = datetime(2026, 9, 28, 10, tzinfo=UTC)
    w = await missed.due(p, now)
    assert w is not None and w.key == "2026-09-28"
    rows = await state.fetch_all(
        """SELECT occurrence, status FROM schedule_runs WHERE job = 'probe'
            ORDER BY scheduled_for NULLS FIRST, occurrence""")
    assert [(r["occurrence"], r["status"]) for r in rows] == [
        ("2026-09-24", "done"), ("2026-09-25", "skipped"),
        ("2026-09-26", "skipped"), ("2026-09-27", "skipped")]
