"""Twilio ConversationRelay for a call ATLAS places: he can cut in, and the
opening is ready the moment he answers.

the owner's voice-partner brief (2026-10-04) asks for real interruption. The
`<Gather>` line (`api/phone.py`) is one turn at a time: ATLAS speaks, then
listens. ConversationRelay holds one WebSocket for the call; Twilio does
the speech both ways at its edge and stops ATLAS's audio the instant he
talks. ATLAS sends and receives text.

**Off unless `PHONE_RELAY=on`.** It needs ConversationRelay enabled on the
Twilio account (an addendum the owner accepts in the Twilio console), and the
voice is one Twilio provides, not the Fish voice the Gather line uses. Off,
nothing here is reached and every call is the Gather line as before.

What does NOT change: who decides and how. This is a transport. Every turn
goes through the same `phone.respond` gate as the Gather line: the keypad,
the plain-spoken-yes conditions and its stop window, the read-back, the
receipts. No model output reaches the executor here either.

What it adds:

  * The three-beat opening is the relay's `welcomeGreeting`, so it plays
    with no fetch after he answers.
  * Interruption. When he cuts in, Twilio reports how much he heard. If he
    cut in before the question was finished, a "yes" that follows is not
    taken as an answer to it: the question is asked again, whole. (He
    cannot agree to something he has not heard.)
  * The stop window after a read-back is a timer here: silence through it
    goes ahead; "stop" or 2 cancels.
  * A slow answer is covered by "One moment." rather than dead air.

Safety of the socket itself: the URL carries a token minted when the call
opened -- an HMAC over the CallSid with the server's secret, good for two
minutes and once -- and the first message must be Twilio's `setup` naming
that same CallSid on a call ATLAS placed. Anything else is closed. If the
relay fails, Twilio returns to `/webhooks/twilio/relay/after`, which puts
the call back on the Gather line.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from xml.sax.saxutils import quoteattr

log = logging.getLogger("atlas.api.relay")

PATH = "/webhooks/twilio/relay"
AFTER_PATH = "/webhooks/twilio/relay/after"
TOKEN_TTL_S = 120
SETUP_TIMEOUT_S = 5.0
FILLER_AFTER_S = 1.5
FILLER = "One moment."
DONE = "done"
_USED: dict[str, float] = {}


def enabled() -> bool:
    return os.getenv("PHONE_RELAY", "").strip().lower() in ("on", "1", "true", "yes")


def _secret() -> bytes:
    return os.getenv("SESSION_SECRET", "").encode()


def mint(call: str, now: float | None = None) -> str:
    """A token for this call's socket: expiry and an HMAC over the CallSid."""
    exp = int((now or time.time()) + TOKEN_TTL_S)
    sig = hmac.new(_secret(), f"relay|{call}|{exp}".encode(), hashlib.sha256).hexdigest()[:40]
    return f"{exp}.{sig}"


def redeem(call: str, token: str, now: float | None = None) -> bool:
    """True once, for the right call, inside the token's two minutes."""
    now = now or time.time()
    for used, at in list(_USED.items()):
        if now - at > TOKEN_TTL_S * 2:
            _USED.pop(used, None)
    exp_s, _, sig = (token or "").partition(".")
    if not (_secret() and call and exp_s.isdigit() and sig) or token in _USED:
        return False
    if int(exp_s) < now:
        return False
    want = hmac.new(_secret(), f"relay|{call}|{exp_s}".encode(), hashlib.sha256).hexdigest()[:40]
    if not hmac.compare_digest(want, sig):
        return False
    _USED[token] = now
    return True


def twiml(host: str, call: str, greeting: str) -> str:
    """The opening TwiML: connect the call to the relay, speaking the
    opening as its welcome greeting. Provider and voice only when set."""
    attrs = {
        "url": f"wss://{host}{PATH}?t={mint(call)}",
        "welcomeGreeting": " ".join(greeting.split())[:900],
        "welcomeGreetingInterruptible": "any",
        "interruptible": "any",
        "dtmfDetection": "true",
        "language": os.getenv("PHONE_SPEECH_LANGUAGE", "en-US").strip() or "en-US",
    }
    for env, attr in (("PHONE_RELAY_TTS", "ttsProvider"), ("PHONE_RELAY_VOICE", "voice"),
                      ("PHONE_RELAY_STT", "transcriptionProvider")):
        value = os.getenv(env, "").strip()
        if value:
            attrs[attr] = value
    rendered = " ".join(f"{k}={quoteattr(v)}" for k, v in attrs.items())
    return (f'<Response><Connect action="{AFTER_PATH}" method="POST">'
            f"<ConversationRelay {rendered}/></Connect></Response>")


class Session:
    """One call's socket: what he has heard, and the stop-window timer."""

    def __init__(self, call: str) -> None:
        self.call = call
        self.question_heard = True
        self.window: asyncio.Task | None = None
        self.ended = False

    def cancel_window(self) -> None:
        if self.window is not None and not self.window.done():
            self.window.cancel()
        self.window = None


def heard_question(spoken_until: str, question: str) -> bool:
    """Did what he heard before cutting in include the whole question?"""
    q = " ".join((question or "").split()).lower()
    if not q:
        return True
    return q in " ".join((spoken_until or "").split()).lower()


async def turn(session: Session, said: str, digit: str) -> tuple[str, bool]:
    """One exchange, through the same gate as the Gather line. Returns the
    reply and whether the call should end."""
    from . import phone
    call = session.call
    keypad = {"1": "do it", "2": "decline"}
    if digit in keypad or digit in (phone.REPEAT_KEY, phone.STOP_KEY):
        phone.confirm_human(call)
    if digit == phone.STOP_KEY:
        await phone.wrap_up(call)
        return phone.STOPPED, True
    if digit == phone.REPEAT_KEY:
        last = next((what for who, what in reversed(phone.history(call)) if who == "ATLAS"), "")
        return last or "There's nothing to repeat yet.", False
    if phone.confirming(call):
        phone.remember(call, "the owner", f"(pressed {digit})" if digit else said or "(said nothing)")
        reply = await phone.finish_confirm(call, digit, said)
        phone.remember(call, "ATLAS", reply)
        return reply, False
    text = keypad.get(digit) or " ".join((said or "").split())
    if not text:
        return phone.QUIET_FIRST, False
    if (not digit and not session.question_heard and phone.pending(call) is not None
            and (phone.plain_yes(text) or phone.plain_no(text))):
        # He cut in before the question was finished: an answer to a
        # question he has not heard is not an answer. Ask it whole.
        session.question_heard = True
        from ..kernel import briefing
        ask = phone.question(call) or briefing.question("")
        reply = f"Let me finish the question. {ask} {briefing.KEYPAD_HINT}"
        phone.remember(call, "the owner", text)
        phone.remember(call, "ATLAS", reply)
        return reply, False
    phone.remember(call, "the owner", f"(pressed {digit}) {text}" if digit in keypad else text)
    reply = await phone.respond(text, call, phone.begin_turn(call), keypad=digit in keypad)
    phone.remember(call, "ATLAS", reply)
    return reply, phone.machine(call)


async def handle(ws) -> None:
    """The socket for one call. `ws` is a Starlette WebSocket."""
    from . import phone
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), SETUP_TIMEOUT_S))
    except Exception:                                   # noqa: BLE001
        await ws.close(code=1008)
        return
    call = str(first.get("callSid") or "")
    token = ws.query_params.get("t", "")
    if first.get("type") != "setup" or not redeem(call, token) or not phone.outbound(call):
        log.warning("a relay socket did not open as a call ATLAS placed; closed")
        await ws.close(code=1008)
        return
    session = Session(call)
    send_lock = asyncio.Lock()

    async def say(text: str, *, last: bool = True) -> None:
        async with send_lock:
            await ws.send_text(json.dumps(
                {"type": "text", "token": " ".join(text.split())[:900], "last": last}))

    async def end() -> None:
        session.ended = True
        async with send_lock:
            await ws.send_text(json.dumps({"type": "end", "handoffData": DONE}))

    async def answer(said: str, digit: str) -> None:
        task = asyncio.ensure_future(turn(session, said, digit))
        done, _ = await asyncio.wait({task}, timeout=FILLER_AFTER_S)
        if not done:
            await say(FILLER, last=False)
        try:
            reply, finished = await task
        except Exception:
            log.exception("relay turn failed")
            reply, finished = ("Something went wrong at my end. It's in Slack, and "
                               "the keypad still works."), False
        await say(reply)
        if finished:
            await end()
        elif phone.confirming(call):
            session.window = asyncio.ensure_future(stop_window())

    async def stop_window() -> None:
        # Silence through the window goes ahead, exactly as on the Gather line.
        await asyncio.sleep(phone.STOP_WINDOW_S)
        if phone.confirming(call) and not session.ended:
            reply = await phone.finish_confirm(call, "", "")
            phone.remember(call, "ATLAS", reply)
            await say(reply)

    try:
        while not session.ended:
            msg = json.loads(await ws.receive_text())
            kind = msg.get("type")
            if kind == "prompt":
                if msg.get("last") is False:
                    continue                        # a partial: never acted on
                session.cancel_window()
                await answer(str(msg.get("voicePrompt") or ""), "")
            elif kind == "dtmf":
                session.cancel_window()
                await answer("", str(msg.get("digit") or "")[:1])
            elif kind == "interrupt":
                # How much of ATLAS's last line he heard before cutting in.
                session.question_heard = heard_question(
                    str(msg.get("utteranceUntilInterrupt") or ""), phone.question(call))
            elif kind == "error":
                log.warning("relay reported an error: %s", str(msg.get("description"))[:200])
                break
    except Exception as exc:                            # noqa: BLE001
        # A dropped socket is how every call ends; Twilio falls back to
        # AFTER_PATH when it was not ATLAS that ended it.
        log.info("relay socket closed: %s", type(exc).__name__)
    finally:
        session.cancel_window()
