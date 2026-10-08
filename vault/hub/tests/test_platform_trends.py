"""`GET /api/v1/platform/trends` -- the Ledger's per-customer alert sparkline.

Two properties matter here and neither is about the JSON's shape.

It must not scale with the number of customers. The ledger's whole purpose is
to stay readable as the business grows, and `payloads/platform`'s docstring is
explicit that it is grouped queries rather than a loop over tenants for exactly
that reason. A sparkline column is the most natural place for someone to later
write "just fetch the history for this row", so the statement count is pinned.

And it must count the same alerts the customer's own History tab counts. The
owner opens a customer's page FROM this row; a sparkline that disagreed with
the chart it links to would be found by the customer, not by us.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import history, platform as platform_payload
from tests.querycount import queries_for
from tests.test_auth import login, seed_user


def _owner(n_tenants: int = 1):
    uid, tid = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal()
    db.get(m.User, uid).is_superadmin = True
    extra = []
    for i in range(n_tenants):
        t = m.Tenant(slug=f"cust{i}", name=f"Customer {i}")
        db.add(t); db.flush(); extra.append(t.id)
    db.commit(); db.close()
    return tid, extra


def _alerts(tid: str, spec: list[tuple[int, int]], sev: str = "CRIT"):
    """`n` alerts `d` days ago, for each (d, n)."""
    db = SessionLocal()
    now = dt.datetime.now(dt.timezone.utc)
    for days_ago, n in spec:
        for i in range(n):
            when = (now - dt.timedelta(days=days_ago, hours=1)).isoformat()
            db.add(m.Alert(tenant_id=tid, key=f"k{days_ago}-{i}-{sev}", sev=sev,
                           msg="m", first_seen=when, last_seen=when))
    db.commit(); db.close()


def test_a_tenant_admin_cannot_read_every_customers_trend(client):
    seed_user(role="admin")
    login(client)
    assert client.get("/api/v1/platform/trends").status_code == 403


def test_the_cost_does_not_grow_with_the_number_of_customers(client):
    """The guard the module docstring asks for.

    It measures the SAME request at two different customer counts and demands
    the number be identical, rather than asserting some ceiling. A ceiling has
    to be guessed, and a guess that is generous enough not to be brittle is
    generous enough to sit above a real N+1 on a small fixture -- which is
    precisely how the wall-clock version of these guards failed: CLAUDE.md
    records that with one query per device injected (20 statements to 55) the
    old `< 2.0s` assertion still passed, in 0.195s.

    Counting, and comparing two cardinalities, cannot fail that way. Measured
    at the time of writing: 3 statements, whether for 3 customers or 30.
    """
    tid, extra = _owner(n_tenants=2)
    login(client)
    for t in [tid, *extra]:
        _alerts(t, [(3, 2), (1, 1)])
    few = queries_for(client, "/api/v1/platform/trends")

    db = SessionLocal()
    for i in range(20):
        db.add(m.Tenant(slug=f"more{i}", name=f"More {i}"))
    db.commit()
    more_ids = [t.id for t in db.query(m.Tenant).filter(m.Tenant.slug.like("more%")).all()]
    db.close()
    for t in more_ids:
        _alerts(t, [(2, 1)])
    many = queries_for(client, "/api/v1/platform/trends")

    assert few == many, (
        f"{few} statements for 3 customers but {many} for 23 — "
        "the trend column has acquired a query per tenant"
    )


def test_the_sparkline_counts_the_same_alerts_as_the_customers_own_chart(client):
    """The anti-drift guard.

    Both go through `history.alert_first_sightings`, so this asserts the
    property that function exists to provide: the owner's row and the
    customer's chart never disagree about WHICH alerts are in the window.
    """
    tid, _ = _owner()
    login(client)
    _alerts(tid, [(10, 3), (4, 2), (1, 1)])

    db = SessionLocal()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    theirs = history.alert_first_sightings(db, cutoff, tenant_id=tid)
    ours = platform_payload.build_trends(db, days=30)
    db.close()

    mine = [t for t in ours.trends if t.tenant_id == tid]
    assert mine, "no trend built for a tenant with alerts"
    assert mine[0].total == len(theirs) == 6


def test_every_series_spans_the_same_days_so_the_column_reads_down(client):
    """Two sparklines over different windows in one column invite a comparison
    that is not being made."""
    tid, extra = _owner(n_tenants=2)
    login(client)
    _alerts(tid, [(20, 1)])
    _alerts(extra[0], [(2, 5)])

    db = SessionLocal()
    out = platform_payload.build_trends(db, days=30)
    db.close()
    assert out.days == 30
    assert {len(t.points) for t in out.trends} == {30}


def test_a_quiet_day_is_a_zero_rather_than_a_missing_point(client):
    """An alert table with nothing in it for a Tuesday means a quiet Tuesday.
    Dropping the point draws a line from Monday to Wednesday and hides it."""
    tid, _ = _owner()
    login(client)
    _alerts(tid, [(5, 4)])

    db = SessionLocal()
    out = platform_payload.build_trends(db, days=30)
    db.close()
    pts = out.trends[0].points
    assert sum(pts) == 4
    assert pts.count(0.0) == 29, "the other 29 days must be present as zeroes"


def test_a_customer_with_no_alerts_is_absent_rather_than_a_flat_line(client):
    """Drawing a flat line at zero says "we measured and it was nothing";
    drawing nothing says "there is nothing to draw". Only the second is true
    for a customer whose sensor has never reported."""
    tid, extra = _owner(n_tenants=1)
    login(client)
    _alerts(tid, [(2, 1)])

    db = SessionLocal()
    out = platform_payload.build_trends(db, days=30)
    db.close()
    assert [t.tenant_id for t in out.trends] == [tid]
    assert extra[0] not in [t.tenant_id for t in out.trends]


def test_the_window_is_clamped_like_the_history_tabs(client):
    """Same ceiling as `history`, so "30 days" means the same span on the
    owner's column as on the customer's chart."""
    _owner(); login(client)
    db = SessionLocal()
    assert platform_payload.build_trends(db, days=99999).days == history.MAX_DAYS
    assert platform_payload.build_trends(db, days=0).days == 1
    db.close()
