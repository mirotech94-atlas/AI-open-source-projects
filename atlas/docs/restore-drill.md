# Restore drill (RA-H2)

Status 2026-09-28: **scripted, not scheduled, and not yet run on prod.**
The script is `ops/restore_drill.sh`. A human runs it on the ATLAS host.

## What it does

1. **Preflight (read-only).** It finds the newest `atlas-*.dump` in
   `/opt/atlas/data/app/backups` and reads free space with `df -Pk` on the
   Docker data root and on the backup directory. It **refuses with exit 2**,
   before anything starts, in any of these cases:
   - either location has under 5 GB free (`MIN_FREE_GB`);
   - the data root has less than 3 × the dump size free;
   - the image is not already present (it never pulls);
   - the data root is not a local directory.
2. **Throwaway postgres.** It starts the image that `atlas-postgres-1` runs
   (read with `docker inspect`; fallback `pgvector/pgvector:pg16`), with:
   - `--network none`, `--pull never`, `--memory 768m`, `--cpus 0.5`;
   - trust auth, which is safe with no network and means no password exists;
   - a unique name `restore-drill-atlas-<utc>-<pid>` and the label
     `atlas.restore-drill=1`. The name does not start with `atlas-`, so it
     cannot collide with the compose project;
   - exactly one bind mount: the dump file, read-only.
   It then waits for the entrypoint's "init process complete" line and three
   consecutive successful `select 1`s. The temporary init server can
   otherwise pass `pg_isready` too early.
3. **Restore.** It runs `pg_restore --no-owner --no-privileges`. A non-zero
   exit or any "errors ignored on restore" is a FAIL.
4. **Verify and report.**

   | Check | Result |
   |---|---|
   | Core tables `proposals`, `actions_log`, `agent_runs`, `checks`, `observations` | exist + row counts; missing = FAIL |
   | `pauses`, `schedule_runs`, `messaging_inbound`, `messages_inbound` | row counts, or "absent" (older dumps), not a FAIL |
   | Dedup and receipts | `slack.event` rows in `actions_log` (the durable Slack receipt), proposals with a `dedup_key`, active pauses |
   | Audit chain | `sha256(prev|at|payload)` recomputed **in SQL** for every row, with `prev_digest` linked to the previous `digest` from GENESIS. It reproduces `kernel/audit.py::_digest` over `state.canonical_at` (Python `isoformat`, `+00:00`, no fraction when microsecond = 0). Any break = FAIL, with the first bad id |
   | **MUST RECONCILE BEFORE DISPATCH** | every `pending`/`approved` proposal (id, status, action, created, expires, expired now?), plus every `execute.start` with no finish, error or abandoned row (an external effect of unknown outcome) |

   The result is `RESULT: PASS` (exit 0) or `RESULT: FAIL` (exit 1).
5. **Cleanup.** An EXIT trap, with INT/TERM/HUP mapped to exit, runs
   `docker rm -fv <that exact name>`. It then removes the container's own
   anonymous volume by the name it read from `docker inspect`, and reports
   both. Nothing is matched by pattern.

It never references a prod container, volume or network, other than the
read-only `docker inspect atlas-postgres-1`.

## Running it (on the ATLAS host, as root)

Until branch `ra-f4-h2` is merged and deployed, the script is not in
`/opt/atlas`. Copy it outside the deployed tree first, for example
`scp ops/restore_drill.sh root@203.0.113.10:/root/restore_drill.sh`, and run
`/root/restore_drill.sh` in place of `ops/restore_drill.sh` below. It needs
nothing else from the repo.

```bash
cd /opt/atlas
ops/restore_drill.sh --dry-run      # preflight + the plan, starts nothing
ops/restore_drill.sh                # the drill
ops/restore_drill.sh --dump /opt/atlas/data/app/backups/atlas-20260928-0620.dump
```

A restored database is evidence that the dump works. It is never a source to
dispatch from. The reconcile list is what a real recovery would have to settle
by hand, against live proposals and provider receipts, before any restored
ATLAS acts (plan: "restoring an older database is not automatically safe").

## A real restore (the restore fence, R12)

The drill never touches prod. A real restore does, and a restored database
is older than the world: an outbox row it holds as `pending` or `claimed`
may have been sent after the dump, and a card it holds as `pending` may
have been approved and run. The procedure below records the restore, and
ATLAS fences it before anything leaves (`atlas/kernel/restore.py`).

```bash
cd /opt/atlas
age -d -i ~/.config/atlas/backup-age.key atlas-X.dump.age > atlas-X.dump  # off-box copy only
docker compose stop api                       # nothing dispatches in between
docker compose exec -T postgres pg_restore -U atlas -d atlas --clean < atlas-X.dump
docker compose exec -T postgres psql -U atlas -d atlas -c \
  "INSERT INTO restores (dump, restored_by) VALUES ('atlas-X.dump', 'miro')"
docker compose start api
```

The `INSERT` is the step that matters. (`python -m atlas.cli restore-fence
atlas-X.dump` inside the api container records the same row and applies it
at once.) While a `restores` row is unfenced:

- the outbox claims nothing (the check is inside the claim statement), and
  every outbox pass applies the fence first;
- `approvals.decide` applies the fence before its claim.

Applying it is one SQL statement:

- every outbox row `pending`, `claimed` or `dispatching` created before the
  restore goes to `reconciling` (lease cleared, fencing token bumped), so it
  is read back before anything is sent: present -> `delivered`, absent ->
  `pending`, no reconciler or unknown -> `needs_human`;
- every card `pending` created before the restore becomes `held`. It can no
  longer be approved; declining it resolves it (a no causes no effect). A
  content workflow waiting on it goes to `NEEDS_HUMAN`. Check whether it
  ran, and ask again if it still needs doing;
- one short notice says how many sends and cards are held.

Cards the dump holds as `approved` are already handled at API start by
`approvals.reap_orphans` (marked failed, "check by hand", never re-run).
If the marker cannot be read, nothing is dispatched and nothing is decided.
Fencing twice changes nothing. **Limit:** the fence only exists if the
procedure records it; a `pg_restore` run without the `INSERT` is not
detected. The drill's "MUST RECONCILE" list is still the checklist for what
the fence holds.

## Not run on prod, and why

Read-only facts from the ATLAS host, 2026-09-28 ~21:30Z:

- `df -h /`: **39G size, 37G used, 198M available, 100%**. `/var/lib/docker`
  (DockerRootDir) and `/opt/atlas/data/app/backups` are on that same root
  filesystem. That is worse than the ~92% reported earlier today. The drill
  would refuse on the 5 GB floor, so it was not run, not even `--dry-run`.
  Freeing space is for Miro to decide; nothing was deleted.
- Backups: `atlas-backup.timer` (`/etc/systemd/system/`, `OnCalendar 06:20 UTC`,
  `Persistent=true`) **did fire** at 06:20Z. `status.json` shows
  `dump_ok: true, offsite_ok: true`, file `atlas-20260928-0620.dump`,
  9,812,882 bytes. FINDINGS F §2 ("has not yet fired") is now stale. Dumps on
  the host: `atlas-20260928-0422.dump`, `-0428`, `-0620` (about 9.5–9.8 MB
  each, `pg_dump -Fc`). The off-box copies are age-encrypted
  `*.dump.age` in `atlasbak@203.0.113.10:/srv/atlas-backups`.
- The image `pgvector/pgvector:pg16` is already on the host (621 MB), so
  the drill needs no pull. Docker is 29.8.1 and bash is 5.2.

With a ~10 MB dump, 3 × dump is about 30 MB, so the **5 GB floor** is what
decides. The host needs at least 5 GB free before the drill will run.

## Verified locally (Docker Desktop 29.4, Mac)

Fixture: a local `pgvector/pgvector:pg16` container with `db/schema.sql`
applied. It held 5 audit rows built with the repo's own `_digest` and
`canonical_at` (one at microsecond 0, one at microsecond 7), 1 pending +
1 approved (already expired) + 1 expired proposal, 1 unfinished
`linkedin.publish_post` `execute.start`, 1 `schedule_runs` row and 1 active
`pauses` row. It was dumped with `pg_dump -Fc` (157 KB). On the Mac,
`DOCKER_DATA_ROOT=$HOME` was set, since Docker Desktop's root is inside its VM.

| Case | Result |
|---|---|
| Good dump | `RESULT: PASS`, exit 0. 68 tables; the 5 core tables PASS; `audit chain intact: 5 entries`. It listed pending #1 and approved #2 (expired) under MUST RECONCILE, and the unfinished `linkedin.publish_post` start |
| **Negative control**: one audit payload altered after its digest | `FAIL audit chain breaks: 0 link and 1 digest mismatch(es) in 5 entries, first at id 3`, `RESULT: FAIL`, exit 1. The first tamper attempt did not change the payload and PASSed; the fixture was fixed and re-run |
| `MIN_FREE_GB=100000` | REFUSED (floor), exit 2, nothing started |
| 20 GB sparse "dump" against 46.8 GB free | REFUSED: 3 × dump = 60 GB exceeds free, exit 2 |
| No local Docker root (Docker Desktop, no override) | REFUSED, exit 2 |
| `DRILL_IMAGE` not present | REFUSED (never pulls), exit 2 |
| Missing backup dir | REFUSED, exit 2 |
| `--dry-run` | preflight + plan printed, exit 0, nothing started |
| SIGTERM while the container ran | exit 143; trap removed the container and its volume |
| Every run above | `docker ps -aq` and `docker volume ls -q` identical before and after |

Also verified:

- `bash -n` passes. `shellcheck` (koalaman/shellcheck:stable, via Docker)
  is clean. Two info-level notes are disabled with reasons: SC2012 for `ls`
  on the fixed dump-name pattern, and SC2329 for the trap-invoked function.
- The audit-chain SQL was run **read-only** (`BEGIN READ ONLY`, one SELECT)
  against the live prod `actions_log`: `1184 rows, 0 link breaks, 0 digest
  breaks`. This shows the SQL canonicalisation matches real production rows,
  not only the fixture.

## Known gaps

- The drill proves the **local plain dump** restores. It does not prove the
  off-box `.age` copy decrypts: the age key lives only on Miro's Mac
  (`~/.config/atlas/backup-age.key`). An off-box drill has to run on the Mac,
  or on a host Miro chooses, with `age -d` first. That is not scripted here.
- Locally it has only restored dumps taken by the `postgres` superuser. Prod
  dumps are taken by the `atlas` role. The first prod run is the first
  against a prod-authored dump. If it reports "errors ignored on restore",
  read those errors before concluding the backup is bad.
- It checks structure, counts and the chain. It does not check `pgvector`
  index usability or media files (`/srv/atlas-backups/media`).
- RPO is one day, the cadence of the nightly timer. The plan's proposed
  1-hour RPO is not met and is not claimed.
