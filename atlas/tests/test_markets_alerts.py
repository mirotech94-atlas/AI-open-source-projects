"""markets.alert (`markets/alerts.py`) and its tick (`kernel/market_alerts.py`):
one Slack message per event, at most the daily cap, facts with their read
time and never advice. No database, no Slack: both are faked here."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from atlas.kernel import comms, market_alerts, state
from atlas.kernel.contracts import ActionResult, FailureClass, Tier
from atlas.kernel.permissions import classify
from atlas.modules import markets
from atlas.modules.markets import alerts

NOW = datetime(2026, 9, 29, 15, 45, tzinfo=UTC)

# The Alinea tests' list, plus "hold": the words that make a fact advice.
RECOMMENDS = ("sell", "switch", "buy", "should", "recommend", "advise", "consider",
              "hold")


def _no_advice(text: str) -> None:
    words = set(re.findall(r"[a-z]+", text.lower()))
    assert not words & set(RECOMMENDS), text


def _basket(**over) -> dict:
    ev = {"moves": {"NVDA": -6.2, "MSFT": 5.0, "AAPL": 1.1, "TSLA": 7.25},
          "prices": {"NVDA": 180.12, "MSFT": 420.0, "AAPL": 230.0, "TSLA": 300.0},
          "prev": {"NVDA": 192.0, "MSFT": 400.0, "AAPL": 227.5, "TSLA": 279.7},
          "at": {s: "2026-09-29T15:42:00+00:00" for s in ("NVDA", "MSFT", "AAPL",
                                                           "TSLA")},
          "checked_at": "2026-09-29T15:43:00+00:00",
          "read_at": "2026-09-29T15:42:00+00:00"}
    ev.update(over)
    return ev


def _filing(**over) -> dict:
    row = {"ticker": "NVDA", "form": "8-K", "accession": "0001045810-26-000078",
           "filed_on": date(2026, 9, 29), "items": "2.02,9.01",
           "url": "https://www.sec.gov/Archives/edgar/data/1045810/x/n.htm",
           "baseline": False, "read_at": NOW - timedelta(minutes=20)}
    row.update(over)
    return row


def _insider(**over) -> dict:
    row = {"ticker": "MSFT", "accession": "0001234567-26-000001", "code": "P",
           "role": "director", "shares": Decimal(1000), "low": Decimal(410),
           "high": Decimal(410), "n": 1, "n_shares": 1,
           "first_day": date(2026, 9, 26), "last_day": date(2026, 9, 26),
           "plan_10b5_1": False, "filed_on": date(2026, 9, 29),
           "url": "https://www.sec.gov/Archives/edgar/data/789019/y/f4.xml",
           "baseline": False, "read_at": NOW - timedelta(minutes=5)}
    row.update(over)
    return row


ALINEA = {"statement_date": "2026-09-01",
          "benchmark": {"symbol": "VOO", "gap_points": -6.2, "days": 120,
                        "flagged": True,
                        "prices_read_at": "2026-09-29T14:05:00+00:00"}}


# ------------------------------------------------------------ the events

def test_only_a_move_of_more_than_the_threshold_alerts():
    events = alerts.move_events(_basket(), NOW, 5.0)
    assert [e.key for e in events] == ["move:NVDA:2026-09-29", "move:TSLA:2026-09-29"]
    nvda = events[0]
    assert nvda.title == "NVDA down 6.2% on the day"
    assert "$180.12 vs previous close $192.00: -6.20%" in nvda.body
    assert "price as of Tue 29 Sep 15:42 UTC" in nvda.body
    assert "more than 5% either way" in nvda.body


def test_the_threshold_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("MARKETS_ALERT_MOVE_PCT", "7")
    assert markets.move_pct() == 7.0
    assert [e.ticker for e in alerts.move_events(_basket(), NOW, markets.move_pct())] \
        == ["TSLA"]
    monkeypatch.setenv("MARKETS_ALERT_MOVE_PCT", "nonsense")
    assert markets.move_pct() == 5.0


def test_a_move_is_keyed_to_its_trading_day_not_the_clock():
    """Friday's close read on Saturday is still Friday's move: one alert."""
    friday = "2026-09-25T20:00:00+00:00"
    saturday = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    ev = _basket(at={"NVDA": friday}, moves={"NVDA": -6.2},
                 checked_at=(saturday - timedelta(minutes=10)).isoformat())
    [event] = alerts.move_events(ev, saturday, 5.0)
    assert event.key == "move:NVDA:2026-09-25"


def test_a_stale_basket_reading_alerts_nothing():
    old = _basket(checked_at=(NOW - timedelta(hours=3)).isoformat())
    assert alerts.move_events(old, NOW, 5.0) == []
    assert alerts.move_events(_basket(checked_at=None), NOW, 5.0) == []


def test_new_8ks_and_insider_trades_alert_and_baselines_do_not():
    filings = [_filing(), _filing(accession="a2", form="10-Q"),
               _filing(accession="a3", baseline=True),
               _filing(accession="a4", read_at=NOW - timedelta(days=3))]
    insider = [_insider(), _insider(accession="b2", code="S", role="officer"),
               _insider(accession="b3", baseline=True),
               _insider(accession="b4", form="4/A")]      # a restated trade
    events = alerts.filing_events(filings, insider, NOW)
    assert [e.key for e in events] == [
        "filing:0001045810-26-000078", "insider:0001234567-26-000001:P",
        "insider:b2:S"]
    assert events[0].title == ("NVDA filed an 8-K (items 2.02 (results of "
                               "operations), 9.01 (financial statements and "
                               "exhibits))")
    assert "on 29 Sep 2026" in events[0].body and "UTC" in events[0].body
    assert events[1].title == "MSFT insider purchase reported (Form 4)"
    assert ("MSFT insider purchase: a director, 1,000 shares at $410.00 on "
            "26 Sep 2026 (Form 4 filed 29 Sep 2026)") in events[1].body
    assert events[2].title == "MSFT insider sale reported (Form 4)"


def test_alinea_trailing_alerts_once_per_statement():
    event = alerts.alinea_event(ALINEA, NOW)
    assert event.key == "alinea_trails:2026-09-01"
    assert "6.2 points" in event.title and "120 days" in event.body
    assert "Alinea trades on its own; ATLAS only reads its emails." in event.body
    unflagged = {**ALINEA, "benchmark": {**ALINEA["benchmark"], "flagged": False}}
    assert alerts.alinea_event(unflagged, NOW) is None
    assert alerts.alinea_event(ALINEA, NOW + timedelta(hours=7)) is None
    assert alerts.alinea_event({}, NOW) is None


def test_no_alert_ever_carries_advice():
    events = (alerts.move_events(_basket(), NOW, 5.0)
              + alerts.filing_events([_filing()], [_insider(),
                                                   _insider(accession="z", code="S",
                                                            plan_10b5_1=True)], NOW)
              + [alerts.alinea_event(ALINEA, NOW)])
    assert {e.kind for e in events} == {"move", "filing", "insider", "alinea"}
    for e in events:
        _no_advice(e.title)
        _no_advice(e.body)
        assert "UTC" in e.body and "never trades" in e.body


def test_the_advice_filter_matches_whole_words():
    assert alerts.advice_in("Time to BUY the dip") == {"buy"}
    assert alerts.advice_in("you should hold") == {"should", "hold"}
    assert alerts.advice_in("Berkshire holdings, trailing, bought, sold") == set()


# ------------------------------------------------------------- the ledger

class Ledger:
    """markets_alerts in memory, with the SQL's own rules: the key is
    unique, the claim is one step that checks status, age and the cap."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def install(self, monkeypatch) -> Ledger:
        monkeypatch.setattr(state, "fetch_one", self.fetch_one)
        monkeypatch.setattr(state, "fetch_all", self.fetch_all)
        monkeypatch.setattr(state, "execute_sql", self.execute_sql)
        monkeypatch.setattr(state, "fetch_one_locked", self.fetch_one_locked)
        return self

    async def fetch_one_locked(self, lock, sql, *args):
        # The claim is serialised under one named lock (proven against a
        # real Postgres in test_markets_pg.py); here it is one call anyway.
        assert lock == alerts.CLAIM_LOCK
        return await self.fetch_one(sql, *args)

    def _claimed(self, since) -> int:
        return sum(1 for r in self.rows.values()
                   if r["status"] in ("sent", "sending", "retracted")
                   and r["claimed_at"] and r["claimed_at"] >= since)

    async def fetch_one(self, sql, *args):
        sql = " ".join(sql.split())
        if sql.startswith("INSERT INTO markets_alerts"):
            key, kind, ticker, title, body, at = args
            if key in self.rows:
                return None
            self.rows[key] = {"key": key, "kind": kind, "ticker": ticker,
                              "title": title, "body": body, "event_at": at,
                              "status": "pending", "claimed_at": None}
            return {"key": key}
        if sql.startswith("SELECT count(*)"):
            return {"n": self._claimed(args[0])}
        if sql.startswith("SELECT key, kind"):
            return self.rows.get(args[0])
        if sql == " ".join(alerts.CLAIM_SQL.split()):
            key, oldest, since, cap = args
            row = self.rows.get(key)
            if (row is None or row["status"] != "pending" or row["event_at"] < oldest
                    or self._claimed(since) >= cap):
                return None
            row.update(status="sending", claimed_at=datetime.now(UTC))
            return row
        if "SET status = 'retracted'" in sql:
            row = self.rows.get(args[0])
            if row is None or row["status"] != "sent":
                return None
            row["status"] = "retracted"
            return row
        raise AssertionError(sql)

    async def fetch_all(self, sql, *args):
        assert "status = 'pending'" in sql
        keys = sorted((r["event_at"], k) for k, r in self.rows.items()
                      if r["status"] == "pending")
        return [{"key": k} for _at, k in keys[:args[0]]]

    async def execute_sql(self, sql, *args):
        sql = " ".join(sql.split())
        if "SET status = 'expired' WHERE status = 'pending'" in sql:
            for r in self.rows.values():
                if r["status"] == "pending" and r["event_at"] < args[0]:
                    r["status"] = "expired"
        elif "SET status = 'pending'" in sql:
            self.rows[args[0]].update(status="pending", claimed_at=None)
        elif "SET status = 'sent'" in sql:
            self.rows[args[0]].update(status="sent", channels=args[1])
        elif "SET status = 'expired' WHERE key" in sql:
            self.rows[args[0]]["status"] = "expired"
        else:
            raise AssertionError(sql)


@pytest.fixture
def sent(monkeypatch):
    out: list = []

    async def notify(msg):
        out.append(msg)
        return ["slack"]

    monkeypatch.setattr(comms, "notify", notify)
    return out


def _events(n: int) -> list[alerts.Event]:
    at = datetime.now(UTC) - timedelta(minutes=5)
    return [alerts.Event(f"move:T{i}:2026-09-29", "move", f"T{i}", f"T{i} up 6.0% "
                         "on the day", f"T{i} body read 15:42 UTC", at)
            for i in range(n)]


async def test_one_alert_per_event(monkeypatch, sent):
    ledger = Ledger().install(monkeypatch)
    assert await alerts.plan(_events(1)) == 1
    assert await alerts.plan(_events(1)) == 0           # the same event again
    key = "move:T0:2026-09-29"
    first = await alerts.alert(key)
    assert first.ok and first.rollback_token == key
    second = await alerts.alert(key)
    assert not second.ok and len(sent) == 1
    assert sent[0].topic == "markets" and sent[0].title == "T0 up 6.0% on the day"
    dry = await alerts.alert_dry(key)
    assert not dry.ok and "already sent" in dry.detail
    assert ledger.rows[key]["channels"] == "slack"


async def test_the_daily_cap_holds(monkeypatch, sent):
    monkeypatch.setenv("MARKETS_ALERT_DAILY_CAP", "2")
    Ledger().install(monkeypatch)
    await alerts.plan(_events(3))
    results = [await alerts.alert(f"move:T{i}:2026-09-29") for i in range(3)]
    assert [r.ok for r in results] == [True, True, False] and len(sent) == 2
    dry = await alerts.alert_dry("move:T2:2026-09-29")
    assert not dry.ok and "cap of 2" in dry.detail
    assert alerts.daily_cap() == 2
    monkeypatch.delenv("MARKETS_ALERT_DAILY_CAP")
    assert alerts.daily_cap() == 10


async def test_an_undelivered_alert_stays_pending(monkeypatch):
    ledger = Ledger().install(monkeypatch)

    async def nothing(msg):
        return []

    monkeypatch.setattr(comms, "notify", nothing)
    await alerts.plan(_events(1))
    result = await alerts.alert("move:T0:2026-09-29")
    assert not result.ok and ledger.rows["move:T0:2026-09-29"]["status"] == "pending"
    # TRANSIENT, so the tick stops rather than spending the hourly budgets.
    assert result.failure_class is FailureClass.TRANSIENT


async def test_quiet_hours_queue_it_for_the_brief_and_that_counts(monkeypatch):
    ledger = Ledger().install(monkeypatch)

    async def queued(msg):
        return ["queued"]

    monkeypatch.setattr(comms, "notify", queued)
    await alerts.plan(_events(1))
    result = await alerts.alert("move:T0:2026-09-29")
    assert result.ok and "morning brief" in result.detail
    assert ledger.rows["move:T0:2026-09-29"]["status"] == "sent"


async def test_an_old_or_advising_alert_is_refused(monkeypatch, sent):
    Ledger().install(monkeypatch)
    old = alerts.Event("move:OLD:2026-09-27", "move", "OLD", "OLD up 6%", "b",
                       datetime.now(UTC) - timedelta(days=2))
    bad = alerts.Event("move:BAD:2026-09-29", "move", "BAD", "BAD up 6%",
                       "a good time to buy", datetime.now(UTC))
    await alerts.plan([old, bad])
    assert "over a day old" in (await alerts.alert_dry(old.key)).detail
    assert "advice (buy)" in (await alerts.alert_dry(bad.key)).detail
    assert not (await alerts.alert(old.key)).ok
    assert not (await alerts.alert(bad.key)).ok and sent == []


async def test_rollback_records_a_retraction(monkeypatch, sent):
    ledger = Ledger().install(monkeypatch)
    await alerts.plan(_events(1))
    result = await alerts.alert("move:T0:2026-09-29")
    undo = await alerts.alert_rollback(result.rollback_token)
    assert undo.ok and "cannot be unsent" in undo.detail
    assert ledger.rows["move:T0:2026-09-29"]["status"] == "retracted"
    assert len(sent) == 1                               # nothing more was sent
    assert not (await alerts.alert_rollback("move:T0:2026-09-29")).ok


# -------------------------------------------------------- tier and the tick

def test_the_action_is_tier_1_with_a_dry_run_and_a_rollback():
    # The alert is the module's one Tier 1 action; the other two are the
    # card-only paper ones (tests/test_markets_playlist.py / _ideas.py).
    [action] = [a for a in markets.MODULE.actions() if a.name == "markets.alert"]
    assert action.tier is Tier.AUTO
    assert action.dry_run is alerts.alert_dry and action.rollback is alerts.alert_rollback
    assert set(action.params) == {"key"} and not action.irreversible
    decision = classify(action)
    assert decision.allowed and decision.tier is Tier.AUTO
    # contracts.py refuses a Tier 1 action without either (invariant 8).
    from atlas.kernel.contracts import Action
    with pytest.raises(ValueError):
        Action(name="markets.alert", tier=Tier.AUTO, summary="x", run=alerts.alert,
               dry_run=alerts.alert_dry)


def _tick_env(monkeypatch, pending_keys):
    calls: list = []
    monkeypatch.setattr(market_alerts, "_ready", lambda: None)

    async def gather(now):
        return []

    async def plan(events):
        return 0

    async def expire(now):
        return None

    async def claimed_today(now):
        return 0

    async def pending(limit):
        return pending_keys[:limit]

    for name, fn in (("gather", gather), ("plan", plan), ("expire", expire),
                     ("claimed_today", claimed_today), ("pending", pending)):
        monkeypatch.setattr(alerts, name, fn)
    return calls


async def test_the_tick_sends_through_the_executor_only(monkeypatch):
    calls = _tick_env(monkeypatch, ["move:A:2026-09-29", "filing:x", "insider:y:S",
                                    "alinea_trails:2026-09-01", "evil:z"])
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")

    async def execute(req):
        calls.append((req.action, req.params))
        return ActionResult(True, "sent")

    monkeypatch.setattr(market_alerts, "execute", execute)
    await market_alerts.tick(NOW)
    assert calls == [("markets.alert", {"key": "move:A:2026-09-29"}),
                     ("markets.alert", {"key": "filing:x"}),
                     ("markets.alert", {"key": "insider:y:S"}),
                     ("markets.alert", {"key": "alinea_trails:2026-09-01"})]


@pytest.mark.parametrize("cls", [FailureClass.BUDGET, FailureClass.TRANSIENT,
                                 FailureClass.DISABLED])
async def test_the_tick_stops_on_a_not_now_refusal(monkeypatch, cls):
    calls = _tick_env(monkeypatch, ["move:A:1", "move:B:1"])
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")

    async def execute(req):
        calls.append(req.params["key"])
        return ActionResult(False, "not now", failure_class=cls)

    monkeypatch.setattr(market_alerts, "execute", execute)
    await market_alerts.tick(NOW)
    assert calls == ["move:A:1"]


async def test_watch_only_sends_nothing(monkeypatch):
    _tick_env(monkeypatch, ["move:A:1"])
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")

    async def never(req):
        raise AssertionError("executed under watch-only")

    monkeypatch.setattr(market_alerts, "execute", never)
    said = await market_alerts.tick(NOW)
    assert any("ATLAS_WATCH_ONLY" in line for line in said)


def test_the_tick_is_scheduled_with_a_missed_run_policy():
    from atlas.kernel import scheduler
    assert "market_alerts" in scheduler.POLICIES
    assert market_alerts.ALLOWED == ("markets.alert",)
    assert market_alerts.interval() >= timedelta(minutes=5)


