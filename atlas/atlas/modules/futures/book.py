"""The paper forward book: the backtester's step function, fed new bars as
they close, with every entry and exit written down.

$50,000 paper, 1 MNQ contract per signal, at most 3 open, a −$1,000 daily
loss stop and a −$2,000 trailing breaker (`backtest.Policy`). The
simulator's state lives in `futures_state` (key `book`), and every trade
is a `futures_trades` row with a `futures_fills` row per fill: the bar, the
raw price, the slippage and the reason. The forward book is the real test;
the backtest is context. Nothing here can trade: no broker, no order path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from . import engines
from .backtest import Book, Fill, Policy, Position, Spec, Trade, marked, new_book, step
from .engines import Bar, Signal

log = logging.getLogger("atlas.futures.book")

STATE_KEY = "book"
FORWARD = Policy(resume_next_session=False)


# ------------------------------------------------------------ (de)serialise

def _signal_dict(s: Signal) -> dict[str, Any]:
    return {"engine": s.engine, "side": s.side, "at": s.at.isoformat(), "ref": s.ref,
            "stop": s.stop, "target": s.target, "trail_atr": s.trail_atr,
            "time_stop_min": s.time_stop_min, "reason": s.reason}


def _signal(d: dict[str, Any]) -> Signal:
    return Signal(d["engine"], int(d["side"]), datetime.fromisoformat(d["at"]),
                  float(d["ref"]), float(d["stop"]),
                  None if d.get("target") is None else float(d["target"]),
                  None if d.get("trail_atr") is None else float(d["trail_atr"]),
                  int(d["time_stop_min"]), str(d.get("reason", "")))


def _fill_dict(f: Fill) -> dict[str, Any]:
    return {"kind": f.kind, "bar_ts": f.bar_ts.isoformat(), "raw": f.raw,
            "price": f.price, "slippage": f.slippage, "reason": f.reason}


def _fill(d: dict[str, Any]) -> Fill:
    return Fill(d["kind"], datetime.fromisoformat(d["bar_ts"]), float(d["raw"]),
                float(d["price"]), float(d["slippage"]), str(d["reason"]))


def _position_dict(p: Position) -> dict[str, Any]:
    return {"engine": p.engine, "side": p.side, "contracts": p.contracts,
            "signal": _signal_dict(p.signal), "session": p.session.isoformat(),
            "entry": _fill_dict(p.entry), "stop": p.stop, "stop0": p.stop0,
            "target": p.target, "trail_atr": p.trail_atr,
            "time_stop_min": p.time_stop_min, "best": p.best, "trailed": p.trailed,
            "row_id": p.row_id}


def _position(d: dict[str, Any]) -> Position:
    return Position(d["engine"], int(d["side"]), int(d["contracts"]),
                    _signal(d["signal"]), date.fromisoformat(d["session"]),
                    _fill(d["entry"]), float(d["stop"]), float(d["stop0"]),
                    None if d.get("target") is None else float(d["target"]),
                    None if d.get("trail_atr") is None else float(d["trail_atr"]),
                    int(d["time_stop_min"]), float(d["best"]), bool(d.get("trailed")),
                    d.get("row_id"))


def dump(book: Book, last_ts: datetime | None, started_at: datetime) -> dict[str, Any]:
    return {
        "equity": book.equity, "peak": book.peak, "halted": book.halted,
        "breaker_hits": book.breaker_hits, "cancelled": book.cancelled,
        "session": book.session.isoformat() if book.session else None,
        "day_pnl": book.day_pnl, "day_halted": book.day_halted,
        "day_counts": book.day_counts,
        "positions": [_position_dict(p) for p in book.positions],
        "pending": [_signal_dict(s) for s in book.pending],
        "last_ts": last_ts.isoformat() if last_ts else None,
        "started_at": started_at.isoformat(),
        "breaker_tripped_at": (book.breaker_tripped_at.isoformat()
                               if book.breaker_tripped_at else None),
    }


def restore(spec_: Spec, policy: Policy, d: dict[str, Any]) -> Book:
    book = new_book(spec_, policy)
    book.equity, book.peak = float(d["equity"]), float(d["peak"])
    book.halted, book.breaker_hits = bool(d["halted"]), int(d["breaker_hits"])
    book.cancelled = int(d.get("cancelled", 0))
    book.session = date.fromisoformat(d["session"]) if d.get("session") else None
    book.day_pnl, book.day_halted = float(d["day_pnl"]), bool(d["day_halted"])
    book.day_counts = {k: int(v) for k, v in (d.get("day_counts") or {}).items()}
    book.positions = [_position(p) for p in d.get("positions") or []]
    book.pending = [_signal(s) for s in d.get("pending") or []]
    if d.get("breaker_tripped_at"):
        book.breaker_tripped_at = datetime.fromisoformat(d["breaker_tripped_at"])
    return book


# ------------------------------------------------------------------- state

def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def state() -> dict[str, Any] | None:
    from ...kernel.state import fetch_one
    row = await fetch_one("SELECT value FROM futures_state WHERE key = $1", STATE_KEY)
    return _json(row["value"]) if row else None


async def _save(value: dict[str, Any]) -> None:
    from ...kernel.state import execute_sql
    await execute_sql(
        """INSERT INTO futures_state (key, value, updated_at) VALUES ($1, $2::jsonb, now())
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
        STATE_KEY, json.dumps(value))


# ------------------------------------------------------------------ ledger

async def _open_row(symbol: str, interval: str, p: Position) -> int | None:
    from ...kernel.state import execute_sql, fetch_one
    row = await fetch_one(
        """INSERT INTO futures_trades
               (symbol, interval, engine, side, contracts, signal_ts, signal_close,
                reason, session_date, status, entry_ts, entry_price, entry_slippage,
                stop, target, trail_atr, time_stop_min, commission)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'open',$10,$11,$12,$13,$14,$15,$16,0)
           ON CONFLICT (symbol, interval, engine, signal_ts) DO NOTHING RETURNING id""",
        symbol, interval, p.engine, p.side, p.contracts, p.signal.at, p.signal.ref,
        p.signal.reason, p.session, p.entry.bar_ts, p.entry.price, p.entry.slippage,
        p.stop0, p.target, p.trail_atr, p.time_stop_min)
    if row is None:
        row = await fetch_one(
            "SELECT id FROM futures_trades WHERE symbol = $1 AND interval = $2 "
            "AND engine = $3 AND signal_ts = $4", symbol, interval, p.engine, p.signal.at)
        if row is None:
            return None
    rid = int(row["id"])
    await execute_sql(
        """INSERT INTO futures_fills (trade_id, kind, bar_ts, price, raw_price, slippage,
                                      reason)
           VALUES ($1, 'entry', $2, $3, $4, $5, $6) ON CONFLICT (trade_id, kind) DO NOTHING""",
        rid, p.entry.bar_ts, p.entry.price, p.entry.raw, p.entry.slippage,
        p.entry.reason)
    return rid


async def _close_row(t: Trade) -> None:
    from ...kernel.state import execute_sql
    if t.row_id is None:
        log.warning("futures: a closed paper trade has no row (%s %s)", t.engine,
                    t.signal.at)
        return
    await execute_sql(
        """UPDATE futures_trades
              SET status = 'closed', exit_ts = $2, exit_price = $3, exit_slippage = $4,
                  exit_reason = $5, commission = $6, pnl = $7, r_multiple = $8
            WHERE id = $1 AND status = 'open'""",
        t.row_id, t.exit.bar_ts, t.exit.price, t.exit.slippage, t.exit.reason,
        t.commission, t.pnl, t.r_multiple)
    await execute_sql(
        """INSERT INTO futures_fills (trade_id, kind, bar_ts, price, raw_price, slippage,
                                      reason)
           VALUES ($1, 'exit', $2, $3, $4, $5, $6) ON CONFLICT (trade_id, kind) DO NOTHING""",
        t.row_id, t.exit.bar_ts, t.exit.price, t.exit.raw, t.exit.slippage,
        t.exit.reason)


async def trades(where: str = "", *args: Any) -> list[dict[str, Any]]:
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        f"""SELECT id, engine, side, contracts, signal_ts, session_date, status,
                   entry_ts, entry_price, stop, target, exit_ts, exit_price,
                   exit_reason, pnl, r_multiple, reason
              FROM futures_trades {where} ORDER BY entry_ts, id""", *args)
    return [dict(r) for r in rows]


# ----------------------------------------------------------------- advance

class Advanced:
    """What one advance did."""

    def __init__(self, book: Book, opened: list[Position], closed: list[Trade],
                 processed: int, breaker_now: bool) -> None:
        self.book, self.opened, self.closed = book, opened, closed
        self.processed, self.breaker_now = processed, breaker_now


async def advance(symbol: str, interval: str, spec_: Spec, bars: list[Bar], *,
                  paused: bool = False, now: datetime | None = None) -> Advanced:
    """Feed the closed bars newer than the last processed one through the
    simulator, write every fill down, save the state. Bars older than the
    last processed one are ignored (a revised history never rewrites a
    booked trade). On the very first run the history only warms the
    features: booking starts from the newest bar, never before the start.

    `paused` is the module's pause (the breaker set it, or the owner did): open
    positions still exit by their rules, nothing new is taken. When it is
    lifted the halt clears and the breaker's peak restarts from here --
    the owner's resume is the reset, and only his."""
    now = now or datetime.now(UTC)
    saved = await state()
    if saved is None:
        book = new_book(spec_, FORWARD)
        last_ts = bars[-2].ts if len(bars) >= 2 else None
        started_at = now
    else:
        book = restore(spec_, FORWARD, saved)
        last_ts = (datetime.fromisoformat(saved["last_ts"]) if saved.get("last_ts")
                   else None)
        started_at = datetime.fromisoformat(saved["started_at"])
    if paused:
        book.halted = True
    elif book.halted:
        book.halted, book.peak = False, book.equity
    p = engines.prepare(bars)
    opened: list[Position] = []
    closed: list[Trade] = []
    processed = 0
    tripped_before = book.breaker_tripped_at
    for i, b in enumerate(p.bars):
        if last_ts is not None and b.ts <= last_ts:
            continue
        step(book, p, i)
        processed += 1
        last_ts = b.ts
        ids: dict[tuple[str, datetime], int | None] = {}
        for pos in book.opened:
            pos.row_id = await _open_row(symbol, interval, pos)
            ids[(pos.engine, pos.signal.at)] = pos.row_id
            opened.append(pos)
        for t in book.closed:
            if t.row_id is None:
                # Opened and closed inside this same bar: its row was
                # written just above, after the simulator closed it.
                t = replace(t, row_id=ids.get((t.engine, t.signal.at)))
            await _close_row(t)
            closed.append(t)
    breaker_now = (book.breaker_tripped_at is not None
                   and book.breaker_tripped_at != tripped_before)
    await _save(dump(book, last_ts, started_at))
    return Advanced(book, opened, closed, processed, breaker_now)


def equity_line(book: Book, last_close: float | None) -> str:
    m = marked(book, last_close) if last_close is not None else None
    since = book.policy.start_equity
    if m is None:
        return f"equity ${book.equity:,.2f} realised (open positions unmarked)"
    return f"equity ${m:,.2f} ({(m / since - 1) * 100:+.2f}%)"
