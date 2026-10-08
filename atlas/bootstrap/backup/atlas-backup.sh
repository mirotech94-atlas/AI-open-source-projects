#!/bin/bash
# Nightly: dump ATLAS's Postgres, keep 14 nights here, copy each off the box.
#
# Runs on the host from atlas-backup.timer (installed by install.sh), at the
# lowest CPU and IO priority -- the box has one core and renders reels. The
# dump is Postgres's custom format (pg_restore-able, compressed). Each night
# writes data/app/backups/status.json, which the API reads for the backups check:
# a dump that did not happen, or a copy that did not leave the box, reads as
# FAILING there, never as a quiet success.
#
# The off-box copy goes to the GitLab VM as user `atlasbak`, whose key may
# only write into /srv/atlas-backups (rrsync, `restrict`): this host can
# drop a file there and do nothing else on that machine. It is ENCRYPTED
# first (age, to data/backup-age.pub): the only key that decrypts it lives
# on Miro's Mac (~/.config/atlas/backup-age.key), never on either server,
# so the GitLab VM holds nothing readable. To restore (docs/restore-drill.md,
# "A real restore"), with the app STOPPED so nothing dispatches in between:
#   age -d -i ~/.config/atlas/backup-age.key atlas-X.dump.age > atlas-X.dump
#   docker compose stop api
#   docker compose exec -T postgres pg_restore -U atlas -d atlas --clean < atlas-X.dump
#   docker compose exec -T postgres psql -U atlas -d atlas -c \
#     "INSERT INTO restores (dump, restored_by) VALUES ('atlas-X.dump', 'miro')"
#   docker compose start api
# The INSERT is the restore fence (R12, kernel/restore.py): until ATLAS has
# applied it, nothing is dispatched and no card is decided; applying it
# reads restored queued sends back and holds restored pending cards.
set -uo pipefail
cd /opt/atlas || exit 1
DIR=/opt/atlas/data/app/backups   # the API reads it as /app/data/backups
KEEP=14
OFFSITE="${ATLAS_BACKUP_OFFSITE:-atlasbak@203.0.113.10}"
KEY=/root/.ssh/atlas_backup_ed25519
mkdir -p "$DIR"
stamp=$(date -u +%Y%m%d-%H%M)
file="$DIR/atlas-$stamp.dump"
status() {   # ok offsite detail
  printf '{"at":"%s","dump_ok":%s,"offsite_ok":%s,"file":"%s","bytes":%s,"detail":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$(basename "$file")" \
    "$(stat -c %s "$file" 2>/dev/null || echo 0)" "$3" > "$DIR/status.json.tmp"
  mv "$DIR/status.json.tmp" "$DIR/status.json"
  chown 10001 "$DIR" "$DIR/status.json" 2>/dev/null || true
}
if ! nice -n 19 ionice -c3 docker compose exec -T postgres \
     sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$file.part" \
   || [ ! -s "$file.part" ]; then
  rm -f "$file.part"; status false false "pg_dump failed"; exit 1
fi
mv "$file.part" "$file"
# Keep the newest $KEEP dumps here.
ls -1t "$DIR"/atlas-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
PUB=/opt/atlas/data/backup-age.pub
if [ ! -s "$PUB" ] || ! nice -n 19 age -R "$PUB" -o "$file.age" "$file"; then
  rm -f "$file.age"; status true false "dumped here, but not encrypted, so not copied off the box"
  exit 1
fi
if nice -n 19 rsync -q -e "ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=20" \
     "$file.age" "$OFFSITE:" ; then
  rm -f "$file.age"
  status true true "dumped, encrypted and copied off the box"
else
  status true false "dumped here, but the off-box copy failed"
  exit 1
fi
