#!/usr/bin/env bash
# test_changed.sh - the changed-files lane: run only the tests a change can reach.
#
#   ops/test_changed.sh [-b BASE] [-d <pg db>] [-l] [-- <extra pytest args>]
#
# Selects test files with ops/test_changed.py (what differs from BASE, default
# feat/atlas-improve; imports of the changed modules, name matches, ops/test_map.txt),
# then runs them on the Spark through spark-run.sh with xdist. When a selected file
# needs Postgres (ATLAS_TEST_PG_DSN) it goes through ops/pg_lane.sh instead, so
# those tests run rather than skip (-d picks the database, default atlas_lane).
# -f names the changed file by hand instead of reading git (repeatable).
# -l only lists the selection. Not a substitute for the full suite before merge.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
BASE="${TEST_BASE:-feat/atlas-improve}"
DB="${ATLAS_LANE_DB:-atlas_lane}"
LIST=0
GIVEN=()
while getopts ":b:d:f:l" o; do
  case "$o" in
    b) BASE="$OPTARG" ;;
    d) DB="$OPTARG" ;;
    f) GIVEN+=("$OPTARG") ;;
    l) LIST=1 ;;
    *) echo "usage: test_changed.sh [-b base] [-d db] [-f file]... [-l] [-- pytest args]" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
[ "${1:-}" = "--" ] && shift

cd "$ROOT"
FILES=()
while IFS= read -r f; do [ -n "$f" ] && FILES+=("$f"); done \
  < <(python3 ops/test_changed.py --base "$BASE" ${GIVEN[@]+"${GIVEN[@]}"})
if [ ${#FILES[@]} -eq 0 ]; then
  echo "test_changed: nothing selected (no test reaches the change)" >&2
  exit 0
fi
if [ "$LIST" = 1 ]; then printf '%s\n' "${FILES[@]}"; exit 0; fi

JOBS=8
[ ${#FILES[@]} -le 2 ] && JOBS=0
NEEDS_PG=0
grep -lqE "ATLAS_TEST_PG_DSN|acceptance_support" "${FILES[@]}" 2>/dev/null && NEEDS_PG=1 || true

if [ "$NEEDS_PG" = 1 ]; then
  exec ops/pg_lane.sh -d "$DB" -j "$JOBS" -- "$@" "${FILES[@]}"
fi
XD=()
[ "$JOBS" -gt 0 ] && XD=(-n "$JOBS" --dist loadgroup)
exec "${SPARK_RUN:-spark-run.sh}" -p "$ROOT" -- python -m pytest -q \
  -p no:cacheprovider ${XD[@]+"${XD[@]}"} "$@" "${FILES[@]}"
