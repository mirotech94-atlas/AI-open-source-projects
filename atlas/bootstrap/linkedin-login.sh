#!/usr/bin/env bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Sign in to LinkedIn once, then put the session where ATLAS can use it.
#
#   ./bootstrap/linkedin-login.sh
#
# LinkedIn has no API. ATLAS drives a browser session a human established:
# this opens a real window, you sign in yourself (tick "Keep me signed in"),
# and ATLAS never sees the password. The saved session -- cookies, no
# credential -- is synced to the server, where the sweep and the daily post
# run from it.
#
# Re-run it whenever the linkedin checks say the session is signed out.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$REPO/bootstrap/atlas-host.sh"
HOST="$(atlas_host)"
# Local mode (ATLAS runs on this Mac, 2026-11-17): sign in here, no tunnel;
# the api container reaches the web through this Mac's address, so LinkedIn
# sees the same address at sign-in and at every sweep.
cd "$REPO"

PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no virtualenv at $PY — run: python3 -m venv .venv && .venv/bin/pip install -e '.[browser]'" >&2; exit 1; }

"$PY" - <<'CHECK' || { echo; echo "Installing Playwright's browser (one time)…" >&2; "$PY" -m playwright install chromium; }
import sys
try:
    from playwright.async_api import async_playwright  # noqa: F401
except ImportError:
    sys.exit(1)
CHECK

if [ "$HOST" = "local" ]; then STATE="$REPO/data/app/linkedin_session.json"; else STATE="${LINKEDIN_SESSION_PATH:-$REPO/data/linkedin_session.json}"; fi
export LINKEDIN_SESSION_PATH="$STATE"

DEST="${ATLAS_LINKEDIN_DEST:-/opt/atlas/data/app/linkedin_session.json}"

if [ "$HOST" != "local" ]; then
# The sign-in goes through the server. LinkedIn ties a session to where it
# was minted; one signed in from this Mac and then used from the server's
# IP was revoked after a single run. With the browser tunnelled through the
# server, LinkedIn sees the same address at sign-in and at every sweep.
PORT="${LINKEDIN_TUNNEL_PORT:-1080}"
TUNNEL="ssh -f -N -o ExitOnForwardFailure=yes -D 127.0.0.1:$PORT $HOST"
# A tunnel left behind by an earlier attempt is usually dead and holds the
# port. Clear it, open a fresh one, and take it down again on exit.
free_port() { for pid in $(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); do kill "$pid" 2>/dev/null || true; done; }
free_port
sleep 1
$TUNNEL
trap free_port EXIT
export LINKEDIN_LOGIN_PROXY="socks5://127.0.0.1:$PORT"
EXIT_IP=$(curl -s --max-time 15 --socks5-hostname "127.0.0.1:$PORT" https://api.ipify.org || true)
[ -n "$EXIT_IP" ] || { echo "the tunnel to $HOST came up but carries no traffic; check ssh access" >&2; exit 1; }
echo "tunnel up: the sign-in will come from $EXIT_IP (the server)"

fi
echo "A browser window will open. Sign in there; this will notice by itself."
"$PY" -m atlas.modules.linkedin.login

[ -s "$STATE" ] || { echo "no session was saved; nothing to sync" >&2; exit 1; }

"$PY" - "$STATE" <<'VALIDATE'
import json, sys
state = json.load(open(sys.argv[1]))
cookies = state.get("cookies") or []
if not any(c.get("name") == "li_at" for c in cookies):
    sys.exit("the saved file has no LinkedIn session cookie in it — did the sign-in complete?")
print(f"session looks valid: {len(cookies)} cookies")
VALIDATE

if [ "$HOST" = "local" ]; then
    chmod 600 "$STATE"
    echo "session saved at $STATE (the api container reads it as /app/data/linkedin_session.json)"
    echo; echo "Checking it works…"
    DOCKER_CONTEXT="${DOCKER_CONTEXT:-orbstack}" docker exec atlas-agency-api-1 python -m atlas.modules.linkedin.sweep --check
    exit 0
fi

ssh "$HOST" "mkdir -p $(dirname "$DEST")"
rsync --times -q "$STATE" "$HOST:$DEST"
# The api container runs unprivileged; a root-owned 600 file is unreadable
# to it and the sweep reports "no session" for a session that is there.
ssh "$HOST" "cd /opt/atlas && chown \$(docker compose exec -T api id -u | tr -d '\r') $DEST && chmod 600 $DEST"
echo "synced to $HOST:$DEST"
# The copy on the Mac has done its job.
rm -f "$STATE"

echo
echo "Checking it works…"
ssh "$HOST" "cd /opt/atlas && docker compose exec -T api python -m atlas.modules.linkedin.sweep --check"
