"""The one "Hands-On Labs & Live Builds" playlist (Miro, 2026-10-22): every
lab / live build stays in its course playlist and is also added, in course
order, to one playlist whose description says which lab is whose."""
from __future__ import annotations

import json

import pytest

from atlas.kernel.contracts import ActionResult, FailureClass, Tier
from atlas.kernel.permissions import denial_reason
from atlas.modules.academy import lab_playlist as lp
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.youtube import api

# ------------------------------------------------------------------ the list


def test_every_lab_is_a_real_episode_and_no_explainer():
    assert ts.LABS, "no labs"
    assert len(set(ts.LABS)) == len(ts.LABS)
    for key, n in ts.LABS:
        got = tut.resolve(key, n)
        assert not isinstance(got, str), got
        assert (key, n) not in ts.EXPLAINERS
        assert key in ts.LAB_COURSE_ORDER


def test_the_labs_are_exactly_the_reviewed_list_in_course_order():
    want = ([("claude", n) for n in range(1, 7)] + [("n8n", n) for n in range(1, 9)]
            + [("project", n) for n in range(2, 8)]
            + [("business", n) for n in range(1, 7)]
            + [("local", n) for n in (2, 4, 6, 7, 8, 9)] + [("security", 1)])
    assert list(ts.LABS) == want
    assert ts.LAB_COURSE_ORDER == ("claude", "n8n", "project", "business", "local",
                                   "security")
    assert list(ts.LABS) == sorted(ts.LABS, key=lp.lab_key)
    assert set(ts.LAB_SHORT_NAMES) == set(ts.LABS)


def test_is_lab():
    assert ts.is_lab("claude", 1) and ts.is_lab("security", 1)
    assert not ts.is_lab("claude", 0) and not ts.is_lab("project", 1)
    assert not ts.is_lab("local", 1) and not ts.is_lab("agents", 1)
    assert not ts.is_lab("nope", 1)


def test_the_description_groups_every_lab_under_its_course():
    text = ts.lab_playlist_description()
    assert len(text) < 5000
    lines = text.splitlines()
    for key in ts.LAB_COURSE_ORDER:
        line = next(x for x in lines if x.startswith(ts.PLAYLISTS[key]))
        for k, n in ts.LABS:
            if k == key:
                assert f"#{n} {ts.LAB_SHORT_NAMES[(k, n)]}" in line
    # course order is the order of the lines
    at = [next(i for i, x in enumerate(lines) if x.startswith(ts.PLAYLISTS[k]))
          for k in ts.LAB_COURSE_ORDER]
    assert at == sorted(at)


# ---------------------------------------------------------------- position

def test_position_keeps_course_order():
    pos = lp.insert_position
    assert pos(("claude", 3), []) == 0
    assert pos(("claude", 3), [("claude", 1), ("claude", 2), ("n8n", 1)]) == 2
    assert pos(("n8n", 1), [("claude", 1), ("claude", 2)]) == 2
    assert pos(("claude", 1), [("claude", 2), ("n8n", 1)]) == 0
    assert pos(("security", 1), [("claude", 1), ("local", 9)]) == 2


def test_unknown_items_count_as_last():
    pos = lp.insert_position
    assert pos(("claude", 1), [None, ("claude", 2)]) == 0
    assert pos(("claude", 3), [None, ("claude", 2)]) == 2
    assert pos(("claude", 3), [("claude", 2), None]) == 1
    assert pos(("security", 1), [("claude", 1), None, None]) == 1


# --------------------------------------------------------------- the api

async def test_list_playlist_items_pages_in_order(monkeypatch):
    pages = {
        None: {"items": [{"id": "I2", "snippet": {"position": 1},
                          "contentDetails": {"videoId": "bbbbbbbbbbb"}}],
               "nextPageToken": "p2"},
        "p2": {"items": [{"id": "I1", "snippet": {"position": 0},
                          "contentDetails": {"videoId": "aaaaaaaaaaa"}}]},
    }
    seen = []

    async def token(channel):
        return "tok", None

    async def get_json(url, *, headers=None, params=None, **k):
        seen.append((url, dict(params)))
        return pages[params.get("pageToken")], None
    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "get_json", get_json)
    items, why = await api.list_playlist_items("clips", "PLlab")
    assert why is None
    assert items == [{"item_id": "I1", "video_id": "aaaaaaaaaaa", "position": 0},
                     {"item_id": "I2", "video_id": "bbbbbbbbbbb", "position": 1}]
    assert seen[0][0].endswith("/playlistItems")
    assert seen[0][1]["playlistId"] == "PLlab"


async def test_list_playlist_items_reports_a_failure(monkeypatch):
    async def token(channel):
        return "tok", None

    async def get_json(url, **k):
        return None, "403 forbidden"
    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "get_json", get_json)
    items, why = await api.list_playlist_items("clips", "PL")
    assert items is None and "403" in why


async def test_add_playlist_item_takes_a_position(monkeypatch):
    bodies = []

    async def token(channel):
        return "tok", None

    async def post_json(url, *, json_body=None, **k):
        bodies.append(json_body)
        return {"id": "IT"}, None
    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "post_json", post_json)
    await api.add_playlist_item("clips", "PL", "abcdefghijk", position=3)
    await api.add_playlist_item("clips", "PL", "abcdefghijk")
    assert bodies[0]["snippet"]["position"] == 3
    assert "position" not in bodies[1]["snippet"]


# ---------------------------------------------------------------- the action

def _actions():
    from atlas.modules.academy import AcademyModule
    return {a.name: a for a in AcademyModule().actions()}


def test_the_action_is_tier_1_with_dry_run_and_rollback():
    a = _actions()[lp.ACTION]
    assert a.tier is Tier.AUTO
    assert a.dry_run is not None and a.rollback is not None
    assert denial_reason(lp.ACTION) is None
    assert set(a.params) == {"series", "episode"}
    assert a.max_per_hour == lp.MAX_PER_HOUR
    assert a.vet({"series": "claude", "episode": 1}) is None
    assert "not a lab" in a.vet({"series": "claude", "episode": 0})
    assert "no tutorial series" in a.vet({"series": "crypto", "episode": 1})


VIDS = {("claude", 1): "claude1vidA", ("claude", 2): "claude2vidB",
        ("n8n", 1): "n8n1videoCC", ("claude", 0): "claude0vidD"}


@pytest.fixture
def yt(monkeypatch, tmp_path):
    """A fake channel + record. state["rows"] are academy_tutorial_posts."""
    monkeypatch.setattr(tut, "root", lambda: tmp_path)
    state: dict = {"steps": [], "playlist": {"id": "PLlab", "privacy": "public"},
                   "items": [], "scopes": {api.PLAYLIST_SCOPES[0]},
                   "rows": [{"id": i, "series": k[0], "episode": k[1], "video_id": v,
                             "public": True, "withdrawn": False}
                            for i, (k, v) in enumerate(VIDS.items(), 1)]}

    def live(r):
        return r["public"] and not r["withdrawn"] and r["video_id"]

    async def fetch_one(sql, *args):
        assert "academy_tutorial_posts" in sql
        rows = [r for r in state["rows"] if live(r)
                and (r["series"], r["episode"]) == (args[0], args[1])]
        return max(rows, key=lambda r: r["id"]) if rows else None

    async def fetch_all(sql, *args):
        assert "academy_tutorial_posts" in sql
        return sorted(state["rows"], key=lambda r: -r["id"])

    async def find_playlist(ch, title):
        state["steps"].append(("find", title))
        return state["playlist"], None

    async def create_playlist(ch, title, description, privacy="public"):
        state["steps"].append(("create", title, privacy))
        state["description"] = description
        state["playlist"] = {"id": "PLnew", "privacy": privacy}
        return state["playlist"], None

    async def list_playlist_items(ch, pl):
        state["steps"].append(("list", pl))
        return [dict(i) for i in state["items"]], None

    async def add_playlist_item(ch, pl, vid, position=None):
        state["steps"].append(("add", pl, vid, position))
        if state.get("manual_sort") and position is not None:
            return None, "HTTP 400 from /youtube/v3/playlistItems"
        return {"item_id": f"IT-{vid}", "playlist_id": pl, "video_id": vid}, None

    async def take_out_of_playlist(ch, item_id):
        state["steps"].append(("take_out", item_id))
        return True, None

    async def set_playlist_privacy(*a):
        state["steps"].append(("privacy",) + a)
        return {}, None

    async def token_scopes(ch):
        return state["scopes"], None

    for name, fn in (("find_playlist", find_playlist), ("create_playlist", create_playlist),
                     ("list_playlist_items", list_playlist_items),
                     ("add_playlist_item", add_playlist_item),
                     ("take_out_of_playlist", take_out_of_playlist),
                     ("set_playlist_privacy", set_playlist_privacy),
                     ("token_scopes", token_scopes)):
        monkeypatch.setattr(api, name, fn)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    return state


def _writes(state):
    return [s for s in state["steps"] if s[0] in ("create", "add", "take_out", "privacy")]


async def test_dry_run_says_what_it_would_do_and_writes_nothing(yt):
    r = await lp.add_to_lab_playlist_dry("claude", 1)
    assert r.ok and "claude1vidA" in r.detail and ts.LAB_PLAYLIST in r.detail
    assert _writes(yt) == []


async def test_dry_run_refuses_a_non_lab_and_an_unposted_lab(yt):
    assert not (await lp.add_to_lab_playlist_dry("claude", 0)).ok
    r = await lp.add_to_lab_playlist_dry("n8n", 2)
    assert not r.ok and "public" in r.detail
    assert _writes(yt) == []


async def test_run_inserts_at_the_course_order_place(yt):
    yt["items"] = [{"item_id": "X1", "video_id": "claude2vidB", "position": 0},
                   {"item_id": "X2", "video_id": "n8n1videoCC", "position": 1}]
    r = await lp.add_to_lab_playlist("claude", 1)
    assert r.ok, r.detail
    assert ("add", "PLlab", "claude1vidA", 0) in yt["steps"]
    assert json.loads(r.rollback_token) == {"channel": tut.channel(),
                                            "item_id": "IT-claude1vidA"}


async def test_run_puts_a_later_lab_after_the_earlier_ones(yt):
    yt["items"] = [{"item_id": "X1", "video_id": "claude1vidA", "position": 0},
                   {"item_id": "X9", "video_id": "someoneelse", "position": 1}]
    r = await lp.add_to_lab_playlist("n8n", 1)
    assert r.ok and ("add", "PLlab", "n8n1videoCC", 1) in yt["steps"]


async def test_run_skips_a_video_already_in_the_playlist(yt):
    yt["items"] = [{"item_id": "X1", "video_id": "claude1vidA", "position": 0}]
    r = await lp.add_to_lab_playlist("claude", 1)
    assert r.ok and "already" in r.detail and r.rollback_token is None
    assert _writes(yt) == []


async def test_run_makes_the_playlist_public_with_the_grouped_description(yt):
    yt["playlist"] = {}
    r = await lp.add_to_lab_playlist("claude", 2)
    assert r.ok, r.detail
    assert ("create", ts.LAB_PLAYLIST, "public") in yt["steps"]
    assert yt["description"] == ts.lab_playlist_description()
    assert ("add", "PLnew", "claude2vidB", 0) in yt["steps"]


async def test_run_never_takes_the_video_id_from_anywhere_but_the_record(yt):
    yt["rows"].append({"id": 99, "series": "claude", "episode": 1,
                       "video_id": "claude1NEWW", "public": True, "withdrawn": False})
    yt["rows"].append({"id": 100, "series": "claude", "episode": 1,
                       "video_id": "claude1GONE", "public": True, "withdrawn": True})
    r = await lp.add_to_lab_playlist("claude", 1)
    assert r.ok and ("add", "PLlab", "claude1NEWW", 0) in yt["steps"]


async def test_run_refuses_when_the_items_cannot_be_read(yt, monkeypatch):
    async def broken(ch, pl):
        return None, "500 backend"
    monkeypatch.setattr(api, "list_playlist_items", broken)
    r = await lp.add_to_lab_playlist("claude", 1)
    assert not r.ok and "500" in r.detail
    assert _writes(yt) == []


async def test_run_refuses_a_non_lab(yt):
    r = await lp.add_to_lab_playlist("claude", 0)
    assert not r.ok and _writes(yt) == []


async def test_undo_takes_out_exactly_that_item_and_keeps_the_playlist(yt):
    token = json.dumps({"channel": "clips", "item_id": "IT-1"})
    r = await lp.undo_lab_playlist(token)
    assert r.ok
    assert _writes(yt) == [("take_out", "IT-1")]
    assert not (await lp.undo_lab_playlist("not json")).ok


# ---------------------------------------------------------------- the pass

@pytest.fixture
def ran(monkeypatch, yt):
    from atlas.kernel import executor
    seen: list = []

    async def execute(req):
        seen.append((req.action, dict(req.params)))
        return ActionResult(True, "added")
    monkeypatch.setattr(executor, "execute", execute)
    yt["ran"] = seen
    return yt


async def test_the_pass_adds_only_public_labs_not_yet_in_it(ran):
    ran["rows"].append({"id": 50, "series": "n8n", "episode": 2, "video_id": "n8n2videoEE",
                        "public": False, "withdrawn": False})
    ran["rows"].append({"id": 51, "series": "business", "episode": 1,
                        "video_id": "biz1videoFF", "public": True, "withdrawn": True})
    ran["items"] = [{"item_id": "X1", "video_id": "claude2vidB", "position": 0}]
    said = await lp.lab_playlist_backfill()
    assert ran["ran"] == [(lp.ACTION, {"series": "claude", "episode": 1}),
                          (lp.ACTION, {"series": "n8n", "episode": 1})]
    assert said and all(isinstance(x, str) for x in said)


async def test_the_pass_is_capped(ran, monkeypatch):
    for n in range(2, 9):
        ran["rows"].append({"id": 60 + n, "series": "n8n", "episode": n,
                            "video_id": f"n8n{n}vid____"[:11], "public": True,
                            "withdrawn": False})
    said = await lp.lab_playlist_backfill(limit=3)
    assert len(ran["ran"]) == 3
    assert [p for _, p in ran["ran"]] == [{"series": "claude", "episode": 1},
                                          {"series": "claude", "episode": 2},
                                          {"series": "n8n", "episode": 1}]
    assert any("next pass" in x for x in said)


async def test_the_pass_tries_one_when_the_playlist_does_not_exist_yet(ran):
    ran["playlist"] = {}
    await lp.lab_playlist_backfill()
    assert len(ran["ran"]) == 1


async def test_the_pass_stops_at_the_executor_ceiling(ran, monkeypatch):
    from atlas.kernel import executor

    async def execute(req):
        ran["ran"].append(req.action)
        return ActionResult(False, "rate limited", failure_class=FailureClass.BUDGET,
                            dispatched=False)
    monkeypatch.setattr(executor, "execute", execute)
    await lp.lab_playlist_backfill()
    assert len(ran["ran"]) == 1


async def test_the_pass_without_scopes_is_unknown_and_tries_nothing(ran, monkeypatch):
    async def unread(ch):
        return None, "no refresh token"
    monkeypatch.setattr(api, "token_scopes", unread)
    said = await lp.lab_playlist_backfill()
    assert ran["ran"] == [] and "UNKNOWN" in said[0]
    ran["scopes"] = {"openid"}
    monkeypatch.setattr(api, "token_scopes", lambda ch: _scopes(ran))
    said = await lp.lab_playlist_backfill()
    assert ran["ran"] == [] and "cannot manage playlists" in said[0]


async def _scopes(state):
    return state["scopes"], None


async def test_the_pass_never_raises(ran, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    said = await lp.lab_playlist_backfill()
    assert said and "failed" in said[0]
    assert ran["ran"] == []


async def test_the_pass_holds_when_the_playlist_cannot_be_read(ran, monkeypatch):
    async def broken(ch, pl):
        return None, "500 backend"
    monkeypatch.setattr(api, "list_playlist_items", broken)
    said = await lp.lab_playlist_backfill()
    assert ran["ran"] == [] and "UNKNOWN" in said[0]


def test_the_daily_tick_runs_the_pass():
    import inspect
    assert "_lab_playlist()" in inspect.getsource(tut.daily)
    assert "lab_playlist_backfill" in inspect.getsource(tut._lab_playlist)


def test_the_action_is_on_the_tutorial_job():
    from atlas.kernel import departments
    assert lp.ACTION in departments.JOBS["academy_tutorial"].actions


# ------------------------------------------- review fixes (2026-10-22, 3108ed1e)

def _row(state, rid, key, vid, *, public=True, withdrawn=False):
    state["rows"].append({"id": rid, "series": key[0], "episode": key[1], "video_id": vid,
                          "public": public, "withdrawn": withdrawn})


def test_the_take_out_action_is_tier_1_with_dry_run_and_rollback():
    a = _actions()[lp.TAKE_OUT_ACTION]
    assert a.tier is Tier.AUTO
    assert a.dry_run is not None and a.rollback is not None
    assert denial_reason(lp.TAKE_OUT_ACTION) is None
    assert set(a.params) == {"series", "episode", "video_id"}
    ok = {"series": "claude", "episode": 1, "video_id": "abcdefghijk"}
    assert a.vet(ok) is None
    assert "11-character" in a.vet({**ok, "video_id": "x' OR 1=1"})
    assert "not a lab" in a.vet({**ok, "episode": 0})
    from atlas.kernel import departments
    assert lp.TAKE_OUT_ACTION in departments.JOBS["academy_tutorial"].actions


async def test_remove_takes_out_a_withdrawn_lab(yt):
    _row(yt, 20, ("claude", 1), "claude1OLDD", withdrawn=True)
    yt["items"] = [{"item_id": "X1", "video_id": "claude1OLDD", "position": 0}]
    dry = await lp.take_out_of_lab_playlist_dry("claude", 1, "claude1OLDD")
    assert dry.ok and _writes(yt) == []
    r = await lp.take_out_of_lab_playlist("claude", 1, "claude1OLDD")
    assert r.ok, r.detail
    assert _writes(yt) == [("take_out", "X1")]
    undo = json.loads(r.rollback_token)
    assert undo["video_id"] == "claude1OLDD" and undo["playlist_id"] == "PLlab"


async def test_remove_takes_out_a_superseded_post(yt):
    _row(yt, 1, ("n8n", 1), "n8n1OLDvidX")          # older id, still public
    yt["items"] = [{"item_id": "X1", "video_id": "n8n1OLDvidX", "position": 0}]
    r = await lp.take_out_of_lab_playlist("n8n", 1, "n8n1OLDvidX")
    assert r.ok and ("take_out", "X1") in yt["steps"]


async def test_remove_refuses_the_current_post_and_videos_not_ours(yt):
    yt["items"] = [{"item_id": "X1", "video_id": "claude1vidA", "position": 0},
                   {"item_id": "X2", "video_id": "someoneelse", "position": 1}]
    assert not (await lp.take_out_of_lab_playlist("claude", 1, "claude1vidA")).ok
    assert not (await lp.take_out_of_lab_playlist("claude", 1, "someoneelse")).ok
    # ours, but another lesson's
    _row(yt, 21, ("claude", 2), "claude2OLDD", withdrawn=True)
    assert not (await lp.take_out_of_lab_playlist("claude", 1, "claude2OLDD")).ok
    assert not (await lp.take_out_of_lab_playlist_dry("claude", 1, "someoneelse")).ok
    assert _writes(yt) == []


async def test_undo_of_a_removal_re_adds_at_the_sorted_place(yt):
    _row(yt, 20, ("claude", 2), "claude2OLDD", withdrawn=True)
    yt["items"] = [{"item_id": "X1", "video_id": "claude1vidA", "position": 0},
                   {"item_id": "X2", "video_id": "n8n1videoCC", "position": 1}]
    token = json.dumps({"channel": "clips", "playlist_id": "PLlab", "series": "claude",
                        "episode": 2, "video_id": "claude2OLDD"})
    r = await lp.undo_take_out_of_lab_playlist(token)
    assert r.ok, r.detail
    assert _writes(yt) == [("add", "PLlab", "claude2OLDD", 1)]


async def test_a_refused_position_is_appended_once_and_said(yt):
    yt["manual_sort"] = True
    r = await lp.add_to_lab_playlist("claude", 1)
    assert r.ok and "appended" in r.detail
    assert [s for s in yt["steps"] if s[0] == "add"] == [
        ("add", "PLlab", "claude1vidA", 0), ("add", "PLlab", "claude1vidA", None)]


async def test_a_playlist_made_minutes_ago_is_never_made_twice(yt, monkeypatch):
    yt["playlist"] = {}
    assert (await lp.add_to_lab_playlist("claude", 1)).ok

    async def not_listed_yet(ch, title):
        return {}, None
    monkeypatch.setattr(api, "find_playlist", not_listed_yet)
    r = await lp.add_to_lab_playlist("claude", 2)
    assert not r.ok and "not making a second" in r.detail
    assert len([s for s in yt["steps"] if s[0] == "create"]) == 1


async def test_the_pass_removes_withdrawn_and_superseded_then_adds(ran):
    _row(ran, 20, ("claude", 2), "claude2OLDD", withdrawn=True)
    _row(ran, 0, ("n8n", 1), "n8n1OLDvidX")         # older id than n8n1videoCC
    ran["items"] = [{"item_id": "X1", "video_id": "claude2OLDD", "position": 0},
                    {"item_id": "X2", "video_id": "n8n1OLDvidX", "position": 1},
                    {"item_id": "X3", "video_id": "someoneelse", "position": 2}]
    said = await lp.lab_playlist_backfill(limit=10)
    assert ran["ran"] == [
        (lp.TAKE_OUT_ACTION, {"series": "claude", "episode": 2, "video_id": "claude2OLDD"}),
        (lp.TAKE_OUT_ACTION, {"series": "n8n", "episode": 1, "video_id": "n8n1OLDvidX"}),
        (lp.ACTION, {"series": "claude", "episode": 1}),
        (lp.ACTION, {"series": "claude", "episode": 2}),
        (lp.ACTION, {"series": "n8n", "episode": 1})]
    assert said


async def test_removals_count_toward_the_cap(ran):
    _row(ran, 20, ("claude", 2), "claude2OLDD", withdrawn=True)
    ran["items"] = [{"item_id": "X1", "video_id": "claude2OLDD", "position": 0}]
    assert lp.PER_PASS == 3
    said = await lp.lab_playlist_backfill()
    assert [a for a, _ in ran["ran"]] == [lp.TAKE_OUT_ACTION, lp.ACTION, lp.ACTION]
    assert any("next pass" in x for x in said)


def test_the_pass_runs_after_the_days_posts():
    import inspect
    src = inspect.getsource(tut.daily)
    assert src.index("_post(todo)") < src.rindex("_lab_playlist()")
    assert src.count("_lab_playlist()") == 2           # the early return too
