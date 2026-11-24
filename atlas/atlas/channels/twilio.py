"""Twilio: a voice call, for CRITICAL only.

A phone call that is not urgent teaches the owner to ignore phone calls, so `voice`
is deliberately reached by CRITICAL routing and nothing else. Messages go over
Slack.

**There is no SMS channel here, on purpose.** A US 10DLC number cannot send
until an A2P campaign is approved, and that vetting requires a publicly hosted
privacy policy and terms page. Publishing those would widen a public surface
held to /webhooks/* and /healthz — to unblock a channel `ROUTING` never
reached anyway, because Slack precedes it at HIGH and wins. The channel was
removed rather than left registered and permanently undeliverable, which would
have read as available to anything asking.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote, urlencode

import httpx

from ..kernel import briefing
from ..kernel.comms import Outbound, call_brevity_line, register, shorten

_log = logging.getLogger("atlas.twilio")

API = "https://api.twilio.com/2010-04-01/Accounts"
# What one call opens with, at most: title and body, cut to what can be said
# before he has to answer.
SPEECH_MAX = 900


def decisions(msg: Outbound) -> list[int]:
    """The cards this page is about: its `approve:N` buttons, and nothing
    else -- an Acknowledge button is Slack's, never the phone's."""
    out: list[int] = []
    for button in msg.buttons:
        verdict, _, pid = str(button.value).partition(":")
        if verdict == "approve" and pid.isdigit() and int(pid) not in out:
            out.append(int(pid))
    return out[:5]


class _Base:
    """Credentials are read on every use, not captured at import.

    `register(VoiceChannel())` runs at import time, so anything read in
    `__init__` freezes whatever the environment happened to hold then. Tests
    that set an env var, and a process that loads `.env` after the module
    graph is imported, both saw a permanently unavailable channel.
    """

    @property
    def sid(self) -> str:
        return os.getenv("TWILIO_ACCOUNT_SID", "")

    @property
    def auth(self) -> str:
        return os.getenv("TWILIO_AUTH_TOKEN", "")

    @property
    def frm(self) -> str:
        return os.getenv("TWILIO_FROM", "")

    @property
    def to(self) -> str:
        return os.getenv("TWILIO_TO", "")

    async def available(self) -> bool:
        return all((self.sid, self.auth, self.frm, self.to))


class VoiceChannel(_Base):
    name = "voice"
    rich = False
    interactive = True

    async def available(self) -> bool:
        # `send` builds the TwiML callback URL from ATLAS_HOST. Without it the
        # URL is literally "https://None/..." and Twilio places a call that
        # fails to fetch instructions -- a silent CRITICAL page. Inheriting
        # _Base.available() reported this channel as ready.
        return await super().available() and bool(os.getenv("ATLAS_HOST"))

    async def send(self, msg: Outbound) -> str | None:
        """Ring him.

        The call is Twilio's own, and its opening is the phone line's
        (`/webhooks/twilio/voice`, in `api/main.py` and `api/phone.py`): the
        one decision, spoken by Fish Audio or, failing that, Twilio's
        `<Say>`, then the conversation -- read-back and "do it", by voice or
        keypad, bound to this call's SID. A worse voice is much better than
        a missed call, so nothing here waits on the voice.

        What is said is ATLAS's alert: the title and body go into the
        callback URL, which Twilio signs, and are escaped into the TwiML.
        They reach a model only through the call's history, quoted
        (invariant 4).
        """
        # G2 (reel audit): a 30-second, one-decision call when he's out. When
        # `home` is anything but `False` -- True, or unknown because the mesh
        # couldn't be read -- this is `None` and the call says it all
        # (invariant 6: never guess he is away). "Keep it short" is enforced
        # structurally: the BODY is shortened to one sentence before
        # assembly, not the assembled string after -- shortening
        # "Atlas alert. {title}. {body}" as a whole would spend the sentence
        # budget on the fixed preamble and the title, dropping the body
        # entirely (the one decision this call exists to open with).
        away_line = await call_brevity_line()
        body = shorten(msg.body, 1) if away_line else msg.body
        # The opening is composed here, before the phone rings: who is
        # calling, what is wrong in plain words (`kernel.briefing`). It
        # re-words the alert and adds nothing to it. The question -- the
        # third beat -- rides beside it and is asked only when a card is
        # pending on the call.
        speech = briefing.opening(msg.title, body)[:SPEECH_MAX]
        # The title alone is what a machine is told, after its beep.
        query: dict[str, str] = {"say": speech,
                                 "title": " ".join(msg.title.split())[:200]}
        pids = decisions(msg)
        if pids:
            query["pid"] = ",".join(str(pid) for pid in pids)
            # One card, one question. With several, the question stays
            # general: the phone never guesses which one "yes" would mean.
            query["ask"] = briefing.question(
                briefing.action_of(msg.title) if len(pids) == 1 else "")
        if away_line:
            query["away"] = "1"
        host = os.getenv("ATLAS_HOST")
        callback = (f"https://{host}/webhooks/twilio/voice?"
                    + urlencode(query, quote_via=quote))
        async with httpx.AsyncClient(timeout=15, auth=(self.sid, self.auth)) as c:
            r = await c.post(
                f"{API}/{self.sid}/Calls.json",
                data={"From": self.frm, "To": self.to,
                      # POST, so Twilio's own CallSid, Direction, From and To
                      # arrive in the signed form the opening reads.
                      "Url": callback, "Method": "POST",
                      # Answering-machine detection. DetectMessageEnd holds
                      # the opening until a greeting ends, and sends
                      # `AnsweredBy` with it and with the status callback: a
                      # machine is left one line that approves nothing
                      # (invariant 5: a machine answering is not consent).
                      "MachineDetection": "DetectMessageEnd",
                      # Without this ATLAS places calls and never learns
                      # whether they were answered, so "three declines and he
                      # backs off" could never fire — the rule was written and
                      # had no caller.
                      "StatusCallback":
                          f"https://{host}/webhooks/twilio/voice/status",
                      "StatusCallbackMethod": "POST",
                      "StatusCallbackEvent": "completed"})
            r.raise_for_status()
            sid = r.json().get("sid")
            _log.info("rang him (%s)", sid or "no sid returned")
            # What this call is about, so an unanswered one can be rung
            # again while there is still something to decide (kernel/rering).
            from ..kernel import callrecord, rering
            rering.placed(sid, msg)
            await callrecord.placed(
                sid, title=" ".join(msg.title.split()), opening=speech,
                ask=query.get("ask", ""), pids=pids, incident_key=msg.incident_key,
                away=bool(away_line), night=briefing.night())
            return sid


register(VoiceChannel())
