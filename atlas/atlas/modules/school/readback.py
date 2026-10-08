"""Read-back for `school.draft_work` and `school.set_course` (RA-D1).

  draft_work  the `content_drafts` row by `evidence.id` (kind
              `school_<work_kind>`, source `school_item:<item_id>`, not
              dropped, the recorded body hash) and the files it wrote: each
              must exist, non-empty, inside the draft's directory under
              `SCHOOL_DIR`. The rollback marks the row dropped and moves the
              directory to `.trash`, so either is contradicted.
  set_course  the `school_courses` row by its normalised code: its
              `work_kind` is the approved one. Reconciler: the same SELECT.

Reads only: SELECTs and `stat`, never a mkdir. See docs/verification.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ...kernel import state
from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    combine,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import work


def _sha(text: Any) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:12]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ draft_work

def _token(result: ActionResult) -> dict[str, Any]:
    try:
        data = json.loads(result.rollback_token or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def _row_leg(draft_id: int, params: dict[str, Any],
                   ev: dict[str, Any]) -> Verification:
    row = await state.fetch_one(
        "SELECT kind, source, status, body FROM content_drafts WHERE id = $1",
        draft_id)
    if row is None:
        return contradicted(f"there is no draft #{draft_id}", draft_id=draft_id)
    evidence = {"draft_id": draft_id, "kind": row["kind"], "status": row["status"],
                "body_sha256": _sha(row["body"])}
    item_id = _int(params.get("item_id"))
    if item_id is not None and row["source"] != f"school_item:{item_id}":
        return contradicted(f"draft #{draft_id} is not for item {item_id}",
                            FailureClass.VALIDATION, **evidence)
    kind = ev.get("work_kind")
    if kind and row["kind"] != f"school_{kind}":
        return contradicted(f"draft #{draft_id} is a {row['kind']!r}, not "
                            f"school_{kind}", **evidence)
    if row["status"] == "dropped":
        return contradicted(f"draft #{draft_id} is dropped", **evidence)
    if ev.get("body_sha256") and evidence["body_sha256"] != ev["body_sha256"]:
        return contradicted(f"draft #{draft_id}'s body is not the one written",
                            **evidence)
    return verified(**evidence)


def _files(base: Path, files: list[str]) -> tuple[list[str], list[str], int]:
    """(missing or empty, outside the directory, bytes seen). `stat` only."""
    missing: list[str] = []
    outside: list[str] = []
    size = 0
    if not base.is_dir():
        return list(files), outside, 0
    root = base.resolve()
    for name in files:
        target = (base / name).resolve()
        if not target.is_relative_to(root):
            outside.append(name)
            continue
        try:
            n = target.stat().st_size
        except OSError:
            missing.append(name)
            continue
        if n <= 0:
            missing.append(name)
        size += n
    return missing, outside, size


async def _files_leg(result: ActionResult, ev: dict[str, Any]) -> Verification:
    files = [str(f) for f in (ev.get("files") or [])]
    rel = work.safe_relative(str(_token(result).get("dir") or ""))
    if rel is None:
        return unknown("the run recorded no draft directory")
    if not files:
        return unknown("the run recorded no files")
    base = work.root() / rel
    missing, outside, size = await asyncio.to_thread(_files, base, files)
    evidence = {"dir": rel.as_posix(), "files": len(files), "bytes": size}
    if outside:
        return contradicted(f"{len(outside)} recorded file(s) lie outside the "
                            "draft's directory", FailureClass.SECURITY, **evidence)
    if missing:
        return contradicted(f"{len(missing)} of {len(files)} file(s) missing or "
                            f"empty: {', '.join(missing[:5])}", **evidence)
    return verified(**evidence)


@verifier("school.draft_work")
async def verify_draft_work(params: dict[str, Any],
                            result: ActionResult) -> Verification:
    ev = result.evidence or {}
    draft_id = _int(ev.get("id")) or _int(_token(result).get("draft"))
    if draft_id is None:
        return unknown("the run recorded no draft id")
    return combine({"draft": await _row_leg(draft_id, params, ev),
                    "files": await _files_leg(result, ev)})


# ------------------------------------------------------------------ set_course

async def _course(code: str, work_kind: str) -> Verification:
    if not code or not work_kind:
        return unknown("no course code and work kind to compare",
                       FailureClass.VALIDATION)
    row = await state.fetch_one(
        "SELECT work_kind FROM school_courses WHERE code = $1", code)
    if row is None:
        return contradicted(f"there is no course {code}", code=code)
    evidence = {"code": code, "work_kind": row["work_kind"]}
    if row["work_kind"] != work_kind:
        return contradicted(f"{code} is {row['work_kind'] or 'unset'}, not "
                            f"{work_kind}", **evidence)
    return verified(**evidence)


@verifier("school.set_course")
async def verify_set_course(params: dict[str, Any],
                            result: ActionResult) -> Verification:
    ev = result.evidence or {}
    code = str(ev.get("code") or work.normalise_code(str(params.get("code") or "")))
    return await _course(code, str(params.get("work_kind") or ev.get("work_kind")
                                   or ""))


@reconciler("school.set_course")
async def reconcile_set_course(params: dict[str, Any],
                               started: datetime) -> Verification:
    """The course has the approved kind, or not. Nothing is set again."""
    return await _course(work.normalise_code(str(params.get("code") or "")),
                         str(params.get("work_kind") or ""))
