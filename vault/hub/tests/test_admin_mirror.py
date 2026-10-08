"""Two promises about where an alert ends up.

1. A channel's own severity toggles decide what it gets. There used to be a
   second, hidden floor in `ingest` that dropped every INFO before the
   toggles were ever consulted, so a Slack card with INFO ticked sent
   nothing -- and the owner was not told his phone was flapping, because
   `lan:flapping:` is raised at INFO. A switch that silently does nothing is
   worse than no switch.

2. The operator sees his customers' stream in his own channel without
   joining theirs. That is a MIRROR: it must never be able to stop or alter
   the customer's own delivery.
"""
import pytest

from guardian_hub import adminnotify, ingest, models as m, platform_settings as ps
from guardian_hub.db import SessionLocal
from tests.test_auth import seed_user


@pytest.fixture(autouse=True)
def _clean(client):
    ps.refresh()
    yield
    db = SessionLocal(); db.query(m.PlatformSetting).delete(); db.commit(); db.close()
    ps.refresh()


def _scan(alerts):
    """A sensor push. The alert rows the hub upserts come from
    `state.alerts_db` (keyed by alert key), NOT from `last.alerts` -- the
    latter is the fix/prose map."""
    return {
        "last": {"ts": "2026-09-19T18:00:00+00:00", "alerts": alerts, "score": {}},
        "state": {"alerts_db": {a["key"]: a for a in alerts}},
    }


def test_an_info_alert_is_offered_to_the_channels_not_dropped_first(client, monkeypatch):
    """The regression that started this: INFO never reached `deliver()`."""
    seed_user()
    db = SessionLocal()
    tenant = db.query(m.Tenant).first()
    sent: list = []
    monkeypatch.setattr(ingest.registry, "deliver_background",
                        lambda tid, msg: sent.append((msg.severity, msg.body)))
    monkeypatch.setattr(ingest.adminnotify, "mirror_alert", lambda *a, **k: False)
    ingest.store_scan(db, tenant, None, _scan([
        {"key": "lan:primary:flapping:aa:bb", "sev": "INFO",
         "msg": "staff-phone is flapping (joins/leaves repeatedly)"},
    ]))
    db.close()
    assert [s for s, _ in sent] == ["INFO"], sent
    assert "flapping" in sent[0][1]


def test_the_operator_gets_a_copy_naming_the_customer(client, monkeypatch):
    seed_user()
    db = SessionLocal()
    tenant = db.query(m.Tenant).first()
    mirrored: list = []
    monkeypatch.setattr(ingest.registry, "deliver_background", lambda tid, msg: None)
    monkeypatch.setattr(ingest.adminnotify, "mirror_alert",
                        lambda kind, sev, title, body="", *, tenant="": mirrored.append((kind, sev, title, tenant)))
    ingest.store_scan(db, tenant, None, _scan([
        {"key": "lan:primary:flapping:aa:bb", "sev": "INFO", "msg": "staff-phone is flapping"},
    ]))
    db.close()
    assert len(mirrored) == 1
    kind, sev, title, who = mirrored[0]
    assert kind == "alert" and sev == "INFO"
    assert "flapping" in title
    assert who, "the operator must be able to tell WHICH customer this is"


def test_the_mirror_floor_is_a_setting_and_off_means_off(client, monkeypatch):
    """Across enough customers this is the noisiest channel he owns, so the
    volume control must not need a deploy."""
    posted: list = []
    monkeypatch.setattr(adminnotify, "notify",
                        lambda kind, title, body="", *, tenant="": posted.append((kind, title)) or True)

    ps.refresh()
    assert adminnotify.mirror_alert("alert", "INFO", "quiet thing") is True   # default INFO

    db = SessionLocal()
    ps.set_value(db, "admin_mirror_floor", "warn", by="miro"); db.close()
    assert adminnotify.mirror_alert("alert", "INFO", "quiet thing") is False
    assert adminnotify.mirror_alert("alert", "CRIT", "loud thing") is True

    db = SessionLocal()
    ps.set_value(db, "admin_mirror_floor", "off", by="miro"); db.close()
    assert adminnotify.mirror_alert("alert", "CRIT", "loud thing") is False


def test_a_broken_mirror_never_costs_the_customer_their_alert(client, monkeypatch):
    """The mirror is a convenience for the operator. A customer's alert going
    out must not depend on it."""
    seed_user()
    db = SessionLocal()
    tenant = db.query(m.Tenant).first()
    delivered: list = []
    monkeypatch.setattr(ingest.registry, "deliver_background",
                        lambda tid, msg: delivered.append(msg.severity))

    def boom(*a, **k):
        raise RuntimeError("slack is down")
    monkeypatch.setattr(ingest.adminnotify, "mirror_alert", boom)

    with pytest.raises(RuntimeError):
        ingest.store_scan(db, tenant, None, _scan([
            {"key": "lan:primary:flapping:aa:bb", "sev": "WARN", "msg": "something"},
        ]))
    db.close()
    # The real delivery was dispatched BEFORE the mirror was attempted --
    # ordering is the only protection available, so it is asserted.
    assert delivered == ["WARN"]
