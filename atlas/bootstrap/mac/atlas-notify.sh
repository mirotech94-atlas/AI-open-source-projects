#!/bin/bash
# Laptop notifications for the portal (Miro 2026-10-30): a read-only look at the
# local Postgres every minute; a macOS notification for each NEW pending card
# and for a card that was held or failed. Acts on nothing (invariant 1).
# First run (--seed) records what exists without notifying.
set -u
STATE="$HOME/.atlas-notify.seen"; touch "$STATE"
DOCKER=/usr/local/bin/docker; [ -x "$DOCKER" ] || DOCKER=/opt/homebrew/bin/docker
rows=$("$DOCKER" exec atlas-agency-postgres-1 psql -U atlas -d atlas -At -F '|' -c \
  "select id||':'||status, id, action, left(replace(reason,E'\n',' '),110), status from proposals
   where (status='pending' and expires_at>now())
      or (status in ('held','failed') and created_at>now()-interval '1 day')" 2>/dev/null) || exit 0
while IFS='|' read -r key id action reason status; do
  [ -z "${key:-}" ] && continue
  grep -qxF "$key" "$STATE" && continue
  echo "$key" >> "$STATE"
  [ "${1:-}" = "--seed" ] && continue
  case "$status" in
    pending) title="Card waiting: $action" ;;
    held)    title="Card held: $action" ;;
    *)       title="Card failed: $action" ;;
  esac
  /opt/homebrew/bin/terminal-notifier -title "ATLAS Agency" -subtitle "$title" \
    -message "$reason" -open "http://localhost:8080" -group "atlas-$id" -sound default
done <<< "$rows"
