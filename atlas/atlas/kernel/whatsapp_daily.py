"""The one WhatsApp message a day: what each of the six agents did, and whether
any needs help. 8:00 PM New York by default.

A collector-style read (`agent_report.read`, the same reads as the Agency map
and the "agents" WhatsApp question) plus ONE send through the existing owner
WhatsApp path: the Tier 1 action `tutorco.notify_daily` (modules/tutorco/notify.py),
reached only through `executor.execute`. Nothing here talks to Twilio.

Rules kept here (CLAUDE.md):
  * off unless `WHATSAPP_DAILY=on`; hour `WHATSAPP_DAILY_HOUR` (default 20), NY time;
  * `ATLAS_WATCH_ONLY` forbids the send: the job logs "would send" and does nothing
    else (invariant 1; the executor refuses it too);
  * a source that cannot be read is a grey line ("I couldn't read Bea today"), never
    zero or green (invariants 6 and 7); the message carries the New York time of the
    read; one plain line per agent with a status light (agent_report.light);
  * the lines are counts and check names built by `agent_report` from our own
    tables: no inbound text (email, buyer or chat) reaches the message (invariant 4);
  * one send per New York date: the scheduler claims the date once, and the owner
    outbox holds a (kind, date) dedupe slot, so a restart or a second tick sends
    nothing more (invariant 5: no send on timeout, ever);
  * unsent (watch-only, down, failed) the scheduler retries every 30 min until
    midnight New York; still at most one successful send per date;
  * a failure that may still have delivered (timeout, 5xx) is never retried: the
    window is closed and the outbox row stays "sending" (in doubt), so no second
    message goes out; a certain failure (4xx, refused connection) is retried;
  * a failed send falls back to ONE Slack note per date that says WhatsApp failed. WhatsApp
    (sandbox included) only takes free-form text within 24 h of the owner's last
    message to Atlas, so this is expected now and then.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from . import agent_report, wa_format
from .agent_activity import NY
from .contracts import Severity, Tier
from .executor import ExecutionRequest, execute, watch_only
from .registry import REGISTRY

log = logging.getLogger("atlas.whatsapp_daily")

ACTION = "tutorco.notify_daily"
UNCERTAIN_MARK = " (outcome unknown, may have been delivered)"  # = tutorco.notify's
DEFAULT_HOUR = 20
_NOTED: set[str] = set()     # New York dates already given a Slack note
MAX_CHARS = 1500            # one WhatsApp message; longer is split, never cut


def enabled() -> bool:
    return os.getenv("WHATSAPP_DAILY", "off").strip().lower() in (
        "1", "on", "true", "yes")


def hour() -> int:
    """`WHATSAPP_DAILY_HOUR`: 0-23, New York time."""
    try:
        return max(0, min(23, int(os.getenv("WHATSAPP_DAILY_HOUR", str(DEFAULT_HOUR)))))
    except ValueError:
        return DEFAULT_HOUR


def ny_day(now: datetime) -> str:
    return now.astimezone(NY).date().isoformat()


def split(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Whole lines only; a line longer than `limit` is cut as a last resort."""
    parts: list[str] = []
    cur = ""
    for line in text.splitlines():
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


async def digest(now: datetime | None = None, limit: int = MAX_CHARS) -> list[str]:
    """The message as one or more parts, each under `limit`. Reads only."""
    now = now or datetime.now(UTC)
    try:
        data = await agent_report.read(now)
    except Exception as exc:  # noqa: BLE001
        log.warning("daily report unreadable: %s", type(exc).__name__)
        data = {"agents": [], "errors": {"the agents' records": "unreadable"}}
    text = agent_report.compose(data, now)
    head, _, body = text.partition("\n")
    head = wa_format.tag(wa_format.Category.AGENCY, "\U0001F4CA " + head)
    # Leave room for the "(i/n)" the parts carry so the limit holds after it.
    parts = split(body, limit - len(head) - 12)
    if len(parts) == 1:
        return [f"{head}\n{parts[0]}"]
    return [f"{head} ({i}/{len(parts)})\n{p}" for i, p in enumerate(parts, 1)]


def _ready() -> str | None:
    decision = REGISTRY.decisions.get(ACTION)
    if ACTION not in REGISTRY.actions or decision is None or not decision.allowed:
        return f"{ACTION} is not in the catalog (tutorco module not loaded)"
    if decision.tier is not Tier.AUTO:
        return f"{ACTION} is not Tier 1; the daily report runs nothing that needs Miro"
    return None


async def _slack_note(why: str, parts: list[str]) -> bool:
    """One Slack note: WhatsApp failed, and today's report so it is not lost."""
    from .comms import Outbound, notify
    try:
        used = await notify(Outbound(
            title="WhatsApp daily report failed",
            body=f"The WhatsApp daily report was not delivered ({why}). "
                 "Here it is:\n\n" + "\n\n".join(parts),
            severity=Severity.NORMAL, kind="digest", topic="brief"))
    except Exception as exc:  # noqa: BLE001
        log.warning("daily report: the Slack note failed too: %s", type(exc).__name__)
        return False
    return bool(used)


async def run(now: datetime | None = None) -> dict[str, Any]:
    """One day's send. Called once per New York date by the scheduler."""
    now = now or datetime.now(UTC)
    day = ny_day(now)
    if watch_only():
        parts = await digest(now)
        log.info("whatsapp daily %s: ATLAS_WATCH_ONLY is on, would send %d message(s), "
                 "%d chars", day, len(parts), sum(map(len, parts)))
        return {"day": day, "sent": False, "watch_only": True, "would_send": len(parts)}
    if (why := _ready()) is not None:
        log.warning("whatsapp daily %s not run: %s", day, why)
        return {"day": day, "sent": False, "error": why}
    result = await execute(ExecutionRequest(
        action=ACTION, params={"day": day},
        reason=f"the daily agent report Miro asked for at {hour()}:00 New York"))
    if result.ok:
        return {"day": day, "sent": True, "detail": result.detail}
    log.warning("whatsapp daily %s failed: %s", day, result.detail)
    # Twilio may have delivered it (timeout, 5xx): the window must not be retried.
    doubt = UNCERTAIN_MARK in result.detail
    if day in _NOTED:                 # retries every 30 min: one Slack note a day
        return {"day": day, "sent": False, "error": result.detail,
                "uncertain": doubt, "slack_note": False}
    noted = await _slack_note(result.detail, await digest(now))
    if noted:
        _NOTED.add(day)
    return {"day": day, "sent": False, "error": result.detail,
            "uncertain": doubt, "slack_note": noted}
