"""`academy.publish_shop_remote` (Tier 2): push the built storefront to the
store VM (agency.example.com), 2026-10-27, plan step 4.3.

ATLAS now runs on the owner's Mac, on 127.0.0.1 only. The one public surface is
the storefront: static files on a VM that holds no ATLAS secret, no
database and no token. This is the one-way push, through the executor:

* source: `build_root()/current`, the release `python -m atlas.cli
  shop-build` made (a stamp directory; never the build root itself, so
  `--delete` cannot mirror every old build);
* `ssh <host> mkdir -p <root>/.releases/shop`, then `rsync` (deploy.
  rsync_argv, `--exclude=.env --exclude=data/app`) into the new release
  directory `<root>/.releases/shop/<stamp>/` -- `--delete` scoped to it;
* one `ssh` swaps `<root>/shop` to the release (a relative link, `mv -T`,
  atomic) and prints the release it replaced: the rollback token.

Rollback re-points `<root>/shop` at the previous release, and refuses when
the live link no longer names this one. A first push has nothing to
restore and says so; it removes nothing on the VM.

The dry run spawns nothing (no ssh, no rsync -n against the VM): the target
is a release directory that does not exist yet, so what `rsync -n` would
list is every local file, which the dry run lists itself, with the exact
argv and the stamp. ACADEMY_STORE_HOST / ACADEMY_STORE_ROOT are validated
strictly: ssh joins its remote arguments into a shell line.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
from pathlib import Path

from ...kernel.contracts import ActionResult
from .._http import explain
from ..agency import deploy, hosting
from . import publish

HOST_ENV = "ACADEMY_STORE_HOST"
ROOT_ENV = "ACADEMY_STORE_ROOT"
SLUG = "shop"
EXCLUDES = ("--exclude=.env", "--exclude=.env.*", "--exclude=data/app")
SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=15")
RSYNC_SSH = "ssh -o BatchMode=yes -o ConnectTimeout=15"
TIMEOUT = deploy.RSYNC_TIMEOUT

_HOST_RE = re.compile(r"^(?:[a-z_][a-z0-9_-]{0,31}@)?"
                      r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_ROOT_RE = re.compile(r"^(?:/[A-Za-z0-9_.-]+)+$")


def _target() -> tuple[str, str] | str:
    """(host, root) from the env, or why not."""
    host = os.getenv(HOST_ENV, "").strip()
    root = os.getenv(ROOT_ENV, "").strip().rstrip("/")
    if not host or not root:
        return f"{HOST_ENV} / {ROOT_ENV} not set"
    if not _HOST_RE.fullmatch(host):
        return f"{HOST_ENV} is not a plain [user@]host"
    if not _ROOT_RE.fullmatch(root) or ".." in root.split("/"):
        return f"{ROOT_ENV} must be an absolute path of plain names"
    return host, root


def _release() -> tuple[Path, str] | str:
    """(the build's directory, its stamp) from `build_root()/current`.
    Blocking (filesystem): callers use asyncio.to_thread."""
    current = publish.build_root() / "current"
    if not current.is_symlink():
        return (f"no build at {current}: run `python -m atlas.cli shop-build` "
                "first")
    stamp = os.readlink(current)
    if not hosting.STAMP_RE.fullmatch(stamp):
        return f"{current} does not point at a build stamp ({stamp[:40]!r})"
    src = publish.build_root() / stamp
    if not src.is_dir() or not (src / "index.html").is_file():
        return f"build {stamp} is incomplete (no index.html)"
    for p in src.rglob("*"):
        if p.is_symlink():
            return f"build {stamp} contains a link ({p.relative_to(src)}); not pushing"
    return src, stamp


def push_argv(src: str, host: str, root: str, stamp: str) -> list[str]:
    """The rsync for one release: `deploy.rsync_argv` into
    `<root>/.releases/shop/<stamp>/`, with the secret excludes and a
    non-interactive ssh before the two paths."""
    argv = deploy.rsync_argv(src, host, root, f".releases/{SLUG}/{stamp}")
    return [*argv[:-2], *EXCLUDES, "-e", RSYNC_SSH, *argv[-2:]]


def _ssh(host: str, command: str) -> list[str]:
    return ["ssh", *SSH_OPTS, host, command]


def _mkdir_cmd(root: str) -> str:
    return f"mkdir -p -- {shlex.quote(f'{root}/.releases/{SLUG}')}"


def _swap_cmd(root: str, stamp: str) -> str:
    """Point <root>/shop at the release; print the target it replaced.
    Refuses a <root>/shop that exists and is not a link."""
    r = shlex.quote(root)
    rel = shlex.quote(f".releases/{SLUG}/{stamp}")
    return (f"cd {r} && if [ -e {SLUG} ] && [ ! -L {SLUG} ]; then "
            f"echo '{SLUG} is not a link' >&2; exit 3; fi && "
            f"prev=$(readlink {SLUG} || true) && "
            f"ln -sfn {rel} .{SLUG}.next && mv -Tf .{SLUG}.next {SLUG} && "
            f"printf '%s\\n' \"$prev\"")


def _back_cmd(root: str, release: str, previous: str) -> str:
    r = shlex.quote(root)
    live = shlex.quote(f".releases/{SLUG}/{release}")
    prev = shlex.quote(f".releases/{SLUG}/{previous}")
    return (f"cd {r} && [ \"$(readlink {SLUG})\" = {live} ] || "
            f"{{ echo 'release {release} is no longer live' >&2; exit 3; }}; "
            f"[ -d {prev} ] || {{ echo 'release {previous} is gone' >&2; exit 3; }}; "
            f"ln -sfn {prev} .{SLUG}.next && mv -Tf .{SLUG}.next {SLUG}")


async def _run(argv: list[str]) -> tuple[bool, str]:
    """(ok, stdout) or (False, why). Same timeout discipline as deploy._run,
    keeping stdout (the swap prints the release it replaced)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except Exception as exc:                            # noqa: BLE001
        return False, explain(exc)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return False, f"{argv[0]} did not finish in {TIMEOUT:.0f}s and was killed"
    except Exception as exc:                            # noqa: BLE001
        return False, explain(exc)
    if proc.returncode != 0:
        return False, (err or b"").decode(errors="replace")[:300]
    return True, (out or b"").decode(errors="replace")


def _previous(stdout: str) -> str | None:
    """The stamp the swap replaced, from `readlink` output, or None."""
    name = stdout.strip().rsplit("/", 1)[-1]
    return name if hosting.STAMP_RE.fullmatch(name) else None


async def _ready() -> tuple[str, str, Path, str] | str:
    target = _target()
    if isinstance(target, str):
        return target
    release = await asyncio.to_thread(_release)
    if isinstance(release, str):
        return release
    return (*target, *release)


async def publish_shop_remote_dry() -> ActionResult:
    ready = await _ready()
    if isinstance(ready, str):
        return ActionResult(False, ready)
    host, root, src, stamp = ready
    argv = push_argv(str(src), host, root, stamp)
    files = await asyncio.to_thread(deploy._list_local_files, str(src))
    dry = [argv[0], "-n", *argv[1:]]
    return ActionResult(True, (
        f"would push build {stamp} ({len(files)} files) to "
        f"{host}:{root}/.releases/{SLUG}/{stamp}/ and point {root}/{SLUG} at it\n"
        f"rsync -n would list every file (a new, empty release directory): "
        f"{' '.join(dry)}\nfiles: " + ("; ".join(files) or "(none)")),
        evidence={"stamp": stamp, "files": len(files), "host": host, "root": root})


async def publish_shop_remote() -> ActionResult:
    ready = await _ready()
    if isinstance(ready, str):
        return ActionResult(False, ready)
    host, root, src, stamp = ready
    ok, why = await _run(_ssh(host, _mkdir_cmd(root)))
    if not ok:
        return ActionResult(False, f"nothing pushed: {why}")
    ok, why = await _run(push_argv(str(src), host, root, stamp))
    if not ok:
        return ActionResult(False, f"rsync failed, the live store is unchanged: {why}")
    ok, out = await _run(_ssh(host, _swap_cmd(root, stamp)))
    if not ok:
        return ActionResult(False, (f"release {stamp} uploaded but not made live: "
                                    f"{out}"))
    previous = _previous(out)
    # What the link named before, verbatim, when it was not one of our
    # releases: the rollback then refuses instead of saying "first push".
    replaced = "" if previous else out.strip()[:200]
    return ActionResult(
        True, f"pushed build {stamp} live at {host}:{root}/{SLUG} "
              f"(replaced {previous or replaced or 'nothing'})",
        evidence={"stamp": stamp, "previous": previous, "host": host},
        rollback_token=json.dumps({"host": host, "root": root, "release": stamp,
                                   "previous": previous, "replaced": replaced}))


async def publish_shop_remote_rollback(token: str) -> ActionResult:
    try:
        data = json.loads(token)
        host, root = str(data["host"]), str(data["root"])
        release, previous = data["release"], data.get("previous")
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    if not _HOST_RE.fullmatch(host) or not _ROOT_RE.fullmatch(root) \
            or ".." in root.split("/") \
            or not isinstance(release, str) or not hosting.STAMP_RE.fullmatch(release) \
            or not (previous is None or (isinstance(previous, str)
                                         and hosting.STAMP_RE.fullmatch(previous))):
        return ActionResult(False, "malformed rollback token")
    if previous is None and data.get("replaced"):
        return ActionResult(False, (
            f"{root}/{SLUG} pointed at {str(data['replaced'])[:200]!r}, not a release "
            "this action made; not restoring it automatically"))
    if previous is None:
        return ActionResult(True, (f"release {release} was the first push; nothing "
                                   "to restore, left live"))
    ok, why = await _run(_ssh(host, _back_cmd(root, release, previous)))
    if not ok:
        return ActionResult(False, f"rollback refused: {why}")
    return ActionResult(True, f"restored release {previous} of the store at "
                              f"{host}:{root}/{SLUG}")
