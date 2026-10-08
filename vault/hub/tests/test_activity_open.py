"""Activity rows open: a scan shows what it found and downloads; a report
opens and downloads.

Miro: "In Activity I should be able to press on the history ... or download
each history activity that I did, if it is a report or a scan, to actually
see the scan." A scan request's queue row records only "scan requested" --
the sensor runs the scan and pushes it like any other -- so the scan a row
opens is the first one that sensor sent after the request finished, and the
drawer says so rather than implying the row stored it.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _scan_job(tid, finished):
    db = SessionLocal()
    q = m.Queue(tenant_id=tid, path="/api/run", body={}, status="done", who="demoadmin",
                result={"out": "scan requested"}, finished_at=finished,
                ts=finished - dt.timedelta(seconds=5))
    db.add(q)
    db.commit()
    qid = q.id
    db.close()
    return qid


def test_a_finished_scan_request_links_to_the_scan_that_followed_it(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    scans = sorted(db.query(m.Scan).filter_by(tenant_id=tid).all(), key=lambda s: s.ts)
    db.close()
    # A request that finished just before the second-to-last scan.
    from guardian_hub.scans import parse_ts
    target = scans[-2]
    qid = _scan_job(tid, parse_ts(target.ts) - dt.timedelta(seconds=1))
    rows = {r["id"]: r for r in client.get("/api/v1/jobs").json()["jobs"]}
    assert rows[qid]["scan_id"] == target.id


def test_a_scan_opens_as_a_summary_and_downloads_whole(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    scan = db.query(m.Scan).filter_by(tenant_id=tid).first()
    sid, ts = scan.id, scan.ts
    db.close()
    s = client.get(f"/api/v1/scans/{sid}").json()
    assert s["id"] == sid and s["ts"] == ts
    for field in ("devices", "sites_up", "sites_total", "hosts_reachable", "hosts_total", "alerts"):
        assert field in s
    r = client.get(f"/api/v1/scans/{sid}/download")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert "vault-scan-" in r.headers["content-disposition"]
    assert isinstance(r.json(), dict)


def test_another_tenants_scan_is_not_found(client):
    seeded_demo(client)
    db = SessionLocal()
    other = m.Tenant(slug="elsewhere", name="Elsewhere")
    db.add(other)
    db.flush()
    scan = m.Scan(tenant_id=other.id, ts="2026-09-27T10:00:00-04:00", raw={"secret": "x"})
    db.add(scan)
    db.commit()
    sid = scan.id
    db.close()
    assert client.get(f"/api/v1/scans/{sid}").status_code == 404
    assert client.get(f"/api/v1/scans/{sid}/download").status_code == 404


def test_reports_appear_in_activity_and_point_at_themselves(client):
    tid = seeded_demo(client)
    r = client.post("/api/v1/reports/build", json={"days": 7})
    assert r.status_code < 400, r.text
    db = SessionLocal()
    rep = db.query(m.Report).filter_by(tenant_id=tid).first()
    rid = rep.id
    db.close()
    rows = [row for row in client.get("/api/v1/jobs").json()["jobs"] if row.get("report_id") == rid]
    assert rows and rows[0]["kind"] == "report" and rows[0]["status"] == "done"
