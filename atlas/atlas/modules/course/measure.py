"""What a course post brought to the store, read -- or UNKNOWN, never guessed.

The attribution key is the UTM campaign: every course post's link carries
`utm_campaign=<experiment id>`, so what the store saw of that campaign is
what that post did. Three sources, in order, and the first one that can
actually be read answers:

  1. **Shopify orders.** ATLAS has no Shopify Admin API credential (it reads
     only the public product JSON), so there is no order read path and sales
     are always UNKNOWN here. This is said on every reading rather than
     skipped quietly; Shopify's own analytics (Marketing > UTM campaigns)
     shows them to Miro.
  2. **PostHog pageviews on the store's domain**, when POSTHOG_PERSONAL_KEY is
     set: pageviews whose `utm_campaign` is the experiment id. First it asks
     whether the store has EVER sent PostHog an event -- a store that is not
     instrumented reads as UNKNOWN, not as a confident zero.
  3. Otherwise UNKNOWN, with the reason.

Read-only. The HogQL is PostHog's own `events` table (not ATLAS's Postgres),
and only a validated integer id is ever interpolated into it.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from . import data

METRIC = "store_visits"
_PH = "events"          # PostHog's table, interpolated (tests/test_schema_lint.py)

NO_ORDERS = ("sales unknown: ATLAS has no Shopify order read path (no Admin API "
             "credential); Shopify's UTM report shows them")


async def read(experiment_id: int) -> tuple[dict[str, Any] | None, str]:
    """({"value", "metric", "observed_at", "source"}, note) or (None, reason).
    The note always carries the sales line, so a lesson never implies sales
    were zero."""
    try:
        exp = int(experiment_id)
    except (TypeError, ValueError):
        return None, f"not an experiment id: {experiment_id!r}; {NO_ORDERS}"
    if isinstance(experiment_id, bool) or exp <= 0:
        return None, f"not an experiment id: {experiment_id!r}; {NO_ORDERS}"
    if not os.getenv("POSTHOG_PERSONAL_KEY", "").strip():
        return None, (f"store visits unknown: POSTHOG_PERSONAL_KEY is not set; "
                      f"{NO_ORDERS}")
    from ..posthog import hogql

    host = data.store_host()
    ever, why = await hogql(
        f"SELECT count() FROM {_PH} WHERE properties.$host = '{host}'")
    if ever is None:
        return None, f"store visits unknown: PostHog: {why}; {NO_ORDERS}"
    if not ever or int(ever[0][0] or 0) == 0:
        return None, (f"store visits unknown: the store ({host}) has never sent "
                      f"PostHog an event, so a zero would mean nothing; {NO_ORDERS}")
    rows, why = await hogql(
        f"SELECT count(), uniq(person_id) FROM {_PH} WHERE event = '$pageview' "
        f"AND properties.$host = '{host}' "
        f"AND properties.utm_campaign = '{exp}' "
        f"AND properties.utm_medium = '{data.MEDIUM}'")
    at = datetime.now(UTC)
    if rows is None:
        return None, f"store visits unknown: PostHog: {why}; {NO_ORDERS}"
    views, visitors = (int(x or 0) for x in (rows[0] if rows else [0, 0]))
    return ({"value": float(views), "metric": METRIC, "observed_at": at,
             "source": (f"PostHog: {views} store pageview(s) from {visitors} "
                        f"visitor(s) tagged utm_campaign={exp}")},
            NO_ORDERS)
