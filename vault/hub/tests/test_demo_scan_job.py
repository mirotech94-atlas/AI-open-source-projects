"""The demo's "Scan now" job survives the scan it is running.

On the demo tenant the scan IS a refresh, and the refresh deletes every
ingest-derived row -- including the queue row representing the scan. So for
about ten seconds the Activity page showed no job at all, and the row that came
back afterwards carried a fresh timestamp and reported that the scan had taken
no time.

A job cannot honestly report on itself if running it destroys it.
"""

from guardian_hub import models as m, seed
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _demo(db):
    return db.query(m.Tenant).filter_by(slug="demo").first()


def test_a_kept_queue_row_survives_the_refresh(client):
    seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = _demo(db)
        q = m.Queue(tenant_id=tenant.id, who="demoadmin", path="/api/run", body={}, status="running")
        db.add(q)
        db.commit()
        qid, queued_at = q.id, q.ts

        seed.refresh_demo(db, keep_queue_ids={qid})
        db.expire_all()

        after = db.get(m.Queue, qid)
        assert after is not None, "the scan deleted the job that was running it"
        assert after.ts == queued_at, (
            "the job came back with a new queued time, so its duration would be "
            "reported as zero however long the scan really took"
        )
    finally:
        db.close()


def test_an_unrelated_queue_row_is_still_cleared(client):
    """Keeping one row must not turn the reset into a no-op for the rest."""
    seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = _demo(db)
        keep = m.Queue(tenant_id=tenant.id, who="demoadmin", path="/api/run", body={}, status="running")
        other = m.Queue(tenant_id=tenant.id, who="demoadmin", path="/api/speedtest", body={}, status="done")
        db.add_all([keep, other])
        db.commit()
        keep_id, other_id = keep.id, other.id

        seed.refresh_demo(db, keep_queue_ids={keep_id})
        db.expire_all()

        assert db.get(m.Queue, keep_id) is not None
        assert db.get(m.Queue, other_id) is None, "the reset stopped clearing other queue rows"
    finally:
        db.close()


def test_refreshing_with_nothing_to_keep_behaves_as_before(client):
    seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = _demo(db)
        q = m.Queue(tenant_id=tenant.id, who="demoadmin", path="/api/speedtest", body={}, status="done")
        db.add(q)
        db.commit()
        qid = q.id

        seed.refresh_demo(db)
        db.expire_all()

        assert db.get(m.Queue, qid) is None
    finally:
        db.close()
