# The brand spine — one identity across three surfaces

Phase 1 of the Layers/design programme. Decided 2026-09-18.

## Why this document exists

Guardian is about to gain a third front end (`admin.guarder.io`). It already
has two, and they carry **two separate colour systems kept in agreement by
hand**:

| | `site/styles.css` | `web/src/styles/tokens.css` |
|---|---|---|
| Tokens | 16, dark only | Two full skins |
| Naming | `--acc`, `--deep`, `--ok`, `--crit` | `--color-primary`, `--color-good`, `--color-alert` |
| Fonts | Manrope + IBM Plex Mono | Manrope + IBM Plex Mono |
| Primary blue | `--deep: #1257c4` | `--color-primary: #1257c4` |
| Contrast guard | **none** | `tests/contrast.test.ts`, 9 pairs × 2 skins |

The fonts match and the brand blue is the same hex. **Nothing else does.**
Measured on 2026-09-18, every other role is a near-neighbour rather than a
match — `--bg` is `#080d17` against the portal's `#070d18`, `--acc` is
`#6ba5ff` against `#66a6ff` — and the status triad diverges visibly: the
site's red is an orange-red `#ff6b5e`, the portal's dark skin a pink-red
`#e2647c`.

So the two systems were not hand-synced, as their similarity suggests. They
were authored independently to the same intent, and they have already drifted.
The one genuine invariant is the brand blue.

A third surface is exactly the event that turns "already drifted" into "three
different products", because the person adding it copies whichever file they
happened to open. This document fixes the spine so the third surface inherits
rather than diverges.

## The constraint that shapes the answer

The obvious move — hoist the shared values into one file both stylesheets
import — **does not work here**, and the reason is worth stating so nobody
tries it again.

`web/tests/contrast.test.ts` reads `src/styles/tokens.css` by literal path
and pulls values out with

```
/--(color-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})/g
```

scoped to two specific blocks: the file's **first bare `:root {`** (light) and
`:root[data-theme="dark"] {` (dark). That means three things are load-bearing
about the file's physical shape:

1. Values must be **literal 6-digit hex**, in those blocks. A token expressed
   as `color-mix()` or `var()` is invisible to the regex — which is why
   `--color-scrim` is not covered today.
2. Token names must keep the `--color-` prefix.
3. A token that moves out of the file does not degrade quietly: `tokens[fg]`
   becomes `undefined`, `hexToRgb` calls `.slice(1)` on it and throws. The
   failure is loud, which is the one mercy here.

The test is right to work this way — its own header explains that `jest-axe`'s
`color-contrast` rule cannot help, because jsdom has no layout engine and
`vitest.config.ts` runs with `css: false`, so axe reports "incomplete" forever
and never discriminates light from dark. Parsing the real bytes is the only
honest option.

**So `tokens.css` stays the source of truth and derivation runs outward.**
The spine is not a file that `tokens.css` imports; it is a contract that
`site/styles.css` is held to.

## The precedent to copy

Guardian has already solved "two files that must agree and cannot import each
other." From `CLAUDE.md`:

> The hub cannot import `guardian_sensor` — it is the sensor's own package,
> installed on the sensor host — so `remedy`'s fix tables are ported verbatim
> from `fix.FIX_ALERT_KEY`. `hub/tests/test_remedy.py` parses
> `sensor/guardian_sensor/fix.py` and fails when the two drift.

Colour is the same shape of problem. The fix is the same shape of answer: a
test that parses the other file and fails on divergence. No build step, no
generated CSS, no new dependency — and it reads as something this codebase
already does rather than a convention imported from outside.

## The spine

### Palette roles

The site is the **console skin**. Every site token has a counterpart role in
the portal. This table is the *vocabulary* — which site token means what — and
it holds regardless of which drift-test shape is chosen below; what varies is
how much of it the test enforces as equality:

| Role | `site/styles.css` | `tokens.css` (dark) | Notes |
|---|---|---|---|
| Page ground | `--bg` | `--color-bg` | |
| Recessed surface | `--panel` | `--color-surface` | Down from the page, never lifted |
| Hairline | `--line` | `--color-border` | |
| Primary text | `--fg` | `--color-text` | |
| Muted text | `--dim` | `--color-text-muted` | |
| Interactive accent | `--acc` | `--color-primary` | Light blue for text on dark |
| Accent ink | `--accink` | `--color-on-primary` | |
| Brand blue (fill) | `--deep` | `--color-primary` **(light skin)** | The one exact match today |
| Good | `--ok` | `--color-good` | |
| Warning | `--warn` | `--color-warn` | |
| Alert | `--crit` | `--color-alert` | |

**The brand blue is skin-independent.** `#1257c4` is `--color-primary` in the
portal's *light* skin and `--deep` on the dark marketing site — the only value
the two files agree on exactly. It works in both places because it is used
differently: on a light ground it is the interactive colour; on a dark ground
it is a saturated *fill* carrying white text (6.59:1). The dark skin has no
equivalent, because on dark the interactive colour lightens to `#66a6ff`
instead.

That is the spine's real shape: one brand blue, two jobs, chosen by ground.

**And a third thing it is not: a mark on the dark ground.** `#1257c4` is 2.95:1
against `--bg`. That is the pair a naive contrast audit flags and this document
dismisses as a false positive — correctly, because the site draws it only as a
button fill with white on top. The dismissal is about what the site *does*, not
a licence: the moment anything paints `--deep` as a dot, a stroke, a ring or a
point of light on `--bg`, the false positive becomes a true one.

This is recorded because it was got wrong here after being written down. The
scene behind "How it works" shipped painting its resting devices in `--deep` and
was effectively invisible — 0.66% of the canvas lit. The mark colour on this
ground is `--acc` at 7.82:1, which is what the skin already uses everywhere a
thing has to read; `--fg` at 16.82:1 is available above it when something must
read as *lit* rather than merely present. Both were measured, not judged.

### Type

Manrope (variable, 400–800) for text; IBM Plex Mono for every number,
latency, MAC, IP, timestamp and code value. Both self-hosted, latin subset,
`font-display: swap`, explicit `unicode-range` so a missing glyph falls
through to the system stack instead of rendering as tofu. This is settled and
not reopened — see the offline rule below.

### Status triad

Red = alert, green = good, amber = warning, and **these are the only
saturated colours on any Guardian surface**. Blue is structure and
interaction, never status. A product whose whole job is to say what is wrong
cannot afford a second red.

Two strengths, and the distinction is load-bearing:

- `--color-good` / `-warn` / `-alert` — the **mark** colour: dots, strokes,
  ring arcs, borders, filled grounds.
- `--color-good-text` / … — the same hue darkened enough to clear AA **as
  text on its own `-soft` ground**.

Pairing a mark colour against a soft ground tests something the app never
draws. The existing test says so in a comment; the site's guard must inherit
the same care.

### Motion

The one place the three surfaces deliberately differ.

- **`guarder.io`** — cinematic. Its job is to arrest a stranger in four
  seconds. WebGL scenes, gradients and video are appropriate here, subject to
  the vendoring rule.
- **`my.guarder.io`** — instrument panel. `--shadow-glow: none`, radii 2–4px,
  hairline separators, surfaces recessed rather than lifted. Someone opens
  this because something is broken; motion that competes with the alert is
  motion that costs the owner time.
- **`admin.guarder.io`** — inherits the portal's restraint, denser still.

Every surface honours `prefers-reduced-motion`. On the marketing site that
means the hero degrades to its poster frame, not to a blank box.

### Density

The console skin is denser than the calm skin by design — `--pad-card` and
`--font-size-base` are skin-specific for this reason. The admin portal is
denser again: it is read by one operator who lives in it, not by an owner
visiting twice a week.

## Decisions taken

**The marketing site stays dark-only.** Dark is the right register for a
security product's front door, it is what Layers' scenes and gradients are
built to sit on, and a light skin would need a light variant of every
borrowed asset — which is where the real cost lands, not in the CSS.

**Strict portal separation.** `my.guarder.io` is for clients. Platform
superadmin work moves to `admin.guarder.io`, and the superadmin controls
client tenants from inside the admin portal. The tenant admin — the customer's
own admin, who buys the subscription — stays in the client portal; billing is
theirs (`pages/Settings/index.tsx:52` already says so).

The superadmin surface currently sitting in the client bundle is bounded:

| Surface | Location |
|---|---|
| Leads (whole page) | `web/src/pages/Leads.tsx` |
| Tenants CRUD section | `web/src/pages/Settings/index.tsx:237` |
| Superadmin tenant API | `web/src/lib/api.ts:599` |
| Enrol-code call | `web/src/api.ts:124` |
| Cross-tenant switching | `web/src/shell/Shell.tsx:237` |

For a security product this is not only tidiness: admin UI shipped to a client
browser is a disclosure surface, and `CLAUDE.md` already rules that Guardian's
own output must be treated as one.

## The offline rule, restated

Every Guardian surface is first-party only. Grep either app and the sole
external origins are `guarder.io`, `my.guarder.io` and `api.guarder.io`.
`fonts.css` carries the reason: Guardian has to render on an isolated or
WAN-down network, and a dashboard that goes blank when the uplink dies is
worse than no dashboard, because the owner reads the blankness as "nothing is
wrong."

Layers assets therefore ship **vendored**: fonts downloaded to the repo,
video and posters downloaded (their signed links expire in about an hour and
fail by painting a blank element, not by erroring), WebGL inlined, and tint
driven from these tokens rather than the library's own values. Recorded in
full in `CLAUDE.md` § Design assets.

## The reconciliation question

A drift test that asserts byte-equality cannot be written against the files as
they stand — it would fail on all eleven roles on the first run. Measured
distance between the site's value and the portal's dark-skin value (plain sRGB
distance; small numbers are imperceptible, large ones are a visible change of
hue):

| Token | Site now | Portal dark | Distance |
|---|---|---|---|
| `--deep` | `#1257c4` | `#1257c4` (light skin) | **0.0** |
| `--bg` | `#080d17` | `#070d18` | 1.4 |
| `--fg` | `#e9eff8` | `#e9f0fa` | 2.2 |
| `--panel` | `#0e1524` | `#0d1626` | 2.4 |
| `--accink` | `#08111f` | `#04101f` | 4.1 |
| `--acc` | `#6ba5ff` | `#66a6ff` | 5.1 |
| `--dim` | `#8da0bc` | `#91a7c4` | 11.4 |
| `--line` | `#1e2b42` | `#21354f` | 16.7 |
| `--ok` | `#46d18b` | `#4fbb85` | 24.5 |
| `--warn` | `#f2a63c` | `#d99a4e` | 33.1 |
| `--crit` | `#ff6b5e` | `#e2647c` | 42.3 |

The split is clean and it is not arbitrary. **The neutrals have drifted by
rounding** — nobody would see `#080d17` become `#070d18`. **The status triad
has drifted by intent**: the site's alert is an orange-red, the portal's a
pink-red, and swapping one for the other is a visible change to the marketing
site, not a tidy-up.

Adopting the portal's dark values wholesale is *safe* for accessibility —
every pair the site draws still clears AA, worst case 5.47:1 (`--crit` on
`--panel`, down from 6.53). So the decision is aesthetic, not technical, and
it belongs to the owner rather than to this document.

## What Phase 1 builds

**Decided: invariant-only.** No pixel of the live marketing site changes as a
side effect of adding a test, and Phase 2 is about to redesign that site
anyway — reconciling now would be restyling it twice.

1. **A drift test** — `web/tests/brandspine.test.ts`. Pins the brand blue
   (`--deep` == light-skin `--color-primary` == `#1257c4`, each asserted
   against the literal so a commit that moves *both* files still trips it),
   asserts every spine role is present, and leaves per-surface tuning free.
   Mirrors `hub/tests/test_remedy.py` — parse the other file, fail on
   divergence — and reads across the boundary the same way `hub/tests` reads
   `sensor/`.

   **Why the status triad is not pinned.** Investigating this turned up the
   reason the two palettes diverged, and it is not sloppiness. The portal
   splits every status colour in two — a *mark* strength for dots and strokes,
   and a darker `-text` strength that clears AA on its own `-soft` ground. The
   site has no such split, because it has exactly one ground: it paints
   `--ok`/`--warn`/`--crit` both as marks (`.rail-dot`, `.gauge-bar`) and as
   text (`.badge.ok`, `.price.free`, `.form-msg.crit`). One token doing both
   jobs on a dark ground *has* to sit lighter and more saturated than one that
   only ever works on a dedicated soft ground. `#ff6b5e` against `#e2647c` is
   two correct answers to two different questions.
2. **A contrast test for the marketing site** — it has none. The pairs it
   actually draws were verified by hand on 2026-09-18 and all pass; the point
   is to stop that being a manual check. This one is unconditional: it is
   worth having under either shape above.
3. **This document**, as the thing a fourth surface reads before it is built.

### Proof the guard discriminates

A passing test is not evidence it can fail — `CLAUDE.md` § Tests records the
N+1 guard that "could not have caught the regression it existed to catch". So
the guard was run against three injected regressions on 2026-09-18, with
`site/styles.css` restored byte-identical afterwards:

| Injected | Result |
|---|---|
| Brand blue nudged `#1257c4` → `#1457c8` | 1 failed / 14 passed |
| `--dim` deleted outright | 3 failed / 12 passed |
| `--dim` darkened below AA (`#46546b`) | 2 failed / 13 passed |
| Restored | 15 passed |

The first is the one that matters: an eight-hundredths-of-a-percent shift in
the brand blue is invisible to review and caught here.

### Verified state at time of writing

The marketing site is AA-clean on every pair it renders. One pair looked like
a failure and is not: `--deep` on `--bg` is 2.95:1, but `--deep` is used
exactly once, as a button *background* with `#fff` on it, and that real pair
is 6.59:1. Recorded because the next person to run a naive audit will hit the
same false positive.

| Pair | Ratio |
|---|---|
| `--fg` on `--bg` | 16.82 |
| `--fg` on `--panel` | 15.78 |
| `--dim` on `--bg` | 7.30 |
| `--dim` on `--panel` | 6.85 |
| `--acc` on `--bg` | 7.82 |
| `--ok` on `--panel` | 9.34 |
| `--warn` on `--panel` | 8.94 |
| `--crit` on `--panel` | 6.53 |
| `#fff` on `--deep` (button) | 6.59 |

## Programme sequence

| Phase | Surface | State |
|---|---|---|
| 0 | Layers ↔ Claude MCP wiring | **Done** — `.mcp.json` written; awaiting the owner's OAuth approval |
| 1 | The brand spine | **This document**, plus the two guards above |
| 2 | `guarder.io` | Cinematic treatment via Layers. See the locale constraint below |
| 3 | `admin.guarder.io` | New surface; migrate the five superadmin surfaces above out of the client bundle |
| 4 | `my.guarder.io` | Last on purpose — most mature, in daily use, highest risk and lowest return to touch early |

## Phase 2's locale constraint, verified

`site/index.html` is the single hand-edited English source. The other eight
locales are *generated* from it by `site/i18n/build_locales.py` (2888 lines,
run by hand) through literal and regex substitution keyed on the English
strings, and committed as plain files — nginx never runs Python. The script's
own header explains why this beat a client-side JSON string table: a Hindi
reader would otherwise get a flash of English on every load, and with
JavaScript off would never get Hindi at all.

Two consequences for a redesign:

1. **Never hand-edit `site/<locale>/index.html`.** Edit the English copy and
   re-run `python3 site/i18n/build_locales.py`.
2. **Rewriting copy breaks the tables, loudly.** Each substitution is guarded
   by `assert en in doc, f"LITERAL[{key!r}] not found ... English source
   moved"`, so a changed string halts the build naming the key rather than
   silently shipping eight English pages. Structural markup changes are
   comparatively cheap; *copy* changes are the expensive ones, and Phase 2
   should budget for editing the tables in lockstep.

The script also carries a caveat worth honouring: every non-English string in
it was produced by an LLM, not a native speaker, and is a first draft rather
than a proofread translation.

## Where a redesign is cheap, measured

Phase 2's real budget is not markup, it is *copy*. Every string the generator
pins is assert-guarded against `site/index.html`, so rewriting one means
editing eight translations in a 2888-line table; moving markup around costs
nothing. Counting the 115 pinned strings per section (110 in the body, 5 in
`<head>` — title, description, the OG pair and the skip link) gives the map:

| Section | Lines | Pinned strings | Redesign cost |
|---|---|---|---|
| `capabilities` | 12 | 0 | free |
| `live` | 59 | 1 | **free, and large** |
| `caps` | 17 | 3 | low |
| `how` | 41 | 4 | low |
| `pricing` | 103 | 5 | low per line |
| `problem` | 27 | 6 | medium |
| `fixes` | 56 | 6 | medium |
| `faq` | 64 | 6 | medium |
| `watches` | 61 | 7 | medium |
| `evidence` | 26 | 9 | medium |
| `who` | 28 | 11 | medium |
| `top` (header) | 60 | 12 | medium |
| `integrations` | 48 | 15 | **high** |
| `access` | 114 | 25 | **high** |

The standout is `live`: 59 lines of structure carrying a single pinned string,
because it is the telemetry rail and its content is numbers rather than prose.
It is simultaneously the cheapest section to rebuild and the most thematically
honest place to spend motion — a security product's front page showing real
movement in its own data, rather than decoration bolted onto a static hero.
`capabilities`, `caps` and `how` are similarly cheap.

`access` and `integrations` are the opposite: 40 of the 110 body strings sit in
those two. Restructure their *layout* freely; leave their words alone unless
the copy change is itself the point.

This is a budget, not a prohibition. A copy rewrite that improves the site is
worth eight translation edits — the generator will name every key that moved.
What it rules out is rewriting copy *incidentally*, as a side effect of moving
a section, and discovering the cost at build time.
