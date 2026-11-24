"""Part C: moving lesson videos. Pure tests for the planner and the gate maths;
render tests are skipped when Chrome or ffmpeg is missing."""
from __future__ import annotations

import importlib.util
import itertools
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LV = ROOT / "tools" / "lesson_video"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"lv_{name}", LV / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


beats = load("beats")
Seg = beats.Seg

LONG = ("First you open the folder that holds your files, and then you read the name of "
        "each one out loud so you know what is inside. Second you pick the one you need "
        "and write its name on a card, which you keep next to the screen. ")


def lesson() -> list:
    return [
        Seg("title", "", "<h1>Folders</h1>", "Folders. Lesson one.", 3.0),
        Seg("body", "In this lesson", "<ul><li>Open a folder</li><li>Read a name</li>"
            "<li>Pick a file</li></ul>",
            "In this lesson you will: Open a folder. Read a name. Pick a file.", 6.0),
        Seg("body", "Why it matters", "<p>Files you can find save <b>real time</b> every day.</p>",
            "Files you can find save real time every day. " + LONG, 24.0),
        Seg("body", "Steps", "<ol><li>Open the folder</li><li>Read each name</li>"
            "<li>Pick the file</li></ol>",
            "Steps. Open the folder. Read each name. Pick the file.", 8.0),
        Seg("body", "Compare", "<table><tr><th>Tool</th><th>Use</th></tr>"
            "<tr><td>Folder</td><td>Keeps files</td></tr></table>",
            "Tool: Folder. Use: Keeps files.", 5.0),
        Seg("body", "Quote", "<blockquote>" + LONG + "</blockquote>", LONG, 30.0),
        Seg("body", "Check", "<p>Which folder holds the file?</p>",
            "Check. Which folder holds the file?", 4.0),
        Seg("body", "Next", "<p>Next we make a folder.</p>", "Next we make a folder.", 3.0),
    ]


def test_length_is_audio_plus_sting_end_pauses():
    segs = lesson()
    p = beats.plan(segs, lesson="Folders")
    audio = sum(s.seconds for s in segs)
    assert p.audio_seconds == pytest.approx(audio)
    assert p.total == pytest.approx(
        beats.STING + audio + beats.END_CARD + sum(b - a for a, b in p.pauses), abs=0.01)
    assert len(p.pauses) == 1                      # one "Check"
    assert all(b - a <= beats.PAUSE_MAX for a, b in p.pauses)
    assert beats.END_CARD <= 4.0


def test_beats_are_contiguous_and_none_over_limit():
    p = beats.plan(lesson(), lesson="Folders")
    t = 0.0
    for b in p.beats:
        assert b.t0 == pytest.approx(t, abs=0.002), (b.kind, b.t0, t)
        assert b.t1 > b.t0
        t = b.t1
    assert t == pytest.approx(p.total, abs=0.002)
    body = [b for b in p.beats if b.kind not in ("sting", "end")]
    assert max(b.seconds for b in body) <= beats.MAX_BEAT + 0.01
    assert beats.MAX_BEAT < beats.FREEZE_MAX


def test_every_kind_is_planned():
    kinds = {b.kind for b in beats.plan(lesson(), lesson="Folders").beats}
    assert {"sting", "title", "map", "key", "flow", "table", "quote",
            "checkpoint", "end"} <= kinds


def test_no_beat_names_a_list_item_before_it_is_spoken():
    segs = lesson()
    p = beats.plan(segs, lesson="Folders")
    flow = [b for b in p.beats if b.kind == "flow"]
    assert [b.data["now"] for b in flow] == sorted(b.data["now"] for b in flow)
    a0 = next(a for i, a, _ in p.audio if i == 3)
    assert flow[0].t0 >= a0 - 0.01


def test_captions_cover_every_spoken_second_and_stay_readable():
    p = beats.plan(lesson(), lesson="Folders")
    for i, a0, a1 in p.audio:
        cs = [c for c in p.cues if a0 - 0.01 <= c.t0 and c.t1 <= a1 + 0.01]
        assert cs and cs[0].t0 == pytest.approx(a0, abs=0.01)
        assert cs[-1].t1 == pytest.approx(a1, abs=0.01)
        for x, y in itertools.pairwise(cs):
            assert y.t0 == pytest.approx(x.t1, abs=0.01)
    assert all(len(c.text) <= beats.CUE_MAX_CHARS + 20 for c in p.cues)
    assert all(c.t1 - c.t0 <= beats.CUE_MAX_S + 0.5 for c in p.cues)


def test_caption_words_are_the_narration_words():
    segs = lesson()
    p = beats.plan(segs, lesson="Folders")
    said = beats.norm(" ".join(s.say for s in segs))
    got = beats.norm(" ".join(c.text for c in p.cues))
    assert got == said


def test_nothing_on_screen_that_is_not_in_the_lesson():
    segs = lesson()
    p = beats.plan(segs, lesson="Folders")
    corpus = beats.norm(" ".join(s.say + " " + beats.plain(s.html)[0] + " " + s.heading
                                 for s in segs))
    for b in p.beats:
        if b.kind in ("key", "quote"):
            assert beats.norm(b.data["phrase"]) in corpus, b.data["phrase"]
        if b.kind == "list":
            for x in b.data["items"]:
                assert beats.norm(x) in corpus


def test_senior_limits():
    st = beats.STYLE[True]
    assert st["items"] <= 3 and st["item"] >= beats.FLOOR_SENIOR
    assert beats.STYLE[False]["item"] >= beats.FLOOR
    long_list = Seg("body", "Rules", "<ul>" + "".join(f"<li>Rule number {k} says do it</li>"
                                                      for k in range(6)) + "</ul>",
                    "Rules. " + " ".join(f"Rule number {k} says do it." for k in range(6)), 18.0)
    p = beats.plan([lesson()[0], long_list], senior=True, lesson="x")
    assert all(len(b.data["items"]) <= 3 for b in p.beats if b.kind == "list")


def test_senior_pace_is_from_audio_not_added_silence():
    segs = lesson()
    a = beats.plan(segs, senior=False, lesson="x")
    b = beats.plan(segs, senior=True, lesson="x")
    assert a.total == pytest.approx(b.total, abs=0.01)


def test_long_items_are_labelled_not_shrunk():
    long = "Keep a notebook beside the keyboard and write every new word you meet today"
    out = beats.label(long, chars=40)
    assert len(out) <= 40 and beats.norm(out) in beats.norm(long)


def test_budget_flags_overflow_instead_of_hiding_it():
    p = beats.plan(lesson(), lesson="Folders")
    assert beats.budget_problems(p) == []
    assert not beats.fits("x" * 400, 56, 2)


def test_align_uses_pauses_when_plausible():
    sents = ["One two three four five six.", "Seven eight nine ten eleven twelve."]
    spans = beats.align(sents, 10.0, [(5.1, 0.4), (2.0, 0.2)])
    assert spans[0][1] == pytest.approx(5.1)
    spans = beats.align(sents, 10.0, None)
    assert spans[0][1] == pytest.approx(10.0 * len(sents[0]) / sum(map(len, sents)), abs=0.01)


# ---------------------------------------------------------------- the gate
gate = load("gate")


def test_parse_freezedetect():
    log = ("[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 3.5\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 7.25\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 10.75\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 20\n")
    got = gate.parse_freezedetect(log, 30.0)
    assert got == [(3.5, 10.75), (20.0, 30.0)]


def test_freeze_check_only_counts_between_sting_and_end_card():
    ok = gate.check_freeze([(0.0, 1.0), (50.0, 54.0)], total=54.0, end_card=4.0)
    assert ok["pass"] and ok["longest"] <= 6.0
    bad = gate.check_freeze([(5.0, 12.0)], total=54.0, end_card=4.0)
    assert not bad["pass"] and bad["longest"] == pytest.approx(7.0)
    still = gate.check_freeze([(1.0, 54.0)], total=54.0, end_card=4.0)   # a slide video
    assert not still["pass"]


def test_length_check_two_percent():
    assert gate.check_length(100.0, 101.9)["pass"]
    assert not gate.check_length(100.0, 102.5)["pass"]
    assert not gate.check_length(None, 100.0)["pass"]       # unknown is never ok


def test_loudness_check():
    assert gate.check_loudness(-19.4, -3.0)["pass"]
    assert not gate.check_loudness(-27.0, -3.0)["pass"]
    assert not gate.check_loudness(-19.5, 1.0)["pass"]
    assert not gate.check_loudness(None, None)["pass"]


def test_overflow_missing_report_fails():
    assert not gate.check_overflow(None)["pass"]
    assert gate.check_overflow([])["pass"]
    assert not gate.check_overflow(["a"])["pass"]


def test_gate_json_shape(tmp_path):
    out = gate.result_json({"length": {"pass": True}, "freeze": {"pass": False}})
    d = json.loads(out)
    assert d["pass"] is False and set(d) == {"pass", "checks", "read_at"}
    assert re.match(r"\d{4}-\d\d-\d\dT", d["read_at"])


# ---------------------------------------------------------------- guards
motion = load("motion")


def test_refuses_to_write_under_live_tree():
    for bad in ("data/app/academy/files/x.mp4", "/x/data/app/academy/files/y/z.mp4"):
        with pytest.raises(ValueError):
            motion.guard_out(Path(bad))
    motion.guard_out(Path("data/academy-staging/video/ok.mp4"))


def test_never_writes_in_place_over_a_hard_link(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"old")
    link = tmp_path / "b.mp4"
    link.hardlink_to(f)
    motion.replace_atomic(tmp_path / "new.tmp", b"new", link)
    assert f.read_bytes() == b"old"          # the other name of the old inode is untouched
    assert link.read_bytes() == b"new"
