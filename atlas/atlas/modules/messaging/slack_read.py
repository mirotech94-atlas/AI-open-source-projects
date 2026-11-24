"""Slack — reading, not sending.

Slack is already wired for approvals (`atlas.channels.slack`) and for typed
commands (`atlas.api.slack_events`); CAPABILITIES.md §2.3 asks this module to
add *reading* the workspaces the owner names, not a second way to post. There is
no `send_text` here on purpose.

Uses `SLACK_BOT_TOKEN` and `SLACK_APPROVER_IDS`, both already required
elsewhere for the approval flow to work at all — this module does not gate
its own loading on them (they are not in `optional_env`): an unset token
reads as "no channel configured", not as a module that failed to load.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from .._http import get_json

API = "https://slack.com/api"


class SlackUnreadable(Exception):
    """A channel's history or a thread's replies could not be read. Raised
    rather than skipped: a channel that could not be read is not a channel
    with nothing waiting in it, and a reply lookup that failed is not "he
    has not replied" (invariant 7)."""


def _channels() -> list[str]:
    return [c.strip() for c in os.getenv("MESSAGING_SLACK_CHANNELS", "").split(",")
            if c.strip()]


def _approvers() -> set[str]:
    return {u.strip() for u in os.getenv("SLACK_APPROVER_IDS", "").split(",")
            if u.strip()}


async def _replied(channel: str, thread_ts: str, own_ts: str,
                   approvers: set[str], headers: dict[str, str]) -> bool:
    if not thread_ts:
        return False
    data, reason = await get_json(
        f"{API}/conversations.replies", headers=headers,
        params={"channel": channel, "ts": thread_ts})
    if reason or not isinstance(data, dict) or not data.get("ok"):
        why = reason or (data.get("error") if isinstance(data, dict) else None)
        raise SlackUnreadable(f"replies in {channel}: {why or 'unreadable'}")
    return any(str(r.get("user", "")) in approvers and r.get("ts") != own_ts
              for r in data.get("messages", []) or [])


async def unanswered(hours: int = 24) -> list[dict]:
    """Messages in the configured channels that mention one of the owner's
    approver ids and have not been answered by him in the thread yet.

    A message from an approver, or from a bot (ATLAS's own cards included),
    is never itself a thing waiting on a reply.

    Raises SlackUnreadable when any configured channel could not be read;
    unconfigured Slack is an empty list, not an error.
    """
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    channels = _channels()
    approvers = _approvers()
    if not token or not channels or not approvers:
        return []
    headers = {"Authorization": f"Bearer {token}"}
    oldest = f"{(datetime.now(UTC) - timedelta(hours=hours)).timestamp():.6f}"
    out: list[dict] = []
    for channel in channels:
        data, reason = await get_json(
            f"{API}/conversations.history", headers=headers,
            params={"channel": channel, "oldest": oldest, "limit": 200})
        if reason or not isinstance(data, dict) or not data.get("ok"):
            why = reason or (data.get("error") if isinstance(data, dict) else None)
            raise SlackUnreadable(f"history of {channel}: {why or 'unreadable'}")
        for msg in data.get("messages", []) or []:
            if msg.get("bot_id"):
                continue
            user = str(msg.get("user", ""))
            if user in approvers:
                continue
            text = str(msg.get("text", ""))
            if not any(f"<@{a}>" in text for a in approvers):
                continue
            ts = msg.get("ts", "")
            thread_ts = msg.get("thread_ts", ts)
            if await _replied(channel, thread_ts, ts, approvers, headers):
                continue
            try:
                at = datetime.fromtimestamp(float(ts), UTC)
            except (TypeError, ValueError):
                at = datetime.now(UTC)
            out.append({"chat_id": channel, "sender": user, "text": text,
                       "at": at, "thread_ts": thread_ts})
    return out
