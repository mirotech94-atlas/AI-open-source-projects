"""The direct line and the reply-time promise.

Both are operator settings that default to empty and are absent until set.
There is no phone setting by design: a number that rings out during an
outage is worse than no number, and the v2 design's +1 (555) placeholder
must never reach a customer.
"""
from guardian_hub import platform_settings as ps
from tests.test_seed_helpers import seeded_demo


def test_both_default_to_empty(client):
    assert ps.support_direct_email() == ""
    assert ps.support_reply_sla() == ""


def test_the_payload_omits_them_until_an_operator_sets_them(client):
    seeded_demo(client)
    body = client.get("/api/v1/support/tickets").json()
    assert body["support_direct_email"] == ""
    assert body["support_reply_sla"] == ""
    assert "support_phone" not in body


def test_there_is_no_phone_setting_at_all(client):
    assert "support_phone" not in ps.SPEC
    for spec in ps.SPEC.values():
        default = spec.default() if callable(spec.default) else spec.default
        assert "555" not in str(default or "")


def test_a_set_direct_email_reaches_the_customer(client):
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    ps.set_value(db, "support_direct_email", "miro@guarder.io", by="miro")
    ps.set_value(db, "support_reply_sla", "under 2 hours, weekdays", by="miro")
    db.commit(); db.close()
    body = client.get("/api/v1/support/tickets").json()
    assert body["support_direct_email"] == "miro@guarder.io"
    assert body["support_reply_sla"] == "under 2 hours, weekdays"
