import time

from guardian_hub import demoscrub
from tests.test_seed_helpers import enrol_as_tenant_admin, seeded_demo, confirmed_fix

#: The fixture's second ssh host, as the demo scrub renames it.
DEMO_HOST = demoscrub.map_host_name("demo")
#: and its first, the one the CVE tests attach a synthetic CVE to.
PROD_HOST = demoscrub.map_host_name("prod")


def test_updates_payload(client):
    seeded_demo(client)
    u = client.get("/api/v1/updates").json()
    assert u["calendar"] and all(c["state"] in ("patched", "reboot", "failed", "none") for c in u["calendar"])
    assert u["hosts"] and u["cves"]
    assert all(c["tracker"].startswith("https://security-tracker.debian.org/tracker/CVE-") for c in u["cves"])
    assert u["reboot_plan"]["windows"] and len(u["reboot_plan"]["windows"]) == 4


def test_reboot_plan_reason_comes_from_the_seeded_alert(client):
    # `hub/tests/fixtures/last.json` marks its third host reboot_required with
    # a matching Alert row keyed "reboot:<host>" -- the planner's `reason`
    # must be that alert's real message, not a placeholder.
    seeded_demo(client)
    plan = client.get("/api/v1/updates").json()["reboot_plan"]
    name = demoscrub.map_host_name("safehome")
    host = next(h for h in plan["hosts"] if h["host"] == name)
    assert host["reason"] == f"{name} needs a reboot (kernel update)"


def test_calendar_cell_reflects_a_real_seeded_fix_row(client):
    # Rather than hardcode which (host, day) the deterministic demo seed
    # happens to land a reboot on, read it back from the actual `Fix` rows
    # `seed.seed_demo` wrote and assert the calendar cell agrees -- this is
    # the "real values, not shape" check the fix round asked for.
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from guardian_hub.scans import parse_ts
    db = SessionLocal()
    try:
        f = db.query(m.Fix).filter_by(tenant_id=tid, action="reboot", ok=True).first()
        assert f is not None
        day = parse_ts(f.ts).date().isoformat()
        host = f.host
    finally:
        db.close()

    cal = client.get("/api/v1/updates").json()["calendar"]
    cell = next(c for c in cal if c["host"] == host and c["day"] == day)
    assert cell["state"] == "reboot"
    assert cell["reboots"] >= 1


def test_cve_fixed_on_reflects_a_real_fix_after_first_seen(client):
    # Manufacture a CVE that "first appears" in an older scan and gets
    # "fixed" by a real `Fix` row after it, then confirm `cves()` reports
    # that Fix's own ts as `fixed_on` -- not a placeholder/derived string.
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from guardian_hub.scans import latest_scan
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    older_ts = (now - dt.timedelta(days=20)).isoformat(timespec="seconds")
    fixed_ts = (now - dt.timedelta(days=19)).isoformat(timespec="seconds")

    db = SessionLocal()
    try:
        latest = latest_scan(db, tid)
        raw = dict(latest.raw)
        raw["ssh"] = [dict(h) for h in raw.get("ssh") or []]
        for h in raw["ssh"]:
            if h.get("name") == PROD_HOST:
                h["cve_list"] = (h.get("cve_list") or []) + [
                    {"cve": "CVE-9999-0001", "package": "testpkg", "urgency": "important"}
                ]
        latest.raw = raw
        db.add(latest)
        # An older scan that already carries the same CVE -- this is what
        # cves()'s "first appeared" scan pinpoints as the appearance point.
        db.add(m.Scan(tenant_id=tid, ts=older_ts, raw=raw))
        db.add(m.Fix(tenant_id=tid, ts=fixed_ts, host=PROD_HOST, action="full_upgrade", ok=True, out=""))
        db.commit()
    finally:
        db.close()

    cves = client.get("/api/v1/updates").json()["cves"]
    row = next(c for c in cves if c["cve"] == "CVE-9999-0001" and c["host"] == PROD_HOST)
    assert row["fixable"] is True
    assert row["fixed_on"] == fixed_ts


def test_cve_fixable_sentinel_is_clamped_to_zero(client):
    # The sensor's debsecan sentinel for "hasn't run yet" is -1
    # (guardian_sensor/hosts.py) -- the hub must never surface a negative
    # "known vulnerabilities" count.
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from guardian_hub.scans import latest_scan
    db = SessionLocal()
    try:
        scan = latest_scan(db, tid)
        raw = dict(scan.raw)
        raw["ssh"] = [dict(h) for h in raw.get("ssh") or []]
        for h in raw["ssh"]:
            if h.get("name") == DEMO_HOST:
                h["cve_fixable"] = -1
        scan.raw = raw
        db.add(scan)
        db.commit()
    finally:
        db.close()

    hosts = client.get("/api/v1/updates").json()["hosts"]
    row = next(h for h in hosts if h["name"] == DEMO_HOST)
    assert row["cve_fixable"] == 0


def test_upgrade_ws_frame_has_the_shape_the_web_reads(client):
    # UpgradeProgressPanel.tsx's mergeFrame reads queue_id/host/action/
    # status/done/total/log_tail/finished off the `upgrade` frame -- a
    # frame missing status/log_tail/finished is silently ignored by the
    # web client, which is exactly the bug this fix round closes.
    tid = seeded_demo(client)
    fix_id = confirmed_fix(client, PROD_HOST, "full_upgrade").json()["id"]
    H = _reporting_sensor(client)
    client.get("/api/v1/sensor/queue", headers=H)  # the sensor picking the job up -> status "running"

    with client.websocket_connect("/api/v1/ws") as ws:
        assert ws.receive_json()["type"] == "ready"

        client.post(f"/api/v1/sensor/queue/{fix_id}/progress",
                    json={"done": 3, "total": 10, "line": "Setting up libc6"}, headers=H)
        frame = ws.receive_json()
        assert frame["type"] == "upgrade"
        data = frame["data"]
        assert data["queue_id"] == fix_id
        assert data["status"] == "running"
        assert data["done"] == 3 and data["total"] == 10
        assert data["log_tail"] == "Setting up libc6"
        assert data["finished"] is None

        # A measurable gap so "finished" (real completion time) can't
        # coincidentally match "started" (enqueue time) at second
        # resolution -- that coincidence is exactly what let the old
        # fabricated-from-`ts` bug hide.
        time.sleep(1.1)
        client.post(f"/api/v1/sensor/queue/{fix_id}/result", json={"ok": True, "out": "done"}, headers=H)
        # qresult publishes a "queue" frame (existing behaviour) and then
        # the "upgrade" frame this fix round adds -- skip past the former.
        frame2 = ws.receive_json()
        if frame2["type"] != "upgrade":
            frame2 = ws.receive_json()
        assert frame2["type"] == "upgrade"
        data2 = frame2["data"]
        assert data2["queue_id"] == fix_id
        assert data2["status"] == "done"
        assert data2["finished"]

    prog = client.get("/api/v1/updates").json()["progress"]
    row = next(p for p in prog if p["queue_id"] == fix_id)
    assert row["status"] == "done"
    assert row["finished"] is not None and row["finished"] != row["started"]


def test_reboot_plan_queues_with_a_window_and_progress_lands(client):
    seeded_demo(client)
    qid = client.post("/api/v1/reboot-plan", json={"host": demoscrub.map_host_name("safehome"),
        "window_start": "2099-01-01T02:00:00Z", "window_end": "2099-01-01T05:00:00Z"}).json()["id"]
    # the sensor picks the queue up, then reports progress on a separate upgrade job
    fix_id = confirmed_fix(client, PROD_HOST, "full_upgrade").json()["id"]
    H = _reporting_sensor(client)
    client.get("/api/v1/sensor/queue", headers=H)
    client.post(f"/api/v1/sensor/queue/{fix_id}/progress",
                json={"done": 12, "total": 40, "line": "Setting up libc6"}, headers=H)
    prog = client.get("/api/v1/updates").json()["progress"]
    assert any(p["queue_id"] == fix_id and p["done"] == 12 and p["total"] == 40 for p in prog)
    assert client.get(f"/api/v1/queue/{qid}").json()["path"] == "/api/reboot-plan"


def test_updates_answers_quickly_on_the_demo_tenant(client):
    # `cves()`/`calendar()` are the only builders here that scan more than
    # the latest Scan row -- this is the regression guard for that (see
    # payloads/updates.py's cutoff-bounded Scan/Fix queries).
    seeded_demo(client)
    t0 = time.monotonic()
    assert client.get("/api/v1/updates").status_code == 200
    assert time.monotonic() - t0 < 2.0


def _reporting_sensor(client):
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m, security
    from guardian_hub.scans import latest_scan
    token=enrol_as_tenant_admin(client)
    with SessionLocal() as db:
        sensor=db.query(m.Sensor).filter_by(token_hash=security.hash_token(token)).one()
        scan=latest_scan(db,sensor.tenant_id)
        db.add(m.Scan(tenant_id=sensor.tenant_id,sensor_id=sensor.id,ts=m.utcnow().isoformat(),raw=scan.raw))
        db.commit()
    return {"Authorization":f"Bearer {token}"}
