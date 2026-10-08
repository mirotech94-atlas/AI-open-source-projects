"""Reach without paid promotion (Miro, 2026-09-29).

* Promote candidates: a stories reel that clearly outperforms raises ONE
  information-only card; never a clip, never a football or course reel;
  unreadable or stale numbers -> no card and UNKNOWN. No promote or ads
  action or endpoint exists anywhere.
* Niche hashtags per lane, from closed lists, inside every caption guard.
* Best posting hours: views at ~24 h by New York hour; not enough data ->
  UNKNOWN and nothing is held; a hold never passes the day.
"""
from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.kernel.contracts import ActionResult, Health
from atlas.modules.clips import campaigns as clip_campaigns
from atlas.modules.clips import captions as clip_captions
from atlas.modules.content import course, crosspost, guard, hashtags, reach
from atlas.modules.content import football as lane_football
from atlas.modules.content.drafts import AI_LABEL

NOW = datetime(2026, 9, 29, 16, 0, tzinfo=UTC)          # 12:00 New York
ROOT = Path(__file__).resolve().parent.parent / "atlas"


# ============================================================ promote

def _post(ref, *, platform="tiktok", hours_ago=30.0, views=None, eng=None,
          read_after=24.0, source="reel_format:3", format_source="perplexity",
          likes=None, comments=None, shares=None, read=True):
    posted = NOW - timedelta(hours=hours_ago)
    return {"platform": platform, "post_ref": ref, "posted_at": posted,
            "source": source, "format_source": format_source,
            "views": views, "likes": likes, "comments": comments,
            "shares": shares, "engagement": eng,
            "read_at": (posted + timedelta(hours=read_after)) if read else None}


def _baseline(platform="tiktok", views=(900.0, 1000.0, 1100.0)):
    return [_post(f"b{i}", platform=platform, hours_ago=24 * (5 + i), views=v,
                  eng=0.02, read_after=24 * (4 + i) - 2)
            for i, v in enumerate(views)]


TH = reach.Thresholds()


def test_the_default_thresholds_are_mirós():
    assert (TH.min_views, TH.min_engagement, TH.multiple, TH.baseline_min) == (
        1000.0, 0.05, 3.0, 3)


def test_thresholds_are_env_tunable(monkeypatch):
    monkeypatch.setenv("REELS_PROMOTE_MIN_VIEWS", "5000")
    monkeypatch.setenv("REELS_PROMOTE_MULTIPLE", "4")
    th = reach.Thresholds.from_env()
    assert th.min_views == 5000 and th.multiple == 4


def test_a_stories_reel_that_clearly_outperforms_is_a_candidate():
    rows = _baseline() + [_post("hit", views=5200.0, eng=0.081, likes=380.0,
                                comments=21.0, shares=20.0)]
    found, unknown = reach.candidates(rows, NOW, TH)
    assert [c.post_ref for c in found] == ["hit"] and not unknown
    c = found[0]
    assert c.baseline == 1000.0 and c.baseline_posts == 3
    assert c.multiple == pytest.approx(5.2)


@pytest.mark.parametrize("views, eng, why", [
    (2500.0, 0.08, "under 3x the median"),
    (5200.0, 0.04, "engagement under 5%"),
    (5200.0, None, "engagement not read"),
    (900.0, 0.2, "under 1,000 views"),
])
def test_short_of_any_bar_is_not_a_candidate(views, eng, why):
    rows = _baseline() + [_post("x", views=views, eng=eng)]
    found, _ = reach.candidates(rows, NOW, TH)
    assert found == [], why


def test_only_a_reading_within_48_hours_of_posting_counts():
    rows = _baseline() + [_post("late", hours_ago=70, views=9000.0, eng=0.1,
                                read_after=60)]
    found, _ = reach.candidates(rows, NOW, TH)
    assert found == []


@pytest.mark.parametrize("source, format_source", [
    (None, None),                                   # a clip: never in reel_posts
    ("clip:cod-mw4-warzone-operator:12", None),
    ("reel_format:9", "football"),
    ("reel_format:4", "course"),
    ("calendar:business-reels-v1-d01", None),
])
def test_clips_football_course_and_business_posts_are_never_candidates(
        source, format_source):
    """Clipping Culture permanently bans paid boosting of campaign clips: a
    clip-shaped row with huge numbers is never a candidate, nor is any lane
    but the AI stories."""
    rows = _baseline() + [_post("big", views=900_000.0, eng=0.3, source=source,
                                format_source=format_source)]
    found, _ = reach.candidates(rows, NOW, TH)
    assert found == []


def test_no_clip_ever_writes_reel_posts():
    for path in (ROOT / "modules" / "clips").rglob("*.py"):
        assert "reel_posts" not in path.read_text(), path


def test_too_few_posts_to_compare_is_unknown_and_no_candidate():
    rows = _baseline(views=(900.0,)) + [_post("hit", views=5200.0, eng=0.08)]
    found, unknown = reach.candidates(rows, NOW, TH)
    assert found == [] and unknown and "to compare with" in unknown[0]


def test_stale_readings_are_unknown_and_no_candidate():
    stale = NOW + timedelta(hours=40)
    found, unknown = reach.candidates(_baseline() + [
        _post("hit", views=5200.0, eng=0.08)], stale, TH)
    assert found == [] and unknown and "no fresh reading" in unknown[0]


def test_a_reel_not_read_yet_is_normal_but_one_never_read_is_unknown():
    fresh = _baseline() + [_post("hit", views=5200.0, eng=0.08),
                           _post("new", hours_ago=5, read=False)]
    found, unknown = reach.candidates(fresh, NOW, TH)
    assert [c.post_ref for c in found] == ["hit"] and unknown == []
    old = _baseline() + [_post("hit", views=5200.0, eng=0.08),
                         _post("lost", hours_ago=40, read=False)]
    found, unknown = reach.candidates(old, NOW, TH)
    assert [c.post_ref for c in found] == ["hit"]
    assert any("not read" in u for u in unknown)


async def test_the_check_is_unknown_when_the_numbers_cannot_be_read(monkeypatch):
    async def down(*_a, **_k):
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(reach, "fetch_all", down)
    obs = await reach.check_promote_candidates()
    assert obs.health == Health.UNKNOWN and "unreadable" in obs.detail


async def test_the_check_reports_candidates_with_the_read_time(monkeypatch):
    rows = _baseline() + [_post("hit", views=5200.0, eng=0.08)]
    for r in rows:      # relative to the real clock: the check reads "now"
        shift = datetime.now(UTC) - NOW
        r["posted_at"] += shift
        r["read_at"] = r["read_at"] + shift if r["read_at"] else None

    async def fetch(_sql, *_a):
        return rows

    monkeypatch.setattr(reach, "fetch_all", fetch)
    obs = await reach.check_promote_candidates()
    assert obs.health == Health.OK and obs.value == 1.0
    assert "TikTok hit" in obs.detail and "UTC" in obs.detail


def test_the_card_carries_link_numbers_read_time_why_and_the_budget_line():
    rows = _baseline() + [_post("hit", views=5200.0, eng=0.081, likes=380.0,
                                comments=21.0, shares=20.0)]
    (c,), _ = reach.candidates(rows, NOW, TH)
    title, body = reach.card_text(
        c, "https://www.tiktok.com/@atlas.stories/video/1", TH)
    assert "TikTok" in title
    assert "https://www.tiktok.com/@atlas.stories/video/1" in body
    assert "5,200 views" in body and "8.1%" in body and "UTC" in body
    assert "5.2x" in body and "median" in body
    assert "Suggested budget $10–20 — promote it yourself in the TikTok app" in body
    assert "Information only" in body
    _, no_link = reach.card_text(c, None, TH)
    assert "link not readable" in no_link          # never built from an id


async def test_one_card_per_reel_information_only(monkeypatch):
    from atlas.kernel import reels as day
    rows = _baseline() + [_post("hit", views=5200.0, eng=0.08)]
    found, _ = reach.candidates(rows, NOW, TH)
    claimed: set[str] = set()
    sent: list = []

    async def read(now=None):
        return found, [], NOW

    async def fetch_one(sql, kind):
        return {"n": 1 if kind in claimed else 0}

    async def execute_sql(sql, kind, *rest):
        assert "INSERT INTO reports" in sql
        claimed.add(kind)

    async def notify(msg):
        sent.append(msg)
        return ["slack"]

    async def link(platform, ref):
        return None

    async def never(*_a, **_k):
        raise AssertionError("a promote card never runs or proposes anything")

    monkeypatch.setattr(reach, "read_candidates", read)
    monkeypatch.setattr(reach, "reel_link", link)
    monkeypatch.setattr(day, "fetch_one", fetch_one)
    monkeypatch.setattr(day, "execute_sql", execute_sql)
    monkeypatch.setattr(day, "notify", notify)
    monkeypatch.setattr(day, "execute", never)
    monkeypatch.setattr(day, "propose", never)
    first = await day.promote_lines()
    second = await day.promote_lines()
    assert len(sent) == 1 and tuple(sent[0].buttons) == ()
    assert claimed == {"reel_promote:tiktok:hit"}
    assert "1 new card" in first[0] and "0 new card" in second[0]


async def test_no_card_when_the_numbers_cannot_be_read(monkeypatch):
    from atlas.kernel import reels as day

    async def down(now=None):
        raise ConnectionError("postgres is down")

    async def notify(_msg):
        raise AssertionError("no card on unreadable numbers")

    monkeypatch.setattr(reach, "read_candidates", down)
    monkeypatch.setattr(day, "notify", notify)
    (line,) = await day.promote_lines()
    assert "not judged" in line


# ----------------------------------------------- no promote / ads anywhere

_ADS = re.compile(r"boost|promot|\bads?\b|advert|spark|ad_?spend|campaign_budget",
                  re.IGNORECASE)


def test_no_promote_or_ads_action_exists(monkeypatch):
    monkeypatch.setenv("GUARDIAN_WRITE_TOKEN", "test-write-token-not-real")
    import atlas.modules as modules_pkg
    names = []
    for info in pkgutil.iter_modules(modules_pkg.__path__):
        if info.name.startswith("_"):
            continue
        module = getattr(importlib.import_module(f"atlas.modules.{info.name}"),
                         "MODULE", None)
        if module is not None:
            names += [a.name for a in module.actions()]
    assert names, "no action was declared at all"
    assert [n for n in names if _ADS.search(n)] == []


def test_no_ads_or_promote_endpoint_is_called_anywhere():
    endpoints = re.compile(
        r"business-api\.tiktok\.com|ads\.tiktok\.com|/adsets|/adcreatives|"
        r"/ads\b|act_\{|promote_post|/boost|adsmanager|/promotions?\b",
        re.IGNORECASE)
    hits = [str(p) for p in ROOT.rglob("*.py") if endpoints.search(p.read_text())]
    assert hits == []


def test_the_reach_module_acts_on_nothing():
    src = inspect.getsource(reach)
    for word in ("execute(", "propose(", "ExecutionRequest", ".post(", "follow(",
                 "like(", "send_dm"):
        assert word not in src, word


# ============================================================ hashtags

LANES = ("stories", "football", "course")


def test_every_lane_tag_is_lower_case_and_no_disclosure():
    every = (*hashtags.STORIES, hashtags.SERIES_TAG,
             *(t for _, t in hashtags.GENRES), *hashtags.FOOTBALL,
             *(t for _, t in hashtags.LEAGUES), *hashtags.COURSE)
    for tag in every:
        assert re.fullmatch(r"#[a-z0-9]+", tag), tag
        assert tag not in ("#ad", "#sponsored", "#advertisement")


def test_every_lane_tag_passes_its_lanes_caption_guard():
    stories = " ".join((*hashtags.STORIES, hashtags.SERIES_TAG,
                        *(t for _, t in hashtags.GENRES)))
    assert guard.refusal(stories) is None and guard.ip_refusal(stories) is None
    football = " ".join((*hashtags.FOOTBALL, *(t for _, t in hashtags.LEAGUES)))
    assert guard.football_caption_refusal(football) is None
    assert guard.income_refusal(" ".join(hashtags.COURSE)) is None
    assert guard.income_refusal(course.HASHTAGS) is None
    assert course.HASHTAGS == hashtags.line(hashtags.COURSE)
    assert lane_football.HASHTAGS == hashtags.line(hashtags.FOOTBALL)


def test_stories_genre_tags_come_from_the_format_name():
    assert hashtags.for_lane("stories", "murder-mystery story") == [
        "#aistory", "#animation", "#storytime", "#mystery"]
    assert "#lovestory" in hashtags.for_lane("stories", "love story")
    assert "#funnyanimals" in hashtags.for_lane("stories", "dogs in human scenarios")
    assert "#series" in hashtags.for_lane("stories", "", series=True)
    assert len(hashtags.genre_tags("murder mystery love drama revenge")) == 2
    assert hashtags.for_lane("business", "anything") == []
    assert hashtags.for_lane("other", "love story") == []


def test_football_tags_follow_the_leagues_the_facts_name():
    from atlas.modules.football import facts as fx
    fact = fx.Fact("f1", "#1 in Premier League: goals", "12",
                   "2026-09-29T10:00:00Z", "players/topscorers")
    two = fx.Fact("f2", "#1 in La Liga: goals", "9", "2026-09-29T10:00:00Z", "x")
    got = hashtags.football_tags(fx.FactSet("t", "Golden boot", (fact, two)))
    assert got == "#football #soccer #footballstats #premierleague #laliga"
    plain = fx.FactSet("t", "Ronaldo vs Messi", (
        fx.Fact("c", "career goals", "900", "2026-09-29T10:00:00Z", "x"),))
    assert hashtags.football_tags(plain) == lane_football.HASHTAGS
    many = "premier league la liga serie a bundesliga ligue 1 champions league"
    tags = hashtags.football_tags(fx.FactSet("t", many, ()))
    assert len(tags.split()) == hashtags.DEFAULT_MAX


def test_tags_go_before_the_label_once_and_within_the_ceiling():
    cap = f"The sea was gone. #storytime\n\n{AI_LABEL}"
    out = hashtags.apply(cap, ["#aistory", "#storytime", "#animation"])
    assert out.endswith(AI_LABEL) and out.count("#storytime") == 1
    assert "#aistory #animation" in out
    crowded = "a #one #two #three #four #five\n\n" + AI_LABEL
    assert hashtags.apply(crowded, ["#aistory"]) == crowded       # never over 5
    assert "#one" in hashtags.apply(crowded, ["#x"])              # never removes
    assert hashtags.count(hashtags.apply("x", list(hashtags.COURSE) * 3)) <= 5


def test_a_tagged_caption_with_its_cta_still_passes_every_publish_guard():
    cap = f"A door opened.\n\nFollow for part 2\n\n{AI_LABEL}"
    tagged = hashtags.apply(cap, hashtags.for_lane("stories", "mystery hook"))
    assert crosspost.vet_publish_reel({"caption": tagged}) is None
    assert guard.refusal(tagged) is None
    tiktok = crosspost.platform_caption(tagged, "tiktok")
    assert tiktok.endswith(AI_LABEL) and hashtags.count(tiktok) <= 5


def test_tiktok_adds_stories_tags_only_to_a_stories_reel():
    cap = f"Haaland: 12 goals #football #soccer\n\n{AI_LABEL}"
    out = crosspost.platform_caption(cap, "tiktok", "football")
    assert "#aistory" not in out and "#fyp" in out and out.endswith(AI_LABEL)
    assert "#aistory" in crosspost.platform_caption(cap, "tiktok")


@pytest.mark.parametrize("source, fsource, lane, series", [
    ("reel_format:3", "perplexity", "stories", False),
    ("reel_format:3", "miro", "stories", False),
    ("reel_format:9", "football", "football", False),
    ("reel_format:4", "course", "course", False),
    ("series:2:3", None, "stories", True),
    ("import:abc", None, "stories", False),
    ("calendar:business-reels-v1-d01", None, "business", False),
    ("calendar:reels-20260928-v2-d01", None, "stories", True),
    ("weekly", None, "other", False),
    ("", None, "other", False),
])
def test_the_lane_is_decided_in_code(source, fsource, lane, series):
    assert hashtags.lane_of(source, fsource) == (lane, series)


# ---------------------------------------------------------------- clips

def test_clip_niche_tags_only_from_the_campaigns_pool_and_within_its_limit():
    cod = dataclasses.replace(clip_campaigns.COD, rules_text="reviewed")
    tags = list(cod.caption_rules["default_hashtags"])
    extra = clip_captions.niche_tags(cod, "official", tags)
    assert extra == ["#CODWarzone"]
    caption = clip_captions.build_caption(
        cod, clip_captions.sample(cod, "official"), "instagram", tags + extra)
    assert caption.split("\n")[2] == "#Ad"                # the disclosure stays first
    assert caption.split("\n")[3] == "#Warzone #CallofDuty #CODWarzone"


def test_atlus_gets_a_game_tag_only_when_the_caption_names_that_game():
    atlus = clip_campaigns.ATLUS
    base = list(atlus.caption_rules["default_hashtags"])
    assert clip_captions.niche_tags(atlus, "general", base) == []
    assert clip_captions.niche_tags(atlus, "persona3", base) == ["#Persona3Reload"]


def test_a_full_or_poolless_campaign_gets_no_niche_tag():
    ruby = clip_campaigns.IRUBYANA
    full = list(ruby.caption_rules["default_hashtags"])
    # Irubyana's rules require four tags, so her campaign raises the carry.
    assert len(full) == clip_captions.max_hashtags(ruby) == 4
    assert clip_captions.niche_tags(ruby, "general", full) == []
    assert clip_captions.niche_tags(clip_campaigns.WARHAMMER, "x", []) == []


# ============================================================ best hours

def _at(hour_ny: int, day: int) -> datetime:
    """A post at `hour_ny` New York (EDT, UTC-4) on September `day`."""
    return datetime(2026, 9, day, 4, 0, tzinfo=UTC) + timedelta(hours=hour_ny)


def _hour_rows(hour: int, views: list[float], platform="tiktok"):
    rows = []
    for i, v in enumerate(views):
        posted = _at(hour, 1 + i)
        rows.append({"platform": platform, "post_ref": f"{platform}-{hour}-{i}",
                     "posted_at": posted, "views": v,
                     "read_at": posted + timedelta(hours=24)})
        # a farther reading of the same post is not the ~24 h one
        rows.append({"platform": platform, "post_ref": f"{platform}-{hour}-{i}",
                     "posted_at": posted, "views": v * 10,
                     "read_at": posted + timedelta(hours=35)})
    return rows


def test_views_at_24_hours_are_bucketed_by_new_york_hour():
    rows = _hour_rows(14, [4000, 5000, 6000]) + _hour_rows(18, [20000, 21000, 19000])
    table = reach.best_hours(rows, 3)["tiktok"]
    assert table.buckets == {14: (3, 5000.0), 18: (3, 20000.0)}
    assert table.usable and table.trusted == {14: 5000.0, 18: 20000.0}


def test_not_enough_posts_per_hour_holds_nothing():
    rows = _hour_rows(14, [4000, 5000]) + _hour_rows(18, [20000, 21000])
    tables = reach.best_hours(rows, 3)
    assert not tables["tiktok"].usable
    assert reach.hold_until(tables, _at(13, 29)) == (None, "not enough data")


def _tables():
    return reach.best_hours(
        _hour_rows(14, [4000, 5000, 6000]) + _hour_rows(18, [20000, 21000, 19000]),
        3)


def test_a_ready_reel_waits_for_the_best_hour_within_six_hours():
    hour, why = reach.hold_until(_tables(), _at(13, 29))
    assert hour == 18 and reach.BEST_HOUR_HELD in why


def test_no_hold_at_the_best_hour_past_it_or_beyond_six_hours():
    assert reach.hold_until(_tables(), _at(18, 29))[0] is None
    assert reach.hold_until(_tables(), _at(19, 29))[0] is None
    assert reach.hold_until(_tables(), _at(10, 29))[0] is None    # 18:00 is 8 h off


def test_the_window_is_fixed_from_when_the_reel_was_first_ready():
    """Ready at 07:00, still waiting at 13:00: the six hours are spent, so
    a better 18:00 does not hold it again (no rolling re-hold)."""
    assert reach.hold_until(_tables(), _at(13, 29), ready=_at(7, 29))[0] is None
    assert reach.hold_until(_tables(), _at(12, 29), ready=_at(10, 29))[0] is None
    assert reach.hold_until(_tables(), _at(13, 29), ready=_at(12, 29))[0] == 18


async def test_the_first_offer_anchors_the_hold(monkeypatch):
    async def tables():
        return _tables(), NOW

    monkeypatch.setattr(reach, "read_tables", tables)
    monkeypatch.setattr(reach, "_READY", {})
    assert await reach.best_hour_hold(7, _at(13, 29)) is not None
    assert await reach.best_hour_hold(7, _at(17, 29)) is not None
    assert await reach.best_hour_hold(7, _at(19, 29)) is None     # 6 h spent
    assert await reach.best_hour_hold(7, _at(13, 30)) is not None  # a new day


def test_a_hold_never_passes_the_day():
    late = reach.best_hours(
        _hour_rows(20, [4000, 5000, 6000]) + _hour_rows(23, [20000, 21000, 19000]), 3)
    assert reach.hold_until(late, _at(20, 29))[0] is None       # 23:00 > LAST_HOUR


def test_instagram_decides_only_when_tiktok_has_no_trusted_table():
    ig = reach.best_hours(_hour_rows(14, [1, 2, 3], "instagram")
                          + _hour_rows(16, [50, 60, 70], "instagram"), 3)
    assert reach.hold_until(ig, _at(13, 29))[0] == 16


async def test_the_best_hours_check_is_unknown_without_enough_data(monkeypatch):
    async def fetch(_sql, *_a):
        return _hour_rows(14, [4000])

    monkeypatch.setattr(reach, "fetch_all", fetch)
    obs = await reach.check_best_hours()
    assert obs.health == Health.UNKNOWN
    assert "not enough data" in obs.detail and "UTC" in obs.detail
    assert "14h 4,000 (1?)" in obs.detail


async def test_the_best_hours_check_shows_the_table_when_it_can(monkeypatch):
    async def fetch(_sql, *_a):
        return _hour_rows(14, [4000, 5000, 6000]) + _hour_rows(18, [20000] * 3)

    monkeypatch.setattr(reach, "fetch_all", fetch)
    obs = await reach.check_best_hours()
    assert obs.health == Health.OK and "TikTok 18:00" in obs.detail
    assert "read" in obs.detail and "UTC" in obs.detail


async def test_the_best_hours_check_is_unknown_when_unreadable(monkeypatch):
    async def down(*_a):
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(reach, "fetch_all", down)
    assert (await reach.check_best_hours()).health == Health.UNKNOWN


async def test_an_unreadable_table_never_holds(monkeypatch):
    async def down(*_a):
        raise ConnectionError("postgres is down")

    monkeypatch.setattr(reach, "fetch_all", down)
    assert await reach.best_hour_hold() is None
    monkeypatch.setenv("REELS_BEST_HOURS", "off")
    assert await reach.best_hour_hold() is None


async def _post_or_card(monkeypatch, *, best_hour: bool, hold: str | None):
    from atlas.kernel import reels as day
    from atlas.modules.content import quality
    monkeypatch.delenv("ATLAS_DURABLE_CORE", raising=False)
    monkeypatch.delenv("ATLAS_DURABLE_FLOWS", raising=False)
    monkeypatch.setenv("REELS_AUTOPUBLISH", "on")
    ran: list = []

    async def execute(req):
        ran.append(req.params["caption"])
        return ActionResult(True, "posted on 3 platforms")

    async def passes(*_a, **_kw):
        return None

    async def same(_media_id, caption):
        return caption

    async def notify(_msg):
        return ["slack"]

    async def held(media_id=None, now=None):
        return hold

    async def lane(_media_id):
        return "stories", False, "murder-mystery story"

    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "notify", notify)
    monkeypatch.setattr(quality, "check_media", passes)
    monkeypatch.setattr("atlas.modules.shop.caption_with_cta", same)
    monkeypatch.setattr(reach, "best_hour_hold", held)
    monkeypatch.setattr(hashtags, "media_lane", lane)
    got = await day.post_or_card({"media_id": 7, "caption": f"x.\n\n{AI_LABEL}"},
                                 "a reel", dedup="d1", media_id=7,
                                 rollback_plan="delete it", series=False,
                                 best_hour=best_hour)
    return got, ran


async def test_post_or_card_waits_for_the_best_hour_only_when_asked(monkeypatch):
    why = f"{reach.BEST_HOUR_HELD} (18:00 New York)"
    got, ran = await _post_or_card(monkeypatch, best_hour=True, hold=why)
    assert got == ("wait", None, why) and ran == []
    got, ran = await _post_or_card(monkeypatch, best_hour=False, hold=why)
    assert got[0] == "posted" and len(ran) == 1


async def test_post_or_card_posts_with_the_lanes_tags(monkeypatch):
    got, ran = await _post_or_card(monkeypatch, best_hour=True, hold=None)
    assert got[0] == "posted"
    (caption,) = ran
    assert "#aistory #animation #storytime #mystery" in caption
    assert caption.endswith(AI_LABEL)


def test_only_the_retrying_callers_ask_for_the_best_hour():
    """A caller that runs once a day (football, course, the format reels)
    would turn a hold into tomorrow; only the half-hourly tick's calendar
    and series ask for it."""
    from atlas.kernel import reels as day
    src = inspect.getsource(day)
    assert src.count("best_hour=True") == 2
    assert "best_hour=True" in inspect.getsource(day._calendar_tick)
    assert "best_hour=True" in inspect.getsource(day.series_step)
