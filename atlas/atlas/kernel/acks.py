"""Acknowledged alerts: "I know about this. Stop telling me until it changes."

Miro, 2026-09-24: "there should be an acknowledge button in Slack when they
send messages so they don't send the same one again." Before this, the only
brake on a repeating alert was page()'s once-per-title-per-day window, so a
known problem (a CloudHost balance he had already said he would top up) paged him
every day and re-filed a tracking-issue card every time the last one expired.

An ack mutes ONE check, and only while nothing about it has changed:
  * `until` passes (ACK_DAYS), or
  * the check's health moves (degraded -> failing is new information), or
  * the check reads OK again — a later failure is a new problem.
It mutes; it never hides. The check still runs, is still recorded, and still
shows on the dashboard. Only the telling stops.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.acks")

ACK_DAYS = 7


async def latest_health(key: str) -> str | None:
    """The newest recorded health for `module.check`, or None if unknown."""
    module, _, check = key.partition(".")
    row = await fetch_one(
        """SELECT o.health FROM observations o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id
            WHERE p.name = $1 AND c.name = $2
            ORDER BY o.at DESC LIMIT 1""", module, check)
    return row["health"] if row else None


async def ack(key: str, via: str, *, days: int = ACK_DAYS) -> str | None:
    """Record the acknowledgement against the check's CURRENT health.
    Returns that health, or None when the check is unknown."""
    health = await latest_health(key)
    if health is None:
        return None
    until = datetime.now(UTC) + timedelta(days=days)
    await execute_sql(
        """INSERT INTO acks (key, at, until, health, via)
           VALUES ($1, now(), $2, $3, $4)
           ON CONFLICT (key) DO UPDATE
             SET at = now(), until = $2, health = $3, via = $4""",
        key, until, health, via)
    log.info("acknowledged %s (%s) until %s via %s", key, health, until, via)
    return health


async def filter_acked(findings: list) -> list:
    """Drop findings Miro has acknowledged, and retire acks that no longer
    apply. One small query when there are no acks, which is most cycles."""
    try:
        acks = await fetch_all("SELECT key, until, health FROM acks")
    except Exception:
        # If acks cannot be read, tell him. Repeating a known alert is a
        # nuisance; swallowing a new one is the failure this must not cause.
        log.exception("acks unreadable; alerting as if none exist")
        return findings
    if not acks:
        return findings
    now = datetime.now(UTC)
    live: dict[str, str] = {}
    for a in acks:
        current = await latest_health(a["key"])
        if a["until"] <= now or current in (None, "ok") or current != a["health"]:
            await execute_sql("DELETE FROM acks WHERE key = $1", a["key"])
            log.info("ack on %s lapsed (%s)", a["key"],
                     "expired" if a["until"] <= now else f"now {current}")
            continue
        live[a["key"]] = a["health"]
    if not live:
        return findings
    kept = [f for f in findings
            if live.get(f.check) != f.observation.health.value]
    if len(kept) != len(findings):
        log.info("muted %d acknowledged finding(s)", len(findings) - len(kept))
    return kept
