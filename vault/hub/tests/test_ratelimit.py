from guardian_hub import mailer, ratelimit
from tests.test_auth import seed_user
from tests.test_tenants import make_super


# ---------------- unit: ratelimit.allow ----------------

def test_allow_permits_up_to_limit_then_blocks(client):
    ratelimit.reset()
    key = "unit:k1"
    assert all(ratelimit.allow(key, 3, 60) for _ in range(3))
    assert ratelimit.allow(key, 3, 60) is False


def test_allow_is_per_key(client):
    ratelimit.reset()
    assert ratelimit.allow("unit:a", 1, 60) is True
    assert ratelimit.allow("unit:a", 1, 60) is False
    assert ratelimit.allow("unit:b", 1, 60) is True


def test_allow_window_expiry_frees_up_slots():
    ratelimit.reset()
    key = "unit:expiry"
    assert ratelimit.allow(key, 1, 0) is True
    # window_s=0: the very next call sees the first hit as already expired
    assert ratelimit.allow(key, 1, 0) is True


# ---------------- POST /auth/start 429 ----------------

def test_auth_start_rate_limited_per_username(client, monkeypatch):
    seed_user(username="miro", email="m@example.com")
    monkeypatch.setattr("guardian_hub.routers.auth.AUTH_START_PER_USER", (3, 600))
    for _ in range(3):
        r = client.post("/api/v1/auth/start", json={"username": "miro"})
        assert r.status_code == 200
    r = client.post("/api/v1/auth/start", json={"username": "miro"})
    assert r.status_code == 429


def test_auth_start_rate_limited_per_ip_across_usernames(client, monkeypatch):
    seed_user(username="miro", email="m@example.com")
    seed_user(username="ann", email="ann@x.com", tenant_slug="acme")
    monkeypatch.setattr("guardian_hub.routers.auth.AUTH_START_PER_IP", (2, 600))
    assert client.post("/api/v1/auth/start", json={"username": "miro"}).status_code == 200
    assert client.post("/api/v1/auth/start", json={"username": "ann"}).status_code == 200
    r = client.post("/api/v1/auth/start", json={"username": "miro"})
    assert r.status_code == 429


def test_auth_start_still_works_under_the_limit(client):
    seed_user(username="miro", email="m@example.com")
    r = client.post("/api/v1/auth/start", json={"username": "miro"})
    assert r.status_code == 200 and r.json() == {"sent": True}
    assert mailer.LAST_CODES["m@example.com"]


# ---------------- POST /sensor/enrol 429 ----------------

def test_sensor_enrol_rate_limited_per_ip(client, monkeypatch):
    tid = make_super(client)
    monkeypatch.setattr("guardian_hub.routers.sensor.ENROL_PER_IP", (2, 600))

    code1 = client.post(f"/api/v1/tenants/{tid}/enrol-code").json()["code"]
    r1 = client.post("/api/v1/sensor/enrol", json={"code": code1, "name": "a", "platform": "linux", "version": "1"})
    assert r1.status_code == 200

    code2 = client.post(f"/api/v1/tenants/{tid}/enrol-code").json()["code"]
    r2 = client.post("/api/v1/sensor/enrol", json={"code": code2, "name": "b", "platform": "linux", "version": "1"})
    assert r2.status_code == 200

    code3 = client.post(f"/api/v1/tenants/{tid}/enrol-code").json()["code"]
    r3 = client.post("/api/v1/sensor/enrol", json={"code": code3, "name": "c", "platform": "linux", "version": "1"})
    assert r3.status_code == 429
