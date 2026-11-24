"""Individual lesson pages, agency.storefront.test/lessons/<series>-<n>/ (shop
master plan F11, F12, F15): one per public tutorial and none for anything
else, built from the script and the published render's manifest only --
nothing invented, nothing loaded from YouTube before play, and no link to a
lesson that isn't public.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest

from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import lesson_pages as lp
from atlas.modules.academy import publish, site, tutorials
from atlas.modules.academy import tutorial_series as ts
from tests.test_academy_site import BANNED, _off, _raw, _text

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
WELCOME = site.Video("channel", 1, "uPzByskDRXw", datetime(2026, 10, 10, 18, 5, tzinfo=UTC))
CLAUDE0 = site.Video("claude", 0, "M1a9Rd9mN6E", datetime(2026, 10, 11, 17, 20, tzinfo=UTC))
N8N1 = site.Video("n8n", 1, "AbCdEfGhIj1", datetime(2026, 10, 14, 17, 0, tzinfo=UTC),
                  seconds=412.0,
                  chapters=((0.0, "Intro"), (35.5, "Here's what you'll have"),
                            (128.0, "Lab: your own city")))
N8N2 = site.Video("n8n", 2, "AbCdEfGhIj2", datetime(2026, 10, 15, 17, 0, tzinfo=UTC))
ALL = [WELCOME, CLAUDE0, N8N1, N8N2]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(publish, "_fetch_thumb", lambda video_id: None)


def _render(videos=ALL, raw: dict | None = None) -> dict[str, bytes]:
    return site.render_root(cat_mod.parse(raw or _raw()), NOW, videos=videos)


def _lesson(files: dict[str, bytes], key: str) -> str:
    return files[f"lessons/{key}/index.html"].decode()


# ------------------------------------------------------------------ which pages

def test_a_page_per_public_tutorial_and_none_for_the_rest():
    files = _render()
    pages = sorted(k for k in files if k.startswith("lessons/"))
    assert pages == sorted(f"lessons/{v.series}-{v.episode}/index.html" for v in ALL)
    # n8n #0 and #3 are scripted but not public here: no page.
    assert "lessons/n8n-0/index.html" not in files
    assert "lessons/n8n-3/index.html" not in files
    # No video at all: no lesson page.
    assert not any(k.startswith("lessons/") for k in _render([]))


def test_an_unknown_episode_or_bad_id_gets_no_page():
    bad = site.Video("n8n", 1, "not-an-id", N8N1.uploaded)
    ghost = site.Video("n8n", 99, "AbCdEfGhIj9", N8N1.uploaded)
    files = _render([bad, ghost])
    assert not any(k.startswith("lessons/") for k in files)


def test_the_page_has_a_title_description_and_canonical():
    page = _lesson(_render(), "n8n-1")
    ep = ts.series("n8n").episode(1)
    assert f"<title>{site._e(ep.title)} | " in page
    assert (f'<meta name="description" content="{site._e(site.meta_description(ep.summary))}">'
            in page)
    assert '<link rel="canonical" href="https://agency.storefront.test/lessons/n8n-1/">' in page
    assert site._e(ep.summary) in page
    assert ep.level_label in page
    for need in ep.needs:
        assert site._e(need) in page
    # Same shell as /videos/: the nav marks Free Lessons, the footer is there.
    assert 'aria-current="page">Free lessons' in page and 'class="foot"' in page
    assert page.count("<script") == 2   # app.js, and the JSON-LD block


# ------------------------------------------------------------------ the video

def test_the_video_is_click_to_load_as_on_the_videos_page():
    files = _render()
    page = _lesson(files, "n8n-1")
    assert "<iframe" not in page
    assert ('<a class="yt" target="_blank" rel="noopener noreferrer" '
            'href="https://youtu.be/AbCdEfGhIj1" '
            'data-embed="https://www.youtube-nocookie.com/embed/AbCdEfGhIj1'
            '?autoplay=1&amp;rel=0"') in page
    assert '<article class="vcard"' in page   # the .yt styles are scoped to it
    assert '"../../assets/videos/AbCdEfGhIj1.svg"' in page
    assert "assets/videos/AbCdEfGhIj1.svg" in files
    assert ">Watch on YouTube</a>" in page
    for src in re.findall(r'\ssrc="([^"]+)"', page):
        assert not src.startswith(("http:", "https:", "//")), src
    assert "ytimg" not in page


def test_duration_and_chapters_come_from_the_manifest_only():
    files = _render()
    page = _lesson(files, "n8n-1")
    assert "6:52" in page                      # 412 s, as the /videos/ card says it
    assert 'href="https://youtu.be/AbCdEfGhIj1?t=0"' in page
    assert 'href="https://youtu.be/AbCdEfGhIj1?t=35"' in page
    assert 'href="https://youtu.be/AbCdEfGhIj1?t=128"' in page
    assert ">2:08</a> Lab: your own city" in page


def test_a_missing_manifest_means_no_duration_and_no_chapters():
    page = _lesson(_render(), "n8n-2")
    assert "?t=" not in page
    assert 'class="vlen"' not in page
    assert "Chapters" not in page
    # No record of what the video says: the script, labelled as the script.
    assert '<h2 id="transcript">Lesson script</h2>' in page
    assert site._e(lp.SCRIPT_NOTE) in page and lp.SAID_NOTE not in page


# ------------------------------------------------------------------ transcript

def test_the_transcript_is_the_script_with_no_cue_left():
    files = _render()
    for v in ALL:
        page = _lesson(files, f"{v.series}-{v.episode}")
        text = " ".join(_text(page.encode()).parts)
        assert not re.search(r"\[[a-z -]+\]", text), v
        for word in ts.CUE_WORDS:
            assert f"[{word}]" not in page, (v, word)
    page = _lesson(files, "claude-0")
    ep = ts.series("claude").episode(0)
    # A line that carries a cue in the script is shown without it.
    line = next(x for sc in ep.scenes for x in sc.said if "[chuckling]" in x)
    assert site._e(ts.plain(line)) in page
    # In the order the video speaks them.
    said = [site._e(tutorials.shown(x)) for sc in ep.scenes for x in sc.said]
    at = [page.index(x) for x in said[:6]]
    assert at == sorted(at)


def test_shown_turns_spoken_dots_into_the_address():
    ep = ts.series("n8n").episode(1)
    lines = lp.transcript(ep, opener="")
    assert all(" dot " not in x for x in lines)


def test_the_opener_is_included_only_with_its_measured_minutes():
    ep = ts.series("n8n").episode(1)
    assert not any("in under" in x for x in lp.transcript(ep, opener=""))
    voiced = "[happy] Welcome back, guys! My name is Myron, and today in under six minutes."
    assert lp.transcript(ep, opener=voiced)[0] == ts.plain(voiced)
    # The channel welcome's opener names no minutes: always there.
    welcome = ts.series("channel").episode(1)
    assert lp.transcript(welcome, opener="")[0] == ts.INTRO_SAID


# ------------------------------------------------------------------ practice

def test_a_lab_episode_shows_its_practice_check():
    page = _lesson(_render(), "n8n-1")
    ep = ts.series("n8n").episode(1)
    lab = next(sc for sc in ep.scenes if sc.kind == "lab")
    do, expect, stuck, challenge = lab.items
    assert "Practice check" in page
    assert f"Your screen should show: {site._e(expect)}" in page
    for item in (do, stuck, challenge):
        assert site._e(item) in page
    for label in ts.LAB_LABELS:
        assert site._e(label) in page
    assert 'href="../../free/n8n-city-info.json"' in page


def test_no_lab_and_no_stuck_line_means_no_practice_section():
    page = _lesson(_render(), "claude-0")
    assert "Practice check" not in page


def test_lab_links_only_to_files_the_store_serves():
    files = _render()
    for v in ALL:
        page = _lesson(files, f"{v.series}-{v.episode}")
        for name in re.findall(r'href="\.\./\.\./free/([^"]+)"', page):
            assert f"{ts.LAB_DIR}/{name}" in files, name


# ------------------------------------------------------------------ resource

def test_the_matching_resource_is_lesson_products_card():
    cat = cat_mod.parse(_raw())
    page = _lesson(_render(), "n8n-1")
    slug, why = site.LESSON_PRODUCTS["n8n"]
    p = cat.product(slug)
    assert '<article class="card" data-paths=' in page
    assert f'href="../../{slug}/"' in page and site._e(p.title) in page
    assert site._e(why) in page
    assert 'class="card-foot"' in page


def test_no_paid_recommendation_when_mapped_so(monkeypatch):
    monkeypatch.setitem(site.LESSON_PRODUCTS, "n8n", ("", ""))
    page = _lesson(_render(), "n8n-1")
    assert "No paid recommendation" in page
    assert '<article class="card"' not in page
    assert 'href="../../academy/#finder"' in page


# ------------------------------------------------------------------ next lesson

def test_next_lesson_links_only_when_it_is_public():
    files = _render()
    # n8n #2 is public: #1 links to it.
    page = _lesson(files, "n8n-1")
    nxt = ts.series("n8n").episode(2)
    assert 'href="../../lessons/n8n-2/"' in page and site._e(nxt.title) in page
    # n8n #3 is not public: #2 names it with no link.
    page = _lesson(files, "n8n-2")
    later = ts.series("n8n").episode(3)
    assert f"Coming next: {site._e(later.opener_topic)}" in page
    assert "lessons/n8n-3/" not in page
    block = page.split("Coming next:", 1)[1].split("</p>", 1)[0]
    assert "href" not in block
    # Every lesson page links back to its series on /videos/.
    assert 'href="../../videos/#series-n8n"' in page


def test_no_lesson_page_links_a_lesson_page_that_does_not_exist():
    files = _render()
    for path, data in files.items():
        if not path.endswith(".html"):
            continue
        html = data.decode()
        depth = path.count("/")
        for href in re.findall(r'href="((?!https?:)(?:[^"#?]*/)?lessons/[a-z0-9]+-\d+/)"', html):
            parts = (path.rsplit("/", 1)[0] + "/" + href) if depth else href
            norm: list[str] = []
            for seg in parts.split("/"):
                if seg == "..":
                    norm.pop()
                elif seg and seg != ".":
                    norm.append(seg)
            assert "/".join(norm) + "/index.html" in files, (path, href)


# ------------------------------------------------------------------ the site

def test_the_sitemap_lists_the_lesson_pages():
    files = _render()
    sitemap = files["sitemap-academy.xml"].decode()
    for v in ALL:
        assert f"<loc>https://agency.storefront.test/lessons/{v.series}-{v.episode}/</loc>" in sitemap
    assert "lessons/n8n-3/" not in sitemap
    assert "/lessons/" not in _render([])["sitemap-academy.xml"].decode()


def test_videos_cards_link_their_lesson_page():
    page = _render()["videos/index.html"].decode()
    for v in ALL:
        card = page.split(f'id="v-{v.video_id}"', 1)[1].split("</article>", 1)[0]
        assert f'href="../lessons/{v.series}-{v.episode}/"' in card, v


def test_no_banned_word_and_no_new_colour():
    files = _render()
    for v in ALL:
        page = _lesson(files, f"{v.series}-{v.episode}")
        text = " ".join(_text(page.encode()).parts)
        for pattern in BANNED:
            assert not re.search(pattern, text, re.IGNORECASE), (v, pattern)
        assert "style=" not in page
        main = page.split('<main id="main">', 1)[1].split("</main>", 1)[0]
        assert not re.search(r"(?<!&)#[0-9a-fA-F]{3,6}\b", main), v


def test_json_ld_is_valid():
    page = _lesson(_render(), "n8n-1")
    body = page.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    data = json.loads(body)
    assert data["@type"] == "VideoObject"
    assert data["embedUrl"] == "https://www.youtube-nocookie.com/embed/AbCdEfGhIj1"
    assert data["duration"] == "PT6M52S"
    assert "duration" not in json.loads(
        _lesson(_render(), "n8n-2").split('<script type="application/ld+json">', 1)[1]
        .split("</script>")[0])


def test_the_transcript_is_what_the_published_video_says():
    """Script and manifest disagree: the page shows the manifest's lines."""
    said = ((0.0, 4.0, "[happy] Welcome back, guys! Today, in under seven minutes."),
            (4.0, 9.0, "A line the video says that the script no longer has."),
            (9.0, 12.0, "Go to agency dot storefront dot test [chuckling] for the file."))
    v = site.Video("n8n", 1, "AbCdEfGhIj1", N8N1.uploaded, seconds=412.0, timings=said)
    page = _lesson(_render([v]), "n8n-1")
    assert '<h2 id="transcript">Transcript</h2>' in page and lp.SAID_NOTE in page
    assert "Welcome back, guys! Today, in under seven minutes." in page
    assert "A line the video says that the script no longer has." in page
    assert "Go to agency.storefront.test for the file." in page
    assert "[happy]" not in page and "[chuckling]" not in page
    script_only = site._e(tutorials.shown(ts.series("n8n").episode(1).scenes[0].said[0]))
    assert script_only not in page.split('id="transcript"', 1)[1]


def _manifest(tmp_path, monkeypatch, series: str, n: int, **over) -> None:
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    s = ts.series(series)
    d = tutorials.episode_dir(s, s.episode(n))
    d.mkdir(parents=True, exist_ok=True)
    raw = {"series": series, "episode": n, "audio": "fish", "seconds": 300.0,
           "chapters": [[0, "Intro"], [40, "Build"]], "opener": "[happy] Hi.",
           "timings": [[0, 2, "[happy] Hi."], [2, 5, "Build it."]],
           "rendered_at": "2026-10-14T15:00:00+00:00"} | over
    (d / "manifest.json").write_text(json.dumps(raw))


def test_a_manifest_rendered_before_the_upload_is_trusted(tmp_path, monkeypatch):
    _manifest(tmp_path, monkeypatch, "n8n", 1)        # 15:00, uploaded 17:00
    [v] = publish._with_thumbs([site.Video("n8n", 1, "AbCdEfGhIj1", N8N1.uploaded)])
    assert v.seconds == 300.0
    assert v.chapters == ((0.0, "Intro"), (40.0, "Build")) and v.opener == "[happy] Hi."
    assert v.timings == ((0.0, 2.0, "[happy] Hi."), (2.0, 5.0, "Build it."))


@pytest.mark.parametrize("over", [
    {"rendered_at": "2026-10-14T17:30:00+00:00"},     # rendered after the upload
    {"rendered_at": ""},                               # doesn't say when
    {"audio": "placeholder"},                          # a preview
])
def test_an_untrusted_manifest_is_no_manifest(tmp_path, monkeypatch, over):
    _manifest(tmp_path, monkeypatch, "n8n", 1, **over)
    [v] = publish._with_thumbs([site.Video("n8n", 1, "AbCdEfGhIj1", N8N1.uploaded)])
    assert (v.seconds, v.chapters, v.opener, v.timings) == (None, (), "", ())
    page = _lesson(_render([v]), "n8n-1")
    assert 'class="vlen"' not in page and "?t=" not in page
    assert '<h2 id="transcript">Lesson script</h2>' in page


def test_new_manifest_data_changes_the_videos_manifest(tmp_path, monkeypatch):
    bare = site.Video("n8n", 1, "AbCdEfGhIj1", N8N1.uploaded)
    assert site.videos_manifest([bare]) == {"videos": [
        {"series": "n8n", "episode": 1, "video_id": "AbCdEfGhIj1"}]}
    _manifest(tmp_path, monkeypatch, "n8n", 1)
    first = site.videos_manifest(publish._with_manifests([bare]))
    assert first["videos"][0]["render"]
    _manifest(tmp_path, monkeypatch, "n8n", 1,
              chapters=[[0, "Intro"], [41, "Build"]])
    assert site.videos_manifest(publish._with_manifests([bare])) != first


async def test_refresh_republishes_when_a_lesson_render_changed(tmp_path, monkeypatch):
    bare = site.Video("n8n", 1, "AbCdEfGhIj1", N8N1.uploaded)
    _manifest(tmp_path, monkeypatch, "n8n", 1)

    async def today():
        return [bare]
    calls = []

    async def fake_execute(req):
        calls.append(req)
        from atlas.kernel.contracts import ActionResult
        return ActionResult(True, "published")
    from atlas.kernel import executor
    monkeypatch.setattr(executor, "execute", fake_execute)
    monkeypatch.setattr(publish, "public_videos", today)
    # The live page was built before this render's data: ids alone match.
    monkeypatch.setattr(publish, "_live_manifest", lambda: site.videos_manifest([bare]))
    assert await publish.refresh_videos() == "published" and len(calls) == 1


# ------------------------------------------------------------------ fix round 1

def test_a_mapped_title_not_on_sale_renders_nothing():
    page = _lesson(_render(raw=_off()), "n8n-1")
    assert 'id="resource"' not in page and "No paid recommendation" not in page
    assert '<article class="card"' not in page


def test_the_welcome_has_no_level_and_no_outcome_heading():
    page = _lesson(_render(), "channel-1")
    ep = ts.series("channel").episode(1)
    assert ep.level_label not in page
    card = page.split('<article class="vcard"', 1)[1].split("</article>", 1)[0]
    assert "<dt>Level</dt>" not in card
    assert "be able to do</h3>" not in page
    assert site._e(ep.summary) in page
    # A lesson keeps both.
    lesson = _lesson(_render(), "n8n-1")
    assert "<dt>Level</dt>" in lesson and "be able to do</h3>" in lesson


def test_one_series_name_in_the_crumbs_and_the_back_link():
    for key, s in (("channel-1", ts.series("channel")), ("n8n-1", ts.series("n8n"))):
        page = _lesson(_render(), key)
        name = site._e(lp.series_name(s))
        assert f">{name}</a></div>" in page
        assert f"All lessons in {name}</a>" in page
