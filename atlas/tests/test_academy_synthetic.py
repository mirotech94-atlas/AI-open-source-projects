"""academy.flag_synthetic_short (the owner, 2026-10-17: "yes add the AI flag for
Shorts too"): status.containsSyntheticMedia on the Academy's own Shorts,
which go out through Postiz and so cannot carry it on upload."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import ActionResult, Tier
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules.academy import synthetic as syn
from atlas.modules.youtube import api

VID = "iRyhxkLk-Yo"
AT = datetime(2026, 10, 16, 23, 5, tzinfo=UTC)
SLUG = "tutorial:claude:1:payoff"
WITH_ID = ("render 7: TikTok @example_handle: posted — Postiz post tt1, TikTok id "
           "v_pub~1, public (DIRECT_POST) · YouTube clips (Short): posted — Postiz "
           f"post yt1, YouTube id {VID}, a public Short at "
           f"https://www.youtube.com/shorts/{VID}")
PENDING = ("render 7: YouTube clips (Short): posted, id pending — Postiz post yt1, a "
           "public Short; YouTube returned no id yet")
STATUS = {"privacyStatus": "public", "license": "youtube", "embeddable": True,
          "publicStatsViewable": True, "selfDeclaredMadeForKids": False}


@pytest.fixture(autouse=True)
def _fresh_memory():
    syn._flagged.clear()
    yield
    syn._flagged.clear()


def _row(slug=SLUG, detail=WITH_ID, at=AT):
    return {"slug": slug, "media_id": 7, "detail": detail, "at": at}


@pytest.fixture
def yt(monkeypatch):
    """The status read and the PUT, faked below api.set_synthetic so the
    real field-preserving body is what is checked."""
    st = {"status": dict(STATUS), "read_fails": False, "puts": [], "gets": 0}

    async def fake_token(channel):
        return "tok", None

    async def fake_get(url, **kw):
        st["gets"] += 1
        if st["read_fails"]:
            return None, "HTTP 503"
        return {"items": [{"id": VID, "status": st["status"]}]}, None

    async def fake_put(url, **kw):
        st["puts"].append(kw)
        st["status"] = dict(kw["json_body"]["status"])
        return {"id": VID}, None
    monkeypatch.setattr(api, "token", fake_token)
    monkeypatch.setattr(api, "get_json", fake_get)
    monkeypatch.setattr(api, "_put_json", fake_put)

    async def slug_rows(slug):
        return [st.get("row", _row())]
    monkeypatch.setattr(syn, "_slug_rows", slug_rows)
    return st


# ------------------------------------------------------------- the id lookup

def test_the_scope_is_the_academy_lane_only():
    assert syn.in_scope("tutorial:n8n:2:quiz")
    assert syn.in_scope("lesson:sell/1.2") and syn.in_scope("concept:reply-ready")
    assert not syn.in_scope("ai-for-every-age")        # a store promo reel
    assert not syn.in_scope("") and not syn.in_scope("render:9")


async def test_the_id_comes_from_the_recorded_youtube_leg():
    vid, why = await syn.video_id_of(_row(), posts=[])
    assert (vid, why) == (VID, None)


async def test_a_tiktok_id_is_never_taken_for_the_youtube_one():
    detail = ("render 7: TikTok @example_handle: posted — Postiz post tt1, TikTok id "
              "AAAAAAAAAAA, public · YouTube clips (Short): refused — 401")
    vid, why = await syn.video_id_of(_row(detail=detail), posts=[])
    assert vid is None and why


async def test_a_pending_id_is_read_from_postiz_by_its_post_id():
    posts = [{"id": "tt1", "releaseId": "x", "releaseURL": "https://www.tiktok.com/@a",
              "integration": {"providerIdentifier": "tiktok-business"}},
             {"id": "yt1", "releaseId": VID,
              "releaseURL": f"https://www.youtube.com/watch?v={VID}",
              "integration": {"providerIdentifier": "youtube"}}]
    vid, why = await syn.video_id_of(_row(detail=PENDING), posts=posts)
    assert (vid, why) == (VID, None)


async def test_an_id_postiz_does_not_have_yet_is_unknown_never_guessed():
    posts = [{"id": "yt1", "releaseId": "", "releaseURL": "",
              "integration": {"providerIdentifier": "youtube"}}]
    vid, why = await syn.video_id_of(_row(detail=PENDING), posts=posts)
    assert vid is None and "no id yet" in why
    mismatch = [{"id": "yt1", "releaseId": VID,
                 "releaseURL": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
                 "integration": {"providerIdentifier": "youtube"}}]
    vid, _ = await syn.video_id_of(_row(detail=PENDING), posts=mismatch)
    assert vid is None


async def test_an_unreadable_postiz_is_said(monkeypatch):
    from atlas.modules.content import postiz

    async def boom(start, end):
        raise postiz.PostizError("unreachable")
    monkeypatch.setattr(postiz, "posts_between", boom)
    vid, why = await syn.video_id_of(_row(detail=PENDING))
    assert vid is None and "Postiz could not be read" in why


# ---------------------------------------------------------- the action itself

def test_the_action_is_tier_1_with_a_dry_run_rollback_and_an_hourly_cap():
    from atlas.modules.academy import MODULE
    action = next(a for a in MODULE.actions() if a.name == syn.ACTION)
    assert action.tier is Tier.AUTO
    assert action.dry_run is not None and action.rollback is not None
    assert action.max_per_hour == syn.MAX_PER_HOUR
    assert set(action.params) == {"slug", "video_id"}
    assert action.vet({"slug": SLUG, "video_id": VID}) is None
    assert "scope" in action.vet({"slug": "ai-for-every-age", "video_id": VID})
    assert "video id" in action.vet({"slug": SLUG, "video_id": "x; drop"})


async def test_the_dry_run_reads_the_status_and_says_what_would_change(yt):
    r = await syn.flag_dry(SLUG, VID)
    assert r.ok and "false -> true" in r.detail and "public" in r.detail
    assert yt["puts"] == []


async def test_the_dry_run_is_unknown_when_the_status_cannot_be_read(yt):
    yt["read_fails"] = True
    r = await syn.flag_dry(SLUG, VID)
    assert not r.ok and "UNKNOWN" in r.detail and yt["puts"] == []


async def test_a_video_not_recorded_for_the_slug_is_refused(yt):
    yt["row"] = _row(detail=WITH_ID.replace(VID, "bbbbbbbbbbb"))
    r = await syn.flag(SLUG, VID)
    assert not r.ok and "not the Short recorded" in r.detail and yt["puts"] == []


async def test_the_run_sets_the_flag_and_keeps_every_other_status_field(yt):
    r = await syn.flag(SLUG, VID)
    assert r.ok, r.detail
    body = yt["puts"][-1]["json_body"]
    assert body == {"id": VID, "status": {**STATUS, "containsSyntheticMedia": True}}
    assert json.loads(r.rollback_token) == {"video_id": VID, "previous": False}


async def test_an_already_flagged_video_is_skipped_without_a_write(yt):
    yt["status"]["containsSyntheticMedia"] = True
    r = await syn.flag(SLUG, VID)
    assert r.ok and "already" in r.detail and yt["puts"] == []
    dry = await syn.flag_dry(SLUG, VID)
    assert dry.ok and "nothing would change" in dry.detail


async def test_the_rollback_sets_the_flag_back_and_keeps_the_rest(yt):
    r = await syn.flag(SLUG, VID)
    undo = await syn.unflag(r.rollback_token)
    assert undo.ok, undo.detail
    body = yt["puts"][-1]["json_body"]
    assert body == {"id": VID, "status": {**STATUS, "containsSyntheticMedia": False}}


# ------------------------------------------------- the executor path (inv. 1)

@pytest.fixture
def registered(monkeypatch, memory_backend):
    from atlas.modules.academy import MODULE
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    action = next(a for a in MODULE.actions() if a.name == syn.ACTION)
    REGISTRY.actions[syn.ACTION] = action
    REGISTRY.decisions[syn.ACTION] = classify(action)
    executor._recent.clear()
    yield action
    REGISTRY.actions.pop(syn.ACTION, None)
    REGISTRY.decisions.pop(syn.ACTION, None)
    executor._recent.clear()


async def test_the_executor_runs_it_unattended_and_rolls_it_back(yt, registered):
    result = await execute(ExecutionRequest(syn.ACTION, {"slug": SLUG, "video_id": VID},
                                            "catch-up"))
    assert result.ok, result.detail
    assert yt["status"]["containsSyntheticMedia"] is True
    undo = await executor.rollback(syn.ACTION, result.rollback_token or "")
    assert undo.ok and yt["status"]["containsSyntheticMedia"] is False


async def test_the_executor_stops_it_past_the_hourly_cap(yt, registered):
    for _ in range(syn.MAX_PER_HOUR):
        yt["status"].pop("containsSyntheticMedia", None)
        assert (await execute(ExecutionRequest(
            syn.ACTION, {"slug": SLUG, "video_id": VID}, "x"))).ok
    over = await execute(ExecutionRequest(syn.ACTION, {"slug": SLUG, "video_id": VID},
                                          "x"))
    assert not over.ok


def test_only_the_action_writes():
    """Invariant 1: the pass reads, then asks the executor; it never calls the
    write itself."""
    import inspect
    src = inspect.getsource(syn.flag_pass)
    assert "set_synthetic" not in src and "execute(ExecutionRequest(" in src


# ----------------------------------------------------------- the daily pass

@pytest.fixture
def pass_(monkeypatch, yt):
    st = {"rows": [_row()], "seen": [], "rows_fail": False}

    async def rows(since):
        if st["rows_fail"]:
            raise RuntimeError("db")
        return st["rows"]
    monkeypatch.setattr(syn, "_recent_rows", rows)

    async def posts(start, end):
        return []
    from atlas.modules.content import postiz
    monkeypatch.setattr(postiz, "posts_between", posts)

    async def fake_execute(req):
        st["seen"].append((req.action, req.params))
        return ActionResult(True, "flagged")
    monkeypatch.setattr(executor, "execute", fake_execute)
    return st


async def test_the_pass_reads_first_and_flags_only_what_lacks_the_flag(pass_, yt):
    pass_["rows"] = [_row(), _row(slug="ai-for-every-age")]
    said = await syn.flag_pass()
    assert pass_["seen"] == [(syn.ACTION, {"slug": SLUG, "video_id": VID})]
    assert yt["puts"] == [] and "flagged" in " ".join(said)
    yt["status"]["containsSyntheticMedia"] = True
    pass_["seen"].clear()
    assert await syn.flag_pass() == [] and pass_["seen"] == []


async def test_the_pass_looks_back_fourteen_days(pass_, monkeypatch):
    got = {}

    async def rows(since):
        got["since"] = since
        return []
    monkeypatch.setattr(syn, "_recent_rows", rows)
    await syn.flag_pass()
    assert abs((datetime.now(UTC) - got["since"]) - timedelta(days=14)) < timedelta(
        minutes=1)


async def test_an_unread_status_is_unknown_and_retried_next_pass(pass_, yt):
    yt["read_fails"] = True
    said = await syn.flag_pass()
    assert pass_["seen"] == [] and "UNKNOWN" in said[0] and "next pass" in said[0]


async def test_an_unknown_id_is_skipped_and_said(pass_):
    pass_["rows"] = [_row(detail=PENDING)]
    said = await syn.flag_pass()
    assert pass_["seen"] == [] and "id UNKNOWN" in said[0]


async def test_an_unread_record_is_unknown_never_ok(pass_):
    pass_["rows_fail"] = True
    said = await syn.flag_pass()
    assert said and "UNKNOWN" in said[0] and pass_["seen"] == []


async def test_one_video_recorded_twice_is_flagged_once(pass_):
    pass_["rows"] = [_row(), _row()]
    await syn.flag_pass()
    assert len(pass_["seen"]) == 1


async def test_the_pass_holds_the_rest_past_pass_max(pass_, yt):
    pass_["rows"] = [_row(slug=f"tutorial:claude:{i}:payoff",
                          detail=WITH_ID.replace(VID, f"{i:011d}"))
                     for i in range(syn.PASS_MAX + 2)]
    said = await syn.flag_pass()
    assert len(pass_["seen"]) == syn.PASS_MAX
    assert sum("held for the next pass" in s for s in said) == 2


async def test_the_pass_after_a_post_looks_at_that_slug_only(pass_):
    pass_["rows"] = [_row(), _row(slug="tutorial:n8n:1:quiz",
                                  detail=WITH_ID.replace(VID, "bbbbbbbbbbb"))]
    await syn.flag_pass(only=SLUG)
    assert [p["slug"] for _, p in pass_["seen"]] == [SLUG]


async def test_the_hooks_never_raise(monkeypatch):
    async def boom(only=None):
        raise RuntimeError("anything")
    monkeypatch.setattr(syn, "flag_pass", boom)
    assert "failed" in await syn.after_post(SLUG)
    assert "failed" in await syn.catch_up()


# ------------------------------------------------------------ review fixes

def test_the_hourly_cap_leaves_room_for_the_post_and_a_full_catch_up():
    """after_post (one) and catch_up (PASS_MAX) run in the same tick."""
    from atlas.modules.academy import MODULE
    action = next(a for a in MODULE.actions() if a.name == syn.ACTION)
    assert action.max_per_hour == syn.PASS_MAX + 1


async def test_a_tick_flags_after_the_post_then_catches_up_without_a_refusal(
        yt, registered, monkeypatch):
    """One teaching day with a backlog: after_post acts once, catch_up four
    times; nothing is refused by the executor's cap, and the video just
    flagged is not flagged again in the same tick."""
    statuses: dict[str, dict] = {}
    ids = [f"{i:011d}" for i in range(syn.PASS_MAX + 1)]
    rows = [_row(slug=f"tutorial:claude:{i}:payoff",
                 detail=WITH_ID.replace(VID, vid)) for i, vid in enumerate(ids)]

    async def recent(since):
        return rows

    async def slug_rows(slug):
        return [r for r in rows if r["slug"] == slug]

    async def status(channel, vid):
        return dict(statuses.get(vid, STATUS)), None   # a read that lags the write

    async def put(channel, vid, value):
        statuses[vid] = {**STATUS, "containsSyntheticMedia": value}
        return False, None
    monkeypatch.setattr(syn, "_recent_rows", recent)
    monkeypatch.setattr(syn, "_slug_rows", slug_rows)
    monkeypatch.setattr(api, "video_status", status)
    monkeypatch.setattr(api, "set_synthetic", put)
    first = await syn.after_post(rows[0]["slug"])
    statuses.pop(ids[0])                                # the lagging read
    rest = await syn.catch_up()
    assert "flagged" in first and "not flagged" not in first
    assert "not flagged" not in rest and rest.count(": flagged") == syn.PASS_MAX
    assert ids[0] not in rest


async def test_an_older_row_with_the_youtube_leg_still_proves_the_id(yt, monkeypatch):
    """A render re-run for one leg records a newer row with no YouTube leg
    (clips.promo.recently_posted); the id on the older row still counts."""
    from atlas.modules.academy import synthetic as mod
    rows = [_row(), _row(detail="render 7: TikTok @example_handle: posted — Postiz "
                                "post tt9, public", at=AT + timedelta(hours=2))]

    async def slug_rows(slug):
        return rows
    monkeypatch.setattr(mod, "_slug_rows", slug_rows)
    r = await syn.flag(SLUG, VID)
    assert r.ok, r.detail


async def test_a_postiz_outage_in_the_pass_is_unknown_not_a_pending_id(pass_,
                                                                       monkeypatch):
    from atlas.modules.content import postiz

    async def boom(start, end):
        raise postiz.PostizError("unreachable")
    monkeypatch.setattr(postiz, "posts_between", boom)
    pass_["rows"] = [_row(detail=PENDING)]
    said = await syn.flag_pass()
    assert pass_["seen"] == []
    assert "UNKNOWN" in said[0] and "Postiz could not be read" in said[0]
    assert "no id yet" not in said[0]
