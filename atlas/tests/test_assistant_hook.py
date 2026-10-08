"""How a question reaches the assistant: the `ask <q>` / `atlas, <q>` hook
parsed in code through `intent.act`, the dedicated Slack channel, the
module's shape, and its budget check."""
from __future__ import annotations

import asyncio

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import assistant
from atlas.modules.assistant import budget


@pytest.fixture
def on(monkeypatch):
    """The assistant switched on (`ASSISTANT_ENABLED`; off by default)."""
    monkeypatch.setenv(assistant.ENABLED_ENV, "1")


# ------------------------------------------------------------ parse_command

@pytest.mark.parametrize("text, question, mode", [
    ("ask what is the capital of Peru?", "what is the capital of Peru?", None),
    ("Ask: how far is the moon", "how far is the moon", None),
    ("atlas, ask convert 5 miles to km", "convert 5 miles to km", None),
    ("/atlas ask what time is it in Tokyo", "what time is it in Tokyo", None),
    ("atlas, how tall is the Eiffel tower?", "how tall is the Eiffel tower?", None),
    ("hey atlas, what is a hash map", "what is a hash map", None),
    ("atlas: is it raining in Paris?", "is it raining in Paris?", None),
    ("ask! what is the capital of Peru?", "what is the capital of Peru?", "search"),
    ("search the capital of Peru", "the capital of Peru", "search"),
    ("atlas, search: who won the 2026 world cup", "who won the 2026 world cup",
     "search"),
])
def test_questions_are_parsed(text, question, mode):
    got = assistant.parse_command(text)
    assert got == assistant.Request("ask", question, mode)


def test_ask_cancel_is_a_cancel_not_a_question():
    assert assistant.parse_command("ask cancel AB12cd34") == assistant.Request(
        "cancel", run_id="ab12cd34")
    # Not a run id: a question about cancelling.
    assert assistant.parse_command("ask cancel my gym membership how?").kind == "ask"


@pytest.mark.parametrize("text", [
    "atlas, snapshot everything",
    "atlas, restart flycheap",
    "atlas, post the linkedin draft",
    "atlas, can you restart the scraper?",          # a question in shape, an order in fact
    "atlas, block 30 minutes at noon",
    "atlas,",
    "ask",
    "search",
    "asking around about the deploy",
    "searching for the deploy log",
    "funnel list",
    "what is the capital of Peru?",                 # not addressed at all
    "the task is to ask the client",
    # The agency's job, said with a lookup verb: an order for missions.
    ("search up the 50 local businesses that have a very poor website or no "
     "website at all and make them one"),
])
def test_orders_and_unaddressed_text_are_left_for_the_picker(text):
    assert assistant.parse_command(text) is None


# -------------------------------------------------------- the module hook

async def test_the_command_hook_answers_a_question_and_declines_the_rest(
        monkeypatch, on):
    seen: list[tuple] = []

    async def fake_answer(question, *, channel, who, skill=None, mode=None):
        seen.append((question, channel, who))
        return "Lima.\n\nSources:\n- https://example.org (read 15:00 UTC, 2026-10-06)"

    monkeypatch.setattr(assistant, "answer", fake_answer)
    out = await assistant.MODULE.command("ask what is the capital of Peru?",
                                         channel="slack", who="UMIRO")
    assert out is not None and out.ok and out.spoken.startswith("Lima.")
    assert out.action is None and out.ran is False
    assert seen == [("what is the capital of Peru?", "slack", "UMIRO")]
    assert await assistant.MODULE.command("snapshot everything", channel="slack",
                                          who="UMIRO") is None


async def test_intent_act_reaches_the_hook_only_from_a_channel_that_proves_him(
        monkeypatch, memory_backend, on):
    """The hook sits behind `intent.act`'s channel check, like `funnel`."""
    from atlas.kernel import intent

    seen: list[str] = []

    async def fake_answer(question, *, channel, who, skill=None, mode=None):
        seen.append(question)
        return "Lima."

    monkeypatch.setattr(assistant, "answer", fake_answer)
    monkeypatch.setattr(intent.REGISTRY, "modules", {"assistant": assistant.MODULE},
                        raising=False)
    refused = await intent.act("ask what is the capital of Peru?", channel="call_in",
                               who="+1555")
    assert refused.ok is False and seen == []
    out = await intent.act("ask what is the capital of Peru?", channel="slack",
                           who="UMIRO")
    assert out.ok and out.spoken == "Lima." and seen == ["what is the capital of Peru?"]


def test_the_module_has_no_actions_and_needs_no_credential(monkeypatch):
    assert assistant.MODULE.actions() == ()
    assert assistant.MODULE.requires_env == ()
    # Off by default: no check on the board for a module nobody can ask.
    monkeypatch.delenv(assistant.ENABLED_ENV, raising=False)
    assert assistant.MODULE.checks() == ()
    monkeypatch.setenv(assistant.ENABLED_ENV, "true")
    assert [c.name for c in assistant.MODULE.checks()] == ["budget"]
    assert "search_web" in assistant.MODULE.tools()
    assert any(s["name"] == "fact_check" for s in assistant.MODULE.skills())


def test_the_voice_tool_exists_but_is_not_one_the_loop_may_call():
    from atlas.kernel.tools import TOOLS
    from atlas.modules.assistant.tools import ASSISTANT_TOOLS
    assert TOOLS[assistant.VOICE_TOOL].read is not None
    assert assistant.VOICE_TOOL not in ASSISTANT_TOOLS


def test_no_agent_holds_an_assistant_tool():
    from atlas.kernel.agents import ROSTER, build_roster
    from atlas.modules.assistant.tools import ASSISTANT_TOOLS
    build_roster()
    holders = [a.name for a in ROSTER.values()
               if a.tools & (ASSISTANT_TOOLS | {assistant.VOICE_TOOL})]
    assert holders == []


# --------------------------------------------------------- the Slack door

def test_the_assistant_channel_is_closed_unless_named(monkeypatch, on):
    from atlas.api import slack_events
    monkeypatch.delenv("SLACK_ASSISTANT_CHANNEL", raising=False)
    monkeypatch.delenv("SLACK_CHANNELS", raising=False)
    assert slack_events._assistant_channel("C0ASK") is False
    monkeypatch.setenv("SLACK_ASSISTANT_CHANNEL", "C0ASK")
    assert slack_events._assistant_channel("C0ASK") is True
    assert slack_events._assistant_channel("C0OTHER") is False
    assert slack_events._assistant_channel("") is False
    monkeypatch.delenv("SLACK_ASSISTANT_CHANNEL")
    monkeypatch.setenv("SLACK_CHANNELS", "assistant:C0ASK2")
    assert slack_events._assistant_channel("C0ASK2") is True


def test_a_plain_message_in_the_assistant_channel_is_a_question(monkeypatch, on):
    from atlas.api import slack_events
    from atlas.kernel import intent

    asked: list[tuple] = []
    replied: list = []

    async def fake_answer(question, *, channel, who, skill=None, mode=None):
        asked.append((question, channel, who))
        return "Lima.\n\nSources:\n- https://example.org (read 15:00 UTC)"

    async def never_act(text, *, channel, who):
        raise AssertionError("intent.act must not see a question in the assistant channel")

    async def fake_notify(msg):
        replied.append(msg)
        return ["slack"]

    monkeypatch.setenv("SLACK_ASSISTANT_CHANNEL", "C0ASK")
    monkeypatch.setattr(assistant, "answer", fake_answer)
    monkeypatch.setattr(intent, "act", never_act)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)

    asyncio.run(slack_events._request("atlas, snapshot everything", "UMIRO", "C0ASK",
                                      "7.7"))

    assert asked == [("snapshot everything", "slack", "UMIRO")]
    assert replied and replied[0].body.startswith("Lima.")
    assert replied[0].channel == "C0ASK" and replied[0].thread == "7.7"


def test_an_explicit_ask_anywhere_goes_to_the_assistant_not_chat(monkeypatch, on):
    """A question-shaped message is otherwise chat or knowledge in Slack
    before intent.act is ever reached; `ask <q>` must not be swallowed."""
    from atlas.api import slack_events
    from atlas.kernel import intent, knowledge

    asked: list[str] = []
    replied: list = []

    async def fake_answer(question, *, channel, who, skill=None, mode=None):
        asked.append(question)
        return "Lima."

    async def never(*_a, **_kw):
        raise AssertionError("neither chat, knowledge nor intent.act may take an ask")

    async def fake_notify(msg):
        replied.append(msg)
        return ["slack"]

    monkeypatch.delenv("SLACK_ASSISTANT_CHANNEL", raising=False)
    monkeypatch.setattr(assistant, "answer", fake_answer)
    monkeypatch.setattr(intent, "act", never)
    monkeypatch.setattr(knowledge, "answer", never)
    monkeypatch.setattr(slack_events, "_converse", never)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)

    asyncio.run(slack_events._request("atlas, ask what is the capital of Peru?",
                                      "UMIRO", "CRANDOM", "7.8"))

    assert asked == ["what is the capital of Peru?"]
    assert replied and replied[0].body == "Lima." and replied[0].channel == "CRANDOM"
    assert assistant.parse_explicit("what is the capital of Peru?") is None


def test_the_word_assistant_is_not_a_name_of_his_estate():
    """The module's name is ordinary English: a question about assistants
    is still a question about the world, and may still go to Perplexity."""
    from atlas.api import voice
    from atlas.modules import _perplexity
    assert voice._mentions_a_name("how do I hire a virtual assistant") is False
    assert _perplexity.names_own_system("best assistant apps") is False


def test_the_topic_has_its_own_env_and_project(monkeypatch):
    from atlas.channels.slack import TOPIC_CHANNEL_ENV, SlackChannel, project
    assert TOPIC_CHANNEL_ENV["assistant"] == "SLACK_ASSISTANT_CHANNEL"
    monkeypatch.setenv("SLACK_ASSISTANT_CHANNEL", "C0ASK")
    assert SlackChannel().channel_for("assistant") == "C0ASK"
    assert project("assistant") == "Atlas"


# ---------------------------------------------------------------- budget

async def test_the_budget_check_is_unknown_when_the_log_cannot_be_counted(
        memory_backend):
    got = await assistant.check_budget()
    assert got.health is Health.UNKNOWN and "could not be read" in got.detail


async def test_the_budget_check_reads_the_counts_with_a_time(memory_backend, monkeypatch):
    memory_backend.responses["FROM actions_log WHERE event = $1 AND subject = ANY"] = {
        "n": 3}
    got = await assistant.check_budget()
    assert got.health is Health.OK and got.value == 6.0
    assert "3/40 searches; 3/80 fetches" in got.detail and "UTC" in got.detail
    monkeypatch.setenv(budget.SEARCH_ENV, "3")
    got = await assistant.check_budget()
    assert got.health is Health.DEGRADED and "cap is spent" in got.detail


def test_caps_come_from_the_environment_with_sane_defaults(monkeypatch):
    assert budget.cap_for("search_web") == 40
    assert budget.cap_for("read_page") == 80
    assert budget.cap_for("calculate") is None
    monkeypatch.setenv(budget.SEARCH_ENV, "nonsense")
    assert budget.cap_for("search_web") == 40
    monkeypatch.setenv(budget.SEARCH_ENV, "-1")
    assert budget.cap_for("search_web") == 40
    monkeypatch.setenv(budget.FETCH_ENV, "5")
    assert budget.cap_for("weather_now") == 5


async def test_allowance_fails_closed(memory_backend):
    got = await budget.allowance("search_web")
    assert got.ok is False and "could not be read" in got.reason
    assert (await budget.allowance("calculate")).ok is True


# ---------------------------------------------------------------- privacy

@pytest.mark.parametrize("question, private", [
    ("who emailed me today?", True),
    ("what's in my calendar tomorrow?", True),
    ("summarise my inbox", True),
    ("what did I visit yesterday?", True),
    ("how is my health looking", True),
    ("what is my school grade in physics", True),
    ("what are the symptoms of flu?", False),
    ("tell me the history of Rome", False),
    ("how does IMAP work?", False),
    ("what is the weather in Paris?", False),
    ("compare gmail and outlook", False),
])
def test_private_questions_are_told_apart_from_general_ones(question, private):
    from atlas.modules.assistant.privacy import private_reason
    assert (private_reason(question) is not None) is private, question
