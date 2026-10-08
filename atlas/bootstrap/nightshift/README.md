# nightshift — the overnight sandbox

`nightshift.run` starts a throwaway, `--read-only` container that clones a
branch, lets Claude Code work on a task inside it, and pushes the result as a
scratch branch — a build ATLAS can hand you by morning without ever touching
anything you'd notice from outside. `nightshift.merge` is the separate,
Tier ASK, irreversible step that actually lands that branch, and it only
runs once you've looked.

## What this module can never do for itself

Two things below are **host actions for Miro**, done once, by hand, on the
machine that runs ATLAS. Nothing in `atlas/modules/nightshift/` creates
them, and nothing in ATLAS ever will — a module that could create its own
network and egress rules could also widen them, and that is exactly the kind
of infrastructure change invariant 1 (only `kernel/executor.py` acts, and
only through the closed action catalog) is there to keep out of a module's
hands.

### 1. Build the image

```sh
docker build -t atlas-nightshift -f bootstrap/nightshift/Dockerfile bootstrap/nightshift
```

### 2. Create the network, `--internal`, with an egress allow-list

The sandbox has no business reaching the general internet — a compromised
or simply wrong task should not be able to phone anywhere it likes. Create
an `--internal` docker network (no default route out) and put a forward
proxy on it that allow-lists exactly four destinations:

* `registry.npmjs.org` — installing `@anthropic-ai/claude-code`'s own
  dependencies, if the image is rebuilt inside the network instead of before
  joining it
* `pypi.org` — the same, for Python-based tasks
* `api.anthropic.com` — the only place `claude -p` needs to reach
* the git host named in the job's `REPO` — to clone and to push the branch

```sh
docker network create --internal nightshift
```

The allow-list itself is not a docker network primitive — `--internal` only
removes the default route to the internet. Put a small forward proxy (e.g.
Squid or tinyproxy with an `http_access allow` list of the four hosts above)
on a container attached to both `nightshift` and the outside world, and point
the sandbox's `HTTP_PROXY`/`HTTPS_PROXY` at it via the image or an extra `-e`
in `sandbox.py:docker_argv` once that proxy exists. Until that proxy is
running, `nightshift` should stay `--internal` with **no** route out at all:
a sandbox that cannot reach anything is a safe default; a sandbox that can
reach everything because the allow-list was skipped is not.

## Environment

* `NIGHTSHIFT_ANTHROPIC_KEY` — optional at the ATLAS process level (the
  module loads without it); without it, every container `claude -p` invokes
  will fail authenticating, and that failure is `claude`'s own exit status,
  surfaced by `entry.sh` and then by `nightshift.check_jobs`.
* `NIGHTSHIFT_GIT_TOKEN` — same: optional to load, required for the clone
  and push inside the container to do anything against a private repo.
* `NIGHTSHIFT_REPOS` — comma-separated `host/owner/name` (for example
  `code.example.org/miro/flycheap,aicode.example.com/miro/guardian`). The ONLY
  repositories `nightshift.run` will work on: `repo` is filled by a model,
  and the container hands `NIGHTSHIFT_GIT_TOKEN` to whatever host it names,
  so anything off this list is refused before the token goes anywhere, and
  the URL cloned is rebuilt from the list entry (https only). Unset, every
  run is refused.

Both are read only inside `atlas/modules/nightshift/sandbox.py:docker_argv`,
which passes them into the container as `-e ANTHROPIC_API_KEY=...` and
`-e GIT_TOKEN=...`. No other environment variable ATLAS holds — a database
DSN, a Slack token, another module's key — is ever passed in; see
`sandbox.forbidden_env()` and its test in `tests/test_nightshift.py`.

## What a job looks like end to end

1. A reasoner picks `nightshift.run` with a `task` and a `repo`. Tier AUTO:
   it runs unattended, with a dry run first and a rollback if anything needs
   undoing: the rollback stops the container, marks the job stopped, and
   deletes the pushed `nightshift/job-<id>` branch with a second throwaway
   container (`sandbox.delete_branch`, the token passed by name, never on
   the host's argv). If the branch cannot be deleted the rollback fails and
   says so.
2. `atlas/modules/nightshift/__init__.py:run` records a row in
   `nightshift_jobs`, computes a scratch branch name
   (`nightshift/job-<id>`), and calls `sandbox.start`, which execs
   `docker run` (never a shell string — see `docker_argv`'s docstring) and
   returns immediately; the job runs for however long it runs.
3. Inside the container, `entry.sh` clones `$REPO` with `$GIT_TOKEN`, checks
   out `-b $BRANCH`, runs `claude -p "$TASK" --dangerously-skip-permissions`,
   commits and pushes if anything changed, and exits with `claude`'s own
   status.
4. `nightshift.check_jobs` (a Check, so read-only) polls `docker inspect` for
   jobs still marked `running` in Postgres. A job past
   `NIGHTSHIFT_MAX_HOURS` (8) is `DEGRADED`. If docker itself cannot be
   reached — no binary, daemon down — the check reports `UNKNOWN`, never a
   guess about whatever is happening inside.
5. Once a job is `done`, `nightshift.merge` is the only path to actually
   landing it, and it is Tier ASK and `irreversible=True`: it needs your
   explicit yes, every time, and nothing about `nightshift.run` succeeding
   grants that on its own.

`nightshift.merge` merges only the commit pinned when the job finished.
When `check_jobs` marks a job `done` it reads the branch's head from GitLab
and records it as `nightshift_jobs.head_sha`; the card names that commit.
If that read fails, the next checks retry it for two hours after the job
finished, then stop: a later read could pin a commit pushed afterwards.
A job with no pin cannot be merged, including every job that finished
before the pin existed.
`gitlab.merge_branch` then refuses unless the merge request's head is still
that commit and its pipeline passed for it, hands GitLab the pin (`sha=`) on
the accept call, and reports an MR already merged at the pin as merged
without merging again. If no merge request exists yet it opens one and
refuses until its pipeline passes. The GitLab token for the repo's host
(`GITLAB_TOKEN` or `GITLAB_WORK_TOKEN`) needs merge request write access for
this. ATLAS's own repository is never on `NIGHTSHIFT_REPOS`: an entry naming
atlas is dropped.
