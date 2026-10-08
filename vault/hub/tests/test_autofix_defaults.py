"""The hub and the sensor must agree on what an unconfigured policy means.

They did not. `sensor/guardian_sensor/fix.py` treats a missing `auto_fix`
block as "repair, up to 30 a day"; the hub's read model defaulted to
off/0 and Config told the owner "Auto-fix: Off, up to 0/day" while Vault
was repairing for them.
"""
import pathlib
import re

from guardian_hub import schemas_dash as sd

SENSOR_FIX = pathlib.Path(__file__).resolve().parents[2] / "sensor" / "guardian_sensor" / "fix.py"


def test_the_read_model_defaults_to_repairing_like_the_sensor_does():
    policy = sd.AutoFixPolicy()
    assert policy.enabled is True
    assert policy.max_per_day == 30


def test_those_defaults_are_the_ones_the_sensor_actually_uses():
    # Read them out of the sensor rather than restating them, so this fails
    # if someone changes the sensor and not the hub.
    source = SENSOR_FIX.read_text()
    enabled = re.search(r'\("auto_fix"\)\s*or\s*\{\}\)\.get\("enabled",\s*(True|False)\)', source)
    cap = re.search(r'\("auto_fix"\)\s*or\s*\{\}\)\.get\("max_per_day",\s*(\d+)\)', source)
    assert enabled and cap, "the sensor's auto_fix defaults moved; re-point this test"
    assert sd.AutoFixPolicy().enabled is (enabled.group(1) == "True")
    assert sd.AutoFixPolicy().max_per_day == int(cap.group(1))


def test_a_stored_policy_still_wins_over_the_default():
    assert sd.AutoFixPolicy(enabled=False, max_per_day=5).enabled is False
    assert sd.AutoFixPolicy(enabled=False, max_per_day=5).max_per_day == 5


def test_an_enabled_policy_cannot_be_saved_with_a_zero_cap(client):
    """A cap of 0 with auto-fix on is "off" that nobody confirmed: the sensor
    reads it as the cap already reached. The switch is the way to turn it
    off; the number field must not be a second, silent one."""
    from tests.test_seed_helpers import seeded_demo
    seeded_demo(client)
    r = client.post("/api/v1/settings/autofix", json={
        "enabled": True, "max_per_day": 0, "attacker_method": "auto",
        "maintenance_window": {"start": "02:00", "end": "05:00"}})
    assert r.status_code == 422
    assert "at least 1" in r.json()["detail"]
    off = client.post("/api/v1/settings/autofix", json={
        "enabled": False, "max_per_day": 0, "attacker_method": "auto",
        "maintenance_window": {"start": "02:00", "end": "05:00"}})
    assert off.status_code == 200, off.text
