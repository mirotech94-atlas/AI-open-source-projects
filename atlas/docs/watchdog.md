# Independent watchdog (RA-F4)

Status 2026-09-28: **installed on the FlyCheap VPS (Miro OK'd); the timer stays
disabled until Miro provides a Slack incoming webhook.** Code is in
`ops/watchdog/atlas_watchdog.py`, the tests are in `tests/test_watchdog.py`,
and the units plus install steps are in `ops/watchdog/`. The `/healthz`
heartbeat field it reads is built (below) and ships with the next deploy.

## Why a second host

Everything ATLAS knows about its own health is in-process: `modules/comms`,
`backups` and the scheduler itself. When the api process dies, when the
scheduler loop stalls, or when the box fills its disk, nothing is left to
say so. Docker's `HEALTHCHECK` marks a container unhealthy and does not
restart it (FINDINGS F §2). The watchdog runs on the FlyCheap VPS
(203.0.113.10), a different machine. It shares no process, database or
Slack credential with ATLAS.

## What it checks, every 5 minutes

| Check | OK when | Otherwise |
|---|---|---|
| `healthz` | `GET https://atlas.example.com/healthz` → 200 within 10 s, body `{"ok": true, ...}` | FAIL (HTTP code, unreachable, slow, bad body) |
| `tls` | chain and hostname verify with the system CA store, and more than 14 days remain | FAIL on a verify or handshake error, or under 14 days; UNKNOWN if the TCP connect itself fails (`healthz` already reports that) |
| `heartbeat` | the scheduler's last cycle and the newest observation are both present and at most 900 s old | FAIL if either is over 900 s (a FAIL wins over a null partner); **UNKNOWN** when either age is null or missing, when `/healthz` has no `heartbeat` field (an ATLAS older than 2026-09-28), or when `healthz` is down |

A check that cannot run is UNKNOWN and never OK (invariant 7). UNKNOWN is a
state change like any other, so on the first run with a webhook configured
the watchdog posts one "heartbeat UNKNOWN" message and then stays quiet
until something changes.

## Alerting

- It alerts **only on a state change** per check (OK / FAIL / UNKNOWN). The
  message lists what changed and what is still not OK. A detail that changes
  inside the same state, such as a new HTTP code while still FAIL, does not
  re-alert.
- The route is a **Slack incoming webhook**, read from
  `/etc/atlas-watchdog/watchdog.env` on the watchdog host. systemd passes it
  in through `LoadCredential=`. It is never ATLAS's Slack bot token: a value
  that starts `xoxb-`/`xoxp-`/`xoxa-`/`xapp-` is refused, and so is anything
  that is not a `https://hooks.slack.com/` URL. The URL is never logged, and
  it is redacted from exception text.
- **No webhook configured:** the checks still run and are logged. Then it logs
  `no alternate route configured` and exits 2 on every run. State is not
  advanced, so the first run after a webhook is added announces whatever is
  wrong at that moment.
- **Webhook POST fails:** the new state is not saved, and the next run sees the
  same transition and retries (exit 3).
- First run with no saved state: the baseline is taken as OK, so only non-OK
  checks are announced.
- Exit codes: 0 all OK, 10 a check is not OK (the unit's
  `SuccessExitStatus`), 2 no route, 3 alert not delivered, 4 the watchdog
  itself crashed. A crash can never exit with a code the unit treats as
  success, so it shows up as a failed unit and not as a quiet run.

State is one JSON file (`/var/lib/atlas-watchdog/state.json`, systemd
`StateDirectory`), replaced atomically with `mkstemp` + `os.replace`.

## Heartbeat: the `/healthz` field (built 2026-09-28)

There was no existing read-only signal that proves the scheduler is alive
end to end and is reachable from a host off the mesh:

- `/healthz` returned `{"ok": true, "modules": N, "checks": N}` from the
  in-memory registry and never touched the scheduler or the database.
  `scheduler.py` records that `/healthz stayed green` through loop stalls of
  1h46m and 1h21m (2026-09-24).
- `/api/status` is mesh-only and needs a signed-in session.
- Caddy exposes only `/webhooks/*`, `/healthz` (exact path), `/media/*` and
  `/deliver/*` to the internet.

So one data-free field was added to the existing `/healthz`. There is no new
route, no Caddyfile edit and no change to `PUBLIC_PATHS`:

```json
{"ok": true, "modules": 31, "checks": 117,
 "heartbeat": {"cycle_age_s": 12, "observation_age_s": 99}}
```

- `cycle_age_s` (`scheduler.LAST_CYCLE_AT`) is the number of seconds since
  `scheduler.loop` last finished a full pass over its steps. It is stamped
  in-process after the last step and before the sleep. It proves the loop
  is turning, which is exactly what the stalls above broke. It is `null`
  before the first cycle.
- `observation_age_s` is the number of seconds since the newest
  `observations.at`. It proves that checks run **and** reach Postgres. The
  row time is cached for 30 s (`HEARTBEAT_CACHE_S`), and the age is computed
  per request, so it keeps growing between reads. The read time is stamped
  before the query is awaited, so a burst of public hits costs one query. The
  query is bounded at 1.5 s, inside the container HEALTHCHECK's 5 s. A DB
  error, a timeout or an empty table gives `null`, never 0, and an error is
  cached for 30 s as well. The query uses the existing
  `observations_at (at DESC)` index.
- `"ok"` stays `true` whatever the heartbeat says. Docker's `HEALTHCHECK` and
  the deploy gate keep their meaning, and the judgement stays with the
  watchdog.
- Exposure: two integers, and no names or counts beyond what `/healthz`
  already shows.

Code: `atlas/api/main.py` (`_heartbeat`) and `atlas/kernel/scheduler.py`
(`LAST_CYCLE_AT`). Tests: `tests/test_healthz_heartbeat.py`. The copy of
`atlas_watchdog.py` installed on the FlyCheap VPS predates the stricter
check (both ages required, and a stale age wins over a null partner). On a
heartbeat with both ages as integers it reads the same. It differs only when
one age is missing (old: judged on the other) or null next to a stale one
(old: UNKNOWN, new: FAIL). Refresh it before the timer is enabled.

**Threshold.** Measured on prod 2026-09-28 (read-only query): the newest
observation was 99 s old. The p99 gap between consecutive observations over
24 h was 300 s, and the max was 3616 s. The limit is therefore 900 s
(`ATLAS_WATCHDOG_HEARTBEAT_MAX_S`). That is three watchdog intervals and above
the p99 gap. It also stays above one bounded scheduler step (`STEP_TIMEOUT_S`
600 s) plus the 30 s sleep, so a single slow step does not page.

Once the field is deployed, the `heartbeat` check moves from UNKNOWN to OK,
and that change is announced once.

## Verified

- `tests/test_watchdog.py`: 25 tests with mocked HTTP and TLS. They cover
  healthz failures (HTTP code, unreachable, bad JSON, `ok:false`); heartbeat
  absent → UNKNOWN, fresh → OK, stale → FAIL, null or missing age → UNKNOWN,
  stale wins over a null partner, and 900 s OK / 901 s FAIL at the default; TLS OK,
  near expiry, verify failure and unreachable; token and non-Slack URLs
  refused; env-file parsing. Transitions: no webhook → exit 2 with no state
  written; first run all OK is silent; first-run UNKNOWN is announced once;
  OK→FAIL→(silent)→OK; a failed POST keeps the state and the webhook is not
  logged, then the next run retries; `since` is preserved; a crash (state
  unwritable) exits 4, never 0 or 10.
- `ruff check` is clean at line length 90.
- One live read-only run from the Mac against prod with no webhook:
  `healthz OK: 200 in 0.3s`, `tls OK: 86 days left (2026-12-24)`,
  `heartbeat UNKNOWN: no heartbeat field`, `no alternate route configured`,
  exit 2.
- The FlyCheap VPS has Python 3.13.5 and systemd 257 (read-only check). Nothing
  was installed there.
