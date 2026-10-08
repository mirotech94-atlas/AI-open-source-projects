"""Item 34: pending updates reach the client's own channel.

Alerts, resolutions and reports already did; updates never did — so the
one finding with a known fix and a known fixer was the one nobody heard
about until they opened the tab.
"""
from unittest.mock import patch

from guardian_hub.db import SessionLocal
from guardian_hub import models as m, scheduler
from guardian_hub.integrations import registry
from tests.test_auth import seed_user


def _scan(tid, ssh):
    return m.Scan(tenant_id=tid, ts="2026-09-22T10:00:00-04:00", score=90, health={},
                  raw={"ssh": ssh})


def _run():
    # The scheduler imports the registry lazily inside the job, so the patch
    # has to land on the module it imports FROM, not on the scheduler.
    with patch.object(registry, "deliver_background") as sent:
        scheduler.patch_digest_job()
        return sent.call_args_list


def test_a_security_update_reaches_the_channel(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_scan(tid, [{"name": "vps1", "updates": {"count": 4},
                        "sec": {"security_updates": ["openssl"]}}]))
    db.commit(); db.close()
    calls = _run()
    assert len(calls) == 1
    msg = calls[0].args[1]
    assert "security update" in msg.body
    assert msg.severity == "WARN", "a known fix is not an emergency"
    assert msg.dedup_key.startswith("patch-digest:")


def test_a_pending_restart_alone_is_enough_to_send(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_scan(tid, [{"name": "vps1", "updates": {"count": 0}, "reboot_required": True}]))
    db.commit(); db.close()
    calls = _run()
    assert len(calls) == 1 and "restart" in calls[0].args[1].body


def test_nothing_to_install_sends_nothing(client):
    # A daily "nothing to install" is how a channel gets muted, and a muted
    # channel misses the message that mattered.
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_scan(tid, [{"name": "vps1", "updates": {"count": 0}}]))
    db.commit(); db.close()
    assert _run() == []


def test_a_server_it_could_not_reach_is_named_in_the_message(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_scan(tid, [{"name": "vps1", "updates": {"count": 1},
                        "sec": {"security_updates": ["openssl"]}}]))
    db.add(m.Host(tenant_id=tid, name="vps1", address="root@1.2.3.4", last={}))
    db.add(m.Host(tenant_id=tid, name="unreachable", address="root@5.6.7.8", last={}))
    db.commit(); db.close()
    body = _run()[0].args[1].body
    assert "could not be reached" in body, "silence about an unseen server is the bug this whole pass is about"


def test_one_tenant_failing_does_not_stop_the_others(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    db.add(_scan(tid, [{"name": "vps1", "updates": {"count": 1},
                        "sec": {"security_updates": ["openssl"]}}]))
    db.commit(); db.close()
    with patch.object(scheduler.updates_payload, "patch_posture", side_effect=RuntimeError("boom")):
        scheduler.patch_digest_job()  # must not raise into the scheduler
