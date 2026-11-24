"""A short WhatsApp ping when something genuinely bad happens: a health check that
was OK on its last reading and is now FAILING.

Off unless `WHATSAPP_ALERTS=on` (the kill switch; default off). Never for UNKNOWN
(a check that cannot run is noise here, invariant 7 keeps it on the board), never
for "degraded", never twice for the same reading.

How it stays inside CLAUDE.md:
  * read-only scan of `observations`; the only send is the Tier 1 action
    `tutorco.notify_alert`, reached through `executor.execute` (invariant 1);
  * `ATLAS_WATCH_ONLY` sends nothing (the job logs "would send"; the executor
    refuses too);
  * dedupe: one ping per failing reading (`tutorco_owner_outbox` unique slot), plus a
    per-hour cap (`WHATSAPP_ALERTS_PER_HOUR`, default 3);
  * quiet hours (`QUIET_HOURS`) and a closed 24 h window (the owner has not
    written in 24 h, so free-form text would be refused) go to ONE Slack note
    instead, the same fallback the daily summary uses;
  * the words are built in code from the check's name. No stored detail text is
    sent (invariant 4).
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from . import wa_format
from .contracts import Severity
from .executor import ExecutionRequest, execute, watch_only

log = logging.getLogger("atlas.whatsapp_alerts")

ACTION = "tutorco.notify_alert"
ALERT_KIND = "alert"
DEFAULT_PER_HOUR = 3
LOOKBACK = timedelta(minutes=30)     # a flip older than this is no longer news
WINDOW = timedelta(hours=24)         # free-form WhatsApp is only allowed this long
                                     # after the owner last wrote


def enabled() -> bool:
    return os.getenv("WHATSAPP_ALERTS", "off").strip().lower() in (
        "1", "on", "true", "yes")


def per_hour() -> int:
    try:
        return max(0, int(os.getenv("WHATSAPP_ALERTS_PER_HOUR", str(DEFAULT_PER_HOUR))))
    except ValueError:
        return DEFAULT_PER_HOUR


def ref_of(observation_id: int) -> str:
    return f"obs{int(observation_id)}"


def category_of(property_name: str, check_name: str = "") -> wa_format.Category:
    """The tag for a failed check, by code from the property's own name: a module
    name maps to its category; anything else is a system-health matter."""
    for word in str(property_name or "").lower().replace("_", " ").replace(".", " ").split():
        cat = wa_format.category_for_module(word)
        if cat is not wa_format.Category.SYSTEM:
            return cat
    return wa_format.Category.SYSTEM


def say(property_name: str, check_name: str) -> str:
    """The one human line. Built from names only."""
    name = " ".join(f"{property_name} {check_name}".replace("_", " ")
                    .replace(".", " ").split())
    return wa_format.tag(
        category_of(property_name, check_name),
        f"\u26A0\uFE0F Heads up! {name} just stopped working. I'm keeping an eye on it "
        "\U0001F440 You can see it in the portal.")


async def _flips(now: datetime) -> list[dict[str, Any]]:
    """Checks whose newest reading is FAILING and whose reading before it was OK."""
    from .registry import REGISTRY
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT c.id AS check_id, p.name AS property, c.name AS check_name,
                  o1.id AS observation_id, o1.at AS at
             FROM checks c
             JOIN properties p ON p.id = c.property_id
             JOIN LATERAL (SELECT id, health, at FROM observations
                            WHERE check_id = c.id ORDER BY at DESC LIMIT 1) o1 ON TRUE
             JOIN LATERAL (SELECT health FROM observations
                            WHERE check_id = c.id ORDER BY at DESC OFFSET 1 LIMIT 1) o2
                  ON TRUE
            WHERE c.enabled AND o1.health = 'failing' AND o2.health = 'ok'
              AND o1.at > $1
            ORDER BY o1.at""", now - LOOKBACK)
    out = []
    for r in rows or []:
        module, name = str(r["property"]), str(r["check_name"])
        if module in REGISTRY.modules and f"{module}.{name}" not in REGISTRY.checks:
            continue                                 # a retired check
        out.append(dict(r))
    return out


async def _last_inbound() -> datetime | None:
    from .state import fetch_one
    row = await fetch_one("SELECT max(claimed_at) AS at FROM effect_claims "
                          "WHERE action = 'whatsapp.inbound'")
    return None if row is None else row["at"]


def quiet_now() -> bool:
    from .comms import _quiet_now
    return _quiet_now()


async def window_open(now: datetime) -> bool:
    """Whether the owner wrote within 24 h. Unreadable counts as closed."""
    try:
        at = await _last_inbound()
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts: last inbound unreadable: %s", type(exc).__name__)
        return False
    return at is not None and now - at <= WINDOW


async def sent_last_hour() -> int:
    from .state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM tutorco_owner_outbox WHERE kind = $1 AND "
        "status <> 'failed' AND created_at > now() - interval '1 hour'", ALERT_KIND)
    return int(row["n"]) if row else 0


async def _slack_note(flip: dict[str, Any], why: str) -> bool:
    """One Slack note per reading when WhatsApp cannot be used (quiet hours, closed
    window). comms applies its own quiet-hours rule to it."""
    from . import idempotency
    from .comms import Outbound, notify
    key = ref_of(flip["observation_id"])
    if await idempotency.claim("whatsapp.alert.slack", key) != idempotency.CLAIMED:
        return False
    try:
        used = await notify(Outbound(
            title="A check just failed",
            body=say(flip["property"], flip["check_name"]) + f" ({why})",
            severity=Severity.HIGH, kind="alert", topic="alerts"))
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts: the Slack note failed: %s", type(exc).__name__)
        return False
    return bool(used)


async def run(now: datetime | None = None) -> dict[str, Any]:
    """One pass. Returns what it did, for the log."""
    if not enabled():
        return {"enabled": False}
    now = now or datetime.now(UTC)
    try:
        flips = await _flips(now)
    except Exception as exc:  # noqa: BLE001
        log.warning("alerts: checks unreadable: %s", type(exc).__name__)
        return {"error": "unreadable"}
    if not flips:
        return {"flips": 0}
    if watch_only():
        log.info("whatsapp alerts: ATLAS_WATCH_ONLY is on, would tell Miro about %d "
                 "failing check(s)", len(flips))
        return {"watch_only": True, "would_send": len(flips)}
    sent = slacked = 0
    quiet, open_ = quiet_now(), await window_open(now)
    for flip in flips:
        if quiet or not open_:
            why = "quiet hours" if quiet else "WhatsApp is closed until you message me"
            if await _slack_note(flip, why):
                slacked += 1
            continue
        try:
            if await sent_last_hour() >= per_hour():
                log.info("whatsapp alerts: hourly cap reached")
                break
        except Exception as exc:  # noqa: BLE001
            log.warning("alerts: cap unreadable: %s", type(exc).__name__)
            break
        result = await execute(ExecutionRequest(
            action=ACTION, params={"observation_id": flip["observation_id"]},
            reason="a health check went from OK to failing"))
        if result.ok:
            sent += 1
        elif result.detail.startswith("send failed") and "may have been delivered" not in \
                result.detail and await _slack_note(flip, "WhatsApp refused the message"):
            # A certain refusal (e.g. the window closed early): one Slack note per
            # reading instead. A send that may have been delivered is not doubled.
            slacked += 1
    return {"flips": len(flips), "sent": sent, "slack_notes": slacked}
