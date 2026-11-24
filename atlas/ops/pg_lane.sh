#!/usr/bin/env bash
# pg_lane.sh - run tests with a throwaway Postgres so the PG tests run, not skip.
#
#   ops/pg_lane.sh [-d <dbname>] [-p <port>] [-j <workers>] [-- <pytest args>]
#
# Starts (or reuses) a throwaway pgvector container named atlas-test-pg-improve on
# 127.0.0.1:<port> (default 55432), creates database <dbname> (default atlas_lane),
# opens a reverse ssh tunnel so the Spark sees it on its own 127.0.0.1:<port>, then
# runs pytest on the Spark through spark-run.sh with ATLAS_TEST_PG_DSN set.
# The Spark has no docker and no sudo, so the database lives on the Mac.
#
# A PG test that skips is NOT a pass: after the run the log is read, and if any
# skip reason mentions ATLAS_TEST_PG_DSN the script exits 3.
# It never touches the live stack: it uses its own container, port and database,
# and the fixtures TRUNCATE tables there only. Use one -d per parallel worker.
#
# -j N runs the suite in parallel (xdist: -n N --dist loadgroup). The *_pg fixtures
# TRUNCATE, so each worker gets its own database <dbname>_gw0 .. <dbname>_gw<N-1>
# (tests/conftest.py names it from the worker id and creates it on demand; this
# script also creates them up front). Keep N at 8 or fewer on the shared test
# container: Postgres allows 100 connections in all.
# No secrets: the password below is the throwaway container's, nothing else.
set -euo pipefail

NAME="${PG_LANE_NAME:-atlas-test-pg-improve}"
PORT=55432
DB=atlas_lane
JOBS=0
while getopts ":d:p:j:" o; do
  case "$o" in
    d) DB="$OPTARG" ;;
    p) PORT="$OPTARG" ;;
    j) JOBS="$OPTARG" ;;
    *) echo "usage: pg_lane.sh [-d db] [-p port] [-j workers] [-- pytest args]" >&2
       exit 2 ;;
  esac
done
shift $((OPTIND - 1))
[ "${1:-}" = "--" ] && shift
[ $# -gt 0 ] || set -- tests

# This script may `docker rm -f` the container and turns fsync off in it: only
# a throwaway test container, never a live one (same list as restore_drill.sh).
case "$NAME" in
  atlas-agency-*|atlas-postgres-*) echo "refusing the live container" >&2; exit 2 ;;
  atlas-test-pg-*) ;;
  *) echo "refusing $NAME: the lane container must be named atlas-test-pg-*" >&2; exit 2 ;;
esac
case "$DB" in *[!A-Za-z0-9_]*) echo "bad db name" >&2; exit 2 ;; esac
case "$JOBS" in ""|*[!0-9]*) echo "bad -j" >&2; exit 2 ;; esac

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"

if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" -e POSTGRES_PASSWORD=testonly \
    -p "127.0.0.1:${PORT}:5432" pgvector/pgvector:pg16 \
    -c max_connections=300 >/dev/null
  until docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
  sleep 2
fi
# A throwaway database needs no durability: with fsync on, every test's schema
# apply waited on the Mac's disk (setup phases of 20-30 s under load). These
# three reload without a restart, so a container already running gets them too.
for setting in fsync synchronous_commit full_page_writes; do
  docker exec "$NAME" psql -U postgres -qc \
    "ALTER SYSTEM SET $setting = 'off'" >/dev/null
done
docker exec "$NAME" psql -U postgres -qc "SELECT pg_reload_conf()" >/dev/null
make_db() {
  docker exec "$NAME" psql -U postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname='$1'" | grep -q 1 \
    || docker exec "$NAME" psql -U postgres -c "CREATE DATABASE $1" >/dev/null
}
make_db "$DB"
XDIST=()
if [ "$JOBS" -gt 0 ]; then
  for ((i = 0; i < JOBS; i++)); do make_db "${DB}_gw${i}"; done
  XDIST=(-n "$JOBS" --dist loadgroup)
fi

if ! ssh -o BatchMode=yes spark "(exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null"; then
  # supervised tunnel with keepalives: a bare ssh -fN dropped mid-run and caused
  # ConnectionRefused errors that looked like test failures
  nohup bash -c "while true; do ssh -o BatchMode=yes -N -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    -R ${PORT}:127.0.0.1:${PORT} spark; sleep 2; done" >/dev/null 2>&1 &
  sleep 4
fi

DSN="postgresql://postgres:testonly@127.0.0.1:${PORT}/${DB}"
LOG="$(mktemp -t pg_lane.XXXXXX)"
set +e
"${SPARK_RUN:-spark-run.sh}" -p "$ROOT" -- env "ATLAS_TEST_PG_DSN=$DSN" \
  python -m pytest -q -p no:cacheprovider -rs ${XDIST[@]+"${XDIST[@]}"} "$@" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}
set -e
if grep -E "^SKIPPED" "$LOG" | grep -q "ATLAS_TEST_PG_DSN"; then
  echo "pg_lane: PG tests were SKIPPED - this is NOT a pass" >&2
  rm -f "$LOG"; exit 3
fi
rm -f "$LOG"
exit "$rc"
