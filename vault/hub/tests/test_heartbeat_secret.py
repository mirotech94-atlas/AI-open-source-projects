"""The heartbeat token belongs to exactly one surface: an admin-only,
audited route. Everything else — alert prose, the chat DATA blob, the
public API — must not carry it.

The fake tokens here are deliberately obviously fake (`FAKE-...`) so that a
grep for a leaked credential in this repository never lands on a test
fixture and never lands on something that would work.
"""

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import ingest, models as m, security

FAKE_TOKEN = "FAKE-HEARTBEAT-TOKEN-DO-NOT-USE"
#: The demo scan declares this heartbeat, and `ingest.store_scan` now creates
#: the hub row for it, so these tests pin the row's token rather than
#: inserting a second one.
HB_NAME = "nightly-backup"


def _hb(tid: str, token: str = FAKE_TOKEN) -> m.Heartbeat:
    db = SessionLocal()
    row = db.query(m.Heartbeat).filter_by(tenant_id=tid, name=HB_NAME).first()
    if row is None:
        row = m.Heartbeat(tenant_id=tid, name=HB_NAME, expect_minutes=1440)
        db.add(row)
    row.token = token
    db.commit()
    return row


# --------------------------------------------------------------------------
# The redaction helper itself
# --------------------------------------------------------------------------

def test_redact_prose_strips_a_ping_url_but_leaves_ordinary_text():
    poisoned = f"Heartbeat 'x' never pinged yet — add `curl -s https://h.example/hb/x/{FAKE_TOKEN}` to the job"
    out = security.redact_prose(poisoned)
    assert FAKE_TOKEN not in out
    assert "/hb/x/" in out, "the reader still needs to know which heartbeat it is"
    assert security.redact_prose("disk 91% full") == "disk 91% full"
    assert security.redact_prose("") == ""


# --------------------------------------------------------------------------
# Ingest: the single choke point for sensor-written prose
# --------------------------------------------------------------------------

def test_a_poisoned_alert_from_an_old_sensor_is_scrubbed_on_the_way_in(client):
    """A sensor build older than the fix still sends the token. The stored
    row is what reports, integrations, chat and the public API all read, so
    it must never hold the credential."""
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    ingest.store_scan(db, tenant, None, {"last": {
        "ts": "2026-09-09T00:00:00+00:00",
        "alerts": [{
            "key": f"hb:{HB_NAME}:setup", "sev": "INFO",
            "msg": f"Heartbeat '{HB_NAME}' never pinged yet — add "
                   f"`curl -s https://h.example/hb/{HB_NAME}/{FAKE_TOKEN}` to the end of that job",
            "state": "open",
        }],
    }}, deliver=False)
    db.commit()
    row = db.query(m.Alert).filter_by(tenant_id=tid, key=f"hb:{HB_NAME}:setup").one()
    assert FAKE_TOKEN not in row.msg
    assert HB_NAME in row.msg


# --------------------------------------------------------------------------
# The admin-only routes
# --------------------------------------------------------------------------

def test_setup_route_gives_an_admin_the_command_and_audits_the_disclosure(client):
    tid = seeded_demo(client)
    _hb(tid)
    body = client.get(f"/api/v1/heartbeats/{HB_NAME}/setup").json()
    assert body["name"] == HB_NAME
    assert FAKE_TOKEN in body["command"], "the whole point of this route is the working command"
    assert body["command"].startswith("curl ")
    assert body["rotated"] is False

    db = SessionLocal()
    logged = db.query(m.Audit).filter_by(tenant_id=tid, action="heartbeat.reveal").all()
    assert len(logged) == 1 and logged[0].target == HB_NAME
    # The audit log is itself readable in the app: logging the token there
    # would just move the leak.
    assert FAKE_TOKEN not in str(logged[0].body)


def test_setup_route_404s_on_an_unknown_heartbeat(client):
    seeded_demo(client)
    assert client.get("/api/v1/heartbeats/no-such-job/setup").status_code == 404


def test_rotation_invalidates_the_old_token_immediately(client):
    tid = seeded_demo(client)
    _hb(tid)
    # The old token works right up to the rotation.
    assert client.get(f"/hb/{HB_NAME}/{FAKE_TOKEN}").json()["ok"] is True

    rotated = client.post(f"/api/v1/heartbeats/{HB_NAME}/rotate").json()
    assert rotated["rotated"] is True
    new_token = rotated["url"].rsplit("/", 1)[1]
    assert new_token != FAKE_TOKEN and len(new_token) >= 32

    assert client.get(f"/hb/{HB_NAME}/{FAKE_TOKEN}").status_code == 404, "no grace period"
    assert client.get(f"/hb/{HB_NAME}/{new_token}").json()["ok"] is True

    db = SessionLocal()
    logged = db.query(m.Audit).filter_by(tenant_id=tid, action="heartbeat.rotate").all()
    assert len(logged) == 1 and logged[0].target == HB_NAME
    assert new_token not in str(logged[0].body) and FAKE_TOKEN not in str(logged[0].body)


def test_a_viewer_cannot_read_or_rotate_the_token(client):
    """Revealing a write credential is an admin act, and so is replacing it.
    A viewer reading the Security page sees the heartbeat and its state and
    nothing else."""
    tid = seeded_demo(client)
    _hb(tid)
    db = SessionLocal()
    for mem in db.query(m.Membership).filter_by(tenant_id=tid).all():
        mem.role = "viewer"
    db.commit()
    assert client.get(f"/api/v1/heartbeats/{HB_NAME}/setup").status_code == 403
    assert client.post(f"/api/v1/heartbeats/{HB_NAME}/rotate").status_code == 403


# --------------------------------------------------------------------------
# The payload and prompt surfaces
# --------------------------------------------------------------------------

def test_the_raw_scan_route_never_carries_the_token(client):
    """`GET /scans/latest` hands any VIEWER the whole sensor payload, and the
    sensor's `heartbeats_check` puts each token in it. Ingest pulls them out
    before `Scan.raw` is ever stored -- otherwise this is the alert-message
    leak again, through a door with a lower role gate."""
    seeded_demo(client)
    raw = client.get("/api/v1/scans/latest").json()
    hbs = raw.get("heartbeats") or {}
    assert hbs, "the seeded scan is expected to declare a heartbeat"
    for name, hb in hbs.items():
        assert "token" not in hb, f"{name} still ships its token in Scan.raw"
        assert hb.get("every_min"), "the useful half of the row is still there"


def test_the_security_payload_never_carries_the_token(client):
    tid = seeded_demo(client)
    _hb(tid)
    body = client.get("/api/v1/security").text
    assert FAKE_TOKEN not in body
    assert HB_NAME in body, "the heartbeat itself is still listed"


def test_the_chat_data_blob_never_carries_a_ping_url(client):
    """Alert prose lands in the model prompt verbatim; a row written before
    the ingest guard existed must still not reach a provider."""
    from guardian_hub.chat import prompt
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    alert = db.query(m.Alert).filter_by(tenant_id=tid).first()
    alert.msg = f"Heartbeat '{HB_NAME}' never pinged — curl -s https://h.example/hb/{HB_NAME}/{FAKE_TOKEN}"
    db.commit()
    blob = prompt.context_blob(db, tenant)
    assert FAKE_TOKEN not in blob
