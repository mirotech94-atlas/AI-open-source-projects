"""Telling a phone that rotated its MAC from a genuinely new device.

MAC randomization is the false-positive machine the trust plan singles out
(§2.5E): iOS and Android rotate private Wi-Fi addresses, so one phone
becomes a stream of "new unknown device" alerts, each of which drops the
score and trains the owner to dismiss the next one.

Vault already suggested a parent when the names matched. A name is weak
evidence on its own, and it is weak in a specific and checkable way: if the
supposed parent is STILL ANSWERING on the network this very sweep, then
whatever the new device is, it is not that one wearing a new address. Two
Apple devices signed into one account share a name; a docked laptop and its
own hotspot share a name. The current rule suggests a parent in both cases.

So the match now weighs the one correlation that actually distinguishes
rotation from coincidence — the old address going quiet as the new one
appears — and reports a confidence with the reasons behind it, rather than
a bare suggestion the owner has no way to assess.

Deliberately still a SUGGESTION and never an automatic merge, at any
confidence. The plan is explicit that silent low-confidence merging is worse
than the flood it replaces: a wrongly merged device inherits trust it was
never granted, which is a security failure rather than a tidiness one.
"""
from guardian_sensor import alerts


PARENT = "aa:bb:cc:11:22:33"
ROTATED = "b2:bb:cc:44:55:66"      # locally-administered bit set
GLOBAL_MAC = "a0:bb:cc:44:55:66"   # ordinary burned-in address


def known(**over):
    base = {PARENT: {"name": "Miro's iPhone", "vendor": "Apple", "ip": "192.168.1.40",
                     "ports": {"62078": "x"}, "trusted": True}}
    base[PARENT].update(over)
    return base


def test_a_rotated_address_matches_when_its_predecessor_has_gone_quiet():
    parent, conf, reasons = alerts.identity_match(
        ROTATED, {"name": "Miro's iPhone"}, known(), live_macs={ROTATED})
    assert parent == PARENT
    assert conf == "high"
    assert any("no longer answering" in r for r in reasons)
    assert any("name" in r.lower() for r in reasons)


def test_a_name_match_is_not_enough_while_the_other_device_is_still_online():
    """The false positive the old rule produced. Two Apple devices on one
    account share a name; so do a laptop and its own hotspot. If the
    supposed parent is answering right now, the new address is not it."""
    parent, conf, reasons = alerts.identity_match(
        ROTATED, {"name": "Miro's iPhone"}, known(), live_macs={ROTATED, PARENT})
    assert parent is None
    assert conf == "none"
    assert any("still answering" in r for r in reasons)


def test_an_ordinary_burned_in_address_is_never_a_rotation():
    """Only a locally-administered address can be a privacy MAC. A device
    with a real OUI that shares a name is a different device."""
    parent, conf, _ = alerts.identity_match(
        GLOBAL_MAC, {"name": "Miro's iPhone"}, known(), live_macs={GLOBAL_MAC})
    assert parent is None and conf == "none"


def test_no_name_means_no_claim_rather_than_a_guess():
    parent, conf, _ = alerts.identity_match(
        ROTATED, {"name": ""}, known(), live_macs={ROTATED})
    assert parent is None and conf == "none"


def test_a_matching_port_signature_raises_confidence():
    """Ports are a second, independent signal: the same service set on the
    same kind of device is evidence the name alone cannot give."""
    _p, conf_without, _ = alerts.identity_match(
        ROTATED, {"name": "Miro's iPhone"}, known(), live_macs={ROTATED})
    _p2, conf_with, reasons = alerts.identity_match(
        ROTATED, {"name": "Miro's iPhone", "ports": {"62078": "x"}}, known(),
        live_macs={ROTATED})
    assert conf_with == "high"
    assert any("port" in r.lower() for r in reasons)
    assert conf_without in ("high", "moderate")


def test_the_reasons_are_always_returned_so_a_verdict_can_be_read():
    """A confidence with no reasons is an assertion. The owner is being
    asked to confirm a merge; they need to see what it rests on."""
    for live in ({ROTATED}, {ROTATED, PARENT}):
        _p, _c, reasons = alerts.identity_match(
            ROTATED, {"name": "Miro's iPhone"}, known(), live_macs=live)
        assert reasons, "every verdict has to explain itself"


def test_nothing_is_merged_automatically_even_at_high_confidence():
    """The suggestion never becomes a merge here. A wrongly merged device
    inherits trust nobody granted it, which is a security failure rather
    than an untidy list."""
    parent, conf, _ = alerts.identity_match(
        ROTATED, {"name": "Miro's iPhone"}, known(), live_macs={ROTATED})
    assert conf == "high" and parent == PARENT
    src = __import__("inspect").getsource(alerts.identity_match)
    assert "trusted" not in src, "identity matching must not read or grant trust"
