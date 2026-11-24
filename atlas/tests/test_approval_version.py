"""What runs must be what was approved or queued (S08, P04): the executor's
check of the recorded catalog version and params hash."""
from __future__ import annotations

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Action, ActionResult, FailureClass, Tier
from atlas.kernel.executor import STALE, ExecutionRequest, execute
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

NAME = "stubver.resize"


@pytest.fixture(autouse=True)
def armed(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    saved = dict(REGISTRY.actions), dict(REGISTRY.decisions)
    executor._recent.clear()
    yield memory_backend
    for live, kept in zip((REGISTRY.actions, REGISTRY.decisions), saved,
                          strict=True):
        live.clear()
        live.update(kept)
    executor._recent.clear()


def install(ran: list, *, params: dict | None = None) -> Action:
    async def run(target: str, size: str = "s"):
        ran.append((target, size))
        return ActionResult(True, "resized", evidence={"id": target})

    async def dry(target: str, size: str = "s"):
        return ActionResult(True, "would resize")

    async def undo(_token):
        return ActionResult(True, "undone")

    action = Action(name=NAME, tier=Tier.ASK, summary="resize", run=run,
                    dry_run=dry, rollback=undo,
                    params=params or {"target": "which", "size": "how big"})
    REGISTRY.actions[NAME] = action
    REGISTRY.decisions[NAME] = classify(action)
    return action


def _events(backend) -> list[str]:
    return [r.event for r in backend.audit_rows]


async def test_the_version_it_was_approved_at_runs():
    ran: list = []
    install(ran)
    got = await execute(ExecutionRequest(
        NAME, {"target": "a"}, "approved", approved_by="slack:UOWNER",
        approved_version=REGISTRY.action_version(NAME)))
    assert got.ok and ran == [("a", "s")]


async def test_a_changed_action_is_refused_before_anything_runs(armed):
    ran: list = []
    install(ran)
    approved_at = REGISTRY.action_version(NAME)
    # The deploy: the action now takes one more parameter.
    install(ran, params={"target": "which", "size": "how big", "zone": "where"})
    got = await execute(ExecutionRequest(
        NAME, {"target": "a"}, "approved", approved_by="slack:UOWNER",
        approved_version=approved_at))
    assert not got.ok and ran == []
    assert got.dispatched is False
    assert got.failure_class is FailureClass.POLICY
    assert got.evidence == {STALE: "version"}
    assert got.detail == ("refused: the action changed since it was approved; "
                          "needs a fresh approval")
    assert "execute.superseded" in _events(armed)
    assert "execute.dry_run" not in _events(armed)
    assert "execute.start" not in _events(armed)


async def test_a_stale_request_is_refused_even_in_watch_only(monkeypatch, armed):
    """Refused, not parked: a hold would keep a stale row waiting forever."""
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    ran: list = []
    install(ran)
    got = await execute(ExecutionRequest(
        NAME, {"target": "a"}, "queued", approved_by="slack:UOWNER",
        approved_version="av1:0000"))
    assert got.evidence == {STALE: "version"} and ran == []
    assert "execute.watch_only" not in _events(armed)


async def test_a_version_that_cannot_be_computed_is_a_mismatch(monkeypatch):
    ran: list = []
    install(ran)

    def broken(_name):
        raise RuntimeError("no")

    monkeypatch.setattr(REGISTRY, "action_version", broken)
    got = await execute(ExecutionRequest(
        NAME, {"target": "a"}, "approved", approved_by="slack:UOWNER",
        approved_version="av1:whatever"))
    assert not got.ok and got.evidence == {STALE: "version"} and ran == []


async def test_nothing_recorded_is_checked_exactly_as_before():
    """A card raised or a row queued before versions existed."""
    ran: list = []
    install(ran)
    got = await execute(ExecutionRequest(NAME, {"target": "a"}, "legacy",
                                         approved_by="slack:UOWNER"))
    assert got.ok and ran == [("a", "s")]


async def test_the_tier_checks_still_come_first():
    """An unapproved Tier 2 request is refused as unapproved, whatever its
    version says."""
    ran: list = []
    install(ran)
    got = await execute(ExecutionRequest(NAME, {"target": "a"}, "unattended",
                                         approved_version="av1:0000"))
    assert "unapproved" in got.detail and STALE not in (got.evidence or {})


# ------------------------------------------------------------ params hash

async def test_the_params_it_was_approved_with_run():
    from atlas.kernel.registry import payload_hash
    ran: list = []
    install(ran)
    params = {"target": "a", "size": "l"}
    got = await execute(ExecutionRequest(
        NAME, params, "approved", approved_by="slack:UOWNER",
        approved_version=REGISTRY.action_version(NAME),
        approved_payload_hash=payload_hash(params)))
    assert got.ok and ran == [("a", "l")]


async def test_changed_params_are_refused_before_anything_runs(armed):
    from atlas.kernel.registry import payload_hash
    ran: list = []
    install(ran)
    got = await execute(ExecutionRequest(
        NAME, {"target": "a", "size": "xl"}, "approved", approved_by="slack:UOWNER",
        approved_version=REGISTRY.action_version(NAME),
        approved_payload_hash=payload_hash({"target": "a", "size": "l"})))
    assert not got.ok and ran == []
    assert got.dispatched is False
    assert got.failure_class is FailureClass.POLICY
    assert got.evidence == {STALE: "params"}
    assert got.detail == ("refused: the parameters changed since they were "
                          "approved; needs a fresh approval")
    superseded = [r for r in armed.audit_rows if r.event == "execute.superseded"]
    assert len(superseded) == 1 and "params changed" in superseded[0].payload
    assert "execute.start" not in _events(armed)


async def test_a_legacy_hash_of_none_is_not_checked():
    ran: list = []
    install(ran)
    got = await execute(ExecutionRequest(
        NAME, {"target": "b"}, "legacy", approved_by="slack:UOWNER",
        approved_version=REGISTRY.action_version(NAME)))
    assert got.ok and ran == [("b", "s")]


def test_stale_of_reads_the_marker_only_on_a_result_that_did_not_run():
    from atlas.kernel.executor import stale_of
    assert stale_of(ActionResult(False, "x", evidence={STALE: "params"},
                                 dispatched=False)) == "params"
    assert stale_of(ActionResult(False, "x", evidence={STALE: "params"},
                                 dispatched=True)) is None
    assert stale_of(ActionResult(False, "refused: the action changed since it "
                                 "was approved", dispatched=False)) is None
    assert stale_of(ActionResult(False, "x", evidence={STALE: "version"})) is None


# ------------------------------------------------ decide(), beside the claim

async def test_decide_passes_what_was_recorded_to_the_executor(
        memory_backend, monkeypatch):
    from atlas.kernel import approvals
    from tests.test_approvals import claimable
    state = claimable(memory_backend)
    state["recorded"] = {"catalog_version": "av1:abc",
                         "approved_payload_hash": "h1", "dedup_key": None,
                         "evidence": "{}", "rollback_plan": ""}
    seen: list = []

    async def fake_execute(req):
        seen.append(req)
        return ActionResult(True, "did it")

    monkeypatch.setattr(approvals, "execute", fake_execute)
    verdict = await approvals.decide(41, "approve", via="dashboard")
    assert verdict.ok
    assert (seen[0].approved_version, seen[0].approved_payload_hash) == (
        "av1:abc", "h1")


async def test_an_unreadable_record_runs_nothing(memory_backend, monkeypatch):
    """Deny by default: if what was approved cannot be read back, nothing
    is run and the card does not stay 'approved' for the reaper."""
    from atlas.kernel import approvals
    from tests.test_approvals import claimable
    claimable(memory_backend)
    inner = memory_backend.fetchrow

    async def broken(sql, *args):
        if "SELECT catalog_version" in sql:
            raise ConnectionError("db gone")
        return await inner(sql, *args)

    memory_backend.fetchrow = broken
    seen: list = []

    async def fake_execute(req):
        seen.append(req)
        return ActionResult(True, "did it")

    monkeypatch.setattr(approvals, "execute", fake_execute)
    verdict = await approvals.decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "failed" and seen == []
    assert any("SET status=$2" in sql and args[1] == "failed"
               for sql, args in memory_backend.sql_log)


async def test_a_stale_refusal_supersedes_the_card_and_raises_a_fresh_one(
        memory_backend, monkeypatch):
    from atlas.kernel import approvals
    from tests.test_approvals import claimable
    state = claimable(memory_backend)
    state["recorded"] = {"catalog_version": "av1:old",
                         "approved_payload_hash": "h1", "dedup_key": "cause",
                         "evidence": '{"why": "x"}', "rollback_plan": "undo"}

    async def refuse(req):
        return ActionResult(False, executor.STALE_VERSION_DETAIL,
                            evidence={STALE: "version"},
                            failure_class=FailureClass.POLICY, dispatched=False)

    raised: list = []

    async def propose(action, params, reason, **kw):
        raised.append((action, params, reason, kw))
        return 77

    monkeypatch.setattr(approvals, "execute", refuse)
    monkeypatch.setattr(approvals, "propose", propose)
    verdict = await approvals.decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "superseded"
    assert "#77" in verdict.detail
    assert verdict.receipt().startswith("Proposal #41 was not run: the action "
                                        "changed since it was approved")
    [(action, params, reason, kw)] = raised
    assert reason.startswith("Fresh card for #41: the action changed")
    assert action == "flycheap.regenerate_links" and params == {}
    assert kw["dedup_key"] == "superseded:41"
    assert kw["evidence"] == {"why": "x"} and kw["rollback_plan"] == "undo"
    sqls = [sql for sql, _ in memory_backend.sql_log]
    assert any("status='superseded'" in sql and "status='approved'" in sql
               for sql in sqls)
    assert not any("SET status=$2" in sql for sql in sqls)   # not 'failed'
    assert "proposal.superseded" in [r.event for r in memory_backend.audit_rows]


async def test_a_fresh_card_that_cannot_be_raised_is_audited_not_raised(
        memory_backend, monkeypatch):
    from atlas.kernel import approvals

    async def refuses(*_a, **_kw):
        raise ValueError("missing parameter zone")

    monkeypatch.setattr(approvals, "propose", refuses)
    got = await approvals.fresh_card("stubver.resize", {"target": "a"}, "why",
                                     dedup_key="superseded:9")
    assert got is None
    assert "proposal.fresh_card_refused" in [
        r.event for r in memory_backend.audit_rows]


def test_the_outbox_sends_a_stale_refusal_to_superseded_without_an_attempt():
    from atlas.kernel import outbox
    stale = ActionResult(False, executor.STALE_PARAMS_DETAIL,
                         evidence={STALE: "params"},
                         failure_class=FailureClass.POLICY, dispatched=False)
    assert outbox._outcome("effect", stale, 1, 5) == ("superseded", None, True)
    assert outbox._outcome("notice", stale, 1, 5) == ("superseded", None, True)
    other = ActionResult(False, "refused: undeclared parameter(s): x",
                         failure_class=FailureClass.VALIDATION, dispatched=False)
    assert outbox._outcome("effect", other, 1, 5)[0] == "failed"
