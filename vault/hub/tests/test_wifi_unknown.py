"""Not being able to read the Wi-Fi is not a finding about the Wi-Fi.

An owner on WPA2 was shown a failed "Wi-Fi encryption" check reading
`wifi: (unknown)` and told to go set WPA2 -- because an empty `security`
string was folded in with "open". Unknown has to be its own state, all the
way through to the advice.
"""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import security as sec
from tests.test_auth import seed_user


def _checks(raw):
    db = SessionLocal()
    try:
        t = db.query(m.Tenant).first()
        return {c.key: c for c in sec.checks(db, t, raw, "2026-09-20T03:00:00Z")}
    finally:
        db.close()


def test_wpa2_passes():
    assert sec.wifi_verdict({"security": "WPA2 Personal"}) == "good"
    assert sec.wifi_verdict({"security": "WPA3 Personal"}) == "good"


def test_open_and_wep_are_weak():
    assert sec.wifi_verdict({"security": "Open"}) == "weak"
    assert sec.wifi_verdict({"security": "none"}) == "weak"
    assert sec.wifi_verdict({"security": "WEP"}) == "weak"


def test_an_unread_value_is_unknown_not_weak():
    """The whole bug in one assertion."""
    for wifi in ({}, {"security": ""}, {"security": "?"}, {"ssid": "x"}):
        assert sec.wifi_verdict(wifi) == "unknown", wifi


def test_the_check_is_tri_state(client):
    seed_user()
    good = _checks({"home": {"wifi": {"ssid": "Home", "security": "WPA2 Personal"}}})["wifi:home"]
    assert good.passed is True

    weak = _checks({"home": {"wifi": {"ssid": "Home", "security": "Open"}}})["wifi:home"]
    assert weak.passed is False

    unknown = _checks({"home": {"wifi": {}}})["wifi:home"]
    assert unknown.passed is None, "an unread value must not render as a failed check"


def test_the_unknown_row_does_not_claim_a_security_value(client):
    seed_user()
    ev = _checks({"home": {"wifi": {}}})["wifi:home"].evidence
    assert "unknown" not in ev.lower(), "'(unknown)' reads as a value; say why instead"
    assert "could not be read" in ev


def test_a_passing_row_names_the_encryption_it_found(client):
    seed_user()
    ev = _checks({"home": {"wifi": {"ssid": "Home", "security": "WPA2 Personal"}}})["wifi:home"].evidence
    assert "WPA2 Personal" in ev


def test_the_guard_verdict_never_prints_open_for_a_value_it_never_read(client):
    """`Weak Wi-Fi encryption (open)` was being shown for networks that were
    not open -- the word came from a fallback, not from the radio."""
    g = sec.wifi_guard({"home": {"wifi": {}}}, "2026-09-20T03:00:00Z")
    assert "open" not in g.verdict.lower()
    assert "could not be read" in g.verdict


def test_the_guard_still_calls_a_genuinely_open_network_weak():
    g = sec.wifi_guard({"home": {"wifi": {"security": "Open"}}}, "2026-09-20T03:00:00Z")
    assert "weak" in g.verdict.lower()
