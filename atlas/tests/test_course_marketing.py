"""Nora markets Miro's course (2026-09-29): organic posts, one card each,
honest by rule, priced live, tagged for attribution, capped, measured."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from atlas.kernel import llm
from atlas.kernel.contracts import Tier
from atlas.kernel.marketing import experiments
from atlas.kernel.permissions import classify
from atlas.modules import course
from atlas.modules.content import postiz
from atlas.modules.course import channels, compose, data, honesty, measure, posts
from atlas.modules.course import price as live

URL = "https://atlasacademy-ai.myshopify.com/products/build-a-safe-ai-operator-the-course"
T0 = datetime(2026, 9, 29, 18, 30, tzinfo=UTC)
PRICE = live.Price(Decimal("20.00"), Decimal("49.00"), T0, available=True)
WORDS = {lang: PRICE.words(lang) for lang in honesty.LANGUAGES}
NO_PRICE = {lang: None for lang in honesty.LANGUAGES}


def _async(value):
    async def f(*_a, **_k):
        return value
    return f


def _actions():
    return {a.name: a for a in course.MODULE.actions()}


def _link(platform="x", exp=42):
    return data.utm_url(URL, platform, exp)


# ------------------------------------------------------------------ tiers

def test_proposing_and_drafting_are_tier_one_with_dry_run_and_rollback():
    acts = _actions()
    for name in ("course.propose", "course.draft_post"):
        a = acts[name]
        assert a.tier is Tier.AUTO and a.dry_run and a.rollback, name
        assert classify(a).tier is Tier.AUTO


def test_every_post_is_tier_two_card_only_and_shows_its_exact_words():
    acts = _actions()
    for name in ("course.publish_post", "course.hand_off"):
        a = acts[name]
        assert a.tier is Tier.ASK and classify(a).tier is Tier.ASK, name
        assert a.card_only and a.verbose_card and a.card_detail and a.vet
        assert set(a.params) == {"post_id", "text"}
        assert "exactly as it goes out" in a.card_detail(
            {"post_id": 3, "text": "hello there"})
        assert "hello there" in a.card_detail({"post_id": 3, "text": "hello there"})
    assert acts["course.publish_post"].irreversible
    assert acts["course.publish_post"].rollback is None


def test_no_course_action_spends_or_is_permanently_denied():
    for a in course.MODULE.actions():
        assert classify(a).allowed, a.name
        for word in ("ad", "boost", "promote", "buy", "purchase", "spend"):
            assert word not in a.name.split(".", 1)[1].split("_"), a.name


def test_the_marketing_agent_proposes_and_growth_drafts():
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all
    register_all()
    assert TOOLS["course_propose"].action == "course.propose"
    assert TOOLS["course_draft_post"].action == "course.draft_post"
    assert set(TOOLS["course_propose"].schema["properties"]) == set(
        _actions()["course.propose"].params)
    assert "course_publish" not in TOOLS and "course_hand_off" not in TOOLS


def test_course_is_a_marketing_business():
    from atlas.modules import marketing
    assert "course" in marketing.BUSINESSES
    assert data.BUSINESS == "course"
    assert data.product()["store"] == "https://atlasacademy-ai.myshopify.com"


# ------------------------------------------------------------------ honesty

@pytest.mark.parametrize(("text", "lang"), [
    ("I make $500 a day with this", "en"),
    ("passive income while you sleep", "en"),
    ("Earn from your agent", "en"),
    ("Aprende a ganar dinero con tu agente", "es"),
    ("Genera ingresos pasivos", "es"),
    ("Tenha uma renda passiva com IA", "pt"),
    ("Aprenda a ganhar dinheiro", "pt"),
    ("حقق أرباح من وكيلك", "ar"),
    ("دخل سلبي كل يوم", "ar"),
])
def test_income_claims_are_refused_in_every_language(text, lang):
    why = honesty.refusal(f"{text}\n{honesty.LABELS[lang]}", lang, None)
    assert why and "income" in why


@pytest.mark.parametrize(("text", "lang"), [
    ("Students say it changed their business", "en"),
    ("★★★★★ from 200 buyers", "en"),
    ("Rated 4.9 stars", "en"),
    ("Trusted by 1,000 owners", "en"),
    ("Lee las reseñas de mis alumnos", "es"),
    ("Veja os depoimentos", "pt"),
    ("شهادات العملاء", "ar"),
    ("\"This course saved my shop\" -- Dana", "en"),
])
def test_fake_testimonials_and_reviews_are_refused(text, lang):
    why = honesty.refusal(f"{text}\n{honesty.LABELS[lang]}", lang, None)
    assert why and ("testimonial" in why or "quote" in why)


@pytest.mark.parametrize(("text", "lang"), [
    ("Hurry, only 3 spots left", "en"),
    ("Limited time offer", "en"),
    ("Today only!", "en"),
    ("Last chance before the price goes up", "en"),
    ("Offer ends tonight ⏰", "en"),
    ("¡Solo hoy! Última oportunidad", "es"),
    ("Por tiempo limitado", "es"),
    ("Só hoje, não perca", "pt"),
    ("عرض لفترة محدودة", "ar"),
    ("اليوم فقط", "ar"),
])
def test_urgency_and_fake_scarcity_are_refused(text, lang):
    why = honesty.refusal(f"{text}\n{honesty.LABELS[lang]}", lang, None)
    assert why and "urgency" in why


def test_a_post_not_labelled_as_his_own_course_is_refused():
    assert "labelled" in honesty.refusal("A calm post about agents.", "en", None)


def test_a_price_other_than_the_live_read_is_refused():
    text = f"My course: $19.\n{honesty.LABELS['en']}"
    assert "live read" in honesty.refusal(text, "en", "$20, was $49")
    assert "not read" in honesty.refusal(text, "en", None)
    ok = f"My course: $20, was $49.\n{honesty.LABELS['en']}"
    assert honesty.refusal(ok, "en", "$20, was $49") is None


@pytest.mark.parametrize("fmt", compose.FORMATS)
async def test_every_lesson_composes_clean_in_english(fmt):
    for item in data.lessons():
        for words in (WORDS, NO_PRICE):
            out, why = await compose.compose(fmt, item, "en", words, _link())
            assert why is None, (fmt, item["id"], why)
            assert honesty.LABELS["en"] in out["text"]
            assert out["translated"] == ""
            assert item["hook"] in out["text"]


# ------------------------------------------------------------------ UTM

def test_the_link_carries_the_utm_scheme():
    link = data.utm_url(URL, "linkedin", 17)
    assert data.utm_of(link) == {"utm_source": "linkedin", "utm_medium": "organic",
                                 "utm_campaign": "17"}
    assert link.startswith(URL + "?")


def test_utm_tags_replace_old_ones_and_refuse_anything_off_the_store():
    link = data.utm_url(URL + "?utm_source=ads&variant=1", "x", 5)
    assert data.utm_of(link)["utm_source"] == "x"
    assert "variant=1" in link
    for bad in ("https://example.com/p", "http://atlasacademy-ai.myshopify.com/p"):
        with pytest.raises(ValueError):
            data.utm_url(bad, "x", 5)
    for platform, exp in (("X Ads", 5), ("x", 0), ("x", True)):
        with pytest.raises(ValueError):
            data.utm_url(URL, platform, exp)


async def test_the_composed_post_carries_its_tagged_link():
    out, _ = await compose.compose("social", data.lessons()[0], "en", WORDS,
                                   _link("threads", 99))
    assert "utm_source=threads&utm_medium=organic&utm_campaign=99" in out["text"]


# ------------------------------------------------------------------ channels

INTEGRATIONS = [
    {"id": "tt1", "identifier": "tiktok-business", "name": "atlasstories7"},
    {"id": "tt2", "identifier": "tiktok-business", "name": "atlasclips12"},
    {"id": "ig1", "identifier": "instagram", "name": "atlas.clipsdrop"},
    {"id": "x1", "identifier": "x", "name": "atlasacademy"},
    {"id": "x2", "identifier": "x", "name": "atlas_clips"},
    {"id": "li1", "identifier": "linkedin", "name": "Miro Mekhael"},
    {"id": "th1", "identifier": "threads", "name": "atlas.clipsdrop"},
    {"id": "bs1", "identifier": "bluesky", "name": "atlas", "disabled": True},
    {"id": "yt1", "identifier": "youtube", "name": "ATLAS"},
]


def test_clips_accounts_and_media_only_channels_are_never_course_channels(
        monkeypatch):
    monkeypatch.setenv(postiz.CLIPS_ENV, "li1")      # even a text one, if named
    usable = {i["id"] for i in channels.course_integrations(INTEGRATIONS)}
    assert usable == {"x1"}
    monkeypatch.delenv(postiz.CLIPS_ENV)
    usable = {i["id"] for i in channels.course_integrations(INTEGRATIONS)}
    assert usable == {"x1", "li1"}


def test_youtube_instagram_and_tiktok_are_refused_by_name():
    for name in ("youtube", "youtube_community", "instagram", "tiktok"):
        assert "not a course channel" in channels.shape_refusal(name)
    assert channels.shape_refusal("linkedin") is None
    assert channels.shape_refusal("shopify_blog") is None
    assert channels.shape_refusal("postiz:x1") is None
    assert "unknown channel" in channels.shape_refusal("facebook_ads")


class FakePostiz:
    """Postiz answered at `httpx.AsyncClient.request`; records what was sent."""

    def __init__(self, integrations, settings=None):
        self.integrations = integrations
        self.settings = settings if settings is not None else {"output": {
            "settings": {"properties": {}}}}
        self.posted: list[dict] = []

    async def request(self, _client, method, url, **kw):
        req = httpx.Request(method, url)
        if url.endswith("/integrations"):
            return httpx.Response(200, json=self.integrations, request=req)
        if "/integration-settings/" in url:
            return httpx.Response(200, json=self.settings, request=req)
        if url.endswith("/posts") and method == "POST":
            self.posted.append(kw.get("json"))
            return httpx.Response(200, json=[{"postId": "pz-1"}], request=req)
        return httpx.Response(404, json={}, request=req)


@pytest.fixture
def fake_postiz(monkeypatch):
    fake = FakePostiz(list(INTEGRATIONS))
    monkeypatch.setenv(postiz.KEY_ENV, "pz-test-key")
    monkeypatch.delenv(postiz.CLIPS_ENV, raising=False)

    async def request(client, method, url, **kw):
        return await fake.request(client, method, url, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    return fake


async def test_resolving_a_clips_integration_is_refused(fake_postiz):
    with pytest.raises(channels.ChannelError, match="clips"):
        await channels.resolve("postiz:tt2")
    with pytest.raises(channels.ChannelError, match="clips"):
        await channels.resolve("postiz:th1")
    with pytest.raises(channels.ChannelError, match="text post"):
        await channels.resolve("postiz:tt1")
    target = await channels.resolve("postiz:x1")
    assert target.provider.platform == "x" and not target.handoff


async def test_a_post_is_refused_if_its_channel_became_a_clips_account(fake_postiz,
                                                                      monkeypatch):
    target = await channels.resolve("postiz:x1")
    monkeypatch.setenv(postiz.CLIPS_ENV, "x1")
    status, detail, ref = await channels.post_text(target, "hello")
    assert status == channels.REFUSED and ref is None and "clips" in detail
    assert fake_postiz.posted == []


async def test_a_text_post_keeps_its_link_and_sends_only_the_type(fake_postiz):
    target = await channels.resolve("postiz:li1")
    status, _detail, ref = await channels.post_text(target, "hi " + _link())
    assert status == channels.POSTED and ref == "pz-1"
    body = fake_postiz.posted[0]
    assert body["shortLink"] is False
    assert body["posts"][0]["settings"] == {"__type": "linkedin"}
    assert _link() in body["posts"][0]["value"][0]["content"]


async def test_a_provider_that_requires_an_unknown_setting_is_not_posted_blind(
        fake_postiz):
    fake_postiz.settings = {"output": {"settings": {"required": ["who_can_reply"]}}}
    target = await channels.resolve("postiz:x1")
    status, detail, _ = await channels.post_text(target, "hi")
    assert status == channels.REFUSED and "who_can_reply" in detail
    assert fake_postiz.posted == []


async def test_the_channel_listing_says_what_is_excluded_and_why(fake_postiz):
    found = await channels.listing()
    names = {c["channel"] for c in found["postiz"]["channels"]}
    assert names == {"postiz:x1", "postiz:li1"}
    why = {e["name"]: e["why"] for e in found["postiz"]["excluded"]}
    assert "clips" in why["atlasclips12"] and "clips" in why["atlas_clips"]
    assert "youtube" in found["not_channels"] and "read_at" in found


async def test_postiz_unset_is_said_not_filled_in(monkeypatch):
    monkeypatch.delenv(postiz.KEY_ENV, raising=False)
    found = await channels.listing()
    assert found["postiz"]["status"] == "unknown"
    with pytest.raises(channels.ChannelError, match="unset"):
        await channels.resolve("postiz:x1")


# ------------------------------------------------------------------ price

def test_the_price_is_read_with_its_time_and_worded_from_the_read():
    payload = {"product": {"variants": [{"price": "20.00",
                                         "compare_at_price": "49.00"}]}}
    p = live.parse_product(payload, T0)
    assert p.words() == "$20, was $49"
    assert p.words("es") == "$20, antes $49"
    assert "read from the store at 2026-09-29 18:30 UTC" in p.card_line()
    assert live.from_record(p.record()).same_as(p)


@pytest.mark.parametrize("payload", [
    {}, {"product": {"variants": []}},
    {"product": {"variants": [{"price": "twenty"}]}},
    {"product": {"variants": [{"price": "20.00", "price_currency": "EUR"}]}},
])
def test_an_unreadable_price_is_unknown_never_guessed(payload):
    p = live.parse_product(payload, T0)
    assert not p.read and p.words() is None
    assert p.card_line().startswith("Price: UNKNOWN")


async def test_the_reader_reads_only_the_store(monkeypatch):
    p = await live.read_price("https://example.com/products/x.json")
    assert not p.read and "own store" in p.why
    p = await live.read_price("http://atlasacademy-ai.myshopify.com/x.json")
    assert not p.read and "https" in p.why


async def test_a_post_with_no_price_read_names_no_price():
    out, _ = await compose.compose("social", data.lessons()[1], "en", NO_PRICE,
                                   _link())
    assert "$" not in out["text"]


async def test_approval_refuses_when_the_store_price_changed(monkeypatch):
    post = {"id": 7, "status": "drafted", "channel": "linkedin", "language": "en",
            "body": "text", "price": PRICE.record()}
    monkeypatch.setattr(live, "read_price", _async(
        live.Price(Decimal("25.00"), Decimal("49.00"), T0)))
    why, _ = await posts._approval_refusal(post, 7, "text", handoff=True)
    assert "now $25, was $49" in why
    monkeypatch.setattr(live, "read_price", _async(live.not_read("timed out")))
    why, _ = await posts._approval_refusal(post, 7, "text", handoff=True)
    assert "could not be re-read" in why


async def test_approval_refuses_changed_text_and_the_wrong_card_kind(monkeypatch):
    post = {"id": 7, "status": "drafted", "channel": "linkedin", "language": "en",
            "body": "text", "price": {}}
    why, _ = await posts._approval_refusal(post, 7, "other", handoff=True)
    assert "changed since the card" in why
    why, _ = await posts._approval_refusal(post, 7, "text", handoff=False)
    assert "handed to Miro" in why


# ------------------------------------------------------------------ translations

def _fake_model(monkeypatch, answer):
    seen: list = []

    async def think(need, system, messages, **_kw):
        seen.append((need, system, [m.content for m in messages]))
        text = answer(messages[0].content) if callable(answer) else answer
        return SimpleNamespace(text=text)

    monkeypatch.setattr(llm, "think", think)
    return seen


SPANISH = {
    "Ask your AI how much you made today. Mine refuses to guess.":
        "Pregúntale a tu IA cuánto vendiste hoy. La mía se niega a adivinar.",
    "Every figure is read now, from the source, with the time it was read.":
        "Cada cifra se lee ahora, de la fuente, con la hora en que se leyó.",
    "Lesson {LESSON} of my course, \"{TITLE}\". {PRICE}.":
        "Lección {LESSON} de mi curso, \"{TITLE}\". {PRICE}.",
}


def _spanish(source: str) -> str:
    for en, es in SPANISH.items():
        source = source.replace(en, es)
    return source


async def test_a_translation_is_the_models_marked_so_and_priced_in_code(monkeypatch):
    seen = _fake_model(monkeypatch, _spanish)
    item = data.lesson("2.1")
    out, why = await compose.compose("social", item, "es", WORDS, _link())
    assert why is None
    assert out["translated"] == "model"
    assert "Lección 2.1 de mi curso" in out["text"]
    assert "$20, antes $49" in out["text"]
    assert honesty.LABELS["es"] in out["text"] and _link() in out["text"]
    assert out["english"].startswith(item["hook"])
    # The model never saw a price, a link or a figure it could change.
    prompt = seen[0][2][0]
    assert "$" not in prompt and "http" not in prompt and "{PRICE}" in prompt


@pytest.mark.parametrize(("answer", "why"), [
    (lambda s: s.replace("{PRICE}", "$20"), "placeholder"),
    (lambda s: _spanish(s) + " Solo 3 plazas.", "numbers"),
    (lambda s: _spanish(s) + " https://evil.example", "added"),
    (lambda s: _spanish(s) + " #ia", "added"),
    (lambda s: _spanish(s).replace("La mía", "Gana dinero; la mía"), "income"),
    (lambda s: _spanish(s) + " ¡Solo hoy!", "urgency"),
    (lambda s: "", "empty"),
])
async def test_a_translation_that_adds_anything_is_refused(monkeypatch, answer, why):
    _fake_model(monkeypatch, answer)
    out, reason = await compose.compose("social", data.lesson("2.1"), "es", WORDS,
                                        _link())
    assert out is None and why in reason


def test_a_translation_card_says_it_is_ai_drafted_and_shows_the_english():
    post = {"id": 4, "experiment_id": 42, "channel": "postiz:x1", "platform": "x",
            "language": "ar", "lesson_id": "4.2", "link": _link(),
            "translated": "model", "english": "The English text",
            "price": PRICE.record()}
    reason = posts.reason_for(post, "x @atlasacademy (Postiz)")
    first = reason.splitlines()[0]
    assert first.startswith("AI-DRAFTED TRANSLATION (Arabic)")
    assert "The English text" in reason
    assert ("$20, was $49, available, read from the store at 2026-09-29 18:30 UTC"
            in reason)
    assert "utm_campaign=42" in reason


# ------------------------------------------------------------------ caps

class Rows:
    """`fetch_one` answering count queries by which cap they ask about."""

    def __init__(self, week=0, today=0):
        self.week, self.today, self.sql = week, today, []

    async def fetch_one(self, sql, *args):
        self.sql.append((sql, args))
        if "channel = $1" in sql:
            return {"n": self.today}
        return {"n": self.week}


async def test_the_weekly_cap_defaults_to_seven_across_channels(monkeypatch):
    monkeypatch.delenv("COURSE_POSTS_PER_WEEK", raising=False)
    assert channels.weekly_cap() == 7
    rows = Rows(week=7)
    monkeypatch.setattr(posts, "fetch_one", rows.fetch_one)
    why = await posts.proposal_cap_refusal("postiz:x1", T0)
    assert "7 of 7" in why
    rows.week = 6
    assert await posts.proposal_cap_refusal("postiz:x1", T0) is None


async def test_one_post_per_channel_per_day(monkeypatch):
    rows = Rows(week=1, today=1)
    monkeypatch.setattr(posts, "fetch_one", rows.fetch_one)
    assert "one per channel a day" in await posts.proposal_cap_refusal("linkedin", T0)


async def test_the_publish_claim_is_serialised_and_counts_both_caps(monkeypatch):
    calls = []

    async def locked(lock, sql, *args):
        calls.append((lock, sql, args))

    monkeypatch.setattr(posts, "fetch_one_locked", locked)
    why = await posts.claim({"id": 3, "channel": "postiz:x1"}, "publishing", T0)
    assert "caps hold it back" in why
    lock, sql, args = calls[0]
    assert lock == posts.CLAIM_LOCK
    assert "count(*)" in sql and "NOT EXISTS" in sql and "d.channel = $6" in sql
    assert args[4] == channels.weekly_cap() and args[5] == "postiz:x1"
    # New York calendar: the week starts Monday 00:00 America/New_York.
    assert args[3] == datetime(2026, 9, 28, tzinfo=posts.ZONE)
    assert args[6] == datetime(2026, 9, 29, tzinfo=posts.ZONE)


async def test_a_capped_post_is_not_published(monkeypatch):
    post = {"id": 3, "experiment_id": 9, "status": "drafted", "channel": "postiz:x1",
            "language": "en", "body": "t", "price": {}}
    monkeypatch.setattr(posts, "_post", _async(post))
    monkeypatch.setattr(posts, "_approval_refusal", _async((None, PRICE)))
    target = channels.Target("postiz:x1", channels.TEXT_PROVIDERS["x"], "x", False,
                             integration="x1", identifier="x")
    monkeypatch.setattr(channels, "resolve", _async(target))
    monkeypatch.setattr(posts, "claim", _async("the caps hold it back"))
    sent = []
    monkeypatch.setattr(channels, "post_text", lambda *a: sent.append(a))
    result = await posts.publish_post(3, "t")
    assert not result.ok and "caps" in result.detail and sent == []


async def test_publishing_starts_a_seven_day_clock(monkeypatch):
    post = {"id": 3, "experiment_id": 9, "status": "drafted", "channel": "postiz:x1",
            "language": "en", "body": "t", "price": {}}
    monkeypatch.setattr(posts, "_post", _async(post))
    monkeypatch.setattr(posts, "_approval_refusal", _async((None, PRICE)))
    target = channels.Target("postiz:x1", channels.TEXT_PROVIDERS["x"], "x", False,
                             integration="x1", identifier="x")
    monkeypatch.setattr(channels, "resolve", _async(target))
    monkeypatch.setattr(posts, "claim", _async(None))
    monkeypatch.setattr(channels, "post_text",
                        _async((channels.POSTED, "Postiz post pz-1", "pz-1")))
    monkeypatch.setattr(posts, "execute_sql", _async("OK"))
    attached = []

    async def attach(eid, ref, *, posted_at=None, after=None):
        attached.append((eid, ref, after))
    monkeypatch.setattr(experiments, "attach", attach)
    result = await posts.publish_post(3, "t")
    assert result.ok and attached == [(9, "pz-1", timedelta(days=7))]


# ------------------------------------------------------------------ cards

async def test_one_card_per_drafted_post_with_the_post_id_in_the_title(monkeypatch):
    rows = [
        {"id": 11, "experiment_id": 1, "channel": "linkedin", "platform": "linkedin",
         "fmt": "social", "language": "en", "lesson_id": "0.1", "link": _link(),
         "body": "A", "english": "A", "translated": "", "price": PRICE.record(),
         "status": "drafted"},
        {"id": 12, "experiment_id": 2, "channel": "postiz:x1", "platform": "x",
         "fmt": "short", "language": "pt", "lesson_id": "1.2", "link": _link(),
         "body": "B", "english": "E", "translated": "model",
         "price": json.dumps(PRICE.record()), "status": "drafted"},
    ]
    monkeypatch.setattr(posts, "waiting_for_card", _async(rows))
    signals = await course.MODULE.signals()
    assert [s.suggests for s in signals] == [posts.HAND_OFF, posts.PUBLISH]
    assert [s.params for s in signals] == [{"post_id": 11, "text": "A"},
                                          {"post_id": 12, "text": "B"}]
    assert "#11" in signals[0].title and "#12" in signals[1].title
    assert signals[1].body.startswith("AI-DRAFTED TRANSLATION (Portuguese)")


def test_the_card_vet_refuses_what_the_rules_refuse():
    assert posts.vet({"post_id": 1, "text": "Earn $500 a week"})
    assert posts.vet({"post_id": 1, "text": "Hurry, today only"})
    assert posts.vet({"post_id": "x", "text": "fine"})
    assert posts.vet({"post_id": 1, "text": "A calm post."}) is None


# ------------------------------------------------------------------ measurement

async def test_no_posthog_key_is_unknown_and_says_sales_are_unknown(monkeypatch):
    monkeypatch.delenv("POSTHOG_PERSONAL_KEY", raising=False)
    found, why = await measure.read(42)
    assert found is None and "POSTHOG_PERSONAL_KEY" in why
    assert "no Shopify order read path" in why


async def test_a_store_that_never_sent_posthog_an_event_is_unknown_not_zero(
        monkeypatch):
    from atlas.modules import posthog
    monkeypatch.setenv("POSTHOG_PERSONAL_KEY", "phx-test")
    asked = []

    async def hogql(sql):
        asked.append(sql)
        return [[0]], None
    monkeypatch.setattr(posthog, "hogql", hogql)
    found, why = await measure.read(42)
    assert found is None and "never sent PostHog an event" in why
    assert "atlasacademy-ai.myshopify.com" in asked[0]


async def test_utm_tagged_store_visits_are_read_with_their_time(monkeypatch):
    from atlas.modules import posthog
    monkeypatch.setenv("POSTHOG_PERSONAL_KEY", "phx-test")
    asked = []

    async def hogql(sql):
        asked.append(sql)
        return ([[120]] if len(asked) == 1 else [[14, 9]]), None
    monkeypatch.setattr(posthog, "hogql", hogql)
    found, note = await measure.read(42)
    assert found["value"] == 14.0 and found["metric"] == "store_visits"
    assert found["observed_at"].tzinfo is not None
    assert "utm_campaign = '42'" in asked[1] and "utm_medium = 'organic'" in asked[1]
    assert "sales unknown" in note


@pytest.mark.parametrize("bad", ["42' OR 1=1 --", -1, 0, True, None])
async def test_only_an_integer_id_reaches_hogql(monkeypatch, bad):
    monkeypatch.setenv("POSTHOG_PERSONAL_KEY", "phx-test")
    from atlas.modules import posthog
    monkeypatch.setattr(posthog, "hogql", lambda *_a: pytest.fail("queried"))
    found, why = await measure.read(bad)
    assert found is None and "not an experiment id" in why


async def test_a_course_experiment_is_measured_from_the_store(monkeypatch):
    monkeypatch.setattr(measure, "read", _async((
        {"value": 14.0, "metric": "store_visits", "observed_at": T0,
         "source": "PostHog: 14 store pageview(s)"}, measure.NO_ORDERS)))
    reading, why = await experiments.read_metric("x", "course", "pz-1",
                                                 experiment_id=42)
    assert why is None and reading.value == 14.0
    assert reading.metric == experiments.COURSE_METRIC
    assert "sales unknown" in reading.source


async def test_unmeasured_for_want_of_a_baseline_keeps_the_reading_in_the_lesson(
        monkeypatch):
    row = {"id": 42, "business": "course", "platform": "x", "variant": "lesson 2.1",
           "posted_ref": "pz-1", "metric": "store_visits"}
    monkeypatch.setattr(experiments, "_due", _async([row]))
    monkeypatch.setattr(experiments, "read_metric", _async((
        experiments.Reading(14.0, "store_visits", T0, "PostHog"), None)))
    monkeypatch.setattr(experiments, "baseline_for", _async((
        None, "baseline has 0 of 5 comparable readings")))
    monkeypatch.setattr(experiments, "execute_sql", _async("OK"))
    recorded = []

    async def record(eid, outcome, lesson, **kw):
        recorded.append((eid, outcome, lesson, kw))
    results = await experiments.measure_due(record=record)
    assert results[0]["outcome"] == "unmeasured"
    lesson = recorded[0][2]
    assert "0 of 5" in lesson and "read 14 store_visits at 18:30 UTC" in lesson
    assert recorded[0][3]["observed"] == 14.0


async def test_the_course_baseline_is_earlier_course_readings(monkeypatch):
    seen = []

    async def fetch_all(sql, *args):
        seen.append((sql, args))
        return [{"value": v} for v in (3, 5, 8, 2, 6)]
    monkeypatch.setattr(experiments, "fetch_all", fetch_all)
    base, why = await experiments.baseline_for("x", "store_visits",
                                               business="course", exclude_ref="pz-1")
    assert why is None and base.value == 5 and base.metric == "store_visits"
    assert "marketing_experiments" in seen[0][0] and seen[0][1][0] == "course"


# ------------------------------------------------------------------ propose

class Ledger:
    """The two INSERTs a proposal makes, and every UPDATE after them."""

    def __init__(self, week=0, today=0):
        self.inserts: list[tuple[str, tuple]] = []
        self.updates: list[tuple[str, tuple]] = []
        self.week, self.today = week, today

    async def fetch_one(self, sql, *args):
        if "INSERT INTO marketing_experiments" in sql:
            self.inserts.append(("experiment", args))
            return {"id": 42}
        if "INSERT INTO course_posts" in sql:
            self.inserts.append(("post", args))
            return {"id": 7}
        if "channel = $1" in sql:
            return {"n": self.today}
        return {"n": self.week}

    async def execute_sql(self, sql, *args):
        self.updates.append((sql, args))
        return "OK"

    async def fetch_all(self, sql, *args):
        return []           # no posts this week: every product is due


@pytest.fixture
def ledger(monkeypatch):
    book = Ledger()
    monkeypatch.setattr(posts, "fetch_one", book.fetch_one)
    monkeypatch.setattr(posts, "fetch_all", book.fetch_all)
    monkeypatch.setattr(posts, "execute_sql", book.execute_sql)
    monkeypatch.setattr(live, "read_price", _async(PRICE))
    return book


async def test_proposing_files_an_experiment_and_drafts_a_tagged_priced_post(ledger):
    result = await posts.propose("operator", "linkedin", "4.2", "en",
                                 "the never-list hook sends more store visits")
    assert result.ok, result.detail
    kind, exp_args = ledger.inserts[0]
    assert kind == "experiment" and exp_args[0] == "course"
    assert exp_args[1] == "linkedin" and exp_args[4] == "store_visits"
    kind, post_args = ledger.inserts[1]
    link = post_args[-1]
    assert post_args[-2] == "operator"
    assert data.utm_of(link) == {"utm_source": "linkedin", "utm_medium": "organic",
                                 "utm_campaign": "42"}
    drafted = [a for sql, a in ledger.updates if "status = 'drafted'" in sql]
    body = drafted[0][1]
    assert "$20, was $49" in body and link in body
    assert honesty.LABELS["en"] in body
    assert json.loads(drafted[0][4])["read_at"].startswith("2026-09-29T18:30")
    assert result.rollback_token == "7:42"


async def test_a_translation_that_fails_leaves_the_post_for_growth(ledger,
                                                                  monkeypatch):
    _fake_model(monkeypatch, lambda s: s + " ¡Solo hoy!")
    result = await posts.propose("operator", "linkedin", "2.1", "es",
                                 "Spanish readers")
    assert result.ok and not result.evidence["drafted"]
    assert "course_draft_post" in result.detail
    kept = [a for sql, a in ledger.updates if "status = 'proposed'" in sql]
    assert kept and "Spanish translation" in kept[0][1]


@pytest.mark.parametrize(("channel", "lesson", "lang", "why"), [
    ("youtube", "2.1", "en", "not a course channel"),
    ("linkedin", "9.9", "en", "no lesson"),
    ("linkedin", "2.1", "fr", "unknown language"),
])
async def test_a_bad_proposal_writes_nothing(ledger, channel, lesson, lang, why):
    result = await posts.propose("operator", channel, lesson, lang, "h")
    assert not result.ok and why in result.detail
    assert ledger.inserts == []


async def test_a_full_week_writes_nothing(ledger):
    ledger.week = 7
    result = await posts.propose("operator", "linkedin", "2.1", "en", "h")
    assert not result.ok and "7 of 7" in result.detail and ledger.inserts == []
