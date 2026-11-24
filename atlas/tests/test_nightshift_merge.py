"""nightshift.merge against a mocked GitLab (RA-D1).

What has to hold:

  * the merge happens only at the commit pinned when the job finished: an MR
    whose head moved is refused, and GitLab is handed the pin (`sha=`);
  * a pipeline that did not pass for that commit is refused;
  * success merges once, and a retry after a success never merges again;
  * ATLAS's own repository is never on the night shift's list, whatever
    NIGHTSHIFT_REPOS says;
  * the pin is read from GitLab when a job finishes done, never from logs;
  * the read-back (and its reconciler) checks the merged SHA against the pin.

GitLab is faked at `httpx.AsyncClient.send`. No network, no database.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import httpx
import pytest

from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult
from atlas.modules import gitlab, nightshift
from atlas.modules.nightshift import readback, sandbox

SECRET = "glpat-merge-supersecret-0123456789"
REPO = "https://aicode.storefront.test/root/flycheap-tracker.git"
PROJECT = r"/api/v4/projects/root%2Fflycheap-tracker"
PIN = "a" * 40
OTHER = "b" * 40


class GitLab:
    """One project's merge requests, in memory. Records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.mrs: list[dict] = []
        self.branch_sha = PIN
        self.pipeline = {"id": 9, "status": "success", "sha": PIN}
        self.fail: dict[str, int] = {}

    def mr(self, **over) -> dict:
        m = {"iid": 3, "state": "opened", "sha": PIN,
             "web_url": "https://aicode.storefront.test/root/flycheap-tracker/-/merge_requests/3",
             "merge_commit_sha": None, **over}
        self.mrs.append(m)
        return m

    async def send(self, client, request: httpx.Request, **_kw) -> httpx.Response:
        path = request.url.raw_path.decode().split("?")[0]
        body = json.loads(request.content or b"{}") if request.content else {}
        self.calls.append((request.method, path, body))
        assert request.headers.get("PRIVATE-TOKEN") == SECRET

        def reply(status=200, data=None):
            return httpx.Response(status, json=data, request=request)

        for key, status in self.fail.items():
            if key in f"{request.method} {path}":
                return reply(status, {"message": "nope"})
        if request.method == "GET" and re.fullmatch(PROJECT, path):
            return reply(data={"default_branch": "main"})
        if request.method == "GET" and "/repository/branches/" in path:
            return reply(data={"name": "nightshift/job-1",
                               "commit": {"id": self.branch_sha}})
        if request.method == "GET" and path.endswith("/merge_requests"):
            assert request.url.params.get("source_branch") == "nightshift/job-1"
            return reply(data=[dict(m) for m in self.mrs])
        m = re.search(r"/merge_requests/(\d+)(/merge)?$", path)
        if request.method == "GET" and m and not m.group(2):
            mr = next(x for x in self.mrs if x["iid"] == int(m.group(1)))
            return reply(data={**mr, "head_pipeline": self.pipeline})
        if request.method == "POST" and path.endswith("/merge_requests"):
            assert body["target_branch"] == "main"
            return reply(201, self.mr(iid=4, sha=self.branch_sha))
        if request.method == "PUT" and m and m.group(2):
            mr = next(x for x in self.mrs if x["iid"] == int(m.group(1)))
            if body.get("sha") != mr["sha"]:
                return reply(409, {"message": "SHA does not match HEAD of source branch"})
            mr.update(state="merged", merge_commit_sha="c" * 40)
            return reply(data=dict(mr))
        return reply(404, {"message": "404 Not Found"})

    def puts(self) -> list:
        return [c for c in self.calls if c[0] == "PUT"]


@pytest.fixture
def gl(monkeypatch):
    fake = GitLab()

    async def send(client, request, **kw):
        return await fake.send(client, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    monkeypatch.setenv("GITLAB_TOKEN", SECRET)
    monkeypatch.setenv("NIGHTSHIFT_REPOS", "aicode.storefront.test/root/flycheap-tracker")
    return fake


@pytest.fixture
def job(monkeypatch):
    row = {"repo": REPO, "branch": "nightshift/job-1", "status": "done",
           "kind": "code", "head_sha": PIN}

    async def fetch_one(sql, *args):
        assert args == (1,)
        return dict(row)

    monkeypatch.setattr(nightshift, "fetch_one", fetch_one)
    monkeypatch.setattr(readback, "_job", lambda job_id: fetch_one("", job_id))
    return row


# ------------------------------------------------------------------ merge

async def test_a_moved_head_is_refused(gl, job):
    gl.mr(sha=OTHER)
    result = await nightshift.merge(1)
    assert not result.ok
    assert "not the approved" in result.detail and PIN[:12] in result.detail
    assert gl.puts() == []


async def test_a_failed_pipeline_is_refused(gl, job):
    gl.mr()
    gl.pipeline = {"id": 9, "status": "failed", "sha": PIN}
    result = await nightshift.merge(1)
    assert not result.ok and "failed, not passed" in result.detail
    assert gl.puts() == []


async def test_a_pipeline_for_another_commit_is_refused(gl, job):
    gl.mr()
    gl.pipeline = {"id": 9, "status": "success", "sha": OTHER}
    result = await nightshift.merge(1)
    assert not result.ok and "not passed" in result.detail
    assert gl.puts() == []


async def test_no_pipeline_is_refused(gl, job):
    gl.mr()
    gl.pipeline = None
    result = await nightshift.merge(1)
    assert not result.ok and "none, not passed" in result.detail
    assert gl.puts() == []


async def test_success_merges_once_with_the_pin(gl, job):
    gl.mr()
    result = await nightshift.merge(1)
    assert result.ok, result.detail
    assert len(gl.puts()) == 1
    assert gl.puts()[0][2]["sha"] == PIN
    assert result.evidence["merge_commit_sha"] == "c" * 40
    assert result.evidence["job_id"] == 1
    assert SECRET not in json.dumps(result.evidence) + result.detail


async def test_a_retry_after_success_does_not_merge_again(gl, job):
    gl.mr()
    first = await nightshift.merge(1)
    second = await nightshift.merge(1)
    assert first.ok and second.ok
    assert "already merged" in second.detail
    assert len(gl.puts()) == 1


async def test_merged_at_another_commit_is_refused_not_merged(gl, job):
    gl.mr(state="merged", sha=OTHER)
    result = await nightshift.merge(1)
    assert not result.ok and "another commit" in result.detail
    assert gl.puts() == []


async def test_no_mr_opens_one_and_waits_for_its_pipeline(gl, job):
    gl.pipeline = {"id": 10, "status": "running", "sha": PIN}
    result = await nightshift.merge(1)
    assert not result.ok and "running, not passed" in result.detail
    assert [c[0] for c in gl.calls].count("POST") == 1
    # The next attempt reuses that MR, never opens a second.
    gl.pipeline = {"id": 10, "status": "success", "sha": PIN}
    again = await nightshift.merge(1)
    assert again.ok, again.detail
    assert [c[0] for c in gl.calls].count("POST") == 1
    assert len(gl.puts()) == 1


async def test_gitlab_refusing_the_merge_is_a_plain_failure(gl, job):
    gl.mr()
    gl.fail["PUT"] = 405
    result = await nightshift.merge(1)
    assert not result.ok and "GitLab refused" in result.detail


async def test_a_job_that_is_not_done_is_refused(gl, job):
    job["status"] = "running"
    result = await nightshift.merge(1)
    assert not result.ok and "not done" in result.detail
    assert gl.calls == []


async def test_the_dry_run_names_the_pinned_commit(gl, job):
    result = await nightshift.merge_dry(1)
    assert result.ok and PIN[:12] in result.detail
    assert gl.calls == []


def test_merge_keeps_its_tier():
    from atlas.kernel.contracts import Tier
    merge_action, = [a for a in nightshift.MODULE.actions()
                     if a.name == "nightshift.merge"]
    assert merge_action.tier is Tier.ASK and merge_action.irreversible


# ---------------------------------------------------------- atlas is never listed

@pytest.mark.parametrize("repos", [
    "aicode.storefront.test/root/atlas",
    "aicode.storefront.test/root/ATLAS.git",
    "aicode.storefront.test/root/flycheap-tracker, aicode.storefront.test/root/atlas",
    "code.matrix.example.com/miro/atlas-tools",
])
def test_nightshift_repos_never_includes_atlas(monkeypatch, repos):
    monkeypatch.setenv("NIGHTSHIFT_REPOS", repos)
    allowed = nightshift.allowed_repos()
    assert not any("atlas" in k for k in allowed)
    url, why = nightshift.repo_url("https://aicode.storefront.test/root/atlas.git")
    assert url is None and "ATLAS" in why


async def test_the_merge_helper_refuses_atlas_itself(gl):
    result = await gitlab.merge_branch("https://aicode.storefront.test/root/atlas.git",
                                       "nightshift/job-1", PIN)
    assert not result.ok and "ATLAS" in result.detail
    assert gl.calls == []


async def test_the_merge_helper_refuses_an_unknown_host(gl):
    result = await gitlab.merge_branch("https://gitlab.com/group/proj.git",
                                       "nightshift/job-1", PIN)
    assert not result.ok and "not one of ATLAS's GitLab instances" in result.detail


async def test_the_merge_helper_refuses_no_pin(gl):
    result = await gitlab.merge_branch(REPO, "nightshift/job-1", "")
    assert not result.ok and "no commit SHA" in result.detail
    assert gl.calls == []


# ------------------------------------------------------ pinned when it finishes

async def test_a_job_finishing_done_pins_its_branch_head(gl, monkeypatch):
    updates = []

    async def fake_execute(sql, *args):
        updates.append((" ".join(sql.split()), args))

    async def fake_logs(container):
        return f"NIGHTSHIFT_HEAD_SHA={OTHER}"      # model output never counts

    async def fake_record_log(job_id, argv, output):
        return "data/nightshift/1.log"

    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "logs", fake_logs)
    monkeypatch.setattr(nightshift, "_record_log", fake_record_log)
    await nightshift._finish_job(1, "nightshift-1", "task", REPO, "nightshift/job-1",
                                 "exited(0)", datetime.now(UTC))
    pins = [args for sql, args in updates if "head_sha" in sql]
    assert pins == [(PIN, 1)]
    assert "head_sha IS NULL" in next(sql for sql, _ in updates if "head_sha" in sql)


async def test_a_failed_job_pins_nothing(gl, monkeypatch):
    updates = []

    async def fake_execute(sql, *args):
        updates.append(sql)

    async def fake_logs(container):
        return ""

    async def fake_record_log(job_id, argv, output):
        return "data/nightshift/1.log"

    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "logs", fake_logs)
    monkeypatch.setattr(nightshift, "_record_log", fake_record_log)
    await nightshift._finish_job(1, "nightshift-1", "task", REPO, "nightshift/job-1",
                                 "exited(1)", datetime.now(UTC))
    assert not any("head_sha" in sql for sql in updates)
    assert gl.calls == []


# --------------------------------------------------------------- read-back

async def test_readback_verified_at_the_pin(gl, job):
    gl.mr(state="merged", merge_commit_sha="c" * 40)
    assert v.has_verifier("nightshift.merge")
    got = await v.verify("nightshift.merge", {"job_id": 1}, ActionResult(True))
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["pinned_sha"] == PIN
    assert all(c[0] == "GET" for c in gl.calls)


async def test_readback_contradicted_at_another_sha(gl, job):
    gl.mr(state="merged", sha=OTHER)
    got = await v.verify("nightshift.merge", {"job_id": 1}, ActionResult(True))
    assert got.state is v.State.CONTRADICTED
    assert got.failure_class is v.FailureClass.SECURITY


async def test_readback_contradicted_when_not_merged(gl, job):
    gl.mr()
    got = await v.verify("nightshift.merge", {"job_id": 1}, ActionResult(True))
    assert got.state is v.State.CONTRADICTED and "is merged" in got.reason


async def test_readback_unknown_without_a_pin_or_gitlab(gl, job):
    job["head_sha"] = None
    got = await v.verify("nightshift.merge", {"job_id": 1}, ActionResult(True))
    assert got.state is v.State.UNKNOWN and got.reason != "no verifier"
    job["head_sha"] = PIN
    gl.fail["GET"] = 503
    got = await v.verify("nightshift.merge", {"job_id": 1}, ActionResult(True))
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.TRANSIENT


async def test_readback_raises_is_unknown(gl, job, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError(f"broken with {SECRET}")

    monkeypatch.setattr(gitlab, "merge_requests_for", boom)
    got = await v.verify("nightshift.merge", {"job_id": 1}, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    assert SECRET not in json.dumps(got.as_audit(), default=str)


async def test_reconciler_checks_the_sha(gl, job):
    row = {"at": datetime.now(UTC), "payload": {"data": {"params": {"job_id": 1}}}}
    gl.mr(state="merged", sha=OTHER)
    got = await v.reconcile_unknown("nightshift.merge", None, row)
    assert got.state is v.State.CONTRADICTED
    assert got.failure_class is v.FailureClass.SECURITY
    gl.mrs.clear()
    gl.mr(state="merged")
    got = await v.reconcile_unknown("nightshift.merge", None, row)
    assert got.state is v.State.VERIFIED
    assert gl.puts() == []


# ------------------------------------------------- a pin that failed is retried

async def test_a_failed_pin_read_is_logged_and_retried_later(gl, monkeypatch):
    """A GitLab blip at finish time must not leave the job unmergeable for
    good: the next check reads the pin again, within the window."""
    from datetime import timedelta
    now = datetime.now(UTC)
    rows = {1: {"id": 1, "repo": REPO, "branch": "nightshift/job-1", "status": "done",
                "kind": "code", "head_sha": None,
                "finished_at": now - timedelta(minutes=20)},
            2: {"id": 2, "repo": REPO, "branch": "nightshift/job-2", "status": "done",
                "kind": "code", "head_sha": None,
                "finished_at": now - timedelta(hours=5)}}

    async def fetch_all(sql, *args):
        if "head_sha IS NULL" in sql:
            assert "kind = 'code'" in sql and "status = 'done'" in sql
            since = args[0]
            return [dict(r) for r in rows.values()
                    if r["head_sha"] is None and r["finished_at"] >= since]
        return []

    async def execute_sql(sql, *args):
        sha, job_id = args
        assert "head_sha IS NULL" in sql            # a pin is never moved
        if rows[job_id]["head_sha"] is None:
            rows[job_id]["head_sha"] = sha

    monkeypatch.setattr(nightshift, "fetch_all", fetch_all)
    monkeypatch.setattr(nightshift, "execute_sql", execute_sql)

    gl.fail["GET /api/v4/projects/root%2Fflycheap-tracker/repository"] = 503
    assert await nightshift._pin_head(1, REPO, "nightshift/job-1") is False
    assert rows[1]["head_sha"] is None

    gl.fail.clear()
    await nightshift.check_jobs()
    assert rows[1]["head_sha"] == PIN
    assert rows[2]["head_sha"] is None            # outside the window: left alone

    gl.branch_sha = OTHER                          # a later push never moves it
    await nightshift._pin_head(1, REPO, "nightshift/job-1")
    assert rows[1]["head_sha"] == PIN


async def test_pinning_never_breaks_the_check(monkeypatch):
    async def fetch_all(sql, *args):
        if "head_sha" in sql:
            raise RuntimeError('column "head_sha" does not exist')
        return []

    monkeypatch.setattr(nightshift, "fetch_all", fetch_all)
    obs = await nightshift.check_jobs()
    assert obs.health.name == "OK"


async def test_a_closed_mr_is_refused_not_replaced(gl, job):
    gl.mr(state="closed")
    result = await nightshift.merge(1)
    assert not result.ok and "was closed" in result.detail
    assert [c[0] for c in gl.calls if c[0] != "GET"] == []


async def test_a_refusal_after_opening_an_mr_says_so(gl, job):
    gl.pipeline = {"id": 10, "status": "pending", "sha": PIN}
    result = await nightshift.merge(1)
    assert not result.ok and "opened !4" in result.detail
