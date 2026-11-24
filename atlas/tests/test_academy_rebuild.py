"""T5: `python -m atlas.cli rebuild-tutorials --dry-run|--apply` rebuilds the
posted-tutorial state the restored database lacks from what YouTube says
(read-only), so `lineup` does not re-post an episode and `pin_pass` does not
comment a second time under a video.

Rows are built with `at = publishedAt`, the playlist id and item id of the
series playlist; comment rows only from OUR channel's comments; a video that
maps to no (series, episode), and a private upload, is listed, not inserted;
`--dry-run` executes no INSERT; a video_id already on record is not inserted
twice; thumb_set / captions_set are never claimed. No network, no database:
the YouTube helpers and the state functions are stubbed."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas import cli
from atlas.kernel import state
from atlas.modules.academy import rebuild
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.youtube import api

OURS = "UCours0000000000000000"
STRANGER = "UCsomeone00000000000000"


def _title(key: str, n: int) -> str:
    s = ts.series(key)
    assert s is not None
    ep = s.episode(n)
    assert ep is not None
    return ep.title


def _uploaded(key: str, n: int) -> str:
    """The title as uploaded: the level formula with measured minutes."""
    s = ts.series(key)
    ep = s.episode(n) if s is not None else None
    assert ep is not None and ep.title_hook
    title = tut.final_title(ep, 287.0)
    assert title != ep.title
    return title


UPLOADS = [
    # video id, privacy, publishedAt, title
    ("vClaude0aaa", "public", "2026-10-27T07:00:00Z", _uploaded("claude", 0)),
    ("vN8n0aaaaaa", "public", "2026-10-27T08:00:00Z", _title("n8n", 0)),
    # on record already, under a title the catalog no longer has
    ("vKnownaaaaa", "public", "2026-10-26T07:00:00Z", "An old title, since renamed"),
    ("vPrivateaaa", "private", "2026-10-27T09:00:00Z", _title("n8n", 1)),
    ("vStrayaaaaa", "public", "2026-10-27T10:00:00Z", "My cat, unedited"),
]
PLAYLISTS = {
    "PL_claude": [("vClaude0aaa", "ITEM_c0"), ("vKnownaaaaa", "ITEM_c1")],
    "PL_n8n": [("vN8n0aaaaaa", "ITEM_n0"), ("vPrivateaaa", "ITEM_n1")],
}
COMMENTS = {
    "vClaude0aaa": [("cOurs1", OURS, "Quiz: ...", "2026-10-27T07:30:00Z"),
                    ("cTheirs", STRANGER, "great video", "2026-10-27T07:40:00Z")],
    "vKnownaaaaa": [("cOurs3", OURS, "(withdrawn)", "2026-10-26T08:00:00Z")],
}


@pytest.fixture
def youtube(monkeypatch):
    calls: list[str] = []

    async def own_channel_id(channel):
        calls.append("channel_id")
        return OURS, None

    async def uploads_playlist(channel):
        calls.append("uploads")
        return "UU_ours", None

    async def playlist_items(channel, playlist_id):
        calls.append(f"items:{playlist_id}")
        if playlist_id == "UU_ours":
            return [{"item_id": f"U_{v}", "video_id": v, "position": i}
                    for i, (v, *_rest) in enumerate(UPLOADS)], None
        return [{"item_id": item, "video_id": v, "position": i}
                for i, (v, item) in enumerate(PLAYLISTS.get(playlist_id, []))], None

    async def video_details(channel, ids):
        calls.append("videos")
        return [{"video_id": v, "privacy": p, "published_at": at, "title": t}
                for v, p, at, t in UPLOADS if v in ids], None

    async def find_playlist(channel, title):
        for key in ("claude", "n8n"):
            if title == ts.PLAYLISTS[key]:
                return {"id": f"PL_{key}", "privacy": "public"}, None
        return {}, None

    async def comment_threads(channel, video_id):
        calls.append(f"comments:{video_id}")
        return [{"comment_id": c, "thread_id": c, "author_channel_id": who,
                 "text": text, "published_at": at}
                for c, who, text, at in COMMENTS.get(video_id, [])], None

    for name, fn in (("own_channel_id", own_channel_id),
                     ("uploads_playlist", uploads_playlist),
                     ("playlist_items", playlist_items),
                     ("video_details", video_details),
                     ("find_playlist", find_playlist),
                     ("comment_threads", comment_threads)):
        monkeypatch.setattr(api, name, fn)
    return calls


class FakeDB:
    """The two tables as lists. Answers the reads rebuild makes and the one
    `unpinned()` makes; records every statement; honours NOT EXISTS."""

    def __init__(self) -> None:
        self.posts: list[dict] = [
            {"series": "claude", "episode": 1, "video_id": "vKnownaaaaa",
             "public": True, "withdrawn": False,
             "at": datetime(2026, 10, 26, 7, tzinfo=UTC)}]
        self.comments: list[dict] = []
        self.statements: list[str] = []

    async def fetch_all(self, sql, *args):
        self.statements.append(sql)
        if "FROM academy_tutorial_comments" in sql and "comment_id" in sql \
                and "academy_tutorial_posts" not in sql:
            return [{"comment_id": c["comment_id"]} for c in self.comments]
        if "NOT EXISTS (SELECT 1 FROM academy_tutorial_comments" in sql:   # unpinned
            commented = {c["video_id"] for c in self.comments}
            rows = [p for p in self.posts if p["public"] and not p["withdrawn"]
                    and p["video_id"] not in commented]
            return [{"series": p["series"], "episode": p["episode"], "at": p["at"]}
                    for p in sorted(rows, key=lambda p: p["at"])]
        if "FROM academy_tutorial_posts" in sql:
            return [{"video_id": p["video_id"], "series": p["series"],
                     "episode": p["episode"]} for p in self.posts]
        raise AssertionError(f"unexpected read: {sql}")

    async def fetch_one(self, sql, *args):
        self.statements.append(sql)
        raise AssertionError(f"unexpected fetch_one: {sql}")

    async def execute_sql(self, sql, *args):
        self.statements.append(sql)
        if "INSERT INTO academy_tutorial_posts" in sql:
            at, series, episode, vid, pl, item, public, detail = args
            if any(p["video_id"] == vid for p in self.posts):
                return "INSERT 0 0"
            assert isinstance(at, datetime) and at.tzinfo is not None
            self.posts.append({"at": at, "series": series, "episode": episode,
                               "video_id": vid, "playlist_id": pl, "item_id": item,
                               "public": public, "withdrawn": False,
                               "detail": detail, "sql": sql})
            return "INSERT 0 1"
        if "INSERT INTO academy_tutorial_comments" in sql:
            at, series, episode, vid, cid, withdrawn, text = args
            if any(c["comment_id"] == cid for c in self.comments):
                return "INSERT 0 0"
            self.comments.append({"at": at, "series": series, "episode": episode,
                                  "video_id": vid, "comment_id": cid,
                                  "withdrawn": withdrawn, "text": text})
            return "INSERT 0 1"
        raise AssertionError(f"unexpected statement: {sql}")


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(state, "fetch_all", fake.fetch_all)
    monkeypatch.setattr(state, "fetch_one", fake.fetch_one)
    monkeypatch.setattr(state, "execute_sql", fake.execute_sql)
    return fake


def _inserts(db: FakeDB) -> list[str]:
    return [s for s in db.statements if "INSERT" in s.upper()]


# ------------------------------------------------------------------ dry run

async def test_the_dry_run_lists_every_mapping_and_inserts_nothing(youtube, db):
    report = await rebuild.run(apply=False, channel="clips")
    assert report.ok, report.lines
    assert _inserts(db) == []
    text = "\n".join(report.lines)
    assert "vClaude0aaa" in text and "vN8n0aaaaaa" in text
    assert "read" in text and "UTC" in text               # every figure has its time
    assert {p["video_id"] for p in report.posts} == {"vClaude0aaa", "vN8n0aaaaaa"}
    assert {c["comment_id"] for c in report.comments} == {"cOurs1", "cOurs3"}
    assert [u["video_id"] for u in report.unmapped] == ["vStrayaaaaa"]
    assert [p["video_id"] for p in report.private] == ["vPrivateaaa"]
    assert "would insert 2 post row(s) and 2 comment row(s)" in text


# ------------------------------------------------------------------ apply

async def test_apply_inserts_only_the_missing_rows_built_from_youtube(youtube, db):
    report = await rebuild.run(apply=True, channel="clips")
    assert report.ok, report.lines
    new = {p["video_id"]: p for p in db.posts if "detail" in p}
    assert set(new) == {"vClaude0aaa", "vN8n0aaaaaa"}       # vKnown not twice
    c0 = new["vClaude0aaa"]
    assert c0["at"] == datetime(2026, 10, 27, 7, tzinfo=UTC)   # = publishedAt
    assert (c0["series"], c0["episode"]) == ("claude", 0)
    assert (c0["playlist_id"], c0["item_id"]) == ("PL_claude", "ITEM_c0")
    assert c0["public"] is True
    assert c0["detail"].startswith("rebuilt from YouTube read ")
    # never claimed: the INSERT does not set them, so the defaults (false) hold
    assert "thumb_set" not in c0["sql"] and "captions_set" not in c0["sql"]
    assert "WHERE NOT EXISTS" in c0["sql"]
    assert {c["comment_id"] for c in db.comments} == {"cOurs1", "cOurs3"}
    assert "cTheirs" not in {c["comment_id"] for c in db.comments}
    withdrawn = {c["comment_id"]: c["withdrawn"] for c in db.comments}
    assert withdrawn == {"cOurs1": False, "cOurs3": True}


async def test_apply_twice_inserts_nothing_the_second_time(youtube, db):
    await rebuild.run(apply=True, channel="clips")
    before = (len(db.posts), len(db.comments))
    again = await rebuild.run(apply=True, channel="clips")
    assert again.ok and again.posts == [] and again.comments == []
    assert (len(db.posts), len(db.comments)) == before


async def test_afterwards_unpinned_and_lineup_skip_what_youtube_shows(youtube, db):
    await rebuild.run(apply=True, channel="clips")
    pending = await tut.unpinned()
    # vClaude0 and vKnown carry our comment; only n8n #0 has none.
    assert [(r["series"], r["episode"]) for r in pending] == [("n8n", 0)]
    done = {(p["series"], p["episode"]) for p in db.posts}
    todo, _notes = tut.lineup(done, {})
    assert ("claude", 0) not in todo and ("n8n", 0) not in todo


# ------------------------------------------------------------------ refusals

async def test_a_failed_youtube_read_inserts_nothing(youtube, db, monkeypatch):
    async def broken(channel, playlist_id):
        return [], "clips: HTTP 403"
    monkeypatch.setattr(api, "playlist_items", broken)
    report = await rebuild.run(apply=True, channel="clips")
    assert not report.ok
    assert _inserts(db) == []
    assert any("403" in line for line in report.lines)


async def test_an_unknown_channel_id_inserts_no_comment(youtube, db, monkeypatch):
    async def unknown(channel):
        return None, "clips: HTTP 500"
    monkeypatch.setattr(api, "own_channel_id", unknown)
    report = await rebuild.run(apply=True, channel="clips")
    assert db.comments == []
    assert any("UNKNOWN" in line for line in report.lines)


async def test_an_unreadable_database_inserts_nothing(youtube, monkeypatch):
    async def down(sql, *a):
        raise ConnectionError("db down")
    ran: list = []

    async def execute_sql(sql, *a):
        ran.append(sql)
    monkeypatch.setattr(state, "fetch_all", down)
    monkeypatch.setattr(state, "execute_sql", execute_sql)
    report = await rebuild.run(apply=True, channel="clips")
    assert not report.ok and ran == []


# ------------------------------------------------------------------ helpers

async def test_the_helpers_parse_youtube_payloads(monkeypatch):
    """The GET-only helpers against fixture payloads shaped as YouTube
    answers them; every request is a GET (get_json), none a write."""
    seen: list[tuple[str, dict]] = []
    pages = {
        "channels": {"items": [{"id": OURS, "contentDetails": {
            "relatedPlaylists": {"uploads": "UU_ours"}}}]},
        "playlistItems": {"items": [{"id": "ITEM_1", "snippet": {"position": 0},
                                     "contentDetails": {"videoId": "vClaude0aaa"}}]},
        "videos": {"items": [{"id": "vClaude0aaa",
                              "snippet": {"title": "T",
                                          "publishedAt": "2026-10-27T07:00:00Z"},
                              "status": {"privacyStatus": "public"}}]},
        "commentThreads": {"items": [{"id": "cOurs1", "snippet": {"topLevelComment": {
            "id": "cOurs1", "snippet": {"authorChannelId": {"value": OURS},
                                        "textOriginal": "Quiz",
                                        "publishedAt": "2026-10-27T07:30:00Z"}}}}]},
    }

    async def token(channel):
        return "tok", None

    async def get_json(url, **kw):
        seen.append((url, kw.get("params") or {}))
        return pages[url.rsplit("/", 1)[-1]], None

    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "get_json", get_json)
    assert await api.uploads_playlist("clips") == ("UU_ours", None)
    assert await api.own_channel_id("clips") == (OURS, None)
    items, why = await api.playlist_items("clips", "UU_ours")
    assert why is None and items[0]["video_id"] == "vClaude0aaa"
    assert items[0]["item_id"] == "ITEM_1"
    vids, why = await api.video_details("clips", ["vClaude0aaa"])
    assert why is None and vids == [{"video_id": "vClaude0aaa", "title": "T",
                                     "privacy": "public",
                                     "published_at": "2026-10-27T07:00:00Z"}]
    comments, why = await api.comment_threads("clips", "vClaude0aaa")
    assert why is None and comments[0]["author_channel_id"] == OURS
    assert comments[0]["comment_id"] == "cOurs1" and comments[0]["text"] == "Quiz"
    assert {u.rsplit("/", 1)[-1] for u, _ in seen} == set(pages)
    mine = [p for u, p in seen if u.endswith("/channels")]
    assert all(p.get("mine") == "true" for p in mine)


async def test_a_capped_playlist_read_is_an_error_not_a_short_list(monkeypatch):
    async def token(channel):
        return "tok", None

    async def get_json(url, **kw):
        return {"items": [{"id": "I", "snippet": {"position": 0},
                           "contentDetails": {"videoId": "v"}}],
                "nextPageToken": "more"}, None
    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "get_json", get_json)
    items, why = await api.playlist_items("clips", "UU_ours")
    assert items == [] and why is not None


# ------------------------------------------------------------------ cli

@pytest.mark.parametrize("argv", [[], ["--dry-run", "--apply"], ["--force"]])
async def test_the_cli_needs_exactly_one_mode(monkeypatch, argv, capsys):
    monkeypatch.setattr("sys.argv", ["atlas.cli", "rebuild-tutorials", *argv])
    with pytest.raises(SystemExit) as stop:
        await cli.rebuild_tutorials()
    assert stop.value.code == 1


async def test_the_cli_dry_run_prints_the_report(monkeypatch, youtube, db, capsys):
    monkeypatch.setattr("sys.argv", ["atlas.cli", "rebuild-tutorials", "--dry-run"])
    await cli.rebuild_tutorials()
    out = capsys.readouterr().out
    assert "vClaude0aaa" in out and "would insert" in out
    assert _inserts(db) == []
    assert cli.COMMANDS["rebuild-tutorials"] is cli.rebuild_tutorials
