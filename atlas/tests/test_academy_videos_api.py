"""The store's public video gallery: GET /public/academy/videos and the
script that draws it (atlas/api/academy_videos.py, modules/academy/
video_feed.py, assets/gallery.js).

Only the channel's PUBLIC, not-withdrawn tutorials are ever listed; every
field is built in code (the thumbnail address from the id alone); text is
capped and stripped of markup characters; an unreadable source is a 503 the
page shows as "Videos are unavailable right now", never an empty list; and
nothing from YouTube is in the page's HTML before a visitor presses play.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import site, video_feed
from atlas.modules.academy import tutorial_series as ts

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
WELCOME = ("channel", 1, "uPzByskDRXw", datetime(2026, 10, 10, 18, 5, tzinfo=UTC))
CLAUDE0 = ("claude", 0, "M1a9Rd9mN6E", datetime(2026, 10, 11, 17, 20, tzinfo=UTC))
PRIVATE_ID = "PRIVATEid_1"            # a private/unlisted/scheduled upload
SECRET_DETAIL = "scheduled-for-friday"


def _rows(*items):
    return [{"series": s, "episode": e, "video_id": v, "at": at} for s, e, v, at in items]


@pytest.fixture
def db(monkeypatch):
    """A fake academy_tutorial_posts. Whatever SQL is asked, `served` is what
    comes back; `asked` records the query so the test can pin its filter."""
    state = {"rows": _rows(WELCOME, CLAUDE0), "asked": [], "fail": False}

    async def fetch_all(sql, *args):
        state["asked"].append(sql)
        if state["fail"]:
            raise RuntimeError("db down")
        return state["rows"]

    from atlas.kernel import state as kstate
    monkeypatch.setattr(kstate, "fetch_all", fetch_all)
    video_feed.clear_cache()
    yield state
    video_feed.clear_cache()


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from atlas.api import academy_videos as routes
    from atlas.api.main import app
    routes._limiter.clear()
    return TestClient(app, client=("203.0.113.9", 4000),
                      headers={"X-Atlas-Public": "1"})


def _get(client):
    return client.get("/public/academy/videos")


# ------------------------------------------------------------ what is listed

def test_the_list_is_the_public_tutorials_with_built_in_code_fields(client):
    r = _get(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    ids = {v["id"] for v in body["videos"]}
    assert ids == {WELCOME[2], CLAUDE0[2]}
    for v in body["videos"]:
        assert set(v) == {"id", "title", "description", "published", "thumbnail",
                          "watch"}
        assert v["thumbnail"] == f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg"
        assert v["watch"] == f"https://www.youtube.com/watch?v={v['id']}"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", v["published"])
    welcome = next(v for v in body["videos"] if v["id"] == WELCOME[2])
    assert welcome["title"] == ts.series("channel").episode(1).title[:100]
    assert welcome["published"] == "2026-10-10"


def test_the_query_keeps_only_public_and_not_withdrawn_rows(client, db):
    _get(client)
    sql = " ".join(db["asked"][0].split()).lower()
    assert "where public and not withdrawn" in sql


def test_unknown_episodes_and_malformed_ids_never_appear(client, db):
    at = WELCOME[3]
    db["rows"] = _rows(WELCOME, ("nope", 9, "aaaaaaaaaaa", at),
                       ("claude", 0, "bad id <script>", at),
                       ("channel", 1, "../../etc/passwd", at))
    ids = [v["id"] for v in _get(client).json()["videos"]]
    assert ids == [WELCOME[2]]


def test_no_private_id_or_stored_detail_is_in_the_response(client, db):
    db["rows"] = _rows(WELCOME)
    db["rows"][0]["detail"] = SECRET_DETAIL
    db["rows"][0]["playlist_id"] = "PLsecret"
    text = _get(client).text
    assert PRIVATE_ID not in text and SECRET_DETAIL not in text
    assert "PLsecret" not in text


def test_an_empty_read_is_ok_and_empty_not_unavailable(client, db):
    db["rows"] = []
    r = _get(client)
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "videos": []}


# --------------------------------------------------- unreadable is not empty

def test_an_unreadable_source_is_503_unavailable_never_an_empty_ok(client, db):
    db["fail"] = True
    r = _get(client)
    assert r.status_code == 503
    assert r.json() == {"status": "unavailable"}
    assert "videos" not in r.json()
    assert r.headers["cache-control"] == "no-store"


def test_a_failed_read_is_not_cached_as_good_and_recovers(client, db):
    db["fail"] = True
    assert _get(client).status_code == 503
    db["fail"] = False
    video_feed.clear_cache()           # the short failure memory elapsed
    assert _get(client).status_code == 200


# ------------------------------------------------------------ text is capped

def test_text_is_capped_and_stripped_of_markup_and_control_characters():
    assert video_feed.clean("<img src=x onerror=alert(1)> hi\x00\x07", 100) == \
        "img src=x onerror=alert(1) hi"
    assert len(video_feed.clean("x" * 500, 100)) <= 100
    assert video_feed.clean("a\n\n  b\t c", 100) == "a b c"
    assert video_feed.clean(None, 100) == ""


def test_a_hostile_title_from_the_catalog_is_neutralised_in_the_response(
        client, db, monkeypatch):
    evil = type("E", (), {"title": "<script>alert(1)</script>" + "T" * 300,
                          "summary": "</p><img src=x onerror=1> " + "S" * 600,
                          "number": 1})()
    real_published = site.published
    monkeypatch.setattr(site, "published", lambda videos: [
        (s, evil, v) for s, _ep, v in real_published(videos)])
    db["rows"] = _rows(WELCOME)
    v = _get(client).json()["videos"][0]
    assert "<" not in v["title"] and ">" not in v["title"]
    assert "<" not in v["description"] and ">" not in v["description"]
    assert len(v["title"]) <= video_feed.TITLE_MAX
    assert len(v["description"]) <= video_feed.DESCRIPTION_MAX


# ----------------------------------------------- cache and rate limit, edge

def test_the_response_is_cached_for_a_minute_and_the_db_asked_once(client, db):
    r1 = _get(client)
    r2 = _get(client)
    assert r1.json() == r2.json()
    assert len(db["asked"]) == 1
    assert "max-age=" in r1.headers["cache-control"]
    assert int(re.search(r"max-age=(\d+)", r1.headers["cache-control"]).group(1)) >= 60
    assert r1.headers["x-content-type-options"] == "nosniff"


def test_a_flood_that_forces_rebuilds_is_rate_limited_and_the_429_is_not_a_list(
        client, db):
    from atlas.api import academy_videos as routes
    codes = []
    for _ in range(routes._limiter.limit + 5):
        video_feed.clear_cache()                  # every request must rebuild
        codes.append(_get(client).status_code)
    assert codes[0] == 200 and codes[-1] == 429
    video_feed.clear_cache()
    last = _get(client)
    assert last.status_code == 429 and "videos" not in last.json()
    assert last.json()["status"] == "unavailable"


def test_cached_answers_are_never_rate_limited(client, db):
    from atlas.api import academy_videos as routes
    assert _get(client).status_code == 200            # fills the cache
    codes = {_get(client).status_code for _ in range(routes._limiter.limit + 20)}
    assert codes == {200}
    assert len(db["asked"]) == 1


def test_a_failure_is_remembered_for_30_seconds_not_retried_each_time(client, db):
    assert video_feed.FAILURE_SECONDS == 30.0
    db["fail"] = True
    assert _get(client).status_code == 503
    assert _get(client).status_code == 503
    assert len(db["asked"]) == 1
    db["fail"] = False
    video_feed._cache["at"] -= 31.0                   # the memory expires
    assert _get(client).status_code == 200


def test_concurrent_expiries_rebuild_once(db):
    import asyncio

    async def run():
        return await asyncio.gather(*(video_feed.feed() for _ in range(8)))

    out = asyncio.run(run())
    assert all(o == out[0] and o for o in out)
    assert len(db["asked"]) == 1


def test_the_counter_counts_the_gallery_source_uncapped_and_shows_the_time():
    cat = cat_mod.parse(json.loads(cat_mod.CATALOG_PATH.read_text()))
    vids = [site.Video(s, e, f"vid{n:08d}", datetime(2026, 10, 1, tzinfo=UTC))
            for n, (s, e) in enumerate(
                (ser.key, ep.number) for ser in ts.SERIES for ep in ser.episodes)]
    expected = len(site.published(vids))
    assert expected == sum(len(ser.episodes) for ser in ts.SERIES)   # every one, uncapped
    page = site.render_root(cat, now=NOW, videos=vids)["academy/index.html"].decode()
    assert f'data-count="{expected}">{expected}</b><span>free lessons on the channel' in page
    assert 'As of <time datetime="2026-11-11T12:00Z">2026-11-11 12:00 UTC</time>' in page


def test_gallery_js_says_unavailable_when_no_card_passes_its_checks():
    js = (site.ASSETS / "gallery.js").read_text()
    assert "!cards.length && list.length" in js


def test_the_route_is_public_exactly_and_nothing_near_it_is():
    from atlas.api import main
    assert main._is_public("/public/academy/videos")
    for path in ("/public/academy/videos/", "/public/academy/videosx",
                 "/public/academy/videos/../../api", "/public/academy/video"):
        assert not main._is_public(path), path


def test_the_public_caddyfile_is_not_widened():
    caddy = (ROOT / "Caddyfile.public").read_text()
    assert "handle /public/academy/* {" in caddy
    assert "handle /public/* {\n\t\trespond \"not found\" 404" in caddy
    code = "\n".join(l for l in caddy.splitlines() if not l.lstrip().startswith("#"))
    assert "public/academy/videos" not in code         # no new API path was added


def test_no_write_verbs_on_the_route(db):
    from fastapi.testclient import TestClient

    from atlas.api.main import app
    c = TestClient(app, client=("203.0.113.9", 4000), headers={"X-Atlas-Public": "1"})
    for verb in ("post", "put", "delete", "patch"):
        assert getattr(c, verb)("/public/academy/videos").status_code in (404, 405)


# ----------------------------------------------- the page and its script

def _built(raw=None) -> dict[str, bytes]:
    return site.render_root(cat_mod.parse(raw or json.loads(cat_mod.CATALOG_PATH.read_text())),
                       NOW)


def test_the_home_html_has_the_gallery_shell_but_no_third_party_address():
    page = _built()["academy/index.html"].decode()
    assert 'id="videos"' in page and "data-gallery" in page
    assert "Videos are unavailable right now" in page      # the no-JS / pre-load state
    assert "i.ytimg.com" not in page
    assert "youtube-nocookie.com" not in page
    assert "assets/gallery.js" in page
    # the only absolute addresses are the ones the store already carried
    hosts = set(re.findall(r'(?:src|href|action)="https?://([^/"]+)', page))
    assert not {h for h in hosts if "ytimg" in h or "youtube" in h and
                h not in {"www.youtube.com", "youtu.be"}}, hosts


def test_no_page_loads_a_youtube_image_or_frame_before_a_click():
    for path, data in _built().items():
        if not path.endswith(".html"):
            continue
        html = data.decode()
        assert not re.search(r"<(?:img|iframe|script|link)[^>]+(?:ytimg|youtube-nocookie)",
                             html), path


def test_the_gallery_script_never_builds_html_from_text():
    js = (site.ASSETS / "gallery.js").read_text()
    for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                 "eval(", "localStorage", "sessionStorage", "document.cookie",
                 "sendBeacon"):
        assert word not in js, word
    assert "textContent" in js and "createElement" in js
    assert "/public/academy/videos" in js
    assert "Videos are unavailable right now" in js
    # the frame is made only inside the click handler, from the nocookie host
    assert js.count("createElement('iframe')") == 1
    assert "https://www.youtube-nocookie.com/embed/" in js
    assert "https://i.ytimg.com/vi/" in js


def test_the_gallery_script_is_published_as_an_asset_and_only_on_the_home_page():
    built = _built()
    assert built["assets/gallery.js"] == (site.ASSETS / "gallery.js").read_bytes()
    for path, data in built.items():
        if path.endswith(".html"):
            assert ("assets/gallery.js" in data.decode()) == (path == "academy/index.html"), path


def test_the_privacy_page_tells_the_truth_about_the_thumbnails():
    text = _built()["privacy/index.html"].decode()
    assert "i.ytimg.com" in text
    assert "video gallery on the home page" in text
    assert "only request this site makes to" in text
    assert "youtube-nocookie.com" in text


def test_payment_and_recovery_routes_are_untouched():
    """The join, Stripe and recover code is not part of this change."""
    import subprocess
    out = subprocess.run(["git", "diff", "--stat", "feat/local-agency", "--",
                          "atlas/api/academy.py", "atlas/modules/academy/delivery.py",
                          "atlas/modules/academy/stripe_read.py",
                          "atlas/kernel/executor.py"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode == 0:
        assert out.stdout.strip() == ""
