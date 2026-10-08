"""Real estate -- a deal checker that does the analysis part honestly.

Miro, 2026-09-29, after a reel on wholesaling: find a house, paste it into
an "AI deal analyzer", get ARV, repairs, a Max Allowable Offer and the
assignment fee, text the agent an offer, generate a contract, assign it for
a fee ("$25k/month", unverified). ATLAS does only the analysis:

* **Input is what Miro gives** -- an address, and optionally a list price and
  a pasted listing blurb (`/atlas deal <address> | <list price> | <notes>`,
  or POST /api/realestate/deals from the signed-in dashboard). Zillow has no
  public API and its terms forbid scraping: a zillow.com link is read only
  for the address its path spells out, and zillow.com is never requested.
* **Estimates come from RentCast's official API** (`rentcast.py`), and only
  when RENTCAST_API_KEY is set: the value estimate with its comparables, and
  the long-term rent estimate, each with the time it was read, cached, and
  every call counted against the ~50-a-month free tier. Without the key the
  ARV is "unknown — add RENTCAST_API_KEY or enter an ARV", never invented.
* **The card** (`deal.py`), topic `realestate`: ARV with source, comparable
  count and read time; repairs and their assumptions ($/sqft bands); the MAO
  formula with its numbers; the spread vs list; a rental view (rent, rough
  cap rate, 1% rule); a plain verdict; and the disclaimer. Then a text-only
  offer draft and a contract-step checklist.

ATLAS never contacts agents, never messages a third party, never signs or
generates a binding contract. Its one action, `realestate.check_deal`, is
Tier 1: it reads RentCast, writes ATLAS's own `realestate_deals` row and
sends Miro the card -- nothing else. It is `principal_only`: only Miro
asking directly runs it (Slack /atlas, the dashboard), never the autonomous
reasoner, which protects the month's RentCast calls.

The weekly summary can include `checker.weekly_section()`.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ...kernel.contracts import Action, Check, Health, Observation, Severity, Tier
from . import rentcast
from .checker import ACTION, PARAMS, check_deal, check_deal_dry, weekly_section, withdraw

ACTION_VERSION = "1"
NAME = "rentcast"

__all__ = ["MODULE", "weekly_section"]


async def check_rentcast() -> Observation:
    """ATLAS's own count of this month's RentCast calls, and whether the
    last answer refused the key (FAILING). Never calls RentCast (that would
    spend the quota it is counting); a key never yet used reads OK on the
    count alone."""
    read_at = datetime.now(UTC)
    if not rentcast.configured():
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"{rentcast.KEY_ENV} not set — ARVs are unknown "
                                  f"unless entered by hand ({rentcast.SIGNUP})")
    try:
        used = await rentcast.used_this_month()
        last = await rentcast.last_status()
    except Exception as exc:                            # noqa: BLE001
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"call count unreadable ({type(exc).__name__})")
    limit = rentcast.monthly_limit()
    detail = (f"ATLAS counted {used}/{limit} RentCast calls this month "
              f"(read {read_at:%H:%M} UTC)")
    if last is not None and last[0] in (401, 403):
        # A refused key is not "fine because nothing was spent".
        return Observation(NAME, Health.FAILING, float(used), "calls",
                           f"RentCast refused the key (HTTP {last[0]}) at "
                           f"{last[1]:%Y-%m-%d %H:%M} UTC — {detail}",
                           evidence={"used": used, "limit": limit,
                                     "status": last[0],
                                     "read_at": read_at.isoformat()})
    health = Health.DEGRADED if used >= limit else Health.OK
    if health is Health.DEGRADED:
        detail = f"this month's calls are used up — {detail}"
    return Observation(NAME, health, float(used), "calls", detail,
                       evidence={"used": used, "limit": limit,
                                 "read_at": read_at.isoformat()})


class RealEstateModule:
    name = "realestate"
    title = "Real estate deal checker"
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = (
        "RENTCAST_API_KEY", "RENTCAST_MONTHLY_LIMIT", "REALESTATE_CACHE_DAYS",
        "REALESTATE_ARV_FACTOR", "REALESTATE_ASSIGNMENT_FEE",
        "REALESTATE_REPAIR_BANDS")
    setup_note = ""

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check(NAME, check_rentcast, timedelta(hours=6), Severity.INFO,
                      "RentCast: ATLAS's count of this month's calls, read-only",
                      baseline_sigma=None),)

    def actions(self):
        return (
            Action(
                name=ACTION, tier=Tier.AUTO,
                summary="Check a real estate deal Miro gives (address, list "
                        "price, notes): ARV, repairs, MAO, spread, rent, "
                        "verdict. Sends only Miro the card; contacts no one",
                run=check_deal, dry_run=check_deal_dry, rollback=withdraw,
                params=dict(PARAMS), max_per_hour=10, principal_only=True),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""


MODULE = RealEstateModule()
