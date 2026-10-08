"""An evil twin is not "the SSID is on another channel".

The owner's own network set this off constantly: a Verizon CR1000A puts one
name on 2.4GHz and 5GHz, and two CE1000A extenders repeat it again, so the
SSID legitimately appears on several channels -- and routers re-pick channels
by themselves, so each move looked like a new intruder. Three APs, one
network, `rogue_dhcp: false`. Every meshed house would have seen the same.

What a rogue AP cannot avoid is offering weaker encryption than the real
network, or none at all, or a client will not talk to it. That is the signal.
"""
from guardian_hub.payloads.security import _evil_twin


def test_the_owners_own_mesh_is_not_an_evil_twin():
    # Exactly what his network reports: same name, same WPA2, several channels.
    wifi = {"security": "WPA2 Personal", "channel": 100}
    rogue = {"twin_channels": [6, 11, 100], "twin_security": ["WPA2 Personal"]}
    flagged, why = _evil_twin(wifi, rogue)
    assert flagged is False, why


def test_a_twin_offering_weaker_encryption_is_flagged():
    wifi = {"security": "WPA2 Personal", "channel": 100}
    rogue = {"twin_channels": [6], "twin_security": ["WPA2 Personal", "Open"]}
    flagged, why = _evil_twin(wifi, rogue)
    assert flagged is True
    assert "Open" in why and "WPA2 Personal" in why


def test_an_open_twin_of_an_open_network_is_still_not_flagged():
    """Matching security means matching hardware, even when that security is
    bad. The weak-encryption check is a separate row and says so itself."""
    wifi = {"security": "Open", "channel": 6}
    rogue = {"twin_channels": [11], "twin_security": ["Open"]}
    assert _evil_twin(wifi, rogue)[0] is False


def test_channels_alone_never_flag_anything():
    """The whole point: no amount of channel evidence is an evil twin."""
    wifi = {"security": "WPA3 Personal", "channel": 36}
    for chans in ([6], [1, 6, 11], list(range(1, 12))):
        assert _evil_twin(wifi, {"twin_channels": chans})[0] is False


def test_an_older_sensor_gives_no_opinion_rather_than_a_false_alarm():
    """A sensor that predates `twin_security` sends only channels. It must not
    resurrect the old behaviour."""
    wifi = {"security": "WPA2 Personal", "channel": 100}
    assert _evil_twin(wifi, {"twin_channels": [6, 11]})[0] is False


def test_unknown_local_security_gives_no_opinion():
    assert _evil_twin({}, {"twin_security": ["Open"]})[0] is False


def test_case_and_padding_do_not_create_a_phantom_mismatch():
    wifi = {"security": "WPA2 Personal"}
    rogue = {"twin_security": ["  wpa2 personal  "]}
    assert _evil_twin(wifi, rogue)[0] is False
