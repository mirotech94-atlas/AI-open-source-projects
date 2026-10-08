"""Two enrolment credentials with two threat models.

A code shown on screen is read by somebody standing in front of it and lives
fifteen minutes. A code e-mailed to whoever can actually reach the server is
read hours later, and that delay is the entire reason for sending it.

Lengthening the window is not free, and the reason is in `routers/sensor.enrol`:
it matches on `filter_by(enrol_code=...)` with NO tenant in the lookup, so every
unredeemed code on the platform is guessable at once. A fifteen-minute life
keeps that set nearly empty; a seven-day life across many customers does not.

So the e-mailed code is a different credential rather than the same one held
open longer -- sixteen characters instead of eight, which costs the reader
nothing because nobody types it. It travels inside the install command.
"""
import datetime as dt

from guardian_hub import models as m, security, sensors
from guardian_hub.db import SessionLocal
from tests.test_auth import seed_user

ALPHABET = set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def test_the_emailed_code_is_much_harder_to_guess_than_the_screen_one():
    assert len(security.new_enrol_code()) == 8
    assert len(security.new_install_code()) == 16


def test_both_codes_avoid_the_characters_people_mistype():
    """No O/0 and no I/1. A code read down a phone line must not be typeable
    into a different code that also exists."""
    assert set(security.new_enrol_code()) <= ALPHABET
    assert set(security.new_install_code()) <= ALPHABET


def test_install_codes_do_not_repeat():
    assert len({security.new_install_code() for _ in range(200)}) == 200


def test_an_emailed_code_outlives_the_inbox_it_is_sent_to(client):
    _uid, tid = seed_user()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    now = dt.datetime.now(dt.timezone.utc)

    screen = sensors.new_enrol_code_for(db, tenant)
    emailed = sensors.new_enrol_code_for(db, tenant, emailed=True)
    db.commit()

    assert (screen.enrol_expires - now) < dt.timedelta(hours=1)
    assert (emailed.enrol_expires - now) > dt.timedelta(days=6)
    assert len(screen.enrol_code) == 8
    assert len(emailed.enrol_code) == 16
    db.close()


def test_the_emailed_code_still_binds_to_its_network(client):
    """The longer credential must not lose the property the whole per-network
    flow rests on: the sensor that redeems it lands on the right network."""
    _uid, tid = seed_user()
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    net = m.Network(tenant_id=tid, name="Clinic", slug="clinic", is_primary=True)
    db.add(net); db.flush()
    s = sensors.new_enrol_code_for(db, tenant, net.id, emailed=True)
    db.commit()
    assert s.network_id == net.id and s.status == "pending"
    db.close()


def test_a_sixteen_character_code_fits_the_column(client):
    """`Sensor.enrol_code` is String(16). If this fails the design went wrong:
    widening it is a migration, and this work is deliberately migration-free."""
    assert m.Sensor.__table__.c.enrol_code.type.length >= 16
