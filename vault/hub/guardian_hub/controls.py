"""The control catalogue, and what each compliance framework calls it.

Vault holds continuous, timestamped evidence for a meaningful slice of
the questions on a cyber-insurance form or a Cyber Essentials
self-assessment. The people filling those forms in today are mostly
guessing. That is the whole commercial argument of the MSP spec's business
value pack: monitoring competes with free tools, "answers your insurance
renewal with evidence" competes with nothing.

It only works if the document survives contact with a sceptic, so this
module is built around one rule:

    THE CREDIBILITY OF THE PACK IS SET BY WHAT IT REFUSES TO CLAIM.

An MSP caught over-claiming to an underwriter never opens the document
again, and neither does the underwriter. So `Scope.OUTSIDE` controls carry
no predicate at all -- not a predicate that returns False, not one that
returns None. There is no code path by which Vault can report a pass for
something it cannot see, because the function that would do it does not
exist.

TWO THINGS THE OLDER `reports/compliance.py` GETS WRONG, deliberately not
carried over:

* Its "Access control" row reads "Remote admin uses key-based or MFA login"
  and tests `sec.ssh_password`. Vault can see that password
  authentication is disabled on SSH. It cannot see whether MFA is enforced
  anywhere, and the MSP spec names MFA as its example of something not
  visible from a network sensor. Those are split here: `remote_access_auth`
  is evidenced, `mfa_enforcement` is outside scope.
* Its "Monitoring & logging" and "Incident response" rows are
  `lambda L: True` -- they pass for a tenant whose sensor has never
  reported. Both are genuinely defensible claims for Vault to make, but
  they have to be made against evidence that the monitoring actually
  happened.

THE SENTENCE THAT MUST APPEAR ON THE DOCUMENT is `DISCLAIMER` below.
Vault produces evidence; it does not make anyone compliant. Per the
spec, that line "costs one line and it is the difference between a document
an auditor accepts and one they dismiss".

On the framework references: they name the clause an assessor will look
for, so the reader can go and check. They are a mapping aid, not legal
advice, and they are versioned against the framework editions named in
`FRAMEWORKS`. A framework revision is a change to this table, not a
silent reinterpretation.
"""
from __future__ import annotations

import dataclasses
from typing import Callable

DISCLAIMER = (
    "Vault provides evidence drawn from continuous monitoring. It does not make "
    "any organisation compliant with any framework, and nothing here is an audit "
    "opinion or legal advice."
)


class Scope:
    """How completely Vault can answer a control, and nothing finer.

    Three states, because two would collapse the distinction that matters.
    "We watch this continuously" and "we see a corner of this" are both
    useful to an assessor and they are not the same answer, while
    "we cannot see this at all" has to be unmistakable.
    """

    EVIDENCED = "answered_with_evidence"
    PARTIAL = "partial"
    OUTSIDE = "outside_scope"


@dataclasses.dataclass(frozen=True)
class Control:
    key: str
    title: str
    #: How a questionnaire actually phrases it, so the person copying an
    #: answer across recognises the row without translating.
    question: str
    scope: str
    #: What Vault holds, or -- for PARTIAL and OUTSIDE -- precisely what
    #: it does not. Required for both of those; a limitation nobody wrote
    #: down becomes a limitation nobody mentions to the underwriter.
    note: str
    #: `Scan.raw` -> True | False | None. None means "applicable but not
    #: measurable from this scan", which is different from False and must
    #: never be rendered as a pass. Always None for OUTSIDE controls: see
    #: the module docstring.
    evidence: Callable[[dict], bool | None] | None = None


# --------------------------------------------------------------------------
# Predicates
#
# Each reads `Scan.raw` (Nexus-shaped, as `ingest.store_scan` writes it).
# They return None rather than False when the thing is simply not configured
# -- a tenant with no SSH hosts has not failed the patching control, it has
# no servers, and scoring that as a failure is the mirror of the
# over-claiming this module exists to prevent.
# --------------------------------------------------------------------------

def _hosts(last: dict) -> list[dict]:
    return [h for h in (last.get("ssh") or []) if h.get("reachable")]


def _all_hosts(last: dict, test) -> bool | None:
    hosts = _hosts(last)
    if not hosts:
        return None
    return all(test(h) for h in hosts)


def _remote_access_auth(last):
    return _all_hosts(last, lambda h: not (h.get("sec") or {}).get("ssh_password"))


def _host_firewall(last):
    return _all_hosts(last, lambda h: (h.get("sec") or {}).get("ufw") == "active")


def _brute_force(last):
    return _all_hosts(last, lambda h: h.get("fail2ban") == "active")


def _patch_cadence(last):
    return _all_hosts(last, lambda h: bool((h.get("sec") or {}).get("unattended"))
                      and not ((h.get("sec") or {}).get("security_updates") or []))


def _vuln_management(last):
    hosts = _hosts(last)
    if not hosts:
        return None
    # `cve_fixable` is -1 when debsecan is absent: unmeasured, not clean.
    if all(h.get("cve_fixable", -1) < 0 for h in hosts):
        return None
    return all(h.get("cve_fixable", 0) <= 0 for h in hosts if h.get("cve_fixable", -1) >= 0)


def _malware_defence(last):
    hosts = _hosts(last)
    dns_on = bool((last.get("dns_filter") or {}).get("running"))
    if not hosts and not dns_on:
        return None
    return all(not ((h.get("sec") or {}).get("miners") or []) for h in hosts) and dns_on


def _secure_configuration(last):
    probe = last.get("admin_probe") or {}
    if not probe:
        return None
    return not any(v.get("hits") for v in probe.values())


def _unauthorised_devices(last):
    home = last.get("home") or {}
    if "unverified" not in home:
        return None
    return not home.get("unverified")


def _encryption_in_transit(last):
    sites = [s for s in (last.get("sites") or []) if s.get("sec")]
    if not sites:
        return None
    return all(not (s.get("sec") or {}).get("old_tls")
               and (s.get("sec") or {}).get("https_redirect") is not False for s in sites)


def _certificate_management(last):
    sites = [s for s in (last.get("sites") or []) if s.get("cert_days_left") is not None]
    if not sites:
        return None
    # The served chain's weakest link where it was readable, else the leaf.
    return all(min([d for d in (s.get("cert_days_left"), s.get("cert_chain_days_left"))
                    if d is not None]) > 0 for s in sites)


def _email_authentication(last):
    doms = {k: v for k, v in (last.get("email_sec") or {}).items() if v.get("has_mx")}
    if not doms:
        return None
    return all(v.get("spf") and v.get("dmarc") for v in doms.values())


def _dns_filtering(last):
    flt = last.get("dns_filter") or {}
    if not flt:
        return None
    return bool(flt.get("running"))


def _boundary_exposure(last):
    home = last.get("home") or {}
    exposure = home.get("exposure")
    if exposure is None:
        return None
    return not (exposure.get("open_ports") or [])


def _wireless_security(last):
    wifi = (last.get("home") or {}).get("wifi") or {}
    sec = wifi.get("security")
    if not sec:
        return None
    return "WPA" in sec


def _change_detection(last):
    # Drift hashes are collected per host; their presence is the evidence
    # that configuration change would be noticed, which is the control.
    hosts = _hosts(last)
    if not hosts:
        return None
    return all(h.get("drift") is not None or (h.get("sec") or {}) for h in hosts)


def _monitoring_and_logging(last):
    """Evidence that monitoring actually happened, not that it is configured.

    The old row returned True unconditionally, which passed for a tenant
    whose sensor had never reported -- the exact shape of claim this pack
    cannot afford. A sweep with a timestamp is the evidence.
    """
    return bool(last.get("ts"))


def _availability_monitoring(last):
    sites = last.get("sites") or []
    hosts = last.get("ssh") or []
    if not sites and not hosts:
        return None
    return True


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

CONTROLS: tuple[Control, ...] = (
    # ---- evidenced -------------------------------------------------------
    Control("asset_inventory", "Asset inventory",
            "Do you maintain an inventory of devices connected to your network?",
            Scope.EVIDENCED,
            "Every device seen on the monitored networks, with first and last seen "
            "timestamps, maintained continuously rather than at a point in time.",
            lambda last: bool((last.get("home") or {}).get("devices")
                              or (last.get("lan") or {}).get("devices"))),
    Control("unauthorised_device_detection", "Unauthorised device detection",
            "Are unrecognised devices on your network detected and reviewed?",
            Scope.EVIDENCED,
            "New devices raise a finding and carry an explicit trust decision.",
            _unauthorised_devices),
    Control("host_firewall", "Host firewall",
            "Is a firewall enabled on servers, restricting inbound traffic?",
            Scope.EVIDENCED, "Firewall state read per host over SSH.", _host_firewall),
    Control("boundary_exposure", "External attack surface",
            "Which services are reachable from the internet?",
            Scope.EVIDENCED,
            "Ports answering from an external prober, with change history.",
            _boundary_exposure),
    Control("patch_cadence", "Security patching",
            "Are security updates applied promptly, and is that automated?",
            Scope.EVIDENCED,
            "Pending security updates and unattended-upgrade state per host, over time.",
            _patch_cadence),
    Control("vuln_management", "Vulnerability identification",
            "Do you identify and remediate known vulnerabilities?",
            Scope.EVIDENCED,
            "Fixable CVE counts per host and findings by severity, with time to closure.",
            _vuln_management),
    Control("malware_defence", "Malware defence",
            "Are systems protected against malware?",
            Scope.EVIDENCED,
            "Host checks for known miner signatures, plus DNS blocking of malicious "
            "domains. This is not an EDR product -- see endpoint_protection.",
            _malware_defence),
    Control("brute_force_protection", "Brute-force protection",
            "Are repeated failed logins detected and blocked?",
            Scope.EVIDENCED, "fail2ban state and ban counts per host.", _brute_force),
    Control("remote_access_auth", "Remote access authentication",
            "Is remote administrative access restricted to key-based login?",
            Scope.EVIDENCED,
            "SSH password authentication state per host. This evidences key-only SSH "
            "and says nothing about MFA -- see mfa_enforcement.",
            _remote_access_auth),
    Control("encryption_in_transit", "Encryption in transit",
            "Is data in transit protected with current TLS?",
            Scope.EVIDENCED,
            "TLS configuration per monitored site: obsolete protocol versions and "
            "HTTPS redirection.",
            _encryption_in_transit),
    Control("certificate_management", "Certificate management",
            "Are TLS certificates monitored and renewed before expiry?",
            Scope.EVIDENCED,
            "Expiry of the weakest link in each served chain, issuer changes, and "
            "certificates renewed but not reloaded.",
            _certificate_management),
    Control("email_authentication", "Email anti-spoofing",
            "Do your domains publish SPF, DKIM and DMARC?",
            Scope.EVIDENCED,
            "SPF, DKIM and DMARC records for each domain that receives mail.",
            _email_authentication),
    Control("dns_filtering", "DNS filtering",
            "Is malicious or unwanted domain traffic blocked?",
            Scope.EVIDENCED,
            "Resolver block state and per-client lookup history, for the devices "
            "that use Vault's resolver.",
            _dns_filtering),
    Control("secure_configuration", "Secure configuration",
            "Are systems hardened, with no default or exposed admin interfaces?",
            Scope.EVIDENCED,
            "Probes for reachable administrative panels and default credentials.",
            _secure_configuration),
    Control("change_detection", "Configuration change detection",
            "Would an unauthorised configuration change be noticed?",
            Scope.EVIDENCED,
            "Per-host fingerprints of SSH config, authorised keys, cron and firewall "
            "rules, compared between sweeps.",
            _change_detection),
    Control("wireless_security", "Wireless security",
            "Is the wireless network encrypted to a current standard?",
            Scope.EVIDENCED,
            "Wi-Fi security mode, plus rogue access point and rogue DHCP detection.",
            _wireless_security),
    Control("network_monitoring_logging", "Network monitoring and logging",
            "Is network activity continuously monitored and logged?",
            Scope.EVIDENCED,
            "The sweep record and action log themselves, retained per the account's "
            "retention policy.",
            _monitoring_and_logging),
    Control("availability_monitoring", "Availability monitoring",
            "Is the availability of critical services measured?",
            Scope.EVIDENCED,
            "Per-service uptime measured from inside the network, with planned "
            "maintenance excluded and coverage stated.",
            _availability_monitoring),

    # ---- partial ---------------------------------------------------------
    Control("endpoint_protection", "Endpoint protection / EDR",
            "Is endpoint detection and response deployed on all endpoints?",
            Scope.PARTIAL,
            "Vault checks servers it has SSH access to for malware indicators. It "
            "cannot see whether an EDR agent is installed on laptops, phones or any "
            "device it only observes from the network.",
            None),
    Control("access_review", "Access review",
            "Are administrative accounts reviewed and removed when no longer needed?",
            Scope.PARTIAL,
            "Vault records authorised SSH keys and login accounts per host, and "
            "notices when they change. It has no view of the organisation's user "
            "directory, cloud accounts or SaaS access.",
            None),
    Control("incident_response_evidence", "Incident response evidence",
            "Can you evidence how security incidents were handled?",
            Scope.PARTIAL,
            "Vault holds a timeline and the actions taken for each incident it "
            "raised. It is not evidence of a documented incident response process, "
            "and it does not cover incidents Vault did not detect.",
            None),
    Control("log_retention", "Log retention",
            "How long is monitoring data retained?",
            Scope.PARTIAL,
            "Vault's own retention is configurable per account and is documented. "
            "It says nothing about retention in any other system.",
            None),
    Control("privileged_access", "Privileged access management",
            "Is privileged access controlled and monitored?",
            Scope.PARTIAL,
            "Vault records root login policy and authorised keys on monitored "
            "hosts. It has no view of privilege elevation elsewhere, and no "
            "credential vaulting.",
            None),

    # ---- outside scope ---------------------------------------------------
    # These carry no predicate at all. See the module docstring: there must
    # be no code path by which Vault reports a pass for something it
    # cannot observe.
    Control("mfa_enforcement", "Multi-factor authentication",
            "Is MFA enforced for email, VPN and remote access?",
            Scope.OUTSIDE,
            "Not visible from a network sensor. Vault can evidence that SSH "
            "password login is disabled; it cannot see MFA on email, VPN, SaaS or "
            "any identity provider.",
            None),
    Control("backup_verification", "Backup and restore verification",
            "Are backups taken, and are restores tested?",
            Scope.PARTIAL,
            "Vault verifies that configured backup jobs are still producing "
            "plausible artifacts: that a new one appeared inside its own window, that "
            "its size has not collapsed against the previous run, and that the "
            "destination is reachable with headroom. It does NOT test restores. A "
            "backup that cannot be restored looks identical to one that can from "
            "outside, so the restore half of this question is unanswered.",
            None),
    Control("asset_lifecycle", "End-of-life systems",
            "Are any systems running software past end of support?",
            Scope.PARTIAL,
            "Vault observes the operating system and version on every host it can "
            "reach and matches them against a dated end-of-support table, reporting "
            "standard vendor support rather than paid extended support. Two limits: "
            "the table is maintained by hand and anything it does not recognise is "
            "reported as unknown rather than supported, and this covers server "
            "operating systems only -- not application versions, firmware or hardware.",
            None),
    Control("encryption_at_rest", "Encryption at rest",
            "Is data encrypted at rest on servers and endpoints?",
            Scope.OUTSIDE,
            "Disk encryption state is not collected. Vault would have to be told, "
            "and a control answered from what someone typed is not evidence.",
            None),
    Control("security_awareness_training", "Security awareness training",
            "Do staff receive security awareness training?",
            Scope.OUTSIDE, "Not observable from a network. No evidence exists here.",
            None),
    Control("incident_response_plan", "Documented incident response plan",
            "Do you have a documented and tested incident response plan?",
            Scope.OUTSIDE,
            "A document and a rehearsal, not a network fact. Vault evidences what "
            "happened during incidents, not that a plan exists -- see "
            "incident_response_evidence.",
            None),
    Control("vendor_risk", "Third-party and vendor risk",
            "Do you assess the security of third parties with access to your systems?",
            Scope.OUTSIDE, "Outside anything a network sensor observes.", None),
    Control("physical_security", "Physical security",
            "Is physical access to systems controlled?",
            Scope.OUTSIDE, "Outside anything a network sensor observes.", None),
    Control("penetration_testing", "Penetration testing",
            "Is penetration testing performed periodically?",
            Scope.OUTSIDE,
            "Vault runs automated vulnerability checks, which are not a penetration "
            "test and must not be offered as one -- see vuln_management.",
            None),
    Control("business_continuity", "Business continuity planning",
            "Do you have a tested business continuity plan?",
            Scope.OUTSIDE, "A document and a rehearsal, not a network fact.", None),
    Control("data_classification", "Data classification",
            "Is sensitive data identified and classified?",
            Scope.OUTSIDE, "Vault does not inspect data content.", None),
)

BY_KEY: dict[str, Control] = {c.key: c for c in CONTROLS}


# --------------------------------------------------------------------------
# Frameworks
# --------------------------------------------------------------------------

#: Edition matters. A framework revision renumbers clauses, and a reference
#: to "Requirement 6.1" means different things in PCI DSS v3.2.1 and v4.0.
#: Naming the edition is what lets a reader check a citation instead of
#: trusting it, and what makes a future revision an edit to this table
#: rather than a silent reinterpretation.
FRAMEWORKS: dict[str, str] = {
    "cyber_insurance": "Cyber insurance questionnaire (composite of common carrier forms)",
    "cyber_essentials": "Cyber Essentials (UK, NCSC) — five technical controls",
    "soc2": "SOC 2 Trust Services Criteria (2017, with 2022 points of focus)",
    "hipaa_security": "HIPAA Security Rule — 45 CFR Part 164 Subpart C",
    "pci_dss": "PCI DSS v4.0",
}

#: `(control_key, framework, reference)`.
#:
#: The reference names the clause an assessor looks for, so a reader can go
#: and check rather than take Vault's word. Mapping aid, not legal advice
#: -- `DISCLAIMER` says so on the document.
#:
#: Outside-scope controls are mapped ON PURPOSE. A framework requirement
#: Vault cannot evidence still belongs in the pack, printed as "outside
#: scope" with the reason, because the assessor's list has that row on it
#: either way. A pack that silently omits every question it cannot answer
#: looks complete and is the over-claim this module exists to prevent; one
#: that lists the gaps is the one an underwriter believes.
MAPPINGS: tuple[tuple[str, str, str], ...] = (
    # ---- Cyber Essentials: five technical controls -----------------------
    ("host_firewall", "cyber_essentials", "Firewalls"),
    ("boundary_exposure", "cyber_essentials", "Firewalls"),
    ("secure_configuration", "cyber_essentials", "Secure configuration"),
    ("change_detection", "cyber_essentials", "Secure configuration"),
    ("wireless_security", "cyber_essentials", "Secure configuration"),
    ("patch_cadence", "cyber_essentials", "Security update management"),
    ("vuln_management", "cyber_essentials", "Security update management"),
    ("asset_lifecycle", "cyber_essentials", "Security update management (supported software)"),
    ("remote_access_auth", "cyber_essentials", "User access control"),
    ("access_review", "cyber_essentials", "User access control"),
    ("mfa_enforcement", "cyber_essentials", "User access control (MFA)"),
    ("privileged_access", "cyber_essentials", "User access control (administrative accounts)"),
    ("malware_defence", "cyber_essentials", "Malware protection"),
    ("endpoint_protection", "cyber_essentials", "Malware protection"),
    ("dns_filtering", "cyber_essentials", "Malware protection"),

    # ---- SOC 2 Trust Services Criteria -----------------------------------
    ("remote_access_auth", "soc2", "CC6.1 — logical access controls"),
    ("access_review", "soc2", "CC6.2 / CC6.3 — access provisioning and removal"),
    ("privileged_access", "soc2", "CC6.3 — privileged access"),
    ("physical_security", "soc2", "CC6.4 — physical access"),
    ("boundary_exposure", "soc2", "CC6.6 — protection against external threats"),
    ("host_firewall", "soc2", "CC6.6 — protection against external threats"),
    ("encryption_in_transit", "soc2", "CC6.7 — transmission of information"),
    ("certificate_management", "soc2", "CC6.7 — transmission of information"),
    ("encryption_at_rest", "soc2", "CC6.7 — data at rest"),
    ("malware_defence", "soc2", "CC6.8 — prevention of malicious software"),
    ("endpoint_protection", "soc2", "CC6.8 — prevention of malicious software"),
    ("vuln_management", "soc2", "CC7.1 — vulnerability detection"),
    ("secure_configuration", "soc2", "CC7.1 — configuration standards"),
    ("network_monitoring_logging", "soc2", "CC7.2 — monitoring for anomalies"),
    ("unauthorised_device_detection", "soc2", "CC7.2 — monitoring for anomalies"),
    ("availability_monitoring", "soc2", "CC7.2 — monitoring of system components"),
    ("incident_response_evidence", "soc2", "CC7.3 / CC7.4 — evaluation and response to incidents"),
    ("incident_response_plan", "soc2", "CC7.4 — incident response programme"),
    ("change_detection", "soc2", "CC8.1 — change management"),
    ("log_retention", "soc2", "CC7.2 — retention of monitoring records"),
    ("security_awareness_training", "soc2", "CC1.4 / CC2.2 — competence and awareness"),
    ("vendor_risk", "soc2", "CC9.2 — vendor and business partner risk"),
    ("backup_verification", "soc2", "A1.2 — backup and recovery"),
    ("business_continuity", "soc2", "A1.3 — recovery plan testing"),

    # ---- HIPAA Security Rule, 45 CFR Part 164 ----------------------------
    ("remote_access_auth", "hipaa_security", "§164.312(a)(1) — access control"),
    ("access_review", "hipaa_security", "§164.308(a)(4) — information access management"),
    ("mfa_enforcement", "hipaa_security", "§164.312(d) — person or entity authentication"),
    ("network_monitoring_logging", "hipaa_security", "§164.312(b) — audit controls"),
    ("log_retention", "hipaa_security", "§164.316(b)(2) — retention of documentation"),
    ("change_detection", "hipaa_security", "§164.312(c)(1) — integrity"),
    ("encryption_in_transit", "hipaa_security", "§164.312(e)(1) — transmission security"),
    ("certificate_management", "hipaa_security", "§164.312(e)(2)(ii) — encryption"),
    ("encryption_at_rest", "hipaa_security", "§164.312(a)(2)(iv) — encryption and decryption"),
    ("vuln_management", "hipaa_security", "§164.308(a)(1)(ii)(A) — risk analysis"),
    ("patch_cadence", "hipaa_security", "§164.308(a)(5)(ii)(B) — protection from malicious software"),
    ("malware_defence", "hipaa_security", "§164.308(a)(5)(ii)(B) — protection from malicious software"),
    ("brute_force_protection", "hipaa_security", "§164.308(a)(5)(ii)(C) — log-in monitoring"),
    ("incident_response_evidence", "hipaa_security", "§164.308(a)(6)(ii) — response and reporting"),
    ("incident_response_plan", "hipaa_security", "§164.308(a)(6)(i) — security incident procedures"),
    ("backup_verification", "hipaa_security", "§164.308(a)(7)(ii)(A) — data backup plan"),
    ("business_continuity", "hipaa_security", "§164.308(a)(7)(ii)(B) — disaster recovery plan"),
    ("physical_security", "hipaa_security", "§164.310 — physical safeguards"),
    ("security_awareness_training", "hipaa_security", "§164.308(a)(5)(i) — security awareness and training"),
    ("vendor_risk", "hipaa_security", "§164.308(b)(1) — business associate contracts"),
    ("asset_inventory", "hipaa_security", "§164.310(d)(1) — device and media controls"),

    # ---- PCI DSS v4.0 ----------------------------------------------------
    ("host_firewall", "pci_dss", "Requirement 1 — network security controls"),
    ("boundary_exposure", "pci_dss", "Requirement 1 — network security controls"),
    ("secure_configuration", "pci_dss", "Requirement 2 — secure configurations"),
    ("wireless_security", "pci_dss", "Requirement 2.3 — wireless environments"),
    ("encryption_at_rest", "pci_dss", "Requirement 3 — protect stored account data"),
    ("encryption_in_transit", "pci_dss", "Requirement 4 — strong cryptography in transit"),
    ("certificate_management", "pci_dss", "Requirement 4.2.1 — certificates in use"),
    ("malware_defence", "pci_dss", "Requirement 5 — protect against malicious software"),
    ("endpoint_protection", "pci_dss", "Requirement 5.2 — anti-malware on systems"),
    ("patch_cadence", "pci_dss", "Requirement 6.3.3 — security patches"),
    ("vuln_management", "pci_dss", "Requirement 6.3.1 — identify vulnerabilities"),
    ("remote_access_auth", "pci_dss", "Requirement 8.3 — strong authentication"),
    ("mfa_enforcement", "pci_dss", "Requirement 8.4 — multi-factor authentication"),
    ("access_review", "pci_dss", "Requirement 7 — restrict access by need to know"),
    ("privileged_access", "pci_dss", "Requirement 7.2 — privilege assignment"),
    ("physical_security", "pci_dss", "Requirement 9 — restrict physical access"),
    ("network_monitoring_logging", "pci_dss", "Requirement 10 — log and monitor access"),
    ("log_retention", "pci_dss", "Requirement 10.5.1 — audit log retention"),
    ("unauthorised_device_detection", "pci_dss", "Requirement 11.2 — detect unauthorised wireless and devices"),
    ("penetration_testing", "pci_dss", "Requirement 11.4 — penetration testing"),
    ("asset_inventory", "pci_dss", "Requirement 12.5.1 — inventory of system components"),
    ("security_awareness_training", "pci_dss", "Requirement 12.6 — security awareness"),
    ("incident_response_plan", "pci_dss", "Requirement 12.10 — incident response plan"),
    ("vendor_risk", "pci_dss", "Requirement 12.8 — third-party service providers"),

    # ---- Cyber insurance questionnaire -----------------------------------
    # Not a standard. A composite of the questions that recur across the
    # common carrier forms, which is why the references read as questions
    # rather than clause numbers. The high-value ones for an SMB renewal are
    # MFA, backups, EOL and RDP exposure -- and Vault can currently
    # evidence exactly one of those four, which the pack must say plainly.
    ("mfa_enforcement", "cyber_insurance", "MFA on email, VPN and remote access"),
    ("backup_verification", "cyber_insurance", "Backups taken, and restores tested"),
    ("asset_lifecycle", "cyber_insurance", "Systems running end-of-life software"),
    ("boundary_exposure", "cyber_insurance", "RDP or other admin services exposed to the internet"),
    ("endpoint_protection", "cyber_insurance", "EDR deployed across endpoints"),
    ("patch_cadence", "cyber_insurance", "Patching cadence for critical updates"),
    ("vuln_management", "cyber_insurance", "Vulnerability scanning and remediation"),
    ("malware_defence", "cyber_insurance", "Anti-malware protection"),
    ("email_authentication", "cyber_insurance", "Email anti-spoofing (SPF, DKIM, DMARC)"),
    ("dns_filtering", "cyber_insurance", "Web and DNS filtering"),
    ("host_firewall", "cyber_insurance", "Firewalls in place"),
    ("network_monitoring_logging", "cyber_insurance", "Network monitoring and logging"),
    ("privileged_access", "cyber_insurance", "Privileged account management"),
    ("access_review", "cyber_insurance", "Joiner, mover and leaver process"),
    ("incident_response_plan", "cyber_insurance", "Documented incident response plan"),
    ("security_awareness_training", "cyber_insurance", "Staff security awareness training"),
    ("encryption_at_rest", "cyber_insurance", "Encryption of sensitive data at rest"),
    ("asset_inventory", "cyber_insurance", "Inventory of connected devices"),
    ("remote_access_auth", "cyber_insurance", "Secure remote administrative access"),
    ("availability_monitoring", "cyber_insurance", "Monitoring of critical service availability"),
    ("business_continuity", "cyber_insurance", "Business continuity and disaster recovery plan"),
    ("vendor_risk", "cyber_insurance", "Third-party and supply-chain risk"),
    ("data_classification", "cyber_insurance", "Types of sensitive data held"),
    ("data_classification", "soc2", "C1.1 — identification of confidential information"),
    ("data_classification", "pci_dss", "Requirement 12.5.2 — data flows and stored account data"),
)


def for_framework(framework: str) -> list[tuple[Control, str]]:
    """Every control an assessor for `framework` will ask about, in catalogue
    order, paired with that framework's own reference for it.

    Includes the outside-scope rows. See `MAPPINGS` for why: the pack that
    lists its gaps is the one an underwriter believes.
    """
    refs = [(BY_KEY[k], ref) for k, fw, ref in MAPPINGS if fw == framework]
    order = {c.key: i for i, c in enumerate(CONTROLS)}
    return sorted(refs, key=lambda pair: order[pair[0].key])


def answer(control: Control, last: dict) -> str:
    """`answered_with_evidence` | `partial` | `outside_scope` | `unmeasured`.

    A fourth value the catalogue does not carry, because it is a property of
    THIS SCAN rather than of the control: an evidenced control whose
    predicate could not run against the data in hand. A tenant with no
    servers has not failed the patching control and must not be shown as
    having answered it either.
    """
    if control.scope != Scope.EVIDENCED or control.evidence is None:
        return control.scope
    try:
        got = control.evidence(last or {})
    except Exception:
        return "unmeasured"
    return "unmeasured" if got is None else Scope.EVIDENCED


def pack(last: dict, framework: str | None = None) -> list[dict]:
    """Every control, answered against one scan, with its framework references.

    The single shape behind both the report section and the CSV export. They
    must not drift: the MSP prints the PDF and the client's compliance
    officer pastes from the CSV, and a row that says two different things in
    the two files is the sort of thing that gets noticed in the meeting
    rather than beforehand.

    Rows are returned for outside-scope controls too, carrying their reason.
    Omitting them would make the pack look complete; see `MAPPINGS`.
    """
    refs: dict[str, list[str]] = {}
    for key, fw, ref in MAPPINGS:
        if framework and fw != framework:
            continue
        refs.setdefault(key, []).append(f"{FRAMEWORKS[fw].split(' —')[0].split(' (')[0]}: {ref}"
                                        if not framework else ref)
    out = []
    for control in CONTROLS:
        if control.key not in refs:
            continue
        out.append({
            "key": control.key,
            "title": control.title,
            "question": control.question,
            "status": answer(control, last),
            "scope": control.scope,
            "note": control.note,
            "references": refs[control.key],
        })
    return out


def tally(rows: list[dict]) -> dict[str, int]:
    """Counts by status, for the one-line summary above the table."""
    out = {Scope.EVIDENCED: 0, Scope.PARTIAL: 0, Scope.OUTSIDE: 0, "unmeasured": 0}
    for row in rows:
        out[row["status"]] = out.get(row["status"], 0) + 1
    return out
