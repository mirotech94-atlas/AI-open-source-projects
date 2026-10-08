"""Slack incoming-webhook integration: a Block-Kit-ish attachment payload
with a severity colour bar. stdlib `urllib` only, 10 s timeout.

`watchdog()` posts the tenant's daily digest at its configured hour
(`config.digest_hour`, default 9) — the hourly APScheduler tick fires this
once per day the hour matches.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request

from .base import Integration, IntegrationField, Message, SendContext, SendResult
from .registry import register

TIMEOUT_S = 10
_COLOR = {"CRIT": "#d92c2c", "WARN": "#e0a72c", "INFO": "#2c6fe0"}


def _post_json(url: str, body: dict) -> SendResult:
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                      headers={"Content-Type": "application/json", "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S):
            return SendResult(True, "")
    except Exception as e:
        return SendResult(False, str(e)[:200])


@register
class SlackIntegration(Integration):
    kind = "slack"
    title = "Slack"
    help = "Post alerts to a Slack channel via an incoming webhook."
    direction = "out"
    fields = [IntegrationField(name="url", label="Webhook URL", kind="url", required=True),
              IntegrationField(name="channel_id", label="Channel ID for the Vault bot (e.g. C0C1WA82HFB) -- invite @Vault to the channel", kind="text"),
              IntegrationField(name="allowed_users", label="Slack user IDs that may make changes as admin (comma-separated; blank = everyone is a viewer)", kind="text"),
              IntegrationField(name="accept_commands", label="Accept incoming commands independently of outgoing notifications", kind="bool"),
              IntegrationField(name="user_roles", label="Individual roles: U123=admin, U456=operator (unlisted people are viewers)", kind="text"),
              IntegrationField(name="named_approver", label="Optional named approver: one Slack user ID with an admin role", kind="text")]
    #: What the bot actually answers in the channel, from
    #: `routers/slackbot.py`. The last line is not a feature note: it says
    #: who can change the network from that channel. With `allowed_users`
    #: blank everyone there is a viewer (it used to be admin, until the
    #: 2026-09-28 audit), so an owner who wants to act from Slack has to know
    #: to list themselves.
    commands = [
        "Ask it anything: \u201cis anything wrong?\u201d, \u201cwhy is the NAS offline?\u201d",
        "\u201cfix it\u201d in a reply under an alert means that alert",
        "\u201cyes\u201d runs a change Vault has proposed, \u201cno\u201d drops it",
        "Everyone in the channel is a viewer here unless you list their user IDs above",
    ]

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        text = f"*{msg.title}*\n{msg.body}" + (f"\n<{msg.url}|Open Vault>" if msg.url else "")
        channel = ((ctx.config or {}).get("channel_id") or "").strip()
        # With the bot installed and a channel id on the card, post AS THE
        # BOT: the reply carries the message ts, so a person replying in that
        # message's thread ("fix it") is replying to a known alert -- the
        # webhook path cannot give us that.
        if channel and bot_token():
            alert_text = f"{msg.title}\n{msg.body}"
            if msg.severity in ("WARN", "CRIT") and msg.kind in ("alert", "report"):
                text += "\n_Reply *fix it* and I will handle it, or ask me about it._"
            res = bot_post(channel, text, msg.severity)
            if res.ok:
                if res.error:   # error slot carries the ts on success
                    remember_alert_thread(channel, res.error, alert_text)
                # Only things worth fixing: an INFO notice ("device online at
                # an unusual hour") must not be what a later "fix it" means.
                if msg.severity in ("WARN", "CRIT"):
                    if msg.kind == "resolve":
                        forget_recent_alert(channel, msg.dedup_key or "")
                    else:
                        remember_recent_alert(channel, alert_text, msg.dedup_key or "")
                return SendResult(True, "")
        if not ctx.secret:
            return SendResult(False, "no Slack webhook URL configured")
        body = {"attachments": [{"color": _COLOR.get(msg.severity, "#888888"), "text": text}]}
        return _post_json(ctx.secret, body)

    def watchdog(self, ctx: SendContext) -> None:
        hour = int((ctx.config or {}).get("digest_hour", 9))
        if dt.datetime.now(dt.timezone.utc).hour != hour:
            return
        self.send(ctx, Message(title="Vault daily digest",
                                body="Daily summary — see the dashboard for full detail.",
                                severity="INFO", kind="digest"))


# --------------------------------------------------------------------------
# The bot half (one Slack app for the hub). Token from settings; the alert
# thread map lets a reply in an alert's thread name the alert.
# --------------------------------------------------------------------------
from collections import OrderedDict

from ..config import settings

_ALERT_THREADS: "OrderedDict[tuple[str, str], str]" = OrderedDict()   # (channel, ts) -> alert text
_ALERT_THREADS_MAX = 2000


def bot_token() -> str:
    return (settings.slack_bot_token or "").strip()


def remember_alert_thread(channel: str, ts: str, text: str) -> None:
    _ALERT_THREADS[(channel, ts)] = text[:1200]
    while len(_ALERT_THREADS) > _ALERT_THREADS_MAX:
        _ALERT_THREADS.popitem(last=False)


def alert_for_thread(channel: str, thread_ts: str) -> str:
    return _ALERT_THREADS.get((channel, thread_ts), "")


#: channel -> the last few alerts the bot posted there, newest first, so a
#: bare "fix it" in the channel (replies are not threaded) means those.
_RECENT: "OrderedDict[str, list[str]]" = OrderedDict()
_RECENT_KEEP = 5


def remember_recent_alert(channel: str, text: str, key: str = "") -> None:
    lst = _RECENT.get(channel) or []
    entry = (f"[{key}] " if key else "") + text[:600]
    _RECENT[channel] = [entry] + [x for x in lst if x != entry][:_RECENT_KEEP - 1]
    while len(_RECENT) > _ALERT_THREADS_MAX:
        _RECENT.popitem(last=False)


def forget_recent_alert(channel: str, key: str) -> None:
    """An alert that resolved is no longer "the alert(s) you just sent me":
    drop every line for it so a later bare "fix it" targets what is actually
    open. Leaving a resolved fire (and a "resolved" notice) in the list handed
    the engine a stale "FlyCheap DOWN" beside the live alert -- and its DOWN
    keyword is what steered the rules fallback into "Everything is up"."""
    if not key:
        return
    tag = f"[{key}] "
    kept = [x for x in (_RECENT.get(channel) or []) if not x.startswith(tag)]
    if kept:
        _RECENT[channel] = kept
    else:
        _RECENT.pop(channel, None)


def recent_alerts(channel: str) -> list[str]:
    return list(_RECENT.get(channel) or [])


def bot_post(channel: str, text: str, severity: str = "INFO", thread_ts: str | None = None,
             plain: bool = False) -> SendResult:
    """chat.postMessage as the bot. On success `SendResult.error` carries the
    message ts (the only extra field the caller needs) -- see `send()`.
    `plain` posts the text as the message body (a conversational reply);
    otherwise it rides in a colour-barred attachment like the webhook path."""
    tok = bot_token()
    if not tok:
        return SendResult(False, "no bot token")
    body: dict = {"channel": channel, "unfurl_links": False}
    if plain:
        body["text"] = text
    else:
        # The attachment carries the text; `fallback` is what notifications
        # and old clients show. Setting top-level `text` too makes Slack
        # render the whole message twice.
        body["attachments"] = [{"color": _COLOR.get(severity, "#888888"), "text": text, "fallback": text[:300]}]
    if thread_ts:
        body["thread_ts"] = thread_ts
    try:
        req = urllib.request.Request("https://slack.com/api/chat.postMessage", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json; charset=utf-8",
                                              "Authorization": f"Bearer {tok}", "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            j = json.loads(r.read() or b"{}")
        if not j.get("ok"):
            return SendResult(False, str(j.get("error") or "slack error")[:120])
        return SendResult(True, str(j.get("ts") or ""))
    except Exception as e:
        return SendResult(False, str(e)[:200])

