"""The call budget.

COMMS.md calls this "the single most important rule in this file", and the
reason is not technical: an agent that rings six times a day gets silenced,
and a silenced agent is worth nothing. Every test here is really one test —
does ATLAS stay worth answering.

The other half is the exemption. CRITICAL must never be capped, because his
willingness to pick up is the thing the budget protects, and a CRITICAL page
is what that willingness is FOR. A budget that suppressed one would have
inverted the whole point.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import escalation
from atlas.kernel.contracts import Severity

NOW = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    escalation.reset()
    monkeypatch.delenv("ATLAS_QUIET_UNTIL", raising=False)
    yield
    escalation.reset()


def ring(n: int, *, severity=Severity.HIGH, at=NOW, answered=None):
    for i in range(n):
        escalation.record(severity, at=at - timedelta(hours=n - i))
        if answered is not None:
            escalation.record_outcome(answered)


# ---------------------------------------------------------------- the budget

def test_three_calls_is_the_limit():
    assert escalation.may_call(Severity.HIGH, now=NOW)[0] is True
    ring(3)
    allowed, why = escalation.may_call(Severity.HIGH, now=NOW)
    assert allowed is False
    assert "budget" in why


def test_critical_is_never_capped():
    """The exemption that makes the budget safe. If a fourth thing today is
    genuinely critical, the phone rings."""
    ring(10)
    assert escalation.may_call(Severity.CRITICAL, now=NOW) == (True, "")


def test_critical_calls_do_not_spend_the_budget():
    """Otherwise a bad night would silence him for the following day."""
    ring(5, severity=Severity.CRITICAL)
    assert escalation.spent_today(NOW) == 0
    assert escalation.may_call(Severity.HIGH, now=NOW)[0] is True


# --------------------------- G3: "I won't call again once you've arrived"

def test_an_answered_critical_page_taken_away_becomes_a_text_once_home():
    """He answered a CRITICAL page while he was out. Once he's home, the
    same still-open page re-rings as a text, not another call."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False, title="db down")
    escalation.record_outcome(True, at=NOW)

    allowed, why = escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                                       title="db down")
    assert allowed is False
    assert "texting" in why


def test_a_new_critical_still_rings_after_an_away_page_was_settled():
    """The promise is "I won't call again about THIS" -- a genuinely new
    emergency, under a different title, always rings, home or not."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False, title="db down")
    escalation.record_outcome(True, at=NOW)
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                               title="db down")[0] is False

    escalation.record(Severity.CRITICAL, at=NOW, title="fire")
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                               title="fire") == (True, "")
    # And the settled page stays settled -- this isn't a global reset.
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                               title="db down")[0] is False


def test_same_title_different_incident_key_still_rings_while_the_matching_key_texts():
    """The whole reason `incident_key` exists: a same-titled but genuinely
    different incident (a different check) must not inherit the settled
    state of the one that shares its title."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False, title="db down",
                      incident_key="flycheap.database")
    escalation.record_outcome(True, at=NOW)
    allowed, why = escalation.may_call(
        Severity.CRITICAL, now=NOW, home=True, title="db down",
        incident_key="flycheap.database")
    assert allowed is False
    assert "texting" in why

    # A different check, same human-readable title -- still rings.
    escalation.record(Severity.CRITICAL, at=NOW, title="db down",
                      incident_key="guardian.database")
    assert escalation.may_call(
        Severity.CRITICAL, now=NOW, home=True, title="db down",
        incident_key="guardian.database") == (True, "")
    # The first incident is unaffected by the second's arrival.
    assert escalation.may_call(
        Severity.CRITICAL, now=NOW, home=True, title="db down",
        incident_key="flycheap.database")[0] is False


def test_a_keyed_call_is_never_matched_by_title_alone():
    """A caller with the same title but no key must not inherit a keyed
    call's settled state -- the two correlation strategies never cross."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False, title="db down",
                      incident_key="flycheap.database")
    escalation.record_outcome(True, at=NOW)
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                               title="db down") == (True, "")


def test_title_fallback_matches_within_24h_but_not_beyond():
    """Without an `incident_key`, title correlation is bounded to a rolling
    24h window -- a same-titled page more than a day later is treated as a
    new, unrelated occurrence rather than "the same still-open incident"."""
    escalation.record(Severity.CRITICAL, at=NOW - timedelta(hours=25),
                      home=False, title="disk full")
    escalation.record_outcome(True, at=NOW - timedelta(hours=25))
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                               title="disk full") == (True, "")

    escalation.reset()
    escalation.record(Severity.CRITICAL, at=NOW - timedelta(hours=1),
                      home=False, title="disk full")
    escalation.record_outcome(True, at=NOW - timedelta(hours=1))
    allowed, _ = escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                                     title="disk full")
    assert allowed is False


def test_the_away_settled_call_still_rings_while_he_is_not_yet_home():
    """`home` unknown or still False changes nothing -- invariant 6."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False, title="db down")
    escalation.record_outcome(True, at=NOW)
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=False,
                               title="db down") == (True, "")
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=None,
                               title="db down") == (True, "")


def test_an_unanswered_critical_page_still_rings_even_if_he_is_home():
    """Only an *answered* page counts as settled -- one still ringing (or
    whose outcome was never learned) must keep trying."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False, title="db down")
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                               title="db down") == (True, "")


def test_a_critical_page_answered_while_already_home_is_never_downgraded():
    """The rule is specifically about a page taken away from home -- one
    answered while he was already home has nothing to soften."""
    escalation.record(Severity.CRITICAL, at=NOW, home=True, title="db down")
    escalation.record_outcome(True, at=NOW)
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True,
                               title="db down") == (True, "")


def test_an_untitled_page_is_never_treated_as_settled():
    """No title, no correlation -- the safe default is to keep ringing rather
    than guess two untitled pages are the same incident."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False)
    escalation.record_outcome(True, at=NOW)
    assert escalation.may_call(Severity.CRITICAL, now=NOW, home=True) == (True, "")


def test_may_call_without_home_or_title_behaves_exactly_as_before():
    """Every pre-G3 caller of `may_call` omits both; the new parameters must
    be purely additive."""
    escalation.record(Severity.CRITICAL, at=NOW, home=False, title="db down")
    escalation.record_outcome(True, at=NOW)
    assert escalation.may_call(Severity.CRITICAL, now=NOW) == (True, "")


def test_never_twice_in_ten_minutes():
    escalation.record(Severity.HIGH, at=NOW - timedelta(minutes=3))
    allowed, why = escalation.may_call(Severity.HIGH, now=NOW)
    assert allowed is False
    assert "ten minutes" in why
    # Past the window, it may ring again.
    assert escalation.may_call(Severity.HIGH,
                               now=NOW + timedelta(minutes=11))[0] is True


def test_the_ten_minute_rule_does_not_gag_a_critical():
    escalation.record(Severity.HIGH, at=NOW - timedelta(seconds=30))
    assert escalation.may_call(Severity.CRITICAL, now=NOW)[0] is True


def test_yesterdays_calls_do_not_count_against_today():
    for i in range(5):
        escalation.record(Severity.HIGH, at=NOW - timedelta(days=1, hours=i))
    assert escalation.spent_today(NOW) == 0
    assert escalation.may_call(Severity.HIGH, now=NOW)[0] is True


# ------------------------------------------------------ silence is information

def test_three_declines_and_he_stops_ringing():
    """COMMS.md 4. Declining three times running is information: something is
    going on, and hammering him is not the answer."""
    for i in range(3):
        escalation.record(Severity.HIGH, at=NOW - timedelta(hours=3 - i))
        escalation.record_outcome(False)
    allowed, why = escalation.may_call(Severity.HIGH, now=NOW)
    assert allowed is False
    assert "unanswered" in why
    assert escalation.backed_off(NOW) is True


def test_answering_one_clears_the_run():
    escalation.record(Severity.HIGH, at=NOW - timedelta(hours=3))
    escalation.record_outcome(False)
    escalation.record(Severity.HIGH, at=NOW - timedelta(hours=2))
    escalation.record_outcome(True)
    escalation.record(Severity.HIGH, at=NOW - timedelta(hours=1))
    escalation.record_outcome(False)
    assert escalation.declines_in_a_row(NOW) == 1
    assert escalation.backed_off(NOW) is False


def test_backing_off_still_does_not_gag_a_critical():
    for i in range(4):
        escalation.record(Severity.HIGH, at=NOW - timedelta(hours=4 - i))
        escalation.record_outcome(False)
    assert escalation.may_call(Severity.CRITICAL, now=NOW)[0] is True


def test_a_call_whose_outcome_was_never_learned_neither_counts_nor_clears():
    """Twilio does not always tell us. An unknown outcome must not be read as
    a decline — that would silence the phone off the back of missing data."""
    for i in range(3):
        escalation.record(Severity.HIGH, at=NOW - timedelta(hours=3 - i))
    assert escalation.declines_in_a_row(NOW) == 0


# ----------------------------------------------------------------- quiet hold

def test_quiet_until_holds_a_call(monkeypatch):
    monkeypatch.setenv("ATLAS_QUIET_UNTIL",
                       (NOW + timedelta(hours=2)).isoformat())
    allowed, why = escalation.may_call(Severity.HIGH, now=NOW)
    assert allowed is False and "quiet until" in why


def test_quiet_until_never_holds_a_critical(monkeypatch):
    monkeypatch.setenv("ATLAS_QUIET_UNTIL",
                       (NOW + timedelta(hours=2)).isoformat())
    assert escalation.may_call(Severity.CRITICAL, now=NOW)[0] is True


def test_an_unparseable_quiet_window_does_not_silence_the_phone(monkeypatch):
    """Failing closed here would mute ATLAS off a typo."""
    monkeypatch.setenv("ATLAS_QUIET_UNTIL", "half past never")
    assert escalation.may_call(Severity.HIGH, now=NOW)[0] is True


# --------------------------------------------------------------- the ladder

def test_routine_traffic_never_rings():
    for sev in (Severity.INFO, Severity.NORMAL):
        allowed, why = escalation.may_call(sev, now=NOW)
        assert allowed is False
        assert "warrants" in why


@pytest.mark.parametrize("sev,first", [
    (Severity.INFO, "hold for the brief"),
    (Severity.NORMAL, "post to the module's channel"),
    (Severity.HIGH, "call"),
    (Severity.CRITICAL, "call"),
])
def test_the_ladder_matches_the_spec(sev, first):
    assert escalation.ladder(sev)[0] == first


def test_critical_keeps_trying_and_urgent_does_not():
    assert len(escalation.ladder(Severity.CRITICAL)) > 2
    assert "2 minutes" in " ".join(escalation.ladder(Severity.CRITICAL))
    assert "voicemail" in " ".join(escalation.ladder(Severity.HIGH))


# ------------------------------------------------- the decline path, end to end

async def test_a_missed_call_is_recorded_and_falls_back_to_slack(monkeypatch):
    """COMMS.md 3.3, the "if declined" column. Until the status webhook
    existed this was a rule with no input: ATLAS rang and never learned
    whether anyone picked up."""
    import base64 as _b64
    import hashlib as _h
    import hmac as _hm

    from fastapi.testclient import TestClient

    from atlas.api import auth

    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("x" * 14))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")

    posted: list = []

    async def fake_notify(msg):
        posted.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)

    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)

    def signed(client, params):
        path = "/webhooks/twilio/voice/status"
        url = f"https://atlas.example.com{path}"
        data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
        sig = _b64.b64encode(
            _hm.new(b"twtok", data.encode(), _h.sha1).digest()).decode()
        return client.post(path, data=params,
                           headers={"X-Twilio-Signature": sig})

    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        escalation.record(Severity.HIGH)
        r = signed(c, {"CallStatus": "no-answer", "CallSid": "CA1"})
        assert r.status_code == 200
        assert escalation.declines_in_a_row() == 1
        assert posted, "a missed call must land in Slack instead"
        assert posted[0].topic == "decisions"

        # An answered call clears the run and posts nothing.
        posted.clear()
        escalation.record(Severity.HIGH)
        signed(c, {"CallStatus": "completed", "CallSid": "CA2"})
        assert escalation.declines_in_a_row() == 0
        assert posted == []


async def test_a_call_he_placed_never_counts_as_a_decline(monkeypatch):
    """If the number's own status callback points at this route, a call he
    rang and hung up on is not an answer to anything ATLAS rang about."""
    import base64 as _b64
    import hashlib as _h
    import hmac as _hm

    from fastapi.testclient import TestClient

    from atlas.api import auth

    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("x" * 14))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")
    posted: list = []

    async def fake_notify(msg):
        posted.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    params = {"CallStatus": "no-answer", "CallSid": "CA7", "Direction": "inbound"}
    path = "/webhooks/twilio/voice/status"
    data = f"https://atlas.example.com{path}" + "".join(
        f"{k}{params[k]}" for k in sorted(params))
    sig = _b64.b64encode(_hm.new(b"twtok", data.encode(), _h.sha1).digest()).decode()
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        escalation.record(Severity.HIGH)
        assert c.post(path, data=params,
                      headers={"X-Twilio-Signature": sig}).status_code == 200
    assert escalation.declines_in_a_row() == 0
    assert posted == []


async def test_an_unsigned_status_callback_is_refused(monkeypatch):
    """This endpoint writes to the decline counter. Left unsigned, anyone
    could silence the phone by posting three no-answers."""
    from fastapi.testclient import TestClient

    from atlas.api import auth

    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("x" * 14))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twtok")
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.com")

    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        r = c.post("/webhooks/twilio/voice/status",
                   data={"CallStatus": "no-answer"},
                   headers={"X-Twilio-Signature": "v0=nope"})
    assert r.status_code == 401


def test_an_outcome_with_no_recorded_call_still_counts():
    """The call and its outcome can arrive in different processes: a call
    placed from a shell records itself there and exits, and the webhook
    lands in the API with no memory of it. The first live decline test hit
    exactly this and the counter read zero."""
    escalation.record_outcome(False, at=NOW)
    assert escalation.declines_in_a_row(NOW) == 1
    escalation.record_outcome(False, at=NOW)
    escalation.record_outcome(False, at=NOW)
    assert escalation.backed_off(NOW) is True


def test_an_outcome_attaches_to_a_pending_call_rather_than_adding_one():
    escalation.record(Severity.HIGH, at=NOW)
    escalation.record_outcome(False, at=NOW)
    assert len(escalation._CALLS) == 1
    assert escalation._CALLS[0].answered is False


def test_a_second_outcome_does_not_overwrite_a_settled_call():
    escalation.record(Severity.HIGH, at=NOW)
    escalation.record_outcome(True, at=NOW)
    escalation.record_outcome(False, at=NOW)
    assert [c.answered for c in escalation._CALLS] == [True, False]
