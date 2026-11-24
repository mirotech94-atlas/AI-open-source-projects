"""The hard wipe: the only way a remembered note is really deleted.

Typed by hand at a shell (`python -m atlas.cli memory-wipe ...`). Nothing in
chat, the dashboard, a model or the executor reaches it: "forget" in chat only
hides a note, and this is the separate, deliberate step that destroys it.
Only the owner's own notes (namespace `owner` or `biz.*`) can be wiped.
"""
from __future__ import annotations

from .audit import audit
from .state import fetch_all

OWN = "(namespace = 'owner' OR namespace LIKE 'biz.%')"


async def wipe(*, hidden: bool = False, ids: list[int] | None = None,
               everything: bool = False, confirmed: bool = False) -> int:
    """Hard-delete notes. Exactly one of hidden / ids / everything.

    `everything` needs `confirmed=True`. Returns the number of rows removed.
    """
    chosen = sum([bool(hidden), bool(ids), bool(everything)])
    if chosen != 1:
        raise ValueError("choose exactly one of --hidden, --id, --everything")
    if everything and not confirmed:
        raise ValueError("--everything needs --yes")
    if hidden:
        rows = await fetch_all(
            f"DELETE FROM memories WHERE {OWN} AND deleted_at IS NOT NULL "
            "RETURNING id")
        scope = "hidden"
    elif ids:
        rows = await fetch_all(
            f"DELETE FROM memories WHERE {OWN} AND id = ANY($1::bigint[]) "
            "RETURNING id", [int(i) for i in ids])
        scope = "ids"
    else:
        rows = await fetch_all(f"DELETE FROM memories WHERE {OWN} RETURNING id")
        scope = "everything"
    await audit("memory.wipe", "memory_wipe", {"scope": scope, "rows": len(rows)})
    return len(rows)
