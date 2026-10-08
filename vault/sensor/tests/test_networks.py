"""Several watched networks per sensor (Task 52).

`config.networks[]`, the per-network scan loop, per-network state with the
legacy flat keys aliased at the primary, namespaced LAN alert keys, and the
`net` routing of hub edits. No network and no subprocess: `lan.local_subnets`
and `lan.lan_sweep` are stubbed everywhere a scan is involved.
"""
import json
import os

import pytest

from guardian_sensor import alerts, config, lan, paths, runner, sync

TWO_NETS = [
    {"name": "Office Wi-Fi", "kind": "wifi", "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"},
    {"name": "Lab", "kind": "lab", "subnet_prefix": "10.10.0", "expected_gateway": "10.10.0.1"},
]


# ---------------- config ----------------

def test_old_lan_block_becomes_one_primary_network(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"lan": {"subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"}}))
    cfg = config.load(str(p))
    nets = config.networks_of(cfg)
    assert len(nets) == 1 and nets[0]["slug"] == "home-network" and nets[0]["primary"] is True
    assert cfg["lan"]["subnet_prefix"] == "192.168.1"      # invariant 1: legacy readers untouched


def test_two_networks_keep_order_and_slugs(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"networks": TWO_NETS}))
    cfg = config.load(str(p))
    nets = config.networks_of(cfg)
    assert [n["slug"] for n in nets] == ["office-wi-fi", "lab"]
    assert cfg["lan"]["expected_gateway"] == "192.168.1.1"
    assert cfg["lan"]["subnet_prefix"] == "192.168.1"


def test_a_network_named_after_a_lan_subkey_never_slugs_to_it():
    # a slug that IS a sub-key would make lan:new:port:<mac> unstrippable
    assert config.slugify("New") == "new-net"
    assert config.slugify("Port") == "port-net"
    assert config.slugify("") == "network"


def test_primary_inherits_the_legacy_watch_list_and_secondaries_do_not():
    cfg = {"lan": {"watch_ips": {"192.168.1.50": "NAS"}}, "networks": TWO_NETS}
    prim, lab = config.networks_of(cfg)
    assert prim["watch_ips"] == {"192.168.1.50": "NAS"}
    assert lab["watch_ips"] == {}


# ---------------- the scan loop ----------------

def _stub_sweep(monkeypatch, subs):
    monkeypatch.setattr(lan, "local_subnets", lambda: subs)
    monkeypatch.setattr(lan, "lan_sweep", lambda net: {
        "live_count": 2, "devices": {net["subnet_prefix"] + ".9": "aa:bb"}, "names": {},
        "gw_mac": None, "watch_ips": {}})


def _cfg_two():
    return {"lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1",
                    "watch_ips": {}},
            "networks": [dict(n, enabled=True) for n in TWO_NETS]}


def test_unreachable_network_reports_a_status_not_an_empty_scan(monkeypatch):
    _stub_sweep(monkeypatch, {"192.168.1": "en0"})
    nets = runner.scan_networks(_cfg_two(), {}, no_lan=False)
    assert [n["status"] for n in nets] == ["active", "unreachable"]
    assert nets[1]["reason"] and "devices" not in nets[1]
    assert nets[1]["slug"] == "lab" and nets[1]["subnet_prefix"] == "10.10.0"


def test_both_networks_are_swept_when_the_sensor_is_multi_homed(monkeypatch):
    _stub_sweep(monkeypatch, {"192.168.1": "en0", "10.10.0": "en8"})
    nets = runner.scan_networks(_cfg_two(), {}, no_lan=False)
    assert [n["status"] for n in nets] == ["active", "active"]
    assert nets[1]["devices"] == {"10.10.0.9": "aa:bb"}


def test_a_pinned_interface_decides_reachability():
    assert lan.reachable({"subnet_prefix": "10.10.0", "interface": "en8"}, {"192.168.1": "en8"}) is True
    assert lan.reachable({"subnet_prefix": "10.10.0", "interface": "en9"}, {"192.168.1": "en8"}) is False
    assert lan.reachable({"subnet_prefix": "10.10.0"}, {"10.10.0": "en8"}) is True


def test_no_lan_scans_nothing(monkeypatch):
    _stub_sweep(monkeypatch, {"192.168.1": "en0"})
    assert runner.scan_networks(_cfg_two(), {}, no_lan=True) == []


# ---------------- alert keys ----------------

def test_lan_key_is_legacy_for_primary_and_namespaced_after_it():
    prim = {"slug": "office-wi-fi", "primary": True}
    lab = {"slug": "lab", "primary": False}
    assert alerts.lan_key("new", prim, mac="aa:bb") == "lan:new:aa:bb"
    # slug FIRST: the shape hub/guardian_hub/networks.lan_alert_key produces,
    # and the only one canonical_lan_key can strip (spec S3.1 writes the slug
    # after the sub-key; the parent RULING and the landed hub say otherwise).
    assert alerts.lan_key("new", lab, mac="aa:bb") == "lan:lab:new:aa:bb"
    assert alerts.lan_key("port", lab, mac="aa:bb", port=23) == "lan:lab:port:aa:bb:23"
    assert alerts.lan_key("unverified", prim) == "lan:unverified"
    assert alerts.lan_key("unverified", lab) == "lan:lab:unverified"


def test_canonical_lan_key_strips_only_the_network_segment():
    assert alerts.canonical_lan_key("lan:lab:new:aa:bb") == "lan:new:aa:bb"
    assert alerts.canonical_lan_key("lan:new:aa:bb") == "lan:new:aa:bb"       # idempotent
    assert alerts.canonical_lan_key("home:gw-mac") == "home:gw-mac"


def test_secondary_network_alerts_are_namespaced_and_stamped():
    res = {"ts": "2026-09-10T12:00:00-04:00", "sites": [], "ssh": [],
           "networks": [
               {"slug": "office-wi-fi", "primary": True, "status": "active", "live_count": 1,
                "devices": {"192.168.1.9": "aa:bb:cc:dd:ee:01"}, "names": {}, "watch_ips": {}},
               {"slug": "lab", "primary": False, "status": "active", "live_count": 1,
                "devices": {"10.10.0.9": "aa:bb:cc:dd:ee:02"}, "names": {}, "watch_ips": {}}]}
    cfg = {"thresholds": {"http_latency_warn_s": 4.0, "cert_days_crit": 7, "cert_days_warn": 21},
           "lan": {"expected_gateway": "192.168.1.1", "watch_ips": {}}, "tailscale": {}}
    state = {"lan": {"networks": {
        "office-wi-fi": {"known_macs": {}},
        "lab": {"known_macs": {}}}}}
    built = alerts.build_alerts(res, cfg, state)
    got = {a["key"] for a in built}
    assert "lan:new:aa:bb:cc:dd:ee:01" in got          # primary: legacy shape
    assert "lan:lab:new:aa:bb:cc:dd:ee:02" in got      # secondary: namespaced
    # every LAN alert is stamped with the network it fired on (hub's
    # `_net_for` in hub/guardian_hub/ingest.py resolves a row's own `net`
    # slug straight against `nets[slug]` before falling back to the primary,
    # so the primary's alerts carry ITS OWN slug too, not a bare "primary").
    by_key = {a["key"]: a for a in built}
    assert by_key["lan:new:aa:bb:cc:dd:ee:01"]["net"] == "office-wi-fi"
    assert by_key["lan:lab:new:aa:bb:cc:dd:ee:02"]["net"] == "lab"


def test_an_unreachable_network_does_not_report_its_devices_as_gone():
    res = {"ts": "2026-09-10T12:00:00-04:00", "sites": [], "ssh": [],
           "networks": [{"slug": "lab", "primary": False, "status": "unreachable",
                         "reason": "no local interface on 10.10.0.0/24"}]}
    cfg = {"thresholds": {"http_latency_warn_s": 4.0, "cert_days_crit": 7, "cert_days_warn": 21},
           "lan": {"expected_gateway": "10.10.0.1", "watch_ips": {}}, "tailscale": {}}
    state = {"lan": {"networks": {"lab": {"known_macs": {"aa:bb": {"presence": "ONLINE"}}}}}}
    keys = {a["key"] for a in alerts.build_alerts(res, cfg, state)}
    assert not [k for k in keys if ":new:" in k or k.endswith(":baseline")]


# ---------------- state migration ----------------

def test_state_migration_keeps_the_legacy_keys_pointing_at_the_primary():
    state = {"lan": {"known_macs": {"aa:bb": {"ip": "192.168.1.9"}}, "last_live_macs": ["aa:bb"],
                     "miss_count": {}, "transitions": {}}}
    runner._migrate_lan_state(state, "office-wi-fi")
    nets = state["lan"]["networks"]
    assert nets["office-wi-fi"]["known_macs"] is state["lan"]["known_macs"]     # same object
    nets["office-wi-fi"]["known_macs"]["cc:dd"] = {"ip": "192.168.1.10"}
    assert "cc:dd" in state["lan"]["known_macs"]
    assert state["lan"]["schema"] == 2
    runner._migrate_lan_state(state, "office-wi-fi")                            # idempotent
    assert len(state["lan"]["networks"]) == 1


def test_rebinding_the_primary_baseline_keeps_the_alias(monkeypatch):
    state = {"lan": {"known_macs": {"aa:bb": {}}}}
    runner._migrate_lan_state(state, "office-wi-fi")
    net = {"slug": "office-wi-fi", "primary": True}
    runner._set_net(state, net, "known_macs", {"cc:dd": {}})
    assert state["lan"]["known_macs"] == {"cc:dd": {}}
    assert state["lan"]["networks"]["office-wi-fi"]["known_macs"] is state["lan"]["known_macs"]


# ---------------- edit routing ----------------

def test_edits_route_to_the_named_network_and_default_to_primary():
    state = {"lan": {"networks": {"office-wi-fi": {"known_macs": {"aa:bb": {}}},
                                  "lab": {"known_macs": {"aa:bb": {}}}},
                     "primary": "office-wi-fi"}}
    state["lan"]["known_macs"] = state["lan"]["networks"]["office-wi-fi"]["known_macs"]
    assert sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:bb", "label": "Lab PC",
                                                            "net": "lab"}}, state, {}) == "state"
    assert state["lan"]["networks"]["lab"]["known_macs"]["aa:bb"]["label"] == "Lab PC"
    assert "label" not in state["lan"]["networks"]["office-wi-fi"]["known_macs"]["aa:bb"]
    sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:bb", "label": "Desk"}}, state, {})
    assert state["lan"]["known_macs"]["aa:bb"]["label"] == "Desk"               # no net = primary


def test_reset_baseline_scopes_to_one_network():
    state = {"lan": {"networks": {"office-wi-fi": {"known_macs": {"aa:bb": {}}},
                                  "lab": {"known_macs": {"cc:dd": {}}}},
                     "primary": "office-wi-fi"}}
    state["lan"]["known_macs"] = state["lan"]["networks"]["office-wi-fi"]["known_macs"]
    sync.apply_edit({"path": "/api/reset-lan-baseline", "body": {"net": "lab"}}, state, {})
    assert state["lan"]["networks"]["lab"]["known_macs"] is None
    assert state["lan"]["known_macs"] == {"aa:bb": {}}                          # primary untouched
    sync.apply_edit({"path": "/api/reset-lan-baseline", "body": {}}, state, {})
    assert state["lan"]["known_macs"] is None
    assert state["lan"]["networks"]["office-wi-fi"]["known_macs"] is None


# ---------------- the push shape ----------------

@pytest.fixture
def stubbed_client(monkeypatch, tmp_path):
    """A real HubClient (so the push body is really built) fed one two-network
    scan; `.last_push` is the recorded ingest body."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sync, "RETRY_DELAY", 0)
    client = sync.HubClient("https://hub.example.invalid", "tok")
    pushes = []

    def fake_req(method, path, body=None):
        if (method, path) == ("POST", "/ingest"):
            pushes.append(body)
            return {"ok": True}
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(client, "_req", fake_req)
    res = {"ts": "2026-09-10T12:00:00-04:00", "networks": [
        {"slug": "office-wi-fi", "name": "Office Wi-Fi", "primary": True, "status": "active",
         "live_count": 2, "devices": {"192.168.1.9": "aa:bb"}, "names": {}, "subnet_prefix": "192.168.1"},
        {"slug": "lab", "name": "Lab", "primary": False, "status": "unreachable",
         "reason": "no local interface on 10.10.0.0/24", "subnet_prefix": "10.10.0"}]}
    client.push({"version": "t", "last": res, "state": {}})
    client.last_push = pushes[-1]
    return client


def test_push_body_carries_networks(stubbed_client):
    body = stubbed_client.last_push
    assert [n["slug"] for n in body["networks"]] == ["office-wi-fi", "lab"]
    assert body["networks"][1]["status"] == "unreachable" and body["networks"][1]["reason"]
    # the bulky maps stay in `last`; the networks block is identity + status
    assert "devices" not in body["networks"][0] and "names" not in body["networks"][0]


def test_pushed_dns_rows_are_attributed_to_a_network():
    nets = [{"slug": "office-wi-fi", "primary": True, "subnet_prefix": "192.168.1"},
            {"slug": "lab", "primary": False, "subnet_prefix": "10.10.0"}]
    rows = [{"client": "10.10.0.20", "name": "a.example"},
            {"client": "192.168.1.20", "name": "b.example"},
            {"client": "8.8.8.8", "name": "c.example"}]
    assert [r["net"] for r in sync._attribute_dns(rows, nets)] == ["lab", "office-wi-fi", "office-wi-fi"]


# ---------------- DNS per network ----------------

def test_dns_by_network_splits_clients_by_subnet():
    from guardian_sensor import dnsstats
    cfg = {"lan": {}, "networks": TWO_NETS}
    by = dnsstats._by_network(cfg, {"192.168.1.5": 10, "10.10.0.7": 3},
                              {"192.168.1.5": 2}, {"192.168.1.5": {"x.example": 10},
                                                   "10.10.0.7": {"y.example": 3}})
    assert by["office-wi-fi"] == {"queries_24h": 10, "blocked_24h": 2, "top": [("x.example", 10)], "clients": 1}
    assert by["lab"]["queries_24h"] == 3 and by["lab"]["blocked_24h"] == 0


# ---------------- a full two-network cycle ----------------

@pytest.fixture
def two_net_run(monkeypatch, tmp_path):
    """A whole `run_once` cycle with two reachable networks and every other
    collector stubbed — the only place the per-network device pass, event pass
    and state commit actually run."""
    from guardian_sensor import checks, dnsstats, domains, fix, home, hosts, notify, sites
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    cfg = {
        "sites": [], "ssh_hosts": [], "networks": [dict(n, enabled=True) for n in TWO_NETS],
        "lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1",
                "watch_ips": {}},
        "tailscale": {"enabled": False}, "thresholds": {"fire_after_runs": 2, "resend_hours": 6},
        "auto_fix": {"enabled": False, "digest_hour": 99}, "home": {}, "heartbeats": {},
        "checks": [], "dns": {"enabled": False},
    }
    _stub_sweep(monkeypatch, {"192.168.1": "en0", "10.10.0": "en8"})
    monkeypatch.setattr(lan, "default_gateway", lambda: "192.168.1.1")
    monkeypatch.setattr(lan, "lan_ports", lambda ips: {ip: [] for ip in ips})
    monkeypatch.setattr(lan, "my_lan_ip", lambda: "192.168.1.2")
    monkeypatch.setattr(lan, "mac_vendor", lambda m: "Acme")
    monkeypatch.setattr(hosts, "mac_updates", lambda: {"ts": None})
    monkeypatch.setattr(home, "dns_check", lambda: {"system": {"ok": True, "ms": 9},
                                                   "cloudflare": {"ok": True, "ms": 12},
                                                   "google": {"ok": True, "ms": 14}})
    monkeypatch.setattr(home, "wan_ip", lambda: None)
    # Signature tracks the real `home.wan_path(cfg, slow_ms=None)`: `run_once`
    # passes `slow_ms=` from the thresholds, and a stub that refuses the
    # keyword raises inside the collector's broad `except Exception`, which
    # files it under `res["collect_error"]` and leaves `res["rogues"]` unset.
    # The failure then surfaces as KeyError in whichever later test reads
    # rogues, naming neither this line nor the argument.
    monkeypatch.setattr(home, "wan_path", lambda cfg, slow_ms=None: None)
    monkeypatch.setattr(home, "wifi_info", lambda: None)
    monkeypatch.setattr(home, "rogue_checks", lambda *a: None)
    monkeypatch.setattr(home, "home_security", lambda *a: None)
    monkeypatch.setattr(home, "speed_test", lambda: {"error": "no speed test in tests"})
    monkeypatch.setattr(domains, "domain_expiry", lambda h, s: {})
    monkeypatch.setattr(domains, "email_security", lambda d, s, advisory=frozenset(): {})
    monkeypatch.setattr(checks, "heartbeats_check", lambda cfg, state: {})
    monkeypatch.setattr(checks, "extra_checks", lambda cfg: [])
    monkeypatch.setattr(dnsstats, "dns_analyse", lambda cfg, state: {"enabled": False})
    monkeypatch.setattr(sites, "admin_probe", lambda s, state: {})
    monkeypatch.setattr(sites, "external_sites", lambda cfg, s, state: {})
    monkeypatch.setattr(fix, "remediate", lambda res, cfg, state, runner=None: [])
    monkeypatch.setattr(notify, "notify", lambda *a, **k: [])
    monkeypatch.setattr(sync, "client_from_disk", lambda: None)
    return cfg, type("A", (), {"once": True, "no_lan": False})()


def test_two_network_cycle_keeps_one_baseline_per_network(two_net_run):
    cfg, args = two_net_run
    state = {}
    r1 = runner.run_once(cfg, state, args)          # baseline run
    r2 = runner.run_once(cfg, state, args)          # diffing run

    assert [n["status"] for n in r2["networks"]] == ["active", "active"]
    assert r2["lan"] is r2["networks"][0]           # invariant 1: primary by reference
    nets = state["lan"]["networks"]
    assert set(nets) == {"office-wi-fi", "lab"}
    # each network keeps its OWN devices, and the primary's map is still the
    # legacy flat key (same object)
    assert list(nets["office-wi-fi"]["known_macs"]) == ["aa:bb"]
    assert nets["office-wi-fi"]["known_macs"] is state["lan"]["known_macs"]
    assert nets["office-wi-fi"]["known_macs"]["aa:bb"]["ip"] == "192.168.1.9"
    assert nets["lab"]["known_macs"]["aa:bb"]["ip"] == "10.10.0.9"
    assert state["lan"]["schema"] == 2
    # the history line splits live macs per network once there are two
    hist = [json.loads(l) for l in open(paths.p("history.jsonl"))]
    assert hist[-1]["live_macs"] == {"office-wi-fi": ["aa:bb"], "lab": ["aa:bb"]}
    assert hist[-1]["networks"]["lab"]["status"] == "active"
    assert r1["ts"] and r2["ts"]


def test_an_unreachable_second_network_is_reported_not_emptied(monkeypatch, two_net_run):
    cfg, args = two_net_run
    state = {}
    runner.run_once(cfg, state, args)                       # both reachable: baseline
    monkeypatch.setattr(lan, "local_subnets", lambda: {"192.168.1": "en0"})   # lab cable pulled
    res = runner.run_once(cfg, state, args)

    lab = next(n for n in res["networks"] if n["slug"] == "lab")
    assert lab["status"] == "unreachable" and "10.10.0" in lab["reason"]
    assert "devices" not in lab
    # its baseline survives untouched: nothing "left the network"
    assert list(state["lan"]["networks"]["lab"]["known_macs"]) == ["aa:bb"]
    evp = paths.p("events.jsonl")
    evs = open(evp).read().splitlines() if os.path.exists(evp) else []
    assert not [e for e in evs if '"leave"' in e and '"lab"' in e]


def test_a_device_edit_after_a_baseline_reset_does_not_recreate_the_map():
    # known_macs is None = "no baseline, record one next scan"; {} would mean
    # "baseline exists and is empty" and would skip the lan:baseline alert.
    state = {"lan": {"networks": {"office-wi-fi": {"known_macs": None},
                                  "lab": {"known_macs": None}},
                     "primary": "office-wi-fi", "known_macs": None}}
    assert sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:bb", "label": "X"}}, state, {}) is None
    assert sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:bb", "label": "X",
                                                            "net": "lab"}}, state, {}) is None
    assert state["lan"]["known_macs"] is None
    assert state["lan"]["networks"]["lab"]["known_macs"] is None


# ---------------- roaming guard: gateway MAC must match the stored one ----------------

def test_a_foreign_network_with_the_same_subnet_prefix_is_rejected(monkeypatch):
    """A sensor carried to an unrelated 192.168.1.0/24 network must not adopt
    it: the stored gateway MAC differs from what this sweep just saw, so the
    whole sweep is discarded (no gateway-MAC CRIT, no lan:new flood, no
    baseline pollution) in favour of an unreachable status."""
    monkeypatch.setattr(lan, "local_subnets", lambda: {"192.168.1": "en0"})
    monkeypatch.setattr(lan, "lan_sweep", lambda net: {
        "live_count": 1, "devices": {"192.168.1.9": "aa:bb"}, "names": {},
        "gw_mac": "11:11:11:11:11:11", "watch_ips": {}})
    cfg = {"lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1", "watch_ips": {}},
           "networks": [dict(TWO_NETS[0], enabled=True)]}
    state = {"lan": {"networks": {"office-wi-fi": {"gw_mac": "22:22:22:22:22:22", "known_macs": {"cc:dd": {}}}}}}
    nets = runner.scan_networks(cfg, state, no_lan=False)
    assert nets[0]["status"] == "unreachable"
    assert nets[0]["reason"] == "gateway MAC does not match this network"
    assert "devices" not in nets[0]
    # baseline and stored gateway MAC both untouched
    assert state["lan"]["networks"]["office-wi-fi"]["known_macs"] == {"cc:dd": {}}
    assert state["lan"]["networks"]["office-wi-fi"]["gw_mac"] == "22:22:22:22:22:22"


def test_a_sweep_with_a_missing_gateway_mac_stays_active(monkeypatch):
    """A transient ARP miss (the real gateway just didn't answer this round,
    so `gw_mac` comes back None) must NOT be treated as a roam: the guard
    only fires when the sweep actually reports a differing gateway MAC.
    The network stays active (devices/alerts keep flowing for this cycle)
    and the stored gw_mac is left untouched rather than being overwritten
    with None."""
    monkeypatch.setattr(lan, "local_subnets", lambda: {"192.168.1": "en0"})
    monkeypatch.setattr(lan, "lan_sweep", lambda net: {
        "live_count": 1, "devices": {"192.168.1.9": "aa:bb"}, "names": {},
        "gw_mac": None, "watch_ips": {}})
    cfg = {"lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1", "watch_ips": {}},
           "networks": [dict(TWO_NETS[0], enabled=True)]}
    state = {"lan": {"networks": {"office-wi-fi": {"gw_mac": "22:22:22:22:22:22"}}}}
    nets = runner.scan_networks(cfg, state, no_lan=False)
    assert nets[0]["status"] == "active"
    assert nets[0]["devices"] == {"192.168.1.9": "aa:bb"}
    # stored gateway MAC is unchanged (never overwritten with None)
    assert state["lan"]["networks"]["office-wi-fi"]["gw_mac"] == "22:22:22:22:22:22"


def test_a_matching_gateway_mac_sweeps_normally(monkeypatch):
    monkeypatch.setattr(lan, "local_subnets", lambda: {"192.168.1": "en0"})
    monkeypatch.setattr(lan, "lan_sweep", lambda net: {
        "live_count": 1, "devices": {"192.168.1.9": "aa:bb"}, "names": {},
        "gw_mac": "22:22:22:22:22:22", "watch_ips": {}})
    cfg = {"lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1", "watch_ips": {}},
           "networks": [dict(TWO_NETS[0], enabled=True)]}
    state = {"lan": {"networks": {"office-wi-fi": {"gw_mac": "22:22:22:22:22:22"}}}}
    nets = runner.scan_networks(cfg, state, no_lan=False)
    assert nets[0]["status"] == "active"
    assert nets[0]["devices"] == {"192.168.1.9": "aa:bb"}


def test_a_network_with_no_stored_gateway_mac_learns_it_on_first_sweep(monkeypatch):
    monkeypatch.setattr(lan, "local_subnets", lambda: {"192.168.1": "en0"})
    monkeypatch.setattr(lan, "lan_sweep", lambda net: {
        "live_count": 1, "devices": {"192.168.1.9": "aa:bb"}, "names": {},
        "gw_mac": "22:22:22:22:22:22", "watch_ips": {}})
    cfg = {"lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1", "watch_ips": {}},
           "networks": [dict(TWO_NETS[0], enabled=True)]}
    nets = runner.scan_networks(cfg, {}, no_lan=False)   # nothing stored yet
    assert nets[0]["status"] == "active"


# ---------------- rogues placeholder per network ----------------

def test_rogues_placeholder_for_a_network_without_the_sensors_wifi(monkeypatch, two_net_run):
    from guardian_sensor import home
    cfg, args = two_net_run
    monkeypatch.setattr(home, "wifi_info", lambda: {"ssid": "Office"})
    monkeypatch.setattr(home, "rogue_checks", lambda *a: {"on_home_ssid": True})
    res = runner.run_once(cfg, {}, args)
    assert not res.get("collect_error"), res["collect_error"]
    assert res["rogues"]["office-wi-fi"] == {"on_home_ssid": True}
    assert res["rogues"]["lab"] == {"verified": False, "reason": "no Wi-Fi interface on this network"}


def test_rogues_all_placeholders_when_the_sensor_has_no_wifi_at_all(two_net_run):
    cfg, args = two_net_run                       # fixture stubs wifi_info -> None
    res = runner.run_once(cfg, {}, args)
    assert not res.get("collect_error"), res["collect_error"]
    assert set(res["rogues"]) == {"office-wi-fi", "lab"}
    assert all(v == {"verified": False, "reason": "no Wi-Fi interface on this network"} for v in res["rogues"].values())


# ---------------- stale network-state pruning ----------------

def test_renaming_a_network_prunes_its_old_state_slice():
    cfg = {"lan": {"enabled": True, "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"},
           "networks": [{"name": "Office Wi-Fi", "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"},
                        {"name": "Lab Renamed", "subnet_prefix": "10.10.0", "expected_gateway": "10.10.0.1"}]}
    state = {"lan": {"networks": {"office-wi-fi": {"known_macs": {}}, "lab": {"known_macs": {"aa:bb": {}}}}}}
    runner._prune_stale_networks(state, cfg)
    # "lab" is gone (its slug is no longer live); "lab-renamed" is not
    # fabricated here — it gets created lazily by `net_state` on the network's
    # next scan, same as any other new network.
    assert set(state["lan"]["networks"]) == {"office-wi-fi"}


def test_pruning_is_a_no_op_when_no_slice_is_stale():
    cfg = {"lan": {"enabled": True}, "networks": [dict(n, enabled=True) for n in TWO_NETS]}
    state = {"lan": {"networks": {"office-wi-fi": {"known_macs": {}}, "lab": {"known_macs": {}}}}}
    runner._prune_stale_networks(state, cfg)
    assert set(state["lan"]["networks"]) == {"office-wi-fi", "lab"}


# ---------------- _hist_live_macs shape follows config.networks ----------------

def test_hist_live_macs_is_dict_shaped_once_networks_is_configured_even_with_one():
    cfg = config.normalise_networks({"networks": [{"name": "Office", "subnet_prefix": "192.168.1",
                                                    "expected_gateway": "192.168.1.1"}]})
    res = {"networks": [{"slug": "office", "status": "active", "devices": {"192.168.1.9": "aa:bb"}}]}
    assert runner._hist_live_macs(res, cfg) == {"office": ["aa:bb"]}


def test_hist_live_macs_is_flat_when_the_config_has_no_networks_key_at_all():
    cfg = config.normalise_networks({"lan": {"subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"}})
    res = {"networks": [{"slug": "home-network", "status": "active", "devices": {"192.168.1.9": "aa:bb"}}]}
    assert runner._hist_live_macs(res, cfg) == ["aa:bb"]


def test_a_site_on_the_probe_host_is_probed_from_the_sensor_not_from_itself(monkeypatch):
    """Probing a host from itself bypasses its firewall and reports every
    listening port as internet-facing (the hub's own :8443 read that way)."""
    from guardian_sensor import sites, util
    cfg = {"home": {"external_probe_host": "hub"}, "ssh_hosts": [{"name": "hub", "target": "root@203.0.113.9"}]}
    monkeypatch.setattr(sites, "probe_host_addresses", lambda h: {"203.0.113.9"})
    monkeypatch.setattr(sites, "probe_ports_from_here", lambda ip, ports, timeout=2.0: [443])
    called = {}
    def fake_ssh(h, cmd, timeout=120, on_line=None):
        called["ssh"] = True
        return 0, '{"other.example": [22, 80]}'
    monkeypatch.setattr(util, "ssh_run", fake_ssh)
    out = sites.external_sites(cfg, [{"name": "Hub", "url": "https://hub.example/", "host": "hub.example", "dns_a": ["203.0.113.9"]},
                                     {"name": "Other", "url": "https://other.example/", "host": "other.example", "dns_a": ["198.51.100.4"]}], {})
    assert out["hub.example"]["open_ports"] == [443] and out["hub.example"]["probe_host"] == "sensor"
    assert out["other.example"]["open_ports"] == [22, 80] and called.get("ssh")


def test_a_shared_name_never_transfers_trust_to_a_new_private_mac():
    old,new="aa:00:00:00:00:01","aa:00:00:00:00:02"
    cfg={"lan":{"expected_gateway":"192.168.1.1"},"thresholds":{}}
    net={"slug":"office","primary":True,"devices":{"192.168.1.9":new},"names":{"192.168.1.9":"phone"}}
    state={"lan":{"known_macs":{old:{"name":"phone","trusted":True}}}}
    res={"ts":"2026-09-15T12:00:00Z"}
    notices=[]
    alerts._lan_alerts(res,cfg,state,net,lambda *args:notices.append(args))
    device=net["new_devices"][0]
    assert device.get("suggested_parent") == old
    assert not device.get("trusted") and not device.get("parent")
    assert any("confirm before trusting" in item[2] for item in notices)
