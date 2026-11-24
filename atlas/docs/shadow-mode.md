# Shadow mode, then canaries

Reliability plan item RA-G1 (FINDINGS.md §F, BUILD_ORDER.md 21.G.1). The
source is "Atlas: Reliable Automation and End-to-End Verification Plan",
page 16, "Shadow, canary and soak":

> Run an initial 48-hour shadow period with external writes disabled,
> comparing proposed actions to observed events. Then run separately
> approved low-risk canaries, followed by an initial seven-day scoped
> observation period…

**Nothing is shadowed by default, and this change enables nothing.** Every
step below needs the owner's approval, given separately for that step.

## What shadow mode does

A shadowed module keeps doing everything except acting:

- its checks still observe;
- its proposals are still made and still reach the owner;
- Tier 1 fixes are still chosen.

When one of those would run, it is refused before `execute.start`. Nothing
reaches a provider, and a "would have run" record goes into the audit chain.
`shadow-report` then lists those records against what actually ran.

| Piece | Where |
|---|---|
| Set, lift, report | `atlas/kernel/shadow.py` |
| Enforcement, every caller | `pause.active_for` → `executor.execute` (unchanged) |
| Enforcement, outbox | `outbox.dispatch_once`, before the fence |
| Storage | `pauses` rows with scope `shadow:<module>` or `shadow:all` |
| CLI | `python -m atlas.cli shadow / unshadow / shadow-report` |

### Why it is built on the kill switch

A shadow is a `pauses` row, so it inherits every property the kill switch
already has:

- it survives a restart;
- it fails closed: if the table cannot be read, nothing dispatches;
- it is never deleted: lifting it stamps `resumed_at`, and that history is
  what the report reads;
- it is enforced in `executor.execute`, the one path out, so no caller can
  route around it.

A real pause outranks a shadow row. A pause stops the action and records
nothing.

### The two records

| Path | Record | Params hash |
|---|---|---|
| Any caller → `executor.execute` | `execute.paused`, subject = action, `scope: shadow:<module>` | **No.** `pause.active_for` sees only the module, and `executor.py` is not changed here |
| Outbox → dispatcher | `shadow.would_execute`, subject = action, with `params_hash`, destination, occurrence, `outbox` id. The row becomes `shadowed` | Yes, sha256 of canonical params (`durable.digest`) |
| Watch-only (a global shadow) | `execute.watch_only` (existing) | Yes. The report hashes the recorded params. They were redacted before storage, so the hash may differ from an outbox hash of the same intent |

No producer enqueues to the outbox yet (docs/durable-core.md), so today
**every real would-have-run record comes from the executor path and has no
params hash**. The action, time, reason and scope are recorded, and
comparing those with observed events is what the 48 hours are for.

Adding the hash on the executor path is a one-line change to `executor.py`.
It is proposed here, not applied: `PRISTINE_EXECUTE` would be regenerated
on purpose, and that is the kernel owner's call.

```diff
--- a/atlas/kernel/executor.py
+++ b/atlas/kernel/executor.py
@@ async def execute(req: ExecutionRequest) -> ActionResult:
     if stopped is not None:
         await audit("execute.paused", req.action,
-                    {"scope": stopped.scope, "reason": req.reason})
+                    {"scope": stopped.scope, "reason": req.reason,
+                     "params_hash": digest(req.params)})
```

`shadow.build` already reads a `params_hash` from an `execute.paused` row
when there is one, so the report needs no change.

### Deliberate limits

- **The unit is a module, or everything.** Per-action shadowing is not
  offered. `pause.active_for` is called with the module only, so an
  action-level entry would stop the outbox but not a direct executor call.
  That is a false sense of safety. `shadow reels.post` is refused.
- **A shadowed outbox row is terminal (`shadowed`).** Lifting the shadow
  never replays intents queued during it: the page-13 replay concern.
  Enqueuing the same idempotency key again returns the shadowed row. A
  canary is a new occurrence.
- **A shadowed workflow moves `RUNNING → BLOCKED`**, and is not left
  running.
- **A rollback is not blocked**, the same as with a pause.
- **Only the server CLI sets or lifts a shadow.** The Slack `/atlas pause`
  pattern cannot name a `shadow:` scope. Nothing in the catalog can touch
  it, and no model can.
- **What the dry run would have said is not recorded.** The shadow stops
  the action before the dry run. A dry run can call a provider read path,
  and the point of the window is that nothing leaves.
- **Host jobs outside ATLAS are not covered.** These are the reel timers
  and Veo calls (RA-C3 and RA-F5). A shadow cannot see them. Stop them
  separately, or list them as excluded.

## The 48-hour shadow procedure

Each numbered step needs the owner's approval for that step.

1. **Choose the scope.** Watch-only is **off** in prod (Gate 9, opened
   2026-09-22), so a shadow is the per-module way to "disable external
   writes". Take the modules whose actions run unattended today (Tier 1
   and scheduled). List every module that stays live, and why, before
   starting.
2. **Record the baseline.** Save the output of:
   ```
   python -m atlas.cli pauses
   python -m atlas.cli shadow-report --since 48h
   ```
   The second one shows the last 48 h of real runs, which is the
   comparison baseline.
3. **Shadow.** On the host, for each module in the scope:
   ```
   docker compose exec api python -m atlas.cli shadow <module> 48h G1 shadow
   ```
   Or `shadow all` for everything. Confirm with `python -m atlas.cli
   pauses`: each shadowed module is listed as "in shadow mode". (On the
   host every CLI command runs as `docker compose exec api python -m
   atlas.cli …`.)
4. **Run 48 hours.** Change nothing on the tested path. The window must
   cover every enabled schedule at least once: the daily brief, review,
   reels and the weekly job if it falls inside. **Extend the window** if a
   required schedule did not run, or if a critical fix changed the tested
   path (plan, page 16).

   **Approving a card during the window uses it up.** A Tier 2 card for a
   shadowed module still reaches the owner. Approving it claims the proposal,
   and `execute` then refuses with "not run: … shadow mode". The proposal
   ends `failed`, and the receipt says "Failed: not run: … shadow mode".
   This is how a pause already behaves. Watch-only is different: it leaves
   the card pending. The approval itself is the "would have run" record
   (`execute.paused`). To act on that card for real later, the cause has
   to propose again after the shadow is lifted. Decline or ignore cards
   for shadowed modules unless the approval itself is what is being
   tested.
5. **Read the report:**
   ```
   python -m atlas.cli shadow-report --since <the shadow start, ISO>
   ```
   The report exits 1 if any run started inside a shadow window.
   - **Would run.** Each action and each count should be explainable by an
     observed event: a finding, a proposal, a schedule. Anything
     unexplained is a finding.
   - **Actually ran.** For a shadowed module this must be zero. A non-zero
     value prints as `UNAUTHORIZED EFFECTS` and **blocks the rollout**.
   - **Duplicates.** The same action and reason, repeated for one cause, is
     noise to fix (RA-B6, RA-F1) before a canary.
6. **Decide**, and it is the owner's decision: go to canaries, extend, or stop.
   Record the decision and the report next to FINDINGS RA-G1.

Rollout is **blocked** if any of the following remains: an unauthorized
effect, an unreconciled duplicate, a false completion, a tenant or privacy
leak, or a failed restore check (plan, page 16). A passing subset is
reported as a passing subset.

## Canary steps, after the shadow

Each canary needs the owner's separate approval for the **exact action,
destination and payload**. Testing is not an exemption from confirmation
(plan, page 16). One module at a time, lowest risk first:

1. **Name the canary** in writing: the module, the one action, the
   destination, what a correct result looks like, and the verifier that
   will read it back (RA-D1). An action with no verifier is not a canary
   candidate.
2. **Lift the shadow for that module only:**
   `python -m atlas.cli unshadow <module>`. Every other module stays
   shadowed.
3. **Watch the first run.** Check the `execute.start` / `execute.finish`
   pair, the verifier's verdict, and the provider's own record (read-only).
   A contradicted or unverified result puts the module back in shadow
   (`shadow <module>`) and needs a fix before a retry.
4. **Rollback and pause exercise** (gate 21.G: S07, S08). Run the action's
   rollback once on the canary's own effect, if it has one. Pause the
   module mid-batch and resume it. Confirm that nothing new dispatched
   while paused, that the in-flight run was tracked honestly, and that
   resume needed an explicit command.
5. **Seven-day scoped observation.** Only the canaried modules act. The
   window covers every enabled schedule. `shadow-report --since 7d` is
   read daily, and the window is extended on the same conditions as step 4
   of the shadow procedure.
6. **Next module**, with a new approval. Never two at once.

## Lifting everything

`python -m atlas.cli unshadow all` lifts only the `shadow:all` row. Each
per-module row is lifted by name. Anything paused, or watch-only, stays as
it is. Nothing queued during the shadow is replayed.
