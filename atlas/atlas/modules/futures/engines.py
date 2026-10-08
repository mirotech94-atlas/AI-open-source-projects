"""The rulebook's engines: pure functions from closed bars to signals.

`rulebook.md` is the spec; this file is its implementation, and nothing
else. No model, no network, no database: a signal on bar N is a function of
bars 0..N and nothing after (`tests/test_futures_engines.py` mutates every
later bar and checks). Every rule is written long-only; the short side is
the same rule run on a price-negated copy of the bars (`mirror`), so the
two sides are exact mirrors by construction.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(18, 0)        # CME Globex, ET
SESSION_CLOSE = time(17, 0)
CASH_CLOSE = time(16, 0)
RTH_OPEN = time(9, 30)
OR15_END = time(9, 45)
OR30_END = time(10, 0)
ORB_LAST = time(11, 0)
KILLZONES: dict[str, tuple[time, time]] = {
    "london": (time(3, 0), time(5, 0)),
    "ny_open": (time(9, 30), time(11, 0)),
    "ny_pm": (time(13, 30), time(15, 30)),
}
ALL_SESSIONS = ("london", "ny_open", "ny_pm")
NY = ("ny_open", "ny_pm")
PIVOT = 3                 # bars either side of a swing, and its confirmation lag
ATR_N = 14
VOL_N = 20
SMA_N = 20
BAND_SIGMA = 2.0
FVG_LOOKBACK = 12
OB_LOOKBACK = 20
OB_IMPULSE_ATR = 1.5
STRUCTURE_LOOKBACK = 60
EQH_LOOKBACK = 40
EQH_TOLERANCE = 0.0005    # 0.05%
VOLUME_MULT = 2.0
TRAIL_ATR = 2.0
MAX_PER_SESSION = 2
MIN_RISK_TICKS = 2
TICK = 0.25


@dataclass(frozen=True, slots=True)
class Bar:
    ts: datetime              # bar open, aware UTC
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    minutes: int

    @property
    def end(self) -> datetime:
        return self.ts + timedelta(minutes=self.minutes)


@dataclass(frozen=True, slots=True)
class Signal:
    engine: str
    side: int                 # +1 long, -1 short
    at: datetime              # the closed bar it fired on
    ref: float                # that bar's close
    stop: float               # initial stop
    target: float | None      # None: the trailing stop is the exit
    trail_atr: float | None   # trail distance in ATRs, when trailing
    time_stop_min: int
    reason: str


# ---------------------------------------------------------------- sessions

def et(ts: datetime) -> datetime:
    return ts.astimezone(TZ)


def session_date(ts: datetime) -> date:
    """The CME session a bar belongs to: the date of its 17:00 ET close.
    Sunday 18:00 opens Monday's session."""
    return (et(ts) + timedelta(hours=6)).date()


def in_cme_hours(ts: datetime) -> bool:
    """Inside Globex hours for equity index futures: Sunday 18:00 ET to
    Friday 17:00 ET, minus the 17:00-18:00 ET maintenance hour."""
    local = et(ts)
    wd, t = local.weekday(), local.time()
    if wd == 5:
        return False
    if wd == 6:
        return t >= SESSION_OPEN
    if wd == 4:
        return t < SESSION_CLOSE
    return not (SESSION_CLOSE <= t < SESSION_OPEN)


def killzone(ts: datetime) -> str | None:
    t = et(ts).time()
    for name, (a, b) in KILLZONES.items():
        if a <= t < b:
            return name
    return None


def ends_cash_day(bar: Bar) -> bool:
    """The bar whose close is the 16:00 ET cash close (or the first that
    reaches past it): open positions go flat at its close."""
    start, end = et(bar.ts), et(bar.end)
    return (start.date() == end.date() and start.time() < CASH_CLOSE <= end.time())


# ---------------------------------------------------------------- features

@dataclass(slots=True)
class Features:
    """Per-bar indicator values, each computed from bars 0..i only."""

    session: list[date]
    vwap: list[float | None]
    upper: list[float | None]
    lower: list[float | None]
    atr: list[float | None]
    avg_vol: list[float | None]       # mean volume of the PRIOR VOL_N bars
    sma: list[float | None]
    pdh: list[float | None]
    pdl: list[float | None]
    or15: list[tuple[float, float] | None]
    or30: list[tuple[float, float] | None]
    # Confirmed swing pivots: (index, price), sorted by index. A pivot at j
    # is known from bar j + PIVOT on (see `swings`).
    ph: list[tuple[int, float]]
    pl: list[tuple[int, float]]


def _pivots(values: Sequence[float], high: bool) -> list[tuple[int, float]]:
    out = []
    n = len(values)
    # Strictly beyond the PIVOT bars before it, at or beyond the PIVOT bars
    # after it: the first bar of a tie is the swing (a bar opening at the
    # prior close often repeats its neighbour's extreme).
    for j in range(PIVOT, n - PIVOT):
        v = values[j]
        left, right = values[j - PIVOT:j], values[j + 1:j + PIVOT + 1]
        if high:
            ok = all(x < v for x in left) and all(x <= v for x in right)
        else:
            ok = all(x > v for x in left) and all(x >= v for x in right)
        if ok:
            out.append((j, v))
    return out


def swings(pivots: list[tuple[int, float]], i: int, n: int) -> list[tuple[int, float]]:
    """The last `n` pivots confirmed by bar i (index <= i - PIVOT), oldest
    first."""
    keys = [j for j, _ in pivots]
    k = bisect_right(keys, i - PIVOT)
    return pivots[max(0, k - n):k]


def compute(bars: Sequence[Bar]) -> Features:
    n = len(bars)
    session = [session_date(b.ts) for b in bars]
    vwap: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    atr: list[float | None] = [None] * n
    avg_vol: list[float | None] = [None] * n
    sma: list[float | None] = [None] * n
    pdh: list[float | None] = [None] * n
    pdl: list[float | None] = [None] * n
    or15: list[tuple[float, float] | None] = [None] * n
    or30: list[tuple[float, float] | None] = [None] * n
    sv = spv = spv2 = 0.0
    cur: date | None = None
    prev_hl: tuple[float, float] | None = None
    hi = lo = 0.0
    o15: list[float] = []
    o30: list[float] = []
    r15: tuple[float, float] | None = None
    r30: tuple[float, float] | None = None
    trs: list[float] = []
    for i, b in enumerate(bars):
        if session[i] != cur:
            if cur is not None:
                prev_hl = (hi, lo)
            cur, hi, lo = session[i], b.high, b.low
            sv = spv = spv2 = 0.0
            o15, o30, r15, r30 = [], [], None, None
        else:
            hi, lo = max(hi, b.high), min(lo, b.low)
        if prev_hl is not None:
            pdh[i], pdl[i] = prev_hl
        v = b.volume or 0.0
        tp = (b.high + b.low + b.close) / 3
        sv += v
        spv += tp * v
        spv2 += tp * tp * v
        if sv > 0:
            m = spv / sv
            var = max(spv2 / sv - m * m, 0.0)
            vwap[i], upper[i], lower[i] = (m, m + BAND_SIGMA * math.sqrt(var),
                                           m - BAND_SIGMA * math.sqrt(var))
        t = et(b.ts).time()
        # A range is only real from bars no longer than its own window.
        if RTH_OPEN <= t < OR15_END and b.minutes <= 15:
            o15 += [b.high, b.low]
        if RTH_OPEN <= t < OR30_END and b.minutes <= 30:
            o30 += [b.high, b.low]
        if t >= OR15_END and o15 and r15 is None:
            r15 = (max(o15[0::2]), min(o15[1::2]))
        if t >= OR30_END and o30 and r30 is None:
            r30 = (max(o30[0::2]), min(o30[1::2]))
        or15[i], or30[i] = r15, r30
        if i > 0:
            p = bars[i - 1]
            trs.append(max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close)))
            prior = atr[i - 1]
            if prior is not None:
                atr[i] = (prior * (ATR_N - 1) + trs[-1]) / ATR_N     # Wilder
            elif len(trs) >= ATR_N:
                atr[i] = sum(trs[-ATR_N:]) / ATR_N
        if i >= VOL_N:
            vols = [x.volume for x in bars[i - VOL_N:i]]
            if all(x is not None for x in vols):
                avg_vol[i] = sum(x for x in vols if x is not None) / VOL_N
        if i >= SMA_N - 1:
            sma[i] = sum(x.close for x in bars[i - SMA_N + 1:i + 1]) / SMA_N
    return Features(session, vwap, upper, lower, atr, avg_vol, sma, pdh, pdl,
                    or15, or30, _pivots([b.high for b in bars], True),
                    _pivots([b.low for b in bars], False))


def mirror(bars: Sequence[Bar]) -> list[Bar]:
    """The same bars with every price negated: a long rule on these is the
    short rule on the originals."""
    return [Bar(b.ts, -b.open, -b.low, -b.high, -b.close, b.volume, b.minutes)
            for b in bars]


# ----------------------------------------------------------------- engines

Rule = Callable[[Sequence[Bar], Features, int], Signal | None]


def _same_session(f: Features, i: int, back: int) -> bool:
    return i - back >= 0 and f.session[i - back] == f.session[i]


def _sig(name: str, b: Bar, stop: float, *, r: float | None = 2.0,
         target: float | None = None, trail: float | None = None,
         minutes: int, why: str) -> Signal | None:
    risk = b.close - stop
    if not math.isfinite(risk) or risk < MIN_RISK_TICKS * TICK:
        return None
    if target is None and r is not None:
        target = b.close + r * risk
    if target is not None and target - b.close < risk:
        return None                          # under 1R: not worth a trade
    return Signal(name, 1, b.ts, b.close, stop, target, trail, minutes, why)


def vwap_reclaim(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    if i < 3 or not _same_session(f, i, 3) or f.atr[i] is None:
        return None
    vals = [f.vwap[j] for j in range(i - 3, i + 1)]
    if any(v is None for v in vals):
        return None
    below = all(b[j].close < vals[k] for k, j in enumerate(range(i - 3, i)))
    if below and b[i].close > vals[3]:
        low = min(b[j].low for j in range(i - 3, i + 1))
        return _sig("vwap_reclaim", b[i], low - 0.25 * f.atr[i], minutes=90,
                    why=f"3 closes under VWAP, reclaimed at {b[i].close:.2f}")
    return None


def vwap_reject(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    """Long form: VWAP held as support from above."""
    if i < 3 or not _same_session(f, i, 3) or f.atr[i] is None:
        return None
    vals = [f.vwap[j] for j in range(i - 3, i + 1)]
    if any(v is None for v in vals):
        return None
    above = all(b[j].close > vals[k] for k, j in enumerate(range(i - 3, i)))
    if above and b[i].low <= vals[3] < b[i].close:
        return _sig("vwap_reject", b[i], b[i].low - 0.25 * f.atr[i], minutes=90,
                    why=f"VWAP {vals[3]:.2f} touched and held")
    return None


def vwap_band_fade(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    if i < 1 or not _same_session(f, i, 1) or f.atr[i] is None:
        return None
    lo_prev, lo_now, v = f.lower[i - 1], f.lower[i], f.vwap[i]
    if lo_prev is None or lo_now is None or v is None:
        return None
    if b[i - 1].close < lo_prev and b[i].close > lo_now:
        stop = min(b[i - 1].low, b[i].low) - 0.25 * f.atr[i]
        return _sig("vwap_band_fade", b[i], stop, r=None, target=v, minutes=120,
                    why=f"back inside the -2σ band, target VWAP {v:.2f}")
    return None


def _orb(name: str, ranges: list[tuple[float, float] | None],
         stop_of: Callable[[tuple[float, float]], float],
         b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    rng = ranges[i]
    if rng is None or f.atr[i] is None:
        return None
    t = et(b[i].ts).time()
    if not (t < ORB_LAST) or b[i].close <= rng[0]:
        return None
    j = i - 1
    while j >= 0 and f.session[j] == f.session[i] and ranges[j] is not None:
        if b[j].close > rng[0]:
            return None                      # not the first break
        j -= 1
    return _sig(name, b[i], stop_of(rng), minutes=120,
                why=f"opening range {rng[1]:.2f}-{rng[0]:.2f} broken")


def orb15(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    return _orb("orb15", f.or15, lambda r: r[1], b, f, i)


def orb30(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    return _orb("orb30", f.or30, lambda r: (r[0] + r[1]) / 2, b, f, i)


def pdh_sweep(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    """Long form: the previous session's low is swept and rejected."""
    pdl, a = f.pdl[i], f.atr[i]
    if pdl is None or a is None:
        return None
    if b[i].low < pdl < b[i].close:
        return _sig("pdh_sweep", b[i], b[i].low - 0.25 * a, minutes=120,
                    why=f"swept the prior session low {pdl:.2f} and closed back above")
    return None


def fvg_fill(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    a = f.atr[i]
    if a is None or b[i].close <= b[i].open:
        return None
    for k in range(1, FVG_LOOKBACK + 1):
        lo_i, hi_i = i - k - 2, i - k
        if lo_i < 0 or f.session[lo_i] != f.session[i]:
            break
        bottom, top = b[lo_i].high, b[hi_i].low
        if bottom < top and b[i].low <= top < b[i].close:
            return _sig("fvg_fill", b[i], bottom - 0.25 * a, minutes=120,
                        why=f"fair value gap {bottom:.2f}-{top:.2f} filled and held")
    return None


def order_block(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    a = f.atr[i]
    if a is None or b[i].close <= b[i].open:
        return None
    for j in range(i - 2, max(i - OB_LOOKBACK, 3) - 1, -1):
        aj = f.atr[j]
        if aj is None or f.session[j - 3] != f.session[i]:
            continue
        if b[j].close - b[j - 3].close < OB_IMPULSE_ATR * aj:
            continue
        for m in range(j - 3, max(j - 8, 0) - 1, -1):
            if b[m].close < b[m].open:
                if any(b[t].close < b[m].low for t in range(j + 1, i)):
                    return None          # the block was broken since
                if b[i].low <= b[m].high < b[i].close:
                    return _sig("order_block", b[i], b[m].low - 0.25 * a,
                                minutes=120,
                                why=f"order block {b[m].low:.2f}-{b[m].high:.2f} "
                                    f"retested after a {OB_IMPULSE_ATR:g} ATR impulse")
                return None
        return None
    return None


def bos(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    a = f.atr[i]
    if a is None or i < 1:
        return None
    highs, lows = swings(f.ph, i, 1), swings(f.pl, i, 2)
    if not highs or len(lows) < 2:
        return None
    (jh, ph), (_, l1), (_, l2) = highs[0], lows[0], lows[1]
    if i - jh > STRUCTURE_LOOKBACK or l2 <= l1:
        return None
    if b[i - 1].close <= ph < b[i].close:
        return _sig("bos", b[i], l2 - 0.1 * a, minutes=180,
                    why=f"higher low {l2:.2f}, first close over swing high {ph:.2f}")
    return None


def choch(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    a = f.atr[i]
    if a is None or i < 1:
        return None
    highs, lows = swings(f.ph, i, 2), swings(f.pl, i, 2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    (_, h1), (jh, h2) = highs
    (_, l1), (_, l2) = lows
    if i - jh > STRUCTURE_LOOKBACK or not (h2 < h1 and l2 < l1):
        return None
    if b[i - 1].close <= h2 < b[i].close:
        return _sig("choch", b[i], l2 - 0.1 * a, minutes=180,
                    why=f"lower highs and lows, then a close over {h2:.2f}")
    return None


def eqh_sweep(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    """Long form: equal lows swept, then a close back above them."""
    a = f.atr[i]
    if a is None:
        return None
    lows = [(j, p) for j, p in swings(f.pl, i, 6) if i - j <= EQH_LOOKBACK]
    for k in range(len(lows) - 1, 0, -1):
        (_, p1), (_, p2) = lows[k - 1], lows[k]
        if abs(p1 - p2) <= EQH_TOLERANCE * abs(p2):
            if b[i].low < min(p1, p2) and b[i].close > max(p1, p2):
                return _sig("eqh_sweep", b[i], b[i].low - 0.25 * a, minutes=120,
                            why=f"equal lows {p1:.2f}/{p2:.2f} swept and reclaimed")
            return None
    return None


def volume_imbalance(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    a, avg = f.atr[i], f.avg_vol[i]
    if a is None or avg is None or avg <= 0 or i < 1 or b[i].volume is None:
        return None
    rng = b[i].high - b[i].low
    if rng <= 0:
        return None
    if (b[i].volume >= VOLUME_MULT * avg and b[i].close > b[i - 1].high
            and (b[i].close - b[i].low) / rng >= 0.75):
        return _sig("volume_imbalance", b[i], b[i].low - 0.25 * a, minutes=90,
                    why=f"volume {b[i].volume / avg:.1f}x the 20-bar average, "
                        f"close over the prior high")
    return None


def inside_bar(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    a = f.atr[i]
    if a is None or i < 2 or not _same_session(f, i, 2):
        return None
    m, ib = b[i - 2], b[i - 1]
    if ib.high <= m.high and ib.low >= m.low and b[i].close > m.high:
        return _sig("inside_bar", b[i], m.low - 0.1 * a, minutes=90,
                    why=f"inside bar, mother high {m.high:.2f} broken")
    return None


def atr_trail(b: Sequence[Bar], f: Features, i: int) -> Signal | None:
    a = f.atr[i]
    if a is None or i < 3 or f.sma[i] is None or f.sma[i - 3] is None:
        return None
    if f.sma[i - 1] is None:
        return None
    if b[i].close > f.sma[i] > f.sma[i - 3] and b[i - 1].close <= f.sma[i - 1]:
        return _sig("atr_trail", b[i], b[i].close - TRAIL_ATR * a, r=None,
                    trail=TRAIL_ATR, minutes=240,
                    why=f"close crossed a rising SMA{SMA_N} {f.sma[i]:.2f}; "
                        f"{TRAIL_ATR:g} ATR trail")
    return None


ENGINES: dict[str, tuple[Rule, tuple[str, ...]]] = {
    "vwap_reclaim": (vwap_reclaim, NY),
    "vwap_reject": (vwap_reject, ALL_SESSIONS),
    "vwap_band_fade": (vwap_band_fade, NY),
    "orb15": (orb15, ("ny_open",)),
    "orb30": (orb30, ("ny_open",)),
    "pdh_sweep": (pdh_sweep, ALL_SESSIONS),
    "fvg_fill": (fvg_fill, ALL_SESSIONS),
    "order_block": (order_block, ALL_SESSIONS),
    "bos": (bos, ALL_SESSIONS),
    "choch": (choch, ALL_SESSIONS),
    "eqh_sweep": (eqh_sweep, ALL_SESSIONS),
    "volume_imbalance": (volume_imbalance, NY),
    "inside_bar": (inside_bar, ALL_SESSIONS),
    "atr_trail": (atr_trail, ("london", "ny_open", "ny_pm")),
}


@dataclass(slots=True)
class Prepared:
    """Bars with their features, both ways round, computed once."""

    bars: list[Bar]
    f: Features
    mbars: list[Bar]
    mf: Features


def prepare(bars: Sequence[Bar]) -> Prepared:
    bars = list(bars)
    m = mirror(bars)
    return Prepared(bars, compute(bars), m, compute(m))


def _flip(s: Signal) -> Signal:
    return Signal(s.engine, -1, s.at, -s.ref, -s.stop,
                  None if s.target is None else -s.target,
                  s.trail_atr, s.time_stop_min, s.reason)


def evaluate(p: Prepared, i: int, engines: Sequence[str] | None = None
             ) -> list[Signal]:
    """Every engine's signal on closed bar i (both sides), in rulebook
    order. Pure: reads bars 0..i only."""
    zone = killzone(p.bars[i].ts)
    out: list[Signal] = []
    for name, (rule, sessions) in ENGINES.items():
        if engines is not None and name not in engines:
            continue
        if zone not in sessions:
            continue
        long = rule(p.bars, p.f, i)
        if long is not None:
            out.append(long)
        short = rule(p.mbars, p.mf, i)
        if short is not None:
            out.append(_flip(short))
    return out
