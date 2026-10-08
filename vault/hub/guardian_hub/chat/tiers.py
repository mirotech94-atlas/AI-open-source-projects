"""The three chat action tiers (spec S7 "Three action tiers") and the
escalation rule that bumps a tool's declared tier to 3 at dispatch time.

Tier and role are independent axes: `Tool.tier` says whether Vault asks
before acting, `Tool.min_role` mirrors the permission of the matching
dashboard route. Role gates apply on top of tiers (viewer: tier 1 only;
operator: tiers 1-2; admin: all) -- that check lives in `registry.dispatch`.
"""

import ipaddress
import socket
from typing import Literal
from urllib.parse import urlparse

from .. import models as m, platform_settings
from ..config import settings

Tier = Literal[1, 2, 3]

# Port of the sensor's `fix.FIX_ACTIONS` (see also
# `payloads/inbox.py::FIX_ACTIONS`/`MANUAL_ONLY`), split into the two
# `apply_fix` tiers the brief's table names explicitly. `enable_unattended`
# is a real FIX_ACTIONS entry the brief's table does not mention by name --
# it is a reversible settings toggle like `enable_fail2ban`/`enable_ufw`, so
# it is grouped with TIER2_FIXES rather than left ungated.
TIER2_FIXES = {
    "block_ip", "security_updates", "restart_container", "restart_service",
    "enable_fail2ban", "enable_ufw", "cleanup_disk", "harden_fail2ban",
    "install_debsecan", "kill_miner", "enable_unattended",
}
TIER3_FIXES = {
    "reboot", "full_upgrade", "disable_ssh_password", "block_subnet",
    "ban_long", "limit_ssh", "security_headers",
}

#: Every remediation `apply_fix` will accept. An action outside this set is
#: rejected outright by `registry.dispatch` (never silently run at the
#: tool's declared tier 2) -- an LLM can hallucinate an action name, and a
#: new entry added to `sensor/guardian_sensor/fix.py` must be classified
#: here deliberately before chat can run it.
KNOWN_FIXES = TIER2_FIXES | TIER3_FIXES

_RESOLVE_TIMEOUT_S = 2.0
_resolve_cache: dict[str, frozenset[str]] = {}


def _resolve(host: str) -> frozenset[str]:
    """The addresses `host` names, as a set. An IP literal short-circuits
    (no resolver call at all -- `touches_own_port` runs on every proposal,
    twice, and both sides are usually literals). A name is resolved once
    under a 2 s guard and cached, failures included, so a dead resolver
    costs one stall per name per process, not one per proposal."""
    host = (host or "").strip().lower()
    if not host:
        return frozenset()
    try:
        ipaddress.ip_address(host)
        return frozenset({host})
    except ValueError:
        pass
    if host in _resolve_cache:
        return _resolve_cache[host]
    old = socket.getdefaulttimeout()
    out: frozenset[str] = frozenset()
    try:
        socket.setdefaulttimeout(_RESOLVE_TIMEOUT_S)
        out = frozenset({socket.gethostbyname(host)})
    except Exception:
        out = frozenset()
    finally:
        socket.setdefaulttimeout(old)
    _resolve_cache[host] = out
    return out


def _sensor_reported_ip(db, tenant) -> str:
    """The closest thing the sensor reports to "the address Vault is
    reachable on": the WAN IP from the latest scan. Nothing in the sensor
    payload or the `Sensor` model carries the hub's own address, so a
    target equal to this is treated as "own" too -- it is the one
    sensor-sourced address that can identify the hub's edge when
    `base_url` is a name that does not resolve inside the container."""
    if db is None or tenant is None:
        return ""
    try:
        from ..scans import latest_scan
        scan = latest_scan(db, tenant.id)
        return str((scan.raw or {}).get("wan_ip") or "") if scan else ""
    except Exception:      # pragma: no cover - defensive, never break a proposal
        return ""


def _target_host_of(tenant: "m.Tenant", host: str) -> str:
    """The bare hostname/IP an `ssh_hosts` entry's `target` (e.g.
    "root@1.2.3.4") points at, or "" when `host` is not configured."""
    for entry in (tenant.config or {}).get("ssh_hosts") or []:
        if entry.get("name") == host:
            target = entry.get("target", "")
            return target.split("@")[-1] if "@" in target else target
    return ""


def touches_own_port(tenant: "m.Tenant", host: str, action: str, db=None) -> bool:
    """True when a `block_ip`/`enable_ufw` on `host` would affect the same
    machine that serves `settings.base_url` -- i.e. Vault would be
    proposing to (possibly) cut off its own dashboard.

    Both sides are resolved to address sets before comparing, because in
    production `base_url` is a domain while the `ssh_hosts` entry is
    `root@<ip>`: a bare string comparison (the pre-fix behaviour) never
    fired. A target equal to the sensor-reported WAN IP counts as own too.
    """
    if action not in ("block_ip", "enable_ufw"):
        return False
    target_host = _target_host_of(tenant, host)
    if not target_host:
        return False
    base_host = urlparse(settings.base_url).hostname or ""
    if base_host and target_host.lower() == base_host.lower():
        return True
    target_ips, base_ips = _resolve(target_host), _resolve(base_host)
    if target_ips and base_ips and (target_ips & base_ips):
        return True
    own = _sensor_reported_ip(db, tenant)
    return bool(own) and (own.lower() == target_host.lower() or own in target_ips)


def fix_args(*, action: str, host: str, arg: str | None = "") -> dict:
    """The `apply_fix` argument set one repair is described by, everywhere.

    There used to be two of these -- one in `remedy.py` (`action, host,
    arg`) and one in `routers/write.py` (`host, action, arg`) -- because
    the two lanes that need the dict arrive from opposite directions. The
    read lane builds it from a resolved remedy: the inbox row's Fix button,
    the tier `effective_tier` computes below, the chat proposal minted from
    an alert hand-off. The write lane builds it from a posted `FixIn`: the
    confirmation `/fix/confirm` mints and `/fix` burns, and the body queued
    for the sensor. They have to be ONE dict, because a token is bound to
    the canonicalised args (`acttokens.canon_args`) and the job the sensor
    runs is that same dict again -- a proposal and the job queued from it
    describing the same repair differently is a confirmation that will not
    match, discovered by the owner rather than by us.

    The parameters are keyword-only, and that is the whole point of this
    function's signature rather than an accident of style. `host` and
    `action` are both plain strings and the returned shape is identical
    either way round, so a transposed positional call raises nothing: it
    mints a token that will never match, or computes a tier for a host name
    read as an action name, failing far from the call with nothing pointing
    back at it. Keyword-only makes that call a `TypeError` at the call site.
    `tests/test_fix_args.py` fails if the parameters stop being keyword-only
    or a second definition reappears.

    A fix that takes no argument carries no `arg` key at all, rather than
    an empty one. The read lane spells "no argument" `""` (`AlertRemedy.arg`
    is a plain string) and the write lane spells it `None` (`FixIn.arg`
    defaults to None); `canon_args` is `json.dumps`, so those two would
    canonicalise differently and a confirmation minted through one door
    would not burn through the other. Dropping the key is what makes them
    the same repair. It is also the dict the chat model itself emits for an
    argument-less fix, and every reader of the queued body already asks for
    `arg` with a default (`runner.apply_fix` passes `b.get("arg")`).
    """
    args: dict = {"host": host, "action": action}
    if arg:
        args["arg"] = arg
    return args


def _has_successful_fix(db, tenant_id: str, host: str) -> bool:
    return db.query(m.Fix).filter_by(tenant_id=tenant_id, host=host, ok=True).first() is not None


def effective_tier(db, tenant: "m.Tenant", tool, args: dict) -> Tier:
    """The tool's declared tier, escalated to 3 per spec S7:

    - any `apply_fix` action in `TIER3_FIXES` is always tier 3;
    - any action on a host Vault has never successfully fixed before is
      tier 3 ("anything on a host Vault has never fixed before");
    - a `block_ip`/`enable_ufw` that would touch the hub's own listening
      port is tier 3 (callers must say so explicitly in the proposal text --
      see `registry._build_proposal`, which calls `touches_own_port` again);
    - an `apply_fix` action `KNOWN_FIXES` does not classify is tier 3.
    """
    tier: Tier = tool.tier
    if tool.name != "apply_fix":
        return tier

    # The platform-wide pause, enforced here and nowhere else.
    #
    # This function has exactly two callers and they are the two that matter:
    # `remedy.resolve_action`, which decides whether a row offers **Fix** or
    # asks first, and `registry.dispatch`, which is what actually runs a tool.
    # Escalating here therefore changes both the offer and the execution in one
    # move -- a pause implemented in the UI alone would leave chat able to run
    # the same repair.
    #
    # It escalates to 3 rather than refusing outright, which is the whole
    # semantic: "Vault may not act ON ITS OWN" is not "no repair may
    # happen". Every fix still works, and every fix now needs a person to say
    # yes to a specific proposal. A confirmation already granted is still
    # honoured -- `dispatch` checks `confirm_token` BEFORE this tier, so a
    # repair somebody authorised before the pause is not silently revoked.
    if not platform_settings.auto_heal():
        return 3

    action = args.get("action")
    host = args.get("host")

    # Fail closed: an action neither set classifies is never tier 2.
    # `registry.dispatch` rejects it before this point; if some future
    # caller does not, it still asks first rather than running.
    if action not in KNOWN_FIXES:
        return 3
    if action in TIER3_FIXES:
        tier = 3
    if tier < 3 and host and not _has_successful_fix(db, tenant.id, host):
        tier = 3
    if tier < 3 and host and touches_own_port(tenant, host, action, db=db):
        tier = 3
    return tier
