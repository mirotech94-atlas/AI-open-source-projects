"""RentCast's official API, read-only -- only when RENTCAST_API_KEY is set.

Verified 2026-09-29 against RentCast's API reference
(developers.rentcast.io/reference/value-estimate and
/reference/rent-estimate-long-term):

    GET https://api.rentcast.io/v1/avm/value?address=<Street, City, State, Zip>
    GET https://api.rentcast.io/v1/avm/rent/long-term?address=...
        X-Api-Key: $RENTCAST_API_KEY
    value -> {price, priceRangeLow, priceRangeHigh, subjectProperty{squareFootage,
              ...}, comparables[...]}
    rent  -> {rent, rentRangeLow, rentRangeHigh, subjectProperty, comparables[...]}
    401 -> missing or invalid key

The free plan is about 50 calls a month, and RentCast's responses carry no
remaining-quota figure. So every call is RESERVED in
`realestate_rentcast_quota` (keyed by UTC month) before it is sent, a failed
call included -- the count may over-count, never under-count -- and it is
shown as "ATLAS counted N/limit". RENTCAST_MONTHLY_LIMIT overrides 50.

A successful read is kept in `realestate_rentcast_reads` and served from
there for REALESTATE_CACHE_DAYS (default 30) with its ORIGINAL read time, so
one address is never paid for twice in a month.

The key rides only in its header. It is never in a URL, a log line, an
error, a stored row or a prompt. Only api.rentcast.io is ever requested;
zillow.com never is.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx

from ...kernel.state import fetch_one

log = logging.getLogger("atlas.realestate.rentcast")

KEY_ENV = "RENTCAST_API_KEY"
HOST = "api.rentcast.io"
BASE = f"https://{HOST}/v1"
HEADER = "X-Api-Key"
VALUE = "avm/value"
RENT = "avm/rent/long-term"
FREE_LIMIT_MONTH = 50
TIMEOUT_S = 20.0
SIGNUP = "https://app.rentcast.io"

# Tests put an httpx.MockTransport here; None is the real network.
transport: httpx.AsyncBaseTransport | None = None


class NoKey(Exception):
    """RENTCAST_API_KEY is not set."""


class QuotaSpent(Exception):
    """This month's calls are used up by ATLAS's own count."""


class ApiError(Exception):
    """RentCast refused or failed. `auth` for a refused key; `no_data` for
    an address it has nothing for. The message never carries the key."""

    def __init__(self, message: str, *, status: int | None = None,
                 auth: bool = False, no_data: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.auth = auth
        self.no_data = no_data


@dataclass(frozen=True, slots=True)
class Read:
    endpoint: str
    body: dict[str, Any]
    read_at: datetime
    cached: bool = False


def key() -> str:
    return os.getenv(KEY_ENV, "").strip()


def configured() -> bool:
    return bool(key())


def monthly_limit() -> int:
    try:
        return max(1, int(os.getenv("RENTCAST_MONTHLY_LIMIT", str(FREE_LIMIT_MONTH))))
    except ValueError:
        return FREE_LIMIT_MONTH


def cache_days() -> int:
    try:
        return max(0, min(int(os.getenv("REALESTATE_CACHE_DAYS", "30")), 365))
    except ValueError:
        return 30


def month(now: datetime | None = None) -> date:
    """The quota month, assumed to turn at 00:00 UTC on the 1st."""
    d = (now or datetime.now(UTC)).astimezone(UTC).date()
    return d.replace(day=1)


def masked(text: str) -> str:
    k = key()
    text = str(text or "")
    return text.replace(k, "***") if k else text


def _query_key(params: dict[str, Any]) -> str:
    return json.dumps({k: " ".join(str(v).lower().split())
                       for k, v in sorted(params.items())}, sort_keys=True)


# ------------------------------------------------------------------ quota

async def used_this_month() -> int:
    row = await fetch_one(
        "SELECT used FROM realestate_rentcast_quota WHERE month = $1::date", month())
    return int(row["used"]) if row else 0


async def last_status() -> tuple[int, datetime] | None:
    """The newest RentCast answer ATLAS stored: (HTTP status, when)."""
    row = await fetch_one(
        "SELECT status, at FROM realestate_rentcast_reads ORDER BY at DESC, id DESC "
        "LIMIT 1")
    return (int(row["status"]), row["at"]) if row else None


async def calls_line() -> str:
    """'ATLAS counted 3/50 RentCast calls this month' -- or why not."""
    if not configured():
        return f"RentCast not configured ({KEY_ENV} unset) — no calls made"
    try:
        used = await used_this_month()
    except Exception:  # noqa: BLE001
        return "RentCast calls this month: unknown (ATLAS's count unreadable)"
    return (f"ATLAS counted {used}/{monthly_limit()} RentCast calls this month "
            f"({datetime.now(UTC):%Y-%m} UTC)")


async def reserve() -> int:
    """Count one call before it is sent, or raise QuotaSpent."""
    limit = monthly_limit()
    row = await fetch_one(
        """INSERT INTO realestate_rentcast_quota (month, used, limit_month)
           VALUES ($1::date, 1, $2)
           ON CONFLICT (month) DO UPDATE
              SET used = realestate_rentcast_quota.used + 1, limit_month = $2
            WHERE realestate_rentcast_quota.used < $2
           RETURNING used""",
        month(), limit)
    if row is None:
        raise QuotaSpent(f"this month's RentCast calls are used up by ATLAS's count "
                         f"({limit}/{limit}; RENTCAST_MONTHLY_LIMIT)")
    return int(row["used"])


# ------------------------------------------------------------------ reads

async def _cached(endpoint: str, qkey: str) -> Read | None:
    days = cache_days()
    if days <= 0:
        return None
    row = await fetch_one(
        """SELECT body, at FROM realestate_rentcast_reads
            WHERE endpoint = $1 AND query_key = $2 AND ok
              AND at > now() - make_interval(days => $3::int)
            ORDER BY at DESC LIMIT 1""",
        endpoint, qkey, days)
    if row is None:
        return None
    body = row["body"]
    body = json.loads(body) if isinstance(body, str) else (body or {})
    return Read(endpoint, body, row["at"], cached=True)


async def _store(endpoint: str, qkey: str, status: int, ok: bool,
                 body: dict[str, Any] | None) -> datetime:
    row = await fetch_one(
        """INSERT INTO realestate_rentcast_reads (endpoint, query_key, status, ok, body)
           VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING at""",
        endpoint, qkey, status, ok, json.dumps(body) if body is not None else None)
    return row["at"] if row else datetime.now(UTC)


def _reason(r: httpx.Response) -> str:
    try:
        data = r.json()
        msg = data.get("message") or data.get("error") if isinstance(data, dict) else ""
    except ValueError:
        msg = ""
    return masked(str(msg or r.reason_phrase or ""))[:200]


async def get(endpoint: str, params: dict[str, Any]) -> Read:
    """One RentCast read, from the cache when fresh."""
    k = key()
    if not k:
        raise NoKey(f"{KEY_ENV} not set")
    qkey = _query_key(params)
    hit = await _cached(endpoint, qkey)
    if hit is not None:
        return hit
    await reserve()
    url = f"{BASE}/{endpoint}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as c:
            r = await c.get(url, params=params, headers={HEADER: k})
    except httpx.HTTPError as exc:
        # Never str(exc) of a request: say what kind of failure it was.
        raise ApiError(f"RentCast unreachable ({type(exc).__name__})") from None
    if r.status_code in (401, 403):
        await _store(endpoint, qkey, r.status_code, False, None)
        raise ApiError(f"RentCast refused the key (HTTP {r.status_code}: "
                       f"{_reason(r)})", status=r.status_code, auth=True)
    if r.status_code == 404:
        await _store(endpoint, qkey, 404, False, None)
        raise ApiError(f"RentCast has no data for this address (HTTP 404: "
                       f"{_reason(r)})", status=404, no_data=True)
    if r.status_code != 200:
        await _store(endpoint, qkey, r.status_code, False, None)
        raise ApiError(f"RentCast answered HTTP {r.status_code} ({_reason(r)})",
                       status=r.status_code)
    try:
        body = r.json()
    except ValueError:
        raise ApiError("RentCast answered with something that is not JSON",
                       status=200) from None
    if not isinstance(body, dict):
        raise ApiError("RentCast's answer was not an object", status=200)
    read_at = await _store(endpoint, qkey, 200, True, body)
    return Read(endpoint, body, read_at)


def _num(body: dict[str, Any], name: str) -> float | None:
    v = body.get(name)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v) if v > 0 else None


def value_fields(body: dict[str, Any]) -> dict[str, Any]:
    comps = body.get("comparables")
    subject = body.get("subjectProperty") or {}
    return {"price": _num(body, "price"), "low": _num(body, "priceRangeLow"),
            "high": _num(body, "priceRangeHigh"),
            "comps": len(comps) if isinstance(comps, list) else None,
            "sqft": _num(subject, "squareFootage") if isinstance(subject, dict)
            else None}


def rent_fields(body: dict[str, Any]) -> dict[str, Any]:
    comps = body.get("comparables")
    return {"rent": _num(body, "rent"), "low": _num(body, "rentRangeLow"),
            "high": _num(body, "rentRangeHigh"),
            "comps": len(comps) if isinstance(comps, list) else None}
