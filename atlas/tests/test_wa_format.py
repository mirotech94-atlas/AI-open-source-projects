"""The one place a WhatsApp message gets its category tag (wa_format)."""
from __future__ import annotations

import pytest

from atlas.kernel import wa_format as wf
from atlas.kernel.wa_format import Category


def test_every_category_has_a_distinct_emoji_tag():
    tags = [wf.TAGS[c] for c in Category]
    assert len(set(tags)) == len(tags)
    for tag in tags:
        assert not tag[0].isascii(), tag             # starts with an emoji
        assert len(tag) < 30


def test_the_named_categories_exist_with_the_agreed_labels():
    assert wf.TAGS[Category.HOME_CAMERAS] == "\U0001F3E0 Home cameras"
    assert wf.TAGS[Category.MMT] == "\U0001F4DA MMT"
    assert wf.TAGS[Category.AGENCY] == "\U0001F3E2 Agency"
    assert wf.TAGS[Category.ACADEMY] == "\U0001F393 Academy"
    assert wf.TAGS[Category.CHURCH] == "⛪ ECC Church"
    assert wf.TAGS[Category.MAIL] == "\U0001F4E7 Mail"
    assert wf.TAGS[Category.MONEY] == "\U0001F4B3 Money/Stripe"
    assert wf.TAGS[Category.SYSTEM] == "\U0001F6E0️ System health"
    assert wf.TAGS[Category.APPROVALS] == "✅ Approvals"
    assert wf.TAGS[Category.CONTENT] == "\U0001F4FA YouTube/content"
    assert wf.TAGS[Category.GENERAL] == "\U0001F4AC General"


def test_tag_puts_the_category_first():
    out = wf.tag(Category.HOME_CAMERAS, "hello")
    assert out == "\U0001F3E0 Home cameras · hello"


def test_ensure_tags_untagged_text_and_defaults_to_general():
    assert wf.ensure("hi").startswith(wf.TAGS[Category.GENERAL] + " · ")
    assert wf.ensure("hi", Category.MAIL).startswith(wf.TAGS[Category.MAIL])


def test_ensure_never_double_tags_and_never_retags():
    once = wf.tag(Category.MMT, "x")
    assert wf.ensure(once, Category.AGENCY) == once
    assert wf.ensure(wf.ensure("x", Category.MAIL), Category.MONEY).count("·") == 1


def test_ensure_handles_none_and_blank():
    assert wf.ensure(None).startswith(wf.TAGS[Category.GENERAL])
    assert wf.ensure("   ").startswith(wf.TAGS[Category.GENERAL])


def test_a_bad_category_value_falls_back_to_general():
    assert wf.ensure("x", "nonsense").startswith(wf.TAGS[Category.GENERAL])   # type: ignore[arg-type]
    assert wf.ensure("x", None).startswith(wf.TAGS[Category.GENERAL])         # type: ignore[arg-type]


def test_the_tagged_message_stays_under_the_whatsapp_limit():
    out = wf.ensure("a" * 5000, Category.AGENCY)
    assert len(out) <= wf.MAX_CHARS
    assert out.startswith(wf.TAGS[Category.AGENCY])
    assert out.endswith(wf.CUT_NOTE)


def test_a_short_message_is_not_changed_beyond_the_tag():
    assert wf.ensure("short", Category.MAIL).endswith(" · short")


@pytest.mark.parametrize("agent,want", [
    ("mmt", Category.MMT), ("qa", Category.SYSTEM), ("content", Category.CONTENT),
    ("seo", Category.CONTENT), ("sales", Category.AGENCY), ("bizops", Category.AGENCY),
    ("sites", Category.AGENCY), ("research", Category.GENERAL),
    ("nobody", Category.GENERAL), (None, Category.GENERAL)])
def test_an_agent_maps_to_its_category_in_code(agent, want):
    assert wf.category_for_agent(agent) is want


@pytest.mark.parametrize("module,want", [
    ("cameras", Category.HOME_CAMERAS), ("mmt", Category.MMT),
    ("church", Category.CHURCH), ("academy", Category.ACADEMY),
    ("mail", Category.MAIL), ("stripe_billing", Category.MONEY),
    ("youtube", Category.CONTENT), ("whatever", Category.SYSTEM)])
def test_a_module_maps_to_its_category_in_code(module, want):
    assert wf.category_for_module(module) is want


@pytest.mark.parametrize("text,want", [
    ("did the front camera see anyone", Category.HOME_CAMERAS),
    ("any new email today", Category.MAIL),
    ("how much did stripe pay out", Category.MONEY),
    ("how's the church channel", Category.CHURCH),
    ("how are academy sales", Category.ACADEMY),
    ("anything on mmt", Category.MMT),
    ("how did my youtube video do", Category.CONTENT),
    ("what is the capital of France", Category.GENERAL)])
def test_a_question_topic_is_picked_by_code_never_by_a_model(text, want):
    assert wf.category_for_text(text) is want


def test_the_reply_category_is_per_task_and_defaults_to_general():
    wf.reset_reply_category()
    assert wf.reply_category() is Category.GENERAL
    wf.set_reply_category(Category.MMT)
    assert wf.reply_category() is Category.MMT
    wf.reset_reply_category()
    assert wf.reply_category() is Category.GENERAL
