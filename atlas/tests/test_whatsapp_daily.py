"""The 8 PM WhatsApp agent report: format, UNKNOWN, once a day, off by default,
watch-only, Slack fallback, split."""
from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import agent_activity, agent_report, scheduler, whatsapp_daily
from atlas.kernel.contracts import ActionResult, Tier
from atlas.modules.tutorco import MODULE, notify

# 8:00 PM New York on 2026-11-10 (EST, UTC-5).
NOW = datetime(2026, 11, 11, 1, 0, tzinfo=UTC)
DAY = "2026-11-10"
NUMBER = "+15551230001"
ROSTER = [("content", "Mark", "Content"), ("bizops", "Bea", "Bizops"),
          ("seo", "Sage", "SEO"), ("sales", "Sam", "Sales"),
          ("research", "Rex", "Research"), ("sites", "Felix", "Sites")]


def _seat(key):
    return {"name": key, "waiting": [], "counts": {"in_flight": 0}, "jobs": [],
            "headline": ""}


def _assembled(**over):
    at = NOW - timedelta(seconds=1)
    kwargs = {"now": NOW, "roster": ROSTER, "modules_of": {k: {k} for k, *_ in ROSTER},
              "board": {"agents": [_seat(k) for k, *_ in ROSTER]},
              "board_at": at, "last_runs": [], "last_runs_at": at, "y_runs": [],
              "y_events": [], "y_at": at, "recent_runs": [], "recent_events": [],
              "checks": [], "issues_at": at, "y_start": NOW - timedelta(hours=24),
              "y_end": NOW, "errors": {}}
    kwargs.update(over)
    return agent_activity.assemble(**kwargs)


def _patch_read(monkeypatch, data=None, error=None):
    async def read(now=None):
        if error:
            raise error
        return data

    monkeypatch.setattr(agent_report, "read", read)


# ------------------------------------------------------------ format

async def test_the_message_is_a_short_human_note_with_one_line_per_agent(
        monkeypatch):
    y = NOW - timedelta(hours=3)
    runs = [{"agent": "seo", "runs": 4, "failed": 1, "last_at": y, "last_failed_at": y}]
    card = {"id": 7, "action": "tutorco.publish_post", "tier": 2, "reason": "x",
            "created_at": None, "expires_at": None}
    board = {"agents": [{**_seat(k), "waiting": [card] if k == "seo" else []}
                        for k, *_ in ROSTER]}
    _patch_read(monkeypatch, _assembled(y_runs=runs, recent_runs=runs, board=board))
    (text,) = await whatsapp_daily.digest(NOW)
    lines = text.splitlines()
    assert lines[0] == ("\U0001F3E2 Agency \u00B7 \U0001F4CA "
                        "Last 24 hours, as of 8:00 PM ET")
    assert [ln.split(":")[0].split()[-1] for ln in lines[1:-1]] == [
        "Mark", "Bea", "Sage", "Sam", "Rex", "Felix"]
    assert lines[-1] == "Reply with an agent's name for details \U0001F642"
    assert "\U0001F7E1 Sage: did 0 things, 1 failed - 1 waiting for you" in text
    assert "\U0001F7E2 Mark: all good, a quiet day" in text
    assert "did:" not in text and "needs help" not in text
    assert len(text) <= whatsapp_daily.MAX_CHARS


# ------------------------------------------------------------ UNKNOWN

async def test_an_unreadable_source_says_cant_read_never_zero_or_green(monkeypatch):
    _patch_read(monkeypatch, _assembled(
        y_runs=None, recent_runs=None, y_events=None, recent_events=None,
        errors={"agent_runs": "db down"}))
    (text,) = await whatsapp_daily.digest(NOW)
    assert "can't read" in text and "\u26AA" in text
    assert "UNKNOWN" not in text and "checks not reading" not in text
    assert "\U0001F7E2" not in text and "all good" not in text
    assert " 0 things" not in text and "quiet day" not in text


async def test_a_failed_read_makes_every_agent_grey(monkeypatch):
    _patch_read(monkeypatch, error=RuntimeError("db down"))
    (text,) = await whatsapp_daily.digest(NOW)
    for name in ("Mark", "Bea", "Sage", "Sam", "Rex", "Felix"):
        assert f"\u26AA {name}: I couldn't read {name} today." in text
    assert "\U0001F7E2" not in text and "8:00 PM ET" in text


# ------------------------------------------------------------ split

async def test_a_long_message_is_split_in_whole_lines_under_the_limit(monkeypatch):
    big = [{"agent": k, "runs": 3, "failed": 0, "last_at": None, "last_failed_at": None}
           for k, *_ in ROSTER]
    _patch_read(monkeypatch, _assembled(y_runs=big, recent_runs=big))
    parts = await whatsapp_daily.digest(NOW, limit=140)
    assert len(parts) > 1 and all(len(p) <= 140 for p in parts)
    assert parts[0].splitlines()[0].endswith(f"(1/{len(parts)})")
    lamps = "\U0001F7E2\U0001F7E1\U0001F534\u26AA"
    names = [ln.split(":")[0].split()[-1] for p in parts for ln in p.splitlines()[1:]
             if ln[0] in lamps]
    assert names == ["Mark", "Bea", "Sage", "Sam", "Rex", "Felix"]


def test_split_cuts_a_single_overlong_line_as_a_last_resort():
    assert whatsapp_daily.split("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]


# ------------------------------------------------------------ the action

@pytest.fixture
def box(monkeypatch):
    b = types.SimpleNamespace(rows={}, sent=[], ok=True)
    for k in ("TUTORCO_OWNER_WHATSAPP", "TUTORCO_WHATSAPP_FROM", "TWILIO_FROM",
              "TUTORCO_NOTIFY_PER_HOUR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TUTORCO_OWNER_WHATSAPP", NUMBER)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtestsid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok-test")
    monkeypatch.setenv("TWILIO_FROM", "+15559990000")

    async def find_live(kind, ref):
        return next((r for r in b.rows.values() if r["kind"] == kind
                     and r["ref"] == ref and r["status"] in ("sending", "sent")), None)

    async def count():
        return sum(1 for r in b.rows.values() if r["status"] != "failed")

    async def insert(kind, ref, body):
        i = len(b.rows) + 1
        b.rows[i] = {"kind": kind, "ref": ref, "body": body, "status": "sending"}
        return i

    async def set_status(i, status, sid=None):
        b.rows[i]["status"] = status

    async def send(to, body):
        if not b.ok:
            return False, "HTTP 400 from api.twilio.com (code 63016)"
        b.sent.append((to, body))
        return True, "SMfake"

    async def digest(now=None, limit=whatsapp_daily.MAX_CHARS):
        return ["Last 24 hours, as of 8:00 PM ET\n\U0001F7E2 Mark: all good, a quiet day"]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW

    monkeypatch.setattr(notify, "_find_live", find_live)
    monkeypatch.setattr(notify, "_count_last_hour", count)
    monkeypatch.setattr(notify, "_insert_outbox", insert)
    monkeypatch.setattr(notify, "_set_status", set_status)
    monkeypatch.setattr(notify, "send_whatsapp", send)
    monkeypatch.setattr(notify, "datetime", Clock)
    monkeypatch.setattr(whatsapp_daily, "digest", digest)
    return b


def test_the_action_is_tier1_with_a_date_only_and_no_recipient():
    action = next(a for a in MODULE.actions() if a.name == "tutorco.notify_daily")
    assert action.tier is Tier.AUTO and action.dry_run and action.rollback
    assert set(action.params) == {"day"}


async def test_one_send_per_new_york_day(box):
    first = await notify.notify_daily(DAY)
    second = await notify.notify_daily(DAY)
    assert first.ok and second.ok and "already sent" in second.detail
    assert len(box.sent) == 1 and box.sent[0][0] == NUMBER
    assert box.rows[1]["kind"] == "daily_report" and box.rows[1]["ref"] == DAY


async def test_only_todays_new_york_date_is_sent(box):
    assert not (await notify.notify_daily("2026-11-09")).ok
    assert not (await notify.notify_daily("tomorrow")).ok
    assert box.sent == []


async def test_a_failed_send_frees_nothing_it_did_not_send(box):
    box.ok = False
    result = await notify.notify_daily(DAY)
    assert not result.ok and "send failed" in result.detail
    assert [r["status"] for r in box.rows.values()] == ["failed"]


async def _fail_once(box, monkeypatch, reason):
    async def send(to, body):
        box.sent.append("attempt")
        return False, reason

    monkeypatch.setattr(notify, "send_whatsapp", send)
    return await notify.notify_daily(DAY)


async def test_a_timeout_may_have_delivered_so_no_second_send(box, monkeypatch):
    out = await _fail_once(box, monkeypatch, "timed out")
    assert not out.ok and "outcome unknown" in out.detail
    assert [r["status"] for r in box.rows.values()] == ["sending"]   # in doubt
    again = await notify.notify_daily(DAY)
    assert again.ok and "already sent" in again.detail
    assert box.sent == ["attempt"]                                    # never resent


async def test_a_5xx_is_in_doubt_too(box, monkeypatch):
    out = await _fail_once(box, monkeypatch, "HTTP 503 from api.twilio.com (code None)")
    assert "outcome unknown" in out.detail
    assert [r["status"] for r in box.rows.values()] == ["sending"]


async def test_a_refused_connection_certainly_did_not_send_so_it_is_retried(
        box, monkeypatch):
    out = await _fail_once(box, monkeypatch, "unreachable: ConnectError")
    assert not out.ok and "outcome unknown" not in out.detail
    assert [r["status"] for r in box.rows.values()] == ["failed"]
    monkeypatch.undo()          # a working sender again (fixture patches are undone too)


async def test_the_hourly_cap_holds_the_whole_report(box, monkeypatch):
    monkeypatch.setenv("TUTORCO_NOTIFY_PER_HOUR", "0")
    result = await notify.notify_daily(DAY)
    assert not result.ok and "rate limit" in result.detail and box.sent == []


# ------------------------------------------------------------ the job

@pytest.fixture
def job(monkeypatch):
    calls: dict = {"execute": [], "slack": []}

    async def execute(req):
        calls["execute"].append(req)
        return calls["result"]

    async def slack(why, parts):
        calls["slack"].append(why)
        return True

    calls["result"] = ActionResult(True, "told the owner")
    monkeypatch.setattr(whatsapp_daily, "execute", execute)
    monkeypatch.setattr(whatsapp_daily, "_slack_note", slack)
    monkeypatch.setattr(whatsapp_daily, "_ready", lambda: None)
    monkeypatch.setattr(whatsapp_daily, "watch_only", lambda: False)

    async def digest(now=None, limit=1500):
        return ["one message"]

    monkeypatch.setattr(whatsapp_daily, "digest", digest)
    return calls


async def test_the_job_sends_once_through_the_executor_for_the_ny_date(job):
    out = await whatsapp_daily.run(NOW)
    (req,) = job["execute"]
    assert req.action == "tutorco.notify_daily" and req.params == {"day": DAY}
    assert out["sent"] is True and job["slack"] == []


async def test_watch_only_does_not_send_it_logs_would_send(job, monkeypatch, caplog):
    monkeypatch.setattr(whatsapp_daily, "watch_only", lambda: True)
    with caplog.at_level("INFO", logger="atlas.whatsapp_daily"):
        out = await whatsapp_daily.run(NOW)
    assert job["execute"] == [] and job["slack"] == []
    assert out["sent"] is False and out["watch_only"] is True
    assert "would send" in caplog.text


async def test_a_failed_whatsapp_send_falls_back_to_one_slack_note(job):
    job["result"] = ActionResult(False, "send failed: HTTP 400 (code 63016)")
    out = await whatsapp_daily.run(NOW)
    assert out["sent"] is False and out["slack_note"] is True
    assert job["slack"] == ["send failed: HTTP 400 (code 63016)"]


async def test_the_slack_note_says_whatsapp_failed_and_carries_the_report(monkeypatch):
    sent = []

    async def notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    assert await whatsapp_daily._slack_note("HTTP 400", ["Mark: no runs"])
    assert "WhatsApp" in sent[0].title and "failed" in sent[0].title
    assert "Mark: no runs" in sent[0].body


# ------------------------------------------------------------ the switch

def test_it_is_off_by_default_and_the_hour_defaults_to_8pm(monkeypatch):
    monkeypatch.delenv("WHATSAPP_DAILY", raising=False)
    monkeypatch.delenv("WHATSAPP_DAILY_HOUR", raising=False)
    assert whatsapp_daily.enabled() is False and whatsapp_daily.hour() == 20
    monkeypatch.setenv("WHATSAPP_DAILY", "on")
    monkeypatch.setenv("WHATSAPP_DAILY_HOUR", "9")
    assert whatsapp_daily.enabled() is True and whatsapp_daily.hour() == 9
    monkeypatch.setenv("WHATSAPP_DAILY_HOUR", "junk")
    assert whatsapp_daily.hour() == 20


async def test_the_scheduler_step_does_nothing_while_off(monkeypatch):
    monkeypatch.delenv("WHATSAPP_DAILY", raising=False)
    from atlas.kernel import missed

    async def due(*_a, **_k):
        raise AssertionError("the window must not even be looked at while off")

    monkeypatch.setattr(missed, "due", due)
    await scheduler._maybe_whatsapp_daily()


async def _tick(monkeypatch, *, status, clock=0.0):
    """One scheduler tick with the window machinery stubbed; returns the log."""
    import types as _t

    from atlas.kernel import missed, occurrence
    log: list = []
    win = _t.SimpleNamespace(key=DAY, scheduled_for=NOW)

    async def due(*_a, **_k):
        return win

    async def claim(*_a, **_k):
        return True

    async def done(job, key, detail=""):
        log.append("done")

    async def release(job, key):
        log.append("release")

    async def run(now=None):
        log.append("run")
        return status

    monkeypatch.setenv("WHATSAPP_DAILY", "on")
    monkeypatch.setattr(scheduler, "_whatsapp_daily_last_try", clock)
    monkeypatch.setattr(missed, "due", due)
    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "done", done)
    monkeypatch.setattr(occurrence, "release", release)
    monkeypatch.setattr(whatsapp_daily, "run", run)
    await scheduler._maybe_whatsapp_daily()
    return log


async def test_an_unsent_day_is_released_and_retried_a_sent_one_is_done(monkeypatch):
    assert await _tick(monkeypatch, status={"sent": False, "watch_only": True}) == [
        "run", "release"]
    assert await _tick(monkeypatch, status={"sent": True}) == ["run", "done"]


async def test_a_retry_waits_thirty_minutes_after_the_last_try(monkeypatch):
    import time
    assert await _tick(monkeypatch, status={"sent": False},
                       clock=time.monotonic() - 60) == []
    assert await _tick(monkeypatch, status={"sent": False},
                       clock=time.monotonic() - 1801) == ["run", "release"]


async def test_the_slack_note_goes_out_once_per_date_across_retries(job):
    whatsapp_daily._NOTED.clear()
    job["result"] = ActionResult(False, "send failed: HTTP 400")
    await whatsapp_daily.run(NOW)
    await whatsapp_daily.run(NOW + timedelta(minutes=30))
    assert len(job["slack"]) == 1
    whatsapp_daily._NOTED.clear()


async def test_an_in_doubt_failure_closes_the_window_a_certain_one_retries(
        job, monkeypatch):
    real_run = whatsapp_daily.run       # `_tick` stubs it for the scheduler
    job["result"] = ActionResult(False, "send failed on message 1 of 1"
                                        + whatsapp_daily.UNCERTAIN_MARK + ": timed out")
    out = await whatsapp_daily.run(NOW)
    assert out["uncertain"] is True
    assert await _tick(monkeypatch, status=out) == ["run", "done"]
    job["result"] = ActionResult(False, "send failed on message 1 of 1: "
                                        "unreachable: ConnectError")
    whatsapp_daily._NOTED.clear()
    out = await real_run(NOW)
    assert out["uncertain"] is False
    assert await _tick(monkeypatch, status=out) == ["run", "release"]
    whatsapp_daily._NOTED.clear()


def test_the_markers_agree():
    assert whatsapp_daily.UNCERTAIN_MARK == notify.UNCERTAIN_MARK


def test_the_policy_retries_until_midnight():
    p = scheduler.POLICIES["whatsapp_daily"]
    assert p.retry and p.grace is None


def test_the_job_is_a_new_york_daily_window_registered_like_the_others(monkeypatch):
    monkeypatch.delenv("WHATSAPP_DAILY_HOUR", raising=False)
    p = scheduler.POLICIES["whatsapp_daily"]
    assert p.recorded and p.windows is scheduler._whatsapp_daily_windows
    w = scheduler._whatsapp_daily_windows(NOW, p.horizon)
    assert w[-1].key == DAY and w[-1].scheduled_for.hour == 20
    assert str(w[-1].scheduled_for.tzinfo) == "America/New_York"
    assert "whatsapp_daily" in scheduler._DETACHED
    assert "whatsapp_daily" in scheduler.DETACHED_DEADLINE_S
    import inspect
    assert '("whatsapp_daily", _maybe_whatsapp_daily)' in inspect.getsource(
        scheduler.loop)
