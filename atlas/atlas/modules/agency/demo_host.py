"""agency.demo_host -- `agency.deploy_demo`, Tier ASK.

A client demo is a static build made outside ATLAS (Royal HairStyles:
`scripts/build_static_demo.py`, its API mocked in the browser) and dropped
by the operator into `AGENCY_DEMO_INTAKE_DIR/<slug>/`. Nothing here builds,
fetches or executes anything: the intake is audited, copied into a release
directory under `AGENCY_DEMO_PUBLISH_DIR`, audited again on the copy, and a
relative `<slug>` link is swapped onto it -- the release layout `hosting.py`
uses for `/preview/<slug>/`. Caddy serves the publish directory read-only
(`(demo_site)` in the Caddyfile, one block per hostname).

The audit refuses a symlink or a dotfile, a file over `MAX_FILE_BYTES` or a
total over `MAX_TOTAL_BYTES`, a page without a noindex robots meta, an
`index.html` without the "Preview made by ATLAS Agency" banner, any live Stripe
key, a form that posts off-site and a script loaded from another host.

Every real outcome is an `agency_site_events` row and rings the owner's
phone through `escalate.ring_owner` (`CALL_ON`; quiet hours and the call
budget apply, Slack always gets it). A dry run records and rings nothing.
Deletion is Tier 3: taking a demo down is Miro's, by hand
(docs/client-sites.md).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from ...kernel.contracts import ActionResult, Severity, Signal
from .._http import explain
from . import _store, build, escalate, hosting
from .deploy import _stamp

log = logging.getLogger("atlas.agency.demo_host")

INTAKE_ROOT = Path(os.getenv("AGENCY_DEMO_INTAKE_DIR", "data/demos-intake"))
PUBLISH_ROOT = Path(os.getenv("AGENCY_DEMO_PUBLISH_DIR", "data/demos"))
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
CALL_ON = ("deployed", "deploy_failed")
# Miro, 2026-10-30: the brand is ATLAS Agency, and demos go live on Vercel
# only (`agency.publish_vercel`); `agency.deploy_demo` refuses.
BANNER = "Preview made by ATLAS Agency"

_TEXT = frozenset({".html", ".htm", ".js", ".mjs", ".css", ".json", ".txt", ".xml",
                   ".svg", ".map", ".webmanifest"})
_HTML = frozenset({".html", ".htm"})
_LIVE_KEY = re.compile(r"\b(?:sk|pk|rk)_live_")
_META = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_FORM_ACTION = re.compile(
    r"<form\b[^>]*?\baction\s*=\s*[\"']?\s*((?:https?:)?//[^\"'\s>]*)", re.IGNORECASE)
_SCRIPT_SRC = re.compile(
    r"<script\b[^>]*?\bsrc\s*=\s*[\"']?\s*((?:https?:)?//[^\"'\s>]*)", re.IGNORECASE)


# ---------------------------------------------------------------- the audit
# Blocking filesystem work: callers run it through `asyncio.to_thread`.

def _off_site(url: str, hostname: str | None) -> bool:
    host = (urlsplit(url if "//" in url[:8] else f"//{url}").hostname or "").lower()
    return not hostname or host != hostname.lower()


def _noindex(page: str) -> bool:
    for tag in _META.findall(page):
        low = tag.lower()
        if re.search(r"name\s*=\s*[\"']?robots", low) and "noindex" in low:
            return True
    return False


def demo_audit(site_dir: Path, hostname: str | None = None, *,
               require_banner: bool = True,
               allow_external_scripts: bool = False) -> list[str]:
    """Why `site_dir` may not go live as a demo; [] when it may. A script
    or form URL with a host is allowed only when it is `hostname` itself.
    A built lead preview is audited with `require_banner=False` (it carries
    noindex but not the demo banner); an off-site form is refused always."""
    site_dir = Path(site_dir)
    if not site_dir.is_dir() or site_dir.is_symlink():
        return [f"{site_dir} is not a directory"]
    problems: list[str] = []
    total = 0
    texts: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(site_dir, followlinks=False):
        here = Path(dirpath)
        for name in sorted(dirnames + filenames):
            path = here / name
            rel = path.relative_to(site_dir).as_posix()
            if path.is_symlink():
                problems.append(f"symlink refused: {rel}")
                continue
            if name.startswith("."):
                problems.append(f"dotfile refused: {rel}")
                continue
            if name in dirnames:
                continue
            if not path.is_file():
                problems.append(f"not a regular file: {rel}")
                continue
            size = path.stat().st_size
            total += size
            if size > MAX_FILE_BYTES:
                problems.append(f"{rel} is over {MAX_FILE_BYTES // 1048576} MB")
            if path.suffix.lower() in _TEXT:
                texts.append((rel, path))
        # Never descend into what was refused.
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and not (here / d).is_symlink()]
    if total > MAX_TOTAL_BYTES:
        problems.append(f"the total of {total} bytes is over "
                        f"{MAX_TOTAL_BYTES // 1048576} MB")
    if not (site_dir / "index.html").is_file():
        problems.append("index.html is missing")
    for rel, path in texts:
        text = path.read_text(encoding="utf-8", errors="replace")
        if _LIVE_KEY.search(text):
            problems.append(f"a live key (sk_/pk_/rk_live_) in {rel}")
        if path.suffix.lower() not in _HTML:
            continue
        if not _noindex(text):
            problems.append(f'{rel} has no <meta name="robots" content="noindex">')
        if require_banner and rel == "index.html" and BANNER not in text:
            problems.append(f"index.html lacks the banner text '{BANNER}'")
        for url in _FORM_ACTION.findall(text):
            if _off_site(url, hostname):
                problems.append(f"a form in {rel} posts off-site: {url[:80]}")
        for url in _SCRIPT_SRC.findall(text):
            if not allow_external_scripts and _off_site(url, hostname):
                problems.append(f"an external script in {rel}: {url[:80]}")
    return problems


def _fingerprint(site_dir: Path) -> str:
    """Of the intake's names, sizes and times; never follows a link."""
    digest = hashlib.sha256()
    if site_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(site_dir, followlinks=False):
            dirnames.sort()
            for name in sorted(filenames + dirnames):
                path = Path(dirpath) / name
                st = path.lstat()
                rel = path.relative_to(site_dir).as_posix()
                digest.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:12]


async def _fingerprint_async(site_dir: Path) -> str:
    return await asyncio.to_thread(_fingerprint, site_dir)


def _count(site_dir: Path) -> tuple[int, int]:
    files = size = 0
    for dirpath, _dirs, filenames in os.walk(site_dir, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                files += 1
                size += path.stat().st_size
    return files, size


# ------------------------------------------------------------- publishing

def _root_problem() -> str | None:
    """The publish root must be apart from the intake, the built sites'
    publish root and the build tree, or Caddy would serve what was never
    approved (or a demo would land under a client site)."""
    root = PUBLISH_ROOT.resolve()
    for name, other in (("the demo intake", INTAKE_ROOT),
                        ("the client sites' publish root", hosting.PUBLISH_ROOT),
                        ("the build directory", build.SITES_DIR)):
        o = other.resolve()
        if root == o or root.is_relative_to(o) or o.is_relative_to(root):
            return (f"not publishing: the demo publish root {PUBLISH_ROOT} overlaps "
                    f"{name} {other}")
    return None


def _publish(intake: Path, slug: str, stamp: str,
             hostname: str) -> tuple[str | None, list[str]]:
    """Blocking. Stage, audit the copy, then swap the link. On a failed
    audit the staged release is discarded and the live link is untouched."""
    root = PUBLISH_ROOT
    root.mkdir(parents=True, exist_ok=True)
    previous = hosting.live_release(root, slug)
    release = hosting.stage_release(intake, root, slug, stamp)
    problems = demo_audit(release, hostname)
    if problems:
        hosting.discard_release(root, slug, stamp)
        return previous, problems
    hosting.point_at(root, slug, stamp)
    hosting.prune_releases(root, slug, keep={stamp} | ({previous} if previous else set()))
    return previous, []


def _unpublish(slug: str, release: str, previous: str | None) -> str:
    """Blocking. Put back what was live before `release`."""
    root = PUBLISH_ROOT
    live = hosting.live_release(root, slug)
    if live != release:
        raise ValueError(f"release {release} is no longer live for {slug} "
                         f"(live: {live or 'nothing'}); not rolling back over it")
    if previous:
        if not hosting.release_dir(root, slug, previous).is_dir():
            raise ValueError(f"release {previous} of {slug} is no longer on disk; "
                             f"leaving release {release} live")
        hosting.point_at(root, slug, previous)
        return f"restored release {previous} of demo {slug!r}"
    hosting.point_at(root, slug, None)
    return f"unpublished demo {slug!r}; it was the first release"


def hostname_of(site: dict) -> str:
    """The row's own domain, else its Vercel host (Miro 2026-10-30: no
    agency.storefront.test/preview/<slug>/ any more)."""
    if site.get("domain"):
        return str(site["domain"])
    from . import vercel
    slug = str(site.get("slug") or "")
    try:
        return vercel.public_host(slug)
    except ValueError:
        return slug


async def _event(slug: str, event: str, detail: str = "",
                 release: str | None = None) -> None:
    try:
        await _store.record_site_event(slug, event, detail=detail, release=release)
    except Exception as exc:  # noqa: BLE001
        log.warning("demo %s: event %s not recorded: %s", slug, event, explain(exc))


async def _tell(event: str, title: str, line: str) -> None:
    if event not in CALL_ON:
        return
    try:
        await escalate.ring_owner(title, line)
    except Exception as exc:  # noqa: BLE001
        log.warning("demo call not placed: %s", type(exc).__name__)


async def _failed(slug: str, hostname: str, reason: str, fp: str = "") -> ActionResult:
    await _event(slug, "deploy_failed", f"intake {fp}: {reason}" if fp else reason)
    await _tell("deploy_failed", f"Demo deploy failed: {hostname}",
                f"Agency: the {slug} demo for {hostname} did not go live. "
                "Details are in Slack.")
    return ActionResult(False, f"demo {slug} not published: {reason}")


async def _demo_row(slug: str) -> tuple[dict | None, str | None]:
    problem = hosting.slug_problem(slug)
    if problem:
        return None, f"not publishing: {problem}"
    problem = _root_problem()
    if problem:
        return None, problem
    site = await _store.get_site(slug)
    if site is None:
        return None, f"no such site: {slug}"
    if site.get("kind") != "demo":
        return None, (f"{slug} is not a demo (kind {site.get('kind') or 'built'}); "
                      "a built site goes through agency.deploy_site")
    return site, None


async def deploy_demo(slug: str) -> ActionResult:
    from .deploy import VERCEL_ONLY, VERCEL_REFUSAL
    if VERCEL_ONLY:
        return ActionResult(False, VERCEL_REFUSAL)
    site, refusal = await _demo_row(slug)
    if site is None:
        return ActionResult(False, refusal or "refused")
    hostname = hostname_of(site)
    intake = INTAKE_ROOT / slug
    if not intake.is_dir() or intake.is_symlink():
        return await _failed(slug, hostname, f"intake directory not found: {intake}")
    fp = await _fingerprint_async(intake)
    problems = await asyncio.to_thread(demo_audit, intake, hostname)
    if problems:
        return await _failed(slug, hostname, "audit refused: " + "; ".join(problems[:8]),
                             fp)
    stamp = _stamp()
    try:
        previous, problems = await asyncio.to_thread(_publish, intake, slug, stamp,
                                                     hostname)
    except PermissionError as exc:
        return await _failed(slug, hostname, (
            f"publish failed, nothing went live: {exc}. The demo publish root must "
            "be writable by the api (uid 10001): on the Mac, `install -d -o 10001 "
            "-g 10001 ~/atlas-agency/data/app/demos`"), fp)
    except (ValueError, OSError) as exc:
        return await _failed(slug, hostname,
                             f"publish failed, nothing went live: {exc}", fp)
    if problems:
        return await _failed(slug, hostname, "audit of the release copy refused: "
                             + "; ".join(problems[:8]), fp)

    await _store.set_site_deployed(slug, previous)
    url = f"https://{hostname}/"
    await _event(slug, "deployed", f"intake {fp}", release=stamp)
    await _tell("deployed", f"Demo live: {hostname}",
                f"Agency: the {slug} demo is live at {hostname}.")
    token = json.dumps({"mode": "demo", "slug": slug, "release": stamp,
                        "previous": previous})
    return ActionResult(
        True, f"published demo {slug} at {url} (release {stamp})",
        evidence={"slug": slug, "url": url, "release": stamp, "intake": fp},
        rollback_token=token)


async def deploy_demo_dry(slug: str) -> ActionResult:
    """What approving the card would do. Records nothing, rings nothing."""
    from .deploy import VERCEL_ONLY, VERCEL_REFUSAL
    if VERCEL_ONLY:
        return ActionResult(False, VERCEL_REFUSAL)
    site, refusal = await _demo_row(slug)
    if site is None:
        return ActionResult(False, refusal or "refused")
    hostname = hostname_of(site)
    intake = INTAKE_ROOT / slug
    if not intake.is_dir() or intake.is_symlink():
        return ActionResult(False, f"intake directory not found: {intake}")
    problems = await asyncio.to_thread(demo_audit, intake, hostname)
    if problems:
        return ActionResult(False, "audit: refused -- " + "; ".join(problems[:8]))
    files, size = await asyncio.to_thread(_count, intake)
    fp = await _fingerprint_async(intake)
    try:
        live = await asyncio.to_thread(hosting.live_release, PUBLISH_ROOT, slug)
    except ValueError as exc:
        return ActionResult(False, f"the publish directory is not as expected: {exc}")
    now = f"release {live} is live now" if live else "nothing is live: first release"
    return ActionResult(True, (
        f"would publish {files} file(s), {size} bytes (intake {fp}) at "
        f"https://{hostname}/; audit: passed; {now}"))


async def deploy_demo_rollback(token: str) -> ActionResult:
    try:
        data = json.loads(token)
    except (TypeError, ValueError):
        return ActionResult(False, "rollback token is not readable")
    if not isinstance(data, dict) or data.get("mode") != "demo":
        return ActionResult(False, "rollback token is not a demo deploy's")
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
    await _event(slug, "rolled_back", detail, release=previous)
    return ActionResult(True, detail)


# ------------------------------------------------------------------- card

async def signals() -> list[Signal]:
    """A card per demo whose intake changed since its last recorded outcome.
    An intake the audit refuses is reported without an action to approve."""
    if _root_problem():
        return []
    try:
        rows = await _store.list_sites()
    except Exception as exc:  # noqa: BLE001
        log.warning("demo intake: registry unreadable: %s", explain(exc))
        return []
    out: list[Signal] = []
    for row in rows:
        slug = row.get("slug")
        if row.get("kind") != "demo" or hosting.slug_problem(slug):
            continue
        intake = INTAKE_ROOT / str(slug)
        if not intake.is_dir() or intake.is_symlink():
            continue
        try:
            fp = await _fingerprint_async(intake)
            events = await _store.site_events(str(slug), 20)
        except Exception as exc:  # noqa: BLE001
            log.warning("demo intake %s unreadable: %s", slug, explain(exc))
            continue
        seen = next((e for e in events if "intake " in str(e.get("detail") or "")), None)
        if seen is not None and f"intake {fp}" in str(seen.get("detail")):
            continue
        hostname = hostname_of(row)
        problems = await asyncio.to_thread(demo_audit, intake, hostname)
        if problems:
            out.append(Signal(
                title=f"Demo intake for {hostname} refused by the audit (intake {fp})",
                body="Fix the build and drop it again:\n- " + "\n- ".join(problems[:8]),
                severity=Severity.NORMAL))
            continue
        out.append(Signal(
            title=f"Publish demo {hostname} (intake {fp})",
            body=(f"A new build of the {slug} demo is in the intake and passed the "
                  f"audit. Approving publishes it at https://{hostname}/ and rings "
                  "Miro once it is live (or if it fails)."),
            severity=Severity.NORMAL, suggests="agency.deploy_demo",
            params={"slug": str(slug)}))
    return out
