"""The website contact form: every message is stored first, then delivered.

A visitor writes on the agency landing, the thanks page or the store's contact
and help pages (`POST /public/agency/contact`, atlas/api/agency_contact.py). The
API only validates and WRITES A ROW (`agency_site_messages`): nothing is sent from the
request, so no lead is ever lost to a down mail server. Delivery is the
executor's, one channel at a time (`agency.deliver_contact`, Tier 1, driven by
`kernel/agency_contact.py`):

  * `email`    -- to the owner, through the Gmail relay the agency already uses,
                  with `Reply-To` set to the visitor so Miro answers by pressing
                  Reply in Gmail.
  * `whatsapp` -- to the owner's number (Twilio, the one WhatsApp writer in
                  `modules/tutorco/notify.py`), with the visitor's email and phone in
                  the text so he can answer from his phone. The Twilio call only
                  ACCEPTS the message; `poll_whatsapp` reads its real status later
                  and says `delivered` or `failed` (sandbox not joined, outside the
                  24 h window, ...). Accepted is never reported as delivered.
  * `ack`      -- a short "we got it" to the visitor, fixed text, no visitor text
                  but a name that passes a strict pattern.

A channel is independent of the others: one failing never touches the rest, and a
failed one is retried (MAX_ATTEMPTS) before it is marked `failed` with its cause.
A channel that cannot be configured is `unknown` in `channel_health`, never ok.

Everything a visitor typed is untrusted: it is capped, control characters are
stripped, header fields are single-line, and it goes into mail and WhatsApp bodies
as plain text. No model reads it (if one ever must, it goes through
`wrap_untrusted()` first). The visitor's address, phone and message are never
logged; a log line carries the row id and a channel name only.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from email.utils import formataddr
from typing import Any

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    FailureClass,
    Health,
    Observation,
    Tier,
)
from .. import store_origin
from .._http import explain

log = logging.getLogger("atlas.agency.contact")

PATH = "/public/agency/contact"
ACTION = "agency.deliver_contact"
CHANNELS = ("email", "whatsapp", "slack", "ack")
# Miro 2026-11-17: Slack is the owner's second channel; WhatsApp is not linked. The
# whatsapp channel stays in the code but is switched OFF unless this is set, so nothing
# waits on it and no check requires it.
WA_ENABLE_ENV = "AGENCY_CONTACT_WHATSAPP_ENABLED"
SLACK_TOPIC = "agency_alerts"          # #agency-alerts (SLACK_CHANNELS can remap it)
OWNER_EMAIL = store_origin.CONTACT_MAIL
DEFAULT_OWNER_WHATSAPP = store_origin.CONTACT_PHONE
OWNER_WA_ENV = "AGENCY_CONTACT_WHATSAPP"
WA_CAP_ENV = "AGENCY_CONTACT_WA_PER_HOUR"
TOPICS = frozenset({"preview", "question", "support", "order", "other"})
DEFAULT_TOPIC = "other"
SOURCES = frozenset({"landing", "thanks", "store-contact", "store-help"})
DEFAULT_SOURCE = "site"
NAME_MAX, EMAIL_MAX, PHONE_MAX = 100, 254, 30
MESSAGE_MIN, MESSAGE_MAX = 5, 4000
WA_MESSAGE_CHARS = 300
SLACK_MESSAGE_CHARS = 1000
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=15)           # a "sending" row nobody finished
WA_CHECK_FOR = timedelta(hours=6)             # how long to keep asking Twilio
LATE_AFTER = timedelta(minutes=30)            # undelivered this long = a problem
TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"
_WA_DONE = {"delivered": "delivered", "read": "delivered"}
_WA_DEAD = frozenset({"failed", "undelivered", "canceled"})

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
_SPACES = re.compile(r"\s+")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_PHONE = re.compile(rf"^\+?[0-9][0-9 ().\-]{{5,{PHONE_MAX - 1}}}$")
_SAFE_FIRST = re.compile(r"^[A-Za-z][A-Za-z'\-]{0,29}$")


# ------------------------------------------------------------------ input

def _line(raw: object, limit: int) -> str:
    """One visible line: control and bidi characters dropped, whitespace folded."""
    if not isinstance(raw, str):
        return ""
    return _SPACES.sub(" ", _CONTROL.sub(" ", raw)).strip()[:limit]


def _text(raw: object, limit: int) -> str:
    """Multi-line text: control characters dropped, newlines kept (max 2 blank)."""
    if not isinstance(raw, str):
        return ""
    cleaned = _CONTROL.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()[:limit]


def clean_email(raw: object) -> str:
    """The address lowercased, or "" when it is not one (no display names, no
    spaces, no header-splitting characters, at most 254 characters)."""
    if not isinstance(raw, str) or len(raw) > EMAIL_MAX + 66:
        return ""
    value = raw.strip().lower()
    if len(value) > EMAIL_MAX or not _EMAIL.match(value) or ".." in value:
        return ""
    return value


def clean_phone(raw: object) -> str:
    value = _line(raw, PHONE_MAX)
    return value if value and _PHONE.match(value) else ""


def validate(form: dict[str, str]) -> tuple[dict[str, str] | None, str | None, str]:
    """(submission, error, field). `field` names the offending input."""
    name = _line(form.get("name"), NAME_MAX)
    if not name:
        return None, "Please tell us your name.", "name"
    email = clean_email(form.get("email"))
    if not email:
        return None, "That does not look like an email address.", "email"
    raw_phone = _line(form.get("phone"), PHONE_MAX + 1)
    phone = clean_phone(raw_phone)
    if raw_phone and not phone:
        return None, "That phone number does not look right (or leave it empty).", "phone"
    message = _text(form.get("message"), MESSAGE_MAX + 1)
    if len(message) < MESSAGE_MIN:
        return None, "Please write a short message.", "message"
    if len(message) > MESSAGE_MAX:
        return None, f"Please keep the message under {MESSAGE_MAX} characters.", "message"
    topic = _line(form.get("topic"), 20).lower()
    source = _line(form.get("source"), 20).lower()
    return {
        "name": name, "email": email, "phone": phone, "message": message,
        "topic": topic if topic in TOPICS else DEFAULT_TOPIC,
        "source": source if source in SOURCES else DEFAULT_SOURCE,
    }, None, ""


def dedupe_key(email: str, message: str) -> str:
    """One identical message from one address is one row (a double click)."""
    return hashlib.sha256(f"{email}\n{message}".encode()).hexdigest()


def mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


# ------------------------------------------------------------------ config

def whatsapp_enabled() -> bool:
    return os.getenv(WA_ENABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def active_channels() -> tuple[str, ...]:
    """The channels a message is delivered through: WhatsApp only when switched on."""
    return tuple(c for c in CHANNELS if c != "whatsapp" or whatsapp_enabled())


def slack_problem() -> str | None:
    from ...channels import slack
    return slack.notice_problem(SLACK_TOPIC)


def owner_whatsapp() -> str:
    raw = (os.getenv(OWNER_WA_ENV) or DEFAULT_OWNER_WHATSAPP).strip()
    return re.sub(r"[\s().-]", "", raw)


TWILIO_JOINED_ENV = "WHATSAPP_TWILIO_JOINED"


def twilio_ready() -> bool:
    """Twilio is a fallback only when it is configured AND Miro said the sandbox is
    joined (or the sender is approved): `WHATSAPP_TWILIO_JOINED=1`."""
    from ..tutorco import notify
    return (os.getenv(TWILIO_JOINED_ENV, "").strip().lower() in ("1", "true", "yes")
            and notify.sender_problem() is None)


def whatsapp_problem() -> str | None:
    """Static configuration only: the number is valid and some route exists."""
    from ..tutorco import notify
    from . import wa_bridge
    if not re.fullmatch(r"\+[1-9]\d{7,14}", owner_whatsapp()):
        return f"{OWNER_WA_ENV} is not a valid E.164 number"
    if wa_bridge.configured():
        return None
    return notify.sender_problem()


async def wa_route() -> tuple[str | None, str]:
    """("bridge" | "twilio" | None, reason). The linked-device bridge when it is
    connected as the owner; else Twilio only when the bridge is UNREACHABLE and the
    sandbox is joined; else no route and the honest reason."""
    from . import wa_bridge
    if not wa_bridge.configured():
        return "twilio", ""
    st = await wa_bridge.status()
    if st.usable:
        return "bridge", ""
    if not st.reachable and twilio_ready():
        return "twilio", ""
    return None, st.reason or "WhatsApp is not available"


def email_problem() -> str | None:
    from . import outreach
    return outreach.sender_problem()


def wa_hourly_cap() -> int:
    try:
        return max(0, int(os.getenv(WA_CAP_ENV, "15")))
    except ValueError:
        return 15


def problem_for(channel: str) -> str | None:
    if channel in ("email", "ack"):
        return email_problem()
    if channel == "whatsapp":
        return whatsapp_problem()
    if channel == "slack":
        return slack_problem()
    return f"unknown channel {channel!r}"


# ------------------------------------------------------------------ storage

class Store:
    """The SQL, one atomic statement per step. Tests substitute a fake with the
    same methods (`use_store`); tests/test_schema_lint.py checks the SQL."""

    async def insert(self, sub: dict[str, str], ip_hash: str, now: datetime) -> int | None:
        """The new row id, or None when the same message from the same address
        is already stored (the answer to the visitor is the same either way)."""
        from ...kernel.state import fetch_one
        row = await fetch_one(
            """INSERT INTO agency_site_messages
                   (name, email, phone, message, topic, source, ip_hash, dedupe_key,
                    created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
               ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""",
            sub["name"], sub["email"], sub["phone"], sub["message"], sub["topic"],
            sub["source"], ip_hash, dedupe_key(sub["email"], sub["message"]), now)
        return int(row["id"]) if row else None

    async def peek(self, contact_id: int) -> dict[str, Any] | None:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT * FROM agency_site_messages WHERE id = $1",
                              int(contact_id))
        return dict(row) if row else None

    async def due(self, limit: int, now: datetime) -> list[tuple[int, str]]:
        """(id, channel) pairs waiting for delivery, oldest first. A `sending`
        row nobody finished within STALE_AFTER counts as waiting again."""
        from ...kernel.state import fetch_all
        out: list[tuple[int, str]] = []
        stale = now - STALE_AFTER
        for channel in active_channels():
            rows = await fetch_all(
                f"SELECT id FROM agency_site_messages WHERE ({channel}_status = 'pending' "
                f"OR ({channel}_status = 'sending' AND updated_at < $2)) "
                "ORDER BY id LIMIT $1", limit, stale)
            out += [(int(r["id"]), channel) for r in rows or []]
        return sorted(out)[:limit]

    async def claim(self, contact_id: int, channel: str, now: datetime) -> dict[str, Any] | None:
        """Take the one attempt at this channel: the row, or None when it is not
        waiting (so two ticks cannot send twice)."""
        from ...kernel.state import fetch_one
        row = await fetch_one(
            f"UPDATE agency_site_messages SET {channel}_status = 'sending', "
            f"{channel}_attempts = {channel}_attempts + 1, updated_at = $2 "
            f"WHERE id = $1 AND ({channel}_status = 'pending' "
            f"OR ({channel}_status = 'sending' AND updated_at < $3)) RETURNING *",
            int(contact_id), now, now - STALE_AFTER)
        return dict(row) if row else None

    async def sent(self, contact_id: int, channel: str, now: datetime,
                   sid: str | None = None) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            f"UPDATE agency_site_messages SET {channel}_status = 'sent', {channel}_at = $2, "
            f"{channel}_error = NULL, updated_at = $2 WHERE id = $1",
            int(contact_id), now)
        if sid and channel == "whatsapp":
            await execute_sql("UPDATE agency_site_messages SET whatsapp_sid = $2 WHERE id = $1",
                              int(contact_id), sid)

    async def failed(self, contact_id: int, channel: str, reason: str,
                     now: datetime) -> None:
        """Back to `pending` while attempts remain, else `failed` for good."""
        from ...kernel.state import execute_sql
        await execute_sql(
            f"UPDATE agency_site_messages SET {channel}_error = $2, updated_at = $3, "
            f"{channel}_status = CASE WHEN {channel}_attempts >= $4 THEN 'failed' "
            "ELSE 'pending' END WHERE id = $1", int(contact_id), reason[:300], now,
            MAX_ATTEMPTS)

    async def skipped(self, contact_id: int, channel: str, reason: str,
                      now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            f"UPDATE agency_site_messages SET {channel}_status = 'skipped', "
            f"{channel}_error = $2, updated_at = $3 WHERE id = $1",
            int(contact_id), reason[:300], now)

    async def wa_open(self, limit: int, now: datetime) -> list[dict[str, Any]]:
        from ...kernel.state import fetch_all
        rows = await fetch_all(
            "SELECT id, whatsapp_sid, whatsapp_at FROM agency_site_messages WHERE whatsapp_status = 'sent' "
            "AND whatsapp_sid IS NOT NULL AND whatsapp_at > $2 ORDER BY id LIMIT $1",
            limit, now - WA_CHECK_FOR)
        return [dict(r) for r in rows or []]

    async def wa_result(self, contact_id: int, status: str, detail: str,
                        now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            "UPDATE agency_site_messages SET whatsapp_status = $2, whatsapp_error = $3, "
            "whatsapp_delivered_at = CASE WHEN $2::text = 'delivered' THEN $4::timestamptz ELSE NULL END, "
            "updated_at = $4::timestamptz WHERE id = $1 AND whatsapp_status = 'sent'",
            int(contact_id), status, detail[:300] or None, now)

    async def sent_in_last_hour(self, now: datetime) -> int:
        from ...kernel.state import fetch_one
        row = await fetch_one(
            "SELECT count(*) AS n FROM agency_site_messages WHERE whatsapp_at > $1",
            now - timedelta(hours=1))
        return int(row["n"]) if row else 0

    async def health_counts(self, now: datetime) -> dict[str, int]:
        """Rows older than LATE_AFTER whose owner channels are not done."""
        from ...kernel.state import fetch_one
        row = await fetch_one(
            """SELECT
                 count(*) FILTER (WHERE email_status IN ('pending','sending','failed')) AS email_late,
                 count(*) FILTER (WHERE slack_status IN ('pending','sending','failed')) AS slack_late,
                 count(*) FILTER (WHERE whatsapp_status IN ('pending','sending','failed')) AS wa_late,
                 count(*) FILTER (WHERE whatsapp_status = 'sent') AS wa_unconfirmed,
                 count(*) AS total
               FROM agency_site_messages WHERE created_at < $1 AND created_at > $2""",
            now - LATE_AFTER, now - timedelta(days=2))
        return {k: int(v) for k, v in dict(row or {}).items()}


_store: Any = Store()


def use_store(new: Any) -> Any:
    """Swap the store (tests); returns the old one."""
    global _store
    old, _store = _store, new
    return old


def _now() -> datetime:
    return datetime.now(UTC)


async def submit(sub: dict[str, str], ip_hash: str, *, now: datetime | None = None) -> int | None:
    """Store one validated submission. Raises when it could not be stored: the
    caller must tell the visitor nothing was saved."""
    return await _store.insert(sub, ip_hash, now or _now())


# ------------------------------------------------------------------ messages

def _defang(text: str) -> str:
    from ..tutorco.notify import _defang as defang
    return defang(text)


def owner_subject(row: dict[str, Any]) -> str:
    return f"New message from {_line(row['name'], 60)} via agency site"


def owner_email_body(row: dict[str, Any]) -> str:
    phone = row.get("phone") or "(not given)"
    return (
        f"Name:    {row['name']}\n"
        f"Email:   {row['email']}\n"
        f"Phone:   {phone}\n"
        f"Topic:   {row.get('topic') or DEFAULT_TOPIC}\n"
        f"Page:    {row.get('source') or DEFAULT_SOURCE}\n"
        f"Sent:    {row['created_at']:%Y-%m-%d %H:%M UTC}   (message #{row['id']})\n"
        f"\n{row['message']}\n\n"
        "--\n"
        f"Press Reply to answer {row['name']} directly: Reply-To is {row['email']}.\n"
        "This message came from the website contact form; everything above was typed "
        "by the visitor.\n")


def owner_whatsapp_body(row: dict[str, Any]) -> str:
    msg = _SPACES.sub(" ", row["message"]).strip()
    cut = msg if len(msg) <= WA_MESSAGE_CHARS else msg[:WA_MESSAGE_CHARS - 1] + "…"
    lines = [f"[Contact] New website message #{row['id']}",
             f"From: {_defang(_line(row['name'], 60))}",
             f"Email: {row['email']}"]
    if row.get("phone"):
        lines.append(f"Phone: {row['phone']}")
    lines.append(f"Message: {_defang(cut)}")
    lines.append(f"Reply: press Reply on the email in Gmail, or write to {row['email']}"
                 + (f" / call {row['phone']}" if row.get("phone") else "") + ".")
    return "\n".join(lines)


def _slack_escape(text: str) -> str:
    from ...channels.slack import escape
    return escape(text)


def owner_slack_body(row: dict[str, Any]) -> str:
    """The Slack post: the visitor's words are escaped (Slack parses only `&`, `<`,
    `>`, so a pasted mention or link shows as text and notifies nobody) and links
    are defanged. The email and phone are given so Miro can answer from the thread."""
    msg = _SPACES.sub(" ", row["message"]).strip()
    cut = msg if len(msg) <= SLACK_MESSAGE_CHARS else msg[:SLACK_MESSAGE_CHARS - 1] + "…"
    lines = [f"New website message #{row['id']}",
             f"From: {_slack_escape(_defang(_line(row['name'], 60)))}",
             f"Email: {_slack_escape(row['email'])}"]
    if row.get("phone"):
        lines.append(f"Phone: {_slack_escape(row['phone'])}")
    lines.append(f"Message: {_slack_escape(_defang(cut))}")
    lines.append("Reply: press Reply on the email in Gmail, or write to the address above.")
    return "\n".join(lines)


def ack_body(row: dict[str, Any]) -> str:
    first = _line(row["name"], 60).split(" ")[0]
    hello = f"Hi {first}," if _SAFE_FIRST.match(first) else "Hello,"
    return (f"{hello}\n\nWe got your message and Miro will reply to this address soon, "
            "usually within a day. You do not need to send it again.\n\n"
            f"If it is urgent, call {store_origin.CONTACT_PHONE_DISPLAY}.\n\n"
            "This is an automatic confirmation. It repeats nothing you wrote.\n")


# ------------------------------------------------------------------ senders

async def _send_mail(to: str, subject: str, body: str, reply_to: str | None,
                     display: str) -> tuple[bool, str | None]:
    from . import outreach
    problem = outreach.sender_problem()
    if problem:
        return False, problem
    password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    sender = outreach.agency_sender()
    from ..academy import mail_template
    message = outreach._message(formataddr((display, sender)), to, subject, body,
                                None, reply_to=reply_to,
                                html=mail_template.render(subject, body))
    host, port = outreach.GMAIL_SMTP
    ok, reason = await outreach._deliver(message, host, port, sender, password)
    if not ok:
        return False, f"Gmail refused the message: {reason}"
    return True, None


async def _send_wa(body: str) -> tuple[bool, str]:
    """(True, id) or (False, reason) through the route `wa_route` picks. The only
    WhatsApp write for the owner chat; reached from executor actions only."""
    from ..tutorco import notify
    from . import wa_bridge
    route, why = await wa_route()
    if route is None:
        return False, why
    if route == "bridge":
        return await wa_bridge.send(owner_whatsapp(), body)
    return await notify.send_whatsapp(owner_whatsapp(), body)


async def _send_slack(row: dict[str, Any]) -> tuple[bool, str]:
    """(True, ts) or (False, reason). The Slack write for the owner channel; reached
    from the executor action only. Slack's `ok: true` is the delivery."""
    from ...channels import slack
    ts, why = await slack.post_notice(
        topic=SLACK_TOPIC, title=f"Website message #{row['id']}",
        body=owner_slack_body(row))
    return (True, ts or "") if why is None else (False, why)


# ------------------------------------------------------------------ the action

def vet(params: dict[str, Any]) -> str | None:
    cid = params.get("contact_id")
    if not isinstance(cid, int) or isinstance(cid, bool) or cid < 1:
        return "contact_id must be a positive integer"
    if params.get("channel") not in CHANNELS:
        return f"channel must be one of {', '.join(CHANNELS)}"
    return None


async def _guard(contact_id: int, channel: str) -> tuple[dict[str, Any] | None, str]:
    row = await _store.peek(contact_id)
    if row is None:
        return None, "no such message"
    if channel not in active_channels():
        return None, f"the {channel} channel is not enabled"
    if row.get(f"{channel}_status") not in ("pending", "sending"):
        return None, f"{channel} is already {row.get(f'{channel}_status')}"
    why = problem_for(channel)
    if why:
        return None, why
    return row, ""


async def deliver_dry(contact_id: int = 0, channel: str = "") -> ActionResult:
    row, why = await _guard(int(contact_id), channel)
    if row is None:
        return ActionResult(False, f"would not send: {why}")
    to = {"email": OWNER_EMAIL, "ack": mask(row["email"]),
          "whatsapp": "the owner's WhatsApp", "slack": "the owner's Slack channel"}[channel]
    return ActionResult(True, f"would send the {channel} notice for message "
                              f"#{row['id']} to {to}", evidence={"channel": channel})


async def deliver(contact_id: int = 0, channel: str = "") -> ActionResult:
    cid = int(contact_id)
    now = _now()
    row, why = await _guard(cid, channel)
    if row is None:
        return ActionResult(False, f"refused: {why}", failure_class=FailureClass.VALIDATION)
    if channel == "whatsapp":
        # No route is not an attempt: the row stays pending until the link is back.
        route, why = await wa_route()
        if route is None:
            return ActionResult(False, f"refused: {why}; the message waits")
        if await wa_sent_in_last_hour(now) >= wa_hourly_cap():
            return ActionResult(False, "refused: the hourly WhatsApp cap is reached; it "
                                       "waits for the next pass")
    taken = await _store.claim(cid, channel, now)
    if taken is None:
        return ActionResult(False, "refused: this attempt is already taken")
    sid: str | None = None
    try:
        if channel == "email":
            ok, reason = await _send_mail(
                OWNER_EMAIL, owner_subject(taken), owner_email_body(taken),
                taken["email"], "Website contact form")
        elif channel == "ack":
            ok, reason = await _send_mail(
                taken["email"], "We got your message", ack_body(taken), OWNER_EMAIL,
                "ATLAS Agency")
        elif channel == "slack":
            ok, got = await _send_slack(taken)
            sid, reason = (None, None) if ok else (None, got)
        else:
            ok, got = await _send_wa(owner_whatsapp_body(taken))
            sid, reason = (got, None) if ok else (None, got)
    except Exception as exc:  # noqa: BLE001
        ok, reason = False, explain(exc)
    if not ok:
        await _store.failed(cid, channel, str(reason or "send failed"), _now())
        log.warning("agency contact #%s: the %s notice failed", cid, channel)
        if channel not in ("whatsapp", "slack"):
            await _alert_if_given_up(cid, channel)
        return ActionResult(False, f"{channel} failed: {reason}")
    await _store.sent(cid, channel, _now(), sid)
    detail = {"email": f"emailed the owner (Reply-To {mask(taken['email'])})",
              "ack": f"told {mask(taken['email'])} the message arrived",
              "slack": "posted to the owner's Slack channel",
              "whatsapp": ("WhatsApp accepted by the linked device" if
                           str(sid).startswith("wa:") else "WhatsApp accepted by Twilio")
                          + " (delivery is confirmed later)"}[channel]
    return ActionResult(True, f"message #{cid}: {detail}",
                        evidence={"channel": channel, "sid": sid},
                        rollback_token=f"{cid}:{channel}")


async def _alert_if_given_up(cid: int, channel: str) -> None:
    """A channel that ran out of attempts is a delivery failure Miro should know
    about: one [Alert] on the owner WhatsApp (never the visitor's text)."""
    try:
        row = await _store.peek(cid)
        if row and row.get(f"{channel}_status") == "failed":
            from . import owner_wa
            await owner_wa.notify_owner(
                "alert", f"Website message #{cid}: the {channel} notice failed after "
                         f"{MAX_ATTEMPTS} tries. Check the Contact delivery health.")
    except Exception:  # noqa: BLE001
        log.warning("agency contact #%s: failure alert not sent", cid)


async def wa_sent_in_last_hour(now: datetime) -> int:
    """WhatsApp messages to the owner in the last hour, contact and portal alike:
    one chat, one cap."""
    n = await _store.sent_in_last_hour(now)
    try:
        from . import owner_wa
        n += await owner_wa.sent_in_last_hour(now)
    except Exception:  # noqa: BLE001
        log.warning("owner WhatsApp count unreadable; contact cap uses its own rows")
    return n


async def deliver_rollback(token: str) -> ActionResult:
    """A delivered email or WhatsApp cannot be unsent, and this does not pretend
    to; the row keeps its status so nothing follows it."""
    return ActionResult(True, "a delivered message cannot be unsent; nothing further "
                              "is sent for it", evidence={"unsent": False})


def actions() -> tuple[Action, ...]:
    return (
        Action(name=ACTION, tier=Tier.AUTO,
               summary="Deliver one website contact message: email the owner "
                       "(Reply-To the visitor), WhatsApp the owner, or send the "
                       "visitor a fixed confirmation",
               run=deliver, dry_run=deliver_dry, rollback=deliver_rollback, vet=vet,
               params={"contact_id": "the agency_site_messages row id",
                       "channel": "email, slack, ack (or whatsapp when enabled)"},
               max_per_hour=90),
    )


# ------------------------------------------------------------------ WhatsApp status

async def twilio_status(sid: str) -> tuple[str, str]:
    """(status, detail) of one Twilio message: a read, never a send."""
    account = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not (account and token) or not re.fullmatch(r"[A-Za-z0-9]{10,64}", sid or ""):
        return "unknown", "Twilio credentials or message id missing"
    try:
        async with httpx.AsyncClient(timeout=15, auth=(account, token)) as client:
            r = await client.get(f"{TWILIO_API}/{account}/Messages/{sid}.json")
        data = r.json() if r.status_code < 500 else {}
    except Exception as exc:  # noqa: BLE001
        return "unknown", explain(exc)
    if r.status_code >= 400:
        return "unknown", f"HTTP {r.status_code} from api.twilio.com"
    status = str(data.get("status") or "unknown")
    code = data.get("error_code")
    return status, (f"Twilio error {code}" if code else "")


async def poll_whatsapp(limit: int = 20) -> int:
    """Ask Twilio what became of each accepted message. Returns rows settled."""
    now = _now()
    settled = 0
    from . import wa_bridge
    for row in await _store.wa_open(limit, now):
        if str(row["whatsapp_sid"]).startswith(wa_bridge.SID_PREFIX):
            ack = await wa_bridge.message_status(row["whatsapp_sid"])
            if ack in ("delivered", "read"):
                await _store.wa_result(int(row["id"]), "delivered", "", _now())
                settled += 1
            elif ack == "error":
                await _store.wa_result(int(row["id"]), "failed",
                                       "WhatsApp reported an error for the message", _now())
                settled += 1
            continue
        status, detail = await twilio_status(row["whatsapp_sid"])
        if status in _WA_DONE:
            await _store.wa_result(int(row["id"]), "delivered", "", _now())
            settled += 1
        elif status in _WA_DEAD:
            await _store.wa_result(int(row["id"]), "failed",
                                   detail or f"Twilio status {status}", _now())
            settled += 1
    return settled


# ------------------------------------------------------------------ health

async def check_contact_delivery() -> Observation:
    """`agency.contact_delivery`: do website messages reach Miro?

    Email and Slack are the owner channels; WhatsApp counts only when switched on
    (`AGENCY_CONTACT_WHATSAPP_ENABLED`). UNKNOWN when a channel cannot be configured
    or the table cannot be read; FAILING when a message older than half an hour has
    not reached a channel; OK only when every recent message reached all of them."""
    name = "contact_delivery"
    wa_on = whatsapp_enabled()
    owner = ("email", "slack") + (("whatsapp",) if wa_on else ())
    missing = [f"{c}: {p}" for c in owner if (p := problem_for(c))]
    if wa_on:
        route, why = await wa_route()
        if route is None and not any(m.startswith("whatsapp") for m in missing):
            missing.append(f"whatsapp: {why}")               # bridge down = UNKNOWN
    if missing:
        return Observation(name, Health.UNKNOWN,
                           detail="cannot deliver website messages (" + "; ".join(missing) + ")")
    try:
        counts = await _store.health_counts(_now())
    except Exception as exc:  # noqa: BLE001
        return Observation(name, Health.UNKNOWN,
                           detail=f"agency_site_messages unreadable ({type(exc).__name__})")
    late = counts.get("email_late", 0) + counts.get("slack_late", 0)
    if wa_on:
        late += counts.get("wa_late", 0) + counts.get("wa_unconfirmed", 0)
    if late:
        return Observation(name, Health.FAILING, value=float(late), unit="messages",
                           detail=f"{late} channel delivery(ies) older than 30 minutes are "
                                  "not confirmed", evidence=counts)
    return Observation(name, Health.OK, value=0.0, unit="messages",
                       detail="recent website messages reached email and Slack"
                              + (" and WhatsApp" if wa_on else ""),
                       evidence=counts)
