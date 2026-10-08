"""Twilio ConversationRelay for calls ATLAS places (atlas/api/relay.py).

A transport, behind PHONE_RELAY=on. The same gate decides as on the Gather
line; what is new is the socket's own safety (a single-use token bound to
the CallSid, then Twilio's setup), cutting in, and the stop window as a
timer."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from atlas.api import phone, relay
from atlas.kernel import briefing

SID = "CA" + "9" * 32
SECRET = "s" * 40


@pytest.fixture
def on(monkeypatch, memory_backend):
    monkeypatch.setenv("PHONE_RELAY", "on")
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("ATLAS_HOST", "atlas.test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-token")
    monkeypatch.setattr(phone, "STOP_WINDOW_S", 0.05)
    relay._USED.clear()
    phone._OUTBOUND[SID] = (phone.time.monotonic(), False)
    phone._HUMAN.add(SID)
    phone.set_pending(SID, 41)
    phone.set_question(SID, "Want me to restart the worker, yes or no?")

    async def wrap_up(call, direction=""):
        phone.forget(call)
    monkeypatch.setattr(phone, "wrap_up", wrap_up)
    from atlas.api.main import app
    yield TestClient(app)
    phone.forget(SID)
    relay._USED.clear()


def _deciding(monkeypatch):
    decided: list = []

    async def decide(pid, verdict, via="slack"):
        decided.append((pid, verdict, via.split(":")[0]))

        class V:
            def render(self):
                return "Running it." if verdict == "approve" else "Declined."
        return V()
    monkeypatch.setattr("atlas.kernel.approvals.decide", decide)

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(phone, "_receipt_kept", nothing)
    return decided


def _sign(url: str) -> str:
    import base64
    import hashlib
    import hmac
    return base64.b64encode(hmac.new(b"twilio-token", url.encode(), hashlib.sha1).digest()).decode()


def _open(client, call=SID, token=None, signature=None):
    token = token or relay.mint(call)
    sig = _sign(f"wss://atlas.test{relay.PATH}?t={token}") if signature is None else signature
    return client.websocket_connect(f"{relay.PATH}?t={token}",
                                    headers={"x-twilio-signature": sig})


def _setup(ws, call=SID):
    ws.send_text(json.dumps({"type": "setup", "callSid": call, "direction": "outbound-api"}))


def _text(ws):
    msg = json.loads(ws.receive_text())
    assert msg["type"] == "text", msg
    return msg


# ------------------------------------------------------------------ the token

def test_a_token_is_for_one_call_once_and_for_two_minutes(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    relay._USED.clear()
    now = time.time()
    token = relay.mint(SID, now)
    assert not relay.redeem("CA" + "1" * 32, token, now), "another call"
    assert relay.redeem(SID, token, now)
    assert not relay.redeem(SID, token, now), "once"
    late = relay.mint(SID, now - relay.TOKEN_TTL_S - 5)
    assert not relay.redeem(SID, late, now), "expired"
    assert not relay.redeem(SID, "123.forged", now) and not relay.redeem(SID, "", now)
    monkeypatch.setenv("SESSION_SECRET", "")
    assert not relay.redeem(SID, relay.mint(SID, now), now), "no secret, no socket"


def test_the_twiml_connects_with_the_opening_as_the_greeting(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    xml = relay.twiml("atlas.test", SID, "Hey Miro, it's ATLAS. <Disk> & more. Yes or no?")
    assert xml.startswith(f'<Response><Connect action="{relay.AFTER_PATH}" method="POST">')
    assert 'url="wss://atlas.test/webhooks/twilio/relay?t=' in xml
    assert "&lt;Disk&gt; &amp; more" in xml and "<Disk>" not in xml
    assert 'interruptible="any"' in xml and 'dtmfDetection="true"' in xml
    assert "ttsProvider" not in xml, "provider and voice only when Miro sets them"
    monkeypatch.setenv("PHONE_RELAY_VOICE", "abc")
    assert 'voice="abc"' in relay.twiml("atlas.test", SID, "x")


# ------------------------------------------------------------------ the socket

def test_off_by_default_the_socket_refuses(monkeypatch, memory_backend):
    monkeypatch.delenv("PHONE_RELAY", raising=False)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("ATLAS_HOST", "atlas.test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-token")
    from atlas.api.main import app
    with pytest.raises(WebSocketDisconnect), _open(TestClient(app)) as ws:
        ws.receive_text()


def test_a_handshake_twilio_did_not_sign_is_refused(on):
    """The token says which call; the signature says it is Twilio. Both."""
    for bad in ("", "bm90LXR3aWxpbw=="):
        with pytest.raises(WebSocketDisconnect), _open(on, signature=bad) as ws:
            _setup(ws)
            ws.receive_text()


@pytest.mark.parametrize("first", [
    {"type": "prompt", "voicePrompt": "do it", "callSid": SID},      # not a setup
    {"type": "setup", "callSid": "CA" + "1" * 32},                    # another call
])
def test_a_socket_that_does_not_open_as_this_call_is_closed(on, first):
    with pytest.raises(WebSocketDisconnect), _open(on) as ws:
        ws.send_text(json.dumps(first))
        ws.receive_text()


def test_a_forged_token_or_a_call_atlas_did_not_place_is_closed(on):
    with pytest.raises(WebSocketDisconnect), _open(on, token="9999999999.deadbeef") as ws:
        _setup(ws)
        ws.receive_text()
    phone._OUTBOUND.pop(SID, None)
    with pytest.raises(WebSocketDisconnect), _open(on) as ws:
        _setup(ws)
        ws.receive_text()


def test_key_1_decides_through_the_same_gate_as_miro(on, monkeypatch):
    decided = _deciding(monkeypatch)
    with _open(on) as ws:
        _setup(ws)
        ws.send_text(json.dumps({"type": "dtmf", "digit": "1"}))
        assert "Running it." in _text(ws)["token"]
    assert decided == [(41, "approve", "call_out")]


def test_a_plain_yes_is_read_back_and_silence_through_the_window_goes_ahead(on, monkeypatch):
    decided = _deciding(monkeypatch)

    async def undoable(pid):
        return True, "Restart the worker"
    monkeypatch.setattr(phone, "_undoable", undoable)
    with _open(on) as ws:
        _setup(ws)
        ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "yeah", "last": True}))
        assert "Okay, I'll restart the worker. Say stop" in _text(ws)["token"]
        assert decided == []
        assert "Running it." in _text(ws)["token"]       # the window passed in silence
    assert decided == [(41, "approve", "call_out")]


def test_stop_in_the_window_cancels(on, monkeypatch):
    decided = _deciding(monkeypatch)
    monkeypatch.setattr(phone, "STOP_WINDOW_S", 5)

    async def undoable(pid):
        return True, "Restart the worker"
    monkeypatch.setattr(phone, "_undoable", undoable)
    with _open(on) as ws:
        _setup(ws)
        ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "yes", "last": True}))
        assert "Say stop" in _text(ws)["token"]
        ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "stop", "last": True}))
        reply = _text(ws)["token"]
        assert phone.CANCELLED in reply and "yes or no" in reply
    assert decided == [] and phone.pending(SID) == 41


def test_a_yes_after_cutting_in_before_the_question_is_not_an_answer(on, monkeypatch):
    """Playback-aware consent: he cannot agree to what he has not heard."""
    decided = _deciding(monkeypatch)

    async def undoable(pid):
        return True, "Restart the worker"
    monkeypatch.setattr(phone, "_undoable", undoable)
    with _open(on) as ws:
        _setup(ws)
        ws.send_text(json.dumps({"type": "interrupt", "durationUntilInterruptMs": 900,
                                 "utteranceUntilInterrupt": "Hey Miro, it's ATLAS. FlyCheap's"}))
        ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "yeah yeah", "last": True}))
        reply = _text(ws)["token"]
        assert reply.startswith("Let me finish the question. Want me to restart the worker")
        assert briefing.KEYPAD_HINT in reply and decided == []
        assert not phone.confirming(SID)
        # Having now heard it, a yes is read back as usual.
        ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "yes", "last": True}))
        assert "Say stop" in _text(ws)["token"]


def test_cutting_in_after_the_whole_question_keeps_his_answer():
    q = "Want me to restart the worker, yes or no?"
    assert relay.heard_question(f"Hey Miro. The poller is stuck. {q} Press 1", q)
    assert not relay.heard_question("Hey Miro. The poller is stuck. Want me to", q)
    assert relay.heard_question("anything", ""), "no question pending: nothing to have missed"


def test_partials_are_never_acted_on_and_star_ends_the_call(on, monkeypatch):
    decided = _deciding(monkeypatch)
    with _open(on) as ws:
        _setup(ws)
        ws.send_text(json.dumps({"type": "prompt", "voicePrompt": "do it", "last": False}))
        ws.send_text(json.dumps({"type": "dtmf", "digit": "*"}))
        assert phone.STOPPED in _text(ws)["token"]
        end = json.loads(ws.receive_text())
        assert end == {"type": "end", "handoffData": relay.DONE}
    assert decided == []


def test_key_0_repeats_the_last_line(on):
    phone.remember(SID, "ATLAS", "Hey Miro. Want me to restart the worker, yes or no?")
    with _open(on) as ws:
        _setup(ws)
        ws.send_text(json.dumps({"type": "dtmf", "digit": "0"}))
        assert "Want me to restart the worker" in _text(ws)["token"]
