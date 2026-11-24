"""A TikTok link handed in by the Mac-side task is recorded only once TikTok
says so.

What these pin down (`atlas/modules/clips/links.py::record_tiktok_link`,
`POST /clips/links/{clip_id}` in `atlas/api/clips.py`):
  * the route takes the HANDOFF_TOKEN bearer and is mesh-only, answering
    exactly as the /clips/whop routes do without it;
  * only `https://www.tiktok.com/@example_handle/video/<id>` is accepted: a
    short link, another host, another handle, the profile, a query string
    are 400 and nothing is read or written;
  * TikTok's public oEmbed is the ONE TikTok endpoint read (`GET /oembed`,
    the shape seen live 2026-09-30 for clip 8's video), and the link is
    recorded only when it names the clips account as the author and its
    title is the clip's caption with whitespace collapsed, and the time
    minted into the video id sits within 15 minutes of the row's posted_at;
  * a mismatch is 409 with the reason; TikTok unreachable is 503; neither
    writes anything;
  * a row that is not a posted TikTok clip with a Postiz id and no link is
    409; a link already on file is never overwritten; a URL already on
    another row is 409;
  * the write carries source `tiktok` (one whop.py offers) and is audited;
  * at most 30 checks an hour for one bearer.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.api import clips as clips_api
from atlas.api.peers import PerAddressLimiter
from atlas.modules.clips import links, whop
from atlas.modules.content import postiz, tiktok

TOKEN = "h" * 32
HANDLE = postiz.CLIPS_HANDLE
VIDEO_ID = 7691384068834954509
VIDEO = f"https://www.tiktok.com/@{HANDLE}/video/{VIDEO_ID}"
MINTED = datetime(2026, 9, 30, 17, 38, 23, tzinfo=UTC)          # VIDEO_ID >> 32
POSTED = datetime(2026, 9, 30, 17, 38, 27, 718721, tzinfo=UTC)  # clip 8, live
REF = "postiz_example_id_05"
CAPTION = ("Best moments from @example_handle live on Twitch\n\n#Ad\n"
           "#irubyana #irubyanaclips #twitch\n@irubyana")
# oEmbed's title for it, seen live: the caption whole, line breaks as spaces.
TITLE = ("Best moments from @example_handle live on Twitch #Ad #irubyana "
         "#irubyanaclips #twitch @example_handle")


def _oembed(**changes) -> dict:
    """TikTok's oEmbed record for VIDEO, as seen live 2026-09-30."""
    data = {
        "version": "1.0", "type": "video", "title": TITLE,
        "author_url": f"https://www.tiktok.com/@{HANDLE}",
        "author_name": "atlasclips", "width": "100%", "height": "100%",
        "html": "<blockquote class=\"tiktok-embed\"></blockquote>",
        "thumbnail_width": 720, "thumbnail_height": 1280,
        "thumbnail_url": "https://p16-common-sign.tiktokcdn-us.com/x.image",
        "provider_url": "https://www.tiktok.com", "provider_name": "TikTok",
        "author_unique_id": HANDLE, "embed_product_id": str(VIDEO_ID),
        "embed_type": "video",
    }
    data.update(changes)
    return data


def _row(cid: int = 8, **changes) -> dict:
    row = {"id": cid, "platform": "tiktok", "status": "posted", "post_ref": REF,
           "post_url": None, "post_url_source": None, "caption": CAPTION,
           "posted_at": POSTED}
    row.update(changes)
    return row


class Rows:
    """clip_outputs in memory, answering the three statements the way
    Postgres would: the row read, the other-row URL check, the guarded
    UPDATE."""

    def __init__(self, *rows: dict) -> None:
        self.rows = {r["id"]: dict(r) for r in rows}
        self.writes = 0

    async def fetch_one(self, sql: str, *args):
        text = sql.lstrip()
        if text.startswith("UPDATE clip_outputs"):
            cid, ref, url, source = args
            row = self.rows.get(cid)
            taken = any(o["post_url"] == url for o in self.rows.values()
                        if o["id"] != cid)
            if (row is None or row["post_ref"] != ref or row["status"] != "posted"
                    or row["post_url"] is not None or taken):
                return None
            self.writes += 1
            row["post_url"], row["post_url_source"] = url, source
            return {"id": cid}
        if text.startswith("SELECT id FROM clip_outputs WHERE post_url"):
            url, cid = args
            hits = sorted(o["id"] for o in self.rows.values()
                          if o["post_url"] == url and o["id"] != cid)
            return {"id": hits[0]} if hits else None
        assert text.startswith("SELECT id, platform, status, post_ref"), text
        row = self.rows.get(args[0])
        return dict(row) if row is not None else None


@pytest.fixture
def db(monkeypatch, memory_backend):
    def install(*rows: dict) -> Rows:
        fake = Rows(*rows)
        monkeypatch.setattr(links, "fetch_one", fake.fetch_one)
        return fake
    return install


def _tiktok(monkeypatch, data: dict | None = None, *, status: int = 200,
            down: bool = False, body: str | None = None) -> list[httpx.Request]:
    """TikTok's oEmbed answering `data` (or `body`); every request recorded."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if down:
            raise httpx.ConnectError("connection refused", request=request)
        if body is not None:
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=_oembed() if data is None else data)

    monkeypatch.setattr(tiktok, "client", lambda: httpx.AsyncClient(
        timeout=1.0, transport=httpx.MockTransport(handler)))
    return seen


def _only_oembed(seen: list[httpx.Request], url: str = VIDEO) -> None:
    assert seen, "TikTok was never read"
    for request in seen:
        assert request.method == "GET"
        assert (f"{request.url.scheme}://{request.url.host}{request.url.path}"
                == tiktok.OEMBED)
        assert request.url.params["url"] == url


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("HANDOFF_TOKEN", TOKEN)
    # A fresh limiter per test: the module's is one for the process.
    monkeypatch.setattr(clips_api, "_links_per_token",
                        PerAddressLimiter(clips_api.LINKS_PER_HOUR, 3600.0))
    app = FastAPI()
    app.include_router(clips_api.router)
    return TestClient(app)


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post(api: TestClient, cid: int = 8, url: str = VIDEO, **kw):
    return api.post(f"/clips/links/{cid}", headers=kw.pop("headers", _auth()),
                    json={"url": url}, **kw)


# ------------------------------------------------------------------ accepted

def test_the_video_id_carries_its_minting_time():
    assert links.video_time(VIDEO_ID) == MINTED
    assert abs(MINTED - POSTED) < timedelta(seconds=5)


def test_a_verified_link_is_recorded_with_the_platform_source(
        api, monkeypatch, db, memory_backend):
    rows = db(_row(8))
    seen = _tiktok(monkeypatch)
    r = _post(api)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["clip_id"] == 8 and body["post_url"] == VIDEO
    assert body["post_url_source"] == "tiktok"
    assert body["verified"]["author_unique_id"] == HANDLE
    assert body["verified"]["title"] == TITLE
    assert body["verified"]["video_id"] == str(VIDEO_ID)
    assert datetime.fromisoformat(body["read_at"]).tzinfo is not None
    assert rows.writes == 1
    assert rows.rows[8]["post_url"] == VIDEO
    assert rows.rows[8]["post_url_source"] == "tiktok"
    assert "tiktok" in whop.TRUSTED_SOURCES
    _only_oembed(seen)
    events = [json.loads(e.payload) for e in memory_backend.audit_rows]
    hit = [e for e in events if e["event"] == links.AUDIT_EVENT]
    assert len(hit) == 1 and hit[0]["subject"] == "clip_outputs:8"
    data = hit[0]["data"]
    assert data["post_url"] == VIDEO and data["post_url_source"] == "tiktok"
    assert data["postiz_post"] == REF and data["via"] == "handed_in"
    assert data["author_unique_id"] == HANDLE and data["minted_at"]


def test_the_title_is_the_caption_with_whitespace_collapsed():
    assert links.normalise(CAPTION) == TITLE
    assert links.title_matches(TITLE, CAPTION)
    assert links.title_matches("  " + TITLE.replace(" ", "   ") + "\n", CAPTION)
    assert not links.title_matches("", CAPTION)
    assert not links.title_matches(TITLE, "")
    assert not links.title_matches(TITLE + " extra", CAPTION)
    # Not seen live, but tolerated: a long title the caption begins with.
    assert links.title_matches(TITLE[:links.TITLE_MIN_PREFIX], CAPTION)
    # A short prefix is not the caption.
    assert not links.title_matches(TITLE[:links.TITLE_MIN_PREFIX - 1], CAPTION)
    assert not links.title_matches("Best moments from @example_handle live on Twitch",
                                   CAPTION)


# ------------------------------------------------------------------ refused

def test_the_wrong_author_is_409_and_nothing_is_written(api, monkeypatch, db):
    rows = db(_row(8))
    seen = _tiktok(monkeypatch, _oembed(author_unique_id="someoneelse",
                                        author_url="https://www.tiktok.com/@s"))
    r = _post(api)
    assert r.status_code == 409 and "@someoneelse" in r.json()["detail"]
    assert rows.writes == 0 and rows.rows[8]["post_url"] is None
    _only_oembed(seen)


def test_a_caption_mismatch_is_409_and_nothing_is_written(api, monkeypatch, db):
    rows = db(_row(8))
    _tiktok(monkeypatch, _oembed(title="Save up to 75% on select ATLUS titles "
                                       "during the ATLUS Developer Sale on Steam. "
                                       "#Ad #ATLUS #SteamSale @atlus_west"))
    r = _post(api)
    assert r.status_code == 409 and "caption" in r.json()["detail"]
    assert rows.writes == 0


def test_another_video_in_the_record_is_409(api, monkeypatch, db):
    rows = db(_row(8))
    _tiktok(monkeypatch, _oembed(embed_product_id="7691384068834954000"))
    r = _post(api)
    assert r.status_code == 409 and "7691384068834954000" in r.json()["detail"]
    assert rows.writes == 0


def test_a_video_minted_outside_the_window_is_409_before_tiktok_is_read(
        api, monkeypatch, db):
    # Clip 6 has the same caption as clip 8 and posted 29 minutes earlier
    # (live): only the minting time tells them apart.
    rows = db(_row(6, post_ref="postiz_example_id_06",
                   posted_at=datetime(2026, 9, 30, 17, 9, 14, tzinfo=UTC)))
    seen = _tiktok(monkeypatch)
    r = _post(api, cid=6)
    assert r.status_code == 409 and "minutes apart" in r.json()["detail"]
    assert rows.writes == 0 and seen == []


def test_a_video_minted_just_inside_the_window_is_taken(api, monkeypatch, db):
    rows = db(_row(8, posted_at=MINTED + links.POST_WINDOW - timedelta(seconds=1)))
    _tiktok(monkeypatch)
    assert _post(api).status_code == 200
    assert rows.writes == 1


@pytest.mark.parametrize("url", [
    "https://vm.tiktok.com/ZMabcdef/",                            # short link
    f"https://tiktok.com/@{HANDLE}/video/{VIDEO_ID}",             # bare host
    f"https://m.tiktok.com/@{HANDLE}/video/{VIDEO_ID}",           # other host
    f"https://www.tiktok.com/@someoneelse/video/{VIDEO_ID}",      # other handle
    f"https://www.tiktok.com/@{HANDLE}",                          # the profile
    f"https://www.tiktok.com/@{HANDLE}/video/{VIDEO_ID}?lang=en",  # a query
    f"https://www.tiktok.com/@{HANDLE}/video/{VIDEO_ID}/",        # a slash
    f"http://www.tiktok.com/@{HANDLE}/video/{VIDEO_ID}",          # not https
    f"https://www.tiktok.com/@{HANDLE}/video/abc",                # not an id
    f"https://www.youtube.com/shorts/{VIDEO_ID}",                 # elsewhere
    "",
])
def test_anything_but_the_clips_video_url_is_400(api, monkeypatch, db, url):
    rows = db(_row(8))
    seen = _tiktok(monkeypatch)
    r = _post(api, url=url)
    assert r.status_code == 400, (url, r.text)
    assert rows.writes == 0 and seen == []


def test_a_link_already_on_file_is_never_overwritten(api, monkeypatch, db):
    other = f"https://www.tiktok.com/@{HANDLE}/video/7691384068834954000"
    rows = db(_row(8, post_url=other, post_url_source="record_link"))
    seen = _tiktok(monkeypatch)
    r = _post(api)
    assert r.status_code == 409 and "not overwritten" in r.json()["detail"]
    assert rows.writes == 0 and rows.rows[8]["post_url"] == other
    assert rows.rows[8]["post_url_source"] == "record_link"
    assert seen == []


@pytest.mark.parametrize("changes, said", [
    ({"platform": "youtube"}, "not TikTok"),
    ({"status": "handed_off"}, "not posted"),
    ({"status": "submitted"}, "not posted"),
    ({"post_ref": None}, "no Postiz post id"),
    ({"posted_at": None}, "no posting time"),
])
def test_a_row_that_is_not_eligible_is_409(api, monkeypatch, db, changes, said):
    rows = db(_row(8, **changes))
    seen = _tiktok(monkeypatch)
    r = _post(api)
    assert r.status_code == 409 and said in r.json()["detail"]
    assert rows.writes == 0 and seen == []


def test_no_such_clip_is_404(api, monkeypatch, db):
    db(_row(8))
    seen = _tiktok(monkeypatch)
    assert _post(api, cid=99).status_code == 404
    assert seen == []


def test_a_url_already_on_another_row_is_409(api, monkeypatch, db):
    rows = db(_row(6, post_url=VIDEO, post_url_source="tiktok",
                   post_ref="postiz_example_id_06"),
              _row(8))
    seen = _tiktok(monkeypatch)
    r = _post(api, cid=8)
    assert r.status_code == 409 and "clip 6" in r.json()["detail"]
    assert rows.writes == 0 and rows.rows[8]["post_url"] is None
    assert seen == []


def test_a_row_that_changed_under_the_check_is_409(api, monkeypatch, db):
    rows = db(_row(8))
    _tiktok(monkeypatch)

    async def changed(sql: str, *args):
        if sql.lstrip().startswith("UPDATE"):
            return None
        return await Rows.fetch_one(rows, sql, *args)
    monkeypatch.setattr(links, "fetch_one", changed)
    r = _post(api)
    assert r.status_code == 409 and "changed" in r.json()["detail"]
    assert rows.writes == 0


# ------------------------------------------------------------ TikTok not read

def test_tiktok_unreachable_is_503_and_nothing_is_written(api, monkeypatch, db):
    rows = db(_row(8))
    seen = _tiktok(monkeypatch, down=True)
    r = _post(api)
    assert r.status_code == 503 and "not read" in r.json()["detail"]
    assert rows.writes == 0 and rows.rows[8]["post_url"] is None
    _only_oembed(seen)


@pytest.mark.parametrize("status, body", [
    (500, None), (502, "<html>bad gateway</html>"), (200, "not json"),
    (200, "[]"),
])
def test_an_answer_that_is_not_a_record_is_503(api, monkeypatch, db, status, body):
    rows = db(_row(8))
    _tiktok(monkeypatch, status=status, body=body)
    assert _post(api).status_code == 503
    assert rows.writes == 0


def test_a_video_tiktok_does_not_know_is_409(api, monkeypatch, db):
    # Seen live for an id that is not a video: HTTP 400, "Something went wrong".
    rows = db(_row(8))
    _tiktok(monkeypatch, {"message": "Something went wrong", "code": 400},
            status=400)
    r = _post(api)
    assert r.status_code == 409 and "does not know" in r.json()["detail"]
    assert rows.writes == 0


def test_the_read_is_the_public_oembed_with_a_ten_second_timeout():
    assert tiktok.TIMEOUT_S == 10.0
    assert tiktok.client().timeout.read == 10.0
    assert tiktok.OEMBED == "https://www.tiktok.com/oembed"


def test_the_clips_package_still_imports_no_http_client():
    """The read lives in content/tiktok.py; tests/test_clips.py pins the rule."""
    import ast
    import pathlib
    text = pathlib.Path(links.__file__).read_text()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] == "httpx" for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "httpx"


# ------------------------------------------------------------------ the door

def test_the_route_needs_the_token_exactly_as_the_whop_routes_do(
        api, monkeypatch, db):
    rows = db(_row(8))
    seen = _tiktok(monkeypatch)
    for headers in ({}, _auth("x" * 32), {"Authorization": TOKEN}):
        mine = _post(api, headers=headers)
        theirs = api.get("/clips/whop/pending", headers=headers)
        assert mine.status_code == theirs.status_code == 401
        assert mine.json() == theirs.json()
    monkeypatch.setenv("HANDOFF_TOKEN", "short")        # a weak token admits nobody
    assert _post(api, headers=_auth("short")).status_code == 401
    assert rows.writes == 0 and seen == []


def test_thirty_checks_an_hour_per_token(api, monkeypatch, db):
    rows = db(_row(8))
    seen = _tiktok(monkeypatch, _oembed(author_unique_id="someoneelse"))
    assert clips_api.LINKS_PER_HOUR == 30
    for _ in range(30):
        assert _post(api).status_code == 409
    r = _post(api)
    assert r.status_code == 429 and "30" in r.json()["detail"]
    assert len(seen) == 30 and rows.writes == 0


def test_a_missing_url_is_400(api, monkeypatch, db):
    db(_row(8))
    seen = _tiktok(monkeypatch)
    assert api.post("/clips/links/8", headers=_auth(), json={}).status_code == 400
    assert api.post("/clips/links/8", headers=_auth(),
                    content=b"not json").status_code == 400
    assert seen == []


def _main_app(monkeypatch, cidrs: str):
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", cidrs)
    monkeypatch.setenv("HANDOFF_TOKEN", TOKEN)
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return app


def test_the_route_is_mesh_only_and_mounted_behind_the_token(monkeypatch, db):
    from atlas.api import main
    assert not any("/clips".startswith(p) or p.startswith("/clips")
                   for p in main.PUBLIC_PATHS)
    assert "/clips/links" not in main.PUBLIC_EXACT
    rows = db(_row(8))
    seen = _tiktok(monkeypatch)
    app = _main_app(monkeypatch, "100.64.0.0/10")
    with TestClient(app, client=("203.0.113.9", 4000)) as outsider:
        # Even with the right token: an address off the mesh is not let in.
        assert outsider.post("/clips/links/8", headers=_auth(),
                             json={"url": VIDEO}).status_code == 404
    with TestClient(app, client=("100.64.0.7", 4000)) as mesh:
        assert mesh.post("/clips/links/8", json={"url": VIDEO}).status_code == 401
        r = mesh.post("/clips/links/8", headers=_auth(), json={"url": VIDEO})
        assert r.status_code == 200 and r.json()["post_url"] == VIDEO
    assert rows.writes == 1
    _only_oembed(seen)


# ------------------------------------------------------------------ pending

def test_pending_lists_only_tiktok_clips_still_without_a_link(api, monkeypatch):
    rows = [_row(8), _row(9, post_url="https://www.tiktok.com/@x/video/1"),
            _row(10, platform="youtube"), _row(11, status="made", post_ref=None)]

    async def fetch_all(sql: str, *args):
        assert "post_url IS NULL" in sql
        return [r for r in rows if r["platform"] == "tiktok" and r["status"] == "posted"
                and not r["post_url"]]
    monkeypatch.setattr(links, "fetch_all", fetch_all)
    r = api.get("/clips/links/pending", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert "read_at" in body
    assert body["clips"] == [{"clip_id": 8, "caption": CAPTION,
                              "posted_at": POSTED.isoformat()}]


def test_pending_needs_the_bearer(api):
    r = api.get("/clips/links/pending", headers=_auth("wrong"))
    assert r.status_code == 401 and r.json() == {"detail": "bad token"}
    assert api.get("/clips/links/pending").status_code == 401
