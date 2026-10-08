from guardian_hub import demoscrub, models as m
from guardian_hub.db import SessionLocal
from guardian_hub.seed import seed_demo, synth_history
import json, pathlib
FX = pathlib.Path(__file__).parent / "fixtures"


def test_synth_history_is_dense_and_bounded():
    last = json.load(open(FX / "last.json"))
    rows = synth_history(last, days=30, step_minutes=30)
    assert 1400 <= len(rows) <= 1500
    assert all(78 <= r["score"] <= 92 for r in rows)
    assert any(r["speed"] for r in rows) and any(r["live_macs"] for r in rows)
    assert sorted(rows, key=lambda r: r["ts"]) == rows


def test_seed_demo_fills_every_tab(client):
    db = SessionLocal()
    out = seed_demo(db)
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    assert t.settings["demo_code"] == "424242"
    assert db.query(m.Device).filter_by(tenant_id=t.id).count() >= 20
    assert db.query(m.Metric).filter_by(tenant_id=t.id, name="score").count() > 1000
    assert db.query(m.PresenceSample).filter_by(tenant_id=t.id).count() > 5000
    assert db.query(m.DnsQuery).filter_by(tenant_id=t.id).count() == 3000
    assert db.query(m.Alert).filter_by(tenant_id=t.id).count() > 0
    assert out["tenant_id"] == t.id

    # Task 14: the demo has traffic to look at -- flow rows seeded, and the
    # primary network declared "mirror" so `payloads/traffic.mode_for` shows
    # something other than "none".
    assert db.query(m.Flow).filter_by(tenant_id=t.id).count() > 0
    primary = db.query(m.Network).filter_by(tenant_id=t.id, is_primary=True).one()
    assert primary.traffic_mode == "mirror"

    counts_before = {
        "device": db.query(m.Device).filter_by(tenant_id=t.id).count(),
        "metric": db.query(m.Metric).filter_by(tenant_id=t.id).count(),
        "event": db.query(m.Event).filter_by(tenant_id=t.id).count(),
        "dns": db.query(m.DnsQuery).filter_by(tenant_id=t.id).count(),
        "presence": db.query(m.PresenceSample).filter_by(tenant_id=t.id).count(),
        "fix": db.query(m.Fix).filter_by(tenant_id=t.id).count(),
        "flow": db.query(m.Flow).filter_by(tenant_id=t.id).count(),
    }

    seed_demo(db)   # idempotent
    assert db.query(m.Tenant).filter_by(slug="demo").count() == 1
    for name, cnt in counts_before.items():
        model = {"device": m.Device, "metric": m.Metric, "event": m.Event,
                 "dns": m.DnsQuery, "presence": m.PresenceSample, "fix": m.Fix,
                 "flow": m.Flow}[name]
        assert db.query(model).filter_by(tenant_id=t.id).count() == cnt, f"{name} row count changed on re-seed"


def test_seed_demo_forces_the_demo_tenants_identity_over_an_import(client):
    """The live `demo` tenant on the VPS was imported from Nexus and carries
    the owner's real name, site config and profile. That tenant's sign-in is
    public, so re-seeding must *replace* all three rather than preserve them:
    `reset_tenant` never touches `Tenant`, so a preserved config would keep
    serving the real site names and urls through Settings while every
    ingested row was scrubbed -- and `_publish_demo_sites` matches
    `Site.name` against the config, so a stale config would publish nothing
    and leave `/status/demo` empty. Nothing is lost: `refresh_demo` re-seeds
    this tenant from the same fixtures every hour anyway.
    """
    db = SessionLocal()
    t = m.Tenant(
        slug="demo", name="Imported Demo Network",
        config={"sites": [{"name": "real-site", "url": "https://real.example"}]},
        settings={"profile": {"owner": "someone@real.example", "address": "1 Real Street"}},
    )
    db.add(t); db.commit()

    out = seed_demo(db)
    db.refresh(t)
    assert t.name == demoscrub.BRAND
    assert "real-site" not in json.dumps(t.config)
    assert "real.example" not in json.dumps(t.config)
    assert t.settings["profile"] == {"owner": demoscrub.BRAND, "email": "", "address": ""}
    assert t.settings["demo_code"] == "424242"
    assert out["tenant_id"] == t.id
    # the forced config names the same sites that were ingested, so
    # `_publish_demo_sites` has something to match and /status/demo is not empty
    assert {s["name"] for s in t.config["sites"]} == \
        {r.name for r in db.query(m.Site).filter_by(tenant_id=t.id).all()}
    assert db.query(m.Site).filter_by(tenant_id=t.id, public=True).count() > 0
