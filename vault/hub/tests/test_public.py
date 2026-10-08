import datetime as dt
import threading

import pytest

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, acttokens
from guardian_hub.security import new_token


def test_act_token_is_single_use_and_leaks_nothing(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    mac = db.query(m.Device).filter_by(tenant_id=t.id).first().mac
    tok = acttokens.mint(db, t, "device", mac, ["trust", "snooze"]); db.commit()
    r = client.get(f"/act/{tok}/trust")
    assert r.status_code == 200 and "Vault" in r.text
    assert db.query(m.Device).filter_by(tenant_id=t.id, mac=mac).one().trusted is True
    assert db.query(m.Audit).filter_by(action="device.update", user="act:device").count() == 1
    assert client.get(f"/act/{tok}/trust").status_code == 410
    bad = client.get("/act/deadbeef/trust")
    assert bad.status_code == 410 and "demo" not in bad.text.lower()


def test_act_token_consume_is_atomic_under_concurrency(client):
    """N threads race to consume the same token+action; the DB-level
    UPDATE...WHERE used='' guard must let exactly one win, no matter how
    the threads interleave."""
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    mac = db.query(m.Device).filter_by(tenant_id=t.id).first().mac
    tok = acttokens.mint(db, t, "device", mac, ["trust"]); db.commit()

    results = []
    lock = threading.Lock()

    def worker():
        thread_db = SessionLocal()
        try:
            acttokens.consume(thread_db, tok, "trust")
            ok = True
        except acttokens.ActTokenError:
            ok = False
        finally:
            thread_db.close()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for th in threads: th.start()
    for th in threads: th.join()

    assert results.count(True) == 1
    assert results.count(False) == 11
    row = db.query(m.ActToken).filter_by(token=tok).one()
    assert row.used == "trust"


def test_mint_rejects_fix_action(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    with pytest.raises(ValueError):
        acttokens.mint(db, t, "alert", "somekey", ["trust", "fix"])


def test_act_publishes_live_frame(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    mac = db.query(m.Device).filter_by(tenant_id=t.id).first().mac
    tok = acttokens.mint(db, t, "device", mac, ["trust"]); db.commit()

    from guardian_hub.live import bus
    seen = []
    orig_publish = bus.publish
    def spy(tenant_id, kind, data):
        seen.append((tenant_id, kind, data))
        return orig_publish(tenant_id, kind, data)
    bus.publish = spy
    try:
        r = client.get(f"/act/{tok}/trust")
        assert r.status_code == 200
    finally:
        bus.publish = orig_publish
    assert any(k == "device" and tid == t.id for tid, k, _ in seen)


def test_act_fix_action_is_rejected(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    a = db.query(m.Alert).filter_by(tenant_id=t.id).first()
    if a is None:
        a = m.Alert(tenant_id=t.id, key="test:fixreject", sev="WARN", msg="x", status="open", fix="restart",
                    first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00")
        db.add(a); db.commit()
    tok = acttokens.mint(db, t, "alert", a.key, ["ack"]); db.commit()
    # A token minted without "fix" (mint rejects it anyway) hitting the
    # fix route by hand-crafted URL never runs anything -- consume() 410s
    # it immediately since "fix" isn't among the token's allowed actions.
    r = client.get(f"/act/{tok}/fix")
    assert r.status_code == 410
    assert db.query(m.Queue).filter_by(tenant_id=t.id, path="/api/fix").count() == 0

    # Even a row inserted straight into the table (bypassing mint's guard
    # entirely, e.g. a token minted before this fix round) hits the route's
    # own belt-and-suspenders: there is no fix branch left in `act()`, so
    # it 400s as "unknown action" rather than queuing a fix.
    raw_tok = new_token()
    db.add(m.ActToken(token=raw_tok, tenant_id=t.id, kind="alert", target=a.key, actions=["fix"],
                       expires=acttokens._now() + dt.timedelta(hours=1)))
    db.commit()
    r2 = client.get(f"/act/{raw_tok}/fix")
    assert r2.status_code == 400
    assert db.query(m.Queue).filter_by(tenant_id=t.id, path="/api/fix").count() == 0


def test_act_snooze_and_ack_on_missing_alert_return_410_with_no_side_effects(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    missing_key = "alert:does-not-exist"

    tok1 = acttokens.mint(db, t, "alert", missing_key, ["snooze"]); db.commit()
    r1 = client.get(f"/act/{tok1}/snooze")
    assert r1.status_code == 410
    assert db.query(m.Edit).filter_by(tenant_id=t.id, path="/api/alert").count() == 0
    assert db.query(m.Audit).filter_by(action="alert.snooze").count() == 0

    tok2 = acttokens.mint(db, t, "alert", missing_key, ["ack"]); db.commit()
    r2 = client.get(f"/act/{tok2}/ack")
    assert r2.status_code == 410
    assert db.query(m.Edit).filter_by(tenant_id=t.id, path="/api/alert").count() == 0
    assert db.query(m.Audit).filter_by(action="alert.ack").count() == 0


def test_status_page_shows_only_public_sites(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    sites = db.query(m.Site).filter_by(tenant_id=tid).all()
    sites[0].public = True
    for s in sites[1:]: s.public = False
    db.commit()
    html = client.get("/status/demo").text
    assert sites[0].name.split(" (")[0] in html
    for s in sites[1:]: assert s.name.split(" (")[0] not in html
    assert "192.168." not in html and "Security score" not in html


def test_heartbeat_and_share_link_expiry(client):
    import datetime as dt
    tid = seeded_demo(client)
    db = SessionLocal()
    hb = m.Heartbeat(tenant_id=tid, name="roots-daily-report", token="t" * 32, expect_minutes=1500)
    db.add(hb); db.commit()
    assert client.get("/hb/roots-daily-report/" + "t" * 32).json()["ok"] is True
    assert client.get("/hb/roots-daily-report/wrong").status_code == 404
    card = client.post("/api/v1/reports/build", json={"days": 7, "preset": "executive",
                                                      "sections": [], "title": "T"}).json()
    tok = client.post(f"/api/v1/reports/{card['id']}/share", json={"hours": 1}).json()["share_url"].rsplit("/", 1)[1]
    assert client.get(f"/r/{tok}").status_code == 200
    rep = db.query(m.Report).filter_by(share_token=tok).one()
    rep.share_expires = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1); db.commit()
    assert client.get(f"/r/{tok}").status_code == 410


def test_metrics_are_guardian_prefixed_and_tenant_labelled(client):
    seeded_demo(client)
    body = client.get("/metrics").text
    assert "guardian_site_up{" in body and 'tenant="demo"' in body
    assert "nexus_" not in body
    assert "guardian_data_age_seconds{" in body and "guardian_sensor_up{" in body


def test_act_alert_snooze_and_ack_happy_paths(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    a1 = m.Alert(tenant_id=t.id, key="test:snoozeme", sev="WARN", msg="x", status="open",
                 first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00")
    a2 = m.Alert(tenant_id=t.id, key="test:ackme", sev="WARN", msg="y", status="open",
                 first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00")
    db.add_all([a1, a2]); db.commit()

    tok1 = acttokens.mint(db, t, "alert", a1.key, ["snooze"]); db.commit()
    r1 = client.get(f"/act/{tok1}/snooze")
    assert r1.status_code == 200 and "Vault" in r1.text
    db.expire_all()
    row1 = db.query(m.Alert).filter_by(tenant_id=t.id, key=a1.key).one()
    assert row1.status == "snoozed" and row1.snoozed_until
    assert db.query(m.Audit).filter_by(action="alert.snooze", user="act:alert").count() == 1
    assert db.query(m.Edit).filter_by(tenant_id=t.id, path="/api/alert").count() == 1
    assert db.query(m.ActToken).filter_by(token=tok1).one().used == "snooze"
    assert client.get(f"/act/{tok1}/snooze").status_code == 410

    tok2 = acttokens.mint(db, t, "alert", a2.key, ["ack"]); db.commit()
    assert client.get(f"/act/{tok2}/ack").status_code == 200
    db.expire_all()
    assert db.query(m.Alert).filter_by(tenant_id=t.id, key=a2.key).one().status == "acked"
    assert db.query(m.Audit).filter_by(action="alert.ack", user="act:alert").count() == 1


def test_act_on_a_vanished_target_does_not_burn_the_token(client):
    """The target is checked before `consume`, so a button for something
    that no longer exists says so instead of reporting a perfectly good
    link as expired/used."""
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    tok = acttokens.mint(db, t, "device", "aa:bb:cc:00:11:22", ["trust", "snooze"]); db.commit()

    r = client.get(f"/act/{tok}/trust")
    assert r.status_code == 404 and "nothing to do" in r.text.lower()
    assert db.query(m.ActToken).filter_by(token=tok).one().used == ""

    r2 = client.get(f"/act/{tok}/snooze")
    assert r2.status_code == 410 and "any more" in r2.text.lower()
    assert db.query(m.ActToken).filter_by(token=tok).one().used == ""
