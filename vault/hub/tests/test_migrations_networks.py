"""`0007_networks` on a real 0006-shape database (Task 51 fix round 1).

Nothing here touches the test app or `conftest`'s engine: the migration is run
by the alembic CLI in a SUBPROCESS against its own temp SQLite file, because
`guardian_hub.db` binds `GUARDIAN_DATABASE_URL` once at import and because
`hub/alembic/` shadows the installed `alembic` package for anything running with
`hub/` as its working directory (so the CLI is invoked from the repo root, the
same way `guardian migrate` reaches it through `Config(hub/alembic.ini)`).

A fresh DB is brought to 0006 by MIGRATING (`upgrade 0006_integration_secrets`)
rather than by `create_all` + `stamp`: `create_all` would build the 0007 shape
and the assertions below would prove nothing.

Covered: the forward data steps (one primary per tenant, zero unattached rows in
all five scoped tables, LAN key rewrite, `incident.sensor_id` backfilled only
where a tenant has exactly one sensor) and -- the fix this round is about -- a
`downgrade` of a two-network tenant whose two networks each hold a LAN alert for
the SAME MAC, plus duplicate device and presence rows for that MAC, which used
to die on `UNIQUE(alert.tenant_id, key)` half-way through the rollback.
"""

import json
import os
import pathlib
import sqlite3
import subprocess
import sys

import pytest

HUB = pathlib.Path(__file__).resolve().parents[1]
REPO = HUB.parent
INI = "hub/alembic.ini"
BASE = "0006_integration_secrets"

SCOPED = ("device", "event", "alert", "dns_query", "presence_sample")


def _alembic(db_path: pathlib.Path, *args: str) -> None:
    env = dict(os.environ)
    env["GUARDIAN_DATABASE_URL"] = f"sqlite:///{db_path}"
    env.setdefault("GUARDIAN_SECRET_KEY", "test-secret")
    r = subprocess.run([sys.executable, "-m", "alembic", "-c", INI, *args],
                       cwd=REPO, env=env, capture_output=True, text=True)
    assert r.returncode == 0, f"alembic {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}"


def _conn(db_path: pathlib.Path) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def _tenant(c, tid, slug, subnet):
    c.execute("INSERT INTO tenant (id, slug, name, brand, ntfy_topic, config, settings, created)"
              " VALUES (?,?,?,'{}','t',?,'{}','2026-01-01 00:00:00')",
              (tid, slug, slug.title(), json.dumps({
                  "lan": {"name": f"{slug} network", "subnet": subnet,
                          "expected_gateway": subnet.split("/")[0].rsplit(".", 1)[0] + ".1"},
                  "sites": [{"url": "https://x"}],
              })))


def _sensor(c, sid, tid, name):
    c.execute("INSERT INTO sensor (id, tenant_id, name, platform, version, status)"
              " VALUES (?,?,?,'linux','0.1.0','online')", (sid, tid, name))


def _device(c, did, tid, mac, ip):
    c.execute("INSERT INTO device (id, tenant_id, mac, ip, name, vendor, kind, label, trusted,"
              " always_on, quarantined, risk, presence, ports, first_seen, last_seen, extra)"
              " VALUES (?,?,?,?,'','','unknown','',0,0,0,0,'ONLINE','{}','t0','t1','{}')",
              (did, tid, mac, ip))


def _alert(c, aid, tid, key):
    c.execute("INSERT INTO alert (id, tenant_id, key, sev, msg, why, evidence, confidence,"
              " first_seen, last_seen, count, status, resolved_by, fix, ticket_key)"
              " VALUES (?,?,?,'WARN','m','w','{}','high','t0','t1',1,'open','','','')",
              (aid, tid, key))


def _fixture(db_path: pathlib.Path) -> dict:
    """A 0006-shape DB: tenant A with TWO sensors, tenant B with one."""
    _alembic(db_path, "upgrade", BASE)
    c = _conn(db_path)
    _tenant(c, "ta", "alpha", "192.168.1.0/24")
    _tenant(c, "tb", "bravo", "10.0.0.0/24")
    _sensor(c, "sa1", "ta", "a1")
    _sensor(c, "sa2", "ta", "a2")
    _sensor(c, "sb1", "tb", "b1")
    _device(c, "d1", "ta", "aa:bb:cc:00:00:01", "192.168.1.10")
    _device(c, "d2", "ta", "aa:bb:cc:00:00:02", "192.168.1.11")
    _device(c, "d3", "tb", "aa:bb:cc:00:00:01", "10.0.0.9")
    for tid, n in (("ta", 3), ("tb", 2)):
        for i in range(n):
            c.execute("INSERT INTO event (tenant_id, ts, type, entity, payload)"
                      " VALUES (?,?,'lan','e','{}')", (tid, f"2026-01-0{i + 1}T00:00:00Z"))
            c.execute("INSERT INTO dns_query (tenant_id, ts, client, name, qtype, blocked, ms)"
                      " VALUES (?,?,'192.168.1.10','x.example','A',0,3)",
                      (tid, f"2026-01-0{i + 1}T00:00:00Z"))
            c.execute("INSERT INTO presence_sample (tenant_id, ts, mac)"
                      " VALUES (?,?,'aa:bb:cc:00:00:01')", (tid, f"2026-01-0{i + 1}T00:00:00Z"))
    _alert(c, "a1", "ta", "lan:new:aa:bb:cc:00:00:01")
    _alert(c, "a2", "ta", "sec:ssh:host")
    _alert(c, "a3", "tb", "lan:missing:aa:bb:cc:00:00:01")
    for tid, sid in (("ta", "i1"), ("tb", "i2")):
        c.execute("INSERT INTO incident (id, tenant_id, iid, entity, sev, story, actions,"
                  " alert_keys, opened) VALUES (?,?,'100','e','WARN','s','[]','[]','t0')",
                  (sid, tid))
    c.commit()
    counts = {t: {r[0]: r[1] for r in c.execute(
        f"SELECT tenant_id, COUNT(*) FROM {t} GROUP BY tenant_id").fetchall()}
        for t in SCOPED}
    c.close()
    return counts


@pytest.fixture()
def db(tmp_path) -> pathlib.Path:
    return tmp_path / "m.db"


def test_0007_upgrade_attaches_every_row_and_rewrites_lan_keys(db):
    before = _fixture(db)
    _alembic(db, "upgrade", "head")
    c = _conn(db)

    nets = {r["tenant_id"]: dict(r) for r in c.execute("SELECT * FROM network").fetchall()}
    assert set(nets) == {"ta", "tb"}
    for tid, n in nets.items():
        assert n["slug"] == "primary" and n["is_primary"] == 1 and n["kind"] == "home"
    assert nets["ta"]["subnet_prefix"] == "192.168.1" and nets["tb"]["subnet_prefix"] == "10.0.0"
    # Only tenant B has exactly one sensor, so only it gets a sensor on its
    # network and on its incidents.
    assert nets["tb"]["sensor_id"] == "sb1" and nets["ta"]["sensor_id"] is None
    assert nets["tb"]["status"] == "active" and nets["ta"]["status"] == "no-sensor"

    for t in SCOPED:
        assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE network_id IS NULL").fetchone()[0] == 0, t
        after = {r[0]: r[1] for r in c.execute(
            f"SELECT tenant_id, COUNT(*) FROM {t} GROUP BY tenant_id").fetchall()}
        assert after == before[t], t          # nothing lost, nothing duplicated
        bad = c.execute(f"SELECT COUNT(*) FROM {t} tt JOIN network n ON n.id = tt.network_id"
                        " WHERE n.tenant_id != tt.tenant_id").fetchone()[0]
        assert bad == 0, t                    # and nothing crossed tenants

    keys = {r["id"]: r["key"] for r in c.execute("SELECT id, key FROM alert").fetchall()}
    assert keys["a1"] == "lan:primary:new:aa:bb:cc:00:00:01"
    assert keys["a3"] == "lan:primary:missing:aa:bb:cc:00:00:01"
    assert keys["a2"] == "sec:ssh:host", "non-LAN keys are untouched"

    inc = {r["id"]: r["sensor_id"] for r in c.execute("SELECT id, sensor_id FROM incident").fetchall()}
    assert inc == {"i1": None, "i2": "sb1"}
    c.close()


def test_0007_downgrade_survives_two_networks_clashing_on_one_mac(db):
    """The Critical-1 regression: two networks each holding a LAN alert for the
    same MAC both strip to one legacy key, and `alert` has
    `UNIQUE(tenant_id, key)`. Same for the duplicate device and presence rows
    the returning uniques cannot hold. The rollback must complete regardless,
    keeping the primary's rows and every event/DNS row."""
    _fixture(db)
    _alembic(db, "upgrade", "head")

    c = _conn(db)
    prim = c.execute("SELECT id FROM network WHERE tenant_id = 'ta'").fetchone()["id"]
    c.execute("INSERT INTO network (id, tenant_id, name, slug, kind, subnet_prefix,"
              " expected_gateway, ssid, colour, is_primary, status, created)"
              " VALUES ('nlab','ta','Lab','lab','lab','10.10.0','','','',0,'active',"
              "'2026-02-01 00:00:00')")
    mac = "aa:bb:cc:00:00:01"
    # The same MAC on the second network: a device row, a presence sample at a ts
    # the primary already has, a LAN alert whose legacy form is already taken,
    # plus history that must SURVIVE the rollback.
    _device(c, "d9", "ta", mac, "10.10.0.5")
    c.execute("UPDATE device SET network_id = 'nlab' WHERE id = 'd9'")
    c.execute("INSERT INTO presence_sample (tenant_id, network_id, ts, mac)"
              " VALUES ('ta','nlab','2026-01-01T00:00:00Z',?)", (mac,))
    _alert(c, "a9", "ta", f"lan:lab:new:{mac}")
    c.execute("UPDATE alert SET network_id = 'nlab' WHERE id = 'a9'")
    c.execute("INSERT INTO event (tenant_id, network_id, ts, type, entity, payload)"
              " VALUES ('ta','nlab','2026-02-01T00:00:00Z','lan','e','{}')")
    c.execute("INSERT INTO dns_query (tenant_id, network_id, ts, client, name, qtype, blocked, ms)"
              " VALUES ('ta','nlab','2026-02-01T00:00:00Z','10.10.0.5','y.example','A',0,3)")
    c.commit()
    events = c.execute("SELECT COUNT(*) FROM event").fetchone()[0]
    dns = c.execute("SELECT COUNT(*) FROM dns_query").fetchone()[0]
    c.close()

    _alembic(db, "downgrade", BASE)            # must not raise -- this is the fix

    c = _conn(db)
    assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'network'").fetchone()[0] == 0
    for t in ("device", "event", "alert", "dns_query", "presence_sample", "incident"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({t})").fetchall()}
        assert "network_id" not in cols and (t != "incident" or "sensor_id" not in cols), t
    # event/dns history is reassigned, never deleted (S3.1 ruling a).
    assert c.execute("SELECT COUNT(*) FROM event").fetchone()[0] == events
    assert c.execute("SELECT COUNT(*) FROM dns_query").fetchone()[0] == dns
    # …while the rows the returning uniques cannot hold are de-duplicated, and
    # the survivor is the PRIMARY network's row.
    devs = c.execute("SELECT id, ip FROM device WHERE tenant_id = 'ta' AND mac = ?", (mac,)).fetchall()
    assert [r["id"] for r in devs] == ["d1"] and devs[0]["ip"] == "192.168.1.10"
    assert c.execute("SELECT COUNT(*) FROM presence_sample WHERE tenant_id = 'ta'"
                     " AND ts = '2026-01-01T00:00:00Z' AND mac = ?", (mac,)).fetchone()[0] == 1
    keys = {r["key"] for r in c.execute("SELECT key FROM alert WHERE tenant_id = 'ta'").fetchall()}
    assert keys == {f"lan:new:{mac}", "sec:ssh:host"}, keys
    assert c.execute("SELECT COUNT(*) FROM alert WHERE id = 'a1'").fetchone()[0] == 1, \
        "the primary's alert is the one that survives the key clash"
    c.close()

    # …and the forward migration is clean again on the rolled-back DB.
    _alembic(db, "upgrade", "head")
    c = _conn(db)
    for t in SCOPED:
        assert c.execute(f"SELECT COUNT(*) FROM {t} WHERE network_id IS NULL").fetchone()[0] == 0, t
    assert c.execute("SELECT COUNT(*) FROM network").fetchone()[0] == 2
    c.close()
