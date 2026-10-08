# ATLAS scaffold — step 1 review

Read-only audit of the scaffold as imported (commit `b311ca7`). Every item was
verified against the code, not inferred.

> **Status — all fixed except where noted.** This document is kept as the
> record of what was wrong and why. See the table below for what is still open;
> the fixes are in the commits that follow `b311ca7`.
>
> | Item | Status |
> | --- | --- |
> | A1 deny-list holes, no namespacing | fixed — `2d4af6f` |
> | A2 CRITICAL not cross-provider | fixed — `1a17f55`, Hermes is now `chain[1]` |
> | A3 digest round-trip | fixed — `6fe9d97` |
> | A4 audit append race | fixed — `6fe9d97` |
> | A5 `setup()`/`teardown()` never called | fixed — `1a17f55` |
> | B1 unauthenticated TwiML injection | fixed — `1a17f55`, keypad route added |
> | B2 any Slack member could approve | fixed — `1a17f55`, needs `SLACK_APPROVER_IDS` |
> | B3 API bound loopback, not the mesh | fixed — `1a17f55` + `0d0f903` |
> | B4 `_verify_slack` 500 on a bad header | fixed — `1a17f55` |
> | C1 unrelated runbook as a "known fix" | fixed — `9350360` |
> | C2 dry runs spent the rate-limit budget | fixed — `1a17f55` |
> | **C3 rate-limit window is in-process** | **OPEN — your decision** |
> | C4 LLM-supplied params unvalidated | fixed — `1a17f55` |
> | C5 ungated `executor.rollback` | fixed — `1a17f55` |
> | C6 local-time observation timestamps | fixed — `6fe9d97` |
> | D1 "approve + always" was a dead promise | message made truthful; **design still your call** |
> | **D2 dry run mandatory for Tier 2?** | **OPEN — your decision** |
> | D3 local Python 3.10 | resolved — tests run on 3.14, target stays 3.12 |
>
> Found later, during deployment, and also fixed:
> a check that raised or timed out was dropped entirely rather than reported as
> `UNKNOWN`; four modules defaulted missing API fields to numbers that read as
> healthy; secrets passed as query parameters reached `Observation.detail`; one
> failing scheduler step aborted the morning brief, proposal expiry and
> retention for every subsequent iteration.

`python -m compileall` passes. `ruff` is not installed locally, so the lint pass
is still owed.

**Scope of this read:** all of `kernel/` except `scheduler.py`, `harness.py`,
`memory.py`, `skills.py` and `agents.py` (skimmed only, via grep, for injection
and lifecycle call sites); plus `api/main.py`, `boot.py`, `db/schema.sql`,
`Dockerfile`, `docker-compose.yml` and `Caddyfile`. Individual business modules
were not reviewed line by line — that is step 4.

---

## A. Invariant violations — these need a decision before anything is built

### A1. The permanent deny-list has holes. Every pattern requires a dot. (invariant 3)

`kernel/permissions.py:18`. `fnmatch` is matched against the whole action name,
so `*.payout*` needs a literal `.` somewhere before `payout`. Two consequences,
both verified by running the patterns:

| action name | result |
| --- | --- |
| `stripe.payout_create` | denied |
| `payout` | **allowed** |
| `transfer_funds` | **allowed** |
| `delete_all` | **allowed** |
| `netbird.dns_set` | **allowed** |
| `cloudflare.update_dns` | **allowed** |
| `flycheap.set_dns_record` | **allowed** |
| `guardian.deleteBackup` | **allowed** |
| `mail.purge_inbox` | **allowed** |

Two distinct defects:

1. **Nothing enforces namespacing.** `registry.py:59` registers actions under
   `key = action.name` verbatim. Compare `registry.py:56`, which *does* namespace
   checks as `f"{instance.name}.{check.name}"`. A module that declares an action
   named `payout` — by mistake or otherwise — is registered under a bare name and
   misses the entire deny-list.
2. **`*.dns.*` does not match DNS actions.** It requires a literal `.` *after*
   `dns`. Invariant 3 names DNS as permanently denied; the pattern as written
   catches `vpsie.dns.update` and nothing else. The three realistic spellings all
   pass.

Also `*.delete_*` is case-sensitive and verb-prefix-only: `deleteBackup`,
`purge_*`, `remove_*`, `wipe_*`, `terminate_*` are uncovered.

**This is the highest-priority item in the review.** Suggested fix, for your
approval: enforce `module.action` namespacing at registration (refuse to register
otherwise), and match the deny-list against the *verb half* case-insensitively
rather than fnmatching the whole string.

### A2. CRITICAL never actually uses two providers unless `OPENAI_API_KEY` is set

The model table says CRITICAL is "Opus **and** a second provider". The code does
not deliver that in the default configuration.

`kernel/llm.py:190`:

```python
ROUTES[Need.CRITICAL] = [deep] + ([other] if have_openai else [mid])
```

With no OpenAI key, CRITICAL becomes `[deep, mid]` — Opus and Sonnet, **both
`AnthropicProvider`**. `think_twice` takes `chain[0]` and `chain[1]`
(`llm.py:225-228`), so the "two independent models must agree" check is two
models from the same vendor, same family, same API, with correlated failure
modes and a shared outage surface. It satisfies neither half of the requirement:
not two providers, and not resilient to the provider being down.

Note that `extend_routes_with_failover` appends Hermes to CRITICAL
(`llm.py:316`), but only at position 2, so it is never one of the two compared.
Configuring OpenRouter does *not* fix this.

Two consequences worth separating:

1. **Live, default config:** cross-vendor verification silently isn't happening.
2. **Latent:** the `len(chain) < 2` guard at `llm.py:222` returns
   `(one, one, True)` — one model that unanimously agrees with itself, and
   `reason.diagnose` proceeds to propose. This branch is only reachable when
   there is no Anthropic key at all (CRITICAL is then unset), because `mid`
   otherwise pads the chain to length 2. When it does fire it is the exact
   failure class invariant 7 exists to prevent: a check that cannot run
   reporting OK. It should return `agree=False` and escalate.

Related, and I'd flag it independently: the adjudicator that decides whether the
two answers agree runs at `Need.TRIVIAL` (`llm.py:230`), which routes to the
local 8B model when one is configured (`llm.py:179`). An 8B model is the final
arbiter of whether two frontier models concur about an irreversible production
action. Recommend the adjudication run at `REASON` or above, or be replaced with
a structural comparison of the parsed `action` field rather than a prose
judgement.

### A3. `verify_chain` will report tampering on an untampered log

`kernel/audit.py`. The digest is computed over a **local-offset ISO string**
(`datetime.now().astimezone().isoformat()`, line 25), but the value is stored in
a `TIMESTAMPTZ` column (`db/schema.sql`) and re-read on line 51 as
`row["at"].isoformat()`. asyncpg returns `timestamptz` normalised to UTC, so the
string that comes back is `...+00:00` where the string that was hashed was
`...-04:00`. The recomputed digest differs, and `verify_chain` reports
`"payload altered"` at entry 1 — on a perfectly intact chain.

Verified locally: hashing `2026-09-21T23:36:02.396199-04:00` and reading back
`2026-09-22T03:36:02.396199+00:00` are not equal as strings, and this host's
offset is -04:00, so the mismatch is live here rather than theoretical. What
remains to confirm against a running Postgres is only that asyncpg returns the
column UTC-normalised rather than in the session timezone — the digest breaks
either way unless the server happens to be UTC.

This makes the audit chain non-functional as written, and it will look like an
attack when it fires. Fix is to hash a canonical UTC form (or hash the stored
string, not a reconstructed one). Worth deciding which, since it changes the
digest definition.

### A4. `audit()` races itself; concurrent writes fork the chain

`kernel/audit.py:30-37` does `SELECT ... ORDER BY id DESC LIMIT 1` and then a
separate `INSERT`, with no transaction, no lock and no unique constraint on
`prev_digest`. The executor is async and the scheduler runs checks concurrently,
so two audits interleaving will both read the same `prev` and both insert with
it. The chain then has a fork that `verify_chain` reports as a break at whichever
row loses. Needs a serialised append (advisory lock, `SERIALIZABLE`, or a unique
index on `prev_digest`).

### A5. Module `setup()` / `teardown()` are never called

Declared in the `Module` protocol (`contracts.py:137-138`) and implemented by
modules, but `grep` finds no caller anywhere in the tree. `registry.load` does not
call `setup()`, and neither does `boot()`. Any module that opens an HTTP client,
authenticates, or validates config in `setup()` is running unconfigured — and
under invariant 7 that is dangerous, because a module that never set up may
report `OK` rather than `UNKNOWN`. Nothing calls `teardown()` on shutdown either.

---

## B. Public-surface security

### B1. `/webhooks/twilio/voice` is unauthenticated and injects caller-supplied text into TwiML

`api/main.py:100-110`. Unlike `/webhooks/twilio/sms`, this endpoint performs **no
signature verification and no sender check**, and Caddy proxies all of
`/webhooks/*` to it, so it is reachable from the public internet on 443. The
`say` query parameter is interpolated raw into the XML response:

```python
<Say voice="Polly.Matthew">{say[:900]}</Say>
```

An attacker can close the `<Say>` element and inject arbitrary TwiML verbs —
`<Dial>` to a premium-rate number billed to your Twilio account being the
obvious one — and can also make your number read attacker-chosen text to you.
Needs `_verify_twilio` and XML-escaping of `say`.

### B2. Any Slack workspace member can approve your Tier 2 actions

`api/main.py:50-67` verifies the Slack *signature* (that the request came from
Slack) but never checks *which user* clicked. The Twilio path does this
correctly at line 90 (`form["From"] != TWILIO_TO` → 403). The Slack path has no
equivalent, so approval authority is "anyone in the workspace who can see the
card". Given that the approval card is how Tier 2 becomes execution, this is an
authorization gap, not a style issue.

### B3. The API binds to loopback, not the mesh

`docker-compose.yml` says `ports: - "127.0.0.1:8080:8080"` under a comment
reading "Bound to the NetBird interface only." Those are different things.
As configured, `/ask`, `/agents`, the dashboard and the browser extension
endpoint are unreachable from any other machine on the NetBird mesh — which
breaks step 5 (the extension posts over the mesh) before it is written. It needs
to bind the NetBird interface address.

Related: mesh-only endpoints are mesh-only *by Caddy convention only*. There is
no code-level check that `/ask` is not being served publicly. A Caddyfile edit
silently makes the agent roster public. Worth a defence-in-depth check in the app.

### B4. `_verify_slack` throws instead of rejecting

`api/main.py:43`: `int(ts or 0)` raises `ValueError` on a non-numeric header,
producing a 500 rather than a 401. Fails closed, but noisily and unlogged.

---

## C. Correctness

### C1. `_matching_runbook` returns an unrelated runbook when nothing matches

`reason.py:60`: `return rows[0] if rows else None`. When no runbook signature
matches the finding, it falls back to the highest-`successes` runbook *for that
property* anyway, and passes it to the model as `"known_fix"` with a
`worked_before` count. That biases the reasoner toward a fix for a different
failure. Given invariant 6's spirit — never present something as known when it
isn't — this should return `None`.

### C2. The rate-limit budget is consumed by dry runs that never act

`executor.py:60` calls `_rate_limited` (which appends to the window on the allow
path) *before* the dry run at line 69. Three consecutive dry-run refusals exhaust
`max_per_hour` for an action that has not touched anything. The counter should
increment on actual execution.

### C3. `_recent` is in-process memory

`executor.py:21`. `max_per_hour` resets on every restart and is not shared across
uvicorn workers. For a guard whose stated purpose is "stop repeating a fix that
is not fixing anything", restart-amnesia defeats it — a crash-looping container
gets a fresh budget each time. Belongs in Postgres or Redis.

### C4. LLM-supplied params are passed to actions unvalidated

`tools.py:102` and `approvals.py:96` forward `params` verbatim into
`execute()`, which calls `action.run(**params)` (`executor.py:86`). The action
*name* is correctly constrained to the closed catalog, but the model controls the
parameter dict entirely, and `Action.params` (the declared schema) is never
checked against it. The catalog being closed is a much weaker guarantee if
`delete_older_than="0 days"` is model-chosen. Recommend validating params against
`Action.params` in the executor.

### C5. `executor.rollback` has no gates

`executor.py:100-107` takes a caller-supplied action name and token and calls
`action.rollback(token)` with no `decision.allowed` check, no tier check and no
rate limit. `rollback` lives inside `executor.py`, so this is not
strictly an invariant-1 violation — it is inside the sanctioned component. It is
an ungated entry point within it. Nothing reaches it from `api/` or `tools.py`
today, so it is latent; it becomes a hole the moment anything calls it.

### C6. Observation timestamps are local time; baselines will skew across DST

`contracts.py:54` uses `datetime.now().astimezone()`. `state.baseline` buckets on
`extract(hour ...)`/`extract(dow ...)` of a `TIMESTAMPTZ`, which Postgres
evaluates in the session timezone. Around a DST change one hour-of-week bucket
gets double-counted and another is empty. Store and bucket in UTC.

Verified as *correct*, for the record: `detect.evaluate` handles `UNKNOWN` first
and can never fall through to healthy (`detect.py:32`); `state.baseline` refuses
thin baselines with `n < 5` and refuses `sigma == 0`, so the z-score cannot divide
by zero; the Slack-skip-when-iMessage-is-available logic in `comms.py:107-111` is
correct; `_quiet_now` handles the overnight window correctly; `reason.diagnose`
discards an out-of-catalog name before any side effect.

---

## D. Questions I am not going to answer by guessing

### D1. "Approve + always" is a promise nothing keeps — and keeping it would break invariant 3

The button exists (`approvals.py:63`), the column exists (`schema.sql:69`), the
verdict is stored (`approvals.py:92`), and you are told *"I'll handle this on
this condition myself from now on"* (`approvals.py:104`). **Nothing ever reads
`proposals.always`.** The next identical proposal will ask again.

I did not implement it, because the obvious implementation — consult `always`,
skip the approval gate — is a runtime tier override, which invariant 3
explicitly forbids and tells me not to add. There is a version that does not
break the invariant: treat `always` as a *promotion candidate* that surfaces in
the monthly review and results in **you** changing the tier in code. Which do you
want? Until you decide, the honest short-term fix is to stop the message
promising something that does not happen.

### D2. Is a dry run mandatory for Tier 2?

`executor.py:68` comments "Dry run first, always, even for approved Tier 2 work",
but line 69 guards it with `if action.dry_run is not None`. Tier 1 is covered
(contracts + `classify` both require one), so the gap is only Tier 2 actions that
declare no dry run. Should the executor refuse those, or is the comment simply
aspirational?

### D3. Local Python is 3.10; `Health(enum.StrEnum)` needs 3.11+

The Dockerfile is `python:3.12-slim`, so the container is fine. But this machine
is 3.10.3, which means the test suite in step 3 cannot run locally as-is. Do you
want me to target 3.11+ and run tests in the container, or keep the source
3.10-compatible?

---

## E. Sequencing, so we agree before I build

Your phase discipline says ship with no ability to act at all. Worth noting
explicitly: **steps 4, 5 and 6 are all read-only by nature** — module collectors,
the browser extension, the morning brief. Those are the real phase-1
deliverables. Steps 2 and 3 (make it run, test the expensive parts) support them.

One thing that collapses two passes into one: your step-3 test list asks for
tests that `permissions.classify` covers every deny pattern and that
`audit.verify_chain` detects a tampered payload. Those are tests for exactly the
things A1 and A3 say are broken. The tests and the fixes are the same work, so
A1 and A3 are best done test-first rather than as a separate hardening pass.

So the proposed order is: A1–A5 fixes → step 2 → step 3 → steps 4/5/6, with the
executor never wired to a live trigger until you say so. Item A1 in particular I
would like signed off before I touch `permissions.py`, since you said that file
gets reviewed by hand on every change.


---

## F. Reliable-automation baseline (2026-09-28)

Source: Phase A of "Atlas: Reliable Automation and End-to-End Verification Plan" (Miro, 2026-09-27), read-only baseline taken 2026-09-28 against prod caa65bd. V = verified with evidence, H = hypothesis.

### 1. Revision: what is deployed vs local

| | Value | Tag |
|---|---|---|
| Local branch / HEAD at start | `phases-12-14` @ `94b71b2` | V |
| Local HEAD at end | `caa65bd`. Other sessions committed during the read (`423ae07`, `7e63d26`, `df743c9`, `caa65bd`). The working tree has `M atlas/api/main.py`, another session's uncommitted edit | V `git log`, `git status` |
| Prod `ATLAS_DEPLOY_SHA` at start | `1f22d18`. The py/sh/sql files under `atlas/ bootstrap/ tools/ tests/` in `/opt/atlas` matched `1f22d18` byte for byte, except two prod-only files | V sha1 join |
| Prod `ATLAS_DEPLOY_SHA` at end | `caa65bd`. A deploy landed about 05:02Z mid-read: the api container was recreated at 05:02:08Z, and all 209 `atlas/**/*.py` files inside `atlas-api-1` hash-equal `caa65bd` | V |
| Prod-only, not in git | `bootstrap/deploy/deploy.sh` and `deploy-gate.sh` (the CD path: flock, git archive, rsync, rebuild, health-or-rollback) | V |
| Not compared | `db/`, `ui/`, `Dockerfile`, `docker-compose.yml`, `Caddyfile` | — |
| `.env` holds a short SHA while `deploy-gate.sh` requires 40 hex | Suggests the deploy that wrote it did not come through the gate | H |

So local HEAD and prod both equal `caa65bd` for Python right now. The
difference is the prod-only deploy scripts and a dirty `main.py` locally.

Prod containers (V, `docker compose ps`): `api` (healthy), `caddy`, `ollama`,
`postgres` pgvector pg16 (healthy), `redis`.

Modules in prod (V, boot log 04:43Z): 31 loaded; 117 checks and 62 actions
registered. Four modules are disabled:
- `mirotech`: missing EBAY_* env.
- `screening`, `tiktok`, `youtube`: switched off by `ATLAS_DISABLED_MODULES`.

No action was refused at registration. **No DB table holds registered
actions or tiers.** The registry is in memory (`kernel/registry.py:18-24`), so
the inventory comes from code plus the boot log (V). There is also no
scheduler-jobs table. `guardian` loaded 4 actions, so
`GUARDIAN_WRITE_TOKEN` is set (inferred from `guardian/__init__.py:665-666`
and the log).

### 2. Plan components that already exist

| Component | State | Evidence | Tag |
|---|---|---|---|
| Single executor, tiers in code, Tier 3 deny-list | **Exists** | `kernel/executor.py:221-334`; `kernel/permissions.py:33-73,130-159`; `kernel/registry.py:70-91`; `contracts.py:143-150` refuses a Tier 1 action with no dry run or rollback. Prod `actions_log` has `vpsie.delete_vm` with 0 starts, so it was refused | V |
| Atomic proposal claim, expiry in the same UPDATE (invariant 5) | **Exists** | `kernel/approvals.py:353-357` (decline), `:393-397` (approve), `WHERE status='pending' AND expires_at > now()`; `expire_stale` `:594-597` | V |
| Background approval cut short → failed + receipt on graceful shutdown | **Exists** | `approvals.py:461` `BACKGROUND_DEADLINE_S=900`, `:483-488` CancelledError → `failed` + "cut short by a shutdown" receipt; `kernel/background.py:52-64` `cancel_all` | V |
| Startup sweep of orphaned `approved` proposals | **Exists; live since 05:02Z** | `approvals.py:500-537` `reap_orphans`, called from `api/main.py:55-57` (commits `423ae07`, `7e63d26`). Before the redeploy, prod proposal **#189** (`linkedin.update_page_identity`) sat at `approved` with no finish. It marks `failed` ("check by hand"), not `reconciling` | V |
| Handoff claim with expiry (lease-like) | **Exists, partial** | Queue: `modules/delegate/__init__.py:110-113`. Claim: `api/handoffs.py:56-59` (atomic, `expires_at > now()`). Report: `:86-87`. A claim silent past `REPORT_TTL` (6h) is only *surfaced* (`delegate/__init__.py:153`). There is no re-lease or fencing token | V |
| Hash-chained audit | **Exists** | `kernel/audit.py:25-43` digest over `{event, subject, data}` payload (`:37`), redacted before the digest; append serialised by `pg_advisory_xact_lock` (`kernel/state.py:148`); `verify_rows` `audit.py:46-56`. `actions_log` holds 996 rows, 2026-09-22 19:01Z → 2026-09-28 04:43Z | V |
| `execute.start` written before `run` | **Exists** | `executor.py:285-292` | V |
| Inbound event dedup (Slack) | **Partial** | `api/slack_events.py:90-121`: `event_id` stored as a `slack.event` audit row before acting, plus an in-memory set. It is a receipt only, with no processing state. Retries with `X-Slack-Retry-Num` are **dropped outright** (`:406-407`), so a first delivery that failed is never retried. Only approvers' messages are deduped (`:416-425`) | V |
| Proposal dedup ("one pending per cause") | **Partial** | `approvals.py:153-161`. It only matches a *pending, unexpired* row, and the index `proposals_dedup` is **not UNIQUE** (`\d proposals`), so check-then-insert can race. Prod: `gitlab.open_issue` has 151 proposals from 33 dedup keys, 124 of them expired | V |
| Transactional outbox | **Absent** | `kernel/comms.py:413-470` `notify` sends inline. A failure writes only a `comms.undelivered` audit row (`:270-283`, prod: 19), never retried. `brief_queue` is only a quiet-hours deferral (`comms.py:439-444`) | V |
| Durable workflow state | **Absent** | `kernel/workflows.py` is a read-only graph renderer (docstring). No workflow, step or checkpoint table exists among the 67 prod tables. `incidents` has 0 rows, so the plan's incident is not persisted | V |
| Worker leases / fencing | **Absent** | No `lease`, `fencing`, `FOR UPDATE`, `SKIP LOCKED` or job advisory lock anywhere in `atlas/`. The one advisory lock is the audit append. There is one api process with an in-process scheduler | V |
| Reconciliation of uncertain effects | **Absent (generic)** | Prod has 3 `execute.start` rows with no finish or error (`linkedin.publish_post` ×3). `deploy.sh`'s own comment cites "a LinkedIn publish died mid-run once, #181/#182". Only LinkedIn has `posted_unverified` (`linkedin/publish.py:354-375`) `[map]` | V |
| Verifiers | **Ad hoc, 11 of 68 actions** | No verifier hook in `Action` (`contracts.py:77-141`) and no read-back in `executor.execute`. Some actions read back inside `run`: LinkedIn publish, reshare and identity; browser submit; research figures; nightshift job check. The rest trust the provider's 2xx | V |
| Kill switch / pause | **Partial** | Only global `ATLAS_WATCH_ONLY` (`executor.py:77-91,241-253`, read per call, change = restart) and load-time `ATLAS_DISABLED_MODULES` (`registry.py:32-42`). No per-module runtime pause and no pause on recurring jobs | V |
| Watch-only | **Exists, OFF in prod** | `ATLAS_WATCH_ONLY=false`. Owner decision, recorded at `BUILD_ORDER.md` Gate 9 ("OPENED 2026-09-22 BY INSTRUCTION"); last `execute.watch_only` event 2026-09-22 22:35Z | V |
| Independent watchdog | **Absent for ATLAS** | Only in-process self-checks (`modules/comms`, `backups`). Docker `HEALTHCHECK` does not restart an unhealthy container. No host cron (`crontab -l` empty). Mac Nexus agents did not reference the ATLAS host in a quick grep | V absent in repo/host; H for Mac |
| Rate limits | **Exists, in-memory** | `executor.py:103-141`, per action, per module (10/h) and global (30/h). Resets on restart (FINDINGS C3, still open) | V |
| Backups | **Exists; restore drill absent** | Host timer `atlas-backup` daily 06:20Z → `pg_dump -Fc`, keep 14, rsync to `atlasbak@203.0.113.10` (`bootstrap/backup/atlas-backup.sh`). The timer was installed 2026-09-28 04:22Z and **fired at 06:20Z** (`atlas-20260928-0620.dump`, 9.8 MB; off-box copy ok). There were 2 manual runs before that. The `backups.backup` check reads OK, but `offsite_ok` is only rsync's exit 0. A restore drill is scripted in `ops/restore_drill.sh` (isolated, network-less, refuses under 5 GB free), verified locally but not yet run on prod | V |
| Model failover | **Exists** | Prod log "failover chain extended with Hermes via OpenRouter". The `vpsie` agent_run shows Hermes 404 → Claude fallback. Private lane stops rather than going to cloud unless `ATLAS_PRIVATE_ALLOW_CLOUD` (`kernel/llm.py:312-318,711-718`) `[map]` | V/[map] |
| Agent loop bounds | **Exists** | `kernel/harness.py:234` `max_turns`, `:274` limit message; prod `agent_runs` shows "Stopped after the tool-call limit" | V |
| DB migrations | Single idempotent `db/schema.sql` (910 lines), applied by `deploy.sh`. No versioned migrations or rollback | V |

### 4. Gap list against plan phases B–H (→ FINDINGS.md)

IDs are prefixed `RA-` (Reliable Automation) so they do not collide with
FINDINGS.md's existing A1–E. All V unless marked.

> | Item | Status |
> | --- | --- |
> | RA-B1 durable workflow record | BUILT, off: `kernel/durable.py` + `workflows` table, with the state machine in code (`ATLAS_DURABLE_CORE`, default off). No producers yet |
> | RA-B2 reconciliation state | PARTIAL: 116107c closes runs a killed process abandoned (`execute.abandoned` + one message; the deploy gate counts them). An unknown *external* effect is still not reconciled. |
> | RA-B3 outbox | BUILT, off: `kernel/outbox.py`. Dispatches only via `executor.execute`; the idempotency key is action+version+destination+occurrence. Notifications stay outside it (reviewed decision) |
> | RA-B4 leases / fencing | BUILT, off: `kernel/leases.py`. Monotonic fencing, fail closed in the same SQL. Scheduler steps leased when the flag is on (not `checks`/`approvals`) |
> | RA-B5 Slack retries dropped | DONE: 116107c (retries go through the durable dedup) |
> | RA-B6 non-unique proposal dedup | DONE: 759a4fe (an automatic cause stays quiet 3 days after an expired card, 7 after a declined one) |
> | RA-B7 per-action idempotency | PARTIAL, branch `ra-b7-0928`: `kernel/idempotency.py` names the run (`proposal:<id>` from approvals, `outbox:<id>` from dispatch; fallback = action + params digest, never the time). DONE: `stripe.retry_invoice` (Stripe `Idempotency-Key` from invoice + run), `messages.reply` / `messaging.reply` (claimed in `effect_claims` before the send; same run twice = one send; an unknown outcome is never resent; a provider refusal releases the claim), `matrix_rooms.send_reply` (txn from the run + the claim; a 2xx counts as sent; redact txn from the event), reels (a render with a `reel_posts` row posts nothing, and the pipeline treats it as posted: no remake, no card). **Decided 2026-09-28 (Miro): retry_invoice always charges and the customer always gets a receipt (emailed once per charge, idempotent).** It stays Tier 2 in code -- a charge is irreversible and `contracts.py` refuses an irreversible Tier 1 -- so each retry still needs one approval. Limits: a claim left in doubt has no reconciler; on a proposal it only blocks that card (a new card is a new key), but on a path with no named run (intent, spoken) it blocks that exact (inbound, text) or (room, body, to) until cleared by hand (`DELETE FROM effect_claims WHERE action=... AND key=...`); after `unpublish_reel` the render's `reel_posts` rows remain, so a rolled-back render cannot repost. Still OPEN: `gitlab.open_issue` lookup, `vpsie.snapshot_all`, calendar writes, the LinkedIn click-to-status crash window, the per-leg reel crash window (a crash between legs, before `reel_posts` is written) |
> | RA-C1 per-module pause | PARTIAL: 759a4fe kill switch (`/atlas pause`, `pauses` table). Coverage of host timers is still to verify. |
> | RA-C2 crosspost tier bypass | Decided 2026-09-28 14:05Z (Miro, settled): reels that pass every check post automatically (`REELS_AUTOPUBLISH=on`); a failed check gets one remake, then a card. DONE, branch `ra-b7-0928`: the Instagram leg runs under `instagram.publish`'s own gates via `kernel/legs.py` -- parents listed in code, registered + allowed, module pause, the executor's shared limiter (action + module window), `leg.start`/`leg.finish`/`leg.refused` audit rows naming the parent. Autopublish holds a reel whose Instagram hour is spent; `post_or_card` treats that (and any executor hourly refusal) as *wait*, never a remake or card. A hold by an hourly allowance leaves the calendar / series / import row untouched, so the next 30-minute tick retries it; the daily-drafts path (`on_fail="skip"`) logs it as waiting for tomorrow, as it did for the day cap |
> | RA-C3 writes outside executor | answer_principal: MOOT, WhatsApp was removed upstream (d2f8853) along with `answer_principal` and `messaging.reply`. Host reel jobs: expire 2026-10-02 (RA-F5). Screening `<Dial>`: OPEN (module off in prod) |
> | RA-C4 approval version / hash | DONE, branch `ra-ver-0929`: `registry.action_version` (catalog version: name, code tier, reversibility, dry run, principal/card-only, param schema, module `ACTION_VERSION`) and `registry.payload_hash` are recorded on every card (`proposals`) and queued row (`outbox`). The executor refuses a mismatch before anything runs (`dispatched=False`, `policy`, `execute.superseded`). The card or row becomes `superseded` (terminal) and a fresh card asks again. The proposal claim is unchanged: the record is read beside it. NULL (legacy) is not checked. Not covered: live approvals with no card (spoken, intent), and the rendered file behind a reel card (S09) |
> | RA-C5 matrix guard hole | DONE, branch `ra-b7-0928`: a missing `to` is refused (dry run and send); the room's real name is read from the homeserver at send time (404 = unnamed DM, id-only; any other read error refuses) |
> | RA-C6 guardian trust-label writes | **Decided 2026-09-28 (Miro): suggest only; write path removed.** Branch `ra-b7-0928`: `guardian.mark_untrusted`, its tool and the hub write functions are gone; an incident plan carries a suggestion (device, network, why, Vault link) in `manual`, and the `open_incidents` reading names it. `docs/capabilities.json` dropped the old row on 2026-09-29 (ra-v4) |
> | RA-C7 in-memory rate limits | OPEN (= FINDINGS C3, your decision) |
> | RA-D1 verifier contract | DONE for coverage: `kernel/verify.py`, and the executor hook behind `ATLAS_VERIFY_AFTER` (OFF in prod until the linkedin second-session cookie question is settled). **71 of 71 actions have a registered read-back verifier** (ra-v4 added the last 45; `messaging.reply` and `guardian.mark_untrusted` are gone from code, the four `mac.*` actions were missing from the inventory). 63 can ever read verified. 8 cannot, by design: `mac.open_app`, `mac.play_music`, `mac.music_control`, `mac.add_reminder` (the Mac agent has no read-only op), `tiktok.upload_draft` (its status reads are POSTs), `mirotech.price_check` (writes nothing), `browser.submit_form` (the effect is the recipient's) are unknown at best; `guardian.contain` is always contradicted. `purchasing.prepare_flight` has a row read but its run cannot return ok today. 25 actions have reconcilers; `reconcile_unknown` is wired into `reap_abandoned_runs` behind `ATLAS_RECONCILE` (off); `ActionResult` has no failure_class yet |
> | RA-D2 partial success as ok | DONE (verifier): `vpsie.snapshot_all` partial → contradicted, listing the failures; missing reel legs → contradicted, naming the leg |
> | RA-D3 detached steps unbounded | DONE: 759a4fe (detached scheduler steps have deadlines) |
> | RA-D4 incident re-posting | DONE, branch `ra-e2-0928`: `fixes.attempted` carries a fingerprint (health, severity, last OK, detail without read-times/elapsed times). Unchanged stays quiet until `REMIND_HOURS` = 7 days; a material change posts again, never inside the 24 h `RETRY_HOURS` floor. Decided before `route()`, so a quiet incident costs no model call. |
> | RA-E1 OK-on-empty checks | PARTIAL: `ra-e-domains` (merged) fixes flycheap, gitlab, stripe, fiverr, vpsie, mirotech, tiktok, instagram DMs, linkedin sweep reads, guardian `token_scope` and nightshift `jobs` (test per check). Still open: see the RA-E1 note below. No "disabled by owner" health state exists (gap noted below). |
> | RA-E2 calendar timezone | DONE, branch `ra-e2-0928`: one resolver (`calendar/zone.py`) in the primary calendar's own `timeZone` for every write, dry run and read-back; spring-forward gap refused, fall-back hour = first occurrence (stated), end computed in UTC; no zone = no write / UNKNOWN read-back; duplicate slot + attendees returns the existing event. D11 tests. Leftovers: see the RA-E2 note below. |
> | RA-E3 FlyCheap coverage | DONE in code, branch `ra-e-domains`: `flycheap.coverage` check tracks configured / enabled / reachable / fresh per source; one stale-source note via signals, no action. `/api/health` exposes only "configured" and one global poll age, so the check reads UNKNOWN in prod until FlyCheap exposes per-source status. |
> | RA-E4 Guardian synthetic path | DONE as a test, branch `ra-e-domains` (`tests/test_guardian_synthetic_path.py`); `open_incidents` evidence now says `investigation: complete|incomplete`. |
> | RA-E5 reel rights / identity checks | PARTIAL, branch `ra-e2-0928`: one quality gate (`content/quality.py`) in `post_or_card` for every producer, autopost or card: native 1080x1920 (no rotation), playable stream length in band, audible sound, AI disclosure, captions on series. Failure = quality exception: one remake, then a card led by the reason. Rights and identity continuity still OPEN; open questions in the RA-E5 note below. |
> | RA-F1 duplicate reviews | DONE: 759a4fe (brief and review claimed once per window in Postgres, `schedule_runs`) |
> | RA-F2 missed-run policy | DONE: 7b9aaba. Every loop step declares skip-stale, run-once-on-recovery or bounded catch-up (`scheduler.POLICIES`, `kernel/missed.py`). brief, review, weekly, marketing plan and judgment record each window in `schedule_runs` (run, failed, or skipped with the reason). After an outage, at most one window per job runs. No loop job uses bounded catch-up (nothing per-window that sends nothing); the engine and its tests are ready for one. Schema: `schedule_runs.scheduled_for`, `.detail` |
> | RA-F3 backpressure | DONE: 116107c (automatic drafts stop at the review backlog cap) |
> | RA-F4 independent watchdog | INSTALLED on the FlyCheap VPS 2026-09-28 (Miro OK'd); the timer stays disabled until Miro provides a Slack webhook. Heartbeat BUILT: 9aa03f4 adds `heartbeat.{cycle_age_s, observation_age_s}` to `/healthz` (null when unknown, never 0; `ok` unchanged). ea1791e updates the watchdog: over 900 s is FAIL, null or missing is UNKNOWN. The copy installed on the VPS predates ea1791e; refresh it before enabling the timer |
> | RA-F5 transient reel timers | Expires 2026-10-02, no action. Verified read-only 2026-09-28 23:33Z: only `reel-hosp2..5` remain, and the last fires 2026-10-02 07:03Z. After that the ATLAS reels engine owns all series (see RA-F5 below) |
> | RA-G1 shadow mode | BUILT, nothing shadowed: `kernel/shadow.py` + `docs/shadow-mode.md`. A shadow is a `pauses` row (`shadow:<module>`/`shadow:all`, CLI only), refused in `executor.execute` via `pause.active_for` (audited `execute.paused`, **no params hash**: executor.py unchanged, one-line diff proposed) and in the outbox before the fence (`shadowed`, terminal; `shadow.would_execute` with params hash). `atlas.cli shadow-report --since` lists would-run vs ran and flags any run inside a shadow window. Module granularity only; host reel jobs not covered. The 48 h run (21.G.1) and canaries each need Miro's approval |
> | RA-H1 CD not in git | DONE in git: `ops/deploy/` holds `deploy.sh` + `deploy-gate.sh` byte-identical to the host (read 2026-09-28, `SHA256SUMS`); no secret in either. CI still reaches the host copy through the forced command; `tests/test_deploy_scripts.py` pins the hashes and the CI→gate→deploy.sh chain, and `ops/deploy/check-prod-sync.sh` compares the host copy (read-only). Kept out of `bootstrap/deploy/` so a push cannot rewrite the root-run path; installing a change there is manual, with Miro's OK |
> | RA-H2 restore drill | SCRIPTED, not run on prod yet: `ops/restore_drill.sh` + `docs/restore-drill.md` (refuses under 5 GB free; prod had 14 GB free at 17:50Z on 09-28) |

#### B — Durable core
- **RA-B1** No durable workflow record. There are no fields for `workflow_id`, `state`, `current_step`, `attempt_count`, `lease_owner`, `fencing_token` or `provider_request_ids`. `proposals` has no `version` or `approved_payload_hash` column (`\d proposals`).
- **RA-B2** No reconciliation state. An executor crash between `execute.start` and `finish` leaves an unknown outcome with nothing to resolve it. Prod has 3 such rows. `reap_orphans` covers only `approved` proposals, marks them `failed`, and does not cover Tier 1 or intent.act runs.
- **RA-B3** No outbox. Notifications and receipts are sent inline. Undelivered ones are audited but never retried. Card threading uses in-memory `sent_ref` (`docs/slack/2026-09-27-slack-review.md` §7).
- **RA-B4** No leases or fencing. A single process is assumed. The scheduler guards are process memory (`scheduler.py:32-34,426-429,600,624,660,697-698,843-849`).
- **RA-B5** Slack retries are discarded (`slack_events.py:406-407`). An event whose first delivery failed is lost, which conflicts with R06/R07.
- **RA-B6** `proposals_dedup` is not unique. Dedup covers only pending rows, so an expired cause re-proposes forever. Prod: 124 expired `gitlab.open_issue`.
- **RA-B7** Per-action idempotency is missing for external writes:
  - no check before send:
    - `messages.reply` (`messages/__init__.py:292-311`, V);
    - `messaging.reply` [map];
    - `stripe.retry_invoice` (no Idempotency-Key, `stripe_billing/__init__.py:272-281`, V);
  - `matrix_rooms.send_reply` uses a time-based txn id (`:341`, V);
  - `gitlab.open_issue` has no existing-issue lookup;
  - `vpsie.snapshot_all`;
  - calendar writes;
  - `content.publish_reel` / `autopublish_reel`: no per-media "already posted" check before the legs run (`content/crosspost.py:249-270`, V);
  - `linkedin.publish_post` crash window: the Post click is at `linkedin/publish.py:460`, but `status='published'` is only set at `:509` (V).

#### C — Policy and control
- **RA-C1** No per-module runtime pause. Watch-only is global and needs a restart. Recurring jobs and host timers are not covered by any pause.
- **RA-C2** Tier bypass inside an action: `content/crosspost.py:122` calls `instagram.publish` as a function, skipping that action's Tier 2 and `max_per_hour=2` (V). It is still inside an executor run.
- **RA-C3** Writes outside the executor:
  - `messaging.answer_principal` sends Telegram/WhatsApp from a webhook (`messaging/__init__.py:117-128`, V). It is limited to Miro's own chat and audited, but watch-only does not stop it.
  - The screening `<Dial>` patch-through (`screening/flow.py:390-391`) `[map]`; the module is off in prod.
  - Host `reel-hosp*/run.sh` calls Veo (paid) and INSERTs `veo_usage` via psql, outside ATLAS entirely.
  - *2026-09-28, branch `ra-b7-0928`, `answer_principal` stays outside the executor.* Routing it through `executor.execute` was judged disproportionate: watch-only (the default) would silence ATLAS's answers to Miro in exactly the phase where it must still tell him things (`comms.notify` is outside the executor for the same reason); the answers would spend the module and global hourly budgets that real actions need; a Tier 1 action needs a rollback no sent message has; and a Tier 2 / `principal_only` action would need the webhook to forge an `approved_by`. Instead the guard stands alone: the recipient is `MIRO_WHATSAPP_NUMBER` itself (digits), never the caller's `chat_id`; every attempt is audited with the recipient; and `tests/test_ra_c3_answer_principal.py` pins `whatsapp.send_text` to its two callers (`answer_principal`, the Tier 2 `messaging.reply`).
- **RA-C4** No approval version or payload hash. Params cannot be edited in place, but a refreshed preview is not identity-checked (Slack review §7).
- **RA-C5** `matrix_rooms.send_reply` guard hole: an omitted `to` defaults to an internal recipient (`matrix_rooms/__init__.py:333`, V). A blank `room_name` leaves only the opaque room id for silent-room matching (`:120-126`, V).
- **RA-C6** *(Decided 2026-09-28, Miro: suggest only; write path removed -- see the status table.)* `guardian.mark_untrusted` was Tier 2 but wrote a trust label. The plan wants trust and label warrants human-only; this needs a policy call. `network` is regex-checked only, not tenant-checked `[map]`.
- **RA-C7** Rate-limit windows are in-memory (FINDINGS C3, open). Every deploy resets them, and prod had many deploys on 09-27/28.

#### D — Verification and recovery
- **RA-D1** No `verification_handler` contract. 56 of the 68 actions have no read-back (JSON; includes n/a for blocked/read-only ones). *(2026-09-29: resolved for coverage, all 71 actions now have a verifier, 63 of which can read verified; see the status table.)* There is no failure classification (transient, auth, disabled, post-dispatch timeout, and so on). `ActionResult` is `ok` + `detail` only.
  - *Fixed 2026-09-28, branch `fix-ig-dm-0929` (verifier review): `messages.reply` sent to the wrong id.* It posted `{"recipient": {"id": <thread_id>}}`, but `thread_id` is the Instagram CONVERSATION id that `/{page}/conversations` returns, and the Graph send addresses a person (IGSID). The reply now resolves the recipient from the conversation's `participants` (Page token in the Authorization header, never the URL), dropping the Page and this account's own IG id. It caches the id in `messages_inbound.recipient_id` for every row of the thread and sends to that id. When exactly one participant does not remain (none, several, or an unreadable conversation), the reply is refused with `refused: no recipient for conversation ...`, before the RA-B7 claim, so nothing is sent or held; there is no fallback to `sender`. `send()` also refuses a recipient equal to the conversation id. The evidence keeps `thread_id` and adds `recipient_id` and the Graph `message_id` for a read-back. `messages.draft` never sends and the dashboard's `thread` is display only, so no other path treats the conversation id as a recipient. Tests: `tests/test_ra_b7_idempotency.py` (mocked Graph API).
- **RA-D2** Partial success reported as success: `vpsie.snapshot_all` returns ok on partial failure `[map]`. Reel publish records legs individually, which is good, but media 10 has instagram and tiktok rows and no facebook row, with no reconciliation.
- **RA-D3** Scheduler detached steps have **no timeout**. `_detach` (`scheduler.py:919-934`) versus `_step` (`:945-956`). Prod: `signals` was skipped every 30 s from 04:44Z to 04:57Z+ (stuck). This also applies to `brief`, `weekly`, `fixes`, `judgment` and `agent_shifts`.
- **RA-D4** `fixes.sweep` is bounded (`fixes.py:35-37`), but re-posts an unchanged incident every 24 h (Slack review §7).
  - *2026-09-28, branch `ra-e2-0928`.* Fixed as in the table. Rows written before the fingerprint compare by health alone. The incident card's Retry line now says "only if it changes; unchanged, raised again <date>". `FIX_REMIND_HOURS` overrides the 7 days.

#### E — Domain completion
- **RA-E1** Several checks answer OK on empty or absent data:
  - flycheap `api` with an empty checks dict reads OK "all 0 subsystems healthy" (`flycheap/__init__.py:146-153`, V);
  - gitlab runners;
  - stripe list checks;
  - fiverr selectors;
  - vpsie token served from a memory or disk cache (`vpsie/__init__.py:135-142`, V that the cache is used; that the `auth` check then reads OK is `[map]`);
  - vpsie `protection` when the policy is unreadable.
  The gitlab runners, stripe, fiverr and vpsie `protection` items are `[map]`. All of these are invariant 7 risks.
  - *2026-09-28, branch `ra-e-domains`.* Fixed (UNKNOWN with a reason, and a test with a positive control for each): every item above, plus vpsie `resource_ledger` (no total), mirotech `listings`/`orders`/`stale_listings`, tiktok `video_stats` (error envelope), instagram DM threads that failed to read, linkedin sweep page reads (now raise, so the surface counts as unread), guardian `token_scope` (only a literal `read` is OK), and nightshift `jobs`. The nightshift fix: a docker daemon error is not "missing", and a created, paused or restarting container no longer closes the job.
  - Still open, not changed: netbird `new_peers` (`last_evidence` returns `{}` on a DB error as well as on no history, and telling those apart needs `state.py`); mail `watch_active` (reads `/profile`, never the watch expiry); mail `followups` (fetched with `format=metadata`, so no body); linkedin `sent_invites` (an empty page flips pending invites to accepted); tiktok and youtube token checks served from cache; the medium and low items from the sweep (matrix encrypted rooms, rhythm `HOME_IPS`, calendar and shop pagination, and others).
  - **Gap, "disabled by owner":** `Health` has only ok, degraded, failing and unknown. A switched-off module (`ATLAS_DISABLED_MODULES`) is shown as the dashboard's `setup: "switched off"` label over `health: "unknown"` (`api/dashboard.py:225-241`). Orphan check rows of a live module are dropped (`:151-158`). A switched-off module's old rows are replaced by the label. Neither is a health state. Adding one means changing `contracts.py` and the UI, which needs an owner decision, so it was not invented here.
- **RA-E2** Calendar has no `zoneinfo` or `timeZone` anywhere in `atlas/modules/calendar/` (V, grep). Naive datetimes and all-day events at UTC midnight are `[map]`. The D11 test is expected to fail (H).
  - *2026-09-28, branch `ra-e2-0928`.* Fixed: `create_block`, `book_with` and both dry runs go through `zone.slot_for` (zone read from `GET /calendars/primary`; the payload carries the offset and `timeZone`). `readback` uses the same resolver and compares instants in UTC (Python's inter-zone `==` is always False inside the repeated hour, PEP 495). All-day dates start at midnight in the calendar's zone, and deadline days are bucketed in it. A duplicate returns the existing event with no rollback token, and the verifier reads its id from evidence. Tests: `tests/test_calendar_zone.py` (DST dates computed from zoneinfo).
  - Still open: `screening.book_slot` writes a calendar event without the calendar's zone or a duplicate check (the module is off in prod). `booking.free_slots` and `guard` use host-local time, which is UTC in the container unless `TZ` is set (not verified on prod); a focus block of "08:00-11:00" would then be read as UTC. `offer_slots` sends prose times, with no zone check.
- **RA-E3** FlyCheap disabled-vs-unreachable: a disabled provider only raises an INFO signal, and freshness trusts FlyCheap's own flag `[map]`.
- **RA-E4** Guardian is read-only by design except 4 Tier 2 writes. There is no synthetic-alert end-to-end path.
- **RA-E5** Reels: no-stills is enforced (`content/__init__.py:349-355`). Output checks exist (1080x1920, audio, ≥5 s, in `autopublish_check`) `[map]`. There is no rights or identity-continuity check.
  - *2026-09-28, branch `ra-e2-0928`.* Quality gate built (`content/quality.py`, run by `kernel.reels.post_or_card` before autopost AND before a card, with `REELS_AUTOPUBLISH` on or off). Checks from the file: native 1080x1920 with no display rotation; the video stream's playable length (series 60-90 s, others 30-60 s, a calendar reel its package's planned length ±1.5 s); an audio track above `SILENT_DB`; the "Made with AI" label; captions on a series episode. New column `content_media.captions` is set at render time; older media fall back to the render's audited detail. `import-reel` refuses only an unreadable file or a bad caption; any other failure is filed and carded (no remake is possible).
  - Owner decisions / to confirm:
    - `CALENDAR_SLACK_S = 1.5` s. Under Miro's 60-90 s series band, 18 of the 30 story-package jobs (FOOD 12-35 s, COURT 32-40 s, KINDNESS 40-42 s) would fail, so calendar reels are held to their package's own length instead.
    - `beat_range` caps series beats at `VEO_DAILY_CLIPS`. Below 8, every drafted series episode is under 60 s and fails the gate.
    - Host-made series imports need a subtitle stream or a `.srt`/`.vtt`/`.ass` beside the file. Without one they become cards, not autoposts.
  - Still open: rights and identity-continuity checks. `crosspost.autopublish_check` still runs its own older probe (≥5 s); the crosspost branch should switch it to call `quality.check_media`.

#### F — Noise and operations
- **RA-F1** Duplicate reports. `review.send` has no durable once-per-day guard (`kernel/review.py:248-290`; `scheduler.py:640-657` is process memory). Prod: 10 `review` reports on 2026-09-27 between 23:00 and 23:56Z; 37 `brief` rows on 09-22 (before the brief DB guard). Mechanism V; the correlation with deploy restarts is H, but strong.
- **RA-F2** No persisted scheduled-occurrence table and no missed-run policy (skip, once, bounded). `marketing` admits it cannot recover a restart after a missed day (`scheduler.py:739`).
  - *2026-09-28, 7b9aaba.* Policies are declared in code, one per loop step (`scheduler.POLICIES`); a test fails if a step has none.
    - **skip-stale** (a window runs only until local midnight of its day; older windows are recorded `skipped`): brief, review, reels digest, reels nudge.
    - **run-once-on-recovery** (only the newest missed window runs, however late; older ones are `skipped`): weekly summary, marketing plan, nightly judgment. Agent shifts are run-once through `agent_runs`.
    - **Interval** jobs (checks, approvals, signals, rhythm, retention, fixes, outbox, reels calendar, experiment measuring) have no windows to miss. Their first tick after an outage runs once. The calendar drains a backlog at the daily post cap, one card a day.
    - **Bounded catch-up** is built and tested but unused: no loop job does per-window work that sends nothing.
  - Rules:
    - history that cannot be read runs nothing (fail closed);
    - a job with no recorded history considers only its newest window, and only inside its grace, so the first deploy never replays from before records began;
    - `claim_once` jobs (marketing, judgment) are never run twice for a window, including after a crash or a partial filing. A failed weekly summary is retried by the next process, not every cycle; `weekly.send` refuses a second one per ISO week (3408220).
  - Behaviour changes:
    - a restart later in the week no longer re-sends the weekly summary;
    - a restart after 03:00 no longer re-runs the nightly retrain;
    - a missed marketing Sunday is now filed once after a Monday restart (the gap quoted above).
  - Reels digest and nudge keep `reports` as their once-only record and were not rewired (reels posting is an owner-settled area).
- **RA-F3** No queue backpressure or oldest-pending metric. `signals` 146 undelivered vs 6 delivered; `brief_queue` 20 undelivered.
- **RA-F4** No independent watchdog or alternate alert route (see §2).
  - *2026-09-28.* The watchdog was installed on the FlyCheap VPS, with its timer disabled pending a Slack webhook. The `/healthz` heartbeat it reads is built (9aa03f4, ea1791e; `docs/watchdog.md`).
- **RA-F5** Transient systemd timers (`/run/systemd/transient/reel-hosp*.timer`) vanish on reboot, and the job scripts (`/opt/reel-jobs`, 67 dirs) are not in git.
  - *Resolution: expires 2026-10-02, no action.* Read-only check on 203.0.113.10, 2026-09-28 23:33Z (`systemctl list-timers`, `/run/systemd/transient/`, `journalctl`).
  - Four transient timers remain. Each is `Persistent=yes` and `RemainAfterElapse=no`, and each runs its own `/opt/reel-jobs/hospN/run.sh`:

    | Timer | Fires (UTC) |
    |---|---|
    | `reel-hosp2` | 2026-09-29 07:03 |
    | `reel-hosp3` | 2026-09-30 07:03 |
    | `reel-hosp4` | 2026-10-01 07:03 |
    | `reel-hosp5` | 2026-10-02 07:03 |

  - `reel-hosp` itself fired 2026-09-28 07:03Z. It ran 16 min and exited successfully, and its timer is gone.
  - Each `run.sh` is 13 lines: `veo.py`, then `atlas.cli import-reel` via `docker compose`, and a `psql` INSERT into `veo_usage`. Its contents are not copied here.
  - `/opt/reel-jobs` now holds 74 dirs and is not a git repository.
  - After 2026-10-02 no host reel timer remains. The ATLAS reels engine (`kernel/reels.py` calendar and series) owns every series, so the duplicate-owner row in §6 and the host-job part of RA-C3 lapse with it.
  - A reboot before 10-02 drops the remaining episodes. That is accepted: the engine carries the series.
  - The server was not modified.

#### G/H — Shadow, canary, rollout
- **RA-G1** No shadow mode separate from watch-only. There is no proposed-vs-observed comparison and no staging environment. Tests: 127 files under `tests/`, **not run** in Phase A to keep the tree clean.
- **RA-H1** CD exists only as prod-only scripts, not in git. There is no per-capability enablement other than module switch-off.
- **RA-H2** No restore drill. Backups are unproven (§2).

### 6. Duplicate or overlapping schedulers

| Work | Owners | Status | Tag |
|---|---|---|---|
| AI-stories reels generation + posting | (a) ATLAS scheduler: `_maybe_reels` + calendar tick → `content.render_calendar_job` (44 runs) → `post_or_card`; (b) host transient timers `reel-hosp..5` 07:03Z → `veo.py` + `atlas.cli import-reel` → same `post_or_card` | **Two owners, one account.** Coordination is partial: a shared daily cap via `reel_posts` (`crosspost.py:posted_today`) and a Veo reservation (`VEO_RESERVED_*`, `calendar.py:532-537`) `[map]` | V |
| Veo quota accounting | ATLAS reserves via `render.py:328-345` `[map]`; host `run.sh` INSERTs `veo_usage` by psql | Two writers | V |
| Daily review | Scheduler only, but re-fired per restart (RA-F1) | Not two schedulers; the same effect as one | V |
| LinkedIn sweep + posting | ATLAS `linkedin` module (sweep check 12 h, publish Tier 2) vs Mac task `linkedin-daily-sweep` | Mac task **disabled**; `com.atlas.linkedin-state-sync.plist.disabled`. Docs disagree: `AGENTS.md` lists linkedin among Mac agents, `CLAUDE.md` says inside ATLAS only | V |
| Fiverr | ATLAS `fiverr` checks vs Mac task `fiverr-agent` | Mac task **disabled** | V |
| Backups | Host timer only; ATLAS just reads `status.json` | Single owner | V |
| Handoffs | Mac `atlas-handoff-runner` (enabled, 15 min) consumes ATLAS's queue | Producer/consumer, not a duplicate | V |

### 7. Top 10 risks (ranked)

1. **Unattended public posting is armed in code, and a host timer drives it at 07:03Z.** Watch-only is `false` (V). `content.autopublish_reel` is Tier 1, and `REELS_AUTOPUBLISH` defaults on (`crosspost.py:301-303`, V). `reel-hosp.timer` fires 2026-09-28 07:03Z → `import-reel` → `post_or_card` → autopublish (`kernel/reels.py:293-296`, V). Whether the env overrides the default was not read. No autopublish has executed in prod yet (V).
   The autopublish code was committed at 2026-09-28 00:38Z (`07bb2f1`). The last reel card, #180, was raised at 23:01Z the day before. Since then, `actions_log` has no `content.autopublish_reel` row and no new reel card. So `post_or_card` has not run since the code shipped (V; every `execute()` path audits).
   The switch state is therefore **undetermined**. 07:03Z would be its first live exercise (H).
2. **Unknown external outcomes are never reconciled.** 3 LinkedIn `execute.start` rows have no finish. There is no RECONCILING state, and the LinkedIn crash window (click at `publish.py:460`, status at `:509`) can re-post (V). Proposal #189 was left stuck at `approved` (V).
3. **Duplicate side effects on retry or double run.** `messages.reply`, `stripe.retry_invoice` and `matrix_rooms.send_reply` have no idempotency (V). `gitlab.open_issue` has no lookup `[map]`, and reels have no per-media posted check (V).
4. **Stuck detached scheduler steps with no timeout.** `signals` was observed stuck for over 13 min (V). The same applies to brief, weekly and fixes.
5. **Restart-driven duplicate reports and in-memory guards/rate limits.** 10 reviews in one hour (V). Rate limits reset on each deploy (V). Deploys are frequent (4 commits deployed during this read).
6. **No independent watchdog.** A dead api process or scheduler reports nothing. Docker does not restart an unhealthy container (V).
7. **Backups unproven.** The timer fired at 06:20Z on 09-28. A restore drill is scripted (`ops/restore_drill.sh`) but has not yet run on prod. "Offsite ok" means only rsync exit 0 (V).
8. **Writes and paid calls outside the executor:**
   - host reel jobs (Veo spend, direct DB insert);
   - `messaging.answer_principal`;
   - `crosspost` → `instagram.publish` skipping its own tier and limit.

   All V.
9. **Checks that read OK on absent data, plus orphan and disabled rows shown as UNKNOWN.** This erodes invariant 7 and the readiness denominators (E1: flycheap is V, the rest `[map]`; the orphan rows are V).
10. **Tier 2 guard holes and noisy proposals:**
    - the `matrix_rooms` `to` default (V);
    - `guardian.mark_untrusted` trust writes;
    - `stripe.retry_invoice` charges customers;
    - `gitlab.open_issue` re-proposes after expiry (151 proposals, V).

### 8. Owner decisions the plan conflicts with (do not resolve by guessing)

- The plan says keep `ATLAS_WATCH_ONLY=true`. The owner opened Gate 9 on 2026-09-22 (`BUILD_ORDER.md` Phase 9).
- The plan says public publishing stays approval-required. The owner made reels "fully automated" on 2026-09-27 and wrote "no need for me to meddle" on 2026-09-28 (`content/__init__.py:669`, `kernel/reels.py:283-290`).
- `stripe.retry_invoice` moves customer money in. It is Tier 2, not denied. Confirm this is intended under "no payments".
  *Decided 2026-09-28 (Miro): retry_invoice stays; it always charges, and the customer always gets a receipt; idempotent. It remains Tier 2 (one approval per retry): an irreversible Tier 1 would break invariant 8.*

**Decided 2026-09-28 14:05Z (Miro, settled after conflicting answers):** reels that pass every check post automatically (`REELS_AUTOPUBLISH=on`); a failed check is remade once, then becomes a card.
