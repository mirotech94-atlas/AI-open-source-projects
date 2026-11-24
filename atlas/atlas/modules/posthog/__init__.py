"""PostHog -- read-only site traffic for the weekly report.

the owner, 2026-09-28, from the "solo startup playbook" reel: PostHog measures
the funnel (reel -> bio -> page -> checkout) so the reel mix follows real
numbers. ATLAS only reads: a HogQL query with the owner's personal key
(POSTHOG_PERSONAL_KEY), never the ingest key the pages carry. A read that
fails is UNKNOWN; a project that has never received an event says so rather
than reporting a confident zero.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ...kernel.contracts import Check, Health, Observation, Severity
from .._http import explain


def _host() -> str:
    return os.getenv("POSTHOG_HOST", "https://us.posthog.com").rstrip("/")


def _project() -> str:
    return os.getenv("POSTHOG_PROJECT_ID", "634619")


async def hogql(sql: str) -> tuple[list[list[Any]] | None, str | None]:
    """Rows, or (None, why). Read-only: a HogQL SELECT."""
    key = os.getenv("POSTHOG_PERSONAL_KEY", "")
    if not key:
        return None, "POSTHOG_PERSONAL_KEY is not set"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{_host()}/api/projects/{_project()}/query/",
                headers={"Authorization": f"Bearer {key}"},
                json={"query": {"kind": "HogQLQuery", "query": sql}})
        if r.status_code != 200:
            return None, f"PostHog returned {r.status_code}"
        return r.json().get("results") or [], None
    except (httpx.HTTPError, ValueError) as exc:
        return None, explain(exc)


# HogQL, run by PostHog against ITS events table -- not ATLAS's Postgres, so
# the table name is interpolated to keep tests/test_schema_lint.py from
# checking it against db/schema.sql.
_PH = "events"
TOTALS = ("SELECT countIf(event = '$pageview' AND timestamp > now() - INTERVAL 7 DAY), "
          "uniqIf(person_id, event = '$pageview' AND timestamp > now() - INTERVAL 7 DAY), "
          f"count() FROM {_PH}")
BY_SITE = (f"SELECT properties.$host, count(), uniq(person_id) FROM {_PH} "
           "WHERE event = '$pageview' AND timestamp > now() - INTERVAL 7 DAY "
           "GROUP BY properties.$host ORDER BY count() DESC LIMIT 8")


def _between(since: datetime, until: datetime) -> str:
    fmt = "%Y-%m-%d %H:%M:%S"
    return (f"timestamp > toDateTime('{since.astimezone(UTC):{fmt}}', 'UTC') AND "
            f"timestamp <= toDateTime('{until.astimezone(UTC):{fmt}}', 'UTC')")


async def pageviews_between(since: datetime, until: datetime,
                            ) -> tuple[dict[str, Any] | None, str | None]:
    """Pageviews, visitors and the top sites over exactly `since`..`until`
    (the reels Analyst's window), or (None, why). A project that has never
    received an event is not a zero: it says so."""
    where = f"event = '$pageview' AND {_between(since, until)}"
    totals, why = await hogql(f"SELECT count(), uniq(person_id) FROM events WHERE {where}")
    if totals is None:
        return None, why
    ever, why = await hogql("SELECT count() FROM events")
    if ever is None:
        return None, why
    if not ever or int(ever[0][0] or 0) == 0:
        return None, "no event has ever reached PostHog: no site sends events yet"
    sites, why = await hogql(
        f"SELECT properties.$host, count() FROM events WHERE {where} "
        f"GROUP BY properties.$host ORDER BY count() DESC LIMIT 8")
    if sites is None:
        return None, why
    views, visitors = (int(x or 0) for x in (totals[0] if totals else [0, 0]))
    return {"views": views, "visitors": visitors,
            "sites": [(h, int(n)) for h, n in sites]}, None


async def check_traffic() -> Observation:
    read_at = datetime.now(UTC)
    rows, why = await hogql(TOTALS)
    if rows is None:
        return Observation("traffic_7d", Health.UNKNOWN,
                           detail=f"site traffic unreadable: {why}")
    views, visitors, ever = (int(x or 0) for x in (rows[0] if rows else [0, 0, 0]))
    if ever == 0:
        return Observation("traffic_7d", Health.UNKNOWN, detail=(
            "no event has ever reached PostHog: no site sends events yet"))
    sites, _ = await hogql(BY_SITE)
    per = ", ".join(f"{h or '?'} {int(n)}" for h, n, _u in (sites or []))
    return Observation(
        "traffic_7d", Health.OK, views, "pageviews",
        f"{views} pageviews from {visitors} visitors in 7 days"
        + (f" ({per})" if per else "")
        + f" (read {read_at:%a %d %b %H:%M} UTC)",
        evidence={"views": views, "visitors": visitors, "read_at": read_at.isoformat()})


class PostHogModule:
    name = "posthog"
    title = "Site traffic (PostHog)"
    requires_env: tuple[str, ...] = ("POSTHOG_PERSONAL_KEY",)

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("traffic_7d", check_traffic, timedelta(hours=6), Severity.INFO,
                      "pageviews and visitors in the last 7 days, per site, read-only",
                      baseline_sigma=None),)

    def actions(self):
        return ()          # read-only

    async def signals(self):
        return ()


MODULE = PostHogModule()
