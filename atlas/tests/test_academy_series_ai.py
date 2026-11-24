"""The three 2026-10-18 series (the owner, video system pack): AI Explained, AI
Security, Where AI Is Going -- their plans, holds and labels."""
from __future__ import annotations

from dataclasses import replace

from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorial_series_ai as ai
from atlas.modules.academy import tutorials as tut

NEW = ("explained", "security", "future")
WRITABLE = {("explained", 1), ("explained", 6), ("explained", 7), ("future", 6)}


def test_the_three_series_are_planned_with_the_specs_lessons():
    assert [len(ts.series(k).episodes) for k in NEW] == [8, 8, 6]
    for key in NEW:
        s = ts.series(key)
        assert s.playlist == ts.PLAYLISTS[key] and ts.STORE_URL in s.playlist_description
        assert key in ts.SCRIPT_SERIES
        for e in s.episodes:
            assert not e.scripted and 5 <= len(e.outline) <= 8, e.slug
            assert e.opener_topic == ts.OPENER_TOPICS[(key, e.number)]
            assert not any(ch.isdigit() for ch in e.opener_topic), e.opener_topic


def test_every_outline_carries_the_result_the_anchor_and_the_check():
    for key in NEW:
        for e in ts.series(key).episodes:
            first, anchor, check = e.outline[:3]
            assert first.startswith("Result at 0-3 s: "), e.slug
            assert anchor.startswith(("Demo anchor: ", "Attack (own test setup only): ",
                                      "Anchor: ")), e.slug
            want = "Fix verified: " if key == "security" else "Checkpoint: "
            assert check.startswith(want), e.slug


def test_the_specs_thumbnail_words_are_used_where_they_fit():
    for (key, n), words in ai.SPEC_THUMB_WORDS.items():
        got = ts.series(key).episode(n).thumb_title.replace("\n", " ")
        if len(words.split()) <= 5:
            assert got == words, (key, n)
    # the one over the spec's own 2-5 words is a draft, not cut silently
    assert len(ai.SPEC_THUMB_WORDS[("security", 1)].split()) == 6


def test_episodes_that_need_a_recording_or_owner_are_held():
    held = {(k, e.number) for k in NEW for e in ts.series(k).episodes
            if ts.held(k, e) is not None}
    every = {(k, e.number) for k in NEW for e in ts.series(k).episodes}
    assert every - held == WRITABLE
    # every security lesson verifies its fix on camera; #6 and #8 need the owner
    assert {n for k, n in held if k == "security"} == set(range(1, 9))
    assert ai.HOLDS[("security", 6)][0] == ai.OWNER
    assert ("future", 5) in held and ("explained", 8) in held
    assert all(why.startswith("held: ") for why in
               (ts.held(k, ts.series(k).episode(n)) for k, n in held))


async def test_a_held_episode_never_renders_even_when_scripted(monkeypatch):
    s = ts.series("security")
    ep = replace(s.episode(1), scenes=(ts.Scene("Recap", "bullets", "Recap", ("a",),
                                                items=("a",)),))
    assert ts.held("security", ep) is not None        # no recording shown
    monkeypatch.setattr(tut, "resolve", lambda k, n: (s, ep))
    got = await tut.render_tutorial_dry("security", 1)
    assert not got.ok and got.detail == ts.held("security", ep)
    _d, _m, why = await tut.render("security", 1, preview=True)
    assert why == ts.held("security", ep)


def test_a_recording_hold_lifts_only_with_a_recording_on_disk(monkeypatch, tmp_path):
    from atlas.modules.academy import tutorial_demos
    ep = replace(ts.series("explained").episode(2),
                 scenes=(ts.Scene("Run it", "demo", "Run it", ("a",), demo="x"),))
    monkeypatch.setattr(tutorial_demos, "video_path", lambda k: tmp_path / f"{k}.mp4")
    assert ts.held("explained", ep) is not None
    (tmp_path / "x.mp4").write_bytes(b"v")
    assert ts.held("explained", ep) is None
    # a OWNER hold never lifts from a file
    owner = replace(ts.series("security").episode(6), scenes=ep.scenes)
    assert ts.held("security", owner) is not None


def test_the_lineup_skips_held_episodes_and_names_the_first_when_only_held_remain():
    sec = ts.series("security")
    n, note = tut.next_in_series(sec, set())
    assert n is None and note == ts.held("security", sec.episode(1))
    fut = ts.series("future")
    assert tut.next_in_series(fut, set()) == (None, "script not written for future #6")
    exp = ts.series("explained")
    done = {("explained", 1)}
    assert tut.next_in_series(exp, done) == (None, "script not written for explained #6")


def test_each_new_series_resolves_to_its_own_3d_kit():
    from atlas.modules.academy import tutorial_design as design
    for key in NEW:
        assert key in design.KIT_SERIES
        loop, still = design.loop_path(key), design.still_path(key)
        assert loop.stat().st_size > 100_000, key
        assert still.read_bytes()[:3] == b"\xff\xd8\xff", key           # a JPEG
        assert design.TOPICS[design.SERIES_TOPIC[key]] == loop.name
        assert design.TOPIC_ALT[design.SERIES_TOPIC[key]]
    # AI Security has its own scene, never the existing "security" topic loop
    assert design.loop_path("security") != design.topic_path("security")
    assert design.loop_path("security").name == "loop-vault.mp4"
    # the dry render of a writable episode on its kit: test_academy_tutorials
    # test_a_writable_new_series_episode_renders_on_its_own_kit


def test_the_security_rules_and_the_opinion_label_are_in_code():
    rules = " ".join(ai.SECURITY_RULES).lower()
    for want in ("own accounts and test data", "name the defense before",
                 "no fear music", "no red flashing"):
        assert want in rules
    for e in ts.series("future").episodes:
        assert e.opener_topic.startswith(ai.OPINION_TOPIC)
        assert ts.opening_lines(e)[1].startswith(f"In this lesson: {ai.OPINION_TOPIC}")
