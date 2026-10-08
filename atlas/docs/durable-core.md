# The durable core: workflow record, outbox, leases

Reliability plan items RA-B1 (durable workflow record), RA-B3 (outbox) and
RA-B4 (leases and fencing). See FINDINGS.md §F and BUILD_ORDER.md phase 21
(21.B.1, 21.B.3). The source is "Atlas: Reliable Automation and End-to-End
Verification Plan", pages 5–7.

Everything here is **additive and off by default**. With
`ATLAS_DURABLE_CORE` unset or `off`, the scheduler loop runs exactly as it
did before, and nothing reads or writes the three new tables.

## What was built

| Piece | File | Table |
|---|---|---|
| Workflow record and state machine | `atlas/kernel/durable.py` | `workflows` |
| Outbox and dispatcher | `atlas/kernel/outbox.py` | `outbox` |
| Named leases with fencing | `atlas/kernel/leases.py` | `leases` |
| Scheduler wire-in (behind the flag) | `atlas/kernel/scheduler.py` | — |

The module is `durable.py`, not `workflow.py`. `kernel/workflows.py` already
exists and is a read-only graph renderer. The table is `workflows`, as the
task asked. BUILD_ORDER 21.B.1 calls it `workflow_runs`; the name should be
settled there.

## The kernel owner's conditions, and where each one holds

| Condition | How it holds |
|---|---|
| The atomic claim stays exactly as it is | `approvals.py` is untouched. `durable.approved()` records an approval only when, in the same statement, the `proposals` row is already `approved` or `executed`. A lapsed, declined or pending proposal leaves the workflow in `WAITING_APPROVAL`. The workflow cannot manufacture a yes. Tested against Postgres, including an expired proposal refused by `decide()`. |
| `reap_orphans` / `reap_abandoned_runs` run only from the lifespan | Neither is called or changed. The outbox needs no startup reaper: lapsed rows are swept at the start of every dispatch pass (`outbox.sweep_lapsed`). |
| The outbox never sends | `outbox.py` imports no channel or HTTP client and calls only `executor.execute`. A test parses the file and fails if it calls `notify`, `send`, `run`, `post` or `rollback`. |
| Leases and fencing fail closed | Every lease or fence read that errors answers "not held": `None` or `False`. Expiry is judged by the database clock, inside the statement that claims or commits. |
| No `SKIP LOCKED` in schema.sql | It is not used anywhere. Claims use the house pattern from `api/handoffs.py`: a conditional `UPDATE … WHERE id = (SELECT min(id) …) AND <same predicate> RETURNING`. The schema lint also misreads `FOR UPDATE SKIP LOCKED` inside Python strings: checked with `lint_sql(... FOR UPDATE SKIP LOCKED ...)`, which reports `unknown table 'skip'`. |
| `PRISTINE_EXECUTE` regenerated only on purpose | The durable core itself did not modify `executor.py`. The later `failure_class` (RA-D1) and `dispatched` (RA-B3) commits each changed `execute` and regenerated it deliberately (see `dispatched` below). |
| No new broker, framework or database | Postgres via `kernel.state`, the existing scheduler loop, `executor.execute` and the existing audit chain. |

## B1: the workflow record

One row per `(workflow_type, correlation_id)`, enforced by a `UNIQUE`
constraint. `durable.receive()` is `INSERT … ON CONFLICT DO NOTHING`, then a
read-back. A redelivered event returns the same workflow with
`created=False` (R01).

### Where the plan's fields live

| Plan field | Where |
|---|---|
| workflow_id, workflow_type, workflow_version, correlation_id | `workflows.id`, `workflow_type`, `workflow_version`, `correlation_id` |
| workspace_or_tenant_id | `tenant` (default `miro`) |
| originating_actor / channel, thread_id | `originating_actor`, `originating_channel`, `thread_id` |
| desired_outcome, data_classification, policy_version | columns of the same names |
| input_refs, input_hash | `input_refs` (JSON list of references such as `slack:event:Ev1`), `input_hash` (sha256 of canonical JSON). **The payload itself is never stored.** |
| state, current_step | `state` (see below), `current_step` |
| proposal_id, approval_expiry | `proposal_id` references `proposals.id`. Approval status lives on the proposal. `approval_expiry` is a copy for display; **enforcement stays in the claim**. |
| approved_payload_hash | `approved_payload_hash`, written only by `durable.approved()` |
| created_at, updated_at, deadline, next_attempt_at | columns of the same names |
| attempt_count, retry_budget, cancellation_state | columns of the same names |
| lease_owner, lease_expiry, fencing_token | on the row itself; the row is its own lease |
| provider_request_ids, provider_resource_ids | JSON lists, appended |
| reconciliation_state, evidence_refs, result_summary, remaining_unknowns | columns. Evidence is referenced (`outbox:<id>`, and `actions_log` rows through their subject), not copied. |
| child_action_ids | `outbox.workflow_id`: a workflow's intents are its outbox rows |
| prerequisites, cost_budget_ref | **not stored yet** (see "What remains") |

### States and transitions

`durable.TRANSITIONS` is the plan's diagram, one arrow for one:

```
RECEIVED -> VALIDATING -> READY
READY -> PREPARING -> WAITING_APPROVAL       (policy requires)
READY / PREPARING -> RUNNING                 (only when authorized)
WAITING_APPROVAL -> READY                    (only via durable.approved())
RUNNING -> VERIFYING -> SUCCEEDED
RUNNING / PREPARING -> RETRY_WAIT -> READY
RUNNING / VERIFYING / CANCEL_REQUESTED -> RECONCILING
RECONCILING -> VERIFYING (effect found) | RETRY_WAIT (effect absent)
WAITING_APPROVAL -> DECLINED | EXPIRED
any nonterminal -> BLOCKED | FAILED | NEEDS_HUMAN | CANCEL_REQUESTED
BLOCKED -> READY
CANCEL_REQUESTED -> CANCELLED | RECONCILING (too late to stop the effect)
NEEDS_HUMAN -> SUCCEEDED | FAILED | CANCELLED (a person closes it)
```

The terminal states are SUCCEEDED, FAILED, DECLINED, EXPIRED and CANCELLED.

- An illegal move raises `IllegalTransition` before any SQL runs.
- A legal move is a compare-and-set on the source state. With a fence, the
  statement also requires `fencing_token`, `lease_owner` and
  `lease_expiry > now()` to match.
- A stale worker's move matches nothing and returns `False`, and a
  `workflow.transition_refused` audit row is written.
- `WAITING_APPROVAL -> READY` through `transition()` is refused outright. It
  has to go through `approved()`.

### Workflow leases

`durable.claim(id)` and `durable.claim_next(states)` take a free or lapsed
lease on a nonterminal row, bump `fencing_token`, and return a `Fence`.
`claim_next` is how a second worker resumes what a dead one left (R03).

## B3: the outbox

`outbox.enqueue(action, params, destination=, occurrence=, version=)` records
one intent. The idempotency key is
`sha256(action, version, destination, occurrence)`:

- It never includes message text. Deduplicating on text would swallow a
  legitimate second "restart it".
- Enqueuing the same intent again returns the existing row.

`outbox.enqueue_with_transition()` moves a workflow and inserts its intent in
**one statement** (a data-modifying CTE). The move is fenced, so a stale
worker queues nothing.

### Dispatch

`dispatch_once()` works through two durable phases before the executor sees
a row:

```
pending --claim--> claimed --fence--> dispatching --executor.execute--> outcome
```

- **Crash while `claimed`**: nothing left the building. The lease lapses and
  another worker claims the row again (R03).
- **Crash while `dispatching`**: the effect is uncertain. `sweep_lapsed()`
  moves the row to `reconciling`, never back to `pending` (R04).

The outcome is chosen conservatively:

| `execute` returned | Row becomes |
|---|---|
| ok, with `evidence` | `delivered`. This is the only way to reach it. |
| ok, no evidence | `unverified`. It says it ran, and nothing proves it. |
| watch-only or a pause, refused before `execute.start` (an intentional stop) | `pending`, offered again every 15 min (`DISABLED_RECHECK`). **No attempt is spent**, so a long pause never turns queued intents into failures. Each offer writes one refusal row to the audit log. |
| a rate ceiling or an unreadable pause state, refused before `execute.start` | `pending` with exponential backoff and jitter, up to `max_attempts`, then `failed` |
| a permanent refusal: not in catalog, denied, unapproved Tier 2, bad params, dry run said no | `failed` |
| the action's catalog version or the params hash no longer matches what the row recorded (`executor.STALE`) | `superseded`: terminal, attempt refunded, never retried. A fresh card asks again (see "Version checks at dispatch"). |
| the provider refused the credential (R09): the action's `failure_class` is `auth`, or its detail carries HTTP 401/403 | the module is paused (below); a `notice` becomes `failed`, an `effect` becomes `reconciling` as in the next row |
| anything else, for `kind='effect'` (raised, timed out, ok=False from `run`) | `reconciling`. It is not resent blind. |
| anything else, for `kind='notice'` | `pending` with backoff (R07) |

**A refused login blocks that credential (R09).** Only on a result that got
past the executor's own refusals. `pause.block_capability` pauses the
credential the action used, as a normal `pauses` row
(`paused_by = atlas:auth-refused`; the reason names the status code, never
the provider's words):

- a module that posts as several accounts, each with its own token, is
  paused per account, `module:account`, keyed on the param that names it
  (`pause.CREDENTIAL_PARAM`): `instagram` by `account` (default `main`),
  `youtube` by `channel`, `shop` by `account`. A 401 on the clips token
  pauses `instagram:clips`; FlyCheap's and Guardian's accounts keep posting;
- any other module is paused whole (one credential);
- a module that holds several credentials with no param saying which one a
  call used (`pause.NOTIFY_ONLY`: content, clips, gitlab, mail, messages,
  comms, messaging, nightshift, agency, school, stripe_billing, research),
  or a `youtube` call with no channel, is **told, never paused**, at most
  once per action per 6 h. Each queued notice there fails after its own one
  try, which is bounded, not a storm.

The executor's one pause lookup asks `active_for(module, account_of(action,
params))`, so a `module:account` pause stops only that account, and a plain
module or global pause still stops everything. The executor validates
param names, not values, so an account value that is not a plain account
name (`../clips`, `clips;main`, a list, a number) is read as ANY account and
is stopped by every account-scoped pause of its module (fail closed); an
empty or absent one is the module's own default (`main`), as the module
reads it (the reel's Instagram leg asks
for `stories` the same way, `legs.gate(..., account=)`). Held rows wait
without spending attempts. Miro gets one line ("Paused instagram:clips:
instagram.publish's login was refused. Reconnect it, then `/atlas resume
instagram:clips`."), and every active pause is listed in the brief's
"Waiting on you" and the weekly's "Paused until you resume" until he lifts
it. `/atlas pause|resume module:account` works from Slack and the CLI; a
`shadow:` scope still cannot be named there. Setting a pause automatically
is the fail-closed direction; lifting it stays his. Scope: outbox rows. A
card or direct run refused 401 is reported as a failure, as before.

`execute` is cut off at 80% of the lease (`EXECUTE_BUDGET_S`), so it can never
outrun its own lease. A run that is cut off is treated as uncertain.

The commit is fenced in its own WHERE clause: token, owner,
`lease_expiry > now()`, and a status still `dispatching`.

- A worker whose lease lapsed or was taken over writes nothing to the row.
- It records what it saw as an `outbox.stale_commit` audit row, which
  reconciliation can read (R05).
- If the database is lost after the run, the row stays `dispatching`, then
  lapses to `reconciling`. It is never reported `delivered` (R06).

An `effect` row with a `workflow_id` also moves its workflow by
compare-and-set:

- delivered or unverified: `RUNNING -> VERIFYING`;
- reconciling: `RUNNING -> RECONCILING`.

A `notice` row never moves its workflow. The action and the message about
the action are separate rows, so a failed Slack receipt is retried on its
own and the action is not re-run (R07).

### Reconciliation

`reconcile_once()` claims one `reconciling` row and asks
`outbox.RECONCILERS[action]`, a **read-only** lookup:

- `present`: the row becomes `delivered`.
- `absent`: the row goes back to `pending`, capped by `max_attempts`.
- `unknown`, or no reconciler registered: the row becomes `needs_human`.

**No reconcilers are registered yet.** Every uncertain row therefore goes to
a person. Nothing is resent on a guess.

### The restore fence (R12)

A database restored from a backup is older than the world. The real restore
procedure (docs/restore-drill.md, "A real restore") inserts one `restores`
row right after `pg_restore`, with the app stopped. Until the kernel has
fenced that row, `_claim` takes nothing (a `NOT EXISTS` inside the claim
statement), and both `outbox.dispatch()` and the approve path of
`approvals.decide()` apply the fence first (`kernel/restore.py`, one SQL
statement):

- outbox rows `pending`, `claimed` or `dispatching` from before the restore
  go to `reconciling` (lease cleared, token bumped) and are read back;
- cards `pending` from before the restore become `held`: not approvable (declining
  resolves one), and a content workflow waiting on one goes to `NEEDS_HUMAN`;
- one short notice says how many are held.

An unreadable marker dispatches and decides nothing. With no reconcilers
registered (below), every fenced outbox row ends at `needs_human`. A restore
done without the procedure's `INSERT` is not detected.

## B4: leases and fencing for recurring jobs

`leases.acquire(name, ttl)` is an `INSERT … ON CONFLICT DO UPDATE … WHERE
expires_at <= now()`.

- It bumps `fencing_token` on every acquire and returns a `Fence`.
- It is refused while anyone holds the lease, including the same owner. A
  holder extends with `renew()`.
- `holds(fence)` checks token, owner and expiry together.
- `release()` stamps the expiry and never deletes, so the token only grows. A
  test fails if `DELETE` appears in any of the three modules.
- `OWNER` is `host:pid:nonce`, unique per process.

## The flag

`ATLAS_DURABLE_CORE` (`on`, `1`, `true` or `yes` to enable; default off) is
read on every scheduler cycle, before any SQL.

**Off**: `scheduler.loop` iterates the same steps in the same order, with the
same detach and inline split. No lease is read and no outbox step exists. A
test drives the loop with the flag off and fails if a lease is taken or the
outbox step runs.

**On**:

- Every step except `checks` and `approvals` runs under the lease
  `scheduler:<step>`. The TTL is the step's deadline plus 60 s, and the lease
  is released when the step ends.
- A lease that is held elsewhere or unreadable skips that step for this cycle
  (fail closed).
- `checks` (observe and page) and `approvals` (expire stale) are deliberately
  unleased. Running them twice is harmless. A fail-closed lease on paging
  would turn a lease-table blip into silence.
- A new detached step, `outbox`, runs `outbox.dispatch(limit=10)` and then
  `outbox.reconcile_once()`, with a deadline of 1500 s. The pass starts a
  row only while a whole `EXECUTE_BUDGET_S` (960 s) still fits inside that
  deadline, less 120 s kept for reconciliation. The step deadline therefore
  never cuts a dispatch off mid-run, which would make a finished effect look
  uncertain. `outbox` is not added to `_DETACHED`; it lives in
  `_DURABLE_DETACHED`, so the flag-off step set is unchanged.

With only this flag on, nothing enqueues to the outbox or creates workflows,
so the outbox step finds nothing to do. The first producers (the plan's
three first-slice workflows) are wired behind a second flag,
`ATLAS_DURABLE_FLOWS`; see docs/acceptance.md.

## Rollout

1. **Deploy with the flag off.** `init-db` creates `workflows`, `outbox` and
   `leases`, empty. Check that `\dt` lists them, that `SELECT count(*) FROM
   leases` stays 0, and that the logs show the same step cadence as before.
2. **Prove the SQL on a throwaway database**, not prod:
   ```
   docker run -d --rm --name atlas-pg -e POSTGRES_PASSWORD=test \
       -e POSTGRES_DB=atlas -p 55432:5432 pgvector/pgvector:pg16
   ATLAS_TEST_PG_DSN=postgresql://postgres:test@localhost:55432/atlas \
       .venv/bin/python -m pytest tests/test_durable_pg.py
   ```
   This truncates `workflows`, `outbox`, `leases` and `proposals`.
3. **Turn the flag on** (`ATLAS_DURABLE_CORE=on`, then a restart). Watch
   three things:
   - `leases` rows appear for each step, and their tokens climb once per
     cycle;
   - no step logs "lease not held" while only one api container runs;
   - review and brief still fire once per window. They keep their
     `schedule_runs` claim; the lease is in addition to it.

   To roll back, set the flag off. Nothing else needs undoing.
4. **Wire the first producer**, with the lowest-risk intent first. The
   candidate is receipts, but that is blocked on an owner decision (below).
   Then an idempotent Tier 1 effect, and only then a Tier 2 approved effect
   (`approved_by` carried on the row).
5. **Add reconcilers** per action as RA-D1 verifiers land, starting with
   `linkedin.publish_post`: its crash window is FINDINGS RA-B7.

Turning the flag off with rows in flight is safe:

- `pending` rows wait;
- `dispatching` rows lapse to `reconciling` on the next pass after the flag
  is turned back on;
- nothing is lost or resent.

## `dispatched`: the executor says whether it ran

`ActionResult.dispatched` (`bool | None`, default None) is set by
`executor.execute` alone:

- `False` on every refusal it makes before `run()` is invoked: not in the
  catalog, no decision, denied, principal only, unapproved Tier 2,
  watch-only, bad params, pause unreadable, paused, an hourly ceiling, a dry
  run that said no. Nothing reached a provider.
- `True` once `run()` was called, whatever it returned or raised. A result
  `run()` returned is stamped with `dataclasses.replace`, so `execute`
  returns an equal copy of it, not the same object.
- `None` for any result that did not come through `execute`.

The outbox reads the flag (`outbox._pre_dispatch_of`):

| `dispatched` | Treated as |
|---|---|
| `True` | possibly dispatched, whatever the detail says. An effect is reconciled, never resent. |
| `False` | refused before dispatch. Waits without spending an attempt ONLY for the executor's own watch-only or pause refusal: class `disabled` AND `evidence[executor.HOLD]` of `watch_only` or `paused`. `transient` and `budget` (the unreadable pause state, the ceilings) back off. Anything else fails, including a bare `disabled` class. A refused dry run is always `validation`, so neither a dry run nor a module can make a row wait forever or retry. |
| `None` | the old wording match, `outbox.PRE_DISPATCH_*`, as a fallback only. |

This closes the residual risk the wording match carried: a `run()` that acts
and then returns a detail starting `not run: ` or `ATLAS is in watch-only
mode` is now reconciled, not offered to the executor again. A test runs
every real refusal through `_outcome` twice, once by flag and once by
wording, and requires the same outcome.

A dry run that raises still propagates out of `execute` unchanged; the
outbox treats that as uncertain and reconciles, as before.

## Version checks at dispatch (S08, P04, RA-C4)

**What is recorded**

- `registry.action_version(name)` is `"av1:" + sha256[:32]` of the canonical
  JSON of what defines the action's behaviour:
  - the name and the tier written in code;
  - `irreversible`, and whether it has a rollback and a dry run;
  - `principal_only` and `card_only`;
  - the declared param names, and the params `run()` requires;
  - the declaring module's `ACTION_VERSION`. This is a string, or a dict
    naming single actions. A module author bumps it for a semantic change
    the schema cannot show.
- The summary, the param descriptions and the source file are left out, so
  a wording edit or an unrelated deploy refuses nothing.
- The version is the same in every process. `av1` names the derivation, so
  changing it is deliberate: every held row and card is refused once.
- `registry.payload_hash(params)` is the sha256 of the canonical JSON of the
  params as JSON gives them back.
- `propose()` stores both on the card, and `outbox.enqueue` and
  `enqueue_with_transition` store them on the row. The columns are
  `catalog_version` and `approved_payload_hash`, added with
  `ALTER ... ADD COLUMN IF NOT EXISTS`.
- If JSONB rewrote the params on the way in, the hash is refreshed from
  what the database returned. A stale hash can only refuse.
- `outbox.action_version` is untouched. It stays the caller's label inside
  the idempotency key, so a version bump never mints a second key, and a
  second send, for the same occurrence.

**What is compared**

- `ExecutionRequest.approved_version` and `approved_payload_hash` default to
  None. None means no check.
- `execute` compares both after the tier checks and before watch-only. The
  version is checked first, then the params.
- A mismatch is refused before anything runs:
  - `dispatched=False` and `FailureClass.POLICY`;
  - `evidence={executor.STALE: "version" | "params"}`;
  - an `execute.superseded` audit row, with no dry run and no
    `execute.start`;
  - a detail that says what changed and that it "needs a fresh approval".
- A version or hash that cannot be computed counts as a mismatch.
- Callers read the result with `executor.stale_of`, never the wording.

**Who passes what, and what a refusal does**

- **Approved cards.** `decide()` and the background `_run_claimed` read the
  record in a separate SELECT after the claim. The claim statement is
  unchanged.
  - If the record cannot be read, nothing runs and the card is `failed`.
  - On a stale refusal, the card becomes `superseded`. That status is
    terminal and never approvable. It is set only from `approved`.
  - `approvals.fresh_card` then raises a new card, with dedup
    `superseded:<id>`, showing what would run now.
  - `sync_cards` moves a reel waiting on that card to `NEEDS_HUMAN`.
- **Queued rows.** The outbox claims the two columns with the row and passes
  them on.
  - A stale refusal ends the row `superseded` and raises a card with dedup
    `outbox-superseded:<id>`.
  - Content `settle` and ops read a superseded row as "did not run".
- Nothing is ever re-approved silently. If `propose()` refuses the fresh
  card (the action left the catalog, or it now needs a param the row lacks),
  that is audited as `proposal.fresh_card_refused`.
- NULL columns mean the card was raised or the row queued before this
  change. They are not checked and behave exactly as before.

## What remains

- **Receipts and notifications through the outbox.** `comms.notify` is not
  a catalog action, and the outbox dispatches only through
  `executor.execute`, so `reap_abandoned_runs`' message, approval receipts
  and `comms.undelivered` retries cannot be outbox rows yet. Doing it needs
  a Miro-bound notice action in the catalog, with a tier decided in code.
  **That is an owner decision.** R07 is proven with test-registered actions.
- **Producers are wired only behind `ATLAS_DURABLE_FLOWS`** (docs/acceptance.md:
  Slack intake, the fixes sweep, the reel post). With it off, Slack intake
  still dedups through its audit row alone (RA-B5). No approval creates a
  workflow. The proposal claim in `decide()` is deliberately untouched (the
  version check reads beside it, and the restore fence runs before it), so
  linking a proposal to a workflow is for the caller that creates both.
- **No reconcilers** (they depend on RA-D1). Until each action has a
  read-back, every uncertain row ends at `needs_human`.
- **`reap_abandoned_runs` still marks `execute.abandoned`.** It does not
  create `RECONCILING` workflows (21.B.4). Feeding it in means a change to
  the lifespan reaper, which is out of scope here.
- **Approval payload hash (RA-C4).** Checked at dispatch (see above). Not
  covered:
  - An intent approved without a card and then queued records both, like
    every outbox row (D12). `intent.queue` and the spoken read-back raise a
    card through `propose()`, which records them. What is not compared is a
    live request that runs at once (`intent.act`, Tier 1 or a principal's
    direct ask): nothing is stored between the yes and the run, so there is
    nothing to edit.
  - The rendered file behind a reel card is not hashed. Only its params are
    (S09).
- **Outbox params are stored as given**, like `proposals.params` and the
  `execute.start` audit row. The workflow record holds only hashes and
  references. If params ever carry sensitive text, they need a reference
  instead.
- **Not modelled:** `prerequisites` and `cost_budget_ref`, workflow depth,
  and parent/child budgets (the plan's recursion guard).
- **21.B.2** (unique partial index on `proposals.dedup_key`) is not part of
  this change.
- **Retention.** Nothing purges old `workflows` or `outbox` rows yet.
- **R02** (button and text approval together) is still carried by the
  existing atomic claim, not by this code.
- **Postgres-level tests** are opt-in (`ATLAS_TEST_PG_DSN`). The suite had no
  pattern for them, and CI does not run them.
