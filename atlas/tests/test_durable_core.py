"""The durable core: workflow record, outbox, leases (RA-B1/B3/B4).

No database here: the database's answers are scripted, the way
test_reap_orphans.py does it, so these tests pin the Python half -- the
state machine, the fail-closed branches, the outcome classification, the
flag -- and check that each guarantee sits in the same SQL statement as the
write it guards. The SQL itself is proven against a real Postgres in
test_durable_pg.py (opt-in, ATLAS_TEST_PG_DSN).
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import durable, executor, leases, outbox, scheduler, state
from atlas.kernel.contracts import ActionResult, FailureClass
from atlas.kernel.durable import IllegalTransition, State

ROOT = pathlib.Path(__file__).resolve().parent.parent
KERNEL = ROOT / "atlas" / "kernel"
S = State
TTL = timedelta(minutes=5)


class Script:
    """A scripted database: each call pops the next answer for the first
    fragment of its SQL that matches, and records what was asked."""

    def __init__(self, answers: list[tuple[str, object]] | None = None,
                 fail: bool = False) -> None:
        self.answers = list(answers or [])
        self.fail = fail
        self.sql: list[tuple[str, tuple]] = []

    async def fetch_one(self, sql: str, *args):
        flat = " ".join(sql.split())
        self.sql.append((flat, args))
        if self.fail:
            raise ConnectionError("database unavailable")
        for i, (fragment, value) in enumerate(self.answers):
            if fragment in flat:
                self.answers.pop(i)
                if isinstance(value, BaseException):
                    raise value
                return value
        return None

    async def fetch_all(self, sql: str, *args):
        flat = " ".join(sql.split())
        self.sql.append((flat, args))
        if self.fail:
            raise ConnectionError("database unavailable")
        return []


@pytest.fixture
def db(monkeypatch, memory_backend):
    def install(script: Script) -> Script:
        monkeypatch.setattr(state, "fetch_one", script.fetch_one)
        monkeypatch.setattr(state, "fetch_all", script.fetch_all)
        return script
    return install


@pytest.fixture
def executed(monkeypatch):
    """Record every ExecutionRequest the outbox hands the executor; answer
    with whatever the test queues (default: ok with evidence)."""
    calls: list = []
    answers: list = []

    async def fake_execute(req):
        calls.append(req)
        answer = answers.pop(0) if answers else ActionResult(
            True, "done", evidence={"id": "x1"})
        if isinstance(answer, BaseException):
            raise answer
        return answer

    from atlas.kernel import executor
    monkeypatch.setattr(executor, "execute", fake_execute)
    return calls, answers


# ----------------------------------------------------------- the machine

PLAN_ARROWS = [
    (S.RECEIVED, S.VALIDATING), (S.VALIDATING, S.READY),
    (S.READY, S.WAITING_APPROVAL), (S.READY, S.PREPARING),
    (S.PREPARING, S.WAITING_APPROVAL),
    (S.READY, S.RUNNING), (S.PREPARING, S.RUNNING),
    (S.WAITING_APPROVAL, S.READY),
    (S.RUNNING, S.VERIFYING), (S.VERIFYING, S.SUCCEEDED),
    (S.RUNNING, S.RETRY_WAIT), (S.RETRY_WAIT, S.READY),
    (S.RUNNING, S.RECONCILING),
    (S.RUNNING, S.FAILED), (S.RECONCILING, S.NEEDS_HUMAN),
    (S.WAITING_APPROVAL, S.DECLINED), (S.WAITING_APPROVAL, S.EXPIRED),
    (S.RUNNING, S.CANCEL_REQUESTED), (S.CANCEL_REQUESTED, S.CANCELLED),
    (S.VALIDATING, S.BLOCKED), (S.RUNNING, S.BLOCKED),
]


@pytest.mark.parametrize(("src", "dst"), PLAN_ARROWS)
def test_every_arrow_in_the_plan_is_permitted(src, dst):
    assert durable.allowed(src, dst)


@pytest.mark.parametrize(("src", "dst"), [
    (S.RECEIVED, S.RUNNING),            # skips validation and authorization
    (S.WAITING_APPROVAL, S.RUNNING),    # approval must pass through READY
    (S.RETRY_WAIT, S.RUNNING),
    (S.RECONCILING, S.READY),           # never straight back to a resend
    (S.RECONCILING, S.SUCCEEDED),       # a found effect is verified first
    (S.CANCEL_REQUESTED, S.SUCCEEDED),
    (S.READY, S.DECLINED),              # only a pending proposal declines
    (S.RUNNING, S.EXPIRED),
    (S.SUCCEEDED, S.RUNNING), (S.CANCELLED, S.READY), (S.EXPIRED, S.READY),
    (S.DECLINED, S.READY), (S.FAILED, S.RETRY_WAIT),
    (S.RUNNING, S.RUNNING),
])
def test_anything_else_is_illegal(src, dst):
    assert not durable.allowed(src, dst)
    with pytest.raises(IllegalTransition):
        durable.check(src, dst)


def test_terminal_states_go_nowhere():
    for s in durable.TERMINAL:
        assert durable.TRANSITIONS[s] == frozenset()


async def test_an_illegal_transition_raises_before_any_sql(db):
    script = db(Script())
    with pytest.raises(IllegalTransition):
        await durable.transition(1, "RECEIVED", "SUCCEEDED")
    with pytest.raises(IllegalTransition):
        await durable.transition(1, "WAITING_APPROVAL", "READY")
    assert script.sql == []


async def test_approval_is_recorded_only_from_a_claimed_proposal(db):
    script = db(Script([("UPDATE workflows w", None)]))
    assert not await durable.approved(3, "abc")
    [(sql, _)] = script.sql
    # One statement: the state it leaves AND the proposal's claim.
    assert "w.state = 'WAITING_APPROVAL'" in sql
    assert "p.status IN ('approved', 'executed')" in sql
    with pytest.raises(ValueError):
        await durable.approved(3, "")


def test_the_proposal_claim_is_untouched():
    """Built beside decide(), not into it: the approvals module does not
    know the durable core exists."""
    text = (KERNEL / "approvals.py").read_text()
    assert "durable" not in text and "outbox" not in text
    assert "WHERE id=$1 AND status='pending' AND expires_at > now()" in text


# ------------------------------------------------------------------- flag

def test_the_flag_defaults_off_and_reads_each_call(monkeypatch):
    monkeypatch.delenv("ATLAS_DURABLE_CORE", raising=False)
    assert not durable.enabled()
    for on in ("on", "1", "true", "ON"):
        monkeypatch.setenv("ATLAS_DURABLE_CORE", on)
        assert durable.enabled()
    for off in ("off", "0", "", "no"):
        monkeypatch.setenv("ATLAS_DURABLE_CORE", off)
        assert not durable.enabled()


# -------------------------------------------------------------------- R01

async def test_r01_a_redelivered_event_finds_its_workflow(db):
    db(Script([("INSERT INTO workflows", None),
               ("SELECT id, state FROM workflows",
                {"id": 7, "state": "RUNNING"})]))
    got = await durable.receive("slack.message", "Ev1", payload={"text": "hi"})
    assert got == durable.Intake(7, False, S.RUNNING)


async def test_r01_the_payload_is_hashed_not_stored(db):
    script = db(Script([("INSERT INTO workflows", {"id": 1, "state": "RECEIVED"})]))
    got = await durable.receive("slack.message", "Ev1",
                                payload={"text": "a secret sentence"},
                                input_refs=["slack:event:Ev1"])
    assert got.created
    [(sql, args)] = script.sql
    assert "ON CONFLICT (workflow_type, correlation_id) DO NOTHING" in sql
    assert not any("a secret sentence" in str(a) for a in args)
    assert durable.digest({"text": "a secret sentence"}) in args


def test_r01_the_outbox_key_is_the_occurrence_never_the_text():
    k = outbox.idempotency_key("messages.reply", "1", "thread:9", "msg:41")
    assert k == outbox.idempotency_key("messages.reply", "1", "thread:9", "msg:41")
    for other in (("messages.reply", "2", "thread:9", "msg:41"),
                  ("messages.reply", "1", "thread:8", "msg:41"),
                  ("messages.reply", "1", "thread:9", "msg:42"),
                  ("messaging.reply", "1", "thread:9", "msg:41")):
        assert outbox.idempotency_key(*other) != k
    with pytest.raises(ValueError):
        outbox.idempotency_key("messages.reply", "1", "thread:9", " ")
    import inspect
    assert "text" not in inspect.signature(outbox.idempotency_key).parameters


async def test_r01_enqueueing_twice_returns_the_first_row(db):
    db(Script([("INSERT INTO outbox", None),
               ("SELECT id FROM outbox WHERE idempotency_key", {"id": 4})]))
    got = await outbox.enqueue("fake.send", {"to": "m"}, destination="m",
                               occurrence="1")
    assert got.outbox_id == 4 and not got.created


# -------------------------------------------------------------------- R03

def _claimed(**over):
    row = {"id": 5, "kind": "effect", "action": "fake.send", "params": '{"to": "m"}',
           "reason": "r", "approved_by": None, "incident_id": None,
           "workflow_id": None, "fencing_token": 3, "attempts": 0,
           "max_attempts": 5}
    row.update(over)
    return row


async def test_r03_the_claim_takes_pending_rows_and_lapsed_claims_only(db, executed):
    script = db(Script([("SET status = 'claimed'", _claimed()),
                        ("SET status = 'dispatching'", {"id": 5}),
                        ("SET status = $4", {"id": 5})]))
    done = await outbox.dispatch_once("worker-b")
    assert done.status == "delivered"
    claim = next(s for s, _ in script.sql if "SET status = 'claimed'" in s)
    # A 'claimed' row is re-claimable only once its lease lapsed; a
    # 'dispatching' one never is (that is R04's reconcile).
    assert "(status = 'claimed' AND lease_expiry <= now())" in claim
    assert "status = 'dispatching'" not in claim
    assert "fencing_token = fencing_token + 1" in claim


async def test_r03_a_lost_fence_means_nothing_is_dispatched(db, executed):
    calls, _ = executed
    db(Script([("SET status = 'claimed'", _claimed()),
               ("SET status = 'dispatching'", None)]))
    done = await outbox.dispatch_once("worker-a")
    assert done.status == "stale" and not done.committed
    assert calls == []


# -------------------------------------------------------------------- R04

async def test_r04_a_lapsed_dispatch_is_swept_to_reconciling(db):
    script = db(Script())
    await outbox.sweep_lapsed()
    [(sql, _)] = script.sql
    assert "SET status = 'reconciling'" in sql
    assert "WHERE status = 'dispatching' AND lease_expiry <= now()" in sql


@pytest.mark.parametrize("answer", [
    RuntimeError("provider socket closed"),
    TimeoutError(),
    ActionResult(False, "action raised: HTTP 502 from api.linkedin.com"),
    ActionResult(False, "the page did not confirm the post"),
])
async def test_r04_an_uncertain_effect_is_reconciled_never_retried(
        db, executed, answer):
    calls, answers = executed
    answers.append(answer)
    script = db(Script([("SET status = 'claimed'", _claimed()),
                        ("SET status = 'dispatching'", {"id": 5}),
                        ("SET status = $4", {"id": 5})]))
    done = await outbox.dispatch_once()
    assert done.status == "reconciling"
    commit = next(a for s, a in script.sql if "SET status = $4" in s)
    assert commit[3] == "reconciling" and commit[6] is None   # no retry time
    assert len(calls) == 1


async def test_r04_reconciliation_without_a_lookup_goes_to_a_person(db):
    db(Script([("WHERE status = 'reconciling'", _claimed()),
               ("SET status = $4", {"id": 5})]))
    done = await outbox.reconcile_once()
    assert done.status == "needs_human"


async def test_r04_a_lookup_that_finds_nothing_allows_one_more_dispatch(db):
    async def lookup(row):
        assert row["params"] == {"to": "m"}
        return "absent", {"checked": "provider list"}
    outbox.RECONCILERS["fake.send"] = lookup
    try:
        db(Script([("WHERE status = 'reconciling'", _claimed()),
                   ("SET status = $4", {"id": 5})]))
        done = await outbox.reconcile_once()
    finally:
        outbox.RECONCILERS.clear()
    assert done.status == "pending"


# -------------------------------------------------------------------- R05

async def test_r05_a_stale_commit_writes_nothing_and_is_audited(
        db, executed, memory_backend):
    db(Script([("SET status = 'claimed'", _claimed()),
               ("SET status = 'dispatching'", {"id": 5}),
               ("SET status = $4", None)]))
    done = await outbox.dispatch_once("worker-a")
    assert not done.committed and done.status == "delivered"
    events = [r.event for r in memory_backend.audit_rows]
    assert events == ["outbox.stale_commit"]


async def test_r05_every_fenced_write_checks_the_fence_in_the_same_statement(db):
    script = db(Script())
    fence = leases.Fence("workflow", "1", "w-a", 9)
    await durable.transition(1, "RECEIVED", "VALIDATING", fence=fence)
    await outbox._commit(_claimed(), "w-a", "delivered", "", {}, None)
    await outbox._fence_for_dispatch(_claimed(), "w-a")
    await leases.renew(leases.Fence("lease", "j", "w-a", 2), TTL)
    fenced = [s for s, _ in script.sql]
    assert len(fenced) == 4
    for sql in fenced:
        assert "fencing_token = $" in sql
        assert ("lease_expiry > now()" in sql) or ("expires_at > now()" in sql)


async def test_r05_a_lease_is_refused_while_anyone_holds_it(db):
    script = db(Script([("INSERT INTO leases", None)]))
    assert await leases.acquire("scheduler:review", TTL) is None
    [(sql, _)] = script.sql
    assert "WHERE leases.expires_at IS NULL OR leases.expires_at <= now()" in sql
    assert "fencing_token = leases.fencing_token + 1" in sql


def test_nothing_ever_deletes_a_lease_or_a_fenced_row():
    """A deleted row restarts its token at 1, and a stale holder of token 1
    would match again."""
    for name in ("leases.py", "durable.py", "outbox.py"):
        assert "DELETE" not in (KERNEL / name).read_text().upper().replace(
            "DELETED", "")


# -------------------------------------------------------------------- R06

async def test_r06_an_unreachable_database_accepts_nothing_and_runs_nothing(
        db, executed):
    calls, _ = executed
    db(Script(fail=True))
    with pytest.raises(durable.DurableUnavailable):
        await durable.receive("slack.message", "Ev1")
    with pytest.raises(durable.DurableUnavailable):
        await outbox.enqueue("fake.send", {}, destination="m", occurrence="1")
    with pytest.raises(durable.DurableUnavailable):
        await durable.transition(1, "RECEIVED", "VALIDATING")
    assert await outbox.dispatch_once() is None
    assert await outbox.dispatch() == []
    assert await outbox.reconcile_once() is None
    assert await leases.acquire("scheduler:review", TTL) is None
    assert not await leases.holds(leases.Fence("lease", "x", "o", 1))
    assert await durable.claim(1, TTL) is None
    assert calls == []


async def test_r06_losing_the_database_after_the_run_is_not_delivered(
        db, executed):
    calls, _ = executed
    db(Script([("SET status = 'claimed'", _claimed()),
               ("SET status = 'dispatching'", {"id": 5}),
               ("SET status = $4", ConnectionError("gone"))]))
    done = await outbox.dispatch_once()
    assert not done.committed and len(calls) == 1


# -------------------------------------------------------------------- R07

def test_r07_a_failed_notice_is_retried_and_a_failed_effect_is_not():
    failed = ActionResult(False, "slack answered 503")
    status, retry, _ = outbox._outcome("notice", failed, attempts=1,
                                       max_attempts=5)
    assert status == "pending" and retry is not None
    status, retry, _ = outbox._outcome("effect", failed, attempts=1,
                                       max_attempts=5)
    assert status == "reconciling" and retry is None
    status, _, _ = outbox._outcome("notice", failed, attempts=5, max_attempts=5)
    assert status == "failed"


@pytest.mark.parametrize("detail", [
    "ATLAS is in watch-only mode and did not run x.y.",
    "not run: everything is paused since Mon 28 Sep 04:00 UTC by miro",
])
def test_a_pause_or_watch_only_waits_and_never_spends_an_attempt(detail):
    """The plan: an intentionally disabled dependency is skipped as
    intended, not labelled a failure. A long pause must not turn queued
    intents into terminal failures."""
    refused = ActionResult(False, detail)
    for kind in ("effect", "notice"):
        status, retry, refund = outbox._outcome(kind, refused, attempts=99,
                                                max_attempts=5)
        assert status == "pending" and refund
        assert retry == outbox.DISABLED_RECHECK


def test_a_rate_ceiling_spends_attempts_and_ends():
    capped = ActionResult(False, "x.y has already run 3 times this hour; "
                                 "escalating instead of repeating")
    assert outbox._outcome("effect", capped, 2, 5)[:1] == ("pending",)
    assert outbox._outcome("effect", capped, 2, 5)[2] is False
    assert outbox._outcome("effect", capped, 5, 5)[0] == "failed"


async def test_a_pass_starts_no_row_it_could_not_finish(monkeypatch, db):
    db(Script())
    started: list = []

    async def once(owner=None, lease=None):
        started.append(1)
        return outbox.Dispatched(len(started), "fake.send", "delivered")
    monkeypatch.setattr(outbox, "dispatch_once", once)
    assert await outbox.dispatch(limit=5,
                                 budget_s=outbox.EXECUTE_BUDGET_S - 1) == []
    assert started == []
    done = await outbox.dispatch(limit=3, budget_s=outbox.EXECUTE_BUDGET_S * 10)
    assert len(done) == 3


def test_flag_off_the_step_set_is_unchanged():
    assert "outbox" not in scheduler._DETACHED
    # The flag adds only `outbox`; `agency_meeting` is a flag-independent
    # daily step (a model call for its polish), detached like the brief.
    assert scheduler._DETACHED == ("signals", "brief", "weekly", "retention",
                                   "judgment", "fixes", "agent_shifts",
                                   "agency_meeting", "playlist", "ideas",
                                   "approvals_board", "sales_shift",
                                   "content_weekly", "bizops_digest",
                                   "bizops_find_repeats", "seo_rank",
                                   "research_scan", "research_weekly",
                                   "inbox_shift", "sites_shift", "tutorco_shift",
                                   "whatsapp_daily",
                                   "sage_tutorco_weekly", "youtube_learn")


async def test_r07_a_notice_never_moves_its_workflow(db, executed):
    _, answers = executed
    answers.append(ActionResult(False, "slack answered 503"))
    script = db(Script([("SET status = 'claimed'",
                         _claimed(kind="notice", workflow_id=12)),
                        ("SET status = 'dispatching'", {"id": 5}),
                        ("SET status = $4", {"id": 5})]))
    done = await outbox.dispatch_once()
    assert done.status == "pending"
    assert not any("UPDATE workflows" in s for s, _ in script.sql)


# ------------------------------------------------- outcome classification

def test_evidence_is_what_makes_delivered():
    assert outbox._outcome("effect", ActionResult(True, "ok", evidence={"id": 1}),
                           1, 5)[0] == "delivered"
    assert outbox._outcome("effect", ActionResult(True, "ok"), 1, 5)[0] == \
        "unverified"


@pytest.mark.parametrize(("detail", "kind"), [
    ("ATLAS is in watch-only mode and did not run x.y. Unset ...", "disabled"),
    ("refused: the pause state could not be read, so nothing is dispatched",
     "transient"),
    ("not run: everything is paused since ...", "disabled"),
    ("Format research not run: no key", None),
    ("x.y has already run 3 times this hour; escalating instead of repeating",
     "transient"),
    (("the x module has already run 10 actions this hour; escalating instead "
      "of repeating"), "transient"),
    (("ATLAS has run 30 actions across everything this hour; escalating "
      "instead of repeating"), "transient"),
    ("no such action in the catalog", "permanent"),
    ("refused: permanently denied", "permanent"),
    ("tier 2 action reached the executor unapproved", "permanent"),
    ("dry run refused: would break", "permanent"),
    ("action raised: HTTP 500 from slack.com", None),
    ("the provider said no", None),
])
def test_only_the_executors_own_refusals_count_as_not_dispatched(detail, kind):
    assert outbox._pre_dispatch(detail) == kind


def test_the_refusal_strings_are_still_the_executors():
    """If executor.py rewords a refusal, this fails rather than the outbox
    quietly treating a refusal as a possible effect (or the reverse)."""
    text = (KERNEL / "executor.py").read_text()
    for prefix in (outbox.PRE_DISPATCH_DISABLED_PREFIXES
                   + outbox.PRE_DISPATCH_TRANSIENT_PREFIXES
                   + outbox.PRE_DISPATCH_PERMANENT_PREFIXES):
        assert prefix.strip() in text, prefix
    assert text.count(outbox.PRE_DISPATCH_TRANSIENT_SUFFIX.lstrip("; ")) >= 3


async def test_a_pre_dispatch_refusal_is_not_dispatched_by_its_flag(
        db, executed):
    """`dispatched=False` from execute: nothing reached a provider, so the
    row is failed (a policy refusal) -- never reconciled -- even when the
    wording matches none of PRE_DISPATCH_*."""
    calls, answers = executed
    answers.append(ActionResult(False, "wording nobody has matched",
                                failure_class=FailureClass.POLICY,
                                dispatched=False))
    script = db(Script([("SET status = 'claimed'", _claimed()),
                        ("SET status = 'dispatching'", {"id": 5}),
                        ("SET status = $4", {"id": 5})]))
    done = await outbox.dispatch_once()
    assert done.status == "failed" and len(calls) == 1
    commit = next(a for s, a in script.sql if "SET status = $4" in s)
    assert commit[3] == "failed"


async def test_a_disabled_refusal_by_its_flag_waits_without_an_attempt(
        db, executed):
    _, answers = executed
    answers.append(ActionResult(False, "held", evidence={executor.HOLD: "paused"},
                                failure_class=FailureClass.DISABLED,
                                dispatched=False))
    db(Script([("SET status = 'claimed'", _claimed()),
               ("SET status = 'dispatching'", {"id": 5}),
               ("SET status = $4", {"id": 5})]))
    assert (await outbox.dispatch_once()).status == "pending"


async def test_a_bare_disabled_class_without_the_marker_fails(db, executed):
    """Only the executor's own watch-only / pause refusal may wait."""
    _, answers = executed
    answers.append(ActionResult(False, "held", failure_class=FailureClass.DISABLED,
                                dispatched=False))
    db(Script([("SET status = 'claimed'", _claimed()),
               ("SET status = 'dispatching'", {"id": 5}),
               ("SET status = $4", {"id": 5})]))
    assert (await outbox.dispatch_once()).status == "failed"


@pytest.mark.parametrize("detail", [
    "action raised: HTTP 500 from slack.com",
    "not run: the page never loaded",          # worded like a pause
])
async def test_a_crash_after_invoke_is_uncertain_and_reconciled(
        db, executed, detail):
    """`dispatched=True`: run() was invoked and may have reached the
    provider, so the effect is reconciled, never resent -- even when its
    detail reads like one of the executor's refusals."""
    calls, answers = executed
    answers.append(ActionResult(False, detail, dispatched=True))
    script = db(Script([("SET status = 'claimed'", _claimed()),
                        ("SET status = 'dispatching'", {"id": 5}),
                        ("SET status = $4", {"id": 5})]))
    done = await outbox.dispatch_once()
    assert done.status == "reconciling" and len(calls) == 1
    commit = next(a for s, a in script.sql if "SET status = $4" in s)
    assert commit[3] == "reconciling" and commit[6] is None


# ------------------------------------------------- the outbox never sends

def test_the_outbox_sends_nothing_itself():
    tree = ast.parse((KERNEL / "outbox.py").read_text())
    imported = {n.module for n in ast.walk(tree)
                if isinstance(n, ast.ImportFrom) and n.module}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                 for a in n.names}
    assert not imported & {"comms", "channels", "httpx", "aiohttp", "requests"}
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else
              getattr(n.func, "id", "") for n in ast.walk(tree)
              if isinstance(n, ast.Call)}
    assert "execute" in called
    assert not called & {"notify", "send", "run", "post", "rollback"}


async def test_dispatch_goes_through_executor_execute(db, executed):
    calls, _ = executed
    db(Script([("SET status = 'claimed'",
                _claimed(approved_by="slack:U1", incident_id=4)),
               ("SET status = 'dispatching'", {"id": 5}),
               ("SET status = $4", {"id": 5})]))
    await outbox.dispatch_once()
    [req] = calls
    assert req.action == "fake.send" and req.params == {"to": "m"}
    assert req.approved_by == "slack:U1" and req.incident_id == 4


# ------------------------------------------------------ scheduler wire-in

@pytest.fixture
def quiet_loop(monkeypatch):
    async def noop() -> None:
        return None
    for name in ("_run_checks", "expire_stale", "_maybe_signals",
                 "_maybe_brief", "_maybe_weekly", "_maybe_marketing",
                 "_maybe_reels", "_maybe_calendar", "_maybe_clips", "_maybe_review",
                 "_maybe_rhythm", "_maybe_purge", "_maybe_tournament",
                 "_maybe_fixes", "_maybe_agent_shifts", "_maybe_agency_meeting",
                 "_outbox_pass"):
        monkeypatch.setattr(scheduler, name, noop)
    scheduler._tasks.clear()
    yield monkeypatch
    for task in scheduler._tasks.values():
        task.cancel()
    scheduler._tasks.clear()


async def _cycle(seconds: float = 0.1) -> None:
    task = asyncio.create_task(scheduler.loop(interval=0.01))
    try:
        await asyncio.sleep(seconds)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_flag_off_the_loop_takes_no_lease_and_has_no_outbox(quiet_loop):
    quiet_loop.delenv("ATLAS_DURABLE_CORE", raising=False)
    touched: list = []

    async def acquire(*a, **k):
        touched.append(a)
        raise AssertionError("a lease was taken with the flag off")

    async def outbox_pass():
        touched.append("outbox")
    quiet_loop.setattr(leases, "acquire", acquire)
    quiet_loop.setattr(scheduler, "_outbox_pass", outbox_pass)
    await _cycle()
    assert touched == []


async def test_flag_on_steps_run_under_a_lease_and_checks_do_not(quiet_loop):
    quiet_loop.setenv("ATLAS_DURABLE_CORE", "on")
    asked: list[str] = []
    ran: list[str] = []
    released: list[str] = []

    async def acquire(name, ttl, owner=leases.OWNER):
        asked.append(name)
        return leases.Fence("lease", name, owner, 1,
                            datetime.now(UTC) + ttl)

    async def release(fence):
        released.append(fence.key)
        return True

    async def review():
        ran.append("review")

    async def checks():
        ran.append("checks")

    async def outbox_pass():
        ran.append("outbox")
    quiet_loop.setattr(leases, "acquire", acquire)
    quiet_loop.setattr(leases, "release", release)
    quiet_loop.setattr(scheduler, "_maybe_review", review)
    quiet_loop.setattr(scheduler, "_run_checks", checks)
    quiet_loop.setattr(scheduler, "_outbox_pass", outbox_pass)
    await _cycle()
    assert "scheduler:review" in asked and "scheduler:outbox" in asked
    assert "scheduler:checks" not in asked
    assert "scheduler:approvals" not in asked
    assert {"review", "checks", "outbox"} <= set(ran)
    assert "scheduler:review" in released


async def test_flag_on_an_unheld_lease_skips_the_step_but_not_the_checks(
        quiet_loop):
    quiet_loop.setenv("ATLAS_DURABLE_CORE", "on")
    ran: list[str] = []

    async def acquire(name, ttl, owner=leases.OWNER):
        return None                         # held elsewhere, or unreadable

    async def review():
        ran.append("review")

    async def checks():
        ran.append("checks")
    quiet_loop.setattr(leases, "acquire", acquire)
    quiet_loop.setattr(scheduler, "_maybe_review", review)
    quiet_loop.setattr(scheduler, "_run_checks", checks)
    await _cycle()
    assert "review" not in ran and "checks" in ran


# ------------------------------------------------------------------ schema

def test_the_tables_are_in_the_schema_init_db_applies():
    text = (ROOT / "db" / "schema.sql").read_text()
    for table in ("workflows", "outbox", "leases"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in text
    assert "UNIQUE (workflow_type, correlation_id)" in text
    assert "idempotency_key TEXT NOT NULL UNIQUE" in text
    assert "SKIP LOCKED" not in text.upper()


def test_the_workflow_record_keeps_references_not_payloads():
    text = (ROOT / "db" / "schema.sql").read_text()
    body = text[text.index("CREATE TABLE IF NOT EXISTS workflows ("):]
    body = body[:body.index(");")]
    for column in ("input_refs", "input_hash", "approved_payload_hash",
                   "evidence_refs", "fencing_token", "lease_owner",
                   "lease_expiry", "provider_request_ids", "reconciliation_state",
                   "remaining_unknowns", "correlation_id", "next_attempt_at"):
        assert column in body
    for raw in ("payload JSONB", "body TEXT", "params JSONB"):
        assert raw not in body
