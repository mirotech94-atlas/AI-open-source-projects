"""nightshift — the overnight sandbox.

What has to hold, in order of how bad it is to get wrong:

  * every docker invocation is an argv list, never a shell string, and never
    carries a secret it was not explicitly given (invariant 4: every ingested
    byte — here, the task text an LLM chose — is untrusted, and a shell would
    be the one place that trust could be broken);
  * `nightshift.run` is Tier AUTO, so it needs a real dry run and a real
    rollback (invariant 8);
  * `nightshift.merge` is Tier ASK and irreversible, and merges only the
    commit pinned when the job finished (tests/test_nightshift_merge.py);
  * a docker daemon ATLAS cannot reach is UNKNOWN, never a guess about the
    job inside (invariant 7);
  * a failed job is reported as FAILED in the brief, never dressed up
    (invariant 6, in spirit: never invent a rosier answer than what was
    measured).
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify
from atlas.modules import nightshift
from atlas.modules.nightshift import sandbox

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "nightshift"


class _Rec:
    """Minimal stand-in for `asyncpg.Record`: supports item access
    (`row["col"]`) but is deliberately NOT a `dict` and has no `.get`. A
    module that branches on `isinstance(row, dict)` — as this one used to —
    silently takes the wrong path on a real Record and passes the whole
    object through where a scalar was expected. Using this instead of a
    plain dict for storage-shaped fakes catches that regression."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __repr__(self):
        return f"_Rec({self._data!r})"


# --------------------------------------------------------- no executor path

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "nightshift"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


# --------------------------------------------------------------- docker_argv

@pytest.fixture
def secret_sentinels(monkeypatch):
    """Env vars that must never leak, alongside the two nightshift is
    explicitly allowed to use."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://sentinel-db-secret/x")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-sentinel-slack-secret")
    monkeypatch.setenv("NIGHTSHIFT_ANTHROPIC_KEY", "sk-ant-sentinel")
    monkeypatch.setenv("NIGHTSHIFT_GIT_TOKEN", "ghp-sentinel")


def test_docker_argv_is_locked_down(secret_sentinels):
    argv = sandbox.docker_argv(7, "fix the flaky test", "git@host:org/repo.git",
                               "nightshift/job-7")
    assert "--read-only" in argv
    assert "--network" in argv
    assert argv[argv.index("--network") + 1] == "nightshift"
    assert "-v" not in argv
    assert "nightshift-7" in argv
    # detached: start() needs the docker CLI call itself to return quickly,
    # not block for as long as the job runs.
    assert "-d" in argv


def test_docker_argv_accepts_a_non_numeric_job_id_for_dry_runs():
    """run_dry has no real job id yet — "dry-run" must not blow up argv
    construction, which only ever interpolates it into strings."""
    argv = sandbox.docker_argv("dry-run", "task", "repo",
                               sandbox.branch_name("dry-run"))
    assert "nightshift-dry-run" in argv


def test_docker_argv_never_carries_an_unrelated_secret(secret_sentinels):
    argv = sandbox.docker_argv(7, "task", "repo", "branch")
    joined = " ".join(argv)
    forbidden = sandbox.forbidden_env()
    assert "DATABASE_URL" in forbidden
    assert "SLACK_BOT_TOKEN" in forbidden
    assert "NIGHTSHIFT_ANTHROPIC_KEY" not in forbidden
    assert "NIGHTSHIFT_GIT_TOKEN" not in forbidden
    import os
    for name in forbidden:
        assert os.environ[name] not in joined


def test_docker_argv_passes_the_two_secrets_by_name_only(secret_sentinels):
    """2026-09-27: the git token may be instance-wide, so it never sits on
    the host's argv or in `docker inspect`; docker copies it from the
    client environment `start` builds."""
    argv = sandbox.docker_argv(7, "task", "repo", "branch")
    joined = " ".join(argv)
    assert "sk-ant-sentinel" not in joined and "ghp-sentinel" not in joined
    for name in ("GIT_TOKEN", "ANTHROPIC_API_KEY"):
        assert argv[argv.index(name) - 1] == "-e"


async def test_start_hands_the_secrets_to_docker_through_its_environment(
        secret_sentinels, monkeypatch):
    seen = {}

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*argv, **kw):
        seen.update(kw)
        return FakeProc()

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    await sandbox.start(3, "task", "repo")
    assert seen["env"]["GIT_TOKEN"] == "ghp-sentinel"
    assert seen["env"]["ANTHROPIC_API_KEY"] == "sk-ant-sentinel"
    assert "DATABASE_URL" not in seen["env"] and "SLACK_BOT_TOKEN" not in seen["env"]


def test_the_model_never_holds_the_token_and_only_its_branch_is_pushed():
    """entry.sh: the model runs as `night` under `env -i`, the clone's remote
    is stripped of the token, and root pushes only refs/heads/$BRANCH (which
    must be nightshift/job-<id>) from its own bare repo with hooks off."""
    import pathlib
    script = (pathlib.Path(sandbox.__file__).resolve().parents[3]
              / "bootstrap" / "nightshift" / "entry.sh").read_text()
    assert "unset GIT_TOKEN ANTHROPIC_API_KEY" in script
    assert "runuser -u night -- env -i" in script
    assert 'remote set-url origin "$REPO"' in script
    assert '"refs/heads/$BRANCH:refs/heads/$BRANCH"' in script
    assert "^nightshift/job-" in script
    assert "core.hooksPath=/dev/null" in script
    assert "--force" not in script and " -f " not in script
    # the push is from root's own bare repo, never from the model's tree
    push_line = next(l for l in script.splitlines() if " push -q" in l)
    assert '-C "$BARE"' in push_line


def test_docker_argv_task_is_one_argv_element_not_shell_text():
    """A task string with shell metacharacters must stay inert: it is one
    list element handed to exec, never parsed by a shell."""
    hostile = "task; rm -rf / #"
    argv = sandbox.docker_argv(1, hostile, "repo", "branch")
    assert f"TASK={hostile}" in argv
    assert not any(a == "rm" for a in argv)


# -------------------------------------------------------------- start / stop

async def test_start_returns_the_container_name(monkeypatch):
    calls = []

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"nightshift-3\n", b""

    async def fake_exec(*argv, **kw):
        calls.append(argv)
        return FakeProc()

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    container, reason = await sandbox.start(3, "do the thing", "repo-url")
    assert container == "nightshift-3"
    assert reason is None
    assert calls[0][0] == "docker"
    assert "-d" in calls[0]


async def test_start_reports_a_reason_when_docker_is_missing(monkeypatch):
    async def fake_exec(*argv, **kw):
        raise FileNotFoundError("no docker binary")

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    container, reason = await sandbox.start(3, "task", "repo")
    assert container is None
    assert "docker" in reason


async def test_start_reports_a_reason_when_docker_run_itself_fails(monkeypatch):
    """A real launch failure — bad image, missing network — must come back
    as a reason, not a container name nothing actually started. The reason
    is also run through redact(), since stderr could in principle echo back
    part of an -e value."""
    class FakeProc:
        returncode = 125

        async def communicate(self):
            return b"", b"docker: Error response from daemon: network nightshift not found\n"

    async def fake_exec(*argv, **kw):
        return FakeProc()

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    container, reason = await sandbox.start(4, "task", "repo")
    assert container is None
    assert "network nightshift not found" in reason


async def test_status_reports_running_exited_and_missing(monkeypatch):
    async def make(output: bytes, code: int):
        class FakeProc:
            returncode = code

            async def communicate(self):
                return output, b""

        async def fake_exec(*argv, **kw):
            return FakeProc()
        return fake_exec

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec",
                        await make(b"running|0", 0))
    assert await sandbox.status("c") == "running"

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec",
                        await make(b"exited|1", 0))
    assert await sandbox.status("c") == "exited(1)"

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec",
                        await make(b"", 1))
    assert await sandbox.status("c") == "missing"


async def test_status_raises_docker_unavailable_when_docker_cannot_be_asked(
        monkeypatch):
    async def fake_exec(*argv, **kw):
        raise FileNotFoundError("no docker")

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(sandbox.DockerUnavailable):
        await sandbox.status("c")


async def test_logs_returns_docker_logs_output(monkeypatch):
    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"build ok\npushed\n", b""

    async def fake_exec(*argv, **kw):
        assert argv[:2] == ("docker", "logs")
        return FakeProc()

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    assert await sandbox.logs("c") == "build ok\npushed\n"


async def test_logs_is_a_placeholder_when_docker_cannot_be_asked(monkeypatch):
    async def fake_exec(*argv, **kw):
        raise FileNotFoundError("no docker")

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    out = await sandbox.logs("c")
    assert "could not fetch logs" in out


# -------------------------------------------------------------------- run()

REPO = "https://code.example.org/miro/flycheap.git"


@pytest.fixture(autouse=True)
def allowed(monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_REPOS", "code.example.org/miro/flycheap, "
                                           "gitlab.com/group/sub/proj")


async def test_run_records_a_job_and_returns_the_container_name(monkeypatch):
    updates = []

    async def fake_fetch_one(sql, *args):
        assert "INSERT INTO nightshift_jobs" in sql
        return _Rec({"id": 42})

    async def fake_execute(sql, *args):
        updates.append((" ".join(sql.split()), args))
        return "OK"

    async def fake_start(job_id, task, repo):
        assert job_id == 42
        assert repo == REPO
        return f"nightshift-{job_id}", None

    monkeypatch.setattr(nightshift, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "start", fake_start)

    result = await nightshift.run("fix the thing", REPO)

    assert result.ok
    assert result.evidence["container"] == "nightshift-42"
    assert result.evidence["job_id"] == 42
    assert result.rollback_token == "42"
    assert any("'running'" in sql for sql, _ in updates if "SET status" in sql)


async def test_run_marks_the_job_failed_when_the_sandbox_cannot_start(
        monkeypatch):
    async def fake_fetch_one(sql, *args):
        return {"id": 5}

    updates = []

    async def fake_execute(sql, *args):
        updates.append((" ".join(sql.split()), args))
        return "OK"

    async def fake_start(job_id, task, repo):
        return None, "docker not available"

    monkeypatch.setattr(nightshift, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "start", fake_start)

    result = await nightshift.run("task", REPO)
    assert not result.ok
    assert "docker not available" in result.detail
    assert any("'failed'" in sql for sql, _ in updates)


async def test_run_dry_never_calls_fetch_one_or_execute_sql(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry run must not touch storage")

    monkeypatch.setattr(nightshift, "fetch_one", boom)
    monkeypatch.setattr(nightshift, "execute_sql", boom)
    result = await nightshift.run_dry("task", REPO)
    assert result.ok
    assert "would run" in result.detail
    # a placeholder, not a real (or falsely-zero) job id
    assert "dry-run" in result.detail
    assert "nightshift-0" not in result.detail


def _rollback_fakes(monkeypatch, job: dict | None, deleted=(True, "deleted")):
    stopped: list = []
    updates: list = []
    deletes: list = []

    async def fake_fetch_one(sql, *args):
        return _Rec(job) if job is not None else None

    async def fake_execute(sql, *args):
        updates.append((" ".join(sql.split()), args))
        return "OK"

    async def fake_stop(container):
        stopped.append(container)

    async def fake_delete(url, branch):
        deletes.append((url, branch))
        return deleted

    monkeypatch.setattr(nightshift, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "stop", fake_stop)
    monkeypatch.setattr(sandbox, "delete_branch", fake_delete)
    return stopped, updates, deletes


JOB = {"container": "nightshift-9", "repo": REPO, "branch": "nightshift/job-9"}


async def test_run_rollback_stops_the_container_then_deletes_the_branch(monkeypatch):
    stopped, updates, deletes = _rollback_fakes(monkeypatch, JOB)
    result = await nightshift.run_rollback("9")
    assert result.ok
    assert stopped == ["nightshift-9"]
    assert deletes == [(REPO, "nightshift/job-9")]
    assert any("'stopped'" in sql for sql, _ in updates)


async def test_a_branch_that_cannot_be_deleted_is_a_failed_rollback(monkeypatch):
    stopped, updates, _deletes = _rollback_fakes(
        monkeypatch, JOB, deleted=(False, "could not reach the repository"))
    result = await nightshift.run_rollback("9")
    assert result.ok is False
    assert "could not delete nightshift/job-9" in result.detail
    assert "could not reach the repository" in result.detail
    assert stopped == ["nightshift-9"], "the container is still stopped"
    assert any("'stopped'" in sql for sql, _ in updates)


@pytest.mark.parametrize("job", [
    dict(JOB, branch="main"),                              # never another branch
    dict(JOB, branch="nightshift/job-10"),                 # nor another job's
    dict(JOB, repo="https://evil.example/miro/flycheap"),  # nor an unlisted repo
])
async def test_the_rollback_deletes_only_its_own_branch_on_a_listed_repo(
        monkeypatch, job):
    _stopped, _updates, deletes = _rollback_fakes(monkeypatch, job)
    result = await nightshift.run_rollback("9")
    assert result.ok is False and deletes == []


async def test_a_rollback_that_raises_reports_a_failure(monkeypatch):
    async def broken(*_a, **_k):
        raise RuntimeError("database gone")

    monkeypatch.setattr(nightshift, "fetch_one", broken)
    result = await nightshift.run_rollback("9")
    assert result.ok is False and "rollback failed" in result.detail


# --------------------------------------------------- the NIGHTSHIFT_REPOS list

@pytest.mark.parametrize("repo", [
    "https://code.example.org/miro/flycheap.git",
    "https://code.example.org/miro/flycheap",
    "https://CODE.example.org/Miro/FlyCheap/",
    "https://gitlab.com/group/sub/proj.git",
])
def test_a_listed_repo_is_cloned_from_the_list_entry(repo):
    url, why = nightshift.repo_url(repo)
    assert url in ("https://code.example.org/miro/flycheap.git",
                   "https://gitlab.com/group/sub/proj.git"), why


@pytest.mark.parametrize("repo", [
    "http://code.example.org/miro/flycheap.git",                 # not https
    "git@code.example.org:miro/flycheap.git",                    # not a URL we take
    "https://evil.example/miro/flycheap.git",              # not listed
    "https://code.example.org.evil.example/miro/flycheap.git",   # a suffix is not the host
    "https://code.example.org@evil.example/miro/flycheap.git",   # userinfo trick
    "https://evil.example@code.example.org/miro/flycheap.git",   # any userinfo at all
    "https://tok:x@code.example.org/miro/flycheap.git",
    "https://code.example.org:8443/miro/flycheap.git",           # a port
    "https://code.example.org/miro/flycheap.git?x=1",            # a query
    "https://code.example.org/miro/flycheap.git#frag",
    "https://code.example.org/miro/../miro/flycheap.git",        # dot segments
    "https://evil.example\\@code.example.org/miro/flycheap",     # a backslash
    "https://code.example.org/miro",                              # too short
    "https://code.example.org/miro/other",                        # same host, other repo
    "",
])
def test_anything_off_the_list_is_refused(repo):
    url, why = nightshift.repo_url(repo)
    assert url is None and why


def test_an_unset_list_allows_nothing(monkeypatch):
    monkeypatch.delenv("NIGHTSHIFT_REPOS")
    url, why = nightshift.repo_url(REPO)
    assert url is None and "NIGHTSHIFT_REPOS" in why


@pytest.mark.parametrize("fn", ["run", "run_dry"])
async def test_an_unlisted_repo_is_refused_before_the_token_is_used(
        monkeypatch, fn):
    def boom(*a, **k):
        raise AssertionError("nothing may be recorded, built or started")

    monkeypatch.setattr(nightshift, "fetch_one", boom)
    monkeypatch.setattr(nightshift, "execute_sql", boom)
    monkeypatch.setattr(sandbox, "start", boom)
    monkeypatch.setattr(sandbox, "docker_argv", boom)
    result = await getattr(nightshift, fn)("push it",
                                           "https://evil.example/x/y.git")
    assert result.ok is False and "refused" in result.detail


# ------------------------------------------------------ deleting the branch

def test_the_delete_command_never_carries_the_token(secret_sentinels, monkeypatch):
    monkeypatch.setenv("NIGHTSHIFT_GIT_TOKEN", "glpat-sentinel-git-token")
    argv = sandbox.delete_branch_argv(REPO, "nightshift/job-9")
    assert "glpat-sentinel-git-token" not in " ".join(argv)
    assert argv[argv.index("GIT_TOKEN") - 1] == "-e", "passed by name only"
    assert "--network" in argv and "nightshift" in argv


@pytest.mark.parametrize(("out", "code", "ok"), [
    (b"absent\n", 0, True), (b"deleted\n", 0, True), (b"", 3, False),
    (b"", 4, False),
])
async def test_delete_branch_reads_the_container_answer(monkeypatch, out, code, ok):
    monkeypatch.setenv("NIGHTSHIFT_GIT_TOKEN", "glpat-sentinel-git-token")
    seen: dict = {}

    class Proc:
        returncode = code

        async def communicate(self):
            return out, b"could not reach https://glpat-sentinel-git-token@x"

    async def fake_exec(*argv, **kw):
        seen["argv"], seen["env"] = argv, kw.get("env")
        return Proc()

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    done, why = await sandbox.delete_branch(REPO, "nightshift/job-9")
    assert done is ok
    assert seen["env"]["GIT_TOKEN"] == "glpat-sentinel-git-token"
    assert "glpat-sentinel-git-token" not in " ".join(seen["argv"])
    assert "glpat-sentinel-git-token" not in why


async def test_without_a_token_nothing_was_pushed(monkeypatch):
    monkeypatch.delenv("NIGHTSHIFT_GIT_TOKEN", raising=False)

    async def boom(*a, **k):
        raise AssertionError("no docker call is needed")

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", boom)
    done, why = await sandbox.delete_branch(REPO, "nightshift/job-9")
    assert done and "nothing was pushed" in why


# ------------------------------------------------------------------ merge()

SHA = "a" * 40


async def test_merge_refuses_a_job_with_no_pinned_commit(monkeypatch):
    async def fake_fetch_one(sql, *args):
        return _Rec({"repo": REPO, "branch": "nightshift/job-1", "status": "done",
                     "kind": "code", "head_sha": None})

    async def never(*a, **k):
        raise AssertionError("GitLab must not be called without a pin")

    monkeypatch.setattr(nightshift, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(nightshift.gitlab, "merge_branch", never)
    result = await nightshift.merge(1)
    assert not result.ok
    assert "no pinned commit" in result.detail


async def test_merge_hands_the_pinned_commit_to_the_gitlab_helper(monkeypatch):
    async def fake_fetch_one(sql, *args):
        return _Rec({"repo": REPO, "branch": "nightshift/job-1", "status": "done",
                     "kind": "code", "head_sha": SHA})

    calls = []

    async def fake_merge_branch(repo, branch, sha):
        calls.append((repo, branch, sha))
        from atlas.kernel.contracts import ActionResult
        return ActionResult(True, "merged", evidence={"iid": 3})

    monkeypatch.setattr(nightshift, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(nightshift.gitlab, "merge_branch", fake_merge_branch)

    result = await nightshift.merge(1)
    assert result.ok
    assert calls == [(REPO, "nightshift/job-1", SHA)]
    assert result.evidence == {"job_id": 1, "iid": 3}


async def test_merge_reports_an_unknown_job():
    async def fake_fetch_one(sql, *args):
        return None

    import atlas.modules.nightshift as ns
    old = ns.fetch_one
    ns.fetch_one = fake_fetch_one
    try:
        result = await ns.merge(999)
    finally:
        ns.fetch_one = old
    assert not result.ok
    assert "999" in result.detail


def test_merge_is_tier_ask_and_irreversible():
    merge_action, = [a for a in nightshift.MODULE.actions()
                     if a.name == "nightshift.merge"]
    assert merge_action.tier is Tier.ASK
    assert merge_action.irreversible is True
    decision = classify(merge_action)
    assert decision.allowed
    assert decision.tier is Tier.ASK


def test_run_is_tier_auto_with_dry_run_and_rollback():
    run_action, = [a for a in nightshift.MODULE.actions()
                  if a.name == "nightshift.run"]
    assert run_action.tier is Tier.AUTO
    assert run_action.dry_run is not None
    assert run_action.rollback is not None
    assert run_action.irreversible is False
    decision = classify(run_action)
    assert decision.allowed
    assert decision.tier is Tier.AUTO


# -------------------------------------------------------------- check_jobs()

async def test_check_jobs_is_unknown_when_docker_is_unreachable(monkeypatch):
    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return [{"id": 1, "container": "nightshift-1",
                     "at": datetime.now(UTC)}]
        return []

    async def fake_status(container):
        raise sandbox.DockerUnavailable("no docker binary")

    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(sandbox, "status", fake_status)

    obs = await nightshift.check_jobs()
    assert obs.health is Health.UNKNOWN
    assert "docker unreachable" in obs.detail


async def test_check_jobs_is_unknown_when_the_table_cannot_be_read(monkeypatch):
    async def boom(sql, *args):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(nightshift, "fetch_all", boom)
    obs = await nightshift.check_jobs()
    assert obs.health is Health.UNKNOWN


async def test_check_jobs_flags_a_job_running_past_the_limit(monkeypatch):
    started = datetime.now(UTC) - timedelta(hours=nightshift.NIGHTSHIFT_MAX_HOURS + 1)

    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return [_Rec({"id": 1, "container": "nightshift-1", "at": started})]
        return []

    async def fake_status(container):
        return "running"

    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(sandbox, "status", fake_status)

    obs = await nightshift.check_jobs()
    assert obs.health is Health.DEGRADED
    assert obs.evidence["job_ids"] == [1]


async def test_check_jobs_is_ok_with_nothing_running(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return []

    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    obs = await nightshift.check_jobs()
    assert obs.health is Health.OK
    assert obs.value == 0.0


# ------------------------------------------------- check_jobs() finishes a job

def _running_row(**over):
    row = {"id": 1, "container": "nightshift-1", "at": datetime.now(UTC),
           "task": "add retries", "repo": "org/repo",
           "branch": "nightshift/job-1"}
    row.update(over)
    return _Rec(row)


async def test_check_jobs_marks_a_clean_exit_done(monkeypatch):
    updates = []

    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return [_running_row()]
        return []

    async def fake_execute(sql, *args):
        updates.append((" ".join(sql.split()), args))
        return "OK"

    async def fake_status(container):
        return "exited(0)"

    async def fake_logs(container):
        return "build ok"

    async def fake_record_log(job_id, argv, output):
        return f"data/nightshift/{job_id}.log"

    monkeypatch.setenv("NIGHTSHIFT_PREVIEW_BASE", "https://preview.example")
    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "status", fake_status)
    monkeypatch.setattr(sandbox, "logs", fake_logs)
    monkeypatch.setattr(nightshift, "_record_log", fake_record_log)

    obs = await nightshift.check_jobs()
    assert obs.health is Health.OK
    assert obs.value == 0.0        # no longer counted as running

    finish_sql, finish_args = next(
        (sql, args) for sql, args in updates if "SET status" in sql)
    assert "log_path" in finish_sql and "preview_url" in finish_sql
    assert finish_args[0] == "done"
    assert finish_args[2] == "data/nightshift/1.log"
    assert finish_args[3] == "https://preview.example/nightshift/job-1"
    assert finish_args[4] == 1


async def test_check_jobs_marks_a_nonzero_exit_failed(monkeypatch):
    updates = []

    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return [_running_row()]
        return []

    async def fake_execute(sql, *args):
        updates.append((" ".join(sql.split()), args))
        return "OK"

    monkeypatch.delenv("NIGHTSHIFT_PREVIEW_BASE", raising=False)
    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "status", lambda c: _async("exited(1)"))
    monkeypatch.setattr(sandbox, "logs", lambda c: _async("boom"))
    monkeypatch.setattr(nightshift, "_record_log",
                        lambda job_id, argv, output: _async("data/nightshift/1.log"))

    obs = await nightshift.check_jobs()
    assert obs.health is Health.OK
    finish_args = next(args for sql, args in updates if "SET status" in sql)
    assert finish_args[0] == "failed"
    assert finish_args[3] is None       # no NIGHTSHIFT_PREVIEW_BASE set


async def test_check_jobs_marks_a_vanished_container_failed(monkeypatch):
    updates = []
    logs_calls = []

    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return [_running_row()]
        return []

    async def fake_execute(sql, *args):
        updates.append((" ".join(sql.split()), args))
        return "OK"

    async def fake_logs(container):
        logs_calls.append(container)
        return "should not be called"

    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(nightshift, "execute_sql", fake_execute)
    monkeypatch.setattr(sandbox, "status", lambda c: _async("missing"))
    monkeypatch.setattr(sandbox, "logs", fake_logs)
    monkeypatch.setattr(nightshift, "_record_log",
                        lambda job_id, argv, output: _async("data/nightshift/1.log"))

    obs = await nightshift.check_jobs()
    assert obs.health is Health.OK
    finish_args = next(args for sql, args in updates if "SET status" in sql)
    assert finish_args[0] == "failed"
    assert logs_calls == []             # a gone container has no logs to fetch


def _async(value):
    """Wrap a plain return value as a fresh coroutine each call, for
    monkeypatching an `async def` with a plain `lambda`."""
    async def _coro():
        return value
    return _coro()


async def test_record_log_redacts_the_secrets_in_the_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("NIGHTSHIFT_ANTHROPIC_KEY", "sk-ant-should-not-appear")
    monkeypatch.setattr(nightshift, "LOG_DIR", tmp_path)

    argv = sandbox.docker_argv(1, "task", "repo", "branch")
    path = await nightshift._record_log(1, argv, "plain output")

    written = pathlib.Path(path).read_text()
    assert "sk-ant-should-not-appear" not in written
    assert "plain output" in written


# --------------------------------------------------- brief() cache freshness

async def test_check_jobs_refreshes_the_brief_cache_even_when_docker_is_down(
        monkeypatch):
    """A docker outage must not also silence what already finished: the
    finished-jobs read and the running-jobs/docker read are independent."""
    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return [_running_row()]
        return [{"task": "add retries", "repo": "org/repo",
                 "branch": "nightshift/job-1", "status": "done",
                 "preview_url": "https://preview.example/1"}]

    async def fake_status(container):
        raise sandbox.DockerUnavailable("no docker binary")

    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(sandbox, "status", fake_status)

    obs = await nightshift.check_jobs()
    assert obs.health is Health.UNKNOWN
    assert "nightshift/job-1" in nightshift.brief()


async def test_refresh_recent_lines_reports_its_own_failure(monkeypatch):
    async def boom(sql, *args):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(nightshift, "fetch_all", boom)
    await nightshift._refresh_recent_lines(datetime.now(UTC))
    assert "could not read jobs" in nightshift.brief()
    assert "connection refused" in nightshift.brief()


# ----------------------------------------------------------------- brief()

async def test_brief_reports_a_failed_job_without_dressing_it_up(monkeypatch):
    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return []
        return [{"task": "upgrade the flaky dependency", "repo": "org/repo",
                 "branch": "nightshift/job-9", "status": "failed",
                 "preview_url": None}]

    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    await nightshift.check_jobs()
    line = nightshift.brief()
    assert "FAILED" in line
    assert "upgrade the flaky dependency" in line
    # never softened with anything claiming success
    assert "preview" not in line.lower()
    assert "as of" in line


async def test_brief_reports_a_done_job_with_its_preview(monkeypatch):
    async def fake_fetch_all(sql, *args):
        if "status = 'running'" in sql:
            return []
        return [{"task": "add retry", "repo": "org/repo",
                 "branch": "nightshift/job-11", "status": "done",
                 "preview_url": "https://preview.example/11"}]

    monkeypatch.setattr(nightshift, "fetch_all", fake_fetch_all)
    await nightshift.check_jobs()
    line = nightshift.brief()
    assert "nightshift/job-11" in line
    assert "org/repo" in line
    assert "https://preview.example/11" in line


def test_brief_is_empty_with_no_recent_jobs(monkeypatch):
    nightshift._recent_lines.clear()
    monkeypatch.setattr(nightshift, "_recent_at", None)
    assert nightshift.brief() == ""


# ------------------------------------------------------------- module shape

def test_module_declares_no_required_env():
    assert nightshift.MODULE.requires_env == ()
    assert set(nightshift.MODULE.optional_env) == {
        "NIGHTSHIFT_ANTHROPIC_KEY", "NIGHTSHIFT_GIT_TOKEN"}



def test_the_sandbox_reaches_out_only_through_the_proxy(monkeypatch):
    monkeypatch.delenv("NIGHTSHIFT_PROXY", raising=False)
    argv = sandbox.docker_argv(7, "task", "repo", "nightshift/job-7")
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        assert f"{name}=http://nightshift-proxy:8888" in argv
    assert argv[argv.index("--network") + 1] == "nightshift"


def test_the_model_keeps_the_proxy_but_nothing_secret_but_its_key():
    import pathlib
    script = (pathlib.Path(sandbox.__file__).resolve().parents[3]
              / "bootstrap" / "nightshift" / "entry.sh").read_text()
    start = script.index("as_night() {")
    block = script[start:script.index("\n}\n", start)]
    assert 'HTTPS_PROXY="${HTTPS_PROXY:-}"' in block
    assert "GIT_TOKEN" not in block and "TOKEN" not in block
