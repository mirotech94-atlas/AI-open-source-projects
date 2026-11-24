"""`tutorco.call_owner`: one Twilio Voice call to the owner's own number. Tier 1.

The number is `TUTORCO_OWNER_WHATSAPP`, read from the environment, and the action has
no parameter that could change it. The only parameters are a `reason` from the
closed list in `kernel/owner_calls.py` and an `event` id; the words spoken are
fixed text there (TwiML `<Say>`), built from the reason alone.

Off unless `OWNER_CALLS=on`. One call per event, ever (an `effect_claims` row keyed
by the event); at most `CALL_MAX_PER_DAY` a New York day. A refused call frees its
slot; one that may have gone out (a timeout, a 5xx) keeps it, because a second
call cannot be taken back. Credentials come from the environment only.

A WhatsApp sandbox number cannot place a voice call. The caller id is
`OWNER_CALL_FROM`, else `TWILIO_FROM`; a `whatsapp:` sender is refused here
instead of failing at Twilio.

Rollback: a call cannot be un-rung; it marks the event as handled and says so.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from ...kernel import idempotency, owner_calls
from ...kernel.contracts import ActionResult, FailureClass
from ...kernel.state import fetch_one
from .._http import explain
from .notify import API, owner_number

log = logging.getLogger("atlas.tutorco.calls")

CLAIM = "tutorco.call_owner"
_EVENT = re.compile(r"[A-Za-z0-9_-]{1,64}")
UNCERTAIN_MARK = " (outcome unknown, the call may have gone out)"


def _from_number() -> str:
    return (os.getenv("OWNER_CALL_FROM", "").strip()
            or os.getenv("TWILIO_FROM", "").strip())


def _problem(reason: Any, event: Any) -> str | None:
    if str(reason) not in owner_calls.SCRIPTS:
        return f"unknown reason {str(reason)[:40]!r}"
    if not _EVENT.fullmatch(str(event or "")):
        return "an event id is required"
    if not owner_calls.enabled():
        return "OWNER_CALLS is off"
    _, why = owner_number()
    if why:
        return why
    if not (os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN")):
        return "Twilio credentials are not configured"
    sender = _from_number()
    if not sender:
        return "no voice number (OWNER_CALL_FROM or TWILIO_FROM) is configured"
    if sender.lower().startswith("whatsapp:"):
        return ("the sender is a WhatsApp number, which cannot place a voice call "
                "(set OWNER_CALL_FROM to a voice-enabled number)")
    return None


async def _calls_today() -> int:
    """Calls claimed since midnight in New York (a refused call released its row)."""
    from ...kernel.whatsapp_daily import NY
    midnight = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        "SELECT count(*) AS n FROM effect_claims WHERE action = $1 AND claimed_at >= $2",
        CLAIM, midnight.astimezone(UTC))
    return int(row["n"]) if row else 0


async def place_call(to: str, twiml: str) -> tuple[bool, str]:
    """(True, CallSid) or (False, reason). The only phone write in ATLAS; reached
    from `call_owner` only, through the executor."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    try:
        async with httpx.AsyncClient(timeout=20, auth=(sid, token)) as client:
            r = await client.post(f"{API}/{sid}/Calls.json", data={
                "From": _from_number(), "To": to, "Twiml": twiml})
    except Exception as exc:  # noqa: BLE001
        return False, explain(exc)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        return False, f"HTTP {r.status_code} from api.twilio.com (code {data.get('code')})"
    return True, str(data.get("sid") or "")


async def call_owner_dry(reason: str, event: str) -> ActionResult:
    why = _problem(reason, event)
    if why:
        return ActionResult(False, f"would not call: {why}")
    try:
        if await _calls_today() >= owner_calls.max_per_day():
            return ActionResult(False, "would not call: the daily call limit is reached")
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"would not call: cannot read today's calls "
                                   f"({explain(exc)})")
    return ActionResult(True, "would ring the owner's number once with a fixed "
                              "message", evidence={"script": owner_calls.SCRIPTS[reason]})


async def call_owner(reason: str, event: str) -> ActionResult:
    why = _problem(reason, event)
    if why:
        return ActionResult(False, f"not placed: {why}",
                            failure_class=FailureClass.VALIDATION)
    event = str(event)
    try:
        if await _calls_today() >= owner_calls.max_per_day():
            return ActionResult(False, "not placed: the daily call limit is reached")
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"not placed: cannot read today's calls "
                                   f"({explain(exc)})")
    state = await idempotency.claim(CLAIM, event)
    if state in (idempotency.SENT, idempotency.IN_DOUBT):
        return ActionResult(True, f"already called for {event}; not called again")
    if state != idempotency.CLAIMED:
        return ActionResult(False, "not placed: the call could not be recorded")
    number, _ = owner_number()
    ok, detail = await place_call(number or "", owner_calls.twiml(reason))
    if not ok:
        await idempotency.finish(CLAIM, event, detail)
        doubt = idempotency.uncertain(detail)
        return ActionResult(False, f"not placed: {detail}"
                                   f"{UNCERTAIN_MARK if doubt else ''}")
    await idempotency.settle(CLAIM, event, detail)
    return ActionResult(True, f"rang the owner: {reason} {event}",
                        evidence={"call_sid": detail, "reason": reason},
                        rollback_token=event)


async def call_owner_rollback(token: str) -> ActionResult:
    return ActionResult(True, "a phone call cannot be un-rung; nothing to undo")
