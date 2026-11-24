"""The course-promo lane (the owner, relayed 2026-09-29): two reels a week for his
course on the AI-stories accounts, honest by rule."""
from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from atlas.kernel import reels as day
from atlas.kernel.contracts import ActionResult, Tier
from atlas.modules.content import captions, course, guard, higgsfield, reel_script, veo
from atlas.modules.content import reels as playbook
from atlas.modules.content.drafts import AI_LABEL

URL = "https://agency.example.com/academy/safe-ai-operator/"
PRICE_JSON = "https://agency.example.com/academy/catalog.json"
TUE, WED, THU, FRI, SAT = (date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1),
                           date(2026, 10, 2), date(2026, 10, 3))


READ_AT = datetime(2026, 9, 29, 16, 5, tzinfo=UTC)
SALE = course.Price(Decimal("20.00"), Decimal("49.00"), READ_AT)
PLAIN = course.Price(Decimal("20.00"), None, READ_AT)
UNREAD = course.not_read("the store answered 503", READ_AT)


def _async(value):
    async def f(*a, **k):
        return value
    return f


def _own() -> list[dict]:
    """The first course's own lessons (bare ids), in order: the tests that
    pick a lesson by position were written for it."""
    return [x for x in course.lessons() if ":" not in x["id"]]


# ------------------------------------------------------------------ the data

def test_the_course_data_ships_every_hooked_lesson_and_the_product():
    rows = course.lessons()
    ids = [x["id"] for x in rows]
    # The first course keeps its bare ids, in order (p.*: the owner's promo hooks).
    assert [i for i in ids if ":" not in i] == [
        "0.1", "1.1", "1.2", "2.1", "3.1", "4.2", "5.2", "6.1", "8.1",
        "p.1", "p.2", "p.3", "p.4", "p.5", "p.6", "p.7"]
    # the owner, 2026-10-05: every course is advertised -- the courses take turns.
    assert ids[:5] == ["0.1", "sell-with-ai:0.2", "ai-for-every-age-adults:1.4",
                       "ai-for-every-age-60plus:3.1", "1.1"]
    assert len(ids) == len(set(ids)) == 16 + 12 + 10 + 8
    adults = course.lesson("ai-for-every-age-adults:1.4")
    assert adults["no"] == "1.4" and adults["product"] == "ai-for-every-age-adults"
    assert course.product(adults)["url"] == "https://agency.example.com/academy/ai-for-every-age/"
    assert all(q.is_file() for x in rows for q in course.images(x))
    p = course.product()
    assert "price" not in p and p["url"] == URL     # read live, never stored
    for x in course.lessons():
        assert x["hook"] and x["line"] and x["title"]


def test_the_product_images_are_in_the_repo_and_reasonably_small():
    paths = course.images()
    assert [p.name for p in paths] == ["1-cover.jpg", "2-eight-rules.jpg",
                                       "3-whats-inside.jpg"]
    for p in paths:
        assert p.is_file() and p.stat().st_size < 400_000


@pytest.mark.parametrize("beats", [1, 2])
@pytest.mark.parametrize("price", [SALE, PLAIN, UNREAD])
def test_every_lesson_passes_every_rule_and_fits_the_length(beats, price):
    for x in course.lessons():
        assert course.honesty_refusal(x, beats, price) is None, x["id"]
        assert len(" ".join(course.spoken(x, price)).split()) <= course.MAX_WORDS


# ------------------------------------------------------------------ honesty

@pytest.mark.parametrize("claim", [
    "I make $500 a day with this", "passive income while you sleep",
    "earn from your agent", "Students earned back the price", "get rich with AI",
    "$3,000/month on autopilot", "a six-figure side hustle",
    "made $10k last month", "guaranteed results", "INCOME"])
def test_income_claims_are_refused(claim):
    assert guard.income_refusal(claim) is not None


@pytest.mark.parametrize("fine", [
    "$49, link in bio", "$20 (was $49)", "$20, down from $49, link in bio.",
    "learn to build it", "Ask your AI how much you made today.",
    "It's in my course, Build a Safe AI Operator. $49, link in bio."])
def test_the_price_and_ordinary_words_pass(fine):
    assert guard.income_refusal(fine) is None


def test_a_lesson_that_claims_income_is_refused_in_script_and_caption(monkeypatch):
    bad = dict(_own()[0], line="My students earn money with it.")
    assert "income" in course.honesty_refusal(bad, 1)
    base = _own()[0]
    monkeypatch.setattr(course, "HASHTAGS", "#passiveincome")
    assert course.honesty_refusal(base, 1) is not None


def test_the_render_refuses_income_words_in_a_draft():
    x = _own()[0]
    body = course.compose(x, 1).replace("link in bio", "make $500 a day")
    script = reel_script.parse(body, max_scenes=2)
    assert "income" in course.text_refusal(script)


# ------------------------------------------------------------------ no screens

@pytest.mark.parametrize("prompt", [
    "a laptop screen showing a dashboard", "a phone with a notification",
    "code scrolling in a terminal", "a glowing user interface", "an AI chat window",
    "a spreadsheet of sales", "hands typing on a keyboard", "a website in a browser"])
def test_screen_words_are_refused_in_a_course_prompt(prompt):
    assert guard.screen_refusal(prompt) is not None
    assert guard.ip_refusal(f"{guard.COURSE_PROMPT_HEAD} {prompt}") is not None


def test_every_catalog_shot_passes_both_doors_and_shows_no_one():
    for i, shot in enumerate(course.SHOTS, 1):
        prompt = course.beat_prompt(shot, i, 2)
        assert prompt.startswith(guard.COURSE_PROMPT_HEAD)
        assert guard.screen_refusal(prompt) is None
        assert guard.ip_refusal(prompt) is None
        assert guard.refusal(prompt) is None
        assert "Nobody appears" in prompt and "nobody speaks" in prompt


def test_the_screen_rule_is_the_course_lanes_only():
    assert guard.ip_refusal("A robot looks at a phone screen in the rain.") is None


async def test_both_clip_doors_refuse_a_course_screen_without_a_request(monkeypatch):
    import httpx

    class Never:
        def __init__(self, *a, **k):
            raise AssertionError("no request is ever sent")

    monkeypatch.setattr(httpx, "AsyncClient", Never)
    monkeypatch.setenv(veo.KEY_ENV, "k")
    bad = f"{guard.COURSE_PROMPT_HEAD} A glowing dashboard on a monitor."
    with pytest.raises(veo.VeoError, match="screen"):
        await veo.clip(bad)
    for name in (higgsfield.KEY_ID_ENV, higgsfield.SECRET_ENV):
        monkeypatch.setenv(name, "k")
    with pytest.raises(higgsfield.HiggsfieldError, match="screen"):
        await higgsfield.clip(bad)


def test_a_tampered_shot_is_refused_before_any_clip():
    x = _own()[0]
    body = course.compose(x, 1).replace("[[", "[[a monitor ").replace(
        "a monitor light_lattice", "a monitor")
    script = reel_script.parse(body, max_scenes=2)
    assert "no catalog shot" in course.text_refusal(script)


async def test_a_changed_draft_never_renders(monkeypatch, memory_backend):
    from atlas.modules import content
    x = _own()[2]
    body = course.compose(x, 2).replace(x["line"], "Something else entirely.")
    memory_backend.responses["FROM content_drafts WHERE id"] = {
        "id": 9, "kind": "story_script", "body": body}

    async def no_backend(*a, **k):
        raise AssertionError("no backend is chosen for a changed draft")

    monkeypatch.setattr(content, "_choice", no_backend)
    result = await content.render_video(9)
    assert not result.ok and "no longer matches" in result.detail


async def test_an_intact_draft_verifies():
    x = _own()[4]
    script = reel_script.parse(course.compose(x, 2), max_scenes=2)
    assert course.is_course(script)
    assert await course.verify(1, script) is None
    assert course.text_refusal(script) is None


# ------------------------------------------------------------------ the caption

@pytest.mark.parametrize("beats", [1, 2])
def test_the_caption_says_my_course_link_in_bio_and_the_url(beats):
    for x in course.lessons():
        script = reel_script.parse(course.compose(x, beats, SALE), max_scenes=2)
        cap = script.caption
        assert course.product(x)["url"] in cap
        assert "link in bio" in cap and "my course" in cap
        assert f"Lesson {x['no']} of my course" in cap
        assert "$20 (was $49), link in bio" in cap
        assert cap.rstrip().endswith(AI_LABEL)
        assert guard.refusal(cap) is None
        assert "my course" in course.narration(script)
        assert len(script.scenes) == beats


def test_the_end_card_carries_the_price_and_link_in_bio():
    cues = captions.course_cues(None, ["Hook here.", "Line here."], 10.0)
    words = course.price_words(SALE)
    ass = captions.build_course_ass(cues, 25.0, end_start=22.0,
                                    end_lines=course.end_lines(words),
                                    label="MY COURSE", price=words)
    assert "LINK IN BIO" in ass and "$20 (was $49)" in ass and AI_LABEL in ass
    assert "Label,,0,0,0,,MY COURSE" in ass
    assert "Hook here." in ass


def test_the_layout_holds_the_reel_to_20_to_35_seconds():
    for beats in (1, 2):
        for voice in (8.0, 15.0, 20.0):
            each, total = course.layout(voice, beats)
            assert course.SECONDS[0] <= total <= course.SECONDS[1]
            assert each >= course.MIN_IMAGE_SECONDS
    with pytest.raises(course.CourseRenderError):
        course.layout(36.0, 2)


# ------------------------------------------------------------------ cadence

def test_two_a_week_fall_on_tuesday_and_friday():
    assert course.slot_days(2) == (1, 4)
    assert [course.entitled(d, 2) for d in (date(2026, 9, 28), TUE, WED, THU,
                                            FRI, SAT)] == [0, 1, 1, 1, 2, 2]


def test_tuesday_is_due_and_wednesday_not_after_tuesday_posted():
    assert course.due(TUE, 2, 0)
    assert not course.due(WED, 2, 1)
    assert course.due(FRI, 2, 1)
    assert not course.due(SAT, 2, 2)


def test_a_slot_missed_for_the_cap_moves_to_the_next_day_not_the_next_week():
    assert course.due(WED, 2, 0)          # Tuesday skipped: Wednesday takes it
    assert course.due(THU, 2, 0)
    monday = date(2026, 10, 5)
    assert not course.due(monday, 2, 0)   # last week's shortfall is gone
    assert course.week_start(monday) > course.week_start(SAT)


def test_the_weekly_count_is_an_environment_setting(monkeypatch):
    assert course.per_week() == 2
    monkeypatch.setenv("COURSE_REELS_PER_WEEK", "3")
    assert course.per_week() == 3 and course.slot_days(3) == (0, 2, 4)
    monkeypatch.setenv("COURSE_REELS_PER_WEEK", "0")
    assert course.per_week() == 0


def _day_stubs(monkeypatch, *, done=0, tried=0, posted=0):
    from atlas.modules.content import calendar as cal
    from atlas.modules.content import crosspost
    monkeypatch.setattr(course, "ensure_format", _async(5))
    monkeypatch.setattr(course, "done_this_week", _async(done))
    monkeypatch.setattr(course, "attempted_today", _async(tried))
    monkeypatch.setattr(course, "waiting", _async(None))
    monkeypatch.setattr(day, "no_platform_line", _async(None))
    monkeypatch.setattr(crosspost, "posted_today", _async(posted))
    monkeypatch.setattr(cal, "clips_today", _async(0))


def _real_post_stub(monkeypatch, outcome: str = "posted") -> list[dict]:
    """A post_or_card stand-in that binds its call against the real
    signature, so a missing required keyword fails here, not in production."""
    real = inspect.signature(day.post_or_card)
    posted: list[dict] = []

    async def post_or_card(params, reason, **kw):
        real.bind(params, reason, **kw)
        posted.append({"params": params, "reason": reason, **kw})
        return outcome, None, "Instagram (stories): posted"

    monkeypatch.setattr(day, "post_or_card", post_or_card)
    return posted


TUE_NOON = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)
WED_NOON = datetime(2026, 9, 30, 16, 0, tzinfo=UTC)


async def test_no_course_reel_is_made_once_the_post_cap_is_used(monkeypatch):
    monkeypatch.setenv("REELS_AUTOPUBLISH_PER_DAY", "3")
    _day_stubs(monkeypatch, posted=3)

    async def never(req):
        raise AssertionError("nothing is drafted once the cap is used")

    monkeypatch.setattr(day, "execute", never)
    lines = await day.course_lines(TUE_NOON)
    assert "3 of 3 automatic posts already made" in lines[0]
    assert "next eligible day" in lines[0]


async def test_a_day_that_is_not_due_does_nothing(monkeypatch):
    _day_stubs(monkeypatch, done=1)

    async def never(req):
        raise AssertionError("nothing is drafted on a day that is not due")

    monkeypatch.setattr(day, "execute", never)
    lines = await day.course_lines(WED_NOON)
    assert lines[0].startswith("Course: none due today")


async def test_the_lane_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("COURSE_REELS_PER_WEEK", "0")
    assert (await day.course_lines(TUE_NOON))[0].startswith("Course: off")


async def test_a_due_day_drafts_renders_and_posts_on_the_stories_path_only(
        monkeypatch):
    _day_stubs(monkeypatch, posted=1)
    asked: list[str] = []
    x = _own()[0]
    script = reel_script.parse(course.compose(x, 2), max_scenes=2)

    async def execute(req):
        asked.append(req.action)
        if req.action == "content.draft_course_reel":
            assert req.params == {"beats": 2}
            return ActionResult(True, "drafted", evidence={
                "id": 11, "format": script.format, "caption": script.caption,
                "scenes": course.screen_lines(script)})
        if req.action == "content.render_video":
            return ActionResult(True, "rendered", evidence={"media_id": 21})
        raise AssertionError(f"unexpected action {req.action}")

    posted = _real_post_stub(monkeypatch)
    wrote: list[tuple] = []

    async def execute_sql(sql, *args):
        wrote.append((sql, args))

    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "fetch_one", _async({"status": "rendered"}))
    monkeypatch.setattr(day, "execute_sql", execute_sql)
    lines = await day.course_lines(TUE_NOON)
    assert lines[0].startswith("Course: posted")
    assert asked == ["content.draft_course_reel", "content.render_video"]
    assert not any("clip" in a for a in asked)
    assert posted[0]["seconds"] == course.SECONDS
    assert posted[0]["params"]["caption"] == script.caption
    assert "never the clips accounts" in posted[0]["reason"]
    assert wrote[0][1] == (11, "published")


def test_the_post_path_is_the_stories_accounts_and_never_the_clips_ones():
    from atlas.modules.content import crosspost, postiz
    src = inspect.getsource(crosspost.publish_reel) + inspect.getsource(
        crosspost._instagram) + inspect.getsource(crosspost._tiktok)
    assert 'account="stories"' in src and "clips" not in src
    assert "post_tiktok_clip" not in src
    lane = inspect.getsource(course) + inspect.getsource(day.course_lines) + \
        inspect.getsource(day._post_course)
    assert 'account="clips"' not in lane and "post_tiktok_clip" not in lane
    both = [{"id": "a", "identifier": "tiktok-business", "name": "atlasclips12"},
            {"id": "b", "identifier": "tiktok-business", "name": "atlasstories7"}]
    assert postiz.pick_integration(both, "stories") == ("b", "atlasstories7")


def test_the_course_step_runs_before_football_so_it_can_take_a_post():
    src = inspect.getsource(day.daily)
    assert src.index("course_lines") < src.index("football_lines")


# ------------------------------------------------------------------ metrics

def test_drafts_are_tagged_course_promo_and_the_rotation_never_sees_them():
    x = _own()[0]
    assert reel_script.parse(course.compose(x, 1)).format.startswith("course_promo")
    assert "f.source <> 'course'" in inspect.getsource(playbook.top_formats)
    assert "source = 'course'" in inspect.getsource(playbook.drafted_today)
    assert "'course_promo'" not in inspect.getsource(playbook.top_formats)
    src = inspect.getsource(course.ensure_format)
    assert "reel_formats" in src and course.FORMAT == "course_promo"


def test_the_draft_action_is_tier_auto_with_a_dry_run_and_a_rollback():
    from atlas.modules import content
    actions = {a.name: a for a in content.MODULE.actions()}
    a = actions["content.draft_course_reel"]
    assert a.tier is Tier.AUTO and a.dry_run and a.rollback


async def test_the_beats_argument_is_closed():
    assert not (await course.draft_course_reel(3)).ok
    assert not (await course.draft_course_reel_dry("rm -rf")).ok
    assert (await course.draft_course_reel_dry(1)).ok


async def test_the_next_lesson_rotates_through_the_list(memory_backend):
    memory_backend.responses["SELECT title FROM content_drafts"] = {
        "title": "Course reel: lesson 8.1"}
    ids = [x["id"] for x in course.lessons()]

    def after(i):
        return ids[(ids.index(i) + 1) % len(ids)]

    assert (await course.next_lesson(5))["id"] == after("8.1")
    memory_backend.responses["SELECT title FROM content_drafts"] = {
        "title": f"Course reel: lesson {ids[-1]}"}
    assert (await course.next_lesson(5))["id"] == "0.1"   # and the list wraps
    memory_backend.responses["SELECT title FROM content_drafts"] = {
        "title": "Course reel: lesson ai-for-every-age-adults:1.4"}
    assert (await course.next_lesson(5))["id"] == "ai-for-every-age-60plus:3.1"
    memory_backend.responses["SELECT title FROM content_drafts"] = None
    assert (await course.next_lesson(5))["id"] == "0.1"


# ------------------------------------------------------------------ held reels

def _held():
    x = _own()[1]
    return {"draft_id": 31, "media_id": 41, "body": course.compose(x, 1)}


async def test_the_calendar_tick_posts_a_held_course_reel_and_never_drafts(
        monkeypatch):
    monkeypatch.setattr(course, "ensure_format", _async(5))
    monkeypatch.setattr(course, "waiting", _async(_held()))
    monkeypatch.setattr(day, "fetch_one", _async({"status": "rendered"}))
    wrote: list[tuple] = []

    async def execute_sql(sql, *args):
        wrote.append(args)

    async def never(req):
        raise AssertionError("the retry never drafts or renders")

    monkeypatch.setattr(day, "execute_sql", execute_sql)
    monkeypatch.setattr(day, "execute", never)
    posted = _real_post_stub(monkeypatch)
    lines = await day.course_waiting_lines()
    assert lines[0].startswith("Course: posted") and wrote == [(31, "published")]
    assert posted[0]["params"]["media_id"] == 41
    assert posted[0]["series"] is False


async def test_a_held_reel_that_still_waits_stays_waiting(monkeypatch):
    monkeypatch.setattr(course, "ensure_format", _async(5))
    monkeypatch.setattr(course, "waiting", _async(_held()))
    monkeypatch.setattr(day, "fetch_one", _async({"status": "rendered"}))

    async def execute_sql(sql, *args):
        raise AssertionError("a waiting reel's draft is left as it is")

    monkeypatch.setattr(day, "execute_sql", execute_sql)
    _real_post_stub(monkeypatch, "wait")
    lines = await day.course_waiting_lines()
    assert "waiting for the next eligible day" in lines[0]


async def test_a_reel_already_posted_by_the_other_path_is_not_posted_again(
        monkeypatch):
    monkeypatch.setattr(course, "ensure_format", _async(5))
    monkeypatch.setattr(course, "waiting", _async(_held()))
    monkeypatch.setattr(day, "fetch_one", _async({"status": "published"}))

    async def never(*a, **k):
        raise AssertionError("never posted twice")

    monkeypatch.setattr(day, "post_or_card", never)
    lines = await day.course_waiting_lines()
    assert "already published" in lines[0]


async def test_the_retry_is_idle_when_the_lane_is_off(monkeypatch):
    monkeypatch.setenv("COURSE_REELS_PER_WEEK", "0")
    assert await day.course_waiting_lines() == []


def test_the_calendar_tick_retries_held_course_reels():
    assert "course_waiting_lines" in inspect.getsource(day._calendar_tick)


# ------------------------------------------------------------------ the live price

class _Response:
    def __init__(self, status: int, payload=None, *, text: bool = False):
        self.status_code = status
        self._payload = payload
        self._text = text

    def json(self):
        if self._text:
            raise ValueError("not JSON")
        return self._payload


def _store(monkeypatch, response=None, *, error: Exception | None = None,
           public: str | None = None) -> list[str]:
    """Shopify's product JSON, mocked at httpx, with netguard's DNS stubbed."""
    import httpx

    from atlas.modules import _netguard
    asked: list[str] = []

    class Client:
        def __init__(self, *a, **k):
            assert k.get("follow_redirects") is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            asked.append(url)
            if error is not None:
                raise error
            return response

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(_netguard, "assert_public", _async(public))
    return asked


def _variant(**v):
    return {"product": {"title": "x", "variants": [v]}}


def _catalog(price="20.00", *, live=True, currency="USD", slug="safe-ai-operator"):
    return {"products": [{"slug": slug, "price_usd": price, "currency": currency,
                          "buy_live": live}]}


async def test_the_price_is_read_live_from_the_stores_catalog(monkeypatch):
    asked = _store(monkeypatch, _Response(200, _catalog("20.00")))
    before = datetime.now(UTC)
    price = await course.read_price()
    assert asked == [PRICE_JSON]
    assert price.read and price.amount == Decimal("20.00") and price.compare_at is None
    assert price.read_at >= before
    assert course.price_words(price) == "$20" and course.price_said(price) == "$20"


@pytest.mark.parametrize("payload,why", [
    (_catalog(live=False), "does not sell it yet"),
    (_catalog(slug="other"), "has no 'safe-ai-operator'"),
    (_catalog(currency="EUR"), "not USD"),
    (_catalog(price=None), "not a plain positive amount"),
    ({"products": "x"}, "no products"),
])
async def test_an_unsellable_or_off_shape_catalog_is_not_read(monkeypatch, payload, why):
    _store(monkeypatch, _Response(200, payload))
    price = await course.read_price()
    assert not price.read and why in price.why


async def test_shopify_product_json_is_still_understood_with_its_compare_at(monkeypatch):
    asked = _store(monkeypatch, _Response(200, _variant(
        price="20.00", compare_at_price="49.00", price_currency="USD",
        compare_at_price_currency="USD")))
    before = datetime.now(UTC)
    price = await course.read_price()
    assert asked == [PRICE_JSON]
    assert price.read and price.amount == Decimal("20.00")
    assert price.compare_at == Decimal("49.00") and price.read_at >= before
    assert course.price_words(price) == "$20 (was $49)"
    assert course.price_said(price) == "$20, down from $49"
    x = _own()[0]
    script = reel_script.parse(course.compose(x, 2, price), max_scenes=2)
    assert "$20 (was $49), link in bio" in script.caption
    assert course.narration(script).endswith("$20, down from $49, link in bio.")
    assert course.end_lines(course.price_words(price))[2] == "$20 (was $49)"
    assert "$49," not in script.caption.replace("(was $49)", "")
    assert course.parse_price_line(script.price) == course.Price(
        price.amount, price.compare_at, price.read_at.replace(microsecond=0))
    assert "read from the store at" in price.note()
    assert course.honesty_refusal(x, 2, price) is None


@pytest.mark.parametrize("was", [None, "", "20.00", "15.00"])
async def test_no_compare_at_or_one_not_above_the_price_reads_plain(monkeypatch, was):
    _store(monkeypatch, _Response(200, _variant(price="20.00", compare_at_price=was)))
    price = await course.read_price()
    assert price.read and course.price_words(price) == "$20"
    x = _own()[3]
    body = course.compose(x, 1, price)
    script = reel_script.parse(body, max_scenes=2)
    assert "Operator\": $20, link in bio or https" in script.caption
    assert "was" not in script.price and "(was" not in script.caption
    assert course.narration(script).endswith("$20, link in bio.")


@pytest.mark.parametrize("response,error", [
    (_Response(503, None), None),
    (_Response(301, None), None),
    (_Response(200, None, text=True), None),
    (_Response(200, {"product": {"variants": []}}), None),
    (_Response(200, _variant(price="twenty")), None),
    (_Response(200, _variant(price="0.00")), None),
    (_Response(200, _variant(price="20.00", price_currency="EUR")), None),
    (_Response(200, _variant(price="20.00", compare_at_price="lots")), None),
    (None, OSError("connection refused")),
])
async def test_an_unreadable_store_means_no_price_anywhere(monkeypatch, response,
                                                           error):
    _store(monkeypatch, response, error=error)
    price = await course.read_price()
    assert not price.read and course.price_words(price) is None
    assert price.note().startswith("price not read")
    for x in course.lessons():
        script = reel_script.parse(course.compose(x, 2, price), max_scenes=2)
        words = " ".join([script.caption, course.narration(script),
                          *course.end_lines(course.price_words(price))])
        assert "$" not in words, x["id"]
        assert "link in bio" in script.caption and "LINK IN BIO" in words
        assert script.price.startswith("not read, tried ")
        assert course.honesty_refusal(x, 2, price) is None


async def test_a_non_public_store_address_is_not_read(monkeypatch):
    asked = _store(monkeypatch, _Response(200, _variant(price="20.00")),
                   public="resolves to a loopback address")
    price = await course.read_price()
    assert not price.read and asked == []


@pytest.mark.parametrize("url", [
    "http://academy-store.myshopify.example/products/x.json",
    "https://evil.example.com/products/x.json",
    "ftp://academy-store.myshopify.example/x.json"])
async def test_only_the_stores_own_https_url_is_read(monkeypatch, url):
    import httpx

    class Never:
        def __init__(self, *a, **k):
            raise AssertionError("no request is ever sent")

    monkeypatch.setattr(httpx, "AsyncClient", Never)
    price = await course.read_price(url)
    assert not price.read and price.why


async def test_drafting_reads_the_price_and_stores_it_with_the_draft(monkeypatch):
    _store(monkeypatch, _Response(200, _variant(price="20.00",
                                                compare_at_price="49.00")))
    x = _own()[4]
    monkeypatch.setattr(course, "ensure_format", _async(5))
    monkeypatch.setattr(course, "next_lesson", _async(x))
    bodies: list[str] = []

    async def fetch_one(sql, *args):
        bodies.append(args[2])
        return {"id": 77}

    monkeypatch.setattr(course, "fetch_one", fetch_one)
    result = await course.draft_course_reel(2)
    assert result.ok and "read from the store at" in result.detail
    assert result.evidence["price"]["price"] == "20.00"
    assert result.evidence["price"]["compare_at"] == "49.00"
    assert result.evidence["price"]["read_at"]
    script = reel_script.parse(bodies[0], max_scenes=2)
    assert script.price.startswith("20.00 was 49.00, read 2")
    assert "$20 (was $49)" in script.caption
    assert await course.verify(77, script) is None


async def test_drafting_with_the_store_down_says_price_not_read(monkeypatch):
    _store(monkeypatch, error=OSError("down"))
    monkeypatch.setattr(course, "ensure_format", _async(5))
    monkeypatch.setattr(course, "next_lesson", _async(_own()[0]))
    bodies: list[str] = []

    async def fetch_one(sql, *args):
        bodies.append(args[2])
        return {"id": 78}

    monkeypatch.setattr(course, "fetch_one", fetch_one)
    result = await course.draft_course_reel(1)
    assert result.ok and "price not read" in result.detail
    assert result.evidence["price"]["read"] is False
    assert "price not read" in result.evidence["notes"][0]
    script = reel_script.parse(bodies[0], max_scenes=2)
    assert "$" not in script.caption and "$" not in course.narration(script)
    assert await course.verify(78, script) is None


@pytest.mark.parametrize("old,new", [
    ("20.00 was 49.00", "10.00 was 49.00"),
    ("20.00 was 49.00", "20.00 was 99.00"),
    ("20.00 was 49.00, read", "20.00, read"),
])
async def test_the_render_refuses_a_draft_whose_stored_price_was_tampered(old, new):
    x = _own()[2]
    body = course.compose(x, 2, SALE)
    assert old in body
    script = reel_script.parse(body.replace(old, new), max_scenes=2)
    why = await course.verify(5, script)
    assert why is not None and "no longer matches" in why


async def test_the_render_refuses_a_price_changed_in_the_words_only():
    x = _own()[2]
    body = course.compose(x, 2, SALE)
    script = reel_script.parse(body.replace("$20 (was $49)", "$10 (was $49)"),
                               max_scenes=2)
    assert "caption was changed" in await course.verify(5, script)
    script = reel_script.parse(body.replace("$20, down", "$10, down"), max_scenes=2)
    assert "no longer matches" in await course.verify(5, script)


async def test_the_render_refuses_a_draft_with_no_price_read():
    x = _own()[2]
    body = "\n".join(ln for ln in course.compose(x, 2, SALE).splitlines()
                     if not ln.startswith("PRICE:"))
    script = reel_script.parse(body, max_scenes=2)
    assert "no store price read" in await course.verify(5, script)
    garbled = course.compose(x, 2, SALE).replace("20.00 was", "about 20 was")
    assert "no store price read" in await course.verify(
        5, reel_script.parse(garbled, max_scenes=2))


def test_the_price_line_round_trips():
    for price in (SALE, PLAIN, UNREAD):
        back = course.parse_price_line(price.line())
        assert back is not None and back.line() == price.line()
    assert course.parse_price_line("") is None
    assert course.parse_price_line("20.00, read yesterday") is None
    assert course.parse_price_line("20.00, read 2026-09-29T16:05:00") is None


async def test_another_courses_price_is_read_by_its_own_slug(monkeypatch):
    """the owner, 2026-10-05: every course advertised. Each reel's price is its
    own course's catalog entry, read live -- never the first course's."""
    asked = _store(monkeypatch, _Response(200, {"products": [
        {"slug": "safe-ai-operator", "price_usd": 20, "currency": "USD", "buy_live": True},
        {"slug": "ai-for-every-age-adults", "price_usd": 29, "currency": "USD",
         "buy_live": True}]}))
    item = course.lesson("ai-for-every-age-adults:1.4")
    price = await course.read_price(item=item)
    assert asked == [PRICE_JSON]
    assert price.read and price.amount == Decimal(29)
    assert "$29, link in bio" in course.caption(item, price)
    assert course.end_lines("$29", item)[1] == "Everyday AI for Adults"
    seniors = course.lesson("ai-for-every-age-60plus:3.1")
    assert not (await course.read_price(item=seniors)).read, "its slug is not in this catalog"


async def test_the_catalogs_numeric_price_is_read(monkeypatch):
    """Seen live 2026-10-05: catalog.json says "price_usd": 20.0, a JSON
    number, and the reader took only strings -- so no course reel ever
    carried its price. A number is read; a boolean or a nonsense one is not."""
    _store(monkeypatch, _Response(200, _catalog(20.0)))
    price = await course.read_price()
    assert price.read and price.amount == Decimal("20.00")
    assert course.price_words(price) == "$20"
    for bad in (True, -5, 0, float("inf"), 19.999):
        _store(monkeypatch, _Response(200, _catalog(bad)))
        assert not (await course.read_price()).read, bad


async def test_a_course_with_several_offers_shows_the_one_it_names(monkeypatch):
    """Sell With AI is sold as an ebook, a video course and both; until the
    videos are on sale its reels carry the ebook's price, by `price_slug`."""
    _store(monkeypatch, _Response(200, {"products": [
        {"slug": "sell-with-ai-ebook", "price_usd": 19.0, "currency": "USD", "buy_live": True},
        {"slug": "sell-with-ai-video", "price_usd": 39.0, "currency": "USD", "buy_live": False}]}))
    item = course.lesson("sell-with-ai:0.2")
    price = await course.read_price(item=item)
    assert price.read and price.amount == Decimal(19)
    cap = course.caption(item, price)
    assert "https://agency.example.com/academy/sell-with-ai/" in cap and "$19, link in bio" in cap
    assert course.honesty_refusal(item, 2, price) is None
