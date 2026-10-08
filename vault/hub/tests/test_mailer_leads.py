"""The owner hears about an access request by email as well as on Slack."""
from guardian_hub import mailer, models as m
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from tests.test_auth import seed_user

GOOD = {"name": "Dana Okafor", "email": "dana@example.com", "company": "Okafor Dental",
        "network_size": "one-site", "note": "Two servers.", "site_name": "Main St", "accept_terms": True}


def _sent_to(addr):
    return [s for s in mailer.SENT if s[0] == addr]


def test_owner_addresses_prefers_the_setting_then_falls_back_to_superadmins(client, monkeypatch):
    uid, _ = seed_user(username="owner", email="owner@example.com")
    db = SessionLocal(); db.get(m.User, uid).is_superadmin = True; db.commit(); db.close()
    monkeypatch.setattr(settings, "owner_email", "")
    assert mailer.owner_addresses(SessionLocal()) == ["owner@example.com"]
    monkeypatch.setattr(settings, "owner_email", "a@example.com, b@example.com")
    assert mailer.owner_addresses(SessionLocal()) == ["a@example.com", "b@example.com"]


def test_a_new_lead_emails_the_owner_with_the_details_and_the_decision_link(client, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    mailer.SENT.clear()
    r = client.post("/api/v1/public/lead", json=GOOD)
    assert r.status_code == 200
    (to, subject, text), = _sent_to("owner@example.com")
    assert "Dana Okafor" in subject
    for needle in ("dana@example.com", "Okafor Dental", "Main St", "One office or shop", "Two servers."):
        assert needle in text
    db = SessionLocal(); lead_id = db.query(m.Lead).first().id; db.close()
    assert f"{settings.base_url}/settings/leads?lead={lead_id}&decide=approve" in text
    assert f"{settings.base_url}/settings/leads?lead={lead_id}&decide=reject" in text


def test_a_caught_bot_emails_nobody(client, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    mailer.SENT.clear()
    client.post("/api/v1/public/lead", json={**GOOD, "website": "http://spam.example"})
    assert _sent_to("owner@example.com") == []


def test_a_crafted_name_cannot_inject_a_header(client, monkeypatch):
    monkeypatch.setattr(settings, "owner_email", "owner@example.com")
    mailer.SENT.clear()
    bad = {**GOOD, "name": "X\r\nBcc: evil@example.com"}
    r = client.post("/api/v1/public/lead", json=bad)
    assert r.status_code == 200
    sent = _sent_to("owner@example.com")
    assert len(sent) == 1
    _, subject, _ = sent[0]
    assert "\r" not in subject and "\n" not in subject


def test_send_with_smtp_not_configured_does_not_raise(monkeypatch):
    monkeypatch.setattr(settings, "dev_console_otp", False)
    monkeypatch.setattr(settings, "smtp_host", "")
    mailer.send("a@example.com", "bad\r\nsubject", "body")


def test_send_swallows_an_smtp_failure(monkeypatch):
    monkeypatch.setattr(settings, "dev_console_otp", False)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer.smtplib, "SMTP", _Boom)
    mailer.send("a@example.com", "bad\r\nsubject", "body")


def test_sent_never_grows_past_fifty_entries(monkeypatch):
    # SENT is dev/test-only, but a long-running dev process (or a chatty test
    # suite) must not let it grow without bound.
    monkeypatch.setattr(settings, "dev_console_otp", True)
    mailer.SENT.clear()
    for i in range(60):
        mailer.send(f"a{i}@example.com", "subject", "body")
    assert len(mailer.SENT) == 50
    # And it kept the most recent ones, not the oldest.
    assert mailer.SENT[-1][0] == "a59@example.com"
