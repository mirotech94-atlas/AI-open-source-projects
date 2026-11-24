"""A teaching Short's YouTube tags (2026-10-18): the lesson's own upload tags
(the channel's, the series' and the lesson's), within YouTube's 500 as
tags_cost counts them, through clips.promo.youtube_tags. Before this the
Shorts went up with the fallback pair ["ATLAS Academy", "AI"]."""
from __future__ import annotations

import pytest

from atlas.modules.academy import promo_copy
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.clips import promo

TEACHING = [(s, e) for s in ts.SERIES for e in s.episodes if e.scripted]


@pytest.mark.parametrize("s,ep", TEACHING, ids=lambda x: getattr(x, "slug", ""))
def test_a_teaching_short_carries_its_lessons_series_and_lesson_tags(s, ep):
    for kind in ("payoff", "stuck", "quiz"):
        slug = tut.teaching_slug(s, ep, kind)
        tags = promo.youtube_tags(slug)
        assert tags != promo.YT_TAGS
        assert ts.tags_cost(tags) <= ts.TAGS_LIMIT
        assert set(ts.SERIES_TAGS.get(s.key, ())) <= set(tags)
        assert set(ts.lesson_tags(s.key, ep)) <= set(tags)
        assert tags == [t for t in tut.upload_tags(s, ep) if t in tags]   # same order


def test_the_tags_are_cut_to_the_limit_by_tags_cost(monkeypatch):
    long = tuple(f"tag number {i} with a long phrase" for i in range(40))
    monkeypatch.setattr(tut, "upload_tags", lambda s, ep: long)
    tags = tut.teaching_tags("tutorial:n8n:1:payoff")
    assert tags and ts.tags_cost(tags) <= ts.TAGS_LIMIT < ts.tags_cost(long)
    assert tags == list(long[:len(tags)])


def test_a_product_promo_keeps_its_own_tags():
    slug = next(iter(promo_copy.COPY))
    assert promo.youtube_tags(slug) == promo_copy.tags_for(slug)


@pytest.mark.parametrize("slug", ["", "sell-with-ai-unknown", "tutorial:n8n:99:payoff",
                                  "tutorial:nope:1:quiz", "tutorial:n8n:x:quiz",
                                  "tutorial:n8n:1"])
def test_anything_else_keeps_the_old_pair(slug):
    assert tut.teaching_tags(slug) is None or slug.startswith("tutorial:n8n:1:")
    assert promo.youtube_tags(slug) == promo.YT_TAGS
