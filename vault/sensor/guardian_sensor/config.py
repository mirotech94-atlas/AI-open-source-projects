"""Sensor configuration: load the user's config.json, deep-merged over defaults.

Defaults mirror the values shipped in the Nexus reference config
(~/.claude/nexus-agent/config.json) so that downstream code can safely index
any top-level key without a KeyError, even on a freshly-enrolled sensor with
a near-empty config.json.
"""
import json
import os
import re

from . import paths

SLUG_MAX = 24

#: The sub-keys a `lan:` alert key can carry. Duplicated deliberately from
#: `hub/guardian_hub/networks.LAN_SUBKEYS` -- the sensor imports nothing from
#: the hub -- because a network slug may never BE one of them: the namespaced
#: key shape `lan:<slug>:<sub>:<rest>` is told from the legacy `lan:<sub>:…`
#: one by testing whether the segment after `lan:` is a sub-key.
LAN_SUBKEYS = ("baseline", "rotated", "new", "missing", "port", "offline",
               "flapping", "unverified", "unusual-hour",
               # Task 69: cameras and the rogue-device findings.
               "camera-new", "camera-gone", "offsubnet", "second-router")


def slugify(name):
    """`"Office Wi-Fi"` -> `"office-wi-fi"`. The same rule as the hub's
    `networks.slugify`, including the reserved-sub-key `-net` suffix."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    s = s[:SLUG_MAX].rstrip("-")
    if s in LAN_SUBKEYS:
        s = f"{s}-net"[:SLUG_MAX].rstrip("-")
    return s or "network"

DEFAULTS = {
    "sites": [],
    "ssh_hosts": [],
    # Task 52: several watched networks per sensor. Empty means "the legacy
    # `lan` block is the one and only network" -- `load()` normalises that
    # into a one-entry list, and `lan` keeps pointing at the primary's values
    # so every pre-Task-52 reader is unchanged.
    "networks": [],
    "lan": {
        "enabled": True,
        "subnet_prefix": "192.168.1",
        "expected_gateway": "192.168.1.1",
        "watch_ips": {},
    },
    "tailscale": {
        "enabled": False,
    },
    "thresholds": {
        "http_latency_warn_s": 4.0,
        "cert_days_warn": 21,
        "cert_days_crit": 7,
        "disk_pct_warn": 80,
        "disk_pct_crit": 92,
        "mem_pct_warn": 90,
        "load_per_core_warn": 2.0,
        "failed_ssh_24h_warn": 20000,
        "require_fail2ban": True,
        "attacker_block_24h": 300,
        "fire_after_runs": 2,
        "resend_hours": 6,
        "loss_pct_warn": 20,
        "path_ms_warn": 120,
    },
    "home": {
        "external_probe_host": "",
        "site_allowed_ports": [],
    },
    "capture": {
        # "" = no mirror port; flows fall back to router counters when the
        # router integration exposes them, else mode "none". Never guessed.
        "interface": "",
        "snaplen": 128,
    },
    "auto_fix": {
        "enabled": True,
        "max_per_day": 30,
        "attacker_method": "auto",
        "digest_hour": 9,
    },
    "heartbeats": {},
    # Task 40: the hub now owns alert delivery for every non-local channel
    # (ntfy/Slack/Teams/webhook). `local_only=True` is written into
    # `Tenant.config` by the hub the moment its first Integration row is
    # created, at which point `notify.notify()` stops even trying the
    # network and only shows the macOS banner. See notify.py's docstring.
    "notify": {"local_only": False},
    "checks": [],
    # Backup jobs to verify. Vault does not take backups; it checks that
    # whatever already runs is still producing plausible artifacts. Each is
    # {name, host, path, every_hours, min_bytes?} -- see `backups.py`.
    "backups": [],
    "dns": {
        "enabled": False,
        "bind": "0.0.0.0",
        "port": 53,
        "family_filter": False,
        "allow": [],
        "block": [],
        # Empty on purpose. `dnsfilter.upstreams_for()` treats a non-empty
        # `upstreams` as "an operator who has named their own resolver" and
        # lets it win over both `dnsfilter.DEFAULT_UPSTREAMS` (Cloudflare's
        # SECURITY resolver, which filters malware/phishing) and
        # `FAMILY_UPSTREAMS`. A shipped default was never that -- it used to
        # be the *plain* Cloudflare pair here, which meant every stock
        # install silently got zero upstream filtering and `family_filter`
        # could never switch resolvers either, because this key's mere
        # presence always beat it. Leave this `[]` and let `dnsfilter`'s own
        # defaults decide.
        "upstreams": [],
    },
}


def _deep_merge(defaults, override):
    """Return a new dict: defaults with override's values layered on top,
    recursing into nested dicts so partial user config doesn't drop sibling
    default keys."""
    out = dict(defaults)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path=None):
    """Load config.json (deep-merged over DEFAULTS for every top-level key).

    path defaults to paths.p("config.json"). Missing top-level keys and
    missing nested default fields (e.g. thresholds.cert_days_warn) are
    always filled in so callers can index them without a KeyError.
    """
    if path is None:
        path = paths.p("config.json")
    user_cfg = {}
    if os.path.exists(path):
        with open(path) as f:
            user_cfg = json.load(f)
    merged = dict(DEFAULTS)
    for key, default_value in DEFAULTS.items():
        if isinstance(default_value, dict):
            merged[key] = _deep_merge(default_value, user_cfg.get(key, {}) or {})
        else:
            merged[key] = user_cfg.get(key, default_value)
    # carry over any extra top-level keys the user config has that aren't
    # in DEFAULTS (e.g. owner, public_url, roles) unchanged.
    for key, value in user_cfg.items():
        if key not in DEFAULTS:
            merged[key] = value
    normalise_networks(merged)
    return merged


# ---------------- networks ----------------
def _entry_from_lan(lan):
    """The legacy `lan` block as one network entry (the primary)."""
    return {
        "name": lan.get("name") or "Home network",
        "kind": "wifi",
        "subnet_prefix": lan.get("subnet_prefix") or "",
        "expected_gateway": lan.get("expected_gateway") or "",
        # The public address this network is expected to leave through. Set it
        # and an unexpected egress address becomes a finding instead of a note.
        "expected_wan_ip": lan.get("expected_wan_ip") or "",
        "enabled": lan.get("enabled", True),
        "interface": None,
    }


def normalise_networks(cfg):
    """Fill in `cfg["networks"]` from the legacy `lan` block when it is empty,
    and either way leave `cfg["lan"]` pointing at the PRIMARY (first) network's
    values -- mutated in place, so `cfg["lan"]["watch_ips"]` and every other
    legacy reader (`alerts`, `home.home_security`, `lan.lan_sweep`) is
    unchanged. Returns cfg."""
    lan = cfg.setdefault("lan", {})
    nets = cfg.get("networks")
    # Internal-only marker (never written back to config.json — normalise
    # runs on the merged in-memory cfg, not `raw_cfg`): True once the user's
    # own config.json defines a `networks` list, even a one-entry one, so
    # `runner._hist_live_macs` can tell a real Task-52 setup from a bare
    # pre-Task-52 `lan` block that was merely promoted into one entry here.
    cfg["_networks_configured"] = bool(nets)
    if not isinstance(nets, list) or not nets:
        cfg["networks"] = nets = [_entry_from_lan(lan)]
    prim = nets[0] if isinstance(nets[0], dict) else {}
    if prim.get("name") and not lan.get("name"):
        lan["name"] = prim["name"]
    if prim.get("subnet_prefix"):
        lan["subnet_prefix"] = prim["subnet_prefix"]
    if prim.get("expected_gateway"):
        lan["expected_gateway"] = prim["expected_gateway"]
    if prim.get("expected_wan_ip"):
        lan["expected_wan_ip"] = prim["expected_wan_ip"]
    if "enabled" in prim:
        lan["enabled"] = prim["enabled"]
    return cfg


def networks_of(cfg):
    """Every configured network, in config order, each a COPY carrying the
    computed `slug`, `primary` flag and the defaults the scan loop needs.

    A config with no `networks` block is one network built from `lan`, so a
    pre-Task-52 sensor behaves exactly as before with slug `home-network`
    (or whatever `lan.name` slugs to).
    """
    lan = cfg.get("lan") or {}
    raw = cfg.get("networks")
    if not isinstance(raw, list) or not raw:
        raw = [_entry_from_lan(lan)]
    out = []
    seen = set()
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            continue
        primary = not out            # the first usable entry is the primary
        slug = e.get("slug") or slugify(e.get("name") or "")
        while slug in seen:          # a duplicate name must not share a slug
            slug = f"{slug[:SLUG_MAX - 2]}-{len(seen) + 1}"
        seen.add(slug)
        net = dict(e)
        net.update(
            slug=slug,
            name=e.get("name") or "Home network",
            kind=e.get("kind") or "wifi",
            subnet_prefix=e.get("subnet_prefix") or "",
            expected_gateway=e.get("expected_gateway") or "",
            expected_wan_ip=e.get("expected_wan_ip") or (lan.get("expected_wan_ip") or "" if primary else ""),
            enabled=bool(e.get("enabled", True)),
            interface=e.get("interface"),
            primary=primary,
            # only the primary inherits the legacy watch list
            watch_ips=e.get("watch_ips") or (dict(lan.get("watch_ips") or {}) if primary else {}),
        )
        out.append(net)
    return out


def primary_slug(cfg):
    nets = networks_of(cfg)
    return nets[0]["slug"] if nets else "network"
