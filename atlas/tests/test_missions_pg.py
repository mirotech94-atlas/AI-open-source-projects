"""The missions SQL (`kernel/missions.py`) against a real Postgres, on the
schema `init-db` applies: the row a mission is, its lease, the question that
waits for an answer, and `lead_facts` -- the read every next step and every
count in the report come from.

Skipped unless ATLAS_TEST_PG_DSN points at a throwaway database
(tests/acceptance_support.py). It TRUNCATEs `missions`; the agency rows it
needs it inserts under its own place ids and leaves the rest alone.
"""
from __future__ import annotations

import json
import uuid

import pytest

from atlas.kernel import missions, state
from tests.acceptance_support import DSN, needs_pg

pytestmark = needs_pg


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql("TRUNCATE missions")
    try:
        yield
    finally:
        state.set_backend(previous)
        pool, state._pool = state._pool, None
        if pool is not None:
            await pool.close()


async def _lead(website: str | None) -> int:
    row = await state.fetch_one(
        "INSERT INTO agency_leads (place_id, name, category, website) "
        "VALUES ($1, $2, 'plumber', $3) RETURNING id",
        f"test-missions-{uuid.uuid4()}", "Joe's Plumbing", website)
    return int(row["id"])


async def test_a_mission_row_round_trips_and_is_leased_once(pg):
    mid = await missions._insert_mission(
        agent="agency", recipe=missions.AGENCY_SITES, request="find 5 plumbers",
        channel="voice", who_="dashboard", thread="", slots={"n": 5, "area": "Astoria"},
        status="running")
    first = await missions._claim(mid)
    assert first is not None and first["slots"] == {"n": 5, "area": "Astoria"}
    assert first["deadline"] is not None
    # A second pass while the first holds the lease gets nothing.
    assert await missions._claim(mid) is None
    first["state"] = {"lead_ids": [1, 2]}
    first["steps"] = [{"action": "agency.rank", "ok": True}]
    first["status"], first["phase"] = "waiting", "search"
    await missions._save(first)
    await missions._release(mid)
    again = await missions._claim(mid)
    assert again["state"] == {"lead_ids": [1, 2]} and again["phase"] == "search"
    assert again["steps"][0]["action"] == "agency.rank"
    assert await missions._live_ids() == [mid]
    again["status"] = "done"
    await missions._save(again)
    row = await state.fetch_one("SELECT finished_at FROM missions WHERE id=$1", mid)
    assert row["finished_at"] is not None
    assert await missions._live_ids() == []
    listed = await missions.recent(5)
    assert listed[0]["id"] == mid and listed[0]["status"] == "done"


async def test_a_question_waits_for_its_answer_on_the_same_surface(pg):
    mid = await missions._insert_mission(
        agent="agency", recipe=missions.AGENCY_SITES, request="find 5 plumbers",
        channel="voice", who_="dashboard", thread="", slots={"n": 5},
        status="needs_input")
    assert (await missions._pending_question("voice"))["id"] == mid
    assert await missions._pending_question("slack") is None
    await missions._supersede_questions("voice", "agency")
    assert await missions._pending_question("voice") is None


async def test_lead_facts_reads_audits_mockups_and_sites(pg):
    none, poor, tidy = await _lead(None), await _lead("http://old.example"), \
        await _lead("https://fine.example")
    for lead, severity, checks in ((none, 3, ["no_website"]),
                                   (poor, 4, ["no_https", "not_mobile"]),
                                   (tidy, 1, ["no_schema"])):
        await state.execute_sql(
            "INSERT INTO agency_audits (lead_id, url, findings, severity) "
            "VALUES ($1, NULL, $2::jsonb, $3)", lead,
            json.dumps([{"check": c, "severity": 1, "detail": ""} for c in checks]),
            severity)
    # A withdrawn audit (severity -1) is not an audit.
    await state.execute_sql(
        "INSERT INTO agency_audits (lead_id, url, findings, severity) "
        "VALUES ($1, NULL, '[]'::jsonb, -1)", tidy)
    await state.execute_sql(
        "INSERT INTO agency_mockups (lead_id, path) VALUES ($1, 'x.html')", poor)
    await state.execute_sql(
        "INSERT INTO agency_sites (lead_id, slug, path) VALUES ($1, $2, 'p')",
        poor, f"joes-{poor}")
    facts = {f.id: f for f in await missions.lead_facts([none, poor, tidy])}
    # Nothing is verified yet: nothing qualifies, however poor its site.
    assert [missions.qualifies(facts[i]) for i in (none, poor, tidy)] == [
        False, False, False]
    for lead in (none, poor, tidy):
        await state.execute_sql(
            "INSERT INTO agency_verifications (lead_id, verdict) "
            "VALUES ($1, 'verified')", lead)
    await state.execute_sql(
        "INSERT INTO agency_verifications (lead_id, verdict, withdrawn) "
        "VALUES ($1, 'disqualified', TRUE)", poor)
    facts = {f.id: f for f in await missions.lead_facts([none, poor, tidy])}
    assert {f.verdict for f in facts.values()} == {"verified"}
    assert facts[none].checks == {"no_website"} and not facts[none].mockup
    assert facts[poor].mockup and facts[poor].site == f"joes-{poor}"
    assert facts[tidy].severity == 1
    assert [missions.qualifies(facts[i]) for i in (none, poor, tidy)] == [
        True, True, False]
