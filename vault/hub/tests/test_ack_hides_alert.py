"""Acknowledging an alert takes it out of "what needs me".

The owner acked a DMARC advisory and found it still sitting in the open list
with the badge still counting it. A button called Acknowledge that leaves the
row exactly where it was does nothing a person can perceive.

Ack is not resolve and does not pretend to be: the row keeps its status, is
still there under "All", and the sensor keeps the condition acked until it
genuinely clears. What changes is only whether it is presented as outstanding.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user


def _alert(tenant_id, key="email:dmarc-none:example.org", sev="INFO", status="open"):
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    db = SessionLocal()
    db.add(m.Alert(tenant_id=tenant_id, key=key, sev=sev, msg="DMARC is p=none",
                   status=status, first_seen=now, last_seen=now, count=1))
    db.commit(); db.close()


def _inbox(client, status="open"):
    r = client.get(f"/api/v1/inbox?view=alerts&status={status}")
    assert r.status_code == 200, r.text
    return r.json()


def test_an_acked_alert_leaves_the_open_list_and_the_count(client):
    _uid, tid = seed_user()
    login(client)
    _alert(tid)
    before = _inbox(client)
    assert any("DMARC" in (row.get("title") or "") + (row.get("body") or "")
               for row in before["rows"]), before["rows"]
    assert before["counts"]["alerts"] == 1

    assert client.post("/api/v1/alerts/email:dmarc-none:example.org/ack").status_code in (200, 201)

    after = _inbox(client)
    assert after["rows"] == [], "an acked alert must not stay in the open list"
    assert after["counts"]["alerts"] == 0, "nor keep counting against the badge"


def test_it_is_still_there_under_all_and_still_says_acked(client):
    """Hidden from "needs me", not deleted and not relabelled as fixed."""
    _uid, tid = seed_user()
    login(client)
    _alert(tid)
    client.post("/api/v1/alerts/email:dmarc-none:example.org/ack")

    rows = _inbox(client, status="all")["rows"]
    assert len(rows) == 1, rows
    db = SessionLocal()
    got = db.query(m.Alert).filter_by(key="email:dmarc-none:example.org").one()
    assert got.status == "acked"     # not "resolved"
    db.close()


def test_reopen_brings_it_back(client):
    """The way out is not one-way."""
    _uid, tid = seed_user()
    login(client)
    _alert(tid)
    client.post("/api/v1/alerts/email:dmarc-none:example.org/ack")
    assert _inbox(client)["rows"] == []
    client.post("/api/v1/alerts/email:dmarc-none:example.org/reopen")
    assert len(_inbox(client)["rows"]) == 1


def test_every_surface_agrees_on_what_counts_as_open(client):
    """The four queries that define this had already drifted: the inbox
    counted acked rows, the overview and topology did not, so one network
    reported two different open-alert totals depending on the page."""
    _uid, tid = seed_user()
    login(client)
    _alert(tid, key="site:Foo:down", sev="WARN")
    _alert(tid, key="site:Bar:down", sev="CRIT")
    client.post("/api/v1/alerts/site:Foo:down/ack")

    inbox_n = _inbox(client)["counts"]["alerts"]
    db = SessionLocal()
    direct_n = (db.query(m.Alert)
                  .filter(m.Alert.tenant_id == tid,
                          m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES)).count())
    db.close()
    assert inbox_n == direct_n == 1, (inbox_n, direct_n)
