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
#   ops/restore_drill.sh --into CONTAINER --db restore_drill_NAME [--dump FILE]
#                                     restore into a database THIS run creates
#                                     inside an existing throwaway Postgres
#                                     container (never a live one), then drop it
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
# --into mode (Phase 1 of the improve plan): for a Mac where docker run of a fresh
# Postgres is not wanted. It refuses (exit 2) a container named atlas-agency-* or
# atlas-postgres-1 or any name outside the allow pattern (ATLAS_DRILL_INTO_ALLOW,
# default: atlas-test-pg-*), and any database not named restore_drill_[a-z0-9_]+.
# It CREATEs the database (refusing if it already exists), copies the dump in with
# `docker cp`, restores, and DROPs that database and the copy on every exit path.
# It never removes the container. No result file is written unless
# DRILL_RESULT_FILE is set, so a fixture run cannot stand in for the real drill.
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
#   DRILL_RESULT_FILE $BACKUP_DIR/restore_drill.json (--into: none unless set)
#   ATLAS_DRILL_INTO_ALLOW  glob of containers --into may use [atlas-test-pg-*]
#   ATLAS_DRILL_PG_USER     postgres user inside the --into container [postgres]
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
INTO=""
DB=drill
PG_USER="${ATLAS_DRILL_PG_USER:-postgres}"
INTO_ALLOW="${ATLAS_DRILL_INTO_ALLOW:-atlas-test-pg-*}"
PROD_PG_CONTAINER="atlas-postgres-1"

DRY_RUN=0
DUMP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --dump) shift; DUMP="${1:-}" ;;
    --into) shift; INTO="${1:-}" ;;
    --db) shift; DB="${1:-}" ;;
    -h|--help) sed -n '2,60p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ -z "$INTO" ]; then
  DRILL_RESULT_FILE="${DRILL_RESULT_FILE:-$BACKUP_DIR/restore_drill.json}"
else
  DRILL_RESULT_FILE="${DRILL_RESULT_FILE:-}"
fi

say()    { printf '%s\n' "$*"; }
refuse() { say ""; say "REFUSED: $*"; say "Nothing was started. Exit 2."; exit 2; }

# ------------------------------------------------------------ preflight
command -v docker >/dev/null 2>&1 || refuse "docker is not on PATH"
docker info >/dev/null 2>&1 || refuse "cannot talk to the Docker daemon"

if [ -n "$INTO" ]; then
  # Never a live stack, never a name outside the allow list, never an unknown db.
  case "$INTO" in
    atlas-agency-*|"$PROD_PG_CONTAINER"|atlas-postgres-*)
      refuse "--into $INTO is a live ATLAS container" ;;
  esac
  # shellcheck disable=SC2254  # INTO_ALLOW is meant to be a glob
  case "$INTO" in $INTO_ALLOW) ;; *) refuse "--into $INTO is not allowed by ATLAS_DRILL_INTO_ALLOW ($INTO_ALLOW)" ;; esac
  case "$DB" in
    restore_drill_*[!a-z0-9_]*|restore_drill_) refuse "--db $DB: use restore_drill_ then [a-z0-9_]" ;;
    restore_drill_?*) ;;
    *) refuse "--db must be named restore_drill_<something> with --into (got '$DB')" ;;
  esac
  [ "$(docker inspect --format '{{.State.Running}}' "$INTO" 2>/dev/null)" = "true" ] \
    || refuse "container $INTO is not running"
  # A database this run did not create is never touched.
  EXISTING=$(docker exec "$INTO" psql -U "$PG_USER" -d postgres -X -At \
    -c "SELECT 1 FROM pg_database WHERE datname='$DB'" 2>/dev/null)
  [ -z "$EXISTING" ] || refuse "database $DB already exists in $INTO; this drill only creates and drops its own"
fi

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

if [ -n "$INTO" ]; then
  # The data root is inside the container VM: the throwaway database lives in an
  # existing container, so only the dump's own location is floor-checked.
  DOCKER_DATA_ROOT="$DUMP_DIR"
fi
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

if [ -n "$INTO" ]; then
  DRILL_IMAGE="(existing container $INTO)"
elif [ -z "${DRILL_IMAGE:-}" ]; then
  DRILL_IMAGE=$(docker inspect --format '{{.Config.Image}}' "$PROD_PG_CONTAINER" 2>/dev/null)
  DRILL_IMAGE="${DRILL_IMAGE:-pgvector/pgvector:pg16}"
fi
# Never pull here: on the ATLAS host a 600 MB pull onto a tight disk is the
# very thing the floor above guards against.
[ -n "$INTO" ] || docker image inspect "$DRILL_IMAGE" >/dev/null 2>&1 \
  || refuse "image $DRILL_IMAGE is not present locally (the drill never pulls)"
say "image             $DRILL_IMAGE"

NAME="restore-drill-atlas-$(date -u +%Y%m%d%H%M%S)-$$"
COPY="/tmp/restore-drill-$$.dump"
LABEL="atlas.restore-drill=1"

if [ "$DRY_RUN" -eq 1 ] && [ -n "$INTO" ]; then
  say ""
  say "DRY RUN: preflight passed. The drill would run:"
  say "  CREATE DATABASE $DB in $INTO (refused above if it existed)"
  say "  docker cp $DUMP $INTO:$COPY ; pg_restore into $DB"
  say "  verify and print row counts; DROP DATABASE $DB; rm $COPY (trap, every exit)"
  exit 0
fi
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
ROWS_JSON=""          # "table":count, pairs, filled after the restore
CREATED_DB=0
# shellcheck disable=SC2329  # invoked by the EXIT trap
# 0 is a pass and 1 a fail; 129/130/143 (interrupted) are no verdict.
write_result() {
  local rc=$1 ok
  case "$rc" in 0) ok=true ;; 1) ok=false ;; *) return 0 ;; esac
  [ -n "$DRILL_RESULT_FILE" ] || return 0
  [ -d "$(dirname "$DRILL_RESULT_FILE")" ] || return 0
  printf '{"at":"%s","ok":%s,"dump":"%s","exit":%s,"rows":{%s}}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$ok" "$(basename "$DUMP")" "$rc" "${ROWS_JSON%,}" \
    > "$DRILL_RESULT_FILE.tmp" && mv "$DRILL_RESULT_FILE.tmp" "$DRILL_RESULT_FILE" \
    && chown 10001 "$DRILL_RESULT_FILE" 2>/dev/null
  say "result            $DRILL_RESULT_FILE (ok=$ok)"
}

cleanup() {
  local rc=$?
  trap - EXIT INT TERM HUP
  write_result "$rc"
  if [ -n "$INTO" ]; then
    # Only what this run created: its own database and the copied dump.
    if [ "$CREATED_DB" -eq 1 ]; then
      docker exec "$INTO" psql -U "$PG_USER" -d postgres -X -At \
        -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" >/dev/null 2>&1 \
        || say "CLEANUP FAILED: drop database $DB in $INTO by hand"
    fi
    docker exec "$INTO" rm -f "$COPY" >/dev/null 2>&1 || true
    say "cleanup           database $DB dropped, $COPY removed; container $INTO left running"
    exit "$rc"
  fi
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
if [ -n "$INTO" ]; then
  say "creating          database $DB in $INTO"
  if ! docker exec "$INTO" psql -U "$PG_USER" -d postgres -X -At -v ON_ERROR_STOP=1 \
        -c "CREATE DATABASE $DB" >/dev/null; then
    say "FAIL: could not create database $DB"
    exit 1
  fi
  CREATED_DB=1
  if ! docker cp "$DUMP" "$INTO:$COPY" >/dev/null; then
    say "FAIL: could not copy the dump into $INTO"
    exit 1
  fi
else
say "starting          $NAME (--network none)"
if ! docker run -d --name "$NAME" --label "$LABEL" --network none --pull never \
      --memory "$DRILL_MEMORY" --cpus "$DRILL_CPUS" \
      -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB="$DB" \
      -v "$DUMP:/drill/atlas.dump:ro" "$DRILL_IMAGE" >/dev/null; then
  say "FAIL: the throwaway container did not start"
  exit 1
fi

fi
TARGET="${INTO:-$NAME}"
psql_q() { docker exec -i "$TARGET" psql -U "$PG_USER" -d "$DB" -X -At -v ON_ERROR_STOP=1 -c "$1"; }

# The entrypoint runs a temporary socket-only server during init, then
# restarts. pg_isready can pass against that first one, so wait for the log
# line that marks the end of init, then for three good answers in a row.
deadline=$(( $(date +%s) + READY_TIMEOUT_S ))
good=0
[ -z "$INTO" ] || good=3     # an existing container is already serving
while [ -z "$INTO" ] && [ "$(date +%s)" -lt "$deadline" ]; do
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
DUMP_IN_CONTAINER=/drill/atlas.dump
[ -z "$INTO" ] || DUMP_IN_CONTAINER="$COPY"
RESTORE_LOG=$(docker exec "$TARGET" pg_restore -U "$PG_USER" -d "$DB" \
               --no-owner --no-privileges "$DUMP_IN_CONTAINER" 2>&1)
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

say "-- row counts (recorded in the result file under \"rows\")"
for t in proposals actions_log agent_runs checks observations pauses schedule_runs; do
  if has_table "$t"; then
    n=$(psql_q "SELECT count(*) FROM public.$t" 2>/dev/null)
    case "$n" in ''|*[!0-9]*) ;; *) ROWS_JSON="$ROWS_JSON\"$t\":$n," ; info "$(printf '%-20s %s' "$t" "$n")" ;; esac
  fi
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
