"""purchasing — prepares, never pays.

`CAPABILITIES.md` §2.8, quoted verbatim:

    ### 2.8 `purchasing` — the one place ATLAS deliberately does less

    Apex advertises booking flights and ordering supplies "within defined
    parameters." ATLAS does all of it **except pressing pay**:

    - **Flights**: searches, compares, uses flycheap's own price history to say
      whether now is a good time, holds the fare if the airline offers a free
      hold, hands you a checkout link with everything filled in.
    - **ExampleCo supplies**: SSDs and RAM for firewall upgrades, shipping boxes,
      labels — finds the best price, builds the cart, one link.

    You tap, you pay. That's about 95% of the value for none of the risk.

    Money out is **Tier 3, permanently denied**, and this is the line in
    `BUILD_ORDER.md` you wrote down for yourself: *if I ask for a Tier 3 override,
    remind me of this line.* If you ever want ATLAS spending within a budget, that
    is a deliberate code change to `PERMANENT_DENY` that you make with a clear
    head — not a feature that grows into it.

Both actions below write nothing but a `purchasing_prepared` row and hand
back a real checkout link; they are Tier AUTO because nothing leaves an
account and the only side effect — a prepared-but-unbought row — undoes
cleanly by being marked withdrawn. There is no action here, or anywhere else
in this module, that captures a payment: that is the entire point of it.

Three honest gaps against the spec above, named rather than papered over:

* **No fare hold.** flycheap exposes no fare-search endpoint at all today
  (see `flights.py`), so there is nothing yet to hold. When flycheap adds
  one, holding a free fare is a Tier ASK action (it is externally visible to
  the airline) that belongs next to `prepare_flight`, not inside it.
* **Cart pricing needs eBay credentials.** `EBAY_CLIENT_ID` /
  `EBAY_CLIENT_SECRET` / `EBAY_REFRESH_TOKEN` are `optional_env`: this module
  loads without them (flight prep does not need eBay), and `prepare_cart`
  reports the gap plainly instead of guessing a price.
* **No single cart link.** eBay's Browse API has no cart-build endpoint (see
  `carts.py`), so `prepare_cart` leaves `checkout_url` null rather than
  picking one item's listing to stand in for a cart that was never built.
  Each item's own real link is still in `options`.

This module watches nothing on a schedule — it only acts when asked, and
"how many things have I prepared lately" is not worth polling for.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from ...kernel.contracts import Action, ActionResult, Tier
from ...kernel.state import execute_sql, fetch_one
from . import carts, flights


def _stamp(options: list[dict], source: str) -> list[dict]:
    """Every option that reaches storage carries where it came from and when
    it was seen — the reasoner is told a figure's source and age, never a
    bare number (invariant 6)."""
    now = datetime.now(UTC).isoformat()
    stamped = []
    for opt in options:
        opt = dict(opt)
        opt.setdefault("source", source)
        opt.setdefault("seen_at", now)
        stamped.append(opt)
    return stamped


def _price_of(option: dict) -> float:
    try:
        return float(option.get("price", float("inf")))
    except (TypeError, ValueError):
        return float("inf")


def _cheapest(options: list[dict]) -> dict:
    return min(options, key=_price_of)


async def _withdraw(token: str) -> ActionResult:
    """The rollback for both actions: mark the prepared row withdrawn.
    Nothing was ever paid, so there is nothing to reverse but the offer."""
    try:
        row_id = int(token)
    except (TypeError, ValueError):
        return ActionResult(
            False, f"cannot withdraw: {token!r} is not a purchasing_prepared id")
    await execute_sql(
        "UPDATE purchasing_prepared SET withdrawn = TRUE WHERE id = $1",
        row_id,
    )
    return ActionResult(True, f"withdrew prepared purchase #{row_id}")


async def prepare_flight(origin: str, destination: str, date: str) -> ActionResult:
    """Search, judge against history, store, hand back a checkout link.
    Writes nothing when the search itself cannot run.

    `flights.good_time` runs on every call, not just when there happens to be
    something to compare against: flycheap has no price-history source yet
    (the same gap as the fare search itself), so today it always answers
    "no history" — and that string is the honest answer, not a placeholder,
    so it goes in the result rather than being computed and discarded.
    """
    request = f"{origin} -> {destination} on {date}"
    options, reason = await flights.search_fares(origin, destination, date)
    if reason:
        return ActionResult(False, reason)
    if not options:
        return ActionResult(False, f"no fares found for {request}")

    stamped = _stamp(options, "flycheap")
    best = _cheapest(stamped)
    try:
        link = flights.checkout_link(best)
    except KeyError:
        link = None
    verdict = flights.good_time([], _price_of(best))

    row = await fetch_one(
        """INSERT INTO purchasing_prepared (kind, request, options, checkout_url)
           VALUES ('flight', $1, $2, $3) RETURNING id""",
        request, json.dumps(stamped, default=str), link,
    )
    token = str(row["id"]) if row else None
    return ActionResult(
        True,
        f"Prepared {len(stamped)} fare option(s) for {request}. Best: "
        f"{best.get('title', 'unlabeled option')} at "
        f"{best.get('price', '?')} {best.get('currency', '')}, read "
        f"{best.get('seen_at', 'unknown time')} ({verdict}). Checkout link "
        f"is ready — nothing has been paid.",
        evidence={"options": stamped, "checkout_url": link, "good_time": verdict},
        rollback_token=token,
    )


async def prepare_flight_dry(origin: str, destination: str, date: str) -> ActionResult:
    options, reason = await flights.search_fares(origin, destination, date)
    if reason:
        return ActionResult(False, reason)
    if not options:
        return ActionResult(
            False, f"no fares found for {origin} -> {destination} on {date}")
    return ActionResult(
        True,
        f"would prepare {len(options)} fare option(s) for "
        f"{origin} -> {destination} on {date}",
    )


def _parse_items(items: str) -> list[str]:
    return [part.strip() for part in items.split(",") if part.strip()]


async def prepare_cart(items: str) -> ActionResult:
    """Find the best eBay price per item and store the results. Writes
    nothing when the search itself cannot run. No single checkout link: see
    the module docstring on why `checkout_url` stays null for carts."""
    parsed = _parse_items(items)
    if not parsed:
        return ActionResult(False, "no items given")

    options, reason = await carts.find_prices(parsed)
    if reason:
        return ActionResult(False, reason)
    if not options:
        return ActionResult(False, f"no prices found for: {items}")

    stamped = _stamp(options, "ebay")
    total = sum(_price_of(o) for o in stamped if _price_of(o) != float("inf"))
    # eBay's Browse API has no cart-build endpoint (see carts.py) to make a
    # single combined link from. A checkout_url here pointing at one
    # arbitrary item would read as "here is your cart" while being one item
    # out of several — worse than no link. Each option already carries its
    # own real listing url; checkout_url stays null rather than picking one
    # of them to stand in for a cart that was never built.
    checkout = None
    # A figure on screen carries the time it was read (invariant 6); a cart
    # total is read across several listings, so the latest of them is the
    # honest bound on how fresh "total" is.
    read_times = sorted(o["seen_at"] for o in stamped if o.get("seen_at"))
    latest_read = read_times[-1] if read_times else "unknown time"

    row = await fetch_one(
        """INSERT INTO purchasing_prepared (kind, request, options, checkout_url)
           VALUES ('cart', $1, $2, $3) RETURNING id""",
        items, json.dumps(stamped, default=str), checkout,
    )
    token = str(row["id"]) if row else None
    return ActionResult(
        True,
        f"Prepared {len(stamped)} of {len(parsed)} item(s), total "
        f"~{total:.2f}, prices read as of {latest_read}. eBay has no single "
        f"cart link — each item has its own listing link below. Nothing "
        f"has been paid.",
        evidence={"options": stamped, "checkout_url": checkout},
        rollback_token=token,
    )


async def prepare_cart_dry(items: str) -> ActionResult:
    parsed = _parse_items(items)
    if not parsed:
        return ActionResult(False, "no items given")
    return ActionResult(True, f"would search eBay prices for {len(parsed)} item(s)")


class PurchasingModule:
    name = "purchasing"
    title = "Purchasing"
    requires_env = ()
    # Unlocks purchasing.prepare_cart; flights need no env at all (and get
    # no fare data either, until flycheap exposes a search endpoint).
    optional_env = ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_REFRESH_TOKEN")
    setup_note = (
        "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET / EBAY_REFRESH_TOKEN unlock "
        "purchasing.prepare_cart; purchasing.prepare_flight needs nothing "
        "but has no fare source to search until flycheap exposes one."
    )

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return ()

    def actions(self):
        return (
            Action(
                name="purchasing.prepare_flight",
                tier=Tier.AUTO,
                summary="Search fares, judge the price against history, and "
                        "prepare a checkout link. Never pays.",
                run=prepare_flight,
                dry_run=prepare_flight_dry,
                rollback=_withdraw,
                params={
                    "origin": "departure airport or city",
                    "destination": "arrival airport or city",
                    "date": "travel date, e.g. 2026-10-01",
                },
            ),
            Action(
                name="purchasing.prepare_cart",
                tier=Tier.AUTO,
                summary="Find the best eBay price for each item and store "
                        "each listing's link. Never pays.",
                run=prepare_cart,
                dry_run=prepare_cart_dry,
                rollback=_withdraw,
                params={
                    "items": "comma-separated items to price, e.g. "
                             "'240GB SSD, Cat6 cable 50ft'",
                },
            ),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = PurchasingModule()
