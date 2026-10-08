"""An /api/config edit must never destroy config the hub does not own.

The hub's `tenant.config` holds only what the dashboard manages (auto_fix,
dns); sites and ssh hosts live in their own tables. Replacing the local
config with it wiped the sensor's own targets — scans went out with
`sites: []` and `ssh: []`, and every target aged into "not checked
recently".
"""
from guardian_sensor.sync import apply_edit


def _edit(config):
    return {"id": 1, "path": "/api/config", "body": {"config": config}}


def test_keys_the_hub_does_not_send_survive():
    cfg = {
        "sites": [{"name": "Roots", "url": "https://status.example.org/health"}],
        "ssh_hosts": [{"name": "guardian-hub", "host": "203.0.113.10"}],
        "thresholds": {"http_latency_warn_s": 5},
        "auto_fix": {"enabled": True},
    }
    assert apply_edit(_edit({"auto_fix": {"enabled": False}, "dns": {}}), {}, cfg) == "config"
    assert [s["name"] for s in cfg["sites"]] == ["Roots"], "a targets list must outlive an autofix save"
    assert [h["name"] for h in cfg["ssh_hosts"]] == ["guardian-hub"]
    assert cfg["thresholds"] == {"http_latency_warn_s": 5}
    assert cfg["auto_fix"] == {"enabled": False}, "a key the hub DID send still wins"


def test_a_deliberate_removal_still_applies():
    # The targets editor sends the whole list, so the key is present and
    # overwrites — merging does not make deletion impossible.
    cfg = {"sites": [{"name": "old"}], "ssh_hosts": [{"name": "keep"}]}
    apply_edit(_edit({"sites": []}), {}, cfg)
    assert cfg["sites"] == []
    assert cfg["ssh_hosts"] == [{"name": "keep"}]


def test_a_non_dict_config_is_ignored():
    cfg = {"sites": [{"name": "Roots"}]}
    apply_edit({"id": 2, "path": "/api/config", "body": {"config": None}}, {}, cfg)
    assert cfg["sites"] == [{"name": "Roots"}]


# ---------------------------------------------------------------------------
# Nested sections: a three-way merge, remembered in state
# ---------------------------------------------------------------------------

def test_a_sensor_only_setting_inside_a_hub_section_survives():
    """2026-09-27: the owner had `dns.enabled: true` on the sensor. Two
    dashboard saves elsewhere each replayed the hub's `dns` section, which
    never carried `enabled`, and the top-level merge replaced the whole
    section -- the setting was gone with nothing to say so."""
    cfg = {"dns": {"enabled": True, "upstreams": ["https://dns.example/dns-query"],
                   "lists": {"stevenblack": True}}}
    state = {}
    apply_edit(_edit({"dns": {"lists": {"stevenblack": False}}}), state, cfg)
    assert cfg["dns"]["enabled"] is True
    assert cfg["dns"]["upstreams"] == ["https://dns.example/dns-query"]
    assert cfg["dns"]["lists"] == {"stevenblack": False}, "what the hub sent still wins"


def test_a_key_the_hub_stops_sending_is_removed():
    """The reason a plain deep merge is not enough: the hub sends its whole
    section each time, so it has no way to say "delete this" except by
    leaving it out. Removing a per-host auto-fix override on the dashboard
    must remove it on the sensor."""
    cfg = {"auto_fix": {"enabled": True}}
    state = {}
    apply_edit(_edit({"auto_fix": {"enabled": True, "overrides": {"Roots": {"cap": 2}}}}), state, cfg)
    assert cfg["auto_fix"]["overrides"] == {"Roots": {"cap": 2}}
    apply_edit(_edit({"auto_fix": {"enabled": True, "overrides": {}}}), state, cfg)
    assert cfg["auto_fix"]["overrides"] == {}, "an override the hub dropped must not linger"


def test_what_the_hub_sent_is_remembered_across_restarts():
    """The memory lives in `state`, which the runner persists -- so a
    removal is still recognised after the sensor restarts between edits."""
    import json
    cfg = {"dns": {"enabled": True}}
    state = {}
    apply_edit(_edit({"dns": {"lists": {"a": True, "b": True}}}), state, cfg)
    state = json.loads(json.dumps(state))          # a restart: state round-trips through disk
    apply_edit(_edit({"dns": {"lists": {"a": True}}}), state, cfg)
    assert cfg["dns"]["lists"] == {"a": True}
    assert cfg["dns"]["enabled"] is True
