"""Reading the gateway's per-station Wi-Fi table.

Why it exists: Vault decides a device is present with a ping sweep plus
ARP. A device in Wi-Fi power save ignores pings, so it looks offline, and one
that keeps "leaving and rejoining" may never have left. Signal strength and
association uptime are known only to the router, so without this Vault can
say a device is flapping but not one true word about why.

The shapes below are REAL, captured from a live CR1000A. The data does not
come from db.cgi -- that was a wrong guess that answered 200 with an empty
body. The SPA loads `/cgi/cgi_owl.js`, a JavaScript file of
`addROD("<name>", {...});` calls.
"""
import guardian_sensor.router_fios as rf

# Trimmed from the real response for f8:89:d2:cc:b7:ce ("Staffphone").
STAFFPHONE = {
    "station_mac": "F8:89:D2:CC:B7:CE", "station_name": "Staffphone",
    "station_ip": "192.168.1.166", "signal_strength": "-62",
    "signal_strength_min": "-71", "signal_strength_max": "-58", "snr": "28",
    "connect_type": "5G", "channel": "100", "link_rate": "351Mbps",
    "link_rate_max": "433Mbps", "assoc_time": "86400",
    "parent_mac": "70:3E:76:94:4B:94", "connect_bssid": "70:3E:76:28:7A:F8",
    "online": "0",
}
OWL_JS = ('\n addROD("known_device_list", { "known_devices": [] });\n'
          ' addROD("dump_toplogy_station_info", { "stations": [%s] });\n'
          ' addROD("dump_toplogy_map_info", { "nodes": [{"device_mac": "70:3E:76:94:4B:94"}] });\n'
          % __import__("json").dumps(STAFFPHONE))


def test_it_parses_the_addrod_javascript():
    rod = rf.parse_rod(OWL_JS)
    assert set(rod) == {"known_device_list", "dump_toplogy_station_info", "dump_toplogy_map_info"}
    assert rod["dump_toplogy_station_info"]["stations"][0]["station_name"] == "Staffphone"


def test_a_brace_inside_a_device_name_does_not_end_the_object_early():
    """Device names are user-supplied. A regex would stop at the first `}`."""
    js = 'addROD("t", { "stations": [{"station_mac": "aa:bb", "station_name": "Bob}s {TV}"}] });'
    got = rf.parse_rod(js)["t"]["stations"][0]
    assert got["station_name"] == "Bob}s {TV}"


def test_malformed_payloads_are_skipped_not_fatal():
    rod = rf.parse_rod('addROD("good", {"a": 1}); addROD("bad", {nope});')
    assert rod.get("good") == {"a": 1} and "bad" not in rod
    assert rf.parse_rod("") == {} and rf.parse_rod(None) == {}


def test_the_signal_is_read_with_the_routers_real_field_names():
    sig = rf.read_signal([STAFFPHONE], "f8:89:d2:cc:b7:ce")
    assert sig["rssi"] == -62 and sig["snr"] == 28          # ints, not strings
    assert sig["band"] == "5G" and sig["link_rate"] == "351Mbps"
    assert sig["assoc_time"] == 86400
    assert sig["parent_mac"] == "70:3E:76:94:4B:94"


def test_mac_matching_is_case_and_separator_insensitive():
    for probe in ("F8:89:D2:CC:B7:CE", "f8:89:d2:cc:b7:ce", "f8-89-d2-cc-b7-ce"):
        assert rf.read_signal([STAFFPHONE], probe)["rssi"] == -62


def test_a_long_association_with_a_healthy_signal_says_guardian_was_wrong():
    """The whole point. Vault called this device flapping; the router says
    it has been associated for 24 hours at -62 dBm. The honest answer names
    Vault's own blind spot rather than inventing a Wi-Fi problem."""
    said = rf.explain_presence(rf.read_signal([STAFFPHONE], "f8:89:d2:cc:b7:ce"))
    assert "never left" in said and "24h" in said and "-62" in said
    assert "sleeps" in said


def test_a_genuinely_weak_signal_is_called_weak():
    weak = dict(STAFFPHONE, signal_strength="-78", assoc_time="90")
    said = rf.explain_presence(rf.read_signal([weak], "f8:89:d2:cc:b7:ce"))
    assert "Weak signal" in said and "-78" in said
    assert "closer" in said


def test_a_device_the_gateway_does_not_list_is_genuinely_off():
    assert rf.read_signal([STAFFPHONE], "00:11:22:33:44:55") == {}
    assert "not associated" in rf.explain_presence({})


def test_the_read_never_touches_apply_abstract():
    """A diagnostic that can change a router setting is not a diagnostic."""
    import ast, inspect, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(rf.station_info)))
    posts = [n.args[0].value for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "post"
             and n.args and isinstance(n.args[0], ast.Constant)]
    assert "apply_abstract.cgi" not in posts, posts
    assert "login.cgi" in posts and "logout.cgi" in posts, posts
