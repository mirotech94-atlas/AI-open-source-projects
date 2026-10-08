"""The shared tenant policy: what a new organization starts with, what the
hub must never hand it, and the drift guard against the sensor's own defaults.

The parity test is the important one, for the same reason `test_remedy.py`'s
is: `guardian_hub/tenant_defaults.POLICY` restates values that live in
`sensor/guardian_sensor/config.py`, and the hub cannot import
`guardian_sensor` (it is the sensor's own package, installed on the sensor
host). So the sensor file is read as *source* and compared here -- someone
retuning a sensor default gets a red test asking whether the tenant default
moves with it, instead of a silent divergence.
"""

import ast
import pathlib

from tests.test_auth import login, seed_user
from guardian_hub import cli, models as m, tenant_defaults
from guardian_hub.db import SessionLocal

SENSOR_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "sensor" / "guardian_sensor" / "config.py"

#: The only two values that deliberately differ from the sensor's defaults --
#: WEME's own, stricter, thresholds. Asserted by value below so that adopting
#: a third override has to be written down here as well.
OVERRIDES = {"disk_pct_warn": (70, 80), "mem_pct_warn": (80, 90)}


def _sensor_defaults() -> dict:
    """`DEFAULTS` out of the sensor's config.py, parsed rather than imported:
    the sensor package is not in the hub's venv and must not be."""
    tree = ast.parse(SENSOR_CONFIG.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("sensor config.py no longer defines a literal DEFAULTS")


def test_policy_still_matches_the_sensor_defaults_except_the_two_overrides():
    sensor = _sensor_defaults()
    for block, values in tenant_defaults.POLICY.items():
        assert block in sensor, f"{block} is not a sensor DEFAULTS block"
        assert set(values) == set(sensor[block]), f"{block} keys drifted from the sensor"
        for key, value in values.items():
            if block == "thresholds" and key in OVERRIDES:
                continue
            assert value == sensor[block][key], f"{block}.{key} drifted from the sensor default"


def test_the_two_overrides_are_exactly_the_documented_ones():
    sensor = _sensor_defaults()
    for key, (ours, theirs) in OVERRIDES.items():
        assert tenant_defaults.POLICY["thresholds"][key] == ours
        assert sensor["thresholds"][key] == theirs, (
            f"the sensor's {key} moved; decide whether the tenant default moves too")


def test_wemes_other_deltas_are_deliberately_not_defaults():
    # Each of these is WEME's value inverted on purpose, and the spec rules on
    # why: DNS filtering would make a customer's sensor their LAN resolver
    # unasked, the probe host is WEME's own retired machine, and local_only is
    # state the hub derives when a tenant's first Integration row appears.
    assert tenant_defaults.POLICY["dns"]["enabled"] is False
    assert tenant_defaults.POLICY["home"]["external_probe_host"] == ""
    assert tenant_defaults.POLICY["notify"]["local_only"] is False


def test_new_tenant_config_is_a_fresh_deep_copy():
    # Shared nesting would mean one owner's tuned threshold reaching every
    # other tenant created in the same process.
    a, b = tenant_defaults.new_tenant_config(), tenant_defaults.new_tenant_config()
    a["thresholds"]["disk_pct_warn"] = 55
    a["dns"]["upstreams"].append("https://example.invalid/dns-query")
    assert b["thresholds"]["disk_pct_warn"] == 70
    assert b["dns"]["upstreams"] == tenant_defaults.POLICY["dns"]["upstreams"]
    assert tenant_defaults.POLICY["thresholds"]["disk_pct_warn"] == 70


# --- the three creation paths ------------------------------------------------

POLICY_BLOCKS = {"thresholds", "auto_fix", "tailscale", "dns", "home", "notify"}


def _assert_policy_only(config: dict):
    """A created tenant's config is the policy and *nothing else*.

    The equality is the disclosure guard, not the named absences below it: a
    key added to the shared policy by way of copying somebody's real config
    would be caught by the set comparison even though no one thought to name
    it. `ssh_hosts` is spelled out because it holds SSH *private keys* --
    WEME's config has them, and a new customer's config must never be built by
    reading his.
    """
    assert set(config) == POLICY_BLOCKS
    for leaked in ("ssh_hosts", "sites", "checks", "lan", "profile", "networks", "heartbeats"):
        assert leaked not in config
    assert config["thresholds"]["disk_pct_warn"] == 70
    assert config["dns"]["enabled"] is False


def _make_super(client):
    uid, tid = seed_user()
    db = SessionLocal(); db.get(m.User, uid).is_superadmin = True; db.commit()
    login(client)
    return tid


def test_cli_tenant_add_starts_on_the_shared_policy(client, capsys):
    cli.main(["tenant", "add", "acme", "Acme Dental"])
    capsys.readouterr()
    t = SessionLocal().query(m.Tenant).filter_by(slug="acme").first()
    _assert_policy_only(t.config)


def test_tenants_router_create_starts_on_the_shared_policy(client):
    _make_super(client)
    r = client.post("/api/v1/tenants", json={"slug": "acme", "name": "Acme Dental"})
    assert r.status_code == 200
    _assert_policy_only(r.json()["config"])


def test_settings_router_create_starts_on_the_shared_policy(client):
    _make_super(client)
    r = client.post("/api/v1/settings/tenants", json={"slug": "acme", "name": "Acme Dental"})
    assert r.status_code == 200
    t = SessionLocal().query(m.Tenant).filter_by(slug="acme").first()
    _assert_policy_only(t.config)


# --- applying the policy to a tenant that already exists ---------------------

def test_apply_defaults_fills_gaps_and_keeps_what_the_owner_set(client, capsys):
    db = SessionLocal()
    db.add(m.Tenant(slug="old", name="Old Co", config={
        # A tuned threshold, a half-populated block, and network data that
        # must survive untouched.
        "thresholds": {"disk_pct_warn": 60},
        "ssh_hosts": [{"name": "prod", "key": "-----BEGIN OPENSSH PRIVATE KEY-----"}],
    }))
    db.commit()

    cli.main(["tenant", "apply-defaults", "old"])
    out = capsys.readouterr().out
    cfg = SessionLocal().query(m.Tenant).filter_by(slug="old").first().config

    assert cfg["thresholds"]["disk_pct_warn"] == 60      # the owner's value wins
    assert cfg["thresholds"]["mem_pct_warn"] == 80       # per-leaf: the block existed, the key did not
    assert POLICY_BLOCKS <= set(cfg)
    assert cfg["ssh_hosts"][0]["name"] == "prod"         # its own data is left alone
    assert "= thresholds.disk_pct_warn (kept)" in out
    assert "+ thresholds.mem_pct_warn" in out
    assert "added" in out


def test_apply_defaults_dry_run_writes_nothing(client, capsys):
    db = SessionLocal()
    db.add(m.Tenant(slug="old", name="Old Co", config={"thresholds": {"disk_pct_warn": 60}}))
    db.commit()

    cli.main(["tenant", "apply-defaults", "old", "--dry-run"])
    out = capsys.readouterr().out
    assert "nothing changed" in out
    assert SessionLocal().query(m.Tenant).filter_by(slug="old").first().config == {
        "thresholds": {"disk_pct_warn": 60}}


def test_apply_defaults_is_idempotent_and_says_so(client, capsys):
    cli.main(["tenant", "add", "acme", "Acme Dental"])
    cli.main(["tenant", "apply-defaults", "acme"])
    out = capsys.readouterr().out
    assert "already has every policy key" in out
    t = SessionLocal().query(m.Tenant).filter_by(slug="acme").first()
    _assert_policy_only(t.config)


# --- the legacy spelling collision -------------------------------------------

def test_apply_defaults_announces_a_threshold_it_would_supersede(client, capsys):
    """The demo tenant's shape: bare threshold names only.

    Adding the canonical `disk_pct_warn` does not fill a gap here -- it takes
    over from `disk_pct`, because `payloads/assets.py` reads the canonical
    name first. The command has to say the effective value is moving, or an
    owner opting in would find the public demo warning at 70% instead of 90%
    with nothing in the output to explain it.
    """
    db = SessionLocal()
    db.add(m.Tenant(slug="legacy", name="Legacy",
                    config={"thresholds": {"disk_pct": 90, "mem_pct": 90}}))
    db.commit()

    cli.main(["tenant", "apply-defaults", "legacy", "--dry-run"])
    out = capsys.readouterr().out
    assert "disk_pct_warn=70 supersedes disk_pct=90" in out
    assert "the effective threshold changes from 90 to 70" in out
    assert "mem_pct_warn=80 supersedes mem_pct=90" in out


def test_no_supersede_warning_when_the_canonical_name_is_already_there(client, capsys):
    db = SessionLocal()
    db.add(m.Tenant(slug="canon", name="Canon",
                    config={"thresholds": {"disk_pct": 90, "disk_pct_warn": 65}}))
    db.commit()

    cli.main(["tenant", "apply-defaults", "canon", "--dry-run"])
    out = capsys.readouterr().out
    assert "supersedes disk_pct" not in out   # 65 is the owner's, and it stays
    assert "= thresholds.disk_pct_warn (kept)" in out
