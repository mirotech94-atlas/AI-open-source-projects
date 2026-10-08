"""`label` and `trusted` suppress detections. Only a person may set them.

This is the rule the first-run onboarding design turned on, and it is easy to
undo by accident because both fields look like display data. They are not:

  * a trusted device is skipped by the rogue-uplink check entirely
    (`sensor/guardian_sensor/alerts.py`), is exempt from risky-port alerts, and
    scores lower risk (`lan.py`);
  * a device with EITHER `trusted` or a non-empty `label` is excluded from
    `lan:unverified` (`home.py`);
  * and `ingest.store_scan` refuses to un-trust a device a later scan reports
    as untrusted, so the effect never wears off.

The first draft of the onboarding work auto-trusted every device Vault
recognised, to make a new customer's estate look settled. That would have
permanently disabled three detections on each of thirty-five devices, and
nobody would have found out until the day one mattered.

It was never needed either: `home.py` anchors its baseline at the earliest
`first_seen` and only flags devices NEWER than it, so the first scan's whole
cohort is already exempt. Vault already implements "this is my network as
of today".

A name the CUSTOMER types is a different thing and is written to `label` on
purpose -- that is a person vouching, which is exactly what the field means.
"""
import pathlib
import re

HUB = pathlib.Path(__file__).resolve().parents[1] / "guardian_hub"

#: Modules that run without a person deciding anything: first-run state, enrol
#: code minting, and the payload builders that shape rows for reading.
AUTOMATIC = [
    "onboarding.py",
    "sensors.py",
    "payloads/account.py",
    "payloads/platform.py",
    "payloads/overview.py",
]

_WRITE = re.compile(r"\.(trusted|label)\s*=(?!=)")


def test_no_automatic_path_writes_a_warrant_field():
    offenders = []
    for rel in AUTOMATIC:
        path = HUB / rel
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _WRITE.search(line):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert offenders == [], (
        "these set a field that suppresses a detection:\n  " + "\n  ".join(offenders))


def test_the_rule_is_written_down_where_the_next_reader_will_look():
    """A test that fails is a puzzle; CLAUDE.md is where the reason lives."""
    claude = (HUB.parents[1] / "CLAUDE.md").read_text()
    assert "human warrant" in claude
    assert "trusted" in claude and "label" in claude
