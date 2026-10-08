"""Acknowledging an alert whose key contains a slash must work.

The owner's report: "Method Not Allowed" on ack. The one open alert was
`sec:panel:FlyCheap:/admin` -- a key with a `/` in it. `/alerts/{key}/{op}`
split that into three segments, so no POST route matched. Percent-encoding
on the client does not help: the ASGI server decodes `%2F` back to `/`
before routing. The route itself has to accept slashes (`{key:path}`).
"""
from urllib.parse import quote

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

KEY = "sec:panel:FlyCheap:/admin"


def _open(tid):
    db = SessionLocal()
    try:
        db.query(m.Alert).filter_by(tenant_id=tid, key=KEY).delete()
        now = "2026-09-16T10:00:00-04:00"
        db.add(m.Alert(tenant_id=tid, key=KEY, sev="INFO", status="open",
                       msg="FlyCheap: /admin is reachable from the internet", first_seen=now, last_seen=now))
        db.commit()
    finally:
        db.close()


def test_ack_works_for_a_key_containing_a_slash_encoded(client):
    tid = seeded_demo(client)
    _open(tid)
    r = client.post(f"/api/v1/alerts/{quote(KEY, safe='')}/ack")
    assert r.status_code == 200, f"{r.status_code}: {r.text[:120]}"
    assert SessionLocal().query(m.Alert).filter_by(tenant_id=tid, key=KEY).one().status == "acked"


def test_ack_works_for_a_key_containing_a_slash_raw(client):
    tid = seeded_demo(client)
    _open(tid)
    r = client.post(f"/api/v1/alerts/{KEY}/resolve")   # the browser may send it raw too
    assert r.status_code == 200, f"{r.status_code}: {r.text[:120]}"
    assert SessionLocal().query(m.Alert).filter_by(tenant_id=tid, key=KEY).one().status == "resolved"
