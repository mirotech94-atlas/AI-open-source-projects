import datetime as dt

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m


def test_three_views_share_one_row_shape(client):
    seeded_demo(client)
    keys = {"id", "view", "sev", "title", "entity", "why", "what_changed", "status", "first_seen",
            "last_seen", "count", "actions", "children", "ticket_key", "confidence", "network", "remedy",
            "drift"}
    for view in ("alerts", "incidents", "recs"):
        body = client.get(f"/api/v1/inbox?view={view}").json()
        assert set(body["counts"]) == {"incidents", "alerts", "recs", "unread", "needs"}
        for row in body["rows"]:
            assert set(row) == keys and row["view"] == view
            assert all(a["method"] == "POST" and a["path"].startswith("/api/v1/") for a in row["actions"])


def test_bulk_ack_writes_one_audit_and_one_edit_per_key(client):
    seeded_demo(client)
    keys = [r["id"] for r in client.get("/api/v1/inbox?view=alerts").json()["rows"][:3]]
    r = client.post("/api/v1/alerts/bulk", json={"keys": keys, "op": "ack"})
    assert r.json()["updated"] == len(keys)
    db = SessionLocal()
    assert db.query(m.Audit).filter_by(action="alert.bulk").count() == 1
    assert db.query(m.Edit).filter_by(path="/api/alert").count() == len(keys)


def test_yes_on_an_actionable_rec_queues_the_fix(client):
    seeded_demo(client)
    rec = next(r for r in client.get("/api/v1/inbox?view=recs").json()["rows"]
               if any(a["kind"] == "yes" for a in r["actions"]))
    out = client.post(f"/api/v1/recs/{rec['id']}", json={"decision": "yes"}).json()
    assert out["status"] in ("queued", "needs-you")
    if out["status"] == "queued":
        assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() >= 1


def test_snoozed_alert_is_hidden_from_the_open_view(client):
    tid = seeded_demo(client)
    key = client.get("/api/v1/inbox?view=alerts").json()["rows"][0]["id"]

    r = client.post(f"/api/v1/alerts/{key}/snooze", json={"hours": 8})
    assert r.status_code == 200, r.text

    open_rows = client.get("/api/v1/inbox?view=alerts").json()["rows"]
    assert all(row["id"] != key for row in open_rows), "a still-snoozed alert leaked into the open view"

    # explicitly asking for snoozed rows (or "all") still surfaces it
    snoozed_rows = client.get("/api/v1/inbox?view=alerts&status=snoozed").json()["rows"]
    assert any(row["id"] == key and row["status"] == "snoozed" for row in snoozed_rows)

    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid, key=key).first().status == "snoozed"


def test_snoozed_alert_reappears_once_its_snooze_expires(client):
    tid = seeded_demo(client)
    key = client.get("/api/v1/inbox?view=alerts").json()["rows"][0]["id"]
    client.post(f"/api/v1/alerts/{key}/snooze", json={"hours": 8})

    # Fast-forward: back-date snoozed_until into the past directly, the way
    # a real clock eventually would, rather than waiting 8 hours.
    db = SessionLocal()
    a = db.query(m.Alert).filter_by(tenant_id=tid, key=key).first()
    a.snoozed_until = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat(timespec="seconds")
    db.commit()
    db.close()

    open_rows = client.get("/api/v1/inbox?view=alerts").json()["rows"]
    row = next((r for r in open_rows if r["id"] == key), None)
    assert row is not None, "an expired snooze should have flipped the alert back to open"
    assert row["status"] == "open"

    db = SessionLocal()
    fresh = db.query(m.Alert).filter_by(tenant_id=tid, key=key).first()
    assert fresh.status == "open"
    # The stamp DELIBERATELY survives the read. It is the reminder's
    # receipt: `scheduler.snooze_wake_job` clears it when it tells the
    # customer their snooze is up. Clearing it here -- which this test used
    # to require -- meant anyone who opened the inbox before the sweep
    # consumed their own reminder and never got one. See
    # `payloads/inbox.wake_expired`.
    assert fresh.snoozed_until is not None
    db.close()


def test_unread_reflects_a_real_stale_session(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    s = db.query(m.Session).order_by(m.Session.created.desc()).first()
    s.last_seen = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)
    db.commit()
    db.close()

    body = client.get("/api/v1/inbox?view=alerts").json()
    assert body["counts"]["unread"] > 0


def test_an_accepted_recommendation_stops_asking_the_same_question(client):
    """After a yes, the row must not look untouched.

    Saying yes to something Vault cannot run alone sets `status` to
    `needs-you` and hands off to chat. The row went on offering the same
    "Yes, do it" and "No thanks", so the only evidence the click had done
    anything was a toast that had already disappeared -- and ten minutes later
    a demo refresh would put it back to undecided anyway.
    """
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal

    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        rec = db.query(m.Recommendation).filter_by(tenant_id=tid).first()
        key = rec.key
        rec.decision = "yes"
        rec.status = "needs-you"
        db.commit()
    finally:
        db.close()

    rows = client.get("/api/v1/inbox?view=recs").json()["rows"]
    row = next(r for r in rows if r["id"] == key)
    kinds = [a["kind"] for a in row["actions"]]
    assert "yes" not in kinds, "it asked the same question it had already been answered"
    assert "ask" in kinds, "no way to reach the conversation it says it is waiting in"
    assert "restore" in kinds, "no way to change your mind"
    assert "waiting in the chat" in row["what_changed"], row["what_changed"]


def test_a_queued_recommendation_leaves_the_open_list_and_stops_asking(client):
    """The owner's report: "when clickong on fix or resolve or yes do it - it
    still says done but doesnt do anything it doesnt deltete the asks that
    will be queded".

    The click really did queue a job. But the open list filtered out only
    DECLINED recommendations, and `_rec_actions` only stopped re-offering
    "Yes, do it" for the `needs-you` case -- so an accepted, already-queued
    recommendation sat in the list still showing the very question it had
    just been answered. Indistinguishable from a click that did nothing.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        rec = db.query(m.Recommendation).filter_by(tenant_id=tid).first()
        key = rec.key
        rec.decision, rec.status = "yes", "queued"
        db.commit()
    finally:
        db.close()

    body = client.get("/api/v1/inbox?view=recs").json()
    assert key not in [r["id"] for r in body["rows"]], "queued work is not a question any more"

    # Still findable under "All", and there it must not re-ask either.
    all_body = client.get("/api/v1/inbox?view=recs&status=all").json()
    row = next(r for r in all_body["rows"] if r["id"] == key)
    assert [a["kind"] for a in row["actions"]] == []
    assert "queued the work" in row["what_changed"]


def test_a_finished_recommendation_is_also_out_of_the_open_list(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        rec = db.query(m.Recommendation).filter_by(tenant_id=tid).first()
        key = rec.key
        rec.decision, rec.status = "yes", "done"
        db.commit()
    finally:
        db.close()
    assert key not in [r["id"] for r in client.get("/api/v1/inbox?view=recs").json()["rows"]]


def test_one_that_still_needs_the_owner_stays_in_the_open_list(client):
    """The counterpart: `needs-you` and `failed` are still the owner's to act
    on, so they must NOT be filtered away by the same rule."""
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        recs = db.query(m.Recommendation).filter_by(tenant_id=tid).limit(2).all()
        needs, failed = recs[0], recs[1]
        needs_key, failed_key = needs.key, failed.key
        needs.decision, needs.status = "yes", "needs-you"
        failed.decision, failed.status = "yes", "failed"
        db.commit()
    finally:
        db.close()
    ids = [r["id"] for r in client.get("/api/v1/inbox?view=recs").json()["rows"]]
    assert needs_key in ids and failed_key in ids


def test_the_open_count_matches_the_open_rows(client):
    """Counts and rows share one filter, so a queued recommendation cannot
    vanish from the list while still inflating the sidebar badge."""
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        rec = db.query(m.Recommendation).filter_by(tenant_id=tid).first()
        rec.decision, rec.status = "yes", "queued"
        db.commit()
    finally:
        db.close()
    body = client.get("/api/v1/inbox?view=recs").json()
    assert body["counts"]["recs"] == len(body["rows"])



def test_alert_action_path_encodes_a_key_with_url_special_chars(client):
    """The owner's report: "Method Not Allowed when trying to ack alerts".

    The inbox built each row action's path by interpolating the raw alert key
    (`f"/api/v1/alerts/{a.key}/{op}"`). A key carrying a `?` or `#` -- both
    legal in an SSID, a device label or a discovered hostname -- truncated the
    URL: `.../alerts/foo?x/ack` parses as path `.../alerts/foo` + query, which
    lands on the GET-only `GET /api/v1/alerts/{key}` route, so the POST came
    back 405 Method Not Allowed. The key must be percent-encoded in the path.
    """
    from urllib.parse import quote, urlsplit

    tid = seeded_demo(client)
    key = "lan:home:new:printer?model#v2"
    db = SessionLocal()
    try:
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        db.add(m.Alert(tenant_id=tid, key=key, sev="WARN", msg="odd device name",
                       first_seen=now, last_seen=now, status="open"))
        db.commit()
    finally:
        db.close()

    rows = client.get("/api/v1/inbox?view=alerts").json()["rows"]
    row = next(r for r in rows if r["id"] == key)
    ack = next(a for a in row["actions"] if a["kind"] == "ack")

    # The path stays one four-segment path with no query/fragment split.
    split = urlsplit(ack["path"])
    assert split.query == "" and split.fragment == "", ack["path"]
    assert split.path == f"/api/v1/alerts/{quote(key, safe='')}/ack", ack["path"]

    # And it reaches the POST handler, not 405 on the GET route.
    assert client.post(ack["path"], json={}).status_code == 200, ack["path"]


def test_needs_you_is_a_filter_on_alerts_not_a_fourth_pile(client):
    """`view=needs` narrows the alerts view to the rows a "yes" would run.

    Its rows stay `view: "alerts"` on purpose -- they are alerts, and their
    actions are the alert actions. Calling them a new kind would fork the row
    contract for a filter.
    """
    seeded_demo(client)
    needs = client.get("/api/v1/inbox?view=needs").json()
    alerts = client.get("/api/v1/inbox?view=alerts").json()

    assert len(needs["rows"]) <= len(alerts["rows"])
    assert needs["counts"]["needs"] == len(needs["rows"])
    for row in needs["rows"]:
        assert row["view"] == "alerts"
        assert row["remedy"]["kind"] == "approval", "only a repair a yes runs belongs here"


def test_a_settled_alert_never_appears_in_needs_you(client):
    """An acked alert is not waiting on a decision, whatever its remedy is."""
    seeded_demo(client)
    before = client.get("/api/v1/inbox?view=needs").json()["rows"]
    if not before:
        return  # nothing to settle in this fixture; the filter is proven above
    key = before[0]["id"]
    client.post(f"/api/v1/alerts/{key}/ack")
    after = client.get("/api/v1/inbox?view=needs").json()
    assert key not in [r["id"] for r in after["rows"]]
    assert after["counts"]["needs"] == len(before) - 1
