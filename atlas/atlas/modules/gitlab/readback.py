"""Read-back for `gitlab.open_issue` (RA-D1). Reads only: GET the issue by
the iid the run recorded, and compare its project and title with what was
approved. See docs/verification.md."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    classify,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import INSTANCES, _api


def _instance(key: str):
    return next((i for i in INSTANCES if i.key == key), None)


def _quoted(project: str) -> str:
    return project.replace("/", "%2F")


def _compare(issue: dict[str, Any], project: str, title: str) -> Verification:
    evidence = {"iid": issue.get("iid"), "web_url": issue.get("web_url"),
                "state": issue.get("state"), "title": issue.get("title")}
    web = str(issue.get("web_url") or "")
    # GitLab 19 links an issue as /-/work_items/<iid>; older ones as
    # /-/issues/<iid>. Accepting only the old form contradicted six issues
    # that were opened exactly as approved (2026-09-29).
    if not any(f"/{project}/{kind}/" in web
               for kind in ("-/issues", "issues", "-/work_items")):
        return contradicted(f"issue {issue.get('iid')} is not in {project}",
                            **evidence)
    if str(issue.get("title") or "").strip() != title.strip():
        return contradicted(f"issue {issue.get('iid')} carries a different title",
                            **evidence)
    return verified(**evidence)


@verifier("gitlab.open_issue")
async def verify_open_issue(params: dict[str, Any], result: ActionResult
                            ) -> Verification:
    token = result.rollback_token or ""
    parts = token.split("|")
    if len(parts) != 3:
        return unknown("the run recorded no issue iid")
    instance, project, iid = parts
    inst = _instance(instance)
    if inst is None:
        return unknown(f"unknown instance {instance!r}")
    if project != params.get("project", project):
        return contradicted("the issue went to a different project than approved",
                            project=project, approved=params.get("project"))
    try:
        issue = await _api(inst, f"projects/{_quoted(project)}/issues/{iid}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return contradicted(f"issue {iid} is not in {project}", iid=iid,
                                project=project)
        return unknown(f"could not read issue {iid}", classify(exc))
    if not isinstance(issue, dict):
        return unknown("GitLab answered with an unexpected shape")
    return _compare(issue, project, str(params.get("title") or ""))


@reconciler("gitlab.open_issue")
async def reconcile_open_issue(params: dict[str, Any], started: datetime
                               ) -> Verification:
    inst = _instance(str(params.get("instance") or ""))
    project, title = str(params.get("project") or ""), str(params.get("title") or "")
    if inst is None or not project or not title:
        return unknown("the start row does not name the instance, project and title",
                       FailureClass.VALIDATION)
    try:
        found = await _api(inst, f"projects/{_quoted(project)}/issues",
                           search=title, **{"in": "title"},
                           created_after=started.isoformat(), per_page=20)
    except httpx.HTTPError as exc:
        return unknown("could not search the project's issues", classify(exc))
    exact = [i for i in (found or []) if isinstance(i, dict)
             and str(i.get("title") or "").strip() == title.strip()]
    if not exact:
        return contradicted(f"no issue titled as approved was opened in {project} "
                            "after the run started", project=project)
    if len(exact) > 1:
        return unknown(f"{len(exact)} matching issues; a person should pick",
                       iids=[i.get("iid") for i in exact])
    return _compare(exact[0], project, title)
