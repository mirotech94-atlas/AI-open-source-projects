"""Tests for the two honest network-centric detections added on top of the
FortiNDR review (task-83 design proposal): a weaker, evidence-based version
of lateral movement, and a volume-only (never "exfiltration") DNS anomaly.

Both are built from data Vault already collects — no packet capture, no
new collector, no new dependency. Both must stay silent on a first-seen
baseline (mirrors `lan:new`'s `known_macs is None` rule) so a freshly
enrolled site or host produces no noise on day one.
"""
import copy

from guardian_sensor import alerts, config


def base_cfg():
    return copy.deepcopy(config.DEFAULTS)


def ssh_host(name, login_sources):
    return {"name": name, "reachable": True, "optional": False, "disk_pct": 10, "mem_pct": 10,
            "load1": 0.1, "load_per_core": 0.05, "fail2ban": "active", "failed_ssh_24h": 0,
            "reboot_required": False, "docker": [], "services": {}, "uptime": None, "ports": None,
            "cve_fixable": -1, "sec": {"login_sources": login_sources}}


def base_res(ssh=None, networks=None):
    return {"ts": "2026-09-10T12:00:00-04:00", "sites": [], "ssh": ssh or [],
            "networks": networks or []}


NET = {"slug": "office-wifi", "primary": True, "status": "active", "live_count": 1,
       "devices": {"192.168.1.50": "aa:bb:cc:dd:ee:01", "192.168.1.9": "aa:bb:cc:dd:ee:02"},
       "names": {}, "watch_ips": {}}


# ---------------- lateral movement (sec:lanlogin) ----------------

def test_first_scan_records_a_silent_baseline_not_an_alert():
    res = base_res(ssh=[ssh_host("records", ["192.168.1.50"])], networks=[NET])
    cfg = base_cfg()
    state = {}  # no prior "sec" baseline at all
    got = alerts.build_alerts(res, cfg, state)
    keys = {a["key"]: a["sev"] for a in got}
    assert "sec:lanlogin:records" in keys and keys["sec:lanlogin:records"] == "INFO"
    assert not [k for k in keys if k.startswith("sec:lanlogin:records:")]


def test_a_lan_device_never_seen_signing_in_before_fires_once_baseline_exists():
    res = base_res(ssh=[ssh_host("records", ["192.168.1.50", "192.168.1.9"])], networks=[NET])
    cfg = base_cfg()
    state = {"sec": {"records": {"lan_login_macs": {"aa:bb:cc:dd:ee:01": "2026-09-01T00:00:00-04:00"}}}}
    got = alerts.build_alerts(res, cfg, state)
    keys = {a["key"]: a["sev"] for a in got}
    assert "sec:lanlogin:records:aa:bb:cc:dd:ee:02" in keys
    assert keys["sec:lanlogin:records:aa:bb:cc:dd:ee:02"] == "WARN"
    # the address already in the baseline must not re-fire
    assert "sec:lanlogin:records:aa:bb:cc:dd:ee:01" not in keys


def test_an_unresolvable_address_is_never_flagged():
    # login came from a private address that never showed up on any configured
    # LAN sweep -- Vault has no MAC to pin the baseline to, so this must
    # stay silent rather than risk firing on a routed/VPN address.
    res = base_res(ssh=[ssh_host("records", ["10.99.99.99"])], networks=[NET])
    cfg = base_cfg()
    state = {"sec": {"records": {"lan_login_macs": {"aa:bb:cc:dd:ee:01": "2026-09-01T00:00:00-04:00"}}}}
    got = alerts.build_alerts(res, cfg, state)
    assert not [a for a in got if a["key"].startswith("sec:lanlogin:")]


def test_baseline_persists_forward_on_a_scan_with_no_internal_logins():
    # a scan where the login window happens to be empty (or all external) must
    # not reset the established baseline back to "first sight" -- otherwise
    # every device would look "new" again the next time it did show up.
    res = base_res(ssh=[ssh_host("records", [])], networks=[NET])
    cfg = base_cfg()
    state = {"sec": {"records": {"lan_login_macs": {"aa:bb:cc:dd:ee:01": "2026-09-01T00:00:00-04:00"}}}}
    alerts.build_alerts(res, cfg, state)
    assert res["ssh"][0]["sec"]["lan_login_macs"] == {"aa:bb:cc:dd:ee:01": "2026-09-01T00:00:00-04:00"}


def test_unreachable_host_is_skipped_entirely():
    res = base_res(ssh=[{"name": "records", "reachable": False, "optional": False}], networks=[NET])
    cfg = base_cfg()
    state = {}
    got = alerts.build_alerts(res, cfg, state)
    assert not [a for a in got if a["key"].startswith("sec:lanlogin:")]


# ---------------- DNS volume (dns:volume) ----------------

def test_no_baseline_yet_stays_silent():
    res = base_res()
    res["dns_filter"] = {"client_totals_by_mac": {"aa:bb:cc:dd:ee:01": 9000},
                          "client_names_by_mac": {"aa:bb:cc:dd:ee:01": "NAS"}}
    cfg = base_cfg()
    state = {}
    got = alerts.build_alerts(res, cfg, state)
    assert not [a for a in got if a["key"].startswith("dns:volume:")]


def test_a_thin_baseline_does_not_fire_either():
    # only 3 samples recorded -- not enough history to call anything "unusual"
    res = base_res()
    res["dns_filter"] = {"client_totals_by_mac": {"aa:bb:cc:dd:ee:01": 9000},
                          "client_names_by_mac": {"aa:bb:cc:dd:ee:01": "NAS"}}
    cfg = base_cfg()
    state = {"dns_volume_baseline": {"aa:bb:cc:dd:ee:01": {"avg": 100, "n": 3}}}
    got = alerts.build_alerts(res, cfg, state)
    assert not [a for a in got if a["key"].startswith("dns:volume:")]


def test_a_large_multiple_of_an_established_baseline_fires_a_warn():
    res = base_res()
    res["dns_filter"] = {"client_totals_by_mac": {"aa:bb:cc:dd:ee:01": 9000},
                          "client_names_by_mac": {"aa:bb:cc:dd:ee:01": "NAS"}}
    cfg = base_cfg()
    state = {"dns_volume_baseline": {"aa:bb:cc:dd:ee:01": {"avg": 100, "n": 10}}}
    got = alerts.build_alerts(res, cfg, state)
    hits = [a for a in got if a["key"] == "dns:volume:aa:bb:cc:dd:ee:01"]
    assert len(hits) == 1
    assert hits[0]["sev"] == "WARN"
    msg = hits[0]["msg"]
    assert "NAS" in msg
    assert "exfiltrat" not in msg.lower()
    assert "!" not in msg
    assert "—" not in msg


def test_alert_copy_has_no_em_dash_or_exclamation():
    res = base_res()
    res["dns_filter"] = {"client_totals_by_mac": {"aa:bb:cc:dd:ee:01": 9000},
                          "client_names_by_mac": {"aa:bb:cc:dd:ee:01": "NAS"}}
    cfg = base_cfg()
    state = {"dns_volume_baseline": {"aa:bb:cc:dd:ee:01": {"avg": 100, "n": 10}}}
    got = alerts.build_alerts(res, cfg, state)
    hit = next(a for a in got if a["key"] == "dns:volume:aa:bb:cc:dd:ee:01")
    assert "—" not in hit["msg"]
    assert "!" not in hit["msg"]


def test_a_modest_increase_within_a_normal_range_does_not_fire():
    res = base_res()
    res["dns_filter"] = {"client_totals_by_mac": {"aa:bb:cc:dd:ee:01": 150},
                          "client_names_by_mac": {"aa:bb:cc:dd:ee:01": "NAS"}}
    cfg = base_cfg()
    state = {"dns_volume_baseline": {"aa:bb:cc:dd:ee:01": {"avg": 100, "n": 10}}}
    got = alerts.build_alerts(res, cfg, state)
    assert not [a for a in got if a["key"].startswith("dns:volume:")]
