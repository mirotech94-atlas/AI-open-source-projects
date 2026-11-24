"""Who may approve, and which tenant a write may reach: P06 (plan p. 14) and
D07 (plan p. 15).

P06: a lower-privilege actor, or the wrong tenant, attempting an approval is
denied without leaking protected details -- on Slack (the approver list).
D07 covered the Guardian tenant boundary; Guardian was removed on 2026-10-29.

All in-suite; docs/acceptance.md is the matrix.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

# Fixtures. pytest registers them by their own names; the aliases keep
# the module names apart from the test parameters that request them.
from tests.acceptance_support import (
    APPROVER,
    client_fixture,  # noqa: F401
    decisions_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
)
from tests.test_slack_events import SECRET, message, post, settle

STRANGER = "USTRANGER"
SECRET_REASON = "rotate the flycheap stripe key sk_live_hidden"


def _signed_form(client, path: str, form: dict):
    body = urllib.parse.urlencode(form)
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:{body}".encode(),
                           hashlib.sha256).hexdigest()
    return client.post(path, content=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})


@pytest.fixture
def guarded(monkeypatch):
    """decide() and the proposal's details, wired to fail the test if a
    stranger's request reaches either; every reply captured."""
    replies: list[tuple[str, bool]] = []

    async def decide(*_a, **_kw):
        raise AssertionError("a non-approver reached decide()")

    async def detail(pid):
        raise AssertionError("a non-approver reached the proposal's details")

    async def reply(_url, text, *, ephemeral=False):
        replies.append((text, ephemeral))

    monkeypatch.setattr("atlas.api.main.decide", decide)
    monkeypatch.setattr("atlas.api.main._proposal_detail", detail)
    monkeypatch.setattr("atlas.api.main._slack_reply", reply)
    return replies


# -------------------------------------------------------------------- P06

@pytest.mark.parametrize("value", ["approve:82", "always:82", "decline:82",
                                   "detail:82", "ack:cloudco.balance"])
def test_P06_a_non_approvers_button_is_denied_without_the_cards_details(
        client, guarded, value):
    r = _signed_form(client, "/webhooks/slack", {"payload": json.dumps({
        "user": {"id": STRANGER}, "response_url": "https://hooks.slack/x",
        "actions": [{"value": value}]})})
    assert r.status_code == 200
    [(text, ephemeral)] = guarded
    assert text == "You are not an approver for ATLAS. Nothing was done."
    assert ephemeral is True             # only the clicker sees the refusal
    assert "82" not in text and "cloudco" not in text


def test_P06_a_non_approvers_slash_command_is_denied_and_decides_nothing(
        client, guarded):
    r = _signed_form(client, "/webhooks/slack/command", {
        "user_id": STRANGER, "text": "approve 82",
        "response_url": "https://hooks.slack/x"})
    assert r.json() == {"response_type": "ephemeral",
                        "text": "You're not an approver for ATLAS. Nothing was done."}
    assert guarded == []


@pytest.mark.parametrize("text", ["approve 82", "yes", "do it", "decline 82",
                                  "what is #82 about?"])
def test_P06_a_non_approvers_typed_words_are_ignored_silently(
        client, decisions, memory_backend, text):
    decided, replied = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message(text, user=STRANGER))
    settle()
    # No decision, and no reply at all: an answer would confirm #82 exists.
    assert decided == [] and replied == []


def test_P06_the_same_words_from_the_approver_do_decide(client, decisions,
                                                        memory_backend):
    """The positive control for the test above: the gate is the sender."""
    decided, _ = decisions
    memory_backend.responses["SELECT id, action FROM proposals"] = [
        {"id": 82, "action": "gitlab.open_issue"}]
    post(client, message("approve 82", user=APPROVER))
    settle()
    assert decided == [(82, "approve", f"slack:{APPROVER}")]
