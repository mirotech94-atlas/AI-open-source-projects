"""agency.deploy_site — ship a built site to its production host (AGENCY.md §2.6).

Tier ASK: someone else's production domain, the thing AGENCY.md §3 puts
right below sending outreach. `rsync_argv` is a pure function, the same
discipline `nightshift/sandbox.py::docker_argv` uses for `docker run` — the
exact invocation as an argv list, never a shell string, so a slug or a
path cannot break out of its own arguments no matter what it contains.

`--delete` makes the remote directory an exact mirror of the local one,
which is what "deploy" should mean, but it is also the one flag that can
delete a stranger's files if the destination is ever wrong. Both the
source and destination end in `/`, so rsync always treats `local_dir` as
CONTENTS to place into `<remote_root>/<slug>/`, never as a directory to
place beside it — the destination is never the bare `remote_root`, which
is what would let a wrong build scope `--delete` across every client's
site the host serves.

Rollback restores the previous release: each successful deploy snapshots
what it just made live under `AGENCY_RELEASES_DIR/<slug>/current` before
overwriting it, so `deploy_rollback` has something to rsync back up. The
first deploy for a slug has no such snapshot, and `deploy_rollback` says so
rather than pretending to have restored anything (`audit_action`'s
"nothing was written, nothing to roll back" convention).

**The launch check gates the deploy (2026-09-29).** Before `deploy_dry`
shows the approval card and again before `deploy_site` runs rsync, the
built files are read back with `launchcheck.launch_audit`. Any ISSUE (a
page without a title, an image without alt text, a testimonial nobody
gave, a tracker the privacy page doesn't mention) refuses the deploy by
name; there is no override parameter, because the fix is a rebuild, not a
flag. NEEDS_CLIENT items (reviews, case studies, a team photo, a stated
response time...) are listed on the card and do not block: approving the
card is Miro deciding the site can go live without them. The second check
exists because files can change between the card and the click.

**Local target: Mapfront (2026-09-29).** With `AGENCY_DEPLOY_HOST` unset or
`local`, the site is published on this box instead of rsynced anywhere:
copied into a new release under `hosting.PUBLISH_ROOT` and made live by
swapping one relative symlink, served by Caddy at `https://agency.storefront.test/preview/<slug>/`
(`hosting.py` has the layout and what it refuses). The launch check runs a
third time there, on the release copy, before the swap -- the bytes it
approves are the bytes that go live. Rollback points the link back at the
release that was live before, or removes it when this deploy was the first,
and refuses when a newer deploy has replaced this one since.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from ...kernel.contracts import ActionResult
from .._http import explain
from . import _store, build, hosting
from .launchcheck import LaunchReport, launch_audit, provenance_path

RELEASES_DIR = Path(os.getenv("AGENCY_RELEASES_DIR", "data/agency/releases"))


def _list_local_files(local_dir: str) -> list[str]:
    """Every file under `local_dir`, with its size, sorted -- what a human
    approving this Tier ASK deploy actually sees is about to go out (I-3).
    Before this, `deploy_dry` showed only the rsync command line: the
    directory it names could contain anything `agency.build_site` was
    tricked into copying there, and nothing in the approval path listed
    or checked it. Blocking (`Path.rglob` + `stat`); always run through
    `asyncio.to_thread`."""
    base = Path(local_dir)
    if not base.is_dir():
        return []
    return [f"{p.relative_to(base)} ({p.stat().st_size} bytes)"
           for p in sorted(base.rglob("*")) if p.is_file()]


def rsync_argv(local_dir: str, host: str, remote_root: str, slug: str) -> list[str]:
    """The exact `rsync` invocation for one deploy.

    Trailing `/` on the source makes rsync sync `local_dir`'s CONTENTS into
    the destination rather than `local_dir` itself into a sibling of it;
    trailing `/` on the destination keeps it an explicit directory. The
    destination is always `<remote_root>/<slug>/`, built here rather than
    handed in whole, so `--delete` is provably scoped to one client's
    release directory and can never land on the bare `remote_root`.
    """
    remote_root = remote_root.rstrip("/")
    local = local_dir.rstrip("/") + "/"
    remote = f"{host}:{remote_root}/{slug}/"
    return ["rsync", "-az", "--delete", local, remote]


def _snapshot_current_release(current_release_dir: Path, previous_release: str) -> None:
    """Blocking (`shutil.copytree`); always run through `asyncio.to_thread`
    — "async throughout" (CLAUDE.md) rules out a synchronous filesystem
    call on the event loop just as much as a synchronous network one."""
    shutil.copytree(current_release_dir, previous_release)


def _record_current_release(local_dir: str, current_release_dir: Path) -> None:
    """Blocking; always run through `asyncio.to_thread`."""
    current_release_dir.parent.mkdir(parents=True, exist_ok=True)
    if current_release_dir.exists():
        shutil.rmtree(current_release_dir)
    shutil.copytree(local_dir, current_release_dir)


def _restore_current_release(previous_release: str, current_release_dir: Path) -> None:
    """Blocking; always run through `asyncio.to_thread`."""
    if current_release_dir.exists():
        shutil.rmtree(current_release_dir)
    shutil.copytree(previous_release, current_release_dir)


# How long one rsync may run. A site is a few megabytes; a stalled ssh (a host
# that accepts the connection and then never answers) would otherwise hold the
# executor's run open forever (audit #28).
RSYNC_TIMEOUT = 600.0


async def _run(argv: list[str]) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except Exception as exc:  # noqa: BLE001
        return False, explain(exc)
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=RSYNC_TIMEOUT)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return False, (f"rsync did not finish in {RSYNC_TIMEOUT:.0f}s and was killed; "
                       "the remote directory may be partially updated")
    except Exception as exc:  # noqa: BLE001
        return False, explain(exc)
    if proc.returncode != 0:
        return False, (err or b"").decode(errors="replace")[:300]
    return True, ""


# Miro, 2026-10-30: client demos and previews go live on Vercel only
# (`agency.publish_vercel`, a card). This box no longer serves them at
# agency.storefront.test/preview/<slug>/, whatever AGENCY_DEPLOY_HOST says.
VERCEL_ONLY = True
VERCEL_REFUSAL = ("previews publish on Vercel only, through agency.publish_vercel "
                  "(a card); this box no longer serves <slug> sites")


def _not_built(slug: str, site: dict) -> str | None:
    """Why this row may not go through `agency.deploy_site` (Tier AUTO), on
    any path: only a 'built' row with a build path. A demo is Tier ASK
    (`agency.deploy_demo`); an app is never deployed by ATLAS."""
    kind = site.get("kind") or "built"
    if kind != "built":
        return (f"{slug} is a {kind} row, not a built site: a demo goes through "
                "agency.deploy_demo, an app is never deployed by ATLAS")
    if not str(site.get("path") or "").strip():
        return f"{slug} has no build path on record"
    return None


async def deploy_site(slug: str) -> ActionResult:
    if VERCEL_ONLY:
        return ActionResult(False, VERCEL_REFUSAL)
    local = hosting.local_mode()
    if local:
        problem = _local_problem(slug)
        if problem:
            return ActionResult(False, problem)
    site = await _store.get_site(slug)
    if site is None:
        return ActionResult(False, f"no such site: {slug}")
    refusal = _not_built(slug, site)
    if refusal:
        return ActionResult(False, refusal)
    if local:
        return await _deploy_local(slug, site)

    host = os.getenv("AGENCY_DEPLOY_HOST")
    remote_root = os.getenv("AGENCY_DEPLOY_ROOT")
    if not host or not remote_root:
        return ActionResult(False, "AGENCY_DEPLOY_HOST / AGENCY_DEPLOY_ROOT not set")

    local_dir = site["path"]
    if not Path(local_dir).exists():
        return ActionResult(False, f"built site not found on disk: {local_dir}")
    check = await asyncio.to_thread(launch_audit, Path(local_dir))
    if not check.ready:
        return ActionResult(False, _refusal(slug, check))

    current_release_dir = RELEASES_DIR / slug / "current"
    previous_release: str | None = None
    if current_release_dir.exists():
        previous_release = str(
            RELEASES_DIR / slug / f"before-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
        await asyncio.to_thread(
            _snapshot_current_release, current_release_dir, previous_release)

    argv = rsync_argv(local_dir, host, remote_root, slug)
    ok, reason = await _run(argv)
    if not ok:
        return ActionResult(False, f"rsync failed: {reason}")

    await asyncio.to_thread(_record_current_release, local_dir, current_release_dir)

    await _store.set_site_deployed(slug, previous_release)

    token = json.dumps({"slug": slug, "previous_release": previous_release,
                        "host": host, "remote_root": remote_root})
    return ActionResult(
        True, f"deployed {slug} to {host}:{remote_root.rstrip('/')}/{slug}/",
        evidence={"slug": slug, "host": host}, rollback_token=token)


async def deploy_dry(slug: str) -> ActionResult:
    if VERCEL_ONLY:
        return ActionResult(False, VERCEL_REFUSAL)
    local = hosting.local_mode()
    if local:
        problem = _local_problem(slug)
        if problem:
            return ActionResult(False, problem)
    site = await _store.get_site(slug)
    if site is None:
        return ActionResult(False, f"no such site: {slug}")
    refusal = _not_built(slug, site)
    if refusal:
        return ActionResult(False, refusal)
    host = os.getenv("AGENCY_DEPLOY_HOST")
    remote_root = os.getenv("AGENCY_DEPLOY_ROOT")
    if not local and (not host or not remote_root):
        return ActionResult(False, "AGENCY_DEPLOY_HOST / AGENCY_DEPLOY_ROOT not set")
    check = await asyncio.to_thread(launch_audit, Path(site["path"]))
    if not check.ready:
        return ActionResult(False, _refusal(slug, check))
    files = await asyncio.to_thread(_list_local_files, site["path"])
    listing = "; ".join(files) if files else "(no files found)"
    if local:
        try:
            live = await asyncio.to_thread(
                hosting.live_release, hosting.PUBLISH_ROOT, slug)
        except (ValueError, OSError) as exc:
            return ActionResult(False, f"cannot publish {slug!r}: {exc}")
        target = (f"would publish to {hosting.PUBLISH_ROOT / slug} "
                  f"(a new release, swapped in atomically), served at "
                  f"{hosting.public_url(slug)}; "
                  + (f"replaces release {live}" if live else "first release"))
        detail = f"{check.summary()}\n{target}\nfiles: {listing}"
    else:
        argv = rsync_argv(site["path"], host or "", remote_root or "", slug)
        detail = f"{check.summary()}\nwould run: {' '.join(argv)}\nfiles: {listing}"
    ranking = _variants_line(_store.parse_json(site.get("variants"), None))
    if ranking:
        detail = f"{ranking}\n{detail}"
    return ActionResult(True, detail)


def _refusal(slug: str, check: LaunchReport) -> str:
    return (f"launch check failed for {slug!r}, not deploying: {check.summary()}. "
            "Fix the ISSUEs and rebuild (agency.build_site); the full report is "
            "LAUNCH_CHECK.md beside the site")


def _variants_line(record: object) -> str:
    """Phase 20: a store built as several variants deploys only its primary.
    The approval preview says which one that is and what else was built,
    so the alternates are attached to the decision, not shipped with it --
    they live beside the site directory, so the file listing above never
    includes them."""
    if not isinstance(record, dict) or not record.get("primary"):
        return ""
    rows = record.get("variants") if isinstance(record.get("variants"), list) else []

    def _score(row: dict) -> str:
        score = row.get("score")
        return f" (rubric {score:.2f})" if isinstance(score, (int, float)) else ""

    primary = next((r for r in rows if isinstance(r, dict) and r.get("rank") == 1), {})
    line = f"variants: primary: {record['primary']}{_score(primary)}"
    others = [r for r in rows if isinstance(r, dict) and r.get("rank") not in (None, 1)]
    if others:
        line += "; attached, not deployed: " + ", ".join(
            f"{r.get('angle')}{_score(r)} at {r.get('path')}" for r in others)
    dropped = [r for r in rows if isinstance(r, dict) and r.get("rank") is None]
    if dropped:
        line += "; rejected by the rubric or failed: " + ", ".join(
            str(r.get("angle")) for r in dropped)
    return line


async def deploy_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "no previous release was captured; nothing to restore")
    try:
        data = json.loads(token)
    except ValueError:
        return ActionResult(False, "rollback token is not valid JSON")
    if isinstance(data, dict) and data.get("mode") == "local":
        return await _rollback_local(data)

    previous_release = data.get("previous_release")
    if not previous_release or not Path(previous_release).exists():
        return ActionResult(True, "this was the first deploy; nothing to restore")

    slug, host, remote_root = data["slug"], data["host"], data["remote_root"]
    argv = rsync_argv(previous_release, host, remote_root, slug)
    ok, reason = await _run(argv)
    if not ok:
        return ActionResult(False, f"rollback rsync failed: {reason}")

    current_release_dir = RELEASES_DIR / slug / "current"
    await asyncio.to_thread(_restore_current_release, previous_release, current_release_dir)

    await _store.set_site_deployed(slug, None)
    return ActionResult(True, f"restored the previous release of {slug!r}")


# ------------------------------------------------------------- local target

def _local_problem(slug: str) -> str | None:
    problem = hosting.slug_problem(slug)
    if problem:
        return f"not publishing: {problem}"
    return hosting.root_problem(hosting.PUBLISH_ROOT, build.SITES_DIR)


def _published_here(site: dict) -> bool:
    slug = site.get("slug")
    return bool((hosting.local_mode() or hosting.vercel_mode())
                and site.get("deployed_at") and hosting.slug_problem(slug) is None)


def published_domain(site: dict) -> str | None:
    """Where a site row is live, as a HOST, for the site check: its own
    `domain`, else -- for a site this box published (local target,
    deployed) -- the preview host, `agency.storefront.test`. Never a link for a
    lead: `outreach.preview_url` takes the recorded domain only."""
    if site.get("domain"):
        return str(site["domain"])
    return hosting.public_host(str(site.get("slug"))) if _published_here(site) else None


def published_url(site: dict) -> str | None:
    """The page URL the read-back fetches: `https://<domain>/` for a site
    with its own domain, else the path-based preview URL
    `https://agency.storefront.test/preview/<slug>/` for one this box published."""
    if site.get("domain"):
        return f"https://{str(site['domain']).strip('/')}/"
    return hosting.public_url(str(site.get("slug"))) if _published_here(site) else None


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _publish(local_dir: Path, slug: str, stamp: str) -> tuple[str | None, LaunchReport]:
    """Blocking. Stage the release, re-run the launch check on the copy,
    and only then swap the link. Returns (the stamp that was live before,
    the report); on a failed check the staged release is discarded and the
    live link is untouched."""
    root = hosting.PUBLISH_ROOT
    root.mkdir(parents=True, exist_ok=True)
    previous = hosting.live_release(root, slug)
    release = hosting.stage_release(local_dir, root, slug, stamp,
                                    provenance_path(local_dir))
    check = launch_audit(release)
    if not check.ready:
        hosting.discard_release(root, slug, stamp)
        return previous, check
    # Audited as built; now moved under /preview/<slug>/ before it goes live.
    hosting.prefix_release(release, slug)
    hosting.point_at(root, slug, stamp)
    hosting.prune_releases(root, slug, keep={stamp} | ({previous} if previous else set()))
    return previous, check


async def _deploy_local(slug: str, site: dict) -> ActionResult:
    """`deploy_site` has already checked the slug, the publish root and
    that the row is a built site with a path."""
    local_dir = Path(site["path"])
    if not local_dir.is_dir():
        return ActionResult(False, f"built site not found on disk: {local_dir}")
    check = await asyncio.to_thread(launch_audit, local_dir)
    if not check.ready:
        return ActionResult(False, _refusal(slug, check))

    stamp = _stamp()
    try:
        previous, check = await asyncio.to_thread(_publish, local_dir, slug, stamp)
    except PermissionError as exc:
        return ActionResult(False, (
            f"publish failed, nothing went live: {exc}. The publish root must be "
            "writable by the api (uid 10001): on the Mac, "
            "`install -d -o 10001 -g 10001 ~/atlas-agency/data/app/sites`"))
    except (ValueError, OSError) as exc:
        return ActionResult(False, f"publish failed, nothing went live: {exc}")
    if not check.ready:
        return ActionResult(False, _refusal(slug, check))

    await _store.set_site_deployed(slug, previous)
    url = hosting.public_url(slug)
    token = json.dumps({"mode": "local", "slug": slug, "release": stamp,
                        "previous": previous})
    return ActionResult(
        True, f"published {slug} at {url} (release {stamp})",
        evidence={"slug": slug, "host": "local", "url": url, "release": stamp},
        rollback_token=token)


def _unpublish(slug: str, release: str, previous: str | None) -> str:
    """Blocking. Put back what was live before `release`."""
    root = hosting.PUBLISH_ROOT
    live = hosting.live_release(root, slug)
    if live != release:
        raise ValueError(f"release {release} is no longer live for {slug} "
                         f"(live: {live or 'nothing'}); not rolling back over it")
    if previous:
        if not hosting.release_dir(root, slug, previous).is_dir():
            raise ValueError(f"release {previous} of {slug} is no longer on disk; "
                             f"leaving release {release} live")
        hosting.point_at(root, slug, previous)
        return f"restored release {previous} of {slug!r}"
    hosting.point_at(root, slug, None)
    return f"unpublished {slug!r}; it was the first release"


async def _rollback_local(data: dict) -> ActionResult:
    slug, release, previous = data.get("slug"), data.get("release"), data.get("previous")
    if hosting.slug_problem(slug) or not isinstance(release, str) \
            or not hosting.STAMP_RE.fullmatch(release) \
            or not (previous is None or (isinstance(previous, str)
                                         and hosting.STAMP_RE.fullmatch(previous))):
        return ActionResult(False, "rollback token does not name a valid release")
    try:
        detail = await asyncio.to_thread(_unpublish, slug, release, previous)
    except (ValueError, OSError) as exc:
        return ActionResult(False, f"rollback refused: {exc}")
    if previous:
        await _store.set_site_deployed(slug, None)
    else:
        await _store.set_site_undeployed(slug)
    return ActionResult(True, detail)
