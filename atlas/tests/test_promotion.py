"""'Earn it' (the owner, 2026-09-28): a reversible Tier 2 action approved enough
times in a row is OFFERED for promotion; nothing here changes a tier."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import promotion

NOW = datetime.now(UTC)


def _d(n):
    return NOW - timedelta(days=n)


def test_a_streak_counts_approvals_and_skips_silence():
    s = [("executed", _d(1)), ("expired", _d(2)), ("executed", _d(3)),
         ("declined", _d(4)), ("executed", _d(5))]
    n, since = promotion.streak_of(s)
    assert n == 2 and since == _d(3)


def test_a_failure_ends_the_streak():
    assert promotion.streak_of([("failed", _d(1)), ("executed", _d(2))])[0] == 0


def _action(name, tier, *, rollback=True, irreversible=False):
    from atlas.kernel.contracts import Action, ActionResult
    from atlas.kernel.permissions import classify
    from atlas.kernel.registry import REGISTRY

    async def run(**kw):
        return ActionResult(True, "done", rollback_token="t")

    async def undo(token):
        return ActionResult(True, "undone")

    a = Action(name=name, tier=tier, summary=name, run=run, dry_run=run,
               rollback=undo if rollback else None, irreversible=irreversible)
    REGISTRY.actions[name] = a
    REGISTRY.decisions[name] = classify(a)


@pytest.fixture
def actions(monkeypatch):
    from atlas.kernel.contracts import Tier
    from atlas.kernel.registry import REGISTRY
    monkeypatch.setattr(REGISTRY, "actions", {})
    monkeypatch.setattr(REGISTRY, "decisions", {})
    _action("mail.apply_labels", Tier.ASK)
    _action("content.publish_reel", Tier.ASK, rollback=False, irreversible=True)
    _action("flycheap.regenerate_links", Tier.AUTO)


def test_only_reversible_tier_2_actions_are_eligible(actions):
    assert set(promotion.eligible_actions()) == {"mail.apply_labels"}


async def test_an_earned_streak_is_offered_and_nothing_else_changes(actions, monkeypatch):
    from atlas.kernel import state
    from atlas.kernel.registry import REGISTRY
    tier_before = REGISTRY.decisions["mail.apply_labels"].tier

    async def fetch_all(sql, *args):
        if "FROM proposals" in sql:
            return [{"action": "mail.apply_labels", "status": "executed",
                     "decided_at": _d(i)} for i in range(6)]
        return []

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    [c] = await promotion.candidates()
    assert c.action == "mail.apply_labels" and c.streak == 6 and not c.requested
    text = promotion.render([c])
    assert "approved 6 in a row" in text and "never switched by ATLAS at runtime" in text
    assert REGISTRY.decisions["mail.apply_labels"].tier is tier_before


async def test_asked_to_automate_is_offered_after_one_approval(actions, monkeypatch):
    from atlas.kernel import state

    async def fetch_all(sql, *args):
        if "FROM proposals" in sql:
            return [{"action": "mail.apply_labels", "status": "executed",
                     "decided_at": _d(1)}]
        return [{"subject": "mail.apply_labels"}]

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    [c] = await promotion.candidates()
    assert c.requested and c.streak == 1


async def test_a_short_streak_is_not_offered(actions, monkeypatch):
    from atlas.kernel import state

    async def fetch_all(sql, *args):
        if "FROM proposals" in sql:
            return [{"action": "mail.apply_labels", "status": "executed",
                     "decided_at": _d(i)} for i in range(2)]
        return []

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    assert await promotion.candidates() == []
