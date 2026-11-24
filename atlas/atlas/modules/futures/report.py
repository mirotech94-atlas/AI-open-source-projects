"""What the futures engine tells the owner, and how little: one daily close
summary, one weekly scoreboard. No per-trade cards -- intraday cards are
noise. Every figure is read from ATLAS's own tables at the time of writing;
what cannot be read is UNKNOWN, never zero. "Paper, not advice." once.

A model may write one short reading of the weekly numbers (`explain`); it
is shown as the model's words, every figure it cites must be one it was
given, and the scoreboard stands without it.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import propfirm
from .backtest import START_EQUITY, Fill, Signal, Trade

log = logging.getLogger("atlas.futures.report")

PAPER = "Paper, not advice."
HEAD = "*Futures paper engine*"
NOTE_MAX = 240
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![0-9]*[A-Za-z])")


def _json(value: Any) -> Any:
    import json
    return json.loads(value) if isinstance(value, str) else value


def _usd(v: float) -> str:
    return f"{'-' if v < 0 else '+'}${abs(v):,.2f}"


# ------------------------------------------------------------------ trades

def trade_from_row(r: dict[str, Any]) -> Trade:
    """A ledger row as the simulator's Trade, for the metrics and the
    prop-firm arithmetic (closed rows only)."""
    sig = Signal(str(r["engine"]), int(r["side"]), r["signal_ts"], float(r["entry_price"]),
                 float(r["stop"]), None if r["target"] is None else float(r["target"]),
                 None, 0, str(r.get("reason") or ""))
    entry = Fill("entry", r["entry_ts"], float(r["entry_price"]), float(r["entry_price"]),
                 0.0, "signal")
    exit_ = Fill("exit", r["exit_ts"], float(r["exit_price"]), float(r["exit_price"]),
                 0.0, str(r["exit_reason"] or ""))
    return Trade(str(r["engine"]), int(r["side"]), int(r["contracts"]), sig,
                 r["session_date"], entry, exit_, float(r["stop"]),
                 None if r["target"] is None else float(r["target"]), 0.0,
                 float(r["pnl"]), None if r["r_multiple"] is None else float(r["r_multiple"]),
                 int(r["id"]))


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    done = [r for r in rows if r["status"] == "closed" and r["pnl"] is not None]
    if not done:
        return {"trades": 0, "win_rate": None, "expectancy_r": None, "net_pnl": 0.0}
    wins = sum(1 for r in done if float(r["pnl"]) > 0)
    rs = [float(r["r_multiple"]) for r in done if r["r_multiple"] is not None]
    return {"trades": len(done), "win_rate": wins / len(done) * 100,
            "expectancy_r": sum(rs) / len(rs) if rs else None,
            "net_pnl": sum(float(r["pnl"]) for r in done)}


def _pct(v: float | None) -> str:
    return "?" if v is None else f"{v:.0f}%"


def _r(v: float | None) -> str:
    return "?" if v is None else f"{v:+.2f}R"


# ------------------------------------------------------------------- daily

async def daily_summary(session: date, symbol: str, now: datetime | None = None) -> str:
    """One message at the close: trades taken, P&L, which engines fired,
    the breaker, the data. Never raises past UNKNOWN."""
    from . import book, data
    now = now or datetime.now(UTC)
    rows = await book.trades("WHERE session_date = $1", session)
    st = await book.state()
    read = await data.last_read(symbol, "5m")
    done = [r for r in rows if r["status"] == "closed"]
    pnl = sum(float(r["pnl"]) for r in done if r["pnl"] is not None)
    head = f"{symbol} paper · {session:%a %-d %b}"
    if not rows:
        line = f"{head}: no trades — no engine fired inside its killzones."
    else:
        fired = Counter(str(r["engine"]) for r in rows)
        reasons = Counter(str(r["exit_reason"]) for r in done if r["exit_reason"])
        line = (f"{head}: {len(rows)} trade{'s' if len(rows) != 1 else ''}, "
                f"{_usd(pnl)} · fired: "
                + ", ".join(f"{e}×{n}" if n > 1 else e for e, n in fired.most_common())
                + (" · exits: " + ", ".join(f"{k} {n}" for k, n in reasons.most_common())
                   if reasons else "")
                + (f" · {len(rows) - len(done)} still open" if len(rows) > len(done)
                   else "") + ".")
    if st is None:
        line += " Book: not started yet."
    else:
        # Equity at that session's close: the start plus every trade closed
        # in or before it (the state's own figure is the book's now).
        before = await book.trades("WHERE status = 'closed' AND session_date <= $1", session)
        eq = START_EQUITY + sum(float(r["pnl"]) for r in before if r["pnl"] is not None)
        started = datetime.fromisoformat(st["started_at"])
        line += (f" Equity ${eq:,.2f} at the close ({(eq / START_EQUITY - 1) * 100:+.2f}% "
                 f"since {started:%-d %b}).")
        if st.get("halted"):
            when = st.get("breaker_tripped_at")
            line += (" Breaker: TRIPPED"
                     + (f" at {datetime.fromisoformat(when):%a %-d %b %H:%M} UTC"
                        if when else "")
                     + " — futures is paused until you resume it.")
        else:
            used = float(st["peak"]) - float(st["equity"])
            line += f" Breaker: clear (${max(used, 0):,.0f} of $2,000 from the peak now)."
        if st.get("day_halted") and st.get("session") == session.isoformat():
            line += " Daily stop hit: no more entries that session."
    if read is None:
        line += " Data: UNKNOWN — the feed has not been read."
    elif not read["ok"]:
        line += (f" Data: UNKNOWN — the last read failed at "
                 f"{read['at']:%H:%M} UTC ({read['detail'][:60]}).")
    else:
        gaps = _json(read["gaps"]) or []
        newest = read["newest"]
        line += (f" Data: bars to {newest:%H:%M} UTC" if newest else " Data: no bars")
        line += f", read {read['at']:%H:%M} UTC"
        if gaps:
            line += f", {len(gaps)} gap{'s' if len(gaps) != 1 else ''} inside CME hours"
        line += "."
    return f"{line} {PAPER}"


# ------------------------------------------------------------------ weekly

async def latest_backtest() -> dict[str, Any] | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT at, symbol, interval, results FROM futures_backtests ORDER BY id DESC LIMIT 1")
    if row is None:
        return None
    return {"at": row["at"], "symbol": row["symbol"], "interval": row["interval"],
            "results": _json(row["results"])}


async def scoreboard(now: datetime | None = None, *, with_model: bool = True) -> str:
    """Forward book vs backtest expectation, a per-engine table, the
    prop-firm check, the disclaimer once."""
    from . import book
    now = now or datetime.now(UTC)
    st = await book.state()
    rows = await book.trades()
    bt = await latest_backtest()
    lines: list[str] = []
    if st is None:
        lines.append("Not started: $50,000 paper, 1 MNQ contract a signal, first tick on "
                     "the next 15-minute mark inside CME hours.")
    else:
        started = datetime.fromisoformat(st["started_at"])
        fwd = _stats(rows)
        eq = float(st["equity"])
        lines.append(
            f"Forward book since {started:%-d %b}: {fwd['trades']} closed, "
            f"{_pct(fwd['win_rate'])} won, {_r(fwd['expectancy_r'])} avg, "
            f"{_usd(fwd['net_pnl'])} → equity ${eq:,.2f} "
            f"({(eq / START_EQUITY - 1) * 100:+.2f}%)"
            + (", breaker TRIPPED — paused" if st.get("halted") else "") + ".")
        week = [r for r in rows if r["status"] == "closed"
                and r["exit_ts"] is not None and r["exit_ts"] > now - timedelta(days=7)]
        w = _stats(week)
        lines.append(f"This week: {w['trades']} closed, {_pct(w['win_rate'])} won, "
                     f"{_usd(w['net_pnl'])}.")
    if bt is None:
        lines.append("Backtest expectation: UNKNOWN — none stored yet.")
        bt_engines: dict[str, Any] = {}
    else:
        c = bt["results"]["combined"]
        lines.append(
            f"Backtest ({bt['symbol']} {bt['interval']}, {bt['results']['sessions']} "
            f"sessions to {bt['results']['last_ts'][:10]}, run {bt['at']:%-d %b}): "
            f"{c['trades']} trades, {_pct(c['win_rate'])} won, {_r(c['expectancy_r'])} "
            f"avg, PF {c['profit_factor']}, {_usd(c['net_pnl'])}, max DD "
            f"${c['max_drawdown']:,.0f}; halves {_r(bt['results']['halves']['first']['expectancy_r'])} "
            f"then {_r(bt['results']['halves']['second']['expectancy_r'])}.")
        bt_engines = bt["results"].get("engines", {})
    by_engine: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_engine.setdefault(str(r["engine"]), []).append(r)
    names = sorted(set(by_engine) | set(bt_engines))
    if names:
        table = ["```engine            forward: n  win   P&L  · backtest: n  win     R"]
        for e in names:
            f = _stats(by_engine.get(e, []))
            b = bt_engines.get(e) or {}
            table.append(
                f"{e:<17} {f['trades']:>11} {_pct(f['win_rate']):>5} {_usd(f['net_pnl']):>8}"
                f"  · {b.get('trades', '?'):>10} {_pct(b.get('win_rate')):>5} "
                f"{_r(b.get('expectancy_r')):>6}")
        table.append("```")
        lines.append("\n".join(table))
    closed = [trade_from_row(r) for r in rows if r["status"] == "closed"
              and r["pnl"] is not None and r["exit_ts"] is not None]
    if closed:
        lines.append(propfirm.line(propfirm.check(closed, propfirm.rules(), START_EQUITY))
                     .replace("Prop-firm check", "Prop-firm check on the forward book", 1))
    if bt is not None and bt["results"].get("propfirm"):
        lines.append(propfirm.line(bt["results"]["propfirm"])
                     .replace("Prop-firm check", "Prop-firm check on the backtest", 1))
    if with_model and st is not None:
        note = await explain("\n".join(lines))
        if note:
            lines.append(f"A model's reading (its words, not a measurement): {note}")
    lines.append(PAPER)
    return "\n".join(lines)


async def weekly_section(now: datetime | None = None) -> str:
    """The weekly report's paragraph. Never raises: a failure is an UNKNOWN
    paragraph, never a missing one and never a zero."""
    now = now or datetime.now(UTC)
    try:
        return f"{HEAD}\n{await scoreboard(now)}"
    except Exception as exc:
        log.exception("futures: weekly section failed")
        return (f"{HEAD}\nCouldn't read it this week ({type(exc).__name__}), so every "
                f"figure is UNKNOWN — not zero. {PAPER}")


# ------------------------------------------------------------------- model

SYSTEM = """You read a PAPER futures strategy scoreboard and write ONE short line \
(under 240 characters) saying what the numbers show. Nothing is traded and nothing \
you write is advice. Cite only figures exactly as they appear in the block; add no \
number of your own. Answer with the line and nothing else."""


def figures_in(text: str) -> list[str]:
    return _NUMBER.findall(text)


def cited(note: str, block: str) -> bool:
    """Every number in the note appears in the block, at the precision the
    note wrote it."""
    allowed = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", block)]
    for tok in figures_in(note):
        places = len(tok.split(".", 1)[1]) if "." in tok else 0
        n = float(tok)
        if not any(abs(round(abs(a), places) - n) < 1e-9 for a in allowed):
            return False
    return True


async def explain(block: str) -> str | None:
    """One line from a model about the numbers, or None: no model, or a
    line that invents a figure or runs long. The block is wrapped as data."""
    try:
        from ...kernel.llm import Message, Need, think
        from ...kernel.permissions import wrap_untrusted
        prompt = (wrap_untrusted(block, "the futures paper scoreboard, ATLAS's own tables")
                  + "\n\nWrite the one line now.")
        reply = await think(Need.REASON, SYSTEM, [Message("user", prompt)], max_tokens=200)
    except Exception as exc:                            # noqa: BLE001
        log.info("futures: no model note (%s)", type(exc).__name__)
        return None
    text = (getattr(reply, "text", "") or "").strip().replace("\n", " ")
    if not text or len(text) > NOTE_MAX or not cited(text, block):
        return None
    return text
