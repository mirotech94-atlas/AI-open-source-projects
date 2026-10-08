"""The SQLite connection settings the hub depends on.

WAL keeps readers running while a write is in flight; the busy timeout decides
what happens to anyone who still has to queue behind that write. The default
5s was short enough that the hourly demo refresh -- one transaction over
thousands of rows -- made the dashboard answer 500 "database is locked" for a
few seconds every hour.
"""
from guardian_hub.db import SessionLocal
from sqlalchemy import text


def test_wal_and_a_patient_busy_timeout():
    db = SessionLocal()
    try:
        assert db.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        # Long enough to outlast a bulk rewrite; a reader waiting a moment
        # beats a reader seeing an error.
        assert db.execute(text("PRAGMA busy_timeout")).scalar() >= 30000
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
    finally:
        db.close()
