"""A "security updates pending" alert is an advisory, not a problem to nag
about, when the host installs them automatically.

Reported: guardian-hub (which runs unattended-upgrades) sent a WARN "2 security
updates pending (nginx, nginx-common) -- please run apt yourself" to Slack, two
minutes AFTER the box's own unattended-upgrades had already installed them. On a
box that auto-patches, this should be an INFO advisory that stays in the app
(never pushed, per the delivery policy), not a WARN that punts to the owner.
"""
from guardian_sensor import alerts


def _th():
    return {"disk_pct_crit": 999, "disk_pct_warn": 999, "mem_pct_warn": 999,
            "load_per_core_warn": 999, "require_fail2ban": False, "failed_ssh_24h_warn": 999}


def _host(sec):
    return {"name": "guardian-hub", "reachable": True, "optional": False,
            "disk_pct": 1, "mem_pct": 1, "load_per_core": 0.1, "load1": 0.1,
            "failed_ssh_24h": 0, "reboot_required": False, "docker": [],
            "services": {}, "updates": {}, "sec": sec}


def _alerts_for(sec):
    host = _host(sec)
    res = {"sites": [], "ssh": [host], "wan_ip": None}
    cfg = {"thresholds": _th(), "ssh_hosts": [host]}
    return {a["key"]: a for a in alerts.build_alerts(res, cfg, {})}


def test_auto_patching_host_files_security_updates_as_info():
    got = _alerts_for({"security_updates": ["nginx", "nginx-common"], "unattended": True})
    a = got["sec:secupd:guardian-hub"]
    assert a["sev"] == "INFO"
    assert "installing automatically" in a["msg"]


def test_host_without_auto_updates_still_warns():
    got = _alerts_for({"security_updates": ["nginx", "nginx-common"], "unattended": False})
    a = got["sec:secupd:guardian-hub"]
    assert a["sev"] == "WARN"
    # and it also flags that auto-updates are off (the real fix)
    assert "sec:unatt:guardian-hub" in got
