"""Domain and interaction acceptance tests, the rest of D01-D16 (plan p. 15):
D04, D06, D11, D12, D13, D14, D15 and D16.

Each test is named by its plan ID; docs/acceptance.md is the matrix. The
in-suite ones need no database; the ones marked `needs_pg` need a throwaway
Postgres (ATLAS_TEST_PG_DSN) and are skipped without it.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import approvals, executor, outbox, state
from atlas.kernel.contracts import Health

# Fixtures. pytest registers them by their own names; the aliases keep
# the module names apart from the test parameters that request them.
from tests.acceptance_support import (
    APPROVER,
    CARD,
    StubWorld,
    install_stub,
    media_params,
    needs_pg,
    outbox_rows,
    pg_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
    slack_fixture,  # noqa: F401
    slack_message,
)
from tests.test_calendar_zone import FALL, NY, SPRING, _existing, _iso
from tests.test_calendar_zone import google as google  # noqa: PLC0414

# -------------------------------------------------------------------- D06
# Stale telemetry or an offline personal endpoint: no fabricated current
# health, and no global-outage claim.

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def test_D06_a_stale_ok_is_said_as_unknown_and_its_number_is_dropped():
    from atlas.api import voice
    row = {"module": "cloudhost", "check": "instances", "health": "ok",
           "detail": "12 of 12 instances running", "interval_s": 300,
           "at": NOW - timedelta(hours=3)}
    line = voice._live_line(row, NOW)
    assert line.startswith("cloudhost.instances: unknown")
    assert "stale" in line and "not known now" in line
    assert "12 of 12" not in line, "a stale number must not read as today's"
    never = voice._live_line({**row, "at": None}, NOW)
    assert "unknown — never measured" in never


async def test_D06_the_agents_status_tool_never_reports_a_stale_ok(monkeypatch):
    from atlas.tools import builtin
    now = datetime.now(UTC)
    rows = [
        {"check": "instances", "health": "ok", "value": 12.0, "unit": "vms",
         "detail": "12 running", "observed_at": now - timedelta(hours=3),
         "interval_s": 300},
        {"check": "balance", "health": None, "value": None, "unit": None,
         "detail": None, "observed_at": None, "interval_s": 3600},
        {"check": "fresh", "health": "ok", "value": 1.0, "unit": "x",
         "detail": "just now", "observed_at": now - timedelta(minutes=1),
         "interval_s": 300},
    ]

    async def fetch_all(_sql, *_a):
        return rows

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    got = await builtin.module_status("cloudhost")
    health = {c["check"]: c["health"] for c in got["checks"]}
    assert health == {"instances": "unknown", "balance": "unknown", "fresh": "ok"}


async def test_D06_an_offline_mac_is_not_an_outage(memory_backend):
    from atlas.modules import mac
    memory_backend.responses["SELECT max(last_seen) AS at FROM mac_agent"] = {
        "at": datetime.now(UTC) - timedelta(hours=2)}
    obs = await mac.check_agent()
    assert obs.health is Health.UNKNOWN          # not OK, and not FAILING
    assert "asleep or offline" in obs.detail
    assert "outage" not in obs.detail.lower() and "down" not in obs.detail.lower()


async def test_D06_an_optional_personal_peer_off_the_mesh_is_not_degraded(monkeypatch):
    from atlas.modules import netbird

    async def peers(_path):
        return [{"id": "1", "name": "atlas", "connected": True},
                {"id": "2", "name": "miro-iphone", "connected": False}], None

    monkeypatch.setattr(netbird, "_get", peers)
    monkeypatch.setenv("NETBIRD_OPTIONAL_PEERS", "miro-iphone")
    obs = await netbird.check_peers()
    assert obs.health is Health.OK
    assert "optional and off: miro-iphone" in obs.detail
    # The same phone, not declared optional, is one peer offline -- degraded,
    # never a claim that the mesh is down.
    monkeypatch.setenv("NETBIRD_OPTIONAL_PEERS", "")
    obs = await netbird.check_peers()
    assert obs.health is Health.DEGRADED
    assert obs.detail.startswith("1 of 2 peers offline")


# -------------------------------------------------------------------- D11

async def test_D11_a_booking_on_a_dst_day_uses_the_calendars_agreed_zone(google):
    from atlas.modules.calendar import booking
    done = await booking.book_with("ana@example.com", "Call", _iso(FALL, 1, 30), 30)
    assert done.ok
    [(_method, _path, body)] = google.writes
    assert body["start"]["timeZone"] == NY and body["end"]["timeZone"] == NY
    # 01:30 happens twice; the first (daylight) one, and 30 real minutes.
    assert body["start"]["dateTime"] == f"{FALL.isoformat()}T01:30:00-04:00"
    assert body["end"]["dateTime"] == f"{FALL.isoformat()}T01:00:00-05:00"
    assert "happens twice" in done.evidence["occurrence"]


async def test_D11_a_time_that_does_not_exist_is_refused_not_moved(google):
    from atlas.modules import calendar
    done = await calendar.create_block("Focus", _iso(SPRING, 2, 30), 60)
    assert not done.ok and "does not exist" in done.detail
    assert google.writes == []


async def test_D11_the_same_request_twice_is_one_event(google):
    from atlas.modules.calendar import booking
    first = await booking.book_with("ana@example.com", "Call",
                                    "2026-10-01T09:00:00", 30)
    assert first.ok and len(google.writes) == 1
    # What the first write made is now in the calendar; the retry finds it.
    google.on(r"/calendars/primary/events$", body={"timeZone": NY, "items": [
        _existing("2026-10-01T13:00:00Z", "2026-10-01T13:30:00Z",
                  attendees=["ana@example.com"])]})
    again = await booking.book_with("ana@example.com", "Call",
                                    "2026-10-01T09:00:00", 30)
    assert again.ok and again.evidence["duplicate"] is True
    assert again.rollback_token is None
    assert len(google.writes) == 1


# -------------------------------------------------------------------- D14

def _cloudhost(balance, listed, actual):
    async def fake(path, **_params):
        data = {"current_balance": balance}
        if listed is not None:
            data["monthly_charge"] = listed
        if actual is not None:
            data["actual_monthly_charge"] = actual
        return {"data": {"balanceData": data}}
    return fake











async def test_D14_revenue_in_two_currencies_is_not_summed_into_one(monkeypatch):
    from atlas.modules import stripe_billing

    def sub(amount, currency):
        return {"items": {"data": [{"quantity": 1, "price": {
            "unit_amount": amount, "currency": currency,
            "recurring": {"interval": "month"}}}]}}

    async def get(business, path, **_params):
        assert path == "subscriptions"
        return {"data": [sub(1000, "usd"), sub(900, "eur")]}

    monkeypatch.setattr(stripe_billing, "_get", get)
    obs = await stripe_billing.mrr("guardian")
    assert obs.health is Health.UNKNOWN and obs.value is None
    assert "eur, usd" in obs.detail and "not summed" in obs.detail

    async def one(business, path, **_params):
        return {"data": [sub(1000, "eur"), sub(500, "eur")]}

    monkeypatch.setattr(stripe_billing, "_get", one)
    obs = await stripe_billing.mrr("guardian")
    assert obs.health is Health.OK and obs.value == 15.0 and obs.unit == "EUR"


# -------------------------------------------------------------------- D15

async def test_D15_an_unsupported_spoken_task_is_an_honest_limit_and_no_card(
        monkeypatch):
    from atlas.api import voice

    async def pick(step, *, principal=False):
        return ("stubd15.balance", {}) if "balance" in step else None

    proposed: list = []

    async def propose(name, params, **_kw):
        proposed.append(name)
        return 5

    monkeypatch.setattr(voice, "_pick_action", pick)
    monkeypatch.setattr(approvals, "propose", propose)
    pids, said = await voice._queue_steps("book me a flight to the moon",
                                          source="voice", principal=False)
    assert pids == [] and proposed == []
    assert said == voice.NO_ACTION           # "No action for that ... By hand:"
    pids, said = await voice._queue_steps(
        "check the cloudhost balance and book me a flight to the moon", source="voice",
        principal=False)
    assert pids == [5] and proposed == ["stubd15.balance"]
    assert "“book me a flight to the moon”: no action for that." in said
    assert "is queued as #5" in said


async def test_D15_a_typed_unsupported_task_offers_the_smallest_next_step(
        monkeypatch, memory_backend):
    from atlas.kernel import intent

    async def nothing(_request, **_kw):
        return None

    monkeypatch.setattr(intent, "choose", nothing)
    out = await intent.act("buy me a helicopter", channel="slack", who=APPROVER)
    assert not out.ok and out.action is None and out.proposal_id is None
    assert out.spoken == intent.NO_ACTION
    assert "I don't have an action that does that" in out.spoken
    assert "file it as a GitLab issue" in out.spoken     # one small next step


async def test_D15_improve_yourself_is_a_handoff_never_a_self_edit(
        monkeypatch, memory_backend):
    from atlas.kernel import improve
    sent: list = []

    async def notify(msg):
        sent.append(msg)
        return ["slack"]

    async def no_execute(*_a, **_kw):
        raise AssertionError("an improve request must never execute")

    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    monkeypatch.setattr(executor, "execute", no_execute)
    got = await improve.handle("improve your voice", source="slack")
    assert got is not None and got.target == "atlas"
    assert "access" not in got.spoken.lower()
    assert len(sent) == 1 and "DEVELOPMENT TASK" in sent[0].body
    assert [r.event for r in memory_backend.audit_rows] == ["improve.noted"]


# -------------------------------------------------------------------- D16

class Drafts:
    """content_drafts in memory: the count the cap reads, the rows made."""

    def __init__(self, waiting: int) -> None:
        self.waiting = waiting
        self.made: list[str] = []

    async def fetch_one(self, sql, *args):
        flat = " ".join(sql.split())
        if "count(*) AS n FROM content_drafts" in flat:
            return {"n": self.waiting}
        if flat.startswith("INSERT INTO content_drafts"):
            self.waiting += 1
            self.made.append(args[1])
            return {"id": len(self.made)}
        raise AssertionError(f"unexpected SQL: {flat[:80]}")


@pytest.fixture
def drafts_world(monkeypatch):
    from atlas.kernel.llm import Completion
    from atlas.modules.content import drafts
    monkeypatch.setenv("DRAFTS_BACKLOG_CAP", "3")
    thought: list = []

    async def gather(source):
        return f"<material from {source}>", None

    async def think(*_a, **_kw):
        thought.append(1)
        return Completion(text="A hook.\nA beat.\nA close.", provider="stub",
                          model="stub")

    world = Drafts(waiting=0)
    monkeypatch.setattr(drafts, "gather", gather)
    monkeypatch.setattr(drafts, "think", think)
    monkeypatch.setattr(drafts, "fetch_one", world.fetch_one)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", world.fetch_one)
    world.thought = thought
    return world


async def test_D16_at_the_cap_no_more_drafts_until_capacity_returns(drafts_world):
    from atlas.modules.content import drafts
    for n in range(3):
        assert (await drafts.draft("short_script", f"weekly:{n}")).ok
    stopped = await drafts.draft("short_script", "weekly:3")
    assert not stopped.ok and "cap is 3" in stopped.detail
    assert len(drafts_world.thought) == 3, "no model call is spent past the cap"
    dry = await drafts.draft_dry("short_script", "weekly:3")
    assert not dry.ok and "cap is 3" in dry.detail
    # One is approved or dropped: capacity returns, and so do drafts.
    drafts_world.waiting -= 1
    assert (await drafts.draft("short_script", "weekly:4")).ok
    assert drafts_world.made == ["weekly:0", "weekly:1", "weekly:2", "weekly:4"]


# -------------------------------------------------------------------- D04

@needs_pg
async def test_D04_one_action_id_from_any_channel_is_one_action_one_proposal(
        pg, slack, monkeypatch):
    """Card #N is raised in its own channel. The same cause raised again
    returns #N; "approve N" typed in another channel, the card's own button
    and the operator command all name #N -- one run, one proposal row, and
    the later two are told it was already decided."""
    import httpx

    from atlas.api import main
    from tests.acceptance_support import SLACK_SECRET, drain_background
    post_event, _replies = slack
    world = StubWorld()
    install_stub(world)
    params = media_params(44)
    pid = await approvals.propose(CARD, params, "reel card", dedup_key="D04")
    again = await approvals.propose(CARD, params, "the same reel, raised again",
                                    dedup_key="D04")
    assert again == pid

    # 1. Typed by id in a channel that is not the card's.
    await post_event(slack_message(f"approve {pid}", event_id="EvD04",
                                   channel="C0ELSEWHERE"))
    await drain_background()
    assert [n for n, _ in world.runs] == [CARD]

    # 2. The card's own button, clicked afterwards.
    clicked: list[str] = []

    async def reply(_url, text, *, ephemeral=False):
        clicked.append(text)

    monkeypatch.setattr(main, "_slack_reply", reply)
    body = urllib_encode({"payload": json.dumps({
        "user": {"id": APPROVER}, "response_url": "https://hooks.slack/x",
        "actions": [{"value": f"approve:{pid}"}]})})
    ts = str(int(datetime.now(UTC).timestamp()))
    sig = "v0=" + _hmac(SLACK_SECRET, f"v0:{ts}:{body}")
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app, client=("127.0.0.1", 51000)),
            base_url="http://atlas.test") as client:
        r = await client.post("/webhooks/slack", content=body, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})
    assert r.status_code == 200
    assert clicked and f"#{pid} was already executed" in clicked[0]

    # 3. The operator command, with the id written "#N".
    said = await main._handle_command(f"approve #{pid}", via="cli:miro")
    assert f"#{pid} was already executed" in said

    assert [n for n, _ in world.runs] == [CARD]
    row = await state.fetch_one("SELECT count(*) AS n FROM proposals")
    assert row["n"] == 1


def urllib_encode(form: dict) -> str:
    import urllib.parse
    return urllib.parse.urlencode(form)


def _hmac(secret: str, base: str) -> str:
    import hashlib
    import hmac
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


# -------------------------------------------------------------------- D12

@needs_pg
async def test_D12_a_card_whose_content_changed_returns_to_review(pg):
    from atlas.kernel.flows import content as content_flow
    world = StubWorld()
    install_stub(world)
    params = media_params(12)
    pid = await approvals.propose(CARD, params, "reel card", dedup_key="D12")
    await content_flow.carded(params, dedup="day:D12", media_id=12, pid=pid)
    # An edit request widens what goes out: a new caption.
    await state.execute_sql(
        "UPDATE proposals SET params = $2::jsonb WHERE id = $1", pid,
        json.dumps({"media_id": 12, "caption": "Now with a giveaway. Made with AI"}))
    await approvals.decide(pid, "approve", "slack:UMIRO")
    assert world.runs == []          # required: back to review, nothing posted


async def _queued_and_approved(media_id: int, occurrence: str) -> int:
    """An intent approved without a card and queued (the outbox path): the
    row records the catalog version and the params hash as it is queued."""
    from tests.acceptance_support import PUBLISH
    got = await outbox.enqueue(PUBLISH, {**media_params(media_id),
                                         "platform": "instagram"},
                               destination="instagram", occurrence=occurrence,
                               approved_by="slack:UMIRO")
    row = await state.fetch_one(
        "SELECT catalog_version, approved_payload_hash FROM outbox WHERE id = $1",
        got.outbox_id)
    assert row["catalog_version"] and row["approved_payload_hash"]
    return got.outbox_id


@needs_pg
async def test_D12_an_approved_intent_edited_after_approval_returns_to_review(pg):
    """The outbox path. Every enqueue records `catalog_version` and
    `approved_payload_hash` (S08), rehashed from what JSONB stored, so an
    edit after the yes is refused by the executor before anything runs. The
    row ends `superseded` (terminal) and a fresh card asks about the edited
    version: that is "back to review"."""
    from tests.acceptance_support import PUBLISH
    world = StubWorld()
    install_stub(world)
    oid = await _queued_and_approved(13, "D12")
    await state.execute_sql(
        "UPDATE outbox SET params = $2::jsonb WHERE id = $1", oid,
        json.dumps({"media_id": 13, "caption": "Edited after the yes",
                    "platform": "instagram"}))
    done = await outbox.dispatch_once(only_id=oid)
    assert done.status == "superseded"
    assert world.runs == []          # required: back to review, nothing posted
    [row] = await outbox_rows()
    assert row["status"] == "superseded" and row["attempts"] == 0
    [card] = await state.fetch_all(
        "SELECT action, status, params FROM proposals WHERE dedup_key = $1",
        f"outbox-superseded:{oid}")
    assert card["action"] == PUBLISH and card["status"] == "pending"
    params = card["params"]
    params = json.loads(params) if isinstance(params, str) else params
    assert params["caption"] == "Edited after the yes"   # he reviews the edit
    await outbox.dispatch()
    assert world.runs == []          # the superseded row never runs


@needs_pg
async def test_D12_an_approved_intent_left_alone_runs_once(pg):
    """Positive control: the same row, not edited, is dispatched once."""
    world = StubWorld()
    install_stub(world)
    oid = await _queued_and_approved(14, "D12:ok")
    done = await outbox.dispatch_once(only_id=oid)
    assert done.status == "delivered"
    assert world.count() == 1
    row = await state.fetch_one("SELECT count(*) AS n FROM proposals")
    assert row["n"] == 0


@needs_pg
async def test_D12_a_row_queued_before_hashes_were_recorded_runs_as_before(pg):
    """Legacy: NULL columns are not a mismatch (no retroactive break)."""
    world = StubWorld()
    install_stub(world)
    oid = await _queued_and_approved(15, "D12:legacy")
    await state.execute_sql(
        """UPDATE outbox SET catalog_version = NULL,
                             approved_payload_hash = NULL WHERE id = $1""", oid)
    done = await outbox.dispatch_once(only_id=oid)
    assert done.status == "delivered" and world.count() == 1


# -------------------------------------------------------------------- D13

@needs_pg
async def test_D13_duplicate_weekly_runs_send_one_report_per_window(pg, monkeypatch):
    from atlas.kernel import missed, scheduler, weekly
    await state.execute_sql("TRUNCATE schedule_runs, reports")
    missed.forget()
    now = datetime.now().astimezone().replace(hour=12, minute=0, second=0,
                                              microsecond=0)
    monkeypatch.setenv("WEEKLY_DAY", str(now.weekday()))
    monkeypatch.setenv("BRIEF_HOUR", "7")
    monkeypatch.setattr(scheduler, "_now", lambda: now)
    sent: list[int] = []
    real_send = weekly.send

    async def send():
        sent.append(1)
        await asyncio.sleep(0.05)            # long enough for the two to overlap

    monkeypatch.setattr(weekly, "send", send)

    async def a_scheduler_process():
        """A fresh process: nothing in memory, only what Postgres holds."""
        monkeypatch.setattr(scheduler, "_last_weekly_week", None)
        missed.forget()
        await scheduler._maybe_weekly()

    # Two schedulers tick at once, then one restarts later the same week.
    await asyncio.gather(a_scheduler_process(), a_scheduler_process())
    await a_scheduler_process()
    assert sent == [1]
    [row] = await state.fetch_all(
        "SELECT occurrence, status FROM schedule_runs WHERE job = 'weekly'")
    assert row["status"] == "done"

    # The report itself is the second guard: one stored weekly this ISO week
    # and the real send() refuses another without building or posting it.
    await state.execute_sql(
        "INSERT INTO reports (at, kind, title, body, sections, delivered_via) "
        "VALUES (now(), 'weekly', 'This week', 'x', '[]', 'slack')")

    async def no_notify(_msg):
        raise AssertionError("a second weekly report was posted")

    monkeypatch.setattr("atlas.kernel.comms.notify", no_notify)
    assert await real_send() is None
