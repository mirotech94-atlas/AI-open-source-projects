"""PagerDuty: Events API v2 (trigger/resolve) outbound, incident webhook
v3 inbound.

Inbound is signature-verified (fix round 1, finding 5): the URL token alone
used to authorise a call that acks or resolves any alert. PagerDuty signs
every webhook v3 delivery as `X-PagerDuty-Signature: v1=<hex>[,v1=<hex>]`
— HMAC-SHA256 of the **raw request body** with the webhook subscription's
own signing secret, and the header can legitimately carry more than one
`v1=` value while the secret is being rotated, so any one match is
accepted. The secret is a required `kind="secret"` field on the row
(encrypted in `Integration.secrets_enc`), and verification FAILS CLOSED: a
row with no signing secret stored rejects every inbound call rather than
falling back to token-only auth.

Real watchdog job, per spec §10.1: PagerDuty isn't just another place a
CRIT push lands — enabling it (with `owns_escalation` true, the default)
takes over the urgent-escalation duty ntfy's `watchdog` otherwise runs
after 30 minutes unacked, since PagerDuty's own on-call escalation policy
is the whole reason a tenant wired this up. `ntfy.watchdog` checks for
that (see `_pagerduty_owns_escalation` below, imported from there).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request

from .. import models as m
from ..routers.write import apply_alert_op
from .base import Integration, IntegrationField, InboundResult, Message, SendContext, SendResult
from .registry import register

TIMEOUT_S = 10
EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
_SIGNATURE_HEADER = "x-pagerduty-signature"


def _verify_signature(signing_secret: str, header_value: str, raw_body: str) -> bool:
    """PagerDuty webhook v3 signature check. Fails closed on a missing
    secret or a missing/malformed header, and compares in constant time."""
    if not signing_secret or not header_value:
        return False
    expected = hmac.new(signing_secret.encode(), raw_body.encode(), hashlib.sha256).hexdigest()
    for part in header_value.split(","):
        part = part.strip()
        if not part.startswith("v1="):
            continue
        candidate = part[3:]
        if candidate.isascii() and hmac.compare_digest(candidate, expected):
            return True
    return False


def _post(url: str, body: dict) -> SendResult:
    """A thin, monkeypatchable seam (tests patch this exact name) around
    the one PagerDuty Events API call this module makes."""
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                      headers={"Content-Type": "application/json", "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return SendResult(True, "")
    except Exception as e:
        return SendResult(False, str(e)[:200])


def owns_escalation(db, tenant_id: str) -> bool:
    """True when this tenant has an enabled PagerDuty row whose
    `owns_escalation` config flag isn't explicitly turned off. Read by
    `ntfy.watchdog` so the two integrations never both run an urgent
    escalation ladder for the same open CRIT alert."""
    row = db.query(m.Integration).filter_by(tenant_id=tenant_id, kind="pagerduty", enabled=True).first()
    if not row:
        return False
    return str((row.config or {}).get("owns_escalation", "true")).lower() != "false"


@register
class PagerDutyIntegration(Integration):
    kind = "pagerduty"
    title = "PagerDuty"
    help = "Trigger/resolve PagerDuty incidents on CRIT alerts; acknowledging there closes the alert in Vault too."
    direction = "out+in"
    fields = [
        IntegrationField(name="routing_key", label="Events API v2 routing key", kind="secret", required=True),
        IntegrationField(name="signing_secret", label="Webhook signing secret", kind="secret", required=True),
        IntegrationField(name="owns_escalation", label="Replace ntfy's 30-min escalation", kind="bool"),
    ]

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        if not ctx.secret:
            return SendResult(False, "no PagerDuty routing key configured")
        if msg.kind == "resolve":
            action = "resolve"
        elif msg.severity == "CRIT":
            action = "trigger"
        else:
            return SendResult(True, "")   # not this integration's job — only CRIT triggers, non-CRIT resolves are moot
        if not msg.dedup_key:
            return SendResult(False, "no dedup_key on this message")
        body = {
            "routing_key": ctx.secret, "event_action": action, "dedup_key": msg.dedup_key,
        }
        if action == "trigger":
            body["payload"] = {
                "summary": msg.title, "source": ctx.tenant.slug, "severity": "critical",
                "custom_details": {"body": msg.body, "url": msg.url},
            }
        return _post(EVENTS_URL, body)

    def watchdog(self, ctx: SendContext) -> None:
        """Reconciliation duty, not a resend-for-visibility ladder like
        ntfy's (PagerDuty's own on-call policy already owns that once this
        integration is enabled — see `owns_escalation` above): re-trigger
        every still-open CRIT alert once per tick. The Events API v2
        `dedup_key` makes `trigger` idempotent against an incident that's
        already open, so this is a safety net against a transient failure
        on the original `deliver()` call (network blip, PagerDuty briefly
        down) rather than a duplicate-incident risk."""
        if not ctx.secret:
            return
        for a in ctx.db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, sev="CRIT", status="open").all():
            self.send(ctx, Message(title=f"Vault: {a.sev} alert", body=a.msg, severity="CRIT",
                                    url=f"{ctx.base_url}/inbox?row={a.key}", kind="alert", dedup_key=a.key))

    def inbound(self, ctx: SendContext, request_body: dict, headers: dict) -> InboundResult:
        # Finding 5: verify PagerDuty's own signature over the raw bytes
        # (`routers/inbound.py` stashes them under `_raw_body`, the same
        # seam Discord's Ed25519 check uses) before touching any alert.
        if not _verify_signature(ctx.secrets.get("signing_secret", ""),
                                  headers.get(_SIGNATURE_HEADER) or headers.get("X-PagerDuty-Signature", ""),
                                  headers.get("_raw_body", "")):
            return InboundResult(ok=False, body={"error": "invalid signature"}, status=401)

        event = (request_body or {}).get("event") or {}
        event_type = event.get("event_type", "")
        dedup_key = (event.get("data") or {}).get("dedup_key", "")
        if not dedup_key:
            return InboundResult(ok=True, body={"ignored": "no dedup_key"}, status=200)

        if event_type == "incident.acknowledged":
            op = "ack"
        elif event_type == "incident.resolved":
            op = "resolve"
        else:
            return InboundResult(ok=True, body={"ignored": event_type}, status=200)

        a = apply_alert_op(ctx.db, ctx.tenant, dedup_key, op, "pagerduty", "", ip=ctx.ip)
        return InboundResult(ok=True, body={"ok": True, "found": a is not None}, status=200)
