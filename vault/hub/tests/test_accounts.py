from guardian_hub import models as m
from guardian_hub.accounts import upsert_membership, upsert_user
from guardian_hub.db import SessionLocal


def test_upsert_user_creates_then_reuses(client):
    db = SessionLocal()
    u1 = upsert_user(db, "Alice", "alice@example.com", "Alice A")
    db.commit()
    assert u1.username == "alice"  # lowercased
    assert u1.email == "alice@example.com"
    assert u1.is_superadmin is False

    # second call with the same username finds the existing row rather than
    # creating a duplicate, and does NOT overwrite email/display_name.
    u2 = upsert_user(db, "alice", "different@example.com", "Different Name")
    db.commit()
    assert u2.id == u1.id
    assert db.query(m.User).filter_by(username="alice").count() == 1
    assert u2.email == "alice@example.com"
    assert u2.display_name == "Alice A"


def test_upsert_user_superadmin_only_turns_on(client):
    db = SessionLocal()
    u = upsert_user(db, "bob", "bob@example.com", superadmin=False)
    db.commit()
    assert u.is_superadmin is False

    u = upsert_user(db, "bob", "bob@example.com", superadmin=True)
    db.commit()
    assert u.is_superadmin is True

    # a later call passing False again must not turn it back off
    u = upsert_user(db, "bob", "bob@example.com", superadmin=False)
    db.commit()
    assert u.is_superadmin is True


def test_upsert_membership_creates_then_updates_role(client):
    db = SessionLocal()
    t = m.Tenant(slug="acct-test", name="Acct Test")
    db.add(t)
    db.commit()
    u = upsert_user(db, "carol", "carol@example.com")
    db.commit()

    mb1 = upsert_membership(db, u, t, "viewer")
    db.commit()
    assert mb1.role == "viewer"
    assert db.query(m.Membership).filter_by(user_id=u.id, tenant_id=t.id).count() == 1

    mb2 = upsert_membership(db, u, t, "admin")
    db.commit()
    assert mb2.id == mb1.id
    assert mb2.role == "admin"
    assert db.query(m.Membership).filter_by(user_id=u.id, tenant_id=t.id).count() == 1
