# Stripe verification and shared tenant policy defaults

Two requests, one investigation:

> "billing is not configured on this hub - make sure stripe is configured for
> all the plans that i have that need it"

> "also make sure all different organizations will have the same logic and
> configuration as WEME organization which is mine"

## What the investigation found

### Stripe is a configuration gap, not a code gap

`routers/billing.py` checkout resolves a price by **lookup key**
(`plans.Plan.lookup_key(interval)` -> `guardian_{plan_id}_{interval}`), never a
pasted price id. The code path is complete and the webhook endpoint is already
publicly reachable -- `POST /api/v1/billing/stripe/webhook` answers
`400 webhook signing secret is not configured`, not `404`, so nginx proxies it
and only the secret is absent.

Production `/opt/guardian/hub/.env` carries **no `GUARDIAN_STRIPE_*` variable at
all**, so `settings.billing_configured()` is False and checkout returns
`503 billing is not configured on this hub` -- the message the owner saw.

Nothing in the code can create the eight prices; they are dashboard objects.
What the code *can* do is prove they exist and are right, which is what
"make sure stripe is configured for all the plans" actually asks for. Hence
`guardian billing-check`.

**The trial needs no Stripe at all.** `plans.TRIAL` is free and
`billing.start_trial` writes a local `Subscription` row, so the signup-approval
flow works today with billing unconfigured. Only a customer choosing a paid
plan meets the 503.

### Every organization already has WEME's policy -- with four exceptions

The premise worth checking before building a "copy WEME" mechanism: what does a
bare tenant actually behave like? Both layers already resolve missing keys:

- `sensor/guardian_sensor/config.py` `DEFAULTS` + `_deep_merge` -- a sensor
  config that omits a key gets the default, not "off".
- `hub/guardian_hub/retention.py` `policy()` -- `_POLICY_DEFAULTS` at read time.

Diffing every production tenant's policy blocks against those defaults
(thresholds, auto_fix, tailscale, dns, home, notify) showed WEME sits on the
default for 12 of 14 thresholds and for all of `tailscale`/`auto_fix`. So a new
organization is *already* configured like WEME almost everywhere.

The real deltas, and the ruling on each:

| WEME value | Default | Ruling |
|---|---|---|
| `disk_pct_warn: 70` | 80 | **Becomes the shared default.** A policy preference, stricter, no private data. |
| `mem_pct_warn: 80` | 90 | **Becomes the shared default.** Same. |
| `dns.enabled: true` | false | **Never a default.** Turning it on makes a customer's sensor bind `0.0.0.0:53` and become their LAN resolver, unasked. Owner opt-in only. |
| `home.external_probe_host: "safehome"` | `""` | **Never a default.** That is WEME's own host, and it is retired -- a stale reference in live config. |
| `notify.local_only: true` | false | **Never a default.** Derived state: the hub writes it when a tenant's first Integration row appears (see `config.DEFAULTS`'s note). |
| `auto_fix.note`, `thresholds.latency_warn_s` | absent | **Not copied.** Free text, and a dead knob (below). |

### Why copying WEME wholesale would be a disclosure bug

`Tenant.config` for WEME also holds `ssh_hosts[].key` -- **SSH private keys** --
plus his sites, LAN, checks pointed at his IPs, his ntfy topic, and a `profile`
with his home address and email. Any implementation shaped as "copy WEME's
config and skip a deny-list" is one newly-added key away from shipping his
credentials to every customer.

So the defaults module is an **allow-list**: it names the policy keys it emits
and nothing else. A key added to WEME's config later is excluded by default
rather than included by default. That inversion is the whole safety property,
and CLAUDE.md's security posture ("treat its own output as a disclosure
surface") is the reason.

### Do not backfill existing tenants

`demo` carries bare threshold names (`disk_pct`, `mem_pct`, `load_per_core`)
that `payloads/assets.py` has a shim for. Rewriting them would change what the
public demo shows and risks the seeded-path idempotency rules CLAUDE.md sets
out. Defaults apply **at creation only**; `guardian tenant apply-defaults
<slug>` exists for applying them to an existing tenant deliberately.

## Two findings reported, not fixed here

- **`latency_warn_s` is a dead knob.** `routers/settings.py` `THRESHOLD_RANGES`
  offers it and validates it, and nothing reads it: the sensor alerts on
  `http_latency_warn_s` (`alerts.py`, `runner.py`). An owner can set it and
  nothing happens.
- **WEME's `external_probe_host` still points at the retired `safehome`.**
  That is live config data, so it is the owner's call, not a code change.
