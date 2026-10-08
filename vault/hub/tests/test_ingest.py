
from tests.test_sensor_api import enrol
from tests.test_tenants import make_super
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from tests import scanfixture



def test_store_scan_tables(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    # Real history-line shape: dict-keyed sites/hosts (not the list shape),
    # sites keyed by name -> {ok, lat (seconds), cert (days left)}, hosts
    # keyed by name -> {disk, mem, load, failed}.
    hist = [{
        "ts": last["ts"],
        "sites": {s["name"]: {"ok": s.get("ok"), "lat": s.get("latency_s"), "cert": s.get("cert_days_left")} for s in last["sites"]},
        "hosts": {h["name"]: {"disk": h.get("disk_pct"), "mem": h.get("mem_pct"), "load": h.get("load1"), "failed": h.get("failed_ssh_24h")} for h in last["ssh"]},
        "lan": 9,
        "live_macs": sorted(set(last["lan"]["devices"].values())),
        "dns_ms": 12,
        "speed": None,
        "score": last["score"]["total"],
        "alerts": 0,
        "checks": 0,
        "incidents": 0,
        "presence": {},
        "wan_path": [],
    }]
    ev = [{"ts": last["ts"], "type": "join", "mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.250", "vendor": "Test"}]
    dq = [{"ts": last["ts"], "client": "192.168.1.1", "name": "example.com", "type": "A", "blocked": False}]
    r = client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": ev, "history": hist, "fixes": [], "dns_queries": dq}, headers=H)
    assert r.status_code == 200
    db = SessionLocal()
    assert db.query(m.Scan).count() == 1
    assert db.query(m.Alert).count() == len(state["alerts_db"])
    assert db.query(m.Device).filter_by(tenant_id=tid).count() == len(state["lan"]["known_macs"])
    assert db.query(m.Event).count() == 1
    assert db.query(m.Metric).filter_by(name="latency_ms").count() == len(last["sites"])
    assert db.query(m.Recommendation).count() == len(last["recommendations"])
    # history live_macs became presence samples, and dns rows carry the mac
    assert db.query(m.PresenceSample).count() == sum(len(h.get("live_macs") or []) for h in hist)
    ip2mac = {ip: mac for ip, mac in (last["lan"]["devices"] or {}).items()}
    for row in db.query(m.DnsQuery).all():
        assert row.mac == ip2mac.get(row.client)
    # idempotent on re-ingest of the same scan ts
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    assert db.query(m.Scan).count() == 1


def test_ingest_bad_token_401(client):
    tid = make_super(client); enrol(client, tid)
    last = scanfixture.load_last(); state = scanfixture.load_state()
    r = client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


def test_store_scan_alert_status_from_state(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    # every alert's status must come from state.alerts_db[*].state, never left unset
    statuses = {a.status for a in db.query(m.Alert).filter_by(tenant_id=tid).all()}
    expected = {v.get("state") or "open" for v in state["alerts_db"].values()}
    assert statuses == expected


def test_store_scan_rec_decision_override_from_state(client):
    # state.rec_decisions[key] must win over whatever decision/status the
    # recommendation itself carries (untested merge branch).
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    key = last["recommendations"][0]["key"]
    state["rec_decisions"] = {key: {"decision": "no", "by": "miro", "ts": last["ts"], "status": ""}}
    r = client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first()
    assert rec.decision == "no"
    assert rec.decided_by == "miro"


def test_ingest_fills_in_a_missing_action_from_the_remedy_catalogue(client):
    # The sensor sends `action: null` for most recommendations -- some of
    # those genuinely have no automatic repair (SPF/DMARC lives at a DNS
    # provider Vault has no login to), but some ARE runnable through a
    # fix the hub already knows from `remedy.fix_for_key` (the same table
    # that powers an alert row's own Fix button). `docker:{h}:{c}` clears
    # via `restart_container` there, so a recommendation carrying that key
    # with no action of its own must come back from ingest with one filled
    # in -- without alerts.py ever having to know that mapping itself.
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    last["recommendations"] = last["recommendations"] + [
        {"key": "docker:prod:web", "priority": "High",
         "text": "prod: container web is unhealthy", "why": "", "action": None},
        # A key with no remedy at all must stay None -- never invent one.
        {"key": "hb:nightly-backup", "priority": "Medium",
         "text": "Heartbeat 'nightly-backup' stopped arriving", "why": "", "action": None},
    ]
    r = client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    docker_rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key="docker:prod:web").first()
    assert docker_rec.action == {"host": "prod", "action": "restart_container", "arg": "web"}
    hb_rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key="hb:nightly-backup").first()
    assert hb_rec.action is None


def test_ingest_never_overrides_an_action_the_sensor_already_sent(client):
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    # `disk:{h}` also resolves via the remedy catalogue (cleanup_disk), but
    # the sensor already sent its own action for this key -- ingest must
    # not second-guess it.
    last["recommendations"] = last["recommendations"] + [
        {"key": "disk:prod", "priority": "Medium", "text": "prod: free disk space", "why": "",
         "action": {"host": "prod", "action": "cleanup_disk", "arg": "custom"}},
    ]
    r = client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    assert r.status_code == 200, r.text
    rec = SessionLocal().query(m.Recommendation).filter_by(tenant_id=tid, key="disk:prod").first()
    assert rec.action == {"host": "prod", "action": "cleanup_disk", "arg": "custom"}


def test_store_scan_incident_merge_by_id(client):
    # state.incidents (dict, keyed by an internal composite string) and
    # last.incidents (list, richer fields) must merge into ONE Incident row
    # keyed by the shared `id` field (untested merge branch).
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    state["incidents"] = {"host:demo|2026-09-09": {"id": 101, "opened": "2026-09-09T11:36:38-04:00"}}
    last["incidents"] = [{
        "id": 101, "entity": "demo", "sev": "WARN", "story": "SSH attack",
        "actions": ["blocked"], "alerts": ["sec:attacker:demo"],
    }]
    r = client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    incs = db.query(m.Incident).filter_by(tenant_id=tid).all()
    assert len(incs) == 1
    inc = incs[0]
    assert inc.iid == "101"
    assert inc.entity == "demo"
    assert inc.story == "SSH attack"
    assert inc.alert_keys == ["sec:attacker:demo"]


def test_store_scan_replay_does_not_duplicate_appended_rows(client):
    # Replaying the exact same payload twice (e.g. a retried POST) must not
    # duplicate Event/Fix/DnsQuery rows, and must not grow Metric counts.
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    ts = "2026-09-09T12:00:00Z"
    payload = {
        "last": {"ts": ts, "score": {"total": 90}},
        "state": {},
        "events": [{"ts": ts, "type": "join", "mac": "aa:bb:cc:dd:ee:ee", "ip": "192.168.1.251"}],
        "history": [{"ts": ts, "sites": {}, "hosts": {}, "lan": 5}],
        "fixes": [{"ts": ts, "host": "h1", "action": "reboot", "arg": None, "ok": True, "by": "auto"}],
        "dns_queries": [{"ts": ts, "client": "192.168.1.10", "name": "example.com", "type": "A", "blocked": False}],
    }
    r1 = client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert r1.status_code == 200, r1.text
    db = SessionLocal()
    assert db.query(m.Event).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.Fix).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.DnsQuery).filter_by(tenant_id=tid).count() == 1
    metric_count_1 = db.query(m.Metric).filter_by(tenant_id=tid).count()

    r2 = client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert r2.status_code == 200, r2.text
    assert db.query(m.Event).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.Fix).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.DnsQuery).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.Metric).filter_by(tenant_id=tid).count() == metric_count_1

    # the "stored" counters on the second ingest must reflect 0 new rows
    assert r2.json()["stored"]["events"] == 0
    assert r2.json()["stored"]["fixes"] == 0
    assert r2.json()["stored"]["dns"] == 0
    assert r2.json()["stored"]["metrics"] == 0


def test_history_dns_ms_and_wan_path_real_shapes(client):
    # Real sensor history lines carry dns_ms as {"system": .., "cloudflare":
    # .., "google": ..} and wan_path as {name: {"avg": .., "loss": ..}, ...}
    # -- not the bare-number / bare-list shapes a first draft might assume.
    # The old `float(h["dns_ms"])` raised on the dict shape, 500ing every
    # real push; wan_path wasn't stored at all.
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    hist = [{
        "ts": last["ts"],
        "dns_ms": {"system": 24, "cloudflare": 25, "google": 32},
        "wan_path": {
            "cloudflare": {"avg": 10.3, "loss": 0.0},
            "google": {"avg": 12.1, "loss": 0.0},
            "safehome": {"avg": 8.4, "loss": 1.5},
        },
    }]
    r = client.post(
        "/api/v1/sensor/ingest",
        json={"last": last, "state": state, "events": [], "history": hist, "fixes": [], "dns_queries": []},
        headers=H,
    )
    assert r.status_code == 200, r.text

    db = SessionLocal()

    def metric_value(name):
        row = db.query(m.Metric).filter_by(tenant_id=tid, name=name, entity="").first()
        assert row is not None, f"missing metric {name}"
        return row.value

    assert metric_value("dns_ms") == 24
    assert metric_value("dns_ms_system") == 24
    assert metric_value("dns_ms_cloudflare") == 25
    assert metric_value("dns_ms_google") == 32

    assert metric_value("path_ms_cloudflare") == 10.3
    assert metric_value("path_loss_cloudflare") == 0.0
    assert metric_value("path_ms_google") == 12.1
    assert metric_value("path_ms_safehome") == 8.4
    assert metric_value("path_loss_safehome") == 1.5


def test_history_dns_ms_resolver_can_be_none(client):
    # A resolver probe that failed comes back as None (not a number) --
    # met() must skip it silently instead of choking on float(None).
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    hist = [{"ts": last["ts"], "dns_ms": {"system": 24, "cloudflare": None}}]
    r = client.post(
        "/api/v1/sensor/ingest",
        json={"last": last, "state": state, "events": [], "history": hist, "fixes": [], "dns_queries": []},
        headers=H,
    )
    assert r.status_code == 200, r.text
    db = SessionLocal()
    assert db.query(m.Metric).filter_by(tenant_id=tid, name="dns_ms_cloudflare").count() == 0
    assert db.query(m.Metric).filter_by(tenant_id=tid, name="dns_ms_system").first().value == 24


def test_malformed_history_line_is_skipped_not_fatal(client):
    # One garbage history line must not fail the whole ingest -- it's
    # counted in stored["skipped"], and rows for the other (valid) line
    # are still stored.
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    hist = [
        "this-is-not-a-dict-and-has-no-.get-method",
        {"ts": last["ts"], "score": 87},
    ]
    r = client.post(
        "/api/v1/sensor/ingest",
        json={"last": last, "state": state, "events": [], "history": hist, "fixes": [], "dns_queries": []},
        headers=H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"]["skipped"] == 1
    db = SessionLocal()
    assert db.query(m.Metric).filter_by(tenant_id=tid, name="score", entity="").first().value == 87


def test_history_line_with_none_host_keeps_good_host_and_global_metrics(client):
    # A host probe that failed entirely comes back as `None` (not a dict).
    # That must not crash/skip the whole history line -- the good host's
    # metrics AND the line's global metrics (dns_ms/score/live_devices)
    # must still be stored, and out["skipped"] stays 0.
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    hist = [{
        "ts": last["ts"],
        "sites": {"good-site": {"ok": True, "lat": 0.05, "cert": 30}, "bad-site": None},
        "hosts": {"good-host": {"disk": 50, "mem": 60, "load": 1.0, "failed": 0}, "bad-host": None},
        "lan": 5,
        "dns_ms": 20,
        "score": 91,
    }]
    r = client.post(
        "/api/v1/sensor/ingest",
        json={"last": last, "state": state, "events": [], "history": hist, "fixes": [], "dns_queries": []},
        headers=H,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"]["skipped"] == 0

    db = SessionLocal()

    def metric_value(name, entity=""):
        row = db.query(m.Metric).filter_by(tenant_id=tid, name=name, entity=entity).first()
        assert row is not None, f"missing metric {name}/{entity}"
        return row.value

    assert metric_value("disk_pct", "good-host") == 50
    assert metric_value("mem_pct", "good-host") == 60
    assert metric_value("load", "good-host") == 1.0
    assert metric_value("failed_ssh", "good-host") == 0
    assert db.query(m.Metric).filter_by(tenant_id=tid, name="disk_pct", entity="bad-host").count() == 0

    assert metric_value("latency_ms", "good-site") == 50.0
    assert metric_value("up", "good-site") == 1
    assert metric_value("cert_days", "good-site") == 30
    assert db.query(m.Metric).filter_by(tenant_id=tid, name="up", entity="bad-site").count() == 0

    assert metric_value("dns_ms") == 20
    assert metric_value("score") == 91
    assert metric_value("live_devices") == 5


def test_incident_story_list_is_flattened(client):
    from guardian_hub.ingest import _story_text
    assert _story_text(None) == ""
    assert _story_text("plain") == "plain"
    assert _story_text([{"ts": "t1", "sev": "WARN", "text": "joined"}, {"ts": "t2", "text": "telnet"}]) == "t1 WARN joined\nt2 telnet"
    assert _story_text({"a": 1}) == '{"a": 1}'


def test_presence_samples_are_idempotent_and_dedup_within_a_batch(client):
    # Two history lines sharing a ts, a mac repeated inside one line, and a
    # full replay of the push: PresenceSample has a (tenant, ts, mac) unique
    # constraint and SessionLocal is autoflush=False, so the in-batch guard --
    # not just the "already in the DB" query -- is what keeps this from
    # failing at commit.
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    ts = "2026-09-09T13:00:00Z"
    payload = {
        "last": {"ts": ts, "score": {"total": 90}},
        "state": {},
        "events": [], "fixes": [], "dns_queries": [],
        "history": [
            {"ts": ts, "sites": {}, "hosts": {}, "lan": 2, "live_macs": ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:01"]},
            {"ts": ts, "sites": {}, "hosts": {}, "lan": 2, "live_macs": ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]},
        ],
    }
    r1 = client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert r1.status_code == 200, r1.text
    assert r1.json()["stored"]["presence"] == 2
    r2 = client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert r2.status_code == 200, r2.text
    assert r2.json()["stored"]["presence"] == 0
    db = SessionLocal()
    assert db.query(m.PresenceSample).filter_by(tenant_id=tid).count() == 2


def test_replay_of_ts_less_history_and_dns_lines_does_not_duplicate(client):
    # A history/dns line with no own "ts" falls back to the top-level scan
    # ts (`h.get("ts") or ts`) for its dedup KEY. The preload bounds used to
    # be computed from the raw (possibly-missing) `h.get("ts")` instead of
    # that same effective timestamp, so a ts-less line's key never matched
    # anything in the preloaded set and a replay re-inserted it every time.
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    ts = "2026-09-09T14:00:00Z"
    payload = {
        "last": {"ts": ts, "score": {"total": 90}},
        "state": {},
        "events": [], "fixes": [],
        "history": [
            {"sites": {"example.com": {"ok": True, "lat": 0.1, "cert": 30}}, "hosts": {}, "lan": 1,
             "live_macs": ["aa:bb:cc:dd:ee:03"]},
        ],
        "dns_queries": [
            {"client": "192.168.1.5", "name": "example.com", "type": "A", "blocked": False},
        ],
    }
    r1 = client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert r1.status_code == 200, r1.text
    assert r1.json()["stored"]["presence"] == 1
    assert r1.json()["stored"]["dns"] == 1
    r2 = client.post("/api/v1/sensor/ingest", json=payload, headers=H)
    assert r2.status_code == 200, r2.text
    assert r2.json()["stored"]["presence"] == 0
    assert r2.json()["stored"]["dns"] == 0
    db = SessionLocal()
    assert db.query(m.PresenceSample).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.DnsQuery).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.Metric).filter_by(tenant_id=tid, name="up", entity="example.com").count() == 1


def test_ingest_creates_the_primary_network_and_backfills(client):
    """The state a live single-network deployment upgrades from: rows that
    predate the `network` table land wholly on the primary on the first push,
    and the primary's facts come from the config/scan rather than being
    invented (Task 51)."""
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    hist = [{"ts": last["ts"], "sites": {}, "hosts": {}, "lan": 3,
             "live_macs": sorted(set(last["lan"]["devices"].values()))}]
    dq = [{"ts": last["ts"], "client": "192.168.1.1", "name": "example.com", "type": "A", "blocked": False}]

    db = SessionLocal()
    # a pre-existing row with no network (what the migration's backfill covers,
    # and what this ingest-side backfill covers for anything created since)
    db.add(m.Device(tenant_id=tid, mac="aa:bb:cc:dd:ee:ff", ip="192.168.1.240"))
    db.add(m.PresenceSample(tenant_id=tid, ts="2026-01-01T00:00:00", mac="aa:bb:cc:dd:ee:ff"))
    db.commit()

    r = client.post("/api/v1/sensor/ingest",
                    json={"last": last, "state": state, "events": [], "history": hist,
                          "fixes": [], "dns_queries": dq}, headers=H)
    assert r.status_code == 200, r.text
    net = db.query(m.Network).filter_by(tenant_id=tid, is_primary=True).one()
    assert net.subnet_prefix == "192.168.1" and net.expected_gateway == "192.168.1.1"
    assert net.ssid == "Northwind-Staff" and net.status == "active"
    assert net.sensor_id is not None
    assert db.query(m.Device).filter_by(tenant_id=tid, network_id=None).count() == 0
    assert db.query(m.PresenceSample).filter_by(tenant_id=tid, network_id=None).count() == 0
    for row in db.query(m.DnsQuery).all():
        assert row.network_id == net.id
    # LAN alert keys arrived in the legacy shape and were namespaced on the way in
    lan_keys = [a.key for a in db.query(m.Alert).filter(m.Alert.key.like("lan:%")).all()]
    assert lan_keys and all(k.startswith(f"lan:{net.slug}:") for k in lan_keys)
    # every incident carries the reporting sensor
    assert all(i.sensor_id == net.sensor_id for i in db.query(m.Incident).all())


def test_a_second_push_does_not_create_a_second_primary(client):
    """`ensure_primary` matches on `is_primary`, never on the config-derived
    slug -- otherwise a tenant migrated with slug `primary` would grow a second
    network on its very next push."""
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    db = SessionLocal()
    db.add(m.Network(tenant_id=tid, name="Home network", slug="primary", kind="home",
                     subnet_prefix="192.168.1", is_primary=True, status="no-sensor"))
    db.commit()
    for _ in range(2):
        r = client.post("/api/v1/sensor/ingest",
                        json={"last": last, "state": state, "events": [], "history": [],
                              "fixes": [], "dns_queries": []}, headers=H)
        assert r.status_code == 200, r.text
    rows = db.query(m.Network).filter_by(tenant_id=tid).all()
    assert len(rows) == 1 and rows[0].slug == "primary" and rows[0].status == "active"


def test_a_scan_does_not_wipe_a_decision_the_dashboard_just_made(client):
    """The owner's report: "when I press resolve now they are not working at
    all, it says done but I dont see anything being queued or done".

    Pressing Yes queues a real job and sets the row to `queued`/`needs-you`.
    The sensor learns of that decision one round-trip later and stores it with
    a hardcoded empty `status` (`sync.py`), so the next scan -- about six
    minutes after the click -- used to echo that blank straight over the hub's
    own answer. The job had really run, but every trace of it on the
    recommendation was gone, so the screen looked like nothing had happened.
    """
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    key = last["recommendations"][0]["key"]
    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first()
    rec.decision, rec.decided_by, rec.status = "yes", "miro", "queued"
    db.commit()

    # The next scan: the sensor has not pulled the edit yet, so it knows
    # nothing about this decision at all.
    state["rec_decisions"] = {}
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first()
    assert (rec.decision, rec.decided_by, rec.status) == ("yes", "miro", "queued")

    # And the scan after it, once the sensor HAS pulled the edit -- which it
    # stores with an empty status of its own.
    state["rec_decisions"] = {key: {"decision": "yes", "by": "miro", "ts": last["ts"], "status": ""}}
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first()
    assert rec.status == "queued", "the sensor's blank status overwrote the hub's own"


def test_the_sensor_still_reports_the_outcome_of_a_fix_it_ran(client):
    """The other side of that rule: a status the sensor genuinely holds must
    still win. `fix.py` marks an accepted recommendation "done"/"failed" once
    it has actually run it, and the hub has no other way to learn that."""
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    key = last["recommendations"][0]["key"]
    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first()
    rec.decision, rec.decided_by, rec.status = "yes", "miro", "queued"
    db.commit()

    state["rec_decisions"] = {key: {"decision": "yes", "by": "miro", "ts": last["ts"], "status": "done"}}
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first()
    assert rec.status == "done"


def test_a_scan_in_flight_does_not_undo_a_resolve_made_while_it_ran(client):
    """The owner's report, from the alert side: resolve an alert and watch it
    come straight back open.

    A scan samples the network, takes minutes to finish, and is ingested when
    it arrives -- so its snapshot can predate a click made while it was still
    running. The sensor could not have known about that click, and writing its
    stale `state` through undid it.
    """
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    db = SessionLocal()
    key = db.query(m.Alert).filter_by(tenant_id=tid).first().key
    assert client.post(f"/api/v1/alerts/{key}/resolve").status_code == 200
    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid, key=key).first().status == "resolved"

    # The very same scan payload arrives again: sampled before the click, so
    # it still reports the alert open.
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid, key=key).first().status == "resolved", (
        "a scan older than the click must not undo it"
    )


def test_a_scan_taken_after_the_click_is_authoritative_again(client):
    """The stale-guard is about ordering, not about pinning the hub's answer
    forever: once the sensor has seen the op and reports a newer scan, its
    state is the truth again (that is how an alert legitimately re-fires)."""
    import datetime as dt

    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    # An alert the sensor itself reports as open, so "open" after the second
    # ingest really is the sensor's word and not the fixture's.
    raw = next(k for k, v in state["alerts_db"].items() if (v.get("state") or "open") == "open")
    db = SessionLocal()
    key = db.query(m.Alert).filter_by(tenant_id=tid).filter(m.Alert.key.endswith(raw.split(":")[-1])).first().key
    client.post(f"/api/v1/alerts/{key}/resolve")

    # A scan sampled well after the click, still reporting it open: a real
    # re-fire, which must land.
    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(timespec="seconds")
    last["ts"] = later
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid, key=key).first().status == "open"


def test_a_recommendation_the_sensor_stopped_making_is_cleared(client):
    """The owner's `dns:system` case.

    "System DNS resolver is failing" kept asking to be dealt with two days
    after the alert behind it had resolved on both sides. A recommendation is
    derived fresh from the current findings every sweep -- the sensor only
    emits one while its alert is open -- but this function upserted whatever
    arrived and never removed anything, so a suggestion outlived its cause
    forever. The hub was holding 18 while the sensor emitted 5.
    """
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    db = SessionLocal()
    keys = {r.key for r in db.query(m.Recommendation).filter_by(tenant_id=tid).all()}
    assert len(keys) >= 2, "fixture needs a couple of recommendations"
    dropped = sorted(keys)[0]

    # The next sweep no longer suggests that one -- its condition cleared.
    last["recommendations"] = [r for r in last["recommendations"] if r.get("key") != dropped]
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    db = SessionLocal()
    left = {r.key for r in db.query(m.Recommendation).filter_by(tenant_id=tid).all()}
    assert dropped not in left, "a recommendation must not outlive the finding that produced it"
    assert left == keys - {dropped}, "only the one that stopped being suggested goes"


def test_an_answered_recommendation_is_cleared_too_once_its_cause_is_gone(client):
    """Even one the owner said yes to. `needs-you` deliberately keeps a row in
    the open list, which is right while the thing is still true -- and exactly
    what pinned `dns:system` to the screen once it was not."""
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid).first()
    key = rec.key
    rec.decision, rec.status = "yes", "needs-you"
    db.commit()

    last["recommendations"] = [r for r in last["recommendations"] if r.get("key") != key]
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    db = SessionLocal()
    assert db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first() is None


def test_a_payload_with_no_recommendations_clears_nothing(client):
    """A push carrying none is far likelier to be partial or malformed than a
    genuine "nothing to suggest", and wiping the table on one would be worse
    than keeping a stale row."""
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    before = db.query(m.Recommendation).filter_by(tenant_id=tid).count()
    assert before > 0

    last["recommendations"] = []
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)
    db = SessionLocal()
    assert db.query(m.Recommendation).filter_by(tenant_id=tid).count() == before


def test_a_declined_recommendation_survives_the_prune(client):
    """"No thanks" hides a recommendation rather than deleting it, and the row
    carries the "Bring this back" undo. Clearing it when its cause went away
    would take the undo with it."""
    tid = make_super(client); tok = enrol(client, tid); H = {"Authorization": f"Bearer {tok}"}
    last = scanfixture.load_last(); state = scanfixture.load_state()
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    db = SessionLocal()
    rec = db.query(m.Recommendation).filter_by(tenant_id=tid).first()
    key = rec.key
    rec.decision = "no"
    db.commit()

    last["recommendations"] = [r for r in last["recommendations"] if r.get("key") != key]
    client.post("/api/v1/sensor/ingest", json={"last": last, "state": state, "events": [], "history": [], "fixes": [], "dns_queries": []}, headers=H)

    db = SessionLocal()
    assert db.query(m.Recommendation).filter_by(tenant_id=tid, key=key).first() is not None
