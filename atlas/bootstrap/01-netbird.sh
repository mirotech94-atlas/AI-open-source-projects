#!/usr/bin/env bash
# Enroll the box in the NetBird mesh. Pass a setup key as $1.
#
#   NETBIRD_MGMT_URL=https://netbird.example.com bash 01-netbird.sh <setup-key>
#
# NETBIRD_MGMT_URL is REQUIRED when you run your own control plane. Without it
# `netbird up` silently enrols against NetBird's hosted service instead, which
# looks like success and puts the box on somebody else's mesh.
# After this succeeds and you have verified mesh access, run with --close-ssh
# to drop public SSH entirely.
set -euo pipefail

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
[[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }

if [[ "${1:-}" == "--close-ssh" ]]; then
  ip -4 addr show wt0 >/dev/null 2>&1 || { echo "wt0 not up; refusing"; exit 1; }
  MESH_IP=$(ip -4 -o addr show wt0 | awk '{print $4}' | cut -d/ -f1)
  # Read the port sshd is ACTUALLY on rather than assuming the hardened one.
  # Guessing wrong here removes the rule for a port nothing listens on and
  # leaves the real one open, or locks you out of a box you cannot reach.
  SSH_PORT="${SSH_PORT:-$(ss -tlnp 2>/dev/null | awk '/sshd/{split($4,a,":"); print a[length(a)]; exit}')}"
  SSH_PORT="${SSH_PORT:-22}"
  log "Mesh IP is ${MESH_IP}. sshd is on port ${SSH_PORT}."

  command -v ufw >/dev/null || { echo "ufw is not installed; run 00-harden.sh first"; exit 1; }

  echo "Before closing public SSH, confirm from ANOTHER terminal that this works:"
  echo "    ssh -p ${SSH_PORT} root@${MESH_IP}"
  read -rp "Confirmed mesh SSH works? [y/N] " go
  [[ "$go" == [yY] ]] || { echo "aborted; nothing changed"; exit 1; }

  ufw delete allow "${SSH_PORT}"/tcp || true
  ufw allow in on wt0 to any port "${SSH_PORT}" proto tcp comment 'ssh over mesh'
  ufw reload
  echo "Public SSH closed. Keep this session open until you have re-tested."
  exit 0
fi

SETUP_KEY="${1:?usage: NETBIRD_MGMT_URL=https://... 01-netbird.sh <setup-key>}"
MGMT_URL="${NETBIRD_MGMT_URL:-}"

if [[ -z "$MGMT_URL" ]]; then
  echo "!! NETBIRD_MGMT_URL is not set."
  echo "!! Without it this box joins NetBird's HOSTED service, not your own"
  echo "!! control plane — and it will look like it worked."
  read -rp "   Really use the hosted service? [y/N] " go
  [[ "$go" == [yY] ]] || exit 1
fi

log "Installing NetBird"
curl -fsSL https://pkgs.netbird.io/install.sh | sh

log "Joining mesh${MGMT_URL:+ at ${MGMT_URL}}"
if [[ -n "$MGMT_URL" ]]; then
  netbird up --setup-key "$SETUP_KEY" --management-url "$MGMT_URL"
else
  netbird up --setup-key "$SETUP_KEY"
fi
sleep 5
netbird status

log "Mesh interface:"
ip -4 -o addr show wt0 || echo "wt0 not up yet — check 'netbird status'"

MESH_IP=$(ip -4 -o addr show wt0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)

cat <<NOTE

Next:
  1. Put the mesh address in ATLAS's .env so the API binds to it:
       sed -i 's|^MESH_BIND=.*|MESH_BIND=${MESH_IP:-<wt0-address>}|' /opt/atlas/.env
       cd /opt/atlas && docker compose up -d
     Until you do this the API stays on 127.0.0.1 and the dashboard is
     reachable only from the box itself.

  2. Confirm you can reach this box over NetBird from your laptop:
       ssh root@${MESH_IP:-<wt0-address>}
       open http://${MESH_IP:-<wt0-address>}:8080/

  3. ONLY THEN:  sudo bash bootstrap/01-netbird.sh --close-ssh
     That removes public SSH. Do not run it until step 2 works.
NOTE
