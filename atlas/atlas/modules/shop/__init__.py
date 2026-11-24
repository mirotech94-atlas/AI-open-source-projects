"""Shop -- a product for every reel, and the money it brings in.

the owner, 2026-09-28: "create own plan to make money per reel". The plan lives
in his vault (Plans/Money per reel.md); this is its Seller. Each reel type
points to one product; a caption carries that product's call to action
(link in bio -- caption links are not clickable on Instagram); a product
is sold through a Stripe Payment Link that the owner approved on a Tier 2 card;
a digital product is handed over at /deliver/{checkout session}, only after
Stripe says that session is paid.

Money comes IN here. Nothing refunds, pays out or changes a price after
the fact: those stay denied or his to do in Stripe.

The Stripe key is STRIPE_SHOP_<ACCOUNT>_KEY -- a restricted key the owner makes
with write access to Products, Prices and Payment Links and read access to
Checkout Sessions. ATLAS's existing Stripe keys are read-only and are not
used to write.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)

API = "https://api.stripe.com/v1"
ACCOUNTS = ("owner",)   # his own account; guardian/flycheap removed 2026-10-29
REEL_TYPES = ("business", "tech", "travel", "stories")

# What each reel type says at the end of its caption. A product that is not
# live yet gets the follow line instead: a call to action never points at
# a link that doesn't exist.
FOLLOW = "Follow for the next one."
CTA = {
    "business": "All 30 lessons in one guide: link in bio.",
    "tech": "Get the Claude + Obsidian vault kit: link in bio.",
    "travel": FOLLOW,
    "stories": "Follow for the next part.",
}


def shop_key(account: str) -> str:
    return os.getenv(f"STRIPE_SHOP_{account.upper()}_KEY", "").strip()


def reel_type_of(source: str, kind: str = "") -> str:
    """The reel type from its draft's source, decided in code."""
    s = (source or "").lower()
    if "business-reels" in s or s.startswith("calendar:business"):
        return "business"
    if s.startswith(("series:", "import:")) or "reels-" in s or kind == "story_script":
        return "stories"
    if "flycheap" in s or "fare" in s or "travel" in s:
        return "travel"
    if "prompt" in s or "tech" in s:
        return "tech"
    return "stories"


async def product_for(reel_type: str) -> dict | None:
    from ...kernel.state import fetch_one
    try:
        return await fetch_one(
            """SELECT key, name, payment_link_url, status FROM shop_products
                WHERE reel_type = $1 ORDER BY key LIMIT 1""", reel_type)
    except Exception:                                   # noqa: BLE001
        return None


async def caption_with_cta(media_id: int, caption: str) -> str:
    """The caption with its reel type's call to action, placed before the AI
    label (which stays last). Unchanged when it already carries one."""
    from ...kernel.state import fetch_one
    from ..content.drafts import AI_LABEL
    try:
        row = await fetch_one(
            """SELECT d.source, d.kind FROM content_media m
                 JOIN content_drafts d ON d.id = m.draft_id WHERE m.id = $1""",
            media_id)
    except Exception:                                   # noqa: BLE001
        row = None
    rtype = reel_type_of(str((row or {}).get("source") or ""),
                         str((row or {}).get("kind") or ""))
    product = await product_for(rtype)
    live = bool(product and product.get("status") == "live"
                and product.get("payment_link_url"))
    line = CTA.get(rtype, FOLLOW) if (live or rtype == "stories") else FOLLOW
    body = caption.rstrip()
    if line in body or "link in bio" in body.lower():
        return caption
    label = ""
    if body.endswith(AI_LABEL):
        body, label = body[: -len(AI_LABEL)].rstrip(), AI_LABEL
    out = f"{body}\n\n{line}"
    return f"{out}\n\n{label}" if label else out


# ------------------------------------------------------------ the Stripe card

_KEY = re.compile(r"^[a-z0-9_]{2,40}$")


def _problem(product_key: str, account: str, price_cents: int) -> str | None:
    if not _KEY.match(product_key or ""):
        return "product_key must be a short lowercase key"
    if account not in ACCOUNTS:
        return f"account must be one of {', '.join(ACCOUNTS)}"
    if not (100 <= int(price_cents or 0) <= 50000):
        return "price_cents must be between 100 and 50000"
    if not shop_key(account):
        return (f"no shop key for the {account} Stripe account yet "
                f"(STRIPE_SHOP_{account.upper()}_KEY)")
    return None


async def create_link_dry(product_key: str = "", account: str = "",
                          price_cents: int = 0) -> ActionResult:
    if (why := _problem(product_key, account, price_cents)) is not None:
        return ActionResult(False, why)
    from ...kernel.state import fetch_one
    p = await fetch_one("SELECT name, status FROM shop_products WHERE key = $1",
                        product_key)
    if p is None:
        return ActionResult(False, f"no product {product_key!r} in the shop")
    if p["status"] == "live":
        return ActionResult(False, f"{p['name']} already has a live payment link")
    return ActionResult(True, (f"would create {p['name']} at ${int(price_cents)/100:.2f} "
                               f"on the {account} Stripe account, with a payment "
                               f"link that delivers after a paid checkout"))


async def create_link(product_key: str = "", account: str = "",
                      price_cents: int = 0) -> ActionResult:
    if (why := _problem(product_key, account, price_cents)) is not None:
        return ActionResult(False, why)
    from ...kernel.state import execute_sql, fetch_one
    p = await fetch_one("SELECT name, status FROM shop_products WHERE key = $1",
                        product_key)
    if p is None or p["status"] == "live":
        return ActionResult(False, "no such product, or it is already live")
    host = os.getenv("ATLAS_HOST", "").strip()
    if not host:
        return ActionResult(False, "ATLAS_HOST is unset: no delivery URL to send buyers to")
    auth = {"Authorization": f"Bearer {shop_key(account)}"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/products", headers=auth,
                         data={"name": str(p["name"]),
                               "metadata[atlas_product]": product_key})
        r.raise_for_status()
        prod = r.json()["id"]
        r = await c.post(f"{API}/prices", headers=auth, data={
            "product": prod, "unit_amount": str(int(price_cents)), "currency": "usd"})
        r.raise_for_status()
        price = r.json()["id"]
        r = await c.post(f"{API}/payment_links", headers=auth, data={
            "line_items[0][price]": price, "line_items[0][quantity]": "1",
            "metadata[atlas_product]": product_key,
            "after_completion[type]": "redirect",
            "after_completion[redirect][url]":
                f"https://{host}/deliver/{{CHECKOUT_SESSION_ID}}"})
        r.raise_for_status()
        link = r.json()
    await execute_sql(
        """UPDATE shop_products SET status = 'live', stripe_account = $2,
                  payment_link_id = $3, payment_link_url = $4, price_cents = $5
            WHERE key = $1""",
        product_key, account, link["id"], link["url"], int(price_cents))
    return ActionResult(True, f"{p['name']} is on sale: {link['url']} -- put it in the bio",
                        evidence={"payment_link": link["id"]},
                        rollback_token=f"{account}:{link['id']}:{product_key}")


async def deactivate_link(token: str) -> ActionResult:
    """Stop selling: the payment link is switched off, never deleted."""
    try:
        account, link_id, product_key = token.split(":", 2)
    except ValueError:
        return ActionResult(False, "unreadable rollback token")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/payment_links/{link_id}",
                         headers={"Authorization": f"Bearer {shop_key(account)}"},
                         data={"active": "false"})
        r.raise_for_status()
    from ...kernel.state import execute_sql
    await execute_sql("UPDATE shop_products SET status = 'paused' WHERE key = $1",
                      product_key)
    return ActionResult(True, f"payment link {link_id} switched off")


# ------------------------------------------------------------ sales, read

async def sales_since(days: int = 7, *, since: datetime | None = None,
                      until: datetime | None = None,
                      ) -> tuple[dict[str, dict], str | None]:
    """Paid checkouts per product over `days` (or exactly `since`..`until`,
    for the Analyst's window), read from Stripe. ({}, why) when it can't be
    read -- never zero for unknown."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        "SELECT key, name, stripe_account, payment_link_id FROM shop_products "
        "WHERE payment_link_id IS NOT NULL")
    out: dict[str, dict] = {}
    import time
    start = int(since.timestamp()) if since else int(time.time()) - days * 86400
    window: dict[str, int] = {"created[gte]": start}
    if until is not None:
        window["created[lte]"] = int(until.timestamp())
    for r in rows:
        key = shop_key(str(r["stripe_account"] or ""))
        if not key:
            return {}, f"no shop key for {r['stripe_account']}"
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.get(f"{API}/checkout/sessions",
                               headers={"Authorization": f"Bearer {key}"},
                               params={"payment_link": r["payment_link_id"],
                                       **window, "limit": 100,
                                       "status": "complete"})
        if resp.status_code != 200:
            return {}, f"Stripe answered {resp.status_code} for {r['name']}"
        if resp.json().get("has_more"):
            # One page is 100 sessions; a count cut at a page is a wrong count.
            return {}, f"more than 100 checkouts for {r['name']}: not paged yet"
        paid = [s for s in resp.json().get("data", []) if s.get("payment_status") == "paid"]
        out[str(r["key"])] = {"name": r["name"], "sales": len(paid),
                              "revenue_cents": sum(int(s.get("amount_total") or 0)
                                                   for s in paid)}
    return out, None


async def check_sales() -> Observation:
    try:
        sales, why = await sales_since(7)
    except Exception as exc:                            # noqa: BLE001
        return Observation("sales_7d", Health.UNKNOWN,
                           detail=f"Stripe could not be read: {type(exc).__name__}")
    if why:
        return Observation("sales_7d", Health.UNKNOWN, detail=why)
    total = sum(v["revenue_cents"] for v in sales.values())
    parts = [f"{v['name']}: {v['sales']} sale(s), ${v['revenue_cents']/100:.2f}"
             for v in sales.values()]
    return Observation("sales_7d", Health.OK, total / 100, "usd",
                       ("; ".join(parts) or "no product on sale yet") + " (last 7 days)",
                       evidence={"sales": sales})


async def check_sales_count() -> Observation:
    """How many paid checkouts in the last 7 days, as a number of its own:
    `sales_7d` carries the dollars as its value, and a figure on the home
    page is a check's value, never a count lifted out of its sentence. Its
    own Stripe read, with its own time; unknown, never zero, when it
    cannot be read."""
    try:
        sales, why = await sales_since(7)
    except Exception as exc:                            # noqa: BLE001
        return Observation("sales_count_7d", Health.UNKNOWN,
                           detail=f"Stripe could not be read: {type(exc).__name__}")
    if why:
        return Observation("sales_count_7d", Health.UNKNOWN, detail=why)
    count = sum(int(v["sales"]) for v in sales.values())
    return Observation("sales_count_7d", Health.OK, float(count), "sales, 7 d",
                       f"{count} paid sale(s) in the last 7 days",
                       evidence={"sales": {k: v["sales"] for k, v in sales.items()}})


class ShopModule:
    name = "shop"
    title = "Shop"
    requires_env: tuple[str, ...] = ()

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("sales_7d", check_sales, timedelta(hours=6), Severity.INFO,
                      "paid sales per product over the last 7 days, from Stripe",
                      baseline_sigma=None),
                Check("sales_count_7d", check_sales_count, timedelta(hours=6),
                      Severity.INFO, "how many paid sales in the last 7 days, from Stripe",
                      baseline_sigma=None))

    def actions(self):
        return (
            Action(name="shop.create_payment_link", tier=Tier.ASK,
                   summary="Put a product on sale with a Stripe payment link",
                   run=create_link, dry_run=create_link_dry, rollback=deactivate_link,
                   params={"product_key": "the shop product, e.g. business_guide",
                           "account": "owner: the Stripe account",
                           "price_cents": "the price in cents, e.g. 1900"},
                   card_only=True, max_per_hour=4),
        )

    async def signals(self):
        return ()


MODULE = ShopModule()
