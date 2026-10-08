# First-run onboarding

**Date:** 2026-09-20
**Status:** implemented 2026-09-20 — see `docs/superpowers/plans/2026-09-20-first-run-onboarding.md` for what was built and where it deviated

A customer signs up, and between that moment and Guardian being useful there
is a gap: a sensor has to be running on their LAN, their networks have to be
declared, and somebody has to point a router at a resolver. This spec is about
making that gap short, obvious, and survivable when it goes wrong.

It covers one tenant's first hour. It does not cover billing, signup itself, or
the owner console.

---

## What already exists

Most of this is already built, and the single most useful thing this
investigation produced was finding out how much. Recording it here so the
implementation does not rebuild any of it.

| Already built | Where |
|---|---|
| Per-user, per-tenant wizard state with two paths (`setup` installs, `tour` orients) | `hub/guardian_hub/onboarding.py`, `models.UserOnboarding` |
| A five-step setup wizard: name → networks → sensor → waiting → label → done | `web/src/pages/Onboarding.tsx` |
| Per-network enrol codes that bind the sensor on redemption | `sensors.new_enrol_code_for(db, tenant, network_id)`, `Sensor.network_id` |
| One-line installer served by the hub itself, never a third-party host | `hub/guardian_hub/installer.py` |
| Device classification and grouping into eight groups, at read time | `payloads/assets.kind_of`, `KIND_GROUPS` |
| DNS setup walkthrough with a live "is traffic actually flowing" check | `web/src/components/DnsWizard.tsx`, `schemas_dash.DnsWizard` |
| Outgoing mail, configured in production | `mailer.py`, Resend (moved off Zoho 2026-09-20; `send` returns a bool) |
| Multi-network as a first-class concept | `models.Network`, Task 51 / spec S3.1 |

**No Alembic migration is required by this design.** Every piece of state it
needs is derivable from rows that already exist.

---

## The two findings that shaped this

### 1. `trusted` is a security warrant, not a display label

The first draft of this design auto-trusted devices Guardian recognised, so
that a new customer's estate would look settled instead of like a wall of
unknowns. Reading the sensor's alert code killed that idea. A trusted device
is:

- **skipped entirely** by the rogue-uplink / second-router check
  (`sensor/guardian_sensor/alerts.py:289`) — the check that catches a device
  handing out its own uplink;
- **exempt from risky-port alerts** (`alerts.py:299`) — telnet appearing on a
  trusted device raises nothing at all;
- **scored lower risk** (`lan.py:207`);
- **excluded from `lan:unverified`** (`home.py:281`).

And trust is sticky: `ingest.store_scan` refuses to un-trust a device a later
scan reports as untrusted (`ingest.py:349`). So auto-trusting thirty-five
devices would permanently and silently disable three detections on each of
them. In a product whose value is those detections, that is the worst change
in this document, and it would have been invisible until the day it mattered.

The same line in `home.py` shows `label` does half the same job: a device with
a non-empty `label` is also excluded from `unverified`.

**Rule, to be stated in CLAUDE.md:** `kind` and `name` are machine inference
and may be written automatically. `label` and `trusted` are human warrants that
suppress detections, and no automatic path may ever write them.

### 2. The baseline already does what auto-trust was for

`home.py:279` computes the baseline as the earliest `first_seen` across known
devices, and `unverified` requires `first_seen > base`. Devices from the first
scan share that earliest timestamp, so they are **not** `> base` and never
appear as unverified.

Guardian already implements "this is my network as of today". The quiet first
view that auto-trust was meant to buy is already there, for free, without
touching a single warrant field.

This is why this spec contains no device organiser. The read path classifies,
the baseline keeps the estate quiet, and the only real defect is one
inconsistent fallback in the UI (see §3).

---

## 1. Progress is derived, not stored

### The problem

`Onboarding.tsx` walks a fixed `STEP_ORDER` array, keeps the current step name
in `UserOnboarding.step`, and mirrors it in `localStorage` under a
tenant-scoped key. That is sound for one network and one sensor.

It cannot represent what customers actually have. A practice with three LANs
behind one WAN needs three sensors. Halfway through, two are enrolled, one is
not, one has reported and one has not. A cursor holding `"sensor"` can only be
wrong about that — and worse, it can *disagree with reality*: it says `sensor`
while two sensors are already pushing scans, or `done` while a declared
network has nothing on it. Every such disagreement is a support ticket.

### The change

The hub stops storing where the user is and starts answering **what this
tenant still needs**, computed from rows that are already the truth:

```
GET /api/v1/onboarding
{
  path: "setup" | "tour",
  show, completed, skipped,        # per-USER, unchanged, still stored
  site_named: bool,
  networks: [
    { id, name, slug,
      sensor: "none" | "pending" | "active" | "quiet",
      first_scan_at: "",
      devices: 0, by_group: { "Cameras": 12, "Phones": 8, ... },
      dns: "off" | "working" }
  ],
  next: { network_id, action }     # the single next thing to do, or null
}
```

Each field is a lookup, not a record:

- `sensor` — is there a `Sensor` row for this `network_id`; is its `status`
  `pending` (code minted, never redeemed) or `active`; if active, is
  `last_seen` inside `SENSOR_STALE_S`.
- `first_scan_at` — the earliest `Scan.ts` attributable to the network.
- `dns` — `DnsWizard.working and clients_using > 0`, which is the existing
  live signal and already means "traffic is genuinely flowing through the
  resolver", not "someone clicked a button".

`UserOnboarding.completed` and `.skipped` stay stored. They are per-user facts
about a person, not about a tenant, and nothing else can derive them — that
distinction is the whole point of `onboarding.py`'s existing docstring and is
preserved.

`UserOnboarding.step` stops being authoritative for the `setup` path. It is
retained for the `tour` path, which is genuinely linear (three steps, no
tenant state), and as a resume hint for which network the client was last
looking at.

### Why this serves "fewer client issues"

Resume works on any device with no `localStorage`. A tab closed during the
wait comes back to the right place because the scan either landed or did not.
A third network added a week later re-opens the wizard at exactly the right
step, because "needs a sensor" is a fact about the network, not a cursor
someone forgot to rewind.

---

## 2. One sensor per network, delivered by email

### Why email

The person who can install a sensor is usually not the person at the browser.
The practice manager signs up; the sensor goes on a machine that the IT
contractor, or the owner's son, or nobody yet has access to. A screen that
shows an enrol code helps only if the right person is standing at it.

So when networks are declared, Guardian mints one enrol code per network and
sends one email per network, each naming its own network. The email is
forwardable, which is the actual delivery mechanism for the instruction.

### The email

One per declared network, containing:

- which network it is for, by the name the client just gave it;
- the enrol code, and when it expires;
- the one-line install command, from `installer.py` — which builds it from the
  origin the request arrived on, so it never names a host this hub does not
  control;
- the hardware option: request a pre-enrolled device instead.

Patterned on the existing `mailer.send_account_ready`. There is no enrol email
today; this is new.

### Security constraints

The enrol code is a **bearer credential** — whoever holds it can attach a
sensor to this tenant. Two constraints follow, and both are requirements, not
preferences:

1. **It is sent only to the account's own address.** Never to an address typed
   into the wizard. A wizard field that mails a credential to arbitrary
   recipients is a credential-exfiltration feature with a friendly label.
2. **An e-mailed code is a different credential from one on a screen.**
   `ENROL_CODE_TTL_MINUTES` is 15, which is right for a code someone is
   standing in front of and useless for one read out of an inbox two hours
   later — the delay is the entire point of sending it.

   Lengthening the window is not free. `routers/sensor.enrol` matches on
   `filter_by(enrol_code=...)` with **no tenant in the lookup**, so every
   unredeemed code on the platform is guessable at once; a fifteen-minute life
   keeps that set nearly empty and a seven-day life across many customers does
   not. So the e-mailed code is 16 characters rather than 8 — which costs the
   reader nothing, because nobody types it. It is embedded in the install
   command the e-mail carries.

   The wizard offers re-mint rather than extending an existing code, and a
   superseded code stays dead.

`hub/tests/test_account_payload.py` already guards that no enrol code reaches
the owner console's payloads. That guard is extended to the onboarding payload.

### Both delivery paths

The wizard asks, per network, how the sensor will get there:

- **Self-install** — the code and command, as above.
- **Hardware** — the client asks for a pre-enrolled device. The enrol code is
  still minted and bound to the network; it is redeemed during provisioning
  rather than by the customer, so the rest of the flow is identical and the
  network shows `pending` until the box is plugged in.

The two paths differ only in who redeems the code, which is why they can share
one state model.

**The hardware request is not tenant state.** It is a message to the owner, and
it goes to the support queue that already exists rather than to a new column on
`Sensor` — which is what keeps this design migration-free. The tenant-side
truth stays exactly what it is for self-install: a network with a pending code
and no sensor reporting yet. Storing "hardware was requested" alongside it
would add a second thing that can disagree with the first, for no reader who
needs it: the client sees "waiting for your sensor" either way, and the owner
needs a queue item they can act on, not a flag on a row.

---

## 3. The first view

No device organiser. §"findings" explains why: `kind_of` already classifies on
read, `KIND_GROUPS` already buckets, and the baseline already keeps the first
cohort quiet.

Two changes:

**Fix the `DeviceGrid` fallback.** `DevicesTable` renders
`label || name || vendor || mac`; `DeviceGrid` renders `label || name || mac`.
A device with no hostname therefore shows a readable vendor in one view and a
raw MAC in the other. The grid is what a new customer sees first, so this
single missing fallback is a meaningful part of why a fresh estate looks
unorganised. Add `vendor`.

**Turn the wizard's `label` step from a gate into an offer.** Today it presents
unrecognised devices one at a time, each with a text box and two buttons, and
the client cannot reach "done" without walking the list. That is the step people
abandon, and — given the baseline — none of it is *required*.

It leads with the summary: what the first scan found, grouped ("12 cameras,
8 phones, 3 printers, 2 unrecognised"). Naming is offered right there rather
than deferred to another screen, because the moment a client is looking at
their own freshly-discovered estate is the moment they are most able to say
which laptop is whose — sending them to Assets later spends that.

So the step carries the same per-device naming the current one does, expanded
under the summary, with two differences that matter:

- **"Done" is always available.** Naming nothing is a complete, correct
  outcome, and the step must never imply otherwise.
- **The unrecognised devices sort first**, because they are the ones where a
  human name adds something the classifier could not infer. A device already
  reading as "Reolink camera" needs no help.

A client who names nothing is in exactly the state this design intends. A
client who names everything has done something genuinely useful — and, unlike
the machine, is *entitled* to, because a name typed by the owner is a human
warrant. That is the same rule from §"findings" seen from the other side:
`label` suppresses the unverified alert, which is correct when a person set it
and wrong when a machine did.

---

## 4. Teaching, and DNS

**The wait.** The first scan leaves roughly a minute of blank screen. It is
filled with what Guardian is doing right now and why it matters — one idea at
a time, paced against the scan rather than a fixed timer, so the copy is
describing something true.

**The tour.** Once the scan lands, a short walkthrough over the client's *own*
devices rather than abstractions: here are your cameras and what we check on
them; here is what "unverified" will mean when it appears. This is the
existing `tour` path's job, reused rather than rewritten — it currently only
runs for people joining an established tenant, and it should also run once,
here, at the end of setup.

**DNS.** The last step, using the existing `DnsWizard`. Skipping is one click
and carries no penalty, because most clients will not have the router password
to hand on day one. A tenant with `dns: "off"` keeps a visible prompt on the
dashboard afterwards, so the feature keeps asking instead of going quiet —
this is one of the main reasons to buy Guardian and it should not be possible
to simply never notice it.

---

## 5. Error handling

These are the states a real customer reaches, and each needs a defined answer
rather than an empty screen.

| Case | Behaviour |
|---|---|
| Enrol code expires unredeemed | Network shows `sensor: "none"` again; wizard offers re-mint and re-send. The old code stays dead — never extended. |
| Sensor enrols, never pushes a scan | `sensor: "active"`, `first_scan_at` empty. The wizard says the sensor is connected but silent, which is a different problem from "not installed" and needs different advice. |
| Sensor reported, then went quiet | `sensor: "quiet"` via `SENSOR_STALE_S`. Distinguished from `active` because a sensor that worked and stopped is the archetype of the `human` remedy tier — no fix Guardian runs can reach a host that is not answering. |
| Network declared, then abandoned | Stays `sensor: "none"` indefinitely and holds `next` hostage. The networks step must allow deleting a network that has never had a sensor. Deleting the primary network stays forbidden (`Network` docstring). |
| Two sensors on one network | Allowed — `Sensor.network_id` is not unique, and a redundant sensor is legitimate. The network counts as covered if **any** bound sensor is active. |
| Three networks declared, one machine available | The sensor sees only its own LAN. The wizard must say so plainly at declare time, because this is the misunderstanding that produces "Guardian is broken, it only found half my devices". |
| Client closes the tab mid-setup | Nothing is lost; §1 makes this a non-event. |

---

## 6. Testing

Following the house rules: no wall-clock assertions, and guards about work that
scales with rows count statements via `tests/querycount.py`.

**Derived state**
- The right `next` for each partial setup: nothing declared; declared with no
  sensor; sensor pending; sensor active but silent; scan landed; DNS off.
- Adding a third network a week later re-opens setup at that network.
- `completed`/`skipped` remain per-user: a second user on a ready tenant gets
  `tour`, never `setup`.
- The payload does not issue a query per network.

**Enrol and email**
- One code and one email per declared network, each bound to its own network.
- The code binds the sensor to that network on redemption.
- The email goes to the account address, and a wizard-supplied address cannot
  redirect it.
- No enrol code appears in any payload — extending
  `test_account_payload.py`'s existing field-name guard to the onboarding
  models.

**The warrant rule — the most important test here**
- No automatic path writes `trusted` or `label`. Asserted against the models
  the onboarding and ingest paths touch, so a future "helpful" default fails
  loudly rather than silently disabling risky-port and rogue-uplink alerts.
- A device present in the first scan does not appear in `unverified`
  (pinning the baseline behaviour this design depends on).

**First view**
- `DeviceGrid` and `DevicesTable` render the same fallback chain for a device
  with no name.
- The naming step can be completed having named nothing — the regression that
  would make it a gate again is the one worth pinning, because it is the
  single behaviour this step exists to avoid.
- A name typed by a client IS written to `label`. The warrant rule forbids
  machines writing it, not people, and a test that simply asserted "nothing
  writes `label`" would forbid the feature.

---

## Out of scope

Signup and billing; the owner console; changing `kind_of`'s heuristics;
hardware provisioning logistics beyond recording that hardware was requested;
any change to what the sensor scans.

## Open question for implementation

Whether the `tour` path running at the end of `setup` should be the same three
steps it uses today, or a shortened version — the existing tour explains "where
things live" to someone joining an established site, and a person who has just
watched their own first scan land may need less of it. Decide when the copy is
written; it does not affect the state model.
