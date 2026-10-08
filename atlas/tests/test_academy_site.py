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
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser

import pytest

from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import site

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def _raw() -> dict:
    return json.loads(cat_mod.CATALOG_PATH.read_text())


def _render(raw: dict | None = None) -> dict[str, bytes]:
    return site.render(cat_mod.parse(raw or _raw()), NOW)


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
    for path in ("index.html", "all-four/index.html", "terms/index.html",
                 "privacy/index.html", "refunds/index.html", "contact/index.html",
                 "404.html", "catalog.json"):
        assert path in files
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
    r"\btestimonials?\b", r"\breviewed by\b", r"\bratings?\b", r"\brated\b",
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
    inline script, and it does nothing but assign the finder's data."""
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
        if path != "index.html":
            assert inline == [], path
            continue
        assert len(inline) == 1 and inline[0][0] == ""
        body = inline[0][1].strip()
        assert body.startswith("window.FINDER = ") and body.endswith(";")
        data = json.loads(body[len("window.FINDER = "):-1])
        assert set(data) == {k for k, _line, _tag in site.FINDER}
        for recs in data.values():
            for r in recs:
                assert set(r) == {"cover", "title", "why", "price", "buy", "href"}


def test_app_js_makes_no_request_and_stores_nothing():
    js = (site.ASSETS / "app.js").read_text()
    for word in ("fetch", "XMLHttpRequest", "sendBeacon", "localStorage",
                 "sessionStorage", "document.cookie", "http"):
        assert word not in js, word
    assert _render()["assets/app.js"] == js.encode()


def test_the_finder_recommends_only_catalog_titles_with_their_own_summaries():
    cat = cat_mod.load()
    data = site._finder_data(cat)
    summaries = {unescape(p.summary) for p in cat.products} | {cat.bundle.summary}
    for recs in data.values():
        assert recs, "a finder choice with nothing to recommend"
        for r in recs:
            assert unescape(r["why"]) in summaries
            assert r["href"].endswith("/") and not r["href"].startswith("http")
            assert r["buy"] == "" or r["buy"].startswith("https://buy.stripe.com/")
    assert data["all"][0]["title"] == cat.bundle.title
    # No live link, no Buy: the finder then points at the product's buy box.
    off = site._finder_data(cat_mod.parse(_off()))
    assert all(r["buy"] == "" for recs in off.values() for r in recs)


def test_every_number_on_every_page_is_catalog_text():
    """A figure the catalog doesn't state can't reach a page."""
    raw_text = cat_mod.CATALOG_PATH.read_text()
    allowed = set(re.findall(r"\d+(?:\.\d+)?", raw_text))
    for path, html in _pages(_render()).items():
        text = " ".join(_text(html).parts)
        for number in re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")):
            assert number in allowed, (path, number)


def test_a_bundle_total_is_shown_only_when_every_price_is_set():
    # Miro, 2026-10-01: every title priced (20 + 9 + 19 + 39), bundle $79.
    assert "Bought one by one today: $87." in _render()["all-four/index.html"].decode()
    raw = _raw()
    course = raw["products"][0]["offers"][0]
    course.update(price_usd=None, payment_link="", stripe_price_id="")
    assert "Bought one by one" not in _render(raw)["all-four/index.html"].decode()
    course["price_usd"] = 49
    page = _render(raw)["all-four/index.html"].decode()
    # 49 + 9 + 19 + 39
    assert "Bought one by one today: $116." in page


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
        assert e["url"].startswith("https://shop.mapfront.io/")
        assert e["cover"].startswith("https://shop.mapfront.io/assets/")
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
    assert (out / "index.html").is_file() and (out / "assets/style.css").is_file()
    assert len(written) == len(site.render(cat_mod.load(), NOW))
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
    robots = files["robots.txt"].decode()
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot"):
        assert f"User-agent: {bot}\nAllow: /" in robots
    assert "Sitemap: https://shop.mapfront.io/sitemap.xml" in robots
    sitemap = files["sitemap.xml"].decode()
    for path in _pages(files):
        if path.endswith("index.html") and not path.startswith("contact/sent"):
            loc = "https://shop.mapfront.io/" + path.removesuffix("index.html")
            assert f"<loc>{loc}</loc>" in sitemap, path
    llms = files["llms.txt"].decode()
    assert llms.startswith("# ATLAS Academy") and "miro@mapfront.io" in llms
    page = files["safe-ai-operator/index.html"].decode()
    body = page.split('<script type="application/ld+json">', 1)[1].split("</script>")[0]
    ld = json.loads(body)
    assert ld["@type"] == "Product" and ld["offers"][0]["priceCurrency"] == "USD"
    assert "aggregateRating" not in body and "review" not in body.lower()
    off = _render(_off())["safe-ai-operator/index.html"].decode()
    assert '"offers"' not in off
