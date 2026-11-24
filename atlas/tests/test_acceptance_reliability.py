"""Reliability and concurrency acceptance tests, R01-R12 (plan p. 14-15).

Each test is named by its plan ID; docs/acceptance.md is the matrix. Most
need a real Postgres (the guarantees are SQL: UNIQUE rows, compare-and-set,
fences in the WHERE clause) and are skipped without ATLAS_TEST_PG_DSN.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from atlas.kernel import approvals, durable, outbox, state
from atlas.kernel.flows import content as content_flow

# Fixtures. pytest registers them by their own names; the aliases keep
# the module names apart from the test parameters that request them.
from tests.acceptance_support import (
    CARD,
    STUB,
    StubWorld,
    audit_events,
    drain_background,
    due_now,
    install_stub,
    lapse,
    media_params,
    needs_pg,
    outbox_rows,
    pg_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
    slack_fixture,  # noqa: F401
    slack_message,
    workflows,
)

S = durable.State
NOW = timedelta(0)


def _question_path(monkeypatch, answers: list[str]) -> None:
    """A knowledge question, answered by a fake knowledge module."""
    from atlas.kernel import improve, knowledge

    async def not_improve(*_a, **_kw):
        return None

    async def answer(question, **_kw):
        answers.append(question)
        return "About 100 °C at sea level. (Perplexity, read 12:00 UTC)"

    monkeypatch.setattr(improve, "handle", not_improve)
    monkeypatch.setattr(knowledge, "answer", answer)


# -------------------------------------------------------------------- R01

@needs_pg
async def test_R01_one_slack_event_delivered_five_times_is_one_workflow_one_reply(
        pg, slack, monkeypatch):
    """The legacy dedup (`_seen_before`) is neutralised, as it is for two
    workers racing its audit lookup: the workflow row alone must hold."""
    post, replies = slack
    from atlas.api import slack_events

    async def never_seen(_event_id):
        return False

    monkeypatch.setattr(slack_events, "_seen_before", never_seen)
    answered: list[str] = []
    _question_path(monkeypatch, answered)
    event = slack_message("atlas, what is the boiling point of water at sea level?",
                          event_id="EvR01")
    got = await asyncio.gather(*[post(event) for _ in range(5)])
    await drain_background()
    assert all(r.status_code == 200 for r in got)
    assert sum(r.json().get("ignored") == "duplicate" for r in got) == 4
    rows = await workflows("slack.event")
    assert len(rows) == 1 and rows[0]["state"] == "SUCCEEDED"
    assert len(answered) == 1 and len(replies) == 1


@needs_pg
async def test_R01_one_content_job_delivered_five_times_is_one_effect_per_leg(pg):
    world = StubWorld()
    install_stub(world)
    got = await asyncio.gather(*[
        content_flow.autopost(media_params(), "daily reel", dedup="day:R01",
                              media_id=7, publisher=STUB) for _ in range(5)])
    assert sum(r.ok for r in got) == 1
    assert all(r.ok or "in flight" in r.detail or content_flow.HELD in r.detail
               for r in got)
    assert len(await workflows("content.reel")) == 1
    assert world.count("instagram") == 1 and world.count("tiktok") == 1
    assert len(await outbox_rows()) == 2


# -------------------------------------------------------------------- R02

@needs_pg
async def test_R02_button_and_typed_approval_together_run_the_card_once(pg):
    world = StubWorld()
    install_stub(world)
    params = media_params(21)
    pid = await approvals.propose(CARD, params, "reel card", dedup_key="R02")
    wid = await content_flow.carded(params, dedup="day:R02", media_id=21, pid=pid)
    assert (await durable.get(wid))["state"] == "WAITING_APPROVAL"
    verdicts = await asyncio.gather(
        approvals.decide(pid, "approve", "slack:button:UMIRO"),
        approvals.decide(pid, "approve", "slack:UMIRO"))
    assert sorted(v.outcome for v in verdicts).count("executed") == 1
    assert world.posted.get("card") == ["card-21"]          # one effect
    assert [n for n, _ in world.runs].count(CARD) == 1
    # The workflow mirrors the one claim, twice asked, once moved.
    await asyncio.gather(content_flow.sync_cards(), content_flow.sync_cards())
    row = await durable.get(wid)
    assert row["state"] == "SUCCEEDED", row
    ready = [e for e in await audit_events("workflow.transition_refused")]
    assert all("SUCCEEDED" not in str(e["payload"]) for e in ready)


# -------------------------------------------------------------------- R03

@needs_pg
async def test_R03_a_worker_that_dies_before_dispatch_is_resumed_once(pg):
    world = StubWorld()
    install_stub(world)
    # Worker A queues the job, then claims the first leg and dies.
    res = await content_flow.autopost(media_params(), "reel", dedup="day:R03",
                                      media_id=7, publisher=STUB, inline=False)
    assert content_flow.HELD in res.detail and world.runs == []
    claimed = await outbox._claim("worker-a", timedelta(minutes=5))
    assert claimed is not None
    # B finds only the unclaimed leg while A's lease is live...
    first = await outbox.dispatch(limit=5, owner="worker-b")
    assert [d.action for d in first] == ["stubpub.publish"]
    # ...and A's leg once its lease lapses: each leg ran exactly once.
    await lapse("outbox", int(claimed["id"]))
    await outbox.dispatch(limit=5, owner="worker-b")
    assert world.count("instagram") == 1 and world.count("tiktok") == 1
    # A comes back: its fence is stale, nothing runs.
    assert not await outbox._fence_for_dispatch(claimed, "worker-a")
    assert world.count() == 2


@needs_pg
async def test_R03_a_job_whose_first_worker_died_mid_intake_is_taken_over(pg):
    world = StubWorld()
    install_stub(world)
    taken, _ = await content_flow._take("day:R03b", 8, media_params(8))
    assert taken is not None                      # A: RECEIVED, lease held
    held = await content_flow.autopost(media_params(8), "reel", dedup="day:R03b",
                                       media_id=8, publisher=STUB)
    assert not held.ok and world.runs == []       # B waits: A's lease is live
    await lapse("workflows", taken.workflow_id)
    done = await content_flow.autopost(media_params(8), "reel", dedup="day:R03b",
                                       media_id=8, publisher=STUB)
    assert done.ok and world.count() == 2
    assert len(await workflows("content.reel")) == 1


# -------------------------------------------------------------------- R04

@needs_pg
async def test_R04_crash_after_the_provider_accepted_is_reconciled_not_resent(pg):
    world = StubWorld()
    install_stub(world)
    await content_flow.autopost(media_params(), "reel", dedup="day:R04",
                                media_id=7, publisher=STUB, inline=False)
    rows = await outbox_rows()
    ig = rows[0]
    # Worker A dispatches instagram; the provider takes it; A dies.
    row = await outbox._claim("worker-a", timedelta(minutes=5), only_id=ig["id"])
    assert await outbox._fence_for_dispatch(row, "worker-a")
    from atlas.kernel.executor import ExecutionRequest, execute
    params = row["params"]
    params = json.loads(params) if isinstance(params, str) else params
    await execute(ExecutionRequest(action=ig["action"], params=params, reason="t"))
    assert world.count("instagram") == 1
    await lapse("outbox", ig["id"])
    await outbox.dispatch(limit=5, owner="worker-b")       # sweeps + tiktok
    assert (await outbox_rows())[0]["status"] == "reconciling"
    assert world.count("instagram") == 1                   # never resent
    settled = await outbox.reconcile_once("worker-b")
    assert settled.status == "delivered"
    assert world.count("instagram") == 1
    assert await content_flow.settle(ig["workflow_id"], grace=NOW) is S.SUCCEEDED


@needs_pg
async def test_R04_a_reconciled_leg_with_its_id_reads_back_and_the_job_succeeds(pg):
    """The same crash, where the reconciler recovers the post id: the leg
    is read back like any other and the workflow ends SUCCEEDED."""
    world = StubWorld(behaviour={"tiktok": "timeout_after_accept"})
    install_stub(world)
    res = await content_flow.autopost(media_params(), "reel", dedup="day:R04b",
                                      media_id=7, publisher=STUB)
    assert res.ok                                        # instagram posted
    wid = (await workflows("content.reel"))[0]["id"]
    statuses = {r["destination"]: r["status"] for r in await outbox_rows(wid)}
    assert statuses == {"instagram": "delivered", "tiktok": "reconciling"}
    assert await content_flow.settle(wid, grace=NOW) is None      # still open
    assert (await durable.get(wid))["state"] == "RECONCILING"
    assert (await outbox.reconcile_once()).status == "delivered"
    assert await content_flow.settle(wid, grace=NOW) is S.SUCCEEDED
    assert world.count("tiktok") == 1                    # reconciled, not resent


# -------------------------------------------------------------------- R05

@needs_pg
async def test_R05_a_stale_worker_cannot_commit_and_its_effect_is_reconciled(pg):
    world = StubWorld()
    install_stub(world)
    await content_flow.autopost(media_params(), "reel", dedup="day:R05",
                                media_id=7, publisher=STUB, inline=False)
    ig = (await outbox_rows())[0]
    swept: list = []

    async def while_running(platform):
        # A's lease lapses mid-run; B sweeps the row before A returns.
        if platform == "instagram" and not swept:
            await lapse("outbox", ig["id"])
            swept.extend(await outbox.sweep_lapsed())

    world.on_run = while_running
    done = await outbox.dispatch_once("worker-a", only_id=ig["id"])
    assert swept == [ig["id"]]
    assert done.committed is False                       # the fence refused A
    assert (await outbox_rows())[0]["status"] == "reconciling"
    assert await audit_events("outbox.stale_commit")      # what A saw is kept
    settled = await outbox.reconcile_once("worker-b")
    assert settled.status == "delivered" and world.count("instagram") == 1
    # The same holds for the workflow's own lease.
    wf = await durable.receive("t.r05", "c1")
    a = await durable.claim(wf.workflow_id, timedelta(minutes=5), owner="a")
    await lapse("workflows", wf.workflow_id)
    b = await durable.claim(wf.workflow_id, timedelta(minutes=5), owner="b")
    assert not await durable.transition(wf.workflow_id, S.RECEIVED, S.VALIDATING,
                                        fence=a)
    assert await durable.transition(wf.workflow_id, S.RECEIVED, S.VALIDATING,
                                    fence=b)


# -------------------------------------------------------------------- R06

async def test_R06_database_down_at_intake_accepts_nothing_and_posts_nothing(
        monkeypatch, memory_backend, registries):
    """No database: the content job is not accepted, nothing is dispatched,
    and post_or_card's caller is told it waits (in-suite: the database is
    a function that raises)."""
    monkeypatch.setenv("ATLAS_DURABLE_CORE", "on")
    monkeypatch.setenv("ATLAS_DURABLE_FLOWS", "all")
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    world = StubWorld()
    install_stub(world)

    async def down(*_a, **_kw):
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(state, "fetch_one", down)
    monkeypatch.setattr(state, "fetch_all", down)
    got = await content_flow.autopost(media_params(), "reel", dedup="day:R06",
                                      media_id=7, publisher=STUB)
    assert not got.ok and content_flow.HELD in got.detail
    assert "nothing was posted" in got.detail
    assert world.runs == []


# -------------------------------------------------------------------- R07

@needs_pg
async def test_R07_a_failed_receipt_is_retried_alone_never_the_post(pg):
    world = StubWorld(receipt_failures=1)
    install_stub(world)
    res = await content_flow.autopost(media_params(), "reel", dedup="day:R07",
                                      media_id=7, publisher=STUB)
    assert res.ok
    wid = (await workflows("content.reel"))[0]["id"]
    assert await content_flow.settle(wid, grace=NOW) is S.SUCCEEDED
    notice = [r for r in await outbox_rows(wid) if r["kind"] == "notice"]
    assert len(notice) == 1
    first = await outbox.dispatch_once(only_id=notice[0]["id"])
    assert first.status == "pending"                     # Slack said 503
    await due_now(notice[0]["id"])
    second = await outbox.dispatch_once(only_id=notice[0]["id"])
    assert second.status == "delivered"
    assert len(world.receipts) == 1 and "instagram" in world.receipts[0]
    assert world.count() == 2                            # each leg once, only


# -------------------------------------------------------------------- R11

def test_R11_missed_windows_follow_one_declared_policy_each():
    from atlas.kernel import missed, scheduler
    now = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
    windows = [missed.Window(f"2026-09-{d}", datetime(2026, 9, d, 7, 0, tzinfo=UTC))
               for d in (25, 26, 27, 28)]
    keys = {w.key for w in windows}

    def policy(kind, **kw):
        return missed.JobPolicy("job", kind, "test", **kw)

    run, skip = missed.plan(policy(missed.Missed.SKIP_STALE), windows, keys, now)
    assert [w.key for w in run] == ["2026-09-28"] and len(skip) == 3
    run, skip = missed.plan(policy(missed.Missed.RUN_ONCE), windows, keys, now)
    assert [w.key for w in run] == ["2026-09-28"] and len(skip) == 3
    run, skip = missed.plan(policy(missed.Missed.CATCH_UP, max_runs=2),
                            windows, keys, now)
    assert [w.key for w in run] == ["2026-09-27", "2026-09-28"] and len(skip) == 2
    # Past its grace, a skip-stale window is skipped, never sent late.
    late = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)
    run, _ = missed.plan(policy(missed.Missed.SKIP_STALE), windows, keys, late)
    assert run == []
    # And every loop step has a stated policy.
    assert all(p.missed in set(missed.Missed) for p in scheduler.POLICIES.values())


# -------------------------------------------------------------------- R08

@needs_pg
async def test_R08_a_transient_outage_is_retried_boundedly_from_a_clear_state(pg):
    """The provider is down for the first call. The row is an effect, so it
    is reconciled first (nothing is resent on a guess); the read-back finds
    no post, the row goes back to `pending` with its attempt counted, and
    the retry posts it once. `max_attempts` bounds the loop."""
    world = StubWorld(behaviour={"instagram": "raise_before"})
    install_stub(world)

    async def recover(platform):
        if platform == "instagram" and world.count("instagram") == 1:
            return
        world.behaviour["instagram"] = "ok"

    world.on_run = recover
    await content_flow.autopost(media_params(), "reel", dedup="day:R08",
                                media_id=7, publisher=STUB)
    ig = (await outbox_rows())[0]
    assert ig["status"] == "reconciling" and ig["attempts"] == 1
    world.behaviour["instagram"] = "ok"
    assert (await outbox.reconcile_once()).status == "pending"
    await due_now(ig["id"])
    assert (await outbox.dispatch_once(only_id=ig["id"])).status == "delivered"
    assert world.posted["instagram"] == ["instagram-7-1"]
    row = (await outbox_rows())[0]
    assert row["attempts"] == 2 and row["max_attempts"] == 5


async def test_R06_a_database_blip_after_slack_intake_still_answers(monkeypatch):
    """The ask slice only replies: when its record fails after intake, the
    handler still runs (unrecorded) instead of the message being dropped
    after Slack already had its 200."""
    from atlas.kernel.flows import ask
    from atlas.kernel.leases import Fence

    async def down(*_a, **_kw):
        raise durable.DurableUnavailable("ConnectionError")

    async def released(_fence):
        return False

    monkeypatch.setattr(durable, "transition", down)
    monkeypatch.setattr(durable, "release", released)
    replied: list = []

    async def handler():
        ask.note_reply(["slack"])
        replied.append("answered")

    taken = ask.Taken(1, Fence("workflow", "1", "w", 1))
    await ask.handled(taken, handler(), "request")
    assert replied == ["answered"]
    assert taken.replies == []          # unrecorded: outside the workflow
    assert not ask.active()
