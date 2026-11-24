#!/usr/bin/env bash
# Authorise ATLAS to read Gmail, Calendar and Search Console, once.
#
#   ./bootstrap/google-login.sh ~/Downloads/client_secret_*.json
#   ./bootstrap/google-login.sh --reconsent    # same client, new consent
#
# The consent asks for gmail.modify, calendar.events, userinfo.email and
# webmasters.readonly (Search Console, read only, for the SEO agent).
# ATLAS_GOOGLE_SEARCH_CONSOLE=0 leaves Search Console out. A refresh token
# granted before 2026-10-29 lacks it: `--reconsent` replaces that token
# with one that has it, reusing the client ATLAS already has (no file).
#
# Google's API needs three values: a client id, a client secret, and a
# refresh token. The first two you create in the console; the third can only
# come from you consenting in a browser, and no API can produce it for you.
# This runs that consent and writes all three to ATLAS's .env: the local
# checkout when /opt/atlas is absent (ATLAS_SSH_HOST=local), else over ssh.
#
# ATLAS never sees your Google password. The consent happens on Google's own
# page, and what comes back is a token scoped to the two things below.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# google_login.py is stdlib only, so any python3 will do when there is no
# virtualenv (a fresh checkout on the Mac).
PY="$REPO/.venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || true)"
    [ -n "$PY" ] || { echo "no .venv and no python3 on PATH" >&2; exit 1; }
fi

# --reconsent: the client is read from ATLAS's .env, no file needed.
if [ "${1:-}" = "--reconsent" ]; then
    exec "$PY" "$REPO/bootstrap/google_login.py" --reconsent
fi

# ATLAS_GOOGLE_MAILBOX=CLIPS connects one more Gmail mailbox with the
# server's shared client: no file needed, just sign in as that account.
if { [ -n "${ATLAS_GOOGLE_MAILBOX:-}" ] || [ -n "${ATLAS_GOOGLE_YOUTUBE:-}" ]; } \
        && [ -z "${1:-}" ]; then
    exec "$PY" "$REPO/bootstrap/google_login.py"
fi

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
       ./bootstrap/google-login.sh --reconsent   (reuse ATLAS's client: adds
                                                  Search Console to an old token)

First, in the Google Cloud console for your project:

  1. Enable the APIs:
       Gmail API, Google Calendar API, Google Search Console API
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
