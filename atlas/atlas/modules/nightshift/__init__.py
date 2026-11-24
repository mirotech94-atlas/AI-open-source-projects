"""nightshift — the overnight sandbox.

Watches: jobs recorded in `nightshift_jobs`. A job still `running` past
`NIGHTSHIFT_MAX_HOURS` is DEGRADED — it has overrun and someone should look.
Docker itself being unreachable (no binary, daemon down) is UNKNOWN, never a
guess at whether the job inside is fine.

May do: `nightshift.run` hands a task to a throwaway, network-isolated
container that clones a branch, lets Claude Code work inside it, pushes the
branch, and exits — reversible, so it is Tier AUTO: its rollback stops the
container and deletes the pushed branch, or fails and says it could not.
It runs only against a repository listed in `NIGHTSHIFT_REPOS`
(comma-separated `host/owner/name`); anything else is refused before the
git token is used, and the URL cloned is rebuilt from the list. `nightshift.merge`
accepts a GitLab merge request for that branch once Miro has looked at the
diff; merging cannot be undone by anything this module offers, so it is
Tier ASK and `irreversible=True`. ATLAS's own repository is never on the
list: an entry naming atlas is dropped from `allowed_repos`, whatever
NIGHTSHIFT_REPOS says.

`check_jobs` (the Check) also reconciles a job's own row once its container
has exited or vanished — `status`/`finished_at`/`log_path`/`preview_url` on
the very row `nightshift.run` created. That is the same shape as
`kernel.state.record_observation`: ATLAS writing down what it just measured
about its own bookkeeping, not an outside-world write. It is the only place
that can capture the job's outcome and logs before its container disappears
(`--rm`), so it happens here rather than waiting for a separate write path
that would race the container's own cleanup.

Assumes: docker is installed on the host running ATLAS; a `nightshift`
docker network with an egress allow-list already exists (a host action for
Miro — see `bootstrap/nightshift/README.md`, this module never creates
infrastructure); the `atlas-nightshift` image is built from
`bootstrap/nightshift/Dockerfile`; and, for `nightshift.merge`, a GitLab
token (GITLAB_TOKEN / GITLAB_WORK_TOKEN, by the repo's host) that may open
and accept merge requests. What is merged is pinned: when `check_jobs` sees
a job finish `done`, it reads the branch's head commit from GitLab and
records it as `nightshift_jobs.head_sha`. The merge card names that SHA,
and `gitlab.merge_branch` merges only when the MR's head is still that SHA
and its pipeline passed for it (see `merge` below). Job logs are
written to `data/nightshift/<id>.log`; `NIGHTSHIFT_PREVIEW_BASE`, if set, is
joined with the branch name to fill `preview_url` on a completed job.

Research jobs (Task 42): `nightshift.research` is a second job kind
(`nightshift_jobs.kind = 'research'`). Like `nightshift.run`, it starts when
it is executed — nothing here holds it for a night window. It runs the
research report — whose source list is Perplexity's deep research
(`perplexity_research`) when it is configured, the web search otherwise —
files a `research_reports` row, and records the job already finished (done
or failed), never `running`: there is no container for `check_jobs` to ask
docker about. The morning brief says "researched ..." for it, not a branch.
Tier AUTO: it reads the world and writes ATLAS's own rows; the rollback
withdraws the report and marks the job stopped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)
from ...kernel.state import execute_sql, fetch_all, fetch_one
from .. import gitlab
from .._http import redact
from . import sandbox

NIGHTSHIFT_MAX_HOURS = 8

log = logging.getLogger("atlas.nightshift")

LOG_DIR = Path("data/nightshift")

# Populated by `check_jobs` with one line per job that finished since its
# last run, and when that read last succeeded. `brief()` is called
# synchronously by `kernel.brief._businesses` from inside a running event
# loop, so it cannot itself await a query — `check_jobs`, which already runs
# on its own schedule and does the reading, is where "since yesterday" gets
# computed. `_recent_at` lets the brief line say how stale the cache is
# rather than silently reusing yesterday's read as though it were fresh
# (invariant 6, in spirit).
_recent_lines: list[str] = []
_recent_at: datetime | None = None


# ------------------------------------------------------------- which repos

# `repo` is a parameter a model fills -- `diagnose` can pick nightshift.run
# with nobody attached, from a prompt that quotes third-party text -- and
# the container presents NIGHTSHIFT_GIT_TOKEN to whatever host it names.
# So a repo runs only when it is on this list, and what is cloned is a URL
# rebuilt from the list entry, never the model's own string (so a URL
# parser and git cannot disagree about which host it is).
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def allowed_repos() -> dict[str, str]:
    """NIGHTSHIFT_REPOS, comma-separated `host/owner/name`, as
    {lowercased key: the entry as written}. Unset or empty allows nothing."""
    out: dict[str, str] = {}
    for raw in os.getenv("NIGHTSHIFT_REPOS", "").split(","):
        entry = raw.strip().strip("/").removesuffix(".git")
        parts = entry.split("/")
        if is_atlas(entry):
            # The night shift never gets ATLAS's own code: not to clone,
            # not to push, not to merge (the same rule as
            # `kernel.improve.nightshift_repo`).
            continue
        if len(parts) >= 3 and all(_SEGMENT.match(p) for p in parts):
            out[entry.lower()] = entry
    assert not any(is_atlas(k) for k in out), "NIGHTSHIFT_REPOS includes atlas"
    return out


def is_atlas(entry: str) -> bool:
    return "atlas" in (entry or "").lower()


def repo_url(repo: str) -> tuple[str | None, str]:
    """The https URL to clone for `repo`, or (None, why it is refused).

    https only; no user, password or port; no query or fragment; no dot
    segments. The host is compared as a hostname, not a netloc. Checked
    before anything is recorded and before the token goes anywhere.
    """
    if is_atlas(repo):
        return None, "ATLAS's own repository is never given to the night shift"
    allowed = allowed_repos()
    if not allowed:
        return None, ("NIGHTSHIFT_REPOS is unset, so no repository may be "
                      "given the git token")
    text = (repo or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None, "not a URL"
    if parts.scheme != "https":
        return None, "only an https repository URL is accepted"
    if "@" in parts.netloc or parts.username or parts.password:
        return None, "a repository URL may not carry a user or password"
    if port is not None or ":" in parts.netloc:
        return None, "a repository URL may not name a port"
    if parts.query or parts.fragment or "\\" in text:
        return None, "a repository URL may not carry a query or fragment"
    host = (parts.hostname or "").lower()
    segments = parts.path.strip("/").removesuffix(".git").split("/")
    if not host or len(segments) < 2 or not all(_SEGMENT.match(s) for s in segments):
        return None, "expected https://host/owner/name"
    key = "/".join([host, *segments]).lower()
    if key not in allowed:
        return None, f"{host}/{'/'.join(segments)} is not in NIGHTSHIFT_REPOS"
    return f"https://{allowed[key]}.git", ""


async def run(task: str, repo: str) -> ActionResult:
    """Tier AUTO: reversible and invisible to anyone outside ATLAS. Starting
    a throwaway container on a scratch branch changes nothing anyone else can
    see until `nightshift.merge` is separately approved -- and the rollback
    deletes the branch it pushed. Only an allow-listed repo (`repo_url`)."""
    url, refused = repo_url(repo)
    if url is None:
        return ActionResult(False, f"refused: {refused}")
    repo = url
    inserted = await fetch_one(
        "INSERT INTO nightshift_jobs (task, repo, branch, status) "
        "VALUES ($1, $2, $3, 'queued') RETURNING id",
        task, repo, "")
    if inserted is None:
        return ActionResult(False, "could not record the job")
    job_id = inserted["id"]
    branch = sandbox.branch_name(job_id)
    await execute_sql(
        "UPDATE nightshift_jobs SET branch = $1 WHERE id = $2", branch, job_id)

    container, reason = await sandbox.start(job_id, task, repo)
    if container is None:
        await execute_sql(
            "UPDATE nightshift_jobs SET status = 'failed', finished_at = $1 "
            "WHERE id = $2", datetime.now(UTC), job_id)
        return ActionResult(False, f"could not start the sandbox: {reason}")

    await execute_sql(
        "UPDATE nightshift_jobs SET status = 'running', container = $1 "
        "WHERE id = $2", container, job_id)
    return ActionResult(
        True, f"nightshift running on branch {branch} of {repo}",
        evidence={"job_id": job_id, "container": container, "branch": branch},
        rollback_token=str(job_id))


async def run_dry(task: str, repo: str) -> ActionResult:
    """Never touches Postgres or docker — the dry run for a Tier 1 action
    must not have a side effect of its own. `"dry-run"` is a placeholder
    purely for the preview text; nothing is recorded, and no real job id
    exists yet to use instead."""
    url, refused = repo_url(repo)
    if url is None:
        return ActionResult(False, f"refused: {refused}")
    branch = sandbox.branch_name("dry-run")
    argv = sandbox.docker_argv("dry-run", task, url, branch)
    return ActionResult(True, f"would run: {redact(' '.join(argv))}")


async def run_rollback(token: str) -> ActionResult:
    """Undo a run: stop the container, then delete the branch it pushed.

    A pushed branch is visible to collaborators and CI, so leaving it was not
    a rollback (invariant 8). The branch is deleted by a second sandbox
    container (`sandbox.delete_branch`), and only nightshift's own
    `nightshift/job-<id>` on an allow-listed repo. If it cannot be deleted
    the rollback FAILS and says so -- the job is still marked stopped,
    because the container is. Never raises: `executor.rollback` does not
    catch, and a crash here would leave no rollback.finish line.
    """
    try:
        job_id = int(token)
        job = await fetch_one(
            "SELECT container, repo, branch FROM nightshift_jobs WHERE id = $1",
            job_id)
        if job is None:
            return ActionResult(False, f"no nightshift job {job_id} to roll back")
        container = job["container"]
        if container:
            await sandbox.stop(container)
        await execute_sql(
            "UPDATE nightshift_jobs SET status = 'stopped', finished_at = $1 "
            "WHERE id = $2", datetime.now(UTC), job_id)

        branch = str(job["branch"] or "")
        if branch != sandbox.branch_name(job_id):
            return ActionResult(
                False, f"stopped job {job_id}, but its branch {branch!r} is not "
                       f"{sandbox.branch_name(job_id)!r}; deleting nothing")
        url, refused = repo_url(str(job["repo"] or ""))
        if url is None:
            return ActionResult(
                False, f"stopped job {job_id}, but could not delete {branch}: "
                       f"{refused}")
        deleted, why = await sandbox.delete_branch(url, branch)
        if not deleted:
            return ActionResult(
                False, f"stopped job {job_id}, but could not delete {branch} "
                       f"from {url}: {why}")
        return ActionResult(True, f"stopped nightshift job {job_id}; {why}")
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return ActionResult(False, f"nightshift rollback failed: {explain(exc)}")


async def research(question: str) -> ActionResult:
    """Tier AUTO: research a question now, as a night-shift job, and file
    the report for the brief."""
    from ..research import report

    question = (question or "").strip()
    if not question:
        return ActionResult(False, "no question to research")
    outcome = await report.run_report(question)
    status = "done" if outcome.ok else "failed"
    row = await fetch_one(
        "INSERT INTO nightshift_jobs (task, repo, branch, status, kind, finished_at) "
        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id",
        question, "research", "", status, "research", datetime.now(UTC))
    job_id = row["id"] if row is not None else None
    if not outcome.ok:
        return ActionResult(False, f"night-shift research failed: {outcome.detail}",
                            evidence={"job_id": job_id})
    return ActionResult(
        True, f"night-shift research: {outcome.detail}",
        evidence={"job_id": job_id, **(outcome.evidence or {})},
        rollback_token=json.dumps({"job": job_id,
                                   "report": outcome.rollback_token}))


async def research_dry(question: str) -> ActionResult:
    """No Postgres, no network — says only which source it would start from,
    and fails when there is none, because the real run would too."""
    if os.getenv("PERPLEXITY_API_KEY"):
        source = "Perplexity deep research, web search as the fallback"
    elif os.getenv("BRAVE_API_KEY"):
        source = "the web search (PERPLEXITY_API_KEY is not set)"
    else:
        return ActionResult(False, f"would research {question!r}, but neither "
                                   "PERPLEXITY_API_KEY nor BRAVE_API_KEY is set "
                                   "— the real run would fail")
    return ActionResult(True, f"would research {question!r} now from {source} "
                              f"and file a report for the brief")


async def research_rollback(token: str) -> ActionResult:
    from ..research import report

    try:
        parsed = json.loads(token)
    except ValueError:
        return ActionResult(False, f"not a night-shift research token: {token!r}")
    notes = []
    if parsed.get("report"):
        withdrawn = await report.withdraw(str(parsed["report"]))
        notes.append(withdrawn.detail)
    if parsed.get("job") is not None:
        await execute_sql(
            "UPDATE nightshift_jobs SET status = 'stopped' WHERE id = $1",
            int(parsed["job"]))
        notes.append(f"job {parsed['job']} stopped")
    return ActionResult(True, "; ".join(notes) or "nothing to roll back")


def _field(row, name: str):
    """`row[name]`, or None when the row has no such column (an asyncpg
    Record and a plain dict both answer `[]`; neither has to have `.get`)."""
    try:
        return row[name]
    except (KeyError, IndexError):
        return None


async def _mergeable(job_id: int) -> tuple[dict | None, str]:
    """The job's clone URL, branch and pinned SHA, or (None, why not)."""
    job = await fetch_one(
        "SELECT repo, branch, status, kind, head_sha FROM nightshift_jobs "
        "WHERE id = $1", job_id)
    if job is None:
        return None, f"no nightshift job {job_id}"
    if (_field(job, "kind") or "code") != "code":
        return None, f"job {job_id} is a research job; there is no branch to merge"
    status = _field(job, "status")
    if status is not None and status != "done":
        return None, f"job {job_id} is {status}, not done"
    branch = str(_field(job, "branch") or "")
    if branch != sandbox.branch_name(job_id):
        return None, (f"job {job_id}'s branch {branch!r} is not "
                      f"{sandbox.branch_name(job_id)!r}")
    url, refused = repo_url(str(_field(job, "repo") or ""))
    if url is None:
        return None, refused
    sha = str(_field(job, "head_sha") or "").strip().lower()
    if not sha:
        return None, (f"job {job_id} has no pinned commit: its branch head was "
                      f"not read when it finished, so there is nothing approved "
                      f"to merge")
    return {"repo": url, "branch": branch, "sha": sha}, ""


async def merge(job_id: int) -> ActionResult:
    """Tier ASK, irreversible: merge the job's merge request, only at the
    commit pinned when the job finished (`head_sha`), only when its pipeline
    passed for that commit, and only once (`gitlab.merge_branch`)."""
    job, why = await _mergeable(int(job_id))
    if job is None:
        return ActionResult(False, f"refused: {why}")
    result = await gitlab.merge_branch(job["repo"], job["branch"], job["sha"])
    evidence = {"job_id": int(job_id), **(result.evidence or {})}
    return ActionResult(result.ok, result.detail, evidence=evidence)


async def merge_dry(job_id: int) -> ActionResult:
    """Reads Postgres only. Names the pinned commit, so the card Miro
    approves says exactly what will be merged."""
    job, why = await _mergeable(int(job_id))
    if job is None:
        return ActionResult(False, f"would not merge: {why}")
    return ActionResult(True, f"would merge {job['branch']} into {job['repo']} "
                              f"at {job['sha'][:12]} (the commit the job "
                              f"finished on), only if its pipeline passed",
                        evidence={"job_id": int(job_id), "sha": job["sha"]})


# How long after a job finished its pin may still be read, when the read at
# finish time failed (a GitLab blip, a token missing Repository: Read). Kept
# short: a later read could capture a push made after the job finished. The
# card names whatever was pinned, so Miro approves exactly that commit.
PIN_RETRY_WINDOW = timedelta(hours=2)


async def _pin_head(job_id: int, repo: str, branch: str) -> bool:
    """Record the commit a finished job's branch points at. Read from
    GitLab, never from the container's logs (they carry model output and
    could name any SHA). Written once: a pin is never moved. Never raises:
    a failure is logged, and `_retry_pins` tries again for a while."""
    try:
        url, refused = repo_url(repo)
        if url is None:
            log.warning("nightshift job %s: not pinned: %s", job_id, refused)
            return False
        sha = await gitlab.branch_head(url, branch)
        if not sha:
            log.warning("nightshift job %s: GitLab showed no commit on %s",
                        job_id, branch)
            return False
        await execute_sql(
            "UPDATE nightshift_jobs SET head_sha = $1 WHERE id = $2 "
            "AND head_sha IS NULL", sha.lower(), job_id)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("nightshift job %s: could not pin %s: %s", job_id, branch,
                    gitlab._gitlab_reason(exc))
        return False


async def _retry_pins(now: datetime) -> None:
    """Done code jobs that finished within PIN_RETRY_WINDOW with no pin.
    Independent of docker; never raises (a missing column before init-db
    included)."""
    try:
        rows = await fetch_all(
            "SELECT id, repo, branch FROM nightshift_jobs WHERE status = 'done' "
            "AND kind = 'code' AND head_sha IS NULL AND finished_at >= $1",
            now - PIN_RETRY_WINDOW)
    except Exception as exc:  # noqa: BLE001
        log.warning("nightshift: could not look for unpinned jobs: %s", exc)
        return
    for row in rows:
        try:
            job_id, repo, branch = row["id"], row["repo"], row["branch"]
        except (KeyError, IndexError, TypeError):
            continue
        await _pin_head(job_id, repo, branch)


def _write_log_sync(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


async def _record_log(job_id: int, argv: list[str], output: str) -> str:
    """Writes this job's command (redacted — it carries the two nightshift
    secrets) and its captured `docker logs` output to local disk. Local
    filesystem, not a third party: not an outside-world write either."""
    path = LOG_DIR / f"{job_id}.log"
    content = f"$ {redact(' '.join(argv))}\n\n{redact(output)}"
    await asyncio.to_thread(_write_log_sync, path, content)
    return str(path)


async def _finish_job(job_id: int, container: str, task: str, repo: str,
                      branch: str, state: str, now: datetime) -> None:
    """A running job's container is no longer running. Reconcile ATLAS's own
    row: capture the logs while the container still exists to give them up,
    decide done vs. failed from the exit code, and fill in a preview URL
    when one can be built."""
    if state == "missing":
        new_status = "failed"
        output = "(container gone before its outcome could be read)"
    else:
        code_text = state[len("exited("):-1]
        try:
            code = int(code_text)
        except ValueError:
            code = -1
        new_status = "done" if code == 0 else "failed"
        output = await sandbox.logs(container)

    argv = sandbox.docker_argv(job_id, task, repo, branch)
    log_path = await _record_log(job_id, argv, output)

    preview_base = os.environ.get("NIGHTSHIFT_PREVIEW_BASE")
    preview_url = f"{preview_base.rstrip('/')}/{branch}" if preview_base else None

    await execute_sql(
        "UPDATE nightshift_jobs SET status = $1, finished_at = $2, "
        "log_path = $3, preview_url = $4 WHERE id = $5",
        new_status, now, log_path, preview_url, job_id)
    if new_status == "done":
        await _pin_head(job_id, repo, branch)


async def _refresh_recent_lines(now: datetime) -> None:
    """Runs independently of the docker/running-job section below: a docker
    outage must not also leave `brief()` silently serving a stale cache — an
    empty or unreachable read has to say so, not read as "nothing to
    report" (invariant 5, in spirit)."""
    global _recent_at
    try:
        finished_rows = await fetch_all(
            "SELECT task, repo, branch, status, preview_url, kind "
            "FROM nightshift_jobs WHERE status IN ('done', 'failed') "
            "AND finished_at >= $1", now - timedelta(days=1))
    except Exception as exc:  # noqa: BLE001
        _recent_lines[:] = [f"Night shift: could not read jobs — {exc}"]
        _recent_at = now
        return

    lines = []
    for row in finished_rows:
        kind = row.get("kind") if hasattr(row, "get") else None
        if kind == "research":
            lines.append(
                f"Night shift: researched {row['task']!r} — report filed"
                if row["status"] == "done" else
                f"Night shift: research on {row['task']!r} FAILED")
            continue
        if row["status"] == "done":
            lines.append(
                f"Night shift: branch {row['branch']} on {row['repo']} "
                f"— preview {row['preview_url']}")
        else:
            # Never dressed up. A failed overnight run says FAILED, plainly,
            # with nowhere the reasoner could soften it before it reaches
            # Miro.
            lines.append(f"Night shift: {row['task']} FAILED — see log")
    _recent_lines[:] = lines
    _recent_at = now


async def check_jobs() -> Observation:
    """Reads `nightshift_jobs` and asks docker about anything still
    `running`. The only writes are back to a job's own row when its
    container has finished (see `_finish_job`'s docstring for why that is
    not an outside-world write) and the in-process `_recent_lines` cache."""
    now = datetime.now(UTC)
    await _refresh_recent_lines(now)
    await _retry_pins(now)

    try:
        running_rows = await fetch_all(
            "SELECT id, container, at, task, repo, branch "
            "FROM nightshift_jobs WHERE status = 'running'")
    except Exception as exc:  # noqa: BLE001
        return Observation("jobs", Health.UNKNOWN,
                           detail=f"could not read nightshift_jobs: {exc}")

    running = 0
    overdue: list[int] = []
    unsettled: list[str] = []
    for row in running_rows:
        job_id = row["id"]
        container = row["container"]
        started_at = row["at"]
        try:
            state = await sandbox.status(container)
        except sandbox.DockerUnavailable as exc:
            return Observation("jobs", Health.UNKNOWN,
                               detail=f"docker unreachable: {exc}")
        if state == "running":
            running += 1
            if started_at is not None and now - started_at > timedelta(
                    hours=NIGHTSHIFT_MAX_HOURS):
                overdue.append(job_id)
            continue
        if state in sandbox.UNSETTLED:
            # Not running and not finished: leave the job open and say so.
            unsettled.append(f"#{job_id} {state}")
            continue
        await _finish_job(job_id, container, row["task"], row["repo"],
                          row["branch"], state, now)

    if overdue:
        return Observation(
            "jobs", Health.DEGRADED, float(len(overdue)), "jobs",
            f"{len(overdue)} job(s) running past {NIGHTSHIFT_MAX_HOURS}h",
            evidence={"job_ids": overdue})
    if unsettled:
        return Observation(
            "jobs", Health.UNKNOWN, float(running), "jobs",
            f"{running} job(s) running; not running and not finished: "
            f"{', '.join(unsettled[:6])}",
            evidence={"unsettled": unsettled})
    return Observation("jobs", Health.OK, float(running), "jobs",
                       f"{running} job(s) running")


def brief() -> str:
    if not _recent_lines:
        return ""
    stamp = _recent_at.astimezone().strftime("%H:%M") if _recent_at else "?"
    return f"{'; '.join(_recent_lines)} (as of {stamp})"


class NightshiftModule:
    name = "nightshift"
    title = "Nightshift (overnight sandbox)"
    requires_env = ()
    optional_env = ("NIGHTSHIFT_ANTHROPIC_KEY", "NIGHTSHIFT_GIT_TOKEN")
    setup_note = ("no NIGHTSHIFT_ANTHROPIC_KEY / NIGHTSHIFT_GIT_TOKEN set — "
                  "nightshift.run will start containers that can neither call "
                  "Claude nor push a branch. Also needs the `nightshift` "
                  "docker network and the atlas-nightshift image; see "
                  "bootstrap/nightshift/README.md")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("jobs", check_jobs, timedelta(minutes=15), Severity.NORMAL,
                  "overnight sandbox jobs still running or stuck",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="nightshift.run",
                tier=Tier.AUTO,
                summary="Hand a coding task to the overnight sandbox on a "
                       "scratch branch",
                run=run, dry_run=run_dry, rollback=run_rollback,
                params={"task": "what to do, in plain English",
                        "repo": "https URL of a repository listed in "
                                "NIGHTSHIFT_REPOS; anything else is refused"},
                max_per_hour=4,
            ),
            Action(
                name="nightshift.research",
                tier=Tier.AUTO,
                summary="Research a question now as a night-shift job "
                       "(Perplexity deep research, web search as the "
                       "fallback) and file a report for the brief",
                run=research, dry_run=research_dry, rollback=research_rollback,
                params={"question": "the question to research — a topic about "
                                    "the world, not a document"},
                max_per_hour=4,
            ),
            Action(
                name="nightshift.merge",
                tier=Tier.ASK,
                summary="Merge a finished overnight branch — cannot be "
                       "undone by anything this module offers",
                run=merge, dry_run=merge_dry, rollback=None,
                irreversible=True,
                params={"job_id": "the nightshift_jobs.id to merge"},
                max_per_hour=6,
            ),
        )

    def brief(self) -> str:
        return brief()


MODULE = NightshiftModule()
