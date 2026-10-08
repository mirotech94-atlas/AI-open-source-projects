"""Mapfront's Stripe account, in (`atlas/modules/agency/payments.py`).

    POST /webhooks/stripe-mapfront

Public, one exact path. Stripe signs every delivery: the `Stripe-Signature`
header carries `t=<unix time>` and one or more `v1=<hex>`, each an
HMAC-SHA256 of "<t>.<raw body>" under the endpoint's signing secret,
`MAPFRONT_STRIPE_WEBHOOK_SECRET` (set with set-secret). In order:

    503  the secret is not set -- nothing is read
    413  a body over 256 KB
    400  no usable Stripe-Signature header
    401  no v1 signature matches, or the timestamp is more than five minutes
         from now (a replayed old delivery) -- no detail
    400  a signed body that is not a JSON object
    200  recorded, a duplicate event id, or an event type ATLAS does not
         record (Stripe stops retrying on a 2xx)
    500  recording failed after the claim; the claim is released, so
         Stripe's own retry runs it again

Neither the secret nor the raw body is ever logged or stored: a log line
names the event type and id, nothing else. Nothing here calls Stripe or
acts on the account -- it reads and records. Money out stays Tier 3.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

router = APIRouter()
log = logging.getLogger("atlas.stripe_mapfront")

PATH = "/webhooks/stripe-mapfront"
SECRET_ENV = "MAPFRONT_STRIPE_WEBHOOK_SECRET"
MAX_BODY = 256 * 1024
TOLERANCE_S = 300


def _secret() -> str:
    return os.getenv(SECRET_ENV, "").strip()


def verify(body: bytes, header: str, secret: str, *, now: float | None = None) -> bool:
    """Stripe's v1 scheme. False on anything malformed, never an exception."""
    if not secret or not header:
        return False
    stamp, signatures = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            stamp = value
        elif key == "v1" and value:
            signatures.append(value)
    if not stamp or not stamp.isdigit() or not signatures:
        return False
    if abs((now if now is not None else time.time()) - int(stamp)) > TOLERANCE_S:
        return False
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + body,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


async def _body(request: Request) -> bytes | None:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(PATH)
async def stripe_mapfront(request: Request) -> Response:
    secret = _secret()
    if not secret:
        return PlainTextResponse("not configured", status_code=503)
    body = await _body(request)
    if body is None:
        return PlainTextResponse("too large", status_code=413)
    header = request.headers.get("stripe-signature", "")
    if "v1=" not in header or "t=" not in header:
        return PlainTextResponse("no signature", status_code=400)
    if not verify(body, header, secret):
        return Response(status_code=401)
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return PlainTextResponse("invalid json", status_code=400)
    if not isinstance(event, dict):
        return PlainTextResponse("invalid json", status_code=400)

    from ..modules.agency import payments
    kind, event_id = str(event.get("type") or ""), str(event.get("id") or "")
    try:
        recorded, what = await payments.handle(event)
    except Exception as exc:                            # noqa: BLE001
        log.error("stripe-mapfront %s %s not recorded (%s)", kind, event_id,
                  type(exc).__name__)
        return PlainTextResponse("not recorded", status_code=500)
    log.info("stripe-mapfront %s %s: %s", kind, event_id,
             "recorded" if recorded else what)
    return JSONResponse({"ok": True, "recorded": recorded,
                         "duplicate": what == "duplicate"})
