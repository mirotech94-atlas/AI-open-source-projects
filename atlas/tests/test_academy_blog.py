"""The ATLAS Academy blog (atlas/modules/academy/blog_pages.py, mdlite.py).

Built by the same pure `site.render`, inside the store's chrome; drafts never
reach any file; the "Pairs with" line names only a catalog product on sale;
the player is the /videos/ one (store-served poster, youtube-nocookie loaded
on click) and only for one of ATLAS's own published tutorials; no external
script or image; the one script makes no request and keeps only the
finished-lesson ticks, in localStorage, inside try/catch.
"""
from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from html.parser import HTMLParser

from atlas.modules.academy import blog_pages as bp
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import mdlite, site

NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)
CAT = cat_mod.load()
WELCOME = site.Video("channel", 1, "uPzByskDRXw", datetime(2026, 10, 11, 1, 48, tzinfo=UTC))
N8N = site.Video("n8n", 1, "aMcqs_wonuk", datetime(2026, 10, 13, 4, 22, tzinfo=UTC))
BUSINESS = site.Video("business", 1, "JK-Iic0JX0c", datetime(2026, 10, 14, 13, 2, tzinfo=UTC))
VIDEOS = [WELCOME, N8N, BUSINESS]


def _raw() -> dict:
    return json.loads(cat_mod.CATALOG_PATH.read_text())


def _post(**kw: object) -> bp.Post:
    base: dict = {"slug": "a-test-post", "title": "A test post", "day": date(2026, 10, 18),
                  "summary": "Plain words about nothing in particular.",
                  "body": "Hello.\n\n## Part\n\nMore words."}
    base.update(kw)
    return bp.Post(**base)


def _render(raw: dict | None = None, **kw: object) -> dict[str, bytes]:
    cat = cat_mod.parse(raw) if raw is not None else CAT
    return site.render_root(cat, NOW, **kw)


def _blog(posts: list[bp.Post], videos: list[site.Video] | None = None,
          cat: cat_mod.Catalog | None = None) -> dict[str, str]:
    return {k: v.decode() for k, v in bp.files(cat or CAT, videos, posts).items()}


# ------------------------------------------------------------------ build

def test_the_blog_pages_build_from_the_committed_posts():
    files = _render()
    live = bp.live(CAT)
    assert live, "no live post"
    for path in ("blog/index.html", "blog/feed.xml", "assets/data/posts.json",
                 "assets/blog.css", "assets/blog.js"):
        assert path in files, path
    index = files["blog/index.html"].decode()
    for p in live:
        page = files[f"blog/{p.slug}/index.html"].decode()
        assert f"<h1>{site._e(p.title)}</h1>" in page
        assert f'href="{p.slug}/"' in index
        assert 'aria-current="page">Blog<' in page
    data = json.loads(files["assets/data/posts.json"])
    assert [d["slug"] for d in data] == [p.slug for p in live]
    assert all(d["url"] == f"/blog/{d['slug']}/" for d in data)


LIVE_POSTS = {
    "ai-vulnerabilities-protect-yourself", "how-a-lesson-gets-made",
    "ml-deep-learning-generative-ai-explained", "where-ai-is-going-what-to-learn",
    "who-i-am", "why-every-workflow-pauses",
}


def test_every_committed_post_parses_and_all_six_are_live():
    """Miro, 2026-10-18: why-every-workflow-pauses is live, its video removed."""
    posts = bp.load()
    assert len(posts) == 6
    assert not [p.slug for p in posts if p.draft]
    assert {p.slug for p in bp.live(CAT)} == LIVE_POSTS


def test_the_approval_pause_post_points_to_the_free_lessons_not_a_player():
    post = next(p for p in bp.load() if p.slug == "why-every-workflow-pauses")
    assert not post.video
    assert "I woke up to a return request." in post.body
    assert "one-star" not in post.body and "video above" not in post.body
    page = _render(videos=VIDEOS)["blog/why-every-workflow-pauses/index.html"].decode()
    assert 'href="../../videos/"' in page
    assert "<iframe" not in page and "blog-player" not in page


def test_no_live_post_says_miro_records_or_narrates_the_lessons():
    """Miro, 2026-10-18: the lessons are narrated by a disclosed AI voice and
    made by an automated pipeline; nothing is marked verified on camera."""
    claims = [r"\bme, a microphone\b", r"\bI'?m recording\b", r"\bI talk\b", r"\bI speak\b",
              r"\bsee me\b", r"\bon camera\b", r"\bfilming\b", r"\bre-record",
              r"fix verified", r"\bwithin a day\b"]
    for p in bp.live(CAT):
        text = f"{p.title} {p.summary} {p.body}"
        for pattern in claims:
            assert not re.search(pattern, text, re.IGNORECASE), (p.slug, pattern)


def test_drafts_reach_no_file_at_all():
    files = _render(videos=VIDEOS)
    drafts = [p for p in bp.load() if p.draft]
    blob = b"\n".join(files.values())
    for p in drafts:
        assert f"blog/{p.slug}/index.html" not in files
        assert p.slug.encode() not in blob, p.slug
        assert p.title.encode() not in blob, p.title
    # and a fixture draft beside a live post
    out = _blog([_post(), _post(slug="secret-draft", title="Secret draft", draft=True)])
    assert "blog/secret-draft/index.html" not in out
    assert not any("Secret draft" in v or "secret-draft" in v for v in out.values())


# ------------------------------------------------------------------ chrome

def test_every_page_has_blog_in_the_shared_nav():
    for path, html in _render(videos=VIDEOS).items():
        if not path.endswith(".html") or '<ul class="nav"' not in html.decode():
            continue
        nav = html.decode().split('<ul class="nav"', 1)[1].split("</ul>", 1)[0]
        labels = re.findall(r">([^<>]+)</a>", nav)
        assert labels.index("Blog") == labels.index("Ebooks") + 1, path
        assert labels.index("Free lessons") == labels.index("Blog") + 1, path
        root = "/" if path == "404.html" else "../" * path.count("/")
        assert f'href="{root}blog/"' in nav, path


def test_the_home_page_head_links_the_feed():
    home = _render()["academy/index.html"].decode()
    head = home.split("</head>", 1)[0]
    assert ('<link rel="alternate" type="application/rss+xml" title="ATLAS Academy blog" '
            'href="../blog/feed.xml">') in head


def test_the_feed_is_valid_rss_of_the_live_posts():
    files = _render()
    root = ET.fromstring(files["blog/feed.xml"])
    assert root.tag == "rss" and root.get("version") == "2.0"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("link") == f"{CAT.base_url}/blog/"
    items = channel.findall("item")
    live = bp.live(CAT)
    assert [i.findtext("link") for i in items] == [f"{CAT.base_url}/blog/{p.slug}/"
                                                 for p in live]
    for i, p in zip(items, live, strict=True):
        assert i.findtext("title") == p.title and i.findtext("description") == p.summary
        assert i.findtext("guid") == i.findtext("link")
        assert i.findtext("pubDate", "").endswith("+0000")


def test_the_sitemap_and_llms_list_live_posts_only():
    files = _render()
    sitemap = files["sitemap-academy.xml"].decode()
    llms = files["academy/llms.txt"].decode()
    assert f"{CAT.base_url}/blog/</loc>" in sitemap
    for p in bp.load():
        url = f"{CAT.base_url}/blog/{p.slug}/"
        assert (url in sitemap) is (not p.draft), p.slug
        assert (url in llms) is (not p.draft), p.slug


# ------------------------------------------------------------------ kits

def test_every_kit_map_rule_names_a_catalog_product():
    kits = bp.kit_map(CAT)
    assert kits.dropped == ()
    assert len(kits.rules) == 7
    assert all(CAT.product(r.product) is not None for r in kits.rules)


def test_a_kit_map_rule_for_a_product_the_catalog_lacks_is_dropped(tmp_path):
    path = tmp_path / "kit_map.json"
    path.write_text(json.dumps({"rules": [
        {"keywords": ["ghost"], "product": "no-such-title"},
        {"keywords": ["scam"], "product": "family-ai-scam-defence-workbook"}]}))
    kits = bp.kit_map(CAT, path)
    assert kits.dropped == ("no-such-title",)
    assert [r.product for r in kits.rules] == ["family-ai-scam-defence-workbook"]
    assert bp.pairing(CAT, _post(title="A ghost story"), {}, kits) is None


def test_kit_lines_name_only_live_catalog_products_by_their_catalog_url():
    files = _render(videos=VIDEOS)
    seen = 0
    for path, html in files.items():
        if not path.startswith("blog/") or not path.endswith("index.html"):
            continue
        for block in re.findall(r'<aside class="blog-kit".*?</aside>', html.decode(), re.DOTALL):
            seen += 1
            slugs = set(re.findall(r'href="\.\./\.\./([a-z0-9-]+)/"', block))
            assert len(slugs) == 1, block
            p = CAT.product(slugs.pop())
            assert p is not None and any(o.buy_live for o in p.offers)
            assert site._e(p.title) in block and site._e(p.summary) in block
    assert seen, "no kit line rendered"


def test_a_post_kit_must_resolve_or_the_line_is_omitted():
    good = _post(kit="Anything", kit_url="/safe-ai-operator/")
    bad = _post(slug="bad-kit", kit="Imaginary Course", kit_url="/imaginary-course/",
                title="About approval and scams")
    out = _blog([good, bad])
    assert 'href="../../safe-ai-operator/"' in out["blog/a-test-post/index.html"]
    page = out["blog/bad-kit/index.html"]
    assert "blog-kit" not in page and "Imaginary Course" not in page
    # no fall-through to the keyword table either
    assert bp.pairing(CAT, bad, {}) is None


def test_no_kit_line_and_no_guarantee_while_nothing_is_on_sale():
    raw = copy.deepcopy(_raw())
    for p in raw["products"]:
        p["refund"] = None
        for o in p["offers"]:
            o["payment_link"] = ""
            o["stripe_price_id"] = ""
    raw["bundle"]["payment_link"] = ""
    raw["bundle"]["stripe_price_id"] = ""
    cat = cat_mod.parse(raw)
    out = _blog([_post(kit="x", kit_url="/safe-ai-operator/"),
                 _post(slug="by-keyword", title="Scam safety for parents")], cat=cat)
    for page in out.values():
        assert "blog-kit" not in page and "guarantee" not in page.lower()


def test_a_post_that_names_the_guarantee_waits_for_every_refund_term():
    post = _post(body="There's a 14-day guarantee if it isn't for you.")
    assert bp.live(CAT, [post]) == [post]          # every product says it today
    raw = copy.deepcopy(_raw())
    raw["products"][0]["refund"] = "Refunds by email within 7 days."
    assert bp.live(cat_mod.parse(raw), [post]) == []


def test_the_kit_line_states_the_guarantee_only_from_the_products_own_terms():
    out = _blog([_post(kit="x", kit_url="/safe-ai-operator/")])
    p = CAT.product("safe-ai-operator")
    assert p is not None
    page = out["blog/a-test-post/index.html"]
    assert (site.GUARANTEE in page) is site._guarantee(p)


# ------------------------------------------------------------------ honesty

def test_the_store_scans_cover_the_blog_pages():
    from test_academy_site import BANNED, _pages, _text
    from test_academy_store_design import ABSOLUTE
    pages = _pages(_render(videos=VIDEOS))
    blog = [k for k in pages if k.startswith("blog/")]
    assert "blog/index.html" in blog and len(blog) == 1 + len(bp.live(CAT))
    for path in blog:
        text = " ".join(_text(pages[path]).parts)
        for pattern in BANNED + ABSOLUTE:
            assert not re.search(pattern, text, re.IGNORECASE), (path, pattern)
    # the scan catches the sentence that held why-every-workflow-pauses back
    assert any(re.search(pat, "a return request and a one-star rating", re.IGNORECASE)
               for pat in BANNED)


def test_no_reading_time_or_other_estimate_is_shown():
    for path, html in _render().items():
        if path.startswith("blog/") and path.endswith(".html"):
            assert "min read" not in html.decode(), path


class _Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and a.get("rel") == "canonical":
            return  # names the page's own address; loads nothing
        for name in ("src", "href", "srcset", "data", "poster"):
            if a.get(name) and not (tag == "a" and name == "href"):
                self.refs.append((tag, name, a[name] or ""))
        if tag in ("iframe", "embed", "object"):
            self.refs.append((tag, "tag", tag))


def test_no_external_script_style_or_hot_linked_image_on_a_blog_page():
    post = _post(video=N8N.video_id)
    files = {**_render(videos=VIDEOS), **bp.files(CAT, VIDEOS, [post])}
    for path, html in files.items():
        if not (path.startswith("blog/") and path.endswith(".html")):
            continue
        page = html.decode()
        parser = _Refs()
        parser.feed(page)
        for tag, name, value in parser.refs:
            assert tag not in ("iframe", "embed", "object"), path
            assert not re.match(r"(?:https?:)?//", value), (path, tag, value)
        assert "ytimg" not in page and "youtube.com/embed" not in page, path
        srcs = re.findall(r'<script[^>]*\ssrc="([^"]+)"', page)
        assert [s.rsplit("/", 1)[1] for s in srcs] == ["app.js", "blog.js"], path
    css = files["assets/blog.css"].decode()
    assert "url(" not in css and "@import" not in css


def test_blog_js_makes_no_request_and_keeps_only_ticks_inside_try():
    js = (site.ASSETS / "blog.js").read_text()
    for word in ("fetch", "XMLHttpRequest", "sendBeacon", "sessionStorage",
                 "document.cookie", "http", "indexedDB", "import("):
        assert word not in js, word
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)
    uses = [m.start() for m in re.finditer(r"localStorage", code)]
    assert len(uses) == 3                        # getItem, setItem, removeItem
    for at in uses:
        before = code[:at]
        assert before.rfind("try {") > before.rfind("} catch"), at
    assert code.count("try {") == code.count("catch (e)") == 2
    assert "theme" not in js.lower()             # the store is dark only


def test_the_privacy_page_says_what_the_blog_keeps():
    privacy = _render()["privacy/index.html"].decode()
    assert "The blog's script" in privacy and "local storage" in privacy


# ------------------------------------------------------------------ player

def test_a_post_video_plays_click_to_load_from_youtube_nocookie():
    post = _post(video=N8N.video_id)
    out = bp.files(CAT, VIDEOS, [post])
    page = out["blog/a-test-post/index.html"].decode()
    player = page.split('<div class="vcard blog-player"', 1)[1].split("</div>", 1)[0]
    assert (f'data-embed="https://www.youtube-nocookie.com/embed/{N8N.video_id}'
            '?autoplay=1&amp;rel=0"') in player
    assert f'href="https://youtu.be/{N8N.video_id}"' in player
    assert f'src="../../assets/videos/{N8N.video_id}.svg"' in player
    assert "<iframe" not in page
    assert f"assets/videos/{N8N.video_id}.svg" in _render(videos=VIDEOS)
    js = (site.ASSETS / "app.js").read_text()
    assert "a.yt[data-embed]" not in js  # the card opens YouTube itself
    ld = json.loads(page.split('<script type="application/ld+json">', 1)[1]
                    .split("</script>", 1)[0])
    assert ld["@type"] == "BlogPosting"
    assert ld["video"]["embedUrl"].startswith("https://www.youtube-nocookie.com/")


def test_a_video_that_is_not_one_of_our_published_tutorials_gets_no_player():
    post = _post(video="dQw4w9WgXcQ")
    page = bp.files(CAT, VIDEOS, [post])["blog/a-test-post/index.html"].decode()
    assert "blog-player" not in page and "dQw4w9WgXcQ" not in page
    # with no published videos (a plain build) no post has a player
    page = bp.files(CAT, None, [_post(video=N8N.video_id)])
    assert "blog-player" not in page["blog/a-test-post/index.html"].decode()


def test_latest_from_the_channel_is_the_videos_list_newest_first():
    index = _render(videos=VIDEOS)["blog/index.html"].decode()
    channel = index.split('id="channel-h"', 1)[1]
    ids = re.findall(r'data-video="([^"]+)"', channel)
    assert ids == [BUSINESS.video_id, N8N.video_id, WELCOME.video_id]
    assert 'href="../lessons/business-1/"' in channel
    assert f'src="../assets/videos/{BUSINESS.video_id}.svg"' in channel
    empty = _render()["blog/index.html"].decode()
    assert "No lesson is published here yet" in empty


# ------------------------------------------------------------------ markdown

def test_markdown_escapes_everything_and_keeps_only_safe_links():
    html = mdlite.to_html(
        "# Head\n\nA <script>x</script> & [ok](/safe-ai-operator/) "
        "[bad](javascript:alert) [mail](mailto:a@b.c) [web](https://x.y/) "
        "[plain](http://x.y/) `**not bold**` **bold** *em*\n\n"
        "- [ ] a box\n- [x] done\n\n1. one\n2. two\n\n> quoted\n\n"
        "```\n<b>code</b>\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n", root="../../")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<h2>Head</h2>" in html
    assert '<a href="../../safe-ai-operator/">ok</a>' in html
    assert "javascript:" not in html and ">bad<" not in html and " bad " in html
    assert 'href="mailto:a@b.c"' in html and 'href="https://x.y/" rel="noopener"' in html
    assert 'href="http://x.y/"' not in html
    assert "<code>**not bold**</code>" in html
    assert "<strong>bold</strong>" in html and "<em>em</em>" in html
    assert html.count('<li class="md-task') == 2 and "md-done" in html
    assert "<ol><li>one</li><li>two</li></ol>" in html
    assert "<blockquote><p>quoted</p></blockquote>" in html
    assert "<pre><code>&lt;b&gt;code&lt;/b&gt;</code></pre>" in html
    assert "<th>A</th>" in html and "<td>2</td>" in html


def test_a_post_without_its_front_matter_is_refused():
    import pytest
    with pytest.raises(ValueError):
        bp.parse("no front matter")
    with pytest.raises(ValueError):
        bp.parse("---\ntitle: T\nslug: Bad Slug\ndate: 2026-10-18\nsummary: s\n---\nx")
    with pytest.raises(ValueError):
        bp.parse("---\ntitle: T\nslug: ok\ndate: 2026-10-18\nsummary: s\n"
                 "video: not an id!\n---\nx")
