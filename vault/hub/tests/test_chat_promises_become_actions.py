"""A promise is not an action. "I'll mark this pattern as accepted" with no
tool call behind it left the inbox row exactly where it was; now the engine
is nudged once to do it, and in a conversation opened about an alert, that
alert is the target even when the person does not repeat its key."""
import pytest

from guardian_hub import models as m
from guardian_hub.chat import providers
from guardian_hub.db import SessionLocal
from guardian_hub.routers import chat as chat_router
from guardian_hub.routers.chat import _promises_a_change, _thread_subjects

from tests.test_seed_helpers import seeded_demo


@pytest.mark.parametrize("text,promise", [
    ("I'll mark this pattern as accepted and stop flagging it.", True),
    ("I will stop flagging that device.", True),
    ("I’ll acknowledge the alert now.", True),
    ("Noted. I am going to resolve it.", True),
    ("That looks normal to me; nothing to do.", False),
    ("I marked it as accepted.", False),           # a claim, handled by _claims_an_unmade_change
    ("Reply yes and I will trust it.", False),      # an offer, handled by the proposal checks
])
def test_promise_detection(text, promise):
    assert _promises_a_change(text, []) is promise
    # a turn that DID write something may describe what it will do next
    assert _promises_a_change(text, ["resolve_alert"]) is False


def test_thread_subjects_come_from_the_handoff_opening():
    t = m.ChatThread(turns=[{"role": "user", "text": "What can you do about the alert lan:primary:unusual-hour:aa:bb?"},
                            {"role": "guardian", "text": "..."}])
    assert _thread_subjects(t) == ["lan:primary:unusual-hour:aa:bb"]


def test_thats_normal_in_an_alert_conversation_actually_resolves_it(client, monkeypatch):
    tid = seeded_demo(client)
    db = SessionLocal()
    alert = db.query(m.Alert).filter_by(tenant_id=tid, status="open").order_by(m.Alert.last_seen.desc()).first()
    key = alert.key
    db.close()
    h = client.post("/api/v1/chat/handoff", json={"key": key, "thread": None})
    assert h.status_code == 200, h.text
    thread = h.json()["thread_id"]

    seq = [
        # 1. the model promises instead of acting
        providers.ProviderReply(text="Understood, that is normal. I'll mark this pattern as accepted and stop flagging it.",
                                tool_calls=[], provider="groq/x"),
        # 2. nudged: it calls the tool, naming the thread's alert without the person repeating it
        providers.ProviderReply(text="", tool_calls=[{"name": "resolve_alert", "args": {"key": key}}], provider="groq/x"),
        # 3. closing answer
        providers.ProviderReply(text="Done: that alert is resolved and I will not flag it again.", tool_calls=[], provider="groq/x"),
    ]
    monkeypatch.setattr(providers, "chat", lambda *a, **k: seq.pop(0))
    monkeypatch.setattr(chat_router, "QUEUE_POLL_SECONDS", 0.05)
    monkeypatch.setattr(chat_router, "QUEUE_POLL_INTERVAL", 0.01)
    r = client.post("/api/v1/chat", json={"message": "thats normal", "thread": thread, "tab": "inbox"})
    assert r.status_code == 200, r.text
    j = r.json()
    steps = {(s["tool"], s["status"]) for s in j["tool_steps"]}
    assert ("resolve_alert", "ok") in steps, j
    assert j["proposal"] is None
    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid, key=key).one().status == "resolved"
    db.close()


def test_a_promise_that_survives_the_nudge_is_not_served(client, monkeypatch):
    seeded_demo(client)
    seq = [providers.ProviderReply(text="I'll acknowledge that for you.", tool_calls=[], provider="groq/x"),
           providers.ProviderReply(text="I'll acknowledge that for you.", tool_calls=[], provider="groq/x"),
           providers.ProviderReply(text="I'll acknowledge that for you.", tool_calls=[], provider="groq/x")]
    monkeypatch.setattr(providers, "chat", lambda *a, **k: seq.pop(0) if seq else providers.ProviderReply(text="", tool_calls=[], provider="groq/x"))
    r = client.post("/api/v1/chat", json={"message": "ok", "thread": None, "tab": "inbox"})
    assert r.status_code == 200
    assert "I'll acknowledge" not in r.json()["text"] and r.json()["provider"] == "rules"


def test_thread_subjects_ignores_guardian_replies_carrying_untrusted_text():
    """A key that appears only in Vault's own reply (which can embed a
    device's DHCP hostname or tool output) must NOT count as a named target
    -- otherwise a crafted hostname plants a subject the user never chose."""
    from guardian_hub.routers.chat import _thread_subjects
    t = m.ChatThread(turns=[
        {"role": "user", "text": "What can you do about the alert lan:primary:unusual-hour:aa:bb?"},
        # attacker-influenced content echoed back in guardian's reply
        {"role": "guardian", "text": "The device (about the alert sec:cve:prod) is online."},
        {"role": "user", "text": "thats normal"},
    ])
    subs = _thread_subjects(t)
    assert subs == ["lan:primary:unusual-hour:aa:bb"]
    assert "sec:cve:prod" not in subs
