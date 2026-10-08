"""The threats-and-protection report: the matrix on paper.

A separate document rather than a preset of existing sections, for the same
reason `vuln_report` is separate: a preset can only select from `SECTION_KEYS`,
and there is no threats section to select. The closest preset (`security`)
would have given "alerts and fixes under a new label", which is not the thing
that was asked for.

What matters here is that it says the same thing the screen says. The report
and the Security tab read the same builders, so a customer cannot be shown one
stance in the browser and a different one in a PDF sent to their insurer.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import security as sec
from guardian_hub.reports import builder, presets as presets_mod
from tests.test_auth import seed_user


def _tenant():
    _uid, tid = seed_user()
    return tid


def test_the_report_is_offered_in_the_picker(client):
    label, sections = presets_mod.label_and_sections("threat_report")
    assert label
    assert sections == []          # a document, not a selection of sections


def test_it_names_every_attack_class_the_screen_names(client):
    """The report and the tab must not disagree about which attacks exist.
    Both read `THREAT_SPEC`, so a class added to one appears in the other."""
    tid = _tenant()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    html = builder.build_threat_report(db, tenant, days=7, toolbar=False)
    labels = [t.label for t in sec.threats(db, tenant)]
    db.close()
    for label in labels:
        assert label in html, f"{label} missing from the report"


def test_the_admission_is_printed_not_dropped(client):
    """`dos` is effect-only and `ransomware` is indirect. A PDF that listed
    them under "Threats and protection" without saying Vault cannot stop
    them would be the most misleading document this product can produce -- it
    is the one that goes to an insurer."""
    tid = _tenant()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    html = builder.build_threat_report(db, tenant, days=7, toolbar=False)
    dos = sec.threat_detail(db, tenant, "dos")
    db.close()
    assert dos.limit[:40] in html


def test_a_settled_alert_shows_what_settled_it(client):
    tid = _tenant()
    db = SessionLocal()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db.add(m.Alert(tenant_id=tid, key="email:dmarc:none", sev="CRIT",
                   msg="DMARC not enforced", status="resolved",
                   fix="dns_record_add", resolved_by="guardian",
                   first_seen=now, last_seen=now, count=1))
    db.commit()
    tenant = db.get(m.Tenant, tid)
    html = builder.build_threat_report(db, tenant, days=7, toolbar=False)
    db.close()
    assert "DMARC not enforced" in html
    assert "dns_record_add" in html


def test_it_is_a_whole_document(client):
    tid = _tenant()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    html = builder.build_threat_report(db, tenant, days=7, toolbar=False)
    db.close()
    assert html.startswith("<!doctype html>")
    assert "Confidential" in html


def test_build_report_routes_the_preset_to_this_document(client):
    """`build_report` dispatches the special presets before it touches
    `PRESETS`, the same way `exec_report` and `vuln_report` are handled."""
    tid = _tenant()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    html = builder.build_report(db, tenant, days=7, preset="threat_report", toolbar=False)
    db.close()
    assert "Threats and protection" in html
