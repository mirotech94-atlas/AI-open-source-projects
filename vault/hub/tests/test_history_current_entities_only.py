"""Trend series are drawn only for sites and hosts that still exist.

The owner's Websites tab listed six names per chart -- three current sites
and three retired ones ("Safehome public (safehome.example.com)", "Roots prod
(status.example.org)", ...) whose metric rows were still inside the window. A
retired site's history is kept (retention prunes it), but a legend that
offers it as a comparison is a claim that Vault still watches it.
"""
import datetime as dt

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m


def _stale_rows(tid, entity, name="latency_ms"):
    db = SessionLocal()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        for i in range(3):
            db.add(m.Metric(tenant_id=tid, name=name, entity=entity, value=120.0 + i,
                            ts=(now - dt.timedelta(hours=i + 1)).isoformat(timespec="seconds")))
        db.commit()
    finally:
        db.close()


def test_site_charts_skip_a_site_that_no_longer_exists(client):
    tid = seeded_demo(client)
    _stale_rows(tid, "Safehome public (safehome.example.com)")
    _stale_rows(tid, "Safehome public (safehome.example.com)", name="up")
    _stale_rows(tid, "Safehome public (safehome.example.com)", name="cert_days")
    body = client.get("/api/v1/sites").json()
    current = {s["name"] for s in body["sites"]}
    for chart in body["charts"]:
        names = {s["name"] for s in chart["series"]}
        assert "Safehome public (safehome.example.com)" not in names, chart["key"]
        assert names <= current, (chart["key"], names - current)


def test_history_tab_skips_a_host_that_no_longer_exists(client):
    tid = seeded_demo(client)
    _stale_rows(tid, "old-box", name="disk_pct")
    body = client.get("/api/v1/history?days=7").json()
    disk = next((c for c in body["charts"] if c["key"] == "disk"), None)
    assert disk is not None
    assert "old-box" not in {s["name"] for s in disk["series"]}
