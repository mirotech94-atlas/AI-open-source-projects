"""Item 24: three depths, and an unknown one never launches the slow scan."""
from guardian_sensor import vulnscan as v


def test_the_three_depths_are_ordered_and_distinct():
    q, s, f = len(v.QUICK_PORTS), len(v.STANDARD_PORTS), len(v.FULL_PORTS)
    assert q < s < f, "each depth must actually sweep more than the one before it"
    assert set(v.QUICK_PORTS) <= set(v.STANDARD_PORTS) <= set(v.FULL_PORTS), \
        "a deeper scan must never skip something a shallower one checked"


def test_standard_catches_what_quick_walks_past():
    # The whole reason for a middle depth: the ports an attacker scans for
    # that do not live under 1024 and are not in the quick set.
    for port in (2375, 6379, 3389, 11211, 27018):
        assert port in set(v.STANDARD_PORTS), f"{port} is exactly what this depth is for"


def test_an_unknown_profile_falls_back_to_quick_not_to_full():
    # An unknown profile is a bug, and a bug should not launch a
    # several-minute scan on someone's network.
    assert v.PROFILE_PORTS.get("bogus", v.QUICK_PORTS) is v.QUICK_PORTS
    assert set(v.PROFILE_PORTS) == {"quick", "standard", "full"}
