"""The owner phone call: `tutorco.call_owner`, its escalation job and the chat "call me".

Proved here (Twilio, the database and the executor are always faked; no call is
placed):
  * the action is Tier 1 with a dry run and a rollback, and takes no number and no
    text: only a reason from a closed list and an event id;
  * off unless OWNER_CALLS is on; the number is TUTORCO_OWNER_WHATSAPP and nothing else;
    a WhatsApp sender cannot place a voice call, so it is refused, not guessed;
  * the script is fixed text in code: TwiML with `<Say>` only, nothing from the event;
  * one call per event, ever; a daily cap; a refused call frees its slot, one that
    may have gone out keeps it;
  * escalation: nothing when off, nothing under watch-only, nothing in quiet hours,
    a call for an unacknowledged event, a Slack note (once) when it could not be placed;
  * the chat "call me" is a strict regex, no model, goes through the executor, ignores
    quiet hours and says honestly when it cannot call.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest

from atlas.api import whatsapp
from atlas.kernel import idempotency, owner_calls
from atlas.kernel.contracts import ActionResult, Tier
from atlas.modules.tutorco import MODULE, calls

NOW = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
NUMBER = "+15551230001"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("OWNER_CALLS", "on")
    monkeypatch.setenv("TUTORCO_OWNER_WHATSAPP", NUMBER)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM", "+15559990000")
    monkeypatch.delenv("OWNER_CALL_FROM", raising=False)
    monkeypatch.delenv("CALL_MAX_PER_DAY", raising=False)
    monkeypatch.delenv("TUTORCO_WHATSAPP_FROM", raising=False)


@pytest.fixture
def claims(monkeypatch):
    """An in-memory effect_claims."""
    state: dict[str, str] = {}

    async def claim(action, key):
        if key in state:
            return idempotency.SENT if state[key] == "sent" else idempotency.IN_DOUBT
        state[key] = "sending"
        return idempotency.CLAIMED

    async def settle(action, key, detail=""):
        state[key] = "sent"

    async def release(action, key):
        if state.get(key) == "sending":
            del state[key]

    monkeypatch.setattr(idempotency, "claim", claim)
    monkeypatch.setattr(idempotency, "settle", settle)
    monkeypatch.setattr(idempotency, "release", release)
    return state


@pytest.fixture
def twilio(monkeypatch):
    """Records every call that would be placed."""
    seen: dict = {"posts": [], "result": (True, "CAfake")}

    async def post(to, twiml):
        seen["posts"].append((to, twiml))
        return seen["result"]

    async def today():
        return seen.get("today", 0)

    monkeypatch.setattr(calls, "place_call", post)
    monkeypatch.setattr(calls, "_calls_today", today)
    return seen


# ------------------------------------------------------------------ the action

def test_the_action_is_tier_one_with_a_dry_run_and_a_rollback():
    action = {a.name: a for a in MODULE.actions()}["tutorco.call_owner"]
    assert action.tier is Tier.AUTO
    assert action.dry_run is not None and action.rollback is not None
    assert set(action.params) == {"reason", "event"}      # no number, no text


def test_no_model_can_pick_the_call(monkeypatch):
    """Only the escalation job and the strict "call me" parse raise it: a model
    choosing `requested` would ring him with "You asked me to call"."""
    from atlas.kernel import intent
    from atlas.kernel.permissions import Decision
    from atlas.kernel.registry import REGISTRY
    action = {a.name: a for a in MODULE.actions()}["tutorco.call_owner"]
    assert action.card_only is True
    monkeypatch.setitem(REGISTRY.actions, "tutorco.call_owner", action)
    monkeypatch.setitem(REGISTRY.decisions, "tutorco.call_owner",
                        Decision(True, Tier.AUTO, ""))
    assert "tutorco.call_owner" not in intent.catalog()


async def test_an_event_already_called_is_not_re_executed(monkeypatch):
    """Both escalation reads skip events that already have a call claim, so a
    still-failing event does not burn the executor's hourly budget every pass."""
    seen: list[str] = []

    async def fetch_all(sql, *args):
        seen.append(sql)
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    await owner_calls._unacked_alerts(NOW)
    await owner_calls._unacked_cards(NOW)
    assert len(seen) == 2
    assert all("k.action = 'tutorco.call_owner'" in sql for sql in seen)


async def test_off_by_default(env, claims, twilio, monkeypatch):
    monkeypatch.delenv("OWNER_CALLS")
    res = await calls.call_owner("requested", "req1")
    assert not res.ok and "OWNER_CALLS" in res.detail
    assert twilio["posts"] == []


async def test_the_call_goes_to_the_env_number_only(env, claims, twilio):
    res = await calls.call_owner("escalation_alert", "obs41")
    assert res.ok
    (to, _twiml), = twilio["posts"]
    assert to == NUMBER


async def test_a_whatsapp_sender_cannot_place_a_call(env, claims, twilio, monkeypatch):
    monkeypatch.setenv("TWILIO_FROM", "whatsapp:+14155238886")
    res = await calls.call_owner("requested", "req1")
    assert not res.ok and "voice" in res.detail.lower()
    assert twilio["posts"] == []


async def test_no_sender_is_refused(env, claims, twilio, monkeypatch):
    monkeypatch.delenv("TWILIO_FROM")
    res = await calls.call_owner("requested", "req1")
    assert not res.ok and twilio["posts"] == []


async def test_a_dedicated_voice_number_wins(env, claims, twilio, monkeypatch):
    monkeypatch.setenv("TWILIO_FROM", "whatsapp:+14155238886")
    monkeypatch.setenv("OWNER_CALL_FROM", "+15558887777")
    assert (await calls.call_owner("requested", "req1")).ok


@pytest.mark.parametrize("reason,event", [
    ("anything you like", "req1"), ("requested", ""), ("requested", "has space"),
    ("requested", "x" * 99), ("requested", "https://evil.example")])
async def test_bad_params_are_refused(env, claims, twilio, reason, event):
    res = await calls.call_owner(reason, event)
    assert not res.ok and twilio["posts"] == []


@pytest.mark.parametrize("reason", sorted(owner_calls.SCRIPTS))
def test_the_script_is_fixed_say_only_twiml(reason):
    root = ET.fromstring(owner_calls.twiml(reason))
    assert root.tag == "Response"
    assert [child.tag for child in root] == ["Say"]
    assert (root[0].text or "").strip()


def test_the_script_carries_nothing_from_the_event():
    assert owner_calls.twiml("escalation_alert") == owner_calls.twiml("escalation_alert")
    with pytest.raises(KeyError):
        owner_calls.twiml("<Dial>+1999</Dial>")


async def test_one_call_per_event_ever(env, claims, twilio):
    assert (await calls.call_owner("escalation_alert", "obs41")).ok
    again = await calls.call_owner("escalation_alert", "obs41")
    assert "already" in again.detail
    assert len(twilio["posts"]) == 1


async def test_the_daily_cap(env, claims, twilio, monkeypatch):
    monkeypatch.setenv("CALL_MAX_PER_DAY", "2")
    twilio["today"] = 2
    res = await calls.call_owner("requested", "req9")
    assert not res.ok and "limit" in res.detail
    assert twilio["posts"] == [] and claims == {}


async def test_the_cap_defaults_to_three(env, monkeypatch):
    assert owner_calls.max_per_day() == 3


async def test_a_refused_call_frees_its_slot(env, claims, twilio):
    twilio["result"] = (False, "HTTP 400 from api.twilio.com (code 21212)")
    assert not (await calls.call_owner("requested", "req1")).ok
    assert claims == {}


async def test_a_call_that_may_have_gone_out_keeps_its_slot(env, claims, twilio):
    twilio["result"] = (False, "timed out")
    res = await calls.call_owner("requested", "req1")
    assert not res.ok and "may have" in res.detail
    assert claims == {"req1": "sending"}
    twilio["result"] = (True, "CAfake")
    again = await calls.call_owner("requested", "req1")
    assert "already" in again.detail
    assert len(twilio["posts"]) == 1


async def test_dry_run_places_nothing(env, claims, twilio):
    res = await calls.call_owner_dry("requested", "req1")
    assert res.ok and twilio["posts"] == [] and claims == {}


async def test_dry_run_says_why_not(env, twilio, monkeypatch):
    monkeypatch.delenv("OWNER_CALLS")
    res = await calls.call_owner_dry("requested", "req1")
    assert not res.ok and "OWNER_CALLS" in res.detail


async def test_rollback_is_honest():
    res = await calls.call_owner_rollback("req1")
    assert res.ok and "cannot be" in res.detail


# ------------------------------------------------------------------ escalation

@pytest.fixture
def job(env, monkeypatch):
    seen: dict = {"exec": [], "slack": [], "alerts": [], "cards": [],
                  "sensor": [], "result": ActionResult(True, "called")}

    async def alerts(now):
        return list(seen["alerts"])

    async def cards(now):
        return list(seen["cards"])

    async def sensor(now):
        return list(seen["sensor"])

    async def execute(req):
        seen["exec"].append((req.action, dict(req.params)))
        return seen["result"]

    async def slack(event, why):
        seen["slack"].append((event, why))
        return True

    monkeypatch.setattr(owner_calls, "_unacked_alerts", alerts)
    monkeypatch.setattr(owner_calls, "_unacked_cards", cards)
    monkeypatch.setattr(owner_calls, "_unacked_sensor", sensor)
    monkeypatch.setattr(owner_calls, "execute", execute)
    monkeypatch.setattr(owner_calls, "_slack_note", slack)
    monkeypatch.setattr(owner_calls, "watch_only", lambda: False)
    monkeypatch.setattr(owner_calls, "quiet_now", lambda: False)
    return seen


def test_defaults():
    assert owner_calls.escalate_minutes() == 15


async def test_escalation_is_off_by_default(job, monkeypatch):
    monkeypatch.delenv("OWNER_CALLS")
    job["alerts"] = ["obs1"]
    assert (await owner_calls.run(NOW)) == {"enabled": False}
    assert job["exec"] == []


async def test_nothing_is_called_under_watch_only(job, monkeypatch):
    monkeypatch.setattr(owner_calls, "watch_only", lambda: True)
    job["alerts"] = ["obs1"]
    out = await owner_calls.run(NOW)
    assert out["watch_only"] and job["exec"] == []


async def test_quiet_hours_hold_an_escalation(job, monkeypatch):
    monkeypatch.setattr(owner_calls, "quiet_now", lambda: True)
    job["alerts"] = ["obs1"]
    out = await owner_calls.run(NOW)
    assert job["exec"] == [] and out["quiet"]


async def test_an_unacknowledged_alert_and_card_each_get_one_call(job):
    job["alerts"] = ["obs41"]
    job["cards"] = ["card7"]
    out = await owner_calls.run(NOW)
    assert job["exec"] == [
        ("tutorco.call_owner", {"reason": "escalation_alert", "event": "obs41"}),
        ("tutorco.call_owner", {"reason": "escalation_card", "event": "card7"})]
    assert out["called"] == 2


async def test_nothing_to_escalate_calls_nobody(job):
    assert (await owner_calls.run(NOW))["called"] == 0
    assert job["exec"] == []


async def test_a_call_that_could_not_be_placed_leaves_a_slack_note(job):
    job["alerts"] = ["obs41"]
    job["result"] = ActionResult(False, "not placed: Twilio credentials are not configured")
    out = await owner_calls.run(NOW)
    why = "not placed: Twilio credentials are not configured"
    assert out["called"] == 0 and job["slack"] == [("obs41", why)]


async def test_a_call_already_made_is_not_noted(job):
    job["alerts"] = ["obs41"]
    job["result"] = ActionResult(True, "already called for this event")
    await owner_calls.run(NOW)
    assert job["slack"] == []


async def test_the_slack_note_is_claimed_once_per_event(env, claims, monkeypatch):
    sent: list = []

    async def notify(out):
        sent.append(out)
        return True

    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    assert await owner_calls._slack_note("obs41", "not placed: x")
    assert not await owner_calls._slack_note("obs41", "not placed: x")
    assert len(sent) == 1


# ------------------------------------------------------------------ chat: call me

@pytest.mark.parametrize("text", [
    "call me", "Call me", "call me please", "Please call me!", "can you call me?",
    "could you give me a call", "give me a call now", "ring me", "phone me asap"])
def test_call_me_phrases(text):
    assert owner_calls.is_call_request(text)


@pytest.mark.parametrize("text", [
    "call me a taxi", "don't call me", "call me when the build is green",
    "should I call you?", "call", "call the client", "what is the status",
    "yes call me tomorrow at nine"])
def test_other_phrases_are_not_call_requests(text):
    assert not owner_calls.is_call_request(text)


@pytest.fixture
def chat(env, monkeypatch):
    seen: dict = {"exec": [], "spawned": [], "preview": ActionResult(True, "ok")}

    async def execute(req):
        seen["exec"].append((req.action, dict(req.params)))
        return ActionResult(True, "called")

    async def preview(name, params):
        seen["preview_args"] = (name, dict(params))
        return seen["preview"]

    def spawn(coro, name):
        seen["spawned"].append(name)
        seen["coro"] = coro

    monkeypatch.setattr(owner_calls, "execute", execute)
    monkeypatch.setattr(owner_calls, "preview", preview)
    monkeypatch.setattr(owner_calls, "_spawn", spawn)
    monkeypatch.setattr(owner_calls, "watch_only", lambda: False)
    monkeypatch.setattr(owner_calls, "quiet_now", lambda: True)   # must not matter
    return seen


async def test_call_me_runs_the_executor_and_ignores_quiet_hours(chat):
    said = await owner_calls.handle_call_request("SM123")
    assert "calling" in said.lower()
    await chat["coro"]
    assert chat["exec"] == [("tutorco.call_owner", {"reason": "requested",
                                                "event": "reqSM123"})]


async def test_call_me_when_calls_are_off_says_so(chat, monkeypatch):
    monkeypatch.delenv("OWNER_CALLS")
    said = await owner_calls.handle_call_request("SM123")
    assert "off" in said.lower() and chat["spawned"] == []


async def test_call_me_under_watch_only_says_so(chat, monkeypatch):
    monkeypatch.setattr(owner_calls, "watch_only", lambda: True)
    said = await owner_calls.handle_call_request("SM123")
    assert "watch" in said.lower() and chat["spawned"] == []


async def test_call_me_that_cannot_be_placed_says_why(chat):
    chat["preview"] = ActionResult(False, "would not call: no voice number")
    said = await owner_calls.handle_call_request("SM123")
    assert "can't call" in said.lower() and "no voice number" in said
    assert chat["spawned"] == []


async def test_the_webhook_branch_answers_call_me_without_a_model(chat, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("the model was reached")

    monkeypatch.setattr(whatsapp, "answer", boom)
    said = await whatsapp.owner_commands("call me", "SM123")
    assert said is not None and "calling" in said.lower()
    chat["coro"].close()
    assert await whatsapp.owner_commands("how are things?", "SM124") is None


# ------------------------------------------------------------------ urgent cards

async def test_an_urgent_card_call_still_obeys_the_daily_cap(env, claims, twilio):
    """An urgent card reaches the same `tutorco.call_owner` action as an alert: three
    a day in all, whatever marked it urgent."""
    for n in range(3):
        assert (await calls.call_owner("escalation_card", f"card{n}")).ok
        twilio["today"] = n + 1
    fourth = await calls.call_owner("escalation_card", "card9")
    assert not fourth.ok and "limit" in fourth.detail
    assert len(twilio["posts"]) == 3


async def test_an_urgent_card_call_stays_card_only_and_quiet_under_watch_only(job):
    action = {a.name: a for a in MODULE.actions()}["tutorco.call_owner"]
    assert action.card_only is True and action.tier is Tier.AUTO
    job["cards"] = ["card7"]
    owner_calls.watch_only = lambda: True               # restored by `job`'s patch
    out = await owner_calls.run(NOW)
    assert job["exec"] == [] and out["watch_only"]


async def test_the_card_query_reads_only_the_code_written_flag(monkeypatch):
    seen: list[str] = []

    async def fetch_all(sql, *args):
        seen.append(sql)
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    await owner_calls._unacked_cards(NOW)
    assert "p.evidence->>'urgent' = 'true'" in seen[0]


# ------------------------------------------------------------------ sensor_serious

CAM_SCRIPT = ("ATLAS: the house sensors raised a serious alert. "
              "Check your WhatsApp or the portal.")


def test_the_sensor_script_is_fixed_text():
    assert owner_calls.SCRIPTS["sensor_serious"] == CAM_SCRIPT
    assert CAM_SCRIPT in owner_calls.twiml("sensor_serious")


async def test_a_sensor_call_goes_through_the_same_action(env, claims, twilio):
    res = await calls.call_owner("sensor_serious", "cam41")
    assert res.ok and twilio["posts"][0][0] == NUMBER
    assert CAM_SCRIPT in twilio["posts"][0][1]
    again = await calls.call_owner("sensor_serious", "cam41")
    assert "already called" in again.detail and len(twilio["posts"]) == 1


async def test_sensor_calls_share_the_daily_cap(env, claims, twilio):
    twilio["today"] = 3
    res = await calls.call_owner("sensor_serious", "cam42")
    assert not res.ok and "limit" in res.detail


async def test_a_serious_sensor_event_gets_one_call(job):
    job["sensor"] = ["cam41"]
    out = await owner_calls.run(NOW)
    assert job["exec"] == [("tutorco.call_owner", {"reason": "sensor_serious",
                                               "event": "cam41"})]
    assert out["called"] == 1


async def test_a_serious_sensor_call_is_not_held_by_quiet_hours(job, monkeypatch):
    monkeypatch.setattr(owner_calls, "quiet_now", lambda: True)
    job["sensor"] = ["cam41"]
    job["alerts"] = ["obs1"]
    out = await owner_calls.run(NOW)
    assert job["exec"] == [("tutorco.call_owner", {"reason": "sensor_serious",
                                               "event": "cam41"})]
    assert out["called"] == 1 and out["held"] == 1


async def test_a_sensor_call_is_still_off_without_the_switch_or_in_watch_only(
        job, monkeypatch):
    job["sensor"] = ["cam41"]
    monkeypatch.setattr(owner_calls, "watch_only", lambda: True)
    assert (await owner_calls.run(NOW))["watch_only"] and job["exec"] == []
    monkeypatch.delenv("OWNER_CALLS")
    assert (await owner_calls.run(NOW)) == {"enabled": False}


@pytest.fixture
def cam_db(monkeypatch):
    seen: dict = {"sql": [], "rows": [], "inbound": None}

    async def fetch_all(sql, *args):
        seen["sql"].append(sql)
        return list(seen["rows"])

    async def inbound():
        return seen["inbound"]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    monkeypatch.setattr(owner_calls, "_last_inbound", inbound)
    return seen


def row(event_id, minutes_ago, status="sent"):
    return {"id": event_id, "sent_at": NOW - timedelta(minutes=minutes_ago),
            "status": status}


async def test_the_sensor_query_reads_level_three_sent_alerts_not_yet_called(cam_db):
    await owner_calls._unacked_sensor(NOW)
    sql = cam_db["sql"][0]
    assert "alert_level = 3" in sql and "'sensor_alert'" in sql
    assert "o.status IN ('sent', 'sending', 'failed')" in sql
    assert "action = 'tutorco.call_owner'" in sql          # one call per event
    assert "alert_level = 2" not in sql


async def test_the_sensor_detector_returns_cam_ids(cam_db):
    cam_db["rows"] = [row(41, 5)]
    assert await owner_calls._unacked_sensor(NOW) == ["cam41"]


async def test_a_refused_send_is_called_at_once_even_after_the_owner_wrote(cam_db):
    # WhatsApp refused it (24 h window closed): the Slack stand-in waits for the
    # morning in quiet hours, so the call is what reaches him.
    cam_db["rows"] = [row(41, 0.5, "failed"), row(42, 0.5, "sending"),
                      row(43, 5, "sending")]
    cam_db["inbound"] = NOW - timedelta(seconds=10)
    assert await owner_calls._unacked_sensor(NOW) == ["cam41", "cam43"]


async def test_a_sent_alert_waits_for_the_grace(cam_db):
    cam_db["rows"] = [row(41, 1), row(42, 3)]
    assert await owner_calls._unacked_sensor(NOW) == ["cam42"]


async def test_a_sensor_call_survives_unreadable_other_events(monkeypatch):
    async def broken(now):
        raise RuntimeError("db")

    async def sensor(now):
        return ["cam7"]

    placed: list = []

    async def place(reason, event, why):
        placed.append((reason, event))
        return True, "ok"

    monkeypatch.setattr(owner_calls, "enabled", lambda: True)
    monkeypatch.setattr(owner_calls, "_unacked_alerts", broken)
    monkeypatch.setattr(owner_calls, "_unacked_sensor", sensor)
    monkeypatch.setattr(owner_calls, "_place", place)
    monkeypatch.setattr(owner_calls, "watch_only", lambda: False)
    monkeypatch.setattr(owner_calls, "quiet_now", lambda: True)
    out = await owner_calls.run(NOW)
    assert placed == [("sensor_serious", "cam7")] and out["called"] == 1


async def test_nothing_after_twenty_minutes(cam_db):
    cam_db["rows"] = [row(41, 21), row(42, 19)]
    assert await owner_calls._unacked_sensor(NOW) == ["cam42"]


async def test_nothing_when_the_owner_wrote_back(cam_db):
    cam_db["rows"] = [row(41, 5), row(42, 3)]
    cam_db["inbound"] = NOW - timedelta(minutes=4)     # after 41, before 42
    assert await owner_calls._unacked_sensor(NOW) == ["cam42"]
    cam_db["inbound"] = NOW - timedelta(minutes=1)
    assert await owner_calls._unacked_sensor(NOW) == []


async def test_an_unreadable_inbound_means_not_acknowledged_is_unknown(cam_db,
                                                                      monkeypatch):
    async def boom():
        raise RuntimeError("db down")

    cam_db["rows"] = [row(41, 5)]
    monkeypatch.setattr(owner_calls, "_last_inbound", boom)
    assert await owner_calls._unacked_sensor(NOW) == ["cam41"]


async def test_a_sensor_read_failure_does_not_stop_the_other_escalations(
        job, monkeypatch):
    async def boom(now):
        raise RuntimeError("db down")

    monkeypatch.setattr(owner_calls, "_unacked_sensor", boom)
    job["alerts"] = ["obs41"]
    out = await owner_calls.run(NOW)
    assert out["called"] == 1
