"""Thumbnails made from a frame of the lesson's own video (2026-10-20):
academy/video_frames.py (the pure scoring, dHash and pick; ffmpeg for real
only where it is installed), tutorial_thumbs' frame path, and
notebooklm_thumbs' uniqueness store, backfill and CLI."""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from atlas.modules.academy import notebooklm as nlm
from atlas.modules.academy import notebooklm_thumbs as nth
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorial_thumbs as thumbs
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy import video_frames as vf

KEY = "claude"
S = ts.series(KEY)
assert S is not None
EPS = [e for e in S.episodes if e.scripted][:3]
W, H = vf.ANALYSE_W, vf.ANALYSE_H


def flat(v: int) -> bytes:
    return bytes([v]) * (W * H)


def slide() -> bytes:
    """Paper with dark strokes: a drawn slide."""
    px = bytearray([235]) * (W * H)
    for y in range(10, 80, 9):
        for x in range(12, 148):
            if (x // 7 + y) % 3:
                px[y * W + x] = 30
    for y in range(20, 70):
        px[y * W + 40] = px[y * W + 120] = 20
    return bytes(px)


def busier() -> bytes:
    px = bytearray(slide())
    for y in range(0, H, 3):
        for x in range(0, W, 4):
            px[y * W + x] = (x * 7 + y * 13) % 256
    return bytes(px)


# ------------------------------------------------------------- the scoring

def test_candidates_skip_the_intro_and_the_end_card():
    got = vf.candidate_times(300.0)
    assert len(got) == vf.CANDIDATES == 24
    assert got == sorted(got)
    assert got[0] > 300 * vf.HEAD and got[-1] < 300 * (1 - vf.TAIL)
    assert vf.candidate_times(0) == [] and vf.candidate_times(10, 0) == []


@pytest.mark.parametrize("frame,word", [(flat(0), "dark"), (flat(12), "dark"),
                                        (flat(128), "flat"), (flat(250), "flat"),
                                        (b"\x80" * 10, "short")])
def test_blank_dark_and_fade_frames_are_rejected_with_why(frame, word):
    score, why = vf.richness(frame)
    assert score is None and word in why


def test_a_soft_gradient_with_nothing_drawn_is_rejected():
    px = bytes(60 + (x * 120) // W for _y in range(H) for x in range(W))
    score, why = vf.richness(px)
    assert score is None and "nothing drawn" in why


def test_a_drawn_slide_scores_and_a_busier_one_scores_higher():
    a, _ = vf.richness(slide())
    b, _ = vf.richness(busier())
    assert a is not None and b is not None and 0 < a < b <= 1
    assert vf.richness(slide()) == vf.richness(slide())         # deterministic


def test_the_rich_frame_is_picked_over_blank_and_fade():
    frames = [flat(0), flat(128), slide(), busier(), flat(250)]
    cands = [vf.Candidate(i, float(i), *vf.richness(f), dhash=i) for i, f in enumerate(frames)]
    best, note = vf.pick(cands, [])
    assert best is not None and best.index == 3 and note == ""


def test_all_rejected_says_why():
    cands = [vf.Candidate(0, 1.0, *vf.richness(flat(0))),
             vf.Candidate(1, 2.0, *vf.richness(flat(128)))]
    best, why = vf.pick(cands, [])
    assert best is None and "rejected" in why and "dark" in why and "flat" in why


# --------------------------------------------------------------- the hash

def test_dhash_is_64_bits_and_hamming_counts_them():
    ramp = bytes(x * 30 for _y in range(8) for x in range(9))
    down = bytes(255 - x * 30 for _y in range(8) for x in range(9))
    assert vf.dhash(ramp) == 0
    assert vf.dhash(down) == 2 ** 64 - 1
    assert vf.hamming(vf.dhash(ramp), vf.dhash(down)) == 64
    assert vf.hamming(0b1011, 0b0001) == 2
    assert vf.hash_hex(255) == "00000000000000ff"


def test_a_frame_too_like_another_lessons_gives_way_to_the_next_best():
    best = vf.Candidate(0, 1.0, 0.9, dhash=0)
    second = vf.Candidate(1, 2.0, 0.8, dhash=(1 << 20) - 1)       # 20 bits away
    third = vf.Candidate(2, 3.0, 0.7, dhash=(1 << 40) - 1)
    got, note = vf.pick([best, second, third], [1])               # 1 bit from best
    assert got is second and note == ""
    # 9 bits off is "the same picture"; 10 is different
    near = vf.Candidate(3, 4.0, 0.95, dhash=(1 << 9) - 1)
    assert vf.pick([near], [0])[0] is near and vf.pick([near], [0])[1]
    far = vf.Candidate(4, 5.0, 0.95, dhash=(1 << 10) - 1)
    assert vf.pick([far], [0]) == (far, "")


def test_when_none_is_unique_the_best_is_kept_and_said():
    a = vf.Candidate(0, 1.0, 0.9, dhash=0)
    b = vf.Candidate(1, 2.0, 0.5, dhash=1)
    got, note = vf.pick([a, b], [0, 3])
    assert got is a and "the best kept at 0 bits" in note


# ------------------------------------------------- real ffmpeg (when installed)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


def _make_video(path: Path) -> None:
    """10 s: 1 s black, 8 s of testsrc2, 1 s black (the fade frames)."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=black:s=640x360:r=10:d=1",
         "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=8",
         "-f", "lavfi", "-i", "color=black:s=640x360:r=10:d=1",
         "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0", "-g", "5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


@needs_ffmpeg
async def test_a_real_video_gives_scored_candidates_and_a_frame(tmp_path):
    video = tmp_path / "video.mp4"
    await asyncio.to_thread(_make_video, video)
    cands, why = await vf.candidates(video, 12)
    assert why is None and len(cands) == 12
    assert any(c.score is not None for c in cands)
    out = tmp_path / "frame.jpg"
    best, note, why = await vf.choose(video, [], out)
    assert why is None and best is not None and note == ""
    assert 1.0 < best.at < 9.0
    assert out.read_bytes().startswith(b"\xff\xd8\xff")
    assert await vf.image_dhash(out) is not None


@needs_ffmpeg
async def test_an_unreadable_video_says_why(tmp_path):
    bad = tmp_path / "video.mp4"
    bad.write_bytes(b"\x00not a video")
    best, _note, why = await vf.choose(bad, [], tmp_path / "f.jpg")
    assert best is None and why and "probed" in why


# ------------------------------------------------ the thumbnail over the frame

class FakeShooter:
    def __init__(self) -> None:
        self.docs: list[str] = []

    async def shoot(self, doc: str, path: str, w: int = 0, h: int = 0) -> str:
        self.docs.append(doc)
        Path(path).write_bytes(thumbs.PNG_MAGIC + b"x" * 100)
        return path


def test_the_frame_look_shows_the_frame_large():
    for ep in EPS:
        look = thumbs.frame_look(ep)
        assert look is not None and look.family in thumbs.FRAME_FAMILIES


async def test_render_with_a_frame_uses_it_not_the_drawn_image(tmp_path, monkeypatch):
    async def no_image(*a, **k):
        raise AssertionError("the drawn image must not be made")
    monkeypatch.setattr(thumbs, "image", no_image)
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff" + b"jpegbytes")
    shooter = FakeShooter()
    why = await thumbs.render(EPS[0], shooter, str(tmp_path / "t.png"), level=1,
                              badge="5 MIN", work=str(tmp_path), frame=str(frame))
    assert why is None
    assert "data:image/jpeg;base64," in shooter.docs[0]
    assert "5 min" in shooter.docs[0]                       # the tab is still drawn


async def test_render_with_a_frame_that_is_no_image_says_so(tmp_path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"GIF89a")
    why = await thumbs.render(EPS[0], FakeShooter(), str(tmp_path / "t.png"), level=1,
                              badge="", work=str(tmp_path), frame=str(frame))
    assert why and "not a PNG or JPEG" in why


def test_two_lessons_with_different_frames_get_different_pages():
    a = thumbs.page(EPS[0], level=1, badge="", image="data:image/jpeg;base64,AAA",
                    look=thumbs.frame_look(EPS[0]))
    b = thumbs.page(EPS[0], level=1, badge="", image="data:image/jpeg;base64,BBB",
                    look=thumbs.frame_look(EPS[0]))
    assert a != b


# ---------------------------------------------- the store, backfill and CLI

@pytest.fixture
def lessons(monkeypatch, tmp_path):
    """Three posted NotebookLM lessons with video.mp4 on disk; the record and
    the frame thumbnail mocked."""
    from atlas.kernel import state
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "tut"))
    st: dict = {"made": [], "fail": None,
                "rows": [{"series": KEY, "episode": e.number,
                          "video_id": f"vid{e.number:08d}", "last": 10 - i}
                         for i, e in enumerate(EPS)]}
    for e in EPS:
        d = tut.episode_dir(S, e)
        d.mkdir(parents=True)
        (d / "video.mp4").write_bytes(b"mp4")
        m = tut.Manifest(series=KEY, episode=e.number, audio=nlm.AUDIO, seconds=300.0,
                         badge="5 MIN")
        (d / "manifest.json").write_text(json.dumps(asdict(m)))
        (d / nlm.KEPT_META).write_text(json.dumps(
            {"notebook_url": f"https://notebooklm.google.com/notebook/n{e.number}"}))

    async def fetch_all(sql, *args):
        if isinstance(st["rows"], Exception):
            raise st["rows"]
        return st["rows"]

    async def frame(ep, video, out_dir, badge, skip=None, skip_drop=""):
        st["made"].append((ep.number, badge, Path(video).parent == skip, skip_drop))
        if st["fail"]:
            return None, st["fail"]
        (out_dir / "thumb.jpg").write_bytes(b"j")
        (out_dir / "thumb.png").write_bytes(nth.PNG_MAGIC + b"p")
        return {"source": nth.FRAME_SOURCE, "frame_dhash": f"{ep.number:016x}",
                "thumb_dhash": "f" * 16, "frame_at": 12.0}, None
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(nth, "frame_thumbnail", frame)
    return st


def test_the_marker_keeps_a_frames_hashes_and_drops_bad_ones():
    good = json.loads(nth.marker_text({}, frame={"frame_dhash": "a" * 16,
                                                 "thumb_dhash": "b" * 16, "frame_at": 3.25}))
    assert good["source"] == "video-frame" and good["frame_dhash"] == "a" * 16
    assert good["thumb_dhash"] == "b" * 16 and good["frame_at"] == 3.25
    bad = json.loads(nth.marker_text({}, frame={"frame_dhash": "zz", "frame_at": "soon"}))
    assert "frame_dhash" not in bad and "frame_at" not in bad


def test_a_drops_own_meta_can_never_plant_frame_hashes():
    forged = {"source": "video-frame", "frame_dhash": "a" * 16, "thumb_dhash": "b" * 16,
              "frame_at": 3.0}
    got = json.loads(nth.marker_text(forged))
    assert got["source"] == "notebooklm"
    assert not {"frame_dhash", "thumb_dhash", "frame_at"} & set(got)


def test_known_hashes_reads_markers_and_waiting_drops_but_not_itself(lessons):
    ds = [tut.episode_dir(S, e) for e in EPS]
    (ds[0] / nth.MARKER).write_text(nth.marker_text(
        {}, frame={"frame_dhash": "0" * 15 + "1"}))
    (ds[1] / nth.MARKER).write_text(nth.marker_text({}))      # an infographic: no hash
    drop = nth.drop_for(KEY, EPS[2].number)
    drop.mkdir(parents=True)
    (drop / "meta.json").write_text(json.dumps({"frame_dhash": "0" * 15 + "2"}))
    assert sorted(nth.known_hashes()) == [1, 2]
    assert nth.known_hashes(skip=ds[0], skip_drop=drop.name) == []


async def test_frame_candidates_are_drawn_notebooklm_lessons_with_their_video(lessons):
    got = await nth.frame_candidates()
    assert [r["episode"] for r in got] == [e.number for e in EPS]
    d0, d1, d2 = (tut.episode_dir(S, e) for e in EPS)
    (d0 / nth.MARKER).write_text("{}")                   # already NotebookLM's
    (d1 / "video.mp4").unlink()                          # no video kept
    m = tut.read_manifest(d2)
    assert m is not None
    (d2 / "manifest.json").write_text(json.dumps(asdict(replace(m, audio="fish"))))
    assert await nth.frame_candidates() == []



async def test_a_rerender_lesson_gets_no_frame_on_its_old_video(lessons, monkeypatch):
    """notebooklm.RERENDER_POSTED: the render on disk may be the relaunch's,
    so no frame of it is set on the live (old) video."""
    monkeypatch.setattr(nlm, "RERENDER_POSTED", frozenset({(KEY, EPS[0].number)}))
    got = await nth.frame_candidates()
    assert [r["episode"] for r in got] == [e.number for e in EPS[1:]]

async def test_the_backfill_drops_thumb_then_meta_for_the_executor(lessons):
    said = await nth.frame_backfill(limit=2)
    assert len(said) == 2 and all("dropped a video-frame thumbnail" in s for s in said)
    assert [x[0] for x in lessons["made"]] == [EPS[0].number, EPS[1].number]
    assert all(x[1] == "5 MIN" and x[2] for x in lessons["made"])
    drop = nth.drop_for(KEY, EPS[0].number)
    assert sorted(p.name for p in drop.iterdir()) == ["meta.json", "thumb.png"]
    meta = json.loads((drop / "meta.json").read_text())
    assert meta["series"] == KEY and meta["episode"] == EPS[0].number
    assert meta["source"] == "video-frame" and meta["frame_dhash"] == f"{EPS[0].number:016x}"
    assert meta["notebook_url"].endswith(f"/n{EPS[0].number}")
    assert nth.meta_refusal(meta, KEY, EPS[0].number) is None
    assert drop in nth.complete_drops()
    assert not [p for p in nth.inbox().iterdir() if p.name.startswith(".frame-")]
    # a waiting drop is not made again; the third lesson is next
    lessons["made"].clear()
    await nth.frame_backfill(limit=5)
    assert [x[0] for x in lessons["made"]] == [EPS[2].number]


async def test_a_lesson_whose_frame_fails_is_said_and_keeps_its_drawn_one(lessons):
    lessons["fail"] = "every candidate frame was rejected (dark (mean 3))"
    said = await nth.frame_backfill(limit=1)
    assert "no video-frame thumbnail" in said[0] and "rejected" in said[0]
    assert not nth.complete_drops()
    assert not [p for p in nth.inbox().iterdir() if p.name.startswith(".frame-")]


async def test_the_frame_thumbs_command_lists_drops_or_says_unknown(lessons, capsys):
    assert await asyncio.to_thread(nlm.main, ["frame-thumbs", "--list", "--limit", "1"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [r["episode"] for r in listed] == [EPS[0].number] and not nth.complete_drops()
    assert await asyncio.to_thread(nlm.main, ["frame-thumbs", "--limit", "1"]) == 0
    assert "dropped" in json.loads(capsys.readouterr().out)[0]
    assert len(nth.complete_drops()) == 1
    lessons["rows"] = OSError("connection refused")
    assert await asyncio.to_thread(nlm.main, ["frame-thumbs"]) == 1
    err = capsys.readouterr()
    assert "UNKNOWN" in err.err and err.out == ""


# --------------------------------------------- the review's fixes (2026-10-20)

def test_late_frames_give_way_and_near_ties_go_to_the_earlier():
    early = vf.Candidate(0, 10.0, 0.70, pos=0.2)
    late = vf.Candidate(1, 290.0, 0.80, pos=0.8)         # a recap card, richer
    assert vf.pick([late, early], [])[0] is early
    a = vf.Candidate(2, 20.0, 0.700, pos=0.3)
    b = vf.Candidate(3, 40.0, 0.705, pos=0.4)             # a near-tie
    assert vf.pick([b, a], [])[0] is a
    clear = vf.Candidate(4, 50.0, 0.80, pos=0.5)
    assert vf.pick([a, clear], [])[0] is clear


def test_candidates_carry_their_place_in_the_video():
    times = vf.candidate_times(100.0, 4)
    assert all(0 < t / 100.0 < 1 for t in times)


def test_the_hash_is_shrunk_from_the_analysed_frame():
    small = vf.shrink(slide())
    assert len(small) == vf.HASH_W * vf.HASH_H
    assert vf.shrink(flat(77)) == bytes([77]) * (vf.HASH_W * vf.HASH_H)


def test_every_ffmpeg_reading_the_video_is_held_to_files():
    for argv in (vf.probe_argv("/v.mp4"), vf.gray_argv("/v.mp4", 1.0),
                 vf.frame_argv("/v.mp4", 1.0, "/o.jpg"), vf.image_hash_argv("/o.png")):
        i = argv.index("-protocol_whitelist")
        assert argv[i + 1] == "file,pipe" and i < argv.index("-i")


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    from atlas.modules.content import ffmpeg
    st = {"runs": 0}

    async def run(argv, timeout=None):
        st["runs"] += 1
        if argv[0] == "ffprobe":
            return b'{"format": {"duration": "100.0"}}'
        if argv[-1] == "pipe:1":
            return slide()
        Path(argv[-1]).write_bytes(b"\xff\xd8\xffjpeg")
        return b""
    monkeypatch.setattr(ffmpeg, "run", run)
    return st


async def test_one_seek_a_candidate(fake_ffmpeg):
    cands, why = await vf.candidates(Path("/v.mp4"), 6)
    assert why is None and len(cands) == 6 and all(c.score for c in cands)
    assert fake_ffmpeg["runs"] == 1 + 6


async def test_a_lesson_past_its_deadline_stops_and_says_so(fake_ffmpeg):
    import time
    cands, why = await vf.candidates(Path("/v.mp4"), 6, deadline=time.monotonic() - 1)
    assert cands == [] and why and "deadline" in why
    assert fake_ffmpeg["runs"] <= 1
    best, _n, why = await vf.choose(Path("/v.mp4"), [], Path("/tmp/x.jpg"),
                                    deadline=time.monotonic() - 1)
    assert best is None and "deadline" in (why or "")


async def test_a_failing_lesson_is_recorded_skipped_and_does_not_starve_the_rest(lessons):
    first = EPS[0].number

    async def fail_first(ep, video, out_dir, badge, skip=None, skip_drop=""):
        lessons["made"].append((ep.number,))
        if ep.number == first:
            return None, "the thumbnail words do not fit any family"
        (out_dir / "thumb.png").write_bytes(nth.PNG_MAGIC + b"p")
        return {"source": nth.FRAME_SOURCE, "frame_dhash": f"{ep.number:016x}",
                "frame_at": 12.0}, None
    import atlas.modules.academy.notebooklm_thumbs as mod
    mod_frame = mod.frame_thumbnail
    mod.frame_thumbnail = fail_first
    try:
        said = await nth.frame_backfill(limit=1)
    finally:
        mod.frame_thumbnail = mod_frame
    assert [x[0] for x in lessons["made"]] == [first, EPS[1].number]
    assert any("words do not fit" in s for s in said)
    assert len(nth.complete_drops()) == 1                  # the success counted, once
    rec = json.loads((tut.episode_dir(S, EPS[0]) / nth.FRAME_FAILED).read_text())
    assert "words do not fit" in rec["why"] and rec["at"]
    got = await nth.frame_candidates()
    assert first not in [r["episode"] for r in got]


async def test_a_pass_tries_a_bounded_number_of_lessons(lessons, monkeypatch):
    monkeypatch.setattr(nth, "FRAME_TRIES_PER_PASS", 2)
    lessons["fail"] = "every candidate frame was rejected"
    said = await nth.frame_backfill(limit=1)
    assert len(lessons["made"]) == 2 and len(said) == 2


async def test_a_failure_is_tried_again_after_the_retry_days(lessons):
    from datetime import UTC, datetime, timedelta
    d = tut.episode_dir(S, EPS[0])
    old = (datetime.now(UTC) - timedelta(days=nth.FRAME_RETRY_DAYS + 1)).isoformat()
    (d / nth.FRAME_FAILED).write_text(json.dumps({"at": old, "why": "x"}))
    assert EPS[0].number in [r["episode"] for r in await nth.frame_candidates()]
    (d / nth.FRAME_FAILED).write_text(json.dumps(
        {"at": datetime.now(UTC).isoformat(), "why": "x"}))
    assert EPS[0].number not in [r["episode"] for r in await nth.frame_candidates()]


async def test_a_rolled_back_lesson_is_never_framed_again(lessons):
    d = tut.episode_dir(S, EPS[0])
    (d / nth.FRAME_ROLLED_BACK).write_text("{}")
    assert EPS[0].number not in [r["episode"] for r in await nth.frame_candidates()]


async def test_stale_staging_folders_are_swept_and_fresh_ones_kept(lessons):
    import os
    import time
    nth.inbox().mkdir(parents=True, exist_ok=True)
    stale = nth.inbox() / ".frame-claude-01-old"
    fresh = nth.inbox() / ".frame-claude-02-new"
    for p in (stale, fresh):
        p.mkdir()
        (p / "thumb.png").write_bytes(b"x")
    t = time.time() - nth.STAGING_STALE.total_seconds() - 60
    os.utime(stale, (t, t))
    await nth.frame_backfill(limit=0)
    assert not stale.exists() and fresh.exists()


async def test_an_environment_failure_is_retried_in_days_not_a_month(lessons):
    from datetime import UTC, datetime, timedelta
    d = tut.episode_dir(S, EPS[0])
    ago = (datetime.now(UTC) - timedelta(days=nth.FRAME_ENV_RETRY_DAYS + 1)).isoformat()
    for why in ("the lesson passed its deadline of 300s and was stopped",
                "the thumbnail could not be drawn (TimeoutError)",
                "ffmpeg: ffmpeg not installed"):
        (d / nth.FRAME_FAILED).write_text(json.dumps({"at": ago, "why": why}))
        assert EPS[0].number in [r["episode"] for r in await nth.frame_candidates()]
    (d / nth.FRAME_FAILED).write_text(json.dumps(
        {"at": ago, "why": "every candidate frame was rejected (dark (mean 3))"}))
    assert EPS[0].number not in [r["episode"] for r in await nth.frame_candidates()]
