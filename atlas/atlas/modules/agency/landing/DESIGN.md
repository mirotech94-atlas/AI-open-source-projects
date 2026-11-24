# Mapfront landing page: design notes

Not served. Caddy returns 404 for `/DESIGN.md`, `/getlayers.json` and
`/_review/*` (the `@unlisted` matcher in the landing handle in Caddyfile.public).

## Brief

Mapfront is Miro's New York City web agency for small local businesses. We look
at your current web presence, build a site, show a free preview, and you pay
only to launch it. One public offer: the Website Package at $500. The reader is
a busy owner on a phone who needs to trust the page fast.

Honesty rules the copy: no testimonials, ratings, counts, client names, logos
or invented figures. The only digits on the page are `$500` and the phone number
`+1 555 010 0142`; `tests/test_mapfront_landing.py` enforces that.

## Direction: ATLAS royal blue, dark only

A calm, premium dark page in the ATLAS house look (same tokens as
`atlas/modules/academy/lesson_thumb.py` and the Academy store). Type and space
do the work; royal blue is the one accent. There is no light scheme and no
`prefers-color-scheme` query: `color-scheme: dark`, `theme-color #0a0d14`.
The old cream/teal/brown rate card is gone. Structure and copy are unchanged.

### Palette

| name | hex | use |
|---|---|---|
| Ground | `#0a0d14` | page, form fields, theme-color |
| Band | `#0f1420` | contact + footer band |
| Panel | `#131a28` | package section, phone bar |
| Royal blue | `#5b8cff` | pin, primary buttons, A-B-C bullets, price rule, link underline |
| Light blue | `#8fb0ff` | button hover, focus ring, form error, hairline accents |
| On-accent | `#070a12` | text on blue (6:1+) |
| Text | `#eef2fa` / `#c9d2e3` | body and band text |
| Muted | `#9aa5ba` | secondary text (AA on ground and panel) |

Hairlines are light blue at 16 % (`--line`) and 34 % (`--line-strong`). No
gradients, glows, text-shadow, backdrop-filter. Depth comes from one soft dark
shadow plus a 1 px blue edge on the hero sample frame. The sample bakery keeps
three colours of its own (`sample-colours:` comment); it is a made-up client
site, not the brand. The icon `mapfront-icon.png` is the same pin and word in
ground and royal blue.

### Type (Archivo only, condensed width for h1, h2 and the price)

| role | size | weight / width |
|---|---|---|
| h1 | clamp(2.75rem, 6.4vw, 5.5rem) | 750 / 76% |
| h2 | clamp(2rem, 4vw, 3.25rem) | 700 / 80% |
| price | clamp(5rem, 11vw, 9rem) | 800 / 72% |
| h3 | 1.375rem | 650 |
| body | 1.125rem, line 1.6 | 400 |
| small | 0.9375rem | 600 |

### Layout

12-column grid, 75rem max. Hairline rules and columns, no cards. Section gaps
differ on purpose (large before the package, medium before the steps, small
before the statement) and halve on phones. Radius: 6 px controls, 10 px sample
frame, 0 on bands.

Order: header, hero (copy 7 | sample 5), one typographic trades line, package
(sticky price column + seven ruled rows), how it works (three steps on one
line), one-sentence statement, contact + footer band.

### Motion

One `@keyframes` (hero copy and sample rise 8 px over 400 ms on load), 150 ms
colour transitions. Nothing on scroll, nothing looping. All off under
`prefers-reduced-motion: reduce`.

### Imagery

No `<img>`, no photos. One drawn sample site in a browser frame with a phone
over it, using real words and labelled "Sample: a made-up Queens bakery, not a
client." No digits, prices or claims inside the sample.

### Contact rule

Only `owner@example.com` and+1 555 010 0142 (`tel:+1 555 010 0142`). The form
is a mailto; nothing is sent until the visitor presses send in their mail app.
The phone bar (640 px and narrower) has Call and Free preview, each 48 px tall.

## Mechanics that must survive edits

- **Postal address:** Caddy's `templates` renders it in the footer row from
  `AGENCY_POSTAL_ADDRESS`, HTML-escaped. Unset renders a TODO comment. The file
  holds only that one template expression; never add other double curly braces.
- **The assistant:** named only inside `<section class="band alt" id="package">`
  (no nested `<section>`); "chat" appears nowhere else in the body.
- **Required phrases:** "a home page, a contact page, privacy and terms pages"
  and "no online checkout" each stay in one text node.
- **Brand checks:** `<span class="wordmark">mapfront</span>` and the icon `<link>`.
- **Digits:** only `$500` and `+1 555 010 0142` (and the email) in visible text,
  none in any `alt`, `aria-label` or `title`.
- **Banned class names:** blk, map, lane, shop, bleed, street, loaves.
- **CSP:** inline styles, same-origin fonts and images, no script (the JSON-LD
  record is data), `form-action mailto:`.

## Review screenshots (`_review/`, not served)

`desktop-hero`, `desktop-full`, `desktop-dark`, `mobile-390` (true 390x844 via
an iframe wrapper; inner width logged 390), `mobile-390-full`, `mobile-360`,
`thanks-desktop`, `thanks-390`. Captured with headless Chrome from the
worktree, served on 127.0.0.1 with the production CSP as a meta tag.
