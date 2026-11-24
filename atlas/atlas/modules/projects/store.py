"""`projects`, `project_tasks` and `project_tasks_history`: the local project
board every agency agent writes to through `projects.*` actions.

Every write is ONE statement that also writes its history row (a
data-modifying CTE), so a write without its history cannot exist. A
rollback reads the history row by its token and puts back what was there:
a created project is archived, a created task is cancelled, a status
change is reversed. Nothing is ever DELETEd.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from ...kernel.state import fetch_all, fetch_one

PROJECT_STATUSES = ("planned", "active", "blocked", "done", "archived")
TASK_KINDS = ("task", "remediation", "build", "site_change")
TASK_STATUSES = ("todo", "doing", "blocked", "done")
# Reached only by a rollback of `projects.add_task`: never set by an action.
CANCELLED = "cancelled"
OPEN = ("todo", "doing", "blocked")
MAX_TITLE = 300
MAX_TEXT = 4000

TASK_COLUMNS = ("id, project_id, title, kind, owner, status, due_at, evidence, "
                "suggested_fix, source_action, source_ref, created_at, done_at")


class Invalid(ValueError):
    """A value the board refuses; the text is safe to show."""


def _token() -> str:
    return uuid.uuid4().hex


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def task_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    if "evidence" in out:
        out["evidence"] = _json(out["evidence"]) or {}
    return out


def clean_title(title: str) -> str:
    text = " ".join((title or "").split())
    if not text:
        raise Invalid("a title is required")
    return text[:MAX_TITLE]


def check_kind(kind: str) -> str:
    if kind not in TASK_KINDS:
        raise Invalid(f"kind must be one of {', '.join(TASK_KINDS)}")
    return kind


def check_status(status: str) -> str:
    if status not in TASK_STATUSES:
        raise Invalid(f"status must be one of {', '.join(TASK_STATUSES)}")
    return status


# ------------------------------------------------------------------ writes

async def create_project(client_lead_id: int | None, client_name: str,
                         name: str) -> tuple[int, str]:
    token = _token()
    row = await fetch_one(
        """WITH n AS (
               INSERT INTO projects (client_lead_id, client_name, name, status)
               VALUES ($1, $2, $3, 'planned') RETURNING id),
           h AS (
               INSERT INTO project_tasks_history (token, "row")
               SELECT $4, jsonb_build_object('table', 'projects', 'id', n.id,
                                             'before', NULL)
               FROM n)
           SELECT id FROM n""",
        client_lead_id, (client_name or "")[:200], clean_title(name), token)
    if row is None:
        raise RuntimeError("the project row was not written")
    return int(row["id"]), token


async def add_task(*, project_id: int | None, title: str, kind: str, owner: str,
                   due_at: datetime | None, evidence: dict[str, Any] | None,
                   suggested_fix: str, source_action: str, source_ref: str
                   ) -> tuple[int, str]:
    token = _token()
    row = await fetch_one(
        """WITH n AS (
               INSERT INTO project_tasks (project_id, title, kind, owner, status,
                                          due_at, evidence, suggested_fix,
                                          source_action, source_ref)
               VALUES ($1, $2, $3, $4, 'todo', $5::timestamptz, $6::jsonb, $7, $8,
                       $9) RETURNING id),
           h AS (
               INSERT INTO project_tasks_history (token, "row")
               SELECT $10, jsonb_build_object('table', 'project_tasks',
                                              'id', n.id, 'before', NULL)
               FROM n)
           SELECT id FROM n""",
        project_id, clean_title(title), check_kind(kind), (owner or "")[:60],
        due_at, json.dumps(evidence or {}, default=str),
        (suggested_fix or "")[:MAX_TEXT], (source_action or "")[:120],
        (source_ref or "")[:200], token)
    if row is None:
        raise RuntimeError("the task row was not written")
    return int(row["id"]), token


async def set_task_status(task_id: int, status: str) -> tuple[dict[str, Any], str]:
    """(the task's previous status and done_at, the history token)."""
    token = _token()
    row = await fetch_one(
        """WITH b AS (SELECT id, status, done_at FROM project_tasks WHERE id = $1),
           h AS (
               INSERT INTO project_tasks_history (token, "row")
               SELECT $3, jsonb_build_object('table', 'project_tasks', 'id', b.id,
                                             'before', to_jsonb(b))
               FROM b),
           u AS (
               UPDATE project_tasks
                  SET status = $2,
                      done_at = CASE WHEN $2 = 'done' THEN now() ELSE NULL END
                WHERE id = $1 RETURNING id)
           SELECT b.status AS before_status, b.done_at AS before_done_at
             FROM b JOIN u ON u.id = b.id""",
        int(task_id), check_status(status), token)
    if row is None:
        raise Invalid(f"no task {task_id}")
    return {"status": row["before_status"], "done_at": row["before_done_at"]}, token


async def history(token: str) -> dict[str, Any] | None:
    row = await fetch_one(
        'SELECT "row" FROM project_tasks_history WHERE token = $1', token)
    if row is None:
        return None
    value = _json(row["row"])
    return value if isinstance(value, dict) else None


async def restore(token: str) -> str:
    """Put back what the write with `token` changed. Returns what it did."""
    entry = await history(token)
    if entry is None:
        raise Invalid("no history for that token")
    table, rid, before = entry.get("table"), int(entry.get("id") or 0), entry.get("before")
    if table == "projects" and before is None:
        await fetch_one("UPDATE projects SET status = 'archived', updated_at = now() "
                        "WHERE id = $1 RETURNING id", rid)
        return f"project {rid} archived"
    if table == "project_tasks" and before is None:
        await fetch_one(f"UPDATE project_tasks SET status = '{CANCELLED}' "
                        "WHERE id = $1 RETURNING id", rid)
        return f"task {rid} cancelled"
    if table == "project_tasks" and isinstance(before, dict):
        status = before.get("status")
        if status not in TASK_STATUSES:
            raise Invalid("the history row holds no valid status")
        await fetch_one("UPDATE project_tasks SET status = $2, done_at = $3::timestamptz "
                        "WHERE id = $1 RETURNING id", rid, status,
                        _when(before.get("done_at")))
        return f"task {rid} back to {status}"
    raise Invalid("the history row is not one this board wrote")


def _when(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# ------------------------------------------------------------------- reads

async def project(project_id: int) -> dict[str, Any] | None:
    row = await fetch_one(
        "SELECT id, client_lead_id, client_name, name, status, kickoff_at, created_at, "
        "updated_at FROM projects WHERE id = $1", int(project_id))
    return dict(row) if row is not None else None


async def task(task_id: int) -> dict[str, Any] | None:
    row = await fetch_one(f"SELECT {TASK_COLUMNS} FROM project_tasks WHERE id = $1",
                          int(task_id))
    return task_row(row) if row is not None else None


async def projects() -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT p.id, p.client_lead_id, p.client_name, p.name, p.status,
                  p.kickoff_at, p.created_at, p.updated_at,
                  count(t.id) FILTER (WHERE t.status IN ('todo','doing','blocked'))
                      AS open_tasks
             FROM projects p LEFT JOIN project_tasks t ON t.project_id = p.id
            WHERE p.status <> 'archived'
            GROUP BY p.id ORDER BY p.updated_at DESC LIMIT 200""")
    return [dict(r) for r in rows]


async def tasks(project_id: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        f"SELECT {TASK_COLUMNS} FROM project_tasks WHERE project_id = $1 "
        "ORDER BY created_at DESC LIMIT 500", int(project_id))
    return [task_row(r) for r in rows]


async def remediation(limit: int = 200) -> list[dict[str, Any]]:
    """Remediation tasks, open ones first, newest first within each."""
    rows = await fetch_all(
        f"""SELECT {TASK_COLUMNS} FROM project_tasks WHERE kind = 'remediation'
            ORDER BY (status IN ('todo','doing','blocked')) DESC, created_at DESC
            LIMIT $1""", int(limit))
    return [task_row(r) for r in rows]


async def open_remediation_for(source_action: str, source_ref: str) -> int | None:
    """The open remediation task for (source_action, source_ref), if any:
    the dedupe key while a task is open."""
    row = await fetch_one(
        """SELECT id FROM project_tasks WHERE kind = 'remediation'
              AND source_action = $1 AND source_ref = $2
              AND status IN ('todo','doing','blocked')
            ORDER BY id LIMIT 1""", source_action, source_ref)
    return int(row["id"]) if row is not None else None


async def overdue_count() -> int:
    row = await fetch_one(
        """SELECT count(*) AS n FROM project_tasks
            WHERE due_at < now() AND status IN ('todo','doing','blocked')""")
    return int(row["n"]) if row is not None else 0
