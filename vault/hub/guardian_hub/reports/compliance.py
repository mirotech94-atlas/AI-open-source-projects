"""The twelve compliance controls, ported one-for-one from Nexus `COMPLIANCE`
(`~/.claude/nexus-agent/ui/server.py:338-351`). Each predicate reads the
`last` dict -- `Scan.raw`, which is stored exactly Nexus-shaped by
`ingest.store_scan` -- so the predicates themselves need no translation.
"""

COMPLIANCE = [
    ("Access control", "Remote admin uses key-based or MFA login, not reusable passwords",
     lambda L: all(not (h.get("sec") or {}).get("ssh_password") for h in (L.get("ssh") or []) if h.get("reachable"))),
    ("Boundary firewall", "A host firewall restricts inbound to needed ports",
     lambda L: all((h.get("sec") or {}).get("ufw") == "active" for h in (L.get("ssh") or []) if h.get("reachable"))),
    ("Brute-force protection", "Repeated failed logins are automatically blocked",
     lambda L: all(h.get("fail2ban") == "active" for h in (L.get("ssh") or []) if h.get("reachable"))),
    ("Security patching", "Security updates are applied promptly and automatically",
     lambda L: all((h.get("sec") or {}).get("unattended") and not ((h.get("sec") or {}).get("security_updates") or [])
                    for h in (L.get("ssh") or []) if h.get("reachable"))),
    ("Vulnerability management", "Known vulnerabilities are tracked and remediated",
     lambda L: all(h.get("cve_fixable", 0) < 100 for h in (L.get("ssh") or []) if h.get("reachable"))),
    ("Malware defence", "Hosts checked for malware; DNS blocks malicious domains",
     lambda L: all(not ((h.get("sec") or {}).get("miners") or []) for h in (L.get("ssh") or []) if h.get("reachable"))
               and bool((L.get("dns_filter") or {}).get("running"))),
    ("Secure configuration", "Config changes detected; no leaked admin panels exposed",
     lambda L: not any(v.get("hits") for v in (L.get("admin_probe") or {}).values())),
    ("Network segmentation", "Unknown devices on the network are identified and reviewed",
     lambda L: not ((L.get("home") or {}).get("unverified"))),
    ("Encryption in transit", "Websites enforce HTTPS and reject obsolete TLS",
     lambda L: all(not (s.get("sec") or {}).get("old_tls") and (s.get("sec") or {}).get("https_redirect") is not False
                   for s in (L.get("sites") or []) if s.get("sec"))),
    ("Email anti-spoofing", "Domains publish SPF and DMARC",
     lambda L: all(v.get("spf") and v.get("dmarc") for v in (L.get("email_sec") or {}).values() if v.get("has_mx"))),
    ("Monitoring & logging", "Continuous monitoring with an audit trail", lambda L: True),
    ("Incident response", "Alerts triaged, correlated into incidents, acted on", lambda L: True),
]


def rows(last: dict) -> list[dict]:
    """Port of Nexus `compliance_rows`: `[{"area", "control", "met"}, ...]`,
    where `met` is `True`/`False`/`None` (predicate raised -- not applicable
    / unmeasurable with the current data)."""
    last = last or {}
    out = []
    for area, ctrl, test in COMPLIANCE:
        try:
            ok = bool(test(last))
        except Exception:
            ok = None
        out.append({"area": area, "control": ctrl, "met": ok})
    return out
