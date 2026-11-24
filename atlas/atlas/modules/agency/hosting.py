"""Preview hosting: client sites published on this box at
`https://agency.storefront.test/preview/<slug>/` (path-based: no wildcard DNS, no
per-slug certificate; Miro 2026-11-14).

`agency.deploy_site` (Tier AUTO) has two targets. With `AGENCY_DEPLOY_HOST`
set to a host it rsyncs over ssh as before. Unset, or `local`, it publishes
here, into `PUBLISH_ROOT` -- `/app/data/sites` in the api container, the
host's `./data/app/sites`, mounted READ-ONLY into Caddy at `/srv/sites`,
where Caddy's `/preview/*` handle serves `/srv/sites/<slug>`.

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
  path segment (and was a hostname label before), so it stays one safe DNS
  label; the names the domain itself uses stay reserved;
* a symlink (or anything but a regular file or directory) in the built
  tree. Caddy follows symlinks, and its own `/data` holds the ACME
  account and certificate keys: a built "file" that pointed there would
  serve them;
* a publish root that is, contains, or sits inside the BUILD directory
  (`build.SITES_DIR`). Builds are Tier AUTO; if Caddy served that tree,
  every unapproved build, variant and rollback copy would be public with
  no card in between.

Static HTML cannot assume it lives at `/`: `prefix_html` rewrites root-absolute
`href`/`src`/`action`/`srcset`/`url(...)` in the release's `.html` files to
`/preview/<slug>/...` after the launch check passes (other file types are
never touched, and no `<base>` is injected: it would turn every `#anchor`
into a page load).
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


DEFAULT_PREVIEW_ORIGIN = "https://agency.storefront.test"
PREVIEW_PREFIX = "/preview/"


def preview_origin() -> str:
    """`AGENCY_PREVIEW_ORIGIN`: scheme and host of the page that serves the
    previews, `https` only, no path, no credentials. Anything else falls
    back to the default. The Caddyfile hardcodes the same name, so the
    default must agree with it."""
    raw = os.getenv("AGENCY_PREVIEW_ORIGIN", "").strip().lower().rstrip("/")
    if re.fullmatch(r"https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d{1,5})?", raw):
        return raw
    return DEFAULT_PREVIEW_ORIGIN


def slug_of_preview_path(path: str) -> str | None:
    """The slug in `/preview/<slug>/...`, or None when `path` is not one."""
    if not isinstance(path, str) or not path.startswith(PREVIEW_PREFIX):
        return None
    slug = path[len(PREVIEW_PREFIX):].split("/", 1)[0]
    return slug if slug and SLUG_RE.fullmatch(slug) else None


def local_mode() -> bool:
    host = os.getenv("AGENCY_DEPLOY_HOST", "").strip()
    return not host or host.lower() == "local"


def vercel_mode() -> bool:
    """Previews publish on Vercel through `agency.publish_vercel` (a card);
    the box's own deploy refuses."""
    return os.getenv("AGENCY_DEPLOY_HOST", "").strip().lower() == "vercel"


def slug_problem(slug: object) -> str | None:
    """Why `slug` cannot be a public preview path segment, or None when it can."""
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


def public_host(slug: str = "") -> str:
    """The host every preview is served from (the slug is in the path)."""
    return preview_origin().removeprefix("https://")


def public_url(slug: str) -> str:
    return f"{preview_origin()}{PREVIEW_PREFIX}{slug}/"


_ATTR = re.compile(
    rb"""(\b(?:href|src|action|formaction|poster|data-src)\s*=\s*)(["'])/(?!/)""", re.IGNORECASE)
_SRCSET = re.compile(rb"""(\bsrcset\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)
_CSS_URL = re.compile(rb"""(\burl\(\s*)(["']?)/(?!/)""", re.IGNORECASE)


def prefix_html(data: bytes, slug: str) -> bytes:
    """`data` with every root-absolute URL moved under `/preview/<slug>/`.
    Bytes in, bytes out: nothing else changes, and a second pass changes
    nothing. `//host/x`, `https://`, `#x`, `mailto:` and relative links stay."""
    pre = f"{PREVIEW_PREFIX}{slug}/".encode()

    def fix(rx: re.Pattern[bytes], blob: bytes) -> bytes:
        out, last = [], 0
        for m in rx.finditer(blob):
            out.append(blob[last:m.start()])
            tail = blob[m.end():m.end() + len(pre) - 1]
            out.append(m.group(0) if b"/" + tail == pre
                       else m.group(1) + m.group(2) + pre)
            last = m.end()
        out.append(blob[last:])
        return b"".join(out)

    def srcset(m: re.Match[bytes]) -> bytes:
        value = re.sub(rb"(^|,\s*)/(?!/)(?!" + re.escape(pre[1:]) + rb")",
                       lambda n: n.group(1) + pre, m.group(3))
        return m.group(1) + m.group(2) + value + m.group(2)

    return fix(_CSS_URL, fix(_ATTR, _SRCSET.sub(srcset, data)))


def prefix_release(release: Path, slug: str) -> int:
    """Blocking. Rewrite the `.html` files of a staged release in place with
    `prefix_html`; every other file is left alone. Returns how many changed."""
    changed = 0
    for path in release.rglob("*"):
        if path.suffix.lower() in (".html", ".htm") and path.is_file() \
                and not path.is_symlink():
            old = path.read_bytes()
            new = prefix_html(old, slug)
            if new != old:
                path.write_bytes(new)
                changed += 1
    return changed


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
