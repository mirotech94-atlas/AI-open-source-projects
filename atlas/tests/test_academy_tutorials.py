"""ATLAS Academy YouTube tutorials (academy/tutorials.py, tutorial_series.py,
tutorial_design.py, the playlist calls in youtube/api.py). the owner, 2026-10-09:
n8n and Claude & MCP series, a fixed intro and ending, playlists, 4-10
minutes, a thumbnail and https://agency.example.com in every description."""
from __future__ import annotations

import ast
import itertools
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.kernel.contracts import ActionResult, Tier
from atlas.kernel.permissions import denial_reason
from atlas.modules.academy import tutorial_demos as td
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_music as tm
from atlas.modules.academy import tutorial_scripts
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.youtube import api

REAL_UPLOAD = api.upload
FORCE_SSL = "https://www.googleapis.com/auth/youtube.force-ssl"
N8N, MCP = ts.series("n8n"), ts.series("claude")
assert N8N is not None and MCP is not None
# the owner 2026-10-18, video system pack: three more series, planned only, most
# of their episodes held for a recording or for the owner (tutorial_series_ai).
NEW = ("explained", "security", "future")


# ------------------------------------------------------------------ catalog

def test_the_roadmap_series_and_their_playlists():
    assert [(s.key, s.playlist) for s in ts.SERIES] == [
        ("channel", ""),
        ("claude", "Claude Mastery: MCP, Skills & Connectors"),
        ("n8n", "n8n Workflows from Zero"),
        ("project", "Build a Real Project: Claude + n8n"),
        ("business", "AI Automation for Small Business"),
        ("agents", "AI Agents Explained Simply"),
        ("local", "Local AI: Hermes Agent on NVIDIA DGX Spark"),
        ("explained", "AI Explained"),
        ("security", "AI Security"),
        ("future", "Where AI Is Going")]
    for s in ts.SERIES:
        # the security and opinion series start at Level 1 (nothing in them
        # is a "Basics" lesson); every other series at 0
        assert s.episodes and s.episodes[0].level == (1 if s.key in NEW[1:] else 0)
        assert not s.playlist or ts.STORE_URL in s.playlist_description
        assert len({e.number for e in s.episodes}) == len(s.episodes)
    assert [len(s.episodes) for s in ts.SERIES] == [1, 8, 9, 8, 7, 6, 10, 8, 8, 6]


def test_every_series_runs_from_level_0_to_advanced():
    """the owner, 2026-10-10: "teach the basics first and then advanced, 0 to
    advanced, for all series"."""
    for s in ts.SERIES:
        levels = [e.level for e in s.episodes]
        assert levels[0] == (1 if s.key in NEW[1:] else 0), s.key
        assert levels == sorted(levels), s.key            # never back down
        assert all(lv in ts.LEVEL_NAMES for lv in levels)
        assert [e.number for e in s.episodes] == sorted(e.number for e in s.episodes)
        # the 2026-10-18 series are a concept series, a security series and a
        # monthly opinion (the pack's spec); none promises advanced
        if s.playlist and s.key not in NEW:
            assert max(levels) == 3, s.key                # reaches advanced
    claude = ts.series("claude")
    assert claude.episodes[0].slug == "what-is-claude"
    assert claude.episode(1).slug == "mcp-what-it-is" and claude.episode(1).level >= 1
    assert ts.series("n8n").episodes[0].slug == "what-is-n8n"
    assert ts.series("n8n").episode(1).slug == "n8n-first-workflow"
    assert ts.series("agents").episodes[0].scripted


def test_the_level_is_data_for_the_frames_the_tags_and_the_description():
    for ep in ALL:
        assert ep.level_label == f"Level {ep.level} · {ts.LEVEL_NAMES[ep.level]}"
        assert ep.level_tag in ep.tags, ep.slug
    ep = ts.series("claude").episodes[0]
    assert ep.level_label == "Level 0 · Basics"
    text = tut.description(ts.series("claude"), ep, CH)
    # 2026-10-18: the result and what you need lead; then level and time
    assert text.splitlines()[3] == "Level 0 · Basics"            # not measured yet
    assert tut.description(ts.series("claude"), ep, CH, 5).splitlines()[:5] == [
        "What you'll have: a usable draft.", f"What you need: {'; '.join(ep.needs)}.",
        "", "Level 0 · Basics · 5 minutes", ep.summary]
    welcome = ts.series("channel").episodes[0]
    assert tut.description(ts.series("channel"), welcome, CH).startswith(welcome.summary)


def test_the_beginner_phrases_are_tagged():
    claude0, n8n0 = ts.series("claude").episodes[0], ts.series("n8n").episodes[0]
    for t in ("claude for beginners", "how to use claude", "what is claude ai",
              "claude ai"):
        assert t in claude0.tags, t
    for t in ("n8n basics", "what is n8n", "automation for beginners", "n8n",
              "n8n tutorial"):
        assert t in n8n0.tags, t


ALL = [e for s in ts.SERIES for e in s.episodes]
SCRIPTED = [e for e in ALL if e.scripted]


def test_the_scripted_episodes_are_each_ladders_first_rungs():
    """Scripts are written in ladder order: a series never has a scripted
    episode above an unscripted one. A held episode (it needs a recording or
    the owner, tutorial_series.held) is skipped by the writer and the lineup."""
    for s in ts.SERIES:
        flags = [e.scripted for e in tut.ladder(s) if ts.held(s.key, e) is None]
        assert flags == sorted(flags, reverse=True), s.key


@pytest.mark.parametrize("ep", ALL, ids=lambda e: e.slug)
def test_every_episode_has_title_tags_hashtags_and_thumbnail_words(ep):
    assert len(ep.title) <= tut.TITLE_MAX
    assert len(ep.thumb_title.split("\n")) == 2
    # 2-5 words (docs/academy-video-system/tags-and-hashtags.md, the owner
    # 2026-10-18; tutorial_thumbs.MAX_WORDS)
    assert 2 <= len(ep.thumb_title.replace("\n", " ").split()) <= 5
    assert tut.tags_length(ep.tags) <= tut.TAGS_MAX
    assert len(ep.hashtags) == 3 and all(h.startswith("#") for h in ep.hashtags)
    assert "ATLAS Academy" in ep.tags
    if not ep.scripted:
        assert 5 <= len(ep.outline) <= 8


def test_no_other_creator_brand_or_money_tag():
    banned = ("networkchuck", "nate herk", "hostinger", "higgsfield", "decoding",
              "make money", "side hustle", "income", "$")
    for s in ts.SERIES:
        for ep in s.episodes:
            # what is uploaded: the full tag list and the formula titles
            sent = (*ts.full_tags(s.key, ep, 2026), ep.title, *ts.hashtags_for(s.key, ep),
                    tut.final_title(ep, 240.0), tut.final_title(ep, 600.0))
            text = " ".join(sent).lower()
            assert not [b for b in banned if b in text], ep.slug
            assert not re.search(r"\b(secret|earn|income|per month)\b", text), ep.slug


@pytest.mark.parametrize("ep", SCRIPTED, ids=lambda e: e.slug)
def test_every_scripted_episode_has_a_sound_shape(ep):
    assert all(url.startswith("https://") for _, url in ep.links)
    assert len(ep.scenes) >= 3 and ep.scenes[0].chapter
    for sc in tut.opening(ep) + ep.scenes:
        assert sc.kind in ts.SCENE_KINDS and sc.said
        if sc.kind in ts.LIST_KINDS:
            assert len(sc.said) == len(sc.items), sc.chapter
        if sc.kind == "code":
            assert sc.code and len(sc.marks) in (0, len(sc.said))
        if sc.kind in ts.BEAT_KINDS:
            assert not sc.chapter            # a beat, never its own chapter
        if sc.kind == "quiz":
            assert len(sc.said) == 2 and -1 <= sc.answer < len(sc.items)
    if ep.slug == "welcome":
        assert (ep.min_seconds, ep.max_seconds) == (90.0, 180.0) and not ep.hook
        assert 180 <= ts.words(ep) <= 400
    else:
        assert ep.hook is not None and ep.topic_said
        # a 4-10 minute video, its teaching under five minutes when it says so
        assert 330 <= ts.words(ep) <= 620, ts.words(ep)


def test_every_lesson_is_interactive():
    for ep in SCRIPTED:
        kinds = [sc.kind for sc in ep.scenes]
        assert "quiz" in kinds, ep.slug
        if ep.slug != "welcome":
            assert {"pause", "subscribe", "checklist"} <= set(kinds), ep.slug
            assert kinds.count("quiz") == 2


LESSONS = [e for e in SCRIPTED if e.slug != "welcome"]
# A manifest of the result-first opening (2026-10-17): the sting at 6-7 s.
OPENED = {"opening": "result", "sting_at": 6.0, "sting_seconds": 1.0}


def opened(seconds: float, key: str = "n8n", n: int = 0) -> dict:
    """A manifest's opening and end-card record (2026-10-17): the sting at
    6-7 s, a 15 s end card to the end, naming the catalog's next lesson."""
    nxt = ts.next_up(key, ts.series(key).episode(n))
    return {**OPENED, "card_at": seconds - 15.0, "card_seconds": 15.0,
            "next_title": nxt.title if nxt else "", "next_kind": nxt.kind if nxt else ""}
SLOW_WPS = tut.FISH_WORDS_PER_SECOND * 0.9      # the cautious side of the measured pace


# --------------------------------------------- rule 17: the payoff comes first

@pytest.mark.parametrize("key,ep", [(s.key, e) for s in ts.SERIES for e in s.episodes
                                    if e.scripted and e.opener_topic],
                         ids=lambda x: getattr(x, "slug", x))
def test_every_lesson_opens_on_its_payoff_and_the_sting_is_one_second(key, ep):
    """Rule 17: the first thing after the opener is the finished result."""
    assert ep.payoff is not None and ep.payoff.kind == "payoff", key
    assert len(ep.payoff.said) == 1 and ep.payoff.code
    assert tut.design.STING_SECONDS <= 1.0


@pytest.mark.parametrize("ep", LESSONS, ids=lambda e: e.slug)
def test_the_payoff_is_one_result_in_the_hooks_picture(ep):
    p = ep.payoff
    assert p is not None and tut.first_scene_kind(ep) == "payoff"
    assert p.chapter == ts.PAYOFF_CHAPTER and p.heading == "Here's what you'll have"
    assert "\n" not in p.code and p.items == ep.hook.items, ep.slug
    # 2026-10-17: the payoff's result IS the opening (ResultTitle); no
    # payoff or hook scene plays after it
    assert tut.opening(ep) == ()
    # the payoff's sentence is said once: never also left in the hook, and
    # no hook line half-repeats it (rule 14: no repeats)
    assert p.said[0] not in ep.hook.said, ep.slug
    said = _long_words(p.said[0])
    for line in ep.hook.said:
        words = _long_words(line)
        assert len(words & said) <= 0.6 * len(words), (ep.slug, line)


def _long_words(line: str) -> set[str]:
    """The words of `line` of four letters or more, lowercased, no cues or
    punctuation."""
    return {w for w in re.sub(r"[^a-z' ]+", " ", ts.plain(line).lower()).split()
            if len(w.strip("'")) >= 4}


def test_the_welcome_keeps_its_layout():
    ep = ts.series("channel").episode(1)
    assert ep.payoff is None and not ep.hook
    assert tut.first_scene_kind(ep) == "intro" and ep.scenes[0].kind == "bullets"


def test_a_payoff_shows_its_result_from_the_first_step():
    ep = N8N.episode(1)
    doc = design.slide(ep.payoff, 0, mark="m", chapter=0, chapters=1)
    assert 'class="bubble"' in doc and "Lisbon is in Portugal" in doc
    assert '<h1 class="small">Here' in doc
    hook = design.slide(ep.hook, 0, mark="m", chapter=0, chapters=1)
    assert 'class="bubble hidden"' in hook            # the hook still builds to it


def test_the_sting_is_cut_to_its_length_and_keys_the_intro_cache(monkeypatch):
    argv = tut.segment_argv("/w/s.png", "/w/a.wav", 8.0, "/w/o.mp4",
                            bg=str(design.INTRO_LOOP), lead=str(design.STING))
    i = argv.index(str(design.STING))
    # the sting's LAST second: the mark whole, never half-assembled
    assert argv[i - 3:i] == ["-sseof", f"-{design.STING_SECONDS:.1f}", "-i"]
    assert "-t" not in argv[:i]
    # 2026-10-17: the welcome's intro carries no sting at all, so its key
    # no longer follows the sting's length (and never matches an old one)
    before = tut.cache_key("intro", "x", True)
    monkeypatch.setattr(design, "STING_SECONDS", 4.0)
    assert tut.cache_key("intro", "x", True) == before


@pytest.mark.parametrize("ep", LESSONS, ids=lambda e: e.slug)
def test_every_lesson_has_enough_teaching_to_clear_four_minutes(ep):
    """n8n #0 at ~490 words rendered 232 s (2026-10-11), under the 240 s
    floor: every scripted lesson carries at least 530 words of teaching."""
    assert ts.words(ep) >= 530, ts.words(ep)
    total, _body = tut.estimate_seconds(ep, wps=tut.FISH_WORDS_PER_SECOND)
    assert total >= ep.min_seconds, total


@pytest.mark.parametrize("ep", SCRIPTED, ids=lambda e: e.slug)
def test_the_slower_voice_still_fits_every_window_and_claim(ep):
    """the owner, 2026-10-11 "no rush": at the tutorial voice speed and a cautious
    pace, every video stays under its ceiling, the welcome inside 90-180 s,
    and an "under five minutes" body under 300 s."""
    total, body = tut.estimate_seconds(ep, wps=SLOW_WPS)
    assert total <= ep.max_seconds, total
    if ep.slug == "welcome":
        fast, _ = tut.estimate_seconds(ep)
        assert ep.min_seconds <= fast and total <= ep.max_seconds
    if ts.UNDER_FIVE.search(ts.topic_line(ep)):
        assert body < ts.UNDER_FIVE_SECONDS, body


def test_the_estimate_follows_the_voice_speed(monkeypatch):
    ep = N8N.episode(1)
    monkeypatch.setenv("ACADEMY_TUTORIALS_VOICE_SPEED", "1.0")
    normal = tut.estimate_seconds(ep)[0]
    monkeypatch.delenv("ACADEMY_TUTORIALS_VOICE_SPEED")
    assert tut.voice_speed() == 0.8
    assert tut.estimate_seconds(ep)[0] > normal
    for bad, want in (("x", 0.8), ("0.1", 0.5), ("9", 2.0), ("nan", 0.8)):
        monkeypatch.setenv("ACADEMY_TUTORIALS_VOICE_SPEED", bad)
        assert tut.voice_speed() == want


def test_the_default_voice_speed_paces_every_lesson_at_140_to_145(monkeypatch):
    """2026-10-18: narration.md asks 130-150 words a minute explaining; at
    0.9 the lessons measured 156-161. The speech-rate model (Fish at
    FISH_WORDS_PER_SECOND times the speed) puts every scripted lesson's
    narration, counted as video_qa counts words, at 140-145 at the default,
    inside Fish's 0.5-2.0 prosody range."""
    from atlas.modules.academy import video_qa
    monkeypatch.delenv("ACADEMY_TUTORIALS_VOICE_SPEED", raising=False)
    speed = tut.voice_speed()
    assert speed == tut.VOICE_SPEED_DEFAULT == 0.8 and 0.5 <= speed <= 2.0
    lo, hi = tut.PACE_TARGET
    assert 130 <= lo < hi <= 150
    for s in ts.SERIES:
        for ep in s.episodes:
            if not ep.scripted:
                continue
            lines = [ts.plain(x) for sc in ep.scenes for x in sc.said]
            spoken = sum(len(x.split()) for x in lines) / (
                tut.FISH_WORDS_PER_SECOND * speed)
            wpm = sum(video_qa.words(x) for x in lines) / spoken * 60
            assert lo <= wpm <= hi, (s.key, ep.number, round(wpm))
            assert not lo <= wpm * 0.9 / speed <= hi     # 0.9 was outside it


def test_only_the_tutorials_ask_fish_to_slow_down(monkeypatch):
    from atlas.modules._fish import fish_request
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "k")
    assert "prosody" not in fish_request("hi")[2]           # phone, menu, reels
    assert fish_request("hi", speed=0.9)[2]["prosody"] == {"speed": 0.9, "volume": 0}
    assert fish_request("hi", speed=5)[2]["prosody"]["speed"] == 2.0


async def test_the_tutorial_narration_sends_the_voice_speed(monkeypatch, tmp_path):
    from atlas.modules.content import stills
    seen = {}

    async def narration(text, speed=None, model=None):
        seen["speed"] = speed
        return b"mp3", "Fish", None
    monkeypatch.setattr(stills, "narration", narration)
    monkeypatch.setenv("ACADEMY_TUTORIALS_VOICE_SPEED", "0.85")
    _path, _note, why = await tut._narrate("hello", str(tmp_path / "a"), False)
    assert why is None and seen["speed"] == 0.85


def test_a_scene_ends_with_a_longer_breath():
    plan = tut.beats(N8N.episode(1).scenes)
    ends = [b for b in plan if b.last_of_scene]
    assert ends and all(b.said and not b.silent for b in ends)
    assert not any(b.last_of_scene for b in plan if b.scene.kind == "pause")


def test_under_five_minutes_is_said_only_while_the_teaching_fits():
    # a topic line that is said (an episode with no opener topic)
    ep = replace(N8N.episode(1), opener_topic="")
    assert ts.UNDER_FIVE.search(ts.topic_line(ep))
    assert ts.claim_refusal(ep, 299.0) is None
    assert "under five minutes" in ts.claim_refusal(ep, 300.0)
    welcome = ts.series("channel").episodes[0]
    assert ts.claim_refusal(welcome, 900.0) is None       # it claims nothing


def test_an_unsaid_topic_line_claims_nothing():
    """n8n #1's topic_said still reads "under five minutes", but a lesson
    opens on its result and never says it (topic_line is ""), so its
    teaching length is not held to 300 s."""
    ep = N8N.episode(1)
    assert ts.UNDER_FIVE.search(ep.topic_said) and ts.topic_line(ep) == ""
    assert ts.claim_refusal(ep, 330.0) is None


def test_the_beats_count_down_pause_and_join_chapters():
    ep = N8N.episode(1)
    plan = tut.beats(ep.scenes)
    quiz = [b for b in plan if b.scene.kind == "quiz"]
    assert [b.countdown for b in quiz[:5]] == [None, 3, 2, 1, None]
    assert all(b.silent == 1.0 for b in quiz[1:4])
    hold = [b for b in plan if b.hold]
    assert len(hold) == 1 and hold[0].silent == ts.PAUSE_HOLD
    chapters = tut.chapters_of_scenes(ep.scenes)
    assert chapters[1] == chapters[0]                    # the quiz joins chapter 1


def test_the_scripts_name_no_price_and_no_income():
    text = " ".join(x for s in ts.SERIES for e in s.episodes for sc in e.scenes
                    for x in sc.said + sc.items).lower()
    for word in ("price", "earn", "income", "make money", "per month", "usd"):
        assert not re.search(rf"\b{word}\b", text), word


def test_the_n8n_workflow_json_is_a_real_three_node_chain():
    wf = json.loads(ts.N8N_WORKFLOW_JSON)
    names = [n["name"] for n in wf["nodes"]]
    assert [n["type"] for n in wf["nodes"]] == [
        "n8n-nodes-base.webhook", "n8n-nodes-base.httpRequest", "n8n-nodes-base.slack"]
    conn = wf["connections"]
    assert conn["Webhook"]["main"][0][0]["node"] == "HTTP Request"
    assert conn["HTTP Request"]["main"][0][0]["node"] == "Slack"
    assert set(conn) <= set(names)
    # the free, no-key API the live demo really calls (2026-10-11)
    http = wf["nodes"][1]["parameters"]
    assert http["url"] == "https://geocoding-api.open-meteo.com/v1/search"
    assert http["queryParameters"]["parameters"][0]["name"] == "name"


def test_the_mcp_server_is_valid_python_and_never_prints():
    tree = ast.parse(ts.MCP_SERVER_PY)
    assert "print(" not in ts.MCP_SERVER_PY
    assert any(isinstance(n, ast.ImportFrom) and n.module == "mcp.server" for n in tree.body)
    assert json.loads(ts.MCP_DESKTOP_JSON)["mcpServers"]["hello"]["command"] == "uv"


def test_the_intro_and_ending_say_what_owner_asked():
    """the owner, 2026-10-12: the "new channel" opener is the channel welcome's
    only. 2026-10-17 (the video system): every lesson opens on its result,
    "Here is the result: ...", then its scope; no welcome, no number."""
    assert ts.INTRO_SAID == ("Welcome, guys, to my new channel! My name is the owner, and "
                             "welcome to ATLAS Academy.")
    assert "the owner · ATLAS Academy" in design.intro()
    welcome = ts.series("channel").episodes[0]
    assert ts.opener_said(welcome) == ts.INTRO_SAID
    for s in ts.SERIES:
        for e in s.episodes:
            line = ts.opener_said(e)
            if s.key == "channel":
                assert line == ts.INTRO_SAID and not e.opener_topic
                continue
            assert e.opener_topic == ts.OPENER_TOPICS[(s.key, e.number)]
            assert 3 <= len(e.opener_topic.split()) <= 16, e.opener_topic
            assert ts.topic_line(e) == ""            # the topic is said once
            if not e.scripted:
                continue
            assert line.startswith("Here is the result"), line
            low = line.lower()
            assert not [w for w in ts.WELCOME_WORDS if w in low], line
            assert "minutes" not in low and "owner" not in low
    # 2026-10-17: the narrated ending ("Thank you guys for watching ...") went;
    # every video ends on its EndCard
    assert not hasattr(ts, "OUTRO_SAID") and not hasattr(design, "outro")


# ------------------------------------------------------------------ design

def test_every_slide_carries_the_store_and_no_ai_label():
    """The AI label is absent: the owner 2026-10-17: off screen; disclosed via containsSyntheticMedia + description."""
    ep = N8N.episode(1)
    for c, sc in enumerate(ep.scenes):
        for k in range(len(sc.said)):
            doc = design.slide(sc, k, mark=N8N.mark, chapter=c, chapters=len(ep.scenes))
            assert design.TAG not in doc and design.STORE_SHOWN in doc


def test_a_list_slide_reveals_one_more_line_per_step():
    sc = next(s for s in N8N.episode(1).scenes if s.kind == "steps")
    first = design.slide(sc, 0, mark="m", chapter=0, chapters=1)
    assert first.count('class="hidden"') == len(sc.items) - 1
    last = design.slide(sc, len(sc.items) - 1, mark="m", chapter=0, chapters=1)
    assert 'class="hidden"' not in last


def test_a_preview_is_bannered_and_a_real_render_is_not():
    sc = N8N.episode(1).scenes[0]
    assert "placeholder audio" in design.slide(sc, 0, mark="m", chapter=0, chapters=1,
                                               preview=True)
    assert "placeholder audio" not in design.slide(sc, 0, mark="m", chapter=0, chapters=1)


def test_thumbnails_are_1280x720_and_differ_by_series():
    a = design.thumbnail("Your first\nn8n workflow", "n8n", N8N.mark, 1)
    b = design.thumbnail("What is\nMCP?", "claude", MCP.mark, 1)
    assert "1280px" in a and "720px" in a and a != b


# --------------------------------------------------- description, chapters

CH = [(0.0, "Intro"), (12.0, "One"), (60.0, "Two"), (300.0, "Thanks for watching")]


def test_description_has_summary_chapters_links_store_and_label():
    s, ep = N8N, N8N.episode(1)
    text = tut.description(s, ep, CH, 6)
    assert text.startswith("What you'll have: your first workflow.\nWhat you need: ")
    assert f"\n\nLevel 1 · Beginner · 6 minutes\n{ep.summary}" in text
    assert "\n#n8n " in text                                   # the product first
    assert "0:00 Intro\n0:12 One\n1:00 Two\n5:00 Thanks for watching" in text
    assert text.count(ts.STORE_URL) >= 2 and tut.description_refusal(text) is None
    assert ep.links[0][1] in text and s.playlist in text


def test_chapters_follow_youtubes_rules():
    assert tut.chapters_refusal(CH, 320.0) is None
    assert "three" in tut.chapters_refusal(CH[:2], 320.0)
    assert "0:00" in tut.chapters_refusal([(1.0, "a")] + CH[1:], 320.0)
    assert "under 10" in tut.chapters_refusal([(0.0, "a"), (5.0, "b"), (60.0, "c")], 320.0)


def test_an_overlong_description_is_refused_never_cut():
    assert "over 5000" in tut.description_refusal("x" * 5001)
    assert "agency.example.com" in tut.description_refusal(f"x\n\n{tut.AI_LABEL}")


def test_every_description_discloses_the_synthetic_voice_and_passes_its_gates():
    """the owner 2026-10-17 (docs/academy-video-system/narration.md): the British
    male AI voice stays, disclosed in every description, just before the
    AI label, outside the chapters block."""
    for s in ts.SERIES:
        for ep in s.episodes:
            text = tut.description(s, ep, CH, 6)
            assert tut.VOICE_LINE in text
            lines = text.rstrip().splitlines()
            assert lines[-1] == tut.AI_LABEL and lines[-2] == tut.VOICE_LINE
            assert "0:00 Intro\n0:12 One" in text
            assert tut.description_refusal(text) is None
    assert "synthetic voice" in tut.VOICE_LINE.lower()


def test_a_description_without_the_voice_line_is_refused():
    text = tut.description(N8N, N8N.episode(1), CH, 6)
    bare = text.replace(tut.VOICE_LINE + "\n", "")
    assert tut.VOICE_LINE not in bare
    assert "synthetic voice" in tut.description_refusal(bare)


# ------------------------------------------------ intro/outro reuse, cache

def test_the_cache_key_follows_words_voice_and_design(monkeypatch):
    monkeypatch.setenv("REELS_FISH_VOICE_ID", "v1")
    a = tut.cache_key("intro", ts.INTRO_SAID, False)
    assert a != tut.cache_key("intro", ts.INTRO_SAID, False, shown="topic|x")
    assert a == tut.cache_key("intro", ts.INTRO_SAID, False)
    assert a != tut.cache_key("intro", ts.INTRO_SAID + "!", False)
    assert a != tut.cache_key("intro", ts.INTRO_SAID, True)
    monkeypatch.setenv("REELS_FISH_VOICE_ID", "v2")
    assert a != tut.cache_key("intro", ts.INTRO_SAID, False)
    b = tut.cache_key("intro", ts.INTRO_SAID, False)
    monkeypatch.setenv("ACADEMY_TUTORIALS_VOICE_SPEED", "1.0")
    assert b != tut.cache_key("intro", ts.INTRO_SAID, False)   # a slower voice: new clip
    key = tut.script_key(N8N.episode(1))
    monkeypatch.setenv("ACADEMY_TUTORIALS_VOICE_SPEED", "0.85")
    assert key != tut.script_key(N8N.episode(1))                # and a new render


async def test_the_intro_is_rendered_once_and_reused(monkeypatch, tmp_path):
    """The channel welcome's narrated intro is the one fixed clip left
    (2026-10-17): cached, over the mark idling, no sting, no watermark."""
    from atlas.modules.content import ffmpeg
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    made, spaces = [], []

    async def segment(shooter, doc, said, base, preview, **space):
        made.append(said)
        spaces.append(space)
        Path(base + ".mp4").write_bytes(b"mp4")
        return base + ".mp4", 11.0, "fish", None

    async def duration(path):
        return 11.0
    monkeypatch.setattr(tut, "_segment", segment)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    first = await tut.fixed_clip("intro", shooter=None)
    again = await tut.fixed_clip("intro", shooter=None)
    assert first == again and first[2] is None
    assert made == [f"{ts.INTRO_CUE} {ts.INTRO_SAID}"]
    # (2026-10-15) its words rise in as the guide's title fade + rise
    assert spaces == [{"bg": str(design.INTRO_LOOP), "watermark": False,
                       "spec": design.INTRO_MOTION}]
    _p, _s, why = await tut.fixed_clip("outro", shooter=None)
    assert why and "end card" in why                  # no fixed ending any more


# ------------------------------------------------- what may ever be uploaded

def _episode(tmp_path, monkeypatch, audio: str) -> Path:
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    d = tut.episode_dir(N8N, N8N.episode(1))
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"x")
    m = tut.Manifest("n8n", 1, audio, 300.0, CH)
    (d / "manifest.json").write_text(json.dumps(m.__dict__))
    return d


def test_a_fish_render_is_publishable(tmp_path, monkeypatch):
    d = _episode(tmp_path, monkeypatch, "fish")
    assert tut.publishable_render(str(d / "video.mp4")) is None


def test_a_placeholder_preview_is_never_publishable(tmp_path, monkeypatch):
    d = _episode(tmp_path, monkeypatch, "placeholder")
    assert "placeholder" in tut.publishable_render(str(d / "video.mp4"))
    (d / "video.PREVIEW.mp4").write_bytes(b"x")
    assert tut.publishable_render(str(d / "video.PREVIEW.mp4")) is not None


def test_a_symlink_or_a_file_elsewhere_is_refused(tmp_path, monkeypatch):
    d = _episode(tmp_path, monkeypatch, "fish")
    link = d.parent / "video.mp4"
    link.symlink_to(d / "video.mp4")
    assert tut.publishable_render(str(link)) is not None
    other = tmp_path / "x" / "y"
    other.mkdir(parents=True)
    (other / "video.mp4").write_bytes(b"x")
    assert tut.publishable_render(str(other / "video.mp4")) is not None


async def test_the_clips_channel_upload_refuses_a_preview(tmp_path, monkeypatch):
    d = _episode(tmp_path, monkeypatch, "placeholder")
    called = []

    async def token(channel):
        called.append(channel)
        return "t", None
    monkeypatch.setattr(api, "token", token)
    data, why = await api.upload("clips", str(d / "video.mp4"), "t", "d", [])
    assert data is None and "placeholder" in why and called == []


# ------------------------------------------------------- playlist calls

@pytest.fixture
def google(monkeypatch):
    calls = []

    async def token(channel):
        return "tok", None

    async def get_json(url, *, headers=None, params=None, **k):
        calls.append(("GET", url, params, None))
        return {"items": [{"id": "PLx", "snippet": {"title": "n8n Workflows from Zero"},
                           "status": {"privacyStatus": "public"}}]}, None

    async def post_json(url, *, headers=None, params=None, json_body=None, **k):
        calls.append(("POST", url, params, json_body))
        return {"id": "PLnew" if url.endswith("/playlists") else "ITEM1"}, None

    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "get_json", get_json)
    monkeypatch.setattr(api, "post_json", post_json)
    return calls


async def test_find_playlist_by_exact_title(google):
    assert await api.find_playlist("clips", "n8n Workflows from Zero") == (
        {"id": "PLx", "privacy": "public"}, None)
    assert await api.find_playlist("clips", "Claude & MCP Explained") == ({}, None)
    assert google[0][2]["mine"] == "true"


async def test_create_playlist_and_add_an_item(google):
    pl, why = await api.create_playlist("clips", "Claude & MCP Explained", "d")
    assert why is None and pl == {"id": "PLnew", "privacy": "public"}
    item, why = await api.add_playlist_item("clips", "PLnew", "abcdefghijk")
    assert why is None and item["item_id"] == "ITEM1"
    body = google[-1][3]["snippet"]
    assert body == {"playlistId": "PLnew",
                    "resourceId": {"kind": "youtube#video", "videoId": "abcdefghijk"}}


async def test_a_refused_scope_is_reported_not_succeeded(monkeypatch):
    async def token(channel):
        return "tok", None

    async def post_json(url, **k):
        return None, "403 authenticated but not permitted"
    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "post_json", post_json)
    item, why = await api.add_playlist_item("clips", "PL", "abcdefghijk")
    assert item is None and "403" in why


async def test_a_thumbnail_must_be_an_image_under_the_limit(tmp_path):
    big = tmp_path / "t.jpg"
    big.write_bytes(b"x" * (api.THUMBNAIL_MAX_BYTES + 1))
    assert "bytes" in (await api.set_thumbnail("clips", "abcdefghijk", str(big)))[1]
    assert "png" in (await api.set_thumbnail("clips", "abcdefghijk", str(tmp_path / "t.gif")))[1]


async def test_token_scopes_are_read_from_a_form_post_never_the_url(monkeypatch):
    seen = {}

    async def token(channel):
        return "tok", None

    async def post_json(url, *, data=None, params=None, **k):
        seen.update(url=url, data=data, params=params)
        return {"scope": "https://www.googleapis.com/auth/youtube openid"}, None
    monkeypatch.setattr(api, "token", token)
    monkeypatch.setattr(api, "post_json", post_json)
    scopes, why = await api.token_scopes("clips")
    assert why is None and "https://www.googleapis.com/auth/youtube" in scopes
    assert seen["data"] == {"access_token": "tok"} and not seen["params"]


def test_no_delete_call_for_a_video_or_a_playlist_exists():
    names = [n for n in dir(api) if not n.startswith("_")]
    assert not [n for n in names if "delete" in n.lower()]


# ------------------------------------------------------- tiers, closed params

def _actions():
    from atlas.modules.academy import AcademyModule
    return {a.name: a for a in AcademyModule().actions()}


def test_tiers_mirror_the_reel():
    a = _actions()
    assert a["academy.render_tutorial"].tier is Tier.AUTO
    assert a["academy.autopublish_tutorial"].tier is Tier.AUTO
    assert a["academy.publish_tutorial"].tier is Tier.ASK
    assert a["academy.add_to_series_playlist"].tier is Tier.ASK
    for name in ("academy.render_tutorial", "academy.autopublish_tutorial",
                 "academy.publish_tutorial", "academy.add_to_series_playlist"):
        assert a[name].dry_run is not None and a[name].rollback is not None
        assert denial_reason(name) is None
    assert set(a["academy.autopublish_tutorial"].params) == {"series", "episode"}
    # One run of the daily tick posts one per series: the executor's hourly
    # ceiling must not refuse the second (2026-10-15).
    assert a["academy.autopublish_tutorial"].max_per_hour == tut.PER_DAY_DEFAULT


def test_params_are_closed_to_the_catalog():
    assert tut.vet({"series": "n8n", "episode": 1}) is None
    assert "no tutorial series" in tut.vet({"series": "crypto", "episode": 1})
    assert "no episode 9" in tut.vet({"series": "n8n", "episode": 9})
    assert "number" in tut.vet({"series": "n8n", "episode": "one; rm -rf"})
    assert tut.vet_video({"series": "n8n", "episode": 1, "video_id": "abcdefghijk"}) is None
    assert "11-character" in tut.vet_video({"series": "n8n", "episode": 1,
                                            "video_id": "x' OR 1=1"})


def test_a_level_0_episode_numbered_0_passes_the_real_param_checks():
    """Episode 0 is falsy: the executor's own checks must still take it."""
    from atlas.kernel import executor
    params = {"series": "claude", "episode": 0}
    for name in ("academy.render_tutorial", "academy.publish_tutorial",
                 "academy.autopublish_tutorial"):
        action = _actions()[name]
        assert executor.missing_params(action, params) == [], name
        assert executor._validate_params(action, params) is None, name
    assert tut.vet(params) is None
    assert "no episode 0" in tut.vet({"series": "agents", "episode": 0})


# --------------------------------------------------------- gate and posting

async def test_autopublish_holds_and_cards_when_a_check_fails(monkeypatch):
    from atlas.kernel import approvals
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPOST", "off")
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")   # the Fish path
    cards = []

    async def fresh_card(action, params, reason, *, dedup_key, **kw):
        cards.append((action, params, dedup_key))
        return 77
    monkeypatch.setattr(approvals, "fresh_card", fresh_card)

    async def never(*a):
        raise AssertionError("published")
    monkeypatch.setattr(tut, "publish_tutorial", never)
    result = await tut.autopublish_tutorial("n8n", 1)
    assert result.ok and "card #77" in result.detail
    assert cards == [("academy.publish_tutorial", {"series": "n8n", "episode": 1},
                      "academy.tutorial.n8n.1")]


def test_posting_by_itself_is_on_unless_autopublish_says_off(monkeypatch):
    """the owner, 2026-10-15: tutorials go public by themselves once every gate
    passes; ACADEMY_TUTORIALS_AUTOPUBLISH=off brings back the review card."""
    monkeypatch.delenv("ACADEMY_TUTORIALS_AUTOPUBLISH", raising=False)
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPOST", "on")
    assert tut.autopublish_enabled()
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "off")
    assert not tut.autopublish_enabled()
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "on")
    assert tut.autopublish_enabled()
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPOST", "off")      # an off-switch only
    assert not tut.autopublish_enabled()


def test_the_automatic_cap_is_one_per_series(monkeypatch):
    """the owner 2026-10-11: one video for each series each day -- the global
    automatic cap is never lower than the number of series."""
    monkeypatch.delenv("ACADEMY_TUTORIALS_PER_DAY", raising=False)
    # the owner's cap stays seven (rule 23); the three 2026-10-18 series have no
    # script yet, so every series that can post still fits under it.
    can_post = [s for s in ts.SERIES if any(e.scripted for e in s.episodes)]
    assert tut.per_day() == 7 >= len(can_post)
    assert tut.per_series_daily() == 1


async def test_autopublish_honours_the_daily_cap(monkeypatch):
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "on")

    monkeypatch.delenv("ACADEMY_TUTORIALS_PER_DAY", raising=False)

    async def seven():
        return 7
    monkeypatch.setattr(tut, "posted_today", seven)
    assert "already posted today" in await tut.autopublish_check("n8n", 1)


@pytest.fixture
def youtube(monkeypatch, tmp_path):
    """Every Google call mocked; `state` controls failures and the row."""
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")   # the Fish path
    from atlas.kernel import idempotency
    (tmp_path / "thumb.jpg").write_bytes(b"j")
    m = tut.Manifest("n8n", 1, "fish", 300.0, CH)
    state = {"steps": [], "row": None, "thumb_ok": True, "written": [],
             "scopes": {"https://www.googleapis.com/auth/youtube", FORCE_SSL},
             "manifest": m}

    async def ready(series, episode):
        return (N8N, N8N.episode(1), tmp_path, m, "desc"), None

    async def posted(*a):
        return state["row"]

    async def upload(ch, path, title, text, tags, privacy="private", **kw):
        state["steps"].append(("upload", privacy))
        state["upload_kw"] = kw
        return {"video_id": "abcdefghijk"}, None

    async def set_thumbnail(ch, vid, path):
        state["steps"].append(("thumbnail", vid))
        return ({"items": []}, None) if state["thumb_ok"] else (None, "403 forbidden")

    async def find_playlist(ch, title):
        state["steps"].append(("find", title))
        return {}, None

    async def create_playlist(ch, title, desc):
        state["steps"].append(("create", title))
        return {"id": "PLnew", "privacy": "public"}, None

    async def add_playlist_item(ch, pl, vid):
        state["steps"].append(("add", pl))
        return {"item_id": "IT"}, None

    async def set_privacy(ch, vid, privacy, **kw):
        state["steps"].append(("privacy", privacy))
        state.setdefault("privacy_kw", []).append(kw)
        return {}, None

    async def token_scopes(ch):
        return state["scopes"], None

    async def claim(action, key):
        return idempotency.CLAIMED

    async def settle(*a, **k):
        return None

    async def fetch_one(sql, *args):
        state["written"].append(("insert", args))
        return {"id": 5}

    async def execute_sql(sql, *args):
        state["written"].append(("update", args))

    async def zero():
        return 0
    monkeypatch.setattr(tut, "ready", ready)
    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(tut, "posted_today", zero)
    for name, fn in (("upload", upload), ("set_thumbnail", set_thumbnail),
                     ("find_playlist", find_playlist), ("create_playlist", create_playlist),
                     ("add_playlist_item", add_playlist_item), ("set_privacy", set_privacy),
                     ("token_scopes", token_scopes)):
        monkeypatch.setattr(api, name, fn)
    monkeypatch.setattr(idempotency, "claim", claim)
    monkeypatch.setattr(idempotency, "settle", settle)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    return state


async def test_publish_uploads_private_then_thumbnail_playlist_public(youtube):
    result = await tut.publish_tutorial("n8n", 1)
    assert result.ok, result.detail
    assert youtube["steps"] == [
        ("upload", "private"), ("thumbnail", "abcdefghijk"),
        ("find", "n8n Workflows from Zero"), ("create", "n8n Workflows from Zero"),
        ("add", "PLnew"), ("privacy", "public")]
    undo = json.loads(result.rollback_token)
    assert undo["item_id"] == "IT" and undo["created_playlist"]["id"] == "PLnew"
    assert youtube["written"][0] == ("insert", ("n8n", 1, "abcdefghijk"))
    assert youtube["written"][-1][1][4] is True          # the row is marked public


async def test_publish_uploads_as_education_in_british_english(youtube):
    result = await tut.publish_tutorial("n8n", 1)
    assert result.ok, result.detail
    assert youtube["upload_kw"] == {"category_id": api.EDUCATION,
                                    "language": api.TUTORIAL_LANGUAGE,
                                    "synthetic": True}


async def test_publish_declares_synthetic_media_on_upload_and_going_public(youtube):
    """the owner 2026-10-17: status.containsSyntheticMedia on the upload, and the
    private -> public update sends it again."""
    result = await tut.publish_tutorial("n8n", 1)
    assert result.ok, result.detail
    assert youtube["upload_kw"]["synthetic"] is True
    assert youtube["privacy_kw"] and all(kw == {"synthetic": True}
                                         for kw in youtube["privacy_kw"])


@pytest.mark.parametrize("fresh", [False, True])
async def test_a_timeout_on_the_last_chunk_keeps_the_upload_claim(
        youtube, monkeypatch, tmp_path, fresh):
    """Final review I1: through the REAL api.upload, a timeout on the final
    PUT reaches idempotency.finish as a reason uncertain() keeps, so the
    next run never uploads a second copy YouTube may already hold."""
    import httpx

    from atlas.kernel import idempotency
    (tmp_path / "video.mp4").write_bytes(b"v" * 10)
    finished = []

    async def finish(action, key, reason, detail=""):
        finished.append((key, reason))

    async def fake_token(ch):
        return "tok", None

    async def rows(series, episode):
        return [{"id": 7}]

    class Init:
        status_code = 200

        @property
        def headers(self) -> dict:
            return {"Location": "https://upload.example/s"}

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, **kw):
            return Init()

        async def put(self, url, **kw):
            raise httpx.ReadTimeout("slow", request=httpx.Request("PUT", url))
    monkeypatch.setattr(api, "upload", REAL_UPLOAD)
    monkeypatch.setattr(api, "token", fake_token)
    monkeypatch.setattr(api, "clips_path_refusal", lambda path: None)
    monkeypatch.setattr(api.httpx, "AsyncClient", Client)
    monkeypatch.setattr(idempotency, "finish", finish)
    monkeypatch.setattr(tut, "_episode_rows", rows)
    result = await tut._publish("n8n", 1, strict=False, fresh=fresh)
    assert not result.ok
    ((key, reason),) = finished
    assert idempotency.uncertain(reason), reason
    assert key.endswith(":fresh:7") == fresh


async def test_the_automatic_post_stays_private_without_its_thumbnail(youtube, monkeypatch):
    from atlas.kernel import approvals
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "on")
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")   # the Fish path
    youtube["thumb_ok"] = False
    cards = []

    async def fresh_card(action, params, reason, *, dedup_key, **kw):
        cards.append(reason)
        return 9
    monkeypatch.setattr(approvals, "fresh_card", fresh_card)
    result = await tut.autopublish_tutorial("n8n", 1)
    assert "card #9" in result.detail and "thumbnail NOT set" in cards[0]
    assert ("privacy", "public") not in youtube["steps"]
    assert youtube["written"][-1][1][4] is False          # recorded, still private


async def test_the_card_finishes_the_same_upload_never_a_second(youtube):
    youtube["row"] = {"id": 5, "video_id": "abcdefghijk", "playlist_id": "PLx",
                      "item_id": "IT", "thumb_set": False, "public": False}
    result = await tut.publish_tutorial("n8n", 1)
    assert result.ok
    assert youtube["steps"] == [("thumbnail", "abcdefghijk"), ("privacy", "public")]


async def test_a_token_without_the_playlist_scope_holds(youtube, monkeypatch):
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "on")
    youtube["scopes"] = {"https://www.googleapis.com/auth/youtube.upload"}
    why = await tut.autopublish_check("n8n", 1)
    assert "re-authorise YOUTUBE_CLIPS_REFRESH_TOKEN" in why


async def test_rollback_makes_private_and_takes_out_never_deletes(monkeypatch):
    done = []

    async def set_privacy(ch, vid, privacy, **kw):
        done.append(("video", privacy))
        return {}, None

    async def take_out(ch, item):
        done.append(("out", item))
        return True, None

    async def pl_private(ch, pid, title, privacy):
        done.append(("playlist", privacy))
        return {}, None

    async def execute_sql(sql, *args):
        done.append(("row", args))
    monkeypatch.setattr(api, "set_privacy", set_privacy)
    monkeypatch.setattr(api, "take_out_of_playlist", take_out)
    monkeypatch.setattr(api, "set_playlist_privacy", pl_private)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    token = json.dumps({"channel": "clips", "video_id": "abcdefghijk", "item_id": "IT",
                        "created_playlist": {"id": "PL", "title": "t"}})
    result = await tut.unpublish_tutorial(token)
    assert result.ok and "thumbnail stays" in result.detail
    assert done[:3] == [("video", "private"), ("out", "IT"), ("playlist", "private")]




# ---------------------------------------------------------- the daily tick

def test_the_preview_order_is_the_welcome_then_by_level_round_robin():
    assert tut.order()[:9] == [("channel", 1), ("claude", 0), ("n8n", 0), ("project", 0),
                               ("business", 0), ("agents", 1), ("local", 0),
                               ("explained", 1), ("explained", 2)]
    level = {(s.key, e.number): e.level for s in ts.SERIES for e in s.episodes}
    assert sorted(tut.order()) == sorted(level)             # every episode, once


def test_each_series_climbs_its_own_ladder_in_strict_order():
    claude = ts.series("claude")
    assert tut.next_in_series(claude, set()) == (0, None)
    assert tut.next_in_series(claude, {("claude", 0)}) == (1, None)
    agents = ts.series("agents")
    assert tut.next_in_series(agents, set()) == (1, None)
    # never past an unscripted lower episode, even when a later one is scripted
    planned = next((s, e) for s in ts.SERIES for e in tut.ladder(s) if not e.scripted)
    s, ep = planned
    done = {(s.key, e.number) for e in tut.ladder(s) if e is not ep and
            tut.ladder(s).index(e) < tut.ladder(s).index(ep)}
    assert tut.next_in_series(s, done) == (None, f"script not written for {s.key} #{ep.number}")
    every = {(x.key, e.number) for x in ts.SERIES for e in x.episodes}
    assert tut.next_in_series(agents, every) == (None, None)


def test_todays_lineup_is_one_per_series():
    """2026-10-11: the welcome and Claude #0 are public."""
    todo, notes = tut.lineup({("channel", 1), ("claude", 0)}, {})
    assert todo == [("claude", 1), ("n8n", 0), ("project", 0), ("business", 0),
                    ("agents", 1), ("local", 0)]
    # the 2026-10-18 series: nothing scripted yet, security held to its first
    # recording, the opinion series' only writable lesson is #6
    assert notes == NEW_NOTES


def test_a_series_that_drafted_today_waits_for_tomorrow(monkeypatch):
    todo, _ = tut.lineup({("channel", 1), ("claude", 0)}, {"claude": 1, "n8n": 1})
    assert ("claude", 1) not in todo and ("n8n", 0) not in todo
    assert todo[0] == ("project", 0)
    monkeypatch.setenv("ACADEMY_TUTORIALS_PER_SERIES_DAILY", "2")
    todo, _ = tut.lineup({("channel", 1), ("claude", 0)}, {})
    assert [x for x in todo if x[0] == "n8n"] == [("n8n", 0), ("n8n", 1)]


def _future_scripted(monkeypatch):
    """Every future episode writable, so the monthly rule is what decides."""
    real = tut.next_in_series

    def nxt(s, done):
        if s.key != "future":
            return real(s, done)
        return next((e.number for e in tut.ladder(s) if (s.key, e.number) not in done),
                    None), None
    monkeypatch.setattr(tut, "next_in_series", nxt)


def test_where_ai_is_going_takes_one_episode_a_calendar_month(monkeypatch):
    """the owner, 2026-10-18: future posts monthly; the other series stay daily
    and the daily cap stays 7."""
    _future_scripted(monkeypatch)
    assert tut.MONTHLY_SERIES == ("future",) and tut.PER_DAY_DEFAULT == 7
    done = {("channel", 1), ("claude", 0)}
    todo, _ = tut.lineup(done, {}, None, {})
    assert [x for x in todo if x[0] == "future"] == [("future", 1)]
    todo, _ = tut.lineup(done, {}, None, {"future": 1, "claude": 4})
    assert not [x for x in todo if x[0] == "future"]
    assert ("claude", 1) in todo                    # a daily series is never held
    monkeypatch.setenv("ACADEMY_TUTORIALS_PER_SERIES_DAILY", "3")
    todo, _ = tut.lineup(done, {}, None, {})
    assert [x for x in todo if x[0] == "future"] == [("future", 1)]
    assert len([x for x in todo if x[0] == "n8n"]) == 3


async def test_the_month_count_reads_public_posts_of_this_calendar_month(tick, monkeypatch):
    from datetime import timedelta
    now = datetime.now(UTC)
    last_month = now.replace(day=1) - timedelta(days=1)
    tick["rows"] = [
        {"kind": "post", "series": "future", "episode": 1, "at": last_month,
         "public": True},
        {"kind": "post", "series": "claude", "episode": 1, "at": now, "public": True},
        {"kind": "post", "series": "claude", "episode": 2, "at": now, "public": True,
         "withdrawn": True},
        {"kind": "post", "series": "n8n", "episode": 1, "at": now, "public": False},
    ]
    _done, _today, _last, month = await tut._done()
    assert month == {"claude": 1}                  # last month, withdrawn, private: no
    tick["rows"].append({"kind": "post", "series": "future", "episode": 2,
                         "at": now, "public": True})
    _done, _today, _last, month = await tut._done()
    assert month["future"] == 1
    _future_scripted(monkeypatch)
    todo, _ = tut.lineup(*(await tut._done()))
    assert not [x for x in todo if x[0] == "future"]


def test_a_monthly_runway_counts_calendar_months_not_days():
    at = datetime(2026, 10, 18, 9, tzinfo=UTC)
    assert tut.monthly_runway_days(0, False, at) == 0          # due now, unscripted
    assert tut.monthly_runway_days(0, True, at) == 14          # posted: to 1 Nov
    assert tut.monthly_runway_days(1, False, at) == 14         # October's script
    assert tut.monthly_runway_days(1, True, at) == 14 + 30     # November's
    assert tut.monthly_runway_days(3, True, at) == (datetime(2027, 2, 1, tzinfo=UTC)
                                                    - at).days + 1
    dec = datetime(2026, 12, 31, tzinfo=UTC)
    assert tut.monthly_runway_days(1, True, dec) == 32          # through January
    # one script a month is never short
    assert tut.monthly_runway_days(1, True, at) >= tut.RUNWAY_WARN_DAYS


def test_the_series_posted_longest_ago_goes_first():
    """2026-10-16: a fixed catalog order made the same series lose the
    hourly render ceiling every day. Never posted first, then the oldest
    last public post; ties keep catalog order."""
    from datetime import timedelta
    now = datetime.now(UTC)
    last = {"channel": now - timedelta(days=5), "claude": now - timedelta(days=1),
            "n8n": now - timedelta(days=1), "project": now - timedelta(days=2),
            "business": now - timedelta(days=1)}
    todo, _ = tut.lineup({("channel", 1), ("claude", 0)}, {}, last)
    assert todo == [("agents", 1), ("local", 0), ("project", 0), ("claude", 1),
                    ("n8n", 0), ("business", 0)]


async def test_a_series_that_lost_yesterday_comes_first_today(tick):
    from datetime import timedelta
    now = datetime.now(UTC)
    yesterday, before = now - timedelta(days=1), now - timedelta(days=2)
    # Every series posted yesterday but local and agents, which last posted
    # the day before: they lost yesterday's ceiling.
    tick["rows"] = [{"kind": "post", "series": "channel", "episode": 1,
                     "at": before, "public": True}]
    for s in ts.SERIES:
        if not s.playlist:
            continue
        first = tut.ladder(s)[0].number
        at = before if s.key in ("local", "agents") else yesterday
        tick["rows"].append({"kind": "post", "series": s.key, "episode": first,
                             "at": at, "public": True})
    await tut.daily()
    assert [k for k, _ in tick["reviewed"]][:2] == ["agents", "local"]


async def test_a_private_or_withdrawn_upload_is_not_a_last_post(tick):
    from datetime import timedelta
    now = datetime.now(UTC)
    tick["rows"] = [{"kind": "post", "series": s.key, "episode": tut.ladder(s)[0].number,
                     "at": now - timedelta(days=1), "public": s.key != "n8n"}
                    for s in ts.SERIES]
    tick["rows"].append({"kind": "post", "series": "n8n", "episode": 1,
                         "at": now - timedelta(days=3), "public": True})
    tick["rows"].append({"kind": "draft", "series": "claude", "episode": 1,
                         "at": now - timedelta(days=9)})
    tick["rows"].append({"kind": "post", "series": "n8n", "episode": 2, "at": now,
                         "public": True, "withdrawn": True})
    _done, _today, last, _month = await tut._done()
    assert last["n8n"] == now - timedelta(days=3)          # withdrawn: not a last post
    assert last["claude"] == now - timedelta(days=1)


NEW_NOTES = ["script not written for explained #1",
             ts.held("security", ts.series("security").episode(1)),
             "script not written for future #6"]


def test_every_series_is_scripted_two_days_ahead():
    """the owner, 2026-10-11: one a day per series, so today's and tomorrow's
    rung of every ladder must already have a script. The 2026-10-18 series
    start empty: the writer stocks them (docs/academy-script-writer.md)."""
    done = {("channel", 1), ("claude", 0)}
    for _day in range(2):            # today and tomorrow, every series
        todo, notes = tut.lineup(done, {})
        assert notes == NEW_NOTES, notes
        assert {k for k, _ in todo} == {s.key for s in ts.SERIES
                                        if s.playlist and s.key not in NEW}
        done |= set(todo)


async def test_a_planned_episode_never_renders():
    planned = next((s, e) for s in ts.SERIES for e in s.episodes if not e.scripted)
    _d, _m, why = await tut.render(planned[0].key, planned[1].number, preview=True)
    assert why.startswith(f"script not written for {planned[0].key} #{planned[1].number}")


def test_the_daily_tick_is_registered():
    from atlas.kernel import scheduler
    assert callable(scheduler._maybe_academy_tutorial)


@pytest.fixture
def sched(monkeypatch):
    """The scheduler's tutorial step with a settable clock and a recording
    spawn. A spawned run is 'done' unless the test says it is running."""
    from atlas.kernel import scheduler
    state = {"now": datetime(2026, 10, 16, 16, 0, tzinfo=UTC), "spawned": [],
             "running": False, "due": False}

    class Task:
        def done(self):
            return not state["running"]

    def spawn(coro, name):
        coro.close()
        state["spawned"].append(name)
        return Task()
    monkeypatch.setattr(scheduler, "_now", lambda: state["now"])
    monkeypatch.setattr(scheduler.background, "spawn", spawn)
    monkeypatch.setattr(scheduler, "_academy_tutorial_task", None)
    monkeypatch.setattr(scheduler, "_last_academy_tutorial_date", None)
    monkeypatch.setattr(tut, "enabled", lambda: True)
    monkeypatch.setattr(tut, "hour_utc", lambda: 17)
    monkeypatch.setattr(tut, "retry_due", lambda now: state["due"])
    return state


async def test_the_tick_runs_once_from_the_hour_then_only_to_catch_up(sched):
    from datetime import timedelta

    from atlas.kernel import scheduler
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"] == []                            # before the hour
    sched["now"] += timedelta(hours=1)
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"] == ["the daily Academy tutorial"]
    sched["now"] += timedelta(hours=1)
    await scheduler._maybe_academy_tutorial()
    assert len(sched["spawned"]) == 1                        # nothing held: no rerun
    sched["due"] = True
    sched["running"] = True
    await scheduler._maybe_academy_tutorial()
    assert len(sched["spawned"]) == 1                        # one run at a time
    sched["running"] = False
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"][1] == "the Academy tutorials the hourly ceiling held"
    sched["due"] = False
    sched["now"] += timedelta(days=1)
    await scheduler._maybe_academy_tutorial()
    assert sched["spawned"][2] == "the daily Academy tutorial"   # a new day: the full run


@pytest.fixture
def tick(monkeypatch, tmp_path):
    from atlas.kernel import executor
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    # The review-card path: posting by itself is the default since 2026-10-15.
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "off")
    # The Fish render path these tests drive is the fallback since 2026-10-19
    # (lessons come from NotebookLM: tests/test_academy_notebooklm.py).
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    state = {"rows": [], "seen": [], "reviewed": [], "told": []}

    async def rows(sql, *a):
        return state["rows"]

    async def execute(req):
        state["seen"].append((req.action, req.params))
        return ActionResult(True, "ok")

    async def review(key, n):
        state["reviewed"].append((key, n))
        return "card #1 in Slack"

    async def tell(text):
        state["told"].append(text)

    async def unpinned():
        return state["unpinned"]

    async def pinned_today():
        return state["pinned_today"]
    state["unpinned"], state["pinned_today"] = [], 0
    async def untaught():
        return state["untaught"]

    async def promo_today():
        return state["promo_today"]

    async def rendered(slug):
        return state["rendered"].get(slug)

    async def posted(series, episode):
        return state["posted_row"]
    state["untaught"], state["promo_today"], state["rendered"] = [], 0, {}
    state["posted_row"] = {"video_id": "iRyhxkLk-Yo", "public": True}
    from atlas.modules.clips import promo
    monkeypatch.setattr(promo, "posted_today", promo_today)
    monkeypatch.setattr(tut, "rendered_teaching", rendered)
    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(tut, "unpinned", unpinned)
    monkeypatch.setattr(tut, "pinned_today", pinned_today)
    monkeypatch.setattr(tut, "untaught", untaught)
    monkeypatch.setattr(tut, "teaching_day", lambda now: True)

    async def skip_teaching(slug, why):
        state["skips"].append((slug, why))
    state["skips"] = []
    monkeypatch.setattr(tut, "skip_teaching", skip_teaching)

    async def token_scopes(ch):
        state["scope_reads"] += 1
        return state["scopes"]
    state["scopes"] = ({FORCE_SSL}, None)
    state["scope_reads"] = 0
    monkeypatch.setattr(api, "token_scopes", token_scopes)
    monkeypatch.setattr("atlas.kernel.state.fetch_all", rows)
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(tut, "review_tutorial", review)
    monkeypatch.setattr(tut, "_tell", tell)
    # The deferral clock: noon, so an hour later is still the same UTC day.
    state["noon"] = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(tut, "_clock", lambda: state["noon"])
    monkeypatch.setattr(tut, "_deferred", None)
    # the owner 2026-10-17: the AI flag on the Shorts, recorded, never run here.
    from atlas.modules.academy import synthetic
    state["flagged_after"], state["catch_ups"] = [], 0

    async def after_post(slug):
        state["flagged_after"].append(slug)
        return state.get("after_said", "")

    async def catch_up():
        state["catch_ups"] += 1
        return state.get("catch_up_said", "")
    monkeypatch.setattr(synthetic, "after_post", after_post)
    monkeypatch.setattr(synthetic, "catch_up", catch_up)
    # the owner 2026-10-18: the metadata refresh, recorded, never run here.
    from atlas.modules.academy import metadata
    state["refreshes"] = 0

    async def refresh():
        state["refreshes"] += 1
        return ""
    monkeypatch.setattr(metadata, "catch_up", refresh)
    return state


async def test_a_fresh_channel_drafts_the_welcome_and_each_series_first(tick):
    said = await tut.daily()
    first = [(s.key, n) for s in ts.SERIES
             if (n := tut.next_in_series(s, set())[0]) is not None]
    assert tick["seen"] == [("academy.render_tutorial", {"series": k, "episode": n})
                            for k, n in first]
    assert tick["reviewed"] == first
    assert "academy.autopublish_tutorial" not in str(tick["seen"])
    assert "channel #1: card #1" in said      # after the 2026-10-18 series' notes


async def test_today_drafts_one_per_series_then_stops(tick):
    from datetime import UTC, datetime, timedelta
    yesterday = datetime.now(UTC) - timedelta(days=1)
    tick["rows"] = [{"kind": "post", "series": "channel", "episode": 1, "at": yesterday},
                    {"kind": "post", "series": "claude", "episode": 0, "at": yesterday}]
    await tut.daily()
    assert tick["reviewed"] == [("claude", 1), ("n8n", 0), ("project", 0),
                                ("business", 0), ("agents", 1), ("local", 0)]
    now = datetime.now(UTC)
    tick["rows"] += [{"kind": "draft", "series": k, "episode": n, "at": now}
                     for k, n in tick["reviewed"]]
    tick["reviewed"].clear()
    said = await tut.daily()
    assert said.startswith("today's drafts are already made")
    assert said.removeprefix("today's drafts are already made") == "".join(
        f"; {n}" for n in NEW_NOTES)               # the 2026-10-18 series' notes
    assert tick["reviewed"] == []


async def test_a_missing_script_skips_only_its_series_and_is_said_once_a_day(
        tick, monkeypatch):
    from datetime import UTC, datetime, timedelta
    old = datetime.now(UTC) - timedelta(days=3)
    claude = ts.series("claude")
    gap = next(e for e in tut.ladder(claude) if not e.scripted)
    tick["rows"] = [{"kind": "draft", "series": "claude", "episode": e.number, "at": old}
                    for e in tut.ladder(claude) if e.level < gap.level
                    or (e.level == gap.level and e.number < gap.number)]
    said = await tut.daily()
    note = f"script not written for claude #{gap.number}"
    assert note in said
    assert ("claude", gap.number) not in tick["reviewed"]
    assert ("n8n", 0) in tick["reviewed"]                  # the others go on
    assert sum(note in t for t in tick["told"]) == 1
    await tut.daily()                                      # a redeploy reruns the tick
    assert sum(note in t for t in tick["told"]) == 1


async def test_a_refused_render_does_not_stop_the_other_series(tick, monkeypatch):
    from atlas.kernel import executor

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        return ActionResult(req.params["series"] != "claude", "no")
    monkeypatch.setattr(executor, "execute", execute)
    await tut.daily()
    assert ("claude", 0) not in tick["reviewed"] and ("n8n", 0) in tick["reviewed"]


async def test_by_default_the_tick_posts_by_itself_not_a_card(tick, monkeypatch):
    """the owner, 2026-10-15: env unset -> academy.autopublish_tutorial for each
    series; "off" -> the review card."""
    monkeypatch.delenv("ACADEMY_TUTORIALS_AUTOPUBLISH", raising=False)
    await tut.daily()
    first = [(s.key, n) for s in ts.SERIES
             if (n := tut.next_in_series(s, set())[0]) is not None]
    assert [p for a, p in tick["seen"] if a == "academy.autopublish_tutorial"] == [
        {"series": k, "episode": n} for k, n in first]
    assert tick["reviewed"] == []
    tick["seen"].clear()
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "off")
    await tut.daily()
    assert "academy.autopublish_tutorial" not in str(tick["seen"])
    assert tick["reviewed"] == first


async def test_a_post_the_executor_never_ran_is_carded_not_dropped(tick, monkeypatch):
    """Watch-only or a pause refuses before the action runs: no card would
    come from autopublish_tutorial itself, so the tick raises it
    (2026-10-15). An hourly ceiling (FailureClass.BUDGET) waits instead."""
    from atlas.kernel import executor
    monkeypatch.delenv("ACADEMY_TUTORIALS_AUTOPUBLISH", raising=False)
    cards = []

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.action == "academy.autopublish_tutorial" and req.params["series"] == "n8n":
            return ActionResult(False, "ran 1 times this hour", dispatched=False)
        return ActionResult(True, "ok", dispatched=True)

    async def card(series, episode, why):
        cards.append((series, episode, why))
        return ActionResult(True, "held; card #4 raised")
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(tut, "_card", card)
    said = await tut.daily()
    n8n = tut.ladder(ts.series("n8n"))[0].number
    assert cards == [("n8n", n8n, "ran 1 times this hour")]
    assert f"n8n #{n8n}: held; card #4 raised" in said


def _ceiling(detail: str = "academy.render_tutorial has already run 2 times this hour"):
    from atlas.kernel.contracts import FailureClass
    return ActionResult(False, detail, failure_class=FailureClass.BUDGET,
                        dispatched=False)


async def test_a_render_over_the_hourly_ceiling_waits_quietly_for_the_next_hour(
        tick, monkeypatch):
    """2026-10-16: academy.render_tutorial runs twice an hour; renders 3-6
    of the day were refused, told to Slack as failures, and never retried.
    Now the ceiling is said once a day and the series wait for the next
    hour (catch_up), while a real refusal is still told."""
    from datetime import timedelta

    from atlas.kernel import executor
    monkeypatch.delenv("ACADEMY_TUTORIALS_AUTOPUBLISH", raising=False)
    renders: list[str] = []
    cards: list = []

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.action == "academy.render_tutorial":
            if req.params["series"] == "n8n":
                return ActionResult(False, "dry run refused: no voice",
                                    dispatched=False)
            if len(renders) >= 2:
                return _ceiling()
            renders.append(req.params["series"])
        return ActionResult(True, "posted", dispatched=True)

    async def card(series, episode, why):
        cards.append((series, episode))
        return ActionResult(True, "held")
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(tut, "_card", card)
    said = await tut.daily()
    assert renders == ["channel", "claude"]
    assert cards == []
    real = [t for t in tick["told"] if "n8n" in t]
    assert len(real) == 1 and "no voice" in real[0]          # a real refusal: told
    ceiling = [t for t in tick["told"] if "hourly" in t]
    assert len(ceiling) == 1                                  # said once, not per series
    assert "project" in ceiling[0] and "local" in ceiling[0]
    assert "next hour" in said
    held = {k for k, _ in tut.deferred()}
    assert held == {"project", "business", "agents", "local"}
    now = tick["noon"]
    assert not tut.retry_due(now)                            # the window is still full
    assert tut.retry_due(now + timedelta(minutes=61))

    # The next hour: only the held series, and the ceiling is not said again.
    renders.clear()
    tick["seen"].clear()
    await tut.catch_up()
    assert renders == ["project", "business"]
    assert not [a for a, p in tick["seen"] if p.get("series") in ("channel", "claude",
                                                                 "n8n")]
    assert {k for k, _ in tut.deferred()} == {"agents", "local"}
    assert len([t for t in tick["told"] if "hourly" in t]) == 1
    renders.clear()
    await tut.catch_up()
    assert renders == ["agents", "local"]
    assert tut.deferred() == []
    assert not tut.retry_due(now + timedelta(hours=5))


async def test_a_post_over_the_hourly_ceiling_is_not_carded(tick, monkeypatch):
    from atlas.kernel import executor
    monkeypatch.delenv("ACADEMY_TUTORIALS_AUTOPUBLISH", raising=False)
    cards: list = []

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.action == "academy.autopublish_tutorial" and req.params["series"] == "n8n":
            return _ceiling("the academy module has already run 10 actions this hour")
        return ActionResult(True, "posted", dispatched=True)

    async def card(series, episode, why):
        cards.append((series, episode))
        return ActionResult(True, "held")
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(tut, "_card", card)
    await tut.daily()
    assert cards == []
    assert [k for k, _ in tut.deferred()] == ["n8n"]


async def test_a_held_post_is_posted_next_hour_without_rendering_again(
        tick, monkeypatch):
    """Rendered, then the post was held by the ceiling: the next hour posts
    the render already on disk, once, and never renders it again."""
    from atlas.kernel import executor
    monkeypatch.delenv("ACADEMY_TUTORIALS_AUTOPUBLISH", raising=False)
    s = ts.series("n8n")
    ep = tut.ladder(s)[0]
    posts = {"n8n": 0}

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.action == "academy.render_tutorial" and req.params["series"] == "n8n":
            d = tut.episode_dir(s, ep)
            d.mkdir(parents=True, exist_ok=True)
            (d / "video.mp4").write_bytes(b"x")
            m = tut.Manifest("n8n", ep.number, "fish", 300.0, CH,
                             parts=["intro-a.mp4", "outro-b.mp4"],
                             **opened(300.0, "n8n", ep.number),
                             script=tut.script_key(ep))
            (d / "manifest.json").write_text(json.dumps(m.__dict__))
        if req.action == "academy.autopublish_tutorial" and req.params["series"] == "n8n":
            posts["n8n"] += 1
            if posts["n8n"] == 1:
                return _ceiling("academy.autopublish_tutorial has already run 7 times")
        return ActionResult(True, "posted", dispatched=True)
    monkeypatch.setattr(executor, "execute", execute)
    await tut.daily()
    assert tut.deferred() == [("n8n", ep.number)]
    assert tut.reusable(s, ep)
    tick["seen"].clear()
    await tut.catch_up()
    assert tick["seen"] == [("academy.autopublish_tutorial",
                             {"series": "n8n", "episode": ep.number})]
    assert tut.deferred() == []


async def test_a_catch_up_that_raises_is_not_started_again_at_once(tick, monkeypatch):
    """The held list is restamped when catch_up starts: a run that raises
    midway waits another hour, never a retry every scheduler cycle."""
    from datetime import timedelta
    now = tick["noon"]
    monkeypatch.setattr(tut, "_deferred", (now - timedelta(hours=2), [("n8n", 0)]))
    assert tut.retry_due(now)

    async def broken():
        raise RuntimeError("database gone")
    monkeypatch.setattr(tut, "_done", broken)
    with pytest.raises(RuntimeError):
        await tut.catch_up()
    assert tut.deferred() == [("n8n", 0)]
    assert not tut.retry_due(now)
    assert tut.retry_due(now + timedelta(minutes=61))


async def test_a_day_without_the_ceiling_leaves_nothing_to_retry(tick):
    from datetime import timedelta
    await tut.daily()
    assert tut.deferred() == []
    assert not tut.retry_due(tick["noon"] + timedelta(hours=2))


async def test_a_deferral_is_not_retried_on_another_day(tick, monkeypatch):
    from datetime import timedelta

    from atlas.kernel import executor

    async def execute(req):
        return _ceiling()
    monkeypatch.setattr(executor, "execute", execute)
    await tut.daily()
    assert tut.deferred()
    assert not tut.retry_due(tick["noon"] + timedelta(days=1, minutes=5))


async def test_autopublish_on_keeps_the_old_path(tick, monkeypatch):
    monkeypatch.setenv("ACADEMY_TUTORIALS_AUTOPUBLISH", "on")
    from datetime import UTC, datetime, timedelta
    tick["rows"] = [{"kind": "post", "series": "channel", "episode": 1,
                     "at": datetime.now(UTC) - timedelta(days=1)}]
    await tut.daily()
    assert ("academy.autopublish_tutorial",
            {"series": "claude", "episode": 0}) in tick["seen"]
    assert tick["reviewed"] == []


# 2026-10-13 (task 6, fix round 1): the daily tick posts the quiz + lab
# comment under every public tutorial that has none, through the executor.
async def test_the_tick_pins_a_comment_under_a_public_tutorial_without_one(tick):
    tick["unpinned"] = [{"series": "claude", "episode": 1}]
    said = await tut.daily()
    pins = [p for a, p in tick["seen"] if a == "academy.pin_comment"]
    assert pins == [{"series": "claude", "episode": 1}]
    assert "claude #1 comment: ok" in said


async def test_no_comment_is_tried_when_the_token_lacks_force_ssl(tick):
    """Final review M4: a comment needs youtube.force-ssl. Without it the
    pass says so and asks the executor for nothing, so no claim is taken."""
    tick["unpinned"] = [{"series": "claude", "episode": 1}]
    tick["scopes"] = ({"https://www.googleapis.com/auth/youtube"}, None)
    said = await tut.pin_pass()
    assert said == ["comments held: the token lacks youtube.force-ssl"]
    assert not [a for a, _ in tick["seen"] if a == "academy.pin_comment"]


async def test_unreadable_scopes_hold_the_comments_with_the_reason(tick):
    tick["unpinned"] = [{"series": "claude", "episode": 1}]
    tick["scopes"] = (None, "clips: tokeninfo: timed out")
    said = await tut.pin_pass()
    assert said == [("comments held: the token's scopes could not be read "
                     "(clips: tokeninfo: timed out)")]
    assert not [a for a, _ in tick["seen"] if a == "academy.pin_comment"]


async def test_the_scopes_are_not_read_when_there_is_nothing_to_post(tick):
    tick["unpinned"] = []
    assert await tut.pin_pass() == []
    assert tick["scope_reads"] == 0


async def test_the_tick_pins_nothing_where_a_comment_is_up(tick):
    tick["unpinned"] = []                 # the comment row exists: not unpinned
    await tut.daily()
    assert not [a for a, _ in tick["seen"] if a == "academy.pin_comment"]


async def test_the_tick_pins_at_most_three_a_day(tick):
    tick["unpinned"] = [{"series": "n8n", "episode": n} for n in range(3)] + [
        {"series": "claude", "episode": n} for n in range(3)]
    tick["pinned_today"] = 1
    await tut.daily()
    assert len([a for a, _ in tick["seen"] if a == "academy.pin_comment"]) == 2


async def test_the_tick_pins_even_when_the_drafts_are_done(tick):
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    tick["rows"] = [{"kind": "draft", "series": s.key, "episode": e.number, "at": now}
                    for s in ts.SERIES for e in s.episodes]
    tick["unpinned"] = [{"series": "claude", "episode": 1}]
    said = await tut.daily()
    assert ("academy.pin_comment", {"series": "claude", "episode": 1}) in tick["seen"]
    assert said.startswith("today's drafts are already made")


def test_the_tutorial_job_lists_the_comment_action():
    from atlas.kernel import departments
    assert "academy.pin_comment" in departments.JOBS["academy_tutorial"].actions


# 2026-10-13 (growth plan task 7, part B; part C moved it to its own tick,
# teaching_tick, the owner, 2026-10-13: two Shorts a day): the tick cuts
# one teaching Short from the oldest public tutorial with a cut not yet posted, renders it
# (academy.render_teaching_short) and posts it (clips.autopublish_promo),
# both through the executor.
VID = "iRyhxkLk-Yo"                  # mixed case, the way real ids are


def _short_manifest(series: str = "claude", episode: int = 1) -> tut.Manifest:
    """A render of the episode: the opener, then every scene line said in
    order, 7 s each (a quiz's 3-2-1 before its reveal), its chapters."""
    ep = ts.series(series).episode(episode)
    timings, chapters, t = [(1.0, 11.0, "Welcome back.")], [(0.0, "Intro")], 12.0
    for sc in tut.tutorial_short.lesson_scenes(ep):
        if sc.chapter:
            chapters.append((t, sc.chapter))
        for k, said in enumerate(sc.said):
            if sc.kind == "quiz" and k == 1:
                t += 3.0
            timings.append((t, t + 7.0, said))
            t += 7.0
    return tut.Manifest(series=series, episode=episode, audio="fish", seconds=t + 20.0,
                        chapters=chapters, timings=timings, intro_seconds=12.0)


def _cut(kind: str, series: str = "claude", episode: int = 1) -> tut.tutorial_short.Cut:
    ep = ts.series(series).episode(episode)
    return next(c for c in tut.tutorial_short.cuts(_short_manifest(series, episode), ep)
                if c.kind == kind)


def _put_episode(key: str, n: int) -> Path:
    s = ts.series(key)
    ep = s.episode(n)
    d = tut.episode_dir(s, ep)
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(_short_manifest(key, n).__dict__))
    (d / "video.mp4").write_bytes(b"x")
    return d


async def test_the_tick_cuts_one_teaching_short_and_posts_it(tick, monkeypatch):
    from atlas.kernel import executor
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "video_id": VID, "taught": []}]

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.action == "academy.render_teaching_short":
            return ActionResult(True, "rendered", evidence={
                "media_id": 41, "caption": "the caption\nMade with AI",
                "youtube_caption": "the caption\nMade with AI",
                "slug": "tutorial:claude:1:payoff"})
        return ActionResult(True, "ok")
    monkeypatch.setattr(executor, "execute", execute)
    said = await tut.teaching_tick()
    teach = [(a, p) for a, p in tick["seen"]
             if a in ("academy.render_teaching_short", "clips.autopublish_promo")]
    assert teach == [
        ("academy.render_teaching_short",
         {"series": "claude", "episode": 1, "kind": "payoff"}),
        ("clips.autopublish_promo",
         {"media_id": 41, "caption": "the caption\nMade with AI",
          "slug": "tutorial:claude:1:payoff",
          "youtube_caption": "the caption\nMade with AI"})]
    assert "tutorial:claude:1:payoff: ok" in said
    assert tick["flagged_after"] == ["tutorial:claude:1:payoff"]


async def test_a_held_teaching_post_is_not_flagged(tick, monkeypatch):
    from atlas.kernel import executor
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "video_id": VID, "taught": []}]

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.action == "clips.autopublish_promo":
            return ActionResult(False, "held (the day's cap)")
        return ActionResult(True, "rendered", evidence={"media_id": 41})
    monkeypatch.setattr(executor, "execute", execute)
    await tut.teaching_tick()
    assert tick["flagged_after"] == []


async def test_the_ai_flag_catch_up_runs_every_day_and_never_blocks(tick, monkeypatch):
    monkeypatch.setattr(tut, "teaching_day", lambda now: False)
    tick["catch_up_said"] = "the AI flag catch-up failed (see the log)"
    said = await tut.teaching_tick()
    assert tick["catch_ups"] == 1 and said.startswith("not a teaching day")
    assert "AI flag catch-up failed" in said
    monkeypatch.setattr(tut, "teaching_day", lambda now: True)
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "video_id": VID, "taught": []}]
    tick["after_said"] = "the AI flag on tutorial:claude:1:payoff failed (see the log)"
    said = await tut.teaching_tick()
    assert tick["catch_ups"] == 2
    assert [a for a, _ in tick["seen"]].count("clips.autopublish_promo") == 1
    assert tick["refreshes"] == 2       # the metadata refresh, every day too


async def test_the_tick_takes_the_next_kind_not_yet_posted(tick):
    _put_episode("claude", 4)                      # #4 has a "Not working?" beat
    tick["untaught"] = [{"series": "claude", "episode": 4, "video_id": VID,
                         "taught": ["payoff"]}]
    await tut.teaching_tick()
    renders = [p for a, p in tick["seen"] if a == "academy.render_teaching_short"]
    assert renders == [{"series": "claude", "episode": 4, "kind": "stuck"}]


async def test_the_tick_cuts_nothing_when_every_kind_is_posted(tick):
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "video_id": VID,
                         "taught": ["payoff", "stuck", "quiz"]}]
    await tut.teaching_tick()
    assert not [a for a, _ in tick["seen"]
                if a in ("academy.render_teaching_short", "clips.autopublish_promo")]


async def test_a_refused_teaching_render_is_skipped_and_the_next_is_tried(
        tick, monkeypatch):
    """Final review I2: a cut whose render is refused is skipped for a week
    (academy_teaching_skips), so it no longer heads the queue every day; the
    pass goes on to the next candidate, still at most one post a run."""
    from atlas.kernel import executor
    _put_episode("claude", 1)
    _put_episode("n8n", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "taught": []},
                        {"series": "n8n", "episode": 1, "taught": []}]

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.action == "academy.render_teaching_short":
            if req.params["series"] == "claude":
                return ActionResult(False, "no ffmpeg")
            return ActionResult(True, "rendered", evidence={"media_id": 42})
        return ActionResult(True, "posted")
    monkeypatch.setattr(executor, "execute", execute)
    said = await tut.teaching_tick()
    assert [p for a, p in tick["seen"] if a == "academy.render_teaching_short"] == [
        {"series": "claude", "episode": 1, "kind": "payoff"},
        {"series": "n8n", "episode": 1, "kind": "payoff"}]
    assert [s for s, _ in tick["skips"]] == ["tutorial:claude:1:payoff"]
    assert "no ffmpeg" in tick["skips"][0][1]
    posts = [p for a, p in tick["seen"] if a == "clips.autopublish_promo"]
    assert [p["slug"] for p in posts] == ["tutorial:n8n:1:payoff"]
    assert "render refused: no ffmpeg" in said


async def test_a_carded_teaching_short_is_skipped_and_the_next_is_tried(
        tick, monkeypatch):
    from atlas.kernel import executor
    _put_episode("claude", 1)
    _put_episode("n8n", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "taught": []},
                        {"series": "n8n", "episode": 1, "taught": []}]
    tick["rendered"] = {"tutorial:claude:1:payoff": 33, "tutorial:n8n:1:payoff": 34}

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        if req.params.get("media_id") == 33:
            return ActionResult(True, "held (the file is 8 s, under 12 s); card #4 raised")
        return ActionResult(True, "posted")
    monkeypatch.setattr(executor, "execute", execute)
    await tut.teaching_tick()
    assert [s for s, _ in tick["skips"]] == ["tutorial:claude:1:payoff"]
    posts = [p["media_id"] for a, p in tick["seen"] if a == "clips.autopublish_promo"]
    assert posts == [33, 34]                           # one post, after one card


async def test_a_cap_hold_writes_no_skip_and_tries_nothing_else(tick, monkeypatch):
    from atlas.kernel import executor
    from atlas.modules.clips import promo
    _put_episode("claude", 1)
    _put_episode("n8n", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "taught": []},
                        {"series": "n8n", "episode": 1, "taught": []}]
    tick["rendered"] = {"tutorial:claude:1:payoff": 33, "tutorial:n8n:1:payoff": 34}

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        return ActionResult(True, f"not posted today (2 Academy reel(s) already posted "
                                  f"today {promo.CAP_HELD}); no card")
    monkeypatch.setattr(executor, "execute", execute)
    await tut.teaching_tick()
    assert tick["skips"] == []
    assert [p["media_id"] for a, p in tick["seen"] if a == "clips.autopublish_promo"] == [33]


async def test_a_skipped_cut_is_passed_for_the_episodes_next_kind(tick):
    _put_episode("claude", 4)
    tick["untaught"] = [{"series": "claude", "episode": 4, "taught": [],
                         "skipped": ["payoff"]}]
    await tut.teaching_tick()
    renders = [p for a, p in tick["seen"] if a == "academy.render_teaching_short"]
    assert renders == [{"series": "claude", "episode": 4, "kind": "stuck"}]


async def test_untaught_carries_the_cuts_skipped_until_a_later_day(monkeypatch):
    seen = []

    async def fetch_all(sql, *args):
        q = " ".join(sql.split())
        seen.append(q)
        if "academy_teaching_skips" in q:
            return [{"slug": "tutorial:claude:1:payoff"}]
        if "academy_promo_posts" in q:
            return [{"slug": "tutorial:claude:1:quiz"}]
        return [{"series": "claude", "episode": 1, "at": None}]
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    (row,) = await tut.untaught()
    assert row["taught"] == ["quiz"] and row["skipped"] == ["payoff"]
    assert any("academy_teaching_skips" in q and "until > now()" in q for q in seen)


async def test_skip_teaching_holds_the_cut_for_seven_days(monkeypatch):
    sql = []

    async def execute_sql(q, *args):
        sql.append((" ".join(q.split()), args))
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    await tut.skip_teaching("tutorial:claude:1:payoff", "render refused: no ffmpeg")
    ((q, args),) = sql
    assert "INSERT INTO academy_teaching_skips" in q and "interval '7 days'" in q
    assert "ON CONFLICT (slug) DO UPDATE" in q
    assert args == ("tutorial:claude:1:payoff", "render refused: no ffmpeg")


async def test_a_cut_that_cannot_render_is_passed_over_not_tried(tick):
    """Fix round 1: one episode that can never render (its video is not a
    publishable file) no longer blocks every later one."""
    d = _put_episode("claude", 1)
    (d / "video.mp4").unlink()
    (d / "video.mp4").symlink_to(d / "manifest.json")      # not publishable
    _put_episode("n8n", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "taught": []},
                        {"series": "n8n", "episode": 1, "taught": []}]
    await tut.teaching_tick()
    renders = [p for a, p in tick["seen"] if a == "academy.render_teaching_short"]
    assert renders == [{"series": "n8n", "episode": 1, "kind": "payoff"}]


async def test_read_only_refusals_do_not_spend_the_candidate_budget(tick, monkeypatch):
    """2026-10-15: the oldest public tutorials predate the payoff and the
    timings, so every kind is refused before anything runs. Looking costs
    nothing: the pass walks past them to the first episode that can be cut."""
    looked = []
    real = tut._teaching_cut

    async def spy(key, n, kind):
        looked.append((key, n))
        return await real(key, n, kind)
    monkeypatch.setattr(tut, "_teaching_cut", spy)
    _put_episode("n8n", 4)                         # only the fifth has a render
    tick["untaught"] = [{"series": "n8n", "episode": n, "taught": []} for n in range(5)]
    await tut.teaching_tick()
    assert len(set(looked)) == 5
    renders = [p for a, p in tick["seen"] if a == "academy.render_teaching_short"]
    assert renders == [{"series": "n8n", "episode": 4, "kind": "payoff"}]
    assert len([a for a, _ in tick["seen"] if a == "clips.autopublish_promo"]) == 1


async def test_nothing_is_rendered_when_no_episode_can_be_cut(tick):
    tick["untaught"] = [{"series": "n8n", "episode": n, "taught": []} for n in range(5)]
    await tut.teaching_tick()                      # none has a render on disk
    assert not [a for a, _ in tick["seen"] if a == "academy.render_teaching_short"]


async def test_at_most_three_render_attempts_a_run(tick, monkeypatch):
    """TEACH_CANDIDATES caps the attempts that reach the executor: renders
    refused one after another stop after three, each resting a week."""
    from atlas.kernel import executor
    for n in range(5):
        _put_episode("n8n", n)
    tick["untaught"] = [{"series": "n8n", "episode": n, "taught": []} for n in range(5)]

    async def execute(req):
        tick["seen"].append((req.action, req.params))
        return ActionResult(False, "no ffmpeg")
    monkeypatch.setattr(executor, "execute", execute)
    await tut.teaching_tick()
    renders = [p for a, p in tick["seen"] if a == "academy.render_teaching_short"]
    assert [p["episode"] for p in renders] == [0, 1, 2] == list(range(tut.TEACH_CANDIDATES))
    assert len(tick["skips"]) == 3


async def test_no_teaching_short_when_the_days_promo_slots_are_used(tick):
    from atlas.modules.clips import promo
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "taught": []}]
    tick["promo_today"] = promo.per_day()
    said = await tut.teaching_tick()
    assert not [a for a, _ in tick["seen"]
                if a in ("academy.render_teaching_short", "clips.autopublish_promo")]
    assert "teaching Short held: the day's promo slots are used" in said


async def test_a_finished_render_of_the_cut_is_posted_not_rendered_again(tick):
    """Fix round 1: a cut held or carded yesterday is posted from its render,
    so no second file and no second card (the card's key is the media id)."""
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "taught": []}]
    tick["rendered"] = {"tutorial:claude:1:payoff": 33}
    await tut.teaching_tick()
    assert not [a for a, _ in tick["seen"] if a == "academy.render_teaching_short"]
    (post,) = [p for a, p in tick["seen"] if a == "clips.autopublish_promo"]
    s = ts.series("claude")
    ep = s.episode(1)
    cut = tut.tutorial_short.cuts(_short_manifest(), ep)[0]
    text = tut.teaching_caption(s, ep, cut, VID)
    assert post == {"media_id": 33, "caption": text, "slug": "tutorial:claude:1:payoff",
                    "youtube_caption": text}


async def test_rendered_teaching_reads_a_done_row_whose_file_is_there(monkeypatch, tmp_path):
    from atlas.kernel import state as kstate
    f = tmp_path / "33.mp4"
    seen = []

    async def fetch_one(sql, *a):
        seen.append((sql, a))
        return {"id": 33, "path": str(f)}
    monkeypatch.setattr(kstate, "fetch_one", fetch_one)
    assert await tut.rendered_teaching("tutorial:claude:1:payoff") is None   # no file
    f.write_bytes(b"x")
    assert await tut.rendered_teaching("tutorial:claude:1:payoff") == 33
    sql, args = seen[-1]
    # 2026-10-16: only a render in today's look is reused; an older one
    # (no word highlight, no punch-in) is cut again. The source keeps the
    # slug, so a cut already posted is never posted again.
    assert "status = 'done'" in sql and "d.title LIKE $2" in sql
    assert args == ("academy:tutorial:claude:1:payoff",
                    f"%{tut.tutorial_short.look_tag()}")


async def test_an_episode_without_a_render_on_disk_is_passed_over(tick):
    _put_episode("n8n", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "video_id": VID, "taught": []},
                        {"series": "n8n", "episode": 1, "video_id": VID, "taught": []}]
    await tut.teaching_tick()
    renders = [p for a, p in tick["seen"] if a == "academy.render_teaching_short"]
    assert renders == [{"series": "n8n", "episode": 1, "kind": "payoff"}]


async def test_an_episode_whose_caption_the_guard_refuses_is_passed_over(
        tick, monkeypatch):
    """A title naming a real product (claude #5's PreToolUse) would be carded
    every day and starve every later episode; it is skipped, said once. (The
    episodes the guard refuses today are unscripted and give no cut, so the
    guard is made to refuse a scripted one.)"""
    from atlas.modules.content import guard
    real = guard.refusal
    monkeypatch.setattr(guard, "refusal", lambda text: "names a product"
                        if "Subagents" in text else real(text))
    bad = ("claude", 4)
    _put_episode(*bad)
    _put_episode("n8n", 1)
    tick["untaught"] = [{"series": bad[0], "episode": bad[1], "video_id": VID,
                         "taught": []},
                        {"series": "n8n", "episode": 1, "video_id": VID, "taught": []}]
    said = await tut.teaching_tick()
    renders = [p for a, p in tick["seen"] if a == "academy.render_teaching_short"]
    assert renders == [{"series": "n8n", "episode": 1, "kind": "payoff"}]
    assert f"{bad[0]} #{bad[1]}" in said


def test_the_teaching_caption_passes_the_promo_gate_with_a_real_video_id():
    from atlas.modules.clips import promo
    from atlas.modules.content import guard
    s = ts.series("claude")
    ep = s.episodes[1]
    for cut in tut.tutorial_short.cuts(_short_manifest(), ep):
        text = tut.teaching_caption(s, ep, cut, VID)
        assert f"\nhttps://youtu.be/{VID}\n" in text and "Full lesson: " in text
        assert promo.vet({"media_id": 1, "caption": text, "youtube_caption": text}) is None
        assert guard.refusal(text) is None


def test_every_teaching_caption_discloses_the_voice_in_its_short_form():
    """the owner 2026-10-17: the Short's caption says "Synthetic voice (AI)." on
    the line above the label, never on the first line (the Short's title)."""
    from atlas.modules.clips import promo
    for s in ts.SERIES:
        for ep in s.episodes:
            for cut in tut.tutorial_short.cuts(_short_manifest(), ep):
                text = tut.teaching_caption(s, ep, cut, VID)
                lines = text.splitlines()
                assert lines[-2:] == [tut.VOICE_SHORT, tut.AI_LABEL]
                assert tut.VOICE_SHORT not in promo.youtube_title(text)
                assert len(text) <= 2200
                assert promo.vet({"media_id": 1, "caption": text,
                                  "youtube_caption": text}) is None
    assert "synthetic voice" in tut.VOICE_SHORT.lower()


@pytest.fixture
def teach(monkeypatch, tmp_path):
    """render_teaching_short with the database, ffmpeg and the media rows faked."""
    from atlas.kernel import state as kstate
    from atlas.modules.content import ffmpeg, render
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    # The teaching cut is old-design (ATLAS pages and closing card): it runs
    # only with the fallback on since 2026-10-19 (test_academy_notebooklm.py).
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    st = {"row": {"video_id": VID, "public": True}, "posted": [], "sql": [],
          "argv": [], "finished": [], "failed": [], "probed": [],
          # what ffprobe reads from the finished Short: the delivery spec
          "probe": {"streams": [
              {"codec_type": "video", "codec_name": "h264", "profile": "High",
               "width": 1080, "height": 1920, "pix_fmt": "yuv420p",
               "r_frame_rate": "30/1", "avg_frame_rate": "30/1"},
              {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"}],
              "format": {"duration": "33.0"}}}

    async def posted(series, episode):
        st["posted"].append((series, episode))
        return st["row"]

    async def fetch_one(sql, *a):
        st["sql"].append((sql, a))
        return {"id": 7}

    async def run(argv, **kw):
        if argv[0] == "ffprobe":                     # batch 9: the finished Short
            st["probed"].append(argv[-1])
            return json.dumps(st["probe"]).encode()
        st["argv"].append(argv)
        Path(argv[-1]).write_bytes(b"cut")
        return b""

    async def new_row(draft_id, provider):
        return 41

    async def finish(media_id, draft_id, backend, built, notes, *, captions=None):
        st["finished"].append((media_id, draft_id, backend, captions))
        return ActionResult(True, "rendered", evidence={"media_id": media_id},
                            rollback_token=str(media_id))
    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(kstate, "fetch_one", fetch_one)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(render, "_new_row", new_row)
    async def fail(media_id, why):
        st["failed"].append((media_id, why))
        return ActionResult(False, why)
    monkeypatch.setattr(render, "_finish", finish)
    monkeypatch.setattr(render, "_fail", fail)
    st["docs"] = []

    class Shooter:                                   # Chromium, faked
        pages = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def shoot(self, doc, path, w=0, h=0):
            st["docs"].append((doc, path, w, h))
            Path(path).write_bytes(b"png")
            if self.pages is not None:
                self.pages.measured += 1             # measured clean
            return path
    monkeypatch.setattr(tut, "Shooter", Shooter)
    return st


async def test_an_unexpected_error_mid_render_marks_the_row_failed(teach, monkeypatch):
    """Fix round 1: a row is never left at 'rendering'."""
    from atlas.modules.content import ffmpeg
    _put_episode("claude", 1)

    async def boom(argv, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(ffmpeg, "run", boom)
    r = await tut.render_teaching_short("claude", 1, "quiz")
    assert not r.ok and teach["failed"] and teach["failed"][0][0] == 41
    assert "disk full" in teach["failed"][0][1]


async def test_render_teaching_short_cuts_the_window_and_names_the_slug(teach):
    _put_episode("claude", 1)
    r = await tut.render_teaching_short("claude", 1, "quiz")
    assert r.ok, r.detail
    (argv,) = teach["argv"]
    quiz = _cut("quiz")
    assert argv[argv.index("-to") + 1] == f"{quiz.end:.3f}"   # ends on the reveal
    m = _short_manifest()
    reveal = next(b for a, b, _ in m.timings if abs(b - quiz.end) < 0.01)
    assert reveal == quiz.end
    ev = r.evidence
    assert ev["media_id"] == 41 and ev["slug"] == "tutorial:claude:1:quiz"
    assert ev["caption"] == ev["youtube_caption"]
    assert ev["caption"].rstrip().endswith("Made with AI")
    # The draft's source carries the slug, so publish_promo records the post
    # under it (clips.promo.slug_of) and the cut is never posted twice.
    draft = next(a for sql, a in teach["sql"] if "content_drafts" in sql)
    assert draft[1] == "academy:tutorial:claude:1:quiz"
    assert draft[2].endswith(tut.tutorial_short.look_tag())     # the render's look
    assert teach["finished"] == [(41, 7, "academy", True)]      # its lines, captioned
    assert r.rollback_token == "41"
    assert teach["probed"] == [argv[-1]]                        # batch 9: the file read


@pytest.mark.parametrize("change,said", [
    ({"sample_rate": "44100"}, "QA: the sound is 44.1 kHz, not 48 kHz"),
    ({"profile": "Main"}, "QA: the video is H.264 Main, not High profile"),
])
async def test_a_teaching_short_that_fails_qa_is_held_not_finished(teach, change, said):
    """Batch 9: the finished Short's encoding is read before its row is
    finished; a failure marks the row failed, so nothing posts it."""
    _put_episode("claude", 1)
    v, a = teach["probe"]["streams"]
    (a if "sample_rate" in change else v).update(change)
    r = await tut.render_teaching_short("claude", 1, "quiz")
    assert not r.ok and r.detail == said
    assert teach["failed"] == [(41, said)] and teach["finished"] == []


async def test_a_teaching_short_whose_pages_were_not_measured_is_held(teach, monkeypatch):
    _put_episode("claude", 1)
    shooter = tut.Shooter                            # the fixture's fake

    async def unmeasured(self, doc, path, w=0, h=0):
        Path(path).write_bytes(b"png")
        return path
    monkeypatch.setattr(shooter, "shoot", unmeasured)
    r = await tut.render_teaching_short("claude", 1, "quiz")
    assert not r.ok and "pages were not measured" in r.detail


async def test_a_teaching_cut_never_runs_over_forty_seconds(teach, monkeypatch):
    d = _put_episode("claude", 1)
    m = _short_manifest()
    # the line before the first quiz runs 60 s: the reach-back cannot take it
    i = next(i for i, (_, _, t) in enumerate(m.timings) if t.startswith("Quick quiz")) - 1
    m.timings = [(a, b, t) if k < i else (a, b + 53.0, t) if k == i else
                 (a + 53.0, b + 53.0, t) for k, (a, b, t) in enumerate(m.timings)]
    (d / "manifest.json").write_text(json.dumps(m.__dict__))
    r = await tut.render_teaching_short("claude", 1, "quiz")
    assert r.ok, r.detail
    (argv,) = teach["argv"]
    start, end = float(argv[argv.index("-ss") + 1]), float(argv[argv.index("-to") + 1])
    assert end - start <= tut.tutorial_short.CUT_MAX
    held = [float(argv[i + 1]) for i, x in enumerate(argv) if x == "-t"]
    assert sum(held) <= ts.SHORT_MAX + 0.01                  # the card included


async def test_a_render_whose_lines_name_no_scene_is_not_drawn(teach):
    """A render made before its lines matched its scenes gives nothing to
    recompose: refused, never an empty frame."""
    d = _put_episode("claude", 4)
    m = _short_manifest("claude", 4)
    m.timings = [(a, b, f"line {i}") for i, (a, b, _) in enumerate(m.timings)]
    m.chapters = [*m.chapters, (m.seconds - 50.0, "Quick quiz")]
    (d / "manifest.json").write_text(json.dumps(m.__dict__))
    r = await tut.render_teaching_short("claude", 4, "quiz")
    assert not r.ok and tut.RERENDER in r.detail and teach["argv"] == []


async def test_an_unknown_kind_is_refused_before_anything_is_read(teach):
    r = await tut.render_teaching_short("claude", 1, "promo")
    assert not r.ok and "kind" in r.detail
    assert teach["posted"] == [] and teach["argv"] == [] and teach["sql"] == []
    assert not (await tut.render_teaching_short_dry("claude", 1, "promo")).ok
    assert tut.vet_teaching({"series": "claude", "episode": 1, "kind": "promo"})
    assert tut.vet_teaching({"series": "claude", "episode": 1, "kind": "quiz"}) is None


async def test_a_tutorial_that_is_not_public_is_not_cut(teach):
    _put_episode("claude", 1)
    teach["row"] = {"video_id": VID, "public": False}
    r = await tut.render_teaching_short("claude", 1, "payoff")
    assert not r.ok and "not public" in r.detail
    assert teach["argv"] == [] and teach["sql"] == []


async def test_a_missing_cut_is_refused_not_guessed(teach):
    d = _put_episode("claude", 1)
    m = _short_manifest()
    reveals = {sc.said[1] for sc in ts.series("claude").episode(1).scenes
               if sc.kind == "quiz" and len(sc.said) > 1}
    m.timings = [x for x in m.timings if x[2] not in reveals]   # no quiz is answered
    (d / "manifest.json").write_text(json.dumps(m.__dict__))
    r = await tut.render_teaching_short("claude", 1, "quiz")
    assert not r.ok and teach["argv"] == []


async def test_the_teaching_dry_run_says_the_window_and_the_slug(teach):
    _put_episode("claude", 4)
    r = await tut.render_teaching_short_dry("claude", 4, "stuck")
    stuck = _cut("stuck", "claude", 4)
    assert r.ok and "tutorial:claude:4:stuck" in r.detail
    assert f"{stuck.start:.1f}-{stuck.end:.1f}" in r.detail
    assert teach["argv"] == [] and teach["sql"] == []


def test_the_teaching_job_lists_the_teaching_short_actions():
    """Part C: the teaching Short is its own job, not the tutorial tick's."""
    from atlas.kernel import departments
    acts = departments.JOBS["academy_teaching"].actions
    # the owner 2026-10-17: the same tick runs the AI flag on the Shorts.
    assert acts == ("academy.render_teaching_short", "clips.autopublish_promo",
                    "academy.flag_synthetic_short")
    tut_acts = departments.JOBS["academy_tutorial"].actions
    assert "academy.render_teaching_short" not in tut_acts
    assert "clips.autopublish_promo" not in tut_acts


# ------------------------------------------- the teaching Short's own slot
#
# the owner, 2026-10-13: two Shorts a day. The teaching Short posts at or after
# 23 UTC from its own tick, on even New York days of the year only; its tick
# is spawned before the reel's 23 UTC slot, and a reel that finds the cap used
# holds without a card
# (tests/test_academy_reel.py::test_the_daily_cap_is_kept_and_never_a_card).

def _ny_yday(now: datetime) -> int:
    from zoneinfo import ZoneInfo
    return now.astimezone(ZoneInfo("America/New_York")).timetuple().tm_yday


@pytest.mark.parametrize("now", [
    # either side of midnight New York (EDT, UTC-4): one UTC date, two NY days
    datetime(2026, 10, 13, 3, 59, tzinfo=UTC),
    datetime(2026, 10, 13, 4, 1, tzinfo=UTC),
    # either side of a UTC day boundary: two UTC dates, one NY day
    datetime(2026, 10, 13, 23, 59, tzinfo=UTC),
    datetime(2026, 10, 14, 0, 1, tzinfo=UTC),
    # winter (EST, UTC-5)
    datetime(2026, 1, 2, 4, 59, tzinfo=UTC),
    datetime(2026, 1, 2, 5, 1, tzinfo=UTC),
])
def test_a_teaching_day_is_an_even_new_york_day_of_year(now):
    assert tut.teaching_day(now) is (_ny_yday(now) % 2 == 0)


def test_teaching_days_flip_at_new_york_midnight_not_utc():
    # 2026-10-12 is day 285 in New York, 2026-10-13 is day 286.
    assert not tut.teaching_day(datetime(2026, 10, 13, 3, 59, tzinfo=UTC))   # NY 12th
    assert tut.teaching_day(datetime(2026, 10, 13, 4, 1, tzinfo=UTC))        # NY 13th
    # 23:59 UTC on the 13th and 00:01 UTC on the 14th are both NY's 13th
    assert tut.teaching_day(datetime(2026, 10, 13, 23, 59, tzinfo=UTC))
    assert tut.teaching_day(datetime(2026, 10, 14, 0, 1, tzinfo=UTC))
    # 2026-01-01 (day 1) and 2026-01-02 (day 2) in New York, EST
    assert not tut.teaching_day(datetime(2026, 1, 2, 4, 59, tzinfo=UTC))
    assert tut.teaching_day(datetime(2026, 1, 2, 5, 1, tzinfo=UTC))


@pytest.mark.parametrize(("raw", "hour"), [
    (None, 23), ("23", 23), ("0", 0), ("-3", 0), ("30", 23), ("x", 23)])
def test_the_teaching_hour_defaults_to_23_and_is_clamped(monkeypatch, raw, hour):
    if raw is None:
        monkeypatch.delenv("ACADEMY_TEACHING_HOUR_UTC", raising=False)
    else:
        monkeypatch.setenv("ACADEMY_TEACHING_HOUR_UTC", raw)
    assert tut.teaching_hour_utc() == hour


async def test_the_teaching_tick_does_nothing_on_an_odd_day(tick, monkeypatch):
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "taught": []}]
    monkeypatch.setattr(tut, "teaching_day", lambda now: False)
    assert await tut.teaching_tick() == "not a teaching day"
    assert tick["seen"] == []


async def test_the_daily_tutorial_tick_no_longer_posts_teaching_shorts(tick):
    _put_episode("claude", 1)
    tick["untaught"] = [{"series": "claude", "episode": 1, "video_id": VID,
                         "taught": []}]
    await tut.daily()
    assert not [a for a, _ in tick["seen"]
                if a in ("academy.render_teaching_short", "clips.autopublish_promo")]


@pytest.fixture
def teaching_slot(monkeypatch):
    """_maybe_academy_teaching with the clock and background.spawn faked."""
    from atlas.kernel import background, scheduler
    state = {"now": datetime(2026, 10, 13, 22, 59, tzinfo=UTC), "spawned": []}

    def spawn(coro, name):
        coro.close()
        state["spawned"].append(name)
    monkeypatch.setattr(background, "spawn", spawn)
    monkeypatch.setattr(scheduler, "_now", lambda: state["now"])
    monkeypatch.setattr(scheduler, "_last_academy_teaching_date", None)
    monkeypatch.setattr(scheduler, "_academy_teaching_task", None)
    monkeypatch.delenv("ACADEMY_TEACHING_HOUR_UTC", raising=False)
    monkeypatch.delenv("ACADEMY_TUTORIALS", raising=False)
    return state


async def test_the_teaching_slot_waits_for_its_hour_and_runs_once_a_utc_day(
        teaching_slot):
    from atlas.kernel import scheduler
    await scheduler._maybe_academy_teaching()                  # 22:59: not yet
    assert teaching_slot["spawned"] == []
    teaching_slot["now"] = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)
    await scheduler._maybe_academy_teaching()
    assert teaching_slot["spawned"] == ["the Academy teaching Short"]
    teaching_slot["now"] = datetime(2026, 10, 13, 23, 30, tzinfo=UTC)
    await scheduler._maybe_academy_teaching()                  # same UTC date
    assert len(teaching_slot["spawned"]) == 1
    teaching_slot["now"] = datetime(2026, 10, 14, 23, 5, tzinfo=UTC)
    await scheduler._maybe_academy_teaching()                  # the next UTC date
    assert len(teaching_slot["spawned"]) == 2


async def test_the_teaching_slot_is_off_with_the_tutorials(teaching_slot, monkeypatch):
    from atlas.kernel import scheduler
    monkeypatch.setenv("ACADEMY_TUTORIALS", "off")
    teaching_slot["now"] = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)
    await scheduler._maybe_academy_teaching()
    assert teaching_slot["spawned"] == []


async def test_the_loop_calls_the_teaching_tick_before_the_reel(monkeypatch):
    """Fix round 1: on a teaching day the teaching tick is spawned first, so
    its seconds-long cut normally takes the 23 UTC slot before the reel's
    minutes-long render."""
    from atlas.kernel import scheduler
    called: list[str] = []

    def recorder(name):
        async def step():
            called.append(name)
        return step
    for name in ("teaching", "reel", "tutorial"):
        monkeypatch.setattr(scheduler, f"_maybe_academy_{name}", recorder(name))

    async def only_academy(name, fn, timeout=None):
        if name in ("academy_teaching", "academy_reel", "academy_tutorial"):
            await fn()

    class OneCycle(Exception):
        pass

    async def stop(_s):
        raise OneCycle
    monkeypatch.setattr(scheduler, "_durable_enabled", lambda: False)
    monkeypatch.setattr(scheduler, "_step", only_academy)
    monkeypatch.setattr(scheduler, "_detach", lambda name, coro: coro.close())
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop)
    with pytest.raises(OneCycle):
        await scheduler.loop()
    assert called == ["teaching", "reel", "tutorial"]


def test_the_teaching_slot_is_a_scheduler_step():
    from atlas.kernel import scheduler
    assert "academy_teaching" in scheduler.POLICIES
    assert callable(scheduler._maybe_academy_teaching)


# ------------------------------------------------- the render's bookends

@pytest.fixture
def fake_render(monkeypatch, tmp_path):
    """Everything a render touches, faked: each narrated segment lasts `secs`."""
    from atlas.modules.academy import reel_design
    from atlas.modules.content import ffmpeg
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    state = {"secs": 4.0, "said": [], "kw": []}

    class Shooter:
        pages = tut.video_qa.Pages()          # batch 9: render records it
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def shoot(self, doc, path, w=0, h=0):
            Path(path).write_bytes(b"\x89PNG\r\n\x1a\nfake")
            return path

    async def segment(shooter, doc, said, base, preview, **kw):
        state["said"].append(said)
        state["kw"].append(kw)
        return (base + ".mp4", max(kw.get("silent") or state["secs"], kw.get("hold_to", 0.0)),
                "n", None)

    state["intros"] = []
    state["openings"] = []
    state["measured"] = 290.0
    state["scope"] = 8.0                 # the scope line's seconds after the sting

    async def fixed(kind, shooter, preview=False):
        if kind == "intro":
            state["intros"].append(kind)
            return str(tmp_path / "cache" / "intro-abc.mp4"), 6.5, None
        return str(tmp_path / "cache" / f"{kind}-abc.mp4"), 6.0, None

    async def opening(ep, shooter, work, preview=False):
        """The result (6 s), the sting (1 s) and the scope line, as rendered."""
        state["openings"].append(ep.slug)
        a, c = ts.opening_lines(ep)
        end = 7.0 + state["scope"]
        return tut.Opening(
            parts=[f"{work}/intro-result.mp4", str(tmp_path / "cache" / "sting-k.mp4"),
                   f"{work}/scope.mp4"], seconds=end,
            timings=[(0.0, 6.0, a), (7.0, end, c)], note="n",
            last_png=f"{work}/scope.png"), None

    state["argv"] = []

    async def run(argv, timeout=0):
        # the files a real ffmpeg would write (the music step moves them)
        state["argv"].append(list(argv))
        if argv and argv[-1].endswith((".mp4", ".m4a", ".jpg", ".png")):
            Path(argv[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(argv[-1]).write_bytes(b"\x89PNG\r\n\x1a\nfake"
                                       if argv[-1].endswith(".png") else b"")
        return b""

    async def duration(path):
        m = state["measured"]
        return m(len(state["intros"])) if callable(m) else m
    monkeypatch.setattr(tut, "Shooter", Shooter)
    monkeypatch.setattr(tut, "_segment", segment)
    monkeypatch.setattr(tut, "fixed_clip", fixed)
    monkeypatch.setattr(tut, "opening_part", opening)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(reel_design, "available", lambda: True)
    return state


@pytest.mark.parametrize("key,n", [(s.key, e.number) for s in ts.SERIES
                                   for e in s.episodes if e.scripted])
async def test_every_render_opens_with_the_intro_and_closes_with_the_ending(
        fake_render, key, n):
    """2026-10-17 (the video system): a lesson opens on its result, the
    sting at 6-7 s, then its scope; the channel welcome on its narrated
    intro. Every render ends with the ending."""
    d, m, why = await tut.render(key, n)
    assert why is None, why
    assert m.parts[0].startswith("intro-") and m.parts[-1] == "outro-card.mp4"
    assert tut.bookends_refusal(m) is None
    ep = ts.series(key).episode(n)
    # the EndCard: at least 15 s, to the last frame, naming the real next lesson
    assert m.card_seconds >= design.END_CARD_MIN
    assert m.card_at + m.card_seconds == pytest.approx(m.seconds, abs=0.01)
    nxt = ts.next_up(key, ep)
    assert nxt is not None and (m.next_title, m.next_kind) == (nxt.title, nxt.kind)
    assert m.presenter == [] and m.outro_seconds == m.card_seconds
    if key == "channel":
        assert m.opener == ts.INTRO_SAID and m.opening == "welcome"
        assert m.chapters[0][0] == 0.0 and m.sting_seconds == 0.0
        assert fake_render["intros"] == ["intro"] and not fake_render["openings"]
        if ts.topic_line(ep):
            assert fake_render["said"][0] == ts.topic_line(ep)
    else:
        assert m.opening == "result" and fake_render["openings"] == [ep.slug]
        assert m.parts[:3] == ["intro-result.mp4", "sting-k.mp4", "scope.mp4"]
        assert (m.sting_at, m.sting_seconds) == (6.0, 1.0)
        assert m.chapters[0] == (0.0, ts.PAYOFF_CHAPTER)
        assert m.opener == ts.opener_said(ep) and m.opener.startswith("Here is the result")
        assert not fake_render["intros"]              # no welcome, no presenter
        assert "topic.mp4" not in m.parts             # the topic is said once
        assert m.timings[0] == (0.0, 6.0, ts.opening_lines(ep)[0])
    assert m.spoken_minutes == 0 and m.minutes == 5           # 290 s measured
    assert (d / "manifest.json").is_file() and m.audio == "fish"


async def test_the_opening_claims_no_minutes_and_is_never_re_voiced(fake_render):
    """2026-10-17: the time is in the title, measured (rule 2); the opening
    says no number, so a longer join changes the title, never the opening."""
    fake_render["measured"] = 410.0
    d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    assert fake_render["openings"] == ["first-workflow"] or len(fake_render["openings"]) == 1
    assert m.spoken_minutes == 0 and m.minutes == 7 == tut.minutes_of(m.seconds)
    assert not re.search(r"\b(minutes?|\d+)\b", m.opener)
    assert tut.opener_refusal(N8N.episode(1), m.spoken_minutes, m.seconds) is None
    assert "7 minutes" in (d / "description.txt").read_text().splitlines()[3]
    # every lesson line narrated once, the recap's closing one over the card
    lesson, card = tut.card_line("n8n", N8N.episode(1), tut.beats(N8N.episode(1).scenes))
    assert len(fake_render["said"]) == len(lesson) + 1 and fake_render["said"][-1] == card


async def test_a_render_plays_the_result_first_in_its_own_chapter(fake_render):
    """Rule 17 and the video system: the payoff's result is the first thing
    on screen, its chapter at 0:00, the lesson after the scope line."""
    fake_render["secs"] = 6.0
    ep = N8N.episode(1)
    _d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    said = fake_render["said"]
    assert said[0] == ep.scenes[0].said[0]        # no payoff or hook scene after it
    assert [n for _t, n in m.chapters[:2]] == [ts.PAYOFF_CHAPTER, ep.scenes[0].chapter]
    assert m.chapters[1][0] == 15.0               # the lesson starts after the scope
    assert m.timings[0][2] == ts.opening_lines(ep)[0]
    assert m.timings[1] == (7.0, 15.0, ts.opening_lines(ep)[1])


async def test_a_short_opening_gives_its_chapter_to_the_lesson(fake_render):
    """Under 10 s of opening the first lesson chapter takes 0:00: YouTube
    drops every chapter under 10 s."""
    fake_render["scope"] = 2.0                      # the opening ends at 9 s
    _d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    assert m.chapters[0] == (0.0, N8N.episode(1).scenes[0].chapter)
    assert ts.PAYOFF_CHAPTER not in [n for _t, n in m.chapters]
    assert m.chapters[1][0] >= tut.CHAPTER_MIN_SECONDS


def test_the_opener_minutes_must_be_the_titles_and_true():
    """A render made before 2026-10-17 still claims "under N minutes" and is
    held to it; the result-first opening claims none (spoken 0)."""
    ep = N8N.episode(1)
    assert tut.opener_refusal(ep, 0, 400.0) is None
    assert tut.opener_refusal(ep, 5, 290.0) is None
    assert "under 5 minutes" in tut.opener_refusal(ep, 5, 300.0)     # not under
    assert "6 minutes in the title" in tut.opener_refusal(ep, 5, 330.0)   # longer
    assert "4 minutes in the title" in tut.opener_refusal(ep, 5, 230.0)   # title differs
    assert tut.opener_refusal(ts.series("channel").episodes[0], 0, 900.0) is None


async def test_a_too_long_teaching_body_cannot_say_under_five_minutes(fake_render,
                                                                     monkeypatch):
    # a topic line that is said (no opener topic) claims the five minutes
    s = ts.series("n8n")
    said = replace(s.episode(1), opener_topic="", payoff=None)
    monkeypatch.setattr(tut, "resolve", lambda key, n: (s, said))
    fake_render["secs"] = 9.0
    _d, _m, why = await tut.render("n8n", 1)
    assert "under five minutes" in why


async def test_a_long_lesson_that_never_says_under_five_minutes_renders(fake_render):
    """n8n #1 opens on its result; its unsaid topic_said holds nothing."""
    fake_render["secs"] = 9.0
    _d, _m, why = await tut.render("n8n", 1)
    assert why is None or "under five minutes" not in why


# ------------------------------------------------------------- the 3D kit

def test_every_series_has_its_3d_loop_and_still_on_disk():
    # Every series has its loop and still, the 2026-10-18 ones included.
    assert set(design.KIT_SERIES) == {s.key for s in ts.SERIES}
    assert set(NEW) == set(design.KIT_AI_SERIES)
    assert design.kit_missing() is None
    for p in (design.STING, design.INTRO_LOOP, design.OUTRO_LOOP):
        assert p.is_file() and p.stat().st_size > 100_000
    for key in design.KIT_SERIES:
        assert design.loop_path(key).stat().st_size > 100_000
        still = design.still_path(key)
        assert still.read_bytes()[:3] == b"\xff\xd8\xff"            # a JPEG
        assert still.stat().st_size < 2_000_000                     # YouTube's 2 MB


def test_the_kit_manifest_matches_the_files_and_stays_small():
    import hashlib
    kit = json.loads((design.KIT / "kit.json").read_text())
    # the combined bump (2026-10-18) folded the AI series' kit-ai.json in
    assert not (design.KIT / "kit-ai.json").exists()
    ai_names = {p.name for k in design.KIT_AI_SERIES
                for p in (design.loop_path(k), design.still_path(k))}
    files = kit["files"]
    assert ai_names <= set(files)
    names = {p.name for p in design.kit_files()}
    assert names <= set(files)
    for name, meta in files.items():
        data = (design.KIT / name).read_bytes()
        assert len(data) == meta["bytes"]
        assert hashlib.sha256(data).hexdigest() == meta["sha256"]
    assert sum(m["bytes"] for m in files.values()) < 120_000_000
    assert kit["loop_seconds"] == design.LOOP_SECONDS
    assert (kit["width"], kit["height"]) == (design.W, design.H)
    assert list(files) == sorted(files)              # as build.py writes it


def test_every_episode_carries_the_one_kit_stamp():
    old = ts.series("n8n").episode(1)
    new = ts.series("explained").episode(1)
    assert tut.kit_stamp_of(old) == tut.kit_stamp_of(new) == design.kit_stamp()
    assert not hasattr(design, "kit_ai_stamp")


async def test_a_writable_new_series_episode_renders_on_its_own_kit(fake_render,
                                                                    monkeypatch):
    # AI Explained #1 is writable (tutorial_series_ai.HOLDS) but unscripted:
    # a fixture script (n8n #1's scenes and payoff), never a committed one.
    src = ts.series("n8n").episode(1)
    s = ts.series("explained")
    ep = replace(s.episode(1), scenes=src.scenes, payoff=src.payoff, hook=src.hook)
    assert ts.held("explained", ep) is None
    monkeypatch.setattr(tut, "resolve", lambda k, n: (s, ep))
    _d, m, why = await tut.render("explained", 1, preview=True)
    assert why is None, why
    assert m.backdrop == design.TOPICS["layers"] == design.loop_path("explained").name


def test_a_missing_kit_refuses_the_render(monkeypatch, tmp_path):
    monkeypatch.setattr(design, "KIT", tmp_path)
    monkeypatch.setattr(design, "STING", tmp_path / "sting.mp4")
    assert "sting.mp4" in design.kit_missing()


def test_a_segment_is_laid_over_the_loop_where_the_last_one_left_it():
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4",
                            bg="/k/loop-n8n.mp4", bg_offset=13.25, prev="/w/p.png")
    i = argv.index("/k/loop-n8n.mp4")
    assert argv[i - 5:i - 1] == ["-stream_loop", "-1", "-ss", "3.250"]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "eof_action=repeat" in graph and "fade=t=out" in graph
    assert "zoompan" not in graph and "scale=1920:1080" in graph
    # the slide PNG is decoded once, never looped per frame
    png = argv.index("/w/s.png")
    assert argv[png - 1] == "-i" and "-loop" not in argv[:png]
    assert argv[argv.index("-t", argv.index("-filter_complex")) + 1] == "6.000"


def test_the_intro_segment_opens_on_the_sting_with_no_label():
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 9.0, "/w/o.mp4",
                            bg=str(design.INTRO_LOOP), lead=str(design.STING),
                            lead_png="/w/lead.png")
    graph = argv[argv.index("-filter_complex") + 1]
    assert str(design.STING) in argv and "/w/lead.png" in argv
    assert "[lv][la][body][a]concat=n=2:v=1:a=1" in graph
    assert argv[argv.index("-t", argv.index("-filter_complex")) + 1] == "10.000"   # 1 + 9
    # the owner 2026-10-17: off screen; disclosed via containsSyntheticMedia + description
    assert design.TAG not in design.sting_layer() and "placeholder" in design.sting_layer(True)


def test_without_a_loop_the_slide_sits_on_navy():
    """2026-10-17: the video frame is navy, never black."""
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 3.0, "/w/o.mp4")
    assert any(a.startswith("color=c=#101b30") for a in argv)


async def test_every_segment_stands_on_its_topic_and_a_change_crossfades(fake_render):
    _d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    *kws, card = fake_render["kw"]
    # the end card (2026-10-17) stands on flat navy: no loop, no recording
    assert card["hold_to"] == design.END_CARD_MIN and "bg" not in card
    assert "demo" not in card and "prev_bg" not in card
    loops = {str(design.topic_path(t)) for t in design.TOPICS}
    # every segment stands on a topic loop or a live-demo recording, never nothing
    assert kws and all((k.get("bg") in loops) != ("demo" in k) for k in kws)
    assert m.backdrop == design.TOPICS[design.SERIES_TOPIC["n8n"]]
    assert len(m.backdrops) == len(kws)              # one picture per segment
    assert len({b for b in m.backdrops if not b.startswith("demo:")}) >= 6
    assert any(b.startswith("demo:") for b in m.backdrops)
    # each topic's loop resumes where that topic was left; a change crossfades
    clocks: dict[str, float] = {}
    last = None
    for k in kws:
        secs = k.get("silent") or fake_render["secs"]
        pic = k.get("bg") or "demo"     # demo to demo is one continuous session: a cut
        if "bg" in k:
            assert k["bg_offset"] == pytest.approx(clocks.get(pic, 0.0) % design.LOOP_SECONDS)
            clocks[pic] = clocks.get(pic, 0.0) + secs
        if "demo" in k:                 # each step carries the region it shows
            assert k["demo"][3] in td.DEMOS[Path(k["demo"][0]).stem[5:]].steps
        if last is not None and pic != last:
            assert k.get("prev_bg") and (k["prev_bg"][1] == last or last == "demo")
            if k["prev_bg"][0] == "demo":
                assert isinstance(k["prev_bg"][3], td.Step)
        elif last is not None:
            assert "prev_bg" not in k
        last = pic


def test_topics_follow_the_words_and_never_repeat_back_to_back():
    q = ts.Scene("", "quiz", "Q", ("a", "b"), items=("x", "y"), answer=0)
    hook = ts.Scene("Hooked", "bullets", "The Webhook node", ("a",), items=("x",))
    slack = ts.Scene("Slack", "bullets", "Post to Slack", ("a",), items=("x",))
    assert design.scene_topic(q, "n8n") == "quiz"
    assert design.scene_topic(hook, "n8n") == "trigger"
    assert design.scene_topic(slack, "n8n") == "message"
    plain = ts.Scene("Intro", "bullets", "Hello there", ("a",), items=("x",))
    assert design.scene_topic(plain, "claude") == "mcp-hub"         # the series' own
    # the same topic twice in a row moves to its alternate...
    assert design.backdrops((hook, hook), "n8n") == ["trigger", "workflow"]
    # ...unless the author chose it
    mine = ts.Scene("Hooked", "bullets", "The Webhook node", ("a",), items=("x",),
                    backdrop="trigger")
    assert design.backdrops((hook, mine), "n8n") == ["trigger", "trigger"]
    assert set(design.SERIES_TOPIC.values()) <= set(design.TOPICS)
    assert all(set(alt) <= set(design.TOPICS) for alt in design.TOPIC_ALT.values())


@pytest.mark.parametrize("ep", SCRIPTED, ids=lambda e: e.slug)
def test_every_episode_keeps_changing_its_backdrop(ep):
    key = next(s.key for s in ts.SERIES if ep in s.episodes)
    scenes = tut.opening(ep) + ep.scenes
    topics = design.backdrops(scenes, key, before=design.SERIES_TOPIC[key])
    assert all(t in design.TOPICS or t == "demo" for t in topics)
    assert not [a for a, b in itertools.pairwise(topics) if a == b != "demo"]
    assert len(set(topics)) >= 4


def test_every_topic_loop_is_in_the_kit():
    for topic in design.TOPICS:
        p = design.topic_path(topic)
        assert p.is_file() and p.stat().st_size > 100_000, topic
        assert p in design.kit_files()


def test_slides_are_transparent_layers_and_no_thumbnail_stands_on_the_3d_still():
    """Batch 7 (2026-10-17): a thumbnail's one picture is the episode's own
    frame, never the series' 3D still."""
    sc = N8N.episodes[0].scenes[0]
    doc = design.slide(sc, 0, mark="m", chapter=0, chapters=1)
    assert 'class="scrim"' in doc and "background:transparent" in doc
    for key in ("n8n", "claude"):
        thumb = design.thumbnail("What is\nn8n?", key, "m", 1)
        assert "data:image/jpeg;base64," not in thumb and 'class="scrim"' not in thumb
    assert design.still_uri("n8n") != design.still_uri("claude")


def test_a_manifest_without_its_bookends_is_refused():
    m = tut.Manifest("n8n", 1, "fish", 300.0, CH, parts=["topic.mp4", "outro-x.mp4"],
                     **opened(300.0, "n8n", 1))
    assert "opening" in tut.bookends_refusal(m)
    m.parts = ["intro-x.mp4", "lesson001.mp4"]
    assert "end card" in tut.bookends_refusal(m)


def test_a_render_from_before_the_result_first_opening_is_refused():
    """the owner, 2026-10-17: a lesson opens on its result, the sting at 6-7 s;
    an older render (or one that opens on the owner's clip) is made again."""
    m = tut.Manifest("n8n", 1, "fish", 300.0, CH, parts=["intro-a.mp4", "outro-b.mp4"])
    assert "before the result-first opening" in tut.bookends_refusal(m)
    m = tut.Manifest("n8n", 1, "fish", 300.0, CH, parts=["intro-a.mp4", "outro-b.mp4"],
                     **opened(300.0, "n8n", 1))
    assert tut.bookends_refusal(m) is None
    m.sting_at = 0.0
    assert "not 6-7 s" in tut.bookends_refusal(m)
    m.sting_at, m.sting_seconds = 6.0, 4.0
    assert "not 6-7 s" in tut.bookends_refusal(m)
    m.sting_seconds, m.presenter = 1.0, ["intro:abc"]
    assert "the owner's intro clip" in tut.bookends_refusal(m)
    welcome = tut.Manifest("channel", 1, "fish", 120.0, CH,
                           parts=["intro-a.mp4", "outro-b.mp4"], opening="welcome",
                           card_at=105.0, card_seconds=15.0)
    assert tut.bookends_refusal(welcome) is None
    welcome.series = "n8n"                       # only the channel welcome opens so
    assert tut.bookends_refusal(welcome) is not None


# ------------------------------------------------- the Slack review card

def test_cards_land_in_the_academy_videos_channel(monkeypatch):
    from atlas.channels.slack import SlackChannel
    monkeypatch.delenv("ACADEMY_TUTORIALS_SLACK_CHANNEL", raising=False)
    monkeypatch.delenv("SLACK_CHANNELS", raising=False)
    assert SlackChannel().home_for("academy_videos") == "C0SLACKCH03"
    monkeypatch.setenv("ACADEMY_TUTORIALS_SLACK_CHANNEL", "C999")
    assert SlackChannel().home_for("academy_videos") == "C999"
    from atlas.kernel.approvals import _topic
    spec = _actions()["academy.publish_tutorial"]
    assert _topic(spec, spec.name, {"series": "n8n", "episode": 1}) == "academy_videos"


def _m():
    return tut.Manifest("n8n", 1, "fish", 330.0, CH, parts=["intro-a.mp4", "outro-b.mp4"],
                        **opened(330.0, "n8n", 1),
                        intro_seconds=6.4, outro_seconds=13.2, body_seconds=270.0)


def test_the_card_says_intro_and_ending_and_never_carries_the_link():
    text = tut.review_reason(N8N, N8N.episode(1), _m())
    assert "Intro ✓ (6 s) / Ending ✓ (13 s)" in text
    # 5:30 measured: the formula title says 6 minutes, rounded up
    assert "Build Your First n8n Workflow in 6 Minutes (step by step)" in text
    assert "#n8n" in text
    assert "/media/" not in text and "tut." not in text


def test_the_preview_shows_thumbnail_link_description_and_tags(monkeypatch):
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    from datetime import UTC, datetime
    blocks = tut.review_blocks(7, N8N, N8N.episode(1), _m(), "the description",
                               "tut.TOKEN", datetime.now(UTC))
    image = [b for b in blocks if b["type"] == "image"]
    assert image and image[0]["image_url"] == "https://atlas.example/media/tut.TOKEN.jpg"
    flat = json.dumps(blocks, ensure_ascii=False)
    assert "https://atlas.example/media/tut.TOKEN|" in flat
    assert "the description" in flat and "n8n tutorial for beginners" in flat
    assert "*Intro* ✓ 6 s" in flat and "*Ending* ✓ 13 s" in flat
    bare = tut.review_blocks(7, N8N, N8N.episode(1), _m(), "d", "tut.T", datetime.now(UTC),
                             image=False)
    assert not [b for b in bare if b["type"] == "image"]


@pytest.fixture
def review(monkeypatch):
    from atlas.kernel import approvals, comms
    state = {"posts": [], "errors": [], "sql": [], "cards": []}

    async def ready(series, episode):
        return (N8N, N8N.episode(1), Path("/x"), _m(), "desc"), None

    async def fresh_card(action, params, reason, *, dedup_key, **kw):
        state["cards"].append((action, params, reason))
        state["dedup"] = dedup_key
        return 41

    async def reviewed(*a):
        return False

    async def execute_sql(sql, *args):
        state["sql"].append(args)

    class Slack:
        def channel_for(self, topic):
            return "C0SLACKCH03" if topic == "academy_videos" else "CDEFAULT"

        async def post_blocks(self, *, channel, text, blocks):
            state["posts"].append((channel, blocks))
            err = state["errors"].pop(0) if state["errors"] else None
            return (None, err) if err else ("123.4", None)
    monkeypatch.setattr(tut, "ready", ready)
    monkeypatch.setattr(tut, "_reviewed", reviewed)
    monkeypatch.setattr(approvals, "fresh_card", fresh_card)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    monkeypatch.setitem(comms.CHANNELS, "slack", Slack())
    monkeypatch.setattr(comms, "sent_ref", lambda prefix, ch: "111.1")
    return state


async def test_review_raises_the_card_then_posts_the_preview(review):
    said = await tut.review_tutorial("n8n", 1)
    assert review["cards"][0][:2] == ("academy.publish_tutorial",
                                      {"series": "n8n", "episode": 1})
    assert review["posts"][0][0] == "C0SLACKCH03"
    token = review["sql"][0][3]
    assert token.startswith("tut.") and token not in review["cards"][0][2]
    assert said == "card #41 in Slack; preview posted (123.4)"
    assert review["dedup"] == "academy.tutorial.n8n.1.review"   # never an old held card


async def test_not_in_channel_is_reported_not_swallowed(review):
    review["errors"] = ["not_in_channel"]
    said = await tut.review_tutorial("n8n", 1)
    assert "not_in_channel" in said and "/invite @Atlas" in said
    assert len(review["posts"]) == 1                     # no blind retry


async def test_an_image_slack_cannot_fetch_is_dropped_not_the_message(review):
    review["errors"] = ["invalid_blocks"]
    said = await tut.review_tutorial("n8n", 1)
    assert said.endswith("preview posted (123.4)") and len(review["posts"]) == 2
    assert not [b for b in review["posts"][1][1] if b["type"] == "image"]


async def test_a_render_that_is_not_ready_is_said_in_the_channel(monkeypatch):
    told = []

    async def ready(series, episode):
        return None, "it runs 200 s, outside 240-600 s"

    async def tell(text):
        told.append(text)
    monkeypatch.setattr(tut, "ready", ready)
    monkeypatch.setattr(tut, "_tell", tell)
    said = await tut.review_tutorial("n8n", 1)
    assert said == "not drafted: it runs 200 s, outside 240-600 s" and told


# ------------------------------------------------- the public watch link

async def test_a_tutorial_token_serves_its_video_and_thumbnail(monkeypatch, tmp_path):
    from fastapi import HTTPException

    from atlas.api import media
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    d = tut.episode_dir(N8N, N8N.episode(1))
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"v")
    (d / "thumb.jpg").write_bytes(b"j")

    async def fetch_one(sql, token):
        return {"series": "n8n", "episode": 1} if token == "tut.GOOD" else None
    monkeypatch.setattr(media, "fetch_one", fetch_one)
    path, kind = await media._tutorial_path("tut.GOOD")
    assert path.endswith("video.mp4") and kind == "video/mp4"
    path, kind = await media._tutorial_path("tut.GOOD.jpg")
    assert path.endswith("thumb.jpg") and kind == "image/jpeg"
    for bad in ("tut.BAD", "tut.BAD.jpg"):
        with pytest.raises(HTTPException):
            await media._tutorial_path(bad)


# --------------------------------------------------------- Shorts (plan)

def test_a_short_is_cut_from_one_chapter_20_to_40_seconds():
    m = tut.Manifest("n8n", 1, "fish", 330.0,
                     [(0.0, "Intro"), (30.0, "A"), (100.0, "B"), (115.0, "C")])
    assert tut.short_window(m, "A") == (30.0, 70.0)
    assert tut.short_window(m, "B") is None             # 15 s: too short
    assert tut.short_window(m, "nope") is None
    for short in ts.SHORTS:
        assert short.title.endswith("#Shorts") and len(short.title) <= 100
        s = ts.series(short.series)
        assert s and s.episode(short.episode).scripted
        assert short.chapter in [sc.chapter for sc in s.episode(short.episode).scenes]


def test_only_a_render_of_this_very_script_is_reused(tmp_path, monkeypatch):
    s, ep = N8N, N8N.episode(1)
    d = _episode(tmp_path, monkeypatch, "fish")      # an older render: no bookends
    assert not tut.reusable(s, ep)
    m = tut.Manifest("n8n", 1, "fish", 300.0, CH, parts=["intro-a.mp4", "outro-b.mp4"],
                     **opened(300.0, "n8n", 1), script="stale")
    (d / "manifest.json").write_text(json.dumps(m.__dict__))
    assert not tut.reusable(s, ep)
    m.script = tut.script_key(ep)
    (d / "manifest.json").write_text(json.dumps(m.__dict__))
    assert tut.reusable(s, ep)


def test_the_concat_list_names_every_part_by_absolute_path(tmp_path, monkeypatch):
    """A relative entry resolves against the list file's folder (a temp
    dir), which broke the cached intro on the server (2026-10-10)."""
    from atlas.modules.academy import tutorials
    monkeypatch.chdir(tmp_path)
    text = tutorials.concat_listing(["data/academy-tutorials/cache/intro.mp4", "/tmp/w/s1.mp4"])
    lines = text.splitlines()
    assert lines[0] == f"file '{tmp_path}/data/academy-tutorials/cache/intro.mp4'"
    assert lines[1] == "file '/tmp/w/s1.mp4'"


async def test_a_posted_video_without_its_thumbnail_gets_it_on_a_later_day(monkeypatch, tmp_path):
    """YouTube 403s thumbnails until the channel is phone-verified; the
    daily pass finishes the approved post once it is (2026-10-11)."""
    from atlas.kernel import state
    from atlas.modules.academy import tutorials
    from atlas.modules.youtube import api
    s, ep = tutorials.resolve("claude", 0)
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    d = tutorials.episode_dir(s, ep)
    d.mkdir(parents=True)
    (d / "thumb.jpg").write_bytes(b"jpg")
    sql: list[tuple] = []

    async def rows(q, *a):
        return [{"id": 7, "series": "claude", "episode": 0, "video_id": "M1a9Rd9mN6E"}]

    async def run(q, *a):
        sql.append(a)

    calls: list[str] = []

    async def thumb(ch, vid, path):
        calls.append(vid)
        return {"ok": True}, None

    monkeypatch.setattr(state, "fetch_all", rows)
    monkeypatch.setattr(state, "execute_sql", run)
    monkeypatch.setattr(api, "set_thumbnail", thumb)
    assert await tutorials.finish_thumbnails() == ["claude #0: thumbnail set"]
    assert calls == ["M1a9Rd9mN6E"] and sql and sql[0][0] == 7


def test_the_upload_takes_the_png_thumbnail_and_an_older_render_its_jpg(tmp_path):
    from atlas.modules.academy import tutorials
    (tmp_path / "thumb.jpg").write_bytes(b"jpg")
    assert tutorials.thumb_file(tmp_path) == tmp_path / "thumb.jpg"
    (tmp_path / "thumb.png").write_bytes(b"png")
    assert tutorials.thumb_file(tmp_path) == tmp_path / "thumb.png"



def test_an_old_previews_png_never_beats_a_newer_real_jpg(tmp_path):
    """Before batch 7 a preview left thumb.png in the episode's folder and
    the real render wrote only thumb.jpg: that PNG must not be uploaded."""
    import os

    from atlas.modules.academy import tutorials
    png, jpg = tmp_path / "thumb.png", tmp_path / "thumb.jpg"
    png.write_bytes(b"preview png")
    jpg.write_bytes(b"real jpg")
    os.utime(png, (1_000, 1_000))
    os.utime(jpg, (2_000, 2_000))
    assert tutorials.thumb_file(tmp_path) == jpg
    os.utime(png, (2_000, 2_000))                    # written with it, after it
    assert tutorials.thumb_file(tmp_path) == png


def _backfill_world(monkeypatch, tmp_path, rows_):
    from atlas.kernel import state
    from atlas.modules.academy import tutorial_thumbs as tt
    from atlas.modules.academy import tutorials
    from atlas.modules.youtube import api
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    drawn: list[str] = []
    sets: list[str] = []

    async def fetch_all(q, *a):
        assert "public" in q and "withdrawn" in q
        return rows_

    async def render(ep, shooter, out_png, *, level, badge, work):
        drawn.append(ep.slug)
        Path(out_png).write_bytes(b"\x89PNG\r\n\x1a\nx")

    async def set_thumbnail(*a):
        sets.append(a)
        return {"ok": True}, None

    class Shooter:
        pages = tut.video_qa.Pages()          # batch 9: render records it
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(tt, "render", render)
    monkeypatch.setattr(api, "set_thumbnail", set_thumbnail)
    monkeypatch.setattr(tutorials, "Shooter", Shooter)
    return tutorials, tt, drawn, sets


async def test_the_thumbnail_backfill_is_on_and_sets_nothing(monkeypatch, tmp_path):
    """On since the combined bump: the new thumbnail is drawn for a public
    video and no YouTube call is made (thumbnails.set is never reached)."""
    rows_ = [{"series": "claude", "episode": 0, "video_id": "V0aaaaaaaaa"}]
    tutorials, tt, drawn, sets = _backfill_world(monkeypatch, tmp_path, rows_)
    assert tt.BACKFILL is True and tt.NEXT_VERSION == tt.THUMB_VERSION == "4"
    said = await tutorials.backfill_thumbnails()
    assert len(drawn) == 1 and sets == [] and all("not set" in x for x in said)


async def test_a_backfill_switched_off_draws_nothing(monkeypatch, tmp_path):
    rows_ = [{"series": "claude", "episode": 0, "video_id": "V0aaaaaaaaa"}]
    tutorials, tt, drawn, sets = _backfill_world(monkeypatch, tmp_path, rows_)
    monkeypatch.setattr(tt, "BACKFILL", False)
    assert await tutorials.backfill_thumbnails() == [] and drawn == [] and sets == []


async def test_the_backfill_renders_a_few_an_hour_once_each_and_sets_none(
        monkeypatch, tmp_path):
    from datetime import timedelta
    rows_ = [{"series": "claude", "episode": n, "video_id": f"V{n}aaaaaaaaa"}
             for n in range(5)]
    tutorials, tt, drawn, sets = _backfill_world(monkeypatch, tmp_path, rows_)
    monkeypatch.setattr(tt, "BACKFILL", True)
    now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    said = await tutorials.backfill_thumbnails(now=now)
    assert len(drawn) == tutorials.BACKFILL_PER_HOUR == 3
    assert all("not set" in x for x in said) and sets == []      # no public write
    # the same hour: nothing more; an hour on: the rest, never one twice
    assert await tutorials.backfill_thumbnails(now=now + timedelta(minutes=30)) == []
    await tutorials.backfill_thumbnails(now=now + timedelta(hours=1, minutes=1))
    assert sorted(drawn) == sorted(set(drawn)) and len(drawn) == 5
    assert await tutorials.backfill_thumbnails(now=now + timedelta(hours=3)) == []
    record = json.loads((tmp_path / "thumb-backfill.json").read_text())
    assert set(record["done"]) == {r["video_id"] for r in rows_}
    assert set(record["done"].values()) == {tt.NEXT_VERSION}
    d = tutorials.episode_dir(*tutorials.resolve("claude", 0))
    assert (d / f"thumb-v{tt.NEXT_VERSION}.png").is_file()


# ----------------------------------- titles, tags, hashtags (the 10-11 plan)

_LONG, _TOOL = "over 60", "lead with the tool"
# The scripted titles the 2026-10-18 title rule holds, at 4 and 10 minutes:
# reported to the owner, never hacked (their hooks are TITLE_HOOKS, Tier 2 to change).
TITLE_HELD: dict[tuple[str, int], dict[float, str]] = {
    # the level 0/1 aside drops past 60; only these Learn titles still run
    # over at ten minutes (none renders that long today)
    ("project", 1): {600.0: _LONG},
    ("business", 4): {600.0: _LONG},
}


@pytest.mark.parametrize("key,ep", [(s.key, e) for s in ts.SERIES for e in s.episodes
                                    if e.scripted], ids=lambda x: getattr(x, "slug", x))
def test_every_scripted_title_follows_its_level_formula(key, ep):
    """docs/academy-youtube-plan.md: one formula per level, the minutes
    stated (the welcome excepted), at most 100 characters at ten minutes.
    Since 2026-10-18 (rule 30) at most 60 shown: the level 0/1 aside drops
    first, and exactly the titles in TITLE_HELD are held, never cut."""
    if key == "channel":
        assert not ep.title_hook and tut.final_title(ep, 150.0) == ep.title
        assert "over 60" in tut.title_refusal(ep, ep.title, 150.0)
        return
    assert ep.title_hook, ep.slug
    for seconds in (240.0, 600.0):
        title = tut.final_title(ep, seconds)
        got = ts.TITLE_PATTERNS[ep.formula_level].match(title)
        assert got and int(got["min"]) == tut.minutes_of(seconds), title
        assert len(title) <= tut.TITLE_MAX
        why = tut.title_refusal(ep, title, seconds)
        held = TITLE_HELD.get((key, ep.number), {}).get(seconds)
        if held is None:
            assert why is None, why
        else:
            assert why is not None and held in why, why


def test_an_episode_that_explains_takes_the_learn_formula_whatever_its_level():
    """docs/academy-youtube-plan.md rule 2: "Build Your First" is a promise
    the video must keep, so an explainer never wears it."""
    by = {(s.key, e.number): e for s in ts.SERIES for e in s.episodes}
    for key in (("agents", 2), ("local", 1), ("project", 1)):
        ep = by[key]
        assert ep.level == 1 and ep.formula_level == 2, key
        title = tut.final_title(ep, 300.0)
        assert title.startswith("Learn ") and title.endswith(" in 5 Minutes"), title
        assert "Build Your First" not in title
        # follows the formula; agents #2 and project #1 are held only for
        # not leading with the tool (2026-10-18, TITLE_HELD)
        why = tut.title_refusal(ep, title, 300.0)
        assert why is None or "lead with the tool" in why, why
        built = ts.TITLE_FORMULAS[1].format(x=ep.title_hook, min=5, benefit="")
        assert "formula" in tut.title_refusal(ep, built, 300.0)
    assert tut.final_title(by[("agents", 2)], 300.0) == (
        "Learn When to Use an AI Agent vs a Workflow in 5 Minutes")
    # a lesson that builds keeps its level's formula
    assert tut.final_title(by[("n8n", 1)], 300.0).startswith("Build Your First n8n Workflow")
    assert by[("n8n", 1)].formula_level == by[("n8n", 1)].level


def test_the_minutes_are_the_measured_length_rounded_up():
    assert [tut.minutes_of(x) for x in (178.0, 240.0, 241.0, 299.9, 300.0)] == [3, 4, 5, 5, 5]
    ep = N8N.episode(0)
    assert tut.final_title(ep, 241.0) == ("What is n8n? Explained in 5 Minutes "
                                          "(for total beginners)")
    assert "says 4 minutes" in tut.title_refusal(ep, tut.final_title(ep, 240.0), 241.0)
    assert "formula" in tut.title_refusal(ep, ep.title, 241.0)
    assert tut.final_title(ep, 0.0) == ep.title                  # never a guessed number


async def test_the_publish_gate_refuses_a_title_under_the_real_length(tmp_path, monkeypatch):
    """The title is filled from the manifest's measured length; a file that
    probes longer than the title says is refused."""
    monkeypatch.setattr(tut, "episode_dir", lambda *_: tmp_path)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: tut.Manifest(
        series="n8n", episode=0, audio="fish", seconds=239.0,
        chapters=[(0.0, "Intro"), (60.0, "A"), (120.0, "B")], parts=["intro-a", "outro-b"],
        **opened(239.0),
        body_seconds=200.0, music=tm.MUSIC_VERSION))

    async def probe(_path):
        return {"width": tut.design.W, "height": tut.design.H, "audio": True,
                "seconds": 241.0}, None
    monkeypatch.setattr(tut, "_probe", probe)
    got, why = await tut.ready("n8n", 0)
    assert got is None and "says 4 minutes" in why


@pytest.mark.parametrize("spoken,seconds,ok", [(0, 290.0, True), (0, 410.0, True),
                                               (5, 290.0, True), (6, 290.0, False),
                                               (4, 290.0, False)])
async def test_the_publish_gate_holds_the_opener_to_the_file(tmp_path, monkeypatch,
                                                           spoken, seconds, ok):
    """A render whose opener says other minutes than the file's is never
    published, whatever the manifest says. Since 2026-10-17 the opening says
    none (spoken 0) and only the title carries them."""
    monkeypatch.setattr(tut, "episode_dir", lambda *_: tmp_path)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: tut.Manifest(
        series="n8n", episode=0, audio="fish", seconds=seconds,
        chapters=[(0.0, "Intro"), (60.0, "A"), (120.0, "B")], parts=["intro-a", "outro-b"],
        **opened(seconds),
        body_seconds=200.0, music=tm.MUSIC_VERSION, spoken_minutes=spoken))

    async def probe(_path):
        return {"width": tut.design.W, "height": tut.design.H, "audio": True,
                "seconds": seconds}, None
    monkeypatch.setattr(tut, "_probe", probe)

    async def heard(_d, _m):
        return ts.opener_said(ts.series("n8n").episodes[0]), None
    monkeypatch.setattr(tut, "opener_heard", heard)
    _got, why = await tut.ready("n8n", 0)
    assert (why is None or "opener" not in why) if ok else "opener" in why


@pytest.mark.parametrize("key,heard,ok", [
    ("n8n", ("Here is the result: the n8n basics. In this lesson: what automation is, "
             "what n8n is, and a quick tour of the editor."), True),
    ("n8n", "Here's the result. The N8N basics. In this lesson...", True),
    ("n8n", ("Welcome back guys, my name is the owner and today we'll be learning "
             "about what automation is, in under five minutes."), False),   # the old opener
    ("n8n", "Here is the result. Welcome to my new channel, guys.", False),  # a welcome
    ("n8n", "Here is the result, guys.", False),
    ("n8n", "In this lesson: what automation is.", False),     # no result stated
    ("n8n", "", False),                                        # nothing heard
    ("channel", "Welcome guys to my new channel, my name is the owner.", True),
    ("channel", "Here is the result: the n8n basics.", False),
])
def test_the_opener_heard_in_the_audio_must_be_the_one_the_manifest_claims(
        key, heard, ok):
    """2026-10-13: five lessons went live saying "my new channel" because
    they were rendered before the opener rule shipped and the gate only read
    the manifest. The gate listens to the file too. Since 2026-10-17 (the
    video system's result-first opening) a lesson must be heard stating its
    result and never a welcome word; the channel welcome says "new channel"."""
    ep = ts.series(key).episodes[0]
    why = tut.heard_refusal(ep, heard)
    assert (why is None) if ok else (why is not None and heard[:20] in why)


async def test_the_publish_gate_refuses_a_file_whose_audio_says_another_opener(
        tmp_path, monkeypatch):
    monkeypatch.setattr(tut, "episode_dir", lambda *_: tmp_path)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: tut.Manifest(
        series="n8n", episode=0, audio="fish", seconds=290.0,
        chapters=[(0.0, "Intro"), (60.0, "A"), (120.0, "B")], parts=["intro-a", "outro-b"],
        **opened(290.0),
        body_seconds=200.0, music=tm.MUSIC_VERSION, spoken_minutes=5,
        # batch 9's measurements, all passing: the bed in the sting and the
        # card, 140 words a minute, the pages measured clean
        music_windows=[(6.0, 7.0), (275.0, 290.0)], pages_measured=40,
        timings=[(7.0 + 6 * i, 13.0 + 6 * i, " ".join(["word"] * 14)) for i in range(20)]))

    async def probe(_path):
        return {"width": tut.design.W, "height": tut.design.H, "audio": True,
                "seconds": 290.0, "video_codec": "h264", "profile": "High",
                "pix_fmt": "yuv420p", "fps": 30.0, "avg_fps": 30.0,
                "audio_codec": "aac", "sample_rate": 48000}, None
    monkeypatch.setattr(tut, "_probe", probe)

    async def navy(_v, _at, box):
        return bytes(tut.video_qa.NAVY_RGB) * ((box[2] - box[0]) * (box[3] - box[1]))
    monkeypatch.setattr(tut.video_qa, "frame_rgb", navy)

    async def heard(_d, _m):
        return "Welcome guys to my new channel, my name is the owner.", None
    monkeypatch.setattr(tut, "opener_heard", heard)
    got, why = await tut.ready("n8n", 0)
    assert got is None and "does not state its result" in why

    async def deaf(_d, _m):
        return None, "GROQ_API_KEY not set"
    monkeypatch.setattr(tut, "opener_heard", deaf)
    got, why = await tut.ready("n8n", 0)
    assert got is None and "could not be heard" in why     # unknown is never OK


@pytest.mark.parametrize("key,ep", [(s.key, e) for s in ts.SERIES for e in s.episodes
                                    if e.scripted], ids=lambda x: getattr(x, "slug", x))
def test_tags_are_channel_series_then_lesson_under_500_and_hashtags_at_the_end(key, ep):
    """the owner 2026-10-18 (tags-and-hashtags.md): the five channel tags, the
    series' set in the spec's phrases, 3-5 lesson tags, under 500 characters
    as YouTube counts them; 3-5 hashtags, the series' strongest three first.
    Every scripted episode produces valid metadata."""
    tags = ts.full_tags(key, ep)
    assert ts.tags_refusal(key, ep) is None, ts.tags_refusal(key, ep)
    assert tags[:5] == ts.CHANNEL_TAGS
    assert tags[5:5 + len(ts.SERIES_TAGS[key])] == ts.SERIES_TAGS[key]
    lesson = tags[5 + len(ts.SERIES_TAGS[key]):]
    assert 3 <= len(lesson) <= 5 and lesson == ts.lesson_tags(key, ep)
    assert ts.tags_cost(tags) <= tut.TAGS_MAX == ts.TAGS_LIMIT
    assert len({t.lower() for t in tags}) == len(tags)
    assert not any(re.search(r"\b20\d\d\b", t) for t in lesson)
    hashtags = ts.hashtags_for(key, ep)
    assert 3 <= len(hashtags) <= 5 and hashtags == ts.SERIES_HASHTAGS[key][:5]
    assert "#shorts" not in hashtags



def test_the_spoken_minutes_are_said_once_and_only_in_the_opener():
    for s_ in ts.SERIES:
        for ep in s_.episodes:
            if not ep.scripted:
                continue
            # 2026-10-17: the opening says no number; the title carries it
            said = " ".join([ts.opener_said(ep), ts.topic_line(ep)])
            assert "minutes" not in ts.opener_said(ep).lower(), ep.slug
            assert said.lower().count("minutes") <= 1, ep.slug
            assert not hasattr(ts, "PROMISE_SAID")


async def test_the_time_badge_is_the_measured_minutes_when_the_design_draws_it(
        fake_render, monkeypatch):
    from atlas.modules.academy import tutorial_thumbs as tt
    _d, m, why = await tut.render("n8n", 0)
    assert why is None and m.minutes == tut.minutes_of(m.seconds)
    assert m.badge == ("" if not tut.thumbnail_takes_badge() else f"{m.minutes} MIN")
    seen = {}
    real = tt.page

    def page(ep, *, level, badge, image):
        seen["badge"] = badge
        seen["doc"] = real(ep, level=level, badge=badge, image=image)
        return seen["doc"]
    monkeypatch.setattr(tt, "page", page)
    d, m, why = await tut.render("n8n", 0)
    assert why is None and seen["badge"].endswith(" MIN") and m.badge == seen["badge"]
    # drawn in sentence case (batch 7); the manifest keeps the title's form
    assert f">{tt.badge_shown(m.badge)}<" in seen["doc"] and " min<" in seen["doc"]
    assert (d / "thumb.png").is_file() and (d / "thumb.jpg").is_file()


# ------------------------------------------------- live demos (2026-10-11)

DEMO_EPISODES = {("n8n", 0), ("n8n", 1), ("claude", 1)}


def test_the_hands_on_episodes_switch_to_a_live_demo():
    for s in ts.SERIES:
        for ep in s.episodes:
            demos = [sc for sc in ep.scenes if sc.kind == "demo"]
            assert bool(demos) == ((s.key, ep.number) in DEMO_EPISODES), (s.key, ep.number)
            for sc in demos:
                demo = td.DEMOS[sc.demo]
                # one narrated line per recorded step, the step's caption under it
                assert len(sc.said) == len(demo.steps) == len(sc.items)
                assert sc.items == tuple(st.caption for st in demo.steps)
                assert sc.chapter                   # a demo is a chapter of its own
    assert {sc.demo for s in ts.SERIES for e in s.episodes for sc in e.scenes
            if sc.kind == "demo"} == set(td.DEMOS)


def test_every_demo_is_a_declarative_step_list():
    for demo in td.DEMOS.values():
        assert demo.app in ("n8n", "inspector", "terminal", "editor") and demo.steps
        for st in demo.steps:
            assert st.caption and st.acts
            assert all(a.op in td.OPS for a in st.acts), demo.key
            # never a real account: only the local instances, never claude.ai
            assert not any("claude.ai" in a.target or "anthropic.com" in a.target
                           for a in st.acts)


def test_every_recording_is_on_disk_and_matches_its_sidecar():
    import hashlib
    assert td.missing(list(td.DEMOS)) is None
    total = 0
    for key, demo in td.DEMOS.items():
        m = td.meta(key)
        data = td.video_path(key).read_bytes()
        assert hashlib.sha256(data).hexdigest() == m["sha256"] and len(data) == m["bytes"]
        assert (m["width"], m["height"]) == (td.DEMO_W, td.DEMO_H)
        assert len(m["marks"]) == len(demo.steps) == len(m["urls"])
        ends = [b for _a, b in m["marks"]]
        assert all(a < b for a, b in m["marks"]) and ends == sorted(ends)
        assert ends[-1] <= m["seconds"] + 0.01
        # nothing secret on screen or in the sidecar
        text = json.dumps(m).lower()
        assert "token" not in text and "password" not in text and "/users/" not in text
        total += len(data)
    kit = sum(p.stat().st_size for p in design.KIT.iterdir())
    assert total + kit < 120_000_000


def test_a_missing_recording_refuses_the_render(monkeypatch, tmp_path):
    monkeypatch.setattr(td, "DEMO_DIR", tmp_path)
    assert "n8n-build" in td.missing(["n8n-build"])
    assert "no demo" in td.missing(["nope"])


async def test_a_render_without_its_recordings_stops(fake_render, monkeypatch, tmp_path):
    monkeypatch.setattr(td, "DEMO_DIR", tmp_path)
    _d, _m, why = await tut.render("n8n", 1)
    assert why and "recording is missing" in why
    _d, _m, why = await tut.render("agents", 1)          # no demo in it: unaffected
    assert why is None


def test_a_demo_step_is_fitted_to_its_narration():
    assert td.fit(4.0, 6.0) == (1.0, 4.0)                 # shorter: as recorded, then held
    assert td.fit(9.0, 6.0) == (1.5, 9.0)                 # longer: faster
    speed, used = td.fit(20.0, 6.0)                      # far longer: capped, tail trimmed
    assert speed == td.MAX_SPEED and used == pytest.approx(12.0)


def test_a_demo_segment_is_cut_from_the_recording_and_cropped_to_its_step():
    step = td.Step("c", (), box=(512, 240, 1024, 576))
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4",
                            demo=("/d/demo-n8n-build.mp4", 12.5, 9.0, 1.5, step))
    i = argv.index("/d/demo-n8n-build.mp4")
    assert argv[i - 5:i - 1] == ["-ss", "12.500", "-t", "9.000"]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "setpts=(PTS-STARTPTS)/1.5000" in graph and "tpad=stop_mode=clone" in graph
    assert f"overlay={td.HOLE_X}:{td.HOLE_Y}" in graph
    demo_chain = graph.split(";")[0]
    # 2026-10-13 (rule 18): cut straight to the box in recording pixels, then
    # scaled once to fill the window (2026-10-18: the 1344x756 hole); never
    # scaled up first
    assert (f"[2:v]setpts=(PTS-STARTPTS)/1.5000,"
            f"{td.crop_filter(step, td.HOLE_W, td.HOLE_H)},fps=") in demo_chain
    assert "crop=1024:576:512:240,scale=1344:756,fps=" in demo_chain
    assert "scale=1920:1080" not in graph
    assert "-stream_loop" not in argv


def test_every_demo_step_names_the_region_it_shows():
    """Rule 18: never a full white page on a phone."""
    for demo in tut.demos.DEMOS.values():
        for step in demo.steps:
            x, y, w, h = step.box
            assert w <= 1024 and h <= 576, f"{demo.key}: {step.caption!r} shows too much"
            assert w * 9 == h * 16, f"{demo.key}: {step.caption!r} is not 16:9"
            assert x >= 0 and y >= 0 and x + w <= td.DEMO_W and y + h <= td.DEMO_H
            assert not (x % 2 or y % 2 or w % 2 or h % 2), (demo.key, step.caption)


def test_the_crop_filter_cuts_then_scales_to_the_demo_window():
    step = tut.demos.Step("c", (), box=(256, 144, 1024, 576))
    assert tut.demos.crop_filter(step, tut.demos.DEMO_W, tut.demos.DEMO_H) == (
        f"crop=1024:576:256:144,scale={tut.demos.DEMO_W}:{tut.demos.DEMO_H}")
    assert tut.demos.Step("c", ()).box == (256, 144, 1024, 576)


def test_the_crop_never_reaches_outside_the_frame():
    past = tut.demos.Step("c", (), box=(800, 400, 1024, 576))     # 1824 x 976: off it
    assert tut.demos.crop_filter(past, 1536, 864) == "crop=1024:576:512:288,scale=1536:864"
    big = tut.demos.Step("c", (), box=(-40, -20, 1920, 1080))
    assert tut.demos.crop_filter(big, 1536, 864) == "crop=1536:864:0:0,scale=1536:864"


def test_moving_a_box_renders_the_demo_again(monkeypatch):
    before = td.stamp(["mcp-inspector"])
    demo = td.DEMOS["mcp-inspector"]
    moved = td.Step(demo.steps[0].caption, demo.steps[0].acts, box=(0, 0, 1024, 576))
    monkeypatch.setitem(td.DEMOS, "mcp-inspector",
                        td.Demo(demo.key, demo.session, demo.app, demo.title,
                                (moved, *demo.steps[1:])))
    assert td.stamp(["mcp-inspector"]) != before


def test_a_change_of_picture_crossfades_inside_the_segment():
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-api.mp4",
                            bg_offset=2.0, prev_bg=("loop", "/k/loop-trigger.mp4", 13.0))
    i = argv.index("/k/loop-trigger.mp4")
    assert argv[i - 7:i - 1] == ["-stream_loop", "-1", "-ss", "3.000", "-t", f"{tut.XFADE:.3f}"]
    graph = argv[argv.index("-filter_complex") + 1]
    assert f"fade=t=out:st=0:d={tut.XFADE}:alpha=1[pb]" in graph
    assert "[bg0][pb]overlay" in graph
    # out of a demo: its last frame, cropped as it was shown, on its box, fades
    step = td.Step("c", (), box=(0, 48, 1024, 576))
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-api.mp4",
                            prev_bg=("demo", "/d/demo-x.mp4", 20.0, step))
    graph = argv[argv.index("-filter_complex") + 1]
    assert "trim=end_frame=1" in graph and "[pb]" in graph
    assert td.crop_filter(step, td.HOLE_W, td.HOLE_H) in graph
    with pytest.raises(ValueError):                 # never the whole page by default
        tut.segment_argv("/w/s.png", "/w/a.mp3", 6.0, "/w/o.mp4", bg="/k/loop-api.mp4",
                         prev_bg=("demo", "/d/demo-x.mp4", 20.0))
    # a short beat has no room for it (twice the crossfade)
    argv = tut.segment_argv("/w/s.png", "/w/a.mp3", 2 * tut.XFADE, "/w/o.mp4",
                            bg="/k/loop-api.mp4",
                            prev_bg=("loop", "/k/loop-trigger.mp4", 1.0))
    assert "/k/loop-trigger.mp4" not in argv


def test_a_demo_slide_shows_the_browser_not_a_scrim():
    sc = next(x for x in N8N.episode(1).scenes if x.kind == "demo")
    for k in range(len(sc.said)):
        doc = design.slide(sc, k, mark=N8N.mark, chapter=1, chapters=5, progress=0.3)
        assert design.STORE_SHOWN in doc and "Live demo" in doc
        assert design.TAG not in doc      # the owner 2026-10-17: off screen; disclosed via containsSyntheticMedia + description
        assert 'class="scrim"' not in doc and sc.items[k] in doc
        assert f"{k + 1}/{len(sc.said)}" in doc
        assert "localhost" in doc                       # the real address of the step
    assert "placeholder audio" in design.slide(sc, 0, mark="m", chapter=0, chapters=1,
                                               preview=True)
    term = next(x for x in MCP.episode(1).scenes if x.demo == "mcp-setup")
    assert "Terminal" in design.slide(term, 0, mark="m", chapter=0, chapters=1)


def test_a_new_recording_renders_the_episode_again(monkeypatch):
    ep = N8N.episode(1)
    before = tut.script_key(ep)
    monkeypatch.setattr(td, "stamp", lambda keys: "other")
    assert tut.script_key(ep) != before


async def test_the_preview_voice_is_male(monkeypatch, tmp_path):
    """the owner, 2026-10-11: "only use the guy voice at all times". `say`'s
    default is Samantha (female); the preview asks for Daniel."""
    import asyncio as aio
    import shutil as sh
    seen = []

    class Proc:
        async def wait(self):
            Path(seen[0][seen[0].index("-o") + 1]).write_bytes(b"x")
            return 0

    async def fake_exec(*argv, **_kw):
        seen.append(list(argv))
        return Proc()
    monkeypatch.setattr(sh, "which", lambda name: "/usr/bin/say" if name == "say" else None)
    monkeypatch.setattr(aio, "create_subprocess_exec", fake_exec)
    path, _note, why = await tut._narrate("Hello.", str(tmp_path / "x"), preview=True)
    assert why is None and path.endswith(".aiff")
    argv = seen[0]
    assert argv[0] == "say" and argv[argv.index("-v") + 1] == tut.PREVIEW_VOICE == "Daniel"


# ------------------------------- stale drafts (card #364, 2026-10-12)

@pytest.fixture
def redraft(monkeypatch, tmp_path):
    from atlas.kernel import audit, executor, state
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")   # the Fish path
    st = {"rows": [], "sql": [], "seen": [], "reviewed": [], "told": []}

    async def rows(q, *a):
        return st["rows"]

    async def run(q, *a):
        st["sql"].append((q, a))

    async def execute(req):
        st["seen"].append((req.action, req.params))
        return ActionResult(True, "ok")

    async def review(key, n):
        st["reviewed"].append((key, n))
        return "card #9 in Slack"

    async def tell(text):
        st["told"].append(text)

    async def noaudit(*a, **k):
        return None
    monkeypatch.setattr(state, "fetch_all", rows)
    monkeypatch.setattr(state, "execute_sql", run)
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(audit, "audit", noaudit)
    monkeypatch.setattr(tut, "review_tutorial", review)
    monkeypatch.setattr(tut, "_tell", tell)
    monkeypatch.setattr(tut, "reusable", lambda *_: False)
    return st


async def test_a_failed_card_is_drafted_again_with_a_fresh_render(redraft):
    redraft["rows"] = [{"series": "n8n", "episode": 1, "id": 364, "status": "failed"}]
    out = await tut.redraft_stale()
    assert redraft["seen"] == [("academy.render_tutorial", {"series": "n8n", "episode": 1})]
    assert redraft["reviewed"] == [("n8n", 1)]
    assert "was card #364" in out[0]
    # A failed card is already terminal: nothing is rewritten on it.
    assert not any("superseded" in q for q, _ in redraft["sql"])


async def test_a_waiting_card_on_an_older_render_is_superseded_then_redrafted(
        redraft, monkeypatch):
    redraft["rows"] = [{"series": "n8n", "episode": 1, "id": 370, "status": "pending"}]
    monkeypatch.setattr(tut, "read_manifest", lambda _d: type(
        "M", (), {"script": "an-older-key"})())
    await tut.redraft_stale()
    (q, a), = [x for x in redraft["sql"] if "superseded" in x[0]]
    assert "status = 'pending'" in q and a == (370,)
    assert redraft["reviewed"] == [("n8n", 1)]


async def test_a_waiting_card_on_the_current_render_is_left_alone(redraft, monkeypatch):
    redraft["rows"] = [{"series": "n8n", "episode": 1, "id": 371, "status": "pending"}]
    _s, ep = tut.resolve("n8n", 1)
    key = tut.script_key(ep)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: type("M", (), {"script": key})())
    assert await tut.redraft_stale() == []
    assert redraft["sql"] == [] and redraft["reviewed"] == []


async def test_declined_and_expired_drafts_are_not_asked_again(redraft):
    redraft["rows"] = [{"series": "n8n", "episode": 1, "id": 372, "status": "declined"},
                       {"series": "claude", "episode": 2, "id": 373, "status": "expired"}]
    assert await tut.redraft_stale() == []


async def test_a_redraft_is_tried_once_a_day(redraft, monkeypatch):
    async def refuse(req):
        return ActionResult(False, "the box is busy")
    from atlas.kernel import executor
    monkeypatch.setattr(executor, "execute", refuse)
    redraft["rows"] = [{"series": "n8n", "episode": 1, "id": 364, "status": "failed"}]
    first = await tut.redraft_stale()
    assert "re-render refused" in first[0] and len(redraft["told"]) == 1
    assert await tut.redraft_stale() == []
    assert len(redraft["told"]) == 1


# ------------------------------------- the voice: feelings, cues, human words
# the owner, 2026-10-12: "the voice should have feelings and laughter and make
# jokes ... don't make him sound like a robot" and "make it like a human
# talking, not an AI".

def _all_said(ep):
    """Every line the render voices, as written (cues in): the opening (the
    result line, voiced with its cue, and the scope line), then the lesson."""
    head = [ts.opener_voiced(ep)]
    if ep.opener_topic:
        head.append(ts.opening_lines(ep)[1])
    return head + [line for sc in tut.opening(ep) + ep.scenes for line in sc.said]


BANNED_PHRASES = ("delve", "in today's fast-paced world", "fast-paced world", "seamless",
                  "leverage", "unlock", "game-changer", "game changer",
                  "it's important to note", "important to note", "let's dive in",
                  "dive in", "in conclusion")


def test_no_line_says_an_ai_sounding_phrase():
    fixed = [ts.INTRO_SAID,
             *(ts.opener_said(e) for s_ in ts.SERIES for e in s_.episodes if e.scripted)]
    for s_ in ts.SERIES:
        for ep in s_.episodes:
            for line in [*_all_said(ep), ep.topic_said, *fixed]:
                low = line.lower()
                for phrase in BANNED_PHRASES:
                    assert phrase not in low, (s_.key, ep.number, phrase, line)


def test_only_known_cues_are_written_and_plain_takes_them_all_out():
    for s_ in ts.SERIES:
        for ep in s_.episodes:
            for line in _all_said(ep):
                assert "[" not in ts.plain(line) and "]" not in ts.plain(line), line
                assert "  " not in ts.plain(line) and " ." not in ts.plain(line), line
    assert ts.plain("[excited] Hello, Ana! [break] Done. [chuckling]") == (
        "Hello, Ana! Done.")
    assert ts.plain("uv add mcp[cli] [happy]") == "uv add mcp[cli]"   # never eats words
    assert ts.cues("[sighing] a [laughing softly] b") == ["sighing", "laughing softly"]


@pytest.mark.parametrize("key,n", [(s.key, e.number) for s in ts.SERIES
                                   for e in s.episodes if e.scripted])
def test_no_cue_reaches_anything_shown_counted_or_timed(key, n):
    s_ = ts.series(key)
    ep = s_.episode(n)
    cue = re.compile(r"\[(" + "|".join(map(re.escape, ts.CUE_WORDS)) + r")\]")
    shown = [design.topic(ep, minutes=5), design.intro(),
             design.end_card(ep, ts.next_up(key, ep), shop=True),
             tut.description(s_, ep, CH, 5), tut.final_title(ep, 290.0),
             tut.thumbnail_doc(ep, s_, "5 MIN")]
    scenes = tut.opening(ep) + ep.scenes
    chapters = ts.chapters_of(ep)
    for c, sc in enumerate(scenes):
        for k in range(len(sc.said)):
            shown.append(design.slide(sc, k, mark=s_.mark, chapter=min(c, max(chapters)),
                                      chapters=max(chapters) + 1))
    for doc in shown:
        assert not cue.search(doc), (key, n, cue.search(doc).group(0))
    plan = tut.beats(scenes)
    assert all(not cue.search(b.said) for b in plan)
    assert ts.words(ep) == sum(len(ts.plain(x).split()) for sc in ep.scenes
                               for x in sc.said)
    # the estimate counts words, never cues: the same with them stripped
    bare = ts.Episode(**{**ep.__dict__, "scenes": tuple(
        ts.Scene(**{**sc.__dict__, "said": tuple(map(ts.plain, sc.said))})
        for sc in ep.scenes)})
    assert tut.estimate_seconds(bare) == tut.estimate_seconds(ep)


@pytest.mark.parametrize("key,n", [(s.key, e.number) for s in ts.SERIES
                                   for e in s.episodes if e.scripted])
def test_the_delivery_is_human_and_sparse(key, n):
    """A warm opener, a light joke now and then with a soft laugh after it,
    a sigh on a pain point, a cheer on a win: sparse, never on every line,
    and never a laugh where the lesson is about safety or money."""
    ep = ts.series(key).episode(n)
    lines = _all_said(ep)
    used = [c for line in lines for c in ts.cues(line)]
    assert ts.opener_voiced(ep).startswith(("[confident] ", "[excited] "))
    assert len(used) >= 4, (key, n, used)
    assert len(used) * 2 <= len(lines), (key, n, len(used), len(lines))
    assert all(len(ts.cues(line)) <= 1 for line in lines)
    for sc in tut.opening(ep) + ep.scenes:
        serious = re.search(r"safe|security|refund|money|legal|keep", sc.heading + " "
                            + sc.chapter, re.IGNORECASE)
        if serious:
            for line in sc.said:
                assert not {"chuckling", "laughing", "laughing softly"} & set(
                    ts.cues(line)), (key, n, line)


def test_cues_reach_only_an_s2_voice(monkeypatch):
    line = "[excited] And there it is. [chuckling]"
    assert tut.voiced(line, "s2.1-pro") == line and tut.voiced(line, "s2-pro") == line
    assert tut.voiced(line, "s1") == "And there it is." == tut.voiced(line, None)
    assert tut.fish_model() == "s2.1-pro"
    monkeypatch.setenv("ACADEMY_TUTORIALS_FISH_MODEL", "s1")
    assert tut.fish_model() == "s1"


@pytest.mark.parametrize("model,want", [("", "[excited] And there it is."),
                                        ("s1", "And there it is.")])
async def test_the_narration_sends_cues_to_s2_and_strips_them_for_s1(
        monkeypatch, tmp_path, model, want):
    from atlas.modules.content import stills
    seen = {}

    async def narration(text, speed=None, model=None):
        seen.update(text=text, model=model)
        return b"mp3", "Fish", None
    monkeypatch.setattr(stills, "narration", narration)
    monkeypatch.setenv("ACADEMY_TUTORIALS_FISH_MODEL", model)
    _p, _n, why = await tut._narrate("[excited] And there it is.", str(tmp_path / "a"),
                                     False)
    assert why is None and seen["text"] == want
    assert seen["model"] == (model or "s2.1-pro")


async def test_the_preview_voice_is_daniel_and_never_hears_a_cue(monkeypatch, tmp_path):
    import asyncio
    import shutil
    argv = []

    class Proc:
        async def wait(self):
            return 1

    async def exec_(*a, **kw):
        argv.extend(a)
        return Proc()
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/say")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", exec_)

    async def run(a, timeout=0):
        return b""
    from atlas.modules.content import ffmpeg
    monkeypatch.setattr(ffmpeg, "run", run)
    await tut._narrate("[chuckling] Hello there.", str(tmp_path / "a"), True)
    assert argv[:3] == ["say", "-v", "Daniel"] and argv[-1] == "Hello there."


def test_the_model_header_is_the_tutorials_own_and_the_reels_keep_theirs(monkeypatch):
    from atlas.modules._fish import fish_request
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "k")
    monkeypatch.delenv("FISH_AUDIO_MODEL", raising=False)
    assert fish_request("hi")[1]["model"] == "s1"              # phone, menu, reels
    assert fish_request("hi", model="s2.1-pro")[1]["model"] == "s2.1-pro"
    monkeypatch.setenv("REELS_FISH_VOICE_ID", "v1")
    a = tut.cache_key("intro", "x", False)
    monkeypatch.setenv("ACADEMY_TUTORIALS_FISH_MODEL", "s1")
    assert a != tut.cache_key("intro", "x", False)              # a new model: new clip
    key = tut.script_key(N8N.episode(1))
    monkeypatch.setenv("ACADEMY_TUTORIALS_FISH_MODEL", "s2-pro")
    assert key != tut.script_key(N8N.episode(1))


@pytest.mark.parametrize("key,n", [(s.key, e.number) for s in ts.SERIES
                                   for e in s.episodes if e.scripted])
def test_every_episode_keeps_its_chapters_and_the_chapter_rail(key, n):
    """YouTube chapters need 3 or more; every slide carries the rail, one
    tick per chapter, and each chapter opens on its own heading -- the
    channel welcome too."""
    s_ = ts.series(key)
    ep = s_.episode(n)
    chapters = ts.chapters_of(ep)
    count = max(chapters) + 1
    assert count >= 3, (key, n, count)
    for sc, c in zip(ep.scenes, chapters, strict=True):
        for k in range(len(sc.said)):
            doc = design.slide(sc, k, mark=s_.mark, chapter=c, chapters=count)
            assert 'class="rail"' in doc and doc.count('class="tick ') == count
        if sc.chapter:
            import html as html_
            first = design.slide(sc, 0, mark=s_.mark, chapter=c, chapters=count)
            assert html_.escape(sc.heading) in first, (key, n, sc.heading)


async def test_a_redraft_cut_short_by_a_restart_is_tried_again(redraft):
    redraft["rows"] = [{"series": "n8n", "episode": 1, "id": 364, "status": "failed"}]
    await tut.redraft_stale()
    await tut.redraft_stale()
    assert redraft["reviewed"] == [("n8n", 1), ("n8n", 1)]


def test_the_caption_track_is_the_script_without_cues_in_srt_order():
    ep = ts.series("n8n").episodes[0]
    m = tut.Manifest(series="n8n", episode=0, audio="fish", seconds=290.0,
                     timings=[(0.0, 4.2, "[happy] Welcome back, guys!"),
                              (4.2, 9.0, "Today we build a workflow."),
                              (9.0, 12.0, "Find us at agency dot one eight d n dot com.")])
    srt = tut.caption_track(m, ep)
    assert srt.startswith("1\n00:00:00,000 --> 00:00:04,200\nWelcome back, guys!\n\n2\n")
    assert "[happy]" not in srt and "00:00:09,000" in srt
    assert "agency.example.com" in srt and " dot " not in srt


async def test_a_render_without_timings_uploads_no_empty_caption_track(
        youtube, monkeypatch):
    async def no_captions(*a, **k):
        raise AssertionError("an empty track must never be uploaded")
    monkeypatch.setattr(api, "set_captions", no_captions)
    result = await tut.publish_tutorial("n8n", 1)
    assert "captions NOT set" in result.detail and "render again" in result.detail
    assert youtube["written"][-1][1][6] is False          # captions_set stays false


async def test_a_render_with_timings_uploads_its_caption_track(youtube, monkeypatch):
    youtube["manifest"].timings = [(0.0, 3.0, "[happy] Welcome back!")]
    got = {}

    async def captions(ch, vid, srt, **k):
        got.update(vid=vid, srt=srt)
        return {"id": "cap1"}, None
    monkeypatch.setattr(api, "set_captions", captions)
    result = await tut.publish_tutorial("n8n", 1)
    assert "captions set" in result.detail
    assert got["vid"] == "abcdefghijk" and "Welcome back!" in got["srt"]
    assert youtube["written"][-1][1][6] is True           # captions_set recorded


async def test_captions_are_not_tried_without_force_ssl(youtube, monkeypatch):
    """Final review M4: the caption upload needs youtube.force-ssl; without
    it nothing is called and the card says the owner must re-consent."""
    youtube["manifest"].timings = [(0.0, 3.0, "Welcome back!")]
    youtube["scopes"] = {"https://www.googleapis.com/auth/youtube"}

    async def captions(*a, **k):
        raise AssertionError("no caption call without force-ssl")
    monkeypatch.setattr(api, "set_captions", captions)
    result = await tut.publish_tutorial("n8n", 1)
    assert ("captions NOT set: the token lacks youtube.force-ssl; the owner must "
            "re-consent") in result.detail
    assert youtube["written"][-1][1][6] is False


async def test_captions_are_not_tried_when_the_scopes_cannot_be_read(
        youtube, monkeypatch):
    youtube["manifest"].timings = [(0.0, 3.0, "Welcome back!")]

    async def unreadable(ch):
        return None, "clips: tokeninfo: timed out"

    async def captions(*a, **k):
        raise AssertionError("unknown scopes are never OK")
    monkeypatch.setattr(api, "token_scopes", unreadable)
    monkeypatch.setattr(api, "set_captions", captions)
    result = await tut.publish_tutorial("n8n", 1)
    assert ("captions NOT set: the token's scopes could not be read "
            "(clips: tokeninfo: timed out)") in result.detail


def test_a_quiz_is_asked_unanswered_then_counted_down_then_revealed():
    """Rule 19: the tick never shares a frame with "comment your answer"."""
    scene = ts.Scene(chapter="", kind="quiz", heading="What breaks stdio?",
                     said=("Which one?", "A print statement."),
                     items=("A print statement", "A docstring", "A type hint"),
                     caption="print() writes to stdout.", answer=0)
    asked = tut.design._quiz(scene, 0, None)
    held = tut.design._quiz(scene, 0, 3)
    told = tut.design._quiz(scene, 1, None)
    assert "✓" not in asked and "class=\"answer\"" not in asked
    assert "Comment your answer" in asked
    assert "✓" not in held and "lit" in held
    assert "✓" in told and "print() writes to stdout." in told
    assert "Comment your answer" not in told
    assert "Tell me in the comments" in told
    beats = tut.beats((scene,))
    kinds = [(b.step, b.countdown) for b in beats]
    assert kinds[:5] == [(0, None), (0, 3), (0, 2), (0, 1), (1, None)]


def test_a_poll_keeps_its_comment_prompt_on_the_reveal():
    poll = ts.Scene(chapter="", kind="quiz", heading="A or B?", said=("Pick.", "Thanks."),
                    items=("A thing", "B thing"), answer=-1)
    assert "Comment A or B" in tut.design._quiz(poll, 1, None)


def test_every_graded_quiz_has_a_question_line_and_a_reveal_line():
    bad = [s_.heading for ser in ts.SERIES for ep in ser.episodes for s_ in ep.scenes
           if s_.kind == "quiz" and s_.answer >= 0 and len(s_.said) < 2]
    assert not bad, bad


# 2026-10-13 (growth plan, task 6, rule 20; fix round 1): the recap's last
# sentence names the next episode by its opener topic, replacing the
# recap's own "Next ..." sentence, so "next" is said once.
def _recap(ep):
    return next((sc for sc in ep.scenes if sc.chapter == "Recap"), None)


def test_every_recap_ends_by_naming_the_next_episode():
    """Fix round 2: the next episode, scripted or planned, by its opener
    topic; no subscribe clause (the outro asks); "next" said once."""
    for s in ts.SERIES:
        eps = list(s.episodes)
        for a, b in itertools.pairwise(eps):
            recap = _recap(a)
            if not a.scripted or recap is None:   # project #1 has no recap chapter
                continue
            last = ts.plain(recap.said[-1])
            short = ts.SHORT_NEXT.get((s.key, a.number), "")
            topic = short or (b.opener_topic.split(", ")[0]
                              if (s.key, a.number) in ts.SHORT_NEXT else b.opener_topic)
            assert last.endswith(f"Next time: {topic}."), (a.slug, last)
            assert last.endswith(ts.next_line(s.key, a)), a.slug
            assert last.lower().count("next") <= 1, (a.slug, last)
            assert "subscribe" not in last.lower(), a.slug


def test_no_recap_says_a_playlist_or_the_series_is_over():
    for s in ts.SERIES:
        for e in s.episodes:
            recap = _recap(e)
            if not e.scripted or recap is None:
                continue
            last = ts.plain(recap.said[-1])
            assert len(recap.said) == len(recap.items), e.slug
            assert "&" not in last and (not s.playlist or s.playlist not in last), e.slug
            # every series has planned episodes today: none is a true last one
            assert ts.LAST_SAID not in last, e.slug


def test_a_true_last_episode_closes_the_series():
    s = ts.series("claude")
    assert ts._next_said(None) == "That's the whole series."
    last = s.episodes[-1]
    assert ts._later(s.episodes, last) is None


def test_the_owners_closing_sentences_stay():
    kept = {("n8n", 1): "That's a real automation, and you built it.",
            ("business", 1): "Every lead, answered fast.",
            ("business", 2): "Run it by hand for a week and read every message "
                             "before you let it run alone."}
    for (key, n), sentence in kept.items():
        last = _recap(ts.series(key).episode(n)).said[-1]
        assert sentence in ts.plain(last), (key, n)
    n8n1 = _recap(ts.series("n8n").episode(1)).said[-1]
    assert "[happy] That's a real automation" in n8n1          # its cue too
    claude2 = ts.plain(_recap(ts.series("claude").episode(2)).said[-1])
    assert "See you in the next one" not in claude2            # it pointed ahead


def test_only_the_listed_episodes_say_the_short_line():
    claude = ts.series("claude")
    assert ts.next_line("claude", claude.episodes[0]) == (
        f"Next time: {claude.episodes[1].opener_topic}.")
    assert ts.next_line("claude", claude.episodes[1]) == (
        "Next time: adding MCP servers to Claude Code.")
    assert ts.next_line("n8n", ts.series("n8n").episode(2)) == (
        "Next time: managing your workflows.")


def test_titling_twice_changes_nothing():
    for s in ts.SERIES[1:]:
        assert ts.titled(s.key, s.episodes, playlist=s.playlist) == s.episodes, s.key


def test_the_heard_opening_reads_here_s_and_curly_quotes_as_here_is():
    """Whisper writes "Here's" as often as "Here is", with a straight or a
    curly apostrophe; all three state the result."""
    ep = next(e for s in ts.SERIES for e in s.episodes if e.scripted and e.opener_topic)
    for said in ("Here is the result: it.", "Here's the result: it.",
                 "Here\u2019s the result: it.", "HERE IS THE RESULT"):
        assert tut.heard_refusal(ep, said) is None, said
    assert tut.heard_refusal(ep, "Here, the result.") is not None


# ------------------------------------------- the relaunch (growth plan task 9)
#
# 2026-10-13: five lessons went public before the "in under N minutes"
# opener. YouTube cannot replace a file, so the remedy is a new upload under
# today's render and the old video private -- never deleted (Tier 3).

class _Recorder:
    """An async stand-in that records every call's arguments."""

    def __init__(self, result: object = None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args: object, **kwargs: object) -> object:
        self.calls.append(args + tuple(sorted(kwargs.items())))
        return self.result


def _relaunch_ready(ok: bool = True, *, script: str | None = None,
                    timings: bool = True):
    s, ep = ts.series("claude"), ts.series("claude").episodes[1]
    m = tut.Manifest(series="claude", episode=1, audio="fish", seconds=301.0,
                     script=script if script is not None else tut.script_key(ep),
                     timings=[(0.0, 1.0, "hi")] if timings else [])

    async def ready(_s, _n):
        if not ok:
            return None, "the render is of an older script; render it again"
        return (s, ep, Path("/nowhere"), m, "text"), None
    return ready


@pytest.fixture
def relaunch(monkeypatch):
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")   # the Fish path
    from atlas.kernel import state as kstate
    st: dict = {"rows": [{"id": 7, "video_id": "OLD1", "public": True,
                          "withdrawn": False, "playlist_id": "PLOLD",
                          "item_id": "ITOLD"}],
                "privacy": [], "sql": _Recorder(), "out": _Recorder((True, None)),
                "comments": _Recorder([{"id": 31}])}

    async def rows(_s, _n):
        return st["rows"]

    async def set_privacy(ch, vid, privacy, **kw):
        st["privacy"].append((vid, privacy))
        st.setdefault("privacy_kw", []).append(kw)
        return {}, None

    publish = _Recorder(ActionResult(
        True, "t: uploaded NEW1 -- https://youtu.be/NEW1",
        evidence={"video_id": "NEW1"},
        rollback_token=json.dumps({"channel": "clips", "video_id": "NEW1",
                                   "item_id": "ITNEW"})))
    st["publish"] = publish
    monkeypatch.setattr(tut, "ready", _relaunch_ready())
    monkeypatch.setattr(tut, "_episode_rows", rows)
    monkeypatch.setattr(tut, "_publish", publish)
    monkeypatch.setattr(api, "take_out_of_playlist", st["out"])
    monkeypatch.setattr(api, "set_privacy", set_privacy)
    monkeypatch.setattr(kstate, "execute_sql", st["sql"])
    monkeypatch.setattr(kstate, "fetch_all", st["comments"])
    return st


async def test_relaunch_makes_the_old_video_private_and_never_deletes(relaunch):
    r = await tut.relaunch_tutorial("claude", 1)
    assert r.ok, r.detail
    assert relaunch["publish"].calls == [("claude", 1, ("fresh", True), ("strict", False))]
    assert relaunch["privacy"] == [("OLD1", "private")]
    assert relaunch["out"].calls == [(tut.channel(), "ITOLD")]   # out of its playlist
    assert "OLD1" in r.detail and "NEW1" in r.detail
    sql = str(relaunch["sql"].calls)
    assert "withdrawn = true" in sql and "relaunched as NEW1" in sql and "DELETE" not in sql
    assert "coalesce(detail, '')" in sql
    assert relaunch["sql"].calls[0][1:] == (7, " -- relaunched as NEW1", None)
    assert "academy_tutorial_comments" in str(relaunch["comments"].calls)
    undo = json.loads(r.rollback_token)
    assert undo == {"channel": tut.channel(), "old_video_id": "OLD1",
                    "new_video_id": "NEW1", "old_row_id": 7,
                    "new_item_id": "ITNEW", "comment_ids": [31],
                    "old_playlist_id": "PLOLD", "old_item_id": "ITOLD"}


async def test_relaunch_finishes_a_pending_private_upload_not_a_third(relaunch):
    relaunch["rows"] = [{"id": 9, "video_id": "NEW1", "public": False,
                         "withdrawn": False},
                        {"id": 7, "video_id": "OLD1", "public": True,
                         "withdrawn": False, "item_id": None}]
    dry = await tut.relaunch_tutorial_dry("claude", 1)
    assert dry.ok and "would finish NEW1" in dry.detail
    r = await tut.relaunch_tutorial("claude", 1)
    assert r.ok, r.detail
    assert relaunch["publish"].calls == [("claude", 1, ("fresh", False), ("strict", False))]
    assert relaunch["privacy"] == [("OLD1", "private")]
    assert relaunch["sql"].calls[0][1] == 7 and "withdrawn = true" in relaunch["sql"].calls[0][0]


async def test_a_half_finished_relaunch_with_two_public_videos_is_refused(relaunch):
    relaunch["rows"] = [{"id": 9, "video_id": "NEW1", "public": True, "withdrawn": False},
                        {"id": 7, "video_id": "OLD1", "public": True, "withdrawn": False}]
    r = await tut.relaunch_tutorial("claude", 1)
    assert not r.ok and "NEW1" in r.detail and "OLD1" in r.detail
    assert relaunch["publish"].calls == []


async def test_the_old_video_failing_to_go_private_keeps_the_undo(relaunch, monkeypatch):
    async def set_privacy(ch, vid, privacy, **kw):
        return None, "403"
    monkeypatch.setattr(api, "set_privacy", set_privacy)
    r = await tut.relaunch_tutorial("claude", 1)
    assert not r.ok and "both are public" in r.detail
    assert json.loads(r.rollback_token)["new_video_id"] == "NEW1"
    assert relaunch["sql"].calls == []


async def test_relaunch_dry_names_both_and_deletes_nothing(relaunch):
    r = await tut.relaunch_tutorial_dry("claude", 1)
    assert r.ok, r.detail
    assert "OLD1" in r.detail and "private" in r.detail and "nothing is deleted" in r.detail
    assert "5 minutes" in r.detail or "301 s" in r.detail
    assert relaunch["publish"].calls == [] and relaunch["privacy"] == []


async def test_relaunch_refuses_when_the_old_video_is_not_public(relaunch):
    relaunch["rows"] = [{"id": 7, "video_id": "OLD1", "public": False,
                         "withdrawn": False}]
    r = await tut.relaunch_tutorial("claude", 1)
    assert not r.ok and "not public" in r.detail
    relaunch["rows"] = [{"id": 7, "video_id": "OLD1", "public": True, "withdrawn": True}]
    r = await tut.relaunch_tutorial("claude", 1)
    assert not r.ok
    relaunch["rows"] = []
    r = await tut.relaunch_tutorial("claude", 1)
    assert not r.ok
    assert relaunch["publish"].calls == [] and relaunch["privacy"] == []


def _relaunch_ready_nlm(*, sha: str | None = None, timings: bool = True):
    """A NotebookLM render as notebooklm._install writes it: no script_key,
    the source_sha256 of the text it was made from."""
    from atlas.modules.academy import notebooklm
    s, ep = ts.series("claude"), ts.series("claude").episodes[1]
    m = tut.Manifest(series="claude", episode=1, audio=notebooklm.AUDIO, seconds=301.0,
                     source_sha256=sha if sha is not None else notebooklm.source_sha(ep),
                     timings=[(0.0, 1.0, "hi")] if timings else [])

    async def ready(_s, _n):
        return (s, ep, Path("/nowhere"), m, "text"), None
    return ready


async def test_relaunch_takes_a_notebooklm_render_of_todays_script(relaunch, monkeypatch):
    monkeypatch.setattr(tut, "ready", _relaunch_ready_nlm())
    r = await tut.relaunch_tutorial("claude", 1)
    assert r.ok, r.detail
    assert relaunch["publish"].calls == [("claude", 1, ("fresh", True), ("strict", False))]


@pytest.mark.parametrize("ready", [_relaunch_ready_nlm(sha="0" * 64),
                                   _relaunch_ready_nlm(sha=""),
                                   _relaunch_ready_nlm(timings=False)])
async def test_relaunch_refuses_a_notebooklm_render_off_the_script(relaunch, monkeypatch,
                                                                   ready):
    monkeypatch.setattr(tut, "ready", ready)
    r = await tut.relaunch_tutorial("claude", 1)
    assert not r.ok and "render it again first" in r.detail
    assert relaunch["publish"].calls == [] and relaunch["privacy"] == []


@pytest.mark.parametrize("ready", [_relaunch_ready(False), _relaunch_ready(script=""),
                                   _relaunch_ready(timings=False)])
async def test_relaunch_refuses_a_render_older_than_the_script(relaunch, monkeypatch,
                                                               ready):
    monkeypatch.setattr(tut, "ready", ready)
    for fn in (tut.relaunch_tutorial, tut.relaunch_tutorial_dry):
        r = await fn("claude", 1)
        assert not r.ok and "render it again first" in r.detail
    assert relaunch["publish"].calls == [] and relaunch["privacy"] == []


async def test_a_failed_new_upload_leaves_the_old_video_alone(relaunch):
    relaunch["publish"].result = ActionResult(False, "upload refused: quota")
    r = await tut.relaunch_tutorial("claude", 1)
    assert not r.ok and "quota" in r.detail
    assert relaunch["privacy"] == [] and relaunch["sql"].calls == []


async def test_relaunch_rollback_restores_both_and_unwithdraws(monkeypatch):
    from atlas.kernel import state as kstate
    privacy, sql = [], _Recorder()

    async def set_privacy(ch, vid, p, **kw):
        privacy.append((vid, p))
        return {}, None
    out = _Recorder((True, None))
    back = _Recorder(({"item_id": "ITBACK"}, None))
    monkeypatch.setattr(api, "set_privacy", set_privacy)
    monkeypatch.setattr(api, "take_out_of_playlist", out)
    monkeypatch.setattr(api, "add_playlist_item", back)
    monkeypatch.setattr(kstate, "execute_sql", sql)
    token = json.dumps({"channel": "clips", "old_video_id": "OLD1",
                        "new_video_id": "NEW1", "old_row_id": 7,
                        "new_item_id": "ITNEW", "comment_ids": [31],
                        "old_playlist_id": "PLOLD", "old_item_id": "ITOLD"})
    r = await tut.unrelaunch_tutorial(token)
    assert r.ok, r.detail
    assert privacy == [("NEW1", "private"), ("OLD1", "public")]
    assert out.calls == [("clips", "ITNEW")]
    assert back.calls == [("clips", "PLOLD", "OLD1")]
    assert any(c[1:] == (7, "ITBACK") and "item_id" in c[0] for c in sql.calls)
    text = str(sql.calls)
    assert "withdrawn = false" in text and "DELETE" not in text
    assert any(7 in c for c in sql.calls) and any("NEW1" in c for c in sql.calls)


async def test_fresh_false_still_refuses_an_already_public_episode(youtube):
    youtube["row"] = {"id": 5, "video_id": "abcdefghijk", "public": True}
    r = await tut.publish_tutorial("n8n", 1)
    assert not r.ok and "already public" in r.detail
    assert youtube["steps"] == []


async def test_fresh_uploads_a_second_video_under_its_own_claim(youtube, monkeypatch):
    youtube["row"] = {"id": 5, "video_id": "abcdefghijk", "public": True}
    keys = []

    async def claim(action, key):
        keys.append(key)
        return "claimed"

    async def rows(_s, _n):           # a rolled-back relaunch left row 12 withdrawn
        return [{"id": 12, "withdrawn": True}, {"id": 5, "withdrawn": False}]
    monkeypatch.setattr("atlas.kernel.idempotency.claim", claim)
    monkeypatch.setattr(tut, "_episode_rows", rows)
    r = await tut._publish("n8n", 1, strict=False, fresh=True)
    assert r.ok, r.detail
    assert youtube["steps"][0] == ("upload", "private")
    assert youtube["written"][0] == ("insert", ("n8n", 1, "abcdefghijk"))
    assert keys == ["tutorial:n8n:1:fresh:12"]


async def test_a_rolled_back_relaunch_can_be_tried_again(youtube, monkeypatch):
    """The gate's key moves past the withdrawn row a rollback leaves, so a
    second relaunch is not refused as already sent."""
    from atlas.kernel import idempotency
    spent: set[str] = set()

    async def claim(action, key):
        if key in spent:
            return idempotency.SENT
        spent.add(key)
        return idempotency.CLAIMED
    table = [{"id": 5, "withdrawn": False}]

    async def rows(_s, _n):
        return sorted(table, key=lambda x: -x["id"])
    monkeypatch.setattr(idempotency, "claim", claim)
    monkeypatch.setattr(tut, "_episode_rows", rows)
    youtube["row"] = {"id": 5, "video_id": "abcdefghijk", "public": True}
    assert (await tut._publish("n8n", 1, strict=False, fresh=True)).ok
    table.append({"id": 6, "withdrawn": True})            # the first, rolled back
    again = await tut._publish("n8n", 1, strict=False, fresh=True)
    assert again.ok, again.detail
    assert spent == {"tutorial:n8n:1:fresh:5", "tutorial:n8n:1:fresh:6"}


def test_relaunch_is_a_tier_2_card_with_closed_params():
    from atlas.modules.academy import MODULE
    a = {x.name: x for x in MODULE.actions()}["academy.relaunch_tutorial"]
    assert a.tier is Tier.ASK and a.card_only and a.verbose_card
    assert set(a.params) == {"series", "episode"} and a.vet is tut.vet
    assert a.rollback is tut.unrelaunch_tutorial


# ------------------------------------------------- background music (rule 22)

async def test_every_render_carries_the_music_bed_after_the_final_join(fake_render):
    """Rule 22: the joined file is mixed once, after the opener settles; the
    bed is the series' own. Since the owner 2026-10-17 (narration.md "Delivery")
    it sounds only in the sting and end-card windows; until then (10-13:
    "add background music in the intro and the ending clip and in
    between") it ran under the whole file."""
    d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    assert m.music == tm.MUSIC_VERSION
    mixes = [a for a in fake_render["argv"] if "-filter_complex" in a
             and "sidechaincompress" in a[a.index("-filter_complex") + 1]]
    joins = [i for i, a in enumerate(fake_render["argv"]) if "concat" in a]
    # every join is mixed at once
    assert len(mixes) == len(joins) == 1
    mix = mixes[-1]
    assert mix[mix.index("-i") + 1] == str(d / "video.mp4.dry.mp4")
    assert mix[-1] == str(d / "video.mp4")
    assert Path(mix[mix.index("-i", mix.index("-i") + 1) + 1]) == tm.bed_path(
        m.seconds, "n8n")
    # rule 22 since 2026-10-17: the bed sounds under the sting and the end
    # card only; the manifest records where, from the render's own timings
    assert m.music_windows == tut.music_spans(m.seconds, card_seconds=m.card_seconds,
                                              sting=(6.0, 7.0))
    assert m.music_windows[-1] == (m.card_at, m.seconds)    # the EndCard's span
    assert m.music_windows == tut.music_windows(m)
    # the sting's window is where the sting plays: 6-7 s (the video system)
    assert m.music_windows[0] == (design.STING_AT, design.STING_AT + design.STING_SECONDS)
    assert m.music_windows[0] == (6.0, 7.0)
    assert tm.envelope(m.music_windows) in mix[mix.index("-filter_complex") + 1]
    assert not (d / "video.mp4.dry.mp4").exists() and (d / "video.mp4").is_file()
    # each join is followed by its mix, the last one after the last join
    at = [i for i, a in enumerate(fake_render["argv"]) if a in mixes]
    # (the first mix renders the bed into the cache in between)
    assert all(j < i < k for j, i, k in zip(joins, at, [*joins[1:], 10**6],
                                            strict=True))


async def test_a_minute_the_mix_rolls_over_into_the_title_not_the_opening(
        fake_render, monkeypatch):
    """Fix round 1 (2026-10-13): the AAC mix adds ~18 ms, which can roll a
    minute over. Since 2026-10-17 the opening says no number, so nothing is
    re-voiced: the title's minutes are read from the mixed file."""
    from atlas.modules.content import ffmpeg
    mixed: list[str] = []

    async def add_bed(video, series_key, out, **_kw):
        mixed.append(video)
        Path(out).touch()
        return 0.0

    async def duration(path):
        return 300.01                      # 4:59.99 joined, 5:00.01 mixed
    monkeypatch.setattr(tm, "add_bed", add_bed)
    monkeypatch.setattr(ffmpeg, "duration", duration)
    _d, m, why = await tut.render("n8n", 1)
    assert why is None, why
    assert len(mixed) == 1 and len(fake_render["openings"]) == 1
    assert m.minutes == 6 == tut.minutes_of(m.seconds) and m.spoken_minutes == 0


async def test_a_preview_carries_the_bed_too(fake_render):
    d, m, why = await tut.render("n8n", 1, preview=True)
    assert why is None, why
    assert m.music == tm.MUSIC_VERSION and (d / "video.PREVIEW.mp4").is_file()


def test_the_music_windows_come_from_the_render_timings():
    """Rule 22 (the owner, 2026-10-17): the sting at 6-7 s and the EndCard, the
    last card_seconds of the file; a render made before then is read the
    way it was mixed."""
    sting = (6.0, 7.0)
    assert tut.music_spans(300.0, card_seconds=15.0, sting=sting) == [sting, (285.0, 300.0)]
    assert tut.music_spans(300.0, card_seconds=18.4, sting=sting) == [
        sting, (281.6, 300.0)]
    # the welcome has no sting: only the end card
    assert tut.music_spans(300.0, card_seconds=15.0, sting=None) == [(285.0, 300.0)]
    m = tut.Manifest(series="n8n", episode=1, audio="fish", seconds=300.0,
                     **opened(300.0, "n8n", 1))
    assert tut.music_windows(m) == [sting, (285.0, 300.0)]
    m.opening = "welcome"
    assert tut.music_windows(m) == [(285.0, 300.0)]
    # renders from before 2026-10-17: the sting at 0 s, the owner's 5 s 3D card or
    # the narrated ending (at most 15 s)
    old = tut.Manifest(series="n8n", episode=1, audio="fish", seconds=300.0,
                       outro_seconds=14.0, presenter=["intro:aaa", "outro:bbb"])
    assert tut.music_windows(old) == [(0.0, design.STING_SECONDS), (295.0, 300.0)]
    old.presenter = ["intro:aaa"]
    assert tut.music_windows(old) == [(0.0, design.STING_SECONDS), (286.0, 300.0)]


def test_an_older_manifest_without_music_windows_still_reads(tmp_path):
    import json as _json
    raw = {"series": "n8n", "episode": 1, "audio": "fish", "seconds": 300.0,
           "chapters": [[0.0, "Intro"]], "music": tm.MUSIC_VERSION}
    (tmp_path / "manifest.json").write_text(_json.dumps(raw))
    m = tut.read_manifest(tmp_path)
    assert m is not None and m.music_windows == []
    raw["music_windows"] = [[0.0, 1.0], [295.0, 300.0]]
    (tmp_path / "manifest.json").write_text(_json.dumps(raw))
    assert tut.read_manifest(tmp_path).music_windows == [(0.0, 1.0), (295.0, 300.0)]


def test_the_sting_carries_no_noise_swell():
    """2026-10-17: only the line moves in the sting; the pink-noise swell
    under it went. The sting's own sound is silence until the bed is mixed."""
    seg = tut.segment_argv("/w/s.png", "/w/a.wav", 8.0, "/w/o.mp4",
                           bg=str(design.INTRO_LOOP), lead=str(design.STING))
    sting = tut.sting_argv("/w/f%02d.png", "/w/sting.mp4")
    for argv in (seg, sting):
        joined = " ".join(argv)
        assert "anoisesrc" not in joined and "pink" not in joined
        assert tut.STING_SILENCE in argv


def test_a_new_bed_renders_every_episode_again(monkeypatch):
    ep = N8N.episode(1)
    before = tut.script_key(ep)
    assert tm.MUSIC_VERSION == "2"                 # the combined bump's bed
    monkeypatch.setattr(tm, "MUSIC_VERSION", "3")
    assert tut.script_key(ep) != before


@pytest.mark.parametrize("music", ["", "0"])
async def test_the_publish_gate_refuses_a_render_without_the_bed(tmp_path, monkeypatch,
                                                                music):
    monkeypatch.setattr(tut, "episode_dir", lambda *_: tmp_path)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: tut.Manifest(
        series="n8n", episode=0, audio="fish", seconds=290.0,
        chapters=[(0.0, "Intro"), (60.0, "A"), (120.0, "B")], parts=["intro-a", "outro-b"],
        **opened(290.0),
        body_seconds=200.0, spoken_minutes=5, music=music))
    got, why = await tut.ready("n8n", 0)
    assert got is None and "background music" in why


# ---------------------------- tutorial_scripts/: one module per episode (10-15)


def test_script_modules_fill_their_planned_entries_in_number_order():
    for key in ts.SCRIPT_SERIES:
        eps = tutorial_scripts.for_series(key)
        assert [e.number for e in eps] == sorted(e.number for e in eps), key
        series_ = ts.series(key)
        for e in eps:
            got = series_.episode(e.number)
            assert got is not None and got.scripted and got.slug == e.slug, (key, e.number)
    assert tutorial_scripts.for_series("nothing") == ()
    # every module is named <series>_<NN> for a known series: none is skipped
    for name in tutorial_scripts.names():
        got = tutorial_scripts.MODULE_NAME.match(name)
        assert got and got["series"] in ts.SCRIPT_SERIES, name
    assert [e.number for e in tutorial_scripts.for_series("n8n")][:1] == [3]


def test_a_script_module_that_collides_with_an_inline_episode_is_refused():
    n8n2 = next(e for e in ts.N8N_PLANNED if e.number == 2)          # scripted inline
    with pytest.raises(ValueError, match="already scripted inline"):
        ts.from_scripts("n8n", ts.N8N_PLANNED, (n8n2,))
    n8n1 = ts.series("n8n").episode(1)                                # outside the plan
    with pytest.raises(ValueError, match="already scripted inline"):
        ts.from_scripts("n8n", ts.N8N_PLANNED, (n8n1,), (ts.N8N_EP0, ts.N8N_EP1))


def _synthetic_plan() -> tuple:
    """Three planned, unscripted entries (2026-10-18): the refusals below are
    checked against a plan of their own, so they hold however far the real
    n8n plan has been scripted."""
    return tuple(ts.plan(n, 2, f"synthetic-{n}", f"Synthetic {n}", "Two\nlines",
                         "A synthetic entry.", ("n8n",), ("#n8n",), ("Outline",))
                 for n in (10, 11, 12))


def test_a_script_module_with_no_planned_entry_or_written_twice_is_refused():
    planned = _synthetic_plan()
    base = planned[1]                       # unscripted, with a neighbour after it
    assert not base.scripted
    holed = tuple(e for e in planned if e.number != base.number)
    inside = replace(base, scenes=n8n_scenes())        # inside the numbering, no slot
    assert base.number < max(e.number for e in holed)
    with pytest.raises(ValueError, match="no planned entry"):
        ts.from_scripts("n8n", holed, (inside,))
    twice = replace(base, scenes=n8n_scenes())
    with pytest.raises(ValueError, match="written twice"):
        ts.from_scripts("n8n", planned, (twice, twice))
    assert ts.from_scripts("n8n", planned, (twice,))[
        [e.number for e in planned].index(base.number)] == twice


def test_a_script_module_past_the_plan_is_appended_and_a_gap_is_refused():
    """2026-10-16: once a series' plan is used up, the writer plans and
    scripts the next number in one module (extend)."""
    last = max(e.number for e in ts.N8N_PLANNED)
    base = replace(ts.N8N_PLANNED[-1], scenes=n8n_scenes(), outline=())
    one, two = replace(base, number=last + 1), replace(base, number=last + 2)
    got = ts.from_scripts("n8n", ts.N8N_PLANNED, (two, one))
    assert got[:len(ts.N8N_PLANNED)] == ts.N8N_PLANNED
    assert [e.number for e in got[len(ts.N8N_PLANNED):]] == [last + 1, last + 2]
    with pytest.raises(ValueError, match="gap in numbering"):
        ts.from_scripts("n8n", ts.N8N_PLANNED, (two,))
    with pytest.raises(ValueError, match="gap in numbering"):
        ts.from_scripts("n8n", ts.N8N_PLANNED, (replace(base, number=99),))
    # past the inline episodes when the plan is empty
    alone = replace(base, number=2)
    assert ts.from_scripts("n8n", (), (alone,), (ts.N8N_EP0, ts.N8N_EP1)) == (alone,)
    assert ts.extend((), two, one) == (one, two)


def n8n_scenes():
    return ts.series("n8n").episode(0).scenes


def test_a_module_whose_name_and_episode_disagree_is_refused(monkeypatch):
    ep = ts.series("n8n").episode(2)
    wrong = SimpleNamespace(__name__="x.n8n_05", EPISODE=ep)
    monkeypatch.setattr(tutorial_scripts, "_modules", lambda key: [(5, wrong)])
    with pytest.raises(ValueError, match="not #5"):
        tutorial_scripts.for_series("n8n")
    empty = SimpleNamespace(__name__="x.n8n_02", EPISODE=replace(ep, scenes=()))
    monkeypatch.setattr(tutorial_scripts, "_modules", lambda key: [(2, empty)])
    with pytest.raises(ValueError, match="no scenes"):
        tutorial_scripts.for_series("n8n")


def test_a_script_modules_lab_file_joins_the_store_and_never_overwrites_one():
    assert ts.with_lab_files({"a.json": "1"}, {"b.json": "2"}) == {"a.json": "1",
                                                                  "b.json": "2"}
    with pytest.raises(ValueError, match="defined twice"):
        ts.with_lab_files(dict(ts.LAB_FILES), {"n8n-first-run.json": "{}\n"})
    for key in ts.SCRIPT_SERIES:
        for name, text in tutorial_scripts.lab_files(key).items():
            assert ts.LAB_FILES[name] == text


# ------------------------------------------------- script runway (2026-10-15)

def _public(rows, cards=()):
    async def fetch_all(sql, *a):
        if "FROM proposals" in sql:
            assert a == (tut.ACTION,)
            return list(cards)
        assert "academy_tutorial_posts" in sql and "WHERE public OR withdrawn" in sql
        return rows
    return fetch_all


async def test_the_runway_counts_scripted_episodes_not_yet_public(monkeypatch):
    lessons = [s for s in ts.SERIES if s.playlist]
    scripted = {s.key: [e.number for e in s.episodes if e.scripted] for s in lessons}
    rows = [{"series": "n8n", "episode": n, "public": True, "withdrawn": False}
            for n in scripted["n8n"][:2]]
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _public(rows))
    days = await tut.runway()
    assert set(days) == {s.key for s in lessons}             # never the welcome
    assert days["n8n"] == len(scripted["n8n"]) - 2
    assert days["claude"] == len(scripted["claude"])
    assert tut.runway_text({"claude": 2, "n8n": None}) == "claude 2 · n8n UNKNOWN"


async def test_the_runway_leaves_out_what_will_not_post_by_itself(monkeypatch):
    """2026-10-16: a withdrawn episode, and one held on a failed-gate card
    for more than two days, are not runway; a fresh held card still is."""
    from datetime import timedelta
    claude = [e.number for e in ts.series("claude").episodes if e.scripted]
    assert len(claude) >= 3
    a, b, c = claude[:3]
    rows = [{"series": "claude", "episode": a}]                # a withdrawn row
    now = datetime.now(UTC)
    cards = [{"dedup_key": f"academy.tutorial.claude.{b}", "created_at": now - timedelta(days=3),
              "reason": f"{tut.HELD_PREFIX} the store does not serve x. The claude ..."},
             {"dedup_key": f"academy.tutorial.claude.{c}", "created_at": now - timedelta(hours=5),
              "reason": f"{tut.HELD_PREFIX} the cap. The claude ..."},
             {"dedup_key": "academy.tutorial.n8n.3", "created_at": now - timedelta(days=9),
              "reason": "Publish the n8n tutorial"}]           # not a held card
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _public(rows, cards))
    days = await tut.runway()
    assert days["claude"] == len(claude) - 2
    assert days["n8n"] == sum(e.scripted for e in ts.series("n8n").episodes)


async def test_the_monthly_series_runway_is_in_months_of_posting(monkeypatch):
    """Where AI Is Going needs about one script a month: its runway is the
    days its scripts cover at one a calendar month, and this month's public
    episode moves the next one due to next month."""
    from datetime import timedelta
    now = datetime.now(UTC)
    left = sum(1 for e in ts.series("future").episodes
               if e.scripted and ts.held("future", e) is None)
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _public([]))
    days = await tut.runway()
    assert days["future"] == tut.monthly_runway_days(left, False, now)
    last_month = now.replace(day=1) - timedelta(days=1)
    rows = [{"series": "future", "episode": 1, "at": last_month, "public": True,
             "withdrawn": False}]
    monkeypatch.setattr("atlas.kernel.state.fetch_all", _public(rows))
    assert (await tut.runway())["future"] == tut.monthly_runway_days(left, False, now)
    rows[0]["at"] = now
    days = await tut.runway()
    assert days["future"] == tut.monthly_runway_days(left, True, now) > 0
    assert days["claude"] == sum(e.scripted for e in ts.series("claude").episodes)


async def test_an_unreadable_runway_is_unknown_never_zero(monkeypatch):
    async def boom(sql, *a):
        raise RuntimeError("db down")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    days = await tut.runway()
    assert days and all(n is None for n in days.values())
    assert tut.runway_short(days) == days                   # UNKNOWN is never long enough


async def test_a_short_runway_is_said_once_a_day(monkeypatch, tmp_path):
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    told: list[str] = []

    async def tell(text):
        told.append(text)
    monkeypatch.setattr(tut, "_tell", tell)

    async def days():
        return {"claude": 5, "n8n": 1, "project": 0, "local": None}
    monkeypatch.setattr(tut, "runway", days)
    line = await tut.warn_runway()
    assert told == [line]
    assert "n8n 1 day," in line and "project 0 days" in line and "local UNKNOWN" in line
    assert re.search(r"\(read \w{3} \d\d:\d\d UTC\)", line)
    assert "claude" not in line and "writer" not in line
    assert await tut.warn_runway() is None and len(told) == 1     # once a day


async def test_a_long_runway_says_nothing_and_a_failure_never_stops_the_tick(
        monkeypatch, tmp_path):
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path))
    told: list[str] = []

    async def tell(text):
        told.append(text)
    monkeypatch.setattr(tut, "_tell", tell)

    async def long_():
        return {"claude": 3, "n8n": 7}
    monkeypatch.setattr(tut, "runway", long_)
    assert await tut.warn_runway() is None and told == []

    async def boom():
        raise RuntimeError("x")
    monkeypatch.setattr(tut, "runway", boom)
    assert await tut.warn_runway() is None


async def test_the_daily_tick_warns_of_a_short_runway_even_with_nothing_to_draft(tick):
    tick["rows"] = []                 # nothing public: every series' full runway
    await tut.daily()
    short = [k for k, n in (await tut.runway()).items() if n < tut.RUNWAY_WARN_DAYS]
    said = sum("script runway" in t for t in tick["told"])
    assert said == (1 if short else 0), short


async def test_the_teaching_short_is_drawn_in_portrait_with_its_hook_first(teach):
    """ShortsFrame (the owner, 2026-10-17): every page 1080x1920, the hook on the
    first from frame 0, the closing card naming the lesson last; no page
    says "Made with AI" and nothing is zoomed."""
    from atlas.modules.academy import short_frame as sf
    from atlas.modules.academy import tutorial_short as tsh
    _put_episode("claude", 4)
    r = await tut.render_teaching_short("claude", 4, "quiz")
    assert r.ok, r.detail
    docs = teach["docs"]
    assert docs and all((w, h) == (sf.W, sf.H) for _, _, w, h in docs)
    hook = tsh.hook_from(["You leave out tools. The subagent gets…"])
    assert f'<h1 class="hook">{hook}</h1>' in docs[0][0]
    assert docs[-1][1].endswith("close.png")
    assert ts.series("claude").episode(4).title in docs[-1][0]
    assert not any("Made with AI" in d for d, *_ in docs)
    graph = teach["argv"][-1][teach["argv"][-1].index("-filter_complex") + 1]
    assert "zoompan" not in graph and "fade=t=in" not in graph and "ass=" in graph
