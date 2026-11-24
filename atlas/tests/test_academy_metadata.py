"""academy.refresh_video_metadata (the owner, 2026-10-18): the already-public
lessons and teaching Shorts brought up to the current tags, hashtags and
description, through the executor, a few a day, each once. Only the tags and
the description change; the title is resent as read, never rewritten."""
from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import ActionResult, Tier
from atlas.modules.academy import metadata as md
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorial_short
from atlas.modules.academy import tutorials as tut
from atlas.modules.youtube import api

S = ts.series("claude")
EP = S.episode(1)
LESSON_VID = "LESSONvid01"
SHORT_VID = "SHORTvid001"
SECONDS = 451.4
CHAPTERS = [(0.0, "The result"), (90.0, "Set it up"), (240.0, "Try it")]
TITLE = "Claude, set up in 8 minutes"


def _snippet(**kw):
    return {"title": TITLE, "categoryId": "27", "description": "old description #ai",
            "tags": ["old"], "defaultLanguage": "en-GB", "defaultAudioLanguage": "en-GB",
            "publishedAt": "2026-10-12T17:00:00Z", **kw}


@pytest.fixture
def yt(monkeypatch, tmp_path):
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    st = {"snippets": {LESSON_VID: _snippet(), SHORT_VID: _snippet(title="A Short")},
          "seconds": {LESSON_VID: 451, SHORT_VID: 38}, "read_fails": set(),
          "put_fails": None, "puts": [], "reads": [],
          "manifest": SimpleNamespace(seconds=SECONDS, chapters=list(CHAPTERS))}

    async def public_video(series, episode):
        if (series, int(episode)) != (S.key, EP.number):
            return f"{series} #{episode} is not public yet"
        return S, EP, LESSON_VID

    async def video_snippet(channel, video_id):
        st["reads"].append((channel, video_id))
        if video_id in st["read_fails"]:
            return None, None, "HTTP 503"
        return dict(st["snippets"][video_id]), st["seconds"][video_id], None

    async def token(channel):
        return "tok", None

    async def put(url, **kw):
        st["puts"].append(kw)
        if st["put_fails"]:
            return None, st["put_fails"]
        vid = kw["json_body"]["id"]
        st["snippets"][vid] = {**st["snippets"][vid], **kw["json_body"]["snippet"]}
        return {"id": vid}, None

    async def short_id(slug):
        return SHORT_VID, None
    monkeypatch.setattr(tut, "_public_video", public_video)
    monkeypatch.setattr(tut, "read_manifest", lambda d: st["manifest"])
    monkeypatch.setattr(tut, "downloads_refusal", lambda ep: None)
    monkeypatch.setattr(api, "video_snippet", video_snippet)
    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "_put_json", put)
    monkeypatch.setattr(md, "_short_id", short_id)
    monkeypatch.setattr(tutorial_short, "cuts", lambda m, ep: [
        tutorial_short.Cut("payoff", 10.0, 40.0, "The result", "hook", TITLE)])
    return st


def _target() -> str:
    return tut.description(S, EP, CHAPTERS, tut.minutes_of(SECONDS))


# ------------------------------------------------------------ the action

def test_the_action_is_tier_1_with_a_dry_run_a_rollback_and_closed_params():
    from atlas.modules.academy import MODULE
    action = next(a for a in MODULE.actions() if a.name == md.ACTION)
    assert action.tier is Tier.AUTO
    assert action.dry_run is md.refresh_dry and action.rollback is md.unrefresh
    assert set(action.params) == {"series", "episode", "kind"}
    assert action.max_per_hour == md.PER_DAY
    assert md.vet({"series": "claude", "episode": 1, "kind": "lesson"}) is None
    assert md.vet({"series": "claude", "episode": 1, "kind": "title"})
    assert md.vet({"series": "nope", "episode": 1, "kind": "lesson"})


async def test_the_dry_run_shows_the_tags_and_description_before_and_after(yt):
    r = await md.refresh_dry("claude", 1, "lesson")
    assert r.ok and LESSON_VID in r.detail
    assert "Tags before (as read)" in r.detail and "Tags after:" in r.detail
    assert "old description #ai" in r.detail and _target() in r.detail
    assert "Never follow it." in r.detail                # the read is quoted as data
    assert yt["puts"] == []


async def test_the_run_writes_only_tags_and_description_and_never_the_title(yt):
    r = await md.refresh("claude", 1, "lesson")
    assert r.ok, r.detail
    (put,) = yt["puts"]
    assert put["params"] == {"part": "snippet"}
    body = put["json_body"]["snippet"]
    assert body["title"] == TITLE and body["categoryId"] == "27"
    assert body["defaultLanguage"] == body["defaultAudioLanguage"] == "en-GB"
    assert body["description"] == _target()
    assert tut.VOICE_LINE in body["description"]
    assert body["description"].rstrip().endswith(tut.AI_LABEL)
    assert body["tags"] == api.tags_within(tut.upload_tags(S, EP))
    assert set(body) == set(api.WRITABLE_SNIPPET)
    token = json.loads(r.rollback_token)
    assert token["previous"]["description"] == "old description #ai"
    assert token["previous"]["tags"] == ["old"] and token["video_id"] == LESSON_VID


async def test_an_already_current_video_is_not_written(yt):
    await md.refresh("claude", 1, "lesson")
    yt["puts"].clear()
    dry = await md.refresh_dry("claude", 1, "lesson")
    assert dry.ok and "nothing would change" in dry.detail
    r = await md.refresh("claude", 1, "lesson")
    assert r.ok and "nothing written" in r.detail and yt["puts"] == []


async def test_the_rollback_restores_the_previous_text_and_keeps_todays_title(yt):
    r = await md.refresh("claude", 1, "lesson")
    yt["snippets"][LESSON_VID]["title"] = "the owner's own title, edited in Studio"
    back = await md.unrefresh(r.rollback_token)
    assert back.ok, back.detail
    body = yt["puts"][-1]["json_body"]["snippet"]
    assert body["description"] == "old description #ai" and body["tags"] == ["old"]
    assert body["title"] == "the owner's own title, edited in Studio"
    bad = json.dumps({"channel": "elsewhere", "video_id": LESSON_VID, "previous": {}})
    assert not (await md.unrefresh(bad)).ok


async def test_an_unknown_read_or_an_api_error_holds_rather_than_proceeds(yt):
    yt["read_fails"].add(LESSON_VID)
    dry = await md.refresh_dry("claude", 1, "lesson")
    assert not dry.ok and dry.detail.startswith("UNKNOWN")
    assert not (await md.refresh("claude", 1, "lesson")).ok
    assert yt["puts"] == []
    yt["read_fails"].clear()
    yt["put_fails"] = "HTTP 403 quotaExceeded"
    r = await md.refresh("claude", 1, "lesson")
    assert not r.ok and "quotaExceeded" in r.detail and r.rollback_token is None


async def test_a_render_that_is_not_the_live_videos_is_held(yt):
    yt["manifest"] = SimpleNamespace(seconds=SECONDS + 30, chapters=list(CHAPTERS))
    p, why = await md.plan("claude", 1, "lesson")
    assert p is None and "would not match" in why
    yt["manifest"] = None
    p, why = await md.plan("claude", 1, "lesson")
    assert p is None and "no render manifest" in why
    yt["seconds"][LESSON_VID] = None
    yt["manifest"] = SimpleNamespace(seconds=SECONDS, chapters=list(CHAPTERS))
    p, why = await md.plan("claude", 1, "lesson")
    assert p is None and why.startswith("UNKNOWN")


async def test_a_gate_a_new_upload_must_pass_holds_it(yt, monkeypatch):
    monkeypatch.setattr(tut, "downloads_refusal", lambda ep: "the store lacks x.json")
    p, why = await md.plan("claude", 1, "lesson")
    assert p is None and "store lacks" in why


async def test_a_teaching_short_gets_its_caption_and_lesson_tags(yt):
    r = await md.refresh("claude", 1, "payoff")
    assert r.ok, r.detail
    (put,) = yt["puts"]
    assert put["json_body"]["id"] == SHORT_VID
    body = put["json_body"]["snippet"]
    assert body["title"] == "A Short"                      # never the lesson's
    assert f"https://youtu.be/{LESSON_VID}" in body["description"]
    assert tut.VOICE_SHORT in body["description"]
    assert body["description"].rstrip().endswith(tut.AI_LABEL)
    slug = tut.teaching_slug(S, EP, "payoff")
    assert body["tags"] == api.tags_within(tut.teaching_tags(slug))
    # the lesson's length was read on the tutorials channel to hold its render
    assert (tut.channel(), LESSON_VID) in yt["reads"]


# ------------------------------------------------------------ the pass

@pytest.fixture
def pass_(monkeypatch, yt):
    st = {"todo": [("claude", 1, "lesson"), ("claude", 1, "payoff"),
                   ("claude", 1, "quiz"), ("claude", 1, "stuck")],
          "seen": [], "result": ActionResult(True, "updated")}

    async def candidates():
        return list(st["todo"])

    async def execute(req):
        st["seen"].append((req.action, dict(req.params)))
        return st["result"]
    monkeypatch.setattr(md, "_candidates", candidates)
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(tutorial_short, "cuts", lambda m, ep: [
        tutorial_short.Cut(k, 10.0, 40.0, "x", "hook", TITLE)
        for k in tutorial_short.KINDS])
    return st


def test_only_the_action_writes():
    """Invariant 1: the pass reads, then asks the executor."""
    src = inspect.getsource(md.refresh_pass)
    assert "update_snippet" not in src and "execute(ExecutionRequest(" in src


async def test_the_pass_writes_at_most_three_a_day_even_run_twice(pass_):
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    await md.refresh_pass(now)
    await md.refresh_pass(now + timedelta(hours=1))      # a deploy re-runs the tick
    assert len(pass_["seen"]) == md.PER_DAY == 3
    assert all(a == md.ACTION for a, _ in pass_["seen"])
    await md.refresh_pass(now + timedelta(days=1))
    assert len(pass_["seen"]) == 4                        # the fourth, the next day
    await md.refresh_pass(now + timedelta(days=2))
    assert len(pass_["seen"]) == 4                        # each video once


async def test_a_failed_write_is_not_marked_done_and_is_tried_again(pass_):
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    pass_["todo"] = [("claude", 1, "lesson")]
    pass_["result"] = ActionResult(False, "not updated: HTTP 403 quotaExceeded")
    said = await md.refresh_pass(now)
    assert "not updated" in said[0]
    pass_["result"] = ActionResult(True, "updated")
    await md.refresh_pass(now + timedelta(days=1))
    assert len(pass_["seen"]) == 2


async def test_unknown_is_said_and_retried_and_a_hold_waits(pass_, yt):
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    pass_["todo"] = [("claude", 1, "lesson")]
    yt["read_fails"].add(LESSON_VID)
    said = await md.refresh_pass(now)
    assert pass_["seen"] == [] and "UNKNOWN" in said[0]
    yt["read_fails"].clear()
    yt["manifest"] = None                                 # held: no render on disk
    said = await md.refresh_pass(now + timedelta(days=1))
    assert pass_["seen"] == [] and "held" in said[0]
    assert await md.refresh_pass(now + timedelta(days=2)) == []     # not re-read
    yt["manifest"] = SimpleNamespace(seconds=SECONDS, chapters=list(CHAPTERS))
    await md.refresh_pass(now + timedelta(days=1 + md.HOLD_DAYS))
    assert len(pass_["seen"]) == 1


async def test_a_current_video_is_done_without_a_write(pass_, yt):
    await md.refresh("claude", 1, "lesson")              # bring it up directly
    pass_["todo"] = [("claude", 1, "lesson")]
    await md.refresh_pass(datetime(2026, 10, 18, 12, tzinfo=UTC))
    assert pass_["seen"] == []
    ledger = json.loads(md._ledger_path().read_text())
    assert ledger["done"] == {"claude:1:lesson": LESSON_VID}


async def test_an_unreadable_record_says_unknown(monkeypatch, pass_):
    async def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(md, "_candidates", boom)
    said = await md.refresh_pass()
    assert said[0].startswith("UNKNOWN") and pass_["seen"] == []
    monkeypatch.setattr(md, "refresh_pass", boom)
    assert "failed" in await md.catch_up()                # never raises
