"""NotebookLM Shorts (academy/notebooklm_shorts.py; Miro 2026-10-19: a
NotebookLM Short may run 60-90 s, the 20-40 s rule stays for the Shorts
ATLAS renders itself): what the Mac runner makes next, the text it pastes,
the Shorts inbox ingest with its holds and refusals, the relaxed length on
the clips post, and the post through the teaching Short's slot."""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.kernel.contracts import ActionResult
from atlas.modules.academy import lesson_pages
from atlas.modules.academy import notebooklm as nlm
from atlas.modules.academy import notebooklm_shorts as nls
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.content.captions import Word

KEY, N = "claude", 1
S = ts.series(KEY)
assert S is not None
EP = S.episode(N)
assert EP is not None and EP.scripted
VID = "iRyhxkLk-Yo"
SLUG = f"tutorial:{KEY}:{N}:notebooklm"
# What ffprobe read from the real NotebookLM Short (2026-10-19, 720x1280, 76 s).
GOOD_PROBE = {"width": 720, "height": 1280, "seconds": 76.0, "audio": True,
              "video_codec": "h264", "audio_codec": "aac"}
REFERENCE = ("totally different words about gardening tomatoes in the spring sun "
             "and watering them every single morning before work")


def heard(prefix: str = "", extra: str = "") -> list[Word]:
    text = " ".join(filter(None, [prefix, *lesson_pages.transcript(EP, "")[:4], extra]))
    return [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(text.split())]


@pytest.fixture
def box(monkeypatch, tmp_path):
    """The Shorts inbox with the probe, Whisper, the public video, the posts
    record and the make step (draft row, transcode, QA, media row) mocked."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "tut"))
    monkeypatch.setenv("ACADEMY_REFS_DIR", str(tmp_path / "refs"))
    st: dict = {"probe": dict(GOOD_PROBE), "words": heard(), "told": [],
                "public": (S, EP, VID), "already": None, "made": [], "whisper": 0,
                "make": None, "unrendered": []}
    refs = tmp_path / "refs" / nlm.ref_key(KEY, N)
    refs.mkdir(parents=True)
    (refs / "abc123.txt").write_text(REFERENCE)

    async def probe(path):
        return (dict(st["probe"]), None) if st["probe"] else (None, "ffprobe: gone")

    async def transcribe(video):
        st["whisper"] += 1
        return (st["words"], None) if st["words"] else (None, "Groq down")

    async def public(series, episode):
        if isinstance(st["public"], Exception):
            raise st["public"]
        return st["public"]

    async def already(s, ep):
        if isinstance(st["already"], Exception):
            raise st["already"]
        return st["already"]

    async def make(s, ep, video, text, slug, pace=1.0):
        st["pace"] = pace
        st["made"].append((s.key, ep.number, Path(video).read_bytes(), text, slug))
        if st["make"] is not None:
            return ActionResult(False, st["make"])
        return ActionResult(True, "rendered", evidence={"media_id": 41},
                            rollback_token="41")

    async def tell(text):
        st["told"].append(text)

    async def unrender(token):
        st["unrendered"].append(token)
        return ActionResult(True, f"render {token} withdrawn")
    from atlas.modules.academy import reel
    monkeypatch.setattr(tut, "_probe", probe)
    monkeypatch.setattr(nlm, "transcribe", transcribe)
    monkeypatch.setattr(tut, "_public_video", public)
    monkeypatch.setattr(nls, "already", already)
    monkeypatch.setattr(nls, "_make", make)
    monkeypatch.setattr(tut, "_tell", tell)
    monkeypatch.setattr(reel, "unrender_promo_reel", unrender)

    def drop(sha: str | None = None, name: str = nlm.drop_name(KEY, N),
             meta: dict | None = None) -> Path:
        d = nls.inbox() / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "video.mp4").write_bytes(b"\x00short")
        body = meta if meta is not None else {
            "series": KEY, "episode": N, "source_sha256": sha or nls.source_sha(EP),
            "notebook_url": "https://notebooklm.google.com/notebook/abc",
            "made_at": "2026-10-19T10:00:00+00:00"}
        (d / "meta.json").write_text(json.dumps(body))
        return d
    st["drop"] = drop
    return st


def refused() -> list[Path]:
    r = nls.inbox() / nlm.REFUSED
    return sorted(r.iterdir()) if r.is_dir() else []


# ---------------------------------------------------------------- the source

def test_the_short_source_is_the_lessons_own_text(capsys):
    assert nls.source_text(EP) == nlm.source_text(EP)
    assert nls.source_sha(EP) == nlm.source_sha(EP)
    assert nlm.main(["short-source", KEY, str(N)]) == 0
    assert capsys.readouterr().out == nlm.source_text(EP)
    assert nlm.main(["short-source", "nope", "1"]) == 2


def test_the_lesson_inbox_never_takes_the_shorts_folder(box):
    box["drop"]()
    (nls.inbox() / "meta.json").write_text("{}")       # even a stray meta
    assert nlm.complete_drops() == []
    assert [d.name for d in nls.complete_drops()] == [nlm.drop_name(KEY, N)]


# ----------------------------------------------------------------- the probe

def test_the_real_notebooklm_short_passes_the_probe_gate():
    assert nls.probe_refusal(dict(GOOD_PROBE)) is None
    assert nls.SECONDS == (50.0, 95.0)


@pytest.mark.parametrize(("change", "says"), [
    ({"video_codec": "vp9"}, "not H.264"),
    ({"audio_codec": "opus"}, "not AAC"),
    ({"audio": False}, "no sound"),
    ({"width": 1920, "height": 1080}, "not vertical"),
    ({"width": 1280, "height": 1280}, "not vertical"),
    ({"width": 540, "height": 960}, "under 720x1280"),
    ({"seconds": 40.0}, "outside 50-95"),
    ({"seconds": 100.0}, "outside 50-95"),
])
async def test_a_wrong_short_file_is_refused(box, change, says):
    assert says in (nls.probe_refusal({**GOOD_PROBE, **change}) or "")
    box["probe"].update(change)
    box["drop"]()
    got = await nls.ingest(KEY, N)
    assert not got.ok and says in got.detail
    assert len(refused()) == 1 and box["whisper"] == 0 and box["made"] == []


# ---------------------------------------------------------------- the ingest

async def test_a_good_short_is_made_and_its_rollback_puts_the_drop_back(box):
    drop = box["drop"]()
    dry = await nls.ingest_dry(KEY, N)
    assert dry.ok and dry.detail.startswith("would ingest") and box["whisper"] == 0
    got = await nls.ingest(KEY, N)
    assert got.ok, got.detail
    [(key, n, data, text, slug)] = box["made"]
    assert (key, n, data, slug) == (KEY, N, b"\x00short", SLUG)
    assert got.evidence["media_id"] == 41 and got.evidence["slug"] == SLUG
    assert got.evidence["caption"] == text == got.evidence["youtube_caption"]
    assert nls.caption_refusal(text) is None
    assert not drop.exists()
    back = await nls.uningest(got.rollback_token or "")
    assert back.ok, back.detail
    assert box["unrendered"] == ["41"]
    assert (drop / "video.mp4").read_bytes() == b"\x00short"
    assert (drop / "meta.json").is_file()


def test_the_caption_carries_the_title_link_hashtags_and_disclosure():
    text = nls.caption(S, EP, VID)
    from atlas.modules.clips import promo
    from atlas.modules.content import postiz
    title = promo.youtube_title(text)
    assert postiz.YT_TITLE_MIN <= len(title) <= postiz.YT_TITLE_MAX
    assert f"https://youtu.be/{VID}" in text and "#shorts" in text
    assert tut.VOICE_SHORT in text and text.rstrip().endswith(tut.AI_LABEL)
    assert nls.caption_refusal(text) is None
    assert nls.caption_refusal(text.replace(tut.VOICE_SHORT, "")) is not None
    assert nls.caption_refusal(text.replace(f"https://youtu.be/{VID}", "")) is not None
    assert nls.caption_refusal(text.replace(" #shorts", "")) is not None
    assert nls.caption_refusal("x" * 120 + ".\n" + text) is not None


async def test_the_opening_gate_does_not_apply_to_shorts(box):
    box["words"] = heard(prefix="Welcome to this Atlas Academy explainer.")
    box["drop"]()
    assert (await nls.ingest(KEY, N)).ok


@pytest.mark.parametrize(("words", "says"), [
    (heard(extra="it costs 987654 dollars"), "987654"),
    (heard(extra="as you can see on my screen"), "human on camera"),
])
async def test_the_transcript_gates_refuse(box, words, says):
    box["words"] = words
    box["drop"]()
    got = await nls.ingest(KEY, N)
    assert not got.ok and says in got.detail
    assert len(refused()) == 1 and box["made"] == []


async def test_eight_words_shared_with_a_human_tutorial_are_refused(box, tmp_path):
    said = " ".join(w.text for w in heard())
    (tmp_path / "refs" / nlm.ref_key(KEY, N) / "copied.txt").write_text(
        "intro " + " ".join(said.split()[2:11]) + " outro")
    box["drop"]()
    got = await nls.ingest(KEY, N)
    assert not got.ok and "8+ words" in got.detail and len(refused()) == 1


async def test_a_stale_script_is_refused(box):
    box["drop"](sha="0" * 64)
    got = await nls.ingest(KEY, N)
    assert not got.ok and "stale script" in got.detail and len(refused()) == 1


async def test_a_lesson_not_public_yet_is_refused(box):
    box["public"] = f"{KEY} #{N} is not public yet"
    box["drop"]()
    got = await nls.ingest(KEY, N)
    assert not got.ok and "not public" in got.detail and len(refused()) == 1


async def test_a_second_short_for_one_lesson_is_refused(box):
    box["already"] = "its NotebookLM Short is already posted"
    box["drop"]()
    got = await nls.ingest(KEY, N)
    assert not got.ok and "already" in got.detail and len(refused()) == 1


@pytest.mark.parametrize("break_", ["whisper", "probe", "public", "already", "refs",
                                    "make"])
async def test_unknown_holds_and_never_passes(box, tmp_path, break_):
    if break_ == "whisper":
        box["words"] = None
    elif break_ == "probe":
        box["probe"] = None
    elif break_ in ("public", "already"):
        box[break_] = OSError("db down")
    elif break_ == "refs":
        for f in (tmp_path / "refs" / nlm.ref_key(KEY, N)).iterdir():
            f.unlink()
    else:
        box["make"] = "QA: the file's encoding could not be read, so it is held"
    drop = box["drop"]()
    got = await nls.ingest(KEY, N)
    assert not got.ok and got.detail.startswith("held"), got.detail
    assert drop.is_dir() and (drop / "meta.json").is_file() and refused() == []
    await nls.ingest(KEY, N)
    assert sum("held" in t for t in box["told"]) == 1      # once a day


async def test_the_pass_goes_through_the_executor_and_refuses_strange_folders(
        box, monkeypatch):
    from atlas.kernel import executor
    seen = []

    async def execute(req):
        seen.append((req.action, req.params))
        return ActionResult(True, "ok")
    monkeypatch.setattr(executor, "execute", execute)
    box["drop"]()
    box["drop"](name="nope-07")
    got = await nls.ingest_pending()
    assert seen == [(nls.ACTION, {"series": KEY, "episode": N})]
    assert any("nope-07" in x and "refused" in x for x in got)
    assert [r.name.startswith("nope-07") for r in refused()] == [True]


def test_the_ingest_is_a_tier_one_local_action_with_a_dry_run_and_rollback():
    from atlas.kernel.contracts import Tier
    from atlas.modules import academy
    acts = {a.name: a for a in academy.MODULE.actions()}
    a = acts[nls.ACTION]
    teach = acts["academy.render_teaching_short"]
    assert a.tier is Tier.AUTO is teach.tier                # as the teaching Short's
    assert a.dry_run is not None and a.rollback is not None
    assert a.vet is not None and a.vet({"series": "x", "episode": 1}) is not None


def test_the_daily_tick_ingests_the_shorts_after_the_lessons():
    src = inspect.getsource(tut.daily)
    assert src.index("notebooklm.ingest_pending") < src.index(
        "notebooklm_shorts.ingest_pending")


# ----------------------------------------------------------------- the make

@pytest.fixture
def make(monkeypatch, tmp_path):
    """The real _make with the database, ffmpeg and the media rows faked."""
    from atlas.kernel import state as kstate
    from atlas.modules.content import ffmpeg, render
    st: dict = {"argv": [], "sql": [], "failed": [], "finished": [],
                "probe": {"streams": [
                    {"codec_type": "video", "codec_name": "h264", "profile": "High",
                     "width": 1080, "height": 1920, "pix_fmt": "yuv420p",
                     "r_frame_rate": "30/1", "avg_frame_rate": "30/1"},
                    {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000"}],
                    "format": {"duration": "76.0"}}}

    async def fetch_one(sql, *a):
        st["sql"].append((sql, a))
        return {"id": 7}

    async def run(argv, **kw):
        if argv[0] == "ffprobe":
            return json.dumps(st["probe"]).encode()
        st["argv"].append(argv)
        Path(argv[-1]).write_bytes(b"made")
        return b""

    async def new_row(draft_id, provider):
        return 41

    async def fail(media_id, reason):
        st["failed"].append((media_id, reason))
        return ActionResult(False, reason)

    async def finish(media_id, draft_id, backend, built, notes, *, captions=None):
        st["finished"].append((media_id, draft_id, Path(built).read_bytes(), captions))
        return ActionResult(True, "rendered", evidence={"media_id": media_id},
                            rollback_token=str(media_id))
    monkeypatch.setattr(kstate, "fetch_one", fetch_one)
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "missing", lambda: None)
    monkeypatch.setattr(render, "_new_row", new_row)
    monkeypatch.setattr(render, "_fail", fail)
    monkeypatch.setattr(render, "_finish", finish)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00short")
    st["video"] = video
    return st


async def test_the_make_scales_to_the_shorts_frame_and_checks_the_file(make):
    text = nls.caption(S, EP, VID)
    got = await nls._make(S, EP, make["video"], text, SLUG)
    assert got.ok, got.detail
    [argv] = make["argv"]
    joined = " ".join(argv)
    assert "scale=1080:1920" in joined and "-profile:v high" in joined
    assert "yuv420p" in joined and "-r 30" in joined and "-ar 48000" in joined
    [(sql, args)] = make["sql"]
    assert "content_drafts" in sql and args[1] == f"academy:{SLUG}" and args[3] == text
    assert make["finished"] == [(41, 7, b"made", False)]


async def test_a_made_short_out_of_spec_is_failed_never_finished(make):
    make["probe"]["format"]["duration"] = "120.0"
    got = await nls._make(S, EP, make["video"], nls.caption(S, EP, VID), SLUG)
    assert not got.ok and "outside 50-95" in got.detail
    assert make["finished"] == [] and make["failed"][0][0] == 41
    make["probe"] = {}                                  # ffprobe read nothing: UNKNOWN
    got = await nls._make(S, EP, make["video"], nls.caption(S, EP, VID), SLUG)
    assert not got.ok and "could not be read" in got.detail and make["finished"] == []


# ------------------------------------------------------------ the clips gate

@pytest.mark.parametrize(("source", "window"), [
    (f"academy:{SLUG}", (50.0, 95.0)),
    ("academy:tutorial:claude:1:payoff", (12.0, 60.0)),
    ("academy:notebooklm", (12.0, 60.0)),
    (OSError("db down"), (12.0, 60.0)),
])
async def test_only_a_notebooklm_short_gets_the_longer_window(monkeypatch, source, window):
    from atlas.kernel import state as kstate
    from atlas.modules.clips import promo
    from atlas.modules.content import quality
    seen = {}

    async def fetch_one(sql, *a):
        if isinstance(source, Exception):
            raise source
        return {"source": source}

    async def check(media_id, caption, *, series, seconds, motion=True):
        seen["seconds"] = seconds

    async def none_today():
        return 0
    monkeypatch.setattr(kstate, "fetch_one", fetch_one)
    monkeypatch.setattr(quality, "check_media", check)
    monkeypatch.setattr(promo, "posted_today", none_today)
    monkeypatch.delenv("ACADEMY_PROMO_AUTOPOST", raising=False)
    caption = nls.caption(S, EP, VID)
    assert await promo.autopublish_check(41, caption, caption) is None
    assert seen["seconds"] == window
    assert promo.NOTEBOOKLM_SECONDS == nls.SECONDS


# -------------------------------------------------------------- the posting

@pytest.fixture
def posting(monkeypatch, tmp_path):
    from atlas.kernel import executor
    from atlas.modules.academy import synthetic
    f = tmp_path / "41.mp4"
    f.write_bytes(b"made")
    st: dict = {"rows": [{"id": 41, "path": str(f), "source": f"academy:{SLUG}",
                          "body": nls.caption(S, EP, VID)}],
                "seen": [], "result": ActionResult(True, "render 41: posted"),
                "skips": [], "flagged": []}

    async def finished():
        if isinstance(st["rows"], Exception):
            raise st["rows"]
        return st["rows"]

    async def execute(req):
        st["seen"].append((req.action, req.params))
        return st["result"]

    async def skip(slug, why):
        st["skips"].append(slug)

    async def after(slug):
        st["flagged"].append(slug)
        return ""
    monkeypatch.setattr(nls, "finished", finished)
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(tut, "skip_teaching", skip)
    monkeypatch.setattr(synthetic, "after_post", after)
    return st


async def test_a_made_short_posts_through_the_clips_promo_action(posting):
    said, stop = await nls.post_next()
    [(action, params)] = posting["seen"]
    assert action == "clips.autopublish_promo"
    assert params == {"media_id": 41, "caption": posting["rows"][0]["body"],
                      "slug": SLUG, "youtube_caption": posting["rows"][0]["body"]}
    assert stop and posting["flagged"] == [SLUG] and SLUG in said[0]


async def test_a_carded_short_rests_and_a_cap_hold_waits(posting):
    from atlas.modules.clips import promo
    posting["result"] = ActionResult(True, "held (it plays 99 s); card #3 raised")
    _said, stop = await nls.post_next()
    assert not stop and posting["skips"] == [SLUG] and posting["flagged"] == []
    posting["result"] = ActionResult(True, f"not posted today (2 {promo.CAP_HELD})")
    _said, stop = await nls.post_next()
    assert stop and posting["skips"] == [SLUG]


async def test_nothing_to_post_or_an_unreadable_record_lets_the_pass_go_on(posting):
    posting["rows"] = []
    assert await nls.post_next() == ([], False)
    posting["rows"] = OSError("db down")
    _said, stop = await nls.post_next()
    assert not stop and posting["seen"] == []


def test_the_teaching_pass_posts_a_notebooklm_short_first():
    src = inspect.getsource(tut.teaching_pass)
    assert src.index("notebooklm_shorts.post_next") < src.index("untaught()")


# ------------------------------------------------------------------ next-shorts

@pytest.fixture
def lessons(box, monkeypatch):
    """claude #1 and #2 public; #1 a NotebookLM render, #2 a Fish one."""
    async def untaught():
        return [{"series": KEY, "episode": 1, "taught": [], "skipped": []},
                {"series": KEY, "episode": 2, "taught": [], "skipped": []},
                {"series": KEY, "episode": 3, "taught": ["payoff"], "skipped": []}]

    async def made():
        return set()

    async def public(series, episode):
        got = tut.resolve(series, episode)
        return (*got, VID)
    monkeypatch.setattr(tut, "untaught", untaught)
    monkeypatch.setattr(nls, "_made", made)
    monkeypatch.setattr(tut, "read_manifest", lambda d: SimpleNamespace(
        audio="notebooklm" if tut.episode_dir(S, EP) == d
        or tut.episode_dir(S, S.episode(3)) == d else "fish"))
    monkeypatch.setattr(tut, "_public_video", public)
    return box


async def test_next_shorts_lists_public_notebooklm_lessons_with_no_short(lessons):
    got = await nls.next_shorts()
    assert [(r["series"], r["episode"]) for r in got] == [(KEY, 1)]
    row = got[0]
    assert row["title"] == EP.title and row["drop"] == f"shorts/{nlm.drop_name(KEY, N)}"
    assert row["source_sha256"] == nls.source_sha(EP)
    assert row["video_id"] == VID and row["video_url"] == f"https://youtu.be/{VID}"
    assert (row["min_seconds"], row["max_seconds"]) == nls.SECONDS
    lessons["drop"]()                                 # a drop waiting: not listed again
    assert await nls.next_shorts() == []


async def test_next_shorts_skips_a_short_already_made(lessons, monkeypatch):
    async def made():
        return {(KEY, 1)}
    monkeypatch.setattr(nls, "_made", made)
    assert await nls.next_shorts(limit=5) == []



async def test_next_shorts_skips_a_lesson_on_the_rerender_list(lessons, monkeypatch):
    """notebooklm.RERENDER_POSTED: its render may be the relaunch's, so no
    Short of it points at the old video."""
    monkeypatch.setattr(nlm, "RERENDER_POSTED", frozenset({(KEY, 1)}))
    assert await nls.next_shorts(limit=5) == []

async def test_next_shorts_says_unknown_when_the_record_cannot_be_read(
        lessons, monkeypatch, capsys):
    async def broken():
        raise OSError("connection refused")
    monkeypatch.setattr(tut, "untaught", broken)
    with pytest.raises(OSError):
        await nls.next_shorts()
    assert await asyncio.to_thread(nlm.main, ["next-shorts", "--limit", "2"]) == 1
    err = capsys.readouterr()
    assert "UNKNOWN" in err.err and err.out == ""


async def test_the_next_shorts_command_prints_json(lessons, capsys):
    assert await asyncio.to_thread(nlm.main, ["next-shorts", "--limit", "1"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert [r["episode"] for r in got] == [1]


# ------------------------------------------------ the pace (Miro, 2026-10-20)

def test_a_short_is_slowed_but_stays_at_the_gates_frame():
    argv = nls.transcode_argv("in.mp4", "out.mp4", 0.9)
    vf = argv[argv.index("-vf") + 1]
    assert vf.startswith("setpts=PTS/0.9,scale=1080:1920:")
    assert vf.index("setpts") < vf.index("fps=30")
    assert argv[argv.index("-af") + 1] == "atempo=0.9"
    plain = nls.transcode_argv("in.mp4", "out.mp4")
    assert "setpts" not in plain[plain.index("-vf") + 1] and "-af" not in plain
    assert (nls.OUT_W, nls.OUT_H) == (1080, 1920)
    assert argv[argv.index("-preset") + 1] == "veryfast"
    assert argv[argv.index("-threads") + 1] == "0"


def test_a_short_never_runs_past_its_max(monkeypatch):
    monkeypatch.setenv(nlm.PACE_ENV, "0.75")
    assert nls.short_pace(75.2) == pytest.approx(0.8)    # 94 s, a second under
    assert nls.short_pace(60.0) == pytest.approx(0.75)
    assert nls.short_pace(94.0) == 1.0


async def test_a_short_ingest_passes_its_pace_to_the_make(box, monkeypatch):
    monkeypatch.setenv(nlm.PACE_ENV, "0.9")
    box["drop"]()
    got = await nls.ingest(KEY, N)
    assert got.ok, got.detail
    assert box["pace"] == pytest.approx(0.9)
    assert (got.evidence or {})["pace"] == pytest.approx(0.9)
