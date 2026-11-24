"""The local project board (atlas/modules/projects): every write carries its
history in the same statement; a rollback restores from it; nothing is
ever deleted."""
from __future__ import annotations

import json

import pytest

from atlas.kernel.contracts import FailureClass, Health, Tier
from atlas.modules import projects
from atlas.modules.projects import store


def _writes(fake):
    return [sql for sql, _ in fake.sql_log
            if any(w in sql for w in ("INSERT", "UPDATE", "DELETE"))]


def test_all_three_actions_are_tier_1_with_dry_run_and_rollback():
    acts = {a.name: a for a in projects.MODULE.actions()}
    assert set(acts) == {"projects.create_project", "projects.add_task",
                         "projects.set_task_status"}
    for a in acts.values():
        assert a.tier is Tier.AUTO and a.dry_run and a.rollback


async def test_create_writes_its_history_row_in_the_same_statement(memory_backend):
    memory_backend.responses["INSERT INTO projects"] = {"id": 7}
    r = await projects.create_project("Joe's Plumbing site", client_lead_id="12",
                                      client_name="Joe's Plumbing")
    assert r.ok and r.evidence["project_id"] == 7 and r.rollback_token
    (sql,) = _writes(memory_backend)
    assert "INSERT INTO projects" in sql and "project_tasks_history" in sql
    assert memory_backend.sql_log[-1][1][-1] == r.rollback_token


async def test_add_task_writes_history_and_refuses_an_unknown_kind(memory_backend):
    memory_backend.responses["INSERT INTO project_tasks"] = {"id": 3}
    r = await projects.add_task("Fix the booking link", "remediation", "qa",
                                evidence={"url": "https://x.com/book"},
                                suggested_fix="point it at /book",
                                source_action="qa.check_site", source_ref="joes")
    assert r.ok and r.evidence["task_id"] == 3
    (sql,) = _writes(memory_backend)
    assert "project_tasks_history" in sql
    bad = await projects.add_task("x", "chore", "qa")
    assert not bad.ok and bad.failure_class is FailureClass.VALIDATION
    assert "kind must be one of" in bad.detail
    assert len(_writes(memory_backend)) == 1


async def test_set_status_writes_history_and_refuses_an_unknown_status(memory_backend):
    memory_backend.responses["UPDATE project_tasks SET status = $2, done_at = CASE"] = {
        "before_status": "todo", "before_done_at": None}
    r = await projects.set_task_status(3, "done")
    assert r.ok and r.evidence == {"task_id": 3, "before": "todo", "after": "done"}
    (sql,) = _writes(memory_backend)
    assert "project_tasks_history" in sql and "to_jsonb(b)" in sql
    bad = await projects.set_task_status(3, "archived")
    assert not bad.ok and "status must be one of" in bad.detail


async def test_rollback_of_a_status_change_restores_the_previous_status(memory_backend):
    memory_backend.responses['SELECT "row" FROM project_tasks_history'] = {
        "row": json.dumps({"table": "project_tasks", "id": 3,
                           "before": {"id": 3, "status": "doing", "done_at": None}})}
    memory_backend.responses["UPDATE project_tasks SET status = $2, done_at = $3"] = {
        "id": 3}
    r = await projects.rollback("tok")
    assert r.ok and "back to doing" in r.detail
    _sql, args = memory_backend.sql_log[-1]
    assert args[:2] == (3, "doing")


async def test_rollback_of_a_create_archives_never_deletes(memory_backend):
    memory_backend.responses['SELECT "row" FROM project_tasks_history'] = {
        "row": {"table": "projects", "id": 7, "before": None}}
    memory_backend.responses["UPDATE projects SET status = 'archived'"] = {"id": 7}
    r = await projects.rollback("tok")
    assert r.ok and "archived" in r.detail
    assert not any("DELETE" in sql for sql, _ in memory_backend.sql_log)


async def test_rollback_of_a_new_task_cancels_it(memory_backend):
    memory_backend.responses['SELECT "row" FROM project_tasks_history'] = {
        "row": {"table": "project_tasks", "id": 4, "before": None}}
    memory_backend.responses["UPDATE project_tasks SET status = 'cancelled'"] = {"id": 4}
    r = await projects.rollback("tok")
    assert r.ok and "cancelled" in r.detail
    assert not any("DELETE" in sql for sql, _ in memory_backend.sql_log)


async def test_rollback_without_history_refuses(memory_backend):
    memory_backend.responses['SELECT "row" FROM project_tasks_history'] = None
    r = await projects.rollback("nope")
    assert not r.ok and "no history" in r.detail


async def test_the_dry_run_shows_the_row_and_writes_nothing(memory_backend):
    r = await projects.add_task_dry("Write the FAQ", "task", "content", due_at="2026-11-01")
    assert r.ok and r.evidence["would_write"]["kind"] == "task"
    assert r.evidence["would_write"]["due_at"].startswith("2026-11-01")
    assert memory_backend.sql_log == []
    bad = await projects.add_task_dry("x", "task", "content", due_at="next week")
    assert not bad.ok and "due_at" in bad.detail


def test_no_delete_anywhere_in_the_store():
    import inspect
    assert "DELETE" not in inspect.getsource(store).upper().replace(
        "NOTHING IS EVER DELETED", "")


async def test_overdue_is_unknown_when_unreadable(monkeypatch):
    async def broken():
        raise ConnectionRefusedError("db down")
    monkeypatch.setattr(store, "overdue_count", broken)
    obs = await projects.check_overdue()
    assert obs.health is Health.UNKNOWN and "unreadable" in obs.detail


@pytest.mark.parametrize(("n", "health"), [(0, Health.OK), (2, Health.DEGRADED)])
async def test_overdue_counts(monkeypatch, n, health):
    async def count():
        return n
    monkeypatch.setattr(store, "overdue_count", count)
    obs = await projects.check_overdue()
    assert obs.health is health and obs.value == n and "read" in obs.detail
