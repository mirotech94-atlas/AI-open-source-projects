#!/usr/bin/env bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Install Docker and prepare ATLAS. Run after 00-harden.sh and 01-netbird.sh.
set -euo pipefail
log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
[[ $EUID -eq 0 ]] || { echo "run as root"; exit 1; }

ADMIN_USER="${ADMIN_USER:-miro}"
APP_DIR="${APP_DIR:-/opt/atlas}"

log "Docker"
# The distro is read from os-release rather than assumed. atlas.storefront.test ships
# as Debian 13 (trixie), and download.docker.com/linux/ubuntu has no trixie
# suite — hardcoding "ubuntu" here makes apt-get update fail on this host.
. /etc/os-release
DOCKER_DISTRO="${ID}"                      # debian | ubuntu
DOCKER_CODENAME="${VERSION_CODENAME}"
case "$DOCKER_DISTRO" in
  debian|ubuntu) ;;
  *) echo "unsupported distro for the Docker repo: ${DOCKER_DISTRO}"; exit 1 ;;
esac

if ! command -v docker >/dev/null; then
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${DOCKER_DISTRO}/gpg" \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DOCKER_DISTRO} ${DOCKER_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
                         docker-buildx-plugin docker-compose-plugin
fi
id -u "$ADMIN_USER" >/dev/null 2>&1 && usermod -aG docker "$ADMIN_USER" || \
  log "admin user ${ADMIN_USER} does not exist yet — skipping docker group"

log "Directories"
install -d -o "$ADMIN_USER" -g "$ADMIN_USER" "$APP_DIR"/{data,logs,secrets}
chmod 700 "$APP_DIR/secrets"

log "Env file"
if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  # Generate a real password instead of shipping CHANGE_ME into production.
  PW=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)
  sed -i "s|CHANGE_ME_LONG_RANDOM|${PW}|g" "$APP_DIR/.env"
  chmod 600 "$APP_DIR/.env"
  chown "$ADMIN_USER:$ADMIN_USER" "$APP_DIR/.env"
  echo "Generated a Postgres password into .env. Fill in the API tokens."
fi

# Secrets ATLAS generates for itself. A blank ingest token fails closed — the
# endpoint refuses anything under 24 characters — so a clean bootstrap that
# skipped this produced an extension that could never authenticate.
if ! grep -qE '^BROWSER_INGEST_TOKEN=.{24,}' "$APP_DIR/.env"; then
  TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '\n=')
  if grep -q '^BROWSER_INGEST_TOKEN=' "$APP_DIR/.env"; then
    sed -i "s|^BROWSER_INGEST_TOKEN=.*|BROWSER_INGEST_TOKEN=${TOKEN}|" "$APP_DIR/.env"
  else
    echo "BROWSER_INGEST_TOKEN=${TOKEN}" >> "$APP_DIR/.env"
  fi
  echo "Generated BROWSER_INGEST_TOKEN. Put it in the browser extension's options:"
  echo "  grep '^BROWSER_INGEST_TOKEN=' $APP_DIR/.env"
fi

log "Swap"
# The box ships with no swap. Postgres, Redis, the API and a build in the same
# 2 GB will OOM without it. Idempotent: skipped if any swap already exists.
if [[ -z "$(swapon --show --noheadings 2>/dev/null)" ]]; then
  fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -qw vm.swappiness=10
  echo 'vm.swappiness=10' > /etc/sysctl.d/99-atlas-swap.conf
  log "4G swapfile active"
else
  log "swap already present — leaving it alone"
fi

log "Nightly encrypted backup"
cat > /etc/cron.daily/atlas-backup <<'CRON'
#!/bin/bash
set -euo pipefail
cd /opt/atlas
STAMP=$(date +%F)
docker compose exec -T postgres pg_dump -U atlas atlas \
  | gzip \
  | age -r "$(cat /opt/atlas/secrets/backup.pub)" \
  > "/opt/atlas/data/backup-${STAMP}.sql.gz.age"
find /opt/atlas/data -name 'backup-*.age' -mtime +30 -delete
CRON
chmod +x /etc/cron.daily/atlas-backup
apt-get install -y -qq age || true

log "Done. Next: edit /opt/atlas/.env, then 'docker compose up -d'."
