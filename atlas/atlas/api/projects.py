"""The local project board, read-only, behind the sign-in.

* `GET /api/projects` -- projects with their open task counts.
* `GET /api/projects/{id}/tasks` -- one project's tasks.
* `GET /api/remediation` -- remediation tasks, open first, each with its
  owner, evidence, suggested fix and creation time.

Writes go only through the `projects.*` actions (the executor). A read that
fails is a 503 that says why, never an empty board. Every response carries
`served_at`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ..modules._http import explain
from . import auth

router = APIRouter()
SignedIn = Annotated[auth.Session, Depends(auth.require)]


def _unreadable(exc: BaseException) -> HTTPException:
    return HTTPException(503, f"I could not read the project board — {explain(exc)}")


@router.get("/api/projects")
async def projects(_s: SignedIn) -> dict[str, Any]:
    from ..modules.projects import store
    try:
        rows = await store.projects()
    except Exception as exc:  # noqa: BLE001
        raise _unreadable(exc) from None
    return {"served_at": datetime.now(UTC).isoformat(), "projects": rows}


@router.get("/api/projects/{project_id}/tasks")
async def project_tasks(project_id: int, _s: SignedIn) -> dict[str, Any]:
    from ..modules.projects import store
    try:
        head = await store.project(project_id)
        rows = await store.tasks(project_id) if head is not None else []
    except Exception as exc:  # noqa: BLE001
        raise _unreadable(exc) from None
    if head is None:
        raise HTTPException(404, f"no project {project_id}")
    return {"served_at": datetime.now(UTC).isoformat(), "project": head,
            "tasks": rows}


@router.get("/api/remediation")
async def remediation(_s: SignedIn) -> dict[str, Any]:
    from ..modules.projects import store
    try:
        rows = await store.remediation()
    except Exception as exc:  # noqa: BLE001
        raise _unreadable(exc) from None
    keep = ("id", "project_id", "title", "owner", "status", "evidence", "suggested_fix",
            "source_action", "source_ref", "due_at", "created_at", "done_at")
    tasks = [{k: r.get(k) for k in keep} | {"open": r.get("status") in store.OPEN}
             for r in rows]
    return {"served_at": datetime.now(UTC).isoformat(),
            "open": sum(1 for t in tasks if t["open"]), "tasks": tasks}
