"""The rulebook's 14 engines: each fires on a constructed pattern and not on
its control, the short side is the exact mirror, and no signal on bar N
reads anything after bar N (every later bar is mutated and the signal is
unchanged). Sessions, killzones and the feature maths are pinned too.
"""
from __future__ import annotations

import random
from datetime import UTC, datetime, time, timedelta

import pytest
from futures_support import MONDAY, TUESDAY, WICK, bars_from, et, morning, ramp

from atlas.modules.futures import engines
from atlas.modules.futures.engines import (
    TZ,
    Bar,
    compute,
    evaluate,
    in_cme_hours,
    killzone,
    mirror,
    prepare,
    session_date,
)


def fired(bars: list[Bar], i: int | None = None, only: tuple[str, ...] | None = None
          ) -> dict[str, int]:
    i = len(bars) - 1 if i is None else i
    return {s.engine: s.side for s in evaluate(prepare(bars), i, only)}


def one(bars: list[Bar], engine: str) -> engines.Signal:
    got = [s for s in evaluate(prepare(bars), len(bars) - 1, (engine,))]
    assert len(got) == 1, got
    return got[0]


# ---------------------------------------------------------------- sessions

def test_sessions_are_cme_sessions_in_new_york():
    assert session_date(et(TUESDAY, 9, 30)) == TUESDAY
    assert session_date(et(MONDAY, 18, 0)) == TUESDAY          # Monday evening opens Tuesday
    assert session_date(et(MONDAY, 16, 59)) == MONDAY
    sunday = MONDAY - timedelta(days=1)
    assert session_date(et(sunday, 18, 0)) == MONDAY


@pytest.mark.parametrize(("d", "hh", "mm", "inside"), [
    (TUESDAY, 9, 30, True), (TUESDAY, 17, 0, False), (TUESDAY, 17, 59, False),
    (TUESDAY, 18, 0, True), (date := MONDAY - timedelta(days=1), 17, 0, False),
    (date, 18, 0, True), (MONDAY - timedelta(days=2), 12, 0, False),
    (MONDAY + timedelta(days=4), 16, 59, True), (MONDAY + timedelta(days=4), 17, 0, False),
    (MONDAY + timedelta(days=4), 18, 30, False),
])
def test_cme_hours(d, hh, mm, inside):
    assert in_cme_hours(et(d, hh, mm)) is inside


def test_killzones_are_named_by_the_bars_open_time():
    assert killzone(et(TUESDAY, 3, 0)) == "london"
    assert killzone(et(TUESDAY, 5, 0)) is None
    assert killzone(et(TUESDAY, 9, 30)) == "ny_open"
    assert killzone(et(TUESDAY, 11, 0)) is None
    assert killzone(et(TUESDAY, 13, 30)) == "ny_pm"
    assert killzone(et(TUESDAY, 15, 30)) is None


def test_the_bar_that_reaches_the_cash_close_ends_the_day():
    b = bars_from([100, 100, 100], et(TUESDAY, 15, 50))
    assert [engines.ends_cash_day(x) for x in b] == [False, True, False]
    hourly = bars_from([100, 100], et(TUESDAY, 15, 0), minutes=60)
    assert [engines.ends_cash_day(x) for x in hourly] == [True, False]


# ---------------------------------------------------------------- features

def test_vwap_atr_and_pivots_are_causal_and_right():
    b = bars_from([100, 102, 101, 103, 102, 104, 103, 105, 104, 103, 102, 101, 100, 99,
                   98, 99, 100, 101, 102, 103, 104], et(TUESDAY, 9, 30))
    f = compute(b)
    # VWAP of the first bar is its typical price.
    assert f.vwap[0] == pytest.approx((b[0].high + b[0].low + b[0].close) / 3)
    assert f.vwap[1] == pytest.approx(
        ((b[0].high + b[0].low + b[0].close) / 3 + (b[1].high + b[1].low + b[1].close) / 3)
        / 2)
    assert f.atr[12] is None and f.atr[14] is not None and f.atr[14] > 0
    assert f.avg_vol[19] is None and f.avg_vol[20] == pytest.approx(1000)
    # The swing high at index 7 (105) is known from bar 10 on, not before.
    assert (7, b[7].high) in f.ph
    assert engines.swings(f.ph, 9, 1) == [] or engines.swings(f.ph, 9, 1)[-1][0] != 7
    assert engines.swings(f.ph, 10, 1)[-1][0] == 7
    # A new session restarts the VWAP.
    c = bars_from([100, 100], et(TUESDAY, 16, 55))
    c += bars_from([200, 200], et(TUESDAY, 18, 0))
    g = compute(c)
    assert g.vwap[2] == pytest.approx((c[2].high + c[2].low + c[2].close) / 3)
    assert g.pdh[2] == c[0].high and g.pdl[2] == c[0].low


def test_the_opening_range_needs_bars_no_longer_than_itself():
    f = compute(bars_from([100, 101, 100, 102, 101], et(TUESDAY, 9, 30)))
    assert f.or15[2] is None and f.or15[3] is not None
    assert f.or15[3] == (max(b.high for b in bars_from([100, 101, 100], et(TUESDAY, 9, 30))),
                         min(b.low for b in bars_from([100, 101, 100], et(TUESDAY, 9, 30))))
    hourly = compute(bars_from([100, 101, 102], et(TUESDAY, 9, 30), minutes=60))
    assert hourly.or15 == [None] * 3 and hourly.or30 == [None] * 3


# ----------------------------------------------------------------- engines
#
# Each pattern ends on the bar under test, inside ny_open (09:30-11:00 ET)
# after a 42-bar monotonic warm-up from 06:00 ET. `control` is the same
# pattern with the deciding bar changed so the rule does not hold.

def vwap_reclaim_bars(control: bool = False) -> list[Bar]:
    last = 99.5 if control else 100.6
    return morning([99.0, 98.8, 99.0, last])


def vwap_reject_bars(control: bool = False) -> list[Bar]:
    b = morning([100.5, 100.6, 100.5, 100.4], lows={45: 99.9})
    if control:
        b = morning([100.5, 100.6, 100.5, 99.8], lows={45: 99.7})
    return b


def vwap_band_fade_bars(control: bool = False) -> list[Bar]:
    # Two plateaus give the typical price a real spread (a wide ±2σ band)
    # while the ATR stays small: the bounce back inside is then over 1R.
    warm = [99.0] * 21 + [101.0] * 21
    base = bars_from(warm, et(TUESDAY, 6, 0))
    f = compute(base)
    lower, vwap = f.lower[-1], f.vwap[-1]
    assert lower is not None and vwap is not None
    out = lower - 0.05                       # closes just under the -2σ band
    back = lower + 0.15                      # then just back inside
    if control:
        back = lower - 0.02                  # still outside: no fade
    return bars_from([*warm, out, back], et(TUESDAY, 6, 0),
                     lows={42: out - 0.1, 43: out - 0.05})


def orb15_bars(control: bool = False) -> list[Bar]:
    last = 100.7 if control else 101.4
    return morning([100.0, 100.5, 100.0, 100.5, last], highs={42: 101.0, 43: 100.9, 44: 100.8})


def orb30_bars(control: bool = False) -> list[Bar]:
    last = 100.9 if control else 101.5
    return morning([100.0, 100.5, 100.0, 100.5, 100.2, 100.4, last],
                   highs={42: 101.0, 43: 100.9, 44: 100.8, 45: 100.9, 46: 100.7, 47: 100.8})


def pdh_sweep_bars(control: bool = False) -> list[Bar]:
    monday = bars_from([100.0] * 6 + [96.0, 95.5, 96.0] + [100.0] * 6, et(MONDAY, 10, 0),
                       lows={7: 95.0})
    sweep = 95.2 if control else 94.5
    tuesday = morning([99.0, 98.0, 97.0, 96.0, 95.6], lows={46: sweep})
    return monday + tuesday


def fvg_fill_bars(control: bool = False) -> list[Bar]:
    dip = 100.9 if control else 100.6
    return morning([100.0, 101.5, 101.4, 101.6, 101.5, 101.0, 101.2],
                   highs={42: 100.2}, lows={44: 100.8, 48: dip})


def order_block_bars(control: bool = False) -> list[Bar]:
    retest = 100.5 if control else 100.2       # the control never touches the block
    closes = [100.4, 99.8, 100.3, 100.9, 101.6, 101.4, 101.3, 100.6, 100.9]
    return morning(closes, highs={43: 100.3}, lows={43: 99.6, 50: retest})


def bos_bars(control: bool = False) -> list[Bar]:
    last = 101.2 if control else 101.7
    return morning([100.0, 99.5, 99.0, 99.5, 100.0, 100.5, 101.0, 100.6, 100.2, 99.8,
                    100.1, 100.4, 100.7, 100.9, last])


def choch_bars(control: bool = False) -> list[Bar]:
    last = 101.5 if control else 102.0
    return morning([101.0, 102.0, 101.4, 100.8, 100.5, 100.8, 101.1, 101.5, 101.0, 100.6,
                    100.0, 100.3, 100.6, 100.9, 101.2, last])


def eqh_sweep_bars(control: bool = False) -> list[Bar]:
    pattern = [99.6, 99.3, 99.0, 99.4, 99.8, 100.1, 99.7, 99.3, 99.02, 99.4, 99.8, 100.0,
               99.5]
    low = 98.9 if control else 98.5
    return morning(pattern, lows={42 + 12: low})


def volume_imbalance_bars(control: bool = False) -> list[Bar]:
    vol = 1500.0 if control else 2500.0
    return morning([100.4, 101.4], highs={43: 101.5}, lows={43: 100.3}, volumes={43: vol})


def inside_bar_bars(control: bool = False) -> list[Bar]:
    last = 100.9 if control else 101.2
    return morning([100.5, 100.3, last], highs={42: 101.0, 43: 100.8},
                   lows={42: 99.0, 43: 100.1})


def atr_trail_bars(control: bool = False) -> list[Bar]:
    warm = ramp(42, 100.0, 102.0)
    last = 101.3 if control else 102.1
    return morning([101.4, last], warm=warm)


PATTERNS = {
    "vwap_reclaim": vwap_reclaim_bars, "vwap_reject": vwap_reject_bars,
    "vwap_band_fade": vwap_band_fade_bars, "orb15": orb15_bars, "orb30": orb30_bars,
    "pdh_sweep": pdh_sweep_bars, "fvg_fill": fvg_fill_bars,
    "order_block": order_block_bars, "bos": bos_bars, "choch": choch_bars,
    "eqh_sweep": eqh_sweep_bars, "volume_imbalance": volume_imbalance_bars,
    "inside_bar": inside_bar_bars, "atr_trail": atr_trail_bars,
}


def test_the_rulebook_has_fourteen_engines_and_every_one_has_a_pattern():
    assert len(engines.ENGINES) == 14
    assert set(PATTERNS) == set(engines.ENGINES)


@pytest.mark.parametrize("engine", sorted(PATTERNS))
def test_each_engine_fires_long_on_its_pattern_and_not_on_the_control(engine):
    s = one(PATTERNS[engine](), engine)
    assert s.side == 1 and s.engine == engine
    assert s.stop < s.ref, (s.stop, s.ref)
    if s.target is not None:
        assert s.target > s.ref
        assert s.target - s.ref >= s.ref - s.stop - 1e-9        # at least 1R
    else:
        assert engine == "atr_trail" and s.trail_atr == engines.TRAIL_ATR
    assert s.time_stop_min > 0 and s.reason
    assert engine not in fired(PATTERNS[engine](control=True), only=(engine,))


@pytest.mark.parametrize("engine", sorted(PATTERNS))
def test_the_short_side_is_the_exact_mirror(engine):
    bars = PATTERNS[engine]()
    long = one(bars, engine)
    short = one(mirror(bars), engine)
    assert short.side == -1 and short.engine == engine
    assert short.ref == pytest.approx(-long.ref)
    assert short.stop == pytest.approx(-long.stop)
    if long.target is None:
        assert short.target is None
    else:
        assert short.target == pytest.approx(-long.target)
    assert engine not in fired(mirror(PATTERNS[engine](control=True)), only=(engine,))


@pytest.mark.parametrize("engine", sorted(PATTERNS))
def test_a_signal_on_bar_n_never_reads_bar_n_plus_one(engine):
    bars = PATTERNS[engine]()
    n = len(bars) - 1
    before = evaluate(prepare(bars), n, (engine,))
    rng = random.Random(3)
    future = bars_from([rng.uniform(80, 120) for _ in range(40)],
                       bars[-1].ts + timedelta(minutes=5), volume=9e9)
    wild = [Bar(b.ts, 500.0, 900.0, 1.0, 400.0, 1e9, 5) for b in future]
    for tail in (future, wild):
        after = evaluate(prepare(bars + tail), n, (engine,))
        assert after == before


def test_every_signal_carries_a_stop_a_target_or_a_trail_and_a_time_stop():
    for engine, make in PATTERNS.items():
        s = one(make(), engine)
        assert s.stop != s.ref
        assert (s.target is None) == (s.trail_atr is not None)
        assert s.time_stop_min in (90, 120, 180, 240)


def test_no_engine_fires_outside_its_killzones():
    # The same reclaim pattern at 12:30 ET: no killzone, nothing fires.
    warm = ramp()
    noon = bars_from([*warm, 99.0, 98.8, 99.0, 100.6], et(TUESDAY, 9, 0))
    assert killzone(noon[-1].ts) is None
    assert fired(noon) == {}


def test_a_risk_under_two_ticks_is_discarded():
    flat = bars_from([100.0] * 50, et(TUESDAY, 6, 0))
    assert fired(flat) == {}


def test_engines_only_use_ohlcv():
    """A bar with no volume still produces signals for the rules that do
    not need it, and none for the volume rule."""
    bars = [Bar(b.ts, b.open, b.high, b.low, b.close, None, b.minutes)
            for b in bos_bars()]
    assert "bos" in fired(bars, only=("bos",))
    nv = [Bar(b.ts, b.open, b.high, b.low, b.close, None, b.minutes)
          for b in volume_imbalance_bars()]
    assert fired(nv, only=("volume_imbalance",)) == {}


def test_mirror_swaps_high_and_low_and_keeps_time_and_volume():
    b = bars_from([100, 101], et(TUESDAY, 9, 30))
    m = mirror(b)
    assert m[0].ts == b[0].ts and m[0].volume == b[0].volume
    assert m[0].high == -b[0].low and m[0].low == -b[0].high
    assert m[1].close == -101 and m[1].open == -100


def test_wick_constant_is_what_the_patterns_assume():
    assert WICK == 0.3
    assert datetime(2026, 9, 29, 9, 30, tzinfo=TZ).astimezone(UTC).hour == 13
    assert time(9, 30) == engines.RTH_OPEN
