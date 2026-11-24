"""Markets -- watches the AI stock basket; never trades.

the owner, 2026-09-28, after an Alinea ad ("ChatGPT and Claude picking stocks"):
"make sure there is a trading agent in control of this as well". ATLAS
never trades: moving money is Tier 3, permanently denied, and nothing here
gives personal investment advice. What it does:

* reads the public daily prices of the basket the ad showed (WATCHLIST,
  overridable with MARKETS_WATCHLIST), each with the time it was read;
* keeps a PAPER basket -- $100 split equally on the first day it ran, never
  changed -- so "how would that basket have done" is a measured answer, not
  a claim (the control);
* keeps the **Claude playlist (paper)** (`playlist.py`), the Alinea
  "Playlist Pro" loop on paper: once a week (Sunday 15:00 New York) a model,
  Claude first, proposes target weights from prices ATLAS read itself; the owner
  approves or declines the card; approved, `markets.paper_rebalance` moves
  the paper holdings at the latest closes. It starts at $100 split equally
  across the non-VOO tickers and is compared weekly with the control and
  with $100 in VOO;
* sends **trade idea cards** (`ideas.py`): weekdays after the US close,
  deterministic long-only rules over the watchlist, index and sector ETFs
  (a model may only rank them), at most 2 a day; approved, an idea is
  booked in a $10,000 PAPER idea book; declined, its hypothetical result is
  tracked too. The card's order ticket is for the owner's own broker, if he
  chooses -- ATLAS never places a real trade;
* says plainly when a price can't be read (UNKNOWN, never a guess).

It also reads back the owner's own Alinea account from Alinea's emails (the
`alinea` check, `alinea.py`): Alinea trades on its own; ATLAS only reads.

And, since 2026-09-29, SEC EDGAR (the `filings` check, `edgar.py`): the
watchlist's new 10-K, 10-Q and 8-K filings and Form 4 insider purchases and
sales, read from SEC's public JSON with a contact User-Agent.

Its actions: `markets.alert` (Tier 1, `alerts.py`) sends the owner a Slack
message when a watchlist stock moves more than MARKETS_ALERT_MOVE_PCT on the
day, a new 8-K or insider purchase/sale is filed, or Alinea trails VOO by the
Alinea rule -- a message and nothing else. `markets.paper_rebalance` and
`markets.paper_idea` are Tier 2 and card_only: raised only by their own
scheduled jobs, approved only by the owner, never offered to a model; they write
ATLAS's own paper tables. No action here can trade or move money, and none
may be added (money out is Tier 3). Prices come from Yahoo's public chart
endpoint (no key; unofficial, so it can change); Finnhub is read if
FINNHUB_API_KEY is set.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from ...kernel.contracts import Action, Check, Health, Observation, Severity, Tier
from .edgar import check_filings

# As shown in the ad's "Claude" basket, 2026-09-28, plus the three the second
# Alinea ad added (JNJ, JPM, VOO). Its screens are illustrative.
WATCHLIST = ("AMZN", "NVDA", "MSFT", "LLY", "GOOGL", "AAPL", "V", "ISRG", "DE",
             "NEE", "TSLA", "ASML", "JNJ", "JPM", "VOO")
PAPER_DOLLARS = 100.0
BENCHMARK_SYMBOL = "VOO"      # the benchmark; never held by the playlist
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"
HISTORY = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           "?period1={p1}&period2={p2}&interval=1d")


def move_pct() -> float:
    """MARKETS_ALERT_MOVE_PCT: a day's move of MORE than this, either way, is
    a big move -- in the basket's text and for `markets.alert`. Default 5."""
    try:
        pct = float(os.getenv("MARKETS_ALERT_MOVE_PCT", "5"))
    except ValueError:
        return 5.0
    return pct if 0 < pct < 100 else 5.0


def watchlist() -> tuple[str, ...]:
    raw = os.getenv("MARKETS_WATCHLIST", "").strip()
    syms = tuple(s.strip().upper() for s in raw.split(",") if s.strip()) if raw else ()
    return syms or WATCHLIST


async def quote(client: httpx.AsyncClient, sym: str) -> dict | None:
    """{last, prev, at} from the public chart, or None when unreadable."""
    try:
        r = await client.get(CHART.format(sym=sym),
                             headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
        meta = res["meta"]
        last = float(meta.get("regularMarketPrice") or closes[-1])
        prev = float(closes[-2]) if len(closes) >= 2 else None
        at = datetime.fromtimestamp(int(meta["regularMarketTime"]), UTC)
        return {"last": last, "prev": prev, "at": at}
    except (KeyError, IndexError, ValueError, TypeError, httpx.HTTPError):
        return None


async def closes(sym: str, start: date, end: date, *,
                 adjusted: bool = False) -> dict[date, float] | None:
    """Daily closes from `start` to `end`, keyed by the exchange's own
    trading day, from the same public chart feed as `quote`. `adjusted`
    reads the dividend-adjusted close instead (a total return, as if its
    dividends were reinvested). None when the feed can't be read, or has no
    adjusted series when one was asked for. A day with no close is simply
    absent -- never filled in."""
    p1 = int(datetime.combine(start, time(), UTC).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=1), time(), UTC).timestamp())
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(HISTORY.format(sym=sym, p1=p1, p2=p2),
                            headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "America/New_York")
        out: dict[date, float] = {}
        series = (res["indicators"]["adjclose"][0]["adjclose"] if adjusted
                  else res["indicators"]["quote"][0]["close"])
        for ts, close in zip(res["timestamp"], series, strict=True):
            if close is not None:
                out[datetime.fromtimestamp(int(ts), tz).date()] = float(close)
        return out
    except (KeyError, IndexError, ValueError, TypeError, httpx.HTTPError):
        return None


async def bars(sym: str, start: date, end: date) -> dict[date, dict] | None:
    """Daily bars -- {open, high, low, close, volume} -- from `start` to
    `end`, from the same public chart feed as `closes` (plain, not
    adjusted). A day with no close is absent; a missing high, low, open or
    volume is None, never filled in. None when the feed can't be read."""
    p1 = int(datetime.combine(start, time(), UTC).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=1), time(), UTC).timestamp())
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(HISTORY.format(sym=sym, p1=p1, p2=p2),
                            headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "America/New_York")
        q = res["indicators"]["quote"][0]
        out: dict[date, dict] = {}
        for i, ts in enumerate(res["timestamp"]):
            close = q["close"][i]
            if close is None:
                continue

            def f(key: str, i: int = i) -> float | None:
                v = (q.get(key) or [None] * (i + 1))[i]
                return float(v) if v is not None else None

            out[datetime.fromtimestamp(int(ts), tz).date()] = {
                "open": f("open"), "high": f("high"), "low": f("low"),
                "close": float(close), "volume": f("volume")}
        return out
    except (KeyError, IndexError, ValueError, TypeError, httpx.HTTPError):
        return None


async def check_basket() -> Observation:
    syms = watchlist()
    async with httpx.AsyncClient(timeout=15) as c:
        got = await asyncio.gather(*(quote(c, s) for s in syms))
    quotes = {s: q for s, q in zip(syms, got, strict=True) if q}
    missing = [s for s in syms if s not in quotes]
    if not quotes:
        return Observation("basket", Health.UNKNOWN,
                           detail="no price could be read from the public chart feed")
    moves = {s: (q["last"] / q["prev"] - 1) * 100 for s, q in quotes.items() if q["prev"]}
    basket = sum(moves.values()) / len(moves) if moves else None
    paper = await _paper(quotes)
    read = max(q["at"] for q in quotes.values())
    parts = []
    if basket is not None:
        parts.append(f"basket {basket:+.2f}% on the day (equal weight, {len(moves)} stocks)")
    if paper is not None:
        parts.append(f"paper ${PAPER_DOLLARS:.0f} basket now ${paper[0]:.2f} "
                     f"since {paper[1]:%d %b}")
    if missing:
        parts.append(f"unreadable: {', '.join(missing)}")
    threshold = move_pct()
    big = sorted(((s, m) for s, m in moves.items() if abs(m) > threshold),
                 key=lambda x: -abs(x[1]))
    if big:
        parts.append(f"big moves (more than {threshold:g}%): "
                     + ", ".join(f"{s} {m:+.1f}%" for s, m in big))
    return Observation(
        "basket", Health.OK if not missing else Health.DEGRADED,
        round(basket, 3) if basket is not None else None, "%",
        "; ".join(parts) + f" (prices as of {read:%a %d %b %H:%M} UTC; watch only, "
        f"never a trade)",
        evidence={"moves": {s: round(m, 3) for s, m in moves.items()},
                  "prices": {s: q["last"] for s, q in quotes.items()},
                  "prev": {s: q["prev"] for s, q in quotes.items() if q["prev"]},
                  # Each price's own time, so an alert keys a move to the
                  # trading day it happened on, not to when it was read.
                  "at": {s: q["at"].isoformat() for s, q in quotes.items()},
                  "checked_at": datetime.now(UTC).isoformat(),
                  "read_at": read.isoformat(), "missing": missing})


async def _paper(quotes: dict) -> tuple[float, datetime] | None:
    """The paper basket's value; started on the first run with every stock
    read, and never changed after."""
    from ...kernel.state import execute_sql, fetch_all
    try:
        rows = await fetch_all("SELECT ticker, shares, started_at FROM markets_paper")
        if not rows:
            if len(quotes) < len(watchlist()):
                return None        # start only from a complete read
            each = PAPER_DOLLARS / len(quotes)
            for s, q in quotes.items():
                await execute_sql(
                    "INSERT INTO markets_paper (ticker, shares, start_price) "
                    "VALUES ($1, $2, $3) ON CONFLICT (ticker) DO NOTHING",
                    s, each / q["last"], q["last"])
            rows = await fetch_all("SELECT ticker, shares, started_at FROM markets_paper")
        if any(str(r["ticker"]) not in quotes for r in rows):
            return None
        value = sum(float(r["shares"]) * quotes[str(r["ticker"])]["last"] for r in rows)
        return value, rows[0]["started_at"]
    except Exception:                                   # noqa: BLE001
        return None


class MarketsModule:
    name = "markets"
    title = "Markets (watch only)"
    requires_env: tuple[str, ...] = ()

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        from .alinea import check_alinea
        from .tradingview import check_tradingview
        return (Check("basket", check_basket, timedelta(hours=1), Severity.INFO,
                      "the AI stock basket's prices and the paper basket, read-only",
                      baseline_sigma=None),
                Check("alinea", check_alinea, timedelta(hours=2), Severity.INFO,
                      "the owner's Alinea account as its emails state it, read-only; "
                      "Alinea trades on its own",
                      baseline_sigma=None),
                Check("filings", check_filings, timedelta(hours=1), Severity.INFO,
                      "the watchlist's new SEC filings and Form 4 insider "
                      "purchases and sales, read-only from SEC EDGAR",
                      baseline_sigma=None,
                      # ~15 filing indexes and up to 20 Form 4s at under 10
                      # requests a second: longer than the 30 s default.
                      timeout=timedelta(seconds=150)),
                Check("tradingview", check_tradingview, timedelta(minutes=15),
                      Severity.INFO,
                      "the owner's own TradingView alerts as TradingView sent them, "
                      "read-only; never traded on",
                      baseline_sigma=None))

    def actions(self):
        # The alert sends the owner a message; the two paper actions move ATLAS's
        # own paper books on his approval. Nothing here can trade; money out
        # is Tier 3, permanently denied.
        from . import ideas, playlist
        from .alerts import ACTION, alert, alert_dry, alert_rollback
        return (Action(
            name=ACTION, tier=Tier.AUTO,   # a message to the owner; trades nothing
            summary=("Send the owner one planned market alert (a big move, a new "
                     "8-K or insider purchase/sale, Alinea trailing VOO): "
                     "facts with their read time, never advice"),
            run=alert, dry_run=alert_dry, rollback=alert_rollback,
            params={"key": "the planned alert's key (markets_alerts.key)"},
            max_per_hour=10), playlist.action(), ideas.action())

    def brief(self) -> str:
        """The last Alinea, SEC and TradingView readings, with their read
        times; nothing before a check has run. TradingView's own words are
        inside wrap_untrusted (`tradingview.brief_line`)."""
        from .alinea import brief_line
        from .edgar import brief_line as filings_line
        from .tradingview import brief_line as tradingview_line
        return " | ".join(x for x in (brief_line(), filings_line(),
                                      tradingview_line()) if x)

    async def signals(self):
        return ()


MODULE = MarketsModule()
