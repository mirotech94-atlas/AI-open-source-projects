"""Task 28: the `/security` payload -- scorecards, Wi-Fi guard, checks with
proof, attackers with geo, and exposure tiles.

`test_security_payload` adds a `seeded_demo(client)` call the brief's own
snippet omits: without it there is no session cookie, `GET /api/v1/security`
401s, and `s["scorecards"]` KeyErrors -- every other payload test in this
repo (test_overview.py, test_assets.py, ...) seeds and signs in first, and
this one needs to as well.
"""

import time

from tests.test_seed_helpers import seeded_demo
from tests import scanfixture


def test_security_payload(client, monkeypatch):
    seeded_demo(client)
    import guardian_hub.geoip as geoip
    monkeypatch.setattr(geoip, "_fetch", lambda ip: {"country": "Turkey", "country_code": "TR",
        "city": "Istanbul", "org": "ISP", "isp": "ISP", "lat": 41.0, "lon": 29.0,
        "hosting": False, "reverse": ""})
    s = client.get("/api/v1/security").json()
    assert s["scorecards"] and all(c["grade"] in ("A", "B", "C", "D", "F") for c in s["scorecards"])
    assert s["wifi"]["state"] in ("good", "warn", "bad") and s["wifi"]["verdict"]
    assert s["checks"] and all(c["evidence"] and c["last_verified"] for c in s["checks"])
    assert all(c["autofix"] in ("auto", "manual", "none") for c in s["checks"])
    # The demo's attacker addresses are RFC 5737 documentation addresses
    # (`guardian_hub.demoscrub`), so `geoip.lookup` never calls the provider
    # for one -- the `_fetch` patch above is inert and the geo comes from the
    # `IpGeo` rows `seed._seed_attacker_geo` wrote.
    assert s["attackers"] and len(s["attackers"][0]["geo"]["country_code"]) == 2
    assert s["exposure"] and s["attacker_method"] in s["methods"]


def test_geoip_skips_private_ips_and_caches(client, monkeypatch):
    from guardian_hub import geoip, models as m
    from guardian_hub.db import SessionLocal
    calls = []
    monkeypatch.setattr(geoip, "_fetch", lambda ip: calls.append(ip) or {"country": "X"})
    db = SessionLocal()
    assert geoip.lookup(db, "192.168.1.5") is None and calls == []
    geoip.lookup(db, "8.8.8.8"); geoip.lookup(db, "8.8.8.8")
    assert calls == ["8.8.8.8"] and db.query(m.IpGeo).count() == 1


def test_attacker_method_requires_admin_and_writes_an_edit(client):
    tid = seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from tests.test_auth import login

    db = SessionLocal()
    u = m.User(username="secop", email="secop@x.com", display_name="secop")
    db.add(u); db.flush()
    db.add(m.Membership(user_id=u.id, tenant_id=tid, role="operator"))
    db.commit()

    client.post("/api/v1/auth/logout")
    login(client, "secop", "secop@x.com")
    assert client.post("/api/v1/attacker", json={"method": "block_subnet"}).status_code == 403

    client.post("/api/v1/auth/logout")
    # The public demo code opens a read-only session (audit 2026-09-28) ...
    r = client.post("/api/v1/auth/verify", json={"username": "demoadmin", "code": "424242"})
    assert r.status_code == 200, r.text
    assert client.post("/api/v1/attacker", json={"method": "block_subnet"}).status_code == 403

    # ... and the same admin signed in the ordinary way may write.
    client.post("/api/v1/auth/logout")
    from tests.test_seed_helpers import sign_in_as_demoadmin
    sign_in_as_demoadmin(client)
    assert client.post("/api/v1/attacker", json={"method": "block_subnet"}).status_code == 200
    e = SessionLocal().query(m.Edit).filter_by(path="/api/attacker").one()
    assert e.body == {"method": "block_subnet"}


def test_attacker_method_rejects_unknown_method(client):
    seeded_demo(client)
    assert client.post("/api/v1/attacker", json={"method": "nonsense"}).status_code == 422


def test_security_answers_quickly_on_the_demo_tenant(client, monkeypatch):
    import guardian_hub.geoip as geoip
    monkeypatch.setattr(geoip, "_fetch", lambda ip: None)
    seeded_demo(client)
    t0 = time.monotonic()
    assert client.get("/api/v1/security").status_code == 200
    assert time.monotonic() - t0 < 2.0


# --- Fix round 1, finding 1: catch_all is a baseline flag, not a failure ---

def test_admin_panel_catch_all_alone_passes(client):
    """Nexus parity: `catch_all` just tells the probe a single-page app
    answers 200 for every path -- only `hits` (a real signature match) is
    a failure. `hits: {}` must pass even when `catch_all` is true."""
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub.scans import latest_scan
    from guardian_hub import models as m

    db = SessionLocal()
    tid = db.query(m.Membership).first().tenant_id
    scan = latest_scan(db, tid)
    raw = dict(scan.raw)
    raw["admin_probe"] = {"Patient portal": {"catch_all": True, "hits": {}, "ts": scan.ts},
                           "Demo": {"catch_all": True, "hits": {}, "ts": scan.ts}}
    scan.raw = raw
    db.add(scan); db.commit()

    s = client.get("/api/v1/security").json()
    admin_rows = [c for c in s["checks"] if c["area"] == "Admin panels"]
    assert admin_rows and all(c["passed"] for c in admin_rows)


# --- Fix round 1, finding 2: 22/80/443 are allowed on external sites ------

def test_exposure_allows_port_22_80_443_on_external_sites(client):
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub.scans import latest_scan
    from guardian_hub import models as m

    db = SessionLocal()
    tid = db.query(m.Membership).first().tenant_id
    scan = latest_scan(db, tid)
    raw = dict(scan.raw)
    raw["external_sites"] = {"prod": {"open_ports": [22, 80, 443]}}
    scan.raw = raw
    db.add(scan); db.commit()

    s = client.get("/api/v1/security").json()
    tile = next(t for t in s["exposure"] if t["label"] == "prod open ports")
    assert tile["state"] == "ok"


def test_exposure_flags_ports_outside_the_allowed_set(client):
    seeded_demo(client)
    from guardian_hub.db import SessionLocal
    from guardian_hub.scans import latest_scan
    from guardian_hub import models as m

    db = SessionLocal()
    tid = db.query(m.Membership).first().tenant_id
    scan = latest_scan(db, tid)
    raw = dict(scan.raw)
    raw["external_sites"] = {"prod": {"open_ports": [22, 80, 443, 8080]}}
    scan.raw = raw
    db.add(scan); db.commit()

    s = client.get("/api/v1/security").json()
    tile = next(t for t in s["exposure"] if t["label"] == "prod open ports")
    assert tile["state"] == "warn"


# --- Fix round 1, findings 3+4: geo cap counts cache misses, with a ---
# --- per-request wall-clock budget so slow lookups can't stall the route --

def _add_attacker_alert(tid, host, ip, fails):
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    db = SessionLocal()
    db.add(m.Alert(tenant_id=tid, key=f"sec:attacker:{host}:{ip}", sev="WARN",
                    msg=f"{fails} failed login attempts", status="open",
                    first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-02T00:00:00+00:00"))
    db.commit(); db.close()


def test_geo_budget_counts_cache_misses_not_list_position(client, monkeypatch):
    import guardian_hub.geoip as geoip
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    import datetime as dt

    tid = seeded_demo(client)
    # 20 attackers, decreasing fails so ordering (by -fails) is deterministic;
    # the 20th (fewest fails) lands well past the old positional cap of 15.
    ips = [f"1.2.3.{i}" for i in range(1, 21)]
    for i, ip in enumerate(ips):
        _add_attacker_alert(tid, "prod", ip, fails=100 - i)
    cached_ip = ips[-1]

    db = SessionLocal()
    db.add(m.IpGeo(ip=cached_ip, country="Cachedland", country_code="CL",
                    fetched=dt.datetime.now(dt.timezone.utc)))
    db.commit()

    calls = []
    monkeypatch.setattr(geoip, "_fetch", lambda ip: calls.append(ip) or {"country": "Freshland"})

    s = client.get("/api/v1/security").json()
    by_ip = {a["ip"]: a for a in s["attackers"]}
    assert cached_ip not in calls  # the cache hit never touched the network
    assert by_ip[cached_ip]["geo"]["country"] == "Cachedland"  # ...and still got its geo


def test_geo_wall_clock_budget_stops_slow_fetches(client, monkeypatch):
    import guardian_hub.geoip as geoip

    tid = seeded_demo(client)
    ips = [f"5.6.7.{i}" for i in range(1, 11)]  # 10 uncached IPs, well under MAX_GEO_LOOKUPS
    for i, ip in enumerate(ips):
        _add_attacker_alert(tid, "prod", ip, fails=50 - i)

    def slow_fetch(ip):
        time.sleep(0.5)
        return {"country": "Slowland"}

    monkeypatch.setattr(geoip, "_fetch", slow_fetch)

    from guardian_hub.payloads import security as security_ns
    assert security_ns.GEO_BUDGET_S < 0.5 * len(ips)  # the budget must be the binding constraint

    t0 = time.monotonic()
    r = client.get("/api/v1/security")
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    # 10 * 0.5s = 5s if every fetch ran; the budget should cut it well short.
    assert elapsed < security_ns.GEO_BUDGET_S + 1.5

    s = r.json()
    geos = [a["geo"] for a in s["attackers"] if a["ip"] in ips]
    assert any(g is None for g in geos)  # the budget left at least one un-looked-up


# --------------------------------------------------------------------------
# Task 58 (G2): the three watch lists the sensor already collects
# --------------------------------------------------------------------------

def test_extra_checks_domains_and_heartbeats_reach_the_payload(client):
    """`config.checks`, the daily whois and the dead-man `Heartbeat` rows all
    existed on the sensor and in the report but never reached the dashboard."""
    import datetime as dt

    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    from guardian_hub.scans import latest_scan

    tenant_id = seeded_demo(client)
    db = SessionLocal()
    try:
        scan = latest_scan(db, tenant_id)
        scan.raw = {
            **(scan.raw or {}),
            "checks": [
                {"name": "office-vpn", "type": "tcp", "target": "10.10.0.1", "port": 51820,
                 "ok": True, "latency_ms": 12, "detail": ""},
                {"name": "nas-ping", "type": "icmp", "target": "192.168.1.20",
                 "ok": False, "latency_ms": 0, "detail": "no reply"},
                {"name": "mx-lookup", "type": "dns", "target": "mail.internal.test"},
            ],
            "domains": {
                "lab.test": {"expires": "", "days_left": None, "error": "whois timed out"},
                "example.test": {"expires": "2026-09-20", "days_left": 5},
                "other.test": {"expires": "2027-01-01", "days_left": 200},
            },
            "heartbeats": {"nightly-backup": {"note": "borg to the NAS"}},
        }
        # `ingest.store_scan` now creates a row for every heartbeat the scan
        # declares, and the seeded scan declares this one -- so pin the
        # existing row rather than inserting a second.
        existing = db.query(m.Heartbeat).filter_by(tenant_id=tenant_id, name="nightly-backup").first()
        if existing is None:
            existing = m.Heartbeat(tenant_id=tenant_id, name="nightly-backup", token="t1")
            db.add(existing)
        existing.expect_minutes = 1440
        existing.last_ping = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)
        db.add(m.Heartbeat(tenant_id=tenant_id, name="db-dump", token="t2",
                           expect_minutes=60, last_ping=None))
        db.commit()
    finally:
        db.close()

    s = client.get("/api/v1/security").json()

    # Failures sort first, so a broken probe is the first thing on the card.
    names = [c["name"] for c in s["extra_checks"]]
    assert names[0] == "nas-ping"
    assert set(names) == {"office-vpn", "nas-ping", "mx-lookup"}
    # A probe the sensor has not run must be null, not False -- "not run" and
    # "failed" are different claims.
    assert next(c for c in s["extra_checks"] if c["name"] == "mx-lookup")["ok"] is None
    assert next(c for c in s["extra_checks"] if c["name"] == "office-vpn")["port"] == 51820

    # Soonest expiry first, unknown last.
    assert [d["domain"] for d in s["domains"]] == ["example.test", "other.test", "lab.test"]
    assert s["domains"][-1]["days_left"] is None and s["domains"][-1]["error"]

    hbs = {h["name"]: h for h in s["heartbeats"]}
    assert hbs["nightly-backup"]["late"] is False
    assert hbs["nightly-backup"]["note"] == "borg to the NAS"
    assert hbs["nightly-backup"]["age_min"] == 30
    # Never pinged => late, and `age_min` stays null rather than inventing 0.
    assert hbs["db-dump"]["late"] is True and hbs["db-dump"]["age_min"] is None
    # Late rows sort first.
    assert s["heartbeats"][0]["name"] == "db-dump"


def test_watch_lists_are_lists_and_a_declared_heartbeat_reaches_the_panel(client):
    """The seeded demo payload carries `checks`, `domains` AND a heartbeat.

    That last one used to arrive as `[]`: nothing created the `Heartbeat` row
    the panel reads, so a heartbeat the sensor reported was invisible here --
    and the setup/rotate routes had nothing to address. `ingest.store_scan`
    creates the row now, so the panel shows what the sensor declared."""
    seeded_demo(client)
    s = client.get("/api/v1/security").json()
    assert [h["name"] for h in s["heartbeats"]] == ["nightly-backup"]
    assert s["heartbeats"][0]["every_min"] == 1500
    assert isinstance(s["extra_checks"], list) and isinstance(s["domains"], list)
    assert all(set(c) >= {"name", "type", "target", "ok"} for c in s["extra_checks"])


def test_a_legacy_check_map_payload_is_still_read(client):
    """An older sensor wrote the history-line shape (`{name: ok}`) rather than
    a list. Accept it instead of 500-ing the whole Security tab."""
    from guardian_hub.db import SessionLocal
    from guardian_hub.scans import latest_scan

    tenant_id = seeded_demo(client)
    db = SessionLocal()
    try:
        scan = latest_scan(db, tenant_id)
        scan.raw = {**(scan.raw or {}), "checks": {"office-vpn": True, "nas-ping": False}}
        db.commit()
    finally:
        db.close()
    rows = client.get("/api/v1/security").json()["extra_checks"]
    assert {r["name"]: r["ok"] for r in rows} == {"office-vpn": True, "nas-ping": False}


# --------------------------------------------------------------------------
# Task 77 / F8: email anti-spoofing, external ports, the fix log, the rogue box
# --------------------------------------------------------------------------

def test_email_rows_carry_the_records_and_the_same_verdict_as_the_check(client):
    """The panel and the Email checklist row for one domain must never
    disagree, so they share one verdict: SPF present and DMARC enforcing."""
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    body = client.get("/api/v1/security").json()
    rows = {r["domain"]: r for r in body["email"]}
    assert rows, "the fixture monitors two domains"
    assert list(rows) == sorted(rows), "alphabetical, so the panel never reshuffles"

    checks = {c["target"]: c for c in body["checks"] if c["key"].startswith("email:")}
    for domain, row in rows.items():
        assert row["passed"] == checks[domain]["passed"]
        assert row["checked"]
        # a policy of "none" is published but not enforcing
        if row["dmarc_policy"] == "none":
            assert row["passed"] is False


def test_external_ports_flag_only_the_unexpected_ones_and_attach_probe_hits(client):
    from guardian_hub.payloads import security as security_payload
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    rows = client.get("/api/v1/security").json()["ext_ports"]
    assert rows and list(r["host"] for r in rows) == sorted(r["host"] for r in rows)
    for row in rows:
        assert set(row["unexpected"]) <= set(row["ports"])
        for port in security_payload.DEFAULT_SITE_PORTS:
            assert port not in row["unexpected"], f"{port} is expected on a public host"
        assert row["ports"] == sorted(row["ports"])
        # the fixture's probes found nothing, so no row may invent a finding
        assert row["findings"] == []


def test_an_unexpected_port_is_named_as_unexpected(client):
    import json
    import pathlib

    from tests.test_sensor_api import enrol
    from tests.test_tenants import make_super

    tid = make_super(client)
    tok = enrol(client, tid)
    last = scanfixture.load_last()
    host = sorted(last["external_sites"])[0]
    last["external_sites"][host]["open_ports"] = [22, 443, 5432]
    last["admin_probe"] = {f"Something ({host})": {"hits": {"/wp-login.php": "WARN"}, "catch_all": False,
                                                    "ts": last["ts"]}}
    r = client.post("/api/v1/sensor/ingest",
                    json={"last": last, "state": scanfixture.load_state(), "events": [],
                          "history": [], "fixes": [], "dns_queries": []},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text

    row = next(x for x in client.get("/api/v1/security").json()["ext_ports"] if x["host"] == host)
    assert row["unexpected"] == [5432]
    assert row["findings"] == ["/wp-login.php"]


def test_the_fix_log_is_newest_first_and_capped(client):
    from guardian_hub.payloads import security as security_payload
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    rows = client.get("/api/v1/security").json()["fix_log"]
    assert rows and len(rows) <= security_payload.FIX_LOG_LIMIT
    assert [r["ts"] for r in rows] == sorted((r["ts"] for r in rows), reverse=True)
    for row in rows:
        assert row["host"] and row["action"]
        assert isinstance(row["ok"], bool)
        assert "\n" not in row["detail"], "only the first line of the command tail"


def test_the_wifi_guard_carries_the_rogue_boxs_two_extra_facts(client):
    """The reference's rogue box is rendered from the guard, so the guard has to
    carry the route in use and the MAC answering as the router."""
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    body = client.get("/api/v1/security").json()
    assert body["wifi"]["default_gateway"] == body["wifi"]["expected_gateway"]
    assert body["wifi"]["gateway_mac"], "the fixture's LAN block reports a gateway MAC"

    # A network the pushing sensor did not report on claims neither fact rather
    # than a false clean bill of health.
    others = [g for g in body["wifis"] if g["ssid"] != body["wifi"]["ssid"]]
    for guard in others:
        assert guard["default_gateway"] == "" and guard["gateway_mac"] == ""


# --------------------------------------------------------------------------
# _headers_fix_target: whether "Security headers present" is a Vault fix
# or a human errand depends on the site's config, not on the key alone --
# see remedy.py's module docstring for the bug this replaces (a host
# Vault already had SSH to, told the owner it had "no repair" for a
# header it could safely set).
# --------------------------------------------------------------------------

class _FakeTenant:
    def __init__(self, config):
        self.config = config


def test_headers_fix_target_needs_an_ssh_host_configured():
    from guardian_hub.payloads.security import _headers_fix_target
    tenant = _FakeTenant({"sites": [{"name": "blog", "url": "https://blog.example.com"}]})
    action, host, arg, why_not = _headers_fix_target(tenant, "blog", {"hsts": False, "nosniff": True, "frame": True})
    assert action == "" and host == ""
    assert "ssh" in why_not.lower() and "settings" in why_not.lower()


def test_headers_fix_target_resolves_when_ssh_host_is_configured():
    from guardian_hub.payloads.security import _headers_fix_target
    tenant = _FakeTenant({"sites": [{"name": "blog", "url": "https://blog.example.com",
                                     "ssh_host": "prod"}]})
    action, host, arg, why_not = _headers_fix_target(tenant, "blog", {"hsts": False, "nosniff": False, "frame": True})
    assert action == "security_headers" and host == "prod" and why_not == ""
    domain, _, hdrs = arg.partition("|")
    assert domain == "blog.example.com"
    assert set(hdrs.split(",")) == {"hsts", "nosniff"}


def test_headers_fix_target_never_offers_csp_or_redirect():
    """Only hsts/nosniff/frame are missing-header-safe fixes -- if the ONLY
    thing failing is CSP (or the redirect, which this check does not even
    track), Vault must say so is still a human call, never invent one."""
    from guardian_hub.payloads.security import _headers_fix_target
    tenant = _FakeTenant({"sites": [{"name": "blog", "url": "https://blog.example.com",
                                     "ssh_host": "prod"}]})
    action, host, arg, why_not = _headers_fix_target(tenant, "blog", {"hsts": True, "nosniff": True,
                                                                       "frame": True, "csp": False})
    assert action == "" and host == ""
    assert "content-security-policy" in why_not.lower() or "csp" in why_not.lower()


def test_headers_fix_target_names_csp_precisely_and_gives_a_real_next_step():
    """The two live sites this shipped for -- FlyCheap and safehome.example.com --
    are both missing only CSP, with the HTTPS redirect already in place. The
    old copy said "CSP or the redirect", an ambiguous guess the boolean
    already answered; a person reading it learns nothing they can act on."""
    from guardian_hub.payloads.security import _headers_fix_target
    tenant = _FakeTenant({"sites": [{"name": "blog", "url": "https://blog.example.com",
                                     "ssh_host": "prod"}]})
    _, _, _, why_not = _headers_fix_target(
        tenant, "blog",
        {"hsts": True, "nosniff": True, "frame": True, "csp": False, "https_redirect": True},
    )
    low = why_not.lower()
    assert "or the https" not in low, "still hedging between two things the data already answers"
    assert "content-security-policy" in low
    assert "report-only" in low, "gives no real next step for the common case"


def test_headers_fix_target_names_the_redirect_when_that_is_the_gap():
    from guardian_hub.payloads.security import _headers_fix_target
    tenant = _FakeTenant({"sites": [{"name": "blog", "url": "https://blog.example.com",
                                     "ssh_host": "prod"}]})
    _, _, _, why_not = _headers_fix_target(
        tenant, "blog",
        {"hsts": True, "nosniff": True, "frame": True, "csp": True, "https_redirect": False},
    )
    low = why_not.lower()
    assert "content-security-policy" not in low, "named CSP when CSP was fine"
    assert "301" in why_not or "redirect" in low
