import json
import os

from guardian_sensor import config

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "config.json")


def _write(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_defaults(tmp_path):
    (tmp_path / "config.json").write_text('{"sites":[],"ssh_hosts":[]}')
    c = config.load(tmp_path / "config.json")
    assert c["thresholds"]["cert_days_warn"] == 21
    assert c["auto_fix"]["enabled"] is True
    assert c["lan"]["enabled"] is True


def test_defaults_cover_every_top_level_key(tmp_path):
    c = config.load(_write(tmp_path, {}))
    for key in (
        "sites", "ssh_hosts", "lan", "tailscale", "thresholds",
        "home", "auto_fix", "heartbeats", "checks", "dns",
    ):
        assert key in c


def test_dns_defaults_to_disabled(tmp_path):
    c = config.load(_write(tmp_path, {}))
    assert c["dns"]["enabled"] is False


def test_auto_fix_defaults(tmp_path):
    c = config.load(_write(tmp_path, {}))
    assert c["auto_fix"]["max_per_day"] == 30
    assert c["auto_fix"]["attacker_method"] == "auto"


def test_empty_list_and_dict_defaults(tmp_path):
    c = config.load(_write(tmp_path, {}))
    assert c["sites"] == []
    assert c["ssh_hosts"] == []
    assert c["checks"] == []
    assert c["heartbeats"] == {}


def test_user_values_are_not_overwritten(tmp_path):
    c = config.load(_write(tmp_path, {"thresholds": {"cert_days_warn": 99}}))
    assert c["thresholds"]["cert_days_warn"] == 99
    # other threshold defaults still merged in
    assert c["thresholds"]["cert_days_crit"] == 7


def test_load_against_real_fixture():
    c = config.load(FIXTURE)
    assert c["thresholds"]["cert_days_warn"] == 21
    assert c["thresholds"]["disk_pct_warn"] == 80
    assert c["auto_fix"]["max_per_day"] == 30


def test_load_default_path_uses_paths_p(tmp_path, monkeypatch):
    from guardian_sensor import paths

    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    _write(tmp_path, {"sites": []})
    c = config.load()
    assert c["sites"] == []
    assert c["thresholds"]["cert_days_warn"] == 21
