# The external console handoff: what we took, and what we did not

**Date:** 2026-09-20
**Source:** `docs/design/2026-09-20-console-handoff.md` and
`docs/design/2026-09-20-console-prototype.html` — an external design bundle
delivered as a working HTML prototype plus a ~450-line written handoff. Both
are kept verbatim; this document records the decisions made against them, so
the next person does not have to re-derive them from a diff.

## The finding that reframed the whole bundle

The handoff is titled "Guardian Admin Console" and names `admin.guarder.io`.
Its contents are not that console.

Every screen in it describes **one network**: "Sensor-01 · your network, right
now", *Assets watched 64*, Devices / Servers / Websites / Certificates / DNS
zones, open ports on a host, Approve-and-Defer on a fix about to run. The
handoff states it outright — *"The console has a single user: the owner. There
is no multi-tenant client view."*

Guardian's `admin.guarder.io` is the opposite surface. It is the platform owner
looking at **customers** — Northwind Dental, WEME — through
`GET /api/v1/platform`, which returns tenants and is gated by
`require_superadmin`. It has no device, port or certificate in it by design.

The ten views map almost one-to-one onto pages that already exist in the
**customer portal** at `my.guarder.io`:

| Handoff view | Already in the product |
|---|---|
| Overview | `web/src/pages/Overview.tsx` |
| Incidents | `pages/IncidentDetail.tsx` + the alert surfaces |
| Maintenance | `pages/Jobs.tsx` |
| Assets / Asset detail | `pages/Assets.tsx` |
| Websites & DNS | `pages/Dns.tsx` |
| Certificates | `pages/Security.tsx` |
| Reports | `pages/Reports.tsx` |
| Settings (allow-list) | `pages/Settings/` + the auto-fix policy |
| Billing | `pages/Settings/Billing.tsx` |

So building the bundle literally at `admin.guarder.io` would have meant
rebuilding the customer portal inside the owner console, against a payload that
does not carry the data. **This was raised with the owner before any code was
written, and the decision was: take the visual system to both portals, and give
the owner console the things it was actually missing.**

## What the owner console was actually missing

Named directly by the owner: *"show the admin all the clients and what their
status is and how they are going and if there are any issues — also it should
show payments and billing from each company, and the ability to switch between
clients."*

Three of those already existed and two did not.

- **Clients, status, trend, issues** — the Ledger already carries them, with a
  30-day sparkline per customer.
- **Payments and billing per company** — missing. `AccountPayload` described a
  customer completely except for the money. Added as `AccountBilling`.
- **Switch between clients** — missing. Every route into an account went
  through a list. Added as the picker in the bar.

## The four design principles, and where they already lived

These are the part of the bundle worth keeping permanently. Three of the four
were already Guardian's rules under different names, which is the strongest
evidence they are right.

1. **Monospace means machine-produced.** Mono for anything a computer emitted
   or a human must read character by character — hostnames, addresses, IDs,
   timestamps, counts, currency, column headers, status pills. Sans for prose a
   human wrote. Guardian already did this; the handoff states it as a rule,
   which is what makes it survive the next screen.
2. **Blue is for actions only.** Never decoration. `owner.css`'s `--own-act` is
   exactly this, and is the one `--own-*` name that is not a translation of an
   app token.
3. **Colour carries status, nothing else.** No gradients, no decorative
   accents. Guardian's version is stricter and better stated: *healthy
   recedes*, and emphasis shifts weight rather than hue.
4. **Say what the fix will cost.** Every approvable action states its blast
   radius in plain language next to it — "drops live connections for roughly 3
   seconds", "may break vendor tool". This one is genuinely new to us as a
   stated rule, and it is the best idea in the bundle. `remedy.py`'s three
   action tiers already imply it; nothing in the UI was required to print it.

The prototype's **elevation rule** is also worth keeping: no shadows anywhere,
depth from background steps and 1px borders. `tokens.css` already sets
`--shadow-glow: none` for the same reason.

## Two deliberate deviations

### Fonts stay vendored, and stay ours

The handoff specifies Instrument Sans + JetBrains Mono **from Google Fonts**.
Guardian ships Manrope + IBM Plex Mono as local `woff2` under
`web/public/fonts/`.

`CLAUDE.md` forbids the hotlink outright, and the reason is the product's whole
premise: Guardian is a network watchdog, so it has to paint on an isolated or
WAN-down network. A dashboard that goes blank when the uplink dies is worse
than no dashboard, because the owner reads the blankness as "nothing is wrong".

The owner's instruction was "whichever is recommended". The recommendation
taken: keep the vendored faces and adopt the typographic **rules** — the
mono/sans split, the size and tracking scale, uppercase tracked-out column
headers. IBM Plex Mono is a close stand-in for JetBrains Mono, and switching
faces would have moved every existing screen, not only the redesigned ones. If
the faces are ever adopted, they must be downloaded into `web/public/fonts/`
with `unicode-range` declared — never a `<link>`.

### The palette lands in tokens, not in `owner.css`

The handoff's token table is ~40 raw hex values. `owner.css` contains **no hex
or `rgb()` literal at all** — its `--own-*` names are aliases onto app tokens,
and `web/tests/ownerPalette.test.ts` fails if a literal appears.

That constraint is not tidiness: it is what makes both skins free and keeps the
console from drifting into a second, hand-maintained palette. Any colour from
the handoff that is genuinely new belongs in `web/src/styles/tokens.css`, with
`owner.css` aliasing it. The handoff's own observation — *"all greys are
blue-tinted (hue around 215-220); swapping in neutral greys flattens the whole
thing"* — is worth carrying into any token added.

## What shipped on 2026-09-20

- `AccountBilling` on `GET /api/v1/platform/tenants/{tid}`, and a "What they
  pay" section leading the account page. It imports `_PAYING`,
  `plans.monthly_cents` and `_per_site` from `payloads/platform` rather than
  re-deriving them, because the roster and this page are read seconds apart
  about the same account.
- The customer picker in the owner bar. It navigates; it does not switch the
  session, which is what the Ledger's "Open their dashboard" does.
- Earlier the same day, and part of the same brief: monthly recurring revenue
  on the Billing roster and the Overview.

## What has not been done

**The visual-system pass over the customer portal.** The owner asked for it
across both portals; only the owner console has been worked. `my.guarder.io`
has not been audited against the four principles above — in particular the
mono/sans split and the "say what the fix will cost" rule, which is where its
alert and job surfaces would gain most.

**The blast-radius line — done.** `remedy.FIX_IMPACT` is the third table
beside `FIX_LABELS` (what) and `_FIX_STEPS` (how): one sentence per repair
saying what breaks, for how long, and for whom. It is carried on every branch
including `auto` (nobody is asked, but the owner reading the log afterwards is
asking exactly this) and including the unreachable-host case (a person is
about to do it by hand, which is when it matters most). Missing entries render
as nothing rather than as reassurance, and tests fail if a repair gains a
label without gaining a cost, or if a line hedges instead of naming the
blast radius.

**The typographic rule — done.** Principle 1, monospace means
machine-produced, broke in one specific shape: a field holding a NAME that
falls back to an address when there is no name. Ten of them existed, each
rendering "Reception iPad" on one row and a MAC on the next through the same
element. `web/src/lib/machine.ts` decides per value; the call sites ask it.

Measured on the live portal, machine values in the numeral face vs the
proportional one:

| Page | Before | After |
|---|---|---|
| `/dns` | 17 / 27 | **44 / 0** |
| `/assets/devices` | 27 / 3 | **30 / 0** |
| `/history` | 7 / 113 | **120 / 0** |
| `/overview` | 0 / 54 | **49 / 0** |
| `/topology` | 0 / 4 | **3 / 0** |

The audit walks text nodes on the live page and compares computed
`font-family`. None of this was visible from the source, so re-run it rather
than reasoning about it. Its one remaining hit on `/topology` is an SVG
`<title>` — a tooltip, never rendered as text — which is a false positive in
the audit, not a defect.

**Principles 2 and 3 — audited, nothing to fix.** Both were measured on the
live portal rather than assumed.

*Blue is for actions only.* Across `/overview`, `/inbox`, `/security` and
`/assets/devices`, exactly one element carries `--color-primary` without being
interactive: the `Guardian` wordmark. The reference colours its own wordmark
the same way, so that is the rule being followed rather than broken. No other
decorative use of the action blue exists.

*Colour carries status, nothing else.* **Zero gradients** on any page. The only
outer shadows are the chat panel and its floating button — genuine overlays,
which are supposed to be lifted — plus two 0-blur rings on the Security attack
markers, which are borders drawn as shadows rather than elevation. The
reference's "no shadows anywhere, depth from background steps and 1px borders"
already holds.

## Nothing outstanding

Every part of the bundle that transfers has shipped. What was deliberately not
taken, and why:

- **Instrument Sans / JetBrains Mono from Google Fonts** — the hotlink is
  forbidden; the typographic rules were adopted in the vendored faces instead.
- **KPI values coloured by status** — this console keeps weight rather than
  hue. Colour it all and red stops meaning alert.
- **The auto-heal toggle — built, on request.** It was held back deliberately:
  in the reference it pauses one network, here it pauses every customer, and a
  kill switch is not a visual decision to take from a design bundle. Built when
  the owner asked for it.

  Paused means *nothing runs without a person saying yes to a specific
  proposal* — not "no repair may happen", which would strand an owner whose
  disk fills at 2am with a switch they flipped in June. Consent already given
  is honoured: `registry.dispatch` checks `confirm_token` before the tier.

  Enforced in `chat/tiers.effective_tier` and nowhere else, because its two
  callers are the two that matter — `remedy.resolve_action` (does the row offer
  Fix, or ask) and `registry.dispatch` (does the tool run). A pause in the UI
  alone would have left chat able to run the same repair.

  Two traps worth carrying forward. A `bool` setting had no branch in the owner
  console and fell through to a TEXT INPUT, so the kill switch was a box you
  typed a word into. And the switch then shipped reading OFF while Guardian was
  repairing, because the hub serialises a stored bool with Python's `str()` —
  `"True"`, capital T — and the UI compared against `"true"`. The fix is that
  `isOn` mirrors `set_value`'s own accepted set rather than matching one
  spelling.
- **The sensor status chip, the range switch and the search** as drawn — all
  scoped to one network. The chip was translated (the rail now reports how much
  of the estate is reporting); the search became a customer/screen palette.
