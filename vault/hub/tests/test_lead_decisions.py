"""Approve or reject an access request. Approval provisions the account."""
import pytest

from guardian_hub import billing, mailer, models as m
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user
from tests.test_tenants import make_super

GOOD = {"name": "Dana Okafor", "email": "Dana@Example.com", "company": "Okafor Dental",
        "network_size": "one-site", "note": "Two servers.", "site_name": "Okafor Dental — Main St",
        "accept_terms": True}


def _submit(client, **over):
    assert client.post("/api/v1/public/lead", json={**GOOD, **over}).status_code == 200
    db = SessionLocal()
    try:
        return db.query(m.Lead).order_by(m.Lead.created.desc()).first().id
    finally:
        db.close()


def test_approve_creates_tenant_user_membership_trial_and_emails_the_applicant(client):
    lead_id = _submit(client)
    make_super(client)
    mailer.SENT.clear()
    r = client.post(f"/api/v1/leads/{lead_id}/approve", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "approved" and out["decided_by"] == "miro" and out["tenant_slug"] == "okafor-dental-main-st"

    db = SessionLocal()
    try:
        t = db.query(m.Tenant).filter_by(slug="okafor-dental-main-st").one()
        assert t.name == "Okafor Dental — Main St"
        u = db.query(m.User).filter_by(username="dana@example.com").one()
        assert u.email == "dana@example.com" and u.display_name == "Dana Okafor" and not u.is_superadmin
        mb = db.query(m.Membership).filter_by(user_id=u.id, tenant_id=t.id).one()
        assert mb.role == "admin"
        sub = db.query(m.Subscription).filter_by(tenant_id=t.id).one()
        assert sub.status == "trialing"
        assert db.query(m.Lead).get(lead_id).tenant_id == t.id
        actions = {a.action for a in db.query(m.Audit).filter_by(tenant_id=t.id)}
        assert {"tenant.create", "tenant.user_add", "lead.approve"} <= actions
    finally:
        db.close()

    (to, subject, text), = [s for s in mailer.SENT if s[0] == "dana@example.com"]
    assert "ready" in subject.lower()
    assert f"{settings.base_url}/login?u=dana%40example.com" in text


def test_approve_honours_edited_names_and_uniquifies_a_taken_slug(client):
    lead_id = _submit(client)
    tid = make_super(client)
    # `home` is taken by the superadmin's own tenant (seed_user).
    r = client.post(f"/api/v1/leads/{lead_id}/approve",
                    json={"tenant_name": "Dana's Clinic", "slug": "home", "username": "dana", "display_name": "D. Okafor"})
    assert r.status_code == 200, r.text
    assert r.json()["tenant_slug"] == "home-2"
    db = SessionLocal()
    try:
        assert db.query(m.Tenant).filter_by(slug="home-2").one().name == "Dana's Clinic"
        assert db.query(m.User).filter_by(username="dana").one().display_name == "D. Okafor"
    finally:
        db.close()


def test_approve_is_idempotent_and_a_rejected_lead_cannot_be_approved(client):
    lead_id = _submit(client)
    make_super(client)
    first = client.post(f"/api/v1/leads/{lead_id}/approve", json={}).json()
    second = client.post(f"/api/v1/leads/{lead_id}/approve", json={})
    assert second.status_code == 200 and second.json()["tenant_slug"] == first["tenant_slug"]
    db = SessionLocal()
    try:
        assert db.query(m.Tenant).filter_by(slug=first["tenant_slug"]).count() == 1
    finally:
        db.close()
    assert client.post(f"/api/v1/leads/{lead_id}/reject", json={}).status_code == 409

    other = _submit(client, email="sam@example.net")
    assert client.post(f"/api/v1/leads/{other}/reject", json={}).status_code == 200
    assert client.post(f"/api/v1/leads/{other}/approve", json={}).status_code == 409


def test_approve_for_an_email_that_already_has_an_account_adds_a_membership_only(client):
    seed_user(username="dana@example.com", email="dana@example.com", tenant_slug="dana-old")
    lead_id = _submit(client)
    make_super(client)
    mailer.SENT.clear()
    r = client.post(f"/api/v1/leads/{lead_id}/approve", json={})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        assert db.query(m.User).filter_by(username="dana@example.com").count() == 1
        u = db.query(m.User).filter_by(username="dana@example.com").one()
        assert db.query(m.Membership).filter_by(user_id=u.id).count() == 2
        # The pre-existing user's own record is left alone: the lead's name
        # ("Dana Okafor") does not overwrite what was already on file.
        assert u.email == "dana@example.com" and u.display_name == "Miro"
    finally:
        db.close()
    (_, _, text), = [s for s in mailer.SENT if s[0] == "dana@example.com"]
    assert "existing sign-in" in text


def test_approve_rejects_a_username_containing_spaces(client):
    lead_id = _submit(client)
    make_super(client)
    r = client.post(f"/api/v1/leads/{lead_id}/approve", json={"username": "dana okafor"})
    assert r.status_code == 422


def test_approve_rolls_back_entirely_when_the_trial_start_fails(client, monkeypatch):
    lead_id = _submit(client)
    make_super(client)

    def _boom(db, tenant_id, **kw):
        raise RuntimeError("stripe is down")

    monkeypatch.setattr(billing, "start_trial", _boom)
    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/leads/{lead_id}/approve", json={})

    db = SessionLocal()
    try:
        assert db.query(m.Tenant).filter_by(slug="okafor-dental-main-st").count() == 0
        lead = db.query(m.Lead).get(lead_id)
        assert lead.status == "pending" and lead.tenant_id is None
    finally:
        db.close()


def test_reject_records_the_decision_and_sends_the_fixed_decline(client):
    lead_id = _submit(client)
    make_super(client)
    mailer.SENT.clear()
    r = client.post(f"/api/v1/leads/{lead_id}/reject", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected" and r.json()["decided_by"] == "miro"
    (to, subject, text), = [s for s in mailer.SENT if s[0] == "dana@example.com"]
    assert text == mailer.DECLINE_TEXT
    db = SessionLocal()
    try:
        assert db.query(m.Tenant).count() == 1   # only the superadmin's own
    finally:
        db.close()


def test_decisions_are_superadmin_only(client):
    lead_id = _submit(client)
    assert client.post(f"/api/v1/leads/{lead_id}/approve", json={}).status_code == 401
    seed_user(); login(client)   # a plain tenant admin
    assert client.post(f"/api/v1/leads/{lead_id}/approve", json={}).status_code == 403
    assert client.post(f"/api/v1/leads/{lead_id}/reject", json={}).status_code == 403
