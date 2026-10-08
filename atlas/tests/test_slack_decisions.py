"""Typed Slack decisions: what approves, what never does.

The 2026-09-27 review found "what happens if I approve?" and "don't approve
180" both approving the proposal they named, bare numbers read as ids, and
quoted text counting. These pin the fixed behaviour, first on the parser and
then through the real /webhooks/slack/events endpoint.
"""
from __future__ import annotations

import pytest

from atlas.api.slack_decisions import card_id, classify, own_words, proposal_ids

# ------------------------------------------------------------------ parser


@pytest.mark.parametrize("text", [
    "what happens if I approve?", "what happens if I approve 180?",
    "should I approve 180?", "can you approve it", "why approve this one?",
])
def test_a_question_is_never_a_decision(text):
    assert classify(text).kind == "question"


@pytest.mark.parametrize("text", [
    "don't approve 180", "do not approve 180", "never approve that",
    "dont approve it", "not approve 180 yet",
])
def test_a_negated_approval_is_a_hold(text):
    assert classify(text).kind == "hold"


@pytest.mark.parametrize("text", ["ack", "acknowledged", "seen", "thanks",
                                  "thank you!", "got it", "noted."])
def test_acknowledgements_are_never_approvals(text):
    assert classify(text).kind == "ack"


@pytest.mark.parametrize("text", [
    "&gt; approve 180", "> approve 180", "```approve 180```", "`approve 180`",
    "he wrote:\n&gt; do it 180",
])
def test_quoted_or_code_text_decides_nothing(text):
    d = classify(text)
    assert d is None or d.kind not in ("approve", "decline")


@pytest.mark.parametrize("text, strong", [
    ("approve 180", True), ("do it", True), ("go ahead #180", True),
    ("yes", False), ("ok", False), ("yes please", False), ("yes 180", False),
])
def test_approvals_are_explicit_and_carry_their_strength(text, strong):
    d = classify(text)
    assert d.kind == "approve" and d.strong is strong


@pytest.mark.parametrize("text", ["no worries", "ok cool", "yes, but first check it",
                                  "nope, not that one either way"])
def test_weak_words_decide_only_as_the_whole_message(text):
    d = classify(text)
    assert d is None or d.kind not in ("approve", "decline")


@pytest.mark.parametrize("text", ["do it in 5 minutes", "approve it later",
                                  "go ahead tomorrow", "run it at 5pm"])
def test_a_deferred_ask_is_not_an_approval_now(text):
    assert classify(text) is None


def test_decline_forms():
    assert classify("decline 180").kind == "decline"
    assert classify("leave it 89").kind == "decline"
    assert classify("no").kind == "decline" and classify("no").strong is False


@pytest.mark.parametrize("text, ids", [
    ("approve #180", (180,)), ("approve proposal 180", (180,)),
    ("180 go ahead", (180,)), ("approve 180 181", (180, 181)),
    ("approve 5%", ()), ("run it on 3 hours of logs", ()),
    ("approve #180, not 5 minutes", (180,)),
])
def test_ids_are_read_as_ids_and_quantities_are_not(text, ids):
    assert proposal_ids(own_words(text)) == ids


def _rendered_card(pid: int, title: str = "Post a rendered reel") -> dict:
    """A card exactly as SlackChannel renders it -- header, text and buttons
    -- not a hand-typed message in an old format."""
    from atlas.channels.slack import SlackChannel
    from atlas.kernel.comms import Button, Outbound
    msg = Outbound(title=f"#{pid} — {title}", body="b", topic="instagram_stories",
                   presentation="REVIEW",
                   buttons=(Button("Approve", f"approve:{pid}"),
                            Button("Decline", f"decline:{pid}"),
                            Button("Details", f"detail:{pid}")))
    ch = SlackChannel()
    return {"ts": "1.0", "bot_id": "BATLAS", "text": ch.headline(msg),
            "blocks": ch._blocks(msg)}


def test_a_card_is_recognised_by_its_own_buttons_not_its_header_wording():
    card = _rendered_card(180)
    assert card["text"].startswith("[Reels] REVIEW: #180")
    assert card_id(card) == 180
    assert card_id({"text": "#180 — typed by a person"}) is None
    assert card_id({}) is None


@pytest.mark.parametrize("text, bare", [
    ("do it", True), ("approve please", True), ("go ahead", True),
    ("approve 180", True), ("yes do it now", True),
    ("approve the linkedin drafts", False), ("confirm the booking", False),
    ("post it to guardian", False),
])
def test_only_a_bare_verb_may_take_the_single_pending_card(text, bare):
    d = classify(text)
    assert d.kind == "approve" and d.bare is bare


def test_a_number_in_a_question_is_not_a_proposal_unless_said_as_one():
    assert classify("what happened with node 7?").ids == ()
    assert classify("what does #7 do?").ids == (7,)
    assert classify("what happens if I approve proposal 7?").ids == (7,)


def test_mentions_and_quotes_are_not_his_words():
    assert own_words("<@U123> approve <!channel>\n&gt; decline 9") == "approve"
