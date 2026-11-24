"""Tracked requests: everything the owner asks ATLAS to do, in one table he can
check on.

the owner, 2026-10-06 (Q&A on "automate everything"): hand ATLAS an urgent job
from Slack or the phone, then "just check on him". Both surfaces already
route a request -- to an action (kernel/intent), a mission
(kernel/missions), a development handoff (kernel/improve), a coding card
(kernel/solve) or a card he taps on the call -- and reply once. What was
missing was the record: a week later nothing said what he asked for,
where it went, or how it ended. This is that record, and nothing more.

  * `open()` when a request arrives (Slack `_request`, the phone line's
    `spoken_turn` for an order); `close()` when the surface has its
    answer, with the route it took, whether it worked, the one-line
    outcome and the cards it raised. Both never raise: a request is
    answered whether or not it could be recorded.
  * `context_text()` is the record as a prompt section, so "what did you
    do with my request" and "what are you working on" are answered from
    it (slack `_converse`, the phone's fast prompt). Free text from the
    table -- his words, the outcome -- goes through `wrap_untrusted`.
  * `status_text()` answers a status question with no model at all.

Read-only beyond its own table. Nothing here runs an action.
"""
from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.tasks")

TEXT_MAX = 600
OUTCOME_MAX = 600
RECENT = 8
OPEN, DONE, HELD, FAILED = "open", "done", "held", "failed"
# A PRIVATE run (mail, calendar, notes, cameras) is never written here: the
# rows below are read back into prompts that can reach a cloud model and into
# other channels. Its question and reply become this placeholder, flagged.
PRIVATE_OUTCOME = "(private request: its words and answer are not kept)"
# One mutable cell per request, set by open(). A child task copies the context
# but shares the cell, so a mark made deep inside a run still reaches close().
_PRIVATE_SEEN: ContextVar[dict | None] = ContextVar("atlas_task_private", default=None)
_SCRUB = """UPDATE tasks SET route = 'private', outcome = $2, text = '(private request)',
                   cards = '[]', private = true WHERE id = $1"""


async def note_private() -> None:
    """The request tracked in this context is a PRIVATE run: scrub its row now
    (a path that never reaches close() must not keep his words either)."""
    cell = _PRIVATE_SEEN.get()
    if cell is None or cell.get("private"):
        return
    cell["private"] = True
    if cell.get("id"):
        try:
            await execute_sql(_SCRUB, int(cell["id"]), PRIVATE_OUTCOME)
        except Exception as exc:                        # noqa: BLE001
            log.warning("private request not scrubbed: %s", type(exc).__name__)
STATUS_QUESTION = re.compile(
    r"\b(status of|any update|where are (we|you) (with|on)|progress on|"
    r"what did you do (with|about)|what happened (with|to) my|did you (do|finish|get to)|"
    r"what are you working on|what('s| is) (open|pending|in flight)|"
    r"(my|the) (request|task)s?\b)", re.IGNORECASE)


async def open(text: str, *, source: str, who: str = "", thread: str = "") -> int | None:
    """Record a request as it arrives. None when it could not be stored."""
    asked = " ".join((text or "").split())[:TEXT_MAX]
    if not asked:
        return None
    cell: dict = {"id": None, "private": False}
    _PRIVATE_SEEN.set(cell)
    try:
        row = await fetch_one(
            """INSERT INTO tasks (source, who, thread, text) VALUES ($1, $2, $3, $4)
               RETURNING id""", source[:40], (who or "")[:80], (thread or "")[:80], asked)
        cell["id"] = int(row["id"]) if row else None
        return cell["id"]
    except Exception as exc:                            # noqa: BLE001
        log.warning("request not recorded: %s", type(exc).__name__)
        return None


async def close(task_id: int | None, *, route: str, ok: bool | None, outcome: str,
                cards: tuple[int, ...] | list[int] = ()) -> None:
    """How the request ended. `ok` None means it was answered but nothing
    ran (a question, a handoff that waits on him)."""
    if not task_id:
        return
    status = DONE if ok else (HELD if ok is None else FAILED)
    if cards and ok is None:
        status = HELD
    cell = _PRIVATE_SEEN.get()
    if cell and cell.get("private"):          # a private run: keep no words at all
        _PRIVATE_SEEN.set(None)
        try:
            await execute_sql(
                """UPDATE tasks SET route = $2, status = $3, outcome = $4, text = $5,
                          cards = '[]', private = true, done_at = now() WHERE id = $1""",
                int(task_id), "private", status, PRIVATE_OUTCOME, "(private request)")
        except Exception as exc:                        # noqa: BLE001
            log.warning("request #%s not closed: %s", task_id, type(exc).__name__)
        return
    try:
        await execute_sql(
            """UPDATE tasks SET route = $2, status = $3, outcome = $4, cards = $5,
                      done_at = now() WHERE id = $1""",
            int(task_id), route[:40], status,
            " ".join((outcome or "").split())[:OUTCOME_MAX],
            json.dumps([int(c) for c in cards]))
    except Exception as exc:                            # noqa: BLE001
        log.warning("request #%s not closed: %s", task_id, type(exc).__name__)


async def recent(limit: int = RECENT, *, like: str = "") -> list[dict[str, Any]]:
    """The newest requests, or those whose words overlap `like`."""
    words = {w for w in re.findall(r"[a-z0-9]{4,}", (like or "").lower())
             if w not in _STOP}
    try:
        rows = await fetch_all(
            """SELECT id, at, source, text, route, status, outcome, cards, done_at
                 FROM tasks WHERE NOT private ORDER BY id DESC LIMIT $1""", max(limit * 4, 40))
    except Exception as exc:                            # noqa: BLE001
        log.warning("requests unreadable: %s", type(exc).__name__)
        return []
    out = [dict(r) for r in rows]
    if words:
        hit = [r for r in out if words & set(re.findall(r"[a-z0-9]{4,}", str(r["text"]).lower()))]
        if hit:
            out = hit
    return out[:limit]


_STOP = {"what", "with", "about", "that", "this", "have", "your", "status", "request",
         "update", "working", "doing", "atlas", "please", "could", "would"}


def _when(at: datetime, now: datetime) -> str:
    mins = int((now - at).total_seconds() // 60)
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60} h ago"
    return at.strftime("%d %b")


def line(r: dict[str, Any], now: datetime | None = None, *, quote: bool = True) -> str:
    now = now or datetime.now(UTC)
    from .permissions import wrap_untrusted
    text = wrap_untrusted(str(r["text"]), "tracked request") if quote else str(r["text"])
    cards = r.get("cards")
    if isinstance(cards, str):
        cards = json.loads(cards or "[]")
    tail = ""
    if r.get("outcome"):
        said = str(r["outcome"])
        tail = " — " + (wrap_untrusted(said, "request outcome") if quote else said)
    if cards:
        tail += " (card" + ("s " if len(cards) > 1 else " ") + ", ".join(f"#{c}" for c in cards) + ")"
    route = f" via {r['route']}" if r.get("route") else ""
    return (f"#{r['id']} {_when(r['at'], now)} from {r['source']}: {r['status']}{route}: "
            f"{text}{tail}")


async def context_text(limit: int = RECENT) -> str:
    """The record as a prompt section (every line quoted as untrusted)."""
    rows = await recent(limit)
    if not rows:
        return "TRACKED REQUESTS: none recorded yet."
    now = datetime.now(UTC)
    return ("TRACKED REQUESTS (what the owner asked for, newest first; read at "
            f"{now:%H:%M} UTC):\n" + "\n".join(line(r, now) for r in rows))


def is_status_question(text: str) -> bool:
    return bool(STATUS_QUESTION.search(text or ""))


async def status_text(question: str = "") -> str:
    """A plain answer, no model: the requests his words match, or the newest."""
    rows = await recent(RECENT, like=question)
    if not rows:
        return "No tracked requests yet. Ask me for something and it goes on the list."
    now = datetime.now(UTC)
    open_rows = [r for r in rows if r["status"] in (OPEN, HELD)]
    head = (f"{len(open_rows)} still open or waiting on you, "
            f"{len(rows) - len(open_rows)} settled, of the last {len(rows)}:")
    return head + "\n" + "\n".join("• " + line(r, now, quote=False) for r in rows)
