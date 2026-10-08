"""What a leaver still has on the network a fortnight after they left.

"Your former office manager's laptop has been on this network for six
weeks" sells a subscription in one sentence, and it is also a real control
— unauthorised device detection and access review are on every
questionnaire, and a signed-off checklist is an evidence artifact.

The rule the feature rests on, and most of what is tested here:
**ownership is typed by a person and never inferred.** Vault can see a
laptop; it cannot see whose it is. `sarah-macbook` is a guess, and a guess
on an offboarding checklist puts the wrong name against a device in the one
document whose entire value is that someone can sign it.
"""
import datetime as dt

from guardian_hub import models as m, offboarding
from guardian_hub.db import SessionLocal
from tests.test_api_v1 import bearer, seeded

NOW = dt.datetime.now(dt.timezone.utc)


def _device(db, tid, mac, owner="", label="", days_ago=1):
    db.add(m.Device(tenant_id=tid, mac=mac, owner=owner, label=label,
                    last_seen=(NOW - dt.timedelta(days=days_ago)).isoformat()))
    db.commit()


def _tenant(tid):
    db = SessionLocal()
    return db.get(m.Tenant, tid), db


def test_a_leavers_devices_are_listed(client):
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:01", owner="Sarah Blake", label="Sarah's laptop")
        _device(db, tid, "aa:00:00:00:00:02", owner="Someone Else", label="other")
        got = offboarding.sweep(db, t, "Sarah Blake")
        assert got["counts"]["tagged"] == 1
        assert got["devices"][0]["label"] == "Sarah's laptop"
    finally:
        db.close()


def test_matching_the_owner_is_not_case_sensitive(client):
    """Whoever types the leave request is not the person who typed the tag."""
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:01", owner="Sarah Blake")
        assert offboarding.sweep(db, t, "sarah blake")["counts"]["tagged"] == 1
    finally:
        db.close()


def test_a_device_still_here_long_after_the_leave_date_is_the_finding(client):
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:01", owner="Sarah Blake", days_ago=0)
        left = (NOW - dt.timedelta(days=40)).date().isoformat()
        got = offboarding.sweep(db, t, "Sarah Blake", left_on=left)
        assert got["counts"]["still_here_after_notice"] == 1
        assert got["devices"][0]["seen_since_they_left"] is True
    finally:
        db.close()


def test_a_device_collected_promptly_is_not_a_finding(client):
    """Last seen before they left is the handover working."""
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:01", owner="Sarah Blake", days_ago=40)
        left = (NOW - dt.timedelta(days=30)).date().isoformat()
        got = offboarding.sweep(db, t, "Sarah Blake", left_on=left)
        assert got["counts"]["still_here_after_notice"] == 0
        assert got["devices"][0]["seen_since_they_left"] is False
    finally:
        db.close()


def test_inside_the_notice_window_is_not_yet_overdue(client):
    """Two weeks covers a notice period, a handover and a holiday."""
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:01", owner="Sarah Blake", days_ago=0)
        left = (NOW - dt.timedelta(days=3)).date().isoformat()
        got = offboarding.sweep(db, t, "Sarah Blake", left_on=left)
        assert got["counts"]["still_here_after_notice"] == 0
    finally:
        db.close()


def test_without_a_leave_date_it_lists_but_claims_nothing_overdue(client):
    """Useful before someone leaves — what will need collecting — but
    there is no date for anything to be overdue against."""
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:01", owner="Sarah Blake", days_ago=0)
        got = offboarding.sweep(db, t, "Sarah Blake")
        assert got["counts"]["tagged"] == 1
        assert got["counts"]["still_here_after_notice"] == 0
        assert got["left_on"] is None
    finally:
        db.close()


def test_an_untagged_device_is_never_attributed_to_anyone(client):
    """The rule the feature rests on. A hostname is a guess, and a guess
    here puts the wrong name on a document somebody signs."""
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:09", owner="", label="sarah-macbook")
        got = offboarding.sweep(db, t, "Sarah Blake")
        assert got["counts"]["tagged"] == 0, "a matching hostname is not ownership"
    finally:
        db.close()


def test_the_owner_list_admits_how_much_is_untagged(client):
    """A sweep is only as good as the tagging behind it, and a page that
    hides the untagged count implies a completeness it has not got."""
    tid, _secret, _ = seeded(client)
    t, db = _tenant(tid)
    try:
        _device(db, tid, "aa:00:00:00:00:01", owner="Sarah Blake")
        _device(db, tid, "aa:00:00:00:00:02", owner="")
        rows = offboarding.owners(db, t)
        untagged = [r for r in rows if r.get("untagged")]
        assert untagged and untagged[0]["devices"] >= 1
    finally:
        db.close()


# --- the endpoints --------------------------------------------------------

def test_tagging_a_device_and_sweeping_for_it(client):
    """Tagged through the ordinary device write path, so the edit is
    audited exactly like a label change rather than through a side door."""
    tid, secret, write = seeded(client)
    mac = client.get("/v1/devices", headers=bearer(secret)).json()["items"][0]["mac"]

    r = client.post(f"/v1/devices/{mac}", json={"owner": "Sarah Blake"},
                    headers=bearer(write))
    assert r.status_code == 200, r.text

    got = client.get("/v1/offboarding?owner=Sarah%20Blake", headers=bearer(secret)).json()
    assert got["counts"]["tagged"] == 1
    assert got["devices"][0]["mac"] == mac


def test_clearing_an_owner_removes_them_from_the_sweep(client):
    tid, secret, write = seeded(client)
    mac = client.get("/v1/devices", headers=bearer(secret)).json()["items"][0]["mac"]
    client.post(f"/v1/devices/{mac}", json={"owner": "Sarah Blake"}, headers=bearer(write))
    client.post(f"/v1/devices/{mac}", json={"owner": ""}, headers=bearer(write))
    got = client.get("/v1/offboarding?owner=Sarah%20Blake", headers=bearer(secret)).json()
    assert got["counts"]["tagged"] == 0


def test_a_read_token_cannot_tag_a_device(client):
    tid, secret, _write = seeded(client)
    mac = client.get("/v1/devices", headers=bearer(secret)).json()["items"][0]["mac"]
    r = client.post(f"/v1/devices/{mac}", json={"owner": "X"}, headers=bearer(secret))
    assert r.status_code in (401, 403)


def test_tagging_an_unknown_device_is_404(client):
    _tid, _secret, write = seeded(client)
    r = client.post("/v1/devices/aa:bb:cc:dd:ee:ff", json={"owner": "X"},
                    headers=bearer(write))
    assert r.status_code == 404


def test_the_owner_listing_is_reachable(client):
    _tid, secret, _ = seeded(client)
    r = client.get("/v1/offboarding", headers=bearer(secret))
    assert r.status_code == 200 and "owners" in r.json()
