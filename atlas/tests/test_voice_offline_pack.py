"""The offline pack: ATLAS's offline lines in its own Fish voice, or a 503.

Miro wants ATLAS to speak only in his chosen Fish Audio voice, including
offline. ATLAS Menu and Air Glass keep this pack and play it when ATLAS is
out of reach. The pack is never made in another voice: with no voice id or
no way to render a missing line, the answer is 503 with the reason.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from atlas.api import auth, voice, voice_cache, voice_offline

SECRET = "v" * 40
PASSWORD = "a-long-enough-password"


@pytest.fixture
def client(monkeypatch, memory_backend, tmp_path):
    monkeypatch.setenv("ATLAS_VOICE_CACHE_DIR", str(tmp_path / "voice-cache"))
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    monkeypatch.setenv("FISH_AUDIO_API_KEY", "fish-test")
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "atlas-british-voice")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        c.post("/api/login", json={"password": PASSWORD})
        yield c


def _fish(monkeypatch, fail: bool = False) -> list[tuple[str, str | None]]:
    calls: list[tuple[str, str | None]] = []

    async def tts(text: str, voice_id: str | None = None) -> bytes:
        calls.append((text, voice_id))
        if fail:
            raise RuntimeError("fish down")
        return b"ID3" + text.encode()
    monkeypatch.setattr(voice, "_fish_tts", tts)
    return calls


def test_the_pack_is_every_offline_line_with_its_audio_hash(client, monkeypatch):
    calls = _fish(monkeypatch)
    r = client.get("/api/voice/offline-pack")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"voice", "built_at", "phrases"}
    assert "atlas-british-voice" not in r.text           # fingerprint, not the id
    assert set(body["phrases"]) == set(voice_offline.PACK)
    for name, entry in body["phrases"].items():
        assert set(entry) == {"text", "url", "sha256", "bytes"}
        assert entry["text"] == voice_cache.fixed_lines()[name]
        assert entry["url"] == f"/api/voice/clip/{name}?cached_only=true"
        clip = client.get(entry["url"])
        assert clip.status_code == 200
        assert clip.content == b"ID3" + entry["text"].encode()
        assert entry["sha256"] == hashlib.sha256(clip.content).hexdigest()
        assert entry["bytes"] == len(clip.content)
    # ATLAS's own voice: the one-argument call, never a named other voice.
    assert sorted(t for t, _ in calls) == sorted(voice_offline.phrases().values())
    assert all(v is None for _, v in calls)


def test_the_lines_are_rendered_once_then_served_from_the_cache(client, monkeypatch):
    calls = _fish(monkeypatch)
    first = client.get("/api/voice/offline-pack").json()
    again = client.get("/api/voice/offline-pack").json()
    assert len(calls) == len(voice_offline.PACK)          # one Fish call per line
    assert first["phrases"] == again["phrases"]


def test_lines_warmed_earlier_are_not_rendered_again(client, monkeypatch):
    calls = _fish(monkeypatch)
    voice_cache.store("Listening.", b"ID3kept")
    body = client.get("/api/voice/offline-pack").json()
    assert ("Listening.", None) not in calls
    assert body["phrases"]["listening"]["sha256"] == hashlib.sha256(b"ID3kept").hexdigest()


def test_no_voice_id_is_503_never_fish_default_voice(client, monkeypatch):
    calls = _fish(monkeypatch)
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "")
    r = client.get("/api/voice/offline-pack")
    assert r.status_code == 503
    assert "FISH_AUDIO_VOICE_ID" in r.json()["detail"]
    assert calls == []


def test_no_fish_key_and_nothing_kept_is_503(client, monkeypatch):
    calls = _fish(monkeypatch)
    monkeypatch.delenv("FISH_AUDIO_API_KEY")
    r = client.get("/api/voice/offline-pack")
    assert r.status_code == 503
    assert "FISH_AUDIO_API_KEY" in r.json()["detail"]
    assert "fish-test" not in r.text
    assert calls == []


def test_fish_down_is_503_with_the_reason_and_no_partial_pack(client, monkeypatch):
    _fish(monkeypatch, fail=True)
    r = client.get("/api/voice/offline-pack")
    assert r.status_code == 503
    assert "could not render" in r.json()["detail"]
    assert "phrases" not in r.json()


def test_a_failure_part_way_keeps_what_was_rendered(client, monkeypatch):
    calls: list[str] = []

    async def tts(text: str, voice_id: str | None = None) -> bytes:
        calls.append(text)
        if len(calls) > 2:
            raise RuntimeError("fish down")
        return b"ID3" + text.encode()
    monkeypatch.setattr(voice, "_fish_tts", tts)
    assert client.get("/api/voice/offline-pack").status_code == 503
    kept = [t for t in voice_offline.phrases().values() if voice_cache.cached(t)]
    assert len(kept) == 2
    _fish(monkeypatch)
    assert client.get("/api/voice/offline-pack").status_code == 200


def test_everything_kept_serves_even_with_fish_down(client, monkeypatch):
    _fish(monkeypatch)
    assert client.get("/api/voice/offline-pack").status_code == 200
    calls = _fish(monkeypatch, fail=True)
    monkeypatch.delenv("FISH_AUDIO_API_KEY")
    r = client.get("/api/voice/offline-pack")
    assert r.status_code == 200                       # kept Fish clips, same voice
    assert calls == []


def test_a_new_voice_needs_a_new_pack(client, monkeypatch):
    _fish(monkeypatch)
    before = client.get("/api/voice/offline-pack").json()
    monkeypatch.setenv("FISH_AUDIO_VOICE_ID", "another-voice")
    calls = _fish(monkeypatch)
    after = client.get("/api/voice/offline-pack").json()
    assert after["voice"] != before["voice"]
    assert len(calls) == len(voice_offline.PACK)          # rendered again, new voice


def test_the_pack_needs_a_session(monkeypatch, memory_backend, tmp_path):
    monkeypatch.setenv("ATLAS_VOICE_CACHE_DIR", str(tmp_path / "voice-cache"))
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        assert c.get("/api/voice/offline-pack").status_code == 401


def test_every_pack_line_is_a_kept_fixed_line():
    lines = voice_cache.fixed_lines()
    assert set(voice_offline.PACK) <= set(lines)
    for name in voice_offline.PACK:
        assert voice_cache.is_fixed(lines[name])
