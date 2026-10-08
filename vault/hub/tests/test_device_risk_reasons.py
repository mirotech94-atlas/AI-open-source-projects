"""A device's risk score comes with its arithmetic, mirroring the sensor's
formula, so "risk 40" is never a bare number."""
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import assets


def test_risk_reasons_explain_this_macs_40(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    t = db.get(m.Tenant, tid)
    d = db.query(m.Device).filter_by(tenant_id=tid).first()
    d.ports = {"22": "t", "445": "t", "5000": "t", "5900": "t", "53": "t"}
    d.trusted, d.label, d.quarantined = True, "This Mac", False
    db.commit()
    reasons = assets.device_detail(db, t, d.mac).risk_reasons
    whys = " | ".join(r.why for r in reasons)
    assert sum(r.points for r in reasons) == 40
    assert "smb (445)" in whys and "vnc (5900)" in whys      # the +25 names the risky services
    assert "3 other open port(s)" in whys                     # the +15
