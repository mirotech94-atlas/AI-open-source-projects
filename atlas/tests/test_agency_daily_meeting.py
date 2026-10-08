"""The daily Agency meeting (`kernel/meeting.py`): a theme that rotates by
date, ATLAS's follow-ups and agent-to-agent exchanges drawn from the record,
a model's rewording kept only where it is grounded, the scheduler step that
builds, stores and posts it, and the API serving today's stored copy.

The single-turn content (did / issues / improve / next) is proved in
tests/test_agency_meeting.py; this file covers what sits on top of it.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth
from atlas.kernel import agents as agent_module
from atlas.kernel import meeting, occurrence, personas, scheduler
from atlas.kernel.meeting import THEMES, Seat, compose, exchanges, make_turn, theme_for

READ_AT = "2026-09-29T11:00:00+00:00"
NOW = datetime(2026, 9, 29, 11, 0, tzinfo=UTC)


def empty_record() -> dict:
    return {"counts": {}, "latest": {}, "failures": {}, "shifts": {}}


def seat_turns(monkeypatch, record: dict, names: tuple[str, ...], *,
               blocked: dict[str, list[str]] | None = None, theme=THEMES[0]):
    """Turns for roster-shaped agents, readiness pinned by the test."""
    blocked = blocked or {}
    monkeypatch.setattr(agent_module, "readiness",
                        lambda n: (n not in blocked, list(blocked.get(n, []))))
    return [make_turn(n, SimpleNamespace(title=n.title(), brief=""), record,
                      NOW, READ_AT, theme) for n in names]


def name(agent: str) -> str:
    return personas.PERSONAS[agent].name


# ------------------------------------------------------------------ theme

def test_the_theme_rotates_by_date_and_is_deterministic():
    start = date(2026, 9, 29)
    week = [theme_for(start + timedelta(days=i)).key for i in range(len(THEMES))]
    assert len(set(week)) == len(THEMES) == 7
    assert theme_for(start) == theme_for(date(2026, 9, 29))
    assert theme_for(start + timedelta(days=7)) == theme_for(start)
    assert theme_for(start + timedelta(days=1)) != theme_for(start)


def test_the_opening_and_every_question_change_with_the_day(monkeypatch):
    record = empty_record()
    openings, questions = set(), set()
    for i in range(len(THEMES)):
        day = date(2026, 9, 29) + timedelta(days=i)
        turns = seat_turns(monkeypatch, record, ("ops",), theme=theme_for(day))
        m = compose(turns, {}, day, READ_AT)
        openings.add(m["opening"]["text"])
        questions.add(turns[0]["question"])
        assert m["theme"]["key"] == theme_for(day).key
        assert theme_for(day).title.lower() in m["opening"]["text"]
        assert turns[0]["question"].startswith(name("ops"))
    assert len(openings) == len(questions) == 7


def test_each_theme_foregrounds_different_record_facts(monkeypatch):
    at = NOW - timedelta(hours=2)
    record = empty_record()
    record["counts"]["ops"] = {"runs": 3, "self_tests": 0, "shifts": 1, "failed": 1,
                               "total": 3}
    record["latest"]["ops"] = {"at": at, "outcome": "Disk on vps2 is 81%.",
                               "model": "m", "provider": "p"}
    record["failures"]["ops"] = {"at": at, "model": "", "outcome": "Nothing answered."}
    answers = {}
    for t in THEMES:
        answers[t.key] = seat_turns(monkeypatch, record, ("ops",), theme=t)[0]
    assert len({a["answer"]["text"] for a in answers.values()}) == 7
    assert "1 of 3 runs in the last 24 hours failed" in answers["learned"]["answer"]["text"]
    num = answers["number"]["answer"]["text"]
    assert "3 runs" in num and "1 failed" in num and "read at" in num
    assert answers["plan"]["answer"]["text"].startswith("Next shift")
    # Every turn keeps its four items whatever the theme.
    for a in answers.values():
        assert a["did"]["runs_24h"] == 3 and a["issues"]["failed_24h"] == 1
        assert a["speech"].startswith(name("ops"))


# ------------------------------------------------------------------ exchanges

def _threads(xs):
    out: dict[int, list] = {}
    for x in xs:
        out.setdefault(x["thread"], []).append(x)
    return out


def test_atlas_follows_up_a_failed_run_and_the_agent_answers_from_the_record(
        monkeypatch):
    at = NOW - timedelta(minutes=30)
    record = empty_record()
    record["counts"]["analyst"] = {"runs": 2, "self_tests": 0, "shifts": 0,
                                   "failed": 1, "total": 2}
    record["failures"]["analyst"] = {"at": at, "model": "", "outcome": "Down."}
    turns = seat_turns(monkeypatch, record, ("ops", "analyst"))
    xs = exchanges(turns, {}, THEMES[0], READ_AT)
    q, a = _threads(xs)[1]
    assert q["kind"] == "follow_up" and q["speaker"] == "atlas" and q["to"] == "analyst"
    assert q["text"] == f"{name('analyst')}, your last run failed: no model answered. What's next?"
    assert a["speaker"] == "analyst" and a["reply_to"] == q["id"]
    analyst = turns[1]
    assert analyst["improve"]["text"] in a["text"] and analyst["next"]["text"] in a["text"]
    assert a["speaker_name"] == name("analyst") and a["voice"] == analyst["voice"]
    assert q["voice"] == "atlas" and q["read_at"] == READ_AT
    # ops is fine: nobody follows up with it.
    assert all("ops" not in (x["speaker"], x["to"]) for x in xs)


def test_atlas_follows_up_a_blocked_agent(monkeypatch):
    turns = seat_turns(monkeypatch, empty_record(), ("fiverr",),
                       blocked={"fiverr": ["browser disabled — missing env: X"]})
    q, a = _threads(exchanges(turns, {}, THEMES[0], READ_AT))[1]
    assert "you are blocked: browser disabled — missing env: X." in q["text"]
    assert a["text"].startswith("No shift until it is ready.")
    assert "Clear the blocker: browser disabled" in a["text"]


def test_an_agent_blocked_on_a_module_asks_its_owner_who_answers_from_its_run(
        monkeypatch):
    at = NOW - timedelta(hours=1)
    record = empty_record()
    record["counts"]["fiverr"] = {"runs": 1, "self_tests": 0, "shifts": 1,
                                  "failed": 0, "total": 1}
    record["latest"]["fiverr"] = {"at": at, "outcome": "Two orders are open. Rest.",
                                  "model": "m", "provider": "p"}
    reason = "fiverr disabled — missing env: FIVERR_X (fiverr_orders)"
    turns = seat_turns(monkeypatch, record, ("commerce", "fiverr"),
                       blocked={"commerce": [reason]})
    seats = {"commerce": Seat("commerce", frozenset({"fiverr"}), frozenset({"fiverr"})),
             "fiverr": Seat("fiverr", frozenset({"fiverr"}))}
    xs = exchanges(turns, seats, THEMES[0], READ_AT)
    dep = [t for t in _threads(xs).values() if t[0]["kind"] == "dependency"]
    # ATLAS's follow-up to commerce comes first, then commerce asks fiverr --
    # one thread per pair, so ATLAS-commerce and commerce-fiverr are both kept.
    assert xs[0]["kind"] == "follow_up"
    q, a = dep[0]
    assert (q["speaker"], q["to"]) == ("commerce", "fiverr")
    assert q["text"].startswith(f"{name('fiverr')}, I am blocked on fiverr: {reason}")
    assert a["speaker"] == "fiverr" and a["reply_to"] == q["id"]
    assert a["text"] == (f"My last run, at {at.astimezone():%H:%M}, said: "
                         "Two orders are open.")


def test_an_owner_with_no_run_says_so_rather_than_inventing_one(monkeypatch):
    turns = seat_turns(monkeypatch, empty_record(), ("commerce", "fiverr"))
    seats = {"commerce": Seat("commerce", frozenset({"fiverr"})),
             "fiverr": Seat("fiverr", frozenset({"fiverr"}))}
    q, a = _threads(exchanges(turns, seats, THEMES[0], READ_AT))[1]
    assert q["text"] == f"{name('fiverr')}, I use the fiverr module too. How did your last run go?"
    assert a["text"] == "I have no run on record, so I cannot say."


def test_two_agents_sharing_an_unowned_module_talk_later_asks_earlier(monkeypatch):
    turns = seat_turns(monkeypatch, empty_record(), ("growth", "marketing"))
    seats = {"growth": Seat("growth", frozenset({"instagram"})),
             "marketing": Seat("marketing", frozenset({"instagram"}))}
    q, _a = _threads(exchanges(turns, seats, THEMES[0], READ_AT))[1]
    assert q["kind"] == "shared"
    assert (q["speaker"], q["to"]) == ("marketing", "growth")
    assert "we both use instagram" in q["text"]


def test_exchanges_are_bounded_and_each_pair_talks_once(monkeypatch):
    names = tuple(personas.PERSONAS)            # 19 agents
    record = empty_record()
    for n in names:
        record["counts"][n] = {"runs": 1, "self_tests": 0, "shifts": 0,
                               "failed": 1, "total": 1}
        record["failures"][n] = {"at": NOW, "model": "", "outcome": "x"}
    turns = seat_turns(monkeypatch, record, names)
    seats = {n: Seat(n, frozenset({"shared"})) for n in names}
    xs = exchanges(turns, seats, THEMES[0], READ_AT)
    threads = _threads(xs)
    assert len(threads) == meeting.MAX_EXCHANGES == 8
    # A module every agent shares is one conversation, not eighteen -- and
    # it still gets a place though nineteen follow-ups could fill the room.
    kinds = [t[0]["kind"] for t in threads.values()]
    assert kinds.count("shared") == 1 and kinds.count("follow_up") == 7
    pairs = [frozenset({t[0]["speaker"], t[0]["to"]}) for t in threads.values()]
    assert len(set(pairs)) == len(pairs)
    for t in threads.values():
        assert [x["reply_to"] for x in t] == [None, t[0]["id"]]


def test_on_help_day_the_agents_talk_to_each_other_first(monkeypatch):
    help_theme = next(t for t in THEMES if t.key == "help")
    turns = seat_turns(monkeypatch, empty_record(), ("fiverr", "commerce"),
                       blocked={"fiverr": ["x disabled — y"]}, theme=help_theme)
    seats = {"commerce": Seat("commerce", frozenset({"fiverr"})),
             "fiverr": Seat("fiverr", frozenset({"fiverr"}))}
    xs = exchanges(turns, seats, help_theme, READ_AT)
    assert xs[0]["kind"] == "dependency"
    assert xs[-2]["kind"] == "follow_up"


# ------------------------------------------------------------------ polish

def _meeting(monkeypatch, *, names=("ops", "inbox"), private=("inbox",)):
    at = NOW - timedelta(hours=2)
    record = empty_record()
    record["counts"]["ops"] = {"runs": 3, "self_tests": 0, "shifts": 1, "failed": 1,
                               "total": 3}
    record["latest"]["ops"] = {"at": at, "outcome": "All four servers answered.",
                               "model": "m", "provider": "p"}
    record["failures"]["ops"] = {"at": at, "model": "", "outcome": "x"}
    record["latest"]["inbox"] = {"at": at, "outcome": "Mail from the landlord.",
                                 "model": "m", "provider": "p"}
    turns = seat_turns(monkeypatch, record, names, theme=THEMES[0])
    seats = {n: Seat(n, private=n in private) for n in names}
    return compose(turns, seats, date(2026, 9, 29), READ_AT), seats


def _fake_think(monkeypatch, reply=None, error: Exception | None = None):
    from atlas.kernel import llm
    seen: list[str] = []

    async def think(need, system, messages, tools=None, max_tokens=2048, *, model=None):
        seen.append(messages[0].content)
        assert need is llm.Need.DRAFT
        if error:
            raise error
        return llm.Completion(text=reply(json.loads(_inner(messages[0].content))),
                              provider="p", model="m")

    monkeypatch.setattr(llm, "think", think)
    return seen


def _inner(wrapped: str) -> str:
    return wrapped.split(">\n", 1)[1].rsplit("\n</untrusted_data>", 1)[0]


async def test_a_grounded_rewording_is_kept_and_marked_polished(monkeypatch):
    m, seats = _meeting(monkeypatch)
    ops = m["turns"][0]
    plain = ops["answer"]["plain"]
    good = plain.replace("In my way:", "What's in my way:")

    def reply(lines):
        return json.dumps([{"id": "turn:ops", "text": good}])

    seen = _fake_think(monkeypatch, reply)
    out = await meeting.polish_meeting(m, seats)
    ops = out["turns"][0]
    assert ops["answer"] == {"text": good, "plain": plain, "polished": True}
    assert ops["speech"].endswith(good)
    assert out["polished"] == 1
    assert out["opening"]["polished"] is False           # the model left it out
    # Wrapped as untrusted data (invariant 4), and the private agent's
    # record never left the box.
    assert seen[0].startswith("<untrusted_data source='agency.meeting'>")
    assert "landlord" not in seen[0] and "turn:inbox" not in seen[0]


async def test_a_rewording_with_an_invented_number_is_thrown_away(monkeypatch):
    m, seats = _meeting(monkeypatch)
    plain = m["turns"][0]["answer"]["plain"]
    bad = plain.replace("3 runs", "7 runs")
    _fake_think(monkeypatch, lambda lines: json.dumps([{"id": "turn:ops", "text": bad}]))
    out = await meeting.polish_meeting(m, seats)
    assert out["turns"][0]["answer"] == {"text": plain, "plain": plain, "polished": False}
    assert out["polished"] == 0 and out["polish_rejected"] == 1


@pytest.mark.parametrize("error", [RuntimeError("provider down"), TimeoutError()])
async def test_a_model_failure_is_the_deterministic_meeting_never_an_error(
        monkeypatch, error):
    m, seats = _meeting(monkeypatch)
    before = json.dumps(m, sort_keys=True)
    _fake_think(monkeypatch, error=error)
    out = await meeting.polish_meeting(m, seats)
    assert out["polished"] == 0
    assert "record's own words" in out["polish_note"]
    after = json.loads(json.dumps(out, sort_keys=True))
    after.pop("polish_note")
    assert json.dumps(after, sort_keys=True) == before


async def test_a_malformed_model_answer_keeps_every_line(monkeypatch):
    m, seats = _meeting(monkeypatch)
    _fake_think(monkeypatch, lambda lines: "Sure! Here you go: nothing useful")
    out = await meeting.polish_meeting(m, seats)
    assert out["polished"] == 0
    assert all(not t["answer"]["polished"] for t in out["turns"])


async def test_every_polishable_line_is_offered_with_its_id(monkeypatch):
    m, seats = _meeting(monkeypatch)
    offered: list[str] = []

    def reply(lines):
        offered.extend(line["id"] for line in lines)
        return "[]"

    _fake_think(monkeypatch, reply)
    await meeting.polish_meeting(m, seats)
    assert offered[0] == "opening" and "turn:ops" in offered
    assert "turn:inbox" not in offered
    assert all(i == "opening" or i.startswith(("turn:", "x:")) for i in offered)


# ------------------------------------------------------------------ Slack summary

def test_the_slack_summary_has_read_times_and_no_number_of_its_own(monkeypatch):
    m, _ = _meeting(monkeypatch, names=("ops", "fiverr", "commerce"), private=())
    m["built_at"] = "2026-09-29T11:02:00+00:00"
    text = meeting.slack_summary(m)
    read = datetime.fromisoformat(READ_AT).astimezone().strftime("%H:%M")
    built = datetime.fromisoformat(m["built_at"]).astimezone().strftime("%H:%M")
    assert f"Records read {read}, meeting built {built}." in text
    assert f"Theme: {m['theme']['title']}." in text
    assert f"Worked (1 of 3): {name('ops')}." in text
    assert "Top exchanges:" in text
    # Every digit run in the summary is one the meeting itself states.
    source = json.dumps(m) + read + built
    for run in re.findall(r"\d+(?::\d+)?", text):
        assert run in source, run
    # At most three threads.
    assert text.count("•") <= 2 * meeting.TOP_EXCHANGES


def test_a_blocked_agent_is_named_with_its_reason_in_the_summary(monkeypatch):
    turns = seat_turns(monkeypatch, empty_record(), ("fiverr",),
                       blocked={"fiverr": ["browser disabled — missing env: X"]})
    m = compose(turns, {}, date(2026, 9, 29), READ_AT)
    text = meeting.slack_summary(m)
    assert f"Blocked (1): {name('fiverr')}." in text
    assert f"  {name('fiverr')} — browser disabled — missing env: X." in text


def test_a_bad_day_is_still_a_short_summary(monkeypatch):
    names = tuple(personas.PERSONAS)
    turns = seat_turns(monkeypatch, empty_record(), names,
                       blocked={n: [f"{n} disabled — missing env"] for n in names})
    text = meeting.slack_summary(compose(turns, {}, date(2026, 9, 29), READ_AT))
    # Every blocked agent named, the reason for only the first three.
    assert all(name(n) in text for n in names)
    reasons = [ln for ln in text.splitlines() if ln.startswith("  ")]
    assert len(reasons) == meeting.BLOCKED_REASONS == 3
    assert all("disabled — missing env" in ln for ln in reasons)


# ------------------------------------------------------------------ storage + step

async def test_store_writes_one_row_per_local_date(memory_backend, monkeypatch):
    m, _ = _meeting(monkeypatch)
    built = datetime(2026, 9, 29, 11, 2, tzinfo=UTC)
    await meeting.store(m, built)
    sql, args = memory_backend.sql_log[-1]
    assert "INSERT INTO agency_meetings" in sql and "ON CONFLICT (day)" in sql
    assert args[0] == date(2026, 9, 29) and args[1] == m["theme"]["key"]
    assert json.loads(args[2])["turns"][0]["agent"] == "ops"
    assert args[3] == built


async def test_stored_for_returns_the_payload_with_its_build_time(memory_backend,
                                                                  monkeypatch):
    m, _ = _meeting(monkeypatch)
    built = datetime(2026, 9, 29, 11, 2, tzinfo=UTC)
    memory_backend.responses["FROM agency_meetings"] = {
        "day": date(2026, 9, 29), "theme": m["theme"]["key"],
        "payload": json.dumps(m), "built_at": built}
    out = await meeting.stored_for(date(2026, 9, 29))
    assert out["source"] == "stored" and out["built_at"] == built.isoformat()
    assert out["turns"] == json.loads(json.dumps(m))["turns"]
    memory_backend.responses["FROM agency_meetings"] = None
    assert await meeting.stored_for(date(2026, 9, 29)) is None


async def test_run_daily_builds_stores_then_posts(memory_backend, monkeypatch):
    order: list[str] = []
    m, _ = _meeting(monkeypatch, private=())

    async def build(*, polish=False, now=None):
        assert polish is True
        order.append("build")
        return m

    async def store(meeting_, built_at):
        order.append("store")

    posted = []

    async def notify(msg):
        order.append("notify")
        posted.append(msg)
        return ["slack"]

    monkeypatch.setattr(meeting, "build", build)
    monkeypatch.setattr(meeting, "store", store)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    out = await meeting.run_daily()
    assert order == ["build", "store", "notify"]
    assert out["source"] == "stored" and out["built_at"]
    assert posted[0].title == "Agency meeting — Tue 29 Sep"
    assert posted[0].topic == "brief" and posted[0].kind == "digest"
    assert "Records read" in posted[0].body


async def test_a_failed_store_posts_nothing(memory_backend, monkeypatch):
    m, _ = _meeting(monkeypatch)

    async def build(*, polish=False, now=None):
        return m

    async def store(meeting_, built_at):
        raise ConnectionError("postgres is not answering")

    posted = []

    async def notify(msg):
        posted.append(msg)
        return ["slack"]

    monkeypatch.setattr(meeting, "build", build)
    monkeypatch.setattr(meeting, "store", store)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    with pytest.raises(ConnectionError):
        await meeting.run_daily()
    assert posted == []


def test_the_step_declares_skip_stale_today_only():
    from atlas.kernel.missed import Missed
    p = scheduler.POLICIES["agency_meeting"]
    assert p.missed is Missed.SKIP_STALE and p.retry and p.recorded
    assert p.grace is None                       # until local midnight: today only
    now = datetime(2026, 9, 29, 10, tzinfo=UTC)
    assert p.windows(now, timedelta(days=0))[-1].key == "2026-09-29"
    assert "agency_meeting" in scheduler._DETACHED


@pytest.fixture
def step(monkeypatch):
    """The step with its window and claim faked, like test_missed_runs."""
    calls: list[tuple] = []
    now = datetime(2026, 9, 29, 9, tzinfo=UTC)
    monkeypatch.setattr(scheduler, "_now", lambda: now)
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    monkeypatch.setattr(scheduler, "_last_meeting_date", None)

    async def due(policy, at):
        from atlas.kernel.missed import Window
        return Window(at.date().isoformat(), at)

    async def claim(job, window, scheduled_for=None):
        calls.append(("claim", job, window))
        return True

    async def done(job, window):
        calls.append(("done", job, window))

    async def release(job, window):
        calls.append(("release", job, window))

    monkeypatch.setattr("atlas.kernel.missed.due", due)
    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "done", done)
    monkeypatch.setattr(occurrence, "release", release)
    return calls


async def test_the_step_claims_runs_and_marks_the_day_done(step, monkeypatch):
    ran = []

    async def run_daily():
        ran.append(1)
        return {}

    monkeypatch.setattr(meeting, "run_daily", run_daily)
    await scheduler._maybe_agency_meeting()
    assert ran == [1]
    assert step == [("claim", "agency_meeting", "2026-09-29"),
                    ("done", "agency_meeting", "2026-09-29")]
    # Once a day: the next tick does nothing.
    await scheduler._maybe_agency_meeting()
    assert ran == [1]


async def test_a_failed_step_releases_the_day_for_a_retry(step, monkeypatch):
    async def run_daily():
        raise ConnectionError("postgres is not answering")

    monkeypatch.setattr(meeting, "run_daily", run_daily)
    await scheduler._maybe_agency_meeting()
    assert step[-1] == ("release", "agency_meeting", "2026-09-29")
    assert scheduler._last_meeting_date is None


async def test_the_step_waits_for_the_brief_hour(step, monkeypatch):
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 10)
    await scheduler._maybe_agency_meeting()
    assert step == []


# ------------------------------------------------------------------ the API

SECRET = "s" * 40
PASSWORD = "a-long-enough-password"


@pytest.fixture
def signed_in(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    agent_module.build_roster()
    monkeypatch.setattr(agent_module, "readiness", lambda n: (True, []))

    async def fetch(sql, *args):
        return []                     # no runs: every agent idle

    memory_backend.fetch = fetch
    with TestClient(app, client=("127.0.0.1", 51008)) as client:
        assert client.post("/api/login", json={"password": PASSWORD}).status_code == 200
        yield client


def test_the_api_serves_today_s_stored_meeting(signed_in, memory_backend):
    built = datetime(2026, 9, 29, 7, 1, tzinfo=UTC)
    stored = {"day": "2026-09-29", "theme": {"key": "risk"}, "turns": [],
              "exchanges": [], "read_at": "2026-09-29T07:00:00+00:00"}
    memory_backend.responses["FROM agency_meetings"] = {
        "day": date(2026, 9, 29), "theme": "risk", "payload": json.dumps(stored),
        "built_at": built}
    body = signed_in.get("/api/agency/meeting").json()
    assert body["source"] == "stored" and body["built_at"] == built.isoformat()
    assert body["theme"] == {"key": "risk"} and body["served_at"]
    today = datetime.now().astimezone().date()
    reads = [a for s, a in memory_backend.sql_log if "FROM agency_meetings" in s]
    assert reads and reads[-1] == (today,)


def test_live_rebuilds_from_the_records_and_never_reads_the_stored_copy(
        signed_in, memory_backend):
    memory_backend.responses["FROM agency_meetings"] = {
        "day": date(2026, 9, 29), "theme": "risk", "payload": "{}",
        "built_at": datetime.now(UTC)}
    body = signed_in.get("/api/agency/meeting?live=1").json()
    assert body["source"] == "live"
    assert body["polished"] == 0
    assert body["theme"]["key"] == theme_for(datetime.now().astimezone().date()).key
    assert {t["agent"] for t in body["turns"]} == set(agent_module.ROSTER)
    assert "exchanges" in body and body["built_at"] == body["read_at"]
    assert not any("FROM agency_meetings" in s for s, _ in memory_backend.sql_log)


def test_no_stored_meeting_yet_is_a_live_build(signed_in, memory_backend):
    memory_backend.responses["FROM agency_meetings"] = None
    body = signed_in.get("/api/agency/meeting").json()
    assert body["source"] == "live" and body["turns"]


def test_an_unreadable_store_is_a_503_not_a_guess(signed_in, memory_backend):
    # No canned answer: the memory backend refuses the read, like a
    # database that is not answering.
    memory_backend.responses.pop("FROM agency_meetings", None)
    response = signed_in.get("/api/agency/meeting")
    assert response.status_code == 503
    assert "stored meeting" in response.json()["detail"]


async def test_a_private_agent_stays_off_the_model_even_without_seats(monkeypatch):
    """The roster's own Need.PRIVATE is read too: a failed seat read (no
    seats at all) still never sends inbox's record to a model."""
    agent_module.build_roster()
    assert agent_module.ROSTER["inbox"].need.value == "private"
    m, _ = _meeting(monkeypatch, private=())
    offered: list[str] = []

    def reply(lines):
        offered.extend(line["id"] for line in lines)
        return "[]"

    seen = _fake_think(monkeypatch, reply)
    await meeting.polish_meeting(m, {})
    assert "turn:inbox" not in offered and "landlord" not in seen[0]


def test_an_agent_blocked_on_a_shared_module_says_so_when_it_asks(monkeypatch):
    reason = "calendar disabled — missing env: GOOGLE_X (calendar_free_slots)"
    turns = seat_turns(monkeypatch, empty_record(), ("inbox", "scheduler"),
                       blocked={"scheduler": [reason]})
    seats = {"inbox": Seat("inbox", frozenset({"calendar", "mail"})),
             "scheduler": Seat("scheduler", frozenset({"calendar"}),
                               frozenset({"calendar"}))}
    shared = [t for t in _threads(exchanges(turns, seats, THEMES[0], READ_AT)).values()
              if t[0]["kind"] == "shared"]
    q, a = shared[0]
    assert (q["speaker"], q["to"]) == ("scheduler", "inbox")
    assert q["text"] == (f"{name('inbox')}, I am blocked on calendar: {reason}. "
                         "You use it too. What does your side show?")
    assert a["text"] == "I have no run on record, so I cannot say."


def test_private_agents_reach_slack_by_name_and_status_only(monkeypatch):
    """inbox and school (Need.PRIVATE): on the Agency page in full, but in
    Slack only as a name under worked or blocked -- no reason, no outcome,
    no thread they are in."""
    from atlas.kernel.llm import Need

    at = NOW - timedelta(hours=1)
    record = empty_record()
    record["counts"]["school"] = {"runs": 2, "self_tests": 0, "shifts": 1,
                                  "failed": 0, "total": 2}
    record["latest"]["school"] = {"at": at, "outcome": "Essay due Friday in ENG 201.",
                                  "model": "m", "provider": "p"}
    record["counts"]["ops"] = {"runs": 1, "self_tests": 0, "shifts": 0,
                               "failed": 1, "total": 1}
    record["failures"]["ops"] = {"at": at, "model": "", "outcome": "x"}
    inbox_reason = "calendar disabled — missing env: GOOGLE_X (landlord thread)"
    monkeypatch.setattr(agent_module, "readiness",
                        lambda n: (n != "inbox", [inbox_reason] if n == "inbox" else []))
    needs = {"inbox": Need.PRIVATE, "school": Need.PRIVATE, "ops": Need.REASON}
    turns = [make_turn(n, SimpleNamespace(title=n.title(), brief="", need=needs[n]),
                       record, NOW, READ_AT, THEMES[0])
             for n in ("ops", "inbox", "school")]
    assert [t["private"] for t in turns] == [False, True, True]
    seats = {"ops": Seat("ops", frozenset({"calendar"})),
             "inbox": Seat("inbox", frozenset({"calendar"}), frozenset({"calendar"}),
                           private=True),
             "school": Seat("school", frozenset({"school"}), private=True)}
    m = compose(turns, seats, date(2026, 9, 29), READ_AT)
    m["built_at"] = READ_AT
    # The page keeps everything: inbox is followed up and asks ops.
    page = json.dumps(m)
    assert "landlord" in page and "ENG 201" in page
    assert any(x["speaker"] == "inbox" or x["to"] == "inbox" for x in m["exchanges"])

    text = meeting.slack_summary(m)
    assert f"Worked (2 of 3): {name('ops')}, {name('school')}." in text
    assert f"Blocked (1): {name('inbox')}." in text
    for leaked in ("landlord", "ENG 201", "Essay", "GOOGLE_X", "calendar disabled"):
        assert leaked not in text, leaked
    # No line of a thread a private agent is in: only ops's own follow-up.
    bullets = [ln for ln in text.splitlines() if ln.startswith("•")]
    assert bullets and all(name("inbox") not in ln and name("school") not in ln
                           for ln in bullets)
    # The roster's own Need.PRIVATE counts even for a stored meeting whose
    # turns carry no flag.
    for t in m["turns"]:
        t.pop("private")
    agent_module.build_roster()
    assert "landlord" not in meeting.slack_summary(m)
