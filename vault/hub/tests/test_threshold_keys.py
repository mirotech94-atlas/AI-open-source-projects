"""Every threshold the Settings page offers must be one the sensor reads.

`routers/settings.THRESHOLD_RANGES` is the whole contract for
`POST /settings/thresholds`: the keys in it are what the web form renders,
validates and writes into `Tenant.config["thresholds"]`. The hub itself
barely reads that dict -- it hands it to the sensor, and the sensor decides
what alerts. So a key in `THRESHOLD_RANGES` that the sensor has no default
for is a knob wired to nothing: the owner moves it, the hub stores it, and
no alert ever changes.

That is not hypothetical. This table shipped `latency_warn_s` for a long
time while the sensor alerted on `http_latency_warn_s`, and WEME's live
config ended up holding both -- `http_latency_warn_s: 4.0` doing the work
and `latency_warn_s: 0.2` doing nothing.

The hub cannot import `guardian_sensor` (it is the sensor's own package,
installed on the sensor host), so this reads the source and parses it, the
same way `test_remedy.py` keeps the ported fix tables honest.
"""

import ast
import pathlib

from guardian_hub.routers.settings import THRESHOLD_RANGES

SENSOR_CONFIG = (pathlib.Path(__file__).resolve().parents[2]
                 / "sensor" / "guardian_sensor" / "config.py")


def _sensor_threshold_defaults() -> dict:
    """`DEFAULTS["thresholds"]` out of the sensor's config module, read as
    source. `ast.literal_eval` rather than exec: this must never run sensor
    code, only read its literals."""
    tree = ast.parse(SENSOR_CONFIG.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", "") == "DEFAULTS" for t in node.targets):
            continue
        defaults = ast.literal_eval(node.value)
        return defaults["thresholds"]
    raise AssertionError(f"no module-level DEFAULTS found in {SENSOR_CONFIG}")


def test_every_settable_threshold_is_one_the_sensor_reads():
    sensor_keys = set(_sensor_threshold_defaults())
    orphans = sorted(set(THRESHOLD_RANGES) - sensor_keys)
    assert not orphans, (
        f"THRESHOLD_RANGES offers {orphans}, which the sensor has no default "
        f"for. A threshold the sensor does not read cannot change any alert. "
        f"Sensor thresholds: {sorted(sensor_keys)}"
    )


def test_the_dead_latency_key_is_gone():
    """Named explicitly so the specific regression cannot come back quietly."""
    assert "latency_warn_s" not in THRESHOLD_RANGES
    assert "http_latency_warn_s" in THRESHOLD_RANGES


def test_each_range_contains_the_sensor_default():
    """A range that excludes the sensor's own default would make the form
    reject the value already in force -- the owner opens Settings, changes
    nothing, saves, and gets a 422."""
    defaults = _sensor_threshold_defaults()
    for key, (lo, hi) in THRESHOLD_RANGES.items():
        value = defaults[key]
        if isinstance(value, bool):        # require_fail2ban is not numeric
            continue
        assert lo <= value <= hi, f"{key} default {value} is outside ({lo}, {hi})"
