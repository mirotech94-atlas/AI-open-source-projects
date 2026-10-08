"""Last month's availability figure is still last month's availability figure.

Every uptime number Vault draws is recomputed from `metric` rows at the
moment it is drawn, and `retention` deletes those after 90 days. So the
figure in a report from March is not a record of March — it is a
recomputation whose inputs are vanishing underneath it, and which eventually
resolves to "no checks".

That makes the one question an MSP's client always asks unanswerable: "is
this the same number you showed me last quarter?" (trust plan §2.5M).

The test that matters most here is
`test_the_figure_survives_the_metrics_it_was_computed_from`: it writes the
facts, deletes every metric row they came from, and asserts the number is
unchanged. That is the whole feature, stated as an experiment.
"""
import datetime as dt

from guardian_hub import dailyfacts, maintenance, models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads.assets import MIN_UPTIME_SAMPLES
from tests.test_tenants import make_super

NOON = dt.time(12, 0)


def _tenant(tid):
    db = SessionLocal()
    t = db.get(m.Tenant, tid)
    return t, db


def _metrics(tid, monitor, day, up, down):
    """`up` good and `down` bad checks, spread through one UTC day."""
    base = dt.datetime.fromisoformat(day + "T00:30:00+00:00")
    db = SessionLocal()
    try:
        for i in range(up + down):
            db.add(m.Metric(tenant_id=tid, ts=(base + dt.timedelta(minutes=5 * i)).isoformat(),
                            name="up", entity=monitor, value=1.0 if i < up else 0.0))
        db.commit()
    finally:
        db.close()


YESTERDAY = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).date().isoformat()


def test_a_closed_day_records_what_the_page_would_have_said(client):
    tid = make_super(client)
    _metrics(tid, "shop", YESTERDAY, up=95, down=5)
    t, db = _tenant(tid)
    try:
        assert dailyfacts.close_day(db, t, YESTERDAY, dt.timedelta(0)) == 1
        fact = db.query(m.DailyFact).filter_by(tenant_id=tid, day=YESTERDAY).one()
        assert fact.checks == 100 and fact.up == 95
        assert fact.uptime_pct == 95.0
    finally:
        db.close()


def test_the_figure_survives_the_metrics_it_was_computed_from(client):
    """The whole feature, as an experiment: freeze it, delete every input,
    read it back unchanged. Without this table the same read returns None
    once retention has been round."""
    tid = make_super(client)
    _metrics(tid, "shop", YESTERDAY, up=99, down=1)
    t, db = _tenant(tid)
    try:
        dailyfacts.close_day(db, t, YESTERDAY, dt.timedelta(0))
        before = dailyfacts.uptime_for(db, tid, "shop", days=30)

        deleted = db.query(m.Metric).filter_by(tenant_id=tid, name="up").delete()
        db.commit()
        assert deleted == 100, "the inputs really are gone"

        after = dailyfacts.uptime_for(db, tid, "shop", days=30)
        assert after == before
        assert after["uptime_pct"] == 99.0
    finally:
        db.close()


def test_closing_a_day_twice_updates_rather_than_duplicates(client):
    """The job has to be safe to re-run after a failed night."""
    tid = make_super(client)
    _metrics(tid, "shop", YESTERDAY, up=50, down=0)
    t, db = _tenant(tid)
    try:
        dailyfacts.close_day(db, t, YESTERDAY, dt.timedelta(0))
        dailyfacts.close_day(db, t, YESTERDAY, dt.timedelta(0))
        rows = db.query(m.DailyFact).filter_by(tenant_id=tid, day=YESTERDAY).all()
        assert len(rows) == 1 and rows[0].checks == 50
    finally:
        db.close()


def test_today_is_never_closed_because_it_is_not_over(client):
    """A partial day frozen forever looks authoritative and is wrong."""
    tid = make_super(client)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    _metrics(tid, "shop", today, up=20, down=0)
    t, db = _tenant(tid)
    try:
        dailyfacts.close_due(db, t, dt.timedelta(0))
        assert db.query(m.DailyFact).filter_by(tenant_id=tid, day=today).first() is None
    finally:
        db.close()


def test_a_day_already_closed_is_not_recomputed_from_thinner_data(client):
    """Pruning only ever removes rows, so re-deriving a closed day later
    would produce a lower, wronger number — and destroy the guarantee."""
    tid = make_super(client)
    _metrics(tid, "shop", YESTERDAY, up=100, down=0)
    t, db = _tenant(tid)
    try:
        dailyfacts.close_due(db, t, dt.timedelta(0))
        db.query(m.Metric).filter_by(tenant_id=tid, name="up").delete()
        db.commit()
        dailyfacts.close_due(db, t, dt.timedelta(0))
        fact = db.query(m.DailyFact).filter_by(tenant_id=tid, day=YESTERDAY).one()
        assert fact.checks == 100, "a second pass must not rewrite it from nothing"
    finally:
        db.close()


def test_planned_maintenance_is_excluded_and_counted_separately(client):
    """Same rule as the live page: a window leaves both halves of the
    fraction, and says how much it took."""
    tid = make_super(client)
    _metrics(tid, "shop", YESTERDAY, up=40, down=20)
    t, db = _tenant(tid)
    try:
        # The 20 failures sit at minutes 200..295 of the day.
        start = dt.datetime.fromisoformat(YESTERDAY + "T00:30:00+00:00")
        t.settings = maintenance.record(dict(t.settings or {}),
                                        start + dt.timedelta(minutes=5 * 40 - 1),
                                        start + dt.timedelta(minutes=5 * 60 + 1))
        db.commit()
        dailyfacts.close_day(db, t, YESTERDAY, dt.timedelta(0))
        fact = db.query(m.DailyFact).filter_by(tenant_id=tid, day=YESTERDAY).one()
        assert fact.planned == 20
        assert fact.checks == 40 and fact.up == 40
        assert fact.uptime_pct == 100.0
    finally:
        db.close()


def test_too_few_checks_freeze_no_percentage_rather_than_a_made_up_one(client):
    """Freezing invented precision forever is worse than freezing nothing."""
    tid = make_super(client)
    _metrics(tid, "shop", YESTERDAY, up=MIN_UPTIME_SAMPLES - 2, down=1)
    t, db = _tenant(tid)
    try:
        dailyfacts.close_day(db, t, YESTERDAY, dt.timedelta(0))
        fact = db.query(m.DailyFact).filter_by(tenant_id=tid, day=YESTERDAY).one()
        assert fact.uptime_pct is None
        assert fact.checks == MIN_UPTIME_SAMPLES - 1, "the counts are still recorded"
    finally:
        db.close()


def test_a_day_with_too_few_checks_is_left_out_of_the_aggregate(client):
    """Not counted as perfect. The same rule within a day and across days."""
    tid = make_super(client)
    _metrics(tid, "shop", YESTERDAY, up=3, down=0)
    t, db = _tenant(tid)
    try:
        dailyfacts.close_day(db, t, YESTERDAY, dt.timedelta(0))
        got = dailyfacts.uptime_for(db, tid, "shop", days=30)
        assert got["days"] == 0 and got["uptime_pct"] is None
    finally:
        db.close()


def test_the_day_boundary_follows_the_tenant_not_utc(client):
    """A "daily" figure cut at UTC midnight for an account in New York mixes
    two of the owner's days together (§2.5K)."""
    tid = make_super(client)
    day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).date().isoformat()
    # One check at 02:00 UTC, which is the PREVIOUS evening at UTC-5.
    db = SessionLocal()
    try:
        db.add(m.Metric(tenant_id=tid, ts=day + "T02:00:00+00:00", name="up",
                        entity="shop", value=1.0))
        db.commit()
    finally:
        db.close()
    t, db = _tenant(tid)
    try:
        dailyfacts.close_day(db, t, day, dt.timedelta(hours=-5))
        assert db.query(m.DailyFact).filter_by(tenant_id=tid, day=day).first() is None, (
            "at UTC-5 that sample belongs to the day before"
        )
        prev = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        dailyfacts.close_day(db, t, prev, dt.timedelta(hours=-5))
        assert db.query(m.DailyFact).filter_by(tenant_id=tid, day=prev).one().checks == 1
    finally:
        db.close()


# --------------------------------------------------------------------------
# The report reads the frozen rows, so a re-run reproduces what was sent.
# --------------------------------------------------------------------------

def _shop_report(tid: str, *, frozen: bool) -> str:
    """A tenant whose latest scan lists one site, `shop`, with a week of
    frozen daily facts at 98% -- or none, to show what the report says when
    only a live recomputation is available (nothing: there is no history)."""
    from guardian_hub.reports import builder
    db = SessionLocal()
    try:
        t = db.get(m.Tenant, tid)
        t.config = {**(t.config or {}), "sites": [{"name": "shop", "url": "https://shop.example"}]}
        db.add(m.Site(tenant_id=tid, name="shop", url="https://shop.example", last={"ok": True}))
        # The availability section rows are the sites the LATEST SCAN lists
        # (`builder.last_of` -> `Scan.raw["sites"]`), so a site has to be in
        # a scan to have a row at all. The earlier version of this test
        # seeded no scan, rendered no row, and passed anyway: the "98" it
        # found was `max-width:980px` in the report's CSS. It went red the
        # day the CSS was restyled, having never once checked the figure.
        db.add(m.Scan(tenant_id=tid, ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      raw={"sites": [{"name": "shop", "url": "https://shop.example", "ok": True}]}))
        if frozen:
            for back in range(1, 8):
                day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=back)).date().isoformat()
                db.add(m.DailyFact(tenant_id=tid, day=day, monitor="shop",
                                   checks=100, up=98, planned=0, uptime_pct=98.0))
        db.commit()
        return builder.build_report(db, db.get(m.Tenant, tid), days=7)
    finally:
        db.close()


def _shop_row(html: str) -> str:
    """The availability table row for `shop`, and nothing outside it."""
    import re
    m_ = re.search(r"<tr>(?:(?!</tr>).)*?<b>shop</b>(?:(?!</tr>).)*?</tr>", html, re.S)
    assert m_, "the availability section must carry a row for the site the scan lists"
    return m_.group(0)


def test_a_report_quotes_the_frozen_figure_not_a_fresh_recomputation(client):
    """A report is the one artefact that gets filed and quoted back months
    later, so it is the worst place for a figure that drifts."""
    import re
    tid = make_super(client)
    row = _shop_row(_shop_report(tid, frozen=True))
    assert re.search(r"\b98(\.0)?%", row), f"the frozen 98% should reach the site's row: {row!r}"


def test_without_frozen_facts_the_row_says_it_cannot_answer(client):
    """The other half of the proof: the same report with no frozen rows and
    no history has nothing to recompute from, so the figure is a dash, not
    a number. If this ever shows 98 the test above is measuring something
    other than the frozen facts."""
    tid = make_super(client)
    row = _shop_row(_shop_report(tid, frozen=False))
    assert "98" not in row
    assert "—%" in row


def test_the_report_falls_back_when_no_day_in_the_window_was_closed(client):
    """A window the table cannot cover — one that is only today, or predates
    the table — must still produce a report rather than a blank."""
    from guardian_hub.reports import builder
    tid = make_super(client)
    db = SessionLocal()
    try:
        t = db.get(m.Tenant, tid)
        html = builder.build_report(db, t, days=7)
    finally:
        db.close()
    assert html and "<section" in html


def test_a_windowed_read_reports_nothing_rather_than_zero_when_uncovered(client):
    """`days == 0` is the caller's signal to fall back. A zero would be a
    claim that the sites were down all week."""
    tid = make_super(client)
    db = SessionLocal()
    try:
        got = dailyfacts.uptime_between(db, tid, "shop", "2020-01-01", "2020-01-07")
        assert got["days"] == 0 and got["uptime_pct"] is None
    finally:
        db.close()
