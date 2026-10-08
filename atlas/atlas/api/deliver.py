"""A buyer's download, after Stripe says they paid (modules/shop).

    GET /deliver/{checkout_session_id}

Public, like /media: the session id Stripe appended to the redirect is the
unguessable part, and it is only half the gate. The other half is Stripe:
the session is read back with the shop key, and the file is handed over only
when it is `paid` and came from this product's own payment link. Anything
else is a plain 404 that says nothing about why.
"""
from __future__ import annotations

import pathlib
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from .peers import PerAddressLimiter, effective_client

router = APIRouter()

# Per source address, in-process (audit #20). A buyer opens their link a
# handful of times; anything past this is someone walking session ids, and
# every request here costs a Stripe API call per shop account.
MAX_REQUESTS = 12
WINDOW = 600.0
_requests = PerAddressLimiter(MAX_REQUESTS, WINDOW)

_SESSION = re.compile(r"^cs_(?:live|test)_[A-Za-z0-9]{10,200}$")
ROOT = pathlib.Path("data/products").resolve()


async def _paid_product(session_id: str) -> dict | None:
    from ..kernel.state import fetch_all
    from ..modules.shop import API, shop_key
    products = await fetch_all(
        """SELECT key, name, stripe_account, payment_link_id, file_path
             FROM shop_products WHERE payment_link_id IS NOT NULL""")
    by_link = {str(p["payment_link_id"]): p for p in products}
    for account in {str(p["stripe_account"]) for p in products}:
        key = shop_key(account)
        if not key:
            continue
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{API}/checkout/sessions/{session_id}",
                            headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            continue
        s = r.json()
        product = by_link.get(str(s.get("payment_link") or ""))
        if product and s.get("payment_status") == "paid":
            return dict(product)
    return None


@router.get("/deliver/{session_id}")
async def deliver(session_id: str, request: Request):
    if not _requests.allow(effective_client(request)):
        raise HTTPException(429, "too many requests; try again in a few minutes")
    if not _SESSION.match(session_id or ""):
        raise HTTPException(404, "not found")
    product = await _paid_product(session_id)
    if product is None or not product.get("file_path"):
        raise HTTPException(404, "not found")
    path = pathlib.Path(str(product["file_path"])).resolve()
    if ROOT not in path.parents or not path.is_file():
        return HTMLResponse("<p>Thank you -- your purchase went through, but the file "
                            "isn't ready here yet. Reply to your receipt email and it "
                            "will be sent to you.</p>", status_code=200)
    from ..kernel.audit import audit
    await audit("shop.delivered", str(product["key"]), {"session": session_id[:14]})
    return FileResponse(path, filename=path.name)
