"""scrape -- the shared polite scraper's budget, as a module the portal and
the departments can see. The scraper itself is `atlas/modules/_scrape.py`
(a library, not a module: it never acts and has no actions).

Watches: `budget` -- requests today against `SCRAPE_DAILY_CAP`. OK under the
cap with the count and its read time, DEGRADED at the cap, UNKNOWN when the
count cannot be read (never OK).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import Check, Health, Observation, Severity
from .. import _scrape

log = logging.getLogger("atlas.scrape")


async def check_budget() -> Observation:
    now = datetime.now(UTC)
    try:
        used = await _scrape._count_today()
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return Observation("budget", Health.UNKNOWN, at=now,
                           detail=f"scrape count unreadable: {explain(exc)}")
    cap = _scrape.daily_cap()
    health = Health.DEGRADED if used >= cap else Health.OK
    try:
        await _scrape.prune()
    except Exception as exc:  # noqa: BLE001
        log.debug("scrape prune skipped: %s", type(exc).__name__)
    return Observation("budget", health, value=float(used), unit="requests",
                       detail=f"{used}/{cap} scrape requests today "
                              f"(read {now:%H:%M} UTC)",
                       evidence={"used": used, "cap": cap,
                                 "read_at": now.isoformat()}, at=now)


class ScrapeModule:
    name = "scrape"
    title = "Polite scraper"
    requires_env = ()
    optional_env = ("SCRAPE_DAILY_CAP", "SCRAPE_USER_AGENT", "BRAVE_API_KEY",
                    "GOOGLE_CSE_KEY", "GOOGLE_CSE_CX")
    setup_note = ("BRAVE_API_KEY (free plan) gives the agents web search; "
                  "without it search says 'no free search provider'.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple[Check, ...]:
        return (Check("budget", check_budget, timedelta(minutes=30), Severity.INFO,
                      "shared scraper requests today against the daily cap",
                      baseline_sigma=None),)

    def actions(self) -> tuple:
        return ()

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = ScrapeModule()
