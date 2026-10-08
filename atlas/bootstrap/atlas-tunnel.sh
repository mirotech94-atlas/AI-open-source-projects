#!/usr/bin/env bash
# Reach the ATLAS dashboard at https://atlas.example.com from this Mac, before
# NetBird is up — or any time the mesh is down.
#
#   sudo bash atlas-tunnel.sh
#
# It forwards 443 to the ATLAS box over SSH and points atlas.example.com at your
# own machine while the tunnel is open. Nothing is exposed: the only way in is
# your SSH key, and the dashboard still refuses every source that is not the
# mesh or this tunnel.
set -euo pipefail

HOST="${ATLAS_HOST:-203.0.113.10}"
NAME="${ATLAS_NAME:-atlas.example.com}"
KEY="${ATLAS_KEY:-$HOME/.ssh/id_rsa}"
HOSTS=/etc/hosts
MARK="# added by atlas-tunnel"

[[ $EUID -eq 0 ]] || { echo "run with sudo — forwarding port 443 needs root"; exit 1; }

cleanup() {
  echo
  echo "Closing the tunnel and restoring ${HOSTS}…"
  sed -i '' "/${MARK}/d" "$HOSTS" 2>/dev/null || sed -i "/${MARK}/d" "$HOSTS"
  echo "Done. ${NAME} resolves normally again."
}
trap cleanup EXIT INT TERM

if ! grep -q "$MARK" "$HOSTS"; then
  printf '127.0.0.1 %s %s\n' "$NAME" "$MARK" >> "$HOSTS"
fi
# macOS caches DNS; without this the browser keeps the public address.
dscacheutil -flushcache 2>/dev/null || true
killall -HUP mDNSResponder 2>/dev/null || true

echo "Tunnel open. Go to:  https://${NAME}"
echo "Leave this running. Ctrl-C closes it and undoes the hosts entry."
echo
# While the entry is in place, ${NAME} points at THIS Mac. Anything else that
# uses the name resolves here too — notably ssh, which then offers your own
# laptop's host key and produces a REMOTE HOST IDENTIFICATION HAS CHANGED
# warning that looks exactly like an attack.
echo "NOTE: while this is open, ${NAME} points at this Mac."
echo "      For ssh, use the address instead:"
echo "          ssh root@${HOST}"
echo "      Using the name will warn about a changed host key. That is this"
echo "      tunnel, not an attack."
echo
# NOT exec: exec replaces this shell, which would discard the EXIT trap and
# leave the hosts entry behind after Ctrl-C. Run it as a child instead, so the
# cleanup always happens.
ssh -N -L 443:127.0.0.1:443 \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -i "$KEY" "root@${HOST}" || true
