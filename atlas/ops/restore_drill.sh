#!/usr/bin/env bash
# ATLAS restore drill (RA-H2). Run BY A HUMAN on the ATLAS host. Not scheduled.
#
# Proves that the newest local Postgres dump restores, in a throwaway
# container that has no network and no view of production, then reports what
# came back: key tables, row counts, the audit hash chain, and every
# pending/approved proposal and in-flight action that must be reconciled
# before anything restored from this dump is allowed to dispatch.
#
#   ops/restore_drill.sh              run the drill
#   ops/restore_drill.sh --dry-run    run the read-only preflight, print the plan
#   ops/restore_drill.sh --dump FILE  use FILE instead of the newest dump
#
# Safety, in order:
#   1. It REFUSES (exit 2) before starting anything when the Docker data root
#      or the backup directory has under MIN_FREE_GB free, or when the Docker
#      data root has less than 3x the dump size free.
#   2. The container: `--network none`, a unique name (restore-drill-atlas-*),
#      a label, `--pull never`, and one bind mount only, the dump file,
#      read-only. No prod volume, network or container is referenced except
#      a read-only `docker inspect` of atlas-postgres-1 to learn its image.
#   3. A trap removes that exact container and its anonymous volume
#      (`docker rm -fv NAME`) on every exit path, including Ctrl-C.
#
# Exit: 0 PASS, 1 FAIL, 2 refused before starting (or bad usage).
#
# Environment overrides (defaults are the ATLAS host's):
#   BACKUP_DIR        /opt/atlas/data/app/backups
#   DOCKER_DATA_ROOT  `docker info` DockerRootDir. Docker Desktop keeps it
#                     inside a VM, so on a Mac set this to a local path
#   MIN_FREE_GB       5
#   DRILL_IMAGE       image of atlas-postgres-1, else pgvector/pgvector:pg16
#   DRILL_MEMORY      768m     DRILL_CPUS  0.5
#   READY_TIMEOUT_S   180
#   DRILL_RESULT_FILE $BACKUP_DIR/restore_drill.json
#
# Result file (plan S05): a drill that ran -- not --dry-run, not refused,
# not interrupted -- writes {"at", "ok", "dump", "exit"} to DRILL_RESULT_FILE
# from the EXIT trap, on every exit path including the early FAILs. The
# backups check reads it: a failed drill newer than the last passing one
# makes backup readiness FAILING, whatever last night's dump says.
set -uo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/atlas/data/app/backups}"
MIN_FREE_GB="${MIN_FREE_GB:-5}"
DRILL_MEMORY="${DRILL_MEMORY:-768m}"
DRILL_CPUS="${DRILL_CPUS:-0.5}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-180}"
DRILL_RESULT_FILE="${DRILL_RESULT_FILE:-$BACKUP_DIR/restore_drill.json}"
PROD_PG_CONTAINER="atlas-postgres-1"
DB=drill

DRY_RUN=0
DUMP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --dump) shift; DUMP="${1:-}" ;;
    -h|--help) sed -n '2,41p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

say()    { printf '%s\n' "$*"; }
refuse() { say ""; say "REFUSED: $*"; say "Nothing was started. Exit 2."; exit 2; }

# ------------------------------------------------------------ preflight
command -v docker >/dev/null 2>&1 || refuse "docker is not on PATH"
docker info >/dev/null 2>&1 || refuse "cannot talk to the Docker daemon"

if [ -z "$DUMP" ]; then
  [ -d "$BACKUP_DIR" ] || refuse "backup directory $BACKUP_DIR does not exist"
  # Newest plain custom-format dump. *.dump.part (in progress) and *.dump.age
  # (encrypted off-box copies) do not match this pattern.
  # shellcheck disable=SC2012  # names are atlas-YYYYMMDD-HHMM.dump (atlas-backup.sh)
  DUMP=$(ls -1t "$BACKUP_DIR"/atlas-*.dump 2>/dev/null | head -n 1)
  [ -n "$DUMP" ] || refuse "no atlas-*.dump in $BACKUP_DIR"
fi
[ -f "$DUMP" ] && [ -r "$DUMP" ] || refuse "dump $DUMP is missing or unreadable"
case "$DUMP" in /*) ;; *) DUMP="$(cd "$(dirname "$DUMP")" && pwd)/$(basename "$DUMP")" ;; esac
DUMP_DIR=$(dirname "$DUMP")
DUMP_BYTES=$(wc -c < "$DUMP" | tr -d ' ')
[ "$DUMP_BYTES" -gt 0 ] 2>/dev/null || refuse "dump $DUMP is empty"

DOCKER_DATA_ROOT="${DOCKER_DATA_ROOT:-$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)}"
[ -n "$DOCKER_DATA_ROOT" ] && [ -d "$DOCKER_DATA_ROOT" ] \
  || refuse "Docker data root '${DOCKER_DATA_ROOT:-?}' is not a local directory; \
set DOCKER_DATA_ROOT (Docker Desktop keeps it inside its VM)"

free_kb() { df -Pk "$1" 2>/dev/null | awk 'NR==2 {print $4}'; }
human()   { awk -v k="$1" 'BEGIN { printf "%.2f GB", k / 1048576 }'; }
ROOT_FREE_KB=$(free_kb "$DOCKER_DATA_ROOT")
BAK_FREE_KB=$(free_kb "$DUMP_DIR")
[ -n "$ROOT_FREE_KB" ] || refuse "could not read free space on $DOCKER_DATA_ROOT"
[ -n "$BAK_FREE_KB" ]  || refuse "could not read free space on $DUMP_DIR"
MIN_KB=$(( MIN_FREE_GB * 1048576 ))
NEED_KB=$(( (DUMP_BYTES * 3 + 1023) / 1024 ))

say "ATLAS restore drill  $(date -u +%Y-%m-%dT%H:%M:%SZ)  host $(hostname)"
say "dump              $DUMP ($DUMP_BYTES bytes)"
say "docker data root  $DOCKER_DATA_ROOT: $(human "$ROOT_FREE_KB") free"
say "backup location   $DUMP_DIR: $(human "$BAK_FREE_KB") free"
say "floor             ${MIN_FREE_GB} GB on both; restore estimate 3 x dump = $(human "$NEED_KB")"

[ "$ROOT_FREE_KB" -ge "$MIN_KB" ] || refuse "Docker data root $DOCKER_DATA_ROOT has \
$(human "$ROOT_FREE_KB") free, under the ${MIN_FREE_GB} GB floor. Free space first; \
this drill will not add to a full disk."
[ "$BAK_FREE_KB" -ge "$MIN_KB" ] || refuse "backup location $DUMP_DIR has \
$(human "$BAK_FREE_KB") free, under the ${MIN_FREE_GB} GB floor."
[ "$ROOT_FREE_KB" -ge "$NEED_KB" ] || refuse "restore estimate $(human "$NEED_KB") \
(3 x dump) exceeds the $(human "$ROOT_FREE_KB") free on $DOCKER_DATA_ROOT."

if [ -z "${DRILL_IMAGE:-}" ]; then
  DRILL_IMAGE=$(docker inspect --format '{{.Config.Image}}' "$PROD_PG_CONTAINER" 2>/dev/null)
  DRILL_IMAGE="${DRILL_IMAGE:-pgvector/pgvector:pg16}"
fi
# Never pull here: on the ATLAS host a 600 MB pull onto a tight disk is the
# very thing the floor above guards against.
docker image inspect "$DRILL_IMAGE" >/dev/null 2>&1 \
  || refuse "image $DRILL_IMAGE is not present locally (the drill never pulls)"
say "image             $DRILL_IMAGE"

NAME="restore-drill-atlas-$(date -u +%Y%m%d%H%M%S)-$$"
LABEL="atlas.restore-drill=1"

if [ "$DRY_RUN" -eq 1 ]; then
  say ""
  say "DRY RUN: preflight passed. The drill would run:"
  say "  trap 'docker rm -fv $NAME' EXIT INT TERM HUP"
  say "  docker run -d --name $NAME --label $LABEL --network none --pull never \\"
  say "    --memory $DRILL_MEMORY --cpus $DRILL_CPUS -e POSTGRES_HOST_AUTH_METHOD=trust \\"
  say "    -e POSTGRES_DB=$DB -v $DUMP:/drill/atlas.dump:ro $DRILL_IMAGE"
  say "  wait for 'PostgreSQL init process complete' + 3 consecutive 'select 1'"
  say "  docker exec $NAME pg_restore -U postgres -d $DB --no-owner --no-privileges /drill/atlas.dump"
  say "  verify: core tables + row counts, newer tables, dedup/receipts,"
  say "          audit chain (sha256 in SQL), reconcile list, in-flight actions"
  say "  docker rm -fv $NAME   (the trap, on every exit path)"
  exit 0
fi

# -------------------------------------------------------------- cleanup
VOLUMES=""
# shellcheck disable=SC2329  # invoked by the EXIT trap
# 0 is a pass and 1 a fail; 129/130/143 (interrupted) are no verdict.
write_result() {
  local rc=$1 ok
  case "$rc" in 0) ok=true ;; 1) ok=false ;; *) return 0 ;; esac
  [ -d "$(dirname "$DRILL_RESULT_FILE")" ] || return 0
  printf '{"at":"%s","ok":%s,"dump":"%s","exit":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ok" "$(basename "$DUMP")" "$rc" \
    > "$DRILL_RESULT_FILE.tmp" && mv "$DRILL_RESULT_FILE.tmp" "$DRILL_RESULT_FILE" \
    && chown 10001 "$DRILL_RESULT_FILE" 2>/dev/null
  say "result            $DRILL_RESULT_FILE (ok=$ok)"
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  write_result "$rc"
  if docker inspect "$NAME" >/dev/null 2>&1; then
    VOLUMES=$(docker inspect --format \
      '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}' "$NAME" 2>/dev/null)
    docker rm -fv "$NAME" >/dev/null 2>&1
  fi
  # Belt and braces: the exact anonymous volume(s) this container had, by name.
  for v in $VOLUMES; do
    docker volume rm "$v" >/dev/null 2>&1 || true
  done
  if docker inspect "$NAME" >/dev/null 2>&1; then
    say "CLEANUP FAILED: container $NAME still exists; remove it with: docker rm -fv $NAME"
    rc=1
  else
    say "cleanup           container $NAME removed${VOLUMES:+, volume(s) $VOLUMES removed}"
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# ---------------------------------------------------------------- start
say ""
say "starting          $NAME (--network none)"
if ! docker run -d --name "$NAME" --label "$LABEL" --network none --pull never \
      --memory "$DRILL_MEMORY" --cpus "$DRILL_CPUS" \
      -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB="$DB" \
      -v "$DUMP:/drill/atlas.dump:ro" "$DRILL_IMAGE" >/dev/null; then
  say "FAIL: the throwaway container did not start"
  exit 1
fi

psql_q() { docker exec -i "$NAME" psql -U postgres -d "$DB" -X -At -v ON_ERROR_STOP=1 -c "$1"; }

# The entrypoint runs a temporary socket-only server during init, then
# restarts. pg_isready can pass against that first one, so wait for the log
# line that marks the end of init, then for three good answers in a row.
deadline=$(( $(date +%s) + READY_TIMEOUT_S ))
good=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  if docker logs "$NAME" 2>&1 | grep -q "PostgreSQL init process complete"; then
    if psql_q "select 1" >/dev/null 2>&1; then
      good=$((good + 1)); [ "$good" -ge 3 ] && break
    else
      good=0
    fi
  fi
  if [ "$(docker inspect --format '{{.State.Running}}' "$NAME" 2>/dev/null)" != "true" ]; then
    say "FAIL: the throwaway postgres exited during start-up"; docker logs --tail 20 "$NAME" 2>&1
    exit 1
  fi
  sleep 1
done
[ "$good" -ge 3 ] || { say "FAIL: postgres was not ready within ${READY_TIMEOUT_S}s"; exit 1; }

# -------------------------------------------------------------- restore
say "restoring         pg_restore --no-owner --no-privileges"
start_s=$(date +%s)
RESTORE_LOG=$(docker exec "$NAME" pg_restore -U postgres -d "$DB" \
               --no-owner --no-privileges /drill/atlas.dump 2>&1)
RESTORE_RC=$?
RESTORE_S=$(( $(date +%s) - start_s ))
IGNORED=$(printf '%s\n' "$RESTORE_LOG" | sed -n 's/.*errors ignored on restore: \([0-9][0-9]*\).*/\1/p' | tail -n 1)
IGNORED="${IGNORED:-0}"

# --------------------------------------------------------------- verify
FAILS=0
pass() { say "PASS  $*"; }
fail() { say "FAIL  $*"; FAILS=$((FAILS + 1)); }
info() { say "      $*"; }

say ""
say "== report =="
if [ "$RESTORE_RC" -eq 0 ] && [ "$IGNORED" -eq 0 ]; then
  pass "pg_restore finished cleanly in ${RESTORE_S}s"
else
  fail "pg_restore exit $RESTORE_RC, $IGNORED error(s) ignored on restore"
  printf '%s\n' "$RESTORE_LOG" | grep -E 'error|ERROR' | head -n 10 | sed 's/^/      /'
fi

has_table() {
  [ "$(psql_q "SELECT to_regclass('public.$1') IS NOT NULL" 2>/dev/null)" = "t" ]
}
count() { psql_q "SELECT count(*) FROM public.$1" 2>/dev/null; }

TOTAL_TABLES=$(psql_q "SELECT count(*) FROM pg_tables WHERE schemaname='public'" 2>/dev/null)
info "tables restored: ${TOTAL_TABLES:-?}"

say "-- core tables (missing = FAIL)"
for t in proposals actions_log agent_runs checks observations; do
  if has_table "$t"; then pass "$(printf '%-20s %s rows' "$t" "$(count "$t")")"
  else fail "$t is missing"; fi
done

say "-- durability tables (absent in dumps older than 759a4fe = not a failure)"
for t in pauses schedule_runs messaging_inbound messages_inbound; do
  if has_table "$t"; then info "$(printf '%-20s %s rows' "$t" "$(count "$t")")"
  else info "$(printf '%-20s absent' "$t")"; fi
done

say "-- dedup keys and receipts"
if has_table actions_log; then
  info "slack.event receipts (actions_log): $(psql_q "SELECT count(*) FROM actions_log WHERE event='slack.event'")"
fi
if has_table proposals; then
  if [ "$(psql_q "SELECT count(*) FROM information_schema.columns WHERE table_name='proposals' AND column_name='dedup_key'")" = "1" ]; then
    info "proposals with a dedup_key: $(psql_q "SELECT count(*) FROM proposals WHERE dedup_key IS NOT NULL")"
  else
    info "proposals.dedup_key column absent"
  fi
  if has_table pauses; then
    active=$(psql_q "SELECT coalesce(string_agg(scope, ', '), '') FROM pauses WHERE resumed_at IS NULL")
    info "active pauses: ${active:-none}"
  fi
fi

say "-- audit chain (sha256(prev|at|payload), recomputed in SQL)"
if has_table actions_log; then
  # Must match kernel/audit.py `_digest` over state.canonical_at: Python's
  # isoformat of a UTC datetime, `+00:00`, and no fraction when microsecond=0.
  CHAIN=$(psql_q "
    WITH c AS (
      SELECT id, prev_digest, digest, payload,
             coalesce(lag(digest) OVER (ORDER BY id), repeat('0', 64)) AS want_prev,
             to_char(at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS')
               || CASE WHEN (extract(microseconds FROM at)::bigint % 1000000) = 0 THEN ''
                       ELSE '.' || to_char(at AT TIME ZONE 'UTC', 'US') END
               || '+00:00' AS ts
        FROM actions_log),
    b AS (
      SELECT id,
             prev_digest <> want_prev AS link_bad,
             digest <> encode(sha256(convert_to(prev_digest || '|' || ts || '|' || payload,
                                                'UTF8')), 'hex') AS digest_bad
        FROM c)
    SELECT count(*) || ' ' || count(*) FILTER (WHERE link_bad)
           || ' ' || count(*) FILTER (WHERE digest_bad)
           || ' ' || coalesce(min(id) FILTER (WHERE link_bad OR digest_bad)::text, '-')
      FROM b" 2>&1)
  # shellcheck disable=SC2086
  set -- $CHAIN
  if [ $# -eq 4 ] && [ "$2" = "0" ] && [ "$3" = "0" ]; then
    pass "audit chain intact: $1 entries"
  elif [ $# -eq 4 ]; then
    fail "audit chain breaks: $2 link and $3 digest mismatch(es) in $1 entries, first at id $4"
  else
    fail "audit chain could not be checked: $CHAIN"
  fi
else
  fail "audit chain not checkable: actions_log missing"
fi

say "-- MUST RECONCILE BEFORE DISPATCH"
if has_table proposals; then
  NREC=$(psql_q "SELECT count(*) FROM proposals WHERE status IN ('pending','approved')")
  info "pending/approved proposals in this dump: $NREC"
  if [ "${NREC:-0}" -gt 0 ]; then
    info "id | status | action | created_at | expires_at | expired_now"
    psql_q "SELECT id, status, action,
                   to_char(created_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI\"Z\"'),
                   to_char(expires_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI\"Z\"'),
                   CASE WHEN expires_at <= now() THEN 'yes' ELSE 'no' END
              FROM proposals WHERE status IN ('pending','approved')
             ORDER BY id LIMIT 100" | sed 's/|/ | /g; s/^/      /'
    info "Each of these may already have been decided or executed in the live system"
    info "after this dump was taken. Match each against live proposals and provider"
    info "receipts before a restored ATLAS is allowed to dispatch anything."
  fi
fi
if has_table actions_log; then
  NFLY=$(psql_q "SELECT count(*) FROM actions_log s WHERE s.event='execute.start'
                   AND NOT EXISTS (SELECT 1 FROM actions_log f
                     WHERE f.subject=s.subject AND f.at>=s.at AND f.id<>s.id
                       AND f.event IN ('execute.finish','execute.error','execute.abandoned'))")
  info "execute.start with no finish/error/abandoned (effect unknown): $NFLY"
  if [ "${NFLY:-0}" -gt 0 ]; then
    psql_q "SELECT s.id, s.subject, to_char(s.at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI\"Z\"')
              FROM actions_log s WHERE s.event='execute.start'
               AND NOT EXISTS (SELECT 1 FROM actions_log f
                 WHERE f.subject=s.subject AND f.at>=s.at AND f.id<>s.id
                   AND f.event IN ('execute.finish','execute.error','execute.abandoned'))
             ORDER BY s.id LIMIT 50" | sed 's/|/ | /g; s/^/      /'
  fi
fi

say ""
say "This drill restored the LOCAL plain dump only. It does not prove the off-box"
say "age-encrypted copy decrypts: that key lives on Miro's Mac, not on this host."
say ""
if [ "$FAILS" -eq 0 ]; then
  say "RESULT: PASS ($DUMP)"
  exit 0
fi
say "RESULT: FAIL, $FAILS check(s) failed ($DUMP)"
exit 1
