"""The ATLAS Academy storefront (atlas/modules/academy/site.py).

A page per product from the one catalog; nothing on any page that the catalog
doesn't say -- no review, rating, count of buyers or countdown, and no number
that isn't catalog text; a Buy button only with a payment link AND a price;
one first-party script that makes no request and stores nothing (plus, on
the home page, one inline script that only hands it the finder's data); and
a public catalog.json the reels lane can read without learning a payment
link, a file path or a Stripe id.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import UTC, date, datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import pytest

from atlas.modules import store_origin
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import site

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
LANDING = Path(__file__).resolve().parents[1] / "atlas/modules/agency/landing"


def _raw() -> dict:
    return json.loads(cat_mod.CATALOG_PATH.read_text())


def _render(raw: dict | None = None) -> dict[str, bytes]:
    return site.render_root(cat_mod.parse(raw or _raw()), NOW)


def _off(raw: dict | None = None) -> dict:
    """The catalog with nothing on sale: no links, no price ids, no refund
    terms -- the launch-day baseline the buy-button tests reason from."""
    raw = copy.deepcopy(raw or _raw())
    for p in raw["products"]:
        p["refund"] = None
        for o in p["offers"]:
            o["payment_link"] = ""
            o["stripe_price_id"] = ""
    raw["bundle"]["payment_link"] = ""
    raw["bundle"]["stripe_price_id"] = ""
    return raw


class _Text(HTMLParser):
    """The page's words: text, plus alt / aria-label / meta content / title
    attributes. `scripts` is every <script> tag with its src and its body."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.scripts: list[tuple[str, str]] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.scripts.append((attrs.get("src") or "", ""))
            self._in_script = True
        if tag == "meta" and attrs.get("name") == "theme-color":
            return                          # a colour, not a figure
        for name, value in attrs.items():
            if name in ("alt", "aria-label", "content", "title") and value:
                self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            src, body = self.scripts[-1]
            self.scripts[-1] = (src, body + data)
        self.parts.append(data)


def _text(html: bytes) -> _Text:
    parser = _Text()
    parser.feed(html.decode())
    return parser


def _pages(files: dict[str, bytes]) -> dict[str, bytes]:
    return {k: v for k, v in files.items() if k.endswith(".html")}


def _live(raw: dict, offer_id: str, *, price=None, link="https://buy.stripe.com/test_abc",
          price_id="price_TEST123456") -> dict:
    raw = copy.deepcopy(raw)
    for p in raw["products"]:
        for o in p["offers"]:
            if o["id"] == offer_id:
                o["payment_link"] = link
                o["stripe_price_id"] = price_id
                if price is not None:
                    o["price_usd"] = price
    return raw


# ------------------------------------------------------------------ pages

def test_every_product_gets_its_own_page_and_the_legal_pages_exist():
    files = _render()
    raw = _raw()
    for p in raw["products"]:
        page = files[f"{p['slug']}/index.html"].decode()
        assert p["title"] in page
        assert "Contents" in page and "What's inside" in page
        assert "Pairs well with" in page
    for path in ("academy/index.html", "all-four/index.html", "terms/index.html",
                 "privacy/index.html", "refunds/index.html", "contact/index.html",
                 "404.html", "catalog.json"):
        assert path in files
    assert "index.html" not in files      # the root belongs to the agency landing
    assert raw["store"]["contact_email"] in files["contact/index.html"].decode()


def test_the_real_contents_are_on_the_page():
    files = _render()
    operator = files["safe-ai-operator/index.html"].decode()
    assert "93 pages, A4" in operator and "28 lessons in 10 modules" in operator
    assert "Silence is never consent" in operator
    aiea = files["ai-for-every-age/index.html"].decode()
    assert "182 pages" in aiea and "87 pages" in aiea
    assert "Sold to adults 18+" in aiea and "Can my child use Claude?" in aiea
    assert "14-day money-back guarantee" in aiea and "one household" in aiea.lower()


def test_the_vault_kit_cover_is_drawn_from_its_own_files():
    files = _render()
    svg = files["assets/covers/claude-obsidian-vault-kit.svg"].decode()
    assert "Prompt library.md" in svg and "CLAUDE.md" in svg


def test_the_arabic_edition_is_not_sold():
    files = _render()
    page = files["30-business-lessons/index.html"].decode()
    assert "not for sale yet" in page
    assert "-ar.pdf" not in json.dumps(_raw())


# ------------------------------------------------------------------ honesty

BANNED = [
    r"\btestimonials?\b", r"\breviewed by\b(?! me\b)", r"\bratings?\b", r"\brated\b",
    r"\bstars?\b", r"[★☆]", r"\d(?:\.\d)?\s*/\s*5\b", r"\bbest[- ]?sell",
    r"\blimited time\b", r"\bcountdown\b", r"\bhurry\b", r"\bonly \d+ left\b",
    r"\bends (?:soon|tonight|today)\b",
    (r"\b\d[\d,]*\+?\s+(?:reviews|customers|students|"
     r"buyers|sales|sold|downloads|happy)\b"),
    r"\bsave \$", r"\bwas \$",
    r"\bjoin (?:thousands|hundreds)\b", r"\btrusted by\b",
]


def test_no_page_carries_reviews_ratings_counts_of_buyers_or_urgency():
    for path, html in _pages(_render()).items():
        text = " ".join(_text(html).parts)
        for pattern in BANNED:
            assert not re.search(pattern, text, re.IGNORECASE), (path, pattern)


def test_every_page_runs_app_js_and_nothing_else():
    """One <script src=assets/app.js> per page; the home page alone adds one
    inline script, and it does nothing but assign the finder's data. The
    contact and Help pages and the blog each add their own first-party script file."""
    for path, html in _pages(_render()).items():
        root = "/" if path == "404.html" else "../" * (path.count("/"))
        scripts = _text(html).scripts
        assert scripts[0] == (f"{root}assets/app.js", ""), path
        # schema.org records (2026-10-06) are data, never run: each body is a
        # JSON object naming schema.org, and they are set aside here.
        inline = []
        for src, body in scripts[1:]:
            text = body.strip()
            if text.startswith("{") and '"https://schema.org"' in text:
                assert json.loads(text)["@context"] == "https://schema.org", path
                continue
            inline.append((src, body))
        if path.startswith("blog/"):
            # the blog's own first-party script (blog_pages.py), nothing inline
            assert inline == [(f"{root}assets/blog.js", "")], path
            continue
        if path in ("contact/index.html", "help/index.html"):
            # the contact forms' own first-party script (assets/contact.js)
            assert inline == [(f"{root}assets/contact.js", "")], path
            continue
        if path != "academy/index.html":
            assert inline == [], path
            continue
        # the home page's video gallery has its own first-party script file
        # (assets/gallery.js, tests/test_academy_videos_api.py)
        assert (f"{root}assets/gallery.js", "") in inline or \
            ("assets/gallery.js", "") in inline
        inline = [i for i in inline if not i[0].endswith("assets/gallery.js")]
        assert len(inline) == 1 and inline[0][0] == ""
        body = inline[0][1].strip()
        assert body.startswith("window.FINDER = ") and body.endswith(";")
        data = json.loads(body[len("window.FINDER = "):-1])
        assert set(data) == {"products", "lessons", "routes", "paths"}
        for r in data["products"]:
            assert set(r) == {"slug", "title", "why", "needs", "level", "price", "href",
                              "cover", "paths", "fits", "install"}


def test_app_js_makes_no_request_and_stores_nothing():
    js = (site.ASSETS / "app.js").read_text()
    for word in ("fetch", "XMLHttpRequest", "sendBeacon", "localStorage",
                 "sessionStorage", "document.cookie", "http"):
        assert word not in js, word
    assert _render()["assets/app.js"] == js.encode()


def test_the_finder_recommends_only_catalog_titles_with_their_own_summaries():
    """Goal and level (both optional, no email) narrow the catalog's own
    titles; every goal and every level has at least one title, and the
    finder carries no payment link: it points at the product page."""
    cat = cat_mod.load()
    data = site._finder_data(cat)
    summaries = {unescape(p.summary) for p in cat.products}
    recs = data["products"]
    assert len(recs) == len(cat.products)
    for r in recs:
        assert unescape(r["why"]) in summaries
        assert r["href"].endswith("/") and not r["href"].startswith("http")
        assert r["paths"] and r["fits"]
    for key, *_ in site.PATHS:
        assert any(key in r["paths"] for r in recs), key
    for key, _line in site.LEVELS:
        assert any(key in r["fits"] for r in recs), key
    assert "buy.stripe.com" not in json.dumps(data)
    assert data["lessons"] == {}


def test_the_finder_rules_out_before_it_ranks_and_says_why():
    """Plan F04 acceptance: business + new + no installations never leads
    with the Python course or the Docker pack; one best start, at most two
    alternatives; what the answers rule out is listed (with its own
    requirement text on the page); no eligible title gives the free route."""
    cat = cat_mod.load()
    for setup in ("", "no"):
        r = site.finder_route(cat, "work", "new", setup)
        assert r["best"] not in ("safe-ai-operator", "local-business-automation-pack")
        assert {"safe-ai-operator", "local-business-automation-pack"} <= set(r["out"])
        assert len(r["alts"]) <= site.FINDER_ALTERNATIVES
        assert r["best"] not in r["alts"] and not set(r["alts"]) & set(r["out"])
    no_install = site.finder_route(cat, "work", "", "no")
    assert "claude-obsidian-vault-kit" in no_install["out"]       # needs the app
    assert "30-business-lessons" not in no_install["out"]          # "No software to install"
    nothing = site.finder_route(cat, "build", "new", "no")
    assert nothing == {"best": "", "alts": [], "out": nothing["out"]} and nothing["out"]
    assert site.finder_route(cat, "build", "code", "yes")["best"]
    routes = site.finder_routes(cat)
    assert len(routes) == 4 * 4 * 3 - 1
    slugs = {p.slug for p in cat.products}
    for key, r in routes.items():
        goal = key.split("|")[0]
        for s in [*([r["best"]] if r["best"] else []), *r["alts"], *r["out"]]:
            assert s in slugs, key
            assert not goal or goal in site.product_paths(cat.product(s)), key
    js = (site.ASSETS / "app.js").read_text()
    assert "Set aside for now" in js and "Watch the free lessons" in js


def test_the_catalog_search_reads_the_catalog_and_shares_by_address():
    """Plan F02/F37: search and filters are hidden until the script runs
    (the whole catalog shows without it); every card carries its own catalog
    words to search; the state goes into the address, never into storage."""
    home = _render()["academy/index.html"].decode()
    tools = home.split("<form class=\"cat-tools pv-tools\"", 1)[1].split("</form>", 1)[0]
    assert "data-cat-tools" in tools and " hidden>" in tools
    assert 'type="search" name="q"' in tools and "Clear all" in tools
    cat = cat_mod.load()
    for p in cat.products:
        card = home.split(f'data-slug="{p.slug}"', 1)[1].split(">", 1)[0]
        words = unescape(card.split('data-search="', 1)[1].split('"', 1)[0])
        assert p.title.lower() in words and p.summary.lower() in words
        for f in site.formats(p.offers):
            assert site._format_key(f) in card.split('data-formats="', 1)[1].split('"')[0]
    assert "data-cat-empty hidden" in home and 'href="../videos/">Browse the free lessons' in home
    js = (site.ASSETS / "app.js").read_text()
    assert "history.replaceState" in js and "URLSearchParams" in js


def test_the_comparison_adds_only_fields_the_catalog_states():
    """Plan F03: when each title was last checked (its newest "Tested with"
    date, or "Not stated") and its refund terms; a narrowed table is shared
    as ?compare=slug,slug."""
    home = _render()["academy/index.html"].decode()
    table = home.split('<table class="compare">', 1)[1].split("</table>", 1)[0]
    cat = cat_mod.load()
    for p in cat.products:
        row = table.split(f'data-compare="{p.slug}"', 1)[1].split("</tr>", 1)[0]
        cell = row.split('data-label="Last checked">', 1)[1].split("<", 1)[0]
        if p.tested:
            newest = max(c.checked_on for c in p.tested)
            d = date.fromisoformat(newest)
            assert cell == f"{d.day} {d:%b %Y}", p.slug
        else:
            assert cell == "Not stated", p.slug
        assert 'data-label="Refunds">' in row
    raw = _raw()
    raw["products"][0]["tested"] = []
    home = _render(raw)["academy/index.html"].decode()
    row = home.split(f'data-compare="{raw["products"][0]["slug"]}"', 1)[1].split("</tr>")[0]
    assert 'data-label="Last checked">Not stated<' in row
    js = (site.ASSETS / "app.js").read_text()
    assert "params.set('compare'" in js


def test_every_number_on_every_page_is_catalog_text():
    """A figure the catalog doesn't state can't reach a page, except the
    arithmetic done on catalog values at build time: the bundle's separate
    total and difference, and the count of an offer's video files."""
    raw_text = cat_mod.CATALOG_PATH.read_text()
    allowed = set(re.findall(r"\d+(?:\.\d+)?", raw_text))
    cat = cat_mod.load()
    _parts, total, _price, diff = site.bundle_math(cat)
    allowed |= {f"{total:g}", f"{diff:g}"}
    # The storefront's "01 / 07" ordinals: positions in the catalog and the
    # path list, zero-padded, never a claim about a title.
    allowed |= {site._idx(i, len(cat.products)) for i in range(1, len(cat.products) + 1)}
    allowed |= {site._idx(i, len(site.PATHS)) for i in range(1, len(site.PATHS) + 1)}
    allowed |= {str(sum(f.label.endswith("(video)") for f in o.files))
                for o in cat.offers()}
    # The sneak peeks' figures are measured from the delivered files by
    # tools/academy_previews.py (page numbers and totals, file sizes, a
    # README's lines and the numbers in its opening): previews.json, and the
    # sizes and counts the page derives from it.
    peeks = site._previews()
    allowed |= set(re.findall(r"\d+(?:\.\d+)?", json.dumps(peeks)))
    # The contact phone is the owner's number, not a claim about a title.
    allowed |= set(re.findall(r"\d+", store_origin.CONTACT_PHONE_DISPLAY))
    for entry in peeks.values():
        if k := entry.get("kit"):
            allowed |= {str(len(k["tree"])), str(len(k["excerpt"]))}
            for f in k["tree"]:
                allowed |= set(re.findall(r"\d+(?:\.\d+)?", site._size(int(f["bytes"]))))
    for path, html in _pages(_render()).items():
        if path.startswith("blog/"):
            # A blog post's own prose (the <article class="post-body">) is
            # Miro's writing, not a product claim: "a chess program from
            # 1997" is no figure about a title. Everything around it on a
            # blog page (titles, dates, the list, the kit line) still obeys
            # the rule, and the prose may only carry the post's own text.
            html, prose = _split_post_body(html)
            assert prose is None or _prose_is_the_posts_own(path, prose), path
        text = " ".join(_text(html).parts)
        for number in re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")):
            assert number in allowed, (path, number)


def _split_post_body(html: bytes) -> tuple[bytes, bytes | None]:
    """A blog page without its post body, and the body (None when absent)."""
    start, end = b'<article class="post-body">', b"</article>"
    if start not in html:
        return html, None
    head, rest = html.split(start, 1)
    prose, tail = rest.split(end, 1)
    return head + tail, prose


def _prose_is_the_posts_own(path: str, prose: bytes) -> bool:
    from atlas.modules.academy import blog_pages, mdlite
    slug = path.split("/")[1]
    post = next(p for p in blog_pages.load() if p.slug == slug)
    return prose.decode().strip() == mdlite.to_html(post.body, "../../")


def test_a_bundle_total_is_shown_only_when_every_price_is_set():
    # Miro, 2026-10-01: every title priced (20 + 9 + 19 + 39), bundle $79.
    page = _render()["all-four/index.html"].decode()
    assert "$20 + $9 + $19 + $39 = <b>$87</b> bought separately." in page
    assert "The bundle is $79, $8 less than buying them one by one." in page
    raw = _raw()
    course = raw["products"][0]["offers"][0]
    course.update(price_usd=None, payment_link="", stripe_price_id="")
    assert "bought separately" not in _render(raw)["all-four/index.html"].decode()
    course["price_usd"] = 49
    page = _render(raw)["all-four/index.html"].decode()
    # 49 + 9 + 19 + 39 = 116, against 79: the page says so, never a saving.
    assert "$49 + $9 + $19 + $39 = <b>$116</b> bought separately." in page
    assert "The bundle is $79, $37 less" in page


# ------------------------------------------------------------------ buy buttons

def test_without_a_payment_link_every_buy_is_a_disabled_coming_soon_button():
    files = _render(_off())
    for path, html in _pages(files).items():
        page = html.decode()
        assert "buy.stripe.com" not in page, path
        assert "btn-buy\" href" not in page, path
    product = files["30-business-lessons/index.html"].decode()
    assert re.search(r'<button class="btn btn-buy[^"]*" type="button" disabled '
                     r'aria-disabled="true">Coming soon</button>', product)


def test_a_live_offer_links_to_its_payment_link():
    raw = _live(_off(), "claude-obsidian-vault-kit")
    raw["products"][2]["refund"] = "Test terms."
    files = _render(raw)
    page = files["claude-obsidian-vault-kit/index.html"].decode()
    assert 'href="https://buy.stripe.com/test_abc"' in page
    assert "Buy for $19" in page
    # Its neighbours stay off.
    assert "buy.stripe.com" not in files["30-business-lessons/index.html"].decode()


def test_a_payment_link_without_a_price_is_refused():
    raw = _live(_raw(), "safe-ai-operator", price=None)
    raw["products"][0]["offers"][0]["price_usd"] = None
    raw["products"][0]["refund"] = "Test terms."
    with pytest.raises(cat_mod.CatalogError, match="no price"):
        cat_mod.parse(raw)


def test_a_payment_link_without_refund_terms_is_refused():
    raw = _live(_off(), "30-business-lessons")
    with pytest.raises(cat_mod.CatalogError, match="refund terms"):
        cat_mod.parse(raw)


def test_a_payment_link_without_a_stripe_price_id_is_refused():
    raw = _live(_raw(), "ai-for-every-age-adults", price_id="")
    with pytest.raises(cat_mod.CatalogError, match="stripe_price_id"):
        cat_mod.parse(raw)


def test_a_payment_link_must_be_a_stripe_link():
    raw = _live(_raw(), "ai-for-every-age-adults", link="https://evil.example/pay")
    with pytest.raises(cat_mod.CatalogError, match="payment_link"):
        cat_mod.parse(raw)


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", "a/../../b.pdf",
                                 ".hidden/x.pdf", "a\\b.pdf", "A/B.PDF"])
def test_a_file_path_that_could_leave_the_files_dir_is_refused(bad):
    raw = _raw()
    raw["products"][1]["offers"][0]["files"][0]["path"] = bad
    with pytest.raises(cat_mod.CatalogError, match="unsafe path"):
        cat_mod.parse(raw)


# ------------------------------------------------------------------ catalog.json

ENTRY_KEYS = {"slug", "kind", "title", "url", "price_usd", "currency", "buy_live",
              "cover", "summary"}


def test_the_public_catalog_has_the_agreed_shape():
    data = json.loads(_render()["catalog.json"])
    assert data["generated_at"] == "2026-09-30T12:00:00Z"
    entries = data["products"]
    assert {e["slug"] for e in entries} == {
        "safe-ai-operator", "30-business-lessons", "claude-obsidian-vault-kit",
        "ai-for-every-age-adults", "ai-for-every-age-60plus", "ai-for-every-age-both",
        "local-business-automation-pack", "family-ai-scam-defence-workbook",
        "sell-with-ai-ebook",
        "sell-with-ai-video", "sell-with-ai-both", "all-four"}
    for e in entries:
        assert set(e) == ENTRY_KEYS
        assert e["url"].startswith("https://agency.storefront.test/")
        assert e["cover"].startswith("https://agency.storefront.test/assets/")
        assert e["currency"] == "USD"
        assert e["price_usd"] is None or isinstance(e["price_usd"], float | int)
    by = {e["slug"]: e for e in entries}
    assert by["safe-ai-operator"]["price_usd"] == 20       # Miro, 2026-10-01
    assert by["30-business-lessons"]["price_usd"] == 9
    assert by["all-four"]["kind"] == "bundle"


def test_nothing_is_buy_live_without_a_link_or_without_a_price():
    data = json.loads(_render(_off())["catalog.json"])
    assert not any(e["buy_live"] for e in data["products"])
    # A link with no price is refused outright; a price with no link is not live.
    raw = _off()
    raw["products"][0]["offers"][0]["price_usd"] = 49
    data = json.loads(_render(raw)["catalog.json"])
    assert not any(e["buy_live"] for e in data["products"])


def test_buy_live_is_true_only_with_a_link_and_a_price():
    raw = _live(_off(), "claude-obsidian-vault-kit")
    raw["products"][2]["refund"] = "Test terms."
    data = json.loads(_render(raw)["catalog.json"])
    live = {e["slug"] for e in data["products"] if e["buy_live"]}
    assert live == {"claude-obsidian-vault-kit"}


def test_the_public_catalog_leaks_no_link_path_or_stripe_id():
    raw = _live(_raw(), "claude-obsidian-vault-kit")
    raw["products"][2]["refund"] = "Test terms."
    text = _render(raw)["catalog.json"].decode()
    for leak in ("buy.stripe.com", "price_TEST", ".pdf", ".zip", "payment_link",
                 "stripe_price_id", "\"files\"", "safe-ai-operator/build"):
        assert leak not in text, leak


def test_build_writes_the_site_into_an_empty_directory(tmp_path):
    out = tmp_path / "shop"
    written = site.build(out, now=NOW)
    assert (out / "academy/index.html").is_file() and (out / "academy/assets/style.css").is_file()
    assert not (out / "index.html").exists()      # the root is the agency landing's
    assert len(written) == len(site.render_root(cat_mod.load(), NOW))
    with pytest.raises(ValueError):
        site.build(out, now=NOW)


def test_the_checked_in_catalog_sells_every_offer_and_the_bundle():
    data = json.loads(_render()["catalog.json"])
    live = {e["slug"]: e["price_usd"] for e in data["products"] if e["buy_live"]}
    assert live == {"safe-ai-operator": 20, "30-business-lessons": 9,
                    "claude-obsidian-vault-kit": 19, "ai-for-every-age-adults": 29,
                    "ai-for-every-age-60plus": 19, "ai-for-every-age-both": 39,
                    "all-four": 79, "local-business-automation-pack": 29,
                    "family-ai-scam-defence-workbook": 19,
                    "sell-with-ai-ebook": 19, "sell-with-ai-video": 39,
                    "sell-with-ai-both": 49}


def test_search_engines_and_ai_assistants_can_read_the_store():
    """Miro, 2026-10-06: robots names the AI crawlers and the sitemap; the
    sitemap lists every page; llms.txt carries only catalog facts; a
    product's record offers only what is on sale and carries no rating."""
    files = _render()
    # One robots.txt for the whole host: the landing's (Caddyfile.public). The
    # store build writes none, and its sitemap is named in the landing's file.
    assert "robots.txt" not in files and "sitemap.xml" not in files and "llms.txt" not in files
    robots = (LANDING / "robots.txt").read_text()
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot"):
        assert f"User-agent: {bot}\nAllow: /" in robots
    assert "Sitemap: https://agency.storefront.test/sitemap.xml" in robots
    assert "Sitemap: https://agency.storefront.test/sitemap-academy.xml" in robots
    sitemap = files["sitemap-academy.xml"].decode()
    for path in _pages(files):
        # a "sent" confirmation is not a page to find in search
        if path.endswith("index.html") and not path.startswith(("contact/sent", "help/sent", "updates/thanks")):
            loc = "https://agency.storefront.test/" + path.removesuffix("index.html")
            assert f"<loc>{loc}</loc>" in sitemap, path
    llms = files["academy/llms.txt"].decode()
    assert llms.startswith("# ATLAS Academy") and "owner@example.com" in llms
    page = files["safe-ai-operator/index.html"].decode()
    body = page.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    ld = json.loads(body)
    assert ld["@type"] == "Product" and ld["offers"][0]["priceCurrency"] == "USD"
    assert "aggregateRating" not in body and "review" not in body.lower()
    off = _render(_off())["safe-ai-operator/index.html"].decode()
    assert '"offers"' not in off


# Miro, 2026-10-12 (the growth plan): /start/ and the worksheet, off until
# he has read the copy.

def test_the_start_page_and_worksheet_are_off_until_switched_on(monkeypatch):
    monkeypatch.delenv(site.START_ENV, raising=False)
    files = _render()
    assert "start/index.html" not in files
    monkeypatch.setenv(site.START_ENV, "on")
    files = _render()
    start = files["start/index.html"].decode()
    sheet = files["before-you-automate/index.html"].decode()
    assert "Before You Automate" in start and "local-business-automation-pack/" in start
    for name, ask, _example in site.WORKSHEET_FIELDS:
        assert name in sheet and site._e(ask) in sheet
    assert "no email, no sign-up" in sheet


def test_every_sneak_peek_points_at_files_the_store_publishes():
    """Miro, 2026-10-12: a preview on every course page. Each one is cut from
    the delivered files (tools/academy_previews.py); every file it names is
    published with the store, and every product in the catalog has one."""
    files = _render()
    pages = _pages(files)
    peeks = site._previews()
    cat = cat_mod.load()
    assert {p.slug for p in cat.products} <= set(peeks)
    for slug, entry in peeks.items():
        page = pages[f"{slug}/index.html"]
        page = page.decode() if isinstance(page, bytes) else page
        assert 'id="peek"' in page and 'href="#peek"' in page, slug
        names = [v[k] for v in entry.get("videos", []) for k in ("src", "poster")]
        names += [pg["src"] for b in entry.get("books", []) for pg in b["pages"]]
        for name in names:
            assert f"assets/previews/{slug}/{name}" in files, (slug, name)
            assert f"../assets/previews/{slug}/{name}" in page, (slug, name)
        assert "<iframe" not in page and "youtube.com/embed" not in page


def test_every_sitemap_page_names_itself_canonical_and_is_indexable():
    """Search Console, 2026-10-29: the home page was not indexed. Every page
    the sitemap lists carries one <title>, a description, exactly one
    canonical that is its own sitemap address, and no noindex; robots.txt
    names the sitemap at the same origin."""
    files = _render()
    base = "https://agency.storefront.test/"
    assert f"Sitemap: {base}sitemap-academy.xml" in (LANDING / "robots.txt").read_text()
    sitemap = files["sitemap-academy.xml"].decode()
    assert sitemap.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert locs and len(locs) == len(set(locs))
    assert all(loc.startswith(base) for loc in locs)
    assert len(re.findall(r"<lastmod>\d{4}-\d{2}-\d{2}</lastmod>", sitemap)) == len(locs)
    for loc in locs:
        path = loc.removeprefix(base) + "index.html"
        html = files[path].decode()
        assert html.count("<title>") == 1, path
        desc = re.search(r'<meta name="description" content="([^"]+)">', html)
        assert desc and desc.group(1).strip(), path
        assert re.findall(r'<link rel="canonical" href="([^"]+)">', html) == [loc], path
        assert "noindex" not in html.lower(), path
    for path in ("404.html", "contact/sent/index.html", "help/sent/index.html"):
        if path in files:
            assert 'rel="canonical"' not in files[path].decode(), path


def test_every_public_page_carries_the_contact_address_and_phone():
    # Miro 2026-11-13 reversed 2026-10-29: the public contact is the Gmail
    # address plus a phone number; the old mapfront address is not shown.
    files = _no_mirotech_files()
    assert any("owner@example.com" in b for b in files.values())
    assert any("tel:+15550100142" in b for b in files.values())
    for name, body in files.items():
        assert "miro@mapfront" + ".io" not in body, name


def _no_mirotech_files() -> dict[str, str]:
    files = dict(site.render_root(cat_mod.load()))
    return {k: v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
            for k, v in files.items()}


# ------------------------------------------- one host: the store lives under /academy/

class _Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag, attrs):
        for k, v in attrs:
            if k in ("href", "src") and v:
                self.refs.append(v)


def _resolve(page: str, ref: str) -> str | None:
    """A page's href/src as a host path, or None for off-site, mailto, tel and
    in-page references."""
    from urllib.parse import urljoin, urlsplit
    if ref.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    full = urlsplit(urljoin("https://agency.storefront.test/" + page, ref))
    if full.netloc != "agency.storefront.test":
        return None
    return full.path


def test_the_store_home_is_at_academy_and_the_root_is_the_landings():
    files = _render()
    assert "academy/index.html" in files and "index.html" not in files
    home = files["academy/index.html"].decode()
    assert '<link rel="canonical" href="https://agency.storefront.test/academy/">' in home
    sitemap = files["sitemap-academy.xml"].decode()
    assert "<loc>https://agency.storefront.test/academy/</loc>" in sitemap
    assert "<loc>https://agency.storefront.test/</loc>" not in sitemap   # the landing's own
    assert "/academy/" in files["academy/llms.txt"].decode()


def test_every_internal_link_on_every_page_reaches_a_file_and_only_the_header_hits_the_landing():
    """Served from one host, the store's links must land on store files. The
    one exception is the exact root `/` (the shared header's brand and Agency
    link, the way back to the agency landing); `index.html` or `../` forms of
    it are still refused."""
    files = _render()
    served = set(files)
    landing = {"thanks", "thanks/index.html", "robots.txt", "sitemap.xml", "llms.txt",
               "mapfront-icon.png"}
    bad = []
    for page, body in _pages(files).items():
        parser = _Refs()
        parser.feed(body.decode())
        for ref in parser.refs:
            path = _resolve(page, ref)
            if path is None:
                continue
            rel = path.lstrip("/")
            if ref in ("/", "/agency/"):
                continue                          # the brand (store home) and the Agency page
            if rel in ("", "index.html"):
                bad.append((page, ref, "points at the landing"))
                continue
            if rel.startswith("fonts/") or rel in landing:
                continue
            if rel.startswith(("preview/", "public/", "webhooks/", "media/")):
                continue                                         # other handles on the host
            target = rel + "index.html" if rel.endswith("/") else rel
            if target not in served:
                bad.append((page, ref, f"no file {target}"))
    assert not bad, bad[:12]
