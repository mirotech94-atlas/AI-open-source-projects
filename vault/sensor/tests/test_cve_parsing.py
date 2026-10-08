"""The CVE list actually names a package, not debsecan's own status marker.

`hosts.py`'s remote collector ran `debsecan --only-fixed --format detail`.
That format is multi-line per CVE -- a bare `CVE-ID (fixed)` header line, then
an indented description, then an indented `installed: <package> <version>`
line -- and the collector's own `awk '/^CVE/{print $1" "$2" "$NF}'` only ever
reads the header line. On a header line of exactly two fields, `$2` and `$NF`
are both "(fixed)" (debsecan's own annotation meaning a fix is available),
so every row's package and urgency came out reading `(fixed)` -- confirmed
against the live database and against a real host over SSH.

`--format summary` puts CVE, package and the same "(fixed)" marker on ONE
line -- `CVE-2026-14258 dhcpcd-base (fixed)` -- which is what the existing awk
was written for. The fix is the flag, not the awk.
"""

import pathlib
import re
import subprocess

HOSTS_PY = pathlib.Path(__file__).resolve().parent.parent / "guardian_sensor" / "hosts.py"

# A real capture from `debsecan --suite trixie --only-fixed --format summary`
# on a live host, in this session -- not invented.
REAL_SUMMARY_OUTPUT = """\
CVE-2026-14258 dhcpcd-base (fixed)
CVE-2026-15588 gir1.2-glib-2.0 (fixed)
CVE-2026-16118 gir1.2-glib-2.0 (fixed)
"""

# What the OLD, broken flag produced -- one CVE's --format detail header line,
# captured on a live host, kept here so a future edit that reverts the flag
# fails against a shape that actually happened rather than an invented one.
REAL_DETAIL_HEADER = "CVE-2026-14258 (fixed)\n"


def _extract_awk_line() -> str:
    """Pull the exact `debsecan ...` line out of hosts.py, so this test
    breaks if that line is ever edited rather than silently drifting from it."""
    text = HOSTS_PY.read_text()
    line = next(l for l in text.splitlines() if "debsecan" in l and "CVEALL=" in l)
    assert "--format detail" not in line, (
        "hosts.py reverted to --format detail, which has no package name on "
        "the one line this collector's awk reads"
    )
    assert "--format summary" in line
    return line


def _run_cvel(debsecan_stub_output: str,
              upgradable: str = "dhcpcd-base/stable 1.0 amd64 [upgradable from: 0.9]\ngir1.2-glib-2.0/stable 2 amd64 [upgradable from: 1]") -> str:
    """Run the collector's real debsecan+awk line against a stubbed
    `debsecan` on PATH, and return the parsed CVEL string."""
    line = _extract_awk_line()
    script = f"""
    debsecan() {{ printf '%s' "$STUB_OUTPUT"; }}
    export -f debsecan
    # apt says which packages actually have an update; only those CVEs count
    apt() {{ printf 'Listing... Done\n%s\n' "$STUB_UPGRADABLE"; }}
    export -f apt
    . /etc/os-release 2>/dev/null || VERSION_CODENAME=trixie
    {line}
    echo "$CVEL"
    """
    r = subprocess.run(
        ["bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", "STUB_OUTPUT": debsecan_stub_output, "STUB_UPGRADABLE": upgradable},
        capture_output=True, text=True, timeout=15,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_the_flag_is_summary_not_detail():
    _extract_awk_line()  # asserts inside


def test_a_real_summary_line_yields_the_real_package_name():
    cvel = _run_cvel(REAL_SUMMARY_OUTPUT)
    assert "dhcpcd-base" in cvel, cvel
    assert cvel.count("(fixed)") == 3, "one status marker per CVE, not doubled"
    # The old bug's exact symptom must not reappear: a package field that IS
    # the literal string "(fixed)".
    assert " (fixed) (fixed)" not in cvel, cvel


def test_the_old_detail_header_would_have_produced_the_bug():
    """Guard rail proving the diagnosis, not just the fix: replaying the
    real broken output through the awk still shows the symptom, so the flag
    change -- not a coincidence in the awk -- is what fixed it."""
    r = subprocess.run(
        ["bash", "-c",
         'awk \'/^CVE/{print $1" "$2" "$NF}\''],
        input=REAL_DETAIL_HEADER, capture_output=True, text=True, timeout=5,
    )
    assert r.stdout.strip() == "CVE-2026-14258 (fixed) (fixed)"



def test_a_cve_on_a_package_apt_cannot_update_is_not_counted_as_fixable():
    """Caddy from its vendor repo: debsecan says "(fixed)" by Debian's table,
    apt has nothing to install. That is not a fix available."""
    cvel = _run_cvel(REAL_SUMMARY_OUTPUT, upgradable="something-else/stable 1 amd64 [upgradable from: 0]")
    assert "dhcpcd-base" not in cvel and cvel.strip() == ""
