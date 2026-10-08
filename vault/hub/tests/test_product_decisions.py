"""Behavioral coverage for personal settings and operational boundaries."""
import datetime as dt
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.deps import COOKIE


def sign_in(client, role="admin", username="owner"):
    with SessionLocal() as db:
        tenant=m.Tenant(name="Example",slug=username)
        user=m.User(username=username,email=f"{username}@example.test")
        db.add_all([tenant,user]);db.flush()
        db.add(m.Membership(user_id=user.id,tenant_id=tenant.id,role=role))
        session=m.Session(id=m.uid(),user_id=user.id,tenant_id=tenant.id,expires=m.utcnow()+dt.timedelta(hours=1))
        db.add(session);db.commit()
        client.cookies.set(COOKIE,session.id)
        return tenant.id,user.id


def test_viewer_preferences_are_personal_and_survive_reload(client):
    tid,uid=sign_in(client,"viewer")
    assert client.get("/api/v1/preferences").json()["view"] == "customer"
    value={"view":"technician","density":"comfortable","show_ticker":False,"theme":"light",
           "saved_views":[{"name":"My decisions","url":"/inbox?status=open"}]}
    assert client.post("/api/v1/preferences",json=value).status_code == 200
    assert client.get("/api/v1/preferences").json() == value
    with SessionLocal() as db:
        assert not (db.get(m.Tenant,tid).settings or {}).get("preferences")
    sign_in(client,"viewer","other")
    assert client.get("/api/v1/preferences").json()["saved_views"] == []


def test_saved_views_cannot_navigate_to_an_external_site(client):
    sign_in(client)
    for url in ("https://example.test", "//example.test", "/\\example.test"):
        assert client.post("/api/v1/preferences",json={"saved_views":[{"name":"bad","url":url}]}).status_code == 422


def test_network_timezone_inherits_and_cannot_modify_another_tenant(client):
    tid,_=sign_in(client)
    with SessionLocal() as db:
        network=m.Network(tenant_id=tid,name="Office",slug="office")
        db.add(network);db.commit();nid=network.id
    assert client.post("/api/v1/timezone",json={"timezone":"America/New_York"}).status_code == 200
    assert client.get(f"/api/v1/timezone?network={nid}").json() == {"timezone":"America/New_York","inherited":True}
    assert client.post("/api/v1/timezone",json={"network":nid,"timezone":"Europe/London"}).status_code == 200
    assert client.get(f"/api/v1/timezone?network={nid}").json()["timezone"] == "Europe/London"
    sign_in(client,"admin","other")
    assert client.post("/api/v1/timezone",json={"network":nid,"timezone":"UTC"}).status_code == 404


def test_dns_exception_is_audited_and_does_not_disable_filtering(client):
    tid,_=sign_in(client)
    with SessionLocal() as db:
        t=db.get(m.Tenant,tid);t.config={"dns":{"enabled":True,"block":["bad.example"]}};db.commit()
    assert client.post("/api/v1/dns/exceptions",json={"domain":"service.example","hours":1}).status_code == 200
    entries=client.get("/api/v1/dns/exceptions").json()["exceptions"]
    assert entries[0]["verification"] == "Waiting for an allowed query"
    with SessionLocal() as db:
        cfg=db.get(m.Tenant,tid).config
        assert cfg["dns"]["enabled"] is True and cfg["dns"]["block"] == ["bad.example"]
        assert db.query(m.Audit).filter_by(tenant_id=tid,action="dns.exception").count() == 1
    assert client.post("/api/v1/dns/exceptions",json={"domain":"service.example","remove":True}).status_code == 200
    assert client.get("/api/v1/dns/exceptions").json()["exceptions"] == []


def test_scan_comparison_does_not_claim_missing_findings_resolved(client):
    tid,_=sign_in(client)
    now=m.utcnow()
    with SessionLocal() as db:
        old=m.Queue(tenant_id=tid,who="owner",path="/api/vuln-scan",body={"target":"host"},status="done",ts=now-dt.timedelta(hours=1),result={"detail":{"findings":[{"id":"A","title":"old"}]}})
        new=m.Queue(tenant_id=tid,who="owner",path="/api/vuln-scan",body={"target":"host"},status="done",ts=now,result={"detail":{"findings":[{"id":"B","title":"new"}]}})
        db.add_all([old,new]);db.commit();qid=new.id
    result=client.get(f"/api/v1/jobs/{qid}/comparison").json()
    assert result["comparable"] and result["new"][0]["id"] == "B"
    assert result["resolved"] == [] and result["unverified"][0]["id"] == "A"


def test_exception_policy_is_tenant_scoped_and_admin_only(client):
    sign_in(client)
    assert client.post("/api/v1/settings/exception-policy",json={"require_expiry":True}).status_code == 200
    assert client.get("/api/v1/settings/exception-policy").json()["require_expiry"] is True
    sign_in(client,"viewer","other")
    assert client.get("/api/v1/settings/exception-policy").json()["require_expiry"] is False
    assert client.post("/api/v1/settings/exception-policy",json={"require_expiry":True}).status_code == 403


def test_guided_demo_exception_changes_only_future_simulated_dns(client):
    from guardian_hub.demo_trickle import trickle_demo
    tid,_=sign_in(client,"admin","demo")
    now=m.utcnow()
    with SessionLocal() as db:
        t=db.get(m.Tenant,tid)
        before=db.query(m.Fix).count()
        trickle_demo(db,now=now)
        first=db.query(m.DnsQuery).order_by(m.DnsQuery.id.desc()).first()
        assert first.blocked and first.name == "blocked-demo.example"
        t.config={"dns":{"exceptions":[{"domain":"blocked-demo.example","expires":(now+dt.timedelta(minutes=1)).isoformat()}]}}
        db.commit()
        trickle_demo(db,now=now+dt.timedelta(seconds=15))
        assert db.query(m.DnsQuery).order_by(m.DnsQuery.id.desc()).first().blocked is False
        trickle_demo(db,now=now+dt.timedelta(minutes=2))
        assert db.query(m.DnsQuery).order_by(m.DnsQuery.id.desc()).first().blocked is True
        assert db.query(m.Fix).count() == before


def test_named_slack_approver_is_explicit_and_survives_restart(client, monkeypatch):
    from types import SimpleNamespace
    from guardian_hub import acttokens
    from guardian_hub.routers import slackbot
    tid,uid=sign_in(client)
    args={"host":"web","action":"reboot","arg":None}
    with SessionLocal() as db:
        tenant=db.get(m.Tenant,tid)
        token,_=acttokens.mint_chat(db,tenant,"apply_fix",args,uid,{})
        db.add(m.ConversationState(id="proposal-source",tenant_id=tid,data={"channel":"C1","requester":"UOWNER","approver":"UAPP","pending":token}))
        db.commit()
    monkeypatch.setattr(slackbot,"resolve_channel",lambda db,ch,u,**_:(db.get(m.Tenant,tid),db.get(m.User,uid),"admin",{"named_approver":"UAPP"}))
    monkeypatch.setattr(slackbot.slack_mod,"bot_post",lambda *a,**k:None)
    calls=[]
    def run(db,user,tenant,role,body,**kwargs):
        assert acttokens.consume_chat(db,body.confirm_token,tenant_id=tid,tool="apply_fix",args=args,user_id=uid)
        calls.append(kwargs["request_ip"])
        return SimpleNamespace(thread_id="thread-1",proposal=None,text="Approved",tool_steps=[])
    monkeypatch.setattr(slackbot,"run_turn",run)
    slackbot._THREADS.clear();slackbot._PENDING.clear()
    assert "nothing waiting" in slackbot.handle_message("C1","UOTHER","yes","1",None)
    assert calls == []
    assert slackbot.handle_message("C1","UAPP","yes","2",None) == "Approved"
    assert calls == ["slack:UAPP"]
    assert "nothing waiting" in slackbot.handle_message("C1","UAPP","yes","3",None)


def test_recorder_outage_keeps_child_evidence_without_duplicate_notices(client, monkeypatch):
    from guardian_hub.ingest import store_scan
    from guardian_hub.integrations import registry
    tid,_=sign_in(client)
    sent=[]
    monkeypatch.setattr(registry,"deliver_background",lambda tenant,msg:sent.append(msg))
    now=m.utcnow().isoformat()
    findings={"nvr:down:r1":{"sev":"WARN","msg":"Recorder unreadable","state":"open"},
              "nvr:cam-offline:r1:1":{"sev":"WARN","msg":"Last camera finding","state":"open"}}
    with SessionLocal() as db:
        store_scan(db,db.get(m.Tenant,tid),None,{"last":{"ts":now,"recorders":[{"id":"r1","ok":False}]},"state":{"alerts_db":findings}})
        assert db.query(m.Alert).filter_by(tenant_id=tid).count()==2
    assert [msg.dedup_key for msg in sent] == ["nvr:down:r1"]


def test_timed_out_repair_requires_fresh_reconciliation_before_retry(client):
    tid,_=sign_in(client)
    with SessionLocal() as db:
        q=m.Queue(tenant_id=tid,who="owner",path="/api/fix",body={"host":"web","action":"enable_ufw"},status="failed",finished_at=m.utcnow(),result={"out":"timed out -- outcome unknown"})
        db.add(q);db.commit();qid=q.id
    result=client.post(f"/api/v1/jobs/{qid}/retry")
    assert result.status_code==409 and "may have run" in result.json()["detail"]
    with SessionLocal() as db:
        assert db.query(m.Queue).filter_by(tenant_id=tid).count()==1


def test_job_story_never_exposes_confirmation_credentials(client):
    from guardian_hub import acttokens
    tid,uid=sign_in(client)
    args={"host":"web","action":"reboot","arg":None}
    with SessionLocal() as db:
        token,_=acttokens.mint_chat(db,db.get(m.Tenant,tid),"apply_fix",args,uid,{})
        q=m.Queue(tenant_id=tid,who="owner",path="/api/fix",body=args,status="queued")
        db.add(q);db.commit();qid=q.id
    response=client.get(f"/api/v1/jobs/{qid}/story")
    assert response.status_code==200 and response.json()["proposals"][0]["status"]=="awaiting confirmation"
    assert token not in response.text


def test_unknown_camera_is_not_reported_as_confirmed_offline(client):
    from guardian_hub.payloads.cameras import census
    from guardian_hub.payloads.topology import _device_state
    tid,_=sign_in(client)
    with SessionLocal() as db:
        camera=m.Device(tenant_id=tid,mac="aa:bb:cc:dd:ee:ff",kind="camera",presence="ONLINE",last_seen=(m.utcnow()-dt.timedelta(hours=1)).isoformat())
        db.add(camera);db.commit()
        result=census(db,db.get(m.Tenant,tid),{})
        assert result.known==1 and result.online==0 and result.unobserved==1 and result.offline==0
        assert result.exposure_known is False
        assert _device_state(camera,set())=="unknown"
