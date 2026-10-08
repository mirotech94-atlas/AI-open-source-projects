"""Every sensor test runs against a throwaway data directory.

`guardian_sensor.paths` resolves its data directory ONCE, at import, from
`GUARDIAN_DATA` or else `~/.guardian` -- the running sensor's own directory
on the machine the suite is usually run on. A test that called the real
`fix.apply_fix` without redirecting `paths` appended a fake "full apt upgrade
on prod" to that sensor's repair journal on every run: about 160 of them
between 2026-09-10 and 09-22, mixed in with real repairs, counting toward the
real daily auto-fix cap. `d00e82c` fixed that one test; this makes it
impossible for the next one, because it runs before any test module can
import the sensor.
"""
import os
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="guardian-sensor-tests-")
os.environ["GUARDIAN_DATA"] = _SANDBOX


def pytest_sessionstart(session):
    from guardian_sensor import paths
    real = os.path.realpath(os.path.expanduser("~/.guardian"))
    assert os.path.realpath(paths.DATA_DIR) != real, (
        "the sensor suite resolved the LIVE sensor directory; conftest.py "
        "must run before anything imports guardian_sensor.paths")
