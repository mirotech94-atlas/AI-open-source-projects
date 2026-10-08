"""Item 25: a finding reaches the hub already carrying its repair.

Miro's ask was "Vault fixes in parallel with the scan, so most findings
are closed by the time it ends". The cycle already achieves the OUTCOME --
`fix.remediate` runs inside `run_once`, before the result is pushed, and
stamps each repaired alert with `al["fix"]` -- so the customer never sees a
red finding that Vault had already dealt with a moment earlier.

That ordering is load-bearing and invisible: move the push one line earlier
and nothing fails, nothing logs, and every fix silently arrives a full scan
cycle (five minutes) after the finding it belongs to. This is the guard.
"""
import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parents[1] / "guardian_sensor" / "runner.py"


def _line_of(pattern: str) -> int:
    text = SRC.read_text().splitlines()
    for i, line in enumerate(text, start=1):
        if re.search(pattern, line):
            return i
    raise AssertionError(f"not found in runner.py: {pattern}")


def test_remediation_runs_before_the_result_is_pushed():
    remediate = _line_of(r"res\[.fixes.\] = fix\.remediate\(")
    push = _line_of(r"client\.push\(\{")
    assert remediate < push, (
        "fixes must run before the scan result leaves the sensor, or every "
        "finding reaches the customer red and is only closed a cycle later"
    )


def test_alerts_are_built_before_remediation_so_there_is_something_to_fix():
    built = _line_of(r"res\[.alerts.\] = alerts\.build_alerts\(")
    remediate = _line_of(r"res\[.fixes.\] = fix\.remediate\(")
    assert built < remediate


def test_a_repaired_alert_is_stamped_in_the_same_payload():
    """The stamp is what lets the hub show "found and fixed" as one event
    rather than two, which is the whole point of doing it in this order."""
    text = SRC.read_text()
    assert 'al["fix"] = f"{\'auto-fixed\'' in text
