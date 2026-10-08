"""Task 7: flows land in `flow`, never in `Scan.raw` (spec §2/§7).

No `minimal_payload`/`sensor_headers` helpers exist under those names in
`tests/test_seed_helpers.py` (checked: it only has `seeded_demo`,
`enrol_as_tenant_admin`, `confirmed_fix`). The real route is
`POST /api/v1/sensor/ingest` (see `guardian_hub/routers/sensor.py`), bearer
auth minted by `enrol_as_tenant_admin` after `seeded_demo`, and it wraps
`store_scan`'s return in `{"ok": True, "stored": {...}}` (see
`test_ingest.py`'s `r2.json()["stored"]["events"]`). `_push` below unwraps
that so `out["flows"]` reads the same as the brief's tests. The minimal
payload shape is lifted from `test_ingest.py`'s
`test_store_scan_replay_does_not_duplicate_appended_rows`.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from tests.test_seed_helpers import seeded_demo, enrol_as_tenant_admin


def _row(**kw):
    base = {"ts": "2026-09-20T10:00:00+00:00", "net": None, "mac": "aa:bb:cc:00:00:01", "ip": "192.168.1.20",
            "peer_ip": "142.250.1.1", "peer_port": 443, "proto": "tcp", "bytes_in": 1000, "bytes_out": 100,
            "pkts_in": 10, "pkts_out": 3, "hints": {"sni": "www.google.com"}, "source": "mirror"}
    base.update(kw); return base


def _minimal_payload(ts):
    return {"last": {"ts": ts, "score": {"total": 90}}, "state": {},
            "events": [], "fixes": [], "dns_queries": [], "history": []}


def _push(client, rows, ts="2026-09-20T10:05:00+00:00"):
    tok = enrol_as_tenant_admin(client)
    body = _minimal_payload(ts); body["flows"] = rows
    r = client.post("/api/v1/sensor/ingest", json=body, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return r.json()["stored"]


def test_flows_land_in_their_table_and_never_in_scan_raw(client):
    seeded_demo(client)
    out = _push(client, [_row()])
    assert out["flows"] == 1
    db = SessionLocal()
    assert db.query(m.Flow).filter_by(mac="aa:bb:cc:00:00:01").count() == 1
    latest = db.query(m.Scan).order_by(m.Scan.ts.desc()).first()
    assert "flows" not in (latest.raw or {})


def test_a_retry_adds_zero_rows(client):
    seeded_demo(client)
    _push(client, [_row()]); out = _push(client, [_row()], ts="2026-09-20T10:10:00+00:00")
    # Scoped to this test's own mac, not a bare `.count()`: Task 14 makes
    # `seed_demo` push its own ~600 `Flow` rows for the demo tenant, so the
    # table is no longer empty going in.
    assert out["flows"] == 0 and SessionLocal().query(m.Flow).filter_by(mac="aa:bb:cc:00:00:01").count() == 1


def test_one_bad_row_is_dropped_and_the_scan_still_stores(client):
    seeded_demo(client)
    out = _push(client, [_row(), _row(mac="not-a-mac"), _row(peer_port=80, bytes_in=-5)])
    assert out["flows"] == 1 and out["flows_rejected"] == 2 and out["scan"] == 1


def test_network_is_attributed_like_dns(client):
    seeded_demo(client)
    _push(client, [_row(net=None)])
    # `.filter_by(mac=...)`, not a bare `.one()`: Task 14's seeded flows
    # share the table, so an unscoped query now finds many rows.
    row = SessionLocal().query(m.Flow).filter_by(mac="aa:bb:cc:00:00:01").one()
    assert row.network_id is not None
