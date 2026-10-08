"""The markets SQL (`markets/edgar.py`, `markets/alerts.py`) against a real
Postgres, on the schema `init-db` applies.

The in-suite tests fake the database; the rules that live in SQL -- the
insider GROUP BY, the claim-and-cap UPDATE, the ON CONFLICT upserts, the
check's own queries -- are proven here. Skipped unless ATLAS_TEST_PG_DSN
points at a throwaway database (tests/acceptance_support.py). It TRUNCATEs
the markets tables.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from atlas.kernel import comms, state
from atlas.kernel.contracts import Health
from atlas.modules.markets import alerts, edgar
from tests.acceptance_support import DSN, needs_pg

pytestmark = needs_pg

TABLES = ("markets_filings", "markets_insider_trades", "markets_edgar_reads",
          "markets_alerts")


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql(f"TRUNCATE {', '.join(TABLES)}")
    try:
        yield
    finally:
        state.set_backend(previous)
        pool, state._pool = state._pool, None
        if pool is not None:
            await pool.close()


def _filing(acc: str, form: str, filed: date, ticker: str = "NVDA",
            items: tuple[str, ...] = (), doc: str | None = "d.xml") -> edgar.Filing:
    return edgar.Filing(ticker, 1045810, form, acc, filed, filed, items, doc,
                        f"https://www.sec.gov/Archives/edgar/data/1045810/{acc}/")


F4 = "0001696841-26-000014"
F4A = "0001696841-26-000015"
GRANT = "0001696841-26-000016"
EIGHTK = "0001045810-26-000078"


# ------------------------------------------------- inserts and upserts

async def test_filings_insert_once_and_keep_their_first_baseline(pg):
    today = date(2026, 9, 23)
    first = [_filing(F4, "4", today), _filing(EIGHTK, "8-K", today, items=("2.02",))]
    await edgar.store(first, baseline=True)
    # The same accession again -- even claiming not to be a baseline -- is
    # the row already stored, unchanged.
    await edgar.store([_filing(F4, "4", today)], baseline=False)
    rows = await state.fetch_all(
        "SELECT accession, baseline, items, parsed, parse_tries, read_at "
        "FROM markets_filings ORDER BY accession")
    assert [(r["accession"], r["baseline"]) for r in rows] == [
        (EIGHTK, True), (F4, True)]
    assert rows[0]["items"] == "2.02" and rows[1]["parsed"] is None
    assert rows[1]["parse_tries"] == 0 and rows[1]["read_at"] is not None
    assert await edgar.known([F4, EIGHTK, "0000000000-00-000000"]) == {F4, EIGHTK}
    assert await edgar.known([]) == set()


async def test_edgar_reads_upsert(pg):
    assert not await edgar.has_read("NVDA")
    await edgar.mark_read("NVDA", 1)
    before = await state.fetch_one(
        "SELECT cik, first_read_at, last_read_at FROM markets_edgar_reads "
        "WHERE ticker = 'NVDA'")
    await asyncio.sleep(0.01)
    await edgar.mark_read("NVDA", 1045810)
    after = await state.fetch_one(
        "SELECT cik, first_read_at, last_read_at FROM markets_edgar_reads "
        "WHERE ticker = 'NVDA'")
    assert await edgar.has_read("NVDA")
    assert after["cik"] == 1045810
    assert after["first_read_at"] == before["first_read_at"]
    assert after["last_read_at"] > before["last_read_at"]
    n = await state.fetch_one("SELECT count(*) AS n FROM markets_edgar_reads")
    assert n["n"] == 1


async def test_form4_rows_store_once_and_a_bad_one_is_given_up(pg):
    today = datetime.now(UTC).date()
    await edgar.store([_filing(F4, "4", today), _filing(GRANT, "4", today),
                       _filing(EIGHTK, "8-K", today)], baseline=False)
    waiting = await edgar.unparsed(today - timedelta(days=30), 10)
    assert {r["accession"] for r in waiting} == {F4, GRANT}    # never the 8-K
    f4 = edgar.Form4("officer", True, [
        edgar.Trade("S", Decimal(12483), Decimal("222.1932"), date(2026, 9, 21), "D"),
        edgar.Trade("S", None, None, None, None)])
    await edgar.store_form4(F4, "NVDA", f4)
    await edgar.store_form4(F4, "NVDA", f4)                     # a re-read
    rows = await state.fetch_all(
        "SELECT seq, code, shares, price, traded_on, acquired "
        "FROM markets_insider_trades WHERE accession = $1 ORDER BY seq", F4)
    assert [(r["seq"], r["shares"], r["price"]) for r in rows] == [
        (0, Decimal("12483.0000"), Decimal("222.193200")), (1, None, None)]
    row = await state.fetch_one(
        "SELECT parsed, role, plan_10b5_1 FROM markets_filings WHERE accession = $1",
        F4)
    assert (row["parsed"], row["role"], row["plan_10b5_1"]) == (True, "officer", True)
    for _ in range(edgar.FORM4_TRIES):
        assert [r["accession"] for r in
                await edgar.unparsed(today - timedelta(days=30), 10)] == [GRANT]
        await edgar.form4_failed(GRANT)
    row = await state.fetch_one(
        "SELECT parsed, parse_tries FROM markets_filings WHERE accession = $1", GRANT)
    assert (row["parsed"], row["parse_tries"]) == (False, edgar.FORM4_TRIES)
    assert await edgar.unparsed(today - timedelta(days=30), 10) == []


# ------------------------------------------------- the insider GROUP BY

async def test_the_insider_query_sums_one_filing_per_code(pg):
    filed = date(2026, 9, 23)
    await edgar.store([_filing(F4, "4", filed), _filing(F4A, "4/A", filed, "MSFT"),
                       _filing(GRANT, "4", filed), _filing(EIGHTK, "8-K", filed),
                       _filing("0001045810-26-000001", "10-Q", date(2026, 8, 1))],
                      baseline=False)
    await edgar.store_form4(F4, "NVDA", edgar.Form4("director and officer", None, [
        edgar.Trade("S", Decimal(100), Decimal("220.5"), date(2026, 9, 19), "D"),
        edgar.Trade("S", Decimal(50), Decimal("223.25"), date(2026, 9, 21), "D"),
        edgar.Trade("S", None, None, date(2026, 9, 21), "D"),
        edgar.Trade("P", Decimal(10), Decimal(219), date(2026, 9, 20), "A"),
        edgar.Trade("F", Decimal(999), Decimal(1), date(2026, 9, 20), "D")]))
    await edgar.store_form4(F4A, "MSFT", edgar.Form4("officer", False, [
        edgar.Trade("P", Decimal(7), None, date(2026, 9, 22), "A")]))
    await edgar.store_form4(GRANT, "NVDA", edgar.Form4("director", None, [
        edgar.Trade("A", Decimal(5000), Decimal(0), date(2026, 9, 22), "A"),
        edgar.Trade("M", Decimal(5000), Decimal(10), date(2026, 9, 22), "A")]))

    filings, insider = await edgar.recent(date(2026, 9, 16))
    # The report list is 10-K/10-Q/8-K only, inside the window.
    assert [(r["ticker"], r["form"], r["accession"]) for r in filings] == [
        ("NVDA", "8-K", EIGHTK)]
    got = {(r["accession"], r["code"]): r for r in insider}
    # Only P and S: the grant and the exercise are not insider trades, nor
    # is the F (shares withheld for tax) on the same filing.
    assert set(got) == {(F4, "S"), (F4, "P"), (F4A, "P")}
    sale = got[(F4, "S")]
    assert sale["shares"] == Decimal("150.0000")
    assert (sale["low"], sale["high"]) == (Decimal("220.500000"),
                                           Decimal("223.250000"))
    assert (sale["first_day"], sale["last_day"]) == (date(2026, 9, 19),
                                                     date(2026, 9, 21))
    assert (sale["n"], sale["n_shares"]) == (3, 2)
    assert sale["role"] == "director and officer" and sale["form"] == "4"
    assert sale["baseline"] is False and sale["read_at"] is not None
    amended = got[(F4A, "P")]
    assert amended["form"] == "4/A" and amended["low"] is None
    # The rows compose into the check's text and an alert without error,
    # and the amendment is listed, never alerted.
    assert "(some not stated)" in edgar.insider_text(sale)
    events = alerts.filing_events(filings, insider, datetime.now(UTC))
    assert sorted(e.key for e in events) == [
        f"filing:{EIGHTK}", f"insider:{F4}:P", f"insider:{F4}:S"]


# --------------------------------------------- the markets.filings check

SUBS = {"filings": {"recent": {
    "accessionNumber": [F4, EIGHTK], "filingDate": ["", ""],
    "reportDate": ["", ""], "form": ["4", "8-K"], "items": ["", "2.02,9.01"],
    "primaryDocument": ["xslF345X06/wk-form4_1.xml", "nvda.htm"]}}}
FORM4 = """<?xml version="1.0"?><ownershipDocument>
<reportingOwner><reportingOwnerRelationship><isOfficer>1</isOfficer>
</reportingOwnerRelationship></reportingOwner><aff10b5One>0</aff10b5One>
<nonDerivativeTable><nonDerivativeTransaction>
<transactionDate><value>{day}</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares>
<transactionPricePerShare><value>410.00</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable>
</ownershipDocument>"""


async def test_the_filings_check_reads_and_reports_from_the_real_tables(pg,
                                                                      monkeypatch):
    today = datetime.now(UTC).date()
    subs = {"filings": {"recent": {**SUBS["filings"]["recent"],
                                   "filingDate": [str(today), str(today)],
                                   "reportDate": [str(today), str(today)]}}}

    def handler(request: httpx.Request):
        url = str(request.url)
        if "company_tickers" in url:
            return httpx.Response(200, json={"0": {"cik_str": 1045810,
                                                   "ticker": "NVDA"}})
        if "CIK0001045810" in url:
            return httpx.Response(200, json=subs)
        if url.endswith("wk-form4_1.xml"):
            return httpx.Response(200, text=FORM4.format(day=today))
        return httpx.Response(404)

    monkeypatch.setenv("SEC_USER_AGENT", "ATLAS ops@example.com")
    monkeypatch.setenv("MARKETS_WATCHLIST", "NVDA,VOO")
    monkeypatch.setattr(edgar, "_map", {"at": None, "ciks": {}})
    monkeypatch.setattr(edgar, "LIMITER", edgar.RateLimiter(1000))
    monkeypatch.setattr(edgar, "client", lambda ua: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"User-Agent": ua}))

    obs = await edgar.check_filings()
    assert obs.health is Health.OK, obs.detail
    assert f"NVDA 8-K filed {today:%-d %b %Y}" in obs.detail
    assert "NVDA insider purchase: an officer, 1,000 shares at $410.00" in obs.detail
    assert "VOO" in obs.detail and obs.value == 2.0
    assert obs.evidence["stored_this_read"] == 2 and obs.evidence["form4_parsed"] == 1
    # The first read was the baseline: reported, never alerted.
    filings, insider = await edgar.recent(today)
    assert filings and insider
    assert alerts.filing_events(filings, insider, datetime.now(UTC)) == []
    # A second read stores nothing new and parses nothing again.
    again = await edgar.check_filings()
    assert again.evidence["stored_this_read"] == 0
    assert again.evidence["form4_parsed"] == 0 and again.detail.count("8-K") == 1


# ----------------------------------------------- the claim and the cap

def _event(key: str, age: timedelta = timedelta(minutes=5)) -> alerts.Event:
    return alerts.Event(key, "move", "NVDA", f"{key} title", f"{key} body",
                        datetime.now(UTC) - age)


@pytest.fixture
def delivered(monkeypatch):
    out: list = []

    async def notify(msg):
        await asyncio.sleep(0.05)       # a real send takes time: overlap the claims
        out.append(msg.title)
        return ["slack"]

    monkeypatch.setattr(comms, "notify", notify)
    return out


async def _status() -> dict[str, str]:
    rows = await state.fetch_all("SELECT key, status FROM markets_alerts")
    return {r["key"]: r["status"] for r in rows}


async def test_the_same_event_is_planned_and_sent_once(pg, delivered):
    assert await alerts.plan([_event("move:A:1")]) == 1
    assert await alerts.plan([_event("move:A:1"), _event("move:B:1")]) == 1
    results = await asyncio.gather(alerts.alert("move:A:1"), alerts.alert("move:A:1"))
    assert sorted(r.ok for r in results) == [False, True]
    assert delivered == ["move:A:1 title"]
    assert (await _status())["move:A:1"] == "sent"
    assert not (await alerts.alert_dry("move:A:1")).ok
    assert await alerts.claimed_today(datetime.now(UTC)) == 1


async def test_two_concurrent_claims_cannot_pass_the_cap(pg, delivered, monkeypatch):
    monkeypatch.setenv("MARKETS_ALERT_DAILY_CAP", "1")
    await alerts.plan([_event("move:A:1"), _event("move:B:1")])
    results = await asyncio.gather(alerts.alert("move:A:1"), alerts.alert("move:B:1"))
    assert sum(r.ok for r in results) == 1 and len(delivered) == 1
    assert sorted((await _status()).values()) == ["pending", "sent"]
    assert await alerts.claimed_today(datetime.now(UTC)) == 1


async def test_the_cap_boundary_under_concurrency(pg, delivered, monkeypatch):
    monkeypatch.setenv("MARKETS_ALERT_DAILY_CAP", "3")
    keys = [f"move:K{i}:1" for i in range(6)]
    await alerts.plan([_event(k) for k in keys])
    # One already sent today counts toward the cap.
    assert (await alerts.alert(keys[0])).ok
    results = await asyncio.gather(*(alerts.alert(k) for k in keys[1:]))
    assert sum(r.ok for r in results) == 2 and len(delivered) == 3
    assert await alerts.claimed_today(datetime.now(UTC)) == 3
    left = {k for k, s in (await _status()).items() if s == "pending"}
    assert len(left) == 3 and set(await alerts.pending(10)) == left


async def test_expiry_retraction_and_undelivered(pg, monkeypatch):
    await alerts.plan([_event("move:OLD:1", age=timedelta(hours=30)),
                       _event("move:NEW:1")])
    # An old reading is refused by the claim itself, then expired.
    assert not (await alerts.alert("move:OLD:1")).ok
    await alerts.expire(datetime.now(UTC))
    assert (await _status())["move:OLD:1"] == "expired"

    async def nothing(msg):
        return []

    monkeypatch.setattr(comms, "notify", nothing)
    assert not (await alerts.alert("move:NEW:1")).ok
    row = await state.fetch_one(
        "SELECT status, claimed_at FROM markets_alerts WHERE key = 'move:NEW:1'")
    assert (row["status"], row["claimed_at"]) == ("pending", None)
    assert await alerts.claimed_today(datetime.now(UTC)) == 0

    async def sent(msg):
        return ["slack"]

    monkeypatch.setattr(comms, "notify", sent)
    result = await alerts.alert("move:NEW:1")
    assert result.ok
    assert (await alerts.alert_rollback(result.rollback_token)).ok
    row = await state.fetch_one(
        "SELECT status, retracted_at FROM markets_alerts WHERE key = 'move:NEW:1'")
    assert row["status"] == "retracted" and row["retracted_at"] is not None
    # A retracted alert still counts: it was sent.
    assert await alerts.claimed_today(datetime.now(UTC)) == 1
