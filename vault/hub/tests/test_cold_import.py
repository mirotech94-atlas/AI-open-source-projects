"""The app must import from a cold interpreter, the way uvicorn starts it.

A circular import shipped to `main` on 2026-09-20 and the full 1431-test
suite passed over it. `reports/builder` imported `scheduler._tenant_offset`
at module scope while `scheduler` imports `builder`, so
`import guardian_hub.main` raised ImportError — and production is exactly
that import, run cold by uvicorn.

The suite could not see it because `conftest` imports `guardian_hub.db`,
`models`, `demo_trickle`, `ratelimit` and `public_feed` before it touches
`main`. By then the modules in the cycle are already in `sys.modules` and
the cycle resolves. Every test then passed against an application that
could not start.

That is the trap worth naming: **a test suite's import order is not
production's, so no in-process test can prove the app boots.** The only
check that works is a fresh interpreter, which is what this does.

It deliberately shells out rather than using `importlib.reload`: reload
keeps the existing `sys.modules` entries, which is the very state that
hides the bug. CLAUDE.md's conftest note says the same thing from the other
direction — that module is careful to import `guardian_hub.db` exactly once
precisely because import order here is load-bearing.
"""
import pathlib
import subprocess
import sys

HUB = pathlib.Path(__file__).resolve().parents[1]


def _cold(statement: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", statement], cwd=str(HUB),
                          capture_output=True, text=True, timeout=120)


def test_the_application_imports_in_a_fresh_interpreter():
    got = _cold("import guardian_hub.main")
    assert got.returncode == 0, (
        "`import guardian_hub.main` fails cold, which is how uvicorn starts it:\n"
        + got.stderr[-2000:]
    )


def test_the_application_builds_in_a_fresh_interpreter():
    """Importing is not starting. `create_app` wires every router, which is
    where a cycle between a router and a payload module would surface."""
    got = _cold("from guardian_hub.main import create_app; create_app()")
    assert got.returncode == 0, got.stderr[-2000:]


def test_the_scheduler_imports_on_its_own():
    """`guardian migrate` and the CLI reach the scheduler without going
    through `main`, so it has to stand up by itself too."""
    got = _cold("import guardian_hub.scheduler")
    assert got.returncode == 0, got.stderr[-2000:]


def test_the_report_builder_imports_on_its_own():
    """The other end of the cycle that broke. Either side importing first
    has to work, because which one happens to be first is an accident of
    the caller."""
    got = _cold("from guardian_hub.reports import builder")
    assert got.returncode == 0, got.stderr[-2000:]


def test_the_cli_runs_cold():
    """`guardian migrate` on a deploy host is a cold start with no pytest
    conftest to have warmed anything."""
    got = _cold("from guardian_hub import cli; assert cli.main")
    assert got.returncode == 0, got.stderr[-2000:]
