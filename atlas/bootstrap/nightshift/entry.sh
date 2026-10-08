#!/bin/bash
# The one thing that runs inside the overnight sandbox container.
#
# It starts as root and does exactly three things with privileges, none of
# which involve the model:
#
#   1. clone REPO with the git token, then strip the token from the clone's
#      remote so nothing in the working tree carries it;
#   2. run `claude -p` as the unprivileged user `night`, with a CLEAN
#      environment (`env -i`): the model never sees GIT_TOKEN -- not in its
#      env, not in .git/config, and not in /proc/1/environ, which only root
#      can read;
#   3. push ONLY `refs/heads/$BRANCH` (which must match nightshift/job-N) from
#      a fresh bare repository root creates itself, with hooks disabled. The
#      model's repository is only ever FETCHED from, never pushed from, so a
#      hook, an `insteadOf` rewrite or a `pushurl` it planted in .git/config
#      cannot run as root or redirect the push.
#
# Why this matters (2026-09-27): the token handed to nightshift may be far
# broader than one repository. Branch-only pushing is enforced here, in the
# one place that holds the token, rather than trusted to the token's scope
# or the model's good behaviour.
set -euo pipefail

: "${REPO:?REPO not set}"
: "${BRANCH:?BRANCH not set}"
: "${TASK:?TASK not set}"

if ! [[ "$BRANCH" =~ ^nightshift/job-[A-Za-z0-9-]+$ ]]; then
    echo "refusing branch '$BRANCH': only nightshift/job-<id> may be pushed" >&2
    exit 5
fi

WORK="/home/night/work"
TOKEN="${GIT_TOKEN:-}"
KEY="${ANTHROPIC_API_KEY:-}"
unset GIT_TOKEN ANTHROPIC_API_KEY

authed() {   # REPO with the token, for root's own clone and push only
    if [ -n "$TOKEN" ] && [[ "$REPO" == https://* ]]; then
        echo "https://oauth2:${TOKEN}@${REPO#https://}"
    else
        echo "$REPO"
    fi
}

git clone --quiet "$(authed)" "$WORK"
git -C "$WORK" remote set-url origin "$REPO"
git -C "$WORK" checkout -q -b "$BRANCH"
chown -R night:night "$WORK"

as_night() {
    # A clean environment, except the egress proxy (not a secret): the
    # network is --internal and the proxy is the only way to reach the API.
    runuser -u night -- env -i HOME=/home/night PATH="$PATH" \
        ANTHROPIC_API_KEY="$KEY" \
        HTTPS_PROXY="${HTTPS_PROXY:-}" HTTP_PROXY="${HTTP_PROXY:-}" \
        https_proxy="${https_proxy:-}" http_proxy="${http_proxy:-}" \
        NO_PROXY="${NO_PROXY:-}" no_proxy="${no_proxy:-}" "$@"
}

set +e
as_night bash -c 'cd /home/night/work && claude -p "$1" --dangerously-skip-permissions' _ "$TASK"
CLAUDE_STATUS=$?
# Nothing the model started may still be running when root reads its work:
# a background process left behind could rewrite the repository mid-push.
pkill -9 -u night 2>/dev/null
set -e

PUSH_FAILED=0
if as_night git -C "$WORK" status --porcelain | grep -q .; then
    as_night git -C "$WORK" -c core.hooksPath=/dev/null add -A \
        || { echo "git add failed" >&2; PUSH_FAILED=1; }
    if [ "$PUSH_FAILED" -eq 0 ]; then
        as_night git -C "$WORK" -c core.hooksPath=/dev/null \
            -c user.name="ATLAS Nightshift" -c user.email=nightshift@atlas.local \
            commit -q -m "nightshift: ${TASK:0:200}" \
            || { echo "commit failed" >&2; PUSH_FAILED=1; }
    fi
fi

if [ "$PUSH_FAILED" -eq 0 ]; then
    if [ -z "$TOKEN" ]; then
        echo "no GIT_TOKEN — branch committed locally but not pushed" >&2
    else
        pkill -9 -u night 2>/dev/null || true
        BARE="$(mktemp -d /tmp/push.XXXXXX)"
        git init -q --bare "$BARE"
        # The workspace belongs to `night`; git refuses to read another
        # user's repository unless a protected (global) config says so, and
        # `-c` is not passed on to the local fetch helper. So this one read
        # runs with a throwaway HOME whose .gitconfig names the workspace. It
        # is a fetch: the workspace's hooks never run, and its config (an
        # insteadOf, a pushurl) is never consulted for the push below.
        SAFEHOME="$(mktemp -d /tmp/safe.XXXXXX)"
        printf '[safe]\n\tdirectory = %s\n\tdirectory = %s/.git\n' \
            "$WORK" "$WORK" > "$SAFEHOME/.gitconfig"
        if HOME="$SAFEHOME" git -C "$BARE" -c core.hooksPath=/dev/null \
               fetch -q --no-tags "$WORK" "+refs/heads/$BRANCH:refs/heads/$BRANCH"; then
            git -C "$BARE" -c core.hooksPath=/dev/null push -q --no-follow-tags \
                "$(authed)" "refs/heads/$BRANCH:refs/heads/$BRANCH" \
                || { echo "push failed" >&2; PUSH_FAILED=1; }
        else
            echo "could not read the branch back from the workspace" >&2
            PUSH_FAILED=1
        fi
        rm -rf "$BARE" "$SAFEHOME"
    fi
fi

if [ "$CLAUDE_STATUS" -ne 0 ]; then
    exit "$CLAUDE_STATUS"
elif [ "$PUSH_FAILED" -ne 0 ]; then
    exit 1
fi
exit 0
