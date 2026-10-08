"""SEC EDGAR, read only: the watchlist's new filings and insider trades.

Miro, 2026-09-29: Ivan should "act, not just watch", with more market data.
This is the more-data half, and it only reads. For each watchlist ticker it
maps the ticker to its SEC CIK (SEC's own `company_tickers.json`, cached a
day), reads the company's filing index from data.sec.gov's JSON API, and
keeps the 10-K, 10-Q, 8-K and Form 4 filings of the last LOOKBACK_DAYS in
Postgres (`markets_filings`). A Form 4's own XML is read for its
non-derivative transactions (`markets_insider_trades`); only code P (an
open-market purchase) and S (an open-market sale) count as an insider
purchase or sale -- a grant (A), an option exercise (M), shares withheld for
tax (F) or a gift (G) is none of those and is never reported as one.

"New" means not seen by an earlier read: a filing is new once, when its
accession number is first stored. The first read of a ticker is a baseline
-- its filings are stored and reported, never alerted -- so turning this on
does not send a month of old 8-Ks.

SEC's fair-access policy (sec.gov/os/accessing-edgar-data): a User-Agent
that names who is asking with a contact address, and at most 10 requests a
second. The contact is `SEC_USER_AGENT` (e.g. "ATLAS ops@example.com"); with
none set, nothing is requested and the check is UNKNOWN saying so. Every
request here passes one limiter, at MAX_RPS.

What reaches the check's text (and so Ivan's prompt) is a closed
vocabulary (invariant 4): watchlist tickers, form types from FORMS, 8-K item
codes shaped `d.dd`, dates, numbers, and an insider's role from the filing's
yes/no flags. No company name, owner name, officer title or document text
is ever composed into it.

Parsing uses the standard library's ElementTree on a document that is size
capped and refused outright if it declares a DOCTYPE or an ENTITY: a Form 4
never needs either.

Watch only: nothing here can trade (money out is Tier 3, permanently
denied), and a figure SEC did not state is unknown, never guessed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time as _time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ...kernel.contracts import Health, Observation

log = logging.getLogger("atlas.markets.edgar")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"

MAX_RPS = 8.0                 # SEC allows 10; stay under it
LOOKBACK_DAYS = 30            # older filings are never stored
REPORT_DAYS = 7               # what the check lists
FORM4_PER_RUN = 20            # Form 4 XMLs read per run; the rest wait
FORM4_TRIES = 3               # a Form 4 that will not parse is given up after this
MAP_TTL = timedelta(days=1)
MAX_XML_BYTES = 1_000_000
LIST_MAX = 12                 # filings named in the detail before "and N more"

REPORTS = ("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A")
INSIDER = ("4", "4/A")
FORMS = frozenset(REPORTS + INSIDER)
# The two codes that are an insider trading in the open market.
CODES = {"P": "purchase", "S": "sale"}

NO_UA = ("SEC_USER_AGENT is not set, so SEC EDGAR was not asked: SEC requires "
         "a User-Agent naming who asks with a contact address, e.g. "
         "SEC_USER_AGENT='ATLAS ops@yourdomain.com'")
LABEL = "watch only, never a trade"

_ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_ITEM = re.compile(r"^\d\.\d{2}$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_DOC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,150}$")
_XSL_DIR = re.compile(r"^xslF345X\d\d$")

# The last check's reading, for the markets module's sync `brief()`.
_state: dict[str, Any] = {}


# ------------------------------------------------------------------ access

def user_agent() -> str | None:
    """SEC_USER_AGENT when it carries a contact address, else None."""
    ua = " ".join(os.getenv("SEC_USER_AGENT", "").split())
    if not ua or "@" not in ua or len(ua) > 200:
        return None
    return ua


class RateLimiter:
    """At most `rps` request starts a second, across every caller."""

    def __init__(self, rps: float) -> None:
        self.gap = 1.0 / rps
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = _time.monotonic()
            delay = self._last + self.gap - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = _time.monotonic()


LIMITER = RateLimiter(MAX_RPS)


def _why(exc: BaseException) -> str:
    from .._http import explain
    return explain(exc)


async def _get(client: httpx.AsyncClient, url: str) -> tuple[httpx.Response | None,
                                                             str | None]:
    """One rate-limited GET. (response, None) on HTTP 200, else (None, why)."""
    await LIMITER.wait()
    try:
        r = await client.get(url)
    except httpx.HTTPError as exc:
        return None, _why(exc)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} from {r.url.host}"
    return r, None


async def _get_json(client: httpx.AsyncClient, url: str) -> tuple[Any, str | None]:
    r, why = await _get(client, url)
    if r is None:
        return None, why
    try:
        return r.json(), None
    except ValueError:
        return None, f"{r.url.host} did not answer JSON"


def client(ua: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20, headers={"User-Agent": ua},
                             follow_redirects=False)


# ------------------------------------------------------------- ticker -> CIK

_map: dict[str, Any] = {"at": None, "ciks": {}}


def parse_ticker_map(data: Any) -> dict[str, int]:
    """SEC's `company_tickers.json` ({"0": {cik_str, ticker, title}, ...})
    as {TICKER: cik}. Raises ValueError when it is not that shape."""
    if not isinstance(data, dict) or not data:
        raise ValueError("company_tickers.json is not a non-empty object")
    out: dict[str, int] = {}
    for row in data.values():
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        try:
            cik = int(row.get("cik_str"))
        except (TypeError, ValueError):
            continue
        if _TICKER.match(ticker) and cik > 0:
            out.setdefault(ticker, cik)
    if not out:
        raise ValueError("company_tickers.json maps no ticker")
    return out


async def ticker_map(c: httpx.AsyncClient, now: datetime) -> tuple[dict[str, int] | None,
                                                                   str | None]:
    """The cached map, refreshed once it is MAP_TTL old. A refresh that
    fails keeps a cached map younger than a week, and says nothing is lost."""
    at = _map["at"]
    if at is not None and now - at < MAP_TTL and _map["ciks"]:
        return _map["ciks"], None
    data, why = await _get_json(c, TICKERS_URL)
    if data is not None:
        try:
            _map.update(at=now, ciks=parse_ticker_map(data))
            return _map["ciks"], None
        except ValueError as exc:
            why = str(exc)
    if at is not None and now - at < timedelta(days=7) and _map["ciks"]:
        return _map["ciks"], None
    return None, why


# ----------------------------------------------------------------- filings

@dataclass(frozen=True)
class Filing:
    ticker: str
    cik: int
    form: str
    accession: str
    filed: date
    report_date: date | None
    items: tuple[str, ...]
    doc: str | None          # the primary document's basename, validated
    url: str


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _doc(form: str, primary: Any) -> str | None:
    """The primary document's basename, or None when it is not a plain one.
    A Form 4's is `xslF345X06/<name>.xml` -- the XSL-rendered view; the raw
    XML is the same name without the directory."""
    text = str(primary or "")
    parts = text.split("/")
    if len(parts) == 2 and form in INSIDER and _XSL_DIR.match(parts[0]):
        text = parts[1]
    elif len(parts) != 1:
        return None
    return text if _DOC.match(text) else None


def parse_filings(data: Any, ticker: str, cik: int, since: date) -> list[Filing]:
    """The watched forms filed on or after `since`, from a submissions JSON
    (`filings.recent` is columnar: one array per field). Raises KeyError /
    ValueError / TypeError when it is not that shape."""
    recent = data["filings"]["recent"]
    cols = ("accessionNumber", "filingDate", "form", "primaryDocument")
    n = len(recent["accessionNumber"])
    if any(len(recent[k]) != n for k in cols):
        raise ValueError("submissions arrays differ in length")
    items = recent.get("items") or [""] * n
    reports = recent.get("reportDate") or [""] * n
    if len(items) != n or len(reports) != n:
        raise ValueError("submissions arrays differ in length")
    out: list[Filing] = []
    for i in range(n):
        form = str(recent["form"][i])
        acc = str(recent["accessionNumber"][i])
        filed = _date(recent["filingDate"][i])
        if form not in FORMS or not _ACCESSION.match(acc) or filed is None:
            continue
        if filed < since:
            continue
        doc = _doc(form, recent["primaryDocument"][i])
        flat = acc.replace("-", "")
        url = (ARCHIVE_URL.format(cik=cik, acc=flat, doc=doc) if doc
               else INDEX_URL.format(cik=cik, acc=flat))
        codes = tuple(x.strip() for x in str(items[i] or "").split(",")
                      if _ITEM.match(x.strip()))
        out.append(Filing(ticker, cik, form, acc, filed, _date(reports[i]),
                          codes, doc, url))
    return out


# ------------------------------------------------------------------ Form 4

@dataclass(frozen=True)
class Trade:
    code: str                 # the SEC transaction code, one letter
    shares: Decimal | None
    price: Decimal | None
    traded_on: date | None
    acquired: str | None      # "A" acquired / "D" disposed


@dataclass(frozen=True)
class Form4:
    role: str                         # from the yes/no flags only
    plan: bool | None                 # the 10b5-1 box; None when not stated
    trades: list[Trade] = field(default_factory=list)


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _flag(value: str | None) -> bool | None:
    if value is None:
        return None
    v = value.strip().lower()
    return True if v in ("1", "true") else False if v in ("0", "false") else None


def _num(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        n = Decimal(value)
    except InvalidOperation:
        return None
    return n if n.is_finite() and n >= 0 else None


def parse_form4(xml: str) -> Form4:
    """A Form 4 ownership document's roles and non-derivative transactions.
    Raises ValueError for anything that is not one."""
    if len(xml) > MAX_XML_BYTES:
        raise ValueError("Form 4 larger than the cap")
    upper = xml.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ValueError("Form 4 declares a DOCTYPE or ENTITY; refused")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"Form 4 is not XML: {exc}") from exc
    if root.tag != "ownershipDocument":
        raise ValueError(f"not an ownership document ({root.tag[:40]})")
    roles: list[str] = []
    for rel in root.findall("reportingOwner/reportingOwnerRelationship"):
        for tag, word in (("isDirector", "director"), ("isOfficer", "officer"),
                          ("isTenPercentOwner", "10% owner"), ("isOther", "other")):
            if _flag(_text(rel, tag)) and word not in roles:
                roles.append(word)
    trades = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = (_text(tx, "transactionCoding/transactionCode") or "").upper()
        if not re.fullmatch(r"[A-Z]", code):
            continue
        acquired = _text(tx, "transactionAmounts/transactionAcquiredDisposedCode/value")
        trades.append(Trade(
            code=code,
            shares=_num(_text(tx, "transactionAmounts/transactionShares/value")),
            price=_num(_text(tx, "transactionAmounts/transactionPricePerShare/value")),
            traded_on=_date(_text(tx, "transactionDate/value")),
            acquired=acquired if acquired in ("A", "D") else None))
    return Form4(" and ".join(roles) or "insider", _flag(_text(root, "aff10b5One")),
                 trades)


# ------------------------------------------------------------------- store

async def _db():
    from ...kernel import state
    return state


async def known(accessions: list[str]) -> set[str]:
    if not accessions:
        return set()
    state = await _db()
    rows = await state.fetch_all(
        "SELECT accession FROM markets_filings WHERE accession = ANY($1::text[])",
        accessions)
    return {str(r["accession"]) for r in rows}


async def has_read(ticker: str) -> bool:
    state = await _db()
    return await state.fetch_one(
        "SELECT 1 AS one FROM markets_edgar_reads WHERE ticker = $1", ticker) is not None


async def store(filings: list[Filing], *, baseline: bool) -> None:
    state = await _db()
    for f in filings:
        await state.execute_sql(
            "INSERT INTO markets_filings (accession, ticker, cik, form, filed_on, "
            "report_date, items, doc, url, baseline) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT (accession) DO NOTHING",
            f.accession, f.ticker, f.cik, f.form, f.filed, f.report_date,
            ",".join(f.items), f.doc, f.url, baseline)


async def mark_read(ticker: str, cik: int) -> None:
    state = await _db()
    await state.execute_sql(
        "INSERT INTO markets_edgar_reads (ticker, cik) VALUES ($1, $2) "
        "ON CONFLICT (ticker) DO UPDATE SET cik = $2, last_read_at = now()",
        ticker, cik)


async def unparsed(since: date, limit: int) -> list[dict]:
    state = await _db()
    rows = await state.fetch_all(
        "SELECT accession, ticker, cik, doc FROM markets_filings "
        "WHERE form = ANY($1::text[]) AND parsed IS NULL AND doc IS NOT NULL "
        "AND filed_on >= $2 ORDER BY filed_on DESC LIMIT $3",
        list(INSIDER), since, limit)
    return [dict(r) for r in rows]


async def store_form4(accession: str, ticker: str, f4: Form4) -> None:
    state = await _db()
    for seq, t in enumerate(f4.trades):
        await state.execute_sql(
            "INSERT INTO markets_insider_trades (accession, seq, ticker, code, "
            "shares, price, traded_on, acquired) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) "
            "ON CONFLICT (accession, seq) DO NOTHING",
            accession, seq, ticker, t.code, t.shares, t.price, t.traded_on, t.acquired)
    await state.execute_sql(
        "UPDATE markets_filings SET parsed = TRUE, role = $2, plan_10b5_1 = $3 "
        "WHERE accession = $1", accession, f4.role, f4.plan)


async def form4_failed(accession: str) -> None:
    state = await _db()
    await state.execute_sql(
        "UPDATE markets_filings SET parse_tries = parse_tries + 1, "
        "parsed = CASE WHEN parse_tries + 1 >= $2 THEN FALSE ELSE NULL END "
        "WHERE accession = $1", accession, FORM4_TRIES)


async def recent(since: date) -> tuple[list[dict], list[dict]]:
    """(report filings, insider purchase/sale lines) filed on or after
    `since`. An insider line is one filing's P or S transactions summed."""
    state = await _db()
    filings = await state.fetch_all(
        "SELECT ticker, form, accession, filed_on, items, url, baseline, read_at "
        "FROM markets_filings WHERE form = ANY($1::text[]) AND filed_on >= $2 "
        "ORDER BY filed_on DESC, ticker", list(REPORTS), since)
    insider = await state.fetch_all(
        "SELECT f.ticker, f.accession, f.form, f.filed_on, f.url, f.role, "
        "f.plan_10b5_1, f.baseline, f.read_at, t.code, sum(t.shares) AS shares, "
        "min(t.price) AS low, max(t.price) AS high, "
        "min(t.traded_on) AS first_day, max(t.traded_on) AS last_day, "
        "count(*) AS n, count(t.shares) AS n_shares "
        "FROM markets_insider_trades t JOIN markets_filings f USING (accession) "
        "WHERE t.code = ANY($1::text[]) AND f.filed_on >= $2 "
        "GROUP BY f.ticker, f.accession, f.form, f.filed_on, f.url, f.role, "
        "f.plan_10b5_1, f.baseline, f.read_at, t.code "
        "ORDER BY f.filed_on DESC, f.ticker",
        list(CODES), since)
    return [dict(r) for r in filings], [dict(r) for r in insider]


# ------------------------------------------------------------------- words

ITEM_NAMES = {
    "1.01": "a material agreement", "1.02": "an agreement ended",
    "1.03": "bankruptcy or receivership", "1.05": "a cybersecurity incident",
    "2.01": "an acquisition or disposal completed", "2.02": "results of operations",
    "2.03": "a new financial obligation", "2.05": "exit or disposal costs",
    "2.06": "a material impairment", "3.01": "a listing notice",
    "4.01": "an auditor change", "4.02": "earlier financials not to be relied on",
    "5.02": "a director or officer change", "5.03": "bylaws amended",
    "5.07": "a shareholder vote", "7.01": "Regulation FD disclosure",
    "8.01": "other events", "9.01": "financial statements and exhibits",
}


def items_text(items: tuple[str, ...] | str | None) -> str:
    codes = (tuple(x for x in items.split(",") if _ITEM.match(x))
             if isinstance(items, str) else tuple(items or ()))
    if not codes:
        return ""
    return "items " + ", ".join(
        f"{c} ({ITEM_NAMES[c]})" if c in ITEM_NAMES else c for c in codes)


def _day(value: Any) -> str:
    d = value.date() if isinstance(value, datetime) else value
    return f"{d:%-d %b %Y}" if isinstance(d, date) else "date not stated"


def _money(value: Any) -> str:
    return f"${Decimal(str(value)):,.2f}"


def insider_text(row: dict) -> str:
    """One filing's P or S transactions, from stated figures only."""
    noun = CODES.get(str(row.get("code")), "transaction")
    role = str(row.get("role") or "insider")
    article = "an" if role[:1] in "aeiou" else "a"
    shares = row.get("shares")
    counted = row.get("n_shares") == row.get("n")
    if shares is None:
        size = "shares not stated"
    else:
        size = f"{Decimal(str(shares)):,.0f} shares" + ("" if counted else
                                                         " (some not stated)")
    low, high = row.get("low"), row.get("high")
    price = ("price not stated" if low is None
             else f"at {_money(low)}" if low == high
             else f"at {_money(low)}–{_money(high)}")
    first, last = row.get("first_day"), row.get("last_day")
    when = (f"on {_day(first)}" if first == last
            else f"between {_day(first)} and {_day(last)}")
    plan = (", under a 10b5-1 trading plan" if row.get("plan_10b5_1") is True
            else "")
    return (f"{row['ticker']} insider {noun}: {article} {role}, {size} {price} "
            f"{when}{plan} (Form 4 filed {_day(row.get('filed_on'))})")


def filing_text(row: dict) -> str:
    what = items_text(row.get("items"))
    return (f"{row['ticker']} {row['form']} filed {_day(row.get('filed_on'))}"
            + (f" ({what})" if what else ""))


def describe(filings: list[dict], insider: list[dict], read_at: datetime,
             unmapped: list[str], unreadable: list[str], quiet: list[str]) -> str:
    """The check's text. Pure."""
    parts = []
    if filings:
        named = "; ".join(filing_text(r) for r in filings[:LIST_MAX])
        more = len(filings) - LIST_MAX
        parts.append(f"filings of the last {REPORT_DAYS} days: {named}"
                     + (f"; and {more} more" if more > 0 else ""))
    else:
        parts.append(f"no 10-K, 10-Q or 8-K filed in the last {REPORT_DAYS} days")
    if insider:
        named = "; ".join(insider_text(r) for r in insider[:LIST_MAX])
        more = len(insider) - LIST_MAX
        parts.append(f"insider purchases and sales (Form 4, codes P and S): {named}"
                     + (f"; and {more} more" if more > 0 else ""))
    else:
        parts.append(f"no insider purchase or sale reported in the last "
                     f"{REPORT_DAYS} days")
    if unreadable:
        parts.append(f"unreadable: {', '.join(unreadable)}")
    if unmapped:
        parts.append(f"not in SEC's ticker map (a fund, or not an SEC filer): "
                     f"{', '.join(unmapped)}")
    if quiet:
        parts.append(f"no 10-K/10-Q/8-K or Form 4 in {LOOKBACK_DAYS} days "
                     f"(a foreign filer files 20-F/6-K instead): {', '.join(quiet)}")
    return (f"SEC EDGAR, {'; '.join(parts)} (read {read_at:%a %d %b %H:%M} UTC; "
            f"{LABEL})")


# ------------------------------------------------------------------- check

async def _read_ticker(c: httpx.AsyncClient, sym: str, cik: int,
                       since: date) -> tuple[int, int, str | None]:
    """(filings stored this read, filings in the window, why unreadable)."""
    data, why = await _get_json(c, SUBMISSIONS_URL.format(cik=cik))
    if data is None:
        return 0, 0, why
    try:
        filings = parse_filings(data, sym, cik, since)
    except (KeyError, ValueError, TypeError, AttributeError):
        return 0, 0, "the submissions JSON is not the expected shape"
    first = not await has_read(sym)
    seen = await known([f.accession for f in filings])
    fresh = [f for f in filings if f.accession not in seen]
    await store(fresh, baseline=first)
    await mark_read(sym, cik)
    return len(fresh), len(filings), None


async def _read_form4s(c: httpx.AsyncClient, since: date) -> tuple[int, int]:
    """Read up to FORM4_PER_RUN unparsed Form 4 XMLs. (parsed, failed)."""
    done = failed = 0
    for row in await unparsed(since, FORM4_PER_RUN):
        acc = str(row["accession"])
        url = ARCHIVE_URL.format(cik=int(row["cik"]), acc=acc.replace("-", ""),
                                 doc=row["doc"])
        r, why = await _get(c, url)
        if r is None:
            log.info("Form 4 %s not read: %s", acc, why)
            await form4_failed(acc)
            failed += 1
            continue
        try:
            f4 = parse_form4(r.text)
        except ValueError as exc:
            log.info("Form 4 %s not parsed: %s", acc, exc)
            await form4_failed(acc)
            failed += 1
            continue
        await store_form4(acc, str(row["ticker"]), f4)
        done += 1
    return done, failed


def _unknown(detail: str, now: datetime) -> Observation:
    _state.update(at=now, text=None, error=detail)
    return Observation("filings", Health.UNKNOWN,
                       detail=f"{detail} (tried {now:%d %b %H:%M} UTC; {LABEL})")


async def check_filings() -> Observation:
    from . import watchlist
    now = datetime.now(UTC)
    ua = user_agent()
    if ua is None:
        return _unknown(NO_UA, now)
    today = now.date()
    since = today - timedelta(days=LOOKBACK_DAYS)
    syms = watchlist()
    unreadable: list[str] = []
    quiet: list[str] = []
    stored = 0
    try:
        async with client(ua) as c:
            ciks, why = await ticker_map(c, now)
            if ciks is None:
                return _unknown(f"SEC EDGAR could not be read (ticker map: {why})", now)
            unmapped = [s for s in syms if s not in ciks]
            mapped = [s for s in syms if s in ciks]
            for sym in mapped:
                fresh, window, why = await _read_ticker(c, sym, ciks[sym], since)
                if why:
                    log.info("EDGAR %s unreadable: %s", sym, why)
                    unreadable.append(sym)
                    continue
                stored += fresh
                if not window:
                    quiet.append(sym)
            if mapped and len(unreadable) == len(mapped):
                return _unknown("SEC EDGAR could not be read for any watched "
                                "company", now)
            parsed, failed = await _read_form4s(c, since)
        filings, insider = await recent(today - timedelta(days=REPORT_DAYS))
    except Exception as exc:                            # noqa: BLE001
        return _unknown(f"filings could not be stored or read ({type(exc).__name__})",
                        now)
    read_at = datetime.now(UTC)
    text = describe(filings, insider, read_at, unmapped, unreadable, quiet)
    _state.update(at=read_at, text=text, error=None,
                  counts=(len(filings), len(insider)))
    return Observation(
        "filings", Health.DEGRADED if unreadable else Health.OK,
        float(len(filings) + len(insider)), "filings", text,
        evidence={
            "read_at": read_at.isoformat(),
            "stored_this_read": stored,
            "form4_parsed": parsed, "form4_failed": failed,
            "filings": [{"ticker": r["ticker"], "form": r["form"],
                         "accession": r["accession"],
                         "filed": _day(r.get("filed_on")),
                         "items": r.get("items") or ""} for r in filings[:50]],
            "insider": [{"ticker": r["ticker"], "accession": r["accession"],
                         "code": r["code"], "role": r.get("role"),
                         "shares": str(r["shares"]) if r.get("shares") is not None
                         else None,
                         "filed": _day(r.get("filed_on"))} for r in insider[:50]],
            "unmapped": unmapped, "unreadable": unreadable, "quiet": quiet,
        })


def brief_line() -> str:
    """One line from the last check, with its read time; nothing before one."""
    at = _state.get("at")
    if at is None:
        return ""
    if _state.get("error"):
        return f"SEC filings: unknown ({_state['error']}, {at:%H:%M} UTC)"
    n_filings, n_insider = _state.get("counts", (0, 0))
    return (f"SEC filings: {n_filings} report filing(s) and {n_insider} insider "
            f"purchase/sale filing(s) in the last {REPORT_DAYS} days "
            f"(read {at:%H:%M} UTC; {LABEL})")
