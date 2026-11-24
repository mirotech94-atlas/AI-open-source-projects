"""The comms module — the check on the checker.

The bug this module exists for is not hypothetical: `SlackChannel.available()`
reads two environment variables and reports ready, so a revoked token routes
normally, raises inside `send`, gets swallowed by `notify`, and the message is
gone with nothing anywhere reading as unhealthy. Every test below is aimed at
one question: does this module ever report OK when ATLAS cannot actually reach
anyone?
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import comms as router
from atlas.kernel import escalation
from atlas.kernel.comms import Delivery, Outbound
from atlas.kernel.contracts import Health, Severity
from atlas.modules import comms as watchdog


class FakeChannel:
    def __init__(self, name, *, up=True, rich=True):
        self.name, self.rich, self.interactive = name, rich, True
        self._up = up
        self.sent: list[Outbound] = []

    async def send(self, msg):
        self.sent.append(msg)
        return f"{self.name}-ts"

    async def available(self):
        return self._up


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    router.CHANNELS.clear()
    router.LAST_SEND = None
    escalation.reset()
    for var in ("SLACK_BOT_TOKEN", "SLACK_CHANNEL", "TWILIO_ACCOUNT_SID",
                "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "TWILIO_TO",
                "QUIET_HOURS"):
        monkeypatch.delenv(var, raising=False)
    yield
    router.CHANNELS.clear()
    router.LAST_SEND = None


def install(*channels):
    for c in channels:
        router.CHANNELS[c.name] = c
    return channels


# --------------------------------------------------------------- reachable

async def test_a_voice_only_setup_is_failing_not_ok():
    """The trap this check was written for. voice is reachable and correctly
    configured, and every routine message still goes nowhere: routing reaches
    voice at CRITICAL only. The morning brief and `test-channels` both send
    at NORMAL."""
    install(FakeChannel("voice"))
    obs = await watchdog.check_reachable()
    assert obs.health is Health.FAILING
    assert "voice does not count" in obs.detail


async def test_no_registered_channels_is_unknown_not_failing():
    """Nothing registered means atlas.channels was never imported — this
    measured nothing. Invariant 7: a check that cannot run is UNKNOWN."""
    obs = await watchdog.check_reachable()
    assert obs.health is Health.UNKNOWN


async def test_it_names_the_channel_that_would_actually_take_the_message():
    """Matrix is registered and available, but Slack comes first. Reporting
    "matrix" here would send Miro looking in the wrong place."""
    install(FakeChannel("matrix"), FakeChannel("slack"))
    obs = await watchdog.check_reachable()
    assert obs.health is Health.OK
    assert obs.evidence["ranked"] == ["slack", "matrix"]
    assert obs.detail.startswith("slack would take")


async def test_a_dead_slack_falls_through_to_matrix():
    install(FakeChannel("slack", up=False), FakeChannel("matrix"))
    obs = await watchdog.check_reachable()
    assert obs.health is Health.OK
    assert obs.evidence["ranked"] == ["matrix"]


async def test_registered_but_all_unavailable_is_failing():
    install(FakeChannel("matrix", up=False), FakeChannel("slack", up=False))
    assert (await watchdog.check_reachable()).health is Health.FAILING


# --------------------------------------------------------------- last_send

async def test_a_fresh_process_is_unknown_never_ok():
    """The single most dangerous wrong answer in this module. Having sent
    nothing is not evidence that sending works."""
    obs = await watchdog.check_last_send()
    assert obs.health is Health.UNKNOWN
    assert "not an all-clear" in obs.detail


async def test_a_message_that_reached_nobody_is_failing():
    router.LAST_SEND = Delivery(datetime.now(UTC), "disk full",
                                Severity.HIGH, ())
    obs = await watchdog.check_last_send()
    assert obs.health is Health.FAILING
    assert "reached nobody" in obs.detail


async def test_a_delivered_message_is_ok_and_names_the_channel():
    router.LAST_SEND = Delivery(datetime.now(UTC), "brief",
                                Severity.NORMAL, ("slack",))
    obs = await watchdog.check_last_send()
    assert obs.health is Health.OK
    assert obs.evidence["used"] == ["slack"]


async def test_silence_alone_is_never_a_fault():
    """Deliberately not a staleness check: when nothing is wrong ATLAS sends
    nothing, and alerting on quiet trains Miro to ignore this tile."""
    router.LAST_SEND = Delivery(datetime.now(UTC) - timedelta(days=3),
                                "brief", Severity.NORMAL, ("slack",))
    assert (await watchdog.check_last_send()).health is Health.OK


async def test_a_quiet_hours_hold_is_ok_not_a_failure():
    router.LAST_SEND = Delivery(datetime.now(UTC), "brief",
                                Severity.NORMAL, ("queued",))
    obs = await watchdog.check_last_send()
    assert obs.health is Health.OK
    assert "quiet hours" in obs.detail


async def test_notify_records_what_happened(monkeypatch):
    """The wiring: a real notify() must leave the record last_send reads,
    without touching the database on the way."""
    slack, = install(FakeChannel("slack"))
    await router.notify(Outbound("t", "b", Severity.NORMAL))
    assert router.LAST_SEND.used == ("slack",)
    assert slack.sent


async def test_notify_records_a_total_failure(monkeypatch):
    audited: list = []

    async def fake_audit(event, subject, data=None):
        audited.append((event, subject))

    monkeypatch.setattr("atlas.kernel.audit.audit", fake_audit)
    install(FakeChannel("slack", up=False))
    used = await router.notify(Outbound("nobody hears this", "b",
                                        Severity.HIGH))
    assert used == []
    assert router.LAST_SEND.used == ()
    assert audited == [("comms.undelivered", "nobody hears this")]
    assert (await watchdog.check_last_send()).health is Health.FAILING


async def test_an_audit_failure_cannot_break_notify(monkeypatch):
    """Delivery has already failed once here. A database that is also
    unhappy must not turn that into an exception escaping into whatever was
    trying to raise the alarm."""
    async def boom(*_a, **_k):
        raise RuntimeError("postgres is down too")

    monkeypatch.setattr("atlas.kernel.audit.audit", boom)
    install(FakeChannel("slack", up=False))
    assert await router.notify(Outbound("t", "b", Severity.HIGH)) == []


# -------------------------------------------------------------- slack auth

async def test_a_revoked_slack_token_is_failing_not_unknown(monkeypatch):
    """auth.test answers HTTP 200 with ok:false for a dead token, so the
    status code says nothing. This is the exact blind spot the module exists
    for: available() reports ready for this token."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-revoked")
    monkeypatch.setenv("SLACK_CHANNEL", "C123")

    async def fake_post(url, **kw):
        return {"ok": False, "error": "token_revoked"}, None

    monkeypatch.setattr(watchdog, "post_json", fake_post)
    obs = await watchdog.check_slack_auth()
    assert obs.health is Health.FAILING
    assert "token_revoked" in obs.detail


async def test_a_working_slack_token_is_ok(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-good")
    monkeypatch.setenv("SLACK_CHANNEL", "C123")

    async def fake_post(url, **kw):
        return {"ok": True, "team": "storefront", "user": "atlas"}, None

    monkeypatch.setattr(watchdog, "post_json", fake_post)
    obs = await watchdog.check_slack_auth()
    assert obs.health is Health.OK
    assert obs.evidence["team"] == "storefront"


async def test_a_valid_token_with_no_channel_is_degraded(monkeypatch):
    """There is nowhere to post. Reporting OK would be a lie of omission."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-good")

    async def fake_post(url, **kw):
        return {"ok": True, "team": "storefront"}, None

    monkeypatch.setattr(watchdog, "post_json", fake_post)
    assert (await watchdog.check_slack_auth()).health is Health.DEGRADED


async def test_slack_unreachable_is_unknown_but_rejected_is_failing(monkeypatch):
    """A timeout measured nothing. A 401 measured something definite."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("SLACK_CHANNEL", "C1")

    async def timed_out(url, **kw):
        return None, "timed out"

    async def rejected(url, **kw):
        return None, "HTTP 401 from /api/auth.test — credentials rejected"

    monkeypatch.setattr(watchdog, "post_json", timed_out)
    assert (await watchdog.check_slack_auth()).health is Health.UNKNOWN
    monkeypatch.setattr(watchdog, "post_json", rejected)
    assert (await watchdog.check_slack_auth()).health is Health.FAILING


async def test_an_unconfigured_slack_is_unknown_and_says_where_to_look():
    obs = await watchdog.check_slack_auth()
    assert obs.health is Health.UNKNOWN
    assert "api.slack.com" in obs.detail


# ------------------------------------------------------------- twilio auth

async def test_a_suspended_twilio_account_is_failing(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-value")
    monkeypatch.setenv("TWILIO_FROM", "+15550001111")
    monkeypatch.setenv("TWILIO_TO", "+15550002222")

    async def fake_get(url, **kw):
        return {"status": "suspended"}, None

    monkeypatch.setattr(watchdog, "get_json", fake_get)
    obs = await watchdog.check_twilio_auth()
    assert obs.health is Health.FAILING
    assert "suspended" in obs.detail


async def test_an_active_twilio_account_is_ok(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-value")
    monkeypatch.setenv("TWILIO_FROM", "+15550001111")
    monkeypatch.setenv("TWILIO_TO", "+15550002222")

    async def fake_get(url, **kw):
        return {"status": "active"}, None

    monkeypatch.setattr(watchdog, "get_json", fake_get)
    assert (await watchdog.check_twilio_auth()).health is Health.OK


async def test_twilio_without_a_number_to_call_is_degraded(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-value")

    async def fake_get(url, **kw):
        return {"status": "active"}, None

    monkeypatch.setattr(watchdog, "get_json", fake_get)
    obs = await watchdog.check_twilio_auth()
    assert obs.health is Health.DEGRADED
    assert "TWILIO_FROM" in obs.detail


async def test_a_missing_twilio_status_field_is_unknown_not_ok(monkeypatch):
    """A shape we did not expect must never be defaulted into a healthy
    reading — the Phase 3 lesson, applied here."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-value")

    async def fake_get(url, **kw):
        return {"friendly_name": "atlas"}, None

    monkeypatch.setattr(watchdog, "get_json", fake_get)
    assert (await watchdog.check_twilio_auth()).health is Health.UNKNOWN


async def test_the_twilio_auth_token_never_reaches_an_observation(monkeypatch):
    """The basic-auth header is built from a credential. If any part of it
    reaches a detail string it lands in Postgres and then in a prompt."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "sk_live_SUPERSECRETVALUE")

    async def fake_get(url, **kw):
        return None, f"HTTP 401 for {kw['headers']['Authorization']}"

    monkeypatch.setattr(watchdog, "get_json", fake_get)
    obs = await watchdog.check_twilio_auth()
    assert "SUPERSECRET" not in obs.detail
    assert obs.health is Health.FAILING


# ------------------------------------------------- twilio number formatting

def _twilio_env(monkeypatch, to="+15550002222", frm="+15550001111"):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret-token-value")
    monkeypatch.setenv("TWILIO_FROM", frm)
    monkeypatch.setenv("TWILIO_TO", to)

    async def fake_get(url, **kw):
        return {"status": "active"}, None

    monkeypatch.setattr(watchdog, "get_json", fake_get)


async def test_a_number_without_its_country_code_is_degraded(monkeypatch):
    """The real bug: TWILIO_TO stored as bare digits. Twilio sends `From` in
    E.164 and the inbound webhook compares exactly, so every reply was
    refused with a 403 while this check reported OK."""
    _twilio_env(monkeypatch, to="5550002222")
    obs = await watchdog.check_twilio_auth()
    assert obs.health is Health.DEGRADED
    assert "E.164" in obs.detail and "TWILIO_TO" in obs.detail


async def test_a_well_formed_pair_is_ok(monkeypatch):
    _twilio_env(monkeypatch)
    assert (await watchdog.check_twilio_auth()).health is Health.OK


@pytest.mark.parametrize("bad", [
    "5550002222",           # no country code
    "+1 555 000 2222",      # spaces
    "+1-555-000-2222",      # dashes
    "(555) 000-2222",       # national format
    "+05550002222",         # country code starting 0
])
async def test_every_shape_that_cannot_match_is_caught(monkeypatch, bad):
    _twilio_env(monkeypatch, to=bad)
    assert (await watchdog.check_twilio_auth()).health is Health.DEGRADED


async def test_the_phone_number_itself_is_never_recorded(monkeypatch):
    """An Observation lands in Postgres and can reach a reasoner prompt. The
    shape is the whole diagnosis; the number is personal data."""
    _twilio_env(monkeypatch, to="5551234567")
    obs = await watchdog.check_twilio_auth()
    assert "5551234567" not in obs.detail
    assert "10 characters" in obs.detail and "no leading +" in obs.detail


# ------------------------------------------------------------ module shape

def test_the_module_loads_with_no_credentials_at_all():
    """It must never disable itself. "ATLAS cannot reach you" is the finding,
    and it is most true exactly when nothing is configured."""
    assert watchdog.MODULE.requires_env == ()


def test_the_module_offers_no_actions():
    """Watching the reporting path never means changing it."""
    assert watchdog.MODULE.actions() == ()


# ----------------------------------------------------- slack channel membership

def _slack_env(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-good")
    monkeypatch.setenv("SLACK_CHANNEL", "CDEF")
    monkeypatch.setenv("SLACK_CHANNELS", "cloudhost:CVPS,decisions:CDEC")
    monkeypatch.setenv("SLACK_INSTAGRAM_REELS_CHANNEL", "CREELS")
    # The Academy drafts channel has a default id; here it shares the reels one.
    monkeypatch.setenv("ACADEMY_TUTORIALS_SLACK_CHANNEL", "CREELS")
    # So do the agency topics (cards and alerts): here they share decisions.
    monkeypatch.setenv("SLACK_AGENCY_DECISIONS_CHANNEL", "CDEC")
    monkeypatch.setenv("SLACK_AGENCY_ALERTS_CHANNEL", "CDEC")


def _fake_slack(monkeypatch, members: dict[str, bool], broken: set[str] = frozenset()):
    names = {"CVPS": "cloudhost", "CDEC": "atlas-decisions", "CREELS": "reels",
             "CDEF": "mail-calender"}

    async def fake_post(url, **kw):
        return {"ok": True, "user": "atlas"}, None

    async def fake_get(url, **kw):
        cid = kw["params"]["channel"]
        if cid in broken:
            return None, "timed out"
        return {"ok": True, "channel": {"name": names[cid],
                                        "is_member": members.get(cid, False)}}, None

    monkeypatch.setattr(watchdog, "post_json", fake_post)
    monkeypatch.setattr(watchdog, "get_json", fake_get)


async def test_a_channel_the_bot_is_not_in_is_degraded_and_says_how_to_fix(monkeypatch):
    """The 2026-09-27 case: cards reached #cloudhost through chat:write.public,
    but Miro's reply there never reached ATLAS."""
    _slack_env(monkeypatch)
    _fake_slack(monkeypatch, {"CDEC": True, "CREELS": True})
    obs = await watchdog.check_slack_channels()
    assert obs.health is Health.DEGRADED
    assert "#cloudhost (cloudhost)" in obs.detail and "#mail-calender (default)" in obs.detail
    assert "/invite @atlas" in obs.detail
    assert "#reels" not in obs.detail


async def test_every_channel_joined_is_ok(monkeypatch):
    _slack_env(monkeypatch)
    _fake_slack(monkeypatch, {c: True for c in ("CVPS", "CDEC", "CREELS", "CDEF")})
    obs = await watchdog.check_slack_channels()
    assert obs.health is Health.OK and obs.evidence["channels"] == 4


async def test_an_unreadable_channel_is_unknown_never_ok(monkeypatch):
    _slack_env(monkeypatch)
    _fake_slack(monkeypatch, {c: True for c in ("CVPS", "CDEC", "CDEF")},
                broken={"CREELS"})
    obs = await watchdog.check_slack_channels()
    assert obs.health is Health.UNKNOWN and "CREELS" in obs.detail


async def test_no_token_measures_nothing(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert (await watchdog.check_slack_channels()).health is Health.UNKNOWN


def test_the_membership_check_never_joins():
    """Joining is an outside-world write; a check only reads."""
    import inspect
    src = inspect.getsource(watchdog.check_slack_channels)
    assert "conversations.join" not in src.split('"""', 2)[2]
    assert "conversations.invite" not in src


async def test_after_a_restart_the_last_real_send_is_reported_with_its_time(
        monkeypatch):
    """A deploy empties LAST_SEND; the check's own last reading of a real
    send is reported as that, dated, instead of UNKNOWN after every deploy."""
    from atlas.kernel import state
    router.LAST_SEND = None

    async def fetch_one(sql, *args):
        return {"health": "ok", "evidence": {"used": ["slack"],
                                             "sent_at": "2026-09-28T01:40:00+00:00"}}

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    obs = await watchdog.check_last_send()
    assert obs.health is Health.OK
    assert "since the restart" in obs.detail and "2026-09-28 01:40 UTC" in obs.detail


async def test_after_a_restart_a_send_that_reached_nobody_still_fails(monkeypatch):
    from atlas.kernel import state
    router.LAST_SEND = None

    async def fetch_one(sql, *args):
        return {"health": "failing", "evidence": {"used": [],
                                                  "sent_at": "2026-09-28T01:40:00+00:00"}}

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    assert (await watchdog.check_last_send()).health is Health.FAILING
