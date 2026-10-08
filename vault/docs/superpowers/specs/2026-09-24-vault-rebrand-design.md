# Guardian becomes Vault — a rebrand, not a rename

**Date:** 2026-09-24
**Status:** design, decisions taken in chat

The product is called **Vault**. Singular, never "Vaults": the product speaks in
the first person throughout — "Guardian blocks what it knows, logs it, and shows
it in the query stream" — and "Vaults blocks" is not a sentence.

The domain does not move. `guarder.io`, `my.guarder.io`, `api.guarder.io` and
`admin.guarder.io` stay exactly as they are, along with every email address on
them. A product whose name and domain differ is a thing the world is full of,
and the alternative is a DNS, TLS and deliverability migration bought with
nothing.

## What this is and is not

**This changes strings a customer can read. It changes no identifier.**

The word appears 32,268 times. Roughly 14,500 of those are prose a customer
might see; the other ~12,900 are Python packages, environment variables,
on-disk paths, service labels and binary names that only a maintainer ever
reads. Renaming the second group would mean reinstalling every enrolled sensor,
rewriting production's `.env`, swapping the systemd unit, and living through a
window where a half-migrated sensor talks to a renamed hub and neither works —
bought for a benefit no customer can perceive.

So `guardian_hub`, `guardian_sensor`, `GUARDIAN_*`, `/opt/guardian`,
`~/.guardian`, `com.guardian.*`, `guardian-hub.service` and the `guardian` CLI
all keep their names. If they are ever renamed it is a separate project with a
migration path, and this document is not it.

The cost is a codebase whose internals say Guardian and whose product says
Vault. That is a real cost and it is the cheaper one.

## The rule, and the one line that proves it needs stating

**A string changes only if a customer can read it.** Identifiers, storage keys,
wire formats and anything persisted keep the word Guardian, however
brand-looking they appear.

`integrations/homeassistant.py` makes the point in a single expression:

```python
cfg_payload = {"name": f"Guardian {name}", "unique_id": f"guardian_{node}_{obj_id}",
```

`name` is what a customer reads on their Home Assistant dashboard and becomes
`Vault {name}`. `unique_id` is the entity's identity, and changing it does not
rename anything — it creates a second entity beside the first, orphaning every
dashboard card and automation the customer built on the old one. Same line,
opposite answers. A bulk find-and-replace takes both and the failure is silent.

## Never change these

| String | What breaks |
|---|---|
| `guardian_session` | The session cookie. Every signed-in user is logged out. |
| `guardian_{node}_{obj_id}`, `guardian_{node}` | Home Assistant `unique_id` / `identifiers`. Duplicate entities; customer dashboards and automations orphaned. |
| `guardian.theme`, `guardian.sidebar`, `guardian.sidebar.expanded`, `guardian.chat.open`, `guardian.chat.speak`, `guardian.topbar.advanced`, `guardian.network.`, `guardian.onboarding.dismissed.` | `localStorage` keys. Silently resets every user's theme, sidebar state and saved preferences. |
| `guardian.docs.token` | `sessionStorage`, set by the hub in `apidocs.py` — not a browser localStorage key, which is where this table first put it. Forgets the saved API-docs token. |
| `x-guardian-scope`, `x-guardian-role` | OpenAPI extension fields in the **published** API document. Any client reading them breaks. |
| `guardian/1.0` | the User-Agent every integration sends. Distinct from the sensor's two below; found in Task 4, not in the first draft of this table. |
| the Discord slash command `guardian` | `integrations/discord.py` matches the inbound command name. Every customer's Discord app is registered against `/guardian`; renaming it breaks them all. The card's documentation line must therefore keep saying `/guardian` too — it is naming the command, not the product. |
| `guardian_site_*`, `guardian_host_*`, `guardian_lan_*`, `guardian_alerts`, `guardian_speed_*`, `guardian_dns_blocked_24h`, `guardian_data_age_seconds` | **Prometheus metric names** exported by `routers/public.py` and scraped by customers. Renaming one breaks every dashboard, alert rule and recording rule built on it. Found by the Task 8 sweep; absent from the first three drafts of this table. |
| `[guardian]` log prefixes, `guardian-logo.png`, `guardian.png`, `guardian.db`, `guardian.theme`, `guardian-live/1.0`, `guardian-pdf-`, `guardian-probe-`, `guardian-queue-poll`, `/var/lib/guardian` | Log prefixes, asset filenames, a database filename, a shared localStorage key, a User-Agent, temp-file and thread names. |
| `guardian-demo`, `guardian.invalid`, `guardian-tier2-sandbox`, `demo@guardian.invalid` | Seeded demo fixture values. `seed-demo` must stay deterministic and idempotent; these are matched on, not displayed. |
| `fixed_by_guardian` | A data key in the report payload. |
| `"Guardian public"` (capitalised, unusually) | A **lookup key**, not copy. `demoscrub` matches it against a real packet capture in `hub/tests/fixtures/`, and `importer.STALE_SITE_LABEL_PREFIXES` matches it against Site rows a previous `guardian migrate` already relabelled. Renaming it makes the scrub fall through to a generic name that collides with an existing site, and leaves migrate no way to correct tenants stuck on the old label. The one capitalised exception in this table. |
| `guardian-scan/1.0`, `guardian-agent/1.0` | User-Agent strings the sensor puts on the wire. A customer may have them in an IDS rule or an allowlist. |
| `guardian-nvr:{recorder_id}:{channel}` | Persisted recorder key. |
| `guardian_hub`, `guardian_sensor` | Python packages. |
| `GUARDIAN_*` | Environment variables, including production's `.env`. |
| `/opt/guardian`, `~/.guardian` | On-disk paths on the VPS and every sensor host. |
| `com.guardian.sensor`, `com.guardian.dns` | launchd labels on every Mac sensor. |
| `guardian-hub.service`, `guardian-hub`, `guardian-sensor`, `guardian` | systemd unit and binary names. |
| `guarder.io` and every address on it | The domain, by decision. |
| `guardian_{plan_id}_{interval}` (`plans.py`), and the `guardian_` prefix `billing.py` parses back out of it | The **Stripe lookup key**. Prices already exist in the live Stripe account under these keys; a rename resolves nothing and checkout fails for every plan at once — visible only to a customer trying to pay. The two sides must change together or not at all. Added after ship. |
| `guardian-hub/1.0`, `guardian-sensor/{VERSION}`, `guardian-dns/{VERSION}` | The remaining User-Agents on the wire, alongside `guardian/1.0` and the sensor's two above. Same reason: a customer may have any of them in an IDS rule or an allowlist. Added after ship. |
| `guardian-sensor.service`, `guardian-dns.service` | The systemd unit names, spelled out rather than left to the row above. A rename leaves the installed unit running and unmanageable while the new one is never loaded, and `remedy` tells a customer to run `systemctl restart` on this exact name. Added after ship. |
| `CONF.guardian-bak-` (`sensor/fix.py`) | The filename a fix writes its rollback copy to. A repair in flight when the name changes leaves a backup the rollback path cannot find, so a failed fix cannot be undone. Added after ship. |

`apikeys.TOKEN_PREFIX` is already `gapi_` and needs no thought.

## What does change

Everything a customer reads, and nothing else:

- **The dashboard.** Page titles, the chat panel's own name, empty states,
  onboarding, settings copy, the PWA manifest, `index.html`'s `<title>`.
- **The marketing site**, in all nine locales. A locale left behind is a
  customer reading the old name in their own language.
- **Email.** Subjects, bodies, and the `From` display name — the address stays
  `guardian@guarder.io`, the name beside it becomes Vault.
- **PDF reports.** Headers, footers, cover pages; these get filed and forwarded.
- **The assistant's voice.** It refers to itself by name, and `chat/rules.py`
  notes that when no provider is configured "this IS Guardian's voice".
- **Alert and remedy text**, which is where most customers meet the name.
- **Notification titles** on Slack, Discord, Teams, Telegram, ntfy, PagerDuty.
  Dedup keys are separate fields and stay.
- **The installer's output**, which a customer watches scroll past.
- **`security.txt`**, and the download filename `guardian-alerts.csv`.

## Tests

Assertions that pin user-visible copy move with it — a test asserting the old
name is as wrong afterwards as the string was. The demo tenant is re-seeded
constantly and publicly: check it after, not only unit tests.

Two guards worth adding, because this is exactly the change that regresses by
someone being helpful later:

- **No new `Guardian` in user-visible strings.** A test that reads the
  user-visible surfaces and fails on the old name, with the never-change list
  as its explicit allowlist.
- **The never-change list is still intact.** A test asserting `guardian_session`,
  the localStorage keys and the Home Assistant `unique_id` shape are unchanged —
  so a later tidy-up that "finishes the rename" fails loudly instead of logging
  out every customer.

### What the first version of these tests got wrong

Both corrections are the same mistake seen twice: **a guard that cannot fail
still reports success**, and nothing in a 2153-test suite can tell the two
apart.

1. **The web guard shipped to production disarmed.** The mechanical
   comment-and-docstring sweep (`0fe377b`, listed under *Not in scope* above as
   carrying no risk) rewrote one token of live code inside
   `web/tests/noStaleBrand.test.ts`: `rest.includes("Guardian")` became
   `rest.includes("Vault")`. An early return two lines above still tested for
   `Guardian`, so from then on the guard flagged a line only when it contained
   *both* words — never. It was green through a full run and through a deploy.
   An AST/​comment-aware audit of all 326 files in that commit found this was
   the only non-comment change in it.

   The fix is not just the token. `offendingLines` is now exported and has
   `detects` cases that feed it a known offender, and the hub guard's
   `_offenders` and `_string_literals` have the same. *Not in scope* was wrong
   about that sweep: a mechanical rename across a tree that contains the guards
   themselves is exactly the change that can disable them.

2. **The never-change list froze 7 of ~20 identifier families.** Its silence
   about the other 13 read as safety. The rule now used to decide: freeze it
   when a rename **silently orphans something that already exists** — a row, a
   file, an installed service, a stored value, a dashboard somebody else wrote.
   Thread names, temp-directory prefixes and log prefixes are deliberately left
   out and say so in the test, so the list stays short enough to read.

## Not in scope

Renaming any identifier. The domain. Code comments, docstrings, `CLAUDE.md` and
`docs/` — internal prose, invisible to customers, and a separate mechanical
sweep that can follow whenever it is convenient and carries no risk.
