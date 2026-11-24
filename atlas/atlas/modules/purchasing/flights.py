"""Fare search, price-history judgement and checkout links for flights.

`search_fares` is supposed to call flycheap's own fare-search API, the same
way `exampleco._api` calls eBay's. It cannot: `atlas/modules/flycheap/__init__.py`
documents, in detail, that the *only* endpoint flycheap actually exposes is
the public `/api/health` — built for uptime monitors, not fare lookups. The
previous flycheap module invented admin endpoints that do not exist
(`/api/admin/prices/freshness` and friends) and shipped broken by
construction. This module does not repeat that: there is no fare-search
route to reuse here, so `search_fares` says so and returns nothing, rather
than guessing a path.

If flycheap ever adds a fare-search endpoint, this is where it gets wired
in — call the same base URLs (`FLYCHEAP_PUBLIC_URL` / `FLYCHEAP_CLIENT_URL`)
flycheap's module already reads from the environment.
"""
from __future__ import annotations

# The reason `search_fares` always fails today. Named so a future patch that
# wires up a real endpoint has one place to remove this from.
_NO_FARE_LOOKUP = (
    "flycheap exposes no fare-search endpoint. Its module "
    "(atlas/modules/flycheap/__init__.py) documents that the only route it "
    "calls is the public /api/health, built for uptime monitoring — there "
    "is no price lookup to call, and none is invented here."
)


async def search_fares(
    origin: str, destination: str, date: str
) -> tuple[list[dict], str | None]:
    """Fares for one route and date, via flycheap's own API.

    Never guesses (invariant 6): if flycheap has nothing to call, this
    returns `([], reason)` rather than inventing a shape that looks like
    fare data.
    """
    return [], f"flycheap fare search not available: {_NO_FARE_LOOKUP}"


def good_time(history: list[float], now_price: float) -> str:
    """Compare `now_price` against the 30-day median of `history`.

    Three branches, and only three: below the median, above (or at) it, or
    no history to compare against at all. Never estimates a median from an
    empty history (invariant 6) — "no history" is the honest answer.
    """
    if not history:
        return "no history"
    ordered = sorted(history)
    mid = len(ordered) // 2
    median = (
        ordered[mid]
        if len(ordered) % 2
        else (ordered[mid - 1] + ordered[mid]) / 2
    )
    verdict = "below" if now_price < median else "above"
    return f"{verdict} the 30-day median ({now_price:g} vs {median:g})"


def checkout_link(option: dict) -> str:
    """The airline/flycheap deep link carried on the option, unchanged.

    Not rewritten, not re-signed, not shortened — whatever `option["url"]`
    is, that is what the owner taps to pay.
    """
    return option["url"]
