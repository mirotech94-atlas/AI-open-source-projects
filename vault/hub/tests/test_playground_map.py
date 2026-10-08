"""`docs/guardian-playground.html` names real modules, and nothing makes it
keep doing so.

The playground is a map of the modules a change usually lands in: you click
one, say what should change there, and it emits a prompt quoting that module's
path. Its whole value is that the path is real -- a prompt naming
`hub/guardian_hub/payloads/dnsview.py` sends the reader to the right file,
and a prompt naming a path that moved two commits ago sends them nowhere and
is worse than no map at all.

Nothing else catches that. The file is plain HTML with no build step and no
imports, so a renamed or deleted module breaks it *silently* -- the page still
renders, the prompt still copies, and the wrong path is only noticed by
whoever follows it. This test is the build step it doesn't have.

Read as text rather than parsed as HTML for the same reason
`test_remedy.py` reads `sensor/guardian_sensor/fix.py` as source: the thing
being checked is a literal in a file this package cannot import.
"""

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAYGROUND = _ROOT / "docs" / "guardian-playground.html"

#: Matches the `path:` field of each entry in the playground's own `NODES`
#: array, e.g. `{ id: 'ingest', label: 'ingest', path: 'hub/...py', ... }`.
_PATH = re.compile(r"path: *'([^']+)'")

#: The map covered 43 modules when this test was written. The exact number is
#: expected to drift as modules are added to it; the point of the floor is to
#: fail loudly if `_PATH` ever stops matching, because a regex that matches
#: nothing would otherwise make every assertion below vacuously true.
_MIN_NODES = 30


def module_paths(html: str) -> list[str]:
    """Every repo-relative module path the playground claims exists."""
    return _PATH.findall(html)


def missing(paths: list[str]) -> list[str]:
    """Those that are not in the tree, in the order the playground lists them."""
    return [p for p in paths if not (_ROOT / p).exists()]


def test_the_playground_names_a_plausible_number_of_modules():
    paths = module_paths(PLAYGROUND.read_text())
    assert len(paths) >= _MIN_NODES, (
        f"only {len(paths)} module paths found in {PLAYGROUND.name}; the "
        "extraction is probably broken, which would make the drift check below "
        "pass without checking anything"
    )


def test_every_module_on_the_map_still_exists():
    paths = module_paths(PLAYGROUND.read_text())
    gone = missing(paths)
    assert not gone, (
        "the playground points at modules that are no longer there: "
        + ", ".join(gone)
        + f" -- update the NODES array in {PLAYGROUND.name}"
    )


def test_the_drift_check_notices_a_module_that_moved():
    """The guard above only earns its keep if it can fail.

    A path check that silently matches nothing passes forever, so this feeds
    it a doctored copy of the real page -- one module renamed, exactly what a
    refactor does -- and asserts it is reported.
    """
    html = PLAYGROUND.read_text()
    moved = html.replace(
        "path: 'hub/guardian_hub/ingest.py'",
        "path: 'hub/guardian_hub/ingestion.py'",
        1,
    )
    assert moved != html, "the fixture this test doctors is no longer on the map"

    # `in`, not `==`: this test answers "can the guard see a moved module?".
    # Asserting the whole list would make it fail a second time for real drift
    # the test above already reports, which reads as two bugs instead of one.
    assert "hub/guardian_hub/ingestion.py" in missing(module_paths(moved))
