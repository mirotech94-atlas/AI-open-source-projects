"""Read-back for `research.report` (RA-D1). The effect is two rows ATLAS
owns: the `research_reports` row (the rollback token's id) and the
`reports` row it files (kind 'research', `evidence.report_id`). Both are
SELECTed by id. `done` with the approved question, the recorded
`report_id` and the recorded body hash is verified; `withdrawn` (the
rollback) or a missing row is contradicted. Reads only.

`read_report` is shared with `nightshift.research`, which files its report
through `run_report` and carries the same ids. See docs/verification.md.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ...kernel import state
from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
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


async def read_report(research_id: int | None, *, question: str,
                      report_id: int | None, body_sha256: str | None
                      ) -> Verification:
    """The research_reports row by id, and the reports row it names."""
    if research_id is None:
        return unknown("the run recorded no research report id")
    row = await state.fetch_one(
        """SELECT question, status, body, report_id FROM research_reports
            WHERE id = $1""", research_id)
    if row is None:
        return contradicted(f"there is no research report #{research_id}",
                            research_id=research_id)
    got_sha = _sha(row["body"])
    evidence = {"research_id": research_id, "status": row["status"],
                "report_id": row["report_id"], "body_sha256": got_sha}
    if row["status"] == "withdrawn":
        return contradicted(f"research report #{research_id} was withdrawn",
                            **evidence)
    if row["status"] != "done":
        return contradicted(f"research report #{research_id} is {row['status']!r}, "
                            "not done", **evidence)
    if question and str(row["question"]).strip() != question.strip():
        return contradicted(f"research report #{research_id} answers a different "
                            "question", FailureClass.VALIDATION, **evidence)
    if body_sha256 and got_sha != body_sha256:
        return contradicted(f"research report #{research_id}'s body is not the one "
                            "filed", **evidence)
    if report_id is not None and _int(row["report_id"]) != report_id:
        return contradicted(f"research report #{research_id} points at report "
                            f"{row['report_id']}, not #{report_id}", **evidence)
    linked = _int(row["report_id"])
    if linked is None:
        return unknown(f"research report #{research_id} names no filed report",
                       **evidence)
    filed = await state.fetch_one(
        "SELECT kind FROM reports WHERE id = $1", linked)
    if filed is None:
        return contradicted(f"the filed report #{linked} is missing", **evidence)
    if filed["kind"] != "research":
        return contradicted(f"report #{linked} is a {filed['kind']!r}, not research",
                            **evidence)
    return verified(**evidence)


@verifier("research.report")
async def verify_report(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    return await read_report(
        _int(ev.get("research_id")) or _int(result.rollback_token),
        question=str(params.get("question") or ""),
        report_id=_int(ev.get("report_id")),
        body_sha256=str(ev.get("body_sha256") or "") or None)
