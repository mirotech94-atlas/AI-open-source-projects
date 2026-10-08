"""Task 77 / F4: the per-host package list and the sensor host's own updates.

Both facts have always been in `Scan.raw` (`ssh[*].updates.pending`,
`ssh[*].sec.security_updates`, `mac_updates`) and never reached a payload
field, so the reference console's two panels under the servers table had
nothing to draw. Built on one pushed scan rather than `seed_demo`, which costs
minutes -- these assertions are about what one scan turns into.
"""


from guardian_hub.payloads import updates as updates_payload
from tests.test_sensor_api import enrol
from tests.test_tenants import make_super
from tests import scanfixture



def _ingest(client, mutate=None):
    tid = make_super(client)
    tok = enrol(client, tid)
    last = scanfixture.load_last()
    state = scanfixture.load_state()
    if mutate is not None:
        mutate(last)
    r = client.post("/api/v1/sensor/ingest",
                    json={"last": last, "state": state, "events": [], "history": [],
                          "fixes": [], "dns_queries": []},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    return tid


def test_pending_packages_are_named_per_host_with_the_security_subset(client):
    def mutate(last):
        host = last["ssh"][0]
        host["updates"] = {**(host.get("updates") or {}), "count": 3,
                           "pending": ["openssl", "curl", "libc6", "curl"]}
        host["sec"] = {**(host.get("sec") or {}),
                       # `nginx` is NOT pending, so it must not be advertised.
                       "security_updates": ["openssl", "nginx"]}

    _ingest(client, mutate)
    row = client.get("/api/v1/updates").json()["hosts"][0]
    # duplicates dropped, order kept
    assert row["packages"] == ["openssl", "curl", "libc6"]
    # intersected with what is actually pending
    assert row["security_packages"] == ["openssl"]


def test_the_package_list_is_capped_and_the_true_count_still_reported(client):
    def mutate(last):
        host = last["ssh"][0]
        names = [f"pkg{i}" for i in range(updates_payload.MAX_PACKAGES + 40)]
        host["updates"] = {**(host.get("updates") or {}), "count": len(names), "pending": names}

    _ingest(client, mutate)
    row = client.get("/api/v1/updates").json()["hosts"][0]
    assert len(row["packages"]) == updates_payload.MAX_PACKAGES
    # `pending` is the honest count, so a trimmed list is visibly trimmed.
    assert row["pending"] == updates_payload.MAX_PACKAGES + 40


def test_a_host_with_nothing_pending_names_nothing(client):
    _ingest(client)
    for row in client.get("/api/v1/updates").json()["hosts"]:
        if row["pending"] == 0:
            assert row["packages"] == [] and row["security_packages"] == []


def test_the_sensor_hosts_own_updates_reach_the_payload(client):
    _ingest(client)
    mac = client.get("/api/v1/updates").json()["mac"]
    assert mac["reported"] is True
    assert mac["os"] and mac["available"] and mac["checked"]
    assert mac["error"] == ""


def test_a_scan_without_the_block_reports_not_reported_rather_than_up_to_date(client):
    """"The sensor never told us" and "there is nothing waiting" are different
    claims, and the panel must be able to tell them apart."""
    _ingest(client, lambda last: last.pop("mac_updates", None))
    mac = client.get("/api/v1/updates").json()["mac"]
    assert mac["reported"] is False and mac["available"] == []


def test_a_failed_check_carries_its_reason(client):
    _ingest(client, lambda last: last.update(
        mac_updates={"error": "softwareupdate timed out", "ts": "2026-09-09T17:00:00-04:00"}))
    mac = client.get("/api/v1/updates").json()["mac"]
    assert mac["reported"] is True and mac["error"] == "softwareupdate timed out"
    assert mac["available"] == []


def test_the_security_count_reads_the_key_the_sensor_actually_writes(client):
    """`updates.security` does not exist in the sensor payload -- the security
    list is `sec.security_updates` -- so this column read 0 for every host in
    production."""
    def mutate(last):
        host = last["ssh"][0]
        host["updates"] = {**(host.get("updates") or {}), "count": 2, "pending": ["openssl", "sudo"]}
        host["sec"] = {**(host.get("sec") or {}), "security_updates": ["openssl", "sudo"]}

    _ingest(client, mutate)
    row = client.get("/api/v1/updates").json()["hosts"][0]
    assert row["security_pending"] == 2 == len(row["security_packages"])
