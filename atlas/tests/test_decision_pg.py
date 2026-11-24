"""The GUI decision route and the shift query against a real Postgres.

`tests/test_api.py` scripts the database's answers; here the atomic claim,
the audit chain and the `LIKE ANY` shift filter run as SQL on the schema
`init-db` applies. Skipped unless ATLAS_TEST_PG_DSN points at a throwaway
database, e.g.

    docker run -d --rm --name atlas-pg-test -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=atlas -p 127.0.0.1:55432:5432 pgvector/pgvector:pg16

It TRUNCATEs proposals, actions_log and agent_runs. Never point it at a
database whose rows matter: the cards it decides are its own test cards.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ATLAS_TEST_PG_DSN to a throwaway Postgres to run")

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"
ASK_ACTION = "testmod.pg_ask_thing"


async def _sql(query: str, *args):
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        return await conn.fetch(query, *args)
    finally:
        await conn.close()


def q(query: str, *args):
    return asyncio.run(_sql(query, *args))


async def _init() -> None:
    from atlas import cli
    from atlas.kernel import state
    previous = state._pool
    state._pool = None
    try:
        await cli.init_db()
    finally:
        pool, state._pool = state._pool, previous
        if pool is not None:
            await pool.close()


@pytest.fixture
def pg(monkeypatch):
    from atlas.kernel import state
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    state._pool = None
    previous = state.set_backend(state.PostgresBackend())
    asyncio.run(_init())
    q("TRUNCATE proposals, actions_log, agent_runs RESTART IDENTITY CASCADE")
    try:
        yield
    finally:
        state._pool = None
        state.set_backend(previous)


@pytest.fixture
def browser(pg, monkeypatch):
    from fastapi.testclient import TestClient

    from atlas.api import auth, dashboard
    from atlas.kernel import approvals, judgment
    from atlas.kernel.contracts import Tier
    from atlas.kernel.permissions import Decision
    from atlas.kernel.registry import REGISTRY

    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    monkeypatch.delenv("ATLAS_ALLOWED_HOSTS", raising=False)
    monkeypatch.setitem(REGISTRY.decisions, ASK_ACTION,
                        Decision(True, Tier.ASK, "asks first"))
    auth._attempts.clear()
    dashboard._decision_limit.clear()
    dashboard._decision_calls.clear()
    executed: list = []

    async def fake_execute(req):
        executed.append(req)
        raise AssertionError("a decline never reaches the executor")

    async def nothing(*_a, **_kw):
        return None

    monkeypatch.setattr(approvals, "execute", fake_execute)
    monkeypatch.setattr(judgment, "record_outcome", nothing)

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    from atlas.api.main import app
    with TestClient(app, base_url="http://127.0.0.1:8080",
                    client=("127.0.0.1", 51000)) as c:
        assert c.post("/api/login", json={"password": PASSWORD}).status_code == 200
        csrf = c.get("/api/session").json()["csrf"]
        yield {"client": c, "executed": executed,
               "headers": {"Origin": "http://127.0.0.1:8080", "X-CSRF-Token": csrf,
                           "Sec-Fetch-Site": "same-origin"}}


def _card(action: str = ASK_ACTION, tier: int = 2) -> int:
    rows = q("""INSERT INTO proposals (expires_at, action, params, tier, reason)
                VALUES (now() + interval '1 hour', $1, '{}', $2,
                        'test card for the GUI decision route')
                RETURNING id""", action, tier)
    return int(rows[0]["id"])


def test_gui_decline_of_a_tier_2_test_card_end_to_end(browser):
    pid = _card()
    r = browser["client"].post(f"/api/approvals/{pid}/decision",
                               json={"verdict": "decline"}, headers=browser["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["verdict"]["outcome"] == "declined"
    row = q("SELECT status, decided_via FROM proposals WHERE id=$1", pid)[0]
    assert row["status"] == "declined"
    assert row["decided_via"].startswith("dashboard:")
    audit = q("SELECT payload FROM actions_log WHERE event='dashboard.decision' "
              "AND subject=$1", str(pid))
    assert len(audit) == 1
    data = json.loads(audit[0]["payload"])["data"]
    assert data["verdict"] == "decline" and data["outcome"] == "declined"
    # A second decline finds nothing pending: the claim matched once.
    again = browser["client"].post(f"/api/approvals/{pid}/decision",
                                   json={"verdict": "decline"},
                                   headers=browser["headers"])
    assert again.status_code == 409
    assert browser["executed"] == []


def test_gui_approve_under_watch_only_leaves_the_test_card_pending(browser):
    pid = _card()
    r = browser["client"].post(f"/api/approvals/{pid}/decision",
                               json={"verdict": "approve"}, headers=browser["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["verdict"]["outcome"] == "refused"
    assert q("SELECT status FROM proposals WHERE id=$1", pid)[0]["status"] == "pending"
    assert q("SELECT 1 FROM actions_log WHERE event='proposal.watch_only'")
    assert browser["executed"] == []


@pytest.mark.parametrize("action", ["testmod.unknown_never", ASK_ACTION])
def test_gui_decision_refuses_a_card_whose_code_tier_is_not_2(
        browser, monkeypatch, action):
    from atlas.kernel.contracts import Tier
    from atlas.kernel.permissions import Decision
    from atlas.kernel.registry import REGISTRY
    if action == ASK_ACTION:      # stored tier says 2, code says Tier 3
        monkeypatch.setitem(REGISTRY.decisions, ASK_ACTION,
                            Decision(False, Tier.NEVER, "money out"))
    pid = _card(action)
    r = browser["client"].post(f"/api/approvals/{pid}/decision",
                               json={"verdict": "decline"}, headers=browser["headers"])
    assert r.status_code == 403
    assert q("SELECT status FROM proposals WHERE id=$1", pid)[0]["status"] == "pending"
    assert browser["executed"] == []


def test_shift_query_counts_unanswered_shifts_as_failures(pg):
    from atlas.kernel import agents, scheduler
    from atlas.kernel.harness import NO_ANSWER, NO_TOOLS_PREFIX
    now = datetime.now(UTC)
    rows = [
        ("qa", now - timedelta(days=1), "fiverr: checks read."),
        ("qa", now - timedelta(minutes=30), NO_ANSWER),
        ("seo", now - timedelta(minutes=40), f"{NO_TOOLS_PREFIX} {NO_ANSWER}"),
        ("seo", now - timedelta(minutes=20), agents.TOOL_LIMIT_OUTCOME),
        ("sales", now - timedelta(minutes=10), "shift deferred: quota"),
        ("content", now - timedelta(minutes=5), "content: ten drafts waiting."),
    ]
    for agent, at, outcome in rows:
        q("INSERT INTO agent_runs (at, agent, task, model, outcome) "
          "VALUES ($1, $2, $3, 'm', $4)", at, agent, agents.SHIFT_PROMPT, outcome)
    got = {r["agent"]: r for r in q(scheduler.shift_rows_sql(),
                                     *scheduler.shift_rows_args(now))}
    assert got["qa"]["last_shift"] == rows[0][1]
    assert got["qa"]["failures"] == [rows[1][1]]
    assert got["seo"]["last_shift"] is None
    assert got["seo"]["failures"] == [rows[2][1], rows[3][1]]
    assert got["sales"]["last_shift"] is None and len(got["sales"]["failures"]) == 1
    assert got["content"]["last_shift"] == rows[5][1]
    assert got["content"]["failures"] is None
