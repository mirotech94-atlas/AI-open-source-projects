"""Task 77 / F3: a disruptive fix may never run on a bare POST.

`full_upgrade`, `reboot` and the rest of `chat.tiers.TIER3_FIXES` are
disruptive, so `POST /api/v1/fix` now demands the same one-shot, bound
confirmation chat's tier-3 flow uses (`acttokens.mint_chat`/`consume_chat`).
The happy path is one test; the four ways a token must fail are the point.
"""

from guardian_hub import models as m
from guardian_hub.chat.tiers import TIER2_FIXES, TIER3_FIXES
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import confirmed_fix, seeded_demo


def _queued(tenant_id: str) -> list[m.Queue]:
    db = SessionLocal()
    try:
        return db.query(m.Queue).filter_by(tenant_id=tenant_id, path="/api/fix").all()
    finally:
        db.close()


def _add_admin(tenant_id: str, username: str, email: str, role: str = "admin") -> None:
    """A second user with a membership on the SAME tenant -- `test_auth.
    seed_user` always makes its own tenant, and the point here is a token
    minted for one user being spent by another inside one tenant."""
    db = SessionLocal()
    try:
        u = m.User(username=username, email=email, display_name=username)
        db.add(u)
        db.flush()
        db.add(m.Membership(user_id=u.id, tenant_id=tenant_id, role=role))
        db.commit()
    finally:
        db.close()


def _mint(client, host="prod", action="full_upgrade", arg=None) -> str:
    r = client.post("/api/v1/fix/confirm", json={"host": host, "action": action, "arg": arg})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == 3 and body["token"] and body["expires"]
    assert host in body["summary"] and "disruptive" in body["summary"]
    return body["token"]


def test_full_upgrade_needs_a_confirmation_and_queues_nothing_without_one(client):
    tid = seeded_demo(client)
    r = client.post("/api/v1/fix", json={"host": "prod", "action": "full_upgrade"})
    assert r.status_code == 409
    assert "confirm" in r.json()["detail"]
    assert _queued(tid) == [], "a refused disruptive fix must queue nothing at all"


def test_a_confirmed_full_upgrade_queues_once_and_is_audit_logged(client):
    tid = seeded_demo(client)
    assert confirmed_fix(client, "prod", "full_upgrade").status_code == 200
    rows = _queued(tid)
    assert len(rows) == 1 and rows[0].body["action"] == "full_upgrade"

    actions = [r["action"] for r in client.get("/api/v1/audit").json()["rows"]]
    assert "fix" in actions and "fix.confirm" in actions


def test_a_confirmation_cannot_be_replayed(client):
    tid = seeded_demo(client)
    token = _mint(client)
    body = {"host": "prod", "action": "full_upgrade", "arg": None, "confirm_token": token}
    assert client.post("/api/v1/fix", json=body).status_code == 200
    second = client.post("/api/v1/fix", json=body)
    assert second.status_code == 403
    assert len(_queued(tid)) == 1, "the replay must not queue a second upgrade"


def test_a_confirmation_is_bound_to_its_exact_target(client):
    tid = seeded_demo(client)
    token = _mint(client, host="prod", action="full_upgrade")

    # different host, different action, different argument -- all refused, and
    # the token is still unspent afterwards because nothing burnt it.
    for body in (
        {"host": "other", "action": "full_upgrade", "arg": None},
        {"host": "prod", "action": "reboot", "arg": None},
        {"host": "prod", "action": "full_upgrade", "arg": "openssl"},
    ):
        r = client.post("/api/v1/fix", json={**body, "confirm_token": token})
        assert r.status_code == 403, body
    assert _queued(tid) == []

    ok = client.post("/api/v1/fix", json={"host": "prod", "action": "full_upgrade",
                                          "arg": None, "confirm_token": token})
    assert ok.status_code == 200


def test_an_expired_confirmation_is_refused(client):
    import datetime as dt

    tid = seeded_demo(client)
    token = _mint(client)
    db = SessionLocal()
    try:
        row = db.get(m.ActToken, token)
        row.expires = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()

    r = client.post("/api/v1/fix", json={"host": "prod", "action": "full_upgrade",
                                         "arg": None, "confirm_token": token})
    assert r.status_code == 403
    assert _queued(tid) == []


def test_another_users_confirmation_is_refused(client):
    """The token is bound to the user who was shown the sentence."""
    from tests.test_auth import login

    tid = seeded_demo(client)
    token = _mint(client)
    client.post("/api/v1/auth/logout")
    _add_admin(tid, "ann", "a@x.com")
    login(client, "ann", "a@x.com")

    r = client.post("/api/v1/fix", json={"host": "prod", "action": "full_upgrade",
                                         "arg": None, "confirm_token": token})
    assert r.status_code == 403
    assert _queued(tid) == []


def test_a_reversible_fix_still_runs_on_one_post(client):
    """The gate is for the disruptive set only -- tier 2 has never asked."""
    tid = seeded_demo(client)
    assert "security_updates" in TIER2_FIXES and "full_upgrade" in TIER3_FIXES
    assert client.post("/api/v1/fix", json={"host": "prod", "action": "security_updates"}).status_code == 200
    assert len(_queued(tid)) == 1
    # ...and a confirmation is not on offer for one, so no UI can grow the step.
    assert client.post("/api/v1/fix/confirm",
                       json={"host": "prod", "action": "security_updates"}).status_code == 422


def test_confirming_is_admin_only(client):
    from tests.test_auth import login

    tid = seeded_demo(client)
    client.post("/api/v1/auth/logout")
    _add_admin(tid, "op", "o@x.com", role="operator")
    login(client, "op", "o@x.com")
    assert client.post("/api/v1/fix/confirm",
                       json={"host": "prod", "action": "full_upgrade"}).status_code == 403
    assert client.post("/api/v1/fix",
                       json={"host": "prod", "action": "full_upgrade"}).status_code == 403
