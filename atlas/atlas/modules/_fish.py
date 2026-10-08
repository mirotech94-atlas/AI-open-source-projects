"""The Fish Audio text-to-speech request, as data: shared by `/speak`
(`atlas.api.voice`) and the free reel renderer (`content.stills`), so
neither imports the other and a module never reaches up into the API layer.
Builds the request only; each caller sends it and handles the answer.
"""
from __future__ import annotations

import os
from typing import Any

FISH_TTS_URL = "https://api.fish.audio/v1/tts"


def fish_request(
    text: str, voice_id: str | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """The Fish Audio call, as data: url, headers, JSON body.

    `reference_id` is the chosen voice: `voice_id` when a caller names one
    (the Agency room's female and male voices, read from env by the caller
    -- never from a browser), otherwise FISH_AUDIO_VOICE_ID, ATLAS's own.
    With neither set it is left out rather than sent as null, and Fish Audio
    uses its default voice -- a request that names no voice is honest; one
    that names `None` is a guess at how their API treats it.
    """
    headers = {
        "Authorization": f"Bearer {os.environ['FISH_AUDIO_API_KEY']}",
        "Content-Type": "application/json",
        "model": os.getenv("FISH_AUDIO_MODEL", "").strip() or "s1",
    }
    body: dict[str, Any] = {"text": text}
    voice_id = (voice_id or "").strip() or os.getenv("FISH_AUDIO_VOICE_ID", "").strip()
    if voice_id:
        body["reference_id"] = voice_id
    body["format"] = "mp3"
    return FISH_TTS_URL, headers, body
