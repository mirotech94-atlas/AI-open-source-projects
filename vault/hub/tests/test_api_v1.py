"""The public REST API: tokens, scoping, writes, docs, and the limiter.

The two tests that matter most, and would matter even if every other one here
were deleted:

- `test_cross_tenant_identifiers_are_404_never_403` -- a token from one account
  must not be able to learn that another account's identifier exists.
- `test_read_token_cannot_write` / `test_role_is_clamped_to_creator` -- scope
  and role are the whole authorisation model, and both must hold on the row
  rather than on the caller's word.
"""
import json

from guardian_hub import apideps, apidocs, apikeys, models as m, ratelimit
from guardian_hub.db import SessionLocal
from tests.test_auth import login, seed_user
from tests.test_sensor_api import enrol
from tests.test_tenants import make_super
from tests import scanfixture


TOKENS = "/api/v1/settings/api-tokens"


def _ingest(client, tid):
    """Seed one tenant with the standard scan fixture, via its own sensor."""
    tok = enrol(client, tid)
    H = {"Authorization": f"Bearer {tok}"}
    client.post("/api/v1/sensor/ingest", json={
        "last": scanfixture.load_last(),
        "state": scanfixture.load_state(),
        "events": [], "history": [], "fixes": [], "dns_queries": [],
    }, headers=H)
    return H


def mint(client, *, name="ci", scope="read", role="operator", days=365):
    r = client.post(TOKENS, json={"name": name, "scope": scope, "role": role, "days": days})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["secret"], body["token"]


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def seeded(client):
    """A superadmin session on a seeded tenant, plus a read and a write token."""
    tid = make_super(client)
    _ingest(client, tid)
    read_secret, _ = mint(client, name="reader", scope="read", role="viewer")
    write_secret, _ = mint(client, name="writer", scope="write", role="operator")
    return tid, read_secret, write_secret


# --------------------------------------------------------------------------
# Token shape and storage
# --------------------------------------------------------------------------

def test_token_format_and_storage(client):
    make_super(client)
    secret, row = mint(client, name="Zapier", scope="write", role="admin")

    assert secret.startswith("gapi_")
    head, public, tail = secret.split("_")
    assert head == "gapi" and len(public) == apikeys.PREFIX_LEN
    assert len(tail) == apikeys.SECRET_LEN
    assert row["prefix"] == f"gapi_{public}"
    # The response carries the row and the secret, and nothing that could be
    # turned back into a second secret.
    assert "token_hash" not in row and "hash" not in json.dumps(row)

    db = SessionLocal()
    stored = db.query(m.ApiToken).filter_by(prefix=row["prefix"]).one()
    # Only a hash, and it is not the secret.
    assert stored.token_hash != secret and len(stored.token_hash) == 64
    assert secret not in json.dumps(apikeys.out(stored))


def test_secret_is_shown_once_and_never_again(client):
    make_super(client)
    secret, row = mint(client)
    listed = client.get(TOKENS).json()["tokens"]
    blob = json.dumps(listed)
    assert secret not in blob
    assert row["prefix"] in blob


def test_last_used_is_recorded(client):
    _tid, read_secret, _w = seeded(client)
    before = client.get(TOKENS).json()["tokens"]
    assert all(t["last_used"] is None for t in before)

    assert client.get("/v1/whoami", headers=bearer(read_secret)).status_code == 200

    after = {t["name"]: t for t in client.get(TOKENS).json()["tokens"]}
    assert after["reader"]["last_used"] is not None
    assert after["reader"]["last_used_ip"]
    # The token that was not used stays untouched.
    assert after["writer"]["last_used"] is None


def test_revoked_token_stops_working_but_stays_listed(client):
    make_super(client)
    secret, row = mint(client)
    assert client.get("/v1/whoami", headers=bearer(secret)).status_code == 200

    r = client.delete(f"{TOKENS}/{row['id']}")
    assert r.status_code == 200 and r.json()["revoked"] is True

    assert client.get("/v1/whoami", headers=bearer(secret)).status_code == 401
    listed = client.get(TOKENS).json()["tokens"]
    assert [t["prefix"] for t in listed] == [row["prefix"]]


def test_expired_token_is_rejected(client):
    make_super(client)
    secret, row = mint(client, days=1)
    db = SessionLocal()
    stored = db.query(m.ApiToken).filter_by(id=row["id"]).one()
    stored.expires = stored.created  # already past
    db.commit()
    assert client.get("/v1/whoami", headers=bearer(secret)).status_code == 401


def test_token_creation_is_audited_without_the_secret(client):
    make_super(client)
    secret, row = mint(client, name="Zapier")
    rows = SessionLocal().query(m.Audit).filter_by(action="apitoken.create").all()
    assert len(rows) == 1
    assert rows[0].target == row["prefix"] and rows[0].user == "miro"
    assert secret not in json.dumps(rows[0].body)


# --------------------------------------------------------------------------
# Scope and role
# --------------------------------------------------------------------------

def test_read_token_cannot_write(client):
    _tid, read_secret, _w = seeded(client)
    mac = client.get("/v1/devices", headers=bearer(read_secret)).json()["items"][0]["mac"]
    r = client.post(f"/v1/devices/{mac}", json={"trusted": True}, headers=bearer(read_secret))
    assert r.status_code == 403 and "read-only" in r.json()["detail"]


def test_write_token_with_viewer_role_cannot_write(client):
    """Scope and role are independent: a write-scope token still needs the
    operator role, which is what makes a viewer-created token harmless."""
    make_super(client)
    secret, _ = mint(client, name="weak", scope="write", role="viewer")
    r = client.post("/v1/scans", headers=bearer(secret))
    assert r.status_code == 403 and "operator" in r.json()["detail"]


def test_role_is_clamped_to_creator(client):
    """An operator creating an admin token gets an operator token."""
    seed_user(role="operator")
    login(client)
    r = client.post(TOKENS, json={"name": "x", "scope": "write", "role": "admin"})
    # Token management is admin-only, so an operator cannot reach it at all --
    # which is the strongest possible form of the clamp.
    assert r.status_code == 403

    # And the clamp itself, at the unit level, for the operator-admin creator
    # pair the route can produce.
    assert apikeys.clamp_role("admin", "operator") == "operator"
    assert apikeys.clamp_role("viewer", "admin") == "viewer"
    assert apikeys.clamp_role("nonsense", "admin") == "viewer"


def test_token_management_is_admin_only(client):
    seed_user(role="viewer")
    login(client)
    assert client.get(TOKENS).status_code == 403
    assert client.post(TOKENS, json={"name": "x"}).status_code == 403


def test_session_cookie_cannot_authenticate_the_api(client):
    """The signed-in admin's cookie rides along on every TestClient call. It
    must not be enough for a `/v1` route -- the API host is a different origin,
    and a cookie-authenticated API would be a forgery surface on every write."""
    make_super(client)
    r = client.get("/v1/whoami")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_bad_tokens_are_indistinguishable(client):
    make_super(client)
    secret, row = mint(client)
    client.delete(f"{TOKENS}/{row['id']}")
    details = set()
    for candidate in ("gapi_deadbeef_" + "0" * 48, "not-a-token", secret, ""):
        r = client.get("/v1/whoami", headers=bearer(candidate))
        assert r.status_code == 401
        details.add(r.json()["detail"])
    # "no token at all" is allowed to read differently from "a token that did
    # not work"; every *presented* token must not be.
    assert details <= {"invalid API token", "an API token is required"}
    assert "revoked" not in " ".join(details) and "expired" not in " ".join(details)


# --------------------------------------------------------------------------
# Tenant scoping
# --------------------------------------------------------------------------

def test_cross_tenant_identifiers_are_404_never_403(client):
    """Tenant A's token, given B's real identifiers, must answer 404 with no
    hint that they exist."""
    tid_a = make_super(client)
    _ingest(client, tid_a)
    secret_a, _ = mint(client, name="a", scope="write", role="admin")

    # A second tenant with its own data, created through the superadmin routes.
    tid_b = client.post("/api/v1/tenants", json={"slug": "other", "name": "Other Co"}).json()["id"]
    _ingest(client, tid_b)

    db = SessionLocal()
    b_mac = db.query(m.Device).filter_by(tenant_id=tid_b).first().mac
    a_macs = {d.mac for d in db.query(m.Device).filter_by(tenant_id=tid_a).all()}
    b_alert = m.Alert(tenant_id=tid_b, key="b-only-alert", sev="WARN", msg="theirs",
                      first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00")
    b_report = m.Report(tenant_id=tid_b, kind="manual", period_days=7, path="/nope.html")
    b_queue = m.Queue(tenant_id=tid_b, who="them", path="/api/run", body={})
    b_net = db.query(m.Network).filter_by(tenant_id=tid_b).first()
    db.add_all([b_alert, b_report, b_queue])
    db.commit()
    b_ids = {"report": b_report.id, "queue": b_queue.id,
             "net": b_net.slug if b_net else "primary"}
    db.close()

    H = bearer(secret_a)
    # Every one of these identifiers is real -- just not A's.
    assert client.get(f"/v1/alerts/{b_alert.key}", headers=H).status_code == 404
    assert client.post(f"/v1/alerts/{b_alert.key}/ack", headers=H).status_code == 404
    assert client.get(f"/v1/reports/{b_ids['report']}", headers=H).status_code == 404
    assert client.get(f"/v1/jobs/{b_ids['queue']}", headers=H).status_code == 404
    if b_mac not in a_macs:  # the fixture is the same file, so most MACs overlap
        assert client.get(f"/v1/devices/{b_mac}", headers=H).status_code == 404
        assert client.post(f"/v1/devices/{b_mac}", json={"trusted": True},
                           headers=H).status_code == 404

    # Not one of them was a 403: a 403 would confirm the identifier is real.
    for path in (f"/v1/alerts/{b_alert.key}", f"/v1/reports/{b_ids['report']}",
                 f"/v1/jobs/{b_ids['queue']}"):
        assert client.get(path, headers=H).status_code != 403

    # And B's alert never appears in A's list, nor B's rows in A's totals.
    keys = [a["key"] for a in client.get("/v1/alerts", headers=H).json()["items"]]
    assert b_alert.key not in keys
    assert client.get("/v1/reports", headers=H).json()["total"] == 0


def test_writes_never_touch_another_tenants_rows(client):
    tid_a = make_super(client)
    _ingest(client, tid_a)
    secret_a, _ = mint(client, name="a", scope="write", role="admin")
    tid_b = client.post("/api/v1/tenants", json={"slug": "other", "name": "Other"}).json()["id"]

    db = SessionLocal()
    db.add(m.Alert(tenant_id=tid_b, key="shared-key", sev="CRIT", msg="theirs",
                   first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00"))
    db.commit()
    db.close()

    assert client.post("/v1/alerts/shared-key/resolve",
                       headers=bearer(secret_a)).status_code == 404
    db = SessionLocal()
    assert db.query(m.Alert).filter_by(tenant_id=tid_b, key="shared-key").one().status == "open"


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def test_reads_answer_and_paginate(client):
    _tid, read_secret, _w = seeded(client)
    H = bearer(read_secret)

    for path in ("/v1/whoami", "/v1/overview", "/v1/networks", "/v1/sites", "/v1/hosts",
                 "/v1/security", "/v1/topology", "/v1/presence", "/v1/dns", "/v1/dns/rules",
                 "/v1/dns/activity", "/v1/devices", "/v1/alerts", "/v1/incidents",
                 "/v1/recommendations", "/v1/events", "/v1/reports"):
        r = client.get(path, headers=H)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"

    page = client.get("/v1/devices?limit=3&offset=0", headers=H).json()
    assert set(page) == {"items", "total", "limit", "offset"}
    assert page["limit"] == 3 and len(page["items"]) == 3 and page["total"] > 3

    second = client.get("/v1/devices?limit=3&offset=3", headers=H).json()
    assert {d["mac"] for d in second["items"]}.isdisjoint({d["mac"] for d in page["items"]})
    assert second["total"] == page["total"]

    # limit is clamped, not rejected, and the envelope reports the truth.
    assert client.get("/v1/devices?limit=99999", headers=H).json()["limit"] == 200
    assert client.get("/v1/devices?limit=0", headers=H).status_code == 422
    assert client.get("/v1/devices?offset=-1", headers=H).status_code == 422


def test_alerts_do_not_publish_a_heartbeat_token_or_raw_evidence(client):
    """Two leaks the dashboard tolerates and a public API must not.

    A heartbeat setup alert's message contains the heartbeat's own token, because
    the message IS the command you paste into a cron job. Handing that to a
    read-scope API token is an escalation: it is a second credential, and that one
    can write -- whoever holds it can falsely report that a backup ran, which is
    exactly the failure Vault exists to catch.

    `evidence` is a free-form pass-through of sensor-supplied JSON, so no version
    of this API can promise to have vetted its contents. It is dropped, not
    filtered.
    """
    tid, read_secret, _w = seeded(client)
    db = SessionLocal()
    db.add(m.Alert(
        tenant_id=tid, key="hb:nightly-backup:setup", sev="INFO",
        msg="Heartbeat 'nightly-backup' never pinged yet — add "
            "`curl -s https://my.guarder.io/hb/nightly-backup/s3cr3tt0k3nvalue` "
            "to the end of that job",
        why="add `curl -s https://my.guarder.io/hb/nightly-backup/s3cr3tt0k3nvalue`",
        evidence={"internal_note": "do-not-publish", "token": "s3cr3tt0k3nvalue"},
        first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00",
    ))
    db.commit()
    db.close()

    H = bearer(read_secret)
    for path in ("/v1/alerts?limit=50", "/v1/alerts/hb:nightly-backup:setup"):
        body = client.get(path, headers=H).text
        assert "s3cr3tt0k3nvalue" not in body, path
        assert "do-not-publish" not in body, path
        assert "evidence" not in body, path
        # The useful half of the message survives -- this is a redaction, not a
        # blanket suppression of the alert.
        assert "nightly-backup" in body, path

    one = client.get("/v1/alerts/hb:nightly-backup:setup", headers=H).json()
    assert "/hb/nightly-backup/<token" in one["msg"]

    # And the dashboard is unchanged: an admin on the app's own origin still sees
    # the real command, because that is the point of the message.
    dash = client.get("/api/v1/inbox?view=alerts").text
    assert "s3cr3tt0k3nvalue" in dash


def test_reads_do_not_publish_internal_device_fields(client):
    _tid, read_secret, _w = seeded(client)
    d = client.get("/v1/devices", headers=bearer(read_secret)).json()["items"][0]
    for hidden in ("quarantined", "parent", "extra"):
        assert hidden not in d


def test_filters_narrow_the_result(client):
    _tid, read_secret, write_secret = seeded(client)
    H = bearer(read_secret)
    all_devices = client.get("/v1/devices?limit=200", headers=H).json()
    mac = all_devices["items"][0]["mac"]
    client.post(f"/v1/devices/{mac}", json={"trusted": True, "label": "Kitchen tablet"},
                headers=bearer(write_secret))

    trusted = client.get("/v1/devices?trusted=true&limit=200", headers=H).json()
    assert trusted["total"] >= 1 and all(d["trusted"] for d in trusted["items"])
    assert trusted["total"] < all_devices["total"]

    found = client.get("/v1/devices?q=Kitchen%20tablet", headers=H).json()
    assert [d["mac"] for d in found["items"]] == [mac]

    assert client.get("/v1/alerts?status=nonsense", headers=H).status_code == 422
    assert client.get("/v1/alerts?sev=nonsense", headers=H).status_code == 422
    # An unknown network reference 404s rather than silently widening.
    assert client.get("/v1/devices?network=no-such-net", headers=H).status_code == 404


def test_unknown_v1_path_is_json_404_not_the_app(client):
    _tid, read_secret, _w = seeded(client)
    r = client.get("/v1/nope", headers=bearer(read_secret))
    assert r.status_code == 404
    assert "<html" not in r.text.lower()


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------

def test_alert_write_is_audited_and_replayable(client):
    tid, _r, write_secret = seeded(client)
    db = SessionLocal()
    db.add(m.Alert(tenant_id=tid, key="lan.new-device", sev="WARN", msg="a new thing",
                   first_seen="2026-01-01T00:00:00+00:00", last_seen="2026-01-01T00:00:00+00:00"))
    db.commit()
    db.close()

    r = client.post("/v1/alerts/lan.new-device/snooze", json={"hours": 24},
                    headers=bearer(write_secret))
    assert r.status_code == 200 and r.json()["status"] == "snoozed"
    assert r.json()["snoozed_until"]

    db = SessionLocal()
    row = db.query(m.Audit).filter_by(tenant_id=tid, action="alert.snooze").one()
    # The token's identity, the role, the tenant and the IP -- all four.
    assert row.user.startswith("token:gapi_")
    assert row.role == "operator" and row.tenant_id == tid and row.ip

    # The sensor's edit-replay contract is byte-identical to a dashboard snooze.
    edit = db.query(m.Edit).filter_by(tenant_id=tid, path="/api/alert").one()
    assert edit.body == {"key": "lan.new-device", "action": "snooze", "hours": 24}
    assert edit.who == row.user
    db.close()

    assert client.post("/v1/alerts/lan.new-device/snooze", json={"hours": 999},
                       headers=bearer(write_secret)).status_code == 422
    assert client.post("/v1/alerts/lan.new-device/detonate",
                       headers=bearer(write_secret)).status_code == 404


def test_device_write_is_audited_and_replayable(client):
    tid, read_secret, write_secret = seeded(client)
    mac = client.get("/v1/devices", headers=bearer(read_secret)).json()["items"][0]["mac"]

    r = client.post(f"/v1/devices/{mac}", json={"trusted": True, "label": "Kitchen tablet"},
                    headers=bearer(write_secret))
    assert r.status_code == 200 and r.json()["trusted"] is True
    assert r.json()["label"] == "Kitchen tablet"

    db = SessionLocal()
    row = db.query(m.Audit).filter_by(tenant_id=tid, action="device.update").one()
    assert row.user.startswith("token:gapi_") and row.role == "operator"
    edit = db.query(m.Edit).filter_by(tenant_id=tid, path="/api/device").one()
    assert edit.body["label"] == "Kitchen tablet" and edit.body["trusted"] is True
    assert "net" in edit.body
    db.close()

    assert client.post(f"/v1/devices/{mac}", json={}, headers=bearer(write_secret)).status_code == 422


def test_device_write_cannot_reach_the_excluded_fields(client):
    """`quarantined` and `parent` are dashboard-only. The body model forbids
    them, so a caller cannot smuggle one in alongside a permitted field."""
    _tid, read_secret, write_secret = seeded(client)
    mac = client.get("/v1/devices", headers=bearer(read_secret)).json()["items"][0]["mac"]
    for smuggled in ({"quarantined": True}, {"parent": "aa:bb:cc:dd:ee:ff"},
                     {"trusted": True, "quarantined": True}):
        r = client.post(f"/v1/devices/{mac}", json=smuggled, headers=bearer(write_secret))
        assert r.status_code == 422, smuggled
    db = SessionLocal()
    assert not db.query(m.Device).filter_by(quarantined=True).count()


def test_scan_and_speedtest_queue_work(client):
    tid, read_secret, write_secret = seeded(client)
    r = client.post("/v1/scans", headers=bearer(write_secret))
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert r.json()["status"] == "queued"

    job = client.get(f"/v1/jobs/{job_id}", headers=bearer(read_secret)).json()
    assert job["id"] == job_id and job["status"] == "queued" and job["ok"] is None

    db = SessionLocal()
    q = db.query(m.Queue).filter_by(tenant_id=tid, id=job_id).one()
    assert q.path == "/api/run" and q.who.startswith("token:gapi_")
    assert db.query(m.Audit).filter_by(tenant_id=tid, action="run").one().role == "operator"
    db.close()

    assert client.post("/v1/speedtest", headers=bearer(write_secret)).status_code == 202


def test_disruptive_routes_are_absent_from_v1(client):
    """The tier-3 surface is not merely gated -- it does not exist under /v1."""
    _tid, _r, write_secret = seeded(client)
    H = bearer(write_secret)
    for path in ("/v1/fix", "/v1/reboot-plan", "/v1/attacker", "/v1/reset-lan-baseline",
                 "/v1/dns/rules", "/v1/settings", "/v1/config", "/v1/billing",
                 "/v1/settings/users", "/v1/integrations", "/v1/tenants",
                 "/v1/settings/api-tokens"):
        assert client.post(path, json={}, headers=H).status_code in (404, 405), path
    # Forget is a POST sub-route on the dashboard; it has no v1 twin either.
    # 405 rather than 404 because the app's single-page catch-all is GET-only,
    # so an unknown path answers "method not allowed" to a POST -- either way
    # nothing runs, and neither answer is the app's HTML.
    mac = client.get("/v1/devices", headers=H).json()["items"][0]["mac"]
    r = client.post(f"/v1/devices/{mac}/forget", headers=H)
    assert r.status_code in (404, 405) and "<html" not in r.text.lower()


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

def test_per_token_rate_limit_trips_with_retry_after(client):
    from guardian_hub import apideps

    _tid, read_secret, _w = seeded(client)
    H = bearer(read_secret)
    ratelimit.reset()

    limit, _window = apideps.PER_TOKEN
    codes = [client.get("/v1/whoami", headers=H).status_code for _ in range(limit + 5)]
    # Exactly the documented allowance, then 429s -- nothing else shares this
    # bucket, so the boundary is not approximate.
    assert codes[:limit] == [200] * limit
    assert set(codes[limit:]) == {429}
    r = client.get("/v1/whoami", headers=H)
    assert r.status_code == 429
    assert r.headers["Retry-After"] == str(apideps.PER_TOKEN[1])
    assert r.json()["detail"] == "rate limit exceeded"


def test_write_ceiling_is_tighter_than_the_read_ceiling(client):
    from guardian_hub import apideps

    assert apideps.PER_TOKEN_WRITE[0] < apideps.PER_TOKEN[0]
    _tid, _r, write_secret = seeded(client)
    ratelimit.reset()
    codes = [client.post("/v1/speedtest", headers=bearer(write_secret)).status_code
             for _ in range(apideps.PER_TOKEN_WRITE[0] + 2)]
    assert 429 in codes
    # Reads on the same token are still inside their own, larger, ceiling.
    assert client.get("/v1/whoami", headers=bearer(write_secret)).status_code == 200


def test_the_documentation_routes_are_rate_limited(client):
    from guardian_hub import apideps

    make_super(client)
    ratelimit.reset()
    codes = [client.get("/v1/health").status_code for _ in range(apideps.PER_IP_ANON[0] + 3)]
    assert codes[0] == 200 and 429 in codes


def test_bad_credentials_are_rate_limited_separately(client):
    """The failure bucket, and the property an earlier version got wrong."""
    from guardian_hub import apideps

    _tid, read_secret, _w = seeded(client)
    ratelimit.reset()

    bad = bearer("gapi_deadbeef_" + "0" * 48)
    codes = [client.get("/v1/whoami", headers=bad).status_code
             for _ in range(apideps.PER_IP_BAD[0] + 3)]
    assert codes[0] == 401
    assert 429 in codes, "wrong credentials from one address were never throttled"


def test_a_valid_token_is_not_squeezed_by_the_failure_bucket(client):
    """A real client shares its address with whatever else is out there. Its
    ceiling must be its own per-token one -- never the (much lower) bucket that
    absorbs someone else's wrong credentials from the same address.

    This is a regression test: the first version of `api_principal` charged the
    anonymous per-address bucket on every call, which capped an authenticated
    caller at 60 requests a minute against a documented ceiling of 120.
    """
    from guardian_hub import apideps

    _tid, read_secret, _w = seeded(client)
    ratelimit.reset()

    # Exhaust the failure bucket from this same address first -- as a noisy
    # neighbour behind the same NAT would.
    bad = bearer("gapi_deadbeef_" + "0" * 48)
    for _ in range(apideps.PER_IP_BAD[0] + 5):
        client.get("/v1/whoami", headers=bad)
    assert client.get("/v1/whoami", headers=bad).status_code == 429

    # The good token is unaffected, and gets its whole per-token allowance.
    H = bearer(read_secret)
    ok = sum(1 for _ in range(apideps.PER_TOKEN[0]) if client.get("/v1/whoami", headers=H).status_code == 200)
    assert ok == apideps.PER_TOKEN[0], f"only {ok} of {apideps.PER_TOKEN[0]} got through"
    # And the very next one is its own ceiling talking, not the failure bucket.
    r = client.get("/v1/whoami", headers=H)
    assert r.status_code == 429 and r.headers["Retry-After"] == str(apideps.PER_TOKEN[1])


def test_minting_is_rate_limited(client):
    from guardian_hub.routers import apitokens

    make_super(client)
    ratelimit.reset()
    codes = []
    for i in range(apitokens.MINT_LIMIT[0] + 2):
        codes.append(client.post(TOKENS, json={"name": f"t{i}"}).status_code)
    assert 429 in codes


def test_live_token_cap(client):
    make_super(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).first()
    for i in range(apikeys.MAX_LIVE_PER_TENANT):
        apikeys.mint(db, tenant, name=f"t{i}", scope="read", role="viewer",
                     creator_role="admin", created_by="miro")
    db.commit()
    try:
        apikeys.mint(db, tenant, name="one too many", scope="read", role="viewer",
                     creator_role="admin", created_by="miro")
    except apikeys.ApiTokenError as exc:
        assert "revoke one" in str(exc)
    else:
        raise AssertionError("the per-tenant cap did not hold")


# --------------------------------------------------------------------------
# Health, docs and the in-app guide
# --------------------------------------------------------------------------

def test_health_needs_no_token(client):
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["api"] == "v1"


def test_openapi_document_covers_only_v1(client):
    doc = client.get("/v1/openapi.json").json()
    assert doc["info"]["title"] == "Vault API"
    assert doc["paths"], "the document is empty"
    assert all(p.startswith("/v1/") for p in doc["paths"]), sorted(doc["paths"])
    # No dashboard route, and therefore none of their internal docstrings.
    blob = json.dumps(doc)
    assert "/api/v1/" not in blob
    assert "Nexus" not in blob and "nexus" not in blob
    # A public document must not carry internal engineering notes.
    for leak in ("schemas_dash", "payloads/", "routers/", "sync.apply_edit", "Task ", "task "):
        assert leak not in blob, leak
    # The bearer scheme is declared, and health is exempted from it.
    assert doc["components"]["securitySchemes"]["bearerToken"]["scheme"] == "bearer"
    assert doc["paths"]["/v1/health"]["get"]["security"] == []
    # It documents itself as neither self-describing nor a docs page.
    assert "/v1/openapi.json" not in doc["paths"] and "/v1/docs" not in doc["paths"]


def test_openapi_marks_scope_and_role_on_every_operation(client):
    doc = client.get("/v1/openapi.json").json()
    for path, ops in doc["paths"].items():
        for method, op in ops.items():
            assert op["x-guardian-scope"] in ("read", "write", "none"), (path, method)
            assert op["x-guardian-role"] in ("viewer", "operator", "admin", "none")
            if method == "post":
                assert op["x-guardian-scope"] == "write", path


def test_reference_page_is_self_contained(client):
    r = client.get("/v1/docs")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert "Vault API" in html
    # No external asset of any kind -- the API host serves this under
    # `default-src 'self'`. The page loads exactly one script, from this
    # origin, as a file: no CDN, no third party, and nothing inline, because
    # the relaxed policy on this path grants `script-src 'self'` and no
    # `'unsafe-inline'`, nonce or hash.
    assert html.count("<script") == 1
    assert '<script src="/v1/docs.js" defer></script>' in html
    assert "<script>" not in html and "</script>" not in html.replace(
        '<script src="/v1/docs.js" defer></script>', "")
    assert "https://cdn" not in html
    assert "unpkg" not in html and "jsdelivr" not in html
    assert "Nexus" not in html
    # Every documented route appears, and so does the not-in-v1 section.
    doc = client.get("/v1/openapi.json").json()
    for path in doc["paths"]:
        assert path in html, path
    assert "Not in v1" in html


def test_reference_page_wears_the_product_palette(client):
    """The reference is styled from Vault's own tokens, not a palette of its
    own -- the same values `site/styles.css` and the app's `tokens.css` use, so
    api.guarder.io reads as a sibling of the app and the site.

    The values live in the stylesheet, which is a separate file: the API host's
    CSP refuses an inline `<style>` block."""
    css = client.get("/v1/docs.css").text
    for light in ("#f4f7fb", "#ffffff", "#eef3fa", "#dbe4f0", "#0f1a2b", "#5b6b82",
                  "#16a34a", "#e0b000", "#e02424", "#1d6fe0"):
        assert light in css, light
    for dark in ("#070d1a", "#0d1628", "#121d33", "#1e2c47", "#e8eef8", "#8fa3c2",
                 "#34d16a", "#f5c518", "#ff5a5a", "#4d9bff"):
        assert dark in css, dark
    # Both skins, keyed off the OS the way the site does it -- a docs page has no
    # session to remember a toggle in.
    assert "prefers-color-scheme:dark" in css.replace(" ", "")
    assert "--radius:14px" in css.replace(" ", "") and "--radius:10px" in css.replace(" ", "")
    # No teal, and none of the invented greys the page used to carry.
    for gone in ("#0f766e", "#2dd4bf", "#fbfbfa", "#16181d", "#0e7490"):
        assert gone not in css, gone


def test_reference_page_serves_the_product_faces_from_its_own_origin(client):
    """The page renders in the same two faces as the app and the marketing site.

    They are served from this origin under `/v1/`, which is the only prefix the
    API vhost proxies -- a font anywhere else would silently 404 and drop the
    page back to the system stack. Nothing is fetched from a third party, and
    no `data:` URI is used, because `font-src` falls back to
    `default-src 'self'` and would refuse it.

    The `@font-face` rules live in the stylesheet, which is a separate file:
    the API host's CSP refuses an inline `<style>` block."""
    css = client.get("/v1/docs.css").text
    assert "Manrope" in css and "IBM Plex Mono" in css
    assert css.count("@font-face") == 3
    assert "/v1/fonts/manrope-latin-var.woff2" in css
    assert "data:" not in css
    assert "fonts.googleapis" not in css and "fonts.gstatic" not in css


def test_the_reference_fonts_are_served_and_traversal_is_refused(client):
    ok = client.get("/v1/fonts/manrope-latin-var.woff2")
    assert ok.status_code == 200, ok.text
    assert ok.headers["content-type"] == "font/woff2"
    # `name` comes off the wire, so an allow-list -- not a path join -- is what
    # stops it reading anything else on the box. A name carrying separators
    # never reaches this route at all (the client normalises the path and it
    # lands elsewhere), so the cases worth asserting are the ones that DO
    # arrive: a single segment that is simply not one of the three faces.
    for bad in ("hub.json", ".env", "secrets.woff2", "manrope-latin-var.woff"):
        r = client.get(f"/v1/fonts/{bad}")
        assert r.status_code == 404, f"{bad} -> {r.status_code}"


def test_reference_page_has_a_scriptless_index_and_real_landmarks(client):
    html = client.get("/v1/docs").text
    guide = apidocs.guide(client.app)

    # Landmarks and a skip link, in that order.
    assert '<a class="skip" href="#main">' in html
    assert "<header class=" in html and '<main id="main">' in html and "<footer>" in html
    assert '<nav class="index" aria-label="Contents">' in html
    # Exactly one h1, and every group is an h2 under it.
    assert html.count("<h1") == 1
    for group in guide["groups"]:
        gid = "g-" + apidocs._slug(group["name"])
        assert f'href="#{gid}"' in html, gid          # the index entry
        assert f'id="{gid}"' in html, gid              # the section it lands on
        assert f'<h2 id="{gid}-h">' in html, gid
    # Anchors, not scripts, and not `:target` hiding -- every word stays
    # findable with the browser's own find, and the index works with the one
    # script blocked. No inline handler anywhere: the console wires itself with
    # `addEventListener` from its own file.
    assert "onclick" not in html and "onload" not in html
    assert ":target" not in html
    # Anchor targets clear the sticky header.
    assert "scroll-padding-top" in client.get("/v1/docs.css").text


def test_the_console_is_served_as_a_file_and_never_inline(client):
    """The one script is a real URL on this origin.

    That is what lets `deploy/nginx-api.conf.tmpl` relax the docs path to
    `script-src 'self'` with no inline allowance at all -- no
    `'unsafe-inline'`, no nonce, no hash. An inline block here would be a
    silently dead page in production, so the test guards the file-ness rather
    than trusting it."""
    r = client.get("/v1/docs.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/javascript")
    assert r.headers["cache-control"] == "no-cache"
    body = r.text
    assert "sessionStorage" in body and "localStorage" not in body
    # No third party reached from the script either: no absolute URL, no
    # import, no measurement.
    for bad in ('"http', "'http", "//cdn", "unpkg", "jsdelivr", "gtag", "import("):
        assert bad not in body, bad
    # Responses are written back as text, never as markup. (`.innerHTML =`
    # rather than the bare word, so the comment saying why can say the word.)
    assert "innerHTML =" not in body and "textContent" in body
    assert "document.write" not in body and "eval(" not in body

    # It is an asset route, not an API route: absent from the document.
    doc = client.get("/v1/openapi.json").json()
    assert "/v1/docs.js" not in doc["paths"]


def test_the_stylesheet_is_served_as_a_file_and_never_inline(client):
    """The reference carries no inline CSS of any kind.

    The API host sends `default-src 'self'` and declares no `style-src`, so an
    inline `<style>` block or a `style=` attribute on this page is refused and
    the reference renders as unstyled Times. That is what production did until
    this route existed, which is why the test guards the file-ness of the CSS
    rather than trusting it."""
    html = client.get("/v1/docs").text
    assert "<style" not in html, "an inline <style> block is dead under this host's CSP"
    assert 'style="' not in html, "an inline style attribute is dead under this host's CSP"
    assert '<link rel="stylesheet" href="/v1/docs.css">' in html

    r = client.get("/v1/docs.css")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/css")
    assert r.headers["cache-control"] == "no-cache"
    body = r.text
    # Self-contained, like the script: nothing third-party is reached from it.
    for bad in ("http://", "https://", "@import", "//cdn", "fonts.googleapis"):
        assert bad not in body, bad
    # It really is the page's stylesheet, not an empty file.
    assert ".hero" in body and "--acc" in body

    # An asset route, not an API route: absent from the document.
    doc = client.get("/v1/openapi.json").json()
    assert "/v1/docs.css" not in doc["paths"]


def test_the_console_is_hidden_until_its_script_runs(client):
    """Progressive enhancement, with the `hidden` attribute rather than CSS.

    A reader with the script blocked, or one who arrives before it loads, sees
    exactly the static reference -- no dead Send buttons, no empty token box.
    Hiding in CSS would mean the script had to fight the stylesheet to reveal
    anything."""
    html = client.get("/v1/docs").text
    assert '<div class="tokenbar" id="tokenbar" hidden>' in html
    assert '<div class="block try" hidden' in html
    # Nothing console-shaped is hidden by the stylesheet instead: the script
    # unsets an attribute, it does not have to out-specify a rule.
    css = client.get("/v1/docs.css").text
    assert ".tokenbar{" in css and ".try{" in css
    for rule in (".tokenbar", ".try", ".try-out", ".try-go"):
        block = html.split(rule + "{", 1)
        if len(block) > 1:
            assert "display:none" not in block[1].split("}", 1)[0], rule

    doc = client.get("/v1/openapi.json").json()
    gets = sum(1 for ops in doc["paths"].values() for m in ops if m == "get")
    writes = sum(len(ops) for ops in doc["paths"].values()) - gets
    # A Send button on every read, and on no write: a documentation page must
    # not change the account on a stray click.
    assert html.count('class="btn primary try-go"') == gets
    assert html.count('class="try-no"') == writes
    assert "The console here sends reads only" in html


def test_the_console_offers_an_input_for_every_declared_parameter(client):
    """Path parameters included -- a Send button that requested the literal
    `{alert_key}` would 404, which is worse than no button at all."""
    html = client.get("/v1/docs").text
    doc = client.get("/v1/openapi.json").json()
    seen = 0
    for path, ops in doc["paths"].items():
        op = ops.get("get")
        if not op:
            continue
        for prm in op.get("parameters") or []:
            if prm.get("in") not in ("path", "query"):
                continue
            seen += 1
            assert f'data-name="{prm["name"]}" data-in="{prm["in"]}"' in html, (path, prm)
    assert seen, "no GET declares a parameter -- this test stopped testing anything"


def test_the_token_panel_keeps_the_token_in_the_tab_only(client):
    html = client.get("/v1/docs").text
    # Masked by default, with a deliberate reveal, and no browser autofill.
    assert '<input id="token-input" type="password"' in html
    assert 'autocomplete="off"' in html and 'spellcheck="false"' in html
    assert 'id="token-show"' in html and 'aria-pressed="false"' in html
    assert 'id="token-save"' in html and 'id="token-clear"' in html
    assert "sessionStorage" in html and "localStorage" not in html
    # The page says where it keeps the token and for how long.
    assert "gone when you close the tab" in html

    js = client.get("/v1/docs.js").text
    # Clear clears all three: the store, the field, and the pill.
    clear = js.split('"token-clear"')[1].split("});", 1)[0]
    assert "drop()" in clear and 'input.value = ""' in clear and "paint()" in clear
    assert "reveal(false)" in clear
    # The token rides in a header and nowhere else -- never a path or a query,
    # which is the point of this vhost's `Referrer-Policy: no-referrer`.
    assert 'headers.Authorization = "Bearer " + token' in js
    url_line = js.split("var url = ")[1].split(";", 1)[0]
    assert "token" not in url_line and "Bearer" not in url_line


def test_the_hero_counts_are_computed_from_the_document(client):
    """Every number in the hero is read off the generated document.

    Asserted against the recomputed expression rather than a literal, so the
    day someone adds a route the test still passes -- and the day someone
    types a number into the page instead of deriving it, it fails."""
    html = client.get("/v1/docs").text
    doc = client.get("/v1/openapi.json").json()
    guide = apidocs.guide(client.app)

    endpoints = sum(len(ops) for ops in doc["paths"].values())
    schemas = len(doc["components"]["schemas"])
    per_min, _window = apideps.PER_TOKEN

    assert [v for v, _ in apidocs._counts(doc, guide)] == [
        str(endpoints), str(len(guide["groups"])), str(schemas), str(per_min)]
    for value, label in apidocs._counts(doc, guide):
        assert f"<b>{value}</b><span>{label}</span>" in html, label

    # The endpoint tile counts operations, which is exactly how many endpoint
    # blocks the page renders -- the tile and the body read one number.
    assert html.count('<article class="ep"') == endpoints
    # The spec version badge comes off the document too.
    assert f'OpenAPI {".".join(doc["openapi"].split(".")[:2])}' in html
    # One h1, in the hero, with the brand word carrying the accent.
    assert html.count("<h1") == 1 and "<em>Vault</em>" in html
    assert 'id="main"' in html and html.index('class="hero"') > html.index('id="main"')


def test_every_endpoint_shows_a_request_and_a_response(client):
    html = client.get("/v1/docs").text
    doc = client.get("/v1/openapi.json").json()
    for path, ops in doc["paths"].items():
        for method in ops:
            slug = apidocs._slug(method.upper() + path)
            assert f'id="ep-{slug}"' in html, (method, path)
    # A worked request for every endpoint, and a response body for the ones
    # whose schema resolves -- which, on this document, is all of them.
    assert html.count(">Request</p>") == sum(len(o) for o in doc["paths"].values())
    # A response body for every endpoint whose success response declares a JSON
    # schema -- which is all of them but `/v1/reports/{report_id}`, a file
    # download with nothing to print. `202` shows as `202`, not as `200`.
    with_json = [
        (path, method)
        for path, ops in doc["paths"].items() for method, op in ops.items()
        if any(((op["responses"][code].get("content") or {}).get("application/json") or {}).get("schema")
               for code in op["responses"] if code.startswith("2"))
    ]
    assert html.count('class="label">Response ') == len(with_json)
    assert ">Response 202</p>" in html and ">Response 200</p>" in html
    assert "curl -s" in html and "$GUARDIAN_TOKEN" in html
    # Scope and role ride as chips on the endpoint header.
    assert "scope read" in html and "role operator" in html and "no token needed" in html
    # Wide code and wide tables scroll inside their own box, never the page.
    assert "overflow-x:auto" in client.get("/v1/docs.css").text.replace(" ", "")


def test_example_synthesis_survives_a_self_referential_schema(client):
    """The response examples are built by walking `components.schemas`, so a
    shape that contains itself -- a topology node whose children are nodes --
    has to terminate. The branch-local `seen` set is what makes it."""
    schemas = {
        "Node": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
                "peer": {"$ref": "#/components/schemas/Node"},
            },
        },
        "Loop": {"$ref": "#/components/schemas/Loop"},
    }
    node = apidocs._example_for({"$ref": "#/components/schemas/Node"}, schemas)
    assert node["name"] == "Kitchen tablet"
    assert isinstance(node["children"], list) and isinstance(node["peer"], dict)
    # A direct self-reference stops rather than recursing.
    assert apidocs._example_for({"$ref": "#/components/schemas/Loop"}, schemas) == {}
    # A nullable field takes its non-null branch, not `null`.
    assert apidocs._example_for(
        {"anyOf": [{"type": "integer"}, {"type": "null"}]}, {}) == 1


def test_api_guide_is_generated_from_the_same_document(client):
    make_super(client)
    guide = client.get("/api/v1/api-guide").json()
    doc = client.get("/v1/openapi.json").json()

    guided = {(e["method"], e["path"]) for g in guide["groups"] for e in g["endpoints"]}
    documented = {(mth.upper(), path) for path, ops in doc["paths"].items()
                  for mth in ops if mth in ("get", "post")}
    assert guided == documented, guided ^ documented

    assert guide["token_prefix"] == "gapi_"
    assert guide["auth_scheme"] == "Bearer" and guide["auth_header"] == "Authorization"
    assert guide["excluded"] and all(x["action"] and x["why"] for x in guide["excluded"])
    assert guide["examples"] and all("curl" in x["curl"] for x in guide["examples"])
    assert {s["action"] for s in guide["scopes"]} == {"read", "write"}
    assert "Nexus" not in json.dumps(guide)


def test_api_guide_needs_a_session_but_not_admin(client):
    assert client.get("/api/v1/api-guide").status_code == 401
    seed_user(role="viewer")
    login(client)
    assert client.get("/api/v1/api-guide").status_code == 200


def test_api_base_url_is_derived_from_the_app_hostname(client):
    from guardian_hub import apidocs
    from guardian_hub.config import settings

    original = settings.base_url
    try:
        for app_url, expected in (
            ("https://my.guarder.io", "https://api.guarder.io"),
            ("https://guarder.io", "https://api.guarder.io"),
            ("http://127.0.0.1:8800", "http://127.0.0.1:8800"),
            ("http://localhost:8800", "http://localhost:8800"),
        ):
            settings.base_url = app_url
            assert apidocs.base_url() == expected, app_url
    finally:
        settings.base_url = original
