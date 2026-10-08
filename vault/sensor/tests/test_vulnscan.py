"""The manual vulnerability scan: connect, read, judge -- never log in.

Everything network-facing is patched out; these test the judgement over
what a probe returned, the host-report parsing, and that the queue runner
finally answers the two paths it used to refuse.
"""
from guardian_sensor import runner, vulnscan


def _sev(findings, title_part):
    return next((f["severity"] for f in findings if title_part in f["title"]), None)


def test_a_risky_service_is_named_with_its_reason():
    f = vulnscan.rules_for_port(23, "", None, None, None)
    assert _sev(f, "telnet") == "high"
    f = vulnscan.rules_for_port(6379, "", None, None, None)
    assert _sev(f, "redis") == "critical"


def test_an_everyday_service_raises_nothing_on_its_own():
    assert vulnscan.rules_for_port(22, "SSH-2.0-OpenSSH_9.6", None, None, None) == []
    assert vulnscan.rules_for_port(443, "", {"verified": True, "days_left": 200, "old_tls": False}, {"strict-transport-security": "1", "content-security-policy": "x", "x-frame-options": "d", "x-content-type-options": "nosniff"}, 200) == []


def test_an_ancient_banner_is_a_finding_even_on_a_safe_port():
    f = vulnscan.rules_for_port(22, "SSH-2.0-OpenSSH_5.3", None, None, None)
    assert _sev(f, "Very old OpenSSH") == "high"


def test_tls_problems_are_graded():
    tls = {"verified": False, "error": "self signed", "days_left": -3, "old_tls": "TLSv1"}
    f = vulnscan.rules_for_port(443, "", tls, {}, 200)
    assert _sev(f, "self-signed") == "low"
    assert _sev(f, "expired") == "high"
    assert _sev(f, "obsolete TLSv1") == "medium"
    assert _sev(f, "Missing web security headers") == "low"


def test_host_report_is_parsed_into_findings(monkeypatch):
    report = """== LISTEN
LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))
LISTEN 0 128 0.0.0.0:3306 0.0.0.0:* users:(("mysqld",pid=2,fd=3))
LISTEN 0 128 127.0.0.1:6379 0.0.0.0:* users:(("redis",pid=3,fd=3))
== SSHD
passwordauthentication yes
permitrootlogin prohibit-password
== UFW
Status: inactive
== UNATT
0
== UPG
31
== SECUPG
4
== CVE
CVE-2024-0001 openssl high
CVE-2024-0002 curl medium
== REBOOT
yes
== SUID
/opt/x/bin/thing
== END
"""
    monkeypatch.setattr(vulnscan.util, "cfg_host", lambda cfg, name: {"target": "root@x"})
    monkeypatch.setattr(vulnscan.util, "ssh_run", lambda h, cmd, timeout=0: (0, report))
    out = vulnscan.run({"ssh_hosts": [{"name": "prod", "target": "root@x"}]}, {"target": "prod", "kind": "host"})
    f = out["findings"]
    assert _sev(f, "mysql listening on all interfaces") == "high"
    assert _sev(f, "redis") is None, "bound to localhost is not exposed"
    assert _sev(f, "SSH accepts passwords") == "high"
    assert _sev(f, "No host firewall") == "medium"
    assert _sev(f, "Unattended security upgrades are off") == "medium"
    assert _sev(f, "4 security update(s) waiting") == "high"
    assert _sev(f, "CVE-2024-0001") == "medium"
    assert _sev(f, "Reboot required") == "low"
    assert out["summary"]["high"] >= 3 and 0 <= out["score"] < 100
    # Every finding that Vault can fix names the fix.
    assert next(x for x in f if "SSH accepts passwords" in x["title"])["fix"] == "disable_ssh_password"


def test_kind_is_inferred_from_the_target(monkeypatch):
    monkeypatch.setattr(vulnscan, "scan_device", lambda ip, profile: {"kind": "device", "target": ip, "findings": []})
    monkeypatch.setattr(vulnscan, "scan_site", lambda cfg, t: {"kind": "site", "target": t, "findings": []})
    monkeypatch.setattr(vulnscan, "scan_host", lambda cfg, t: {"kind": "host", "target": t, "findings": []})
    cfg = {"ssh_hosts": [{"name": "safehome", "target": "root@1.2.3.4"}]}
    assert vulnscan.run(cfg, {"target": "192.168.1.5"})["kind"] == "device"
    assert vulnscan.run(cfg, {"target": "https://example.org/"})["kind"] == "site"
    assert vulnscan.run(cfg, {"target": "safehome"})["kind"] == "host"


def test_the_queue_runner_answers_both_paths(monkeypatch):
    seen = []
    monkeypatch.setattr(vulnscan, "run", lambda cfg, b: (seen.append(b) or {"kind": "device", "target": b["target"], "findings": [], "summary": {}, "score": 100}))
    posted = []
    class C:
        def result(self, qid, ok, out, detail=None, **kw): posted.append((qid, ok, out))
        def progress(self, *a, **k): pass
    runner.run_queue([{"id": "q1", "path": "/api/deep-dive", "body": {"target": "10.0.0.9"}},
                      {"id": "q2", "path": "/api/vuln-scan", "body": {"target": "10.0.0.9", "profile": "full"}}],
                     {"ssh_hosts": []}, {}, C())
    assert [b.get("profile") for b in seen] == ["quick", "full"]
    assert len(posted) == 2 and all(ok for _, ok, _ in posted)


def test_the_router_gets_context_instead_of_two_unfixable_lows(monkeypatch, tmp_path):
    import json
    last = {"home": {"gateway": "192.168.1.1", "exposure": {"open_ports": [8443], "wan_ip": "1.2.3.4", "probe_host": "safehome"}}}
    (tmp_path / "last.json").write_text(json.dumps(last))
    monkeypatch.setattr(vulnscan.paths, "p", lambda name: str(tmp_path / name))
    ctx = vulnscan._gateway_context("192.168.1.1", [53, 80, 443])
    assert len(ctx) == 1 and ctx[0]["severity"] == "info" and "LAN-only" in ctx[0]["title"]
    # A different device gets nothing extra.
    assert vulnscan._gateway_context("192.168.1.9", [80]) == []


def test_a_router_admin_page_open_to_the_internet_is_critical(monkeypatch, tmp_path):
    import json
    last = {"home": {"gateway": "192.168.1.1", "exposure": {"open_ports": [443, 8443], "wan_ip": "1.2.3.4", "probe_host": "safehome"}}}
    (tmp_path / "last.json").write_text(json.dumps(last))
    monkeypatch.setattr(vulnscan.paths, "p", lambda name: str(tmp_path / name))
    ctx = vulnscan._gateway_context("192.168.1.1", [80, 443])
    assert ctx[0]["severity"] == "critical" and "443" in ctx[0]["evidence"]
