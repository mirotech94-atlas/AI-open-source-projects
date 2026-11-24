"""A call ATLAS placed to a lead who asked for one (`agency.call_lead`).

The business line (`reception.py`) answers leads who ring in; this is the
other direction, and it exists only because the lead asked
(`agency.consent`). Its own routes -- `/webhooks/twilio/agency/voice`,
`/turn`, `/status` in `main.py` -- so nothing here can approve a card by
keypad, and an unanswered lead call never counts as Miro declining.

**Admission.** A webhook is heard only when its `c` (the `agency_contacts`
row) and `t` (the per-call secret, compared against the stored SHA-256)
match, the row is a call still open, Twilio's `To` is the number the lead
consented on, and the CallSid is the one the row recorded. Otherwise 403.

**The opening is the identification**, built in code (`consent.disclosure`)
and spoken first (Miro, 2026-10-01): "Hi, this is Miro's assistant from
ATLAS Agency, calling about <business>. We saw your website needed help /
you didn't have a website, so we built you a free preview at <url>." --
then how to stop the calls and the number to call back on (47 CFR
64.1200(b)), then "Do you have five minutes? I can put you through to the
owner, Miro." The opening does not say "AI"; asked whether it is a bot, an
AI or a recording (`reception.AI_QUESTION`), the line answers plainly that
it is Miro's AI assistant (`DISCLOSE_AGAIN`), before any model, and never
claims to be Miro or a person.

**Yes to the owner is a warm transfer** (`transfer_twiml`): the call
bridges to Miro's phone (AGENCY_ESCALATION_PHONE, else TWILIO_TO) with
Twilio `<Dial>`, a short whisper to him first ("<business>, from the
preview call", `whisper_twiml`, on its own signed route) so he knows who is
on the line before it connects. If he does not pick up within
`DIAL_TIMEOUT_S` (25 s), the Dial's action route (`dial_result`) tells the
lead Miro will call back today, records the request on the lead, and
posts "CALL BACK: <business> <phone> agreed to talk — <time>" to Slack as
an alert. "Yes" and "no" right after that question are matched in code
(`_YES`, `_NO`); the model's `transfer` and `decline` moves cover the
rest. Not interested: thanked, recorded, consent revoked and the number
suppressed (`consent.opt_out`), so nothing dials them again. Every
outcome is one Slack summary (`finish`).

**Turn-taking.** Each spoken line sits INSIDE `<Gather input="speech">`
(`listen`), so the lead's speech interrupts it at once -- Twilio's
barge-in for a nested `<Say>`/`<Play>` -- and `speechTimeout="auto"`
ends the turn on their pause. The assistant speaks only on a FINAL
result: a Gather posts to its `action` once the utterance has ended, and
partial results (`partialResultCallback`, not set) never start a line.
`actionOnEmptyResult="true"` is the silence fallback. Lines are one or
two short sentences (`REPLY_MAX`).

**What the model may do is a closed set of moves** (`MOVES`), chosen as JSON
and checked in code:

  * ``talk`` -- answer from the lead brief, handle an objection, negotiate.
    Its words pass the same checks a written reply does: every figure within
    the price rules for this moment (`pricing.price_refusal`), no promise
    the build does not keep, no false urgency, never "I'm Miro" or "I'm a
    person" -- plus no delivery date. A line that fails is replaced with
    "Let me have Miro confirm that one for you in writing."
  * ``offer_times`` -- they'd like to talk to Miro: 2-3 real free
    half-hours read live from his calendar (`agency.meetings`), spoken by
    code. Calendar unreadable: "Miro will text you some times" -- never an
    invented slot.
  * ``pick_time`` -- they chose one of the offered times (by number, from
    the closed list): `agency.hold_meeting` (Tier 1) runs through the
    kernel's unattended door (`kernel.unattended.run_tier1`, the executor
    behind it), then the `agency.send_invite` card (Tier 2 since
    2026-10-27), off the turn; nothing is sent until Miro approves.
    A lead with no email on record gets no invite; the summary says Miro
    books it.
  * ``send_link`` -- the site link: texting leads is off (Miro,
    2026-09-30), so "Miro will send you the link himself".
  * ``agree`` -- the lead said yes at an allowed price: `agency.send_agreement`
    becomes a card (Tier 2 since 2026-10-27): the agreement text written by
    code and Miro's own payment link for exactly that price, dry-run now and
    sent only when he approves; the line says "Miro signs off on it first".
  * ``end`` -- say goodbye.

Anything else is "Let me have Miro get back to you on that." Before any
model: a STOP/"don't call" revokes consent and ends the call; "are you a
bot?" gets the disclosure word for word; a complaint, a legal threat or a
request outside the rules (a price below the floor, custom terms, a refund)
rings Miro through `agency.escalate`, the only path that does. A system
failure mid-call does the same.

The lead's words are untrusted (invariant 4): quoted to the model inside
`wrap_untrusted()`, stored as received, and posted to Slack with the moves
and cards when the call ends.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from ..kernel.permissions import wrap_untrusted

log = logging.getLogger("atlas.lead_call")

MOVES = ("talk", "offer_times", "pick_time", "send_link", "agree", "transfer",
         "decline", "end")
ASK_TIME = "Is now an OK time for a quick chat?"
ASK_OWNER = "Do you have five minutes? I can put you through to the owner, Miro."
STOP_ANYTIME = "Just say stop anytime and we won't call again."
DISCLOSE_AGAIN = ("Yep, I'm an AI, Miro's assistant at ATLAS Agency. I pass "
                  "everything on to him, and he'll follow up himself. What can I "
                  "help with?")
CONNECTING = "Great, one moment while I put you through to Miro."
AFTER_TRANSFER = "Thanks again, bye now."
MIRO_CALLBACK = ("Miro isn't free right now, but he'll call you back today on this "
                 "number. Thanks for your time, bye now.")
DECLINED = "No problem, thanks for your time. We won't call again. Bye now."
DIAL_TIMEOUT_S = 25
HOLD = "Let me have Miro get back to you on that."
CONFIRM_IN_WRITING = "Let me have Miro confirm that one for you in writing."
LINK_BY_EMAIL = "Miro will send you the link himself."
NO_TIMES = "I can't see Miro's calendar right now, so he'll text you some times."
NONE_FREE = "Miro's pretty booked this week, so he'll text you some times."
HELD_LINE = ("Perfect, you're down for {slot}. Miro will send the calendar invite "
             "shortly.")
TAKEN = "Ah, that one just went. How about {slots}?"
AGREED_LINE = ("Great. I'll send over the agreement and the payment link. Miro "
               "signs off on it first, so you'll have it shortly.")
OPTED_OUT = "Understood. We won't call again. Sorry to bother you, bye now."
ESCALATED = "I'm sorry about that. I'll have Miro call you back himself today."
OUTSIDE = "That one's for Miro himself. I'll have him get back to you today."
GOODBYE = "Thanks for your time. Miro will be in touch. Bye for now."
UNSURE = "Sorry, I didn't catch that. Could you say it again?"
FAILED = "Sorry, I'm having trouble on my end. Miro will call you back himself."
REPLY_MAX = 260
MAX_TURNS = 16
MAX_CALL_S = 10 * 60
MODEL_BUDGET_S = 8.0
CALL_TTL_S = 2 * 3600

_DATE_PROMISE = re.compile(
    r"\b(?:i|we|miro)\s+(?:can|will|'ll)\s+(?:have\s+it|deliver|finish|launch|get\s+it"
    r"\s+(?:done|live))\s+(?:by|on|in|tomorrow|today|this|next)\b", re.IGNORECASE)

SYSTEM = (
    "You are on a phone call as Miro's assistant from {agency}. You are an AI "
    "assistant: if asked whether you are a person, a bot or an AI, say "
    "plainly that you are Miro's AI assistant; never claim to be Miro or a "
    "person. The lead asked for this call, and you opened by offering to put "
    "them through to the owner, Miro. Speak like a relaxed, friendly person "
    "on the phone: one or two short sentences, contractions, a brief natural "
    "acknowledgement when it fits (\"Got it.\", \"Sure.\", \"Makes sense.\"), "
    "no lists, no markdown, nothing robotic. Let them finish; never talk over "
    "them.\n"
    "Answer with a JSON object only: "
    "{{\"move\": one of {moves}, \"say\": \"your next spoken line\", "
    "\"price\": a whole number only for agree, \"choice\": 1, 2 or 3 only "
    "for pick_time}}.\n"
    "- talk: answer from the LEAD facts, handle objections, negotiate within "
    "the price rules.\n"
    "- transfer: they said yes to speaking with Miro now; leave say empty.\n"
    "- decline: they are not interested; leave say empty.\n"
    "- offer_times: they're interested but would rather talk to Miro another "
    "time; the times are read from his calendar for you, so leave say empty.\n"
    "- pick_time: they chose one of the TIMES OFFERED; give its number as "
    "choice.\n"
    "- send_link: they want the site link by text.\n"
    "- agree: they clearly said yes at one of the allowed prices.\n"
    "- end: they want to go; say goodbye.\n"
    "Never promise a delivery date, never agree to custom terms or a refund.\n\n"
    "{playbook}\n\n{prices}\n\n{deliverables}"
)

# Right after the owner question, a plain yes or no is matched in code; a
# flat "not interested" anywhere on the call ends it the same way.
_YES = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|alright|go ahead|please|put me "
    r"through|that works|sounds good|fine|absolutely|of course)\b", re.IGNORECASE)
_NO = re.compile(r"^\W*(?:no|nope|nah|not now|not today)\b", re.IGNORECASE)
_NOT_INTERESTED = re.compile(
    r"\bnot interested\b|\bno thanks?\b|\bno thank you\b|\bdon'?t need\b|"
    r"\bnot for us\b|\bwe'?re good\b|\bwe are good\b", re.IGNORECASE)


def _digits(number: str) -> str:
    return "".join(ch for ch in str(number or "") if ch.isdigit())


@dataclass
class LeadCall:
    contact_id: int
    lead: dict[str, Any]
    phone: str
    opening: str
    token_sha: str
    started: float = field(default_factory=time.monotonic)
    turns: list[tuple[str, str]] = field(default_factory=list)
    quiet: int = 0
    notes: list[str] = field(default_factory=list)
    cards: list[str] = field(default_factory=list)
    machine: bool = False
    offered: list = field(default_factory=list)       # datetimes, spoken in order
    meeting: str = ""                                 # what was booked, for Slack
    preview: str = ""                                 # the preview line, built once
    transferring: bool = False                        # the next TwiML is the Dial
    outcome: str = ""                                 # agreed|transferred|callback|declined

    @property
    def lead_id(self) -> int:
        return int(self.lead["id"])

    def their_words(self) -> list[str]:
        return [text for who, text in self.turns if who == "lead"]


_CALLS: dict[str, LeadCall] = {}


def _sweep() -> None:
    now = time.monotonic()
    for sid, call in list(_CALLS.items()):
        if now - call.started > CALL_TTL_S:
            _CALLS.pop(sid, None)


def _sha(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


async def admit(contact_id: str, token: str, call_sid: str, to: str) -> LeadCall | None:
    """The call this webhook is about, or None when anything disagrees."""
    _sweep()
    if not (contact_id.isdigit() and token and call_sid):
        return None
    known = _CALLS.get(call_sid)
    if known is not None:
        ok = (known.contact_id == int(contact_id)
              and hmac.compare_digest(known.token_sha, _sha(token))
              and _digits(to)[-10:] == _digits(known.phone)[-10:])
        return known if ok else None
    from ..modules.agency import _store
    row = await _store.get_contact(int(contact_id))
    if not row or row.get("channel") != "call" or not row.get("token_sha"):
        return None
    if not hmac.compare_digest(str(row["token_sha"]), _sha(token)):
        return None
    if row.get("ended_at") is not None or row.get("status") not in ("placed", "answered"):
        return None
    if _digits(to)[-10:] != _digits(str(row["phone"]))[-10:]:
        return None
    if row.get("sid") and row["sid"] != call_sid:
        return None
    lead = await _store.get_lead(int(row["lead_id"]))
    if lead is None:
        return None
    call = LeadCall(int(row["id"]), lead, str(row["phone"]), str(row["opening"]),
                    str(row["token_sha"]))
    _CALLS[call_sid] = call
    return call


# ------------------------------------------------------------------ TwiML

def listen(text: str, clip_token: str | None, host: str, call: LeadCall,
           token: str, turn: int) -> str:
    """One spoken line, nested INSIDE the speech Gather: the lead's speech
    interrupts it (barge-in), `speechTimeout="auto"` ends their turn on a
    pause, and the next line is spoken only on the FINAL result the Gather
    posts to `action` -- no `partialResultCallback`, so a half-sentence
    never starts a reply."""
    from .phone import _recognition, _speak
    action = f"/webhooks/twilio/agency/turn?c={call.contact_id}&t={token}&turn={turn}"
    return ("<Response>"
            f'<Gather input="speech" speechTimeout="auto" {_recognition()} '
            f'action="{xml_escape(action)}" method="POST" actionOnEmptyResult="true">'
            + _speak(text, clip_token, host) + "</Gather><Hangup/></Response>")


# ------------------------------------------------------------ the transfer

def escalation_number() -> str | None:
    """Miro's own phone, E.164: AGENCY_ESCALATION_PHONE, else TWILIO_TO (the
    number ATLAS already rings him on). None when neither is set."""
    from ..modules.agency import consent
    raw = (os.getenv("AGENCY_ESCALATION_PHONE", "").strip()
           or os.getenv("TWILIO_TO", "").strip())
    return consent.normalize(raw) if raw else None


def whisper_line(call: LeadCall) -> str:
    """What Miro hears before the bridge, and the lead does not."""
    who = " ".join(str(call.lead.get("name") or "a lead").split())[:80]
    return f"{who}, from the preview call."


def whisper_twiml(call: LeadCall, clip_token: str | None, host: str) -> str:
    """The whisper leg's TwiML: the line, then (no Hangup) the bridge."""
    from .phone import _speak
    return "<Response>" + _speak(whisper_line(call), clip_token, host) + "</Response>"


def transfer_twiml(call: LeadCall, clip_token: str | None, host: str,
                   token: str) -> str:
    """Bridge the lead to Miro: `<Dial>` his number with the whisper on the
    `<Number>`'s own signed URL, the business line as caller id, and the
    Dial's outcome posted to the dial route, which speaks the last line."""
    from .phone import _speak
    number = escalation_number() or ""
    query = f"c={call.contact_id}&t={token}"
    action = f"/webhooks/twilio/agency/dial?{query}"
    whisper = f"https://{host}/webhooks/twilio/agency/whisper?{query}"
    caller = os.getenv("TWILIO_FROM", "").strip()
    return ("<Response>" + _speak(CONNECTING, clip_token, host)
            + f'<Dial action="{xml_escape(action)}" method="POST" '
            f'timeout="{DIAL_TIMEOUT_S}" callerId="{xml_escape(caller)}">'
            f'<Number url="{xml_escape(whisper)}" method="POST">{xml_escape(number)}'
            "</Number></Dial></Response>")


async def admit_whisper(contact_id: str, token: str, parent_sid: str,
                        to: str) -> LeadCall | None:
    """The whisper leg is a child call: its CallSid is new, its `To` is
    Miro's number. It is admitted on its ParentCallSid being a lead call
    this process is holding, the secret matching, and `To` being the
    escalation number -- never any other number."""
    if not (contact_id.isdigit() and token and parent_sid):
        return None
    known = _CALLS.get(parent_sid)
    if known is None or not known.transferring:
        return None
    number = escalation_number()
    ok = (known.contact_id == int(contact_id)
          and hmac.compare_digest(known.token_sha, _sha(token))
          and number is not None and _digits(to)[-10:] == _digits(number)[-10:])
    return known if ok else None


def _lead_time() -> str:
    from datetime import UTC, datetime

    from ..modules.agency import consent
    return datetime.now(UTC).astimezone(consent.lead_tz()).strftime("%H:%M %Z")


async def _mark(call: LeadCall, key: str, **facts: Any) -> None:
    """A fact on the lead's own row (`agency_leads.meta`); never raises."""
    from datetime import UTC, datetime

    from ..modules.agency import _store
    try:
        await _store.set_lead_meta(call.lead_id, key, {
            "at": datetime.now(UTC).isoformat(), **facts})
    except Exception as exc:  # noqa: BLE001
        log.warning("lead call: %s not recorded: %s", key, type(exc).__name__)


async def _callback_alert(call: LeadCall) -> None:
    from ..kernel.comms import Outbound, notify
    from ..kernel.contracts import Severity
    name = str(call.lead.get("name") or f"lead {call.lead_id}")
    await notify(Outbound(
        title=f"CALL BACK: {name} {call.phone} agreed to talk — {_lead_time()}",
        body=(f"{name} said yes to speaking with you on ATLAS's call and you did "
              f"not pick up within {DIAL_TIMEOUT_S} s. They were told you will "
              f"call back today on {call.phone}."),
        severity=Severity.HIGH, topic="agency", kind="alert"))


async def _callback(call: LeadCall, call_sid: str) -> str:
    """Miro did not answer (or cannot be dialled): the lead is told he will
    call back today, the request is on the lead, and Slack is alerted."""
    from ..kernel.background import spawn
    call.outcome = "callback"
    call.transferring = False
    await _mark(call, "callback_requested", phone=call.phone, sid=call_sid)
    call.notes.append(f"Agreed to talk to you; you did not answer the transfer, so "
                      f"they were told you will call back today on {call.phone}.")
    spawn(_callback_alert(call), "call-back alert")
    return MIRO_CALLBACK


async def _transfer(call: LeadCall, call_sid: str) -> tuple[str, bool]:
    """The lead said yes to the owner: record it, then bridge (or, with no
    number to bridge to, the call-back path)."""
    call.outcome = "agreed"
    await _mark(call, "owner_call", agreed=True, sid=call_sid)
    if escalation_number() is None:
        call.notes.append("No AGENCY_ESCALATION_PHONE or TWILIO_TO: no transfer.")
        return await _callback(call, call_sid), True
    call.notes.append("Agreed to talk to you: transferred to your phone.")
    call.transferring = True
    return CONNECTING, False


async def _decline(call: LeadCall, call_sid: str) -> tuple[str, bool]:
    """Not interested: thanked, recorded, and never dialled again."""
    from ..modules.agency import consent
    call.outcome = "declined"
    await _mark(call, "owner_call", agreed=False, sid=call_sid)
    await consent.opt_out(call.lead_id, phone=call.phone,
                          why=f"not interested, said on ATLAS's call ({call_sid})")
    call.notes.append("Not interested: recorded, consent revoked, number suppressed.")
    return DECLINED, True


async def dial_result(call: LeadCall, call_sid: str, status: str,
                      duration: str = "") -> tuple[str, bool]:
    """What the lead hears once the Dial ends. `completed` means Miro
    answered and the two talked; anything else is the call-back path."""
    status = (status or "").strip().lower()
    call.transferring = False
    if status == "completed":
        call.outcome = "transferred"
        call.notes.append(f"Put through to you ({duration or '?'} s on the line).")
        text = AFTER_TRANSFER
    else:
        text = await _callback(call, call_sid)
        call.notes.append(f"The transfer ended {status or 'without an answer'}.")
    call.turns.append(("atlas", text))
    return text, True


def hang_up(text: str, clip_token: str | None, host: str) -> str:
    from .phone import closing
    return closing(text, clip_token, host)


def spoken_number(number: str) -> str:
    """A US number the way a person reads it out: 718, 555, 0000."""
    digits = _digits(number)[-10:]
    if len(digits) != 10:
        return ""
    return f"{digits[:3]}, {digits[3:6]}, {digits[6:]}"


def callback_line() -> str:
    """47 CFR 64.1200(b)(2): an artificial-voice call states a number the
    business can be reached on. TWILIO_FROM, the number the call came from."""
    number = spoken_number(os.getenv("TWILIO_FROM", ""))
    return f"You can reach us back at {number}." if number else ""


def spoken_url(url: str) -> str:
    """https://agency.storefront.test/preview/joes-plumbing-7/ ->
    agency dot storefront dot com slash preview slash joes-plumbing-7"""
    rest = url.split("://", 1)[-1].split("?", 1)[0].split("#", 1)[0].strip("/")
    host, _, path = rest.partition("/")
    parts = [host.replace(".", " dot ")]
    parts += [seg.replace(".", " dot ") for seg in path.split("/") if seg]
    return " slash ".join(parts)


def preview_line(lead: dict[str, Any], links: dict[str, Any]) -> str:
    """Miro's wording (2026-10-01). Only a lead with a website on record
    hears that we saw it; a business with none hears that it had none."""
    from ..modules.agency.outreach import preview_url
    url = preview_url(links)
    where = f"at {spoken_url(url)}" if url else "for you; Miro will send you the link"
    if lead.get("website"):
        return f"We saw your website needed help, so we built you a free preview {where}."
    return f"You didn't have a website, so we built you one, a free preview {where}."


def opening_line(call: LeadCall) -> str:
    """The identification first, the preview, then how to stop the calls
    (64.1200(b)(3): an opt-out announced right after the identification)
    and the callback number, then the offer of the owner."""
    parts = [call.opening, call.preview, STOP_ANYTIME, callback_line(), ASK_OWNER]
    return " ".join(p for p in parts if p)


def voicemail_line(call: LeadCall) -> str:
    from ..modules.agency.outreach import agency_sender
    email = agency_sender().replace("@", " at ").replace(".", " dot ")
    parts = [call.opening,
             ("Sorry I missed you. Miro will follow up by email, "
              f"or you can write to him at {email}."),
             callback_line(),
             "If you'd rather we didn't call again, just tell us and we won't."]
    return " ".join(p for p in parts if p)


async def opening(call: LeadCall, call_sid: str, answered_by: str, host: str,
                  token: str) -> str:
    from ..modules.agency import _store
    from . import phone
    await _store.set_contact(call.contact_id, sid=call_sid)
    if phone.is_machine(answered_by):
        call.machine = True
        line = voicemail_line(call)
        call.turns.append(("atlas", line))
        return hang_up(line, await phone.clip(line), host)
    if not call.preview:
        # The link is spoken only once Miro approved the email that carries
        # it (modules/agency/preview_card.py); before that: "Miro will send".
        from ..modules.agency.preview_card import shared
        links = (await _store.lead_links(call.lead_id)
                 if await shared(call.lead_id) else {})
        call.preview = preview_line(call.lead, links)
    line = opening_line(call)
    call.turns.append(("atlas", line))
    return listen(line, await phone.clip(line), host, call, token, 1)


# ------------------------------------------------------------------ turns

def _clean(text: str) -> str:
    text = " ".join(str(text or "").replace("*", "").split())
    if len(text) > REPLY_MAX:
        cut = text[:REPLY_MAX]
        text = cut[: max(cut.rfind(". "), cut.rfind("? ")) + 1] or cut
    return text


def parse_move(raw: str) -> dict[str, Any]:
    """The model's JSON, or a `talk` move with no words (which becomes HOLD)."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    move = str(data.get("move") or "").strip().lower()
    return {"move": move if move in MOVES else "", "say": _clean(data.get("say") or ""),
            "price": data.get("price"), "choice": data.get("choice")}


async def _propose(action: str, params: dict[str, Any], reason: str,
                   evidence: dict[str, Any], key: str) -> str | None:
    """A card, never a send. Returns the card id as text, or None."""
    from ..kernel.approvals import Unrunnable, propose
    try:
        pid = await propose(action, params, reason, evidence=evidence,
                            dedup_key=key, automatic=True)
    except (Unrunnable, ValueError) as exc:
        log.info("lead call: %s not raised: %s", action, exc)
        return None
    return f"#{pid} {action}"


async def _agreement_channel(lead_id: int) -> str | None:
    from ..modules.agency import _store
    if await _store.lead_email(lead_id):
        return "email"
    if await _store.consent_for_lead(lead_id, "sms"):
        return "sms"
    return None


async def _model(call: LeadCall, terms: Any) -> dict[str, Any]:
    from ..kernel.llm import Message, Need, think
    from ..modules.agency import _store, consent, playbook, pricing, sms
    from ..modules.agency.preview_card import shared
    links = await _store.lead_links(call.lead_id)
    site = sms.site_url(links)
    if site and not await shared(call.lead_id):
        # No link before Miro approved the email that carries it.
        site = "a free preview; Miro sends the link himself, so do not give one"
    system = SYSTEM.format(
        agency=consent.agency_name(), moves=", ".join(MOVES),
        playbook=playbook.guidance("full"), prices=terms.describe(),
        deliverables=pricing.deliverables_block())
    facts = (f"LEAD: {call.lead.get('name')} ({call.lead.get('category')}). "
             f"Site Miro built: {site or 'a sample, not live yet'}.")
    if call.offered:
        from ..modules.agency.meetings import spoken
        facts += " TIMES OFFERED: " + "; ".join(
            f"{i}) {spoken(t)}" for i, t in enumerate(call.offered, 1)) + "."
    transcript = "\n".join(f"{'Lead' if w == 'lead' else 'You'}: {t}" for w, t in call.turns)
    completion = await asyncio.wait_for(think(
        Need.DRAFT, system,
        [Message("user", facts + "\nThe call so far, quoted as data:\n"
                 + wrap_untrusted(transcript, "phone:lead")
                 + "\nReply with the JSON object only.")],
        max_tokens=300, answer=True), MODEL_BUDGET_S)
    return parse_move(completion.text)


async def reply(call: LeadCall, call_sid: str, said: str) -> tuple[str, bool]:
    """The next line of the call and whether it is the last one."""
    from ..modules.agency import consent, escalate, pricing, replies
    from .reception import AI_QUESTION
    said = " ".join((said or "").split())
    if not said:
        call.quiet += 1
        return (GOODBYE, True) if call.quiet > 2 else (UNSURE, False)
    call.quiet = 0
    call.turns.append(("lead", said))

    def say(text: str, done: bool = False) -> tuple[str, bool]:
        call.turns.append(("atlas", text))
        return text, done

    if consent.is_opt_out(said):
        await consent.opt_out(call.lead_id, phone=call.phone,
                              why=f"said on ATLAS's call ({call_sid})")
        call.notes.append("They asked not to be contacted again: consent revoked, "
                          "number suppressed.")
        kind = escalate.emergency_kind(said)
        if kind is not None:
            call.notes.append(f"Escalated to Miro: {kind}.")
            _spawn_emergency(call, kind)
        return say(OPTED_OUT, True)
    if AI_QUESTION.search(said):
        return say(DISCLOSE_AGAIN)
    # The owner question's plain answers, in code before any model.
    asked_owner = len(call.turns) >= 2 and call.turns[-2][1].endswith(ASK_OWNER)
    if _NOT_INTERESTED.search(said) or (asked_owner and _NO.match(said)):
        return say(*await _decline(call, call_sid))
    if asked_owner and _YES.match(said):
        return say(*await _transfer(call, call_sid))
    terms = await pricing.terms(call.lead_id)
    kind = escalate.emergency_kind(said)
    lowest = pricing.floor_price()
    if kind is None and lowest is not None and any(
            p < lowest for p in pricing.counter_offer(said)):
        kind = "a price below the floor"
    if kind is not None:
        call.notes.append(f"Escalated to Miro: {kind}.")
        _spawn_emergency(call, kind)
        legal = kind.startswith("a complaint")
        return say(ESCALATED if legal else OUTSIDE, legal)
    if (len(call.their_words()) >= MAX_TURNS
            or time.monotonic() - call.started > MAX_CALL_S):
        return say(GOODBYE, True)

    try:
        move = await _model(call, terms)
    except TimeoutError:
        return say(HOLD)
    except Exception as exc:  # noqa: BLE001
        log.warning("lead call: model failed: %s", type(exc).__name__)
        call.notes.append("The model failed mid-call.")
        _spawn_emergency(call, "a system failure on a call")
        return say(FAILED, True)

    text = move["say"]
    if move["move"] == "transfer":
        return say(*await _transfer(call, call_sid))
    if move["move"] == "decline":
        return say(*await _decline(call, call_sid))
    if move["move"] == "send_link":
        call.notes.append("They asked for the site link: send it yourself "
                          "(texting leads is off).")
        return say(LINK_BY_EMAIL)
    if move["move"] == "offer_times":
        return say(await _offer(call))
    if move["move"] == "pick_time":
        return say(await _book(call, call_sid, move.get("choice")))
    if move["move"] == "agree":
        try:
            price = int(move["price"])
        except (TypeError, ValueError):
            price = -1
        channel = await _agreement_channel(call.lead_id)
        if price not in terms.allowed():
            call.notes.append(f"They wanted to agree at a price outside the rules "
                              f"({move['price']!r}).")
            _spawn_emergency(call, "a request outside the rules")
            return say(OUTSIDE)
        if channel is None:
            call.notes.append(f"Agreed at ${price}, but there is no email or text "
                              "consent to send the agreement to.")
            return say(AGREED_LINE)
        return say(await _agree(call, call_sid, price, channel))
    if not move["move"] or not text:
        return say(HOLD)
    why = replies.refusal(text, terms)
    spoken = [p for p in pricing.spoken_prices(text)
              if p not in terms.allowed()]
    if why is None and spoken:
        why = f"a spoken figure outside the price rules ({spoken[0]})"
    if why is not None or _DATE_PROMISE.search(text):
        call.notes.append(f"A line was held back: {why or 'a delivery date'}.")
        return say(CONFIRM_IN_WRITING)
    return say(text, move["move"] == "end")


async def _agree(call: LeadCall, call_sid: str, price: int, channel: str) -> str:
    """The agreement is a card (Tier 2, six agents 2026-10-27): drafted and
    checked by the dry run, sent only when Miro approves. The spoken line
    never says it was sent."""
    params = {"lead_id": call.lead_id, "price": price, "channel": channel}
    reason = f"the lead agreed at ${price} on a call ({call_sid})"
    held = await _preview_held("agency.send_agreement", params)
    card = await _propose("agency.send_agreement", params,
                          f"Held for you: {held}\n{reason}" if held else reason,
                          {"agreed_on_call": call_sid, "price": price, "held": held},
                          f"lead-call:{call_sid}:agree")
    call.notes.append(f"Agreed at ${price}; the agreement waits for your approval "
                      f"(card {card or 'could not be raised'})"
                      + (f"; held: {held}." if held else "."))
    if card:
        call.cards.append(card)
    return AGREED_LINE


async def _preview_held(action: str, params: dict[str, Any]) -> str | None:
    """The dry run's refusal for the card, or None when it would go. An
    autosend hold (HELD:) is what the card itself answers, so it is not one."""
    from ..kernel import executor
    try:
        result = await executor.preview(action, params)
    except Exception as exc:  # noqa: BLE001
        return f"the dry run could not run ({type(exc).__name__})"
    from ..modules.agency.outreach import HELD
    if result.ok or str(result.detail).lower().startswith(HELD):
        return None
    return _held_reason(str(result.detail))


def _held_reason(detail: str) -> str:
    """The executor's refusal without its prefixes, for the card."""
    for prefix in ("dry run refused: ", "refused: "):
        detail = detail.removeprefix(prefix)
    return detail


def _say_times(slots: list) -> str:
    from ..modules.agency.meetings import spoken
    words = [spoken(t) for t in slots]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]


async def _offer(call: LeadCall) -> str:
    """Two or three real open slots, read now. None readable: say so."""
    from ..modules.agency import meetings
    slots, why = await meetings.free_times()
    if slots is None:
        call.notes.append(f"Wanted a call with you; no times offered: {why}.")
        return NO_TIMES
    call.offered = meetings.choose(slots)
    if not call.offered:
        call.notes.append("Wanted a call with you; no free slot in business hours "
                          "this week.")
        return NONE_FREE
    return (f"Miro could do {_say_times(call.offered)}. "
            f"{'Which' if len(call.offered) > 1 else 'Does that'} work for you?")


async def _book(call: LeadCall, call_sid: str, choice: Any) -> str:
    """Hold the chosen slot through the executor (Tier 1), then raise the
    invite card (Tier 2). Only a slot from the list just offered."""
    try:
        index = int(choice) - 1
    except (TypeError, ValueError):
        index = -1
    if not call.offered:
        return HOLD
    if not 0 <= index < len(call.offered):
        return f"Sorry, which one? {_say_times(call.offered)}?"
    from ..kernel.unattended import run_tier1
    from ..modules.agency import meetings
    slot = call.offered[index]
    result = await run_tier1(
        "agency.hold_meeting",
        {"lead_id": call.lead_id, "start": slot.isoformat(), "call_sid": call_sid},
        f"the lead picked {meetings.spoken(slot)} on a call ({call_sid})")
    if not result.ok:
        if "not an open" in str(result.detail):
            fresh, _why = await meetings.free_times()
            call.offered = meetings.choose(fresh or [])
            if call.offered:
                return TAKEN.format(slots=_say_times(call.offered))
        call.notes.append(f"They picked {meetings.spoken(slot)}; the hold was not "
                          f"made ({result.detail}). Book it yourself.")
        return "Great, Miro will confirm that time with you himself."
    evidence = result.evidence or {}
    call.meeting = f"{meetings.spoken(slot)} (held on your calendar)"
    if evidence.get("email") and evidence.get("meeting_id"):
        # The invite (Google's email) goes off the turn: Twilio abandons a
        # webhook at fifteen seconds, and the lead is waiting on this line.
        from ..kernel.background import spawn
        spawn(_invite(call, call_sid, int(evidence["meeting_id"]),
                      str(evidence["email"]), slot), "meeting invite")
    else:
        call.notes.append(f"Booked {meetings.spoken(slot)}, but there is no email "
                          "on record: no invite. Send the invite yourself.")
    call.offered = []
    return HELD_LINE.format(slot=meetings.spoken(slot))


async def _invite(call: LeadCall, call_sid: str, meeting_id: int, email: str,
                  slot: Any) -> None:
    """The invite is a card (Tier 2, six agents 2026-10-27); it goes to the
    lead only when Miro approves."""
    from ..modules.agency import meetings
    reason = f"invite the lead to the call they booked ({call_sid})"
    params = {"meeting_id": meeting_id}
    held = await _preview_held("agency.send_invite", params)
    card = await _propose("agency.send_invite", params,
                          f"Held for you: {held}\n{reason}" if held else reason,
                          {"to": email, "start": slot.isoformat(), "held": held},
                          f"lead-call:{call_sid}:invite")
    if card:
        call.cards.append(card)
    call.notes.append(f"Booked {meetings.spoken(slot)}; the invite to {email} waits "
                      f"for your approval (card {card or 'could not be raised'})"
                      + (f"; held: {held}." if held else "."))


# A turn's whole budget, under Twilio's fifteen seconds with room for the
# voice: past it the lead hears "let me have Miro get back to you", never
# Twilio's application error. Work that must not be cut short mid-write --
# the calendar hold -- is shielded and finishes in the background.
TURN_BUDGET_S = 11.0


async def reply_in_time(call: LeadCall, call_sid: str, said: str) -> tuple[str, bool]:
    task = asyncio.ensure_future(reply(call, call_sid, said))
    try:
        return await asyncio.wait_for(asyncio.shield(task), TURN_BUDGET_S)
    except TimeoutError:
        from ..kernel.background import track
        track(task, "a slow lead-call turn")
        call.notes.append("A turn ran past its time budget; the lead was told "
                          "Miro would follow up.")
        call.turns.append(("atlas", HOLD))
        return HOLD, False


def _spawn_emergency(call: LeadCall, kind: str) -> None:
    from ..kernel.background import spawn
    from ..modules.agency import escalate
    body = (f"On ATLAS's call to {call.lead.get('name')} ({call.phone}).\n"
            + "\n".join(f"{'Them' if w == 'lead' else 'ATLAS'}: {t}"
                        for w, t in call.turns[-6:]))
    spawn(escalate.emergency(str(call.lead.get("name") or ""), kind, body),
          "agency emergency")


# ------------------------------------------------------------------ finish

async def finish(call_sid: str, *, contact_id: int | None = None,
                 status: str = "", answered_by: str = "") -> None:
    """File the call and post it to Slack. Never raises."""
    from ..modules.agency import _store, leads
    from . import phone
    call = _CALLS.pop(call_sid, None)
    heard = bool(call and call.their_words())
    outcome = "answered" if heard else "unanswered"
    if status and status != "completed":
        outcome = "unanswered"
    if phone.is_machine(answered_by) or (call and call.machine):
        outcome = "unanswered"
    cid = call.contact_id if call else contact_id
    try:
        if cid is not None:
            await _store.set_contact(
                cid, status=outcome, sid=call_sid or None, ended=True,
                transcript=[{"who": w, "text": t} for w, t in call.turns] if call else None)
        if call and heard:
            said = "\n".join(call.their_words())
            mid = await _store.insert_lead_message(call.lead_id, "call", call_sid,
                                                   call.phone, "", said)
            if mid is not None:
                await _store.set_message_intent(mid, await leads.classify(said))
    except Exception as exc:  # noqa: BLE001
        log.warning("lead call: not filed: %s", type(exc).__name__)
    try:
        from ..kernel.comms import Outbound, notify
        name = (call.lead.get("name") if call else None) or f"contact {cid}"
        verdict = {"agreed": "agreed to talk to you",
                   "transferred": "put through to you",
                   "callback": "agreed to talk; you did not answer, call back today",
                   "declined": "not interested"}.get(call.outcome if call else "")
        if verdict:
            outcome = f"{outcome} — {verdict}"
        if call and heard:
            lines = "\n".join(f"{'Them' if w == 'lead' else 'ATLAS'}: {t}"
                              for w, t in call.turns)
            extra = "\n".join(f"- {n}" for n in call.notes) or "- nothing noted"
            cards = ", ".join(call.cards) or "none"
            if call.meeting:
                extra += f"\n- *Meeting:* {call.meeting}"
            body = (f"{lines}\n\n*Noted:*\n{extra}\n*Cards raised:* {cards}\n"
                    "Anything sent on the call is noted above: the agreement goes "
                    "only at an allowed price with your own payment link, the invite "
                    "only to a slot that was free on your calendar.")
        else:
            body = (f"No one picked up ({status or 'no status'}"
                    f"{', voicemail' if call and call.machine else ''}).")
        await notify(Outbound(title=f"Call to {name}: {outcome}", body=body,
                              topic="agency", kind="alert"))
    except Exception as exc:  # noqa: BLE001
        log.warning("lead call: not posted: %s", type(exc).__name__)
