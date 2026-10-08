"""Alert building, security scoring, recommendations, incident correlation,
and unusual-hours detection.

Ported from the Nexus collector (~/.claude/nexus-agent/checks.py) — logic
copied faithfully, never imported from that directory.
"""
import datetime as dt
import json
import os
import re

from .probe import humanize as _hz
from . import cameras, config, paths
from .home import STRONG_WIFI
from .lan import RISKY_PORTS, host_name, mac_vendor
from .util import cfg_host
from .endpoint import LABELS as endpoint_labels


def _uptime_reset(prev, cur):
    def days(u):
        m = re.search(r"(\d+) day", u)
        return int(m.group(1)) if m else 0
    return days(cur) < days(prev)


# ---------------- per-network LAN alerts ----------------
def lan_key(kind, net, mac=None, port=None):
    """The dedup key for one LAN alert on one network.

    The PRIMARY network keeps the legacy, un-namespaced key (`lan:new:<mac>`)
    so a single-network sensor reproduces Nexus byte-for-byte and every hub
    that predates Task 51 keeps working. Every other network is namespaced
    slug-FIRST — `lan:<slug>:<sub>:<rest>` — which is the shape
    `hub/guardian_hub/networks.lan_alert_key` produces and
    `canonical_lan_key` strips (it tells the two shapes apart by testing
    whether the segment after `lan:` is a known sub-key, so the slug cannot
    sit after it).
    """
    net = net or {}
    slug = net.get("slug")
    head = "lan:" if (net.get("primary", True) or not slug) else f"lan:{slug}:"
    key = head + kind
    if mac:
        key += f":{mac}"
    if port is not None:
        key += f":{port}"
    return key


def canonical_lan_key(key):
    """Strip the network segment so a key can be read positionally again —
    the sensor-side mirror of the hub's `networks.canonical_lan_key`."""
    if not key or not key.startswith("lan:"):
        return key
    parts = key.split(":")
    if len(parts) >= 3 and parts[1] not in config.LAN_SUBKEYS and parts[2] in config.LAN_SUBKEYS:
        return "lan:" + ":".join(parts[2:])
    return key


def net_state(state, net):
    """This network's slice of `state["lan"]`.

    Falls back to the flat legacy block for the primary network, so
    `build_alerts` can be called with a pre-Task-52 `state.json` (the parity
    fixtures are exactly that) and still see its `known_macs`.
    """
    ls = state.get("lan") or {}
    slug = (net or {}).get("slug")
    nets = ls.get("networks")
    if isinstance(nets, dict) and slug in nets:
        return nets[slug] or {}
    return ls if (net or {}).get("primary", True) else {}


def alert_networks(res, cfg):
    """The networks `build_alerts` should walk: this scan's `networks` list
    when the runner produced one, otherwise the legacy single `res["lan"]`
    block promoted to the primary network (which is what a pre-Task-52
    `last.json` — the parity fixture — carries)."""
    nets = res.get("networks")
    if isinstance(nets, list) and nets:
        # unreachable entries are kept: they carry no `devices`, so only the
        # state-driven always-on/flapping alerts fire for them — a network the
        # sensor cannot reach must never read as "every device left".
        return nets
    lan = res.get("lan")
    if not lan:
        # no sweep this cycle (off the LAN, or --no-lan): the always-on /
        # flapping alerts still come from the stored baseline, as before.
        return [{"slug": config.primary_slug(cfg), "primary": True, "status": "active",
                 "name": (cfg.get("lan") or {}).get("name") or "Home network"}]
    lan.setdefault("slug", config.primary_slug(cfg))
    lan.setdefault("primary", True)
    lan.setdefault("expected_gateway", (cfg.get("lan") or {}).get("expected_gateway"))
    lan.setdefault("subnet_prefix", (cfg.get("lan") or {}).get("subnet_prefix") or "")
    lan.setdefault("expected_wan_ip", (cfg.get("lan") or {}).get("expected_wan_ip") or "")
    lan.setdefault("name", (cfg.get("lan") or {}).get("name") or "Home network")
    return [lan]


def served_or(site):
    """The served expiry, for the message. Guarded by the caller."""
    return site.get("cert_expires") or "an unknown date"


def _cert_stale_served(site, hosts):
    """A newer certificate sitting on a host than the one being served.

    certbot succeeded, the service was never reloaded, and the host goes on
    presenting the old certificate until it expires and the site goes down.
    The trust plan calls this the most common real-world certificate outage
    and the one worth catching (§2.5I); it is invisible to every check that
    looks at only one side, because the renewal timer reports success and
    the file on disk is perfectly valid.

    Seeing it needs both vantage points at once, which Vault already has:
    the served certificate read over TLS like any visitor, and the file read
    over SSH. If a host holds a certificate for this hostname that outlives
    the one being served, the renewal happened and nothing picked it up.

    Compared on EXPIRY, not serial. A serial mismatch is true of any two
    different certificates -- a rollback, a second unrelated cert for the
    same name -- while "the file outlives what is served" is specifically
    the renewed-but-not-reloaded shape, and it is the shape whose remedy is
    a reload.

    Only reachable hosts are consulted. No inventory is not an empty
    inventory: a host Vault could not reach has not told it there is
    nothing newer, and treating silence as "all current" is the same
    unmeasured-reads-as-healthy mistake the score coverage gate exists for.
    """
    served = site.get("cert_expires")
    host = site.get("host")
    if not served or not host:
        return None
    for h in hosts:
        if not h.get("reachable"):
            continue
        on_disk = (h.get("certs_on_disk") or {}).get(host)
        if on_disk and on_disk > served:
            return h.get("name") or h.get("hostname") or "a server", on_disk
    return None


def identity_match(mac, info, known, live_macs):
    """Is this new address a known device that rotated its MAC? With reasons.

    MAC randomization is the false-positive machine the trust plan singles
    out (§2.5E): a phone rotating its private Wi-Fi address becomes a stream
    of "new unknown device" alerts, each dropping the score and teaching the
    owner to dismiss the next one.

    Matching on the device NAME alone -- what this used to do -- is weak in
    a specific and checkable way. If the supposed predecessor is still
    answering on this very sweep then whatever the new address is, it is not
    that device wearing a different one. Two Apple devices on one account
    share a name; so do a laptop and the hotspot it is running. Both used to
    produce a confident-looking suggestion.

    So the decisive signal is the correlation the old rule ignored: the old
    address going quiet exactly as the new one appears. Ports are a second,
    independent signal -- the same service set is evidence a name cannot
    give on its own.

    Returns `(parent_mac | None, confidence, reasons)` where confidence is
    "high", "moderate" or "none". It NEVER merges and never reads or grants
    trust: the caller raises a suggestion for a person to confirm. Silently
    merging at low confidence is worse than the flood it replaces, because a
    wrongly merged device inherits trust nobody gave it -- a security
    failure rather than an untidy list.
    """
    reasons = []
    # Only a locally-administered address can be a privacy MAC. A device
    # with a real OUI that happens to share a name is a different device.
    if not int(mac[:2], 16) & 2:
        return None, "none", ["address is a real hardware OUI, not a private one"]

    name = (info.get("name") or "").split(" (")[0].strip().lower()
    if not name:
        return None, "none", ["the new device reported no name to compare"]

    candidates = [m for m, d in known.items()
                  if m != mac
                  and (d.get("name") or "").split(" (")[0].strip().lower() == name]
    if not candidates:
        return None, "none", ["no known device shares this name"]

    parent = candidates[0]
    reasons.append(f"name matches {known[parent].get('name') or parent}")

    if parent in live_macs:
        reasons.append("but that device is still answering on this sweep, so this is a "
                       "different one")
        return None, "none", reasons
    reasons.append("and that device is no longer answering, which is what a rotation "
                   "looks like")

    new_ports = set((info.get("ports") or {}).keys())
    old_ports = set((known[parent].get("ports") or {}).keys())
    if new_ports and old_ports and new_ports & old_ports:
        reasons.append("open port signature matches ("
                       + ", ".join(sorted(new_ports & old_ports)) + ")")

    return parent, "high", reasons


def _lan_alerts(res, cfg, state, net, add):
    """Every `lan:`/`home:gw-mac` alert for ONE network. Extracted verbatim
    from `build_alerts` with each literal key replaced by `lan_key` and each
    `state["lan"]` read replaced by that network's slice."""
    lan = net if net.get("devices") is not None else None
    nstate = net_state(state, net)
    labels = net.get("watch_ips_labels") or (cfg.get("lan") or {}).get("watch_ips") or {}
    if lan:
        known = nstate.get("known_macs")
        if known is None:
            add("INFO", lan_key("baseline", net), f"LAN baseline recorded: {len(lan['devices'])} devices (future joins will be flagged)")
            lan["new_devices"] = []
        else:
            new = []
            for ip, mac in lan["devices"].items():
                if mac and mac not in known:
                    info = {"ip": ip, "mac": mac, "vendor": mac_vendor(mac), "name": host_name(ip, lan.get("names")), "first_seen": res["ts"]}
                    parent, conf, why = identity_match(
                        mac, info, known, set(lan["devices"].values()))
                    if parent:
                        info["suggested_parent"] = parent
                        info["identity_confidence"] = conf
                        info["identity_reasons"] = why
                        add("WARN", lan_key("rotated", net, mac=mac),
                            f"{info['name']} may be a known device using a new private MAC "
                            f"({parent} -> {mac}) — {'; '.join(why)}; confirm before trusting, "
                            "and Vault will not merge them on its own.")
                    else:
                        add("WARN", lan_key("new", net, mac=mac), f"NEW device joined the Wi-Fi/LAN: {ip} {mac} — {info['vendor']}" + (f" ({info['name']})" if info["name"] else ""))
                    new.append(info)
            lan["new_devices"] = new
        # A watched device must miss TWO consecutive sweeps before "not on the
        # LAN": an ARP sweep skips a sleeping phone or a Wi-Fi power-save blip.
        _miss = state.setdefault("miss_counts", {})
        for ip, present in (lan.get("watch_ips") or {}).items():
            _k = f"watch:{net.get('slug', '')}:{ip}"
            if present:
                _miss.pop(_k, None); continue
            _miss[_k] = _miss.get(_k, 0) + 1
            if _miss[_k] >= 2:
                add("WARN", lan_key("missing", net, mac=ip), f"{labels.get(ip, ip)} ({ip}) not on the LAN")
        prev_gw = nstate.get("gw_mac") or (state.get("gw_mac") if net.get("primary") else None)
        if lan.get("gw_mac") and prev_gw and lan["gw_mac"] != prev_gw:
            key = "home:gw-mac" if net.get("primary", True) else f"home:gw-mac:{net.get('slug')}"
            add("CRIT", key, f"Router MAC changed {prev_gw} -> {lan['gw_mac']} — ARP spoofing / rogue gateway unless you replaced the router")
        # --- cameras -------------------------------------------------------
        # `net["cameras"]` is the census `runner._device_pass` built from this
        # network's own known_macs. A network whose census has never been
        # stored learns it silently (`diff` returns nothing for a None
        # baseline), so enrolling a sensor in a house full of cameras opens no
        # alerts at all on the first scan.
        cens = lan.get("cameras")
        if cens is not None:
            new_cams, gone_cams = cameras.diff(nstate.get("cameras"), cens, res["ts"])
            for mac in new_cams:
                info = (cens.get("detail") or {}).get(mac, {})
                who = info.get("label") or info.get("name") or info.get("vendor") or mac
                why = "; ".join(info.get("reasons") or []) or "camera signals on the network"
                add("INFO", lan_key("camera-new", net, mac=mac),
                    f"New camera in the inventory: {who} at {info.get('ip') or '?'} ({why}) — "
                    f"{cens['count']} camera(s) now on {net.get('name') or 'this network'}")
            for row in gone_cams:
                who = row.get("label") or row.get("name") or row.get("vendor") or row["mac"]
                quiet = str(row.get("last_online") or "")[11:16]
                add("WARN", lan_key("camera-gone", net, mac=row["mac"]),
                    f"Camera {who} ({row.get('ip') or '?'}) has stopped answering — it was reachable at "
                    f"{quiet or 'its last scan'} and has been quiet since. Check its power, cable and recorder.")
        # --- a device on-link with an address outside this network ----------
        for ip, mac in sorted((lan.get("offsubnet") or {}).items()):
            add("WARN", lan_key("offsubnet", net, mac=mac or ip),
                f"{ip} ({mac or 'unknown MAC'}) answers on {net.get('name') or 'this network'} but its address is "
                f"outside {net.get('subnet_prefix') or '?'}.0/24 — it did not take an address from your router. "
                f"That is either a device someone gave a fixed address, or one bridging in from another network.")
        # --- a second router / DNS server on the network --------------------
        # Router-shaped means DNS plus a web admin port, on something that is
        # NOT the gateway. A rogue uplink looks exactly like this — and so does
        # a Pi-hole the owner installed himself, which is why this is a WARN
        # that says so, not an accusation.
        gw = net.get("expected_gateway")
        for mac in sorted({m for m in lan["devices"].values() if m}):
            d = (nstate.get("known_macs") or {}).get(mac) or {}
            if d.get("trusted") or d.get("ip") == gw:
                continue
            pts = {int(p) for p in (d.get("ports") or {}) if str(p).isdigit()}
            if 53 in pts and pts & {80, 443, 8080}:
                add("WARN", lan_key("second-router", net, mac=mac),
                    f"{d.get('label') or d.get('name') or d.get('ip') or mac} answers like a second router on "
                    f"{net.get('name') or 'this network'}: DNS plus a web admin page, and it is not the router "
                    f"{gw or ''}. A device handing out its own uplink would look like this; so would your own "
                    f"Pi-hole or mesh node — mark it trusted if it is yours.".replace("  ", " "))
        for ev in lan.get("port_events") or []:
            if ev["type"] == "port_open" and ev["port"] in RISKY_PORTS and not ev.get("trusted"):
                add("WARN", lan_key("port", net, mac=ev["mac"], port=ev["port"]),
                    f"{ev.get('label') or ev['ip']} now exposes {RISKY_PORTS[ev['port']]} (port {ev['port']}) on the LAN")
    _miss = state.setdefault("miss_counts", {})
    for m, d in (nstate.get("known_macs") or {}).items():
        # Same two-sweep rule for an always-on device: OFFLINE after one missed
        # sweep is how a printer in power-save became a WARN every night.
        _k = f"offline:{net.get('slug', '')}:{m}"
        if d.get("presence") == "OFFLINE" and d.get("always_on"):
            _miss[_k] = _miss.get(_k, 0) + 1
            if _miss[_k] >= 2:
                add("WARN", lan_key("offline", net, mac=m), f"{d.get('label') or d.get('name') or m} is OFFLINE (marked always-on)")
        else:
            _miss.pop(_k, None)
        if d.get("flapping"):
            _who = d.get("label") or d.get("name") or m
            _verdict = flapping_verdict(res, m)
            if _verdict is None:
                # The router says it is associated with a healthy signal, so
                # it never left: the gaps are this sensor's ping sweep missing
                # a sleeping device. Raising anything here would be reporting
                # our own blind spot as the owner's problem.
                pass
            else:
                add("INFO", lan_key("flapping", net, mac=m), f"{_who} {_verdict}")


#: Below this, a Wi-Fi signal is weak enough to lose packets and re-associate.
WEAK_DBM = -70

#: Associated at least this long counts as "it never left". An hour is well
#: past any single re-connect, so a device still holding the same association
#: was not the one dropping -- the sensor's probe was.
STABLE_ASSOC_S = 3600


def flapping_verdict(res, mac: str):
    """What to say about a device that keeps appearing and disappearing.

    Returns the sentence to alert with, or **None** meaning "say nothing":
    the gateway confirms the device is associated with a healthy signal, so
    it never left the Wi-Fi and there is nothing wrong with the network.

    Presence here is a ping sweep plus ARP (`lan.py`). A device in Wi-Fi
    power save ignores pings, so the gaps look identical to a device leaving.
    The old text resolved that by asserting "weak Wi-Fi or power saving" --
    a cause nothing had measured, and usually the wrong one.

    With no station table (no router credential, a failed read, a non-Fios
    gateway) this returns the original vaguer wording rather than inventing
    certainty: unknown is not the same as fine.
    """
    stations = res.get("wifi_stations") or []
    if not stations:
        return ("is flapping (joins/leaves repeatedly) — weak Wi-Fi or power "
                "saving; join/leave alerts muted")
    from .router_fios import read_signal
    sig = read_signal(stations, mac)
    if not sig:
        return ("keeps dropping off the Wi-Fi — the router has no connection "
                "for it right now, so it is genuinely disconnecting")
    rssi, assoc = sig.get("rssi"), sig.get("assoc_time") or 0
    if rssi is not None and rssi <= WEAK_DBM:
        band = f" on {sig['band']}" if sig.get("band") else ""
        return (f"has a weak Wi-Fi signal: {rssi} dBm{band}. That is low enough "
                f"to drop packets and re-connect — move it closer to an access point")
    if assoc >= STABLE_ASSOC_S:
        # Associated for an hour or more with a signal that is not weak: it
        # never left, so the gaps are this sensor's ping sweep missing a
        # sleeping device. A good signal ALONE is not enough -- a device that
        # re-associated a minute ago is genuinely reconnecting, however
        # strong it is right now, and silencing that would hide the thing
        # the owner actually wants to know.
        return None
    return (f"is joining and leaving the Wi-Fi at {rssi} dBm — it has only been "
            f"connected {assoc}s")


# ---------------- lateral movement, the honest version (Task 83 FortiNDR review) ----------------
def _lateral_alerts(res, cfg, state, add):
    """The one lateral-movement signal Vault can actually stand behind.

    Vault does not capture packets (a standing product ruling), so it
    never sees "device A talked to device B" in general -- `lan.lan_ports`
    is the sensor probing each device's own listening ports, not a record of
    who connected to whom. What IS a real, observed connection is a
    monitored host's own login record (`last -i`, read in `hosts.check_ssh`
    as `sec.login_sources`): an SSH sign-in that actually happened, not an
    inference from an open port.

    So the rule is narrow on purpose: a LAN device -- resolved to its MAC
    through this scan's own device inventory, never trusted by IP address
    alone, because IPs on a home LAN are DHCP leases that rotate -- signing
    in to a monitored host it has never signed in to before, per that host's
    own log. The first time a host is seen at all, its baseline is recorded
    silently (no alert), exactly like `lan:new`'s `known_macs is None` rule;
    only a genuinely new pairing after that fires. An address the sensor
    cannot resolve to a MAC is dropped, not flagged -- better a miss than a
    false alarm on a VPN or routed address Vault cannot pin down.
    """
    ip2mac = {}
    for net in alert_networks(res, cfg):
        for ip, mac in (net.get("devices") or {}).items():
            if mac:
                ip2mac[ip] = mac
    sec_state = state.get("sec") or {}
    for h in res.get("ssh") or []:
        if not h.get("reachable"):
            continue
        n = h["name"]
        h.setdefault("sec", {})
        sources = h["sec"].get("login_sources") or []
        internal_macs = sorted({ip2mac[ip] for ip in sources if ip in ip2mac})
        prev = (sec_state.get(n) or {}).get("lan_login_macs")
        merged = None
        if prev is None:
            if internal_macs:
                add("INFO", f"sec:lanlogin:{n}",
                    f"Recorded which devices on your network sign in to {n} over SSH; I will flag any new one from here")
                merged = {m: res["ts"] for m in internal_macs}
        else:
            merged = dict(prev)
            for m in internal_macs:
                if m not in prev:
                    add("WARN", f"sec:lanlogin:{n}:{m}",
                        f"A device on your network I have not seen sign in to {n} over SSH before just did. "
                        f"If that was you or something you set up, there is nothing to do. If not, find out "
                        f"what device {m} is and why it can reach {n}.")
                    merged[m] = res["ts"]
        if merged is not None:
            h["sec"]["lan_login_macs"] = merged


# ---------------- cameras behind a recorder (Task 73) ----------------
#: `error_kind` -> `(key suffix, severity, what to say)`. Every failed probe
#: has to produce a finding that names the reason: an owner with twelve
#: cameras and a recorder whose HTTP interface is switched off must be told
#: that, not shown a zero. `HUMAN_REMEDIES` in the hub's `remedy.py` carries
#: the matching "what I need from you" for each key suffix.
NVR_ERRORS = {
    "http_disabled": ("http-off", "WARN",
        "its HTTP interface looks switched off — only the proprietary client port answers, "
        "and Vault will not speak an undocumented protocol"),
    "api_disabled": ("http-off", "WARN",
        "it answers on HTTP but has no /cgi-bin/api.cgi — the HTTP/ONVIF API is disabled on this firmware"),
    "auth_failed": ("auth", "WARN", "it rejected the username or password stored for it"),
    "unreachable": ("down", "WARN", "it did not answer at all"),
    "timeout": ("down", "WARN", "it did not answer in time"),
    "protocol": ("http-off", "WARN", "it answered something that is not its documented HTTP API"),
    "no_credentials": ("setup", "INFO", "no username or password has been stored for it yet"),
    "out_of_scope": ("setup", "WARN",
        "its address is not inside any network configured on this site, so Vault will not contact it"),
    "unsupported": ("setup", "INFO", "Vault has no adapter for that make yet"),
}


def _nvr_alerts(res, A):
    """Findings for every registered NVR/DVR, appended straight onto `A` so
    each one can carry its own `net` (the recorder's camera network) rather
    than falling back to the primary.

    The five things that make this a watchdog rather than an inventory:
    a camera discovered, a camera that stopped answering, a channel that
    disappeared from the recorder entirely, the recorder itself unreadable,
    and a count that does not match what the owner said he has.
    """
    for r in res.get("recorders") or []:
        if not isinstance(r, dict):
            continue
        rid = r.get("id") or ""
        who = r.get("name") or r.get("host") or "the recorder"
        net = r.get("net") or None
        def _add(sev, what, msg, ch=None):
            # `nvr:<what>:<recorder id>[:<channel>]` -- the WHAT comes first so the
            # hub's `remedy.HUMAN_REMEDIES` (longest matching PREFIX wins) can
            # carry a different "what I need from you" per finding.
            key = f"nvr:{what}:{rid}" + ("" if ch is None else f":{ch}")
            A.append({"sev": sev, "key": key, "msg": msg, "net": net})
        if not r.get("ok"):
            suffix, sev, why = NVR_ERRORS.get(r.get("error_kind") or "", ("down", "WARN", "it could not be read"))
            _add(sev, suffix,
                 f"Cannot list the cameras on {who}: {why}. Until it can be read, Vault cannot tell "
                 f"you whether the cameras behind it are recording.")
            continue
        chans = r.get("channels") or []
        f = r.get("findings") or {}
        for ch in f.get("new") or []:
            name = ch.get("name") or f"channel {ch['channel'] + 1}"
            _add("INFO", "cam-new",
                 f"New camera behind {who}: {name} on channel {ch['channel'] + 1}"
                 + (f" ({ch['model']})" if ch.get("model") else "")
                 + f" — {len(chans)} camera(s) now on that recorder", ch=ch["channel"])
        for ch in f.get("offline") or []:
            name = ch.get("name") or f"channel {ch['channel'] + 1}"
            quiet = str(ch.get("last_online") or "")[11:16]
            _add("WARN", "cam-offline",
                 f"Camera {name} (channel {ch['channel'] + 1} on {who}) has stopped answering — it was up at "
                 f"{quiet or 'its last check'} and has been offline since. Check its cable, its PoE port and its power.",
                 ch=ch["channel"])
        for ch in f.get("vanished") or []:
            name = ch.get("name") or f"channel {ch['channel'] + 1}"
            _add("WARN", "cam-gone",
                 f"Channel {ch['channel'] + 1} ({name}) is no longer listed by {who} at all — the camera was "
                 f"removed from the recorder, or the recorder lost that channel. If you did not remove it, that is a blind spot.",
                 ch=ch["channel"])
        expected = int(r.get("expected_channels") or 0)
        if expected and len(chans) != expected:
            _add("WARN", "count",
                 f"{who} lists {len(chans)} camera(s), and you told Vault to expect {expected}. "
                 f"Either one is unplugged or not paired, or the expected number needs updating.")


def evaluate_capture(res):
    """Capture problems are things a PERSON fixes -- install a package, check
    a switch -- so both keys live in remedy.HUMAN_REMEDIES, not FIX_ALERT_KEY."""
    cap = res.get("capture") or {}
    out = []
    if not cap.get("interface"):
        return out
    err = cap.get("error") or ""
    if "not installed" in err or "could not start" in err:
        out.append({"sev": "INFO", "key": "capture:unavailable", "msg": f"Traffic capture is configured on {cap['interface']} but cannot run: {err}"})
    elif err.startswith("no packets"):
        out.append({"sev": "WARN", "key": "capture:silent", "msg": f"The mirror port {cap['interface']} has gone quiet ({err}); check the switch's mirror/SPAN setting"})
    return out


# ---------------- alerts ----------------

def uplink_blackout(res) -> bool:
    """Is every site "down" only because Vault itself cannot see out?

    The sensor is a single vantage point, so anything that breaks its own
    internet makes every monitored site fail in the same sweep. Reported
    naively that is N critical pages saying the customer's business is
    offline; once they discover it was the sensor's ISP, every future
    Vault alert reads as probably-nothing. Fake outages cost more trust
    than missed ones because they are self-inflicted and repeatable.

    Two INDEPENDENT signals must agree, and the independence is the whole
    design:

    - every configured site failed this sweep, and there are at least two.
      One site down is just that site; two failing together is suspicious
      but they might share a host.
    - `on_home_lan` is true and `wan_ip` is None: the sensor is on the LAN
      it expects and still could not get an address out of either external
      service `home.wan_ip` tries. That evidence does not come from the
      sites, which is what makes it admissible -- the sites cannot be
      witnesses about themselves.

    `on_home_lan` is required because `runner` only probes `wan_ip` when it
    is true. Off-LAN, None means "not measured", and treating that as "no
    internet" would silence real outages for any sensor whose gateway
    changed -- turning a trust fix into the most expensive bug in the
    product.

    The residual risk is stated rather than hidden: a genuine outage that
    takes out every site AND the sensor's own uplink at once (one building,
    one ISP) is reported as a blackout. That is the right call anyway --
    Vault genuinely cannot see whether those sites are up, and saying so
    beats asserting a state it did not measure.
    """
    sites = res.get("sites") or []
    if len(sites) < 2 or any(s.get("ok") for s in sites):
        return False
    return bool(res.get("on_home_lan")) and res.get("wan_ip") is None


def build_alerts(res, cfg, state):
    th = cfg["thresholds"]; A = []
    blackout = uplink_blackout(res)
    def add(sev, key, msg): A.append({"sev": sev, "key": key, "msg": msg})
    if blackout:
        add("CRIT", "sensor:uplink",
            f"Vault cannot reach the internet — all {len(res['sites'])} site checks failed in the "
            "same sweep and the sensor could not reach any address service. Those sites are UNKNOWN, "
            "not confirmed down; nothing on your network has been shown to be broken.")
    for s in res["sites"]:
        if not s["ok"]:
            # Suppressed during a blackout: an unreachable prober measured
            # nothing, so "DOWN" would be asserting a state never observed.
            # The uplink CRIT raised above already says those sites are
            # UNKNOWN, so staying quiet here is the whole point rather than a
            # missing alert.
            #
            # `_hz` stays on the error: it is the same treatment the SSH alert
            # below gives its own error string, and it bounds a raw stderr tail
            # at 160 characters instead of putting all of it in an alert.
            if not blackout:
                add("CRIT", f"site:{s['name']}:down", f"{s['name']} DOWN — http {s.get('code')} {_hz(s.get('error',''))}".strip())
        elif s["latency_s"] > th["http_latency_warn_s"]:
            add("WARN", f"site:{s['name']}:slow", f"{s['name']} slow: {s['latency_s']}s")
        # The weakest link in the SERVED CHAIN, not just the leaf. A site
        # goes down when any certificate in its chain lapses, so warning on
        # the leaf's 74 days while an intermediate has 9 is a confident
        # number pointing at the wrong certificate. `cert_chain_days_left`
        # is absent when the chain could not be read, and then the leaf
        # stands on its own -- unmeasured is not a reason to say less.
        d_leaf = s.get("cert_days_left")
        d_chain = s.get("cert_chain_days_left")
        d = min([x for x in (d_leaf, d_chain) if x is not None], default=None)
        if d is not None:
            weakest = s.get("cert_chain_weakest") or "leaf"
            where = "" if d == d_leaf or weakest == "leaf" else f" — the {weakest}, not the site's own certificate"
            when = s.get("cert_expires") if d == d_leaf else ""
            detail = f" ({when})" if when else ""
            if d <= th["cert_days_crit"]: add("CRIT", f"cert:{s['name']}", f"{s['name']} TLS cert expires in {d} days{detail}{where}")
            elif d <= th["cert_days_warn"]: add("WARN", f"cert:{s['name']}", f"{s['name']} TLS cert expires in {d} days{detail}{where}")
        ss = s.get("sec") or {}
        if ss.get("old_tls"): add("WARN", f"sec:tls:{s['name']}", f"{s['name']} still accepts obsolete {ss['old_tls']}")
        if ss.get("https_redirect") is False: add("WARN", f"sec:redirect:{s['name']}", f"{s['name']}: http:// does not redirect to https://")
        if ss and not ss.get("hsts"): add("INFO", f"sec:hsts:{s['name']}", f"{s['name']}: no HSTS header")
        prev = (state.get("sites") or {}).get(s["name"], {}).get("dns_a")
        if prev and s.get("dns_a") and prev != s["dns_a"]:
            add("CRIT", f"dns:{s['name']}", f"{s['name']} DNS changed {prev} -> {s['dns_a']} (hijack/misconfig?)")
        # Who signed the certificate, and did that change? A different CA can
        # mean interception, and nobody at this price point watches for it.
        #
        # WARN rather than CRIT on purpose. The overwhelmingly common cause is
        # a routine CA migration, or a CA renaming its own organisation field,
        # and a CRIT that is usually nothing teaches people to dismiss the one
        # that is not. The message says both names so the owner can recognise
        # their own migration in one glance and move on.
        #
        # Both guards matter: a failed handshake leaves no `cert_issuer` at
        # all, and `sites.check` writes "?" when an otherwise valid
        # certificate has no organisation field. Comparing either against a
        # real name would manufacture a change out of missing information --
        # the same mistake as scoring an unmeasured host as healthy.
        stale = _cert_stale_served(s, res.get("ssh") or [])
        if stale:
            where, newer = stale
            add("WARN", f"cert:stale-served:{s['name']}",
                f"{s['name']} is serving a certificate that expires {served_or(s)}, but "
                f"{where} already holds a newer one valid to {newer}. The renewal worked and "
                "the service never picked it up — it needs a reload, or the site will go down "
                "on the older date for no reason.")
        prev_iss = (state.get("sites") or {}).get(s["name"], {}).get("cert_issuer")
        now_iss = s.get("cert_issuer")
        if prev_iss and now_iss and "?" not in (prev_iss, now_iss) and prev_iss != now_iss:
            add("WARN", f"cert:issuer:{s['name']}",
                f"{s['name']} TLS certificate is now signed by {now_iss}, previously {prev_iss}. "
                "Normal if you changed certificate authority; worth checking if you did not.")
    # --- backups: the job that stopped working and still exits zero -------
    # A destination or host Vault could not reach raises NOTHING. It has
    # not told us the backup is late, and a "backup failed" page for an SSH
    # blip is how this check gets muted in a fortnight.
    for b in res.get("backups") or []:
        if not b.get("reachable"):
            continue
        key = f"backup:{b['name']}"
        if b.get("present") is False:
            add("WARN", f"{key}:missing",
                f"Backup '{b['name']}' on {b['host']}: {b.get('note') or 'no artifact found'}. "
                "Vault checks that a backup exists and looks plausible; it does not take one.")
            continue
        if b.get("late"):
            add("WARN", f"{key}:late",
                f"Backup '{b['name']}' on {b['host']} is {b['age_hours']:.0f}h old, and runs "
                f"every {b['every_hours']}h. The newest copy is from {b.get('newest')}.")
        if b.get("shrank"):
            add("WARN", f"{key}:shrank",
                f"Backup '{b['name']}' on {b['host']} dropped from {b['previous_bytes']:,} to "
                f"{b['bytes']:,} bytes. A backup that lands on time at a fraction of its usual "
                "size is the classic silent failure — check it contains what you expect.")
        if b.get("too_small"):
            add("WARN", f"{key}:small",
                f"Backup '{b['name']}' on {b['host']} is {b['bytes']:,} bytes, below the floor "
                "you configured for it.")
        if b.get("destination_full"):
            add("WARN", f"{key}:full",
                f"Backup destination for '{b['name']}' on {b['host']} has "
                f"{b.get('free_pct')}% free. The next run is what fails.")

    for h in res["ssh"]:
        n = h["name"]
        if not h["reachable"]:
            add("WARN" if h["optional"] else "CRIT", f"ssh:{n}:down", f"{n} unreachable over SSH ({_hz(h.get('error',''))})"); continue
        if h["disk_pct"] >= th["disk_pct_crit"]: add("CRIT", f"disk:{n}", f"{n} disk {h['disk_pct']}% full")
        elif h["disk_pct"] >= th["disk_pct_warn"]: add("WARN", f"disk:{n}", f"{n} disk {h['disk_pct']}%")
        if h["mem_pct"] >= cfg_host(cfg, n).get("mem_pct_warn", th["mem_pct_warn"]): add("WARN", f"mem:{n}", f"{n} memory {h['mem_pct']}%")
        if h["load_per_core"] >= th["load_per_core_warn"]: add("WARN", f"load:{n}", f"{n} load {h['load1']} ({h['load_per_core']}/core)")
        if th["require_fail2ban"] and h.get("fail2ban") != "active":
            add("WARN", f"fail2ban:{n}", f"{n}: fail2ban {h.get('fail2ban')} while taking {h['failed_ssh_24h']} failed SSH logins/24h")
        if h["failed_ssh_24h"] >= th["failed_ssh_24h_warn"]: add("WARN", f"bruteforce:{n}", f"{n}: {h['failed_ssh_24h']} failed SSH logins in 24h (spike)")
        if h["reboot_required"]: add("INFO", f"reboot:{n}", f"{n} needs a reboot (kernel update)")
        for c, st in [d.split(":", 1) for d in h["docker"] if ":" in d]:
            if "unhealthy" in st or "Restarting" in st: add("CRIT", f"docker:{n}:{c}", f"{n}: container {c} is {st}")
        for exp in (cfg_host(cfg, n).get("expect_docker") or []):
            if not any(d.startswith(exp + ":") and "Up" in d for d in h["docker"]): add("CRIT", f"docker:{n}:{exp}", f"{n}: container {exp} not running")
        for svc, st in h["services"].items():
            if st != "active": add("CRIT", f"svc:{n}:{svc}", f"{n}: service {svc} is {st}")
        sec = h.get("sec") or {}; base = (state.get("sec") or {}).get(n)
        if sec:
            if sec.get("miners"): add("CRIT", f"sec:miner:{n}", f"{n}: crypto-miner/malware process running: {', '.join(sec['miners'])} — killing it")
            if sec.get("preload"): add("CRIT", f"sec:preload:{n}", f"{n}: /etc/ld.so.preload is set (rootkit technique) — inspect NOW")
            if base is not None:
                if sec.get("authorized_keys") != base.get("authorized_keys"): add("CRIT", f"sec:authkeys:{n}", f"{n}: root authorized_keys changed {base.get('authorized_keys')} -> {sec.get('authorized_keys')} (backdoor key?)")
                if sec.get("login_users") != base.get("login_users"): add("CRIT", f"sec:users:{n}", f"{n}: login-capable users changed {base.get('login_users')} -> {sec.get('login_users')}")
                if sec.get("cron_net") != base.get("cron_net"): add("WARN", f"sec:cron:{n}", f"{n}: cron jobs using curl/wget changed: {sec.get('cron_net')}")
                for ip in sec.get("login_sources", []):
                    if ip not in base.get("login_sources", []) and ip not in ("", res.get("wan_ip")) and not ip.startswith(("10.", "192.168.")):
                        add("WARN", f"sec:login:{n}:{ip}", f"{n}: SSH login from a NEW source {ip} — was that you?")
            if sec.get("ssh_password"): add("WARN", f"sec:sshpw:{n}", f"{n}: SSH password login is enabled (root={sec.get('ssh_root')}) — brute-force target; switching to key-only automatically")
            if sec.get("ufw") != "active": add("WARN", f"sec:ufw:{n}", f"{n}: firewall (ufw) is {sec.get('ufw')} — enabling automatically (22 + listening ports only)")
            if (h.get("updates") or {}).get("count") and not sec.get("security_updates"): add("INFO", f"upd:pending:{n}", f"{n}: {h['updates']['count']} package updates pending — installing automatically" + (" tonight (touches Docker)" if any(re.match(r"^(docker|containerd)", p) for p in h['updates'].get('pending', [])) else ""))
            if sec.get("security_updates"):
                # A box with unattended-upgrades enabled installs these on its
                # own (usually within hours) -- so "pending" is an advisory,
                # not a problem to nag about or tell the owner to run by hand.
                # It stays INFO (in-app only, per the push policy) there, and
                # is a WARN only where nothing will apply it automatically.
                _auto = bool(sec.get("unattended"))
                _pk = ', '.join(sec['security_updates'][:4]) + ('…' if len(sec['security_updates']) > 4 else '')
                add("INFO" if _auto else "WARN", f"sec:secupd:{n}",
                    f"{n}: {len(sec['security_updates'])} security updates pending ({_pk})"
                    + (" — installing automatically" if _auto else " — automatic updates are off, apply them"))
            if not sec.get("unattended"): add("INFO", f"sec:unatt:{n}", f"{n}: automatic security updates not enabled")
            if sec.get("tmp_exec"): add("INFO", f"sec:tmpexec:{n}", f"{n}: executable files in /tmp: {', '.join(sec['tmp_exec'][:3])}")
            for ip, c in (sec.get("top_attackers") or {}).items():
                if c >= th.get("attacker_block_24h", 300): add("WARN", f"sec:attacker:{n}:{ip}", f"{n}: {ip} made {c} failed SSH logins in 24h")
        bd = (base or {}).get("drift") or {}
        for what, hv in (h.get("drift") or {}).items():
            if bd.get(what) and hv and hv != bd[what]: add("WARN", f"sec:drift:{n}:{what}", f"{n}: {what} changed since last scan (config drift) — was it you or Vault?")
        prevh = (state.get("ssh") or {}).get(n, {})
        if prevh.get("uptime") and h["uptime"] and _uptime_reset(prevh["uptime"], h["uptime"]):
            add("WARN", f"rebooted:{n}", f"{n} rebooted (uptime {prevh['uptime']} -> {h['uptime']})")
        if prevh.get("ports") is not None and h.get("ports") is not None:
            opened = sorted(set(h["ports"]) - set(prevh["ports"])); closed = sorted(set(prevh["ports"]) - set(h["ports"]))
            if opened: add("WARN", f"ports:{n}:open:{','.join(map(str, opened))}", f"{n}: NEW listening port(s) {opened} (was not there last run)")
            if closed: add("INFO", f"ports:{n}:closed:{','.join(map(str, closed))}", f"{n}: port(s) {closed} stopped listening")
    _lateral_alerts(res, cfg, state, add)
    if res.get("wan_ip") and state.get("wan_ip") and res["wan_ip"] != state["wan_ip"]:
        add("INFO", "wan:changed", f"Home WAN IP changed {state['wan_ip']} -> {res['wan_ip']}")
    # The pinned public address, per network. A sensor measures the egress
    # address of the network its OWN default route uses, so the pin is only
    # checked against that network -- claiming a second network's egress
    # matched would be a fact nobody collected.
    if res.get("wan_ip"):
        _dgw = (res.get("rogue") or {}).get("default_gateway")
        for _n in alert_networks(res, cfg):
            _exp = _n.get("expected_wan_ip")
            if not _exp:
                continue
            if _dgw and _n.get("expected_gateway") and not _dgw.startswith("link#") and _dgw != _n["expected_gateway"]:
                continue          # not the network this sensor is leaving through
            if not _dgw and not _n.get("primary", True):
                continue
            if res["wan_ip"] != _exp:
                add("WARN", f"wan:{_n['slug']}:unexpected",
                    f"{_n.get('name') or 'This network'} is reaching the internet through {res['wan_ip']}, not the "
                    f"expected {_exp} — either your ISP gave you a new address (update the pinned address if so) "
                    f"or this network's traffic is taking a different way out.")
    d = res.get("dns")
    if d:
        if not d["system"]["ok"] and (d["cloudflare"]["ok"] or d["google"]["ok"]):
            add("CRIT", "dns:system", "System DNS resolver is failing (public resolvers work) — router/ISP DNS problem")
        elif not any(x["ok"] for x in d.values()):
            add("CRIT", "dns:all", "All DNS lookups failing — internet or Wi-Fi down?")
        elif d["system"]["ms"] > 1500:
            add("WARN", "dns:slow", f"System DNS slow: {d['system']['ms']} ms")
    sp = res.get("speed")
    if sp and not sp.get("error"):
        base = (state.get("speed_baseline") or {}).get("down_mbps")
        if base and sp["down_mbps"] < base * 0.4:
            add("WARN", "speed:slow", f"Download {sp['down_mbps']} Mbps — well below usual {base} Mbps")
    for _net in alert_networks(res, cfg):
        n0 = len(A)
        _lan_alerts(res, cfg, state, _net, add)
        for _a in A[n0:]:
            _a["net"] = _net["slug"]
    for dom, dv in (res.get("domains") or {}).items():
        dl = dv.get("days_left")
        if dl is not None:
            if dl <= 7: add("CRIT", f"domain:{dom}", f"Domain {dom} EXPIRES in {dl} days ({dv.get('expires')}) — renew now")
            elif dl <= 30: add("WARN", f"domain:{dom}", f"Domain {dom} expires in {dl} days ({dv.get('expires')})")
    wp = res.get("wan_path") or {}
    if wp:
        if all(v.get("loss_pct", 100) >= 100 for v in wp.values()): add("CRIT", "wan:path", "Internet line down: 100% packet loss to every anchor")
        else:
            for name, v in wp.items():
                if v.get("loss_pct", 0) >= th.get("loss_pct_warn", 20): add("WARN", f"wan:loss:{name}", f"Packet loss {v['loss_pct']}% to {name} ({v['ip']})")
                elif v.get("avg_ms") and v["avg_ms"] > th.get("path_ms_warn", 120): add("WARN", f"wan:slow:{name}", f"Latency {v['avg_ms']} ms to {name} (jitter {v.get('jitter_ms')} ms)")
    rg = res.get("rogue") or {}
    if rg.get("on_home_ssid") and rg.get("dhcp_server") and rg["dhcp_server"] != cfg["lan"]["expected_gateway"]:
        add("CRIT", "home:rogue-dhcp", f"DHCP lease came from {rg['dhcp_server']}, not the router {cfg['lan']['expected_gateway']} — rogue DHCP server on the Wi-Fi")
    if rg.get("on_home_ssid") and rg.get("dhcp_router") and rg["dhcp_router"] != cfg["lan"]["expected_gateway"]:
        add("CRIT", "home:rogue-uplink", f"Your DHCP lease names {rg['dhcp_router']} as the way out to the internet, not the router {cfg['lan']['expected_gateway']} — a device on the network is advertising its own uplink, and anything that accepted that lease is sending its traffic through it")
    if rg.get("on_home_ssid") and rg.get("default_gateway") and rg["default_gateway"] != cfg["lan"]["expected_gateway"] and not rg["default_gateway"].startswith("link#"):
        add("CRIT", "home:route", f"Default route on the home Wi-Fi is {rg['default_gateway']} instead of the router — traffic may be hijacked")
    # The same three checks for any OTHER network this sensor produced verified
    # Wi-Fi facts for (`res["rogues"][slug]`). An unverified network gets
    # nothing here rather than a false clean bill of health.
    for _n in alert_networks(res, cfg):
        if _n.get("primary", True):
            continue
        _rg = (res.get("rogues") or {}).get(_n.get("slug")) or {}
        if not _rg or _rg.get("verified") is False:
            continue
        _gw = _n.get("expected_gateway")
        _slug = _n["slug"]
        if _rg.get("dhcp_server") and _gw and _rg["dhcp_server"] != _gw:
            add("CRIT", f"home:rogue-dhcp:{_slug}", f"{_n.get('name')}: the DHCP lease came from {_rg['dhcp_server']}, not the router {_gw} — rogue DHCP server on that network")
        if _rg.get("dhcp_router") and _gw and _rg["dhcp_router"] != _gw:
            add("CRIT", f"home:rogue-uplink:{_slug}", f"{_n.get('name')}: the DHCP lease names {_rg['dhcp_router']} as the way out to the internet, not the router {_gw} — a device there is advertising its own uplink")
        if _rg.get("default_gateway") and _gw and _rg["default_gateway"] != _gw and not str(_rg["default_gateway"]).startswith("link#"):
            add("CRIT", f"home:route:{_slug}", f"{_n.get('name')}: the default route is {_rg['default_gateway']} instead of the router {_gw} — traffic may be hijacked")
    # Evil twin, on the only evidence that actually separates one from your
    # own equipment: the encryption it advertises.
    #
    # This used to fire on "your SSID appeared on a channel it was not on
    # before", which describes every dual-band router (same name on 2.4GHz and
    # 5GHz) and every mesh extender, and fires again each time the router picks
    # a new channel by itself -- which they all do. On the owner's own network
    # it flagged his Verizon router plus two of his own extenders, repeatedly.
    # An alert that cries wolf on the normal case trains people to ignore it,
    # which costs more than the detection was ever worth.
    #
    # A real evil twin has to offer weaker encryption than the real network,
    # or none, or the client will not talk to it. So: same name, DIFFERENT
    # security from the network we are actually on. A twin matching our own
    # security is our own hardware, and we say nothing.
    mine_sec = (rg.get("security") or "").strip().lower()
    twin_secs = [x for x in (rg.get("twin_security") or []) if x and x.strip().lower() != mine_sec]
    if mine_sec and twin_secs:
        add("CRIT", "home:evil-twin",
            f"Another access point is broadcasting your Wi-Fi name with different encryption "
            f"({', '.join(twin_secs)}, yours is {rg.get('security')}) — that is what an evil twin "
            f"looks like. Do not connect to it, and change your Wi-Fi password.")
    for sname, ap in (res.get("admin_probe") or {}).items():
        for path, sev in (ap.get("hits") or {}).items(): add(sev, f"sec:panel:{sname}:{path}", f"{sname}: {path} is reachable from the internet" + (" — LEAKED SECRETS, fix now" if sev == "CRIT" else ""))
    for dom, es in (res.get("email_sec") or {}).items():
        sevm = "WARN" if es.get("has_mx") else "INFO"; tail = "" if es.get("has_mx") else " (domain sends no mail: publish 'v=spf1 -all' and DMARC p=reject so nobody can spoof it)"
        if not es.get("spf"): add(sevm, f"email:spf:{dom}", f"{dom}: no SPF record — anyone can spoof mail from this domain" + tail)
        if not es.get("dmarc"): add(sevm, f"email:dmarc:{dom}", f"{dom}: no DMARC record — spoofed mail is not rejected" + tail)
        elif es.get("dmarc_policy") == "none": add("INFO", f"email:dmarc-none:{dom}", f"{dom}: DMARC policy is p=none (monitor only); move to quarantine or reject")
    for host, ex in (res.get("external_sites") or {}).items():
        allowed = {22, 80, 443} | set((cfg.get("home") or {}).get("site_allowed_ports", []))
        extra = [p for p in (ex.get("open_ports") or []) if p not in allowed]
        if extra: add("WARN", f"sec:extport:{host}:{','.join(map(str, extra))}", f"{host} exposes port(s) {extra} to the internet (only 22/80/443 expected)")
    for c in res.get("checks") or []:
        if not c["ok"]: add("CRIT", f"check:{c['name']}", f"Check '{c['name']}' ({c['type']} {c['target']}{':'+str(c['port']) if c.get('port') else ''}) FAILED {c.get('detail','')}".strip())
    for h in res["ssh"]:
        if h["reachable"] and h.get("cve_fixable", -1) > 0: add("INFO", f"sec:cve:{h['name']}", f"{h['name']}: {h['cve_fixable']} known vulnerabilities with a fix available (debsecan) — the daily upgrade clears them")
    if res.get("self", {}).get("env_age_days", 0) > 90: add("INFO", "sec:env-age", f"Vault secrets (.env) are {res['self']['env_age_days']} days old — rotate the API keys and ntfy topic")
    d = res.get("dns_filter") or {}
    if d.get("enabled") and not d.get("running"): add("WARN", "dns:filter-down", "DNS filter is enabled but not answering — devices set to use it will lose name resolution; the launchd job should restart it")
    for b in d.get("beaconing") or []:
        add("WARN", f"dns:beacon:{b['ip']}:{b['domain']}", f"{b['client']} contacts {b['domain']} every ~{b['every_s']}s ({b['count']}x today) — looks like automated beaconing; is this device meant to phone home?")
    for s in d.get("suspicious") or []:
        add("INFO", f"dns:dga:{s['domain']}", f"Odd domain looked up {s['count']}x today: {s['domain']} (random-looking name, sometimes malware)")
    # A volume anomaly, never framed as exfiltration: Vault reads domain
    # names and counts, not payloads, so it cannot tell a busy backup job from
    # data leaving the network. What it CAN say honestly is that one device is
    # making far more DNS lookups than its own history, and that is exactly
    # what this says -- no verdict about what the traffic is. `n < 5` is the
    # warm-up gate (mirrors `unusual_hours`'s 5-day minimum): a device needs a
    # real history before "unusual" means anything. The 6x-and-a-floor-of-200
    # test keeps a device that went from 3 lookups to 20 (a 6x jump on nothing)
    # from ever qualifying.
    vol_base = state.get("dns_volume_baseline") or {}
    for mac, total in (d.get("client_totals_by_mac") or {}).items():
        base = vol_base.get(mac) or {}
        avg = base.get("avg") or 0
        if base.get("n", 0) < 5 or avg <= 0:
            continue
        if total >= max(200, avg * 6):
            who = (d.get("client_names_by_mac") or {}).get(mac, mac)
            add("WARN", f"dns:volume:{mac}",
                f"{who} looked up domain names {total} times in the last 24 hours, well above its usual "
                f"{int(avg)}. I cannot see what is inside that traffic, so this only says the device is "
                f"unusually busy, not what it is doing. Worth a look if you do not expect it to be busy right now.")
    for name, hb in (res.get("heartbeats") or {}).items():
        if hb.get("late") and hb.get("last"): add("WARN", f"hb:{name}", f"Heartbeat '{name}' missing — expected every {hb['every_min']} min, last {hb['last'][:16].replace('T', ' ')}")
        # The command that wires this job up carries the heartbeat's own
        # token, and a token is a WRITE credential (whoever holds it can
        # claim the backup ran). An alert message is not a private place --
        # it goes to push, to Discord/Telegram/PagerDuty/webhooks, into
        # built reports, and reports are shareable over unauthenticated
        # /r/<token> links. So the alert says what is wrong and where the
        # command lives; Security -> Heartbeats renders it for a signed-in
        # admin, on the app's own origin, from the configuration the hub
        # already holds.
        elif not hb.get("last"): add("INFO", f"hb:{name}:setup", f"Heartbeat '{name}' never pinged yet — nothing has checked in on it. Open Security → Heartbeats in Vault and press “Setup command” for the line to add to that job (it carries this heartbeat's secret, so only a signed-in admin is shown it)")
    # The host's own defences. Only a protection Vault positively
    # observed to be OFF is raised: `None` means the check could not run,
    # and reporting an unknown as a problem is how a security panel teaches
    # its owner to ignore it. WARN, not CRIT -- the machine is weakened, not
    # breached.
    _ep = res.get("endpoint") or {}
    for _k, _on in (_ep.get("checks") or {}).items():
        if _on is False:
            _label, _buys = endpoint_labels.get(_k, (_k, ""))
            add("WARN", f"sec:endpoint:{_k}",
                f"{_label} is off on {_ep.get('platform', 'this machine')} — {_buys}")
    # A startup item running something out of /tmp is the commonest way
    # commodity malware survives a reboot, and unlike "is it signed" it is
    # cheap to check and hard to argue with.
    for _it in (_ep.get("suspicious_launch_items") or [])[:5]:
        add("CRIT", f"sec:launch-item:{_it['item']}",
            f"Startup item {_it['item']} {_it['why']} — this is how malware survives a reboot")

    if res.get("self", {}).get("env_perms_bad"): add("INFO", "sec:env-perms", f"Vault .env is readable by other users on this Mac — run: chmod 600 {paths.p('.env')}")
    if res.get("fix_cap_hit"): add("INFO", "fix:cap", f"Auto-fix daily cap reached ({(cfg.get('auto_fix') or {}).get('max_per_day', 30)}) — remaining fixes wait for tomorrow or a dashboard click")
    home = res.get("home")
    if home:
        w = home.get("wifi")
        if w and w.get("security") not in STRONG_WIFI: add("CRIT", "home:wifi:weak", f"Wi-Fi '{w.get('ssid')}' uses {w.get('security')} — anyone can join/sniff; switch the router to WPA2/WPA3 Personal")
        ex = home.get("exposure") or {}
        if ex.get("open_ports"): add("CRIT", "home:exposed:" + ",".join(map(str, ex["open_ports"])), f"Your home is reachable from the internet on port(s) {ex['open_ports']} (WAN {ex.get('wan_ip')}) — close port forwarding/UPnP on the router")
        # A camera reachable from the internet is the classic case, so it is
        # called out separately from the generic exposure alert above: the
        # ports the outside world answered on that a camera on this LAN also
        # serves.
        _cam_ports = sorted({p for p in (ex.get("open_ports") or []) if p in cameras.CAMERA_PORTS})
        _cam_names = []
        for _n in alert_networks(res, cfg):
            for _mac, _info in ((_n.get("cameras") or {}).get("detail") or {}).items():
                if {int(p) for p in (((net_state(state, _n).get("known_macs") or {}).get(_mac) or {}).get("ports") or {}) if str(p).isdigit()} & set(_cam_ports):
                    _cam_names.append(_info.get("label") or _info.get("name") or _info.get("vendor") or _mac)
        # Only fire on evidence: a camera on this LAN actually serves one of the
        # exposed ports, or the port is one of the strong camera ports. Without
        # that gate a forwarded 8000 -- a dev server, an admin UI -- would tell
        # the owner strangers can watch his house.
        _strong_exposed = [p for p in _cam_ports if p in cameras.STRONG_PORTS]
        if _cam_ports and (_cam_names or _strong_exposed):
            add("CRIT", "home:camera-exposed:" + ",".join(map(str, _cam_ports)),
                f"Camera port(s) {_cam_ports} answer from the public internet on {ex.get('wan_ip') or 'your WAN address'}"
                + (f" — {', '.join(sorted(set(_cam_names))[:4])} can be reached by anyone" if _cam_names else " — a camera in your home can be reached by anyone")
                + ". Remove that port forward / turn off UPnP on the router and use a VPN instead.")
        for pt in home.get("gateway_ports") or []:
            if pt in (21, 23, 7547): add("WARN", f"home:gw:{pt}", f"Router {home['gateway']} has port {pt} open on the LAN ({'telnet' if pt==23 else 'ftp' if pt==21 else 'TR-069'})")
        if home.get("unverified"): add("WARN", "lan:unverified", f"{len(home['unverified'])} device(s) on the Wi-Fi are not verified (no label, not trusted): " + ", ".join((d.get("name") or d.get("vendor") or d["mac"]) + f" {d.get('ip')}" for d in home["unverified"][:5]))
    ts = res.get("tailscale")
    if ts is not None:
        if not ts.get("running"): add("INFO", "tailscale:stopped", f"Tailscale on this Mac is not running ({ts.get('state') or ts.get('error')})")
        else:
            for p in cfg["tailscale"].get("expect_peers", []):
                pe = ts["peers"].get(p)
                if not pe: add("WARN", f"ts:peer:{p}", f"Tailscale peer {p} missing")
                elif not pe.get("online"): add("WARN", f"ts:peer:{p}", f"Tailscale peer {p} offline")
    _nvr_alerts(res, A)
    A.extend(evaluate_capture(res))
    CONF = {"site:": 90, "cert:": 99, "dns:": 85, "ssh:": 90, "disk:": 99, "mem:": 95, "load:": 80, "fail2ban:": 95, "bruteforce:": 90, "reboot:": 99, "docker:": 95, "svc:": 95, "rebooted:": 90, "ports:": 85,
            "wan:": 80, "speed:": 60, "lan:new:": 70, "lan:rotated:": 75, "lan:camera-new:": 85, "lan:camera-gone:": 85, "lan:offsubnet:": 75, "lan:second-router:": 60, "lan:unverified": 65, "lan:port:": 80, "lan:offline:": 85, "lan:flapping:": 70, "lan:unusual-hour:": 45,
            "sec:miner:": 90, "sec:preload:": 95, "sec:authkeys:": 90, "sec:users:": 90, "sec:cron:": 60, "sec:login:": 70, "sec:sshpw:": 99, "sec:ufw:": 99, "sec:secupd:": 99, "sec:unatt:": 99, "sec:tmpexec:": 40,
            "sec:attacker:": 95, "sec:drift:": 75, "sec:panel:": 85, "sec:extport:": 90, "sec:cve:": 90, "sec:env": 90, "email:": 95, "home:wifi": 95, "home:exposed": 95, "home:camera-exposed": 95, "home:rogue-uplink": 90, "home:gw": 80, "home:rogue": 85, "home:route": 85, "home:evil-twin": 55,
            "hb:": 80, "domain:": 99, "check:": 90, "upd:": 99, "fix:": 99, "dns:filter-down": 95, "dns:beacon:": 70, "dns:dga:": 45, "dns:volume:": 50,
            "sec:lanlogin:": 75,
            "nvr:": 95}
    for a in A:
        a["confidence"] = next((v for k, v in CONF.items() if a["key"].startswith(k)), 70)
    order = {"CRIT": 0, "WARN": 1, "INFO": 2}
    return sorted(A, key=lambda a: order[a["sev"]])

# ---------------- security score, incidents, recommendations ----------------

#: The rubric this file implements, stamped onto every score it returns.
#:
#: A score is a number produced by a particular set of weights and caps, and
#: two scores computed under different weights are not comparable even though
#: they are drawn on the same trend line. The hub stores `Scan.raw["score"]`
#: once per sweep and never rewrites it, so a stamp taken here freezes with
#: the row and an old number keeps the meaning it had when it was measured.
#:
#: BUMP THIS whenever a weight, a cap, a category or a grade band below
#: changes. Leaving it alone after changing a weight is the failure it exists
#: to prevent: history silently reshaped to a rubric that did not exist yet.
SCORE_MODEL = "2026.10"

#: Below this much coverage the score is withheld entirely rather than
#: published (trust plan §2.5C, "unknown must never render as green").
#:
#: The three host-derived categories -- Vulnerabilities, Configuration and
#: Patch status -- are worth 35 of the 100 points between them, and every
#: one of them is vacuously perfect for a host that did not answer. So a
#: mostly-silent estate does not merely produce a vaguer score, it produces
#: a HIGHER one than the same estate in good health. There is no honest way
#: to publish that number, and the only safe thing it can say is nothing.
#:
#: 80 is the plan's own figure and is deliberately strict: it will sometimes
#: make Vault look worse than a competitor that scores a blind network
#: 100, which is exactly the trade being made. The deductions Vault DID
#: observe are still reported alongside, so withholding the total never
#: means withholding the findings.
SCORE_COVERAGE_FLOOR = 80


def _coverage(res):
    """How many of the inputs the score wants an answer from actually gave one.

    This exists because of an asymmetry in the deductions below: the
    Vulnerabilities, Configuration and Patch-status categories are all built
    from `[h for h in res["ssh"] if h.get("reachable")]`. A host that stops
    answering does not score badly -- it stops being counted at all, and the
    deductions it was contributing simply vanish. The score goes UP when
    Vault goes blind, and nothing on the face of the number says so.

    Availability catches part of it (an unreachable non-optional host deducts
    there), but that is a different fact: "this host is down" is not "this
    host's firewall, SSH policy, pending updates and CVE count are all
    unknown". A host marked `optional` is allowed to be off and raises no
    availability deduction at all, so for that host the blindness is
    currently invisible from every angle.

    Hosts are the whole measure here on purpose. A site that fails its probe
    is *measured* -- "down" is an answer -- and cameras and recorders already
    deduct explicitly under Cameras when they cannot be read. The unmeasured
    surface the score actually hides is the SSH estate.

    An estate with no hosts configured is 100%, not 0%: a home account that
    never added a server is fully measured, and dividing by zero to print a
    frightening "0% coverage" on it would be a false alarm of exactly the
    kind this function exists to prevent.
    """
    ssh = res.get("ssh") or []
    applicable = len(ssh)
    measured = sum(1 for h in ssh if h.get("reachable"))
    pct = 100 if not applicable else int(100 * measured / applicable)
    return {"measured": measured, "applicable": applicable, "pct": pct}


def security_score(res, cfg, state):
    """0-100 with named deductions and the score reachable by following the recommendations.

    Also carries `model` (the rubric, see `SCORE_MODEL`) and `coverage` (how
    much of the estate answered, see `_coverage`), so a reader can tell a
    good score apart from a score computed over the half of the network that
    was still talking.
    """
    ded = []; th = cfg["thresholds"]; hosts = [h for h in res["ssh"] if h.get("reachable")]; al = res.get("alerts") or []
    def d(cat, pts, why):
        if pts > 0: ded.append({"category": cat, "points": int(pts), "why": why})
    crit = [a for a in al if a["sev"] == "CRIT"]
    d("Active threats", min(30, 15 * len(crit)), f"{len(crit)} critical alert(s) open" if crit else "")
    # During an uplink blackout the sites were not measured, so they are
    # UNKNOWN rather than down -- deducting Availability for them would move
    # the customer's security score because Vault's own ISP had a bad
    # minute. Hosts are still counted: those are reached over the LAN.
    _blackout = uplink_blackout(res)
    down = [h["name"] for h in res["ssh"] if not h.get("reachable") and not h.get("optional")] + \
           ([] if _blackout else [s["name"] for s in res["sites"] if not s["ok"]])
    d("Availability", min(20, 10 * len(down)), f"down: {', '.join(down)}" if down else "")
    secupd = sum(len((h.get("sec") or {}).get("security_updates") or []) for h in hosts); cves = sum(max(0, h.get("cve_fixable", 0)) for h in hosts)
    d("Vulnerabilities", min(15, (3 if secupd else 0) + min(12, cves // 25)), f"{secupd} security updates and {cves} fixable CVEs pending (auto-patched daily)" if (secupd or cves) else "")
    weak = [h["name"] for h in hosts if (h.get("sec") or {}).get("ssh_password") or (h.get("sec") or {}).get("ufw") != "active" or h.get("fail2ban") != "active" or not (h.get("sec") or {}).get("unattended")]
    d("Configuration", min(15, 5 * len(weak)), f"hardening gaps on {', '.join(weak)}" if weak else "")
    home = res.get("home") or {}; ex = (home.get("exposure") or {}).get("open_ports") or []; extp = [h for h, v in (res.get("external_sites") or {}).items() if [p for p in (v.get("open_ports") or []) if p not in (22, 80, 443)]]
    risky = [a for a in al if canonical_lan_key(a["key"]).startswith("lan:port:")]
    d("Exposure", min(15, 10 * bool(ex) + 3 * len(extp) + 2 * len(risky)), "; ".join(x for x in [f"home ports open {ex}" if ex else "", f"extra internet ports on {', '.join(extp)}" if extp else "", f"{len(risky)} risky LAN service(s)" if risky else ""] if x))
    unv = home.get("unverified") or []
    d("Coverage", min(10, 3 * len(unv)), f"{len(unv)} unverified device(s) on the Wi-Fi" if unv else "")
    # Cameras: one the owner installed and that has stopped answering is a
    # coverage hole, and a camera answering from the internet is exposure.
    cams_gone = [a for a in al if canonical_lan_key(a["key"]).startswith("lan:camera-gone:")]
    cam_exposed = [a for a in al if a["key"].startswith("home:camera-exposed")]
    # Task 73: cameras behind a recorder count exactly the same. A channel
    # that stopped answering or vanished is a blind spot; a recorder that
    # cannot be read at all is EVERY camera behind it unaccounted for, which
    # is why it is weighted like several.
    nvr_gone = [a for a in al if a["key"].startswith(("nvr:cam-offline:", "nvr:cam-gone:"))]
    nvr_blind = [a for a in al if a["key"].startswith(("nvr:http-off:", "nvr:down:", "nvr:auth:"))]
    d("Cameras", min(12, 4 * (len(cams_gone) + len(nvr_gone)) + 8 * bool(cam_exposed) + 6 * len(nvr_blind)),
      "; ".join(x for x in [f"{len(cams_gone) + len(nvr_gone)} camera(s) not answering" if (cams_gone or nvr_gone) else "",
                            f"{len(nvr_blind)} recorder(s) cannot be read" if nvr_blind else "",
                            "a camera answers from the internet" if cam_exposed else ""] if x))
    beac = (res.get("dns_filter") or {}).get("beaconing") or []
    d("Behaviour", min(8, 4 * len(beac)), f"{len(beac)} device(s) beaconing to an external domain" if beac else "")
    rb = [h["name"] for h in hosts if h.get("reboot_required")]; pend = sum((h.get("updates") or {}).get("count", 0) for h in hosts)
    d("Patch status", min(5, 3 * len(rb) + (2 if pend > 50 else 0)), (f"reboot pending on {', '.join(rb)}; " if rb else "") + (f"{pend} packages pending" if pend > 50 else ""))
    w = (home.get("wifi") or {}).get("security"); attackers = [a for a in al if a["key"].startswith("sec:attacker:")]
    d("Identity & access", min(10, (10 if w and "WPA" not in w else 0) + min(6, 2 * len(attackers))), "; ".join(x for x in [f"weak Wi-Fi security {w}" if w and "WPA" not in w else "", f"{len(attackers)} active brute-force source(s)" if attackers else ""] if x))
    total = max(0, 100 - sum(x["points"] for x in ded))
    fixable = sum(x["points"] for x in ded if x["category"] in ("Vulnerabilities", "Configuration", "Coverage", "Patch status", "Exposure", "Cameras"))
    cov = _coverage(res)
    if cov["applicable"] and cov["pct"] < SCORE_COVERAGE_FLOOR:
        # Withheld, not zeroed and not estimated. A number here would be
        # higher than a healthy network's, because the categories the silent
        # hosts feed simply stop deducting. The findings that WERE observed
        # still travel, so this hides no evidence -- only the total that
        # cannot be stood behind.
        return {"total": None, "deductions": ded, "potential": None, "grade": "",
                "model": SCORE_MODEL, "coverage": cov}
    return {"total": total, "deductions": ded, "potential": min(100, total + fixable), "grade": "A" if total >= 90 else "B" if total >= 75 else "C" if total >= 60 else "D" if total >= 40 else "F", "model": SCORE_MODEL, "coverage": cov}

def recommendations(res, cfg, state):
    """Prioritised, explained, actionable. Powers the Recommendations tab and the report."""
    R = []; hosts = [h for h in res["ssh"] if h.get("reachable")]; home = res.get("home") or {}; ex = home.get("exposure") or {}; w = home.get("wifi") or {}
    def add(pri, text, why, action=None, key=None): R.append({"priority": pri, "text": text, "why": why, "action": action, "key": key or text[:40]})
    if ex.get("open_ports"): add("Critical", f"Close port forwarding / UPnP on the router for ports {ex['open_ports']}", "these ports answer from the public internet; anyone can reach a device in your home", None, "home:exposed")
    if w.get("security") and "WPA" not in w["security"]: add("Critical", "Switch the router to WPA2 or WPA3 Personal", f"the Wi-Fi is on {w['security']}, which can be joined or sniffed without the password", None, "home:wifi")
    for a in res.get("alerts") or []:
        if a["sev"] == "CRIT" and a.get("state") in (None, "open"): add("Critical", a["msg"], f"critical alert, confidence {a.get('confidence')}%", None, a["key"])
    for h in hosts:
        s = h.get("sec") or {}; n = h["name"]
        if h.get("reboot_required"): add("High", f"Reboot {n} to activate the updated kernel", "the running kernel is older than the installed one; security fixes are not active until reboot", {"host": n, "action": "reboot"}, f"reboot:{n}")
        if s.get("ssh_password"): add("High", f"{n}: switch SSH to key-only login", "password logins are what brute-force bots try all day", {"host": n, "action": "disable_ssh_password"}, f"sec:sshpw:{n}")
        if s.get("ufw") != "active": add("High", f"{n}: turn on the firewall", "only the ports you serve should be reachable", {"host": n, "action": "enable_ufw"}, f"sec:ufw:{n}")
        if h.get("fail2ban") != "active": add("High", f"{n}: enable fail2ban", "bans IPs after repeated failed logins", {"host": n, "action": "enable_fail2ban"}, f"fail2ban:{n}")
        if s.get("security_updates"): add("Medium", f"{n}: install {len(s['security_updates'])} security updates", "Vault does this automatically within a day; press to run now", {"host": n, "action": "security_updates", "arg": " ".join(p for p in s["security_updates"] if not re.match(r"^(docker|containerd|nginx$)", p))}, f"sec:secupd:{n}")
        if not s.get("unattended"): add("Medium", f"{n}: enable automatic security updates", "keeps the box patched without anyone remembering", {"host": n, "action": "enable_unattended"}, f"sec:unatt:{n}")
        if h.get("disk_pct", 0) >= 80: add("Medium", f"{n}: free disk space ({h['disk_pct']}% used)", "full disks crash databases and stop logging", {"host": n, "action": "cleanup_disk"}, f"disk:{n}")
        if h.get("cve_fixable", 0) > 50: add("Low", f"{n}: {h['cve_fixable']} known CVEs have fixes available", "the nightly full upgrade applies them; a reboot may follow", None, f"sec:cve:{n}")
    for a in res.get("alerts") or []:
        k = canonical_lan_key(a["key"])
        # `home:camera-exposed` is a CRIT, so the generic critical-alert loop
        # above already recommends it -- adding it again would put two rows
        # under one key. Only the WARN-level findings need a line here.
        if k.startswith("lan:camera-gone:") and a.get("state") in (None, "open"):
            add("High", a["msg"], "a camera that stops answering leaves a blind spot, and a cut cable or pulled plug looks exactly like this", None, a["key"])
        elif k.startswith("nvr:") and a.get("state") in (None, "open") and a["sev"] != "INFO":
            add("High", a["msg"],
                "cameras behind a recorder are only as watched as the recorder is readable — a channel that "
                "went quiet, or a recorder Vault cannot log into, is a blind spot you cannot see from the app",
                None, a["key"])
        elif k.startswith("lan:offsubnet:") and a.get("state") in (None, "open"):
            add("High", a["msg"], "a device that did not take an address from your router is either configured by hand or not part of this network at all", None, a["key"])
        elif k.startswith("lan:second-router:") and a.get("state") in (None, "open"):
            add("High", a["msg"], "a second router or DNS server can read and redirect everything the devices that use it do", None, a["key"])
    unv = home.get("unverified") or []
    if unv: add("High", f"Verify {len(unv)} unknown device(s) on the Wi-Fi", "press 'mine, trust' on the ones you own; block the rest in the router and change the Wi-Fi password", None, "lan:unverified")
    for a in res.get("alerts") or []:
        if canonical_lan_key(a["key"]).startswith("lan:port:") and a.get("state") in (None, "open"): add("Medium", a["msg"], "telnet, SMB, RDP, VNC or UPnP on an unknown device is how IoT gets taken over; trust it if it is yours, isolate it if not", None, a["key"])
    for host, v in (res.get("external_sites") or {}).items():
        extra = [p for p in (v.get("open_ports") or []) if p not in (22, 80, 443)]
        if extra: add("High", f"{host}: close internet-facing port(s) {extra}", "databases and admin services must not be reachable from the internet", None, f"sec:extport:{host}")
    for dom, es in (res.get("email_sec") or {}).items():
        if es.get("has_mx") and es.get("dmarc_policy") == "none": add("Low", f"{dom}: DMARC is in monitor mode (p=none)", "kept as a reported item by your choice; spoofed mail is reported, not rejected", None, f"email:dmarc-none:{dom}")
        if not es.get("has_mx") and not es.get("spf"): add("Low", f"{dom}: publish 'v=spf1 -all' and a DMARC reject record", "the domain sends no mail, so nobody should be able to send as it", None, f"email:spf:{dom}")
    for name, hb in (res.get("heartbeats") or {}).items():
        if hb.get("late") and hb.get("last"): add("Medium", f"Heartbeat '{name}' stopped arriving", "a backup or job that should check in has not", None, f"hb:{name}")
    if not any(r["key"].startswith("home:") for r in R):
        add("Low", "Use a guest Wi-Fi for visitors and smart-home gear", "isolates cheap IoT from your laptops and phones", None, "adv:guest")
        add("Low", "Change the router admin password from the default and disable remote administration", "the router is the front door", None, "adv:router-admin")
    acked = state.get("acked") or {}; dec = state.get("rec_decisions") or {}
    # "No thanks" (decision == "no") hides a recommendation, but not forever
    # sight-unseen: it comes back once the condition it warned about has
    # gone away and then happened again -- not on a timer, and not just
    # because the same still-true condition is still being regenerated
    # every scan. `live_keys` is this scan's fresh set of keys, i.e. exactly
    # the conditions that are true right now; a declined key missing from
    # it this scan means the condition cleared, so its dict is marked
    # "absent" to watch for a comeback. A declined key that IS in
    # `live_keys` and was previously marked absent has recurred: the old
    # decline is forgotten (popped, not just un-hidden) so the row returns
    # as a fresh, undecided recommendation rather than a "no" that silently
    # flipped back to open. One rule for all 17 families for free, because
    # each key already encodes the specific host/finding it is about (a
    # decline never crosses hosts) -- "disk:prod" existing only while prod
    # is actually over the threshold IS the disk case's recurrence rule,
    # "sec:cve:prod" existing only while its fixable count is over the
    # recommendation's own threshold is the CVE case's (a 51 -> 80 -> 60
    # wobble never leaves the key absent, so it is not treated as a fresh
    # occurrence -- only a drop to/under the threshold and back up is).
    live_keys = {r["key"] for r in R}
    recurred = []
    for key, d in dec.items():
        if d.get("decision") != "no":
            continue
        if key in live_keys:
            if d.get("absent"):
                recurred.append(key)
        else:
            d["absent"] = True
    for key in recurred:
        dec.pop(key, None)
    R = [r for r in R if (dec.get(r["key"]) or {}).get("decision") != "no"]  # "no" = hidden until it recurs
    for r in R:
        d = dec.get(r["key"]) or {}
        r["done"] = r["key"] in acked or d.get("decision") == "yes" and d.get("status") == "done"
        r["decision"] = d.get("decision"); r["status"] = d.get("status"); r["decided_at"] = d.get("ts")
    order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    return sorted(R, key=lambda r: (order[r["priority"]], r["done"]))

def correlate_incidents(res, state):
    """Turn related alerts into one incident per entity with a timeline and confidence, numbered stably."""
    al = [a for a in res.get("alerts") or [] if a["sev"] != "INFO"]
    def entity(a):
        k = a["key"]; parts = k.split(":")
        if k.startswith(("site:", "cert:", "dns:", "sec:tls:", "sec:redirect:", "sec:hsts:", "sec:panel:")): return "site:" + (parts[2] if k.startswith("sec:") else parts[1])
        if k.startswith(("lan:", "home:", "wan:", "speed:", "dns:system", "dns:all")): return "home"
        if k.startswith("sec:extport:"): return "site:" + parts[2]
        if k.startswith(("email:", "domain:")): return "domain:" + parts[-1]
        if k.startswith("hb:"): return "job:" + parts[1]
        if k.startswith("check:"): return "check:" + parts[1]
        if k.startswith("sec:") or k.startswith(("disk:", "mem:", "load:", "fail2ban:", "bruteforce:", "reboot:", "docker:", "svc:", "rebooted:", "ports:", "ssh:", "upd:")):
            return "host:" + (parts[2] if k.startswith("sec:") else parts[1])
        return "other"
    groups = {}
    for a in al: groups.setdefault(entity(a), []).append(a)
    inc_state = state.setdefault("incidents", {}); nxt = state.get("incident_seq", 100)
    out = []
    for ent, items in groups.items():
        if len(items) < 2 and not any(a["sev"] == "CRIT" for a in items): continue
        sev = "CRIT" if any(a["sev"] == "CRIT" for a in items) else "WARN"
        started = min(a.get("first_seen") or res["ts"] for a in items); key = f"{ent}|{started[:10]}"
        if key not in inc_state: nxt += 1; inc_state[key] = {"id": nxt, "opened": res["ts"]}
        keys = [a["key"] for a in items]; kinds = {k.split(":")[0] for k in keys}
        label = ent.split(":", 1)[-1] if ":" in ent else "home network"
        if any(k.startswith("sec:attacker:") for k in keys): title = f"Brute-force pressure on {label}"
        elif any(k.startswith(("sec:miner", "sec:preload", "sec:authkeys", "sec:users")) for k in keys): title = f"Possible compromise of {label}"
        elif any(k.startswith(("site:", "ssh:")) and k.endswith(":down") for k in keys): title = f"{label} is down"
        elif ent == "home" and any(k.startswith(("lan:new", "lan:unverified", "lan:port")) for k in keys): title = "Unknown or risky devices on the Wi-Fi"
        elif ent == "home": title = "Home network needs attention"
        elif "sec" in kinds: title = f"Security posture gaps on {label}"
        else: title = f"{len(items)} issues on {label}"
        conf = int(sum(a.get("confidence", 70) for a in items) / len(items))
        story = sorted(({"ts": a.get("first_seen") or res["ts"], "text": a["msg"], "sev": a["sev"]} for a in items), key=lambda x: x["ts"])
        acts = [r for r in (res.get("recommendations") or []) if r["key"] in keys or (ent == "home" and r["key"].startswith(("home:", "lan:")))][:4]
        out.append({"id": inc_state[key]["id"], "entity": ent, "title": title, "severity": sev, "confidence": conf, "alerts": keys, "started": started, "story": story, "actions": acts,
                    "state": "acked" if all(a.get("state") in ("acked", "snoozed") for a in items) else "open"})
    state["incident_seq"] = nxt
    cutoff = (dt.datetime.now().astimezone() - dt.timedelta(days=30)).isoformat(timespec="seconds")
    state["incidents"] = {k: v for k, v in inc_state.items() if v.get("opened", "") >= cutoff}
    return sorted(out, key=lambda i: ({"CRIT": 0, "WARN": 1}[i["severity"]], i["started"]))

def unusual_hours(res, state):
    """Behaviour baseline (light version): a device online at an hour it has
    never been online in the last 7 days.

    Per network: returns `[{"mac": …, "net": <network entry>}, …]`. The history
    line's `live_macs` is a flat list on a single-network sensor (unchanged)
    and `{slug: [macs]}` once a second network is configured — both shapes are
    read here, so the 7-day baseline survives the day a network is added.
    """
    hp = paths.p("history.jsonl")
    nets = [n for n in (res.get("networks") or ([res["lan"]] if res.get("lan") else []))
            if isinstance(n, dict) and n.get("devices") is not None]
    if not os.path.exists(hp) or not nets: return []
    prim = next((n.get("slug") for n in nets if n.get("primary", True)), nets[0].get("slug"))
    cut = (dt.datetime.now().astimezone() - dt.timedelta(days=7)).isoformat(timespec="seconds"); days = set(); seen = {}
    for l in open(hp):
        if l[8:33] < cut or '"live_macs"' not in l: continue
        try: h = json.loads(l)
        except Exception: continue
        live = h.get("live_macs")
        if not live: continue
        days.add(h["ts"][:10]); hr = int(h["ts"][11:13])
        per = live if isinstance(live, dict) else {prim: live}
        for slug, macs in per.items():
            for m in macs or []: seen.setdefault(slug, {}).setdefault(m, set()).add(hr)
    if len(days) < 5: return []
    now_hr = int(res["ts"][11:13]); out = []
    for net in nets:
        known = net_state(state, net).get("known_macs") or {}
        per = seen.get(net.get("slug")) or {}
        for m in {x for x in net["devices"].values() if x}:
            hrs = per.get(m)
            dev = known.get(m, {})
            # A device the owner marked trusted is his own phone or TV: it
            # being up at a new hour is his evening, not an event, and it was
            # most of the Slack feed (about 80 raise/resolve posts a day). An
            # unknown or untrusted device keeps the alert.
            if hrs and len(hrs) <= 12 and now_hr not in hrs and not dev.get("always_on") \
                    and not dev.get("trusted"):
                out.append({"mac": m, "net": net})
    return out
