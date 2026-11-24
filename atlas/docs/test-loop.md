# The test loop

Three lanes, cheapest first. All run on the Spark through `spark-run.sh`
(the Mac only when the Spark is down). Nothing here touches the live stack.

| Lane | Command | When |
|---|---|---|
| Changed files | `ops/test_changed.sh` | while working; picks the tests a change can reach |
| Full, parallel, with Postgres | `ops/pg_lane.sh -d <db> -j 8 -- tests` | before merge |
| Full, serial | `spark-run.sh -p . -- python -m pytest -q tests` | the reference count |

The full suite still runs before every merge. The changed-files lane is a
direct-import map, one hop: a test that reaches a module only through another
module is not selected.

## Parallel runs (pytest-xdist)

- `pytest-xdist` and `execnet` are pinned in `constraints.txt` (section 3, test
  tooling; the image never installs them). `requirements-test.txt` names the
  package; `spark-run.sh` installs every `requirements*.txt` against
  `constraints.txt`, so the Spark venv gets it with no extra variable. The same
  list is the `test` extra in `pyproject.toml`.
- `-n N --dist loadgroup`. The `loadgroup` mode is what lets the `serial`
  marker work; `tests/conftest.py` switches `-n` runs to it by itself.
- `@pytest.mark.serial` on a test, class or module (`pytestmark = pytest.mark.serial`)
  puts it in one xdist group: it runs on a single worker, after its neighbours
  in the group, never beside another worker's tests. Use it when a test
  sleeps on wall-clock time, binds a fixed port, or mutates process-wide
  state that is not restored. Without `-n` the marker does nothing.
- Pass counts must match: a parallel run reports the same passed / failed /
  skipped numbers as a serial one. A difference means a test shares state:
  mark it `serial` and say why in a comment.

## Postgres lane

`ops/pg_lane.sh [-d db] [-p port] [-j workers] [-- pytest args]` starts or
reuses the throwaway container `atlas-test-pg-improve` on the Mac, tunnels it
to the Spark, sets `ATLAS_TEST_PG_DSN` and runs the suite. A PG test that skips
makes the script exit 3: a skip is not a pass.

With `-j N` every xdist worker gets its own database, `<db>_gw0` ..
`<db>_gw<N-1>`. The `*_pg` fixtures TRUNCATE, so workers must not share one;
`tests/conftest.py` derives the name from the worker id (`PYTEST_XDIST_WORKER`)
and creates the database on demand, and the script creates them up front. Keep
`-j` at 8 or fewer on the shared container (Postgres allows 100 connections).

## Changed-files lane

`ops/test_changed.sh [-b base] [-d db] [-f file]... [-l] [-- extra args]`

`-f` names the changed file by hand instead of reading git (a one-module
change: `ops/test_changed.sh -f atlas/modules/sensors/matching.py`, 5 s wall).

1. `ops/test_changed.py` lists what differs from the base branch
   (`feat/atlas-improve`, or `$TEST_BASE`): committed, staged, modified, untracked.
2. It selects each test file that imports a changed module, mentions its path,
   is named `test_<module>*.py`, imports a changed helper in `tests/`, or is
   named by a rule in `ops/test_map.txt` (SQL, Dockerfile, constraints).
   A core module such as `atlas/kernel/executor.py` selects about 90 files;
   a leaf module selects a handful.
3. It runs them with xdist; files that need Postgres go through `pg_lane.sh`.

`-l` prints the selection and runs nothing.

## Image build

The timed image build was not measured for this phase: the Mac's disk is tight
and the live stack runs there, so no `docker build` was run. What the files
say, read only:

- Stage 2 copied `pyproject.toml` before the pip layer, so any edit to it
  (a marker, a test extra) rebuilt the whole dependency layer, although the
  install names its packages itself. It now copies `constraints.txt` only.
- The stages are already ordered well for a Python-only change: UI build,
  apt, pip, Chromium, `useradd`, then `COPY . .`. Only the last copy and the UI
  copy re-run.
- The last copy is about 100 MB because `atlas/modules/academy/assets` is
  about 88 MB and is needed at runtime. Every code-only rebuild writes that
  copy again as a new layer, which is what fills the disk. Fixing it needs a
  `COPY --exclude` (Dockerfile frontend 1.19 or newer) or a separate
  `COPY atlas/modules/academy/assets` layer ahead of `COPY . .` with the assets
  excluded from the second copy. Not applied: it needs one timed build to prove
  it, and the live stack's update path must not change without that proof.
