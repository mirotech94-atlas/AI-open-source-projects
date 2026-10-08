"""Task 55 (d): rendering a reel without a new bill, and with Veo when there
is room under its cap.

What is load-bearing here:

* The backend is chosen per call, in order: veo (key, ffmpeg, and the WHOLE
  story under VEO_MONTHLY_SECONDS) -> openart (token) -> local.
* Veo: the request / poll / download shape against a fake transport, the key
  only ever in the `x-goog-api-key` header, every beat prompt headed by the
  story's character sheet, beats x 8 s counted up front and the story refused
  as a whole at the cap -- never half-rendered.
* The local path: Fish narration + stills or typographic cards + ffmpeg, the
  argv shape Miro proved on production (single-frame inputs, zoompan,
  drawtext "Made with AI", a PART 1 end card on a cliffhanger), a
  content_media row and a public token like the OpenArt path; "ffmpeg not
  installed" when it is not, before any row exists.
* Captions: 3-word chunks, the active word yellow, MarginV 330, DejaVu Sans,
  burned with the `ass` filter.
* No real brand or person reaches a prompt. No secret reaches a reason.
  Every process is `asyncio.create_subprocess_exec`, never `subprocess.run`.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib

import httpx
import pytest

from atlas.kernel.contracts import Health
from atlas.modules import content
from atlas.modules.content import (
    captions,
    cards,
    ffmpeg,
    guard,
    higgsfield,
    reel_script,
    render,
    stills,
    veo,
)
from atlas.modules.content.drafts import AI_LABEL

PKG = pathlib.Path(__file__).resolve().parent.parent / "atlas" / "modules" / "content"

CLIFF = reel_script.compose(
    'cliffhanger "Part 1" story',
    ["The lighthouse keeper had not seen a ship in forty years.",
     "Tonight a light answered his.",
     "It was coming from under the water.",
     "And it was spelling his name."],
    "Nobody told him the sea was gone.",
    characters="an old lighthouse keeper, grey beard, yellow oilskin coat")
# A draft written before 2026-09-27, when reels were narrated: it still
# renders with its narrator. Every new draft is dialogue (CLIFF).
NARRATED = reel_script.compose(
    'cliffhanger "Part 1" story',
    ["The lighthouse keeper had not seen a ship in forty years.",
     "Tonight a light answered his.",
     "It was coming from under the water.",
     "And it was spelling his name."],
    "Nobody told him the sea was gone.",
    characters="an old lighthouse keeper, grey beard, yellow oilskin coat",
    dialogue=False)


# ================================================================ guard

@pytest.mark.parametrize(("text", "hit"), [
    ("A cat drinks a can of Red Bull", "Red Bull"),
    ("She met Taylor Swift at the fair", "Taylor Swift"),
    ("He checked his iPhone again", "iPhone"),
    ("A PlayStation glowed in the dark", "PlayStation"),
    ("The SuperCola™ fizzed", "SuperCola™"),
])
def test_the_guard_says_what_it_caught(text, hit):
    why = guard.refusal(text)
    assert why is not None and hit in why


@pytest.mark.parametrize("text", [
    "Grandma baked an apple pie.", "Apple pie was warm on the sill.",
    "The amazon parrot sulked.", "A drake paddled past the swan.",
    "He opened a coke at the diner.", "a shell on the beach",
])
def test_common_nouns_on_the_list_pass(text):
    assert guard.refusal(text) is None


@pytest.mark.parametrize(("text", "hit"), [
    ("She bought an Apple watch.", "Apple"),
    ("Apple iPhone in hand, he waited.", "iPhone"),
    ("A nike® shoe on the step", "nike®"),
    ("He drove to the Amazon warehouse.", "Amazon"),
    ("Nike shoes lined the hall.", "Nike"),
])
def test_names_written_as_names_are_refused(text, hit):
    why = guard.refusal(text)
    assert why is not None and hit in why


def test_ordinary_story_words_pass_the_guard():
    assert guard.refusal(
        "The Carrot family argued at dinner. Grandma Potato said no. "
        "A ginger cat named Biscuit watched from the fridge.") is None


async def test_render_video_refuses_a_branded_script_before_any_backend(
        memory_backend, monkeypatch):
    memory_backend.responses["FROM content_drafts"] = {
        "id": 3, "kind": "story_script",
        "body": reel_script.compose("mystery hook", ["A Nike box ticks."], "What?")}

    async def boom(*a, **k):
        raise AssertionError("no backend may be chosen for a refused prompt")

    monkeypatch.setattr(render, "choose_backend", boom)
    result = await content.render_video(3)
    assert result.ok is False and "Nike" in result.detail


# ================================================================ script

def test_compose_and_parse_round_trip():
    script = reel_script.parse(CLIFF)
    assert script.format == 'cliffhanger "Part 1" story'
    assert len(script.scenes) == 4 and script.scenes[0].startswith("The lighthouse")
    assert script.caption.endswith(AI_LABEL)
    assert script.part_one is True
    assert script.characters.startswith("an old lighthouse keeper")
    assert CLIFF.splitlines()[3] == reel_script.DIALOGUE_MARK
    assert script.dialogue is True
    assert reel_script.parse(NARRATED).dialogue is False


def test_an_older_free_form_story_script_still_parses():
    body = f"Hook line.\nBeat one.\nBeat two.\nA caption.\n\n{AI_LABEL}"
    script = reel_script.parse(body)
    assert script.scenes == ("Hook line.", "Beat one.", "Beat two.")
    assert script.caption.startswith("A caption.") and script.part_one is False


# ================================================================ captions (ASS)

def _words(n: int) -> list[captions.Word]:
    return [captions.Word(f"w{i}", i * 0.5, i * 0.5 + 0.4) for i in range(n)]


def test_the_ass_file_chunks_three_words_and_lights_the_active_one():
    ass = captions.build_ass(_words(7), 4.0, part_one=True)
    lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: 0,")]
    assert len(lines) == 7                      # one event per spoken word
    assert "{\\c&H0000FFFF&}w0{\\c&H00FFFFFF&} w1 w2" in lines[0]
    assert "w0 {\\c&H0000FFFF&}w1{\\c&H00FFFFFF&} w2" in lines[1]
    assert lines[3].endswith("{\\c&H0000FFFF&}w3{\\c&H00FFFFFF&} w4 w5")
    assert lines[6].endswith("{\\c&H0000FFFF&}w6{\\c&H00FFFFFF&}")
    assert "Style: Caption,DejaVu Sans,66," in ass and ",330,1\n" in ass
    assert f"Tag,,0,0,0,,{AI_LABEL}" in ass
    assert "Part,,0,0,0,,PART 1" in ass


def test_without_word_timings_the_label_is_still_there():
    ass = captions.build_ass(None, 10.0)
    assert f"0:00:00.00,0:00:10.00,Tag,,0,0,0,,{AI_LABEL}" in ass
    assert "Dialogue: 0," not in ass and "PART 1" not in ass


async def test_groq_is_asked_for_word_and_segment_timings(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret-123")
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"words": [
            {"word": " Hello", "start": 0.0, "end": 0.4},
            {"word": "there", "start": 0.4, "end": 0.9}]})

    _transport(monkeypatch, captions, handler)
    words, why = await captions.word_timings(b"mp3")
    assert why is None and [w.text for w in words] == ["Hello", "there"]
    assert seen["auth"] == "Bearer gsk-secret-123"
    assert "gsk-secret-123" not in seen["url"]
    for part in (b"whisper-large-v3", b"verbose_json", b"word", b"segment"):
        assert part in seen["body"]


async def test_a_groq_failure_is_a_reason_without_the_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-secret-123")
    _transport(monkeypatch, captions,
               lambda r: httpx.Response(401, json={"error": "gsk-secret-123 bad"}))
    words, why = await captions.word_timings(b"mp3")
    assert words is None and "gsk-secret-123" not in why


# ================================================================ ffmpeg argv

def _stills_argv(end: bool = True):
    return ffmpeg.stills_argv(
        ["/w/s1.png", "/w/s2.png", "/w/s3.png"], [240, 240, 250], "/w/n.mp3",
        "/w/out.mp4", label_file="/w/label.txt", font="/w/font.ttf",
        end_card="/w/end.png" if end else None,
        end_text="/w/end.txt" if end else None)


def test_each_still_is_a_single_input_frame_through_zoompan():
    argv = _stills_argv()
    assert "-loop" not in argv
    inputs = [argv[i + 1] for i, a in enumerate(argv) if a == "-i"]
    assert inputs == ["/w/s1.png", "/w/s2.png", "/w/s3.png", "/w/n.mp3",
                      "/w/end.png"]
    # no "-t" in front of any input: zoompan's d= alone sets each scene's length
    first_input = argv.index("-i")
    assert "-t" not in argv[:argv.index("-filter_complex")][first_input:]
    graph = argv[argv.index("-filter_complex") + 1]
    assert graph.count("zoompan=") == 4          # three scenes + the end card
    assert "d=240" in graph and "d=250" in graph
    assert "concat=n=4:v=1:a=0" in graph
    assert "drawtext=textfile='/w/label.txt'" in graph
    assert "textfile='/w/end.txt'" in graph      # the PART 1 card
    assert argv[argv.index("-map") + 1] == "[v]"
    assert "3:a" in argv and argv[-1] == "/w/out.mp4"
    assert "aac" in argv and "yuv420p" in argv


def test_no_end_card_without_a_cliffhanger():
    graph = _stills_argv(end=False)[_stills_argv(end=False).index("-filter_complex") + 1]
    assert graph.count("zoompan=") == 3 and "concat=n=3" in graph


def test_a_path_the_filter_syntax_would_misread_is_refused():
    with pytest.raises(ffmpeg.FfmpegError):
        ffmpeg.filter_path("/tmp/it's.txt")


def test_the_captions_are_burned_with_the_ass_filter():
    argv = ffmpeg.burn_argv("/w/joined.mp4", "/w/c.ass", "/w/out.mp4")
    vf = argv[argv.index("-vf") + 1]
    assert "ass='/w/c.ass'" in vf and "scale=1080:1920" in vf


def test_the_concat_list_and_a_re_encoded_join():
    listing = ffmpeg.concat_list(["/w/clip_1.mp4", "/w/clip_2.mp4", "/w/clip_3.mp4"])
    assert listing == ("file '/w/clip_1.mp4'\nfile '/w/clip_2.mp4'\n"
                       "file '/w/clip_3.mp4'\n")
    argv = ffmpeg.concat_argv("/w/clips.txt", "/w/joined.mp4")
    assert argv[argv.index("-f") + 1] == "concat"
    assert "copy" not in argv and "libx264" in argv


def test_no_process_is_started_any_way_but_create_subprocess_exec():
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in (
                    "run", "Popen", "call", "check_output", "system"):
                base = node.value
                if isinstance(base, ast.Name) and base.id in ("subprocess", "os"):
                    raise AssertionError(f"{path.name}: {base.id}.{node.attr}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""])
                assert "subprocess" not in names, path.name


def test_a_card_is_a_valid_png():
    png = cards.card(54, 96)
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and b"IHDR" in png and png.endswith(
        b"IEND\xaeB`\x82")


# ================================================================ backend choice

@pytest.fixture
def fake_ffmpeg(monkeypatch, tmp_path):
    """ffmpeg and ffprobe "installed"; every command recorded, and its output
    file (the last argv element) written, so the pipeline can proceed."""
    ran: list[list[str]] = []
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")

    async def fake_run(argv, *, timeout=ffmpeg.TIMEOUT_S):
        ran.append(list(argv))
        if argv[-1].endswith((".mp4", ".mp3")):
            pathlib.Path(argv[-1]).write_bytes(b"media")
        return b""

    async def fake_duration(path):
        return 24.0

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "duration", fake_duration)
    monkeypatch.setattr(content, "DATA_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    return ran


@pytest.fixture
def no_ffmpeg(monkeypatch):
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": None)


def _spent(memory_backend, seconds: int):
    memory_backend.responses["FROM veo_usage"] = {"seconds": seconds}


async def test_veo_first_when_keyed_and_under_the_cap(memory_backend, fake_ffmpeg,
                                                     monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("OPENART_TOKEN", "o-tok")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 100)
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == "veo"
    assert "32 s" in choice.why and "100/600" in choice.why and "UTC" in choice.why


async def test_a_story_that_would_cross_the_cap_goes_to_openart(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("OPENART_TOKEN", "o-tok")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 580)                 # 4 beats x 8 s = 32 > 20 left
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == "openart" and "580/600" in choice.why


async def test_local_when_neither_veo_nor_openart(memory_backend, fake_ffmpeg,
                                                 monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENART_TOKEN", raising=False)
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == "local"


async def test_no_ffmpeg_means_no_veo(memory_backend, no_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.delenv("OPENART_TOKEN", raising=False)
    _spent(memory_backend, 0)
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == "local" and "ffmpeg not installed" in choice.why


# ================================================================ Veo

def _transport(monkeypatch, module, handler):
    real = httpx.AsyncClient

    def factory(*a, **k):
        k.pop("transport", None)
        return real(*a, transport=httpx.MockTransport(handler), **k)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


async def test_veo_submits_polls_and_downloads(memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    monkeypatch.delenv("VEO_MODEL", raising=False)
    monkeypatch.setattr(veo, "POLL_S", 0)
    calls: list[tuple[str, str]] = []
    polls = {"n": 0}

    def handler(request):
        assert request.headers["x-goog-api-key"] == "g-secret-key"
        assert "g-secret-key" not in str(request.url)
        calls.append((request.method, request.url.path))
        if request.url.path.endswith(":predictLongRunning"):
            body = json.loads(request.content)
            assert body == {"instances": [{"prompt": "a scene"}],
                            "parameters": {"aspectRatio": "9:16",
                                           "durationSeconds": 8,
                                           "resolution": veo.resolution()}}
            return httpx.Response(200, json={"name": "models/veo/operations/op1"})
        if request.url.path.endswith("/operations/op1"):
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(200, json={"name": "op1"})
            return httpx.Response(200, json={"done": True, "response": {
                "generateVideoResponse": {"generatedSamples": [
                    {"video": {"uri": "https://generativelanguage.googleapis.com"
                               "/v1beta/files/clip:download"}}]}}})
        if request.url.path == "/v1beta/files/clip:download":
            return httpx.Response(200, content=b"MP4BYTES")
        return httpx.Response(500)

    _transport(monkeypatch, veo, handler)
    data = await veo.clip("a scene")
    assert data == b"MP4BYTES"
    submit = "/v1beta/models/veo-3.1-fast-generate-preview:predictLongRunning"
    assert calls[0] == ("POST", submit)
    assert [c for c in calls if "operations" in c[1]] == [
        ("GET", "/v1beta/models/veo/operations/op1")] * 2
    # clip() charges nothing itself: render_veo reserved before asking
    assert not any("veo_usage" in sql for sql, _ in memory_backend.sql_log)


async def test_a_wrong_veo_model_says_so(memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    monkeypatch.setenv("VEO_MODEL", "veo-nope")
    _transport(monkeypatch, veo, lambda r: httpx.Response(404, json={}))
    with pytest.raises(veo.VeoError) as info:
        await veo.clip("a scene")
    assert "veo-nope" in str(info.value) and "404" in str(info.value)
    assert not any("veo_usage" in sql for sql, _ in memory_backend.sql_log)


async def test_a_veo_error_never_carries_the_key(memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    _transport(monkeypatch, veo, lambda r: httpx.Response(
        403, json={"error": {"message": "key g-secret-key is not allowed"}}))
    with pytest.raises(veo.VeoError) as info:
        await veo.clip("a scene")
    assert "g-secret-key" not in str(info.value) and "403" in str(info.value)


async def test_a_429_quota_body_is_marked_daily_quota(memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    _transport(monkeypatch, veo, lambda r: httpx.Response(
        429, json={"error": {"message": "Quota exceeded for quota metric "
                                        "'GenerateVideoRequestsPerDay'"}}))
    with pytest.raises(veo.VeoError) as info:
        await veo.clip("a scene")
    assert info.value.status_code == 429
    assert info.value.daily_quota is True


async def test_a_plain_429_is_not_marked_daily_quota(memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    _transport(monkeypatch, veo, lambda r: httpx.Response(
        429, json={"error": {"message": "Too many requests, slow down"}}))
    with pytest.raises(veo.VeoError) as info:
        await veo.clip("a scene")
    assert info.value.status_code == 429
    assert info.value.daily_quota is False


async def test_the_whole_story_is_refused_at_the_cap_before_the_first_clip(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 580)

    async def boom(*a, **k):
        raise AssertionError("no clip may be requested past the cap")

    monkeypatch.setattr(veo, "clip", boom)
    result = await render.render_veo(1, reel_script.parse(CLIFF))
    assert result.ok is False
    assert "32 s" in result.detail and "580/600" in result.detail
    assert "as a whole" in result.detail
    assert not any("INSERT INTO content_media" in sql
                   for sql, _ in memory_backend.sql_log)


async def test_a_veo_render_heads_every_beat_with_the_character_sheet(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 42}
    prompts: list[str] = []

    async def fake_clip(prompt):
        prompts.append(prompt)
        return b"CLIP"

    async def fake_words(audio, filename="reel.mp3"):
        return [captions.Word("The", 0.0, 0.3)], None

    monkeypatch.setattr(veo, "clip", fake_clip)
    monkeypatch.setattr(captions, "word_timings", fake_words)
    listed: list[str] = []
    real_list = ffmpeg.concat_list
    monkeypatch.setattr(ffmpeg, "concat_list",
                        lambda clips: listed.append(real_list(clips)) or listed[-1])

    script = reel_script.parse(CLIFF)
    result = await render.render_veo(7, script)

    assert result.ok is True, result.detail
    sheet = script.character_sheet()
    assert len(prompts) == 4
    assert all(p.startswith(sheet) for p in prompts)
    assert "Beat 3 of 4: It was coming from under the water." in prompts[2]
    assert listed[0].count("file '") == 4 and "clip_1.mp4" in listed[0]
    commands = [" ".join(a) for a in fake_ffmpeg]
    assert any("-f concat" in c for c in commands)
    assert any("ass=" in c for c in commands)
    inserted = [args for sql, args in memory_backend.sql_log
                if "INSERT INTO content_media" in sql]
    assert inserted == [(7, "veo")]
    usage = [args for sql, args in memory_backend.sql_log
             if "INSERT INTO veo_usage" in sql]
    assert usage == [(32, "veo-3.1-fast-generate-preview", 42)]  # all 4 used
    done = [args for sql, args in memory_backend.sql_log
            if "SET status = 'done'" in sql]
    assert done and done[0][0] == 42 and done[0][2]            # a media token
    token = done[0][2]
    assert "media_url" not in result.evidence and result.evidence["public"] is True
    assert token not in result.detail and token not in json.dumps(result.evidence)
    assert "32/600" in result.detail and "UTC" in result.detail


# ================================================================ local

async def _fake_fish(text):
    return b"MP3", "Fish Audio, the British reels voice (REELS_FISH_VOICE_ID)", None


async def test_the_local_render_makes_a_row_and_a_token(memory_backend, fake_ffmpeg,
                                                        monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(stills, "narration", _fake_fish)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 43}
    result = await render.render_local(8, reel_script.parse(CLIFF))

    assert result.ok is True, result.detail
    inserted = [args for sql, args in memory_backend.sql_log
                if "INSERT INTO content_media" in sql]
    assert inserted == [(8, "local")]
    done = [args for sql, args in memory_backend.sql_log
            if "SET status = 'done'" in sql]
    assert done and done[0][0] == 43 and done[0][1].endswith("/43.mp4")
    assert os.path.isfile(done[0][1])
    assert result.evidence["public"] is True
    memory_backend.responses["FROM content_media WHERE id"] = {
        "media_token": done[0][2], "media_token_expires_at": done[0][3]}
    url, _ = await content.public_link(43)
    assert url == f"https://atlas.example/media/{done[0][2]}"
    argv = fake_ffmpeg[-1]
    graph = argv[argv.index("-filter_complex") + 1]
    assert "-loop" not in argv and graph.count("zoompan=") == 5   # 4 + PART 1
    assert "drawtext=" in graph
    assert "typographic cards" in result.detail and "PART 1" in result.detail


async def test_stills_refused_on_404_fall_back_to_cards_and_say_so(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("GEMINI_IMAGE_MODEL", "gemini-nope")
    monkeypatch.setattr(stills, "narration", _fake_fish)
    _transport(monkeypatch, stills, lambda r: httpx.Response(404, json={}))
    memory_backend.responses["INSERT INTO content_media"] = {"id": 44}
    result = await render.render_local(9, reel_script.parse(CLIFF))
    assert result.ok is True
    assert "gemini-nope" in result.detail and "404" in result.detail
    assert "typographic cards instead" in result.detail


async def test_the_local_render_refuses_without_ffmpeg(memory_backend, no_ffmpeg,
                                                      monkeypatch):
    async def boom(text):
        raise AssertionError("nothing may be narrated without ffmpeg")

    monkeypatch.setattr(stills, "narration", boom)
    result = await render.render_local(8, reel_script.parse(CLIFF))
    assert result.ok is False and result.detail == "refused: ffmpeg not installed"
    assert not any("content_media" in sql for sql, _ in memory_backend.sql_log)


async def test_render_video_dry_never_offers_a_slideshow(memory_backend, no_ffmpeg,
                                                        monkeypatch):
    """Miro, 2026-09-27: motion, not slides. With no Veo and no OpenArt the
    only backend left is the stills render, and it is refused by name."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENART_TOKEN", raising=False)
    memory_backend.responses["FROM content_drafts"] = {
        "id": 3, "kind": "story_script", "body": CLIFF}
    result = await content.render_video_dry(3)
    assert result.ok is False and "no slideshow reels" in result.detail


async def test_fish_narration_failure_carries_no_key(monkeypatch):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "fish-secret-9")
    _transport(monkeypatch, stills, lambda r: httpx.Response(401, json={}))
    audio, _, why = await stills.narration("hello")
    assert audio is None and "fish-secret-9" not in why


async def test_the_british_voice_is_chosen_only_when_named(monkeypatch):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "fish-secret-9")
    monkeypatch.setenv("REELS_FISH_VOICE_ID", "british-voice-1")
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, content=b"MP3")

    _transport(monkeypatch, stills, handler)
    audio, note, _ = await stills.narration("hello")
    assert audio == b"MP3" and sent["reference_id"] == "british-voice-1"
    assert "British" in note


# ================================================================ the check

async def test_render_backend_is_unknown_without_ffmpeg(memory_backend, no_ffmpeg,
                                                       monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    obs = await content.check_render_backend()
    assert obs.health == Health.UNKNOWN and "ffmpeg not installed" in obs.detail


async def test_render_backend_shows_veo_seconds_with_the_read_time(
        memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 48)
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")

    async def caps():
        return ffmpeg.Capabilities(True, True, True, True)

    monkeypatch.setattr(ffmpeg, "capabilities", caps)
    obs = await content.check_render_backend()
    assert obs.health == Health.OK, obs.detail
    assert "Veo 48/600 s this month (read " in obs.detail
    assert obs.evidence["backend"] == "veo"


async def test_render_backend_reports_depleted_veo_credits_and_never_ok(
        memory_backend, monkeypatch):
    """Veo's HTTP 402 (2026-09-30) is a state of its own: the balance cannot
    be read, only the refusal can, so the check says when Veo last refused
    and where the top-up is -- and is not OK until Veo accepts a clip."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 48)
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")

    async def caps():
        return ffmpeg.Capabilities(True, True, True, True)

    monkeypatch.setattr(ffmpeg, "capabilities", caps)
    veo.note_depleted()
    try:
        obs = await content.check_render_backend()
        assert obs.health == Health.FAILING, obs.detail
        assert "prepaid credits depleted" in obs.detail
        assert "ai.studio" in obs.detail and "UTC" in obs.detail
        assert obs.evidence["veo_credits"] == "depleted"
        veo.note_accepted()
        obs = await content.check_render_backend()
        assert obs.health == Health.OK, obs.detail
    finally:
        veo.note_accepted()


async def test_render_backend_with_veo_off_reads_higgsfield_and_is_failing_without_credits(
        memory_backend, monkeypatch):
    """REELS_RENDERER=higgsfield: the check reads Higgsfield's own check.
    Higgsfield refusing (no credits) is FAILING, never OK, never Veo."""
    monkeypatch.setenv("REELS_RENDERER", "higgsfield")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv(higgsfield.KEY_ID_ENV, "fake-id")
    monkeypatch.setenv(higgsfield.SECRET_ENV, "fake-secret")
    _spent(memory_backend, 0)
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")

    async def caps():
        return ffmpeg.Capabilities(True, True, True, True)

    monkeypatch.setattr(ffmpeg, "capabilities", caps)
    from atlas.kernel.contracts import Observation

    async def broke():
        return Observation("higgsfield_budget", Health.FAILING,
                           detail="insufficient credits (HTTP 403) (read 23:40 UTC)")

    monkeypatch.setattr(higgsfield, "check_budget", broke)
    obs = await content.check_render_backend()
    assert obs.health == Health.FAILING, obs.detail
    assert "Veo off" in obs.detail and "insufficient credits" in obs.detail
    assert obs.evidence["backend"] == "higgsfield"

    async def fine():
        return Observation("higgsfield_budget", Health.OK,
                           detail="$2.00 of $20.00 booked this month (read 23:41 UTC)")

    monkeypatch.setattr(higgsfield, "check_budget", fine)
    obs = await content.check_render_backend()
    assert obs.health == Health.OK, obs.detail
    assert "Veo off" in obs.detail


async def test_a_402_is_not_waited_out_and_names_the_top_up(memory_backend,
                                                            monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")

    def handler(request):
        return httpx.Response(402, json={"error": {"message": (
            "Your prepayment credits are depleted. Please go to AI Studio at "
            "https://ai.studio/projects to manage your project and billing.")}})

    _transport(monkeypatch, veo, handler)
    with pytest.raises(veo.VeoError) as info:
        await veo.clip("a beat")
    assert info.value.status_code == 402 and info.value.credits_depleted
    assert veo.depleted_since() is not None
    veo.note_accepted()
    assert veo.depleted_since() is None


async def test_render_backend_is_unknown_when_libass_is_not_confirmed(
        memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    _spent(memory_backend, 0)
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")

    async def caps():
        return ffmpeg.Capabilities(True, True, False, True)

    monkeypatch.setattr(ffmpeg, "capabilities", caps)
    obs = await content.check_render_backend()
    assert obs.health == Health.UNKNOWN and "ass filter" in obs.detail



# ================================================================ Veo cap edges (review M6)

async def test_seconds_are_reserved_before_asking_and_refunded_after(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 50}
    order: list[str] = []
    real_record = veo.record

    async def recording(seconds, media_id):
        order.append(f"record {seconds}")
        await real_record(seconds, media_id)

    calls = {"n": 0}

    async def second_beat_refused(prompt):
        calls["n"] += 1
        order.append(f"clip {calls['n']}")
        if calls["n"] == 2:
            raise veo.VeoError("Veo submit failed: HTTP 400", accepted=False)
        return b"CLIP"

    monkeypatch.setattr(veo, "record", recording)
    monkeypatch.setattr(veo, "clip", second_beat_refused)
    result = await render.render_veo(1, reel_script.parse(CLIFF))
    assert result.ok is False
    assert order == ["record 32", "clip 1", "clip 2", "record -24"]


# ------------------------------------------- Veo 429s (Task 56)

async def test_beats_are_submitted_sequentially_never_in_parallel(
        memory_backend, fake_ffmpeg, monkeypatch):
    import asyncio
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 53}
    in_flight = {"n": 0, "max": 0}

    async def clip(prompt):
        in_flight["n"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["n"])
        await asyncio.sleep(0)     # yield -- a parallel caller would overlap here
        in_flight["n"] -= 1
        return b"CLIP"

    monkeypatch.setattr(veo, "clip", clip)
    result = await render.render_veo(1, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert in_flight["max"] == 1


async def test_a_429_retries_the_same_beat_with_backoff_and_succeeds(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 51}
    calls = {"n": 0}
    slept: list[float] = []

    async def flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise veo.VeoError("Veo submit failed: HTTP 429: rate limited",
                               status_code=429)
        return b"CLIP"

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(veo, "clip", flaky)
    monkeypatch.setattr(render.asyncio, "sleep", fake_sleep)
    result = await render.render_veo(1, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert calls["n"] == 5              # beat 1 failed once, retried, then 3 more
    assert slept == [60.0]              # only the first backoff was needed
    usage = [args for sql, args in memory_backend.sql_log
             if "INSERT INTO veo_usage" in sql]
    assert usage == [(32, "veo-3.1-fast-generate-preview", 51)]   # no refund


async def test_four_429s_exhaust_retries_mark_daily_quota_and_refund(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 52}
    slept: list[float] = []

    async def always_429(prompt):
        raise veo.VeoError(
            "Veo submit failed: HTTP 429: Daily quota exceeded for requests",
            status_code=429, daily_quota=True)

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(veo, "clip", always_429)
    monkeypatch.setattr(render.asyncio, "sleep", fake_sleep)
    result = await render.render_veo(1, reel_script.parse(CLIFF))
    assert result.ok is False
    assert "Veo daily quota exhausted — retry tomorrow" in result.detail
    assert "UTC" in result.detail
    assert slept == [60.0, 120.0, 240.0]      # 3 retries, never abandoned early
    usage = [args for sql, args in memory_backend.sql_log
             if "INSERT INTO veo_usage" in sql]
    assert usage == [(32, "veo-3.1-fast-generate-preview", 52),
                     (-32, "veo-3.1-fast-generate-preview", 52)]   # fully refunded


async def test_a_non_429_failure_is_not_retried(memory_backend, fake_ffmpeg,
                                               monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 54}
    calls = {"n": 0}
    slept: list[float] = []

    async def bad_request(prompt):
        calls["n"] += 1
        raise veo.VeoError("Veo submit failed: HTTP 400: malformed prompt")

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(veo, "clip", bad_request)
    monkeypatch.setattr(render.asyncio, "sleep", fake_sleep)
    result = await render.render_veo(1, reel_script.parse(CLIFF))
    assert result.ok is False
    assert calls["n"] == 1               # never retried -- not a 429
    assert slept == []
    assert "quota" not in result.detail.lower()


async def test_two_renders_cannot_both_pass_the_cap(memory_backend, fake_ffmpeg,
                                                   monkeypatch):
    import asyncio
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "40")      # room for one story
    memory_backend.responses["INSERT INTO content_media"] = {"id": 60}
    spent = {"s": 0}

    async def used():
        await asyncio.sleep(0)                           # let the other in
        return spent["s"]

    async def record(seconds, media_id):
        spent["s"] += seconds

    async def clip(prompt):
        return b"CLIP"

    async def words(audio, filename="reel.mp3"):
        return None, "GROQ_API_KEY not set"

    monkeypatch.setattr(veo, "seconds_this_month", used)
    monkeypatch.setattr(veo, "record", record)
    monkeypatch.setattr(veo, "clip", clip)
    monkeypatch.setattr(captions, "word_timings", words)
    results = await asyncio.gather(render.render_veo(1, reel_script.parse(CLIFF)),
                                   render.render_veo(2, reel_script.parse(CLIFF)))
    assert sorted(r.ok for r in results) == [False, True]
    assert spent["s"] == 32
    assert any("refused as a whole" in r.detail for r in results)


async def test_a_clip_on_a_foreign_host_is_never_fetched(memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    monkeypatch.setattr(veo, "POLL_S", 0)
    hosts: list[str] = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.path.endswith(":predictLongRunning"):
            return httpx.Response(200, json={"name": "models/veo/operations/op1"})
        return httpx.Response(200, json={"done": True, "response": {
            "generateVideoResponse": {"generatedSamples": [
                {"video": {"uri": "https://evil.example/clip.mp4"}}]}}})

    _transport(monkeypatch, veo, handler)
    with pytest.raises(veo.VeoError) as info:
        await veo.clip("a scene")
    assert "evil.example" in str(info.value) and info.value.accepted is True
    assert "evil.example" not in hosts


async def test_a_redirected_download_is_not_followed(memory_backend, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-secret-key")
    monkeypatch.setattr(veo, "POLL_S", 0)
    hosts: list[str] = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.path.endswith(":predictLongRunning"):
            return httpx.Response(200, json={"name": "models/veo/operations/op1"})
        if "operations" in request.url.path:
            return httpx.Response(200, json={"done": True, "response": {
                "generateVideoResponse": {"generatedSamples": [{"video": {
                    "uri": "https://generativelanguage.googleapis.com/f/c"}}]}}})
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    _transport(monkeypatch, veo, handler)
    with pytest.raises(veo.VeoError, match="redirect"):
        await veo.clip("a scene")
    assert "evil.example" not in hosts


def test_the_content_package_never_reaches_up_into_the_api_layer():
    """Review M7: the Fish request builder moved to atlas.modules._fish."""
    for path in PKG.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not (node.level >= 3 and mod.startswith("api")), path.name
                assert not mod.startswith("atlas.api"), path.name


# ================================================== stills fallback (quota)

async def test_veo_out_of_quota_waits_for_tomorrow_never_a_slideshow(
        memory_backend, fake_ffmpeg, monkeypatch):
    """Miro, 2026-09-27: no slides. Veo's quota gone means no reel today --
    said so -- never a stills fallback."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["FROM content_drafts"] = {
        "id": 3, "kind": "story_script", "body": CLIFF}
    memory_backend.responses["INSERT INTO content_media"] = {"id": 61}
    monkeypatch.setattr(stills, "narration", _fake_fish)

    async def always_429(prompt):
        raise veo.VeoError("Veo submit failed: HTTP 429: Daily quota exceeded",
                           status_code=429, daily_quota=True)

    async def fake_sleep(seconds):
        pass

    async def no_stills(*a, **k):
        raise AssertionError("no slideshow fallback")

    monkeypatch.setattr(veo, "clip", always_429)
    monkeypatch.setattr(render, "render_local", no_stills)
    monkeypatch.setattr(render.asyncio, "sleep", fake_sleep)
    result = await content.render_video(3)
    assert result.ok is False and "retry tomorrow" in result.detail
    inserted = [args for sql, args in memory_backend.sql_log
                if "INSERT INTO content_media" in sql]
    assert inserted == [(3, "veo")]


async def test_a_veo_failure_that_is_not_quota_does_not_fall_back(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    _spent(memory_backend, 0)
    memory_backend.responses["FROM content_drafts"] = {
        "id": 3, "kind": "story_script", "body": CLIFF}
    memory_backend.responses["INSERT INTO content_media"] = {"id": 62}

    async def bad_request(prompt):
        raise veo.VeoError("Veo submit failed: HTTP 400: malformed prompt")

    async def no_stills(*a, **k):
        raise AssertionError("stills must not run for a non-quota failure")

    monkeypatch.setattr(veo, "clip", bad_request)
    monkeypatch.setattr(render, "render_local", no_stills)
    result = await content.render_video(3)
    assert result.ok is False and "HTTP 400" in result.detail
    assert "fallback" not in result.detail


# ============================================== the chosen voice on Veo

def test_the_beat_prompt_asks_veo_for_silence_when_fish_narrates():
    with_voice = render.beat_prompt("sheet", 1, 4, "The tide turns.", own_voice=False)
    assert "Nobody speaks" in with_voice and "narrator says" not in with_voice
    own = render.beat_prompt("sheet", 1, 4, "The tide turns.")
    assert 'A British narrator says: "The tide turns."' in own


def test_narrate_argv_ducks_veo_and_holds_the_last_frame_for_the_voice():
    argv = ffmpeg.narrate_argv("joined.mp4", "voice.mp3", "out.mp4",
                               video_seconds=24.0, narration_seconds=27.5)
    graph = argv[argv.index("-filter_complex") + 1]
    assert "tpad=stop_mode=clone:stop_duration=3.500" in graph
    assert f"volume={ffmpeg.AMBIENCE_VOLUME}" in graph and "amix=inputs=2" in graph
    assert argv[argv.index("-t") + 1] == "27.500"
    assert "-shortest" not in argv
    short = ffmpeg.narrate_argv("j.mp4", "v.mp3", "o.mp4",
                                video_seconds=24.0, narration_seconds=20.0)
    assert "stop_duration=0.000" in short[short.index("-filter_complex") + 1]
    assert short[short.index("-t") + 1] == "24.000"


async def test_veo_reel_is_narrated_by_the_chosen_fish_voice(
        memory_backend, fake_ffmpeg, monkeypatch):
    """REELS_FISH_VOICE_ID set: Veo is asked for no speech, the Fish voice is
    laid over the joined clips, and the captions come from that voice."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    monkeypatch.setenv("REELS_FISH_VOICE_ID", "brit-voice")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 71}
    prompts: list[str] = []
    timed: list[bytes] = []

    async def fake_clip(prompt):
        prompts.append(prompt)
        return b"MP4"

    async def fake_words(audio, filename="reel.mp3"):
        timed.append(audio)
        return [captions.Word("The", 0.0, 0.3)], None

    monkeypatch.setattr(veo, "clip", fake_clip)
    monkeypatch.setattr(captions, "word_timings", fake_words)
    monkeypatch.setattr(stills, "narration", _fake_fish)
    result = await render.render_veo(7, reel_script.parse(NARRATED))

    assert result.ok is True, result.detail
    assert all("Nobody speaks" in p for p in prompts)
    assert any("narrate" in " ".join(a) or "amix" in " ".join(a) for a in fake_ffmpeg)
    assert timed == [b"MP3"]                    # captions timed from the voice
    assert "narrated by Fish Audio, the British reels voice" in result.detail


async def test_veo_reel_keeps_its_own_narrator_without_a_chosen_voice(
        memory_backend, fake_ffmpeg, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    monkeypatch.delenv("REELS_FISH_VOICE_ID", raising=False)
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 72}
    prompts: list[str] = []

    async def fake_clip(prompt):
        prompts.append(prompt)
        return b"MP4"

    async def fake_words(audio, filename="reel.mp3"):
        return [captions.Word("The", 0.0, 0.3)], None

    async def never(*a, **k):
        raise AssertionError("Fish must not be asked without a chosen voice")

    monkeypatch.setattr(veo, "clip", fake_clip)
    monkeypatch.setattr(captions, "word_timings", fake_words)
    monkeypatch.setattr(stills, "narration", never)
    result = await render.render_veo(7, reel_script.parse(NARRATED))
    assert result.ok is True, result.detail
    assert all("A British narrator says" in p for p in prompts)
    assert not any("amix" in " ".join(a) for a in fake_ffmpeg)
    assert "Veo's own narrator" in result.detail


# ================================================== clearer video (09-27)

def test_veo_is_asked_for_1080p_by_default_and_4k_on_request(monkeypatch):
    monkeypatch.delenv("VEO_RESOLUTION", raising=False)
    assert veo.resolution() == "1080p"
    monkeypatch.setenv("VEO_RESOLUTION", "4K")
    assert veo.resolution() == "4k"
    monkeypatch.setenv("VEO_RESOLUTION", "8k")
    assert veo.resolution() == "1080p"          # never a value Veo refuses


async def test_the_submit_carries_the_resolution(monkeypatch):
    import httpx
    sent: list[dict] = []
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_RESOLUTION", "1080p")

    class C:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.append(json)
            return httpx.Response(400, json={"error": {"message": "stop here"}},
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(veo.httpx, "AsyncClient", C)
    with pytest.raises(veo.VeoError):
        await veo.clip("a lighthouse at dusk")
    assert sent[0]["parameters"]["resolution"] == "1080p"
    assert sent[0]["parameters"]["aspectRatio"] == "9:16"


def test_the_final_encode_is_high_quality():
    tail = ffmpeg.burn_argv("in.mp4", "c.ass", "out.mp4")
    assert "-crf" in tail and tail[tail.index("-crf") + 1] == "18"
    assert tail[tail.index("-preset") + 1] == "medium"


# ================================================== reel polish (Task 67)

def test_the_burn_normalises_loudness_and_punches_in_on_each_beat(monkeypatch):
    monkeypatch.delenv("REELS_EXPORT_4K", raising=False)
    argv = ffmpeg.burn_argv("j.mp4", "c.ass", "o.mp4", beat_seconds=8)
    vf = argv[argv.index("-vf") + 1]
    assert argv[argv.index("-af") + 1] == ffmpeg.LOUDNORM
    assert "I=-14" in ffmpeg.LOUDNORM
    assert "mod(t" in vf and "eval=frame" in vf and "crop=1080:1920" in vf
    assert vf.rstrip().endswith("ass='c.ass'"), "captions drawn last, at output size"
    assert "2160" not in vf


def test_4k_is_an_archive_master_never_the_posted_file(monkeypatch):
    """Instagram's Graph API refuses a Reel wider than 1920 px, so the posted
    file stays 1080x1920 and the 4K is a separate, upscaled master."""
    monkeypatch.delenv("REELS_EXPORT_4K", raising=False)
    assert ffmpeg.export_4k() is True, "on by default (Miro, 2026-09-27)"
    monkeypatch.setenv("REELS_EXPORT_4K", "0")
    assert ffmpeg.export_4k() is False
    delivery = ffmpeg.burn_argv("j.mp4", "c.ass", "o.mp4")
    assert "2160" not in delivery[delivery.index("-vf") + 1]
    master = ffmpeg.burn_argv("j.mp4", "c.ass", "m.mp4", uhd=True)
    vf = master[master.index("-vf") + 1]
    assert "scale=2160:3840:flags=lanczos" in vf and "unsharp" in vf
    assert vf.index("lanczos") < vf.index("ass=")


def test_the_stills_render_is_loudness_normalised_too():
    argv = ffmpeg.stills_argv(["a.png"], [30], "n.mp3", "o.mp4",
                              label_file="l.txt", font=None)
    assert argv[argv.index("-af") + 1] == ffmpeg.LOUDNORM


# ============================================ Egyptian-Arabic reels (09-27)

def test_an_arabic_reel_is_spoken_by_veo_in_egyptian_arabic(monkeypatch):
    monkeypatch.setenv("REELS_FISH_VOICE_ID", "brit-voice")
    script = reel_script.parse(CLIFF)
    assert render.is_arabic(script) is False
    arabic = type("S", (), {"format": "Egyptian Arabic comedy series"})()
    assert render.is_arabic(arabic) is True
    p = render.beat_prompt("sheet", 1, 3, "إزيك يا جدعان", arabic=True)
    assert "in Egyptian Arabic" in p and "Nobody speaks" not in p


async def test_an_interrupted_render_is_marked_failed_and_its_reservation_kept(
        memory_backend):
    """2026-09-27: a deploy killed draft 12's render mid-flight; the row sat
    'rendering' forever. It is reaped after 30 min, and no refund is written:
    what Veo had already billed is unknown, and the cap must not under-count."""
    memory_backend.responses["UPDATE content_media SET status = 'failed'"] = [{"id": 12}]
    assert await render.reap_interrupted() == 1
    sql = " ".join(s for s, _ in memory_backend.sql_log)
    assert "status = 'rendering'" in sql and "30 minutes" in sql
    assert "INSERT INTO veo_usage" not in sql


# ============================================ 60 fps (Miro, 2026-09-27)

def test_the_delivery_is_60_fps_and_veo_is_motion_interpolated_to_it():
    assert ffmpeg.FPS == 60, "the Graph API's ceiling, and Miro's ask"
    argv = ffmpeg.smooth_argv("j.mp4", "s.mp4")
    vf = argv[argv.index("-vf") + 1]
    assert "minterpolate=fps=60" in vf and "mi_mode=mci" in vf
    assert argv[argv.index("-r") + 1] == "60"


async def test_a_failed_smoothing_never_fails_the_reel(monkeypatch):
    async def boom(argv, *, timeout=0):
        assert timeout == ffmpeg.SMOOTH_TIMEOUT_S
        raise ffmpeg.FfmpegError("timed out")
    monkeypatch.setattr(ffmpeg, "run", boom)
    src, note = await ffmpeg.smooth("j.mp4", "s.mp4")
    assert src == "j.mp4" and "NOT applied" in note


async def test_every_render_command_runs_at_the_lowest_cpu_priority(monkeypatch):
    """One CPU core on the server: a 20-40 minute interpolation must never
    starve the API, the checks or a live call (2026-09-27)."""
    seen = {}

    class P:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_exec(*argv, **kw):
        seen.update(kw)
        return P()

    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")
    monkeypatch.setattr(ffmpeg.asyncio, "create_subprocess_exec", fake_exec)
    await ffmpeg.run(["ffmpeg", "-version"])
    assert seen["preexec_fn"] is ffmpeg._lowest_priority
    assert ffmpeg.NICENESS == 19


def test_a_fake_footage_format_says_parody_on_every_frame():
    from atlas.modules.content import captions, reels
    tag = reels.screen_tag("animal-led absurd chase, fake footage (labelled)")
    assert tag == "PARODY - Made with AI"
    assert reels.screen_tag("AI news parody (labelled)") == tag
    assert reels.screen_tag("clever pet outsmarts human") == "Made with AI"
    assert "Tag,,0,0,0,,PARODY - Made with AI" in captions.build_ass(None, 8.0, tag=tag)


def test_the_new_formats_have_templates_and_the_right_rules():
    from atlas.modules.content import reels
    names = [n for n, _, _ in reels.MIRO_IDEAS]
    for name in ("animal-led absurd chase, fake footage (labelled)",
                 "Egyptian Arabic street-dog phone call",
                 "top-5 countdown of talking-animal job interviews"):
        assert name in names and name.lower() in reels.FORMAT_TEMPLATES
    assert "never children" in reels.FORMAT_TEMPLATES[
        "top-5 countdown of talking-animal job interviews"]


async def test_a_dialogue_reel_is_spoken_by_its_characters_with_no_narrator(
        memory_backend, fake_ffmpeg, monkeypatch):
    """Miro, 2026-09-27: characters talk to each other on camera, everything
    moves; no narrator, and no Fish voice laid over them even when one is set."""
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    monkeypatch.setenv("REELS_FISH_VOICE_ID", "brit-voice")
    _spent(memory_backend, 0)
    memory_backend.responses["INSERT INTO content_media"] = {"id": 73}
    prompts: list[str] = []

    async def fake_clip(prompt):
        prompts.append(prompt)
        return b"MP4"

    async def fake_words(audio, filename="reel.mp3"):
        return [captions.Word("Where", 0.0, 0.3)], None

    async def never(*a, **k):
        raise AssertionError("no narrator over a dialogue reel")

    body = reel_script.compose(
        "clever pet outsmarts human",
        ["Mia: Where did the cheese go? | Rex: What cheese?",
         "Mia: You have crumbs on your nose."],
        "He has no idea.", characters="Mia, a girl of 10; Rex, a beagle")
    monkeypatch.setattr(veo, "clip", fake_clip)
    monkeypatch.setattr(captions, "word_timings", fake_words)
    monkeypatch.setattr(stills, "narration", never)
    result = await render.render_veo(8, reel_script.parse(body))
    assert result.ok is True, result.detail
    assert all("to each other" in p and "no narrator" in p
               and render.MOTION in p for p in prompts)
    assert not any("narrator says" in p for p in prompts)


def test_each_line_is_said_by_its_named_speaker_and_captioned_in_their_colour():
    """Relayed 2026-09-27: talking-car skit style -- 'X says: "..."' per line,
    Veo speaks it on the speaker's face; captions colour-coded per speaker."""
    beat = "Silver Sedan: You call that a spoiler? | Red Coupe: Fridge on wheels."
    p = render.beat_prompt("sheet", 1, 2, beat, dialogue=True)
    assert 'Silver Sedan says: "You call that a spoiler?"' in p
    assert 'Red Coupe says: "Fridge on wheels."' in p and "no narrator" in p
    body = reel_script.compose("rival cars family drama",
                               [beat, "Silver Sedan: Race me. Red Coupe: Fine!"],
                               "Who wins?", "a silver sedan; a red coupe")
    ex = reel_script.parse(body).exchanges()
    assert ex[1] == [("Silver Sedan", "Race me."), ("Red Coupe", "Fine!")]
    cues = captions.dialogue_cues(ex, 8.0)
    sedan = {c.color for c in cues if "spoiler" in c.text or "Race" in c.text}
    coupe = {c.color for c in cues if "Fridge" in c.text or "Fine" in c.text}
    assert len(sedan) == 1 and len(coupe) == 1 and sedan != coupe
    ass = captions.build_scripted_ass(cues, 16.0, arabic=False)
    assert "{\\c&H0000FFFF&}" in ass and "{\\c&H00FFFF00&}" in ass
    assert all(8.0 <= c.start for c in cues if "Race" in c.text)


# ========================================= Miro's reference look (09-27)

@pytest.mark.parametrize("prompt", [
    "Pixar-style 3D cars arguing", "in the style of Disney", "a DreamWorks look",
    "like the Cars movie", "Lightning McQueen races", "Mater laughs",
    "an old rusty tow truck with a hook grins",
])
def test_a_studio_look_or_a_known_character_is_refused(prompt):
    from atlas.modules.content import guard
    assert guard.ip_refusal(prompt) is not None


@pytest.mark.parametrize("prompt", [
    "stylized 3D animation of two cars arguing", "the material is wood",
    "a teal tow truck with a crane arm and round goggles",
])
def test_original_designs_pass_the_ip_guard(prompt):
    from atlas.modules.content import guard
    assert guard.ip_refusal(prompt) is None


def test_real_car_brands_are_caught_by_the_brand_guard():
    from atlas.modules.content import guard
    for name in ("Honda", "Maybach", "BMW", "Lamborghini"):
        assert guard.caught(f"a {name} sedan argues") == [name]


async def test_veo_is_never_asked_for_a_studio_look(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")

    def no_network(*a, **k):
        raise AssertionError("a refused prompt must never reach Veo")

    monkeypatch.setattr(veo.httpx, "AsyncClient", no_network)
    with pytest.raises(veo.VeoError) as caught:
        await veo.clip("Pixar-style talking cars")
    assert "refused before Veo" in str(caught.value)
    assert caught.value.accepted is False


def test_every_prompt_carries_the_reference_look():
    p = render.beat_prompt("sheet", 1, 2, "Mia: Hi. | Rex: Woof.", dialogue=True)
    assert render.STYLE in p and "mouth that forms every word" in render.STYLE
    assert "No subtitles or text" in render.STYLE
    assert "pixar" not in p.lower()


def test_a_camelcase_hashtag_is_a_tag_not_a_brand():
    """Seen live 2026-10-05: every business lesson's own tags
    (#SmallBusinessIdeas, #ServiceBusiness) tripped the inner-capital rule,
    so none of them could post itself. A tag's spelling is not a brand; a
    listed brand inside a tag, or the same word outside one, still is."""
    from atlas.modules.content import guard as g
    cap = ("Your AI tool isn't the business.\nEducational idea, not an income "
           "guarantee.\n#SmallBusinessIdeas #AIWorkflows #ServiceBusiness")
    assert g.refusal(cap) is None
    assert g.caught("we tested it on a PlayStation") == ["PlayStation"]
    assert g.caught("#Nike drop today"), "a listed brand in a tag is still a brand"
    assert g.caught("new SmallBusinessIdeas app") == ["SmallBusinessIdeas"]
