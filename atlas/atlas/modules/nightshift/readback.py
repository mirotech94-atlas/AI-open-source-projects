"""Read-back for `nightshift.merge` (RA-D1). Reads only: the job's row (its
repo, branch and pinned `head_sha`) and the branch's merge requests on
GitLab, by GET. Merged at the pinned commit is verified; merged at any
other commit is contradicted (class security: something other than what
was approved landed); not merged is contradicted. See docs/verification.md.

Also `nightshift.run` and `nightshift.research`, both read from ATLAS's own
`nightshift_jobs` row by SELECT -- never `check_jobs`, which UPDATEs.

  nightshift.run       the job row by `job_id`: kind 'code', the recorded
                       branch and container, the approved task. queued,
                       running, done and failed are verified -- the job was
                       handed to the sandbox (a failed job is the sandbox's
                       own outcome, as with delegate's handoffs; a start
                       failure never returns ok). stopped is the rollback,
                       contradicted.
  nightshift.research  the job row (kind 'research', done, the question as
                       its task) and the research report it filed, read by
                       `research.readback.read_report`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    classify,
    combine,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from .. import gitlab


async def _job(job_id: int) -> Any:
    from ...kernel.state import fetch_one
    return await fetch_one(
        "SELECT repo, branch, head_sha FROM nightshift_jobs WHERE id = $1", job_id)


def _field(row: Any, name: str) -> Any:
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return None


async def _read(params: dict[str, Any]) -> Verification:
    try:
        job_id = int(params.get("job_id"))
    except (TypeError, ValueError):
        return unknown("the run names no job id", FailureClass.VALIDATION)
    try:
        job = await _job(job_id)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return unknown(f"nightshift_jobs could not be read: {explain(exc)}")
    if job is None:
        return unknown(f"no nightshift job {job_id}", job_id=job_id)
    pin = str(_field(job, "head_sha") or "").strip().lower()
    branch = str(_field(job, "branch") or "")
    if not pin:
        return unknown(f"job {job_id} has no pinned commit to compare with",
                       job_id=job_id)
    inst, project, why = gitlab.project_for(str(_field(job, "repo") or ""))
    if inst is None:
        return unknown(why, job_id=job_id)
    try:
        mrs = await gitlab.merge_requests_for(inst, project, branch)
    except httpx.HTTPError as exc:
        return unknown(f"could not read {project}'s merge requests", classify(exc),
                       job_id=job_id)
    evidence = {"job_id": job_id, "project": project, "branch": branch,
                "pinned_sha": pin}
    merged = [m for m in mrs if m.get("state") == "merged"]
    same = [m for m in merged if str(m.get("sha") or "").lower() == pin]
    if same:
        m = same[0]
        return verified(iid=m.get("iid"), merge_commit_sha=m.get("merge_commit_sha"),
                        web_url=m.get("web_url"), **evidence)
    if merged:
        m = merged[0]
        return contradicted(
            f"!{m.get('iid')} was merged at {str(m.get('sha'))[:12]}, not the "
            f"pinned {pin[:12]}", FailureClass.SECURITY,
            iid=m.get("iid"), merged_sha=m.get("sha"), **evidence)
    return contradicted(f"no merge request for {branch} is merged", **evidence)


@verifier("nightshift.merge")
async def verify_merge(params: dict[str, Any], result: ActionResult) -> Verification:
    job_id = params.get("job_id", (result.evidence or {}).get("job_id"))
    return await _read({"job_id": job_id})


@reconciler("nightshift.merge")
async def reconcile_merge(params: dict[str, Any], started: datetime) -> Verification:
    """A merge with a start and no finish: merged at the pinned commit, or
    not. Nothing is merged again."""
    return await _read(params)


# --------------------------------------------------------------- run / research

HANDED_OVER = frozenset({"queued", "running", "done", "failed"})


def _sha(text: Any) -> str:
    import hashlib
    return hashlib.sha256(str(text or "").encode()).hexdigest()[:12]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _job_row(job_id: int) -> Any:
    from ...kernel import state
    return await state.fetch_one(
        """SELECT status, kind, task, branch, container FROM nightshift_jobs
            WHERE id = $1""", job_id)


@verifier("nightshift.run")
async def verify_run(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    job_id = _int(ev.get("job_id")) or _int(result.rollback_token)
    if job_id is None:
        return unknown("the run recorded no job id")
    row = await _job_row(job_id)
    if row is None:
        return contradicted(f"there is no nightshift job {job_id}", job_id=job_id)
    status = str(_field(row, "status") or "")
    evidence = {"job_id": job_id, "status": status, "kind": _field(row, "kind"),
                "branch": _field(row, "branch"), "container": _field(row, "container"),
                "task_sha256": _sha(_field(row, "task"))}
    if (_field(row, "kind") or "code") != "code":
        return contradicted(f"job {job_id} is a {_field(row, 'kind')!r} job, not "
                            "code", **evidence)
    if "task" in params and _sha(params["task"]) != evidence["task_sha256"]:
        return contradicted(f"job {job_id} carries a different task than approved",
                            **evidence)
    for key in ("branch", "container"):
        if ev.get(key) and str(_field(row, key) or "") != str(ev[key]):
            return contradicted(f"job {job_id}'s {key} is not the one the run "
                                "recorded", **evidence)
    if status == "stopped":
        return contradicted(f"job {job_id} was stopped", **evidence)
    if status not in HANDED_OVER:
        return contradicted(f"job {job_id} is {status!r}", **evidence)
    return verified(**evidence)


@verifier("nightshift.research")
async def verify_research(params: dict[str, Any],
                          result: ActionResult) -> Verification:
    """Two legs: the job row, and the research report it filed. The
    report's `research_reports` id is the rollback token's "report" (the
    evidence's `report_id` is the `reports` row, a different table)."""
    import json

    from ..research.readback import read_report
    ev = result.evidence or {}
    try:
        token = json.loads(result.rollback_token or "{}")
    except ValueError:
        token = {}
    token = token if isinstance(token, dict) else {}
    job_id = _int(ev.get("job_id")) or _int(token.get("job"))
    question = str(params.get("question") or "").strip()
    legs: dict[str, Verification] = {}
    if job_id is None:
        legs["job"] = unknown("the run recorded no job id")
    else:
        row = await _job_row(job_id)
        if row is None:
            legs["job"] = contradicted(f"there is no nightshift job {job_id}",
                                       job_id=job_id)
        else:
            status = str(_field(row, "status") or "")
            evidence = {"job_id": job_id, "status": status,
                        "kind": _field(row, "kind")}
            if _field(row, "kind") != "research":
                legs["job"] = contradicted(f"job {job_id} is not a research job",
                                           **evidence)
            elif question and str(_field(row, "task") or "").strip() != question:
                legs["job"] = contradicted(f"job {job_id} researched a different "
                                           "question", **evidence)
            elif status != "done":
                legs["job"] = contradicted(f"job {job_id} is {status!r}, not done",
                                           **evidence)
            else:
                legs["job"] = verified(**evidence)
    legs["report"] = await read_report(
        _int(ev.get("research_id")) or _int(token.get("report")),
        question=question, report_id=_int(ev.get("report_id")),
        body_sha256=str(ev.get("body_sha256") or "") or None)
    return combine(legs)
