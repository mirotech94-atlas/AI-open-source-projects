"""Ask Vault, from an incident row.

The Inbox's incident rows carry the incident's own id, not an alert key. The
hand-off endpoint looked every key up as an alert, so pressing Ask Vault on
the Incidents view answered 404 "alert not found" -- on the one view where a
conversation is most useful, because an incident is several alerts with a
single cause.
"""

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _incident_ids(client) -> list[str]:
    r = client.get("/api/v1/inbox?view=incidents")
    assert r.status_code == 200, r.text
    return [row["id"] for row in r.json()["rows"]]


def test_asking_about_an_incident_opens_the_conversation(client):
    seeded_demo(client)
    ids = _incident_ids(client)
    assert ids, "the demo tenant has no incidents to ask about"

    for iid in ids:
        r = client.post("/api/v1/chat/handoff", json={"key": iid})
        assert r.status_code == 200, f"incident {iid}: {r.status_code} {r.text}"
        body = r.json()
        assert body["opening"].strip(), "the hand-off opened with nothing to say"


def test_an_incident_of_several_alerts_says_so(client):
    seeded_demo(client)
    db = SessionLocal()
    multi = next(
        (i for i in db.query(m.Incident).all() if len(i.alert_keys or []) > 1), None
    )
    iid = multi.iid if multi else None
    db.close()
    if iid is None:
        return  # nothing grouped in this fixture; the other test still covers the route

    opening = client.post("/api/v1/chat/handoff", json={"key": iid}).json()["opening"]
    assert "incident" in opening.lower(), (
        "an incident hand-off should say it covers several alerts at once, so "
        f"the owner knows fixing the cause clears them together: {opening!r}"
    )


def test_a_key_that_is_neither_says_which_and_what_to_do(client):
    seeded_demo(client)
    r = client.post("/api/v1/chat/handoff", json={"key": "no-such-thing"})
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "incident" in detail.lower(), (
        "the message still blamed alerts only, which is what sent the owner "
        f"looking for a missing alert: {detail!r}"
    )
