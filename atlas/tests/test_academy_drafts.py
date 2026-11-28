"""Draft Academy scripts (atlas/modules/academy/tutorial_drafts): each one holds
the script shape, says it is an unapproved draft, and stays out of the runway."""
from __future__ import annotations

import importlib
import pkgutil
import re

import pytest

from atlas.modules.academy import tutorial_drafts, tutorial_scripts
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut

NAME = re.compile(r"^(?P<series>[a-z][a-z0-9]*)_(?P<number>\d{2})$")
BANNED = ("delve", "fast-paced world", "seamless", "leverage", "unlock",
          "game-changer", "game changer", "important to note", "dive in",
          "in conclusion")
EXPECTED = {"explained": 1, "security": 2, "future": 2}


def _names() -> list[str]:
    return sorted(i.name for i in pkgutil.iter_modules(tutorial_drafts.__path__))


def _load(name: str):
    return importlib.import_module(f"{tutorial_drafts.__name__}.{name}")


def test_the_draft_count_per_series():
    got: dict[str, int] = {}
    for n in _names():
        m = NAME.match(n)
        assert m, n
        got[m["series"]] = got.get(m["series"], 0) + 1
    assert got == EXPECTED


@pytest.mark.parametrize("name", _names())
def test_a_draft_is_unapproved_and_outside_the_runway(name):
    m = NAME.match(name)
    mod = _load(name)
    ep = mod.EPISODE
    assert mod.STATUS == "draft/unapproved"
    assert ep.number == int(m["number"]) and ep.scripted
    assert ep.number not in {e.number for e in tutorial_scripts.for_series(m["series"])}
    assert name not in tutorial_scripts.names()
    s = ts.series(m["series"])
    assert not any(e.scripted and e.number == ep.number for e in s.episodes)


@pytest.mark.parametrize("name", _names())
def test_a_draft_has_the_script_shape(name):
    m = NAME.match(name)
    ep = _load(name).EPISODE
    assert ep.hook is not None and ep.topic_said and ep.needs and ep.payoff
    assert ep.links and all(url.startswith("https://") for _, url in ep.links)
    kinds = [sc.kind for sc in ep.scenes]
    assert "demo" not in kinds
    assert {"pause", "subscribe", "checklist"} <= set(kinds)
    assert kinds.count("quiz") == 2 and kinds[-1] == "checklist"
    for sc in ep.scenes:
        assert sc.kind in ts.SCENE_KINDS and sc.said
        if sc.kind in ts.LIST_KINDS:
            assert len(sc.said) == len(sc.items), sc.heading
        if sc.kind == "code":
            assert sc.code and len(sc.marks) in (0, len(sc.said))
        if sc.kind in ts.BEAT_KINDS:
            assert not sc.chapter
        if sc.kind == "quiz":
            assert len(sc.said) == 2 and -1 <= sc.answer < len(sc.items)
    # 530-620 once the "Next time" line (10-15 words) is added by titled()
    assert 515 <= ts.words(ep) <= 608, ts.words(ep)
    lines = [ln for sc in (ep.payoff, ep.hook, *ep.scenes) for ln in sc.said]
    for ln in lines:
        low = ln.lower()
        assert not [b for b in BANNED if b in low], ln
        assert "[" not in ts.plain(ln) and "  " not in ts.plain(ln), ln
        assert len(ts.cues(ln)) <= 1, ln
    used = [c for ln in lines for c in ts.cues(ln)]
    assert len(used) >= 4 and len(used) * 2 <= len(lines), used
    for sc in (ep.payoff, ep.hook, *ep.scenes):
        if re.search(r"safe|security|refund|money|legal|keep", sc.heading + " " + sc.chapter,
                     re.IGNORECASE):
            for ln in sc.said:
                assert not {"chuckling", "laughing", "laughing softly"} & set(ts.cues(ln))
    assert ts.tags_refusal(m["series"], ep) is None
    title = tut.final_title(ep, 600.0)
    assert len(title) <= 60 and tut.title_refusal(ep, title, 600.0) is None, title
