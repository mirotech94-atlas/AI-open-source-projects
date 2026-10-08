"""Mirotech — eBay reselling of IT and networking hardware.

Watches listing performance and sourcing opportunities. The judgement encoded
here: compare against **completed sale** prices, never asking prices. Asking
prices on used enterprise hardware are fiction; sold prices are the market.
"""
from __future__ import annotations

import os
from datetime import timedelta

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)

API = "https://api.ebay.com"


async def _token() -> str:
    import base64
    creds = base64.b64encode(
        f"{os.environ['EBAY_CLIENT_ID']}:{os.environ['EBAY_CLIENT_SECRET']}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{API}/identity/v1/oauth2/token",
                         headers={"Authorization": f"Basic {creds}",
                                  "Content-Type": "application/x-www-form-urlencoded"},
                         data={"grant_type": "refresh_token",
                               "refresh_token": os.environ["EBAY_REFRESH_TOKEN"],
                               "scope": "https://api.ebay.com/oauth/api_scope/sell.inventory"})
        r.raise_for_status()
        return r.json()["access_token"]


async def _api(path: str, **params):
    tok = await _token()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}{path}",
                        headers={"Authorization": f"Bearer {tok}",
                                 "Accept": "application/json"}, params=params)
        r.raise_for_status()
        return r.json()


def _listed(data, key: str, path: str) -> list:
    """The list under `key`, or ValueError when it cannot be counted.

    `data.get("offers", [])` made a response without the list read as zero
    listings, OK (RA-E1). An absent list is accepted as empty only when the
    response's own `total` says 0.
    """
    rows = data.get(key) if isinstance(data, dict) else None
    if isinstance(rows, list):
        return rows
    if rows is None and isinstance(data, dict) and data.get("total") == 0:
        return []
    raise ValueError(f"eBay {path} returned no {key} list and no total of 0, "
                     f"so nothing was counted")


async def check_listings() -> Observation:
    try:
        data = await _api("/sell/inventory/v1/offer", limit=100)
        offers = _listed(data, "offers", "/sell/inventory/v1/offer")
        published = [o for o in offers
                     if o.get("status", "").upper() == "PUBLISHED"]
        return Observation("listings", Health.OK, float(len(published)),
                           "listings", f"{len(published)} active listings")
    except Exception as exc:                            # noqa: BLE001
        return Observation("listings", Health.UNKNOWN, detail=str(exc))


async def check_stale_listings() -> Observation:
    """A listing that has sat for 60 days with few watchers is priced wrong,
    and every day it sits is capital not working."""
    try:
        data = await _api("/sell/inventory/v1/offer", limit=100)
    except Exception as exc:                            # noqa: BLE001
        return Observation("stale_listings", Health.UNKNOWN, detail=str(exc))
    try:
        offers = _listed(data, "offers", "/sell/inventory/v1/offer")
    except ValueError as exc:
        return Observation("stale_listings", Health.UNKNOWN, detail=str(exc))
    published = [o for o in offers if isinstance(o, dict)
                 and str(o.get("status", "")).upper() == "PUBLISHED"]

    def age(o: dict) -> int | None:
        raw = (o.get("listing") or {}).get("listingDuration_days")
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else None

    aged = [o for o in published if age(o) is not None]
    if published and not aged:
        # The offer resource carries no listing age (eBay's own field is
        # `listingDuration`, a string such as "GTC"), so every age read as 0
        # and this check could only ever say OK (RA-E1).
        return Observation(
            "stale_listings", Health.UNKNOWN, None, None,
            f"{len(published)} published listing(s), none carrying a readable "
            f"listing age, so staleness is not measured")
    stale = [o.get("sku") for o in aged if (age(o) or 0) > 60]
    return Observation(
        "stale_listings",
        Health.DEGRADED if len(stale) > 3 else Health.OK,
        float(len(stale)), "listings",
        f"{len(stale)} listing(s) live for over 60 days",
        evidence={"skus": [s for s in stale if s][:10]})


async def check_orders() -> Observation:
    try:
        data = await _api("/sell/fulfillment/v1/order",
                          filter="orderfulfillmentstatus:{NOT_STARTED|IN_PROGRESS}",
                          limit=50)
        orders = _listed(data, "orders", "/sell/fulfillment/v1/order")
        return Observation(
            "orders",
            Health.DEGRADED if len(orders) > 5 else Health.OK,
            float(len(orders)), "orders",
            f"{len(orders)} order(s) awaiting fulfilment")
    except Exception as exc:                            # noqa: BLE001
        return Observation("orders", Health.UNKNOWN, detail=str(exc))


async def price_check(query: str) -> ActionResult:
    """Sold-price comparison. Read-only: it reports, it does not reprice."""
    try:
        data = await _api("/buy/browse/v1/item_summary/search",
                          q=query, limit=25,
                          filter="conditionIds:{3000|4000|5000}")
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"eBay search failed: {exc}")
    items = data.get("itemSummaries", [])
    prices = [float(i["price"]["value"]) for i in items if i.get("price")]
    if not prices:
        return ActionResult(False, f"no comparable listings for {query!r}")
    prices.sort()
    mid = prices[len(prices) // 2]
    return ActionResult(
        True,
        f"{len(prices)} comparables for '{query}': "
        f"low ${prices[0]:,.0f}, median ${mid:,.0f}, high ${prices[-1]:,.0f}. "
        f"Note these are active asks, not sold prices — check Sold filters "
        f"before repricing.",
        evidence={"prices": prices})


async def price_dry(query: str) -> ActionResult:
    return ActionResult(True, f"would search eBay comparables for {query!r}")


class MirotechModule:
    name = "mirotech"
    title = "Mirotech (eBay)"
    requires_env = ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_REFRESH_TOKEN")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("listings", check_listings, timedelta(hours=6),
                  Severity.INFO, "active listing count"),
            Check("stale_listings", check_stale_listings, timedelta(days=1),
                  Severity.NORMAL, "listings sitting too long",
                  baseline_sigma=None),
            Check("orders", check_orders, timedelta(hours=2), Severity.HIGH,
                  "orders awaiting fulfilment", baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="mirotech.price_check",
                tier=Tier.AUTO,     # read-only lookup
                summary="Look up comparable eBay prices for a piece of hardware",
                run=price_check, dry_run=price_dry,
                rollback=lambda t: price_dry("noop"),
                params={"query": "model or part number, e.g. 'PA-3220'"},
                max_per_hour=20,
            ),
        )

    async def signals(self):
        orders = await check_orders()
        if orders.value and orders.value > 0:
            return (Signal(
                title=f"Mirotech: {int(orders.value)} order(s) to ship",
                body="Late dispatch hits seller metrics harder than anything "
                     "else on eBay.",
                severity=Severity.HIGH),)
        stale = await check_stale_listings()
        skus = stale.evidence.get("skus", [])
        if len(skus) > 3:
            return (Signal(
                title=f"Mirotech: {len(skus)} listings over 60 days old",
                body=", ".join(skus[:8]) +
                     "\nUsually a pricing problem. I can pull comparables.",
                severity=Severity.NORMAL,
                suggests="mirotech.price_check"),)
        return ()

    def brief(self) -> str:
        return "Mirotech: listings, orders, stale stock"


MODULE = MirotechModule()
