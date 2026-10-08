from guardian_hub import mailer
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

def seed_user(username="miro", email="m@example.com", tenant_slug="home", role="admin"):
    db = SessionLocal()
    t = m.Tenant(slug=tenant_slug, name="Miro Home Network Security")
    u = m.User(username=username, email=email, display_name="Miro")
    db.add_all([t, u]); db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=t.id, role=role)); db.commit()
    return u.id, t.id

def login(client, username="miro", email="m@example.com"):
    assert client.post("/api/v1/auth/start", json={"username": username}).json() == {"sent": True}
    code = mailer.LAST_CODES[email]
    r = client.post("/api/v1/auth/verify", json={"username": username, "code": code})
    assert r.status_code == 200, r.text
    return r.json()

def test_start_unknown_user_is_silent(client):
    assert client.post("/api/v1/auth/start", json={"username": "nobody"}).json() == {"sent": True}

def test_full_login(client):
    seed_user()
    body = login(client)
    assert body["user"]["username"] == "miro"
    assert body["tenants"][0]["role"] == "admin"
    me = client.get("/api/v1/me").json()
    assert me["active_tenant"]["slug"] == "home"

def test_wrong_code_and_lockout(client):
    seed_user()
    client.post("/api/v1/auth/start", json={"username": "miro"})
    for _ in range(5):
        r = client.post("/api/v1/auth/verify", json={"username": "miro", "code": "000000"})
        assert r.status_code == 401
    good = mailer.LAST_CODES["m@example.com"]
    assert client.post("/api/v1/auth/verify", json={"username": "miro", "code": good}).status_code == 401  # burnt

def test_logout(client):
    seed_user(); login(client)
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/me").status_code == 401

def test_expired_code_rejected(client, monkeypatch):
    seed_user()
    monkeypatch.setattr(settings, "otp_ttl_seconds", -1)
    client.post("/api/v1/auth/start", json={"username": "miro"})
    code = mailer.LAST_CODES["m@example.com"]
    r = client.post("/api/v1/auth/verify", json={"username": "miro", "code": code})
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]

def test_switch_tenant(client):
    uid, _tid1 = seed_user()
    db = SessionLocal()
    t2 = m.Tenant(slug="work", name="Work Network")
    db.add(t2); db.flush()
    db.add(m.Membership(user_id=uid, tenant_id=t2.id, role="viewer"))
    db.commit()
    tid2 = t2.id
    db.close()

    login(client)
    r = client.post("/api/v1/me/tenant", json={"tenant_id": tid2})
    assert r.status_code == 200, r.text
    assert r.json()["active_tenant"]["slug"] == "work"

    me = client.get("/api/v1/me").json()
    assert me["active_tenant"]["slug"] == "work"

def test_demo_code_signs_in_without_an_emailed_otp(client):
    from guardian_hub.seed import seed_demo
    from guardian_hub.db import SessionLocal
    seed_demo(SessionLocal())
    r = client.post("/api/v1/auth/verify", json={"username": "demo", "code": "424242"})
    assert r.status_code == 200 and r.json()["active_tenant"]["slug"] == "demo"
    assert client.post("/api/v1/auth/verify", json={"username": "demo", "code": "111111"}).status_code == 401

def test_verify_is_rate_limited_per_ip(client):
    seed_user()
    # Spread over usernames, so this is the per-IP cap and not the
    # per-username one (`AUTH_VERIFY_PER_USER`), which would trip first.
    for i in range(20):
        r = client.post("/api/v1/auth/verify", json={"username": f"miro{i}", "code": "000000"})
        assert r.status_code == 401
    r = client.post("/api/v1/auth/verify", json={"username": "miro", "code": "000000"})
    assert r.status_code == 429
