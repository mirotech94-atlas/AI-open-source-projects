"""Tell Miro about TUTORCO events on his own WhatsApp chat. Notify only.

`tutorco.notify_owner` is Tier 1: it writes one outbox row and sends one templated
message to ONE recipient, `TUTORCO_OWNER_WHATSAPP`, which the action cannot override
(there is no recipient parameter). The words come from a fixed template per event
kind; the only outside text is a short quote, truncated and wrapped as untrusted.
A free-text WhatsApp reply is never an approval: `ingest_owner_reply` files a note and
calls nothing. The one exception is `tutorco.notify_card`: every Tier 2 card that is not
portal-only (`kernel/wa_approvals.py`) goes out with a one-time code and two tappable
buttons (a Twilio quick-reply template, `WHATSAPP_CONTENT_SID_CARD`); if the template
is unavailable the plain "Reply YES <code> or NO <code>" text goes instead, so a card
is never lost. Only a tap or an exact `YES <code>` / `NO <code>` from the verified
owner number is recorded, through `approvals.decide`. A portal-only card is told
read-only.

Rollback marks the outbox row retracted so it frees its dedupe slot. A message
already delivered cannot be unsent, and the rollback says so. The number and the
Twilio credentials are read from the environment only and never logged or stored.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from ...kernel import idempotency, wa_format
from ...kernel.contracts import ActionResult, FailureClass, Health, Observation
from ...kernel.permissions import wrap_untrusted
from ...kernel.state import execute_sql, fetch_one
from .._http import explain

log = logging.getLogger("atlas.tutorco.notify")

API = "https://api.twilio.com/2010-04-01/Accounts"
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
KINDS: dict[str, str] = {
    "post_decision_pending": "\U0001F4DD A post is waiting for your decision.",
    "client_urgent": "\U0001F6A8 An urgent item about a client needs you.",
    "portal_fix": "\U0001F527 A portal fix was made.",
    "prospect_reply": "\U0001F4AC A prospect replied!",
}
QUOTED_KINDS = ("client_urgent", "prospect_reply")
QUOTE_MAX = 160
REF_MAX = 64
NOTE_MAX = 500
DEFAULT_HOURLY_CAP = 6
# Sensor messages have their own hourly cap (sensors/alerts.py); counting them here
# let a busy hour starve the daily report and every other owner message.
SENSOR_KINDS = ("sensor_alert", "sensor_clip")


# ------------------------------------------------------------------ config

def owner_number() -> tuple[str | None, str]:
    """(number, "") when TUTORCO_OWNER_WHATSAPP is set and E.164, else (None, why)."""
    raw = os.getenv("TUTORCO_OWNER_WHATSAPP", "").strip()
    if not raw:
        return None, "TUTORCO_OWNER_WHATSAPP is not configured"
    if not E164.match(raw):
        return None, "TUTORCO_OWNER_WHATSAPP is not a valid E.164 number"
    return raw, ""


def sender_problem() -> str | None:
    if not (os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN")):
        return "Twilio credentials are not configured"
    if not _from_number():
        return "no WhatsApp sender (TUTORCO_WHATSAPP_FROM or TWILIO_FROM) is configured"
    return None


def _from_number() -> str:
    return (os.getenv("TUTORCO_WHATSAPP_FROM", "").strip()
            or os.getenv("TWILIO_FROM", "").strip())


def hourly_cap() -> int:
    try:
        return max(0, int(os.getenv("TUTORCO_NOTIFY_PER_HOUR", str(DEFAULT_HOURLY_CAP))))
    except ValueError:
        return DEFAULT_HOURLY_CAP


def _wa(number: str) -> str:
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


# ------------------------------------------------------------------ storage

async def _find_live(kind: str, ref: str) -> dict[str, Any] | None:
    row = await fetch_one(
        "SELECT id, status FROM tutorco_owner_outbox WHERE kind=$1 AND ref=$2 "
        "AND status IN ('sending','sent')", kind, ref)
    return None if row is None else dict(row)


async def _count_last_hour() -> int:
    row = await fetch_one(
        "SELECT count(*) AS n FROM tutorco_owner_outbox WHERE status<>'failed' "
        "AND kind <> ALL($1::text[]) AND created_at > now() - interval '1 hour'",
        list(SENSOR_KINDS))
    return int((row or {})["n"]) if row else 0


async def _insert_outbox(kind: str, ref: str, body: str) -> int:
    row = await fetch_one(
        "INSERT INTO tutorco_owner_outbox (kind, ref, body) VALUES ($1,$2,$3) "
        "RETURNING id", kind, ref, body)
    return int(row["id"])                                   # type: ignore[index]


async def _set_status(row_id: int, status: str, sid: str | None = None) -> None:
    await execute_sql("UPDATE tutorco_owner_outbox SET status=$2, sid=COALESCE($3, sid) "
                      "WHERE id=$1", row_id, status, sid)


async def _insert_note(body: str) -> int:
    row = await fetch_one("INSERT INTO tutorco_owner_notes (body) VALUES ($1) RETURNING id",
                          body)
    return int(row["id"])                                   # type: ignore[index]


# ------------------------------------------------------------------ message

# An opaque id, or a Slack message ts. Anything else (a URL, a scheme, free
# text) is dropped whole, never trimmed into shape: the message carries no link
# a caller could choose.
_REF = re.compile(rf"[A-Za-z0-9_-]{{1,{REF_MAX}}}|\d{{9,10}}\.\d{{6}}")
_SCHEME = re.compile(r"\b([A-Za-z][A-Za-z0-9+.\-]*):(?=//)|\b(javascript|vbscript|data"
                     r"|mailto|tel|sms|whatsapp|intent):", re.IGNORECASE)
_DOT = re.compile(r"(?<=\w)\.(?=\w)")


def _clean_ref(value: Any) -> str:
    text = str(value or "").strip()
    return text if _REF.fullmatch(text) else ""


def _defang(text: str) -> str:
    """Quoted outside text must not become a tappable link in WhatsApp:
    `https://x.y` -> `https[:]//x[.]y`."""
    return _DOT.sub("[.]", _SCHEME.sub(lambda m: f"{m.group(1) or m.group(2)}[:]", text))


def compose(kind: str, ref: Any, card: Any = "", quote: Any = "") -> str:
    """Fixed template per kind plus the id and card reference. Never model text."""
    text = f"{KINDS[kind]} Ref {_clean_ref(ref)}."
    card_ref = _clean_ref(card)
    if card_ref:
        text += f" Slack card: {card_ref}."
    if kind in QUOTED_KINDS and str(quote or "").strip():
        quoted = _defang(str(quote).strip()[:QUOTE_MAX])
        text += "\n" + wrap_untrusted(quoted, "tutorco-quote")
    text += "\nNotify only: decide on the Slack card, not here."
    return wa_format.tag(wa_format.Category.TUTORCO, text)


def _problem(kind: str, ref: Any) -> str | None:
    if kind not in KINDS:
        return f"unknown event kind {kind!r}"
    if not _clean_ref(ref):
        return "an event id is required"
    _, why = owner_number()
    return why or sender_problem()


# ------------------------------------------------------------------ action

async def send_whatsapp(to: str, body: str,
                        media_url: str | None = None) -> tuple[bool, str]:
    """(True, MessageSid) or (False, reason). The only WhatsApp write in ATLAS;
    reached from `notify_owner` only, through the executor."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    # Nothing leaves untagged: a body without a category tag becomes "General".
    body = wa_format.ensure(body)
    try:
        async with httpx.AsyncClient(timeout=20, auth=(sid, token)) as client:
            form = {"From": _wa(_from_number()), "To": _wa(to), "Body": body}
            if media_url:
                form["MediaUrl"] = media_url
            r = await client.post(f"{API}/{sid}/Messages.json", data=form)
    except Exception as exc:  # noqa: BLE001
        return False, explain(exc)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code} from api.twilio.com (code {data.get('code')})"
    return True, str(data.get("sid") or "")


CONTENT_API_SID = re.compile(r"^HX[0-9a-f]{32}$")


def content_sid() -> str | None:
    """The quick-reply template's Content SID (`HX...`), made once by
    `tools/wa_card_template.py` and kept in WHATSAPP_CONTENT_SID_CARD. Unset or
    malformed: no buttons, the plain-text card is sent."""
    raw = os.getenv("WHATSAPP_CONTENT_SID_CARD", "").strip()
    return raw if CONTENT_API_SID.match(raw) else None


async def send_whatsapp_card(to: str, text: str, pid: int, code: str) -> tuple[bool, str]:
    """(True, MessageSid) or (False, reason): the card as a quick-reply template
    message, `text` in the body and two buttons whose payloads are
    `approve:<pid>:<code>` / `decline:<pid>:<code>`. Same single WhatsApp write as
    `send_whatsapp`, reached only from `notify_card` through the executor."""
    import json
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    template = content_sid()
    if template is None:
        return False, "no card template configured"
    # WhatsApp refuses line breaks in a template variable: one flat line.
    flat = " ".join(wa_format.ensure(text, wa_format.Category.APPROVALS).split())
    try:
        async with httpx.AsyncClient(timeout=20, auth=(sid, token)) as client:
            r = await client.post(f"{API}/{sid}/Messages.json", data={
                "From": _wa(_from_number()), "To": _wa(to), "ContentSid": template,
                "ContentVariables": json.dumps(
                    {"1": flat, "2": str(int(pid)), "3": code})})
    except Exception as exc:  # noqa: BLE001
        return False, explain(exc)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code} from api.twilio.com (code {data.get('code')})"
    return True, str(data.get("sid") or "")


async def notify_owner_dry(kind: str, ref: str, card: str = "",
                           quote: str = "") -> ActionResult:
    why = _problem(kind, ref)
    if why:
        return ActionResult(False, f"would not send: {why}")
    return ActionResult(True, "would send one templated message to the owner chat",
                        evidence={"text": compose(kind, ref, card, quote)})


async def notify_owner(kind: str, ref: str, card: str = "",
                       quote: str = "") -> ActionResult:
    why = _problem(kind, ref)
    if why:
        return ActionResult(False, f"not sent: {why}",
                            failure_class=FailureClass.VALIDATION)
    ref = _clean_ref(ref)
    if await _find_live(kind, ref):
        return ActionResult(False, f"already notified for {kind} {ref}")
    if await _count_last_hour() >= hourly_cap():
        return ActionResult(False, "owner notification rate limit reached for this hour")
    number, _ = owner_number()
    body = compose(kind, ref, card, quote)
    try:
        row_id = await _insert_outbox(kind, ref, body)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"not sent: could not record it ({explain(exc)})")
    ok, detail = await send_whatsapp(number or "", body)
    if not ok:
        await _set_status(row_id, "failed")
        return ActionResult(False, f"send failed: {detail}")
    await _set_status(row_id, "sent", detail)
    return ActionResult(True, f"told the owner: {kind} {ref}",
                        evidence={"outbox_id": row_id, "sid": detail,
                                  "text_sha256": hashlib.sha256(body.encode()).hexdigest()},
                        rollback_token=str(row_id))


async def notify_owner_rollback(token: str) -> ActionResult:
    try:
        row_id = int(token)
    except (TypeError, ValueError):
        return ActionResult(True, "nothing to roll back")
    await _set_status(row_id, "retracted")
    return ActionResult(True, "outbox row marked retracted; a message already "
                              "delivered cannot be unsent")


# ------------------------------------------------------------------ daily report

DAILY_KIND = "daily_report"
UNCERTAIN_MARK = " (outcome unknown, may have been delivered)"  # = whatsapp_daily
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _daily_ref(day: str, part: int) -> str:
    return day if part == 0 else f"{day}-p{part + 1}"


def _daily_problem(day: Any) -> str | None:
    """The day must be today in New York: a past or future date is never sent."""
    from ...kernel import whatsapp_daily
    if not _DAY.fullmatch(str(day or "")):
        return "a New York date (YYYY-MM-DD) is required"
    if str(day) != whatsapp_daily.ny_day(datetime.now(UTC)):
        return "the daily report is only sent for today, New York time"
    _, why = owner_number()
    return why or sender_problem()


async def notify_daily_dry(day: str) -> ActionResult:
    why = _daily_problem(day)
    if why:
        return ActionResult(False, f"would not send: {why}")
    from ...kernel import whatsapp_daily
    parts = await whatsapp_daily.digest()
    return ActionResult(True, f"would send the daily report in {len(parts)} message(s) "
                              "to the owner chat", evidence={"text": "\n\n".join(parts)})


async def notify_daily(day: str) -> ActionResult:
    """The daily agent report: one message per New York date, split in whole lines
    when long. The text is built by `kernel.whatsapp_daily`, never by a caller."""
    why = _daily_problem(day)
    if why:
        return ActionResult(False, f"not sent: {why}",
                            failure_class=FailureClass.VALIDATION)
    day = str(day)
    if await _find_live(DAILY_KIND, day):
        return ActionResult(True, f"the daily report for {day} was already sent")
    from ...kernel import whatsapp_daily
    parts = await whatsapp_daily.digest()
    if hourly_cap() - await _count_last_hour() < len(parts):
        return ActionResult(False, "owner notification rate limit reached for this hour")
    number, _ = owner_number()
    rows: list[int] = []
    for i, body in enumerate(parts):
        try:
            row_id = await _insert_outbox(DAILY_KIND, _daily_ref(day, i), body)
        except Exception as exc:  # noqa: BLE001
            return ActionResult(False, f"not sent: could not record it ({explain(exc)})")
        ok, detail = await send_whatsapp(number or "", body)
        if not ok:
            doubt = idempotency.uncertain(detail)
            if not doubt:                   # certainly not sent: frees the slot
                await _set_status(row_id, "failed")
            # else the row stays 'sending' (in doubt): `_find_live` then blocks a
            # second send for the day, because Twilio may have delivered it.
            return ActionResult(
                False, f"send failed on message {i + 1} of {len(parts)}"
                       f"{UNCERTAIN_MARK if doubt else ''}: {detail}",
                rollback_token=",".join(map(str, rows)) or None)
        await _set_status(row_id, "sent", detail)
        rows.append(row_id)
    return ActionResult(True, f"told the owner: daily report {day} ({len(parts)} message(s))",
                        evidence={"outbox_ids": rows, "text_sha256": hashlib.sha256(
                            "\n".join(parts).encode()).hexdigest()},
                        rollback_token=",".join(map(str, rows)))


async def notify_daily_rollback(token: str) -> ActionResult:
    ids = [int(t) for t in str(token or "").split(",") if t.strip().isdigit()]
    if not ids:
        return ActionResult(True, "nothing to roll back")
    for row_id in ids:
        await _set_status(row_id, "retracted")
    return ActionResult(True, "outbox rows marked retracted; a message already "
                              "delivered cannot be unsent")


# ------------------------------------------------------------------ bad-news alert

ALERT_KIND = "alert"


async def _alert_row(observation_id: Any) -> dict[str, Any] | None:
    try:
        oid = int(observation_id)
    except (TypeError, ValueError):
        return None
    row = await fetch_one(
        "SELECT o.id, o.health, p.name AS property, c.name AS check_name "
        "FROM observations o JOIN checks c ON c.id = o.check_id "
        "JOIN properties p ON p.id = c.property_id WHERE o.id = $1", oid)
    return None if row is None else dict(row)


def _alert_problem() -> str | None:
    from ...kernel import whatsapp_alerts
    if not whatsapp_alerts.enabled():
        return "WHATSAPP_ALERTS is off"
    _, why = owner_number()
    return why or sender_problem()


async def notify_alert_dry(observation_id: int) -> ActionResult:
    why = _alert_problem()
    if why:
        return ActionResult(False, f"would not send: {why}")
    row = await _alert_row(observation_id)
    if row is None or row["health"] != "failing":
        return ActionResult(False, "would not send: that reading is not a failure")
    from ...kernel import whatsapp_alerts
    return ActionResult(True, "would send one alert line to the owner chat", evidence={
        "text": whatsapp_alerts.say(row["property"], row["check_name"])})


async def notify_alert(observation_id: int) -> ActionResult:
    """Tell the owner a check just failed. The only parameter is the reading's id;
    the words are built in code from the check's name, the recipient is fixed, one
    message per reading, and at most `WHATSAPP_ALERTS_PER_HOUR` an hour."""
    from ...kernel import whatsapp_alerts
    why = _alert_problem()
    if why:
        return ActionResult(False, f"not sent: {why}",
                            failure_class=FailureClass.VALIDATION)
    try:
        row = await _alert_row(observation_id)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"not sent: could not read it ({explain(exc)})")
    if row is None or row["health"] != "failing":
        return ActionResult(False, "not sent: that reading is not a failure",
                            failure_class=FailureClass.VALIDATION)
    ref = whatsapp_alerts.ref_of(row["id"])
    if await _find_live(ALERT_KIND, ref):
        return ActionResult(False, f"already notified for {ref}")
    if (await whatsapp_alerts.sent_last_hour() >= whatsapp_alerts.per_hour()
            or await _count_last_hour() >= hourly_cap()):
        return ActionResult(False, "owner alert rate limit reached for this hour")
    number, _ = owner_number()
    body = whatsapp_alerts.say(row["property"], row["check_name"])
    try:
        row_id = await _insert_outbox(ALERT_KIND, ref, body)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"not sent: could not record it ({explain(exc)})")
    ok, detail = await send_whatsapp(number or "", body)
    if not ok:
        doubt = idempotency.uncertain(detail)
        if not doubt:
            await _set_status(row_id, "failed")
        return ActionResult(False, f"send failed{UNCERTAIN_MARK if doubt else ''}: "
                                   f"{detail}")
    await _set_status(row_id, "sent", detail)
    return ActionResult(True, f"told the owner about {ref}",
                        evidence={"outbox_id": row_id, "sid": detail},
                        rollback_token=str(row_id))


# ------------------------------------------------------------------ inbound

async def ingest_owner_reply(text: str) -> ActionResult:
    """File what the owner wrote as a note. It is untrusted, wrapped, and never
    triggers an action: not an approval, not a command. No webhook calls this yet."""
    body = wrap_untrusted(str(text or "").strip()[:NOTE_MAX], "owner-whatsapp-reply")
    try:
        note_id = await _insert_note(body)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"note not saved: {explain(exc)}")
    return ActionResult(True, "noted; a WhatsApp reply approves nothing",
                        evidence={"note_id": note_id})


# ------------------------------------------------------------------ cards

CARD_WHAT_MAX = 220


def compose_card(pid: int, action: str, params: dict, expires_at: datetime,
                 code: str | None, *, buttons: bool = False) -> str:
    """The owner's message for one Tier 2 card: what it is, what approving does,
    the risk, when it expires and how to answer. Built from the action and its
    parameters through the board's own wording, never from the reasoner's text;
    defanged so nothing in it is a tappable link. `buttons` says the Approve /
    Decline buttons are attached; without them the text carries the YES/NO code."""
    from ...kernel import board
    from ...kernel.registry import REGISTRY
    from ...kernel.wa_approvals import effect_line, strip_code_shapes
    spec = REGISTRY.actions.get(action)
    full = " ".join(board.what_of(action, params, spec).split())
    what = _defang(strip_code_shapes(full[:CARD_WHAT_MAX])) or "a decision is waiting"
    if len(full) > CARD_WHAT_MAX:
        what += " ... (cut; full text on the portal)"
    risk = board.risk_of(action, spec)
    head = f"\U0001F4CB ATLAS card #{int(pid)}: {what}.\nRisk: {risk}. "
    head += f"Expires {expires_at:%a %d %b %H:%M} UTC."
    cat = wa_format.Category.APPROVALS
    if code is None:
        return wa_format.tag(
            cat, head + "\nDecide it on the portal (approvals board) \U0001F5A5\uFE0F "
                        "WhatsApp can't decide this one.")
    effect = _defang(strip_code_shapes(effect_line(action, params, spec)))
    how = ("Tap \u2705 Approve or \u274C Decline, or decide on the portal."
           if buttons else
           f"Reply \u2705 YES {code} or \u274C NO {code}, or decide on the portal. "
           f"The code works once.")
    return wa_format.tag(
        cat, f"{head}\n{effect}\nNothing runs unless you answer \U0001F642 {how}")


async def _card_row(pid: int) -> dict[str, Any] | None:
    row = await fetch_one(
        "SELECT action, params, tier, status, expires_at, dry_run_ok "
        "FROM proposals WHERE id=$1", int(pid))
    return None if row is None else dict(row)


async def notify_card_dry(proposal_id: int) -> ActionResult:
    why = _problem("post_decision_pending", "card")
    if why:
        return ActionResult(False, f"would not send: {why}")
    return ActionResult(True, "would send one templated card message to the owner chat")


async def notify_card(proposal_id: int) -> ActionResult:
    """Tell the owner about the pending card #proposal_id. The recipient is fixed
    to TUTORCO_OWNER_WHATSAPP; the only parameter is the card id, and the words are
    built from the stored card. Once per card."""
    from ...kernel import idempotency, wa_approvals
    from ...kernel.audit import audit
    why = _problem("post_decision_pending", "card")
    if why:
        return ActionResult(False, f"not sent: {why}",
                            failure_class=FailureClass.VALIDATION)
    try:
        pid = int(proposal_id)
        card = await _card_row(pid)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"not sent: could not read the card ({explain(exc)})")
    if card is None or card["status"] != "pending":
        return ActionResult(False, "not sent: the card is not pending",
                            failure_class=FailureClass.VALIDATION)
    key = f"card:{pid}"
    if await idempotency.claim("tutorco.notify_card", key) != idempotency.CLAIMED:
        return ActionResult(False, f"already notified for card #{pid}")
    params = wa_approvals._params_of(card["params"])
    approvable, _ = wa_approvals.eligibility(
        str(card["action"]), params, dry_ok=card.get("dry_run_ok"))
    code = None
    try:
        if approvable:
            code = await wa_approvals.create_code(
                pid, str(card["action"]), card["expires_at"])
            if code is None:
                raise RuntimeError("no code could be stored")
        body = compose_card(pid, str(card["action"]), params, card["expires_at"], code)
    except Exception as exc:  # noqa: BLE001
        await wa_approvals.retire(pid)
        await idempotency.release("tutorco.notify_card", key)
        return ActionResult(False, f"not sent: {explain(exc)}")
    number, _ = owner_number()
    buttons = False
    ok, detail = False, ""
    if code is not None and content_sid() is not None:
        # Buttons first. A template the sandbox or WhatsApp refuses, or any other
        # failure, falls back to the plain YES/NO text so the card is never lost.
        ok, detail = await send_whatsapp_card(
            number or "", compose_card(pid, str(card["action"]), params,
                                       card["expires_at"], code, buttons=True),
            pid, code)
        buttons = ok
        if not ok:
            log.warning("card #%s: buttons not sent (%s), sending plain text",
                        pid, detail[:120])
    if not ok:
        ok, detail = await send_whatsapp(number or "", body)
    if not ok:
        await wa_approvals.retire(pid)
        await idempotency.release("tutorco.notify_card", key)
        return ActionResult(False, f"send failed: {detail}")
    await idempotency.settle("tutorco.notify_card", key, detail)
    await audit("whatsapp.card_sent", str(pid), {
        "sid": detail, "approvable_here": code is not None, "buttons": buttons})
    return ActionResult(True, f"told the owner about card #{pid}"
                              + ((" with buttons and a one-time code" if buttons
                                  else " with a one-time code") if code
                                 else " (read-only)"),
                        evidence={"sid": detail, "approvable_here": code is not None,
                                  "buttons": buttons},
                        rollback_token=str(pid))


async def notify_card_rollback(token: str) -> ActionResult:
    from ...kernel import wa_approvals
    try:
        pid = int(token)
    except (TypeError, ValueError):
        return ActionResult(True, "nothing to roll back")
    await wa_approvals.retire(pid)
    return ActionResult(True, "the card's code is retired; a message already "
                              "delivered cannot be unsent")


# ------------------------------------------------------------------ check

async def check_owner_whatsapp() -> Observation:
    _, why = owner_number()
    why = why or sender_problem()
    if why:
        return Observation("tutorco_owner_whatsapp", Health.UNKNOWN, None, "messages",
                           f"not configured: {why}")
    try:
        sent = await _count_last_hour()
    except Exception as exc:  # noqa: BLE001
        return Observation("tutorco_owner_whatsapp", Health.UNKNOWN, None, "messages",
                           f"could not read the outbox: {explain(exc)}")
    return Observation(
        "tutorco_owner_whatsapp", Health.OK, float(sent), "messages",
        f"{sent} of {hourly_cap()} owner messages used this hour "
        f"(read {datetime.now(UTC):%Y-%m-%d %H:%M} UTC)")
