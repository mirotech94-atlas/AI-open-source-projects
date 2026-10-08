import time

from tests.test_seed_helpers import enrol_as_tenant_admin, seeded_demo, confirmed_fix


def test_dns_payload_joins_clients_to_devices(client):
    seeded_demo(client)
    d = client.get("/api/v1/dns").json()
    assert d["status"]["running"] and d["status"]["queries_24h"] > 0
    assert d["top"] and d["blocked_top"] and d["lists"]
    assert d["clients"] and any(c["name"] and c["mac"] for c in d["clients"])
    assert d["wizard"]["resolver_ip"] and d["wizard"]["working"] is True


def test_clients_sort_survives_a_null_network_tie(client):
    """Fix round 3: two client rows tied on `-queries` with the same client
    ip, one of them carrying no `network_id` (NULL), used to 500 --
    `sorted()` compared `"lab"` (a str) against `None` on the tuple's second
    slot. Both rows must come back, and the endpoint must return 200."""
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    db = SessionLocal()
    lab = db.query(m.Network).filter_by(tenant_id=tid, slug="lab").one()
    ts = db.query(m.DnsQuery).filter_by(tenant_id=tid).order_by(m.DnsQuery.id.desc()).first().ts
    ip = "192.168.9.9"
    db.add_all([
        m.DnsQuery(tenant_id=tid, network_id=lab.id, ts=ts, client=ip,
                  name="tie.example", qtype="A", blocked=False, ms=2),
        m.DnsQuery(tenant_id=tid, network_id=None, ts=ts, client=ip,
                  name="tie.example", qtype="A", blocked=False, ms=2),
    ])
    db.commit()

    r = client.get("/api/v1/dns")
    assert r.status_code == 200, r.text
    rows = [c for c in r.json()["clients"] if c["ip"] == ip]
    assert len(rows) == 2, rows
    assert {r["network"]["slug"] if r["network"] else None for r in rows} == {"lab", None}


def test_rules_validate_normalise_and_write_one_edit(client):
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    assert client.post("/api/v1/dns/rules", json={"kind": "block", "domain": "Ads.Example.COM.",
                                                  "op": "add"}).status_code == 200
    assert client.post("/api/v1/dns/rules", json={"kind": "block", "domain": "not a domain",
                                                  "op": "add"}).status_code == 422
    db = SessionLocal()
    assert db.query(m.DnsRule).filter_by(kind="block", domain="ads.example.com").count() == 1
    e = db.query(m.Edit).filter_by(path="/api/dns/rules").order_by(m.Edit.id.desc()).first()
    assert "ads.example.com" in e.body["block"]


def test_wizard_resolver_ip_is_not_the_gateway(client):
    # The setup wizard's resolver_ip must be the sensor's own address (Task
    # 30 fix round finding 6) -- the hub/tests/fixtures/last.json fixture
    # carries a distinct dns_filter.resolver_ip ("192.168.1.50") from the
    # LAN gateway ("192.168.1.1", home.gateway) precisely so this can't
    # pass by accident.
    seeded_demo(client)
    d = client.get("/api/v1/dns").json()
    assert d["wizard"]["resolver_ip"] == "192.168.1.50"
    assert d["wizard"]["router_hint"] == "192.168.1.1"
    assert d["wizard"]["resolver_ip"] != d["wizard"]["router_hint"]


def test_wizard_clients_using_is_bounded_by_hours(client):
    # `clients_using` used to be an unbounded distinct() over every
    # DnsQuery this tenant has ever ingested -- a client whose only query
    # is well outside the requested window must not be counted.
    tid = seeded_demo(client)
    baseline = client.get("/api/v1/dns?hours=24").json()["wizard"]["clients_using"]

    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    import datetime as dt
    old_ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=72)).isoformat(timespec="seconds")
    db = SessionLocal()
    try:
        db.add(m.DnsQuery(tenant_id=tid, ts=old_ts, client="10.0.0.250", name="old-client-only.example.com",
                          qtype="A", blocked=False, list=None, ms=1))
        db.commit()
    finally:
        db.close()

    d24 = client.get("/api/v1/dns?hours=24").json()["wizard"]
    assert d24["clients_using"] == baseline  # the 72h-old client is outside the 24h window

    d96 = client.get("/api/v1/dns?hours=96").json()["wizard"]
    assert d96["clients_using"] == baseline + 1  # but visible with a wide-enough window


def test_dns_frames_are_filtered_for_non_subscribers(client):
    # Negative counterpart to test_dns_frames_only_reach_subscribers: a
    # socket that never opted in must not receive the `dns` frame at all --
    # not just "receives it late". Publishing a second, unrelated event
    # (a "queue" frame from /fix) right after the dns ingest and asserting
    # THAT is what arrives next proves the dns frame was actually dropped
    # for this connection, since a per-connection queue is FIFO: a
    # delivered dns frame would have to arrive before the queue frame, not
    # instead of it.
    tid = seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as plain:
        assert plain.receive_json()["type"] == "ready"
        H = {"Authorization": f"Bearer {enrol_as_tenant_admin(client)}"}
        client.post("/api/v1/sensor/ingest", headers=H, json={"last": {}, "state": {},
            "events": [], "history": [], "fixes": [], "dns_queries": [
                {"ts": "2026-09-09T18:31:00-04:00", "client": "192.168.1.159",
                 "name": "ads.example.com", "type": "A", "blocked": True,
                 "list": "stevenblack", "ms": 1}]})
        confirmed_fix(client, "prod", "reboot")
        # Ingest may also emit its own non-dns "scan" frame; drain frames
        # until the "queue" frame from /fix shows up, and confirm none of
        # them -- including that stretch -- was ever a "dns" frame.
        seen_types = []
        for _ in range(5):
            frame = plain.receive_json()
            seen_types.append(frame["type"])
            if frame["type"] == "queue":
                break
        assert "dns" not in seen_types
        assert "queue" in seen_types


def test_dns_frames_only_reach_subscribers(client):
    seeded_demo(client)
    with client.websocket_connect("/api/v1/ws") as plain:
        assert plain.receive_json()["type"] == "ready"
        with client.websocket_connect("/api/v1/ws") as sub:
            sub.send_json({"subscribe": ["dns"]}); assert sub.receive_json()["type"] == "ready"
            # ingest one dns query as the sensor would; the hub publishes a `dns` frame.
            H = {"Authorization": f"Bearer {enrol_as_tenant_admin(client)}"}
            client.post("/api/v1/sensor/ingest", headers=H, json={"last": {}, "state": {},
                "events": [], "history": [], "fixes": [], "dns_queries": [
                    {"ts": "2026-09-09T18:31:00-04:00", "client": "192.168.1.159",
                     "name": "ads.example.com", "type": "A", "blocked": True,
                     "list": "stevenblack", "ms": 1}]})
            assert sub.receive_json()["type"] == "dns"


def test_dns_answers_quickly_on_the_demo_tenant(client):
    # the demo tenant seeds 3000 DnsQuery rows -- this is the regression
    # guard against clients()/lists() ever losing their `hours` cutoff.
    seeded_demo(client)
    t0 = time.monotonic()
    assert client.get("/api/v1/dns").status_code == 200
    assert time.monotonic() - t0 < 2.0


def test_long_window_rollup_path_is_network_scoped(client):
    """Task 56 merge: the `?network=` filter (Task 51) and the raw-vs-rollup
    window branch (Task 55) landed on separate branches and conflicted in
    `payloads/dnsview.py`. A textual union would have left the SHORT window
    network-scoped and the LONG (rollup) one not -- a window past
    `dns_raw_days` silently reporting the whole tenant's domains for a single
    network. Both legs of `_merged_top_blocked_clients` must filter.
    """
    from guardian_hub import models as m, retention
    from guardian_hub.db import SessionLocal
    from guardian_hub.payloads import dnsview

    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(id=tid).one()
    office = db.query(m.Network).filter_by(tenant_id=tid, is_primary=True).one()
    lab = db.query(m.Network).filter_by(tenant_id=tid, slug="lab").one()

    # Two raw rows old enough to be rolled up, one per network, on distinct
    # domains, plus one recent row per network so the raw leg has something
    # inside the window too.
    # 30 days back: older than `dns_raw_days` (7) so it is rolled up, but
    # still inside the 90-day window the merged path is asked for below.
    import datetime as dt
    old_dtm = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    old_ts, old_day = old_dtm.isoformat(), old_dtm.date().isoformat()
    recent_ts = db.query(m.DnsQuery).filter_by(tenant_id=tid).order_by(m.DnsQuery.id.desc()).first().ts
    db.add_all([
        m.DnsQuery(tenant_id=tid, network_id=office.id, ts=old_ts, client="192.168.1.9",
                   name="old-office.example", qtype="A", blocked=False, ms=1),
        m.DnsQuery(tenant_id=tid, network_id=lab.id, ts=old_ts, client="10.10.0.9",
                   name="old-lab.example", qtype="A", blocked=True, ms=1),
        m.DnsQuery(tenant_id=tid, network_id=office.id, ts=recent_ts, client="192.168.1.9",
                   name="new-office.example", qtype="A", blocked=False, ms=1),
        m.DnsQuery(tenant_id=tid, network_id=lab.id, ts=recent_ts, client="10.10.0.9",
                   name="new-lab.example", qtype="A", blocked=False, ms=1),
    ])
    db.commit()

    roll_before = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).date().isoformat()
    written = retention.rollup_dns(db, tenant, before_day=roll_before)
    assert written >= 2
    rolled = db.query(m.DnsRollup).filter(m.DnsRollup.tenant_id == tid,
                                          m.DnsRollup.day == old_day).all()
    # The rollup itself must carry the attribution, or nothing downstream can
    # filter on it.
    assert {r.network_id for r in rolled} == {office.id, lab.id}

    # A window longer than `dns_raw_days` (7) takes the merged path.
    hours = 90 * 24
    assert hours > retention.policy(tenant)["dns_raw_days"] * 24

    def names(net):
        return {n.name for n in dnsview.build(db, tenant, hours=hours, net=net).top}

    all_names, office_names, lab_names = names(None), names(office), names(lab)

    # Rollup leg scoped: the other network's OLD domain is absent.
    assert "old-office.example" in office_names and "old-lab.example" not in office_names
    assert "old-lab.example" in lab_names and "old-office.example" not in lab_names
    # Raw leg scoped the same way, inside the same call.
    assert "new-office.example" in office_names and "new-lab.example" not in office_names
    assert "new-lab.example" in lab_names and "new-office.example" not in lab_names
    # Unscoped sees both.
    assert {"old-office.example", "old-lab.example",
            "new-office.example", "new-lab.example"} <= all_names

    # Client rows are scoped and carry their network ref.
    lab_clients = dnsview.build(db, tenant, hours=hours, net=lab).clients
    assert lab_clients
    assert {c.network.slug for c in lab_clients if c.network} == {"lab"}
