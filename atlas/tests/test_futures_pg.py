"""The futures module on a real Postgres with a fake Yahoo: bars stored
idempotently, the paper forward book fed tick by tick reaching exactly the
trades a backtest over the same bars reaches, every fill a row, the
breaker pausing the module with one line, the pause honoured and the
resume clearing it, the daily summary and the weekly scoreboard with real
numbers, the `futures` check's states, and the module's own windows
claimed once in `schedule_runs`.

Skipped unless ATLAS_TEST_PG_DSN points at a throwaway database
(tests/acceptance_support.py). It TRUNCATEs the futures tables, reports,
schedule_runs and pauses.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from acceptance_support import needs_pg, pg_fixture, registries_fixture  # noqa: F401
from futures_support import et, random_walk, yahoo_payload

from atlas.kernel import comms, missed, pause, state
from atlas.kernel.contracts import Health, Severity
from atlas.kernel.missed import Window
from atlas.kernel.registry import REGISTRY
from atlas.modules import futures
from atlas.modules.futures import backtest, book, data, engines, job, report
from atlas.modules.futures.backtest import Policy

pytestmark = needs_pg

TABLES = ("futures_bars", "futures_reads", "futures_trades", "futures_fills",
          "futures_state", "futures_backtests")
FIRST = date(2026, 9, 14)          # two weeks before the real clock: bars are closed
SYM = "MNQ=F"


class World:
    def __init__(self) -> None:
        self.bars = random_walk(sessions=4, seed=7, first=FIRST)
        self.cutoff = len(self.bars)
        self.status = 200
        self.holes: list[int] = []
        self.requests: list[httpx.Request] = []
        self.sent: list[comms.Outbound] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET" and request.url.host in data.HOSTS
        if self.status != 200:
            return httpx.Response(self.status, json={"chart": {"result": None,
                                                              "error": {"code": "x"}}})
        return httpx.Response(200, json=yahoo_payload(self.bars[:self.cutoff],
                                                      holes=self.holes))

    async def notify(self, msg: comms.Outbound) -> list[str]:
        self.sent.append(msg)
        return ["slack"]

    def now(self) -> datetime:
        """Just after the newest served bar closed."""
        return self.bars[self.cutoff - 1].end + timedelta(minutes=1)


@pytest.fixture
async def world(pg, monkeypatch):
    await state.execute_sql(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    await state.execute_sql("TRUNCATE reports, schedule_runs, pauses RESTART IDENTITY CASCADE")
    REGISTRY.load("atlas.modules.futures", "futures")
    missed.forget()
    w = World()
    monkeypatch.setattr(data, "transport", httpx.MockTransport(w.handler))
    monkeypatch.setattr(comms, "notify", w.notify)

    async def no_model(block: str) -> None:
        return None

    monkeypatch.setattr(report, "explain", no_model)
    monkeypatch.delenv("FUTURES_SYMBOL", raising=False)
    return w


async def _rows(sql: str, *args) -> list[dict]:
    return [dict(r) for r in await state.fetch_all(sql, *args)]


def _win(now: datetime) -> Window:
    return Window(now.astimezone(engines.TZ).strftime("%Y-%m-%dT%H:%M"), now)


# ------------------------------------------------------------------ storage

async def test_bars_are_stored_once_however_often_they_are_read(world):
    feed = data.parse(yahoo_payload(world.bars[:50]), SYM, "5m", world.bars[49].end)
    assert await data.upsert(feed) == 50
    assert await data.upsert(feed) == 50
    rows = await _rows("SELECT count(*) AS n FROM futures_bars")
    assert rows[0]["n"] == 50
    back = await data.load(SYM, "5m", world.bars[0].ts)
    assert back == world.bars[:50]
    await data.record_read(SYM, "5m", feed)
    last = await data.last_read(SYM, "5m")
    assert last["ok"] and last["bars"] == 50 and last["newest"] == world.bars[49].ts
    await data.record_read(SYM, "5m", None, error=data.Unreadable("HTTP 503", 503))
    last = await data.last_read(SYM, "5m")
    assert not last["ok"] and last["status"] == 503 and "503" in last["detail"]


# --------------------------------------------------------- the forward book

async def _feed(world: World, cutoffs: list[int]) -> list[str]:
    out = []
    for c in cutoffs:
        world.cutoff = c
        out.append(await job.tick(_win(world.now()), world.now()))
    return out


async def test_the_forward_book_reaches_exactly_what_a_backtest_reaches(world):
    n = len(world.bars)
    start = 300
    cutoffs = [start] + list(range(start + 37, n, 37)) + [n]
    statuses = await _feed(world, cutoffs)
    assert statuses[0].startswith("1 bars") and all(
        s.split()[0].isdigit() for s in statuses)
    # The reference: the same step function over the same bars from the
    # same first booked bar.
    p = engines.prepare(world.bars)
    ref = backtest.new_book(backtest.spec(SYM), book.FORWARD)
    for i in range(start - 1, n):
        backtest.step(ref, p, i)
    ledger = await book.trades("WHERE status = 'closed'")
    assert ledger, "the walk should have produced closed paper trades"
    got = sorted((r["engine"], r["side"], r["signal_ts"], r["entry_ts"],
                  round(r["entry_price"], 4), r["exit_ts"], round(r["exit_price"], 4),
                  r["exit_reason"], round(r["pnl"], 4)) for r in ledger)
    want = sorted((t.engine, t.side, t.signal.at, t.entry.bar_ts, round(t.entry.price, 4),
                   t.exit.bar_ts, round(t.exit.price, 4), t.exit.reason, round(t.pnl, 4))
                  for t in ref.trades)
    assert got == want
    st = await book.state()
    assert st["equity"] == pytest.approx(ref.equity)
    assert len(st["positions"]) == len(ref.positions)
    # Every entry and exit is a fill row with its bar, price, slippage, reason.
    fills = await _rows("SELECT trade_id, kind, bar_ts, price, raw_price, slippage, reason "
                        "FROM futures_fills ORDER BY id")
    assert len(fills) == 2 * len(ledger) + len(st["positions"])
    entry = next(f for f in fills if f["kind"] == "entry")
    assert entry["slippage"] == 0.25 and entry["reason"] == "signal"
    assert abs(entry["price"] - entry["raw_price"]) == 0.25
    exits = {f["reason"] for f in fills if f["kind"] == "exit"}
    assert exits <= {"stop", "target", "trail", "time", "eod", "daily_stop", "breaker"}
    # Feeding the same bars again books nothing twice.
    again = await job.tick(_win(world.now()), world.now())
    assert again.startswith("0 bars")
    assert len(await book.trades()) == len(ledger) + len(st["positions"])


async def test_the_breaker_pauses_the_module_with_one_line_and_resume_clears_it(
        world, monkeypatch):
    monkeypatch.setattr(book, "FORWARD", Policy(resume_next_session=False, breaker=15.0,
                                                daily_stop=None))
    n = len(world.bars)
    await _feed(world, [300, n - 300])
    st = await book.state()
    assert st["halted"] and st["breaker_hits"] >= 1 and st["breaker_tripped_at"]
    rows = await _rows("SELECT scope, paused_by, reason FROM pauses WHERE resumed_at IS NULL")
    assert [(r["scope"], r["paused_by"]) for r in rows] == [("futures", job.BREAKER_BY)]
    assert "breaker" in rows[0]["reason"]
    pauses = [m for m in world.sent if m.title == "Futures paper breaker"]
    assert len(pauses) == 1 and pauses[0].severity is Severity.HIGH
    assert pauses[0].topic == "markets" and "Paper, not advice" in pauses[0].body
    assert "/atlas resume futures" in pauses[0].body
    breaker_exit = await _rows("SELECT count(*) AS n FROM futures_trades "
                               "WHERE exit_reason = 'breaker'")
    assert breaker_exit[0]["n"] >= 1
    # Paused: more bars, nothing new booked, no second line.
    before = len(await book.trades())
    status = await _feed(world, [n - 100])
    assert status[0].endswith("paused") and len(await book.trades()) == before
    assert len([m for m in world.sent if m.title == "Futures paper breaker"]) == 1
    assert (await book.state())["halted"]
    # Miro resumes: the halt clears and the peak restarts from here.
    assert (await pause.resume("futures", "miro")).startswith("Resumed")
    monkeypatch.setattr(book, "FORWARD", Policy(resume_next_session=False))
    await _feed(world, [n])
    st = await book.state()
    assert not st["halted"] and st["peak"] == pytest.approx(
        max(st["equity"], st["peak"]))


async def test_an_unreadable_feed_books_nothing_and_says_unknown(world):
    world.status = 503
    status = await job.tick(_win(world.now()), world.now())
    assert status.startswith("unreadable")
    assert await book.state() is None
    obs = await futures.check_futures(world.now())
    assert obs.health is Health.UNKNOWN and "last read failed" in obs.detail


# -------------------------------------------------------------- the check

async def test_the_check_reads_freshness_gaps_and_the_breaker(world, monkeypatch):
    await _feed(world, [300])
    now = world.now()
    ok = await futures.check_futures(now)
    assert ok.health is Health.OK and "bars to" in ok.detail and "paper" in ok.detail
    assert ok.evidence["gaps"] == 0 and ok.value == 300
    stale = await futures.check_futures(now + timedelta(hours=3))
    assert stale.health is Health.DEGRADED and "stale" in stale.detail
    world.holes = [250]                     # a hole inside CME hours
    await _feed(world, [320])
    gappy = await futures.check_futures(world.now())
    assert gappy.health is Health.DEGRADED and "gap" in gappy.detail
    world.holes = []
    monkeypatch.setattr(book, "FORWARD", Policy(resume_next_session=False, breaker=15.0,
                                                daily_stop=None))
    await _feed(world, [len(world.bars)])
    tripped = await futures.check_futures(world.now())
    assert tripped.health is Health.DEGRADED and "breaker tripped" in tripped.detail
    assert tripped.evidence["halted"] is True


# ------------------------------------------------------------- the reports

async def test_the_daily_summary_and_the_weekly_scoreboard_carry_real_numbers(world):
    n = len(world.bars)
    await _feed(world, [300] + list(range(337, n, 37)) + [n])
    ledger = await book.trades("WHERE status = 'closed'")
    session = ledger[0]["session_date"]
    now = world.now()
    text = await report.daily_summary(session, SYM, now)
    day_rows = [r for r in await book.trades() if r["session_date"] == session]
    assert text.startswith(f"{SYM} paper · {session:%a %-d %b}: {len(day_rows)} trade")
    assert "fired: " in text and ledger[0]["engine"] in text
    assert "Equity $" in text and "Breaker: clear" in text
    assert "Data: bars to" in text and "read " in text
    assert text.count("Paper, not advice.") == 1
    quiet = await report.daily_summary(date(2026, 9, 12), SYM, now)     # a Saturday
    assert "no trades" in quiet and "Paper, not advice." in quiet
    # Sent once, recorded once.
    w = Window(session.isoformat(), et(session, 16, 15))
    assert await job.daily(w, now) == "sent"
    assert await job.daily(w, now) == "already sent"
    digest = [m for m in world.sent if m.kind == "digest" and "paper ·" in m.body]
    assert len(digest) == 1 and digest[0].topic == "markets"
    reps = await _rows("SELECT kind FROM reports WHERE kind = $1", job.DAILY_KIND)
    assert len(reps) == 1
    # The weekly: a fresh backtest stored, then the scoreboard.
    results = await job.refresh_backtest(now)
    assert results and results["combined"]["trades"] > 0
    stored = await _rows("SELECT symbol, interval, bars, results FROM futures_backtests")
    assert stored[0]["symbol"] == SYM and stored[0]["bars"] == n
    assert json.loads(stored[0]["results"])["propfirm"]["status"] in (
        "passed", "failed", "incomplete")
    week = await report.weekly_section(now)
    assert week.startswith("*Futures paper engine*\nForward book since")
    assert f"Backtest ({SYM} 5m, 4 sessions" in week
    assert "engine            forward:" in week and "vwap_reclaim" in week
    assert "Prop-firm check on the backtest" in week and "illustrative" in week
    assert week.count("Paper, not advice.") == 1
    assert "UNKNOWN" not in week
    fri = Window("2026-09-18", et(date(2026, 9, 18), 16, 45))
    assert await job.weekly(fri, now) == "sent"
    assert await job.weekly(fri, now) == "already sent"
    weekly = [m for m in world.sent if m.title.startswith("Futures paper — week to")]
    assert len(weekly) == 1 and weekly[0].body == week or "Backtest" in weekly[0].body


async def test_the_scoreboard_before_the_first_tick_says_so(world):
    text = await report.weekly_section(datetime.now(UTC))
    assert "Not started" in text and "UNKNOWN — none stored yet" in text
    assert text.count("Paper, not advice.") == 1


async def test_a_model_note_is_shown_as_its_words_and_dropped_when_it_invents(
        world, monkeypatch):
    await _feed(world, [300, len(world.bars)])
    now = world.now()

    async def note(block: str) -> str | None:
        return "Coin-flip so far; the halves disagree." if "Forward" in block else None

    monkeypatch.setattr(report, "explain", note)
    text = await report.scoreboard(now)
    assert "A model's reading (its words, not a measurement): Coin-flip" in text


# ------------------------------------------------------------- the windows

async def test_run_due_claims_each_window_once_and_skips_a_late_one(world):
    # A 10:00 ET mark on the walk's second session, read 3 minutes later:
    # inside the tick's grace, far from any daily or weekly window.
    idx = next(i for i, b in enumerate(world.bars)
               if b.ts.astimezone(engines.TZ).strftime("%m-%d %H:%M") == "09-15 10:00")
    world.cutoff = idx + 1
    mark = world.bars[idx].ts.astimezone(engines.TZ)
    now = (mark + timedelta(minutes=3)).astimezone(UTC)
    out = await job.run_due(now)
    assert out == {"futures_tick": out["futures_tick"]}
    assert out["futures_tick"].startswith("1 bars")
    runs = await _rows("SELECT job, occurrence, status FROM schedule_runs ORDER BY job")
    assert [(r["job"], r["occurrence"], r["status"]) for r in runs] == [
        ("futures_tick", "2026-09-15T10:00", "done")]
    assert await job.run_due(now) == {}
    # Half an hour on: the 10:30 window runs; 10:15, now past its 12-minute
    # grace, is recorded skipped and never run late.
    missed.forget()
    world.cutoff = idx + 7
    later = (mark + timedelta(minutes=31)).astimezone(UTC)
    out = await job.run_due(later)
    assert out["futures_tick"].startswith("6 bars")
    runs = await _rows("SELECT occurrence, status, detail FROM schedule_runs "
                       "WHERE job = 'futures_tick' ORDER BY occurrence")
    assert [(r["occurrence"], r["status"]) for r in runs] == [
        ("2026-09-15T10:00", "done"), ("2026-09-15T10:15", "skipped"),
        ("2026-09-15T10:30", "done")]
    assert "skip-stale" in runs[1]["detail"]


async def test_the_module_registers_one_read_only_check_and_no_action(world):
    m = REGISTRY.modules["futures"]
    assert [c.name for c in m.checks()] == ["futures"]
    assert m.actions() == () and not [a for a in REGISTRY.actions if a.startswith("futures.")]
    assert pause.valid_scope("futures") == "futures"
