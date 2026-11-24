"""projects -- the local project and task board.

One board for every client project and every task the agency agents raise:
ordinary tasks, `remediation` (what QA found and someone must fix), `build`
(an approved idea a human builds; ATLAS never edits its own code) and
`site_change`. It writes only ATLAS's own tables, so its three actions are
Tier 1: each has a dry run (the row it would write) and a rollback (from
`project_tasks_history`, never a DELETE).

Watches: `overdue` -- open tasks past their due time. OK at none, DEGRADED
with the count, UNKNOWN when the board cannot be read (never OK).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    FailureClass,
    Health,
    Observation,
    Severity,
    Tier,
)
from .._http import explain
from . import store


def _due(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        when = datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise store.Invalid("due_at must be an ISO date or time") from None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _int(value: Any, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise store.Invalid(f"{name} must be a number") from None


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


# ------------------------------------------------------------- create_project

def _project_row(client_lead_id: Any, name: str, client_name: str) -> dict[str, Any]:
    return {"client_lead_id": _int(client_lead_id, "client_lead_id"),
            "client_name": (client_name or "")[:200],
            "name": store.clean_title(name), "status": "planned"}


async def create_project_dry(name: str, client_lead_id: Any = None,
                             client_name: str = "") -> ActionResult:
    try:
        row = _project_row(client_lead_id, name, client_name)
    except store.Invalid as exc:
        return _refused(str(exc))
    return ActionResult(True, f"would create project {row['name']!r} (planned)",
                        evidence={"would_write": row})


async def create_project(name: str, client_lead_id: Any = None,
                         client_name: str = "") -> ActionResult:
    try:
        row = _project_row(client_lead_id, name, client_name)
    except store.Invalid as exc:
        return _refused(str(exc))
    pid, token = await store.create_project(row["client_lead_id"], row["client_name"],
                                            row["name"])
    return ActionResult(True, f"project #{pid} {row['name']!r} created",
                        evidence={"project_id": pid, **row}, rollback_token=token)


# ------------------------------------------------------------------ add_task

def _task_row(title: str, kind: str, owner: str, project_id: Any, due_at: Any,
              evidence: Any, suggested_fix: str, source_action: str,
              source_ref: str) -> dict[str, Any]:
    if evidence is not None and not isinstance(evidence, dict):
        raise store.Invalid("evidence must be an object")
    return {"project_id": _int(project_id, "project_id"), "title": store.clean_title(title),
            "kind": store.check_kind(kind), "owner": (owner or "")[:60],
            "due_at": _due(due_at), "evidence": evidence or {},
            "suggested_fix": (suggested_fix or "")[:store.MAX_TEXT],
            "source_action": (source_action or "")[:120],
            "source_ref": (source_ref or "")[:200]}


async def add_task_dry(title: str, kind: str, owner: str, project_id: Any = None,
                       due_at: Any = None, evidence: Any = None,
                       suggested_fix: str = "", source_action: str = "",
                       source_ref: str = "") -> ActionResult:
    try:
        row = _task_row(title, kind, owner, project_id, due_at, evidence,
                        suggested_fix, source_action, source_ref)
    except store.Invalid as exc:
        return _refused(str(exc))
    return ActionResult(True, f"would add {row['kind']} task {row['title']!r} "
                              f"for {row['owner'] or 'nobody'}",
                        evidence={"would_write": {**row, "due_at": str(row["due_at"])
                                                  if row["due_at"] else None}})


async def add_task(title: str, kind: str, owner: str, project_id: Any = None,
                   due_at: Any = None, evidence: Any = None, suggested_fix: str = "",
                   source_action: str = "", source_ref: str = "") -> ActionResult:
    try:
        row = _task_row(title, kind, owner, project_id, due_at, evidence,
                        suggested_fix, source_action, source_ref)
    except store.Invalid as exc:
        return _refused(str(exc))
    tid, token = await store.add_task(**row)
    return ActionResult(True, f"task #{tid} {row['title']!r} added ({row['kind']})",
                        evidence={"task_id": tid, "kind": row["kind"],
                                  "owner": row["owner"]},
                        rollback_token=token)


# ----------------------------------------------------------- set_task_status

async def set_task_status_dry(task_id: Any, status: str) -> ActionResult:
    try:
        tid = int(task_id)
        store.check_status(status)
    except (TypeError, ValueError) as exc:
        return _refused(str(exc) if isinstance(exc, store.Invalid)
                        else "task_id must be a number")
    current = await store.task(tid)
    if current is None:
        return _refused(f"no task {tid}")
    return ActionResult(True, f"would move task #{tid} from {current['status']} to "
                              f"{status}",
                        evidence={"would_write": {"id": tid, "status": status},
                                  "before": current["status"]})


async def set_task_status(task_id: Any, status: str) -> ActionResult:
    try:
        tid = int(task_id)
        before, token = await store.set_task_status(tid, status)
    except (TypeError, ValueError) as exc:
        return _refused(str(exc) if isinstance(exc, store.Invalid)
                        else "task_id must be a number")
    return ActionResult(True, f"task #{tid}: {before['status']} -> {status}",
                        evidence={"task_id": tid, "before": before["status"],
                                  "after": status},
                        rollback_token=token)


async def rollback(token: str) -> ActionResult:
    try:
        what = await store.restore(token)
    except store.Invalid as exc:
        return ActionResult(False, str(exc), failure_class=FailureClass.VALIDATION)
    return ActionResult(True, what)


# -------------------------------------------------------------------- check

async def check_overdue() -> Observation:
    now = datetime.now(UTC)
    try:
        n = await store.overdue_count()
    except Exception as exc:                            # noqa: BLE001
        return Observation("overdue", Health.UNKNOWN, at=now,
                           detail=f"project board unreadable: {explain(exc)}")
    return Observation("overdue", Health.DEGRADED if n else Health.OK,
                       value=float(n), unit="tasks",
                       detail=f"{n} open task(s) past due (read {now:%H:%M} UTC)",
                       evidence={"overdue": n, "read_at": now.isoformat()}, at=now)


TASK_PARAMS = {
    "title": "what to do, one line",
    "kind": "task | remediation | build | site_change",
    "owner": "the agent or person who owns it",
    "project_id": "the project it belongs to (optional)",
    "due_at": "ISO date or time it is due (optional)",
    "evidence": "object: what was found, where (optional)",
    "suggested_fix": "how to fix it (optional)",
    "source_action": "the action or check that raised it (optional)",
    "source_ref": "what it is about: a slug, a proposal id (optional)",
}


class ProjectsModule:
    name = "projects"
    title = "Projects"
    requires_env = ()
    optional_env = ()
    setup_note = ""

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple[Check, ...]:
        return (Check("overdue", check_overdue, timedelta(minutes=30), Severity.INFO,
                      "open project tasks past their due time", baseline_sigma=None),)

    def actions(self) -> tuple[Action, ...]:
        return (
            Action("projects.create_project", Tier.AUTO,
                   "Create a project on the local board",
                   run=create_project, dry_run=create_project_dry, rollback=rollback,
                   params={"name": "project name",
                           "client_lead_id": "agency_leads id of the client (optional)",
                           "client_name": "client's name (optional)"},
                   max_per_hour=30),
            Action("projects.add_task", Tier.AUTO,
                   "Add a task to the local project board",
                   run=add_task, dry_run=add_task_dry, rollback=rollback,
                   params=dict(TASK_PARAMS), max_per_hour=60),
            Action("projects.set_task_status", Tier.AUTO,
                   "Move a task on the local project board",
                   run=set_task_status, dry_run=set_task_status_dry, rollback=rollback,
                   params={"task_id": "the task's id",
                           "status": "todo | doing | blocked | done"},
                   max_per_hour=60),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = ProjectsModule()
