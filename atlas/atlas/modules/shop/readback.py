"""Read-back for `shop.create_payment_link` (RA-D1). Reads only: the payment
link by the id the run recorded, and its line items, by GET with the same
shop key the run used (STRIPE_SHOP_<ACCOUNT>_KEY). Checked against what was
approved: active, tagged with the approved product, one item at the
approved price in USD, and a redirect to ATLAS's /deliver/ page after
payment. See docs/verification.md.
"""
from __future__ import annotations

from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    classify,
    contradicted,
    unknown,
    verified,
    verifier,
)
from . import ACCOUNTS, API, shop_key


def _ids(params: dict[str, Any], result: ActionResult) -> tuple[str, str, str]:
    """(account, link id, product key) from the rollback token, else the
    params and the evidence."""
    token = result.rollback_token or ""
    parts = token.split(":", 2)
    if len(parts) == 3 and all(parts):
        return parts[0], parts[1], parts[2]
    return (str(params.get("account") or ""),
            str((result.evidence or {}).get("payment_link") or ""),
            str(params.get("product_key") or ""))


async def _get(key: str, path: str, **params: Any) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}/{path}", headers={"Authorization": f"Bearer {key}"},
                        params=params)
        r.raise_for_status()
        body = r.json()
    return body if isinstance(body, dict) else {}


@verifier("shop.create_payment_link")
async def verify_create_link(params: dict[str, Any], result: ActionResult
                             ) -> Verification:
    account, link_id, product = _ids(params, result)
    if not link_id.startswith("plink_"):
        return unknown("the run recorded no payment link id")
    if account not in ACCOUNTS:
        return unknown(f"unknown shop account {account!r}", FailureClass.VALIDATION)
    if params.get("account") and params["account"] != account:
        return contradicted("the link was made on another account than approved",
                            account=account, approved=params.get("account"))
    key = shop_key(account)
    if not key:
        return unknown(f"no shop key for {account} to read the link with",
                       FailureClass.AUTH, payment_link=link_id)
    try:
        link = await _get(key, f"payment_links/{link_id}")
        items = await _get(key, f"payment_links/{link_id}/line_items", limit=10)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return contradicted(f"Stripe has no payment link {link_id} on {account}",
                                payment_link=link_id)
        return unknown(f"could not read payment link {link_id}", classify(exc),
                       payment_link=link_id)
    except httpx.HTTPError as exc:
        return unknown(f"Stripe did not answer for {link_id}", classify(exc),
                       payment_link=link_id)

    rows = [i for i in (items.get("data") or []) if isinstance(i, dict)]
    price = (rows[0].get("price") or {}) if len(rows) == 1 else {}
    redirect = ((link.get("after_completion") or {}).get("redirect") or {}).get("url")
    evidence = {"payment_link": link_id, "account": account, "url": link.get("url"),
                "active": link.get("active"),
                "product": (link.get("metadata") or {}).get("atlas_product"),
                "items": len(rows), "unit_amount": price.get("unit_amount"),
                "currency": price.get("currency"),
                "quantity": rows[0].get("quantity") if len(rows) == 1 else None}
    wrong: list[str] = []
    if link.get("id") not in (None, link_id):
        wrong.append("a different link id")
    if link.get("active") is not True:
        wrong.append("inactive")
    if product and evidence["product"] != product:
        wrong.append(f"tagged {evidence['product']!r}, not {product!r}")
    if len(rows) != 1:
        wrong.append(f"{len(rows)} line items, not 1")
    else:
        approved = params.get("price_cents")
        if approved is not None and evidence["unit_amount"] != int(approved):
            wrong.append(f"priced {evidence['unit_amount']}, not {int(approved)}")
        if evidence["currency"] != "usd":
            wrong.append(f"in {evidence['currency']!r}, not usd")
        if evidence["quantity"] != 1:
            wrong.append(f"quantity {evidence['quantity']}, not 1")
    if "/deliver/" not in str(redirect or ""):
        wrong.append("no redirect to the /deliver/ page after payment")
    if wrong:
        return contradicted(f"payment link {link_id}: " + "; ".join(wrong), **evidence)
    return verified(**evidence)
