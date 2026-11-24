"""Short memory for the owner's WhatsApp chat: the last few exchanges, so a
follow-up ("and Bea?") makes sense.

Stored in Postgres (`whatsapp_chat_memory`, additive; Postgres is this app's
durable store, there is no Redis client). Only the plain text of the owner's message
and of ATLAS's general-chat reply, trimmed. A reply built from a live read (the
agent report, a status line, an answer over the fresh read) is marked
`LiveReply` and stored as `LIVE_PLACEHOLDER` only: no figure and no echoed source
text is ever replayed (invariants 4 and 6, enforced here, not by the prompt). Rows expire after 24 hours (the WhatsApp window), the table keeps
at most `KEEP` rows, and a failure to read or write never blocks an answer.

Memory is separate per channel (`channel`: whatsapp, slack, dashboard; the owner,
2026-11-19): the keep limit and the trim work per channel, so one door can never
evict another's turns, and nothing is replayed across channels. A row written by
a PRIVATE assistant run carries `private`; a replayed window with any such row
makes the next run PRIVATE too (`recent_with_taint`), so a follow-up with no
private cue never carries private text to a cloud model.

the owner's old messages are replayed to the model through `wrap_untrusted` every
time (invariant 4); ATLAS's own general-chat replies go back as assistant turns.
"""
from __future__ import annotations

import logging

from .llm import Message
from .permissions import wrap_untrusted
from .state import execute_sql, fetch_all

log = logging.getLogger("atlas.chat_memory")

EXCHANGES = 6
KEEP = EXCHANGES * 2
MAX_CHARS = 500
TTL_HOURS = 24
DEFAULT_CHANNEL = "whatsapp"
# Room for a conversation key such as `slack:<channel id>` (Slack ids are 9-11
# characters): memory is per conversation, not per platform, so a private row
# written in the owner's DM is never replayed into another Slack channel.
CHANNEL_MAX = 64
LIVE_PLACEHOLDER = "(I gave him a status read here; its figures are not kept.)"


_FIGURE = __import__("re").compile(r"\d(?:[\d.,:/-]*\d)?%?")


def mask_figures(text: str) -> str:
    """The prose of a reply with every figure masked: what the owner said to keep
    ("my test word is pelican") survives, a reading never does (invariant 6)."""
    return _FIGURE.sub("[figure]", _trim(text))


class LiveReply(str):
    """A reply that carries live figures or source text: never stored verbatim."""


def _trim(text: str) -> str:
    return " ".join((text or "").split())[:MAX_CHARS]


def _channel(channel: str) -> str:
    return " ".join(str(channel or DEFAULT_CHANNEL).lower().split())[:CHANNEL_MAX] or DEFAULT_CHANNEL


async def remember(user_text: str, reply: str, *, channel: str = DEFAULT_CHANNEL,
                   private: bool = False) -> None:
    """Add one exchange to `channel`, drop what expired or is beyond that
    channel's last `KEEP` rows. `private` marks rows written from a PRIVATE run."""
    said = LIVE_PLACEHOLDER if isinstance(reply, LiveReply) else _trim(reply)
    channel = _channel(channel)
    try:
        for role, body in (("user", _trim(user_text)), ("assistant", said)):
            if body:
                await execute_sql(
                    "INSERT INTO whatsapp_chat_memory (role, body, channel, private) "
                    "VALUES ($1, $2, $3, $4)", role, body, channel, bool(private))
        await execute_sql(
            "DELETE FROM whatsapp_chat_memory WHERE channel = $3 AND "
            "(created_at < now() - make_interval(hours => $1) OR id NOT IN "
            "(SELECT id FROM whatsapp_chat_memory WHERE channel = $3 "
            "ORDER BY id DESC LIMIT $2))", TTL_HOURS, KEEP, channel)
    except Exception as exc:                                # noqa: BLE001
        log.warning("chat memory not saved: %s", type(exc).__name__)


class Remembered(str):
    """A reply `assistant.converse` has already stored in chat memory (under its
    own channel, with its private flag). A door that sees one must not store it
    again. `live` is True when the answer used a tool: nothing is then learned
    from it into long-term memory; `private` (a PRIVATE run) likewise."""
    live: bool = False
    private: bool = False


async def last_user_turn(channel: str = DEFAULT_CHANNEL) -> tuple[str, bool]:
    """the owner's own most recent message in this channel, plain and trimmed, and
    whether it was written from a PRIVATE run; ('', False) when there is none.
    For the explicit "do it" hand-off: his words, never ATLAS's."""
    try:
        rows = await fetch_all(
            "SELECT role, body, private FROM whatsapp_chat_memory WHERE channel = $1 "
            "AND created_at > now() - make_interval(hours => $2) "
            "ORDER BY id DESC LIMIT $3", _channel(channel), TTL_HOURS, KEEP)
    except Exception as exc:                                # noqa: BLE001
        log.warning("chat memory unreadable: %s", type(exc).__name__)
        return "", False
    for row in rows or []:
        if row["role"] == "user":
            return _trim(str(row["body"])), bool(row.get("private"))
    return "", False


async def last_user_text(channel: str = DEFAULT_CHANNEL) -> str:
    """`last_user_turn`'s text alone."""
    return (await last_user_turn(channel))[0]


async def recent(*, channel: str = DEFAULT_CHANNEL) -> list[Message]:
    """The last exchanges, oldest first, as model messages: the owner's wrapped as
    untrusted data (source = the channel), ATLAS's as they were said. Empty
    when nothing is readable. Rows written from a PRIVATE run are left out: this
    plain form can never carry private text to a cloud model (fails closed). A
    caller that needs them uses `recent_with_taint`."""
    return (await recent_with_taint(channel=channel, include_private=False))[0]


async def recent_with_taint(*, channel: str = DEFAULT_CHANNEL,
                            include_private: bool = True
                            ) -> tuple[list[Message], bool]:
    """`recent`, and whether any row in the window came from a PRIVATE run.
    An unreadable table reports no history and no taint (nothing is replayed,
    so nothing private can be carried)."""
    channel = _channel(channel)
    try:
        rows = await fetch_all(
            "SELECT role, body, private FROM whatsapp_chat_memory WHERE channel = $1 "
            "AND created_at > now() - make_interval(hours => $2) "
            "ORDER BY id DESC LIMIT $3", channel, TTL_HOURS, KEEP)
    except Exception as exc:                                # noqa: BLE001
        log.warning("chat memory unreadable: %s", type(exc).__name__)
        return [], False
    out: list[Message] = []
    tainted = False
    for row in reversed(list(rows or [])):
        body = _trim(str(row["body"]))
        if not body:
            continue
        # The stored stand-in for a reply with figures is a note to the store,
        # not something the assistant said: never replay it, or the model
        # imitates it and answers with nothing else. The user's turn stays.
        if row["role"] != "user" and body == LIVE_PLACEHOLDER:
            continue
        is_private = bool(row.get("private") if hasattr(row, "get")
                          else row["private"])
        if is_private and not include_private:
            continue
        tainted = tainted or is_private
        if row["role"] == "user":
            out.append(Message("user", wrap_untrusted(body, channel)))
        else:
            out.append(Message("assistant", body))
    # A model needs the turns to alternate and to start with the user.
    while out and out[0].role != "user":
        out.pop(0)
    merged: list[Message] = []
    for m in out:
        if merged and merged[-1].role == m.role:
            merged[-1] = Message(m.role, merged[-1].content + "\n" + m.content)
        else:
            merged.append(m)
    return merged, tainted
