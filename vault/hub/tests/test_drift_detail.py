"""Task 83, item 5: the honest version of "show the diff" on a config-drift
alert (`sec:drift:<host>:<what>`).

Checked before writing this: the sensor keeps only a truncated md5
fingerprint per file (`sensor/guardian_sensor/hosts.py`), never the file's
content, and never even the PRIOR fingerprint reaches the hub -- only the
current one, on `Host.last["drift"]`. So there is no line diff Vault can
honestly produce. These tests pin the smallest honest thing it CAN say: which
file, on which host, when Vault first saw the change, and its current
fingerprint -- with the limitation stated in plain language rather than a
blank "no diff available" the reader would misread as "nothing changed".
"""

from guardian_hub import drift
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _open_drift_alert(db, tenant_id, host_name="Garage NVR", what="cron", fingerprint="a1b2c3d4e5"):
    host = db.query(m.Host).filter_by(tenant_id=tenant_id, name=host_name).first()
    if host is None:
        host = m.Host(tenant_id=tenant_id, name=host_name, address="192.168.1.50")
        db.add(host)
        db.flush()
    host.last = {**(host.last or {}), "drift": {**((host.last or {}).get("drift") or {}), what: fingerprint}}
    key = f"sec:drift:{host_name}:{what}"
    alert = db.query(m.Alert).filter_by(tenant_id=tenant_id, key=key).first()
    if alert is None:
        alert = m.Alert(tenant_id=tenant_id, key=key, sev="WARN",
                         msg=f"{host_name}: {what} changed since last scan (config drift) — was it you or Guardian?",
                         first_seen="2026-09-01T12:00:00-04:00", last_seen="2026-09-01T12:00:00-04:00")
        db.add(alert)
    db.commit()
    return key


def test_parse_key_splits_host_and_file_token():
    assert drift.parse_key("sec:drift:records:cron") == ("records", "cron")
    assert drift.parse_key("sec:drift:booking:authorized_keys") == ("booking", "authorized_keys")
    assert drift.parse_key("sec:cron:records") is None
    assert drift.parse_key("home:exposed") is None


def test_detail_names_the_file_the_host_and_when_never_a_content_diff():
    db = SessionLocal()
    got = drift.detail({"records": m.Host(name="records", last={"drift": {"cron": "abc1234567"}})},
                        "sec:drift:records:cron", "2026-09-01T12:00:00-04:00")
    db.close()
    assert got["host"] == "records"
    assert "cron" in got["file"].lower()
    assert got["changed_at"] == "2026-09-01T12:00:00-04:00"
    assert got["fingerprint"] == "abc1234567"
    assert "fingerprint" in got["note"] and "cannot show what changed" in got["note"]


def test_detail_is_none_for_a_non_drift_alert():
    assert drift.detail({}, "sec:cron:records:aa:bb", "ts") is None
    assert drift.detail({}, "home:exposed", "ts") is None


def test_a_missing_host_still_returns_the_honest_note_with_an_empty_fingerprint():
    got = drift.detail({}, "sec:drift:ghost-host:ufw_rules", "2026-09-01T12:00:00-04:00")
    assert got["host"] == "ghost-host"
    assert got["fingerprint"] == ""
    assert got["note"]


def test_the_inbox_row_for_a_drift_alert_carries_the_detail(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        key = _open_drift_alert(db, tid)
    finally:
        db.close()
    row = next(r for r in client.get("/api/v1/inbox?view=alerts&status=all").json()["rows"] if r["id"] == key)
    assert row["drift"] is not None
    assert row["drift"]["host"] == "Garage NVR"
    assert "cron" in row["drift"]["file"].lower()
    assert row["drift"]["fingerprint"] == "a1b2c3d4e5"
    assert row["drift"]["changed_at"] == row["first_seen"]


def test_an_ordinary_alert_carries_no_drift_detail(client):
    seeded_demo(client)
    rows = client.get("/api/v1/inbox?view=alerts").json()["rows"]
    assert rows, "the seeded demo must have at least one alert"
    assert all(r["drift"] is None for r in rows if not r["id"].startswith("sec:drift:"))
