"""A watched IP or an always-on device must miss TWO consecutive sweeps before
it alerts: an ARP sweep routinely skips a sleeping phone or a device in
Wi-Fi power-save, and one miss used to be a WARN."""
from guardian_sensor import alerts


def _run(net, state):
    got = []
    alerts._lan_alerts({"ts": "2026-09-16T10:00:00"}, {"lan": {}}, state, net,
                       lambda sev, key, msg: got.append((sev, key, msg)))
    return got


def _net(**kw):
    base = {"devices": {}, "names": {}, "cameras": [], "port_events": [], "slug": "primary", "primary": True}
    base.update(kw)
    return base


def test_watched_ip_needs_two_missed_sweeps():
    state = {}
    net = _net(watch_ips={"192.168.1.50": False}, watch_ips_labels={"192.168.1.50": "Printer"})
    first = _run(net, state)
    assert not any("missing" in k and "192.168.1.50" in k for _, k, _ in first)   # one miss: quiet
    second = _run(net, state)
    assert any("missing" in k and "192.168.1.50" in k for _, k, _ in second)      # two misses: WARN
    net["watch_ips"] = {"192.168.1.50": True}
    _run(net, state)                                                             # seen again: reset
    net["watch_ips"] = {"192.168.1.50": False}
    again = _run(net, state)
    assert not any("missing" in k for _, k, _ in again)                          # back to one miss


def test_always_on_device_needs_two_offline_sweeps():
    # The primary network's known_macs live in the flat state["lan"] slice
    # (net_state falls back to it); a bare {} would be a fresh dict each call.
    state = {"lan": {"known_macs": {"aa:bb:cc:dd:ee:ff": {"presence": "OFFLINE", "always_on": True, "label": "NAS"}}}}
    net = _net()
    assert not any("offline" in k for _, k, _ in _run(net, state))
    assert any("offline" in k for _, k, _ in _run(net, state))
