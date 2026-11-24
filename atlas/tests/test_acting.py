"""The whole path, end to end: a check fails, a finding is raised, a proposal
is made, and ATLAS acts.

Watch-only is the phase gate, not the capability. These tests run with it off
and prove the machinery works — and that every guard still holds when it does.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from atlas.kernel import detect, executor, reason, scheduler
from atlas.kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY


@pytest.fixture
def armed(monkeypatch):
    """ATLAS with hands. The whole point of these tests."""
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")


@pytest.fixture(autouse=True)
def clean(memory_backend):
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    REGISTRY.checks.clear()
    executor._recent.clear()
    yield
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    REGISTRY.checks.clear()
    executor._recent.clear()


def build_action(log: list, *, name="flycheap.regenerate_links", tier=Tier.AUTO):
    async def run(**kw):
        log.append(("ran", kw))
        return ActionResult(True, "regenerated 6 links", rollback_token="snap-1")

    async def dry(**kw):
        log.append(("dry", kw))
        return ActionResult(True, "would regenerate 6 links")

    async def undo(token):
        log.append(("rolled-back", token))
        return ActionResult(True, "restored")

    action = Action(name=name, tier=tier, summary="regenerate affiliate links",
                    run=run, dry_run=dry, rollback=undo, max_per_hour=3)
    REGISTRY.actions[name] = action
    REGISTRY.decisions[name] = classify(action)
    return action


# ------------------------------------------------------- tier 1 acts alone

async def test_a_tier1_action_executes_unattended(armed):
    """No human in the loop. This is ATLAS fixing something by itself."""
    log: list = []
    build_action(log)

    result = await execute(ExecutionRequest(
        action="flycheap.regenerate_links", params={},
        reason="every sampled link lost its affiliate marker"))

    assert result.ok
    assert result.detail == "regenerated 6 links"
    assert result.rollback_token == "snap-1"
    # Dry run first, always — even unattended.
    assert [step for step, _ in log] == ["dry", "ran"]


async def test_acting_is_audited_before_it_happens(armed, memory_backend):
    """A crash mid-action must still leave a record that it started."""
    log: list = []
    build_action(log)
    await execute(ExecutionRequest("flycheap.regenerate_links", {}, "because"))

    events = [r.event for r in memory_backend.audit_rows]
    assert events.index("execute.start") < events.index("execute.finish")
    assert "execute.dry_run" in events


async def test_a_tier1_action_can_be_rolled_back(armed):
    log: list = []
    build_action(log)
    result = await execute(ExecutionRequest("flycheap.regenerate_links", {}, "x"))
    undo = await executor.rollback("flycheap.regenerate_links",
                                   result.rollback_token or "")
    assert undo.ok
    assert ("rolled-back", "snap-1") in log


# --------------------------------------------- the full scheduler pipeline

async def test_a_failing_check_leads_all_the_way_to_an_action(
        armed, memory_backend, monkeypatch):
    """check -> observation -> finding -> proposal -> execute, unattended."""
    log: list = []
    build_action(log)

    async def failing() -> Observation:
        return Observation(
            check="flycheap.affiliate_links", health=Health.FAILING,
            value=0.0, unit="links",
            detail="0/6 affiliate links intact",
            evidence={"broken": ["https://flycheap.example.com/go/abc"]})

    REGISTRY.checks["flycheap.affiliate_links"] = Check(
        name="affiliate_links", run=failing, interval=timedelta(minutes=5),
        severity_on_fail=Severity.HIGH, baseline_sigma=None)

    async def one() -> int:
        return 1
    monkeypatch.setattr(scheduler, "_check_id", lambda key: one())
    monkeypatch.setattr(scheduler, "record_observation", lambda *_a: _none())
    monkeypatch.setattr(scheduler, "record_schedule", lambda *_a: _none())
    monkeypatch.setattr(scheduler, "record_finding", lambda *_a: one())
    monkeypatch.setattr(scheduler, "link_finding_to_proposal", lambda *_a: _none())
    monkeypatch.setattr(detect, "baseline", lambda *_a, **_k: _none())

    # The reasoner picks an action from the closed catalog.
    async def diagnose(finding):
        return reason.Proposal(
            diagnosis="the affiliate marker is missing from every redirect",
            action="flycheap.regenerate_links", params={},
            reason="regenerate the link table",
            rollback_plan="restore snapshot", confidence=0.9)

    monkeypatch.setattr(reason, "diagnose", diagnose)
    monkeypatch.setattr(scheduler, "expire_stale", lambda: one())

    findings = await scheduler.run_once()
    assert len(findings) == 1
    assert findings[0].severity is Severity.HIGH

    await scheduler.handle(findings)
    assert ("ran", {}) in log, "ATLAS did not act on a failing check"


async def _none():
    return None


# ------------------------------------ tier 2 still requires a human, armed

async def test_a_tier2_action_still_waits_for_approval(armed):
    """Having hands does not mean skipping the gate."""
    log: list = []
    build_action(log, name="instagram.publish", tier=Tier.ASK)

    unapproved = await execute(ExecutionRequest("instagram.publish", {}, "x"))
    assert not unapproved.ok and "unapproved" in unapproved.detail
    assert log == []

    approved = await execute(ExecutionRequest(
        "instagram.publish", {}, "x", approved_by="miro"))
    assert approved.ok
    assert [step for step, _ in log] == ["dry", "ran"]


async def test_a_denied_action_is_refused_even_when_armed(armed):
    """Tier 3 is not a phase. It does not turn off."""
    log: list = []
    build_action(log, name="stripe.payout_create", tier=Tier.ASK)
    result = await execute(ExecutionRequest(
        "stripe.payout_create", {}, "x", approved_by="miro"))
    assert not result.ok
    assert "money out" in result.detail
    assert log == []


async def test_the_rate_limit_still_stops_a_repeating_fix(armed):
    """A fix firing over and over is a symptom, not a solution."""
    log: list = []
    build_action(log)
    for _ in range(3):
        assert (await execute(
            ExecutionRequest("flycheap.regenerate_links", {}, "x"))).ok
    fourth = await execute(ExecutionRequest("flycheap.regenerate_links", {}, "x"))
    assert not fourth.ok
    assert "escalating instead of repeating" in fourth.detail


async def test_arming_is_a_single_switch(monkeypatch):
    """Off by default, and the default is what ships."""
    log: list = []
    build_action(log)

    monkeypatch.delenv("ATLAS_WATCH_ONLY", raising=False)
    assert not (await execute(
        ExecutionRequest("flycheap.regenerate_links", {}, "x"))).ok
    assert log == []

    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    assert (await execute(
        ExecutionRequest("flycheap.regenerate_links", {}, "x"))).ok
    assert ("ran", {}) in log
