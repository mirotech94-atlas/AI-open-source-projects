#!/usr/bin/env bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Sign ATLAS in to k9 as Miro's own account, read-only plus drafts.
#
#   ./bootstrap/matrix-login.sh
#
# Runs on the Mac: the password goes to chat.matrix.example.com once over HTTPS, is never
# stored, and ATLAS keeps only the session token -- written to the server
# through the .env lock, restarting the API, then running the Matrix checks
# so you see it work. The session is its own device ("ATLAS") and can be
# revoked from Element without signing anything else out.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no virtualenv at $PY" >&2; exit 1; }

export MATRIX_HOMESERVER="${MATRIX_HOMESERVER:-https://matrix.example.com}"
"$PY" -m atlas.cli matrix-setup

echo
echo "Checking it works…"
. "$REPO/bootstrap/atlas-host.sh"
HOST="$(atlas_host)"
if [ "$HOST" = "local" ]; then
    sleep 8
    (cd "${ATLAS_COMPOSE_DIR:-$REPO}" && docker compose -f docker-compose.yml \
        -f docker-compose.local.yml exec -T api python -m atlas.cli connect matrix_rooms)
    exit 0
fi
ssh "$HOST" \
  "cd /opt/atlas && sleep 8 && docker compose exec -T api python -m atlas.cli connect matrix_rooms"
