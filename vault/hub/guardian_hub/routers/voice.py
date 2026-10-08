"""Vault's ear and mouth -- the only two routes that touch the voice vendor.

Both are session-authenticated dashboard routes. Neither reads a row and
neither writes one: `transcribe` turns audio into text and forgets the audio,
`speak` turns text into audio and forgets the text. That is why there is no
audit row here and no `Edit` -- there is no state change to attribute.

**Nothing in this file carries authority, and that is the whole design.** A
transcript goes back to the browser and lands in the chat composer's
*textarea*, where the customer reads it and presses send like any other
sentence. So an approval-tier repair still comes back as a `ChatProposal`
whose single-use token only the on-screen button can spend, and a misheard
sentence gets edited instead of executed. Do not add a `confirm_token` here,
and do not route a transcript straight through `chat.run_turn` to save a hop:
the hop IS the clamp. Vault's approval tier is token-bound precisely
because an intention has to be attributable, and a sentence spoken in a
waiting room is not attributable to anyone.
`tests/test_voice_authority.py` fails if that stops being true.

Push-to-talk, never a wake word. A wake word means this hub receives audio
from a medical practice's waiting room continuously; the browser's microphone
indicator being lit for exactly as long as a button is held is the only
honest signal a customer can actually check.

`speak` is a POST even though a GET would let an `<audio>` element point
straight at it, because the text is a sentence about somebody's network and a
GET would put it in a URL, an access log and a browser history. The web plays
it through Web Audio rather than an element anyway -- the app's CSP is
`default-src 'self'`, under which a `blob:` URL is not `'self'` -- so the
element was never available to lose.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from .. import ratelimit, voice
from ..deps import require_role

router = APIRouter(prefix="/api/v1", tags=["voice"])

VIEWER = Depends(require_role("viewer"))

#: A held button cannot legitimately produce more than this. The vendor
#: accepts 5 GB, which is not a ceiling this product wants to inherit, and an
#: unbounded upload on a session route is a memory-exhaustion primitive.
MAX_CLIP_BYTES = 10 * 1024 * 1024

#: Comfortably above a verdict sentence plus a "Next:" line, and far below
#: anything that would bill a surprise -- TTS is charged per character, and
#: what the app speaks is deliberately the conclusion and not the whole reply.
SPEAK_CHARS_MAX = 600

#: Generous for a human pressing a button, tight enough that a stuck loop in
#: the browser cannot turn a session into a bill.
RATE = (30, 60)


class SpeakIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=SPEAK_CHARS_MAX)


def _guard(user_id: str) -> None:
    """The two checks both routes share: voice exists, and this user is not
    hammering it. 503 rather than 404 when unconfigured, because the route is
    real and the capability is missing -- and the web hides the controls
    entirely on `OverviewPayload.voice`, so reaching here at all means either
    a stale tab or somebody with curl."""
    if not voice.ready():
        raise HTTPException(503, "Voice is not configured on this hub.")
    if not ratelimit.allow(f"voice:{user_id}", *RATE):
        raise HTTPException(429, "too many voice requests -- wait a minute and try again")


@router.post("/voice/transcribe")
def transcribe(file: UploadFile = File(...), ctx=VIEWER) -> dict:
    # **Plain `def`, deliberately -- do not "modernise" this to `async def`.**
    # `voice.transcribe` below is a synchronous `httpx.post` with a 30-second
    # timeout, and `deploy/guardian-hub.service` runs uvicorn with
    # `--workers 1`. Starlette runs a plain `def` endpoint in its threadpool
    # and an `async def` one directly on the event-loop thread, so declaring
    # this coroutine would put a blocking vendor call on the only thread this
    # process has: one customer letting go of the mic button while ElevenLabs
    # is having a bad afternoon would stall every other tenant's dashboard
    # poll, every sensor's `POST /ingest` and the `WS /api/v1/ws` live feed
    # behind it -- and `RATE` allows 30 of those a minute, so a retry loop
    # chains the stalls. On a product whose proposition is being up when the
    # customer's network is not, that is worse than the 500 `voice.py`'s
    # failure discipline exists to prevent.
    #
    # `UploadFile` is what makes this look like it has to be `async`: its
    # `.read()` is a coroutine. It does not have to be -- `.file` is the
    # underlying `SpooledTemporaryFile`, read synchronously below. The
    # sibling `speak` is a plain `def` for the same reason, as is
    # `routers/chat.py`'s `chat_turn`, the other slow-vendor route here.
    user, _tenant, _role = ctx
    _guard(user.id)

    # The bare type only. Chrome sends `audio/webm;codecs=opus` and Safari
    # sends `audio/mp4`, so matching the whole header would reject the single
    # most common real upload -- and accepting the header unread would make
    # the vendor our input validator.
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in voice.ALLOWED_AUDIO:
        raise HTTPException(415, f"unsupported audio type: {ctype or 'unknown'}")

    # By the time this line runs, Starlette has already buffered the whole
    # multipart body (`UploadFile` resolution parses the request stream into
    # a `SpooledTemporaryFile`, spilling to disk past ~1 MiB) -- so this read
    # is not what stands between an oversized clip and this process's memory.
    # The actual ceiling on that buffering is nginx's `client_max_body_size`
    # (`deploy/nginx-guardian-443.conf`, 20m). What the `+ 1` and the length
    # check below DO guarantee is that a clip past `MAX_CLIP_BYTES` never
    # reaches the vendor and is never billed -- read one byte over the cap,
    # not the cap itself, so a body exactly at the limit still reads clean.
    #
    # `file.file` rather than `await file.read(...)`: that buffering is
    # already done, so this is a read off a spooled temp file and not a
    # reason to make the handler a coroutine -- see the note at the top.
    data = file.file.read(MAX_CLIP_BYTES + 1)
    if len(data) > MAX_CLIP_BYTES:
        raise HTTPException(413, "that clip is too long")

    return {"text": voice.transcribe(data, ctype)}


@router.post("/voice/speak")
def speak(body: SpeakIn, ctx=VIEWER) -> Response:
    user, _tenant, _role = ctx
    _guard(user.id)

    audio = voice.speak(body.text)
    if not audio:
        # Better than 200 with an empty body: the browser would "play" silence
        # and the customer would be left deciding whether they had gone deaf.
        raise HTTPException(502, "The voice service did not answer.")

    # A sentence about somebody's network does not belong in a disk cache.
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})
