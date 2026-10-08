# Handoff: Guardian Admin Console

## Overview

A redesign of the owner-facing admin console for **Guardian** (`admin.guarder.io`). Guardian places one sensor on a customer's network and watches devices, servers, websites, certificates, DNS and open ports. When something routine breaks it fixes the problem itself from an allow-listed set of actions, logs every action, and asks for approval before anything disruptive.

The console has a single user: the **owner**. There is no multi-tenant client view and no team roles. The design goal was to make the console read as an operations console that an owner could also show to a business client or an auditor — institutional and legible rather than playful — while staying dense enough to actually run a network from.

Ten views are covered, including a sign-in screen.

## About the Design Files

The file in this bundle is a **design reference created in HTML** — a working prototype that shows intended look, layout and behavior. It is **not production code to copy directly**.

The task is to **recreate these designs in the Guardian admin codebase's existing environment**, using its established framework, component library, routing and data layer. If the target codebase has a component system, build these screens from it and treat the HTML as the visual specification. If no frontend environment exists yet, choose the framework appropriate for the project and implement the designs there.

Specifically, do not carry over these prototype-only traits:

- **All styling is inline.** That is a constraint of the prototype environment, not a recommendation. Move it to whatever the codebase uses (CSS modules, Tailwind, styled-components, etc.) and lift the token table below into that system.
- **All data is hardcoded** in the logic class. Every list needs a real API behind it.
- **Navigation is local component state**, not routing. Each view should become a real route (see the route table below).
- **Nothing is interactive beyond navigation** — Approve, Defer, Export CSV, Generate now, Add window, Replace card, filter chips and the search field are all visual. They need real handlers.

## Fidelity

**High fidelity.** Colors, typography, spacing and layout are final and should be recreated closely. Exact values are in Design Tokens below. Interaction detail is intentionally shallow — the prototype specifies appearance and information architecture, not transition choreography.

## Design Principles

These four rules generated everything else. Keep them when you extend the console.

1. **Monospace means machine-produced.** JetBrains Mono is used for anything a computer emitted or that a human must read character by character: hostnames, IP and MAC addresses, incident IDs, timestamps, shell commands, version numbers, counts, currency, column headers, status pills. Instrument Sans is used for prose a human wrote: titles, descriptions, explanations. This one rule does most of the work in making the console read as an operations tool.
2. **Blue is for actions only.** `#2f7ff0` marks the primary button, the active nav indicator and links. It is never decoration. When everything is calm, there is almost no blue on screen.
3. **Color carries status, nothing else.** Green = fine or fixed automatically. Amber = needs attention or waiting on you. Red = failing. Blue = informational or newly discovered. Grey = unmanaged or not watched. There are no decorative accents, no gradients, no illustrations.
4. **Say what the fix will cost.** Every approvable action states its blast radius in plain language next to it ("drops live connections for roughly 3 seconds", "may break vendor tool"). Guardian is asking permission for something disruptive; the UI's job is to make the tradeoff legible in one read.

## Routes

| View | Suggested route | Nav group | Nav badge |
|---|---|---|---|
| Sign in | `/login` | — | — |
| Overview | `/` | Operations | — |
| Incidents | `/incidents` | Operations | open count |
| Maintenance | `/maintenance` | Operations | held job count |
| Audit log | `/audit` | Operations | — |
| Assets | `/assets` | Inventory | asset count |
| Asset detail | `/assets/:id` | Inventory (Assets stays active) | — |
| Websites & DNS | `/sites` | Inventory | endpoint count |
| Certificates | `/certificates` | Inventory | cert count |
| Reports | `/reports` | Insight | — |
| Settings | `/settings` | Account | — |
| Billing | `/billing` | Account | — |

Asset detail must keep **Assets** highlighted in the nav, not a separate item.

## Global Shell

Two-column layout, `display: flex`, `min-height: 100vh`. Body background `#0b0f15`, text `#e6ecf5`.

### Sidebar

- `width: 236px`, `flex: 0 0 236px`, `position: sticky; top: 0; height: 100vh`
- Background `#0a0d13` (darker than the content area), right border `1px solid #1b2430`
- Three blocks: brand + sensor status (padding `18px 18px 16px`, bottom border `1px solid #1b2430`), scrolling nav (`flex: 1; overflow-y: auto; padding: 14px 10px`), user footer (padding `12px 14px`, top border `1px solid #1b2430`)

**Brand mark.** A 22×22px square, `1.5px solid #2f7ff0`, `border-radius: 3px`, centering a 7×7px `#2f7ff0` square with `border-radius: 1px`. Wordmark `GUARDIAN` in JetBrains Mono 12.5px/500, `letter-spacing: 0.14em`. Gap 10px. Replace with the real Guardian mark if one exists; keep the size and the monospace wordmark treatment.

**Sensor status chip.** Background `#101722`, border `1px solid #1e2937`, radius 5px, padding `8px 10px`. A 6px green dot (`#3ecf8e`) with `animation: gpulse 2.6s ease-in-out infinite`, then `SENSOR-01` (JetBrains Mono 11px, `#8d9bb0`) over `Online · 41 days` (11px, `#5d6b80`). The dot pulse is the only motion in the console — it means the sensor is reporting. Do not animate anything else.

**Nav groups.** Group label in JetBrains Mono 10px, `letter-spacing: 0.16em`, `#4d5a6e`, padding `0 10px 8px`. Groups separated by `gap: 16px`; items by `gap: 2px`.

**Nav item.** Padding `8px 10px`, radius 5px, label 13.5px/500.

| State | Background | Left indicator | Label |
|---|---|---|---|
| Default | transparent | none | `#9aa8bc` |
| Hover | `#121a26` | none | `#9aa8bc` |
| Active | `#161f2c` | `inset 2px 0 0 #2f7ff0` (box-shadow, not border — avoids layout shift) | `#e6ecf5` |

Badge on the right: JetBrains Mono 11px, padding `1px 6px`, `border-radius: 20px`. Neutral badges `#1a2330` / `#8d9bb0`; the Incidents badge is amber (`#3a2a14` / `#f5b544`) when anything is open.

**Footer.** 28×28px avatar square, `border-radius: 4px`, background `#1a2534`, border `1px solid #263243`, initials in JetBrains Mono 11px `#8d9bb0`. Name 12.5px/500 `#cfd8e6`, email 11px `#5d6b80` with ellipsis. Below it a `SIGN-IN SCREEN →` link (JetBrains Mono 10px, `letter-spacing: 0.1em`, `#4d5a6e`, hover `#8d9bb0`) — **this is a prototype affordance for reaching the login screen. Remove it and replace with a real sign-out action.**

### Top bar

`height: 60px`, padding `0 26px`, bottom border `1px solid #1b2430`, background `#0b0f15`, `position: sticky; top: 0; z-index: 5`. Contents on one row, `gap: 14px`, spacer pushing controls right.

- **Title** 16px/600, `letter-spacing: -0.01em`, `white-space: nowrap`. **Subtitle** 12.5px `#5d6b80`, ellipsised. Per-view strings are in the Views section.
- **Search** 190px pill: background `#101722`, border `1px solid #1e2937`, radius 5px, padding `6px 11px`. A 9px circle outline (`1.5px solid #4d5a6e`) as the magnifier stand-in, placeholder "Search assets" 12.5px `#4d5a6e`, and `⌘K` in JetBrains Mono 10px `#3d4858`. Should open a command palette over assets, incidents and hostnames.
- **Range switch** `24h / 7d / 30d`, one bordered group (`1px solid #1e2937`, radius 5px, `overflow: hidden`), each item padding `6px 11px`, 12px/500. Selected `#1c2634` / `#e6ecf5`; unselected transparent / `#7c8a9e`. In the prototype this is presentational — it should filter the Overview charts and counts.
- **Auto-heal toggle**, the most important control in the console. Padding `6px 11px`, radius 5px, a 6px dot plus a JetBrains Mono 11px label at `letter-spacing: 0.06em`. On: label `AUTO-HEAL ON`, foreground `#3ecf8e`, border `#1d3a2c`. Paused: `AUTO-HEAL PAUSED`, foreground `#f5b544`, border `#3a2a14`. Clicking flips it. **In production this needs a confirmation step and should be audit-logged** — pausing it stops Guardian acting on its own.

### Content area

`flex: 1`, padding `22px 26px 44px`, vertical scroll. Section stacks use `gap: 17-18px`.

### Card

The single container primitive, used for every panel:

- Background `#131924`, border `1px solid #1e2937`, `border-radius: 7px`, `overflow: hidden`
- Card header: padding `13px 16px`, bottom border `1px solid #1e2937`, title 13.5px/600, optional monospace 11px `#5d6b80` meta pushed right
- List rows inside a card: padding `11-13px 16px`, bottom border `1px solid #171f2b` (dimmer than the card border), row hover `#161d2a`
- Padded (non-list) cards use `padding: 16px` with no inner header divider

Cards that need attention use border `#2a3547` instead of `#1e2937` — a slightly lighter edge, no colored border.

## Views

### 1. Sign in

Title/subtitle: none (full-bleed, no shell).

Two equal columns, `min-height: 100vh`.

**Left** (background `#0a0d13`, right border `1px solid #1b2430`, padding 48px, `justify-content: space-between`): brand mark at top; centered pitch block `max-width: 420px` with a 30px/600 headline at `line-height: 1.2`, `letter-spacing: -0.02em` — "One sensor. Everything on your network accounted for." — and a 15px/1.6 `#8d9bb0` paragraph describing what Guardian watches and that anything disruptive waits for you; footer line `GUARDER.IO · BUILT FOR WHOEVER GETS THE CALL` in JetBrains Mono 11px `#4d5a6e`.

**Right** (padding 48px, centered): a 340px column, `gap: 18px`. "Sign in" 19px/600 with 13px `#7c8a9e` sub. Two fields, each a JetBrains Mono 10px `letter-spacing: 0.12em` `#5d6b80` label over an input (padding `10px 12px`, background `#101722`, border `1px solid #1e2937`, radius 5px, 13.5px text). Primary button full width, padding 11px, radius 5px, `#2f7ff0`, white 13.5px/600, hover `filter: brightness(1.12)`. Then a bordered note with a green dot: "Hardware key required after password". Footer: "Sessions expire after 12 hours of inactivity." 12px `#4d5a6e`, centered.

Wire the button to real auth. Needs error, loading and locked-out states, which the prototype does not show.

### 2. Overview

Title "Overview" · sub "Sensor-01 · your network, right now".

**KPI row.** `grid-template-columns: repeat(auto-fit, minmax(178px, 1fr))`, `gap: 13px`. Each card: padding `15px 16px`, `gap: 10px`. Label JetBrains Mono 10.5px `letter-spacing: 0.1em` `#7c8a9e` with a 5px status dot pushed right. Value JetBrains Mono 29px, `line-height: 0.95`, `letter-spacing: -0.02em`. Unit 12px `#5d6b80` baseline-aligned to the value. Note 12px `#7c8a9e`, `text-wrap: pretty`.

Four KPIs: Assets watched (`64` online, dot blue), Open incidents (`3` of 47, amber value, dot amber), Fixed automatically (`412` actions, green value, dot green), Awaiting approval (`2` queued, dot amber). Each note adds a second fact rather than repeating the number — "2 discovered today, unmanaged", "44 closed without you this week", "93% of everything Guardian saw", "Oldest has been waiting 41 min".

**Body.** `grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr)`, `gap: 17px`, `align-items: start`.

**Left column, card 1 — "Waiting on you"** (attention card, border `#2a3547`). Header: amber dot, title, right-aligned 12px `#5d6b80` "Disruptive actions need approval". Each row padding `14px 16px`: title 13.5px/600, detail 12.5px `#8d9bb0`, then a monospace target pill (`#1a2330` background, 10.5px, padding `2px 7px`, radius 3px) and an impact line in JetBrains Mono 10.5px `#5d6b80` (e.g. `BRIEF OUTAGE · WAITING 41 MIN`). Right side: **Defer** (padding `7px 13px`, radius 5px, border `1px solid #2a3547`, 12.5px/500 `#a8b4c6`, hover background `#1a2330` + text `#e6ecf5`) and **Approve** (padding `7px 14px`, radius 5px, `#2f7ff0`, white 12.5px/600, hover `brightness(1.12)`). Hide the whole card when the queue is empty.

**Left column, card 2 — "Self-healing activity."** 14-day stacked bar chart. Header meta `LAST 14 DAYS`. Plot area `height: 108px`, bars `display: flex; gap: 5px`, each bar `flex: 1` as a bottom-anchored column with `gap: 2px`: an auto-fixed segment (`#2f7ff0`, `border-radius: 2px 2px 0 0`) above an after-approval segment (`#3a4a61`, `border-radius: 0 0 2px 2px`). Heights are percentages of the plot area. Below: three date ticks (`SEP 07 / SEP 13 / SEP 20`) space-between in JetBrains Mono 10px `#4d5a6e`, then a legend above a `1px solid #1b2430` rule with 8×8px 2px-radius swatches, 12px `#8d9bb0` labels and monospace totals (412 / 31). Implement with the codebase's chart library or plain divs — it is a simple stacked bar and does not need a charting dependency.

**Left column, card 3 — "Live activity."** Header: title, pulsing green dot, "Full log" link (12.5px `#63a4ff`) routing to `/audit`. Rows padding `11px 16px`: monospace 11.5px time in a fixed 42px column, a 6px status dot (`margin-top: 5px` to sit on the first text line), the event text 13px `#cfd8e6` `text-wrap: pretty`, and a right-aligned monospace 10.5px tag pill.

Tag vocabulary: `AUTO` (`#132b22` / `#3ecf8e`), `WAITING` (`#3a2a14` / `#f5b544`), `DISCOVERED` (`#14243a` / `#63a4ff`), `APPROVED` (`#1a2330` / `#a8b4c6`). Should poll or subscribe; newest first.

**Right column, card 1 — "Coverage."** Padded card. Title with 12px `#5d6b80` sub "What the sensor can currently see". Five rows, `gap: 13px`, each a label (13px `#cfd8e6`) / monospace value (`64 / 66`) / monospace percent in a right-aligned 36px column, above a 4px track (`#1c2532`, radius 3px) with a fill at that percent — green at 100%/97%, amber below (DNS zones at 75%).

**Right column, card 2 — "Certificates expiring."** Header meta `90 D`. Rows: host in JetBrains Mono 12.5px with 11.5px `#5d6b80` issuer beneath; right side monospace 13px days-left (red under 14, amber under 30, `#cfd8e6` above) over 10.5px `#5d6b80` "days left". Closing row, 12.5px `#8d9bb0`: "Renewal is allow-listed. Guardian handles these without asking."

**Right column, card 3 — "Sensor health."** Padded card, 2×2 grid, `gap: 13px`: Uptime `41 d`, Last sweep `4 min`, Agent `2.8.1`, Queue `0`. Labels JetBrains Mono 10px `letter-spacing: 0.1em` `#5d6b80`; values JetBrains Mono 17px.

### 3. Incidents

Title "Incidents" · sub "Open and recently resolved".

**Filter row.** Chips: padding `6px 12px`, `border-radius: 20px`, 12.5px/500. Selected `#1c2634` / `#e6ecf5` / border `#2a3547`; unselected transparent / `#8d9bb0` / border `#1e2937`. The "Unmanaged"-style warning chip uses amber text with an amber border. Labels carry counts: `Open · 3`, `Waiting on you · 2`, `Resolved · 44`, `All`. Right-aligned 12.5px `#5d6b80` "Severity, then age".

**Incident card.** A card with a 3px full-height severity stripe on the left (`flex-shrink: 0`), then padding `15px 17px`, `gap: 11px`.

Meta row: severity pill (JetBrains Mono 10.5px, `letter-spacing: 0.08em`, padding `2px 7px`, radius 3px), incident ID (monospace 11.5px `#5d6b80`), relative age (11.5px `#5d6b80`). Title 15px/600 `letter-spacing: -0.01em`. Detail 13px `#8d9bb0`, `max-width: 76ch` — always states the consequence of the fix, not just the symptom.

Right block: state pill (padding `5px 10px`, radius 4px, dot + monospace 10.5px label) over the monospace 11px target.

Severity and state palette:

| Level | Stripe / text | Pill background |
|---|---|---|
| CRITICAL | `#f2686b` | `#3a1618` |
| HIGH | `#f5b544` | `#3a2a14` |
| MEDIUM | `#63a4ff` | `#14243a` |
| RESOLVED | `#3ecf8e` | `#132b22` |

States: `NEEDS YOU` (red), `WAITING` (amber), `CLOSED` (green).

**Plan strip**, the distinguishing element of this screen. A recessed bar: background `#101722`, border `1px solid #1c2532`, radius 5px, padding `10px 13px`. `PLAN` label in JetBrains Mono 10px `letter-spacing: 0.12em` `#5d6b80`, then the literal command or change Guardian intends to run in JetBrains Mono 12px `#a8b4c6` (single line, ellipsised) — e.g. `systemctl restart nginx`, `firewall: deny tcp/8080 from 10.0.20.0/24`, `acme: repoint webroot → /srv/www/.well-known, then reissue`. Right side: `APPROVE TO RUN` in blue for pending, or `FIXED 13:51` in green for resolved. Showing the exact command is the point — the owner should never have to guess what approval authorizes.

### 4. Maintenance

Title "Maintenance" · sub "Windows and held work".

`grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr)`, `gap: 17px`, `align-items: start`.

**Left — "Windows."** Header: title, 12px `#7c8a9e` sub "Anything disruptive that you approve is held until the next window opens.", and an **Add window** secondary button. Rows: label 13.5px/500 over the schedule in JetBrains Mono 11.5px `#7c8a9e` (`TUE–THU · 02:00–04:00 · UTC+2`); right side a monospace state pill (`OPEN IN 11 H` blue, later windows neutral `#1a2330` / `#8d9bb0`) over 11.5px `#5d6b80` queue count. Includes a **Freeze** entry (`DEC 20 – JAN 02 · nothing disruptive runs`) — keep this concept; a change freeze is a real requirement.

**Right — "Held for the next window."** Rows: job title 13px `text-wrap: pretty`, then a monospace target pill and a monospace 10.5px `#5d6b80` provenance line (`APPROVED 09:20 · WEEKNIGHT WINDOW`). These are already-approved jobs waiting on a window, which is why there is no Approve button here.

### 5. Audit log

Title "Audit log" · sub "Append-only record of every action".

Intro row: 13px `#8d9bb0` `max-width: 72ch` — "Every action Guardian takes is written here first. Append-only, exportable, and it names the rule that authorised the change." — plus an **Export CSV** secondary button.

**Table.** Card containing a horizontal scroll container (`overflow-x: auto`) wrapping a `min-width: 840px` inner div. Columns `96px 118px minmax(0, 1fr) 150px 104px`, `gap: 13px`.

Header row: background `#101722`, padding `11px 16px`, bottom border `1px solid #1e2937`, labels `TIME / ACTOR / ACTION / TARGET / RESULT` in JetBrains Mono 10px `letter-spacing: 0.1em` `#5d6b80`.

Body rows: padding `11px 16px`, bottom border `1px solid #171f2b`, hover `#161d2a`. Time monospace 12px `#7c8a9e`. Actor monospace 11.5px — `guardian` in `#63a4ff`, `owner` in `#e6ecf5`, so machine and human actions are distinguishable at a glance. Action 13px `#cfd8e6`. Target monospace 11.5px `#7c8a9e`. Result monospace 11px: `SUCCESS` / `APPLIED` green, `PENDING` amber, `DEFERRED` grey.

**The horizontal scroll wrapper is deliberate** — it was added because fixed column widths truncated the ACTION column at narrow viewports. Keep it, or replace with a responsive table that reflows rather than ellipsising the action text. Needs pagination or infinite scroll, plus filters by actor, result and date range.

### 6. Assets

Title "Assets" · sub "Devices, servers, sites and certificates".

Filter chips (`All · 64`, `Servers · 8`, `Devices · 42`, `Websites · 11`, `Unmanaged · 2` — the last in amber), right-aligned monospace 11.5px `#5d6b80` `64 DISCOVERED · 2 UNMANAGED`.

**Table.** Same card + scroll-container pattern, `min-width: 880px`, columns `minmax(0, 1.5fr) 96px 118px minmax(0, 1fr) 92px 104px`. Headers `ASSET / TYPE / ADDRESS / PLATFORM / LAST SEEN / POSTURE` (last right-aligned).

Rows padding `12px 16px`, `cursor: pointer`, route to asset detail. Asset cell: 7px status dot, then name 13.5px/500 over 11.5px `#5d6b80` owner/location, both ellipsised. Type 12.5px `#8d9bb0`. Address monospace 12px `#a8b4c6`. Platform 12.5px `#8d9bb0` ellipsised. Last seen monospace 11.5px `#7c8a9e` (relative — `now`, `4 min`). Posture: right-aligned monospace 10.5px pill, `letter-spacing: 0.06em`, padding `3px 8px`, radius 3px.

Posture vocabulary: `CLEAN` (`#132b22` / green), `3 FINDINGS` and `DEGRADED` and `UNMANAGED` (`#3a2a14` / amber), `CERT 9 D` (`#3a1618` / red), unmanaged-and-idle (`#1a2330` / `#8d9bb0`). The pill states the specific problem rather than a generic severity word.

### 7. Asset detail

Title = asset name · sub = `Server · 10.0.4.12` (type · address). The prototype uses `app-srv-02` for all rows; production keys off `:id`.

**Back link.** `← ASSETS` in JetBrains Mono 11px `letter-spacing: 0.08em` `#5d6b80`, hover `#a8b4c6`.

**Header card.** Padded 19px, `flex-wrap: wrap`. Left: 8px status dot, name 21px/600 `letter-spacing: -0.015em`, status pill (`NEEDS ATTENTION` amber). Beneath, 13px `#8d9bb0` platform/hardware/location line. Right: **Run check** and **Maintenance mode** secondary buttons plus **Apply 3 fixes** primary (padding `8px 14-15px`, radius 5px). Give the button group `flex-shrink: 0` / `flex-wrap: nowrap` where space allows — at narrow widths the primary currently wraps to its own line.

**Body.** `grid-template-columns: 248px minmax(0, 1fr)`, `gap: 17px`, `align-items: start`.

**Left, card 1 — "IDENTITY."** Monospace card header (JetBrains Mono 10px `letter-spacing: 0.12em` `#5d6b80`). Key/value rows padding `9px 15px`: key 12px `#5d6b80` in a fixed 76px column, value JetBrains Mono 12px `#cfd8e6` with `overflow-wrap: anywhere`. Fields: Address, MAC, Role, First seen, Agent, Checks, Tags.

**Left, card 2 — "30-DAY UPTIME."** Monospace label, then `99.94%` in JetBrains Mono 26px green, then 12px `#7c8a9e` explaining the one gap and its cause ("One 26-minute window on Sep 04, during a kernel update Guardian applied after approval"). A number with its own explanation attached.

**Right, card 1 — "Open findings."** Header meta `3 FIXABLE · 1 MANUAL`. Rows: severity pill (`HIGH` amber, `MED` blue, `LOW` neutral), title 13px, the literal remediation in JetBrains Mono 11.5px `#7c8a9e` (`apt install openssh-server=1:9.6p1`), and a right-aligned monospace 11px mode — `ALLOW-LISTED` green (Guardian may act) or `MANUAL` grey (needs a human on site).

**Right, card 2 — "Open ports."** Header meta `SCANNED 4 MIN AGO`. A `repeat(auto-fit, minmax(150px, 1fr))` grid with `gap: 1px` over a `#171f2b` background, each cell `#131924` with padding `13px 16px` — the gap produces hairline dividers. Each cell: port in JetBrains Mono 14px, service in monospace 11px `#7c8a9e`, note 11.5px `#5d6b80`. Expected ports render `#cfd8e6`; unexpected ones render amber with the reason ("Unexpected · no auth set").

**Right, card 3 — "Recent actions on this asset."** Rows: monospace 11.5px timestamp in a 92px column, action 13px ellipsised, right-aligned monospace 11px result (`AUTO` green, `APPROVED` `#a8b4c6`, `MANUAL` grey).

### 8. Websites & DNS

Title "Websites & DNS" · sub "11 endpoints · 4 zones".

`grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr)`, `gap: 17px`.

**Left — "Websites."** Header meta `CHECKED EVERY 60 S`. Rows: 7px status dot, host in JetBrains Mono 13px over an 11.5px `#5d6b80` note (either infrastructure detail or the current problem — "Certificate expires in 9 days"), right side monospace 13px latency over 10.5px `#5d6b80` 30-day uptime.

**Right — "DNS zones."** Header with 12px `#7c8a9e` sub "Guardian compares every nameserver against the zone of record." Rows: zone in JetBrains Mono 12.5px over an 11.5px `#5d6b80` note, right-aligned monospace state pill: `IN SYNC` green, `DRIFTING` amber, `NOT WATCHED` neutral.

### 9. Reports

Title "Reports" · sub "Monthly summary and audit export".

Intro row: 13px `#8d9bb0` `max-width: 72ch` — "A monthly summary is generated on the first of the month and mailed to you. It is the same record an auditor or insurer would ask for." — plus a **Generate now** primary button.

**KPI row.** Same `auto-fit, minmax(178px, 1fr)` grid and card treatment as Overview, but with the label above the value and no status dot. Four: Actions taken `443`, Handled alone `93%` (green), Median time to fix `41 s` (green), Your time spent `18 m`. Notes give comparison or basis — "Up from 88% in August", "From detection to verified", "Across 31 approvals".

**Body.** `grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr)`, `gap: 17px`.

**Left — "Where the work went."** Padded card, 12px `#5d6b80` sub "Actions by category, last 30 days". Five label / monospace-value rows over 4px `#1c2532` tracks. Bars are normalized to the largest category (164 = 100%), blue for automated categories, `#3a4a61` for the one that still asks first (Security updates). The color difference is meaningful — keep it tied to the allow-list state, not arbitrary.

**Right — "Past reports."** Rows: month 13px over an 11.5px `#5d6b80` one-line summary, right-aligned `PDF` link in JetBrains Mono 11px `#63a4ff`.

### 10. Settings

Title "Settings" · sub "Allow-list and alerting".

`grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr)`, `gap: 17px`.

**Left — "Allow-list,"** the most consequential screen in the console. Header: title plus 12px `#7c8a9e` "Actions Guardian may take on its own. Everything else waits for approval." Rows padding `13px 16px`: label 13.5px/500, detail 12px `#7c8a9e` stating the exact trigger condition ("Only when the service was running and health checks fail twice", "When a disk crosses 85%"), and a toggle.

**Toggle.** 38×21px, `border-radius: 12px`, `padding: 3px`, a 15px white circle positioned by `justify-content: flex-end`/`flex-start`. Track `#2f7ff0` when on, `#242f3e` when off.

Six rules, with the last two off by default — Apply security updates ("Currently asks first, because some need a reboot") and Close unexpected ports ("Off by default — closing a port can break a vendor tool"). The default-off state and its stated reason are part of the design. **Every toggle here changes what Guardian is permitted to do without asking: each change must be audit-logged, and turning one on deserves a confirmation.**

**Right — "Where alerts go."** Padded card, rows of dot + channel label (13px `#cfd8e6`) + right-aligned monospace 11px routing rule: `owner@guarder.io / ALL SEVERITIES` (green dot), `SMS · ···4417 / CRITICAL ONLY` (green dot), `Slack · #ops / NOT CONNECTED` (grey dot).

### 11. Billing

Title "Billing" · sub "One sensor, unlimited assets".

`grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr)`, `gap: 17px`.

**Left — "Invoices."** Rows: monospace 12px date in a 96px column, monospace 12.5px reference, monospace 13px amount, right-aligned monospace 11px `PAID` in green (62px column). Rows should link to invoice PDFs.

**Right, card 1 — "Current plan."** `$240` in JetBrains Mono 26px with `/ month` in 12.5px `#7c8a9e` baseline-aligned, then 12.5px `#8d9bb0` "One sensor, unlimited assets. Renews Oct 01 on Visa ···4417."

**Right, card 2 — "Payment method."** A 34×22px card placeholder (radius 3px, `#1a2534`, border `1px solid #263243`), `VISA ···4417` in JetBrains Mono 12.5px over 11.5px `#5d6b80` expiry, and a **Replace** link (12.5px `#63a4ff`). Swap the placeholder for the real card-brand asset from whatever payment provider the codebase uses.

## Interactions & Behavior

Implemented in the prototype:

- Sidebar navigation between all views; Assets rows and the "Full log" link route onward; asset detail has a back link; login button enters the console; sidebar footer link returns to login
- Range switch (`24h/7d/30d`) selection state
- Auto-heal toggle flips label, color and border
- Allow-list toggles flip individually
- Row hover `#161d2a`; nav hover `#121a26`; primary-button hover `filter: brightness(1.12)`; secondary-button hover background `#1a2330`
- The sensor dot pulse: `@keyframes gpulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.3 } }`, `2.6s ease-in-out infinite`

Needs building:

- **Approve / Defer** — Approve should confirm, show the exact plan string, then either execute or hold for the next maintenance window; Defer should ask for a duration. Both write to the audit log.
- **Auto-heal pause** — confirmation, a reason field, and an audit entry. Consider a visible banner while paused.
- **Allow-list changes** — confirmation on enable, audit entry on every change.
- **Search / ⌘K** — command palette across assets, incidents, hostnames.
- **Range switch** — should actually filter Overview counts and the 14-day chart.
- **Filter chips** on Incidents and Assets.
- **Export CSV** (Audit) and **Generate now** / **PDF** (Reports).
- **Add window** (Maintenance), **Replace** card (Billing), **Run check** / **Maintenance mode** / **Apply N fixes** (asset detail).
- **Live updates** — Live activity, incident counts and the nav badge should poll or subscribe. Prefer append-with-highlight over a full re-sort so the owner does not lose their place.
- **Empty states** — the prototype shows none. Every list needs one, and "Waiting on you" should hide entirely when empty. The healthy overview is the common case and should feel calm, not broken.
- **Loading and error states** — none specified. Use skeleton rows matching row height rather than spinners.
- **Sign-out** replacing the prototype's sidebar login link.

## Responsive Behavior

Designed for desktop (1440×900 reference). Data tables already scroll horizontally below roughly 880px. Below about 1100px the two-column view bodies should stack to one column; below about 900px the sidebar should collapse to a drawer. Incident cards, the asset-detail header and approval rows already wrap via `flex-wrap: wrap`. No mobile layout is specified — an owner-facing alert view for phones is a separate design.

## State Management

Prototype state (component-local, all of it replaceable):

| Key | Type | Purpose |
|---|---|---|
| `view` | string | Which view is showing — becomes routing |
| `range` | `"24h" \| "7d" \| "30d"` | Overview time range |
| `auto` | boolean | Auto-heal on/off — real server-side setting |
| `rules` | boolean[6] | Allow-list toggles — real server-side settings |

Production state:

- **Server settings** (persisted, audited): auto-heal enabled, allow-list rules, alert channels, maintenance windows
- **Polled / subscribed**: live activity feed, incident list and counts, sensor health, asset last-seen
- **Per-request**: asset list (paginated, filtered), asset detail, audit log (paginated, filtered), certificate list, site and zone status, report aggregates, invoices
- **Optimistic UI** is risky here — Approve triggers a real change on real infrastructure. Show a pending state and confirm from the server.

## Design Tokens

### Color

| Token | Value | Use |
|---|---|---|
| `bg/base` | `#0b0f15` | Body and content background |
| `bg/rail` | `#0a0d13` | Sidebar, login left panel |
| `bg/card` | `#131924` | All cards |
| `bg/inset` | `#101722` | Recessed inputs, table headers, plan strips |
| `bg/row-hover` | `#161d2a` | Row hover |
| `bg/nav-active` | `#161f2c` | Active nav item |
| `bg/nav-hover` | `#121a26` | Nav hover |
| `bg/control` | `#1c2634` | Selected segment in range switch |
| `bg/pill` | `#1a2330` | Neutral pill / secondary hover |
| `bg/avatar` | `#1a2534` | Avatar square, card placeholder |
| `bg/track` | `#1c2532` | Progress track |
| `bg/toggle-off` | `#242f3e` | Toggle track, off |
| `border/rail` | `#1b2430` | Sidebar and top-bar edges |
| `border/card` | `#1e2937` | Card border, inputs, dividers |
| `border/card-attn` | `#2a3547` | Attention card, secondary buttons |
| `border/row` | `#171f2b` | Row dividers inside cards |
| `border/inset` | `#1c2532` | Plan strip border |
| `border/avatar` | `#263243` | Avatar and placeholder border |
| `text/primary` | `#e6ecf5` | Titles, values |
| `text/secondary` | `#cfd8e6` | Row body text |
| `text/tertiary` | `#a8b4c6` | Commands, secondary button labels |
| `text/muted` | `#8d9bb0` | Descriptions, inactive nav |
| `text/nav` | `#9aa8bc` | Inactive nav label |
| `text/dim` | `#7c8a9e` | Fine print, monospace meta |
| `text/faint` | `#5d6b80` | Column headers, timestamps, labels |
| `text/faintest` | `#4d5a6e` | Group labels, placeholders |
| `text/ghost` | `#3d4858` | `⌘K` hint |
| `accent/blue` | `#2f7ff0` | Primary buttons, active indicator, chart fill |
| `accent/link` | `#63a4ff` | Links, `guardian` actor, info text |
| `accent/link-hover` | `#9cc6ff` | Link hover |
| `status/green` | `#3ecf8e` | Healthy, auto-fixed |
| `status/amber` | `#f5b544` | Needs attention, waiting |
| `status/red` | `#f2686b` | Failing, critical |
| `status/neutral-bar` | `#3a4a61` | After-approval chart segment, off-allow-list bar |
| `statusbg/green` | `#132b22` | Green pill background |
| `statusbg/amber` | `#3a2a14` | Amber pill background |
| `statusbg/red` | `#3a1618` | Red pill background |
| `statusbg/blue` | `#14243a` | Blue pill background |
| `statusborder/green` | `#1d3a2c` | Auto-heal-on border |

All greys are blue-tinted (hue around 215-220) rather than neutral. Swapping in neutral greys flattens the whole thing.

### Typography

- **Sans:** Instrument Sans, weights 400 / 500 / 600 / 700 (Google Fonts). Fallback `Helvetica, Arial, sans-serif`.
- **Mono:** JetBrains Mono, weights 400 / 500 (Google Fonts).
- Base size 14px; `-webkit-font-smoothing: antialiased`.

| Role | Spec |
|---|---|
| Login headline | 30px / 600 / `line-height: 1.2` / `letter-spacing: -0.02em` |
| Page H1 (top bar) | 16px / 600 / `letter-spacing: -0.01em` |
| Detail asset name | 21px / 600 / `letter-spacing: -0.015em` |
| Login H2 | 19px / 600 |
| KPI value | mono 29px / `line-height: 0.95` / `letter-spacing: -0.02em` |
| Large figure | mono 26px |
| Small figure | mono 17px |
| Card title | 13.5px / 600 |
| Incident title | 15px / 600 / `letter-spacing: -0.01em` |
| Row primary | 13-13.5px / 400-500 |
| Row secondary | 12-12.5px |
| Fine print | 11.5px |
| Mono body | 12-12.5px |
| Mono meta | 11-11.5px |
| Column header | mono 10px / `letter-spacing: 0.1em`, uppercase |
| Nav group label | mono 10px / `letter-spacing: 0.16em`, uppercase |
| Status pill | mono 10.5px / `letter-spacing: 0.06em`, uppercase |
| Wordmark | mono 12.5-13px / 500 / `letter-spacing: 0.14em` |

Body prose uses `text-wrap: pretty`; long-form paragraphs are capped at `72-76ch`.

### Spacing

4px base. Used: 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 20, 22, 26, 44, 48.

- Content padding `22px 26px 44px`; login panels 48px
- Card padding 16px (padded) or `13px 16px` (header) and `11-13px 16px` (rows)
- Section gap 17-18px; card grid gap 13px; inline gap 7-14px

### Radius

3px (pills, small squares) · 4px (small controls) · 5px (buttons, inputs, inset bars) · 7px (cards) · 12px (toggle) · 20px (filter chips) · 50% (status dots)

### Elevation

None. No shadows anywhere — depth comes from the background steps (`#0a0d13` → `#0b0f15` → `#101722` → `#131924`) and 1px borders. The one exception is the active nav item's `inset 2px 0 0 #2f7ff0`, used as an indicator rather than elevation. Do not add shadows.

### Status dot

6-8px circle, `border-radius: 50%`, filled with a status color. Inside text rows it needs `margin-top: 5px` to align with the first line.

## Assets

No image, icon or illustration assets. Everything is CSS.

- **Brand mark** is two nested squares in CSS. Replace with the real Guardian mark.
- **Search icon** is a 9px outlined circle standing in for a magnifier. Replace with the codebase's icon set.
- **Payment card** is an empty rounded rectangle. Replace with the provider's brand asset.
- **Nav has no icons** by design — labels only. If the codebase's nav uses icons, add them at 16px in the inactive label color, but the label-only treatment is part of why the sidebar reads as calm.
- **Fonts** load from Google Fonts: `Instrument+Sans:wght@400;500;600;700` and `JetBrains+Mono:wght@400;500`. Self-host in production.

## Content Notes

The copy is part of the design and worth preserving in spirit:

- Every approval request states the cost in plain language, in a full sentence, before any jargon.
- Every allow-list rule states its exact trigger condition.
- Every number that could raise a question carries its explanation ("One 26-minute window on Sep 04, during a kernel update Guardian applied after approval").
- Nothing is exclamatory. No emoji. No "Oops" or "Great news". The tone is a competent colleague reporting in.
- Guardian is referred to by name in the third person ("Guardian handles these without asking"), which keeps the automated actor distinct from the owner throughout.

All data values in the prototype are plausible fabrications for layout purposes. Do not ship them.

## Files

- `Guardian Admin.dc.html` — the full prototype: all ten views plus asset detail and sign-in. Template markup first, then the logic class holding navigation state and all sample data. Open it in a browser to click through.
