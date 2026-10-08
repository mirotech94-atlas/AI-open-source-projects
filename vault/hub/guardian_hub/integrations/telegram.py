"""Telegram: bot API outbound with inline action buttons, `/status` /
`/devices` /`/report` commands inbound, and a pairing flow so the admin's
own chat gets bound without ever typing a chat id by hand.

One tenant configures its own bot (its own token), so the webhook URL this
tenant's `inbound_token` maps to is inherently tenant-scoped — no
cross-tenant chat-id lookup needed on the inbound side, unlike a
multi-tenant bot fronted by one shared Telegram application would need.

Pairing: `POST /api/v1/integrations/telegram/pair` (admin, `routers/
integrations.py`) writes a 6-char code + expiry into this row's
`config["_sync"]`. The admin DMs `/start <code>` to the bot; `inbound()`
below checks the code, binds `chat_id`, and clears the code so it can't be
replayed. Every other command is only honoured from the already-paired
chat — a stranger who finds the bot cannot pull this tenant's alert counts
just by messaging it.

Fix round 1:

- ruling (b) / finding 3: the pairing code, its expiry and the bound
  `chat_id` are provider bookkeeping, NOT form fields, so they live under
  the reserved `config["_sync"]` (`registry.sync_state`). They used to sit
  at the top level of `config`, which a re-save of the card replaced
  wholesale — silently unpairing the chat and stopping every alert.
- finding 5: Telegram's `setWebhook secret_token` is verified on every
  inbound call (`X-Telegram-Bot-Api-Secret-Token`), so the URL token is no
  longer the sole credential. Required field, and verification fails
  closed.
- finding 8: `digest_hour` is a real form field now. It was read out of
  `config` but no field ever wrote it, so the digest was permanently 09:00
  UTC.
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import urllib.request

from .. import models as m
from .base import Integration, IntegrationField, InboundResult, Message, SendContext, SendResult
from .registry import register, sync_state

TIMEOUT_S = 10
_FIX_NOW_LABEL = "fix now"   # Task 42's controller removed `fix` from /act — never render this action here
_SECRET_TOKEN_HEADER = "x-telegram-bot-api-secret-token"
_DEFAULT_DIGEST_HOUR = 9


def digest_hour(config: dict) -> int:
    """Finding 8: `digest_hour` is admin-settable, so it arrives as whatever
    string the form posted -- an empty box, "9 am", "25". Anything that is
    not an hour in 0..23 falls back to the documented 09:00 UTC default
    instead of raising out of the hourly watchdog tick."""
    try:
        hour = int(str((config or {}).get("digest_hour", "")).strip())
    except (TypeError, ValueError):
        return _DEFAULT_DIGEST_HOUR
    return hour if 0 <= hour <= 23 else _DEFAULT_DIGEST_HOUR


def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _post(url: str, body: dict) -> SendResult:
    """Monkeypatchable seam around the one Telegram Bot API call this
    module makes (send/answer)."""
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                      headers={"Content-Type": "application/json", "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return SendResult(True, "")
    except Exception as e:
        return SendResult(False, str(e)[:200])


def _send_message(token: str, chat_id, text: str, buttons: list[tuple[str, str]] | None = None) -> SendResult:
    body = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if buttons:
        body["reply_markup"] = {"inline_keyboard": [[{"text": label, "url": url}] for label, url in buttons]}
    return _post(_api(token, "sendMessage"), body)


def _status_text(ctx: SendContext) -> str:
    db = ctx.db
    open_alerts = db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, status="open").all()
    crit = sum(1 for a in open_alerts if a.sev == "CRIT")
    warn = sum(1 for a in open_alerts if a.sev == "WARN")
    scan = db.query(m.Scan).filter_by(tenant_id=ctx.tenant.id).order_by(m.Scan.ts.desc()).first()
    score = scan.score if scan else None
    return f"{ctx.tenant.name}: score {score if score is not None else '—'}. CRIT {crit}, WARN {warn}."


def _devices_text(ctx: SendContext) -> str:
    devices = ctx.db.query(m.Device).filter_by(tenant_id=ctx.tenant.id).all()
    online = sum(1 for d in devices if d.presence == "ONLINE")
    unverified = sum(1 for d in devices if not d.trusted and not d.label)
    return f"{len(devices)} known devices, {online} online now, {unverified} not verified yet."


def _report_text(ctx: SendContext) -> str:
    r = (ctx.db.query(m.Report).filter_by(tenant_id=ctx.tenant.id)
         .filter(m.Report.share_token.isnot(None)).order_by(m.Report.created.desc()).first())
    if not r:
        return "No shareable report yet — build one from the Reports tab."
    return f"Latest {r.kind} report: {ctx.base_url.rstrip('/')}/r/{r.share_token}"


@register
class TelegramIntegration(Integration):
    kind = "telegram"
    title = "Telegram"
    help = "Alerts with inline action buttons, plus /status, /devices and /report commands from your bot."
    direction = "out+in"
    fields = [
        IntegrationField(name="bot_token", label="Bot token", kind="secret", required=True),
        IntegrationField(name="webhook_secret", label="Webhook secret token", kind="secret", required=True),
        IntegrationField(name="digest_hour", label="Daily digest hour (0-23 UTC)", kind="text"),
    ]
    #: Exactly the three the `inbound` handler below answers, and only from
    #: the paired chat -- every other chat is ignored, which is the point of
    #: the pairing code.
    commands = [
        "/status \u2014 the score, and anything open right now",
        "/devices \u2014 what is on the network",
        "/report \u2014 the latest report",
        "Only the chat you paired with the code gets answers",
    ]

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        if not ctx.secret:
            return SendResult(False, "no Telegram bot token configured")
        # Read-only: `send()` runs on a shared `row.config` (see
        # `registry.deliver`), so it must not create the `_sync` key.
        chat_id = ((ctx.config or {}).get("_sync") or {}).get("chat_id")
        if not chat_id:
            return SendResult(False, "not paired yet — send /start <code> to the bot")
        text = f"*{msg.title}*\n{msg.body}"
        # Render whatever the caller supplied in Message.actions (the same
        # convention ntfy/webhook already follow) — never a hardcoded
        # Fix-now, since Task 42's /act controller doesn't support "fix".
        buttons = [(label, url) for label, url in msg.actions if label.strip().lower() != _FIX_NOW_LABEL][:4]
        return _send_message(ctx.secret, chat_id, text, buttons)

    def watchdog(self, ctx: SendContext) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        sync = sync_state(ctx)
        # An unclaimed pairing code past its TTL is dead weight — clear it
        # so a stale code can't be brute-forced at leisure.
        expires = sync.get("pair_expires")
        if expires and sync.get("pair_code"):
            try:
                if dt.datetime.fromisoformat(expires) < now:
                    sync.pop("pair_code", None)
                    sync.pop("pair_expires", None)
            except ValueError:
                pass

        chat_id = sync.get("chat_id")
        if not chat_id:
            return
        if now.hour != digest_hour(ctx.config):
            return
        _send_message(ctx.secret, chat_id, f"Vault daily digest\n{_status_text(ctx)}")

    def inbound(self, ctx: SendContext, request_body: dict, headers: dict) -> InboundResult:
        # Finding 5: Telegram's own `setWebhook secret_token`, echoed on
        # every delivery. Fails closed -- no stored secret, no inbound.
        expected_token = ctx.secrets.get("webhook_secret", "")
        presented = headers.get(_SECRET_TOKEN_HEADER) or headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not (expected_token and presented and presented.isascii()
                 and hmac.compare_digest(presented, expected_token)):
            return InboundResult(ok=False, body={"error": "invalid secret token"}, status=401)

        message = (request_body or {}).get("message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None or not text:
            return InboundResult(ok=True, body={}, status=200)

        sync = sync_state(ctx)
        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            code = parts[1].strip() if len(parts) > 1 else ""
            expected = sync.get("pair_code", "")
            expires = sync.get("pair_expires", "")
            expired = True
            try:
                expired = not expires or dt.datetime.fromisoformat(expires) < dt.datetime.now(dt.timezone.utc)
            except ValueError:
                expired = True
            if expected and code.upper() == expected.upper() and not expired:
                sync["chat_id"] = chat_id
                sync.pop("pair_code", None)
                sync.pop("pair_expires", None)
                _send_message(ctx.secret, chat_id, "Paired! This chat now receives Vault alerts.")
                return InboundResult(ok=True, body={"paired": True}, status=200)
            _send_message(ctx.secret, chat_id, "That code is invalid or expired.")
            return InboundResult(ok=True, body={"paired": False}, status=200)

        # Every other command only answers the already-paired chat.
        if chat_id != sync.get("chat_id"):
            return InboundResult(ok=True, body={}, status=200)

        if text.startswith("/status"):
            _send_message(ctx.secret, chat_id, _status_text(ctx))
        elif text.startswith("/devices"):
            _send_message(ctx.secret, chat_id, _devices_text(ctx))
        elif text.startswith("/report"):
            _send_message(ctx.secret, chat_id, _report_text(ctx))
        return InboundResult(ok=True, body={}, status=200)
