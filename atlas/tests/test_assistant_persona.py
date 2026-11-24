"""The best-friend persona: warm voice, no tools and no status in casual talk.

Miro, 2026-11-19. Live bug: a chat reply about an octopus added the sensor
status from earlier turns. Casual talk gets no tools and no status notes; a
question about his systems keeps every tool it had (invariant 6: figures come
only from a tool), and the persona never loosens privacy or tiers.
"""
from __future__ import annotations

import pytest

from atlas.api import voice
from atlas.kernel import private_data
from atlas.kernel.llm import Completion
from atlas.modules.assistant import loop, persona


@pytest.fixture(autouse=True)
def _env(monkeypatch, memory_backend):
    for name in ("ASSISTANT_ENABLED", "ASSISTANT_DOORS", "ASSISTANT_PROPOSE",
                 "ASSISTANT_PROFILE", "ATLAS_LOCAL_FIRST",
                 "ASSISTANT_MAX_TOOL_CALLS"):
        monkeypatch.delenv(name, raising=False)


def _done(text=""):
    return Completion(text=text, provider="fake", model="fake-1", input_tokens=5,
                      output_tokens=5, tool_calls=None)


class Script:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.turns: list[dict] = []

    async def __call__(self, need, system, messages, tools=None, max_tokens=2048, **kw):
        self.turns.append({"system": system, "tools": tools,
                           "private_run": private_data.PRIVATE_RUN.get()})
        return self.replies.pop(0) if self.replies else _done("ok")


@pytest.mark.parametrize("text", [
    "I had a rough day",
    "tell me about octopuses",
    "how are you doing",
    "thanks, that helped",
    "what is the capital of France",
])
def test_plain_talk_is_casual(text):
    assert persona.is_casual(text)


@pytest.mark.parametrize("text", [
    "how many sensors are online",
    "any new email",
    "what is the status of atlas",
    "what time is it",
    "convert 5 miles to km",
    "what is 15% of 80",
    "how is cloudhost doing",
    "summarize my unread email",
])
def test_systems_figures_and_clock_questions_are_not_casual(text):
    assert not persona.is_casual(text)


async def _turn(monkeypatch, question, channel="dashboard"):
    script = Script(_done("ok"))
    monkeypatch.setattr(loop, "think", script)
    await loop.ask(question, channel=channel, extended=True, private_ok=True)
    first = script.turns[0]
    return {t["name"] for t in first["tools"] or []}, first["system"]


async def test_casual_chat_is_offered_no_tool_and_no_status_note(monkeypatch):
    offered, system = await _turn(monkeypatch, "I had a rough day, honestly")
    assert offered == set()
    assert loop.ATLAS_NOTE not in system and loop.SENSOR_NOTE not in system
    assert "atlas_status" not in system and "sensor_status" not in system
    assert persona.PERSONA in system and persona.CASUAL_NOTE in system
    assert loop.ASK_NOTE not in system and "`search" not in system.replace(
        persona.CASUAL_NOTE, "")


async def test_the_octopus_turn_after_sensor_talk_gets_no_sensor_tool(monkeypatch):
    offered, system = await _turn(monkeypatch, "tell me something about octopuses")
    assert "sensor_status" not in offered
    assert loop.SENSOR_NOTE not in system


async def test_a_systems_question_keeps_its_tools_and_notes(monkeypatch):
    offered, system = await _turn(monkeypatch, "how many sensors are online")
    assert "sensor_status" in offered
    assert loop.SENSOR_NOTE in system
    assert persona.CASUAL_NOTE not in system
    assert persona.PERSONA in system            # same voice, with its tools


async def test_the_clock_question_keeps_the_clock_tool(monkeypatch):
    offered, _ = await _turn(monkeypatch, "what time is it")
    assert "time_now" in offered


async def test_a_private_question_stays_private_and_local(monkeypatch):
    script = Script(_done("ok"))
    monkeypatch.setattr(loop, "think", script)
    await loop.ask("summarize my unread email", channel="dashboard",
                   extended=True, private_ok=True)
    first = script.turns[0]
    assert first["private_run"] is True
    assert persona.CASUAL_NOTE not in first["system"]
    assert {t["name"] for t in first["tools"] or []} & loop.PRIVATE_ONLY_TOOLS


async def test_slack_keeps_the_terse_identity_without_the_persona(monkeypatch):
    _, system = await _turn(monkeypatch, "tell me about octopuses", channel="slack")
    assert persona.PERSONA not in system


def test_the_persona_keeps_the_rules_that_matter():
    text = persona.PERSONA.lower()
    assert "no bullet" in text and "as an ai" in text      # forbidden, named as such
    assert "not sure of a fact" in text
    assert "question" in text                              # the gentle follow-up


# ------------------------------------------------------------------ voice

async def test_a_casual_voice_turn_has_the_persona_and_no_live_status(monkeypatch):
    async def boom(sql, *a):
        raise AssertionError("a casual voice turn must not read the board")
    monkeypatch.setattr(voice, "fetch_all", boom)
    system = await voice.voice_system("I had a rough day")
    assert persona.PERSONA in system
    assert "LIVE STATUS" not in system
    assert "decision(s) waiting" not in system


async def test_a_voice_status_question_keeps_the_live_board(monkeypatch):
    async def rows(sql, *a):
        return []
    monkeypatch.setattr(voice, "fetch_all", rows)
    system = await voice.voice_system("how is cloudhost doing")
    assert "LIVE STATUS" in system and persona.PERSONA in system


async def test_a_voice_order_keeps_the_action_style(monkeypatch):
    async def rows(sql, *a):
        return []
    monkeypatch.setattr(voice, "fetch_all", rows)
    system = await voice.voice_system("restart the nexus agent", wants_action=True)
    assert voice.ACTION_STYLE in system



async def test_a_private_run_marks_the_tracked_request_private(monkeypatch):
    from atlas.kernel import tasks
    marked = []

    async def note():
        marked.append(True)
    monkeypatch.setattr(tasks, "note_private", note)
    monkeypatch.setattr(loop, "think", Script(_done("ok")))
    await loop.ask("summarize my unread email", channel="dashboard",
                   extended=True, private_ok=True)
    assert marked == [True]
    marked.clear()
    await loop.ask("tell me about octopuses", channel="dashboard",
                   extended=True, private_ok=True)
    assert marked == []
