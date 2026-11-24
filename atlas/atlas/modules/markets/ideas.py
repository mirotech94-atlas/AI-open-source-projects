"""Trade idea cards -- ATLAS suggests, Miro approves or declines, on PAPER.

Miro, 2026-09-29: "ATLAS sends trade suggestions, I approve or decline".
ATLAS never places a real trade (money out is Tier 3, permanently
denied); an idea is a card with a plain order ticket he may copy to his own
broker if he chooses, and a paper position in ATLAS's own idea book.

The loop:

1. **The scan** (`scan`, weekdays 16:30 America/New_York, after the US
   close; scheduler job `ideas`, missed-run policy skip-stale -- a stale
   idea is worthless). Over UNIVERSE (the markets WATCHLIST, SPY QQQ IWM
   DIA and the nine original SPDR sector funds) it reads daily bars from
   the public chart feed and applies DETERMINISTIC long-only rules:
     * trend pullback: close > SMA50 > SMA200, the day's low within 1% of
       SMA20, RSI14 between 40 and 50, close above the prior day's high;
     * breakout: close above the prior 20 days' high, volume over 1.5x the
       prior 20 days' average.
   Guards: no single stock within EARNINGS_GUARD trading days of earnings,
   and an unknown earnings date (FINNHUB_API_KEY unset, or not read) skips
   single stocks -- ETFs are fine; no idea on a ticker already open or
   pending (no averaging down); at most PER_DAY ideas a day and MAX_OPEN
   open or pending; none at all while the book is more than MAX_DRAWDOWN
   below its peak, or while that cannot be measured.
2. **The model** (the REASON route, Claude first) may only RANK the rule's
   candidates and add one line each citing their figures. It cannot add a
   ticker, a price or a size; a bad answer is rejected and the rule's own
   order is used, saying so. No model answering changes nothing but that.
3. **The card**: `markets.paper_idea`, Tier 2, card_only, topic `markets`
   (#trades). Size from the book: risk RISK_PCT of the book per idea, stop
   2 x ATR14 below the reference close, target 2R, at most MAX_POSITION of
   the book, whole shares. The card carries DISCLAIMER and the order ticket.
4. **Approved**, the idea is booked in the paper IDEA book ($10,000, apart
   from the playlist) at the first daily close after the approval; it
   exits at its stop or target (daily high/low; both in one bar means the
   stop, first) or at the close of its HOLD_DAYS-th trading day. Rollback
   before booking cancels it. **Declined or expired**, nothing is booked --
   silence is not consent -- but the idea's hypothetical result is tracked
   the same way, from the first close after the decision.

Every figure carries its read time; an unreadable price is unknown, never 0.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult, Severity
from . import bars as read_bars
from . import finnhub, watchlist

log = logging.getLogger("atlas.markets.ideas")

ACTION = "markets.paper_idea"
DISCLAIMER = ("Paper idea — not financial advice. ATLAS never places real trades; "
              "if you act on it, you place the trade yourself.")
TZ = ZoneInfo("America/New_York")
CLOSE_AT = time(16, 0)
SCAN_AT = time(16, 30)
START_BOOK = 10_000.0
PER_DAY = 2
MAX_OPEN = 5
RISK_PCT = 0.5            # percent of the book risked per idea
ATR_MULT = 2.0            # stop distance, in ATR14
REWARD_R = 2.0            # target, in R
MAX_POSITION = 10.0       # percent of the book per position
MAX_DRAWDOWN = 10.0       # percent below the book's peak: no new ideas
HOLD_DAYS = 20            # trading days, then out at the close
EARNINGS_GUARD = 3        # trading days either side of earnings
HISTORY_DAYS = 420        # calendar days: enough bars for SMA200
NOTE_MAX = 200
BENCH = "SPY"
INDEXES = ("SPY", "QQQ", "IWM", "DIA")
SECTORS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")
ETFS = frozenset(INDEXES + SECTORS + ("VOO",))
RULES = {"breakout": "breakout", "pullback": "trend pullback"}

SYSTEM = """You rank PAPER trade ideas that deterministic rules already \
produced. Nothing here is advice and nothing is traded. You may only order the \
candidates given and add one short line per ranked ticker explaining it from \
the figures shown (cite them exactly as shown; no other numbers). You cannot \
add a ticker, change a price, a stop, a target or a size. Any headlines or \
analyst counts are outside data, context only.

Answer with one JSON object and nothing else -- no prose, no code fence:
{"ranked": ["TICKER", ...], "notes": {"TICKER": "..."}}
"ranked" must list exactly the number of tickers asked for, best first."""


def universe() -> tuple[str, ...]:
    return tuple(dict.fromkeys(watchlist() + INDEXES + SECTORS))


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def close_time(day: date) -> datetime:
    """When a trading day's close is final (16:00 New York)."""
    return datetime.combine(day, CLOSE_AT, tzinfo=TZ)


def trading_days_between(a: date, b: date) -> int:
    """Weekdays from a to b (signed; holidays are not known, so counted)."""
    lo, hi = sorted((a, b))
    n = sum(1 for i in range(1, (hi - lo).days + 1)
            if (lo + timedelta(days=i)).weekday() < 5)
    return n if b >= a else -n


# ------------------------------------------------------------ indicators

Bars = dict[date, dict]


def _series(b: Bars) -> list[tuple[date, dict]]:
    return sorted(b.items())


def sma(values: Sequence[float], n: int) -> float | None:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(closes: Sequence[float], n: int = 14) -> float | None:
    """Wilder's RSI over all the closes given."""
    if len(closes) < n + 1:
        return None
    ch = [b - a for a, b in pairwise(closes)]
    gain = sum(max(c, 0) for c in ch[:n]) / n
    loss = sum(max(-c, 0) for c in ch[:n]) / n
    for c in ch[n:]:
        gain = (gain * (n - 1) + max(c, 0)) / n
        loss = (loss * (n - 1) + max(-c, 0)) / n
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def atr(rows: Sequence[dict], n: int = 14) -> float | None:
    """Wilder's ATR. None when any bar lacks a high or low."""
    if len(rows) < n + 1 or any(r["high"] is None or r["low"] is None for r in rows):
        return None
    tr = [max(r["high"] - r["low"], abs(r["high"] - p["close"]),
              abs(r["low"] - p["close"]))
          for p, r in pairwise(rows)]
    value = sum(tr[:n]) / n
    for t in tr[n:]:
        value = (value * (n - 1) + t) / n
    return value


@dataclass(frozen=True)
class Candidate:
    ticker: str
    rule: str                  # key of RULES
    day: date                  # the close it is read from
    close: float
    atr: float
    numbers: dict[str, float]  # the rule's figures, as shown on the card
    score: float               # the rule's own ordering, higher first


def evaluate(ticker: str, b: Bars) -> list[Candidate]:
    """The rules, over one ticker's bars. Pure."""
    rows = [r for _, r in _series(b)]
    days = [d for d, _ in _series(b)]
    if len(rows) < 201:
        return []
    closes = [r["close"] for r in rows]
    today, prev = rows[-1], rows[-2]
    a = atr(rows[-60:])
    if a is None or a <= 0:
        return []
    c = today["close"]
    out: list[Candidate] = []
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    r14 = rsi(closes[-100:])
    if (None not in (s20, s50, s200, r14, today["low"], prev["high"])
            and c > s50 > s200 and today["low"] <= s20 * 1.01
            and 40 <= r14 <= 50 and c > prev["high"]):
        out.append(Candidate(ticker, "pullback", days[-1], c, a, {
            "close": round(c, 2), "SMA20": round(s20, 2), "SMA50": round(s50, 2),
            "SMA200": round(s200, 2), "RSI14": round(r14, 1),
            "prior high": round(prev["high"], 2), "ATR14": round(a, 2)},
            score=(s50 / s200 - 1) * 100))
    prior = rows[-21:-1]
    highs = [r["high"] for r in prior]
    vols = [r["volume"] for r in prior]
    if (len(prior) == 20 and None not in highs and None not in vols
            and today["volume"] is not None):
        high20, avg20 = max(highs), sum(vols) / 20
        if avg20 > 0 and c > high20 and today["volume"] > 1.5 * avg20:
            ratio = today["volume"] / avg20
            out.append(Candidate(ticker, "breakout", days[-1], c, a, {
                "close": round(c, 2), "20d high": round(high20, 2),
                "volume": round(today["volume"]), "20d avg volume": round(avg20),
                "volume ratio": round(ratio, 2), "ATR14": round(a, 2)},
                score=10 + ratio))
    return out


# ---------------------------------------------------------------- sizing

@dataclass(frozen=True)
class Sized:
    shares: int
    stop: float
    target: float
    size_pct: float


def size(close: float, atr14: float, book: float) -> Sized | None:
    """Whole shares risking RISK_PCT of the book with a 2xATR stop, capped
    at MAX_POSITION of the book. None when not even one share fits."""
    if not (close > 0 and atr14 > 0 and book > 0):
        return None
    stop = round(close - ATR_MULT * atr14, 2)
    if stop <= 0:
        return None
    risk = close - stop
    target = round(close + REWARD_R * risk, 2)
    shares = min(math.floor(book * RISK_PCT / 100 / risk),
                 math.floor(book * MAX_POSITION / 100 / close))
    if shares < 1:
        return None
    return Sized(shares, stop, target, round(shares * close / book * 100, 2))


# ------------------------------------------------------ entries and exits

def entry(bars_: Bars, after: datetime) -> tuple[date, float] | None:
    """The first daily close strictly after `after`."""
    for d, r in _series(bars_):
        if close_time(d) > after:
            return d, r["close"]
    return None


@dataclass(frozen=True)
class Exit:
    day: date
    price: float
    reason: str               # stop | target | time


def advance(bars_: Bars, entry_day: date, stop: float, target: float) -> Exit | None:
    """Where a position bought at `entry_day`'s close exits, from the bars
    after it: the stop or the target on the day's low/high -- both in one
    bar is the stop, first; a gap below the stop exits at the open -- or
    the HOLD_DAYS-th bar's close. None: still open."""
    later = [(d, r) for d, r in _series(bars_) if d > entry_day]
    for i, (d, r) in enumerate(later, start=1):
        low = r["low"] if r["low"] is not None else r["close"]
        high = r["high"] if r["high"] is not None else r["close"]
        if low <= stop:
            px = stop if r["open"] is None else min(r["open"], stop)
            return Exit(d, px, "stop")
        if high >= target:
            px = target if r["open"] is None else max(r["open"], target)
            return Exit(d, px, "target")
        if i >= HOLD_DAYS:
            return Exit(d, r["close"], "time")
    return None


# ------------------------------------------------------------- the model

class Invalid(ValueError):
    """The model's ranking broke a rule; the rule's own order stands."""


def parse_ranking(text: str, tickers: Sequence[str], want: int,
                  data: str) -> tuple[list[str], dict[str, str]]:
    from .playlist import _cited, figures_in
    raw = (text or "").strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        raise Invalid("the answer is not one bare JSON object")

    def constant(name: str) -> None:
        raise Invalid(f"the answer holds {name}")

    try:
        obj = json.loads(raw, parse_constant=constant)
    except json.JSONDecodeError as exc:
        raise Invalid(f"the answer is not valid JSON ({exc.msg})") from None
    if not isinstance(obj, dict) or set(obj) != {"ranked", "notes"}:
        raise Invalid("the answer must have exactly 'ranked' and 'notes'")
    ranked, notes = obj["ranked"], obj["notes"]
    if not isinstance(ranked, list) or not all(isinstance(t, str) for t in ranked):
        raise Invalid("ranked is not a list of tickers")
    if len(ranked) != want or len(set(ranked)) != want:
        raise Invalid(f"ranked must name {want} distinct candidate(s)")
    stray = [t for t in ranked if t not in tickers]
    if stray:
        raise Invalid(f"{', '.join(stray)} is not a candidate")
    if not isinstance(notes, dict) or set(notes) - set(ranked):
        raise Invalid("notes must be about ranked tickers only")
    import re
    allowed = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", data)]
    out: dict[str, str] = {}
    for t in ranked:
        note = notes.get(t)
        if not isinstance(note, str) or not note.strip():
            raise Invalid(f"no note for {t}")
        note = note.strip()
        if "\n" in note or len(note) > NOTE_MAX:
            raise Invalid(f"the note for {t} is not one short line")
        bad = [n for n in figures_in(note) if not _cited(n, allowed)]
        if bad:
            raise Invalid(f"the note for {t} cites {', '.join(bad)}, not given")
        out[t] = note
    return ranked, out


def _data_block(cands: Sequence[Candidate], context: dict[str, dict]) -> str:
    rows = []
    for c in cands:
        figs = ", ".join(f"{k} {v}" for k, v in c.numbers.items())
        rows.append(f"{c.ticker} | {RULES[c.rule]} | {figs}")
        extra = context.get(c.ticker) or {}
        if extra.get("recommendation"):
            r = extra["recommendation"]
            rows.append(f"  analysts ({r['period']}): strong buy {r['strongBuy']}, "
                        f"buy {r['buy']}, hold {r['hold']}, sell {r['sell']}, "
                        f"strong sell {r['strongSell']}")
        for h in extra.get("headlines") or []:
            rows.append(f"  headline: {h}")
    return "\n".join(rows)


async def rank(cands: list[Candidate], want: int, context: dict[str, dict]
               ) -> tuple[list[Candidate], dict[str, str], str]:
    """(chosen, notes, how). The rule's order unless a model's valid ranking
    replaces it."""
    by_rule = sorted(cands, key=lambda c: -c.score)
    fallback = by_rule[:want]
    from ...kernel.llm import Message, Need, think
    from ...kernel.permissions import wrap_untrusted
    data = _data_block(cands, context)
    prompt = (wrap_untrusted(data, "chart feed and Finnhub, read by ATLAS")
              + f"\n\nRank the best {want} of these {len(cands)} candidates now.")
    try:
        reply = await think(Need.REASON, SYSTEM, [Message("user", prompt)],
                            max_tokens=800)
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return fallback, {}, f"ranked by the rule (no model answered: {explain(exc)})"
    model = f"{reply.provider}:{reply.model}"
    try:
        order, notes = parse_ranking(reply.text, [c.ticker for c in cands], want,
                                     "\n".join(_data_block([c], {}) for c in cands))
    except Invalid as exc:
        return fallback, {}, f"ranked by the rule ({model}'s answer rejected: {exc})"
    pick = {c.ticker: c for c in by_rule}
    return [pick[t] for t in order], notes, f"ranked by {model}"


# ------------------------------------------------------------- the book

@dataclass(frozen=True)
class Mark:
    equity: float | None
    peak: float | None
    drawdown: float | None     # percent below peak
    read_at: datetime


_IDEA = ("SELECT id, at, day, ticker, rule, numbers, note, ranked_by, ref_close, "
         "ref_day, read_at, atr, stop, target, shares, size_pct, book, status, "
         "card_id, decided_at, entry_day, entry_price, exit_day, exit_price, "
         "exit_reason, r_multiple, pnl FROM markets_ideas")
BOOKED = ("open", "closed")
HYPOTHETICAL = ("declined", "expired")


async def ideas(where: str = "", *args: Any) -> list[dict]:
    from ...kernel.state import fetch_all
    rows = await fetch_all(f"{_IDEA} {where} ORDER BY id", *args)
    return [dict(r) for r in rows]


def equity(rows: Iterable[dict], last: dict[str, float]) -> float | None:
    """START_BOOK + realised + open marked at `last`; None when an open
    position's price is unknown."""
    value = START_BOOK
    for r in rows:
        if r["status"] == "closed":
            value += float(r["pnl"])
        elif r["status"] == "open":
            px = last.get(r["ticker"])
            if px is None:
                return None
            value += r["shares"] * (px - float(r["entry_price"]))
    return value


async def _sync_cards() -> None:
    """Carry each card's answer onto its idea: declined / expired start
    their hypothetical track; a failed run is 'refused'."""
    from ...kernel.state import execute_sql
    await execute_sql(
        """UPDATE markets_ideas i
              SET status = CASE p.status WHEN 'declined' THEN 'declined'
                                         WHEN 'expired' THEN 'expired'
                                         ELSE 'refused' END,
                  decided_at = COALESCE(p.decided_at, p.expires_at)
             FROM proposals p
            WHERE p.id = i.card_id AND i.status = 'carded'
              AND p.status IN ('declined', 'expired', 'failed', 'suppressed')""")


async def settle(b: dict[str, Bars]) -> None:
    """Book approved ideas at the first close after approval, start the
    hypothetical track of declined/expired ones, and exit what hit its stop,
    target or time. Only ATLAS's own paper rows change."""
    from ...kernel.state import execute_sql
    for r in await ideas("WHERE status IN ('approved', 'declined', 'expired') "
                         "AND entry_day IS NULL"):
        got = b.get(r["ticker"])
        after = r["decided_at"]
        if not got or after is None:
            continue
        e = entry(got, after)
        if e is None:
            continue
        # Each write re-states the status it read, as a literal: a row that
        # changed meanwhile (a rollback cancelled it) is left alone, and only
        # an 'approved' row can ever become 'open' (fail closed).
        if r["status"] == "approved":
            await execute_sql(
                "UPDATE markets_ideas SET entry_day = $2, entry_price = $3, "
                "status = 'open' WHERE id = $1 AND status = 'approved' "
                "AND entry_day IS NULL", r["id"], e[0], e[1])
        else:
            await execute_sql(
                "UPDATE markets_ideas SET entry_day = $2, entry_price = $3 "
                "WHERE id = $1 AND status IN ('declined', 'expired') "
                "AND entry_day IS NULL", r["id"], e[0], e[1])
    for r in await ideas("WHERE status IN ('open', 'declined', 'expired') "
                         "AND entry_day IS NOT NULL AND exit_day IS NULL"):
        got = b.get(r["ticker"])
        if not got:
            continue
        x = advance(got, r["entry_day"], float(r["stop"]), float(r["target"]))
        if x is None:
            continue
        risk = float(r["entry_price"]) - float(r["stop"])
        pnl = r["shares"] * (x.price - float(r["entry_price"]))
        await execute_sql(
            "UPDATE markets_ideas SET exit_day = $2, exit_price = $3, exit_reason = $4, "
            "r_multiple = $5, pnl = $6, "
            "status = CASE status WHEN 'open' THEN 'closed' ELSE status END "
            "WHERE id = $1 AND exit_day IS NULL AND entry_day IS NOT NULL "
            "AND status IN ('open', 'declined', 'expired')",
            r["id"], x.day, x.price, x.reason,
            (x.price - float(r["entry_price"])) / risk if risk > 0 else None, pnl)


async def mark(day: date, last: dict[str, float], read_at: datetime) -> Mark:
    """Today's book value, its peak and drawdown, stored once a day."""
    from ...kernel.state import execute_sql, fetch_one
    value = equity(await ideas("WHERE status IN ('open', 'closed')"), last)
    if value is not None:
        await execute_sql(
            "INSERT INTO markets_idea_marks (day, equity, spy, read_at) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (day) DO UPDATE SET "
            "equity = EXCLUDED.equity, spy = EXCLUDED.spy, read_at = EXCLUDED.read_at",
            day, value, last.get(BENCH), read_at)
    row = await fetch_one("SELECT max(equity) AS peak FROM markets_idea_marks")
    peak = max(float(row["peak"]), START_BOOK) if row and row["peak"] else START_BOOK
    dd = (peak - value) / peak * 100 if value is not None else None
    return Mark(value, peak, dd, read_at)


# -------------------------------------------------------------- the scan

async def read_all(syms: Iterable[str], end: date) -> tuple[dict[str, Bars], datetime]:
    syms = list(dict.fromkeys(syms))
    got = await asyncio.gather(*(read_bars(s, end - timedelta(days=HISTORY_DAYS), end)
                                 for s in syms))
    return {s: g for s, g in zip(syms, got, strict=True) if g}, datetime.now(UTC)


async def _earnings_ok(c: Candidate) -> tuple[bool, str]:
    if c.ticker in ETFS:
        return True, ""
    if not finnhub.enabled():
        return False, "earnings date unknown (FINNHUB_API_KEY not set)"
    dates = await finnhub.earnings(c.ticker, c.day - timedelta(days=10),
                                   c.day + timedelta(days=14))
    if dates is None:
        return False, "earnings date unknown (Finnhub not read)"
    near = [d for d in dates if abs(trading_days_between(c.day, d)) <= EARNINGS_GUARD]
    if near:
        return False, f"earnings {near[0].isoformat()} within {EARNINGS_GUARD} trading days"
    return True, ""


async def _record_scan(day: date, status: str, detail: str, *, candidates: Any = None,
                       raised: int = 0) -> None:
    from ...kernel.state import execute_sql
    await execute_sql(
        "INSERT INTO markets_idea_scans (day, status, detail, finnhub, candidates, raised) "
        "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (day) DO NOTHING",
        day, status, detail, finnhub.enabled(), json.dumps(candidates or []), raised)


def card_text(p: dict[str, Any]) -> str:
    """What the card carries. Pure, from the params alone."""
    figs = ", ".join(f"{k} {v}" for k, v in (p.get("numbers") or {}).items())
    t, n = p.get("ticker"), p.get("shares")
    ref, stop, target = (float(p.get(k) or 0) for k in ("ref_close", "stop", "target"))
    lines = [
        f"BUY {t} — {RULES.get(str(p.get('rule')), p.get('rule'))}: {figs}",
        (f"Entry reference: close {ref:.2f} of {p.get('ref_day')} (read "
         f"{p.get('read_at')}) · stop {stop:.2f} (2×ATR14) · target {target:.2f} (2R)"),
        (f"Size: {n} shares ≈ {float(p.get('size_pct') or 0):.1f}% of the paper idea "
         f"book (${float(p.get('book') or 0):,.0f}; risk {RISK_PCT}% of it)"),
    ]
    if p.get("note"):
        lines.append(str(p["note"]))
    lines.append(f"Order ticket (for your own broker, if you choose): BUY {n} {t} "
                 f"limit {ref:.2f}, stop {stop:.2f}, target {target:.2f}")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def vet(p: dict[str, Any]) -> str | None:
    try:
        if p.get("side") != "BUY":
            return "long ideas only"
        if p.get("ticker") not in universe():
            return f"{p.get('ticker')!r} is not in the idea universe"
        ref, stop, target = (float(p[k]) for k in ("ref_close", "stop", "target"))
        shares, pct = int(p["shares"]), float(p["size_pct"])
        if not all(math.isfinite(x) for x in (ref, stop, target, pct)):
            return "a figure is not finite"
        if not 0 < stop < ref < target:
            return "stop < reference < target does not hold"
        if shares < 1 or shares != p["shares"]:
            return "shares must be a whole number of at least 1"
        if pct > MAX_POSITION + 1e-9:
            return f"size over {MAX_POSITION:.0f}% of the book"
    except (KeyError, TypeError, ValueError):
        return "a figure is missing or not a number"
    return None


async def scan(day: date) -> str:
    """One day's scan: settle the book, then raise at most PER_DAY cards.
    Returns the recorded status. Once a day (markets_idea_scans.day)."""
    from ...kernel.state import fetch_one
    if await fetch_one("SELECT day FROM markets_idea_scans WHERE day = $1", day):
        return "exists"
    await _sync_cards()
    held = {r["ticker"] for r in await ideas(
        "WHERE status IN ('carded', 'approved', 'open', 'declined', 'expired') "
        "AND exit_day IS NULL")}
    b, read_at = await read_all(set(universe()) | held | {BENCH}, day)
    await settle(b)
    last = {s: g[max(g)]["close"] for s, g in b.items() if g}
    m = await mark(day, last, read_at)
    if m.drawdown is None:
        await _record_scan(day, "paused", "the book's value is unknown (a held price "
                           "could not be read), so no new ideas")
        return "paused"
    if m.drawdown > MAX_DRAWDOWN:
        await _record_scan(day, "paused", f"the book is {m.drawdown:.1f}% below its "
                           f"peak (over {MAX_DRAWDOWN:.0f}%), so no new ideas")
        return "paused"
    busy = {r["ticker"] for r in await ideas(
        "WHERE status IN ('carded', 'approved', 'open')")}
    today = await fetch_one("SELECT count(*) AS n FROM markets_ideas WHERE day = $1", day)
    slots = min(PER_DAY - int(today["n"]), MAX_OPEN - len(busy))
    if slots <= 0:
        await _record_scan(day, "full", f"{len(busy)} open or pending; cap {MAX_OPEN}")
        return "full"
    cands, skipped = [], []
    for sym in universe():
        if sym not in b:
            skipped.append(f"{sym}: bars unreadable")
            continue
        for c in evaluate(sym, b[sym]):
            if c.day != max(b[sym]) or c.ticker in busy:
                continue
            ok, why = await _earnings_ok(c)
            if not ok:
                skipped.append(f"{c.ticker}: {why}")
                continue
            if size(c.close, c.atr, m.equity) is None:
                skipped.append(f"{c.ticker}: one share is over {MAX_POSITION:.0f}% "
                               f"of the book")
                continue
            cands.append(c)
    best: dict[str, Candidate] = {}
    for c in cands:                 # one idea per ticker: the stronger rule
        if c.ticker not in best or c.score > best[c.ticker].score:
            best[c.ticker] = c
    cands = list(best.values())
    listed = [{"ticker": c.ticker, "rule": c.rule, **c.numbers} for c in cands]
    if not cands:
        await _record_scan(day, "none", "; ".join(skipped)[:2000] or "no setup today",
                           candidates=listed)
        return "none"
    context: dict[str, dict] = {}
    if finnhub.enabled():
        for c in cands:
            if c.ticker in ETFS:
                continue
            context[c.ticker] = {
                "headlines": await finnhub.headlines(c.ticker, c.day - timedelta(days=3),
                                                     c.day),
                "recommendation": await finnhub.recommendation(c.ticker)}
    chosen, notes, how = await rank(cands, min(slots, len(cands)), context)
    raised = 0
    for c in chosen:
        if await _raise(day, c, notes.get(c.ticker, ""), how, m.equity, read_at):
            raised += 1
    await _record_scan(day, "raised", f"{how}; skipped: {'; '.join(skipped)[:1500]}",
                       candidates=listed, raised=raised)
    return "raised"


# ------------------------------------------------- a TradingView alert
#
# Miro's own chart alerts land in `tradingview_alerts` (tradingview.py) and
# become one factual Slack line each. This runs the SAME idea rules on that
# one ticker, once per alert, and hands back one line for that Slack
# message: a paper idea card if a rule fired and the caps allow, else why
# not. Deterministic: no model ranks a single candidate. The card carries
# "Triggered by your TradingView alert ...". Nothing here trades.

CHECK_MAX = 80


def bare_ticker(ticker: str) -> str | None:
    """"NASDAQ:NVDA" -> "NVDA"; a futures or odd symbol -> None (the rules
    are for the watchlist's stocks and ETFs)."""
    sym = ticker.rsplit(":", 1)[-1].strip().upper()
    return sym if re.fullmatch(r"[A-Z]{1,6}", sym) else None


async def check_alert(alert_id: int, ticker: str, name: str = "",
                      price: float | None = None, at: datetime | None = None,
                      now: datetime | None = None) -> str:
    """Ivan's one-line verdict on a stored TradingView alert, recorded on
    the row (`ivan_checked_at`, `ivan_note`) so it runs once. Never raises:
    a failure reads as "could not check", never as "no setup"."""
    from ...kernel.state import execute_sql, fetch_one
    now = now or datetime.now(UTC)
    # The claim is the UPDATE itself: two ticks in flight cannot both run the
    # rules for one alert. The loser reads the winner's note (or, for the
    # moment between claim and note, an empty line).
    claimed = await fetch_one(
        "UPDATE tradingview_alerts SET ivan_checked_at = $2 WHERE id = $1 "
        "AND ivan_checked_at IS NULL RETURNING id", alert_id, now)
    if claimed is None:
        row = await fetch_one("SELECT ivan_note FROM tradingview_alerts WHERE id = $1",
                              alert_id)
        return str((row or {}).get("ivan_note") or "")
    try:
        note = await _check_alert(ticker, _plain(name), price, at, now)
    except Exception as exc:  # noqa: BLE001
        log.warning("alert %s: Ivan could not check %s (%s)", alert_id, ticker,
                    type(exc).__name__)
        note = f"Ivan could not check {ticker} right now (unknown, not a no)."
    await execute_sql("UPDATE tradingview_alerts SET ivan_note = $2 WHERE id = $1",
                      alert_id, note[:400])
    return note


def _plain(name: str) -> str:
    """The alert's own name, made safe for one Slack line and the card: no
    control characters or line breaks (a crafted name must not fake a
    second line), and Slack's markup characters escaped."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(name or "")).strip()
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            [:CHECK_MAX])


async def _check_alert(ticker: str, name: str, price: float | None,
                       at: datetime | None, now: datetime) -> str:
    from ...kernel.state import fetch_one
    sym = bare_ticker(ticker)
    if sym is None:
        return f"Ivan checked: {ticker} is outside the idea rules (stocks and ETFs only)."
    day = now.astimezone(TZ).date()
    b, read_at = await read_all({sym, BENCH}, day)
    if sym not in b or not b[sym]:
        return f"Ivan checked: {sym}'s bars could not be read, so unknown."
    held = await ideas("WHERE status IN ('carded', 'approved', 'open')")
    if any(r["ticker"] == sym for r in held):
        return f"Ivan checked: {sym} is already carded or held in the paper book."
    last = {s: g[max(g)]["close"] for s, g in b.items() if g}
    m = await mark(day, last, read_at)
    if m.drawdown is None or m.drawdown > MAX_DRAWDOWN:
        return "Ivan checked: the paper book is paused, so no new idea."
    cands = [c for c in evaluate(sym, b[sym]) if c.day == max(b[sym])]
    if not cands:
        return f"Ivan checked: no rule setup on {sym} right now."
    today = await fetch_one("SELECT count(*) AS n FROM markets_ideas WHERE day = $1", day)
    if int(today["n"]) >= PER_DAY or len(held) >= MAX_OPEN:
        return "Ivan checked: a rule setup, but the daily idea cap is used."
    c = max(cands, key=lambda x: x.score)
    ok, why = await _earnings_ok(c)
    if not ok:
        return f"Ivan checked: a rule setup on {sym}, skipped: {why}."
    when = f" ({at.astimezone(TZ):%-d %b %H:%M} NY)" if at else ""
    shown = f" at {price:g}" if price else ""
    label = (name or "alert")[:CHECK_MAX]
    note = f"Triggered by your TradingView alert '{label}'{shown}{when}"
    if await _raise(day, c, note, "your TradingView alert (rule order, no model)",
                    m.equity, read_at):
        return f"Ivan checked: a {RULES[c.rule]} setup on {sym}; a paper idea card is up."
    return f"Ivan checked: a setup on {sym}, but an idea for it is already carded today."


async def _raise(day: date, c: Candidate, note: str, how: str, book: float,
                 read_at: datetime) -> bool:
    from ...kernel import approvals
    from ...kernel.state import execute_sql, fetch_one
    s = size(c.close, c.atr, book)
    if s is None:
        return False
    row = await fetch_one(
        """INSERT INTO markets_ideas (day, ticker, rule, numbers, note, ranked_by,
               ref_close, ref_day, read_at, atr, stop, target, shares, size_pct, book,
               status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,'carded')
           ON CONFLICT (day, ticker) DO NOTHING RETURNING id""",
        day, c.ticker, c.rule, json.dumps(c.numbers), note, how, c.close, c.day,
        read_at, c.atr, s.stop, s.target, s.shares, s.size_pct, book)
    if row is None:
        return False
    iid = int(row["id"])
    params = {"idea_id": iid, "ticker": c.ticker, "side": "BUY", "shares": s.shares,
              "ref_close": round(c.close, 2), "ref_day": c.day.isoformat(),
              "read_at": f"{read_at:%a %-d %b %H:%M} UTC", "stop": s.stop,
              "target": s.target, "size_pct": s.size_pct, "book": round(book, 2),
              "rule": c.rule, "numbers": c.numbers, "note": note}
    reason = (f"Paper idea: BUY {s.shares} {c.ticker} ({RULES[c.rule]}), {how}. "
              f"{DISCLAIMER}")
    try:
        card = await approvals.propose(
            ACTION, params, reason, severity=Severity.NORMAL,
            evidence={"read_at": read_at.isoformat(), "ranked_by": how},
            rollback_plan="cancels the idea if it is not booked yet",
            dedup_key=f"markets.idea:{day.isoformat()}:{c.ticker}", automatic=True)
    except Exception as exc:  # noqa: BLE001
        await execute_sql("UPDATE markets_ideas SET status = 'refused', note = $2 "
                          "WHERE id = $1", iid, f"the card could not be raised: {exc}")
        return False
    await execute_sql("UPDATE markets_ideas SET card_id = $2 WHERE id = $1", iid, card)
    return True


# ------------------------------------------------------------ the action

_MATCH = ("ticker", "shares", "stop", "target", "ref_close")


async def _check(p: dict[str, Any]) -> tuple[str | None, dict | None]:
    why = vet(p)
    if why:
        return why, None
    rows = await ideas("WHERE id = $1", int(p["idea_id"]))
    if not rows:
        return f"no idea #{p['idea_id']}", None
    r = rows[0]
    for k in _MATCH:
        have = r[k] if k == "ticker" else float(r[k])
        want = p[k] if k == "ticker" else float(p[k])
        if (have != want) if k == "ticker" else abs(have - want) > 0.005:
            return f"idea #{r['id']} has a different {k}", None
    return None, r


async def idea_dry(**p: Any) -> ActionResult:
    why, r = await _check(p)
    if why:
        return ActionResult(False, why)
    if r["status"] != "carded":
        return ActionResult(True, f"idea #{r['id']} is already {r['status']}; "
                                  f"nothing would change")
    return ActionResult(True, (
        f"would book BUY {r['shares']} {r['ticker']} in the paper idea book at the "
        f"next close after approval (stop {float(r['stop']):.2f}, target "
        f"{float(r['target']):.2f}). Paper only; nothing real is traded."))


async def idea_run(**p: Any) -> ActionResult:
    """Approve the idea into the paper book. It is booked at the next daily
    close by the scan; nothing leaves ATLAS."""
    why, r = await _check(p)
    if why:
        return ActionResult(False, why)
    if r["status"] in ("approved", "open", "closed"):
        return ActionResult(True, f"idea #{r['id']} is already {r['status']}",
                            evidence={"idea_id": r["id"], "status": r["status"]})
    if r["status"] != "carded":
        return ActionResult(False, f"idea #{r['id']} is {r['status']}")
    from ...kernel.state import fetch_one
    live = await fetch_one("SELECT count(*) AS n FROM markets_ideas "
                           "WHERE status IN ('approved', 'open')")
    if int(live["n"]) >= MAX_OPEN:
        return ActionResult(False, f"{live['n']} ideas already open (cap {MAX_OPEN})")
    row = await fetch_one(
        "UPDATE markets_ideas SET status = 'approved', decided_at = now() "
        "WHERE id = $1 AND status = 'carded' RETURNING id, decided_at", r["id"])
    if row is None:
        return ActionResult(False, f"idea #{r['id']} changed while approving")
    return ActionResult(
        True, (f"Paper idea #{r['id']}: BUY {r['shares']} {r['ticker']} goes into the "
               f"paper idea book at the next close. Nothing real is traded."),
        evidence={"idea_id": r["id"], "status": "approved",
                  "decided_at": row["decided_at"].isoformat()},
        rollback_token=str(r["id"]))


async def idea_rollback(token: str) -> ActionResult:
    from ...kernel.state import fetch_one
    try:
        iid = int(token)
    except ValueError:
        return ActionResult(False, f"not an idea id: {token!r}")
    row = await fetch_one("UPDATE markets_ideas SET status = 'cancelled' "
                          "WHERE id = $1 AND status = 'approved' RETURNING id", iid)
    if row is None:
        return ActionResult(False, f"idea #{iid} is not waiting to be booked; a "
                                   f"booked paper position exits only at its stop, "
                                   f"target or time")
    return ActionResult(True, f"idea #{iid} cancelled before booking",
                        evidence={"idea_id": iid, "status": "cancelled"})


def action():
    from ...kernel.contracts import Action, Tier
    return Action(
        name=ACTION, tier=Tier.ASK,
        summary="Book a paper trade idea in the idea book (paper only)",
        run=idea_run, dry_run=idea_dry, rollback=idea_rollback,
        params={"idea_id": "the idea's id", "ticker": "the ticker",
                "side": "BUY (long only)", "shares": "whole shares",
                "ref_close": "the reference close", "ref_day": "its date",
                "read_at": "when it was read", "stop": "stop price",
                "target": "target price", "size_pct": "percent of the idea book",
                "book": "the idea book's value", "rule": "the rule that fired",
                "numbers": "the rule's figures", "note": "the ranking's line"},
        max_per_hour=4, card_only=True, vet=vet, card_detail=card_text,
        verbose_card=False)


# ---------------------------------------------------------------- report

def _usd(v: float | None, base: float = START_BOOK) -> str:
    if v is None:
        return "unknown"
    return f"${v:,.2f} ({(v / base - 1) * 100:+.1f}%)"


def _stats(rows: list[dict]) -> str:
    done = [r for r in rows if r["exit_day"] is not None and r["pnl"] is not None]
    if not done:
        return "none closed yet"
    wins = sum(1 for r in done if float(r["pnl"]) > 0)
    rs = [float(r["r_multiple"]) for r in done if r["r_multiple"] is not None]
    avg = f"{sum(rs) / len(rs):+.2f}R" if rs else "R unknown"
    pnl = sum(float(r["pnl"]) for r in done)
    return (f"{len(done)} closed, {wins} won ({wins / len(done) * 100:.0f}%), avg "
            f"{avg}, P&L {'+' if pnl >= 0 else '-'}${abs(pnl):,.2f}")


async def section_body(now: datetime) -> str:
    from ...kernel.state import fetch_all, fetch_one
    start = await fetch_one("SELECT day, spy FROM markets_idea_marks ORDER BY day LIMIT 1")
    if start is None:
        return (f"Not started: ${START_BOOK:,.0f} paper, first scan on a weekday at "
                f"16:30 New York. Paper, not advice.")
    rows = await ideas()
    open_ = [r for r in rows if r["status"] == "open"]
    b, read_at = await read_all({r["ticker"] for r in open_} | {BENCH},
                                now.astimezone(TZ).date())
    last = {s: g[max(g)]["close"] for s, g in b.items() if g}
    value = equity([r for r in rows if r["status"] in BOOKED], last)
    spy0, spy1 = start["spy"], last.get(BENCH)
    spy = START_BOOK * spy1 / float(spy0) if spy0 and spy1 else None
    days = [max(g) for g in b.values() if g]
    when = (f"closes of {max(days):%a %-d %b}, read {read_at:%a %H:%M} UTC"
            if days else f"nothing readable at {read_at:%a %H:%M} UTC")
    lines = [(f"Since {start['day']:%-d %b}: book {_usd(value)} vs $10k in SPY "
              f"{_usd(spy)} ({when}).")]
    booked = [r for r in rows if r["status"] in BOOKED]
    passed = [r for r in rows if r["status"] in HYPOTHETICAL]
    lines.append(f"Approved: {_stats(booked)} · declined (hypothetical): {_stats(passed)}.")
    if open_:
        lines.append("Open: " + ", ".join(
            f"{r['ticker']} {r['shares']} @ {float(r['entry_price']):.2f} "
            f"(stop {float(r['stop']):.2f}, target {float(r['target']):.2f})"
            for r in open_) + ".")
    waiting = [r for r in rows if r["status"] in ("carded", "approved")]
    if waiting:
        lines.append(f"Waiting: {len(waiting)} (cards or booking at the next close).")
    week = await fetch_all(
        "SELECT day, status, detail, finnhub FROM markets_idea_scans "
        "WHERE day > $1 ORDER BY day DESC", (now.astimezone(TZ) - timedelta(days=7)).date())
    paused = [dict(r) for r in week if r["status"] == "paused"]
    if paused:
        lines.append(f"Paused: {paused[0]['detail']}.")
    if week and not any(r["finnhub"] for r in week):
        lines.append("No Finnhub key, so earnings dates are unknown: single stocks "
                     "were skipped, ETFs only.")
    lines.append("Paper, not advice.")
    return "\n".join(lines)


async def ideas_section(now: datetime | None = None) -> str:
    """The weekly report's idea-book paragraph. Never raises."""
    now = now or datetime.now(UTC)
    head = "*Paper idea book*"
    try:
        return f"{head}\n{await section_body(now)}"
    except Exception as exc:
        log.exception("ideas section failed")
        return (f"{head}\nCouldn't read it this week ({type(exc).__name__}), so every "
                f"figure is UNKNOWN — not zero. Paper, not advice.")


# ------------------------------------------------------------- schedule

def windows(now: datetime, horizon: timedelta) -> list:
    """Weekdays at 16:30 in `now`'s zone (the scheduler passes New York)."""
    from ...kernel.missed import Window
    out = []
    for back in range(horizon.days, -1, -1):
        d = now.date() - timedelta(days=back)
        at = datetime.combine(d, SCAN_AT, tzinfo=now.tzinfo)
        if d.weekday() < 5 and at <= now:
            out.append(Window(d.isoformat(), at))
    return out
