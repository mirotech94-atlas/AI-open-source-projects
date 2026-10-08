"""A finished manual scan goes wherever alerts go -- Telegram, Slack, the
rest -- as a report with the score and the top findings."""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.integrations import registry
from tests.test_seed_helpers import seeded_demo


def _sensor_headers(client):
    db = SessionLocal()
    s = db.query(m.Sensor).filter(m.Sensor.token_hash.isnot(None)).first()
    db.close()
    return s


def test_a_done_scan_is_delivered_as_a_report(client, monkeypatch):
    from guardian_hub.routers import sensor as sensor_router
    seeded_demo(client)
    sent = []
    monkeypatch.setattr(registry, "deliver_background", lambda tid, msg: sent.append((tid, msg)))
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    q = m.Queue(tenant_id=t.id, who="demoadmin", path="/api/vuln-scan", body={"target": "192.168.1.9"})
    q.status = "done"
    q.result = {"out": "x", "detail": {"kind": "device", "target": "192.168.1.9", "score": 71,
                "summary": {"critical": 0, "high": 2, "medium": 1, "low": 0, "info": 0},
                "findings": [{"severity": "high", "title": "telnet open on port 23", "port": 23},
                             {"severity": "high", "title": "rdp open on port 3389", "port": 3389},
                             {"severity": "medium", "title": "No host firewall"},
                             {"severity": "info", "title": "This is your router; its admin page is LAN-only"}]}}
    db.add(q); db.commit()
    sensor_router._deliver_scan_report(t, q)
    db.close()

    assert len(sent) == 1
    tid, msg = sent[0]
    assert tid == t.id and msg.kind == "report" and msg.severity == "WARN"
    assert "192.168.1.9" in msg.title and "score 71" in msg.title
    assert "2 high, 1 medium" in msg.body
    assert "telnet open on port 23 (port 23)" in msg.body
    assert "LAN-only" not in msg.body, "info lines stay out of the push"
    assert msg.dedup_key == f"vulnscan:{q.id}"


def test_a_critical_finding_rides_the_crit_route_and_a_clean_scan_the_info_route(client, monkeypatch):
    from guardian_hub.routers import sensor as sensor_router
    seeded_demo(client)
    sent = []
    monkeypatch.setattr(registry, "deliver_background", lambda tid, msg: sent.append(msg))
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    for counts in ({"critical": 1}, {}):
        q = m.Queue(tenant_id=t.id, who="x", path="/api/vuln-scan", body={"target": "h"})
        q.status = "done"; q.result = {"out": "", "detail": {"target": "h", "score": 50, "summary": counts, "findings": []}}
        db.add(q); db.commit(); sensor_router._deliver_scan_report(t, q)
    db.close()
    assert [m_.severity for m_ in sent] == ["CRIT", "INFO"]
    assert "nothing to report" in sent[1].body


def test_a_failed_scan_is_reported_as_a_warning(client, monkeypatch):
    from guardian_hub.routers import sensor as sensor_router
    seeded_demo(client)
    sent = []
    monkeypatch.setattr(registry, "deliver_background", lambda tid, msg: sent.append(msg))
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    q = m.Queue(tenant_id=t.id, who="x", path="/api/vuln-scan", body={"target": "prod"})
    q.status = "failed"; q.result = {"out": "ssh failed"}
    db.add(q); db.commit(); sensor_router._deliver_scan_report(t, q); db.close()
    assert sent[0].severity == "WARN" and "failed" in sent[0].title and "ssh failed" in sent[0].body
