"""The simulator's honesty guards, the metrics, the halves, the prop-firm
arithmetic, the contract specs, and the proof that nothing in the module
can place an order: every request it can make is a GET to Yahoo.
"""
from __future__ import annotations

import ast
import inspect
import json
import math
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from futures_support import MONDAY, TUESDAY, et, random_walk

from atlas.modules import futures
from atlas.modules.futures import backtest, book, data, engines, job, propfirm, report
from atlas.modules.futures.backtest import Book, Policy, Signal, new_book, step
from atlas.modules.futures.engines import Bar

MNQ = backtest.spec("MNQ=F")
QUIET = Policy(engines=(), daily_stop=None, breaker=None)   # no engine may add trades


def long_signal(at: datetime, ref: float = 100.0, stop: float = 95.0,
                target: float | None = 110.0, trail: float | None = None,
                minutes: int = 60, engine: str = "t") -> Signal:
    return Signal(engine, 1, at, ref, stop, target, trail, minutes, "test")


def short_signal(at: datetime, ref: float = 100.0, stop: float = 105.0,
                 target: float | None = 90.0) -> Signal:
    return Signal("t", -1, at, ref, stop, target, None, 60, "test")


def run_pending(bars: list[Bar], *signals: Signal, policy: Policy = QUIET) -> Book:
    """A book with `signals` pending before bar 1, stepped over all bars."""
    p = engines.prepare(bars)
    b = new_book(MNQ, policy)
    step(b, p, 0)
    b.pending = list(signals)
    for i in range(1, len(bars)):
        step(b, p, i)
    return b


def bar(ts: datetime, o: float, h: float, lo: float, c: float, v: float = 1000) -> Bar:
    return Bar(ts, o, h, lo, c, v, 5)


T0 = et(TUESDAY, 9, 30)


def at(k: int) -> datetime:
    return T0 + timedelta(minutes=5 * k)


# ------------------------------------------------------------ the guards

def test_the_entry_fills_at_the_next_bars_open_plus_a_tick():
    bars = [bar(at(0), 100, 101, 99, 100), bar(at(1), 102, 103, 101, 102.5),
            bar(at(2), 102.5, 103, 102, 102.7)]
    b = run_pending(bars, long_signal(at(0)))
    (pos,) = b.positions
    assert pos.entry.bar_ts == at(1) and pos.entry.raw == 102
    assert pos.entry.price == 102 + MNQ.slippage == 102.25
    assert pos.entry.slippage == 0.25


def test_stop_first_when_one_bar_touches_both():
    bars = [bar(at(0), 100, 101, 99, 100), bar(at(1), 100, 112, 94, 108)]
    b = run_pending(bars, long_signal(at(0), stop=95, target=110))
    (t,) = b.trades
    assert t.exit.reason == "stop" and t.exit.raw == 95
    assert t.exit.price == 95 - MNQ.slippage                  # sold a tick worse
    short = run_pending(bars, short_signal(at(0), stop=105, target=90))
    assert short.trades[0].exit.reason == "stop" and short.trades[0].exit.raw == 105


def test_gap_through_fills_at_the_open_for_the_stop_and_the_target():
    gap_down = [bar(at(0), 100, 101, 99, 100), bar(at(1), 100, 101, 99, 100),
                bar(at(2), 90, 91, 89, 90.5)]
    b = run_pending(gap_down, long_signal(at(0), stop=95, target=110))
    (t,) = b.trades
    assert (t.exit.reason, t.exit.raw, t.exit.bar_ts) == ("stop", 90, at(2))
    gap_up = [bar(at(0), 100, 101, 99, 100), bar(at(1), 100, 101, 99, 100),
              bar(at(2), 115, 116, 114, 115)]
    b = run_pending(gap_up, long_signal(at(0), stop=95, target=110))
    (t,) = b.trades
    assert (t.exit.reason, t.exit.raw) == ("target", 115)


def test_slippage_and_commission_arithmetic_for_mnq():
    bars = [bar(at(0), 100, 101, 99, 100), bar(at(1), 100, 101, 99, 100),
            bar(at(2), 100, 111, 99, 108)]
    b = run_pending(bars, long_signal(at(0), stop=95, target=110))
    (t,) = b.trades
    # In at 100.25, out at 109.75: 9.5 points x $2 = $19, minus 2 x $0.50.
    assert t.entry.price == 100.25 and t.exit.price == 109.75
    assert t.pnl == pytest.approx(9.5 * 2 - 1.0)
    assert t.commission == 1.0
    assert t.r_multiple == pytest.approx((109.75 - 100.25) / (100.25 - 95))
    assert b.equity == pytest.approx(50_000 + 18.0)
    nq = backtest.spec("NQ=F")
    assert (nq.point_value, nq.tick_size, nq.commission_per_side) == (20.0, 0.25, 2.0)
    assert MNQ.point_value == 2.0 and MNQ.tick_size == 0.25


def test_the_time_stop_exits_at_the_close_and_the_cash_close_flattens():
    steady = [bar(at(k), 100, 100.5, 99.5, 100) for k in range(15)]
    b = run_pending(steady, long_signal(at(0), minutes=30))
    (t,) = b.trades
    # In at bar 1's open; 30 minutes later is bar 6's close.
    assert t.exit.reason == "time" and t.exit.bar_ts == at(6) and t.exit.raw == 100
    late = [bar(et(TUESDAY, 15, 45) + timedelta(minutes=5 * k), 100, 100.5, 99.5, 100)
            for k in range(4)]
    b = run_pending(late, long_signal(late[0].ts, minutes=600))
    (t,) = b.trades
    assert t.exit.reason == "eod" and t.exit.bar_ts == et(TUESDAY, 15, 55)


def test_the_trailing_stop_moves_on_closed_bars_and_binds_on_the_next():
    steady = [bar(at(k), 100, 100.5, 99.5, 100) for k in range(20)]   # ATR ~ 1
    p = engines.prepare(steady + [bar(at(20), 100, 100.5, 99.5, 100),
                                  bar(at(21), 100, 121, 99.5, 120),      # a big up bar
                                  bar(at(22), 120, 121, 119, 120),       # trail from 21
                                  bar(at(23), 120, 121, 100, 100.5)])    # through it
    b = new_book(MNQ, QUIET)
    for i in range(20):
        step(b, p, i)
    b.pending = [long_signal(at(19), stop=90, target=None, trail=1.0, minutes=600)]
    for i in range(20, 24):
        step(b, p, i)
    (t,) = b.trades
    assert t.exit.reason == "trail" and t.exit.bar_ts == at(23)
    # The trail binds on bar 23 at the level set from bar 22's close and
    # bar 22's ATR: well above the initial 90, below bar 22's close.
    assert 90 < t.exit.raw < 120
    assert t.exit.raw == pytest.approx(120 - p.f.atr[22], abs=1e-9)


def test_only_the_pending_signals_engine_and_the_open_caps_limit_fills():
    bars = [bar(at(k), 100, 100.5, 99.5, 100) for k in range(4)]
    sigs = [long_signal(at(0), engine=f"e{i}", target=None, trail=None, minutes=600)
            for i in range(5)]
    for s in sigs:
        object.__setattr__(s, "target", 150.0)
    b = run_pending(bars, *sigs, policy=Policy(engines=(), daily_stop=None, breaker=None,
                                               max_open=3))
    assert len(b.positions) == 3 and b.cancelled == 2


def test_the_daily_stop_flattens_and_blocks_the_rest_of_the_session():
    bars = [bar(at(0), 100, 101, 99, 100), bar(at(1), 100, 101, 99, 100),
            bar(at(2), 100, 101, 80, 85), bar(at(3), 85, 86, 84, 85)]
    pol = Policy(engines=(), daily_stop=10.0, breaker=None)
    b = run_pending(bars, long_signal(at(0), stop=95, target=200), policy=pol)
    assert b.day_halted and b.trades[0].exit.reason == "stop"
    # A second signal that session is cancelled at fill time.
    b.pending = [long_signal(at(3), stop=80, target=200)]
    p = engines.prepare(bars + [bar(at(4), 85, 86, 84, 85)])
    step(b, p, 4)
    assert b.positions == [] and b.cancelled == 1
    # Next session: the halt lifts.
    nxt = bar(et(TUESDAY, 18, 0), 85, 86, 84, 85)
    p = engines.prepare(bars + [bar(at(4), 85, 86, 84, 85), nxt])
    step(b, p, 5)
    assert not b.day_halted and b.day_pnl == 0


def test_the_breaker_flattens_and_halts_until_the_next_session_in_a_backtest():
    bars = [bar(at(0), 100, 101, 99, 100), bar(at(1), 100, 101, 99, 100),
            bar(at(2), 100, 101, 60, 70), bar(at(3), 70, 71, 69, 70)]
    pol = Policy(engines=(), daily_stop=None, breaker=20.0)
    b = run_pending(bars, long_signal(at(0), stop=50, target=200), policy=pol)
    assert b.halted and b.breaker_hits == 1 and b.trades[0].exit.reason == "breaker"
    assert b.breaker_tripped_at == at(2) + timedelta(minutes=5)
    nxt = bar(et(TUESDAY, 18, 0), 70, 71, 69, 70)
    p = engines.prepare(bars + [nxt])
    step(b, p, 4)
    assert not b.halted and b.peak == pytest.approx(b.equity)
    stay = Policy(engines=(), daily_stop=None, breaker=20.0, resume_next_session=False)
    b2 = run_pending(bars + [nxt], long_signal(at(0), stop=50, target=200), policy=stay)
    assert b2.halted


def test_signals_are_read_from_closed_bars_only_and_fill_next_bar():
    """The real engines over a random walk: every trade's entry bar is
    the bar right after its signal bar, never the signal bar itself."""
    bars = random_walk(sessions=2, seed=11)
    b = backtest.run(bars, MNQ, Policy())
    assert b.trades, "the walk should produce trades"
    index = {x.ts: k for k, x in enumerate(bars)}
    for t in b.trades:
        assert index[t.entry.bar_ts] == index[t.signal.at] + 1
        assert t.exit.bar_ts >= t.entry.bar_ts
        assert t.session == engines.session_date(t.entry.bar_ts)
        assert engines.killzone(t.signal.at) in engines.ENGINES[t.engine][1]


def test_a_backtest_never_reads_the_future_either():
    """Mutating bars after N leaves every trade decided by N unchanged."""
    bars = random_walk(sessions=2, seed=5)
    cut = len(bars) * 2 // 3
    full = backtest.run(bars, MNQ, Policy())
    wild = bars[:cut] + [Bar(x.ts, 1.0, 2.0, 0.5, 1.5, 1e9, 5) for x in bars[cut:]]
    changed = backtest.run(wild, MNQ, Policy())
    before = [(t.engine, t.signal.at, t.entry.price, t.exit.bar_ts, t.exit.price)
              for t in full.trades if t.exit.bar_ts < bars[cut].ts]
    after = [(t.engine, t.signal.at, t.entry.price, t.exit.bar_ts, t.exit.price)
             for t in changed.trades if t.exit.bar_ts < bars[cut].ts]
    assert before and before == after


# ------------------------------------------------------------- metrics

def test_metrics_and_the_walk_forward_halves():
    bars = random_walk(sessions=4, seed=7)
    s = backtest.summary(bars, MNQ, gaps=0)
    c = s["combined"]
    assert c["trades"] > 0 and 0 <= c["win_rate"] <= 100
    assert c["max_drawdown"] >= 0 and c["avg_hold_min"] > 0
    assert set(s["engines"]) == set(engines.ENGINES)
    assert s["halves"]["split_at"] is not None
    assert s["halves"]["first"]["trades"] + s["halves"]["second"]["trades"] == c["trades"]
    assert s["propfirm"]["status"] in ("passed", "failed", "incomplete")
    assert "survivorship" in " ".join(s["notes"]) and "paper, not advice" in s["notes"]
    json.dumps(s)                                                # JSON-ready
    m = backtest.metrics([], 50_000)
    assert m["trades"] == 0 and m["win_rate"] is None and m["expectancy_r"] is None


def test_metrics_arithmetic_on_known_trades():
    bars = [bar(at(0), 100, 101, 99, 100), bar(at(1), 100, 101, 99, 100),
            bar(at(2), 100, 111, 99, 108)]
    win = run_pending(bars, long_signal(at(0), stop=95, target=110)).trades[0]
    lose = run_pending([bars[0], bars[1], bar(at(2), 100, 101, 90, 92)],
                       long_signal(at(0), stop=95, target=110)).trades[0]
    m = backtest.metrics([win, lose], 50_000)
    assert m["trades"] == 2 and m["win_rate"] == 50.0
    assert m["net_pnl"] == pytest.approx(win.pnl + lose.pnl)
    assert m["profit_factor"] == pytest.approx(win.pnl / -lose.pnl, abs=0.01)
    assert m["max_drawdown"] == pytest.approx(-lose.pnl)
    assert m["curve"][-1][1] == pytest.approx(50_000 + win.pnl + lose.pnl)
    assert m["sharpe"] is None                                    # one session


# ----------------------------------------------------------- prop firm

def _trade(day, pnl: float) -> backtest.Trade:
    ts = et(day, 10, 0)
    f = backtest.Fill("entry", ts, 100, 100, 0, "signal")
    x = backtest.Fill("exit", ts + timedelta(minutes=30), 100, 100, 0, "target")
    return backtest.Trade("t", 1, 1, long_signal(ts), day, f, x, 95, 110, 1.0, pnl, 1.0)


def test_prop_firm_rules_are_illustrative_and_the_arithmetic_is_exact():
    r = propfirm.rules()
    assert (r["account"], r["profit_target"], r["daily_loss_limit"],
            r["trailing_max_drawdown"], r["min_trading_days"]) == (50000, 3000, 1000,
                                                                   2000, 5)
    assert "illustrative" in json.loads(propfirm.RULES.read_text())["_note"].lower()
    days = [MONDAY + timedelta(days=k) for k in range(5)]
    passed = propfirm.check([_trade(d, 700) for d in days], r, 50_000)
    assert passed["status"] == "passed" and passed["days_traded"] == 5
    short = propfirm.check([_trade(d, 1600) for d in days[:2]], r, 50_000)
    assert short["status"] == "incomplete"                        # 2 days < 5
    daily = propfirm.check([_trade(days[0], -600), _trade(days[0], -450)], r, 50_000)
    assert daily["status"] == "failed" and daily["rule"] == "daily_loss_limit"
    trail = propfirm.check([_trade(days[0], 900), _trade(days[1], 900),
                            _trade(days[2], -900), _trade(days[3], -900),
                            _trade(days[4], -250)], r, 50_000)
    assert trail["status"] == "failed" and trail["rule"] == "trailing_max_drawdown"
    assert trail["session"] == days[4].isoformat()
    assert "illustrative" in propfirm.line(trail)


# ----------------------------------------------------- never a broker

def _code_words(tree: ast.AST) -> tuple[set[str], list[str]]:
    """Every identifier, attribute and keyword name in the code, and every
    string constant that is not a docstring (a docstring may quote the
    reel; code may not name a broker)."""
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef
                      | ast.AsyncFunctionDef):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docs.add(id(body[0].value))
    names: set[str] = set()
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg.lower())
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and id(node) not in docs):
            strings.append(node.value)
    return names, strings


def test_no_code_path_can_place_an_order():
    """Static, over every file in the module: no POST/PUT/PATCH/DELETE call,
    no `method=` argument, no broker vocabulary in any identifier or
    non-docstring string, and the only URL is Yahoo's chart endpoint on
    query1/query2. The MockTransport tests prove the dynamic side: every
    request that leaves is a GET to those hosts."""
    root = pathlib.Path(futures.__file__).parent
    forbidden = {"post", "put", "patch", "delete", "method", "broker", "broker_api",
                 "brokerage", "alpaca", "ninjatrader", "tradovate", "rithmic",
                 "place_order", "submit_order", "interactive_brokers", "ibapi", "ccxt",
                 "order", "orders", "buy", "sell", "trade_api"}
    for path in sorted(root.glob("*.py")):
        names, strings = _code_words(ast.parse(path.read_text(), str(path)))
        hit = names & forbidden
        assert not hit, (path.name, hit)
        for text in strings:
            low = text.lower()
            for word in ("alpaca", "ninjatrader", "tradovate", "rithmic", "place_order",
                         "submit_order", "ibapi", "ccxt", "broker_api"):
                assert word not in low, (path.name, word, text[:60])
            if "://" in low:
                assert text.startswith("https://query1.finance.yahoo.com/v8/finance/chart/"), \
                    (path.name, text)
    assert data.HOSTS == ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
    assert futures.MODULE.actions() == ()
    src = inspect.getsource(data.fetch)
    assert "c.get(" in src and ".post(" not in src


def test_the_scan_sees_what_it_forbids():
    """Positive control: an order path in code (not a docstring) is caught."""
    names, strings = _code_words(ast.parse(
        '"""ninjatrader is only quoted here"""\n'
        'async def go(c):\n    return await c.post("https://api.broker.example/orders")\n'))
    assert {"post"} <= names and "ninjatrader" not in " ".join(strings)
    assert any("api.broker" in s for s in strings)


def test_the_rulebook_and_specs_exist_and_agree_with_the_code():
    root = pathlib.Path(futures.__file__).parent
    rule = (root / "rulebook.md").read_text()
    for name in engines.ENGINES:
        assert f"`{name}`" in rule, name
    for word in ("Stop first", "Gap-through", "No look-ahead", "Survivorship", "unofficial",
                 "never trades"):
        assert word in rule
    specs = json.loads((root / "contracts.json").read_text())
    assert specs["MNQ=F"]["point_value"] == 2.0 and specs["NQ=F"]["point_value"] == 20.0
    assert "tick value" in specs["_note"]


# -------------------------------------------------------------- windows

def test_tick_windows_are_15_minute_marks_inside_cme_hours():
    now = et(TUESDAY, 10, 3)
    ws = job.tick_windows(now, timedelta(hours=2))
    assert [w.key for w in ws][-3:] == ["2026-09-29T09:30", "2026-09-29T09:45",
                                        "2026-09-29T10:00"]
    assert all(engines.in_cme_hours(w.scheduled_for) for w in ws)
    closed = job.tick_windows(et(TUESDAY, 17, 30), timedelta(hours=1))
    assert [w.key for w in closed] == ["2026-09-29T16:30", "2026-09-29T16:45",
                                       "2026-09-29T17:00"][:2]
    saturday = job.tick_windows(et(MONDAY + timedelta(days=5), 12, 0), timedelta(hours=2))
    assert saturday == []


def test_daily_and_weekly_windows_and_the_policies():
    from atlas.kernel.missed import Missed
    d = job.daily_windows(et(TUESDAY, 16, 20), timedelta(days=4))
    assert [w.key for w in d] == ["2026-09-25", "2026-09-28", "2026-09-29"]
    assert d[-1].scheduled_for == datetime.combine(TUESDAY, job.DAILY_AT, tzinfo=engines.TZ)
    assert job.daily_windows(et(TUESDAY, 16, 10), timedelta(days=0)) == []
    w = job.weekly_windows(et(TUESDAY, 12, 0), timedelta(weeks=2))
    assert [x.key for x in w] == ["2026-09-11", "2026-09-18", "2026-09-25"]
    p = job.POLICIES
    assert p["futures_tick"].missed is Missed.SKIP_STALE
    assert p["futures_tick"].grace == timedelta(minutes=12)
    assert p["futures_daily"].missed is Missed.SKIP_STALE
    assert p["futures_weekly"].missed is Missed.RUN_ONCE
    assert set(job.JOBS) == set(p)
    assert job.symbol() == "MNQ=F"


def test_the_symbol_env_only_allows_the_two_contracts(monkeypatch):
    monkeypatch.setenv("FUTURES_SYMBOL", "nq=f")
    assert job.symbol() == "NQ=F"
    monkeypatch.setenv("FUTURES_SYMBOL", "ES=F")
    assert job.symbol() == "MNQ=F"


# ------------------------------------------------------------ the check

async def test_the_check_is_unknown_when_nothing_was_read(monkeypatch):
    async def none(*a, **k):
        return None
    monkeypatch.setattr(data, "last_read", none)
    monkeypatch.setattr(book, "state", none)
    obs = await futures.check_futures(datetime.now(UTC))
    assert obs.health.value == "unknown" and "never read" in obs.detail


async def test_the_check_is_unknown_when_the_tables_cannot_be_read(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("no database")
    monkeypatch.setattr(data, "last_read", boom)
    obs = await futures.check_futures(datetime.now(UTC))
    assert obs.health.value == "unknown" and "unreadable" in obs.detail


def test_last_cme_moment():
    assert futures.last_cme_moment(et(TUESDAY, 10, 0)) == et(TUESDAY, 10, 0)
    assert futures.last_cme_moment(et(TUESDAY, 17, 30)) == et(TUESDAY, 16, 59)
    sat = MONDAY + timedelta(days=5)
    assert futures.last_cme_moment(et(sat, 12, 0)) == et(MONDAY + timedelta(days=4), 16, 59)


# --------------------------------------------------------- the model note

def test_a_model_note_may_only_cite_figures_it_was_given():
    block = "Forward book: 23 closed, 39% won, +0.02R avg, -$412.50 → equity $49,587.50"
    assert report.cited("23 trades at 39% is a coin flip; -412.50 so far", block)
    assert not report.cited("57% of them won", block)
    assert not report.cited("that is about 3 losers a day", block)
    assert math.isfinite(report.NOTE_MAX) and report.NOTE_MAX == 240


async def test_the_scoreboard_stands_without_a_model(monkeypatch):
    async def fail(*a, **k):
        raise RuntimeError("no model")
    monkeypatch.setattr("atlas.kernel.llm.think", fail)
    assert await report.explain("x 1 2 3") is None
