"""Microsoft Teams incoming-webhook integration: a MessageCard payload.
stdlib `urllib` only, 10 s timeout. Same daily-digest duty as Slack.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request

from .base import Integration, IntegrationField, Message, SendContext, SendResult
from .registry import register

TIMEOUT_S = 10
_COLOR = {"CRIT": "D92C2C", "WARN": "E0A72C", "INFO": "2C6FE0"}


def _post_json(url: str, body: dict) -> SendResult:
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                      headers={"Content-Type": "application/json", "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return SendResult(True, "")
    except Exception as e:
        return SendResult(False, str(e)[:200])


@register
class TeamsIntegration(Integration):
    kind = "teams"
    title = "Microsoft Teams"
    help = "Post alerts to a Teams channel via an incoming webhook connector."
    direction = "out"
    fields = [IntegrationField(name="url", label="Webhook URL", kind="url", required=True)]

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        if not ctx.secret:
            return SendResult(False, "no Teams webhook URL configured")
        text = msg.body + (f"\n\n[Open Vault]({msg.url})" if msg.url else "")
        body = {
            "@type": "MessageCard", "@context": "http://schema.org/extensions",
            "summary": msg.title, "themeColor": _COLOR.get(msg.severity, "888888"),
            "title": msg.title, "text": text,
        }
        return _post_json(ctx.secret, body)

    def watchdog(self, ctx: SendContext) -> None:
        hour = int((ctx.config or {}).get("digest_hour", 9))
        if dt.datetime.now(dt.timezone.utc).hour != hour:
            return
        self.send(ctx, Message(title="Vault daily digest",
                                body="Daily summary — see the dashboard for full detail.",
                                severity="INFO", kind="digest"))
