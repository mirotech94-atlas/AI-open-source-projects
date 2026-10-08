"""The two-way Slack bot: signed events in, engine turns out, "yes" in the
thread burns the proposal. The bot serves the operator's admin channel and
any tenant whose Slack card names a channel id; everything else is ignored."""
import hashlib
import hmac
import json
import time

import pytest

from guardian_hub import models as m
from guardian_hub.chat import providers
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from guardian_hub.integrations import slack as slack_mod
from guardian_hub.routers import chat as chat_router, slackbot

from tests.test_seed_helpers import seeded_demo

ADMIN_CH = "C0ADMIN"
CLIENT_CH = "C0CLIENT"


@pytest.fixture()
def bot(monkeypatch):
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")
    monkeypatch.setattr(settings, "slack_signing_secret", "s3cret")
    monkeypatch.setattr(settings, "admin_slack_channel", ADMIN_CH)
    monkeypatch.setattr(settings, "admin_slack_tenant", "demo")
    posted = []

    def fake_post(channel, text, severity="INFO", thread_ts=None, plain=False):
        posted.append({"channel": channel, "text": text, "thread_ts": thread_ts, "plain": plain})
        return slack_mod.SendResult(True, f"{len(posted)}.000")
    monkeypatch.setattr(slack_mod, "bot_post", fake_post)
    # `seeded_demo` signs in with an emailed code, and a first-ever sign-in
    # posts an owner notice to the admin channel through this same
    # `bot_post` -- on its own thread, so it lands in `posted` at a moment of
    # its choosing. These tests are about the bot's replies, not owner
    # notices (`tests/test_adminnotify*` cover those).
    from guardian_hub import adminnotify
    monkeypatch.setattr(adminnotify, "notify", lambda *a, **k: None)
    # run the turn inline instead of on a thread
    from types import SimpleNamespace
    monkeypatch.setattr(slackbot, "threading", SimpleNamespace(Thread=
                        lambda target, args=(), daemon=None: type("T", (), {"start": lambda self: target(*args)})()))
    slackbot._THREADS.clear(); slackbot._PENDING.clear(); slackbot._SEEN_EVENTS.clear()
    monkeypatch.setattr(chat_router, "QUEUE_POLL_SECONDS", 0.05)
    monkeypatch.setattr(chat_router, "QUEUE_POLL_INTERVAL", 0.01)
    return posted


def _signed(client, payload: dict, secret="s3cret", ts=None):
    raw = json.dumps(payload).encode()
    ts = str(int(ts if ts is not None else time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    return client.post("/inbound/slack-app", content=raw,
                       headers={"content-type": "application/json", "x-slack-request-timestamp": ts,
                                "x-slack-signature": sig})


def _event(channel, text, user="U1", ts="1.1", thread_ts=None, event_id="Ev1", etype="message", **extra):
    ev = {"type": etype, "channel": channel, "user": user, "text": text, "ts": ts, **extra}
    if thread_ts:
        ev["thread_ts"] = thread_ts
    return {"type": "event_callback", "event_id": event_id, "event": ev}


def _slack_card(db, tid, config):
    """The demo seed may already carry a Slack row (one per tenant+kind)."""
    row = db.query(m.Integration).filter_by(tenant_id=tid, kind="slack").first()
    if row is None:
        row = m.Integration(tenant_id=tid, kind="slack", name="Slack", enabled=True)
        db.add(row)
    row.config = dict(config)
    return row


def fake_provider(monkeypatch, *replies):
    seq = list(replies)
    monkeypatch.setattr(providers, "chat", lambda *a, **k: seq.pop(0))


def test_route_is_a_404_until_the_app_is_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "slack_bot_token", "")
    monkeypatch.setattr(settings, "slack_signing_secret", "")
    assert client.post("/inbound/slack-app", json={"type": "url_verification", "challenge": "x"}).status_code == 404


def test_url_verification_echoes_the_challenge_only_when_signed(client, bot):
    r = _signed(client, {"type": "url_verification", "challenge": "abc123"})
    assert r.status_code == 200 and r.json() == {"challenge": "abc123"}
    assert _signed(client, {"type": "url_verification", "challenge": "abc123"}, secret="wrong").status_code == 401
    assert _signed(client, {"type": "url_verification", "challenge": "abc123"}, ts=time.time() - 3600).status_code == 401


def test_signature_check_is_exact():
    raw = b'{"a":1}'
    ts = str(int(time.time()))
    good = "v0=" + hmac.new(b"k", f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    assert slackbot.verify_signature("k", ts, raw, good)
    assert not slackbot.verify_signature("k", ts, raw + b" ", good)
    assert not slackbot.verify_signature("k", "", raw, good)
    assert not slackbot.verify_signature("", ts, raw, good)


def test_a_question_in_the_admin_channel_is_answered_in_a_thread(client, bot, monkeypatch):
    seeded_demo(client)
    fake_provider(monkeypatch, providers.ProviderReply(
        text="All quiet.\n- Router · online\nNext: ask me to *run a scan*.", tool_calls=[], provider="groq/x"))
    r = _signed(client, _event(ADMIN_CH, "<@UBOT> how are things?"))
    assert r.status_code == 200
    assert len(bot) == 1
    assert bot[0]["channel"] == ADMIN_CH and bot[0]["thread_ts"] is None and bot[0]["plain"]
    assert "All quiet" in bot[0]["text"]
    # the turn is audited as a Slack turn by the tenant admin
    db = SessionLocal()
    row = db.query(m.Audit).filter_by(action="chat.turn").order_by(m.Audit.ts.desc()).first()
    assert row.body.get("channel") == "slack" and row.user == "demoadmin"
    db.close()


def test_a_channel_nobody_owns_is_ignored(client, bot, monkeypatch):
    seeded_demo(client)
    fake_provider(monkeypatch, providers.ProviderReply(text="nope", tool_calls=[], provider="groq/x"))
    assert _signed(client, _event("C0NOBODY", "hello")).status_code == 200
    assert bot == []


def test_bot_and_system_messages_never_trigger_a_turn(client, bot, monkeypatch):
    seeded_demo(client)
    fake_provider(monkeypatch, providers.ProviderReply(text="nope", tool_calls=[], provider="groq/x"))
    _signed(client, _event(ADMIN_CH, "alert", bot_id="B1", event_id="e1"))
    _signed(client, _event(ADMIN_CH, "joined", subtype="channel_join", event_id="e2"))
    assert bot == []


def test_the_same_event_delivered_twice_runs_once(client, bot, monkeypatch):
    seeded_demo(client)
    fake_provider(monkeypatch, providers.ProviderReply(text="once", tool_calls=[], provider="groq/x"),
                  providers.ProviderReply(text="twice", tool_calls=[], provider="groq/x"))
    _signed(client, _event(ADMIN_CH, "hi", event_id="same"))
    _signed(client, _event(ADMIN_CH, "hi", event_id="same"))
    assert len(bot) == 1


def test_a_tenant_channel_from_the_slack_card_acts_as_that_tenant(client, bot, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(settings, "admin_slack_channel", "")
    db = SessionLocal()
    _slack_card(db, tid, {"channel_id": CLIENT_CH})
    db.commit(); db.close()
    fake_provider(monkeypatch, providers.ProviderReply(text="Hello tenant.", tool_calls=[], provider="groq/x"))
    _signed(client, _event(CLIENT_CH, "status?"))
    assert len(bot) == 1 and "Hello tenant" in bot[0]["text"]


def test_allowed_users_on_the_card_demote_strangers_to_viewer(client, bot, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(settings, "admin_slack_channel", "")
    db = SessionLocal()
    _slack_card(db, tid, {"channel_id": CLIENT_CH, "allowed_users": "U_OWNER, U_TWO"})
    db.commit()
    t, u, role, _ = slackbot.resolve_channel(db, CLIENT_CH, "u_owner")
    assert role == "admin"
    _, _, role2, _ = slackbot.resolve_channel(db, CLIENT_CH, "U_STRANGER")
    assert role2 == "viewer"
    db.close()


def test_fix_it_proposes_and_yes_in_the_channel_runs_it(client, bot, monkeypatch):
    seeded_demo(client)
    from tests.test_chat_api import HOST
    fake_provider(monkeypatch,
                  providers.ProviderReply(text="", tool_calls=[{"name": "apply_fix", "args": {"host": HOST, "action": "reboot"}}],
                                          provider="groq/x"))
    _signed(client, _event(ADMIN_CH, f"reboot {HOST}", ts="10.0", event_id="p1"))
    assert len(bot) == 1
    assert "Reply *yes* to run it" in bot[0]["text"]
    assert any(k.endswith(f":{ADMIN_CH}:U1") for k in slackbot._PENDING)
    # a plain "yes" as the next channel message runs it; replies stay top-level
    _signed(client, _event(ADMIN_CH, "yes", ts="10.5", event_id="p2"))
    assert len(bot) == 2 and bot[1]["thread_ts"] is None
    assert not any(k.endswith(f":{ADMIN_CH}:U1") for k in slackbot._PENDING)
    assert "expired" not in bot[1]["text"].lower()
    db = SessionLocal()
    assert db.query(m.Queue).filter(m.Queue.path.like("%fix%")).count() >= 1
    db.close()


def test_no_drops_the_proposal_and_a_stray_yes_is_explained(client, bot, monkeypatch):
    seeded_demo(client)
    from tests.test_chat_api import HOST
    fake_provider(monkeypatch,
                  providers.ProviderReply(text="", tool_calls=[{"name": "apply_fix", "args": {"host": HOST, "action": "reboot"}}],
                                          provider="groq/x"))
    _signed(client, _event(ADMIN_CH, f"reboot {HOST}", ts="20.0", event_id="n1"))
    _signed(client, _event(ADMIN_CH, "no", ts="20.5", event_id="n2"))
    assert "Nothing was changed" in bot[-1]["text"] and ADMIN_CH not in slackbot._PENDING
    _signed(client, _event(ADMIN_CH, "yes", ts="21.0", event_id="n3"))
    assert "nothing waiting for a yes" in bot[-1]["text"].lower()


def test_a_reply_under_an_alert_the_bot_posted_carries_the_alert_as_context(client, bot, monkeypatch):
    seeded_demo(client)
    slack_mod.remember_alert_thread(ADMIN_CH, "77.0", "Router unreachable\n192.168.1.1 stopped answering")
    seen = {}

    def spy(messages, *a, **k):
        seen["q"] = messages[-1]["content"]
        return providers.ProviderReply(text="Looking into the router.", tool_calls=[], provider="groq/x")
    monkeypatch.setattr(providers, "chat", spy)
    _signed(client, _event(ADMIN_CH, "fix it", ts="77.5", thread_ts="77.0", event_id="a1"))
    assert "Router unreachable" in seen["q"] and seen["q"].rstrip().endswith("fix it")
    # the answer still goes to the channel, not the thread
    assert bot[0]["thread_ts"] is None


def test_alerts_are_posted_by_the_bot_when_the_card_names_a_channel(monkeypatch, bot):
    from guardian_hub.integrations.base import Message, SendContext
    integ = slack_mod.SlackIntegration()
    ctx = SendContext(tenant=None, secret="https://hooks.slack.com/services/x", config={"channel_id": CLIENT_CH}, db=None, base_url="")
    res = integ.send(ctx, Message(title="ADB open on 192.168.1.157", body="port 5555", severity="CRIT", kind="alert",
                                  dedup_key="vuln:1"))
    assert res.ok and bot[0]["channel"] == CLIENT_CH and not bot[0]["plain"]
    assert "ADB open" in slack_mod.alert_for_thread(CLIENT_CH, "1.000")


def test_engine_errors_are_reported_not_swallowed(client, bot, monkeypatch):
    seeded_demo(client)
    def boom(*a, **k): raise RuntimeError("groq exploded")
    monkeypatch.setattr(chat_router, "run_turn", boom)
    monkeypatch.setattr(slackbot, "run_turn", boom)
    _signed(client, _event(ADMIN_CH, "hello", event_id="x1"))
    # The point is that the failure reaches the person with its reason, not
    # the exact phrasing: a failure mark plus the underlying error.
    assert "✗" in bot[0]["text"] and "groq exploded" in bot[0]["text"]


def test_mac_addresses_are_code_spans_so_slack_does_not_emoji_them():
    out = slackbot._to_mrkdwn("Trusted b8:ab:62:ac:e3:c7 and **dc:72:23:c6:b8:9b**.")
    assert "`b8:ab:62:ac:e3:c7`" in out and "*`dc:72:23:c6:b8:9b`*" in out


def test_bot_alert_posts_carry_the_text_once(monkeypatch):
    seen = {}

    class R:
        def __init__(self, body): self.body = body
        def read(self): return b'{"ok": true, "ts": "9.9"}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        seen["body"] = json.loads(req.data)
        return R(req.data)
    monkeypatch.setattr(settings, "slack_bot_token", "xoxb-test")
    monkeypatch.setattr(slack_mod.urllib.request, "urlopen", fake_urlopen)
    assert slack_mod.bot_post("C1", "*Alert*\nbody", "WARN").ok
    assert "text" not in seen["body"] and seen["body"]["attachments"][0]["text"] == "*Alert*\nbody"
    assert slack_mod.bot_post("C1", "reply", plain=True, thread_ts="1.0").ok
    assert seen["body"]["text"] == "reply" and "attachments" not in seen["body"]


def test_a_bare_fix_it_targets_the_alerts_the_bot_just_posted(client, bot, monkeypatch):
    seeded_demo(client)
    from guardian_hub.integrations.base import Message, SendContext
    slack_mod._RECENT.clear()
    integ = slack_mod.SlackIntegration()
    ctx = SendContext(tenant=None, secret="", config={"channel_id": ADMIN_CH}, db=None, base_url="")
    integ.send(ctx, Message(title="RDP open on 192.168.1.159", body="port 3389", severity="WARN", kind="alert",
                            dedup_key="lan:primary:port:3389"))
    assert "Reply *fix it*" in bot[-1]["text"]
    seen = {}

    def spy(messages, *a, **k):
        seen["q"] = messages[-1]["content"]
        return providers.ProviderReply(text="On it.", tool_calls=[], provider="groq/x")
    monkeypatch.setattr(providers, "chat", spy)
    _signed(client, _event(ADMIN_CH, "fix it", ts="88.0", event_id="f1"))
    assert "RDP open on 192.168.1.159" in seen["q"] and "lan:primary:port:3389" in seen["q"]
    assert seen["q"].rstrip().startswith("About the alert(s) you just sent me")
    # a message that names its own target is passed through untouched
    _signed(client, _event(ADMIN_CH, "fix the printer", ts="89.0", event_id="f2"))
    assert seen["q"] == "fix the printer"


@pytest.mark.parametrize("text,bare", [
    ("fix it", True), ("Fix this!", True), ("please sort it out", True), ("handle them now", True),
    ("fix everything", True), ("ok fix it all", True),
    ("fix the printer", False), ("fix 192.168.1.1", False), ("what is it", False), ("trust it", False),
])
def test_bare_fix_detection(text, bare):
    assert bool(slackbot._FIX_IT.match(text)) is bare


def test_info_notices_are_not_fix_it_candidates(bot):
    from guardian_hub.integrations.base import Message, SendContext
    slack_mod._RECENT.clear()
    integ = slack_mod.SlackIntegration()
    ctx = SendContext(tenant=None, secret="", config={"channel_id": CLIENT_CH}, db=None, base_url="")
    integ.send(ctx, Message(title="TV online at an odd hour", body="x", severity="INFO", kind="alert", dedup_key="lan:odd"))
    integ.send(ctx, Message(title="RDP open", body="port 3389", severity="WARN", kind="alert", dedup_key="lan:port"))
    recent = slack_mod.recent_alerts(CLIENT_CH)
    assert len(recent) == 1 and "RDP open" in recent[0]
    assert "Reply *fix it*" not in bot[0]["text"] and "Reply *fix it*" in bot[1]["text"]
