# Vault

Vault watches a small network — a dental practice, a home, a branch office —
tells the owner what is wrong in plain language, and where it can, repairs it.

Three pieces:

| Piece | Lives in | What it is |
|---|---|---|
| **Sensor** | `sensor/guardian_sensor` | Runs on-prem. Scans the LAN, filters DNS, runs vulnerability checks, executes repairs over SSH. Pushes everything to the hub. |
| **Hub** | `hub/guardian_hub` | FastAPI + SQLAlchemy. Ingests scans, stores them, shapes them for reading, pushes notifications, serves the API. |
| **Web** | `web/src` | React + Vite + TypeScript dashboard. |

A sensor never reads the database. It talks to the hub over
`routers/sensor.py` only: `POST /ingest` on the way up, `GET /queue` on the way
back down.

## Commands

Each Python package has its **own** virtualenv and must be run from its own
directory:

```bash
cd hub && .venv/bin/pytest          # 2022 tests
cd sensor && .venv/bin/pytest       # 486 tests
cd web && npm test                  # vitest
cd web && npm run lint              # oxlint
cd web && npm run build             # tsc -b && vite build
```

The hub CLI is `guardian` (`hub/guardian_hub/cli.py`): `migrate`, `seed-demo`,
`demo-refresh`, `selftest`, `import-nexus`, `tenant`, `user`, `enrol-code`,
`prune`.

Schema changes need an Alembic revision in `hub/alembic/versions/`
(numbered `NNNN_short_name.py`, currently up to `0029`).

## Tests

**Never assert wall-clock.** A test that measures duration measures the
machine, not the code. `test_assets`' N+1 guard asserted `< 2.0s` per request
and became the suite's only flaky test -- it failed in one full run and passed
in the next with nothing changed, because a second suite on the same laptop
was enough to push a request past the threshold. This host swings that file's
own runtime from 90s to 241s depending on what else is running.

It was also wrong in the direction it was written for: with a real N+1
injected (one query per device, 20 statements to 55) the old assertion still
passed in 0.195s. It could not have caught the regression it existed to catch.

The same mistake hides one layer down, in how fresh the seeded data is.
`tests/scanfixture` shifts the captured scan onto the current time, and that
delta used to be fixed at import -- so an hour into a run the "scan that just
happened" was an hour old, `effective_presence` read every device as UNKNOWN
past its 900-second window, and two tests failed in the full suite while
passing alone. `conftest`'s `client` fixture now calls `scanfixture.refresh()`
per test. A test that passes alone and fails in the full run is worth checking
for elapsed time before assuming test pollution.

Guards about work that scales with rows count statements instead, via
`tests/querycount.py`. The one honest exception is a test of a *time budget*
feature, where elapsed time is the subject -- `test_security`'s `GEO_BUDGET_S`
guard is correctly a clock test.

## Commit messages

Commit subjects here are **a sentence describing the user-visible behavior
change**, in present tense, sentence case. Not a Conventional Commits prefix,
not a summary of which files moved. Recent history is the reference:

```
A fix reported done closes the alert it remedied, live
Slack bot replies in the channel, not in threads
Trust from chat/inbox sticks; unverified alert clears live
Retire safehome: hub is my.guarder.io only
```

Write the subject from the owner's point of view. If you cannot describe the
change as something a user would notice, say what capability it enables
instead — but reach for the behavior first.

## Comments and docstrings

This is the convention that most distinguishes this codebase, and it is
load-bearing. **Any non-obvious invariant gets a docstring or comment that
explains _why_, not what.** Several correctness bugs here are only avoidable
if the next reader knows the reason. Two examples worth reading before writing
new code:

- `demo_trickle._tenant_offset` — a paragraph explaining that `Event.ts` is a
  plain string and the row builders take their "most recent" slice with a SQL
  `ORDER BY ts DESC`, i.e. a *lexicographic* comparison. A row stamped in a
  different UTC offset is chronologically correct and still sorts to the wrong
  place. So trickled rows must match the tenant's own offset.
- `ingest.store_scan` — explains why `deliver=False` exists: a 30-day backfill
  must not replay hundreds of push notifications for alerts that "fired" only
  because the row is new.

Match that density. A one-line `# increment counter` comment is noise here; a
paragraph explaining a trap is the house style.

## The demo tenant

`guardian seed-demo` builds the `demo` tenant, and the rules around it are
strict because the live demo on the VPS is public (fixed sign-in code in
`seed.DEMO_CODE`) and is re-seeded repeatedly.

- **Deterministic.** Seeded RNG, so re-running against the same fixtures
  produces byte-identical timestamps.
- **Idempotent.** Those identical timestamps let `store_scan`'s
  `(tenant, ts, ...)` dedup keys make a second run add zero rows. Do not
  introduce `now()` into a seeded path — it breaks both properties at once.
- **Scrubbed.** The fixtures are a real capture of the owner's own network.
  `demoscrub.scrub` rewrites them into the fictional Northwind Dental at load
  time. Never commit unscrubbed capture data, and never bypass the scrub.
- Live motion comes from `demo_trickle.py`, which is bounded on purpose: the
  same example domain and client recur, and it does not invent unrelated ports
  or successful repairs between refreshes.

Changes that touch ingest, seeding or the read path should be checked against
the demo tenant, not only against unit tests.

## Where things live

- `hub/guardian_hub/payloads/` — one module per dashboard surface
  (`overview`, `triage`, `topology`, `dnsview`, `updates`, `cameras`,
  `assets`, ...). These shape rows for reading; `routers/read.py` serves them.
- `hub/guardian_hub/payloads/history.py` — **the only place a trend is
  computed**, for both portals. Three surfaces draw the same underlying rows at
  different widths: the customer's own History tab, the owner's per-account
  charts (`payloads/account.build_trends`), and the owner's Ledger/Fleet
  sparkline column (`payloads/platform.build_trends`). They share
  `build_charts`, `window` and `alert_first_sightings` rather than each writing
  its own query.

  This is the same kind of pairing as `remedy` and `fix.FIX_ALERT_KEY` below,
  and the reason is sharper: the owner opens a customer's page *from* a ledger
  row while sitting with that customer, so two definitions of "new alerts over
  30 days" would be discovered by the customer rather than by us. What is easy
  to get wrong is not the bucketing but the predicate — `first_seen` not
  `last_seen` (the alert table keeps one row per key and updates it in place),
  only `TREND_SEVS`, and a deliberately lax SQL cutoff re-checked in Python
  because `first_seen` is a *string* carrying whatever UTC offset the sensor
  wrote, so a SQL `>=` on it is lexicographic.

  The ledger's column must also stay one grouped query. `tests/
  test_platform_trends.py` counts statements at two customer counts and demands
  they match, rather than asserting a ceiling — a ceiling loose enough not to
  be brittle is loose enough to sit above a real N+1 on a small fixture, which
  is how the wall-clock versions of these guards failed (see § Tests).
- **`label` and `trusted` are human warrants, not display fields.** This is the
  rule most likely to be broken by someone trying to be helpful, and the damage
  is silent. A trusted device is skipped entirely by the rogue-uplink /
  second-router check, exempt from risky-port alerts (telnet appearing on it
  raises nothing), and scored lower risk; a device with EITHER `trusted` or a
  non-empty `label` is excluded from `lan:unverified`. `ingest.store_scan`
  refuses to un-trust a device a later scan reports as untrusted, so the effect
  never wears off.

  So `kind` and `name` are machine inference and may be written automatically;
  `label` and `trusted` may not. A name the CUSTOMER types goes to `label` on
  purpose — that is a person vouching, which is what the field means.

  The first draft of the onboarding design auto-trusted every device Vault
  recognised, to make a new customer's estate look settled. It would have
  disabled three detections on each of thirty-five devices, permanently, and
  nobody would have noticed until the day one mattered. It was never needed:
  `home.py` anchors its baseline at the earliest `first_seen` and flags only
  devices newer than it, so the first scan's whole cohort is already exempt.
  Vault already implements "this is my network as of today".
  `hub/tests/test_warrant_rule.py` fails if an automatic path writes either
  field.
- `hub/guardian_hub/integrations/` — one module per channel (Slack, Home
  Assistant, ntfy, Discord, Teams, Telegram, PagerDuty, webhook), dispatched
  through `registry.deliver_background`. Notifications are pushed from **write
  paths** — `ingest.py` when an alert is stored, `jobdispatch.py` when a job is
  waiting, `adminnotify.py` for platform events — never from the read path.
- `hub/guardian_hub/remedy.py` — the alert → repair decision, on three action
  tiers: `auto` (run it now), `approval` (a single-use token-bound yes first),
  and `human` (nothing Vault runs fixes this — say what only a person can
  do; a dead sensor is the archetype, since every fix travels through it).
  `routers/write.py` and the chat tools call in; `jobdispatch.py` queues it;
  the sensor picks it up from `GET /queue` and `sensor/guardian_sensor/fix.py`
  runs it.

  The hub cannot import `guardian_sensor` — it is the sensor's own package,
  installed on the sensor host — so `remedy`'s fix tables are ported verbatim
  from `fix.FIX_ALERT_KEY`. `hub/tests/test_remedy.py` parses
  `sensor/guardian_sensor/fix.py` and fails when the two drift. If you change
  a fix action on either side, change it on both.
- `hub/guardian_hub/live.py` — the live feed: one in-process `Bus` keyed by
  `tenant_id`, served over `WS /api/v1/ws` by `routers/live.py`. A change the
  owner should see without refreshing has to call `bus.publish(...)` from its
  write path; `web/src/lib/live.ts` holds one `WebSocket` per app instance and
  receives it. `publish` is a no-op when no event loop was captured, so it is
  safe to call from a CLI script or a unit test.
- `hub/guardian_hub/chat/` — the Ask Vault engine. `routers/slackbot.py`
  runs the same engine for the Slack bot, so behavior added to one should
  generally work in the other.

  **The assistant's reach is the dashboard's reach, and a test holds it
  there.** Every customer write route (`POST`/`PATCH`/`DELETE` under
  `routers/`, minus owner-console and sensor paths) must have a tool in
  `chat/tools.py` declaring `route=` — or an entry, with its reason, in
  `UNCOVERED_ON_PURPOSE` in `tests/test_chat_coverage.py`. The same test
  asserts each tool's `min_role` is no looser than its route's `ctx=` guard.
  Adding a route without a tool fails the suite with the route's path in the
  message; that is the intended way to find out. Handlers in `chat/
  surfaces.py` call the route function and never reimplement it — the route
  owns the audit row, the `Edit` row and the bus publish, and a second copy
  drifts silently.
- `hub/guardian_hub/payloads/traffic.py` — who talks to whom, from `Flow`
  rows the sensor's `flows.py` rolls up out of tcpdump minute files. Every
  response carries `mode` (`mirror|router|none`), read from the network's
  declared `traffic_mode` crossed with what the sensor reported in
  `raw["capture"]` — never inferred from the rows, because router rows and
  mirror rows look alike and only the mode says which questions they can
  answer. `flows` is popped out of the payload above the `m.Scan` insert
  (`Scan.raw` is served to any viewer). Retention copies DNS: raw a week,
  `FlowRollup` a year. Nothing from a packet body is ever stored; `hints`
  holds only TLS SNI, DHCP 12/60 and mDNS names.

## Security posture

This is a security product; treat its own output as a disclosure surface.
`Scan.raw` is the whole sensor payload and is served to any viewer, so
credentials must be stripped **before** the row is inserted, not mutated out
afterwards — `store_scan` pulls heartbeat tokens out above the `m.Scan` insert
for exactly this reason. Apply the same care to anything new that lands in a
payload, an alert message or a report.

## Design assets

Every Vault surface is **first-party only**. Grep either app and the sole
external origins are `guarder.io`, `my.guarder.io` and `api.guarder.io` — no
CDN, no Google Fonts `<link>`, no hotlinked media. This is not incidental
tidiness. `fonts.css` carries the reason: Vault is a network watchdog, so
it has to render correctly on an isolated or WAN-down network — the moment
the page needs a third-party host to paint, it stops working in exactly the
situation the owner bought it for. A dashboard that goes blank when the
uplink dies is worse than no dashboard, because the owner reads the blankness
as "nothing is wrong".

So any asset borrowed from an external design library (Layers/getlayers.ai
is the one in use, wired up in `.mcp.json`) must be **vendored** before it
ships:

- **Fonts** — download the `woff2`, serve it from `web/public/fonts/` or
  `site/fonts/`, and declare `unicode-range` so a missing glyph falls through
  to the system stack instead of rendering as tofu. Never a `<link>` to a
  font host.
- **Video and posters** — Layers hands out *signed* links that expire in
  about an hour. Download the file into the repo and reference it by
  relative path. A hotlink does not fail loudly; it paints a blank element,
  which is the failure mode above.
- **WebGL scenes and gradients** — inline the shader and its CONFIG. These
  are ~1KB of code, not a dependency; there is no reason to fetch them.
- **Tint and palette** — drive them from the existing tokens rather than the
  library's own values, so a borrowed scene inherits the skin instead of
  fighting it.

The owner console (`admin.guarder.io`) declares **no palette of its own**. It
used to: it re-scoped the whole `--color-*` set onto a paper-and-ink ground so
a glance could never mistake it for a customer's dashboard. That made it look
like a different, older product, and the signal it was protecting now lives in
words — `.g-owner-host` prints the hostname in the bar. `web/src/owner/
owner.css` therefore contains no hex or `rgb()` literal at all; its `--own-*`
names are aliases onto app tokens, and `web/tests/ownerPalette.test.ts`
enforces exactly that. The one name that is not a translation is `--own-act`,
the interactive blue.

Its other rule is about weight rather than colour, and it is easy to undo by
accident: **healthy recedes**. A tile whose non-zero value would be bad news is
drawn quiet when it is at zero — label size, muted, out of the numeral face —
because a row of six equal-weight `0`s makes the loudest marks on the page the
facts that mean "nothing to do". Red stays rationed to "a customer needs you
today"; emphasis shifts weight, not hue.

The two token files are `web/src/styles/tokens.css` (the portal, two skins,
WCAG-asserted by `tests/contrast.test.ts`) and `site/styles.css` (the
marketing site, dark only). They look hand-synced and are not: measured
2026-09-18, the only value they share exactly is the brand blue `#1257c4`
(`--deep` on the site, `--color-primary` in the portal's *light* skin). Every
other role is a near-neighbour — `--bg` is `#080d17` against `#070d18` — and
the status triad differs visibly, the site's alert being an orange-red
`#ff6b5e` against the portal dark skin's pink-red `#e2647c`. They were
authored independently to the same intent and have drifted.

The brand blue is the one real invariant, and it is skin-independent because
it does two different jobs: on a light ground it is the interactive colour, on
a dark ground a saturated fill carrying white text. On dark the *interactive*
blue lightens to `#66a6ff` instead. If you change it, change it in both files,
the same way `remedy`'s fix tables track `fix.FIX_ALERT_KEY`. The full
measurement and the reconciliation decision live in
`docs/superpowers/specs/2026-09-18-brand-spine-design.md`.

Motion deserves its own caution. The portal is an instrument panel someone
opens because something is broken; `tokens.css` sets `--shadow-glow: none`
and keeps radii at 2-4px on purpose. Cinematic treatment belongs on the
public site, where the job is to arrest a stranger. On the portals it
competes with the alert it is supposed to be framing.

## Workflow

This repo uses the superpowers workflow:

- Specs → `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
- Plans → `docs/superpowers/plans/`
- Task briefs, reports and review diffs → `.superpowers/sdd/<dated-project>/`

Follow it rather than inventing a parallel convention.

## Tools

`docs/guardian-playground.html` is a change-request playground: a map of the
modules above, click one to say what should change, and it emits a prompt.
Open it with `open docs/guardian-playground.html`.

## Tools for this project

Account-wide plugins and connectors are installed; these are the ones that serve Vault.
Recommend them when relevant and ignore the rest. Full map: memory `project-tooling-map`.

- **Connectors** — Stripe (billing), Slack (alert integrations), a browser (Control Chrome
  or the built-in pane) to verify the dashboards live, scheduled-tasks for the nexus
  watchdog sweeps.
- **Plugins/skills** — `superpowers`; `frontend-design` + `dataviz` for the dashboard
  (UNKNOWN must never be styled like OK); `getlayers` for `site/` marketing pages only;
  `anthropic-skills:docs` for specs that need to be shared.
- **Agents** — code-reviewer, security-auditor, test-writer.
- **Not for Vault** — Vercel, vidIQ, Gamma, iOS Simulator.
- Slack plugin and Slack connector are duplicates; keep one enabled per session.
