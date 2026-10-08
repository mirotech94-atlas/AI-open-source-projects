"""A DNS block rate only describes the devices pointing at the resolver.

`block_pct`, `queries_24h` and `blocked_24h` are computed from the resolver's
own logs, so they say nothing at all about a device that never asks it. On a
26-device network where four are configured to use Vault's resolver,
"3.2% of lookups blocked" is a fact about those four and reads as a fact
about the network (trust plan §2.5G).

The failure is not that the number is wrong — it is right about what it
measures — but that the thing it measures is invisible. A device doing the
most concerning lookups on the network is also the one most likely to be
using its own resolver, and it shows up here as perfect silence.

So every DNS figure now ships `clients_using`, `devices_total` and
`coverage_pct` beside it, and the page can say "based on 4 of 26 devices".

NOT solved here, and worth being explicit about: this measures whether a
device asked Vault's resolver, not whether it used DNS at all. A device
on DoH or DoT looks identical to one that made no lookups. Detecting that
needs the sensor to watch for port 853 and known DoH endpoints, and until it
does, low coverage is the honest signal that the gap exists.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_read_write import seeded


def _dns(client):
    r = client.get("/api/v1/dns")
    assert r.status_code == 200, r.text
    return r.json()["status"]


def _add_lookups(tid, pairs):
    """pairs: (client_ip, mac) — one lookup each, just now."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    db = SessionLocal()
    try:
        for ip, mac in pairs:
            db.add(m.DnsQuery(tenant_id=tid, ts=now, client=ip, mac=mac,
                              name="example.com", qtype="A", blocked=False))
        db.commit()
    finally:
        db.close()


def _device_count(tid):
    db = SessionLocal()
    try:
        return db.query(m.Device).filter_by(tenant_id=tid).count()
    finally:
        db.close()


def test_the_dns_numbers_say_how_many_devices_they_describe(client):
    tid, _ = seeded(client)
    total = _device_count(tid)
    assert total > 1, "the fixture should bring devices with it"

    _add_lookups(tid, [("192.168.1.50", "aa:bb:cc:dd:ee:01"),
                       ("192.168.1.51", "aa:bb:cc:dd:ee:02")])
    st = _dns(client)
    assert st["devices_total"] == total
    assert st["clients_using"] >= 2
    assert 0 < st["coverage_pct"] < 100, "a handful of resolver users is not full coverage"


def test_coverage_is_the_ratio_it_claims_to_be(client):
    tid, _ = seeded(client)
    _add_lookups(tid, [("192.168.1.50", "aa:bb:cc:dd:ee:01")])
    st = _dns(client)
    assert st["coverage_pct"] == int(100 * st["clients_using"] / st["devices_total"])


def test_a_network_nobody_resolves_through_reads_as_zero_not_as_silence(client):
    """No lookups is the case that matters most: every DNS figure is 0, and
    without coverage a reader cannot tell "nothing was blocked" from "nothing
    was watched"."""
    tid, _ = seeded(client)
    st = _dns(client)
    assert st["clients_using"] == 0
    assert st["coverage_pct"] == 0
    assert st["devices_total"] == _device_count(tid)


def test_coverage_never_divides_by_zero_devices(client):
    """A brand-new tenant with no devices must not 500 or claim 100%."""
    from tests.test_tenants import make_super
    make_super(client)
    st = _dns(client)
    assert st["devices_total"] == 0
    assert st["coverage_pct"] == 0
