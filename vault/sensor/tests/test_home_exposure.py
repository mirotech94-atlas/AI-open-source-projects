"""A dead exposure probe must say it is dead.

`external_probe_host` names a machine that knocks on the home WAN IP from
the outside. When that machine is retired the name stays in the config, the
probe quietly returns nothing, and the dashboard says only "not recently
verified" -- which reads like a timing blip rather than a check that has not
run in weeks. The misconfiguration has to name itself.
"""
from guardian_sensor import home


def test_a_retired_probe_host_reports_why_the_check_cannot_run():
    cfg = {"home": {"external_probe_host": "safehome"}, "ssh_hosts": []}
    got = home.external_exposure(cfg, "203.0.113.9")
    assert got is not None, "a missing probe host must not look like 'no probe configured'"
    assert "safehome" in got["error"]
    assert "open_ports" not in got, "an error must never look like a clean result"


def test_no_probe_configured_is_still_silence():
    """Not configuring a probe at all is a choice, not a fault."""
    assert home.external_exposure({"home": {}, "ssh_hosts": []}, "203.0.113.9") is None


def test_a_configured_host_without_a_wan_ip_stays_silent():
    cfg = {"home": {"external_probe_host": "vps"},
           "ssh_hosts": [{"name": "vps", "target": "root@198.51.100.5"}]}
    assert home.external_exposure(cfg, None) is None
