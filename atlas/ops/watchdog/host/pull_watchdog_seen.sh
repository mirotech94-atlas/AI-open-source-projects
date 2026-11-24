#!/bin/bash
# Host job (launchd, every 5 min) on the Mac: copy the independent watchdog's
# seen.json from the watchdog box (the Spark) to
# <repo>/data/app/backups/watchdog_seen.json, adding this Mac's own pulled_at.
# ATLAS reads it as /app/data/backups/watchdog_seen.json (check "watchdog_seen").
#
# The pull uses the Mac's OWN ssh key to a box Miro already reaches (`ssh spark`).
# ATLAS and the container get no credential, and the Spark gets no key to the
# Mac: nothing is shared with the watchdog (docs/watchdog.md, BUILD_ORDER 21.F.4).
# This script only reads one small file there.
#
# If the pull fails, the OLD record is left alone: its pulled_at stops moving,
# and the check reads UNKNOWN once it is 15 minutes old. A failed pull is never
# written as a fresh one (invariant 7).
#
# Environment (defaults in brackets):
#   ATLAS_COMPOSE_DIR        the repo checkout the stack runs from [this repo]
#   ATLAS_WATCHDOG_HOST      ssh name of the watchdog box [spark]
#   ATLAS_WATCHDOG_SEEN_PATH path of seen.json there [.local/state/atlas-watchdog/seen.json]
#   ATLAS_WATCHDOG_SSH_KEY   optional ssh key file for the pull
#
# Portable: macOS /bin/bash 3.2 and Linux (the tests run it with a fake ssh).
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
WT="${ATLAS_COMPOSE_DIR:-$(cd "$HERE/../../.." && pwd)}"
HOST="${ATLAS_WATCHDOG_HOST:-spark}"
REMOTE="${ATLAS_WATCHDOG_SEEN_PATH:-.local/state/atlas-watchdog/seen.json}"
OUT_DIR="$WT/data/app/backups"
OUT="$OUT_DIR/watchdog_seen.json"
mkdir -p "$OUT_DIR"

SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"
[ -n "${ATLAS_WATCHDOG_SSH_KEY:-}" ] && SSH="$SSH -i $ATLAS_WATCHDOG_SSH_KEY"

raw=$($SSH "$HOST" cat "$REMOTE" 2>/dev/null) || {
  echo "pull_watchdog_seen: could not read $REMOTE on $HOST; record left as it was" >&2
  exit 1
}

printf '%s' "$raw" | python3 -c '
import json, os, sys, tempfile
from datetime import datetime, timezone

out = sys.argv[1]
try:
    data = json.load(sys.stdin)
except ValueError:
    sys.exit("pull_watchdog_seen: the remote file is not JSON; record left as it was")
if not isinstance(data, dict):
    sys.exit("pull_watchdog_seen: the remote file is not a JSON object")
# Keep only the fields ATLAS reads; nothing else crosses over.
keep = {k: data.get(k) for k in
        ("at", "last_healthz_ok_at", "healthz_state", "route_configured", "label")}
keep["pulled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".seen-")
with os.fdopen(fd, "w") as fh:
    json.dump(keep, fh)
os.chmod(tmp, 0o644)
os.replace(tmp, out)
' "$OUT"
