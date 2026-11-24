"""Task 55 (a) and (b): what each Instagram account may post, and where the
AI-stories account's cards land.

Miro, relayed from production: @flycheaphp (`main`) posts ONLY FlyCheap /
travel / cheap-flight content, never AI story reels; AI reels go ONLY to
@atlasstories3 (`stories`). Enforced at PROPOSE time, not only in a prompt:

* `instagram.ACCOUNT_KINDS` names the content kinds each account takes.
* `instagram.publish` carries a required `content_kind`; its dry run and its
  run both refuse a kind the account does not take, before any network call.
* `approvals.propose()` asks the action's pure `vet` before a card exists,
  so a mismatched card is never raised at all.

(b): #instagram-reels takes the stories account's cards. The main account's
cards keep landing exactly where the `instagram` topic always sent them.
"""
from __future__ import annotations

import inspect

import httpx
import pytest

from atlas.kernel import approvals
from atlas.kernel.contracts import ActionResult
from atlas.kernel.executor import required_params
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import instagram
from atlas.modules.content.drafts import AI_LABEL

LABELLED = f"a short story\n\n{AI_LABEL}"


@pytest.fixture(autouse=True)
def _slack_card_on(monkeypatch):
    """These tests read the Slack card; it is opt-in now (APPROVALS_SLACK)."""
    monkeypatch.setenv("APPROVALS_SLACK", "on")


@pytest.fixture
def both_accounts(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "mid")
    monkeypatch.setenv("INSTAGRAM_TOKEN", "mtok")
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")


def _publish_action():
    return {a.name: a for a in instagram.MODULE.actions()}["instagram.publish"]


# ------------------------------------------------------------- the table

def test_account_kinds_is_exactly_what_miro_ruled():
    assert instagram.ACCOUNT_KINDS == {"main": {"flycheap", "travel"},
                                       "stories": {"ai_story"},
                                       # + the store promo, Miro 2026-10-05
                                       "clips": {"campaign_clip", "store_promo"}}
    assert set(instagram.ACCOUNT_KINDS) == set(instagram.ACCOUNTS)


def test_content_kind_is_a_required_declared_parameter():
    action = _publish_action()
    assert "content_kind" in action.params
    sig = inspect.signature(instagram.publish)
    assert sig.parameters["content_kind"].default is inspect.Parameter.empty
    assert "content_kind" in required_params(action)
    # `kind` still means the media type, untouched.
    assert sig.parameters["kind"].default == "image"


# ------------------------------------------------------------- dry run / run

@pytest.mark.parametrize(("account", "content_kind"), [
    ("main", "ai_story"), ("stories", "travel"), ("stories", "flycheap"),
    ("main", "cat_pictures"), ("main", ""), ("stories", ""),
])
async def test_publish_dry_refuses_a_kind_the_account_does_not_take(
        both_accounts, account, content_kind):
    caption = LABELLED if account == "stories" else "LHR to JFK from £199"
    result = await instagram.publish_dry(caption, "https://x/v.mp4", kind="reel",
                                         account=account, content_kind=content_kind)
    assert result.ok is False
    assert account in result.detail
    assert "only posts" in result.detail


@pytest.mark.parametrize(("account", "content_kind", "caption"), [
    ("main", "flycheap", "LHR to JFK from £199"),
    ("main", "travel", "Lisbon in October"),
    ("stories", "ai_story", LABELLED),
])
async def test_publish_dry_allows_a_kind_the_account_takes(
        both_accounts, account, content_kind, caption):
    result = await instagram.publish_dry(caption, "https://x/v.mp4", kind="reel",
                                         account=account, content_kind=content_kind)
    assert result.ok is True, result.detail


async def test_publish_refuses_an_ai_story_on_main_before_any_network(
        both_accounts, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Graph must not be reached for a refused kind")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    result = await instagram.publish(LABELLED, "https://x/v.mp4", kind="reel",
                                     account="main", content_kind="ai_story")
    assert result.ok is False
    assert "main" in result.detail and "ai_story" in result.detail


async def test_publish_refuses_travel_on_stories_before_any_network(
        both_accounts, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Graph must not be reached for a refused kind")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    result = await instagram.publish(LABELLED, "https://x/v.mp4", kind="reel",
                                     account="stories", content_kind="travel")
    assert result.ok is False
    assert "stories" in result.detail


# ------------------------------------------------------------- vet

def test_vet_refuses_mismatches_and_passes_matches():
    vet = _publish_action().vet
    assert vet is not None
    assert vet({"caption": "x", "image_url": "u", "account": "main",
                "content_kind": "ai_story"})
    assert vet({"caption": LABELLED, "image_url": "u", "account": "stories",
                "content_kind": "travel"})
    # `account` defaults to main, exactly as the run signature does.
    assert vet({"caption": "x", "image_url": "u", "content_kind": "ai_story"})
    assert vet({"caption": "x", "image_url": "u", "account": "main",
                "content_kind": "flycheap"}) is None
    assert vet({"caption": LABELLED, "image_url": "u", "account": "stories",
                "content_kind": "ai_story"}) is None


def test_vet_also_refuses_an_unlabelled_stories_caption():
    vet = _publish_action().vet
    why = vet({"caption": "no label", "image_url": "u", "account": "stories",
               "content_kind": "ai_story"})
    assert why and "AI_LABEL" in why


# ------------------------------------------------------------- propose()

@pytest.fixture
def proposing(memory_backend, monkeypatch):
    action = _publish_action()
    monkeypatch.setitem(REGISTRY.actions, action.name, action)
    monkeypatch.setitem(REGISTRY.decisions, action.name, classify(action))
    sent: list = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(approvals, "notify", fake_notify)
    memory_backend.responses["SELECT id FROM proposals"] = None
    memory_backend.responses["INSERT INTO proposals"] = {"id": 7}
    memory_backend.responses["FROM lessons"] = None
    memory_backend.responses["SELECT"] = []
    return memory_backend, sent


@pytest.mark.parametrize(("account", "content_kind", "caption"), [
    ("main", "ai_story", LABELLED),
    ("stories", "travel", LABELLED),
    ("stories", "flycheap", LABELLED),
])
async def test_propose_refuses_a_mismatched_card_before_it_exists(
        proposing, account, content_kind, caption):
    backend, sent = proposing
    with pytest.raises(approvals.Unrunnable) as refused:
        await approvals.propose("instagram.publish", {
            "caption": caption, "image_url": "https://x/v.mp4", "kind": "reel",
            "account": account, "content_kind": content_kind}, reason="post it")
    assert account in refused.value.spoken
    assert not sent
    assert not any("INSERT INTO proposals" in sql for sql, _ in backend.sql_log)
    assert any(row.event == "proposal.vetoed" for row in backend.audit_rows)


async def test_propose_refuses_a_card_with_no_content_kind(proposing):
    _, sent = proposing
    with pytest.raises(approvals.MissingParams) as refused:
        await approvals.propose("instagram.publish", {
            "caption": "LHR to JFK", "image_url": "https://x/i.jpg"},
            reason="post it")
    assert refused.value.missing == ["content_kind"]
    assert not sent


@pytest.mark.parametrize(("account", "content_kind", "caption"), [
    ("main", "travel", "Lisbon in October"),
    ("stories", "ai_story", LABELLED),
])
async def test_propose_raises_a_matching_card(proposing, account, content_kind,
                                               caption):
    _, sent = proposing
    pid = await approvals.propose("instagram.publish", {
        "caption": caption, "image_url": "https://x/v.mp4", "kind": "reel",
        "account": account, "content_kind": content_kind}, reason="post it")
    assert pid == 7
    assert sent


async def test_an_action_without_a_vet_is_unaffected(memory_backend, monkeypatch):
    from atlas.kernel.contracts import Action, Tier

    async def ok(**kw):
        return ActionResult(True, "ok")

    act = Action(name="gitlab.open_issue", tier=Tier.ASK, summary="File",
                 run=ok, dry_run=ok, irreversible=True, params={"title": "t"})
    assert act.vet is None
    monkeypatch.setitem(REGISTRY.actions, act.name, act)
    monkeypatch.setitem(REGISTRY.decisions, act.name, classify(act))

    async def fake_notify(msg):
        return ["slack"]

    monkeypatch.setattr(approvals, "notify", fake_notify)
    memory_backend.responses["SELECT id FROM proposals"] = None
    memory_backend.responses["INSERT INTO proposals"] = {"id": 9}
    memory_backend.responses["FROM lessons"] = None
    memory_backend.responses["SELECT"] = []
    assert await approvals.propose(act.name, {"title": "x"}, reason="r") == 9


# ================================================================== (b)
# #instagram-reels: the stories account's cards and its overnight report.

REELS_ID = "C0C3XH0F6S3"


@pytest.fixture
def slack(monkeypatch):
    from atlas.channels.slack import SlackChannel
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL", "CDEFAULT")
    monkeypatch.delenv("SLACK_CHANNELS", raising=False)
    monkeypatch.delenv("SLACK_INSTAGRAM_REELS_CHANNEL", raising=False)
    return SlackChannel()


def test_the_stories_topic_lands_in_the_reels_channel_named_by_env(slack, monkeypatch):
    monkeypatch.setenv("SLACK_INSTAGRAM_REELS_CHANNEL", REELS_ID)
    assert slack.channel_for("instagram_stories") == REELS_ID


def test_without_the_env_the_stories_topic_falls_back_to_the_default(slack):
    assert slack.channel_for("instagram_stories") == "CDEFAULT"


def test_an_explicit_slack_channels_entry_still_wins(slack, monkeypatch):
    monkeypatch.setenv("SLACK_INSTAGRAM_REELS_CHANNEL", REELS_ID)
    monkeypatch.setenv("SLACK_CHANNELS", "instagram_stories:COVERRIDE")
    assert slack.channel_for("instagram_stories") == "COVERRIDE"


def test_the_main_accounts_topic_is_untouched_by_the_reels_env(slack, monkeypatch):
    monkeypatch.setenv("SLACK_INSTAGRAM_REELS_CHANNEL", REELS_ID)
    monkeypatch.setenv("SLACK_CHANNELS", "instagram:CINSTA")
    assert slack.channel_for("instagram") == "CINSTA"
    monkeypatch.delenv("SLACK_CHANNELS")
    assert slack.channel_for("instagram") == "CDEFAULT"


def test_the_reels_channel_id_is_never_hard_coded():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "atlas"
    hits = [str(p) for p in root.rglob("*.py") if REELS_ID in p.read_text()]
    assert hits == []


@pytest.mark.parametrize(("account", "content_kind", "caption", "topic"), [
    ("stories", "ai_story", LABELLED, "instagram_stories"),
    ("main", "travel", "Lisbon in October", "instagram"),
])
async def test_a_publish_card_lands_by_account(proposing, account, content_kind,
                                               caption, topic):
    _, sent = proposing
    await approvals.propose("instagram.publish", {
        "caption": caption, "image_url": "https://x/v.mp4", "kind": "reel",
        "account": account, "content_kind": content_kind}, reason="post it")
    assert sent[0].topic == topic


async def test_the_overnight_report_goes_to_the_stories_topic_with_its_read_time(
        memory_backend, monkeypatch):
    from datetime import UTC, datetime

    from atlas.kernel import reels
    from atlas.kernel.contracts import Health, Observation

    at = datetime(2026, 9, 24, 6, 5, tzinfo=UTC)

    async def fake_overnight(account):
        assert account == "stories"
        return Observation(f"{account}_overnight", Health.OK,
                           detail="2 hidden, 1 comment(s) answered", at=at)

    monkeypatch.setattr(instagram, "check_overnight", fake_overnight)
    sent = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(reels, "notify", fake_notify)
    monkeypatch.setenv(reels.DIGEST_ENV, "daily")
    lines = await reels.overnight_lines()
    await reels.send_digest(lines)
    assert sent[0].topic == "instagram_stories"
    assert sent[0].kind == "digest"
    assert "06:05 UTC" in sent[0].body and "2 hidden" in sent[0].body
    stored = [args for sql, args in memory_backend.sql_log
              if "INSERT INTO reports" in sql]
    assert stored and stored[0][0] == reels.REPORT_KIND


async def test_an_unmeasured_overnight_is_said_plainly(memory_backend, monkeypatch):
    from atlas.kernel import reels
    from atlas.kernel.contracts import Health, Observation

    async def fake_overnight(account):
        return Observation(f"{account}_overnight", Health.UNKNOWN,
                           detail="not read — actions_log unreachable")

    monkeypatch.setattr(instagram, "check_overnight", fake_overnight)
    lines = await reels.overnight_lines()
    assert "not fully measured" in lines[0]
    assert "actions_log unreachable" in lines[0]


async def test_the_day_is_not_sent_twice_after_a_restart(memory_backend, monkeypatch):
    from datetime import datetime

    from atlas.kernel import reels

    now = datetime.now().astimezone()
    memory_backend.responses["FROM reports WHERE kind"] = {"at": now}

    async def boom():
        raise AssertionError("already sent today: nothing may be read again")

    monkeypatch.setattr(reels, "overnight_lines", boom)
    await reels.daily(now)


async def test_an_unreadable_reports_table_runs_nothing(memory_backend, monkeypatch):
    from atlas.kernel import reels

    async def boom():
        raise AssertionError("reports unreadable: nothing may run")

    monkeypatch.setattr(reels, "overnight_lines", boom)
    # MemoryBackend refuses the un-canned SELECT: that IS the unreadable case.
    assert await reels.already_sent_today() is None
    await reels.daily()


async def test_the_scheduler_waits_for_the_brief_hour(monkeypatch):
    from datetime import datetime

    from atlas.kernel import scheduler

    monkeypatch.setattr(scheduler, "_last_reels_date", None)
    monkeypatch.setattr(scheduler, "_last_nudge_date", None)
    monkeypatch.setattr(scheduler, "_reels_task", None)
    monkeypatch.setattr(scheduler, "_brief_hour", lambda: 7)
    monkeypatch.setattr(scheduler, "_now",
                        lambda: datetime(2026, 9, 24, 6, 59).astimezone())
    spawned = []
    monkeypatch.setattr(scheduler.background, "spawn",
                        lambda coro, name: spawned.append(name) or coro.close())
    await scheduler._maybe_reels()
    assert spawned == []
    monkeypatch.setattr(scheduler, "_now",
                        lambda: datetime(2026, 9, 24, 7, 1).astimezone())
    await scheduler._maybe_reels()
    await scheduler._maybe_reels()
    assert spawned == ["the AI-stories day"]


# ================================================================== the reel container

def _reel_graph(monkeypatch, statuses: list[str]):
    """Graph: container, then `statuses` in turn for each status poll, then
    media_publish. Records every call."""
    calls: list[tuple[str, str]] = []
    queue = list(statuses)

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith(("/sid/media", "/mid/media")):
            return httpx.Response(200, json={"id": "cont-1"})
        if request.method == "GET" and request.url.path.endswith("/cont-1"):
            assert request.url.params["fields"] == "status_code"
            return httpx.Response(200, json={"status_code": queue.pop(0)})
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "media-7"})
        return httpx.Response(500)

    real = httpx.AsyncClient
    monkeypatch.setattr(instagram.httpx, "AsyncClient", lambda *a, **k: real(
        transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(instagram, "REEL_POLL_S", 0)
    return calls


async def test_a_reel_is_published_only_once_its_container_is_finished(
        both_accounts, monkeypatch):
    calls = _reel_graph(monkeypatch, ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"])
    result = await instagram.publish(LABELLED, "https://x/v.mp4", kind="reel",
                                     account="stories", content_kind="ai_story")
    assert result.ok is True and "media-7" in result.detail
    polls = [c for c in calls if c[0] == "GET"]
    assert len(polls) == 3
    assert calls[-1][1].endswith("/sid/media_publish")
    assert calls.index(polls[-1]) < calls.index(calls[-1])


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
async def test_a_failed_container_is_never_published(both_accounts, monkeypatch,
                                                     status):
    calls = _reel_graph(monkeypatch, ["IN_PROGRESS", status])
    result = await instagram.publish(LABELLED, "https://x/v.mp4", kind="reel",
                                     account="stories", content_kind="ai_story")
    assert result.ok is False and status in result.detail
    assert not any(path.endswith("/media_publish") for _, path in calls)


async def test_a_container_still_processing_at_the_deadline_is_not_published(
        both_accounts, monkeypatch):
    calls = _reel_graph(monkeypatch, ["IN_PROGRESS"] * 5)
    monkeypatch.setattr(instagram, "REEL_DEADLINE_S", 0)
    result = await instagram.publish(LABELLED, "https://x/v.mp4", kind="reel",
                                     account="stories", content_kind="ai_story")
    assert result.ok is False and "still processing" in result.detail
    assert not any(path.endswith("/media_publish") for _, path in calls)


async def test_an_image_needs_no_container_wait(both_accounts, monkeypatch):
    calls = _reel_graph(monkeypatch, [])
    result = await instagram.publish("LHR to JFK £199", "https://x/i.jpg",
                                     account="main", content_kind="flycheap")
    assert result.ok is True
    assert not any(method == "GET" for method, _ in calls)



# ================================================================== I2

def test_the_reels_channel_default_lives_in_env_example_not_code():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert f"SLACK_INSTAGRAM_REELS_CHANNEL={REELS_ID}" in (
        root / ".env.example").read_text()
    assert REELS_ID in (root / "CONFIGURE.md").read_text()


async def test_an_unset_reels_channel_is_degraded_never_silent(monkeypatch):
    from atlas.kernel.contracts import Health
    from atlas.modules import content
    monkeypatch.delenv("SLACK_INSTAGRAM_REELS_CHANNEL", raising=False)
    monkeypatch.setenv("SLACK_CHANNELS", "cloudhost:CVPS")
    obs = await content.check_reels_channel()
    assert obs.health == Health.DEGRADED
    assert obs.detail == ("SLACK_INSTAGRAM_REELS_CHANNEL unset — cards fall to "
                          "the default route")
    monkeypatch.setenv("SLACK_INSTAGRAM_REELS_CHANNEL", REELS_ID)
    assert (await content.check_reels_channel()).health == Health.OK
    monkeypatch.delenv("SLACK_INSTAGRAM_REELS_CHANNEL")
    monkeypatch.setenv("SLACK_CHANNELS", "cloudhost:CVPS,instagram_stories:CX")
    assert (await content.check_reels_channel()).health == Health.OK


async def test_the_calendar_ticks_at_any_hour_but_not_twice_in_half_an_hour(monkeypatch):
    """Banking clips cannot wait for the brief hour: the day's allowance
    resets at New York midnight and the longest days need more than one."""
    from datetime import datetime, timedelta

    from atlas.kernel import scheduler

    monkeypatch.setattr(scheduler, "_calendar_task", None)
    monkeypatch.setattr(scheduler, "_last_calendar_at", None)
    at = datetime(2026, 9, 28, 0, 5).astimezone()
    monkeypatch.setattr(scheduler, "_now", lambda: at)
    spawned = []
    monkeypatch.setattr(scheduler.background, "spawn",
                        lambda coro, name: spawned.append(name) or coro.close())
    await scheduler._maybe_calendar()
    await scheduler._maybe_calendar()
    assert spawned == ["the reels calendar"]
    monkeypatch.setattr(scheduler, "_now", lambda: at + timedelta(minutes=31))
    await scheduler._maybe_calendar()
    assert spawned == ["the reels calendar"] * 2


def test_a_reel_card_leads_with_its_quality_and_its_state():
    from atlas.kernel import reels
    fmt = {"format": "twist", "weight": 1, "source": "research"}
    drafted = {"scenes": ["a", "b"], "caption": "c"}
    fallback = reels._card_reason(fmt, drafted,
                                  "stills fallback (Veo quota): media 7", "ig: ready")
    lines = fallback.split("\n")
    assert lines[0] == "*Quality exception: still-image fallback — Veo quota.*"
    assert lines[1] == "Status: draft only; nothing is published until you approve."
    clean = reels._card_reason(fmt, drafted, "rendered on Veo: media 8", "ig: ready")
    assert clean.split("\n")[0].startswith("Status: draft only")
    assert "Quality exception" not in clean
