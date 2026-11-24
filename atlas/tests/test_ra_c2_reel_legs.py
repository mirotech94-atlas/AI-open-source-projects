"""RA-C2: a reel's Instagram leg runs under `instagram.publish`'s own gates.

`content/crosspost.py` calls `instagram.publish` as a function inside the
parent's executor run. What is load-bearing:

* The leg shares the executor's limiter with `instagram.publish`: two
  Instagram posts in an hour, by any path, and the third leg is refused --
  and a refused leg spends nothing.
* A paused or switched-off `instagram` module means no Instagram leg; a
  parent not listed in `kernel.legs.PARENTS` cannot run the leg.
* Every attempt is in the audit chain as `leg.start` / `leg.finish` (or
  `leg.refused`), naming the parent.
* the owner's settled flow still holds: a reel that passes posts itself; one held
  only by an hourly allowance WAITS -- never a remake (a paid Veo call) and
  never a card.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel import executor, legs
from atlas.kernel import reels as day
from atlas.kernel.contracts import ActionResult
from atlas.kernel.permissions import Decision, classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import instagram
from atlas.modules.content import crosspost as pr
from atlas.modules.content.drafts import AI_LABEL

CAPTION = f"The sea was gone.\n\n{AI_LABEL}"


@pytest.fixture(autouse=True)
def quality_passes(monkeypatch):
    """These tests are about the legs and holds, not the file: the RA-E5
    quality gate (its own tests in test_reels_quality.py) passes here."""
    from atlas.modules.content import quality

    async def ok(*_a, **_k):
        return None
    monkeypatch.setattr(quality, "check_media", ok)


@pytest.fixture(autouse=True)
def fresh_windows(monkeypatch):
    monkeypatch.setattr(executor, "_recent", type(executor._recent)(
        executor._recent.default_factory))


@pytest.fixture
def registered(monkeypatch):
    spec = {a.name: a for a in instagram.MODULE.actions()}["instagram.publish"]
    monkeypatch.setitem(REGISTRY.actions, spec.name, spec)
    monkeypatch.setitem(REGISTRY.decisions, spec.name, classify(spec))
    return spec


@pytest.fixture
def media(memory_backend, monkeypatch, tmp_path):
    video = tmp_path / "42.mp4"
    video.write_bytes(b"mp4")
    memory_backend.responses["FROM content_media WHERE id"] = {
        "id": 42, "status": "done", "path": str(video), "media_token": "tok",
        "media_token_expires_at": datetime.now(UTC) + timedelta(hours=3)}
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_ID", "page-1")
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_TOKEN", "ptok")
    monkeypatch.delenv("POSTIZ_API_KEY", raising=False)
    return memory_backend


@pytest.fixture
def graph(monkeypatch):
    """Instagram container + publish and Facebook /videos; counts IG posts."""
    ig_posts: list[str] = []

    def handler(request):
        path = request.url.path
        if path.endswith("/sid/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.method == "GET" and path.endswith("/container-1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if path.endswith("/sid/media_publish"):
            ig_posts.append(path)
            return httpx.Response(200, json={"id": f"ig-{len(ig_posts)}"})
        if path.endswith("/page-1/videos"):
            return httpx.Response(200, json={"id": "fb-1"})
        return httpx.Response(500)

    real = httpx.AsyncClient

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return ig_posts


def _events(backend, event: str) -> list[dict]:
    out = []
    for row in backend.audit_rows:
        if row.event == event:
            out.append(json.loads(row.payload)["data"] | {"subject": row.subject})
    return out


# ------------------------------------------------------------ the limiter

async def test_the_third_instagram_post_of_the_hour_is_refused(media, registered,
                                                              graph):
    first = await pr.publish_reel(42, CAPTION)
    second = await pr.publish_reel(42, CAPTION)
    third = await pr.publish_reel(42, CAPTION)
    assert "Instagram (stories): posted" in first.detail
    assert "Instagram (stories): posted" in second.detail
    assert "Instagram (stories): refused" in third.detail
    assert "max_per_hour" in third.detail
    assert len(graph) == 2                               # never a third IG post
    assert third.ok and "Facebook Page: posted" in third.detail   # legs independent
    refused = _events(media, "leg.refused")
    assert refused and refused[-1]["parent"] == "content.publish_reel"


async def test_a_direct_instagram_publish_and_a_reel_share_one_window(
        media, registered, graph):
    """The executor's own runs of instagram.publish count against the leg."""
    executor._record_run("instagram.publish")
    executor._record_run("instagram.publish")
    result = await pr.publish_reel(42, CAPTION)
    assert "Instagram (stories): refused" in result.detail and graph == []


async def test_a_refused_leg_spends_nothing(media, registered, graph,
                                            monkeypatch):
    before = len(executor._recent["instagram.publish"])
    monkeypatch.setitem(REGISTRY.decisions, "instagram.publish",
                        Decision(allowed=False, tier=registered.tier,
                                 reason="test: refused"))
    result = await pr.publish_reel(42, CAPTION)
    assert "Instagram (stories): refused" in result.detail
    assert len(executor._recent["instagram.publish"]) == before


async def test_a_posted_leg_spends_its_action_and_module_windows_not_global(
        media, registered, graph):
    await pr.publish_reel(42, CAPTION)
    assert len(executor._recent["instagram.publish"]) == 1
    assert len(executor._recent["module:instagram"]) == 1
    assert len(executor._recent["global:"]) == 0      # the parent run spent that


# ------------------------------------------------------------ the other gates

async def test_a_paused_instagram_module_stops_the_leg(media, registered, graph):
    media.responses["FROM pauses WHERE resumed_at IS NULL AND scope"] = {
        "scope": "instagram", "paused_at": datetime.now(UTC),
        "paused_by": "slack:U1", "reason": "token rotation"}
    result = await pr.publish_reel(42, CAPTION)
    assert "Instagram (stories): refused" in result.detail
    assert "paused" in result.detail and graph == []


async def test_the_leg_asks_about_the_stories_account_it_posts_as(
        media, registered, graph, monkeypatch):
    """R09 per account: the reel's Instagram leg posts as `stories`, so the
    pause check is asked for that account -- an `instagram:stories` pause
    (a refused stories login) stops it, an `instagram:clips` one does not."""
    from atlas.kernel import pause
    asked: list = []

    async def only_stories(module, account=None):
        asked.append((module, account))
        if account == "stories":
            return pause.Pause("instagram:stories", datetime.now(UTC),
                               "atlas:auth-refused", "login refused")
        return None

    monkeypatch.setattr(pause, "active_for", only_stories)
    result = await pr.publish_reel(42, CAPTION)
    assert asked == [("instagram", "stories")]
    assert "stories account is paused" in result.detail and graph == []


async def test_a_switched_off_instagram_module_means_no_leg(media, graph,
                                                           monkeypatch):
    monkeypatch.delitem(REGISTRY.actions, "instagram.publish", raising=False)
    monkeypatch.delitem(REGISTRY.decisions, "instagram.publish", raising=False)
    result = await pr.publish_reel(42, CAPTION)
    assert "not registered" in result.detail and graph == []


async def test_only_a_parent_listed_in_code_may_run_the_leg(media, registered,
                                                            graph):
    status, said, _ = await pr._instagram(CAPTION, "https://x/v.mp4",
                                          parent="tools.invoke", media_id=42)
    assert status == pr.REFUSED and "not a parent" in said and graph == []
    assert legs.PARENTS["instagram.publish"] == {
        "content.publish_reel", "content.autopublish_reel",
        "content.post_missing_leg", "clips.publish_promo"}


async def test_each_attempt_is_a_leg_in_the_audit_chain(media, registered, graph):
    await pr.publish_reel(42, CAPTION)
    start, = _events(media, "leg.start")
    finish, = _events(media, "leg.finish")
    assert start["subject"] == finish["subject"] == "instagram.publish"
    assert start["parent"] == finish["parent"] == "content.publish_reel"
    assert finish["ok"] is True and start["media_id"] == 42


async def test_autopublish_names_itself_as_the_parent(media, registered, graph,
                                                      monkeypatch):
    async def passes(media_id, caption):
        return None

    monkeypatch.setattr(pr, "autopublish_check", passes)
    result = await pr.autopublish_reel(42, CAPTION)
    assert result.ok
    start, = _events(media, "leg.start")
    assert start["parent"] == "content.autopublish_reel"


# ------------------------------------------------------------ the owner's flow

async def test_autopublish_holds_a_reel_whose_instagram_hour_is_spent(
        memory_backend, registered, monkeypatch):
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)
    memory_backend.responses["FROM reel_posts WHERE at >="] = {"n": 0}
    executor._record_run("instagram.publish")
    executor._record_run("instagram.publish")
    why = await pr.autopublish_check(42, CAPTION)
    assert why and pr.RATE_HELD in why


@pytest.mark.parametrize("detail", [
    (f"not posted automatically: Instagram's {pr.RATE_HELD}: instagram.publish "
     "has already run 2 time(s) this hour"),
    ("content.autopublish_reel has already run 2 times this hour; escalating "
     "instead of repeating"),
])
async def test_an_hourly_hold_waits_never_a_remake_or_a_card(monkeypatch, detail):
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)

    async def execute(req):
        return ActionResult(False, detail)

    async def never(*a, **k):
        raise AssertionError("no card and no remake for a count")

    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "propose", never)
    monkeypatch.setattr(day, "preview", never)
    for on_fail in ("redo", "skip", "card"):
        got, pid, said = await day.post_or_card(
            {"media_id": 7, "caption": CAPTION}, "Reel 7.", series=False, dedup="reel:7",
            media_id=7, rollback_plan="deletes", on_fail=on_fail)
        assert (got, pid) == ("wait", None) and said == detail


# ------------------------------------------------------------ RA-B7: once per render

async def test_a_reel_already_posted_is_not_posted_again(media, registered, graph):
    media.responses["FROM reel_posts WHERE media_id = $1 ORDER BY at LIMIT 1"] = {
        "platform": "instagram", "post_ref": "ig-1"}
    result = await pr.publish_reel(42, CAPTION)
    assert not result.ok and "already posted" in result.detail
    assert graph == [] and _events(media, "leg.start") == []


async def test_an_unreadable_posted_record_posts_nothing(media, registered, graph):
    async def broken(sql, *args):
        if "FROM reel_posts WHERE media_id" in " ".join(sql.split()):
            raise ConnectionError("db down")
        return media._canned(sql)

    media.fetchrow = broken
    result = await pr.publish_reel(42, CAPTION)
    assert not result.ok and "could not be read" in result.detail and graph == []


async def test_an_already_posted_render_is_done_never_remade_or_carded(
        memory_backend, monkeypatch):
    """The autopublish check and the card's dry run both see it, and the
    pipeline treats it as posted: no paid remake, no card, no receipt."""
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)
    memory_backend.responses[
        "FROM reel_posts WHERE media_id = $1 ORDER BY at LIMIT 1"] = {
        "platform": "tiktok", "post_ref": "pz-1"}
    why = await pr.autopublish_check(42, CAPTION)
    assert why and pr.ALREADY_POSTED in why
    memory_backend.responses["FROM content_media WHERE id"] = {
        "id": 42, "status": "done", "path": __file__, "media_token": "t",
        "media_token_expires_at": datetime.now(UTC) + timedelta(hours=1)}
    dry = await pr.publish_reel_dry(42, CAPTION)
    assert not dry.ok and pr.ALREADY_POSTED in dry.detail

    async def execute(req):
        return ActionResult(False, f"dry run refused: not posted automatically: {why}")

    async def never(*a, **k):
        raise AssertionError("no card, no remake, no receipt")

    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "propose", never)
    monkeypatch.setattr(day, "preview", never)
    monkeypatch.setattr(day, "notify", never)
    for on_fail in ("redo", "skip", "card"):
        got, pid, _ = await day.post_or_card(
            {"media_id": 42, "caption": CAPTION}, "Reel 42.", series=False, dedup="reel:42",
            media_id=42, rollback_plan="deletes", on_fail=on_fail)
        assert (got, pid) == ("posted", None)


async def test_with_autopublish_off_an_already_posted_render_is_not_carded(
        monkeypatch):
    monkeypatch.setenv("REELS_AUTOPUBLISH", "off")

    async def preview(action, params):
        return ActionResult(False, f"reel 42 {pr.ALREADY_POSTED} (instagram x)")

    async def never(*a, **k):
        raise AssertionError("no card for a posted reel")

    monkeypatch.setattr(day, "preview", preview)
    monkeypatch.setattr(day, "propose", never)
    got, pid, _ = await day.post_or_card(
        {"media_id": 42, "caption": CAPTION}, "Reel 42.", series=False, dedup="reel:42",
        media_id=42, rollback_plan="deletes")
    assert (got, pid) == ("posted", None)
