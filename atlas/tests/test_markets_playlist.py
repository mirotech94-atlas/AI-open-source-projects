"""The Claude playlist (paper): the Alinea "Playlist Pro" loop, on paper.

A model proposes weekly target weights from closes ATLAS read itself; the
answer is checked strictly and never repaired; a change becomes a Tier 2
card; approval runs `markets.paper_rebalance` through the executor, which
moves PAPER holdings at fresh closes; rollback restores the previous
weights; a declined card changes nothing; an unreadable price is UNKNOWN,
never 0; no model answering proposes nothing and says so. The public chart
feed and the model are fakes; nothing here can reach a broker -- the one
action's only network traffic is GETs to the chart feed.
"""
from __future__ import annotations

import inspect
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from acceptance_support import needs_pg, pg_fixture, registries_fixture  # noqa: F401

from atlas.kernel import approvals, executor, llm, state
from atlas.kernel.contracts import ActionResult, Tier
from atlas.kernel.llm import Completion
from atlas.kernel.permissions import classify, denial_reason
from atlas.kernel.registry import REGISTRY
from atlas.modules.markets import playlist, readback
from atlas.modules.markets.playlist import Invalid, parse_answer

TICKERS = playlist.eligible()
EQUAL = {t: round(100 / len(TICKERS), 2) for t in TICKERS}
BASE = {"AMZN": 220.0, "NVDA": 180.0, "MSFT": 510.0, "LLY": 760.0, "GOOGL": 245.0,
        "AAPL": 255.0, "V": 345.0, "ISRG": 450.0, "DE": 460.0, "NEE": 75.0,
        "TSLA": 440.0, "ASML": 960.0, "JNJ": 180.0, "JPM": 310.0, "VOO": 610.0}
TABLE = "\n".join(
    ["Closes read 2026-10-04 19:00 UTC from the public chart feed (price only).",
     ("ticker | last close (date) | 4w return % | 12w return % | "
      "20d volatility % (annualised) | current weight %")]
    + [f"{t} | {BASE[t]:.2f} (2026-10-02) | +{i + 0.5:.1f} | -{i + 1.2:.1f} | "
       f"{20 + i:.1f} | {EQUAL[t]:.1f}" for i, t in enumerate(TICKERS)])
TARGET = {"AMZN": 15.0, "NVDA": 15.0, "MSFT": 10.0, "LLY": 10.0, "GOOGL": 10.0,
          "AAPL": 10.0, "V": 10.0, "ISRG": 10.0, "DE": 5.0, "NEE": 5.0}


def answer(weights: dict | None = None, reasons: dict | None = None, **extra) -> str:
    weights = TARGET if weights is None else weights
    if reasons is None:
        reasons = {t: f"4w {row(t)[2]}%, 20d vol {row(t)[4]}%" for t in TICKERS}
    return json.dumps({"weights": weights, "reasons": reasons, **extra})


def row(t: str, table: str = TABLE) -> list[str]:
    line = next(li for li in table.splitlines() if li.startswith(f"{t} |"))
    return [c.strip() for c in line.split("|")]


# ------------------------------------------------------------ validation

def test_a_valid_answer_passes_unchanged():
    weights, reasons = parse_answer(answer(), TICKERS, EQUAL, TABLE)
    assert weights == TARGET
    assert set(reasons) == set(TICKERS)


@pytest.mark.parametrize(("text", "why"), [
    (answer({**TARGET, "NEE": 4.0}), "sum to 99.0%"),
    (answer({**TARGET, "NEE": 6.0}), "sum to 101.0%"),
    (answer({**TARGET, "NEE": 0.0, "DOGE": 5.0}), "'DOGE' is not a playlist ticker"),
    (answer({**{k: v for k, v in TARGET.items() if k != "NEE"}, "nee": 5.0}),
     "'nee' is not a playlist ticker"),
    (answer({**TARGET, "VOO": 5.0, "NEE": 0.0}), "'VOO' is not a playlist ticker"),
    (answer({**TARGET, "AMZN": 20.0, "NVDA": 10.0}), "over the 15% cap"),
    (answer({**TARGET, "NEE": -5.0, "DE": 15.0}), "negative"),
    (answer({**TARGET, "NEE": True}), "not a number"),
    (answer({**TARGET, "NEE": "5"}), "not a number"),
    (answer().replace('"NEE": 5.0', '"NEE": NaN'), "NaN"),
    (answer().replace('"NEE": 5.0', '"NEE": Infinity'), "Infinity"),
    ("Here are my weights: " + answer(), "not one bare JSON object"),
    (answer() + "\nHope this helps!", "not one bare JSON object"),
    ("```json\n" + answer() + "\n```", "not one bare JSON object"),
    (answer() + " {}", "not valid JSON"),
    (answer(note="trust me"), "exactly 'weights' and 'reasons'"),
    (answer().replace('"AMZN": 15.0', '"AMZN": 15.0, "AMZN": 15.0'), "repeats a key"),
    (answer({t: 7.7 for t in TICKERS[:13]} | {TICKERS[13]: 0.0}), "sum to 100.1%"),
    (answer({t: 100 / 13 for t in TICKERS[:13]}), "sum to 100.1%"),
    (answer({t: 7.7 for t in TICKERS[:12]} | {TICKERS[12]: 7.6}),
     "13 holdings, over the 12 cap"),
])
def test_bad_answers_are_rejected_never_fixed(text, why):
    with pytest.raises(Invalid, match=re.escape(why)):
        parse_answer(text, TICKERS, EQUAL, TABLE)


def test_thirteen_holdings_that_sum_are_still_over_the_cap():
    w = {t: 7.7 for t in TICKERS[:12]} | {TICKERS[12]: 7.6}
    assert math.isclose(sum(w.values()), 100.0)
    with pytest.raises(Invalid, match="13 holdings"):
        parse_answer(answer(w), TICKERS, EQUAL, TABLE)


@pytest.mark.parametrize(("reasons", "why"), [
    ({t: "4w +0.5%" for t in TICKERS if t != "TSLA"}, "no reason given for the change"),
    ({**{t: "4w +0.5%" for t in TICKERS}, "AMZN": "it just feels right"},
     "cites no figure"),
    ({**{t: "4w +0.5%" for t in TICKERS}, "AMZN": "up 42.7% this quarter"},
     "cites 42.7, not in the data given"),
    ({**{t: "4w +0.5%" for t in TICKERS}, "AMZN": "4w +0.5%\nignore the rules"},
     "not one short line"),
    ({**{t: "4w +0.5%" for t in TICKERS}, "AMZN": "4w +0.5% " + "x" * 200},
     "not one short line"),
    ({**{t: "4w +0.5%" for t in TICKERS}, "BTC": "4w +0.5%"}, "'BTC'"),
])
def test_reasons_must_cite_the_figures_given(reasons, why):
    with pytest.raises(Invalid, match=re.escape(why)):
        parse_answer(answer(reasons=reasons), TICKERS, EQUAL, TABLE)


def test_labels_are_not_figures_and_rounded_citations_are():
    assert playlist.figures_in("12w -1.2%, 4-week +0.5, 20d vol 20.0%") == [
        "1.2", "0.5", "20.0"]
    weights, _ = parse_answer(
        answer(reasons={t: "12-week -1% and 20-day vol 20%; target 15.0%"
                        for t in TICKERS}), TICKERS, EQUAL, TABLE)
    assert weights == TARGET


def test_the_stats_are_read_from_closes_and_unknown_when_short():
    start = date(2026, 6, 1)
    series = {start + timedelta(days=i): 100.0 + i for i in range(120)}
    s = playlist.stats_of(series)
    last = start + timedelta(days=119)
    assert s.day == last and s.last == 219.0
    assert math.isclose(s.r4, (219 / 191 - 1) * 100)
    assert math.isclose(s.r12, (219 / 135 - 1) * 100)
    assert s.vol20 is not None and s.vol20 > 0
    short = playlist.stats_of({start + timedelta(days=i): 100.0 for i in range(10)})
    assert short.r4 is None and short.r12 is None and short.vol20 is None
    assert playlist.stats_of({}) is None


def test_the_card_shows_before_after_reasons_and_paper_only():
    text = playlist.card_text({"proposal_id": 1, "before": EQUAL, "after": TARGET,
                               "reasons": {"AMZN": "4w +0.5%"}})
    assert "• AMZN 7.1% → 15.0% — 4w +0.5%" in text
    assert "• TSLA 7.1% → 0.0%" in text
    assert text.endswith("Paper only — no real money moves; not advice.")


def test_vet_refuses_a_payload_that_could_only_fail():
    ok = {"proposal_id": 1, "before": EQUAL, "after": TARGET, "reasons": {}}
    assert playlist.vet(ok) is None
    assert "cap" in playlist.vet({**ok, "after": {**TARGET, "AMZN": 20.0,
                                                  "NVDA": 10.0}})
    assert "not a playlist ticker" in playlist.vet({**ok, "after": {"BTC": 100.0}})
    assert "not rounded" in playlist.vet(
        {**ok, "after": {**TARGET, "DE": 5.04, "NEE": 4.96}})


# ------------------------------------------------------- nothing can trade

def test_the_action_is_tier_2_card_only_and_never_denied_or_offered():
    act = playlist.action()
    assert act.tier is Tier.ASK and act.card_only and act.vet and act.card_detail
    assert act.rollback is not None and act.dry_run is not None
    assert denial_reason(act.name) is None and classify(act).allowed
    assert classify(act).tier is Tier.ASK
    REGISTRY.actions[act.name] = act
    try:
        assert act.name not in [a["name"] for a in REGISTRY.catalog_for_prompt()]
    finally:
        REGISTRY.actions.pop(act.name, None)


def test_the_action_has_no_network_write_in_its_source():
    src = inspect.getsource(playlist) + inspect.getsource(readback)
    for verb in (".post(", ".put(", ".patch(", ".delete(", "method=",
                 "send(", "httpx.Client", "broker", "alpaca", "drivewealth"):
        assert verb not in src.lower(), verb
    # Its one outside read is the markets module's chart reader.
    assert "closes(" in src and "AsyncClient" not in src


# ------------------------------------------------------- the fake world

class Feed:
    """The public chart feed: deterministic daily closes; anything but a
    GET to it fails the test."""

    def __init__(self) -> None:
        self.dead: set[str] = set()
        self.bump: dict[str, float] = {}
        self.requests: list[httpx.Request] = []

    def price(self, sym: str, day: date) -> float:
        n = (day - date(2026, 1, 1)).days
        drift = (sum(map(ord, sym)) % 7 - 3) / 2000
        return round(BASE[sym] * (1 + drift * n) * (1 + 0.01 * math.sin(n))
                     * self.bump.get(sym, 1.0), 4)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.method == "GET", f"a non-GET left ATLAS: {request.method}"
        assert request.url.host == "query1.finance.yahoo.com", request.url
        sym = request.url.path.rsplit("/", 1)[-1]
        if sym in self.dead:
            return httpx.Response(404, json={"chart": {"result": None}})
        q = parse_qs(urlparse(str(request.url)).query)
        d0 = datetime.fromtimestamp(int(q["period1"][0]), UTC).date()
        d1 = datetime.fromtimestamp(int(q["period2"][0]), UTC).date()
        days = [d0 + timedelta(days=i) for i in range((d1 - d0).days)]
        days = [d for d in days if d.weekday() < 5]
        stamps = [int(datetime(d.year, d.month, d.day, 20, tzinfo=UTC).timestamp())
                  for d in days]
        return httpx.Response(200, json={"chart": {"result": [{
            "meta": {"exchangeTimezoneName": "America/New_York"},
            "timestamp": stamps,
            "indicators": {"quote": [{"close": [self.price(sym, d) for d in days]}],
                           "adjclose": [{"adjclose": [self.price(sym, d)
                                                      for d in days]}]}}]}})

    def last(self, sym: str) -> float:
        d = datetime.now(UTC).date()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return self.price(sym, d)


class Model:
    """The model: answers from the table it was shown, or fails."""

    def __init__(self) -> None:
        self.weights: dict | None = dict(TARGET)
        self.fail = False
        self.text: str | None = None
        self.prompts: list[str] = []
        self.provider = "anthropic"

    async def think(self, need, system, messages, tools=None, max_tokens=2048, **kw):
        assert need is llm.Need.REASON
        prompt = messages[0].content
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("all providers failed for reason")
        if self.text is not None:
            return Completion(self.text, self.provider, "claude-sonnet-4-5")
        reasons = {t: f"4w {row(t, prompt)[2]}%, 20d vol {row(t, prompt)[4]}%"
                   for t in TICKERS}
        return Completion(json.dumps({"weights": self.weights, "reasons": reasons}),
                          self.provider, "claude-sonnet-4-5")


@pytest.fixture(name="world")
async def world_fixture(pg, monkeypatch):
    feed, model, cards = Feed(), Model(), []
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(feed.handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(llm, "think", model.think)

    async def notify(msg):
        cards.append(msg)
        return ["test"]

    monkeypatch.setattr(approvals, "notify", notify)
    monkeypatch.setenv("APPROVALS_SLACK", "on")      # the Slack card is opt-in now
    await state.execute_sql("TRUNCATE markets_playlist_proposals, "
                            "markets_playlist_holdings, markets_paper RESTART IDENTITY")
    REGISTRY.load("atlas.modules.markets", "markets")
    return SimpleNamespace(feed=feed, model=model, cards=cards)


WEEK = date(2026, 10, 4)


async def _count(table: str) -> int:
    row = await state.fetch_one(f"SELECT count(*) AS n FROM {table}")
    return int(row["n"])


async def _proposal(week: date = WEEK) -> dict:
    return dict(await state.fetch_one(
        "SELECT * FROM markets_playlist_proposals WHERE week = $1", week))


async def _card() -> dict:
    return dict(await state.fetch_one("SELECT * FROM proposals ORDER BY id DESC LIMIT 1"))


@needs_pg
async def test_the_first_run_starts_at_100_equal_and_raises_a_tier_2_card(world):
    assert await playlist.propose_week(WEEK) == "carded"
    start = await playlist.first()
    assert start.kind == "start" and start.value == 100.0
    assert set(start.weights) == set(TICKERS) and "VOO" in start.prices
    for t in TICKERS:
        assert math.isclose(start.shares[t] * start.prices[t], 100 / 14)
    p = await _proposal()
    assert p["status"] == "carded" and p["model"] == "anthropic:claude-sonnet-4-5"
    card = await _card()
    assert card["id"] == p["card_id"] and card["action"] == playlist.ACTION
    assert card["tier"] == 2 and card["status"] == "pending"
    assert card["dedup_key"] == "markets.playlist:2026-10-04"
    (msg,) = world.cards
    assert "7.1% → 15.0%" in msg.body and "`markets.paper_rebalance` · Tier 2" in msg.body
    assert "Paper only — no real money moves; not advice." in msg.body
    assert "proposed by anthropic:claude-sonnet-4-5" in msg.body
    # It lands in #trades (SLACK_CHANNELS markets:<id>), not #decisions.
    assert msg.topic == "markets"
    assert approvals.topic_for_action(playlist.ACTION, {}) == "markets"
    # The model saw only the figures ATLAS read, wrapped as outside data.
    (prompt,) = world.model.prompts
    assert "<untrusted_data" in prompt and "20d volatility" in prompt
    # Once a week: a second run finds the week and does nothing.
    assert await playlist.propose_week(WEEK) == "exists"
    assert await _count("proposals") == 1 and len(world.model.prompts) == 1


@needs_pg
async def test_approval_runs_through_the_executor_at_fresh_closes(world):
    await playlist.propose_week(WEEK)
    start = await playlist.current()
    world.feed.bump = {"NVDA": 1.10, "AMZN": 0.95}          # prices moved since the card
    verdict = await approvals.decide((await _card())["id"], "approve", "slack:UOWNER")
    assert verdict.ok and verdict.outcome == "executed", verdict.detail
    now = await playlist.current()
    assert now.kind == "rebalance" and now.proposal_id == (await _proposal())["id"]
    value = sum(n * world.feed.last(t) for t, n in start.shares.items())
    assert math.isclose(now.value, value)
    for t, w in TARGET.items():
        assert math.isclose(now.shares[t], value * w / 100 / world.feed.last(t))
        assert math.isclose(now.prices[t], world.feed.last(t))
    assert set(now.shares) == set(TARGET)                # sold to zero: gone
    assert (await _proposal())["status"] == "applied"
    assert (await _card())["status"] == "executed"
    assert all(r.method == "GET" for r in world.feed.requests)
    log = await state.fetch_one(
        "SELECT count(*) AS n FROM actions_log WHERE event = 'execute.finish' "
        "AND subject = $1", playlist.ACTION)
    assert log["n"] == 1
    # The read-back sees exactly the approved weights.
    result = ActionResult(True, evidence={"snapshot_id": now.id})
    v = await readback.verify_rebalance({"after": TARGET}, result)
    assert v.ok, v.reason
    bad = await readback.verify_rebalance({"after": {**TARGET, "AMZN": 10.0,
                                                     "TSLA": 5.0}}, result)
    assert bad.state == "contradicted" and "AMZN" in bad.reason


@needs_pg
async def test_rollback_restores_the_previous_weights_at_fresh_closes(world):
    await playlist.propose_week(WEEK)
    start = await playlist.current()
    await approvals.decide((await _card())["id"], "approve", "slack:UOWNER")
    applied = await playlist.current()
    world.feed.bump = {"LLY": 1.2}
    result = await executor.rollback(playlist.ACTION, f"{start.id}:{applied.id}")
    assert result.ok, result.detail
    back = await playlist.current()
    assert back.kind == "rollback" and back.weights == start.weights
    value = sum(n * world.feed.last(t) for t, n in applied.shares.items())
    assert math.isclose(back.value, value)               # nothing created or lost
    for t in TICKERS:
        assert math.isclose(back.shares[t] * world.feed.last(t), value / 14)
    assert (await _proposal())["status"] == "rolled_back"
    # Its read-back now says the approved state is gone, and a second
    # rollback of the same token refuses.
    v = await readback.verify_rebalance(
        {"after": TARGET}, ActionResult(True, evidence={"snapshot_id":
                                                                 applied.id}))
    assert v.state == "contradicted" and "superseded" in v.reason
    again = await executor.rollback(playlist.ACTION, f"{start.id}:{applied.id}")
    assert not again.ok and await _count("markets_playlist_holdings") == 3


@needs_pg
async def test_a_declined_card_changes_nothing(world):
    await playlist.propose_week(WEEK)
    before = await playlist.current()
    verdict = await approvals.decide((await _card())["id"], "decline", "slack:UOWNER")
    assert verdict.ok and verdict.outcome == "declined"
    assert (await playlist.current()) == before
    assert await _count("markets_playlist_holdings") == 1
    text = await playlist.playlist_section(datetime(2026, 10, 5, 12, tzinfo=UTC))
    assert "This week: declined — nothing changed." in text


@needs_pg
async def test_no_card_when_the_proposal_matches_the_current_weights(world):
    await playlist.propose_week(WEEK)
    await approvals.decide((await _card())["id"], "approve", "slack:UOWNER")
    world.model.weights = {**TARGET, "AMZN": 14.6, "NVDA": 15.0, "MSFT": 10.4}
    assert await playlist.propose_week(WEEK + timedelta(days=7)) == "unchanged"
    assert await _count("proposals") == 1 and len(world.cards) == 1
    assert (await playlist.current()).weights == TARGET
    text = await playlist.playlist_section(datetime(2026, 10, 12, 12, tzinfo=UTC))
    assert "This week: no change proposed." in text


@needs_pg
async def test_model_unavailable_proposes_nothing_and_says_so(world):
    world.model.fail = True
    assert await playlist.propose_week(WEEK) == "no_model"
    p = await _proposal()
    assert p["weights"] is None and p["model"] is None and "no model answered" in p["why"]
    assert await _count("proposals") == 0 and not world.cards
    assert (await playlist.current()).kind == "start"     # still the equal split
    text = await playlist.playlist_section(datetime(2026, 10, 5, 12, tzinfo=UTC))
    assert "This week: no model answered, so nothing was proposed." in text


@needs_pg
async def test_an_invalid_answer_is_recorded_unknown_and_raises_nothing(world):
    world.model.text = "I'd go heavy on NVDA: " + answer()
    assert await playlist.propose_week(WEEK) == "invalid"
    p = await _proposal()
    assert p["weights"] is None and "not one bare JSON object" in p["why"]
    assert await _count("proposals") == 0
    text = await playlist.playlist_section(datetime(2026, 10, 5, 12, tzinfo=UTC))
    assert "failed the checks" in text and "UNKNOWN" in text


@needs_pg
async def test_a_non_claude_answer_says_so_on_the_card(world):
    world.model.provider = "groq"
    await playlist.propose_week(WEEK)
    assert "not Claude this week" in world.cards[0].body


@needs_pg
async def test_an_unreadable_price_is_unknown_never_zero(world):
    world.feed.dead = {"TSLA"}
    assert await playlist.propose_week(WEEK) == "no_prices"   # cannot even start
    assert await playlist.current() is None and not world.model.prompts
    world.feed.dead = set()
    assert await playlist.propose_week(WEEK + timedelta(days=7)) == "carded"
    # Approved while a held price can't be read: nothing moves.
    world.feed.dead = {"JPM"}
    verdict = await approvals.decide((await _card())["id"], "approve", "slack:UOWNER")
    assert not verdict.ok and "JPM" in verdict.detail and "never 0" in verdict.detail
    assert (await playlist.current()).kind == "start"
    text = await playlist.playlist_section(datetime(2026, 10, 12, 12, tzinfo=UTC))
    assert "Claude playlist unknown" in text and "$0.00" not in text
    assert "Unreadable: JPM" in text


# ------------------------------------------------------------- the report

@needs_pg
async def test_the_weekly_section_with_realistic_numbers(world, monkeypatch):
    at = datetime(2026, 10, 4, 19, 2, tzinfo=UTC)
    start_prices = {t: BASE[t] for t in BASE}
    start = playlist.Latest(start_prices, {t: date(2026, 10, 2) for t in BASE}, at, [])
    equal = {t: 100 / 14 for t in TICKERS}
    await playlist._insert("start", None, equal,
                           playlist.buy(100.0, equal, start_prices), start, 100.0)
    for t, base in BASE.items():                         # the control, older
        await state.execute_sql(
            "INSERT INTO markets_paper (ticker, shares, start_price) VALUES ($1,$2,$3)",
            t, (100 / 15) / (base * 0.98), base * 0.98)
    pid = await playlist._record(
        WEEK, "applied", model="anthropic:claude-sonnet-4-5", weights=TARGET,
        before={t: 7.14 for t in TICKERS},
        reasons={"NVDA": "12w -2.2%, vol 21.0%", "TSLA": "20d vol 30.0%"})
    moved = {t: BASE[t] * (1.08 if t in ("NVDA", "AMZN") else 1.01) for t in BASE}
    moved["VOO"] = BASE["VOO"] * 1.015
    await playlist._insert("rebalance", pid, TARGET,
                           playlist.buy(100.9, TARGET, start_prices), start, 100.9)
    now = playlist.Latest(moved, {t: date(2026, 10, 9) for t in BASE},
                          datetime(2026, 10, 11, 19, 5, tzinfo=UTC), [])

    async def fixed(syms):
        return now

    monkeypatch.setattr(playlist, "latest", fixed)
    text = await playlist.playlist_section(datetime(2026, 10, 5, 13, tzinfo=UTC))
    mine = sum(n * moved[t] for t, n in
               playlist.buy(100.9, TARGET, start_prices).items())
    fixed = 100 * sum(moved[t] / BASE[t] for t in BASE) / len(BASE)
    print(text)
    assert text.splitlines()[0] == "*Claude playlist (paper)*"
    assert (f"Since 4 Oct, $100 each, price only: Claude playlist ${mine:,.2f} "
            f"(+{mine - 100:.1f}%) · equal split ${fixed:,.2f} "
            f"(+{fixed - 100:.1f}%) · VOO $101.50 (+1.5%) "
            f"(closes of Fri 9 Oct, read Sun 19:05 UTC).") in text
    assert "This week: approved: " in text and "AMZN 7.1→15.0%" in text
    assert "Why: NVDA: 12w -2.2%, vol 21.0%; TSLA: 20d vol 30.0%" in text
    assert text.count("not advice") == 1


@needs_pg
async def test_the_section_before_any_start_and_when_unreadable(world, monkeypatch):
    text = await playlist.playlist_section(datetime(2026, 10, 1, tzinfo=UTC))
    assert "Not started yet" in text and "Paper, not advice." in text

    async def broken():
        raise ConnectionError("db down")

    monkeypatch.setattr(playlist, "first", broken)
    text = await playlist.playlist_section(datetime(2026, 10, 1, tzinfo=UTC))
    assert "UNKNOWN — not zero" in text


async def test_the_weekly_report_carries_the_section(monkeypatch):
    """One call-site in kernel/weekly.py, via alinea.playlist_section."""
    from atlas.kernel import weekly
    src = inspect.getsource(weekly)
    assert src.count("alinea.playlist_section(now)") == 1

    async def section(now=None):
        return "*Claude playlist (paper)*\nstub"

    monkeypatch.setattr(playlist, "playlist_section", section)
    from atlas.modules.markets import alinea
    assert await alinea.playlist_section() == "*Claude playlist (paper)*\nstub"


# ------------------------------------------------------------ the schedule

def test_it_is_weekly_on_sunday_afternoon_new_york_run_once():
    from atlas.kernel import scheduler
    from atlas.kernel.missed import Missed
    p = scheduler.POLICIES["playlist"]
    assert p.missed is Missed.RUN_ONCE and p.recorded and p.why
    now = datetime(2026, 10, 12, 10, tzinfo=playlist.TZ)     # a Monday
    ws = p.windows(now, timedelta(days=21))
    assert [w.key for w in ws] == ["2026-09-20", "2026-09-27", "2026-10-04",
                                   "2026-10-11"]
    assert ws[-1].scheduled_for == datetime(2026, 10, 11, 15, tzinfo=playlist.TZ)
    assert "playlist" in scheduler._DETACHED


async def test_the_job_proposes_once_for_the_window_in_new_york(monkeypatch):
    from atlas.kernel import missed, occurrence, scheduler
    from atlas.kernel.missed import Window
    seen, claimed, done = [], set(), []
    # 01:00 UTC Monday is still Sunday 21:00 in New York.
    monkeypatch.setattr(scheduler, "_now",
                        lambda: datetime(2026, 10, 5, 1, tzinfo=UTC))

    async def due(policy, now):
        seen.append(now)
        return Window("2026-10-04", datetime(2026, 10, 4, 15, tzinfo=playlist.TZ))

    async def claim_once(job, key, at=None):
        if (job, key) in claimed:
            return False
        claimed.add((job, key))
        return True

    async def mark(job, key):
        done.append((job, key))

    calls = []

    async def propose(week):
        calls.append(week)
        return "carded"

    monkeypatch.setattr(missed, "due", due)
    monkeypatch.setattr(occurrence, "claim_once", claim_once)
    monkeypatch.setattr(occurrence, "done", mark)
    monkeypatch.setattr(playlist, "propose_week", propose)
    await scheduler._maybe_playlist()
    await scheduler._maybe_playlist()
    assert calls == [WEEK] and done == [("playlist", "2026-10-04")]
    assert seen[0].tzinfo == playlist.TZ and seen[0].weekday() == 6


def test_playlist_cards_route_to_the_trades_channel(monkeypatch):
    """SLACK_CHANNELS maps `markets:` to #trades; the card's topic is the
    action's namespace, so it lands there and never falls back to #decisions."""
    from atlas.channels.slack import SlackChannel
    monkeypatch.setenv("SLACK_CHANNEL", "C_DECISIONS")
    monkeypatch.setenv("SLACK_CHANNELS", "cloudco:C_CLOUDCO,markets:C_TRADES")
    act = playlist.action()
    assert act.topic_param is None and act.topic_for is None
    topic = approvals._topic(act, act.name, {"proposal_id": 1})
    assert topic == "markets"
    assert SlackChannel().channel_for(topic) == "C_TRADES"
