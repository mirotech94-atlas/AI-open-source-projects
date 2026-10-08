from tests.test_seed_helpers import seeded_demo
from tests.test_auth import seed_user, login
from tests.test_tenants import make_super
from guardian_hub.db import SessionLocal
from guardian_hub import models as m


def test_settings_round_trip_and_audit(client):
    seeded_demo(client)
    s = client.get("/api/v1/settings").json()
    assert set(s) == {"profile", "users", "sites", "hosts", "autofix", "thresholds",
                      "appearance", "sensors", "tenants", "suggested_sites"}
    assert client.post("/api/v1/settings/thresholds", json={"cert_days_warn": 30}).status_code == 200
    assert client.post("/api/v1/settings/thresholds", json={"cert_days_warn": 900}).status_code == 422
    assert client.post("/api/v1/settings/autofix", json={"enabled": False, "max_per_day": 5,
        "attacker_method": "auto", "maintenance_window": {"start": "02:00", "end": "05:00"}}).status_code == 200
    assert client.get("/api/v1/settings").json()["autofix"]["enabled"] is False
    rows = client.get("/api/v1/audit").json()["rows"]
    assert {"thresholds.update", "autofix.update"} <= {r["action"] for r in rows}


def test_targets_are_validated_and_reach_the_sensor(client):
    seeded_demo(client)
    assert client.post("/api/v1/settings/targets", json={"sites": [{"name": "x", "url": "ftp://x"}],
                                                         "hosts": []}).status_code == 422
    assert client.post("/api/v1/settings/targets", json={"sites": [{"name": "x", "url": "https://x.example"}],
                                                         "hosts": []}).status_code == 200
    e = SessionLocal().query(m.Edit).filter_by(path="/api/config").order_by(m.Edit.id.desc()).first()
    assert e.body["config"]["sites"][0]["url"] == "https://x.example"


def test_hosts_are_validated(client):
    seeded_demo(client)
    assert client.post("/api/v1/settings/targets", json={"sites": [], "hosts": [{"name": "db"}]}).status_code == 422
    assert client.post("/api/v1/settings/targets",
                        json={"sites": [], "hosts": [{"name": "db", "target": "10.0.0.5"}]}).status_code == 200


def test_viewer_reads_but_cannot_write_and_audit_is_admin_only(client):
    seed_user(username="ann", email="a@x.com", tenant_slug="other", role="viewer"); login(client, "ann", "a@x.com")
    assert client.get("/api/v1/settings").status_code == 200
    assert client.post("/api/v1/settings/thresholds", json={"cert_days_warn": 30}).status_code == 403
    assert client.get("/api/v1/audit").status_code == 403


def test_admin_cannot_remove_their_own_last_admin_membership(client):
    seeded_demo(client)
    r = client.request("DELETE", "/api/v1/settings/users", json={"username": "demoadmin"})
    assert r.status_code == 422


def test_users_upsert_and_removal(client):
    seeded_demo(client)
    r = client.post("/api/v1/settings/users",
                     json={"username": "bob", "email": "bob@x.com", "display_name": "Bob", "role": "operator"})
    assert r.status_code == 200
    usernames = {u["username"] for u in client.get("/api/v1/settings/users").json()}
    assert "bob" in usernames
    # A second admin exists now so demoadmin is no longer the *last* admin --
    # but bob is only an operator, so promote him first to prove the guard
    # is really about admin-count, not identity.
    client.post("/api/v1/settings/users", json={"username": "bob", "email": "bob@x.com", "role": "admin"})
    assert client.request("DELETE", "/api/v1/settings/users", json={"username": "demoadmin"}).status_code == 200
    # demoadmin just removed their own membership -- the session no longer
    # satisfies "admin role required" for this tenant, so switch to bob
    # (now the tenant's sole admin) to confirm the removal really landed.
    login(client, "bob", "bob@x.com")
    usernames = {u["username"] for u in client.get("/api/v1/settings/users").json()}
    assert "demoadmin" not in usernames


def test_profile_alias_still_works_and_reaches_settings(client):
    seeded_demo(client)
    r = client.post("/api/v1/profile", json={"owner": "Miro", "email": "m@x.com", "address": "123 St"})
    assert r.status_code == 200
    e = SessionLocal().query(m.Edit).filter_by(path="/api/profile").order_by(m.Edit.id.desc()).first()
    assert e.body == {"owner": "Miro", "profile": {"email": "m@x.com", "address": "123 St"}}
    r2 = client.post("/api/v1/settings/profile", json={"owner": "Miro", "email": "m@x.com",
                                                        "address": "123 St", "company": "Acme"})
    assert r2.status_code == 200
    profile = client.get("/api/v1/settings").json()["profile"]
    assert profile == {"owner": "Miro", "email": "m@x.com", "address": "123 St", "company": "Acme"}


def test_appearance_persists_for_admin(client):
    seeded_demo(client)
    assert client.post("/api/v1/settings/appearance", json={"theme": "dark", "accent": "blue"}).status_code == 200
    assert client.post("/api/v1/settings/appearance", json={"theme": "neon"}).status_code == 422
    assert client.get("/api/v1/settings").json()["appearance"]["theme"] == "dark"


def test_sensors_list_and_revoke(client):
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first() or db.query(m.Tenant).first()
    sensor = m.Sensor(tenant_id=tenant.id, name="pi", token_hash="abc", status="active")
    db.add(sensor); db.commit(); sid = sensor.id; db.close()

    rows = client.get("/api/v1/settings/sensors").json()
    assert any(r["id"] == sid for r in rows)

    r = client.post(f"/api/v1/settings/sensors/{sid}/revoke")
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    db = SessionLocal()
    refreshed = db.get(m.Sensor, sid)
    assert refreshed.status == "revoked" and refreshed.token_hash is None
    db.close()
    assert SessionLocal().query(m.Audit).filter_by(action="sensor.revoke").count() == 1


def test_tenants_crud_via_settings_is_superadmin_only(client):
    tid = make_super(client)
    r = client.post("/api/v1/settings/tenants", json={"name": "Northwind", "slug": "northwind"})
    assert r.status_code == 200
    new_id = r.json()["id"]

    r = client.patch(f"/api/v1/settings/tenants/{new_id}", json={"demo": True})
    assert r.status_code == 200
    db = SessionLocal()
    t = db.get(m.Tenant, new_id)
    assert t.settings.get("demo_code") == "424242"
    db.close()

    r = client.patch(f"/api/v1/settings/tenants/{new_id}", json={"demo": False})
    assert r.status_code == 200
    db = SessionLocal()
    assert "demo_code" not in (db.get(m.Tenant, new_id).settings or {})
    db.close()

    r = client.patch(f"/api/v1/settings/tenants/{new_id}", json={"brand": {"name": "NW", "accent": "blue"}})
    assert r.status_code == 200 and r.json()["brand"]["name"] == "NW"

    # Refuses deleting the caller's own active tenant.
    r = client.get("/api/v1/me").json()
    active_id = r["active_tenant"]["id"]
    assert client.delete(f"/api/v1/settings/tenants/{active_id}").status_code == 422

    assert client.delete(f"/api/v1/settings/tenants/{new_id}").status_code == 200
    db = SessionLocal()
    assert db.get(m.Tenant, new_id) is None
    db.close()


def test_tenants_settings_route_requires_superadmin(client):
    seeded_demo(client)
    assert client.get("/api/v1/settings/tenants").status_code == 403
    assert client.post("/api/v1/settings/tenants", json={"name": "x", "slug": "x"}).status_code == 403


def test_maintenance_window_route(client):
    seeded_demo(client)
    r = client.post("/api/v1/maintenance", json={"hours": 2})
    assert r.status_code == 200
    e = SessionLocal().query(m.Edit).filter_by(path="/api/maintenance").order_by(m.Edit.id.desc()).first()
    assert e.body == {"hours": 2}
    assert SessionLocal().query(m.Audit).filter_by(action="maintenance").count() == 1


def test_config_alias_still_replaces_whole_config(client):
    seeded_demo(client)
    cfg = {"sites": [], "ssh_hosts": [], "thresholds": {}}
    r = client.post("/api/v1/config", json=cfg)
    assert r.status_code == 200 and r.json() == cfg


def test_settings_renders_a_real_config_whose_expect_is_a_list(client):
    """`expect` is the list of acceptable status codes the sensor tests
    membership in (`sensor/sites.py`), and a real imported config carries
    `[200]` / `[200, 307, 302]`. `SiteTarget.expect` used to be `int | None`,
    so a tenant with that (perfectly valid) config 500'd the whole Settings
    tab -- which is what the live `home` tenant did. Both shapes, plus the
    single int an older revision of this router wrote, must render."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    config = dict(tenant.config or {})
    config["sites"] = [
        {"name": "Roots prod", "url": "https://status.example.org/health", "expect": [200],
         "expect_body": '"ok":true', "host": "status.example.org", "public": True},
        {"name": "FlyCheap", "url": "https://flycheap.example.com/", "expect": [200, 307, 302]},
        {"name": "Legacy int", "url": "https://legacy.example/", "expect": 204},
        {"name": "Unset", "url": "https://unset.example/"},
    ]
    tenant.config = config
    db.commit()

    sites = client.get("/api/v1/settings").json()["sites"]
    assert [s["expect"] for s in sites] == [[200], [200, 307, 302], [204], []]
    # `expect_body` is a sensor-side field with no dashboard mirror -- it
    # must be ignored on the way out, not blow up the model.
    assert sites[0]["name"] == "Roots prod" and sites[0]["public"] is True


def test_targets_write_keeps_expect_a_list_for_the_sensor(client):
    seeded_demo(client)
    r = client.post("/api/v1/settings/targets", json={
        "sites": [{"name": "redirects", "url": "https://x.example", "expect": [200, 301]}], "hosts": []})
    assert r.status_code == 200
    assert r.json()["sites"][0]["expect"] == [200, 301]
    edit = SessionLocal().query(m.Edit).filter_by(path="/api/config").order_by(m.Edit.id.desc()).first()
    assert edit.body["config"]["sites"][0]["expect"] == [200, 301]


def test_targets_save_never_writes_an_empty_expect(client):
    """An empty `expect` means "no opinion" and must stay ABSENT: the
    sensor only falls back to `[200]` when the key is missing
    (`sites.py`: `s.get("expect", [200])`), so a stored `[]` would make
    `code in []` false forever and report every healthy site as down."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    tenant.config = {**(tenant.config or {}),
                      "sites": [{"name": "plain", "url": "https://plain.example"}]}
    db.commit()

    # Round-trip exactly what GET hands the editor -- which now carries
    # `expect: []` for that site -- and save it back untouched.
    sites = client.get("/api/v1/settings").json()["sites"]
    assert sites[0]["expect"] == []
    assert client.post("/api/v1/settings/targets", json={"sites": sites, "hosts": []}).status_code == 200

    stored = SessionLocal().query(m.Tenant).filter_by(slug="demo").first().config["sites"][0]
    assert "expect" not in stored, f"empty expect was persisted: {stored}"


def test_targets_save_preserves_sensor_only_fields(client):
    """`SiteIn`/`HostIn` carry only what this dashboard edits. A real
    config carries more -- `expect_body` on a site, `expect_docker`/`key`
    on a host (read by `sensor/alerts.py` and `sensor/fix.py`) -- and a
    Save that replaced each row wholesale silently dropped them,
    weakening checks the admin never touched."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    tenant.config = {
        **(tenant.config or {}),
        "sites": [{"name": "Roots prod", "url": "https://status.example.org/health", "expect": [200],
                   "expect_body": '"ok":true', "host": "status.example.org"}],
        "ssh_hosts": [{"name": "prod", "target": "root@1.2.3.4", "key": "~/.ssh/id_ed25519",
                        "expect_docker": ["guardian-hub"]}],
    }
    db.commit()

    payload = client.get("/api/v1/settings").json()
    # Rename the site the way the editor would, and save both tables back.
    payload["sites"][0]["name"] = "Roots production"
    assert client.post("/api/v1/settings/targets",
                        json={"sites": payload["sites"], "hosts": payload["hosts"]}).status_code == 200

    config = SessionLocal().query(m.Tenant).filter_by(slug="demo").first().config
    site, host = config["sites"][0], config["ssh_hosts"][0]
    assert site["name"] == "Roots production"          # the edit landed
    assert site["expect_body"] == '"ok":true'          # ...and the rest survived it
    assert site["expect"] == [200]
    assert host["key"] == "~/.ssh/id_ed25519"
    assert host["expect_docker"] == ["guardian-hub"]


def test_targets_save_still_removes_a_row_the_editor_dropped(client):
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    tenant.config = {**(tenant.config or {}),
                      "sites": [{"name": "keep", "url": "https://keep.example"},
                                {"name": "drop", "url": "https://drop.example"}]}
    db.commit()

    assert client.post("/api/v1/settings/targets", json={
        "sites": [{"name": "keep", "url": "https://keep.example"}], "hosts": []}).status_code == 200
    stored = SessionLocal().query(m.Tenant).filter_by(slug="demo").first().config["sites"]
    assert [s["name"] for s in stored] == ["keep"]


# --- Fix round 2: expect clearable / expect_docker rename / public sync ----

def test_targets_clearing_expect_persists_the_clear(client):
    """An explicit `expect: []` in the request -- the field present and
    empty, distinct from the field being absent -- must actually clear
    the stored value rather than `_merge_targets`'s `{**base, **row}`
    silently falling back to what was already there."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    tenant.config = {**(tenant.config or {}),
                      "sites": [{"name": "x", "url": "https://x.example", "expect": [200, 301]}]}
    db.commit()

    r = client.post("/api/v1/settings/targets",
                     json={"sites": [{"name": "x", "url": "https://x.example", "expect": []}], "hosts": []})
    assert r.status_code == 200
    # The raw stored config never carries an empty `expect` -- the POST
    # response mirrors exactly what got written, so the key is absent
    # there too (a fresh GET renders it back as `[]` via `_site_targets`).
    assert "expect" not in r.json()["sites"][0]

    stored = SessionLocal().query(m.Tenant).filter_by(slug="demo").first().config["sites"][0]
    assert "expect" not in stored, f"clearing expect did not persist: {stored}"

    sites = client.get("/api/v1/settings").json()["sites"]
    assert sites[0]["expect"] == []


def test_targets_absent_expect_keeps_the_stored_value(client):
    """The field simply missing from the request -- as opposed to present
    and empty -- means the caller has no opinion, and the stored value
    must survive the save untouched."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    tenant.config = {**(tenant.config or {}),
                      "sites": [{"name": "x", "url": "https://x.example", "expect": [200, 301]}]}
    db.commit()

    r = client.post("/api/v1/settings/targets",
                     json={"sites": [{"name": "x", "url": "https://x.example"}], "hosts": []})
    assert r.status_code == 200
    assert r.json()["sites"][0]["expect"] == [200, 301]

    stored = SessionLocal().query(m.Tenant).filter_by(slug="demo").first().config["sites"][0]
    assert stored["expect"] == [200, 301]


def test_settings_get_coerces_a_malformed_stored_expect_instead_of_422ing(client):
    """A malformed stored `expect` must never break the read path -- that
    is the same failure class as the earlier 500/422-on-`GET /settings`
    bugs this file already covers, just triggered by a bad value instead
    of an unexpected shape. 422 belongs on the write path only (see
    `test_targets_rejects_a_stringy_expect_instead_of_corrupting_it`
    below). A digit-string (`"200"`) is coerced to the int; a value with
    no usable int left after coercion (`["abc"]`) falls back to the
    sensor's own default `[200]` (`sensor/guardian_sensor/sites.py`)."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    tenant.config = {**(tenant.config or {}),
                      "sites": [{"name": "x", "url": "https://x.example", "expect": "200"},
                                {"name": "y", "url": "https://y.example", "expect": ["abc"]}]}
    db.commit()

    r = client.get("/api/v1/settings")
    assert r.status_code == 200
    assert [s["expect"] for s in r.json()["sites"]] == [[200], [200]]


def test_targets_rejects_a_stringy_expect_instead_of_corrupting_it(client):
    """`list("200")` used to silently turn a stringy `expect` into the
    digits `[2, 0, 0]`. The write path must 422 instead of storing it --
    unlike the read path (above), which coerces rather than raising."""
    seeded_demo(client)
    r = client.post("/api/v1/settings/targets",
                     json={"sites": [{"name": "x", "url": "https://x.example", "expect": ["abc"]}],
                           "hosts": []})
    assert r.status_code == 422


def test_targets_write_coerces_a_digit_string_expect(client):
    """A digit-string `expect` on write is accepted and stored as the
    int it names -- Pydantic's lenient `int` coercion, not a 422."""
    seeded_demo(client)
    r = client.post("/api/v1/settings/targets",
                     json={"sites": [{"name": "x", "url": "https://x.example", "expect": ["200"]}],
                           "hosts": []})
    assert r.status_code == 200
    assert r.json()["sites"][0]["expect"] == [200]

    stored = SessionLocal().query(m.Tenant).filter_by(slug="demo").first().config["sites"][0]
    assert stored["expect"] == [200]


def test_targets_save_syncs_site_public_flag_onto_the_site_row(client):
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    site = m.Site(tenant_id=tenant.id, name="pubtest", url="https://pubtest.example", public=False)
    db.add(site); db.commit(); site_id = site.id

    assert client.post("/api/v1/settings/targets", json={
        "sites": [{"name": "pubtest", "url": "https://pubtest.example", "public": True}],
        "hosts": []}).status_code == 200
    assert SessionLocal().get(m.Site, site_id).public is True

    # Un-tick flips it back.
    assert client.post("/api/v1/settings/targets", json={
        "sites": [{"name": "pubtest", "url": "https://pubtest.example", "public": False}],
        "hosts": []}).status_code == 200
    assert SessionLocal().get(m.Site, site_id).public is False


def test_targets_save_unpublishes_a_site_removed_from_the_editor(client):
    """A `Site` row that no longer matches any row in the editor's save --
    because it was removed -- must be unpublished, not left alone."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    site = m.Site(tenant_id=tenant.id, name="gone", url="https://gone.example", public=True)
    db.add(site); db.commit(); site_id = site.id

    assert client.post("/api/v1/settings/targets", json={"sites": [], "hosts": []}).status_code == 200
    assert SessionLocal().get(m.Site, site_id).public is False


# --------------------------------------------------------------------------
# Items 43/44: Vault offers what it has already found
# --------------------------------------------------------------------------

def test_a_web_server_guardian_can_see_is_offered_not_written(client):
    """An empty Targets tab on a network full of web servers is the product
    asking the customer to type in what it already knows. It is an OFFER:
    nothing reaches the config until a person presses Add."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    db.add(m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:01", ip="192.168.1.50",
                    name="nas", ports={"443": "2026-09-03T10:00:00Z"},
                    first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"))
    before = list((tenant.config or {}).get("sites") or [])
    db.commit(); db.close()

    s = client.get("/api/v1/settings").json()
    offer = next(x for x in s["suggested_sites"] if x["url"] == "https://192.168.1.50")
    assert "port 443" in offer["why"], "a suggestion with no evidence is a guess"

    db = SessionLocal()
    after = list((db.query(m.Tenant).filter_by(slug="demo").first().config or {}).get("sites") or [])
    db.close()
    assert after == before, "Guardian must not add a target on its own"


def test_something_already_watched_is_not_offered_again(client):
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    db.add(m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:02", ip="192.168.1.51",
                    name="nas", ports={"443": "2026-09-03T10:00:00Z"},
                    first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"))
    db.commit(); db.close()
    assert client.post("/api/v1/settings/targets", json={
        "sites": [{"name": "nas", "url": "https://192.168.1.51"}], "hosts": [],
    }).status_code == 200

    s = client.get("/api/v1/settings").json()
    assert not [x for x in s["suggested_sites"] if "192.168.1.51" in x["url"]]


def test_a_device_with_no_web_port_is_not_offered(client):
    """Padding the list with maybes is how it stops being read."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    db.add(m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:03", ip="192.168.1.52",
                    name="printer", ports={"9100": "2026-09-03T10:00:00Z"},
                    first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"))
    db.commit(); db.close()
    s = client.get("/api/v1/settings").json()
    assert not [x for x in s["suggested_sites"] if "192.168.1.52" in x["url"]]


def test_a_box_watched_as_a_SERVER_is_not_offered_as_a_website(client):
    """Miro's standing rule: do not mix hosts and servers, because some have
    the same name. It cuts this way too — the guardian-hub box is watched as
    a server, and offering it again as an unwatched web server is exactly
    the confusion he asked to be rid of."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    db.add(m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:04", ip="192.168.1.60",
                    name="guardian-hub", ports={"443": "2026-09-03T10:00:00Z"},
                    first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"))
    db.commit(); db.close()
    assert client.post("/api/v1/settings/targets", json={
        "sites": [],
        "hosts": [{"name": "guardian-hub", "target": "root@192.168.1.60"}],
    }).status_code == 200

    offers = client.get("/api/v1/settings").json()["suggested_sites"]
    assert not [o for o in offers if "192.168.1.60" in o["url"]], \
        "a configured server is not an unwatched web server"


def test_a_site_configured_by_its_own_NAME_masks_the_device(client):
    """The realistic shape: nobody configures a site as `https://192.168.1.x`."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    db.add(m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:05", ip="192.168.1.61",
                    name="roots", label="Roots", ports={"443": "2026-09-03T10:00:00Z"},
                    first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"))
    db.commit(); db.close()
    assert client.post("/api/v1/settings/targets", json={
        "sites": [{"name": "Roots", "url": "https://status.example.test"}], "hosts": [],
    }).status_code == 200

    offers = client.get("/api/v1/settings").json()["suggested_sites"]
    assert not [o for o in offers if "192.168.1.61" in o["url"]]


def test_the_gateway_and_cameras_stay_out(client):
    """The gateway is the way out, not a target, and a camera already has
    its own tab and its own "is it answering" check -- a second place to add
    one would be two sources for one fact.

    Everything ELSE that answers on a web port is offered, including
    appliance admin pages: the first version called them "websites", which
    was the wrong word, not the wrong list."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first()
    db.add_all([
        m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:10", ip="192.168.9.1",
                 name="router", kind="router", ports={"443": "2026-09-03T10:00:00Z"},
                 first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"),
        m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:11", ip="192.168.9.2",
                 name="Front door", kind="camera", ports={"443": "2026-09-03T10:00:00Z"},
                 first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"),
        m.Device(tenant_id=tenant.id, mac="aa:bb:cc:dd:ee:12", ip="192.168.9.3",
                 name="Canon MF8200C", kind="unknown", ports={"80": "2026-09-03T10:00:00Z"},
                 first_seen="2026-09-03T10:00:00Z", last_seen="2026-09-22T10:00:00Z"),
    ])
    db.commit(); db.close()
    offers = client.get("/api/v1/settings").json()["suggested_sites"]
    assert not [o for o in offers if "192.168.9.2" in o["url"]], "a camera has its own tab"
    assert [o for o in offers if "192.168.9.3" in o["url"]], \
        "an unauthenticated printer admin page is worth watching"
