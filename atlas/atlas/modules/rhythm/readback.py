"""Read-back for `rhythm.note_circumstance` (RA-D1). The effect is ATLAS's
own `rhythm_notes` row, whose id is the run's rollback token. It is
SELECTed by that id: kind 'circumstance', the approved `detail` (compared by
hash) and `until`. The rollback deletes the row, so a missing row is
contradicted.

The detail is the owner's own words, which the module keeps out of memory on
purpose: only its hash goes into evidence, never the text. Reads only. See
docs/verification.md.
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from ...kernel import state
from ...kernel.contracts import ActionResult
from ...kernel.verify import Verification, contradicted, unknown, verified, verifier


def _sha(text: Any) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:12]


@verifier("rhythm.note_circumstance")
async def verify_note(params: dict[str, Any], result: ActionResult) -> Verification:
    try:
        note_id = int(result.rollback_token or "")
    except ValueError:
        return unknown("the run recorded no note id")
    row = await state.fetch_one(
        "SELECT kind, detail, until, memory_id FROM rhythm_notes WHERE id = $1",
        note_id)
    if row is None:
        return contradicted(f"rhythm note #{note_id} is gone", note_id=note_id)
    until = row["until"]
    evidence = {"note_id": note_id, "kind": row["kind"],
                "detail_sha256": _sha(row["detail"]),
                "until": until.isoformat() if until else None,
                "memory_id": row["memory_id"]}
    if row["kind"] != "circumstance":
        return contradicted(f"note #{note_id} is a {row['kind']!r} note", **evidence)
    if "detail" in params and _sha(params["detail"]) != evidence["detail_sha256"]:
        return contradicted(f"note #{note_id} holds a different detail than "
                            "approved", **evidence)
    want = str(params.get("until") or "") or None
    if want is not None:
        try:
            want = date.fromisoformat(want).isoformat()
        except ValueError:
            pass
    if want != evidence["until"]:
        return contradicted(f"note #{note_id} runs until {evidence['until']}, not "
                            f"{want}", **evidence)
    return verified(**evidence)
