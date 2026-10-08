"""ATLAS speaks in Fish Audio and nothing else.

Miro, 2026-10-06: "make sure the voice is Fish Audio, not the sound of Siri
or anything else; make sure she responds with voice as well." No reply path
reaches a system voice -- the browser's speechSynthesis on the dashboard,
AVSpeechSynthesizer in ATLAS Menu -- and the lines ATLAS says again and
again are Fish clips rendered once and kept, so they still play when Fish
itself is down. Only answers are never written to disk.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice, voice_cache

ROOT = Path(__file__).resolve().parents[1]
SECRET = "v" * 40
PASSWORD = "a-long-enough-password"

SYSTEM_VOICE = re.compile(
    r"speechSynthesis|SpeechSynthesisUtterance|AVSpeechSynthesizer|AVSpeechUtterance"
    r"|NSSpeechSynthesizer|\"/usr/bin/say\"")


@pytest.mark.parametrize("path", sorted(
    [p for d in ("ui/src/voice", "ui/src/airglass") for p in (ROOT / d).glob("*.ts*")
     if ".test." not in p.name]
    + list((ROOT / "apps/airglass").glob("*.js"))
    + list((ROOT / "bootstrap/mac/menubar").glob("*.swift"))),
    ids=lambda p: str(p.relative_to(ROOT)))
def test_no_reply_path_uses_a_system_voice(path):
    """The dashboard's voice loop, both Air Glass pages and ATLAS Menu: Fish
    Audio only (the standalone Air Glass shows captions instead)."""
    code = "\n".join(line for line in path.read_text().splitlines()
                     if not line.strip().startswith(("//", "*", "/*")))
    assert not SYSTEM_VOICE.search(code), f"{path.name} reaches a system voice"


@pytest.fixture
def client(monkeypatch, memory_backend):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "fish-test")
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "atlas-voice")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


def _fish(monkeypatch, audio: bytes | None):
    calls: list[str] = []

    async def tts(text: str, voice_id: str | None = None) -> bytes:
        calls.append(text)
        if audio is None:
            raise RuntimeError("fish down")
        return audio
    monkeypatch.setattr(voice, "_fish_tts", tts)
    return calls


def test_a_fixed_line_is_rendered_once_then_played_from_the_cache(client, monkeypatch):
    calls = _fish(monkeypatch, b"ID3listening")
    first = client.post("/api/voice/speak", json={"text": "Listening."})
    again = client.post("/api/voice/speak", json={"text": "Listening."})
    assert first.content == again.content == b"ID3listening"
    assert first.headers["X-Voice-Source"] == "fish"
    assert again.headers["X-Voice-Source"] == "cache"
    assert calls == ["Listening."]                      # one Fish call, ever


def test_an_answer_is_never_kept_on_disk(client, monkeypatch):
    _fish(monkeypatch, b"ID3answer")
    said = "VPSie's fine, as of 7:52."
    assert client.post("/api/voice/speak", json={"text": said}).status_code == 200
    assert voice_cache.cached(said) is None
    assert not voice_cache.path_for(said).exists()


def test_when_fish_is_down_the_kept_fish_clip_plays_never_another_voice(
        client, monkeypatch):
    _fish(monkeypatch, b"ID3later")
    generic = voice_cache.fixed_lines()["no_model"]
    assert client.get("/api/voice/clip/no_model").status_code == 200
    _fish(monkeypatch, None)                            # Fish goes down
    timed = voice.NO_MODEL_SPENT.format(when="the first one comes back at 14:05")
    reply = client.post("/api/voice/speak", json={"text": timed})
    assert reply.status_code == 200 and reply.content == b"ID3later"
    assert reply.headers["X-Voice-Source"] == "fallback"
    assert voice_cache.cached(generic) == b"ID3later"
    # An answer with nothing kept is text only: a 502, no other voice.
    assert client.post("/api/voice/speak",
                       json={"text": "Something new."}).status_code == 502


def test_cached_only_never_calls_fish(client, monkeypatch):
    calls = _fish(monkeypatch, b"ID3novoice")
    assert client.get("/api/voice/clip/no_voice?cached_only=true").status_code == 404
    assert calls == []
    assert client.get("/api/voice/clip/no_voice").status_code == 200
    kept = client.get("/api/voice/clip/no_voice?cached_only=true")
    assert kept.status_code == 200 and kept.content == b"ID3novoice"
    assert client.get("/api/voice/clip/anything_else").status_code == 404


async def test_warming_renders_each_fixed_line_once(monkeypatch, tmp_path):
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "fish-test")
    monkeypatch.setattr(voice, "_WARMED", [])
    calls = _fish(monkeypatch, b"ID3x")
    made = await voice.warm_clips()
    assert made == len(voice_cache.fixed_lines()) == len(calls)
    assert await voice.warm_clips() == 0                # once per process


def test_a_new_voice_is_a_new_clip(monkeypatch):
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "one")
    first = voice_cache.path_for("Listening.")
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "two")
    assert voice_cache.path_for("Listening.") != first
