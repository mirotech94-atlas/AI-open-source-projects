"""ATLAS's fixed spoken lines, rendered once in Fish Audio and kept on disk.

Miro, 2026-10-06: "make sure the voice is Fish Audio, not the sound of Siri
or anything else; make sure she responds with voice as well." So there is no
system voice anywhere ATLAS answers. The lines ATLAS says again and again --
"Listening", "Go ahead", "I couldn't reach my voice", the line for when its
free models are out -- are rendered in Fish Audio, in ATLAS's own voice, the
first time they are needed, and played from this cache after that: instantly,
at no Fish cost, and still when Fish Audio itself is down. Only if Fish is
unreachable AND nothing is cached is a line shown as text alone.

Files live under ATLAS_VOICE_CACHE_DIR (default `data/voice/cache`, which is
the persistent ./data/app volume on the server), one `<sha256>.mp3` per
(voice, text), so a change of voice is a new file, never the old voice
answering for the new one. Only the fixed lines below are ever written:
answers are not kept (they are personal, and the disk is not for them).
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

log = logging.getLogger("atlas.voice.cache")

# Said when a reply could not be voiced: the text is on screen.
NO_VOICE = "I couldn't reach my voice, so here it is in text."
# Said from a kept clip when a request to ATLAS never arrived.
UNREACHABLE = "I can't reach ATLAS right now."
# Air Glass's own version: its hands and models run in the page.
AIRGLASS_OFFLINE = "I can't reach ATLAS right now. Gestures still work."


def fixed_lines() -> dict[str, str]:
    """Every line ATLAS may say from the cache, by name. Read at call time,
    so the voice module's own wording is the one source."""
    from .voice import NO_MODEL_DOWN, NO_MODEL_SPENT
    return {
        "listening": "Listening.",
        "go_ahead": "Go ahead.",
        "at_your_service": "At your service.",
        "no_voice": NO_VOICE,
        "no_model": NO_MODEL_SPENT.format(when="they reset later today"),
        "no_model_down": NO_MODEL_DOWN,
        "bye": "Talk soon.",
        # Said by ATLAS Menu and Air Glass when ATLAS itself is out of reach
        # (the offline pack, voice_offline.py). Each says only what is known:
        # nothing retries, so none of them promises to.
        "unreachable": UNREACHABLE,
        "airglass_offline": AIRGLASS_OFFLINE,
        "didnt_catch": "Sorry, I didn't catch that.",
    }


def _dir() -> Path:
    return Path(os.getenv("ATLAS_VOICE_CACHE_DIR", "data/voice/cache"))


def _voice() -> str:
    """ATLAS's own Fish voice, the one /speak uses with no voice named."""
    return os.getenv("FISH_AUDIO_VOICE_ID", "").strip()


def path_for(text: str) -> Path:
    key = hashlib.sha256(f"{_voice()}\n{text.strip()}".encode()).hexdigest()
    return _dir() / f"{key}.mp3"


def is_fixed(text: str) -> bool:
    return text.strip() in {t.strip() for t in fixed_lines().values()}


def cached(text: str) -> bytes | None:
    p = path_for(text)
    try:
        return p.read_bytes() if p.is_file() and p.stat().st_size else None
    except OSError:
        return None


def store(text: str, audio: bytes) -> None:
    """Keep a fixed line's audio. Anything else is refused: answers are
    never written to disk."""
    if not audio or not is_fixed(text):
        return
    p = path_for(text)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(p)
    except OSError as exc:
        log.warning("could not keep a voice clip: %s", type(exc).__name__)


def fallback_for(text: str) -> bytes | None:
    """When Fish Audio cannot render `text`: the same line from the cache,
    or -- for the "free models are out" line, whose reset time changes --
    its "later today" version. Never another voice."""
    if (audio := cached(text)) is not None:
        return audio
    from .voice import NO_MODEL_SPENT
    head = NO_MODEL_SPENT.split("{when}", 1)[0]
    if text.strip().startswith(head.strip()):
        return cached(fixed_lines()["no_model"])
    return None
