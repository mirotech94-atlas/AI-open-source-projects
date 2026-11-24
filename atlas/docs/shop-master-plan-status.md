# Shop master plan: what was built, what waits, what was declined

Source: "From buying a file to finishing a project. Shop experience,
functionality and product roadmap" (prepared for the owner, 13 Oct 2026, 29 pages,
features F01 to F56). Read in full on 15 Oct 2026 against
`atlas/modules/academy/` (site.py, app.js, style.css, offer_panels.py,
support_site.py, catalog.json, publish.py).

Rules that override the plan where they conflict, and the reason each
"declined" row names:

* **Honesty**: no invented number, rating, testimonial, count or logo
  (CLAUDE.md invariant 6 and the store's own rules in site.py). A value the
  catalog does not state is not shown, or is shown as "Not stated".
* **Prices**: null stays null. Payment is the existing Stripe Payment Links
  only (money is Tier 3 for ATLAS).
* **No tracking**: no analytics, no cookies, no device storage, no
  third-party script. The privacy page promises that `app.js` stores nothing
  and makes no request.
* **Static**: agency.example.com/academy/ is static output built on a one-CPU server.
  Anything that needs accounts, sessions, a database view per buyer, email
  sending or a cart waits for a backend (listed under Later, not faked).
* **Brand**: dark only, black + royal blue (The owner, 2026-10-11 and 10-12).

Status words: **Built now** (this branch), **Exists** (already on the store
before this branch; kept), **Later** (needs a backend, content or a decision
from the owner), **Declined** (conflicts with a rule above).

## Summary

| Status | Count | Features |
|---|---|---|
| Built now | 21 | F01, F02, F03, F04, F05, F07, F09, F10, F11, F12, F15 (pilot), F16, F17, F18 (partial), F19 (partial), F20, F24, F33, F37, F47, F54 (partial) |
| Exists, kept | 12 | F08, F21, F23, F26 (base), F27 (base), F29 (base), F30, F34, F35, F36, F39, F40 (visitor state) |
| Later | 18 | F06, F13, F14, F22, F25, F28, F31, F41, F42, F43, F44, F49, F50, F51, F52, F53, F55, F56 |
| Declined | 5 | F32 (analytics part), F38, F45, F46, F48 |

(F15, F26, F27, F29, F40 and F54 appear once, in the row that best fits;
each feature's own row below gives the detail.)

## Every feature

### Page 2 consistency checks

| Check | Status | What / why |
|---|---|---|
| One canonical channel handle | Later (The owner) | The store links @example_handle everywhere (`site.CHANNEL_URL`, one constant). Whether @example_handle is the intended name is the owner's call; question 1 below. |
| One support boundary everywhere | Later | Product pages render `catalog.json` `support`; the Help page renders `support_kb`'s policy. Both say custom setup is paid implementation work, but in different words. Making Help render the same `support` block is a change to the support lane's page (support_site.py), left to that lane. |

### Homepage, catalog, recommendation (plan sections 03-04)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F01 | Clearer homepage (P0) | Built now | Hero actions are now "Find my starting point" and "Explore free lessons". New order: hero, choose a goal (paths), the finder, one free lesson (when published), the searchable catalog, the comparison, the approval demonstration, the bundle, the owner, buying and access (now four steps, the fourth is getting a lost access page back) with the FAQ, close. Every old `#catalog`, `#finder`, `#compare`, `#bundle`, `#about` and `#buying` link still lands. Not done: replacing the shelf with "one large project result" (needs a real result image from the owner) and a separate /explore/ page (seven titles fit one page). |
| F02 | Useful catalog controls (P1) | Built now | Text search (titles, outcomes, kind, level, format, tools needed, with a small synonym list such as workflow/automation), goal chips, level and format filters, a live result count, Clear all and an empty state that suggests the free lessons. The query and filters are mirrored into the address (`?q=&path=&level=&format=`), so a filtered view can be shared. Without the script the whole catalog shows. |
| F03 | Comparison that answers decisions (P1) | Built now | New columns from catalog data only: "Last checked" (the newest `tested` date, or "Not stated") and refunds. The support scope is one policy for every title, so the section links it once instead of repeating a column. A chosen set is shareable as `?compare=slug,slug` (product ids, never personal data). Ongoing tool costs and first-project time stay in the existing "You need" column, which is the catalog's own text (for example "about an hour"); nothing is estimated. Mobile already renders each row as a card. |
| F04 | Explainable finder (P0) | Built now | Goal, comfort level and a new setup question ("can you install software?"). Eligibility first: a title whose requirement the answers rule out (Python, Docker, installing an app) is set aside and the page says why, from the catalog's own requirement text. Then one best starting point and up to two alternatives, in an explicit editorial order, each with why it fits. No email. With nothing eligible it shows the free route (a free lesson and the free worksheet) instead of a paid title. Acceptance example tested: business + new + no installs never leads with the Python course or the Docker pack. |
| F05 | Central lesson-to-product mapping (P0) | Built now | The store had two tables that disagreed (`SERIES_NEXT` paired the Claude/MCP series with AI for Every Age, plan finding [B]; `SERIES_PRODUCTS` paired it with the Vault Kit). Now one table, `LESSON_PRODUCTS`: series to product with a written relevance reason, or "no paid recommendation". The home page, finder, product pages and /videos/ all read it, and a test fails if a reason is missing or a slug is unknown. |
| F06 | Saved learning route (P2) | Later | Print/copy of a finder route is possible statically, but saved progress needs storage or accounts. Device storage is declined (privacy promise); accounts need a backend. |

### Product page (plan section 05)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F07 | One consistent product template (P0) | Built now | Every product page now opens (first in its on-page nav) with the same "Before you pay" block answering the plan's four acceptance questions: what you receive (the Format row, link to every file), can you run it (level, the catalog's "You need" and "Tested on" rows or the last-checked date, who it isn't for), what else costs money (the title's one-time price, plus a cost row such as "Claude plan" when the catalog has one; otherwise "this title's catalog entry lists no other cost"), and how you get your files back. The rest of the template already existed. |
| F08 | Meaningful preview (P1) | Exists | The sneak peeks (first minute of a lesson, real pages, a kit's real file list and README) are cut from the delivered files. A per-product interactive mini-task is Later (content). |
| F09 | Requirements and cost panel (P0) | Built now (as part of F07) | Suitable / not suitable, device and setup from the catalog. A separate purchase-vs-running-cost table needs cost data per product that the catalog does not hold yet: **question for the owner** (see below). |
| F10 | Edition selector with exact contents (P0) | Built now | The page no longer pre-selects the dearest edition under a "From" price. With the script, no edition is chosen until the visitor picks one, and the Buy, price and file list appear for that edition only; without it, every edition shows with its own Buy, as before. The Buy link is each edition's own Stripe Payment Link, so page and checkout agree on edition and amount. |

### Free lessons and learning (plan section 06)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F11 | Individual lesson pages (P1) | Built now | `lessons/<series>-<n>/` for every public tutorial (the same list as /videos/; none for anything unpublished), from `lesson_pages.py`: outcome, level, what you need, the click-to-load video exactly as /videos/ plus "Watch on YouTube", length, chapter links and the transcript (the narrated lines, cues stripped, as the captions show them) from the published render's manifest, trusted only when it is Fish-narrated and rendered no later than the video went public; without one, no length or chapters and the script shown as "Lesson script"; the matching LESSON_PRODUCTS card while it is on sale, and a JSON-LD VideoObject and canonical URL. In the sitemap; each /videos/ card links its page. Still Later: per-lesson notes and progress (need storage), and a transcript corrected against the audio rather than the script. |
| F12 | Learning paths with honest availability (P1) | Built now | /videos/ already lit only published levels. Each lesson page now ends with the next episode in its series: a link only when that lesson is public, otherwise "Coming next: <topic>" with no link (a test checks no page links a lesson page that does not exist), and a link back to the series on /videos/. Still Later: a cross-series path page and "mark as done" (needs storage). |
| F13 | Progress and resume (P1) | Later | Needs accounts or storage (privacy promise forbids device storage today). |
| F14 | Searchable transcript and notes (P2) | Later | Needs transcripts and a notes store. |
| F15 | Small practice checks (pilot) | Built now (pilot) | Every lesson page with a lab scene shows its free lab file(s) from /free/ and Do / Expect ("Your screen should show: ...") / Stuck? / Challenge, the script's own words; without a lab, a "Stuck?" line where the script has one; otherwise no section. Today that is n8n #0-#3 (and claude #1's file). Still waits: checks for the episodes without a lab (content the owner writes), and any self-check that records an answer (storage). |

### My Learning and access (plan section 07)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F16 | Purchase library (P1) | Built now (2026-10-16) | `GET /public/academy/library?t=`: a one-hour HMAC link bound to the email hash and the recovered checkouts lists every purchase from that address, each read fresh from Stripe (paid, refunded, disputed shown as they are; UNKNOWN when Stripe or the record can't be read). |
| F17 | Self-service access recovery (P0) | Built now (2026-10-16) | Store `recover/` form + `POST /public/academy/recover`: email AND Stripe receipt number must match the same paid checkout, the owner checked again on the fresh read. Stripe is asked for the address as typed and lowercased (two read-only list GETs whatever is typed), reading at most 3 pages of 100 checkouts per form (older purchases: Contact). Every miss is the same page, byte for byte, and every answer takes at least 1.5 s; 5/hour per address, 5/day per email (hashed); Stripe down is a 503, never "no purchase". Recovery records only paid purchases and posts nothing to Slack. No email is sent. |
| F18 | First-project onboarding (P1) | Built now (partial) | Files on the access page show size and date. No "Start here": the catalog has no first-task text, and none is invented (needs per-product Start Here content from the owner). |
| F19 | Versioned downloads and updates (P1) | Built now (partial) | Each file shows its real size and last-changed date, read from disk. Versions and changelogs still need catalog fields. |

### Checkout and business rules (plan section 08)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F20 | Payment-to-access reliability (P0) | Built now (2026-10-16) | The webhook also takes `charge.refunded` and `charge.dispute.created`/`closed` (signature first, payment resolved to its checkout, read fresh, row updated); a duplicate checkout event makes one row and one Slack post (tested). The Monday summary carries an "awaiting access" section: paid purchases with a price not in the catalog and files on sale missing on disk, UNKNOWN when unreadable. A won or prevented dispute restores access (The owner 2026-10-16). |
| F21 | Bundle clarity and duplicate prevention (P1) | Exists | The bundle lists its editions, what it leaves out, and its saving summed from catalog prices. Owner overlap warnings need verified ownership (Later). |
| F22 | Gift purchase (P2) | Later | Needs redeemable entitlements; licences must allow it (The owner). |

### Distinctive features (plan section 09)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F23 | Outcome lab (P2) | Exists (base) | The approval-gate simulation, labelled as simulated. Per-product labs are Later. |
| F24 | Setup-readiness checker (P1) | Built now (inside F04) | The finder's setup question applies the catalog's declared requirements; no device scanning. |
| F25 | Practice-file configurator (P2) | Later | Content and validation design. |
| F26 | Project completion checklist (P1) | Exists (base) | The "What you'll finish" panel. Reset/"mine looks different" needs lesson pages (F11). |
| F27 | Troubleshooting decision tree (P1) | Exists (base) | The troubleshooting preview per product. A guided tree is Later (content). |

### Support and trust (plan section 10)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F28 | Improve the AI help assistant (P1) | Later | Backend (support.py / support_guard.py); it already answers only from the catalog and policy and hands off to the owner. |
| F29 | Structured support and known issues (P1) | Exists (base) | The Help form and the product support panel. A known-issues list needs a record of verified issues (Later). |
| F30 | Evidence and honest reviews (P2) | Exists | Only verified-buyer reviews the owner approved (reviews.py); none invented. |

### Visual design, content, measurement (plan sections 11-13)

| ID | Feature | Status | What / why |
|---|---|---|---|
| Visual | Brighter reading surfaces | Declined | Dark-only brand (The owner, 2026-10-11/12). Reading surfaces stay on the lifted dark steps. |
| Visual | Accessibility and motion | Built now | New controls are keyboard operable with visible focus, at least 44 px tall, announced through live regions, and still under reduced motion. |
| F31 | Structured content and release checks (P0 foundation) | Later (partial) | The catalog is already the one source and `validate` refuses a Buy without a price, price id and refund terms. New here: a test fails if the lesson mapping names a missing product or lacks a reason. Draft/published states and a publish checklist UI are Later. |
| F32 | Operational dashboard (P1) | Declined (analytics part) / Later (ops part) | Behavioural analytics: declined, no-tracking rule. An exceptions queue from order records belongs in ATLAS's own dashboard, not the public store. |

### Release sequence, done, course platform (plan sections 14-21)

| ID | Feature | Status | What / why |
|---|---|---|---|
| F33 | Course-first navigation (P1) | Built now | Main nav: Explore, Find your fit, Free Lessons, Paths, Bundle, About, Help. "My Learning" is not added: there is nothing behind it yet. |
| F34 | Consistent course card (P1) | Exists | Every card: cover, type, title, one-line result, level, format, needs, price, two actions. 16:9 outcome images: Later, they need real screenshots (no invented imagery). |
| F35 | Instructor profile (P1) | Exists | "Meet the owner": no credential, count or testimonial. A photo appears only when the owner supplies one. |
| F36 | Purposeful browse collections (P1) | Exists | The three paths. No "Popular" (no ranking data, invariant 6). |
| F37 | Search with useful results (P1) | Built now (with F02) | Titles and tools rank ahead of loose text matches; synonyms; query in the URL; empty state. Lessons and help in the same search: Later. |
| F38 | Wishlist and comparison tray (P2) | Declined (wishlist) | Device storage breaks the privacy promise. The comparison part is F03. |
| F39 | Inspectable syllabus (P1) | Exists | "Every section, listed" with the real titles; ebooks and kits are labelled as such. |
| F40 | Purchase panel that follows learner state (P1) | Exists (visitor state) / Later (owner state) | Owner states need verified ownership. |
| F41 | Full lesson-player controls (P1) | Later | Workspace (backend). |
| F42 | Resource-specific readers (P1) | Later | Access page and content. |
| F43 | Lesson workspace with saving (P2) | Later | Backend. |
| F44 | Project submission and feedback (P2) | Later | Backend and someone to review. |
| F45 | A cart (P2) | Declined for now | Money is Tier 3; the plan itself says Buy now is enough for seven titles. |
| F46 | Promotions and bundle rules (P2) | Declined | Money is Tier 3; no crossed-out prices (store rule). |
| F47 | Order history and refund requests (P1) | Built now (2026-10-16) | Order history is the F16 library (date, amount as Stripe stated it, status). Refund requests stay an email to the owner from the paying address; refunds stay his decision. |
| F48 | Transactional email lifecycle (P1) | Declined | ATLAS sends no automated email (an email is irreversible; the kernel forbids irreversible Tier 1). Stripe's receipt is the purchase confirmation. |
| F49 | Course Q&A (P2) | Later | Needs a backend and a moderator. |
| F50 | Completion certificates (P2) | Later | Needs progress records. |
| F51 | Learning reminders (P2) | Later | Needs opt-in records and email. |
| F52 | Course update workflow (P1) | Later | Editorial states in the catalog. |
| F53 | Component library (P0/P1) | Later | A reference page of states; the tokens already live in style.css. |
| F54 | Mobile and reading-mode behaviour (P1) | Built now (partial) | One column under 760 px, the new filters wrap and stay reachable, no horizontal scroll at 375 px (screenshots). A separate reading theme is declined (dark only). |
| F55 | Enrollment and progress state model (P1) | Later | Backend. |
| F56 | Role boundaries and publishing (P0/P1) | Later | ATLAS's tiers already gate publishing (`academy.publish_shop`). |

## Blog (The owner's site pack, 2026-10-18)

`/blog/` is rendered by `blog_pages.py` inside `site.render` (the posts are
Markdown in `academy/blog/`), so `academy.publish_shop` publishes it with the
rest of the store. Its "Latest from the channel" is the /videos/ list; a post's
player is the /videos/ one, only for a published tutorial; its "Pairs with"
line is a live catalog product (`blog/kit_map.json`, by slug). One change to
a rule above, at the owner's request: `assets/blog.js` keeps the lessons a visitor
ticks as finished in that browser's localStorage (never sent; the privacy
page says so). Every other page still stores nothing, and the store stays
dark only, so the pack's theme switch was not ported. The owner, 2026-10-18 ("you
narrate everything and automate the process"): the posts say the lessons are
narrated by a disclosed AI voice and made by his automated pipeline, never
that he records them. Held as a draft until the owner decides:
why-every-workflow-pauses (its `draft_reason`).

## Questions for the owner (nothing is shown until answered)

1. **Channel handle.** The store links @example_handle; the earlier review used
   @example_handle. Which is canonical? (`site.CHANNEL_URL`)
2. **Other costs per product.** For each title, what does it cost to run
   beyond the price (Claude plan, n8n Cloud vs self-hosted, a printer)?
   Until stated in catalog.json, the store says no other cost is listed.
3. **Lesson pairings.** `LESSON_PRODUCTS` in site.py now carries a reason
   per series; change any pairing there. The Claude series no longer
   points at AI for Every Age.
4. **Self-service recovery.** Answered 2026-10-16: approved as email + Stripe
   receipt number, no email sent (F17 above).

## Needs the owner (shop backend, 2026-10-16)

* Stripe → Settings → Customer emails → **Successful payments ON**, so every
  buyer gets the receipt (and receipt number) recovery depends on.
* Stripe → Developers → Webhooks → the ATLAS Academy endpoint: add the
  events `charge.refunded`, `charge.dispute.created`, `charge.dispute.closed`.
* The restricted key `ACADEMY_STRIPE_READ_KEY`: add **Disputes: Read**, so a
  won dispute can restore access (without it a disputed purchase shows
  "can't check", never paid).
