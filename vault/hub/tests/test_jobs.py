"""Activity/Jobs page: `GET /api/v1/jobs` (payloads/jobs.py) and
`POST /api/v1/jobs/{id}/retry` (routers/write.py).

The owner's own ask: a place to see every queue/job Vault is running or
has just run, in plain English, with a stuck job called out and a failed
one explaining itself and offering a safe retry.
"""

import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal

from tests.test_auth import login, seed_user


def _queue(tid: str, *, path: str, body: dict, who: str = "miro", status: str = "queued",
          result: dict | None = None, age_s: int = 0, finished_age_s: int | None = None) -> str:
    """Insert one `Queue` row directly -- the sensor's own drain path
    (`GET /sensor/queue`, `POST /sensor/queue/{id}/result`) is exercised by
    `test_sensor_api.py`; this test is only about how a row, once it exists,
    reads on the Activity page."""
    db = SessionLocal()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        q = m.Queue(tenant_id=tid, who=who, path=path, body=body, status=status, result=result)
        q.ts = now - dt.timedelta(seconds=age_s)
        if finished_age_s is not None:
            q.finished_at = now - dt.timedelta(seconds=finished_age_s)
        db.add(q)
        db.commit()
        return q.id
    finally:
        db.close()


def _rows(client):
    r = client.get("/api/v1/jobs")
    assert r.status_code == 200, r.text
    return r.json()["jobs"]


def test_a_speedtest_reads_in_plain_english_not_the_raw_path(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="done",
          result={"ok": True, "out": "ok", "down": 120.0}, age_s=30, finished_age_s=5)
    rows = _rows(client)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "speedtest"
    assert "/api/" not in row["label"]
    assert row["label"] == "speed test"
    assert row["status"] == "done" and row["ok"] is True


def test_a_fix_names_the_action_and_the_host(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/fix", body={"host": "prod", "action": "cleanup_disk"},
          status="done", result={"ok": True, "out": "freed 2G", "label": "free disk space"})
    row = _rows(client)[0]
    assert row["kind"] == "fix"
    assert row["target"] == "prod"
    assert row["label"] == "free disk space"


def test_newest_first(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/run", body={}, status="done", result={"ok": True}, age_s=600)
    _queue(tid, path="/api/speedtest", body={}, status="done", result={"ok": True}, age_s=10)
    rows = _rows(client)
    assert [r["kind"] for r in rows] == ["speedtest", "scan"]


def test_queued_and_running_are_flagged_distinctly_from_finished(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="queued")
    _queue(tid, path="/api/run", body={}, status="running", age_s=5)
    _queue(tid, path="/api/fix", body={"host": "prod", "action": "reboot"}, status="done",
          result={"ok": True, "out": "", "label": "reboot host"})
    rows = _rows(client)
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["speedtest"]["status"] == "queued"
    # A queued row reports how long it has been waiting. It used to report
    # nothing, which is how a job no sensor ever collected became invisible.
    assert by_kind["speedtest"]["duration_s"] is not None
    assert by_kind["speedtest"]["stuck"] is False, "queued seconds ago is not stuck"
    assert by_kind["scan"]["status"] == "running"
    assert by_kind["scan"]["duration_s"] is not None and by_kind["scan"]["duration_s"] >= 5
    assert by_kind["fix"]["status"] == "done"


def test_a_running_job_far_past_its_normal_time_is_flagged_stuck(client):
    """The live-hub case the spec calls out: an `/api/speedtest` sitting in
    `running` for 40+ minutes must be visibly called out, not shown as
    ordinary progress."""
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="running", age_s=45 * 60)
    row = _rows(client)[0]
    assert row["status"] == "running"
    assert row["stuck"] is True


def test_a_running_job_within_its_normal_time_is_not_stuck(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="running", age_s=20)
    row = _rows(client)[0]
    assert row["stuck"] is False


def test_a_queued_job_waiting_far_too_long_is_stuck(client):
    """This reverses an earlier decision, on purpose.

    `stuck` was originally about a hung *running* job only, on the reasoning
    that the flag would mislead on a merely-waiting row. That reasoning came
    from when the sensor collected work once per scan, every six or seven
    minutes, so a long queue wait was normal.

    The sensor now polls every ten seconds on its own thread. A row still
    queued forty-five minutes later does not mean a slow sensor, it means no
    sensor is listening to this tenant -- and the second verification cycle
    found the consequence: Vault said "I can fix this myself", queued it,
    and the job sat there with the page showing neither an age nor a warning.
    Invisible is worse than misleading.
    """
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="queued", age_s=45 * 60)
    row = _rows(client)[0]
    assert row["stuck"] is True


def test_a_failed_job_explains_why_in_first_person(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/fix", body={"host": "prod", "action": "cleanup_disk"},
          status="failed", result={"ok": False, "out": "no space left on device", "label": "free disk space"})
    row = _rows(client)[0]
    assert row["status"] == "failed"
    assert row["why_failed"]
    assert row["why_failed"].startswith("I ")
    assert "no space left on device" in row["why_failed"]
    assert "prod" in row["why_failed"]


def test_a_failed_tier2_fix_is_retryable_a_tier3_fix_is_not(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/fix", body={"host": "prod", "action": "cleanup_disk"},
          status="failed", result={"ok": False, "out": "boom"})
    _queue(tid, path="/api/fix", body={"host": "prod", "action": "full_upgrade"},
          status="failed", result={"ok": False, "out": "boom"})
    rows = {r["label"]: r for r in _rows(client)}
    assert rows["free disk space"]["retryable"] is True
    assert rows["full apt upgrade (all packages)"]["retryable"] is False


def test_a_failed_speedtest_and_scan_are_retryable(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="failed", result={"ok": False, "out": "no route"})
    _queue(tid, path="/api/run", body={}, status="failed", result={"ok": False, "out": "boom"})
    rows = _rows(client)
    assert all(r["retryable"] for r in rows)


def test_a_finished_job_is_never_retryable(client):
    _, tid = seed_user()
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="done", result={"ok": True, "out": ""})
    row = _rows(client)[0]
    assert row["retryable"] is False


def test_viewer_can_read_the_jobs_page(client):
    _, tid = seed_user(role="viewer")
    login(client)
    _queue(tid, path="/api/speedtest", body={}, status="queued")
    assert client.get("/api/v1/jobs").status_code == 200


# --------------------------------------------------------------------------
# Retry
# --------------------------------------------------------------------------

def test_viewer_cannot_retry(client):
    _, tid = seed_user(role="viewer")
    login(client)
    qid = _queue(tid, path="/api/speedtest", body={}, status="failed", result={"ok": False, "out": "x"})
    r = client.post(f"/api/v1/jobs/{qid}/retry")
    assert r.status_code == 403


def test_operator_can_retry_a_failed_retryable_job(client):
    _, tid = seed_user(role="operator")
    login(client)
    qid = _queue(tid, path="/api/speedtest", body={}, status="failed", result={"ok": False, "out": "x"})
    r = client.post(f"/api/v1/jobs/{qid}/retry")
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert new_id != qid

    db = SessionLocal()
    try:
        rows = db.query(m.Queue).filter_by(tenant_id=tid, path="/api/speedtest").all()
        assert len(rows) == 2
        fresh = next(row for row in rows if row.id == new_id)
        assert fresh.status == "queued"
    finally:
        db.close()

    db = SessionLocal()
    try:
        actions = [a.action for a in db.query(m.Audit).filter_by(tenant_id=tid).all()]
    finally:
        db.close()
    assert "job.retry" in actions


def test_retrying_a_tier3_fix_is_refused(client):
    _, tid = seed_user(role="admin")
    login(client)
    qid = _queue(tid, path="/api/fix", body={"host": "prod", "action": "full_upgrade"},
                status="failed", result={"ok": False, "out": "x"})
    r = client.post(f"/api/v1/jobs/{qid}/retry")
    assert r.status_code == 409
    db = SessionLocal()
    try:
        assert db.query(m.Queue).filter_by(tenant_id=tid, path="/api/fix").count() == 1
    finally:
        db.close()


def test_retrying_a_job_that_is_not_failed_is_refused(client):
    _, tid = seed_user(role="operator")
    login(client)
    qid = _queue(tid, path="/api/speedtest", body={}, status="done", result={"ok": True, "out": ""})
    r = client.post(f"/api/v1/jobs/{qid}/retry")
    assert r.status_code == 409


def test_retrying_someone_elses_tenant_job_404s(client):
    _, tid = seed_user(role="operator")
    _, other_tid = seed_user(username="other", email="other@example.com", tenant_slug="other")
    qid = _queue(other_tid, path="/api/speedtest", body={}, status="failed", result={"ok": False, "out": "x"})
    login(client)
    r = client.post(f"/api/v1/jobs/{qid}/retry")
    assert r.status_code == 404


def test_an_operator_cannot_replay_an_admin_only_fix_by_retrying_it(client):
    """Retry must not be a way around the role the job's own route demands.

    `POST /api/v1/fix` is admin-only. Retry was operator-only and re-queued a
    failed job's exact body, so an operator could replay any admin-queued
    remediation -- cleanup_disk, restart_service, kill_miner -- by clicking
    Retry on it. `is_retryable` only ever asked whether the action was
    disruptive, never who was allowed to run it.

    The UI now deliberately renders actions a viewer or operator cannot run,
    disabled and labelled with the role needed, which makes the server the
    only thing standing between them and the action. It has to hold.
    """
    _, tid = seed_user(role="operator")
    login(client)
    qid = _queue(tid, path="/api/fix", body={"host": "prod", "action": "cleanup_disk"},
                 status="failed", result={"ok": False, "out": "no space left"})

    r = client.post(f"/api/v1/jobs/{qid}/retry")
    assert r.status_code == 403, (
        "an operator replayed an admin-only fix through retry: "
        f"{r.status_code} {r.text}"
    )

    db = SessionLocal()
    try:
        assert db.query(m.Queue).filter_by(tenant_id=tid, path="/api/fix").count() == 1, \
            "the retry queued a second copy of the admin-only fix"
    finally:
        db.close()


def test_an_admin_may_still_retry_that_same_fix(client):
    """The gate is the role the route demands, not a ban on retrying fixes."""
    _, tid = seed_user(role="admin")
    login(client)
    qid = _queue(tid, path="/api/fix", body={"host": "prod", "action": "cleanup_disk"},
                 status="failed", result={"ok": False, "out": "no space left"})

    r = client.post(f"/api/v1/jobs/{qid}/retry")
    assert r.status_code == 200, r.text


def test_a_job_nobody_collects_is_flagged_stuck_and_shows_how_long_it_waited(client):
    """A job waiting forever is the failure this page exists to show.

    The sensor polls the queue every ten seconds, so a row still `queued`
    minutes later does not mean a slow sensor -- it means no sensor is
    listening to this tenant. The stuck check only looked at `running`, and a
    `queued` row reported no age at all, so Vault could say "I can fix this
    myself", queue it, and have nothing happen with nothing anywhere saying so.
    """
    from guardian_hub.payloads import jobs as jobs_payload

    _, tid = seed_user(role="admin")
    login(client)
    qid = _queue(tid, path="/api/fix", body={"host": "prod", "action": "cleanup_disk"},
                 status="queued", age_s=jobs_payload.QUEUED_STUCK_SECONDS + 120)

    rows = client.get("/api/v1/jobs").json()["jobs"]
    row = next(r for r in rows if r["id"] == qid)
    assert row["stuck"] is True, "a job nobody collected was shown as ordinary progress"
    assert row["duration_s"] is not None and row["duration_s"] >= jobs_payload.QUEUED_STUCK_SECONDS, \
        "a waiting job showed no age, so the reader cannot tell it has been waiting"


def test_a_freshly_queued_job_is_not_called_stuck(client):
    _, tid = seed_user(role="admin")
    login(client)
    qid = _queue(tid, path="/api/speedtest", body={}, status="queued")

    rows = client.get("/api/v1/jobs").json()["jobs"]
    row = next(r for r in rows if r["id"] == qid)
    assert row["stuck"] is False, "a job queued seconds ago was called stuck"
