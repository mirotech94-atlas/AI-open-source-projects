"""What Vault says about a device that keeps appearing and disappearing.

Presence is a ping sweep plus ARP. A device in Wi-Fi power save ignores
pings, so the gaps are indistinguishable from a device leaving. The old text
resolved that by asserting "weak Wi-Fi or power saving" -- a cause nothing
had measured, and on the owner's own network the wrong one: the router
reported -62 dBm and 24 hours of unbroken association.

So the rule is now: ask the router, and only say what it measured.
"""
import guardian_sensor.alerts as A

HEALTHY = {"station_mac": "AA:BB:CC:DD:EE:FF", "signal_strength": "-62",
           "assoc_time": "86400", "connect_type": "5G", "link_rate": "351Mbps"}
MAC = "aa:bb:cc:dd:ee:ff"


def test_a_healthy_associated_device_produces_no_alert_at_all():
    """The case that started this. The device never left the Wi-Fi, so the
    gaps are Vault's own blind spot -- reporting them as the owner's
    problem would be reporting our measurement error as their fault."""
    assert A.flapping_verdict({"wifi_stations": [HEALTHY]}, MAC) is None


def test_a_weak_signal_is_reported_with_the_number():
    said = A.flapping_verdict(
        {"wifi_stations": [dict(HEALTHY, signal_strength="-78", assoc_time="40")]}, MAC)
    assert "-78 dBm" in said and "weak" in said.lower()
    assert "closer" in said          # and what to do about it


def test_a_device_the_router_does_not_list_is_genuinely_disconnecting():
    said = A.flapping_verdict({"wifi_stations": [HEALTHY]}, "00:11:22:33:44:55")
    assert "genuinely disconnecting" in said


def test_no_station_table_keeps_the_old_wording_rather_than_inventing_certainty():
    """No router credential, a failed read, or a gateway we cannot query.
    Unknown is not the same as fine, and it is not the same as broken."""
    for res in ({}, {"wifi_stations": []}, {"wifi_stations": None}):
        said = A.flapping_verdict(res, MAC)
        assert said is not None and "flapping" in said


def test_the_thresholds_are_named_not_buried():
    assert A.WEAK_DBM == -70 and A.STABLE_ASSOC_S == 3600
    # Either side of the signal boundary, both long-associated.
    strong = A.flapping_verdict({"wifi_stations": [dict(HEALTHY, signal_strength="-69")]}, MAC)
    weak = A.flapping_verdict({"wifi_stations": [dict(HEALTHY, signal_strength="-71")]}, MAC)
    assert strong is None
    assert "-71 dBm" in weak and "weak" in weak.lower()


def test_a_good_signal_alone_does_not_silence_it():
    """A device that re-associated a minute ago is genuinely reconnecting,
    however strong the signal reads right now. Only a long-held association
    proves it never left."""
    said = A.flapping_verdict({"wifi_stations": [dict(HEALTHY, signal_strength="-55",
                                                      assoc_time="60")]}, MAC)
    assert said is not None and "-55 dBm" in said


def test_a_briefly_connected_device_is_described_not_diagnosed():
    """Short association and a decent signal: something is happening, but the
    signal does not explain it, so the alert reports both and claims neither."""
    said = A.flapping_verdict(
        {"wifi_stations": [dict(HEALTHY, signal_strength="-65", assoc_time="0")]}, MAC)
    assert said is not None and "-65 dBm" in said
    assert "weak" not in said.lower()
