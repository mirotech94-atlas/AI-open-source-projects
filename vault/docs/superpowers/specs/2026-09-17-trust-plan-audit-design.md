# Trust, Accuracy & Productivity plan — audit and sequencing

Source: `GUARDIAN_TRUST_AND_PRODUCTIVITY_PLAN.md` (external assessment, 2026-09-17).

## Why this document exists

The plan was written from **outside the product**. Its author says so in §0:
`my.guarder.io` returned an empty SPA shell, so every dashboard claim is
reconstructed from the API contract, and the author asks to be corrected —
"Where I have guessed, it says 'inferred'. Correct me and the plan tightens."

Taken literally the plan is 37 backlog items over 90 days. Taken *against the
code*, a large part of it already ships. This document is the correction pass:
every P0 and every load-bearing P1 checked against the actual module, with a
citation, so the remaining work is the real remainder and not a re-build of
things that exist.

The most useful output here is the shrinkage. Five of the ten P0 items are
already done or nearly so.

## P0 — the plan's "trust-breaking or sale-blocking" list

| # | Plan item | Real state | Evidence |
|---|---|---|---|
| 1 | `unknown` first-class; never green; score → unknown under coverage | **Partial** — `unknown` is plumbed widely and deliberately; the *score coverage gate* does not exist | `payloads/staleness.py` is an essay on exactly this problem; no coverage gate in `payloads/security.py` |
| 2 | Coverage + as-of chip on every panel | **Mostly done** | `stale`/`stale_note`/`age_s`/`last_checked` modelled; Q9/Q12 of the Sept-15 review |
| 3 | Maintenance windows + SLA exclusion | **Partial — mute only** | `integrations/registry.py:107` `maintenance_active()` reads one tenant-wide `settings["maintenance_until"]` and suppresses *delivery*. Not per host/site, not recurring, and it excludes nothing from uptime or score |
| 4 | "Handled" = verified, root-cause deduped | **Partial** | `verification.py` reconciles a repair against later target evidence and is careful about it; the verified/recurred/unverified *breakdown* is not reported |
| 5 | Uptime denominators, unknown excluded, confirm-before-alarm, blast-radius | **Largely missing** | `payloads/assets.py:225` is `round(100 * sum(vals) / len(vals), 2)` over present samples. Unknowns are excluded only because absent samples aren't in the list — never published, no minimum sample, and `round()` rounds half-up, which the plan explicitly forbids |
| 6 | 7-day baseline / learning mode | **Missing** | The only "baseline" is the LAN known-device baseline (`routers/write.py:738`), a different concept |
| 7 | Terms, privacy, DPA, security.txt | **Missing, knowingly** | `site/index.html:705` comments that there is "no about page and no terms/privacy page on this domain yet" and omits the footer Legal group rather than link to pages that don't exist |
| 8 | Hub-side dead-man alerting | **Done, and well** | `scheduler.py:350` `sensor_watch_job()` raises `sensor:silent` per tenant through the ordinary alert + delivery path, skips never-enrolled and demo tenants, and is idempotent |
| 9 | Event idempotency keys, dedupe on replay | **Done, thoroughly** | `ingest.py` dedups metrics, events, fixes, presence and DNS queries on natural keys, with the replay case called out in comments |
| 10 | Versioned score model, frozen history | **Missing (stamp only — history is already frozen)** | The headline score is computed **on the sensor** (`sensor/guardian_sensor/alerts.py:597` `security_score`), not the hub, and stored per sweep in `Scan.raw["score"]` + `Scan.score`. `payloads/security.py:427` is a different number — the per-site/host scorecards. So history never recomputes; the only gap is that nothing says which rubric produced a given row |

**Read:** #8 and #9 are done. #2 is close. The genuine P0 remainder is the
score (#1 + #10, one code path), uptime honesty (#5), maintenance as an SLA
concept (#3), the handled breakdown (#4), learning mode (#6), and legal (#7).

## P1 — spot-checked

| Item | State | Evidence |
|---|---|---|
| 11 triage queue, `Cmd-K` | **Done** | `web/src/shell/CommandPalette.tsx`, `components/InboxTriage.tsx` |
| 13 Evidence bundle export | **Missing** | no match anywhere |
| 14 `GET /v1/metrics` timeseries | **Missing** | `routers/api_v1.py` has 26 endpoints, no metrics |
| 15 Device identity for randomized MACs | **Partial** | `payloads/familiarity.py` carries the signals; confidence + split/merge is the gap |
| 16 Signed outbound webhooks + delivery log | **Mostly done** | `integrations/webhook.py`; `m.Delivery` is the log |
| 19 Immutable daily facts | **Missing** | aggregates recompute from raw |
| 20 Promote `questions[]` | **Exists, placement is the ask** | `payloads/overview.py:249` |
| 24 Public demo tenant | **Done** | `guardian seed-demo`, live and public |
| §2.6/6 `GET /v1/fixes` | **Missing** | fix log still inside `/v1/security` |

## Answers to the plan's §10 open questions

3. **Neither, as asked — the score is computed on the sensor and stored per
   sweep.** `alerts.security_score` runs during the sweep and the result is
   written once into `Scan.raw["score"]` and `Scan.score`, never recomputed.
   So the plan's fear in §2.5C — "a trend line that reshapes itself when you
   ship a weight change" — does not apply to stored history. The real gap is
   narrower: nothing recorded *which* rubric produced a row, so two
   incomparable numbers were being drawn on one line without saying so.
   Fixed: `alerts.SCORE_MODEL`. (Note the hub's `payloads/security.py:427` is
   a **different** score — the per-target scorecards — and that one *is*
   computed live per request.)
5. **Yes, the hub dedupes a replayed batch** — per-record natural keys in
   `ingest.py`, not one batch key. The plan's worry about replay inflating
   "handled" does not apply.
7. **The assistant reads the same payload code path as the UI** (`chat/`
   against `payloads/`), which is the property §2.7 asks for.
2. **A maintenance concept exists but only mutes notifications** — see P0#3.

### The rest of §10, from the code

1. **One sweep every 5 minutes covering all domains, not per-domain
   intervals.** `runner.SLEEP_SECONDS = 300`. Device ARP, site HTTP and host
   SSH all ride the same cycle, so the Metric Contract can assume a single
   cadence rather than four. Two exceptions: the job queue is polled every
   10s (`QUEUE_POLL_SECONDS`, exponential backoff on failure), and external
   exposure re-probes at most hourly (`home.py`, a 3300-second staleness
   check) because it leaves the network.

4. **There is no multi-network score to aggregate.** The score is one
   per-tenant number computed over the whole sweep; per-network health
   (`networks.health`) is expressed as open CRIT/WARN *counts*, not a score.
   So the plan's worry that "averages hide one broken site" does not apply
   yet — but neither does a rollup exist for the MSP case, and counts are
   arguably the better primitive to build one from.

6. **A Python tree, not a single binary, and not signed.** `pyproject.toml`
   with a console script, plus a `Dockerfile`. No signing, no published
   checksum. P1 #26 stands as written.

8. **Presence is NOT role-gated and no consent is recorded.**
   `routers/read.py:116` serves `/presence` at `ctx=VIEWER`, the lowest role,
   and nothing records per-network consent or who viewed it. This is the one
   place where the plan's §2.5F is understated rather than overstated: it is
   the most sensitive data in the product and currently the least protected.

9. **Raw samples are kept 90 days and daily figures are not stored at all.**
   `retention.py`: `retention_days` 90 for `metric`, `event`,
   `presence_sample` and reports; DNS is the only rolled-up series (raw
   `dns_query` 7 days into `DnsRollup`, kept 365). `Scan` is never pruned,
   which is why score history is already immutable. Uptime, by contrast, is
   recomputed from `metric` rows on every request — so it *does* change
   under the client's feet once those rows age out at 90 days. That is the
   live case for P1 #19 (immutable daily facts), and it is narrower than the
   plan assumed: scores are safe, availability is not.

10. Not answerable from the repo — how many prospects ask for a self-hosted
    hub is the owner's pipeline data.

## Sequence

Ordered by trust damage per unit of work, P0 code first:

1. **Score model version + coverage gate** — §2.5C, the plan's "biggest trust
   liability", and #1 and #10 are the same function.
2. **Uptime honesty** — published denominator, minimum sample, truncate
   instead of round, then confirm-before-alarm and blast-radius suppression.
3. **Maintenance windows as SLA exclusions** — extends the existing mute.
4. **Handled breakdown** — verified / recurred / unverified on top of `verification.py`.
5. **Learning mode** — the plan's highest-churn-reduction claim.
6. **Legal scaffolding** — placeholders only; see below.
7. Then P1: daily facts, `/v1/metrics`, `/v1/fixes`, evidence bundles.

## Progress, 2026-09-18

Landed on `codex/guardian-improvements`:

1. `87e2516` — **score model + coverage.** P0 #10 in full, and the score
   half of P0 #1. Found the concrete bug the plan only guessed at: an
   unreachable host drops out of three deduction categories, so the score
   *rises* when Guardian goes blind, and an `optional` host raises no
   availability deduction either, making that case invisible from every
   angle.
2. `ac84ad8` — **uptime honesty.** The measurable part of P0 #5: truncation
   instead of half-up rounding (99.849% was published as 99.85%), a
   published denominator, and a minimum sample below which no percentage is
   claimed. Confirm-before-alarm and blast-radius suppression are the
   remaining half and are sensor-side.
3. `9fa6dc5` — **security.txt.** The unambiguous part of P0 #7.

4. `2fd20ea` — **maintenance windows as SLA exclusions.** P0 #3. Windows
   are recorded as intervals beside the existing mute, in tenant settings
   rather than a new table (no Alembic revision on a chain whose 0018-0022
   are uncommitted). Cancelling truncates, so a window cannot be back-dated
   over an accident.
5. `c14c40f` — **blast-radius suppression.** Half of P0 #5. Collapses an
   all-sites-down sweep into one `sensor:uplink` event when the sensor can
   independently show it has no internet, and stops those unmeasured sites
   deducting Availability from the score.

6. `89f4fc6` — **`GET /v1/metrics` and `GET /v1/fixes`.** P1 #14 and
   §2.6/6. Samples carry the state the dashboard applied, `planned`
   included, and a test recomputes uptime from the endpoint and asserts it
   equals the published figure.
7. `a753eaa` — **DNS coverage ratio.** P1 #22's measurable half. Encrypted-DNS
   detection stays open; until it exists, coverage is the honest signal.
8. `516cfb2` — **certificate issuer-change watch.** One of §2.5I's four.

9. `096be80` — **learning mode.** P0 #6, the plan's highest-churn-reduction
   claim. Only CRIT is delivered for the first seven days; everything else
   is stored and waits in the Inbox. The window is anchored on the oldest
   observation Guardian holds, across `Scan` *and* `Metric` — the
   scan-only version shipped the exact bug its own docstring claimed to
   prevent (the demo seeds two scans and thirty days of metrics), and the
   demo-tenant test CLAUDE.md asks for is what caught it.
10. `6b89e34` — **evidence bundles.** P1 #13, which §2.5O calls the single
    highest-value item in that section. `GET /v1/alerts/{key}/evidence`.
    Sensor and hub timestamps are both reported and never reconciled;
    a repair's `ok` stays separate from its `verified_at`.
11. `6d8124f` — **encrypted-DNS detection.** P1 #22's other half, closing
    the gap the DNS coverage commit could only point at.

12. `9785dca` — **the score coverage gate.** The other half of P0 #1, and
    the one that matters: publishing coverage said how blind Guardian was
    without stopping the number beside it being wrong. Below 80% coverage
    the total is withheld. `SCORE_MODEL` → 2026.10.
13. `2e11cd8` — **device identity on evidence.** P1 #15. A name match alone
    suggested a parent even while that parent was still answering; the
    decisive signal is the old address going quiet as the new one appears,
    reported with its reasons and never auto-merged.
14. `63164a7` — **`questions[]` promoted.** P1 #20.

15. `8c8098f` — **renewed but not served.** The last of §2.5I's four and
    the one the plan rates highest: certbot succeeds, nginx is never
    reloaded, and the host serves the old certificate until it expires.
    Invisible to any check that looks at one side; Guardian already had
    both vantage points and was not using them together.

16. `e48b7a0` — **migrations run again.** Not from the plan: merging the
    branches forked the Alembic history at `0017`, leaving two heads, and
    `alembic upgrade head` refuses to run at all with more than one — so
    `guardian migrate` was broken on this branch from the moment the two
    lines met. Fixed with a merge revision, and guarded by a test, because
    nothing complains at merge time and nobody runs `upgrade head` until
    deploy.
17. `1f85b3f` — **immutable daily facts.** P1 #19, unblocked once the
    Alembic chain was joined. Availability is frozen per day at close and
    never recomputed; the test deletes every metric row the figure came
    from and asserts it is unchanged.

18. `d1abad8` — **confirm-before-alarm was already built.** A correction:
    `runner`'s `pushable()` has always held `site:*:down`, `ssh:*:down` and
    `check:*` until the alert fires on `fire_after_runs` consecutive sweeps.
    The alert is stored on the first failure and visible in the app; only
    the PUSH waits. Untested, so now pinned, and a mutation check confirms
    the tests bite.
19. `53d6b31` — **reports read the frozen facts.** P1 #19 finished: a report
    re-run months later quotes the figure it was sent with instead of
    recomputing from rows retention has since deleted.
20. `7be8ec5` — **chain-weakest cert expiry.** §2.5I's last item, and it
    needed no dependency after all: `openssl s_client -showcerts` reads the
    chain, and the sensor already shells out to openssl elsewhere. So the
    supply-chain decision never had to be made.
21. `6bd7933` — **a status page that states its own limits.**
22. `66dbefe` — **installable dashboard.** P2 #33. The service worker
    caches nothing, on purpose.

### Already done by other sessions, found by checking rather than assuming

Pricing, billing and the legal pages are all in place. `plans.py` is the
single place a price is written and feeds the marketing pricing section, the
in-app Billing page and the Stripe lookup keys; `privacy.html` and
`terms.html` exist with no placeholders left. Three items I had listed as
open were not.

That is the third time in this work that an item looked missing and was not
— dead-man alerting, replay idempotency and confirm-before-alarm were the
others. The lesson is recorded here because the plan's whole premise is
inference from outside the product, and inference was wrong about this
codebase more often than it was right.

### The status page, and what it deliberately does not claim (P2 #32)

Shipped as `site/status.html`, with its limits written on the page. A status
page served BY the hub is worth nothing in the moment anyone visits it, so
this one is a static file on the marketing site while the hub is a separate
process behind the same nginx — the two fail independently in the case that
matters, and the page distinguishes "the hub stopped answering" from "the
machine is up". It says plainly that it is not an independent monitor and
that if it does not load at all, everything is down. True independence needs
probing from elsewhere, which is hosting rather than code.

### One decision for the owner: confirm-before-alarm

P0 #5's other half is "require 2-of-3 consecutive failures before declaring
`down`". It is deliberately NOT implemented, because the plan's costing does
not survive contact with this sensor's cadence.

The Metric Contract assumes a 60-second site check, where 2-of-3 delays a
page by two or three minutes — cheap. This sensor sweeps every 5 minutes
(`runner.SLEEP_SECONDS = 300`), so the same rule delays every site-down CRIT
by **10 to 15 minutes**. For a product whose pitch is that it notices things,
that is not a free accuracy fix; it is a trade of detection latency for
false-positive rate, and which side to land on is a product call rather than
a correctness one.

Worth noting it is also less necessary here than the plan assumed, because
the dominant source of fake outages was structural, not flaky: one blind
sensor reporting every site down at once. That is what `c14c40f` fixes, and
it costs no latency at all. What remains for confirm-before-alarm is the
genuinely flaky single check, which is a smaller problem.

Three options, in the order worth considering:

1. Confirm only WARN-and-below, page CRIT immediately. Keeps detection
   latency for outages, removes the noise from transient slowness.
2. Make it a per-site setting, defaulting off, so someone with a flaky
   upstream can opt in for that site alone.
3. Shorten the site probe below the 5-minute sweep so confirmation is cheap,
   which is the real fix but a bigger change to the runner.

Still open, with the reason:

- **P0 #4, handled breakdown** — blocked, not skipped. It has to build on
  `verification.py`, which is part of the other session's *uncommitted*
  work; a commit depending on code that is not in git is not a commit.
- **P0 #5, confirm-before-alarm** — needs the decision above.
- **§2.5I's chain-weakest expiry.** The one cert check still open. It needs
  `ssl.SSLSocket.get_verified_chain()`, added in Python 3.13, and this
  sensor targets 3.10 — so the alternative is adding `cryptography` to an
  agent installed as root, which is a supply-chain decision for the owner
  rather than a bug fix.
- **A `HUMAN_REMEDIES` entry for `cert:stale-served:`.** The generic
  fallback already explains itself, so this is wording rather than
  behaviour; worth adding next time `remedy.py` is touched.
- **Wiring reports and charts to read the daily facts.** The table exists
  and is written; the Assets page and the report builder still recompute
  from `metric`. They agree today, so this is not urgent, and they stop
  agreeing at the 90-day retention edge.
- **P1 #18 client-facing report** — PDF, white-label, scheduled, immutable
  permalink. Unblocked, and the largest remaining single item.
- **MSP console (P2 #29).** The largest thing still open. Tenant switching
  exists for a superadmin; a cross-tenant queue, per-client retention and
  white-label branding do not.
- **App i18n (P2 #35).** The plan's own framing is "ship app i18n, or cut
  the marketing locales". Both are decisions rather than tasks: the app is
  English-only behind eight translated marketing pages, and deleting eight
  locales is a marketing call that is not mine to make.
- **The brand decision (P2 #36).** `guarder.io` versus "Guardian" needs
  trademark clearance, which is the owner's.
- **P2 #37 assistant hardening** — substantially already true: §10 Q7 found
  the chat reads the same payload code path as the UI, which is the property
  the plan asks for. What is left is citing record IDs in answers.
- **P0 #7 legal text** — privacy policy, DPA, Terms placeholders. Owner's to
  author; see below.
- P1: evidence bundles, `/v1/metrics`, `/v1/fixes`, daily facts.

## Not for Claude to author

Prices, `[COMPANY LEGAL NAME]`, `[JURISDICTION]`, binding privacy/DPA text,
and the `guarder` vs `Guardian` brand decision are the owner's. Pages get
scaffolded with visible placeholders; guessing them is worse than leaving them.


## Correction log

Kept because the plan's author asked to be corrected, and because a wrong
row here is worse than a missing one.

- **The headline score is the sensor's, not the hub's.** An early read of
  this audit placed it at `payloads/security.py:427`. That function scores
  the per-site/host *scorecards*. The number on the Overview ring comes from
  `sensor/guardian_sensor/alerts.py:597` and is frozen into `Scan.raw` at
  sweep time. This changes item #10 from "build frozen history" to "stamp the
  history that already exists", and moves the work to the sensor.
- **The real coverage bug was more specific than the plan guessed.** §2.5C
  says "if a check couldn't run, it must not quietly count as compliant".
  In this codebase that is concretely: `security_score` derives its
  Vulnerabilities, Configuration and Patch-status deductions from
  `[h for h in res["ssh"] if h.get("reachable")]`, so an unreachable host
  removes its own deductions and **raises** the score. A host marked
  `optional` raises no availability deduction either, so for that host the
  blindness was invisible from every angle.
