#!/bin/bash
# Host job (launchd, every 15 min) on the Mac that runs ATLAS: measure the REAL
# data volume and write <repo>/data/app/backups/disk.json, which the API reads
# as /app/data/backups/disk.json (atlas/modules/backups, check "disk").
#
# Why a host job: ATLAS runs in a container, and inside it disk_usage() reads
# the OrbStack VM, not the Mac's disk. This script only READS. It never deletes,
# prunes or moves anything (invariant 1; Tier 3 deletion stays denied).
#
# What it writes (JSON, atomic, UTC time with offset):
#   {"at","path","mount","total_bytes","free_bytes","top":[{"path","bytes"}...]}
#
# `top` is the N largest immediate children of a FIXED, configurable list of
# roots (never all of $HOME: slow, and the names are personal). Each `du` is
# depth 1, niced, with a timeout-free read of one level.
#
# If df cannot be read, NOTHING is written: the old file ages out and the check
# reads UNKNOWN (stale), never a made-up number (invariant 6/7).
#
# Environment (defaults in brackets):
#   ATLAS_COMPOSE_DIR   the repo checkout the stack runs from [this repo]
#   ATLAS_DISK_PATH     a path ON the data volume to measure [$ATLAS_COMPOSE_DIR/data]
#   ATLAS_DISK_ROOTS    colon-separated folders to rank [$ATLAS_COMPOSE_DIR/data:
#                       $HOME/atlas-state:$HOME/.orbstack]
#   ATLAS_DISK_TOP_N    how many folders to keep [5]
#
# Portable: macOS /bin/bash 3.2 and Linux (the tests run it with fake df/du).
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
WT="${ATLAS_COMPOSE_DIR:-$(cd "$HERE/../../.." && pwd)}"
MEASURE="${ATLAS_DISK_PATH:-$WT/data}"
ROOTS="${ATLAS_DISK_ROOTS:-$WT/data:$HOME/atlas-state:$HOME/.orbstack}"
TOP_N="${ATLAS_DISK_TOP_N:-5}"
OUT_DIR="$WT/data/app/backups"
OUT="$OUT_DIR/disk.json"

mkdir -p "$OUT_DIR"

# df -Pk: POSIX columns on both macOS and Linux (1024-byte blocks).
line=$(df -Pk "$MEASURE" 2>/dev/null | awk 'NR==2 {print $2, $4}')
mount=$(df -Pk "$MEASURE" 2>/dev/null | awk 'NR==2 {print $NF}')
total_k=${line%% *}
free_k=${line##* }
case "$total_k$free_k" in ''|*[!0-9]*)
  echo "disk_status: could not read df for $MEASURE; writing nothing" >&2
  exit 1 ;;
esac

tmp_list=$(mktemp "${TMPDIR:-/tmp}/atlas-disk.XXXXXX") || exit 1
trap 'rm -f "$tmp_list"' EXIT
old_ifs=$IFS
IFS=:
for root in $ROOTS; do
  [ -d "$root" ] || continue
  # One level only; sizes in KiB; errors (permission) are not fatal.
  nice -n 19 du -k -d 1 "$root" 2>/dev/null | while IFS= read -r row; do
    size=${row%%[[:space:]]*}
    path=${row#*[[:space:]]}
    # `du -d 1` prints the root itself last: keep only its children.
    [ "$path" = "$root" ] && continue
    printf '%s\t%s\n' "$size" "$path"
  done >> "$tmp_list"
done
IFS=$old_ifs

python3 - "$OUT" "$MEASURE" "$mount" "$total_k" "$free_k" "$TOP_N" "$tmp_list" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone

out, path, mount, total_k, free_k, top_n, listing = sys.argv[1:8]
rows = []
with open(listing, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        size, _, p = line.rstrip("\n").partition("\t")
        if size.isdigit() and p:
            rows.append({"path": p, "bytes": int(size) * 1024})
rows.sort(key=lambda r: -r["bytes"])
data = {
    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "path": path, "mount": mount,
    "total_bytes": int(total_k) * 1024, "free_bytes": int(free_k) * 1024,
    "top": rows[: int(top_n)],
}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".disk-")
with os.fdopen(fd, "w") as fh:
    json.dump(data, fh)
os.chmod(tmp, 0o644)        # the api container's user must be able to read it
os.replace(tmp, out)
PY
