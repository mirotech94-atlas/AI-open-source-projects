"""A device that arrives in both a per-network block and the legacy flat map.

Task 52's sensor aliases the primary network's slice onto the legacy
`known_macs` key by reference, so a current sensor sends every primary-network
device twice in one payload. Both copies resolve to the same network, so
upserting both violated `UNIQUE(tenant_id, network_id, mac)` and killed the
whole scan at commit -- a live sensor stopped reporting for an hour while it
was scanning perfectly well. Ingest must take the first and skip the duplicate.
"""
from tests.test_sensor_api import enrol
from tests.test_tenants import make_super

MAC = "aa:bb:cc:dd:ee:ff"
DEV = {"ip": "192.168.1.50", "name": "printer", "vendor": "Acme", "kind": "printer"}
# Both shapes at once, exactly as a current sensor sends them.
LAST = {"ts": "2026-09-10T18:00:00+00:00", "score": {"total": 90},
        "lan": {"devices": {DEV["ip"]: MAC}}}
# The known-device map lives in `state`, not `last` -- and a current sensor
# puts the primary network's copy in both places at once.
STATE = {"lan": {
    "networks": {"primary": {"known_macs": {MAC: DEV}}},
    "known_macs": {MAC: DEV},
}}


def _push(client, H):
    return client.post("/api/v1/sensor/ingest", headers=H,
                       json={"version": 1, "last": LAST, "state": STATE, "history": []})


def test_a_device_in_both_shapes_is_counted_once(client):
    """The ingest response counts devices stored; the duplicate must not
    inflate it, and must not blow the unique constraint at commit."""
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    r = _push(client, H)
    assert r.status_code == 200, r.text
    assert r.json()["stored"]["devices"] == 1, r.json()


def test_the_scan_survives_a_repeat_push(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    assert _push(client, H).status_code == 200
    assert _push(client, H).status_code == 200
