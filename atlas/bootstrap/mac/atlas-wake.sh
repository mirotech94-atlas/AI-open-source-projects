#!/bin/zsh
# Keep ATLAS up through the daily 17:00 UTC Academy tutorial on a Mac that
# sleeps.
#
# launchd runs this from com.atlas.keepawake at 13:00 local; Miro's
# `sudo pmset repeat wakeorpoweron MTWRFSU 13:05:00` wakes the Mac, and
# launchd then runs the missed 13:00 interval. The scheduler already catches
# up on any later hour of a new UTC date (_maybe_academy_tutorial), so all
# this needs is a running stack and a Mac that stays awake long enough for
# Fish, Chromium and ffmpeg: a pmset wake alone re-sleeps within seconds.
#
#   1. open OrbStack, wait up to 120 s for `docker info`;
#   2. docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
#      (caddy stays off: profile "server"; the shop only if it was started);
#   3. GET 127.0.0.1:8080/healthz (up to 90 s);
#   4. caffeinate -i -t 7200.
# It never runs sudo and never publishes a port.
set -uo pipefail
export PATH="$HOME/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
wt=${ATLAS_COMPOSE_DIR:-${0:A:h:h:h}}
log() { print -r -- "$(date -u +%Y-%m-%dT%H:%M:%SZ) atlas-wake: $*" }

open -a OrbStack || log "could not open OrbStack"
for i in {1..60}; do
  docker info >/dev/null 2>&1 && break
  sleep 2
done
if ! docker info >/dev/null 2>&1; then
  log "docker is not up after 120 s; staying awake anyway"
else
  # Always both files, from the repo root: anything else is another compose
  # project with its own (empty) Postgres.
  (cd "$wt" && docker compose -f docker-compose.yml -f docker-compose.local.yml up -d) \
    || log "docker compose up failed"
  healthy=0
  for i in {1..30}; do
    if curl -fsS --max-time 5 http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
      healthy=1
      break
    fi
    sleep 3
  done
  if (( healthy )); then log "api healthy"; else log "api not healthy after 90 s"; fi
fi

exec caffeinate -i -t 7200
