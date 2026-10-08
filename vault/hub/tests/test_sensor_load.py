"""Task 70: the sensor host's load figure, from `last["self"]` to `GET /live`."""

import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def test_live_carries_the_freshest_sensors_host_load(client):
    seeded_demo(client)
    body = client.get("/api/v1/live").json()
    # The demo's fixture reports load1/cores in `last["self"]`, which ingest
    # copies onto the sensor row.
    assert body["load1"] == 1.94 and body["cores"] == 8


def test_live_load_is_null_for_a_sensor_that_never_reported_one(client):
    """An older sensor, or a platform with no load average: the field stays
    null so the sub-header keeps its freshness pill rather than drawing a 0."""
    seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(slug="demo").first()
        db.query(m.Sensor).filter_by(tenant_id=tenant.id).update({"load1": None, "cores": None})
        db.commit()
    finally:
        db.close()
    body = client.get("/api/v1/live").json()
    assert body["load1"] is None and body["cores"] is None
    assert body["sensor_age_s"] is not None


def test_ingest_never_resets_a_known_load_to_zero(client):
    """A second push from the SAME sensor whose `self` block carries no load
    must leave the stored figure alone -- overwriting it with 0.0 (or with
    None) would turn a busy machine into an idle one on the next scan."""
    from tests import scanfixture
    from tests.test_seed_helpers import enrol_as_tenant_admin

    seeded_demo(client)
    tok = enrol_as_tenant_admin(client)
    headers = {"Authorization": f"Bearer {tok}"}
    now = dt.datetime.now(dt.timezone.utc)

    def push(when, drop_load):
        last = scanfixture.load_last()
        if drop_load:
            last["self"].pop("load1"); last["self"].pop("cores")
        last["ts"] = when.isoformat(timespec="seconds")
        r = client.post("/api/v1/sensor/ingest",
                        json={"last": last, "state": {}, "events": [], "history": [],
                              "fixes": [], "dns_queries": []}, headers=headers)
        assert r.status_code == 200, r.text

    def stored():
        db = SessionLocal()
        try:
            tenant = db.query(m.Tenant).filter_by(slug="demo").first()
            row = db.query(m.Sensor).filter_by(tenant_id=tenant.id, name="sensor").first()
            assert row is not None, "the enrolled sensor should exist"
            return row.load1, row.cores
        finally:
            db.close()

    push(now - dt.timedelta(minutes=5), drop_load=False)
    assert stored() == (1.94, 8)

    push(now, drop_load=True)
    assert stored() == (1.94, 8)
