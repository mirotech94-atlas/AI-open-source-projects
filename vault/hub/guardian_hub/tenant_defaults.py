"""The one shared tenant policy every new organization starts on.

The owner's ask was "make sure all different organizations will have the same
logic and configuration as WEME organization which is mine". This module is
that policy -- and the shape it is written in is the whole point.

**It is an allow-list, and must stay one.** `POLICY` below is a literal: it
declares the keys it emits and nothing else. Nothing here reads an existing
tenant's `Tenant.config` and copies keys out of it, and this must never be
re-shaped as "copy WEME's config minus a deny-list". WEME's config holds
`ssh_hosts[].key` -- real SSH private keys -- alongside his sites, his LAN
addressing, checks pointed at his IPs, his ntfy topic and a `profile` with his
home address and email. A copy-with-exclusions is one newly-added config key
away from shipping his credentials to every customer, because the new key is
*included* by default. With an allow-list a key added to WEME's config next
month is *excluded* by default, and somebody has to come here and decide.
CLAUDE.md's security posture -- treat Vault's own output as a disclosure
surface -- is the reason that inversion matters more than the convenience.

**Values track the sensor's own defaults**, `sensor/guardian_sensor/config.py`
`DEFAULTS`, for the six policy blocks below, with exactly two deliberate
overrides taken from WEME's configuration (`disk_pct_warn` 70, `mem_pct_warn`
80 -- both merely stricter, and neither reveals anything). Three of WEME's
other deltas are deliberately *not* here; each is commented at its key.
`hub/tests/test_tenant_defaults.py` parses the sensor file and fails when the
two drift, the same way `test_remedy.py` guards the ported fix catalogue: the
hub cannot import `guardian_sensor`, so a retuned sensor default would
otherwise slip past unnoticed instead of prompting a decision here.

**Threshold spelling.** The `*_warn` / `*_crit` names are canonical -- they are
what the sensor's `DEFAULTS` and `routers/settings.py`'s `THRESHOLD_RANGES`
use. The bare names (`disk_pct`, `mem_pct`, `load_per_core`) that
`payloads/assets.py`'s `th()` also accepts are the legacy half that shim exists
for: they come from a Nexus-imported config.json (and the demo tenant). New
config is written in the canonical spelling only.
"""

import copy

#: The policy blocks a brand-new tenant starts with. Six blocks, no more: no
#: `sites`, `ssh_hosts`, `lan`, `checks`, `heartbeats` or `profile` -- those
#: are a particular network's own data, not policy, and see the allow-list
#: note above for why absence here is the safe default.
POLICY = {
    "thresholds": {
        "http_latency_warn_s": 4.0,
        "cert_days_warn": 21,
        "cert_days_crit": 7,
        # Two deliberate overrides of the sensor default (80/90), adopted from
        # WEME: a stricter warn threshold is a policy preference and carries
        # no private data. The drift test asserts both by value, so retuning
        # the sensor's number forces a decision about this one too.
        "disk_pct_warn": 70,
        "disk_pct_crit": 92,
        "mem_pct_warn": 80,
        "load_per_core_warn": 2.0,
        "failed_ssh_24h_warn": 20000,
        "require_fail2ban": True,
        "attacker_block_24h": 300,
        "fire_after_runs": 2,
        "resend_hours": 6,
        "loss_pct_warn": 20,
        "path_ms_warn": 120,
    },
    "auto_fix": {
        "enabled": True,
        "max_per_day": 30,
        "attacker_method": "auto",
        "digest_hour": 9,
    },
    "tailscale": {
        "enabled": False,
    },
    "dns": {
        # Stays False even though WEME runs with it True. Enabling DNS
        # filtering makes that tenant's sensor bind 0.0.0.0:53 and become the
        # resolver for their whole LAN. Doing that to a customer's network
        # because a default said so would be an unasked-for change to traffic
        # Vault is only supposed to watch. Owner opt-in, always.
        "enabled": False,
        "bind": "0.0.0.0",
        "port": 53,
        "family_filter": False,
        "allow": [],
        "block": [],
        # Empty on purpose, mirroring the sensor's own `DEFAULTS` (see the
        # comment there). The sensor's `dnsfilter.upstreams_for()` only
        # falls through to `DEFAULT_UPSTREAMS` -- Cloudflare's SECURITY
        # resolver, which actually filters malware/phishing -- when
        # `upstreams` is empty; any non-empty value here, including the old
        # plain-Cloudflare pair this used to hold, wins instead and silently
        # turns both the security resolver and `family_filter`'s resolver
        # switch off. This is exactly the bug that shipped: every tenant got
        # the plain pair by default and Cloudflare's own filtering was never
        # on for anyone running stock config.
        "upstreams": [],
    },
    "home": {
        # WEME's value here is the name of his own (now retired) host. Another
        # organization probing it would be probing his network, so the shared
        # default is "no external probe host" and the owner names their own.
        "external_probe_host": "",
        "site_allowed_ports": [],
    },
    "notify": {
        # Derived state, not a preference: the hub writes local_only=True into
        # Tenant.config the moment a tenant's first Integration row is created
        # (see the note in the sensor's DEFAULTS). Seeding it True would tell
        # a sensor with no integrations yet to stop delivering anywhere.
        "local_only": False,
    },
}


def new_tenant_config() -> dict:
    """The config a freshly created tenant starts with: the policy, deep-copied.

    Deep, not shallow: every block is a nested dict and two hold lists
    (`dns.upstreams`, `dns.allow`/`block`, `home.site_allowed_ports`). Handing
    out the module-level objects by reference would mean the first owner who
    tunes a threshold -- or the first test that pokes at one -- mutates the
    policy for every other tenant created in the same process.
    """
    return copy.deepcopy(POLICY)


def legacy_conflicts(config: dict | None) -> list[tuple[str, object, str, object]]:
    """Where adding a canonical threshold would quietly override a legacy one.

    `payloads/assets.py`'s `th()` reads the canonical `*_warn` name first and
    only falls back to the bare name (`disk_pct`) a Nexus-imported config
    carries. So writing `disk_pct_warn: 70` into a config whose only disk
    threshold today is `disk_pct: 90` does not add a setting -- it *changes*
    the effective one, from 90 to 70, without touching the key the owner can
    see. The demo tenant is exactly that shape.

    Returns `(legacy_key, legacy_value, canonical_key, canonical_value)` for
    each such pair so `apply-defaults` can say so out loud. Nothing here
    decides anything: an opt-in that silently retunes a live threshold is the
    one outcome this command must not have.
    """
    out = []
    thresholds = ((config or {}).get("thresholds") or {})
    if not isinstance(thresholds, dict):
        return out
    for canonical, value in POLICY["thresholds"].items():
        if not canonical.endswith("_warn"):
            continue               # only `*_warn` names have a bare twin
        bare = canonical[: -len("_warn")]
        if bare not in thresholds or canonical in thresholds:
            continue
        was = thresholds[bare]
        if was == value:
            continue   # same number under both spellings: nothing to warn about
        out.append((bare, was, canonical, value))
    return out


def apply_defaults(config: dict | None) -> tuple[dict, list[str], list[str]]:
    """Fill policy keys `config` is missing, never overwriting what it has.

    Returns `(new_config, added, kept)` where `added`/`kept` are dotted paths
    (`thresholds.disk_pct_warn`). An owner who tuned `disk_pct_warn` to 60
    keeps 60 -- that is the difference between "apply the defaults" and
    "reset to the defaults", and only the first is ever safe to run against a
    live tenant.

    The merge is per-leaf, not per-block: a tenant that already has a
    `thresholds` dict but no `mem_pct_warn` gets `mem_pct_warn`. Skipping a
    block because it exists would make this command a no-op on exactly the
    tenants that need it.

    A legacy bare name (`disk_pct`, which `payloads/assets.py` still reads)
    does *not* count as `disk_pct_warn` being present, so this adds the
    canonical spelling alongside it. That is intended: the command is a
    deliberate opt-in, and canonicalising the spelling is part of what the
    owner is opting into.
    """
    out = copy.deepcopy(dict(config or {}))
    added: list[str] = []
    kept: list[str] = []
    for block, values in POLICY.items():
        current = out.get(block)
        if not isinstance(current, dict):
            # Absent, or present as something that is not a block (a null left
            # by an old write). Either way the policy block is what goes in.
            out[block] = copy.deepcopy(values)
            added.extend(f"{block}.{k}" for k in values)
            continue
        for key, value in values.items():
            if key in current:
                kept.append(f"{block}.{key}")
            else:
                current[key] = copy.deepcopy(value)
                added.append(f"{block}.{key}")
    return out, added, kept


# --- migrating the old plain-Cloudflare default off existing tenants --------

#: The literal value `POLICY["dns"]["upstreams"]` (and the sensor's own
#: `DEFAULTS`) used to carry, in this exact order, before both became `[]`.
#: `dns_upstreams_migration` below rewrites a tenant's stored config only
#: when it matches this precisely -- a single custom entry, the pair
#: reordered, or an already-empty list are all left alone, because any of
#: those means somebody (an operator, or a previous run of this migration)
#: already made a choice, and this command is not allowed to make a second
#: one for them. Same rule CLAUDE.md states for `trusted`/`label`: an
#: automatic path must never overwrite a human's.
OLD_PLAIN_UPSTREAMS = [
    "https://cloudflare-dns.com/dns-query",
    "https://dns.quad9.net/dns-query",
]


def dns_upstreams_migration(config: dict | None) -> tuple[dict, str]:
    """`(new_config, status)` for `guardian tenant migrate-dns-upstreams`.

    Rewrites `config["dns"]["upstreams"]` to `[]` iff it is *exactly*
    `OLD_PLAIN_UPSTREAMS`, so `dnsfilter.upstreams_for()`'s fallthrough can
    reach `DEFAULT_UPSTREAMS` (the security resolver) the next time this
    tenant's sensor pulls its config. Every other shape -- missing `dns`,
    missing `upstreams`, already `[]`, a single entry, a different resolver,
    the same two reordered -- is left untouched and reported as skipped,
    with the reason, so an operator can see exactly what did and did not
    change before deciding whether a skip needs a human follow-up.

    Never mutates the dict passed in: `config` may be a live `Tenant.config`
    the caller has not decided yet to write back (see the CLI's dry-run).
    """
    cfg = copy.deepcopy(dict(config or {}))
    dns = cfg.get("dns")
    if not isinstance(dns, dict) or "upstreams" not in dns:
        return cfg, "skipped: no dns.upstreams key"
    current = dns.get("upstreams")
    if current == []:
        return cfg, "skipped: already empty"
    if current == OLD_PLAIN_UPSTREAMS:
        dns["upstreams"] = []
        return cfg, "migrated: was the old plain-Cloudflare pair"
    return cfg, f"skipped: custom value {current!r}"
