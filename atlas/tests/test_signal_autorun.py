"""A signal that suggests a Tier 1 action on the closed SIGNAL_AUTORUN list
runs it through the executor instead of raising a card (the owner, 2026-10-12;
memory approval-policy). A refusal is still the card, reason first; a hold
(watch-only, a pause, an hourly ceiling) is neither; anything not on the
list, card_only, principal_only or Tier 2 is a card exactly as before."""
from __future__ import annotations

import pytest

from atlas.kernel import approvals, comms, executor, scheduler
from atlas.kernel.contracts import (
    Action,
    ActionResult,
    FailureClass,
    Severity,
    Signal,
    Tier,
)
from atlas.kernel.executor import HOLD
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY


async def _ok(**_kw):
    return ActionResult(True, "ok")


async def _undo(_token):
    return ActionResult(True, "undone")


def _action(name: str, tier: Tier, **kw) -> Action:
    return Action(name=name, tier=tier, summary=name, run=_ok, dry_run=_ok,
                  rollback=_undo, params={"item_id": "id"}, **kw)


class _Module:
    def __init__(self, signal: Signal):
        self._signal = signal

    async def signals(self):
        return (self._signal,)


@pytest.fixture
def wired(monkeypatch, memory_backend):
    told: list = []
    proposed: list = []
    executed: list = []
    outcome = {"result": ActionResult(True, "drafted item 7")}

    async def notify(msg):
        told.append(msg)
        return ["slack"]

    async def propose(**kw):
        proposed.append(kw)
        return 41

    async def execute(req):
        executed.append(req)
        return outcome["result"]

    monkeypatch.setattr(comms, "notify", notify)
    monkeypatch.setattr(approvals, "propose", propose)
    monkeypatch.setattr(scheduler, "propose", propose)
    monkeypatch.setattr(executor, "execute", execute)
    memory_backend.responses["SELECT id FROM signals"] = None

    def install(action: Action, signal: Signal) -> None:
        monkeypatch.setattr(REGISTRY, "actions", {action.name: action})
        monkeypatch.setattr(REGISTRY, "decisions", {action.name: classify(action)})
        monkeypatch.setattr(REGISTRY, "modules", {"school": _Module(signal)})

    return install, told, proposed, executed, outcome


def _signal(action: str) -> Signal:
    return Signal(title="Due within 24h: CS101 lab 3", body="the lab is due",
                  severity=Severity.HIGH, suggests=action, params={"item_id": 7})


async def test_a_listed_tier1_action_runs_and_sends_a_receipt_not_a_card(wired):
    install, told, proposed, executed, _ = wired
    install(_action("school.draft_work", Tier.AUTO), _signal("school.draft_work"))
    await scheduler.collect_signals()
    assert [r.action for r in executed] == ["school.draft_work"]
    assert executed[0].approved_by is None
    assert executed[0].params == {"item_id": 7}
    assert proposed == []
    assert len(told) == 1 and told[0].kind == "receipt"
    assert "drafted item 7" in told[0].body and "the lab is due" in told[0].body
    assert told[0].severity is Severity.HIGH        # a deadline keeps its weight


async def test_a_refusal_is_still_the_card_with_the_reason_first(wired):
    install, told, proposed, executed, outcome = wired
    outcome["result"] = ActionResult(
        False, "dry run refused: item 7 is not draftable",
        failure_class=FailureClass.VALIDATION, dispatched=False)
    install(_action("school.draft_work", Tier.AUTO), _signal("school.draft_work"))
    await scheduler.collect_signals()
    assert len(executed) == 1 and len(proposed) == 1
    assert proposed[0]["reason"].startswith(
        "Held for you: item 7 is not draftable.")
    assert told == []


async def test_a_hold_is_a_plain_notice_never_swallowed_and_never_a_card(wired):
    """Watch-only (the default), a pause, an hourly ceiling: the signal is
    still told to him -- a deadline reminder must not vanish -- but not as a
    card, which an approval could not run either."""
    install, told, proposed, executed, outcome = wired
    outcome["result"] = ActionResult(
        False, "watch-only", evidence={HOLD: "watch_only"},
        failure_class=FailureClass.DISABLED, dispatched=False)
    install(_action("school.draft_work", Tier.AUTO), _signal("school.draft_work"))
    await scheduler.collect_signals()
    assert len(executed) == 1 and proposed == []
    [notice] = told
    assert notice.title == "Due within 24h: CS101 lab 3"
    assert notice.body == "the lab is due" and notice.kind == "alert"
    assert notice.severity is Severity.HIGH


async def test_an_autorun_that_raises_is_the_card_and_the_cycle_goes_on(wired):
    install, _told, proposed, _executed, _ = wired

    async def boom(req):
        raise RuntimeError("dry run blew up")
    import atlas.kernel.executor as ex
    ex_execute = ex.execute
    try:
        ex.execute = boom
        install(_action("school.draft_work", Tier.AUTO),
                _signal("school.draft_work"))
        await scheduler.collect_signals()
    finally:
        ex.execute = ex_execute
    [card] = proposed
    assert card["reason"].startswith("Held for you: it could not run")


@pytest.mark.parametrize("action", [
    _action("school.draft_work", Tier.ASK),                   # not Tier 1
    _action("school.draft_work", Tier.AUTO, card_only=True),  # the card is the point
    _action("school.draft_work", Tier.AUTO, principal_only=True),
    _action("school.other_thing", Tier.AUTO),                 # not on the list
])
async def test_anything_else_is_a_card_exactly_as_before(wired, action):
    install, _told, proposed, executed, _ = wired
    install(action, _signal(action.name))
    await scheduler.collect_signals()
    assert executed == []
    assert len(proposed) == 1 and proposed[0]["reason"] == "the lab is due"


async def test_the_switch_off_sends_it_back_to_a_card(wired, monkeypatch):
    install, _told, proposed, executed, _ = wired
    monkeypatch.setenv("ATLAS_SIGNAL_AUTORUN", "off")
    install(_action("school.draft_work", Tier.AUTO), _signal("school.draft_work"))
    await scheduler.collect_signals()
    assert executed == [] and len(proposed) == 1


def test_the_list_holds_no_card_only_or_tier3_action():
    # Closed, and never the Whop record or anything the owner must do himself.
    assert "clips.submit_to_whop" not in scheduler.SIGNAL_AUTORUN
    assert "clips.hand_off" not in scheduler.SIGNAL_AUTORUN
    assert scheduler.SIGNAL_AUTORUN == {
        "school.draft_work", "messages.draft", "linkedin.approve_draft"}


def test_every_why_a_card_line_names_a_tier2_action(monkeypatch):
    """board.WHY_A_CARD explains the cards that deliberately stay Tier 2;
    a line for an action that runs itself (or does not exist) would lie."""
    import importlib
    import pkgutil

    from atlas import modules as pkg
    from atlas.kernel import board
    tiers: dict[str, Tier] = {}
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        instance = getattr(importlib.import_module(f"atlas.modules.{info.name}"),
                           "MODULE", None)
        for action in (instance.actions() if instance is not None else ()):
            tiers[action.name] = action.tier
    for name in board.WHY_A_CARD:
        if name in ("clips.submit_to_whop", "academy.send_support_reply"):
            # Tier 1 in code, carded only when a check says no (support) or
            # card_only because the card records the owner's own act (Whop).
            assert tiers[name] is Tier.AUTO
            continue
        assert tiers.get(name) is Tier.ASK, name
    lead = board.card_lead("delegate.claude_code", {}, None)
    assert lead.endswith("_Why this is a card: it runs code on your Mac._")
