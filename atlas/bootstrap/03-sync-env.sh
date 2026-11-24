#!/usr/bin/env bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Reconcile .env with .env.example after an upgrade.
#
# New releases add variables. This appends any key that .env.example has and
# .env does not, and NEVER rewrites a value that is already set — so it is safe
# to run on a live box with real credentials in it.
#
# Run from the app directory (default /opt/atlas). Idempotent.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/atlas}"
cd "$APP_DIR"

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

[[ -f .env.example ]] || { echo "no .env.example in ${APP_DIR}"; exit 1; }
if [[ ! -f .env ]]; then
  cp .env.example .env
  log "created .env from the example — fill in the credentials"
fi

log "Adding keys that .env is missing"
added=0
while IFS= read -r line; do
  case "$line" in
    ''|\#*) continue ;;
  esac
  key="${line%%=*}"
  # A key with no '=' is not a setting.
  [[ "$key" == "$line" ]] && continue
  if ! grep -q "^${key}=" .env; then
    printf '%s\n' "$line" >> .env
    echo "  + ${key}"
    added=$((added + 1))
  fi
done < .env.example
[[ $added -eq 0 ]] && echo "  nothing to add"

log "Generating secrets that are still blank"
# Postgres password, if the placeholder survived.
if grep -q 'CHANGE_ME_LONG_RANDOM' .env; then
  PW=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)
  sed -i "s|CHANGE_ME_LONG_RANDOM|${PW}|g" .env
  echo "  + POSTGRES_PASSWORD"
fi
# Browser extension ingest token. The endpoint refuses anything shorter than
# 24 characters, so a blank value fails closed rather than allowing everything.
if ! grep -qE '^BROWSER_INGEST_TOKEN=.{24,}' .env; then
  TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '\n=')
  sed -i "s|^BROWSER_INGEST_TOKEN=.*|BROWSER_INGEST_TOKEN=${TOKEN}|" .env
  echo "  + BROWSER_INGEST_TOKEN (put this in the browser extension's options)"
fi

# Session signing key. Not a human secret — generated, never typed.
if ! grep -qE '^SESSION_SECRET=.{32,}' .env; then
  SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '\n=')
  sed -i "s|^SESSION_SECRET=.*|SESSION_SECRET=${SECRET}|" .env
  echo "  + SESSION_SECRET"
fi

if ! grep -qE '^DASHBOARD_PASSWORD_HASH=.+' .env; then
  echo
  echo "  !! No dashboard password is set. The dashboard will refuse every"
  echo "     login until you run:"
  echo "       docker compose exec api python -m atlas.cli set-password"
fi

chmod 600 .env
log "Done. Review .env, then: docker compose up -d --build"
echo "Secrets are not printed here on purpose. Read them with:"
echo "  grep '^BROWSER_INGEST_TOKEN=' ${APP_DIR}/.env"
