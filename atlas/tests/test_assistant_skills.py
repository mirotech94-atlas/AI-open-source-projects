"""The assistant's skill registry: a playbook is a prompt fragment and a
subset of read tools, chosen by trigger, and can never widen what the loop
offers or add a write."""
from __future__ import annotations

import pytest

from atlas.kernel.tools import TOOLS
from atlas.modules.assistant import skills
from atlas.modules.assistant.tools import ASSISTANT_TOOLS


@pytest.mark.parametrize("question, expected", [
    ("compare postgres vs mysql for a small app", "compare"),
    ("explain like I'm new to it: what is a hash map", "explain"),
    ("give me a step by step plan to move house", "plan"),
    ("summarise https://example.org/article", "summarise"),
    ("is it true that we only use 10% of our brains?", "fact_check"),
    ("what can you do, atlas?", "atlas"),
    ("how are you doing?", "atlas"),
    ("what is the capital of Peru?", "explain"),
    ("tell me a joke", "answer"),
])
def test_selection_by_trigger_falls_back_to_the_default(question, expected):
    assert skills.select(question).name == expected


def test_a_named_skill_wins_over_the_triggers():
    assert skills.select("compare a and b", name="plan").name == "plan"
    assert skills.select("compare a and b", name="nonsense").name == "compare"


def test_every_skill_uses_only_assistant_read_tools():
    for skill in skills.SKILLS.values():
        assert skill.tools <= ASSISTANT_TOOLS, skill.name
        for name in skill.tools:
            assert TOOLS[name].read is not None and TOOLS[name].action is None


def test_the_summarise_skill_cannot_search_and_the_atlas_skill_cannot_browse():
    assert "search_web" not in skills.SKILLS["summarise"].tools
    assert "read_page" in skills.SKILLS["summarise"].tools
    assert skills.SKILLS["atlas"].tools <= {
        "atlas_status", "atlas_module_status", "atlas_capabilities", "time_now",
        "calculate"}


def test_no_skill_may_add_a_write_tool_or_an_unknown_one():
    with pytest.raises(ValueError, match="does not have"):
        skills.register(skills.Skill("bad", "x", ("zzz",), "body",
                                     frozenset({"gitlab_issue"})))
    with pytest.raises(ValueError, match="does not have"):
        skills.register(skills.Skill("bad", "x", ("zzz",), "body",
                                     frozenset({"instagram_post"})))
    assert "bad" not in skills.SKILLS


def test_render_is_one_paragraph_the_prompt_can_carry():
    text = skills.SKILLS["fact_check"].render()
    assert text.startswith("Playbook (fact_check):") and "\n" not in text


def test_listing_names_each_skill_and_its_tools():
    names = {s["name"] for s in skills.listing()}
    assert {"answer", "compare", "explain", "plan", "summarise", "fact_check",
            "atlas"} <= names
    assert all(isinstance(s["tools"], list) for s in skills.listing())
