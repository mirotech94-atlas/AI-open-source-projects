"""Alinea: what Miro's Alinea Invest emails say, read back -- watch only.

Alinea trades on its own, inside its app, with his money; ATLAS only reads
its emails (`mail/alinea.py` stores them in `alinea_events`). Nothing here
can trade, move money or advise: there is no action, and money out is
Tier 3, permanently denied.

Every figure is the one an email stated, with that email's date; a figure
no email stated is "unknown", never 0 (invariant 6). Totals are per
currency, never added across. The text composed here is numbers, currency
codes, validated tickers and dates only -- no subject line, no body text,
no mailbox address -- because the markets agent that reads it runs on a
cloud model.

The check is UNKNOWN, never OK, when no Alinea email has been seen, when no
statement has been, and when the last statement is more than STALE_DAYS
old (invariant 7): a balance nobody has re-stated in a month is not a
reading of today.

**The benchmark** answers "does Alinea make him money" with a measurement,
never advice: the same stated deposits and withdrawals, on their email
dates, put into VOO (an S&P 500 fund) instead, valued on the latest
statement's date and set beside the balance that statement states. Each
cash flow buys at VOO's close on the first trading day on or after its
email date (when the cash could first have been invested); the benchmark
is valued at the last close on or before the statement date -- the day the
balance is stated for, not today. The closes are dividend-adjusted (VOO's
dividends reinvested), because Alinea's balance includes its dividends.
Prices come from the markets module's public chart feed. If any statement,
trade or dividend is dated before the earliest stated deposit, a deposit
was not seen, so its money is in the balance but not in the benchmark:
UNKNOWN, not "ahead". A flow with no stated amount,
a currency other than USD, or a close that can't be read makes the whole
benchmark UNKNOWN -- nothing is interpolated or assumed. Both returns are
on the same net deposits, so the flows weigh them the same way. Trailing
by more than FLAG_POINTS over at least JUDGE_DAYS of history is DEGRADED
(a flag for him to look at, never FAILING); under JUDGE_DAYS it is "too
early to judge". The wording states the gap and nothing else.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from ...kernel.contracts import Health, Observation

LABEL = "Alinea trades on its own; ATLAS only reads its emails."
NEVER_SEEN = "no Alinea email seen yet — sign up and turn on Automated Investing"
STALE_DAYS = 35
RECENT_DAYS = 7
BENCHMARK = "VOO"
BENCHMARK_NAME = "the S&P 500 fund (VOO)"
JUDGE_DAYS = 90
FLAG_POINTS = 5.0
PRICE_WINDOW = 7          # days a flow may wait for the next trading close

EVENTS_SQL = (
    "SELECT kind, ticker, side, quantity, price, amount, currency, balance, "
    "realized, seen_on, fee, verified FROM alinea_events")

# The last check's reading, for the markets module's sync `brief()`.
_state: dict[str, Any] = {}


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _day(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return "amount unknown"
    cur = currency or "currency not stated"
    return f"{cur} {value:,.2f}"


def _shares(q: Decimal) -> str:
    text = f"{q:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass
class Totals:
    """Stated amounts per currency, and how many said no amount at all."""
    by_currency: dict[str | None, Decimal] = field(
        default_factory=lambda: defaultdict(Decimal))
    count: int = 0
    unstated: int = 0

    def add(self, amount: Decimal | None, currency: str | None) -> None:
        self.count += 1
        if amount is None:
            self.unstated += 1
        else:
            self.by_currency[currency] += amount

    def text(self, noun: str) -> str:
        if not self.count:
            return f"no {noun}s seen"
        sums = ", ".join(money(v, c) for c, v in sorted(
            self.by_currency.items(), key=lambda kv: kv[0] or ""))
        head = f"{self.count} {noun}{'' if self.count == 1 else 's'}"
        if not sums:
            return f"{head}, no amount stated"
        tail = (f" ({self.unstated} with no amount stated, not counted)"
                if self.unstated else "")
        return f"{head} totalling {sums}{tail}"


@dataclass
class Summary:
    seen: int = 0
    statement: dict | None = None            # the latest statement row
    statement_age: int | None = None         # days since its email date
    deposits: Totals = field(default_factory=Totals)
    withdrawals: Totals = field(default_factory=Totals)
    dividends: Totals = field(default_factory=Totals)
    realized: Totals = field(default_factory=Totals)
    trades: list[dict] = field(default_factory=list)   # the last RECENT_DAYS
    fees: Totals = field(default_factory=Totals)
    moves: list[dict] = field(default_factory=list)    # deposits and withdrawals
    first_other: date | None = None       # earliest event that is not a move
    unverified: int = 0                   # rows whose sender was not verified
    last_email: date | None = None


def _later_statement(r: dict, day: date, current: dict | None) -> bool:
    """Is `r` the better latest statement? A later email date wins; on the
    same date, a row that states a balance beats one that does not (one
    statement email can yield a balance row and a realised-gain row)."""
    if current is None:
        return True
    cur_day = _day(current["seen_on"])
    if day != cur_day:
        return day > cur_day
    return current.get("balance") is None and r.get("balance") is not None


def summarize(rows: list[dict], today: date) -> Summary:
    """What the stored events say. Pure: no I/O."""
    out = Summary(seen=len(rows))
    for r in rows:
        out.unverified += r.get("verified") is not True
        kind, day = r.get("kind"), _day(r.get("seen_on"))
        if day and (out.last_email is None or day > out.last_email):
            out.last_email = day
        cur = r.get("currency")
        # Only what implies money already in the account: a fee can be
        # billed from the bank before the first deposit (Alinea handbook,
        # read 2026-09-28), and an empty statement holds nothing.
        holds = kind in ("trade", "dividend") or (
            kind == "statement" and (_dec(r.get("balance")) or 0) > 0)
        if holds and day is not None and (
                out.first_other is None or day < out.first_other):
            out.first_other = day
        if kind == "statement":
            if day is not None and _later_statement(r, day, out.statement):
                out.statement = r
        elif kind == "deposit":
            out.deposits.add(_dec(r.get("amount")), cur)
            out.moves.append(r)
        elif kind == "withdrawal":
            out.withdrawals.add(_dec(r.get("amount")), cur)
            out.moves.append(r)
        elif kind == "dividend":
            out.dividends.add(_dec(r.get("amount")), cur)
        elif kind == "trade" and day and (today - day).days <= RECENT_DAYS:
            out.trades.append(r)
        realized = _dec(r.get("realized"))
        if realized is not None:
            out.realized.add(realized, cur)
        fee = _dec(r.get("fee"))
        if fee is not None or kind == "fee":
            out.fees.add(fee, cur)
    if out.statement is not None:
        out.statement_age = (today - _day(out.statement["seen_on"])).days
    out.trades.sort(key=lambda r: _day(r.get("seen_on")) or today)
    return out


def trade_line(r: dict) -> str:
    side = {"buy": "bought", "sell": "sold"}.get(r.get("side") or "", "traded")
    qty = _dec(r.get("quantity"))
    what = (f"{_shares(qty)} share(s)" if qty is not None else "shares (count unknown)")
    ticker = r.get("ticker") or "ticker unknown"
    price = _dec(r.get("price"))
    at = (f" at {money(price, r.get('currency'))}" if price is not None
          else " (price unknown)")
    day = _day(r.get("seen_on"))
    return f"{side} {what} of {ticker}{at}, email of {day:%d %b}" if day else \
        f"{side} {what} of {ticker}{at}, email date unknown"


def balance_line(s: Summary) -> str:
    if s.statement is None:
        return "balance unknown: no Alinea statement seen yet"
    day = _day(s.statement["seen_on"])
    bal = _dec(s.statement.get("balance"))
    if bal is None:
        return f"balance unknown: the statement of {day:%d %b %Y} states none"
    return (f"balance {money(bal, s.statement.get('currency'))} as stated in the "
            f"statement of {day:%d %b %Y} ({s.statement_age} day(s) old)")


def lines(s: Summary) -> list[str]:
    """The figures, each with its email date or marked unknown."""
    trades = (f"{len(s.trades)} trade(s) in emails of the last {RECENT_DAYS} days"
              + (": " + "; ".join(trade_line(t) for t in s.trades[-5:])
                 if s.trades else ""))
    return [
        balance_line(s),
        "deposits: " + s.deposits.text("deposit"),
        "withdrawals: " + s.withdrawals.text("withdrawal"),
        trades,
        "dividends: " + s.dividends.text("dividend"),
        ("realised gain/loss: " + s.realized.text("stated figure")
         if s.realized.count else "realised gain/loss: unknown (no email stated one)"),
        ("fees stated: " + s.fees.text("fee") if s.fees.count
         else "fees: none stated in any email"),
    ] + ([(f"{s.unverified} of {s.seen} event(s) from emails whose sender "
           f"could not be verified (that mailbox shows no DKIM result); "
           f"counted, not proven")] if s.unverified else [])


def health_of(s: Summary) -> tuple[Health, str]:
    if not s.seen:
        return Health.UNKNOWN, NEVER_SEEN
    if s.statement is None:
        return Health.UNKNOWN, "no Alinea statement seen yet, so no balance"
    if s.statement_age is not None and s.statement_age > STALE_DAYS:
        return Health.UNKNOWN, (f"the last Alinea statement is {s.statement_age} days "
                                f"old (over {STALE_DAYS}); its balance is not today's")
    if _dec(s.statement.get("balance")) is None:
        return Health.UNKNOWN, "the latest Alinea statement states no balance"
    return Health.OK, ""


@dataclass
class Benchmark:
    """The stated flows, put into VOO instead, beside the stated balance."""
    known: bool
    why: str = ""
    balance: Decimal | None = None
    voo: Decimal | None = None
    net: Decimal | None = None
    since: date | None = None
    statement_on: date | None = None
    read_at: datetime | None = None

    @property
    def alinea_pct(self) -> float:
        return float(self.balance / self.net - 1) * 100

    @property
    def voo_pct(self) -> float:
        return float(self.voo / self.net - 1) * 100

    @property
    def gap(self) -> float:
        """Alinea minus VOO, in percentage points."""
        return self.alinea_pct - self.voo_pct

    @property
    def days(self) -> int:
        return (self.statement_on - self.since).days

    @property
    def flagged(self) -> bool:
        return (self.known and self.days >= JUDGE_DAYS
                and self.gap < -FLAG_POINTS)

    def verdict(self) -> str:
        if self.days < JUDGE_DAYS:
            return (f"too early to judge ({self.days} days of history; judged "
                    f"from {JUDGE_DAYS})")
        way = "trailing" if self.gap < 0 else "ahead of"
        return (f"{way} {BENCHMARK_NAME} by {abs(self.gap):.1f} points since "
                f"{self.since:%-d %b %Y}")

    def text(self) -> str:
        if not self.known:
            return f"benchmark vs {BENCHMARK_NAME} unknown: {self.why}"
        return (f"Alinea {money(self.balance, 'USD')} ({self.alinea_pct:+.1f}%) vs "
                f"same deposits in {BENCHMARK} {money(self.voo, 'USD')} "
                f"(dividends reinvested) "
                f"({self.voo_pct:+.1f}%), on net deposits of "
                f"{money(self.net, 'USD')}, statement {self.statement_on:%-d %b}, "
                f"prices read {self.read_at:%H:%M} UTC; {self.verdict()}")


def _unknown(why: str) -> Benchmark:
    return Benchmark(False, why)


def flows_of(s: Summary) -> tuple[list[tuple[date, Decimal]], str | None]:
    """(date, signed USD amount) for every stated deposit and withdrawal up
    to the statement, or the reason there is no benchmark to compute."""
    if s.statement is None or _dec(s.statement.get("balance")) is None:
        return [], "no statement with a balance"
    if s.statement.get("currency") != "USD":
        return [], "the statement's balance is not stated in USD"
    upto = _day(s.statement["seen_on"])
    flows: list[tuple[date, Decimal]] = []
    for r in s.moves:
        day, amount = _day(r.get("seen_on")), _dec(r.get("amount"))
        if day is None or day > upto:
            continue
        noun = r["kind"]
        if amount is None:
            return [], f"a {noun} of {day:%-d %b %Y} states no amount"
        if r.get("currency") != "USD":
            return [], f"a {noun} of {day:%-d %b %Y} is not stated in USD"
        flows.append((day, amount if noun == "deposit" else -amount))
    if not any(a > 0 for _d, a in flows):
        return [], "no deposit with a stated amount before the statement"
    flows.sort()
    first_deposit = min(d for d, a in flows if a > 0)
    if s.first_other is not None and s.first_other < first_deposit:
        return [], (f"Alinea emails from {s.first_other:%-d %b %Y} predate the "
                    f"first deposit seen ({first_deposit:%-d %b %Y}), so a "
                    f"deposit was not seen")
    return flows, None


def compute(flows: list[tuple[date, Decimal]], balance: Decimal, statement_on: date,
            prices: dict[date, float], read_at: datetime) -> Benchmark:
    """The benchmark from flows and daily closes. Pure: no I/O. A close
    that is missing where one is needed makes it UNKNOWN."""
    shares = Decimal(0)
    for day, amount in flows:
        buy = next((d for d in sorted(prices)
                    if day <= d <= min(day + timedelta(days=PRICE_WINDOW),
                                       statement_on)), None)
        if buy is None:
            return _unknown(f"no {BENCHMARK} close could be read for the flow of "
                            f"{day:%-d %b %Y}")
        shares += amount / Decimal(str(prices[buy]))
    value_on = max((d for d in prices if statement_on - timedelta(days=PRICE_WINDOW)
                    <= d <= statement_on), default=None)
    if value_on is None:
        return _unknown(f"no {BENCHMARK} close could be read for the statement of "
                        f"{statement_on:%-d %b %Y}")
    net = sum((a for _d, a in flows), Decimal(0))
    if net <= 0:
        return _unknown("net deposits are not positive, so no return compares")
    voo = (shares * Decimal(str(prices[value_on]))).quantize(Decimal("0.01"))
    return Benchmark(True, balance=balance, voo=voo, net=net, since=flows[0][0],
                     statement_on=statement_on, read_at=read_at)


async def benchmark(s: Summary) -> Benchmark:
    """Read the closes the flows need, fresh, and compute."""
    flows, why = flows_of(s)
    if why:
        return _unknown(why)
    from . import closes
    statement_on = _day(s.statement["seen_on"])
    start = flows[0][0] - timedelta(days=PRICE_WINDOW)
    prices = await closes(BENCHMARK, start, statement_on + timedelta(days=1),
                          adjusted=True)
    read_at = datetime.now(UTC)
    if not prices:
        return _unknown(f"{BENCHMARK} dividend-adjusted prices could not be read "
                        f"from the public chart feed")
    return compute(flows, _dec(s.statement["balance"]), statement_on, prices,
                   read_at)


async def assess(s: Summary) -> tuple[Health, str, Benchmark | None]:
    """Health, why, and the benchmark when the balance itself is readable."""
    health, why = health_of(s)
    if health is not Health.OK:
        return health, why, None
    b = await benchmark(s)
    if not b.known:
        return Health.UNKNOWN, f"benchmark unknown: {b.why}", b
    if b.flagged:
        return Health.DEGRADED, (f"more than {FLAG_POINTS:.0f} points behind "
                                 f"{BENCHMARK_NAME} over {b.days} days"), b
    return Health.OK, "", b


def never_seen() -> str:
    """Why there is nothing: never "sign up" when mail from an Alinea domain
    did arrive and was dropped for failing DKIM."""
    from ..mail import alinea as collector
    result = collector._state.get("result")
    forged = getattr(result, "forged", 0)
    if forged:
        return (f"no trusted Alinea email yet: {forged} from an Alinea domain "
                f"failed DKIM and were not read")
    return NEVER_SEEN


async def read(today: date | None = None) -> Summary:
    """The stored events, summarised. Raises when the table can't be read."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(EVENTS_SQL)
    return summarize([dict(r) for r in rows],
                     today or datetime.now(UTC).astimezone().date())


async def check_alinea() -> Observation:
    now = datetime.now(UTC)
    try:
        s = await read()
    except Exception as exc:  # noqa: BLE001
        _state.update(at=now, summary=None, error=type(exc).__name__)
        return Observation("alinea", Health.UNKNOWN,
                           detail=f"Alinea readings could not be read "
                                  f"({type(exc).__name__}). {LABEL}")
    if not s.seen:
        _state.update(at=now, summary=s, bench=None, error=None)
        return Observation("alinea", Health.UNKNOWN,
                           detail=f"{never_seen()}. {LABEL}")
    health, why, bench = await assess(s)
    _state.update(at=now, summary=s, bench=bench, error=None)
    bal = _dec(s.statement.get("balance")) if s.statement else None
    detail = "; ".join(lines(s) + ([bench.text()] if bench else []))
    if why:
        flag = "flagged: " if health is Health.DEGRADED else ""
        detail = f"{flag}{why}. {detail}"
    return Observation(
        "alinea", health,
        float(bal) if bal is not None and health is not Health.UNKNOWN else None,
        (s.statement.get("currency") if s.statement else None) or None,
        f"{detail} (read {now:%d %b %H:%M} UTC from Alinea's emails). {LABEL}",
        evidence={
            "events": s.seen,
            "statement_date": (_day(s.statement["seen_on"]).isoformat()
                               if s.statement else None),
            "balance": str(bal) if bal is not None else None,
            "last_email": s.last_email.isoformat() if s.last_email else None,
            "trades_recent": len(s.trades),
            "read_at": now.isoformat(),
            "benchmark": ({"symbol": BENCHMARK, "value": str(bench.voo),
                           "net_deposits": str(bench.net),
                           "gap_points": round(bench.gap, 2), "days": bench.days,
                           "flagged": bench.flagged,
                           "prices_read_at": bench.read_at.isoformat()}
                          if bench and bench.known else None),
        })


def brief_line() -> str:
    """One line from the last check, with when it was read. Nothing before
    the check has run -- never a figure it did not read."""
    at = _state.get("at")
    if at is None:
        return ""
    if _state.get("error"):
        return f"Alinea: readings unreadable ({_state['error']}, {at:%H:%M} UTC)"
    s: Summary = _state["summary"]
    if not s.seen:
        return f"Alinea: {never_seen()}"
    bench: Benchmark | None = _state.get("bench")
    versus = f"; {bench.text()}" if bench else ""
    return (f"Alinea: {balance_line(s)}; {len(s.trades)} trade(s) in the last "
            f"{RECENT_DAYS} days{versus} (read {at:%H:%M} UTC). {LABEL}")


async def playlist_section(now: datetime | None = None) -> str:
    """The Claude playlist (paper)'s weekly paragraph (`playlist.py`), beside
    Alinea's own. Never raises."""
    from .playlist import playlist_section as section
    return await section(now)


async def ideas_section(now: datetime | None = None) -> str:
    """The paper idea book's weekly paragraph (`ideas.py`). Never raises."""
    from .ideas import ideas_section as section
    return await section(now)


async def weekly_section(now: datetime | None = None) -> str:
    """The weekly summary's Alinea paragraph, read fresh from the table."""
    now = now or datetime.now(UTC)
    s = await read(now.astimezone().date())
    head = f"*Alinea*\n{LABEL}"
    if not s.seen:
        return f"{head}\nNo Alinea email seen yet, so every figure is unknown — " \
               f"sign up and turn on Automated Investing."
    health, why, bench = await assess(s)
    body = "\n".join(f"• {line}" for line in
                     lines(s) + ([bench.text()] if bench else []))
    note = ("" if health is Health.OK
            else f"\nFlagged: {why}." if health is Health.DEGRADED
            else f"\nUnknown, not fine: {why}.")
    return f"{head}{note}\n{body}\n(Read {now:%a %d %b %H:%M} UTC.)"
