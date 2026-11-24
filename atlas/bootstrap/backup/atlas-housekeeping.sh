#!/bin/bash
# Old server (/opt/atlas), retired 2026-10-28: ATLAS runs from ~/atlas-agency on the Mac.
# Weekly: keep the disk from filling. Runs from atlas-housekeeping.timer.
#
#   * Docker build cache beyond the newest 3 GB, and dangling images -- every
#     deploy leaves ~1 GB behind, and that, not media, is what fills the disk.
#     A size budget, not an age: a busy week of deploys is all "recent".
#   * A reel posted more than 30 days ago: its 1080p delivery file is deleted
#     (the platforms hold the post), and its 4K master moves off the box to
#     the GitLab VM (media/), then is deleted here.
#   * Banked Veo clips of packaged reels that have published.
#
# Writes data/app/backups/housekeeping.json, which the API's disk check reads.
set -uo pipefail
cd /opt/atlas || exit 1
DIR=/opt/atlas/data/app/backups
MEDIA=/opt/atlas/data/app/content
OFFSITE="${ATLAS_BACKUP_OFFSITE:-atlasbak@162.220.52.147}"
KEY=/root/.ssh/atlas_backup_ed25519
mkdir -p "$DIR"
before=$(df --output=avail -B1 / | tail -1)
# Never while an image is being built: pruning under a build breaks it.
if ! pgrep -f "docker(-compose| compose| buildx| build)" >/dev/null; then
  nice -n 19 docker builder prune -f --keep-storage 3GB >/dev/null 2>&1
  nice -n 19 docker image prune -f >/dev/null 2>&1
fi
psql() { docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$1\""; }
moved=0; removed=0; failed=0
for id in $(psql "SELECT media_id FROM reel_posts WHERE media_id IS NOT NULL GROUP BY media_id HAVING max(at) < now() - interval '30 days'"); do
  master="$MEDIA/$id-2160.mp4"
  if [ -f "$master" ]; then
    if nice -n 19 rsync -q -e "ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20" "$master" "$OFFSITE:media/"; then
      rm -f "$master"; moved=$((moved + 1))
    else
      failed=$((failed + 1))
    fi
  fi
  if [ -f "$MEDIA/$id.mp4" ]; then rm -f "$MEDIA/$id.mp4"; removed=$((removed + 1)); fi
done
for job in $(psql "SELECT job_id FROM reel_calendar WHERE status = 'published'"); do
  if [ -d "$MEDIA/calendar/$job" ]; then rm -rf "${MEDIA:?}/calendar/$job"; fi
done
after=$(df --output=avail -B1 / | tail -1)
printf '{"at":"%s","freed_bytes":%s,"masters_moved":%s,"deliveries_removed":%s,"moves_failed":%s}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$((after - before))" "$moved" "$removed" "$failed" \
  > "$DIR/housekeeping.json.tmp" && mv "$DIR/housekeeping.json.tmp" "$DIR/housekeeping.json"
chown 10001 "$DIR/housekeeping.json" 2>/dev/null || true
[ "$failed" -eq 0 ]
