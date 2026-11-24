#!/usr/bin/env bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Stand up a self-hosted NetBird control plane: management, signal, dashboard,
# TURN, and a self-hosted Zitadel for identity. Nothing depends on a third
# party — that is the point of running your own.
#
#   NETBIRD_DOMAIN=netbird.example.com bash 05-netbird-server.sh
#
# Run on a DEDICATED host. The control plane should not share a box with the
# things it protects: if that box goes down you lose the mesh you would use to
# reach it.
#
# Preflight is deliberately noisy. Every one of these checks is something that
# fails silently hours later if it is wrong.
set -euo pipefail

log()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !! \033[0m%s\n' "$*"; }
die()  { printf '\033[1;31m !! \033[0m%s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"

DOMAIN="${NETBIRD_DOMAIN:-}"
[[ -n "$DOMAIN" ]] || die "set NETBIRD_DOMAIN, e.g. NETBIRD_DOMAIN=netbird.example.com"
APP_DIR="${APP_DIR:-/opt/netbird}"

# ---------------------------------------------------------------- preflight

log "Preflight for ${DOMAIN}"

PUBLIC_IP=$(curl -fsS --max-time 10 https://api.ipify.org || true)
[[ -n "$PUBLIC_IP" ]] || die "could not determine this host's public IP"
echo "  public IP        : ${PUBLIC_IP}"

RESOLVED=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}' || true)
echo "  ${DOMAIN} resolves to: ${RESOLVED:-<nothing>}"
if [[ -z "$RESOLVED" ]]; then
  die "${DOMAIN} does not resolve. Create an A record pointing at ${PUBLIC_IP}
     and wait for it to propagate. Let's Encrypt cannot issue without it."
fi
if [[ "$RESOLVED" != "$PUBLIC_IP" ]]; then
  warn "${DOMAIN} resolves to ${RESOLVED}, not ${PUBLIC_IP}."
  warn "If this host is behind NAT that may be fine. If not, issuance will fail."
  read -rp "  Continue anyway? [y/N] " go
  [[ "$go" == [yY] ]] || exit 1
fi

# NetBird needs these. A port already in use is the commonest cause of a
# half-working install that looks fine until a peer tries to relay.
log "Ports"
BUSY=""
for spec in "80/tcp" "443/tcp" "3478/udp" "5349/tcp"; do
  port="${spec%%/*}"; proto="${spec##*/}"
  flag="-tln"; [[ "$proto" == udp ]] && flag="-uln"
  if ss $flag 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$"; then
    BUSY="${BUSY} ${spec}"
  fi
done
if [[ -n "$BUSY" ]]; then
  warn "already in use:${BUSY}"
  warn "NetBird's own reverse proxy wants 80 and 443. Free them, or put NetBird"
  warn "behind the existing proxy (see docs/netbird-behind-nginx.md)."
  read -rp "  Continue anyway? [y/N] " go
  [[ "$go" == [yY] ]] || exit 1
else
  echo "  80, 443, 3478/udp, 5349 are free"
fi

log "Resources"
MEM=$(free -m | awk '/Mem:/{print $2}')
echo "  RAM: ${MEM}MB, disk free: $(df -h / | awk 'NR==2{print $4}')"
# Zitadel plus its database plus management and signal. Below this it swaps.
[[ "$MEM" -ge 3500 ]] || warn "under 4GB of RAM — Zitadel and its database will be tight"

# ------------------------------------------------------------------ docker

log "Docker"
if ! command -v docker >/dev/null; then
  . /etc/os-release
  case "$ID" in
    debian|ubuntu) ;;
    *) die "unsupported distro for the Docker repo: ${ID}" ;;
  esac
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg jq
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" \
    | gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
                         docker-buildx-plugin docker-compose-plugin
fi
docker compose version >/dev/null 2>&1 \
  || die "docker compose v2 is required (install docker-compose-plugin)"
docker --version

# ------------------------------------------------------------------ install

log "Fetching NetBird's installer into ${APP_DIR}"
install -d -m 0750 "$APP_DIR"
cd "$APP_DIR"

# NetBird ships an installer that wires the management and signal services,
# the dashboard, coturn and an identity provider together with matching
# secrets. Rolling our own compose would mean re-deriving that wiring by hand
# and getting it subtly wrong; this pins the upstream one instead.
#
# NOTE: getting-started-with-zitadel.sh was RETIRED upstream and now refuses to
# run. The current installer embeds a Dex identity provider, which is still
# fully self-hosted and far lighter than Zitadel plus its database. Zitadel can
# be attached later as an external IdP from the NetBird dashboard if you want
# it: https://docs.netbird.io/selfhosted/identity-providers/zitadel
curl -fsSL -o getting-started.sh \
  https://github.com/netbirdio/netbird/releases/latest/download/getting-started.sh
[[ -s getting-started.sh ]] || die "download failed"
chmod +x getting-started.sh

log "Running the installer for ${DOMAIN}"
echo "  This pulls several images. It takes a while on a single core."
NETBIRD_DOMAIN="$DOMAIN" bash getting-started.sh

log "Done"
cat <<NOTE

  Dashboard : https://${DOMAIN}
  Credentials were written by the installer — read them with:
      grep -riE 'password|admin|secret' ${APP_DIR}/*.env ${APP_DIR}/docker-compose.yml 2>/dev/null | head

  Next:
    1. Sign in, change the admin password immediately.
    2. Create a setup key (Settings -> Setup Keys), then enrol ATLAS:
         ssh root@atlas.example.com
         bash /opt/atlas/bootstrap/01-netbird.sh <setup-key>
    3. Put the resulting wt0 address in /opt/atlas/.env as MESH_BIND,
       then: docker compose up -d
    4. Only once you have confirmed mesh access, close public SSH:
         bash /opt/atlas/bootstrap/01-netbird.sh --close-ssh

NOTE
