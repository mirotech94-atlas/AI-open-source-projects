from guardian_hub import selftest
from guardian_hub.config import settings
from guardian_hub.db import Base, SessionLocal, engine
from guardian_hub import models as m


def test_selftest_health_ok_and_no_tenants_fails(client, capsys, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert "ok  health" in out
    assert rc == 1


def test_selftest_me_check_prints_ok(client, capsys, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "ok  me" in out


def _seed_tenant_directly() -> None:
    # Insert a Tenant row directly (no login flow) so the shared `client`'s
    # cookie jar stays empty -- selftest's "me" check must keep seeing 401.
    db = SessionLocal()
    db.add(m.Tenant(slug="home", name="Miro Home"))
    db.commit()


def test_selftest_returns_0_after_seeding_tenant(client, capsys, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    _seed_tenant_directly()
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ok  tenants" in out
    # no active sensors were enrolled -> scan freshness is a skip, not a FAIL
    assert "skip scan_freshness" in out or "scan_freshness" not in out
    assert "FAIL" not in out


def test_selftest_reports_dir_writable(client, capsys, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "ok  reports_writable" in out


def test_selftest_ntfy_skipped_when_no_env(client, capsys, tmp_path, monkeypatch):
    settings.data_dir = tmp_path / "gdata"
    monkeypatch.delenv("GUARDIAN_NTFY_TEST_TOPIC", raising=False)
    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "skip ntfy" in out


def _seed_tenant_with_sensor_and_scan(ts: str) -> None:
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="home").first()
    if not t:
        t = m.Tenant(slug="home", name="Miro Home")
        db.add(t)
        db.commit()
    db.add(m.Sensor(tenant_id=t.id, status="active"))
    db.add(m.Scan(tenant_id=t.id, ts=ts, raw={}))
    db.commit()


def test_selftest_scan_freshness_ok_for_fresh_scan(client, capsys, tmp_path):
    import datetime as dt

    settings.data_dir = tmp_path / "gdata"
    fresh_ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _seed_tenant_with_sensor_and_scan(fresh_ts)
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert "ok  scan_freshness:home" in out
    # A single bare scan (no synthesized history) has nothing for the
    # Overview tab to draw -- "dashboard" correctly FAILs on it, which is
    # exactly what that check is for; this test is only about
    # scan_freshness itself, not the overall exit code.
    assert "FAIL scan_freshness" not in out
    assert rc == 1, out
    assert "FAIL dashboard:home" in out


def test_selftest_scan_freshness_skips_demo_tenant_with_stale_scan(client, capsys, tmp_path):
    import datetime as dt

    settings.data_dir = tmp_path / "gdata"
    stale_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=6)).isoformat(timespec="seconds")
    db = SessionLocal()
    t = m.Tenant(slug="demo", name="Guardian Demo", settings={"demo_code": "424242"})
    db.add(t)
    db.commit()
    db.add(m.Sensor(tenant_id=t.id, status="active"))
    db.add(m.Scan(tenant_id=t.id, ts=stale_ts, raw={}))
    db.commit()

    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "skip scan_freshness:demo: demo tenant" in out
    assert "FAIL scan_freshness" not in out


def test_selftest_scan_freshness_fails_for_stale_scan(client, capsys, tmp_path):
    import datetime as dt

    settings.data_dir = tmp_path / "gdata"
    stale_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    _seed_tenant_with_sensor_and_scan(stale_ts)
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "FAIL scan_freshness:home" in out


def test_selftest_dashboard_skipped_without_active_sensor(client, capsys, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    _seed_tenant_directly()
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "skip dashboard: no active sensors" in out


def test_selftest_dashboard_ok_for_seeded_demo_tenant(client, capsys, tmp_path):
    # The seed is ANCHORED to now, which is what makes this test about the
    # dashboard check rather than about what day it is.
    #
    # The comment that used to sit here claimed "seed_demo's history is
    # synthesized relative to when it's seeded, so the Overview payload is
    # always populated regardless of when this test runs". That is only true
    # when an anchor is passed. Without one, `seed_demo` derives every
    # synthesized row from the fixture's own `last["ts"]` -- which its
    # docstring calls "fixed, ever-aging" -- so the demo's newest event is
    # pinned to the day the capture was taken and gets one day older every
    # day. `overview.timeline` admits nothing older than 24 h, so the moment
    # real time moved a day past the capture the timeline went empty and this
    # test failed permanently, reporting "FAIL dashboard:demo: empty timeline"
    # against a product that was fine.
    #
    # Production never had the bug, and that is the tell: `scheduler.
    # demo_refresh_job` re-seeds through `demo_refresh`, which passes
    # `anchor=now` for exactly this reason. Passing it here makes the test do
    # what the live demo tenant does.
    #
    # Its *newest* scan can still be older than SCAN_MAX_AGE_MINUTES by the
    # time a slow run reaches this assertion -- that is `scan_freshness`'s
    # concern, not `dashboard`'s, so this only asserts on the latter.
    import datetime as dt

    from guardian_hub import seed

    settings.data_dir = tmp_path / "gdata"
    db = SessionLocal()
    seed.seed_demo(db, anchor=dt.datetime.now(dt.timezone.utc))
    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "ok  dashboard:demo" in out
    assert "FAIL dashboard" not in out


def test_selftest_dashboard_fails_when_overview_payload_is_empty(client, capsys, tmp_path, monkeypatch):
    # An active sensor with none of the history seed_demo would normally
    # synthesize (strip/timeline/score_trend all empty) must FAIL, not pass
    # silently -- the whole point of this check is to catch a live Overview
    # tab that renders with nothing to draw.
    import datetime as dt

    settings.data_dir = tmp_path / "gdata"
    fresh_ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _seed_tenant_with_sensor_and_scan(fresh_ts)
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "FAIL dashboard:home" in out


def test_selftest_dns_and_security_skipped_without_active_sensor(client, capsys, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    _seed_tenant_directly()
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "skip dns: no active sensors" in out
    assert "skip security: no active sensors" in out


def test_selftest_dns_and_security_ok_for_seeded_demo_tenant(client, capsys, tmp_path):
    # seed_demo's synthesized latest scan carries a `dns_filter` block
    # (running=true, blocked_24h>=0) and enough sensor state for
    # payloads.security.build to produce at least one scorecard.
    from guardian_hub import seed

    settings.data_dir = tmp_path / "gdata"
    db = SessionLocal()
    seed.seed_demo(db)
    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "ok  dns:demo" in out
    assert "ok  security:demo" in out
    assert "FAIL dns" not in out
    assert "FAIL security" not in out


def test_selftest_dns_fails_when_scan_has_no_dns_filter_block(client, capsys, tmp_path):
    import datetime as dt

    settings.data_dir = tmp_path / "gdata"
    fresh_ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _seed_tenant_with_sensor_and_scan(fresh_ts)
    rc = selftest.run(client=client)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "FAIL dns:home" in out


def test_selftest_no_tables_returns_1_without_raising(client, capsys, tmp_path):
    # An unmigrated/empty DB must never make run() raise: db_migrated fails,
    # and the checks that depend on it (tenants, scan_freshness) are skipped
    # rather than blowing up on a query against a table that doesn't exist.
    settings.data_dir = tmp_path / "gdata"
    Base.metadata.drop_all(engine)
    try:
        rc = selftest.run(client=client)
    finally:
        Base.metadata.create_all(engine)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "FAIL db_migrated" in out
    assert "skip tenants: db not migrated" in out
    assert "skip scan_freshness: db not migrated" in out
    assert "skip dns: db not migrated" in out
    assert "skip security: db not migrated" in out
    # still no traceback / raised exception reached the test
    assert "Traceback" not in out


# --- the dns check against an idle resolver -------------------------------


def _seed_with_dns(dns_filter: dict):
    """A tenant with an active sensor whose latest scan carries `dns_filter`."""
    import datetime as dt
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m

    ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db = SessionLocal()
    t = db.query(m.Tenant).filter(m.Tenant.slug == "home").first()
    if not t:
        t = m.Tenant(slug="home", name="Miro Home")
        db.add(t)
        db.commit()
    db.add(m.Sensor(tenant_id=t.id, status="active"))
    db.add(m.Scan(tenant_id=t.id, ts=ts, raw={"dns_filter": dns_filter}))
    db.commit()


def test_dns_check_passes_for_a_resolver_that_is_up_but_has_answered_nothing(client, capsys, tmp_path):
    """The nightly false alarm this check used to raise.

    A sensor older than the fix that seeds the counters sends no `blocked_24h`
    at all between midnight and the day's first lookup, because the query log
    is per-day and nothing has created it yet. Sensors update on their own
    schedule, so the hub cannot assume the new shape; an absent counter next
    to a status block reporting zero queries is an idle resolver, not a fault.
    """
    settings.data_dir = tmp_path / "gdata"
    _seed_with_dns({"enabled": True, "running": True, "resolver_ip": "192.168.1.9",
                    "resolver_port": 53, "status": {"uptime_s": 52641, "queries": 0, "blocked": 0}})
    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "FAIL dns:home" not in out, out
    assert "dns:home" in out


def test_dns_check_still_fails_when_queries_were_served_but_not_counted(client, capsys, tmp_path):
    """The case the check exists for, which must survive the fix above.

    The resolver says it answered 4,000 queries and the collector reported no
    24h totals for them. That is a real inconsistency -- the DNS tab would
    show a confident zero -- and it has to stay a failure.
    """
    settings.data_dir = tmp_path / "gdata"
    _seed_with_dns({"enabled": True, "running": True, "resolver_ip": "192.168.1.9",
                    "resolver_port": 53, "status": {"uptime_s": 52641, "queries": 4000, "blocked": 12}})
    selftest.run(client=client)
    out = capsys.readouterr().out
    # And for the right reason -- not the old "bad blocked_24h None", which
    # fired on the idle case too and so said nothing.
    assert "FAIL dns:home: resolver answered 4000 queries but sent no 24h totals" in out, out


def test_dns_check_reads_the_counter_when_the_sensor_sends_one(client, capsys, tmp_path):
    settings.data_dir = tmp_path / "gdata"
    _seed_with_dns({"enabled": True, "running": True, "resolver_ip": "192.168.1.9",
                    "resolver_port": 53, "queries_24h": 1200, "blocked_24h": 37,
                    "status": {"uptime_s": 52641, "queries": 1200, "blocked": 37}})
    selftest.run(client=client)
    out = capsys.readouterr().out
    assert "ok  dns:home" in out, out
