"""`POST /inbound/slack-app` -- the two-way Vault Slack bot.

One Slack app for the whole hub (bot token + signing secret in the hub env,
never per tenant). Slack sends every message from the channels the bot has
been invited to here; the channel id says which tenant is talking:

* the operator's admin channel (`GUARDIAN_ADMIN_SLACK_CHANNEL`) acts as the
  admin of `GUARDIAN_ADMIN_SLACK_TENANT`;
* any other channel is looked up on the tenants' Slack cards
  (`config.channel_id`) and acts as that tenant's admin -- unless the card
  lists `allowed_users`, in which case other people in the channel get the
  viewer role (questions yes, changes no).

A message runs through `routers.chat.run_turn` -- the same engine, tools,
role gate and single-use confirmation as the web chat. When the engine
answers with a proposal (a tier-3 change it will only make once told to),
the token is parked on the Slack thread and a plain "yes" there runs it; a
"no" drops it. Replies are ordinary channel messages, not threads (the
owner's choice): one running conversation per channel, so "yes" answers
the last proposal made in that channel. A person who does reply under
one of the bot's alert posts still gets the alert fed to the engine as
context, so "fix it" under an alert means that alert.

Slack wants its 200 inside three seconds and a Groq round trip takes
longer, so the turn runs in a background thread on its own DB session;
Slack's retries of the same event are deduplicated by event id.

Security: every request is verified against the signing secret
(`v0:{ts}:{raw body}` HMAC-SHA256, five-minute replay window) before it is
parsed; with no secret configured the route is a plain 404. Bot and
system messages are ignored, so the bot cannot talk to itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import models as m, ratelimit
from ..config import settings
from ..db import SessionLocal
from ..integrations import slack as slack_mod
from ..schemas_dash import CHAT_MESSAGE_MAX, ChatRequest, ChatResponse
from .chat import run_turn
from .. import errfmt

log = logging.getLogger("guardian.slackbot")
router = APIRouter(tags=["slack-bot"])

REPLAY_WINDOW_S = 300
_SEEN_EVENTS: "OrderedDict[str, float]" = OrderedDict()
_SEEN_MAX = 5000
#: Replies go to the channel itself, not into threads. Flip to reply in a
#: thread under the person's message instead.
REPLY_IN_THREAD = False
#: channel -> ChatThread id: one running conversation per channel, so a
#: follow-up continues what the engine already has context for.
_THREADS: "OrderedDict[str, str]" = OrderedDict()
#: channel -> the proposal token a "yes" in that channel burns.
_PENDING: "OrderedDict[str, str]" = OrderedDict()
_MAP_MAX = 2000

_YES = re.compile(r"^\s*(?:yes|y|yes please|yep|yeah|ok|okay|sure|go|go ahead|do it|confirm|confirmed|approve|approved|proceed)\s*[.!]*\s*$", re.I)
_NO = re.compile(r"^\s*(?:no|nope|cancel|stop|don'?t|never mind|nevermind|abort)\s*[.!]*\s*$", re.I)
_MENTION = re.compile(r"<@[A-Z0-9]+>")
#: A bare repair request with no target of its own ("fix it", "handle this",
#: "sort it out please") -- it means the alert(s) the bot just posted here.
_FIX_IT = re.compile(
    r"^\s*(?:please\s+|ok\s+|okay\s+)?(?:fix|handle|resolve|sort(?: out)?|deal with|take care of|remediate|repair|clear|action)"
    r"\s*(?:it|this|that|them|these|those|the alerts?|the issues?|the problems?|everything|all|it all|all of (?:it|them|these))?"
    r"\s*(?:out|up)?\s*(?:now|please|asap)?\s*[.!]*\s*$", re.I)


def _remember(d: "OrderedDict", key, value) -> None:
    d[key] = value
    while len(d) > _MAP_MAX:
        d.popitem(last=False)


def enabled() -> bool:
    return bool((settings.slack_signing_secret or "").strip() and slack_mod.bot_token())


def verify_signature(secret: str, ts: str, raw: bytes, given: str, *, now: float | None = None) -> bool:
    """Slack's `X-Slack-Signature` check: `v0=` + HMAC-SHA256(secret,
    `v0:{ts}:{raw}`), and the timestamp inside the replay window."""
    if not secret or not ts or not given:
        return False
    try:
        if abs((now if now is not None else time.time()) - float(ts)) > REPLAY_WINDOW_S:
            return False
    except ValueError:
        return False
    base = f"v0:{ts}:".encode() + raw
    want = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, given)


def _slack_to_text(text: str) -> str:
    """Strip the bot mention and Slack's link markup so the engine sees what
    a person would have typed."""
    t = _MENTION.sub("", text or "")
    t = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", t)
    t = re.sub(r"<(https?://[^>]+)>", r"\1", t)
    return t.strip()


_MAC = re.compile(r"(?<![`\w:])((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})(?![`\w:])")


def _to_mrkdwn(text: str) -> str:
    """The engine writes markdown-ish; Slack's mrkdwn wants single-star bold.
    MAC addresses go in code spans: Slack otherwise reads `:ab:` inside
    `b8:ab:62:...` as an emoji shortcode and draws a picture in the middle
    of the address."""
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text or "")
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    t = _MAC.sub(r"`\1`", t)
    return t.strip()


# --------------------------------------------------------------------------
# Channel -> (tenant, user, role)
# --------------------------------------------------------------------------

def _tenant_admin_user(db, tenant) -> "m.User | None":
    """The person the bot acts as for a tenant: a superadmin member first,
    else the earliest admin member."""
    rows = (db.query(m.Membership, m.User).join(m.User, m.User.id == m.Membership.user_id)
            .filter(m.Membership.tenant_id == tenant.id, m.Membership.role == "admin").all())
    if not rows:
        return None
    rows.sort(key=lambda r: (not r[1].is_superadmin, r[1].created))
    return rows[0][1]


def resolve_channel(db, channel: str, slack_user: str, team_id: str = "") -> tuple | None:
    """(tenant, user, role, card_config) for a channel the bot serves, or
    None when the channel is nobody's.

    A tenant card's channel grants viewer unless the card names who may do
    more: `allowed_users` (those Slack ids act as admin) or `user_roles`
    (`U123=operator,...`). An empty list used to mean "everyone here is
    admin", so anyone who could post in the channel -- a guest, a shared
    Slack Connect member, a new hire -- could quarantine devices and queue
    fixes on the customer's network (audit 2026-09-28, pass 2). A card that
    records its workspace (`team_id`) answers only events from it."""
    admin_ch = (settings.admin_slack_channel or "").strip()
    if admin_ch and channel == admin_ch:
        tenant = db.query(m.Tenant).filter_by(slug=(settings.admin_slack_tenant or "home").strip()).first()
        if tenant is None:
            return None
        user = _tenant_admin_user(db, tenant)
        return (tenant, user, "admin", {}) if user else None
    for row in db.query(m.Integration).filter_by(kind="slack").all():
        # A channel the tenant has switched OFF drives nothing. The card's
        # toggle read as "stop sending me alerts" while this path went on
        # accepting instructions from that same channel -- and a Slack
        # message here runs as the tenant's ADMIN, so an owner who thought
        # they had cut the integration still had a room full of people able
        # to change their network.
        if not row.enabled:
            continue
        cfg = row.config or {}
        if not cfg.get("accept_commands", row.enabled):
            continue
        if (cfg.get("channel_id") or "").strip() != channel:
            continue
        pinned_team = (cfg.get("team_id") or "").strip()
        if pinned_team and pinned_team != (team_id or "").strip():
            continue
        tenant = db.get(m.Tenant, row.tenant_id)
        if tenant is None:
            return None
        user = _tenant_admin_user(db, tenant)
        if user is None:
            return None
        allowed = [u.strip().upper() for u in (cfg.get("allowed_users") or "").split(",") if u.strip()]
        role = "admin" if (allowed and slack_user.upper() in allowed) else "viewer"
        assignments = dict(part.strip().split("=", 1) for part in (cfg.get("user_roles") or "").split(",") if "=" in part)
        if assignments:
            role = assignments.get(slack_user, "viewer").strip()
            if role not in ("viewer", "operator", "admin"):
                role = "viewer"
        return tenant, user, role, cfg
    return None


# --------------------------------------------------------------------------
# One message -> one engine turn -> one threaded reply
# --------------------------------------------------------------------------

def _error_text(result) -> str:
    if isinstance(result, dict):
        result = result.get("text") or result.get("error") or result.get("out") or "The tool reported an error."
    return str(result)[:140]


def _format_reply(resp: ChatResponse) -> str:
    """Short and to the point: the answer, then ONE line for what was done,
    then the yes/no line if something is waiting. Errors are humanised."""
    lines = [_to_mrkdwn(resp.text)]
    marks = {"ok": "✓", "queued": "⏳", "error": "✗", "denied": "⛔"}
    steps = [s for s in resp.tool_steps if s.status != "proposed"]
    if steps:
        parts, errs = [], []
        for s in steps:
            what = s.args.get("target") or s.args.get("mac") or s.args.get("host") or s.args.get("key") or ""
            name = s.tool.replace("_", " ") + (f" {what}" if what else "")
            if s.status == "error":
                why = errfmt.humanize(getattr(s, "result", "") or "")
                errs.append(f"✗ {name}" + (f" — {why}" if why else ""))
            else:
                parts.append(f"{marks.get(s.status, '•')} {name}")
        if parts:
            lines.append(" · ".join(parts))
        lines.extend(errs)
    if resp.proposal:
        lines.append("Reply *yes* to run it, or *no* to drop it.")
    return "\n".join(l for l in lines if l).strip()


def _handle_message(channel: str, slack_user: str, text: str, ts: str, thread_ts: str | None,
                    team_id: str = "") -> str | None:
    """Run one Slack message through the engine and post the reply. Returns
    the reply text (for tests) or None when the message was not for us."""
    key = f"{channel}:{slack_user}"
    reply_ts = (thread_ts or ts) if REPLY_IN_THREAD else None
    db = SessionLocal()
    try:
        who = resolve_channel(db, channel, slack_user, team_id=team_id)
        if who is None:
            return None
        tenant, user, role, _cfg = who
        key = f"{tenant.id}:{channel}:{slack_user}"
        state_id = hashlib.sha256(key.encode()).hexdigest()
        saved = db.get(m.ConversationState, state_id)
        if saved is None:
            saved = m.ConversationState(id=state_id, tenant_id=tenant.id, data={})
            db.add(saved)
            db.commit()
        stored = saved.data or {}
        if stored.get("thread"):
            _remember(_THREADS, key, stored["thread"])
        token = db.get(m.ActToken, stored.get("pending")) if stored.get("pending") else None
        if token and not token.used and token.expires.timestamp() > time.time():
            _remember(_PENDING, key, token.token)
        else:
            _PENDING.pop(key, None)
        if not ratelimit.allow(f"slack:{channel}", 20, 60):
            slack_mod.bot_post(channel, "Slow down a little -- I take at most 20 messages a minute per channel.",
                               thread_ts=reply_ts, plain=True)
            return "rate-limited"

        message = _slack_to_text(text)
        if not message:
            return None

        confirm_token = None
        pending = _PENDING.get(key)
        if not pending and _YES.match(message) and role == "admin" and (_cfg.get("named_approver") or "").strip() == slack_user:
            choices=[]
            for state in db.query(m.ConversationState).filter_by(tenant_id=tenant.id).all():
                info=state.data or {}
                if info.get("channel") != channel or info.get("approver") != slack_user or not info.get("pending"):
                    continue
                proposed=db.get(m.ActToken,info["pending"])
                if proposed and not proposed.used and proposed.expires.timestamp() > time.time():
                    choices.append((state,proposed))
            if len(choices) > 1:
                out="Several proposals await approval. Ask each requester to confirm their own exact proposal; a bare yes cannot choose between them."
                slack_mod.bot_post(channel,out,thread_ts=reply_ts,plain=True)
                return out
            if len(choices) == 1:
                source, proposed=choices[0]
                pending=proposed.token
                _remember(_PENDING,key,pending)
                if (source.data or {}).get("thread"):
                    _remember(_THREADS,key,source.data["thread"])
                source.data={**(source.data or {}),"pending":None,"approved_by":slack_user}
                db.commit()
        if pending and _NO.match(message):
            _PENDING.pop(key, None)
            saved.data = {"thread": _THREADS.get(key)}
            db.commit()
            out = "Dropped. Nothing was changed."
            slack_mod.bot_post(channel, out, thread_ts=reply_ts, plain=True)
            return out
        if pending and _YES.match(message):
            confirm_token = _PENDING.pop(key)
            saved.data = {"thread": _THREADS.get(key)}
            db.commit()
        elif _YES.match(message) and not pending:
            out = "The previous proposal expired or was already used. Request a new proposal." if stored.get("pending") else "There is nothing waiting for a yes. Tell me what you want done."
            slack_mod.bot_post(channel, out, thread_ts=reply_ts, plain=True)
            return out

        # A reply under one of the bot's own alert posts is about that alert;
        # a bare "fix it" in the channel is about the alert(s) just posted.
        alert_text = slack_mod.alert_for_thread(channel, thread_ts) if thread_ts else ""
        engine_text = message
        if alert_text and not confirm_token:
            engine_text = f"About this alert you sent me:\n{alert_text}\n\n{message}"
        elif not confirm_token and _FIX_IT.match(message):
            recent = slack_mod.recent_alerts(channel)
            if len(recent) > 1:
                out = "Several recent alerts could match. Reply under the alert you want handled, or name its target and action:\n" + "\n---\n".join(recent)
                slack_mod.bot_post(channel, out, thread_ts=reply_ts, plain=True)
                return out
            if recent:
                engine_text = ("About the alert(s) you just sent me:\n" + "\n---\n".join(recent)
                               + f"\n\n{message}. Fix whatever you can yourself; if you need something "
                                 "from me first, tell me exactly what to do.")

        # `ChatRequest` refuses a message over CHAT_MESSAGE_MAX, and here that
        # refusal would be a ValidationError inside a background thread: the
        # person gets no reply at all. Clip instead -- the alert context
        # first, since the person's own words are what the turn is for.
        if len(engine_text) > CHAT_MESSAGE_MAX:
            message = message[:CHAT_MESSAGE_MAX]
            room = CHAT_MESSAGE_MAX - len(message)
            engine_text = (engine_text[:len(engine_text) - len(message)][:room] + message
                           if engine_text.endswith(message) and room > 0 else message)
        body = ChatRequest(message=engine_text, thread=_THREADS.get(key), confirm_token=confirm_token)
        try:
            resp = run_turn(db, user, tenant, role, body, request_ip=f"slack:{slack_user}", channel="slack")
        except Exception as e:  # noqa: BLE001 -- the person must hear something
            log.exception("slack turn failed")
            out = f"✗ Could not finish that — {errfmt.humanize(str(e)) or 'unexpected error'}. Try again in a minute."
            slack_mod.bot_post(channel, out, thread_ts=reply_ts, plain=True)
            return out
        _remember(_THREADS, key, resp.thread_id)
        if resp.proposal and resp.proposal.token:
            _remember(_PENDING, key, resp.proposal.token)
        saved.data = {"thread": resp.thread_id, "pending": _PENDING.get(key), "channel":channel, "requester":slack_user, "approver":(_cfg.get("named_approver") or "").strip()}
        db.commit()
        out = _format_reply(resp)
        if resp.proposal and (_cfg.get("named_approver") or "").strip():
            out += f"\nThe named approver {_cfg['named_approver']} may also approve, if their Vault role is admin."
        slack_mod.bot_post(channel, out, thread_ts=reply_ts, plain=True)
        return out
    finally:
        db.close()


# --------------------------------------------------------------------------
# The events endpoint
# --------------------------------------------------------------------------

def _dedup(event_id: str) -> bool:
    """True the first time an event id is seen."""
    if not event_id:
        return True
    if event_id in _SEEN_EVENTS:
        return False
    _SEEN_EVENTS[event_id] = time.time()
    while len(_SEEN_EVENTS) > _SEEN_MAX:
        _SEEN_EVENTS.popitem(last=False)
    return True


def _should_handle(event: dict) -> bool:
    if event.get("type") not in ("message", "app_mention"):
        return False
    if event.get("bot_id") or event.get("subtype"):     # bot posts, edits, joins, file shares...
        return False
    if event.get("channel_type") == "im":
        return False
    return bool(event.get("user") and event.get("channel") and event.get("ts"))


@router.post("/inbound/slack-app")
async def slack_events(request: Request):
    if not enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    raw = await request.body()
    if not verify_signature(settings.slack_signing_secret.strip(), request.headers.get("x-slack-request-timestamp", ""),
                            raw, request.headers.get("x-slack-signature", "")):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "bad json"}, status_code=400)

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    headers = {"X-Slack-No-Retry": "1"}
    if payload.get("type") != "event_callback":
        return Response(status_code=200, headers=headers)
    event = payload.get("event") or {}
    if not _dedup(str(payload.get("event_id") or "")) or not _should_handle(event):
        return Response(status_code=200, headers=headers)
    # An @mention in a channel arrives twice (message + app_mention); the
    # message event is the one handled, keyed by ts.
    if event.get("type") == "app_mention" and not _dedup("ts:" + str(event.get("ts"))):
        return Response(status_code=200, headers=headers)
    if event.get("type") == "message":
        _dedup("ts:" + str(event.get("ts")))

    # `team_id` sits on the envelope; an event can also carry its own `team`.
    team_id = str(payload.get("team_id") or event.get("team") or "")
    threading.Thread(target=handle_message,
                     args=(event["channel"], event["user"], event.get("text", ""), event["ts"], event.get("thread_ts"),
                           team_id),
                     daemon=True).start()
    return Response(status_code=200, headers=headers)


_CONVERSATION_LOCKS = [threading.RLock() for _ in range(64)]

def handle_message(channel: str, slack_user: str, text: str, ts: str, thread_ts: str | None,
                   team_id: str = "") -> str | None:
    key = f"{channel}:{slack_user}"
    with _CONVERSATION_LOCKS[hash(key) % len(_CONVERSATION_LOCKS)]:
        return _handle_message(channel, slack_user, text, ts, thread_ts, team_id)
