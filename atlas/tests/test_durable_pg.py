"""The durable core against a real Postgres (RA-B1/B3/B4).

The in-suite tests (test_durable_core.py) script the database's answers; the
guarantees themselves -- the compare-and-set, the fence in the WHERE clause,
the UNIQUE dedup, the token that only grows -- live in SQL, so they are
proven here, against the schema `init-db` applies. Skipped unless
ATLAS_TEST_PG_DSN points at a throwaway database, e.g.

    docker run -d --rm --name atlas-pg -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_DB=atlas -p 55432:5432 pgvector/pgvector:pg16
    ATLAS_TEST_PG_DSN=postgresql://postgres:test@localhost:55432/atlas \\
        .venv/bin/python -m pytest tests/test_durable_pg.py

It TRUNCATEs workflows, outbox, leases, proposals and restores. Never point it at a
database whose rows matter.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import durable, executor, leases, outbox, state
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="set ATLAS_TEST_PG_DSN to a throwaway Postgres to run")

TTL = timedelta(minutes=5)


async def _close_pool() -> None:
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    await _close_pool()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql(
        "TRUNCATE workflows, outbox, leases, proposals, restores "
        "RESTART IDENTITY CASCADE")
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    try:
        yield
    finally:
        REGISTRY.actions.clear()
        REGISTRY.decisions.clear()
        executor._recent.clear()
        outbox.RECONCILERS.clear()
        await _close_pool()
        state.set_backend(previous)


def install(name: str, runs: list, *, ok: bool = True,
            evidence: dict | None = None, tier: Tier = Tier.AUTO,
            fail_first: int = 0) -> None:
    async def run(**kw):
        runs.append(kw)
        if len(runs) <= fail_first:
            return ActionResult(False, "slack answered 503")
        return ActionResult(ok, "done", evidence=dict(evidence or {}))

    async def dry(**kw):
        return ActionResult(True, "would do it")

    async def undo(token):
        return ActionResult(True, "undone")

    action = Action(name=name, tier=tier, summary="s", run=run, dry_run=dry,
                    rollback=undo, params={"to": "who"}, max_per_hour=50)
    REGISTRY.actions[name] = action
    REGISTRY.decisions[name] = classify(action)


async def _lapse(table: str, row_id: int) -> None:
    """A worker that stopped renewing: its lease is now in the past."""
    if table == "outbox":
        await state.execute_sql(
            "UPDATE outbox SET lease_expiry = now() - interval '1 second' "
            "WHERE id = $1", row_id)
    else:
        await state.execute_sql(
            "UPDATE workflows SET lease_expiry = now() - interval '1 second' "
            "WHERE id = $1", row_id)


async def _status(oid: int) -> str:
    row = await state.fetch_one("SELECT status FROM outbox WHERE id = $1", oid)
    return str(row["status"])


# -------------------------------------------------------------------- R01

async def test_r01_one_inbound_event_delivered_five_times_is_one_workflow(pg):
    got = await asyncio.gather(*[
        durable.receive("slack.message", "Ev0001", payload={"text": "restart it"},
                        input_refs=["slack:event:Ev0001"], channel="slack")
        for _ in range(5)])
    assert len({g.workflow_id for g in got}) == 1
    assert sum(g.created for g in got) == 1
    row = await state.fetch_one("SELECT count(*) AS n, max(input_hash) AS h "
                                "FROM workflows")
    assert row["n"] == 1
    assert row["h"] == durable.digest({"text": "restart it"})   # hash, not text


async def test_r01_one_intent_enqueued_five_times_is_one_effect(pg):
    runs: list = []
    install("fake.send", runs, evidence={"message_id": "m1"})
    got = await asyncio.gather(*[
        outbox.enqueue("fake.send", {"to": "owner"}, destination="owner",
                       occurrence="card#7") for _ in range(5)])
    assert len({g.outbox_id for g in got}) == 1
    # Same text, a different logical occurrence: a real second request.
    again = await outbox.enqueue("fake.send", {"to": "owner"}, destination="owner",
                                 occurrence="card#8")
    assert again.created and again.outbox_id != got[0].outbox_id
    done = await outbox.dispatch(limit=10)
    assert [d.status for d in done] == ["delivered", "delivered"]
    assert len(runs) == 2


# -------------------------------------------------------------------- R03

async def test_r03_a_worker_that_dies_before_dispatch_is_resumed_once(pg):
    runs: list = []
    install("fake.send", runs, evidence={"message_id": "m1"})
    q = await outbox.enqueue("fake.send", {"to": "owner"}, destination="owner",
                             occurrence="o1")
    claimed = await outbox._claim("worker-a", TTL)       # A claims, then dies
    assert claimed is not None and runs == []
    assert await outbox.dispatch_once("worker-b") is None   # A's lease is live
    await _lapse("outbox", q.outbox_id)
    done = await outbox.dispatch_once("worker-b")
    assert done is not None and done.status == "delivered"
    assert len(runs) == 1
    # A comes back and tries to go on: its fence is stale, nothing runs.
    assert not await outbox._fence_for_dispatch(claimed, "worker-a")
    assert len(runs) == 1


async def test_r03_a_workflow_left_by_a_dead_worker_is_claimed_by_another(pg):
    w = await durable.receive("t", "c1")
    a = await durable.claim(w.workflow_id, TTL, owner="worker-a")
    assert a is not None
    assert await durable.claim_next([durable.State.RECEIVED], TTL,
                                    owner="worker-b") is None
    await _lapse("workflows", w.workflow_id)
    got = await durable.claim_next([durable.State.RECEIVED], TTL, owner="worker-b")
    assert got is not None
    b, st = got
    assert st is durable.State.RECEIVED and b.token == a.token + 1
    assert await durable.transition(w.workflow_id, "RECEIVED", "VALIDATING",
                                    fence=b)


# -------------------------------------------------------------------- R04

async def test_r04_crash_after_the_provider_accepted_is_reconciled_not_resent(pg):
    runs: list = []
    install("fake.publish", runs, evidence={"post_id": "p9"})
    q = await outbox.enqueue("fake.publish", {"to": "page"}, destination="page",
                             occurrence="reel#10")
    row = await outbox._claim("worker-a", TTL)
    assert await outbox._fence_for_dispatch(row, "worker-a")
    # The provider takes it...
    await executor.execute(executor.ExecutionRequest(
        action="fake.publish", params={"to": "page"}, reason="t"))
    assert len(runs) == 1
    # ...and worker A dies before recording anything.
    await _lapse("outbox", q.outbox_id)
    assert await outbox.dispatch(limit=5, owner="worker-b") == []
    assert await _status(q.outbox_id) == "reconciling"
    assert len(runs) == 1                                   # never resent

    async def lookup(r):
        return "present", {"post_id": "p9"}
    outbox.RECONCILERS["fake.publish"] = lookup
    settled = await outbox.reconcile_once("worker-b")
    assert settled is not None and settled.status == "delivered"
    assert await _status(q.outbox_id) == "delivered"
    assert len(runs) == 1


async def test_r04_with_no_reconciler_a_person_decides(pg):
    runs: list = []
    install("fake.publish", runs, evidence={"post_id": "p9"})
    q = await outbox.enqueue("fake.publish", {"to": "page"}, destination="page",
                             occurrence="reel#11")
    row = await outbox._claim("worker-a", TTL)
    assert await outbox._fence_for_dispatch(row, "worker-a")
    await _lapse("outbox", q.outbox_id)
    await outbox.sweep_lapsed()
    settled = await outbox.reconcile_once("worker-b")
    assert settled.status == "needs_human"
    assert await outbox.dispatch(limit=5) == []
    assert runs == []


async def test_r04_an_action_that_raised_is_reconciled_not_retried(pg):
    calls: list = []

    async def run(**kw):
        calls.append(kw)
        raise RuntimeError("socket closed after the POST")

    async def dry(**kw):
        return ActionResult(True, "ok")

    async def undo(token):
        return ActionResult(True, "undone")
    action = Action(name="fake.flaky", tier=Tier.AUTO, summary="s", run=run,
                    dry_run=dry, rollback=undo, params={"to": "x"})
    REGISTRY.actions["fake.flaky"] = action
    REGISTRY.decisions["fake.flaky"] = classify(action)
    q = await outbox.enqueue("fake.flaky", {"to": "x"}, destination="x",
                             occurrence="1")
    done = await outbox.dispatch_once()
    assert done.status == "reconciling"
    assert await outbox.dispatch(limit=5) == []
    assert len(calls) == 1
    assert await _status(q.outbox_id) == "reconciling"


# -------------------------------------------------------------------- R05

async def test_r05_a_stale_worker_cannot_commit_its_outbox_result(pg):
    runs: list = []
    install("fake.send", runs, evidence={"message_id": "m1"})
    q = await outbox.enqueue("fake.send", {"to": "owner"}, destination="owner",
                             occurrence="o5")
    row = await outbox._claim("worker-a", TTL)
    assert await outbox._fence_for_dispatch(row, "worker-a")
    await _lapse("outbox", q.outbox_id)
    await outbox.sweep_lapsed()                      # worker B's pass
    ok = await outbox._commit(row, "worker-a", "delivered", "done",
                              {"message_id": "m1"}, None)
    assert not ok
    assert await _status(q.outbox_id) == "reconciling"


async def test_r05_fencing_blocks_a_stale_workflow_commit(pg):
    w = await durable.receive("t", "c5")
    a = await durable.claim(w.workflow_id, TTL, owner="worker-a")
    await _lapse("workflows", w.workflow_id)
    b = await durable.claim(w.workflow_id, TTL, owner="worker-b")
    assert b is not None and b.token > a.token
    # Both race the same move: only the current fence lands.
    results = await asyncio.gather(
        durable.transition(w.workflow_id, "RECEIVED", "VALIDATING", fence=a),
        durable.transition(w.workflow_id, "RECEIVED", "VALIDATING", fence=b))
    assert results == [False, True]
    # An unexpired but superseded fence cannot queue an effect either.
    await durable.transition(w.workflow_id, "VALIDATING", "READY", fence=b)
    oid = await outbox.enqueue_with_transition(
        w.workflow_id, "READY", "RUNNING", a, "fake.send", {"to": "m"},
        destination="m", occurrence="1")
    assert oid is None
    assert (await durable.get(w.workflow_id))["state"] == "READY"
    assert await state.fetch_one("SELECT id FROM outbox") is None


async def test_r05_job_leases_fence_and_the_token_only_grows(pg):
    a = await leases.acquire("scheduler:review", TTL, owner="worker-a")
    assert a is not None
    assert await leases.acquire("scheduler:review", TTL, owner="worker-b") is None
    assert await leases.acquire("scheduler:review", TTL, owner="worker-a") is None
    await state.execute_sql(
        "UPDATE leases SET expires_at = now() - interval '1 second' WHERE name = $1",
        "scheduler:review")
    assert not await leases.holds(a)                 # expired is not held
    b = await leases.acquire("scheduler:review", TTL, owner="worker-b")
    assert b is not None and b.token == a.token + 1
    assert await leases.renew(a, TTL) is None
    assert not await leases.release(a)
    assert await leases.holds(b)
    assert await leases.release(b)
    c = await leases.acquire("scheduler:review", TTL, owner="worker-a")
    assert c.token == b.token + 1                    # released, never reset
    assert not await leases.holds(b)


# -------------------------------------------------------------------- R06

async def test_r06_database_down_means_nothing_is_accepted_or_run(pg, monkeypatch):
    runs: list = []
    install("fake.send", runs, evidence={"message_id": "m1"})
    q = await outbox.enqueue("fake.send", {"to": "owner"}, destination="owner",
                             occurrence="o6")
    await _close_pool()
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:test@127.0.0.1:1/x")
    with pytest.raises(durable.DurableUnavailable):
        await durable.receive("slack.message", "Ev6")
    with pytest.raises(durable.DurableUnavailable):
        await outbox.enqueue("fake.send", {"to": "m"}, destination="m",
                             occurrence="o7")
    assert await outbox.dispatch_once() is None
    assert await leases.acquire("scheduler:review", TTL) is None
    assert runs == []
    await _close_pool()
    monkeypatch.setenv("DATABASE_URL", DSN)
    assert await _status(q.outbox_id) == "pending"


async def test_r06_database_lost_after_the_run_is_not_reported_delivered(
        pg, monkeypatch):
    runs: list = []
    install("fake.send", runs, evidence={"message_id": "m1"})
    q = await outbox.enqueue("fake.send", {"to": "owner"}, destination="owner",
                             occurrence="o8")
    real = state.fetch_one

    async def flaky(sql, *a):
        if "SET status = $4" in sql:
            raise ConnectionError("database went away")
        return await real(sql, *a)
    monkeypatch.setattr(state, "fetch_one", flaky)
    done = await outbox.dispatch_once()
    assert done.committed is False and len(runs) == 1
    monkeypatch.setattr(state, "fetch_one", real)
    assert await _status(q.outbox_id) == "dispatching"
    await _lapse("outbox", q.outbox_id)
    await outbox.dispatch(limit=5)
    assert await _status(q.outbox_id) == "reconciling"   # never "delivered"
    assert len(runs) == 1


# -------------------------------------------------------------------- R07

async def test_r07_a_failed_notice_is_retried_alone(pg):
    effect_runs: list = []
    notice_runs: list = []
    install("fake.restart", effect_runs, evidence={"job": "j1"})
    install("fake.notify", notice_runs, evidence={"ts": "1.2"}, fail_first=1)
    w = await durable.receive("fix", "incident#3")
    fence = await durable.claim(w.workflow_id, TTL)
    for src, dst in (("RECEIVED", "VALIDATING"), ("VALIDATING", "READY")):
        assert await durable.transition(w.workflow_id, src, dst, fence=fence)
    oid = await outbox.enqueue_with_transition(
        w.workflow_id, "READY", "RUNNING", fence, "fake.restart", {"to": "api"},
        destination="api", occurrence="incident#3")
    assert oid is not None
    note = await outbox.enqueue("fake.notify", {"to": "owner"}, kind="notice",
                                destination="slack:decisions",
                                occurrence="incident#3:receipt",
                                workflow_id=w.workflow_id)
    first = await outbox.dispatch(limit=5)
    assert {d.action: d.status for d in first} == {
        "fake.restart": "delivered", "fake.notify": "pending"}
    wf = await durable.get(w.workflow_id)
    assert wf["state"] == "VERIFYING" and f"outbox:{oid}" in wf["evidence_refs"]
    await state.execute_sql(
        "UPDATE outbox SET next_attempt_at = now() WHERE id = $1", note.outbox_id)
    second = await outbox.dispatch(limit=5)
    assert [(d.action, d.status) for d in second] == [("fake.notify", "delivered")]
    assert len(effect_runs) == 1 and len(notice_runs) == 2


async def test_ok_without_evidence_is_unverified_not_delivered(pg):
    runs: list = []
    install("fake.send", runs)                              # no evidence
    q = await outbox.enqueue("fake.send", {"to": "m"}, destination="m",
                             occurrence="1")
    done = await outbox.dispatch_once()
    assert done.status == "unverified"
    assert await _status(q.outbox_id) == "unverified"


async def test_a_pre_dispatch_refusal_is_retried_and_a_permanent_one_is_not(
        pg, monkeypatch):
    runs: list = []
    install("fake.send", runs, evidence={"m": 1})
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    q = await outbox.enqueue("fake.send", {"to": "m"}, destination="m",
                             occurrence="1")
    done = await outbox.dispatch_once()
    assert done.status == "pending" and runs == []
    row = await state.fetch_one(
        "SELECT attempts, next_attempt_at > now() + interval '10 minutes' AS later "
        "FROM outbox WHERE id = $1", q.outbox_id)
    assert row["attempts"] == 0 and row["later"]      # waiting, not failing
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    bad = await outbox.enqueue("fake.send", {"nope": 1}, destination="m",
                               occurrence="2")
    await state.execute_sql(
        "UPDATE outbox SET next_attempt_at = now() + interval '1 hour' "
        "WHERE id = $1", q.outbox_id)
    done = await outbox.dispatch_once()
    assert done.outbox_id == bad.outbox_id and done.status == "failed"
    assert runs == []


# ------------------------------------------------------- approval, beside it

async def test_a_workflow_leaves_waiting_approval_only_after_the_claim(pg):
    runs: list = []
    install("fake.ask", runs, evidence={"x": 1}, tier=Tier.ASK)
    w = await durable.receive("ask", "c9")
    for src, dst in (("RECEIVED", "VALIDATING"), ("VALIDATING", "READY")):
        await durable.transition(w.workflow_id, src, dst)
    expires = datetime.now(UTC) + timedelta(hours=1)
    row = await state.fetch_one(
        """INSERT INTO proposals (expires_at, action, params, tier, reason)
           VALUES ($1, 'fake.ask', '{"to": "m"}', 2, 'r') RETURNING id""", expires)
    pid = int(row["id"])
    assert await durable.await_approval(w.workflow_id, pid, expires_at=expires)
    h = durable.digest({"to": "m"})
    assert not await durable.approved(w.workflow_id, h)       # still pending
    with pytest.raises(durable.IllegalTransition):
        await durable.transition(w.workflow_id, "WAITING_APPROVAL", "READY")
    await state.execute_sql(
        "UPDATE proposals SET expires_at = now() - interval '1 second' "
        "WHERE id = $1", pid)
    from atlas.kernel import approvals
    verdict = await approvals.decide(pid, "approve", "slack:U1")
    assert verdict.outcome == "expired"                     # silence: no
    assert not await durable.approved(w.workflow_id, h)
    assert (await durable.get(w.workflow_id))["state"] == "WAITING_APPROVAL"


async def test_a_claimed_approval_moves_the_workflow_to_ready(pg):
    runs: list = []
    install("fake.ask", runs, evidence={"x": 1}, tier=Tier.ASK)
    w = await durable.receive("ask", "c10")
    for src, dst in (("RECEIVED", "VALIDATING"), ("VALIDATING", "READY")):
        await durable.transition(w.workflow_id, src, dst)
    expires = datetime.now(UTC) + timedelta(hours=1)
    row = await state.fetch_one(
        """INSERT INTO proposals (expires_at, action, params, tier, reason)
           VALUES ($1, 'fake.ask', '{"to": "m"}', 2, 'r') RETURNING id""", expires)
    pid = int(row["id"])
    await durable.await_approval(w.workflow_id, pid, expires_at=expires)
    from atlas.kernel import approvals
    verdict = await approvals.decide(pid, "approve", "slack:U1")
    assert verdict.ok, verdict.detail
    assert await durable.approved(w.workflow_id, durable.digest({"to": "m"}))
    wf = await durable.get(w.workflow_id)
    assert wf["state"] == "READY" and wf["approved_payload_hash"]


# -------------------------------------------------------------------- RA-B7

async def test_ra_b7_an_effect_is_claimed_once_against_real_sql(pg):
    """kernel.idempotency's claim, settle and release against the schema:
    five concurrent claims of one key grant exactly one; a settled claim
    reads as sent; a released one may be claimed again; an unsettled one
    stays in doubt (no stale re-claim)."""
    from atlas.kernel import idempotency
    await state.execute_sql("TRUNCATE effect_claims")
    got = await asyncio.gather(*[
        idempotency.claim("messages.reply", "inbound:1|proposal:9")
        for _ in range(5)])
    assert sorted(got) == [idempotency.CLAIMED] + [idempotency.IN_DOUBT] * 4
    await idempotency.settle("messages.reply", "inbound:1|proposal:9", "ok")
    assert (await idempotency.claim("messages.reply", "inbound:1|proposal:9")
            == idempotency.SENT)
    assert await idempotency.claim("messages.reply", "k2") == idempotency.CLAIMED
    await idempotency.release("messages.reply", "k2")
    assert await idempotency.claim("messages.reply", "k2") == idempotency.CLAIMED
    await state.execute_sql(
        "UPDATE effect_claims SET claimed_at = now() - interval '2 days'")
    assert await idempotency.claim("messages.reply", "k2") == idempotency.IN_DOUBT
