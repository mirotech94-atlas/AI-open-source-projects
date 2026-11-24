"""NotebookLM thumbnails (academy/notebooklm_thumbs.py; the owner, 2026-10-20):
the image checks (only ffmpeg decodes, mocked here), the thumbs inbox
applied through the executor with its dry run, rollback, refusals and 403
hold, the posted-lessons list, and the lesson ingest using a drop's own
thumbnail."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from atlas.modules.academy import notebooklm as nlm
from atlas.modules.academy import notebooklm_thumbs as nth
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut

KEY, N = "claude", 1
S = ts.series(KEY)
assert S is not None
EP = S.episode(N)
assert EP is not None
VID = "abcdefghijk"
PNG = nth.PNG_MAGIC + b"\x00" * 64
JPEG = nth.JPEG_MAGIC + b"\xe0" + b"\x00" * 64


@pytest.fixture
def box(monkeypatch, tmp_path):
    """A tutorials root; ffmpeg/ffprobe, thumbnails.set, the posts record and
    Slack mocked. `drop()` makes a complete thumbs drop for claude #1."""
    from atlas.modules.content import ffmpeg
    from atlas.modules.youtube import api
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "tut"))
    st: dict = {"src": {"codec_name": "png", "width": 1024, "height": 1024},
                "out": {"codec_name": "mjpeg", "width": 1280, "height": 720},
                "size": 1000, "png_size": None, "runs": [], "sets": [],
                "set": ({"video_id": VID}, None), "told": [],
                "posted": {"public": True, "video_id": VID}, "fail": None}

    async def run(argv, timeout=None):
        st["runs"].append(argv)
        if st["fail"] is not None:
            raise ffmpeg.FfmpegError(st["fail"])
        out = argv[-1]
        if argv[0] == "ffprobe":
            s = st["out"] if out.endswith("thumb.jpg") else st["src"]
            return json.dumps({"streams": [s]}).encode()
        size = st["png_size"] if out.endswith(".png") and st["png_size"] else st["size"]
        Path(out).write_bytes(b"i" * size)
        return b""

    async def set_thumbnail(channel, video_id, path):
        st["sets"].append((channel, video_id, Path(path).name, Path(path).read_bytes()))
        return st["set"]

    async def posted(series, episode):
        if isinstance(st["posted"], Exception):
            raise st["posted"]
        return st["posted"]

    async def tell(text):
        st["told"].append(text)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(api, "set_thumbnail", set_thumbnail)
    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(tut, "_tell", tell)

    def drop(image: bytes = PNG, meta: dict | None = None,
             name: str = nlm.drop_name(KEY, N)) -> Path:
        d = nth.inbox() / name
        d.mkdir(parents=True, exist_ok=True)
        (d / nth.THUMB).write_bytes(image)
        body = meta if meta is not None else {
            "series": KEY, "episode": N, "made_at": "2026-10-20T09:00:00+00:00",
            "notebook_url": "https://notebooklm.google.com/notebook/abc"}
        (d / "meta.json").write_text(json.dumps(body))
        return d
    st["drop"] = drop
    return st


def episode_with_thumb() -> Path:
    d = tut.episode_dir(S, EP)
    d.mkdir(parents=True, exist_ok=True)
    (d / "thumb.jpg").write_bytes(b"old jpg")
    (d / "thumb.png").write_bytes(b"old png")
    return d


# ------------------------------------------------------------ the image

def test_the_image_is_decoded_with_its_demuxer_forced_and_cropped_to_fill():
    argv = nth.encode_argv("in.png", "png", "out.jpg", 2)
    assert argv[argv.index("-f") + 1] == "png_pipe"
    assert argv.index("-f") < argv.index("-i")         # forced before the input
    vf = argv[argv.index("-vf") + 1]
    assert "force_original_aspect_ratio=increase" in vf and "crop=1280:720" in vf
    assert argv[argv.index("-q:v") + 1] == "2" and argv[-1] == "out.jpg"
    probe = nth.probe_argv("in.jpg", "jpeg")
    assert probe[0] == "ffprobe" and probe[probe.index("-f") + 1] == "jpeg_pipe"


@pytest.mark.parametrize(("head", "kind"), [
    (PNG, "png"), (JPEG, "jpeg"), (b"GIF89a....", None), (b"<svg ", None), (b"", None)])
def test_only_png_and_jpeg_bytes_are_images(head, kind):
    assert nth.image_kind(head) == kind


async def test_a_good_png_becomes_a_1280x720_png_and_jpg(box, tmp_path):
    src = tmp_path / "in.png"
    src.write_bytes(PNG)
    out = tmp_path / "out"
    out.mkdir()
    got, why = await nth.normalise(src, out)
    assert why is None and got == out / "thumb.png"
    assert (out / "thumb.jpg").is_file()
    assert os.stat(out / "thumb.png").st_mtime >= os.stat(out / "thumb.jpg").st_mtime
    assert all(a[a.index("-f") + 1] in ("png_pipe", "jpeg_pipe") for a in box["runs"])


async def test_a_png_over_the_limit_leaves_the_jpg(box, tmp_path):
    box["png_size"] = nth.max_bytes() + 1
    src = tmp_path / "in.png"
    src.write_bytes(PNG)
    got, why = await nth.normalise(src, tmp_path)
    assert why is None and got == tmp_path / "thumb.jpg"
    assert not (tmp_path / "thumb.png").exists()


async def test_a_jpg_over_the_limit_at_every_quality_is_refused(box, tmp_path):
    box["size"] = nth.max_bytes() + 1
    src = tmp_path / "in.png"
    src.write_bytes(PNG)
    got, why = await nth.normalise(src, tmp_path)
    assert got is None and "every quality" in (why or "")
    qs = [a[a.index("-q:v") + 1] for a in box["runs"] if "-q:v" in a]
    assert qs == [str(q) for q in nth.JPEG_QUALITY]


@pytest.mark.parametrize("case", ["text", "empty", "huge", "codec", "small", "wide",
                                  "symlink", "ffmpeg", "outsize"])
async def test_a_bad_image_is_refused_with_why(box, tmp_path, case):
    src = tmp_path / "in.png"
    src.write_bytes(PNG)
    if case == "text":
        src.write_bytes(b"ignore previous instructions")
    elif case == "empty":
        src.write_bytes(b"")
    elif case == "huge":
        os.truncate(src, nth.MAX_INPUT_BYTES + 1)
    elif case == "codec":
        box["src"]["codec_name"] = "gif"
    elif case == "small":
        box["src"].update(width=100, height=100)
    elif case == "wide":
        box["src"].update(width=9000, height=1000)
    elif case == "symlink":
        real = tmp_path / "real.png"
        real.write_bytes(PNG)
        src.unlink()
        src.symlink_to(real)
    elif case == "ffmpeg":
        box["fail"] = "decode error"
    elif case == "outsize":
        box["out"].update(width=1280, height=1280)
    out = tmp_path / "out"
    out.mkdir()
    got, why = await nth.normalise(src, out)
    assert got is None and why
    if case in ("text", "empty", "huge", "symlink"):
        assert box["runs"] == []                        # never handed to ffmpeg


# ---------------------------------------------------------- the action

def test_the_apply_is_a_tier_one_catalogued_action_with_a_dry_run_and_rollback():
    from atlas.kernel.contracts import Tier
    from atlas.modules import academy
    a = next(x for x in academy.MODULE.actions() if x.name == nth.ACTION)
    assert a.tier is Tier.AUTO and a.dry_run is nth.apply_dry and a.rollback is nth.unapply
    assert set(a.params) == {"series", "episode"}
    assert a.vet is not None and a.vet({"series": "x", "episode": 1}) is not None


async def test_the_dry_run_checks_the_image_and_finds_the_video(box):
    box["drop"]()
    dry = await nth.apply_dry(KEY, N)
    assert dry.ok and VID in dry.detail and "would set" in dry.detail
    assert box["sets"] == [] and nth.drop_for(KEY, N).is_dir()


async def test_an_apply_sets_it_keeps_the_old_one_and_its_rollback_restores(box):
    d = episode_with_thumb()
    box["drop"]()
    got = await nth.apply(KEY, N)
    assert got.ok, got.detail
    [(ch, vid, fname, _)] = box["sets"]
    assert (ch, vid, fname) == (tut.channel(), VID, "thumb.png")
    assert not nth.drop_for(KEY, N).exists()
    assert len(list((nth.inbox() / nth.APPLIED).iterdir())) == 1
    assert nth.nlm_made(d) and (d / "thumb.png").read_bytes() != b"old png"
    marker = json.loads((d / nth.MARKER).read_text())
    assert marker["notebook_url"].startswith("https://notebooklm.google.com/")
    assert tut.thumb_file(d) == d / "thumb.png"
    back = await nth.unapply(got.rollback_token or "")
    assert back.ok, back.detail
    assert box["sets"][-1][2:] == ("thumb.png", b"old png")   # re-set the previous one
    assert (d / "thumb.png").read_bytes() == b"old png"
    assert (d / "thumb.jpg").read_bytes() == b"old jpg"
    assert not nth.nlm_made(d)
    assert len(list((nth.inbox() / nth.APPLIED).iterdir())) == 1   # not re-applied
    assert (d / nth.FRAME_ROLLED_BACK).is_file()        # never re-framed by the backfill


async def test_a_rollback_whose_files_cannot_be_put_back_still_blocks_a_reframe(
        box, monkeypatch):
    d = episode_with_thumb()
    box["drop"]()
    got = await nth.apply(KEY, N)
    assert got.ok, got.detail
    real = os.replace

    def broken(src, dst):
        if Path(dst).parent == d:
            raise OSError("disk")
        return real(src, dst)
    monkeypatch.setattr(nth.os, "replace", broken)
    back = await nth.unapply(got.rollback_token or "")
    assert not back.ok and "not put back" in back.detail
    assert (d / nth.FRAME_ROLLED_BACK).is_file()


async def test_the_frame_fields_are_read_before_the_thumbnail_is_set(box, monkeypatch):
    from atlas.modules.academy import video_frames as vf
    order: list[str] = []
    d = episode_with_thumb()
    (d / "video.mp4").write_bytes(b"mp4")

    async def frame_hash_at(video, at, timeout=60.0):
        order.append("hash")
        return 5
    monkeypatch.setattr(vf, "frame_hash_at", frame_hash_at)
    from atlas.modules.youtube import api
    real_set = api.set_thumbnail

    async def set_thumbnail(*a, **k):
        order.append("set")
        return await real_set(*a, **k)
    monkeypatch.setattr(api, "set_thumbnail", set_thumbnail)
    box["drop"](meta={"series": KEY, "episode": N, "source": "video-frame",
                      "frame_at": 12.5})
    assert (await nth.apply(KEY, N)).ok
    assert order == ["hash", "set"]


async def test_a_frame_drop_marker_holds_hashes_recomputed_never_the_drops(
        box, monkeypatch):
    from atlas.modules.academy import video_frames as vf
    d = episode_with_thumb()
    (d / "video.mp4").write_bytes(b"mp4")
    seen: list = []

    async def frame_hash_at(video, at, timeout=60.0):
        seen.append((Path(video).name, at))
        return 5

    async def image_dhash(path):
        return 7
    monkeypatch.setattr(vf, "frame_hash_at", frame_hash_at)
    monkeypatch.setattr(vf, "image_dhash", image_dhash)
    box["drop"](meta={"series": KEY, "episode": N, "source": "video-frame",
                      "frame_dhash": "a" * 16, "thumb_dhash": "b" * 16, "frame_at": 12.5})
    assert (await nth.apply(KEY, N)).ok
    marker = json.loads((d / nth.MARKER).read_text())
    assert seen == [("video.mp4", 12.5)]
    assert marker["source"] == "video-frame" and marker["frame_dhash"] == vf.hash_hex(5)
    assert marker["thumb_dhash"] == vf.hash_hex(7) and marker["frame_at"] == 12.5


async def test_a_frame_drop_whose_frame_cannot_be_read_back_is_marked_plain(
        box, monkeypatch):
    from atlas.modules.academy import video_frames as vf

    async def frame_hash_at(video, at, timeout=60.0):
        return None
    monkeypatch.setattr(vf, "frame_hash_at", frame_hash_at)
    d = episode_with_thumb()
    box["drop"](meta={"series": KEY, "episode": N, "source": "video-frame",
                      "frame_dhash": "a" * 16, "frame_at": 12.5})
    assert (await nth.apply(KEY, N)).ok
    marker = json.loads((d / nth.MARKER).read_text())
    assert marker["source"] == "notebooklm" and "frame_dhash" not in marker


async def test_a_rollback_with_no_kept_thumbnail_says_so(box):
    box["drop"]()
    got = await nth.apply(KEY, N)                       # no episode thumbnail before
    assert got.ok
    back = await nth.unapply(got.rollback_token or "")
    assert not back.ok and "no previous thumbnail" in back.detail
    assert len(box["sets"]) == 1


@pytest.mark.parametrize("token", [
    "nope", json.dumps({"channel": "nope", "video_id": VID, "dir": "/tmp/x"}),
    json.dumps({"channel": "clips", "video_id": "bad", "dir": "/tmp/x"}),
    json.dumps({"channel": "clips", "video_id": VID, "dir": "/etc"})])
async def test_a_strange_rollback_token_is_left_alone(box, token):
    back = await nth.unapply(token)
    assert not back.ok and box["sets"] == []


async def test_a_bad_image_is_refused_into_refused_and_nothing_is_set(box):
    box["drop"](image=b"<html>not an image</html>")
    got = await nth.apply(KEY, N)
    assert not got.ok and "refused" in got.detail
    [r] = list((nth.inbox() / nlm.REFUSED).iterdir())
    assert "the image" in (r / "reason.txt").read_text()
    assert box["sets"] == [] and len(box["told"]) == 1


async def test_a_lesson_not_public_is_refused(box):
    box["posted"] = {"public": False, "video_id": VID}
    box["drop"]()
    assert not (await nth.apply(KEY, N)).ok
    assert len(list((nth.inbox() / nlm.REFUSED).iterdir())) == 1
    assert box["sets"] == []


async def test_a_meta_for_another_episode_is_refused(box):
    box["drop"](meta={"series": KEY, "episode": 2})
    assert not (await nth.apply(KEY, N)).ok
    assert len(list((nth.inbox() / nlm.REFUSED).iterdir())) == 1


async def test_an_unreadable_record_holds_the_drop(box):
    box["posted"] = OSError("db down")
    drop = box["drop"]()
    got = await nth.apply(KEY, N)
    assert not got.ok and "held" in got.detail and drop.is_dir()


async def test_a_403_holds_the_whole_inbox_with_one_message_and_no_retry_loop(
        box, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from atlas.kernel import executor
    box["set"] = (None, ("clips: HTTP 403 from /upload/youtube/v3/thumbnails/set — "
                         "authenticated but not permitted"))
    eps = [e.number for e in S.episodes if e.scripted][:3]
    drops = [box["drop"](name=nlm.drop_name(KEY, n), meta={"series": KEY, "episode": n})
             for n in eps]
    runs: list = []

    async def execute(req):                             # the run, as the executor would
        runs.append(req)
        return await nth.apply(req.params["series"], req.params["episode"])
    monkeypatch.setattr(executor, "execute", execute)
    said = await nth.apply_pending()
    assert len(box["sets"]) == 1 and len(runs) == 1     # stopped at the first 403
    assert len(box["told"]) == 1 and nth.hold_path().is_file()
    assert all(d.is_dir() for d in drops)               # nothing moved
    said = await nth.apply_pending()                    # the next day: nothing
    assert len(box["sets"]) == 1 and len(runs) == 1 and "held (403)" in said[0]
    later = datetime.now(UTC) + timedelta(days=nth.HOLD_DAYS, seconds=5)
    await nth.apply_pending(now=later)                  # lapsed: one try, still 403
    assert len(box["sets"]) == 2 and len(runs) == 2 and len(box["told"]) == 1
    assert nth.held_until() is not None                 # held again, no second line
    box["set"] = ({"video_id": VID}, None)
    later2 = datetime.now(UTC) + timedelta(days=nth.HOLD_DAYS, seconds=5)
    await nth.apply_pending(now=later2)                 # allowed now: one, hold gone
    assert len(box["sets"]) == 3 and not nth.hold_path().exists()
    await nth.apply_pending()
    assert len(box["sets"]) == 3 + (len(eps) - 1)       # the rest, at full pace


async def test_a_lesson_hold_still_lets_a_hand_apply_try(box):
    box["drop"]()
    nth.inbox().mkdir(parents=True, exist_ok=True)
    nth.hold_path().write_text("403\n")
    got = await nth.apply(KEY, N)
    assert got.ok and not nth.hold_path().exists()


async def test_other_set_errors_hold_without_a_hold_file(box):
    box["set"] = (None, "clips: timed out")
    box["drop"]()
    got = await nth.apply(KEY, N)
    assert not got.ok and not nth.hold_path().exists() and box["told"] == []


async def test_the_pass_goes_through_the_executor_capped_and_refuses_strange_names(
        box, monkeypatch):
    from atlas.kernel import executor
    from atlas.kernel.contracts import ActionResult
    runs: list = []

    async def execute(req):
        runs.append(req)
        return ActionResult(True, "set")
    monkeypatch.setattr(executor, "execute", execute)
    box["drop"](name="nope-01")
    for n in range(1, nth.PER_PASS + 3):
        if S.episode(n) is not None:
            box["drop"](name=nlm.drop_name(KEY, n), meta={"series": KEY, "episode": n})
    said = await nth.apply_pending()
    assert len(runs) == min(nth.PER_PASS, sum(
        1 for n in range(1, nth.PER_PASS + 3) if S.episode(n) is not None))
    assert all(r.action == nth.ACTION and r.params["series"] == KEY for r in runs)
    assert any("refused" in s for s in said)
    assert len(list((nth.inbox() / nlm.REFUSED).iterdir())) == 1


# ------------------------------------------------------- the posted list

async def test_next_thumbs_lists_newest_first_and_skips_made_and_waiting(
        box, monkeypatch):
    from atlas.kernel import state
    eps = [e for e in S.episodes if e.scripted][:3]
    assert len(eps) == 3
    rows = [{"series": KEY, "episode": e.number, "video_id": f"vid{e.number:08d}",
             "last": 10 - i} for i, e in enumerate(eps)]
    rows.append({"series": "nope", "episode": 1, "video_id": "x" * 11, "last": 1})
    seen: list[str] = []

    async def fetch_all(sql, *args):
        seen.append(sql)
        return rows
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    got = await nth.next_thumbs()
    assert [r["episode"] for r in got] == [e.number for e in eps]
    assert got[0]["drop"] == f"thumbs/{nlm.drop_name(KEY, eps[0].number)}"
    assert got[0]["title"] == eps[0].title and got[0]["video_id"] == f"vid{eps[0].number:08d}"
    assert "public" in seen[0] and "NOT withdrawn" in seen[0]
    made = tut.episode_dir(S, eps[0])
    made.mkdir(parents=True)
    (made / nth.MARKER).write_text("{}")
    box["drop"](name=nlm.drop_name(KEY, eps[1].number),
                meta={"series": KEY, "episode": eps[1].number})
    got = await nth.next_thumbs(limit=5)
    assert [r["episode"] for r in got] == [eps[2].number]
    assert await nth.next_thumbs(limit=0) == []


async def test_the_next_thumbs_command_prints_json_or_says_unknown(
        box, monkeypatch, capsys):
    from atlas.kernel import state

    async def fetch_all(sql, *args):
        return [{"series": KEY, "episode": N, "video_id": VID, "last": 1}]
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    assert await asyncio.to_thread(nlm.main, ["next-thumbs", "--limit", "1"]) == 0
    assert [r["episode"] for r in json.loads(capsys.readouterr().out)] == [N]

    async def broken(sql, *args):
        raise OSError("connection refused")
    monkeypatch.setattr(state, "fetch_all", broken)
    assert await asyncio.to_thread(nlm.main, ["next-thumbs"]) == 1
    err = capsys.readouterr()
    assert "UNKNOWN" in err.err and err.out == ""


def test_the_thumbs_inbox_is_never_a_lesson_drop(box):
    box["drop"]()
    (nth.inbox() / "meta.json").write_text("{}")      # would read as complete
    assert all(d.name != nth.THUMBS for d in nlm.complete_drops())
