"""The course catalog (2026-09-29): Nora markets every product in a closed,
code-defined catalog -- priced and stocked live, rotated fairly within the
weekly cap, honest by per-product rule, one SEO blog draft per product."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from atlas.kernel.contracts import Health
from atlas.modules import course
from atlas.modules.course import catalog, channels, compose, data, honesty, posts
from atlas.modules.course import price as live

T0 = datetime(2026, 9, 29, 18, 30, tzinfo=UTC)
VAULT = Path("/home/user/Documents/ATLAS Vault")

HANDLES = {
    "operator": "build-a-safe-ai-operator-the-course",
    "adults": "everyday-ai-for-adults-use-claude-safely-and-build-your-first-"
              "automation",
    "sixty_plus": "safe-and-simple-ai-60-large-print-guide-to-claude-and-scam-safety",
    "bundle": "ai-for-every-age-bundle-everyday-ai-for-adults-safe-and-simple-ai-60",
    "scam_kit": "free-family-scam-safety-kit-6-printables-to-keep-by-the-phone",
}

# What the store answered on 2026-09-29, as live reads (never used as data).
READS = {
    "operator": live.Price(Decimal(20), Decimal(49), T0, None, True, False),
    "adults": live.Price(Decimal(29), None, T0, None, True, True),
    "sixty_plus": live.Price(Decimal(19), None, T0, None, True, True),
    "bundle": live.Price(Decimal(39), None, T0, None, True, True),
    "scam_kit": live.Price(Decimal(0), None, T0, None, True, False),
}


def _async(value):
    async def f(*_a, **_k):
        return value
    return f


def _words(read):
    return {lang: read.words(lang) for lang in honesty.LANGUAGES}


def _link(product, platform="facebook", exp=42):
    return data.utm_url(catalog.CATALOG[product].url, platform, exp)


async def _compose(product, fmt="social", lesson=None, *, read=None):
    read = read or READS[product]
    item = lesson or data.lessons(product)[0]
    return await compose.compose(fmt, item, "en", _words(read), _link(product),
                                 product, age_notice=read.age_notice)


# ------------------------------------------------------------------ catalog

def test_the_catalog_is_the_five_store_products_and_nothing_else():
    assert {k: p.handle for k, p in catalog.CATALOG.items()} == HANDLES
    for p in catalog.CATALOG.values():
        assert p.url == f"https://academy-store.myshopify.example/products/{p.handle}"
        assert p.audience and p.platforms and p.languages and p.vault_dir
        assert (Path(data.PRODUCTS) / p.data).is_file()
        assert "shopify_blog" in p.platforms
    assert catalog.CATALOG["scam_kit"].lead_magnet
    assert [k for k, p in catalog.CATALOG.items() if p.lead_magnet] == ["scam_kit"]
    assert {k for k, p in catalog.CATALOG.items() if p.strict_numbers} == {
        "sixty_plus", "scam_kit"}
    # Only the Operator has reviewed labels in other languages.
    assert {k for k, p in catalog.CATALOG.items() if p.languages != ("en",)} == {
        "operator"}


def test_a_product_not_in_the_catalog_is_refused_before_anything():
    assert "never marketed" in catalog.product_refusal("teen_pack")
    assert catalog.product_refusal("adults") is None
    assert "never marketed" in posts._param_refusal("teen_pack", "linkedin",
                                                    "0.1", "en")


def test_the_catalog_names_no_price():
    for row in catalog.describe():
        assert not any("price" in k for k in row), row
    for p in catalog.CATALOG.values():
        raw = (Path(data.PRODUCTS) / p.data).read_text()
        assert not re.search(r'"(?:price|amount|compare_at)"', raw), p.key


def test_lesson_ids_are_per_product():
    assert data.lesson("0.1", "operator")["title"] == "The week-one failure"
    assert data.lesson("0.1", "adults") is None
    assert data.lesson("6.2", "sixty_plus")["title"].startswith("Voice-clone")
    assert "no lesson" in posts._param_refusal("adults", "linkedin", "9.9", "en")


@pytest.mark.parametrize(("product", "channel", "lang", "why"), [
    ("sixty_plus", "linkedin", "es", "en only"),
    ("adults", "linkedin", "ar", "en only"),
    ("operator", "shopify_blog", "es", "English only"),
])
def test_languages_are_per_product(product, channel, lang, why):
    assert why in posts._param_refusal(product, channel,
                                       data.lessons(product)[0]["id"], lang)


def test_channels_are_per_product():
    assert posts.platform_refusal("sixty_plus", "x")
    assert posts.platform_refusal("bundle", "bluesky")
    assert posts.platform_refusal("sixty_plus", "facebook") is None
    assert posts.platform_refusal("scam_kit", "x") is None


# ------------------------------------------------------------------ rotation

def test_rotation_gives_every_product_its_turn():
    counts = {"operator": 1, "adults": 1, "sixty_plus": 0, "bundle": 1,
              "scam_kit": 0}
    assert posts.due_products(counts) == ["sixty_plus", "scam_kit"]
    why = posts.rotation_refusal("operator", counts)
    assert "fair share" in why and "sixty_plus" in why and "scam_kit" in why
    assert posts.rotation_refusal("scam_kit", counts) is None
    assert posts.due_products({}) == list(catalog.KEYS)


def test_rotation_within_the_default_weekly_cap_spreads_seven_posts():
    counts: dict[str, int] = {}
    order = []
    for _ in range(channels.weekly_cap()):
        nxt = posts.due_products(counts)[0]
        order.append(nxt)
        counts[nxt] = counts.get(nxt, 0) + 1
    assert set(order[:5]) == set(catalog.KEYS)
    assert max(counts.values()) - min(counts.values()) <= 1


class Book:
    """The rows a proposal reads and writes, per product."""

    def __init__(self, counts=None, week=0, today=0, blogs=0):
        self.counts = counts or {}
        self.week, self.today, self.blogs = week, today, blogs
        self.inserts: list[tuple[str, tuple]] = []
        self.updates: list[tuple[str, tuple]] = []

    async def fetch_one(self, sql, *args):
        if "INSERT INTO marketing_experiments" in sql:
            self.inserts.append(("experiment", args))
            return {"id": 42}
        if "INSERT INTO course_posts" in sql:
            self.inserts.append(("post", args))
            return {"id": 7}
        if "channel = 'shopify_blog'" in sql:
            return {"n": self.blogs}
        if "channel = $1" in sql:
            return {"n": self.today}
        return {"n": self.week}

    async def fetch_all(self, sql, *args):
        assert "GROUP BY product" in sql
        return [{"product": k, "n": n} for k, n in self.counts.items()]

    async def execute_sql(self, sql, *args):
        self.updates.append((sql, args))
        return "OK"


@pytest.fixture
def book(monkeypatch):
    b = Book()
    monkeypatch.setattr(posts, "fetch_one", b.fetch_one)
    monkeypatch.setattr(posts, "fetch_all", b.fetch_all)
    monkeypatch.setattr(posts, "execute_sql", b.execute_sql)
    b.reads = dict(READS)
    b.asked = []

    async def read_price(url=None, *, product="operator"):
        b.asked.append(product)
        return b.reads[product]
    monkeypatch.setattr(live, "read_price", read_price)
    return b


def test_rotation_leaves_an_unsellable_product_out_of_the_floor():
    counts = {"operator": 1, "adults": 1, "sixty_plus": 1, "bundle": 1}
    assert posts.due_products(counts) == ["scam_kit"]
    eligible = ["operator", "adults", "sixty_plus", "bundle"]
    assert posts.due_products(counts, eligible) == eligible
    assert posts.rotation_refusal("adults", counts, eligible) is None


async def test_an_unsellable_product_writes_nothing_and_holds_nobody_back(book):
    book.reads["scam_kit"] = live.Price(Decimal(0), None, T0, None, False)
    book.counts = {"operator": 1, "adults": 1, "sixty_plus": 1, "bundle": 1}
    result = await posts.propose("scam_kit", "linkedin", "1", "en", "h")
    assert not result.ok and "not available" in result.detail
    assert book.inserts == []
    result = await posts.propose("adults", "linkedin", "1.4", "en", "h")
    assert result.ok, result.detail


async def test_a_product_ahead_of_its_share_is_not_proposed(book):
    book.counts = {"operator": 2, "adults": 1, "sixty_plus": 1, "bundle": 1,
                   "scam_kit": 1}
    result = await posts.propose("operator", "linkedin", "2.1", "en", "h")
    assert not result.ok and "fair share" in result.detail
    assert book.inserts == []


async def test_the_weekly_cap_counts_every_product(book, monkeypatch):
    monkeypatch.delenv("COURSE_POSTS_PER_WEEK", raising=False)
    book.week = 7
    result = await posts.propose("scam_kit", "linkedin", "1", "en", "h")
    assert not result.ok and "7 of 7" in result.detail and book.inserts == []
    # The count is across products: the SQL filters by week only.
    rows = []

    async def fetch_one(sql, *args):
        rows.append(sql)
        return {"n": 0}
    monkeypatch.setattr(posts, "fetch_one", fetch_one)
    await posts.proposal_cap_refusal("linkedin", T0)
    assert "product" not in rows[0]
    assert "product" not in posts.CLAIM_SQL


async def test_one_blog_draft_per_product(book):
    book.blogs = 1
    result = await posts.propose("adults", "shopify_blog", "1.4", "en", "h")
    assert not result.ok and "one per product" in result.detail
    assert book.inserts == []


async def test_a_product_is_drafted_with_its_own_live_read(book):
    result = await posts.propose("sixty_plus", "linkedin", "6.2", "en",
                                 "the safe-word hook sends 60+ readers to the store")
    assert result.ok and result.evidence["drafted"], result.detail
    # Every product read for rotation, then this one again to draft it.
    assert sorted(book.asked[:5]) == sorted(catalog.KEYS)
    assert book.asked[5:] == ["sixty_plus"]
    _kind, post_args = book.inserts[1]
    assert post_args[-2] == "sixty_plus"
    assert HANDLES["sixty_plus"] in post_args[-1]
    body = next(a for sql, a in book.updates if "status = 'drafted'" in sql)[1]
    assert "$19." in body and catalog.AGE_NOTICE in body
    assert "This is my own guide. -- the owner" in body
    assert "lesson 6.2" in body


async def test_an_unreadable_price_is_not_proposed_and_says_why(book):
    book.reads["adults"] = live.not_read("timed out", T0)
    result = await posts.propose("adults", "linkedin", "1.4", "en", "h")
    assert not result.ok and "could not be read (timed out)" in result.detail
    assert book.inserts == []


async def test_a_read_that_fails_between_proposal_and_draft_is_not_drafted(
        book, monkeypatch):
    target = channels.static("linkedin")
    monkeypatch.setattr(live, "read_price", _async(live.not_read("timed out", T0)))
    ok, why = await posts._draft(7, target, data.lesson("1.4", "adults"), "en",
                                 _link("adults"), "adults")
    assert not ok and "could not be read (timed out)" in why
    kept = [a for sql, a in book.updates if "status = 'proposed'" in sql]
    assert kept and not [a for sql, a in book.updates if "'drafted'" in sql]


# ------------------------------------------------------------------ live read

class FakeStore:
    """The store's `.json` and `.js` answers, by handle."""

    def __init__(self):
        self.json: dict[str, dict] = {}
        self.js: dict[str, dict | int] = {}
        self.asked: list[str] = []

    def add(self, product, price, *, compare=None, available=True, cents=None,
            body="<p>Sold to adults 18+. The Claude lessons are for adults.</p>"):
        handle = HANDLES[product]
        self.json[handle] = {"product": {"body_html": body, "variants": [{
            "price": price, "compare_at_price": compare,
            "price_currency": "USD"}]}}
        amount = int(Decimal(price) * 100) if cents is None else cents
        self.js[handle] = {"variants": [{"price": amount, "available": available}]}

    async def get(self, url):
        self.asked.append(url)
        req = httpx.Request("GET", url)
        path = httpx.URL(url).path
        handle = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        source = self.json if path.endswith(".json") else self.js
        found = source.get(handle)
        if isinstance(found, int):
            return httpx.Response(found, request=req)
        if found is None:
            return httpx.Response(404, request=req)
        return httpx.Response(200, json=found, request=req)


@pytest.fixture
def store(monkeypatch):
    from atlas.modules import _netguard
    fake = FakeStore()
    monkeypatch.setattr(_netguard, "assert_public", _async(None))
    async def get(_client, url, **_kw):
        return await fake.get(url)

    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    return fake


async def test_the_live_read_takes_price_availability_and_the_age_notice(store):
    store.add("adults", "29.00")
    read = await live.read_price(product="adults")
    assert read.words() == "$29" and read.available and read.age_notice
    assert live.sellable_refusal(read, "adults") is None
    assert [u.rsplit("/", 1)[-1] for u in store.asked] == [
        f"{HANDLES['adults']}.json", f"{HANDLES['adults']}.js"]
    assert "available, read from the store at" in read.card_line("adults")


async def test_the_free_kit_reads_free_and_names_no_figure(store):
    store.add("scam_kit", "0.00", body="<p>Printables.</p>")
    read = await live.read_price(product="scam_kit")
    assert read.free and read.words() == catalog.FREE_WORDS
    assert not read.age_notice
    assert live.sellable_refusal(read, "scam_kit") is None
    assert "$" not in read.words()


@pytest.mark.parametrize(("product", "kw", "why"), [
    ("scam_kit", {"price": "5.00"}, "not free"),
    ("adults", {"price": "0.00"}, "priced product at 0"),
    ("adults", {"price": "29.00", "compare": "49.00"}, "compare-at"),
    ("sixty_plus", {"price": "19.00", "available": False}, "not available"),
])
async def test_a_read_that_breaks_the_catalog_terms_is_not_drafted(store, product,
                                                                   kw, why):
    store.add(product, kw.pop("price"), **kw)
    read = await live.read_price(product=product)
    assert read.read
    assert why in live.sellable_refusal(read, product)


async def test_the_operators_compare_at_is_the_stores_own(store):
    store.add("operator", "20.00", compare="49.00", body="<p>The course.</p>")
    read = await live.read_price(product="operator")
    assert read.words() == "$20, was $49"
    assert live.sellable_refusal(read, "operator") is None


async def test_reads_that_disagree_or_fail_are_unknown(store):
    store.add("bundle", "39.00", cents=2900)
    read = await live.read_price(product="bundle")
    assert not read.read and "disagree" in read.why
    store.add("bundle", "39.00")
    store.js[HANDLES["bundle"]] = 503
    read = await live.read_price(product="bundle")
    assert not read.read and "availability" in read.why and "503" in read.why
    assert "could not be read" in live.sellable_refusal(read, "bundle")
    store.js[HANDLES["bundle"]] = {"variants": [{"price": 3900}]}
    read = await live.read_price(product="bundle")
    assert not read.read and "no availability" in read.why


async def test_only_catalog_products_on_the_store_are_read(store):
    other = "https://academy-store.myshopify.example/products/some-other-thing.json"
    read = await live.read_price(other)
    assert not read.read and "catalog product" in read.why
    assert store.asked == []
    read = await live.read_price(product="teen_pack")
    assert not read.read


async def test_each_product_has_its_own_price_check(store):
    names = [c.name for c in course.MODULE.checks()]
    assert names[:5] == ["price", "price_adults", "price_sixty_plus",
                         "price_bundle", "price_scam_kit"]
    store.add("adults", "29.00")
    obs = await course.check_price("adults")
    assert obs.health is Health.OK and obs.value == 29.0 and obs.at == obs.at
    store.add("scam_kit", "3.00")
    obs = await course.check_price("scam_kit")
    assert obs.health is Health.DEGRADED and "not free" in obs.detail
    obs = await course.check_price("bundle")          # not on the fake store
    assert obs.health is Health.UNKNOWN and "404" in obs.detail


async def test_approval_rereads_the_products_price_and_stock(monkeypatch):
    read = READS["sixty_plus"]
    post = {"id": 9, "status": "drafted", "channel": "linkedin", "language": "en",
            "product": "sixty_plus", "body": "text", "price": read.record()}
    gone = live.Price(read.amount, None, T0, None, False, True)
    monkeypatch.setattr(live, "read_price", _async(gone))
    why, _ = await posts._approval_refusal(post, 9, "text", handoff=True)
    assert "not available" in why


# ------------------------------------------------------------------ honesty

def _post(product, text):
    return f"{text}\n{catalog.AGE_NOTICE}\n{honesty.label_for(product, 'en')}"


@pytest.mark.parametrize(("product", "text", "why"), [
    ("adults", "Earn money with Claude", "income"),
    ("adults", "Your kids will get better grades", "grades"),
    ("adults", "Claude is always accurate", "accuracy"),
    ("adults", "An accredited course", "accreditation"),
    ("adults", "Get certified in AI", "accreditation"),
    ("adults", "Claude for kids, made easy", "child"),
    ("bundle", "Your kids can use Claude too", "child"),
    ("bundle", "Rated 4.9 by parents", "testimonial"),
    ("bundle", "Our ratings speak for themselves", "testimonial"),
    ("adults", "Now 50% off", "discount"),
    ("adults", "Just $19 this week", "live read"),
    ("adults", "Get it discounted", "discount"),
    ("sixty_plus", "Don't become the next victim", "fear"),
    ("sixty_plus", "Scammers are terrifying", "fear"),
    ("scam_kit", "Protect your life savings", "fear"),
    ("sixty_plus", "1 in 3 seniors is scammed every year", "number"),
    ("scam_kit", "Scams cost families billions: 40,000 cases", "number"),
    ("scam_kit", "Worth $19, yours for $0", "number"),
])
def test_per_product_honesty_rules(product, text, why):
    words = READS[product].words()
    found = honesty.refusal(_post(product, text), "en", words, product,
                            age_notice=True)
    assert found and why in found, found


def test_a_verbatim_sourced_figure_may_be_quoted_for_the_kit():
    quote = data.quotes("scam_kit")[0]["text"]
    assert "$7.748 billion" in quote and "(IC3 2025 Annual Report)" in quote
    text = _post("scam_kit", f"{quote}\nFree with an email sign-up")
    assert honesty.refusal(text, "en", catalog.FREE_WORDS, "scam_kit") is None
    # The same figure, changed by one digit, is invented.
    bad = text.replace("201,266", "201,267")
    assert "number" in honesty.refusal(bad, "en", catalog.FREE_WORDS, "scam_kit")


def test_the_age_notice_is_required_where_the_page_has_it():
    text = f"A calm post.\n{honesty.label_for('adults', 'en')}"
    assert "age notice" in honesty.refusal(text, "en", "$29", "adults",
                                           age_notice=True)
    assert honesty.refusal(text, "en", "$29", "adults", age_notice=False) is None


def test_each_product_carries_its_own_label():
    text = f"A calm post.\n{honesty.LABELS['en']}"
    assert "own guide" in honesty.refusal(text, "en", None, "sixty_plus")
    assert honesty.label_for("operator", "es") == honesty.LABELS["es"]


def test_the_card_vet_refuses_the_new_claims_too():
    for text in ("Claude for kids", "accredited", "better grades", "70% off"):
        assert posts.vet({"post_id": 1, "text": text}), text


# ------------------------------------------------------------------ compose

@pytest.mark.parametrize("product", catalog.KEYS)
@pytest.mark.parametrize("fmt", compose.FORMATS)
async def test_every_product_lesson_composes_clean(product, fmt):
    for item in data.lessons(product):
        out, why = await _compose(product, fmt, item)
        assert why is None, (product, item["id"], fmt, why)
        assert honesty.label_for(product, "en") in out["text"]
        assert (catalog.AGE_NOTICE in out["text"]) is READS[product].age_notice
        assert _link(product) in out["text"]


async def test_a_new_product_post_is_its_lesson_text_word_for_word():
    item = data.lesson("6.2", "sixty_plus")
    out, _ = await _compose("sixty_plus", "social", item)
    assert out["text"].startswith(item["hook"])
    assert item["line"] in out["text"]
    assert "#scamawareness" in out["text"]


async def test_the_kit_says_free_and_no_price():
    out, _ = await _compose("scam_kit", "social")
    assert catalog.FREE_WORDS in out["text"] and "$" not in out["text"]


async def test_a_post_too_long_for_threads_falls_back_to_the_short_shape(
        monkeypatch):
    item = data.lesson("1.4", "adults")
    long_, _ = await _compose("adults", "social", item)
    target = channels.Target("postiz:th1", channels.TEXT_PROVIDERS["threads"],
                             "threads", False, integration="th1",
                             identifier="threads")
    assert channels.length_refusal(long_["text"], target.provider)
    written = []

    async def execute_sql(sql, *args):
        written.append((sql, args))
    monkeypatch.setattr(posts, "execute_sql", execute_sql)
    monkeypatch.setattr(live, "read_price", _async(READS["adults"]))
    ok, _ = await posts._draft(7, target, item, "en", _link("adults"), "adults")
    assert ok
    body = written[0][1][1]
    assert len(body) <= 500 and body.startswith("You'll understand")


# ------------------------------------------------------------------ blog

@pytest.mark.parametrize("product", catalog.KEYS)
async def test_each_products_blog_draft_meets_the_seo_limits(product):
    out, why = await _compose(product, "article")
    assert why is None, why
    title, meta, body = compose.article_parts(out["text"])
    assert len(title) <= 60 and len(meta) <= 155
    assert 700 <= compose.word_count(body) <= 1200
    assert body.count(_link(product)) >= 2           # in the text and the CTA
    assert f"## Get {catalog.CATALOG[product].short}" in body
    # Grounded only in its own lesson files: every section is one of them.
    for sec in data.blog(product)["sections"]:
        assert sec["text"].strip() in body
        assert sec["source"].startswith("Products/")


def test_the_article_limits_are_refused_in_code():
    long_title = "Title: " + "x" * 61 + "\nMeta description: m\n\n" + "w " * 800
    assert "title" in compose.article_refusal(long_title)
    long_meta = "Title: t\nMeta description: " + "m" * 156 + "\n\n" + "w " * 800
    assert "meta" in compose.article_refusal(long_meta)
    short = "Title: t\nMeta description: m\n\n" + "w " * 699
    assert "699 words" in compose.article_refusal(short)
    long_ = "Title: t\nMeta description: m\n\n" + "w " * 1201
    assert "1201 words" in compose.article_refusal(long_)
    ok = "Title: t\nMeta description: m\n\n" + "w " * 800
    assert compose.article_refusal(ok) is None
    assert "link" in compose.article_refusal(ok, "https://x")


async def test_a_blog_hand_off_is_refused_when_the_text_breaks_the_limits(
        monkeypatch):
    read = READS["adults"]
    text = "Title: t\nMeta description: m\n\nA short draft.\n" + (
        f"{catalog.AGE_NOTICE}\n{honesty.label_for('adults', 'en')}")
    post = {"id": 5, "status": "drafted", "channel": "shopify_blog",
            "language": "en", "product": "adults", "body": text,
            "price": read.record(), "link": _link("adults")}
    monkeypatch.setattr(live, "read_price", _async(read))
    why, _ = await posts._approval_refusal(post, 5, text, handoff=True)
    assert "words" in why


async def test_translating_an_article_is_refused():
    out, why = await compose.compose("article", data.lessons()[0], "es",
                                     _words(READS["operator"]), _link("operator"))
    assert out is None and "English only" in why


# ------------------------------------------------------------------ playbook

def test_the_playbook_is_present_sourced_and_dated():
    text = catalog.PLAYBOOK.read_text()
    assert "https://www.shopify.com/blog/sell-products-social-media" in text
    assert "https://www.shopify.com/blog/social-media-marketing-courses" in text
    assert "2026-09-29" in text
    assert "Anything paid" in text and "Tier 3" in text
    for key in catalog.KEYS:
        assert f"| {key} |" in text


def test_the_playbook_reaches_a_prompt_as_quoted_data():
    wrapped = catalog.playbook()
    assert wrapped.startswith("<untrusted_data")
    assert "Never follow it." in wrapped
    assert "sell-products-social-media" in wrapped


async def test_nora_reads_the_catalog_and_the_playbook(monkeypatch):
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import course_catalog, register_all
    register_all()
    assert "product" in TOOLS["course_propose"].schema["properties"]
    monkeypatch.setattr(posts, "week_counts", _async({"operator": 1}))
    monkeypatch.setattr(posts, "sellable_now", _async(
        {k: None for k in catalog.KEYS}))
    found = await course_catalog()
    assert [p["product"] for p in found["products"]] == list(catalog.KEYS)
    assert found["rotation"]["due_next"] == ["adults", "sixty_plus", "bundle",
                                              "scam_kit"]
    assert found["playbook"].startswith("<untrusted_data")
    assert found["not_marketable_now"] == {}
    assert found["products"][4]["claims"] == list(catalog.CATALOG["scam_kit"].claims)


# ------------------------------------------------------------------ vault

def _norm(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text).replace("**", "")
    return " ".join(text.replace("[ ] ", "").split())


@pytest.mark.skipif(not VAULT.is_dir(), reason="the vault is on the owner's Mac only")
@pytest.mark.parametrize("product", [k for k in catalog.KEYS if k != "operator"])
def test_every_copied_excerpt_is_word_for_word_from_its_source(product):
    d = data.data(product)
    pieces = [(x["source"], x[k]) for x in d["lessons"] for k in ("hook", "line")]
    pieces += [(s["source"], s["text"]) for s in d["blog"]["sections"]]
    pieces += [(c["source"], c["text"]) for c in d["claims"]]
    pieces += [(q["source"], q["text"]) for q in d["quotes"]]
    for source, text in pieces:
        whole = _norm((VAULT / source).read_text())
        for line in text.split("\n"):
            line = re.sub(r"^(?:- |\d\. )", "", line.strip())
            if line:
                assert _norm(line) in whole, (source, line)


@pytest.mark.skipif(not VAULT.is_dir(), reason="the vault is on the owner's Mac only")
def test_the_operators_blog_stories_are_word_for_word():
    for sec in data.blog("operator")["sections"]:
        assert _norm(sec["text"]) in _norm((VAULT / sec["source"]).read_text())
