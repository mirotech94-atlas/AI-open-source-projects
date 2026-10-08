"""The channel implementations themselves.

These are the last hop before a message leaves the building, and every bug
found here was invisible from the inside: `notify()` catches a channel
exception per channel and logs a warning, so a malformed payload looks
exactly like "Miro wasn't notified" with no louder signal than a log line.
"""
from __future__ import annotations

import pytest

from atlas.channels import matrix as matrix_channel
from atlas.channels import slack as slack_channel
from atlas.channels import twilio as twilio_channel
from atlas.kernel.comms import Button, Outbound
from atlas.kernel.contracts import Severity

# ------------------------------------------------------------ slack buttons

def _elements(msg: Outbound) -> list[dict]:
    blocks = slack_channel.SlackChannel()._blocks(msg)
    actions = [b for b in blocks if b["type"] == "actions"]
    assert actions, "buttons must produce an actions block"
    return actions[0]["elements"]


def test_unstyled_button_omits_the_style_key_entirely():
    """Slack's `style` is an enum of primary|danger with no null member.
    Sending "style": null fails block validation and Slack rejects the whole
    message — so one Details button killed the entire approval request."""
    element, = _elements(Outbound("t", "b", buttons=(Button("Details", "detail:41"),)))
    assert "style" not in element
    assert element["value"] == "detail:41"


def test_approve_and_decline_keep_their_styles():
    approve, decline = _elements(Outbound(
        "t", "b", buttons=(Button("Approve", "approve:41"),
                           Button("Decline", "decline:41"))))
    assert approve["style"] == "primary"
    assert decline["style"] == "danger"


def test_no_block_element_ever_carries_a_null_value():
    """The general form of the bug above: Slack rejects nulls in blocks, and
    the rejection takes the message with it."""
    msg = Outbound("t" * 300, "b" * 4000, Severity.CRITICAL,
                   buttons=(Button("Approve", "approve:1"),
                            Button("Details", "detail:1")))

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert value is not None, f"null at {key!r}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(slack_channel.SlackChannel()._blocks(msg))


def test_thread_ts_is_omitted_rather_than_sent_as_null():
    payload_keys = set()

    class Probe(slack_channel.SlackChannel):
        pass

    # The payload is built inside send(); assert on the shape we control.
    msg = Outbound("t", "b")
    assert msg.thread is None
    # A None thread must not become a JSON null in the request body.
    # (send() guards with `if msg.thread:`.)
    import inspect
    source = inspect.getsource(slack_channel.SlackChannel.send)
    assert "if msg.thread:" in source
    assert '"thread_ts": msg.thread' not in source
    payload_keys.add("ok")
    assert payload_keys


# ------------------------------------------------ credentials read per call

@pytest.mark.parametrize("channel, env", [
    (slack_channel.SlackChannel(), {"SLACK_BOT_TOKEN": "xoxb-x",
                                    "SLACK_CHANNEL": "C1"}),
    (matrix_channel.MatrixChannel(), {"MATRIX_HOMESERVER": "https://example.org",
                                      "MATRIX_TOKEN": "syt_x",
                                      "MATRIX_ALERT_ROOM": "!r:example.org"}),
    (twilio_channel.VoiceChannel(), {"TWILIO_ACCOUNT_SID": "AC1",
                                     "TWILIO_AUTH_TOKEN": "t",
                                     "TWILIO_FROM": "+1", "TWILIO_TO": "+2",
                                     "ATLAS_HOST": "atlas.example.com"}),
])
async def test_credentials_are_read_per_call_not_at_import(channel, env, monkeypatch):
    """`register(SlackChannel())` runs at import. Anything read in __init__
    freezes the environment as it was before .env was loaded, and the channel
    reports unavailable forever with no error anywhere."""
    for key in env:
        monkeypatch.delenv(key, raising=False)
    assert await channel.available() is False
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert await channel.available() is True


# ------------------------------------------------------------- twilio voice

async def test_voice_is_unavailable_without_atlas_host(monkeypatch):
    """send() builds the TwiML callback from ATLAS_HOST. Unset, the URL is
    "https://None/..." and Twilio rings Miro only to fail fetching what to
    say — a CRITICAL page that looks delivered and carries no information."""
    for key, value in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                       ("TWILIO_FROM", "+1"), ("TWILIO_TO", "+2")):
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ATLAS_HOST", raising=False)
    assert await twilio_channel.VoiceChannel().available() is False
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")
    assert await twilio_channel.VoiceChannel().available() is True


# --------------------------------------------- G2: a 30-second, one-decision call
#
# Task 54: a CRITICAL call is a Twilio call whose opening TwiML is the phone
# line's own (/webhooks/twilio/voice): the one decision, spoken by Fish
# Audio or <Say>, then the conversation with its read-back and "do it". No
# agent platform in between.

def _voice_env(monkeypatch):
    for key, value in (("TWILIO_ACCOUNT_SID", "AC1"), ("TWILIO_AUTH_TOKEN", "t"),
                       ("TWILIO_FROM", "+1"), ("TWILIO_TO", "+2"),
                       ("ATLAS_HOST", "atlas.example.com")):
        monkeypatch.setenv(key, value)
    # Daytime, so the greeting is the same whenever the suite runs.
    monkeypatch.setattr("atlas.kernel.briefing.night", lambda now=None: False)


def _presence(home):
    async def read():
        return {"home": home, "peers_seen": [], "read_at": "now", "reason": None}
    return read


def _placing(monkeypatch) -> dict:
    """Capture the Calls.json request `send()` makes."""
    seen: dict = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self): return {"sid": "CA2"}

    class FakeClient:
        def __init__(self, **kw):
            seen["client"] = kw

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kw):
            seen["url"] = url
            seen["data"] = kw["data"]
            return FakeResponse()

    monkeypatch.setattr(twilio_channel.httpx, "AsyncClient", FakeClient)
    return seen


def _query(seen: dict) -> dict:
    from urllib.parse import parse_qs, urlsplit
    parts = urlsplit(seen["data"]["Url"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == \
        "https://atlas.example.com/webhooks/twilio/voice"
    return {k: v[0] for k, v in parse_qs(parts.query).items()}


async def test_a_critical_call_is_placed_on_the_phone_line_itself(monkeypatch):
    _voice_env(monkeypatch)
    monkeypatch.setattr("atlas.kernel.comms.presence", _presence(True))
    seen = _placing(monkeypatch)

    sid = await twilio_channel.VoiceChannel().send(
        Outbound("Server down", "Act now.", Severity.CRITICAL))

    assert sid == "CA2"
    assert seen["url"].endswith("/Accounts/AC1/Calls.json")
    data = seen["data"]
    assert data["From"] == "+1" and data["To"] == "+2"
    # POST, so Twilio's CallSid/Direction/From/To arrive signed in the form.
    assert data["Method"] == "POST"
    assert data["StatusCallback"] == \
        "https://atlas.example.com/webhooks/twilio/voice/status"
    # Answering-machine detection: a machine is told only the title.
    assert data["MachineDetection"] == "DetectMessageEnd"
    # The opening is the three-beat briefing (kernel/briefing.py), composed
    # before the phone rings; with no card there is no question.
    assert _query(seen) == {"say": "Hey Miro, it's ATLAS. Server down. Act now.",
                            "title": "Server down"}


async def test_the_call_opens_with_the_one_decision_it_carries(monkeypatch):
    """The card the page is about is read back on this call; an Acknowledge
    button is Slack's, never the phone's."""
    _voice_env(monkeypatch)
    monkeypatch.setattr("atlas.kernel.comms.presence", _presence(True))
    seen = _placing(monkeypatch)
    await twilio_channel.VoiceChannel().send(Outbound(
        "Restart flycheap?", "The API is down.", Severity.CRITICAL,
        buttons=(Button("Approve", "approve:41"), Button("Decline", "decline:41"),
                 Button("Acknowledge", "ack:flycheap.api"))))
    query = _query(seen)
    assert query["pid"] == "41"
    assert "ack" not in seen["data"]["Url"]
    # Not a card's title, so the question stays general.
    assert query["ask"] == "Should I go ahead, yes or no?"


async def test_one_card_is_asked_as_its_own_action_and_several_are_not(monkeypatch):
    """The third beat names the action only when "yes" can mean one thing."""
    _voice_env(monkeypatch)
    monkeypatch.setattr("atlas.kernel.comms.presence", _presence(True))
    seen = _placing(monkeypatch)
    await twilio_channel.VoiceChannel().send(Outbound(
        "#41 — Restart the FlyCheap worker",
        "The fare poller is stuck.\n`flycheap.restart_worker` · Tier 2 · rollback: start it",
        Severity.CRITICAL, buttons=(Button("Approve", "approve:41"),)))
    query = _query(seen)
    assert query["say"] == "Hey Miro, it's ATLAS. The fare poller is stuck."
    assert query["ask"] == "Want me to restart the FlyCheap worker, yes or no?"
    assert "Tier" not in query["say"] and "#41" not in query["say"]

    seen = _placing(monkeypatch)
    await twilio_channel.VoiceChannel().send(Outbound(
        "#41 — Restart the FlyCheap worker", "Stuck.", Severity.CRITICAL,
        buttons=(Button("Approve", "approve:41"), Button("Approve", "approve:42"))))
    assert _query(seen)["ask"] == "Should I go ahead, yes or no?"


async def test_away_shortens_the_speech_but_keeps_the_decision(monkeypatch):
    """When he is out the same "be quick" intent holds -- but the call still
    opens with the decision, so it's the BODY that gets shortened, not the
    whole assembled "<greeting> <title>. <body>" string (which would spend
    the sentence budget on the fixed preamble and title alone). The away
    flag rides to the opening, which says so and keeps the turns short."""
    _voice_env(monkeypatch)
    monkeypatch.setattr("atlas.kernel.comms.presence", _presence(False))
    seen = _placing(monkeypatch)

    body = "First sentence here. Second sentence here. Third sentence here."
    await twilio_channel.VoiceChannel().send(Outbound("Pager", body,
                                                       Severity.CRITICAL))
    query = _query(seen)
    assert query["say"] == "Hey Miro, it's ATLAS. Pager. First sentence here."
    assert query["away"] == "1"


@pytest.mark.parametrize("home", [True, None])
async def test_home_or_unknown_says_it_all_and_is_not_marked_away(
        monkeypatch, home):
    _voice_env(monkeypatch)
    monkeypatch.setattr("atlas.kernel.comms.presence", _presence(home))
    seen = _placing(monkeypatch)
    body = "First sentence here. Second sentence here. Third sentence here."
    await twilio_channel.VoiceChannel().send(Outbound("Pager", body,
                                                       Severity.CRITICAL))
    query = _query(seen)
    assert query["say"] == f"Hey Miro, it's ATLAS. Pager. {body}"
    assert "away" not in query


async def test_unreadable_presence_is_never_read_as_away(monkeypatch):
    _voice_env(monkeypatch)

    async def broken():
        raise TimeoutError("mesh unreachable")

    monkeypatch.setattr("atlas.kernel.comms.presence", broken)
    seen = _placing(monkeypatch)
    await twilio_channel.VoiceChannel().send(Outbound("Server down", "Act now",
                                                       Severity.CRITICAL))
    assert "away" not in _query(seen)


async def test_a_long_page_is_cut_to_what_one_call_can_say(monkeypatch):
    _voice_env(monkeypatch)
    monkeypatch.setattr("atlas.kernel.comms.presence", _presence(True))
    seen = _placing(monkeypatch)
    await twilio_channel.VoiceChannel().send(Outbound("T" * 3000, "b" * 9000,
                                                       Severity.CRITICAL))
    assert len(_query(seen)["say"]) <= 900


async def test_nothing_about_the_call_reaches_an_agent_platform(monkeypatch):
    """No ElevenLabs agent: one request, to Twilio, and nothing else."""
    import inspect
    _voice_env(monkeypatch)
    monkeypatch.setattr("atlas.kernel.comms.presence", _presence(True))
    seen = _placing(monkeypatch)
    await twilio_channel.VoiceChannel().send(Outbound("x", "y", Severity.CRITICAL))
    assert seen["url"].startswith("https://api.twilio.com/")
    assert "elevenlabs" not in inspect.getsource(twilio_channel).lower()


# ------------------------------------------------------------------- matrix

async def test_matrix_percent_encodes_the_room_id(monkeypatch):
    """Room ids are `!abc:example.org` — reserved characters in a path segment."""
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://example.org")
    monkeypatch.setenv("MATRIX_TOKEN", "syt_x")
    monkeypatch.setenv("MATRIX_ALERT_ROOM", "!abc:example.org")
    seen: dict[str, str] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self): ...
        def json(self): return {"event_id": "$e"}

    class FakeClient:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def put(self, url, **kw):
            seen["url"] = url
            seen["body"] = kw["json"]["body"]
            return FakeResponse()

    monkeypatch.setattr(matrix_channel.httpx, "AsyncClient", FakeClient)
    await matrix_channel.MatrixChannel().send(Outbound("t", "b"))
    assert "%21abc%3Aexample.org" in seen["url"]


async def test_matrix_uses_put_with_a_transaction_id(monkeypatch):
    """PUT .../send/{eventType}/{txnId} is the spec form, and the txn id makes
    a retry idempotent. A retried CRITICAL page must not post twice."""
    import inspect
    source = inspect.getsource(matrix_channel.MatrixChannel.send)
    assert "c.put(" in source
    assert "send/m.room.message/{txn}" in source


async def test_matrix_rejects_a_bare_hostname(monkeypatch):
    """MATRIX_HOMESERVER=example.org (no scheme) raised UnsupportedProtocol deep in
    httpx at send time rather than reporting the channel as unconfigured."""
    monkeypatch.setenv("MATRIX_TOKEN", "syt_x")
    monkeypatch.setenv("MATRIX_ALERT_ROOM", "!r:example.org")
    monkeypatch.setenv("MATRIX_HOMESERVER", "example.org")
    assert await matrix_channel.MatrixChannel().available() is False


# ------------------------------------------------------- per-topic channels

def test_a_topic_goes_to_its_own_channel(monkeypatch):
    """One channel for everything means vpsie billing and a FlyCheap deploy
    sit in the same scroll. SLACK_CHANNELS maps a module to a channel."""
    monkeypatch.setenv("SLACK_CHANNEL", "C_DEFAULT")
    monkeypatch.setenv("SLACK_CHANNELS",
                       "vpsie:C_VPSIE,flycheap:C_FLY,guardian:C_GUARD")
    slack = slack_channel.SlackChannel()
    assert slack.channel_for("vpsie") == "C_VPSIE"
    assert slack.channel_for("flycheap") == "C_FLY"
    assert slack.channel_for("guardian") == "C_GUARD"


def test_two_modules_can_share_one_channel(monkeypatch):
    """"Everything day-to-day in one place" is two modules naming the same
    id, not a second grouping concept that would need its own mapping."""
    monkeypatch.setenv("SLACK_CHANNEL", "C_DEFAULT")
    monkeypatch.setenv("SLACK_CHANNELS", "mail:C_DAY,calendar:C_DAY")
    slack = slack_channel.SlackChannel()
    assert slack.channel_for("mail") == slack.channel_for("calendar") == "C_DAY"


def test_an_unmapped_topic_falls_back_rather_than_vanishing(monkeypatch):
    """A message in the wrong channel is still read. One with no channel is
    lost, so there is no case where an unmapped topic is dropped."""
    monkeypatch.setenv("SLACK_CHANNEL", "C_DEFAULT")
    monkeypatch.setenv("SLACK_CHANNELS", "vpsie:C_VPSIE")
    slack = slack_channel.SlackChannel()
    assert slack.channel_for("linkedin") == "C_DEFAULT"
    assert slack.channel_for("") == "C_DEFAULT"


def test_no_mapping_at_all_still_works(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNEL", "C_DEFAULT")
    monkeypatch.delenv("SLACK_CHANNELS", raising=False)
    assert slack_channel.SlackChannel().channel_for("vpsie") == "C_DEFAULT"


def test_a_proposal_carries_the_namespace_as_its_topic():
    """"vpsie.restart_instance" is vpsie's business, so its approval card
    belongs where that estate is discussed."""
    from types import SimpleNamespace

    from atlas.kernel import approvals
    plain = SimpleNamespace(topic_param=None, topic_for=None)
    assert approvals._topic(plain, "vpsie.restart_instance", {}) == "vpsie", \
        "the namespace is still the default topic"
    by_param = SimpleNamespace(topic_param="page", topic_for=None)
    assert approvals._topic(by_param, "linkedin.publish_post",
                            {"page": "flycheap"}) == "flycheap"
    assert approvals._topic(by_param, "linkedin.publish_post", {}) == "linkedin"


async def test_a_thread_reply_in_a_channel_is_also_shown_in_the_channel(monkeypatch):
    """2026-09-25: ATLAS answered in #decisions inside the message's thread
    and Miro, looking at the channel, saw nothing. A channel thread reply
    broadcasts; a DM thread does not need to."""
    import httpx
    sent: list[dict] = []

    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(json)
            return httpx.Response(200, json={"ok": True, "ts": "1.2"})

    monkeypatch.setattr(httpx, "AsyncClient", C)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    ch = slack_channel.SlackChannel()
    monkeypatch.setattr(slack_channel.SlackChannel, "channel_for",
                        lambda self, topic: "C0C3RK8RS2E")
    await ch.send(Outbound("t", "b", thread="1790363136.669899", kind="receipt"))
    assert sent[-1]["thread_ts"] == "1790363136.669899"
    assert sent[-1]["reply_broadcast"] is True

    monkeypatch.setattr(slack_channel.SlackChannel, "channel_for",
                        lambda self, topic: "D0C2NNTCC56")
    await ch.send(Outbound("t", "b", thread="1.1", kind="receipt"))
    assert "reply_broadcast" not in sent[-1]
    await ch.send(Outbound("t", "b"))
    assert "thread_ts" not in sent[-1] and "reply_broadcast" not in sent[-1]


async def test_a_reply_goes_to_the_channel_it_answers(monkeypatch):
    """2026-09-27: "how many servers do I have?" typed in #vpsie was answered
    in #atlas-decisions, because every typed reply went to the decisions
    topic. An explicit channel wins over the topic's."""
    import httpx
    sent: list[dict] = []

    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(json)
            return httpx.Response(200, json={"ok": True, "ts": "1.2"})

    monkeypatch.setattr(httpx, "AsyncClient", C)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    ch = slack_channel.SlackChannel()
    monkeypatch.setattr(slack_channel.SlackChannel, "channel_for",
                        lambda self, topic: "CDECISIONS")
    await ch.send(Outbound("t", "b", topic="decisions", thread="9.9",
                           kind="receipt", channel="CVPSIE"))
    assert sent[-1]["channel"] == "CVPSIE" and sent[-1]["thread_ts"] == "9.9"
    await ch.send(Outbound("t", "b", topic="decisions"))
    assert sent[-1]["channel"] == "CDECISIONS"
