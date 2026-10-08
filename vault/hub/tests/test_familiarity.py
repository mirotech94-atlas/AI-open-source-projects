"""'Is this device one of ours?' -- answered from history Vault holds.

The commonest worry on a home network is an unnamed device with a
randomized MAC, and the only way to answer it used to be walking round the
house comparing MACs on screens. Vault has watched that device since it
appeared: which days, which hours, joins and leaves, DNS, the same name
under other MACs. Assembled, that beats a MAC lookup -- a stranger's phone
does not keep the household's hours for five nights running.

The verdict is evidence with a name, never certainty.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.chat import registry
from guardian_hub.db import SessionLocal
from guardian_hub.payloads.familiarity import device_familiarity

from tests.test_chat_tools import ctx_for
from tests.test_seed_helpers import seeded_demo


def _tenant(db, slug):
    t = m.Tenant(slug=slug, name=slug)
    db.add(t); db.commit()
    return t


def _device(db, t, mac, **kw):
    d = m.Device(tenant_id=t.id, mac=mac, ip=kw.pop("ip", "192.168.1.50"), name=kw.pop("name", ""),
                 vendor=kw.pop("vendor", "Acme"), kind="unknown", label=kw.pop("label", ""),
                 trusted=kw.pop("trusted", False), presence="ONLINE", ports={},
                 first_seen=kw.pop("first_seen", (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=6)).isoformat()),
                 last_seen=kw.pop("last_seen", dt.datetime.now(dt.timezone.utc).isoformat()))
    db.add(d); db.commit()
    return d


def _presence(db, t, mac, days, hours):
    now = dt.datetime.now(dt.timezone.utc)
    for d in range(days):
        for h in hours:
            ts = (now - dt.timedelta(days=d)).replace(hour=h, minute=0, second=0).isoformat()
            db.add(m.PresenceSample(tenant_id=t.id, mac=mac, ts=ts))
    db.commit()


def test_a_device_seen_every_night_reads_as_a_resident(client):
    db = SessionLocal(); t = _tenant(db, "fam-res")
    _device(db, t, "aa:aa:aa:aa:aa:01", vendor="private/randomized MAC (phone/laptop privacy)")
    _presence(db, t, "aa:aa:aa:aa:aa:01", days=6, hours=[0, 1, 2, 3, 19, 22])
    out = device_familiarity(db, t, "aa:aa:aa:aa:aa:01")
    assert out.verdict == "resident"
    assert out.days_seen == 6 and out.overnight_share >= 0.5
    assert any("recurring overnight presence" in r for r in out.reasons)
    assert out.randomized_mac is True
    assert "has recurring presence on this network" in out.summary
    db.close()


def test_a_device_seen_once_in_the_afternoon_is_a_visitor(client):
    db = SessionLocal(); t = _tenant(db, "fam-vis")
    _device(db, t, "aa:aa:aa:aa:aa:02")
    _presence(db, t, "aa:aa:aa:aa:aa:02", days=1, hours=[14, 15])
    out = device_familiarity(db, t, "aa:aa:aa:aa:aa:02")
    assert out.verdict == "visitor" and out.days_seen == 1
    assert "only been seen briefly" in out.summary
    db.close()


def test_a_daytime_regular_is_not_called_a_resident(client):
    # Three days, never overnight: comes and goes -- a cleaner's phone, a
    # neighbour's laptop. Not a stranger, not a resident either.
    db = SessionLocal(); t = _tenant(db, "fam-reg")
    _device(db, t, "aa:aa:aa:aa:aa:03")
    _presence(db, t, "aa:aa:aa:aa:aa:03", days=3, hours=[10, 11, 12])
    out = device_familiarity(db, t, "aa:aa:aa:aa:aa:03")
    assert out.verdict == "regular"
    assert any("comes and goes" in r for r in out.reasons)
    db.close()


def test_the_same_name_already_trusted_under_another_mac_is_named(client):
    # A randomized address rotating: the phone is known, the MAC is not.
    db = SessionLocal(); t = _tenant(db, "fam-sib")
    _device(db, t, "aa:aa:aa:aa:aa:04", name="iphone", trusted=True, ip="192.168.1.60")
    _device(db, t, "aa:aa:aa:aa:aa:05", name="iphone", ip="192.168.1.61",
            vendor="private/randomized MAC (phone/laptop privacy)")
    _presence(db, t, "aa:aa:aa:aa:aa:05", days=1, hours=[20])
    out = device_familiarity(db, t, "aa:aa:aa:aa:aa:05")
    assert len(out.name_siblings) == 1 and out.name_siblings[0].trusted is True
    assert any("already trusted under another MAC" in r for r in out.reasons)
    db.close()


def test_silence_is_reported_rather_than_hidden(client):
    db = SessionLocal(); t = _tenant(db, "fam-silent")
    _device(db, t, "aa:aa:aa:aa:aa:06",
            last_seen=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=4)).isoformat())
    _presence(db, t, "aa:aa:aa:aa:aa:06", days=2, hours=[21])
    out = device_familiarity(db, t, "aa:aa:aa:aa:aa:06")
    assert out.silent_days == 4
    assert any("not seen for 4 days" in r for r in out.reasons)
    db.close()


def test_dns_names_give_a_platform_hint_not_a_person(client):
    db = SessionLocal(); t = _tenant(db, "fam-dns")
    _device(db, t, "aa:aa:aa:aa:aa:07", ip="192.168.1.70")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for n in ("gateway.icloud.com", "push.apple.com", "example.org"):
        db.add(m.DnsQuery(tenant_id=t.id, ts=now, client="192.168.1.70", name=n, qtype="A",
                          blocked=False, ms=3, mac="aa:aa:aa:aa:aa:07"))
    db.commit()
    out = device_familiarity(db, t, "aa:aa:aa:aa:aa:07")
    assert out.platform_hint == "apple" and out.dns_queries == 3
    assert any("looks like a apple device" in r or "apple device" in r for r in out.reasons)
    db.close()


def test_an_unknown_mac_says_so(client):
    db = SessionLocal(); t = _tenant(db, "fam-none")
    out = device_familiarity(db, t, "00:00:00:00:00:00")
    assert out.verdict == "unknown" and "no record" in out.summary
    db.close()


def test_the_chat_tool_is_read_only_instant_and_finds_by_ip(client):
    seeded_demo(client)
    tool = registry.TOOLS["device_history"]
    assert tool.tier == 1 and tool.min_role == "viewer"
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    dev = db.query(m.Device).filter_by(tenant_id=t.id).first(); db.close()
    out = registry.dispatch(ctx_for(role="viewer"), "device_history", {"target": dev.ip})
    assert out.ok is True and out.queue_id is None
    assert out.data["mac"] == dev.mac
    assert out.text.count("\n") >= 1


def test_it_rides_on_the_device_detail_payload(client):
    seeded_demo(client)
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    dev = db.query(m.Device).filter_by(tenant_id=t.id).first(); db.close()
    body = client.get(f"/api/v1/devices/{dev.mac}").json()
    assert body["familiarity"]["mac"] == dev.mac
    assert body["familiarity"]["verdict"] in ("resident", "regular", "visitor", "unknown")
