"""The voice vendor module: what it sends, and what it does when it cannot.

The failure tests are the point of this file. `chat/providers.py` swallows a
provider failure because a vendor having a bad afternoon must not become a
500 on a dashboard somebody opened *because* their network is down, and voice
inherits that rule exactly -- so every one of these asserts a quiet empty
result rather than an exception.
"""
import logging

import httpx
import pytest

from guardian_hub import voice
from guardian_hub.config import settings


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk-test-key", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "voice-xyz", raising=False)
    return settings


def test_not_ready_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "voice-xyz", raising=False)
    assert voice.ready() is False


def test_not_ready_without_a_voice(monkeypatch):
    # A key with no voice id cannot speak, and a half-configured hub that
    # advertises a speaker and then 502s is worse than one that shows none.
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk-test-key", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "", raising=False)
    assert voice.ready() is False


def test_ready_with_both(keyed):
    assert voice.ready() is True


def test_transcribe_does_not_call_out_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "", raising=False)
    monkeypatch.setattr(voice.httpx, "post", lambda *a, **k: pytest.fail("called the vendor"))
    assert voice.transcribe(b"xx", "audio/webm") == ""


def test_transcribe_sends_the_model_and_the_file_and_returns_the_text(keyed, monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return httpx.Response(200, json={"text": "  is anything wrong  "},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    assert voice.transcribe(b"clipbytes", "audio/webm") == "is anything wrong"
    assert seen["url"] == voice.STT_URL
    assert seen["headers"]["xi-api-key"] == "sk-test-key"
    assert seen["data"]["model_id"] == settings.elevenlabs_stt_model
    assert seen["files"]["file"][1] == b"clipbytes"
    assert seen["files"]["file"][2] == "audio/webm"


def test_speak_posts_to_the_configured_voice_and_returns_bytes(keyed, monkeypatch):
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen.update(kw)
        return httpx.Response(200, content=b"ID3mp3bytes", request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    assert voice.speak("Everything is fine.") == b"ID3mp3bytes"
    assert seen["url"] == voice.TTS_URL.format(voice_id="voice-xyz")
    assert seen["json"]["text"] == "Everything is fine."
    assert seen["json"]["model_id"] == settings.elevenlabs_tts_model
    assert seen["json"]["output_format"] == "mp3_44100_128"


def test_speak_refuses_empty_text_without_calling_out(keyed, monkeypatch):
    monkeypatch.setattr(voice.httpx, "post", lambda *a, **k: pytest.fail("called the vendor"))
    assert voice.speak("   ") == b""


@pytest.mark.parametrize("boom", [
    lambda *a, **k: httpx.Response(500, text="upstream exploded",
                                   request=httpx.Request("POST", "https://x")),
    lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out")),
    lambda *a, **k: httpx.Response(200, content=b"not json",
                                   request=httpx.Request("POST", "https://x")),
])
def test_a_bad_afternoon_leaves_transcribe_empty_never_raising(keyed, monkeypatch, boom):
    monkeypatch.setattr(voice.httpx, "post", boom)
    assert voice.transcribe(b"x", "audio/webm") == ""


@pytest.mark.parametrize("boom", [
    lambda *a, **k: httpx.Response(500, text="upstream exploded",
                                   request=httpx.Request("POST", "https://x")),
    lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("timed out")),
])
def test_a_bad_afternoon_leaves_speak_empty_never_raising(keyed, monkeypatch, boom):
    # No "200 with a junk body" case here, deliberately: `speak` returns the
    # response bytes raw and has no parse step to fail on, so a 200 IS the
    # audio as far as this layer can tell. We cannot validate MP3 bytes and
    # should not pretend to.
    monkeypatch.setattr(voice.httpx, "post", boom)
    assert voice.speak("hello") == b""


def test_the_key_is_never_logged(keyed, monkeypatch, caplog):
    # The vendor's own error bodies are account/quota JSON and never echo the
    # key we sent, so logging status and body is safe -- logging the request
    # headers would not be, and this is the test that says so.
    def fake_post(url, **kw):
        return httpx.Response(401, text='{"detail":"invalid_api_key"}',
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(voice.httpx, "post", fake_post)
    with caplog.at_level(logging.DEBUG):
        voice.transcribe(b"x", "audio/webm")
        voice.speak("hello")
    assert "sk-test-key" not in caplog.text
    assert "invalid_api_key" in caplog.text


def test_the_allowed_type_set_is_explicit_and_excludes_a_wildcard():
    """Exactly the three `MediaRecorder` emits -- an equality, not a floor.

    The three `in` checks below are the floor: every type the browser can
    actually record has to be accepted or Safari or Chrome loses dictation
    outright. The equality is the ceiling, and it is the half with teeth.
    Each extra type is a shape of upload an authenticated viewer can post
    through a path no client of ours uses and have the vendor bill --
    `audio/mpeg` was in this set once, and nothing records MP3."""
    assert "audio/webm" in voice.ALLOWED_AUDIO
    assert "audio/ogg" in voice.ALLOWED_AUDIO
    assert "audio/mp4" in voice.ALLOWED_AUDIO
    assert not any("*" in t for t in voice.ALLOWED_AUDIO)
    assert voice.ALLOWED_AUDIO == {"audio/webm", "audio/ogg", "audio/mp4"}
