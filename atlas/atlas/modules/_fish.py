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
    text: str, voice_id: str | None = None, speed: float | None = None,
    model: str | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """The Fish Audio call, as data: url, headers, JSON body.

    `reference_id` is the chosen voice: `voice_id` when a caller names one
    (the Agency room's female and male voices, read from env by the caller
    -- never from a browser), otherwise FISH_AUDIO_VOICE_ID, ATLAS's own.
    With neither set it is left out rather than sent as null, and Fish Audio
    uses its default voice -- a request that names no voice is honest; one
    that names `None` is a guess at how their API treats it.

    `speed` (None = the field is left out, Fish's normal pace) is sent as
    `"prosody": {"speed": speed, "volume": 0}` -- docs.fish.audio, the TTS
    endpoint, read 2026-10-11: prosody speed 0.5 to 2.0, default 1; volume
    in dB, default 0. Only the Academy tutorials pass one (the owner, 2026-10-11:
    "make him talk slower, no rush"); the phone and menu voices never do.

    `model` (None = FISH_AUDIO_MODEL, else "s1") is the `model` header
    (docs.fish.audio migrate-from-s1, read 2026-10-12). Only the Academy
    tutorials name one (ACADEMY_TUTORIALS_FISH_MODEL); the phone, the menu
    and the reels keep FISH_AUDIO_MODEL.
    """
    headers = {
        "Authorization": f"Bearer {os.environ['FISH_AUDIO_API_KEY']}",
        "Content-Type": "application/json",
        "model": ((model or "").strip() or os.getenv("FISH_AUDIO_MODEL", "").strip()
                  or "s1"),
    }
    body: dict[str, Any] = {"text": text}
    voice_id = (voice_id or "").strip() or os.getenv("FISH_AUDIO_VOICE_ID", "").strip()
    if voice_id:
        body["reference_id"] = voice_id
    if speed is not None:
        body["prosody"] = {"speed": max(0.5, min(2.0, float(speed))), "volume": 0}
    body["format"] = "mp3"
    return FISH_TTS_URL, headers, body
