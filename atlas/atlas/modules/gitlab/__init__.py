"""GitLab — both self-hosted instances.

the owner runs two:
  aicode.example.com   business repos (flycheap, Guardian, ExampleCo, ATLAS itself)
  git.internal.example        work repos, including the owner/ai behind InRoots

Both are watched the same way. The valuable signals are not "is git up" but the
things that rot quietly: failed pipelines nobody looked at, merge requests that
have been open for weeks, runners that went offline, and — because these are
self-hosted — the instances' own health and version currency.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from .._http import redact

# GitLab's new fine-grained personal access tokens grant NOTHING by default:
# every permission is ticked individually. A token with none set authenticates
# fine and then 403s every call with a body that names the exact permission
# missing. Reading it turns "Client error '403 Forbidden' for url ..." -- which
# sends you to look at the token's existence -- into "tick Project: Read".
GRANULAR = "insufficient_granular_scope"


def _gitlab_reason(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return redact(exc)
    try:
        body = response.json()
    except Exception:                                   # noqa: BLE001
        body = {}
    if isinstance(body, dict):
        if body.get("error") == GRANULAR:
            want = str(body.get("error_description", ""))
            # "...with the following user permissions: [Project: Read]."
            perm = want.split("[", 1)[-1].split("]", 1)[0] if "[" in want else ""
            return ("the token authenticates but grants nothing this needs"
                    + (f" — tick `{perm}` on it" if perm else "")
                    + ". GitLab's fine-grained tokens start with every "
                      "permission off; edit the token and add it.")
        message = body.get("message") or body.get("error_description") \
            or body.get("error")
        if message and not isinstance(message, bool):
            return f"HTTP {response.status_code}: {str(message)[:180]}"
    return f"HTTP {response.status_code}"


@dataclass(frozen=True, slots=True)
class Instance:
    key: str
    url: str
    token_env: str

    @property
    def token(self) -> str:
        return os.getenv(self.token_env, "").strip()


INSTANCES = (
    Instance("business", os.getenv("GITLAB_URL", "https://aicode.example.com"),
             "GITLAB_TOKEN"),
    Instance("work", os.getenv("GITLAB_WORK_URL", "https://git.internal.example"),
             "GITLAB_WORK_TOKEN"),
)


async def _api(inst: Instance, path: str, **params):
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.get(f"{inst.url}/api/v4/{path}",
                        headers={"PRIVATE-TOKEN": inst.token}, params=params)
        r.raise_for_status()
        return r.json()


def _bind(inst: Instance, fn):
    async def _wrapped() -> Observation:
        return await fn(inst)
    return _wrapped


async def instance_up(inst: Instance) -> Observation:
    """`/api/v4/version`, not `/-/readiness`.

    Omnibus restricts the `/-/` monitoring endpoints to an allow-listed set of
    IPs, so from anywhere else they 404 -- and a 404 is indistinguishable from
    the instance being broken. This check reported aicode.example.com as FAILING
    while it was serving fine.

    `/api/v4/version` proves the app is up and routing whatever the token
    says: 401 or 403 means GitLab answered, which is the question being asked
    here. The token's own problems are a different check's business.
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
            r = await c.get(f"{inst.url}/api/v4/version",
                            headers={"PRIVATE-TOKEN": inst.token})
        ms = r.elapsed.total_seconds() * 1000
    except Exception as exc:                            # noqa: BLE001
        return Observation(f"{inst.key}_up", Health.FAILING, None, None,
                           f"unreachable: {redact(exc)}")

    if r.status_code >= 500:
        return Observation(f"{inst.key}_up", Health.FAILING, ms, "ms",
                           f"HTTP {r.status_code} — GitLab is answering but "
                           f"erroring")
    if r.status_code == 200:
        try:
            version = r.json().get("version", "?")
        except Exception:                               # noqa: BLE001
            version = "?"
        return Observation(f"{inst.key}_up", Health.OK, ms, "ms",
                           f"GitLab {version} responding",
                           evidence={"version": version})
    # 401/403: the app is up, the token is the problem. Say so without
    # calling the instance unhealthy.
    return Observation(
        f"{inst.key}_up", Health.OK, ms, "ms",
        f"responding (HTTP {r.status_code} to an unauthorised version read — "
        f"the instance is up; see {inst.key}_token)")


async def admin_locked(inst: Instance) -> Observation:
    """Is the admin account locked out of its own GitLab?

    2026-09-24: 675 sign-in attempts from eleven 77.91.x.x addresses in
    seven hours locked root, and every push and API call from ATLAS then
    read "Your account has been blocked" with nobody having blocked
    anything. A lock is Devise's answer to a brute force, so this is two
    findings in one: he cannot push, and someone is at the door. GitLab
    unlocks after ten minutes on its own; a lock that is still there on the
    next read means the attempts have not stopped.
    """
    name = f"{inst.key}_admin_locked"
    try:
        users = await _api(inst, "users", username="root")
    except Exception as exc:                            # noqa: BLE001
        return Observation(name, Health.UNKNOWN, detail=_gitlab_reason(exc))
    if not isinstance(users, list) or not users or not isinstance(users[0], dict):
        return Observation(name, Health.UNKNOWN, detail="no root user returned")
    u = users[0]
    if "locked" not in u:
        return Observation(name, Health.UNKNOWN,
                           detail="the token is not an admin's, so GitLab does "
                                  "not say whether root is locked")
    if u.get("state") != "active" or u.get("locked"):
        return Observation(
            name, Health.FAILING, 1.0, "locked",
            f"root is {'locked' if u.get('locked') else u.get('state')} on "
            f"{inst.url}: pushes and ATLAS's API calls are refused. A lock "
            f"means failed sign-ins, i.e. someone is brute-forcing the login. "
            f"Unlock on the box: gitlab-rails runner "
            f"'User.find_by(username: \"root\").unlock_access!' — and block "
            f"the source range at the firewall, or it locks again.",
            evidence={"state": u.get("state"), "locked": bool(u.get("locked"))})
    return Observation(name, Health.OK, 0.0, "locked", "root is active, not locked",
                       evidence={"state": u.get("state"), "locked": False})


async def token_ok(inst: Instance) -> Observation:
    """What this token can actually do.

    GitLab's fine-grained tokens start with every permission off, so a token
    that authenticates can still be refused every single call. Without this
    check that state looks like four unrelated UNKNOWNs; with it, it is one
    finding that names the box to tick.
    """
    name = f"{inst.key}_token"
    if not inst.token:
        return Observation(
            name, Health.UNKNOWN,
            detail=f"no {inst.token_env} set. Create one at {inst.url} under "
                   f"Preferences -> Access tokens. The checks only read; "
                   f"nightshift.merge also needs Repository: Read and Merge "
                   f"Request: Write on the repositories it merges.")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{inst.url}/api/v4/projects",
                            headers={"PRIVATE-TOKEN": inst.token},
                            params={"membership": True, "simple": True,
                                    "per_page": 1})
    except Exception as exc:                            # noqa: BLE001
        return Observation(name, Health.UNKNOWN, detail=redact(exc))

    if r.status_code == 200:
        return Observation(name, Health.OK, None, None,
                           f"{inst.token_env} can read projects on {inst.key}")
    if r.status_code in (401, 403):
        exc = httpx.HTTPStatusError("forbidden", request=r.request, response=r)
        return Observation(
            name, Health.DEGRADED, None, None,
            f"{inst.token_env} is rejected on {inst.key}: "
            f"{_gitlab_reason(exc)} ATLAS needs Project: Read at minimum, "
            f"plus Merge Request: Read and Runner: Read for the other checks, "
            f"and Repository: Read + Merge Request: Write for nightshift.merge.")
    return Observation(name, Health.UNKNOWN,
                       detail=f"unexpected HTTP {r.status_code} from "
                              f"/api/v4/projects")


# A job GitLab failed because nothing ever picked it up. The code in it never
# ran, so it says nothing about the code: 2026-09-28, a branch pushed to the
# self-hosted GitLab (no runner registered) read as a "failed pipeline" and
# kept `business` degraded while the same commit passed on gitlab.com.
NO_RUNNER = frozenset({"stuck_pending_no_matching_runners", "no_matching_runner"})


async def failed_pipelines(inst: Instance) -> Observation:
    try:
        projects = await _api(inst, "projects", membership=True, simple=True,
                              per_page=50, order_by="last_activity_at")
        if not isinstance(projects, list) or not projects:
            # Zero visible projects is a token that can see nothing, not a
            # clean CI record: "0 failed" was read off an empty list (RA-E1).
            return Observation(
                f"{inst.key}_failed_pipelines", Health.UNKNOWN,
                detail=f"{inst.token_env} can see no projects on {inst.key}, "
                       f"so no pipeline was read")
        failures: list[str] = []
        never_ran: list[str] = []
        for proj in projects:
            pipes = await _api(inst, f"projects/{proj['id']}/pipelines",
                               status="failed", per_page=3)
            for p in pipes:
                updated = datetime.fromisoformat(
                    p["updated_at"])
                if datetime.now(UTC) - updated >= timedelta(days=3):
                    continue
                ref = f"{proj['path_with_namespace']}#{p['id']}"
                # A later green run on the same branch fixed it; not a failure now.
                if p.get("ref"):
                    latest = await _api(inst, f"projects/{proj['id']}/pipelines",
                                        ref=p["ref"], per_page=1)
                    if (latest and latest[0]["id"] > p["id"]
                            and latest[0].get("status") == "success"):
                        continue
                jobs = await _api(inst, f"projects/{proj['id']}/pipelines/"
                                        f"{p['id']}/jobs", per_page=50)
                failed = [j for j in jobs if j.get("status") == "failed"]
                if failed and all(j.get("failure_reason") in NO_RUNNER
                                  for j in failed):
                    never_ran.append(ref)
                else:
                    failures.append(ref)
        detail = f"{len(failures)} failed in the last 3 days"
        if never_ran:
            detail += (f"; {len(never_ran)} never ran because no runner picks up "
                       f"jobs on {inst.key} (not a code failure)")
        return Observation(
            f"{inst.key}_failed_pipelines",
            Health.DEGRADED if failures else Health.OK,
            float(len(failures)), "pipelines", detail,
            evidence={"pipelines": failures[:10], "never_ran": never_ran[:10]})
    except Exception as exc:                            # noqa: BLE001
        return Observation(f"{inst.key}_failed_pipelines", Health.UNKNOWN,
                           detail=_gitlab_reason(exc))


async def stale_mrs(inst: Instance) -> Observation:
    try:
        mrs = await _api(inst, "merge_requests", scope="all", state="opened",
                         per_page=100)
        cutoff = datetime.now(UTC) - timedelta(days=14)
        stale = [m for m in mrs
                 if datetime.fromisoformat(
                     m["updated_at"]) < cutoff]
        return Observation(
            f"{inst.key}_stale_mrs",
            Health.DEGRADED if len(stale) > 3 else Health.OK,
            float(len(stale)), "MRs",
            f"{len(stale)} merge requests untouched for 14+ days",
            evidence={"titles": [m["title"] for m in stale[:8]]})
    except Exception as exc:                            # noqa: BLE001
        return Observation(f"{inst.key}_stale_mrs", Health.UNKNOWN,
                           detail=_gitlab_reason(exc))


async def runners_online(inst: Instance) -> Observation:
    try:
        runners = await _api(inst, "runners/all", per_page=100)
        if not isinstance(runners, list) or not runners:
            # An empty list read "all 0 runners online" (RA-E1). It is either
            # no runner registered or a token that cannot list them; this
            # check cannot tell which, and neither is "online".
            return Observation(
                f"{inst.key}_runners", Health.UNKNOWN,
                detail=f"no runners visible on {inst.key}: none is registered, "
                       f"or {inst.token_env} cannot list them (runners/all "
                       f"needs an admin token). Not measured")
        offline = [r for r in runners if isinstance(r, dict)
                   and r.get("status") == "offline" and r.get("active")]
        return Observation(
            f"{inst.key}_runners",
            Health.FAILING if offline else Health.OK,
            float(len(runners) - len(offline)), "runners",
            f"{len(offline)} active runner(s) offline" if offline
            else f"all {len(runners)} runners online",
            evidence={"offline": [r["description"] for r in offline]})
    except Exception as exc:                            # noqa: BLE001
        return Observation(f"{inst.key}_runners", Health.UNKNOWN,
                           detail=_gitlab_reason(exc))


async def open_issue(instance: str, project: str, title: str,
                     body: str = "") -> ActionResult:
    """Tier 1: filing an issue is reversible, internal, and the right way for
    ATLAS to leave a durable note about something it noticed."""
    inst = next(i for i in INSTANCES if i.key == instance)
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(
            f"{inst.url}/api/v4/projects/{project.replace('/', '%2F')}/issues",
            headers={"PRIVATE-TOKEN": inst.token},
            json={"title": title, "description": body or "", "labels": "atlas"})
        r.raise_for_status()
        issue = r.json()
    return ActionResult(True, f"opened {issue['web_url']}",
                        rollback_token=f"{instance}|{project}|{issue['iid']}")


# ------------------------------------------------- filing on its own (Tier 1)
#
# the owner, 2026-09-30 and 2026-10-12 (memory approval-policy): "whatever you
# think should be done should be automatically approved". 181 cards in 30
# days, 138 of them expired unanswered, were this action. It is Tier 1 now,
# gated in code here, in the dry run the executor runs before every run:
#
#   * the business instance only. On `work` the watchers are colleagues and
#     an issue emails them the moment it exists; ATLAS never files there.
#   * GITLAB_AUTOFILE (on by default): off files nothing.
#   * no near-duplicate: an atlas-labelled issue in the project, open or
#     closed in the last DEDUPE_DAYS, whose title shares most of its words
#     (the reasoner rewords each time, so an exact match would never fire).
#     A duplicate the rollback closed is not filed again the next cycle.
#   * at most GITLAB_ISSUES_PER_DAY (default 5) atlas issues per project per
#     UTC day.
#
# The rollback closes the issue it opened (closing is not deletion).

DAILY_CAP_DEFAULT = 5
DEDUPE_DAYS = 7
SIMILAR = 0.6
_WORD = re.compile(r"[a-z][a-z0-9_-]{2,}")
_FILLER = frozenset({"the", "and", "for", "with", "from", "not", "has", "was",
                     "are", "its", "this", "that", "atlas", "issue"})


def autofile_enabled() -> bool:
    return os.getenv("GITLAB_AUTOFILE", "on").strip().lower() not in (
        "0", "off", "false", "no")


def daily_cap() -> int:
    try:
        return max(0, int(os.getenv("GITLAB_ISSUES_PER_DAY",
                                    str(DAILY_CAP_DEFAULT))))
    except ValueError:
        return DAILY_CAP_DEFAULT


def title_words(title: str) -> frozenset[str]:
    """The words that say what an issue is about. Pure."""
    return frozenset(w for w in _WORD.findall((title or "").lower())
                     if w not in _FILLER)


def same_issue(a: str, b: str) -> bool:
    """Whether two titles name the same thing: most of their words shared,
    or every word of the shorter one (three or more) in the longer. Pure."""
    wa, wb = title_words(a), title_words(b)
    if not wa or not wb:
        return (a or "").strip().lower() == (b or "").strip().lower()
    shared = len(wa & wb)
    if shared / len(wa | wb) >= SIMILAR:
        return True
    return len(min(wa, wb, key=len)) >= 3 and shared == min(len(wa), len(wb))


async def _atlas_issues(inst: Instance, project: str, **params) -> list[dict]:
    got = await _api(inst, f"projects/{project.replace('/', '%2F')}/issues",
                     labels="atlas", per_page=100, **params)
    return [i for i in (got or []) if isinstance(i, dict)]


async def open_issue_dry(instance: str, project: str, title: str,
                         body: str = "") -> ActionResult:
    if instance not in {i.key for i in INSTANCES}:
        return ActionResult(False, f"unknown instance {instance!r}")
    if instance != "business":
        return ActionResult(False, (
            f"ATLAS does not file issues on {instance!r} by itself: its watchers "
            f"are colleagues, and an issue emails them the moment it exists"))
    if not autofile_enabled():
        return ActionResult(False, "automatic issue filing is off (GITLAB_AUTOFILE)")
    if not (title or "").strip():
        return ActionResult(False, "an issue needs a title")
    # The project has to exist. Every early card named "the owner/ai", a project
    # that was never there, and the 404 surfaced only after approval -- as
    # a failed card, not as a dry run that said so.
    inst = next(i for i in INSTANCES if i.key == instance)
    try:
        return await _autofile_check(inst, instance, project, title)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return ActionResult(False, f"no project {project!r} on {instance}")
        return ActionResult(False, f"GitLab could not be read, so nothing is "
                                   f"filed: {_gitlab_reason(exc)}")
    except httpx.HTTPError as exc:
        # A Tier 1 dry run that raises escapes the executor into the caller's
        # loop; GitLab being unreachable is a refusal, never a crash.
        return ActionResult(False, f"GitLab could not be read, so nothing is "
                                   f"filed: {_gitlab_reason(exc)}")


async def _autofile_check(inst: Instance, instance: str, project: str,
                          title: str) -> ActionResult:
    await _api(inst, f"projects/{project.replace('/', '%2F')}")
    now = datetime.now(UTC)
    recent = await _atlas_issues(inst, project, state="opened")
    recent += await _atlas_issues(
        inst, project, state="closed",
        updated_after=(now - timedelta(days=DEDUPE_DAYS)).isoformat())
    for issue in recent:
        if same_issue(title, str(issue.get("title") or "")):
            return ActionResult(False, (
                f"already filed: #{issue.get('iid')} '{issue.get('title')}' "
                f"({issue.get('state')}) in {project}"),
                evidence={"duplicate_of": issue.get("iid")})
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = await _atlas_issues(inst, project, created_after=midnight.isoformat())
    if len(today) >= daily_cap():
        return ActionResult(False, (
            f"{len(today)} ATLAS issue(s) already filed in {project} today "
            f"(GITLAB_ISSUES_PER_DAY={daily_cap()})"))
    return ActionResult(True, f"would open '{title}' in {project} ({instance})")


async def close_issue(token: str) -> ActionResult:
    instance, project, iid = token.split("|")
    inst = next(i for i in INSTANCES if i.key == instance)
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.put(
            f"{inst.url}/api/v4/projects/{project.replace('/', '%2F')}/issues/{iid}",
            headers={"PRIVATE-TOKEN": inst.token},
            json={"state_event": "close"})
    return ActionResult(r.status_code == 200, f"closed issue {iid}")


# ------------------------------------------------------------ merging

# `nightshift.merge` (Tier ASK, irreversible) is the only caller. What it
# merges is pinned: the SHA the job's branch pointed at when the job
# finished, which is what the card the owner approves names. Anything pushed to
# the branch after that is refused, and GitLab itself is handed the pin
# (`sha=` on the accept call) so a push between the check and the merge
# cannot slip in either. ATLAS's own repository is never merged here.

def project_for(repo_url: str) -> tuple[Instance | None, str, str]:
    """(instance, "namespace/path", why-not) for an https repository URL on
    one of the two instances. Anything else -> (None, "", why)."""
    parts = urlsplit((repo_url or "").strip())
    host = (parts.hostname or "").lower()
    path = parts.path.strip("/").removesuffix(".git")
    if parts.scheme != "https" or not host or path.count("/") < 1:
        return None, "", "not an https repository URL"
    if "atlas" in path.lower():
        return None, "", "ATLAS's own repository is never merged by ATLAS"
    inst = next((i for i in INSTANCES
                 if (urlsplit(i.url).hostname or "").lower() == host), None)
    if inst is None:
        return None, "", f"{host} is not one of ATLAS's GitLab instances"
    return inst, path, ""


async def _call(inst: Instance, method: str, path: str, **kw):
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.request(method, f"{inst.url}/api/v4/{path}",
                            headers={"PRIVATE-TOKEN": inst.token}, **kw)
        r.raise_for_status()
        return r.json()


async def branch_head(repo_url: str, branch: str) -> str | None:
    """The commit a branch points at now (GET only). Raises ValueError when
    the repo is not on an instance ATLAS has a token for, and GitLab's own
    error when the read fails; None when GitLab answers with no commit."""
    inst, project, why = project_for(repo_url)
    if inst is None:
        raise ValueError(why)
    if not inst.token:
        raise ValueError(f"no {inst.token_env} set")
    got = await _api(inst, f"projects/{project.replace('/', '%2F')}/"
                           f"repository/branches/{quote(branch, safe='')}")
    commit = got.get("commit") if isinstance(got, dict) else None
    sha = commit.get("id") if isinstance(commit, dict) else None
    return str(sha) if sha else None


async def merge_requests_for(inst: Instance, project: str, branch: str) -> list[dict]:
    """Every MR (any state) whose source branch is `branch`. GET only."""
    got = await _api(inst, f"projects/{project.replace('/', '%2F')}/merge_requests",
                     source_branch=branch, state="all", per_page=20)
    return [m for m in (got or []) if isinstance(m, dict)]


async def merge_branch(repo_url: str, branch: str, expected_sha: str) -> ActionResult:
    """Merge `branch`'s merge request, only at `expected_sha`, only once.

    Refused when: no pin; the MR's head is not the pin; its head pipeline is
    not `success` for the pin. An MR already merged at the pin is reported
    merged without a second call, so a retry never merges twice. When no MR
    exists yet one is opened against the default branch; its pipeline has
    not passed yet, so that attempt is refused and says to ask again.
    """
    expected_sha = (expected_sha or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        return ActionResult(False, "refused: no commit SHA was pinned for this branch")
    inst, project, why = project_for(repo_url)
    if inst is None:
        return ActionResult(False, f"refused: {why}")
    if not inst.token:
        return ActionResult(False, f"refused: no {inst.token_env} set")
    quoted = project.replace("/", "%2F")
    evidence = {"instance": inst.key, "project": project, "branch": branch,
                "sha": expected_sha}
    try:
        mrs = await merge_requests_for(inst, project, branch)
        merged = [m for m in mrs if m.get("state") == "merged"]
        if merged:
            same = [m for m in merged if str(m.get("sha") or "").lower() == expected_sha]
            if same:
                m = same[0]
                return ActionResult(
                    True, f"!{m.get('iid')} was already merged at "
                          f"{expected_sha[:12]}; nothing was merged again",
                    evidence={**evidence, "iid": m.get("iid"),
                              "merge_commit_sha": m.get("merge_commit_sha"),
                              "web_url": m.get("web_url"), "already": True})
            return ActionResult(
                False, f"refused: {branch} was already merged at another commit "
                       f"({str(merged[0].get('sha'))[:12]}), not the approved "
                       f"{expected_sha[:12]}", evidence=evidence)
        opened = [m for m in mrs if m.get("state") == "opened"]
        closed = [m for m in mrs if m.get("state") == "closed"]
        opened_now = False
        if opened:
            iid = opened[0].get("iid")
        elif closed:
            # Someone closed it: that is a "no", not an invitation to open
            # a fresh one and merge that instead.
            return ActionResult(
                False, f"refused: !{closed[0].get('iid')} for {branch} was closed; "
                       f"reopen it by hand if it should be merged",
                evidence={**evidence, "iid": closed[0].get("iid")})
        else:
            proj = await _api(inst, f"projects/{quoted}")
            target = (proj or {}).get("default_branch") or "main"
            mr = await _call(inst, "POST", f"projects/{quoted}/merge_requests",
                             json={"source_branch": branch, "target_branch": target,
                                   "title": f"nightshift: {branch}",
                                   "remove_source_branch": False})
            iid = mr.get("iid")
            opened_now = True
        evidence["iid"] = iid
        note = f" (opened !{iid} for it just now)" if opened_now else ""
        mr = await _api(inst, f"projects/{quoted}/merge_requests/{iid}")
        head = str(mr.get("sha") or "").lower()
        if head != expected_sha:
            return ActionResult(
                False, f"refused: !{iid}'s head is {head[:12] or 'unknown'}, not the "
                       f"approved {expected_sha[:12]}; something was pushed after "
                       f"the job finished{note}", evidence={**evidence, "head": head})
        pipe = mr.get("head_pipeline") or {}
        status = str(pipe.get("status") or "none")
        if status != "success" or str(pipe.get("sha") or "").lower() != expected_sha:
            return ActionResult(
                False, f"refused: !{iid}'s pipeline for {expected_sha[:12]} is "
                       f"{status}, not passed; ask again once it passes{note}",
                evidence={**evidence, "pipeline": pipe.get("id"),
                          "pipeline_status": status})
        done = await _call(inst, "PUT", f"projects/{quoted}/merge_requests/{iid}/merge",
                           json={"sha": expected_sha,
                                 "should_remove_source_branch": False})
    except httpx.HTTPStatusError as exc:
        return ActionResult(False, f"GitLab refused: {_gitlab_reason(exc)}",
                            evidence=evidence)
    except httpx.HTTPError as exc:
        return ActionResult(False, f"GitLab unreachable: {redact(exc)}",
                            evidence=evidence)
    if not isinstance(done, dict) or done.get("state") != "merged":
        return ActionResult(False, f"GitLab did not report !{iid} merged",
                            evidence=evidence)
    return ActionResult(
        True, f"merged !{iid} ({branch}) into {project} at {expected_sha[:12]}",
        evidence={**evidence, "merge_commit_sha": done.get("merge_commit_sha"),
                  "web_url": done.get("web_url")})


class GitLabModule:
    name = "gitlab"
    title = "GitLab (aicode + git.internal.example)"
    requires_env = ("GITLAB_TOKEN",)     # work instance is optional

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def _active(self):
        return [i for i in INSTANCES if os.getenv(i.token_env)]

    def checks(self):
        out = []
        for inst in self._active():
            out += [
                Check(f"{inst.key}_token", _bind(inst, token_ok),
                      timedelta(hours=6), Severity.HIGH,
                      f"the {inst.key} token can read what ATLAS needs",
                      baseline_sigma=None),
                Check(f"{inst.key}_admin_locked", _bind(inst, admin_locked),
                      timedelta(minutes=15), Severity.HIGH,
                      "the admin account is not locked out (brute force)",
                      baseline_sigma=None),
                Check(f"{inst.key}_up", _bind(inst, instance_up),
                      timedelta(minutes=10), Severity.HIGH,
                      f"{inst.url} reachable"),
                Check(f"{inst.key}_failed_pipelines", _bind(inst, failed_pipelines),
                      timedelta(minutes=30), Severity.NORMAL,
                      "recent failed CI pipelines", baseline_sigma=None),
                Check(f"{inst.key}_stale_mrs", _bind(inst, stale_mrs),
                      timedelta(hours=12), Severity.INFO,
                      "merge requests going stale", baseline_sigma=None),
                Check(f"{inst.key}_runners", _bind(inst, runners_online),
                      timedelta(minutes=15), Severity.HIGH,
                      "CI runners online", baseline_sigma=None),
            ]
        return tuple(out)

    def actions(self):
        return (
            Action(
                name="gitlab.open_issue",
                # Tier 1 again since 2026-10-12 (the owner: "whatever you think
                # should be done should be automatically approved, but
                # whatever is really urgent should only need human
                # approval"; memory approval-policy). It was AUTO, then ASK
                # because an issue emails every watcher -- colleagues on the
                # work instance -- and closing it undoes none of that. So the
                # dry run (open_issue_dry) files on the business instance
                # only, where the owner is the watcher, never on work; never a
                # near-duplicate of an open or recently closed atlas issue;
                # at most GITLAB_ISSUES_PER_DAY per project a day; nothing
                # with GITLAB_AUTOFILE=off. The rollback closes the issue it
                # opened (closing, not deletion: that is Tier 3).
                tier=Tier.AUTO,
                summary="File an issue recording something ATLAS noticed",
                run=open_issue, dry_run=open_issue_dry, rollback=close_issue,
                params={"instance": "'business' (ATLAS never files on 'work', "
                                    "where the watchers are colleagues)",
                        "project": "namespace/path. On 'business' the "
                                   "projects are root/atlas (ATLAS's own "
                                   "notes and findings -- the default), "
                                   "owner/guardian, "
                                   "owner/flycheap-tracker",
                        "title": "issue title", "body": "markdown body"},
                max_per_hour=6,
            ),
        )

    async def signals(self):
        out = []
        for inst in self._active():
            runners = await runners_online(inst)
            if runners.health is Health.FAILING:
                out.append(Signal(
                    title=f"GitLab ({inst.key}): runners offline",
                    body=runners.detail + "\nCI is silently not running.",
                    severity=Severity.HIGH))
        return tuple(out)

    def brief(self) -> str:
        return "GitLab: pipelines, MRs, runners"


MODULE = GitLabModule()
