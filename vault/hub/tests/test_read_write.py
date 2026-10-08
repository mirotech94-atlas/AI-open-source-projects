import datetime as dt
from tests.test_tenants import make_super
from tests.test_sensor_api import enrol
from tests.test_auth import seed_user, login
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from tests import scanfixture


def _add_alert(tid, key, sev="WARN", status="open"):
    db = SessionLocal()
    db.add(m.Alert(tenant_id=tid, key=key, sev=sev, msg="test alert",
                    first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00", status=status))
    db.commit(); db.close()


def _add_rec(tid, key, action=None):
    db = SessionLocal()
    db.add(m.Recommendation(tenant_id=tid, key=key, priority="low", title="do a thing",
                             action=action, last_seen="2026-01-01T00:00:00+00:00"))
    db.commit(); db.close()


def _add_member(client, tid, username, email, role):
    r = client.post(f"/api/v1/tenants/{tid}/users",
                     json={"username": username, "email": email, "display_name": username, "role": role})
    assert r.status_code == 200, r.text


def _ingest(client, H, last, state):
    payload = {"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}
    r = client.post("/api/v1/sensor/ingest", json=payload, headers=H); assert r.status_code == 200, r.text

def first_mac(client):
    """`GET /devices` is a DevicesPayload (Task 19), not a flat list."""
    return client.get("/api/v1/devices").json()["groups"][0]["devices"][0]["mac"]


def seeded(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    client.post("/api/v1/sensor/ingest", json={"last": scanfixture.load_last(), "state": scanfixture.load_state(), "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    return tid, H

def test_overview_and_lists(client):
    seeded(client)
    o = client.get("/api/v1/overview").json()
    assert o["score"] is not None and "verdict" in o and o["devices"]["total"] >= 20
    assert client.get("/api/v1/devices").json()["total"] >= 20
    assert client.get("/api/v1/inbox?view=alerts").status_code == 200

def test_device_label_creates_edit(client):
    tid, H = seeded(client)
    mac = first_mac(client)
    r = client.post(f"/api/v1/devices/{mac}", json={"label": "Kitchen TV", "trusted": True}); assert r.status_code == 200
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    assert e and e[-1]["path"] == "/api/device" and e[-1]["body"]["label"] == "Kitchen TV"
    assert SessionLocal().query(m.Audit).filter_by(action="device.update").count() == 1

def test_viewer_cannot_write(client):
    tid, H = seeded(client)
    client.post("/api/v1/auth/logout")
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer"); login(client, "ann", "a@x.com")
    assert client.post("/api/v1/run").status_code == 403

def test_fix_queues(client):
    tid, H = seeded(client)
    assert client.post("/api/v1/fix", json={"host": "prod", "action": "security_updates"}).status_code == 200
    q = client.get("/api/v1/sensor/queue", headers=H).json()["queued"]
    assert q and q[0]["path"] == "/api/fix"


# --- Finding 1: alert ops + snooze persistence ------------------------------

def test_alert_ops_status_transitions_and_snooze_persists(client):
    tid, H = seeded(client)
    _add_alert(tid, "a1")

    r = client.post("/api/v1/alerts/a1/ack"); assert r.status_code == 200
    assert r.json()["status"] == "acked"
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    # Body shape is the sensor's replayable-edit CONTRACT: sync.apply_edit
    # reads b.get("action") (not "op") at path "/api/alert", and always
    # carries the "hours" key (None outside snooze).
    assert e[-1]["path"] == "/api/alert" and e[-1]["body"] == {"key": "a1", "action": "ack", "hours": None}
    assert set(e[-1]["body"].keys()) == {"key", "action", "hours"}

    r = client.post("/api/v1/alerts/a1/snooze", json={"hours": 4}); assert r.status_code == 200
    body = r.json()
    assert body["status"] == "snoozed" and body["snoozed_until"]
    snoozed_at = dt.datetime.fromisoformat(body["snoozed_until"])
    delta = snoozed_at - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(hours=3, minutes=55) < delta < dt.timedelta(hours=4, minutes=5)
    db_row = SessionLocal().query(m.Alert).filter_by(tenant_id=tid, key="a1").first()
    assert db_row.snoozed_until == body["snoozed_until"]
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    assert e[-1]["body"] == {"key": "a1", "action": "snooze", "hours": 4}

    r = client.post("/api/v1/alerts/a1/resolve"); assert r.status_code == 200
    assert r.json()["status"] == "resolved" and r.json()["snoozed_until"] is None

    r = client.post("/api/v1/alerts/a1/reopen"); assert r.status_code == 200
    assert r.json()["status"] == "open" and r.json()["snoozed_until"] is None


# --- Finding 2: unverified-device predicate ---------------------------------

def test_unverified_uses_scan_list_when_key_present(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = {"ts": "2026-01-01T00:00:00+00:00", "home": {"unverified": [{"mac": "aa:bb:cc:dd:ee:01"}]}}
    state = {"lan": {"known_macs": {
        "aa:bb:cc:dd:ee:01": {"ip": "10.0.0.5", "presence": "ONLINE"},
        "aa:bb:cc:dd:ee:02": {"ip": "10.0.0.6", "presence": "ONLINE"},
    }}}
    _ingest(client, H, last, state)
    o = client.get("/api/v1/overview").json()
    assert o["devices"]["unverified"] == 1
    assert o["next_action"]["path"] == "/api/v1/devices/aa:bb:cc:dd:ee:01"


def test_unverified_falls_back_to_predicate_when_key_absent(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = {"ts": "2026-01-01T00:00:00+00:00", "home": {}}
    state = {"lan": {"known_macs": {
        "aa:bb:cc:dd:ee:03": {"ip": "10.0.0.7", "presence": "ONLINE"},                     # untrusted+unlabeled -> counted
        "aa:bb:cc:dd:ee:04": {"ip": "10.0.0.8", "presence": "ONLINE", "trusted": True},     # trusted -> not counted
        "aa:bb:cc:dd:ee:05": {"ip": "10.0.0.9", "presence": "ONLINE", "label": "Printer"},  # labeled -> not counted
        "aa:bb:cc:dd:ee:06": {"ip": "10.0.0.10", "presence": "ARCHIVED"},                   # archived -> not counted
    }}}
    _ingest(client, H, last, state)
    o = client.get("/api/v1/overview").json()
    assert o["devices"]["unverified"] == 1


# --- Finding 3: device forget route -----------------------------------------

def test_forget_device(client):
    tid, H = seeded(client)
    mac = first_mac(client)

    r = client.post(f"/api/v1/devices/{mac}/forget"); assert r.status_code == 200 and r.json() == {"ok": True}
    assert SessionLocal().query(m.Device).filter_by(tenant_id=tid, mac=mac).first() is None
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    # `net` (the network slug) rides in every device edit as of Task 51, so the
    # sensor's replay can pick the right network's known-devices map.
    assert e[-1]["path"] == "/api/device/forget"
    assert e[-1]["body"] == {"mac": mac, "net": "home-network"}
    assert SessionLocal().query(m.Audit).filter_by(action="device.forget").count() == 1

    assert client.post(f"/api/v1/devices/{mac}/forget").status_code == 404


# --- Finding 4: coverage for rec decide, config, profile --------------------

def test_rec_decide_yes_no_clear(client):
    tid, H = seeded(client)
    _add_rec(tid, "r1")

    r = client.post("/api/v1/recs/r1", json={"decision": "yes"}); assert r.status_code == 200
    assert r.json()["decision"] == "yes"
    assert SessionLocal().query(m.Recommendation).filter_by(tenant_id=tid, key="r1").first().decision == "yes"
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    assert e[-1]["path"] == "/api/rec" and e[-1]["body"] == {"key": "r1", "decision": "yes"}

    assert client.post("/api/v1/recs/r1", json={"decision": "no"}).json()["decision"] == "no"
    assert client.post("/api/v1/recs/r1", json={"decision": "clear"}).json()["decision"] == ""


def test_rec_decide_yes_queues_a_tier2_action(client):
    # A recommendation carrying a real, non-disruptive action queues the
    # fix immediately -- the Activity/queue row a "Yes" is supposed to
    # produce.
    tid, H = seeded(client)
    _add_rec(tid, "r-disk", action={"host": "prod", "action": "cleanup_disk"})
    r = client.post("/api/v1/recs/r-disk", json={"decision": "yes"})
    assert r.status_code == 200
    body = r.json()
    assert body["queue_id"] is not None
    q = SessionLocal().query(m.Queue).filter_by(tenant_id=tid, id=body["queue_id"]).first()
    assert q is not None and q.path == "/api/fix" and q.body == {"host": "prod", "action": "cleanup_disk"}


def test_rec_decide_yes_on_a_disruptive_action_does_not_queue(client):
    # `reboot` (and its TIER3_FIXES siblings) is disruptive: this route has
    # no confirmation step, so a "Yes" here must NEVER queue it straight
    # away -- it has to fall back to "needs-you" (the dashboard hands off
    # to chat, whose own apply_fix mints the tier-3 confirmation and waits
    # on a second, real "yes"). Before this fix, `rec_decide` queued this
    # unconditionally the moment the recommendation carried host+action.
    tid, H = seeded(client)
    _add_rec(tid, "r-reboot", action={"host": "prod", "action": "reboot"})
    r = client.post("/api/v1/recs/r-reboot", json={"decision": "yes"})
    assert r.status_code == 200
    body = r.json()
    assert body["queue_id"] is None
    assert body["status"] == "needs-you"
    assert SessionLocal().query(m.Queue).filter_by(tenant_id=tid).count() == 0


def test_config_update(client):
    tid, H = seeded(client)
    cfg = {"sites": [{"name": "x", "url": "https://x"}], "thresholds": {"cpu": 90}}
    r = client.post("/api/v1/config", json=cfg); assert r.status_code == 200 and r.json() == cfg
    assert SessionLocal().get(m.Tenant, tid).config == cfg
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    # sync.apply_edit reads b.get("config") at path "/api/config" -- the
    # Edit body must wrap the config dict, not be the config dict itself.
    assert e[-1]["path"] == "/api/config" and e[-1]["body"] == {"config": cfg}


def test_profile_update(client):
    tid, H = seeded(client)
    prof = {"owner": "Miro", "email": "miro@x.com", "address": "123 Main St"}
    r = client.post("/api/v1/profile", json=prof); assert r.status_code == 200
    assert r.json()["profile"] == prof
    assert SessionLocal().get(m.Tenant, tid).settings["profile"] == prof
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    # sync.apply_edit reads b.get("owner") / b.get("profile") at path
    # "/api/profile" -- email/address nest under "profile".
    assert e[-1]["path"] == "/api/profile"
    assert e[-1]["body"] == {"owner": "Miro", "profile": {"email": "miro@x.com", "address": "123 Main St"}}


def test_device_quarantine_creates_edit_with_on_flag(client):
    tid, H = seeded(client)
    mac = first_mac(client)

    r = client.post(f"/api/v1/devices/{mac}", json={"quarantined": True}); assert r.status_code == 200
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    # sync.apply_edit reads b.get("mac") / b.get("on") at path
    # "/api/device/quarantine".
    assert e[-1]["path"] == "/api/device/quarantine"
    assert e[-1]["body"] == {"mac": mac, "net": "home-network", "on": True}

    r = client.post(f"/api/v1/devices/{mac}", json={"quarantined": False}); assert r.status_code == 200
    e = client.get("/api/v1/sensor/edits", headers=H).json()["edits"]
    assert e[-1]["path"] == "/api/device/quarantine"
    assert e[-1]["body"] == {"mac": mac, "net": "home-network", "on": False}


# --- Finding 4: role boundaries ---------------------------------------------

def test_operator_role_boundaries(client):
    tid, H = seeded(client)
    mac = first_mac(client)
    _add_member(client, tid, "opuser", "op@x.com", "operator")

    client.post("/api/v1/auth/logout")
    login(client, "opuser", "op@x.com")

    assert client.post("/api/v1/fix", json={"host": "prod", "action": "security_updates"}).status_code == 403
    assert client.post("/api/v1/config", json={}).status_code == 403
    assert client.post("/api/v1/profile", json={}).status_code == 403
    assert client.post(f"/api/v1/devices/{mac}/forget").status_code == 403
    assert client.post("/api/v1/attacker", json={"method": "block_subnet"}).status_code == 403

    assert client.post(f"/api/v1/devices/{mac}", json={"label": "Op Labeled"}).status_code == 200
    assert client.post("/api/v1/run").status_code == 200


def test_viewer_role_boundaries(client):
    tid, H = seeded(client)
    _add_alert(tid, "a1")
    _add_rec(tid, "r1")
    mac = first_mac(client)
    _add_member(client, tid, "vwuser", "vw@x.com", "viewer")

    client.post("/api/v1/auth/logout")
    login(client, "vwuser", "vw@x.com")

    assert client.post(f"/api/v1/devices/{mac}", json={"label": "x"}).status_code == 403
    assert client.post("/api/v1/alerts/a1/ack").status_code == 403
    assert client.post("/api/v1/recs/r1", json={"decision": "yes"}).status_code == 403
    assert client.post("/api/v1/run").status_code == 403
    assert client.post("/api/v1/speedtest").status_code == 403
    assert client.post("/api/v1/attacker", json={"method": "block_subnet"}).status_code == 403


# --- Finding 4: cross-tenant isolation --------------------------------------

def test_cross_tenant_isolation(client):
    tid, H = seeded(client)
    _add_alert(tid, "a1")
    mac = first_mac(client)

    client.post("/api/v1/auth/logout")
    seed_user(username="bob", email="bob@x.com", tenant_slug="acme", role="admin")
    login(client, "bob", "bob@x.com")

    assert client.get("/api/v1/devices").json()["total"] == 0
    assert client.post(f"/api/v1/devices/{mac}", json={"label": "x"}).status_code == 404
    assert client.post("/api/v1/alerts/a1/ack").status_code == 404


# --------------------------------------------------------------------------
# Task 77 / F6: the alert-log export
# --------------------------------------------------------------------------

def test_alert_export_is_a_csv_file_with_the_alert_log_in_it(client):
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    r = client.get("/api/v1/export/alerts.csv?days=30")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=\"vault-alerts-" in r.headers["content-disposition"]

    import csv
    import io
    rows = list(csv.reader(io.StringIO(r.text)))
    from guardian_hub.routers.read import EXPORT_COLUMNS
    assert tuple(rows[0]) == EXPORT_COLUMNS
    assert len(rows) > 1, "the demo tenant has alerts"
    keys = {row[0] for row in rows[1:]}
    inbox = {row["id"] for row in client.get("/api/v1/inbox?view=alerts&status=all").json()["rows"]}
    assert inbox <= keys or keys & inbox, "the export is the same alert log the inbox shows"


def test_the_export_is_audit_logged_as_data_leaving_the_hub(client):
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    client.get("/api/v1/export/alerts.csv?days=7&status=open")
    row = next(r for r in client.get("/api/v1/audit").json()["rows"] if r["action"] == "export.alerts")
    assert row["user"] == "demoadmin"
    assert row["body"]["days"] == 7 and row["body"]["status"] == "open"


def test_the_export_neutralises_a_spreadsheet_formula_from_sensor_text(client):
    """`Alert.msg`/`why`/`evidence` are sensor-authored free text landing in a
    spreadsheet: a cell starting `=`/`+`/`-`/`@` must not be a formula."""
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    from tests.test_seed_helpers import seeded_demo

    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        db.add(m.Alert(tenant_id=tid, key="test:formula", sev="WARN",
                       msg='=HYPERLINK("http://evil.test","click")', why="-2+3", evidence="@SUM(A1)",
                       first_seen="2026-09-09T00:00:00+00:00", last_seen="2026-09-09T00:00:00+00:00",
                       count=1, status="open", confidence=90))
        db.commit()
    finally:
        db.close()

    text = client.get("/api/v1/export/alerts.csv?days=365").text
    line = next(l for l in text.splitlines() if "test:formula" in l)
    assert "'=HYPERLINK" in line and "'-2+3" in line and "'@SUM(A1)" in line
    assert ',=HYPERLINK' not in line


def test_the_export_rejects_a_nonsense_window_or_status(client):
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    assert client.get("/api/v1/export/alerts.csv?days=0").status_code == 422
    assert client.get("/api/v1/export/alerts.csv?days=100000").status_code == 422
    assert client.get("/api/v1/export/alerts.csv?status=nonsense").status_code == 422
    assert client.get("/api/v1/export/alerts.csv?network=nope").status_code == 404


def test_a_viewer_can_export_the_rows_it_can_already_read(client):
    from tests.test_auth import login, seed_user

    seed_user(username="val", email="v@x.com", tenant_slug="viewonly", role="viewer")
    login(client, "val", "v@x.com")
    r = client.get("/api/v1/export/alerts.csv")
    assert r.status_code == 200
    # Its own (empty) tenant only: a header row and nothing else.
    assert len([l for l in r.text.splitlines() if l.strip()]) == 1
