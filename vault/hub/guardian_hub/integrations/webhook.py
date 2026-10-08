"""Generic outbound webhook. HTTPS-only — the port of the old Nexus
`server.py:991` rule that a webhook URL must be TLS, enforced twice: once
at save time (`validate()`, so a plaintext http:// URL 422s before it is
ever encrypted and stored) and again defensively at send time.

POSTs `{title, message, severity, priority, url, ts, tenant, kind}`.
`watchdog()` sends an hourly `heartbeat` kind so the receiver can alert on
Vault's own silence.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request

from .. import netguard
from .base import Integration, IntegrationField, Message, SendContext, SendResult
from .registry import register

TIMEOUT_S = 10
_PRIORITY = {"CRIT": "urgent", "WARN": "high", "INFO": "low"}


def _is_https(url: str) -> bool:
    return url.startswith("https://")


@register
class WebhookIntegration(Integration):
    kind = "webhook"
    title = "Webhook"
    help = "POST alerts as JSON to any HTTPS endpoint you control."
    direction = "out"
    fields = [IntegrationField(name="url", label="HTTPS URL", kind="url", required=True)]

    def validate(self, fields: dict) -> str:
        url = (fields or {}).get("url") or ""
        if url and not _is_https(url):
            return "webhook URL must be https"
        # Save-time, no DNS: what is private on its face. `send` resolves
        # and re-checks before every POST (see `guardian_hub.netguard`).
        if url and (why := netguard.literal_problem(url)):
            return f"webhook URL {why}"
        return ""

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        if not ctx.secret:
            return SendResult(False, "no webhook URL configured")
        if not _is_https(ctx.secret):
            return SendResult(False, "webhook URL must be https")
        body = {
            "title": msg.title, "message": msg.body, "severity": msg.severity,
            "priority": _PRIORITY.get(msg.severity, "high"), "url": msg.url,
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "tenant": ctx.tenant.slug, "kind": msg.kind,
        }
        try:
            req = urllib.request.Request(ctx.secret, data=json.dumps(body).encode(),
                                          headers={"Content-Type": "application/json", "User-Agent": "guardian/1.0"})
            # The URL is the customer's; the POST leaves from the hub. Only
            # to an address that resolves public right now, and never
            # through a redirect: a receiver has no reason to send one, and
            # following it is how a checked public URL reaches an unchecked
            # private one.
            with netguard.open_public(req, timeout=TIMEOUT_S, follow_redirects=False):
                return SendResult(True, "")
        except netguard.NotPublicAddress:
            return SendResult(False, "webhook URL is not a public address")
        except Exception as e:
            return SendResult(False, str(e)[:200])

    def watchdog(self, ctx: SendContext) -> None:
        self.send(ctx, Message(title="Vault heartbeat", body="Vault is alive.",
                                severity="INFO", kind="heartbeat"))
