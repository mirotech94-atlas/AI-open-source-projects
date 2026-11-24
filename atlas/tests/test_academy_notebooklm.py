"""Academy lessons rendered by NotebookLM (academy/notebooklm.py; Miro,
2026-10-19): the source text the Mac runner is given, what it renders next,
the inbox ingest with its holds and refusals, ready()'s NotebookLM profile,
and the daily post that never falls back to the Fish voice unless told."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from atlas.kernel.contracts import ActionResult
from atlas.modules.academy import lesson_pages
from atlas.modules.academy import notebooklm as nlm
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.content.captions import Word

ROOT = Path(__file__).resolve().parents[1]
KEY, N = "claude", 1
S = ts.series(KEY)
assert S is not None
EP = S.episode(N)
assert EP is not None and EP.scripted
GOOD_PROBE = {"width": 1920, "height": 1080, "seconds": 300.0, "audio": True,
              "video_codec": "h264", "audio_codec": "aac"}
REFERENCE = ("totally different words about gardening tomatoes in the spring sun "
             "and watering them every single morning before work")


def heard_from_source(extra: str = "") -> list[Word]:
    """Whisper's words: the script's own narrated lines, plus `extra`."""
    text = " ".join(lesson_pages.transcript(EP, "")[:6]) + (" " + extra if extra else "")
    return [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(text.split())]


@pytest.fixture
def box(monkeypatch, tmp_path):
    """A tutorials root with a refs dir, the probe, Whisper, the thumbnail and
    the posts record mocked; a complete drop for claude #1 is made by
    `drop()`."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_DIR", str(tmp_path / "tut"))
    monkeypatch.setenv("ACADEMY_REFS_DIR", str(tmp_path / "refs"))
    monkeypatch.delenv("ACADEMY_NOTEBOOKLM_FALLBACK", raising=False)
    # The re-encode is off here (its own tests below turn it on).
    monkeypatch.setenv(nlm.PACE_ENV, "1.0")
    monkeypatch.setenv(nlm.HEIGHT_ENV, "0")
    state: dict = {"probe": dict(GOOD_PROBE), "words": heard_from_source(),
                   "told": [], "posted": None, "thumb": None, "probes": 0,
                   "whisper": 0}
    refs = tmp_path / "refs" / nlm.ref_key(KEY, N)
    refs.mkdir(parents=True)
    (refs / "abc123.txt").write_text(
        f"<untrusted_data source='x'>\n{REFERENCE}\n</untrusted_data>\ntrailer\n")

    async def probe(path):
        state["probes"] += 1
        return (dict(state["probe"]), None) if state["probe"] else (None, "ffprobe: gone")

    async def transcribe(video):
        state["whisper"] += 1
        return (state["words"], None) if state["words"] else (None, "Groq down")

    async def thumbnail(ep, out_dir, badge):
        if state["thumb"] is not None:
            return state["thumb"]
        (out_dir / "thumb.jpg").write_bytes(b"jpg")
        (out_dir / "thumb.png").write_bytes(b"png")
        return None

    async def posted(series, episode):
        if isinstance(state["posted"], Exception):
            raise state["posted"]
        return state["posted"]

    async def tell(text):
        state["told"].append(text)

    async def no_downloads(ep):
        return None
    monkeypatch.setattr(tut, "_probe", probe)
    monkeypatch.setattr(nlm, "transcribe", transcribe)
    monkeypatch.setattr(nlm, "_thumbnail", thumbnail)

    async def no_frame(ep, video, out_dir, badge, skip=None, skip_drop=""):
        return None, "mocked: no video frame"
    from atlas.modules.academy import notebooklm_thumbs
    monkeypatch.setattr(notebooklm_thumbs, "frame_thumbnail", no_frame)
    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(tut, "_tell", tell)
    monkeypatch.setattr(tut, "downloads_refusal", lambda ep: None)

    def drop(sha: str | None = None, meta: dict | None = None,
             name: str = nlm.drop_name(KEY, N)) -> Path:
        d = nlm.inbox() / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "video.mp4").write_bytes(b"\x00mp4")
        body = meta if meta is not None else {
            "series": KEY, "episode": N, "source_sha256": sha or nlm.source_sha(EP),
            "notebook_url": "https://notebooklm.google.com/notebook/abc",
            "made_at": "2026-10-19T10:00:00+00:00"}
        (d / "meta.json").write_text(json.dumps(body))
        return d
    state["drop"] = drop
    return state


def refused_dirs() -> list[Path]:
    r = nlm.inbox() / nlm.REFUSED
    return sorted(r.iterdir()) if r.is_dir() else []


# ------------------------------------------------------------ source text

def test_the_source_text_is_stable_and_carries_the_script():
    one, two = nlm.source_text(EP), nlm.source_text(EP)
    assert one == two and one.endswith("\n") and not one.endswith("\n\n")
    assert one.splitlines()[0] == EP.title
    assert f"Result: {ts.result_of(EP)}" in one
    for line in lesson_pages.transcript(EP, ""):
        assert line in one
    assert nlm.source_sha(EP) == hashlib.sha256(one.encode()).hexdigest()


def test_the_source_command_prints_exactly_the_text(capsys):
    assert nlm.main(["source", KEY, str(N)]) == 0
    assert capsys.readouterr().out == nlm.source_text(EP)
    assert nlm.main(["source", "nope", "1"]) == 2


def test_the_overlap_check_answers_as_the_study_tool_does():
    spec = importlib.util.spec_from_file_location(
        "academy_study_nlm", ROOT / "tools" / "academy_refs" / "study.py")
    assert spec and spec.loader
    study = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(study)
    a = "So here we go: the quick brown fox jumps over the lazy dog today, fine."
    b = "Yesterday the quick brown fox jumps over the lazy dog today again."
    assert nlm.shared_runs(a, b) == study.shared_runs(a, b) != []
    assert nlm.shared_runs(a, REFERENCE) == study.shared_runs(a, REFERENCE) == []
    assert nlm.NGRAM == study.NGRAM
    wrapped = study.wrapped(REFERENCE, "yt")
    assert nlm.unwrapped(wrapped) == study.unwrapped(wrapped) == REFERENCE


# ------------------------------------------------------------------- next

async def test_next_puts_each_series_first_episode_first_shortest_runway_first(
        box, monkeypatch):
    async def gone():
        return {("claude", 0)}

    async def runway():
        return {s.key: (1 if s.key == "local" else 5) for s in ts.SERIES if s.playlist}
    monkeypatch.setattr(nlm, "_not_needed", gone)
    monkeypatch.setattr(tut, "runway", runway)
    box["drop"]()                                      # claude #1 is waiting in the inbox
    got = await nlm.next_episodes()
    firsts = []
    for row in got:
        if row["series"] in [f["series"] for f in firsts]:
            break
        firsts.append(row)
    assert firsts[0]["series"] == "local"
    assert all(r["runway_days"] == 5 for r in firsts[1:])
    keys = [(r["series"], r["episode"]) for r in got]
    assert ("claude", 0) not in keys and ("claude", 1) not in keys
    assert ("claude", 2) in keys
    assert all(ts.series(k).episode(n).scripted for k, n in keys)
    assert all(ts.held(k, ts.series(k).episode(n)) is None for k, n in keys)
    row = next(r for r in got if (r["series"], r["episode"]) == ("claude", 2))
    assert row["source_sha256"] == nlm.source_sha(ts.series("claude").episode(2))
    assert row["drop"] == "claude-02"
    assert len(await nlm.next_episodes(limit=3)) == 3


async def test_next_says_unknown_and_fails_when_the_record_cannot_be_read(
        box, monkeypatch, capsys):
    async def broken(sql, *a):
        raise OSError("connection refused")
    monkeypatch.setattr("atlas.kernel.state.fetch_all", broken)
    with pytest.raises(OSError):
        await nlm.next_episodes()
    assert await __import__("asyncio").to_thread(nlm.main, ["next"]) == 1
    err = capsys.readouterr()
    assert "UNKNOWN" in err.err and err.out == ""


async def test_next_refuses_an_unknown_runway(box, monkeypatch):
    async def gone():
        return set()

    async def runway():
        return {"claude": None, "n8n": 3}
    monkeypatch.setattr(nlm, "_not_needed", gone)
    monkeypatch.setattr(tut, "runway", runway)
    with pytest.raises(RuntimeError, match="UNKNOWN"):
        await nlm.next_episodes()


# ----------------------------------------------------------------- ingest

async def test_a_good_drop_is_ingested_and_its_rollback_puts_it_back(box):
    drop = box["drop"]()
    d = tut.episode_dir(S, EP)
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"old fish render")
    (d / "manifest.json").write_text("{}")
    dry = await nlm.ingest_dry(KEY, N)
    assert dry.ok and dry.detail.startswith("would ingest")
    assert box["whisper"] == 0                          # the dry run never transcribes
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    assert not drop.exists()
    video = d / "video.mp4"
    assert video.is_file() and not video.is_symlink() and video.read_bytes() == b"\x00mp4"
    m = tut.read_manifest(d)
    assert m is not None and m.audio == "notebooklm" and m.seconds == 300.0
    assert m.source_sha256 == nlm.source_sha(EP) and m.timings
    assert m.notebook_url.startswith("https://notebooklm.google.com/")
    assert m.chapters == [] and m.made_at == "2026-10-19T10:00:00+00:00"
    text = (d / "description.txt").read_text()
    assert tut.VOICE_LINE in text and "Chapters" not in text
    assert (d / "thumb.png").is_file() and (d / "thumb.jpg").is_file()
    assert tut.publishable_render(str(video)) is None
    assert nlm.current(d, EP)
    back = await nlm.uningest(got.rollback_token or "")
    assert back.ok, back.detail
    assert (drop / "video.mp4").read_bytes() == b"\x00mp4" and (drop / "meta.json").is_file()
    assert (d / "video.mp4").read_bytes() == b"old fish render"
    assert (d / "manifest.json").read_text() == "{}"


async def test_a_stale_script_is_refused_into_refused(box):
    box["drop"](sha="0" * 64)
    dry = await nlm.ingest_dry(KEY, N)
    assert dry.ok and "would refuse" in dry.detail      # so the run can move it
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "stale script" in got.detail
    [r] = refused_dirs()
    assert "stale script" in (r / "reason.txt").read_text()
    assert any("refused" in t and "stale script" in t for t in box["told"])
    assert box["probes"] == 0 and box["whisper"] == 0


@pytest.mark.parametrize(("probe", "says"), [
    ({"video_codec": "vp9"}, "not H.264"),
    ({"audio_codec": "opus"}, "not AAC"),
    ({"audio": False}, "no sound"),
    ({"width": 854, "height": 480}, "under 1280x720"),
    ({"seconds": 30.0}, "outside"),
    ({"seconds": 1200.0}, "outside"),
])
async def test_a_wrong_file_is_refused(box, probe, says):
    box["probe"].update(probe)
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and says in got.detail
    assert len(refused_dirs()) == 1 and box["whisper"] == 0


async def test_a_number_the_script_never_says_is_refused(box):
    box["words"] = heard_from_source("it costs 987654 dollars")
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "a number the script doesn't (987654)" in got.detail
    assert len(refused_dirs()) == 1


def test_a_number_said_in_words_by_the_script_passes():
    assert nlm.numbers_refusal("there are 3 steps and version 3.5", "three steps, 3.5") is None
    assert nlm.numbers_refusal("1,000 users", "1000 users") is None
    assert nlm.numbers_refusal("version 4.1", "version 4.0") is not None


@pytest.mark.parametrize(("heard", "source"), [
    ("models up to 200 billion parameters",
     "the Spark supports AI models up to two hundred billion parameters"),
    ("25 prompts", "twenty-five prompts"),
    ("25 prompts", "twenty five prompts"),
    ("100 users", "one hundred users"),
    ("1,000 users", "one thousand users"),
    ("120 seconds", "one hundred and twenty seconds"),
    ("20 and 50", "twenty, then fifty"),
])
def test_a_number_spelled_out_above_fifteen_passes(heard, source):
    assert nlm.numbers_refusal(heard, source) is None


@pytest.mark.parametrize(("heard", "source"), [
    ("100 users", "a few users"),
    ("50 prompts", "forty prompts"),
    ("20 billion", "two hundred billion"),
    ("200 billion", "twenty billion"),
    ("2 prompts", "twenty-five prompts"),
])
def test_a_number_the_script_never_spells_is_still_refused(heard, source):
    assert nlm.numbers_refusal(heard, source) is not None


LOCAL_03 = ("Inside is the GB10 Grace Blackwell Superchip: twenty Arm CPU cores and "
            "a Blackwell GPU. The GB10 Grace Blackwell Superchip shares one memory.")


def test_a_misheard_model_name_the_script_names_passes():
    """local-03, 2026-10-20: Whisper heard the narrator's "GB10" as "GB100"."""
    heard = ("Right at the heart of this thing is the GB10, Grace Blackwell Superchip. "
             "the GB100 SuperChip tightly integrates the CPU and the GPU together.")
    assert nlm.numbers_refusal(heard, LOCAL_03) is None


@pytest.mark.parametrize(("heard", "source"), [
    ("the gb100 chip", "the GB10 chip"),
    ("an RTX4080 card", "an RTX4090 card"),
    ("version v2 ships", "version v1 ships"),
])
def test_an_identifier_whose_prefix_the_script_names_passes(heard, source):
    assert nlm.numbers_refusal(heard, source) is None


@pytest.mark.parametrize(("heard", "source"), [
    ("an H100 runs it", LOCAL_03),                   # no H<digits> in the script
    ("it has 100 cores", LOCAL_03),                  # a bare quantity
    ("the GB100 chip", "the Grace Blackwell chip"),  # no GB identifier at all
    ("the GB 100 chip", "the GB10 chip"),            # spaced: a bare number
    ("100GB of memory", "the GB10 chip"),            # a unit after is a quantity
    ("the GB100 has 100 cores", "the GB10 chip"),    # the bare 100 still counts
])
def test_a_number_outside_a_named_identifier_is_still_refused(heard, source):
    assert nlm.numbers_refusal(heard, source) is not None


def test_an_identifier_in_the_script_still_allows_its_bare_number():
    """Today's behaviour, kept: "GB10" in the script says the number 10."""
    assert nlm.numbers_refusal("10 cores", "the GB10 chip") is None


@pytest.mark.parametrize("phrase", ["as you can see on my screen", "let me open my editor",
                                    "I recorded this yesterday", "in my terminal",
                                    "Hi, I'm Miro"])
async def test_a_human_on_camera_claim_is_refused(box, phrase):
    box["words"] = heard_from_source(phrase)
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "human on camera" in got.detail
    assert len(refused_dirs()) == 1


async def test_no_reference_transcripts_hold_the_drop_never_pass(box, tmp_path):
    for f in (tmp_path / "refs" / nlm.ref_key(KEY, N)).iterdir():
        f.unlink()
    drop = box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "UNKNOWN" in got.detail
    assert drop.is_dir() and (drop / "meta.json").is_file() and refused_dirs() == []
    assert not (tut.episode_dir(S, EP) / "video.mp4").exists()
    assert box["whisper"] == 0
    await nlm.ingest(KEY, N)                            # said once a day, not every run
    assert sum("held" in t for t in box["told"]) == 1


async def test_the_drops_own_refs_count(box, tmp_path):
    for f in (tmp_path / "refs" / nlm.ref_key(KEY, N)).iterdir():
        f.unlink()
    drop = box["drop"]()
    (drop / "refs").mkdir()
    (drop / "refs" / "v1.txt").write_text(REFERENCE)
    assert (await nlm.ingest(KEY, N)).ok


async def test_eight_words_shared_with_a_human_tutorial_are_refused(box, tmp_path):
    words = " ".join(w.text for w in heard_from_source())
    (tmp_path / "refs" / nlm.ref_key(KEY, N) / "copied.txt").write_text(
        "intro " + " ".join(words.split()[3:12]) + " outro")
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "copied" in got.detail and "8+ words" in got.detail
    assert len(refused_dirs()) == 1


async def test_whisper_or_ffprobe_down_holds(box):
    box["words"] = None
    drop = box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and got.detail.startswith("held") and drop.is_dir()
    box["words"], box["probe"] = heard_from_source(), None
    got = await nlm.ingest(KEY, N)
    assert not got.ok and got.detail.startswith("held") and drop.is_dir()
    assert refused_dirs() == []


async def test_an_already_uploaded_episode_is_not_overwritten(box):
    box["posted"] = {"video_id": "iRyhxkLk-Yo", "public": True}
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "already uploaded" in got.detail
    box["posted"] = OSError("db down")
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.detail.startswith("held")



# ------------------------------------- a posted lesson rendered again (relaunch)

def test_the_rerender_list_is_code_only_and_names_real_scripted_lessons():
    assert nlm.RERENDER_POSTED == frozenset({("n8n", 4)})
    assert isinstance(nlm.RERENDER_POSTED, frozenset)
    for key, n in nlm.RERENDER_POSTED:
        s = ts.series(key)
        assert s is not None and s.playlist
        ep = s.episode(n)
        assert ep is not None and ep.scripted


@pytest.fixture
def rerender(box, monkeypatch):
    """claude #1 is public on YouTube and on the re-render list; nothing that
    writes outside the box may be called."""
    from atlas.kernel import state as kstate
    from atlas.modules.youtube import api
    monkeypatch.setattr(nlm, "RERENDER_POSTED", frozenset({(KEY, N)}))
    box["posted"] = {"id": 7, "video_id": "OLD1", "public": True}

    async def never(*a, **k):
        raise AssertionError("an outside-world or posts-row write")
    for name in ("upload", "set_thumbnail", "set_privacy", "set_synthetic",
                 "take_out_of_playlist", "add_playlist_item", "set_captions"):
        monkeypatch.setattr(api, name, never)
    monkeypatch.setattr(kstate, "execute_sql", never)
    return box


async def _next_keys(monkeypatch, gone_keys):
    async def gone():
        return set(gone_keys)

    async def runway():
        return {s.key: 5 for s in ts.SERIES if s.playlist}
    monkeypatch.setattr(nlm, "_not_needed", gone)
    monkeypatch.setattr(tut, "runway", runway)
    return [(r["series"], r["episode"]) for r in await nlm.next_episodes()]


async def test_next_lists_a_posted_lesson_only_when_it_is_on_the_rerender_list(
        box, monkeypatch):
    monkeypatch.setattr(nlm, "RERENDER_POSTED", frozenset({(KEY, N)}))
    keys = await _next_keys(monkeypatch, {(KEY, N), (KEY, 2)})
    assert (KEY, N) in keys and (KEY, 2) not in keys
    got = [r for r in await nlm.next_episodes() if (r["series"], r["episode"]) == (KEY, N)]
    assert got == [{"series": KEY, "episode": N, "title": EP.title,
                    "drop": nlm.drop_name(KEY, N), "source_sha256": nlm.source_sha(EP),
                    "min_seconds": EP.min_seconds, "max_seconds": EP.max_seconds,
                    "runway_days": 5}]
    monkeypatch.setattr(nlm, "RERENDER_POSTED", frozenset())
    assert (KEY, N) not in await _next_keys(monkeypatch, {(KEY, N)})


async def test_a_rerender_lesson_leaves_next_once_its_current_render_is_in(
        rerender, monkeypatch):
    rerender["drop"]()
    assert (KEY, N) not in await _next_keys(monkeypatch, {(KEY, N)})   # drop waiting
    assert (await nlm.ingest(KEY, N)).ok
    assert (KEY, N) not in await _next_keys(monkeypatch, {(KEY, N)})   # current now


async def test_a_rerender_of_a_posted_lesson_is_stored_never_uploaded(rerender):
    d = tut.episode_dir(S, EP)
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"the posted render")
    (d / "manifest.json").write_text("{}")
    drop = rerender["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    assert not drop.exists() and (d / "video.mp4").read_bytes() == b"\x00mp4"
    assert nlm.current(d, EP)
    assert rerender["whisper"] == 1 and rerender["probes"] >= 1    # the gates ran
    back = await nlm.uningest(got.rollback_token or "")
    assert back.ok and (d / "video.mp4").read_bytes() == b"the posted render"


async def test_a_rerender_with_a_stale_script_is_still_refused(rerender):
    rerender["drop"](sha="0" * 64)
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "stale script" in got.detail
    assert not nlm.current(tut.episode_dir(S, EP), EP)


async def test_a_rerender_still_meets_the_gates_after_the_posts_check(rerender):
    rerender["words"] = heard_from_source("as you can see on my screen")
    rerender["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "refused" in got.detail
    rerender["drop"]()
    rerender["probe"]["video_codec"] = "vp9"
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "H.264" in got.detail
    rerender["drop"]()
    rerender["posted"] = OSError("db down")                # the record: UNKNOWN, held
    got = await nlm.ingest(KEY, N)
    assert got.detail.startswith("held")
    assert not nlm.current(tut.episode_dir(S, EP), EP)


async def test_a_rerender_is_never_posted_by_the_automatic_publish(rerender, monkeypatch):
    """The automatic post never uploads it: the episode has a public row, so
    _publish refuses, and lineup never schedules a posted episode."""
    from atlas.kernel import idempotency
    rerender["drop"]()
    assert (await nlm.ingest(KEY, N)).ok

    async def no_claim(*a, **k):
        raise AssertionError("claimed an upload")
    monkeypatch.setattr(idempotency, "claim", no_claim)
    got = await tut._publish(KEY, N, strict=True)
    assert not got.ok and "already public" in got.detail
    todo, _notes = tut.lineup({(KEY, N)}, {})
    assert (KEY, N) not in todo


async def test_the_relaunch_takes_an_ingested_rerender(rerender, monkeypatch):
    """relaunch_tutorial's check passes on a NotebookLM manifest of today's
    script (it names no script_key), and refuses one of an older script."""
    rerender["drop"]()
    assert (await nlm.ingest(KEY, N)).ok

    async def rows(_s, _n):
        return [{"id": 7, "video_id": "OLD1", "public": True, "withdrawn": False}]
    monkeypatch.setattr(tut, "_episode_rows", rows)
    dry = await tut.relaunch_tutorial_dry(KEY, N)
    assert dry.ok, dry.detail
    assert "OLD1" in dry.detail and "nothing is deleted" in dry.detail
    monkeypatch.setattr(nlm, "source_sha", lambda ep: "f" * 64)   # script moved on
    for fn in (tut.relaunch_tutorial_dry, tut.relaunch_tutorial):
        got = await fn(KEY, N)
        assert not got.ok and nlm.WAITING in got.detail

async def test_a_meta_that_names_another_episode_is_refused(box):
    box["drop"](meta={"series": "n8n", "episode": 3, "source_sha256": "a" * 64})
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "not the folder's" in got.detail


async def test_a_half_written_drop_is_left_alone(box):
    d = nlm.inbox() / nlm.drop_name(KEY, N)
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"x")
    assert nlm.complete_drops() == []
    (d / "meta.json").write_text("{not json")
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "not complete" in got.detail and d.is_dir()
    assert not (await nlm.ingest_dry(KEY, N)).ok


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
    got = await nlm.ingest_pending()
    assert seen == [(nlm.ACTION, {"series": KEY, "episode": N})]
    assert any("nope-07" in x and "refused" in x for x in got)
    assert [r.name.startswith("nope-07") for r in refused_dirs()] == [True]


def test_the_ingest_is_a_tier_one_local_action_with_a_rollback():
    from atlas.kernel.contracts import Tier
    from atlas.modules import academy
    a = next(x for x in academy.MODULE.actions() if x.name == nlm.ACTION)
    assert a.tier is Tier.AUTO and a.dry_run is not None and a.rollback is not None
    assert a.vet is not None and a.vet({"series": "x", "episode": 1}) is not None


# ------------------------------------------------------------ ready, post

async def test_ready_takes_a_notebooklm_render_and_keeps_the_metadata_gates(
        box, monkeypatch):
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    got, why = await tut.ready(KEY, N)
    assert why is None and got is not None
    _s, _ep, _d, m, text = got
    assert m.audio == "notebooklm" and tut.VOICE_LINE in text and tut.AI_LABEL in text
    assert ts.STORE_URL in text

    real = tut.description
    monkeypatch.setattr(tut, "description",
                        lambda *a, **k: real(*a, **k).replace(tut.VOICE_LINE, ""))
    _got, why = await tut.ready(KEY, N)
    assert why is not None and "synthetic voice" in why
    monkeypatch.setattr(tut, "description", real)
    monkeypatch.setattr(tut, "final_title", lambda ep, seconds: "x" * 120)
    _got, why = await tut.ready(KEY, N)
    assert why is not None and "title" in why


async def test_ready_refuses_a_notebooklm_render_of_an_older_script(box, monkeypatch):
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    monkeypatch.setattr(nlm, "source_sha", lambda ep: "f" * 64)
    _got, why = await tut.ready(KEY, N)
    assert why is not None and "older script" in why


async def test_ready_refuses_a_notebooklm_file_out_of_length(box):
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    box["probe"]["seconds"] = 900.0
    _got, why = await tut.ready(KEY, N)
    assert why is not None and "outside" in why


async def test_the_upload_is_flagged_synthetic_with_the_voice_line(box, monkeypatch):
    from atlas.kernel import idempotency
    from atlas.modules.youtube import api
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    calls = {}

    async def claim(action, key):
        return idempotency.CLAIMED

    async def settle(*a):
        return None

    async def upload(ch, path, title, text, tags, **kw):
        calls["upload"] = (path, text, kw)
        return None, "stop here"

    async def finish(*a):
        return None
    monkeypatch.setattr(idempotency, "claim", claim)
    monkeypatch.setattr(idempotency, "finish", finish)
    monkeypatch.setattr(api, "upload", upload)
    got = await tut._publish(KEY, N, strict=True)
    assert not got.ok and "stop here" in got.detail
    path, text, kw = calls["upload"]
    assert kw["synthetic"] is True and tut.VOICE_LINE in text
    assert path == str(tut.episode_dir(S, EP) / "video.mp4")


@pytest.fixture
def post(box, monkeypatch):
    from atlas.kernel import executor
    monkeypatch.delenv("ACADEMY_TUTORIALS_AUTOPUBLISH", raising=False)
    seen = []

    async def execute(req):
        seen.append(req.action)
        return ActionResult(True, "ok", dispatched=True)
    monkeypatch.setattr(executor, "execute", execute)
    box["seen"] = seen
    return box


async def test_without_a_notebooklm_render_the_post_waits_and_never_renders(post):
    said = await tut._post([(KEY, N)])
    assert post["seen"] == []
    assert said == [f"{KEY} #{N}: {nlm.WAITING}"]
    assert any(nlm.WAITING in t for t in post["told"])
    await tut._post([(KEY, N)])
    assert sum(nlm.WAITING in t for t in post["told"]) == 1


async def test_a_fish_render_on_disk_is_not_posted_while_the_fallback_is_off(
        post, monkeypatch):
    monkeypatch.setattr(tut, "reusable", lambda s, ep: True)
    await tut._post([(KEY, N)])
    assert post["seen"] == []


async def test_a_notebooklm_render_is_posted_without_rendering(post):
    post["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    await tut._post([(KEY, N)])
    assert post["seen"] == ["academy.autopublish_tutorial"]


async def test_the_fallback_switch_brings_the_fish_render_back(post, monkeypatch):
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    await tut._post([(KEY, N)])
    assert post["seen"] == ["academy.render_tutorial", "academy.autopublish_tutorial"]


def _age(path: Path, days: float) -> None:
    import os
    import time
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_the_fallback_is_the_switch_only(box):
    assert nlm.fallback_enabled() is False
    assert not (nlm.inbox() / nlm.WAITING_SINCE).exists()   # no side effect


def test_the_first_look_marks_when_the_wait_began(box):
    assert nlm.stale() is False
    assert (nlm.inbox() / nlm.WAITING_SINCE).is_file()


def test_days_without_a_render_never_turn_the_fallback_on(box):
    """Miro, 2026-10-19: NotebookLM videos only. The 3-day revert of
    ace186c4 is gone; a stale lane is said, never filled with Fish."""
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 3.5)
    assert nlm.fallback_enabled() is False
    assert nlm.stale() is True


async def test_ten_days_without_a_render_still_post_nothing_old(post, monkeypatch):
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 10)
    monkeypatch.setattr(tut, "reusable", lambda s, ep: True)   # a Fish render on disk
    await tut._post([(KEY, N)])
    assert post["seen"] == []


async def test_an_ingest_ends_the_staleness(box):
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 10)
    assert nlm.stale() is True
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    assert (nlm.inbox() / nlm.LAST_INGEST).is_file()
    assert nlm.stale() is False
    assert nlm.fallback_enabled() is False


def test_the_later_marker_counts(box):
    nlm.stale()
    (nlm.inbox() / nlm.LAST_INGEST).touch()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 10)
    _age(nlm.inbox() / nlm.LAST_INGEST, 1)
    assert nlm.stale() is False
    _age(nlm.inbox() / nlm.LAST_INGEST, 4)
    assert nlm.stale() is True


def test_unreadable_marks_are_unknown_never_fresh(box, monkeypatch, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    monkeypatch.setattr(nlm, "inbox", lambda: blocker / "inbox")
    assert nlm.stale() is None


def test_the_switch_still_forces_the_fallback_on(box, monkeypatch):
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    assert nlm.fallback_enabled() is True


async def test_a_stale_lane_is_said_once_a_day_on_the_ingest_tick(box, monkeypatch):
    told: set[str] = set()
    monkeypatch.setattr(tut, "_already_told", lambda note: note in told)
    monkeypatch.setattr(tut, "_mark_told", told.add)

    async def owed():
        return 2
    monkeypatch.setattr(nlm, "waiting_work", owed)
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 4.2)
    said = await nlm.ingest_pending()
    await nlm.ingest_pending()
    msgs = [t for t in box["told"] if "no NotebookLM video ingested" in t]
    assert len(msgs) == 1
    assert msgs[0].startswith("Academy: no NotebookLM video ingested for 4 days with 2 "
                              "waiting; nothing old-design is posted.")
    assert "Atlas Academy NotebookLM videos" in msgs[0]
    assert any("no NotebookLM video ingested" in line for line in said)


async def test_a_fresh_lane_says_nothing(box, monkeypatch):
    monkeypatch.setattr(tut, "_already_told", lambda note: False)
    monkeypatch.setattr(tut, "_mark_told", lambda note: None)
    await nlm.ingest_pending()
    assert not [t for t in box["told"] if "NotebookLM video" in t]


async def test_unreadable_marks_are_said_as_unknown(box, monkeypatch, tmp_path):
    monkeypatch.setattr(tut, "_already_told", lambda note: False)
    monkeypatch.setattr(tut, "_mark_told", lambda note: None)
    monkeypatch.setattr(nlm, "idle_for", lambda now=None: None)
    monkeypatch.setattr(nlm, "complete_drops", list)
    await nlm.ingest_pending()
    assert [t for t in box["told"] if "UNKNOWN" in t]


async def test_no_teaching_short_is_cut_from_a_notebooklm_render(box, monkeypatch):
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok

    async def public(series, episode):
        return S, EP, "iRyhxkLk-Yo"
    monkeypatch.setattr(tut, "_public_video", public)
    got = await tut._teaching_cut(KEY, N, "payoff")
    assert isinstance(got, str) and "NotebookLM" in got


# ------------------------------------------------------- the opening gate
#
# Miro, 2026-10-17: a lesson opens on the finished result, never on a
# welcome. The first OPENING_SECONDS of narration are read from Whisper's
# word timings; two refusals for one episode let the third drop past this
# one gate (the runner would remake it forever), every other gate intact.

def opening(prefix: str, extra: str = "", at: float = 0.0) -> list[Word]:
    """`prefix` said from `at` s, then the script's own lines."""
    head = [Word(w, at + i * 0.4, at + i * 0.4 + 0.3) for i, w in enumerate(prefix.split())]
    t0 = (head[-1].end + 0.2) if head else 0.0
    rest = [Word(w.text, t0 + w.start, t0 + w.end) for w in heard_from_source(extra)]
    return head + rest


def seed_refusals(why: str, n: int, name: str = nlm.drop_name(KEY, N),
                  hour: int = 10) -> None:
    for i in range(n):
        d = nlm.inbox() / nlm.REFUSED / f"{name}-2026101{i}T{hour:02d}0000Z"
        d.mkdir(parents=True)
        (d / "reason.txt").write_text(why + "\n")


@pytest.mark.parametrize("prefix", [
    "Welcome to this Atlas Academy explainer.", "In this video we build it.",
    "In this explainer, the result first.", "Today we're making an agent.",
    "Today we’re making an agent.", "Today we are making an agent.",
    "Hi everyone, here it is.", "Hello everyone.", "Hello, everyone."])
async def test_a_lesson_that_opens_with_a_welcome_is_refused(box, prefix):
    box["words"] = opening(prefix)
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and nlm.WELCOME in got.detail
    [r] = refused_dirs()
    assert (r / "reason.txt").read_text().startswith(nlm.WELCOME)


def test_the_welcome_pattern_is_a_compiled_constant():
    assert isinstance(nlm.WELCOME_OPENING, __import__("re").Pattern)
    assert nlm.OPENING_SECONDS == 12.0


async def test_a_welcome_after_the_first_12_s_is_not_an_opening(box):
    box["words"] = opening("welcome back to the part that matters", at=12.5)
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok


async def test_one_welcome_refusal_is_not_enough_for_the_waiver(box):
    seed_refusals(nlm.WELCOME + " ('welcome')", 1)
    seed_refusals("stale script", 3, hour=11)          # other reasons never count
    seed_refusals(nlm.WELCOME, 2, name="claude-011")   # another episode's never count
    box["words"] = opening("Welcome to this Atlas Academy explainer.")
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and nlm.WELCOME in got.detail


async def test_the_third_welcome_drop_passes_this_gate_and_says_so_once(box):
    seed_refusals(nlm.WELCOME + " ('welcome')", 2)
    box["words"] = opening("Welcome to this Atlas Academy explainer.")
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    said = [t for t in box["told"] if "welcome opening" in t]
    assert len(said) == 1 and f"{KEY} #{N}" in said[0]
    assert tut._already_told(f"notebooklm welcome {nlm.drop_name(KEY, N)}")


async def test_the_waiver_never_skips_another_gate(box):
    seed_refusals(nlm.WELCOME, 2)
    box["words"] = opening("Welcome to this Atlas Academy explainer.",
                           "it costs 987654 dollars")
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and "987654" in got.detail
    assert not any("welcome opening" in t for t in box["told"])


# ----------------------------------------------------------- lane health
#
# Invariant 7: a lane that can't be read is UNKNOWN, never OK; a lane with
# no ingest for STALE_AFTER is DEGRADED (Health has no WARN) with the reason.

async def test_a_fresh_lane_is_ok(box):
    from atlas.kernel.contracts import Health
    obs = await nlm.check_lane()
    assert obs.health is Health.OK and obs.check == "notebooklm_lane"


async def test_a_stale_lane_is_degraded_with_the_reason(box, monkeypatch):
    from atlas.kernel.contracts import Health

    async def owed():
        return 3
    monkeypatch.setattr(nlm, "waiting_work", owed)
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 5.5)
    obs = await nlm.check_lane()
    assert obs.health is Health.DEGRADED
    assert "no NotebookLM video ingested for 5 days" in obs.detail
    assert obs.value == 5.0 and obs.unit == "days"
    assert "3 waiting" in obs.detail and "Mac" in obs.detail


async def test_an_idle_lane_with_nothing_queued_is_ok_and_says_why(box, monkeypatch):
    from atlas.kernel.contracts import Health

    async def none_owed():
        return 0
    monkeypatch.setattr(nlm, "waiting_work", none_owed)
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 18.5)
    obs = await nlm.check_lane()
    assert obs.health is Health.OK and "nothing is waiting" in obs.detail
    assert await nlm.warn_stale() is None


async def test_an_idle_lane_with_an_unreadable_queue_is_unknown(box, monkeypatch):
    from atlas.kernel.contracts import Health

    async def unreadable():
        return None
    monkeypatch.setattr(nlm, "waiting_work", unreadable)
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 18.5)
    obs = await nlm.check_lane()
    assert obs.health is Health.UNKNOWN and "can't be read" in obs.detail


async def test_an_unreadable_lane_is_unknown_never_ok(box, monkeypatch):
    from atlas.kernel.contracts import Health
    monkeypatch.setattr(nlm, "idle_for", lambda now=None: None)
    obs = await nlm.check_lane()
    assert obs.health is Health.UNKNOWN and obs.value is None


def test_the_academy_module_runs_the_lane_check():
    from atlas.modules import academy
    checks = {c.name: c for c in academy.AcademyModule().checks()}
    assert checks["notebooklm_lane"].run is nlm.check_lane
    assert checks["notebooklm_lane"].baseline_sigma is None


# ------------------------------------------- no Fish teaching Short (step 3)

@pytest.fixture
def teaching(box, monkeypatch):
    """teaching_pass with the promo slots free, no NotebookLM Short made, and
    every step of the old Fish teaching-cut path booby-trapped."""
    from atlas.kernel import executor
    from atlas.modules.academy import notebooklm_shorts as nls
    from atlas.modules.clips import promo
    seen: list[str] = []

    async def zero():
        return 0

    async def nothing():
        return [], False

    async def execute(req):
        seen.append(req.action)
        return ActionResult(True, "ok", dispatched=True)

    async def row():
        return [{"series": KEY, "episode": N, "taught": [], "skipped": []}]

    monkeypatch.setattr(promo, "posted_today", zero)
    monkeypatch.setattr(promo, "per_day", lambda: 3)
    monkeypatch.setattr(nls, "post_next", nothing)
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(tut, "untaught", row)
    box["seen"] = seen
    return box


async def test_no_fish_teaching_short_runs_while_the_fallback_is_off(
        teaching, monkeypatch):
    async def trap(*a, **k):
        raise AssertionError("the Fish teaching-cut path ran")
    monkeypatch.setattr(tut, "_next_cut", trap)
    monkeypatch.setattr(tut, "render_teaching_short", trap)
    said = await tut.teaching_pass()
    assert teaching["seen"] == []
    assert "academy.render_teaching_short" not in teaching["seen"]
    assert any("ACADEMY_NOTEBOOKLM_FALLBACK" in line for line in said)


async def test_the_fallback_switch_lets_the_fish_teaching_cut_run(
        teaching, monkeypatch):
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    tried: list[int] = []

    async def cut(row):
        tried.append(int(row["episode"]))
        return "no cut"
    monkeypatch.setattr(tut, "_next_cut", cut)
    await tut.teaching_pass()
    assert tried == [N]


async def test_the_autopublish_action_never_posts_a_fish_lesson(box, monkeypatch):
    """The catalog action itself, not only its caller (_post), refuses an
    episode with no current NotebookLM render while the switch is off: no
    upload and no card."""
    async def never(*a, **k):
        raise AssertionError("went on to publish or card")
    monkeypatch.setattr(tut, "autopublish_check", never)
    monkeypatch.setattr(tut, "_publish", never)
    monkeypatch.setattr(tut, "_card", never)
    got = await tut.autopublish_tutorial(KEY, N)
    assert not got.ok and "ACADEMY_NOTEBOOKLM_FALLBACK" in got.detail


async def test_the_autopublish_action_takes_a_notebooklm_render(box, monkeypatch):
    box["drop"]()
    assert (await nlm.ingest(KEY, N)).ok

    async def why(series, episode):
        return "held for the test"

    async def card(series, episode, why):
        return ActionResult(True, f"card: {why}")
    monkeypatch.setattr(tut, "autopublish_check", why)
    monkeypatch.setattr(tut, "_card", card)
    got = await tut.autopublish_tutorial(KEY, N)
    assert got.ok and got.detail == "card: held for the test"


@pytest.mark.parametrize("name", ["publish_tutorial", "publish_tutorial_dry",
                                  "relaunch_tutorial", "relaunch_tutorial_dry"])
async def test_a_card_for_a_fish_lesson_never_posts_it(box, monkeypatch, name):
    """A publish or relaunch card made for a Fish render before 2026-10-19
    is refused on approval while the switch is off."""
    async def never(*a, **k):
        raise AssertionError("went on to publish")
    monkeypatch.setattr(tut, "_publish", never)
    monkeypatch.setattr(tut, "ready", never)
    monkeypatch.setattr(tut, "_relaunch_check", never)
    got = await getattr(tut, name)(KEY, N)
    assert not got.ok and "ACADEMY_NOTEBOOKLM_FALLBACK" in got.detail


@pytest.mark.parametrize("kind", ["payoff", "stuck", "quiz"])
async def test_the_teaching_short_action_itself_refuses_while_the_fallback_is_off(
        box, monkeypatch, kind):
    """academy.render_teaching_short (and its dry run, and the pass's
    _next_cut) refuse before reading anything: every teaching cut draws
    ATLAS pages and a closing card over the lesson, so no cut is
    NotebookLM's alone; its Shorts come from the NotebookLM Shorts inbox."""
    async def never(*a, **k):
        raise AssertionError("read the episode to cut it")
    monkeypatch.setattr(tut, "_public_video", never)
    why = await tut._teaching_cut(KEY, N, kind)
    assert isinstance(why, str)
    assert "ACADEMY_NOTEBOOKLM_FALLBACK" in why and "inbox" in why
    for run in (tut.render_teaching_short, tut.render_teaching_short_dry):
        got = await run(KEY, N, kind)
        assert not got.ok and got.detail == why
    got = await tut._next_cut({"series": KEY, "episode": N, "taught": [],
                               "skipped": []})
    assert got == why


async def test_the_teaching_pass_says_why_once_while_the_fallback_is_off(teaching):
    said = await tut.teaching_pass()
    held = [line for line in said if "ACADEMY_NOTEBOOKLM_FALLBACK" in line]
    assert len(held) == 1 and "ATLAS" in held[0] and "inbox" in held[0]


async def test_with_the_switch_on_the_teaching_cut_reads_the_episode(box, monkeypatch):
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")

    async def gone(series, episode):
        return "not public"
    monkeypatch.setattr(tut, "_public_video", gone)
    assert await tut._teaching_cut(KEY, N, "payoff") == "not public"


async def test_with_the_switch_on_a_stale_lane_does_not_claim_nothing_old_posts(
        box, monkeypatch):
    monkeypatch.setenv("ACADEMY_NOTEBOOKLM_FALLBACK", "true")
    monkeypatch.setattr(tut, "_already_told", lambda note: False)
    monkeypatch.setattr(tut, "_mark_told", lambda note: None)

    async def owed():
        return 2
    monkeypatch.setattr(nlm, "waiting_work", owed)
    nlm.stale()
    _age(nlm.inbox() / nlm.WAITING_SINCE, 4.2)
    line = await nlm.warn_stale()
    obs = await nlm.check_lane()
    for text in (line or "", obs.detail):
        assert "nothing old-design is posted" not in text
        assert "ACADEMY_NOTEBOOKLM_FALLBACK is on" in text


# ------------------------------------------- pace and 4K (Miro, 2026-10-20)

@pytest.mark.parametrize(("raw", "want"), [
    (None, 0.9), ("0.8", 0.8), ("0.5", 0.75), ("1.4", 1.0), ("x", 0.9), ("nan", 0.9),
])
def test_the_pace_is_clamped(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv(nlm.PACE_ENV, raising=False)
    else:
        monkeypatch.setenv(nlm.PACE_ENV, raw)
    assert nlm.pace_setting() == pytest.approx(want)


def test_the_pace_never_takes_a_file_past_its_max():
    assert nlm.effective_pace(0.9, 300.0, 600.0) == pytest.approx(0.9)
    assert nlm.effective_pace(0.9, 570.0, 600.0) == pytest.approx(0.95)
    assert nlm.effective_pace(0.9, 600.0, 600.0) == 1.0
    assert nlm.effective_pace(0.75, 700.0, 600.0) == 1.0


def test_the_cues_move_to_the_slowed_clock():
    got = nlm.slowed_cues([(0.0, 1.8, "a"), (9.0, 13.5, "b")], 0.9)
    assert got == [(0.0, 2.0, "a"), (10.0, 15.0, "b")]


@pytest.mark.parametrize(("raw", "height", "want"), [
    (None, 720, 2160), (None, 1080, 2160), (None, 2160, None), (None, 2400, None),
    ("0", 720, None), ("1440", 1080, 1440), ("bad", 1080, 2160),
])
def test_the_height_never_downscales_and_zero_is_off(monkeypatch, raw, height, want):
    if raw is None:
        monkeypatch.delenv(nlm.HEIGHT_ENV, raising=False)
    else:
        monkeypatch.setenv(nlm.HEIGHT_ENV, raw)
    assert nlm.upscale_to(height, nlm.height_setting()) == want


def test_the_ffmpeg_args_slow_and_upscale():
    argv = nlm.reencode_argv("in.mp4", "out.mp4", 0.9, 2160)
    vf = argv[argv.index("-vf") + 1]
    assert vf == "setpts=PTS/0.9,scale=-2:2160:flags=lanczos"
    assert argv[argv.index("-af") + 1] == "atempo=0.9"
    for flag, val in (("-c:v", "libx264"), ("-preset", "veryfast"), ("-crf", "18"),
                      ("-threads", "0"),
                      ("-pix_fmt", "yuv420p"), ("-c:a", "aac"), ("-b:a", "192k"),
                      ("-movflags", "+faststart")):
        assert argv[argv.index(flag) + 1] == val
    assert argv[-1] == "out.mp4"
    only_up = nlm.reencode_argv("in.mp4", "out.mp4", 1.0, 2160)
    assert only_up[only_up.index("-vf") + 1] == "scale=-2:2160:flags=lanczos"
    assert "-af" not in only_up
    only_slow = nlm.reencode_argv("in.mp4", "out.mp4", 0.9, None)
    assert only_slow[only_slow.index("-vf") + 1] == "setpts=PTS/0.9"


@pytest.fixture
def encode(box, monkeypatch):
    """The re-encode on (0.9, 2160): ffmpeg.run writes the output, and the
    probe answers the drop's values for the drop's file, else the output's."""
    from atlas.modules.content import ffmpeg
    monkeypatch.setenv(nlm.PACE_ENV, "0.9")
    monkeypatch.setenv(nlm.HEIGHT_ENV, "2160")
    box["runs"], box["fail"] = [], None
    box["out_probe"] = dict(GOOD_PROBE, width=3840, height=2160,
                            seconds=round(300.0 / 0.9, 2))
    dropped = str(Path(nlm.INBOX) / nlm.drop_name(KEY, N) / "video.mp4")

    async def run(argv, *, timeout=0.0):
        box["runs"].append((argv, timeout))
        if box["fail"]:
            raise ffmpeg.FfmpegError(box["fail"])
        Path(argv[-1]).write_bytes(b"\x00slow4k")
        return b""

    async def probe(path):
        box["probes"] += 1
        if path.endswith(dropped):
            return dict(box["probe"]), None
        return (dict(box["out_probe"]), None) if box["out_probe"] else (None, "gone")
    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(tut, "_probe", probe)
    return box


async def test_an_ingest_slows_and_upscales_and_its_rollback_restores(encode):
    box = encode
    drop = box["drop"]()
    d = tut.episode_dir(S, EP)
    d.mkdir(parents=True)
    (d / "video.mp4").write_bytes(b"old render")
    (d / "manifest.json").write_text("{}")
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    [(argv, timeout)] = box["runs"]
    assert "atempo=0.9" in argv and timeout >= 3600
    assert (d / "video.mp4").read_bytes() == b"\x00slow4k"
    assert sorted(p.name for p in d.iterdir()) == sorted(
        ["video.mp4", "manifest.json", "thumb.png", "thumb.jpg", "description.txt",
         nlm.KEPT_META])                              # only the processed file
    m = tut.read_manifest(d)
    assert m is not None and (m.width, m.height) == (3840, 2160)
    assert m.seconds == pytest.approx(333.33) and m.minutes == tut.minutes_of(333.33)
    want = nlm.cues(box["words"])
    assert len(m.timings) == len(want)
    for (a, b, t), (a0, b0, t0) in zip(m.timings, want, strict=True):
        assert (a, b, t) == (pytest.approx(round(a0 / 0.9, 2)),
                             pytest.approx(round(b0 / 0.9, 2)), t0)
    ev = got.evidence or {}
    assert ev["pace"] == pytest.approx(0.9) and (ev["width"], ev["height"]) == (3840, 2160)
    assert "0.90x" in got.detail and "3840x2160" in got.detail
    back = await nlm.uningest(got.rollback_token or "")
    assert back.ok, back.detail
    assert (drop / "video.mp4").read_bytes() == b"\x00mp4"   # the untouched drop
    assert (drop / "meta.json").is_file()
    assert (d / "video.mp4").read_bytes() == b"old render"
    assert not any((nlm.inbox() / nlm.ORIGINALS).iterdir())


async def test_the_pace_is_capped_by_the_episode_max(encode):
    box = encode
    limit = nlm.lesson_limit(EP, EP.max_seconds * 0.95)
    assert limit <= EP.max_seconds - nlm.CAP_HEADROOM
    src = limit * 0.95
    box["probe"]["seconds"] = src
    box["out_probe"]["seconds"] = limit + 0.05      # ffmpeg overshoots a little
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    assert (got.evidence or {})["pace"] == pytest.approx(0.95)
    [(argv, _)] = box["runs"]
    assert "atempo=0.95" in argv


async def test_a_4k_render_passes_ready(encode):
    encode["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    got, why = await tut.ready(KEY, N)
    assert why is None and got is not None
    _s, _ep, _d, m, _text = got
    assert (m.width, m.height) == (3840, 2160)


async def test_nothing_to_do_skips_the_reencode(encode, monkeypatch):
    box = encode
    monkeypatch.setenv(nlm.PACE_ENV, "1.0")
    box["probe"].update(width=3840, height=2160)
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    assert box["runs"] == [] and (got.evidence or {})["reencoded"] is False
    assert (tut.episode_dir(S, EP) / "video.mp4").read_bytes() == b"\x00mp4"


@pytest.mark.parametrize("fault", ["ffmpeg", "unprobed", "wrong size", "wrong length"])
async def test_a_failed_reencode_holds_and_never_installs_the_original(encode, fault):
    box = encode
    if fault == "ffmpeg":
        box["fail"] = "ffmpeg exited 1: boom"
    elif fault == "unprobed":
        box["out_probe"] = None
    elif fault == "wrong size":
        box["out_probe"].update(width=1920, height=1080)
    else:
        box["out_probe"]["seconds"] = 300.0
    drop = box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert not got.ok and got.detail.startswith("held"), got.detail
    assert (drop / "video.mp4").read_bytes() == b"\x00mp4"
    assert (drop / "meta.json").is_file()
    assert not (tut.episode_dir(S, EP) / "video.mp4").exists()
    assert refused_dirs() == []
    assert any("held" in t for t in box["told"])


@pytest.mark.parametrize(("fps", "want"), [
    (30.0, "fps=30"), (24.0, "fps=24"), (30000 / 1001, "fps=30000/1001"),
    (None, None), (0.0, None), ("x", None),
])
def test_the_slowed_file_keeps_the_source_frame_rate(fps, want):
    argv = nlm.reencode_argv("in.mp4", "out.mp4", 0.9, 2160, fps)
    vf = argv[argv.index("-vf") + 1].split(",")
    assert vf[0] == "setpts=PTS/0.9" and vf[-1] == "scale=-2:2160:flags=lanczos"
    if want is None:
        assert len(vf) == 2
    else:
        assert vf[1] == want                       # right after setpts


async def test_the_ingest_passes_the_drops_frame_rate(encode):
    encode["probe"]["fps"] = 30.0
    encode["drop"]()
    assert (await nlm.ingest(KEY, N)).ok
    [(argv, _)] = encode["runs"]
    assert "setpts=PTS/0.9,fps=30," in argv[argv.index("-vf") + 1]


def test_originals_older_than_seven_days_are_pruned_and_newer_kept(box):
    from datetime import UTC, datetime, timedelta
    now = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
    base = nlm.inbox() / nlm.ORIGINALS

    def kept(age: timedelta) -> Path:
        at = (now - age).strftime("%Y%m%dT%H%M%S%fZ")
        d = base / f"claude-01-{at}"
        d.mkdir(parents=True)
        (d / "video.mp4").write_bytes(b"x")
        return d
    old = kept(timedelta(days=7, seconds=1))
    edge = kept(timedelta(days=7))
    new = kept(timedelta(days=6, hours=23))
    odd = base / "not-a-stamp"
    odd.mkdir()
    assert nlm.prune_originals(now) == 1
    assert not old.exists() and edge.is_dir() and new.is_dir() and odd.is_dir()


async def test_a_pruned_original_rollback_says_so(encode):
    encode["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok
    import shutil
    shutil.rmtree(nlm.inbox() / nlm.ORIGINALS)
    back = await nlm.uningest(got.rollback_token or "")
    assert not back.ok and "pruned" in back.detail


# ------------------------------------- the drop's own thumbnail (2026-10-20)

@pytest.fixture
def nlm_thumb(box, monkeypatch):
    """notebooklm_thumbs.normalise mocked: a 1280x720 pair, or box["why"]."""
    from atlas.modules.academy import notebooklm_thumbs as nth
    box["why"] = None
    box["normalised"] = []

    async def normalise(src, out_dir):
        box["normalised"].append(Path(src).read_bytes())
        if box["why"]:
            (out_dir / "thumb.jpg").write_bytes(b"half")
            return None, box["why"]
        (out_dir / "thumb.jpg").write_bytes(b"nlm jpg")
        (out_dir / "thumb.png").write_bytes(b"nlm png")
        return out_dir / "thumb.png", None
    monkeypatch.setattr(nth, "normalise", normalise)
    return box


async def test_a_drop_thumbnail_is_used_marked_and_restored_by_the_rollback(nlm_thumb):
    drop = nlm_thumb["drop"]()
    (drop / "thumb.png").write_bytes(b"\x89PNG drop")
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    assert got.evidence["thumbnail"] == "notebooklm" and "thumbnail_note" not in got.evidence
    d = tut.episode_dir(S, EP)
    assert (d / "thumb.png").read_bytes() == b"nlm png"
    assert json.loads((d / nlm.THUMB_MARKER).read_text())["notebook_url"]
    m = tut.read_manifest(d)
    assert m is not None and m.badge == ""
    back = await nlm.uningest(got.rollback_token or "")
    assert back.ok, back.detail
    assert (drop / "thumb.png").read_bytes() == b"\x89PNG drop"
    assert not (d / nlm.THUMB_MARKER).exists()
    assert not any((nlm.inbox() / nlm.ORIGINALS).iterdir())


async def test_a_bad_drop_thumbnail_falls_back_to_the_drawn_one_and_says_so(nlm_thumb):
    drop = nlm_thumb["drop"]()
    (drop / "thumb.png").write_bytes(b"not an image")
    nlm_thumb["why"] = "it is not a PNG or JPEG image"
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail                           # never a refusal
    assert got.evidence["thumbnail"] == "drawn"
    assert "not a PNG" in got.evidence["thumbnail_note"]
    d = tut.episode_dir(S, EP)
    assert (d / "thumb.png").read_bytes() == b"png"     # the drawn one (mocked)
    assert (d / "thumb.jpg").read_bytes() == b"jpg"
    assert not (d / nlm.THUMB_MARKER).exists()


async def test_a_drop_with_no_thumbnail_keeps_the_drawn_one(nlm_thumb):
    nlm_thumb["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok and got.evidence["thumbnail"] == "drawn"
    assert nlm_thumb["normalised"] == []
    assert not (tut.episode_dir(S, EP) / nlm.THUMB_MARKER).exists()
    assert json.loads(got.rollback_token or "{}")["thumb"] == ""


async def test_a_render_with_the_drops_thumbnail_passes_ready(nlm_thumb):
    drop = nlm_thumb["drop"]()
    (drop / "thumb.png").write_bytes(b"\x89PNG drop")
    assert (await nlm.ingest(KEY, N)).ok
    got, why = await tut.ready(KEY, N)
    assert why is None and got is not None


# ------------------------------- a frame of the lesson's own video (2026-10-20)

async def test_with_no_drop_thumbnail_a_video_frame_is_used_and_marked(box, monkeypatch):
    from atlas.modules.academy import notebooklm_thumbs as nth
    seen: list = []

    async def frame(ep, video, out_dir, badge, skip=None, skip_drop=""):
        seen.append((Path(video).name, skip_drop))
        (out_dir / "thumb.jpg").write_bytes(b"frame jpg")
        (out_dir / "thumb.png").write_bytes(b"frame png")
        return {"source": nth.FRAME_SOURCE, "frame_dhash": "00ff00ff00ff00ff",
                "thumb_dhash": "0f0f0f0f0f0f0f0f", "frame_at": 42.5}, None
    monkeypatch.setattr(nth, "frame_thumbnail", frame)
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok, got.detail
    assert got.evidence["thumbnail"] == "video-frame"
    assert "thumbnail_note" not in got.evidence
    assert seen == [("video.mp4", nlm.drop_name(KEY, N))]
    d = tut.episode_dir(S, EP)
    assert (d / "thumb.png").read_bytes() == b"frame png"
    marker = json.loads((d / nlm.THUMB_MARKER).read_text())
    assert marker["source"] == "video-frame" and marker["frame_dhash"] == "00ff00ff00ff00ff"
    assert marker["frame_at"] == 42.5
    back = await nlm.uningest(got.rollback_token or "")
    assert back.ok and not (d / nlm.THUMB_MARKER).exists()


async def test_a_frame_not_unique_enough_is_used_and_said(box, monkeypatch):
    from atlas.modules.academy import notebooklm_thumbs as nth

    async def frame(ep, video, out_dir, badge, skip=None, skip_drop=""):
        (out_dir / "thumb.jpg").write_bytes(b"j")
        (out_dir / "thumb.png").write_bytes(b"p")
        return {"source": nth.FRAME_SOURCE, "frame_dhash": "0" * 16, "frame_at": 1.0,
                "note": "no candidate frame is 10+ bits from every other lesson's"}, None
    monkeypatch.setattr(nth, "frame_thumbnail", frame)
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok and got.evidence["thumbnail"] == "video-frame"
    assert "10+ bits" in got.evidence["thumbnail_note"]


async def test_a_failed_frame_falls_back_to_the_drawn_one_and_says_so(box):
    box["drop"]()
    got = await nlm.ingest(KEY, N)
    assert got.ok and got.evidence["thumbnail"] == "drawn"
    assert "video frame was not used (mocked: no video frame)" in \
        got.evidence["thumbnail_note"]
    assert (tut.episode_dir(S, EP) / "thumb.png").read_bytes() == b"png"


def test_numbers_refusal_accepts_a_digit_by_digit_read():
    assert nlm.numbers_refusal(
        "open localhost, port 5678", "open localhost, port five six seven eight"
    ) is None
    # the digits must match, and two words alone are not a number
    assert nlm.numbers_refusal("port 5679", "port five six seven eight") is not None
    assert nlm.numbers_refusal("port 67", "it is six seven") is not None
