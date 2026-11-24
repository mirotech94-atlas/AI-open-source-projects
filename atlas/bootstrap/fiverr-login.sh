#!/usr/bin/env bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Sign in to Fiverr once, then put the session where ATLAS can read it.
#
#   ./bootstrap/fiverr-login.sh
#
# Fiverr has no API, so ATLAS drives a browser session a human established.
# This opens a real window: you log in yourself, including any 2FA, and
# ATLAS never sees the password. Then the saved session is synced to the
# server, because the login happens on your Mac and the checks run in a
# container.
#
# Run it from anywhere; it finds the repo itself. `python -m atlas.modules...`
# from a home directory picks up whatever `python` means there -- on this Mac
# that is Python 2.7, which cannot even import the package.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no virtualenv at $PY — run: python3 -m venv .venv" >&2; exit 1; }

"$PY" - <<'CHECK' || { echo; echo "Installing Playwright's browser (one time)…" >&2; "$PY" -m playwright install chromium; }
import sys
try:
    from playwright.async_api import async_playwright  # noqa: F401
except ImportError:
    sys.exit(1)
CHECK

STATE="${FIVERR_STATE_PATH:-$REPO/data/fiverr_state.json}"
export FIVERR_STATE_PATH="$STATE"

echo "A browser window will open. Log in there; this will notice by itself."
"$PY" -m atlas.modules.fiverr.login

[ -s "$STATE" ] || { echo "no session was saved; nothing to sync" >&2; exit 1; }

# Refuse to ship something that is not a session. An empty or malformed file
# would overwrite a working one and turn four green checks UNKNOWN.
"$PY" - "$STATE" <<'VALIDATE'
import json, sys
state = json.load(open(sys.argv[1]))
cookies = state.get("cookies") or []
if not cookies:
    sys.exit("the saved file has no cookies in it — did the login complete?")
print(f"session looks valid: {len(cookies)} cookies")
VALIDATE

. "$REPO/bootstrap/atlas-host.sh"
HOST="$(atlas_host)"
if [ "$HOST" = "local" ]; then
    # The api mounts ./data/app as /app/data: the session lands where the
    # container reads it, with no copy over the network.
    DEST="${ATLAS_FIVERR_DEST:-$REPO/data/app/fiverr_state.json}"
    mkdir -p "$(dirname "$DEST")"
    install -m 600 "$STATE" "$DEST"
    echo "copied to $DEST"
    echo
    echo "Checking it works…"
    (cd "${ATLAS_COMPOSE_DIR:-$REPO}" && docker compose -f docker-compose.yml \
        -f docker-compose.local.yml exec -T api python -m atlas.cli connect fiverr)
    exit 0
fi
DEST="${ATLAS_FIVERR_DEST:-/opt/atlas/data/app/fiverr_state.json}"
ssh "$HOST" "mkdir -p $(dirname "$DEST")"
rsync --times -q "$STATE" "$HOST:$DEST"
echo "synced to $HOST:$DEST"

echo
echo "Checking it works…"
ssh "$HOST" "cd /opt/atlas && docker compose exec -T api python -m atlas.cli connect fiverr"
