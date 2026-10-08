"""A pipeline whose jobs never ran (no runner) is not a code failure; a job
that ran and failed still is."""
from __future__ import annotations

from datetime import UTC, datetime

from atlas.kernel.contracts import Health
from atlas.modules import gitlab as g

INST = g.Instance("business", "https://git.example", "GITLAB_TOKEN_TEST")
NOW = datetime.now(UTC).isoformat()


def _fake(jobs_by_pipeline, latest=None):
    async def api(inst, path, **params):
        if path == "projects":
            return [{"id": 7, "path_with_namespace": "me/app"}]
        if path == "projects/7/pipelines" and "ref" in params:
            return [latest] if latest else []
        if path == "projects/7/pipelines":
            return [{"id": pid, "ref": "main", "updated_at": NOW}
                    for pid in jobs_by_pipeline]
        pid = int(path.split("/")[3])
        return jobs_by_pipeline[pid]
    return api


async def test_no_runner_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(g, "_api", _fake({
        61: [{"status": "failed",
              "failure_reason": "stuck_pending_no_matching_runners"}]}))
    obs = await g.failed_pipelines(INST)
    assert obs.health is Health.OK and obs.value == 0
    assert "never ran because no runner" in obs.detail
    assert obs.evidence["never_ran"] == ["me/app#61"]


async def test_a_job_that_ran_and_failed_still_degrades(monkeypatch):
    monkeypatch.setattr(g, "_api", _fake({
        61: [{"status": "failed",
              "failure_reason": "stuck_pending_no_matching_runners"}],
        62: [{"status": "failed", "failure_reason": "script_failure"}]}))
    obs = await g.failed_pipelines(INST)
    assert obs.health is Health.DEGRADED and obs.value == 1
    assert obs.evidence["pipelines"] == ["me/app#62"]


async def test_a_failure_a_later_green_run_fixed_is_not_counted(monkeypatch):
    """2026-09-28: root/atlas#64 and #65 failed while CI was being set up, and
    #66 passed on the same branch; the fixed failures kept `business` degraded."""
    monkeypatch.setattr(g, "_api", _fake(
        {64: [{"status": "failed", "failure_reason": "script_failure"}]},
        latest={"id": 66, "status": "success"}))
    obs = await g.failed_pipelines(INST)
    assert obs.health is Health.OK and obs.value == 0


async def test_a_failure_still_latest_on_its_branch_counts(monkeypatch):
    monkeypatch.setattr(g, "_api", _fake(
        {64: [{"status": "failed", "failure_reason": "script_failure"}]},
        latest={"id": 64, "status": "failed"}))
    obs = await g.failed_pipelines(INST)
    assert obs.health is Health.DEGRADED and obs.value == 1
