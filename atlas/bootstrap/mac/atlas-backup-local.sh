#!/bin/bash
# Nightly on Miro's Mac: dump ATLAS's Postgres, encrypt it, copy it off the Mac.
#
# The Mac version of bootstrap/backup/atlas-backup.sh. Since 2026-10-27 the
# Mac is the only copy of ATLAS's database, so a dump that stays here is not
# a backup. launchd runs this daily at 03:30 local (com.atlas.backup-local,
# installed by install-local.sh); a Mac asleep at 03:30 runs it on wake.
#
#   1. pg_dump -Fc inside the postgres container
#      -> $ATLAS_STATE_DIR/backups/atlas-<stamp>.dump (14 kept);
#   2. age -R <repo>/data/backup-age.pub -> .dump.age (14 kept). The public
#      key is the one the server used. If it is missing this run FAILS: it
#      never derives one from the private key and never prints it;
#   3. rsync the .dump.age to ATLAS_BACKUP_OFFSITE (environment, else the
#      repo's .env), e.g. atlasbak@<backup host>: (write-only rrsync).
#      Optional ATLAS_BACKUP_SSH_KEY picks the ssh key. A checksum dry run
#      then reads the remote copy back; only a match is offsite_ok;
#   4. writes <repo>/data/app/backups/status.json, which the API reads as
#      /app/data/backups/status.json (atlas/modules/backups): no dump, no
#      encryption, no offsite configured or a failed copy all read FAILING.
#
# Portable: macOS /bin/bash 3.2 and Linux (the tests run it with fakes).
set -uo pipefail

export PATH="$HOME/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
HERE="$(cd "$(dirname "$0")" && pwd)"
WT="${ATLAS_COMPOSE_DIR:-$(cd "$HERE/../.." && pwd)}"
STATE="${ATLAS_STATE_DIR:-$HOME/atlas-state}"
DIR="$STATE/backups"
STATUS_DIR="$WT/data/app/backups"
PUB="$WT/data/backup-age.pub"
KEEP=14

mkdir -p "$DIR" "$STATUS_DIR"
chmod 700 "$DIR" 2>/dev/null || true
stamp=$(date -u +%Y%m%d-%H%M)
file="$DIR/atlas-$stamp.dump"

size() { if [ -f "$1" ]; then wc -c < "$1" | tr -d ' '; else echo 0; fi; }

status() {   # dump_ok offsite_ok detail
  local ok=false
  [ "$1" = true ] && [ "$2" = true ] && ok=true
  printf '{"at":"%s","dump_ok":%s,"offsite_ok":%s,"ok":%s,"file":"%s","bytes":%s,"detail":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)" "$1" "$2" "$ok" "$(basename "$file")" \
    "$(size "$file")" "$3" > "$STATUS_DIR/status.json.tmp"
  mv "$STATUS_DIR/status.json.tmp" "$STATUS_DIR/status.json"
}

prune() {    # keep the newest $KEEP files named atlas-*<suffix>
  local n=0 f
  # atlas-*.dump never matches atlas-*.dump.age: a glob must match to the end.
  ls -1t "$DIR"/atlas-*"$1" 2>/dev/null | while IFS= read -r f; do
    n=$((n + 1))
    [ "$n" -gt "$KEEP" ] && rm -f "$f"
  done
}

# Always both files, from the repo root: anything else is another compose
# project with its own (empty) Postgres.
if ! (cd "$WT" && nice -n 10 docker compose -f docker-compose.yml \
        -f docker-compose.local.yml exec -T postgres \
        sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc') > "$file.part" \
   || [ ! -s "$file.part" ]; then
  rm -f "$file.part"
  status false false "pg_dump failed (is OrbStack running and postgres up?)"
  echo "atlas-backup-local: pg_dump failed" >&2
  exit 1
fi
mv "$file.part" "$file"
chmod 600 "$file"
prune ".dump"

if [ ! -s "$PUB" ]; then
  status true false "dumped here, but $PUB is missing, so not encrypted and not copied off the Mac"
  echo "atlas-backup-local: $PUB is missing. Copy the server's public key" >&2
  echo "(age1...) there; this script never derives it from the private key." >&2
  exit 1
fi
if ! nice -n 10 age -R "$PUB" -o "$file.age" "$file"; then
  rm -f "$file.age"
  status true false "dumped here, but encryption failed, so not copied off the Mac"
  echo "atlas-backup-local: age failed" >&2
  exit 1
fi
prune ".dump.age"

OFFSITE="${ATLAS_BACKUP_OFFSITE:-}"
if [ -z "$OFFSITE" ] && [ -f "$WT/.env" ]; then
  OFFSITE=$(ATLAS_ENV_PATH="$WT/.env" python3 "$HERE/../envlock.py" \
            ATLAS_BACKUP_OFFSITE 2>/dev/null | tr -d '\r\n') || OFFSITE=""
fi
if [ -z "$OFFSITE" ]; then
  status true false "dumped and encrypted here, but offsite not configured (ATLAS_BACKUP_OFFSITE)"
  echo "atlas-backup-local: offsite not configured; set ATLAS_BACKUP_OFFSITE" >&2
  exit 1
fi

# Without a colon rsync copies to a LOCAL path and exits 0: a "successful"
# offsite copy that never left the Mac. The server form has no colon
# (atlasbak@host); add it, as atlas-backup.sh does.
case "$OFFSITE" in *:*) ;; *) OFFSITE="$OFFSITE:" ;; esac

SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
[ -n "${ATLAS_BACKUP_SSH_KEY:-}" ] && SSH="$SSH -i $ATLAS_BACKUP_SSH_KEY"
if ! nice -n 10 rsync -q -e "$SSH" "$file.age" "$OFFSITE"; then
  status true false "dumped and encrypted here, but the off-box copy failed"
  echo "atlas-backup-local: rsync to the offsite host failed" >&2
  exit 1
fi
# A copy is proven only by reading it back: a checksum dry run (-n -c -i)
# itemizes nothing when the remote file matches byte for byte. Any output,
# or an error, means the offsite copy is not the file made here.
if diff_out=$(nice -n 10 rsync -n -c -i -e "$SSH" "$file.age" "$OFFSITE" 2>&1) \
   && [ -z "$diff_out" ]; then
  status true true "dumped, encrypted, copied off the Mac and verified by checksum"
else
  status true false "dumped and encrypted here, but the offsite copy does not match (checksum verify)"
  echo "atlas-backup-local: offsite copy failed its checksum verify" >&2
  exit 1
fi
