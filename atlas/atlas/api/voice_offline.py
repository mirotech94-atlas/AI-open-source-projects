"""The offline pack: ATLAS's few fixed offline lines, in its own Fish voice.

Miro wants ATLAS to speak only in his chosen Fish Audio voice, including when
the network or this server is out of reach. ATLAS Menu and Air Glass cannot
ask Fish anything then, so they keep a small pack of lines rendered here
ahead of time and play those. A line with no kept clip is shown as text and
not spoken. No client falls back to a system voice.

GET /api/voice/offline-pack returns the manifest
    {"voice": <fingerprint>, "built_at": <iso>, "phrases":
        {id: {"text", "url", "sha256", "bytes"}}}
`url` is the existing `/api/voice/clip/{id}?cached_only=true`, so the audio
comes from the same kept files (voice_cache), behind the same sign-in. The
clips are rendered once and kept on disk under ATLAS_VOICE_CACHE_DIR.

It answers 503 with the reason, and never a pack in another voice, when:
  - FISH_AUDIO_VOICE_ID is unset. Fish would then use its default voice, and
    a pack of someone else's voice is exactly what this exists to prevent.
  - a line is not yet kept and Fish cannot render it: FISH_AUDIO_API_KEY is
    unset, or the call failed. Clips rendered before the failure stay kept,
    so the next request carries on from there. A partial manifest is never
    served, so a client keeps the last whole pack it had.
When every line is already kept, the pack is served without calling Fish,
even if Fish is down: those clips are Fish's audio in ATLAS's voice.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from . import auth, voice_cache

log = logging.getLogger("atlas.voice.offline")

router = APIRouter(prefix="/api/voice")
SignedIn = Annotated[auth.Session, Depends(auth.require)]

# The lines a client needs while ATLAS is out of reach, by voice_cache name.
# Each has a caller: ATLAS Menu's "Listening" cue, its "couldn't reach my
# voice" note, "Talk soon", "I can't reach ATLAS", "Sorry, I didn't catch
# that"; Air Glass's "I can't reach ATLAS right now. Gestures still work."
PACK = ("listening", "no_voice", "bye", "unreachable", "didnt_catch",
        "airglass_offline")

# One render at a time: two clients opening at once must not pay Fish twice.
_render_lock = asyncio.Lock()


class PackUnavailable(Exception):
    """Why the pack cannot be served right now (safe to show: no secrets)."""


def phrases() -> dict[str, str]:
    lines = voice_cache.fixed_lines()
    return {name: lines[name] for name in PACK}


def voice_fingerprint() -> str:
    """Names the voice the pack is in without echoing its id: a client that
    sees it change throws its old clips away."""
    vid = os.getenv("FISH_AUDIO_VOICE_ID", "").strip()
    return hashlib.sha256(f"atlas-voice\n{vid}".encode()).hexdigest()[:16]


async def _render(text: str) -> bytes:
    # Looked up at call time, so a test's patch of voice._fish_tts applies.
    from . import voice
    return await voice._fish_tts(text)


async def build_pack() -> dict:
    """The manifest, rendering any line not yet kept. Raises PackUnavailable."""
    if not os.getenv("FISH_AUDIO_VOICE_ID", "").strip():
        raise PackUnavailable(
            "ATLAS's Fish voice is not set (FISH_AUDIO_VOICE_ID), so no offline "
            "lines can be made in it; nothing else is offered in its place.")
    async with _render_lock:
        for name, text in phrases().items():
            if voice_cache.cached(text) is not None:
                continue
            if not os.getenv("FISH_AUDIO_API_KEY"):
                raise PackUnavailable(
                    f"Fish Audio is not configured (FISH_AUDIO_API_KEY), and the "
                    f"{name!r} line has not been rendered yet.")
            try:
                audio = await _render(text)
                if not audio:
                    raise RuntimeError("Fish Audio returned no audio")
            except Exception as exc:                    # noqa: BLE001
                from ..modules._http import explain
                reason = explain(exc)
                log.warning("offline pack: could not render %s: %s", name, reason)
                raise PackUnavailable(
                    f"Fish Audio could not render the {name!r} line: {reason}") from None
            voice_cache.store(text, audio)
            log.info("offline pack: rendered %s in Fish Audio", name)
    out: dict[str, dict[str, object]] = {}
    for name, text in phrases().items():
        audio = voice_cache.cached(text)
        if audio is None:                       # kept a moment ago, then lost
            raise PackUnavailable(f"The {name!r} clip could not be read from disk.")
        out[name] = {
            "text": text,
            "url": f"/api/voice/clip/{name}?cached_only=true",
            "sha256": hashlib.sha256(audio).hexdigest(),
            "bytes": len(audio),
        }
    return {"voice": voice_fingerprint(),
            "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "phrases": out}


@router.get("/offline-pack")
async def offline_pack(_s: SignedIn) -> dict:
    """The offline lines' manifest. 503 with the reason when it cannot be
    made in ATLAS's Fish voice -- never a stand-in voice."""
    try:
        return await build_pack()
    except PackUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
