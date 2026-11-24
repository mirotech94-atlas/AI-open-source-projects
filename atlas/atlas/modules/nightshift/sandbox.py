"""Process construction and container control for the overnight sandbox.

Every docker invocation is built as an argv list and handed straight to
`asyncio.create_subprocess_exec`. Never `shell=True`, never an f-string
handed to a shell: the task text a reasoner chose comes from an LLM and is
untrusted the moment it exists, and it never has to be. As one argv element
it cannot break out into a second command no matter what it contains — there
is no shell here to parse it.

`docker run` is given no bind mount of the repo: the container clones over
the network with its own token, works on `--tmpfs` scratch space, and is
`--read-only` everywhere else. It cannot reach the host filesystem to leak a
secret onto it, and it cannot reach anything but the allow-listed egress the
`nightshift` docker network's proxy permits (see bootstrap/nightshift/README.md
— that network is a host action for the owner, not something this module creates).
"""
from __future__ import annotations

import asyncio
import contextlib
import os

from .._http import _SECRET_HINT, redact

# The only two secrets this module is allowed to hand the container. Anything
# else matching `_SECRET_HINT` in the environment ATLAS runs in — a database
# DSN, a Slack token, anyone else's API key — must never reach `docker_argv`.
_ALLOWED_SECRET_ENV = frozenset({"NIGHTSHIFT_ANTHROPIC_KEY", "NIGHTSHIFT_GIT_TOKEN"})


# Every docker client call is bounded (audit #28). `docker run -d` returns as
# soon as the container exists, and inspect/logs/stop answer in seconds; a
# daemon that accepts the call and never answers must not hang the executor
# or the scheduler's check. The branch delete talks to the git host over the
# network, so it gets longer.
RUN_TIMEOUT = 120.0
QUERY_TIMEOUT = 30.0
STOP_TIMEOUT = 60.0
DELETE_TIMEOUT = 120.0


async def _communicate(proc: asyncio.subprocess.Process,
                       timeout: float) -> tuple[bytes, bytes]:
    """`proc.communicate()` with a deadline. On timeout the child is killed
    and reaped before `TimeoutError` propagates -- never left running."""
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise
    return out or b"", err or b""


class DockerUnavailable(RuntimeError):
    """docker itself could not be reached — no binary, or the daemon is down.

    Distinct from a container that docker can see is simply not there
    (`missing`). Conflating the two would report "docker is fine, that job's
    container just vanished" when the truth is ATLAS cannot ask docker
    anything at all — which is exactly the broken-collector-reads-healthy
    failure `check_jobs` exists to avoid.
    """


def branch_name(job_id: int | str) -> str:
    return f"nightshift/job-{job_id}"


def proxy_url() -> str:
    """`NIGHTSHIFT_PROXY`, default the proxy container on the network."""
    return os.environ.get("NIGHTSHIFT_PROXY", "http://nightshift-proxy:8888").strip()


def _proxy_env() -> list[str]:
    url = proxy_url()
    if not url:
        return []
    out: list[str] = []
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        out += ["-e", f"{name}={url}"]
    return out + ["-e", "NO_PROXY=", "-e", "no_proxy="]


def docker_argv(job_id: int | str, task: str, repo: str, branch: str) -> list[str]:
    """The exact `docker run` invocation for one job.

    No `-v` of anything: the repo is cloned inside the container, never
    bind-mounted from the host, so a compromised task can't read or write
    outside the container's own tmpfs.

    `-d`: detached. The job runs for hours; `start()` needs the CLI
    invocation itself to finish quickly so it can report whether the
    container actually launched, not block for as long as the job runs.
    `job_id` may be a non-numeric placeholder (`"dry-run"`) when this is
    called from a dry run that never records a real job.
    """
    return [
        "docker", "run", "-d", "--rm",
        "--name", f"nightshift-{job_id}",
        "--network", "nightshift",
        "--memory", "4g",
        "--cpus", "2",
        "--read-only",
        "--tmpfs", "/tmp",
        "--tmpfs", "/home/user",
        # By NAME only: docker copies the values from the client's own
        # environment (`start` sets them there), so neither secret is ever
        # on this host's argv or in `docker inspect`'s recorded command.
        "-e", "ANTHROPIC_API_KEY",
        "-e", "GIT_TOKEN",
        # The `nightshift` network is --internal: the only way out is the
        # allow-listing proxy on it (api.anthropic.com, the git host, the
        # package registries). git reads the lower-case names, node/claude
        # the upper-case ones.
        *_proxy_env(),
        "-e", f"REPO={repo}",
        "-e", f"BRANCH={branch}",
        "-e", f"TASK={task}",
        "atlas-nightshift",
    ]


def forbidden_env() -> list[str]:
    """Names of secret-shaped env vars ATLAS must never pass into a container.

    Anything in `os.environ` whose name matches `_http._SECRET_HINT` and is
    not one of the two names this module is explicitly allowed to use. A test
    sets sentinels like `DATABASE_URL` and `SLACK_BOT_TOKEN` and checks that
    none of their *values* ever appear in `docker_argv`'s output.
    """
    return [
        name for name, value in os.environ.items()
        if value and name not in _ALLOWED_SECRET_ENV and _SECRET_HINT.search(name)
    ]


async def start(job_id: int, task: str, repo: str) -> tuple[str | None, str | None]:
    """Launch the sandbox. Returns `(container_name, None)` or `(None, reason)`.

    `docker run -d` returns as soon as the container is created — the job
    itself keeps running for hours afterwards, unattended, but the `docker`
    client process this awaits exits almost immediately either way. Awaiting
    it (rather than firing it and walking away) is what lets a genuine
    launch failure — bad image, missing network — come back as a reason
    instead of a container name nothing ever actually started.
    """
    branch = branch_name(job_id)
    argv = docker_argv(job_id, task, repo, branch)
    env = {k: os.environ[k] for k in _DOCKER_ENV if os.environ.get(k)}
    env["ANTHROPIC_API_KEY"] = os.environ.get("NIGHTSHIFT_ANTHROPIC_KEY", "")
    env["GIT_TOKEN"] = os.environ.get("NIGHTSHIFT_GIT_TOKEN", "")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _out, err = await _communicate(proc, RUN_TIMEOUT)
    except TimeoutError:
        # The daemon may have created the container before it stopped
        # answering. It is not reported as launched, so it must not be left
        # running unattended: stop it (--rm removes it), best-effort.
        await stop(f"nightshift-{job_id}")
        return None, (f"docker run did not return in {RUN_TIMEOUT:.0f}s; "
                      f"nightshift-{job_id} was stopped in case it had started")
    except (FileNotFoundError, OSError) as exc:
        return None, f"could not start docker: {exc}"
    if proc.returncode != 0:
        reason = err.decode(errors="replace").strip() or \
            f"docker exited with status {proc.returncode}"
        return None, redact(reason)
    return f"nightshift-{job_id}", None


# Container states that are neither running nor finished.
UNSETTLED = frozenset({"created", "paused", "restarting"})


async def status(container: str) -> str:
    """`running` | `exited(<code>)` | `missing` | one of `UNSETTLED`.

    Raises `DockerUnavailable` if docker itself could not be asked — the
    caller turns that into `Health.UNKNOWN`, never a guess at the container's
    state.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect",
            "--format", "{{.State.Status}}|{{.State.ExitCode}}",
            container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, OSError) as exc:
        raise DockerUnavailable(str(exc)) from exc
    try:
        out, err = await _communicate(proc, QUERY_TIMEOUT)
    except TimeoutError as exc:
        # Not "missing": docker was not answered, which is UNKNOWN upstream.
        raise DockerUnavailable(
            f"docker inspect did not answer in {QUERY_TIMEOUT:.0f}s") from exc
    if proc.returncode != 0:
        # "No such object" is docker answering that the container is gone
        # (removed, or never valid): "missing". Anything else it says on a
        # failed inspect -- "Cannot connect to the Docker daemon", permission
        # denied -- is docker not being asked, and reading that as "missing"
        # finished every running job as failed and reported "0 job(s)
        # running", OK (RA-E1).
        said = (err or b"").decode(errors="replace").strip()
        if said and "no such" not in said.lower():
            raise DockerUnavailable(redact(said[:200]))
        return "missing"
    state, _, code = out.decode().strip().partition("|")
    if state == "running":
        return "running"
    if state == "exited":
        return f"exited({code or '0'})"
    if state in UNSETTLED:
        # created / paused / restarting: the container exists and has not
        # finished. Never "missing" -- that would close the job.
        return state
    return "missing"


async def logs(container: str) -> str:
    """`docker logs` for a container that has already exited (or vanished).
    Read-only, best-effort: a container that's gone by the time this runs
    yields an explanatory placeholder rather than raising, since the caller
    is trying to record what happened, not assert that it still can."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "logs", container,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await _communicate(proc, QUERY_TIMEOUT)
    except TimeoutError:
        return f"(could not fetch logs: docker did not answer in {QUERY_TIMEOUT:.0f}s)"
    except (FileNotFoundError, OSError) as exc:
        return f"(could not fetch logs: {exc})"
    return out.decode(errors="replace")


# Run inside a second, throwaway container, as `sh -c` with everything it
# needs in its environment: it is ATLAS's text, never the model's. It asks
# whether the branch exists first, so a job that pushed nothing rolls back
# cleanly; ls-remote exits 2 for "no such ref".
_DELETE_BRANCH = r"""set -eu
url="$REPO"
if [ -n "${GIT_TOKEN:-}" ]; then url="https://${GIT_TOKEN}@${REPO#https://}"; fi
cd /tmp && git init -q gone && cd gone
set +e
git ls-remote --exit-code --heads "$url" "refs/heads/$BRANCH" >/dev/null 2>&1
found=$?
set -e
if [ "$found" -eq 2 ]; then echo "absent"; exit 0; fi
if [ "$found" -ne 0 ]; then echo "could not reach the repository" >&2; exit 3; fi
git push -q "$url" --delete "$BRANCH" 2>/dev/null || { echo "push --delete refused" >&2; exit 4; }
echo "deleted"
"""

# The docker client's own environment, and nothing else of ATLAS's.
_DOCKER_ENV = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG", "DOCKER_CONTEXT",
               "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY")


def delete_branch_argv(repo_url: str, branch: str) -> list[str]:
    """The `docker run` that deletes one pushed branch. `-e GIT_TOKEN` with
    no value: docker copies it from the client's environment, so the token
    is never on this host's argv (`ps`, `docker inspect` of the command)."""
    return [
        "docker", "run", "--rm",
        "--network", "nightshift",
        "--memory", "256m",
        "--read-only",
        "--tmpfs", "/tmp",
        "--tmpfs", "/home/user",
        "--entrypoint", "/bin/sh",
        "-e", "GIT_TOKEN",
        "-e", f"REPO={repo_url}",
        "-e", f"BRANCH={branch}",
        "atlas-nightshift",
        "-c", _DELETE_BRANCH,
    ]


async def delete_branch(repo_url: str, branch: str) -> tuple[bool, str]:
    """Delete `branch` from `repo_url`. `(True, what happened)` when it is
    gone -- deleted now, or never pushed -- else `(False, why not)`.

    The caller has already checked `repo_url` against NIGHTSHIFT_REPOS and
    that `branch` is the job's own. Without NIGHTSHIFT_GIT_TOKEN nothing was
    ever pushed (entry.sh pushes only with it), so there is nothing to do.
    """
    token = os.environ.get("NIGHTSHIFT_GIT_TOKEN", "")
    if not token:
        return True, "no NIGHTSHIFT_GIT_TOKEN, so nothing was pushed"
    env = {k: os.environ[k] for k in _DOCKER_ENV if os.environ.get(k)}
    env["GIT_TOKEN"] = token
    try:
        proc = await asyncio.create_subprocess_exec(
            *delete_branch_argv(repo_url, branch),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        out, err = await _communicate(proc, DELETE_TIMEOUT)
    except TimeoutError:
        return False, (f"the delete did not finish in {DELETE_TIMEOUT:.0f}s "
                       "and was killed")
    except (FileNotFoundError, OSError) as exc:
        return False, redact(f"could not start docker: {exc}")
    said = out.decode(errors="replace").strip()
    if proc.returncode == 0 and said.endswith("absent"):
        return True, f"{branch} was never pushed"
    if proc.returncode == 0 and said.endswith("deleted"):
        return True, f"deleted {branch}"
    reason = err.decode(errors="replace").strip() or \
        f"docker exited with status {proc.returncode}"
    return False, redact(reason)[-300:]


async def stop(container: str) -> None:
    """Best-effort stop, used as `nightshift.run`'s rollback. A container that
    is already gone is not an error here — there is nothing left to stop."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "stop", container,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await _communicate(proc, STOP_TIMEOUT)
    except (FileNotFoundError, OSError, TimeoutError):
        return
