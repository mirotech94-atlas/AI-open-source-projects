"""The Claude playlist (paper): the Alinea "Playlist Pro" loop, on paper.

Miro, 2026-09-29, after the Alinea ad ("open Alinea -> Playlist Pro -> the
Claude portfolio -> deposit $100 -> AI researches and the Alinea team
reviews and approves every move weekly"): "use this for the trading as
well, follow the steps in the video". The same loop, with no money in it:

1. **Weekly proposal** (`propose_week`, Sunday 15:00 America/New_York, from
   the scheduler's `playlist` job, missed-run policy run-once). A model --
   the REASON route, which is Claude first -- proposes target weights over
   the WATCHLIST minus VOO (VOO is the benchmark). It sees only what ATLAS
   read itself from the public chart feed: last close, 4- and 12-week
   returns and 20-day volatility per ticker, with the read time, wrapped as
   untrusted data. The answer is checked strictly (`parse_answer`): one bare
   JSON object, watchlist tickers only, weights >= 0, at most MAX_WEIGHT
   each, at most MAX_HOLDINGS held, summing to exactly 100.0 after rounding
   to 0.1, and one short reason per changed ticker citing figures it was
   given. A bad answer is recorded as rejected -- never fixed up. No model
   answering means nothing is proposed that week, and the report says so.
2. **Human approval**. A valid proposal that moves any ticker by SAME_WITHIN
   points or more becomes an approval card for `markets.paper_rebalance`
   (Tier 2, card_only: proposed only from here, never offered to a model).
   The card shows before -> after, the reasons, and PAPER_ONLY. Declined or
   expired, nothing changes.
3. **The rebalance** runs through the executor like every action: it reads
   fresh closes, values the paper holdings, and buys the approved weights
   in fractional shares with no fees, recording the prices and their read
   time. A price that cannot be read refuses the whole rebalance -- nothing
   is ever valued at 0. Its rollback restores the previous weights at fresh
   closes; `readback.py` confirms the holdings match what was approved.

It starts with $100 split equally across the non-VOO tickers on its first
run, and is compared (`playlist_section`, in the weekly report) with the
fixed equal-split basket (`markets_paper`, the control, untouched here) and
with $100 in VOO, all three from the same start and at plain closes.

Nothing here can trade: the action writes only ATLAS's own tables, and its
only network use is reading public prices. Money out is Tier 3, permanently
denied. Nothing here is advice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult, Severity
from . import BENCHMARK_SYMBOL, closes, watchlist

log = logging.getLogger("atlas.markets.playlist")

NAME = "Claude playlist (paper)"
ACTION = "markets.paper_rebalance"
PAPER_ONLY = "Paper only — no real money moves; not advice."
START_DOLLARS = 100.0
TZ = ZoneInfo("America/New_York")
MAX_WEIGHT = 15            # percent, per ticker
MAX_HOLDINGS = 12
SAME_WITHIN = 0.5          # points: a proposal this close to today's weights is no change
REASON_MAX = 160
HISTORY_DAYS = 130         # enough closes for a 12-week return
LATEST_DAYS = 10           # how far back "the latest close" may be
GAP_DAYS = 7               # a return's base close may be this far before its date
_TENTH = Decimal("0.1")

SYSTEM = f"""You manage a PAPER stock playlist called the Claude playlist. No \
real money moves, and nothing you write is advice to anyone. Once a week you \
propose its target weights over the tickers in the data block, using ONLY the \
figures in that block (last close, 4-week and 12-week returns, 20-day \
volatility, current weight).

Rules:
- Only tickers from the data block. Tickers you leave out are 0.
- Weights are percentages: at least 0, at most {MAX_WEIGHT} each, at most \
{MAX_HOLDINGS} tickers above 0, one decimal place, summing to exactly 100.0.
- For every ticker whose weight changes from its current weight, one short \
reason (one line, under {REASON_MAX} characters) that cites at least one \
figure exactly as the data block shows it, and no other numbers.
- Keeping the current weights is a valid answer when the figures do not \
justify a change.

Answer with one JSON object and nothing else -- no prose, no code fence:
{{"weights": {{"TICKER": 12.5, ...}}, "reasons": {{"TICKER": "..."}}}}"""


def propose_day() -> int:
    """Weekday of the proposal, Monday 0. Sunday by default."""
    try:
        return max(0, min(6, int(os.getenv("PLAYLIST_DAY", "6"))))
    except ValueError:
        return 6


def propose_hour() -> int:
    """Hour of the proposal, America/New_York. 15:00 by default."""
    try:
        return max(0, min(23, int(os.getenv("PLAYLIST_HOUR", "15"))))
    except ValueError:
        return 15


def eligible() -> tuple[str, ...]:
    """What the playlist may hold: the watchlist without the benchmark."""
    return tuple(s for s in watchlist() if s != BENCHMARK_SYMBOL)


def _json(value: Any) -> Any:
    """A JSONB column as asyncpg hands it back (a str, no codec)."""
    return json.loads(value) if isinstance(value, str) else value


def _money(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"${value:,.2f} ({(value / START_DOLLARS - 1) * 100:+.1f}%)"


# ---------------------------------------------------------------- prices

async def read_history(syms: Iterable[str], days: int
                       ) -> tuple[dict[str, dict[date, float]], datetime]:
    """Each symbol's daily closes over the last `days`, from the markets
    module's reader, and when they were read. An unreadable symbol is
    absent -- never an empty series standing in for a price."""
    syms = list(dict.fromkeys(syms))
    end = datetime.now(UTC).date()
    got = await asyncio.gather(*(closes(s, end - timedelta(days=days), end)
                                 for s in syms))
    read_at = datetime.now(UTC)
    return {s: g for s, g in zip(syms, got, strict=True) if g}, read_at


@dataclass(frozen=True)
class Latest:
    prices: dict[str, float]
    days: dict[str, date]
    read_at: datetime
    missing: list[str]


async def latest(syms: Iterable[str]) -> Latest:
    """The latest close of each symbol, with its date and the read time."""
    syms = list(dict.fromkeys(syms))
    series, read_at = await read_history(syms, LATEST_DAYS)
    prices: dict[str, float] = {}
    days: dict[str, date] = {}
    for s in syms:
        got = series.get(s)
        if got:
            day = max(got)
            prices[s], days[s] = got[day], day
    return Latest(prices, days, read_at, [s for s in syms if s not in prices])


@dataclass(frozen=True)
class Stat:
    last: float
    day: date
    r4: float | None          # percent
    r12: float | None
    vol20: float | None       # percent, annualised


def stats_of(series: dict[date, float]) -> Stat | None:
    """Last close, 4- and 12-week returns, 20-day volatility. A figure the
    closes cannot support is None (unknown), never 0."""
    if not series:
        return None
    days = sorted(series)
    last_day = days[-1]
    last = series[last_day]

    def back(n: int) -> float | None:
        cut = last_day - timedelta(days=n)
        prior = [d for d in days if d <= cut]
        if not prior or (cut - prior[-1]).days > GAP_DAYS or series[prior[-1]] <= 0:
            return None
        return (last / series[prior[-1]] - 1) * 100

    vol = None
    tail = days[-21:]
    if len(tail) == 21 and all(series[d] > 0 for d in tail):
        rets = [series[b] / series[a] - 1 for a, b in pairwise(tail)]
        vol = statistics.stdev(rets) * math.sqrt(252) * 100
    return Stat(last, last_day, back(28), back(84), vol)


def _pct(value: float | None) -> str:
    return "unknown" if value is None else f"{value:+.1f}"


def render_table(stats: dict[str, Stat], current: dict[str, float],
                 read_at: datetime) -> str:
    """The data block the model is given: every figure it may cite."""
    rows = [(f"Closes read {read_at:%Y-%m-%d %H:%M} UTC from the public chart "
             f"feed (price only)."),
            ("ticker | last close (date) | 4w return % | 12w return % | "
             "20d volatility % (annualised) | current weight %")]
    for t, s in stats.items():
        vol = "unknown" if s.vol20 is None else f"{s.vol20:.1f}"
        rows.append(f"{t} | {s.last:.2f} ({s.day.isoformat()}) | {_pct(s.r4)} | "
                    f"{_pct(s.r12)} | {vol} | {current.get(t, 0.0):.1f}")
    return "\n".join(rows)


# ------------------------------------------------------------ validation

class Invalid(ValueError):
    """The model's answer broke a rule. Recorded as rejected, never fixed."""


_SPAN = re.compile(r"\b\d+\s*-?\s*(?:d|w|wk|wks|day|days|week|weeks|month|months)\b",
                   re.IGNORECASE)
_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?(?![0-9]*[A-Za-z])")
_TABLE_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def figures_in(text: str) -> list[str]:
    """Number-shaped tokens a reason cites: "4w", "20-day" and dates are
    labels, not figures."""
    text = _ISO.sub(" ", _SPAN.sub(" ", text))
    return _NUMBER.findall(text)


def _cited(token: str, allowed: Sequence[float]) -> bool:
    """Is `token` one of the figures given, at the precision it is written?"""
    places = len(token.split(".", 1)[1]) if "." in token else 0
    n = float(token)
    return any(abs(round(abs(x), places) - n) < 1e-9 for x in allowed)


def _tenths(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Invalid(f"{where} is not a number")
    if not math.isfinite(value):
        raise Invalid(f"{where} is not finite")
    if value < 0:
        raise Invalid(f"{where} is negative")
    return int(Decimal(repr(value)).quantize(_TENTH, ROUND_HALF_UP) / _TENTH)


def check_weights(weights: object, tickers: Sequence[str]) -> dict[str, float]:
    """Target weights (only those above 0), or Invalid. Pure."""
    if not isinstance(weights, dict) or not weights:
        raise Invalid("weights is not a non-empty object")
    tenths: dict[str, int] = {}
    for t, w in weights.items():
        if t not in tickers:
            raise Invalid(f"{t!r} is not a playlist ticker")
        tenths[t] = _tenths(w, f"the weight of {t}")
        if tenths[t] > MAX_WEIGHT * 10:
            raise Invalid(f"{t} is {tenths[t] / 10:.1f}%, over the {MAX_WEIGHT}% cap")
    total = sum(tenths.values())
    if total != 1000:
        raise Invalid(f"the weights sum to {total / 10:.1f}%, not 100.0%")
    held = {t: n / 10 for t, n in tenths.items() if n > 0}
    if len(held) > MAX_HOLDINGS:
        raise Invalid(f"{len(held)} holdings, over the {MAX_HOLDINGS} cap")
    return held


def changed(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """Tickers whose weight moves at 0.1-point resolution."""
    return [t for t in sorted(set(before) | set(after))
            if round(before.get(t, 0.0), 1) != round(after.get(t, 0.0), 1)]


def parse_answer(text: str, tickers: Sequence[str], current: dict[str, float],
                 table: str) -> tuple[dict[str, float], dict[str, str]]:
    """The model's answer, checked. Returns (weights above 0, reasons).
    Anything outside the rules raises Invalid -- nothing is repaired."""
    raw = (text or "").strip()
    if not (raw.startswith("{") and raw.endswith("}")):
        raise Invalid("the answer is not one bare JSON object")

    def constant(name: str) -> None:
        raise Invalid(f"the answer holds {name}")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [k for k, _ in items]
        if len(keys) != len(set(keys)):
            raise Invalid("the answer repeats a key")
        return dict(items)

    try:
        obj = json.loads(raw, parse_constant=constant, object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise Invalid(f"the answer is not valid JSON ({exc.msg})") from None
    if not isinstance(obj, dict) or set(obj) != {"weights", "reasons"}:
        raise Invalid("the answer must have exactly 'weights' and 'reasons'")
    weights = check_weights(obj["weights"], tickers)
    reasons = obj["reasons"]
    if not isinstance(reasons, dict):
        raise Invalid("reasons is not an object")
    allowed = [float(x) for x in _TABLE_NUMBER.findall(_ISO.sub(" ", table))]
    allowed += list(weights.values())
    out: dict[str, str] = {}
    for t, why in reasons.items():
        if t not in tickers:
            raise Invalid(f"a reason names {t!r}, not a playlist ticker")
        if not isinstance(why, str) or not why.strip():
            raise Invalid(f"the reason for {t} is empty")
        why = why.strip()
        if "\n" in why or len(why) > REASON_MAX:
            raise Invalid(f"the reason for {t} is not one short line")
        cited = figures_in(why)
        if not cited:
            raise Invalid(f"the reason for {t} cites no figure")
        stray = [n for n in cited if not _cited(n, allowed)]
        if stray:
            raise Invalid(f"the reason for {t} cites {', '.join(stray)}, "
                          f"not in the data given")
        out[t] = why
    missing = [t for t in changed(current, weights) if t not in out]
    if missing:
        raise Invalid(f"no reason given for the change to {', '.join(missing)}")
    return weights, out


def same(before: dict[str, float], after: dict[str, float],
         within: float = SAME_WITHIN) -> bool:
    return all(abs(before.get(t, 0.0) - after.get(t, 0.0)) < within
               for t in set(before) | set(after))


def vet(params: dict[str, Any]) -> str | None:
    """Pure refusal of a payload that could only fail: the same weight
    rules the answer passed, asked again of what the card will carry."""
    try:
        after = params.get("after")
        check_weights(after, eligible())
        if any(abs(float(v) * 10 - round(float(v) * 10)) > 1e-6
               for v in after.values()):
            return "the target weights are not rounded to 0.1"
    except Invalid as exc:
        return str(exc)
    except (TypeError, ValueError):
        return "the target weights are not numbers"
    if not isinstance(params.get("before"), dict):
        return "no current weights to compare with"
    if not isinstance(params.get("reasons"), dict):
        return "no reasons"
    return None


# ------------------------------------------------------------- holdings

@dataclass(frozen=True)
class Snapshot:
    id: int
    at: datetime
    kind: str
    proposal_id: int | None
    weights: dict[str, float]
    shares: dict[str, float]
    prices: dict[str, float]
    price_days: dict[str, str]
    prices_read_at: datetime
    value: float


_SNAP = ("SELECT id, at, kind, proposal_id, weights, shares, prices, price_days, "
         "prices_read_at, value FROM markets_playlist_holdings")


def _snap(row: Any) -> Snapshot:
    return Snapshot(
        int(row["id"]), row["at"], str(row["kind"]), row["proposal_id"],
        {k: float(v) for k, v in _json(row["weights"]).items()},
        {k: float(v) for k, v in _json(row["shares"]).items()},
        {k: float(v) for k, v in _json(row["prices"]).items()},
        dict(_json(row["price_days"])), row["prices_read_at"], float(row["value"]))


async def current() -> Snapshot | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(_SNAP + " ORDER BY id DESC LIMIT 1")
    return _snap(row) if row else None


async def first() -> Snapshot | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(_SNAP + " WHERE kind = 'start' ORDER BY id LIMIT 1")
    return _snap(row) if row else None


async def snapshot(sid: int) -> Snapshot | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(_SNAP + " WHERE id = $1", sid)
    return _snap(row) if row else None


async def _insert(kind: str, proposal_id: int | None, weights: dict[str, float],
                  shares: dict[str, float], got: Latest, value: float) -> int | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """INSERT INTO markets_playlist_holdings
             (kind, proposal_id, weights, shares, prices, price_days,
              prices_read_at, value)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (proposal_id) WHERE kind = 'rebalance' DO NOTHING
           RETURNING id""",
        kind, proposal_id, json.dumps(weights), json.dumps(shares),
        json.dumps(got.prices), json.dumps({k: d.isoformat() for k, d in got.days.items()}),
        got.read_at, value)
    return int(row["id"]) if row else None


def buy(value: float, weights: dict[str, float], prices: dict[str, float]
        ) -> dict[str, float]:
    """Fractional shares, no fees: each ticker's weight of `value`."""
    return {t: value * w / 100 / prices[t] for t, w in weights.items() if w > 0}


async def start() -> tuple[Snapshot | None, str]:
    """$100 split equally across the eligible tickers, at closes read now.
    Only from a complete read -- VOO included, since it is the benchmark's
    start price."""
    got = await latest(watchlist())
    if got.missing:
        return None, f"closes unreadable for {', '.join(got.missing)}"
    tickers = eligible()
    weights = {t: 100 / len(tickers) for t in tickers}
    sid = await _insert("start", None, weights,
                        buy(START_DOLLARS, weights, got.prices), got, START_DOLLARS)
    return (await snapshot(sid) if sid else None), ""


# ------------------------------------------------------------- proposal

def week_of(now: datetime) -> date:
    """The proposal day on or before `now`, in New York."""
    local = now.astimezone(TZ).date()
    return local - timedelta(days=(local.weekday() - propose_day()) % 7)


async def _record(week: date, status: str, *, why: str = "", model: str | None = None,
                  weights: dict | None = None, before: dict | None = None,
                  reasons: dict | None = None, stats: dict | None = None) -> int | None:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """INSERT INTO markets_playlist_proposals
             (week, status, why, model, weights, before, reasons, stats)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (week) DO NOTHING RETURNING id""",
        week, status, why, model,
        json.dumps(weights) if weights is not None else None,
        json.dumps(before) if before is not None else None,
        json.dumps(reasons) if reasons is not None else None,
        json.dumps(stats or {}, default=str))
    return int(row["id"]) if row else None


def card_text(params: dict[str, Any]) -> str:
    """What the card carries: before -> after, the reasons, paper only.
    Pure, so `approvals.propose` can show exactly what is approved."""
    before = {k: float(v) for k, v in (params.get("before") or {}).items()}
    after = {k: float(v) for k, v in (params.get("after") or {}).items()}
    reasons = params.get("reasons") or {}
    moves = changed(before, after)
    moves.sort(key=lambda t: -abs(after.get(t, 0.0) - before.get(t, 0.0)))
    lines = [f"• {t} {before.get(t, 0.0):.1f}% → {after.get(t, 0.0):.1f}%"
             + (f" — {reasons[t]}" if reasons.get(t) else "") for t in moves]
    kept = sorted(t for t in after if t not in moves)
    if kept:
        lines.append("• unchanged: " + ", ".join(f"{t} {after[t]:.1f}%" for t in kept))
    return "\n".join(lines + [PAPER_ONLY])


def _claude(model: str) -> bool:
    return model.startswith("anthropic:")


async def propose_week(week: date | None = None) -> str:
    """This week's proposal: recorded, and carded when it changes anything.
    Returns the recorded status. Once per week: a second call finds the
    week's row and does nothing."""
    from ...kernel.state import fetch_one
    week = week or week_of(datetime.now(UTC))
    if await fetch_one("SELECT id FROM markets_playlist_proposals WHERE week = $1",
                       week):
        return "exists"
    holding = await current()
    if holding is None:
        holding, why = await start()
        if holding is None:
            await _record(week, "no_prices", why=f"the playlist could not start: {why}")
            return "no_prices"
    tickers = eligible()
    series, read_at = await read_history(tickers, HISTORY_DAYS)
    stats = {t: stats_of(series.get(t) or {}) for t in tickers}
    missing = [t for t, s in stats.items() if s is None]
    if missing:
        await _record(week, "no_prices", why=f"closes unreadable for {', '.join(missing)}")
        return "no_prices"
    good: dict[str, Stat] = {t: s for t, s in stats.items() if s is not None}
    before = {t: round(w, 2) for t, w in holding.weights.items()}
    table = render_table(good, before, read_at)
    stored = {"read_at": read_at.isoformat(),
              "tickers": {t: {"last": s.last, "day": s.day.isoformat(), "r4": s.r4,
                              "r12": s.r12, "vol20": s.vol20} for t, s in good.items()}}

    from ...kernel.llm import Message, Need, think
    from ...kernel.permissions import wrap_untrusted
    prompt = (wrap_untrusted(table, "public chart feed (Yahoo), read by ATLAS")
              + "\n\nPropose this week's target weights now, as the JSON object.")
    try:
        reply = await think(Need.REASON, SYSTEM, [Message("user", prompt)],
                            max_tokens=1500)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        await _record(week, "no_model", before=before, stats=stored,
                      why=f"no model answered ({explain(exc)}); nothing proposed")
        return "no_model"
    model = f"{reply.provider}:{reply.model}"
    try:
        weights, reasons = parse_answer(reply.text, tickers, before, table)
    except Invalid as exc:
        await _record(week, "invalid", why=str(exc), model=model, before=before,
                      stats=stored)
        return "invalid"
    if same(before, weights):
        await _record(week, "unchanged", model=model, weights=weights, before=before,
                      reasons=reasons, stats=stored)
        return "unchanged"
    pid = await _record(week, "carded", model=model, weights=weights, before=before,
                        reasons=reasons, stats=stored)
    if pid is None:
        return "exists"
    params = {"proposal_id": pid, "before": before, "after": weights,
              "reasons": reasons}
    who = (f"proposed by {model}" if _claude(model) else
           f"proposed by {model} (not Claude this week)")
    reason = (f"{NAME}, week of {week:%-d %b}: {len(changed(before, weights))} "
              f"change(s), {who}. Prices read {read_at:%a %-d %b %H:%M} UTC. "
              f"{PAPER_ONLY}")
    from ...kernel import approvals
    from ...kernel.state import execute_sql
    try:
        card = await approvals.propose(
            ACTION, params, reason, severity=Severity.INFO,
            evidence={"model": model, "prices_read_at": read_at.isoformat()},
            rollback_plan="restores the previous paper weights",
            dedup_key=f"markets.playlist:{week.isoformat()}", automatic=True)
    except Exception as exc:                            # noqa: BLE001
        await execute_sql(
            "UPDATE markets_playlist_proposals SET status = 'refused', why = $2 "
            "WHERE id = $1", pid, f"the card could not be raised: {exc}")
        return "refused"
    await execute_sql("UPDATE markets_playlist_proposals SET card_id = $2 WHERE id = $1",
                      pid, card)
    return "carded"


# ------------------------------------------------------------ the action

async def _proposal(pid: int) -> Any:
    from ...kernel.state import fetch_one
    return await fetch_one(
        "SELECT id, week, status, weights FROM markets_playlist_proposals WHERE id = $1",
        pid)


async def _preflight(proposal_id: int, before: dict, after: dict, reasons: dict
                     ) -> tuple[str | None, Snapshot | None, int | None]:
    """(why not, current holdings, an existing rebalance snapshot id)."""
    why = vet({"before": before, "after": after, "reasons": reasons})
    if why:
        return why, None, None
    row = await _proposal(int(proposal_id))
    if row is None:
        return f"no playlist proposal #{proposal_id}", None, None
    stored = {k: float(v) for k, v in (_json(row["weights"]) or {}).items()}
    after = {k: float(v) for k, v in after.items()}
    if stored != after:
        return f"the weights differ from proposal #{proposal_id}", None, None
    from ...kernel.state import fetch_one
    done = await fetch_one(
        "SELECT id FROM markets_playlist_holdings WHERE kind = 'rebalance' "
        "AND proposal_id = $1", int(proposal_id))
    if done is not None:
        return None, None, int(done["id"])
    cur = await current()
    if cur is None:
        return "the playlist has no holdings yet", None, None
    if not same(cur.weights, {k: float(v) for k, v in before.items()}, 0.01):
        return "the paper holdings changed since the card was raised", None, None
    return None, cur, None


async def rebalance_dry(proposal_id: int, before: dict, after: dict,
                        reasons: dict) -> ActionResult:
    why, cur, done = await _preflight(proposal_id, before, after, reasons)
    if why:
        return ActionResult(False, why)
    if done is not None:
        return ActionResult(True, f"proposal #{proposal_id} is already applied "
                                  f"(snapshot #{done}); nothing would change")
    moves = changed(cur.weights, after)
    return ActionResult(True, (
        f"would rebalance the {NAME} at the latest closes: "
        + ", ".join(f"{t} {cur.weights.get(t, 0.0):.1f}→{float(after.get(t, 0)):.1f}%"
                    for t in moves)
        + ". Paper only; nothing real moves."))


async def rebalance(proposal_id: int, before: dict, after: dict,
                    reasons: dict) -> ActionResult:
    """Rebalance the paper holdings to the approved weights at fresh closes.
    Writes ATLAS's own tables only; reads public prices, never sends."""
    why, cur, done = await _preflight(proposal_id, before, after, reasons)
    if why:
        return ActionResult(False, why)
    if done is not None:
        return ActionResult(True, f"proposal #{proposal_id} was already applied",
                            evidence={"snapshot_id": done},
                            rollback_token=None)
    after = {k: float(v) for k, v in after.items() if float(v) > 0}
    got = await latest(set(cur.shares) | set(after))
    if got.missing:
        return ActionResult(False, (
            f"closes unreadable for {', '.join(sorted(got.missing))}: nothing "
            f"changed (a price is unknown, never 0)"))
    value = sum(n * got.prices[t] for t, n in cur.shares.items())
    sid = await _insert("rebalance", int(proposal_id), after,
                        buy(value, after, got.prices), got, value)
    if sid is None:
        return ActionResult(False, f"proposal #{proposal_id} was applied concurrently")
    from ...kernel.state import execute_sql
    await execute_sql("UPDATE markets_playlist_proposals SET status = 'applied' "
                      "WHERE id = $1", int(proposal_id))
    newest = max(got.days.values())
    return ActionResult(
        True, (f"{NAME} rebalanced: ${value:,.2f} at closes of {newest:%-d %b}, "
               f"read {got.read_at:%H:%M} UTC. Paper only."),
        evidence={"snapshot_id": sid, "value": round(value, 4),
                  "prices_read_at": got.read_at.isoformat(),
                  "prices": got.prices},
        rollback_token=f"{cur.id}:{sid}")


async def rebalance_rollback(token: str) -> ActionResult:
    """Put the previous weights back, at fresh closes, so no value appears
    or vanishes. Refused when a later change sits on top of this one."""
    try:
        prev_id, new_id = (int(x) for x in token.split(":"))
    except ValueError:
        return ActionResult(False, f"not a playlist rollback token: {token!r}")
    cur = await current()
    if cur is None or cur.id != new_id:
        return ActionResult(False, f"snapshot #{new_id} is no longer the latest; "
                                   f"nothing restored")
    prev = await snapshot(prev_id)
    if prev is None:
        return ActionResult(False, f"snapshot #{prev_id} is missing; nothing restored")
    got = await latest(set(cur.shares) | set(prev.weights))
    if got.missing:
        return ActionResult(False, (
            f"closes unreadable for {', '.join(sorted(got.missing))}: nothing "
            f"restored (a price is unknown, never 0)"))
    value = sum(n * got.prices[t] for t, n in cur.shares.items())
    sid = await _insert("rollback", cur.proposal_id, prev.weights,
                        buy(value, prev.weights, got.prices), got, value)
    from ...kernel.state import execute_sql
    if cur.proposal_id is not None:
        await execute_sql("UPDATE markets_playlist_proposals SET status = "
                          "'rolled_back' WHERE id = $1", cur.proposal_id)
    return ActionResult(True, f"{NAME}: previous weights restored (snapshot #{sid})",
                        evidence={"snapshot_id": sid})


def action():
    from ...kernel.contracts import Action, Tier
    return Action(
        name=ACTION, tier=Tier.ASK,
        summary=f"Rebalance the {NAME} to this week's approved weights (paper only)",
        run=rebalance, dry_run=rebalance_dry, rollback=rebalance_rollback,
        params={"proposal_id": "the weekly playlist proposal's id",
                "before": "the current target weights, percent by ticker",
                "after": "the proposed target weights, percent by ticker",
                "reasons": "one short reason per changed ticker"},
        max_per_hour=2, card_only=True, vet=vet, card_detail=card_text)


# --------------------------------------------------------------- report

async def _this_week(now: datetime) -> tuple[str, dict | None]:
    """How this week's proposal went, in words, and its row."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT p.id, p.week, p.status, p.why, p.model, p.weights, p.before,
                  p.reasons, p.card_id, c.status AS card_status
             FROM markets_playlist_proposals p
             LEFT JOIN proposals c ON c.id = p.card_id
            WHERE p.week > $1 ORDER BY p.week DESC LIMIT 1""",
        (now.astimezone(TZ) - timedelta(days=7)).date())
    if row is None:
        return "nothing proposed yet this week", None
    r = dict(row)
    before = {k: float(v) for k, v in (_json(r["before"]) or {}).items()}
    after = {k: float(v) for k, v in (_json(r["weights"]) or {}).items()}
    moves = changed(before, after)
    moves.sort(key=lambda t: -abs(after.get(t, 0.0) - before.get(t, 0.0)))
    text = ", ".join(f"{t} {before.get(t, 0.0):.1f}→{after.get(t, 0.0):.1f}%"
                     for t in moves[:5]) + (f" (+{len(moves) - 5} more)"
                                            if len(moves) > 5 else "")
    status, card = r["status"], r["card_status"]
    words = {
        "no_model": "no model answered, so nothing was proposed",
        "invalid": f"the model's answer failed the checks ({r['why']}), so "
                   f"nothing was proposed — UNKNOWN, not a hold",
        "no_prices": f"{r['why']}, so nothing was proposed",
        "unchanged": "no change proposed",
        "refused": f"the card couldn't be raised ({r['why']})",
        "applied": f"approved: {text}",
        "rolled_back": f"approved ({text}), then rolled back",
    }.get(status)
    if words is None:          # carded: the card says how it went
        words = {
            "pending": f"waiting on you (#{r['card_id']}): {text}",
            "suppressed": f"card #{r['card_id']} held back by the judgment layer",
            "approved": f"approved, running: {text}",
            "executed": f"approved: {text}",
            "declined": "declined — nothing changed",
            "expired": "expired unanswered — nothing changed",
            "failed": "approved, but the rebalance failed — nothing changed",
        }.get(str(card), f"card state unknown ({card})")
    if r.get("model") and not _claude(str(r["model"])) and status in (
            "carded", "applied", "unchanged", "rolled_back"):
        words += f" (answered by {r['model']}, not Claude)"
    return words, r


async def section_body(now: datetime) -> str:
    from ...kernel.state import fetch_all
    begin = await first()
    if begin is None:
        return (f"Not started yet: ${START_DOLLARS:.0f} split equally across the "
                f"{len(eligible())} non-VOO tickers on its first Sunday run. "
                f"Paper, not advice.")
    cur = await current()
    control = {str(r["ticker"]): float(r["shares"])
               for r in await fetch_all("SELECT ticker, shares FROM markets_paper")}
    got = await latest(set(cur.shares) | set(control) | {BENCHMARK_SYMBOL})

    def worth(shares: dict[str, float], prices: dict[str, float]) -> float | None:
        if not shares or any(t not in prices for t in shares):
            return None
        return sum(n * prices[t] for t, n in shares.items())

    mine = worth(cur.shares, got.prices)
    c_now, c_then = worth(control, got.prices), worth(control, begin.prices)
    fixed = c_now / c_then * START_DOLLARS if c_now and c_then else None
    v0, v1 = begin.prices.get(BENCHMARK_SYMBOL), got.prices.get(BENCHMARK_SYMBOL)
    voo = START_DOLLARS / v0 * v1 if v0 and v1 else None
    newest = max(got.days.values()) if got.days else None
    when = (f"closes of {newest:%a %-d %b}, read {got.read_at:%a %H:%M} UTC"
            if newest else f"no close readable at {got.read_at:%a %H:%M} UTC")
    lines = [(f"Since {begin.prices_read_at.astimezone(TZ):%-d %b}, ${START_DOLLARS:.0f} each, "
              f"price only: Claude playlist {_money(mine)} · equal split "
              f"{_money(fixed)} · VOO {_money(voo)} ({when}).")]
    if got.missing:
        lines.append(f"Unreadable: {', '.join(sorted(got.missing))}, so what "
                     f"holds them is unknown, not zero.")
    if control and fixed is None and c_now is not None:
        lines.append("The equal split's start value is unknown (a ticker it holds "
                     "had no start close).")
    words, row = await _this_week(now)
    lines.append(f"This week: {words}.")
    reasons = (_json(row.get("reasons")) or {}) if row else {}
    if reasons and row and row["status"] not in ("invalid", "no_model", "no_prices"):
        why = "; ".join(f"{t}: {r}" for t, r in reasons.items())
        lines.append("Why: " + (why if len(why) <= 280 else why[:279] + "…"))
    lines.append("Paper, not advice.")
    return "\n".join(lines)


async def playlist_section(now: datetime | None = None) -> str:
    """The weekly report's playlist paragraph. Never raises: a failure is an
    UNKNOWN paragraph, never a missing one and never a zero."""
    now = now or datetime.now(UTC)
    head = f"*{NAME}*"
    try:
        return f"{head}\n{await section_body(now)}"
    except Exception as exc:
        log.exception("playlist section failed")
        return (f"{head}\nCouldn't read it this week ({type(exc).__name__}), so "
                f"every figure is UNKNOWN — not zero. Paper, not advice.")
