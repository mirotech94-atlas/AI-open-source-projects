"""Integrations API (Task 40): registry-backed provider list, encrypted
secrets, per-severity routing, the Test button, and the generic inbound
webhook endpoint the future "in"-direction providers (Task 41) hang off.

Every secret is masked on the way out (`_mask`) and never returned in
clear — `GET /api/v1/integrations` only ever sees ciphertext-derived masks,
matching the old Nexus `server.py:822` `mask()` behaviour. Every write is
admin-only and audited (`audit.log`); a secret in an audit body is always
redacted to `"(set)"`/`""`, never logged in clear.

Fix round 1:

- ruling (a) / finding 1: EVERY `kind="secret"` field is encrypted at rest
  and masked in `_card`, not just `fields[0]`. `fields[0]` still lives in
  `Integration.secret`; the rest go into `Integration.secrets_enc` (one
  Fernet blob, `crypto.encrypt_json`). Nothing secret-shaped is ever written
  into the plaintext `config` column, and nothing secret-shaped is ever
  returned in clear.
- ruling (b) / finding 3: `upsert_integration` MERGES the submitted fields
  over the stored config instead of replacing it, and never touches
  `config["_sync"]` -- the provider bookkeeping (the ticket-sync's
  incident->ticket map, Telegram's paired `chat_id`). Re-saving an
  integration used to wipe that, which re-filed every ticket and silently
  unpaired Telegram.
"""

import copy
import datetime as dt
import secrets as secretslib

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from .. import audit, crypto, ratelimit
from .. import models as m
from ..integrations.base import RESERVED_CONFIG_KEYS
from ..config import settings
from ..db import get_db
from ..deps import require_role
from ..integrations import registry
from ..schemas_dash import AiFailover, IntegrationCard, IntegrationField as IntegrationFieldOut, IntegrationsPayload

router = APIRouter(prefix="/api/v1", tags=["integrations"])

ADMIN = Depends(require_role("admin"))


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _mask(value: str) -> str:
    """Port of the old Nexus `server.py:822` `mask()`: never return a
    secret in clear. Longer than 24 chars -> partial reveal with an
    ellipsis; anything shorter (still non-empty) -> just "set"."""
    if not value:
        return ""
    if len(value) > 24:
        return value[:14] + "…" + value[-6:]
    return "set"


def _card(kind: str, integ, row: "m.Integration | None", tenant: "m.Tenant") -> IntegrationCard:
    # The decrypted value backing fields[0] (see base.py's field-binding
    # convention) -- falling back to Tenant.ntfy_topic for an unconfigured
    # `ntfy` card, since that is the implicit default channel.
    secret_raw = crypto.decrypt(row.secret) if (row and row.secret) else ""
    if not secret_raw and kind == "ntfy" and not row:
        secret_raw = tenant.ntfy_topic or ""
    extra_secrets = crypto.decrypt_json(row.secrets_enc) if (row and row.secrets_enc) else {}

    fields_out = []
    for i, f in enumerate(integ.fields):
        if i == 0:
            value = _mask(secret_raw)
        elif f.kind == "secret":
            # Finding 1: a secret-shaped field is masked no matter WHERE it
            # is stored -- `_mask` of the decrypted value, so the card still
            # says "set"/partial without ever echoing the credential back.
            value = _mask(str(extra_secrets.get(f.name, "")))
        else:
            value = str((row.config or {}).get(f.name, "")) if row else ""
        fields_out.append(IntegrationFieldOut(name=f.name, label=f.label, kind=f.kind,
                                               value=value, required=f.required, options=list(f.options)))

    inbound_url = None
    if registry.wants_inbound_token(integ.direction) and row and row.inbound_token:
        # Task 41 providers dispatch through the public, unversioned
        # `/inbound/{kind}/{token}` route (`routers/inbound.py`) rather than
        # the phase-1 `/api/v1/hooks/{token}` -- PagerDuty/Discord/Telegram
        # webhook URL fields don't expect an `/api/v1` prefix, and keeping
        # them on one route (vs. a per-kind dispatch inside hooks.py) is
        # simpler for Task 40's still-live generic hook to leave alone.
        inbound_url = f"{settings.base_url.rstrip('/')}/inbound/{kind}/{row.inbound_token}"

    return IntegrationCard(
        kind=kind, title=integ.title, help=integ.help,
        enabled=bool(row.enabled) if row else False,
        configured=bool(secret_raw),
        fields=fields_out,
        routes=dict(row.routes or {}) if row else {},
        inbound_url=inbound_url,
        last_test=row.last_test.isoformat() if row and row.last_test else None,
        last_error=row.last_error if row else "",
        direction=integ.direction,
        # Only for a channel that is connected AND switched on. An
        # unconfigured card's commands are things that would not answer, and
        # a PAUSED card sits under a "paused" badge -- offering four things
        # to type two inches below that would contradict it.
        commands=list(integ.commands) if (secret_raw and row and row.enabled) else [],
    )


#: Kinds that stay registered (existing tenants keep working) but are not
#: offered to a tenant that has not already set one up. ntfy is being
#: superseded by Vault's own mobile app for push notifications; a tenant
#: with an ntfy row keeps its card and its alerts until it removes it.
HIDDEN_UNLESS_CONFIGURED = frozenset({"ntfy"})


@router.get("/integrations", response_model=IntegrationsPayload)
def list_integrations(ctx=ADMIN, db: DbSession = Depends(get_db)):
    _user, tenant, _role = ctx
    cards = []
    for kind, integ in registry.REGISTRY.items():
        row = db.query(m.Integration).filter_by(tenant_id=tenant.id, kind=kind).first()
        if kind in HIDDEN_UNLESS_CONFIGURED and row is None:
            continue
        cards.append(_card(kind, integ, row, tenant))
    # Late import: `chat.providers` imports `rules`, which pulls the chat
    # registry, which imports routers.
    from ..chat.providers import failover_status
    return IntegrationsPayload(integrations=cards, ai=AiFailover(**failover_status()))


class IntegrationIn(BaseModel):
    kind: str
    enabled: bool = True
    routes: dict[str, bool] = {}
    fields: dict[str, str] = {}
    name: str = ""


@router.post("/integrations")
def upsert_integration(body: IntegrationIn, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    integ = registry.REGISTRY.get(body.kind)
    if not integ:
        raise HTTPException(404, "unknown integration kind")

    err = integ.validate(body.fields)
    if err:
        raise HTTPException(422, err)

    is_first_integration = db.query(m.Integration).filter_by(tenant_id=tenant.id).first() is None

    row = db.query(m.Integration).filter_by(tenant_id=tenant.id, kind=body.kind).first()
    is_new_row = row is None
    if not row:
        row = m.Integration(tenant_id=tenant.id, kind=body.kind)
        db.add(row)

    primary_field = integ.fields[0] if integ.fields else None
    primary_name = primary_field.name if primary_field else None
    primary_val = body.fields.get(primary_name, "") if primary_name else ""
    # Finding 6 (task 40): a required primary secret must not be saved empty
    # (an empty encrypted blob that `crypto.decrypt` would happily hand back
    # as "" forever). On edit, an empty value means "keep whatever's already
    # there" -- the Integrations tab never round-trips a decrypted secret
    # into the form, so "field left blank" is indistinguishable from
    # "field cleared on purpose" and must default to the safer no-op.
    if primary_field and primary_field.required and not primary_val and (is_new_row or not row.secret):
        raise HTTPException(422, f"{primary_field.label} is required")
    if primary_val:
        row.secret = crypto.encrypt(primary_val)

    # Ruling (a): every OTHER kind="secret" field goes into the encrypted
    # blob, never into `config`. Same blank-means-keep rule as the primary
    # secret, and the same "required must not end up empty" check -- a
    # provider whose inbound route fails closed without its signing secret
    # (PagerDuty, Telegram) declares that field `required`.
    secret_names = {f.name for f in integ.fields if f.kind == "secret"} - {primary_name}
    stored_secrets = crypto.decrypt_json(row.secrets_enc) if row.secrets_enc else {}
    for f in integ.fields:
        if f.name not in secret_names:
            continue
        val = body.fields.get(f.name, "")
        if val:
            stored_secrets[f.name] = val
        elif f.required and not stored_secrets.get(f.name):
            raise HTTPException(422, f"{f.label} is required")
    # Drop any legacy plaintext copy that predates 0006_integration_secrets
    # (and anything a client tries to smuggle in under a secret field name).
    row.secrets_enc = crypto.encrypt_json(stored_secrets)

    # Finding 7 (task 40): a `kind="bool"` field arrives from the form as
    # the string "true"/"false" (or is simply absent) -- coerce it to a real
    # bool before it lands in `Integration.config`, instead of storing
    # whatever string the client sent (which `bool("false")` would misread
    # as True).
    bool_field_names = {f.name for f in integ.fields if f.kind == "bool"}
    def _coerce(k, v):
        if k in bool_field_names:
            return str(v).strip().lower() in ("true", "1", "yes", "on")
        return v
    # Ruling (b) / finding 3: MERGE over the stored config. A re-save used
    # to replace it wholesale, destroying `config["_sync"]` (the ticket
    # integration's incident->ticket map -> a duplicate ticket per incident
    # AND per recommendation) and Telegram's paired `chat_id` (a silent
    # unpair). `_sync` and the pairing keys are never submitted by the form,
    # so anything the client did send is layered ON TOP of what is there.
    cfg = dict(row.config or {})
    for k, v in body.fields.items():
        if k == primary_name or k in secret_names or k in RESERVED_CONFIG_KEYS:
            continue
        cfg[k] = _coerce(k, v)
    for name in secret_names | ({primary_name} if primary_name else set()):
        cfg.pop(name, None)
    row.config = cfg
    row.routes = dict(body.routes or {})
    row.enabled = body.enabled
    row.name = body.name
    if registry.wants_inbound_token(integ.direction) and not row.inbound_token:
        row.inbound_token = secretslib.token_hex(16)

    if is_first_integration:
        # Hand alert delivery to the hub: the sensor's own notify() ladder
        # goes quiet (local macOS banner only) the moment there is a real
        # Integration row to route through, so it never double-sends.
        cfg = dict(tenant.config or {})
        notify_cfg = dict(cfg.get("notify") or {})
        notify_cfg["local_only"] = True
        cfg["notify"] = notify_cfg
        tenant.config = cfg
        # Finding 1: without this Edit row an enrolled sensor never learns
        # delivery moved to the hub (its own sync loop, `apply_edit` on
        # `/api/config`, only sees this via the edit log -- see
        # `routers/write.py`'s `update_config` for the identical pattern).
        edit_body = {"config": cfg}
        db.add(m.Edit(tenant_id=tenant.id, who=user.username, path="/api/config", body=edit_body))

    audit.log(db, tenant.id, user.username, role, "integration.update", target=body.kind, ip=_ip(request),
               body={"kind": body.kind, "enabled": body.enabled, "routes": row.routes,
                     "secret": "(set)" if primary_val else ""})
    db.commit()
    return _card(body.kind, integ, row, tenant)


@router.post("/integrations/{kind}/test")
def test_integration(kind: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    integ = registry.REGISTRY.get(kind)
    if not integ:
        raise HTTPException(404, "unknown integration kind")

    row = db.query(m.Integration).filter_by(tenant_id=tenant.id, kind=kind).first()
    if not row:
        row = m.Integration(tenant_id=tenant.id, kind=kind, enabled=True, routes={}, secret="",
                             secrets_enc="", config={})
        db.add(row)
        db.flush()

    secret = crypto.decrypt(row.secret) if row.secret else (tenant.ntfy_topic if kind == "ntfy" else "")
    # Deep copy and compare-and-reassign, the same dance as
    # `registry.watchdog_job` and for the same reason: a plain JSON column
    # does not see in-place mutation, and a test that resolves names to ids
    # (the PSA cards) or fetches a token should keep what it learned rather
    # than re-fetch it on the first watchdog tick.
    original_config = row.config or {}
    send_ctx = registry.SendContext(tenant=tenant, secret=secret, config=copy.deepcopy(original_config), db=db,
                                     base_url=settings.base_url,
                                     secrets=registry.row_secrets(integ, row, secret))
    result = integ.test(send_ctx)
    if send_ctx.config != original_config:
        row.config = send_ctx.config

    row.last_test = dt.datetime.now(dt.timezone.utc)
    row.last_error = "" if result.ok else result.error

    audit.log(db, tenant.id, user.username, role, "integration.test", target=kind, ip=_ip(request),
               body={"ok": result.ok, "error": result.error})
    db.commit()
    return {"ok": result.ok, "error": result.error, "detail": result.detail}


@router.delete("/integrations/{kind}")
def delete_integration(kind: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    row = db.query(m.Integration).filter_by(tenant_id=tenant.id, kind=kind).first()
    if not row:
        raise HTTPException(404, "integration not configured")
    db.delete(row)
    audit.log(db, tenant.id, user.username, role, "integration.delete", target=kind, ip=_ip(request))
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Telegram pairing: an admin, signed-in route (unlike everything below,
# which is public/token-authed) that mints a short code for the admin to
# send as `/start <code>` to their own tenant's bot -- `TelegramIntegration.
# inbound()` (Task 41) binds whichever `chat_id` sends that code into
# `Integration.config`. One tenant = one bot = one webhook URL (the bot
# token is per-tenant), so no cross-tenant lookup is needed on the inbound
# side -- this route only has to remember the code on the row that already
# exists for THIS tenant.
# ---------------------------------------------------------------------------

_PAIR_CODE_TTL_MIN = 10


@router.post("/integrations/telegram/pair")
def telegram_pair(request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    row = db.query(m.Integration).filter_by(tenant_id=tenant.id, kind="telegram").first()
    if not row or not row.secret:
        raise HTTPException(422, "configure the Telegram bot token first")

    code = secretslib.token_hex(3).upper()   # 6 hex chars
    # Ruling (b): the pairing code, its expiry and the bound `chat_id` are
    # sync state, not form fields -- they live under `config["_sync"]` so a
    # re-save of the Telegram card can no longer unpair the chat.
    cfg = dict(row.config or {})
    sync = dict(cfg.get("_sync") or {})
    sync["pair_code"] = code
    sync["pair_expires"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=_PAIR_CODE_TTL_MIN)).isoformat(timespec="seconds")
    cfg["_sync"] = sync
    row.config = cfg

    audit.log(db, tenant.id, user.username, role, "integration.telegram_pair", ip=_ip(request))
    db.commit()
    return {"code": code, "expires_in_minutes": _PAIR_CODE_TTL_MIN}


# ---------------------------------------------------------------------------
# Inbound webhooks: a per-integration random token, not a session cookie —
# this route is called by the outside provider (Slack button, PagerDuty
# ack, ...), never by the dashboard. Rate-limited per token since the token
# itself is the only credential.
# ---------------------------------------------------------------------------

@router.post("/hooks/{token}")
async def inbound_hook(token: str, request: Request, db: DbSession = Depends(get_db)):
    if not ratelimit.allow(f"integration_hook:{token}", 30, 60):
        raise HTTPException(429, "too many requests, try again later")

    row = db.query(m.Integration).filter_by(inbound_token=token).first()
    if not row:
        raise HTTPException(404, "unknown inbound token")
    integ = registry.REGISTRY.get(row.kind)
    if not integ:
        raise HTTPException(404, "unknown integration kind")

    tenant = db.get(m.Tenant, row.tenant_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    secret = crypto.decrypt(row.secret)
    ctx = registry.SendContext(tenant=tenant, secret=secret, config=row.config or {},
                                db=db, base_url=settings.base_url,
                                secrets=registry.row_secrets(integ, row, secret), ip=_ip(request))
    result = integ.inbound(ctx, body, dict(request.headers))
    return JSONResponse(content=result.body, status_code=result.status)
