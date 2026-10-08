# ATLAS — verification against the brief

Every line is either **verified** (I ran it and read the output), **built, not
verifiable here** (correct by inspection, needs something only you can supply),
or **outstanding**. Nothing is marked done on the strength of having written it.

Last run: 2026-09-22, against `atlas.example.com` (203.0.113.10).

---

## The eight invariants

| # | Invariant | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Collectors only read; the executor only acts | **verified** | A test walks the AST of every `atlas/api` module and fails if any of them calls `execute()` or `rollback()`. Nothing reachable from a browser can act. |
| 2 | The LLM never executes | **verified** | `reason.diagnose` discards any action name outside the catalog before anything happens; `tests/test_reason.py` covers an invented name, a shell command and a null. Parameters are now checked against `Action.params` too — the name was closed, the arguments were not. |
| 3 | Tiers assigned in code, never inferred | **verified** | `PERMANENT_DENY` rewritten and tested against 30 names. No runtime override exists: "approve + always" records a *promotion candidate* and is never read back to skip a gate. |
| 4 | Every ingested byte is untrusted | **verified** | `wrap_untrusted` now neutralises the marker in any case and spacing, including the opening tag. Voice transcripts go through it too. |
| 5 | Silence is never consent | **verified** | Expiry is enforced inside the same atomic UPDATE that claims a proposal, so a tab open overnight cannot approve a lapsed one. |
| 6 | Never guess a number | **verified** | Every dashboard response carries `as_of`; the brief is built from live queries; a section that could not be built says so rather than rendering empty. |
| 7 | A check that cannot run reports UNKNOWN | **verified** | Fixed in four modules that defaulted missing fields to numbers reading as OK, and in the scheduler, which silently dropped checks that raised or timed out. Production data confirms: no `failing` observation since the fix, 68 `unknown`. |
| 8 | Tier 1 requires a dry run and a rollback | **verified** | `contracts.py` refuses construction; `permissions.classify` refuses again for stale builds; both are tested. |

---

## Step 1 — verify the scaffold

**Done.** `FINDINGS.md` records 18 findings with a status table. Every one is
fixed except two decisions left to you (C3, D2), which are marked as such.

## Step 2 — make it actually run

| Bullet | Status |
| --- | --- |
| `docker compose up -d` from a clean checkout | **verified** — `bootstrap/02-install.sh` added Docker's *Ubuntu* repo on a *Debian* host, which could never have worked. Fixed to read the distro from os-release. |
| `python -m atlas.cli init-db` | **verified** — 17 tables. Note: it applies the *image's* schema, so a schema change needs a rebuild. |
| `python -m atlas.cli selftest` | **verified** — prints mode, every module, every check with interval/severity/baseline, every action with tier and reason, every disabled module with why, and the audit chain. |
| `python -m atlas.cli check` | **verified** — runs all 8 checks; all report UNKNOWN with actionable detail. |

## Step 3 — tests where a bug is expensive

All six areas covered, **252 tests passing**, `ruff` clean, TypeScript strict clean.

## Step 4 — module stubs

`flycheap`, `guardian`, `inroots`, `vpsie` rewritten. Each docstring records the
endpoints and response shapes it assumes. Added `modules/_http.py`: a wrong
shape is UNKNOWN with a message naming the path, and `redact()` strips
credentials from anything bound for an Observation — `flycheap` and `instagram`
pass tokens as query parameters and httpx puts the full URL in the exception.

## Step 5 — browser extension

Built. Deny-list applied in the browser before anything is buffered, one-click
pause, 90-day deletion with the roll-up surviving — **verified** by inserting a
200-day-old row and watching it go while its aggregate stayed. 12 deny-list
assertions pass, including a lookalike-domain case.

## Step 6 — morning brief

Built, scheduled for 07:00, and stored when sent so the archive is real.
The reports archive is **server-rendered** — `/reports` previously fell through
to the SPA, so the one page meant to open from a text needed the bundle first.
A test now asserts none of the server-rendered templates contain a `<script>`.
**Caveat: it currently has nowhere to go** — no channel credentials are set, so
`notify` will log "no channel could deliver".

---

## Infrastructure

| Item | Status |
| --- | --- |
| Host | **verified** — but it is Debian 13, not Ubuntu 24.04, and was 1 vCPU / 1.9 GB until you resized it. |
| 443 serves only `/webhooks/*` and `/healthz` | **verified from the public internet** — every dashboard path returns 404. |
| Dashboard on the mesh | **verified** — `https://atlas.example.com` over NetBird, trusted cert, no port. |
| Postgres never published | **verified** — no `ports:` entry. |
| pgvector + pg_trgm | **verified** |
| Redis | **verified** |
| Ollama | **fixed and verified** — it sits behind a compose profile, so `docker compose up` never started it, leaving `Need.PRIVATE` with no provider at all. Now running `llama3.2:3b`, and a live call returned in 22.9s. **On one core it is slow**: fine for the short classification the inbox agent does, not for prose. |
| **Git remote** | **OUTSTANDING** — 35 commits, no remote. Nothing is pushed to `aicode.example.com`. If this box dies, the work dies with it. |

## Models

Routing table implemented. **CRITICAL was not cross-provider**: it padded the
chain with a second Anthropic model, so `think_twice` compared Opus to Sonnet.
Now left short, and a short chain escalates rather than agreeing with itself.

**No provider keys are set**, so only `PRIVATE` and `TRIVIAL` have routes (to
Ollama). The reasoner, the brief's prose and voice answers cannot run.

## Agents

**8 agents**, matching the eight the brief names: concierge, ops, inbox,
growth, commerce, scheduler, analyst, sentinel. The brief says "Nine
specialists" and then lists eight — I have not invented a ninth. Tell me what
the missing one should do and I will add it.

`inbox` correctly routes to `Need.PRIVATE`. Tool scoping is enforced in
`kernel/tools.py::invoke`, not in a prompt.

## Memory

All four kinds present: episodic, semantic, procedural, working.

## Channels

All registered: imessage, slack, matrix, sms, voice. Slack is skipped when
iMessage is reachable (**tested**), quiet hours hold everything below CRITICAL
(**tested**), the phone line speaks Fish Audio MP3 with a `<Say>` fallback,
Google Voice is read-only by design.
**No credentials are set**, so none can actually deliver.

## Phase discipline

**Watch-only is on and enforced in `executor.execute`** — the single path to the
outside world. `selftest` states it in the first line.

---

## What is still outstanding

1. **Nothing is pushed to git.** The single biggest risk on this list.
2. **No credentials anywhere** — no model provider, no channel, no module token.
   12 of 14 modules are disabled for this reason, which is correct behaviour and
   is named per module in `selftest`.
3. **Voice is partly alive.** The local model can answer now, but there is still
   no transcriber (needs `OPENAI_API_KEY`), no TTS (`FISH_AUDIO_API_KEY`) and no
   wake word (a Picovoice key plus an `atlas.ppn` trained for "atlas"). Until
   then the overlay falls back to click-to-talk and says which parts are
   missing, rather than appearing to listen.
4. **The ninth agent** — the brief's count and its list disagree.
5. **C3** (rate-limit window is in-process) and **D2** (is a dry run mandatory
   for Tier 2?) remain your decisions, not oversights.

---

## Phases 12–20 (branch `phases-12-14`, integration `int`)

### The UI lockfile satisfies the image's `npm ci`

**verified** 2026-09-24. `ui/package.json` declared `@huggingface/transformers`
(Task 19) and `@mediapipe/tasks-vision` (Task 16) but `ui/package-lock.json`
listed neither, nor `vitest`. `Dockerfile` stage 1 runs
`npm ci --no-audit --no-fund`, which refuses a lock out of sync with
`package.json` — so the image could not build on a clean deploy, and the green
vitest proved nothing because `ui/node_modules` in a worktree is a symlink to
the main checkout's install.

Regenerated with `npm install --package-lock-only` (touches no
`node_modules`). The lock now pins `@huggingface/transformers@4.3.0`,
`@mediapipe/tasks-vision@1.0.1`, `vitest@3.2.7`, `three@0.186.1`, each with
`resolved` and `integrity`.

Proof, in an empty scratch directory holding only `package.json` and the lock
(npm 10.8.2, node 20 — the `node:20-slim` stage uses npm 10 too):

| Lock | `npm ci --dry-run` |
| --- | --- |
| before (`c2a6a90`) | exit 1 — `EUSAGE … package.json and package-lock.json … are in sync … Missing: @huggingface/transformers@4.3.0 … @mediapipe/tasks-vision@1.0.1 … vitest@3.2.7` |
| after | exit 0 — `added 240 packages` |

Not verifiable here: the real `npm ci` in Docker runs install scripts, and
`onnxruntime-node` (a dependency of transformers) has a postinstall
(`node ./script/install`) that may fetch a native binary, so give the build
host outbound HTTPS during `docker build`.

### Phases 12–20 on a bare environment

**Run 2026-09-24 at commit `eb65617`** (worktree `sdd/task-17`, based on the
integration branch at `c2a6a90`), Python 3.14 from `.venv`, node 20, npm 10.

| Step | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **verified** — 2628 passed, 1 skipped, 2 warnings (the two pre-existing deprecation warnings) |
| `.venv/bin/python -m ruff check .` | **verified** — 34 errors (the ceiling is 40). `git blame` puts every one of the 34 lines in a commit that predates the phases (an ancestor of `781c858`); none is in any of the 183 files Phases 12–20 created |
| `cd ui && npx tsc -b --noEmit` | **verified** — clean |
| `cd ui && npm run build` | **verified** — built; the one warning is chunk size (the three.js bundle), not an error |
| `cd ui && npm test` | **verified** — 13 files, 202 tests passed |
| `npm ci` against the lock | **verified** — see "The UI lockfile satisfies the image's `npm ci`" above |
| `env -i PATH=$PATH HOME=$HOME python -m atlas.cli selftest` | **verified** — below |

Two tests this task added watch the classes of bug that kept recurring:
`tests/test_schema_lint.py` resolves every `alias.column`, INSERT/SET column
and table in 360 SQL strings across 55 files against `db/schema.sql` (it
would have caught Task 22's `p.finding_id`), and `tests/test_tool_catalog.py`
checks every agent write tool against the action it names, env-independent.

**`selftest`, no credentials and no database.** Every one of the sixteen
modules Phases 12–20 created — agency, browser, content, lessons, marketing,
messages, messaging, nightshift, perplexity, purchasing, research, rhythm,
school, screening, vault, youtube — loads with nothing set. The five disabled
modules all predate these phases and each is named with the variables it is
blocked on (`connect` calls this BLOCKED). Before this task the run ended in a
`KeyError: 'DATABASE_URL'` traceback at the audit step; it now says UNKNOWN
and still exits 1, because an unverifiable chain is not a passing one.

```
mode     : WATCH-ONLY — the executor will refuse every action

modules  : 27 loaded, 5 disabled
  + agency           2 check(s), 9 action(s)  — Agency
  + browser          1 check(s), 1 action(s)  — Browser
  + comms            4 check(s), 0 action(s)  — Reporting channel
  + content          1 check(s), 2 action(s)  — Content
  + fiverr           4 check(s), 1 action(s)  — Fiverr
  + flycheap         11 check(s), 0 action(s)  — FlyCheap
  + guardian         8 check(s), 0 action(s)  — Guardian
  + inroots          8 check(s), 0 action(s)  — Roots
  + instagram        8 check(s), 3 action(s)  — Instagram
  + lessons          0 check(s), 1 action(s)  — Lessons
  + linkedin         6 check(s), 0 action(s)  — LinkedIn pages
  + mail             2 check(s), 3 action(s)  — Mail
  + marketing        2 check(s), 3 action(s)  — Marketing
  + messages         3 check(s), 2 action(s)  — Messages (iMessage, Instagram DM)
  + messaging        3 check(s), 1 action(s)  — Messaging (WhatsApp, Telegram, Slack)
  + netbird          3 check(s), 0 action(s)  — NetBird mesh
  + nightshift       1 check(s), 3 action(s)  — Nightshift (overnight sandbox)
  + perplexity       1 check(s), 0 action(s)  — Perplexity (research over MCP)
  + purchasing       0 check(s), 2 action(s)  — Purchasing
  + research         0 check(s), 1 action(s)  — Research
  + rhythm           1 check(s), 1 action(s)  — Rhythm
  + school           3 check(s), 2 action(s)  — College (CUNY, Fall 2026)
  + screening        1 check(s), 1 action(s)  — Screening line
  + stripe           12 check(s), 1 action(s)  — Payments (Stripe)
  + vault            1 check(s), 2 action(s)  — Vault & Library
  + vpsie            5 check(s), 1 action(s)  — VPSie
  + youtube          6 check(s), 3 action(s)  — YouTube
  - calendar         DISABLED: missing env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
  - gitlab           DISABLED: missing env: GITLAB_TOKEN
  - matrix_rooms     DISABLED: missing env: MATRIX_HOMESERVER, MATRIX_TOKEN
  - mirotech         DISABLED: missing env: EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, EBAY_REFRESH_TOKEN
  - tiktok           DISABLED: missing env: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REFRESH_TOKEN
```

Every action, with its tier (`auto` = Tier 1, `ask` = Tier 2; nothing new is
refused, because nothing new names a Tier 3 verb):

```
actions  : 43 in the catalog, 0 refused
  [auto] agency.audit                       reversible and self-contained [reversible]
  [auto] agency.build_site                  reversible and self-contained [reversible]
  [ask ] agency.deploy_site                 requires approval [reversible]
  [auto] agency.draft_outreach              reversible and self-contained [reversible]
  [auto] agency.mockup                      reversible and self-contained [reversible]
  [auto] agency.prospect                    reversible and self-contained [reversible]
  [auto] agency.rank                        reversible and self-contained [reversible]
  [ask ] agency.send_outreach               requires approval [irreversible]
  [auto] agency.suppress                    reversible and self-contained [reversible]
  [ask ] browser.submit_form                requires approval [irreversible]
  [auto] content.draft                      reversible and self-contained [reversible]
  [auto] content.render_video               reversible and self-contained [reversible]
  [auto] fiverr.draft_buyer_reply           reversible and self-contained [reversible]
  [ask ] instagram.hide_comment             requires approval [reversible]
  [ask ] instagram.publish                  requires approval [reversible]
  [ask ] instagram.reply_comment            requires approval [irreversible]
  [auto] lessons.clear                      reversible and self-contained [reversible]
  [auto] mail.apply_labels                  reversible and self-contained [reversible]
  [auto] mail.draft_nudge                   reversible and self-contained [reversible]
  [auto] mail.draft_reply                   reversible and self-contained [reversible]
  [auto] marketing.propose_experiment       reversible and self-contained [reversible]
  [auto] marketing.propose_idea             reversible and self-contained [reversible]
  [auto] marketing.record_outcome           reversible and self-contained [reversible]
  [auto] messages.draft                     reversible and self-contained [reversible]
  [ask ] messages.reply                     requires approval [irreversible]
  [ask ] messaging.reply                    requires approval [irreversible]
  [ask ] nightshift.merge                   requires approval [irreversible]
  [auto] nightshift.research                reversible and self-contained [reversible]
  [auto] nightshift.run                     reversible and self-contained [reversible]
  [auto] purchasing.prepare_cart            reversible and self-contained [reversible]
  [auto] purchasing.prepare_flight          reversible and self-contained [reversible]
  [auto] research.report                    reversible and self-contained [reversible]
  [auto] rhythm.note_circumstance           reversible and self-contained [reversible]
  [auto] school.draft_work                  reversible and self-contained [reversible]
  [auto] school.set_course                  reversible and self-contained [reversible]
  [auto] screening.book_slot                reversible and self-contained [reversible]
  [ask ] stripe.retry_invoice               requires approval [irreversible]
  [auto] vault.log_reading                  reversible and self-contained [reversible]
  [auto] vault.reindex                      reversible and self-contained [reversible]
  [ask ] vpsie.snapshot_all                 requires approval [irreversible]
  [auto] youtube.draft_metadata             reversible and self-contained [reversible]
  [ask ] youtube.set_visibility             requires approval [reversible]
  [ask ] youtube.upload                     requires approval [reversible]

audit    : UNKNOWN — the audit log could not be read (KeyError: 'DATABASE_URL')
```

**Every check of those sixteen modules, run once in the same bare
environment** (`REGISTRY.load_all()`, each `check.run()` with a 20 s timeout):
25 of 26 are UNKNOWN, each saying what it could not read; the one OK,
`browser.engine`, needs no credential and really launched Chromium. The run
found one check that raised instead of answering (`agency.prospect_budget`,
`KeyError`); it is fixed in `0ce3723` and reads as below.

```
agency.leads_waiting                     UNKNOWN  'DATABASE_URL'
agency.prospect_budget                   UNKNOWN  could not read this month's Places spend: KeyError: 'DATABASE_URL'
browser.engine                           OK       chromium launched and loaded about:blank
content.drafts                           UNKNOWN  'DATABASE_URL'
marketing.experiments_overdue            UNKNOWN  could not count experiments: 'DATABASE_URL'
marketing.playbook_size                  UNKNOWN  could not count playbook lessons: 'DATABASE_URL'
messages.bridge                          UNKNOWN  BLUEBUBBLES_URL / BLUEBUBBLES_PASSWORD not set
messages.dm_scope                        UNKNOWN  INSTAGRAM_ACCOUNT_ID / INSTAGRAM_TOKEN not set
messages.unanswered                      UNKNOWN  both bridges unreachable — iMessage: BLUEBUBBLES_URL / BLUEBUBBLES_P
messaging.telegram_bot                   UNKNOWN  TELEGRAM_BOT_TOKEN not set
messaging.unanswered                     UNKNOWN  'DATABASE_URL'
messaging.whatsapp_token                 UNKNOWN  WHATSAPP_TOKEN/WHATSAPP_PHONE_ID not set
nightshift.jobs                          UNKNOWN  could not read nightshift_jobs: 'DATABASE_URL'
perplexity.budget                        UNKNOWN  PERPLEXITY_API_KEY not set — Perplexity is not configured, nothing i
rhythm.pattern                           UNKNOWN  could not read rhythm_hours: 'DATABASE_URL'
school.calendar_feed                     UNKNOWN  BRIGHTSPACE_ICS_URL not set — Brightspace → Calendar → Settings → Su
school.grades                            UNKNOWN  Gmail not configured (GOOGLE_REFRESH_TOKEN unset) — Brightspace noti
school.notifications                     UNKNOWN  Gmail not configured (GOOGLE_REFRESH_TOKEN unset) — Brightspace noti
screening.screening_line                 UNKNOWN  SCREENING_FROM is not set; the line is not configured, so nothing ca
vault.index                              UNKNOWN  vault directory not found: data/vault
youtube.flycheap_latest_videos           UNKNOWN  no YOUTUBE_FLYCHEAP_CLIENT_ID / YOUTUBE_FLYCHEAP_CLIENT_SECRET / YOU
youtube.flycheap_stats                   UNKNOWN  no YOUTUBE_FLYCHEAP_CHANNEL_ID set for channel 'flycheap'
youtube.flycheap_token                   UNKNOWN  no YOUTUBE_FLYCHEAP_CLIENT_ID / YOUTUBE_FLYCHEAP_CLIENT_SECRET / YOU
youtube.guardian_latest_videos           UNKNOWN  no YOUTUBE_GUARDIAN_CLIENT_ID / YOUTUBE_GUARDIAN_CLIENT_SECRET / YOU
youtube.guardian_stats                   UNKNOWN  no YOUTUBE_GUARDIAN_CHANNEL_ID set for channel 'guardian'
youtube.guardian_token                   UNKNOWN  no YOUTUBE_GUARDIAN_CLIENT_ID / YOUTUBE_GUARDIAN_CLIENT_SECRET / YOU
```

**Not verifiable here:** every third-party endpoint the new modules assume
(MODULE_AUDIT.md, "Phases 12–20") — no credential is set for any of them; the
SQL against a live Postgres (the lint checks names, not semantics); pinch,
clap and the local wake gate on a real camera and microphone; and the image
build on the server, which needs the two model scripts run first
(CONFIGURE.md, "Deploy order").
