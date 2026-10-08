import datetime as dt
from types import SimpleNamespace
from guardian_hub import models as m, jobdispatch
from guardian_hub.db import SessionLocal
from guardian_hub.payloads._util import _live_macs
from guardian_hub.payloads.triage import _score, since_phrase
from guardian_hub.routers.slackbot import _format_reply


def test_secondary_network_presence():
    raw = {"lan": {"devices": {"a": "primary"}}, "networks": [
        {"slug": "lab", "status": "active", "devices": {"b": "secondary"}},
        {"slug": "gone", "status": "unreachable", "devices": {"c": "old"}}]}
    assert _live_macs(raw) == {"primary", "secondary"}


def test_error_result_is_rendered():
    response = SimpleNamespace(text="Failed", proposal=None, tool_steps=[SimpleNamespace(
        status="error", tool="repair", args={"host": "box"}, result={"error": "connection refused"})])
    assert "connection refused" in _format_reply(response)


def test_critical_always_precedes_a_large_warning_group():
    assert _score("CRIT", 40, 1, False)[0] > _score("WARN", 99, 20, True)[0]
    assert "every scan" not in since_phrase("2026-09-14T00:00:00Z", 20)


def estate(db):
    now = dt.datetime.now(dt.timezone.utc)
    tenant = m.Tenant(name="Example", slug="example")
    db.add(tenant); db.flush()
    a = m.Sensor(tenant_id=tenant.id, name="Office", status="active", last_seen=now)
    b = m.Sensor(tenant_id=tenant.id, name="Lab", status="active", last_seen=now)
    db.add_all([a,b]); db.flush()
    office = m.Network(tenant_id=tenant.id, name="Office", slug="office", sensor_id=a.id, is_primary=True)
    lab = m.Network(tenant_id=tenant.id, name="Lab", slug="lab", sensor_id=b.id)
    db.add_all([office,lab]); db.flush()
    return tenant,a,b,now


def test_only_suitable_sensor_claims_once(client):
    with SessionLocal() as db:
        t,a,b,now = estate(db)
        q = m.Queue(tenant_id=t.id, who="admin", path="/api/run", body={"net":"lab"})
        db.add(q); db.commit()
        assert jobdispatch.claim(db,t,a,now) == []
        assert jobdispatch.claim(db,t,b,now)[0]["id"] == q.id
        assert jobdispatch.claim(db,t,b,now) == []
        db.refresh(q)
        assert q.sensor_id == b.id and q.started_at == now


def test_future_window_waits_then_expired_window_is_missed(client):
    with SessionLocal() as db:
        t,a,b,now = estate(db)
        q = m.Queue(tenant_id=t.id, who="admin", path="/api/reboot-plan", body={},
                    status="scheduled", not_before=now+dt.timedelta(hours=1), expires_at=now+dt.timedelta(hours=2))
        db.add(q); db.commit()
        assert jobdispatch.claim(db,t,a,now) == []
        db.refresh(q); assert q.status == "scheduled"
        assert jobdispatch.claim(db,t,a,now+dt.timedelta(hours=3)) == []
        db.refresh(q); assert q.status == "missed"


def test_missing_sensor_does_not_send_job_to_another_network(client):
    with SessionLocal() as db:
        t,a,b,now = estate(db)
        b.last_seen = now-dt.timedelta(hours=2)
        q = m.Queue(tenant_id=t.id, who="admin", path="/api/run", body={"net":"lab"})
        db.add(q); db.commit()
        assert jobdispatch.claim(db,t,a,now) == []
        db.refresh(q)
        assert q.status == "queued" and "Lab" in q.wait_reason


def test_partial_target_evidence_never_verifies_a_repair():
    from guardian_hub.verification import outcome
    assert outcome("enable_ufw", {"reachable": True}) is None
    assert outcome("enable_ufw", {"sec": {"ufw": "inactive"}}) is False
    assert outcome("enable_ufw", {"sec": {"ufw": "active"}}) is True
    assert outcome("security_updates", {"sec": {"security_updates": []}}) is None
    assert outcome("security_updates", {"sec": {"security_updates": []}, "cve_fixable": 1}) is False


def test_duplicate_active_request_returns_existing_and_new_attempt_after_completion(client):
    from guardian_hub.routers.write import queue_and_audit
    with SessionLocal() as db:
        t,a,b,now = estate(db)
        first = queue_and_audit(db,t,"admin","admin","/api/run","run",body={"net":"lab"})
        second = queue_and_audit(db,t,"admin","admin","/api/run","run",body={"net":"lab"})
        assert first["id"] == second["id"] and second["existing"]
        row = db.get(m.Queue,first["id"])
        row.status = "done"; db.commit()
        third = queue_and_audit(db,t,"admin","admin","/api/run","run",body={"net":"lab"})
        assert third["id"] != first["id"]


def test_recovery_requires_a_later_scan_of_same_target(client):
    from guardian_hub.verification import reconcile
    with SessionLocal() as db:
        t,a,b,now = estate(db)
        q = m.Queue(tenant_id=t.id,who="admin",path="/api/fix",body={"host":"web","action":"enable_ufw"},
                    sensor_id=a.id,status="done",finished_at=now,result={"verification":"pending"})
        db.add(q);db.commit()
        data={"ts":(now+dt.timedelta(seconds=1)).isoformat(),"ssh":[{"name":"web","reachable":True,"sec":{"ufw":"active"}}]}
        reconcile(db,t,b,data);db.refresh(q);assert q.result["verification"] == "pending"
        reconcile(db,t,a,{**data,"ts":now.isoformat()});db.refresh(q);assert q.result["verification"] == "pending"
        reconcile(db,t,a,data);db.refresh(q);assert q.result["verification"] == "verified"


def test_recent_unreachable_network_overrides_sensor_binding(client):
    with SessionLocal() as db:
        t,a,b,now=estate(db)
        db.add(m.Scan(tenant_id=t.id,sensor_id=b.id,ts=now.isoformat(),raw={"networks":{"lab":{"status":"unreachable"}}}))
        q=m.Queue(tenant_id=t.id,who="owner",path="/api/run",body={"net":"lab"})
        db.add(q);db.commit()
        chosen,reason=jobdispatch.eligible_sensor(db,t,q,now)
        assert chosen is None and "Lab" in reason


def test_reboot_window_requires_explicit_timezone(client):
    from guardian_hub.routers.write import queue_and_audit
    from fastapi import HTTPException
    import pytest
    with SessionLocal() as db:
        t,a,b,now=estate(db)
        with pytest.raises(HTTPException) as e:
            queue_and_audit(db,t,"owner","admin","/api/reboot-plan","plan",body={"host":"box","window_start":"2099-01-01T02:00:00","window_end":"2099-01-01T03:00:00"})
        assert e.value.status_code == 422


def test_legacy_clock_only_reboot_never_runs_at_an_unexpected_date(client):
    with SessionLocal() as db:
        t,a,b,now=estate(db)
        q=m.Queue(tenant_id=t.id,who="owner",path="/api/reboot-plan",body={"host":"web","window_start":"02:00","window_end":"05:00"})
        db.add(q);db.commit()
        assert jobdispatch.claim(db,t,a,now)==[]
        db.refresh(q)
        assert q.status=="missed" and "choose a new time" in q.wait_reason
