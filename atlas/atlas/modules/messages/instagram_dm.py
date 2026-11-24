"""instagram_dm — Instagram DMs via the Graph API's Messenger platform.

Shares `INSTAGRAM_ACCOUNT_ID` / `INSTAGRAM_TOKEN` with
`atlas.modules.instagram`, whose `_get` shape this mirrors, but this module
never touches the feed — it only reads and sends direct messages, and only
`messages.reply` (in `messages/__init__.py`) may call `send()`. Sending a
DM needs the token to carry `instagram_manage_messages`; `check_scope`
exists because a token that quietly lost that scope looks, from every other
angle, like a healthy integration that has simply gone silent.

Endpoints (the reading path verified live 2026-09-27): the Page token from
`GET /{INSTAGRAM_PAGE_ID}?fields=access_token`, then `GET /{page}/conversations
?platform=instagram&fields=id,updated_time` and `GET /{conversation}?fields=
messages{id,from,message,created_time}` for history; `GET /{conversation}
?fields=participants` for the person to reply to (the participant who is not
the Page or this IG account -- a conversation id is NOT a recipient);
`POST /{page}/messages` with `{"recipient": {"id": <igsid>}, "message":
{"text": ...}}` to send (assumed, the same page-scoped path); `GET /debug_token?input_token=<token>` to read the
token's own scopes. Graph normally wants a separate app-level access token
to inspect another token — this reuses the same user/page token for both
`input_token` and `access_token`, so if this check always comes back
UNKNOWN, that call is the first thing to correct. `is_from_me` is inferred
by comparing a message's `from.id` to `INSTAGRAM_ACCOUNT_ID` — also
unverified, and worth correcting if the id in that field turns out to be
something else (a page-scoped id, for instance).
"""
from __future__ import annotations

import os

from ...kernel.contracts import Health, Observation
from .._http import get_json, post_json

GRAPH = "https://graph.facebook.com/v21.0"


def _cfg() -> tuple[str, str] | None:
    from ..instagram import switched_off
    if switched_off():
        return None                 # the owner, 2026-10-11: Instagram is off
    ig_id = os.getenv("INSTAGRAM_ACCOUNT_ID", "").strip()
    token = os.getenv("INSTAGRAM_TOKEN", "").strip()
    if not ig_id or not token:
        return None
    return ig_id, token


def _page_id() -> str:
    """The Facebook Page linked to INSTAGRAM_ACCOUNT_ID (`INSTAGRAM_PAGE_ID`).
    Verified live 2026-09-27: Instagram DMs are listed through the PAGE with
    a PAGE token -- `/{ig-account}/conversations` with the user token answers
    400 even with instagram_manage_messages granted."""
    return os.getenv("INSTAGRAM_PAGE_ID", "").strip()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_PAGE_TOKEN: dict[str, tuple[float, str]] = {}
PAGE_TOKEN_TTL_S = 3600.0


async def _page_token(page_id: str, user_token: str) -> tuple[str | None, str | None]:
    """The Page's own access token, from the user token, cached an hour."""
    import time
    held = _PAGE_TOKEN.get(page_id)
    if held and time.monotonic() - held[0] < PAGE_TOKEN_TTL_S:
        return held[1], None
    data, reason = await get_json(f"{GRAPH}/{page_id}", headers=_auth(user_token),
                                  params={"fields": "access_token"})
    if reason:
        return None, reason
    token = (data or {}).get("access_token") if isinstance(data, dict) else None
    if not token:
        return None, (f"Page {page_id} gave no access_token — the user token "
                      f"needs pages_show_list and a role on that Page")
    _PAGE_TOKEN[page_id] = (time.monotonic(), str(token))
    return str(token), None


# Both accounts' DMs (the owner enabled message access on both, 2026-09-27):
# (label, env prefix for the IG account and token, env names for its Page).
DM_ACCOUNTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("main", "INSTAGRAM_", ("INSTAGRAM_PAGE_ID",)),
    ("stories", "INSTAGRAM_STORIES_", ("INSTAGRAM_STORIES_PAGE_ID",
                                       "FACEBOOK_STORIES_PAGE_ID")),
)


def _account(prefix: str, page_envs: tuple[str, ...]) -> tuple[str, str, str] | None:
    ig_id = os.getenv(f"{prefix}ACCOUNT_ID", "").strip()
    token = os.getenv(f"{prefix}TOKEN", "").strip()
    page = next((os.getenv(e, "").strip() for e in page_envs
                 if os.getenv(e, "").strip()), "")
    if not (ig_id and token and page):
        return None
    return ig_id, token, page


async def recent(limit: int = 25) -> tuple[list[dict], str | None]:
    """Recent DM threads on every configured account, flattened to one
    entry per message, each tagged with its `account`. Includes the owner's own
    outgoing messages (`is_from_me`). A thread id from the stories account
    is prefixed `stories:` so the two accounts' threads never collide. When
    one account cannot be read its reason is returned beside the other's
    rows -- never a silent partial read."""
    if _cfg() is None:
        return [], "INSTAGRAM_ACCOUNT_ID / INSTAGRAM_TOKEN not set"
    rows: list[dict] = []
    problems: list[str] = []
    configured = 0
    for label, prefix, page_envs in DM_ACCOUNTS:
        acct = _account(prefix, page_envs)
        if acct is None:
            if label == "main":
                problems.append("main: INSTAGRAM_PAGE_ID not set")
            continue
        configured += 1
        got, why = await _recent_one(label, *acct, limit=limit)
        rows += got
        if why:
            problems.append(f"{label}: {why}")
    if not configured:
        return [], "INSTAGRAM_PAGE_ID not set"
    return rows, ("; ".join(problems) or None)


async def _recent_one(label: str, ig_id: str, user_token: str, page_id: str, *,
                      limit: int) -> tuple[list[dict], str | None]:
    token, reason = await _page_token(page_id, user_token)
    if token is None:
        return [], reason
    data, reason = await get_json(
        f"{GRAPH}/{page_id}/conversations", headers=_auth(token),
        params={"platform": "instagram", "fields": "id,updated_time",
                "limit": limit})
    if reason:
        return [], reason
    convos = (data or {}).get("data") if isinstance(data, dict) else None
    if not isinstance(convos, list):
        return [], "unexpected shape from /conversations — no data list"

    async def messages_of(convo_id: str) -> list[dict] | None:
        """A thread's messages, or None when they could not be read.

        Returning [] for a failed or malformed read made the thread vanish:
        no rows, no problem recorded, and `unanswered` read 0, OK (RA-E1).
        """
        got, why = await get_json(
            f"{GRAPH}/{convo_id}", headers=_auth(token),
            params={"fields": "messages{id,from,message,created_time}"})
        if why or not isinstance(got, dict):
            return None
        box = got.get("messages")
        listed = box.get("data") if isinstance(box, dict) else None
        return listed if isinstance(listed, list) else None

    import asyncio
    ids = [str(c.get("id")) for c in convos if isinstance(c, dict) and c.get("id")]
    per = await asyncio.gather(*(messages_of(i) for i in ids[:limit]))
    unread_threads = sum(1 for got in per if got is None)
    out: list[dict] = []
    for convo_id, messages in zip(ids, per, strict=False):
        if messages is None:
            continue
        thread_id = convo_id if label == "main" else f"{label}:{convo_id}"
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            sender = (msg.get("from") or {}).get("id", "")
            out.append({
                "external_id": msg.get("id"),
                "thread_id": thread_id,
                "sender": sender,
                "text": msg.get("message") or "",
                "at": msg.get("created_time"),
                "is_from_me": sender == ig_id,
                "account": label,
            })
    if unread_threads:
        return out, (f"{unread_threads} of {len(per)} conversation(s) could not "
                     f"be read")
    return out, None


def _account_for(thread_id: str) -> tuple[tuple[str, str, str] | None, str, str | None]:
    """The (ig_id, user_token, page_id) that owns `thread_id`, the bare
    conversation id, and why there is no account when there is none. A
    `stories:` prefix picks the stories account; no prefix is main."""
    label, _, bare = thread_id.rpartition(":")
    prefix, page_envs = next(((p, e) for n, p, e in DM_ACCOUNTS
                              if n == (label or "main")), (None, ()))
    acct = _account(prefix, page_envs) if prefix else None
    if acct is None:
        if prefix is None:
            return None, bare, f"no Instagram account is labelled {label!r}"
        if _cfg() is None:
            return None, bare, "INSTAGRAM_ACCOUNT_ID / INSTAGRAM_TOKEN not set"
        return None, bare, "INSTAGRAM_PAGE_ID not set"
    return acct, bare, None


async def resolve_recipient(thread_id: str) -> tuple[str | None, str | None]:
    """The other person's Instagram-scoped id (IGSID) in a conversation.

    `thread_id` is what `recent()` stores: a CONVERSATION id (with a
    `stories:` prefix for the stories account), which is not something the
    send endpoint can address. The conversation's `participants` are read
    through the Page with the Page token, and everyone who is us -- the
    Page itself and this account's own IG id -- is dropped. Exactly one id
    must remain; none, several or an unexpected shape is a reason, never a
    guess, so a reply is refused rather than sent to the wrong person."""
    acct, bare, why = _account_for(thread_id)
    if acct is None:
        return None, why
    if not bare:
        return None, "no conversation id stored for this message"
    ig_id, user_token, page_id = acct
    token, reason = await _page_token(page_id, user_token)
    if token is None:
        return None, reason
    data, reason = await get_json(f"{GRAPH}/{bare}", headers=_auth(token),
                                  params={"fields": "participants"})
    if reason:
        return None, f"could not read the conversation's participants: {reason}"
    box = data.get("participants") if isinstance(data, dict) else None
    listed = box.get("data") if isinstance(box, dict) else None
    if not isinstance(listed, list):
        return None, "the conversation came back with no participants list"
    ours = {page_id, ig_id}
    others = sorted({str(p.get("id")) for p in listed
                     if isinstance(p, dict) and p.get("id")
                     and str(p.get("id")) not in ours})
    if not others:
        return None, "the conversation lists no participant other than us"
    if len(others) > 1:
        return None, (f"the conversation lists {len(others)} other participants; "
                      f"a reply needs exactly one")
    return others[0], None


async def send(thread_id: str, recipient_id: str,
               text: str) -> tuple[dict | None, str | None]:
    """Send `text` to `recipient_id`, the person's Instagram-scoped id
    (IGSID) from `resolve_recipient` -- never the conversation id, which
    the Graph send endpoint does not address. `thread_id` (the stored
    conversation id) only picks which account, Page and Page token send
    it, the way the conversations are read. The one call in this file that
    puts a message in front of someone who is not the owner; reached only from
    `messages.reply`."""
    acct, bare, why = _account_for(thread_id)
    if acct is None:
        return None, why
    recipient_id = (recipient_id or "").strip()
    if not recipient_id or recipient_id == bare:
        return None, "refused: no Instagram-scoped recipient id to send to"
    _ig_id, user_token, page_id = acct
    token, reason = await _page_token(page_id, user_token)
    if token is None:
        return None, reason
    return await post_json(
        f"{GRAPH}/{page_id}/messages", headers=_auth(token),
        json_body={"recipient": {"id": recipient_id}, "message": {"text": text}},
    )


async def check_scope() -> Observation:
    """Whether INSTAGRAM_TOKEN actually carries `instagram_manage_messages`.
    UNKNOWN on anything unexpected — never assumed OK just because the call
    itself did not error."""
    cfg = _cfg()
    if cfg is None:
        return Observation("dm_scope", Health.UNKNOWN,
                           detail="INSTAGRAM_ACCOUNT_ID / INSTAGRAM_TOKEN not set")
    _, token = cfg
    data, reason = await get_json(
        f"{GRAPH}/debug_token", headers=_auth(token),
        params={"input_token": token})
    if reason:
        return Observation("dm_scope", Health.UNKNOWN, detail=reason)
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, dict) or "scopes" not in inner:
        return Observation(
            "dm_scope", Health.UNKNOWN,
            detail="debug_token response had no scopes field — the assumed "
                   "shape is probably wrong")
    scopes = inner.get("scopes") or []
    if not isinstance(scopes, list):
        return Observation("dm_scope", Health.UNKNOWN,
                           detail="debug_token scopes was not a list")
    if "instagram_manage_messages" not in scopes:
        return Observation(
            "dm_scope", Health.DEGRADED,
            detail=f"INSTAGRAM_TOKEN lacks instagram_manage_messages; "
                   f"has: {', '.join(str(s) for s in scopes) or 'no scopes reported'}",
            evidence={"scopes": scopes})
    return Observation("dm_scope", Health.OK,
                       detail="instagram_manage_messages present",
                       evidence={"scopes": scopes})
