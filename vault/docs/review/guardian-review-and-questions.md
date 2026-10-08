# Guardian review and improvement questions

## Summary

Guardian already combines network monitoring, infrastructure checks, controlled repairs, conversational assistance, and reporting. Its strongest direction is explaining what happened, what evidence supports it, and what action can actually be taken. The next investment should make those answers consistent across the app, then make them faster to read and act on.

The product should support three audiences: its owner managing personal infrastructure, nontechnical customers, and technicians managing multiple customers. Improvements should cover accuracy, daily workflow, visual design, and mobile use. Jobs should automatically select a suitable sensor for the target network. These are confirmed preferences; the answers suggested later in this document are recommendations awaiting a decision.

### Recommended order

1. Correct job scheduling, sensor assignment, online-state aggregation, and error reporting.
2. Give every status a consistent meaning, scope, and evidence timestamp.
3. Simplify the existing dense console through better hierarchy, readable summaries, and audience-specific defaults.
4. Make demo data, reports, and public promises agree with the product.

No application behavior was changed as part of this review.

## Version and evidence boundaries

The supplied Guardian checkout is on `phase7-actfix`, commit `015d26a`. The newer work is on `phase5-closeout`, in the `Guardian-main` worktree. The principal source review used `1e4c605`, followed by a review of both subsequent commits through `a291ebc`. Findings below refer to that newer revision unless explicitly described as a live observation.

This distinction matters: newer work already repairs issues that appear in the supplied older checkout. In particular, retry authorization, queued-job visibility, background queue polling, stale-running recovery, trust persistence, recommendation clearing, and several chat behaviors must not be presented as missing features.

The file inventory covers all 567 tracked files at `1e4c605`, plus the new recommendation-handoff test at `a291ebc`: 568 files in total. The initial inventory contains 144,839 text lines, including tests, plans, generated or dependency metadata, and translated pages. All files were indexed; selected implementation paths were read in detail. This is a broad source and product review, **not a claim that every line of every file received an exhaustive manual audit**. The companion file map records the inventory and structural outlines.

Live observations came from the public website and the signed-in Northwind Dental demo organization. The WEME organization was not audited. The deployed build was not independently matched to a commit. Consequently, demo discrepancies are not evidence of failures in the real WEME estate, and source corrections do not prove that old demo records have been regenerated.

Evidence labels used below:

- **Reproduced:** a small, isolated check of the actual source function produced the described result.
- **Code path:** the behavior follows from connected source paths; it was not triggered against production.
- **Observed:** visible in the loaded demo interface.
- **Design proposal:** an optional product choice, not a proven defect.

## What Guardian is and how it works

| Part | Existing responsibility | Implication for improvements |
|---|---|---|
| Sensor | Discover local devices; inspect configured servers, sites, DNS, Wi-Fi, and recorders; execute permitted repairs | Evidence belongs to a particular sensor, network, target, and collection time |
| Hub | Receive observations, preserve decisions, manage organizations and roles, queue work, deliver notifications, build reports | The hub should reconcile state consistently before presenting it |
| Web app | Overview, Inbox, Assets, Topology, History, Activity, Security, Updates, DNS, Integrations, Reports, configuration | Different views should explain the same underlying facts |
| Chat and Slack | Answer questions and invoke concrete tools with permission tiers and explicit confirmation for disruptive actions | A conversational claim must map to a proposal, job, or verified outcome |
| Public site | Explain the service, plans, capabilities, demo, and manual onboarding | Promises and examples should match current capability and configuration |

The implementation uses a Python sensor, a FastAPI/SQLAlchemy hub, and a React/TypeScript frontend with its own design system. Multi-network support is already present. Networks, devices, recorders, and tenant roles are established concepts; a redesign should build on them. Reports already have presets, live preview, schedules, and expiring share links. Billing implementation also exists even though checkout is not enabled as a self-service launch flow. [1][2][3]

## Improvements already present

### Monitoring and decisions

Trust and related device decisions now survive subsequent sensor updates. Network-prefixed alert keys and sensor edits have received compatibility fixes. Recommendations that are no longer emitted can clear. Evil-twin detection now requires stronger evidence than a changed Wi-Fi channel alone. Missing observations are treated more carefully in Security instead of inventing passing checks. Device familiarity combines historical presence and other evidence, while recorders expose cameras that an ordinary LAN sweep cannot see. [2]

### Repairs and conversation

The app distinguishes read-only, reversible, and disruptive tools. Bulk trust requires an explicit proposal. First-time host repairs and disruptive actions have additional confirmation rules. Chat has safeguards against claiming an action without tool execution and against asking for an empty confirmation. Recent history is bounded. Slack supports two-way interaction, channel replies, and contextual “fix it”; the latest change excludes informational notices from the recent actionable-alert list and keeps the last five warning/critical items. “Ask Guardian” on recommendation rows now resolves the relevant context instead of simply failing to find an alert. [2]

### Operations and interface

The sensor polls queued jobs independently of the long scan cycle. Activity includes unattended repairs, explains failures, offers appropriate retries, and surfaces long waits. Retry authorization now respects the role needed for the original action. Stale running jobs have a recovery path. The design has moved toward compact tables, a blue console, a flatter navigation structure, an SVG topology map, failure-focused Security, time-focused Updates, and more distinct Inbox views. Score changes now have an explanation. Integrations distinguish configuration states and collapse unconfigured details. [2][4]

These changes should be preserved. The recommendations below extend them rather than restarting the product design.

## Findings that deserve implementation attention

### F1 — Assign and claim jobs for the right sensor

**Code path · high priority.** The queue endpoint selects every queued row for a tenant and marks them running. It does not select work by sensor identity, target network, or capability. The query and state transition are separate operations, so concurrent collection also deserves a race test. This is especially important now that automatic selection of a suitable sensor is the chosen behavior. [5]

Recommended behavior: resolve the target network, select an eligible fresh sensor with the necessary access, and claim the job atomically. Record the chosen sensor and the reason. If no suitable sensor is available, show “Waiting for a sensor that can reach Lab” instead of allowing another sensor to collect unsuitable work. A fallback sensor should only receive work it is capable of doing. This review did not reproduce a concurrent double execution.

### F2 — Future reboot plans can fail before their scheduled window

**Code path, with helper check · high priority.** The reboot-plan route queues the supplied start/end window immediately. The queue endpoint collects it without checking the window. The sensor tests whether the current time is inside the window and reports failure when it is not. Thus a normally collected future plan can fail as “outside window” before its scheduled time. [5][6]

Recommended behavior: keep the job in a scheduled state until the window opens; distinguish expired windows from execution failure. Validate the interval and display its timezone. The isolated helper check correctly returned false before the window; the problem is how the surrounding queue uses it.

### F3 — Online-state calculation omits secondary-network observations

**Reproduced · high priority.** `_live_macs` reads the primary LAN map and successful recorder observations, but does not traverse `raw.networks`. A synthetic observation containing one primary-network MAC and one secondary-network MAC returned only the primary MAC. The helper is shared across asset-related views. MAC-only identity also needs care because the same MAC may occur on more than one network. [7]

Recommended behavior: calculate presence by organization, network, device identity, and observation source. Reconcile overlapping observations and report their age. Use the resulting facts in Overview, Assets, Topology, Security, and Reports.

### F4 — Network-prefixed LAN alerts can miss score deductions

**Reproduced · high priority.** The security-score exposure calculation tests `lan:port:` directly, while multi-network alerts can use `lan:<network>:port:`. With otherwise identical minimal input, a legacy key produced score 98 and a two-point exposure deduction; a network-prefixed key produced score 100 and no deduction. The neighboring camera calculation already normalizes LAN keys. [8]

Recommended behavior: normalize keys consistently before scoring. Add an equivalence check proving that moving an identical finding to another network does not silently remove its deduction.

### F5 — Slack can fail while explaining a tool error

**Reproduced · high priority.** Slack's reply formatter slices `s.result` as text, but the tool-step schema defines it as a dictionary. A nonempty error result produced `TypeError: unhashable type: 'slice'`. Formatting happens after the handler's engine exception block, so an otherwise useful failure response can be lost. [9]

Recommended behavior: format a safe human-readable error field, retain a correlation/job reference, and protect the final rendering/posting path. This was reproduced locally with an isolated formatter call; no Slack message was sent.

### F6 — One fresh sensor can hide another silent sensor

**Code path · high priority for multi-sensor use.** The watchdog checks the newest scan for the tenant and maintains one `sensor:silent` alert. A fresh scan from one sensor can therefore satisfy the tenant-level check while another stops reporting. The overview's active-sensor count also uses enrollment status rather than solely current reporting freshness. [10]

Recommended behavior: evaluate silence per expected sensor, then summarize network coverage. Preserve a tenant-wide summary but make partial coverage explicit. Existing hub-owned notification delivery is an intentional architecture choice; this recommendation does not imply that remote sensor-side notifications should be reintroduced by default.

### F7 — Queue wait and execution time are conflated

**Code path · medium/high priority.** Activity duration starts at enqueue time. The stale-running recovery path also falls back to that time unless a progress timestamp exists. A job queued a long time ago can look like a long-running job immediately after collection. [5][11]

Recommended behavior: record queued, scheduled, claimed, started, heartbeat, and finished timestamps separately. Show waiting and running time separately. Recover abandoned work from a claim/heartbeat timeout, and reconcile late results instead of silently treating all elapsed time as execution.

### F8 — New manual scan jobs are incompletely described in Activity

**Code path · medium priority.** Manual vulnerability scans use `/api/vuln-scan`; deep dives use another new path. Activity's explicit labels, timing rules, role mappings, and target extraction still center on four older paths. Generic fallback text can appear, and target extraction only reads `host`, while manual scans carry `target`. [11][12]

Recommended behavior: register each job type in one shared catalog containing its label, target field, eligibility, expected duration, result view, and retry rules. Preserve the existing strict authorization fallback.

### F9 — “Last 24 hours” is not enforced by the Overview timeline

**Observed and code path · medium priority.** The loaded Overview showed events from 25 hours, 36 hours, and several days ago beneath “last 24 hours.” Its timeline builder caps rows from each source but applies no 24-hour cutoff. [13]

Recommended behavior: apply an exact time window and offer “View older history.” Use the same time boundary for the headline change count and the timeline, or explicitly explain their different event definitions.

### F10 — Device familiarity needs network and local-time semantics

**Code path · medium priority.** Familiarity receives a network argument but selects the device by tenant/MAC without applying that network. DNS evidence can match the device's current IP across the tenant. Presence timestamps are normalized to UTC, and “midnight to 6am” uses those UTC hours. This can mix overlapping private addresses and describe local evening sightings as overnight evidence. [14]

Recommended behavior: select the exact network/device record; attach cross-network evidence explicitly; avoid assigning historical IP traffic to a device without appropriate identity/time constraints. Use a configured local timezone and home/business context. Treat generic shared names as weak evidence, never enough to grant trust automatically.

### F11 — Triage language claims more evidence than it has

**Observed and code path · medium priority.** “Every scan since Wednesday” is derived from a count greater than one; some callers pass the number of grouped alerts, not the number of scans. Two grouped findings do not establish continuous detection. The displayed confidence percentages are fixed weights by finding type, not demonstrated calibrated probabilities. [8][15]

Recommended behavior: say “First seen Wednesday; three related findings” unless continuity is actually measured. Consider “Evidence strength: high” with named evidence instead of “90% sure.” Preserve ranking explanations, but decide whether a large warning group should ever outrank a critical singleton.

### F12 — Slack pause, identity, and concurrent confirmations need explicit semantics

**Code path / product decision · medium/high priority.** Incoming channel resolution does not check the integration's enabled flag. An empty allowed-user list grants the configured tenant-admin role to channel participants. Conversation and pending-proposal state are held in memory by channel. These choices can be acceptable for a private owner channel, but require clearer behavior for customer and technician teams. [9]

Recommended behavior: retain channel replies, which are an established preference. Define whether “paused” also pauses incoming commands. Bind confirmations to the exact proposal and an authorized person; preserve the real Slack actor in the audit record. Serialize conflicting actions and make restart/expiry behavior visible. This is a design and code-path concern, not a claim that an unauthorized production action occurred.

## Design and demo findings

### D1 — Establish one meaning for every headline number

The demo Overview showed 29 devices online in its top summary, while its conversational Wi-Fi answer showed 20 of 35 online. Network chips also totaled 29 online. Reports showed 13 devices on Wi-Fi and 35 known. Some differences may reflect valid scopes, but the labels do not explain them. The report also called three networks monitored while noting that Lab needs a sensor. [16]

Use precise labels such as “29 online across 3 networks,” “13 visible in the primary LAN sweep,” and “2 networks covered; 1 waiting for a sensor,” only when those statements match the underlying data. Prefer one shared summary with detail rather than making readers reconcile counts themselves.

### D2 — Distinguish observation age from interface activity

The top strip updated every few seconds while the scan was several minutes old. Some security checks were marked verified several days earlier. Fresh animation, a live connection, a recent event, and a fresh check answer different questions. The demo combines generated activity with older detailed records. [16]

Show “Live connection” separately from “Last checked.” Use stale, not configured, not applicable, and unavailable states where appropriate. A no-sensor network should not show reassuring green checks simply because no failure has been reported. Loading and request failure should not temporarily look like an empty, healthy, or uninstalled system.

### D3 — Preserve the dense console, improve the reading hierarchy

The current table-based direction supports technicians well. Its main weaknesses are repeated information, lengthy technical sentences in list rows, and weak distinction between “understand,” “decide,” and “inspect.” Inbox displays a ranked summary and then much of the same information again in the full list. [16]

Keep a compact mode, but make the first level a short title, target, consequence, age, and one clear action. Put detailed commands, raw evidence, and secondary actions in the expanded view. Owner/customer/technician defaults can change emphasis without creating three independent products.

### D4 — Topology needs readable scale behavior

At the inspected desktop size, the initial map was cropped and many names were truncated. “Fit to screen” fitted the entire graph but made labels too small to read, with substantial unused vertical space. The map already has keyboard controls, grouping, and network filters; those are useful foundations. [16]

Use network-level summaries at wide zoom, expand one network at readable scale, and make fit-to-selection a first-class action. Show fewer labels at low zoom rather than shrinking every label. Keep details available by focus/click as well as pointer hover. Clearly separate observed relationships from inferred grouping; an animated relationship should not imply measured traffic unless that evidence exists.

### D5 — Security and Updates need plainer, consistent conclusions

The demo contains rows such as `CSP: False`, repeated check titles without a visible target in the compact text, and “nothing pending” beside substantial fixable-CVE counts. It also shows old `(fixed)` package/urgency values, although the newer source includes CVE parsing improvements. Those old rows should be investigated as deployment/data-refresh issues before opening a duplicate parser task. [2][16]

Use “Content Security Policy is missing,” include the affected target, and explain why no installable package is currently listed when a vulnerability count remains. Avoid implying that every kind of patch requires a full host reboot. Show the installed version, running version, and verification outcome where those distinctions matter.

### D6 — Demo consistency is part of product credibility

The demo DNS total was 55 queries over 24 hours while individual busiest-device rows exceeded 150. The live stream also generated well-known domains with alarming blocklist labels. These should be treated as synthetic examples, not claims about those domains. Security, camera status, report totals, and automated-repair activity also drew on visibly different snapshots. [16]

Build demo scenarios from coherent events and regenerate their dependent summaries together. Keep a persistent “Demo — simulated activity” label. A scenario such as “camera loses contact, incident opens, diagnosis explains it, repair completes, check verifies recovery” would demonstrate Guardian better than unrelated random motion.

### D7 — Reporting should preserve scope and delivery truth

Reports already have extensive presets, including both Executive summary and Executive report. Their distinction could be made clearer. The report-share API sets `pushed` when background delivery is handed off; that does not establish that a destination received the report. Scheduling has an hour selector without an explicit timezone choice in that editor. [3][17]

Use labels such as “Executive brief” and “Detailed report.” Include covered networks, unobserved assets, evidence age, and period boundaries. Show report built, delivery queued, sent, or failed as separate states. Support clear expiry/revocation decisions without asking users to understand token internals.

### D8 — Public wording should match available actions

The public site mixes “billing does not exist yet” with wording that checkout is built but not switched on. It also uses broad repair and screen-time language. The source supports measured network presence, not proof of foreground screen use. [18]

Use one truthful commercial statement across the site: manual setup, current trial terms, and whether payment collection is enabled. Tie automatic-repair claims to supported actions and configuration requirements. Label network-presence estimates as estimates. This is a capability/copy alignment recommendation, not a request to enable billing.

## Q&A decision list

**How to answer:** reply with question numbers and your preferences. “Suggested answer” is a recommendation, not an assumed approval. The first three decisions are already settled.

### Direction and audience

**Q1. Who should the next round serve?**  
**A — Confirmed:** the owner, nontechnical customers, and technicians managing multiple customers.

**Q2. Which dimensions should improve?**  
**A — Confirmed:** logic and clarity, workflow speed, visual polish, and mobile usability.

**Q3. How should multi-sensor jobs be assigned?**  
**A — Confirmed:** automatically select a suitable sensor for the target network.

**Q4. Should each audience start with a different default view?**  
**Suggested answer:** yes—customer decisions, owner health/actions, and technician queues/fleet status—with a visible mode switch and shared underlying data.

**Q5. Should layout preferences follow the person or the organization?**  
**Suggested answer:** personal density and saved views; organization-wide monitoring and repair policies.

**Q6. What should someone understand within ten seconds of opening Guardian?**  
**Suggested answer:** whether coverage is working, what needs attention, and what Guardian has already verified as handled. Keep the score as supporting context.

### Sensor coverage and trustworthy status

**Q7. If no suitable sensor can reach a target, should the job wait or fail immediately?**  
**Suggested answer:** wait with a plain explanation, an expiry time, and a notification if the wait exceeds the expected threshold. Relates to F1.

**Q8. Should another qualified sensor take over abandoned work?**  
**Suggested answer:** automatically for read-only checks; reconcile the previous attempt before retrying a repair that might already have run. Relates to F1/F7.

**Q9. Should a single silent sensor make the whole organization look offline?**  
**Suggested answer:** show partial coverage and name the affected networks and assets; reserve fully offline for loss of all relevant coverage. Relates to F6.

**Q10. What should “online” mean for sleeping phones, cameras, and servers?**  
**Suggested answer:** use device-appropriate states, with a visible last observation. A camera that should always answer needs different treatment from a sleeping phone. Relates to F3/D1.

**Q11. When a check is old, should the last passing result stay green?**  
**Suggested answer:** retain the historical result but label current status stale/unknown once its expected freshness expires. Relates to D2.

**Q12. Should every summary expose its network and time scope?**  
**Suggested answer:** yes, in a short subtitle or detail popover; never require comparing pages to infer the scope. Relates to D1.

**Q13. Should Guardian have an explicit timezone for each site/network?**  
**Suggested answer:** yes, inherited from the organization with per-site overrides; use it for maintenance, reporting, and familiarity. Relates to F10/D7.

**Q14. If data loading fails, should the app keep the last successful values?**  
**Suggested answer:** yes, clearly marked with age and a retry action. First load should show loading or an error, not invented zeros. Relates to D2.

### Jobs, scheduling, and verification

**Q15. Should a reboot be scheduled for an exact time or an allowed window?**  
**Suggested answer:** support both; default to a window with an explicit timezone and a clear missed-window policy. Relates to F2.

**Q16. What should happen if a reboot window is missed?**  
**Suggested answer:** mark it missed and request a new time; do not reboot unexpectedly after the window closes.

**Q17. When should a repair count as successful?**  
**Suggested answer:** distinguish “command completed” from “recovery verified”; use the latter for “Guardian handled it” whenever a follow-up check is available.

**Q18. Should Activity show waiting and execution time separately?**  
**Suggested answer:** yes, alongside the selected sensor, target, progress, and last heartbeat. Relates to F7.

**Q19. What should happen when someone clicks Fix twice?**  
**Suggested answer:** reveal the existing matching active job, and require a deliberate separate action to create another attempt.

**Q20. Should users be able to cancel work?**  
**Suggested answer:** cancel scheduled/waiting work; for running work, show whether safe interruption is supported rather than promising cancellation universally.

**Q21. Should every scan, repair, chat action, and automatic action appear in Activity?**  
**Suggested answer:** yes, with consistent labels and links to full results; maintain the existing protection against duplicating queued repairs in the automatic-fix log. Relates to F8.

**Q22. Should work history connect the initial alert, proposal, job, retry, and verification?**  
**Suggested answer:** yes, as one expandable story with separately identifiable attempts.

### Inbox and alert quality

**Q23. Should critical findings always rank above warnings?**  
**Suggested answer:** make verified critical findings the first tier, then rank within that tier by impact and evidence; avoid group size alone overriding it. Relates to F11.

**Q24. Should a recommendation and its underlying alert occupy separate top-level decision rows?**  
**Suggested answer:** show one decision with the supporting alert and recommendation nested underneath. Preserve both records for inspection. Relates to D3.

**Q25. Should “90% sure” become evidence-strength wording?**  
**Suggested answer:** yes, unless percentages are calibrated; name the observations and uncertainty. Relates to F11.

**Q26. Should repeated findings say “every scan” only when continuity is measured?**  
**Suggested answer:** yes; otherwise use first seen, last seen, and related-finding counts. Relates to F11.

**Q27. What should Resolve mean while the underlying problem still exists?**  
**Suggested answer:** keep the existing recurrence explanation; distinguish fixed, accepted risk, acknowledged, and temporarily snoozed.

**Q28. Should snoozes end only by time, or also by changed severity/evidence?**  
**Suggested answer:** allow both, with escalation on materially worse evidence so a minor accepted issue does not silently cover a new serious one.

**Q29. When a router fails, should downstream alerts generate separate notifications?**  
**Suggested answer:** group the incident and suppress redundant notifications while retaining affected-device evidence. Guardian already groups causes; extend that behavior only where dependencies are supported. [19]

**Q30. Should business-critical assets have different alert urgency?**  
**Suggested answer:** allow importance and expected availability per asset; a payment service, backup recorder, and visitor's phone should not carry identical consequences.

### Chat and Slack

**Q31. Should a Slack “yes” be tied to the person who requested the proposal?**  
**Suggested answer:** yes by default; optionally allow a named authorized approver, with the actual approver recorded. Keep replies in the channel. Relates to F12.

**Q32. Should pausing Slack also stop incoming commands?**  
**Suggested answer:** expose separate “send notifications” and “accept commands” controls if both behaviors are useful; avoid an ambiguous single paused label. Relates to F12.

**Q33. Should Slack users have individual Guardian roles?**  
**Suggested answer:** yes for customer/technician teams; allow a clearly labeled private owner-channel shortcut. Relates to F12.

**Q34. If several actionable alerts were posted, should “fix it” act on all of them?**  
**Suggested answer:** first show the exact scope unless context names one target; use the current recent-warning/critical filtering and preserve confirmation rules.

**Q35. What should happen to an unfinished proposal after a server restart?**  
**Suggested answer:** retain an auditable proposal with an expiry, then revalidate it before execution; otherwise explicitly explain that it expired.

**Q36. How much technical output should chat show by default?**  
**Suggested answer:** outcome, target, evidence, and next action; expandable command/output details for technicians. Relates to D3.

### Devices, topology, and DNS

**Q37. Should familiarity ever automatically trust a device?**  
**Suggested answer:** no; use it to make confirmation easier. Shared names, randomized addresses, and recurring presence are evidence, not identity proof. Relates to F10.

**Q38. Should familiarity differ for homes and businesses?**  
**Suggested answer:** yes; office-hours recurrence and always-on equipment are more useful than “sleeps here” for business networks.

**Q39. Should network presence be called screen time?**  
**Suggested answer:** use “estimated network presence,” with a short explanation of what is and is not measured. Relates to D8.

**Q40. What should Topology show when the whole organization cannot fit readably?**  
**Suggested answer:** readable network summaries first, with click-to-expand and fit-to-selection. Relates to D4.

**Q41. Should inferred connections look different from observed connections?**  
**Suggested answer:** yes, with a legend and evidence in the detail view; animate only relationships supported by suitable recent observations.

**Q42. Should a missing recorder make its cameras “offline” or “unknown”?**  
**Suggested answer:** distinguish recorder unreachable from camera confirmed offline. Show expected, discovered, answering, and unobserved camera counts separately.

**Q43. Should DNS summaries all use the same source/window?**  
**Suggested answer:** yes where they claim comparable totals; otherwise label sensor snapshot versus recorded history and explain coverage. Relates to D6.

**Q44. How should an incorrectly blocked domain be handled?**  
**Suggested answer:** show the matching rule/list and offer a scoped exception with a clear expiry option and verification; keep it distinct from disabling filtering globally.

### Security and updates

**Q45. Should Security prioritize failing and unknown checks before passing checks?**  
**Suggested answer:** yes; keep the existing failure-first direction and collapse passing detail with clear target counts. Relates to D5.

**Q46. Should stale evidence reduce certainty without silently changing the historical score?**  
**Suggested answer:** yes; show score and coverage separately so missing checks cannot look like an improvement.

**Q47. When CVEs remain but no packages are pending, what should the app explain?**  
**Suggested answer:** distinguish stale scan results, repository/package availability, excluded packages, and reboot requirements when the evidence supports each explanation; never guess. Relates to D5.

**Q48. Should a manual vulnerability scan offer a before/after comparison?**  
**Suggested answer:** yes, using comparable targets and scan profiles, with new, unchanged, resolved, and unverified findings.

**Q49. Should automatic repair policies be global or per target/action?**  
**Suggested answer:** organization defaults with per-target/action overrides, clearly showing effective policy and inherited settings.

**Q50. Should accepting a security exception require an expiry and a reason?**  
**Suggested answer:** a short reason plus optional expiry by default; make stricter requirements configurable for technician-managed customers.

### Layout, mobile, and accessibility

**Q51. Should dense tables have a comfortable mode?**  
**Suggested answer:** yes; remember the person's choice, while preserving compact defaults for technicians. Relates to D3.

**Q52. Which action should come first on mobile?**  
**Suggested answer:** current decisions and incident details, followed by key health; move long histories and broad tables below.

**Q53. Should narrow tables become cards, or scroll horizontally?**  
**Suggested answer:** use compact summary rows/cards for common decisions; keep horizontal scrolling for genuine comparisons, with the identifying column visible.

**Q54. Should the top response-time strip always occupy space?**  
**Suggested answer:** make it optional and keep site names readable; hide decorative motion before sacrificing labels. Relates to D4.

**Q55. Should status changes announce themselves to keyboard/screen-reader users?**  
**Suggested answer:** announce meaningful completion/errors politely, without stealing focus or announcing every stream event. W3C's status-message technique provides an implementation reference. [20]

**Q56. What accessibility baseline should guide the design pass?**  
**Suggested answer:** use WCAG 2.2 AA as the target and verify contrast, focus visibility, zoom/reflow, target size, and non-color status cues. This review is not a conformance certification. [21]

### Reports, demo, and commercial experience

**Q57. Should reports default differently for customers and technicians?**  
**Suggested answer:** a brief with decisions/outcomes for customers and an evidence-rich operations report for technicians; use the existing preset system. Relates to D7.

**Q58. Should report delivery display actual destination outcomes?**  
**Suggested answer:** yes—queued, sent, failed, suppressed—separate from successfully building or sharing the report. Relates to D7.

**Q59. Should the demo use coherent guided scenarios instead of unrelated generated events?**  
**Suggested answer:** yes, with a persistent demo label and internally consistent counts, timestamps, alerts, and repairs. Relates to D6.

**Q60. What should the public site promise before self-service checkout launches?**  
**Suggested answer:** manual setup with clearly stated trial/pricing terms, a supported-action capability table, and one consistent billing statement. Relates to D8.

## Suggested delivery sequence and acceptance checks

### First: correctness and lifecycle

Implement F1–F8 as small, independently reviewable changes. A two-sensor/two-network test should show correct assignment and independent silence detection. A future reboot should remain scheduled until its window. Identical LAN findings should score identically regardless of network prefix. Slack should render a structured tool error. Activity should show the correct target and waiting/running time for every job type.

### Second: shared facts and honest language

Resolve F9–F12 and D1–D2. Compare the same network/time scope across Overview, Assets, Topology, Security, and Reports. Test absent sensors, stale checks, loading, failed requests, overlapping private addresses, and timezone boundaries. Confirm that grouped-alert wording never implies continuous observation without evidence.

### Third: audience-specific reading and navigation

Refine Inbox hierarchy, readable topology scale, Security target labels, and mobile priorities. Keep the existing compact console and advanced detail. Evaluate owner, customer, and technician tasks separately: understanding an incident, approving a repair, locating a device, and checking a job outcome.

### Fourth: demo and public consistency

Regenerate demo scenarios and dependent summaries together. Check that reports and marketing use the same capability language. Keep billing activation outside this work unless separately requested.

## Sources

Private source references below identify the reviewed revision. Local file links point to the main worktree and may change after `a291ebc`; the revision is the historical reference. Live observations are from September 14–15, 2026, in the Northwind Dental demo.

1. Guardian, [design specification](</Users/mac/Documents/Code/AI Projects/Guardian-main/docs/superpowers/specs/2026-09-09-guardian-design.md>), September 9, 2026. Product architecture and intended roles.
2. Guardian git history through `a291ebc`, especially `1e4c605`, `b929440`, `2f44547`, `d6f4d37`, `fc99dc5`, `a74f633`, `7820e1a`, `49d7cae`, `35f4ec3`, and `a291ebc`. Private repository, implementation changes and regression tests.
3. Guardian, [report routes](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/routers/reports.py>), [report presets](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/reports/presets.py>), and [billing routes](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/routers/billing.py>), reviewed revision `a291ebc`.
4. Guardian, [integrations redesign report](</Users/mac/Documents/Code/AI Projects/Guardian-main/.superpowers/sdd/2026-09-09-guardian-phase2-4-dashboard/integrations-redesign-report.md>). Intermediate implementation record; current source supersedes stale details.
5. Guardian, [sensor queue and recovery](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/routers/sensor.py:166>), reviewed revision `a291ebc`.
6. Guardian, [reboot-plan route](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/routers/write.py:606>) and [sensor window/execution logic](</Users/mac/Documents/Code/AI Projects/Guardian-main/sensor/guardian_sensor/runner.py:159>), reviewed revision `a291ebc`.
7. Guardian, [shared online-state helper](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/payloads/_util.py:35>) and [Assets payload](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/payloads/assets.py>), reviewed revision `a291ebc`.
8. Guardian, [confidence mapping and security score](</Users/mac/Documents/Code/AI Projects/Guardian-main/sensor/guardian_sensor/alerts.py:585>), reviewed revision `a291ebc`.
9. Guardian, [Slack channel resolution and formatting](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/routers/slackbot.py:145>) and [tool-step schema](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/schemas_dash.py:1800>), reviewed revision `a291ebc`.
10. Guardian, [watchdog](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/scheduler.py:378>) and [Guardian health payload](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/payloads/overview.py:521>), reviewed revision `a291ebc`.
11. Guardian, [Activity job mappings and lifecycle](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/payloads/jobs.py>), reviewed revision `a291ebc`.
12. Guardian, [manual scan route](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/routers/vulnscan.py>), reviewed revision `a291ebc`.
13. Guardian, [Overview timeline](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/payloads/overview.py:184>) and [live Overview](https://my.guarder.io/overview).
14. Guardian, [device familiarity](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/payloads/familiarity.py>) and [timestamp parsing](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/scans.py:23>), reviewed revision `a291ebc`.
15. Guardian, [triage ranking and language](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/payloads/triage.py:52>), reviewed revision `a291ebc`.
16. Guardian demo, live [Overview](https://my.guarder.io/overview), [Inbox](https://my.guarder.io/inbox), [Assets](https://my.guarder.io/assets), [Security](https://my.guarder.io/security), [Updates](https://my.guarder.io/updates), [DNS](https://my.guarder.io/dns), [Topology](https://my.guarder.io/topology), and [Reports](https://my.guarder.io/reports). Authenticated demo access; these are UI observations, not WEME measurements.
17. Guardian, [report share behavior](</Users/mac/Documents/Code/AI Projects/Guardian-main/hub/guardian_hub/routers/reports.py:146>) and [schedule editor](</Users/mac/Documents/Code/AI Projects/Guardian-main/web/src/components/ScheduleEditor.tsx>), reviewed revision `a291ebc`.
18. Guardian, [public website](https://guarder.io), observed copy; repository `site/` and presence/reporting implementation supply capability context.
19. Prometheus, [Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/), accessed September 2026. Grouping, routing, inhibition, and notification timing as a reference for Q29; no claim that Guardian must adopt Prometheus.
20. W3C WAI, [ARIA22: Using role=status to present status messages](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22), accessed September 2026. Reference for Q55.
21. W3C, [Web Content Accessibility Guidelines 2.2](https://www.w3.org/TR/WCAG22/), accessed September 2026. Proposed accessibility baseline for Q56.
