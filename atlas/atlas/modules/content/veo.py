"""Veo: real motion video, one 8-second 9:16 clip per scene, on Miro's
Gemini billing.

Verified live on his key (Task 55, relayed from production):

    POST {GEMINI_URL}/models/{VEO_MODEL}:predictLongRunning
         x-goog-api-key: $GEMINI_API_KEY
         {"instances": [{"prompt": ...}],
          "parameters": {"aspectRatio": "9:16", "durationSeconds": 8}}
    -> {"name": "models/.../operations/..."}
    GET  {GEMINI_URL}/{name}          until "done": true
    -> response.generateVideoResponse.generatedSamples[0].video.uri
    GET  that uri, same header         -> 720x1280, 8 s, native audio

It takes a minute or two per clip. The key rides in the header, never the
query string (ed4a247), and is masked out of every reason anyway.

Veo costs money per second, so `VEO_MONTHLY_SECONDS` (default 600) caps
it -- and that cap is the standing approval (ruled, Task 55): renders run
unattended under it, as OpenArt's do under `RENDER_MONTHLY_CAP`. It is
counted in `veo_usage` BEFORE any request: `render.render_veo` reserves the
whole story's seconds under a per-process lock (so two renders cannot both
pass the cap), then reconciles after with a negative row for every clip Veo
never accepted. A clip that failed after Veo took it stays counted -- it
may have been billed, and an under-count is the one error a spend cap must
not make. A render that would cross the cap is not a Veo render at all
(`render.choose_backend`).

The key rides only in `x-goog-api-key`. No request follows a redirect, and
the finished clip is downloaded only from an https host under
`googleapis.com`: an operation that names any other host is refused
before the key could be sent there.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from ...kernel.state import execute_sql, fetch_one

KEY_ENV = "GEMINI_API_KEY"
DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "veo-3.1-fast-generate-preview"
CLIP_SECONDS = 8
DEFAULT_MONTHLY_SECONDS = 600
POLL_S = 10.0
DEADLINE_S = 300.0


class VeoError(Exception):
    """Veo refused, failed, or was unreachable. The message is safe.
    `accepted` says whether Veo had taken the request (and may bill it).
    `status_code` is the HTTP status when Veo answered at all (None for a
    transport failure or a 404 on the wrong model) -- `render.render_veo`
    reads it to decide whether HTTP 429 is worth a retry. `daily_quota` is
    set when the 429's own body says "daily" or "quota", so the card can
    say which kind of exhaustion this was."""

    def __init__(self, message: str, *, accepted: bool = False,
                status_code: int | None = None, daily_quota: bool = False) -> None:
        super().__init__(message)
        self.accepted = accepted
        self.status_code = status_code
        self.daily_quota = daily_quota

    @property
    def credits_depleted(self) -> bool:
        """HTTP 402: the Gemini project's prepaid balance is gone (seen live
        2026-09-30, "Your prepayment credits are depleted"). Like a daily
        quota, no backoff brings it back; unlike one, midnight does not
        either -- only a top-up does."""
        return self.status_code == 402


# Where Miro tops the project up; the check and the failure both name it.
TOP_UP = "https://ai.studio/projects"

# When Veo last answered 402, until it next accepts a clip. Veo has no
# balance endpoint, so the refusal is the only reading there is: the check
# reports the time it was read, never a figure. In memory on purpose: after
# a restart the check says nothing about credits until Veo speaks again.
_depleted_at: datetime | None = None


def note_depleted() -> None:
    global _depleted_at
    _depleted_at = datetime.now(UTC)


def note_accepted() -> None:
    global _depleted_at
    _depleted_at = None


def depleted_since() -> datetime | None:
    return _depleted_at


def configured() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip())


RESOLUTIONS = ("720p", "1080p", "4k")


def resolution() -> str:
    """`VEO_RESOLUTION`: 1080p by default. Miro, 2026-09-27: "make the
    videos clearer". Veo renders 720p unless asked; the reels were upscaled
    from that. 1080p is what Instagram and TikTok actually show; 4k costs
    about three times as much per second and is downscaled to 1080 on the
    way out, which is only worth it for the sharpest source. Anything else
    falls back to 1080p rather than sending Veo a value it refuses."""
    value = os.getenv("VEO_RESOLUTION", "1080p").strip().lower()
    return value if value in RESOLUTIONS else "1080p"


def model() -> str:
    return os.getenv("VEO_MODEL", "").strip() or DEFAULT_MODEL


def base() -> str:
    return (os.getenv("GEMINI_URL", "").strip() or DEFAULT_BASE).rstrip("/")


def monthly_cap() -> int:
    try:
        return int(os.getenv("VEO_MONTHLY_SECONDS", str(DEFAULT_MONTHLY_SECONDS)))
    except ValueError:
        return DEFAULT_MONTHLY_SECONDS


async def seconds_this_month() -> int:
    """Raises when `veo_usage` cannot be read: an unreadable spend is never
    a spend of zero."""
    row = await fetch_one(
        """SELECT coalesce(sum(seconds), 0) AS seconds FROM veo_usage
           WHERE at >= date_trunc('month', now())""")
    return int(row["seconds"]) if row else 0


async def record(seconds: int, media_id: int | None) -> None:
    """One `veo_usage` row: a reservation (positive) or a refund (negative)."""
    await execute_sql(
        "INSERT INTO veo_usage (seconds, model, media_id) VALUES ($1, $2, $3)",
        seconds, model(), media_id)


def pinned(uri: str) -> str | None:
    """A reason when `uri` is not an https URL under googleapis.com."""
    from urllib.parse import urlsplit
    parts = urlsplit(uri)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == "googleapis.com"
                                       or host.endswith(".googleapis.com")):
        return (f"Veo's clip is at {host or 'no host'!r}, not under "
                f"googleapis.com — not fetched, so the key is not sent there")
    return None


def _masked(text: str, key: str) -> str:
    from .._http import redact
    cleaned = redact(text)
    return cleaned.replace(key, "<redacted>") if key else cleaned


def _said(r: httpx.Response) -> str:
    try:
        body = r.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            return " ".join(str(err.get("message") or "").split())[:200]
    except ValueError:
        pass
    return ""


_DAILY_QUOTA = re.compile(r"daily|quota", re.IGNORECASE)


def _check(r: httpx.Response, key: str, what: str) -> None:
    if r.status_code < 400:
        if what == "submit":
            note_accepted()
        return
    if r.status_code == 402:
        note_depleted()
    if r.status_code == 404 and what == "submit":
        raise VeoError(f"Veo model {model()!r} was not found (HTTP 404) — "
                       f"set VEO_MODEL to one this key can use",
                       status_code=404)
    said = _said(r)
    raise VeoError(_masked(f"Veo {what} failed: HTTP {r.status_code}"
                           + (f": {said}" if said else ""), key),
                   status_code=r.status_code,
                   daily_quota=bool(_DAILY_QUOTA.search(said)))


def _uri(operation: dict[str, Any]) -> str | None:
    try:
        samples = operation["response"]["generateVideoResponse"]["generatedSamples"]
        uri = samples[0]["video"]["uri"]
    except (KeyError, IndexError, TypeError):
        return None
    return uri if isinstance(uri, str) and uri else None


async def clip(prompt: str) -> bytes:
    """One 8 s clip's bytes, or VeoError (with `accepted` set once Veo took
    the request). Charges nothing itself: the caller reserved the seconds
    before asking, and reconciles after."""
    key = os.getenv(KEY_ENV, "").strip()
    if not key:
        raise VeoError(f"{KEY_ENV} not set")
    from . import guard
    if (why := guard.ip_refusal(prompt)) is not None:
        raise VeoError(f"refused before Veo: {why}")     # never sent, never billed
    auth = {"x-goog-api-key": key}
    body = {"instances": [{"prompt": prompt}],
            "parameters": {"aspectRatio": "9:16",
                           "durationSeconds": CLIP_SECONDS,
                           "resolution": resolution()}}
    accepted = False
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c:
            r = await c.post(f"{base()}/models/{model()}:predictLongRunning",
                             headers=auth, json=body)
            _check(r, key, "submit")
            name = (r.json() or {}).get("name")
            if not isinstance(name, str) or not name:
                raise VeoError("Veo accepted the request but named no operation")
            accepted = True

            started = time.monotonic()
            while True:
                r = await c.get(f"{base()}/{name}", headers=auth)
                _check(r, key, "poll")
                op = r.json() or {}
                if op.get("done"):
                    break
                if time.monotonic() - started > DEADLINE_S:
                    raise VeoError(f"Veo did not finish within {DEADLINE_S:.0f}s")
                await asyncio.sleep(POLL_S)
            if isinstance(op.get("error"), dict):
                said = " ".join(str(op["error"].get("message") or "").split())[:200]
                raise VeoError(_masked(f"Veo could not make the clip: {said}", key))
            uri = _uri(op)
            if uri is None:
                raise VeoError("Veo finished but returned no video uri")
            if (why := pinned(uri)) is not None:
                raise VeoError(why)
            r = await c.get(uri, headers=auth)
            if 300 <= r.status_code < 400:
                raise VeoError(f"Veo's download answered a redirect (HTTP "
                               f"{r.status_code}) — not followed, so the key "
                               f"is not sent on")
            _check(r, key, "download")
            if not r.content:
                raise VeoError("Veo's video download was empty")
            return r.content
    except VeoError as exc:
        exc.accepted = accepted
        raise
    except httpx.HTTPError as exc:
        from .._http import explain
        raise VeoError(_masked(f"Veo unreachable: {explain(exc)}", key),
                       accepted=accepted) from None
