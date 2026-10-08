"""RA-G1 shadow mode: a shadowed action is never dispatched, is recorded
("would have executed X with params-hash H"), and shows in the report next
to what actually ran. Default: nothing is shadowed."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import executor, outbox, pause, shadow, state
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.executor import ExecutionRequest
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

PAUSE_READ = "FROM pauses WHERE resumed_at IS NULL AND scope"
T0 = datetime(2026, 9, 29, 9, 0, tzinfo=UTC)


def _row(scope: str) -> dict:
    return {"scope": scope, "paused_at": T0, "paused_by": "cli", "reason": "G1"}


@pytest.fixture
def armed(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    ran: list = []

    async def run(**kw):
        ran.append(kw)
        return ActionResult(True, "posted", rollback_token="t",
                            evidence={"id": "p1"})

    async def undo(token):
        return ActionResult(True, "undone")

    async def dry(**kw):
        return ActionResult(True, "would post")

    a = Action(name="reels.post", tier=Tier.AUTO, summary="post", run=run,
               dry_run=dry, rollback=undo, params={"text": "the caption"})
    monkeypatch.setitem(REGISTRY.actions, a.name, a)
    monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    monkeypatch.setitem(REGISTRY.modules, "reels", object())
    executor._recent.clear()
    return ran, memory_backend


# ------------------------------------------------------- the executor path

@pytest.mark.parametrize("scope", ["shadow:reels", "shadow:all"])
async def test_executor_path_a_shadowed_action_is_never_run(armed, scope):
    ran, mem = armed
    mem.responses[PAUSE_READ] = _row(scope)
    out = await executor.execute(ExecutionRequest("reels.post", {"text": "hi"},
                                                  "tick"))
    assert not out.ok and ran == []
    assert out.detail.startswith("not run: ") and "shadow mode" in out.detail
    events = [r.event for r in mem.audit_rows]
    assert "execute.start" not in events and "execute.dry_run" not in events
    [paused] = [json.loads(r.payload) for r in mem.audit_rows
                if r.event == "execute.paused"]
    assert paused["subject"] == "reels.post"
    assert paused["data"]["scope"] == scope


async def test_default_nothing_is_shadowed_and_the_action_runs(armed):
    ran, mem = armed
    out = await executor.execute(ExecutionRequest("reels.post", {"text": "hi"},
                                                  "tick"))
    assert out.ok and ran == [{"text": "hi"}]
    [(sql, args)] = [(s, a) for s, a in mem.sql_log if PAUSE_READ in s]
    assert args[0] == ["all", "reels", "shadow:all", "shadow:reels"]
    # A real pause outranks a shadow row (it records nothing and stops).
    assert "ORDER BY (scope LIKE 'shadow:%') ASC" in sql


def test_a_shadow_scope_cannot_be_set_or_lifted_from_slack():
    assert pause.parse("resume shadow:reels") is None
    assert pause.parse("pause shadow:reels") is None


def test_only_loaded_modules_can_be_shadowed(monkeypatch):
    monkeypatch.setitem(REGISTRY.modules, "reels", object())
    assert shadow.scope_of("reels") == "shadow:reels"
    assert shadow.scope_of("shadow:reels") == "shadow:reels"
    assert shadow.scope_of("all") == "shadow:all"
    assert shadow.scope_of("reels.post") is None       # modules only
    assert shadow.scope_of("nosuchmodule") is None


async def test_setting_a_shadow_is_one_pauses_row_and_an_audit_row(
        monkeypatch, memory_backend):
    monkeypatch.setitem(REGISTRY.modules, "reels", object())
    said = await shadow.shadow("reels", "cli", "48h G1")
    assert "shadow mode" in said
    [args] = [a for s, a in memory_backend.sql_log if "INSERT INTO pauses" in s]
    assert args[0] == "shadow:reels"
    assert [r.event for r in memory_backend.audit_rows] == ["shadow.set"]
    assert "nothing was shadowed" in await shadow.shadow("nosuchmodule", "cli")


# --------------------------------------------------------- the outbox path

class Script:
    """Answers each SQL call with the first matching fragment, once."""

    def __init__(self, answers: list[tuple[str, object]]) -> None:
        self.answers = list(answers)
        self.sql: list[tuple[str, tuple]] = []

    async def fetch_one(self, sql: str, *args):
        flat = " ".join(sql.split())
        self.sql.append((flat, args))
        for i, (fragment, value) in enumerate(self.answers):
            if fragment in flat:
                self.answers.pop(i)
                if isinstance(value, BaseException):
                    raise value
                return value
        return None

    async def fetch_all(self, sql: str, *args):
        self.sql.append((" ".join(sql.split()), args))
        return []


PARAMS = {"text": "the caption"}


def _claimed(**over) -> dict:
    row = {"id": 7, "kind": "effect", "action": "reels.post",
           "params": json.dumps(PARAMS), "reason": "nightly reel",
           "approved_by": None, "incident_id": None, "workflow_id": None,
           "fencing_token": 3, "attempts": 0, "max_attempts": 5,
           "params_hash": outbox._params_hash(PARAMS), "destination": "ig",
           "occurrence": "2026-09-29"}
    row.update(over)
    return row


@pytest.fixture
def box(monkeypatch, memory_backend):
    calls: list = []

    async def fake_execute(req):
        calls.append(req)
        return ActionResult(True, "done", evidence={"id": "x"})

    monkeypatch.setattr(executor, "execute", fake_execute)

    def install(script: Script) -> Script:
        monkeypatch.setattr(state, "fetch_one", script.fetch_one)
        monkeypatch.setattr(state, "fetch_all", script.fetch_all)
        return script
    return install, calls, memory_backend


async def test_outbox_path_a_shadowed_intent_is_recorded_not_dispatched(box):
    install, calls, mem = box
    script = install(Script([("SET status = 'claimed'", _claimed()),
                             (PAUSE_READ, _row("shadow:reels")),
                             ("SET status = $4", {"id": 7})]))
    done = await outbox.dispatch_once("w-a")
    assert done.status == "shadowed" and done.committed
    assert calls == []                                   # never dispatched
    assert not any("SET status = 'dispatching'" in s for s, _ in script.sql)
    commit = next(a for s, a in script.sql if "SET status = $4" in s)
    assert commit[3] == "shadowed"
    assert commit[7] == "claimed" and commit[8] == 0     # no attempt spent
    [rec] = [json.loads(r.payload) for r in mem.audit_rows
             if r.event == shadow.WOULD_EXECUTE]
    assert rec["subject"] == "reels.post"
    assert rec["data"]["params_hash"] == shadow.params_hash(PARAMS)
    assert rec["data"]["outbox"] == 7 and rec["data"]["layer"] == "outbox"
    assert "the caption" not in json.dumps(rec)          # a hash, not the text


async def test_outbox_path_a_shadowed_workflow_is_blocked_not_left_running(box):
    install, calls, _mem = box
    script = install(Script([("SET status = 'claimed'", _claimed(workflow_id=12)),
                             (PAUSE_READ, _row("shadow:all")),
                             ("SET status = $4", {"id": 7})]))
    done = await outbox.dispatch_once("w-a")
    assert done.status == "shadowed" and calls == []
    moves = [a for s, a in script.sql if "UPDATE workflows" in s]
    assert moves and moves[0][0] == 12 and "BLOCKED" in moves[0]


async def test_outbox_path_an_unreadable_shadow_state_dispatches_nothing(box):
    install, calls, _mem = box
    script = install(Script([("SET status = 'claimed'", _claimed()),
                             (PAUSE_READ, ConnectionError("db gone"))]))
    done = await outbox.dispatch_once("w-a")
    assert done.status == "stale" and not done.committed and calls == []
    assert not any("SET status = 'dispatching'" in s for s, _ in script.sql)


async def test_outbox_path_a_real_pause_still_goes_the_pause_way(box):
    """A plain pause is the executor's to refuse: the row waits, it is not
    marked shadowed (that would swallow it for good)."""
    install, calls, _mem = box
    install(Script([("SET status = 'claimed'", _claimed()),
                    (PAUSE_READ, _row("reels")),
                    ("SET status = 'dispatching'", {"id": 7}),
                    ("SET status = $4", {"id": 7})]))
    done = await outbox.dispatch_once("w-a")
    assert len(calls) == 1 and done.status != "shadowed"


async def test_outbox_path_nothing_shadowed_dispatches_as_before(box):
    install, calls, _mem = box
    install(Script([("SET status = 'claimed'", _claimed()),
                    ("SET status = 'dispatching'", {"id": 7}),
                    ("SET status = $4", {"id": 7})]))
    done = await outbox.dispatch_once("w-a")
    assert done.status == "delivered" and len(calls) == 1


# ------------------------------------------------------------------ report

def _audit_dicts(mem) -> list[dict]:
    return [{"id": r.id, "at": r.at, "event": r.event, "subject": r.subject,
             "payload": r.payload} for r in mem.audit_rows]


async def test_the_report_lists_an_outbox_record_with_its_hash(box):
    install, _calls, mem = box
    # outbox layer
    install(Script([("SET status = 'claimed'", _claimed()),
                    (PAUSE_READ, _row("shadow:reels")),
                    ("SET status = $4", {"id": 7})]))
    await outbox.dispatch_once("w-a")
    since = datetime.now(UTC) - timedelta(minutes=5)
    windows = [{"scope": "shadow:reels", "paused_at": since, "resumed_at": None}]
    rep = shadow.build(since, _audit_dicts(mem), windows)
    [w] = rep.would
    assert (w.action, w.layer, w.params_hash) == (
        "reels.post", "outbox", shadow.params_hash(PARAMS))
    assert rep.unauthorized == [] and rep.ran == {}
    text = shadow.render(rep)
    assert "reels.post" in text and "outbox#7" in text
    assert "Unauthorized effects: none" in text


async def test_the_report_flags_a_run_inside_a_shadow_window(armed):
    ran, mem = armed
    # executor layer: shadowed, then (window over) a real run
    mem.responses[PAUSE_READ] = _row("shadow:reels")
    await executor.execute(ExecutionRequest("reels.post", {"text": "a"}, "t"))
    del mem.responses[PAUSE_READ]
    await executor.execute(ExecutionRequest("reels.post", {"text": "b"}, "t"))
    assert ran == [{"text": "b"}]
    since = datetime.now(UTC) - timedelta(minutes=5)

    lifted = datetime.now(UTC) + timedelta(minutes=5)   # still shadowed at the run
    rep = shadow.build(since, _audit_dicts(mem),
                       [{"scope": "shadow:reels", "paused_at": since,
                         "resumed_at": lifted}])
    [w] = rep.would
    assert w.layer == "executor" and w.params_hash is None
    assert rep.ran["reels.post"].started == 1
    assert rep.ran["reels.post"].finished_ok == 1
    assert [a for _, a in rep.unauthorized] == ["reels.post"]
    text = shadow.render(rep)
    assert "UNAUTHORIZED EFFECTS: 1" in text
    assert "params not recorded at this layer" in text

    ended = shadow.build(since, _audit_dicts(mem),
                         [{"scope": "shadow:reels", "paused_at": since,
                           "resumed_at": since + timedelta(seconds=1)}])
    assert ended.unauthorized == []                      # the run came after


async def test_the_report_reads_only_the_audit_log_and_pauses(memory_backend):
    since = T0 - timedelta(hours=48)
    memory_backend.responses["FROM actions_log"] = [
        {"id": 1, "at": T0, "event": shadow.WOULD_EXECUTE, "subject": "reels.post",
         "payload": json.dumps({"event": shadow.WOULD_EXECUTE,
                                "subject": "reels.post",
                                "data": {"layer": "outbox", "outbox": 3,
                                         "params_hash": "ab" * 32}})}]
    memory_backend.responses["FROM pauses WHERE scope LIKE 'shadow:%'"] = [
        {"scope": "shadow:reels", "paused_at": since, "resumed_at": None}]
    rep = await shadow.report(since)
    assert [w.params_hash for w in rep.would] == ["ab" * 32]
    assert all(s.lstrip().upper().startswith("SELECT")
               for s, _ in memory_backend.sql_log)
    assert memory_backend.audit_rows == []               # read-only


@pytest.mark.parametrize(("text", "want"), [
    ("48h", T0 - timedelta(hours=48)), ("2d", T0 - timedelta(days=2)),
    ("90m", T0 - timedelta(minutes=90)),
    ("2026-09-27T09:00Z", datetime(2026, 9, 27, 9, 0, tzinfo=UTC)),
    ("2026-09-27T09:00", datetime(2026, 9, 27, 9, 0, tzinfo=UTC)),
])
def test_since_parses(text, want):
    assert shadow.parse_since(text, now=T0) == want
