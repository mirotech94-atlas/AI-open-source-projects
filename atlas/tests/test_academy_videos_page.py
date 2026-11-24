"""The store's free video lessons page, agency.storefront.test/videos/ (Miro,
2026-10-11): every tutorial public on YouTube, and nothing else, with the
series' course beside it; schema.org records for the videos; nothing loads
from YouTube before a visitor presses play; and the store republishes when
the published list changes.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.modules import store_origin
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import publish, site
from atlas.modules.academy import tutorial_series as ts
from tests.test_academy_site import BANNED, _off, _raw, _text

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
WELCOME = site.Video("channel", 1, "uPzByskDRXw", datetime(2026, 10, 10, 18, 5, tzinfo=UTC))
CLAUDE0 = site.Video("claude", 0, "M1a9Rd9mN6E", datetime(2026, 10, 11, 17, 20, tzinfo=UTC))
TODAY = [WELCOME, CLAUDE0]
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test downloads a thumbnail from YouTube."""
    monkeypatch.setattr(publish, "_fetch_thumb", lambda video_id: None)


def _render(videos=TODAY, raw: dict | None = None) -> dict[str, bytes]:
    return site.render_root(cat_mod.parse(raw or _raw()), NOW, videos=videos)


def _page(videos=TODAY, raw: dict | None = None) -> str:
    return _render(videos, raw)["videos/index.html"].decode()


def _ld(page: str) -> dict:
    body = page.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    return json.loads(body)


# ------------------------------------------------------------------ the page

def test_todays_two_videos_are_on_the_page_in_their_series():
    page = _page()
    welcome = ts.series("channel").episode(1)
    claude0 = ts.series("claude").episode(0)
    for v, ep in ((WELCOME, welcome), (CLAUDE0, claude0)):
        assert f'id="v-{v.video_id}"' in page
        assert f'href="https://youtu.be/{v.video_id}"' in page
        assert (f'data-embed="https://www.youtube-nocookie.com/embed/{v.video_id}'
                "?autoplay=1&amp;rel=0\"") in page
        assert site._e(ep.title) in page and ep.level_label in page
    assert page.index('id="series-channel"') < page.index('id="series-claude"')
    assert site._e(ts.series("claude").playlist) in page
    assert page.count('rel="noopener">Watch on YouTube</a>') == 2
    assert page.count("Opens on YouTube in a new tab.") == 2
    # Each card: topic, level, takeaway (its chapters), one related title.
    assert site._e(claude0.topic_shown) in page
    assert "<b>You'll learn:</b> What Claude is; Your first chat;" in page
    assert page.count('class="vrel"') == 2
    assert page.count("https://www.youtube.com/@atlasacademyphp?sub_confirmation=1") >= 3
    # The Claude series' level ladder lights Basics only.
    assert '<li class="on"><b>Level 0</b>Basics</li>' in page
    assert "<li><b>Level 3</b>Advanced</li>" in page
    assert "Go deeper: courses at agency.storefront.test" in page


def test_a_series_with_nothing_public_has_no_section():
    page = _page()
    for key in ("n8n", "project", "business", "agents", "local"):
        assert f'id="series-{key}"' not in page


def test_nothing_unpublished_reaches_the_page():
    """Unknown episodes, malformed ids and planned episodes never show;
    the only upcoming title is a scripted one, named in a muted line."""
    junk = [site.Video("claude", 99, "AAAAAAAAAAA", NOW),
            site.Video("nope", 1, "BBBBBBBBBBB", NOW),
            site.Video("n8n", 0, "bad id with spaces", NOW),
            site.Video("n8n", 0, "<script>x</s", NOW)]
    page = _page(TODAY + junk)
    assert "AAAAAAAAAAA" not in page and "BBBBBBBBBBB" not in page
    assert 'id="series-n8n"' not in page and "<script>x" not in page
    assert page.count('class="vcard') == 2
    for s in ts.SERIES:
        for ep in s.episodes:
            if not ep.scripted:
                assert site._e(ep.title) not in page, ep.title
    nxt = ts.series("claude").episode(1)
    assert f"Next up in this series: {site._e(nxt.title)}" in page
    assert "coming soon" not in page.lower().replace("coming soon</button>", "")


def test_the_latest_upload_of_an_episode_wins():
    older = site.Video("claude", 0, "OLDOLDOLD00", datetime(2026, 10, 1, tzinfo=UTC))
    page = _page([older, CLAUDE0])
    assert "M1a9Rd9mN6E" in page and "OLDOLDOLD00" not in page


def test_with_no_video_the_page_says_so_and_fakes_nothing():
    page = _page([])
    assert 'class="vcard' not in page and "No lesson is published here yet" in page
    assert _ld(page)["@graph"][0]["@type"] == "CollectionPage"
    assert len(_ld(page)["@graph"]) == 1


def test_no_banned_word_and_every_number_is_series_row_or_catalog_text():
    page = _render()["videos/index.html"]
    text = " ".join(_text(page).parts)
    for pattern in BANNED:
        assert not re.search(pattern, text, re.IGNORECASE), pattern
    allowed = set(re.findall(r"\d+(?:\.\d+)?", cat_mod.CATALOG_PATH.read_text()))
    allowed |= set(re.findall(r"\d+", store_origin.CONTACT_PHONE_DISPLAY))
    allowed |= set(re.findall(r"\d+(?:\.\d+)?", Path(ts.__file__).read_text()))
    for v in TODAY:
        allowed |= set(re.findall(r"\d+", v.video_id + v.uploaded.isoformat()))
    for number in re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")):
        assert number in allowed, number


# ------------------------------------------------------------------ selling

def test_each_card_sells_one_related_title_with_its_real_buy_link():
    raw = _raw()
    page = _page(raw=raw)
    cat = cat_mod.parse(raw)
    for v in TODAY:
        card = page.split(f'id="v-{v.video_id}"', 1)[1].split("</article>", 1)[0]
        p = cat.product(site.LESSON_PRODUCTS[v.series][0])
        assert p is not None and p.offers[0].buy_live
        assert card.count('class="vrel"') == 1
        assert f'href="{p.offers[0].payment_link}"' in card
        assert cat_mod.money(p.offers[0].price_usd) in card
    # Never the generic upsell: no bundle Buy anywhere in the lesson cards.
    assert cat.bundle.offer.payment_link not in page
    prices = set(re.findall(r"\$\d+(?:\.\d+)?", page))
    assert prices <= {cat_mod.money(o.price_usd) for o in cat.offers()}


def test_an_episode_can_name_its_own_related_title(monkeypatch):
    monkeypatch.setitem(site.EPISODE_PRODUCTS, ("claude", 0),
                        ("safe-ai-operator", "A reason."))
    page = _page()
    card = page.split('id="v-M1a9Rd9mN6E"', 1)[1].split("</article>", 1)[0]
    assert "Build a Safe AI Operator" in card and "Obsidian" not in card


def test_a_length_is_shown_only_when_the_render_says_it():
    timed = site.Video("claude", 0, "M1a9Rd9mN6E", CLAUDE0.uploaded, seconds=372.4)
    page = _page([WELCOME, timed])
    claude = page.split('id="v-M1a9Rd9mN6E"', 1)[1].split("</article>", 1)[0]
    welcome = page.split('id="v-uPzByskDRXw"', 1)[1].split("</article>", 1)[0]
    assert "6:12" in claude and 'class="vlen"' not in welcome
    ld = [g for g in _ld(page)["@graph"] if g["@type"] == "VideoObject"]
    assert ld[1]["duration"] == "PT6M12S" and "duration" not in ld[0]


def test_nothing_on_sale_means_no_course_card_and_no_price():
    page = _page(raw=_off())
    assert 'class="vrel' not in page
    assert "buy.stripe.com" not in page and "$" not in page.split("<main", 1)[1]


def test_every_series_maps_to_a_catalog_title():
    cat = cat_mod.load()
    slugs = {p.slug for p in cat.products} | {cat.bundle.slug}
    assert set(site.LESSON_PRODUCTS) == {s.key for s in ts.SERIES}
    for slug, why in site.LESSON_PRODUCTS.values():
        # a pairing names a catalog title and says why, or is "no paid
        # recommendation" ("", "") -- never a slug without a reason
        assert (slug in slugs and why.strip()) or (slug, why) == ("", ""), slug


# ------------------------------------------------------------------ privacy

def test_nothing_loads_from_youtube_before_play():
    """Posters are the site's own files; the page has no iframe and no
    third-party src; the player is only the data-embed app.js swaps in."""
    files = _render()
    page = files["videos/index.html"].decode()
    assert "<iframe" not in page
    for src in re.findall(r'\ssrc="([^"]+)"', page):
        assert not src.startswith(("http:", "https:", "//")), src
    for v in TODAY:
        assert f"assets/videos/{v.video_id}.svg" in files
    for url in re.findall(r'data-embed="([^"]+)"', page):
        assert url.startswith("https://www.youtube-nocookie.com/embed/"), url
    js = (site.ASSETS / "app.js").read_text()
    assert "createElement('iframe')" not in js  # the card opens YouTube itself


def test_a_thumbnail_on_disk_is_served_from_the_site(tmp_path, monkeypatch):
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    from atlas.modules.academy import tutorials
    s = ts.series("claude")
    d = tutorials.episode_dir(s, s.episode(0))
    d.mkdir(parents=True)
    (d / "thumb.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    (d / "manifest.json").write_text(json.dumps({
        "series": "claude", "episode": 0, "audio": "fish", "seconds": 372.4,
        "chapters": [], "rendered_at": "2026-10-11T15:02:00+00:00"}))
    [with_thumb, without] = publish._with_thumbs([CLAUDE0, WELCOME])
    assert with_thumb.seconds == 372.4 and without.seconds is None
    assert with_thumb.thumb == b"\xff\xd8\xff\xe0fakejpeg" and without.thumb is None
    files = site.render_root(cat_mod.load(), NOW, videos=[with_thumb, without])
    assert files["assets/videos/M1a9Rd9mN6E.jpg"] == with_thumb.thumb
    assert "assets/videos/uPzByskDRXw.svg" in files
    assert '"../assets/videos/M1a9Rd9mN6E.jpg"' in files["videos/index.html"].decode()


def test_the_store_csp_if_any_lets_the_player_in():
    """The store is the last, unmatched `handle` of Caddyfile.public and sets
    no CSP today. If one is ever added, it must allow the youtube-nocookie
    player. (The landing and preview handles above it are not the store's.)"""
    caddy = (ROOT / "Caddyfile.public").read_text()
    block = caddy[caddy.rindex("\thandle {"):].split("\n\t}\n", 1)[0]
    assert "root * /srv/shop/current" in block and "file_server" in block
    for csp in re.findall(r'Content-Security-Policy "([^"]+)"', block):
        frame = re.search(r"(?:frame-src|child-src|default-src)[^;]*", csp)
        assert frame and "https://www.youtube-nocookie.com" in frame.group(0), csp


def test_the_privacy_page_and_faq_say_what_play_loads():
    files = _render()
    privacy = files["privacy/index.html"].decode()
    assert "youtube-nocookie.com" in privacy and "press play" in privacy
    assert "opens on YouTube, in a new tab" in files["academy/index.html"].decode()


# ------------------------------------------------------------------ search

def test_json_ld_has_a_valid_video_object_per_video():
    ld = _ld(_page())
    assert ld["@context"] == "https://schema.org"
    videos = [g for g in ld["@graph"] if g["@type"] == "VideoObject"]
    assert [v["contentUrl"] for v in videos] == ["https://youtu.be/uPzByskDRXw",
                                                 "https://youtu.be/M1a9Rd9mN6E"]
    for v, row in zip(videos, TODAY, strict=True):
        ep = ts.series(row.series).episode(row.episode)
        assert v["name"] == ep.title and v["description"] == ep.summary
        assert v["embedUrl"] == f"https://www.youtube-nocookie.com/embed/{row.video_id}"
        assert v["uploadDate"] == row.uploaded.isoformat().replace("+00:00", "Z")
        assert datetime.fromisoformat(v["uploadDate"]) == row.uploaded
        assert v["thumbnailUrl"][0] == f"https://i.ytimg.com/vi/{row.video_id}/hqdefault.jpg"
        assert v["thumbnailUrl"][1].startswith("https://agency.storefront.test/assets/videos/")


def test_sitemap_llms_nav_and_home_point_at_the_videos():
    files = _render()
    assert "<loc>https://agency.storefront.test/videos/</loc>" in files["sitemap-academy.xml"].decode()
    llms = files["academy/llms.txt"].decode()
    assert "https://agency.storefront.test/videos/" in llms
    assert "https://youtu.be/uPzByskDRXw" in llms and "https://youtu.be/M1a9Rd9mN6E" in llms
    home = files["academy/index.html"].decode()
    assert 'href="../videos/"' in home and ">Free lessons</a>" in home
    product = files["safe-ai-operator/index.html"].decode()
    assert 'href="../videos/"' in product
    assert 'aria-current="page">Free lessons' in files["videos/index.html"].decode()


def test_the_manifest_lists_what_the_page_lists_and_nothing_that_drifts():
    files = _render()
    data = json.loads(files["videos/videos.json"])
    assert data == {"videos": [
        {"series": "channel", "episode": 1, "video_id": "uPzByskDRXw"},
        {"series": "claude", "episode": 0, "video_id": "M1a9Rd9mN6E"}]}
    # Rendered again later, the page and the manifest are byte for byte the same.
    later = site.render_root(cat_mod.load(), datetime(2027, 1, 1, tzinfo=UTC), videos=TODAY)
    assert later["videos/index.html"] == files["videos/index.html"]
    assert later["videos/videos.json"] == files["videos/videos.json"]


# ------------------------------------------------------------------ data + refresh

async def test_public_videos_reads_only_public_unwithdrawn_rows(monkeypatch):
    seen: list[str] = []

    async def fake_fetch_all(sql, *args):
        seen.append(sql)
        return [{"series": "claude", "episode": 0, "video_id": "M1a9Rd9mN6E",
                 "at": datetime(2026, 10, 11, 17, 20, tzinfo=UTC)}]
    from atlas.kernel import state
    monkeypatch.setattr(state, "fetch_all", fake_fetch_all)
    [v] = await publish.public_videos()
    assert "WHERE public AND NOT withdrawn" in seen[0]
    assert v == site.Video("claude", 0, "M1a9Rd9mN6E",
                           datetime(2026, 10, 11, 17, 20, tzinfo=UTC))


async def test_an_unreadable_table_refuses_the_publish(monkeypatch):
    async def broken(sql, *args):
        raise OSError("db down")
    from atlas.kernel import state
    monkeypatch.setattr(state, "fetch_all", broken)
    assert await publish.public_videos() is None
    assert publish._dry([], None).detail == publish.VIDEOS_UNREADABLE
    assert not publish._publish([], None).ok


@pytest.fixture
def executed(monkeypatch):
    calls = []

    async def fake_execute(req):
        calls.append(req)
        from atlas.kernel.contracts import ActionResult
        return ActionResult(True, "published")
    from atlas.kernel import executor
    monkeypatch.setattr(executor, "execute", fake_execute)
    return calls


async def test_refresh_republishes_only_when_the_list_changed(monkeypatch, executed):
    async def today():
        return list(TODAY)
    monkeypatch.setattr(publish, "public_videos", today)
    monkeypatch.setattr(publish, "_live_manifest", lambda: site.videos_manifest(TODAY))
    assert await publish.refresh_videos() == "videos page current"
    assert executed == []

    monkeypatch.setattr(publish, "_live_manifest", lambda: site.videos_manifest([WELCOME]))
    assert await publish.refresh_videos() == "published"
    [req] = executed
    assert req.action == "academy.publish_shop" and req.params == {}
    assert req.approved_by is None


async def test_refresh_does_nothing_when_the_table_is_unreadable(monkeypatch, executed):
    async def unreadable():
        return None
    monkeypatch.setattr(publish, "public_videos", unreadable)
    monkeypatch.setattr(publish, "_live_manifest", lambda: None)
    assert await publish.refresh_videos() == publish.VIDEOS_UNREADABLE
    assert executed == []


def test_the_live_manifest_is_read_from_the_live_release(tmp_path, monkeypatch):
    from atlas.modules.agency import hosting
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", tmp_path)
    assert publish._live_manifest() is None
    stamp = "20261011T180000000000Z"
    rel = hosting.release_dir(tmp_path, publish.SLUG, stamp)
    (rel / "academy" / "videos").mkdir(parents=True)
    (rel / "academy" / "videos" / "videos.json").write_text(json.dumps(site.videos_manifest(TODAY)))
    hosting.point_at(tmp_path, publish.SLUG, stamp)
    assert publish._live_manifest() == site.videos_manifest(TODAY)


def test_a_missing_thumbnail_is_downloaded_once_at_build_time(monkeypatch, tmp_path):
    """The build copies YouTube's thumbnail into the store; a non-JPEG or a
    failed download leaves the drawn poster."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    asked: list[str] = []

    def fetch(video_id):
        asked.append(video_id)
        return b"\xff\xd8\xff\xe0downloaded" if video_id == "uPzByskDRXw" else None
    monkeypatch.setattr(publish, "_fetch_thumb", fetch)
    [w, c] = publish._with_thumbs([WELCOME, CLAUDE0])
    assert asked == ["uPzByskDRXw", "M1a9Rd9mN6E"]
    assert w.thumb == b"\xff\xd8\xff\xe0downloaded" and c.thumb is None
    assert publish._jpeg(b"<html>not an image") is None


def test_fetch_thumb_asks_only_for_a_valid_id(monkeypatch):
    import httpx
    urls: list[str] = []

    class R:
        status_code = 200
        content = b"\xff\xd8\xff\xe0x"

    def get(url, **kw):
        urls.append(url)
        return R()
    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.undo()  # drop the autouse stub of _fetch_thumb, keep nothing else
    monkeypatch.setattr(httpx, "get", get)
    assert publish._fetch_thumb("not valid!") is None and urls == []
    assert publish._fetch_thumb("uPzByskDRXw") == b"\xff\xd8\xff\xe0x"
    assert urls == ["https://i.ytimg.com/vi/uPzByskDRXw/hqdefault.jpg"]
