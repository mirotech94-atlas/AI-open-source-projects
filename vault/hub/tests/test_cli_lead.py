from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.cli import main


def _lead(client):
    client.post("/api/v1/public/lead", json={"name": "Dana Okafor", "email": "dana@example.com",
                                             "site_name": "Main St", "accept_terms": True})
    db = SessionLocal()
    try:
        return db.query(m.Lead).one().id
    finally:
        db.close()


def test_cli_approve_provisions_like_the_route(client, capsys):
    lead_id = _lead(client)
    main(["lead", "approve", lead_id])
    out = capsys.readouterr().out
    assert "main-st" in out and "dana@example.com" in out
    db = SessionLocal()
    try:
        t = db.query(m.Tenant).filter_by(slug="main-st").one()
        u = db.query(m.User).filter_by(username="dana@example.com").one()
        assert db.query(m.Membership).filter_by(user_id=u.id, tenant_id=t.id).one().role == "admin"
        assert db.query(m.Lead).get(lead_id).status == "approved"
    finally:
        db.close()
