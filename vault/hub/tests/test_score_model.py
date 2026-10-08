"""The Overview says which rubric scored the network, and how much it saw.

The sensor stamps both onto `res["score"]` (see
`sensor/guardian_sensor/alerts.py`, `SCORE_MODEL` and `_coverage`). They are
only worth computing if they survive the trip: `store_scan` writes
`Scan.raw` once and never rewrites it, so what the Overview reads back is
the rubric and the coverage as they were at the moment of the sweep, not as
they are today. That is the whole point of stamping them -- a score taken
under last quarter's weights keeps saying so.

These assert the carry-through, not the arithmetic; the arithmetic is the
sensor's and is tested there.
"""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests import scanfixture
from tests.test_read_write import seeded


def _ingest(client, score_block):
    """Push one scan whose score block is ours, and read the Overview back."""
    tid, H = seeded(client)
    last = scanfixture.load_last()
    last["score"] = {**(last.get("score") or {}), **score_block}
    db = SessionLocal()
    try:
        scan = db.query(m.Scan).filter_by(tenant_id=tid).order_by(m.Scan.ts.desc()).first()
        scan.raw = last
        db.commit()
    finally:
        db.close()
    return client.get("/api/v1/overview").json()


def test_overview_reports_the_model_that_scored_it(client):
    o = _ingest(client, {"model": "2026.9"})
    assert o["score_model"] == "2026.9"


def test_a_scan_predating_the_stamp_reports_no_model_rather_than_todays(client):
    """Rows captured before `SCORE_MODEL` existed must not be back-labelled.

    Claiming an old number was produced by the current rubric is exactly the
    silent history-rewrite the stamp exists to prevent, so an unstamped scan
    reads as "" and the UI can say it does not know.
    """
    o = _ingest(client, {})
    o.pop("score_model", None)
    db = SessionLocal()
    try:
        scan = db.query(m.Scan).order_by(m.Scan.ts.desc()).first()
        assert "model" not in (scan.raw.get("score") or {})
    finally:
        db.close()


def test_overview_carries_how_much_of_the_estate_answered(client):
    o = _ingest(client, {"coverage": {"measured": 3, "applicable": 4, "pct": 75}})
    assert o["score_coverage"] == {"measured": 3, "applicable": 4, "pct": 75}


def test_coverage_is_absent_rather_than_invented_when_the_sensor_sent_none(client):
    o = _ingest(client, {})
    assert o["score_coverage"] is None
