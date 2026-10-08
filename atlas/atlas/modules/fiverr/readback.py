"""Read-back for `fiverr.draft_buyer_reply` (RA-D1). The action only stores
a draft in ATLAS's own `messages` table (nothing is typed into Fiverr), so
the authoritative record is that row: read by the id the run recorded, with
one SELECT, and compared with the approved conversation and text (sha256
prefix; the text itself never reaches the audit chain). No browser, no
network. No reconciler: the params carry no row id. See
docs/verification.md.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    unknown,
    verified,
    verifier,
)


def _sha12(text: Any) -> str:
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:12]


@verifier("fiverr.draft_buyer_reply")
async def verify_draft_buyer_reply(params: dict[str, Any], result: ActionResult
                                   ) -> Verification:
    from ...kernel import state
    ev = result.evidence or {}
    try:
        draft_id = int(ev.get("draft_id"))
    except (TypeError, ValueError):
        return unknown("the run recorded no draft id")
    row = await state.fetch_one(
        "SELECT id, source, external_id, draft, status FROM messages WHERE id = $1",
        draft_id)
    if row is None:
        return contradicted(f"draft #{draft_id} is not in messages", draft_id=draft_id)
    evidence = {"draft_id": draft_id, "status": row["status"],
                "draft_sha256": _sha12(row["draft"])}
    if row["source"] != "fiverr":
        return contradicted(f"row #{draft_id} is not a Fiverr draft",
                            FailureClass.VALIDATION, **evidence)
    if str(row["external_id"] or "") != str(params.get("conversation") or ""):
        return contradicted("the draft is stored for a different conversation",
                            **evidence)
    if evidence["draft_sha256"] != _sha12(params.get("body")):
        return contradicted("the stored draft differs from the approved text",
                            **evidence)
    return verified(**evidence)
