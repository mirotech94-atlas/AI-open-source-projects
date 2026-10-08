"""Typing "do it" in the decisions channel.

The same three proofs as a button — signed by Slack, inside the replay
window, from an approver — so the same authority. What must never happen is
channel chatter, or ATLAS's own cards, being read as commands.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth

SECRET = "s3cret"


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("x" * 14))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UMIRO")

    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        yield c


def post(client, payload: dict, *, secret=SECRET, retry: str = ""):
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), b"v0:" + ts.encode() + b":" + raw,
                           hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json",
               "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}
    if retry:
        headers["X-Slack-Retry-Num"] = retry
    return client.post("/webhooks/slack/events", content=raw, headers=headers)


def message(text: str, user: str = "UMIRO", **extra) -> dict:
    return {"type": "event_callback",
            "event": {"type": "message", "text": text, "user": user,
                      "channel": "C0C3RK8RS2E", "ts": "1.2", **extra}}


@pytest.fixture
def decisions(monkeypatch):
    """Capture decide() and the reply, and make the background task run."""
    decided: list = []
    replied: list = []

    class V:
        def __init__(self, pid):
            self.pid = pid

        def render(self):
            return f"#{self.pid} approved — running."

        def receipt(self):
            return f"Proposal #{self.pid} approved and started."

    async def fake_decide(pid, verdict, via):
        decided.append((pid, verdict, via))
        return V(pid)

    async def fake_notify(msg):
        replied.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.approvals.decide", fake_decide)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    return decided, replied


def settle():
    """Let the create_task'd decision run inside the TestClient's loop."""
    import anyio
    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(asyncio.sleep, 0.05)


# ------------------------------------------------------------------ gates

def test_the_challenge_is_answered_only_when_signed(client):
    r = post(client, {"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json()["challenge"] == "abc123"
    r = post(client, {"type": "url_verification", "challenge": "abc123"},
             secret="wrong")
    assert r.status_code == 401


def test_an_unsigned_event_is_refused(client):
    assert post(client, message("do it"), secret="wrong").status_code == 401


def test_a_non_approver_typing_do_it_does_nothing(client, decisions,
                                                   memory_backend):
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message("do it", user="USTRANGER"))
    settle()
    assert decided == [] and replied == []


def test_atlas_own_cards_are_never_read_as_commands(client, decisions,
                                                    memory_backend):
    """The card says "Approve". The bot must not approve its own card."""
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message("Approve it now", bot_id="B123"))
    settle()
    assert decided == []


def test_chatter_is_ignored(client, decisions, memory_backend):
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    from atlas.api import slack_events
    talked: list[str] = []

    async def fake_converse(text):
        talked.append(text)
        return "FlyCheap is green."

    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(slack_events, "_converse", fake_converse)
    try:
        post(client, message("morning, how's flycheap looking"))
        settle()
    finally:
        mp.undo()
    # Never a verdict on a card; since 2026-09-27 it is answered as talk.
    assert decided == []
    assert talked == ["morning, how's flycheap looking"]


def test_a_retry_of_an_event_already_taken_is_dropped(client, decisions,
                                                      memory_backend):
    """The first delivery was taken (slow, not lost); Slack retried."""
    from atlas.api import slack_events
    slack_events._SEEN.clear()
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    event = message("approve 82") | {"event_id": "EvR1"}
    post(client, event)
    r = post(client, event, retry="1")
    settle()
    assert r.json().get("ignored") == "duplicate"
    assert decided == [(82, "approve", "slack:UMIRO")]


def test_a_retry_whose_first_delivery_never_arrived_is_taken(client, decisions,
                                                             memory_backend):
    """RA-B5: dropping every retry lost the events a restart ate."""
    from atlas.api import slack_events
    slack_events._SEEN.clear()
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    r = post(client, message("approve 82") | {"event_id": "EvR2"}, retry="2")
    assert r.status_code == 200
    settle()
    assert decided == [(82, "approve", "slack:UMIRO")]


# --------------------------------------------------------------- approving

def test_do_it_approves_the_only_pending_proposal(client, decisions,
                                                   memory_backend):
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    r = post(client, message("do it"))
    assert r.status_code == 200
    settle()
    assert decided == [(82, "approve", "slack:UMIRO")]
    assert replied and replied[0].thread == "1.2"
    assert "#82" in replied[0].body


@pytest.mark.parametrize("text", ["approve 82", "do it #82", "82 go ahead",
                                  "fix this one, proposal 82"])
def test_a_named_proposal_wins_over_the_latest(client, decisions,
                                                memory_backend, text):
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 99, "action": "vpsie.snapshot_all"}]
    post(client, message(text))
    settle()
    assert decided and decided[0][0] == 82


def test_two_pending_and_none_named_asks_rather_than_guesses(
        client, decisions, memory_backend):
    """Guessing which of two cards he meant is how the wrong thing runs."""
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 99, "action": "vpsie.snapshot_all"},
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message("go ahead"))
    settle()
    assert decided == []
    assert replied and "Which one?" in replied[0].body
    assert "#99" in replied[0].body and "#82" in replied[0].body


def test_nothing_pending_says_so(client, decisions, memory_backend):
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = []
    post(client, message("do it"))
    settle()
    assert decided == []
    assert replied and "Nothing's pending" in replied[0].body


def test_no_typed_declines_the_latest(client, decisions, memory_backend):
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message("leave it"))
    settle()
    assert decided == [(82, "decline", "slack:UMIRO")]


# ---------------------------------------------------------------- requests

@pytest.fixture
def requests_made(monkeypatch):
    """What intent.act was asked, and what went back to the thread."""
    from atlas.api import slack_events
    from atlas.kernel import intent

    asked: list[tuple[str, str, str]] = []
    replied: list[str] = []

    async def fake_act(text, *, channel, who):
        asked.append((text, channel, who))
        return intent.Outcome(True, "Doing it now — blocked 30 min.",
                              action="calendar.create_block", ran=True)

    async def fake_notify(msg):
        replied.append(msg.body)
        return ["slack"]

    monkeypatch.setattr(intent, "act", fake_act)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    monkeypatch.setattr(slack_events, "notify", fake_notify, raising=False)
    return asked, replied


def test_a_typed_request_in_a_dm_is_done(client, requests_made):
    """He tried to ask in Slack and got nothing back. A DM from an approver
    is a request: it runs through intent.act as 'slack', and he gets a line."""
    asked, replied = requests_made
    post(client, message("block 30 minutes today at 12:00 to approve the "
                         "flycheap linkedin drafts", channel_type="im"))
    settle()
    assert asked == [("block 30 minutes today at 12:00 to approve the flycheap "
                      "linkedin drafts", "slack", "UMIRO")]
    assert replied and "Doing it now" in replied[0]


def test_a_request_naming_atlas_in_any_channel_is_done(client, requests_made):
    asked, _ = requests_made
    post(client, message("atlas, snapshot everything", channel_type="channel"))
    settle()
    assert len(asked) == 1


def test_any_channel_is_a_conversation_with_atlas(client, requests_made):
    """Miro, 2026-09-27: every channel in ATLAS's workspace should talk
    back. An approver's message in any channel ATLAS is in is for ATLAS."""
    asked, _ = requests_made
    post(client, message("block 30 minutes at 12:00 for the flycheap drafts",
                         channel_type="channel", channel="CRANDOM"))
    settle()
    assert len(asked) == 1


def test_a_message_tagging_someone_else_is_left_to_them(client, requests_made):
    asked, replied = requests_made
    post(client, message("<@U0SOMEONE> can you look at the flycheap deploy",
                         channel_type="channel", channel="CRANDOM"))
    settle()
    assert asked == [] and replied == []


def test_conversation_is_answered_not_refused(client, requests_made, monkeypatch):
    """"how's it going" used to get "I don't have an action that does that"."""
    from atlas.api import slack_events
    asked, replied = requests_made
    said: list[str] = []

    async def fake_converse(text):
        said.append(text)
        return "All quiet — FlyCheap and Vault are green as of 17:40."

    monkeypatch.setattr(slack_events, "_converse", fake_converse)
    post(client, message("hey atlas how's it going", channel_type="channel",
                         channel="CRANDOM"))
    settle()
    assert asked == []                                  # not a command
    assert said == ["hey how's it going"]
    assert replied and replied[0].startswith("All quiet")


def test_an_action_nothing_can_do_goes_to_the_solver(client, monkeypatch):
    """2026-09-28: "can you optimize my cpu for attendace bot server" got a
    chat reply asking for his own `top` output. A catalog miss now goes to
    kernel/solve.py: the specialists measure, a Tier 2 card hands the work
    to the coding agent, and "file it" stays on offer."""
    from atlas.kernel import intent, solve
    replied: list[str] = []
    solved: list[tuple] = []

    async def no_action(text, *, channel, who):
        return intent.Outcome(False, intent.NO_ACTION)

    async def fake_solve(text, *, who, channel, thread):
        solved.append((text, who, channel, thread))
        return "CPU isn't reported by Roots. Written up for the coding agent — card #7."

    async def fake_notify(msg):
        replied.append(msg.body)

    monkeypatch.setattr(intent, "act", no_action)
    monkeypatch.setattr(solve, "solve", fake_solve)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    post(client, message("atlas can you optimize my cpu for attendace bot server",
                         channel_type="im"))
    settle()
    assert solved and solved[0][0] == "can you optimize my cpu for attendace bot server"
    assert solved[0][1] == "UMIRO"
    assert replied and replied[0].startswith("CPU isn't reported by Roots")
    assert "file it" in replied[0]


def test_a_non_approver_request_is_ignored(client, requests_made):
    asked, _ = requests_made
    post(client, message("atlas delete everything", user="USTRANGER",
                         channel_type="im"))
    settle()
    assert asked == []


def test_a_knowledge_question_in_slack_is_answered_from_knowledge_not_no_action(
        client, requests_made, monkeypatch):
    """Miro, 2026-09-25: Perplexity as the model behind Slack answers, so a
    factual question gets an accurate, sourced answer -- the same knowledge
    route the voice uses -- instead of "I don't have an action that does
    that"."""
    from atlas.kernel import knowledge
    asked, replied = requests_made
    questions: list[str] = []

    async def fake_answer(question):
        questions.append(question)
        return ("A virus needs a host cell; a bacterium is a living cell.\n\n"
                "Sources (read 16:10 UTC):\n- https://example.org/virus")

    monkeypatch.setattr(knowledge, "answer", fake_answer)
    post(client, message("atlas what is the difference between a virus and a "
                         "bacterium", channel_type="im"))
    settle()
    assert asked == []                                   # never intent.act
    assert questions == ["what is the difference between a virus and a bacterium"]
    assert replied and replied[0].startswith("A virus needs a host cell")
    assert "Sources" in replied[0]


def test_a_knowledge_question_in_slack_keeps_the_answer_in_the_library(
        client, requests_made, monkeypatch):
    """Driven end to end through the real Slack webhook and the real
    `knowledge.answer()` -- only `think` and Perplexity are faked -- so this
    proves the Slack caller reaches the library write too, not just voice's."""
    from atlas.kernel import knowledge
    from atlas.modules.vault import library

    kept: list[tuple] = []

    class FakeCompletion:
        text = "A virus needs a host cell; a bacterium is a living cell."

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return FakeCompletion()

    async def fake_keep_answer(question, answer, source):
        kept.append((question, answer, source))

    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setattr(knowledge, "think", fake_think)
    monkeypatch.setattr(library, "keep_answer", fake_keep_answer)

    post(client, message("atlas what is the difference between a virus and a "
                         "bacterium", channel_type="im"))
    settle()

    assert kept == [("what is the difference between a virus and a bacterium",
                     FakeCompletion.text, "model")]


def test_a_request_that_reads_like_a_question_still_runs_as_a_request(
        client, requests_made, monkeypatch):
    """"How to" plus a verb that asks for work is a request, and a question
    naming one of his own modules is for a specialist, never trivia."""
    from atlas.kernel import knowledge
    asked, _ = requests_made

    async def never(question):
        raise AssertionError("knowledge must not answer a request")

    monkeypatch.setattr(knowledge, "answer", never)
    post(client, message("atlas explain what is broken in flycheap", channel_type="im"))
    settle()
    assert len(asked) == 1


def test_the_reply_outbound_carries_the_origin_channel(client, monkeypatch):
    """Asked in #vpsie, answered in #vpsie, under the question (2026-09-27:
    the answer went to #atlas-decisions instead)."""
    import asyncio as _asyncio

    from atlas.api import slack_events
    from atlas.kernel import intent

    got: list = []

    async def fake_act(text, *, channel, who):
        return intent.Outcome(True, "9 servers running (read 23:06).",
                              action="x", ran=False)

    async def fake_notify(msg):
        got.append(msg)
        return ["slack"]

    monkeypatch.setattr(intent, "act", fake_act)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    monkeypatch.setattr(slack_events, "_is_conversation", lambda t: False)
    monkeypatch.setattr(slack_events, "_is_knowledge_question", lambda t: False)
    _asyncio.run(slack_events._request("how many servers", "UMIRO", "CVPSIE", "7.7"))
    assert got and got[0].channel == "CVPSIE" and got[0].thread == "7.7"


# ------------------------------------------------ typed decisions, end to end
# The parser's own cases are in test_slack_decisions.py.

def _pending(memory_backend, *ids):
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": i, "action": "gitlab.open_issue"} for i in ids]


def _no_detail(monkeypatch, shown):
    async def detail(pid):
        shown.append(pid)
        return f"#{pid} detail"
    monkeypatch.setattr("atlas.api.main._proposal_detail", detail)


@pytest.mark.parametrize("text", ["what happens if I approve #82?",
                                  "should I approve proposal 82?"])
def test_a_question_about_an_approval_explains_and_never_decides(
        client, decisions, memory_backend, monkeypatch, text):
    decided, replied = decisions
    _pending(memory_backend, 82)
    shown: list = []
    _no_detail(monkeypatch, shown)
    post(client, message(text))
    settle()
    assert decided == [] and shown == [82]
    assert "Nothing was executed" in replied[0].body


@pytest.mark.parametrize("text", ["don't approve 82", "do not approve 82"])
def test_a_negated_approval_never_approves(client, decisions, memory_backend, text):
    decided, replied = decisions
    _pending(memory_backend, 82)
    post(client, message(text))
    settle()
    assert decided == []
    assert "Not approving #82" in replied[0].body


def test_quoted_approval_is_not_his(client, decisions, memory_backend):
    decided, _ = decisions
    _pending(memory_backend, 82)
    post(client, message("&gt; approve 82"))
    settle()
    assert decided == []


def test_two_ids_ask_rather_than_pick_one(client, decisions, memory_backend):
    decided, replied = decisions
    _pending(memory_backend, 82, 83)
    post(client, message("approve 82 83"))
    settle()
    assert decided == [] and "#82 and #83" in replied[0].body


def test_a_bare_yes_at_top_level_asks_even_with_one_pending(
        client, decisions, memory_backend):
    """A bare yes only answers the card it replies to."""
    decided, replied = decisions
    _pending(memory_backend, 82)
    post(client, message("yes"))
    settle()
    assert decided == [] and "Which proposal?" in replied[0].body


def _parent_is(monkeypatch, msg):
    async def parent(channel, thread_ts):
        return msg
    from atlas.api import slack_events
    monkeypatch.setattr(slack_events, "_parent", parent)


def test_yes_in_the_thread_of_an_atlas_card_approves_that_card(
        client, decisions, memory_backend, monkeypatch):
    decided, replied = decisions
    _pending(memory_backend, 82, 83)
    from tests.test_slack_decisions import _rendered_card
    _parent_is(monkeypatch, _rendered_card(83, "File an issue"))
    post(client, message("yes", ts="1.5", thread_ts="1.0"))
    settle()
    assert decided == [(83, "approve", "slack:UMIRO")]
    assert replied[0].thread == "1.0", "the reply goes under the card, not his reply"


def test_yes_under_a_message_atlas_did_not_post_decides_nothing(
        client, decisions, memory_backend, monkeypatch):
    """_parent returns None for anything not posted by ATLAS."""
    decided, _ = decisions
    _pending(memory_backend, 82, 83)
    _parent_is(monkeypatch, None)
    post(client, message("yes", ts="1.5", thread_ts="1.0"))
    settle()
    assert decided == []


def test_ack_in_an_alert_thread_mutes_that_check_and_approves_nothing(
        client, decisions, memory_backend, monkeypatch):
    decided, replied = decisions
    _parent_is(monkeypatch, {"ts": "1.0", "bot_id": "B1", "text": "vpsie.balance is failing",
                             "blocks": [{"type": "actions", "elements": [
                                 {"type": "button", "value": "ack:vpsie.balance"}]}]})
    acked: list = []

    async def fake_ack(key, via, **kw):
        acked.append((key, via))
        return "failing"

    monkeypatch.setattr("atlas.kernel.acks.ack", fake_ack)
    post(client, message("ack", ts="1.5", thread_ts="1.0"))
    settle()
    assert decided == [] and acked == [("vpsie.balance", "slack:UMIRO")]
    assert "No action executed" in replied[0].body


def test_thanks_is_noted_and_never_reaches_the_model_or_a_decision(
        client, decisions, memory_backend, monkeypatch):
    decided, replied = decisions
    from atlas.api import slack_events

    async def never(text):
        raise AssertionError("thanks must not reach the model")

    monkeypatch.setattr(slack_events, "_converse", never)
    post(client, message("thanks"))
    settle()
    assert decided == [] and replied[0].body == "Noted."


def test_a_strong_verb_still_takes_the_single_pending_proposal(
        client, decisions, memory_backend):
    decided, _ = decisions
    _pending(memory_backend, 82)
    post(client, message("do it"))
    settle()
    assert decided == [(82, "approve", "slack:UMIRO")]


def test_the_same_event_delivered_twice_is_acted_on_once(client, decisions,
                                                         memory_backend):
    """Slack can redeliver one event without a retry header."""
    from atlas.api import slack_events
    slack_events._SEEN.clear()
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    event = message("approve 82") | {"event_id": "Ev0001"}
    post(client, event)
    r = post(client, event)
    settle()
    assert r.json().get("ignored") == "duplicate"
    assert decided == [(82, "approve", "slack:UMIRO")]


def test_an_event_already_in_the_audit_log_is_ignored_after_a_restart(
        client, decisions, memory_backend):
    from atlas.api import slack_events
    slack_events._SEEN.clear()          # a fresh process
    decided, _ = decisions
    memory_backend.responses["SELECT 1 AS x FROM actions_log"] = {"x": 1}
    r = post(client, message("approve 82") | {"event_id": "Ev0002"})
    settle()
    assert r.json().get("ignored") == "duplicate" and decided == []


def test_a_decision_typed_elsewhere_is_noted_under_the_card(client, decisions,
                                                          memory_backend, monkeypatch):
    """Approve #82 from #vpsie: the receipt answers in #vpsie AND lands under
    the card, so the card never looks undecided."""
    from atlas.channels.slack import SlackChannel
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = []
    memory_backend.responses["SELECT action, params FROM proposals"] = {
        "action": "gitlab.open_issue", "params": {}}
    monkeypatch.setattr("atlas.kernel.comms.sent_ref",
                        lambda prefix, ch: "123.4" if prefix == "#82 — " else None)
    monkeypatch.setattr(SlackChannel, "channel_for", lambda self, topic: "CINFRA")
    post(client, message("approve 82", channel="CVPSIE", ts="9.1"))
    settle()
    assert decided == [(82, "approve", "slack:UMIRO")]
    here = [m for m in replied if m.channel == "CVPSIE"]
    card = [m for m in replied if m.thread == "123.4"]
    assert here and here[0].thread == "9.1"
    assert card and "Decided in <#CVPSIE>" in card[0].body


def test_a_decision_in_the_cards_own_channel_is_not_echoed(client, decisions,
                                                          memory_backend, monkeypatch):
    from atlas.channels.slack import SlackChannel
    _decided, replied = decisions
    memory_backend.responses["SELECT action, params FROM proposals"] = {
        "action": "gitlab.open_issue", "params": {}}
    monkeypatch.setattr("atlas.kernel.comms.sent_ref", lambda prefix, ch: "123.4")
    monkeypatch.setattr(SlackChannel, "channel_for", lambda self, topic: "CINFRA")
    post(client, message("approve 82", channel="CINFRA", ts="9.1"))
    settle()
    assert len(replied) == 1 and replied[0].channel == "CINFRA"



def _named_rows(memory_backend, *rows):
    memory_backend.responses["SELECT id, action, params FROM proposals"] = [
        {"id": i, "action": a, "params": {}} for i, a in rows]


def test_a_verb_naming_something_else_never_takes_the_only_pending_card(
        client, decisions, memory_backend, monkeypatch):
    """'approve the linkedin drafts' with only GitLab cards waiting: say so,
    from what is actually pending, and touch no card. (2026-09-28 the
    model answered instead, and a rate-limited fallback cut it off.)"""
    from atlas.api import slack_events
    decided, replied = decisions
    _pending(memory_backend, 82)
    _named_rows(memory_backend, (82, "gitlab.open_issue"))
    monkeypatch.setattr(slack_events, "_linkedin_bank_line",
                        lambda: "LinkedIn post bank — FlyCheap: 5 approved and queued (B5).")

    async def never(*a, **k):
        raise AssertionError("a named approval is answered from data, not the model")

    monkeypatch.setattr(slack_events, "_converse", never)
    post(client, message("approve the linkedin drafts"))
    settle()
    body = replied[0].body
    assert decided == []
    assert 'Nothing matching "linkedin draft" is waiting' in body
    assert "FlyCheap: 5 approved and queued (B5)" in body
    assert "What is waiting: 1 card(s) (gitlab)" in body and "Nothing was executed" in body


def test_a_named_approval_lists_the_matching_cards_and_asks(client, decisions,
                                                          memory_backend):
    decided, replied = decisions
    _pending(memory_backend, 90, 82)
    _named_rows(memory_backend, (90, "linkedin.approve_draft"), (82, "gitlab.open_issue"))
    post(client, message("approve the linkedin drafts"))
    settle()
    body = replied[0].body
    assert decided == []
    assert "#90 `linkedin.approve_draft`" in body and "#82" not in body
    assert "Say `approve 90`" in body


def test_a_verb_naming_nothing_atlas_knows_goes_to_the_request_path(
        client, memory_backend, monkeypatch):
    from atlas.kernel import intent
    asked: list = []

    async def fake_act(text, *, channel, who):
        asked.append(text)
        return intent.Outcome(True, "ok", action="x", ran=False)

    async def fake_notify(msg):
        return ["slack"]

    monkeypatch.setattr(intent, "act", fake_act)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    _pending(memory_backend, 82)
    post(client, message("approve the tomatoes"))
    settle()
    assert asked == ["approve the tomatoes"]


@pytest.mark.parametrize("text, cut", [
    ("FlyCheap has 5 approved posts queued (`B5`, `B6`, `B8`, ", True),
    ("Queued: B5, B6,", True), ("See (the list", True),
    ("You have 9 instances running (read 00:50).", False),
    ("Done — `B5` is live.", False),
])
def test_a_cut_off_answer_is_recognised(text, cut):
    from atlas.api.slack_events import _looks_cut
    assert _looks_cut(text) is cut


def test_only_quoted_text_is_never_a_request(client, requests_made):
    """Live test 2026-09-28: a pasted '> approve #179' reached the model,
    which answered 'I see you want to approve #179'."""
    asked, replied = requests_made
    post(client, message("&gt; approve #179"))
    settle()
    assert asked == []
    assert replied and "quoted text" in replied[0] and "Nothing was executed" in replied[0]


def test_his_own_words_go_through_without_the_quote(client, requests_made):
    asked, _ = requests_made
    post(client, message("&gt; the deploy failed\natlas, restart the flycheap worker",
                         channel_type="im"))
    settle()
    assert asked and asked[0][0] == "atlas, restart the flycheap worker"


def test_which_one_gives_a_real_pending_id_as_the_example(client, decisions,
                                                         memory_backend):
    _decided, replied = decisions
    _pending(memory_backend, 177, 176)
    post(client, message("yes"))
    settle()
    assert "`approve 177`" in replied[0].body and "180" not in replied[0].body


def test_a_question_with_a_number_is_answered_as_a_question(client, decisions,
                                                            memory_backend, monkeypatch):
    decided, _replied = decisions
    from atlas.api import slack_events
    talked: list = []

    async def converse(text):
        talked.append(text)
        return "node 7 is fine."

    async def never(*a, **k):
        raise AssertionError("not a question about a proposal")

    monkeypatch.setattr(slack_events, "_converse", converse)
    monkeypatch.setattr(slack_events, "_explain", never)
    post(client, message("what happened with node 7?"))
    settle()
    assert decided == [] and talked == ["what happened with node 7?"]


async def test_a_thread_parent_counts_only_when_atlas_posted_it(monkeypatch):
    from atlas.api import slack_events

    async def me():
        return ("UATLAS", "BATLAS")

    def history(msg):
        async def get_json(url, **kw):
            return {"ok": True, "messages": [msg]}, None
        return get_json

    monkeypatch.setattr(slack_events, "_me", me)
    monkeypatch.setattr("atlas.modules._http.get_json",
                        history({"ts": "1.0", "bot_id": "BATLAS", "text": "x"}))
    assert await slack_events._parent("C1", "1.0") is not None
    monkeypatch.setattr("atlas.modules._http.get_json",
                        history({"ts": "1.0", "bot_id": "BOTHER", "text": "x"}))
    assert await slack_events._parent("C1", "1.0") is None
    monkeypatch.setattr("atlas.modules._http.get_json",
                        history({"ts": "1.0", "user": "UMIRO", "text": "#1 — x"}))
    assert await slack_events._parent("C1", "1.0") is None


def test_atlas_own_posts_never_reach_the_duplicate_lookup(client, monkeypatch):
    from atlas.api import slack_events
    looked: list = []

    async def seen(event_id):
        looked.append(event_id)
        return False

    monkeypatch.setattr(slack_events, "_seen_before", seen)
    post(client, message("[Reels] REVIEW: #180", bot_id="BATLAS") | {"event_id": "Ev9"})
    post(client, {"type": "event_callback", "event_id": "Ev10",
                  "event": {"type": "message", "subtype": "channel_join",
                            "user": "UMIRO", "channel": "C1", "ts": "1.0"}})
    assert looked == []


def _root_is(monkeypatch, msg):
    from atlas.api import slack_events

    async def root(channel, thread_ts):
        return msg

    monkeypatch.setattr(slack_events, "_thread_root", root)


def test_file_it_opens_an_issue_about_the_request_that_started_the_thread(
        client, requests_made, monkeypatch):
    """2026-09-28: 'file it' answered 'I don't see any file or document'. The
    offer 'say atlas, file it' was never wired to the thread."""
    asked, _replied = requests_made
    _root_is(monkeypatch, {"ts": "5.0", "user": "UMIRO",
                           "text": "can you optimize my cpu for the attendance bot server"})
    post(client, message("file it", ts="5.9", thread_ts="5.0"))
    settle()
    assert len(asked) == 1
    assert asked[0][0].startswith("File a GitLab issue recording this request")
    assert "optimize my cpu for the attendance bot server" in asked[0][0]


def test_file_it_under_a_thread_he_did_not_start_files_nothing(
        client, requests_made, monkeypatch):
    asked, replied = requests_made
    _root_is(monkeypatch, {"ts": "5.0", "bot_id": "BATLAS", "text": "[test] anchor"})
    post(client, message("file it", ts="5.9", thread_ts="5.0"))
    settle()
    assert asked == []
    assert "can't tell what to file" in replied[0] and "Nothing was executed" in replied[0]


async def test_a_cut_off_answer_is_retried_then_labelled(monkeypatch):
    from atlas.api import slack_events

    class C:
        text = "FlyCheap has 5 approved posts queued (`B5`, `B6`, "

    async def think(*a, **k):
        return C()

    async def context():
        return ""

    routed: list = []

    async def route(prompt):
        routed.append(prompt)
        return "Still cut: B5, B6,"

    monkeypatch.setattr("atlas.kernel.llm.think", think)
    monkeypatch.setattr("atlas.api.voice.live_context", context)
    monkeypatch.setattr("atlas.kernel.agents.route", route)
    out = await slack_events._converse("what is queued on linkedin")
    assert routed, "a cut answer goes to the roster instead of being posted"
    assert out.endswith("_(That answer was cut off. Ask again for the rest.)_")


def test_file_it_outside_a_thread_is_never_a_request(client, monkeypatch):
    """2026-09-28: "atlas , file it" at the top of a channel missed the
    matcher, went down the request path, and became card #192."""
    from atlas.api import slack_events
    from atlas.kernel import intent

    assert slack_events._FILE_IT.match("atlas , file it")
    acted, replied = [], []

    async def fake_act(text, *, channel, who):
        acted.append(text)
        return intent.Outcome(False, intent.NO_ACTION)

    async def fake_reply(text, channel, thread):
        replied.append(text)

    monkeypatch.setattr(intent, "act", fake_act)
    monkeypatch.setattr(slack_events, "_reply", fake_reply)
    post(client, message("atlas , file it", channel_type="channel", channel="CRANDOM"))
    settle()
    assert acted == []
    assert replied and "in the thread of the request" in replied[0]


def test_search_in_a_channel_lists_his_records_without_a_model(
        client, requests_made, monkeypatch):
    """Miro, 2026-10-06: his own search engine in Slack."""
    from datetime import UTC, datetime

    from atlas.kernel import search
    asked, replied = requests_made

    async def fake_search(text, limit=8):
        return [search.Hit("card", datetime(2026, 10, 5, 2, 16, tzinfo=UTC),
                           "content.publish_reel (expired)", "business lesson, day 22",
                           "#294")]

    monkeypatch.setattr(search, "search", fake_search)
    post(client, message("atlas search business lesson", channel_type="channel",
                         channel="CRANDOM"))
    settle()
    assert asked == [], "a search never becomes a request"
    assert replied and replied[0].startswith('1 record(s) for "business lesson"')
    assert "card #294" in replied[0]
