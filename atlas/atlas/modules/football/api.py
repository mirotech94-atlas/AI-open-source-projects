"""API-Football v3 (api-sports.io), read-only.

Verified 2026-09-29 against API-Football's own OpenAPI document (v3.9.3,
`public/doc/openapi.yaml`, the file its documentation page renders; the page
itself sits behind a bot challenge) and against the live host:

    GET https://v3.football.api-sports.io/<endpoint>
        x-apisports-key: $API_FOOTBALL_KEY          (GET only; no other header)
    -> {"get", "parameters", "errors", "results", "paging", "response"}

  * `status` -- the account, the plan and `requests.current` /
    `requests.limit_day`. "This call does not count against the daily quota."
  * every other response carries `x-ratelimit-requests-limit` and
    `x-ratelimit-requests-remaining` (per day) and `X-RateLimit-*` (per
    minute).
  * a missing or bad key: the live host answered HTTP 403 with
    `errors: {"token": "..."}`; `errors` is `[]` when all is well and an
    object naming the problem otherwise -- sometimes under HTTP 200, so a
    non-empty `errors` is a failure whatever the status.

The free plan allows 100 requests a day. Every request (but `status`) is
RESERVED in `football_api_quota` before it is sent, and the day's count is
reconciled from the response headers afterwards -- the counter may over-count,
never under-count. A read is stored in `football_api_reads` with the time it
was read and served from there while fresh (`max_age`), so one question is
never paid for twice in a day. The key rides only in its header and is
never written to a reason, a log or the database.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import httpx

from ...kernel.state import execute_sql, fetch_one

KEY_ENV = "API_FOOTBALL_KEY"
BASE = "https://v3.football.api-sports.io"
HEADER = "x-apisports-key"
FREE_LIMIT_DAY = 100
TIMEOUT_S = 20.0


class NoKey(Exception):
    """API_FOOTBALL_KEY is not set: the lane is idle."""


class QuotaSpent(Exception):
    """The day's requests are used up (by our own count or the API's)."""


class ApiError(Exception):
    """The API refused or failed. `auth` is True for a 401/403 or a token
    error -- a key problem, not an outage."""

    def __init__(self, message: str, *, status: int | None = None,
                 auth: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.auth = auth


@dataclass(frozen=True, slots=True)
class Read:
    endpoint: str
    params: dict[str, Any]
    response: list[Any] | dict[str, Any]
    read_at: datetime
    cached: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def key() -> str:
    return os.getenv(KEY_ENV, "").strip()


def configured() -> bool:
    return bool(key())


def limit_day() -> int:
    """Our own ceiling: the plan's 100 unless told otherwise."""
    try:
        return max(1, int(os.getenv("API_FOOTBALL_DAILY_LIMIT", str(FREE_LIMIT_DAY))))
    except ValueError:
        return FREE_LIMIT_DAY


def api_day(now: datetime | None = None) -> date:
    """The quota's day. Assumed to turn at 00:00 UTC; /status reconciles it."""
    return (now or datetime.now(UTC)).astimezone(UTC).date()


def masked(text: str) -> str:
    k = key()
    text = str(text or "")
    return text.replace(k, "***") if k else text


def errors_of(data: Any) -> str | None:
    """The API's `errors` field in words, or None when it is empty."""
    errs = (data or {}).get("errors") if isinstance(data, dict) else None
    if not errs:
        return None
    if isinstance(errs, dict):
        return masked("; ".join(f"{k}: {v}" for k, v in errs.items()))
    if isinstance(errs, list):
        return masked("; ".join(json.dumps(e) if not isinstance(e, str) else e
                                for e in errs))
    return masked(str(errs))


def _is_auth(data: Any) -> bool:
    errs = (data or {}).get("errors") if isinstance(data, dict) else None
    return isinstance(errs, dict) and "token" in errs


def _canonical(params: dict[str, Any]) -> str:
    return json.dumps({k: str(v) for k, v in sorted(params.items())}, sort_keys=True)


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


# ------------------------------------------------------------------ quota

async def used_today() -> int:
    row = await fetch_one("SELECT used FROM football_api_quota WHERE day = $1",
                          api_day())
    return int(row["used"]) if row else 0


async def reserve() -> int:
    """Count one request before it is sent. Returns the day's count with it,
    or raises QuotaSpent when the day's limit is already reached."""
    row = await fetch_one(
        """INSERT INTO football_api_quota (day, used, limit_day)
           VALUES ($1::date, 1, $2)
           ON CONFLICT (day) DO UPDATE SET used = football_api_quota.used + 1
            WHERE football_api_quota.used < least(
                  coalesce(football_api_quota.limit_day, $2), $2)
           RETURNING used""",
        api_day(), limit_day())
    if row is None:
        raise QuotaSpent(f"today's API-Football requests are used up "
                         f"({limit_day()} a day, read {datetime.now(UTC):%H:%M} UTC)")
    return int(row["used"])


async def reconcile(used: int | None, limit: int | None) -> None:
    """Take the API's own count when it is higher than ours."""
    if used is None and limit is None:
        return
    await execute_sql(
        """INSERT INTO football_api_quota (day, used, limit_day)
           VALUES ($1::date, coalesce($2, 0), $3)
           ON CONFLICT (day) DO UPDATE
              SET used = greatest(football_api_quota.used, coalesce($2, 0)),
                  limit_day = coalesce($3, football_api_quota.limit_day)""",
        api_day(), used, limit)


def _header_int(r: httpx.Response, name: str) -> int | None:
    try:
        return int(r.headers.get(name, ""))
    except ValueError:
        return None


# ------------------------------------------------------------------ reads

async def _cached(endpoint: str, params: dict[str, Any], max_age_s: int) -> Read | None:
    if max_age_s <= 0:
        return None
    row = await fetch_one(
        f"""SELECT body, at FROM football_api_reads
             WHERE endpoint = $1 AND params = $2::jsonb AND ok
               AND at > now() - interval '{int(max_age_s)} seconds'
             ORDER BY at DESC LIMIT 1""",
        endpoint, _canonical(params))
    if row is None:
        return None
    body = _loads(row["body"]) or {}
    return Read(endpoint, params, body.get("response") or [], row["at"], cached=True)


async def _store(endpoint: str, params: dict[str, Any], status: int, ok: bool,
                 body: Any) -> None:
    await execute_sql(
        """INSERT INTO football_api_reads (endpoint, params, status, ok, body)
           VALUES ($1, $2::jsonb, $3, $4, $5::jsonb)""",
        endpoint, _canonical(params), status, ok,
        json.dumps(body) if body is not None else None)


async def _send(endpoint: str, params: dict[str, Any]) -> tuple[httpx.Response, Any]:
    k = key()
    if not k:
        raise NoKey(f"{KEY_ENV} not set")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as c:
            r = await c.get(f"{BASE}/{endpoint}", headers={HEADER: k},
                            params={a: str(b) for a, b in params.items()})
    except httpx.HTTPError as exc:
        raise ApiError(f"API-Football unreachable: {type(exc).__name__}") from None
    try:
        data = r.json()
    except ValueError:
        data = None
    return r, data


def _raise_for(r: httpx.Response, data: Any, endpoint: str) -> None:
    said = errors_of(data)
    if r.status_code in (401, 403) or _is_auth(data):
        raise ApiError(f"API-Football refused the key on {endpoint} "
                       f"(HTTP {r.status_code}{': ' + said if said else ''})",
                       status=r.status_code, auth=True)
    if r.status_code == 429:
        raise QuotaSpent(f"API-Football rate limit on {endpoint} (HTTP 429"
                         f"{': ' + said if said else ''})")
    if r.status_code != 200:
        raise ApiError(f"API-Football {endpoint}: HTTP {r.status_code}"
                       f"{': ' + said if said else ''}", status=r.status_code)
    if data is None:
        raise ApiError(f"API-Football {endpoint}: the answer was not JSON",
                       status=r.status_code)
    if said:
        # By the error's own key ("requests", "rateLimit", "plan", "token"...):
        # the text of a quota message may well mention the plan.
        errs = data.get("errors") if isinstance(data, dict) else None
        keys = {str(k).lower() for k in errs} if isinstance(errs, dict) else set()
        if keys & {"requests", "ratelimit"} or (not keys and re.search(
                r"request limit|rate limit", said, re.IGNORECASE)):
            raise QuotaSpent(f"API-Football {endpoint}: {said}")
        raise ApiError(f"API-Football {endpoint}: {said}", status=r.status_code)


async def get(endpoint: str, params: dict[str, Any], *, max_age_s: int = 12 * 3600
              ) -> Read:
    """One read: from the store while fresh, else one reserved request.
    Raises NoKey, QuotaSpent or ApiError; never returns a guess."""
    if not configured():
        raise NoKey(f"{KEY_ENV} not set")
    if (hit := await _cached(endpoint, params, max_age_s)) is not None:
        return hit
    await reserve()
    r, data = await _send(endpoint, params)
    limit = _header_int(r, "x-ratelimit-requests-limit")
    remaining = _header_int(r, "x-ratelimit-requests-remaining")
    if limit is not None and remaining is not None:
        await reconcile(max(0, limit - remaining), limit)
    try:
        _raise_for(r, data, endpoint)
    except (ApiError, QuotaSpent):
        await _store(endpoint, params, r.status_code, False, data)
        raise
    await _store(endpoint, params, r.status_code, True, data)
    return Read(endpoint, params, data.get("response") or [], datetime.now(UTC))


async def status() -> dict[str, Any]:
    """`/status`: the plan and the day's requests. Free of quota (the docs
    say so), so it is never reserved and never cached."""
    r, data = await _send("status", {})
    _raise_for(r, data, "status")
    resp = data.get("response")
    if not isinstance(resp, dict):
        raise ApiError("API-Football status carried no account (response was "
                       f"{type(resp).__name__})")
    return resp
