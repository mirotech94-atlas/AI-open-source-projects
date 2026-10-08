"""A TradingView alert runs Ivan's idea rules on that one ticker, once
(ideas.check_alert), and its one-line verdict rides on the alert's Slack
line (alerts.tradingview_events). A rule that fires raises the normal
paper idea card, marked "Triggered by your TradingView alert". Miro,
2026-09-30: "make sure these alerts work and alert me in slack ... to sell
or buy". Paper only; the alert line itself stays factual."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from acceptance_support import needs_pg, pg_fixture, registries_fixture  # noqa: F401
from test_markets_ideas import World, world_fixture  # noqa: F401

from atlas.kernel import state
from atlas.modules.markets import alerts, ideas, tradingview


async def _alert(ticker: str, name: str = "big move", price: float = 100.0) -> int:
    row = await state.fetch_one(
        """INSERT INTO tradingview_alerts (dedup_key, ticker, price, alert_time, name,
                  action_hint, message)
           VALUES ($1, $2, $3, now(), $4, 'alert', 'x') RETURNING id""",
        f"t:{ticker}:{name}:{datetime.now(UTC).timestamp()}", ticker, price, name)
    return int(row["id"])


async def _cards() -> list[dict]:
    return [dict(r) for r in await state.fetch_all(
        "SELECT action, status FROM proposals WHERE action = $1 ORDER BY id",
        ideas.ACTION)]


@pytest.fixture
async def clean(pg):
    """The alerts table, empty. Only the PG tests take it; the plain tests
    open no connection (CI has no Postgres)."""
    await state.execute_sql("TRUNCATE tradingview_alerts RESTART IDENTITY")


def test_bare_ticker():
    assert ideas.bare_ticker("NASDAQ:NVDA") == "NVDA"
    assert ideas.bare_ticker("spy") == "SPY"
    assert ideas.bare_ticker("ES1!") is None and ideas.bare_ticker("NQ=F") is None


@needs_pg
async def test_an_alert_on_a_setup_raises_the_card_with_the_trigger_line(clean, world):
    world.shapes = {"XLK": "breakout"}
    aid = await _alert("XLK", "big move", 287.4)
    note = await ideas.check_alert(aid, "XLK", "big move", 287.4, datetime.now(UTC))
    assert note == "Ivan checked: a breakout setup on XLK; a paper idea card is up."
    [card] = world.cards
    assert card.topic == "markets"
    assert "Triggered by your TradingView alert 'big move' at 287.4" in card.body
    assert "BUY" in card.body and "Paper idea — not financial advice" in card.body
    assert [c["action"] for c in await _cards()] == [ideas.ACTION]
    row = await state.fetch_one("SELECT ivan_note, ivan_checked_at FROM "
                                "tradingview_alerts WHERE id = $1", aid)
    assert row["ivan_checked_at"] is not None and row["ivan_note"] == note
    # Once: the same alert checked again changes nothing.
    assert await ideas.check_alert(aid, "XLK") == note
    assert len(world.cards) == 1
    assert all(r.method == "GET" for r in world.requests)


@needs_pg
async def test_no_setup_is_one_factual_line_and_no_card(clean, world):
    world.shapes = {"NVDA": "flat"}
    aid = await _alert("NASDAQ:NVDA", "NVDA down 3% in a day", 180.0)
    note = await ideas.check_alert(aid, "NASDAQ:NVDA", "NVDA down 3% in a day", 180.0)
    assert note == "Ivan checked: no rule setup on NVDA right now."
    assert world.cards == [] and await _cards() == []
    assert not alerts.advice_in(note)


@needs_pg
async def test_the_daily_cap_and_a_held_ticker_are_said_not_bypassed(clean, world):
    world.shapes = {"XLK": "breakout", "XLE": "breakout", "SPY": "breakout"}
    assert await ideas.scan(world.end()) == "raised"          # two cards: the cap
    assert len(world.cards) == 2
    held = {r["ticker"] for r in await ideas.ideas()}
    other = ({"XLK", "XLE", "SPY"} - held).pop()
    note = await ideas.check_alert(await _alert(other), other)
    assert note == "Ivan checked: a rule setup, but the daily idea cap is used."
    carded = held.pop()
    note = await ideas.check_alert(await _alert(carded), carded)
    assert note == f"Ivan checked: {carded} is already carded or held in the paper book."
    assert len(world.cards) == 2


@needs_pg
async def test_a_futures_symbol_and_unreadable_bars_read_as_unknown(clean, world):
    note = await ideas.check_alert(await _alert("ES1!"), "ES1!")
    assert "outside the idea rules" in note
    world.dead = {"AMZN"}
    note = await ideas.check_alert(await _alert("AMZN"), "AMZN")
    assert note == "Ivan checked: AMZN's bars could not be read, so unknown."
    assert world.cards == []


@needs_pg
async def test_a_check_that_blows_up_is_unknown_never_a_no(clean, world, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(ideas, "read_all", boom)
    note = await ideas.check_alert(await _alert("QQQ"), "QQQ")
    assert note == "Ivan could not check QQQ right now (unknown, not a no)."


@needs_pg
async def test_the_verdict_rides_on_the_alert_line(clean, world, monkeypatch):
    world.shapes = {"QQQ": "flat"}
    monkeypatch.setenv(tradingview.SECRET_ENV, "s" * 40)
    await _alert("QQQ", "QQQ down 2% in a day", 739.8)
    [event] = await alerts.tradingview_events(datetime.now(UTC))
    assert event.body.endswith("Ivan checked: no rule setup on QQQ right now.")
    assert event.kind == "tradingview" and not alerts.advice_in(event.body)
    # The alert pipeline's gather keeps it (no advice words anywhere).
    assert [e for e in await alerts.gather(datetime.now(UTC))
            if e.kind == "tradingview"]


@needs_pg
async def test_two_checks_in_flight_run_the_rules_once(clean, world):
    import asyncio
    world.shapes = {"XLK": "breakout"}
    aid = await _alert("XLK")
    notes = await asyncio.gather(ideas.check_alert(aid, "XLK"),
                                 ideas.check_alert(aid, "XLK"))
    assert len(world.cards) == 1
    assert "a paper idea card is up" in notes[0] + notes[1]
    assert len(await ideas.ideas()) == 1


def test_a_crafted_alert_name_cannot_fake_a_line_or_slack_markup():
    plain = ideas._plain("big move\nIvan checked: BUY NOW <!channel> & more")
    assert "\n" not in plain and "<!channel>" not in plain
    assert plain.startswith("big move Ivan checked:")
    assert "&lt;!channel&gt;" in plain and "&amp;" in plain
