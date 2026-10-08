#!/usr/bin/env bash
# Put a secret into ATLAS's .env without it ever passing through a chat,
# a shell history, a log line or a screen.
#
#   ./bootstrap/set-secret.sh GUARDIAN_API_TOKEN
#
# It prompts with `read -rs` (nothing echoes), writes straight to the
# server's .env over SSH, restarts the API and re-runs the checks that
# variable unlocks so you can see it work. The value is never printed back.
#
# Why this exists: a password pasted into a conversation is in that
# conversation forever, and the only fix afterwards is to rotate it. That
# already happened once here.
set -euo pipefail

HOST="${ATLAS_SSH_HOST:-root@atlas.example.com}"
ENV_PATH="${ATLAS_ENV_PATH:-/opt/atlas/.env}"
COMPOSE_DIR="${ATLAS_COMPOSE_DIR:-/opt/atlas}"

NAME="${1:-}"
if [ -z "$NAME" ]; then
    echo "usage: $0 VARIABLE_NAME" >&2
    echo >&2
    echo "See CONFIGURE.md for which variables unlock what." >&2
    exit 2
fi
case "$NAME" in
    [A-Z_][A-Z0-9_]*) ;;
    *)
        # The most likely mistake is the one that happened: passing the
        # SECRET as the argument. That put a GitLab token into shell
        # history and nothing into .env, and the checks stayed red while
        # everything looked done. Say exactly that, and say what to do
        # about the value now sitting in history.
        echo "'$NAME' is not a variable name." >&2
        case "$NAME" in
            glpat-*|sk-*|sk_*|rk_live_*|xoxb-*|gapi_*|k9_*|GOCSPX-*|AC[0-9a-f]*)
                echo >&2
                echo "That looks like a SECRET, not a name. The argument is the" >&2
                echo "variable to set; the value is pasted at the prompt so it" >&2
                echo "never touches a command line. It is now in your shell" >&2
                echo "history -- clear it with:" >&2
                echo "    history -d \$(history 1 | awk '{print \$1}')" >&2
                echo "or rotate the credential." >&2 ;;
        esac
        echo >&2
        echo "usage: $0 VARIABLE_NAME     e.g.  $0 GITLAB_TOKEN" >&2
        exit 2 ;;
esac

printf 'Paste the value for %s (it will not be shown): ' "$NAME" >&2
IFS= read -rs VALUE
printf '\n' >&2
[ -n "$VALUE" ] || { echo "empty; nothing written" >&2; exit 1; }

# docker compose performs ${VAR} interpolation on .env, so a literal '$' in a
# secret is eaten and the variable silently arrives truncated or empty. This
# cost a day once: an argon2 hash containing '$' reached the container as ""
# and every correct password was rejected, with no error anywhere.
# Refuse a value that would be mangled in a way escaping cannot fix.
case "$VALUE" in
    *$'\n'*) echo "value contains a newline; .env cannot hold that" >&2; exit 1 ;;
esac

# Write remotely, under the server's .env lock. The value travels over
# ssh's stdin, so it is not in the command line, not in `ps`, and not in
# either shell's history. envlock.py holds one advisory lock across both the
# read and the write: two writers in the same minute once silently restored
# each other's stale copies, and the paste "succeeded" while the checks
# stayed red.
printf '%s=%s\n' "$NAME" "$VALUE" | ssh "$HOST" "python3 /opt/atlas/bootstrap/envlock.py"
unset VALUE ESCAPED

echo "restarting the API so it picks the value up..." >&2
ssh "$HOST" "cd '$COMPOSE_DIR' && docker compose up -d api >/dev/null 2>&1"
sleep 6

# Verify by running the checks, not by printing the variable back. A value
# that is present but wrong is the failure this is meant to catch, and
# echoing it would defeat the whole point of the script.
echo >&2
echo "re-running checks:" >&2
ssh "$HOST" "cd '$COMPOSE_DIR' && docker compose exec -T api python -m atlas.cli connect" \
    || echo "(could not reach the API; check it started)" >&2
