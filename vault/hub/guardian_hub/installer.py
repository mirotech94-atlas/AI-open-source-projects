"""Serve the sensor package, and the one-line installer that installs it.

Why the hub serves this at all: the dashboard used to advertise
`curl -fsSL https://get.guardian.sh/mac | sh`. That domain is not ours -- it
resolves to a third party -- so the advertised command piped a stranger's
script into a root shell. Nothing Vault prints may name a host the operator
of this hub does not control, and the only host we can be sure of is *this
one*. So the hub itself is the distribution channel: `GET /install` renders the
script, `GET /sensor.whl` serves the package, and the script's `SRC` is
whatever origin the request arrived on (never `settings.base_url`, which is a
deploy-time guess and in production points at a different hostname than the
one a customer typed).

**Wheel, not an sdist.** A wheel is a zip that pip unpacks; an sdist has to be
*built* at install time, which means pip fetches `setuptools` from PyPI (a
3.12+ venv has none). Serving a wheel lets the installer run
`pip install --no-index <file>`, so the install provably contacts no host but
this hub. The wheel is built here with `zipfile` alone -- no `build`/`wheel`
dependency on the hub, and the sensor has no dependencies of its own, so
there is nothing else to resolve.

It is built **on demand and cached**, keyed by a fingerprint of the sensor
source tree, rather than at deploy time: `deploy/deploy.sh` already rsyncs
`sensor/` to the VPS, so the source is always present and always current, and
an extra build step in the deploy is one more thing that can be forgotten
(leaving a stale sensor served forever). Every zip entry gets a fixed
timestamp and the names are sorted, so the bytes -- and therefore the sha256 --
are identical in any process that builds from the same source. That is what
lets `/sensor.whl.sha256` and the checksum embedded in the script agree with
the bytes `/sensor.whl` returns.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import threading
import zipfile
from pathlib import Path

DIST_NAME = "guardian_sensor"       # wheel/dist-info use the escaped name
PROJECT_NAME = "guardian-sensor"
SUMMARY = "Vault sensor: runs on your network, collects facts, reports to the hub."
CONSOLE_SCRIPT = "guardian-sensor = guardian_sensor.cli:main"
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)   # fixed, so the build is reproducible

_lock = threading.Lock()
_cache: dict[str, tuple[str, bytes, str]] = {}   # fingerprint -> (version, wheel, sha256)


# --------------------------------------------------------------------------
# locating the sensor source
# --------------------------------------------------------------------------

def sensor_src() -> Path | None:
    """The `guardian_sensor` package directory, or None when it isn't shipped
    next to the hub (a hub-only install). `GUARDIAN_SENSOR_SRC` overrides."""
    env = os.environ.get("GUARDIAN_SENSOR_SRC")
    candidates = []
    if env:
        candidates.append(Path(env))
    # repo_root/sensor, the layout deploy.sh rsyncs to /opt/guardian/sensor
    candidates.append(Path(__file__).resolve().parents[2] / "sensor")
    for base in candidates:
        pkg = base / "guardian_sensor"
        if pkg.is_dir() and (pkg / "cli.py").is_file():
            return pkg
        if base.name == "guardian_sensor" and base.is_dir():
            return base
    return None


def _sources(pkg: Path) -> list[tuple[str, Path]]:
    """(arcname, path) for every file that belongs in the wheel, sorted.

    Only `*.py` under the package: `build/`, `.venv/`, `__pycache__/`, tests
    and editor droppings must never reach a customer's machine, and the
    sensor ships no data files."""
    out = []
    for path in sorted(pkg.rglob("*.py")):
        rel = path.relative_to(pkg)
        parts = rel.parts
        if any(p in ("__pycache__", "build", ".venv", "tests") for p in parts):
            continue
        out.append((f"guardian_sensor/{rel.as_posix()}", path))
    return out


def sensor_version(pkg: Path) -> str:
    """Read VERSION out of the sensor's `__init__.py` textually -- the hub's
    venv does not have `guardian_sensor` importable."""
    text = (pkg / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    return match.group(1) if match else "0.0.0"


def _fingerprint(files: list[tuple[str, Path]]) -> str:
    h = hashlib.sha256()
    for arcname, path in files:
        h.update(arcname.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


# --------------------------------------------------------------------------
# building the wheel
# --------------------------------------------------------------------------

def _record_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
    return f"sha256={digest.decode()}"


def _dist_info_files(version: str) -> list[tuple[str, bytes]]:
    dist_info = f"{DIST_NAME}-{version}.dist-info"
    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {PROJECT_NAME}\n"
        f"Version: {version}\n"
        f"Summary: {SUMMARY}\n"
        "Requires-Python: >=3.10\n"
    )
    wheel = (
        "Wheel-Version: 1.0\n"
        "Generator: guardian-hub\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    )
    # entry_points.txt is the one piece pip needs to create the
    # `guardian-sensor` console script -- and `sensor/guardian_sensor/
    # install.py:script_path()` looks for exactly that file next to the
    # interpreter, so without it the service install no-ops.
    entry_points = f"[console_scripts]\n{CONSOLE_SCRIPT}\n"
    return [
        (f"{dist_info}/METADATA", metadata.encode()),
        (f"{dist_info}/WHEEL", wheel.encode()),
        (f"{dist_info}/entry_points.txt", entry_points.encode()),
    ]


def _build(files: list[tuple[str, Path]], version: str) -> bytes:
    entries: list[tuple[str, bytes]] = [(a, p.read_bytes()) for a, p in files]
    entries.extend(_dist_info_files(version))
    entries.sort()

    record_lines = [f"{name},{_record_hash(data)},{len(data)}" for name, data in entries]
    record_name = f"{DIST_NAME}-{version}.dist-info/RECORD"
    record_lines.append(f"{record_name},,")     # RECORD's own line is hashless, per PEP 427
    entries.append((record_name, ("\n".join(sorted(record_lines)) + "\n").encode()))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ZIP_DATE)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def wheel() -> tuple[str, bytes, str] | None:
    """(version, wheel bytes, sha256 hex), or None when no sensor source is
    shipped with this hub. Cached on the source tree's fingerprint, so an
    edited sensor is picked up without a restart but an unchanged one is
    built exactly once."""
    pkg = sensor_src()
    if pkg is None:
        return None
    files = _sources(pkg)
    if not files:
        return None
    fp = _fingerprint(files)
    with _lock:
        hit = _cache.get(fp)
        if hit is not None:
            return hit
        version = sensor_version(pkg)
        data = _build(files, version)
        built = (version, data, hashlib.sha256(data).hexdigest())
        _cache.clear()          # only ever one live build; an old one is dead weight
        _cache[fp] = built
        return built


def wheel_filename(version: str) -> str:
    return f"{DIST_NAME}-{version}-py3-none-any.whl"


# --------------------------------------------------------------------------
# the installer script
# --------------------------------------------------------------------------

#: POSIX sh. Substituted with `str.replace` on the four @@NAME@@ markers --
#: not `%`/`.format`, because the script is full of `%` and `{}`.
#:
#: Design rules this script must keep obeying (they are the whole point of
#: Task 61):
#:   * no secret is embedded -- the enrol code arrives as an argument the
#:     human pasted, and the wheel/checksum are public bytes;
#:   * SRC (where the package comes from) and HUB (who the sensor enrols
#:     with) are separate: HUB defaults to SRC and `--hub` moves only HUB, so
#:     the checksum baked in here always describes the file SRC serves;
#:   * https only, with a loopback http exception for local testing;
#:   * `pip --no-index`, so the install contacts SRC and nothing else;
#:   * it runs no command the caller did not ask for: an unknown argument is
#:     a hard error, and every step is either "check" or one of the four
#:     actions named in the usage text.
_SCRIPT = r'''#!/bin/sh
# Vault sensor installer -- served by @@SRC@@
#
#   curl -fsSL @@SRC@@/install | sh -s -- --code YOUR-ENROL-CODE
#   curl -fsSL @@SRC@@/install | sh -s -- --code YOUR-ENROL-CODE --hub https://your-hub
#
# It does these things and nothing else:
#   1. checks this is macOS or Linux, with python3 >= 3.10 and curl
#   2. creates a virtualenv at $HOME/.guardian/venv (reused if it exists)
#   3. downloads the guardian-sensor wheel from @@SRC@@ and verifies its sha256
#   4. installs it into that venv with --no-index, so nothing but @@SRC@@ is contacted
#   5. enrols with the hub, using the code you passed on the command line
#   6. installs the background service (launchd on macOS, systemd on Linux)
#
# Safe to re-run: it upgrades in place, and skips enrolment when this machine
# is already enrolled.
set -eu

SRC='@@SRC@@'
WHEEL='@@WHEEL@@'
WHEEL_SHA256='@@SHA256@@'
HUB="$SRC"
CODE=''

say()  { printf '%s\n' "$1"; }
die()  { printf 'guardian-install: %s\n' "$1" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: curl -fsSL $SRC/install | sh -s -- --code <enrol-code> [--hub <url>]

  --code <code>   enrolment code from your Vault dashboard.
                  Required the first time; omit it to upgrade in place.
  --hub <url>     the hub this sensor reports to. Default: $SRC
  -h, --help      print this and exit.
EOF
}

# ---- arguments -----------------------------------------------------------
while [ "$#" -gt 0 ]; do
  case "$1" in
    --code)   [ "$#" -ge 2 ] || die '--code needs a value'; CODE="$2"; shift 2 ;;
    --code=*) CODE="${1#--code=}"; shift ;;
    --hub)    [ "$#" -ge 2 ] || die '--hub needs a value'; HUB="$2"; shift 2 ;;
    --hub=*)  HUB="${1#--hub=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

# Trailing slashes would turn into "//install" and "//api/v1/sensor/enrol".
while :; do
  case "$SRC" in */) SRC="${SRC%/}" ;; *) break ;; esac
done
while :; do
  case "$HUB" in */) HUB="${HUB%/}" ;; *) break ;; esac
done

# ---- https only ----------------------------------------------------------
# A plain-http hub would put the enrol code, and the wheel itself, on the
# wire in clear text. Loopback is exempt so a developer can test against a
# hub on their own machine.
check_url() {
  case "$1" in
    https://*) : ;;
    http://127.0.0.1|http://127.0.0.1/*|http://127.0.0.1:*) : ;;
    http://localhost|http://localhost/*|http://localhost:*) : ;;
    http://\[::1\]|http://\[::1\]/*|http://\[::1\]:*) : ;;
    *) die "refusing to use $1 -- the URL must be https (only a loopback http URL is allowed, for local testing)" ;;
  esac
}
check_url "$SRC"
check_url "$HUB"

# ---- platform ------------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
  Darwin) SERVICE_FLAG='--launchd' ;;
  Linux)  SERVICE_FLAG='--systemd' ;;
  *) die "$OS is not supported. The Vault sensor installs on macOS and Linux; for anything else, install the guardian-sensor package by hand." ;;
esac

command -v curl >/dev/null 2>&1 || die 'curl is required.'
command -v python3 >/dev/null 2>&1 || die 'python3 is required (3.10 or newer).'
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "python3 3.10 or newer is required (found $(python3 -V 2>&1))."

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    die 'neither shasum nor sha256sum is available, so the download cannot be verified.'
  fi
}

GUARDIAN_HOME="$HOME/.guardian"
VENV="$GUARDIAN_HOME/venv"

# Fail before touching anything if there is nothing this run could do.
if [ -z "$CODE" ] && [ ! -f "$GUARDIAN_HOME/hub.json" ] && [ ! -f /var/lib/guardian/hub.json ]; then
  usage >&2
  die 'no --code given and this machine is not enrolled yet.'
fi

if [ "$OS" = Linux ] && [ "$(id -u)" -ne 0 ]; then
  say 'note: not running as root -- the systemd step at the end will need sudo.'
fi

# ---- virtualenv ----------------------------------------------------------
mkdir -p "$GUARDIAN_HOME"
chmod 700 "$GUARDIAN_HOME" 2>/dev/null || true
if [ -x "$VENV/bin/python3" ]; then
  say "reusing the virtualenv at $VENV"
else
  say "creating a virtualenv at $VENV"
  python3 -m venv "$VENV" || die "could not create a virtualenv at $VENV (on Debian/Ubuntu: apt install python3-venv)"
fi

# ---- download and verify -------------------------------------------------
TMP="$(mktemp -d "${TMPDIR:-/tmp}/guardian-install.XXXXXX")" || die 'could not create a temporary directory.'
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

say "downloading $WHEEL from $SRC"
curl -fsSL --proto '=https,http' -o "$TMP/$WHEEL" "$SRC/sensor.whl" \
  || die "could not download $SRC/sensor.whl"

GOT="$(sha256_of "$TMP/$WHEEL")"
if [ "$GOT" != "$WHEEL_SHA256" ]; then
  die "checksum mismatch -- expected $WHEEL_SHA256, got $GOT. Refusing to install; re-fetch $SRC/install and try again."
fi
say 'checksum ok'

# --no-index: the wheel has no dependencies, so pip has no reason to reach
# any package index. This is what makes @@SRC@@ the only host contacted.
"$VENV/bin/python3" -m pip install --quiet --disable-pip-version-check --no-input \
  --no-index --upgrade "$TMP/$WHEEL" || die 'pip could not install the sensor package.'
[ -x "$VENV/bin/guardian-sensor" ] \
  || die "the install did not create $VENV/bin/guardian-sensor."
say "installed $("$VENV/bin/guardian-sensor" version)"

DATA_DIR="$("$VENV/bin/python3" -c 'from guardian_sensor import paths; print(paths.DATA_DIR)')"

# ---- enrol ---------------------------------------------------------------
if [ -n "$CODE" ]; then
  say "enrolling with $HUB"
  "$VENV/bin/guardian-sensor" enrol "$CODE" --hub "$HUB" \
    || die 'enrolment failed. Enrol codes are single-use and short-lived -- mint a fresh one in your dashboard and re-run.'
elif [ -f "$DATA_DIR/hub.json" ]; then
  say "already enrolled ($DATA_DIR/hub.json) -- leaving the enrolment alone"
else
  die "no --code given and no enrolment found in $DATA_DIR. Mint a code in your dashboard and re-run with --code <code>."
fi

# ---- service -------------------------------------------------------------
say 'installing the background service'
if "$VENV/bin/guardian-sensor" install "$SERVICE_FLAG"; then
  SERVICE_OK=yes
else
  SERVICE_OK=no
fi

say ''
say 'Vault sensor installed.'
say "  program   $VENV/bin/guardian-sensor"
say "  data      $DATA_DIR"
say "  hub       $HUB"
if [ "$SERVICE_OK" = yes ]; then
  say '  service   installed and started'
  say ''
  say 'Your dashboard will show the first scan within a few minutes.'
  exit 0
fi
say '  service   NOT installed'
say ''
if [ "$OS" = Linux ]; then
  say "The sensor is enrolled but not running as a service. Finish with:"
  say "  sudo $VENV/bin/guardian-sensor install --systemd"
else
  say "The sensor is enrolled but not running as a service. Finish with:"
  say "  $VENV/bin/guardian-sensor install --launchd"
fi
exit 1
'''


def script(src: str, wheel_name: str, sha256: str) -> str:
    """Render the installer for one origin. `src` is the origin the request
    came in on -- scheme + host + port, no trailing slash."""
    return (
        _SCRIPT
        .replace("@@SRC@@", src)
        .replace("@@WHEEL@@", wheel_name)
        .replace("@@SHA256@@", sha256)
    )
