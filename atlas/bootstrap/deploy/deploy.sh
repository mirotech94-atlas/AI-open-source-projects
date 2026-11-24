#!/bin/bash
# Deploy one commit of ATLAS to this host. Called by GitLab CD after CI passes
# on phases-12-14 (the key that calls it may run nothing else), or by hand:
#   /opt/atlas/bootstrap/deploy/deploy.sh <commit sha>
#   ATLAS_DEPLOY_DRY=1 deploy.sh <sha>   # rehearse: say what would change, change nothing
#
# the owner, 2026-09-28: deploys automated, so no session runs `docker compose`
# on the api by hand again (three at once left it stuck in "Created"). This
# is the deploy session's own proven procedure, written down:
#
#   1. One deploy at a time (flock); a second waits, then deploys its own sha.
#   2. Wait until nothing is running that a rebuild would kill: ffmpeg anywhere
#      on the host (the api's renders and the host reel jobs alike), a fresh
#      'rendering' media row, or an executor run started in the last 30 min
#      with no finish (a LinkedIn publish died mid-run once, #181/#182).
#      Twenty minutes at most, then no deploy: CD goes red and says why.
#   3. `git archive <sha>` from GitLab into a scratch dir, and rsync it over
#      /opt/atlas with --checksum -- root files included (Caddyfile,
#      docker-compose.yml, Dockerfile, pyproject.toml ...). No --delete: the
#      host keeps untracked state (data/, logs/, the .env backups). A file
#      the new commit REMOVED since the last deployed one is removed by name.
#      Never written: .env and .env.* other than .env.example, data/, .git/.
#   4. ATLAS_DEPLOY_SHA in .env (boot and /system read it).
#   5. Tag atlas-api:previous, build, apply db/schema.sql from the new image
#      (idempotent), recreate api; caddy only if the Caddyfile changed, and
#      validated (`caddy validate`) before it is even copied into place.
#   6. api must report healthy within 5 minutes, or the previous image comes
#      back and the deploy exits non-zero.
set -euo pipefail
SHA="${1:?usage: deploy.sh <sha>}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "not a full commit sha: $SHA"; exit 2; }
APP=/opt/atlas
SRC=/opt/atlas-src
REPO="${ATLAS_DEPLOY_REPO:-git@aicode.example.com:root/atlas.git}"
KEY=/root/.ssh/atlas_deploy_read_ed25519
LOG=$APP/logs/deploy.log
mkdir -p "$APP/logs"
exec > >(tee -a "$LOG") 2>&1
echo "== $(date -u +%FT%TZ) deploy $SHA"

exec 9>/var/lock/atlas-deploy.lock
flock -w 3600 9 || { echo "another deploy held the lock for an hour"; exit 1; }
cd "$APP"

sql() {
  docker compose exec -T postgres sh -c \
    "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\"" 2>/dev/null || echo 1
}
busy() {
  local n
  n=$(ps -eo args | grep -c "[f]fmpeg" || true); [ "${n:-0}" -gt 0 ] && { echo "ffmpeg is running ($n)"; return 0; }
  n=$(sql "SELECT count(*) FROM content_media WHERE status = 'rendering' AND at > now() - interval '30 minutes'")
  [ "${n:-1}" -gt 0 ] && { echo "a render is in flight"; return 0; }
  n=$(sql "SELECT count(*) FROM actions_log s WHERE s.event = 'execute.start' AND s.at > now() - interval '30 minutes' AND NOT EXISTS (SELECT 1 FROM actions_log f WHERE f.event IN ('execute.finish', 'execute.error', 'execute.abandoned') AND f.subject = s.subject AND f.at > s.at)")
  [ "${n:-1}" -gt 0 ] && { echo "an executor run is open ($n)"; return 0; }
  return 1
}
for _ in $(seq 1 20); do busy || break; sleep 60; done
if busy; then echo "still busy after 20 minutes; not deploying"; exit 1; fi

export GIT_SSH_COMMAND="ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
[ -d "$SRC/.git" ] || git clone -q --no-checkout "$REPO" "$SRC"
git -C "$SRC" fetch -q origin
git -C "$SRC" cat-file -e "$SHA^{commit}" || { echo "$SHA is not on GitLab"; exit 1; }
SCRATCH=$(mktemp -d); trap 'rm -rf "$SCRATCH"' EXIT
git -C "$SRC" archive "$SHA" | tar -x -C "$SCRATCH"
rm -f "$SCRATCH/.env"; find "$SCRATCH" -maxdepth 1 -name '.env.*' ! -name .env.example -delete

old_caddy=$(sha256sum Caddyfile | cut -d' ' -f1)
new_caddy=$(sha256sum "$SCRATCH/Caddyfile" | cut -d' ' -f1)
# A new Caddyfile is validated BEFORE it is copied into place: an invalid one
# on disk would take the site down at caddy's next restart.
if [ "$new_caddy" != "$old_caddy" ] && ! docker run --rm --env-file .env \
     -v "$SCRATCH/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine \
     caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  echo "the new Caddyfile does not validate; not deploying"; exit 1
fi
PREV=$(sed -n 's/^ATLAS_DEPLOY_SHA=//p' .env | tail -1)
if [ "${ATLAS_DEPLOY_DRY:-0}" = 1 ]; then
  echo "DRY RUN from ${PREV:-unknown} to $SHA -- files that would change:"
  rsync -rlt --checksum --dry-run --itemize-changes --include .env.example \
    --exclude .env --exclude '.env.*' --exclude data/ --exclude logs/ --exclude .git/ \
    "$SCRATCH"/ "$APP"/ | grep -v '^\.d' | head -60
  [ -n "$PREV" ] && git -C "$SRC" cat-file -e "$PREV^{commit}" 2>/dev/null \
    && git -C "$SRC" diff --name-only --diff-filter=D "$PREV" "$SHA" | sed 's/^/would remove: /'
  echo "Caddyfile changes: $([ "$new_caddy" != "$old_caddy" ] && echo yes || echo no)"
  exit 0
fi
rsync -rlt --checksum --include .env.example --exclude .env --exclude '.env.*' \
  --exclude data/ --exclude logs/ --exclude .git/ "$SCRATCH"/ "$APP"/
# Files the new commit removed since the last deploy go too -- by name only.
if [ -n "$PREV" ] && git -C "$SRC" cat-file -e "$PREV^{commit}" 2>/dev/null; then
  git -C "$SRC" diff --name-only --diff-filter=D "$PREV" "$SHA" | while read -r f; do
    case "$f" in .env|.env.*|data/*|logs/*) continue ;; esac
    rm -f -- "$APP/$f"
  done
fi
if grep -q '^ATLAS_DEPLOY_SHA=' .env; then
  sed -i "s/^ATLAS_DEPLOY_SHA=.*/ATLAS_DEPLOY_SHA=$SHA/" .env
else
  printf '\nATLAS_DEPLOY_SHA=%s\n' "$SHA" >> .env
fi

docker image inspect atlas-api:latest >/dev/null 2>&1 \
  && docker tag atlas-api:latest atlas-api:previous
nice -n 10 docker compose build -q api
docker compose run --rm --no-deps -T api python -m atlas.cli init-db
docker compose up -d --no-deps api

healthy() {   # the container's own healthcheck, which calls /healthz
  sleep 10
  for _ in $(seq 1 60); do
    docker compose ps api --format '{{.Status}}' | grep -q '(healthy)' && return 0
    sleep 5
  done
  return 1
}
if ! healthy; then
  echo "NOT healthy after 5 minutes: rolling back to atlas-api:previous"
  docker tag atlas-api:previous atlas-api:latest
  [ -n "$PREV" ] && sed -i "s/^ATLAS_DEPLOY_SHA=.*/ATLAS_DEPLOY_SHA=$PREV/" .env
  docker compose up -d --no-deps api
  healthy && echo "rolled back; the previous build is serving" || echo "rollback ALSO unhealthy"
  exit 1
fi

if [ "$new_caddy" != "$old_caddy" ]; then
  docker compose up -d --force-recreate --no-deps caddy
fi
# Every deploy builds, and the build cache filled the 39 GB disk in a day of
# auto-deploys (2026-09-28: Postgres PANICked, "No space left on device").
# The weekly housekeeping is not enough on its own; trim after each deploy.
# Dangling images only: atlas-api:previous is tagged and stays for rollback.
docker builder prune -f --keep-storage 1GB >/dev/null 2>&1 || true
docker image prune -f >/dev/null 2>&1 || true
echo "disk after deploy: $(df -h / | awk 'NR==2 {print $5 " used, " $4 " free"}')"
echo "== deployed $SHA, healthy"
