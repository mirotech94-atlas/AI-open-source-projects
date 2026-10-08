"""Tests for the ported Nexus collectors.

Pure functions (no network/subprocess) get real assertions on Nexus
behaviour. Network-touching functions only get import/signature coverage
here — they are never exercised over the network in tests.
"""
import inspect

from guardian_sensor import checks, dnsstats, domains, hosts, home, lan, sites, util


def test_all_collector_modules_import():
    # Importing each module must not raise (no top-level network calls,
    # no reliance on notify() which isn't ported for this task).
    for mod in (sites, hosts, lan, home, domains, dnsstats, checks):
        assert mod is not None


def test_cfg_host_found():
    cfg = {"ssh_hosts": [{"name": "safehome", "target": "user@1.2.3.4"}]}
    assert util.cfg_host(cfg, "safehome") == {"name": "safehome", "target": "user@1.2.3.4"}


def test_cfg_host_not_found():
    cfg = {"ssh_hosts": [{"name": "safehome", "target": "user@1.2.3.4"}]}
    assert util.cfg_host(cfg, "missing") == {}


def test_presence_machine():
    d = {"first_seen": "2026-09-01T00:00:00-04:00", "last_seen": "2026-09-09T00:00:00-04:00", "presence": "ONLINE"}
    assert lan.presence_state(d, True, "2026-09-09T00:05:00-04:00", False, "2026-08-01T00:00:00-04:00") == "ONLINE"
    assert lan.presence_state(d, True, "2026-09-09T00:05:00-04:00", True, "2026-08-01T00:00:00-04:00") == "FLAPPING"


def test_presence_state_sleeping_vs_offline():
    now = "2026-09-09T12:00:00-04:00"
    # last seen 2h ago, not always_on -> SLEEPING (age_h < 24)
    d_recent = {"first_seen": "2026-08-01T00:00:00-04:00", "last_seen": "2026-09-09T10:00:00-04:00"}
    assert lan.presence_state(d_recent, False, now, False, "2026-07-01T00:00:00-04:00") == "SLEEPING"
    # last seen 40h ago -> OFFLINE (age_h > 24 but < 24*30)
    d_stale = {"first_seen": "2026-08-01T00:00:00-04:00", "last_seen": "2026-09-07T12:00:00-04:00"}
    assert lan.presence_state(d_stale, False, now, False, "2026-07-01T00:00:00-04:00") == "OFFLINE"
    # always_on device offline recently is still reported OFFLINE (not SLEEPING)
    d_always = {"first_seen": "2026-08-01T00:00:00-04:00", "last_seen": "2026-09-09T10:00:00-04:00", "always_on": True}
    assert lan.presence_state(d_always, False, now, False, "2026-07-01T00:00:00-04:00") == "OFFLINE"


def test_risk_score_untrusted_risky_port():
    assert lan.risk_score({"trusted": False, "ports": {"23": "x"}}, True) > lan.risk_score({"trusted": True, "ports": {}}, False)


def test_risk_score_quarantined_is_max():
    assert lan.risk_score({"trusted": True, "quarantined": True}, False) == 100


def test_risk_score_hard_cap_at_100():
    # unverified(40) + risky-port bonus(25) + min(20, 5*non_risky) + flapping(10) sums to 95 here
    # (the real Nexus formula cannot exceed 100 without quarantined; that path is covered separately).
    d = {"trusted": False, "flapping": True, "ports": {str(p): "x" for p in (21, 22, 23, 25, 80, 443, 445, 3389, 5900, 1900)}}
    assert lan.risk_score(d, True) == 95
    assert lan.risk_score(d, True) <= 100


def test_dga():
    # DGA_RE's leading [a-z]{2}? is a *lazy* exact-count quantifier (still exactly
    # 2 chars, must be letters) followed by 10+ alnum chars — a random-looking
    # long alnum label reads as DGA-ish; a short, real word does not.
    assert dnsstats.DGA_RE.match("ab7f9q2m4k8z1p3.xyz") and not dnsstats.DGA_RE.match("apple.com")


def test_site_security_headers():
    r = sites.site_security("example.invalid", {"strict-transport-security": "max-age=1", "x-content-type-options": "nosniff"})
    # Real Nexus keys: hsts, nosniff, frame, csp, server, https_redirect, old_tls.
    assert r["hsts"] is True
    assert r["nosniff"] is True
    assert r["frame"] is False
    assert r["csp"] is False
    assert set(r.keys()) == {"hsts", "nosniff", "frame", "csp", "server", "https_redirect", "old_tls"}


def test_lan_ports_constants():
    assert lan.RISKY_PORTS[23] == "telnet"
    assert 3389 in lan.LAN_PORTS


def _has_signature(fn, *names):
    params = list(inspect.signature(fn).parameters)
    assert params == list(names)


def test_signatures_match_brief():
    _has_signature(sites.check_site, "s", "th")
    _has_signature(sites.site_security, "host", "hdrs")
    _has_signature(sites.admin_probe, "sites", "state")
    _has_signature(sites.external_sites, "cfg", "sites", "state")
    _has_signature(hosts.check_ssh, "h", "th")
    _has_signature(hosts.mac_updates)
    _has_signature(lan.lan_sweep, "cfg")
    _has_signature(lan.lan_ports, "ips")
    _has_signature(lan.mdns_names)
    _has_signature(lan.mac_vendor, "mac")
    _has_signature(lan.host_name, "ip", "names")
    _has_signature(lan.risk_score, "d", "unverified")
    _has_signature(lan.presence_state, "d", "online", "now", "flapping", "base")
    _has_signature(lan.default_gateway)
    _has_signature(lan.my_lan_ip)
    _has_signature(home.wan_ip)
    # `slow_ms` is the path-latency threshold `runner.run_once` passes from
    # the tenant thresholds; a "slow but not lossy" path is a warning only
    # above it. Pinned here with the rest: every monkeypatch stub of this
    # function has to accept it, and one that does not raises inside the
    # collector's broad `except Exception` rather than failing where it is.
    _has_signature(home.wan_path, "cfg", "slow_ms")
    _has_signature(home.speed_test)
    _has_signature(home.dns_check)
    _has_signature(home.wifi_info)
    _has_signature(home.gateway_ports, "gw")
    _has_signature(home.external_exposure, "cfg", "wan")
    _has_signature(home.home_security, "cfg", "res", "state")
    _has_signature(home.rogue_checks, "cfg", "wifi", "on_home_lan", "state")
    _has_signature(home.tailscale_status)
    _has_signature(home.host_load)
    _has_signature(domains.domain_expiry, "hosts", "state")
    # `advisory` joined the brief: domains the customer does not control are
    # CHECKED but never raise anything (Miro's ruling on item 27).
    _has_signature(domains.email_security, "domains", "state", "advisory")
    _has_signature(dnsstats.dns_analyse, "cfg", "state")
    _has_signature(checks.extra_checks, "cfg")
    _has_signature(checks.heartbeats_check, "cfg", "state")


def test_module_level_constants_present():
    assert isinstance(lan.LAN_PORTS, list)
    assert isinstance(lan.RISKY_PORTS, dict)
    assert isinstance(sites.ADMIN_PATHS, dict)
    assert isinstance(home.STRONG_WIFI, tuple)
    assert isinstance(home.EXPOSURE_PORTS, list)
    assert isinstance(dnsstats.DGA_RE.pattern, str)
    assert isinstance(hosts.REMOTE, str)
    assert isinstance(lan._VENDOR_CACHE, dict)


def test_host_load_reports_load_and_cores():
    got = home.host_load()
    assert set(got) == {"load1", "cores"}
    # On every platform the test suite runs on there IS a load average, so the
    # real call must produce a number -- a silently-None result here would mean
    # the collector is broken, not that the platform has no load.
    assert isinstance(got["load1"], float)
    assert got["load1"] >= 0
    assert isinstance(got["cores"], int) and got["cores"] >= 1


def test_host_load_reports_missing_rather_than_zero(monkeypatch):
    """A platform with no load average (Windows) must come back None, never
    0.0 -- a zero would render in the dashboard as an idle machine."""
    def boom():
        raise OSError("no load average on this platform")
    monkeypatch.setattr(home.os, "getloadavg", boom)
    monkeypatch.setattr(home.os, "cpu_count", lambda: None)
    assert home.host_load() == {"load1": None, "cores": None}


def test_email_security_drops_domains_no_longer_asked_for(monkeypatch):
    """An excluded domain must leave the result on the next cycle, not ride
    the daily cache -- `email.exclude` was silently a no-op before this."""
    from guardian_sensor import domains, util
    fresh = {"spf": None, "dmarc": "v=DMARC1; p=none", "dmarc_policy": "none",
             "dkim_selector": None, "has_mx": True, "ts": util.now_iso()}
    state = {"email_sec": {"example.org": dict(fresh), "example.com": dict(fresh)}}
    out = domains.email_security(["example.com"], state)
    assert set(out) == {"example.com"}

