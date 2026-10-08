"""The futures module's own clock: the paper tick, the daily summary and
the weekly scoreboard, kept once-only by the kernel's own machinery.

`MODULE.setup()` starts `loop()` as one tracked background task
(`kernel.background`, as the mail module's sweeps do). Every minute it asks
`kernel.missed.due` which window of each job is owed, claims it once in
`schedule_runs` (`kernel.occurrence.claim_once`), runs it, and records the
outcome -- the same records the scheduler's own jobs write, so a restart
never runs a window twice and a missed one is 'skipped' with its reason.

  futures_tick    every 15 minutes inside CME hours (Sun 18:00 - Fri 17:00
                  ET, minus the 17:00-18:00 maintenance hour). Reads the
                  newest 5-minute bars, stores them, feeds the closed ones
                  to the paper book. Missed-run policy skip-stale with a
                  12-minute grace: a tick that is late is not run late; the
                  next one reads the same bars anyway.
  futures_daily   weekdays 16:15 ET, after the flat-at-cash-close rule:
                  one summary message, topic `markets` (#trades). skip-stale
                  (grace 6 h): yesterday's summary is noise today.
  futures_weekly  Fridays 16:45 ET: refresh the backtest on the last 60
                  days, store it, send the scoreboard. run-once: one
                  scoreboard on recovery, never one per missed week.

The breaker (−$2,000 from the book's peak) pauses the `futures` module
through `kernel/pause.py` and sends one line. While paused, ticks still
read data and let open paper positions exit by their rules, but book
nothing new. Only Miro resumes it.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime, time, timedelta

from ...kernel.contracts import Severity
from ...kernel.missed import JobPolicy, Missed, Window
from . import backtest, book, data, report
from .engines import TZ, in_cme_hours

log = logging.getLogger("atlas.futures.job")

MODULE_NAME = "futures"
TOPIC = "markets"
INTERVAL = "5m"
TICK_RANGE = "5d"
BACKTEST_RANGE = "60d"
HISTORY_DAYS = 10             # bars fed to the book (features need history)
TICK_MINUTES = 15
DAILY_AT = time(16, 15)
WEEKLY_AT = time(16, 45)
WEEKLY_DAY = 4                # Friday
LOOP_SLEEP_S = 60.0
DAILY_KIND = "futures_daily"
WEEKLY_KIND = "futures_weekly"
BREAKER_BY = "atlas:futures-breaker"


def symbol() -> str:
    """FUTURES_SYMBOL: MNQ=F (default) or NQ=F. Anything else is MNQ."""
    s = os.getenv("FUTURES_SYMBOL", "").strip().upper()
    return s if s in data.SYMBOLS else "MNQ=F"


# ----------------------------------------------------------------- windows

def tick_windows(now: datetime, horizon: timedelta) -> list[Window]:
    """Every 15-minute mark inside CME hours, up to `now` (ET), oldest
    first, keyed by its ET minute."""
    local = now.astimezone(TZ)
    step = timedelta(minutes=TICK_MINUTES)
    floor = local.replace(minute=local.minute - local.minute % TICK_MINUTES, second=0,
                          microsecond=0)
    out: list[Window] = []
    at = floor - step * int(horizon / step)
    while at <= floor:
        if at >= local - horizon and in_cme_hours(at):
            out.append(Window(at.strftime("%Y-%m-%dT%H:%M"), at))
        at += step
    return out


def daily_windows(now: datetime, horizon: timedelta) -> list[Window]:
    local = now.astimezone(TZ)
    out: list[Window] = []
    for back in range(horizon.days, -1, -1):
        d = local.date() - timedelta(days=back)
        at = datetime.combine(d, DAILY_AT, tzinfo=TZ)
        if d.weekday() < 5 and at <= local:
            out.append(Window(d.isoformat(), at))
    return out


def weekly_windows(now: datetime, horizon: timedelta) -> list[Window]:
    local = now.astimezone(TZ)
    latest = local.date() - timedelta(days=(local.weekday() - WEEKLY_DAY) % 7)
    out: list[Window] = []
    for back in range(horizon.days // 7, -1, -1):
        d = latest - timedelta(weeks=back)
        at = datetime.combine(d, WEEKLY_AT, tzinfo=TZ)
        if at <= local:
            out.append(Window(d.isoformat(), at))
    return out


POLICIES: dict[str, JobPolicy] = {
    "futures_tick": JobPolicy(
        "futures_tick", Missed.SKIP_STALE, windows=tick_windows,
        grace=timedelta(minutes=12), horizon=timedelta(hours=2),
        why="a late tick is not run late; the next one reads the same bars"),
    "futures_daily": JobPolicy(
        "futures_daily", Missed.SKIP_STALE, windows=daily_windows,
        grace=timedelta(hours=6),
        why="the day's close summary, that evening; yesterday's is noise today"),
    "futures_weekly": JobPolicy(
        "futures_weekly", Missed.RUN_ONCE, windows=weekly_windows,
        horizon=timedelta(weeks=4),
        why="one scoreboard on recovery, never one per missed week"),
}


# -------------------------------------------------------------------- jobs

async def _paused() -> bool:
    """Whether `futures` is paused. Unreadable pause state is paused (fail
    closed): nothing new is booked until it can be read."""
    from ...kernel import pause
    try:
        return await pause.active_for(MODULE_NAME) is not None
    except pause.PauseUnreadable:
        return True


async def _tell(title: str, body: str, severity: Severity = Severity.INFO,
                kind: str = "digest") -> list[str]:
    from ...kernel import comms
    return await comms.notify(comms.Outbound(title=title, body=body, severity=severity,
                                             topic=TOPIC, kind=kind))


async def _trip_breaker(book_: backtest.Book) -> None:
    from ...kernel import pause
    when = book_.breaker_tripped_at
    reason = (f"paper breaker: −$2,000 from the book's peak at "
              f"{when:%a %-d %b %H:%M} UTC" if when else "paper breaker")
    try:
        await pause.pause(MODULE_NAME, BREAKER_BY, reason)
    except Exception:
        log.exception("futures: the breaker could not pause the module")
    await _tell("Futures paper breaker",
                f"Paper breaker tripped: the futures book is $2,000 under its peak "
                f"(equity ${book_.equity:,.2f}). Flat, and `futures` is paused until "
                f"`/atlas resume futures`. Paper, not advice.",
                Severity.HIGH, "alert")


async def tick(window: Window, now: datetime | None = None) -> str:
    """One paper tick: read, store, advance the book. Returns a status."""
    now = now or datetime.now(UTC)
    sym = symbol()
    try:
        feed = await data.fetch(sym, INTERVAL, TICK_RANGE)
    except data.Unreadable as exc:
        await data.record_read(sym, INTERVAL, None, error=exc)
        return f"unreadable: {exc}"
    await data.upsert(feed)
    await data.record_read(sym, INTERVAL, feed)
    bars = await data.load(sym, INTERVAL, now - timedelta(days=HISTORY_DAYS))
    if not bars:
        return "no bars"
    paused = await _paused()
    done = await book.advance(sym, INTERVAL, backtest.spec(sym), bars, paused=paused,
                              now=now)
    if done.breaker_now:
        await _trip_breaker(done.book)
    return (f"{done.processed} bars, {len(done.opened)} opened, {len(done.closed)} closed"
            + (", paused" if paused else ""))


async def _already(kind: str, title: str) -> bool | None:
    """Whether a report of `kind` with this exact title (it names the
    session or the week) already went out. None when it cannot be read:
    then nothing is sent (a second copy is the worse mistake)."""
    from ...kernel.state import fetch_one
    try:
        row = await fetch_one("SELECT 1 AS x FROM reports WHERE kind = $1 AND title = $2 "
                              "LIMIT 1", kind, title)
    except Exception as exc:                            # noqa: BLE001
        log.warning("futures: reports unreadable (%s)", type(exc).__name__)
        return None
    return row is not None


async def _record(kind: str, title: str, body: str, used: list[str]) -> None:
    from ...kernel.state import execute_sql
    try:
        await execute_sql(
            "INSERT INTO reports (kind, title, body, delivered_via) VALUES ($1, $2, $3, $4)",
            kind, title, body, ",".join(used) or None)
    except Exception as exc:                            # noqa: BLE001
        log.warning("futures: %s went out but was not stored: %s", kind, exc)


async def daily(window: Window, now: datetime | None = None) -> str:
    """The close summary for the window's session date."""
    now = now or datetime.now(UTC)
    day = date.fromisoformat(window.key)
    title = f"Futures paper — {day:%a %-d %b %Y}"
    sent = await _already(DAILY_KIND, title)
    if sent is None or sent:
        return "already sent" if sent else "reports unreadable"
    body = await report.daily_summary(day, symbol(), now)
    used = await _tell(title, body)
    await _record(DAILY_KIND, title, body, used)
    return "sent"


async def refresh_backtest(now: datetime | None = None) -> dict | None:
    """The last 60 days of 5-minute bars, stored, measured, kept."""
    from ...kernel.state import execute_sql
    now = now or datetime.now(UTC)
    sym = symbol()
    try:
        feed = await data.fetch(sym, INTERVAL, BACKTEST_RANGE)
    except data.Unreadable as exc:
        await data.record_read(sym, INTERVAL, None, error=exc)
        return None
    await data.upsert(feed)
    await data.record_read(sym, INTERVAL, feed)
    if not feed.bars:
        return None
    results = backtest.summary(feed.bars, backtest.spec(sym), gaps=len(feed.gaps))
    import json
    await execute_sql(
        """INSERT INTO futures_backtests (symbol, interval, first_ts, last_ts, bars, results)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
        sym, INTERVAL, feed.bars[0].ts, feed.bars[-1].ts, len(feed.bars),
        json.dumps(results))
    return results


async def weekly(window: Window, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    day = date.fromisoformat(window.key)
    title = f"Futures paper — week to {day:%a %-d %b %Y}"
    sent = await _already(WEEKLY_KIND, title)
    if sent is None or sent:
        return "already sent" if sent else "reports unreadable"
    await refresh_backtest(now)
    body = await report.weekly_section(now)
    used = await _tell(title, body)
    await _record(WEEKLY_KIND, title, body, used)
    return "sent"


JOBS = {"futures_tick": tick, "futures_daily": daily, "futures_weekly": weekly}


async def run_due(now: datetime | None = None) -> dict[str, str]:
    """Run whichever windows are owed right now, each claimed once."""
    from ...kernel import missed, occurrence
    from .._http import explain
    now = now or datetime.now(UTC)
    local = now.astimezone(TZ)
    out: dict[str, str] = {}
    for name, policy in POLICIES.items():
        w = await missed.due(policy, local)
        if w is None:
            continue
        if not await occurrence.claim_once(name, w.key, w.scheduled_for):
            continue
        try:
            status = await JOBS[name](w, now)
        except Exception as exc:
            await occurrence.fail(name, w.key, explain(exc))
            log.exception("futures %s %s failed", name, w.key)
            out[name] = f"failed: {explain(exc)}"
            continue
        await occurrence.done(name, w.key)
        log.info("futures %s %s: %s", name, w.key, status)
        out[name] = status
    return out


async def loop(sleep_s: float = LOOP_SLEEP_S) -> None:
    """The module's clock. Errors are logged and the clock goes on."""
    while True:
        try:
            await run_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("futures loop tick failed")
        await asyncio.sleep(sleep_s)
