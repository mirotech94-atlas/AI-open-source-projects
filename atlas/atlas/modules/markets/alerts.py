"""markets.alert -- Ivan tells the owner when something happened. It never trades.

the owner, 2026-09-29: "act, not just watch". The act is a Slack message to him,
nothing more: a Tier 1 action (`markets.alert`) run by the kernel's alert
tick (`kernel/market_alerts.py`) through `executor.execute`, like every
other action. What it alerts on, each from a reading already taken, with
that reading's time:

* **a big move** -- a watchlist stock that moved MORE than
  MARKETS_ALERT_MOVE_PCT (default 5) either way on the day, from the basket
  check's own prices (`markets.basket`), keyed to the trading day of the
  price so a Friday move is not alerted again on Saturday; only from a
  basket reading taken in the last FRESH_MOVE;
* **a new 8-K**, and **an insider purchase or sale** (Form 4 code P or S),
  from `markets.filings` (`edgar.py`); never a filing its ticker's first,
  baseline read stored;
* **Alinea trailing VOO** by the Alinea check's own rule (more than 5
  points over at least 90 days, `alinea.py`), once per statement;
* **the owner's own TradingView alert**, each one TradingView sent to
  /hooks/tradingview (`tradingview.py`): its name, ticker, TradingView's
  price and time, and Ivan's own latest price with its read time when he
  has one. Marked notified in `tradingview_alerts` only once delivered.

One alert per event: `markets_alerts.key` is the event, and the row is
claimed atomically before anything is sent. At most MARKETS_ALERT_DAILY_CAP
(default 10) a day, counted in the same claim. Quiet hours are the comms
layer's own: below CRITICAL, `comms.notify` queues the alert for the morning
brief instead of sending it (`["queued"]`), and that counts as sent. An
alert older than MAX_AGE is expired, never sent late.

The action takes only the event's key. Its text was composed by code from
stated figures when the event was planned; the dry run refuses any text
carrying an advice word (ADVICE_WORDS), so no alert can say buy, sell or
hold. Rollback records the alert as retracted: a Slack message cannot be
unsent, so nothing is performed (the same as `mac.notify`'s).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult, FailureClass

log = logging.getLogger("atlas.markets.alerts")

ACTION = "markets.alert"
TOPIC = "markets"
EXCHANGE_TZ = ZoneInfo("America/New_York")
FRESH_MOVE = timedelta(hours=2)          # a basket reading older than this alerts nothing
FRESH_ALINEA = timedelta(hours=6)
FILING_WINDOW = timedelta(hours=24)      # a filing first seen longer ago is not new
MAX_AGE = timedelta(hours=24)            # a planned alert older than this expires
FOOTER = "Watch only: ATLAS never trades and this is not advice."

# Words that turn a fact into advice. Matched as whole words, so "holdings"
# or "trailing" pass and "hold" does not. The dry run refuses any alert that
# carries one; tests hold every composed alert to it.
ADVICE_WORDS = frozenset({"buy", "sell", "hold", "should", "recommend", "advise",
                          "consider", "switch", "opportunity"})


def advice_in(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower())) & ADVICE_WORDS


def daily_cap() -> int:
    """MARKETS_ALERT_DAILY_CAP: alerts a day, at most. Default 10."""
    try:
        cap = int(os.getenv("MARKETS_ALERT_DAILY_CAP", "10"))
    except ValueError:
        return 10
    return max(0, min(cap, 50))


def local_midnight(now: datetime) -> datetime:
    """The start of today where ATLAS runs, as an aware UTC time."""
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


@dataclass(frozen=True)
class Event:
    key: str
    kind: str                # move | filing | insider | alinea
    ticker: str | None
    title: str
    body: str
    at: datetime             # when the reading behind it was taken


def _when(value: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


# ------------------------------------------------------------ the events

def move_events(evidence: dict, now: datetime, threshold: float) -> list[Event]:
    """Big moves in the basket check's evidence. Pure."""
    checked = _when(evidence.get("checked_at"))
    if checked is None or now - checked > FRESH_MOVE:
        return []
    moves = evidence.get("moves") or {}
    prices = evidence.get("prices") or {}
    prevs = evidence.get("prev") or {}
    stamps = evidence.get("at") or {}
    out = []
    for sym, pct in sorted(moves.items()):
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            continue
        at = _when(stamps.get(sym))
        if abs(pct) <= threshold or at is None or sym not in prices:
            continue
        day = at.astimezone(EXCHANGE_TZ).date()
        way = "up" if pct > 0 else "down"
        prev = prevs.get(sym)
        against = (f" vs previous close ${float(prev):,.2f}" if prev is not None
                   else "")
        out.append(Event(
            key=f"move:{sym}:{day.isoformat()}", kind="move", ticker=sym,
            title=f"{sym} {way} {abs(pct):.1f}% on the day",
            body=(f"{sym} at ${float(prices[sym]):,.2f}{against}: {pct:+.2f}% "
                  f"(price as of {at:%a %d %b %H:%M} UTC, read by the basket "
                  f"check at {checked:%H:%M} UTC). Alerted for a move of more "
                  f"than {threshold:g}% either way, once per stock per trading "
                  f"day. {FOOTER}"),
            at=checked))
    return out


def filing_events(filings: list[dict], insider: list[dict],
                  now: datetime) -> list[Event]:
    """New 8-Ks and insider purchases/sales from `edgar.recent` rows. A row
    stored by its ticker's baseline read, or first seen more than
    FILING_WINDOW ago, is not new. Pure."""
    from .edgar import insider_text, items_text

    def new(row: dict) -> datetime | None:
        seen = row.get("read_at")
        if row.get("baseline") or not isinstance(seen, datetime):
            return None
        seen = seen if seen.tzinfo else seen.replace(tzinfo=UTC)
        return seen if now - seen <= FILING_WINDOW else None

    out = []
    for row in filings:
        seen = new(row)
        if seen is None or row.get("form") not in ("8-K", "8-K/A"):
            continue
        what = items_text(row.get("items"))
        filed = row.get("filed_on")
        out.append(Event(
            key=f"filing:{row['accession']}", kind="filing", ticker=row["ticker"],
            title=f"{row['ticker']} filed an {row['form']}"
                  + (f" ({what})" if what else ""),
            body=(f"{row['ticker']} filed an {row['form']} with the SEC on "
                  f"{filed:%-d %b %Y}" + (f", {what}" if what else "")
                  + f". {row['url']} (first read from SEC EDGAR "
                  f"{seen:%a %d %b %H:%M} UTC). {FOOTER}"),
            at=seen))
    for row in insider:
        seen = new(row)
        # A 4/A restates a trade already filed under its own accession
        # number: listed by the check, never alerted a second time.
        if seen is None or row.get("form") == "4/A":
            continue
        noun = "purchase" if row.get("code") == "P" else "sale"
        out.append(Event(
            key=f"insider:{row['accession']}:{row['code']}", kind="insider",
            ticker=row["ticker"],
            title=f"{row['ticker']} insider {noun} reported (Form 4)",
            body=(f"{insider_text(row)}. {row['url']} (read from SEC EDGAR "
                  f"{seen:%a %d %b %H:%M} UTC). A Form 4 states a trade already "
                  f"made. {FOOTER}"),
            at=seen))
    return out


def alinea_event(evidence: dict, now: datetime) -> Event | None:
    """Alinea trailing VOO by the Alinea check's own rule. Pure."""
    bench = evidence.get("benchmark") or {}
    read = _when(bench.get("prices_read_at"))
    statement = evidence.get("statement_date")
    if not bench.get("flagged") or read is None or not statement:
        return None
    if now - read > FRESH_ALINEA:
        return None
    try:
        gap = float(bench["gap_points"])
        days = int(bench["days"])
        on = date.fromisoformat(str(statement))
    except (KeyError, TypeError, ValueError):
        return None
    from .alinea import BENCHMARK_NAME, FLAG_POINTS, JUDGE_DAYS, LABEL
    return Event(
        key=f"alinea_trails:{on.isoformat()}", kind="alinea", ticker=None,
        title=f"Alinea is trailing {BENCHMARK_NAME} by {abs(gap):.1f} points",
        body=(f"Over {days} days to the statement of {on:%-d %b %Y}, Alinea's "
              f"stated balance is {abs(gap):.1f} points behind the same "
              f"deposits put into {BENCHMARK_NAME} with dividends reinvested "
              f"(prices read {read:%a %d %b %H:%M} UTC). Flagged when more than "
              f"{FLAG_POINTS:g} points behind over at least {JUDGE_DAYS} days. "
              f"{LABEL} {FOOTER}"),
        at=read)


async def gather(now: datetime) -> list[Event]:
    """Every event the latest readings show. Reads only."""
    from ...kernel.state import last_evidence
    from . import edgar, move_pct
    events = move_events(await last_evidence("markets", "basket"), now, move_pct())
    filings, insider = await edgar.recent(now.date() - timedelta(days=5))
    events += filing_events(filings, insider, now)
    trailing = alinea_event(await last_evidence("markets", "alinea"), now)
    if trailing is not None:
        events.append(trailing)
    events += await tradingview_events(now)
    return [e for e in events if not advice_in(e.title + " " + e.body)]


async def tradingview_events(now: datetime) -> list[Event]:
    """the owner's own TradingView alerts not yet delivered (`tradingview.py`).
    A read that fails is logged and skipped: TradingView's table must not
    hold back a move, a filing or Alinea."""
    from .tradingview import EVENT_FIELDS, pending_tradingview_events
    try:
        pending = await pending_tradingview_events(now)
    except Exception as exc:                            # noqa: BLE001
        log.warning("tradingview alerts not read for alerting (%s)",
                    type(exc).__name__)
        return []
    out = []
    for e in pending:
        fields = {k: e[k] for k in EVENT_FIELDS}
        # Ivan's one-line verdict (ideas.check_alert): the idea rules on
        # that ticker, once per alert; a card if one fired. It rides under
        # the alert's own line. Never blocks the alert: a check that fails
        # says so inside itself, and one that raises is left off.
        try:
            from . import ideas
            note = await ideas.check_alert(
                int(e["id"]), str(e["ticker"]),
                str((e.get("untrusted") or {}).get("name") or ""),
                e.get("price"), e.get("alert_time"), now)
            if note:
                fields["body"] = f"{fields['body']}\n{note}"
        except Exception as exc:                        # noqa: BLE001
            log.warning("tradingview alert %s: Ivan's check not run (%s)",
                        e.get("key"), type(exc).__name__)
        out.append(Event(**fields))
    return out


# ------------------------------------------------------------- the ledger

async def plan(events: list[Event]) -> int:
    """Record each event once. Returns how many were new."""
    from ...kernel.state import fetch_one
    added = 0
    for e in events:
        row = await fetch_one(
            "INSERT INTO markets_alerts (key, kind, ticker, title, body, event_at) "
            "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (key) DO NOTHING RETURNING key",
            e.key, e.kind, e.ticker, e.title, e.body, e.at)
        added += row is not None
    return added


async def expire(now: datetime) -> None:
    from ...kernel.state import execute_sql
    await execute_sql(
        "UPDATE markets_alerts SET status = 'expired' WHERE status = 'pending' "
        "AND event_at < $1::timestamptz", now - MAX_AGE)


async def claimed_today(now: datetime) -> int:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM markets_alerts WHERE status IN ('sent', "
        "'sending', 'retracted') AND claimed_at >= $1::timestamptz",
        local_midnight(now))
    return int(row["n"]) if row else 0


async def pending(limit: int) -> list[str]:
    from ...kernel.state import fetch_all
    if limit <= 0:
        return []
    rows = await fetch_all(
        "SELECT key FROM markets_alerts WHERE status = 'pending' "
        "ORDER BY event_at, key LIMIT $1", limit)
    return [str(r["key"]) for r in rows]


# ------------------------------------------------------------- the action

async def _row(key: str) -> dict | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT key, kind, ticker, title, body, event_at, status FROM "
        "markets_alerts WHERE key = $1", key)
    return dict(row) if row else None


def _problem(row: dict | None, now: datetime, used: int, cap: int) -> str | None:
    """Why this alert must not be sent, or None. Pure."""
    if row is None:
        return "no such planned alert"
    if row["status"] != "pending":
        return f"that alert is already {row['status']} (one alert per event)"
    at = row["event_at"]
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    if now - at > MAX_AGE:
        return "the reading behind that alert is over a day old"
    if used >= cap:
        return f"today's cap of {cap} market alert(s) is reached"
    words = advice_in(f"{row['title']} {row['body']}")
    if words:
        return f"the alert's text carries advice ({', '.join(sorted(words))})"
    return None


async def alert_dry(key: str) -> ActionResult:
    now = datetime.now(UTC)
    row = await _row(str(key))
    why = _problem(row, now, await claimed_today(now), daily_cap())
    if why:
        return ActionResult(False, why)
    return ActionResult(True, f"would send the owner: {row['title']}",
                        evidence={"key": key, "kind": row["kind"]})


CLAIM_LOCK = "atlas.markets_alerts.claim"
CLAIM_SQL = (
    "UPDATE markets_alerts SET status = 'sending', claimed_at = now() "
    "WHERE key = $1 AND status = 'pending' AND event_at >= $2::timestamptz "
    "AND (SELECT count(*) FROM markets_alerts WHERE status IN ('sent', 'sending', "
    "'retracted') AND claimed_at >= $3::timestamptz) < $4 "
    "RETURNING title, body, kind")


async def alert(key: str) -> ActionResult:
    """Claim the event (once, under the cap, atomically) and send it."""
    from ...kernel.comms import Outbound, notify
    from ...kernel.contracts import Severity
    from ...kernel.state import execute_sql, fetch_one_locked
    now = datetime.now(UTC)
    # Under one advisory lock: the count and the claim cannot be raced by a
    # concurrent claim of another event (tests/test_markets_pg.py).
    row = await fetch_one_locked(CLAIM_LOCK, CLAIM_SQL, str(key), now - MAX_AGE,
                                 local_midnight(now), daily_cap())
    if row is None:
        return ActionResult(False, "not sent: already alerted, expired, or today's "
                                   "cap is reached")
    title, body = str(row["title"]), str(row["body"])
    if advice_in(f"{title} {body}"):
        await execute_sql("UPDATE markets_alerts SET status = 'expired' "
                          "WHERE key = $1", key)
        return ActionResult(False, "not sent: the alert's text carries advice")
    try:
        used = await notify(Outbound(title=title, body=body, severity=Severity.NORMAL,
                                     topic=TOPIC, kind="alert", presentation="INFO"))
    except Exception:                                   # noqa: BLE001
        used = []
    if not used:
        await execute_sql("UPDATE markets_alerts SET status = 'pending', "
                          "claimed_at = NULL WHERE key = $1", key)
        # TRANSIENT: the tick stops on it, so a Slack outage does not spend
        # the executor's module and global hourly budgets retrying.
        return ActionResult(False, "not sent: no channel delivered it; it stays "
                                   "pending for the next tick",
                            failure_class=FailureClass.TRANSIENT)
    await execute_sql("UPDATE markets_alerts SET status = 'sent', channels = $2 "
                      "WHERE key = $1", key, ",".join(used))
    if str(row["kind"]) == "tradingview":
        # Delivered: only now is the TradingView row notified. A failed mark
        # sends nothing twice -- this key is 'sent' in the ledger.
        from .tradingview import mark_tradingview_notified
        try:
            await mark_tradingview_notified([str(key)])
        except Exception as exc:                        # noqa: BLE001
            log.warning("tradingview alert %s delivered but not marked (%s)",
                        key, type(exc).__name__)
    how =("queued for the morning brief (quiet hours)" if used == ["queued"]
           else f"sent over {', '.join(used)}")
    return ActionResult(True, f"{title}: {how}", evidence={"key": key,
                                                           "channels": used},
                        rollback_token=str(key))


async def alert_rollback(token: str) -> ActionResult:
    """Recorded, not performed: a Slack message cannot be unsent. The row is
    marked retracted, so the ledger says the alert was withdrawn."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "UPDATE markets_alerts SET status = 'retracted', retracted_at = now() "
        "WHERE key = $1 AND status = 'sent' RETURNING title", str(token))
    if row is None:
        return ActionResult(False, "no sent alert under that key to retract")
    return ActionResult(True, f"recorded as retracted: {row['title']} (a sent "
                              f"message cannot be unsent)")
