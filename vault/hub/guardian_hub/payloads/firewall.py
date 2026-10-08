"""Gateway firewall rules Vault proposes, from what its scans found.

Miro's ruling, which the whole module is shaped around: Vault
**proposes** rules with one-click apply and a timed auto-rollback, and
never writes to a router silently. Three consequences:

1. **Nothing here touches a router.** `refresh()` reads the latest scan and
   writes rows; that is all it does. Applying is a separate act a person
   starts, and `can_apply()` is the honest answer about whether Vault
   even has a way to carry it out.
2. **A proposal is stable.** `key` identifies the rule, so a finding that
   persists across scans updates one row instead of minting a new one every
   five minutes -- and a declined rule stays declined instead of coming
   back, which is how a product teaches people to ignore it.
3. **Every proposal names its evidence.** A firewall rule a customer cannot
   trace back to something Vault actually observed is a rule they will
   either apply blindly or ignore entirely. Both are worse than no rule.

What is NOT proposed is as deliberate as what is. Ports 22, 80 and 443
reachable from the internet are how a person administers and serves things;
Vault flags them elsewhere as exposure but does not propose blocking the
door somebody is standing in.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..scans import latest_scan

#: Reachable from the internet on these and Vault says nothing here.
#: SSH, HTTP and HTTPS are how a person reaches their own things; a rule
#: that blocks them is a rule that gets applied once and rolled back in a
#: panic, and the next real proposal is not read.
_EXPECTED = {22, 80, 443}

#: What an exposed port usually IS, so the reason reads like a sentence
#: instead of a port number. Only entries Vault is confident about --
#: an invented service name would be a fact it made up.
_SERVICE = {
    21: ("FTP", "sends its password in the clear"),
    23: ("Telnet", "sends its password in the clear"),
    25: ("SMTP", "is a mail relay, and an open one gets abused within hours"),
    135: ("Windows RPC", "is not something that should ever face the internet"),
    139: ("NetBIOS", "is not something that should ever face the internet"),
    445: ("Windows file sharing", "is how most ransomware spreads"),
    1433: ("Microsoft SQL Server", "is a database, and databases do not belong on the internet"),
    3306: ("MySQL", "is a database, and databases do not belong on the internet"),
    3389: ("Remote Desktop", "is the single most brute-forced service on the internet"),
    5432: ("PostgreSQL", "is a database, and databases do not belong on the internet"),
    5900: ("VNC", "is remote control of a screen, often with no password at all"),
    27017: ("MongoDB", "is a database, and databases do not belong on the internet"),
}

_HIGH_RISK = {23, 21, 445, 3389, 5900, 1433, 3306, 5432, 27017}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def describe(port: int) -> tuple[str, str]:
    """(title, because) for one exposed port."""
    name, why = _SERVICE.get(port, ("", ""))
    if name:
        return (f"Block inbound {name} (port {port}) at the router",
                f"Port {port} is reachable from the internet. {name} {why}.")
    return (f"Block inbound TCP {port} at the router",
            f"Port {port} is reachable from the internet, and nothing Vault watches "
            f"needs it to be.")


def candidates(db: DbSession, tenant) -> list[dict]:
    """The rules the latest scan's own evidence supports. Pure-ish: reads a
    scan, returns dicts, writes nothing."""
    scan = latest_scan(db, tenant.id)
    raw = (scan.raw or {}) if scan else {}
    home = raw.get("home") or {}
    exposure = home.get("exposure") or {}
    wan_ip = exposure.get("wan_ip") or raw.get("wan_ip") or ""
    seen_at = exposure.get("ts") or (scan.ts if scan else "") or _now()

    out = []
    for port in sorted({int(p) for p in (exposure.get("open_ports") or []) if str(p).isdigit()}):
        if port in _EXPECTED:
            continue
        title, because = describe(port)
        out.append({
            "key": f"block_inbound:tcp:{port}",
            "action": "block_inbound", "proto": "tcp", "port": port,
            "target": wan_ip, "title": title, "because": because,
            "evidence": (f"Seen open from outside at {wan_ip or 'your public address'}:{port}"
                         f" on {seen_at[:19] or 'the last scan'}"
                         + (f" (probe host {exposure['probe_host']})" if exposure.get("probe_host") else "")),
            "risk": "CRIT" if port in _HIGH_RISK else "WARN",
        })
    return out


def refresh(db: DbSession, tenant) -> list[m.FirewallProposal]:
    """Bring this tenant's proposals in line with the latest scan.

    A rule whose evidence is gone is marked `expired` rather than deleted:
    "this was open last week and is not now" is the sort of thing an auditor
    asks about, and a row that vanishes cannot answer.

    A DECIDED proposal is never resurrected. Declining a rule is a person
    saying "I know, and no" -- re-proposing it on the next scan would make
    the decision meaningless and train them to stop reading.
    """
    existing = {p.key: p for p in db.query(m.FirewallProposal).filter_by(tenant_id=tenant.id).all()}
    now = _now()
    live = set()
    for c in candidates(db, tenant):
        live.add(c["key"])
        row = existing.get(c["key"])
        if row is None:
            row = m.FirewallProposal(tenant_id=tenant.id, first_seen=now, **c)
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                # Two page loads at once (two tabs, or a poll landing during
                # a reload) both see no row for this key and both insert it.
                # The unique index is doing its job; losing the race is
                # normal, and the loser reads what the winner wrote rather
                # than 500ing on a read.
                db.rollback()
                existing = {p.key: p for p in
                            db.query(m.FirewallProposal).filter_by(tenant_id=tenant.id).all()}
                row = existing.get(c["key"])
                if row is None:
                    continue
            existing[c["key"]] = row
        else:
            # The evidence is refreshed even on a declined row: if anyone
            # looks at it later, it should say when it was last true.
            row.evidence, row.risk, row.because = c["evidence"], c["risk"], c["because"]
        row.last_seen = now
    for key, row in existing.items():
        if key not in live and row.status == "proposed":
            row.status = "expired"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    
    return sorted(existing.values(), key=lambda r: (r.status != "proposed", r.risk != "CRIT", r.port))


def can_apply(db: DbSession, tenant) -> tuple[bool, str]:
    """Whether Vault has any way to write a rule to this gateway.

    Today: no. The Fios client can sign in, read stations and switch remote
    administration off, but it has no firewall-rule write, and building one
    means reverse-engineering a form against somebody's live router. That
    is a decision for the person who owns the router, not something to
    acquire quietly on the way to shipping a feature -- so this returns
    False with the reason, the UI disables Apply and says it, and nobody is
    shown a button that silently does nothing.
    """
    return False, ("Vault has no write access to this gateway yet. It can propose rules "
                   "and explain them; applying one needs a router integration you set up.")
