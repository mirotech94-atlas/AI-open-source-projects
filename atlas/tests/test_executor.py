"""executor.execute — the single path to the outside world.

A bug here is expensive in both directions: a Tier 2 action that runs without
approval, or a Tier 1 action that is blocked from fixing something at 4am.
"""
from __future__ import annotations

import json

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.executor import ExecutionRequest, execute, rollback
from atlas.kernel.permissions import Decision, classify
from atlas.kernel.registry import REGISTRY


@pytest.fixture(autouse=True)
def hands_on(monkeypatch):
    """Most executor tests are about what happens WHEN ATLAS acts, so they run
    with watch-only off. The watch-only tests set it back on explicitly."""
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")


@pytest.fixture(autouse=True)
def clean_registry(memory_backend):
    """Every test gets an empty catalog and an empty rate-limit window."""
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    yield
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


def install(action: Action, decision: Decision | None = None) -> Action:
    REGISTRY.actions[action.name] = action
    REGISTRY.decisions[action.name] = decision or classify(action)
    return action


def build(name="mod.act", tier=Tier.ASK, *, ran: list, dry_ok=True,
          params=None, max_per_hour=3) -> Action:
    async def run(**kw):
        ran.append(("run", kw))
        return ActionResult(True, "done", rollback_token="tok")

    async def dry(**kw):
        ran.append(("dry", kw))
        return ActionResult(dry_ok, "preview" if dry_ok else "would break")

    async def undo(token):
        ran.append(("rollback", token))
        return ActionResult(True, "undone")

    return Action(name=name, tier=tier, summary="s", run=run, dry_run=dry,
                  rollback=undo, params=params or {}, max_per_hour=max_per_hour)


# ------------------------------------------------------------ tier gating

async def test_tier2_without_approval_is_refused():
    ran: list = []
    install(build(tier=Tier.ASK, ran=ran))
    result = await execute(ExecutionRequest("mod.act", {}, "because"))
    assert not result.ok
    assert "unapproved" in result.detail
    assert ran == [], "nothing may run, not even the dry run"


async def test_tier2_with_approval_runs():
    ran: list = []
    install(build(tier=Tier.ASK, ran=ran))
    result = await execute(
        ExecutionRequest("mod.act", {}, "because", approved_by="owner"))
    assert result.ok
    assert [step for step, _ in ran] == ["dry", "run"]


async def test_tier1_runs_unattended():
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))
    result = await execute(ExecutionRequest("mod.auto", {}, "auto"))
    assert result.ok
    assert [step for step, _ in ran] == ["dry", "run"]


async def test_denied_action_is_refused():
    ran: list = []
    action = build(name="mod.delete_everything", tier=Tier.ASK, ran=ran)
    install(action)
    result = await execute(
        ExecutionRequest("mod.delete_everything", {}, "x", approved_by="owner"))
    assert not result.ok
    assert ran == []


async def test_unknown_action_is_refused():
    result = await execute(ExecutionRequest("mod.nope", {}, "x"))
    assert not result.ok and "catalog" in result.detail


async def test_missing_decision_refuses_instead_of_raising():
    ran: list = []
    action = build(ran=ran)
    REGISTRY.actions[action.name] = action          # registered, no decision
    result = await execute(ExecutionRequest("mod.act", {}, "x"))
    assert not result.ok
    assert "no permission decision" in result.detail


# ------------------------------------------------------------- dry running

async def test_a_failing_dry_run_stops_the_action():
    ran: list = []
    install(build(ran=ran, dry_ok=False))
    result = await execute(
        ExecutionRequest("mod.act", {}, "x", approved_by="owner"))
    assert not result.ok
    assert "dry run refused" in result.detail
    assert [step for step, _ in ran] == ["dry"]


# ------------------------------------------------------------ rate limiting

async def test_rate_limit_fires_after_the_ceiling():
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran, max_per_hour=2))
    for _ in range(2):
        assert (await execute(ExecutionRequest("mod.auto", {}, "x"))).ok
    third = await execute(ExecutionRequest("mod.auto", {}, "x"))
    assert not third.ok
    assert "escalating instead of repeating" in third.detail


async def test_a_refused_dry_run_does_not_spend_the_budget():
    """C2: three dry-run refusals must not exhaust an action that never acted."""
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran, dry_ok=False,
                  max_per_hour=2))
    for _ in range(3):
        assert not (await execute(ExecutionRequest("mod.auto", {}, "x"))).ok
    assert len(executor._recent["mod.auto"]) == 0, "a dry run spent the budget"

    # And the action can still act once the dry run passes.
    executor.REGISTRY.actions["mod.auto"] = build(
        name="mod.auto", tier=Tier.AUTO, ran=ran, dry_ok=True, max_per_hour=2)
    assert (await execute(ExecutionRequest("mod.auto", {}, "x"))).ok


# --------------------------------------------------------- parameter safety

async def test_undeclared_parameters_are_refused():
    """C4: the catalog closes the action name; it must close the arguments too."""
    ran: list = []
    install(build(tier=Tier.ASK, ran=ran, params={"limit": "how many"}))
    result = await execute(ExecutionRequest(
        "mod.act", {"limit": 5, "delete_older_than": "0 days"}, "x",
        approved_by="owner"))
    assert not result.ok
    assert "delete_older_than" in result.detail
    assert ran == []


async def test_declared_parameters_are_passed_through():
    ran: list = []
    install(build(tier=Tier.ASK, ran=ran, params={"limit": "how many"}))
    result = await execute(
        ExecutionRequest("mod.act", {"limit": 5}, "x", approved_by="owner"))
    assert result.ok
    assert ("run", {"limit": 5}) in ran


# ------------------------------------------------------------------- audit

async def test_audit_is_written_before_the_action(memory_backend):
    """A crash mid-action must still leave a record that it started."""
    order: list[str] = []

    async def run(**kw):
        order.append("action-ran")
        raise RuntimeError("boom")

    async def dry(**kw):
        return ActionResult(True)

    async def undo(t):
        return ActionResult(True)

    install(Action(name="mod.auto", tier=Tier.AUTO, summary="s", run=run,
                   dry_run=dry, rollback=undo))
    result = await execute(ExecutionRequest("mod.auto", {}, "x"))
    assert not result.ok and "raised" in result.detail

    events = [r.event for r in memory_backend.audit_rows]
    assert "execute.start" in events
    assert events.index("execute.start") < events.index("execute.error")
    assert "action-ran" in order


def _payload_data(row) -> dict:
    return json.loads(row.payload)["data"]


async def test_finish_evidence_survives_into_the_audit_row(memory_backend):
    """A readback diff, a dry-run count -- whatever the action decided to
    explain itself with -- must reach `actions_log`, not just its own
    ActionResult."""
    async def run(**kw):
        return ActionResult(True, "done", evidence={"diff": "a vs b"})

    install(Action(name="mod.act", tier=Tier.ASK, summary="s", run=run))
    await execute(ExecutionRequest("mod.act", {}, "x", approved_by="owner"))

    finish = next(r for r in memory_backend.audit_rows if r.event == "execute.finish")
    assert _payload_data(finish)["evidence"] == {"diff": "a vs b"}


async def test_a_10kb_evidence_is_capped(memory_backend):
    """Quote- and non-ASCII-heavy on purpose: JSON-escaping a `"` doubles it,
    and a non-ASCII character becomes a 6-byte \\uXXXX escape under
    `json.dumps`'s default `ensure_ascii=True` (what `audit()` itself uses) --
    a cap that only bounds the RAW slice, before it is re-serialised, comes
    back well over 4 KB once escaped. This must cap the encoded form."""
    async def dry(**kw):
        return ActionResult(True)

    async def run(**kw):
        return ActionResult(True, "done",
                            evidence={"readback": ('"\\' + "é") * 3_000})

    install(Action(name="mod.act", tier=Tier.ASK, summary="s", run=run, dry_run=dry))
    await execute(ExecutionRequest("mod.act", {}, "x", approved_by="owner"))

    finish = next(r for r in memory_backend.audit_rows if r.event == "execute.finish")
    evidence = _payload_data(finish)["evidence"]
    assert evidence["truncated"] is True
    assert evidence["bytes"] > 4096
    # The cap is on the encoded wrapper, exactly -- not the raw slice inside
    # it, and not a loose tolerance around it.
    assert len(json.dumps(evidence, sort_keys=True).encode("utf-8")) <= 4096


async def test_refusals_are_audited(memory_backend):
    install(build(tier=Tier.ASK, ran=[]))
    await execute(ExecutionRequest("mod.act", {}, "x"))
    assert any(r.event == "execute.refused" for r in memory_backend.audit_rows)


# ---------------------------------------------------------------- rollback

async def test_rollback_is_gated_by_the_permission_decision():
    """C5: rollback is a write path and must not be an ungated one."""
    ran: list = []
    action = build(ran=ran)
    install(action, Decision(False, Tier.NEVER, "permanently denied: money out"))
    result = await rollback("mod.act", "tok")
    assert not result.ok
    assert "money out" in result.detail
    assert ran == []


async def test_rollback_runs_when_allowed():
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))
    result = await rollback("mod.auto", "tok")
    assert result.ok
    assert ("rollback", "tok") in ran


# ------------------------------------------------------------ watch-only

async def test_watch_only_refuses_a_tier1_action(monkeypatch):
    """Phase 1 ships with no ability to act at all."""
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))
    result = await execute(ExecutionRequest("mod.auto", {}, "x"))
    assert not result.ok
    assert "watch-only" in result.detail
    assert ran == [], "not even the dry run may touch anything"


async def test_watch_only_refuses_an_approved_tier2_action(monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    ran: list = []
    install(build(tier=Tier.ASK, ran=ran))
    result = await execute(
        ExecutionRequest("mod.act", {}, "x", approved_by="owner"))
    assert not result.ok and ran == []


async def test_watch_only_is_the_default_when_unset(monkeypatch):
    monkeypatch.delenv("ATLAS_WATCH_ONLY", raising=False)
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))
    assert not (await execute(ExecutionRequest("mod.auto", {}, "x"))).ok
    assert ran == []


async def test_watch_only_refusal_is_audited(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    install(build(name="mod.auto", tier=Tier.AUTO, ran=[]))
    await execute(ExecutionRequest("mod.auto", {}, "x"))
    assert any(r.event == "execute.watch_only" for r in memory_backend.audit_rows)


async def test_watch_only_blocks_rollback_too(monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))
    result = await rollback("mod.auto", "tok")
    assert not result.ok and ran == []


async def test_turning_it_off_restores_execution(monkeypatch):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))
    assert (await execute(ExecutionRequest("mod.auto", {}, "x"))).ok
    assert [s for s, _ in ran] == ["dry", "run"]


# ------------------------------------------------- tiers are code, not data

async def test_always_does_not_create_a_runtime_tier_override(monkeypatch):
    """`Approve + always` must never make a later Tier 2 action self-approve.

    If this test ever fails, someone has taught the system to grant itself
    permissions at runtime, which is the one thing the tier model forbids.
    """
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    ran: list = []
    install(build(tier=Tier.ASK, ran=ran))

    # Whatever an earlier "always" recorded, an unapproved Tier 2 request is
    # still refused at the executor.
    result = await execute(ExecutionRequest("mod.act", {}, "second time"))
    assert not result.ok
    assert "unapproved" in result.detail
    assert ran == []


# -------------------------------------------------------------- preview

async def test_preview_runs_only_the_dry_run(monkeypatch):
    """The approvals page needs the dry-run output before the owner decides.
    preview() must be incapable of reaching run()."""
    from atlas.kernel.executor import preview

    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))

    result = await preview("mod.auto", {})
    assert result.ok
    assert [step for step, _ in ran] == ["dry"], "preview must not call run()"


async def test_preview_works_during_watch_only(monkeypatch):
    """Seeing what WOULD happen is the entire point of the watch-only phase."""
    from atlas.kernel.executor import preview

    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran))
    assert (await preview("mod.auto", {})).ok


async def test_preview_refuses_a_denied_action():
    from atlas.kernel.executor import preview
    ran: list = []
    install(build(name="mod.delete_things", tier=Tier.ASK, ran=ran))
    result = await preview("mod.delete_things", {})
    assert not result.ok
    assert ran == []


async def test_preview_refuses_undeclared_parameters():
    from atlas.kernel.executor import preview
    ran: list = []
    install(build(tier=Tier.ASK, ran=ran, params={"limit": "how many"}))
    result = await preview("mod.act", {"rm_rf": "/"})
    assert not result.ok and "rm_rf" in result.detail
    assert ran == []


async def test_preview_of_an_unknown_action_is_refused():
    from atlas.kernel.executor import preview
    assert not (await preview("mod.nope", {})).ok


# ------------------------------------------------- missing parameters (9a)

def _needs_a_thread(ran: list) -> Action:
    async def run(thread_id: str, note: str = ""):
        ran.append(("run", thread_id))
        return ActionResult(True, "done", rollback_token="t")

    async def dry(thread_id: str, note: str = ""):
        ran.append(("dry", thread_id))
        return ActionResult(True, "preview")

    async def undo(token):
        return ActionResult(True, "undone")

    return Action(name="mail.label", tier=Tier.AUTO, summary="s", run=run,
                  dry_run=dry, rollback=undo,
                  params={"thread_id": "which thread", "note": "optional"})


@pytest.mark.parametrize("params", [{}, {"thread_id": None}, {"thread_id": "  "},
                                    {"note": "x"}])
async def test_a_missing_required_param_is_refused_as_policy(params, monkeypatch):
    """Tier 1 reaches run(**params) without propose() (tools.invoke,
    intent.act, the diagnose pick, the marketing plan). A missing one used to
    raise TypeError inside run -- and be written down as a failed_action
    lesson. Now it is refused before anything runs, and nothing is learned."""
    ran: list = []
    install(_needs_a_thread(ran))

    async def no_lesson(*a, **k):
        raise AssertionError("a policy refusal is not a mistake to learn from")

    monkeypatch.setattr(executor.lessons, "record", no_lesson)
    result = await execute(ExecutionRequest("mail.label", params, "unattended"))
    assert result.ok is False and "missing parameter(s): thread_id" in result.detail
    assert ran == []


async def test_the_preview_refuses_a_missing_param_too():
    ran: list = []
    install(_needs_a_thread(ran))
    result = await executor.preview("mail.label", {})
    assert result.ok is False and "thread_id" in result.detail
    assert ran == []


async def test_a_complete_call_still_runs():
    ran: list = []
    install(_needs_a_thread(ran))
    result = await execute(ExecutionRequest("mail.label", {"thread_id": "t1"}, "r"))
    assert result.ok and ran == [("dry", "t1"), ("run", "t1")]


# ------------------------------------- per-module and global ceilings (9.2)

async def test_one_module_cannot_spend_the_whole_hour(monkeypatch):
    """BUILD_ORDER 9.2 asks for limits per module AND globally.

    Per-action ceilings alone do not bound a module: ten actions at three an
    hour is thirty writes from one integration, which is what a module with a
    broken collector and a confident reasoner produces. The ceiling that
    matters during an incident is the one on the module, not on each action.
    """
    monkeypatch.setattr(executor, "MODULE_MAX_PER_HOUR", 3)
    ran: list = []
    for i in range(4):
        install(build(name=f"mod.a{i}", tier=Tier.AUTO, ran=ran, max_per_hour=9))

    ok = [(await execute(ExecutionRequest(f"mod.a{i}", {}, "x"))).ok
          for i in range(4)]
    assert ok == [True, True, True, False], ok
    assert "mod" in (await execute(ExecutionRequest("mod.a0", {}, "x"))).detail


async def test_a_module_ceiling_does_not_bind_another_module(monkeypatch):
    monkeypatch.setattr(executor, "MODULE_MAX_PER_HOUR", 1)
    ran: list = []
    install(build(name="one.auto", tier=Tier.AUTO, ran=ran, max_per_hour=9))
    install(build(name="two.auto", tier=Tier.AUTO, ran=ran, max_per_hour=9))

    assert (await execute(ExecutionRequest("one.auto", {}, "x"))).ok
    assert not (await execute(ExecutionRequest("one.auto", {}, "x"))).ok
    assert (await execute(ExecutionRequest("two.auto", {}, "x"))).ok, (
        "one module's budget must not spend another's")


async def test_a_global_ceiling_bounds_the_whole_system(monkeypatch):
    """The blast radius of a bad hour, whatever the cause.

    A per-module limit still allows every module to run its allowance at once —
    a correlated incident touching eight modules is eight budgets, not one.
    """
    monkeypatch.setattr(executor, "GLOBAL_MAX_PER_HOUR", 2)
    monkeypatch.setattr(executor, "MODULE_MAX_PER_HOUR", 99)
    ran: list = []
    for name in ("a.auto", "b.auto", "c.auto"):
        install(build(name=name, tier=Tier.AUTO, ran=ran, max_per_hour=9))

    assert (await execute(ExecutionRequest("a.auto", {}, "x"))).ok
    assert (await execute(ExecutionRequest("b.auto", {}, "x"))).ok
    third = await execute(ExecutionRequest("c.auto", {}, "x"))
    assert not third.ok
    assert "everything" in third.detail or "system" in third.detail, third.detail


async def test_a_refused_dry_run_spends_neither_ceiling(monkeypatch):
    """The same rule the per-action budget already follows."""
    monkeypatch.setattr(executor, "MODULE_MAX_PER_HOUR", 2)
    monkeypatch.setattr(executor, "GLOBAL_MAX_PER_HOUR", 2)
    ran: list = []
    install(build(name="mod.auto", tier=Tier.AUTO, ran=ran, dry_ok=False,
                  max_per_hour=9))
    for _ in range(3):
        assert not (await execute(ExecutionRequest("mod.auto", {}, "x"))).ok
    assert len(executor._recent["module:mod"]) == 0
    assert len(executor._recent["global:"]) == 0
