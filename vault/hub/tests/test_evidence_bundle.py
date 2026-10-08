"""One finding, everything Vault knows about it, in one document.

The trust plan (§2.5O) argues this is the highest-value thing in its whole
accuracy section, and the reason is a distinction the rest of the plan keeps
circling: a score or an alert asks to be believed, while a bundle can be
checked. It is what an MSP hands a client who asks what actually happened,
and what survives "prove it" from an auditor.

The property the tests defend is that it is assembled from records, not
composed at export time. A narrative generated now to explain a finding is
precisely what a bundle exists to make unnecessary — if Vault did not
store it, the bundle does not claim it.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_api_v1 import bearer, seeded


def _any_alert_key(client, secret):
    items = client.get("/v1/alerts", headers=bearer(secret)).json()["items"]
    assert items, "the scan fixture should raise alerts"
    return items[0]["key"]


def _bundle(client, secret, key):
    r = client.get(f"/v1/alerts/{key}/evidence", headers=bearer(secret))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_bundle_carries_the_finding_as_recorded(client):
    _tid, secret, _ = seeded(client)
    key = _any_alert_key(client, secret)
    b = _bundle(client, secret, key)
    assert b["alert"]["key"] == key
    assert b["alert"]["severity"] in ("CRIT", "WARN", "INFO")
    assert b["alert"]["message"]
    assert b["alert"]["first_seen"] and b["alert"]["last_seen"]
    assert b["exported_at"]


def test_it_names_the_sensor_and_the_version_that_saw_it(client):
    """A finding is only as good as the thing that made it, and a version is
    how a false positive gets traced back to a release."""
    _tid, secret, _ = seeded(client)
    b = _bundle(client, secret, _any_alert_key(client, secret))
    assert b["observed_by"]["sensor"]
    assert "version" in b["observed_by"]


def test_the_sensor_clock_and_the_hub_clock_are_both_reported(client):
    """Never reconciled into one. Skew between them is real (§2.5K), and a
    bundle that quietly picked one would hide the discrepancy an auditor
    most wants to see."""
    _tid, secret, _ = seeded(client)
    b = _bundle(client, secret, _any_alert_key(client, secret))
    assert "sensor_ts" in b["scan"] and "hub_received_ts" in b["scan"]
    assert b["scan"]["sensor_ts"]


def test_it_records_the_rubric_the_score_was_computed_under(client):
    _tid, secret, _ = seeded(client)
    b = _bundle(client, secret, _any_alert_key(client, secret))
    assert "model" in b["scoring"] and "coverage" in b["scoring"]


def test_it_says_what_guardian_can_and_cannot_repair(client):
    _tid, secret, _ = seeded(client)
    b = _bundle(client, secret, _any_alert_key(client, secret))
    assert b["remedy"]["tier"] in ("auto", "approval", "human")


def test_a_repair_that_ran_is_reported_apart_from_one_that_was_confirmed(client):
    """"The command exited zero" and "a later sweep saw the condition gone"
    are different claims. A bundle that merged them would be the exact
    self-flattery §2.5D objects to, in the one document meant to survive
    scrutiny."""
    tid, secret, _ = seeded(client)
    key = _any_alert_key(client, secret)
    host = (client.get(f"/v1/alerts/{key}", headers=bearer(secret)).json() or {})
    db = SessionLocal()
    try:
        alert = db.query(m.Alert).filter_by(tenant_id=tid, key=key).one()
        alert.evidence = {**(alert.evidence or {}), "host": "vps1"}
        db.add(m.Fix(tenant_id=tid, ts=dt.datetime.now(dt.timezone.utc).isoformat(),
                     host="vps1", action="enable_ufw", by="auto", ok=True, out="done"))
        db.commit()
    finally:
        db.close()
    b = _bundle(client, secret, key)
    assert b["repairs"], "a repair on the finding's host should be attached"
    row = b["repairs"][0]
    assert row["ok"] is True
    assert row["verified_at"] is None, "success is not confirmation"


def test_an_unknown_finding_is_404_not_an_empty_bundle(client):
    """An empty bundle would read as "we looked and there was nothing",
    which is a different and untrue statement."""
    _tid, secret, _ = seeded(client)
    r = client.get("/v1/alerts/not-a-real-key/evidence", headers=bearer(secret))
    assert r.status_code == 404


def test_another_accounts_finding_is_404_never_403(client):
    """The scoping rule the whole API holds to: a 403 would confirm the
    identifier is real."""
    _tid, secret, _ = seeded(client)
    r = client.get("/v1/alerts/sec:ufw:someone-elses-host/evidence", headers=bearer(secret))
    assert r.status_code == 404


def test_the_evidence_route_is_not_swallowed_by_the_single_alert_route(client):
    """A routing regression that reads exactly like a missing alert.

    `/v1/alerts/{key:path}` uses a greedy path converter, so if it is
    registered before this endpoint it matches `/alerts/<key>/evidence`
    whole, takes "<key>/evidence" as the key, finds nothing and answers a
    perfectly plausible 404 "alert not found". Nothing about that says
    "route ordering", and the endpoint's own happy-path tests were written
    before the converter changed, so they did not notice.

    This asserts the same key resolves on BOTH routes, which is the property
    registration order can break.
    """
    _tid, secret, _ = seeded(client)
    key = _any_alert_key(client, secret)
    assert client.get(f"/v1/alerts/{key}", headers=bearer(secret)).status_code == 200
    assert client.get(f"/v1/alerts/{key}/evidence", headers=bearer(secret)).status_code == 200


def test_a_key_containing_a_slash_still_reaches_its_evidence(client):
    """Why this endpoint takes `:path` too rather than a plain `{key}`: the
    single-alert route was widened to a path converter because real keys
    contain slashes, and an evidence route that could not accept one would
    be quietly unreachable for exactly those findings."""
    tid, secret, _ = seeded(client)
    db = SessionLocal()
    try:
        alert = db.query(m.Alert).filter_by(tenant_id=tid).first()
        alert.key = "site:https://shop.example/health:down"
        db.commit()
    finally:
        db.close()
    r = client.get("/v1/alerts/site:https://shop.example/health:down/evidence",
                   headers=bearer(secret))
    assert r.status_code == 200, r.text
    assert r.json()["alert"]["key"] == "site:https://shop.example/health:down"
