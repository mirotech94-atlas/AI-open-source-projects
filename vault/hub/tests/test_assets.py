"""Task 19: the Assets payloads (sites, hosts, devices, device drawer,
screen time), all built against the 30-day demo tenant."""

from guardian_hub import demoscrub, schemas_dash as sd
from tests.querycount import queries_for
from tests.test_seed_helpers import seeded_demo

# The demo tenant is scrubbed on the way in (`guardian_hub.demoscrub`), so
# these tests name the fixture's own values and map them the same way the
# seeder does rather than hard-coding the fictional replacements.
CAMERA_MAC = demoscrub.map_mac("ec:71:db:05:5a:aa")
ROUTER_MAC = demoscrub.map_mac("58:96:71:61:05:e3")
PROD_SITE = demoscrub.map_site_name("Roots prod (status.example.org)")

GROUPS = {"Cameras", "Phones & tablets", "Computers", "TVs & media",
          "Smart home", "Printers & appliances", "Network gear", "Unknown"}


def test_sites_hosts_devices(client):
    seeded_demo(client)
    s = client.get("/api/v1/sites").json()["sites"]
    assert s and 0 <= s[0]["uptime_7d"] <= 100 and len(s[0]["latency_spark"]) > 1
    h = client.get("/api/v1/hosts").json()["hosts"]
    assert h and len(h[0]["gauges"]) == 3 and len(h[0]["hardening"]["checks"]) == 7
    assert h[0]["hardening"]["grade"] in "ABCDF"
    d = client.get("/api/v1/devices").json()
    assert {c["group"] for c in d["chips"]} <= GROUPS
    assert sum(c["count"] for c in d["chips"]) == d["total"] >= 20


def test_search_finds_devices_by_label_and_ip(client):
    seeded_demo(client)
    body = client.get("/api/v1/search?q=camera").json()
    assert sd.SearchPayload.model_validate(body)
    devices = [r for r in body["results"] if r["kind"] == "device"]
    assert devices and any("camera" in r["title"].lower() for r in devices)
    assert devices[0]["href"].startswith("/assets/devices/")

    by_ip = client.get("/api/v1/search?q=192.168.1.182").json()
    assert any(r["id"] == CAMERA_MAC for r in by_ip["results"])


def test_search_is_tenant_scoped_and_capped(client):
    from tests.test_auth import login, seed_user
    seeded_demo(client)
    demo_hits = client.get("/api/v1/search?q=a").json()["results"]
    assert len(demo_hits) <= 25

    client.post("/api/v1/auth/logout")
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer")
    login(client, "ann", "a@x.com")
    other = client.get("/api/v1/search?q=camera").json()
    assert other["results"] == []


def test_search_covers_routes_and_multiple_kinds_within_the_cap(client):
    """A query that matches a site AND a host by name -- "booking" is both a
    demo host name and part of the site name "Online booking (...)" -- must
    surface both kinds in one response, not just whichever kind is scanned
    first. Also exercises the static route entries (the palette's page
    jumps), which never come from the database."""
    seeded_demo(client)
    host = demoscrub.map_host_name("demo")
    body = client.get(f"/api/v1/search?q={host}").json()
    kinds = {r["kind"] for r in body["results"]}
    assert "site" in kinds and "host" in kinds
    hosts = [r for r in body["results"] if r["kind"] == "host"]
    assert hosts and hosts[0]["id"] == host and hosts[0]["href"] == f"/assets/hosts?row={host}"

    routes = client.get("/api/v1/search?q=asset").json()["results"]
    assert any(r["kind"] == "route" and r["href"] == "/assets" for r in routes)


def test_payloads_validate_against_the_contracts(client):
    seeded_demo(client)
    assert sd.SitesPayload.model_validate(client.get("/api/v1/sites").json())
    assert sd.HostsPayload.model_validate(client.get("/api/v1/hosts").json())
    assert sd.DevicesPayload.model_validate(client.get("/api/v1/devices").json())
    assert sd.ScreenTimePayload.model_validate(client.get("/api/v1/presence?days=7").json())
    mac = client.get("/api/v1/devices").json()["groups"][0]["devices"][0]["mac"]
    assert sd.DeviceDetail.model_validate(client.get(f"/api/v1/devices/{mac}").json())


def test_sites_headers_and_uptime_come_from_the_scan(client):
    seeded_demo(client)
    s = client.get("/api/v1/sites").json()["sites"]
    first = next(x for x in s if x["name"] == PROD_SITE)
    assert first["headers"]["hsts"] is True and first["headers"]["server"] == "nginx"
    assert first["cert_days"] == 74
    assert first["dns_a"] == [demoscrub.map_ip("203.0.113.10")]
    assert 0 <= first["uptime_24h"] <= 100 and first["incidents_7d"] >= 0


def test_device_groups_are_classified_not_all_unknown(client):
    seeded_demo(client)
    d = client.get("/api/v1/devices").json()
    groups = {g["group"] for g in d["groups"]}
    assert groups <= GROUPS and len(groups) >= 4          # real classification happened
    assert all(g["devices"] for g in d["groups"])          # no empty group rows
    assert d["online"] >= 1
    # the router is Network gear, the iPhones are phones
    by_mac = {dev["mac"]: dev for g in d["groups"] for dev in g["devices"]}
    assert by_mac[ROUTER_MAC]["group"] == "Network gear"
    assert by_mac[ROUTER_MAC]["kind"] == "router"


def test_device_drawer_and_screen_time(client):
    seeded_demo(client)
    mac = client.get("/api/v1/devices").json()["groups"][0]["devices"][0]["mac"]
    dd = client.get(f"/api/v1/devices/{mac}").json()
    assert dd["device"]["mac"] == mac and len(dd["presence"]["hours"]) == 24
    assert dd["presence"]["online_pct_7d"] is not None and dd["dns"]["queries_24h"] >= 0
    assert len(dd["presence"]["strip"]) == 24
    st = client.get("/api/v1/presence?days=7").json()
    assert st["devices"] and st["devices"][0]["total_h"] > 0 and st["heatmap"]
    assert all(0 <= c["value"] <= 100 for c in st["heatmap"])
    assert all(len(r["hours"]) == 24 for r in st["devices"])


def test_unknown_device_drawer_is_404(client):
    seeded_demo(client)
    assert client.get("/api/v1/devices/00:00:00:00:00:00").status_code == 404


def test_assets_are_tenant_scoped(client):
    from tests.test_auth import login, seed_user
    seeded_demo(client)
    assert client.get("/api/v1/devices").json()["total"] >= 20
    client.post("/api/v1/auth/logout")
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer")
    login(client, "ann", "a@x.com")
    assert client.get("/api/v1/devices").json() == {
        "groups": [], "chips": [], "total": 0, "online": 0, "unverified": 0,
        "cameras": 0, "cameras_online": 0, "networks": [], "charts": [],
        # An empty tenant's tile row is nine empty tiles, not nine zeroes: a
        # count really is 0, but a speed/DNS/WAN figure nobody measured is null.
        "tiles": {"new_devices": 0, "trusted": 0, "randomized": 0,
                  "down_mbps": None, "up_mbps": None, "latency_ms": None,
                  "dns_ms": None, "dns_ok": None, "wan_ip": "", "wan_ips": []},
    }
    assert client.get("/api/v1/sites").json() == {"sites": [], "charts": []}
    assert client.get("/api/v1/hosts").json() == {"hosts": []}
    assert client.get("/api/v1/presence?days=7").json()["devices"] == []


#: Per-request ceiling for the four asset endpoints, counted not timed.
#:
#: Measured on the seeded demo tenant (35 devices, 3 hosts): sites 9, hosts 5,
#: devices 20, presence 6 -- and those counts barely move with tenant size
#: (the same four endpoints take 4/4/13/4 against an empty tenant, so the
#: whole 35-device listing costs +7 queries, not +35). That gap is the point:
#: a genuine N+1 would issue at least one query per device and land above 35,
#: while this ceiling still leaves room for a few honestly-added queries
#: before it goes red.
MAX_QUERIES_PER_ASSET_REQUEST = 30


def test_assets_answer_the_demo_tenant_without_a_query_per_device(client):
    """Catches N+1 queries across sites/hosts/devices/presence.

    This asserted wall-clock (`< 2.0s` per request) until it became the
    suite's one flaky test -- it failed in one full run and passed in the
    next with nothing changed, because the duration it measures belongs to
    the machine, not to the code. On this host the file's own runtime swings
    between 90s and 168s depending on what else is running, and a second
    test suite on the same laptop is enough to push a request past two
    seconds.

    Wall-clock was the wrong proxy in both directions: it goes red when the
    machine is busy and stays green on a fast machine that is running a real
    N+1. Counting statements measures the thing the docstring always claimed
    to measure, and gives the same answer on every machine.
    """
    seeded_demo(client)
    for path in ("/api/v1/sites", "/api/v1/hosts", "/api/v1/devices", "/api/v1/presence?days=7"):
        n = queries_for(client, path)
        assert n <= MAX_QUERIES_PER_ASSET_REQUEST, (
            f"{path} took {n} queries (ceiling {MAX_QUERIES_PER_ASSET_REQUEST}); "
            "a count that scales with the number of devices is an N+1"
        )


# --------------------------------------------------------------------------
# Task 77 / F1: the three site trend panels
# --------------------------------------------------------------------------

def test_sites_payload_carries_the_three_trend_panels(client):
    """`cert_days` and `up` are written by ingest on every scan and appear in no
    History chart, which is why these panels had nothing to draw."""
    from guardian_hub.payloads import assets as assets_payload
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    body = client.get("/api/v1/sites").json()
    keys = [c["key"] for c in body["charts"]]
    assert keys == ["site_latency", "site_cert", "site_uptime"]

    by_key = {c["key"]: c for c in body["charts"]}
    assert by_key["site_uptime"]["max"] == 100.0 and by_key["site_uptime"]["unit"] == "%"
    assert by_key["site_cert"]["unit"] == "days"
    names = {s["name"] for s in by_key["site_latency"]["series"]}
    assert names <= {s["name"] for s in body["sites"]}, "a series per watched site, named after it"
    for chart in body["charts"]:
        for series in chart["series"]:
            stamps = [p["ts"] for p in series["points"]]
            assert stamps == sorted(stamps) and stamps
    assert assets_payload.SITE_TREND_DAYS == 7


def test_the_availability_panel_agrees_with_the_cards_uptime(client):
    """The panel is the hourly mean of the same `up` metric the card's
    `uptime_7d` averages over the whole window, so a site that never failed
    must read 100 in both places."""
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    body = client.get("/api/v1/sites").json()
    uptime = next(c for c in body["charts"] if c["key"] == "site_uptime")
    per_site = {s["name"]: s for s in uptime["series"]}
    for card in body["sites"]:
        series = per_site.get(card["name"])
        if series is None or card["uptime_7d"] is None:
            continue
        values = [p["value"] for p in series["points"] if p["value"] is not None]
        assert values
        if card["uptime_7d"] == 100.0:
            assert all(v == 100.0 for v in values)
        assert min(values) <= card["uptime_7d"] <= max(values) or card["uptime_7d"] in values


def test_a_tenant_with_no_sites_gets_no_panels(client):
    from tests.test_auth import login, seed_user

    seed_user(username="ann", email="a@x.com", tenant_slug="empty", role="viewer")
    login(client, "ann", "a@x.com")
    body = client.get("/api/v1/sites").json()
    assert body["sites"] == [] and body["charts"] == []


# --------------------------------------------------------------------------
# Task 77 / F2: DeviceCard.online_7d
# --------------------------------------------------------------------------

def test_device_cards_online_7d_agrees_with_the_screen_time_page(client):
    """The card's figure and that device's Screen time row are the same number
    for the same window -- both count distinct `(ts, mac)` over the count of
    distinct scan timestamps, through one helper, so a multi-homed device
    cannot read differently on the two pages."""
    from guardian_hub.payloads import assets as assets_payload
    from tests.test_seed_helpers import seeded_demo

    seeded_demo(client)
    cards = {d["mac"]: d for g in client.get("/api/v1/devices").json()["groups"] for d in g["devices"]}
    rows = client.get(f"/api/v1/presence?days={assets_payload.DEVICE_ONLINE_DAYS}").json()["devices"]
    assert rows and cards

    for row in rows:
        card = cards.get(row["mac"])
        if card is None:
            continue
        assert card["online_7d"] == row["pct"], row["mac"]


def test_a_device_no_scan_ever_saw_reports_null_not_zero(client):
    """"Nothing measured this" is not "it was never online" -- a device with no
    presence sample in the window gets None, and the table draws an em dash."""
    from tests.test_seed_helpers import seeded_demo

    tid = seeded_demo(client)
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    try:
        net = db.query(m.Network).filter_by(tenant_id=tid).first()
        db.add(m.Device(tenant_id=tid, network_id=net.id if net else None, mac="aa:00:00:00:00:99",
                        ip="192.168.1.250", name="never-seen", presence="NEW"))
        db.commit()
    finally:
        db.close()

    cards = {d["mac"]: d for g in client.get("/api/v1/devices").json()["groups"] for d in g["devices"]}
    assert cards["aa:00:00:00:00:99"]["online_7d"] is None


def test_the_listing_takes_one_presence_query_for_every_card(client):
    """`device_card` must never query per row: the whole listing's percentages
    are computed once and handed in."""
    import inspect

    from guardian_hub.payloads import assets as assets_payload
    source = inspect.getsource(assets_payload.device_card)
    assert "query(" not in source and "online_pct" in source
