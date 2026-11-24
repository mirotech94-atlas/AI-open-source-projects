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

Since 2026-10-29 ATLAS's own voice is the local clone of the NotebookLM host
(voice_clone.py) when it is set, with Fish Audio as its fallback; each
voice's clips are keyed by that voice, so neither answers for the other.
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


def fish_voice() -> str:
    """ATLAS's own Fish voice, the fallback when the local clone is down."""
    return os.getenv("FISH_AUDIO_VOICE_ID", "").strip()


def clone_voice() -> str:
    """The local clone of the NotebookLM host (voice_clone.py), by tag."""
    from . import voice_clone
    return f"clone:{voice_clone.tag()}"


def _voice() -> str:
    """The voice ATLAS speaks in by default: the clone when it is set,
    otherwise Fish. Each voice's clips are kept under its own key, so a clip
    kept in one never answers for the other."""
    from . import voice_clone
    return clone_voice() if voice_clone.configured() else fish_voice()


def path_for(text: str, voice: str | None = None) -> Path:
    v = _voice() if voice is None else voice
    key = hashlib.sha256(f"{v}\n{text.strip()}".encode()).hexdigest()
    return _dir() / f"{key}.mp3"


def is_fixed(text: str) -> bool:
    return text.strip() in {t.strip() for t in fixed_lines().values()}


def cached(text: str, voice: str | None = None) -> bytes | None:
    p = path_for(text, voice)
    try:
        return p.read_bytes() if p.is_file() and p.stat().st_size else None
    except OSError:
        return None


def store(text: str, audio: bytes, voice: str | None = None) -> None:
    """Keep a fixed line's audio, under the voice that said it (`voice`,
    default ATLAS's own). Anything else is refused: answers are never
    written to disk."""
    if not audio or not is_fixed(text):
        return
    p = path_for(text, voice)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        tmp.write_bytes(audio)
        tmp.replace(p)
    except OSError as exc:
        log.warning("could not keep a voice clip: %s", type(exc).__name__)


def fallback_for(text: str) -> bytes | None:
    """When neither the clone nor Fish Audio can render `text`: the same
    line from the cache, or -- for the "free models are out" line, whose reset time changes --
    its "later today" version: kept in the clone voice first, then in Fish.
    Never a system voice."""
    from .voice import NO_MODEL_SPENT
    head = NO_MODEL_SPENT.split("{when}", 1)[0]
    generic = text.strip().startswith(head.strip())
    for voice in dict.fromkeys((_voice(), fish_voice())):
        if (audio := cached(text, voice)) is not None:
            return audio
        if generic and (audio := cached(fixed_lines()["no_model"], voice)) is not None:
            return audio
    return None
