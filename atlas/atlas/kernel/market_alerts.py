"""Ivan's alerts, unattended: readings in, Slack messages to Miro out.

Every `MARKETS_ALERT_MIN` minutes (default 15), from the scheduler, off the
loop. `MARKETS_ALERTS=off` stops it.

  1. **Plan** -- the markets module reads the latest basket, SEC filings
     and Alinea readings, and Miro's own TradingView alerts not yet
     delivered (`markets.alerts.gather`), and records each event
     once in `markets_alerts` (the key is the event, so it is alerted once).
     Planned alerts past their age expire, never sent late.
  2. **Send** -- while today's cap has room, at most PER_TICK pending
     alerts go through `executor.execute` as `markets.alert` -- the one
     path: its tier check (Tier 1; `ALLOWED` is the whole list), dry run,
     rate limits, pause, watch-only and audit.

It sends Miro messages and does nothing else: there is no action here, or
anywhere in the markets module, that can trade.

Under ATLAS_WATCH_ONLY nothing is sent -- the executor would refuse it --
and the planned alerts wait, then expire.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from .contracts import FailureClass, Tier
from .executor import ExecutionRequest, execute, watch_only
from .registry import REGISTRY

log = logging.getLogger("atlas.market_alerts")

ALLOWED = ("markets.alert",)
ALERT = ALLOWED[0]
PER_TICK = 5
# The event kinds the tick sends, by key prefix; any other key is left alone.
KINDS = ("move", "filing", "insider", "alinea_trails", "tradingview")
DEFAULT_INTERVAL_MIN = 15
# A refusal that says "not now" rather than "not this one": stop the tick.
_STOP = (FailureClass.BUDGET, FailureClass.DISABLED, FailureClass.TRANSIENT)


def enabled() -> bool:
    return os.getenv("MARKETS_ALERTS", "on").strip().lower() not in (
        "0", "off", "false", "no")


def interval() -> timedelta:
    """`MARKETS_ALERT_MIN`: minutes between ticks, at least 5."""
    try:
        minutes = int(os.getenv("MARKETS_ALERT_MIN", str(DEFAULT_INTERVAL_MIN)))
    except ValueError:
        minutes = DEFAULT_INTERVAL_MIN
    return timedelta(minutes=max(5, minutes))


def _ready() -> str | None:
    decision = REGISTRY.decisions.get(ALERT)
    if ALERT not in REGISTRY.actions or decision is None or not decision.allowed:
        return f"{ALERT} is not in the catalog (markets module not loaded)"
    if decision.tier is not Tier.AUTO:
        return f"{ALERT} is not Tier 1; the alert tick runs nothing that needs Miro"
    return None


async def tick(now: datetime | None = None) -> list[str]:
    """One pass. Returns a line per thing done or not done."""
    from ..modules._http import explain
    from ..modules.markets import alerts
    if (why := _ready()) is not None:
        log.info("market alerts not run: %s", why)
        return [why]
    now = now or datetime.now(UTC)
    try:
        planned = await alerts.plan(await alerts.gather(now))
        await alerts.expire(now)
        room = alerts.daily_cap() - await alerts.claimed_today(now)
        keys = await alerts.pending(min(room, PER_TICK))
    except Exception as exc:                            # noqa: BLE001
        log.warning("market alerts: readings not planned: %s", explain(exc))
        return [f"not planned: {explain(exc)}"]
    said = [f"{planned} new event(s) planned"]
    if watch_only():
        return said + [f"ATLAS_WATCH_ONLY is on: {len(keys)} alert(s) not sent"]
    if room <= 0:
        return said + ["today's alert cap is reached"]
    for key in keys:
        if key.split(":", 1)[0] not in KINDS:
            continue
        result = await execute(ExecutionRequest(
            action=ALERT, params={"key": key},
            reason=f"a market event Miro asked to be told about ({key})"))
        said.append(f"{key}: {result.detail}")
        if not result.ok and result.failure_class in _STOP:
            break
    return said
