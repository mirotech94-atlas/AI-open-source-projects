"""Best-price lookup across eBay for supply items (SSDs, RAM, boxes, labels).

This copies the minimal token-exchange-and-search shape from
`atlas/modules/mirotech/__init__.py` (`_token` / `_api`) rather than
importing it. Mirotech's `requires_env` makes the whole reselling module
refuse to load without `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` /
`EBAY_REFRESH_TOKEN` — correct for a module whose only job is eBay. This
module also uses those names (`optional_env`, not `requires_env`): flight
prep must keep working when eBay credentials are absent, so the module has
to load either way and simply report that cart pricing is unavailable.
"""
from __future__ import annotations

import base64
import os
from datetime import UTC, datetime

import httpx

from .._http import env_missing, explain

API = "https://api.ebay.com"


async def _token() -> str:
    creds = base64.b64encode(
        f"{os.environ['EBAY_CLIENT_ID']}:{os.environ['EBAY_CLIENT_SECRET']}".encode()
    ).decode()
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(
            f"{API}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": os.environ["EBAY_REFRESH_TOKEN"],
                "scope": "https://api.ebay.com/oauth/api_scope/sell.inventory",
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def _search_one(token: str, query: str) -> dict | None:
    """The cheapest priced, linkable listing for one item, or None."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{API}/buy/browse/v1/item_summary/search",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"q": query, "limit": 25},
        )
        r.raise_for_status()
        data = r.json()
    priced = [
        s for s in data.get("itemSummaries", [])
        if s.get("price") and s.get("itemWebUrl")
    ]
    if not priced:
        return None
    best = min(priced, key=lambda s: float(s["price"]["value"]))
    return {
        "title": best.get("title", query),
        "price": float(best["price"]["value"]),
        "currency": best["price"].get("currency", "USD"),
        "url": best["itemWebUrl"],
        "source": "ebay",
        "seen_at": datetime.now(UTC).isoformat(),
    }


async def find_prices(items: list[str]) -> tuple[list[dict], str | None]:
    """Best eBay price per item, via the Browse API.

    A missing credential or a dead token is a reason to say so
    (`([], reason)`), not to guess a price. A single item that has no
    listings is not an error for the batch — it is just skipped — but a
    batch that finds nothing at all for any item is reported as a failure
    rather than a silent empty cart.
    """
    missing = env_missing("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_REFRESH_TOKEN")
    if missing:
        return [], f"eBay search not available: missing env {', '.join(missing)}"

    try:
        token = await _token()
    except Exception as exc:  # noqa: BLE001
        return [], f"eBay token request failed: {explain(exc)}"

    results: list[dict] = []
    for item in items:
        try:
            found = await _search_one(token, item)
        except Exception as exc:  # noqa: BLE001
            return [], f"eBay search failed for {item!r}: {explain(exc)}"
        if found is not None:
            results.append(found)

    if not results:
        return [], f"no eBay results for any of: {', '.join(items)}"
    return results, None
