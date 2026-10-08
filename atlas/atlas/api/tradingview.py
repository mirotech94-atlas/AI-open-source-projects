"""TradingView alerts in (`atlas/modules/markets/tradingview.py`).

    POST /hooks/tradingview

Public, with no session: TradingView's servers have none to present, and
TradingView cannot sign a request. The gate is a long random secret Miro
puts in the alert's JSON body (`"secret"`), compared in constant time with
TRADINGVIEW_WEBHOOK_SECRET. In order, each refusal saying nothing about the
next:

    429  in place of any refusal below, once one address has had
         MAX_REFUSALS refusals in WINDOW
    403  TRADINGVIEW_ALLOWED_IPS is set and the address is not in it
    503  the secret is not configured (unset, or shorter than 32 characters)
    413  a body over 8 KB
    415  not application/json (TradingView sends that for a JSON message)
    400  not a JSON object
    401  a missing or wrong secret -- no detail
    422  a field outside the closed set or its rule (the field's name only)
    200  stored, or a duplicate inside ten minutes (`"duplicate": true`)

The client address is the one Caddy forwards, believed only from Caddy
itself (`peers.effective_client`). The secret and the raw body are never
logged or stored. Nothing here acts: the alert is stored, and that is all.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from ..modules.markets import tradingview as tv
from .peers import PerAddressLimiter, effective_client

router = APIRouter()
log = logging.getLogger("atlas.tradingview")

PATH = "/hooks/tradingview"
# Only REFUSED requests count toward the limit. Every real alert comes from
# one of TradingView's four shared addresses, and any TradingView user can
# point an alert at this URL from the same four: counting every request
# would let someone else's wrong-secret spam, or Miro's own burst of alerts
# at a daily close, get his real alerts a 429 -- and TradingView does not
# retry. So a request with the right secret is never counted and never
# refused for the rate: past the limit, every refusal is a 429 instead,
# which still stops a flood being told anything. The secret is 32+ random
# characters, so being answered 429 rather than 401 while guessing changes
# nothing about the odds.
MAX_REFUSALS = 30
WINDOW = 60.0
_requests = PerAddressLimiter(MAX_REFUSALS, WINDOW)
# TradingView cancels a webhook that takes longer than three seconds.
DB_TIMEOUT_S = 2.5


def _allowed_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None:
    """TRADINGVIEW_ALLOWED_IPS as networks; None when unset. A malformed
    entry is skipped and logged; a list with nothing valid in it admits no
    one (it was meant to restrict, so it fails closed)."""
    raw = os.getenv(tv.ALLOWED_IPS_ENV, "").strip()
    if not raw:
        return None
    nets = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.error("ignoring malformed address in %s: %r", tv.ALLOWED_IPS_ENV, item)
    return nets


def _admitted(client: str) -> bool:
    nets = _allowed_networks()
    if nets is None:
        return True
    try:
        addr = ipaddress.ip_address(client)
    except ValueError:
        return False
    return any(addr in net for net in nets)


async def _body(request: Request) -> bytes | None:
    """The body, or None when it is over MAX_BODY. Content-Length is checked
    first, and the stream is capped as it is read, because a chunked body
    has no length to check."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > tv.MAX_BODY:
                return None
        except ValueError:
            return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > tv.MAX_BODY:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _no_constants(name: str) -> float:
    raise ValueError(name)       # NaN / Infinity are not JSON


@router.post(PATH)
async def tradingview_hook(request: Request) -> Response:
    client = effective_client(request)

    def refuse(response: Response) -> Response:
        # Only a refusal counts, and past the limit every refusal is a 429.
        if _requests.limited(client):
            return PlainTextResponse("too many requests", status_code=429)
        _requests.hit(client)
        return response

    if not _admitted(client):
        return refuse(PlainTextResponse("forbidden", status_code=403))
    if not tv.configured():
        return refuse(PlainTextResponse("not configured", status_code=503))

    body = await _body(request)
    if body is None:
        return refuse(PlainTextResponse("too large", status_code=413))
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media != "application/json":
        return refuse(PlainTextResponse("json only", status_code=415))
    try:
        payload = json.loads(body, parse_constant=_no_constants)
    except (ValueError, UnicodeDecodeError):
        return refuse(PlainTextResponse("invalid json", status_code=400))
    if not isinstance(payload, dict):
        return refuse(PlainTextResponse("invalid json", status_code=400))

    given = payload.get("secret")
    expected = tv.secret()
    # Bytes on both sides: compare_digest refuses a non-ASCII str, and that
    # must be a 401, not a 500.
    if not isinstance(given, str) or not hmac.compare_digest(
            given.encode("utf-8"), expected.encode("utf-8")):
        return refuse(Response(status_code=401))

    # Past the secret nothing counts toward the limit: this is Miro's alert.
    try:
        alert = tv.parse(payload)
    except tv.Invalid as bad:
        return JSONResponse({"error": "invalid field", "field": bad.field},
                            status_code=422)

    try:
        row_id = await asyncio.wait_for(tv.store(alert), DB_TIMEOUT_S)
    except Exception as exc:                            # noqa: BLE001
        log.error("tradingview alert for %s not stored (%s)",
                  alert.ticker, type(exc).__name__)
        return PlainTextResponse("not stored", status_code=503)
    if row_id is None:
        log.info("tradingview alert for %s: duplicate inside %s, not stored",
                 alert.ticker, tv.DEDUP_WINDOW)
        return JSONResponse({"ok": True, "duplicate": True})
    log.info("tradingview alert #%d stored for %s", row_id, alert.ticker)
    return JSONResponse({"ok": True, "duplicate": False})
