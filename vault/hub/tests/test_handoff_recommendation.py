"""Ask Vault on a recommendation row must open a conversation, not 404."""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal

from tests.test_seed_helpers import seeded_demo


def _rec(db, tid, key, title, why="databases and admin services must not be reachable from the internet"):
    db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).delete()
    r = m.Recommendation(tenant_id=tid, key=key, priority="High", title=title, why=why, action="",
                         decision="yes", decided_by="demoadmin", status="needs-you", last_seen="2026-09-14T23:10:25-04:00")
    db.add(r); db.commit()
    return r


def test_handoff_on_a_recommendation_uses_the_alert_behind_it(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    _rec(db, tid, "sec:extport:safehome.example.com", "safehome.example.com: close internet-facing port(s) [8443]")
    now = "2026-09-14T23:00:00-04:00"
    db.query(m.Alert).filter(m.Alert.tenant_id == tid, m.Alert.key.like("sec:extport:safehome.example.com%")).delete()
    db.add(m.Alert(tenant_id=tid, key="sec:extport:safehome.example.com:8443", sev="WARN", status="resolved",
                   msg="safehome.example.com exposes port(s) [8443] to the internet", first_seen=now, last_seen=now))
    db.commit(); db.close()
    r = client.post("/api/v1/chat/handoff", json={"key": "sec:extport:safehome.example.com", "thread": None})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["key"] == "sec:extport:safehome.example.com:8443"
    assert "8443" in j["opening"] and j["thread_id"]


def test_handoff_on_a_recommendation_with_no_alert_talks_about_the_recommendation(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    _rec(db, tid, "rec:only:thing", "Turn on automatic updates on Roots", why="unpatched hosts get owned")
    db.close()
    r = client.post("/api/v1/chat/handoff", json={"key": "rec:only:thing", "thread": None})
    assert r.status_code == 200, r.text
    j = r.json()
    assert "automatic updates" in j["opening"] and "unpatched" in j["opening"]
    # nothing was minted as an alert row
    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid, key="rec:only:thing").count() == 0
    db.close()


def test_unknown_key_still_404s_with_a_clear_message(client):
    seeded_demo(client)
    r = client.post("/api/v1/chat/handoff", json={"key": "nope:nothing", "thread": None})
    assert r.status_code == 404 and "recommendation" in r.json()["detail"]
