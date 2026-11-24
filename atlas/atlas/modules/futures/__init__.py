"""Futures -- a paper strategy engine that measures whether "14 Orderflow +
SMC + ICT strategies" work on MNQ, on real price data, with no money in it.

the owner, 2026-09-29, after a reel ("We coded 14 Orderflow+SMC+ICT strategies
into a single algorithm that beats prop firms" -- NinjaTrader 8, MNQ, VWAP
signals, an equity curve per engine and combined, a live P&L, unverified):
"build this for me". ATLAS never trades or moves money (Tier 3, permanently
denied), holds no broker, no order path and no key, and nothing here is
advice. So this is the honest version:

* **Data** (`data.py`): MNQ=F / NQ=F bars from Yahoo's public chart
  endpoint, the feed `markets` reads -- 5m and 15m for 60 days, 1h for two
  years, daily longer. Closed bars only, each with its read time, stored
  idempotently in `futures_bars`. The feed is unofficial and has holes;
  gaps inside CME hours are counted and said. Unreadable is UNKNOWN.
* **Engines** (`rulebook.md`, `engines.py`): 14 deterministic rules that
  CAN be computed from OHLCV -- VWAP reclaim / reject / band fade, opening
  range 15 and 30, previous-day high/low sweep, fair value gap fill, order
  block retest, break of structure, change of character, equal highs/lows
  sweep, a volume-imbalance proxy, inside-bar breakout, an ATR trailing
  trend -- each with a stop, an R target, a time stop, killzone filters
  and a per-session cap, long and short. No model anywhere in the signal
  path.
* **Backtest** (`backtest.py`) with the guards written in: stop first on a
  bar that touches both, gap-through fills at the open, a tick of slippage
  and a commission per side from `contracts.json`, signals on closed bars
  filled on the next, nothing fitted (the halves are reported anyway),
  single instrument (no survivorship claim). Metrics and an equity curve
  per engine and combined.
* **Prop-firm check** (`propfirm.py`, `propfirm.json`): illustrative $50k
  evaluation rules, arithmetic over the paper curve only. ATLAS never
  signs up for, pays for or contacts a firm.
* **Paper forward book** (`book.py`, `job.py`): from deploy on, every 15
  minutes inside CME hours the engines run on the newest closed 5-minute
  bars through the same step function as the backtest. $50,000 paper, 1
  contract a signal, at most 3 open, −$1,000 daily stop, −$2,000 trailing
  breaker that pauses this module (`kernel/pause.py`) with one line to
  the owner. Every entry and exit is a row with its bar, price, slippage and
  reason. The forward book is the real test; the backtest is context.
* **Reporting** (`report.py`), topic `markets` (#trades): one daily close
  summary and one weekly scoreboard (`weekly_section()` for the weekly
  report: forward vs backtest, per-engine table, prop-firm result, "paper,
  not advice" once). No per-trade cards. A model may only write one line
  reading the weekly numbers, shown as its words, figures checked.

No action is declared: nothing here needs a catalog entry, and none may
be added that could reach a market. The `futures` check reads data
freshness, gaps and the breaker; it never reads the feed itself.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Check, Health, Observation, Severity
from . import job as _job
from .report import weekly_section

NAME = "futures"
FRESH_AFTER = timedelta(minutes=45)      # 3 ticks of 15 min

__all__ = ["MODULE", "weekly_section"]


def _json(value: Any) -> Any:
    import json
    return json.loads(value) if isinstance(value, str) else value


def last_cme_moment(now: datetime) -> datetime:
    """`now` inside CME hours, else the last moment that was (to the
    minute). Bounded: a weekend is under 50 hours."""
    from .engines import in_cme_hours
    at = now.replace(second=0, microsecond=0)
    for _ in range(60 * 72):
        if in_cme_hours(at):
            return at
        at -= timedelta(minutes=1)
    return at


async def check_futures(now: datetime | None = None) -> Observation:
    """Data fresh? Gaps? Breaker? From ATLAS's own tables; never the feed."""
    from ...kernel import pause
    from . import book, data
    now = now or datetime.now(UTC)
    sym = _job.symbol()
    try:
        read = await data.last_read(sym, _job.INTERVAL)
        st = await book.state()
    except Exception as exc:                            # noqa: BLE001
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"futures tables unreadable ({type(exc).__name__})")
    if read is None:
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"{sym} never read yet: the paper tick has not run")
    try:
        paused = await pause.active_for(NAME)
    except pause.PauseUnreadable:
        paused = None
    parts: list[str] = []
    health = Health.OK
    if not read["ok"]:
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"{sym} {_job.INTERVAL}: the last read failed at "
                                  f"{read['at']:%a %-d %b %H:%M} UTC "
                                  f"({str(read['detail'])[:80]}); bars are UNKNOWN",
                           evidence={"read_at": read["at"].isoformat(),
                                     "status": read["status"]})
    newest = read["newest"]
    due = last_cme_moment(now) - FRESH_AFTER
    if newest is None or newest < due or read["at"] < due:
        health = Health.DEGRADED
        parts.append(f"stale: newest bar {newest:%a %H:%M} UTC" if newest
                     else "stale: no bars in the last read")
    else:
        parts.append(f"bars to {newest:%a %H:%M} UTC")
    gaps = _json(read["gaps"]) or []
    if gaps:
        health = Health.DEGRADED
        parts.append(f"{len(gaps)} gap(s) inside CME hours in the last read "
                     f"(unofficial feed; holidays count)")
    if st is not None:
        eq = float(st["equity"])
        parts.append(f"paper equity ${eq:,.2f}, {len(st.get('positions') or [])} open")
        if st.get("halted"):
            health = Health.DEGRADED
            parts.append("breaker tripped: paused until the owner resumes")
    if paused is not None and not (st or {}).get("halted"):
        parts.append(f"paused ({paused.paused_by})")
    return Observation(
        NAME, health, float(read["bars"]), "bars",
        f"{sym} {_job.INTERVAL}: " + "; ".join(parts) + f" (read {read['at']:%H:%M} UTC; "
        f"paper, never a trade)",
        evidence={"read_at": read["at"].isoformat(),
                  "newest": newest.isoformat() if newest else None,
                  "gaps": len(gaps), "checked_at": now.isoformat(),
                  "halted": bool((st or {}).get("halted"))})


class FuturesModule:
    name = NAME
    title = "Futures paper engine (never trades)"
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ("FUTURES_SYMBOL",)
    setup_note = ""
    _task: Any = None

    async def setup(self) -> None:
        """Start the module's clock (`job.loop`) as one tracked task."""
        from ...kernel import background
        if self._task is None or self._task.done():
            self._task = background.spawn(_job.loop(), "futures paper loop")

    async def teardown(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    def checks(self):
        return (Check(NAME, check_futures, timedelta(minutes=15), Severity.INFO,
                      "the paper futures engine: data freshness, feed gaps, the "
                      "breaker -- read from ATLAS's own tables, never the feed",
                      baseline_sigma=None),)

    def actions(self):
        # None. Nothing here may reach a market; money out is Tier 3.
        return ()

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""


MODULE = FuturesModule()
