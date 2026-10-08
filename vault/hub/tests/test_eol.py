"""Servers past end of support, and the dataset's own honesty.

Two readers want this. The IT contact cares that an unsupported OS stops
getting security patches; whoever approves spending cares that it means a
replacement, and that is the person an MSP needs in front of. It is also
one of the four questions most likely to move a cyber-insurance premium.

The tests that carry the feature are about the reference data rather than
the matching, because reference data goes stale silently in a way
measurements do not:

- `test_an_unrecognised_system_is_unknown_not_supported` — the failure
  mode that matters. A network running something exotic is exactly where a
  confident green row is worst.
- `test_every_answer_carries_the_datasets_age` — so a reader can see how
  old the table is instead of assuming silence means safety.
- `test_the_table_reports_standard_support_not_extended` — Ubuntu ESM and
  RHEL ELS are paid add-ons. Reporting those dates tells someone they are
  fine when their machine stopped getting patches three years ago.
"""
import datetime as dt

import pytest

from guardian_hub import eol

TODAY = dt.date(2026, 9, 20)


@pytest.mark.parametrize("text,expected", [
    ("Ubuntu 22.04.3 LTS", ("ubuntu", "22.04")),
    ("Ubuntu 20.04 LTS", ("ubuntu", "20.04")),
    ("Debian GNU/Linux 12 (bookworm)", ("debian", "12")),
    ("Debian GNU/Linux 11 (bullseye)", ("debian", "11")),
    ("CentOS Linux 7 (Core)", ("centos", "7")),
    ("Rocky Linux 9.3 (Blue Onyx)", ("rhel", "9")),
    ("AlmaLinux 9.4 (Seafoam Ocelot)", ("rhel", "9")),
    ("Alpine Linux 3.19", ("alpine", "3.19")),
])
def test_the_vendors_own_version_strings_parse(text, expected):
    assert eol.parse(text) == expected


def test_ubuntu_keeps_its_minor_because_that_is_its_release_identity():
    """20.04 and 20.10 are different releases with support windows years
    apart. Truncating to "20" answers a different question."""
    assert eol.parse("Ubuntu 20.04 LTS") == ("ubuntu", "20.04")
    assert eol.parse("Ubuntu 20.10") == ("ubuntu", "20.10")


def test_an_unrecognised_system_is_unknown_not_supported():
    """The failure mode that matters. Anything the table has not heard of
    must not come back green."""
    for text in ("TempleOS 5.03", "Some Appliance Firmware", "", "   "):
        got = eol.state_of(text, today=TODAY)
        assert got["state"] == "unknown", text
        assert got["eol"] is None


def test_a_known_distro_at_an_unknown_version_is_still_unknown():
    """Recognising "Ubuntu" is not the same as knowing when 99.04 expires."""
    got = eol.state_of("Ubuntu 99.04 LTS", today=TODAY)
    assert got["distro"] == "ubuntu" and got["state"] == "unknown"


def test_a_system_past_its_date_is_reported_as_such():
    got = eol.state_of("Ubuntu 18.04.6 LTS", today=TODAY)
    assert got["state"] == "past_eol" and got["days_left"] < 0


def test_a_system_inside_the_notice_window_is_ending_soon():
    """A quarter is roughly the shortest notice on which an SMB can budget
    and schedule a replacement, which is the point of saying anything."""
    on_the_day = dt.date.fromisoformat(eol.EOL[("ubuntu", "22.04")]) - dt.timedelta(days=30)
    assert eol.state_of("Ubuntu 22.04 LTS", today=on_the_day)["state"] == "ending_soon"


def test_a_supported_system_is_quiet():
    early = dt.date.fromisoformat(eol.EOL[("ubuntu", "24.04")]) - dt.timedelta(days=900)
    assert eol.state_of("Ubuntu 24.04 LTS", today=early)["state"] == "supported"


def test_every_answer_carries_the_datasets_age():
    """So a reader can see how old the table is rather than assume the
    silence means safety."""
    for text in ("Ubuntu 22.04 LTS", "TempleOS 5.03", ""):
        assert eol.state_of(text, today=TODAY)["as_of"] == eol.AS_OF
    assert eol.AS_OF, "the dataset must be dated at all"


def test_the_table_reports_standard_support_not_extended():
    """Ubuntu ESM and RHEL ELS are paid add-ons a business may not hold.
    Reporting the extended date tells someone they are covered when their
    machine stopped receiving patches years ago."""
    # 18.04 standard support ended 2023; its ESM runs to 2028.
    assert eol.EOL[("ubuntu", "18.04")] < "2026-01-01"
    # CentOS 7 ended mid-2024, not at any later paid date.
    assert eol.EOL[("centos", "7")] == "2024-06-30"


def test_a_host_with_no_os_recorded_is_counted_as_unknown_not_dropped():
    """Dropping it would shrink the denominator and quietly improve the
    headline — the same arithmetic as scoring an unreachable host as fine."""
    got = eol.survey([{"name": "a", "os": "Ubuntu 24.04 LTS"}, {"name": "b"}], today=TODAY)
    assert got["total"] == 2
    assert got["counts"]["unknown"] == 1


def test_the_survey_counts_add_up_to_the_hosts_surveyed():
    hosts = [{"name": "a", "os": "Ubuntu 18.04 LTS"}, {"name": "b", "os": "Ubuntu 24.04 LTS"},
             {"name": "c", "os": "TempleOS"}, {"name": "d", "os": "Debian GNU/Linux 11 (bullseye)"}]
    got = eol.survey(hosts, today=TODAY)
    assert sum(got["counts"].values()) == got["total"] == 4
    assert got["counts"]["past_eol"] == 2 and got["counts"]["unknown"] == 1


def test_the_survey_names_the_host_so_a_finding_is_actionable():
    got = eol.survey([{"name": "vps1", "os": "Ubuntu 18.04 LTS"}], today=TODAY)
    assert got["hosts"][0]["host"] == "vps1"


# --- the endpoint ---------------------------------------------------------

def test_the_survey_is_reachable_over_the_api(client):
    from tests.test_api_v1 import bearer, seeded
    _tid, secret, _ = seeded(client)
    r = client.get("/v1/eol", headers=bearer(secret))
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["as_of"] == eol.AS_OF
    assert sum(got["counts"].values()) == got["total"]
    assert all("host" in h and "state" in h for h in got["hosts"])


def test_the_endpoint_never_reports_an_unrecognised_system_as_supported(client):
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    from tests.test_api_v1 import bearer, seeded
    tid, secret, _ = seeded(client)
    db = SessionLocal()
    try:
        db.add(m.Host(tenant_id=tid, name="oddbox", address="root@10.9.9.9",
                      last={"os": "TempleOS 5.03"}))
        db.commit()
    finally:
        db.close()
    rows = client.get("/v1/eol", headers=bearer(secret)).json()["hosts"]
    odd = next(h for h in rows if h["host"] == "oddbox")
    assert odd["state"] == "unknown" and odd["as_of"] == eol.AS_OF
