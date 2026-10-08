"""MNQ/NQ bars from Yahoo's public chart endpoint -- the feed the markets
module already reads, read-only, no key.

    GET https://query1.finance.yahoo.com/v8/finance/chart/MNQ=F
        ?range=60d&interval=5m          (5m and 15m: at most 60 days;
                                         1h: 730 days; 1d: years)

Verified 2026-09-29 against live responses: `timestamp` is bar open in
epoch seconds; `indicators.quote[0]` holds open/high/low/close/volume with
`null` holes (the 17:00-18:00 ET maintenance hour, and others); the last
element is the bar still forming, stamped `regularMarketTime`, with zero
volume. `parse` keeps closed bars only -- a bar whose end is after the read
time is dropped, and so is any bar missing a price -- and every bar carries
the time it was read. Nothing is filled in: a hole is a hole, and `gaps`
lists the ones inside CME hours so the report can say so.

The data is unofficial and can change or vanish. An unreadable feed is
`Unreadable` for the caller to report as UNKNOWN, never a guess. Every
request is a GET to query1/query2.finance.yahoo.com; there is no other
host and no other verb in this module (a test reads the source to check).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import httpx

from .engines import Bar, in_cme_hours

log = logging.getLogger("atlas.futures.data")

HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}"
INTERVALS: dict[str, int] = {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}
MAX_RANGE: dict[str, str] = {"5m": "60d", "15m": "60d", "1h": "730d", "1d": "5y"}
SYMBOLS = ("MNQ=F", "NQ=F")
TIMEOUT_S = 30.0
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Tests put an httpx.MockTransport here; None is the real network.
transport: httpx.AsyncBaseTransport | None = None


class Unreadable(Exception):
    """The feed could not be read or made sense of. Report UNKNOWN."""

    def __init__(self, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        self.status = status


@dataclass(frozen=True, slots=True)
class Feed:
    symbol: str
    interval: str
    bars: list[Bar]                 # closed, sorted, unique by ts
    read_at: datetime
    status: int | None
    dropped: int                    # unclosed or priceless rows left out
    gaps: list[tuple[datetime, datetime]]   # inside CME hours: (from, to)


def gaps(bars: list[Bar], minutes: int) -> list[tuple[datetime, datetime]]:
    """Spans between consecutive bars where at least one expected bar slot
    falls inside CME hours. The maintenance hour and weekends are not
    gaps; a hole on a Tuesday afternoon is. Holidays show up here too."""
    out: list[tuple[datetime, datetime]] = []
    delta = timedelta(minutes=minutes)
    for a, b in pairwise(bars):
        if b.ts - a.end <= timedelta(0):
            continue
        slot = a.end
        while slot < b.ts:
            if in_cme_hours(slot):
                out.append((a.end, b.ts))
                break
            slot += delta
    return out


def parse(payload: Any, symbol: str, interval: str, read_at: datetime) -> Feed:
    """Closed bars from one chart response. Pure; raises Unreadable."""
    minutes = INTERVALS.get(interval)
    if minutes is None:
        raise Unreadable(f"unknown interval {interval!r}")
    try:
        chart = payload["chart"]
        if chart.get("error"):
            raise Unreadable(f"the feed answered an error: "
                             f"{str(chart['error'].get('code', ''))[:60]}")
        res = chart["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        cols = {k: q[k] for k in ("open", "high", "low", "close")}
        vols = q.get("volume") or [None] * len(ts)
    except Unreadable:
        raise
    except (KeyError, IndexError, TypeError) as exc:
        raise Unreadable(f"unexpected chart shape ({type(exc).__name__})") from None
    bars: dict[datetime, Bar] = {}
    dropped = 0
    for i, t in enumerate(ts):
        try:
            at = datetime.fromtimestamp(int(t), UTC)
            prices = [cols[k][i] for k in ("open", "high", "low", "close")]
        except (TypeError, ValueError, IndexError):
            dropped += 1
            continue
        if any(v is None for v in prices) or at + timedelta(minutes=minutes) > read_at:
            dropped += 1
            continue
        o, h, lo, c = (float(v) for v in prices)
        v = vols[i] if i < len(vols) else None
        bars[at] = Bar(at, o, max(h, o, c), min(lo, o, c), c,
                       None if v is None else float(v), minutes)
    ordered = [bars[k] for k in sorted(bars)]
    return Feed(symbol, interval, ordered, read_at, 200, dropped, gaps(ordered, minutes))


async def fetch(symbol: str, interval: str, range_: str | None = None) -> Feed:
    """One GET, parsed. Raises Unreadable on any failure: HTTP status,
    network, shape. Never retries into a guess."""
    if interval not in INTERVALS:
        raise Unreadable(f"unknown interval {interval!r}")
    url = CHART.format(sym=symbol, rng=range_ or MAX_RANGE[interval], iv=interval)
    read_at = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as c:
            r = await c.get(url, headers=HEADERS)
    except httpx.HTTPError as exc:
        raise Unreadable(f"unreachable: {type(exc).__name__}") from None
    if r.status_code != 200:
        raise Unreadable(f"HTTP {r.status_code} from the chart feed", r.status_code)
    try:
        payload = r.json()
    except ValueError:
        raise Unreadable("the feed did not answer JSON", 200) from None
    return parse(payload, symbol, interval, read_at)


# ------------------------------------------------------------------ storage

async def upsert(feed: Feed) -> int:
    """Store the feed's closed bars, idempotently (one statement). A bar
    already stored is overwritten with this read -- Yahoo can revise -- and
    its read time moves to now. Returns the number of bars written."""
    from ...kernel.state import execute_sql
    if not feed.bars:
        return 0
    b = feed.bars
    await execute_sql(
        """INSERT INTO futures_bars
               (symbol, interval, ts, open, high, low, close, volume, read_at)
           SELECT $1, $2, t.ts, t.o, t.h, t.l, t.c, t.v, $9
             FROM unnest($3::timestamptz[], $4::float8[], $5::float8[], $6::float8[],
                         $7::float8[], $8::float8[]) AS t(ts, o, h, l, c, v)
           ON CONFLICT (symbol, interval, ts) DO UPDATE
               SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                   close = EXCLUDED.close, volume = EXCLUDED.volume,
                   read_at = EXCLUDED.read_at""",
        feed.symbol, feed.interval, [x.ts for x in b], [x.open for x in b],
        [x.high for x in b], [x.low for x in b], [x.close for x in b],
        [x.volume for x in b], feed.read_at)
    return len(b)


async def record_read(symbol: str, interval: str, feed: Feed | None, *,
                      error: Unreadable | None = None) -> None:
    from ...kernel.state import execute_sql
    if feed is not None:
        await execute_sql(
            """INSERT INTO futures_reads
                   (symbol, interval, ok, status, bars, newest, gaps, detail)
               VALUES ($1, $2, true, $3, $4, $5, $6::jsonb, $7)""",
            symbol, interval, feed.status, len(feed.bars),
            feed.bars[-1].ts if feed.bars else None,
            _json([[a.isoformat(), z.isoformat()] for a, z in feed.gaps]),
            f"{feed.dropped} unclosed or priceless rows dropped")
    else:
        await execute_sql(
            """INSERT INTO futures_reads (symbol, interval, ok, status, detail)
               VALUES ($1, $2, false, $3, $4)""",
            symbol, interval, error.status if error else None,
            str(error)[:300] if error else "unreadable")


def _json(value: Any) -> str:
    import json
    return json.dumps(value)


async def load(symbol: str, interval: str, since: datetime) -> list[Bar]:
    from ...kernel.state import fetch_all
    minutes = INTERVALS[interval]
    rows = await fetch_all(
        """SELECT ts, open, high, low, close, volume FROM futures_bars
            WHERE symbol = $1 AND interval = $2 AND ts >= $3 ORDER BY ts""",
        symbol, interval, since)
    return [Bar(r["ts"], float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), None if r["volume"] is None else float(r["volume"]),
                minutes) for r in rows]


async def last_read(symbol: str, interval: str) -> dict[str, Any] | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT at, ok, status, bars, newest, gaps, detail FROM futures_reads
            WHERE symbol = $1 AND interval = $2 ORDER BY id DESC LIMIT 1""",
        symbol, interval)
    return dict(row) if row else None
