"""Acting on what Miro says, rather than on a button he clicks.

He asked for this twice: say it in Slack or on the phone, and it happens.
That is his Tier 2 approval to give — an approval is an authenticated human
saying yes, and nothing requires the yes to arrive as a mouse click.

What these tests pin is that widening HOW the yes arrives did not widen WHAT
can be said yes to, and did not accept a yes from somewhere that cannot
prove who is speaking.
"""
from __future__ import annotations

import pytest

from atlas.kernel import intent
from atlas.kernel.contracts import Tier


class FakeAction:
    def __init__(self, name, summary="does a thing", params=None):
        self.name = name
        self.summary = summary
        self.params = params or {}


class FakeDecision:
    def __init__(self, tier, allowed=True, reason=""):
        self.tier = tier
        self.allowed = allowed
        self.reason = reason


@pytest.fixture
def registry(monkeypatch):
    """A catalog with one of each tier."""
    actions = {
        "flycheap.rerun": FakeAction("flycheap.rerun", "rerun the scraper"),
        "gitlab.open_issue": FakeAction("gitlab.open_issue", "file an issue",
                                        {"title": "the title"}),
        "billing.payout": FakeAction("billing.payout", "send money"),
    }
    decisions = {
        "flycheap.rerun": FakeDecision(Tier.AUTO),
        "gitlab.open_issue": FakeDecision(Tier.ASK),
        "billing.payout": FakeDecision(Tier.NEVER, allowed=False,
                                       reason="permanently denied: money out"),
    }
    monkeypatch.setattr(intent.REGISTRY, "actions", actions, raising=False)
    monkeypatch.setattr(intent.REGISTRY, "decisions", decisions, raising=False)
    return actions


# ------------------------------------------------------- the closed catalog

def test_tier_three_is_never_offered_to_the_model(registry):
    """Not offered rather than offered-and-refused: a name the model never
    sees is a name it cannot talk itself into."""
    offered = set(intent.catalog())
    assert "billing.payout" not in offered
    assert offered == {"flycheap.rerun", "gitlab.open_issue"}


async def test_tier_three_is_refused_with_his_own_words(registry, monkeypatch, memory_backend):
    """If the registry changed underneath the request, the second check
    catches it -- and quotes the standing instruction back, which is what he
    asked for rather than a generic denial."""
    async def picks_payout(_request):
        return "billing.payout", {}

    monkeypatch.setattr(intent, "choose", picks_payout)
    outcome = await intent.act("send the payout", channel="slack", who="UMIRO")
    assert outcome.ok is False
    assert outcome.ran is False
    assert "no runtime override" in outcome.spoken


async def test_a_hallucinated_action_is_dropped(registry, monkeypatch):
    """The model picks a NAME from a closed list. One it invents never
    reaches the executor."""
    class Completion:
        text = '{"action": "flycheap.wire_the_money", "params": {}}'

    async def fake_think(*a, **k):
        return Completion()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    assert await intent.choose("wire the money") is None


async def test_a_null_action_is_not_a_match(registry, monkeypatch):
    class Completion:
        text = '{"action": null}'

    async def fake_think(*a, **k):
        return Completion()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    assert await intent.choose("make me a sandwich") is None


async def test_fenced_json_is_still_read(registry, monkeypatch):
    """Models fence JSON even when told not to. Failing to parse that would
    read as "no action matched", which is a different and wrong answer."""
    class Completion:
        text = '```json\n{"action": "flycheap.rerun", "params": {}}\n```'

    async def fake_think(*a, **k):
        return Completion()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    assert await intent.choose("rerun it") == ("flycheap.rerun", {})


# ------------------------------------------------- the channel is the identity

async def test_an_inbound_call_may_not_act(registry, monkeypatch, memory_backend):
    """The whole reason the distinction exists. Inbound caller ID is
    trivially forged, so an inbound call is a stranger claiming to be him."""
    async def should_not_run(_request):
        raise AssertionError("an inbound call must not even choose an action")

    monkeypatch.setattr(intent, "choose", should_not_run)
    outcome = await intent.act("restart everything", channel="call_in",
                               who="+15559998888")
    assert outcome.ok is False
    assert "easy to fake" in outcome.spoken


@pytest.mark.parametrize("channel", ["slack", "call_out"])
def test_only_proven_channels_may_approve(channel):
    assert channel in intent.CAN_APPROVE


def test_an_inbound_call_is_not_in_the_allow_list():
    assert "call_in" not in intent.CAN_APPROVE
    assert "voice" not in intent.CAN_APPROVE


# ------------------------------------------------------ it goes through the door

async def test_the_request_reaches_the_executor_as_an_approval(
        registry, monkeypatch, memory_backend):
    """The approval and the request are the same act, recorded as the human
    it came from -- never as "system"."""
    seen = {}

    async def fake_choose(_request):
        return "gitlab.open_issue", {"title": "the scraper is stale"}

    async def fake_execute(req):
        seen["req"] = req
        class Result:
            ok = True
            detail = "issue #12 opened"
        return Result()

    monkeypatch.setattr(intent, "choose", fake_choose)
    monkeypatch.setattr("atlas.kernel.executor.execute", fake_execute)

    outcome = await intent.act("file an issue about the stale scraper",
                               channel="slack", who="UMIRO")
    assert outcome.ran is True
    assert seen["req"].action == "gitlab.open_issue"
    assert seen["req"].approved_by == "slack:UMIRO"
    # The words he actually used travel with it, so the audit log says why.
    assert "stale scraper" in seen["req"].reason


async def test_nothing_matching_is_said_plainly(registry, monkeypatch, memory_backend):
    async def no_match(_request):
        return None

    monkeypatch.setattr(intent, "choose", no_match)
    outcome = await intent.act("book me a flight", channel="slack", who="UMIRO")
    assert outcome.ok is False
    assert "don't have an action that does that" in outcome.spoken


async def test_an_empty_request_does_nothing(registry, memory_backend):
    outcome = await intent.act("   ", channel="slack", who="UMIRO")
    assert outcome.ok is False


async def test_intent_never_imports_a_way_around_the_executor():
    """The structural guarantee: this module adds a way IN, not a way
    around. Nothing here may call an action directly."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(intent))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            attr = getattr(node.func, "attr", "")
            assert attr not in ("run", "dry_run", "rollback"), (
                f"intent calls .{attr}() directly at line {node.lineno}; "
                f"everything must go through executor.execute")


# ---------------------------------------------- his own chat, and nobody else's
#
# A chat message is only an approval when it comes from the chat Miro
# configured as his own. Anyone else's message is data about the world,
# never an instruction -- invariant 4. No messaging chat is configured in
# code today (Telegram and WhatsApp were removed on 2026-09-28), so the
# mechanism is exercised through a stand-in platform, "chat".

REMOVED = (("telegram", "MIRO_TELEGRAM_CHAT_ID", "555"),
           ("whatsapp", "MIRO_WHATSAPP_NUMBER", "15550001111"))


@pytest.fixture
def a_chat(monkeypatch):
    """A principal chat platform, wired the way a real one would be."""
    monkeypatch.setattr(intent, "PRINCIPAL_ENV", {"chat": "MIRO_CHAT_ID"})
    monkeypatch.setattr(intent, "READ_BACK", frozenset({"chat"}))
    monkeypatch.setattr(intent, "CAN_APPROVE", intent.CAN_APPROVE | {"chat"})
    monkeypatch.setenv("MIRO_CHAT_ID", "4242")


def test_principal_channel_is_none_when_nothing_is_configured(monkeypatch, a_chat):
    monkeypatch.delenv("MIRO_CHAT_ID", raising=False)
    assert intent.principal_channel("chat", "4242") is None
    # An empty chat id never matches an empty setting either.
    assert intent.principal_channel("chat", "") is None


def test_principal_channel_matches_only_his_configured_ids(a_chat):
    assert intent.principal_channel("chat", " 4242 ") == "chat"
    assert intent.principal_channel("chat", "4243") is None
    # A platform with no configured principal is never one.
    assert intent.principal_channel("slack", "555") is None


def test_no_messaging_chat_approves_anything():
    assert intent.CAN_APPROVE == frozenset({"slack", "call_out"})
    assert intent.PRINCIPAL_ENV == {}
    assert intent.READ_BACK == frozenset()


@pytest.mark.parametrize("platform,env,chat", REMOVED)
def test_a_removed_platform_can_no_longer_approve(monkeypatch, platform, env, chat):
    """Removed at Miro's request, 2026-09-28. A leftover setting must not
    bring it back as an approval channel, nor as a principal origin at the
    executor."""
    from atlas.kernel.executor import PRINCIPAL_ORIGINS

    monkeypatch.setenv(env, chat)
    assert platform not in intent.CAN_APPROVE
    assert platform not in intent.PRINCIPAL_ENV
    assert platform not in intent.READ_BACK
    assert platform not in PRINCIPAL_ORIGINS
    assert intent.principal_channel(platform, chat) is None


@pytest.mark.parametrize("channel,who", [
    ("chat", "9999"), ("telegram", "555"), ("whatsapp", "15550001111")])
async def test_a_stranger_on_a_messaging_channel_may_not_act(
        registry, monkeypatch, memory_backend, a_chat, channel, who):
    """The channel name alone is not the proof -- the chat id is. A caller
    that passes a principal channel for someone else's chat must be refused
    before a single action is even chosen, and a removed channel refuses
    everyone, even his own old number."""
    for _platform, env, chat in REMOVED:
        monkeypatch.setenv(env, chat)

    async def should_not_run(_request):
        raise AssertionError("a stranger's message must not choose an action")

    monkeypatch.setattr(intent, "choose", should_not_run)
    outcome = await intent.act("restart everything", channel=channel, who=who)
    assert outcome.ok is False
    assert outcome.ran is False


async def test_his_own_chat_runs_tier_one_directly(
        registry, monkeypatch, memory_backend, a_chat):
    seen = {}

    async def fake_choose(_request):
        return "flycheap.rerun", {}

    async def fake_execute(req):
        seen["req"] = req

        class Result:
            ok = True
            detail = "reran"
        return Result()

    monkeypatch.setattr(intent, "choose", fake_choose)
    monkeypatch.setattr("atlas.kernel.executor.execute", fake_execute)
    outcome = await intent.act("rerun the scraper", channel="chat", who="4242")
    assert outcome.ran is True
    assert seen["req"].approved_by == "chat:4242"


async def test_a_tier_two_voice_note_is_a_card_read_back_never_a_run(
        registry, monkeypatch, memory_backend, a_chat):
    """Reel DcbPb2yxrTx, G13. A voice note is a transcription and an
    LLM-chosen action: a mis-hearing must not run a Tier 2 action unseen.
    The card is raised and its text read back; his next "yes" in the same
    chat, or the Slack button, is the approval -- as "do it" is on the
    phone."""
    proposed: list = []

    async def fake_choose(_request):
        return "gitlab.open_issue", {"title": "stale scraper"}

    async def never(_req):
        raise AssertionError("a Tier 2 voice note must not reach the executor")

    async def fake_propose(action, params, **kw):
        proposed.append((action, params, kw))
        return 88

    monkeypatch.setattr(intent, "choose", fake_choose)
    monkeypatch.setattr("atlas.kernel.executor.execute", never)
    monkeypatch.setattr("atlas.kernel.approvals.propose", fake_propose)

    outcome = await intent.act("file an issue", channel="chat", who="4242")
    assert outcome.ran is False and outcome.ok is True
    assert outcome.proposal_id == 88
    assert proposed == [("gitlab.open_issue", {"title": "stale scraper"},
                         proposed[0][2])]
    assert proposed[0][2]["evidence"]["channel"] == "chat"
    said = outcome.spoken
    assert "#88" in said and "file an issue" in said.lower()
    assert "stale scraper" in said
    assert "yes" in said.lower() and "slack" in said.lower()


async def test_slack_typed_tier_two_still_runs_on_the_word(
        registry, monkeypatch, memory_backend):
    """Typed text is not a transcription: the Slack rule is unchanged."""
    ran: list = []

    async def fake_choose(_request):
        return "gitlab.open_issue", {"title": "x"}

    async def fake_execute(req):
        ran.append(req)

        class Result:
            ok = True
            detail = "opened"
        return Result()

    monkeypatch.setattr(intent, "choose", fake_choose)
    monkeypatch.setattr("atlas.kernel.executor.execute", fake_execute)
    outcome = await intent.act("file it", channel="slack", who="U1")
    assert outcome.ran is True and len(ran) == 1


# ----------------------------------------- choose() for a room, not for him

async def test_choose_wraps_a_room_as_untrusted_when_told_it_is_not_him(
        registry, monkeypatch):
    """Words from outside reach the model as data (principal=False); Slack,
    the call ATLAS placed and the signed-in dashboard keep the default."""
    seen: list[str] = []

    class C:
        text = '{"action": "gitlab.open_issue", "params": {"title": "stale"}}'

    async def fake_think(_need, _system, messages, **_kw):
        seen.append(messages[0].content)
        return C()

    monkeypatch.setattr("atlas.kernel.llm.think", fake_think)
    assert await intent.choose("file it", principal=False) == (
        "gitlab.open_issue", {"title": "stale"})
    assert await intent.choose("file it") is not None
    assert seen[0].startswith("<untrusted_data")
    assert seen[1].startswith("<request")
