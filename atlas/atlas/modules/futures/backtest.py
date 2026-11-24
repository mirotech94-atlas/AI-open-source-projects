"""The simulator: one step function shared by the backtest and the paper
forward book, with the honesty guards written into it.

* Signals come from closed bars and fill at the NEXT bar's open (never the
  close they were seen on), plus slippage.
* Stop first: a bar that touches both stop and target is a loss.
* Gap-through fills at the open, for the stop and for the target.
* The trailing stop moves on closed bars and binds on the next bar.
* Costs per side from `contracts.json`: slippage ticks and a commission.
* Flat at the cash close; a daily loss stop and a trailing breaker exactly
  as the forward book applies them, so a backtest over the same bars and
  the forward book reach the same trades (`tests/test_futures_forward.py`).

Nothing here is fitted: the "halves" in `summary` are the first and second
halves of the sessions, reported so a rule that only worked in one of them
is seen for what it is. Single instrument, so no survivorship claim.
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import engines
from .engines import Bar, Prepared, Signal, ends_cash_day, session_date

SPECS = pathlib.Path(__file__).with_name("contracts.json")
START_EQUITY = 50_000.0
MAX_OPEN = 3
DAILY_STOP = 1_000.0
BREAKER = 2_000.0


@dataclass(frozen=True, slots=True)
class Spec:
    symbol: str
    tick_size: float
    point_value: float
    slippage_ticks: int
    commission_per_side: float

    @property
    def slippage(self) -> float:
        return self.slippage_ticks * self.tick_size


def specs() -> dict[str, Spec]:
    raw = json.loads(SPECS.read_text())
    return {k: Spec(k, float(v["tick_size"]), float(v["point_value"]),
                    int(v["slippage_ticks_per_side"]), float(v["commission_per_side"]))
            for k, v in raw.items() if not k.startswith("_")}


def spec(symbol: str) -> Spec:
    return specs()[symbol]


@dataclass(frozen=True, slots=True)
class Policy:
    """How a book takes and limits trades. The forward book's defaults."""

    start_equity: float = START_EQUITY
    contracts: int = 1
    max_open: int = MAX_OPEN
    per_engine_open: int = 1
    per_engine_session: int = engines.MAX_PER_SESSION
    daily_stop: float | None = DAILY_STOP        # $ realised, per session
    breaker: float | None = BREAKER              # $ below the marked peak
    # After a breaker, a backtest goes on next session from a reset peak (a
    # measurement); the forward book stays halted until the owner resumes it.
    resume_next_session: bool = True
    engines: tuple[str, ...] | None = None       # None: all


@dataclass(frozen=True, slots=True)
class Fill:
    kind: str                 # entry | exit
    bar_ts: datetime
    raw: float                # the bar price filled at
    price: float              # after slippage
    slippage: float           # points, always against the trade
    reason: str


@dataclass(slots=True)
class Position:
    engine: str
    side: int
    contracts: int
    signal: Signal
    session: date
    entry: Fill
    stop: float               # current
    stop0: float              # initial
    target: float | None
    trail_atr: float | None
    time_stop_min: int
    best: float               # best close since entry (trailing)
    trailed: bool = False
    row_id: int | None = None  # futures_trades.id, in the forward book


@dataclass(frozen=True, slots=True)
class Trade:
    engine: str
    side: int
    contracts: int
    signal: Signal
    session: date
    entry: Fill
    exit: Fill
    stop0: float
    target: float | None
    commission: float         # $ both sides
    pnl: float                # $ net
    r_multiple: float | None
    row_id: int | None = None

    @property
    def hold_min(self) -> float:
        return (self.exit.bar_ts - self.entry.bar_ts).total_seconds() / 60


@dataclass(slots=True)
class Book:
    spec: Spec
    policy: Policy
    equity: float                      # realised
    peak: float                        # of marked equity
    positions: list[Position] = field(default_factory=list)
    pending: list[Signal] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    session: date | None = None
    day_pnl: float = 0.0
    day_halted: bool = False
    day_counts: dict[str, int] = field(default_factory=dict)
    halted: bool = False               # the breaker tripped
    breaker_hits: int = 0
    cancelled: int = 0
    curve: list[tuple[datetime, float]] = field(default_factory=list)   # per bar
    session_curve: list[tuple[date, float]] = field(default_factory=list)
    # What this step did, for a caller that persists rows (the forward book).
    opened: list[Position] = field(default_factory=list)
    closed: list[Trade] = field(default_factory=list)
    breaker_tripped_at: datetime | None = None


def new_book(spec_: Spec, policy: Policy) -> Book:
    return Book(spec_, policy, policy.start_equity, policy.start_equity)


def marked(book: Book, close: float) -> float:
    return book.equity + sum(
        p.side * (close - p.entry.price) * book.spec.point_value * p.contracts
        for p in book.positions)


def _fill(spec_: Spec, side: int, kind: str, bar_ts: datetime, raw: float,
          reason: str) -> Fill:
    """Slippage always against the trade: a long buys higher and sells
    lower; a short the reverse."""
    against = 1 if (kind == "entry") == (side == 1) else -1
    return Fill(kind, bar_ts, raw, raw + against * spec_.slippage, spec_.slippage,
                reason)


def _close(book: Book, p: Position, bar_ts: datetime, raw: float, reason: str) -> Trade:
    exit_ = _fill(book.spec, p.side, "exit", bar_ts, raw, reason)
    commission = 2 * book.spec.commission_per_side * p.contracts
    pnl = (p.side * (exit_.price - p.entry.price) * book.spec.point_value * p.contracts
           - commission)
    risk = p.side * (p.entry.price - p.stop0)
    r = (p.side * (exit_.price - p.entry.price) / risk) if risk > 0 else None
    t = Trade(p.engine, p.side, p.contracts, p.signal, p.session, p.entry, exit_,
              p.stop0, p.target, commission, pnl, r, p.row_id)
    book.equity += pnl
    book.day_pnl += pnl
    book.trades.append(t)
    book.closed.append(t)
    return t


def _exit_of(p: Position, b: Bar) -> tuple[float, str] | None:
    """(raw price, reason) where a position leaves on bar b, or None."""
    s = p.side
    if s * (b.open - p.stop) <= 0:
        return b.open, "trail" if p.trailed else "stop"      # gapped through
    if p.target is not None and s * (b.open - p.target) >= 0:
        return b.open, "target"
    if s * (b.low if s == 1 else b.high) <= s * p.stop:
        return p.stop, "trail" if p.trailed else "stop"      # stop first
    if p.target is not None and s * (b.high if s == 1 else b.low) >= s * p.target:
        return p.target, "target"
    if b.end - p.entry.bar_ts >= timedelta(minutes=p.time_stop_min):
        return b.close, "time"
    if ends_cash_day(b):
        return b.close, "eod"
    return None


def _flatten(book: Book, b: Bar, reason: str) -> None:
    for p in list(book.positions):
        _close(book, p, b.ts, b.close, reason)
    book.positions.clear()
    book.cancelled += len(book.pending)
    book.pending.clear()


def step(book: Book, p: Prepared, i: int) -> None:
    """Process closed bar i: fill what was pending, manage what is open,
    apply the day stop and the breaker, then read the engines for the next
    bar. Reads bars 0..i only."""
    b = p.bars[i]
    book.opened, book.closed = [], []
    sess = p.f.session[i]
    if sess != book.session:
        if book.session is not None:
            book.session_curve.append((book.session, marked(book, p.bars[i - 1].close)))
        book.session, book.day_pnl, book.day_halted = sess, 0.0, False
        book.day_counts = {}
        if book.halted and book.policy.resume_next_session:
            book.halted, book.peak = False, marked(book, b.open)
    pol = book.policy
    # 1. Fills, at this bar's open.
    for s in book.pending:
        open_engine = sum(1 for q in book.positions if q.engine == s.engine)
        if (book.halted or book.day_halted or len(book.positions) >= pol.max_open
                or open_engine >= pol.per_engine_open):
            book.cancelled += 1
            continue
        entry = _fill(book.spec, s.side, "entry", b.ts, b.open, "signal")
        pos = Position(s.engine, s.side, pol.contracts, s, sess, entry, s.stop,
                       s.stop, s.target, s.trail_atr, s.time_stop_min, best=b.open)
        book.positions.append(pos)
        book.opened.append(pos)
    book.pending = []
    # 2. Trailing stops move on the closed bars before this one.
    prev_atr = p.f.atr[i - 1] if i > 0 else None
    for q in book.positions:
        if q.trail_atr is not None and prev_atr is not None and q.entry.bar_ts < b.ts:
            level = q.best - q.side * q.trail_atr * prev_atr
            if q.side * (level - q.stop) > 0:
                q.stop, q.trailed = level, True
    # 3. Exits on this bar.
    for q in list(book.positions):
        got = _exit_of(q, b)
        if got is not None:
            _close(book, q, b.ts, got[0], got[1])
            book.positions.remove(q)
        else:
            q.best = max(q.best, b.close) if q.side == 1 else min(q.best, b.close)
    # 4. The day stop, then the breaker, on marked equity.
    if (pol.daily_stop is not None and not book.day_halted
            and book.day_pnl <= -pol.daily_stop):
        _flatten(book, b, "daily_stop")
        book.day_halted = True
    m = marked(book, b.close)
    if pol.breaker is not None and not book.halted and m <= book.peak - pol.breaker:
        _flatten(book, b, "breaker")
        book.halted, book.breaker_hits = True, book.breaker_hits + 1
        book.breaker_tripped_at = b.end
        m = marked(book, b.close)
    book.peak = max(book.peak, m)
    book.curve.append((b.end, m))
    # 5. Signals for the next bar.
    if book.halted or book.day_halted:
        return
    for s in engines.evaluate(p, i, pol.engines):
        if any(q.engine == s.engine for q in book.pending):
            continue
        if book.day_counts.get(s.engine, 0) >= pol.per_engine_session:
            continue
        if sum(1 for q in book.positions if q.engine == s.engine) >= pol.per_engine_open:
            continue
        if len(book.positions) + len(book.pending) >= pol.max_open:
            continue
        book.pending.append(s)
        book.day_counts[s.engine] = book.day_counts.get(s.engine, 0) + 1


def run(bars: Sequence[Bar], spec_: Spec, policy: Policy) -> Book:
    p = engines.prepare(bars)
    book = new_book(spec_, policy)
    for i in range(len(p.bars)):
        step(book, p, i)
    if book.session is not None and p.bars:
        book.session_curve.append((book.session, marked(book, p.bars[-1].close)))
    return book


# ----------------------------------------------------------------- metrics

def metrics(trades: Iterable[Trade], start_equity: float) -> dict[str, Any]:
    """Trades, win rate, expectancy (R), profit factor, max drawdown ($),
    a Sharpe-like ratio on per-session P&L, average hold, net P&L, and the
    equity curve at each exit. Unknown where the trades cannot support a
    figure (never 0 standing in)."""
    ts = sorted(trades, key=lambda t: t.exit.bar_ts)
    n = len(ts)
    if n == 0:
        return {"trades": 0, "wins": 0, "win_rate": None, "expectancy_r": None,
                "profit_factor": None, "net_pnl": 0.0, "max_drawdown": 0.0,
                "sharpe": None, "avg_hold_min": None, "curve": []}
    wins = [t for t in ts if t.pnl > 0]
    losses = [t for t in ts if t.pnl <= 0]
    rs = [t.r_multiple for t in ts if t.r_multiple is not None]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    equity, peak, dd = start_equity, start_equity, 0.0
    curve = []
    by_session: dict[date, float] = {}
    for t in ts:
        equity += t.pnl
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
        curve.append((t.exit.bar_ts.isoformat(), round(equity, 2)))
        s = session_date(t.exit.bar_ts)
        by_session[s] = by_session.get(s, 0.0) + t.pnl
    daily = list(by_session.values())
    sharpe = None
    if len(daily) >= 2 and statistics.pstdev(daily) > 0:
        sharpe = statistics.mean(daily) / statistics.pstdev(daily) * math.sqrt(252)
    return {
        "trades": n, "wins": len(wins), "win_rate": round(len(wins) / n * 100, 1),
        "expectancy_r": round(statistics.mean(rs), 3) if rs else None,
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0
                          else (None if gross_win == 0 else math.inf)),
        "net_pnl": round(sum(t.pnl for t in ts), 2),
        "max_drawdown": round(dd, 2), "sharpe": round(sharpe, 2) if sharpe else sharpe,
        "avg_hold_min": round(statistics.mean(t.hold_min for t in ts), 1),
        "sessions_traded": len(by_session), "curve": curve,
    }


def _plain(m: dict[str, Any]) -> dict[str, Any]:
    out = dict(m)
    out.pop("curve", None)
    if out.get("profit_factor") == math.inf:
        out["profit_factor"] = "inf"
    return out


def summary(bars: Sequence[Bar], spec_: Spec, policy: Policy | None = None,
            *, gaps: int | None = None) -> dict[str, Any]:
    """The whole measurement, JSON-ready: the combined book under the
    forward policy, each engine alone (one contract, no stops -- its own
    curve), the two halves, and the prop-firm arithmetic. Pure."""
    from . import propfirm
    policy = policy or Policy()
    sessions = sorted({session_date(b.ts) for b in bars})
    combined = run(bars, spec_, policy)
    by_engine: dict[str, Any] = {}
    for name in engines.ENGINES:
        if policy.engines is not None and name not in policy.engines:
            continue
        alone = run(bars, spec_, Policy(start_equity=policy.start_equity,
                                        max_open=1, daily_stop=None, breaker=None,
                                        engines=(name,)))
        by_engine[name] = _plain(metrics(alone.trades, policy.start_equity))
    half = sessions[len(sessions) // 2] if sessions else None
    first = [t for t in combined.trades if half is None or t.session < half]
    second = [t for t in combined.trades if half is not None and t.session >= half]
    check = propfirm.check(combined.trades, propfirm.rules(), policy.start_equity)
    return {
        "symbol": spec_.symbol, "bars": len(bars), "sessions": len(sessions),
        "first_ts": bars[0].ts.isoformat() if bars else None,
        "last_ts": bars[-1].ts.isoformat() if bars else None,
        "gaps_in_cme_hours": gaps,
        "policy": {"start_equity": policy.start_equity, "contracts": policy.contracts,
                   "max_open": policy.max_open, "daily_stop": policy.daily_stop,
                   "breaker": policy.breaker},
        "costs": {"slippage_ticks_per_side": spec_.slippage_ticks,
                  "commission_per_side": spec_.commission_per_side,
                  "point_value": spec_.point_value},
        "combined": {**_plain(metrics(combined.trades, policy.start_equity)),
                     "breaker_hits": combined.breaker_hits,
                     "cancelled": combined.cancelled,
                     "session_curve": [(d.isoformat(), round(v, 2))
                                       for d, v in combined.session_curve]},
        "engines": by_engine,
        "halves": {"split_at": half.isoformat() if half else None,
                   "first": _plain(metrics(first, policy.start_equity)),
                   "second": _plain(metrics(second, policy.start_equity))},
        "propfirm": check,
        "notes": [
            "signals on closed bars, filled at the next bar's open plus slippage",
            "stop first when a bar touches both; gap-through fills at the open",
            "nothing fitted: the halves are the first and second halves of the sessions",
            "one instrument, chosen after a reel named it: no survivorship claim",
            "Yahoo intraday futures bars are unofficial and can have holes",
            "paper, not advice",
        ],
    }
