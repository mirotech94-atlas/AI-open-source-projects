# White-Label MSP Reporting & Business Value — audit and sequencing

Source: `Guardian — White-Label MSP Reporting & Business Value Spec.pdf`
(2026-09-20, 18 pages).

## The thesis, which is right

> Guardian's monitoring is replaceable. What is not replaceable is the pair
> underneath: it performs allow-listed repairs, and it keeps a record of
> every one. Sell the record, not the dashboard.

That matches where the codebase has been heading. The trust-plan work
(`2026-09-17-trust-plan-audit-design.md`) spent itself on making individual
numbers defensible; this spec is the same instinct one layer up — making the
*document* defensible, and then selling it.

## What already exists

Checked rather than assumed, because on the previous spec inference
underestimated this codebase four times running. It did so again here.

| Spec asks for | State |
|---|---|
| "Reports are immutable once generated. Never re-derive a past report from live tables." Called **the single most important design decision in the spec** | **Already true.** `builder.render_to_file` writes the rendered HTML to disk and `routers/reports.get_report` serves that file. Nothing re-derives |
| "Make revoke actually revoke — check on every request, no caching of the authorisation decision" | **Already true.** `routers/public.shared_report` re-reads the row per request, checks expiry, rate-limits on the token prefix |
| Brand snapshot frozen per report ("May's report keeps May's logo") | **Already true, structurally.** The brand is resolved at render time into frozen HTML, so a later rebrand cannot reach a past report |
| `control` table for the compliance/insurance pack | **Skeleton exists.** `reports/compliance.py` carries twelve controls with predicates over `Scan.raw`. The insurance pack is closer than the spec assumes — what is missing is the per-framework mapping and the three-state `answerable` |
| Share links with expiry | Exists (`Report.share_token`, `share_expires`, `mint_share_token`) |
| Fleet view across clients | **Exists** as the owner console — `web/src/owner/Fleet.tsx`, `Ledger.tsx`, `payloads/platform.py`. It is the single-Partner case of what the spec describes |
| Uptime evidence kept beyond raw retention | Exists as `daily_fact` (added 2026-09-19). The spec's `uptime_window` is the complementary shape: per-outage rows with a cause, kept forever |

## What is genuinely missing

Ordered by value per unit of risk, not by the spec's own phasing.

1. **`content_hash` in the report footer.** SHA-256 over a canonicalised
   payload, printed where a reader can see it. One line, and it is the
   difference between "trust this PDF" and "here is how to check it".
   Underwriters and auditors notice.
2. **Coverage on page one.** The spec is blunt: a report that hides a
   sensor outage and prints 99.9% for a month the sensor was down "would
   cost you every MSP who noticed". The machinery exists — `daily_fact`
   rows, `learning.watching_since`, `sensor:silent`.
3. **`supersedes_id`.** Corrections create a new report; the old one stays
   reachable and marked superseded, rather than being edited.
4. **Share view log.** `view_count` and a per-view row. The spec is right
   that "your client opened last month's report twice" is worth real money
   to an MSP.
5. **The Partner level.** Genuinely absent, and genuinely large: it touches
   every tenant-scoped query and the isolation story. Not a first move.
6. **PDF output.** HTML only today. Headless Chromium, per the spec.
7. The business-value features — backup verification, EOL, ISP SLA credit,
   offboarding sweep, PSA integration. Each is its own feature with its own
   data.

## Landed

1. `0c64419` — **`content_hash` in the footer.** The interesting part was
   the self-reference: printing a hash of a document into that document
   changes it, so the printed value can never be the hash of the bytes the
   reader holds. The canonical form is stated *in the footer beside the
   digest* — replace the 64-hex value with 64 zeros, SHA-256 the file — so
   it is checkable by someone holding nothing but the file. An auditor who
   can only verify a checksum by asking the issuer has verified nothing. A
   test runs that published recipe.
2. `51a5e9d` — **coverage on page one.** "27 of 30 days — no sensor data 10
   to 12 Sept", rendered on good months too. `Scan` rows are the source
   rather than `daily_fact`: scans are never pruned, so a report about last
   March can answer honestly, while the facts table began this month and
   would say a confident, false "0 of 30" about anything earlier.

Both are small. Both are the parts of this spec that a sceptical reader
actually touches, which is why they went first.

3. `fd326c1` — **a circular import that broke every cold boot**, shipped by
   me two commits earlier and missed by the full 1431-test suite.
   `reports/builder` imported `scheduler._tenant_offset` at module scope
   while `scheduler` imports `builder`, so `import guardian_hub.main` —
   which is how uvicorn starts — raised ImportError.

   The reason it passed is worth keeping: `conftest` imports `db`,
   `models`, `demo_trickle`, `ratelimit` and `public_feed` before `main`,
   so both modules are in `sys.modules` by the time the cycle closes. **A
   test suite's import order is not production's, and no in-process test
   can prove the app boots.** `tests/test_cold_import.py` shells out to a
   fresh interpreter; run against the broken commit it fails three ways.

4. `eeac185` — **all five frameworks mapped.** 34 controls, 109 mappings
   across cyber insurance, Cyber Essentials, SOC 2, HIPAA and PCI DSS v4.0,
   delivered as a report section and `GET /v1/compliance` with a CSV export.

### The mapping's design is its refusals

18 evidenced, 5 partial, 11 outside scope. An outside-scope control carries
**no predicate at all** — not one returning False — so no code path can
report a pass for something Guardian cannot observe. A mutation test proves
it rather than asserting it.

Three refusals are worth naming because each was an over-claim already
present or easy to make:

- **MFA.** `reports/compliance.py` says "Remote admin uses key-based or MFA
  login" and tests `sec.ssh_password`. Guardian evidences key-only SSH; it
  cannot see MFA anywhere, and MFA is the single question most likely to
  move an insurance premium. Split, with the evidenced row disclaiming the
  thing it resembles.
- **Penetration testing.** Automated vulnerability checks are not a pentest
  and PCI Requirement 11.4 asks for the latter.
- **Backup verification and EOL.** Outside scope because Guardian has not
  built them, which is a different statement from "your backups are bad".

The old checklist also had two rows returning `lambda L: True` — passing for
a tenant whose sensor never reported. Both claims are defensible; they now
rest on evidence that the monitoring happened.

### The business-value pack, built

Four of the spec's five supporting features. Two of them moved controls in
the compliance catalogue from outside-scope to partial, which is the
measure that matters — the pack can now answer more without claiming more.

5. `1d317e1` — **backup verification.** Not backup software: a check that
   whatever already runs is still producing plausible artifacts. The size
   check earns it — a dump that now contains only an error message lands on
   schedule and exits zero, so it is invisible to anything asking "did the
   job run?". `backup_verification` → partial; restores are still
   unanswered, because running a customer-supplied restore command would
   break the "fixed allow-list, not a shell" claim.
6. `a3d3468` — **end-of-life tracking.** Dated dataset, and unknown never
   reads as supported. Standard vendor support, not paid extended support:
   quoting an ESM date tells someone they are covered when their machine
   stopped receiving patches three years ago. `asset_lifecycle` → partial.
7. `812a9f3` — **ISP SLA credit evidence.** The sensor now pings the
   gateway alongside the external anchors, which is the one extra target
   that separates "the line is down" from "the fault is in the building".
   Only ISP minutes are claimed and the letter names what it excludes — a
   claim that quietly includes the customer's own outages is rejected and
   remembered. `wan_outage` rows are kept forever, because retention would
   otherwise delete the evidence for a claim filed against a quarter that
   has aged out.
8. `5db5bd8` — **offboarding sweep.** Ownership is typed, never inferred:
   a hostname is a guess and a guess puts the wrong name on a document
   somebody signs.

### The last three, landed after the audit

- **PDF output** (`6c7bf17`). `reports/pdf.py` renders the frozen HTML
  with a headless Chrome on the hub, vendored fonts inlined as data URIs
  so the print engine cannot substitute a face, cached beside the HTML on
  first request and served from `/api/v1/reports/{id}/pdf` and
  `GET /v1/reports/{id}?format=pdf`. A hub without a browser answers 503
  and names `GUARDIAN_CHROME`. The deployment dependency the audit named
  is real and is now a documented 503 rather than an undocumented gap.
  One trap worth knowing: a full Chrome on macOS does not exit after
  printing, so the renderer takes the finished file as its signal and
  terminates the browser itself.

- **The Partner level** (`c975cd6`). Landed smaller than the audit
  feared, because it did not retrofit the tenant boundary -- it reused
  it. A `Partner` is a row above the tenant with a brand and staff; a
  `PartnerMembership` is expanded by `deps.memberships` into an ordinary
  membership on every customer carrying that `partner_id`. No tenant-
  scoped query learned about partners, so none can forget to filter, and
  another partner's customer is invisible to the switcher, 403 to a stale
  session and 404 to the owner API. The brand cascade is per key
  (`branding.resolve`), and the report masthead freezes the resolved
  brand while "Prepared for" stays the customer's own name. Postgres
  row-level security keyed on `tenant.partner_id` can be added under this
  at deployment time without an application change; on SQLite, the
  deployed engine, the membership expansion is the boundary.
  `hub/tests/test_partner.py` holds the cross-partner leak tests.

- **PSA integration** (this commit). `integrations/psa.py` registers
  HaloPSA, Autotask and ConnectWise Manage off one class, per escalated
  finding rather than per incident, because the ticket is what the MSP
  bills and closes with the customer. Guardian -> PSA: one ticket per
  open CRIT (widenable to WARN), stamped on `Alert.ticket_key`; the
  catalogued fix noted on it and, only when the card opts in, booked as a
  time entry; a resolved finding resolves its ticket once. PSA ->
  Guardian: a ticket a technician closes ACKS the finding, never resolves
  it, audited as `psa:<flavour>` through the same `apply_alert_op` a
  dashboard ack uses. The audit's reservation stands in a narrower form:
  the three vendors' request shapes are written to their published REST
  documentation and have **not been exercised against a live tenant**.
  The card says so, and the seams (`_create/_note/_time_entry/_resolve/
  _status`) are where a first live run will adjust field names.

## The Partner level: what the snapshot rule bought

The audit wrote, before partners existed, that branding would resolve
`tenant.brand ?? partner.brand ?? default` and that anything frozen into a
report must be a resolved, self-contained blob rather than a pointer.
That is exactly what let the Partner level land without touching a
report already generated: `_report_identity` now returns the resolved
brand and the HTML freezes it, and `test_partner` renames the partner
after rendering to prove nothing already rendered moves.

## Not for Claude to decide

Pricing shape (per-Site with Partner volume tiers), which frameworks to map
first, and whether white-label sits in a Partner tier or an add-on. The spec
argues each well; they are still the owner's calls.
