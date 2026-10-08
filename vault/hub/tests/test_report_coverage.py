"""A report that hides a sensor outage is the one that loses the account.

The MSP spec is blunt about this and it is the only place in eighteen pages
it says a single incident would cost you every customer who noticed:
presenting 99.9% uptime for a month the sensor was down. The figure is not
even wrong by the arithmetic that produced it — nothing was observed to be
down — which is exactly why it has to be stated rather than computed
around.

So page one carries coverage in the same weight as the other numbers:
"Coverage: 27 of 30 days", and the dates of the gap when there is one.

`Scan` rows are the source, not `daily_fact`. Scans are never pruned
(CLAUDE.md), so a report about last March can still say honestly whether
Vault was watching in March; the facts table only began this month and
would report a confident, false 0-of-30 for any period before it existed.
Choosing the older, coarser source is the honest call here.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.reports import builder
from tests.test_tenants import make_super


def _scan_on(tid, day, sensor_id=None):
    db = SessionLocal()
    try:
        db.add(m.Scan(tenant_id=tid, ts=day.isoformat() + "T12:00:00+00:00",
                      raw={"ts": day.isoformat() + "T12:00:00+00:00"}, health={}))
        db.commit()
    finally:
        db.close()


def _cov(tid, days=30):
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        return builder.coverage_days(db, tenant, days=days, offset=dt.timedelta(0))
    finally:
        db.close()


def _fill(tid, days, skip=()):
    today = dt.datetime.now(dt.timezone.utc).date()
    for back in range(1, days + 1):
        day = today - dt.timedelta(days=back)
        if day.isoformat() in skip:
            continue
        _scan_on(tid, day)


def test_an_unbroken_month_reports_full_coverage(client):
    tid = make_super(client)
    _fill(tid, 30)
    got = _cov(tid)
    assert got["covered"] == got["total"] == 30
    assert got["gaps"] == []


def test_a_sensor_outage_is_counted_and_dated(client):
    """The number and the dates, because "27 of 30" without saying which
    three invites the reader to assume the harmless three."""
    today = dt.datetime.now(dt.timezone.utc).date()
    missing = [(today - dt.timedelta(days=n)).isoformat() for n in (10, 11, 12)]
    tid = make_super(client)
    _fill(tid, 30, skip=set(missing))
    got = _cov(tid)
    assert got["covered"] == 27 and got["total"] == 30
    assert len(got["gaps"]) == 1, "three consecutive days are one outage, not three"
    start, end = got["gaps"][0]
    assert start == min(missing) and end == max(missing)


def test_two_separate_outages_stay_separate(client):
    today = dt.datetime.now(dt.timezone.utc).date()
    missing = {(today - dt.timedelta(days=n)).isoformat() for n in (5, 20, 21)}
    tid = make_super(client)
    _fill(tid, 30, skip=missing)
    got = _cov(tid)
    assert got["covered"] == 27
    assert len(got["gaps"]) == 2


def test_a_tenant_that_never_reported_is_zero_of_thirty_not_an_error(client):
    """The worst case has to render. A brand-new account asking for a
    30-day report should be told it has no coverage, not shown a blank."""
    tid = make_super(client)
    got = _cov(tid)
    assert got["covered"] == 0 and got["total"] == 30


def test_page_one_states_coverage_even_when_it_is_complete(client):
    """Not only on the bad month. A coverage line that appears only when
    something is wrong teaches readers that its absence means nothing was
    checked — and it makes the bad month look like a different document."""
    tid = make_super(client)
    _fill(tid, 30)
    db = SessionLocal()
    try:
        html = builder.build_report(db, db.get(m.Tenant, tid), days=30)
    finally:
        db.close()
    assert "Coverage" in html
    assert "30 of 30 days" in html


def test_page_one_names_the_days_the_sensor_was_missing(client):
    today = dt.datetime.now(dt.timezone.utc).date()
    missing = {(today - dt.timedelta(days=n)).isoformat() for n in (10, 11, 12)}
    tid = make_super(client)
    _fill(tid, 30, skip=missing)
    db = SessionLocal()
    try:
        html = builder.build_report(db, db.get(m.Tenant, tid), days=30)
    finally:
        db.close()
    assert "27 of 30 days" in html
    assert "no sensor data" in html.lower()
