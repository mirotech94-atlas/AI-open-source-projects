"""`GET /api/v1/platform/work` -- who has been closing the work, per day.

The owner console's counterpart to the design reference's "Self-healing
activity" chart: "fixed automatically" against "fixed after approval". A
repair is a `Fix` row and its `by` stamp says whose yes it was (`hub:<user>`
is a person's, `auto` is Vault's); a person closing an alert by hand is
an `alert.resolve` audit row. The audit trail alone was the wrong source --
it records the request, so the sensor's own automatic repairs were
invisible and the chart read one automatic fix against forty-nine.

Three properties, and none of them is the JSON's shape.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.querycount import queries_for
from tests.test_auth import login, seed_user


def _owner():
    uid, tid = seed_user(username="miro", email="m@example.com", tenant_slug="home")
    db = SessionLocal()
    db.get(m.User, uid).is_superadmin = True
    db.commit(); db.close()
    return tid


def _act(tid: str, action: str, user: str, days_ago: int, *, ok: bool = True) -> None:
    """A settled piece of work `days_ago` days back: a `fix` is a `Fix` row
    whose `by` is `user` (the hub stamps a person's as `hub:<user>`, the
    sensor its own as `auto`); anything else is an audit row."""
    db = SessionLocal()
    ts = dt.datetime.now(dt.timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) \
        - dt.timedelta(days=days_ago)
    if action == "fix":
        by = "auto" if user == "guardian" else (user if ":" in user or user == "dashboard" else f"hub:{user}")
        db.add(m.Fix(tenant_id=tid, ts=ts.isoformat(timespec="seconds"), host="x", action="security_updates",
                     label="install pending security updates", by=by, ok=ok))
    else:
        db.add(m.Audit(tenant_id=tid, user=user, role="admin", action=action, target="x", ts=ts))
    db.commit(); db.close()


def _work(client, days: int = 14) -> dict:
    r = client.get(f"/api/v1/platform/work?days={days}")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_tenant_admin_cannot_read_what_every_customer_has_been_doing(client):
    seed_user(role="admin")
    login(client)
    assert client.get("/api/v1/platform/work").status_code == 403


def test_guardians_own_work_is_told_apart_from_a_persons(client):
    """The whole point of the card.

    An owner looking at this is asking one question -- how much is Vault
    doing without me -- and a chart that lumped both together would answer it
    with the wrong number while looking right.
    """
    tid = _owner()
    _act(tid, "fix", "guardian", 1)
    _act(tid, "fix", "miro", 1)
    _act(tid, "alert.resolve", "chat:miro", 1)
    _act(tid, "fix", "guardian", 1, ok=False)
    login(client)
    body = _work(client)

    assert body["auto_total"] == 1, "a fix that failed settled nothing"
    # `hub:miro` counts as a person: a fix run from the dashboard or chat
    # is still one somebody asked for, and so is closing an alert by hand.
    assert body["human_total"] == 2


def test_a_quiet_day_is_a_zero_and_not_a_gap(client):
    """Every day in the window is present.

    Dropping the empty days would draw the chart straight from Monday to
    Wednesday and hide a quiet Tuesday -- the same reason the Ledger's
    sparkline emits a real zero rather than skipping the point.
    """
    tid = _owner()
    _act(tid, "fix", "guardian", 3)
    login(client)
    body = _work(client, days=7)

    assert len(body["days"]) == 7
    assert [d["day"] for d in body["days"]] == sorted(d["day"] for d in body["days"]), "oldest first"
    assert sum(d["auto"] + d["human"] for d in body["days"]) == 1


def test_only_work_that_was_SETTLED_is_counted(client):
    """Reading is not work closed.

    `chat.turn` and `device.update` are the two busiest actions in the trail,
    and counting them would swamp the chart with activity that settled
    nothing -- which would make the card look healthiest on the days the owner
    did the most typing.
    """
    tid = _owner()
    _act(tid, "chat.turn", "miro", 1)
    _act(tid, "device.update", "miro", 1)
    _act(tid, "alert.ack", "miro", 1)
    _act(tid, "fix", "miro", 1)
    login(client)
    body = _work(client)
    assert body["human_total"] == 1, "only the fix settled anything"


def test_nothing_outside_the_window_leaks_in(client):
    tid = _owner()
    _act(tid, "fix", "guardian", 2)
    _act(tid, "fix", "guardian", 40)
    login(client)
    body = _work(client, days=7)
    assert body["auto_total"] == 1


def test_the_cost_does_not_grow_with_the_length_of_the_window(client):
    """One grouped query, not one per day.

    A window is small today and the temptation to loop over it is real; a
    query per day would make this the one endpoint on the console that gets
    slower the further back the owner looks. Compared at two window sizes
    rather than held under a ceiling, for the reason CLAUDE.md gives.
    """
    tid = _owner()
    for d in range(0, 60, 2):
        _act(tid, "fix", "guardian" if d % 4 else "miro", d)
    login(client)
    short = queries_for(client, "/api/v1/platform/work?days=7")
    long = queries_for(client, "/api/v1/platform/work?days=90")
    assert short == long, f"{short} statements for 7 days but {long} for 90"


def test_one_customers_work_is_only_that_customers(client):
    """The console's Overview draws one network's chart. A chart that counted
    another customer's fixes under this customer's name would be the most
    confident wrong number on the page."""
    tid = _owner()
    _, other = seed_user(username="other", email="o@example.com", tenant_slug="other")
    _act(tid, "fix", "guardian", 1)
    _act(other, "fix", "guardian", 1)
    _act(other, "fix", "miro", 1)
    login(client)
    mine = client.get(f"/api/v1/platform/work?days=7&tenant={tid}").json()
    everyone = _work(client, 7)
    assert mine["auto_total"] == 1 and mine["human_total"] == 0
    assert everyone["auto_total"] == 2 and everyone["human_total"] == 1
    assert client.get("/api/v1/platform/work?tenant=nope").status_code == 404


def test_the_last_day_can_be_read_by_the_hour(client):
    """Twenty-four buckets for the 24h range, each keyed by its hour, the
    quiet ones present as zeros -- a single day-bucket would be one bar,
    which is a number drawn as a chart."""
    tid = _owner()
    db = SessionLocal()
    ts = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=2)
    db.add(m.Fix(tenant_id=tid, ts=ts.isoformat(timespec="seconds"), host="x", action="security_updates",
                 label="install pending security updates", by="auto", ok=True))
    db.commit(); db.close()
    login(client)
    body = client.get(f"/api/v1/platform/work?hours=24&tenant={tid}").json()
    assert len(body["days"]) == 24
    assert all(len(d["day"]) == 13 and d["day"][10] == "T" for d in body["days"]), "hour keys"
    assert body["auto_total"] == 1
    assert body["days"][-3]["auto"] == 1, "two hours ago is the third bucket from the end"

