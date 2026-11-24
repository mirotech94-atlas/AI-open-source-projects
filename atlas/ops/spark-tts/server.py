"""ATLAS voice: zero-shot clone TTS (Chatterbox, MIT) of one reference clip.

Loopback only (127.0.0.1:8010), bearer key from ~/.config/atlas-tts/api-key
(compared in constant time). POST /tts {"text": ...} -> audio/wav.
GET /health -> {"ok": true, "voice": "<tag>"} once the model and the speaker
conditioning are loaded (computed once at startup, never per request).
"""
from __future__ import annotations

import asyncio
import hmac
import io
import os
import time
from pathlib import Path

import soundfile as sf
import torch
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

HOME = Path.home()
KEY = (HOME / ".config/atlas-tts/api-key").read_text().strip()
REF = os.getenv("ATLAS_TTS_REF", str(HOME / "apps/tts/ref/notebooklm-host-ref.wav"))
VOICE_TAG = os.getenv("ATLAS_TTS_VOICE", "notebooklm-host-v1")
ENGINE = os.getenv("ATLAS_TTS_ENGINE", "turbo")
MAX_CHARS = 1500

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
state: dict = {}
lock = asyncio.Lock()


class Say(BaseModel):
    text: str


@app.on_event("startup")
def load() -> None:
    if ENGINE == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS as M
    else:
        from chatterbox.tts import ChatterboxTTS as M
    model = M.from_pretrained(device="cuda")
    model.prepare_conditionals(REF)
    state["model"] = model


def _auth(authorization: str | None) -> None:
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), KEY.encode()):
        raise HTTPException(401, "unauthorized")


@app.get("/health")
def health() -> JSONResponse:
    ok = "model" in state
    return JSONResponse({"ok": ok, "voice": VOICE_TAG}, status_code=200 if ok else 503)


def _render(text: str) -> bytes:
    model = state["model"]
    with torch.inference_mode():
        wav = model.generate(text)
    buf = io.BytesIO()
    sf.write(buf, wav.squeeze(0).numpy(), model.sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@app.post("/tts")
async def tts(say: Say, authorization: str | None = Header(None)) -> Response:
    _auth(authorization)
    text = say.text.strip()
    if not text:
        raise HTTPException(400, "nothing to say")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, "too long")
    if "model" not in state:
        raise HTTPException(503, "loading")
    async with lock:                       # one GPU render at a time
        t = time.monotonic()
        audio = await asyncio.to_thread(_render, text)
    return Response(audio, media_type="audio/wav",
                    headers={"X-Voice": VOICE_TAG,
                             "X-Render-Ms": str(int((time.monotonic() - t) * 1000))})
