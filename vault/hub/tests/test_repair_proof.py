"""Item 25: a check says what was DONE about it, not just what was found.

A red finding that goes green tells a customer nothing about whether anybody
fixed it -- the thing may simply have stopped being visible to the scan.
This pins the distinction the whole feature rests on: a command that exited
0 is a report, and only a later look at the target is proof.
"""
import datetime as dt

from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.payloads import security
from tests.test_seed_helpers import seeded_demo


def _fix(tid, **over):
    row = dict(tenant_id=tid, ts="2026-09-20T10:00:00+00:00", host="vps1",
               action="enable_ufw", label="Turn the firewall on", by="auto",
               ok=True, out="Firewall is active and enabled on system startup")
    row.update(over)
    return m.Fix(**row)


def _proofs(tid, key="firewall:vps1", target="vps1"):
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(id=tid).one()
    out = security._repairs_for(db, tenant, key, target)
    db.close()
    return out


def test_a_command_that_exited_zero_is_not_yet_proof(client):
    tid = seeded_demo(client)
    db = SessionLocal(); db.add(_fix(tid)); db.commit(); db.close()

    (p,) = _proofs(tid)
    assert p.ran_ok is True
    assert p.confirmed_at == ""
    assert "not yet proof" in p.proof
    assert p.by == "Vault" and p.by_person is False


def test_a_later_scan_looking_again_IS_the_proof(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    db.add(_fix(tid, verified_at=dt.datetime(2026, 9, 20, 10, 25, tzinfo=dt.timezone.utc)))
    db.commit(); db.close()

    (p,) = _proofs(tid)
    assert p.confirmed_at.startswith("2026-09-20T10:25")
    assert p.proof == "A later scan found the firewall active."


def test_a_failed_command_says_so_rather_than_going_quiet(client):
    tid = seeded_demo(client)
    db = SessionLocal(); db.add(_fix(tid, ok=False, out="ufw: command not found")); db.commit(); db.close()
    (p,) = _proofs(tid)
    assert p.ran_ok is False and p.confirmed_at == ""
    assert p.proof == "The command did not succeed."


def test_a_persons_yes_is_credited_to_them_not_to_guardian(client):
    """`payloads/account.is_person` is the ONE classifier for this. A second
    rule here would eventually disagree with it, and the owner's own counts
    have already been wrong that way once."""
    tid = seeded_demo(client)
    db = SessionLocal(); db.add(_fix(tid, by="hub:miro")); db.commit(); db.close()
    (p,) = _proofs(tid)
    assert p.by_person is True and p.by == "hub:miro"


def test_a_check_with_no_repair_of_its_own_carries_none(client):
    """Wi-Fi, TLS, email and exposure checks have no Vault-run repair.
    An empty list is the honest answer, not a missing section."""
    tid = seeded_demo(client)
    db = SessionLocal(); db.add(_fix(tid)); db.commit(); db.close()
    assert _proofs(tid, key="wifi:unverified", target="vps1") == []


def test_repairs_for_another_host_are_not_borrowed(client):
    tid = seeded_demo(client)
    db = SessionLocal(); db.add(_fix(tid, host="vps2")); db.commit(); db.close()
    assert _proofs(tid, target="vps1") == []


def test_newest_first_means_newest_BY_TIME_not_by_insert_order(client):
    """A sensor catching up after a gap pushes several repairs at once, so
    row id is the order they ARRIVED, not the order they happened. Sorting
    on it put an older repair at the top -- which is the wrong answer to
    "has this been fixed since?"."""
    tid = seeded_demo(client)
    db = SessionLocal()
    # Inserted oldest-last on purpose: id order and time order disagree.
    db.add(_fix(tid, ts="2026-09-20T10:00:00+00:00", out="newer"))
    db.add(_fix(tid, ts="2026-09-19T10:00:00+00:00", out="older"))
    db.commit(); db.close()

    newest, older = _proofs(tid)
    assert newest.ran_at.startswith("2026-09-20")
    assert older.ran_at.startswith("2026-09-19")
