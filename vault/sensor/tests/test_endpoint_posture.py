"""Whether the machine Vault runs on is defending itself.

Vault cannot scan a customer's phone for viruses and this does not
pretend to. It reports whether the protections a machine already ships with
are switched on -- a smaller promise than antivirus, and one that is true.

The rule these tests exist to hold: a check Vault could not run is an
UNKNOWN, never a problem. A security panel that cries about things it
failed to measure is a panel people learn to ignore.
"""
import guardian_sensor.endpoint as ep


def test_each_check_is_on_off_or_unknown():
    assert ep.parse_sip("System Integrity Protection status: enabled.") is True
    assert ep.parse_sip("System Integrity Protection status: disabled.") is False
    assert ep.parse_sip("") is None                      # tool missing
    assert ep.parse_gatekeeper("assessments enabled") is True
    assert ep.parse_gatekeeper("assessments disabled") is False
    assert ep.parse_gatekeeper("spctl: command not found") is None
    assert ep.parse_filevault("FileVault is On.") is True
    assert ep.parse_filevault("FileVault is Off.") is False
    assert ep.parse_filevault("") is None


def test_the_mac_firewall_counts_essential_only_as_on():
    """globalstate 2 is "block all incoming except essentials" -- that is
    the firewall doing its job, not a half-measure to nag about."""
    assert ep.parse_alf("1") is True
    assert ep.parse_alf("2") is True
    assert ep.parse_alf("0") is False
    assert ep.parse_alf("does not exist") is None


def test_ufw_and_clamav_parse():
    assert ep.parse_ufw("Status: active") is True
    assert ep.parse_ufw("Status: inactive") is False
    assert ep.parse_ufw("") is None
    assert ep.parse_clamav_version("ClamAV 1.0.1/27000/Mon Sep 1") == "ClamAV 1.0.1"
    assert ep.parse_clamav_version("bash: clamscan: not found") == ""


def test_a_startup_item_whose_executable_lives_in_tmp_is_flagged():
    """The commonest way commodity macOS malware survives a reboot."""
    hits = ep.suspicious_launch_items({
        "/Library/LaunchDaemons/com.evil.plist": "/tmp/payload",
        "/Library/LaunchAgents/com.apple.ok.plist": "/usr/bin/true",
    })
    assert [h["item"] for h in hits] == ["/Library/LaunchDaemons/com.evil.plist"]
    assert "tmp" in hits[0]["why"]


def test_logging_to_tmp_is_not_malware():
    """The regression that made this check honest. The first version
    searched the plist TEXT for "/tmp" and instantly accused two of the
    owner's own agents, because a launch item routinely sends its
    StandardOutPath to /tmp while running an ordinary binary. Only the
    executable counts."""
    assert ep.suspicious_launch_items({
        "/Users/mac/Library/LaunchAgents/com.flycheap.build.plist": "/bin/zsh",
        "/Users/mac/Library/LaunchAgents/com.myron.wake.plist": "/usr/bin/python3",
    }) == []


def test_ordinary_startup_items_are_left_alone():
    """A false positive here accuses someone's own software of being
    malware, so the bar is a hiding place, not merely 'third party'."""
    assert ep.suspicious_launch_items({
        "/Library/LaunchAgents/com.docker.plist": "/Applications/Docker.app/Contents/MacOS/x",
        "/Library/LaunchAgents/com.zoom.plist": "/Users/mac/Applications/zoom",
    }) == []
    assert ep.suspicious_launch_items({}) == []
    assert ep.suspicious_launch_items(None) == []


def test_this_machines_real_startup_items_are_not_accused():
    """Run against the actual host: 50+ launch items, and a check that
    flags any of them without cause is not shippable."""
    hits = ep.suspicious_launch_items(ep._launch_item_programs())
    assert hits == [], hits


def test_every_check_has_wording_that_says_what_it_buys():
    """The alert text comes from LABELS, so a check with no entry would
    reach the owner as a bare key like 'sip is off'."""
    for key in ("sip", "gatekeeper", "filevault", "firewall", "malware_scanner"):
        label, buys = ep.LABELS[key]
        assert label and buys and not buys.endswith(".")


def test_posture_runs_on_this_machine_without_root_and_never_raises():
    rep = ep.posture()
    assert rep["platform"] in ("mac", "linux")
    assert isinstance(rep.get("checks"), dict) and rep["checks"]
    for v in rep["checks"].values():
        assert v in (True, False, None)
