"""The public inbound-webhook route for Task 41's duplex providers.

`POST /inbound/{kind}/{token}` — deliberately NOT under `/api/v1`: the URL
goes into a third party's own webhook-config field (PagerDuty's "Extension
URL", Discord's "Interactions Endpoint URL", Telegram's `setWebhook`, Home
Assistant's REST-command `resource`), and none of those expect a versioned
API prefix. It sits next to Task 40's `/api/v1/hooks/{token}` (still used by
nothing — no phase-1 provider is inbound-capable — and left alone rather
than folded into this route, since a live third-party webhook config
pointing at it must keep working).

No session, no CSRF: the per-integration `inbound_token` (minted by
`routers/integrations.py`'s upsert route for any `wants_inbound_token`
direction) IS the credential, checked with a constant-time compare so a
timing attack can't shave characters off it. Never distinguishes "wrong
token" from "token that used to work" — both 404 with the same body, so a
prober can't learn anything from the response.

Rate-limited per token (60/60s — higher than the phase-1 hook's 30/60s,
since Telegram can burst several commands from one paired chat in a
minute) rather than per kind, so one noisy tenant's webhook can't starve
another tenant's.

Config/secret write-back: a provider's `inbound()` may need to remember
something (Telegram's pairing binds `chat_id`; a future provider might
rotate its own secret) — same compare-and-reassign contract as
`registry.watchdog_job`'s docstring, since a plain JSON column's in-place
mutation is otherwise invisible to SQLAlchemy's flush. The snapshot is a
DEEP copy for the reason spelled out there: ruling (b)'s
`config["_sync"]` bookkeeping is one level down, and a shallow snapshot
would compare equal to it and drop the write.

Fix round 1 also adds, on this route:

- finding 6: `request.client.host` rides in on `SendContext.ip`, so an
  integration-driven ack/quarantine is audited with a real client IP
  instead of an empty string.
- finding 7: a non-ASCII token is rejected with the SAME uniform 404 as a
  wrong one, before it can reach `hmac.compare_digest` (which raises
  `TypeError` on non-ASCII input — an unhandled 500 from a public route,
  and a signal that distinguishes token shapes).
- finding 5: each provider verifies its own provider-side signature
  (`X-PagerDuty-Signature`, `X-Telegram-Bot-Api-Secret-Token`) inside
  `inbound()`; the URL token is no longer the sole credential for a call
  that acks or resolves an alert.
"""

from __future__ import annotations

import copy
import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DbSession
from fastapi import Depends

from .. import crypto, ratelimit
from .. import models as m
from ..config import settings
from ..db import get_db
from ..integrations import registry

router = APIRouter(tags=["inbound-webhooks"])


def _find_row(db: DbSession, kind: str, token: str) -> "m.Integration | None":
    """Constant-time compare against every `kind`-typed row's token, rather
    than an indexed `filter_by(inbound_token=token)` equality lookup —
    there is at most a handful of rows per kind (one per tenant), so the
    O(n) scan costs nothing, and it closes the timing side-channel a direct
    `==`/SQL-equality compare would leak."""
    for row in db.query(m.Integration).filter_by(kind=kind).all():
        if row.inbound_token and hmac.compare_digest(row.inbound_token, token):
            return row
    return None


@router.post("/inbound/{kind}/{token}")
async def inbound(kind: str, token: str, request: Request, db: DbSession = Depends(get_db)):
    if not ratelimit.allow(f"inbound:{token[:8]}", 60, 60):
        raise HTTPException(429, "too many requests, try again later")

    # Finding 7: `hmac.compare_digest` raises TypeError on a non-ASCII str,
    # which would surface as a 500 from a public route. Same 404 body as
    # every other miss, so this rejection tells a prober nothing new.
    if not token.isascii():
        raise HTTPException(404, "not found")

    integ = registry.REGISTRY.get(kind)
    row = _find_row(db, kind, token) if integ else None
    if not integ or not row:
        raise HTTPException(404, "not found")

    # A disabled card accepts nothing. Same 404 as an unknown token: a
    # channel the tenant switched off should look switched off, and telling
    # a caller "that token is real but paused" is information they have no
    # claim to. The toggle used to be outbound-only, so a paused Telegram
    # bot still answered /status and a paused PagerDuty still closed alerts.
    if not row.enabled:
        raise HTTPException(404, "not found")

    tenant = db.get(m.Tenant, row.tenant_id)
    if tenant is None:
        raise HTTPException(404, "not found")

    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}
    headers = dict(request.headers)
    # Discord's Ed25519 interaction signature is computed over the exact
    # raw request bytes, not a round-tripped re-serialization of the parsed
    # JSON (whitespace/key-order can legitimately differ) -- stash the raw
    # body under a synthetic header key so `DiscordIntegration.inbound` can
    # verify against it without this route knowing anything Discord-specific.
    try:
        headers["_raw_body"] = (await request.body()).decode("utf-8", errors="replace")
    except Exception:
        headers["_raw_body"] = ""

    original_secret = crypto.decrypt(row.secret) if row.secret else ""
    original_config = row.config or {}
    ctx = registry.SendContext(tenant=tenant, secret=original_secret, config=copy.deepcopy(original_config),
                                db=db, base_url=settings.base_url,
                                secrets=registry.row_secrets(integ, row, original_secret),
                                ip=(request.client.host if request.client else ""))

    try:
        result = integ.inbound(ctx, body, headers)
    except Exception as e:
        row.last_error = str(e)[:300]
        db.commit()
        raise HTTPException(500, "integration error") from e

    if ctx.config != original_config:
        row.config = ctx.config
    if ctx.secret != original_secret:
        row.secret = crypto.encrypt(ctx.secret)
    db.commit()
    return JSONResponse(content=result.body, status_code=result.status)
