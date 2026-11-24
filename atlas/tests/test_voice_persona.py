"""The house voice (the owner, 2026-10-12: "yes wire the email prompt in").

The persona is stored once; its tone and banned list reach every prompt
that drafts an outbound reply; its identity line and sign-off reach none of
the auto-sent ones (each module's own AI sign-off stays, written by code);
the post-check catches a banned phrase or a company sign-off.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from atlas.kernel import voice_persona as vp
from atlas.modules import messages
from atlas.modules.academy import support
from atlas.modules.agency import replies
from atlas.modules.linkedin import requests as lr


def test_the_persona_is_stored_verbatim_once():
    assert vp.EMAIL_PERSONA.startswith("You are the owner from ATLAS Academy / Mapfront")
    assert '"Cheers, the owner"' in vp.EMAIL_PERSONA
    assert "Never pretend to be someone else" in vp.EMAIL_PERSONA
    for phrase in vp.BANNED_PHRASES:
        assert f'"{phrase}"' in vp.EMAIL_PERSONA


def test_the_style_block_keeps_the_tone_and_drops_identity_and_sign_off():
    style = vp.EMAIL_STYLE
    assert "friendly human who cares" in style
    assert "Answer the question first" in style
    assert "never guess" in style.lower()
    assert "the owner" not in style and "Cheers" not in style and "Talk soon" not in style
    assert "60-150" not in style
    assert "those rules win" in style
    for phrase in vp.BANNED_PHRASES:
        assert phrase in style
    assert "{" not in style and "}" not in style        # safe inside .format()


def _formatted_prompts() -> dict[str, str]:
    facts = lr.FACTS["guardian"]
    return {
        "support": support.SYSTEM.format(sources="[S1] t (s)\ntext"),
        "agency": replies.REPLY_SYSTEM.format(length="L", playbook="P", prices="$",
                                              deliverables="D"),
        "linkedin": lr.SYSTEM.format(name=facts.name, link=facts.link,
                                     contact=facts.contact, what=facts.what,
                                     services=", ".join(facts.services),
                                     role=facts.role, extra=facts.extra),
        "messages": messages.DRAFT_SYSTEM,
    }


@pytest.mark.parametrize("where", ["support", "agency", "linkedin", "messages"])
def test_the_style_is_in_every_wired_prompt(where):
    prompt = _formatted_prompts()[where]
    assert vp.EMAIL_STYLE in prompt
    # Never the persona's identity: each module's rules on who writes stand.
    assert "You are the owner" not in prompt and "Cheers, the owner" not in prompt


def test_each_module_keeps_its_own_identity_rules():
    p = _formatted_prompts()
    assert "never claim to be the owner or a person" in p["support"]
    assert "Never claim to be the owner or a person" in p["agency"]
    assert "never claim to be the owner or a person" in p["linkedin"]
    assert "no sign-off (one is added)" in p["linkedin"]
    assert lr.signoff("guardian").endswith("— Guardian's assistant (the owner sees this "
                                           "thread)")


@pytest.mark.parametrize("text", [
    "I hope this email finds you well. Your site is ready.",
    "I HOPE THIS EMAIL FINDS YOU WELL",
    "Let's delve into the setup.",
    "It works seamlessly with your phone.",
    "We can leverage your reviews.",
    "This will unlock more bookings.",
    "Honestly a game-changer for you.",
    "Don't hesitate to reach out.",
    "Don’t hesitate to reach out.",                  # a curly apostrophe
    "As per my last email, the link is below.",
    "Thanks!\n\nBest regards,\nThe Team",
    "Best regards, The Team",
])
def test_banned_phrases_are_caught(text):
    assert vp.voice_problems(text), text
    assert vp.voice_refusal(text) is not None


@pytest.mark.parametrize("text", [
    ("Glad you wrote back. The preview is live, so have a look when you can.\n\n"
     "Cheers, the owner"),
    "Best regards,\nOwner",                         # a person signing, not "The Team"
    "Our team will look at the deadlock this week.",
])
def test_plain_text_passes(text):
    assert vp.voice_problems(text) == []


def test_inflections_of_a_banned_word_count():
    for text in ("That unlocked it.", "We're leveraging it.", "Delving in."):
        assert vp.voice_problems(text), text


def test_strip_banned_cuts_only_what_breaks_the_voice():
    text = ("I hope this email finds you well. Sure, Thursday works.\n"
            "See you then.\n\nBest regards,\nThe Team")
    out = vp.strip_banned(text)
    assert out == "Sure, Thursday works.\nSee you then."
    assert vp.voice_problems(out) == []


def test_linkedin_refuses_a_banned_phrase_with_the_sign_off_intact():
    body = "Thanks for asking. Guardian can unlock your network." + lr.signoff("guardian")
    assert "banned phrase" in (lr.content_refusal(body, "guardian") or "")
    clean = "Thanks for asking. the owner will answer here." + lr.signoff("guardian")
    assert lr.content_refusal(clean, "guardian") is None


async def test_reask_sends_the_draft_back_once_with_the_material_unchanged():
    from atlas.kernel.llm import Message
    seen: list[tuple[str, list[Any]]] = []

    async def think(need, system, msgs, **kw):
        seen.append((system, list(msgs)))
        return SimpleNamespace(text=" Sure, Thursday works. ")
    material = [Message("user", "<untrusted_data>hi</untrusted_data>")]
    out = await vp.reask(think, "need", "SYS", material, "Let's delve in.",
                         ["it uses a banned phrase ('delve')"], max_tokens=50)
    assert out == "Sure, Thursday works."
    [(system, msgs)] = seen
    assert system == "SYS"
    assert msgs[0] is material[0]
    assert msgs[1].role == "assistant" and msgs[1].content == "Let's delve in."
    assert msgs[2].role == "user" and "delve" in msgs[2].content


async def test_reask_without_a_model_returns_nothing():
    async def think(*a, **k):
        raise RuntimeError("down")
    assert await vp.reask(think, "n", "S", [], "x", ["p"], max_tokens=5) == ""


async def test_a_personal_draft_still_off_voice_after_one_rewrite_is_stripped(
        monkeypatch):
    stored: dict[int, str] = {}
    calls: list[int] = []

    async def no_sync():
        return None

    async def fetch_all(sql, *args):
        return [{"id": 7, "platform": "instagram", "sender": "sam",
                 "text": "are we still on for thursday?"}]

    async def execute_sql(sql, row_id, body):
        stored[row_id] = body

    async def think(need, system, msgs, **kw):
        calls.append(1)
        assert vp.EMAIL_STYLE in system
        return SimpleNamespace(text="I hope this email finds you well. Yes, still on!")
    monkeypatch.setattr(messages, "_sync", no_sync)
    monkeypatch.setattr(messages, "fetch_all", fetch_all)
    monkeypatch.setattr(messages, "execute_sql", execute_sql)
    monkeypatch.setattr(messages, "think", think)
    result = await messages.draft_replies()
    assert result.ok, result.detail
    assert len(calls) == 2                                  # one rewrite asked for
    assert stored == {7: "Yes, still on!"}
