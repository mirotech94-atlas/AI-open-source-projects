"""Money per reel: the Analyst's table and the Improver's mix.

Never a guessed number: an input that can't be read makes its reel type
UNKNOWN and unranked, never zero. The Improver moves at most 20% a week,
never below one reel a week, keeps the total, changes nothing on thin data,
logs every decision with its numbers, and decides once a week. The calendar
reads the mix, and keeps today's two stories to one lesson without one.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from atlas.kernel import reel_money as rm
from atlas.modules import posthog, shop
from atlas.modules.content import calendar as cal

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
UNTIL = NOW - timedelta(hours=1)
SINCE = UNTIL - timedelta(days=7)


def table(**rpm: float | None) -> rm.Table:
    """A table whose types are ranked at the given $ per 1,000 views (None:
    UNKNOWN); every other type is unranked."""
    rows = {t: rm.TypeRow(t, why="no product") for t in shop.REEL_TYPES}
    for t, value in rpm.items():
        row = rows[t]
        if value is None:
            row.state, row.why = "unknown", "Stripe could not be read"
        else:
            row.state, row.views = "ranked", 1000.0
            row.revenue_cents, row.sales = round(value * 100), 1
    return rm.Table(since=SINCE, until=UNTIL, rows=rows)


MIX = {"business": 10.0, "stories": 14.0, "tech": 1.0, "travel": 10.0}


# ------------------------------------------------------------ the Improver

def test_the_best_earner_gains_at_most_20_percent_and_the_total_holds():
    changed, reason, after = rm.decide(MIX, table(travel=5.0, business=1.0))
    assert changed and "travel" in reason
    assert after["travel"] == 12.0 and after["business"] == 8.0
    for t, was in MIX.items():
        assert was * 0.8 - 1e-9 <= after[t] <= was * 1.2 + 1e-9
    assert sum(after.values()) == pytest.approx(sum(MIX.values()))
    # Not ranked, so not touched: an unknown is not a zero.
    assert after["stories"] == 14.0 and after["tech"] == 1.0


def test_the_clamp_is_the_smaller_side_s_20_percent():
    before = {**MIX, "travel": 2.0, "business": 20.0}
    _, _, after = rm.decide(before, table(travel=9.0, business=1.0))
    assert after["travel"] == pytest.approx(2.4)          # +20%, not +4
    assert after["business"] == pytest.approx(19.6)


def test_no_type_drops_below_one_reel_a_week():
    _, _, after = rm.decide({**MIX, "business": 1.1}, table(travel=5.0, business=1.0))
    assert after["business"] == pytest.approx(1.0)
    changed, reason, after = rm.decide({**MIX, "business": 1.0},
                                       table(travel=5.0, business=1.0))
    assert not changed and "floor of one reel a week" in reason
    assert after == {**MIX, "business": 1.0}


def test_fewer_than_two_ranked_types_changes_nothing_and_says_why():
    changed, reason, after = rm.decide(MIX, table(travel=5.0, business=None))
    assert not changed and after == MIX
    assert "only 1 reel type could be ranked (travel)" in reason
    changed, reason, _ = rm.decide(MIX, table())
    assert not changed and "only 0" in reason


def test_unknown_inputs_are_never_ranked():
    t = table(travel=5.0, business=None, tech=None)
    assert [r.reel_type for r in t.ranked()] == ["travel"]
    assert t.rows["business"].rpm is None
    changed, _, _ = rm.decide(MIX, t)
    assert not changed


def test_a_tie_changes_nothing():
    changed, reason, _ = rm.decide(MIX, table(travel=3.0, business=3.0))
    assert not changed and "tie" in reason


@pytest.fixture
def records(memory_backend):
    memory_backend.responses.update({
        "SELECT at, changed, reason, before, after FROM reel_mix_log": [],
        "SELECT reel_type, per_week FROM reel_mix": [
            {"reel_type": k, "per_week": v} for k, v in MIX.items()],
        "WITH claim AS": [{"claimed": 1, "written": 4}],
    })
    return memory_backend


async def test_a_change_is_recorded_once_with_its_numbers_and_audited(records):
    t = table(travel=5.0, business=1.0)
    d = await rm.improve(t, NOW)
    assert d.changed and d.week_start == date(2026, 10, 12)
    [(sql, args)] = [e for e in records.sql_log if "WITH claim AS" in e[0]]
    assert "ON CONFLICT (week_start) DO NOTHING" in sql
    assert json.loads(args[4])["travel"] == 12.0
    based_on = json.loads(args[5])
    assert based_on["rows"]["travel"]["rpm"] == pytest.approx(5.0)
    assert based_on["rows"]["business"]["revenue_cents"] == 100
    assert based_on["baseline"] == "the mix in force"
    assert sorted(args[6]) == ["business", "stories", "tech", "travel"]
    [entry] = records.audit_rows
    assert entry.event == "reel_mix.decided"
    payload = json.loads(entry.payload)["data"]
    assert payload["before"]["travel"] == 10.0 and payload["after"]["travel"] == 12.0
    assert payload["based_on"]["rows"]["travel"]["rpm"] == pytest.approx(5.0)


async def test_no_change_is_still_logged_and_writes_no_mix(records):
    d = await rm.improve(table(travel=5.0), NOW)
    assert not d.changed and "only 1" in d.reason
    [(_, args)] = [e for e in records.sql_log if "WITH claim AS" in e[0]]
    assert args[1] is False and args[6] == [] and args[7] == []
    assert records.audit_rows[0].event == "reel_mix.decided"


async def test_a_second_run_in_the_same_week_changes_nothing(records):
    records.responses["SELECT at, changed, reason, before, after FROM reel_mix_log"] = [{
        "at": NOW - timedelta(hours=2), "changed": True, "reason": "earlier",
        "before": json.dumps(MIX), "after": json.dumps({**MIX, "travel": 12.0,
                                                        "business": 8.0})}]
    d = await rm.improve(table(travel=9.0, business=1.0), NOW)
    assert d.earlier and d.reason == "earlier" and d.after["travel"] == 12.0
    assert not [e for e in records.sql_log if "WITH claim AS" in e[0]]
    assert records.audit_rows == []


async def test_with_no_mix_yet_the_baseline_is_what_was_posted(records):
    records.responses["SELECT reel_type, per_week FROM reel_mix"] = []
    t = table(travel=5.0, business=1.0)
    t.rows["travel"].reels, t.rows["business"].reels = 5, 10
    d = await rm.improve(t, NOW)
    # Stories held at two per lesson, so the calendar's order is unchanged
    # until the Improver moves stories or business itself.
    assert d.before == {"business": 10.0, "stories": 20.0, "tech": 1.0, "travel": 5.0}
    assert d.after["travel"] == 6.0 and d.after["business"] == 9.0


async def test_a_first_change_between_other_types_keeps_the_calendar_at_two_to_one(
        records):
    records.responses["SELECT reel_type, per_week FROM reel_mix"] = []
    t = table(travel=5.0, tech=1.0)
    t.rows["travel"].reels, t.rows["tech"].reels, t.rows["business"].reels = 5, 5, 3
    d = await rm.improve(t, NOW)
    assert d.changed and d.after["travel"] == 6.0 and d.after["tech"] == 4.0
    assert d.after["stories"] / d.after["business"] == rm.STORIES_PER_LESSON


async def test_a_failed_audit_does_not_hide_a_change(records, monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("audit chain unavailable")
    monkeypatch.setattr("atlas.kernel.audit.audit", broken)
    d = await rm.improve(table(travel=5.0, business=1.0), NOW)
    assert d.changed and d.audit_problem
    text = rm.render(rm.Week(table(), d))
    assert "changed —" in text and "audit entry failed" in text


# ------------------------------------------------------------ the scheduler

@pytest.fixture
def ratio(monkeypatch):
    monkeypatch.setattr(cal, "_stories_per_lesson", cal.STORIES_PER_LESSON)


async def test_no_mix_keeps_two_stories_to_each_lesson(ratio, memory_backend):
    memory_backend.responses["SELECT reel_type, per_week FROM reel_mix"] = []
    assert await cal.load_mix(date(2026, 10, 5)) == 2.0
    order = [j["job_id"] for j in cal.scheduled()]
    assert [i.startswith("business-") for i in order[:6]] == [
        False, False, True, False, False, True]


async def test_an_unreadable_mix_keeps_the_default(ratio, memory_backend):
    # No canned answer: the read raises, and the calendar carries on at 2:1.
    assert await cal.load_mix(date(2026, 10, 5)) == 2.0


async def test_the_calendar_follows_the_mix(ratio, memory_backend):
    memory_backend.responses["SELECT reel_type, per_week FROM reel_mix"] = [
        {"reel_type": "stories", "per_week": 9.0},
        {"reel_type": "business", "per_week": 3.0}]
    assert await cal.load_mix(date(2026, 10, 5)) == 3.0
    order = [j["job_id"] for j in cal.scheduled()]
    assert [i.startswith("business-") for i in order[:4]] == [False, False, False, True]
    assert len(order) == 60 and len(set(order)) == 60
    stories = [i for i in order if not i.startswith("business-")]
    assert stories == [j["job_id"] for j in cal.jobs()], "each package keeps its order"


async def test_a_mix_without_a_business_share_keeps_the_default(memory_backend):
    memory_backend.responses["SELECT reel_type, per_week FROM reel_mix"] = [
        {"reel_type": "stories", "per_week": 9.0}]
    assert await rm.stories_per_lesson(date(2026, 10, 5)) == 2.0


# ------------------------------------------------------------ the Analyst

def _post(ref, source, kind="video", media=None, days_in=2):
    return {"platform": "instagram", "post_ref": ref, "posted_at": SINCE
            + timedelta(days=days_in), "media_id": media, "source": source, "kind": kind}


@pytest.fixture
def world(monkeypatch):
    data = {
        "posts": [_post("b1", "calendar:business-reels-v1-d03", media=1),
                  _post("t1", "weekly flycheap fares", media=2),
                  _post("s1", "reel_format:3", "story_script", media=3)],
        "reads": [{"platform": "instagram", "post_ref": r, "at": UNTIL, "views": v}
                  for r, v in (("b1", 2000.0), ("t1", 1000.0), ("s1", 5000.0))],
        "products": [
            {"key": "business_guide", "name": "30 Business Lessons",
             "reel_type": "business", "status": "live", "payment_link_id": "plink_b",
             "payment_link_url": "https://buy.stripe.com/b"},
            {"key": "fares", "name": "Fares", "reel_type": "travel", "status": "live",
             "payment_link_id": "plink_t", "payment_link_url": "https://buy.stripe.com/t"},
            {"key": "vault_kit", "name": "Vault Kit", "reel_type": "tech",
             "status": "draft", "payment_link_id": None, "payment_link_url": None}],
        "followers": [{"at": SINCE - timedelta(hours=1), "value": 4.0},
                      {"at": UNTIL - timedelta(hours=2), "value": 10.0}],
        "sales": ({"business_guide": {"name": "g", "sales": 2, "revenue_cents": 3800},
                   "fares": {"name": "f", "sales": 1, "revenue_cents": 500}}, None),
    }

    async def fetch_all(sql, *args):
        flat = " ".join(sql.split())
        if "max(at) AS at FROM reel_metrics" in flat:
            return [{"at": UNTIL}]
        if "FROM reel_posts p" in flat:           # WHERE p.at > $1 AND p.at <= $2
            return [p for p in data["posts"] if args[0] < p["posted_at"] <= args[1]]
        if "FROM reel_metrics" in flat:           # WHERE at > $1 AND at <= $2
            return [r for r in data["reads"] if args[0] < r["at"] <= args[1]]
        if "FROM shop_products" in flat:
            return data["products"]
        if "FROM observations o" in flat:
            return data["followers"]
        raise AssertionError(flat[:80])

    async def sales_since(days=7, *, since=None, until=None):
        assert (since, until) == (SINCE, UNTIL), "Stripe is read over the same window"
        if isinstance(data["sales"], Exception):
            raise data["sales"]
        return data["sales"]

    async def pageviews(since, until):
        assert (since, until) == (SINCE, UNTIL)
        return {"views": 120, "visitors": 40, "sites": [("flycheap.example", 90)]}, None

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    monkeypatch.setattr(shop, "sales_since", sales_since)
    monkeypatch.setattr(posthog, "pageviews_between", pageviews)
    return data


async def test_revenue_per_thousand_views_per_reel_type(world):
    t = await rm.analyse(NOW)
    assert (t.since, t.until) == (SINCE, UNTIL)
    b, tr = t.rows["business"], t.rows["travel"]
    assert b.state == "ranked" and b.rpm == pytest.approx(19.0)      # $38 / 2,000
    assert tr.state == "ranked" and tr.rpm == pytest.approx(5.0)     # $5 / 1,000
    assert t.rows["stories"].state == "unranked" and t.rows["stories"].views == 5000
    assert t.rows["tech"].state == "unranked" and "not on sale" in t.rows["tech"].why
    assert "+6 over 3 Instagram reel(s), +2.00 per reel" in t.followers
    assert "120 pageviews" in t.traffic and "not ranked" in t.traffic
    text = rm.render(rm.Week(t, None))
    assert "business: $19.00 per 1,000 views" in text
    assert "UTC" in text and "Stripe read" in text


async def test_stripe_unreadable_is_unknown_never_zero(world):
    world["sales"] = ({}, "Stripe answered 401 for 30 Business Lessons")
    t = await rm.analyse(NOW)
    for kind in ("business", "travel"):
        row = t.rows[kind]
        assert row.state == "unknown" and row.rpm is None and row.revenue_cents is None
        assert "401" in row.why
    text = rm.render(rm.Week(t, None))
    assert "business: UNKNOWN, not ranked" in text and "$0.00" not in text


async def test_a_reel_whose_views_were_not_read_makes_its_type_unknown(world):
    world["reads"][0]["views"] = None                    # b1: the metric wasn't given
    world["reads"][1]["at"] = UNTIL - timedelta(days=3)  # t1: last read days ago
    t = await rm.analyse(NOW)
    assert t.rows["business"].state == "unknown"
    assert "instagram b1 was not read" in t.rows["business"].why
    assert t.rows["travel"].state == "unknown"


async def test_an_unattributed_post_is_not_counted_as_stories(world):
    world["posts"].append(_post("x1", None, media=9))
    world["reads"].append({"platform": "instagram", "post_ref": "x1", "at": UNTIL,
                           "views": 99999.0})
    t = await rm.analyse(NOW)
    assert t.rows["stories"].views == 5000
    assert t.unattributed == 1
    assert all(r.state == "unknown" for r in t.rows.values())
    assert "could not be tied to a reel type" in t.rows["business"].why


async def test_an_older_reel_counts_only_the_views_it_gained_in_the_window(world):
    world["posts"][0]["posted_at"] = SINCE - timedelta(days=3)
    world["reads"].append({"platform": "instagram", "post_ref": "b1",
                           "at": SINCE - timedelta(hours=2), "views": 1500.0})
    t = await rm.analyse(NOW)
    assert t.rows["business"].views == 500
    assert t.rows["business"].reels == 0                 # not posted in the window


async def test_a_reel_past_the_metrics_window_does_not_make_its_type_unknown(world):
    # 33 days old: metrics stopped re-reading it 3 days ago.
    world["posts"].append(_post("b0", "calendar:business-reels-v1-d01", media=7,
                                days_in=-26))
    world["reads"].append({"platform": "instagram", "post_ref": "b0",
                           "at": UNTIL - timedelta(days=3), "views": 50000.0})
    t = await rm.analyse(NOW)
    assert t.rows["business"].state == "ranked"
    assert t.rows["business"].views == 2000


async def test_a_product_sold_outside_stripe_links_is_unknown(world):
    world["products"][1].update(payment_link_id=None,
                                payment_link_url="https://flycheap.example/pricing")
    t = await rm.analyse(NOW)
    assert t.rows["travel"].state == "unknown"
    assert "not a Stripe payment link" in t.rows["travel"].why


async def test_the_weekly_section_shows_the_decision_and_its_basis(world):
    t = await rm.analyse(NOW)
    d = rm.Decision(date(2026, 10, 12), True, "business earned more",
                    MIX, {**MIX, "business": 12.0, "travel": 8.0}, NOW)
    text = await rm.section(rm.Week(t, d))
    assert "changed — business 10.00 -> 12.00, travel 10.00 -> 8.00" in text
    assert "Nothing drafts tech or travel reels yet" in text


def test_nothing_here_asks_a_model_for_a_number():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(rm))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = " ".join(a.name for a in node.names)
            assert "llm" not in f"{getattr(node, 'module', '') or ''} {names}"
