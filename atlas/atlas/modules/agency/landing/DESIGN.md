# Mapfront landing page: design notes

Not served. Caddy returns 404 for `/DESIGN.md`, `/getlayers.json` and
`/_review/*` (the `@unlisted` matcher in the `mapfront.io` block).

## Brief

Mapfront is Miro's New York City web agency for small local businesses:
restaurants, salons, contractors and shops. The pitch is simple. We look at
your current web presence, build you a real site and show you a free preview,
and you pay only to launch it. There is one public offer, the Website Package
at $500. The audience is a busy owner, usually on a phone, who got here from a
call or an email. They need to trust the page fast and see what they would
get.

Honesty rules the copy. There are no testimonials, ratings, counts, client
names, logos or invented figures. The only digits on the page are `$500`, and
`tests/test_mapfront_landing.py` enforces that.

## The idea: the map front

The page is a street. The hero is a row of **storefronts**, the package is a
**block map** with a pin on every part, and the process is a **subway line**
with lettered stops. Each section gets one metaphor, and none of them is
mixed in as decoration.

## Plan

### Palette

| name | hex | role |
|---|---|---|
| Teal | `#0f3d3e` | brand ground (hero, footer), ink on light |
| Marigold | `#f4b942` | the one accent: pins and the primary action, fills only |
| Curb | `#eef2ef` | page ground (cool grey-green, deliberately not cream) |
| Street | `#ffffff` | cards, and the streets between blocks on the map |
| Block | `#dfe8e3` | a city block on the map |
| Slate teal | `#465f5f` | muted text (6:1 on white) |

Dark mode swaps in a night map: ground `#0b2223`, blocks `#16393a`, ink
`#eef4f2`, focus rings in marigold. Teal on marigold is about 7:1 and white on
teal about 12:1. Marigold is never used as text on white.

### Type

The page uses one family, **Archivo** variable (wdth 62 to 125, wght 100 to
900), OFL. It is subset to Latin basic and self-hosted as
`fonts/archivo-var.woff2` (45 KB).

- **Wordmark:** 900, normal width, lowercase, tight.
- **Display:** 800 at `font-stretch: 72%`, which reads as awning and
  street-sign lettering. This condensed voice is the page's personality.
- **Shop signs:** 850 at 64%, capitals, because a sign is an illustration and
  not a label.
- **Body:** 400 at 100%, 17px, line-height 1.55. Small labels use 600 to 700 at
  85 to 90%, in sentence case.

### Layout

Desktop:

```
HEADER  [pin mapfront]                      The package  How it works  (Contact)
HERO    ┌──────────────────────────────┬─────────────────────────┐
(teal)  │ [pin] Websites for NY small… │ ║BAKERY ║  ║NAIL SALON║ │ two lanes of
        │ A website                    │ ║BARBER.║  ║TAQUERIA  ║ │ storefronts,
        │ that works as                │ ║PLUMBER║  ║HARDWARE  ║ │ opposite
        │ hard as your                 │ ║FLORIST║  ║LAUNDROM. ║ │ directions
        │ storefront.                  │                          │
        │ lead                         │ Restaurants, salons, …   │
        │ (Ask for a free preview) (See what you get)             │
        │ ───────────────────────────                              │
        │ Preview     Price        Launch                          │
        └──────────────────────────────┴──────────────────────────┘
PACKAGE  The Website Package            lead, hung off the heading's baseline
(map)   ┌──────────────┬────────┬──────────────┐  white gaps = streets,
        │ pages     •  │ call • │ $500 LOT   • │  one diagonal "Broadway"
        ├──────────────┤ bar    │ (marigold)   │  shows only in the gaps
        │ search    •  │ phone  │ not part of  │
        ├─────────┬────┴───┬────┴──────────────┤
        │assist • │share • │ launch check •    │
        ├─────────┴────────┴───────────────────┤
        │ only what is true •                  │
        └──────────────────────────────────────┘
ROUTE    How it works                    Three stops…
         (A)━━━━━━━━━━━━(B)━━━━━━━━━━━━(C)━━━━
         look          preview         decide
PREVIEW  See it before     ┌ browser: "Your Bakery" ┐┌phone┐
         you pay for it.   │ sample, clearly marked  ││call │
         [Sample only: a made-up Queens bakery]      └─────┘
CLOSE    Want to see yours first?                 (Ask for a free preview)
(teal)   ───────────────────────────────────────────────────
         Talk to us: miro@mapfront.io   Mapfront, tagline, [postal address]
         m a p f r o n t   (cropped by the bottom edge, 6% ink)
```

Phone (360 to 640px): the headline comes first, then the storefront lanes turn
horizontal and scroll sideways under it, then the lead, actions and ledger.
The map becomes one column with the price block first. The route line turns
vertical. A fixed thumb bar (free preview + Package) sits at the bottom, the
same call-bar pattern the package puts on client sites. Content is left
aligned throughout. The only centred element is the phone mock inside its
block.

### Principles

1. One metaphor per section: storefronts, map, subway line. No decoration that
   isn't one of them.
2. Marigold means a place to go: a pin or the main action, never text on
   white.
3. Honesty is visible. Samples are labelled as samples, the price is stated
   once, and there are no other numbers.
4. One moving thing. The storefront lanes drift slowly, pause on hover and
   stop entirely under `prefers-reduced-motion`. Everything else is still.
5. Built for the thumb. The action is always within reach on a phone, and
   focus is always visible (a teal ring on light, marigold on teal).

## Critique against the generic defaults, and what changed

- **Cream + serif + terracotta; near-black + acid accent:** not used. The
  ground is a cool curb grey and the brand teal carries the dark surfaces.
- **SaaS card kit (identical rounded cards, one radius, soft grey shadows):**
  this was the main risk in the bento. Revised so the blocks sit on a white
  street grid with no shadows. Blocks vary in size, the price is a different
  material (the marigold lot), and radii step by role: 32 page, 24 frame, 12
  block, pill controls.
- **Template chrome (tracked caps eyebrows, mono labels, `→` on buttons, middle
  dots):** Ridgeline's Geist Mono labels were **dropped**, and labels are
  condensed Archivo in sentence case. The only eyebrow is the street-sign
  plate in the hero. Buttons carry no arrows.
- **Numbered 01/02/03 markers:** first drafted, then **replaced** by subway
  letters A B C. The steps really are a sequence, the letters are NYC
  vernacular, and the page may carry no digits.
- **Scattered motion:** a second horizontal ticker marquee was considered and
  **cut** (Chanel's last accessory). The hero lanes are the single motion
  moment.
- **Map pins in the hero:** an early plan put the package pins in the hero.
  They **moved** to the package map, so each section keeps one metaphor and the
  assistant is named only inside the package section, which the tests require.

Review notes from building: `$500` first rendered at body size because
`.b-price p` outranked `.price`, fixed with `.b-price .price`. Pins overlapped
the mini figures, fixed by reserving a right gutter in `.fig`. The phone hero
buried the storefronts below the fold, fixed by reordering with
`display: contents`. Dark mode lost the teal chips on dark blocks, fixed with a
`--chip` token. A body `padding-bottom` for the thumb bar left a light strip
under the teal footer, so the space now lives inside the footer.

## What came from where

### frontend-design skill

- The process: a compact plan (palette, type, ASCII layout, principles), a
  critique against its list of generic defaults, revisions, then the build,
  with screenshot review passes at 1440, 390, 360 and in dark mode.
- The rules the page follows:
  - one typeface family, used as an active element (the condensed awning
    voice)
  - no single-word headline accents
  - no all-caps labels
  - numbering only for a true sequence
  - one orchestrated motion
  - reduced motion respected
  - visible focus
  - contrast checked
  - responsive down to 360px with no horizontal scroll (verified
    `scrollWidth == innerWidth` at 360, 390 and 1440)

### GetLayers (MCP)

- **Route:** `getlayers_start` → mode "build-site", route (c) build unique,
  target `other`. The state is in `getlayers.json`.
- **Style:** `getlayers_plan` and `getlayers_search` surfaced `ridgeline-style`,
  written for local trades and small businesses as calm, plain-spoken and
  honest. It was committed and re-themed: navy to teal, marigold added as the
  accent, and the type swapped to Archivo. `getlayers_palettes` had no ramp
  closer than the brand's own pair, so the brand hexes were kept.
- **Template:** `getlayers_materialize(ridgeline, styleId=ridgeline-style,
  target=other)` was used for page rhythm and shape language: card on a field,
  the 32/24/20/12 radius ladder, pill controls, and a dark footer with an
  oversized wordmark. Nothing was copied. Its sections run on React springs,
  and its media is roofing photography that has nothing to do with Mapfront, so
  none of it was downloaded. No `preserve` layer (motion, composition) was
  rewritten, because none was taken.
- **Compositions, one per section** (`getlayers_compositions`):
  - hero: `artist-hero` (6|6 split, copy column with a fact ledger beside a tall
    plate)
  - package: `artist-acquire` head row (heading and lead sharing a bottom edge)
    over the bento
  - how it works: `helion-timeline` (stations on one shared line; vertical on
    phones)
  - preview: `creative-director-portfolio` split with an `ai-studio-product-card`
    browser mock
  - close and footer: `lumora-footer` (inverted slab: CTA row, column bank,
    wordmark cropped by the bottom edge)
  - considered and rejected: the marquee compositions (`vexon-marquee`,
    `clarix-clients`, `stride-logo-marquee`), all logo or trust walls, which is
    exactly the claim this page must not make.
- **Scene, gradient or background:** none. Each one needs WebGL and a script,
  and the CSP plus `test_no_client_logos_images_scripts_or_external_requests`
  keep this page script-free. The atmosphere comes from the CSS Manhattan
  street grid, rotated 29 degrees, behind the teal hero.

### Webild ("Web Agency" template, public page, used for structure only)

- **Vertical marquee hero:** adapted as two lanes of drawn storefronts showing
  the **kinds** of businesses Mapfront builds for (Bakery, Plumber, Nail salon
  and so on). These are not client names.
- **Bento services grid, each card carrying a small UI illustration:** adapted
  as the package map, with tabs for the pages, a phone with a call bar, a
  search snippet, a share card, an assistant exchange and a launch checklist.
- **Work showcase carousel:** replaced by one illustration of what a preview
  looks like, a made-up bakery labelled "Sample only: a made-up Queens bakery,
  not a client." There is no business name that could be mistaken for a real
  one, no hours, no ratings and no URL that implies hosting.
- **Team cards:** omitted, because there is no honest team to show. The
  "Award-Winning Agency" badge style was not borrowed either.
- No Webild assets, code or copy were used.

## Mechanics that must survive edits

- **Postal address:** Caddy's `templates` renders it inside the footer's
  Mapfront column from the environment variable `AGENCY_POSTAL_ADDRESS`,
  HTML-escaped. When it is unset, a TODO comment renders and nothing is shown.
  The file holds only that one template expression. Never add any other double
  curly braces to `index.html`.
- **The assistant:** it may be named only inside
  `<section class="band alt" id="package">`, and no `<section>` may be nested
  in it. The word must not appear anywhere else in the body, including class
  names and comments.
- **Required phrases:** "a home page, a contact page, privacy and terms pages"
  and "no online checkout" must each stay in one text node.
- **Brand checks:** `<span class="wordmark">mapfront</span>` and the icon
  `<link>` are checked verbatim.
- **CSP:** the only change was adding `font-src 'self'`. Everything else in the
  header is unchanged.

## Review screenshots (`_review/`, not served)

- `desktop-hero.png`: 1440×900, first screen.
- `desktop-full.png`: 1440, full page.
- `mobile-390.png`: 390×844 at 2x, the first phone screen with the thumb bar.
- `mobile-390-full.png`: 390, full page, downscaled. In a full-page capture the
  fixed thumb bar appears at its first-screen position.

All were captured with reduced motion on, so the lanes hold still, and with
`AGENCY_POSTAL_ADDRESS` unset. They were served under the production CSP (as a
meta tag) with no console violations.
