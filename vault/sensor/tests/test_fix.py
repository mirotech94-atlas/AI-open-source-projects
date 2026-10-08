"""Fix-engine tests: runner injection (no real SSH), cooldown key naming, the
attacker-method decision helper, the manual-only / unknown-action guards, the
apply_fix failure path, and the remediate() gating (auto_fix.enabled, the
daily cap, and per-key cooldown).

apply_fix() writes remediation.jsonl and (with lock=True) a run-lock file
under paths.DATA_DIR, so every test that calls it (directly or via
remediate()) points DATA_DIR at a tmp_path via monkeypatch rather than
touching the real ~/.guardian.
"""
import copy
import json
import pathlib

from guardian_sensor import fix, paths, util

FX = pathlib.Path(__file__).parent / "fixtures"


def _load():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    return last, cfg


def test_apply_fix_uses_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return "ok"
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}], "auto_fix": {"enabled": True, "max_per_day": 20}}
    r = fix.apply_fix(cfg, "prod", "enable_ufw", by="test", lock=False, runner=runner)
    assert r["ok"] and r["action"] == "enable_ufw" and calls


def test_attacker_method_auto():
    assert fix.pick_attacker_method({"days": 3, "fails": 100}, "auto") == "block_subnet"
    assert fix.pick_attacker_method({"days": 1, "fails": 2500}, "auto") == "ban_long"
    assert fix.pick_attacker_method({"days": 1, "fails": 400}, "auto") == "block_ip"


def test_attacker_method_passthrough_when_not_auto():
    # a non-"auto" method (e.g. an operator override) passes straight through
    assert fix.pick_attacker_method({"days": 5, "fails": 5000}, "block_ip") == "block_ip"
    assert fix.pick_attacker_method({}, "ban_long") == "ban_long"


def test_manual_only_is_reboot_only():
    assert fix.MANUAL_ONLY == {"reboot"}


def test_apply_fix_refuses_unknown_action(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return "ok"
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}]}
    r = fix.apply_fix(cfg, "prod", "not_a_real_action", by="test", lock=False, runner=runner)
    assert r["ok"] is False and not calls


def test_apply_fix_refuses_unknown_host(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return "ok"
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}]}
    r = fix.apply_fix(cfg, "no-such-host", "enable_ufw", by="test", lock=False, runner=runner)
    assert r["ok"] is False and not calls


def test_apply_fix_nonzero_rc_is_not_ok(tmp_path, monkeypatch):
    # a real ssh_run-shaped (rc, output) runner reporting a dpkg failure
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    def runner(h, cmd, timeout=120): return (1, "E: dpkg was interrupted, you must manually run 'dpkg --configure -a'")
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}], "auto_fix": {"enabled": True, "max_per_day": 20}}
    r = fix.apply_fix(cfg, "prod", "enable_ufw", by="test", lock=False, runner=runner)
    assert r["ok"] is False


def test_apply_fix_dpkg_lock_message_is_not_ok(tmp_path, monkeypatch):
    # rc==0 but the output text shows the apt run never actually completed
    # (e.g. it's masked by a `| tail` pipeline) — the E:/lock regex must
    # still catch it and refuse to call it a success.
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    def runner(h, cmd, timeout=120): return (0, "Could not get lock /var/lib/dpkg/lock - open (11: Resource temporarily unavailable)")
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}], "auto_fix": {"enabled": True, "max_per_day": 20}}
    r = fix.apply_fix(cfg, "prod", "enable_ufw", by="test", lock=False, runner=runner)
    assert r["ok"] is False


def test_remediate_disabled_returns_empty_without_calling_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    last, cfg = _load()
    cfg = copy.deepcopy(cfg)
    cfg["auto_fix"]["enabled"] = False
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return (0, "ok")
    res = fix.remediate(copy.deepcopy(last), cfg, {}, runner=runner)
    assert res == [] and not calls


def test_remediate_zero_cap_sets_flag_and_skips_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    last, cfg = _load()
    cfg = copy.deepcopy(cfg)
    cfg["auto_fix"]["max_per_day"] = 0
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return (0, "ok")
    res_arg = copy.deepcopy(last)
    done = fix.remediate(res_arg, cfg, {}, runner=runner)
    assert res_arg.get("fix_cap_hit") is True
    assert done == [] and not calls


def test_remediate_does_not_auto_run_reboot_from_a_yes(tmp_path, monkeypatch):
    # A "yes" on a reboot recommendation must still wait for the hub's own
    # tier-3 confirmation -- the unattended consent sweep must never reboot
    # a host on its own just because `rec_decisions` says "yes".
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    last, cfg = _load()
    last = copy.deepcopy(last)
    last["recommendations"] = [{"key": "reboot:demo", "priority": "High", "text": "Reboot demo",
                                 "why": "kernel update pending", "action": {"host": "demo", "action": "reboot"}}]
    state = {"rec_decisions": {"reboot:demo": {"decision": "yes", "status": "pending"}}}
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return (0, "ok")
    done = fix.remediate(last, cfg, state, runner=runner)
    # demo's fixture attacker independently triggers its own unattended
    # fixes (harden_fail2ban/block_ip) -- unrelated to this recommendation.
    # The only thing under test is that "reboot" never ran and the
    # recommendation's own consent status was never marked done/failed.
    assert not any(d["action"] == "reboot" for d in done)
    assert not any("reboot" in c for c in calls)
    assert state["rec_decisions"]["reboot:demo"]["status"] == "pending"


def test_remediate_still_runs_a_tier2_consent_action(tmp_path, monkeypatch):
    # Sanity check for the same code path: a non-disruptive action (not in
    # CONSENT_REQUIRES_CONFIRM) with a "yes" still runs unattended, exactly
    # as before this change.
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    last, cfg = _load()
    last = copy.deepcopy(last)
    last["recommendations"] = [{"key": "disk:demo", "priority": "Medium", "text": "free disk space",
                                 "why": "disk almost full", "action": {"host": "demo", "action": "cleanup_disk"}}]
    state = {"rec_decisions": {"disk:demo": {"decision": "yes", "status": "pending"}}}
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return (0, "ok")
    done = fix.remediate(last, cfg, state, runner=runner)
    assert any(d["action"] == "cleanup_disk" and d["host"] == "demo" for d in done) and calls
    assert state["rec_decisions"]["disk:demo"]["status"] == "done"


def test_remediate_cooldown_skips_due_fix(tmp_path, monkeypatch):
    # demo's attacker 192.0.2.107 (362 fails/24h, >= attacker_block_24h=300)
    # would normally trigger both harden_fail2ban and block_ip; seed cooldown
    # keys for both so remediate() skips them without ever touching the runner.
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    last, cfg = _load()
    cfg = copy.deepcopy(cfg)
    now = util.now_iso()
    state = {"fix_cooldown": {
        "harden_fail2ban:demo:": now,
        "block_ip:demo:192.0.2.107": now,
    }}
    calls = []
    def runner(h, cmd, timeout=120): calls.append(cmd); return (0, "ok")
    done = fix.remediate(copy.deepcopy(last), cfg, state, runner=runner)
    assert done == [] and not calls


# --------------------------------------------------------------------------
# security_headers: the fix for "Security headers present" the owner hit
# three times (his site is on a host Vault already has SSH to -- nobody
# had ever written the fix). These run the REAL generated bash script
# through a real shell against stub `nginx`/`systemctl` binaries, not a
# mocked runner -- the safety contract (refuse, back up, test, restore on
# failure) lives in that script, not in this Python.
# --------------------------------------------------------------------------

import os
import stat
import subprocess
import textwrap


def _write_stub(bin_dir: pathlib.Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _nginx_env(tmp_path, *, matches: int, test_rc: int, reload_rc: int = 0):
    """A fake `nginx`/`systemctl` on PATH: `-T` prints a fixture with
    `matches` server_name lines for example.com (so the "exactly one match"
    guard can be exercised both ways), `-t` exits `test_rc`, and reload
    exits `reload_rc`. Returns (env, conf_path, original_conf_text)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    conf = tmp_path / "site.conf"
    original = textwrap.dedent("""\
        server {
            listen 443 ssl;
            server_name example.com;
            root /var/www;
        }
        """)
    conf.write_text(original)

    t_lines = [f"# configuration file {conf}:"] + [original]
    t_out = "\n".join(t_lines) * matches if matches else "# configuration file %s:\nserver {}\n" % conf
    t_fixture = tmp_path / "t_output.txt"
    t_fixture.write_text(t_out)

    _write_stub(bin_dir, "nginx", textwrap.dedent(f"""\
        if [ "$1" = "-T" ]; then cat {t_fixture}; exit 0; fi
        if [ "$1" = "-t" ]; then
          if [ {test_rc} -ne 0 ]; then echo "nginx: [emerg] test failure" 1>&2; fi
          exit {test_rc}
        fi
        if [ "$1" = "-s" ]; then exit {reload_rc}; fi
        exit 0
    """))
    _write_stub(bin_dir, "systemctl", f"exit {reload_rc}\n")
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return env, conf, original


def _run(cmd: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=15)


def test_security_headers_refuses_without_confidently_matching_one_block(tmp_path):
    # Zero matches: MATCHES=0, refuse rather than guess.
    env, conf, original = _nginx_env(tmp_path, matches=0, test_rc=0)
    cmd = fix.security_headers_cmd("example.com", ["hsts", "nosniff"])
    r = _run(cmd, env)
    assert r.returncode != 0
    assert "GUARDIAN_REFUSED" in r.stdout
    assert conf.read_text() == original          # untouched


def test_security_headers_refuses_when_ambiguous(tmp_path):
    # Two server_name matches -- Vault cannot be sure which block is
    # right, so it refuses instead of picking one.
    env, conf, original = _nginx_env(tmp_path, matches=2, test_rc=0)
    cmd = fix.security_headers_cmd("example.com", ["hsts"])
    r = _run(cmd, env)
    assert r.returncode != 0
    assert "GUARDIAN_REFUSED" in r.stdout
    assert conf.read_text() == original


def test_security_headers_restores_config_and_reports_failure_when_test_fails(tmp_path):
    """The failing-test-first case: `nginx -t` rejects the edited config.
    Vault must restore the file byte-for-byte and report failure --
    never reload an untested config."""
    env, conf, original = _nginx_env(tmp_path, matches=1, test_rc=1)
    cmd = fix.security_headers_cmd("example.com", ["hsts", "nosniff", "frame"])
    r = _run(cmd, env)
    assert r.returncode != 0
    assert "GUARDIAN_FAILED" in r.stdout
    assert "nginx -t failed" in r.stdout
    assert conf.read_text() == original           # restored, not left broken


def test_security_headers_adds_directives_and_reloads_on_a_passing_test(tmp_path):
    env, conf, original = _nginx_env(tmp_path, matches=1, test_rc=0)
    cmd = fix.security_headers_cmd("example.com", ["hsts", "nosniff", "frame"])
    r = _run(cmd, env)
    assert r.returncode == 0
    assert "GUARDIAN_OK" in r.stdout
    after = conf.read_text()
    assert "Strict-Transport-Security" in after
    assert "X-Content-Type-Options" in after
    assert "X-Frame-Options" in after
    assert "root /var/www;" in after               # the rest of the file survives
    assert after != original


def test_security_headers_never_touches_csp(tmp_path):
    # csp is not in SECURITY_HEADER_DIRECTIVES -- asking for it alongside a
    # real header must add only the real one, never invent a CSP.
    env, conf, original = _nginx_env(tmp_path, matches=1, test_rc=0)
    cmd = fix.security_headers_cmd("example.com", ["hsts", "csp"])
    r = _run(cmd, env)
    assert r.returncode == 0
    after = conf.read_text()
    assert "Strict-Transport-Security" in after
    assert "Content-Security-Policy" not in after


def test_security_headers_refuses_on_an_unsafe_looking_domain():
    # `arg` is caller-controlled data threaded into a remote shell command;
    # anything that is not a plain hostname must refuse rather than be
    # interpolated.
    cmd = fix.security_headers_cmd("example.com; rm -rf /", ["hsts"])
    assert "GUARDIAN_REFUSED" in cmd
    assert "rm -rf" not in cmd


def test_apply_fix_security_headers_reports_failure_from_the_script(tmp_path, monkeypatch):
    """`apply_fix()`'s own `ok` computation must trust the script's
    GUARDIAN_FAILED/GUARDIAN_REFUSED markers, not just `rc` -- a test-style
    runner (see `test_apply_fix_uses_runner` above) that returns a bare
    string, not an (rc, out) tuple, defaults `rc` to 0."""
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    def runner(h, cmd, timeout=120):
        return "GUARDIAN_FAILED: nginx -t failed, restored /etc/nginx/site.conf from backup"
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}]}
    r = fix.apply_fix(cfg, "prod", "security_headers", "example.com|hsts,nosniff",
                      by="test", lock=False, runner=runner)
    assert r["ok"] is False


def test_apply_fix_security_headers_ok_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    def runner(h, cmd, timeout=120):
        return (0, "GUARDIAN_OK: added headers to /etc/nginx/site.conf for example.com")
    cfg = {"ssh_hosts": [{"name": "prod", "host": "1.2.3.4", "user": "root"}]}
    r = fix.apply_fix(cfg, "prod", "security_headers", "example.com|hsts,nosniff",
                      by="test", lock=False, runner=runner)
    assert r["ok"] is True


def test_security_headers_is_consent_required():
    assert "security_headers" in fix.CONSENT_REQUIRES_CONFIRM
