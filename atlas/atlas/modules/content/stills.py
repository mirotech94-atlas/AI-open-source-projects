"""The free render path's two inputs: Gemini stills and Fish narration.

**Stills.** `GEMINI_IMAGE_MODEL` (default `gemini-3.1-flash-image`;
`gemini-3-pro-image` also works -- both verified live on the owner's key, Task
55) through `generateContent`, asking for an image at 9:16. The key rides
in the `x-goog-api-key` header, never `?key=` (the ed4a247 leak pattern
`kernel.llm` still uses for text). A 404 means the model name is wrong for
this key: the stills part is refused with that reason and the render goes
on with typographic cards, saying so -- a reel without pictures is still a
reel, a render that dies on a model name is nothing.

**Narration.** Fish Audio, through `/speak`'s own request builder
(`atlas.modules._fish.fish_request`), mp3. `REELS_FISH_VOICE_ID` picks the
British voice for the reels when set; unset, Fish's default voice
(`FISH_AUDIO_VOICE_ID`, or theirs) is used and the render says so. No voice
id is ever invented.
"""
from __future__ import annotations

import base64
import os

import httpx

DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
MAX_STILLS = 6


def image_model() -> str:
    return os.getenv("GEMINI_IMAGE_MODEL", "").strip() or DEFAULT_IMAGE_MODEL


def _masked(text: str, key: str) -> str:
    from .._http import redact
    cleaned = redact(text)
    return cleaned.replace(key, "<redacted>") if key else cleaned


async def still(prompt: str, *, aspect: str = "9:16") -> tuple[bytes | None, str | None]:
    """One image's bytes at `aspect` (default 9:16, the reels shape), or
    `(None, reason)`. Never raises."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return None, "GEMINI_API_KEY not set"
    from .veo import base
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"],
                                 "imageConfig": {"aspectRatio": aspect}}}
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{base()}/models/{image_model()}:generateContent",
                             headers={"x-goog-api-key": key}, json=body)
    except httpx.HTTPError as exc:
        from .._http import explain
        return None, _masked(f"Gemini image unreachable: {explain(exc)}", key)
    if r.status_code == 404:
        return None, (f"Gemini image model {image_model()!r} was not found "
                      f"(HTTP 404) — set GEMINI_IMAGE_MODEL")
    if r.status_code >= 400:
        return None, _masked(f"Gemini image failed: HTTP {r.status_code}", key)
    try:
        for cand in r.json().get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                data = (part.get("inlineData") or part.get("inline_data") or {})
                if data.get("data"):
                    return base64.b64decode(data["data"]), None
    except (ValueError, AttributeError, TypeError):
        pass
    return None, "Gemini returned no image"


async def narration(text: str, speed: float | None = None, model: str | None = None
                    ) -> tuple[bytes | None, str, str | None]:
    """`(mp3, voice_note, None)` or `(None, "", reason)`. Never raises.
    `speed` is Fish's prosody speed (None: unchanged, the reels' pace);
    `model` the Fish model (None: FISH_AUDIO_MODEL, the reels' own)."""
    if not os.getenv("FISH_AUDIO_API_KEY", "").strip():
        return None, "", "FISH_AUDIO_API_KEY not set — the free render needs narration"
    from .._fish import fish_request
    url, headers, body = fish_request(text, speed=speed, model=model)
    voice = os.getenv("REELS_FISH_VOICE_ID", "").strip()
    if voice:
        body["reference_id"] = voice
        note = "Fish Audio, the British reels voice (REELS_FISH_VOICE_ID)"
    else:
        note = ("Fish Audio's default voice — REELS_FISH_VOICE_ID is unset, so "
                "not the British voice")
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(url, headers=headers, json=body)
            r.raise_for_status()
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        key = os.getenv("FISH_AUDIO_API_KEY", "")
        return None, "", _masked(f"Fish narration failed: {explain(exc)}", key)
    if not r.content:
        return None, "", "Fish Audio returned no audio"
    return r.content, note, None
