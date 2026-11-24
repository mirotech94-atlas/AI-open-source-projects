"""ATLAS's own voice: a local zero-shot clone of the NotebookLM host.

the owner, 2026-10-29: ATLAS's spoken voice, in the portal and everywhere local
voice is used, is NotebookLM's voice. NotebookLM has no TTS API, so the
voice is cloned locally: Chatterbox (Resemble AI, MIT) on the Spark, from one
13.5 s reference clip of the male host of a NotebookLM video overview
(`data/voice/notebooklm-host-ref.wav`, outside git). That host is Google's
synthetic voice, not a real person. Chatterbox stamps every clip with its
Perth watermark, and the voice status says it is an AI voice.

The server (`atlas-tts.service`, a user unit on the Spark) listens on the
Spark's loopback only and wants a bearer key. The Mac tunnels it with
`spark-tts.sh up` (bootstrap/mac/com.atlas.spark-tts-tunnel.plist) to
127.0.0.1:18010, which the api container reaches as host.docker.internal.

  ATLAS_VOICE_CLONE_URL   e.g. http://host.docker.internal:18010. Only a
                          loopback, Docker-host or private address is used:
                          never a cloud TTS. Anything else is "not set".
  ATLAS_VOICE_CLONE_KEY   the bearer key (keychain item spark-tts-key).
  ATLAS_VOICE_CLONE_TAG   names the voice; a new tag is a new set of kept
                          clips (default notebooklm-host-v1).

The text sent is words ATLAS has already put on screen. It is turned into
sound, never read as instructions by any model, so it is not wrapped as
untrusted (the markers would be spoken aloud).
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..kernel.contracts import Health

DEFAULT_TAG = "notebooklm-host-v1"
DISCLOSURE = "Synthetic voice (AI), made on ATLAS's own hardware."
# A sentence renders in about 1-4 s on the Spark (its GPU is shared with
# vLLM). 8 s leaves Fish time to answer inside the browser's 15 s
# (FISH_TIMEOUT_MS in useVoice.ts) when the clone hangs.
TIMEOUT_S = float(os.getenv("ATLAS_VOICE_CLONE_TIMEOUT", "8"))
HEALTH_TTL_S = 20.0
_LOCAL_NAMES = {"localhost", "host.docker.internal"}

# (checked_at monotonic, ok, as_of iso, reason)
_health_cache: dict[str, tuple[float, bool, str, str | None]] = {}


def _local_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower()
    if host in _LOCAL_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _url() -> str:
    return os.getenv("ATLAS_VOICE_CLONE_URL", "").strip().rstrip("/")


def _key() -> str:
    return os.getenv("ATLAS_VOICE_CLONE_KEY", "").strip()


def configured() -> bool:
    url = _url()
    return bool(url and _key() and _local_url(url))


def tag() -> str:
    return os.getenv("ATLAS_VOICE_CLONE_TAG", "").strip() or DEFAULT_TAG


def _transport() -> httpx.AsyncBaseTransport | None:
    """Seam for tests; None is httpx's own."""
    return None


def _client(timeout: float) -> httpx.AsyncClient:
    transport = _transport()
    if transport is not None:
        return httpx.AsyncClient(timeout=timeout, transport=transport)
    return httpx.AsyncClient(timeout=timeout)


async def _to_mp3(wav: bytes) -> bytes:
    """wav -> mp3 with the container's ffmpeg, so every clip ATLAS keeps or
    plays stays audio/mpeg (the cache, the offline pack, the clients)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-v", "error", "-f", "wav", "-i", "pipe:0",
        "-codec:a", "libmp3lame", "-q:a", "4", "-f", "mp3", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(wav), timeout=30)
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"ffmpeg could not encode the voice "
                           f"({err.decode(errors='replace')[:120].strip()})")
    return out


async def render(text: str) -> bytes:
    """`text` in the clone voice, as mp3. Raises when it cannot."""
    if not configured():
        raise RuntimeError("the local clone voice is not set (ATLAS_VOICE_CLONE_URL)")
    async with _client(TIMEOUT_S) as client:
        response = await client.post(
            f"{_url()}/tts", json={"text": text},
            headers={"Authorization": f"Bearer {_key()}"})
        response.raise_for_status()
        wav = response.content
    if not wav:
        raise RuntimeError("the clone voice returned no audio")
    return await _to_mp3(wav)


async def _probe() -> bool:
    async with _client(3.0) as client:
        response = await client.get(f"{_url()}/health")
        return response.status_code == 200 and bool(response.json().get("ok"))


async def health() -> tuple[Health, str, str | None]:
    """(health, as_of, reason). OK only when the server answered just now
    (cached for HEALTH_TTL_S); anything else is UNKNOWN, never OK."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if not configured():
        return Health.UNKNOWN, now, "the local clone voice is not set"
    hit = _health_cache.get("clone")
    if hit and time.monotonic() - hit[0] < HEALTH_TTL_S:
        return (Health.OK if hit[1] else Health.UNKNOWN), hit[2], hit[3]
    try:
        ok, reason = await _probe(), None
        if not ok:
            reason = "the clone voice server is loading or unwell"
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        ok, reason = False, f"the clone voice server is unreachable ({explain(exc)})"
    _health_cache["clone"] = (time.monotonic(), ok, now, reason)
    return (Health.OK if ok else Health.UNKNOWN), now, reason


async def status(fish_available: bool) -> dict[str, Any]:
    """The voice status for /capabilities: which voice is speaking, its
    health and when that was read."""
    state, as_of, reason = await health()
    speaking = ("clone" if state is Health.OK
                else "fish" if fish_available else "none")
    return {
        "voice": "NotebookLM host (local clone)",
        "health": state.value,
        "as_of": as_of,
        "reason": reason,
        "speaking": speaking,
        "disclosure": DISCLOSURE,
    }
