"""ActionResult.failure_class and classify_failure (RA-D1, docs/verification.md).

The executor names the plan's failure class on each refusal it makes itself,
and nowhere else: a module's `run()` result is passed through untouched. The
classifier is pure and answers None for any shape it does not recognise --
never a guessed "transient", which would have a caller retry an effect that
may already have happened.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from atlas.kernel import executor, pause, verify
from atlas.kernel.contracts import (
    Action,
    ActionResult,
    FailureClass,
    Tier,
    classify_failure,
)
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import Decision, classify
from atlas.kernel.registry import REGISTRY

FC = FailureClass


# ------------------------------------------------------------ the contract

def test_every_existing_construction_still_works():
    positional = ActionResult(False, "no", {"id": 1}, "tok")
    assert positional.failure_class is None
    assert ActionResult(True).failure_class is None
    assert ActionResult(ok=False, detail="x").failure_class is None
    tagged = ActionResult(False, "x", failure_class=FC.AUTH)
    assert tagged.failure_class == "auth"
    assert ActionResult(False, "x") != tagged


def test_verify_re_exports_the_same_enum():
    assert verify.FailureClass is FailureClass
    assert {c.value for c in FailureClass} == {
        "validation", "auth", "disabled", "transient", "timeout_after_dispatch",
        "budget", "stale", "quality", "policy", "security"}


# ------------------------------------------------------------- the helper

class _HTTPError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.response = SimpleNamespace(status_code=code)


class ReadTimeout(Exception):
    """Named like httpx's, and like it not a TimeoutError."""


class ConnectError(Exception):
    pass


@pytest.mark.parametrize(("cause", "after", "want"), [
    (401, False, FC.AUTH),
    (403, True, FC.AUTH),
    (429, False, FC.TRANSIENT),
    (429, True, FC.TRANSIENT),
    (503, True, FC.TRANSIENT),
    (502, False, FC.TRANSIENT),
    (504, False, FC.TRANSIENT),
    (504, True, FC.TIMEOUT_AFTER_DISPATCH),
    (422, False, FC.VALIDATION),
    (400, True, FC.VALIDATION),
    (404, False, None),
    (500, True, None),
    (_HTTPError(401), False, FC.AUTH),
    (_HTTPError(503), True, FC.TRANSIENT),
    (SimpleNamespace(status_code=403), False, FC.AUTH),
    (TimeoutError(), False, FC.TRANSIENT),
    (TimeoutError(), True, FC.TIMEOUT_AFTER_DISPATCH),
    (ReadTimeout(), False, FC.TRANSIENT),
    (ReadTimeout(), True, FC.TIMEOUT_AFTER_DISPATCH),
    (ConnectionRefusedError(), True, FC.TRANSIENT),
    (ConnectError(), False, FC.TRANSIENT),
    ("HTTP 401 from api.example.com", False, FC.AUTH),
    ("action raised: HTTP 429 from the API", True, FC.TRANSIENT),
    ("HTTP 403 from /v1/x — authenticated but not permitted", False, FC.AUTH),
    ("timed out", True, FC.TIMEOUT_AFTER_DISPATCH),
    ("timed out", False, FC.TRANSIENT),
    ("HTTP 429 from /x — rate limited", False, FC.TRANSIENT),
    ("rate limited", False, FC.TRANSIENT),
    ("ValueError: bad", False, None),
    ("", False, None),
    (None, False, None),
    (ValueError("nope"), True, None),
    (KeyError("x"), False, None),
    (RuntimeError("HTTP 401"), False, None),
])
def test_common_shapes_are_classified(cause, after, want):
    assert classify_failure(cause, after_dispatch=after) == want


def test_an_unknown_shape_is_never_guessed_transient():
    """A crash after dispatch that the helper cannot name stays None, so a
    caller takes the cautious path (reconcile), not a blind retry."""
    for cause in (Exception("boom"), OSError("disk"), 418, "something odd"):
        assert classify_failure(cause, after_dispatch=True) is None


# ------------------------------------------ the executor's own refusals

@pytest.fixture
def hands(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    yield
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


def _install(*, tier=Tier.AUTO, dry=None, principal_only=False, params=None,
             max_per_hour=3, decision: Decision | None = None,
             run_result: ActionResult | None = None) -> list:
    ran: list = []

    async def run(**kw):
        ran.append(kw)
        return run_result or ActionResult(True, "done", rollback_token="t")

    async def dry_run(**kw):
        return dry or ActionResult(True, "would do it")

    async def undo(token):
        return ActionResult(True, "undone")

    action = Action(name="mod.act", tier=tier, summary="s", run=run,
                    dry_run=dry_run, rollback=undo, params=params or {},
                    principal_only=principal_only, max_per_hour=max_per_hour)
    REGISTRY.actions[action.name] = action
    REGISTRY.decisions[action.name] = decision or classify(action)
    return ran


async def _go(params=None, approved_by=None) -> ActionResult:
    return await execute(ExecutionRequest("mod.act", params or {}, "because",
                                          approved_by=approved_by))


async def test_unknown_action_is_validation(hands):
    out = await execute(ExecutionRequest("nope.act", {}, "because"))
    assert out.failure_class is FC.VALIDATION


async def test_undeclared_or_missing_params_are_validation(hands):
    _install()
    assert (await _go({"x": 1})).failure_class is FC.VALIDATION


async def test_tier_denials_are_policy(hands):
    _install(decision=Decision(False, Tier.NEVER, "permanently denied"))
    assert (await _go()).failure_class is FC.POLICY
    REGISTRY.decisions.clear()
    assert (await _go()).failure_class is FC.POLICY          # no decision
    _install(tier=Tier.ASK)
    assert (await _go()).failure_class is FC.POLICY          # unapproved Tier 2
    _install(tier=Tier.ASK, principal_only=True)
    assert (await _go(approved_by="agent:x")).failure_class is FC.POLICY


async def test_watch_only_and_a_pause_are_disabled(hands, monkeypatch):
    _install()
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    assert (await _go()).failure_class is FC.DISABLED
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")

    async def paused(module, account=None):
        return SimpleNamespace(scope="all", says=lambda: "everything is paused")
    monkeypatch.setattr(pause, "active_for", paused)
    out = await _go()
    assert out.detail.startswith("not run: ") and out.failure_class is FC.DISABLED


async def test_an_unreadable_pause_state_is_transient(hands, monkeypatch):
    _install()

    async def broken(module, account=None):
        raise pause.PauseUnreadable("ConnectionError")
    monkeypatch.setattr(pause, "active_for", broken)
    assert (await _go()).failure_class is FC.TRANSIENT


async def test_the_rate_ceiling_is_budget(hands):
    _install(max_per_hour=1)
    assert (await _go()).ok
    out = await _go()
    assert not out.ok and out.failure_class is FC.BUDGET


@pytest.mark.parametrize("own", [None, FC.AUTH, FC.DISABLED, FC.TRANSIENT, FC.BUDGET])
async def test_a_refused_dry_run_is_always_validation(hands, own):
    """Fixed and permanent, never the dry run's own class: a preview must
    not be able to put a caller into a wait or a retry."""
    _install(dry=ActionResult(False, "would break", failure_class=own))
    out = await _go()
    assert out.detail == "dry run refused: would break"
    assert out.failure_class is FC.VALIDATION and out.evidence == {}


async def test_a_modules_result_is_passed_through_unclassified(hands):
    ran = _install(run_result=ActionResult(False, "HTTP 401 from x"))
    out = await _go()
    assert ran and out.failure_class is None
