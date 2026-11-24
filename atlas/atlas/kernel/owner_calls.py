"""A phone call to Miro, only when something serious went unanswered or he asks.

Off unless `OWNER_CALLS=on` (the kill switch; default off). The call is the Tier 1
action `tutorco.call_owner`, reached only through `executor.execute` (invariant 1). It
rings ONE number, `TUTORCO_OWNER_WHATSAPP`, which the action cannot override: it takes
no number and no text, only a reason from the closed list below and an event id.

Two triggers:
  * escalation (this job): a WhatsApp alert for a check that went from OK to
    failing, or a pending Tier 2 card marked urgent (`evidence.urgent` true), was
    sent `CALL_ESCALATE_MINUTES` (15) ago, the owner has not written since, and
    the problem is still there. One call per event, ever.
  * on request: the owner writes "call me" in the verified owner chat. One strict
    regex (no model) turns that into the same executor call. Quiet hours do not
    stop it, because he asked.

  * sensor (`sensor_serious`): a home-sensor event rated level 3 (an unknown person or a
    repeat visitor in any mode; a vehicle or any unknown person when away) whose WhatsApp
    alert was sent at least `CALL_SENSOR_GRACE_MINUTES` (2) ago and at most 20 minutes
    ago, with no message from the owner since; or whose WhatsApp send was refused
    or never confirmed (then he is called whatever he wrote). Quiet hours do not hold it (Miro wants
    these at every hour); every other guard, the daily cap and the OWNER_CALLS switch
    included, is the same.

Limits: `CALL_MAX_PER_DAY` (3) counts every call, requested or not; quiet hours
hold an escalation (it still fires after they end, inside the lookback);
`ATLAS_WATCH_ONLY` places nothing (the job logs "would call"). A call that could
not be placed leaves one Slack note per event.

The words are fixed text in code, TwiML `<Say>` only. Nothing from the event, an
email or a check's stored text is ever read out (invariant 4); nothing secret is.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from .contracts import Severity
from .executor import ExecutionRequest, execute, preview, watch_only

log = logging.getLogger("atlas.owner_calls")

ACTION = "tutorco.call_owner"
DEFAULT_MAX_PER_DAY = 3
DEFAULT_ESCALATE_MINUTES = 15
SENSOR_WINDOW = timedelta(minutes=20)  # a sensor event older than this is not news
DEFAULT_SENSOR_GRACE_MINUTES = 2
LOOKBACK = timedelta(hours=3)        # an event older than this is no longer news

# The only things the phone ever says. Keyed by reason; a caller picks a key and
# can compose nothing.
SCRIPTS: dict[str, str] = {
    "escalation_alert": (
        "This is Atlas. A health check has failed and I have had no reply to my "
        "message. Please check the portal or reply on WhatsApp."),
    "escalation_card": (
        "This is Atlas. An urgent approval is waiting and I have had no reply to my "
        "message. Please check your messages."),
    "sensor_serious": (
        "ATLAS: the house sensors raised a serious alert. "
        "Check your WhatsApp or the portal."),
    "requested": (
        "This is Atlas. You asked me to call. I have nothing more to read out. "
        "Reply on WhatsApp, or open the portal."),
}


def twiml(reason: str) -> str:
    """The TwiML for `reason`. KeyError for anything not on the closed list."""
    return f"<Response><Say>{SCRIPTS[reason]}</Say></Response>"


def enabled() -> bool:
    return os.getenv("OWNER_CALLS", "off").strip().lower() in ("1", "on", "true", "yes")


def max_per_day() -> int:
    try:
        return max(0, int(os.getenv("CALL_MAX_PER_DAY", str(DEFAULT_MAX_PER_DAY))))
    except ValueError:
        return DEFAULT_MAX_PER_DAY


def escalate_minutes() -> int:
    try:
        return max(1, int(os.getenv("CALL_ESCALATE_MINUTES",
                                    str(DEFAULT_ESCALATE_MINUTES))))
    except ValueError:
        return DEFAULT_ESCALATE_MINUTES


def sensor_grace_minutes() -> int:
    try:
        return max(0, int(os.getenv("CALL_SENSOR_GRACE_MINUTES",
                                    str(DEFAULT_SENSOR_GRACE_MINUTES))))
    except ValueError:
        return DEFAULT_SENSOR_GRACE_MINUTES


def quiet_now() -> bool:
    from .comms import _quiet_now
    return _quiet_now()


def _spawn(coro: Any, name: str) -> Any:
    from .background import spawn
    return spawn(coro, name)


# ------------------------------------------------------------------ reading

_OBS = re.compile(r"obs\d+")


async def _still_failing(observation_id: int) -> bool:
    from .state import fetch_one
    row = await fetch_one(
        "SELECT (SELECT health FROM observations WHERE check_id = ob.check_id "
        "ORDER BY at DESC LIMIT 1) AS latest FROM observations ob WHERE ob.id = $1",
        observation_id)
    return row is not None and str(row["latest"]) == "failing"


async def _unacked_alerts(now: datetime) -> list[str]:
    """Event ids ("obs<id>") of alerts sent at least N minutes ago, inside the
    lookback, with no message from the owner since and the check still failing."""
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT o.ref FROM tutorco_owner_outbox o
            WHERE o.kind = 'alert' AND o.status = 'sent'
              AND o.created_at <= $1 AND o.created_at > $2
              AND NOT EXISTS (SELECT 1 FROM effect_claims e
                               WHERE e.action = 'whatsapp.inbound'
                                 AND e.claimed_at > o.created_at)
              AND NOT EXISTS (SELECT 1 FROM effect_claims k
                               WHERE k.action = 'tutorco.call_owner' AND k.key = o.ref)
            ORDER BY o.created_at""",
        now - timedelta(minutes=escalate_minutes()), now - LOOKBACK)
    out: list[str] = []
    for r in rows or []:
        ref = str(r["ref"])
        if _OBS.fullmatch(ref) and await _still_failing(int(ref[3:])):
            out.append(ref)
    return out


async def _unacked_cards(now: datetime) -> list[str]:
    """Event ids ("card<id>") of urgent cards still pending, told to the owner at
    least N minutes ago, with no message from him since."""
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT p.id FROM proposals p
             JOIN effect_claims c ON c.action = 'tutorco.notify_card'
                                 AND c.key = 'card:' || p.id::text
            WHERE p.status = 'pending' AND p.expires_at > now()
              AND p.evidence->>'urgent' = 'true' AND c.status = 'sent'
              AND c.claimed_at <= $1 AND c.claimed_at > $2
              AND NOT EXISTS (SELECT 1 FROM effect_claims e
                               WHERE e.action = 'whatsapp.inbound'
                                 AND e.claimed_at > c.claimed_at)
              AND NOT EXISTS (SELECT 1 FROM effect_claims k
                               WHERE k.action = 'tutorco.call_owner'
                                 AND k.key = 'card' || p.id::text)
            ORDER BY c.claimed_at""",
        now - timedelta(minutes=escalate_minutes()), now - LOOKBACK)
    return [f"card{int(r['id'])}" for r in rows or []]


async def _last_inbound() -> datetime | None:
    from .state import fetch_one
    row = await fetch_one("SELECT max(claimed_at) AS at FROM effect_claims "
                          "WHERE action = 'whatsapp.inbound'")
    return None if row is None else row["at"]


async def _unacked_sensor(now: datetime) -> list[str]:
    """Event ids ("cam<id>") of level 3 sensor alerts not yet called about, within
    the last 20 minutes:

    * sent: past the grace, with no message from the owner since (he wrote back =
      acknowledged);
    * refused (`failed`): at once; still `sending` (outcome unknown): past the
      grace. Either way whatever he wrote since: he may never have seen it, and
      the Slack note that stands in for a refused send is held during quiet
      hours, so the call is what reaches him."""
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT e.id, o.created_at AS sent_at, o.status
             FROM sensor_events e
             JOIN tutorco_owner_outbox o ON o.kind = 'sensor_alert'
                                    AND o.ref = 'cam' || e.id::text
            WHERE e.alert_level = 3 AND o.status IN ('sent', 'sending', 'failed')
              AND o.created_at <= $1 AND o.created_at > $2
              AND NOT EXISTS (SELECT 1 FROM effect_claims k
                               WHERE k.action = 'tutorco.call_owner'
                                 AND k.key = 'cam' || e.id::text)
            ORDER BY o.created_at""",
        now, now - SENSOR_WINDOW)
    grace = timedelta(minutes=sensor_grace_minutes())
    rows = [r for r in rows or [] if now - r["sent_at"] <= SENSOR_WINDOW]
    sent = [r for r in rows if r.get("status", "sent") == "sent"]
    wrote: datetime | None = None
    if sent:
        try:
            wrote = await _last_inbound()
        except Exception as exc:  # noqa: BLE001
            log.warning("owner calls: last inbound unreadable: %s", type(exc).__name__)
    out: list[str] = []
    for r in rows:
        ref = f"cam{int(r['id'])}"
        status = r.get("status", "sent")
        if ref in out:
            continue
        if status != "failed" and now - r["sent_at"] < grace:
            continue                                # a send may still be in flight
        if status == "sent" and wrote is not None and wrote > r["sent_at"]:
            continue
        out.append(ref)
    return out


# ------------------------------------------------------------------ the note

async def _slack_note(event: str, why: str) -> bool:
    """One Slack note per event when a call could not be placed."""
    from . import idempotency
    from .comms import Outbound, notify
    if await idempotency.claim("owner_call.slack", event) != idempotency.CLAIMED:
        return False
    try:
        used = await notify(Outbound(
            title="I could not phone you",
            body=f"I tried to call you ({event}) and could not: {why}",
            severity=Severity.HIGH, kind="alert", topic="alerts"))
    except Exception as exc:  # noqa: BLE001
        log.warning("owner call: the Slack note failed: %s", type(exc).__name__)
        return False
    return bool(used)


# ------------------------------------------------------------------ escalation

async def _place(reason: str, event: str, why: str) -> tuple[bool, str]:
    result = await execute(ExecutionRequest(
        action=ACTION, params={"reason": reason, "event": event}, reason=why))
    return result.ok, result.detail


async def run(now: datetime | None = None) -> dict[str, Any]:
    """One pass. Returns what it did, for the log."""
    if not enabled():
        return {"enabled": False}
    now = now or datetime.now(UTC)
    events: list[tuple[str, str]] = []
    unreadable = False
    try:
        events = [("escalation_alert", e) for e in await _unacked_alerts(now)]
        events += [("escalation_card", e) for e in await _unacked_cards(now)]
    except Exception as exc:  # noqa: BLE001
        log.warning("owner calls: events unreadable: %s", type(exc).__name__)
        unreadable, events = True, []
    try:                                # a sensor call never waits on the other reads
        events += [("sensor_serious", e) for e in await _unacked_sensor(now)]
    except Exception as exc:  # noqa: BLE001
        log.warning("owner calls: sensor events unreadable: %s", type(exc).__name__)
    if not events:
        return {"error": "unreadable"} if unreadable else {"called": 0}
    if watch_only():
        log.info("owner calls: ATLAS_WATCH_ONLY is on, would call about %d event(s)",
                 len(events))
        return {"watch_only": True, "would_call": len(events)}
    held = 0
    if quiet_now():
        # Sensor alerts are never held (any hour); the rest wait for the morning.
        held = sum(1 for reason, _ in events if reason != "sensor_serious")
        events = [(r, e) for r, e in events if r == "sensor_serious"]
        if not events:
            log.info("owner calls: quiet hours, holding %d escalation(s)", held)
            return {"quiet": True, "held": held}
    called = 0
    for reason, event in events:
        ok, detail = await _place(reason, event, "an alert or urgent card went "
                                  "unanswered")
        if ok and "already" not in detail:
            called += 1
        elif not ok:
            await _slack_note(event, detail)
    out: dict[str, Any] = {"called": called, "events": len(events)}
    if held:
        out["held"] = held
    return out


# ------------------------------------------------------------------ chat

_CALL_ME = re.compile(
    r"^\W*(?:(?:hey|hi|please|pls|atlas)[\s,]+)*"
    r"(?:(?:can|could|would|will) you\s+)?(?:please\s+)?"
    r"(?:give me a call|give me a ring|call me|ring me|phone me)"
    r"(?:[\s,]+(?:please|pls|now|asap|back|right now|right away))*\W*$",
    re.IGNORECASE)


def is_call_request(text: str) -> bool:
    """Strict and deterministic: the whole message is a request to be called."""
    return len(text or "") <= 80 and _CALL_ME.match(text or "") is not None


async def handle_call_request(message_sid: str) -> str:
    """The owner wrote "call me". Returns the one plain reply. The call itself
    goes through the executor in the background (a webhook answers fast); a
    failure after this reply leaves a Slack note. Quiet hours are not asked: he
    asked. The daily cap still is, by the action."""
    if not enabled():
        return ("\U0001F4DE Phone calls are switched off, so I can't call you. "
                "Nothing was done.")
    if watch_only():
        return ("\U0001F440 I'm in watch-only mode, so I can't place calls yet. "
                "Nothing was done.")
    event = "req" + re.sub(r"[^A-Za-z0-9]", "", message_sid or "")[:60]
    params = {"reason": "requested", "event": event}
    try:
        pre = await preview(ACTION, params)
    except Exception as exc:  # noqa: BLE001
        log.warning("owner call: preview failed: %s", type(exc).__name__)
        return "I can't call you right now: I couldn't check. Nothing was done."
    if not pre.ok:
        return f"I can't call you right now: {pre.detail}. Nothing was done."

    async def go() -> None:
        ok, detail = await _place("requested", event, "the owner asked for a call "
                                  "in the verified chat")
        if not ok:
            await _slack_note(event, detail)

    _spawn(go(), "owner_call")
    return "\U0001F4DE Calling you now! If it doesn't ring, I'll say so in Slack."
