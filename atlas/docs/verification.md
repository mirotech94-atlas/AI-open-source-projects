# Read-back verification (RA-D1, RA-D2)

`executor.execute` returning ok means an operation was attempted. It does
not prove the outcome exists. A verifier reads the authoritative record back
from the provider, compares it with what was approved, and reports what it
saw. Code: `atlas/kernel/verify.py`. Tests: `tests/test_verify.py`,
`tests/test_verify_writes.py`, `tests/test_verify_readbacks_d3.py` and
`tests/test_verify_rad1_*.py`.

## The contract

```python
async def verify(action, params, result) -> Verification
async def reconcile_unknown(action, params, started_audit_row) -> Verification
def has_verifier(action) -> bool
```

A `Verification` has these fields:

| Field | Meaning |
|---|---|
| `state` | `verified`: the read-back shows the approved effect. `contradicted`: it is missing or wrong, and `reason` names what. `unknown`: it could not be read back. |
| `evidence` | A short dict of ids, permalinks and read-back fields. It never holds a secret. |
| `checked_at` | When the read-back happened, in UTC. |
| `failure_class` | One of the plan's classes, set when the state is not verified and the cause is known: `validation`, `auth`, `disabled`, `transient`, `timeout_after_dispatch`, `budget`, `stale`, `quality`, `policy`, `security`. |
| `reason` | One line, redacted. |

`FailureClass` is defined in `contracts.py` and re-exported here.
`ActionResult` carries it too, as `failure_class` (default None, meaning
unclassified). `executor.execute` sets it on each refusal it makes itself:

| Refusal | Class |
|---|---|
| not in the catalog, bad parameters | `validation` |
| no decision, denied, principal only, unapproved Tier 2 | `policy` |
| watch-only, paused | `disabled` |
| pause state unreadable | `transient` |
| an hourly ceiling | `budget` |
| dry run said no | `validation`, always; never the dry run's own class |

A module's `run()` result is passed through as it came. Modules can use
`contracts.classify_failure(cause, after_dispatch=...)` to name the class of
an exception, an HTTP status or a one-line detail (401/403 auth; 429/502/503
transient; 408/504 and timeouts transient before dispatch,
`timeout_after_dispatch` after it; 400/422 validation). It returns None for
anything else. It never guesses `transient`.

The kernel enforces these rules itself, so they do not depend on each
verifier getting them right:

- **Verifiers only read.** They never post, send, retry or change anything.
  A verifier that cannot read back returns `unknown`, never `verified`. The
  tests fake every provider at `httpx.AsyncClient.send`, and the fake refuses
  any method but GET.
- **No verifier gives `unknown` with the reason "no verifier".** An action
  is never verified by default.
- **A verifier that raises gives `unknown`.** The error is redacted through
  `_http.explain`. A `CancelledError` still propagates.
- **A `verified` with empty evidence is downgraded to `unknown`.**
- **A read-back is bounded** by `ATLAS_VERIFY_TIMEOUT_S` (default 90 s,
  because LinkedIn drives a browser). A timeout gives `unknown`, class
  `transient`.
- **Evidence is cleaned.** Keys that look like credentials (`token`,
  `secret`, `api_key` and so on) are dropped. String values are redacted and
  cut to 300 characters, and lists are capped at 20 items.
- **Several independent read-backs combine** (`combine`): any contradicted
  leg makes the whole contradicted and names that leg. Otherwise any unknown
  leg makes the whole unknown. Only when every leg is verified is the whole
  verified.

### Reconciling a run that never finished

`reconcile_unknown` handles a run with `execute.start` and no finish, such as
a process killed mid-run (see `approvals.reap_abandoned_runs`). It takes the
start row as an `AuditRow` or as a dict row from `fetch_all`. It reads the
params and start time off that row, then looks the effect up by what was
persisted: a title, caption or stamp, anchored on the start time. It never
re-sends and never touches the action's `run`, `dry_run` or `rollback`.

Here the states mean:

- `verified`: the effect is there.
- `contradicted`: the effect is not there.
- `unknown`: nobody can tell, so a person checks.

An action with no reconciler gives `unknown` with the reason "no reconciler".

### Reconciling at API start (`ATLAS_RECONCILE`)

`approvals.reap_abandoned_runs` runs only from the API lifespan. It first
writes `execute.abandoned` for each run, which releases the deploy guard,
and sends its one message. Then, only when `ATLAS_RECONCILE` is `on`
(`1`, `true` and `yes` also count), it starts
`approvals.reconcile_abandoned` as a tracked background task. **The flag
defaults to off** and is read on every call.

For each run, `reconcile_abandoned` calls `reconcile_unknown` with the start
row, including its payload, so the params come from what was recorded. It
appends one audit event:

| State | Event | Meaning |
|---|---|---|
| verified | `reconcile.done` | the effect is there |
| contradicted | `reconcile.failed` | it is not; the evidence says what was seen |
| unknown | `reconcile.needs_human` | nobody can tell; a person checks |

Each event carries `start_id`, `started` and the `as_audit()` fields. When
anything was looked up, a second message lists the outcomes. Nothing is
re-run, retried or rolled back, whatever the answer. An error in one run's
look-up is logged, and that run stays abandoned and unreconciled.

The pass is detached because a reconcile may drive a browser. With the flag
on, an abandoned `linkedin.publish_post` starts a LinkedIn browser read,
under its lock, at API start.

## Adding a verifier

1. Create `atlas/modules/<module>/readback.py`. The kernel imports it lazily,
   the first time `verify` or `has_verifier` is asked about an action in that
   namespace. The kernel never names a module.
2. Register the read-back:

   ```python
   from ...kernel.verify import verifier, verified, contradicted, unknown

   @verifier("gitlab.open_issue")
   async def verify_open_issue(params, result):
       ...   # GET the thing by the id in result.rollback_token / result.evidence
       return verified(iid=7, web_url="...")
   ```

   Use the module's existing read client and credentials. Take the ids from
   what the run recorded: `rollback_token`, or `ActionResult.evidence`. If an
   action returns no id, add one to its `evidence`. Do not change its `ok`,
   `detail` or `rollback_token`.
3. Optionally, register a reconciler with `@reconciler("module.action")`. It
   has the signature `(params, started_at) -> Verification`.
4. Add cases to `tests/test_verify.py` for verified, contradicted, unknown and
   raises, plus a check that no secret reaches the evidence.
5. Set `verification_handler` for the action in `docs/capabilities.json`.

## The executor hook and its flag

`executor.execute` starts `_verify_after` after the `execute.finish` audit.
It does this only when the action returned ok, and only when
`ATLAS_VERIFY_AFTER` is `on` (`1`, `true` and `yes` also count). **The flag
defaults to off.** It is read on every call, like `ATLAS_WATCH_ONLY`, so
changing it takes a restart, not a redeploy.

When the flag is on, the hook does this:

- It calls `verify()` and appends `verify.verified`, `verify.contradicted`
  or `verify.unknown` to the audit chain. The event carries the state, the
  failure class, the reason, `checked_at` and the evidence. The evidence is
  capped like `execute.finish` evidence.
- It returns the action's own `ActionResult` unchanged, apart from the
  executor's `dispatched=True` stamp (RA-B3; an equal copy made with
  `dataclasses.replace`). It never retries, never rolls back, and never raises into the executor: any error
  in the hook is logged and swallowed.
- It is detached. `execute` starts the read-back as a tracked task
  (`kernel.background.spawn`) and returns at once. A slow read-back never
  delays the caller. A cancelled one, for example at a shutdown or at the
  background-approval deadline, can never turn a finished run into a failed
  card. `background.cancel_all` cuts a read-back short at shutdown, which
  leaves no `verify.*` row; that reads as unknown.

`tests/test_lessons.py::PRISTINE_EXECUTE` pins the executor's body. It was
regenerated for these two lines on purpose.

## Covered actions

| Action | What is read back |
|---|---|
| `content.publish_reel`, `content.autopublish_reel` | Each leg is read by the id the run recorded. Instagram: the media, which must be a reel with the approved caption. Facebook: the Page video, whose `video_status` must be ready. TikTok: the Postiz post's state. Postiz is read only; `post_tiktok` and `_release` write, so they are never called. A leg that was refused, or that has no id, is contradicted and named; media 10, whose facebook leg is missing, is this case. A leg that was not configured is skipped as intended, not treated as an outage. A queued or id-pending leg is unknown. |
| `instagram.publish` | The media by id: its account, its kind, the first 40 characters of its caption, and its permalink. |
| `linkedin.publish_post` | The page's published list, read in a browser under the module's `lock()`. The session is never saved back. The post-bank entry supplies the entry id, status and activity URL. |
| `gitlab.open_issue` | The issue by its iid, in the approved project, with the approved title. |
| `calendar.create_block`, `calendar.book_with` | The event by id on the primary calendar. Checks: not cancelled; start and end; title; attendee scope (none for a block, exactly the approved attendee for a booking). A naive `start` is resolved in the calendar's own timezone, and the evidence records that (RA-E2). |
| `calendar.decline_invite` | Miro's own attendee row, which must read `declined`. |
| `calendar.offer_slots` | The sent Gmail message, by id: metadata only, labelled SENT, to the approved recipient. |
| `cloudhost.snapshot_all` | `/snapshot`, checked for a snapshot named `atlas-<stamp>` for each targeted instance. The run's evidence now lists each instance: queued, or failed at dispatch. A dispatch failure, a failed status, or no listing within 10 minutes means contradicted, and each failure is listed (RA-D2). Not listed yet, within that window, means unknown. |
| `nightshift.merge` | The branch's merge requests, by GET, against the commit pinned on the job (`nightshift_jobs.head_sha`, read from GitLab when `check_jobs` marks the job done). Merged at the pin: verified. Merged at any other commit: contradicted, class `security`. Not merged: contradicted. The reconciler makes the same check. |
| `linkedin.reshare_post` | The post bank must record `reshared` as `True`, and the entry's first 40 characters must be on Miro's recent activity. One read in a browser under the module's `lock()`; the session is never saved back. |
| `linkedin.update_page_identity` | The page's public name, read the same way. A logo change cannot be compared, because LinkedIn re-encodes the image, so its leg is always unknown and a change that includes a logo never reads verified. |
| `agency.deploy_site` | One GET of `https://<agency_sites.domain>/`, compared with the `<title>` of the release the deploy recorded. 200 with that title: verified. Another title, or a 4xx: contradicted. A 5xx, no answer, no domain, or no title to expect: unknown. |
| `shop.create_payment_link` | The payment link and its line items, read from Stripe by GET with the same shop key: active, tagged with the approved product, one item at the approved price in USD, quantity 1, and a redirect to `/deliver/`. A 404 is contradicted. |
| `browser.submit_form` | Nothing is read over the network, and it is never verified. A form's effect lives on the recipient's side, and requesting its target again could itself act. The run's own record is checked: a form filled on another host than approved, or a selector that was not found, is contradicted. Everything else is unknown, and the reason says so. |

| `stripe.retry_invoice` | The invoice by id, which must read `paid`, and its latest payment attempt, from whichever field the account's API version carries (`payment_intent`, `charge` or `payments`). An open invoice is contradicted and names the decline. A `processing` payment is unknown, class `stale`. Whether a receipt was sent (the charge's `receipt_number` or `receipt_email`) is reported as evidence and never decides the state. The customer's email never goes into evidence, only whether one was set. The loader finds `stripe_billing/readback.py` through the registry, because the action's namespace is not its package's name. |
| `messages.reply` | The thread's message list, read through the Page the way `instagram_dm.recent` reads it. The run now records the Graph `message_id`. The message must be there, sent by the account, with the approved text. |
| `matrix_rooms.send_reply` | The event by id in the room: the sender must be this account (`account/whoami`), the type `m.room.message`, the approved body, and not redacted. |
| `agency.send_outreach` | The run now stamps its own `Message-ID` and records it. When `AGENCY_IMAP_HOST` is set, the sending mailbox's Sent folder is searched read-only for it (`EXAMINE`, `SEARCH`, `BODY.PEEK`). Found gives verified. Not found gives unknown, because a relay need not keep a copy. With no IMAP host it gives unknown, class `disabled`. A row that reads `bounced` gives contradicted. |
| `instagram.hide_comment` | The comment by id, whose `hidden` must be true. |
| `instagram.reply_comment` | The comment's replies, which must include the reply id the run recorded, with the approved text. |
| `nightshift.merge` | The merge request by the iid the run records. It must be merged, from the job's branch, and at the `sha` the run records. With no recorded sha, the result is unknown. The action itself still fails, because there is no `gitlab.merge_branch`. |
| `delegate.claude_code` | The `handoffs` row, read with a SELECT. `queued`, `claimed`, `done`, `needs_miro` or `failed` means the job was handed over. `expired`, `draft` or `cancelled` is contradicted. |
| `agency.prospect` | The `agency_leads` rows by the `lead_ids` the run recorded: all present, none `dropped`. A run that added no lead is unknown. |
| `agency.audit` | The `agency_audits` row by the recorded `audit_id`: the approved lead, not withdrawn (severity -1), and the severity the run reported. A cached audit says so in evidence. |
| `agency.rank` | The ranked leads' scores, compared with the recorded `scores_sha` (rounded through float4, the column's type). A missing row or null score is contradicted. |
| `agency.mockup` | The HTML file (exists, not empty) and the `agency_mockups` row by id, with the same lead and path. |
| `agency.build_site` | The `agency_sites` row by slug (lead, path) and `index.html` on disk, whose hash must equal the recorded `index_sha`. A changed page is contradicted, class `stale`. |
| `agency.draft_outreach` | The `agency_outreach` row by id: the lead, the kind, still `draft`, and the body hash. Only the hash goes into evidence. |
| `agency.suppress` | The `agency_suppression` row by the lower-cased address. The address is a stranger's, so evidence holds its hash only. |
| `content.draft`, `content.draft_reel` | The `content_drafts` row by id: kind, source (`reel_format:<id>` for a reel), not `dropped`, and the recorded `body_sha256`. |
| `content.draft_series_part` | The draft row as above, then the `reel_series_parts` row, which must point at that draft. |
| `content.plan_series` | The `reel_series` row by id: format, title, not dropped, and an outline as long as `total_parts`. |
| `content.reel_research` | The `reel_formats` rows the run filed: present and not retired. Zero filed is unknown. |
| `content.render_video` | The `content_media` row by `media_id` (`done`, same draft; `media_token` is never selected), the file (exists, size above zero) and an ffprobe of it (a video stream with a duration). A bad probe is contradicted, class `quality`. No ffprobe, or an OpenArt render with no file, is unknown. |
| `content.render_calendar_job` | The `reel_calendar` row by `job_id`: rendered, carded, published or declined with the same `media_id`, then the media read above. An unfinished tick must be `rendering` or `blocked`, and no more clips may be missing on disk than the run reported. |
| `purchasing.prepare_cart`, `purchasing.prepare_flight` | The `purchasing_prepared` row by id: kind, request, not withdrawn, the options' listing URLs, and the checkout URL. No eBay or flycheap call. `prepare_flight` never returns ok today (flycheap has no fare search), so it is never read. |
| `mail.apply_labels`, `mail.apply_label_map` | Gmail by GET: the label by id (its name must be the approved one), then each approved thread (`format=minimal`), which must carry the label id. The map combines one leg per label. |
| `mail.draft_reply`, `mail.draft_nudge` | The Gmail draft by id: its thread, and the sha256 of the bare To address, the subject and the body. A nudge also SELECTs `mail_followups.nudged_at`. Mail is off (unknown, `disabled`) when `GOOGLE_REFRESH_TOKEN` is unset. |
| `messages.draft` | The `messages_inbound` rows by id: a draft present, with the recorded text hash. |
| `fiverr.draft_buyer_reply` | The `messages` row by the recorded `draft_id`: source `fiverr`, the approved conversation, the body hash. The insert now says `RETURNING id`. |
| `mac.open_app`, `mac.play_music`, `mac.music_control`, `mac.add_reminder` | Only ATLAS's own `mac_jobs` row by job id. A wrong op or params is contradicted, class `policy`; a job not done, or not ok, is contradicted. Otherwise unknown by design: the Mac agent has no read-only operation (no list-reminders, no now-playing, no is-open), and asking it anything means queueing a job, which is a write. Off (no `MAC_AGENT_TOKEN`) is unknown, `disabled`. |
| `marketing.propose_experiment` | The experiment row by id (business, platform, variant, hypothesis, not withdrawn) and its draft row, not dropped. |
| `marketing.propose_idea` | The idea row by id: business, mechanism, idea hash; dropped is contradicted. |
| `marketing.record_outcome` | The experiment row (outcome, lesson hash, `measured_at`) and the playbook memory by id (namespace, content hash). |
| `nightshift.run` | The `nightshift_jobs` row by id, by SELECT (never `check_jobs`, which UPDATEs): kind, task hash, branch, container, status. `stopped` is contradicted; `failed` is verified, because the job reached the sandbox. |
| `nightshift.research`, `research.report` | The job row (kind research, done) and the `research_reports` and `reports` rows: done, question, report id, body hash. |
| `school.draft_work` | The `content_drafts` row by id (kind, source item, not dropped, body hash) and each recorded file, which must exist, be non-empty, and sit inside the draft directory. |
| `school.set_course` | The `school_courses` row by normalised code: its `work_kind`. |
| `vault.log_reading` | The `library_entries` row by id (kind, title, notes hash, memory id) and that memory. |
| `vault.reindex` | The `vault_documents` rows for the first 20 recorded paths: sha prefix and chunks. A newer sha is unknown, class `stale`. A run that indexed nothing is checked only by the document count and last-indexed time. |
| `lessons.clear` | Its own SELECT of `blocked()`'s rule: no row may still block, and the latest cleared row is read. `blocked()` itself fails open, so it is not used. |
| `rhythm.note_circumstance` | The `rhythm_notes` row by the rollback-token id: kind, detail hash, until. Miro's words never go into evidence. |
| `linkedin.approve_draft` | The post-bank entry, read from the state file without a browser, the lock or a save: status approved, `posted_unverified` or published, and a payload hash equal to the approved `text_sha256` (a mismatch is contradicted, class `security`). |
| `youtube.upload`, `youtube.set_visibility` | The video by id, by GET on `videos.list`: exists, title, and `privacyStatus` (private for an upload, the approved value for a change). The token comes only from the module's cache; a cold cache is unknown, class `stale`, because a refresh is a POST. |
| `youtube.draft_metadata` | The `content_drafts` row by id: kind `youtube_video`, not dropped, source, title, body hash. |
| `tiktok.upload_draft` | Nothing is read: TikTok's publish-status fetch and video list are POSTs. Unknown by design, with the `publish_id` as evidence. |
| `screening.book_slot` | The calendar event by id (start, 30 minutes, no other attendee) and the `screening_calls` row (`booked`, that event id). The title holds the caller's number, so neither goes into evidence. |
| `mirotech.price_check` | Nothing to read: a read-only search that writes nothing. Unknown by design, with the run's comparables summary as evidence. |

Each of `youtube.*`, `tiktok.*`, `screening.*` and `mirotech.*` returns unknown, class `disabled`, before any read when its module is off: named in `ATLAS_DISABLED_MODULES`, or missing its `requires_env` (and, for YouTube, the channel's credentials; for screening, the calendar's `GOOGLE_*`).

### Coverage

All 71 actions in the catalogue have a registered verifier. Having one does
not mean an action can read verified:

- **Never verified, by design (8):** `mac.open_app`, `mac.play_music`,
  `mac.music_control`, `mac.add_reminder`, `tiktok.upload_draft`,
  `mirotech.price_check` and `browser.submit_form` are unknown at best, with
  the reason. (The Guardian actions were removed on 2026-10-29.)
- **Can read verified (63):** every other action. `purchasing.prepare_flight`
  is among them but is never read today, because its run cannot return ok.
  `linkedin.update_page_identity` never reads verified for a change that
  includes a logo.

### Reconcilers

These have one: the reel pair, `instagram.*`, `linkedin.publish_post`,
`gitlab.open_issue`, `calendar.create_block`, `calendar.book_with`,
`calendar.decline_invite`, `cloudhost.snapshot_all`, `nightshift.merge`,
`stripe.retry_invoice`, `messages.reply`, `matrix_rooms.send_reply`,
`agency.send_outreach`, `delegate.claude_code`, and, new with this pass, `agency.suppress`,
`marketing.record_outcome`, `school.set_course`, `lessons.clear`,
`linkedin.approve_draft` and `youtube.set_visibility`. A reconciler is added
only where the approved params carry the id and one SELECT or GET answers;
most Tier 1 actions mint their id inside `run()`, so they have none.

### Absence in a listing

A reconciler that looks for its effect in a listing (thread messages, comment
replies, a room's newest events, merge requests) gives contradicted only when
the listing was read whole, or reaches back past the run's start. A full
first page that ends after the start gives unknown.
Which actions have a reconciler is listed under Reconcilers above.

### Known limits

- `reel_posts` only ever receives instagram and tiktok rows
  (`crosspost._record_posts`). A reel with no facebook row therefore proves
  nothing. The reel reconciler looks for the Facebook leg in the Page's own
  video listing.
- Whether CloudHost's `/snapshot` returns the complete list (pagination,
  ordering) has not been read from production. So an instance's absence is
  contradicted only while at least one of the same run's snapshots is
  visible. If none are, the result is unknown.
- The CloudHost snapshot listing's field for the VM is taken from `vmIdentifier`,
  `vm_identifier`, `vmId`, `vm_id` or `vm.identifier`. A listing that has
  none of these gives unknown, never verified. The real shape has not been
  read from production.
- The LinkedIn reconciler can only pin a run to a post-bank entry when the
  start row names `entry_id`, or when exactly one entry was stamped after the
  start. Otherwise it gives unknown.
- The reshare read-back looks once at the first screen of Miro's "all"
  activity, which also lists his comments and reactions. A busy day can push
  the repost below it, which reads contradicted. The run itself only returns
  ok after it saw the repost there.
- The deploy read-back compares titles only. Two releases with the same
  `<title>` cannot be told apart, so a stale cache that serves the previous
  release under the same title reads verified.
- The form read-back compares hosts ignoring a leading `www.`. A form whose
  page legitimately redirects to another host (shown in the dry run) still
  reads contradicted.
- The deploy read-back treats any 4xx as contradicted, so a site behind basic
  auth (401) or a staging lock (403) reads contradicted, not unknown. A
  deployed client site is meant to be public.
- The Gmail and calendar read-backs (`mail.*`, `calendar.*`,
  `screening.book_slot`) take their token from the module's helper, which
  refreshes it with a POST to Google's token endpoint when the cache is cold.
  That POST exchanges a credential and changes nothing in the mailbox or
  calendar; the YouTube read-backs are stricter and use the cache only.
- `vault.reindex` runs recorded before this change carry no `docs`, so they
  read unknown when they indexed anything.
