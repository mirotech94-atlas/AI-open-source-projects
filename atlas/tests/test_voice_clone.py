"""ATLAS's own voice is the local clone of the NotebookLM host; Fish Audio is
the fallback.

Miro, 2026-10-29: ATLAS's spoken voice, in the portal and everywhere local
voice is used, is NotebookLM's voice. NotebookLM has no TTS API, so a
zero-shot clone TTS (Chatterbox) on the Spark speaks it, from one reference
clip of the NotebookLM host. When the clone is down, the old Fish path speaks
unchanged, and the voice status reads UNKNOWN -- never OK.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice, voice_cache, voice_clone, voice_offline
from atlas.kernel.contracts import Health

SECRET = "v" * 40
PASSWORD = "a-long-enough-password"


@pytest.fixture
def clone_env(monkeypatch):
    monkeypatch.setenv("ATLAS_VOICE_CLONE_URL", "http://host.docker.internal:18010")
    monkeypatch.setenv("ATLAS_VOICE_CLONE_KEY", "clone-test-key-123")
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "fish-test")
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "atlas-voice")
    voice_clone._health_cache.clear()


@pytest.fixture
def client(monkeypatch, memory_backend, clone_env):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


def _voices(monkeypatch, clone: bytes | None, fish: bytes | None = b"ID3fish"):
    calls: dict[str, list[str]] = {"clone": [], "fish": []}

    async def clone_render(text: str) -> bytes:
        calls["clone"].append(text)
        if clone is None:
            raise RuntimeError("clone down")
        return clone

    async def fish_tts(text: str, voice_id: str | None = None) -> bytes:
        calls["fish"].append(text)
        if fish is None:
            raise RuntimeError("fish down")
        return fish
    monkeypatch.setattr(voice_clone, "render", clone_render)
    monkeypatch.setattr(voice, "_fish_tts", fish_tts)
    return calls


# ------------------------------------------------------------ configuration

def test_unset_is_not_configured(monkeypatch):
    monkeypatch.delenv("ATLAS_VOICE_CLONE_URL", raising=False)
    monkeypatch.delenv("ATLAS_VOICE_CLONE_KEY", raising=False)
    assert not voice_clone.configured()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:18010", "http://localhost:18010",
    "http://host.docker.internal:18010", "http://192.168.80.169:8010",
])
def test_local_hosts_are_allowed(monkeypatch, url):
    monkeypatch.setenv("ATLAS_VOICE_CLONE_URL", url)
    monkeypatch.setenv("ATLAS_VOICE_CLONE_KEY", "k" * 20)
    assert voice_clone.configured()


@pytest.mark.parametrize("url", [
    "https://api.example.com", "http://8.8.8.8:8010", "ftp://127.0.0.1",
    "http://evil.com@127.0.0.1.nip.io",
])
def test_a_cloud_or_odd_url_is_refused(monkeypatch, url):
    """Never a cloud TTS: the clone is local or it is not used at all."""
    monkeypatch.setenv("ATLAS_VOICE_CLONE_URL", url)
    monkeypatch.setenv("ATLAS_VOICE_CLONE_KEY", "k" * 20)
    assert not voice_clone.configured()


def test_a_url_without_a_key_is_not_configured(monkeypatch):
    monkeypatch.setenv("ATLAS_VOICE_CLONE_URL", "http://127.0.0.1:18010")
    monkeypatch.delenv("ATLAS_VOICE_CLONE_KEY", raising=False)
    assert not voice_clone.configured()


# ------------------------------------------------------------------ /speak

def test_atlas_speaks_in_the_clone(client, monkeypatch):
    calls = _voices(monkeypatch, b"ID3clone")
    r = client.post("/api/voice/speak", json={"text": "CloudHost is fine."})
    assert r.status_code == 200 and r.content == b"ID3clone"
    assert r.headers["X-Voice-Source"] == "clone"
    assert r.headers["content-type"] == "audio/mpeg"
    assert calls == {"clone": ["CloudHost is fine."], "fish": []}


def test_clone_down_falls_back_to_fish(client, monkeypatch):
    calls = _voices(monkeypatch, None)
    r = client.post("/api/voice/speak", json={"text": "CloudHost is fine."})
    assert r.status_code == 200 and r.content == b"ID3fish"
    assert r.headers["X-Voice-Source"] == "fish"
    assert calls["clone"] == ["CloudHost is fine."]


def test_both_down_is_a_502_with_text_only(client, monkeypatch):
    _voices(monkeypatch, None, None)
    assert client.post("/api/voice/speak",
                       json={"text": "Something new."}).status_code == 502


def test_clone_speaks_with_no_fish_key(client, monkeypatch):
    monkeypatch.delenv("FISH_AUDIO_API_KEY")
    _voices(monkeypatch, b"ID3clone")
    r = client.post("/api/voice/speak", json={"text": "Hello."})
    assert r.status_code == 200 and r.headers["X-Voice-Source"] == "clone"


def test_a_persona_voice_stays_on_fish(client, monkeypatch):
    """The Agency room's male persona is a Fish voice of its own: unchanged."""
    monkeypatch.setenv("FISH_VOICE_MALE", "male-ref")
    calls = _voices(monkeypatch, b"ID3clone")
    r = client.post("/api/voice/speak", json={"text": "Hi.", "voice": "male"})
    assert r.status_code == 200 and r.headers["X-Voice-Source"] == "fish"
    assert calls["clone"] == []


def test_fixed_line_kept_in_the_clone_voice_not_the_fish_one(client, monkeypatch):
    """A Fish clip kept earlier must not answer for the clone voice."""
    monkeypatch.delenv("ATLAS_VOICE_CLONE_URL")
    voice_cache.store("Listening.", b"ID3oldfish")          # kept in Fish
    monkeypatch.setenv("ATLAS_VOICE_CLONE_URL", "http://127.0.0.1:18010")
    calls = _voices(monkeypatch, b"ID3clonelistening")
    first = client.post("/api/voice/speak", json={"text": "Listening."})
    again = client.post("/api/voice/speak", json={"text": "Listening."})
    assert first.content == again.content == b"ID3clonelistening"
    assert first.headers["X-Voice-Source"] == "clone"
    assert again.headers["X-Voice-Source"] == "cache"
    assert calls["clone"] == ["Listening."]


def test_clone_down_plays_the_kept_fish_line(client, monkeypatch):
    monkeypatch.delenv("ATLAS_VOICE_CLONE_URL")
    voice_cache.store("Listening.", b"ID3oldfish")
    monkeypatch.setenv("ATLAS_VOICE_CLONE_URL", "http://127.0.0.1:18010")
    _voices(monkeypatch, None, None)
    r = client.post("/api/voice/speak", json={"text": "Listening."})
    assert r.status_code == 200 and r.content == b"ID3oldfish"
    assert r.headers["X-Voice-Source"] == "fallback"


def test_clip_renders_in_the_clone(client, monkeypatch):
    calls = _voices(monkeypatch, b"ID3cloneclip")
    r = client.get("/api/voice/clip/no_voice")
    assert r.status_code == 200 and r.content == b"ID3cloneclip"
    assert calls["fish"] == []
    kept = client.get("/api/voice/clip/no_voice?cached_only=true")
    assert kept.content == b"ID3cloneclip"


def test_an_answer_in_the_clone_is_never_kept(client, monkeypatch):
    _voices(monkeypatch, b"ID3answer")
    said = "Two approvals are waiting."
    assert client.post("/api/voice/speak", json={"text": said}).status_code == 200
    assert voice_cache.cached(said) is None


def test_offline_pack_is_in_the_clone_voice(client, monkeypatch):
    calls = _voices(monkeypatch, b"ID3packclone")
    r = client.get("/api/voice/offline-pack")
    assert r.status_code == 200
    assert len(calls["clone"]) == len(voice_offline.PACK) and calls["fish"] == []
    clone_print = r.json()["voice"]
    monkeypatch.delenv("ATLAS_VOICE_CLONE_URL")
    assert voice_offline.voice_fingerprint() != clone_print


# ------------------------------------------------------------------ status

def test_status_unknown_when_unreachable(client, monkeypatch):
    async def down() -> bool:
        raise RuntimeError("connection refused")
    monkeypatch.setattr(voice_clone, "_probe", down)
    caps = client.get("/api/voice/capabilities").json()
    assert caps["voice_source"]["health"] == Health.UNKNOWN.value
    assert caps["voice_source"]["speaking"] == "fish"
    assert caps["speech"] is True                             # Fish still speaks
    assert caps["voice_source"]["as_of"]


def test_status_ok_only_when_the_server_answers(client, monkeypatch):
    async def up() -> bool:
        return True
    monkeypatch.setattr(voice_clone, "_probe", up)
    caps = client.get("/api/voice/capabilities").json()
    assert caps["voice_source"]["health"] == Health.OK.value
    assert caps["voice_source"]["speaking"] == "clone"
    assert "AI" in caps["voice_source"]["disclosure"]


def test_status_unknown_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("ATLAS_VOICE_CLONE_URL")
    caps = client.get("/api/voice/capabilities").json()
    assert caps["voice_source"]["health"] == Health.UNKNOWN.value


def test_speech_true_with_only_the_clone(client, monkeypatch):
    monkeypatch.delenv("FISH_AUDIO_API_KEY")

    async def up() -> bool:
        return True
    monkeypatch.setattr(voice_clone, "_probe", up)
    assert client.get("/api/voice/capabilities").json()["speech"] is True


# ------------------------------------------------------------------ render

async def test_render_posts_text_with_the_bearer_key_and_transcodes(monkeypatch, clone_env):
    import httpx
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})

    async def to_mp3(wav: bytes) -> bytes:
        assert wav == b"RIFFwav"
        return b"ID3mp3"
    monkeypatch.setattr(voice_clone, "_transport", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(voice_clone, "_to_mp3", to_mp3)
    assert await voice_clone.render("Hello there.") == b"ID3mp3"
    assert seen["url"] == "http://host.docker.internal:18010/tts"
    assert seen["auth"] == "Bearer clone-test-key-123"
    assert b"Hello there." in seen["body"]


async def test_render_refuses_when_not_configured(monkeypatch):
    monkeypatch.delenv("ATLAS_VOICE_CLONE_URL", raising=False)
    with pytest.raises(RuntimeError):
        await voice_clone.render("Hi.")


async def test_to_mp3_really_transcodes():
    """The container has ffmpeg (Dockerfile); a real wav becomes an mp3."""
    import io
    import shutil
    import wave
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not on this test host")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000)
    mp3 = await voice_clone._to_mp3(buf.getvalue())
    assert mp3[:3] == b"ID3" or mp3[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
