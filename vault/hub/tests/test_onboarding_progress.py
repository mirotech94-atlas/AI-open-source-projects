"""What the tenant still needs, derived rather than remembered.

The wizard used to walk a fixed array and store its position in
`UserOnboarding.step`, mirrored in `localStorage`. That is sound for one
network and one sensor, and it cannot describe the shape a real practice has:
three LANs behind one WAN, two sensors enrolled and one not, one reporting and
one silent.

Worse than incomplete, a stored cursor can DISAGREE with the rows -- saying
"install a sensor" while two are already pushing scans, or "done" while a
declared network has nothing on it. Every such disagreement is a support call.

So there is no cursor to get wrong. These tests set up rows and assert the
reading, because the reading is a function of the rows.
"""
import datetime as dt

from guardian_hub import models as m, onboarding
from guardian_hub.db import SessionLocal
from tests.test_auth import seed_user


def _net(db, tid: str, slug: str, primary: bool = False) -> m.Network:
    n = m.Network(tenant_id=tid, name=slug.title(), slug=slug, is_primary=primary)
    db.add(n); db.flush()
    return n


def _read(uid: str, tid: str):
    db = SessionLocal()
    st = onboarding.state(db, db.get(m.User, uid), db.get(m.Tenant, tid))
    db.close()
    return st


def test_a_tenant_with_no_networks_is_asked_for_one(client):
    uid, tid = seed_user()
    st = _read(uid, tid)
    assert st.networks == []
    assert st.next is not None and st.next.action == "declare_network"


def test_a_declared_network_with_no_sensor_asks_for_one(client):
    uid, tid = seed_user()
    db = SessionLocal(); net = _net(db, tid, "clinic", primary=True); db.commit()
    nid = net.id; db.close()
    st = _read(uid, tid)
    assert [n.sensor for n in st.networks] == ["none"]
    assert st.next.action == "enrol_sensor" and st.next.network_id == nid


def test_a_minted_code_reads_as_pending_not_as_installed(client):
    """A code that was never redeemed is not a sensor. Telling someone to
    install one they already have a code for is different advice from telling
    them their sensor is silent."""
    uid, tid = seed_user()
    db = SessionLocal(); net = _net(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, enrol_code="ABCD2345", status="pending"))
    db.commit(); db.close()
    assert [n.sensor for n in _read(uid, tid).networks] == ["pending"]


def test_a_sensor_that_enrolled_but_never_spoke_is_not_one_that_is_missing(client):
    uid, tid = seed_user()
    db = SessionLocal(); net = _net(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                    last_seen=dt.datetime.now(dt.timezone.utc)))
    db.commit(); db.close()
    st = _read(uid, tid)
    assert st.networks[0].sensor == "active"
    assert st.networks[0].first_scan_at == ""
    assert st.next.action == "await_first_scan"


def test_a_sensor_that_worked_and_stopped_reads_as_quiet(client):
    """The archetype of `remedy`'s `human` tier: no fix Vault runs can reach
    a host that is not answering. Collapsing this into "active" would have the
    wizard wait forever for a scan that is not coming."""
    uid, tid = seed_user()
    db = SessionLocal(); net = _net(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                    last_seen=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)))
    db.commit(); db.close()
    assert _read(uid, tid).networks[0].sensor == "quiet"


def test_a_revoked_sensor_counts_as_no_sensor_at_all(client):
    uid, tid = seed_user()
    db = SessionLocal(); net = _net(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, status="revoked",
                    last_seen=dt.datetime.now(dt.timezone.utc)))
    db.commit(); db.close()
    assert _read(uid, tid).networks[0].sensor == "none"


def test_a_redundant_second_sensor_does_not_make_a_network_uncovered(client):
    """`Sensor.network_id` is not unique and a spare is legitimate. A network
    is covered when ANY bound sensor is active -- taking the first row would
    make coverage depend on insertion order."""
    uid, tid = seed_user()
    db = SessionLocal(); net = _net(db, tid, "clinic", primary=True)
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, enrol_code="WXYZ6789", status="pending"))
    db.add(m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                    last_seen=dt.datetime.now(dt.timezone.utc)))
    db.commit(); db.close()
    assert _read(uid, tid).networks[0].sensor == "active"


def test_the_next_thing_is_the_second_network_once_the_first_is_covered(client):
    """The property a cursor cannot have. A network added a week later re-opens
    setup at THAT network, because "needs a sensor" is a fact about the network
    and not a position somebody forgot to rewind."""
    uid, tid = seed_user()
    db = SessionLocal()
    a = _net(db, tid, "clinic", primary=True)
    b = _net(db, tid, "lab")
    db.add(m.Sensor(tenant_id=tid, network_id=a.id, status="active",
                    last_seen=dt.datetime.now(dt.timezone.utc)))
    db.commit(); bid = b.id; db.close()
    st = _read(uid, tid)
    assert st.next.action == "enrol_sensor" and st.next.network_id == bid


def test_completion_is_still_per_user_and_still_stored(client):
    """Derived state replaces the TENANT cursor only. Whether THIS person has
    been shown around is a fact about them that no row can derive, which is the
    whole point of `onboarding.py`'s existing docstring."""
    uid, tid = seed_user()
    db = SessionLocal()
    user, tenant = db.get(m.User, uid), db.get(m.Tenant, tid)
    onboarding.save(db, user, tenant, completed=True); db.commit(); db.close()
    assert _read(uid, tid).show is False


# --- what the first scan found -------------------------------------------

from tests.querycount import queries_for          # noqa: E402
from tests.test_auth import login                 # noqa: E402


def _scan(db, tid: str, sensor_id: str, ts: str):
    db.add(m.Scan(tenant_id=tid, sensor_id=sensor_id, ts=ts, raw={}))


def test_the_first_scan_is_found_through_the_sensor_that_pushed_it(client):
    """`Scan` carries a `sensor_id`, not a `network_id`. The link to a network
    runs through `Sensor.network_id`, so a scan counts for the network its
    sensor was bound to -- which is the whole reason enrol codes are minted per
    network in the first place."""
    uid, tid = seed_user()
    db = SessionLocal()
    net = _net(db, tid, "clinic", primary=True)
    s = m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                 last_seen=dt.datetime.now(dt.timezone.utc))
    db.add(s); db.flush()
    _scan(db, tid, s.id, "2026-09-18T09:00:00+00:00")
    _scan(db, tid, s.id, "2026-09-19T09:00:00+00:00")
    db.commit(); db.close()
    assert _read(uid, tid).networks[0].first_scan_at == "2026-09-18T09:00:00+00:00"


def test_the_earliest_scan_wins_even_when_sensors_wrote_different_offsets(client):
    """`Scan.ts` is a string carrying whatever UTC offset the sensor wrote, so
    MIN() over it is a lexicographic comparison and picks the wrong row when
    two sensors disagree about the offset. Same trap `demo_trickle._tenant_offset`
    documents. Compared as instants instead."""
    uid, tid = seed_user()
    db = SessionLocal()
    net = _net(db, tid, "clinic", primary=True)
    a = m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                 last_seen=dt.datetime.now(dt.timezone.utc))
    b = m.Sensor(tenant_id=tid, network_id=net.id, status="active",
                 last_seen=dt.datetime.now(dt.timezone.utc))
    db.add_all([a, b]); db.flush()
    # 22:00-04:00 is 02:00Z the NEXT day -- later in time, earlier as a string.
    _scan(db, tid, a.id, "2026-09-18T22:00:00-04:00")
    _scan(db, tid, b.id, "2026-09-18T23:00:00+00:00")
    db.commit(); db.close()
    assert _read(uid, tid).networks[0].first_scan_at == "2026-09-18T23:00:00+00:00"


def test_a_scan_from_another_networks_sensor_does_not_count(client):
    uid, tid = seed_user()
    db = SessionLocal()
    a = _net(db, tid, "clinic", primary=True)
    b = _net(db, tid, "lab")
    sa = m.Sensor(tenant_id=tid, network_id=a.id, status="active",
                  last_seen=dt.datetime.now(dt.timezone.utc))
    sb = m.Sensor(tenant_id=tid, network_id=b.id, status="active",
                  last_seen=dt.datetime.now(dt.timezone.utc))
    db.add_all([sa, sb]); db.flush()
    _scan(db, tid, sa.id, "2026-09-18T09:00:00+00:00")
    db.commit(); db.close()
    st = _read(uid, tid)
    by_slug = {n.slug: n for n in st.networks}
    assert by_slug["clinic"].first_scan_at == "2026-09-18T09:00:00+00:00"
    assert by_slug["lab"].first_scan_at == ""
    assert st.next.action == "await_first_scan" and st.next.network_id == b.id


def test_the_group_summary_uses_the_same_classifier_as_the_assets_page(client):
    """"12 cameras, 8 phones" is the whole post-scan view. It is grouped here
    with `assets.kind_of`/`group_of` rather than counted in the browser,
    because a summary that grouped devices differently from the screen it
    links to would be discovered by the customer."""
    uid, tid = seed_user()
    db = SessionLocal()
    net = _net(db, tid, "clinic", primary=True)
    for i in range(3):
        db.add(m.Device(tenant_id=tid, network_id=net.id, mac=f"aa:bb:cc:00:00:0{i}",
                        vendor="Reolink", kind="camera"))
    db.add(m.Device(tenant_id=tid, network_id=net.id, mac="aa:bb:cc:00:00:09",
                    vendor="Brother", kind="printer"))
    db.commit(); db.close()
    n = _read(uid, tid).networks[0]
    assert n.devices == 4
    assert sum(n.by_group.values()) == 4
    assert max(n.by_group, key=lambda g: n.by_group[g]) is not None
    assert 3 in n.by_group.values()


def test_reading_progress_does_not_cost_a_query_per_network(client):
    """The ledger's rule applied here. Counted at two sizes and compared rather
    than asserted against a ceiling: a ceiling loose enough not to be brittle
    sits above a real N+1 on a small fixture, which is exactly how the
    wall-clock versions of these guards failed."""
    uid, tid = seed_user()
    login(client)
    db = SessionLocal()
    _net(db, tid, "aclinic", primary=True)
    _net(db, tid, "blab")
    db.commit(); db.close()
    few = queries_for(client, "/api/v1/onboarding")

    db = SessionLocal()
    for i in range(10):
        _net(db, tid, f"site{i}")
    db.commit(); db.close()
    many = queries_for(client, "/api/v1/onboarding")

    assert few == many, f"{few} statements for 2 networks but {many} for 12"
