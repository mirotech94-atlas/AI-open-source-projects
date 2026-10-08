"""Deterministic bars for the futures tests, and a fake Yahoo chart feed.

No network, no database: everything here is built from a seed or from a
list of closes, so a test that fails, fails the same way every time.
"""
from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from atlas.modules.futures.engines import TZ, Bar, in_cme_hours

TUESDAY = date(2026, 9, 29)          # a plain CME weekday
MONDAY = date(2026, 9, 28)
WICK = 0.3


def et(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=TZ).astimezone(UTC)


def bars_from(closes: Sequence[float], start: datetime, *, minutes: int = 5,
              volume: float = 1000.0, first_open: float | None = None,
              highs: dict[int, float] | None = None, lows: dict[int, float] | None = None,
              volumes: dict[int, float] | None = None,
              opens: dict[int, float] | None = None) -> list[Bar]:
    """One bar per close, each opening at the previous close, with a
    fixed wick either side unless a high/low is given for that index."""
    out: list[Bar] = []
    prev = first_open if first_open is not None else closes[0]
    for i, c in enumerate(closes):
        o = (opens or {}).get(i, prev)
        h = (highs or {}).get(i, max(o, c) + WICK)
        lo = (lows or {}).get(i, min(o, c) - WICK)
        v = (volumes or {}).get(i, volume)
        out.append(Bar(start + timedelta(minutes=minutes * i), o, max(h, o, c),
                       min(lo, o, c), c, v, minutes))
        prev = c
    return out


def ramp(n: int = 42, lo: float = 100.0, hi: float = 100.4) -> list[float]:
    """A monotonic warm-up: no interior swing pivots."""
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def morning(pattern: Sequence[float], *, day: date = TUESDAY, warm: list[float] | None = None,
            **kw: Any) -> list[Bar]:
    """42 warm-up bars from 06:00 ET so the pattern's first bar opens at
    09:30 ET (ny_open), the last bar being the one under test."""
    warm = ramp() if warm is None else warm
    return bars_from([*warm, *pattern], et(day, 6, 0), **kw)


def random_walk(sessions: int = 4, seed: int = 7, start: float = 20_000.0,
                first: date = MONDAY, minutes: int = 5) -> list[Bar]:
    """5-minute bars over whole CME sessions (18:00 ET to 16:55 ET, minus
    the maintenance hour), a seeded random walk with lognormal volume."""
    rng = random.Random(seed)
    out: list[Bar] = []
    price = start
    d = first
    done = 0
    while done < sessions:
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        t = datetime.combine(d - timedelta(days=1), time(18, 0), tzinfo=TZ)
        end = datetime.combine(d, time(17, 0), tzinfo=TZ)
        while t < end:
            if in_cme_hours(t):
                o = price
                c = o + rng.gauss(0, 8)
                h = max(o, c) + abs(rng.gauss(0, 4))
                lo = min(o, c) - abs(rng.gauss(0, 4))
                v = rng.lognormvariate(6.5, 0.6)
                out.append(Bar(t.astimezone(UTC), o, h, lo, c, round(v), minutes))
                price = c
            t += timedelta(minutes=minutes)
        d += timedelta(days=1)
        done += 1
    return out


def yahoo_payload(bars: Sequence[Bar], *, partial_at: datetime | None = None,
                  holes: Sequence[int] = ()) -> dict[str, Any]:
    """A chart response for `bars`, with an optional forming bar stamped
    `partial_at` (zero volume) and `holes` (indexes whose prices are null),
    as the live feed does it."""
    rows = list(bars)
    ts = [int(b.ts.timestamp()) for b in rows]
    cols: dict[str, list[Any]] = {k: [getattr(b, k) for b in rows]
                                  for k in ("open", "high", "low", "close", "volume")}
    for i in holes:
        for col in cols.values():
            col[i] = None
    if partial_at is not None:
        last = rows[-1].close
        ts.append(int(partial_at.timestamp()))
        for k in ("open", "high", "low", "close"):
            cols[k].append(last)
        cols["volume"].append(0)
    return {"chart": {"result": [{
        "meta": {"symbol": "MNQ=F", "exchangeTimezoneName": "America/New_York",
                 "regularMarketTime": ts[-1], "dataGranularity": "5m"},
        "timestamp": ts,
        "indicators": {"quote": [cols]}}], "error": None}}
