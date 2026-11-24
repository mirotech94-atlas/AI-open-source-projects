#!/bin/bash
# Host job (Mac, nightly): copy the repository's branch tips to the SECOND box as
# a single `git bundle`, so the code survives the loss of this Mac. NOT part of
# ATLAS: a host script, not installed. It reads the repo and writes one file.
#
#   ATLAS_OFFBOX_TARGET   required: user@host:/dir/ on the second box. Unset -> exit 2.
#   ATLAS_COMPOSE_DIR     repo checkout [this repo]
#   ATLAS_OFFBOX_SSH_KEY  optional ssh key file
#   ATLAS_BUNDLE_REFS     refs to bundle [--branches --tags]
#
# The bundle holds committed history only: no working tree, no .env, no data/.
# It is verified (`git bundle verify`) before it is sent, and the remote file's
# sha256 is compared after. A failure exits 1 and records ok=false. Each run
# writes a new stamped file; nothing on the second box is deleted or overwritten.
# Result: <repo>/data/app/backups/offbox_branches.json {at, ok, file, bytes}.
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
WT="${ATLAS_COMPOSE_DIR:-$(cd "$HERE/../.." && pwd)}"
TARGET="${ATLAS_OFFBOX_TARGET:-}"
REFS="${ATLAS_BUNDLE_REFS:---branches --tags}"
OUT_DIR="$WT/data/app/backups"
OUT="$OUT_DIR/offbox_branches.json"

[ -n "$TARGET" ] || { echo "copy_branch_tip: ATLAS_OFFBOX_TARGET is not set" >&2; exit 2; }
case "$TARGET" in *:*) ;; *) echo "copy_branch_tip: target needs host:path" >&2; exit 2 ;; esac
mkdir -p "$OUT_DIR"
HOSTONLY="${TARGET%%:*}"
RDIR="${TARGET#*:}"; [ -n "$RDIR" ] || RDIR="."

write() {   # write ok file bytes
  python3 - "$OUT" "$1" "$2" "$3" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone
out, ok, name, size = sys.argv[1:5]
data = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": ok == "true", "file": name or None,
        "bytes": int(size) if size.isdigit() else None}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".branches-")
with os.fdopen(fd, "w") as fh:
    json.dump(data, fh)
os.chmod(tmp, 0o644)
os.replace(tmp, out)
PY
}

STAMP=$(date -u +%Y%m%d-%H%M)
NAME="atlas-branches-$STAMP.bundle"
TMPB=$(mktemp "${TMPDIR:-/tmp}/atlas-bundle.XXXXXX") || exit 1
trap 'rm -f "$TMPB"' EXIT

# shellcheck disable=SC2086  # REFS is a list of rev-list options on purpose
if ! git -C "$WT" bundle create "$TMPB" $REFS >/dev/null 2>&1; then
  write false "$NAME" ""; echo "copy_branch_tip: git bundle create failed" >&2; exit 1
fi
if ! git -C "$WT" bundle verify "$TMPB" >/dev/null 2>&1; then
  write false "$NAME" ""; echo "copy_branch_tip: bundle did not verify" >&2; exit 1
fi
BYTES=$(wc -c < "$TMPB" | tr -d ' ')
LOCAL_SHA=$(shasum -a 256 "$TMPB" | awk '{print $1}')

SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
[ -n "${ATLAS_OFFBOX_SSH_KEY:-}" ] && SSH="$SSH -i $ATLAS_OFFBOX_SSH_KEY"
# rsync from the temp file to the stamped final name on the second box.
DEST="${TARGET%/}/$NAME"
if ! rsync -q -e "$SSH" "$TMPB" "$DEST"; then
  write false "$NAME" ""; echo "copy_branch_tip: rsync failed" >&2; exit 1
fi
REMOTE_SHA=$($SSH "$HOSTONLY" "sha256sum '${RDIR%/}/$NAME' 2>/dev/null || shasum -a 256 '${RDIR%/}/$NAME'" 2>/dev/null | awk '{print $1}')
if [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
  write false "$NAME" "$BYTES"; echo "copy_branch_tip: remote checksum mismatch" >&2; exit 1
fi
write true "$NAME" "$BYTES"
echo "copy_branch_tip: $NAME ($BYTES bytes) verified on $HOSTONLY"
