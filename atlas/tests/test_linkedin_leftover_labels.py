"""Drafting labels left in a post never publish (2026-10-13: approved polls
A5 "INTRO: Which group needs AI skills first?" and G18 "Block 3 -- KIND: poll")."""
from __future__ import annotations

import pytest

from atlas.modules.linkedin import publish, sweep


@pytest.fixture(autouse=True)
def _guardian_as_fixture_page(monkeypatch):
    """Guardian is the example page here. It is retired by default (the owner,
    2026-10-29); that default is pinned in test_linkedin_retired.py."""
    from atlas.modules import linkedin as _li
    monkeypatch.setitem(_li.PAGES, "guardian", _li.KNOWN_PAGES["guardian"])


def test_a_label_in_the_text_is_found():
    assert publish.leftover_label("INTRO: Which group needs AI skills first?")
    assert publish.leftover_label("Block 3 – KIND: poll")
    assert publish.leftover_label("**Question:** who?")


def test_ordinary_text_is_clean():
    assert publish.leftover_label("Which group needs AI skills first?") == ""
    assert publish.leftover_label("Your top priority for a secure network?") == ""
    assert publish.leftover_label("Time: 3 minutes. Read on") == ""


def test_the_gate_refuses_an_approved_entry_with_a_label(monkeypatch):
    monkeypatch.setattr(publish, "today_name", lambda: "mon")
    entry = {"id": "A5", "status": "approved", "kind": "poll",
             "text": "INTRO: Which group needs AI skills first?",
             "poll": {"intro": "INTRO: Which group needs AI skills first?",
                      "question": "Who should learn AI today?",
                      "options": ["Kids", "Adults"]}}
    state = {}
    monkeypatch.setattr(publish, "bank", lambda _s, _k: ([entry], {}, {}))
    key = next(iter(publish.PAGES))
    got, why = publish.gate(state, key)
    assert got is None and "drafting label" in why


def test_a_markdown_poll_block_drafts_without_labels():
    block = ("Block 3 – KIND: poll\n**INTRO:** Which group needs AI skills first?\n"
             "Who should learn AI today?\nOptions: Kids | Adults | Seniors")
    f = sweep._loose_fields(block)
    assert f["KIND"] == "poll"
    assert f["INTRO"] == "Which group needs AI skills first?"
    assert "Block" not in f["INTRO"]
