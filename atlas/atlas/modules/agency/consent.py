"""agency.consent -- when ATLAS may text or ring a lead, decided in code.

US law, encoded rather than remembered (this is not legal advice; it is the
narrowest reading, on purpose):

  * **TCPA + FCC, February 2024.** An AI-generated voice is an "artificial
    voice", and an automated text is an autodialled message. Both need the
    recipient's PRIOR EXPRESS CONSENT; marketing needs it in writing. So
    ATLAS texts or calls a lead only when a live `agency_lead_consent` row
    exists for that number and that channel, and that row exists only when
    the lead gave it:
      - they replied (email) with a phone number AND an explicit ask to be
        called or texted -- matched by a regex here, never a model, and the
        number must appear literally in their message (`consent_from_email`);
      - they texted the business line first (Twilio's signed `From`): consent
        to be texted back on that number, and to be called too when the text
        asks for a call (`consent_from_sms`);
      - they rang the business line first: consent to be called back on that
        number (`consent_from_call`);
      - they asked on the "Have Mapfront call me" form on their preview site:
        a required, unchecked box whose label is the written consent
        (`consent_from_web_form`, source `web_form`; `callme.py` stores the
        page URL, time, IP hash, user agent and the exact text shown);
      - they told Miro so on HIS call from the brief ("Can my assistant call
        you back with the full proposal?"), and he recorded it himself:
        `agency.record_consent` (Tier AUTO, principal_only; source
        `principal`, the number their listed one, his words and the time on
        the row). Nothing but his own word, over a channel that proves it is
        him, writes that row.
    The evidence -- source message id, time, and their exact words -- is
    stored with the row; those words are untrusted and only ever reach a
    prompt through `wrap_untrusted()`.
  * **Opt-out.** STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT, "don't
    call", "stop texting", "remove me" and the like revoke every consent the
    lead gave, at once, and put their number (`tel:+1...`) and address on
    the suppression list (`OPT_OUT`, `opt_out`). Twilio's own STOP handling
    (`OptOutType=STOP` on the webhook, error 21610 on a send) counts too.
  * **Identification.** Every text and every call opens with
    `disclosure()`: "Hi, this is Miro's assistant from ATLAS Academy,
    calling about <business>." -- built here, the first line, and refused
    outright when AGENCY_NAME is set empty: the business responsible is
    named first (47 CFR 64.1200(b)(1)). Miro, 2026-10-01: the opening does
    not say "AI". The truth rule stands exactly as before: asked whether
    they are talking to a bot, an AI or a recording, the assistant answers
    plainly that it is Miro's AI assistant (`reception.AI_QUESTION`,
    `lead_call.DISCLOSE_AGAIN`, `reception.DISCLOSURE`), matched in code
    before any model; it never claims to be Miro or a person
    (`playbook.honesty_refusal` refuses "I'm Miro" and "I'm a real person"
    in every drafted or spoken line). His 2026-09-29 ask to introduce
    itself AS him stays refused: not saying "AI" unasked is one thing,
    saying "this is Miro" is a lie to the person on the line.
  * **Hours.** Calls and texts only 08:00-21:00 in the lead's time zone
    (`AGENCY_LEAD_TZ`, default America/New_York -- the missions prospect
    New York).
  * **Cap.** At most `AGENCY_CONTACT_CAP` (default 2) texts/calls since the
    lead last wrote to us; then ATLAS stops until they do.
  * **Suppression.** A suppressed number is never texted or called.

Everything a send needs is checked in `contact_guard`, shared by the dry run
and the run of `text_lead`, `call_lead` and `send_reply(sms)` -- one place,
so the preview and the send can never disagree.

Cold outreach stays email only (`outreach.send_outreach`, CAN-SPAM). A lead
with no consent gets a call brief Miro dials himself (`calls.call_brief`).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult
from . import _store

SCOPES = ("sms", "call")
DEFAULT_TZ = "America/New_York"
CALL_START_HOUR = 8
CALL_END_HOUR = 21          # exclusive: 20:59 is the last minute


# Miro, 2026-10-01: "say: this is ATLAS Academy". The number and the domain
# stay Mapfront's (`outreach.mapfront_number`, mapfront.io).
DEFAULT_AGENCY = "ATLAS Academy"


def agency_name() -> str:
    """AGENCY_NAME, default "ATLAS Academy". Set to "" and every text and
    call is refused: a message that cannot say who it is from does not go."""
    raw = os.getenv("AGENCY_NAME")
    return DEFAULT_AGENCY if raw is None else raw.strip()


def lead_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("AGENCY_LEAD_TZ", DEFAULT_TZ).strip() or DEFAULT_TZ)
    except Exception:                                   # noqa: BLE001
        return ZoneInfo(DEFAULT_TZ)


def contact_cap() -> int:
    try:
        return max(0, int(os.getenv("AGENCY_CONTACT_CAP", "2")))
    except ValueError:
        return 2


def sms_enabled() -> bool:
    """Off by default: Miro, 2026-09-30, "texting is off for leads; calls
    only". His own updates come over Slack."""
    return os.getenv("AGENCY_SMS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def calls_enabled() -> bool:
    return os.getenv("AGENCY_CALLS_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


# ----------------------------------------------------------------- phones

_PHONE = re.compile(r"(?<![\d+])(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")


def normalize(number: str | None) -> str | None:
    """E.164 for a US/Canada number, or an international one given with its
    `+`. None for anything else -- a number is never guessed at."""
    raw = str(number or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if raw.startswith("+") and not raw.startswith("+1"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def phones_in(text: str) -> list[str]:
    out: list[str] = []
    for match in _PHONE.finditer(text or ""):
        number = normalize("".join(match.groups()))
        if number and number not in out:
            out.append(number)
    return out


def tel_key(phone: str) -> str:
    """How a number sits in `agency_suppression` beside email addresses."""
    return f"tel:{phone}"


# --------------------------------------------------------------- opt-out

# Twilio's own keywords, as the whole message, and the phrases people use
# anywhere in a message. Matched before any model sees the text.
_KEYWORDS = re.compile(
    r"^\W*(?:stop|stopall|unsubscribe|cancel|end|quit|optout|opt\s*out|revoke)"
    r"\W*$", re.IGNORECASE)
_PHRASES = re.compile(
    r"\b(?:don'?t|do\s+not|never|stop|quit)\s+(?:ever\s+)?(?:call|calling|text|"
    r"texting|message|messaging|contact|contacting|email|emailing|bother|"
    r"bothering|spam|spamming|sending)\b|\bunsubscribe\b|\bremove\s+(?:me|us|my\s+number)\b|"
    r"\btake\s+(?:me|us)\s+off\b|\blose\s+(?:my|this)\s+number\b|\bopt[\s-]?out\b|"
    r"\bno\s+more\s+(?:calls|texts|messages|emails)\b|\bleave\s+(?:me|us)\s+alone\b",
    re.IGNORECASE)


def is_opt_out(text: str) -> bool:
    return bool(_KEYWORDS.match(text or "") or _PHRASES.search(text or ""))


# ------------------------------------------------------------- consent

_ASK_CALL = re.compile(
    r"\b(?:call|ring|phone)\s+(?:me|us)\b|\b(?:give|shoot)\s+(?:me|us)\s+a\s+"
    r"(?:call|ring)\b|\byou\s+(?:can|may|could)\s+(?:call|ring|phone)\b|"
    r"\bcall\s+(?:at|on)\b|\bprefer\s+a\s+(?:call|phone\s+call)\b",
    re.IGNORECASE)
_ASK_TEXT = re.compile(
    r"\b(?:text|sms|message)\s+(?:me|us)\b|\b(?:give|shoot|send)\s+(?:me|us)\s+"
    r"a\s+text\b|\byou\s+(?:can|may|could)\s+text\b|\btext\s+(?:at|on)\b|"
    r"\bprefer\s+(?:a\s+)?text\b", re.IGNORECASE)
_ASK_EITHER = re.compile(
    r"\b(?:reach|contact)\s+(?:me|us)\s+(?:at|on)\b|\b(?:call|ring)\s+or\s+text\b|"
    r"\btext\s+or\s+call\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Grant:
    phone: str
    scope: str            # sms | call
    quoted: str


def _scopes_asked(text: str) -> set[str]:
    scopes: set[str] = set()
    if _ASK_CALL.search(text) or _ASK_EITHER.search(text):
        scopes.add("call")
    if _ASK_TEXT.search(text) or _ASK_EITHER.search(text):
        scopes.add("sms")
    return scopes


def consent_from_email(text: str) -> list[Grant]:
    """An email grants consent only with exactly one phone number in it and
    an explicit ask to be called or texted. Two numbers, or a number with no
    ask ("my number is ..."), grant nothing -- Miro reads it himself."""
    if is_opt_out(text):
        return []
    numbers = phones_in(text)
    if len(numbers) != 1:
        return []
    quoted = " ".join((text or "").split())[:600]
    return [Grant(numbers[0], scope, quoted) for scope in sorted(_scopes_asked(text))]


def consent_from_sms(signed_from: str, text: str) -> list[Grant]:
    """They texted in first: they may be texted back on that number, and
    called too when the text asks for a call."""
    if is_opt_out(text):
        return []
    phone = normalize(signed_from)
    if phone is None:
        return []
    quoted = " ".join((text or "").split())[:600]
    scopes = {"sms"} | ({"call"} if "call" in _scopes_asked(text) else set())
    return [Grant(phone, scope, quoted) for scope in sorted(scopes)]


def consent_from_call(signed_from: str, first_words: str) -> list[Grant]:
    """They rang the business line first: they may be called back."""
    if is_opt_out(first_words):
        return []
    phone = normalize(signed_from)
    if phone is None:
        return []
    quoted = (" ".join((first_words or "").split())[:600]
              or "(called the business line)")
    return [Grant(phone, "call", quoted)]


WEB_FORM = "web_form"


def consent_from_web_form(phone: str | None, consent_text: str) -> list[Grant]:
    """They ticked the box on the form: consent to be called on that number,
    in writing -- the words are the form's own (`callme.CONSENT_TEXT`), not
    theirs. Calls only: the form says nothing about texts."""
    number = normalize(phone)
    text = " ".join((consent_text or "").split())
    if number is None or not text:
        return []
    return [Grant(number, "call", text[:600])]


async def record(lead_id: int, grants: list[Grant], channel: str,
                 source_id: str) -> list[str]:
    """Store each grant not already live. Returns the scopes newly granted."""
    added: list[str] = []
    for grant in grants:
        if await _store.is_suppressed(tel_key(grant.phone)):
            continue            # an opt-out is not undone by a later message
        if await _store.active_consent(grant.phone, grant.scope):
            continue
        await _store.insert_consent(lead_id, grant.phone, grant.scope, channel,
                                    source_id, grant.quoted)
        added.append(grant.scope)
    return added


PRINCIPAL = "principal"


async def _principal_grant(lead_id: int, how: str) -> tuple[dict[str, Any] | None,
                                                            str | None, str | None]:
    """The lead, their listed number and the words, or why not. Shared by
    the dry run and the run of `agency.record_consent`."""
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return None, None, f"no such lead: {lead_id}"
    phone = normalize(lead.get("phone"))
    if phone is None:
        return None, None, (f"lead {lead_id} has no listed phone number to record "
                            "consent on; a number is never guessed")
    words = " ".join((how or "").split())[:600]
    if not words:
        return None, None, "say how they consented (how=...): the words go on the row"
    if await _store.is_suppressed(tel_key(phone)):
        return None, None, (f"{phone} opted out; a consent recorded by hand does "
                            "not undo an opt-out")
    return lead, phone, words


async def record_consent(lead_id: int, how: str) -> ActionResult:
    """Tier AUTO, principal_only (Miro, 2026-10-01): the lead told HIM, on
    his own call from the brief, that the assistant may call them back with
    the proposal. Only his word reaches this -- a consent row is what lets
    `agency.call_lead` dial, so no email, page or model may write one. The
    number is the lead's listed one; the words are his note of what they
    said, with the time, on the row."""
    lead_id = int(lead_id)
    lead, phone, words = await _principal_grant(lead_id, how)
    if lead is None:
        return ActionResult(False, f"refused: {words}")
    assert phone is not None and words is not None
    if await _store.active_consent(phone, "call"):
        return ActionResult(True, f"consent to call {phone} is already on record",
                            evidence={"phone": phone, "already": True})
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    consent_id = await _store.insert_consent(
        lead_id, phone, "call", PRINCIPAL, f"miro:{stamp}", words,
        evidence={"recorded_by": "Miro", "at": stamp})
    if consent_id is None:
        return ActionResult(False, "failed to record the consent")
    return ActionResult(
        True, f"recorded lead {lead_id}'s consent to be called on {phone}: {words}",
        evidence={"consent_id": consent_id, "phone": phone, "quoted": words},
        rollback_token=str(consent_id))


async def record_consent_dry(lead_id: int, how: str) -> ActionResult:
    lead, phone, words = await _principal_grant(int(lead_id), how)
    if lead is None:
        return ActionResult(False, str(words))
    return ActionResult(True, f"would record consent to call {phone}: {words}")


async def record_consent_rollback(token: str) -> ActionResult:
    """The row this run wrote, removed -- nothing was dialled on it yet."""
    if not token:
        return ActionResult(True, "nothing was recorded; nothing to roll back")
    await _store.delete_consent(int(token))
    return ActionResult(True, f"removed consent row {token}")


async def opt_out(lead_id: int | None, *, phone: str | None = None,
                  address: str | None = None, why: str) -> None:
    """Revoke every consent the lead gave and suppress every way to reach
    them. Immediate, and never undone by code."""
    phones: set[str] = set()
    if phone:
        normal = normalize(phone)
        if normal:
            phones.add(normal)
    if lead_id is not None:
        phones |= set(await _store.consent_phones(lead_id))
        lead = await _store.get_lead(lead_id)
        normal = normalize((lead or {}).get("phone"))
        if normal:
            phones.add(normal)
        await _store.revoke_consent_for_lead(lead_id, why)
    for number in phones:
        await _store.revoke_consent_for_phone(number, why)
        await _store.add_suppression(tel_key(number), why)
    if address:
        await _store.add_suppression(address.strip().lower(), why)


# ------------------------------------------------------------- disclosure

DISCLOSURE_HEAD = "Hi, this is Miro's assistant from"
# The words of the honest answer, when a lead asks (never volunteered in
# the opening since 2026-10-01; never withheld when asked).
AI_WORDS = "I'm an AI"


def disclosure(business: str, *, calling: bool) -> str:
    """The first line of every text and call: who is calling, from where,
    about what. A call does not say "AI" (Miro, 2026-10-01): the person can
    ask, and gets the plain answer. A text cannot be asked, so a text still
    says it up front. Raises when AGENCY_NAME is set empty: a message that
    cannot say who it is from is not sent."""
    agency = agency_name()
    if not agency:
        raise ValueError("AGENCY_NAME is empty; a text or call must name the business")
    who = " ".join((business or "your business").split())[:80]
    if calling:
        return f"{DISCLOSURE_HEAD} {agency}, calling about {who}."
    return (f"{DISCLOSURE_HEAD} {agency}, {AI_WORDS}, texting about the website "
            f"we made for {who}.")


def discloses(text: str) -> bool:
    """The first line is the identification: who, and from where."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    return first.startswith(DISCLOSURE_HEAD)


# ------------------------------------------------------------------ hours

def within_hours(now: datetime | None = None) -> tuple[bool, str]:
    local = (now or datetime.now(UTC)).astimezone(lead_tz())
    ok = CALL_START_HOUR <= local.hour < CALL_END_HOUR
    return ok, (f"it is {local.strftime('%H:%M')} for the lead "
                f"({local.tzinfo}); calls and texts only "
                f"{CALL_START_HOUR:02d}:00-{CALL_END_HOUR:02d}:00")


# ------------------------------------------------------------------ guard

@dataclass(frozen=True, slots=True)
class Contact:
    lead: dict[str, Any]
    phone: str
    consent: dict[str, Any]
    opening: str


async def contact_guard(lead_id: int, channel: str, *,
                        now: datetime | None = None) -> tuple[Contact | None, str | None]:
    """Everything a text or a call to this lead needs, or the first reason
    it may not happen. Shared by every dry run and run that reaches a phone."""
    if channel not in SCOPES:
        return None, f"unknown channel {channel!r}"
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return None, f"no such lead: {lead_id}"
    from .verify import contact_refusal
    unverified = await contact_refusal(lead_id)
    if unverified is not None:
        return None, unverified
    consent_row = await _store.consent_for_lead(lead_id, channel)
    if consent_row is None:
        return None, (f"no consent on record for a {'text' if channel == 'sms' else 'call'}"
                      f" to lead {lead_id}: ATLAS texts or calls only a lead who "
                      "asked for it (TCPA). Use agency.call_brief for Miro to dial "
                      "himself")
    phone = str(consent_row["phone"])
    if await _store.is_suppressed(tel_key(phone)):
        return None, f"{phone} is on the suppression list"
    if channel == "sms" and not sms_enabled():
        return None, "texting leads is off (AGENCY_SMS_ENABLED): calls only"
    if channel == "call" and not calls_enabled():
        return None, "calling leads is off: AGENCY_CALLS_ENABLED is not set"
    missing = [n for n in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")
               if not os.getenv(n, "").strip()]
    if channel == "call" and not os.getenv("ATLAS_HOST", "").strip():
        missing.append("ATLAS_HOST")
    if missing:
        return None, f"not configured: {', '.join(missing)}"
    ok, why = within_hours(now)
    if not ok:
        return None, why
    cap = contact_cap()
    made = await _store.contacts_since_last_word(lead_id)
    if made >= cap:
        return None, (f"{made} text(s)/call(s) since the lead last wrote; the cap "
                      f"is {cap}, so ATLAS stops until they reply")
    try:
        opening = disclosure(str(lead.get("name") or ""), calling=channel == "call")
    except ValueError as exc:
        return None, str(exc)
    return Contact(lead, phone, consent_row, opening), None
