"""ActionResult.dispatched (RA-B3, docs/durable-core.md).

`executor.execute` says itself whether the action body was invoked: False on
every refusal it makes before `run()`, True once `run()` was called, whatever
it returned or raised. The outbox reads the flag, and falls back to matching
the executor's refusal wording only when it is None (a result that did not
come from `execute`).
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from atlas.kernel import executor, outbox, pause
from atlas.kernel.contracts import Action, ActionResult, FailureClass, Tier
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import Decision, classify
from atlas.kernel.registry import REGISTRY

FC = FailureClass


def test_the_flag_defaults_to_none_and_every_construction_still_works():
    assert ActionResult(True).dispatched is None
    assert ActionResult(False, "x", {}, None, None).dispatched is None
    assert ActionResult(False, "x", dispatched=False).dispatched is False


# ------------------------------------------------------------ the executor

@pytest.fixture
def hands(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    yield monkeypatch
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


def _install(name="mod.act", *, tier=Tier.AUTO, dry_ok=True, principal_only=False,
             max_per_hour=3, decision: Decision | None = None, run=None,
             dry_class: FailureClass | None = None) -> list:
    ran: list = []

    async def default_run(**kw):
        ran.append(kw)
        return ActionResult(True, "done", rollback_token="t")

    async def dry_run(**kw):
        return ActionResult(dry_ok, "would do it" if dry_ok else "would break",
                            failure_class=dry_class)

    async def undo(token):
        return ActionResult(True, "undone")

    action = Action(name=name, tier=tier, summary="s", run=run or default_run,
                    dry_run=dry_run, rollback=undo, principal_only=principal_only,
                    max_per_hour=max_per_hour)
    REGISTRY.actions[action.name] = action
    REGISTRY.decisions[action.name] = decision or classify(action)
    return ran


async def _refusal(setup: str, monkeypatch) -> ActionResult:
    """What the real `execute` returns for each refusal it makes before
    `run()`. The action's run records a call, so a refusal that ran it
    fails loudly."""
    ran: list = []
    params: dict = {}
    name = "mod.act"
    if setup == "not_in_catalog":
        name = "nope.act"
    elif setup == "no_decision":
        ran = _install()
        REGISTRY.decisions.clear()
    elif setup == "denied":
        ran = _install(decision=Decision(False, Tier.NEVER, "permanently denied"))
    elif setup == "principal_only":
        ran = _install(tier=Tier.ASK, principal_only=True)
    elif setup == "unapproved":
        ran = _install(tier=Tier.ASK)
    elif setup == "watch_only":
        ran = _install()
        monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    elif setup == "bad_params":
        ran = _install()
        params = {"nope": 1}
    elif setup == "pause_unreadable":
        ran = _install()

        async def broken(module, account=None):
            raise pause.PauseUnreadable("ConnectionError")
        monkeypatch.setattr(pause, "active_for", broken)
    elif setup == "paused":
        ran = _install()

        async def paused(module, account=None):
            return SimpleNamespace(scope="all", says=lambda: "everything is paused")
        monkeypatch.setattr(pause, "active_for", paused)
    elif setup == "rate_limited":
        ran = _install(max_per_hour=1)
        assert (await execute(ExecutionRequest(name, {}, "first"))).ok
        ran.clear()
    elif setup == "dry_run_refused":
        ran = _install(dry_ok=False)
    approved = "agent:x" if setup == "principal_only" else None
    out = await execute(ExecutionRequest(name, params, "because",
                                         approved_by=approved))
    assert not out.ok and ran == [], setup
    return out


REFUSALS = ("not_in_catalog", "no_decision", "denied", "principal_only",
            "unapproved", "watch_only", "bad_params", "pause_unreadable",
            "paused", "rate_limited", "dry_run_refused")


@pytest.mark.parametrize("setup", REFUSALS)
async def test_every_refusal_before_run_is_not_dispatched(hands, setup):
    assert (await _refusal(setup, hands)).dispatched is False


async def test_once_run_was_called_it_is_dispatched(hands):
    _install()
    assert (await execute(ExecutionRequest("mod.act", {}, "go"))).dispatched is True

    async def says_no(**kw):
        return ActionResult(False, "not run: the provider said no")
    _install("mod.no", run=says_no)
    out = await execute(ExecutionRequest("mod.no", {}, "go"))
    assert not out.ok and out.dispatched is True
    assert out.detail == "not run: the provider said no"

    async def boom(**kw):
        raise RuntimeError("mid-flight")
    _install("mod.boom", run=boom)
    out = await execute(ExecutionRequest("mod.boom", {}, "go"))
    assert not out.ok and out.dispatched is True


async def test_the_actions_own_fields_survive_the_flag(hands):
    async def run(**kw):
        return ActionResult(True, "done", {"id": 7}, "tok",
                            failure_class=None)
    _install(run=run)
    out = await execute(ExecutionRequest("mod.act", {}, "go"))
    assert (out.ok, out.detail, out.evidence, out.rollback_token) == (
        True, "done", {"id": 7}, "tok")


# ------------------------------------------------------------- the outbox

@pytest.mark.parametrize("setup", REFUSALS)
@pytest.mark.parametrize("kind", ["effect", "notice"])
async def test_the_flag_agrees_with_the_wording_on_every_real_refusal(
        hands, setup, kind):
    """The flag replaces the wording match without changing a single
    outcome: watch-only is on by default, and a refusal read the wrong way
    would fail -- or resend -- every queued intent."""
    hands.setattr(outbox.random, "uniform", lambda a, b: 1.0)
    refused = await _refusal(setup, hands)
    by_wording = dataclasses.replace(refused, dispatched=None)
    for attempts in (1, 5):
        assert outbox._outcome(kind, refused, attempts, 5) == \
            outbox._outcome(kind, by_wording, attempts, 5), setup


def test_dispatched_true_is_never_read_as_a_refusal():
    """The residual risk docs/durable-core.md named: a run() that acted and
    then returned a detail worded like a refusal. With the flag it is an
    uncertain effect, reconciled, never offered to the executor again."""
    for detail in ("not run: the page did not load",
                   "ATLAS is in watch-only mode, apparently",
                   "refused: the provider refused",
                   ("x has already run 3 times this hour; escalating instead "
                    "of repeating")):
        acted = ActionResult(False, detail, dispatched=True)
        assert outbox._outcome("effect", acted, 1, 5) == ("reconciling", None, False)
        assert outbox._outcome("effect", dataclasses.replace(
            acted, dispatched=None), 1, 5)[0] != "reconciling"


@pytest.mark.parametrize(("failure_class", "status", "retry"), [
    (FC.TRANSIENT, "pending", "backoff"),
    (FC.BUDGET, "pending", "backoff"),
    (FC.DISABLED, "failed", None),       # no executor marker: not a hold
    (FC.POLICY, "failed", None),
    (FC.VALIDATION, "failed", None),
    (FC.AUTH, "failed", None),
    (None, "failed", None),
])
def test_a_pre_dispatch_refusal_is_split_by_its_class_not_its_words(
        monkeypatch, failure_class, status, retry):
    monkeypatch.setattr(outbox.random, "uniform", lambda a, b: 1.0)
    refused = ActionResult(False, "wording the outbox has never seen",
                           failure_class=failure_class, dispatched=False)
    expected = {"backoff": outbox._backoff(1), None: None}[retry]
    assert outbox._outcome("effect", refused, 1, 5) == (status, expected, False)
    assert outbox._outcome("effect", refused, 5, 5)[0] == "failed"


@pytest.mark.parametrize("hold", ["watch_only", "paused"])
def test_only_the_executors_marked_hold_waits(hold):
    """Wait-without-an-attempt needs all three: dispatched False, class
    disabled, and the executor's marker. Drop any one and it is not a hold."""
    held = ActionResult(False, "x", evidence={executor.HOLD: hold},
                        failure_class=FC.DISABLED, dispatched=False)
    assert outbox._outcome("effect", held, 99, 5) == (
        "pending", outbox.DISABLED_RECHECK, True)
    for other in (dataclasses.replace(held, failure_class=FC.POLICY),
                  dataclasses.replace(held, evidence={executor.HOLD: "other"}),
                  dataclasses.replace(held, evidence={})):
        assert outbox._outcome("effect", other, 1, 5)[0] == "failed"
    assert outbox._outcome("effect", dataclasses.replace(held, dispatched=True),
                           1, 5)[0] == "reconciling"


@pytest.mark.parametrize("setup", ["watch_only", "paused"])
async def test_executor_watch_only_and_pause_wait(hands, setup):
    refused = await _refusal(setup, hands)
    assert outbox._outcome("effect", refused, 99, 5) == (
        "pending", outbox.DISABLED_RECHECK, True)


@pytest.mark.parametrize("dry_class", [FC.DISABLED, FC.TRANSIENT, FC.BUDGET])
async def test_a_dry_run_cannot_make_the_outbox_wait_or_retry(hands, dry_class):
    """A dry run returning DISABLED is a permanent fail, not a wait; one
    returning TRANSIENT (or BUDGET) is a permanent fail, not a retry."""
    ran = _install(dry_ok=False, dry_class=dry_class)
    out = await execute(ExecutionRequest("mod.act", {}, "because"))
    assert ran == [] and out.dispatched is False
    assert out.failure_class is FC.VALIDATION
    for kind in ("effect", "notice"):
        assert outbox._outcome(kind, out, 1, 5) == ("failed", None, False)
