from tests.test_auth import seed_user, login
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub import cli


def make_super(client):
    uid, tid = seed_user()
    db = SessionLocal(); db.get(m.User, uid).is_superadmin = True; db.commit()
    login(client); return tid


def test_tenant_crud_and_users(client):
    make_super(client)
    r = client.post("/api/v1/tenants", json={"slug": "dental", "name": "Northwind Dental"}); assert r.status_code == 200
    tid = r.json()["id"]
    r = client.post(f"/api/v1/tenants/{tid}/users", json={"username": "ann", "email": "ann@x.com", "display_name": "Ann", "role": "viewer"})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    r = client.post(f"/api/v1/tenants/{tid}/enrol-code"); assert len(r.json()["code"]) == 8
    assert any(t["slug"] == "dental" for t in client.get("/api/v1/tenants").json())


def test_tenant_writes_are_audited(client):
    make_super(client)

    r = client.post("/api/v1/tenants", json={"slug": "dental", "name": "Northwind Dental"})
    tid = r.json()["id"]
    rows = SessionLocal().query(m.Audit).filter_by(tenant_id=tid, action="tenant.create").all()
    assert len(rows) == 1
    assert rows[0].user == "miro" and rows[0].role == "superadmin" and rows[0].target == "dental"

    client.patch(f"/api/v1/tenants/{tid}", json={"name": "Northwind Dental Group"})
    assert SessionLocal().query(m.Audit).filter_by(tenant_id=tid, action="tenant.update").count() == 1

    client.post(f"/api/v1/tenants/{tid}/users", json={"username": "ann", "email": "ann@x.com", "display_name": "Ann", "role": "viewer"})
    assert SessionLocal().query(m.Audit).filter_by(tenant_id=tid, action="tenant.user_add", target="ann").count() == 1

    client.delete(f"/api/v1/tenants/{tid}/users/ann")
    assert SessionLocal().query(m.Audit).filter_by(tenant_id=tid, action="tenant.user_remove", target="ann").count() == 1

    client.post(f"/api/v1/tenants/{tid}/enrol-code")
    assert SessionLocal().query(m.Audit).filter_by(tenant_id=tid, action="sensor.enrol_code").count() == 1


def test_non_super_forbidden(client):
    seed_user(); login(client)
    assert client.get("/api/v1/tenants").status_code == 403


def test_cli_tenant_add_and_list(client, capsys):
    cli.main(["tenant", "add", "x", "X"])
    cli.main(["tenant", "list"])
    out = capsys.readouterr().out
    assert "x" in out


def test_bad_role_422(client):
    make_super(client)
    r = client.post("/api/v1/tenants", json={"slug": "test", "name": "Test"})
    tid = r.json()["id"]
    r = client.post(f"/api/v1/tenants/{tid}/users", json={"username": "ann", "email": "ann@x.com", "display_name": "Ann", "role": "superadmin"})
    assert r.status_code == 422


def test_duplicate_slug_409(client):
    make_super(client)
    client.post("/api/v1/tenants", json={"slug": "test", "name": "Test"})
    r = client.post("/api/v1/tenants", json={"slug": "test", "name": "Another"})
    assert r.status_code == 409


def test_patch_unknown_tenant_404(client):
    make_super(client)
    r = client.patch("/api/v1/tenants/bogus", json={"name": "NewName"})
    assert r.status_code == 404


def test_add_user_unknown_tenant_404(client):
    make_super(client)
    r = client.post("/api/v1/tenants/bogus/users", json={"username": "ann", "email": "ann@x.com", "display_name": "Ann", "role": "viewer"})
    assert r.status_code == 404


def test_remove_user_unknown_tenant_404(client):
    make_super(client)
    r = client.delete("/api/v1/tenants/bogus/users/ann")
    assert r.status_code == 404


def test_remove_user_unknown_username_404(client):
    make_super(client)
    r = client.post("/api/v1/tenants", json={"slug": "test", "name": "Test"})
    tid = r.json()["id"]
    r = client.delete(f"/api/v1/tenants/{tid}/users/unknown")
    assert r.status_code == 404


def test_remove_user_no_membership_404(client):
    make_super(client)
    r = client.post("/api/v1/tenants", json={"slug": "test", "name": "Test"})
    tid = r.json()["id"]
    # Add a user without membership to this tenant
    db = SessionLocal()
    usr = m.User(username="orphan", email="orphan@x.com", display_name="Orphan")
    db.add(usr)
    db.commit()
    # Try to remove non-existent membership
    r = client.delete(f"/api/v1/tenants/{tid}/users/orphan")
    assert r.status_code == 404


def test_enrol_code_unknown_tenant_404(client):
    make_super(client)
    r = client.post("/api/v1/tenants/bogus/enrol-code")
    assert r.status_code == 404


def test_cli_user_add_upsert_role(client, capsys):
    cli.main(["tenant", "add", "test", "Test"])
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="test").first()
    # Add user with admin role
    cli.main(["user", "add", "alice", "alice@x.com", "--tenant", "test", "--role", "admin"])
    # Check initial role
    mb_check = db.query(m.Membership).join(m.User).filter(m.User.username == "alice", m.Membership.tenant_id == t.id).first()
    assert mb_check.role == "admin"
    # Update role to viewer
    cli.main(["user", "add", "alice", "alice@x.com", "--tenant", "test", "--role", "viewer"])
    db.expire_all()
    mb_check = db.query(m.Membership).join(m.User).filter(m.User.username == "alice", m.Membership.tenant_id == t.id).first()
    assert mb_check.role == "viewer"


def test_enrol_code_audit_target_is_sensor_id(client):
    tid = make_super(client)
    r = client.post(f"/api/v1/tenants/{tid}/enrol-code"); assert r.status_code == 200
    row = SessionLocal().query(m.Audit).filter_by(action="sensor.enrol_code").order_by(m.Audit.id.desc()).first()
    assert row is not None and row.target == r.json()["sensor_id"] and row.target != "None"
