"""'Security headers present': when a site has no `ssh_host` set, Vault
infers it from the address the site resolves to -- and when the address is
not one of its SSH hosts, says so with the addresses, not a bare "no SSH".

The owner's report: FlyCheap's check said "I do not have SSH access
configured for the host that serves this site" while he holds SSH to two
servers. By address those two are the Roots and Roots Demo sites, so the
same reply on THOSE sites would have been simply wrong; on FlyCheap it was
right but named nothing he could act on.
"""
from types import SimpleNamespace

from guardian_hub.payloads import security


def _tenant():
    return SimpleNamespace(config={
        "sites": [
            {"name": "Roots", "url": "https://status.example.org/health", "host": "status.example.org", "ssh_host": ""},
            {"name": "FlyCheap", "url": "https://flycheap.example.com/", "host": "flycheap.example.com", "ssh_host": ""},
        ],
        "ssh_hosts": [
            {"name": "Roots", "target": "root@203.0.113.10"},
            {"name": "Roots Demo", "target": "root@203.0.113.10"},
        ],
    })


ADDRS = {"status.example.org": {"203.0.113.10"}, "flycheap.example.com": {"203.0.113.10"},
         "203.0.113.10": {"203.0.113.10"}, "203.0.113.10": {"203.0.113.10"}}
MISSING = {"hsts": False, "nosniff": True, "frame": True}


def test_a_site_served_by_a_known_ssh_host_is_fixable_without_the_mapping(monkeypatch):
    monkeypatch.setattr(security, "_resolve", lambda host: ADDRS.get(host, set()))
    action, host, arg, why_not = security._headers_fix_target(_tenant(), "Roots", MISSING)
    assert (action, host, why_not) == ("security_headers", "Roots", "")
    assert arg == "status.example.org|hsts"


def test_a_site_on_an_unknown_server_names_the_addresses(monkeypatch):
    monkeypatch.setattr(security, "_resolve", lambda host: ADDRS.get(host, set()))
    action, host, arg, why_not = security._headers_fix_target(_tenant(), "FlyCheap", MISSING)
    assert action == "" and host == ""
    assert "203.0.113.10" in why_not, "the site's own address"
    assert "Roots (203.0.113.10)" in why_not and "Roots Demo (203.0.113.10)" in why_not
    assert "SSH hosts" in why_not


def test_an_explicit_mapping_still_wins_over_inference(monkeypatch):
    monkeypatch.setattr(security, "_resolve", lambda host: ADDRS.get(host, set()))
    t = _tenant(); t.config["sites"][1]["ssh_host"] = "Roots Demo"
    action, host, _, _ = security._headers_fix_target(t, "FlyCheap", MISSING)
    assert (action, host) == ("security_headers", "Roots Demo")


def test_dns_failure_falls_back_to_the_honest_message(monkeypatch):
    monkeypatch.setattr(security, "_resolve", lambda host: set())
    action, host, _, why_not = security._headers_fix_target(_tenant(), "FlyCheap", MISSING)
    assert action == "" and "SSH hosts" in why_not
