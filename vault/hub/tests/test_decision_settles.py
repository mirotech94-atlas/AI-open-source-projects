"""The decision ledger: one row per repair Vault attempts, settled later.

The last three tests are about WHERE the row is written from, which is the
part of this feature easiest to get wrong in a way nothing notices: a ledger
written from the read path fills itself with decisions nobody made (one row
per open alert per dashboard load), and `remedy.resolve()` runs on every
dashboard load, once per alert. So "queueing records" and "reading records
nothing" are both asserted, not just the first.
"""
import datetime as dt

from guardian_hub import confidence as c, decisions, models as m, remedy
from guardian_hub.db import SessionLocal
from guardian_hub.routers import write as write_routes
from tests.test_seed_helpers import seeded_demo


def _conf():
    return c.Confidence(score=88, verdict="auto", factors=[
        c.Factor(name="host_history", question="q?", answer="yes, 4 times",
                 weight=30, why="done before")])


def _row(tid):
    db = SessionLocal()
    try:
        return db.query(m.DecisionOutcome).filter_by(tenant_id=tid).one()
    finally:
        db.close()


def _rows(tid):
    db = SessionLocal()
    try:
        return db.query(m.DecisionOutcome).filter_by(tenant_id=tid).all()
    finally:
        db.close()


def test_recording_a_decision_stores_each_factor_weight(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        decisions.record(db, db.get(m.Tenant, tid), alert_key="k", host="prod",
                         action="security_updates", conf=_conf(), chosen="auto")
    finally:
        db.close()
    row = _row(tid)
    assert row.features["host_history"] == 30 and row.score == 88
    assert row.outcome == ""


def test_settling_fills_the_outcome_once_and_stamps_it(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        decisions.record(db, tenant, alert_key="k", host="prod",
                         action="security_updates", conf=_conf(), chosen="auto")
        n = decisions.settle(db, tenant, alert_key="k", host="prod",
                             action="security_updates", outcome="held")
    finally:
        db.close()
    assert n == 1
    row = _row(tid)
    assert row.outcome == "held" and row.settled_at is not None


def test_a_settled_decision_is_not_settled_again(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        decisions.record(db, tenant, alert_key="k", host="prod",
                         action="security_updates", conf=_conf(), chosen="auto")
        decisions.settle(db, tenant, alert_key="k", host="prod",
                         action="security_updates", outcome="held")
        again = decisions.settle(db, tenant, alert_key="k", host="prod",
                                 action="security_updates", outcome="recurred")
    finally:
        db.close()
    assert again == 0
    assert _row(tid).outcome == "held"


def test_settling_without_an_alert_key_settles_by_host_and_action(client):
    # `verification.reconcile` knows the host and the action and never the
    # alert key (see that module), so an empty key must mean "any decision
    # about this repair" rather than "a decision whose key is empty".
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        decisions.record(db, tenant, alert_key="sec:secupd:prod", host="prod",
                         action="security_updates", conf=_conf(), chosen="auto")
        n = decisions.settle(db, tenant, host="prod", action="security_updates",
                             outcome="held")
    finally:
        db.close()
    assert n == 1
    assert _row(tid).outcome == "held"


def _open_alert(db, tid, key="sec:secupd:prod", msg="pending security updates on prod"):
    # `first_seen`/`last_seen` are non-nullable strings carrying whatever UTC
    # offset the sensor wrote (see `payloads/history.py`), and `msg` is a
    # non-nullable Text column.
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    db.add(m.Alert(tenant_id=tid, key=key, sev="WARN", msg=msg, status="open",
                   first_seen=stamp, last_seen=stamp))
    db.commit()


def test_queueing_a_repair_records_the_decision(client):
    # The write path, which is the only path that may write here.
    #
    # The alert is inserted FIRST and its key asserted, because the seeded
    # demo fixture carries `"security_updates": []` for every host -- so
    # without it `alerts_for_fix` finds nothing, the row is written with
    # `alert_key=""` and the blind branches of `held`/`undone`, and a test
    # that only checked `features` was non-empty would stay green over a
    # feature that had gone inert.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        _open_alert(db, tid)
    finally:
        db.close()
    r = client.post("/api/v1/fix", json={"host": "prod", "action": "security_updates",
                                         "arg": None})
    assert r.status_code == 200, r.text
    rows = _rows(tid)
    assert len(rows) == 1
    row = rows[0]
    assert row.host == "prod" and row.action == "security_updates"
    assert row.alert_key == "sec:secupd:prod"   # the decision knows its alert
    assert row.features and 0 <= row.score <= 100
    assert row.outcome == ""             # nothing has happened yet
    # `chosen` is the EFFECTIVE tier, not `action in TIER3_FIXES`: the demo
    # has no successful fix on a host called "prod", which `effective_tier`
    # escalates to 3, and reaching `queue_fix` at tier 3 means somebody
    # confirmed.
    assert row.chosen == "approval" and row.verdict == "approval"


def test_one_services_restart_does_not_settle_anothers(client):
    # `restart_service nginx` and `restart_service postgres` on one host are
    # two decisions. `remedy.alerts_for_fix` is careful about exactly this
    # distinction; `settle` has to be too, or verifying one stamps both.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        for svc in ("nginx", "postgres"):
            decisions.record(db, tenant, alert_key=f"svc:prod:{svc}", host="prod",
                             action="restart_service", arg=svc, conf=_conf(),
                             chosen="auto")
        n = decisions.settle(db, tenant, host="prod", action="restart_service",
                             arg="nginx", outcome="held")
        settled = {r.arg: r.outcome for r in
                   db.query(m.DecisionOutcome).filter_by(tenant_id=tid).all()}
    finally:
        db.close()
    assert n == 1
    assert settled == {"nginx": "held", "postgres": ""}


def test_reading_a_remedy_records_nothing(client):
    # `payloads/incidents.py` calls `remedy.resolve()` once per alert on every
    # dashboard load. If resolution recorded, the ledger would grow with page
    # views and hold decisions nobody ever made.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        remedy.resolve_action(db, tenant, "admin", "security_updates", "prod", "",
                              "pending security updates on prod")
    finally:
        db.close()
    assert _rows(tid) == []


def test_a_failed_recording_unwinds_only_its_own_row(client, monkeypatch):
    """Scoring fails open, and the handler touches nothing but what it added.

    This calls `_record_decision` directly, with an unrelated pending row in
    the session, because that state is NOT reachable through `queue_fix`:
    `queue_and_audit` commits before the recording runs, and chat's
    confirmation burn commits on its own. So what this pins is the handler's
    own contract -- it discards its own pending rows and does not unwind a
    session it does not own -- rather than a live caller's exposure.
    """
    tid = seeded_demo(client)

    def boom(*a, **k):
        raise RuntimeError("scoring blew up")

    monkeypatch.setattr(c, "assess", boom)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        db.add(m.Edit(tenant_id=tid, who="chat", path="/api/fix",
                      body={"probe": "the caller's own pending work"}))
        write_routes._record_decision(db, tenant, host="prod",
                                      action="security_updates", arg=None)
        db.commit()                      # the caller commits, later
        kept = db.query(m.Edit).filter_by(tenant_id=tid, who="chat").count()
    finally:
        db.close()
    assert _rows(tid) == []              # no half-scored decision row
    assert kept == 1                     # and the caller's row survived


def test_alerts_for_fix_finds_the_open_alert_this_repair_answers(client):
    # The reverse mapping `queue_fix` needs to know which alert it is about,
    # shared with `routers/sensor._settle_fixed_alerts`.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        _open_alert(db, tid)
        found = remedy.alerts_for_fix(db, tenant, "security_updates", "prod", None)
        assert [a.key for a in found] == ["sec:secupd:prod"]
        # Same alert, a repair on a different host: not this alert's remedy.
        assert remedy.alerts_for_fix(db, tenant, "security_updates", "other", None) == []
    finally:
        db.close()


def test_accepting_a_recommendation_records_the_decision(client):
    """The second funnel, which used to be a hole.

    "Yes, do it" on a recommendation calls `queue_and_audit` directly rather
    than `queue_fix`, because it records a different audit row on purpose
    (`"rec.queue"` against the recommendation's key, not `"fix"` against the
    host). It was therefore queueing real repairs onto customer networks with
    no ledger row -- and the harm was worse than a gap, because `settle`
    matches on `(tenant, host, action, arg)`, so this repair's later evidence
    would settle whatever OTHER decision happened to be open for the same
    identity.

    That is what the alert-key assertion is for: a row exists AND it is this
    repair's row, with the key `remedy.alerts_for_fix` recovered. A test that
    only counted rows would pass over a row written blind -- the near-miss
    this feature has already had twice.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        db.add(m.Recommendation(
            tenant_id=tid, key="rec:cve:clinic-nas", priority="high",
            title="Install pending security updates on clinic-nas",
            action={"host": "clinic-nas", "action": "security_updates"},
            last_seen=dt.datetime.now(dt.timezone.utc).isoformat()))
        db.commit()
    finally:
        db.close()

    r = client.post("/api/v1/recs/rec:cve:clinic-nas", json={"decision": "yes"})
    assert r.status_code == 200, r.text
    assert r.json()["queue_id"] is not None

    rows = _rows(tid)
    assert len(rows) == 1
    row = rows[0]
    assert row.host == "clinic-nas" and row.action == "security_updates"
    # A real open alert in the demo capture, so an empty key here would mean
    # the recovery went blind and `held`/`undone` scored nothing.
    assert row.alert_key == "sec:cve:clinic-nas"
    assert row.features and 0 <= row.score <= 100
    assert row.outcome == ""


def test_accepting_the_same_recommendation_twice_records_one_decision(client):
    # Pressing Yes again hits `queue_and_audit`'s identical-pending-job
    # short-circuit, which queues nothing new -- so there is no second
    # attempt to record. Two rows for one repair would be settled together
    # by one piece of evidence and weight double in anything fitted here.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        db.add(m.Recommendation(
            tenant_id=tid, key="rec:cve:records", priority="high", title="updates",
            action={"host": "records", "action": "security_updates"},
            last_seen=dt.datetime.now(dt.timezone.utc).isoformat()))
        db.commit()
    finally:
        db.close()
    for _ in range(2):
        assert client.post("/api/v1/recs/rec:cve:records",
                           json={"decision": "yes"}).status_code == 200
    assert len(_rows(tid)) == 1
