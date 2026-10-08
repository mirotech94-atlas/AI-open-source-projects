"""Roots — status.example.org, the attendance and standup platform.

Written against the real API this time. The previous version said so honestly
("ASSUMED ENDPOINTS — NOT CONFIRMED") and degraded to UNKNOWN, which was the
right way to ship a placeholder — but all four paths were wrong and are now
replaced with ones verified against the repo and probed in production:

    GET /health                          public, no credential
    GET /api/health/detailed             admin
    GET /api/attendance                  any authenticated user
    GET /api/admin/integrations/metrics  admin

**`inroots.nudge_missing` is gone.** It POSTed to an endpoint that does not
exist, and what it did was message colleagues about their attendance. Miro's
standing rule for Matrix is that ATLAS never posts in the attendance or
standup rooms; nudging the same people about the same thing through the Roots
API is the same act down a different pipe. It is also structurally impossible
now — see below — which is the better kind of guarantee.

**Ask for a read-scoped token.** Roots mints `k9_`-prefixed API tokens with
scopes, and a `read` token is refused any non-GET request *before the route
runs*. A token acts as a user and can only narrow that user's authority, so
it is never an escalation path. That makes read-only a property of the
credential rather than of this module's good behaviour — the same reasoning
as the Guardian token's scope.

The check worth having most is `http_log_retention`. Roots purges `http_log`
on a 90-day job, and that table once reached 3.17 million rows unnoticed
because nothing reported it. A purge that stops running is invisible from
inside the product: writes keep succeeding and reads get slower.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx

from ...kernel.contracts import (
    Check,
    Health,
    Observation,
    Severity,
    Signal,
)
from .._http import expect_keys, get_json

BASE = os.getenv("INROOTS_URL", "https://status.example.org").rstrip("/")

NO_TOKEN = ("no INROOTS_TOKEN set. Mint a READ-scoped API token in Roots "
            "(it looks like k9_...) and put it in .env. Read scope is "
            "refused any non-GET request before the route runs, so ATLAS "
            "cannot change attendance, send a nudge, or write anything.")

# The http_log purge job's window. Rows older than this mean it stopped.
RETENTION = timedelta(days=int(os.getenv("INROOTS_HTTP_LOG_DAYS", "90")))


def _token() -> str:
    return os.getenv("INROOTS_TOKEN", "").strip()


async def _api(path: str, **params) -> tuple[dict | list | None, str | None]:
    if not _token():
        return None, NO_TOKEN
    return await get_json(path, base_url=BASE,
                          headers={"Authorization": f"Bearer {_token()}",
                                   "Accept": "application/json"},
                          params=params or None)


# ------------------------------------------------------------ no credential

async def check_up() -> Observation:
    """`GET /health` is public and cheap: `{"ok": true, "ts": ...}`.

    Roots is a work-facing service, so it being down is a people problem
    before it is a technical one — everyone's check-in fails at once. This is
    also the check that tells "Roots is down" apart from "ATLAS has no
    token", which is why it is not behind the credential.
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(f"{BASE}/health")
            ms = r.elapsed.total_seconds() * 1000
            # One slow sample is a slow moment, not a slow site; see the
            # product modules' _probe for the measurement that showed it.
            if ms > 3000 and r.status_code < 400:
                await asyncio.sleep(3)   # not under the same burst
                r = await c.get(f"{BASE}/health")
                ms = min(ms, r.elapsed.total_seconds() * 1000)
        if r.status_code >= 500:
            return Observation("up", Health.FAILING, ms, "ms",
                               f"HTTP {r.status_code}")
        if r.status_code >= 400:
            return Observation("up", Health.FAILING, ms, "ms",
                               f"HTTP {r.status_code} from /health")
        body = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {}
        if isinstance(body, dict) and body.get("ok") is False:
            return Observation("up", Health.FAILING, ms, "ms",
                               "Roots reports itself unhealthy")
        if ms > 3000:
            return Observation("up", Health.DEGRADED, ms, "ms",
                               f"{BASE} was slow to answer: {ms:.0f}ms")
        return Observation("up", Health.OK, ms, "ms", f"HTTP {r.status_code}")
    except Exception as exc:                            # noqa: BLE001
        return Observation("up", Health.FAILING, None, None,
                           f"unreachable: {exc}")


# -------------------------------------------------------- /api/health/detailed

async def _detailed() -> tuple[dict | None, str | None]:
    data, reason = await _api("/api/health/detailed")
    if reason:
        return None, reason
    if not isinstance(data, dict):
        return None, (f"/api/health/detailed returned {type(data).__name__}; "
                      f"expected an object")
    return data, None


async def check_bot() -> Observation:
    """The Matrix bot's connection. A disconnected bot records nothing, and
    everyone's day looks like they never checked in."""
    data, reason = await _detailed()
    if reason:
        return Observation("bot", Health.UNKNOWN, detail=reason)
    bad = expect_keys(data, "bot_connected", where="/api/health/detailed")
    if bad:
        return Observation("bot", Health.UNKNOWN, detail=bad)

    connected = bool(data.get("bot_connected"))
    who = data.get("bot_matrix_id") or "unknown account"
    uptime = data.get("uptime_seconds")
    return Observation(
        "bot", Health.OK if connected else Health.FAILING,
        None, None,
        f"the standup bot is {'connected' if connected else 'DISCONNECTED'} "
        f"as {who}"
        + ("" if connected else
           " — nothing is being recorded, and every check-in today will read "
           "as a no-show")
        + (f" (up {float(uptime) / 3600:.1f}h)"
           if isinstance(uptime, (int, float)) else ""),
        evidence={"bot_connected": connected, "bot": who})


async def check_http_log_retention() -> Observation:
    """Is the 90-day http_log purge still running?

    This table reached 3.17 million rows once because nothing reported it.
    The failure is invisible from inside the product — writes keep working
    and reads just get slower — so the oldest row's age is the only signal,
    and it is exactly the sort of thing ATLAS exists to watch.
    """
    data, reason = await _detailed()
    if reason:
        return Observation("http_log_retention", Health.UNKNOWN, detail=reason)

    counts = data.get("db_row_counts")
    if not isinstance(counts, dict) or "http_log_oldest_ts" not in counts:
        return Observation(
            "http_log_retention", Health.UNKNOWN,
            detail="/api/health/detailed carried no http_log_oldest_ts")

    oldest_raw = counts.get("http_log_oldest_ts")
    if oldest_raw is None:
        return Observation("http_log_retention", Health.OK, 0.0, "days",
                           "http_log is empty; nothing to purge")
    try:
        oldest = datetime.fromisoformat(str(oldest_raw))
    except ValueError:
        return Observation("http_log_retention", Health.UNKNOWN,
                           detail=f"unparseable http_log_oldest_ts: "
                                  f"{oldest_raw!r}")
    # Roots writes these naive, in UTC.
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=UTC)

    age = datetime.now(UTC) - oldest
    days = age.total_seconds() / 86400
    limit = RETENTION.total_seconds() / 86400
    recent = counts.get("http_log_last_24h")
    return Observation(
        "http_log_retention",
        Health.FAILING if days > limit * 1.5
        else Health.DEGRADED if days > limit else Health.OK,
        round(days, 1), "days",
        f"oldest http_log row is {days:.0f} days old against a "
        f"{limit:.0f}-day purge"
        + (f"; {recent:,} rows in the last 24h"
           if isinstance(recent, int) else "")
        + ("" if days <= limit else
           " — the purge job has stopped. Nothing fails when it does: writes "
           "keep succeeding and reads get slower until they stall."),
        evidence={"oldest": str(oldest_raw), "last_24h": recent})


def _resource_check(key: str, what: str, warn: float, fail: float):
    """Disk, memory and swap from the same payload."""
    async def _run() -> Observation:
        data, reason = await _detailed()
        if reason:
            return Observation(key, Health.UNKNOWN, detail=reason)
        value = data.get(key)
        if not isinstance(value, (int, float)):
            # Roots returns None when psutil is unavailable. Absent is not
            # healthy; a default of 0 would read as an empty disk.
            return Observation(
                key, Health.UNKNOWN,
                detail=f"/api/health/detailed reported {key}={value!r}; "
                       f"psutil may not be installed on that host")
        return Observation(
            key,
            Health.FAILING if value >= fail
            else Health.DEGRADED if value >= warn else Health.OK,
            float(value), "%", f"{what} at {value:.0f}%")
    return _run


async def check_open_errors() -> Observation:
    data, reason = await _detailed()
    if reason:
        return Observation("open_errors", Health.UNKNOWN, detail=reason)
    count = data.get("open_errors_count")
    if not isinstance(count, int):
        return Observation("open_errors", Health.UNKNOWN,
                           detail=f"open_errors_count was {count!r}")
    return Observation(
        "open_errors",
        Health.DEGRADED if count else Health.OK,
        float(count), "errors",
        f"{count} open bot error(s)" if count else "no open bot errors")


async def check_ai_budget() -> Observation:
    """Roots caps its own AI spend per day. Hitting the cap degrades the
    product quietly — summaries stop being generated, nothing errors."""
    data, reason = await _detailed()
    if reason:
        return Observation("ai_budget", Health.UNKNOWN, detail=reason)
    used, cap = data.get("ai_calls_today"), data.get("ai_daily_budget")
    if not isinstance(used, (int, float)) or not isinstance(cap, (int, float)):
        return Observation("ai_budget", Health.UNKNOWN,
                           detail=f"ai_calls_today={used!r}, "
                                  f"ai_daily_budget={cap!r}")
    if cap <= 0:
        return Observation("ai_budget", Health.UNKNOWN,
                           detail="Roots reports no daily AI budget")
    pct = used / cap * 100
    return Observation(
        "ai_budget",
        Health.FAILING if pct >= 100 else Health.DEGRADED if pct >= 85
        else Health.OK,
        round(pct, 1), "%",
        f"{used:.0f} of {cap:.0f} AI calls used today ({pct:.0f}%)"
        + ("" if pct < 85 else
           " — at the cap, summaries stop being generated and nothing errors"),
        evidence={"used": used, "budget": cap})


async def check_scheduler() -> Observation:
    """Every scheduled job should have a next run time. One that does not has
    fallen off the scheduler, and its work silently stops happening."""
    data, reason = await _detailed()
    if reason:
        return Observation("scheduler", Health.UNKNOWN, detail=reason)
    jobs = data.get("scheduler_jobs")
    if not isinstance(jobs, list):
        return Observation("scheduler", Health.UNKNOWN,
                           detail=f"scheduler_jobs was {type(jobs).__name__}")
    if not jobs:
        return Observation("scheduler", Health.FAILING, 0.0, "jobs",
                           "the scheduler is running no jobs at all")
    stalled = [str(j.get("name")) for j in jobs
               if isinstance(j, dict) and not j.get("next")]
    return Observation(
        "scheduler",
        Health.DEGRADED if stalled else Health.OK,
        float(len(jobs)), "jobs",
        f"{len(jobs)} scheduled job(s)"
        + (f"; no next run for {', '.join(stalled)} — that work has stopped "
           f"happening" if stalled else ""),
        evidence={"jobs": len(jobs), "stalled": stalled})


class InRootsModule:
    name = "inroots"
    title = "Roots"
    # Not gated: /health is public, and keeping it live is what tells
    # "Roots is down" apart from "ATLAS has no token".
    requires_env = ()
    # Not required to load, but these unlock checks that are
    # UNKNOWN without them. `atlas.cli connect inroots` says which.
    # Where: Roots -> API tokens, READ scope (a k9_... value)
    optional_env = ("INROOTS_TOKEN",)

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("up", check_up, timedelta(minutes=5), Severity.HIGH,
                  "Roots is responding", baseline_sigma=None),
            Check("bot", check_bot, timedelta(minutes=10), Severity.HIGH,
                  "the standup bot is connected to Matrix",
                  baseline_sigma=None),
            Check("http_log_retention", check_http_log_retention,
                  timedelta(hours=12), Severity.NORMAL,
                  "the http_log purge job is still running",
                  baseline_sigma=None),
            Check("disk_percent",
                  _resource_check("disk_percent", "disk", 85, 93),
                  timedelta(minutes=30), Severity.HIGH,
                  "disk on the Roots host"),
            Check("memory_percent",
                  _resource_check("memory_percent", "memory", 88, 95),
                  timedelta(minutes=30), Severity.NORMAL,
                  "memory on the Roots host"),
            Check("open_errors", check_open_errors, timedelta(hours=1),
                  Severity.NORMAL, "open bot errors"),
            Check("ai_budget", check_ai_budget, timedelta(hours=2),
                  Severity.NORMAL, "AI calls against the daily cap"),
            Check("scheduler", check_scheduler, timedelta(hours=1),
                  Severity.HIGH, "every scheduled job has a next run time",
                  baseline_sigma=None),
        )

    def actions(self):
        # None, and it cannot be otherwise: a read-scoped k9_ token is
        # refused every non-GET before the route runs. `nudge_missing` also
        # messaged colleagues about their attendance, which is the thing
        # Miro's Matrix rules forbid -- the same act down a different pipe.
        return ()

    async def signals(self):
        obs = await check_http_log_retention()
        if obs.health in (Health.DEGRADED, Health.FAILING):
            return (Signal(
                title="Roots: the http_log purge has stopped",
                body=obs.detail + "\n\nThis needs someone with access to the "
                     "Roots host; ATLAS holds a read-only token and cannot "
                     "run the purge.",
                severity=Severity.NORMAL),)
        return ()

    def brief(self) -> str:
        return ("Roots: service and bot health, host resources, log "
                "retention, scheduled jobs")


MODULE = InRootsModule()
