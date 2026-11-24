"""Task 55, cross-posting: one approved AI-stories reel, one Tier 2 card,
three legs -- the stories Instagram account, the Atlas Stories Facebook
Page, and TikTok through Postiz (its own steps: tests/test_reels_postiz.py).

What is load-bearing here:

* `content.publish_reel` is Tier ASK with a dry run and a rollback, its card
  lands in `instagram_stories`, and a caption without the AI label is
  refused before the card exists.
* The Instagram leg goes through `instagram.publish` as stories / reel /
  ai_story, so the content gate still applies to it.
* Each leg reports posted / refused: reason / not configured, and a refused
  leg's reason carries no token.
* TikTok's "posted, id pending" counts as posted for the rollback, which
  deletes what Instagram and Facebook posted and hands TikTok to Postiz's
  queued-only undo.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel import approvals
from atlas.kernel.contracts import Health, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import content, instagram
from atlas.modules.content import crosspost as pr
from atlas.modules.content import postiz
from atlas.modules.content.drafts import AI_LABEL

CAPTION = f"The sea was gone.\n\n{AI_LABEL}"
PAGE_TOKEN = "EAAG-page-secret"


@pytest.fixture(autouse=True)
def _slack_card_on(monkeypatch):
    """These tests read the Slack card; it is opt-in now (APPROVALS_SLACK)."""
    monkeypatch.setenv("APPROVALS_SLACK", "on")


def _action():
    return {a.name: a for a in content.MODULE.actions()}["content.publish_reel"]


@pytest.fixture(autouse=True)
def instagram_publish_registered(monkeypatch):
    """The Instagram leg runs under `instagram.publish`'s own gates
    (RA-C2): registered, allowed, within its hourly limit. Registered here as
    boot would, with the executor's windows empty for each test."""
    from atlas.kernel import executor
    spec = {a.name: a for a in instagram.MODULE.actions()}["instagram.publish"]
    monkeypatch.setitem(REGISTRY.actions, spec.name, spec)
    monkeypatch.setitem(REGISTRY.decisions, spec.name, classify(spec))
    monkeypatch.setattr(executor, "_recent", type(executor._recent)(
        executor._recent.default_factory))


@pytest.fixture
def media(memory_backend, monkeypatch, tmp_path):
    video = tmp_path / "42.mp4"
    video.write_bytes(b"mp4")
    row = {"id": 42, "status": "done", "path": str(video),
           "media_token": "tok-live",
           "media_token_expires_at": datetime.now(UTC) + timedelta(hours=3)}
    memory_backend.responses["FROM content_media WHERE id"] = row
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    return row


@pytest.fixture
def all_legs(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_ID", "page-1")
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_TOKEN", PAGE_TOKEN)
    monkeypatch.setenv("POSTIZ_API_KEY", "pz-key")


@pytest.fixture
def no_other_legs(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")
    for name in (*pr.FB_ENV, "POSTIZ_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def _mock(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


def _graph(fb_status: int = 200):
    """Instagram's container + publish and Facebook's /videos, one fake."""
    seen: list[tuple[str, str, dict]] = []

    def handler(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        seen.append((request.method, request.url.path, form))
        assert "access_token" not in str(request.url)
        if request.url.path.endswith("/sid/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.method == "GET" and request.url.path.endswith("/container-1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.url.path.endswith("/sid/media_publish"):
            return httpx.Response(200, json={"id": "ig-media-9"})
        if request.url.path.endswith("/page-1/videos"):
            if fb_status != 200:
                return httpx.Response(fb_status, json={"error": {
                    "message": f"token {PAGE_TOKEN} lacks pages_manage_posts"}})
            return httpx.Response(200, json={"id": "fb-video-7"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"success": True})
        return httpx.Response(500)

    return seen, handler


# ------------------------------------------------------------- the action

def test_publish_reel_is_tier_ask_with_a_dry_run_and_a_rollback():
    action = _action()
    assert action.tier is Tier.ASK and classify(action).tier is Tier.ASK
    assert action.dry_run is not None and action.rollback is not None
    assert action.verbose_card is True
    assert action.topic_for({"media_id": 1, "caption": CAPTION}) == "instagram_stories"
    assert action.vet({"media_id": 1, "caption": "no label"})
    assert action.vet({"media_id": 1, "caption": CAPTION}) is None


async def test_the_card_lands_in_the_reels_topic_and_needs_the_label(
        memory_backend, monkeypatch):
    action = _action()
    monkeypatch.setitem(REGISTRY.actions, action.name, action)
    monkeypatch.setitem(REGISTRY.decisions, action.name, classify(action))
    sent = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(approvals, "notify", fake_notify)
    memory_backend.responses["SELECT id FROM proposals"] = None
    memory_backend.responses["INSERT INTO proposals"] = {"id": 11}
    memory_backend.responses["FROM lessons"] = None
    memory_backend.responses["SELECT"] = []
    with pytest.raises(approvals.Unrunnable):
        await approvals.propose(action.name, {"media_id": 42, "caption": "x"},
                                reason="post it")
    assert not sent
    await approvals.propose(action.name, {"media_id": 42, "caption": CAPTION},
                            reason="post it")
    assert sent[0].topic == "instagram_stories"


# ------------------------------------------------------------- run

async def test_all_three_legs_post_and_each_carries_the_label(
        media, all_legs, monkeypatch):
    seen, handler = _graph()
    _mock(monkeypatch, instagram, handler)
    _mock(monkeypatch, pr, handler)
    tiktok_calls = []

    async def fake_postiz(video, caption):
        tiktok_calls.append((video, caption))
        said = "Postiz post pz-3, public (DIRECT_POST); TikTok returned no id yet"
        return postiz.ID_PENDING, said, "pz-3"

    monkeypatch.setattr(postiz, "post_tiktok", fake_postiz)
    result = await pr.publish_reel(42, CAPTION)

    assert result.ok is True, result.detail
    ig = next(form for _, path, form in seen if path.endswith("/sid/media"))
    assert ig["media_type"] == "REELS" and ig["caption"].endswith(AI_LABEL)
    assert ig["video_url"] == "https://atlas.example/media/tok-live"
    fb = next(form for _, path, form in seen if path.endswith("/videos"))
    assert fb["description"].endswith(AI_LABEL)
    assert fb["file_url"] == "https://atlas.example/media/tok-live"
    # the file itself, labelled; TikTok's discovery tags added before the label
    assert tiktok_calls == [(b"mp4", pr.platform_caption(CAPTION, "tiktok"))]
    assert tiktok_calls[0][1].endswith(AI_LABEL) and "#fyp" in tiktok_calls[0][1]
    assert "Instagram (stories): posted" in result.detail
    assert "Facebook Page: posted" in result.detail
    assert "TikTok (via Postiz): posted, id pending" in result.detail
    assert json.loads(result.rollback_token) == {
        "instagram": "stories:ig-media-9", "facebook": "fb-video-7",
        "tiktok": "pz-3"}
    assert "tok-live" not in result.detail


async def test_the_instagram_leg_goes_through_the_content_gate(
        media, no_other_legs, monkeypatch):
    called = {}

    async def fake_publish(caption, image_url, kind="image", account="main", *,
                           content_kind):
        called.update(kind=kind, account=account, content_kind=content_kind)
        return await instagram.publish_dry(caption, image_url, kind=kind,
                                           account=account,
                                           content_kind=content_kind)

    monkeypatch.setattr(instagram, "publish", fake_publish)
    await pr.publish_reel(42, CAPTION)
    assert called == {"kind": "reel", "account": "stories",
                      "content_kind": "ai_story"}


async def test_unconfigured_legs_say_so_and_one_post_is_enough(
        media, no_other_legs, monkeypatch):
    _, handler = _graph()
    _mock(monkeypatch, instagram, handler)
    result = await pr.publish_reel(42, CAPTION)
    assert result.ok is True
    assert "Facebook Page: not configured — FACEBOOK_STORIES_PAGE_ID" in result.detail
    assert "TikTok (via Postiz): not configured — POSTIZ_API_KEY unset" \
        in result.detail
    assert json.loads(result.rollback_token) == {"instagram": "stories:ig-media-9"}


async def test_a_refused_leg_names_its_reason_without_the_token(
        media, all_legs, monkeypatch):
    _, handler = _graph(fb_status=403)
    _mock(monkeypatch, instagram, handler)
    _mock(monkeypatch, pr, handler)

    async def refused(video, caption):
        return postiz.REFUSED, "Postiz has no enabled TikTok integration", None

    monkeypatch.setattr(postiz, "post_tiktok", refused)
    result = await pr.publish_reel(42, CAPTION)
    assert result.ok is True
    assert "Facebook Page: refused — Graph API HTTP 403" in result.detail
    assert PAGE_TOKEN not in result.detail
    assert "TikTok (via Postiz): refused — Postiz has no enabled" in result.detail
    assert set(json.loads(result.rollback_token)) == {"instagram"}


async def test_posted_nowhere_is_a_failure(media, no_other_legs, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_STORIES_TOKEN")
    result = await pr.publish_reel(42, CAPTION)
    assert result.ok is False and "posted nowhere" in result.detail
    assert result.rollback_token is None


async def test_a_lapsed_link_is_re_minted_on_approval(media, no_other_legs,
                                                      memory_backend, monkeypatch):
    media["media_token_expires_at"] = datetime.now(UTC) - timedelta(minutes=5)
    _, handler = _graph()
    _mock(monkeypatch, instagram, handler)
    result = await pr.publish_reel(42, CAPTION)
    assert result.ok is True
    minted = [args for sql, args in memory_backend.sql_log
              if "SET media_token = $2" in sql]
    assert minted and minted[0][0] == 42 and minted[0][1] != "tok-live"


async def test_an_unfinished_render_is_refused(memory_backend, monkeypatch):
    memory_backend.responses["FROM content_media WHERE id"] = {
        "id": 5, "status": "rendering", "path": None, "media_token": None,
        "media_token_expires_at": None}
    result = await pr.publish_reel(5, CAPTION)
    assert result.ok is False and "rendering" in result.detail


# ------------------------------------------------------------- dry run

async def test_the_dry_run_gives_a_per_platform_status(media, no_other_legs,
                                                       monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", "pz-key")

    async def ready():
        return True, "public, DIRECT_POST; Postiz's TikTok settings allow it"

    monkeypatch.setattr(postiz, "readiness", ready)
    result = await pr.publish_reel_dry(42, CAPTION)
    assert result.ok is True
    assert "Instagram (stories): ready" in result.detail
    assert "Facebook Page: not configured" in result.detail
    assert "TikTok (via Postiz): ready — public, DIRECT_POST" in result.detail
    assert "would post to 2 of 3" in result.detail


async def test_the_dry_run_names_the_tiktok_audit(media, no_other_legs,
                                                 monkeypatch):
    monkeypatch.setenv("POSTIZ_API_KEY", "pz-key")

    async def private_only():
        return False, postiz.AUDIT_NOTE

    monkeypatch.setattr(postiz, "readiness", private_only)
    result = await pr.publish_reel_dry(42, CAPTION)
    assert ("TikTok (via Postiz): refused — private until TikTok's audit passes"
            in result.detail)
    assert "would post to 1 of 3" in result.detail


async def test_the_dry_run_refuses_when_no_leg_is_configured(media, monkeypatch):
    for name in ("INSTAGRAM_STORIES_ACCOUNT_ID", "INSTAGRAM_STORIES_TOKEN",
                 *pr.FB_ENV, "POSTIZ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    result = await pr.publish_reel_dry(42, CAPTION)
    assert result.ok is False and "no platform is configured" in result.detail
    assert "POSTIZ_API_KEY unset" in result.detail


# ------------------------------------------------------------- rollback

async def test_the_rollback_deletes_what_it_can_and_notes_a_published_tiktok(
        all_legs, monkeypatch):
    seen, handler = _graph()
    _mock(monkeypatch, instagram, handler)
    _mock(monkeypatch, pr, handler)

    async def published(post_id):
        return False, f"MANUAL: Postiz post {post_id} has published"

    monkeypatch.setattr(postiz, "undo", published)
    token = json.dumps({"instagram": "stories:ig-media-9", "facebook": "fb-video-7",
                        "tiktok": "pz-3"})
    result = await pr.unpublish_reel(token)
    deletes = [path for method, path, _ in seen if method == "DELETE"]
    assert deletes == ["/v21.0/ig-media-9", "/v21.0/fb-video-7"]
    assert result.ok is False
    assert "Instagram (stories): deleted" in result.detail
    assert "Facebook Page: deleted" in result.detail
    assert "TikTok (via Postiz): not deleted — MANUAL: Postiz post pz-3" \
        in result.detail


async def test_the_rollback_is_complete_without_a_tiktok_leg(all_legs, monkeypatch):
    _, handler = _graph()
    _mock(monkeypatch, instagram, handler)
    _mock(monkeypatch, pr, handler)
    result = await pr.unpublish_reel(json.dumps({"instagram": "stories:ig-media-9",
                                                 "facebook": "fb-video-7"}))
    assert result.ok is True


async def test_a_malformed_rollback_token_is_refused():
    result = await pr.unpublish_reel("not json")
    assert result.ok is False and "malformed" in result.detail


# ------------------------------------------------------------- the check

async def test_reel_legs_is_unknown_for_an_unconfigured_leg(no_other_legs):
    obs = await pr.check_reel_legs()
    assert obs.health == Health.UNKNOWN
    assert "Facebook Page: FACEBOOK_STORIES_PAGE_ID" in obs.detail
    assert "pages_manage_posts" in obs.detail
    assert "TikTok (via Postiz): POSTIZ_API_KEY unset" in obs.detail


async def test_reel_legs_is_ok_when_all_three_are_configured(all_legs):
    obs = await pr.check_reel_legs()
    assert obs.health == Health.OK
    assert "TikTok (via Postiz): configured, posts public by DIRECT_POST" \
        in obs.detail



# ------------------------------------------------------------- approving (review I3)

@pytest.fixture
def approving(memory_backend, monkeypatch):
    import asyncio

    from atlas.kernel import comms
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    action = _action()
    monkeypatch.setitem(REGISTRY.actions, action.name, action)
    monkeypatch.setitem(REGISTRY.decisions, action.name, classify(action))
    memory_backend.responses["UPDATE proposals SET status='approved'"] = {
        "action": "content.publish_reel",
        "params": json.dumps({"media_id": 42, "caption": CAPTION}),
        "reason": "post it", "incident_id": None}
    memory_backend.responses["SELECT"] = []
    monkeypatch.setattr(comms, "_REFS", {"#41 — Post a reel": {"slack": "1727.5"}})
    gate = asyncio.Event()
    ran: list = []

    async def slow_execute(req):
        ran.append(req)
        await gate.wait()
        return pr.ActionResult(True, "posted", evidence={
            "instagram": {"status": "posted", "detail": "published m-1"},
            "facebook": {"status": "not configured", "detail": "unset"},
            "tiktok": {"status": "posted, id pending", "detail": "Postiz pz-3"}})

    sent: list = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    async def no_outcome(*a, **k):
        return None

    from atlas.kernel import judgment
    monkeypatch.setattr(approvals, "execute", slow_execute)
    monkeypatch.setattr(approvals, "notify", fake_notify)
    monkeypatch.setattr(judgment, "record_outcome", no_outcome)
    return gate, ran, sent, memory_backend


async def test_approving_a_reel_card_answers_at_once(approving):
    import asyncio
    import time
    gate, ran, sent, backend = approving
    started = time.monotonic()
    verdict = await approvals.decide(41, "approve", via="slack:U1")
    assert time.monotonic() - started < 1.0          # well inside Slack's 3 s
    assert verdict.ok and verdict.outcome == "started" and not verdict.executed
    assert verdict.render() == "Posting #41 now; the receipts will be in the thread."
    task = approvals.RUNNING[41]
    await asyncio.sleep(0)
    assert ran and ran[0].approved_by == "slack:U1"  # the executor's path
    gate.set()
    await task
    receipts = [m for m in sent if m.kind == "receipt"]
    assert [m.title for m in receipts] == [
        "#41 — instagram: posted", "#41 — facebook: not configured",
        "#41 — tiktok: posted, id pending"]
    assert all(m.thread == "1727.5" and m.topic == "instagram_stories"
               for m in receipts)
    assert any("SET status=$2" in sql and args == (41, "executed")
               for sql, args in backend.sql_log)
    assert 41 not in approvals.RUNNING


async def test_a_shutdown_mid_post_says_so_in_the_thread(approving):
    import asyncio
    _, _, sent, backend = approving
    await approvals.decide(41, "approve", via="voice")
    task = approvals.RUNNING[41]
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any("cut short by a shutdown" in m.body for m in sent)
    assert any(args == (41, "failed") for sql, args in backend.sql_log
               if "SET status=$2" in sql)


async def test_an_ordinary_action_still_runs_inline(memory_backend, monkeypatch):
    from atlas.kernel.contracts import Action, ActionResult, Tier
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")

    async def ok(**kw):
        return ActionResult(True, "ok")

    act = Action(name="gitlab.open_issue", tier=Tier.ASK, summary="File",
                 run=ok, dry_run=ok, irreversible=True, params={"title": "t"})
    monkeypatch.setitem(REGISTRY.actions, act.name, act)
    memory_backend.responses["UPDATE proposals SET status='approved'"] = {
        "action": act.name, "params": "{}", "reason": "r", "incident_id": None}
    memory_backend.responses["SELECT"] = []

    async def fake_execute(req):
        return ActionResult(True, "filed")

    async def no_outcome(*a, **k):
        return None

    from atlas.kernel import judgment
    monkeypatch.setattr(approvals, "execute", fake_execute)
    monkeypatch.setattr(judgment, "record_outcome", no_outcome)
    verdict = await approvals.decide(7, "approve", via="slack:U1")
    assert verdict.outcome == "executed" and verdict.executed



def test_the_tiktok_caption_adds_tags_once_and_keeps_the_label_last():
    cap = "The sea was gone. #fyp\n\n" + AI_LABEL
    out = pr.platform_caption(cap, "tiktok")
    assert out.count("#fyp") == 1 and "#aistory" in out and "#storytime" in out
    assert out.endswith(AI_LABEL)
    assert pr.platform_caption(cap, "instagram") == cap
    assert len(pr.platform_caption("x" * 3000 + " " + AI_LABEL, "tiktok")) <= 2200
