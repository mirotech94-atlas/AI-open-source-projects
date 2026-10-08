"""Discord: an incoming webhook for outbound alerts/digest/report links,
plus the `/guardian status` slash command over Discord's Interactions
endpoint (`direction = "out+command"`).

Two independent Discord credentials live on one row: the *webhook URL*
(outbound only, `fields[0]`, encrypted) and the *application public key*
(needed only to verify an inbound interaction's Ed25519 signature — not a
secret, it's the whole point of a public key, but there is nowhere else to
put it than `config`). A guild id is accepted for the admin's own
reference (narrowing which server the bot commands are expected from) but
is not itself checked — Discord signs every interaction with the
application's key regardless of guild, and rejecting on webhook URL alone
already scopes the row to one tenant.

Fix round 1:

- finding 4 / ruling e: the webhook URL is validated as https, with
  loopback/link-local rejected (`urlpolicy.py`). It is a
  `discord.com/api/webhooks/...` URL in practice; there is no plaintext
  form of it.
- finding 10: the interaction signature check now also requires a FRESH
  `X-Signature-Timestamp` (within 5 minutes). Ed25519 over
  `timestamp + body` is valid forever on its own, so a captured
  interaction could otherwise be replayed indefinitely.
- finding 8: `digest_hour` is a real form field. It was read from `config`
  but no field ever wrote it, pinning the digest at 09:00 UTC.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request

from .. import models as m
from . import urlpolicy
from .base import Integration, IntegrationField, InboundResult, Message, SendContext, SendResult
from .registry import register

TIMEOUT_S = 10
_COLOR = {"CRIT": 0xD92C2C, "WARN": 0xE0A72C, "INFO": 0x2C6FE0}
_PING_TYPE = 1
_APPLICATION_COMMAND = 2
_PONG = {"type": 1}
# Discord's own recommendation for an interactions endpoint; a replayed
# capture older than this is refused even with a valid signature.
_MAX_SIGNATURE_AGE_S = 300
_DEFAULT_DIGEST_HOUR = 9


def digest_hour(config: dict) -> int:
    """Finding 8: same admin-settable-hour parse as `telegram.digest_hour`
    -- anything that is not 0..23 falls back to 09:00 UTC rather than
    raising out of the hourly tick."""
    try:
        hour = int(str((config or {}).get("digest_hour", "")).strip())
    except (TypeError, ValueError):
        return _DEFAULT_DIGEST_HOUR
    return hour if 0 <= hour <= 23 else _DEFAULT_DIGEST_HOUR


def _timestamp_fresh(timestamp: str, max_age_s: int = _MAX_SIGNATURE_AGE_S) -> bool:
    """Discord's `X-Signature-Timestamp` is unix seconds. Rejects a stale
    OR far-future value; an unparsable one is not fresh."""
    try:
        age = abs(time.time() - float(timestamp))
    except (TypeError, ValueError):
        return False
    return age <= max_age_s


def _post_json(url: str, body: dict) -> SendResult:
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                      headers={"Content-Type": "application/json", "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return SendResult(True, "")
    except Exception as e:
        return SendResult(False, str(e)[:200])


def _verify_ed25519(public_key_hex: str, signature_hex: str, timestamp: str, raw_body: str) -> bool:
    """Discord's documented interaction check: Ed25519 over
    `timestamp + raw_body`, verified against the application's public key.
    `cryptography` is already a hard dependency (`crypto.py`'s Fernet), so
    this adds no new package."""
    if not (public_key_hex and signature_hex and timestamp):
        return False
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pubkey.verify(bytes.fromhex(signature_hex), (timestamp + raw_body).encode())
        return True
    except Exception:
        return False


def _status_line(ctx: SendContext) -> str:
    db = ctx.db
    open_alerts = db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, status="open").all()
    crit = sum(1 for a in open_alerts if a.sev == "CRIT")
    warn = sum(1 for a in open_alerts if a.sev == "WARN")
    devices = db.query(m.Device).filter_by(tenant_id=ctx.tenant.id).all()
    unverified = sum(1 for d in devices if not d.trusted and not d.label)
    scan = (db.query(m.Scan).filter_by(tenant_id=ctx.tenant.id).order_by(m.Scan.ts.desc()).first())
    score = scan.score if scan else None
    grade = ((scan.raw or {}).get("score") or {}).get("grade") if scan else ""
    return (f"**{ctx.tenant.name}** — score {score if score is not None else '—'}{f' ({grade})' if grade else ''}\n"
            f"CRIT: {crit} · WARN: {warn} · unverified devices: {unverified}")


@register
class DiscordIntegration(Integration):
    kind = "discord"
    title = "Discord"
    help = "Post alerts and the daily digest to a Discord channel, with a /guardian status slash command."
    direction = "out+command"
    fields = [
        IntegrationField(name="webhook_url", label="Webhook URL", kind="url", required=True),
        IntegrationField(name="public_key", label="Application public key", kind="text"),
        IntegrationField(name="guild_id", label="Guild ID", kind="text"),
        IntegrationField(name="digest_hour", label="Daily digest hour (0-23 UTC)", kind="text"),
    ]
    #: One slash command, which is all `inbound` implements. Anything else
    #: Discord sends gets "Unknown command."
    commands = [
        "/guardian \u2014 a one-line status in the channel",
    ]

    def validate(self, fields: dict) -> str:
        return urlpolicy.check_url((fields or {}).get("webhook_url") or "", "Discord webhook URL")

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        if not ctx.secret:
            return SendResult(False, "no Discord webhook URL configured")
        desc = msg.body + (f"\n[Open Vault]({msg.url})" if msg.url else "")
        embed = {"title": msg.title, "description": desc, "color": _COLOR.get(msg.severity, 0x888888)}
        return _post_json(ctx.secret, {"embeds": [embed]})

    def watchdog(self, ctx: SendContext) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        if now.hour != digest_hour(ctx.config):
            return
        self.send(ctx, Message(title="Vault daily digest",
                                body="Daily summary — see the dashboard for full detail.",
                                severity="INFO", kind="digest"))
        if now.weekday() == 0:   # Monday
            self.send(ctx, Message(title="Vault weekly summary",
                                    body="Start-of-week summary — see the dashboard for full detail.",
                                    severity="INFO", kind="weekly"))
        # A weekly/monthly report with a live share link, created since the
        # last tick, gets its own message with the actual link.
        cutoff = (now - dt.timedelta(hours=1)).isoformat(timespec="seconds")
        reports = (ctx.db.query(m.Report)
                   .filter(m.Report.tenant_id == ctx.tenant.id, m.Report.kind.in_(("weekly", "monthly")),
                           m.Report.share_token.isnot(None), m.Report.created >= dt.datetime.fromisoformat(cutoff))
                   .all())
        for r in reports:
            link = f"{ctx.base_url.rstrip('/')}/r/{r.share_token}"
            self.send(ctx, Message(title=f"Vault {r.kind} report ready",
                                    body=f"[View the report]({link})", severity="INFO", kind="report", url=link))

    def inbound(self, ctx: SendContext, request_body: dict, headers: dict) -> InboundResult:
        public_key = (ctx.config or {}).get("public_key", "")
        signature = headers.get("x-signature-ed25519") or headers.get("X-Signature-Ed25519", "")
        timestamp = headers.get("x-signature-timestamp") or headers.get("X-Signature-Timestamp", "")
        raw_body = headers.get("_raw_body", "")
        # Finding 10: freshness FIRST -- a captured interaction's signature
        # stays valid forever, so the timestamp is the only thing bounding a
        # replay.
        if not _timestamp_fresh(timestamp):
            return InboundResult(ok=False, body={"error": "stale request signature"}, status=401)
        if not _verify_ed25519(public_key, signature, timestamp, raw_body):
            return InboundResult(ok=False, body={"error": "invalid request signature"}, status=401)

        if request_body.get("type") == _PING_TYPE:
            return InboundResult(ok=True, body=_PONG, status=200)

        if request_body.get("type") == _APPLICATION_COMMAND:
            name = ((request_body.get("data") or {}).get("name") or "").lower()
            if name in ("guardian", "status"):
                return InboundResult(ok=True, status=200,
                                      body={"type": 4, "data": {"content": _status_line(ctx)}})
            return InboundResult(ok=True, status=200,
                                  body={"type": 4, "data": {"content": "Unknown command."}})

        return InboundResult(ok=True, body={}, status=200)
