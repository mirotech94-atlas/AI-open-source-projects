"""Unattended auto-fixes show up on the Activity page.

`fix.remediate()` runs on the sensor's own schedule, with no dashboard queue
row -- it writes straight into the `Fix` table via the ingest tail of
`remediation.jsonl`. The Activity page reads only `Queue`, so every self-driven
repair Vault makes -- exactly the "handled without asking" work the Inbox's
own triage counters advertise -- was invisible on the one page whose entire
purpose is "every job Vault is running or has just run". Overview's own Fix
Log showed a long history while Activity said "no jobs have run yet".

A fix Vault ran through the dashboard queue (`by` starting with "hub") is
NOT re-added here: `apply_fix` always appends to `remediation.jsonl`
regardless of how it was invoked, so that same fix already has a real `Queue`
row with a real lifecycle. Only `by == "auto"` has no `Queue` row at all.
"""

import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import jobs as jobs_payload
from tests.test_auth import login, seed_user


def _fix(db, tid, *, by="auto", ts=None, action="cleanup_disk", host="prod", ok=True, label="free disk space"):
    row = m.Fix(tenant_id=tid, ts=ts or dt.datetime.now(dt.timezone.utc).isoformat(),
                host=host, action=action, label=label, arg=None, by=by, ok=ok, out="")
    db.add(row)
    db.commit()
    return row


def test_an_unattended_autofix_appears_on_activity(client):
    _, tid = seed_user(role="admin")
    login(client)
    db = SessionLocal()
    try:
        _fix(db, tid, by="auto")
    finally:
        db.close()

    rows = client.get("/api/v1/jobs").json()["jobs"]
    assert any(r["label"] == "free disk space" and r["target"] == "prod" for r in rows), (
        "an unattended auto-fix never appeared on the Activity page"
    )


def test_a_dashboard_queued_fix_is_not_duplicated(client):
    """The same fix reaches both `Queue` (created when queued) and `Fix`
    (written when the sensor's remediation log syncs back) -- it must show
    once, via its real Queue lifecycle, not a second time from the Fix row."""
    _, tid = seed_user(role="admin")
    login(client)
    db = SessionLocal()
    try:
        q = m.Queue(tenant_id=tid, who="miro", path="/api/fix",
                    body={"host": "prod", "action": "cleanup_disk"}, status="done",
                    result={"ok": True, "out": "", "label": "free disk space"})
        db.add(q)
        db.commit()
        _fix(db, tid, by="hub:miro")
    finally:
        db.close()

    rows = client.get("/api/v1/jobs").json()["jobs"]
    matches = [r for r in rows if r["label"] == "free disk space" and r["target"] == "prod"]
    assert len(matches) == 1, f"expected exactly one row, got {len(matches)}: {matches}"


def test_an_autofix_names_guardian_as_who(client):
    _, tid = seed_user(role="admin")
    login(client)
    db = SessionLocal()
    try:
        _fix(db, tid, by="auto")
    finally:
        db.close()

    rows = client.get("/api/v1/jobs").json()["jobs"]
    row = next(r for r in rows if r["label"] == "free disk space")
    assert row["who"] == "Vault", row["who"]
    assert row["status"] == "done"
    assert row["stuck"] is False
    assert row["retryable"] is False
