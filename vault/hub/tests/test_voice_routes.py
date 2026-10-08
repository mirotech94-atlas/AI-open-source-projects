"""The two voice routes: what they refuse, and what they never keep.

The row-count test is the one that matters most. A clip of a dental
practice's front desk is the `Scan.raw` lesson with a microphone -- the audio
is transcribed and forgotten in the same request, and nothing about it
reaches a table.

Every test below authenticates first with `seeded_demo(client)`
(`tests/test_seed_helpers.py`, the same helper `test_chat_api.py` uses):
`client` on its own carries no session, and `require_role("viewer")` runs as
a route dependency -- resolved before the endpoint body, and in FastAPI's
dependency solving, before body validation too. An unauthenticated request
would fail on the auth dependency rather than exercise anything this file is
actually testing, including the 422 in `test_speak_refuses_an_essay`.
"""
import inspect
import io

import pytest
from sqlalchemy import text as sqltext

from guardian_hub import voice
from guardian_hub.config import settings
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _clip(name="clip.webm", ctype="audio/webm", data=b"fakeaudiobytes"):
    return {"file": (name, io.BytesIO(data), ctype)}


@pytest.fixture
def voiced(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk-test-key", raising=False)
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "voice-xyz", raising=False)


def test_transcribe_is_503_with_a_reason_when_voice_is_off(client, monkeypatch):
    seeded_demo(client)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "", raising=False)
    monkeypatch.setattr(voice.httpx, "post", lambda *a, **k: pytest.fail("called the vendor"))
    r = client.post("/api/v1/voice/transcribe", files=_clip())
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"].lower()


def test_speak_is_503_with_a_reason_when_voice_is_off(client, monkeypatch):
    seeded_demo(client)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "", raising=False)
    r = client.post("/api/v1/voice/speak", json={"text": "hello"})
    assert r.status_code == 503


def test_transcribe_runs_off_the_event_loop():
    """The handler must stay a plain `def`, not a coroutine.

    `voice.transcribe` is a blocking `httpx.post` with a 30-second timeout and
    the hub runs uvicorn with `--workers 1` (`deploy/guardian-hub.service`).
    Starlette runs a plain `def` endpoint in its threadpool and an `async def`
    one on the event-loop thread, so spelling this `async def` would let one
    customer's slow clip stall every other tenant's dashboard poll, every
    sensor's `POST /ingest` and the live feed behind it -- on the single
    worker this process has. `UploadFile.read()` being a coroutine is the
    thing that invites the change back; `.file` is the spooled temp file and
    reads synchronously instead."""
    from guardian_hub.routers.voice import transcribe
    assert not inspect.iscoroutinefunction(transcribe), (
        "POST /voice/transcribe must be a plain `def`: as `async def` its "
        "30s blocking vendor call runs on the event loop of a --workers 1 "
        "uvicorn and stalls every other tenant's request behind it")


def test_transcribe_returns_the_text(client, voiced, monkeypatch):
    seeded_demo(client)
    monkeypatch.setattr(voice, "transcribe", lambda data, ctype: "what is 192.168.1.5")
    r = client.post("/api/v1/voice/transcribe", files=_clip())
    assert r.status_code == 200
    assert r.json() == {"text": "what is 192.168.1.5"}


# `audio/mpeg` and `audio/wav` are here rather than in the "obviously wrong"
# list because they are the near misses: real audio types the vendor would
# happily bill us for, that no `MediaRecorder` produces and no client of ours
# sends. Accepting them widened the billable surface for nothing.
@pytest.mark.parametrize("ctype", ["text/plain", "application/octet-stream",
                                   "audio/x-weird", "audio/mpeg", "audio/wav"])
def test_an_unpinned_content_type_is_415_and_never_reaches_the_vendor(
        client, voiced, monkeypatch, ctype):
    seeded_demo(client)
    monkeypatch.setattr(voice, "transcribe", lambda *a, **k: pytest.fail("forwarded it"))
    r = client.post("/api/v1/voice/transcribe", files=_clip(ctype=ctype))
    assert r.status_code == 415


def test_a_content_type_with_a_codecs_parameter_is_still_accepted(client, voiced, monkeypatch):
    # Chrome sends `audio/webm;codecs=opus`. Matching the whole header would
    # reject the single most common real-world upload.
    seeded_demo(client)
    seen = {}
    monkeypatch.setattr(voice, "transcribe",
                        lambda data, ctype: seen.setdefault("ctype", ctype) or "ok")
    r = client.post("/api/v1/voice/transcribe", files=_clip(ctype="audio/webm;codecs=opus"))
    assert r.status_code == 200
    assert seen["ctype"] == "audio/webm"


def test_an_oversized_clip_is_refused(client, voiced, monkeypatch):
    seeded_demo(client)
    from guardian_hub.routers import voice as vr
    monkeypatch.setattr(voice, "transcribe", lambda *a, **k: pytest.fail("read it all"))
    big = b"\0" * (vr.MAX_CLIP_BYTES + 1024)
    r = client.post("/api/v1/voice/transcribe", files=_clip(data=big))
    assert r.status_code == 413


def test_the_whole_clip_reaches_the_vendor_byte_for_byte(client, voiced, monkeypatch):
    """The cap must not cost a syllable off a clip under it.

    The route reads through `UploadFile.file`, a `SpooledTemporaryFile` --
    a read left at the wrong offset, or one capped at `MAX_CLIP_BYTES`
    instead of one byte past it, truncates the question rather than failing,
    and a truncated question comes back as a plausible wrong sentence in the
    composer instead of an error anybody would notice."""
    seeded_demo(client)
    clip = bytes(range(256)) * 64
    seen: dict[str, bytes] = {}
    monkeypatch.setattr(voice, "transcribe",
                        lambda data, ctype: seen.setdefault("data", data) and "ok")
    r = client.post("/api/v1/voice/transcribe", files=_clip(data=clip))
    assert r.status_code == 200
    assert seen["data"] == clip


def test_speak_returns_mpeg_bytes_that_are_never_cached(client, voiced, monkeypatch):
    seeded_demo(client)
    monkeypatch.setattr(voice, "speak", lambda t: b"ID3mp3bytes")
    r = client.post("/api/v1/voice/speak", json={"text": "Everything is fine."})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert "no-store" in r.headers.get("cache-control", "")
    assert r.content == b"ID3mp3bytes"


def test_speak_refuses_an_essay(client, voiced):
    seeded_demo(client)
    from guardian_hub.routers import voice as vr
    r = client.post("/api/v1/voice/speak", json={"text": "x" * (vr.SPEAK_CHARS_MAX + 1)})
    assert r.status_code == 422


def test_a_vendor_that_returns_nothing_is_502_not_an_empty_sound(client, voiced, monkeypatch):
    seeded_demo(client)
    monkeypatch.setattr(voice, "speak", lambda t: b"")
    r = client.post("/api/v1/voice/speak", json={"text": "hello"})
    assert r.status_code == 502


def test_transcribing_writes_no_row_anywhere(client, voiced, monkeypatch):
    """Audio in, text out, nothing kept."""
    seeded_demo(client)
    monkeypatch.setattr(voice, "transcribe", lambda data, ctype: "hello")
    db = SessionLocal()
    try:
        names = [r[0] for r in db.execute(sqltext(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))]
        before = {n: db.execute(sqltext(f'SELECT COUNT(*) FROM "{n}"')).scalar() for n in names}
    finally:
        db.close()

    assert client.post("/api/v1/voice/transcribe", files=_clip()).status_code == 200

    db = SessionLocal()
    try:
        after = {n: db.execute(sqltext(f'SELECT COUNT(*) FROM "{n}"')).scalar() for n in names}
    finally:
        db.close()
    assert before == after, f"voice wrote rows: { {k: (before[k], after[k]) for k in before if before[k] != after[k]} }"
