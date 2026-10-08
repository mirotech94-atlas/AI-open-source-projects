"""Texting Vault "fix it" must report back when the fix finishes.

The Slack "fix it" flow queues the repair on the sensor and immediately replies
"queued". The sensor runs it and posts the result to the hub -- and THAT is when
the owner needs to hear "done", in the channel they watch. Every `/api/fix` is
human-requested (autonomous repairs never use this queue path), so a completion
notice here is always something someone asked for.
"""
import time

from tests.test_seed_helpers import seeded_demo, enrol_as_tenant_admin
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.integrations import registry


def _wait_for(pred, timeout=2.0, interval=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def _queue_fix(tid, action="security_updates", host="guardian-hub"):
    db = SessionLocal()
    try:
        q = m.Queue(tenant_id=tid, who="demoadmin", path="/api/fix",
                    body={"host": host, "action": action}, status="running")
        db.add(q)
        db.commit()
        return q.id
    finally:
        db.close()


def _post_result(client, token, qid, ok, out):
    return client.post(f"/api/v1/sensor/queue/{qid}/result",
                       json={"ok": ok, "out": out, "result": None},
                       headers={"Authorization": f"Bearer {token}"})


def test_a_finished_fix_reports_done_to_the_channel(client, monkeypatch):
    tid = seeded_demo(client)
    calls = []
    monkeypatch.setattr(registry, "deliver", lambda db, t, msg: calls.append((msg.kind, msg.title, msg.body)))
    token = enrol_as_tenant_admin(client)
    qid = _queue_fix(tid)
    assert _post_result(client, token, qid, True, "remaining=0").status_code == 200
    assert _wait_for(lambda: len(calls) >= 1)
    kind, title, body = calls[-1]
    assert title == "Vault: fix done"
    assert body.startswith("✓ Done: ran")
    assert "remaining=0" in body


def test_a_failed_fix_reports_failure(client, monkeypatch):
    tid = seeded_demo(client)
    calls = []
    monkeypatch.setattr(registry, "deliver", lambda db, t, msg: calls.append((msg.kind, msg.title, msg.body)))
    token = enrol_as_tenant_admin(client)
    qid = _queue_fix(tid, action="enable_ufw", host="guardian-hub")
    assert _post_result(client, token, qid, False, "ssh: connection refused").status_code == 200
    assert _wait_for(lambda: len(calls) >= 1)
    kind, title, body = calls[-1]
    assert title == "Vault: fix failed"
    assert body.startswith("✗")
    assert "failed" in body
    assert "connection refused" in body   # the raw 'ssh: connection refused' is humanised
