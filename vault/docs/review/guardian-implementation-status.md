# Guardian implementation status

Local implementation on `codex/guardian-improvements`, September 15, 2026. The live service has not been deployed or changed. Q1–Q5 retain the user's answers; Q6–Q60 use the accepted recommendations. This ledger describes the implemented default behavior and its limits, rather than treating a product decision as a test result.

## Decision coverage

| Decision | Implemented behavior |
|---|---|
| Q1–Q2 | Changes span customer, owner and technician views; operational logic, clarity, workflow, appearance and mobile layout. |
| Q3 | Jobs choose an active, recent sensor with target/network access; recent failure evidence overrides a network binding. Ambiguous addresses require network scope. |
| Q4 | Personal audience selector and remembered landing page: decisions, overview or Activity. Existing organization switching remains available. |
| Q5 | Per-user density, theme, ticker and saved local views, separate from organization policy. |
| Q6 | Coverage, current decisions and verified recoveries are prominent; historical scores remain supporting evidence. |
| Q7 | Waiting explanation, expiry and delayed notification; the scheduler advances deadlines even during a total sensor outage. |
| Q8 | One automatic replacement attempt for abandoned read-only work. Repairs require later target evidence or explicit review before another attempt. |
| Q9 | Coverage panel names affected networks and separates fresh sensors from the total estate. |
| Q10 | Device presence remains network-specific; sleeping devices differ from always-on equipment; last observations remain visible. |
| Q11 | Old online observations and security checks become unknown/stale without deleting their historical evidence. |
| Q12 | Scope and time captions on the main summaries; selected network context and per-observation timestamps remain visible. |
| Q13 | Organization timezone with per-network inheritance/override. Reporting and reboot presets use the organization zone; familiarity uses the asset's network zone. |
| Q14 | Loading/error states and a last-successful-data notice with Retry on the principal data pages, including Assets. |
| Q15 | Reboot windows plus a chosen time with a five-minute start allowance. Displayed times include timezone information. |
| Q16 | Missed windows become missed jobs; execution after expiry is refused. |
| Q17 | Command completion differs from verified recovery. Supported repairs reconcile against a later, reachable target observation from the assigned sensor. |
| Q18 | Waiting time, execution time, selected sensor, heartbeat, progress and schedule information in Activity. |
| Q19 | Matching active work is returned rather than duplicated; a database uniqueness constraint protects concurrent submissions. |
| Q20 | Waiting/scheduled cancellation; running jobs explicitly state that safe interruption is unavailable. |
| Q21 | Manual vulnerability scans and deep dives join ordinary jobs and automatic fixes in Activity. |
| Q22 | Expandable related attempts, retries, matching alerts, matching proposals and verification. Proposal history is retained for 30 days; token values are never exposed. |
| Q23 | Severity is the first ranking tier; evidence and asset importance rank within it. |
| Q24 | Recommendations matching an underlying alert are nested into its decision rather than counted twice. |
| Q25 | High/moderate/limited evidence wording replaces uncalibrated probability claims. |
| Q26 | First-seen/related-finding wording replaces unmeasured “every scan” continuity claims. |
| Q27 | Existing acknowledgement, snooze, recurrence and accepted-risk distinctions retained; exceptions require reasons. |
| Q28 | Worse alert severity reopens a snoozed or acknowledged finding. Distinct target/evidence keys remain distinct findings. |
| Q29 | Redundant camera notices are suppressed while their explicitly associated recorder is unreadable; child evidence remains stored. Inferred router links do not justify suppression. |
| Q30 | Per-device importance plus existing expected-availability control; importance affects decision ranking. |
| Q31 | Slack proposals belong to the requester. An optional named admin approver can approve one unambiguous pending proposal; the actual Slack actor reaches the audit trail. |
| Q32 | Separate incoming-command control and outgoing-notification enablement. |
| Q33 | Individual Slack roles, with the existing private owner-channel shortcut. |
| Q34 | A broad “fix it” matching several recent alerts asks for the exact target before invoking actions. |
| Q35 | Durable per-person conversation/proposal state; expiry and one-shot authorization are revalidated after restart. |
| Q36 | Concise chat outcomes and explicit errors; Activity technical output is expandable. |
| Q37 | Matching names/private MAC addresses produce a suggestion, never automatic trust inheritance. |
| Q38 | General/home/business context and network-local recurrence descriptions. |
| Q39 | Estimated network presence wording and measurement explanation. |
| Q40 | Multi-network topology starts with readable summaries and explicit expansion/focus. |
| Q41 | Inferred routers are unknown and do not animate; stale device/host/site observations stop animating. |
| Q42 | Unreadable recorder produces unknown cameras, preserving last observations. Discovered, expected and answering counts use distinct wording. |
| Q43 | Overview blocked-DNS total uses recorded history and an exact rolling 24-hour boundary; snapshots retain their separate meaning. |
| Q44 | Exact-domain temporary exceptions, matching rule evidence, expiry and later-query verification; other filtering remains enabled. |
| Q45 | Failure/unknown-first Security presentation with passing detail collapsed. |
| Q46 | Historical scores are preserved while freshness and coverage qualify their meaning. |
| Q47 | CVEs without pending packages explain the observed mismatch and possible repository/age limitations without asserting a cause; reported reboot requirements are explicit. |
| Q48 | Comparable scan targets/profiles show new, unchanged, resolved and unverified findings. Missing findings remain unverified unless the result explicitly certifies complete coverage. |
| Q49 | Organization repair defaults plus per-host/action manual-approval overrides and inheritance. |
| Q50 | Reason required, optional expiry by default, and an admin setting requiring expiry for new accepted risks. |
| Q51 | Personal comfortable/compact density. |
| Q52 | Mobile decisions precede outcome counters; long incident evidence is expandable. |
| Q53 | Compact decision rows; comparison tables scroll with the identifying column retained. |
| Q54 | Personal option to hide the response-time strip; labels remain readable and reduced-motion preferences are respected. |
| Q55 | Meaningful saved/completed/error notices use status/alert announcements; routine streaming updates do not steal focus. |
| Q56 | Focus, target-size, reduced-motion and reflow improvements; light/dark and 320/390px visual checks. This is not a WCAG conformance certification. |
| Q57 | Customer executive brief and technician operations preset, using existing report sections. |
| Q58 | Per-destination queued/sent/failed/suppressed receipts; sent means destination acceptance, not proof someone read the report. Share links can be revoked. |
| Q59 | Persistent demo label and a repeatable blocked-domain/temporary-exception scenario with consistent subsequent simulated queries. |
| Q60 | Manual setup and consistent billing language plus a supported-action capability table on the public page. |

## Practical limits

- No production rollout has been performed. Hub migrations and coordinated sensor updates are required before enabling these behaviors on the live estate.
- Existing jobs have unknown start/assignment values rather than invented historical values. Legacy single-sensor installations have a limited compatibility fallback; multi-sensor routing requires actual target evidence or an explicit network binding.
- Automatic recovery verification only covers checks the sensor reports explicitly. Command completion alone does not prove that every service recovered. Unknown outcomes remain visible for review.
- A chosen reboot time is a five-minute pickup window, not a guarantee of execution at the exact second. A missing eligible sensor produces a missed window.
- Dependency suppression is limited to known recorder/channel relationships. Guardian does not infer a reliable physical router dependency from a drawn topology line.
- Disappeared scan findings cannot be called resolved unless the scan certifies complete comparable coverage. Existing scan results generally do not carry that certification.
- Historical demo charts remain example data. The guided DNS scenario is simulated and labeled as such.
- Accessibility has received targeted checks, not a complete assistive-technology or conformance audit.

## Validation

Completed locally:

- **529 web tests passed** across 52 files; TypeScript checks and the production build passed.
- **103 sensor tests passed** for DNS exceptions, repair policies, network identity and synchronization.
- **24 focused behavior tests passed** for routing, lifecycle, personal settings, tenant boundaries, Slack approval, recorder dependencies, repair reconciliation and camera uncertainty.
- Additional successful hub runs covered Overview/Topology (43 tests), Assets/Security/exception decisions (67), Slack and settings boundaries (46), scheduled updates (8), camera contracts (9), and watcher/integration boundaries (57). These groups overlap; they are not an aggregate total.
- Report generation, delivery receipts, demo retention, queue history and ordinary read/write routes were also exercised. Outdated expectations were updated where the accepted behavior intentionally changed.
- Final migration upgrade/downgrade/re-upgrade preserved a legacy queued job and checked the new tenant indexes. No historical execution time was invented.
- Browser checks: light/dark views, 390px and 320px reflow, no page-wide overflow at 320px, desktop topology summaries, persistent personal view, and no browser errors in inspected pages. The local DNS scenario was verified end to end: an exception produced a later “Allowed query observed” result, then the exception was removed. Sampled light-theme text contrast on the changed decision/demo panels ranged from 5.42:1 to 15.86:1.

The local preview runs at `http://127.0.0.1:8801/` against an isolated demo database. Its normal integration scheduler is disabled; only the labeled DNS simulation advances. Live sensors, external destination delivery, PostgreSQL migration execution and full accessibility conformance were not tested.

Build note: Vite still reports an application chunk above its 500 kB advisory threshold. The build succeeds.
