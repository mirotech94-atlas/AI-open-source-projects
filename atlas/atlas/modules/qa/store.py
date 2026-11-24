"""`qa_results`: what QA checked, at which content hash, and what it found.

A row is evidence, never a body: statuses, byte lengths, hashes and the
failure lines. A rollback supersedes a row (it stops counting as a pass);
nothing here DELETEs.
"""
from __future__ import annotations

import json
from typing import Any

from ...kernel.state import fetch_all, fetch_one

KINDS = ("draft", "site", "security", "runs", "video")


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def row_of(row: Any) -> dict[str, Any]:
    out = dict(row)
    for key in ("failures", "evidence"):
        if key in out:
            out[key] = _json(out[key])
    return out


async def record(kind: str, subject_ref: str, subject_sha: str, ok: bool,
                 failures: list[str], evidence: dict[str, Any]) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    row = await fetch_one(
        """INSERT INTO qa_results (kind, subject_ref, subject_sha, ok, failures, evidence)
           VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb) RETURNING id""",
        kind, subject_ref[:300], subject_sha, bool(ok),
        json.dumps([str(f)[:500] for f in failures][:50]),
        json.dumps(evidence or {}, default=str))
    if row is None:
        raise RuntimeError("the qa_results row was not written")
    return int(row["id"])


async def latest(subject_ref: str, subject_sha: str) -> dict[str, Any] | None:
    """The newest standing (not superseded) result for this exact content."""
    row = await fetch_one(
        """SELECT id, kind, ok, failures, checked_at FROM qa_results
            WHERE subject_ref = $1 AND subject_sha = $2 AND superseded_at IS NULL
            ORDER BY checked_at DESC, id DESC LIMIT 1""",
        subject_ref, subject_sha)
    return row_of(row) if row is not None else None


async def supersede(result_id: int) -> bool:
    row = await fetch_one(
        """UPDATE qa_results SET superseded_at = now()
            WHERE id = $1 AND superseded_at IS NULL RETURNING id""", int(result_id))
    return row is not None


async def recent(limit: int = 50) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, kind, subject_ref, subject_sha, ok, failures, checked_at,
                  superseded_at FROM qa_results ORDER BY checked_at DESC LIMIT $1""",
        int(limit))
    return [row_of(r) for r in rows]


async def counts_since(hours: int = 24) -> dict[str, int]:
    row = await fetch_one(
        """SELECT count(*) FILTER (WHERE ok) AS passed,
                  count(*) FILTER (WHERE NOT ok) AS failed
             FROM qa_results
            WHERE superseded_at IS NULL
              AND checked_at > now() - make_interval(hours => $1)""", int(hours))
    if row is None:
        return {"passed": 0, "failed": 0}
    return {"passed": int(row["passed"] or 0), "failed": int(row["failed"] or 0)}
