"""the owner's memory commands in the owner chat: remember, forget, restore, correct,
and "what do you know about ...". Strict regexes, no model.

The words after the verb only pick WHICH notes: the code searches the owner's own
namespaces (`owner`, `biz.<key>`) and hands the ids it found to the executor
(`owner_memory.*`, Tier 1, dry run and rollback). The model never sees the
request and never chooses an id. Forget hides, never deletes; the hard wipe is
`python -m atlas.cli memory-wipe`, typed by hand (kernel/memory_wipe.py).

"What do you know about X" only reads, and says each note is a recollection
with its source and date, never a live figure.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from . import memory
from .executor import ExecutionRequest, execute, watch_only
from .state import fetch_all

log = logging.getLogger("atlas.owner_memory_chat")

APPROVED_BY = "whatsapp:owner"
SOURCE = "whatsapp"
FIND_LIMIT = 5
_LEAD = r"^\W*(?:(?:hey|hi|please|pls|atlas)[\s,:]+)*"
_FORGET = re.compile(_LEAD + r"forget\s+(?:about\s+|that\s+|what i said about\s+)?"
                     r"(?P<t>.{3,}?)\W*$", re.IGNORECASE | re.DOTALL)
_REMEMBER = re.compile(_LEAD + r"remember\s+(?:that\s+)?(?P<t>.{6,})$", re.IGNORECASE | re.DOTALL)
_RESTORE = re.compile(_LEAD + r"(?:restore|unforget|undo (?:the |that )?forget)"
                      r"(?:\s+(?:about\s+)?(?P<t>.+?))?\W*$", re.IGNORECASE | re.DOTALL)
_KNOW = re.compile(_LEAD + r"what (?:do|did) you (?:know|remember) about\s+"
                   r"(?P<t>.+?)\W*$", re.IGNORECASE | re.DOTALL)
_CORRECT = re.compile(_LEAD + r"correct\s+(?P<t>.+?)\s*[:=]\s*(?P<new>.{4,})$",
                      re.IGNORECASE | re.DOTALL)
_NOTHING = frozenset({"it", "that", "this", "them", "about it", "everything"})
OWN = "(namespace = 'owner' OR namespace LIKE 'biz.%')"

OFF = "I'm in watch-only mode, so I can't change my notes yet. Nothing was changed."
NONE_FOUND = "I found no matching note, so nothing was changed."
FOOTER = "These are things you told me, not live readings."


def is_forget(body: str) -> bool:
    m = _FORGET.match(body or "")
    return bool(m and m["t"].strip().lower() not in _NOTHING)


def is_remember(body: str) -> bool:
    return bool(_REMEMBER.match(body or ""))


def is_restore(body: str) -> bool:
    return bool(_RESTORE.match(body or ""))


def is_know(body: str) -> bool:
    return bool(_KNOW.match(body or ""))


def is_correct(body: str) -> bool:
    return bool(_CORRECT.match(body or ""))


def is_memory_command(body: str) -> bool:
    return (is_forget(body) or is_remember(body) or is_restore(body)
            or is_know(body) or is_correct(body))


async def find(topic: str, *, hidden: bool = False) -> list[dict[str, Any]]:
    """Up to FIND_LIMIT notes of the owner's matching the topic's words, found by
    code. `hidden` looks among the hidden ones (for restore)."""
    terms = memory._terms(topic)
    if not terms:
        return []
    state = "deleted_at IS NOT NULL" if hidden else "deleted_at IS NULL"
    rows = await fetch_all(
        f"""SELECT m.id, m.namespace, m.content, m.source, m.at,
                   (SELECT count(*) FROM unnest($1::text[]) AS p(pat)
                     WHERE m.content ILIKE p.pat) AS hits
              FROM memories m
             WHERE {OWN} AND {state} AND m.content ILIKE ANY($1::text[])
             ORDER BY hits DESC, m.at DESC LIMIT $2""",
        [f"%{t}%" for t in terms], FIND_LIMIT)
    return [dict(r) for r in rows or []]


async def _last_hidden() -> list[dict[str, Any]]:
    rows = await fetch_all(
        f"""SELECT id, namespace, content, source, at FROM memories
             WHERE {OWN} AND deleted_at IS NOT NULL
               AND deleted_at >= (SELECT max(deleted_at) FROM memories
                                   WHERE {OWN}) - interval '2 minutes'
             ORDER BY id LIMIT $1""", FIND_LIMIT)
    return [dict(r) for r in rows or []]


def _line(row: dict[str, Any]) -> str:
    when = f"{row['at']:%Y-%m-%d}" if row.get("at") else "no date"
    return f"- ({row.get('source') or 'older'}, {when}) {str(row['content'])[:100]}"


async def _run(action: str, params: dict[str, str], reason: str):
    return await execute(ExecutionRequest(
        action=action, params=params, reason=reason, approved_by=APPROVED_BY))


async def forget(body: str) -> str:
    if watch_only():
        return OFF
    rows = await find(_FORGET.match(body)["t"])
    if not rows:
        return NONE_FOUND
    res = await _run("owner_memory.forget",
                     {"memory_ids": ",".join(str(r["id"]) for r in rows)},
                     "the owner asked ATLAS to forget these notes")
    if not res.ok:
        return f"I couldn't hide those: {res.detail}. Nothing was changed."
    return ("Hidden (not deleted). Say \"restore\" to bring them back:\n"
            + "\n".join(_line(r) for r in rows))


async def restore(body: str) -> str:
    if watch_only():
        return OFF
    topic = (_RESTORE.match(body)["t"] or "").strip()
    rows = await find(topic, hidden=True) if topic else await _last_hidden()
    if not rows:
        return "I found no hidden note to bring back."
    res = await _run("owner_memory.restore",
                     {"memory_ids": ",".join(str(r["id"]) for r in rows)},
                     "the owner asked ATLAS to bring back hidden notes")
    if not res.ok:
        return f"I couldn't bring those back: {res.detail}. Nothing was changed."
    return "Brought back:\n" + "\n".join(_line(r) for r in rows)


async def remember(body: str) -> str:
    if watch_only():
        return OFF
    text = " ".join(_REMEMBER.match(body)["t"].split())
    res = await _run("owner_memory.remember", {"text": text, "source": SOURCE},
                     "the owner asked ATLAS to remember this")
    return "Got it, I'll remember that." if res.ok else (
        f"I couldn't keep that: {res.detail}. Nothing was changed.")


async def correct(body: str) -> str:
    if watch_only():
        return OFF
    m = _CORRECT.match(body)
    rows = await find(m["t"])
    if not rows:
        return NONE_FOUND
    if len(rows) > 1 and len({r["id"] for r in rows}) > 1 and not _clear_best(rows):
        return ("That matches more than one note. Be more specific:\n"
                + "\n".join(_line(r) for r in rows))
    res = await _run("owner_memory.correct",
                     {"memory_ids": str(rows[0]["id"]),
                      "new_text": " ".join(m["new"].split()), "source": SOURCE},
                     "the owner corrected a note")
    return "Corrected. The old note is hidden, not deleted." if res.ok else (
        f"I couldn't correct it: {res.detail}. Nothing was changed.")


def _clear_best(rows: list[dict[str, Any]]) -> bool:
    return int(rows[0].get("hits") or 0) > int(rows[1].get("hits") or 0)


async def know(body: str) -> str:
    rows = await find(_KNOW.match(body)["t"])
    if not rows:
        return "I have no note about that."
    return "What you told me:\n" + "\n".join(_line(r) for r in rows) + "\n" + FOOTER


async def handle(body: str) -> str | None:
    """The reply when `body` is a memory command, else None."""
    for test, fn in ((is_know, know), (is_correct, correct), (is_restore, restore),
                     (is_forget, forget), (is_remember, remember)):
        if test(body):
            return await fn(body)
    return None
