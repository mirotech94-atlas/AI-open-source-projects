"""ntfy.sh push notifications — the default channel (fires from
`Tenant.ntfy_topic` even with no `Integration` row configured; see
`registry.deliver`).

stdlib `urllib` only, 10 s timeout, no new dependency. Action buttons are
rendered as ntfy's `Actions` header, each pointing back at the hub's own
`/act/<token>/<action>` route (the token itself is minted by Task 42's
`acttokens.mint` — this provider just renders whatever `Message.actions`
the caller already built).
"""

from __future__ import annotations

import datetime as dt
import urllib.request

from .. import models as m
from .base import Integration, IntegrationField, Message, SendContext, SendResult
from .registry import register

TIMEOUT_S = 10
_PRIORITY = {"CRIT": "urgent", "WARN": "default", "INFO": "low"}
_ESCALATE_AFTER_S = 1800   # 30 min unacked -> urgent
_REPEAT_AFTER_S = 6 * 3600  # 6h resend ladder


def _post(topic: str, msg: Message, priority: str) -> SendResult:
    if not topic:
        return SendResult(False, "no ntfy topic configured")
    headers = {"Title": msg.title[:200], "Priority": priority, "Tags": "shield", "User-Agent": "guardian/1.0"}
    if msg.actions:
        headers["Actions"] = "; ".join(f"http, {label}, {url}, method=GET, clear=true" for label, url in msg.actions[:3])
    if msg.url:
        headers["Click"] = msg.url
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=msg.body.encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return SendResult(True, "")
    except Exception as e:
        return SendResult(False, str(e)[:200])


@register
class NtfyIntegration(Integration):
    kind = "ntfy"
    title = "ntfy"
    help = "Push notifications to your phone via ntfy.sh. Free, no account needed — just pick a topic name."
    direction = "out"
    fields = [IntegrationField(name="topic", label="Topic", kind="secret", required=True)]

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        return _post(ctx.secret, msg, _PRIORITY.get(msg.severity, "default"))

    def watchdog(self, ctx: SendContext) -> None:
        """Port of the Nexus `checks.notify` escalation ladder: any CRIT
        alert still open first escalates once it has been open more than
        30 min (at `priority=urgent`), then keeps resending every 6h while
        it stays open (fix round 1, finding 4 -- the hourly watchdog tick
        alone can't tell "just escalated" from "escalated 5 ticks ago"
        without `Alert.escalated_at`, so every open CRIT resent hourly).

        Task 41: skipped entirely when this tenant has an enabled PagerDuty
        integration that owns the escalation instead (`pagerduty.
        owns_escalation` -- PagerDuty's own on-call policy is the reason a
        tenant wires it up; both integrations escalating the same open CRIT
        alert would be noise, not safety)."""
        from .pagerduty import owns_escalation
        if owns_escalation(ctx.db, ctx.tenant.id):
            return
        now = dt.datetime.now(dt.timezone.utc)
        rows = ctx.db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, sev="CRIT", status="open").all()
        for a in rows:
            try:
                first = dt.datetime.fromisoformat(a.first_seen)
                if first.tzinfo is None:
                    first = first.replace(tzinfo=dt.timezone.utc)
            except Exception:
                continue
            age_s = (now - first).total_seconds()
            if age_s < _ESCALATE_AFTER_S:
                continue
            last_escalated = a.escalated_at
            if last_escalated is not None:
                if last_escalated.tzinfo is None:
                    last_escalated = last_escalated.replace(tzinfo=dt.timezone.utc)
                if (now - last_escalated).total_seconds() < _REPEAT_AFTER_S:
                    continue
            msg = Message(title="Vault CRITICAL — still open", body=a.msg, severity="CRIT",
                           url=f"{ctx.base_url}/inbox?row={a.key}", kind="alert", dedup_key=a.key)
            if _post(ctx.secret, msg, "urgent").ok:
                a.escalated_at = now
