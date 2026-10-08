import datetime as dt
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m, retention, scheduler

def _frozen() -> dt.datetime:
    """The clock these tests pin `retention._now` to.

    It has to be ANCHORED TO TODAY rather than written down as a date, and the
    reason is the mistake this file already made once.

    It was `2026-09-09 03:30Z`, a literal, correct on the day it was written.
    Every prune assertion here is self-consistent -- rows are aged relative to
    this value and the expected days are computed from it -- so those kept
    passing as the real date moved. But `test_a_thirty_day_dns_view_reads_the
    _rollup` asks the APP for `?hours=720`, and the app does not know about
    this clock: it measures thirty days back from the real now. So the gap
    between the literal and today was silently added to the age of every
    rolled-up row. Rows written twenty days before a frozen clock that is
    itself ten days stale are thirty days old to the query, and on
    2026-09-19 they fell out of the window and the test began failing
    permanently -- eleven days after anyone touched it.

    CLAUDE.md's rule is "never assert wall-clock", and its worked example is
    `tests/scanfixture`, whose shift onto the current time used to be computed
    at import and went stale within a run. This is the same fault with a
    longer fuse: stale between runs rather than during one. A function, called
    per use, cannot drift.
    """
    return dt.datetime.now(dt.timezone.utc).replace(hour=3, minute=30, second=0, microsecond=0)


FROZEN = _frozen()


def _old(db, tenant_id, days, n=3):
    ts = (FROZEN - dt.timedelta(days=days)).isoformat(timespec="seconds")
    for i in range(n):
        db.add(m.Metric(tenant_id=tenant_id, ts=ts, name="score", entity="", value=80))
        db.add(m.Event(tenant_id=tenant_id, ts=ts, type="join", entity=f"aa:bb:{i:02d}", payload={}))
        db.add(m.PresenceSample(tenant_id=tenant_id, ts=ts, mac=f"aa:bb:{i:02d}"))
        db.add(m.DnsQuery(tenant_id=tenant_id, ts=ts, client="192.168.1.9", name=f"d{i}.example",
                          qtype="A", blocked=bool(i % 2), list="stevenblack", ms=1))
        db.add(m.Audit(tenant_id=tenant_id, ts=FROZEN - dt.timedelta(days=days), user="x",
                       action="device.update", target="aa:bb"))
    db.commit()
    return ts


def test_prune_respects_every_window_and_rolls_dns_up_first(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal()
    _old(db, tid, days=200)          # outside retention_days and audit_days
    _old(db, tid, days=30)           # inside retention_days, outside dns_raw_days
    counts = retention.prune_tenant(db, db.get(m.Tenant, tid))
    assert counts["metric"] == 3 and counts["event"] == 3 and counts["presence_sample"] == 3
    assert counts["audit"] == 0                      # 200 d < audit_days 365
    assert counts["dns_query"] == 6 and counts["rolled_up"] > 0
    rolled = db.query(m.DnsRollup).filter_by(tenant_id=tid).all()
    assert rolled and sum(r.queries for r in rolled) == 6
    assert {r.day for r in rolled} == {(FROZEN - dt.timedelta(days=d)).date().isoformat() for d in (200, 30)}
    # rows inside every window survive
    assert db.query(m.Metric).filter_by(tenant_id=tid).count() > 1000


def test_dry_run_changes_nothing(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); _old(db, tid, days=200)
    before = db.query(m.Metric).filter_by(tenant_id=tid).count()
    counts = retention.prune_tenant(db, db.get(m.Tenant, tid), dry_run=True)
    assert counts["metric"] == 3
    assert db.query(m.Metric).filter_by(tenant_id=tid).count() == before
    assert db.query(m.DnsRollup).count() == 0


def test_reports_older_than_retention_lose_row_and_file(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal()
    card = client.post("/api/v1/reports/build", json={"days": 7, "preset": "executive",
                                                      "sections": [], "title": "Old"}).json()
    row = db.get(m.Report, card["id"]); row.created = FROZEN - dt.timedelta(days=200); db.commit()
    import pathlib; path = pathlib.Path(row.path); assert path.exists()
    assert retention.prune_tenant(db, db.get(m.Tenant, tid))["report"] == 1
    assert db.get(m.Report, card["id"]) is None and not path.exists()


def test_rollup_is_idempotent_and_capped(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); ts = _old(db, tid, days=30, n=250)
    day = ts[:10]
    first = retention.rollup_dns(db, db.get(m.Tenant, tid), before_day=FROZEN.date().isoformat())
    second = retention.rollup_dns(db, db.get(m.Tenant, tid), before_day=FROZEN.date().isoformat())
    assert first == second
    rows = db.query(m.DnsRollup).filter_by(tenant_id=tid, day=day).all()
    assert len(rows) <= 201 and any(r.name == "(other)" for r in rows)


def test_a_thirty_day_dns_view_reads_the_rollup(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); _old(db, tid, days=20)
    retention.prune_tenant(db, db.get(m.Tenant, tid))
    body = client.get("/api/v1/dns?hours=720").json()
    assert body["status"]["queries_24h"] >= 0
    assert any(t["name"].endswith(".example") for t in body["top"]), "rollup rows must feed the top list"


def test_dns_rollup_days_below_dns_raw_days_is_clamped(client, monkeypatch):
    # policy() must never report a rollup window that lets a freshly
    # rolled-up day be deleted in the same run -- a misconfigured
    # dns_rollup_days=3 with dns_raw_days=7 is floored up to 37 (7 + the
    # 30-day margin), not left at 3, and not just clamped to 7: at
    # equality the day rolled up in step 1 sits exactly at the rollup
    # cutoff too and step 3 would delete it in the same prune_tenant run.
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    tenant.settings = {**(tenant.settings or {}), "dns_rollup_days": 3, "dns_raw_days": 7}
    db.commit()
    pol = retention.policy(db.get(m.Tenant, tid))
    assert pol["dns_rollup_days"] == 37 and pol["dns_raw_days"] == 7

    # And the freshly-rolled day must actually survive prune_tenant, not
    # just pass the policy() arithmetic check above.
    _old(db, tid, days=8)   # older than dns_raw_days (7) -> gets rolled up this run
    counts = retention.prune_tenant(db, db.get(m.Tenant, tid))
    assert counts["rolled_up"] > 0
    rolled = db.query(m.DnsRollup).filter_by(tenant_id=tid).all()
    assert rolled and sum(r.queries for r in rolled) == 3


def test_negative_dns_rollup_days_falls_back_and_rollup_rows_survive(client, monkeypatch):
    # Pre-fix, int(-5) or None on dns_rollup_days produced a cutoff that
    # deleted every DnsRollup row unconditionally (a negative timedelta
    # puts the cutoff in the future, or None/garbage was never guarded).
    # Post-fix it falls back to the 365-day default, so rollup rows for
    # data just past dns_raw_days survive the same prune_tenant run.
    tid = seeded_demo(client)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    tenant.settings = {**(tenant.settings or {}), "dns_rollup_days": -5}
    db.commit()
    pol = retention.policy(db.get(m.Tenant, tid))
    assert pol["dns_rollup_days"] == 365
    _old(db, tid, days=8)   # older than the default dns_raw_days (7) -> gets rolled up
    counts = retention.prune_tenant(db, db.get(m.Tenant, tid))
    assert counts["rolled_up"] > 0
    rolled = db.query(m.DnsRollup).filter_by(tenant_id=tid).all()
    assert rolled and sum(r.queries for r in rolled) == 3


def test_policy_falls_back_to_defaults_on_garbage_values(client, monkeypatch):
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    tenant.settings = {
        "retention_days": "abc", "dns_raw_days": -5, "dns_rollup_days": None, "audit_days": 0,
    }
    db.commit()
    pol = retention.policy(db.get(m.Tenant, tid))
    assert pol == {
        "retention_days": 90, "dns_raw_days": 7, "dns_rollup_days": 365,
        "flow_raw_days": 7, "flow_rollup_days": 365, "audit_days": 365,
    }


def test_the_job_runs_daily_and_is_safe_to_repeat(client, monkeypatch):
    tid = seeded_demo(client)
    monkeypatch.setattr(scheduler, "_now", lambda: FROZEN)
    monkeypatch.setattr(retention, "_now", lambda: FROZEN)
    db = SessionLocal(); _old(db, tid, days=200)
    scheduler.retention_job(); scheduler.retention_job()
    assert db.query(m.Metric).filter(m.Metric.ts < "2026-06-01").count() == 0
    job = next(j for j in scheduler.jobs() if j.id == "retention")
    assert (job.trigger.fields_map["hour"].expressions[0].first == 3
            and job.trigger.fields_map["minute"].expressions[0].first == 30)
