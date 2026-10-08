#!/usr/bin/env bash
# Authorise ATLAS to read Gmail and Calendar, once.
#
#   ./bootstrap/google-login.sh ~/Downloads/client_secret_*.json
#
# Google's API needs three values: a client id, a client secret, and a
# refresh token. The first two you create in the console; the third can only
# come from you consenting in a browser, and no API can produce it for you.
# This runs that consent and writes all three to the server.
#
# ATLAS never sees your Google password. The consent happens on Google's own
# page, and what comes back is a token scoped to the two things below.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no virtualenv at $PY" >&2; exit 1; }

CLIENT_JSON="${1:-}"
# Google shows the secret once and never again, so the pair is often copied
# rather than the file downloaded. Both work.
case "$CLIENT_JSON" in
    *.apps.googleusercontent.com)
        [ -n "${2:-}" ] || { echo "usage: $0 <client-id> <client-secret>" >&2; exit 2; }
        exec "$PY" "$REPO/bootstrap/google_login.py" "$1" "$2" ;;
esac
if [ -z "$CLIENT_JSON" ] || [ ! -f "$CLIENT_JSON" ]; then
    cat >&2 <<'HELP'
usage: ./bootstrap/google-login.sh <client_secret_….json>

First, in the Google Cloud console for your project:

  1. Enable both APIs (you already have these pages open):
       Gmail API, Google Calendar API
  2. APIs & Services -> OAuth consent screen
       User type: External. Add yourself under "Test users" — otherwise the
       refresh token expires after 7 days and Mail goes dark without warning.
  3. APIs & Services -> Credentials -> Create credentials -> OAuth client ID

     Either application type works:

       Desktop app        nothing else to fill in.
       Web application    add this under "Authorized redirect URIs":

                              http://localhost:8765/

                          exactly, including the trailing slash. Google
                          matches it character for character, and a
                          mismatch only shows up after you have already
                          consented.

     Then download the JSON and pass its path to this script.

An API key (AIza…) is NOT this. API keys cannot read a person's mail or
calendar at all — only OAuth can — so if that is what you made, it is the
wrong credential and worth deleting rather than leaving around.
HELP
    exit 2
fi

exec "$PY" "$REPO/bootstrap/google_login.py" "$CLIENT_JSON"
