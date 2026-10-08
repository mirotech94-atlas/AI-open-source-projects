"""A sensor push must never undo an owner's decision on a device.

Seen live: 25 devices trusted from the dashboard, and the next sweep flipped
every one back -- the sensor had not applied the edits yet and pushed its
old `trusted: None`, which ingest copied over the hub's rows. The owner's
click lasted six minutes. The sensor only mirrors these fields; the hub
row is where the decision lives.
"""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.ingest import store_scan


def _push(db, t, trusted, label=""):
    payload = {"last": {"ts": "2026-09-14T12:00:00+00:00", "lan": {"devices": {"192.168.1.9": "aa:bb:cc:dd:ee:09"}}},
               "state": {"lan": {"known_macs": {"aa:bb:cc:dd:ee:09": {"ip": "192.168.1.9", "trusted": trusted, "label": label,
                                                                       "vendor": "Acme", "name": "thing"}}}}}
    store_scan(db, t, None, payload, deliver=False)
    db.commit()


def _row(db, t):
    return db.query(m.Device).filter_by(tenant_id=t.id, mac="aa:bb:cc:dd:ee:09").one()


def test_a_trusted_row_stays_trusted_when_the_sensor_lags(client):
    db = SessionLocal()
    t = m.Tenant(slug="keep-dec", name="K"); db.add(t); db.commit()
    _push(db, t, trusted=False)
    row = _row(db, t); row.trusted = True; row.label = "Kitchen tablet"; db.commit()

    _push(db, t, trusted=None)          # the sensor has not applied the edit yet
    row = _row(db, t)
    assert row.trusted is True and row.label == "Kitchen tablet"
    db.close()


def test_the_sensor_can_still_raise_trust_and_carry_a_label(client):
    db = SessionLocal()
    t = m.Tenant(slug="keep-dec2", name="K"); db.add(t); db.commit()
    _push(db, t, trusted=False)
    assert _row(db, t).trusted is False
    _push(db, t, trusted=True, label="Printer")
    row = _row(db, t)
    assert row.trusted is True and row.label == "Printer"
    db.close()
