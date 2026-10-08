"""`GET /api/v1/platform/tenants/{tid}/trends` -- the charts the owner shows a
customer.

The point of this endpoint is that it is NOT a second calculation. The owner
opens this page while sitting with the customer, so the line being pointed at
has to be the line the customer sees when they log in and open History. Two
implementations of "new alerts over 30 days" drift, and the screen where they
drifted would be the worst possible one to discover it on.

So the tests that matter here are about the arithmetic being shared and the
sentences being honest, not about the shape of the JSON.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import account, history
from tests.test_auth import login, seed_user


def _owner():
    uid, tid = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal()
    db.get(m.User, uid).is_superadmin = True
    db.commit(); db.close()
    return uid, tid


def _alerts(tid: str, when_counts: list[tuple[int, int]], sev: str = "CRIT"):
    """Add `n` alerts `h` hours ago, for each (h, n)."""
    db = SessionLocal()
    now = dt.datetime.now(dt.timezone.utc)
    for hours_ago, n in when_counts:
        for i in range(n):
            db.add(m.Alert(
                tenant_id=tid, key=f"k-{hours_ago}-{i}-{sev}", sev=sev, msg="m",
                first_seen=(now - dt.timedelta(hours=hours_ago)).isoformat(),
                last_seen=(now - dt.timedelta(hours=hours_ago)).isoformat(),
            ))
    db.commit(); db.close()


def test_a_tenant_admin_cannot_read_another_customers_trends(client):
    seed_user(role="admin")
    login(client)
    r = client.get("/api/v1/platform/tenants/whatever/trends")
    assert r.status_code == 403, r.text


def test_an_unknown_customer_is_404_not_an_empty_chart(client):
    """An id that does not exist and a customer with no data are different
    answers, and drawing an empty axis for the first is a lie."""
    _owner(); login(client)
    assert client.get("/api/v1/platform/tenants/nope/trends").status_code == 404


def test_the_series_are_the_same_objects_the_customer_sees(client):
    """The guard against a second implementation.

    `history.build_charts` is what draws the customer's own History tab. This
    asserts the console's payload carries those exact series rather than
    anything recomputed -- if someone later writes a bespoke query here, the
    two stop matching and this fails.
    """
    _, tid = _owner()
    login(client)
    _alerts(tid, [(200, 3), (20, 5)])

    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    _d, _h, cutoff, bucket = history.window(days=30)
    theirs = {c.key: c for c in history.build_charts(db, tenant.id, cutoff, None, bucket)}
    ours = account.build_trends(db, tenant, days=30)
    db.close()

    assert ours.trends, "no trend built from alerts that exist"
    for t in ours.trends:
        assert t.chart.key in theirs
        assert t.chart.series == theirs[t.chart.key].series


def test_a_chart_with_no_data_is_absent_rather_than_empty(client):
    """An axis with no line reads as "zero", and zero alerts is a different
    statement from having no alert data at all."""
    _, tid = _owner()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    out = account.build_trends(db, tenant, days=30)
    db.close()
    assert out.trends == []
    assert out.headline == "", "a tenant with nothing recorded must not be narrated"


def test_devices_never_gets_a_verdict():
    """More devices is not better and fewer is not worse. A console that told
    a customer their network got worse because they plugged in a printer would
    be wrong in a way they would remember."""
    agg, good = account._TRENDS["devices"]
    assert good is None
    says, direction = account._say("Devices online", agg, good, [2.0, 2.0], [9.0, 9.0])
    assert direction == ""
    assert "up from 2 to 9" in says


def test_a_wobble_is_not_reported_as_a_trend():
    """This page is read aloud. Narrating normal noise as movement is how a
    customer stops believing the ones that matter."""
    says, direction = account._say("Security score", "mean", "up", [80.0, 80.0], [82.0, 82.0])
    assert direction == "steady"
    assert "steady" in says


def test_fewer_alerts_is_better_and_more_is_worse():
    better = account._say("New alerts", "sum", "down", [6.0, 6.0], [1.0, 1.0])
    worse = account._say("New alerts", "sum", "down", [1.0, 1.0], [6.0, 6.0])
    assert better[1] == "better"
    assert worse[1] == "worse"


def test_two_readings_are_not_a_trend():
    """With fewer than four points "the first half" is a single reading, and
    one number against one number is not a direction of travel."""
    assert account._say("New alerts", "sum", "down", *account._halves([])) == ("", "")


def test_the_sentence_names_which_series_it_measured(client):
    """A tenant with warnings and no criticals gets a sentence about warnings,
    and it has to SAY warnings.

    `_alerts_series` appends CRIT before WARN, so the chart's first series --
    the one the sentence is computed from -- is whichever exists. Reporting a
    fall in warnings as "New alerts: down" claims a fall in all alerts, on a
    page that is read aloud to the customer those alerts belong to.
    """
    _, tid = _owner()
    _alerts(tid, [(500, 4), (400, 4), (100, 1), (50, 1)], sev="WARN")
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    out = account.build_trends(db, tenant, days=30)
    db.close()
    alerts = [t for t in out.trends if t.chart.key == "alerts"]
    assert alerts, "no alerts trend built"
    assert "warning" in alerts[0].says.lower(), alerts[0].says


def test_a_series_already_named_by_its_chart_is_not_repeated():
    """"Security score (score)" is noise. The series is named only when its
    name is not already inside the chart's label."""
    says, _ = account._say("Security score", "mean", "up", [80.0, 80.0], [95.0, 95.0])
    assert "(score)" not in says


def test_the_headline_leads_with_the_bad_news():
    """When something is going the wrong way that is the sentence worth having
    at the top, even if two other charts are fine."""
    import guardian_hub.schemas_dash as sd
    mixed = [sd.AccountTrend(direction="better"), sd.AccountTrend(direction="worse")]
    assert "wrong way" in account._headline(mixed)
    assert account._headline([sd.AccountTrend(direction="")]) == ""
