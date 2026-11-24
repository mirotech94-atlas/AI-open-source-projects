# Sourced, not run. Where ATLAS lives, for every bootstrap/*.sh helper.
#
#   . "$(dirname "$0")/atlas-host.sh"
#   HOST="$(atlas_host)"
#
# The same rule as bootstrap/google_login.py default_host():
#   1. ATLAS_SSH_HOST, when set, wins ("local" or an ssh target);
#   2. "local" when this machine is not the old server (no /opt/atlas,
#      overridable with ATLAS_OPT_DIR for tests) and this checkout runs
#      ATLAS itself (docker-compose.local.yml);
#   3. otherwise the old server's ssh target.
# The server (atlas.example.com) was deleted on 2026-10-27, so on the Mac the
# answer is "local" and no script reaches for a host that is not there.

ATLAS_REMOTE_HOST="root@atlas.example.com"

atlas_host() {
    if [ -n "${ATLAS_SSH_HOST:-}" ]; then
        printf '%s\n' "$ATLAS_SSH_HOST"
        return 0
    fi
    local repo
    repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    if [ ! -e "${ATLAS_OPT_DIR:-/opt/atlas}" ] \
        && [ -f "$repo/docker-compose.local.yml" ]; then
        printf 'local\n'
        return 0
    fi
    printf '%s\n' "$ATLAS_REMOTE_HOST"
}

# For a helper that only knows the server: stop before anything happens,
# and say what to do instead. Exit 2, nothing touched.
atlas_local_unsupported() {
    echo "$1: ATLAS runs on this Mac now (the server was deleted), and this" >&2
    echo "helper only knows how to reach the server. Nothing was done." >&2
    [ -n "${2:-}" ] && echo "$2" >&2
    echo "To target a server explicitly: ATLAS_SSH_HOST=root@host $1" >&2
    exit 2
}
