"""Task 83 header work: `GET /live`'s sites carry a short latency sparkline,
sourced from the same `Metric(name="latency_ms")` rows `payloads/assets.py`
already reads for the Sites tab's own sparkline -- the header must not fake a
line the hub does not already have."""

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def test_live_sites_carry_a_latency_spark_from_stored_metrics(client):
    seeded_demo(client)
    body = client.get("/api/v1/live").json()
    sites = body["sites"]
    assert sites, "the demo tenant has configured sites"
    for site in sites:
        assert "spark" in site
        assert isinstance(site["spark"], list)

    db = SessionLocal()
    try:
        tenant = db.query(m.Tenant).filter_by(slug="demo").first()
        has_metrics = (
            db.query(m.Metric)
            .filter_by(tenant_id=tenant.id, name="latency_ms")
            .first()
            is not None
        )
    finally:
        db.close()
    assert has_metrics, "the demo fixture is expected to seed latency_ms history"
    assert any(site["spark"] for site in sites), "at least one seeded site should have spark points"


def test_live_spark_is_capped_and_chronological(client):
    seeded_demo(client)
    body = client.get("/api/v1/live").json()
    for site in body["sites"]:
        spark = site["spark"]
        assert len(spark) <= 12
        assert all(isinstance(v, (int, float)) for v in spark)


def test_live_spark_survives_the_probe_cache_untouched(client):
    """The 10s probe cache (`_cached_probe`) stores probe-only `LiveSite`
    rows; the spark is attached fresh in the route handler on every request,
    so a second call within the cache window must still carry it (not drop
    it because the cached copy predates the field)."""
    seeded_demo(client)
    first = client.get("/api/v1/live").json()
    second = client.get("/api/v1/live").json()
    assert all("spark" in s for s in first["sites"])
    assert all("spark" in s for s in second["sites"])
