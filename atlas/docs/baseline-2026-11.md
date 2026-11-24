# Baseline, November 2026 (Phase 0)

Every number has its read time. "Not read" says why. No secrets, no numbers of people.

## Host (read 2026-11-15 10:50 EST, this Mac)

| Item | Value |
|---|---|
| Data volume | 926 GiB, 835 GiB used, 38 GiB free, 96% (`df`) |
| `docker system df` | images 10.79 GB, containers 191 MB, volumes 1.54 GB, build cache 11.52 GB (7.5 GB reclaimable) |
| `data/app` | 14 GB; `data/models` 37 MB; `data/voice` 636 KB |
| Live api | `/healthz` returned ok, 28 modules, 75 checks, cycle age 16 s, observation age 16 s |

The 2026-11-14 read of "100% used, 8.3 GiB free" was not repeated: space was freed since.
The disk is still at 96%, so the disk check is close to its floors.

## Not read, and why

- Board snapshot by state, camera counts for 14 days, outbox by status, owner calls, token rows:
  the board and stats routes answer 404 without a session, and the rule allows only the existing local
  API or CLI, read-only. No session token was used. Run the board read as the owner, then fill this in.
- Whether the host backup and housekeeping jobs run on this Mac (`launchctl list`) and which path the
  disk check measures: the new `ops/watchdog/host/disk_status.sh` writes the data-volume status file;
  it is not installed (see `ops/watchdog/INSTALL.md`).
- Timed image build with per-step times: not run. `docker build` on this Mac with a few GiB of slack
  could fill the disk and stop the camera stack. Build-cache is 11.5 GB; the Dockerfile layer order was
  fixed by reading only (see the loop notes in `docs/`).

## Test suite (Spark, 20 cores, read 2026-11-15)

| Run | Result |
|---|---|
| Serial, no database | stopped at first failure after 10,353 passed, 353 skipped, 863 s; the failure was `test_every_loop_step_declares_a_missed_run_policy` (`draft_notice` had no policy; fixed) |
| Serial, PG lane (throwaway Postgres on this Mac, tunnelled) | 15,695 passed, 24 failed, 128 skipped, 3,375 s, with several agents running on the same Spark; none of the skips needs the database |
| The 24 failures | 22 stale PG tests (roster cut 2026-10-28, first-sale price retired, Slack cards are opt-in, weekend date), plus the missed-run policy and two tables created in code but absent from `db/schema.sql`. All fixed in this branch |

The PG tests were skipped by default before this lane, which is how 22 of them went stale unseen.
`ops/pg_lane.sh` now runs them and exits 3 when any PG test skips.
