"""Integration registry: one process-wide singleton instance per `kind`,
populated by `@register` on each provider module (`ntfy.py`, `slack.py`,
`teams.py`, `webhook.py` for Task 40; Task 41 adds discord/pagerduty/
telegram/jira_servicenow/homeassistant to the same `REGISTRY`).

`deliver()` is the one call site every alert transition goes through
(`ingest.store_scan`, after its own commit — see that module's docstring
for why). It is synchronous and returns the full `list[SendResult]` (the
same call is used directly by `routers/integrations.py`'s Test button and
by tests), but it never serializes N slow providers one after another: each
qualifying provider's `send()` runs in a small bounded thread pool, with a
short retry+backoff on failure, so one unreachable webhook cannot make a
CRIT ntfy push (or the ingest request itself) wait behind it.

**Secret rotation.** `Integration.secret` and `Integration.secrets_enc` are
Fernet-encrypted with a key
derived from `settings.secret_key` (`crypto.py`). Rotating
`GUARDIAN_SECRET_KEY` invalidates every already-stored secret:
`crypto.decrypt` returns `""` rather than raising, so a rotated key surfaces
as a normal `send()` failure ("no secret configured") recorded on
`Integration.last_error`, not a 500. Re-save the integration (through the
Integrations tab or `POST /api/v1/integrations`) after rotating the key.
"""

from __future__ import annotations

import copy
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor

from .. import audit
from .. import crypto
from .. import models as m
from ..config import settings
from .base import (Integration, IntegrationField, InboundResult, Message, SendContext, SendResult,
                    sync_state, wants_inbound_token)

__all__ = [
    "Integration", "IntegrationField", "InboundResult", "Message", "SendContext", "SendResult",
    "REGISTRY", "register", "deliver", "deliver_background", "maintenance_active", "watchdog_job",
    "wants_inbound_token", "sync_state", "row_secrets",
]

REGISTRY: dict[str, Integration] = {}

# Bounded: a handful of integrations per tenant, never unbounded fan-out.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="guardian-integration")
_RETRY_ATTEMPTS = 2
_RETRY_BACKOFF_S = 1.0
_FUTURE_TIMEOUT_S = 30

# Fix round 1, finding 2: a SEPARATE, small pool that only ever holds the
# outer "dispatch this alert's delivery" job -- never the per-provider
# `send()` jobs above. Keeping the two pools distinct means a slow/backed-up
# ingest path can never starve out the per-provider retries (and vice
# versa): `deliver_background` returns to its caller (the sensor ingest
# request) the instant `submit()` hands the job off, well before any
# network call happens.
_DISPATCH_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="guardian-alert-dispatch")


def register(cls):
    """Class decorator: instantiate `cls` once and store it in `REGISTRY`
    under its own `kind`. Providers are used as long-lived singletons —
    tests monkeypatch a method directly on `REGISTRY[kind]`."""
    REGISTRY[cls.kind] = cls()
    return cls


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def row_secrets(integ: Integration, row, primary_secret: str) -> dict:
    """The `{field_name: plaintext}` map for `SendContext.secrets` (fix
    round 1, ruling a): `fields[0]`'s already-decrypted value under its own
    name, plus every other `kind="secret"` field out of the encrypted
    `Integration.secrets_enc` blob. One decrypt per row, in the one place
    that owns it -- a provider never calls `crypto` itself.

    `row` may be None (the implicit ntfy default channel has no row), in
    which case only `fields[0]` is known."""
    out: dict[str, str] = {}
    if integ.fields:
        out[integ.fields[0].name] = primary_secret
    if row is not None and getattr(row, "secrets_enc", ""):
        for name, value in crypto.decrypt_json(row.secrets_enc).items():
            out[str(name)] = str(value)
    return out


def _send_with_retry(integ: Integration, ctx: SendContext, msg: Message) -> SendResult:
    result = SendResult(False, "not attempted")
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            result = integ.send(ctx, msg)
        except Exception as e:  # a provider bug must never take ingest down with it
            result = SendResult(False, str(e)[:300])
        if result.ok:
            return result
        if attempt < _RETRY_ATTEMPTS - 1:
            time.sleep(_RETRY_BACKOFF_S * (2 ** attempt))
    return result


def maintenance_active(tenant: "m.Tenant") -> bool:
    """Fix round 1, finding 5: `tenant.settings["maintenance_until"]` is an
    ISO-8601 UTC timestamp -- the exact key Task 39 (branch phase4-hub,
    not yet merged here) writes for its maintenance-window setting, so this
    reads it unchanged once the branches merge. Absent, unparsable, or in
    the past -> not active. A naive timestamp is assumed UTC (same
    convention as everywhere else in this codebase that stores an ISO
    string without an offset)."""
    until = (tenant.settings or {}).get("maintenance_until")
    if not until:
        return False
    try:
        parsed = dt.datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed > _now()


def deliver(db, tenant: "m.Tenant", msg: Message) -> list[SendResult]:
    """Walk the tenant's enabled `Integration` rows whose `routes[severity]`
    is true and call `send()` on each (plus the implicit ntfy default —
    `Tenant.ntfy_topic` fires unconditionally when no explicit `ntfy` row
    exists for this tenant, enabled OR disabled -- an explicitly disabled
    row must suppress the default path too, finding 3). Records
    `last_test`/`last_error` and audits `integration.send` only on failure;
    a successful send is high-volume and is not audited.

    Suppressed entirely (no jobs, no audit, `[]` returned) while the
    tenant's maintenance window is active (finding 5) -- storage of the
    alert itself is untouched, only its delivery is skipped."""
    if maintenance_active(tenant):
        if msg.kind == "report":
            db.add(m.Delivery(tenant_id=tenant.id, reference=msg.dedup_key or "", channel="all", status="suppressed", detail="Maintenance window"))
            db.commit()
        return []

    rows = db.query(m.Integration).filter_by(tenant_id=tenant.id, enabled=True).all()
    # ntfy is retired as the default channel (push moves to the mobile app):
    # `Tenant.ntfy_topic` no longer fires on its own. A tenant that wants
    # ntfy keeps an explicit `ntfy` row, which is handled like any other
    # kind below. The implicit default also used to raise on a topic with
    # a space in it ("Miro Home") and count as a failed delivery.
    any_ntfy_row = True

    jobs: list[tuple["m.Integration | None", str]] = []
    if tenant.ntfy_topic and not any_ntfy_row:
        jobs.append((None, "ntfy"))
    for row in rows:
        if (row.routes or {}).get(msg.severity):
            jobs.append((row, row.kind))

    futures = {}
    for row, kind in jobs:
        integ = REGISTRY.get(kind)
        if not integ:
            continue
        secret = tenant.ntfy_topic if row is None else crypto.decrypt(row.secret)
        ctx = SendContext(tenant=tenant, secret=secret, config=(row.config if row else {}) or {},
                           db=db, base_url=settings.base_url,
                           secrets=row_secrets(integ, row, secret))
        receipt = None
        if msg.kind == "report":
            receipt = m.Delivery(tenant_id=tenant.id, reference=msg.dedup_key or "", channel=kind, status="queued", detail="Waiting for the destination to accept the report")
            db.add(receipt)
            db.commit()
        futures[_EXECUTOR.submit(_send_with_retry, integ, ctx, msg)] = (row, kind, receipt)

    if not futures and msg.kind == "report":
        db.add(m.Delivery(tenant_id=tenant.id, reference=msg.dedup_key or "", channel="all", status="suppressed", detail="No enabled destination accepts this report"))
        db.commit()

    results: list[SendResult] = []
    for fut, (row, kind, receipt) in futures.items():
        try:
            res = fut.result(timeout=_FUTURE_TIMEOUT_S)
        except Exception as e:
            res = SendResult(False, str(e)[:300])
        results.append(res)
        if receipt is not None:
            receipt.status = "sent" if res.ok else "failed"
            receipt.detail = (res.error or "Destination accepted the report")[:300]
            receipt.updated = _now()
            db.commit()
        if row is not None and not res.ok:
            row.last_error = res.error
            row.last_test = _now()
            audit.log(db, tenant.id, "system", "system", "integration.send",
                       target=kind, body={"error": res.error, "severity": msg.severity, "kind": msg.kind})
            db.commit()
    return results


def _deliver_by_tenant_id(tenant_id: str, msg: Message) -> None:
    """The actual background job body: its own DB session (never the
    request's -- that session is closed by `get_db`'s generator teardown
    the moment the request returns, which now happens before this runs)."""
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tenant_id)
        if tenant is not None:
            deliver(db, tenant, msg)
    finally:
        db.close()


def deliver_background(tenant_id: str, msg: Message) -> None:
    """Fix round 1, finding 2: `ingest.store_scan` calls this instead of
    `deliver()` directly, so a slow/unreachable third-party integration can
    never block the sensor's ingest request. `submit()` only hands the job
    to `_DISPATCH_EXECUTOR` and returns -- it does not wait on the result,
    so the caller (the ingest route, running on the request's own worker
    thread) returns to the client immediately regardless of how long
    delivery itself takes."""
    _DISPATCH_EXECUTOR.submit(_deliver_by_tenant_id, tenant_id, msg)


def watchdog_job() -> None:
    """Hourly APScheduler job (wired in `main.py`): call `watchdog()` on
    every enabled integration for every tenant, so each provider "does a
    job, not just relays text" (spec §10.1) — ntfy's CRIT-escalation ladder,
    Slack/Teams' daily digest, the webhook heartbeat, Task 41's ticket
    reconciliation and MQTT-discovery publish.

    `ctx.config` is handed to `watchdog()` as a **deep copy** of
    `row.config` (never the same dict object, and never sharing a nested
    one) and compared back afterwards: a plain JSON column's in-place
    mutations are invisible to SQLAlchemy's flush (no `MutableDict`), so a
    provider that needs to remember something between ticks — the
    ticket-sync's incident->ticket-key map, ntfy-escalation hand-off flags
    — must go through this compare-and-reassign, which does mark the column
    dirty.

    Fix round 1: the copy is DEEP, and the write-back happens in a
    `finally`. A shallow copy was correct only while every remembered value
    sat at the top level; ruling (b) moved provider bookkeeping under
    `config["_sync"]`, and a nested `ctx.config["_sync"][k] = v` mutates the
    *same* dict the snapshot holds, so the equality check would have found
    the two identical and silently dropped every write. And a provider that
    raises after recording something (Home Assistant's MQTT publish,
    finding 9) must not lose what it already wrote.
    """
    from ..db import SessionLocal
    db = SessionLocal()
    try:
        for tenant in db.query(m.Tenant).all():
            for row in db.query(m.Integration).filter_by(tenant_id=tenant.id, enabled=True).all():
                integ = REGISTRY.get(row.kind)
                if not integ:
                    continue
                original_config = row.config or {}
                secret = crypto.decrypt(row.secret)
                ctx = SendContext(tenant=tenant, secret=secret, config=copy.deepcopy(original_config),
                                   db=db, base_url=settings.base_url,
                                   secrets=row_secrets(integ, row, secret))
                try:
                    result = integ.watchdog(ctx)
                except Exception as e:
                    row.last_error = str(e)[:300]
                else:
                    # Finding 9: a provider reports a failed tick as a value
                    # rather than raising, so it keeps both its recorded
                    # state and a real `last_error`.
                    if isinstance(result, SendResult) and not result.ok:
                        row.last_error = (result.error or "watchdog failed")[:300]
                finally:
                    if ctx.config != original_config:
                        row.config = ctx.config
        db.commit()
    finally:
        db.close()


# Phase-1 kinds register themselves against REGISTRY on import. Imported at
# the bottom (after `register`/`REGISTRY` exist) so each provider module can
# do `from .registry import register` without a circular-import error.
from . import ntfy, slack, teams, webhook  # noqa: E402,F401
# Task 41: the five duty-bearing integrations (discord/pagerduty/telegram
# register "discord"/"pagerduty"/"telegram"; tickets.py registers both
# "jira" and "servicenow" off one shared implementation class).
from . import discord, pagerduty, telegram, tickets, homeassistant  # noqa: E402,F401
# The MSP's PSA: halopsa/autotask/connectwise off one class, per escalated finding.
from . import psa  # noqa: E402,F401
# Item 25: findings by email as well as by chat.
from . import email as email_channel  # noqa: E402,F401
