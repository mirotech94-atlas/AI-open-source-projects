"""ElevenLabs speech-to-text and text-to-speech, and the only place either runs.

Why a module rather than a call in the router: the app is served under
`default-src 'self'` with no third-party origin (`deploy/nginx-api.conf.tmpl`),
so a browser cannot reach api.elevenlabs.io at all and every byte in both
directions goes through the hub. That began as a constraint and is the right
answer anyway -- the key never leaves the server, and a customer on a segment
whose only egress is api.guarder.io still has a working dashboard.

Failure discipline is `chat/providers.py`'s, for its reason. A vendor having a
bad afternoon must not become a 500 on a dashboard somebody opened *because*
their network is down. Nothing here raises: `transcribe` returns "" and
`speak` returns b"" on anything that goes wrong, and the caller decides what
to say about it.

The key is never logged. `_why` exists to make that structural rather than
careful -- the vendor's error bodies are plain JSON about accounts and quotas
and never echo the key we sent, so status and body are safe to log; the
request headers would not be, and nothing here logs them.
"""
from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
TIMEOUT_S = 30.0

#: The upload types the hub will forward, pinned rather than passed through.
#: Safari and Chrome hand `MediaRecorder` output back under different types so
#: the set has to be explicit; what it must not be is a client-supplied header
#: forwarded unread, which would make the vendor our input validator.
#:
#: Exactly the three `MediaRecorder` produces, and exactly the three
#: `web/src/lib/speech.ts`'s `PREFERRED` asks for -- no more. The set briefly
#: also held `audio/mpeg` and `audio/wav`, which no browser records and no
#: client here sends: widening past what the app can emit only buys an
#: authenticated viewer with curl the ability to post a 10 MB MP3 and have
#: the vendor bill it. The set is the client's contract, not a guess at what
#: the vendor might cope with.
ALLOWED_AUDIO = {"audio/webm", "audio/ogg", "audio/mp4"}


def ready() -> bool:
    """True when voice can run at all -- both halves, not one.

    Mirrors `chat/providers`' `_groq_available`: the credential's presence is the
    switch. The voice id is part of it because a key without one transcribes
    and cannot speak, and a UI that shows a speaker which always 502s is worse
    than one that shows no speaker.
    """
    return bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id)


def _headers() -> dict[str, str]:
    return {"xi-api-key": settings.elevenlabs_api_key}


def _why(exc: Exception) -> str:
    """A safe-to-log one-liner for a vendor failure.

    Same rule as `chat/providers._describe_failure`: the response body is the
    vendor's own account/quota JSON and never contains the key we sent, so it
    is safe; the request headers are not, and are never passed here.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
    return f"{type(exc).__name__}: {exc}"


def transcribe(data: bytes, content_type: str) -> str:
    """Audio in, one line of text out. "" when voice is off or anything fails."""
    if not ready() or not data:
        return ""
    try:
        r = httpx.post(STT_URL, headers=_headers(), timeout=TIMEOUT_S,
                       data={"model_id": settings.elevenlabs_stt_model},
                       files={"file": ("clip", data, content_type)})
        r.raise_for_status()
        text = r.json().get("text")
        return text.strip() if isinstance(text, str) else ""
    except Exception as exc:
        log.warning("speech-to-text failed: %s", _why(exc))
        return ""


def speak(text: str) -> bytes:
    """Text in, MP3 bytes out. b"" when voice is off or anything fails."""
    if not ready() or not text.strip():
        return b""
    try:
        r = httpx.post(TTS_URL.format(voice_id=settings.elevenlabs_voice_id),
                       headers=_headers(), timeout=TIMEOUT_S,
                       json={"text": text,
                             "model_id": settings.elevenlabs_tts_model,
                             "output_format": "mp3_44100_128"})
        r.raise_for_status()
        return r.content
    except Exception as exc:
        log.warning("text-to-speech failed: %s", _why(exc))
        return b""
