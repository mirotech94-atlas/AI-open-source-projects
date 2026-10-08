"""Mapfront hosting: client sites published on this box at `<slug>.mapfront.io`.

`agency.deploy_site` (Tier ASK) has two targets. With `AGENCY_DEPLOY_HOST`
set to a host it rsyncs over ssh as before. Unset, or `local`, it publishes
here, into `PUBLISH_ROOT` -- `/app/data/sites` in the api container, the
host's `./data/app/sites`, mounted READ-ONLY into Caddy at `/srv/sites`,
where the `*.mapfront.io` block serves `/srv/sites/{labels.2}`.

Layout under the root, one release per deploy::

    .releases/<slug>/<UTC stamp>/     the files, copied, never linked
    <slug> -> .releases/<slug>/<stamp> the live pointer, a RELATIVE symlink

The link is relative because the api and Caddy mount the same directory at
different paths: an absolute target would be broken in one of them. A
deploy copies into a hidden staging name, renames it into place, then
swaps the pointer with `os.replace` over a temporary link, so a visitor
sees the old release or the new one and never half of each.

Three things are refused rather than trusted:

* a slug outside `SLUG_RE` or in `RESERVED` -- the slug becomes a public
  hostname, and `www`, `mail`, `_dmarc` and friends belong to the domain;
* a symlink (or anything but a regular file or directory) in the built
  tree. Caddy follows symlinks, and its own `/data` holds the ACME
  account and certificate keys: a built "file" that pointed there would
  serve them;
* a publish root that is, contains, or sits inside the BUILD directory
  (`build.SITES_DIR`). Builds are Tier AUTO; if Caddy served that tree,
  every unapproved build, variant and rollback copy would be public with
  no card in between.

`tls_allowed` is the question Caddy's on-demand TLS asks before it mints a
certificate (`/internal/tls-ask`, atlas/api/tls_ask.py): the apex, `www`,
or exactly one label that is a published slug. Nothing else.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

PUBLISH_ROOT = Path(os.getenv("AGENCY_PUBLISH_DIR", "data/sites"))
RELEASES = ".releases"
KEEP_RELEASES = 5

# One DNS label, 1-40 characters, no leading or trailing hyphen (DNS and
# Let's Encrypt both refuse those), lowercase only.
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")
STAMP_RE = re.compile(r"^\d{8}T\d{12}Z$")

# Labels the domain itself uses, or that a mail client, a registrar's email
# product or a person would expect to be something other than a client's
# site. Porkbun Email's own host labels are not verified here: if its setup
# page asks for a record on a label not listed, add that label.
RESERVED = frozenset({
    "www", "mail", "api", "admin", "app", "atlas", "smtp", "imap", "pop", "pop3",
    "mx", "webmail", "email", "autodiscover", "autoconfig", "ftp", "ns", "ns1",
    "ns2", "dns", "static", "cdn", "assets", "status", "hello", "support",
    "billing", "_dmarc", "_domainkey", "_mta-sts", "mta-sts", "localhost",
})


def sites_domain() -> str:
    """`AGENCY_SITES_DOMAIN`, lowercased, no trailing dot. The Caddyfile
    hardcodes the same name (an env var in a directive that expands empty
    takes the whole listener down), so the default must agree with it."""
    raw = os.getenv("AGENCY_SITES_DOMAIN", "").strip().lower().strip(".")
    return raw or "mapfront.io"


def local_mode() -> bool:
    host = os.getenv("AGENCY_DEPLOY_HOST", "").strip()
    return not host or host.lower() == "local"


def slug_problem(slug: object) -> str | None:
    """Why `slug` cannot be a public subdomain, or None when it can."""
    if not isinstance(slug, str) or not slug:
        return "the slug is empty"
    if not SLUG_RE.fullmatch(slug):
        return (f"slug {slug[:60]!r} is not a hostname label: lowercase letters, "
                "digits and inner hyphens only, 1-40 characters")
    if slug[2:4] == "--":
        return f"slug {slug!r} has '--' in positions 3-4, which DNS reads as IDN"
    if slug in RESERVED:
        return f"slug {slug!r} is reserved for the domain itself"
    return None


def public_host(slug: str) -> str:
    return f"{slug}.{sites_domain()}"


def public_url(slug: str) -> str:
    return f"https://{public_host(slug)}/"


def root_problem(root: Path, build_dir: Path) -> str | None:
    """None when the publish root is safely apart from the build tree."""
    r, b = root.resolve(), build_dir.resolve()
    if r == b or r.is_relative_to(b) or b.is_relative_to(r):
        return (f"publish root {root} overlaps the build directory {build_dir}; "
                "Caddy would serve unapproved builds")
    return None


# ------------------------------------------------------------ publishing
# Everything below is blocking filesystem work: callers run it through
# `asyncio.to_thread`.

def _copy_tree(src: Path, dest: Path) -> None:
    """Regular files and directories only. Raises ValueError on anything
    else -- a symlink is refused, never followed and never recreated."""
    dest.mkdir(parents=True)
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        here = Path(dirpath)
        target = dest / here.relative_to(src)
        for name in list(dirnames) + filenames:
            path = here / name
            if path.is_symlink():
                raise ValueError(f"the build contains a symlink: {path.relative_to(src)}")
        for name in dirnames:
            (target / name).mkdir()
        for name in filenames:
            path = here / name
            if not path.is_file():
                raise ValueError(f"not a regular file: {path.relative_to(src)}")
            shutil.copyfile(path, target / name)
            os.chmod(target / name, 0o644)
    for dirpath, _dirs, _files in os.walk(dest):
        os.chmod(dirpath, 0o755)


def release_dir(root: Path, slug: str, stamp: str) -> Path:
    return root / RELEASES / slug / stamp


def live_release(root: Path, slug: str) -> str | None:
    """The stamp `<root>/<slug>` points at, or None when nothing is live.
    Raises ValueError when the slug's path is something this module did
    not make (a real directory, a link elsewhere)."""
    link = root / slug
    if not link.is_symlink():
        if link.exists():
            raise ValueError(f"{link} exists and is not a release link")
        return None
    target = os.readlink(link)
    prefix = f"{RELEASES}/{slug}/"
    stamp = target[len(prefix):] if target.startswith(prefix) else ""
    if not STAMP_RE.fullmatch(stamp):
        raise ValueError(f"{link} points at {target!r}, not one of its releases")
    return stamp


def stage_release(src: Path, root: Path, slug: str, stamp: str,
                  provenance: Path | None = None) -> Path:
    """Copy the build into its release directory (not live yet). The build's
    launch provenance, when there is one, goes BESIDE the release under the
    name `launchcheck.provenance_path` looks for, so the launch check can be
    re-run on exactly the bytes that will go live."""
    parent = root / RELEASES / slug
    parent.mkdir(parents=True, exist_ok=True)
    final = parent / stamp
    staging = parent / f".staging-{stamp}"
    if final.exists() or staging.exists():
        raise ValueError(f"release {stamp} already exists for {slug}")
    try:
        _copy_tree(src, staging)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    staging.rename(final)
    if provenance is not None and provenance.is_file():
        shutil.copyfile(provenance, parent / f".{stamp}.launch.json")
    return final


def discard_release(root: Path, slug: str, stamp: str) -> None:
    shutil.rmtree(release_dir(root, slug, stamp), ignore_errors=True)
    (root / RELEASES / slug / f".{stamp}.launch.json").unlink(missing_ok=True)


def point_at(root: Path, slug: str, stamp: str | None) -> None:
    """Make `<root>/<slug>` point at `stamp` atomically, or remove it."""
    link = root / slug
    if stamp is None:
        if link.is_symlink():
            link.unlink()
        return
    if not release_dir(root, slug, stamp).is_dir():
        raise ValueError(f"release {stamp} of {slug} is not on disk")
    tmp = root / f".{slug}.link-{stamp}"
    tmp.unlink(missing_ok=True)
    os.symlink(f"{RELEASES}/{slug}/{stamp}", tmp)
    os.replace(tmp, link)


def prune_releases(root: Path, slug: str, keep: set[str]) -> None:
    """Drop all but the newest KEEP_RELEASES releases, never one in `keep`."""
    parent = root / RELEASES / slug
    if not parent.is_dir():
        return
    stamps = sorted((p.name for p in parent.iterdir()
                     if p.is_dir() and STAMP_RE.fullmatch(p.name)), reverse=True)
    for stamp in stamps[KEEP_RELEASES:]:
        if stamp not in keep:
            discard_release(root, slug, stamp)


# ---------------------------------------------------------------- tls ask

def tls_allowed(domain: str, root: Path | None = None) -> bool:
    """Whether Caddy may obtain a certificate for `domain`. Blocking (a stat)."""
    name = (domain or "").strip().lower().rstrip(".")
    apex = sites_domain()
    if name in (apex, f"www.{apex}"):
        return True
    suffix = f".{apex}"
    if not name.endswith(suffix):
        return False
    label = name[: -len(suffix)]
    if slug_problem(label) is not None:
        return False
    base = PUBLISH_ROOT if root is None else root
    try:
        return live_release(base, label) is not None and (base / label).is_dir()
    except (ValueError, OSError):
        return False
