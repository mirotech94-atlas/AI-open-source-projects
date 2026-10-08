"""Read-back for `vault.log_reading` and `vault.reindex` (RA-D1). Both
effects are rows ATLAS owns, SELECTed by what the run recorded. Reads only:
no helper that hashes into a cache or writes. See docs/verification.md.

  log_reading  the `library_entries` row by `evidence.id` (title, kind and a
               notes hash as approved, the recorded `memory_id`) and its
               `library` memory. The rollback deletes both, so either
               missing is contradicted.
  reindex      each document the run names in `evidence.docs` is in
               `vault_documents` with the recorded content hash. A newer
               hash is unknown (stale: re-indexed since). A run that indexed
               nothing is verified by reading the index's document count
               and last indexed time back.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ...kernel import state
from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    combine,
    contradicted,
    unknown,
    verified,
    verifier,
)


def _sha(text: Any) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:12]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------- log_reading

async def _memory_leg(memory_id: int | None) -> Verification:
    if memory_id is None:
        return unknown("the run recorded no memory id")
    row = await state.fetch_one(
        "SELECT namespace FROM memories WHERE id = $1", memory_id)
    if row is None:
        return contradicted(f"library memory #{memory_id} is gone",
                            memory_id=memory_id)
    if row["namespace"] != "library":
        return contradicted(f"memory #{memory_id} is in {row['namespace']!r}, not "
                            "the library", memory_id=memory_id,
                            namespace=row["namespace"])
    return verified(memory_id=memory_id, namespace=row["namespace"])


@verifier("vault.log_reading")
async def verify_log_reading(params: dict[str, Any],
                             result: ActionResult) -> Verification:
    ev = result.evidence or {}
    entry_s, _, memory_s = str(result.rollback_token or "").partition("|")
    entry_id = _int(ev.get("id")) or _int(entry_s)
    if entry_id is None:
        return unknown("the run recorded no library entry id")
    memory_id = _int(ev.get("memory_id")) or _int(memory_s)
    row = await state.fetch_one(
        """SELECT kind, title, notes, memory_id FROM library_entries
            WHERE id = $1""", entry_id)
    if row is None:
        return contradicted(f"library entry #{entry_id} is gone", id=entry_id)
    evidence = {"id": entry_id, "kind": row["kind"], "title": row["title"],
                "notes_sha256": _sha(row["notes"]), "memory_id": row["memory_id"]}
    wrong = []
    if "title" in params and str(row["title"]) != str(params["title"]):
        wrong.append("title")
    if "notes" in params and _sha(row["notes"]) != _sha(params["notes"]):
        wrong.append("notes")
    kind = str(params.get("kind") or "").strip().lower()
    if kind and row["kind"] != kind:
        wrong.append("kind")
    if wrong:
        entry = contradicted(f"entry #{entry_id} differs from the approved "
                             f"{', '.join(wrong)}", **evidence)
    elif memory_id is not None and _int(row["memory_id"]) != memory_id:
        entry = contradicted(f"entry #{entry_id} points at memory "
                             f"{row['memory_id']}, not #{memory_id}", **evidence)
    else:
        entry = verified(**evidence)
    return combine({"entry": entry, "memory": await _memory_leg(memory_id)})


# --------------------------------------------------------------------- reindex

@verifier("vault.reindex")
async def verify_reindex(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    indexed = _int(ev.get("indexed")) or 0
    docs = [d for d in (ev.get("docs") or []) if isinstance(d, dict) and d.get("path")]
    problems = len(ev.get("problems") or [])
    if not docs:
        if indexed:
            return unknown(f"the run indexed {indexed} document(s) but recorded "
                           "no paths to look up")
        row = await state.fetch_one(
            "SELECT count(*) AS n, max(indexed_at) AS last FROM vault_documents")
        if row is None:
            return unknown("the vault index could not be counted")
        last = row["last"]
        return verified(indexed=0, documents=int(row["n"] or 0), problems=problems,
                        last_indexed_at=last.isoformat() if last else None)
    paths = [str(d["path"]) for d in docs]
    rows = await state.fetch_all(
        """SELECT path, sha256, chunks, indexed_at FROM vault_documents
            WHERE path = ANY($1)""", paths)
    found = {str(r["path"]): r for r in rows or []}
    missing = [p for p in paths if p not in found]
    newer = [str(d["path"]) for d in docs if str(d["path"]) in found
             and not str(found[str(d["path"])]["sha256"] or "").startswith(
                 str(d.get("sha") or ""))]
    evidence = {"indexed": indexed, "checked": len(paths), "found": len(found),
                "chunks": sum(int(r["chunks"] or 0) for r in found.values()),
                "problems": problems}
    if missing:
        return contradicted(f"{len(missing)} indexed document(s) are not in the "
                            f"index: {', '.join(missing[:5])}", **evidence)
    if newer:
        return unknown(f"{len(newer)} document(s) were re-indexed with newer "
                       "content since", FailureClass.STALE, **evidence)
    return verified(**evidence)
