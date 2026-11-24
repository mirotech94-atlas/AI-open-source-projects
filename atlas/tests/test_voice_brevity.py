"""Spoken replies are short (Miro, 2026-09-29: "Not too chatty -- simple and
straight to the point").

Pinned here: every canned line voice can say is at most two sentences and 35
words with no config names or file paths; the prompts ask for the same; and
what is SAID can be shorter than what is typed -- never by rewriting, only by
leaving out the "More:" section and a parenthesis naming a switch -- so an
"unknown" or a refusal's reason is still said.
"""
from __future__ import annotations

import inspect
import re

import pytest

from atlas.api import voice
from atlas.kernel import knowledge

_CONFIG = re.compile(r"\b[A-Z][A-Z0-9]+_[A-Z0-9_]*|[\w./-]+\.(?:py|sh|json)\b")


def assert_brief(said: str) -> None:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", said.strip()) if s]
    assert len(sentences) <= 2, said
    assert len(said.split()) <= 35, said
    assert not _CONFIG.search(said), said


@pytest.mark.parametrize("line", [voice.ASK, voice.ASK_PHONE, voice.NO_ACTION])
def test_the_canned_lines_are_brief(line):
    assert_brief(line)


def test_the_card_line_still_says_queued_and_approve():
    said = voice.ASK.lower()
    assert "queued" in said and "approve" in said and "done" not in said


def test_every_voice_prompt_asks_for_two_sentences_and_no_config_names():
    style = voice.VOICE_STYLE
    assert "one to three short sentences" in style and "45 words" in style
    assert "never restate the question" in style
    assert "config" in style and "file paths" in style
    # Brevity never drops honesty.
    assert "If you don't know, or can't, say so" in style
    assert "unreachable" in voice.FIGURES_LINE and "as of 7:52" in voice.FIGURES_LINE
    # spoken_turn is the record-keeping wrapper (kernel/tasks); the prompt
    # is built in _spoken_turn.
    for fn in (voice._ask, voice._spoken_turn):   # `ask` wraps `_ask` with the task row
        source = inspect.getsource(fn)
        assert "VOICE_STYLE" in source and "ACTION_STYLE" in source
        assert "three short sentences" not in source


def test_the_action_prompt_is_one_sentence_before_the_card_line():
    assert "ONE short sentence" in voice.ACTION_STYLE
    assert "claim to have done it" in voice.ACTION_STYLE


async def test_live_status_asks_for_the_read_time_said_compactly(monkeypatch):
    async def rows(sql, *_a):
        return []

    monkeypatch.setattr(voice, "fetch_all", rows)
    context = await voice.live_context()
    assert "as of 7:52" in context
    assert "never call it fine" in context


@pytest.mark.parametrize("typed,said", [
    ("Two checks can't run: calendar (no GOOGLE_ key) and gitlab (no GITLAB_TOKEN).",
     "Two checks can't run: calendar and gitlab."),
    ("Sites are off (bootstrap/getlayers-connect.py). 38 of 41 fine (3/41 unknown).",
     "Sites are off. 38 of 41 fine (3/41 unknown)."),
    ("Short answer.\n\nMore: the long part.", "Short answer."),
])
def test_what_is_said_leaves_out_switches_and_the_more_section(typed, said):
    assert voice.for_speech(typed) == said


def test_nothing_to_leave_out_means_say_it_all():
    assert voice.for_speech("CloudHost is unknown right now.") is None
    assert voice.for_speech(None) is None


def test_a_medical_answer_still_says_see_a_professional():
    text = ("It's usually harmless.\n\nMore: detail.\nsources\n"
            "See a doctor if it lasts more than a week.")
    said = knowledge.spoken_part(text)
    assert said.startswith("It's usually harmless.")
    assert said.endswith("See a doctor if it lasts more than a week.")
    assert "detail" not in said


def test_the_self_model_is_said_in_two_sentences():
    full = ("I'm running. Up 3 hours, 0 errors since deploy, 38 of 41 checks fine. "
            "Did 2 things. Where you are: home (miro on 203.0.113.7).")
    assert voice.first_sentences(full, 2) == (
        "I'm running. Up 3 hours, 0 errors since deploy, 38 of 41 checks fine.")


def test_the_knowledge_prompt_leads_with_one_or_two_sentences():
    assert "one or two short sentences" in knowledge.EXPERT_SYSTEM
