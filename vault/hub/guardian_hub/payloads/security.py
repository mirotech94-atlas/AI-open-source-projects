"""Security tab payload: scorecards, the Wi-Fi guard verdict, the checklist
with proof, attackers with geo, and the exposure tiles (spec S9 / Task 28).

Reads `Scan.raw` (the sensor's `last.json`-shaped blob: `ssh[*].sec`,
`sites[*].sec`, `home`, `rogue`, `external_sites`, `admin_probe`,
`email_sec`) plus `Host`, `Site`, `Alert` and `Fix`. Deductions are computed
here (`_weighted`), not read from a `score.deductions` field on the raw
blob -- there is no such field.

Three deliberate divergences from the task-28 brief's prose, all forced by
`schemas_dash.py` (the binding contract -- `web/src/types.ts` mirrors it
field-for-field and `tests/test_contracts.py` pins that mirror, and this
module must never add/rename a field):

- `Scorecard.fix` is a plain `str`, not a `NextAction`. It holds the
  `FIX_ACTIONS` key of the highest-value auto-fixable repair (e.g.
  "enable_ufw"), or "" when nothing here is auto-fixable -- "" stands in
  for the brief's "null".
- `SecurityCheck.autofix` is documented in the schema as "fix action name,
  '' when there is none", but `tests/test_security.py` (the executable
  contract) asserts it is one of the literal strings "auto"/"manual"/
  "none". The test wins; that comment is stale.
- `WifiGuard`/`SecurityCheck.passed` etc. use whatever plain-string state
  vocabulary reads best (`good|warn|bad` for the guard, `ok|warn|crit` for
  tiles) since the TS type is just `string` -- nothing pins a closed enum.
"""

import datetime as dt
import re
import time

from sqlalchemy import func
from sqlalchemy.orm import Session as DbSession

from .. import geoip, models as m, networks, schemas_dash as sd
from ..scans import latest_scan
from ..schemas_dash import (
    Attacker, CheckDecision, CheckDetail, Deduction, DomainRow, EmailAuthRow, ExposureTile,
    PostureCoverage,
    ExtPortRow, ExtraCheck, FixLogRow, HeartbeatRow, NetworkHealth, NetworkRef, Scorecard,
    SecurityCheck, SecurityPayload, WifiGuard,
    BlockedLookup, DeviceDefence, ProtectionPanel, ThreatClass, ThreatSignal, ThreatsBlocked,
)
from . import account as account_payload
from . import assets as assets_payload
from . import inbox as inbox_payload
from . import staleness
from ._util import _now, effective_presence

# Six candidate methods the Wi-Fi/attacker "how should we handle this IP"
# control offers (spec S9); `off` disables auto-handling entirely.
METHODS = ["auto", "block_ip", "block_subnet", "ban_long", "limit_ssh", "off"]

# At most this many *new* (cache-miss) ip-api.com calls per /security
# build -- the free tier is 45/min, so a tenant with more attacker IPs than
# this in one scan gets partial geo data now and the rest on the next load
# (brief S9). A cached IP's `geoip.lookup` never touches the network, so it
# never counts against this cap.
MAX_GEO_LOOKUPS = 15

# Wall-clock ceiling (seconds) on *new* geo lookups for one /security build.
# Sequential ip-api.com calls can each take up to geoip.TIMEOUT_S, so
# without this a slow/hanging provider could stall the whole route for
# MAX_GEO_LOOKUPS * TIMEOUT_S (~60s). Once elapsed uncached-lookup time
# crosses this budget, remaining attackers get geo=None -- they'll be
# looked up (and cached) on a later request.
GEO_BUDGET_S = 3.0

# Host hardening check key -> the FIX_ACTIONS name that repairs it (ported
# alongside assets.HARDENING_CHECKS, which defines the seven check keys).
HARDENING_FIX = {
    "ssh_password": "disable_ssh_password", "ufw": "enable_ufw",
    "fail2ban": "enable_fail2ban", "security_updates": "security_updates",
    "unattended": "enable_unattended", "malware": "kill_miner", "reboot": "reboot",
}

_ATTACKER_KEY_RE = re.compile(r"^sec:attacker:([^:]+):(.+)$")
_FAILS_RE = re.compile(r"(\d+)\s+failed")


# --------------------------------------------------------------------------
# The Wi-Fi guard / checklist drill-down (owner request: clicking a failing
# check like "evil twin" should open detail and offer resolve-or-leave-it,
# same for Security posture).
# --------------------------------------------------------------------------

# `SecurityCheck.key` prefix (everything before the first ":") -> the
# `HARDENING_FIX` action that repairs it. Only the five host checks that
# have BOTH an entry in `HARDENING_FIX` and a matching row in `_host_checks()`
# below carry one; `unattended`/`reboot` score the host scorecard (see
# `assets_payload.HARDENING_CHECKS`) but have no checklist row of their own
# today, and every other check (Wi-Fi, site headers/TLS, admin panels,
# email, exposure, cameras) has no Vault-run repair at all.
_FIX_ACTION_BY_PREFIX = {
    "ssh": "disable_ssh_password", "firewall": "enable_ufw",
    "bruteforce": "enable_fail2ban", "patching": "security_updates",
    "malware": "kill_miner",
}


def _fix_action_for(key: str) -> str:
    return _FIX_ACTION_BY_PREFIX.get(key.split(":", 1)[0], "")


#: The three "Security headers present" facts `security_headers_cmd`
#: (`sensor/guardian_sensor/fix.py`) can safely add on its own -- CSP and
#: the HTTPS redirect are deliberately excluded (see that function's
#: docstring: CSP needs to know the site's own script/style origins, and a
#: redirect means editing a different, plain-HTTP server block). Ordered
#: for the copy this builds, not for anything mechanical.
_AUTOFIXABLE_HEADERS = ("hsts", "nosniff", "frame")


_RESOLVE_TTL_S = 300
_resolve_cache: dict[str, tuple[float, set[str]]] = {}


def _resolve(host: str) -> set[str]:
    """Every address `host` resolves to (empty on failure), cached briefly:
    `checks()` runs per poll and a DNS round trip per site per poll is not
    worth it."""
    import socket
    import time
    host = (host or "").strip()
    if not host:
        return set()
    hit = _resolve_cache.get(host)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except OSError:
        addrs = set()
    _resolve_cache[host] = (time.monotonic() + _RESOLVE_TTL_S, addrs)
    return addrs


def _infer_ssh_host(tenant, domain: str) -> tuple[str, str]:
    """`(ssh_host_name, why_not)`: the SSH host whose address is the address
    `domain` resolves to, when exactly one is -- Vault already holds SSH
    to that server, so the missing `ssh_host` mapping is not a reason to
    refuse. Otherwise a `why_not` that names the site's address and the
    hosts Vault does have, so the owner can see the gap instead of being
    told "no SSH" while holding keys to two servers."""
    hosts = [h for h in (tenant.config or {}).get("ssh_hosts") or [] if h.get("name")]
    site_addrs = _resolve(domain)
    matches = []
    described = []
    for h in hosts:
        target = (h.get("target") or "")
        addr = target.split("@", 1)[-1].split(":", 1)[0]
        host_addrs = _resolve(addr) or ({addr} if addr else set())
        described.append(f"{h['name']} ({addr or '?'})")
        if site_addrs and site_addrs & host_addrs:
            matches.append(h["name"])
    if len(matches) == 1:
        return matches[0], ""
    site_addr = ", ".join(sorted(site_addrs)) or "an address I could not resolve"
    if not hosts:
        return "", (f"The server behind {domain} ({site_addr}) is not one I hold SSH to -- there are "
                    f"no SSH hosts configured yet. Add that server under Settings > Targets > SSH "
                    f"hosts, then set it as this site's SSH host, and I can add the missing headers")
    if len(matches) > 1:
        return "", (f"{domain} ({site_addr}) matches more than one of my SSH hosts "
                    f"({', '.join(matches)}); set which one serves it as this site's SSH host under "
                    f"Settings > Targets and I can add the missing headers")
    return "", (f"The server behind {domain} ({site_addr}) is not one of the hosts I hold SSH to: "
                f"{', '.join(described)}. Add that server under Settings > Targets > SSH hosts, "
                f"then set it as this site's SSH host, and I can add the missing headers")


def _headers_fix_target(tenant, site_name: str, sec: dict) -> tuple[str, str, str, str]:
    """`(action, host, arg, why_not)` for one site's "Security headers
    present" check -- the honest answer to "can Vault add these itself?"
    that used to be a flat "no" regardless of the true reason (see
    `remedy.py`'s module docstring: safehome.example.com is on a host Vault
    already holds SSH to, so "not something Vault can change from here"
    was false).

    `action`/`host` are "" (with `why_not` naming the real reason) when:
      - none of the three headers this fix knows how to set
        (`_AUTOFIXABLE_HEADERS`) are actually missing -- only CSP and/or the
        HTTPS redirect are, and those still need the owner (category 3:
        CSP needs the site's own origins; the redirect is a separate,
        not-yet-built fix), or
      - the site has no `ssh_host` configured in `Tenant.config["sites"]`
        (category 2: Vault genuinely has no SSH access to name).

    Never guesses which host serves a site from its URL alone -- a site can
    sit behind a CDN or a host with no `ssh_hosts` entry at all, so this
    reads the explicit mapping an admin set under Settings > Targets, or
    says plainly that no mapping is set.
    """
    missing = [k for k in _AUTOFIXABLE_HEADERS if not sec.get(k)]
    if not missing:
        # `sec` already carries `csp` and `https_redirect` as real booleans --
        # say which one is actually missing rather than naming both as an
        # "or" guess, and give an actionable next step for each rather than
        # only explaining the refusal. A person who hears "I won't guess"
        # and nothing else has learned that Vault declined, not what to
        # do about it.
        csp_missing = not sec.get("csp")
        redirect_missing = not sec.get("https_redirect")
        if csp_missing and redirect_missing:
            why_not = ("Content-Security-Policy is missing and the site does not force HTTPS. "
                       "Both need the owner: a CSP has to name every script, style and font "
                       "origin the site actually uses, which I cannot see from outside, and a "
                       "wrong one breaks the page rather than securing it.")
        elif csp_missing:
            why_not = ("Content-Security-Policy is the one missing here. I will not set it "
                       "myself: it has to name every script, style, image and font origin the "
                       "site actually loads, and a policy that misses one breaks the page "
                       "instead of securing it. Start in Report-Only mode -- "
                       "Content-Security-Policy-Report-Only -- so a wrong entry only logs "
                       "instead of blocking anything, watch the browser console on a real visit "
                       "for a few days, then switch it to enforcing once nothing is flagged.")
        else:
            why_not = ("HTTP still answers instead of redirecting to HTTPS. That is a change to "
                       "a different, plain-HTTP server block from the one I would edit for the "
                       "other headers, and I have not built that fix yet -- add "
                       "`return 301 https://$host$request_uri;` to that block's `server {}` and "
                       "reload nginx.")
        return "", "", "", why_not
    site_cfg = next((s for s in (tenant.config or {}).get("sites") or []
                     if (s.get("name") or "").strip() == site_name.strip()), None)
    domain = (site_cfg or {}).get("host") or re.sub(r"^https?://([^/]+).*", r"\1", site_cfg.get("url", "") if site_cfg else "")
    ssh_host = (site_cfg or {}).get("ssh_host") or ""
    if not ssh_host:
        ssh_host, why_not = _infer_ssh_host(tenant, domain)
        if not ssh_host:
            return "", "", "", why_not
    return "security_headers", ssh_host, f"{domain}|{','.join(missing)}", ""


# `SecurityCheck.key` (exact) or its ":"-prefix -> (confidence, why this
# specific check matters, what to go check). Exact keys win over a prefix so
# the four Wi-Fi guard checks (which all share no useful prefix split among
# themselves) and "wifi:home" (the encryption check in the general checklist,
# prefix "wifi") each get their own copy.
#
# `wifi:evil_twin`'s copy is the one the owner named directly, and the one
# this table has to get right: a second AP broadcasting the same SSID on
# another channel is exactly what an evil-twin attack looks like, and
# exactly what a mesh node or Wi-Fi extender looks like -- Vault cannot
# tell those apart by radio signal alone, so this says that plainly rather
# than dressing up an ambiguous reading as a confirmed attack (or burying a
# real risk behind "probably fine").
_CHECK_COPY: dict[str, tuple[str, str, str]] = {
    "wifi:evil_twin": (
        "confirmed",
        "An access point is broadcasting your network's name with DIFFERENT encryption from "
        "your own. That is the shape of a Wi-Fi eavesdropping attack: a rogue access point "
        "mimics your name but has to offer weaker security -- often none at all -- because it "
        "does not know your password and still needs devices to connect to it. This check "
        "deliberately ignores a twin that matches your own encryption: every dual-band router "
        "puts one name on 2.4GHz and 5GHz, and every mesh node repeats it again, so the name "
        "appearing on several channels is your own equipment and is not reported.",
        "Do not connect to it, and tell anyone on this network not to either -- a device that "
        "joins it hands its traffic to whoever is running it. Change your Wi-Fi password from "
        "the router's admin page, which disconnects everything and forces a reconnect to the "
        "real network, then look for an access point you do not recognise.",
    ),
    "wifi:rogue_dhcp": (
        "confirmed",
        "A device other than your router just handed out a network lease. Whoever controls that "
        "device now decides the gateway and DNS server every new device on the network gets, "
        "which puts it in a position to see or redirect their traffic.",
        "Find and unplug (or reconfigure) the extra router or access point handing out "
        "addresses; its own address is in the evidence above.",
    ),
    "wifi:gateway_drift": (
        "confirmed",
        "Traffic is leaving through a different address than the one configured as your "
        "gateway. If that address is not your router, something else on the network is now "
        "sitting in the path of everything you send.",
        "Confirm the gateway shown above is your own router. If its MAC address changed "
        "unexpectedly, power-cycle the router and check again.",
    ),
    "wifi:unverified": (
        "confirmed",
        "One or more devices on this network have never been confirmed as yours. Vault keeps "
        "track of them, but only you know which of them belong here.",
        "Open Assets and name the devices you recognise; whatever is left over is worth a "
        "closer look.",
    ),
    "wifi:home": (
        "confirmed",
        "Your Wi-Fi is set to weak or no encryption, so anyone within range can read the "
        "traffic on it, or join it outright, without a password.",
        "Set the Wi-Fi security to WPA2 or WPA3 from your router's admin page, then rejoin your "
        "devices.",
    ),
    "ssh": (
        "confirmed",
        "Password logins on this host can be guessed or phished; a key is the one credential "
        "that cannot be handed over by accident.",
        "Vault can switch this host to key-only login.",
    ),
    "firewall": (
        "confirmed",
        "With no firewall active, every service listening on this host is reachable by anyone "
        "who can route to it, not only the ports you meant to expose.",
        "Vault can enable a firewall that allows the ports already listening and nothing "
        "else.",
    ),
    "bruteforce": (
        "confirmed",
        "Without fail2ban, repeated failed logins never get an address banned, so a slow, "
        "patient guesser can keep trying indefinitely.",
        "Vault can turn on fail2ban for this host.",
    ),
    "patching": (
        "confirmed",
        "A pending security update is a published weakness until it is installed -- the longer "
        "it waits, the more widely the fix (and the bug it fixes) is known.",
        "Vault can install the pending security updates.",
    ),
    "malware": (
        "confirmed",
        "A process matching a known miner or rootkit signature is active on this host right "
        "now.",
        "Vault can kill the matching process -- worth a quick check first that it is not "
        "something you run intentionally.",
    ),
    "drift": (
        "confirmed",
        "A tracked configuration file changed since Vault last recorded it. Vault keeps "
        "only a fingerprint of the file, never its contents or a prior fingerprint, so it cannot "
        "show a line-by-line difference.",
        "Confirm the change was yours; if it was not, treat the host as compromised.",
    ),
    "headers": (
        "confirmed",
        "A missing security header makes this site easier to exploit in a visitor's browser -- "
        "clickjacking, MIME-type sniffing and script injection all get easier when the header "
        "that blocks them is absent.",
        # Dead in practice: `check_detail` always overrides this with the
        # exact, per-site reason from `_headers_fix_target` (a missing
        # ssh_host mapping, or that only CSP/the redirect are missing).
        # Kept generic-but-honest as the fallback if that ever changes.
        "Vault can add HSTS, X-Content-Type-Options and X-Frame-Options itself on a host it "
        "has SSH to, once that site names which host under Settings > Targets; a Content-Security-"
        "Policy or an HTTPS redirect still needs you.",
    ),
    "tls": (
        "confirmed",
        "An old TLS version accepts weaker ciphers than modern browsers expect, and some of "
        "those are already considered broken.",
        "Disable the legacy TLS versions in the site's web server configuration.",
    ),
    "admin": (
        "confirmed",
        "An admin panel answering from the internet is a target for credential stuffing whether "
        "or not it is linked from anywhere.",
        "Put the panel behind a login wall or a firewall rule, or remove the port forward that "
        "exposes it.",
    ),
    "email": (
        "confirmed",
        "Without SPF and an enforcing DMARC policy, anyone can send email that appears to come "
        "from this domain.",
        "Add or correct the SPF and DMARC records at your DNS provider.",
    ),
    "exposure": (
        "confirmed",
        "A port answering from the internet is reachable by anyone scanning for it, not only the "
        "people you meant to reach.",
        "Remove the port forward or UPnP mapping at your router, or ask Vault to firewall the "
        "host instead.",
    ),
    "cameras": (
        "confirmed",
        "A camera or its recorder answering from the internet can be found by anyone scanning "
        "for it -- camera feeds are a common target precisely because they are easy to find this "
        "way.",
        "Remove the forwarded camera port at your router; view the feed through a VPN or the "
        "vendor's cloud app instead.",
    ),
}


def _what_we_saw(check: SecurityCheck, raw: dict) -> str:
    """Vault's own sentence for what it saw, for the four Wi-Fi guard
    checks -- built from the live facts, not the evidence log fragment
    (`twin_channels: [6]` is proof for an engineer; "also broadcasting on
    channel 6" is what a homeowner reads). Every other check's evidence is
    already a plain-enough fact (`ufw status: active`), so those just get a
    lead-in sentence rather than a bespoke builder.
    """
    home = raw.get("home") or {}
    rogue = raw.get("rogue") or {}
    wifi = home.get("wifi") or {}
    ssid = wifi.get("ssid") or "your network"

    if check.key == "wifi:evil_twin":
        flagged, why = _evil_twin(wifi, rogue)
        if flagged:
            return (f'An access point is broadcasting "{ssid}" with {why}. Your own router and '
                    f'any mesh nodes would use the same encryption you do.')
        channel = _channel_int(wifi.get("channel"))
        twins = [c for c in (rogue.get("twin_channels") or []) if c != channel]
        if twins:
            plural = "s" if len(twins) > 1 else ""
            chans = ", ".join(str(c) for c in twins)
            return (f'"{ssid}" is also on channel{plural} {chans}, with the same encryption as '
                    f'the network you are on -- that is your own second radio or a mesh node, '
                    f'not a stranger.')
        return f'No other access point is broadcasting "{ssid}".'

    if check.key == "wifi:rogue_dhcp":
        expected_gw = home.get("gateway") or ""
        dhcp_server = rogue.get("dhcp_server") or ""
        if dhcp_server and expected_gw and dhcp_server != expected_gw:
            return (f"A device at {dhcp_server} handed out a network address lease. Your router is "
                    f"{expected_gw}.")
        return "The device handing out network addresses matches your router."

    if check.key == "wifi:gateway_drift":
        expected_gw = home.get("gateway") or ""
        default_gw = rogue.get("default_gateway") or ""
        if default_gw and expected_gw and default_gw != expected_gw:
            return f"Traffic is leaving through {default_gw}. Your configured gateway is {expected_gw}."
        return "Traffic is leaving through your configured gateway."

    if check.key == "wifi:unverified":
        n = home.get("unverified") or []
        count = len(n) if isinstance(n, list) else int(check.evidence.split(":")[-1].strip() or 0)
        if count:
            return f"{count} device{'s' if count != 1 else ''} on this network have not been confirmed as yours."
        return "Every device on this network has been confirmed as yours."

    return f"{check.title} on {check.target}: {check.evidence}."


def _check_copy(key: str) -> tuple[str, str, str]:
    if key in _CHECK_COPY:
        return _CHECK_COPY[key]
    prefix = key.split(":", 1)[0]
    if prefix in _CHECK_COPY:
        return _CHECK_COPY[prefix]
    return ("confirmed", "This affects the security of your network or hosts.",
            "Ask Vault in chat what it would need from you to look into this further.")


def _decisions_by(db: DbSession, tenant) -> dict[tuple[str, str, str], "m.CheckDecision"]:
    rows = db.query(m.CheckDecision).filter_by(tenant_id=tenant.id).all()
    return {(r.check_key, r.network_id, r.target): r for r in rows}


def _decision_out(row: "m.CheckDecision | None") -> CheckDecision:
    if row is None:
        return CheckDecision()
    return CheckDecision(
        status=row.status, reason=row.reason, decided_by=row.decided_by,
        decided_at=row.decided_at.isoformat(timespec="seconds") if row.decided_at else "",
    )


def _attach_decisions(rows: list[SecurityCheck], decisions: dict, network_id: str = "") -> list[SecurityCheck]:
    for row in rows:
        row.decision = _decision_out(decisions.get((row.key, network_id, row.target)))
    return rows


def _grade(score: int) -> str:
    return "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"


def _autofix(key: str) -> str:
    """One of "auto"/"manual"/"none" from `inbox.FIX_ACTIONS`/`MANUAL_ONLY`
    membership -- see the module docstring on why this isn't a fix-action
    name despite the schema field comment."""
    if not key or key not in inbox_payload.FIX_ACTIONS:
        return "none"
    return "manual" if key in inbox_payload.MANUAL_ONLY else "auto"


def _weighted(checks: list[tuple[str, bool, str, float]] | list[tuple[str, bool, str, float, str]],
             ) -> tuple[int, list[Deduction]]:
    """`checks` = (category, passed, why, weight[, key]). Weights need not
    sum to 100 -- score is normalized. `key` is the matching
    `SecurityCheck.key` (see `_CHECK_KEY_BY_CATEGORY` in each caller below),
    "" when this deduction has no counterpart row in `checks()`/`wifi_guard()`
    -- omitted entirely, a 4-tuple, for a caller that predates it. Returns
    (score 0-100, top-3 deductions by points lost, largest first)."""
    rows = [(c[0], c[1], c[2], c[3], c[4] if len(c) > 4 else "") for c in checks]
    total = sum(w for _, _, _, w, _ in rows) or 1.0
    lost = [(cat, w, why, key) for cat, ok, why, w, key in rows if not ok]
    score = round(100 * (total - sum(w for _, w, _, _ in lost)) / total)
    lost.sort(key=lambda x: -x[1])
    deds = [Deduction(category=cat, points=round(w), why=why, key=key) for cat, w, why, key in lost[:3]]
    return score, deds


def _int_from(s) -> int | None:
    m_ = re.search(r"-?\d+", str(s or ""))
    return int(m_.group()) if m_ else None


# --------------------------------------------------------------------------
# Scorecards
# --------------------------------------------------------------------------

#: `assets_payload.HARDENING_CHECKS` name -> the `SecurityCheck.key` PREFIX
#: `_host_checks()` builds the same fact under (that function appends
#: `:{h.name}`). Two of the seven hardening checks -- "Auto-updates" and "No
#: reboot pending" -- score the host scorecard but have no row of their own
#: in `_host_checks()` today, so they are left out here on purpose rather
#: than pointing a deduction at a check key that does not exist.
_HOST_DED_KEY_PREFIX = {
    "Key-only SSH": "ssh", "Firewall": "firewall", "fail2ban": "bruteforce",
    "No security updates pending": "patching", "No malware signs": "malware",
}


def _host_scorecard(h: m.Host) -> Scorecard:
    last = h.last or {}
    hg = assets_payload.hardening(last)
    weight = 100.0 / len(hg.checks) if hg.checks else 100.0
    name_to_key = {name: key for name, key in assets_payload.HARDENING_CHECKS}
    checks = [(c.name, c.passed, c.detail, weight,
              f"{_HOST_DED_KEY_PREFIX[c.name]}:{h.name}" if c.name in _HOST_DED_KEY_PREFIX else "")
              for c in hg.checks]
    _, deds = _weighted(checks)

    # Highest-value auto-fixable failing check becomes the scorecard's fix.
    fix = ""
    best_points = -1
    for c in hg.checks:
        if c.passed:
            continue
        key = name_to_key.get(c.name, "")
        action = HARDENING_FIX.get(key, "")
        if _autofix(action) == "auto" and weight > best_points:
            fix, best_points = action, weight
    return Scorecard(target=h.name, kind="host", grade=hg.grade, score=hg.score,
                      deductions=deds, fix=fix)


SITE_HEADER_CHECKS = [
    ("HSTS", "hsts"), ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "frame"),
    ("CSP", "csp"), ("HTTPS redirect", "https_redirect"),
]


def _site_scorecard(s: m.Site) -> Scorecard:
    last = s.last or {}
    sec = last.get("sec") or {}
    weight = 100.0 / 6
    # All five header deductions point at the same "headers:{s.name}" row --
    # `_site_checks()` reports the headers as one pass/fail fact, not one row
    # per header, so that is the only key any of them can resolve against.
    checks = [(label, bool(sec.get(key)), f"{key}: {bool(sec.get(key))}", weight, f"headers:{s.name}")
              for label, key in SITE_HEADER_CHECKS]
    old_tls = bool(sec.get("old_tls"))
    checks.append(("Modern TLS", not old_tls, f"old_tls: {old_tls}", weight, f"tls:{s.name}"))
    score, deds = _weighted(checks)
    return Scorecard(target=s.name, kind="site", grade=_grade(score), score=score,
                      deductions=deds, fix="")


def _evil_twin(wifi: dict, rogue: dict) -> tuple[bool, str]:
    """Is another access point impersonating this network, and the evidence.

    NOT "the SSID is on a second channel": every dual-band router puts one
    name on 2.4GHz and 5GHz, every mesh node repeats it again, and routers
    move channels by themselves. That test flagged the owner's own Verizon
    router and his two extenders, over and over, and would do the same on any
    meshed house -- an alarm that is always on is an alarm nobody reads.

    A rogue AP has to offer weaker encryption than the real network, or none,
    or a client will not hand it anything. So the signal is: same name,
    DIFFERENT security from the one we are on. A sensor too old to report
    `twin_security` gives no opinion rather than the old false positive.
    """
    mine = str(wifi.get("security") or "").strip().lower()
    seen = rogue.get("twin_security")
    if not mine or not seen:
        return False, ""
    odd = [x for x in seen if x and str(x).strip().lower() != mine]
    if not odd:
        return False, ""
    return True, f"{', '.join(odd)} (yours is {wifi.get('security')})"


def wifi_verdict(wifi: dict) -> str:
    """"good" | "weak" | "unknown" for one network's encryption.

    The third value is the point. `security` is empty whenever the sensor is
    on Ethernet, or `system_profiler` timed out, or macOS withheld the field
    -- and an empty string used to be folded in with "open", so Vault told
    an owner running WPA2 that their Wi-Fi was unencrypted and to go change
    it. Not knowing is not a finding.
    """
    sec_s = str(wifi.get("security") or "").strip().lower()
    if not sec_s or sec_s in ("?", "unknown"):
        return "unknown"
    if sec_s in ("open", "none") or "wep" in sec_s:
        return "weak"
    return "good"


def _channel_int(channel) -> int | None:
    return _int_from(channel)


def _home_scorecard(raw: dict) -> Scorecard:
    home = raw.get("home") or {}
    rogue = raw.get("rogue") or {}
    wifi = home.get("wifi") or {}
    exposure = home.get("exposure") or {}
    security = str(wifi.get("security") or "")
    wifi_state = wifi_verdict(wifi)
    weak_wifi = wifi_state == "weak"

    home_channel = _channel_int(wifi.get("channel"))
    twins = [c for c in (rogue.get("twin_channels") or []) if c != home_channel]
    evil_twin, twin_why = _evil_twin(wifi, rogue)

    expected_gw = home.get("gateway") or ""
    rogue_dhcp = bool(rogue.get("dhcp_server")) and rogue.get("dhcp_server") != expected_gw
    gw_drift = bool(rogue.get("default_gateway")) and rogue.get("default_gateway") != expected_gw

    open_ports = exposure.get("open_ports") or []
    unverified = home.get("unverified") or []

    weight = 100.0 / 6
    checks = [
        ("Wi-Fi encryption", not weak_wifi,
         f"security: {security}" if wifi_state != "unknown"
         else "encryption could not be read (Ethernet, or macOS withheld it)",
         weight, "wifi:home"),
        ("No evil twin", not evil_twin,
         twin_why or (f"same encryption on channel(s) {twins} — your own band or extender" if twins
                      else "no other access point is using this name"),
         weight, "wifi:evil_twin"),
        ("DHCP integrity", not rogue_dhcp, f"dhcp_server: {rogue.get('dhcp_server') or ''}", weight,
         "wifi:rogue_dhcp"),
        ("Gateway integrity", not gw_drift, f"default_gateway: {rogue.get('default_gateway') or ''}", weight,
         "wifi:gateway_drift"),
        ("No exposed ports", not open_ports, f"open_ports: {open_ports}", weight, "exposure:home"),
        ("All devices verified", not unverified, f"unverified: {len(unverified)}", weight, "wifi:unverified"),
    ]
    score, deds = _weighted(checks)
    return Scorecard(target="Home network", kind="lan", grade=_grade(score), score=score,
                      deductions=deds, fix="")


def _still_checked(rows: list, cutoff) -> list:
    """Drop the rows the sensor has stopped reporting (see payloads.staleness).

    Every check this module emits is stamped `last_verified=ts` with the
    CURRENT scan's timestamp. For a row the sensor stopped reporting that is
    not merely out of date, it is false: the check says it was verified on the
    latest sweep when nothing looked at that asset at all. `safehome.example.com`
    showed a failing CSP check, stamped as just-verified, against a header
    that had already been fixed -- the site was not in the sensor's config, so
    nothing was re-checking it and the frozen row kept asserting the old
    result as a current one.

    Excluded rather than marked. A security checklist is the set of things
    that were actually verified; an entry that asserts neither pass nor fail
    is noise in the one place a reader is counting failures. The asset itself
    does not disappear -- it is still on Assets, where the card dims and says
    when it was really last checked.
    """
    return [r for r in rows if not staleness.is_stale(getattr(r, "last_seen", None), cutoff)]


def scorecards(db: DbSession, tenant, raw: dict) -> list[Scorecard]:
    cutoff = staleness.stale_cutoff(db, tenant.id)
    hosts = _still_checked(db.query(m.Host).filter_by(tenant_id=tenant.id).order_by(m.Host.name).all(), cutoff)
    sites = _still_checked(db.query(m.Site).filter_by(tenant_id=tenant.id).order_by(m.Site.name).all(), cutoff)
    out = [_host_scorecard(h) for h in hosts]
    out.extend(_site_scorecard(s) for s in sites)
    out.append(_home_scorecard(raw))
    return out


def posture_coverage(db: DbSession, tenant) -> PostureCoverage:
    """How much of the estate the posture scores above actually speak for.

    `_still_checked` drops a target the sensor has stopped reporting, and it
    is right to: a frozen row stamped `last_verified = now` asserts a current
    result for an asset nothing looked at. But dropping it *silently* is its
    own dishonesty -- the client counts the cards on screen and believes that
    is everything, when a machine Vault has lost sight of is precisely the
    one worth asking about.

    So: still excluded from the scores, never excluded from the count. The
    page can now say "9 of 11 scored, 2 not checked recently" and name them.
    """
    cutoff = staleness.stale_cutoff(db, tenant.id)
    hosts = db.query(m.Host).filter_by(tenant_id=tenant.id).order_by(m.Host.name).all()
    sites = db.query(m.Site).filter_by(tenant_id=tenant.id).order_by(m.Site.name).all()
    # Qualified by kind, never bare. A server and a website may carry the
    # same name -- "Roots" is both here -- and an unqualified list read
    # "Roots, Roots, Roots Demo, Roots Demo", which tells the owner nothing
    # about which of the two to go and look at.
    missing = [f"{r.name} (server)" for r in hosts
               if staleness.is_stale(getattr(r, "last_seen", None), cutoff)]
    missing += [f"{r.name.strip()} (website)" for r in sites
                if staleness.is_stale(getattr(r, "last_seen", None), cutoff)]
    # +1 for the home network card, which is built from the current scan and
    # so is always as fresh as the sweep itself.
    total = len(hosts) + len(sites) + 1
    return PostureCoverage(scored=total - len(missing), total=total, unscored=sorted(missing))


# --------------------------------------------------------------------------
# Wi-Fi guard
# --------------------------------------------------------------------------

def wifi_guard(raw: dict, ts: str, decisions: dict | None = None, network_id: str = "") -> WifiGuard:
    home = raw.get("home") or {}
    rogue = raw.get("rogue") or {}
    wifi = home.get("wifi") or {}
    security = str(wifi.get("security") or "")
    wifi_state = wifi_verdict(wifi)
    weak_wifi = wifi_state == "weak"
    channel = _channel_int(wifi.get("channel"))
    signal = _int_from(wifi.get("signal"))

    twins = [c for c in (rogue.get("twin_channels") or []) if c != channel]
    evil_twin, twin_why = _evil_twin(wifi, rogue)

    expected_gw = home.get("gateway") or ""
    dhcp_server = rogue.get("dhcp_server") or ""
    rogue_dhcp = bool(dhcp_server) and dhcp_server != expected_gw
    gw_drift = bool(rogue.get("default_gateway")) and rogue.get("default_gateway") != expected_gw
    unverified = home.get("unverified") or []

    if evil_twin or rogue_dhcp or gw_drift:
        state, verdict = "bad", "Rogue network activity detected on your Wi-Fi"
        if evil_twin:
            verdict = f"Evil twin: another access point uses your Wi-Fi name with {twin_why}"
        elif rogue_dhcp:
            verdict = f"Rogue DHCP server {dhcp_server} (expected {expected_gw})"
        elif gw_drift:
            verdict = f"Default gateway drifted to {rogue.get('default_gateway')} (expected {expected_gw})"
    elif unverified or weak_wifi:
        state, verdict = "warn", (f"{len(unverified)} unverified device(s) on the network" if unverified
                                   else f"Weak Wi-Fi encryption ({security})")
    elif wifi_state == "unknown":
        # Neither clean nor a finding: say which one it is.
        state, verdict = "warn", "Wi-Fi encryption could not be read from this sensor"
    else:
        state, verdict = "good", f"Wi-Fi looks clean: {security} on channel {channel}"

    rows = [
        # `twins` (already filtered to exclude this network's own channel
        # above), never the raw `twin_channels` list: that list can contain
        # the home channel itself, and showing it back as if it were part of
        # the finding would read as "both of these channels are hostile"
        # when one of them is just this network's own AP.
        SecurityCheck(key="wifi:evil_twin", area="Wi-Fi", title="No evil twin", passed=not evil_twin,
                       last_verified=ts, evidence=(twin_why or "no access point is using this name with different encryption"),
                       autofix="none", target="Home network"),
        SecurityCheck(key="wifi:rogue_dhcp", area="Wi-Fi", title="DHCP integrity", passed=not rogue_dhcp,
                       last_verified=ts, evidence=f"dhcp_server: {dhcp_server} vs expected {expected_gw}",
                       autofix="none", target="Home network"),
        SecurityCheck(key="wifi:gateway_drift", area="Wi-Fi", title="Gateway integrity", passed=not gw_drift,
                       last_verified=ts, evidence=f"default_gateway: {rogue.get('default_gateway') or ''}",
                       autofix="none", target="Home network"),
        SecurityCheck(key="wifi:unverified", area="Wi-Fi", title="All devices verified", passed=not unverified,
                       last_verified=ts, evidence=f"unverified: {len(unverified)}",
                       autofix="none", target="Home network"),
    ]
    if decisions is not None:
        _attach_decisions(rows, decisions, network_id)

    return WifiGuard(
        verdict=verdict, state=state, ssid=str(wifi.get("ssid") or ""), security=security,
        channel=channel, phy=str(wifi.get("phy") or ""), signal=signal,
        evil_twin=evil_twin, twin_channels=list(rogue.get("twin_channels") or []),
        rogue_dhcp=rogue_dhcp, dhcp_server=dhcp_server, expected_gateway=expected_gw,
        default_gateway=str(rogue.get("default_gateway") or ""),
        gateway_mac=str((raw.get("lan") or {}).get("gw_mac") or ""),
        unknown_devices=len(unverified), rows=rows,
    )


# --------------------------------------------------------------------------
# Checklist with proof
# --------------------------------------------------------------------------

def _host_checks(h: m.Host, ts: str) -> list[SecurityCheck]:
    last = h.last or {}
    sec = last.get("sec") or {}
    drift = last.get("drift") or {}
    security_updates = sec.get("security_updates") or []
    miners = sec.get("miners") or []
    ssh_ok = not sec.get("ssh_password")
    fw_ok = sec.get("ufw") == "active"
    f2b_ok = last.get("fail2ban") == "active"
    patch_ok = not security_updates
    malware_ok = not miners and not sec.get("preload")

    return [
        SecurityCheck(key=f"ssh:{h.name}", area="SSH", title="Key-only SSH", passed=ssh_ok,
                       last_verified=ts,
                       evidence=f"sshd -T: passwordauthentication {'no' if ssh_ok else 'yes'}",
                       autofix=_autofix(HARDENING_FIX["ssh_password"]), target=h.name),
        SecurityCheck(key=f"firewall:{h.name}", area="Firewall", title="Firewall active", passed=fw_ok,
                       last_verified=ts, evidence=f"ufw status: {sec.get('ufw') or 'not detected'}",
                       autofix=_autofix(HARDENING_FIX["ufw"]), target=h.name),
        SecurityCheck(key=f"bruteforce:{h.name}", area="Brute force", title="fail2ban active", passed=f2b_ok,
                       last_verified=ts,
                       evidence=f"fail2ban: {last.get('fail2ban') or 'not running'}, banned {last.get('banned') or 0}",
                       autofix=_autofix(HARDENING_FIX["fail2ban"]), target=h.name),
        SecurityCheck(key=f"patching:{h.name}", area="Patching", title="No security updates pending",
                       passed=patch_ok, last_verified=ts,
                       evidence=f"debsecan: {last.get('cve_fixable') or 0} fixable, "
                                f"{len(security_updates)} security updates pending",
                       autofix=_autofix(HARDENING_FIX["security_updates"]), target=h.name),
        SecurityCheck(key=f"malware:{h.name}", area="Malware", title="No malware signs", passed=malware_ok,
                       last_verified=ts,
                       evidence=f"miners: {len(miners)}, preload: {bool(sec.get('preload'))}",
                       autofix=_autofix(HARDENING_FIX["malware"]), target=h.name),
        SecurityCheck(key=f"drift:{h.name}", area="Config drift", title="Config drift baseline", passed=None,
                       last_verified=ts,
                       evidence=", ".join(f"{k}={v}" for k, v in drift.items()) or "no drift facts",
                       autofix="none", target=h.name),
    ]


def _site_checks(s: m.Site, ts: str) -> list[SecurityCheck]:
    last = s.last or {}
    sec = last.get("sec") or {}
    hsts = bool(sec.get("hsts"))
    old_tls = bool(sec.get("old_tls"))
    return [
        SecurityCheck(key=f"headers:{s.name}", area="Web headers", title="Security headers present",
                       passed=hsts and bool(sec.get("nosniff")) and bool(sec.get("frame")) and bool(sec.get("csp")),
                       last_verified=ts, evidence=f"headers: strict-transport-security {'present' if hsts else 'missing'}",
                       autofix="none", target=s.name),
        SecurityCheck(key=f"tls:{s.name}", area="TLS", title="Modern TLS only", passed=not old_tls,
                       last_verified=ts,
                       evidence=f"tls: {'legacy protocols enabled' if old_tls else 'modern only'}",
                       autofix="none", target=s.name),
    ]


def _admin_panel_checks(admin_probe: dict, ts: str) -> list[SecurityCheck]:
    out = []
    for name, probe in admin_probe.items():
        hits = probe.get("hits") or {}
        catch_all = bool(probe.get("catch_all"))
        # Nexus parity (checks.py admin_probe/alert): `catch_all` is only the
        # baseline flag the probe uses to tell a single-page app's 200-for-
        # everything from a real hit -- it is never itself a failure. Only
        # `hits` (paths that matched a real signature) fail the check.
        passed = not hits
        out.append(SecurityCheck(
            key=f"admin:{name}", area="Admin panels", title="No exposed admin panel", passed=passed,
            last_verified=probe.get("ts") or ts,
            evidence=f"catch_all: {catch_all}, hits: {len(hits)}", autofix="none", target=name,
        ))
    return out


def _email_checks(email_sec: dict, ts: str) -> list[SecurityCheck]:
    """One checklist row per domain — except the advisory ones.

    A domain the customer does not control is measured and reported (see
    `email_auth_rows`), but it gets no checklist row: a row is a thing that
    can fail, deduct from the score and ask to be fixed, and asking
    somebody to fix DNS only its owner can change is the nagging Miro
    asked me to stop. Measured, shown, never chased.
    """
    out = []
    for domain, e in email_sec.items():
        if e.get("advisory"):
            continue
        spf, dmarc_policy = e.get("spf"), e.get("dmarc_policy")
        passed = bool(spf) and dmarc_policy in ("quarantine", "reject")
        out.append(SecurityCheck(
            key=f"email:{domain}", area="Email", title="SPF + enforced DMARC", passed=passed,
            last_verified=e.get("ts") or ts,
            evidence=f"spf: {'present' if spf else 'missing'}, dmarc: {dmarc_policy or 'missing'}",
            autofix="none", target=domain,
        ))
    return out


# `guardian_sensor.cameras.CAMERA_PORTS`, mirrored (the hub cannot import the
# sensor package). Used to tell an internet-facing CAMERA from any other
# internet-facing port -- the difference between "a port is forwarded" and
# "strangers can watch your driveway".
CAMERA_PORTS = {554: "rtsp", 8554: "rtsp-alt", 37777: "dahua", 34567: "dvr", 8000: "camera-http"}


def camera_facts(db: DbSession, tenant, raw: dict) -> dict:
    """The camera census across every network, plus which of the ports the
    outside world answered on are camera ports.

    Counted from `Device` rows through `assets.kind_of` (never from the raw
    scan's own census), so the number here always matches the Cameras group on
    the Assets page for the same tenant.
    """
    rows = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
    cams = [d for d in rows if assets_payload.kind_of(d) == "camera"]
    open_ports = ((raw.get("home") or {}).get("exposure") or {}).get("open_ports") or []
    exposed = sorted(p for p in open_ports if p in CAMERA_PORTS)
    # Through `effective_presence`, not off the stored column.
    #
    # `Device.presence` is the last thing a sweep WROTE, and it is never
    # rewritten to say "I have stopped seeing this". A camera seen once and
    # then unplugged keeps `presence == "ONLINE"` for as long as the row lives,
    # so counting the column reports it as answering forever.
    #
    # That matters more here than anywhere else in the product. Cameras are the
    # asset class whose entire value is "is it actually up", `_camera_checks`
    # turns these buckets into a security check that says all of them are
    # answering, and this function's own docstring promises the number matches
    # the Cameras group on the Assets page -- which reports
    # `effective_presence(d)`. Reading the column broke that promise silently:
    # the two screens disagreed about the same camera, and the reassuring one
    # was the wrong one.
    #
    # A stale ONLINE resolves to UNKNOWN, so it lands in `unknown` below, which
    # is exactly the bucket the comment there describes -- an observation
    # nobody has made recently, claimed by neither the good count nor the alarm.
    state = {id(d): effective_presence(d) for d in cams}
    quiet = [d for d in cams if state[id(d)] in ("OFFLINE", "ARCHIVED")]
    online = sum(1 for d in cams if state[id(d)] == "ONLINE")
    # A camera in neither bucket has a presence Vault never established --
    # a row that exists because a recorder listed the channel, but that no
    # sweep has yet seen answer or fail. It is reported as its own number
    # rather than folded into either: counted with `online` it would claim an
    # observation nobody made, and counted with `quiet` it would raise an
    # alarm about a camera that may be perfectly fine. `payloads/cameras`
    # renders it as the census's `unobserved`, alongside the channels a
    # recorder promised and has never listed.
    return {"total": len(cams), "online": online,
            "quiet": quiet, "unknown": len(cams) - online - len(quiet),
            "exposed_ports": exposed,
            "wan_ip": ((raw.get("home") or {}).get("exposure") or {}).get("wan_ip") or raw.get("wan_ip") or ""}


def _camera_checks(facts: dict, ts: str) -> list[SecurityCheck]:
    """Two rows the owner of a dozen cameras actually wants: none of them is
    reachable from the internet, and all of them are answering."""
    exposed = facts["exposed_ports"]
    quiet = facts["quiet"]
    return [
        SecurityCheck(
            key="cameras:internet", area="Cameras",
            title="No camera reachable from the internet", passed=not exposed,
            last_verified=ts,
            evidence=(f"camera port(s) {exposed} answer on {facts['wan_ip'] or 'your public address'}"
                       if exposed else f"{facts['total']} camera(s), none answering from outside"),
            autofix="none", target="Cameras",
        ),
        SecurityCheck(
            key="cameras:answering", area="Cameras",
            title="Every known camera is answering", passed=not quiet, last_verified=ts,
            evidence=(", ".join((d.label or d.name or d.mac) for d in quiet[:5])
                       if quiet else f"{facts['online']}/{facts['total']} answering"),
            autofix="none", target="Cameras",
        ),
    ]


def _exposure_check(home: dict, ts: str) -> SecurityCheck:
    exposure = home.get("exposure") or {}
    open_ports = exposure.get("open_ports") or []
    return SecurityCheck(
        key="exposure:home", area="Exposure", title="No ports open from the internet",
        passed=not open_ports, last_verified=exposure.get("ts") or ts,
        evidence=f"open ports from internet: {open_ports}", autofix="none", target="Home network",
    )


def checks(db: DbSession, tenant, raw: dict, ts: str, decisions: dict | None = None) -> list[SecurityCheck]:
    cutoff = staleness.stale_cutoff(db, tenant.id)
    hosts = _still_checked(db.query(m.Host).filter_by(tenant_id=tenant.id).order_by(m.Host.name).all(), cutoff)
    sites = _still_checked(db.query(m.Site).filter_by(tenant_id=tenant.id).order_by(m.Site.name).all(), cutoff)
    home = raw.get("home") or {}
    wifi = home.get("wifi") or {}

    out: list[SecurityCheck] = []
    for h in hosts:
        out.extend(_host_checks(h, ts))
    for s in sites:
        out.extend(_site_checks(s, ts))
    out.extend(_admin_panel_checks(raw.get("admin_probe") or {}, ts))
    out.extend(_email_checks(raw.get("email_sec") or {}, ts))
    out.append(_exposure_check(home, ts))
    out.extend(_camera_checks(camera_facts(db, tenant, raw), ts))
    # Tri-state on purpose. `passed=False` puts a red cross on the row and
    # offers "set your Wi-Fi to WPA2 or WPA3", which is wrong and alarming
    # advice for an owner already on WPA2 whose sensor simply could not read
    # the field. None renders as "can't tell", which is the truth.
    _wstate = wifi_verdict(wifi)
    out.append(SecurityCheck(
        key="wifi:home", area="Wi-Fi", title="Wi-Fi encryption",
        passed=None if _wstate == "unknown" else (_wstate == "good"),
        last_verified=ts,
        evidence=(f"wifi: {wifi.get('ssid') or ''} ({wifi.get('security')})" if _wstate != "unknown"
                  else "encryption could not be read — the sensor is on Ethernet, "
                       "or macOS withheld the Wi-Fi details"),
        autofix="none", target="Home network",
    ))
    if decisions is not None:
        # None of these checks are network-scoped (host/site names and "Home
        # network" are already unique on their own), so every lookup here
        # uses the network-agnostic bucket, `network_id=""`.
        _attach_decisions(out, decisions, "")
    return out


# --------------------------------------------------------------------------
# Attackers
# --------------------------------------------------------------------------

def _fails_from_msg(msg: str) -> int:
    match = _FAILS_RE.search(msg or "")
    return int(match.group(1)) if match else 0


def _days_from(first_seen: str, last_seen: str) -> int:
    from ..scans import parse_ts
    a, b = parse_ts(first_seen), parse_ts(last_seen)
    if a is None or b is None:
        return 1
    return max(1, (b - a).days + 1)


def attackers(db: DbSession, tenant, raw: dict, net=None) -> list[Attacker]:
    """`net` narrows to attackers whose alert landed on that network. The
    `sec:attacker:*` alerts a HOST reports are not LAN findings and generally
    carry no network at all, so a filtered read keeps network-agnostic rows
    (`network_id IS NULL`) rather than coming back empty."""
    tenant_id = tenant.id
    entries: dict[tuple[str, str], dict] = {}
    netrefs = networks.refs_by_id(db, tenant_id)

    aq = db.query(m.Alert).filter(m.Alert.tenant_id == tenant_id,
                                  m.Alert.key.like("sec:attacker:%"))
    if net is not None:
        aq = aq.filter((m.Alert.network_id == net.id) | (m.Alert.network_id.is_(None)))
    for a in aq.all():
        match = _ATTACKER_KEY_RE.match(a.key)
        if not match:
            continue
        host, ip = match.group(1), match.group(2)
        entries[(host, ip)] = {
            "ip": ip, "host": host, "fails": _fails_from_msg(a.msg),
            "days": _days_from(a.first_seen, a.last_seen),
            "first_seen": a.first_seen, "last_seen": a.last_seen, "alert_key": a.key,
            "network_id": a.network_id,
        }

    for h in raw.get("ssh") or []:
        host = h.get("name") or ""
        for ip, fails in ((h.get("sec") or {}).get("top_attackers") or {}).items():
            key = (host, ip)
            if key in entries:
                entries[key]["fails"] = max(entries[key]["fails"], int(fails or 0))
            else:
                entries[key] = {
                    "ip": ip, "host": host, "fails": int(fails or 0), "days": 1,
                    "first_seen": "", "last_seen": "", "alert_key": f"sec:attacker:{host}:{ip}",
                    "network_id": None,
                }

    ordered = sorted(entries.values(), key=lambda e: (-e["fails"], e["ip"]))

    out: list[Attacker] = []
    uncached_used = 0
    deadline = time.monotonic() + GEO_BUDGET_S
    for e in ordered:
        fix = (
            db.query(m.Fix)
            .filter(m.Fix.tenant_id == tenant_id, m.Fix.arg == e["ip"])
            .order_by(m.Fix.ts.desc()).first()
        )
        # Every attacker gets looked up -- a cached IP costs nothing and
        # never counts against the budget below. Only a cache *miss* (a
        # real network call) is capped, both by count (MAX_GEO_LOOKUPS)
        # and by wall-clock time (GEO_BUDGET_S) so one slow/unreachable
        # provider can't stall the whole route.
        if geoip.is_cached(db, e["ip"]):
            geo = geoip.lookup(db, e["ip"])
        elif uncached_used < MAX_GEO_LOOKUPS and time.monotonic() < deadline:
            geo = geoip.lookup(db, e["ip"])
            uncached_used += 1
        else:
            geo = None
        out.append(Attacker(
            ip=e["ip"], host=e["host"], fails=e["fails"], days=e["days"],
            first_seen=e["first_seen"], last_seen=e["last_seen"], geo=geo,
            action=fix.action if fix else "", action_ts=fix.ts if fix else None,
            alert_key=e["alert_key"], network=netrefs.get(e.get("network_id")),
        ))
    return out


# --------------------------------------------------------------------------
# Exposure
# --------------------------------------------------------------------------

def _wan_pin_tiles(raw: dict, cfg: dict | None = None) -> list[ExposureTile]:
    """One tile per network that pins an expected public address.

    `expected_wan_ip` is a sensor-owned key inside a `config["networks"]`
    entry, so it needs no column and survives a hub-side network write
    (`networks.to_config_entries`). Only the network the sensor's own default
    route uses has a measured egress address, so a network whose pin cannot be
    compared says "not measured here" rather than showing a false match.
    """
    entries = [e for e in ((cfg or {}).get("networks") or []) if isinstance(e, dict) and e.get("expected_wan_ip")]
    if not entries:
        return []
    measured = ((raw.get("home") or {}).get("exposure") or {}).get("wan_ip") or raw.get("wan_ip") or ""
    gateway = (raw.get("rogue") or {}).get("default_gateway") or ""
    tiles = []
    for e in entries:
        pinned = str(e.get("expected_wan_ip"))
        here = not gateway or not e.get("expected_gateway") or gateway == e.get("expected_gateway")
        if not measured or not here:
            state, value, detail = "warn", pinned, "expected address; not measured from this sensor"
        elif measured == pinned:
            state, value, detail = "ok", pinned, "this network leaves through its expected address"
        else:
            state, value, detail = "crit", measured, f"expected {pinned} — this network is leaving another way"
        tiles.append(ExposureTile(label=f"{e.get('name') or 'Network'} public address", value=value,
                                   state=state, detail=detail))
    return tiles


def exposure(raw: dict, cfg: dict | None = None, cameras: dict | None = None) -> list[ExposureTile]:
    home = raw.get("home") or {}
    exposure_facts = home.get("exposure") or {}
    open_ports = exposure_facts.get("open_ports") or []
    wan_ip = exposure_facts.get("wan_ip") or raw.get("wan_ip") or ""
    gateway_ports = home.get("gateway_ports") or []
    admin_hits = sum(len(p.get("hits") or {}) for p in (raw.get("admin_probe") or {}).values())

    # Nexus parity (checks.py alert loop): 22/80/443 are the expected ports
    # for anything reachable from the internet; a tenant can widen that via
    # config.home.site_allowed_ports.
    allowed_ports = {22, 80, 443} | set(((cfg or {}).get("home") or {}).get("site_allowed_ports") or [])

    email_sec = raw.get("email_sec") or {}
    # The tile is a verdict on THIS customer's posture, so a domain they do
    # not control cannot tip it either way. Advisory domains are reported in
    # the Email spoofing table and counted nowhere.
    email_own = {d: e for d, e in email_sec.items() if not e.get("advisory")}
    dmarc_ok = any((e.get("dmarc_policy") in ("quarantine", "reject")) for e in email_own.values())
    spf_ok = any(e.get("spf") for e in email_own.values())

    tiles = [
        ExposureTile(label="WAN IP", value=wan_ip or "unknown", state="ok" if wan_ip else "warn",
                      detail=f"probe host: {exposure_facts.get('probe_host') or ''}"),
        ExposureTile(label="Open ports from internet", value=str(len(open_ports)),
                      state="ok" if not open_ports else "crit", detail=", ".join(str(p) for p in open_ports)),
        ExposureTile(label="Gateway ports", value=str(len(gateway_ports)),
                      state="ok" if len(gateway_ports) <= 3 else "warn",
                      detail=", ".join(str(p) for p in gateway_ports)),
        ExposureTile(label="Admin panel probe hits", value=str(admin_hits),
                      state="ok" if not admin_hits else "crit",
                      detail=f"{len(raw.get('admin_probe') or {})} sites probed"),
    ]
    for name, site in (raw.get("external_sites") or {}).items():
        ports = site.get("open_ports") or []
        risky = [p for p in ports if p not in allowed_ports]
        tiles.append(ExposureTile(label=f"{name} open ports", value=str(len(ports)),
                                   state="ok" if not risky else "warn", detail=", ".join(str(p) for p in ports)))
    if cameras is not None:
        cam_exposed = cameras["exposed_ports"]
        tiles.append(ExposureTile(
            label="Cameras", value=f"{cameras['online']}/{cameras['total']} answering",
            state="crit" if cam_exposed else "warn" if cameras["quiet"] else "ok",
            detail=(f"camera port(s) {cam_exposed} answer from the internet"
                    if cam_exposed else
                    ", ".join((d.label or d.name or d.mac) + " not answering" for d in cameras["quiet"][:3])
                    or "every known camera is answering, none reachable from outside")))
    tiles.extend(_wan_pin_tiles(raw, cfg))
    tiles.append(ExposureTile(label="Email (SPF/DMARC)", value="enforced" if dmarc_ok and spf_ok else "gaps",
                               state="ok" if dmarc_ok and spf_ok else "warn",
                               detail=f"{sum(1 for e in email_own.values() if e.get('spf'))}/{len(email_own)} domains with SPF"))
    return tiles


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def _secondary_guard(db: DbSession, tenant, row: "m.Network", ts: str, decisions: dict | None = None) -> WifiGuard:
    """The Wi-Fi guard for a network the pushing sensor did NOT report on.

    The sensor emits one `home`/`rogue` block per push (the network its own
    interface sits on), so evil-twin / rogue-DHCP facts simply do not exist for
    a second network yet -- claiming they passed would be a false clean bill of
    health. This guard therefore reports only what the hub genuinely knows: the
    network's declared SSID and expected gateway, and its own unverified device
    count. Every fact-free row is `passed=None` ("not verified"), and a network
    with no sensor of its own says so in `verdict`.
    """
    unverified = sum(1 for d in db.query(m.Device)
                      .filter_by(tenant_id=tenant.id, network_id=row.id).all()
                      if not d.trusted and not (d.label or ""))
    no_sensor = row.status in ("no-sensor", "pending") or not row.sensor_id
    if row.status == "unreachable":
        state, verdict = "bad", f"{row.name} is unreachable from its sensor"
    elif no_sensor:
        state, verdict = "warn", f"No sensor watches {row.name} yet. Enrol one on that network"
    elif unverified:
        state, verdict = "warn", f"{unverified} unverified device(s) on {row.name}"
    else:
        state, verdict = "good", f"{row.name} looks clean"
    rows = [
        SecurityCheck(key="wifi:evil_twin", area="Wi-Fi", title="No evil twin", passed=None,
                       last_verified=ts, evidence="not verified on this network", autofix="none",
                       target=row.name),
        SecurityCheck(key="wifi:rogue_dhcp", area="Wi-Fi", title="DHCP integrity", passed=None,
                       last_verified=ts, evidence="not verified on this network", autofix="none",
                       target=row.name),
        SecurityCheck(key="wifi:gateway_drift", area="Wi-Fi", title="Gateway integrity", passed=None,
                       last_verified=ts, evidence=f"expected {row.expected_gateway or 'unset'}",
                       autofix="none", target=row.name),
        SecurityCheck(key="wifi:unverified", area="Wi-Fi", title="All devices verified",
                       passed=not unverified, last_verified=ts,
                       evidence=f"unverified: {unverified}", autofix="none", target=row.name),
    ]
    if decisions is not None:
        _attach_decisions(rows, decisions, row.id)
    return WifiGuard(verdict=verdict, state=state, ssid=row.ssid or "",
                      expected_gateway=row.expected_gateway or "",
                      unknown_devices=unverified, rows=rows)


def extra_checks(raw: dict) -> list[ExtraCheck]:
    """`Scan.raw["checks"]` -> rows, failures first then by name.

    The sensor emits a list of dicts (`checks.extra_checks`); a very old
    payload could carry the `{name: ok}` map the history line uses, so both
    shapes are accepted rather than 500-ing the whole Security tab.
    """
    rows: list[ExtraCheck] = []
    src = raw.get("checks")
    if isinstance(src, dict):
        src = [{"name": k, "ok": v} for k, v in src.items()]
    for c in src or []:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        rows.append(ExtraCheck(
            name=str(c.get("name")),
            type=str(c.get("type") or ""),
            target=str(c.get("target") or ""),
            port=_int_from(c.get("port")),
            ok=c["ok"] if isinstance(c.get("ok"), bool) else None,
            latency_ms=_int_from(c.get("latency_ms")),
            detail=str(c.get("detail") or ""),
        ))
    rows.sort(key=lambda r: (r.ok is not False, (r.name or "").lower()))
    return rows


def heartbeats(db: DbSession, tenant, raw: dict, now: dt.datetime | None = None) -> list[HeartbeatRow]:
    """The `Heartbeat` rows, with `late` computed here.

    The row is the authority for `last_ping` (the public `/hb/<name>/<token>`
    endpoint writes it, and it keeps ticking while a sensor is down, which is
    exactly when a dead-man switch matters). `Scan.raw["heartbeats"]`
    contributes only the human note. `late` uses the predecessor's rule: no
    ping ever, or older than 1.5x the expected interval.
    """
    now = now or _now()
    notes = raw.get("heartbeats") or {}
    out: list[HeartbeatRow] = []
    for hb in db.query(m.Heartbeat).filter_by(tenant_id=tenant.id).all():
        note = notes.get(hb.name) if isinstance(notes, dict) else None
        every = int(hb.expect_minutes or 0)
        age = None
        if hb.last_ping is not None:
            stamp = hb.last_ping if hb.last_ping.tzinfo else hb.last_ping.replace(tzinfo=dt.timezone.utc)
            age = int((now - stamp).total_seconds() / 60)
        out.append(HeartbeatRow(
            name=hb.name, every_min=every,
            last_ping=hb.last_ping.isoformat(timespec="seconds") if hb.last_ping else "",
            age_min=age,
            late=age is None or (every > 0 and age > every * 1.5),
            note=str((note or {}).get("note") or "") if isinstance(note, dict) else "",
        ))
    out.sort(key=lambda r: (not r.late, (r.name or "").lower()))
    return out


def domains(raw: dict) -> list[DomainRow]:
    """`Scan.raw["domains"]` (`{domain: {expires, days_left, error}}`) ->
    rows, soonest expiry first, unknown/failed last."""
    src = raw.get("domains")
    if not isinstance(src, dict):
        return []
    out: list[DomainRow] = []
    for dom, d in src.items():
        if not isinstance(d, dict):
            continue
        out.append(DomainRow(
            domain=str(dom), expires=str(d.get("expires") or ""),
            days_left=_int_from(d.get("days_left")), error=str(d.get("error") or ""),
        ))
    out.sort(key=lambda r: (r.days_left is None, r.days_left if r.days_left is not None else 0,
                            (r.domain or "").lower()))
    return out


# --------------------------------------------------------------------------
# Task 77 (F8): email anti-spoofing, external ports per host, the fix log
# --------------------------------------------------------------------------

#: Ports a public site host is expected to answer on. A tenant can widen this
#: through `config["home"]["site_allowed_ports"]`, the same key the sensor's own
#: alert rule reads, so the two never disagree about what counts as unexpected.
DEFAULT_SITE_PORTS = (22, 80, 443)

#: Rows the fix log carries. The reference shows the most recent runs; the whole
#: history is on Updates' own log for the apt actions.
FIX_LOG_LIMIT = 40


def email_rows(raw: dict, ts: str) -> list[EmailAuthRow]:
    """SPF / DKIM / DMARC per monitored domain, alphabetically.

    The verdict is deliberately `_email_checks`' own -- SPF present and DMARC
    enforcing -- so the panel and the checklist row for one domain can never
    disagree.
    """
    out = []
    for domain in sorted((raw.get("email_sec") or {})):
        e = (raw.get("email_sec") or {})[domain] or {}
        policy = str(e.get("dmarc_policy") or "")
        out.append(EmailAuthRow(
            domain=domain, spf=str(e.get("spf") or ""),
            dkim_selector=str(e.get("dkim_selector") or ""),
            dmarc=str(e.get("dmarc") or ""), dmarc_policy=policy,
            has_mx=bool(e.get("has_mx")),
            passed=bool(e.get("spf")) and policy in ("quarantine", "reject"),
            checked=str(e.get("ts") or ts),
            # Reported, never chased: the records are read so a spoofable
            # domain in the estate is visible, but this one belongs to
            # somebody else and Vault will not ask for it to be fixed.
            advisory=bool(e.get("advisory")),
        ))
    return out


def ext_port_rows(raw: dict, cfg: dict, ts: str) -> list[ExtPortRow]:
    """What the internet answers on, per site host, with the admin-probe hits
    for that host beside it -- the reference's fourth column.

    The probe keys `admin_probe` by a human label ("Roots prod
    (status.example.org)"), not by host, so a finding is attached to a row when the
    row's host appears anywhere in that label -- the same substring relation
    the sensor's own alert text relies on.
    """
    allowed = set(DEFAULT_SITE_PORTS) | {
        int(p) for p in ((cfg.get("home") or {}).get("site_allowed_ports") or [])
        if str(p).isdigit()
    }
    probes = raw.get("admin_probe") or {}
    out = []
    for host in sorted(raw.get("external_sites") or {}):
        site = (raw.get("external_sites") or {})[host] or {}
        ports = [int(p) for p in (site.get("open_ports") or []) if str(p).isdigit()]
        findings: list[str] = []
        for label, probe in probes.items():
            if host in label:
                findings.extend(sorted((probe or {}).get("hits") or {}))
        out.append(ExtPortRow(
            host=host, ip=str(site.get("ip") or ""), ports=sorted(ports),
            unexpected=sorted(p for p in ports if p not in allowed),
            findings=findings, probe_host=str(site.get("probe_host") or ""),
            checked=str(site.get("ts") or ts),
        ))
    return out


def fix_log(db: DbSession, tenant) -> list[FixLogRow]:
    """Every repair Vault ran, newest first, capped at `FIX_LOG_LIMIT`.

    `Fix.out` is the command's own tail, which can be long and is not a
    sentence -- only its first line reaches `detail`, and a failure's line is
    what a reader actually needs.
    """
    rows = (
        db.query(m.Fix)
        .filter_by(tenant_id=tenant.id)
        .order_by(m.Fix.ts.desc())
        .limit(FIX_LOG_LIMIT)
        .all()
    )
    out = []
    for f in rows:
        first_line = (f.out or "").strip().splitlines()[:1]
        out.append(FixLogRow(ts=f.ts, host=f.host, action=f.action, label=f.label or "",
                             by=f.by or "auto", ok=bool(f.ok),
                             detail=first_line[0][:200] if first_line else ""))
    return out


def check_detail(db: DbSession, tenant, role: str, key: str, net=None, target: str = "") -> CheckDetail | None:
    """The drill-down for one failing Wi-Fi guard row, checklist row, or
    posture deduction -- `None` when `key` (+ `net`/`target`) matches no
    current check, which the router turns into a 404.

    Recomputes the same functions `build()` does (`wifi_guard`/
    `_secondary_guard`/`checks`) rather than reading anything cached, so the
    detail can never disagree with what the row on screen just showed; none
    of them touch geo lookups or the attacker table, so this stays cheap
    even though it is called once per click rather than once per poll.
    """
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    ts = scan.ts if scan else ""

    network_id = ""
    if key.startswith("wifi:"):
        # No explicit network -> the primary network, same default `build()`
        # shows (`shown = primary_guard`) -- so a decision recorded from the
        # default view files under the same network id that view's own
        # `checks()`/`wifi_guard()` call resolves it against.
        effective_net = net or networks.primary(db, tenant.id)
        if effective_net is not None:
            network_id = effective_net.id
            guard = (wifi_guard(raw, ts) if effective_net.is_primary
                     else _secondary_guard(db, tenant, effective_net, ts))
        else:
            guard = wifi_guard(raw, ts)
        check = next((r for r in guard.rows if r.key == key and (not target or r.target == target)), None)
        if check is None:
            # `wifi:home` ("Wi-Fi encryption") shares the guard's prefix but
            # is a row of the general checklist, not of the guard -- so it
            # was never found here, the router 404'd, and the drawer said
            # "This check could not be loaded" on every click. Fall through
            # to the checklist, under its network-agnostic decision bucket
            # (`checks()` attaches decisions with `network_id=""`).
            network_id = ""
            check = next((c for c in checks(db, tenant, raw, ts)
                          if c.key == key and (not target or c.target == target)), None)
    else:
        check = next((c for c in checks(db, tenant, raw, ts)
                      if c.key == key and (not target or c.target == target)), None)
    if check is None:
        return None

    decision_row = (
        db.query(m.CheckDecision)
        .filter_by(tenant_id=tenant.id, check_key=check.key, network_id=network_id, target=check.target)
        .first()
    )
    # Imported here, not at module level: `remedy.py` imports `chat.registry`,
    # which (through `chat.tools` -> `routers.write` -> `payloads.jobs`)
    # imports `remedy` back -- a cycle that only resolves if something else
    # has already fully loaded `chat.registry`/`chat.tools` by the time
    # `remedy` is first touched. `payloads/security.py` is imported very
    # early in the app's own import graph (`payloads.overview` ->
    # `payloads.cameras` -> here), well before that -- a module-level import
    # here would make `remedy` the FIRST thing to pull in that whole chain,
    # which is exactly when it doesn't resolve. `_days_from` above already
    # deals with the same shape of cycle (`from ..scans import parse_ts`) the
    # same way.
    from .. import remedy as remedy_mod

    confidence, why_it_matters, need = _check_copy(check.key)
    what_we_saw = _what_we_saw(check, raw)
    if check.key == "wifi:home" and check.passed is None:
        # The stored copy for this check describes weak encryption, which is
        # the wrong thing to say -- and the wrong thing to ask for -- when the
        # reading simply did not arrive. An owner already on WPA2 was being
        # told to go set WPA2.
        confidence = "uncertain"
        why_it_matters = (
            "Vault could not read this network's encryption on the last scan, so it has "
            "nothing to judge. This is not a finding about your Wi-Fi: a sensor on Ethernet, "
            "a slow system_profiler, or macOS withholding the Wi-Fi details all look like this."
        )
        need = (
            "Nothing, unless this persists. It clears on its own once a sensor on the Wi-Fi "
            "reports the encryption again; if it stays this way for a day, check that the "
            "sensor machine is on Wi-Fi rather than a cable."
        )
    arg = ""
    if check.key.startswith("headers:"):
        # See `_headers_fix_target`'s docstring: unlike every other prefix
        # in `_FIX_ACTION_BY_PREFIX`, whether this one is fixable depends on
        # this specific site's config (its `ssh_host`) and which headers it
        # is actually missing, not on the key alone.
        site_row = db.query(m.Site).filter_by(tenant_id=tenant.id, name=check.target).first()
        sec = ((site_row.last or {}).get("sec") or {}) if site_row else {}
        action, host, arg, why_not = _headers_fix_target(tenant, check.target, sec)
        if why_not:
            need = why_not
    else:
        action = _fix_action_for(check.key)
        host = check.target if action else ""
    remedy = remedy_mod.resolve_check(
        db, tenant, role, action=action, host=host, title=check.title,
        msg=f"{check.title} on {check.target}: {check.evidence}", need=need, arg=arg,
    )
    return CheckDetail(
        key=check.key, area=check.area, title=check.title, target=check.target, network_id=network_id,
        passed=check.passed, last_verified=check.last_verified, evidence=check.evidence,
        what_we_saw=what_we_saw, why_it_matters=why_it_matters, confidence=confidence,
        remedy=remedy, decision=_decision_out(decision_row),
        repairs=_repairs_for(db, tenant, check.key, check.target),
    )


#: What a confirmed repair MEANS, in the words the customer would use.
#: Keyed by the same action names `verification.outcome` knows how to
#: re-check -- so a sentence here can only exist where there is a later
#: observation able to support it.
_PROOF_SENTENCE = {
    "enable_ufw": "A later scan found the firewall active.",
    "disable_ssh_password": "A later scan found password logins refused.",
    "enable_unattended": "A later scan found automatic security updates on.",
    "security_updates": "A later scan found no security updates outstanding.",
    "full_upgrade": "A later scan found nothing left to install.",
    "reboot": "A later scan found the machine no longer waiting to restart.",
    "enable_fail2ban": "A later scan found the brute-force guard running.",
    "kill_miner": "A later scan did not find the process again.",
}

#: Why an unconfirmed repair is unconfirmed. Never "it failed" -- it may
#: simply be that no scan has looked since.
_UNCONFIRMED = ("Vault ran this and the command reported success. Nothing has "
                "looked at the target again since, so this is not yet proof.")


def _repairs_for(db: DbSession, tenant, key: str, target: str) -> list[sd.RepairProof]:
    """Item 25: what Vault DID about this check, and whether it stuck.

    A red finding that goes green tells a customer nothing about whether
    anybody fixed it -- the thing may simply have stopped being visible.
    This is the answer: the commands Vault ran against this target for
    this check's action, and for each one whether a LATER scan looked again
    and found the world actually changed.

    `ran_ok` and `confirmed_at` stay apart on purpose, the same way
    `Fix.ok` and `Fix.verified_at` do. A command that exits 0 has reported
    success, not achieved it. Only `verification.outcome` -- a fresh
    reading of the target after the fact -- is proof, and the drawer says
    which of the two it is holding.
    """
    action = _fix_action_for(key)
    if not action or not target:
        return []
    rows = (db.query(m.Fix)
            .filter(m.Fix.tenant_id == tenant.id, m.Fix.host == target,
                    m.Fix.action == action)
            # By WHEN it ran, not by insert order. `id` is the order rows
            # reached the hub, and a sensor that catches up after a gap
            # pushes several at once -- which put an older repair above a
            # newer one and made the top of the list the wrong answer to
            # "has this been fixed since?".
            .order_by(m.Fix.ts.desc(), m.Fix.id.desc()).limit(6).all())
    out = []
    for f in rows:
        person = account_payload.is_person(f.by)
        confirmed = f.verified_at.isoformat(timespec="seconds") if f.verified_at else ""
        out.append(sd.RepairProof(
            label=(f.label or action.replace("_", " ")),
            action=action, target=f.host or target, ran_at=f.ts or "",
            by=(f.by or "Vault") if person else "Vault",
            by_person=person,
            ran_ok=bool(f.ok),
            confirmed_at=confirmed,
            proof=(_PROOF_SENTENCE.get(action, "A later scan confirmed the change.")
                   if confirmed else
                   ("The command did not succeed." if not f.ok else _UNCONFIRMED)),
            output=(f.out or "")[-600:],
        ))
    return out


#: The block lists the DNS filter ships, mapped to words a person uses.
#: `dark-web` is Vault's own built-in refusal (`.onion`, the clearnet Tor
#: gateways, Tor's bootstrap hosts); the rest are the downloaded feeds.
_BLOCK_CATEGORY = {
    "dark-web": "Dark web",
    "urlhaus": "Malware",
    "phishing-army": "Phishing",
    "stevenblack": "Ads and malware",
    "family": "Adult",
    "custom": "Blocked by you",
}


def _category(list_name: str) -> str:
    return _BLOCK_CATEGORY.get((list_name or "").strip(), (list_name or "other").strip() or "Other")


# --------------------------------------------------------------------------
# Named attack types (the "what about phishing / MITM / DDoS / ransomware"
# question). Each row is matched against the keys the SENSOR emits, so this
# table is only ever as honest as `sensor/guardian_sensor/alerts.py` -- when a
# detection is added there it belongs in a pattern here, and when there is no
# detection the row says so rather than inventing one.
#
# `stance` is the load-bearing field and two of these rows admit to weak or
# absent coverage on purpose. Do not "improve" them to `watching`.
THREAT_SPEC: list[dict] = [
    {
        "key": "phishing",
        "label": "Phishing",
        "what": "A fake page dressed up as your bank, Microsoft 365 or a parcel "
                "notice, sent to trick someone into typing a real password.",
        "stance": "blocking",
        "does": "Known phishing and scam domains are refused by Vault's DNS "
                "filter before the page can load, on every device set to use it. "
                "Vault also checks that your own domains publish SPF, DKIM and "
                "DMARC, so nobody can send mail that appears to come from you.",
        "limit": "A brand-new phishing domain can be live for hours before it "
                 "reaches any blocklist. The filter is a net, not a wall.",
        "patterns": [r"^email:", r"^sec:redirect:"],
        "categories": ("Phishing", "Ads and malware"),
    },
    {
        "key": "malware",
        "label": "Malware and viruses",
        "what": "A program that installs itself and then steals, spies or "
                "quietly uses the machine for someone else.",
        "stance": "blocking",
        "does": "Malware domains are refused before anything can download from "
                "them or call home. On the machines Vault runs on it also "
                "watches for crypto-miners, rootkit tricks such as "
                "/etc/ld.so.preload, executables dropped in /tmp and startup "
                "items pointed at odd places, and reports whether the built-in "
                "defences are switched on.",
        "limit": "Vault is not an antivirus. It does not scan files, and it "
                 "has no agent on phones, tablets or TVs -- for those it can only "
                 "block what they try to reach.",
        "patterns": [r"^sec:miner:", r"^sec:preload:", r"^sec:tmpexec:",
                     r"^sec:launch-item:", r"^sec:endpoint:"],
        "categories": ("Malware", "Ads and malware"),
    },
    {
        "key": "mitm",
        "label": "Man in the middle",
        "what": "Somebody puts themselves between your device and the internet "
                "-- a fake Wi-Fi network, a second router, a spoofed gateway -- so "
                "everything you send passes through their hands first.",
        "stance": "watching",
        "does": "Vault re-checks the way out of every network on each scan: "
                "the router's MAC address, which machine handed out the DHCP "
                "lease, which machine the lease names as the uplink, where the "
                "default route points, whether a second router or an off-subnet "
                "device has appeared, and whether your Wi-Fi name is being "
                "broadcast by hardware that is not yours. It also watches for "
                "your sites resolving to a new address and for obsolete TLS.",
        "limit": "",
        "patterns": [r"^home:route", r"^home:rogue-dhcp", r"^home:rogue-uplink",
                     r"^home:evil-twin", r"^home:gw-mac", r"(^|:)second-router:",
                     r"(^|:)offsubnet:", r"^dns:", r"^sec:tls:", r"^cert:issuer:"],
        "categories": (),
    },
    {
        "key": "breakin",
        "label": "Break-in attempts",
        "what": "Somebody guessing passwords at your servers, or walking in "
                "through a door that was left open.",
        "stance": "watching",
        "does": "Failed-login spikes, individual attacking IPs, fail2ban going "
                "quiet while an attack runs, password logins left enabled, the "
                "firewall being off, new listening ports, new root SSH keys and "
                "changed login accounts are all alerted on -- and several of them "
                "Vault repairs by itself.",
        "limit": "",
        "patterns": [r"^bruteforce:", r"^fail2ban:", r"^sec:attacker:",
                     r"^sec:sshpw:", r"^sec:login:", r"^sec:authkeys:",
                     r"^sec:users:", r"^sec:ufw:", r"^ports:.*:open",
                     r"^sec:extport:", r"^sec:lanlogin:", r"^sec:cron:"],
        "categories": (),
    },
    {
        "key": "dos",
        "label": "Denial of service (DoS and DDoS)",
        "what": "An attacker floods a site or your internet line with traffic "
                "until nothing else can get through.",
        "stance": "effect-only",
        "does": "Vault measures the damage, not the flood: it tells you "
                "within a minute when a site stops answering or turns slow, when "
                "packet loss or latency to the internet climbs, when a server's "
                "load spikes, and when the line drops entirely.",
        "limit": "Vault cannot absorb or filter an attack, and it cannot tell "
                 "a flood apart from an outage. Stopping a real DDoS is the job "
                 "of your host or a service like Cloudflare.",
        "patterns": [r"^site:.*:down$", r"^site:.*:slow$", r"^wan:path$",
                     r"^wan:loss:", r"^wan:slow:", r"^load:", r"^speed:slow$"],
        "categories": (),
    },
    {
        "key": "ransomware",
        "label": "Ransomware",
        "what": "Malware that encrypts every file it can reach and then demands "
                "payment for the key.",
        "stance": "indirect",
        "does": "Vault narrows the ways in: it blocks the domains this kind "
                "of malware fetches and reports to, keeps security updates and "
                "known vulnerabilities from piling up, closes off password-based "
                "SSH, and flags new admin access.",
        "limit": "Vault does not detect an encryption run in progress and "
                 "cannot stop one. Nothing here replaces a backup you have "
                 "actually restored from -- Vault does not check your backups.",
        "patterns": [r"^sec:secupd:", r"^sec:cve:", r"^upd:pending:", r"^reboot:"],
        "categories": ("Malware",),
    },
    {
        "key": "darkweb",
        "label": "Dark web and Tor",
        "what": "Hidden .onion and I2P services, and the public gateways that "
                "reach them from an ordinary browser.",
        "stance": "blocking",
        "does": "Vault refuses .onion, .i2p and .exit names outright and "
                "blocks the known tor2web-style gateways, on every device using "
                "its DNS filter.",
        "limit": "A Tor client that ships its own resolver does not ask "
                 "Vault, so this stops casual access, not a determined one.",
        "patterns": [],
        "categories": ("Dark web",),
    },
]

_THREAT_RE = {t["key"]: [re.compile(p) for p in t["patterns"]] for t in THREAT_SPEC}


def threats(db: DbSession, tenant, days: int = 7) -> list:
    """Every named attack type with its live evidence.

    The counts come from the same open-alert definition the rest of the app
    uses (`m.SETTLED_ALERT_STATUSES`), so a class cannot claim "clear" while
    its alert is sitting open on the Inbox.
    """
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    open_alerts = (db.query(m.Alert)
                     .filter(m.Alert.tenant_id == tenant.id,
                             m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES))
                     .all())
    cat_counts: dict[str, int] = {}
    for name, n in (db.query(m.DnsQuery.list, func.count(m.DnsQuery.id))
                      .filter(m.DnsQuery.tenant_id == tenant.id,
                              m.DnsQuery.blocked.is_(True),
                              m.DnsQuery.ts >= since)
                      .group_by(m.DnsQuery.list).all()):
        c = _category(name)
        cat_counts[c] = cat_counts.get(c, 0) + int(n)

    _rank = {"CRIT": 0, "WARN": 1, "INFO": 2}
    out = []
    for spec in THREAT_SPEC:
        res = _THREAT_RE[spec["key"]]
        hits = [a for a in open_alerts if any(r.search(a.key) for r in res)]
        hits.sort(key=lambda a: (_rank.get(a.sev, 3), a.key))
        out.append(ThreatClass(
            key=spec["key"], label=spec["label"], what=spec["what"],
            stance=spec["stance"], does=spec["does"], limit=spec["limit"],
            open_count=len(hits),
            blocked=sum(cat_counts.get(c, 0) for c in spec["categories"]),
            signals=[ThreatSignal(key=a.key, title=a.msg, severity=a.sev) for a in hits[:4]],
        ))
    return out


#: Findings shown in the drawer, newest first. A class with hundreds of
#: settled alerts is a real shape -- phishing on a busy tenant -- and a drawer
#: is read, not audited, so it is capped. The Alerts tab is where the whole
#: list lives.
THREAT_FINDING_CAP = 50


def threat_detail(db: DbSession, tenant, key: str, days: int = 7):
    """One attack class, opened up: what was found and how it was settled.

    The card (`threats()` above) deliberately shows only OPEN alerts, capped at
    four. That is right for "is anything wrong now" and useless for "how did
    you deal with it", because a settled alert is by definition not open. So
    this reads settled alerts as well, and carries what settled them.

    Settled means `m.SETTLED_ALERT_STATUSES` -- the same constant the card and
    the Inbox use. That constant exists because five hand-copied literals had
    already drifted apart with `acked` missing from some of them, and this is
    not going to become a sixth.

    `blocked` stays a separate number. It is a DNS count from `DnsQuery` over
    the window, credited per class through `THREAT_SPEC[*]["categories"]`, and
    adding it to a count of alerts would produce a total that means nothing.

    Returns `None` for a key that is not a class, so the route can 404 rather
    than invent an empty attack type.
    """
    spec = next((t for t in THREAT_SPEC if t["key"] == key), None)
    if spec is None:
        return None

    res = _THREAT_RE[key]
    since_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    since = since_dt.isoformat(timespec="seconds")

    # Open alerts are NOT windowed -- an alert that has been open for a month
    # is exactly the thing this drawer must show. Settled ones are, or a
    # long-lived tenant's drawer becomes an archive nobody reads.
    rows = (db.query(m.Alert)
              .filter(m.Alert.tenant_id == tenant.id)
              .all())

    findings = []
    open_n = settled_n = 0
    for a in rows:
        if not any(r.search(a.key) for r in res):
            continue
        resolved = a.status in m.SETTLED_ALERT_STATUSES
        if resolved:
            if (a.last_seen or "") < since:
                continue
            settled_n += 1
        else:
            open_n += 1
        findings.append(sd.ThreatFinding(
            key=a.key, title=a.msg or "", severity=a.sev or "",
            why=a.why or "", evidence=dict(a.evidence or {}),
            status=a.status or "", resolved=resolved,
            fix=a.fix or "", resolved_by=a.resolved_by or "",
            first_seen=a.first_seen or "", last_seen=a.last_seen or "",
            count=int(a.count or 0),
        ))

    # Open first, then worst, then most recent. Someone opening this wants the
    # live problems before the history of settled ones.
    _rank = {"CRIT": 0, "WARN": 1, "INFO": 2}
    findings.sort(key=lambda f: (f.resolved, _rank.get(f.severity, 3), f.last_seen), reverse=False)

    cat_counts: dict[str, int] = {}
    for name, n in (db.query(m.DnsQuery.list, func.count(m.DnsQuery.id))
                      .filter(m.DnsQuery.tenant_id == tenant.id,
                              m.DnsQuery.blocked.is_(True),
                              m.DnsQuery.ts >= since)
                      .group_by(m.DnsQuery.list).all()):
        c = _category(name)
        cat_counts[c] = cat_counts.get(c, 0) + int(n)

    return sd.ThreatDetail(
        key=spec["key"], label=spec["label"], what=spec["what"],
        stance=spec["stance"], does=spec["does"], limit=spec["limit"],
        days=days, open_count=open_n, resolved_count=settled_n,
        blocked=sum(cat_counts.get(c, 0) for c in spec["categories"]),
        findings=findings[:THREAT_FINDING_CAP],
    )


def protection(db: DbSession, tenant, raw: dict, days: int = 7) -> ProtectionPanel:
    """What is actually keeping malware and the dark web off this network.

    Two honest halves. The blocks are counted from `DnsQuery`, so every
    number can be checked against the rows underneath it. The defences come
    from the sensor's own host, because that is the only machine Vault
    can see inside -- it has no agent on phones or TVs and this panel must
    not imply that it does.
    """
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")

    rows = (db.query(m.DnsQuery.list, func.count(m.DnsQuery.id))
              .filter(m.DnsQuery.tenant_id == tenant.id,
                      m.DnsQuery.blocked.is_(True),
                      m.DnsQuery.ts >= since)
              .group_by(m.DnsQuery.list).all())
    tally: dict[str, int] = {}
    for name, n in rows:
        tally[_category(name)] = tally.get(_category(name), 0) + int(n)
    blocked = [ThreatsBlocked(category=k, count=v)
               for k, v in sorted(tally.items(), key=lambda kv: -kv[1])]

    recent_q = (db.query(m.DnsQuery)
                  .filter(m.DnsQuery.tenant_id == tenant.id, m.DnsQuery.blocked.is_(True))
                  .order_by(m.DnsQuery.ts.desc()).limit(8).all())
    names = {}
    macs = [q.mac for q in recent_q if q.mac]
    if macs:
        names = {d.mac: (d.label or d.name or d.mac)
                 for d in db.query(m.Device).filter(m.Device.tenant_id == tenant.id,
                                                    m.Device.mac.in_(macs)).all()}
    recent = [BlockedLookup(name=q.name, device=names.get(q.mac or "", q.mac or "a device"),
                            category=_category(q.list), ts=q.ts) for q in recent_q]

    ep = (raw or {}).get("endpoint") or {}
    labels = {
        "sip": ("System Integrity Protection", "stops even an admin process rewriting the system"),
        "gatekeeper": ("Gatekeeper", "refuses to launch unsigned or unnotarised apps"),
        "filevault": ("Disk encryption", "makes the disk unreadable if the machine is stolen"),
        "firewall": ("Firewall", "refuses unexpected inbound connections"),
        "malware_scanner": ("Malware scanner", "gives this host something to scan files with"),
    }
    defences = []
    for key, on in (ep.get("checks") or {}).items():
        label, buys = labels.get(key, (key.replace("_", " ").title(), ""))
        defences.append(DeviceDefence(
            key=key, label=label, buys=buys,
            state="on" if on is True else ("off" if on is False else "unknown")))
    defences.sort(key=lambda d: ({"off": 0, "unknown": 1, "on": 2}[d.state], d.label))

    return ProtectionPanel(
        defences=defences,
        defences_host=("This Mac" if ep.get("platform") == "mac" else
                       ("This server" if ep.get("platform") else "")),
        blocked=blocked,
        blocked_total=sum(b.count for b in blocked),
        recent=recent,
        # The filter is answering if it has logged anything at all recently,
        # blocked or not -- a count of zero blocks is good news, not an
        # outage, and the two must not look the same.
        filtering=bool(db.query(m.DnsQuery.id)
                         .filter(m.DnsQuery.tenant_id == tenant.id, m.DnsQuery.ts >= since)
                         .first()),
        days=days,
        threats=threats(db, tenant, days),
    )


def build(db: DbSession, tenant, net=None) -> SecurityPayload:
    """`net` narrows the Wi-Fi guard to one network.

    `wifi` stays the PRIMARY network's guard when no `?network=` is given, so
    every caller that predates Task 51 (and Task 32's tests) keeps working;
    `wifis` is the per-network list, one entry per network, each carrying its
    own `network` ref.
    """
    scan = latest_scan(db, tenant.id)
    raw = scan.raw if scan else {}
    ts = scan.ts if scan else ""

    cfg = tenant.config or {}
    attacker_method = ((cfg.get("auto_fix") or {}).get("attacker_method")) or "auto"
    netrefs = networks.refs_by_id(db, tenant.id)
    net_rows = db.query(m.Network).filter_by(tenant_id=tenant.id).all()
    net_rows.sort(key=lambda n: (not n.is_primary, (n.name or "").lower()))

    # Fetched once and threaded through every check-producing function below
    # so a "leave it" decision (owner request: resolve-or-leave-it on a
    # failing Wi-Fi guard / checklist row) reads back on every poll, not just
    # the request that recorded it.
    decisions = _decisions_by(db, tenant)
    primary_row = next((r for r in net_rows if r.is_primary), None)
    primary_guard = wifi_guard(raw, ts, decisions, primary_row.id if primary_row else "")
    wifis: list[WifiGuard] = []
    for row in net_rows:
        guard = (primary_guard.model_copy(deep=True) if row.is_primary
                 else _secondary_guard(db, tenant, row, ts, decisions))
        # Assigned, so build the model explicitly: a plain dict here would slip
        # past validation and only surface as a pydantic serializer warning.
        guard.network = NetworkRef(**netrefs[row.id])
        wifis.append(guard)
    shown = primary_guard
    if net is not None:
        shown = next((g for g in wifis if g.network and g.network.id == net.id), primary_guard)

    return SecurityPayload(
        scorecards=scorecards(db, tenant, raw),
        posture_coverage=posture_coverage(db, tenant),
        wifi=shown,
        checks=checks(db, tenant, raw, ts, decisions),
        attackers=attackers(db, tenant, raw, net=net),
        exposure=exposure(raw, cfg, camera_facts(db, tenant, raw)),
        attacker_method=attacker_method,
        methods=list(METHODS),
        networks=[NetworkHealth(**h) for h in networks.health(db, tenant, raw)],
        wifis=wifis,
        extra_checks=extra_checks(raw),
        heartbeats=heartbeats(db, tenant, raw),
        domains=domains(raw),
        email=email_rows(raw, ts),
        ext_ports=ext_port_rows(raw, cfg, ts),
        fix_log=fix_log(db, tenant),
        protection=protection(db, tenant, raw),
    )
