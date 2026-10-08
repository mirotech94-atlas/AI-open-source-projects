"""Finnhub, read-only and optional: earnings dates, headlines, analyst
recommendation trends for the trade-idea scan (`ideas.py`).

Official REST API (https://finnhub.io/docs/api), GETs only, with
FINNHUB_API_KEY sent in the `X-Finnhub-Token` header -- never in a URL, a
log line, an audit row, a stored reason or a prompt. Unset, nothing here is
called and every reader answers None ("not read"), which the scan treats as
unknown: without an earnings date, single stocks are skipped.

Headlines are outside text: the scan wraps them with `wrap_untrusted`
before any model sees them.
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import httpx

log = logging.getLogger("atlas.markets.finnhub")

BASE = "https://finnhub.io/api/v1"
HEADER = "X-Finnhub-Token"


def enabled() -> bool:
    return bool(os.getenv("FINNHUB_API_KEY", "").strip())


async def _get(path: str, params: dict[str, str]) -> Any | None:
    """One GET, or None. The key rides in the header; a failure is logged
    by status and path only (`_http.explain`), never with the key."""
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{BASE}{path}", params=params, headers={HEADER: key})
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        from .._http import explain
        log.warning("finnhub %s not read: %s", path, explain(exc))
        return None


async def earnings(sym: str, start: date, end: date) -> list[date] | None:
    """Earnings dates for `sym` in [start, end]; [] means Finnhub lists none
    there. None: not read (no key, or the call failed) -- unknown."""
    got = await _get("/calendar/earnings", {"from": start.isoformat(),
                                            "to": end.isoformat(), "symbol": sym})
    if not isinstance(got, dict) or not isinstance(got.get("earningsCalendar"), list):
        return None
    out: list[date] = []
    for row in got["earningsCalendar"]:
        try:
            if str(row.get("symbol", sym)).upper() == sym:
                out.append(date.fromisoformat(str(row["date"])))
        except (KeyError, ValueError, AttributeError):
            return None          # a row we cannot read makes the answer unknown
    return sorted(out)


async def headlines(sym: str, start: date, end: date, limit: int = 3) -> list[str] | None:
    got = await _get("/company-news", {"symbol": sym, "from": start.isoformat(),
                                       "to": end.isoformat()})
    if not isinstance(got, list):
        return None
    return [str(n.get("headline", ""))[:160] for n in got[:limit]
            if isinstance(n, dict) and n.get("headline")]


async def recommendation(sym: str) -> dict | None:
    """The latest month's analyst counts, or None."""
    got = await _get("/stock/recommendation", {"symbol": sym})
    if not isinstance(got, list) or not got or not isinstance(got[0], dict):
        return None
    row = got[0]
    keys = ("strongBuy", "buy", "hold", "sell", "strongSell")
    if not all(isinstance(row.get(k), int) for k in keys):
        return None
    return {"period": str(row.get("period", "")), **{k: row[k] for k in keys}}
