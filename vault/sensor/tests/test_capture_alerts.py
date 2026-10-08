from guardian_sensor import alerts


def test_capture_error_becomes_a_human_tier_alert():
    res = {"capture": {"mode": "none", "error": "tcpdump not installed on the sensor", "interface": "eth1"}}
    keys = {a["key"]: a for a in alerts.evaluate_capture(res)}
    assert keys["capture:unavailable"]["sev"] == "INFO"


def test_silent_mirror_port_warns():
    res = {"capture": {"mode": "mirror", "error": "no packets on eth1 for 10 minutes", "interface": "eth1"}}
    keys = {a["key"]: a for a in alerts.evaluate_capture(res)}
    assert keys["capture:silent"]["sev"] == "WARN" and "switch" in keys["capture:silent"]["msg"]


def test_no_interface_raises_nothing():
    assert alerts.evaluate_capture({"capture": {"mode": "none", "error": "", "interface": ""}}) == []
