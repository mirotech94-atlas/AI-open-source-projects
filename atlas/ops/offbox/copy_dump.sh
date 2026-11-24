#!/bin/bash
# Host job (Mac, nightly after atlas-backup-local.sh): copy the newest ENCRYPTED
# dump (atlas-*.dump.age) to the SECOND box, then check it arrived intact by
# comparing sha256 on both ends. NOT part of ATLAS: a host script, not installed.
#
#   ATLAS_OFFBOX_TARGET   required: user@host:/dir/ on the second box. Unset -> exit 2.
#   ATLAS_STATE_DIR       where the dumps are [$HOME/atlas-state]  (dumps in backups/)
#   ATLAS_COMPOSE_DIR     repo checkout the stack runs from [this repo]
#   ATLAS_OFFBOX_SSH_KEY  optional ssh key file (the Mac's own key)
#
# Rules it keeps:
#   * only *.dump.age is copied: a plain dump never leaves the Mac (the age
#     private key stays on the Mac, so the copy cannot be read on the second box);
#   * rsync with no --delete: this script deletes nothing on either side;
#   * the result is written to <repo>/data/app/backups/offbox.json
#     {at, ok, file, bytes, sha256, target_host}. ok is true only after the
#     remote sha256 equals the local one; any failure writes ok=false.
#     (The target is recorded as host only, never a path or a key.)
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
WT="${ATLAS_COMPOSE_DIR:-$(cd "$HERE/../.." && pwd)}"
STATE="${ATLAS_STATE_DIR:-$HOME/atlas-state}"
TARGET="${ATLAS_OFFBOX_TARGET:-}"
OUT_DIR="$WT/data/app/backups"
OUT="$OUT_DIR/offbox.json"

[ -n "$TARGET" ] || { echo "copy_dump: ATLAS_OFFBOX_TARGET is not set" >&2; exit 2; }
case "$TARGET" in *:*) ;; *) echo "copy_dump: target needs host:path (no colon would copy locally)" >&2; exit 2 ;; esac
mkdir -p "$OUT_DIR"

HOSTONLY="${TARGET%%:*}"
RDIR="${TARGET#*:}"
[ -n "$RDIR" ] || RDIR="."

write() {   # write ok file bytes sha
  python3 - "$OUT" "$1" "$2" "$3" "$4" "${HOSTONLY#*@}" <<'PY'
import json, os, sys, tempfile
from datetime import datetime, timezone
out, ok, name, size, sha, host = sys.argv[1:7]
data = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": ok == "true", "file": name,
        "bytes": int(size) if size.isdigit() else None,
        "sha256": sha or None, "target_host": host}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out), prefix=".offbox-")
with os.fdopen(fd, "w") as fh:
    json.dump(data, fh)
os.chmod(tmp, 0o644)
os.replace(tmp, out)
PY
}

# shellcheck disable=SC2012  # names are atlas-YYYYMMDD-HHMM.dump.age
FILE=$(ls -1t "$STATE"/backups/atlas-*.dump.age 2>/dev/null | head -n 1)
[ -n "$FILE" ] || { write false "" "" ""; echo "copy_dump: no atlas-*.dump.age in $STATE/backups" >&2; exit 1; }
NAME=$(basename "$FILE")
BYTES=$(wc -c < "$FILE" | tr -d ' ')
LOCAL_SHA=$(shasum -a 256 "$FILE" | awk '{print $1}')

SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
[ -n "${ATLAS_OFFBOX_SSH_KEY:-}" ] && SSH="$SSH -i $ATLAS_OFFBOX_SSH_KEY"

if ! rsync -q -e "$SSH" "$FILE" "$TARGET"; then
  write false "$NAME" "$BYTES" "$LOCAL_SHA"; echo "copy_dump: rsync failed" >&2; exit 1
fi
REMOTE_SHA=$($SSH "$HOSTONLY" "sha256sum '$RDIR/$NAME' 2>/dev/null || shasum -a 256 '$RDIR/$NAME'" 2>/dev/null | awk '{print $1}')
if [ -z "$REMOTE_SHA" ] || [ "$REMOTE_SHA" != "$LOCAL_SHA" ]; then
  write false "$NAME" "$BYTES" "$LOCAL_SHA"
  echo "copy_dump: remote checksum '$REMOTE_SHA' does not match local" >&2; exit 1
fi
write true "$NAME" "$BYTES" "$LOCAL_SHA"
echo "copy_dump: $NAME ($BYTES bytes) verified on $HOSTONLY"
