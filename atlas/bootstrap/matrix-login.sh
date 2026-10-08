#!/usr/bin/env bash
# Sign ATLAS in to k9 as Miro's own account, read-only plus drafts.
#
#   ./bootstrap/matrix-login.sh
#
# Runs on the Mac: the password goes to chat.example.org once over HTTPS, is never
# stored, and ATLAS keeps only the session token -- written to the server
# through the .env lock, restarting the API, then running the Matrix checks
# so you see it work. The session is its own device ("ATLAS") and can be
# revoked from Element without signing anything else out.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no virtualenv at $PY" >&2; exit 1; }

export MATRIX_HOMESERVER="${MATRIX_HOMESERVER:-https://example.org}"
"$PY" -m atlas.cli matrix-setup

echo
echo "Checking it works…"
ssh "${ATLAS_SSH_HOST:-root@atlas.example.com}" \
  "cd /opt/atlas && sleep 8 && docker compose exec -T api python -m atlas.cli connect matrix_rooms"
