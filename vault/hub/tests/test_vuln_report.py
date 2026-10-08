"""The vulnerability-scan report: its own document, with a seen -> fixed ->
reported trail per finding."""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.reports import presets

from tests.test_seed_helpers import seeded_demo


def _scan(db, tid, kind, target, findings, finished, score=50, who="demoadmin"):
    ts = dt.datetime.fromisoformat(finished)
    q = m.Queue(tenant_id=tid, who=who, path="/api/vuln-scan", status="done",
                body={"kind": kind, "target": target, "profile": "quick"},
                result={"kind": kind, "target": target, "profile": "quick", "findings": findings,
                        "summary": {}, "score": score, "started": finished, "finished": finished})
    q.ts = ts; q.finished_at = ts
    db.add(q)
    return q


def _seed(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    now = dt.datetime.now(dt.timezone.utc)
    t1 = (now - dt.timedelta(hours=5)).isoformat(timespec="seconds")
    t2 = (now - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    adb = {"severity": "critical", "title": "adb open on port 5555", "evidence": "Android debug bridge", "port": 5555, "fix": None, "cve": None, "target": "10.9.0.157"}
    rdp = {"severity": "high", "title": "rdp open on port 3389", "evidence": "Remote Desktop", "port": 3389, "fix": None, "cve": None, "target": "10.9.0.159"}
    vnc = {"severity": "high", "title": "vnc open on port 5900", "evidence": "VNC", "port": 5900, "fix": None, "cve": None, "target": "10.9.0.170"}
    _scan(db, tid, "lan", "lan", [adb, rdp, vnc], t1)
    _scan(db, tid, "lan", "lan", [adb, rdp], t2)          # vnc gone by the second scan
    cve = {"severity": "high", "title": "3 known CVEs with a fix available", "evidence": "debsecan", "port": None, "fix": "security_updates", "cve": "CVE-2026-1", "target": "Roots Demo"}
    _scan(db, tid, "host", "Roots Demo", [cve], t1)
    fj = m.Queue(tenant_id=tid, who="demoadmin", path="/api/fix", status="done", body={"host": "Roots Demo", "action": "security_updates"})
    fj.finished_at = now - dt.timedelta(hours=2)
    db.add(fj)
    net = db.query(m.Network).filter_by(tenant_id=tid).first()
    db.add(m.Device(tenant_id=tid, network_id=net.id, mac="aa:bb:cc:dd:ee:59", ip="10.9.0.159", name="frontdesk-pc",
                    trusted=True, label="Work laptop - RDP is company policy, accepted"))
    db.commit(); db.close()
    return tid


def test_the_preset_exists_as_its_own_document():
    assert presets.label_and_sections("vuln_report") == ("Vulnerability scan report", [])
    assert "vuln_report" not in presets.PRESETS


def test_findings_carry_their_story(client):
    _seed(client)
    r = client.get("/api/v1/reports/preview", params={"days": 7, "preset": "vuln_report"})
    assert r.status_code == 200, r.text
    html = r.text
    assert "Vulnerability scan report" in html
    # the critical ADB finding needs the owner, with the concrete instruction
    assert "adb open on port 5555" in html and "Turn ADB debugging off" in html
    # the accepted work laptop is marked accepted with the owner's own words
    assert "Accepted by you: Work laptop" in html
    # the CVE finding was fixed by a Vault job after the scan saw it
    assert "Fixed by Vault: security updates ran" in html
    # VNC disappeared between the two LAN scans
    assert "Fixed since the previous scan" in html and "vnc open on port 5900" in html
    # the summary sentence adds up
    assert "1 fixed by Vault, 1 fixed since the previous scan, 1 accepted by you, 1 need you" in html


def test_it_is_offered_in_the_reports_picker(client):
    seeded_demo(client)
    j = client.get("/api/v1/reports").json()
    assert any(p["key"] == "vuln_report" for p in j["presets"])


def test_building_it_stores_a_report_row_with_its_own_title(client):
    _seed(client)
    r = client.post("/api/v1/reports/build", json={"days": 7, "preset": "vuln_report"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Vulnerability scan report" and r.json()["preset"] == "vuln_report"
