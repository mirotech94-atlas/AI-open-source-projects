"""The life library: books, articles and videos, logged into memory.

`vault.log_reading` is the tool-facing write: it stores the entry in
`library_entries` and remembers it as semantic memory in the `"library"`
namespace, so "what have I read about X" is answered by `recall`, not by a
new lookup endpoint. Rollback removes both rows — the library entry and the
memory it produced — so an approval that gets reversed leaves nothing behind.

Three more writers live here, none of them tools or actions — they are
called from elsewhere in the kernel so the library reflects what ATLAS
already does, not only what Miro explicitly logs:

* `keep_answer` — every knowledge-mode answer (`kernel/knowledge.py`),
  kind='answer'. Matched on kind+title within a day, so asking the same
  question again the same day updates the row instead of piling up a near-
  duplicate. Never raises: a failure to record an answer must never cost
  Miro the answer itself.
* `seed_skill` — one row per `atlas/skills/*.py` playbook, kind='skill',
  written idempotently at boot (`atlas/skills/__init__.py`). Matched on
  kind+source, so the same playbook re-seeded every boot updates one row
  rather than growing one per restart.
* `keep_research` — a finished research report (`modules/research/report.py`),
  kind='research', alongside its own `research_reports` row so the library
  counts it too. Never raises, for the same reason `keep_answer` doesn't:
  filing the report must not depend on this succeeding.
"""
from __future__ import annotations

import logging

from ...kernel.contracts import ActionResult
from ...kernel.memory import Kind, remember
from ...kernel.state import execute_sql, fetch_one

log = logging.getLogger("atlas.vault")

VALID_KINDS = ("book", "article", "video", "note")


def _describe(kind: str, title: str, source: str | None) -> str:
    kind = kind.strip().lower() or "note"
    return f"{kind} '{title}'" + (f" from {source}" if source else "")


async def log_reading(kind: str, title: str, notes: str,
                      source: str | None = None) -> ActionResult:
    if not title.strip():
        return ActionResult(False, "a title is required")
    if not notes.strip():
        return ActionResult(False, "notes are required — what was learned")

    kind = kind.strip().lower() or "note"
    row = await fetch_one(
        """INSERT INTO library_entries (kind, title, source, notes)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        kind, title, source, notes)
    entry_id = row["id"]

    content = f"{title} ({kind}): {notes}" + (f" — {source}" if source else "")
    memory_id = await remember(
        Kind.SEMANTIC, "library", content,
        meta={"library_entry_id": entry_id, "kind": kind, "title": title,
              "source": source})

    await execute_sql(
        "UPDATE library_entries SET memory_id = $2 WHERE id = $1",
        entry_id, memory_id)

    return ActionResult(
        True, f"logged {_describe(kind, title, source)}",
        evidence={"id": entry_id, "memory_id": memory_id},
        rollback_token=f"{entry_id}|{memory_id}")


async def log_dry(kind: str, title: str, notes: str,
                  source: str | None = None) -> ActionResult:
    if not title.strip():
        return ActionResult(False, "a title is required")
    if not notes.strip():
        return ActionResult(False, "notes are required — what was learned")
    return ActionResult(True, f"would log {_describe(kind, title, source)}")


async def unlog(token: str) -> ActionResult:
    """Rollback: removes the library row and the memory row it produced."""
    entry_id_s, _, memory_id_s = (token or "").partition("|")
    if not entry_id_s:
        return ActionResult(False, "no entry to remove")
    await execute_sql("DELETE FROM library_entries WHERE id = $1", int(entry_id_s))
    if memory_id_s:
        await execute_sql("DELETE FROM memories WHERE id = $1", int(memory_id_s))
    return ActionResult(True, "removed the library entry and its memory")


async def keep_answer(question: str, answer: str, source: str) -> None:
    """Every knowledge-mode answer, kept as a library_entries row
    (kind='answer'). Matched on kind+title so the same question asked again
    within a day updates the row rather than duplicating it. Best-effort:
    a failure here is logged and swallowed, never raised — the answer he is
    waiting for must not be lost because writing it down failed.
    """
    title = question.strip()[:200]
    if not title:
        return
    try:
        existing = await fetch_one(
            """SELECT id FROM library_entries
               WHERE kind = 'answer' AND title = $1
                 AND at >= now() - interval '1 day'
               ORDER BY at DESC LIMIT 1""",
            title)
        if existing:
            await execute_sql(
                """UPDATE library_entries SET source = $2, notes = $3, at = now()
                   WHERE id = $1""",
                existing["id"], source, answer)
        else:
            await execute_sql(
                """INSERT INTO library_entries (kind, title, source, notes)
                   VALUES ('answer', $1, $2, $3)""",
                title, source, answer)
    except Exception:
        log.exception("could not keep the answer to %r in the library", title)


async def seed_skill(title: str, source: str, notes: str) -> bool:
    """One library_entries row per skill playbook (kind='skill'), matched on
    kind+source so re-seeding at every boot updates the row in place instead
    of growing one per restart. Returns whether anything changed, so a
    caller can log how many playbooks actually moved. Raises on a genuine
    write failure — the caller (the boot-time seed loop) decides per file
    whether that is worth logging and moving on.
    """
    existing = await fetch_one(
        """SELECT id, title, notes FROM library_entries
           WHERE kind = 'skill' AND source = $1""",
        source)
    if existing:
        if existing["title"] == title and existing["notes"] == notes:
            return False
        await execute_sql(
            "UPDATE library_entries SET title = $2, notes = $3, at = now() "
            "WHERE id = $1",
            existing["id"], title, notes)
        return True
    await execute_sql(
        """INSERT INTO library_entries (kind, title, source, notes)
           VALUES ('skill', $1, $2, $3)""",
        title, source, notes)
    return True


async def keep_research(question: str, source: str, notes: str) -> None:
    """A finished research report, also kept in the library (kind='research')
    so the knowledge base counts it without a second bookkeeping table --
    `research_reports` stays the durable record research.py itself reads
    back. Best-effort like `keep_answer`: filing the report must never
    depend on this succeeding.
    """
    title = question.strip()[:200]
    try:
        await execute_sql(
            """INSERT INTO library_entries (kind, title, source, notes)
               VALUES ('research', $1, $2, $3)""",
            title, source, notes[:2000])
    except Exception:
        log.exception("could not keep the research report %r in the library", title)
