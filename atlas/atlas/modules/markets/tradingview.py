"""TradingView alerts: the owner's own chart alerts, read in. ATLAS never trades on them.

the owner sets an alert on a TradingView chart; TradingView POSTs its message to
`/hooks/tradingview` (`atlas/api/tradingview.py`), which authenticates it,
validates it against the closed set of fields below and stores it here, in
`tradingview_alerts`. Nothing else happens. An alert is the owner's own chart
line crossing, not advice, and nothing in ATLAS acts on one: there is no
order code anywhere in ATLAS, and money out is Tier 3, permanently denied.

What reads the table:

* the `tradingview` check (`check_tradingview`): the last alert's time, how
  many arrived today, the latest per ticker. UNKNOWN when the secret is not
  configured, when the table can't be read, or when no alert arrived inside
  TRADINGVIEW_STALE_HOURS (default 72) -- never OK without an alert;
* Ivan's brief (`brief_line`), from the check's last reading: the facts
  composed by code, and the alerts' own words (`name`, `message`) inside
  `wrap_untrusted`, because they are text from outside;
* the markets.alert pipeline (`alerts.gather`), through
  `pending_tradingview_events()`: each stored alert becomes one Slack
  alert to the owner (topic `markets`), under that pipeline's dedupe, daily cap,
  quiet hours and advice-word ban. A row is marked notified only by
  `mark_tradingview_notified`, which `alerts.alert` calls once the alert
  has been delivered.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from ...kernel.contracts import Health, Observation

SECRET_ENV = "TRADINGVIEW_WEBHOOK_SECRET"
ALLOWED_IPS_ENV = "TRADINGVIEW_ALLOWED_IPS"
STALE_ENV = "TRADINGVIEW_STALE_HOURS"
DEFAULT_STALE_HOURS = 72.0
# The secret is the only gate TradingView can carry (it cannot sign a
# request), so a short one counts as not configured: `openssl rand -hex 32`.
MIN_SECRET_LEN = 32
MAX_BODY = 8 * 1024
DEDUP_WINDOW = timedelta(minutes=10)
# The alert pipeline expires what it could not send inside 24 hours; an
# older row is never handed to it, so nothing is ever sent late.
PENDING_MAX_AGE = timedelta(hours=24)
# Ivan's own basket reading counts for a comparison only while this fresh.
OWN_READING_FRESH = timedelta(hours=6)
BRIEF_ALERTS = 3
KIND = "tradingview"
SOURCE = "tradingview alerts"
FOOTER = ("the owner's own TradingView alert, relayed: not advice, and ATLAS never "
          "trades on it.")

ACTION_HINTS = frozenset({"buy", "sell", "long", "short", "exit", "alert"})
# The closed set. `name` and `strategy` are the same field, the alert's or
# the strategy's name (one or the other, never both).
FIELDS = frozenset({"secret", "ticker", "exchange", "interval", "price", "time",
                    "name", "strategy", "action_hint", "message"})
NAME_MAX = 80
MESSAGE_MAX = 500
PRICE_MAX = 1e12
FUTURE_SLACK = timedelta(days=1)

# `!` for continuous futures (ES1!, NQ1!).
_TICKER = re.compile(r"^[A-Z0-9.:!\-]{1,20}$")
_EXCHANGE = re.compile(r"^[A-Z0-9_.\-]{1,20}$")
_INTERVAL = re.compile(r"^[A-Z0-9]{1,8}$")
# Control characters. A message may keep its line breaks and tabs.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_BUT_LINES = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


class Invalid(ValueError):
    """A field outside the closed set, or a value outside its rule. Carries
    the field's NAME only -- never the value, which is outside text."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field


@dataclass(frozen=True, slots=True)
class Alert:
    ticker: str
    exchange: str | None = None
    interval: str | None = None
    price: float | None = None
    time: datetime | None = None
    name: str | None = None
    action_hint: str | None = None
    message: str | None = None

    def dedup_key(self) -> str:
        """sha256 over (ticker, time, name, price): the same alert sent
        twice is the same key; a later bar or another price is not."""
        parts = [self.ticker,
                 self.time.isoformat() if self.time else None,
                 self.name,
                 repr(float(self.price)) if self.price is not None else None]
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------- configuration

def secret() -> str:
    return os.getenv(SECRET_ENV, "").strip()


def configured() -> bool:
    return len(secret()) >= MIN_SECRET_LEN


def not_configured_reason() -> str:
    if not secret():
        return f"{SECRET_ENV} not set"
    return f"{SECRET_ENV} is shorter than {MIN_SECRET_LEN} characters"


def stale_after() -> timedelta:
    try:
        hours = float(os.getenv(STALE_ENV, str(DEFAULT_STALE_HOURS)))
    except ValueError:
        hours = DEFAULT_STALE_HOURS
    if not math.isfinite(hours) or hours <= 0:
        hours = DEFAULT_STALE_HOURS
    return timedelta(hours=hours)


# ------------------------------------------------------------------ validation

def _text(payload: dict, field: str, pattern: re.Pattern[str]) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise Invalid(field)
    value = value.strip().upper()
    if not pattern.fullmatch(value):
        raise Invalid(field)
    return value


def parse(payload: Any, now: datetime | None = None) -> Alert:
    """The alert in `payload`, or `Invalid(field)`. The closed set: any key
    not in FIELDS is refused, and every value is held to its own rule.
    `secret` is allowed as a key and ignored here (the route checks it)."""
    now = now or _now()
    if not isinstance(payload, dict):
        raise Invalid("body")
    for key in payload:
        if key not in FIELDS:
            raise Invalid("unknown field")

    ticker = _text(payload, "ticker", _TICKER)
    if ticker is None or not re.search(r"[A-Z0-9]", ticker):
        raise Invalid("ticker")
    exchange = _text(payload, "exchange", _EXCHANGE)
    interval = _text(payload, "interval", _INTERVAL)

    price = payload.get("price")
    if price is not None:
        # bool is an int to Python; a price is never true or false.
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise Invalid("price")
        price = float(price)
        if not math.isfinite(price) or abs(price) >= PRICE_MAX:
            raise Invalid("price")

    at = payload.get("time")
    if at is not None:
        if not isinstance(at, str) or len(at) > 40:
            raise Invalid("time")
        try:
            at = datetime.fromisoformat(at.strip())
        except ValueError:
            raise Invalid("time") from None
        if at.tzinfo is None:
            raise Invalid("time")          # TradingView's {{time}} is UTC, "Z"
        at = at.astimezone(UTC)
        if at > now + FUTURE_SLACK:
            raise Invalid("time")

    if "name" in payload and "strategy" in payload:
        raise Invalid("name")
    name_field = "strategy" if "strategy" in payload else "name"
    name = payload.get(name_field)
    if name is not None:
        if not isinstance(name, str):
            raise Invalid(name_field)
        name = name.strip()
        if not name or len(name) > NAME_MAX or _CONTROL.search(name):
            raise Invalid(name_field)

    hint = payload.get("action_hint")
    if hint is not None:
        if not isinstance(hint, str) or hint.strip().lower() not in ACTION_HINTS:
            raise Invalid("action_hint")
        hint = hint.strip().lower()

    message = payload.get("message")
    if message is not None:
        if not isinstance(message, str):
            raise Invalid("message")
        message = message.strip()
        if len(message) > MESSAGE_MAX or _CONTROL_BUT_LINES.search(message):
            raise Invalid("message")
        message = message or None

    return Alert(ticker=ticker, exchange=exchange, interval=interval, price=price,
                 time=at, name=name, action_hint=hint, message=message)


# ----------------------------------------------------------------------- store

# One API process (the Dockerfile's single uvicorn worker), so an in-process
# lock serialises the look-then-insert below. Without it, two copies of the
# same alert arriving together would each find no earlier copy under READ
# COMMITTED and both be stored.
_store_lock = asyncio.Lock()


async def store(alert: Alert) -> int | None:
    """Insert the alert; its row id, or None when the same alert (same
    dedup key) was already stored inside DEDUP_WINDOW."""
    from ...kernel.state import fetch_one
    async with _store_lock:
        row = await fetch_one(
            """INSERT INTO tradingview_alerts
                   (dedup_key, ticker, exchange, bar_interval, price, alert_time,
                    name, action_hint, message)
               SELECT $1::text, $2::text, $3::text, $4::text, $5::numeric,
                      $6::timestamptz, $7::text, $8::text, $9::text
                WHERE NOT EXISTS (
                      SELECT 1 FROM tradingview_alerts
                       WHERE dedup_key = $1::text
                         AND received_at > now() - make_interval(secs => $10::float8))
               RETURNING id""",
            alert.dedup_key(), alert.ticker, alert.exchange, alert.interval,
            Decimal(repr(alert.price)) if alert.price is not None else None,
            alert.time, alert.name, alert.action_hint, alert.message,
            DEDUP_WINDOW.total_seconds())
    return int(row["id"]) if row else None


# ----------------------------------------------------------------------- check

_state: dict[str, Any] = {}


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return _aware(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def local_midnight(now: datetime) -> datetime:
    """The start of today where ATLAS runs, as an aware UTC time."""
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def bare_symbol(ticker: str) -> str:
    """"NASDAQ:AAPL" -> "AAPL": the basket keys prices by bare symbol."""
    return ticker.rsplit(":", 1)[-1]


async def _own_readings(
        now: datetime) -> tuple[dict[str, tuple[float, datetime]], datetime | None]:
    """The basket check's own last prices, each with the time of its price,
    and when the check read them -- only when that read is fresh. Empty when
    there is none, or it is older than OWN_READING_FRESH: a stale price is
    never offered as a comparison. Freshness is the check's own read time
    (`checked_at`), not the price's: on a weekend Friday's close is still
    the latest price, and still a reading taken now."""
    from ...kernel.state import last_evidence
    evidence = await last_evidence("markets", "basket")
    checked = _aware(evidence.get("checked_at")) or _aware(evidence.get("read_at"))
    prices = evidence.get("prices") or {}
    stamps = evidence.get("at") or {}
    if (checked is None or now - checked > OWN_READING_FRESH
            or not isinstance(prices, dict) or not isinstance(stamps, dict)):
        return {}, None
    fallback = _aware(evidence.get("read_at")) or checked
    out: dict[str, tuple[float, datetime]] = {}
    for sym, raw in prices.items():
        price = _num(raw)
        if price is not None:
            out[str(sym)] = (price, _aware(stamps.get(sym)) or fallback)
    return out, checked


def _row(r: Any) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "ticker": str(r["ticker"]),
        "exchange": r["exchange"],
        "interval": r["bar_interval"],
        "price": _num(r["price"]),
        "alert_time": _aware(r["alert_time"]),
        "name": r["name"],
        "action_hint": r["action_hint"],
        "message": r["message"],
        "received_at": _aware(r["received_at"]),
    }


_COLUMNS = ("id, ticker, exchange, bar_interval, price, alert_time, name, "
            "action_hint, message, received_at")


def _price(p: float | None) -> str:
    return "price not stated" if p is None else f"${p:,.2f}"


def _fact(a: dict[str, Any]) -> str:
    """One alert's facts, composed by code from validated fields only."""
    bits = [a["ticker"]]
    if a["exchange"]:
        bits.append(f"on {a['exchange']}")
    if a["interval"]:
        bits.append(f"({a['interval']} chart)")
    bits.append(_price(a["price"]))
    when = a["alert_time"]
    if when is not None:
        bits.append(f"at {when:%a %d %b %H:%M} UTC (TradingView's time)")
    if a["action_hint"]:
        bits.append(f"TradingView's word: {a['action_hint']}")
    bits.append(f"received {a['received_at']:%a %d %b %H:%M} UTC")
    return " ".join(bits)


async def check_tradingview() -> Observation:
    now = _now()
    if not configured():
        why = not_configured_reason()
        _state.clear()
        _state.update(at=now, error=why)
        return Observation(KIND, Health.UNKNOWN,
                           detail=f"{why}, so no TradingView alert can arrive")
    from ...kernel.state import fetch_all, fetch_one
    stale = stale_after()
    midnight = local_midnight(now)
    try:
        head = await fetch_one(
            """SELECT max(received_at) AS last,
                      count(*) FILTER (WHERE received_at >= $1::timestamptz) AS today
                 FROM tradingview_alerts""", midnight)
        latest = await fetch_all(
            f"""SELECT {_COLUMNS} FROM (
                    SELECT DISTINCT ON (ticker) {_COLUMNS}
                      FROM tradingview_alerts
                     WHERE received_at >= $1::timestamptz
                     ORDER BY ticker, received_at DESC, id DESC) t
                 ORDER BY received_at DESC, id DESC LIMIT 10""", now - stale)
        recent = await fetch_all(
            f"""SELECT {_COLUMNS} FROM tradingview_alerts
                 WHERE received_at >= $1::timestamptz
                 ORDER BY received_at DESC, id DESC LIMIT {BRIEF_ALERTS}""",
            now - stale)
        own, own_at = await _own_readings(now)
    except Exception as exc:                            # noqa: BLE001
        why = f"tradingview_alerts unreadable ({type(exc).__name__})"
        _state.clear()
        _state.update(at=now, error=why)
        return Observation(KIND, Health.UNKNOWN, detail=why)

    last = _aware(head["last"]) if head else None
    today = int(head["today"] or 0) if head else 0
    hours = stale.total_seconds() / 3600
    per_ticker = [_row(r) for r in latest]
    newest = [_row(r) for r in recent]
    _state.clear()
    _state.update(at=now, error=None, last=last, today=today, recent=newest,
                  own=own, own_at=own_at, stale_hours=hours)
    if last is None:
        return Observation(KIND, Health.UNKNOWN,
                           detail="no TradingView alert received yet")
    if now - last > stale:
        return Observation(
            KIND, Health.UNKNOWN,
            detail=(f"no TradingView alert in the last {hours:g} h (last received "
                    f"{last:%a %d %b %H:%M} UTC); a quiet chart and a broken "
                    f"alert look the same from here"))
    detail = (
        f"last alert received {last:%a %d %b %H:%M} UTC; {today} since local "
        f"midnight ({midnight:%a %d %b %H:%M} UTC); latest per ticker: "
        + "; ".join(_fact(a) for a in per_ticker)
        + f". {FOOTER} (read {now:%H:%M} UTC)")
    return Observation(
        KIND, Health.OK, float(today), "alerts", detail,
        evidence={
            "last_received_at": last.isoformat(), "today": today,
            "today_since": midnight.isoformat(), "read_at": now.isoformat(),
            "latest": {a["ticker"]: {
                "price": a["price"], "exchange": a["exchange"],
                "interval": a["interval"], "action_hint": a["action_hint"],
                "alert_time": a["alert_time"].isoformat() if a["alert_time"] else None,
                "received_at": a["received_at"].isoformat(),
            } for a in per_ticker},
        })


# ----------------------------------------------------------------------- brief

def _against_own(a: dict[str, Any], own: dict[str, tuple[float, datetime]],
                 own_at: datetime | None) -> str:
    sym = bare_symbol(a["ticker"])
    mine = own.get(sym)
    if mine is None or own_at is None:
        return f"no own reading of {sym} to compare"
    price, price_at = mine
    return (f"own reading {sym} ${price:,.2f} (price as of "
            f"{price_at:%a %d %b %H:%M} UTC, read {own_at:%a %d %b %H:%M} UTC)")


def brief_line() -> str:
    """The TradingView part of Ivan's brief, from the check's last reading.
    Everything outside the wrapped block is composed by code from validated
    fields; the alerts' own words (name, message) are inside it. Nothing
    before the check has run."""
    at: datetime | None = _state.get("at")
    if at is None:
        return ""
    if _state.get("error"):
        return f"TradingView: unknown -- {_state['error']} (read {at:%H:%M} UTC)"
    recent: list[dict[str, Any]] = _state.get("recent") or []
    if not recent:
        return (f"TradingView: no alert in the last {_state['stale_hours']:g} h "
                f"(read {at:%H:%M} UTC)")
    own, own_at = _state.get("own") or {}, _state.get("own_at")
    facts = "; ".join(f"#{a['id']} {_fact(a)}, {_against_own(a, own, own_at)}"
                      for a in recent)
    words = [{"alert": a["id"], "ticker": a["ticker"], "name": a["name"],
              "message": a["message"]}
             for a in recent if a["name"] or a["message"]]
    from ...kernel.permissions import wrap_untrusted
    block = ("\n" + wrap_untrusted(json.dumps(words, ensure_ascii=False), SOURCE)
             if words else "")
    return (f"TradingView (the owner's own chart alerts; not advice, never traded on): "
            f"{_state['today']} today; latest: {facts} (read {at:%H:%M} UTC){block}")


# -------------------------------------------------------- the alert pipeline

# Slack reads `<...>` as a link or a mention; the alert's name is the owner's
# own text but still outside text, so it is shown, never interpreted.
_SLACK_ANGLE = str.maketrans({"<": "\u2039", ">": "\u203a"})


def _shown_name(name: str | None) -> str:
    """The alert's name as the Slack text states it. A name carrying one of
    the pipeline's advice words ("buy zone") is left out rather than sent:
    the ban is on the text, and the name is part of the text."""
    from .alerts import advice_in
    if not name:
        return "(no name)"
    if advice_in(name):
        return "(name left out: it carries an advice word)"
    return "\u201c" + name.translate(_SLACK_ANGLE) + "\u201d"


def _event(a: dict[str, Any], own: dict[str, tuple[float, datetime]],
           own_at: datetime | None) -> dict[str, Any]:
    where = f" on {a['exchange']}" if a["exchange"] else ""
    chart = f", {a['interval']} chart" if a["interval"] else ""
    when = (f" at {a['alert_time']:%a %d %b %H:%M} UTC (TradingView's time)"
            if a["alert_time"] else ", time not stated")
    sym = bare_symbol(a["ticker"])
    mine = own.get(sym)
    if mine is not None and own_at is not None:
        price, price_at = mine
        versus = (f"Ivan's own latest {sym}: ${price:,.2f} (price as of "
                  f"{price_at:%a %d %b %H:%M} UTC, read {own_at:%a %d %b %H:%M} UTC).")
    else:
        versus = f"Ivan has no fresh reading of {sym} of his own."
    return {
        "key": f"{KIND}:{a['id']}",
        "kind": KIND,
        "ticker": a["ticker"],
        "title": f"{a['ticker']} TradingView alert",
        "body": (f"TradingView alert {_shown_name(a['name'])} on {a['ticker']}"
                 f"{where}{chart}: TradingView's price "
                 f"{_price(a['price'])}{when}; received by ATLAS "
                 f"{a['received_at']:%a %d %b %H:%M} UTC. {versus} {FOOTER}"),
        "at": a["received_at"],
        "id": a["id"], "price": a["price"], "alert_time": a["alert_time"],
        "untrusted": {"name": a["name"], "action_hint": a["action_hint"],
                      "message": a["message"]},
    }


EVENT_FIELDS = ("key", "kind", "ticker", "title", "body", "at")


async def pending_tradingview_events(now: datetime | None = None,
                                     limit: int = 20) -> list[dict[str, Any]]:
    """Stored alerts not yet notified, oldest first, for the markets.alert
    pipeline: `alerts.gather` turns each into an `alerts.Event` (the
    EVENT_FIELDS), `alerts.plan` records it once in `markets_alerts`, the
    kernel's alert tick sends it through `executor.execute` as
    `markets.alert`, and `alerts.alert` marks it notified once delivered.
    Reads only: nothing is marked here.

        key       "tradingview:<row id>" -- stable, one per stored alert
        kind      "tradingview"
        ticker    the validated ticker ("AAPL", "NASDAQ:AAPL", "ES1!")
        title     composed by code, e.g. "AAPL TradingView alert"
        body      composed by code: the alert's name (left out when it
                  carries an advice word), the ticker, exchange, interval,
                  TradingView's price and time, when ATLAS received it,
                  and Ivan's own latest price of the stock with its times
                  when he has a fresh one; then FOOTER
        at        when ATLAS received it (aware UTC) -> Event.at / event_at
        untrusted {"name", "action_hint", "message"} -- the alert's own
                  words. Never in a prompt without wrap_untrusted(); the
                  hint and the message never go in the text: "buy"/"sell"
                  are in the pipeline's ADVICE_WORDS, and `gather` drops,
                  and the dry run refuses, any alert whose text carries one.

    Until it is delivered a row stays pending and is offered again each
    tick; `markets_alerts.key` keeps it to one alert. Only alerts received
    inside PENDING_MAX_AGE (24 h, the pipeline's own MAX_AGE) are returned:
    an older one is never sent late."""
    from ...kernel.state import fetch_all
    now = now or _now()
    rows = await fetch_all(
        f"""SELECT {_COLUMNS} FROM tradingview_alerts
             WHERE notified_at IS NULL AND received_at >= $1::timestamptz
             ORDER BY received_at, id LIMIT $2""",
        now - PENDING_MAX_AGE, max(1, min(int(limit), 200)))
    if not rows:
        return []
    own, own_at = await _own_readings(now)
    return [_event(_row(r), own, own_at) for r in rows]


async def mark_tradingview_notified(keys: list[str]) -> int:
    """Mark the events with these keys ("tradingview:<id>") notified; the
    number marked. Called by `alerts.alert` once the alert is delivered.
    A key that is not ours is ignored; an already-marked row is left as it
    was."""
    from ...kernel.state import fetch_all
    ids = []
    for key in keys:
        kind, _, raw = str(key).partition(":")
        if kind == KIND and raw.isdigit():
            ids.append(int(raw))
    if not ids:
        return 0
    rows = await fetch_all(
        """UPDATE tradingview_alerts SET notified_at = now()
            WHERE id = ANY($1::bigint[]) AND notified_at IS NULL
           RETURNING id""", ids)
    return len(rows)
