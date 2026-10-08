from guardian_sensor import sync


def test_dns_rules_edit_updates_config_only():
    cfg, st = {"dns": {"enabled": True}}, {}
    touched = sync.apply_edit({"path": "/api/dns/rules",
                               "body": {"allow": ["ok.com"], "block": ["ads.example.com"]}}, st, cfg)
    assert touched == "config"
    assert cfg["dns"]["block"] == ["ads.example.com"] and cfg["dns"]["allow"] == ["ok.com"]
    assert st == {}
