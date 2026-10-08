"""Reliability acceptance tests, the rest of R01-R12 (plan p. 15): R09, R10
and R12.

Each test is named by its plan ID; docs/acceptance.md is the matrix. The
ones marked `needs_pg` need a throwaway Postgres (ATLAS_TEST_PG_DSN) and are
skipped without it -- a skip is not a pass.
"""
from __future__ import annotations

import dataclasses
import time

import httpx
import pytest

from atlas.kernel import approvals, idempotency, outbox, restore, state, verify
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

# Fixtures. pytest registers them by their own names; the aliases keep
# the module names apart from the test parameters that request them.
from tests.acceptance_support import (
    CARD,
    PUBLISH,
    StubWorld,
    drain_background,
    install_stub,
    media_params,
    needs_pg,
    outbox_rows,
    pg_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
    slack_fixture,  # noqa: F401
    slack_message,
    workflows,
)
from tests.test_acceptance_domain import _knowledge
from tests.test_acceptance_domain_rest import (
    drafts_world as drafts_world,  # noqa: PLC0414
)

# -------------------------------------------------------------------- R09
# Auth revoked or a scope missing: the capability is blocked -- no retry
# storm, no alternate identity -- and it is classed as an auth failure.

REVOKED = "HTTP 401: invalid_auth (the token was revoked)"
STUBAUTH = "stubauth.post"


def _revoked(runs: list[str]) -> None:
    async def run(target: str) -> ActionResult:
        runs.append(target)
        return ActionResult(False, REVOKED)

    async def dry(target: str) -> ActionResult:
        return ActionResult(True, f"would post to {target}")

    async def undo(_token: str) -> ActionResult:
        return ActionResult(True, "undone")

    action = Action(name=STUBAUTH, tier=Tier.AUTO, summary="stub post", run=run,
                    dry_run=dry, rollback=undo, params={"target": "where"},
                    max_per_hour=100)
    REGISTRY.actions[STUBAUTH] = action
    REGISTRY.decisions[STUBAUTH] = classify(action)


@pytest.mark.parametrize("status", [401, 403])
def test_R09_a_revoked_or_unscoped_credential_is_classed_auth(status):
    request = httpx.Request("GET", "https://provider.test/v1/me")
    exc = httpx.HTTPStatusError("refused", request=request,
                                response=httpx.Response(status, request=request))
    assert verify.classify(exc) is verify.FailureClass.AUTH
    # Not transient: a transient class would be the one retried with backoff.
    assert verify.classify(exc) is not verify.FailureClass.TRANSIENT


@needs_pg
async def test_R09_an_auth_refusal_is_never_resent_blind_or_as_someone_else(pg):
    runs: list[str] = []
    _revoked(runs)
    got = await outbox.enqueue(STUBAUTH, {"target": "C0VPSIE"}, destination="slack",
                               occurrence="R09:effect", approved_by="slack:UMIRO")
    first = await outbox.dispatch_once(only_id=got.outbox_id)
    assert first.status == "reconciling" and REVOKED in first.detail
    for _ in range(3):                       # later passes find nothing due
        await outbox.dispatch()
    settled = await outbox.reconcile_once()
    assert settled.status == "needs_human"   # no reconciler: a person checks it
    assert runs == ["C0VPSIE"]               # one attempt, never resent blind
    [row] = await outbox_rows()
    assert row["status"] == "needs_human"
    assert row["approved_by"] == "slack:UMIRO"      # no alternate identity


@pytest.fixture(name="told")
def told_fixture(monkeypatch):
    """What reached Miro through comms.notify."""
    said: list = []

    async def notify(msg):
        said.append(msg)
        return ["test"]

    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    return said


@needs_pg
async def test_R09_a_revoked_token_blocks_the_capability_with_no_retry_storm(
        pg, told):
    runs: list[str] = []
    _revoked(runs)
    for n in range(3):
        await outbox.enqueue(STUBAUTH, {"target": f"C0{n}"}, destination="slack",
                             occurrence=f"R09:notice:{n}", kind="notice",
                             approved_by="slack:UMIRO")
    for _ in range(8):                       # a day of scheduler passes
        await state.execute_sql(
            "UPDATE outbox SET next_attempt_at = now() WHERE status = 'pending'")
        await outbox.dispatch(limit=10)
    assert len(runs) == 1, f"{len(runs)} calls with a revoked token"
    # The capability is blocked: its module is paused, by ATLAS, with a
    # reason that names the status and never the provider's words.
    [pause] = await state.fetch_all(
        "SELECT scope, paused_by, reason FROM pauses WHERE resumed_at IS NULL")
    assert pause["scope"] == "stubauth" and pause["paused_by"] == "atlas:auth-refused"
    assert "HTTP 401" in pause["reason"] and "invalid_auth" not in pause["reason"]
    # The refused notice ends; its siblings hold, unspent, behind the pause.
    rows = await outbox_rows()
    assert [r["status"] for r in rows] == ["failed", "pending", "pending"]
    assert [r["attempts"] for r in rows] == [1, 0, 0]
    # One short notice, however many passes ran.
    [msg] = told
    assert msg.body == ("Paused stubauth: stubauth.post's login was refused. "
                        "Reconnect it, then `/atlas resume stubauth`.")
    assert REVOKED not in msg.body


@needs_pg
async def test_R09_after_reconnect_and_resume_the_held_rows_go_once(
        pg, told, registries):
    """Positive control: the pause is a normal one. Resumed by Miro, with the
    login fixed, the held siblings are each sent once."""
    from atlas.kernel import pause
    from atlas.kernel.registry import REGISTRY
    runs: list[str] = []
    _revoked(runs)
    for n in range(3):
        await outbox.enqueue(STUBAUTH, {"target": f"C0{n}"}, destination="slack",
                             occurrence=f"R09:resume:{n}", kind="notice",
                             approved_by="slack:UMIRO")
    await outbox.dispatch(limit=10)
    assert runs == ["C00"]

    async def fixed(target: str) -> ActionResult:
        runs.append(target)
        return ActionResult(True, f"posted to {target}", evidence={"ts": target})

    REGISTRY.actions[STUBAUTH] = dataclasses.replace(REGISTRY.actions[STUBAUTH],
                                                     run=fixed)
    REGISTRY.modules["stubauth"] = object()
    assert "Resumed" in await pause.resume("stubauth", "slack:UMIRO")
    await state.execute_sql(
        "UPDATE outbox SET next_attempt_at = now() WHERE status = 'pending'")
    done = await outbox.dispatch(limit=10)
    assert [d.status for d in done] == ["delivered", "delivered"]
    assert runs == ["C00", "C01", "C02"]
    assert len(told) == 1


@needs_pg
async def test_R09_an_ordinary_failure_still_backs_off_and_pauses_nothing(
        pg, told):
    """Positive control: a notice that failed for another reason (a 500) is
    retried with backoff, as before, and no module is paused."""
    runs: list[str] = []
    _revoked(runs)

    async def broken(target: str) -> ActionResult:
        runs.append(target)
        return ActionResult(False, "HTTP 500 from the provider")

    REGISTRY.actions[STUBAUTH] = dataclasses.replace(REGISTRY.actions[STUBAUTH],
                                                     run=broken)
    got = await outbox.enqueue(STUBAUTH, {"target": "C0X"}, destination="slack",
                               occurrence="R09:500", kind="notice")
    done = await outbox.dispatch_once(only_id=got.outbox_id)
    assert done.status == "pending"
    row = await state.fetch_one(
        "SELECT next_attempt_at > now() AS later FROM outbox WHERE id = $1",
        got.outbox_id)
    assert row["later"]
    assert await state.fetch_all("SELECT id FROM pauses") == []
    assert told == []


@needs_pg
async def test_R09_an_auth_refusal_pauses_only_its_own_module(pg, told):
    """Another module keeps dispatching while the refused one is paused."""
    runs: list[str] = []
    _revoked(runs)
    world = StubWorld()
    install_stub(world)
    bad = await outbox.enqueue(STUBAUTH, {"target": "C0"}, destination="slack",
                               occurrence="R09:mod", kind="notice")
    await outbox.dispatch_once(only_id=bad.outbox_id)
    good = await outbox.enqueue(PUBLISH, {**media_params(90), "platform": "instagram"},
                                destination="instagram", occurrence="R09:other")
    done = await outbox.dispatch_once(only_id=good.outbox_id)
    assert done.status == "delivered" and world.count() == 1


# R09 per credential: Instagram and YouTube post as several accounts, each
# with its own token. A refused login pauses that account only.

IGSTUB = "instagram.stubpost"


def _accounts(runs: list[tuple[str, str]], dead: set[str]) -> None:
    """A stub Instagram action whose `dead` accounts' tokens are revoked."""
    async def run(target: str, account: str = "main") -> ActionResult:
        # Like instagram.publish: the account defaults to main.
        account = account or "main"
        runs.append((account, target))
        if account in dead:
            return ActionResult(False, REVOKED)
        return ActionResult(True, f"posted as {account}", evidence={"id": target})

    async def dry(target: str, account: str = "main") -> ActionResult:
        return ActionResult(True, "would post")

    async def undo(_token: str) -> ActionResult:
        return ActionResult(True, "undone")

    action = Action(name=IGSTUB, tier=Tier.AUTO, summary="stub ig post", run=run,
                    dry_run=dry, rollback=undo,
                    params={"account": "which account", "target": "where"},
                    max_per_hour=100)
    REGISTRY.actions[IGSTUB] = action
    REGISTRY.decisions[IGSTUB] = classify(action)
    REGISTRY.modules.setdefault("instagram", object())


async def _queue_ig(account: str, n: int) -> int:
    got = await outbox.enqueue(IGSTUB, {"account": account, "target": f"{account}{n}"},
                               destination=f"instagram:{account}",
                               occurrence=f"R09:ig:{account}:{n}", kind="notice")
    return got.outbox_id


async def _pass() -> None:
    await state.execute_sql(
        "UPDATE outbox SET next_attempt_at = now() WHERE status = 'pending'")
    await outbox.dispatch(limit=20)


@needs_pg
async def test_R09_a_401_on_one_account_pauses_only_that_account(
        pg, told, registries):
    runs: list[tuple[str, str]] = []
    _accounts(runs, dead={"clips"})
    for n in range(2):
        for account in ("clips", "main", "flycheap"):
            await _queue_ig(account, n)
    for _ in range(4):
        await _pass()
    [row] = await state.fetch_all(
        "SELECT scope, paused_by FROM pauses WHERE resumed_at IS NULL")
    assert row["scope"] == "instagram:clips"
    assert row["paused_by"] == "atlas:auth-refused"
    # The dead account was tried once; the others' posts all went.
    assert [r for r in runs if r[0] == "clips"] == [("clips", "clips0")]
    assert sorted(r for r in runs if r[0] != "clips") == [
        ("flycheap", "flycheap0"), ("flycheap", "flycheap1"),
        ("main", "main0"), ("main", "main1")]
    held = await state.fetch_all(
        """SELECT status, attempts FROM outbox
            WHERE params->>'account' = 'clips' ORDER BY id""")
    assert [(r["status"], r["attempts"]) for r in held] == [("failed", 1),
                                                            ("pending", 0)]
    [msg] = told
    assert msg.body == ("Paused instagram:clips: instagram.stubpost's login was "
                        "refused. Reconnect it, then `/atlas resume instagram:clips`.")


@needs_pg
async def test_R09_a_plain_module_pause_still_blocks_every_account(
        pg, told, registries):
    from atlas.kernel import pause
    runs: list[tuple[str, str]] = []
    _accounts(runs, dead=set())
    await pause.pause("instagram", "slack:UMIRO", "hold everything")
    for account in ("clips", "main", "flycheap"):
        await _queue_ig(account, 0)
    await _pass()
    assert runs == []
    assert await pause.active_for("instagram", "main") is not None


@needs_pg
async def test_R09_resuming_the_scoped_pause_sends_the_held_rows(
        pg, told, registries):
    from atlas.kernel import pause
    runs: list[tuple[str, str]] = []
    _accounts(runs, dead={"clips"})
    for n in range(3):
        await _queue_ig("clips", n)
    await _pass()
    assert runs == [("clips", "clips0")]
    # Said in the brief and the weekly while it waits for him.
    assert await pause.lines() == [
        next(p for p in await pause.active()).says()]
    assert "instagram module's clips account is paused" in (await pause.lines())[0]
    _accounts(runs, dead=set())                  # he reconnected the login
    said = await pause.command("resume instagram:clips", "slack:UMIRO")
    assert said == "Resumed the instagram module's clips account."
    await _pass()
    assert runs == [("clips", "clips0"), ("clips", "clips1"), ("clips", "clips2")]
    assert await pause.lines() == []


@needs_pg
async def test_R09_a_module_with_several_unnamed_logins_is_told_not_paused(
        pg, told, registries):
    """content's publisher posts through several logins and no param says
    which one failed: a refused login is told once, and nothing is paused."""
    runs: list[str] = []
    _revoked(runs)
    spec = REGISTRY.actions.pop(STUBAUTH)
    name = "content.stubpost"
    REGISTRY.actions[name] = dataclasses.replace(spec, name=name)
    REGISTRY.decisions[name] = classify(REGISTRY.actions[name])
    for n in range(3):
        await outbox.enqueue(name, {"target": f"C{n}"}, destination="x",
                             occurrence=f"R09:content:{n}", kind="notice")
    await _pass()
    await _pass()
    assert await state.fetch_all("SELECT id FROM pauses") == []
    assert len(runs) == 3                        # one try each, never a storm
    assert [r["status"] for r in await outbox_rows()] == ["failed"] * 3
    [msg] = told
    assert "didn't pause content" in msg.body


@pytest.mark.parametrize("value", ["../clips", "clips;main", "clips main",
                                   "CLIPS ", 7, ["clips"], {"a": "clips"}])
@needs_pg
async def test_R09_a_malformed_account_cannot_slip_past_a_scoped_pause(
        pg, registries, value):
    """The executor validates param NAMES, not values, so a malformed account
    reaches the pause lookup. There it matches every account-scoped pause of
    the module (fail closed): with instagram:clips paused, nothing runs."""
    from atlas.kernel import executor as ex
    from atlas.kernel import pause
    runs: list[tuple[str, str]] = []
    _accounts(runs, dead=set())
    await pause.pause("instagram:clips", "atlas:auth-refused", "login refused")
    out = await ex.execute(ex.ExecutionRequest(
        IGSTUB, {"account": value, "target": "x"}, "R09 malformed"))
    assert not out.ok and out.dispatched is False, out.detail
    assert "clips account is paused" in out.detail
    assert out.evidence.get(ex.HOLD) == "paused"
    assert runs == []


@needs_pg
async def test_R09_a_well_formed_other_account_still_runs_past_a_scoped_pause(
        pg, registries):
    """Positive control: flycheap is not clips; an empty or absent account
    is the module's own default (main), exactly as instagram.publish reads
    it (and a REQUIRED account param left blank is refused by the
    executor's missing-parameter check before the pause lookup)."""
    from atlas.kernel import executor as ex
    from atlas.kernel import pause
    runs: list[tuple[str, str]] = []
    _accounts(runs, dead=set())
    await pause.pause("instagram:clips", "atlas:auth-refused", "login refused")
    for value in ("flycheap", "", None):
        out = await ex.execute(ex.ExecutionRequest(
            IGSTUB, {"account": value, "target": "x"}, "R09 control"))
        assert out.ok, (value, out.detail)
    assert len(runs) == 3
    await pause.pause("instagram:main", "atlas:auth-refused", "login refused")
    out = await ex.execute(ex.ExecutionRequest(
        IGSTUB, {"account": "", "target": "x"}, "R09 default"))
    assert not out.ok and "main account is paused" in out.detail


def test_R09_which_credential_each_action_names():
    from atlas.kernel import pause
    assert pause.credential_scope("instagram.publish", {"account": "clips"}) == \
        "instagram:clips"
    assert pause.credential_scope("instagram.publish", {}) == "instagram:main"
    assert pause.credential_scope("youtube.upload", {"channel": "guardian"}) == \
        "youtube:guardian"
    assert pause.credential_scope("youtube.upload", {}) is None    # unknown: tell
    assert pause.credential_scope("shop.create_payment_link",
                                  {"account": "flycheap"}) == "shop:flycheap"
    assert pause.credential_scope("content.autopublish_reel", {}) is None
    # An unreadable account is never a scope to pause, only to be stopped by.
    assert pause.account_of("instagram.publish", {"account": "../clips"}) == \
        pause.ANY_ACCOUNT
    assert pause.credential_scope("instagram.publish", {"account": "../x"}) is None
    assert pause.account_of("vpsie.restart_instance", {"account": "../x"}) is None
    assert pause.credential_scope("vpsie.restart_instance", {}) == "vpsie"
    # A scoped form never names a shadow row, from Slack or anywhere.
    assert pause.parse("resume shadow:all") is None
    assert pause.parse("resume instagram:clips") == ("resume", "instagram:clips", "")


# -------------------------------------------------------------------- R10
# Queue overload or a content-generation burst: backpressure, and the
# interactive work keeps its target.

async def test_R10_a_draft_burst_meets_backpressure_and_his_own_ask_still_drafts(
        drafts_world):
    from atlas.modules.content import drafts
    made = [await drafts.draft("short_script", f"weekly:{n}") for n in range(12)]
    assert sum(r.ok for r in made) == 3          # DRAFTS_BACKLOG_CAP=3
    assert all("cap is 3" in r.detail for r in made[3:])
    assert len(drafts_world.thought) == 3        # the refused nine cost nothing
    # What he asks for himself is never held back by the automatic backlog.
    for source in ("voice:42", "slack:C0MIRO/1700000000.1"):
        assert (await drafts.draft("short_script", source)).ok
    assert drafts_world.made[-2:] == ["voice:42", "slack:C0MIRO/1700000000.1"]


@needs_pg
async def test_R10_a_publishing_burst_is_dispatched_in_bounded_passes(pg):
    world = StubWorld()
    install_stub(world)
    for n in range(25):
        await outbox.enqueue(PUBLISH, {**media_params(100 + n), "platform": "instagram"},
                             destination="instagram", occurrence=f"R10:{n}")
    done = await outbox.dispatch(limit=10)
    assert len(done) == 10 and world.count() == 10
    row = await state.fetch_one(
        "SELECT count(*) AS n FROM outbox WHERE status = 'pending'")
    assert row["n"] == 15                        # waiting, not dropped


@needs_pg
async def test_R10_a_question_is_answered_on_time_behind_a_full_outbox(
        pg, slack, monkeypatch):
    post_event, replies = slack
    world = StubWorld()
    install_stub(world)
    for n in range(40):
        await outbox.enqueue(PUBLISH, {**media_params(200 + n), "platform": "tiktok"},
                             destination="tiktok", occurrence=f"R10q:{n}")
    _knowledge(monkeypatch)
    started = time.monotonic()
    await post_event(slack_message("atlas, what is the boiling point of water?",
                                   event_id="EvR10"))
    await drain_background()
    assert time.monotonic() - started < 10       # its own bound, not the queue's
    [reply] = replies
    assert "100 °C" in reply.body
    [wf] = await workflows("slack.event")
    assert wf["state"] == "SUCCEEDED"
    # The backlog was neither drained by nor in front of the question.
    assert world.count() == 0
    row = await state.fetch_one(
        "SELECT count(*) AS n FROM outbox WHERE status = 'pending'")
    assert row["n"] == 40


# -------------------------------------------------------------------- R12
# Restoring older state with external effects already present: reconcile
# before dispatch. The restore drill (docs/restore-drill.md) lists what must
# be reconciled; these are what the runtime does with such rows.

async def _restored_row(world: StubWorld, media_id: int, status: str) -> int:
    """An outbox row as the dump holds it, for a post that landed on the
    platform after the dump was taken."""
    got = await outbox.enqueue(PUBLISH, {**media_params(media_id),
                                         "platform": "instagram"},
                               destination="instagram",
                               occurrence=f"R12:{media_id}")
    world.posted.setdefault("instagram", []).append(f"instagram-{media_id}-1")
    if status == "dispatching":
        await state.execute_sql(
            """UPDATE outbox SET status = 'dispatching', lease_owner = 'old-host',
                      lease_expiry = now() - interval '1 hour', attempts = 1,
                      dispatched_at = now() - interval '1 hour'
                WHERE id = $1""", got.outbox_id)
    return got.outbox_id


@needs_pg
async def test_R12_a_restored_in_flight_dispatch_is_reconciled_not_resent(pg):
    world = StubWorld()
    install_stub(world)
    oid = await _restored_row(world, 71, "dispatching")
    await outbox.dispatch()                      # the first pass after the restore
    assert world.count() == 0
    [row] = await outbox_rows()
    assert row["status"] == "reconciling"
    settled = await outbox.reconcile_once()
    assert settled.outbox_id == oid and settled.status == "delivered"
    assert settled.evidence == {"post_id": "instagram-71-1"}
    await outbox.dispatch()
    assert world.count() == 0                    # read back, never re-posted


@needs_pg
async def test_R12_a_restored_claim_left_sending_is_in_doubt_not_resent(pg):
    await state.execute_sql(
        """INSERT INTO effect_claims (action, key, status, claimed_at)
           VALUES ('instagram.reply', 'outbox:9', 'sending',
                   now() - interval '1 day')""")
    assert await idempotency.claim("instagram.reply", "outbox:9") == idempotency.IN_DOUBT
    assert "check by hand" in idempotency.refusal("instagram.reply",
                                                  idempotency.IN_DOUBT)


# The fence (kernel/restore.py) is the real restore procedure's step: right
# after pg_restore it records a `restores` row, and the kernel fences it
# before anything is dispatched or decided. `restore.mark` is that step.

@needs_pg
async def test_R12_a_restored_pending_intent_is_reconciled_before_dispatch(pg):
    world = StubWorld()
    install_stub(world)
    oid = await _restored_row(world, 72, "pending")
    await restore.mark("atlas-20260928-0620.dump", "test")     # the procedure
    await outbox.dispatch()
    assert world.count() == 0, "a post that already landed was posted again"
    [row] = await outbox_rows()
    assert row["status"] == "reconciling" and "restored from a backup" in row["detail"]
    settled = await outbox.reconcile_once()          # read back, not resent
    assert settled.outbox_id == oid and settled.status == "delivered"
    assert settled.evidence == {"post_id": "instagram-72-1"}
    await outbox.dispatch()
    assert world.count() == 0


@needs_pg
async def test_R12_a_restored_claim_is_fenced_and_nothing_is_claimed_before_it(pg):
    """A row restored as `claimed` (a live lease in the dump) is fenced too,
    and while the marker is unfenced not even an inline dispatch of one row
    can claim anything."""
    world = StubWorld()
    install_stub(world)
    oid = await _restored_row(world, 74, "pending")
    await state.execute_sql(
        """UPDATE outbox SET status = 'claimed', lease_owner = 'old-host',
                  lease_expiry = now() + interval '1 hour' WHERE id = $1""", oid)
    await restore.mark("atlas-x.dump", "test")
    await state.execute_sql(
        "UPDATE outbox SET status = 'pending', lease_expiry = NULL WHERE id = $1",
        oid)                                          # due, as far as a claim sees
    assert await outbox.dispatch_once(only_id=oid) is None     # guard in the claim
    assert world.count() == 0
    await state.execute_sql(
        """UPDATE outbox SET status = 'claimed', lease_owner = 'old-host',
                  lease_expiry = now() + interval '1 hour' WHERE id = $1""", oid)
    got = await restore.apply()
    assert [r["id"] for r in got.outbox] == [oid]
    row = await state.fetch_one(
        "SELECT status, lease_owner, lease_expiry FROM outbox WHERE id = $1", oid)
    assert row["status"] == "reconciling"
    assert row["lease_owner"] is None and row["lease_expiry"] is None
    assert (await outbox.reconcile_once()).status == "delivered"
    assert world.count() == 0


@needs_pg
async def test_R12_after_the_fence_new_work_runs_and_a_second_fence_is_a_no_op(pg):
    """Positive control: what is queued after the restore dispatches as
    normal, and fencing again changes nothing."""
    world = StubWorld()
    install_stub(world)
    await restore.mark("atlas-x.dump", "test")
    first = await restore.apply()
    assert first.restores == 1 and first.outbox == [] and first.cards == []
    assert (await restore.apply()).restores == 0
    got = await outbox.enqueue(PUBLISH, {**media_params(75), "platform": "instagram"},
                               destination="instagram", occurrence="R12:after")
    done = await outbox.dispatch_once(only_id=got.outbox_id)
    assert done.status == "delivered" and world.count() == 1
    pid = await approvals.propose(CARD, media_params(76), "reel card",
                                  dedup_key="R12:after")
    verdict = await approvals.decide(pid, "approve", "slack:UMIRO")
    assert verdict.outcome == "executed" and len(world.runs) == 2


@needs_pg
async def test_R12_a_restored_pending_card_is_not_run_again_blindly(pg):
    world = StubWorld()
    install_stub(world)
    params = media_params(73)
    pid = await approvals.propose(CARD, params, "reel card", dedup_key="R12")
    world.posted.setdefault("card", []).append("card-73")    # ran after the dump
    await restore.mark("atlas-20260928-0620.dump", "test")     # the procedure
    verdict = await approvals.decide(pid, "approve", "slack:UMIRO")
    assert world.runs == [], "a card that already ran was run again"
    assert not verdict.ok and "backup restore" in verdict.detail
    row = await state.fetch_one("SELECT status FROM proposals WHERE id = $1", pid)
    assert row["status"] == "held"
    again = await approvals.decide(pid, "approve", "slack:UMIRO")     # still no
    assert not again.ok and "can't be approved" in again.detail
    assert world.runs == []


@needs_pg
async def test_R12_a_held_card_can_be_declined_to_resolve_it(pg):
    """Declining a held card resolves it (no effect can follow a no); it is
    then declined, never approvable."""
    world = StubWorld()
    install_stub(world)
    pid = await approvals.propose(CARD, media_params(78), "reel card",
                                  dedup_key="R12:decline")
    await restore.mark("atlas-x.dump", "test")
    await restore.apply()
    # Held even after its expiry: declining still resolves it.
    await state.execute_sql(
        "UPDATE proposals SET expires_at = now() - interval '1 hour' WHERE id = $1",
        pid)
    said = await approvals.decide(pid, "decline", "slack:UMIRO")
    assert said.ok and said.outcome == "declined"
    row = await state.fetch_one("SELECT status FROM proposals WHERE id = $1", pid)
    assert row["status"] == "declined"
    after = await approvals.decide(pid, "approve", "slack:UMIRO")
    assert not after.ok and world.runs == []


@needs_pg
async def test_R12_an_unreadable_fence_dispatches_nothing(pg, monkeypatch):
    world = StubWorld()
    install_stub(world)
    await outbox.enqueue(PUBLISH, {**media_params(77), "platform": "instagram"},
                         destination="instagram", occurrence="R12:unreadable")

    async def unreadable():
        raise restore.FenceUnreadable("db gone")

    monkeypatch.setattr(restore, "apply", unreadable)
    assert await outbox.dispatch() == []
    assert world.count() == 0


@needs_pg
async def test_R12_a_missing_restores_table_sends_and_runs_nothing(pg):
    """Fail closed: with the `restores` table gone (a schema that was never
    applied, or a broken restore), no outbox row is claimed or sent and no
    card is approved. `init-db` (run by ops/deploy/deploy.sh before the new
    container starts) creates it again, and then both work."""
    from atlas import cli
    world = StubWorld()
    install_stub(world)
    got = await outbox.enqueue(PUBLISH, {**media_params(79), "platform": "instagram"},
                               destination="instagram", occurrence="R12:missing")
    pid = await approvals.propose(CARD, media_params(80), "reel card",
                                  dedup_key="R12:missing")
    await state.execute_sql("DROP TABLE restores")
    try:
        assert await outbox.dispatch() == []
        assert await outbox.dispatch_once(only_id=got.outbox_id) is None
        verdict = await approvals.decide(pid, "approve", "slack:UMIRO")
        assert not verdict.ok and "restored" in verdict.detail
        assert world.runs == []
        row = await state.fetch_one("SELECT status FROM proposals WHERE id = $1", pid)
        assert row["status"] == "pending"           # not consumed by the refusal
    finally:
        await cli.init_db()
    assert (await outbox.dispatch_once(only_id=got.outbox_id)).status == "delivered"
    assert (await approvals.decide(pid, "approve", "slack:UMIRO")).outcome == "executed"
