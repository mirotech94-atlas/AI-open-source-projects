"""agency.vercel -- `agency.publish_vercel`, Tier ASK.

A built lead preview (or a demo dropped into the intake) is published as a
static Vercel deployment at `https://atlas-agency-<slug>.vercel.app/`, only after
Miro approves the card. The executor is the only caller of `publish_vercel`;
`publish_vercel_dry` reads and counts, it never uploads.

Before any HTTP call the files are audited (`demo_host.demo_audit`: noindex
on every page, no live key, no off-site form; a built site must also pass
`launchcheck.launch_audit`). After the deployment is READY the live page is
read back: status 200 and the noindex meta, at the host Vercel assigned. Any
doubt fails closed and nothing is recorded as live.

Every call goes through `modules._http` (never raises, redacts tokens). The
team id travels only as a query parameter, never in a detail string.

Rollback promotes the previous production deployment (Vercel's instant
rollback). A first publish has nothing to go back to: deletion is Tier 3,
so the deployment stays at its noindex URL and taking it down is Miro's, by
hand in the Vercel dashboard. Nothing in this module deletes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ...kernel.contracts import ActionResult, Health, Observation
from .. import _http
from . import _store, demo_host, escalate, hosting
from .launchcheck import launch_audit

log = logging.getLogger("atlas.agency.vercel")

API = "https://api.vercel.com"
POLL_EVERY = 3.0
POLL_FOR = 180.0
PAGE_TIMEOUT = 20.0
_DEPLOYMENT_RE = re.compile(r"^dpl_[A-Za-z0-9]{1,64}$")


# ------------------------------------------------------------------ naming

def config_problem() -> str | None:
    for name in ("VERCEL_TOKEN", "VERCEL_TEAM_ID"):
        if not os.getenv(name, "").strip():
            return f"{name} is not set"
    return None


_MAX_NAME = 34


def project_name(slug: str) -> str:
    problem = hosting.slug_problem(slug)
    if problem:
        raise ValueError(problem)
    name = f"atlas-agency-{slug}"
    if len(name) > _MAX_NAME:
        # Vercel shortens longer hosts and then assigns a different alias
        # (lead 70, 2026-10-31): keep the name short, and the trailing id.
        head, _, tail = slug.rpartition("-")
        keep = _MAX_NAME - len("atlas-agency-") - len(tail) - 1
        name = f"atlas-agency-{head[:keep].rstrip('-')}-{tail}"
    return name


def public_host(slug: str) -> str:
    return f"{project_name(slug)}.vercel.app"


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.getenv('VERCEL_TOKEN', '').strip()}"}


def _team() -> dict[str, str]:
    return {"teamId": os.getenv("VERCEL_TEAM_ID", "").strip()}


# --------------------------------------------------------- source and audit

async def _source(slug: str) -> tuple[Path | None, str | None, str | None]:
    """(directory to publish, row kind, refusal)."""
    problem = config_problem()
    if problem:
        return None, None, f"not publishing: {problem}"
    problem = hosting.slug_problem(slug)
    if problem:
        return None, None, f"not publishing: {problem}"
    site = await _store.get_site(slug)
    if site is None:
        return None, None, f"no such site: {slug}"
    kind = site.get("kind") or "built"
    if kind == "demo":
        path = demo_host.INTAKE_ROOT / slug
        if not path.is_dir():
            return None, kind, f"the demo intake {path} is missing"
        return path, kind, None
    if kind != "built":
        return None, kind, f"{slug} is a {kind} row: ATLAS never publishes it"
    raw = str(site.get("path") or "").strip()
    if not raw:
        return None, kind, f"{slug} has no build path on record"
    path = Path(raw)
    if not path.is_dir():
        return None, kind, f"built site not found on disk: {path}"
    return path, kind, None


def _audit(path: Path, kind: str, host: str) -> str | None:
    """Blocking: run through `asyncio.to_thread`."""
    if kind == "built":
        report = launch_audit(path)
        if not report.ready:
            return f"failed the launch check: {report.summary()}"
        problems = demo_host.demo_audit(path, host, require_banner=False)
    else:
        problems = demo_host.demo_audit(path, host)
    if problems:
        more = f" (+{len(problems) - 8} more)" if len(problems) > 8 else ""
        return "audit refused: " + "; ".join(problems[:8]) + more
    return None


def _files(path: Path) -> list[tuple[str, bytes]]:
    """Blocking. Every regular file under an audited tree (the audit has
    already refused links and dotfiles)."""
    out: list[tuple[str, bytes]] = []
    for item in sorted(path.rglob("*")):
        if item.is_file() and not item.is_symlink():
            out.append((item.relative_to(path).as_posix(), item.read_bytes()))
    return out


async def _prepared(slug: str) -> tuple[Any, str | None]:
    path, kind, refusal = await _source(slug)
    if refusal:
        return None, refusal
    assert path is not None and kind is not None
    host = public_host(slug)
    problem = await asyncio.to_thread(_audit, path, kind, host)
    if problem:
        return None, f"{slug} not published: {problem}"
    files = await asyncio.to_thread(_files, path)
    if not files:
        return None, f"{slug} not published: no files under {path}"
    return (host, files), None


# ---------------------------------------------------------------- Vercel API

async def _project(slug: str) -> tuple[dict | None, bool, str | None]:
    """(project, exists, reason). A 404 is a new project, not an error."""
    data, reason = await _http.get_json(
        f"{API}/v9/projects/{project_name(slug)}", headers=_auth(),
        params=_team(), detail=True)
    if reason:
        if reason.startswith("HTTP 404"):
            return None, False, None
        return None, False, f"could not read the project: {reason}"
    return (data if isinstance(data, dict) else {}), True, None


def _production_id(project: dict | None) -> str | None:
    target = ((project or {}).get("targets") or {}).get("production") or {}
    value = target.get("id") if isinstance(target, dict) else None
    return value if isinstance(value, str) and value else None


async def _upload(name: str, body: bytes) -> str | None:
    digest = hashlib.sha1(body).hexdigest()    # Vercel keys files by SHA-1
    headers = {**_auth(), "x-vercel-digest": digest,
               "Content-Type": "application/octet-stream",
               "Content-Length": str(len(body))}
    _data, reason = await _http.post_body(f"{API}/v2/files", headers=headers,
                                          params=_team(), content=body)
    return f"upload of {name} failed: {reason}" if reason else None


async def _wait_ready(deployment: str) -> tuple[dict | None, str | None]:
    deadline = time.monotonic() + POLL_FOR
    state = "unknown"
    while True:
        data, reason = await _http.get_json(
            f"{API}/v13/deployments/{deployment}", headers=_auth(),
            params=_team(), detail=True)
        if reason:
            return None, f"could not read deployment {deployment}: {reason}"
        state = str((data or {}).get("readyState") or "unknown")
        if state in ("ERROR", "CANCELED"):
            return None, f"deployment {deployment} ended {state}"
        if state == "READY" and (data or {}).get("aliasAssigned"):
            return data, None
        if time.monotonic() >= deadline:
            return None, (f"deployment {deployment} not READY after "
                          f"{POLL_FOR:.0f} s (last state {state})")
        await asyncio.sleep(POLL_EVERY)


async def _fetch_page(url: str) -> tuple[int | None, str, str | None]:
    """(status, body, reason). A read; never raises."""
    try:
        async with httpx.AsyncClient(timeout=PAGE_TIMEOUT,
                                     follow_redirects=False) as client:
            response = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        return None, "", _http.explain(exc)
    return response.status_code, response.text[:200_000], None


async def _verify(host: str) -> str | None:
    url = f"https://{host}/"
    status, body, reason = await _fetch_page(url)
    if reason:
        return f"{url} could not be read back: {reason}"
    if status != 200:
        hint = (" (deployment protection is on for the project?)"
                if status in (401, 403) else "")
        return f"{url} answered {status}{hint}"
    if not demo_host._noindex(body):
        return f"{url} is live without the noindex robots meta"
    return None


# ---------------------------------------------------------------- outcomes

async def _event(slug: str, event: str, detail: str = "",
                 release: str | None = None) -> None:
    try:
        await _store.record_site_event(slug, event, detail=detail, release=release)
    except Exception as exc:  # noqa: BLE001
        log.warning("vercel %s: event %s not recorded: %s",
                    slug, event, _http.explain(exc))


async def _tell(event: str, title: str, line: str) -> None:
    if event not in demo_host.CALL_ON:
        return
    try:
        await escalate.ring_owner(title, line)
    except Exception as exc:  # noqa: BLE001
        log.warning("vercel call not placed: %s", type(exc).__name__)


async def _failed(slug: str, host: str, reason: str) -> ActionResult:
    detail = (f"{slug} not published at https://{host}/: {reason}. "
              "Nothing was recorded as live.")
    await _event(slug, "deploy_failed", f"vercel: {reason}")
    await _tell("deploy_failed", f"Preview publish failed: {host}",
                f"Agency: the {slug} preview did not go live. Details are in Slack.")
    return ActionResult(False, detail)


# ------------------------------------------------------------------ actions

async def publish_vercel_dry(slug: str) -> ActionResult:
    ready, refusal = await _prepared(slug)
    if refusal:
        return ActionResult(False, refusal)
    host, files = ready
    project, exists, reason = await _project(slug)
    if reason:
        return ActionResult(False, f"{slug}: {reason}")
    if exists:
        count = len((project or {}).get("latestDeployments") or [])
        line = f"project exists, {count} previous deployment(s)"
    else:
        line = "new project"
    size = sum(len(b) for _n, b in files)
    return ActionResult(
        True, (f"would publish {len(files)} file(s), {size} bytes at "
               f"https://{host}/; audit: passed; {line}"),
        evidence={"slug": slug, "url": f"https://{host}/", "files": len(files),
                  "bytes": size, "project_exists": exists})


async def publish_vercel(slug: str) -> ActionResult:
    ready, refusal = await _prepared(slug)
    if refusal:
        return ActionResult(False, refusal)
    host, files = ready
    name = project_name(slug)

    project, exists, reason = await _project(slug)
    if reason:
        return await _failed(slug, host, reason)
    if not exists:
        _data, reason = await _http.post_json(
            f"{API}/v10/projects", headers=_auth(), params=_team(),
            json_body={"name": name, "framework": None})
        if reason:
            return await _failed(slug, host, f"could not create the project: {reason}")
    previous = _production_id(project)

    manifest = []
    for rel, body in files:
        problem = await _upload(rel, body)
        if problem:
            return await _failed(slug, host, problem)
        manifest.append({"file": rel, "sha": hashlib.sha1(body).hexdigest(),
                         "size": len(body)})

    data, reason = await _http.post_json(
        f"{API}/v13/deployments", headers=_auth(), params=_team(),
        json_body={"name": name, "project": name, "target": "production",
                   "files": manifest, "projectSettings": {"framework": None}})
    deployment = (data or {}).get("id") if isinstance(data, dict) else None
    if reason or not isinstance(deployment, str) or not _DEPLOYMENT_RE.match(deployment):
        return await _failed(slug, host, f"the deployment was not created: "
                                         f"{reason or 'no deployment id'}")

    live, reason = await _wait_ready(deployment)
    if reason:
        return await _failed(slug, host, reason)
    aliases = [str(a) for a in (live or {}).get("alias") or []]
    if host not in aliases:
        return await _failed(slug, host, (
            f"Vercel did not assign {host} to deployment {deployment}; it assigned "
            f"{', '.join(aliases) or 'nothing'}"))
    problem = await _verify(host)
    if problem:
        return await _failed(slug, host, f"deployment {deployment} exists but {problem}")

    await _store.set_site_domain(slug, host)
    await _store.set_site_deployed(slug, previous)
    await _event(slug, "deployed", f"vercel {deployment}", release=deployment)
    await _tell("deployed", f"Preview live: {host}",
                f"Agency: the {slug} preview is live at https://{host}/ (noindex).")
    size = sum(len(b) for _n, b in files)
    token = json.dumps({"mode": "vercel", "slug": slug, "deployment": deployment,
                        "previous": previous})
    return ActionResult(
        True, f"published {slug} at https://{host}/ (deployment {deployment})",
        evidence={"slug": slug, "url": f"https://{host}/", "deployment": deployment,
                  "files": len(files), "bytes": size},
        rollback_token=token)


def _parse_token(token: str) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(token)
    except (TypeError, ValueError):
        return None, "rollback token is not JSON"
    if not isinstance(data, dict) or data.get("mode") != "vercel":
        return None, "not a Vercel publish token"
    slug, deployment, previous = (data.get("slug"), data.get("deployment"),
                                  data.get("previous"))
    if hosting.slug_problem(slug):
        return None, "rollback token has a malformed slug"
    if not isinstance(deployment, str) or not _DEPLOYMENT_RE.match(deployment):
        return None, "rollback token has a malformed deployment id"
    if previous is not None and (not isinstance(previous, str)
                                 or not _DEPLOYMENT_RE.match(previous)):
        return None, "rollback token has a malformed previous deployment id"
    return data, None


async def publish_vercel_rollback(token: str) -> ActionResult:
    data, problem = _parse_token(token)
    if problem:
        return ActionResult(False, problem)
    problem = config_problem()
    if problem:
        return ActionResult(False, f"cannot roll back: {problem}")
    assert data is not None
    slug, deployment, previous = data["slug"], data["deployment"], data["previous"]
    name = project_name(slug)

    project, exists, reason = await _project(slug)
    if reason or not exists:
        return ActionResult(False, f"cannot roll back {slug}: "
                                   f"{reason or 'the project is gone'}")
    current = _production_id(project)
    if current != deployment:
        return ActionResult(False, (
            f"not rolling back {slug}: production is {current or 'nothing'}, "
            f"not {deployment}; a newer publish is live"))

    if previous is None:
        await _store.set_site_undeployed(slug)
        await _store.set_site_domain(slug, None)
        line = ("first deployment stays at its URL (noindex); taking it down is "
                "Miro's, by hand in the Vercel dashboard")
        await _event(slug, "rolled_back", line, release=deployment)
        return ActionResult(True, f"{slug}: {line}")

    _data, reason = await _http.post_body(
        f"{API}/v10/projects/{name}/promote/{previous}", headers=_auth(),
        params=_team())
    if reason:
        return ActionResult(False, f"rollback of {slug} failed: {reason}")
    await _store.set_site_deployed(slug, None)
    await _event(slug, "rolled_back", f"vercel {deployment} -> {previous}",
                 release=previous)
    return ActionResult(True, f"{slug}: production promoted back to {previous}")


# -------------------------------------------------------------------- check

async def check() -> Observation:
    """Can ATLAS authenticate to Vercel? Config missing or the API out of
    reach is UNKNOWN; a refused token is FAILING (it will not heal)."""
    problem = config_problem()
    if problem:
        return Observation("vercel", Health.UNKNOWN,
                           detail=f"Vercel publishing is not configured: {problem}")
    _data, reason = await _http.get_json(f"{API}/v2/user", headers=_auth(),
                                         params=_team())
    if reason is None:
        return Observation("vercel", Health.OK, detail="Vercel accepts the token")
    if reason.startswith(("HTTP 401", "HTTP 403")):
        return Observation("vercel", Health.FAILING,
                           detail=f"Vercel refused the token: {_http.redact(reason)}")
    return Observation("vercel", Health.UNKNOWN,
                       detail=f"Vercel could not be asked: {_http.redact(reason)}")
