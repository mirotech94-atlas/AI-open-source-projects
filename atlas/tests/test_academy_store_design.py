"""The store design brief of 2026-10-11 ("Learn something useful. Put it to
work."), stages 1-4, as checks on the built site (atlas/modules/academy/site.py).

* The bundle's arithmetic is summed from catalog prices at build time.
* No absolute safety promise anywhere on the store or in the catalog.
* Every title sits on at least one of the three learning paths.
* A title's format words agree with the files it delivers.
* Each scene (shelf, approval gate, kit) is complete static HTML and the
  motion honours prefers-reduced-motion.
* No third-party script, style, frame or image is loaded by any page.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser

from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import site

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)
LESSON = {"series": "agents", "episode": 1, "video_id": "abcDEF12345",
          "title": "What an AI agent is, in plain words",
          "summary": "An agent picks the next step. Yours should ask first. More text.",
          "mark": "AI Agents, Simply"}


def _raw() -> dict:
    return json.loads(cat_mod.CATALOG_PATH.read_text())


def _files(raw: dict | None = None, **kw) -> dict[str, bytes]:
    return site.render_root(cat_mod.parse(raw or _raw()), NOW, **kw)


def _pages(files: dict[str, bytes]) -> dict[str, str]:
    return {k: v.decode() for k, v in files.items() if k.endswith(".html")}


# ------------------------------------------------------------ bundle

def test_the_bundle_arithmetic_is_summed_from_catalog_prices():
    raw = _raw()
    by_id = {o["id"]: o["price_usd"] for p in raw["products"] for o in p["offers"]}
    parts = [by_id[i] for i in raw["bundle"]["includes"]]
    parts_, total, price, diff = site.bundle_math(cat_mod.parse(raw))
    assert parts_ == parts and total == sum(parts)
    assert price == raw["bundle"]["price_usd"] and diff == sum(parts) - price
    # As observed in the brief, while these prices stand: 20+9+19+39 = 87, 79, 8.
    assert (total, price, diff) == (87, 79, 8)
    home = _pages(_files())["academy/index.html"]
    terms = " + ".join(f"${p:g}" for p in parts)
    assert f"{terms} = <b>${total:g}</b> bought separately." in home
    assert f"${diff:g} less than buying them one by one" in home
    # A changed price changes the sentence: nothing is typed into the copy.
    raw["bundle"]["price_usd"] = 70
    home = _pages(_files(raw))["academy/index.html"]
    assert "The bundle is $70, $17 less" in home


def test_the_bundle_is_named_precisely_and_lists_what_it_leaves_out():
    cat = cat_mod.load()
    assert cat.bundle.title == "AI Foundations Bundle - 4 Titles"
    pages = _pages(_files())
    included = {o.product for o in cat.bundle_offers()}
    for path in ("academy/index.html", f"{cat.bundle.slug}/index.html"):
        page = pages[path]
        assert cat.bundle.title in page
        for p in cat.products:
            assert p.title in page, (path, p.title)
    assert "AI for Every Age (both editions)" in pages["academy/index.html"]
    left = [p.title for p in cat.products if p.slug not in included]
    assert len(left) == 3
    assert "Not included:</b> " + ", ".join(left) in pages["academy/index.html"]
    assert "whole library" not in " ".join(pages.values()).lower()


# ------------------------------------------------------------ claims

ABSOLUTE = [
    r"can['’]t burn", r"cannot burn", r"without your ok\b", r"can['’]t act without",
    r"can['’]t move money", r"can['’]t delete", r"\b100% safe", r"completely safe",
    r"perfectly safe", r"guaranteed (?:safe|protection|security)", r"never make mistakes",
    r"can['’]t be hacked",
]


def test_no_absolute_safety_claim_on_any_page_or_in_the_catalog():
    texts = dict(_pages(_files()))
    texts["catalog.json"] = cat_mod.CATALOG_PATH.read_text()
    for path, text in texts.items():
        for pattern in ABSOLUTE:
            assert not re.search(pattern, text, re.IGNORECASE), (path, pattern)


# ------------------------------------------------------------ paths

def test_every_title_is_on_at_least_one_learning_path():
    cat = cat_mod.load()
    assert [label for _k, label, *_ in site.PATHS] == [
        "Work & Business", "Build & Automate", "Home & Family"]
    home = _pages(_files())["academy/index.html"]
    for p in cat.products:
        assert site.product_paths(p), p.slug
        assert p.level, f"{p.slug} has no level words"
        card = home.split(f'data-slug="{p.slug}"', 1)[0].rsplit("<article", 1)[1]
        assert f'data-paths="{" ".join(site.product_paths(p))}"' in card
    # Three paths replace the old ten filter chips.
    assert home.count('class="chip" type="button" data-filter=') == 4


def test_the_nav_names_the_brief_sections():
    home = _pages(_files())["academy/index.html"]
    nav = home.split('<ul class="nav"', 1)[1].split("</ul>", 1)[0]
    labels = re.findall(r">([^<>]+)</a>", nav)
    assert labels == ["Agency", "Courses", "Ebooks", "Blog", "Free lessons", "Help"]
    assert 'href="/agency/"' in nav and 'href="../academy/#catalog"' in nav
    assert 'href="../videos/"' in nav and 'href="../help/"' in nav and 'href="../blog/"' in nav


# ------------------------------------------------------------ formats

WRITTEN = re.compile(r"written (?:course|edition)", re.IGNORECASE)


def test_format_statements_agree_with_the_delivered_files():
    cat = cat_mod.load()
    for p in cat.products:
        texts = [p.summary, p.intro] + [a for _q, a in p.faq] + [v for _k, v in p.label]
        assert not any(WRITTEN.search(t) for t in texts), p.slug
        for o in p.offers:
            has_video = any(f.label.endswith("(video)") for f in o.files)
            assert ("Video lessons" in site.formats([o])) == has_video, o.id
            if has_video and o.blurb:
                assert "video" in o.blurb.lower(), o.id
        video = any(f.label.endswith("(video)") for o in p.offers for f in o.files)
        stated = " ".join([p.summary] + [v for _k, v in p.label]).lower()
        assert ("video" in stated) == video, p.slug
    page = _pages(_files())["ai-for-every-age/index.html"]
    assert "Two video courses" in page and "Two written courses" not in page


# ------------------------------------------------------------ motion

def test_each_scene_is_complete_without_the_script_and_honours_reduced_motion():
    css = (site.ASSETS / "style.css").read_text()
    js = (site.ASSETS / "app.js").read_text()
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "prefers-reduced-motion: reduce" in js
    # Motion is event-driven (rAF-throttled pointer/scroll handlers): no timer loop.
    assert "setInterval" not in js
    home = _pages(_files())["academy/index.html"]
    # The approval gate: every step and its words are in the page, none hidden.
    gate = home.split("data-gate>", 1)[1].split("</ol>", 1)[0]
    for step in ("Request", "Draft", "Review", "After review"):
        assert f'<p class="step-name">{step}</p>' in gate
    assert "Paused for review. Nothing has been sent." in gate
    assert " hidden" not in gate
    # The catalog: every title is a plain link to its own page.
    cat = cat_mod.load()
    for p in cat.products:
        assert f'<a href="../{p.slug}/">{p.title}' in home.replace("&amp;", "&").replace("&#x27;", "'")
    # The kit: every delivered non-video file is named, tiles visible.
    page = _pages(_files())["safe-ai-operator/index.html"]
    kit = page.split('<div class="kit" data-kit>', 1)[1].split("</section>", 1)[0]
    assert "28 video lessons" in kit and "(PDF, 93 pages)" in kit and "(zip)" in kit
    assert "is-folded" not in kit


def test_prices_render_in_the_html_before_any_script():
    cat = cat_mod.load()
    home = _pages(_files())["academy/index.html"]
    grid = home.split('<div class="grid pv-grid">', 1)[1].split("</section>", 1)[0]
    for p in cat.products:
        assert site._price_text(p) in grid


# ------------------------------------------------------------ privacy

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
                self.refs.append((tag, name, a[name]))
        if tag in ("iframe", "embed", "object"):
            self.refs.append((tag, "tag", tag))


def test_no_page_loads_a_third_party_script_style_frame_or_image():
    files = _files(lessons=[LESSON])
    for path, html in _pages(files).items():
        parser = _Refs()
        parser.feed(html)
        for tag, name, value in parser.refs:
            assert tag not in ("iframe", "embed", "object"), path
            assert not re.match(r"(?:https?:)?//", value), (path, tag, value)
        # the privacy page names YouTube's image host in words (the home
        # gallery's pictures load from it, from its own script file); it is
        # never an address any page loads in its HTML
        if path != "privacy/index.html":
            assert "ytimg" not in html, path
        assert "youtube.com/embed" not in html, path
    css = files["assets/style.css"].decode()
    for url in re.findall(r"url\(([^)]*)\)", css):
        url = url.strip("'\"")
        assert url.startswith(("fonts/", "data:", "#")), url
    assert "@import" not in css


# ------------------------------------------------------------ lessons

def test_the_featured_lesson_shows_only_when_a_tutorial_is_published():
    plain = _pages(_files())["academy/index.html"]
    assert 'id="free-lesson"' not in plain
    assert 'href="../videos/">Watch a free lesson</a>' in plain
    shown = _pages(_files(lessons=[LESSON, {**LESSON, "video_id": "bad id!"}]))
    home = shown["academy/index.html"]
    assert 'id="free-lesson"' in home and LESSON["title"] in home
    assert 'href="https://www.youtube.com/watch?v=abcDEF12345"' in home
    assert "bad id" not in home
    # Its takeaway is the first sentences, and the next resource is the
    # series' product: the operator course for the agents series.
    assert "An agent picks the next step. Yours should ask first." in home
    assert 'class="next-card" href="../safe-ai-operator/"' in home
    assert 'href="#free-lesson">Watch a free lesson</a>' in home
    data = json.loads(home.split("window.FINDER = ", 1)[1].split(";</script>", 1)[0])
    assert data["lessons"]["build"]["href"].endswith("abcDEF12345")
    assert LESSON["title"] in shown["safe-ai-operator/index.html"]


def test_one_lesson_mapping_feeds_every_page_with_its_reason(monkeypatch):
    """Shop master plan F05: one table pairs a lesson series with a product
    and says why; the home page, the finder, the product page and the
    /videos/ card all agree. An MCP lesson no longer promotes the family
    course (plan finding [B])."""
    assert site.LESSON_PRODUCTS["claude"][0] != "ai-for-every-age"
    claude = {**LESSON, "series": "claude", "episode": 1, "mark": "Claude Mastery",
              "title": "Claude MCP Tutorial"}
    slug, why = site.LESSON_PRODUCTS["claude"]
    video = site.Video("claude", 1, "abcDEF12345", NOW)
    pages = _pages(_files(lessons=[claude], videos=[video]))
    home = pages["academy/index.html"]
    assert f'class="next-card" href="../{slug}/"' in home
    assert why in home.replace("&#x27;", "'")
    data = json.loads(home.split("window.FINDER = ", 1)[1].split(";</script>", 1)[0])
    paths = set(site.product_paths(cat_mod.load().product(slug)))
    assert set(data["lessons"]) == paths
    assert claude["title"] in pages[f"{slug}/index.html"]
    assert claude["title"] not in pages["ai-for-every-age/index.html"]
    card = pages["videos/index.html"].split('id="v-abcDEF12345"', 1)[1]
    assert f'href="../{slug}/"' in card.split("</article>", 1)[0]
    # "No paid recommendation": the lesson stands alone and the finder is offered.
    monkeypatch.setitem(site.LESSON_PRODUCTS, "claude", ("", ""))
    home = _pages(_files(lessons=[claude]))["academy/index.html"]
    assert 'class="next-card"' not in home and "No paid title follows" in home


def test_build_passes_reviews_and_lessons_through(tmp_path):
    out = tmp_path / "site"
    site.build(out, now=NOW, lessons=[LESSON],
               reviews=[{"slug": "30-business-lessons", "stars": 5, "name": "",
                         "text": "Good.", "month": "October 2026"}])
    assert LESSON["title"] in (out / "academy/index.html").read_text()
    assert "Verified buyer" in (out / "academy/30-business-lessons/index.html").read_text()


# ------------------------------------------------------------ buying

def test_editions_are_explicit_choices_each_with_its_own_summary():
    cat = cat_mod.load()
    page = _pages(_files())["sell-with-ai/index.html"]
    p = cat.product("sell-with-ai")
    for o in p.offers:
        assert f'name="edition" value="{o.id}"' in page
        summary = page.split(f'<div class="summary" data-offer="{o.id}">', 1)[1].split(
            "</div>\n", 1)[0]
        assert cat_mod.money(o.price_usd) in summary and o.name in summary
        assert o.payment_link in page.split(f'data-offer="{o.id}">', 1)[1].split(
            '<div class="summary"', 1)[0]
    assert "works for 7 days" in page and "fresh links" in page


def test_every_product_page_answers_the_four_questions_before_payment():
    """Plan F07/F09: what you receive, can you run it, what else costs money,
    how you recover access -- from the catalog's own text, on every page,
    and no invented cost: a title without a cost row says none is listed."""
    pages = _pages(_files())
    cat = cat_mod.load()
    for p in cat.products:
        page = pages[f"{p.slug}/index.html"]
        block = page.split('<section id="before"', 1)[1].split("</section>", 1)[0]
        for q in ("What do I receive?", "Can I run it?", "What else costs money?",
                  "How do I get my files back later?"):
            assert q in block, (p.slug, q)
        assert '<a href="#before">Before you pay</a>' in page
        assert escape(p.level_detail, quote=True) in block
        costs = [v for k, v in p.label if k.lower() in site.COST_ROWS]
        if costs:
            assert all(escape(v, quote=True) in block for v in costs)
            assert "Nothing else from ATLAS Academy" not in block
        else:
            assert "Nothing else from ATLAS Academy" in block, p.slug
        assert "../recover/" in block and "works for 7 days" in block
    # Works on Claude's free plan: the catalog's own words, on AI for Every Age.
    assert "Claude plan:" in pages["ai-for-every-age/index.html"].split(
        'id="before"', 1)[1].split("</section>", 1)[0]


def test_no_edition_is_chosen_for_the_visitor():
    """Plan F10: a "From" price never sits over a dearer edition already
    selected. No radio starts checked; the script shows a prompt and no Buy
    until one is picked; each edition is addressable as #<offer id>, which
    is what the public catalog.json links to."""
    files = _files()
    pages = _pages(files)
    public = json.loads(files["catalog.json"])
    for p in cat_mod.load().products:
        page = pages[f"{p.slug}/index.html"]
        if len(p.offers) < 2:
            assert 'name="edition"' not in page
            continue
        fieldset = page.split('<fieldset class="editions">', 1)[1].split("</fieldset>", 1)[0]
        assert "checked" not in fieldset, p.slug
        assert "data-ed-prompt hidden" in page
        for o in p.offers:
            assert f'<label class="edition" id="{o.id}">' in fieldset
            entry = next(e for e in public["products"] if e["slug"] == o.id)
            assert entry["url"].endswith(f"/{p.slug}/#{o.id}")
    js = (site.ASSETS / "app.js").read_text()
    assert "prompt.hidden = !!r" in js and "window.location.hash.slice(1)" in js


def test_the_help_page_is_the_support_lane_s_and_in_the_sitemap():
    """The /help/ placeholder is replaced by support_site.files (2026-10-11)."""
    from atlas.modules.academy import support_site
    files = _files()
    assert "help/index.html" in files and "assets/support-chat.js" not in files
    lane = support_site.files(cat_mod.load())["help/index.html"].decode()
    assert files["help/index.html"].decode() == site._with_canonical(
        lane, "https://agency.storefront.test/help/")
    assert "<loc>https://agency.storefront.test/help/</loc>" in files["sitemap-academy.xml"].decode()
    assert "SUPPORT_SITE:" not in (site.Path(site.__file__)).read_text()


def test_a_founder_photo_appears_only_when_supplied(tmp_path, monkeypatch):
    home = _pages(_files())["academy/index.html"]
    assert 'class="portrait monogram"' in home and "Meet Miro" in home
    raw = copy.deepcopy(_raw())
    raw["store"]["founder_photo"] = "no-such-photo.jpg"
    assert 'class="portrait monogram"' in _pages(_files(raw))["academy/index.html"]


def test_the_about_section_speaks_in_miros_first_person():
    """Miro, 2026-10-18: the About is his own voice, with every fact kept."""
    home = _pages(_files())["academy/index.html"]
    about = home[home.index('id="about"'):home.index('class="inspect"')]
    assert "I'm Miro (Myron Mekhael), and I write and run ATLAS Academy." in about
    assert "I run ATLAS, my own AI agent, on the rules I teach in" in about
    assert "picks from a closed list of actions" in about and "until I decide." in about
    assert "How I teach: every method is shown on a working example" in about
    assert "who it's for and who it isn't" in about and "before you buy" in about
    assert "I read the counts on these pages from the files on 14 Nov 2026." in about
    assert "Write to me at" in about and "I read every message myself." in about
    for third in ("He runs", "his own", "written and run by", "reach Miro",
                  "a person decides"):
        assert third not in about


# ------------------------------------------------------------ the logo

def test_every_page_carries_the_grid_a0_favicon_and_mark(monkeypatch):
    """Miro, 2026-10-16: the Grid A0 logo everywhere the store shows one. Every
    page links the SVG favicon and the PNG app icons, and draws the edged mark
    in its header and footer; the files they name are in the build."""
    from atlas.modules.academy import logo
    monkeypatch.setenv(site.START_ENV, "on")
    videos = [site.Video("n8n", 1, "AbCdEfGhIj1", NOW)]
    files = _files(lessons=[LESSON], videos=videos)
    pages = _pages(files)
    assert any(p.startswith("lessons/") for p in pages) and "help/index.html" in pages
    for path, html in pages.items():
        for href in ("assets/icon.svg", "assets/icon-192.png", "assets/icon-512.png",
                     "assets/apple-touch-icon.png"):
            assert f'{"../" * path.count("/")}{href}"' in html, (path, href)
        assert 'rel="apple-touch-icon"' in html, path
        if '<header class="top">' in html:
            head = html.split('<header class="top">', 1)[1].split("</header>", 1)[0]
            foot = html.split('<footer class="foot">', 1)[1]
            assert site.BRAND_SVG in head and site.BRAND_SVG in foot, path
    for name in ("icon.svg", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        assert files[f"assets/{name}"], name
    assert files["assets/icon.svg"].decode() == logo.mark_svg(dark=True)
    # the header mark is the logo's own paths, edged for the dark store
    assert logo.A_PATH in site.BRAND_SVG and logo.ZERO_PATH in site.BRAND_SVG
    assert f'stroke="{logo.EDGE}"' in site.BRAND_SVG and 'aria-hidden="true"' in site.BRAND_SVG
    assert "<circle" not in site.BRAND_SVG          # the interim ring-and-A is gone


# ------------------------------------------------------------ recover (F17)

def test_the_recover_page_is_an_email_link_and_is_linked():
    """Plan F17, local-only since 2026-10-29: the buyer emails the receipt
    number; no form posts anywhere. Linked from the footer, Help, Contact and
    the buying steps."""
    pages = _pages(_files())
    page = re.sub(r"<form[^>]*data-sub-form.*?</form>", "", pages["recover/index.html"], flags=re.DOTALL)
    assert "<form" not in page and "/public/" not in page
    assert 'href="mailto:owner@example.com?subject=' in page
    assert "<script" not in page.split("<main", 1)[-1].split("</main>", 1)[0]
    assert "Where is the receipt number?" in page
    assert '<a href="../recover/">Here' in pages["help/index.html"]
    assert "../recover/" in pages["contact/index.html"]
    home = pages["academy/index.html"]
    assert 'href="../recover/">Lost your access page?</a>' in home       # footer
    steps = home.split('<ol class="steps">', 1)[1].split("</ol>", 1)[0]
    assert 'href="../recover/"' in steps
    files = _files()
    assert b"/recover/</loc>" in files["sitemap-academy.xml"]
