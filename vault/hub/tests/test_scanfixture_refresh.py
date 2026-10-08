"""The rebase is refreshed between tests, so a long run does not go stale.

`scanfixture` shifts the captured scan onto the current time so the suite
stops going red purely because time passed. It computed that delta once, at
import -- and the promise it makes ("a scan that just happened") then decayed
for the rest of the run.

Fifteen minutes is all it takes. `_util.effective_presence` reads an ONLINE
device last seen more than 900 seconds ago as UNKNOWN, so once a run is past
that mark every device in the seeded capture reads unknown and every
assertion resting on a live device fails. Measured on the seeded demo tenant:
29 of 35 devices online at import, still 29 at +10 minutes, 0 at +16. The full
hub suite takes about an hour, so `test_device_tiles` and
`test_read_write::test_fix_queues` failed there and passed on their own --
which reads like flakiness or test pollution and is neither. It is the same
mistake as asserting wall-clock, one layer down: nothing about the data
changed, only how long ago it was written.

The delta still may not be recomputed per *load* -- `seed_demo` is idempotent
only if two loads inside one test are byte-identical (see
`test_loads_are_stable_within_one_process`). Refreshing between tests keeps
that and drops the decay: `conftest`'s `client` fixture calls `refresh()`
alongside the other process-global resets it already does.
"""

import datetime as dt

from tests import scanfixture as sf


def test_refresh_moves_the_capture_back_onto_now():
    before = sf.DELTA
    stale = sf.NOW - dt.timedelta(hours=3)
    sf.NOW, sf.DELTA = stale, stale - sf.anchor_of(sf.raw("last.json"))
    sf._CACHE.clear()
    assert sf.anchor_of(sf.load_last()) < dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)

    sf.refresh()

    age = dt.datetime.now(dt.timezone.utc) - sf.anchor_of(sf.load_last())
    assert age < dt.timedelta(seconds=30), age
    assert sf.DELTA >= before, "refresh only ever moves the capture forward"


def test_refresh_keeps_two_loads_in_one_test_identical():
    """The constraint the once-per-import delta existed to protect."""
    sf.refresh()
    import json
    assert json.dumps(sf.load_last()) == json.dumps(sf.load_last())
    assert sf.rebased_dir() == sf.rebased_dir()
    for name in ("last.json", "state.json"):
        assert json.loads((sf.rebased_dir() / name).read_text()) == sf.load(name)


def test_the_rebased_dir_tracks_a_refresh():
    """A refresh that left the on-disk copy behind would be worse than none:
    `seed_demo(fixtures=...)` reads the directory, not `load()`."""
    sf.refresh()
    import json
    on_disk = json.loads((sf.rebased_dir() / "last.json").read_text())
    assert sf.anchor_of(on_disk) == sf.NOW


def test_the_client_fixture_refreshes_the_rebase():
    """Read from conftest's source: the decay is invisible in a short run, so
    only the wiring keeps a long one honest."""
    import inspect
    from tests import conftest
    src = inspect.getsource(conftest.client)
    assert "scanfixture.refresh()" in src or "sf.refresh()" in src, (
        "conftest's client fixture must refresh the rebase per test"
    )
