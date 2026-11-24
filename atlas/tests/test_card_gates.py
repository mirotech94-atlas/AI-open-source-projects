"""Two kernel gates a Tier 2 action whose params ARE the decision relies on.

* The dashboard's spoken door (kernel.spoken) runs Tier 1 Mac actions only,
  and the executor refuses its "voice:" origin as the approval of a Tier 2
  action: the door records a request, never a yes to a card.
* `Action.card_detail` puts the exact params on the card, and
  `Action.card_always` makes a direct Slack request a card, not a run.
"""
from __future__ import annotations

import pytest

from atlas.kernel import executor, intent, spoken
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.executor import ExecutionRequest
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

RAN: list[dict] = []


@pytest.fixture(autouse=True)
def _slack_card_on(monkeypatch):
    """These tests read the Slack card; it is opt-in now (APPROVALS_SLACK)."""
    monkeypatch.setenv("APPROVALS_SLACK", "on")


async def _run(**kw):
    RAN.append(kw)
    return ActionResult(True, "sent")


async def _ok(*_a, **_kw):
    return ActionResult(True, "fine")


def _card(params: dict) -> str:
    return f"To: {params['to']}\nText: {params['text']}"


SEND = Action(name="mac.fake_send", tier=Tier.ASK, summary="Send a message",
              run=_run, dry_run=_ok, irreversible=True, principal_only=True,
              params={"to": "who", "text": "what"}, verbose_card=True,
              card_detail=_card, card_always=True)
VOLUME = Action(name="mac.fake_volume", tier=Tier.AUTO, summary="Volume",
                run=_run, dry_run=_ok, rollback=_ok, principal_only=True,
                params={"level": "0-100"})


@pytest.fixture
def fakes(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    for a in (SEND, VOLUME):
        monkeypatch.setitem(REGISTRY.actions, a.name, a)
        monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    executor._recent.clear()
    RAN.clear()
    return memory_backend


def test_the_spoken_door_takes_tier_1_mac_actions_only(fakes):
    assert spoken.speakable("mac.fake_volume")
    assert not spoken.speakable("mac.fake_send")


async def test_the_spoken_door_refuses_a_tier_2_mac_action(fakes):
    out = await spoken.run_mac_action(
        "mac.fake_send", {"to": "+15551234567", "text": "hi"}, "text mum hi")
    assert not out.ok and RAN == []


async def test_the_executor_never_takes_the_voice_origin_as_a_tier_2_yes(fakes):
    out = await executor.execute(ExecutionRequest(
        "mac.fake_send", {"to": "+15551234567", "text": "hi"}, "said",
        approved_by="voice:dashboard"))
    assert not out.ok and "card" in out.detail and RAN == []


async def test_a_tier_2_mac_action_said_on_the_dashboard_is_a_card(monkeypatch, fakes):
    from atlas.api import voice

    proposed: list = []

    async def choose(text, principal=False):
        return "mac.fake_send", {"to": "+15551234567", "text": "on my way"}

    async def propose(name, params, **kw):
        proposed.append((name, params))
        return 7

    async def never(*_a, **_kw):
        raise AssertionError("a Tier 2 action must not run from the voice door")

    monkeypatch.setattr(intent, "choose", choose)
    monkeypatch.setattr(spoken, "run_mac_action", never)
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    pids, _said = await voice._queue_steps("text mum on my way", source="voice",
                                           principal=True)
    assert pids == [7] and proposed == [("mac.fake_send",
                                         {"to": "+15551234567", "text": "on my way"})]
    assert RAN == []


async def test_card_always_makes_a_direct_slack_request_a_card(monkeypatch, fakes):
    proposed: list = []

    async def choose(_request):
        return "mac.fake_send", {"to": "+15551234567", "text": "late, 10 min"}

    async def never(_req):
        raise AssertionError("card_always must not run on the request alone")

    async def propose(action, params, **kw):
        proposed.append((action, params))
        return 41

    monkeypatch.setattr(intent, "choose", choose)
    monkeypatch.setattr("atlas.kernel.executor.execute", never)
    monkeypatch.setattr("atlas.kernel.approvals.propose", propose)
    out = await intent.act("text mum I'm late", channel="slack", who="UMIRO")
    assert out.ran is False and out.proposal_id == 41 and proposed
    # The read-back carries the exact words, whole, not a 120-char cut.
    assert "To: +15551234567\nText: late, 10 min" in out.spoken


def test_card_text_shows_card_detail_untruncated(fakes):
    long = "x" * 900 + " END"
    said = intent.card_text(3, "mac.fake_send", {"to": "+15551234567", "text": long})
    assert long in said


async def test_card_detail_is_on_the_slack_card(monkeypatch, fakes):
    from atlas.kernel import approvals
    sent = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(approvals, "notify", fake_notify)
    fakes.responses["SELECT id FROM proposals"] = None
    fakes.responses["INSERT INTO proposals"] = {"id": 5}
    fakes.responses["FROM lessons"] = None
    fakes.responses["SELECT"] = []
    text = "see you at 7 -- " + "y" * 600
    await approvals.propose("mac.fake_send", {"to": "Mum", "text": text},
                            reason="Asked out loud: text mum")
    assert sent and f"To: Mum\nText: {text}" in sent[0].body


def test_a_renderer_that_raises_still_leaves_the_params_on_the_card():
    from atlas.kernel.approvals import _card_detail

    def boom(_p):
        raise KeyError("to")

    act = Action(name="mac.fake_boom", tier=Tier.ASK, summary="x", run=_run,
                 irreversible=True, card_detail=boom)
    assert "+1555" in _card_detail(act, {"to": "+1555"})
