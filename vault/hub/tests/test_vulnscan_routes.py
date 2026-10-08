"""Manual vulnerability scans: queued for the sensor, audited, kept as the
report. The sensor's own vulnscan tests cover what a scan finds; these cover
the hub's half -- who may start one, what gets queued, and how a finished
report is shaped for the Security page.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import vulnscans
from tests.test_seed_helpers import seeded_demo
from tests.test_tenants import make_super


def _tenant_id(client):
    return client.get("/api/v1/me").json()["active_tenant"]["id"]


def test_starting_a_scan_queues_the_sensor_job_and_audits_it(client):
    seeded_demo(client)
    r = client.post("/api/v1/security/vuln-scans", json={"target": "192.168.1.9", "profile": "full"})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    db = SessionLocal()
    q = db.get(m.Queue, r.json()["id"])
    assert q.path == "/api/vuln-scan" and q.body == {"target": "192.168.1.9", "profile": "full"}
    assert db.query(m.Audit).filter_by(action="vulnscan.start", target="192.168.1.9").count() == 1
    db.close()


def test_a_whole_lan_scan_needs_an_admin(client):
    seeded_demo(client)          # demoadmin is admin on demo
    assert client.post("/api/v1/security/vuln-scans", json={"target": "lan"}).status_code == 200
    # Demote to operator: still allowed one target, refused the whole LAN.
    db = SessionLocal(); tid = _tenant_id(client)
    u = db.query(m.User).filter_by(username="demoadmin").one()
    mb = db.query(m.Membership).filter_by(user_id=u.id, tenant_id=tid).one(); mb.role = "operator"; db.commit(); db.close()
    assert client.post("/api/v1/security/vuln-scans", json={"target": "lan"}).status_code == 403
    assert client.post("/api/v1/security/vuln-scans", json={"target": "192.168.1.9"}).status_code == 200


def test_bad_profile_or_kind_is_refused(client):
    seeded_demo(client)
    assert client.post("/api/v1/security/vuln-scans", json={"target": "x", "profile": "deep"}).status_code == 422
    assert client.post("/api/v1/security/vuln-scans", json={"target": "x", "kind": "planet"}).status_code == 422


def test_a_finished_report_is_shaped_for_the_page(client):
    seeded_demo(client)
    tid = _tenant_id(client)
    db = SessionLocal()
    q = m.Queue(tenant_id=tid, who="demoadmin", path="/api/vuln-scan", body={"target": "192.168.1.9", "profile": "quick"})
    q.status = "done"; q.finished_at = dt.datetime.now(dt.timezone.utc)
    q.result = {"ok": True, "out": "device 192.168.1.9: 2 finding(s)", "detail": {
        "kind": "device", "target": "192.168.1.9", "profile": "quick", "duration_s": 3.6, "score": 86,
        "summary": {"critical": 0, "high": 1, "medium": 0, "low": 1, "info": 0},
        "ports": [{"port": 23, "service": "telnet", "banner": "", "tls": None, "http": None},
                  {"port": 443, "service": "https", "banner": "", "tls": {"version": "TLSv1.3", "verified": False}, "http": 200}],
        "findings": [{"severity": "high", "title": "telnet open on port 23", "evidence": "telnet is unencrypted", "port": 23},
                     {"severity": "low", "title": "Certificate is self-signed or not trusted", "evidence": "self signed", "port": 443}]}}
    db.add(q); db.commit(); qid = q.id; db.close()

    body = client.get("/api/v1/security/vuln-scans").json()
    row = next(s for s in body["scans"] if s["id"] == qid)
    assert row["status"] == "done" and row["score"] == 86 and row["summary"]["high"] == 1
    assert row["findings"][0]["severity"] == "high" and row["findings"][0]["port"] == 23
    assert row["ports"][1]["tls_version"] == "TLSv1.3" and row["ports"][1]["tls_verified"] is False
    assert body["targets"][0]["kind"] == "lan"
    assert any(t["kind"] == "host" for t in body["targets"]) and any(t["kind"] == "device" for t in body["targets"])

    one = client.get(f"/api/v1/security/vuln-scans/{qid}").json()
    assert one["id"] == qid
    assert client.get("/api/v1/security/vuln-scans/nope").status_code == 404


def test_a_failed_scan_carries_its_error(client):
    seeded_demo(client)
    tid = _tenant_id(client)
    db = SessionLocal()
    q = m.Queue(tenant_id=tid, who="demoadmin", path="/api/vuln-scan", body={"target": "prod"})
    q.status = "failed"; q.result = {"ok": False, "out": "ssh failed"}
    db.add(q); db.commit(); db.close()
    row = client.get("/api/v1/security/vuln-scans").json()["scans"][0]
    assert row["status"] == "failed" and row["error"] == "ssh failed"


def test_the_chat_tool_queues_the_same_job(client):
    from guardian_hub.chat import registry
    from tests.test_chat_tools import ctx_for
    seeded_demo(client)
    out = registry.dispatch(ctx_for(), "vuln_scan", {"target": "192.168.1.9"})
    assert out.ok and out.queue_id
    db = SessionLocal(); q = db.get(m.Queue, out.queue_id); db.close()
    assert q.path == "/api/vuln-scan" and q.body["profile"] == "quick"
    # An operator asking for the whole LAN is told no, not silently narrowed.
    out = registry.dispatch(ctx_for(role="operator"), "vuln_scan", {"target": "lan"})
    assert out.ok is False and "admin" in out.text
