"""Task 69: camera identification, the camera census, the WAN pin and the
rogue-device findings.

No network and no subprocess anywhere: every collector fact is handed in as a
dict, which is the same shape `runner.run_once` builds.
"""
import copy
import json
import pathlib

import pytest

from guardian_sensor import alerts, cameras, config, lan, runner

FX = pathlib.Path(__file__).parent / "fixtures"


def _fixture():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    return last, config.normalise_networks(cfg), state


def _keys(res, cfg, state):
    return {a["key"]: a for a in alerts.build_alerts(copy.deepcopy(res), cfg, copy.deepcopy(state))}


# ---------------- classification ----------------

@pytest.mark.parametrize("dev,expected", [
    ({"vendor": "Reolink Innovation Limited", "name": "weme"}, True),
    ({"vendor": "Hangzhou Hikvision Digital Technology", "name": None}, True),
    ({"vendor": "?", "name": "front-door-cam"}, True),
    ({"vendor": "?", "name": "nvr01"}, True),
    # RTSP alone settles it, whatever the vendor says.
    ({"vendor": "unknown vendor", "name": None, "ports": {"554": "t"}}, True),
    ({"vendor": "unknown vendor", "name": None, "ports": {"37777": "t"}}, True),
    # …but the ambiguous ports do not, on their own.
    ({"vendor": "unknown vendor", "name": None, "ports": {"8000": "t"}}, False),
    ({"vendor": "unknown vendor", "name": None, "ports": {"34567": "t"}}, False),
    # a broad-catalogue vendor plus an ambiguous port does
    ({"vendor": "Amazon Technologies Inc.", "name": None, "ports": {"34567": "t"}}, True),
    # the things that must NEVER be cameras
    ({"vendor": "WNC Corporation", "name": "cr1000a.mynetworksettings.com", "label": "Router (Fios CR1000A)",
      "ports": {"53": "t", "80": "t", "443": "t"}}, False),
    ({"vendor": "AzureWave Technology Inc.", "label": "Canon printer (MF8200C)", "ports": {"9100": "t"}}, False),
    ({"vendor": "Amazon Technologies Inc.", "label": "Amazon Echo"}, False),
    ({"vendor": "Apple", "name": "miros-macbook", "label": "MacBook"}, False),
    # "webcam" is a laptop's own camera, not a network camera
    ({"vendor": "Intel Corporate", "name": "webcam-host"}, False),
])
def test_classify(dev, expected):
    assert cameras.classify(dev)[0] is expected


def test_a_classified_camera_reports_why():
    ok, conf, reasons = cameras.classify({"vendor": "Reolink Innovation Limited", "ports": {"554": "t"}})
    assert ok and conf == "high"
    assert any("vendor" in r for r in reasons) and any("554" in r for r in reasons)


def test_census_counts_and_stamps_the_kind():
    known = {
        "aa:aa:aa:aa:aa:01": {"ip": "192.168.1.5", "vendor": "Reolink Innovation Limited"},
        "aa:aa:aa:aa:aa:02": {"ip": "192.168.1.6", "vendor": "?", "ports": {"554": "t"}},
        "aa:aa:aa:aa:aa:03": {"ip": "192.168.1.7", "vendor": "Apple", "label": "MacBook"},
    }
    cens = cameras.census(known, {"aa:aa:aa:aa:aa:01"})
    assert cens["count"] == 2
    assert cens["online"] == ["aa:aa:aa:aa:aa:01"]
    assert cens["offline"] == ["aa:aa:aa:aa:aa:02"]
    assert known["aa:aa:aa:aa:aa:01"]["kind"] == "camera"
    assert "kind" not in known["aa:aa:aa:aa:aa:03"]


def test_the_first_census_is_silent_then_diffs():
    cens = {"count": 1, "macs": ["aa:bb"], "online": ["aa:bb"], "offline": [], "detail": {}}
    assert cameras.diff(None, cens, "2026-09-10T10:00:00-04:00") == ([], [])
    base = cameras.update_baseline(None, cens, "2026-09-10T10:00:00-04:00")
    assert cameras.diff(base, cens, "2026-09-10T10:05:00-04:00") == ([], [])
    two = {"count": 2, "macs": ["aa:bb", "cc:dd"], "online": ["aa:bb", "cc:dd"], "offline": [], "detail": {}}
    assert cameras.diff(base, two, "2026-09-10T10:05:00-04:00")[0] == ["cc:dd"]


def test_a_camera_is_only_gone_after_half_an_hour_of_silence():
    cens_on = {"count": 1, "macs": ["aa:bb"], "online": ["aa:bb"], "offline": [], "detail": {}}
    base = cameras.update_baseline(None, cens_on, "2026-09-10T10:00:00-04:00")
    quiet = {"count": 1, "macs": ["aa:bb"], "online": [], "offline": ["aa:bb"],
             "detail": {"aa:bb": {"ip": "192.168.1.5", "label": "Driveway"}}}
    assert cameras.diff(base, quiet, "2026-09-10T10:10:00-04:00")[1] == []       # one missed sweep: nothing
    gone = cameras.diff(base, quiet, "2026-09-10T10:40:00-04:00")[1]
    assert [g["mac"] for g in gone] == ["aa:bb"] and gone[0]["label"] == "Driveway"


# ---------------- alerts ----------------

def test_camera_alerts_stay_silent_on_the_recorded_fixture():
    """The recorded scan has one camera and no camera baseline, so it must add
    no camera key at all -- the same silent-first-observation rule the LAN
    device baseline has."""
    last, cfg, state = _fixture()
    assert not [k for k in _keys(last, cfg, state) if "camera" in k]


def test_a_new_camera_and_a_quiet_camera_each_get_one_alert():
    last, cfg, state = _fixture()
    mac = "ec:71:db:05:5a:aa"                     # the fixture's Reolink
    last["lan"]["cameras"] = {"count": 2, "macs": [mac, "aa:bb:cc:dd:ee:ff"],
                              "online": [mac], "offline": ["aa:bb:cc:dd:ee:ff"],
                              "detail": {"aa:bb:cc:dd:ee:ff": {"ip": "192.168.1.50", "label": "Garage cam",
                                                               "reasons": ["camera vendor (Reolink)"]}}}
    state["lan"]["cameras"] = {mac: {"first_seen": "2026-09-01T10:00:00-04:00",
                                     "last_online": "2026-09-09T10:00:00-04:00"}}
    got = _keys(last, cfg, state)
    new = got[f"lan:camera-new:aa:bb:cc:dd:ee:ff"]
    assert new["sev"] == "INFO" and "Garage cam" in new["msg"] and "2 camera(s)" in new["msg"]
    # the fixture's own camera was in the baseline and is online -> not gone
    assert "lan:camera-gone:" + mac not in got

    last["lan"]["cameras"]["online"] = []
    last["lan"]["cameras"]["offline"] = [mac, "aa:bb:cc:dd:ee:ff"]
    last["lan"]["cameras"]["detail"][mac] = {"ip": "192.168.1.182", "label": "Reolink camera"}
    gone = _keys(last, cfg, state)["lan:camera-gone:" + mac]
    assert gone["sev"] == "WARN" and "stopped answering" in gone["msg"]


def test_an_exposed_camera_port_is_called_out_separately():
    last, cfg, state = _fixture()
    last["home"]["exposure"]["open_ports"] = [554, 8080]
    mac = "ec:71:db:05:5a:aa"
    last["lan"]["cameras"] = {"count": 1, "macs": [mac], "online": [mac], "offline": [],
                              "detail": {mac: {"ip": "192.168.1.182", "label": "Reolink camera"}}}
    state["lan"]["known_macs"][mac]["ports"] = {"554": "2026-09-01T10:00:00-04:00"}
    got = _keys(last, cfg, state)
    row = got["home:camera-exposed:554"]
    assert row["sev"] == "CRIT" and "Reolink camera" in row["msg"] and "VPN" in row["msg"]
    assert "home:exposed:554,8080" in got          # the generic alert still fires


def test_a_forwarded_ambiguous_port_with_no_camera_behind_it_is_not_a_camera_alert():
    """8000 and 34567 are camera-ish ports that far more often belong to
    something else. Telling the owner strangers can watch his house because a
    dev server is forwarded is exactly the cry-wolf case."""
    last, cfg, state = _fixture()
    last["home"]["exposure"]["open_ports"] = [8000]
    last["lan"]["cameras"] = {"count": 0, "macs": [], "online": [], "offline": [], "detail": {}}
    got = _keys(last, cfg, state)
    assert not [k for k in got if k.startswith("home:camera-exposed")]
    assert "home:exposed:8000" in got            # the generic exposure CRIT still fires

    # …but the same port WITH a camera serving it behind the forward does fire.
    mac = "ec:71:db:05:5a:aa"
    last["lan"]["cameras"] = {"count": 1, "macs": [mac], "online": [mac], "offline": [],
                              "detail": {mac: {"ip": "192.168.1.182", "label": "Reolink camera"}}}
    state["lan"]["known_macs"][mac]["ports"] = {"8000": "t"}
    assert "home:camera-exposed:8000" in _keys(last, cfg, state)


def test_an_exposed_camera_raises_exactly_one_critical_recommendation():
    last, cfg, state = _fixture()
    last["home"]["exposure"]["open_ports"] = [554]
    mac = "ec:71:db:05:5a:aa"
    last["lan"]["cameras"] = {"count": 1, "macs": [mac], "online": [mac], "offline": [],
                              "detail": {mac: {"ip": "192.168.1.182", "label": "Reolink camera"}}}
    state["lan"]["known_macs"][mac]["ports"] = {"554": "t"}
    last["alerts"] = alerts.build_alerts(copy.deepcopy(last), cfg, copy.deepcopy(state))
    recs = alerts.recommendations(copy.deepcopy(last), cfg, copy.deepcopy(state))
    hits = [r for r in recs if r["key"].startswith("home:camera-exposed")]
    assert len(hits) == 1 and hits[0]["priority"] == "Critical"


def test_camera_ports_are_swept_and_probed_from_outside():
    from guardian_sensor import home as home_mod
    assert {554, 8554, 37777, 34567} <= set(lan.LAN_PORTS)
    assert {554, 8554, 37777, 34567} <= set(home_mod.EXPOSURE_PORTS)


# ---------------- the WAN pin ----------------

def test_no_pin_means_no_wan_finding():
    last, cfg, state = _fixture()
    assert not [k for k in _keys(last, cfg, state) if k.startswith("wan:") and "unexpected" in k]


def test_a_pinned_network_flags_an_unexpected_egress_address():
    last, cfg, state = _fixture()
    cfg = config.normalise_networks({**cfg, "networks": [
        {"name": "Home network", "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1",
         "expected_wan_ip": "198.51.100.105"}]})
    assert not [k for k in _keys(last, cfg, state) if "unexpected" in k]   # matches the pin
    last["wan_ip"] = "203.0.113.9"
    row = _keys(last, cfg, state)["wan:home-network:unexpected"]
    assert row["sev"] == "WARN" and "203.0.113.9" in row["msg"] and "198.51.100.105" in row["msg"]


def test_the_pin_travels_through_the_config_entry():
    cfg = config.load(str(FX / "config.json"))
    cfg["networks"] = [{"name": "Lab", "subnet_prefix": "10.10.0", "expected_wan_ip": "1.2.3.4"}]
    assert config.networks_of(cfg)[0]["expected_wan_ip"] == "1.2.3.4"


# ---------------- rogue devices ----------------

def test_an_off_subnet_neighbour_is_reported_but_never_probed():
    macs = {"192.168.1.20": "aa:bb:cc:dd:ee:01",       # on this network
            "10.9.9.9": "aa:bb:cc:dd:ee:02",           # off-subnet, private -> reported
            "10.10.0.4": "aa:bb:cc:dd:ee:03",          # another network of ours -> ignored
            "100.101.1.2": "aa:bb:cc:dd:ee:04",        # Tailscale CGNAT -> ignored
            "8.8.8.8": "aa:bb:cc:dd:ee:05",            # public -> ignored
            "169.254.5.5": "aa:bb:cc:dd:ee:06",        # link-local -> ignored
            "192.168.1.255": "ff:ff:ff:ff:ff:ff"}
    out = lan.offsubnet(macs, "192.168.1", ["10.10.0"], lambda m: m)
    assert out == {"10.9.9.9": "aa:bb:cc:dd:ee:02"}


def test_off_subnet_and_second_router_alerts():
    last, cfg, state = _fixture()
    last["lan"]["offsubnet"] = {"10.9.9.9": "aa:bb:cc:dd:ee:02"}
    got = _keys(last, cfg, state)
    row = got["lan:offsubnet:aa:bb:cc:dd:ee:02"]
    assert row["sev"] == "WARN" and "10.9.9.9" in row["msg"] and "192.168.1.0/24" in row["msg"]

    # A device answering DNS + a web admin page, that is not the router.
    mac = "d8:be:65:72:45:31"
    state["lan"]["known_macs"][mac]["ports"] = {"53": "t", "80": "t"}
    row = _keys(last, cfg, state)["lan:second-router:" + mac]
    assert row["sev"] == "WARN" and "second router" in row["msg"] and "Pi-hole" in row["msg"]


def test_the_router_itself_and_a_trusted_box_are_not_second_routers():
    last, cfg, state = _fixture()
    got = _keys(last, cfg, state)
    assert not [k for k in got if k.startswith("lan:second-router")]   # the gateway serves 53+80+443
    mac = "d8:be:65:72:45:31"
    state["lan"]["known_macs"][mac].update(ports={"53": "t", "80": "t"}, trusted=True)
    assert not [k for k in _keys(last, cfg, state) if k.startswith("lan:second-router")]


def test_a_lease_that_names_a_different_router_is_a_rogue_uplink():
    last, cfg, state = _fixture()
    last["rogue"]["dhcp_router"] = "192.168.1.66"
    row = _keys(last, cfg, state)["home:rogue-uplink"]
    assert row["sev"] == "CRIT" and "192.168.1.66" in row["msg"] and "own uplink" in row["msg"]


def test_a_secondary_network_gets_its_own_rogue_keys_only_when_verified():
    last, cfg, state = _fixture()
    cfg = config.normalise_networks({**cfg, "networks": [
        {"name": "Home network", "subnet_prefix": "192.168.1", "expected_gateway": "192.168.1.1"},
        {"name": "Lab", "subnet_prefix": "10.10.0", "expected_gateway": "10.10.0.1"}]})
    last["networks"] = [
        {**last["lan"], "slug": "home-network", "primary": True, "status": "active",
         "name": "Home network", "expected_gateway": "192.168.1.1", "subnet_prefix": "192.168.1"},
        {"slug": "lab", "primary": False, "status": "active", "name": "Lab",
         "expected_gateway": "10.10.0.1", "subnet_prefix": "10.10.0"},
    ]
    last["rogues"] = {"home-network": last["rogue"], "lab": {"verified": False}}
    assert not [k for k in _keys(last, cfg, state) if k.endswith(":lab")]
    last["rogues"]["lab"] = {"dhcp_server": "10.10.0.77", "default_gateway": "10.10.0.1"}
    got = _keys(last, cfg, state)
    assert got["home:rogue-dhcp:lab"]["sev"] == "CRIT" and "10.10.0.77" in got["home:rogue-dhcp:lab"]["msg"]


# ---------------- key round-tripping ----------------

@pytest.mark.parametrize("sub", ["camera-new", "camera-gone", "offsubnet", "second-router"])
def test_every_new_sub_key_round_trips_through_the_network_namespace(sub):
    net = {"slug": "lab", "primary": False}
    key = alerts.lan_key(sub, net, mac="aa:bb")
    assert key == f"lan:lab:{sub}:aa:bb"
    assert alerts.canonical_lan_key(key) == f"lan:{sub}:aa:bb"
    assert sub in config.LAN_SUBKEYS
    assert config.slugify(sub) == f"{sub}-net"[:config.SLUG_MAX].rstrip("-")


def test_the_new_immediate_keys_are_matched_in_canonical_form():
    for sub in ("camera-gone", "offsubnet", "second-router"):
        key = alerts.lan_key(sub, {"slug": "lab", "primary": False}, mac="aa:bb")
        assert alerts.canonical_lan_key(key).startswith(runner.IMMEDIATE)


def test_every_new_key_carries_a_confidence():
    last, cfg, state = _fixture()
    last["lan"]["offsubnet"] = {"10.9.9.9": "aa:bb:cc:dd:ee:02"}
    last["home"]["exposure"]["open_ports"] = [554]
    last["rogue"]["dhcp_router"] = "192.168.1.66"
    for a in alerts.build_alerts(copy.deepcopy(last), cfg, copy.deepcopy(state)):
        assert a["confidence"] and a["confidence"] > 0
