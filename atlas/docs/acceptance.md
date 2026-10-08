# Acceptance tests for the first implementation slice

The source is "Atlas: Reliable Automation and End-to-End Verification Plan",
pages 14–17: the prioritized first slice (three workflows) and the contract,
reliability, domain and security test tables (P01–P10, R01–R12, D01–D16,
S01–S10). The IDs the first slice left open are in three later files:
`tests/test_acceptance_access.py` (P06, D07),
`tests/test_acceptance_reliability_rest.py` (R09, R10, R12) and
`tests/test_acceptance_domain_rest.py` (D04, D06, D08, D11–D16).

This is a **passing subset**, reported as one. It is evidence from automated
tests against a local, throwaway Postgres and a stub publisher. It is not
staging or production evidence, and nothing here was deployed or enabled.

## The three slices

Each slice is a durable workflow (a `workflows` row walking the state machine
in `kernel/durable.py`). Each one is a producer for the durable core
(docs/durable-core.md). Its effects leave only as `outbox` rows through
`executor.execute`. The approvals claim statement is unchanged (the restore
fence runs before it). `executor.execute` changed in one line for R09: its
pause lookup passes the request's account; `PRISTINE_EXECUTE` was
regenerated with a dated note.

| Slice | Code | Wired into | Workflow type |
|---|---|---|---|
| 1. A factual question in any Slack channel | `atlas/kernel/flows/ask.py` | `api/slack_events.events` (`go()`), `_reply`, `_request` | `slack.event` |
| 2. One synthetic operational issue | `atlas/kernel/flows/ops.py` | `kernel/fixes.sweep` | `ops.incident` |
| 3. One content job through a stub publisher | `atlas/kernel/flows/content.py` | `kernel/reels.post_or_card` | `content.reel` |
| All three: settle, mirror cards, read back recovery | `flows.tick()` | `scheduler._outbox_pass` | — |

### Flags

Both flags default to off, and both are read on every call, before any SQL.

| Flag | Values | Effect |
|---|---|---|
| `ATLAS_DURABLE_CORE` | `on` / `1` / `true` / `yes` (existing) | Turns on the durable core. Required by every slice. |
| `ATLAS_DURABLE_FLOWS` (new) | a comma list of `ask`, `ops`, `content`, or `all` | Names which slices produce. |

A slice runs only when the core is on **and** the slice is named. Rollout
step 3 of docs/durable-core.md turns the core on by itself, to watch its
leases, and that step must not also start the producers. With either flag
off, every wired call site runs the code it ran before.
`tests/test_acceptance_flags.py` checks this for each slice, without a
database.

### What each slice does

**1. ask.** One workflow per Slack `event_id`:

- Only the worker that holds the workflow's lease runs the handler. Two
  copies of an event that both get past `_seen_before` (two workers racing
  its audit lookup) are handled once.
- A Slack retry never reaches the workflow: `_seen_before` records the event
  id before intake and drops the retry. An event whose first worker died
  after that point is therefore not resumed by this slice, and R03 is not
  claimed for it.
- The reply goes through the existing reply path, in the channel and thread
  it was asked in.
- The workflow ends `SUCCEEDED` only when a channel took the reply. If
  nothing took it, it ends `FAILED`. If the reply was only queued for the
  morning brief, it ends `NEEDS_HUMAN`.
- A question's answer is bounded to 120 s, above the 40 s fast call plus
  the routed fallback `_converse` already makes. A failure, a timeout or an
  empty answer becomes "I don't know right now … and I won't guess", with
  the reason.
- If the intake cannot be recorded, the message takes the legacy path. If
  the record fails after intake, the handler still runs, unrecorded. This
  flow only replies, and `_seen_before` already fails open for the same
  reason.

**2. ops.** One workflow per (check, `fixes.fingerprint`, reminder
generation):

- A repeat detection does nothing. The RA-D4 weekly reminder is still raised.
- Each workflow opens an `incidents` row.
- Diagnostics are bounded to 600 s and 4000 characters. An investigation
  that stops short records `cause` and `impact` as unknowns.
- It prepares one step:
  - a Tier 1 fix, queued through the outbox together with the state move;
  - a Tier 2 card; or
  - one next step, owned by Miro.
- Recovery is a read of the check's own observations. The workflow ends
  `SUCCEEDED` only on an OK reading taken after the fix. Otherwise it goes
  to `NEEDS_HUMAN` once `VERIFY_WINDOW` (2 h) has passed.
- In the sweep wiring the agent itself calls catalog actions through
  `tools.invoke`, so the workflow records the posted next step. The Tier 1
  outbox path is exercised by the SLICE2 tests.

**3. content.** One workflow per render:

- The quality gate's verdict comes first. A flaw ends in `NEEDS_HUMAN` with
  the exception, never a silent pass.
- The autopost rule queues one outbox row per platform leg.
- A card waits on its proposal and mirrors `decide()`'s claim. The effect is
  never queued a second time.
- `settle()` reads every leg back through `kernel.verify`. The workflow ends
  `SUCCEEDED` only when every leg is verified. Otherwise it ends
  `NEEDS_HUMAN`, and the legs that posted stay posted.
- One receipt is sent per workflow.
- In production the publisher is `content.autopublish_reel`, one leg. The
  tests register a stub publisher with one leg per platform. It exists only
  in `tests/acceptance_support.py` and never touches a provider.

## How to run

The in-suite tests need no database:

```
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_acceptance_*.py
```

The tests marked **PG** need a throwaway Postgres. **Without
`ATLAS_TEST_PG_DSN` they are skipped, and a skip is not a pass.**

```
docker run -d --rm --name atlas-pg-<yours> -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=atlas -p <free port>:5432 pgvector/pgvector:pg16
ATLAS_TEST_PG_DSN=postgresql://postgres:test@localhost:<port>/atlas \
    .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_acceptance_*.py
docker rm -f atlas-pg-<yours>
```

The fixture runs `init-db`, then TRUNCATEs `workflows`, `outbox`, `leases`,
`proposals`, `pauses`, `incidents`, `actions_log`, `observations`, `checks`,
`properties`, `effect_claims`, `lessons` and `restores`. D13 also TRUNCATEs
`schedule_runs` and `reports` itself. Never point it at a database whose
rows matter. CI does not run the PG tests, as with
`tests/test_durable_pg.py`.

## The matrix

Status is one of:

- **pass**: a test asserts the plan's required result and passes.
- **xfail**: a strict xfail whose assertion is the required result, with the
  reason it fails today.
- **not yet**: no test in this suite.

"PG" means the test needs `ATLAS_TEST_PG_DSN`.

### Contract and policy (tests/test_acceptance_policy.py)

| ID | Required result | Test(s) | PG | Status |
|---|---|---|---|---|
| P01 | Unknown action or malformed arguments rejected before execution | `test_P01_unknown_action_or_malformed_arguments_rejected_before_execution` (4 cases) | – | pass |
| P02 | Tier 3 denied, with no alternate path | `test_P02_tier_3_is_denied_on_every_path_with_no_alternate` (executor, propose, tool call, deny-list); `test_P02_tier_3_queued_in_the_outbox_fails_without_running` | –, PG | pass |
| P03 | Tier 2 without approval waits; no side effect | `test_P03_tier_2_without_approval_is_refused_by_the_executor`; `test_P03_a_tier_2_card_waits_and_nothing_leaves` | –, PG | pass |
| P04 | Approval for an old asset or version needs a fresh review | `test_P04_an_approval_for_a_changed_payload_needs_a_fresh_review`; `test_P04_a_card_raised_before_versions_runs_as_before` | PG | pass. `propose()` records the action's catalog version (`registry.action_version`) and `registry.payload_hash(params)` with the card. `decide()` reads them in a separate SELECT beside the claim, which is unchanged, and the executor refuses a mismatch before anything runs (`dispatched=False`, class `policy`). The card becomes `superseded` (terminal), and a fresh card (dedup `superseded:<id>`) shows what would run now. The reel's workflow goes to `NEEDS_HUMAN`. Positive control: approving the fresh card runs it once. A card with nothing recorded (raised before this change) runs as before. |
| P05 | An expired or declined proposal cannot execute | `test_P05_an_expired_or_declined_proposal_cannot_execute[expired, declined]` | PG | pass |
| P06 | A lower-privilege actor or the wrong tenant is denied without leaking details | `tests/test_acceptance_access.py`: `test_P06_a_non_approvers_button_is_denied_without_the_cards_details` (approve, always, decline, detail, ack); `test_P06_a_non_approvers_slash_command_is_denied_and_decides_nothing`; `test_P06_a_non_approvers_typed_words_are_ignored_silently` (5 cases), with `test_P06_the_same_words_from_the_approver_do_decide` as the positive control; `test_P06_an_approved_guardian_card_is_refused_on_the_wrong_tenant` | – | pass. The tenant half needed a fix (see D07). Slack has one approver list and no workspace check; "tenant" here is the Guardian account. |
| P07 | A quoted command, injection, negation or approval question is conversation only | `test_P07_quoted_negated_or_questioning_text_is_conversation_only` (4 cases); `test_P07_instructions_inside_a_check_detail_are_data_not_commands` | –, PG | pass |
| P08 | Private local route unavailable: stops, never falls back to the cloud | `test_P08_private_work_stops_rather_than_leaking_to_the_cloud` | – | pass |
| P09 | A global or module pause blocks new dispatch and preserves state | `test_P09_a_module_pause_blocks_new_dispatch_and_keeps_the_state`; `test_P09_a_global_pause_is_enforced_in_the_one_path_out` | PG, – | pass |
| P10 | A model's suggestion to change its own safety code is rejected | `test_P10_atlas_cannot_act_on_its_own_safety_code` | – | pass |

### Reliability and concurrency (tests/test_acceptance_reliability.py)

| ID | Required result | Test(s) | PG | Status |
|---|---|---|---|---|
| R01 | One logical workflow, no duplicate effect | `test_R01_one_slack_event_delivered_five_times_is_one_workflow_one_reply` (the legacy `_seen_before` is neutralised, so the workflow row alone holds); `test_R01_one_content_job_delivered_five_times_is_one_effect_per_leg` | PG | pass |
| R02 | Button and typed approval together: one authorized transition | `test_R02_button_and_typed_approval_together_run_the_card_once` | PG | pass |
| R03 | A worker that crashes before dispatch is safely resumed | `test_R03_a_worker_that_dies_before_dispatch_is_resumed_once`; `test_R03_a_job_whose_first_worker_died_mid_intake_is_taken_over` | PG | pass |
| R04 | Crash after the provider accepted: reconcile, never a blind resend | `test_R04_crash_after_the_provider_accepted_is_reconciled_not_resent`; `test_R04_a_reconciled_leg_with_its_id_reads_back_and_the_job_succeeds` | PG | pass |
| R05 | Lease expires while the old worker continues: fencing stops the stale commit; the effect is reconciled | `test_R05_a_stale_worker_cannot_commit_and_its_effect_is_reconciled` | PG | pass |
| R06 | Database unavailable at intake: no false accepted claim | `test_R06_database_down_at_intake_accepts_nothing_and_posts_nothing` (content: nothing accepted, nothing posted); `test_R06_a_database_blip_after_slack_intake_still_answers` (ask: the reply-only flow falls back to the legacy path, by design) | – | pass |
| R07 | Notification fails after a successful action: retry the notification only | `test_R07_a_failed_receipt_is_retried_alone_never_the_post` | PG | pass |
| R08 | Rate limit or transient outage: bounded retry, clear pending state | `test_R08_a_transient_outage_is_retried_boundedly_from_a_clear_state`. A 429 / `Retry-After` class is not modelled; an effect is reconciled before it is retried. | PG | pass (transient outage only) |
| R09 | Auth revoked or scope missing: capability blocked, no retry storm, no alternate identity | `tests/test_acceptance_reliability_rest.py`: `test_R09_a_revoked_or_unscoped_credential_is_classed_auth` (401, 403); `test_R09_an_auth_refusal_is_never_resent_blind_or_as_someone_else`; `test_R09_a_revoked_token_blocks_the_capability_with_no_retry_storm`; controls `test_R09_after_reconnect_and_resume_the_held_rows_go_once`, `test_R09_an_ordinary_failure_still_backs_off_and_pauses_nothing`, `test_R09_an_auth_refusal_pauses_only_its_own_module` | –, PG | pass. The outbox recognises a refused credential (the action's `failure_class` `auth`, or HTTP 401/403 in its detail) and `pause.block_capability` pauses that credential with a normal `pauses` row (`atlas:auth-refused`, reason names the status code only): `module:account` for Instagram (`account`), YouTube (`channel`) and shop (`account`), the whole module otherwise, and **notify only** for modules whose several credentials no param tells apart (`pause.NOTIFY_ONLY`, e.g. content, clips). The executor's pause check asks for the account, so `instagram:clips` stops only clips and a plain `instagram` pause still stops all. Sibling rows hold, unspent; one short notice; the pause is listed in the brief and weekly; resume (`/atlas resume instagram:clips`) stays his. Per-account tests: `test_R09_a_401_on_one_account_pauses_only_that_account`, `test_R09_a_plain_module_pause_still_blocks_every_account`, `test_R09_resuming_the_scoped_pause_sends_the_held_rows`, `test_R09_a_module_with_several_unnamed_logins_is_told_not_paused`, `test_R09_which_credential_each_action_names`. Limit: outbox rows only; a card or direct run refused 401 is reported as a failure, as before. |
| R10 | Queue overload or content burst: backpressure; interactive work keeps its target | `test_R10_a_draft_burst_meets_backpressure_and_his_own_ask_still_drafts`; `test_R10_a_publishing_burst_is_dispatched_in_bounded_passes`; `test_R10_a_question_is_answered_on_time_behind_a_full_outbox` | –, PG, PG | pass. Known limits: the drafts cap is check-then-insert, so drafts started concurrently can pass it together; today nothing starts them concurrently. The interactive half holds because a Slack reply never goes through the outbox. The outbox itself claims strictly by `min(id)`, so a notice queued behind a burst waits for the whole burst. |
| R11 | Missed recurring jobs: an explicit skip, single or bounded catch-up policy | `test_R11_missed_windows_follow_one_declared_policy_each` | – | pass |
| R12 | Restoring older state with effects present: reconcile before dispatch | `test_R12_a_restored_in_flight_dispatch_is_reconciled_not_resent`; `test_R12_a_restored_claim_left_sending_is_in_doubt_not_resent`; `test_R12_a_restored_pending_intent_is_reconciled_before_dispatch`; `test_R12_a_restored_claim_is_fenced_and_nothing_is_claimed_before_it`; `test_R12_a_restored_pending_card_is_not_run_again_blindly`; controls `test_R12_after_the_fence_new_work_runs_and_a_second_fence_is_a_no_op`, `test_R12_an_unreadable_fence_dispatches_nothing`, `tests/test_approvals.py::test_an_unreadable_restore_fence_runs_nothing` | PG, – | pass. The restore fence (`kernel/restore.py`, docs/restore-drill.md "A real restore"): the real restore procedure records a `restores` row after `pg_restore`; until it is fenced the outbox claims nothing, and the fence moves restored `pending`/`claimed`/`dispatching` rows to `reconciling` and restored `pending` cards to `held` (approve refused; decline allowed and resolves it: `test_R12_a_held_card_can_be_declined_to_resolve_it`). Unreadable marker or missing `restores` table: nothing dispatched and nothing approved (`test_R12_a_missing_restores_table_sends_and_runs_nothing`). **Limit:** a restore done without the procedure's `INSERT` is not detected. |

### Domain and interaction (tests/test_acceptance_domain.py)

| ID | Required result | Test(s) | PG | Status |
|---|---|---|---|---|
| D01 | A question in each channel: same conversation, correct context | `test_D01_a_question_in_any_channel_is_answered_where_it_was_asked` (channel, thread, DM) | PG | pass |
| D02 | "Yes" with one pending versus several: exact approval versus one clarification | `test_D02_yes_under_one_card_approves_exactly_that_card`; `test_D02_yes_with_several_pending_is_one_clarification_and_no_approval` (3 cases) | – | pass |
| D03 | "Ack", "seen", "thanks" or "decline": a distinct state, no accidental execution | `test_D03_ack_seen_or_thanks_is_noted_and_never_a_decision` (4 cases); `test_D03_decline_declines_and_never_executes` | – | pass |
| D04 | An authorized cross-channel action ID: same authoritative action, no duplicate proposal | `tests/test_acceptance_domain_rest.py`: `test_D04_one_action_id_from_any_channel_is_one_action_one_proposal` (the same cause re-proposed; "approve N" typed in another channel; the card's button; the operator command) | PG | pass. `intent.queue` keys its dedup on the channel, so the same spoken request from two surfaces is two cards. That is unchanged, and this test does not claim otherwise. |
| D05 | A diagnostic hits its tool limit: checkpoint, unknowns, next step | `test_D05_a_diagnostic_that_hits_its_tool_limit_checkpoints_unknowns_and_a_next_step` | PG | pass |
| D06 | Stale telemetry or an offline personal endpoint: no fabricated health, no global-outage claim | `test_D06_a_stale_ok_is_said_as_unknown_and_its_number_is_dropped`; `test_D06_the_agents_status_tool_never_reports_a_stale_ok`; `test_D06_an_offline_mac_is_not_an_outage`; `test_D06_an_optional_personal_peer_off_the_mesh_is_not_degraded` | – | pass |
| D07 | Guardian tenant/trust boundaries: no cross-tenant access, no automatic trust-label write | `tests/test_acceptance_access.py`: `test_D07_no_guardian_write_crosses_a_tenant` (2 actions × mismatch, unreadable); `test_D07_one_tenant_writes_once_the_positive_control`; `test_D07_no_path_writes_a_device_trust_label` | – | pass, after a fix. `guardian.alert_op` and `guardian.rescan` wrote with the write token without checking that it names the read token's account. They now call `readback.same_tenant()` first and refuse without naming either account. The trust-label test is behavioural: every catalogued run plus the incident reading, with every request recorded. |
| D08 | FlyCheap configured-but-disabled source: coverage distinction kept | `test_D08_a_configured_but_disabled_source_is_a_choice_not_coverage`; `test_D08_one_fresh_source_does_not_stand_in_for_a_stale_one`; `test_D08_every_source_off_by_choice_is_neither_ok_nor_an_outage` | – | pass (on `assess_coverage`: FlyCheap's `/api/health` does not expose `enabled`) |
| D09 | Reel generation falls back to stills: a quality exception, never a silent approval | `test_D09_a_render_that_would_fall_back_to_stills_is_refused`; `test_D09_a_quality_exception_is_recorded_and_nothing_is_posted` (PG); `test_D09_a_stills_slideshow_file_fails_the_quality_gate`; `test_D09_one_still_held_to_the_end_fails_the_quality_gate`; `test_D09_moving_video_passes_the_same_gate` (the positive control); `test_D09_motion_that_cannot_be_read_is_not_a_pass`; `test_D09_freezedetect_log_is_read_including_an_open_freeze` | –, PG, – | pass. `quality.check_file` now checks motion on the file itself: ffmpeg `freezedetect` (frames resampled to 12 fps, noise -50 dB, a freeze counts from 2 s), and more than 50% of the runtime frozen is a quality exception. A freeze still open at the end of the file counts to the end. If ffmpeg cannot run, the result is "could not be checked", which fails the gate. The fixtures are real ffmpeg files made at test time (a moving testsrc clip, 3 stills x 5 s with sound, one still x 15 s); they are skipped when ffmpeg is missing. **Limit:** a Ken Burns (zoompan) slideshow is not caught, because every frame differs slightly from the last. A production-style zoompan reel measured 0 s frozen. Stills held for under 2 s each are not caught either. ATLAS's own stills path is refused at render time (`NO_SLIDES`), but a zoompan slideshow filed by `import-reel` still passes. |
| D10 | One destination succeeds and another times out: keep the success, reconcile the uncertain one | `test_D10_one_platform_posts_and_one_times_out_success_kept_uncertain_reconciled`; `test_D10_a_leg_nobody_can_confirm_goes_to_a_person_with_the_success_kept` | PG | pass |
| D11 | Calendar DST change or duplicate request: agreed timezone, no duplicate event | `test_D11_a_booking_on_a_dst_day_uses_the_calendars_agreed_zone`; `test_D11_a_time_that_does_not_exist_is_refused_not_moved`; `test_D11_the_same_request_twice_is_one_event` | – | pass (the fake Calendar API of `tests/test_calendar_zone.py`) |
| D12 | Content changed after approval returns to review | `test_D12_a_card_whose_content_changed_returns_to_review`; `test_D12_an_approved_intent_edited_after_approval_returns_to_review`; controls `test_D12_an_approved_intent_left_alone_runs_once`, `test_D12_a_row_queued_before_hashes_were_recorded_runs_as_before` | PG | pass. The card path: a caption edited after the card was raised is refused at approval (`execute.superseded`) and a fresh card asks about the new one (the P04 check). The outbox path: every enqueue records `catalog_version` and `approved_payload_hash` (S08, rehashed from what JSONB stored), so an intent edited after the yes ends `superseded` with nothing posted and a fresh card showing the edit. The old xfail expected `needs_human`, a status from before S08. NULL (legacy) rows run as before. |
| D13 | Duplicate weekly scheduler runs: one top-level report per window | `test_D13_duplicate_weekly_runs_send_one_report_per_window` (two schedulers at once, then a restart; then `weekly.send`'s own `reports` guard) | PG | pass |
| D14 | Conflicting burn-rate inputs: explicit uncertainty or a reconciled basis | `test_D14_conflicting_charges_are_reconciled_on_one_stated_basis`; `test_D14_a_missing_billed_figure_names_the_basis_it_fell_back_to`; `test_D14_no_charge_at_all_is_an_unknown_runway_not_a_number`; `test_D14_revenue_in_two_currencies_is_not_summed_into_one` | – | pass, after fixes. VPSie now names the list-price basis when the billed charge is missing, and says the runway is not known when no charge is reported (the detail text only, not the health). Stripe MRR across two currencies is UNKNOWN instead of one summed "USD" figure. One currency is labelled with its own code. |
| D15 | A question with an unsupported task: honest capability limit and smallest next step | `test_D15_an_unsupported_spoken_task_is_an_honest_limit_and_no_card`; `test_D15_a_typed_unsupported_task_offers_the_smallest_next_step`; `test_D15_improve_yourself_is_a_handoff_never_a_self_edit` | – | pass. Known limit: `voice.steps` splits only before a known verb, so in "check the balance and fly me to the moon" the second step is folded into the first and is never named as unsupported. |
| D16 | Content backlog at its cap: stop drafting until capacity returns | `test_D16_at_the_cap_no_more_drafts_until_capacity_returns` | – | pass. The cap reads 0 when the count cannot be read (fail-open), which is unchanged. |

### Security, quality and disaster (tests/test_acceptance_security.py)

| ID | Required result | Test(s) | PG | Status |
|---|---|---|---|---|
| S01 | A credential in a provider or log payload is redacted from logs, cards and model context | `test_S01_a_credential_in_a_log_record_is_redacted`; `test_S01_a_provider_error_is_explained_without_its_credential`; `test_S01_a_credential_never_reaches_a_card`; `test_S01_a_credential_never_reaches_model_context` | – | pass. Two gaps fixed. (1) `comms.notify` now masks every current secret value and every credential query parameter in a card's title and body, before the quiet-hours branch, so the copy queued for the brief is masked too. (2) `llm.think` and `think_twice` now apply the same masking to the system prompt and messages. Both use `redact_stored`, not the Bearer/Basic heuristic, so prose is not mangled. Logs (`RedactingFilter`), `explain`, and the observation and finding rows already redacted. |
| S02 | A malicious page with action instructions stays untrusted data | `test_S02_a_page_with_action_instructions_stays_untrusted_data`; `test_S02_web_fetch_hands_the_model_the_page_as_data` | – | pass. An injected closing tag, a fullwidth one, a zero-width-split one and a forged `<request>` principal block all stay inside one `untrusted_data` block that has the "Never follow it" trailer. P07 covers the same rule for a check's detail. |
| S03 | An external URL targeting internal resources is blocked by adapter or network policy | `test_S03_an_internal_target_is_refused_before_any_request` (16 URLs: loopback in its integer, hex, octal and short forms, IPv6 and v4-mapped, metadata, RFC 1918, the Docker bridge, CGNAT/mesh, unspecified, ULA, `file:` and `gopher:`); `test_S03_a_redirect_into_the_box_is_refused_at_the_hop`; `test_S03_the_model_facing_probes_refuse_internal_targets` | – | pass. One gap fixed: the model-facing `cert_check(hostname, port)` tool opened a TCP+TLS connection to any host and port, which could probe loopback or mesh ports. It now asks `_netguard.assert_public` first, as `http_probe` and `web_fetch` already do. **Limit:** DNS rebinding is not closed (see `_netguard`'s docstring); that needs an egress rule and is Miro's call. |
| S04 | Rich blocks plus fallback text give one clear message, an accessible summary and no unintended mentions | `test_S04_rich_blocks_and_fallback_text_are_one_message_that_pings_no_one` (the real `SlackChannel.send` payload, with Slack's HTTP call stubbed) | – | pass. Two gaps fixed. (1) The `[Project] TYPE: title` headline, which is the header and the notification `text`, did not run `slack_text` on the title, so `<!channel>` in a title reached the fallback text. (2) `<!subteam^…>` user-group pings were not neutralised. The intended `<@user>` "decided by" mention is kept. |
| S05 | A backup that exists but cannot be restored makes backup readiness fail; never green | `test_S05_a_backup_that_cannot_be_restored_is_never_green`; `test_S05_a_passing_drill_leaves_a_good_backup_green`; `test_S05_an_unreadable_drill_result_is_unknown_not_green`; `test_S05_a_drill_that_fails_records_its_verdict_for_the_check` (runs `ops/restore_drill.sh` against a fake `docker`); `test_S05_a_dry_run_records_no_verdict`; `test_S05_a_backup_never_proven_restorable_is_not_green`; `test_S05_a_drill_that_passed_too_long_ago_is_not_green` | – | pass. `ops/restore_drill.sh` writes `restore_drill.json` (`ok`, `dump`, `exit`) from its EXIT trap on every path that reached the drill, and nothing on a dry run, a refusal or an interrupt. The drill runs weekly (`atlas-restore-drill.timer`). `check_backup`: a failed drill is FAILING until one passes; an unreadable result is UNKNOWN; no drill on record, or a pass older than 8 days, is DEGRADED (a NORMAL finding, not paged). On prod the drill refuses while the disk has under 5 GB free, so backups read DEGRADED until space is freed. |
| S06 | An independent monitor notices a stopped worker | `test_S06_a_stopped_worker_is_noticed_by_the_independent_monitor` | – | pass (unit level; the staging exercise was not run). The API still answers `/healthz` 200 `{"ok": true}`, but the scheduler's `LAST_CYCLE_AT` is 20 minutes old. ATLAS's own `_heartbeat()` builds the body, `ops/watchdog/atlas_watchdog.py` reads it as `heartbeat FAIL`, and it posts one alert on its own webhook route, with no repeat while the worker stays stopped. |
| S07 | Emergency pause mid-batch: no new dispatch; in-flight effects tracked honestly | `test_S07_an_emergency_pause_mid_batch_stops_new_dispatch_and_tracks_in_flight` | PG | pass |
| S08 | Resume after pause or restart needs explicit enablement and keeps the authorization and version checks | `test_S08_resume_after_pause_and_restart_is_explicit_and_keeps_authorization`; `test_S08_a_row_held_across_a_deploy_that_changed_its_action_needs_a_fresh_card`; `test_S08_a_held_row_whose_params_changed_is_refused`; `test_S08_a_row_queued_before_versions_were_recorded_runs_as_before`; `test_S08_params_jsonb_rewrites_do_not_supersede_a_card_or_row` | PG | pass. A pause is a `pauses` row; a restart (pool closed, `init-db` run again) leaves it in force and nothing dispatches, and a model's "you can resume now" is not a command. After an explicit, scoped resume a held row goes through the same executor checks. Every card and queued row records `catalog_version` and `approved_payload_hash`; the executor refuses a row whose action changed across a deploy, or whose params changed, before it runs (`execute.superseded`, no attempt spent), and a fresh card asks about what would run now. Rows recorded before this change (NULL columns) run as before. |
| S09 | Actual artifact differs from approved metadata: rejected before publishing | `test_S09_a_file_that_differs_from_what_was_approved_is_rejected` | – | pass (partial): the gate reads the actual file against the fixed reel spec and the render's own record. It does not compare the file with a card's approved payload; see P04. |
| S10 | A claimed "completed" that fails read-back is a verification failure | `test_S10_a_post_the_platform_does_not_show_is_a_verification_failure`; `test_S10_an_unconfirmed_read_back_is_not_success_after_its_grace`; `test_S10_a_reply_no_channel_took_is_not_recorded_as_answered` | PG | pass |

### The slices' own paths and the flags

| Test | PG |
|---|---|
| `test_SLICE1_a_failed_lookup_is_an_honest_unknown_not_a_guess` | PG |
| `test_SLICE2_one_synthetic_issue_is_deduplicated_fixed_once_and_verified` | PG |
| `test_SLICE2_a_fix_that_does_not_clear_it_is_escalated` | PG |
| `test_SLICE2_no_action_means_one_next_step_with_an_owner` | PG |
| `test_SLICE3_the_stub_publisher_exists_only_inside_tests` | – |
| `tests/test_acceptance_flags.py` (11 tests: each slice off leaves its call site unchanged and issues no SQL) | – |

### Summary

| | IDs |
|---|---|
| pass | 48: P01–P10; R01–R12; D01–D16; S01–S10. Partial: R08 (a transient outage, not a 429 class), R09 (outbox rows only), R12 (only a restore recorded by the procedure is fenced), S06 (unit level only), S09 (the fixed spec, not a card's approved payload), D09 (a zoompan slideshow is not caught). |
| xfail | 0. The last strict sub-case xfails (D12 outbox path, R09 capability block, R12 restore fence ×2, S05 no drill on record) now pass. |
| not yet | 0 |

Of the 48 passes, P01, P06, P08, P10, R06, R11, D02, D03, D06, D07, D08,
D11, D14, D15, D16, S01–S06 and S09 run fully in-suite (D09 motion needs ffmpeg). Every other pass depends at
least in part on a PG test.

The strict xfails that a kernel change would flip: none remain. The owner
delegated the four decisions (2026-09-29, "whatever is best"), and each took
the fail-closed answer:

| Was | Decision | Where |
|---|---|---|
| D12 (outbox) | already closed by S08; the test's expected status was corrected | tests only |
| R09 (block) | pause the credential (`module:account`, or the module; notify only where it cannot be told apart), one notice | `kernel/outbox.py`, `kernel/pause.py`, the pause lookup in `executor.execute` (PRISTINE regenerated) |
| R12 (outbox) | restored rows to `reconciling` before any dispatch | `kernel/restore.py`, `outbox._claim` |
| R12 (card) | hold restored pending cards (`held`) | `kernel/restore.py`, `approvals.decide` |
| S05 | no drill, or a stale one, reads DEGRADED | `modules/backups` |

## Known limits of this slice

- **Receipts in production go through `comms.notify`**, not the outbox.
  `comms.notify` is not a catalog action, and making it one is an owner
  decision (docs/durable-core.md). R07's retry-only-the-notice behaviour is
  proven with a test-registered notice action.
- **The card path mirrors `decide()`; it does not dispatch.** The card path's
  verification reads the evidence from the approved run's `execute.finish`
  row.
- **Multi-leg `_advance`.** The outbox moves a workflow on its first leg's
  outcome, so a workflow can read `VERIFYING` while another leg is still
  pending. `settle()` concludes only once every leg has settled, so it never
  reads `SUCCEEDED` early.
- **The ops sweep's fix is invisible to the workflow.** In `fixes.sweep` the
  agent calls actions itself (`tools.invoke`). The sweep's workflow therefore
  records the next step that was posted and closes on an observed recovery.
  The Tier 1 outbox path is `ops.prepare(Fix)`.
