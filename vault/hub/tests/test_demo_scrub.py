"""The `demo` tenant is publicly signed into (`demo`/`demoadmin`, fixed
code, advertised on guarder.io) and it is seeded from a real capture of the
owner's own network -- so these tests are the guard that nothing real reaches
it. `guardian_hub.demoscrub` does the rewriting; this file proves it.

Two layers, deliberately:

1. **Rule sweeps** (`test_no_real_public_address_survives_a_seed`,
   `test_no_unallowed_domain_survives_a_seed`) walk every table the demo
   tenant populates -- driven off `seed._RESET_MODELS` plus `Tenant`, so a
   table added to seeding later is swept automatically -- and fail on any
   IPv4 that is not private/loopback/documentation/an allowed resolver, or
   any domain outside `demoscrub.ALLOWED_DOMAINS`. Allow-lists, not
   deny-lists: this is the half that catches values nobody has seen yet,
   including a re-captured `last.json` from a different network.
2. **A frozen deny-list** (`test_no_known_owner_value_survives_a_seed`) of
   the exact MACs, hostnames and names today's fixture carries. Strictly
   redundant with layer 1 for the addresses, but not for the free text (a
   device label is not pattern-matchable), and it is the test that fails
   loudly and legibly if somebody weakens an allow-list.

Plus `test_a_new_real_value_in_an_unknown_field_is_still_scrubbed`, which is
the future-proofing claim stated as an executable assertion: real values in
field names this code has never heard of are still rewritten.
"""
import json

import pytest

from guardian_hub import demoscrub, models as m, seed
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


# --------------------------------------------------------------------------
# the frozen deny-list: what today's fixture actually contains
# --------------------------------------------------------------------------

#: Canary values standing in for the owner's real MACs, public addresses and
#: device/host names. The private repo this was open-sourced from kept the
#: actual deny-list here; the public copy must not, so these are inert
#: canaries and the sweep tests below still exercise their own logic.
OWNER_MACS = ("02:00:5e:10:00:01", "02:00:5e:10:00:02")

#: The owner's real public addresses: production and project VPSes, the home
#: WAN address, and the people who attacked those servers.
OWNER_IPS = ("100.65.4.4", "100.65.4.5")

#: Hostnames, domains, product names, machine names, container images, the
#: SSID, and the device labels carrying people's first names.
OWNER_STRINGS = ("acme-prod.net", "files.acme-prod.net", "ACME-HOME-WIFI", "canary-device-label")


# --------------------------------------------------------------------------
# sweeping the demo tenant
# --------------------------------------------------------------------------

#: Every table the demo seeder writes, plus `Tenant` itself -- its `name`,
#: `config` and `settings` are not tenant-scoped rows and so are not in
#: `_RESET_MODELS`. Derived from the seeder's own list, so a model added to
#: seeding is swept without editing this file.
#:
#: `IpGeo` is swept separately (see `demo_strings`): it is hub-global, so a
#: long-lived hub holds cached geo for real addresses that have nothing to do
#: with the demo tenant. Only the rows the demo actually references are
#: relevant, and those are the ones swept.
SWEPT_MODELS = seed._RESET_MODELS + (m.Tenant,)


def _cell_strings(row) -> list[str]:
    """Every column of `row` as text, JSON-encoding dict/list columns so the
    strings buried inside `Scan.raw` -- which is where most of the fixture
    actually lands -- are swept too."""
    out = []
    for column in row.__table__.columns:
        value = getattr(row, column.name, None)
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, (dict, list)):
            out.append(json.dumps(value, ensure_ascii=False))
    return out


@pytest.fixture()
def demo_strings(client):
    """Seed the demo tenant and return every string it put in the database."""
    db = SessionLocal()
    seed.seed_demo(db)
    tenant = db.query(m.Tenant).filter_by(slug="demo").one()

    strings: list[str] = []
    for model in SWEPT_MODELS:
        query = db.query(model)
        query = query.filter_by(slug="demo") if model is m.Tenant else query.filter_by(tenant_id=tenant.id)
        for row in query.all():
            strings += _cell_strings(row)
    assert strings, "swept nothing -- the seeder or SWEPT_MODELS is wrong"

    # Only the hub-global geo rows this tenant's data points at.
    blob = "\n".join(strings)
    for row in db.query(m.IpGeo).all():
        if row.ip and row.ip in blob:
            strings += _cell_strings(row)
    return strings


def _offenders(strings):
    """`find_real_values` returns both classes it knows about; split them so a
    failure names the right thing."""
    bad = set(demoscrub.find_real_values(strings))
    ips = sorted(v for v in bad if not demoscrub.ip_is_allowed(v))
    domains = sorted(v for v in bad - set(ips) if not demoscrub.domain_is_allowed(v))
    return ips, domains


def test_no_real_public_address_survives_a_seed(demo_strings):
    ips, _ = _offenders(demo_strings)
    assert ips == [], f"real-looking public addresses in demo data: {ips}"


def test_no_unallowed_domain_survives_a_seed(demo_strings):
    # The guard has to be able to fail: a real domain planted in the swept
    # strings must come back in the `domains` half, not vanish between the
    # two branches of `_offenders`.
    assert _offenders(["see https://vpn.evil-corp.co.uk/login"])[1] == ["vpn.evil-corp.co.uk"]

    _, domains = _offenders(demo_strings)
    assert domains == [], f"unallowed domains in demo data: {domains}"


def test_no_known_owner_value_survives_a_seed(demo_strings):
    blob = "\n".join(demo_strings)
    found = [token for token in OWNER_MACS + OWNER_IPS + OWNER_STRINGS if token in blob]
    assert found == [], f"the owner's own values reached the demo tenant: {found}"


def test_the_demo_is_still_a_busy_network(client):
    """The scrub must not quietly shrink the shop window: same device count,
    both networks, and the Lab still holds the devices `split_networks` moves
    (its `LAB_MACS` are scrubbed MACs, so a mismatch here is how a broken
    mapping would show up)."""
    db = SessionLocal()
    out = seed.seed_demo(db)
    tenant = db.query(m.Tenant).filter_by(slug="demo").one()
    # 3, not 2: the demo's own recorder (Task 83's camera census fixture) owns a third network
    assert out["devices"] >= 20 and out["networks"] == 3
    assert out["dns"] == 3000 and out["events"] == 40

    lab = db.query(m.Network).filter_by(tenant_id=tenant.id, slug="lab").one()
    lab_devices = db.query(m.Device).filter_by(tenant_id=tenant.id, network_id=lab.id).all()
    # the five moved devices plus the Lab's own synthetic gateway
    assert len(lab_devices) == len(seed.LAB_MACS) + 1
    assert set(seed.LAB_MACS) <= {d.mac for d in lab_devices}
    assert all(d.ip.startswith(seed.LAB_PREFIX + ".") for d in lab_devices)


def test_attackers_keep_a_country_for_the_map(client):
    """Documentation-range addresses are `is_private` to `ipaddress`, so
    `geoip.lookup` would never geolocate one -- the seeder writes the `IpGeo`
    rows itself. Without this the Security tab's attacker map goes blank,
    which would reduce what the demo shows."""
    seeded_demo(client)
    body = client.get("/api/v1/security").json()
    attackers = body["attackers"]
    assert attackers, "the demo has no attackers to show"
    assert all(demoscrub.ip_is_allowed(a["ip"]) for a in attackers)
    assert any((a.get("geo") or {}).get("country") for a in attackers)


# --------------------------------------------------------------------------
# the rules themselves
# --------------------------------------------------------------------------

def test_map_ip_never_returns_a_real_public_address():
    samples = list(OWNER_IPS) + ["1.2.3.4", "203.0.114.1", "185.199.108.153", "13.107.42.14"]
    for ip in samples:
        mapped = demoscrub.map_ip(ip)
        assert demoscrub.ip_is_allowed(mapped), f"{ip} -> {mapped}"
        if ip not in demoscrub.ALLOWED_PUBLIC_IPS:
            assert demoscrub.is_doc_ip(mapped), f"{ip} -> {mapped} is not a documentation address"


def test_map_ip_keeps_private_and_allowed_addresses():
    for ip in ("192.168.1.1", "10.10.0.202", "172.16.4.9", "127.0.0.1",
               "1.1.1.1", "8.8.8.8", "192.0.2.7"):
        assert demoscrub.map_ip(ip) == ip


def test_map_mac_keeps_the_vendor_prefix_and_changes_the_device():
    for mac in OWNER_MACS:
        mapped = demoscrub.map_mac(mac)
        assert mapped != mac
        assert mapped.split(":")[:3] == mac.split(":")[:3]
        assert demoscrub.map_mac(mac) == mapped          # deterministic


def test_map_host_produces_only_unresolvable_example_names():
    for host in ("status.acme-prod.net", "files.acme-prod.net", "acme-prod.net",
                 "something.brand-new-domain.co.uk", "a.b.c.internal-thing.io"):
        mapped = demoscrub.map_host(host)
        assert mapped.endswith(".example"), mapped
        assert demoscrub.domain_is_allowed(mapped)


def test_scrub_is_deterministic():
    def scrubbed():
        last = json.loads((seed._FIXTURES / "last.json").read_text())
        state = json.loads((seed._FIXTURES / "state.json").read_text())
        demoscrub.scrub(last, state)
        return json.dumps([last, state], ensure_ascii=False)

    assert scrubbed() == scrubbed()


def test_a_new_real_value_in_an_unknown_field_is_still_scrubbed():
    """The reason this is a load-time transform rather than a one-off
    sanitise of the fixture: values the code has never seen, in fields it has
    never heard of, are still rewritten."""
    last = {
        "ts": "2026-09-09T18:30:26-04:00",
        "some_new_feature": {
            "endpoint": "https://vpn.realcompany.co.uk/login",
            "peers": ["203.0.114.9", "8.8.8.8", "192.168.1.44"],
            "gw_mac": "aa:bb:cc:11:22:33",
        },
        "sites": [{"name": "Corporate CRM (crm.realcompany.co.uk)", "url": "https://crm.realcompany.co.uk/"}],
    }
    state = {"lan": {"known_macs": {"aa:bb:cc:11:22:33": {"ip": "192.168.1.44", "name": "ceo-laptop"}}}}
    demoscrub.scrub(last, state)

    blob = json.dumps([last, state])
    for token in ("realcompany.co.uk", "203.0.114.9", "aa:bb:cc:11:22:33", "ceo-laptop", "Corporate CRM"):
        assert token not in blob, f"{token} survived the scrub"
    assert demoscrub.find_real_values(list(demoscrub._each_string(last))
                                      + list(demoscrub._each_string(state))) == []
    # untouched: an allowed resolver and a private address stay themselves
    assert "8.8.8.8" in blob and "192.168.1.44" in blob
