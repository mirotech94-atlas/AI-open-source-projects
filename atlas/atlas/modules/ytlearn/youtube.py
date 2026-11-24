"""The read-only YouTube collector: a video id in, a transcript out.

Collectors only read (invariant 1). Nothing here writes anywhere outside this
process. What it accepts is deliberately narrow:

  * `parse_video_id` takes a YouTube address and returns the 11-character id
    or None. The host must be exactly youtube.com, www.youtube.com,
    m.youtube.com or youtu.be; no user-info, no port, no other scheme. The
    address is never fetched. Everything fetched below is built from the id
    alone, so there is no way to point it at another host (no SSRF).
  * `fetch` answers a `Transcript` (health OK) or an `Unreadable` (health
    UNKNOWN, with a plain reason). A video without captions, a rate limit, a
    missing library and a timeout are all `Unreadable`: never an empty
    transcript, never a guess (invariants 6 and 7).

Transcripts come from `youtube-transcript-api` (keyless, pinned in
constraints.txt), imported only when a read happens. Audio and podcasts are out
of scope: with no captions the video is simply unreadable.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from ...kernel.contracts import Health

ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
_ID_PATHS = frozenset({"shorts", "embed", "live"})
_BAD_CHARS = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
URL_MAX = 300
TEXT_MAX = 24000          # characters of transcript read; longer is cut, and said so
TITLE_MAX = 100
FETCH_TIMEOUT_S = 45.0
WATCH = "https://www.youtube.com/watch?v={id}"
OEMBED = "https://www.youtube.com/oembed"
UNREADABLE = "could not read this video"


def parse_video_id(text: str | None, *, allow_bare: bool = False) -> str | None:
    """The 11-character video id of a YouTube address, else None."""
    raw = (text or "").strip().strip("<>")
    if not raw or len(raw) > URL_MAX or _BAD_CHARS.search(raw):
        return None
    if allow_bare and ID_RE.fullmatch(raw):
        return raw
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower()
        # `netloc` must be the bare host: no user@, no :port.
        if (parts.scheme.lower() not in ("http", "https") or host not in HOSTS
                or parts.netloc.lower() != host or parts.username is not None
                or parts.port is not None):
            return None
    except ValueError:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if host == "youtu.be":
        candidate = segments[0] if len(segments) == 1 else ""
    elif len(segments) == 1 and segments[0] == "watch":
        values = parse_qs(parts.query).get("v") or []
        candidate = values[0] if len(values) == 1 else ""
    elif len(segments) == 2 and segments[0] in _ID_PATHS:
        candidate = segments[1]
    else:
        candidate = ""
    return candidate if ID_RE.fullmatch(candidate) else None


@dataclass(frozen=True, slots=True)
class Transcript:
    video_id: str
    title: str
    channel: str
    published: str          # "unknown" when the source did not say
    text: str
    read_at: datetime
    truncated: bool = False
    health: Health = Health.OK

    @property
    def url(self) -> str:
        return WATCH.format(id=self.video_id)


@dataclass(frozen=True, slots=True)
class Unreadable:
    video_id: str
    reason: str
    read_at: datetime
    health: Health = Health.UNKNOWN

    @property
    def url(self) -> str:
        return WATCH.format(id=self.video_id)


def _clean(text: object, limit: int) -> str:
    out = " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f<>]", " ", str(text or "")).split())
    return out[:limit]


def _why(exc: BaseException) -> str:
    """A plain reason from the library's error class. Never the message: it can
    carry a URL or a page body."""
    name = type(exc).__name__
    if name in ("TranscriptsDisabled", "NoTranscriptFound", "NoTranscriptAvailable"):
        return "this video has no captions to read"
    if name in ("RequestBlocked", "IpBlocked", "TooManyRequests"):
        return "YouTube is rate-limiting reads right now"
    if name in ("VideoUnavailable", "VideoUnplayable", "AgeRestricted",
                "InvalidVideoId"):
        return "this video is not available to read"
    if isinstance(exc, ImportError):
        return "the transcript reader is not installed"
    if isinstance(exc, TimeoutError):
        return "reading the video timed out"
    return UNREADABLE


def _library_fetch(video_id: str) -> str:
    """The default reader: the caption lines of one video, joined. Blocking."""
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore[import-not-found]
    fetched = YouTubeTranscriptApi().fetch(video_id, languages=["en", "en-US", "en-GB"])
    return " ".join(str(getattr(s, "text", None) or (s.get("text") if isinstance(
        s, dict) else "") or "") for s in fetched)


async def _oembed(video_id: str) -> tuple[str, str]:
    """(title, channel) from YouTube's public oEmbed, or ("", "") when it does
    not answer. The address is built from the id; nothing else is fetched."""
    from .. import _http
    data, why = await _http.get_json(
        OEMBED, params={"url": WATCH.format(id=video_id), "format": "json"},
        timeout=15.0)
    if why is not None or not isinstance(data, dict):
        return "", ""
    return _clean(data.get("title"), TITLE_MAX), _clean(data.get("author_name"), 80)


async def fetch(video_id: str, *, published: str = "unknown",
                reader: Callable[[str], str] | None = None,
                meta: Callable[[str], object] | None = None,
                title: str = "", channel: str = "") -> Transcript | Unreadable:
    """Read one video. `reader` and `meta` exist for tests; `title`, `channel`
    and `published` are what the caller already knows (the Academy feed)."""
    now = datetime.now(UTC)
    if not ID_RE.fullmatch(video_id or ""):
        return Unreadable(str(video_id)[:20], "that is not a YouTube video id", now)
    read = reader or _library_fetch
    try:
        raw = await asyncio.wait_for(asyncio.to_thread(read, video_id),
                                     timeout=FETCH_TIMEOUT_S)
    except Exception as exc:                            # noqa: BLE001
        return Unreadable(video_id, _why(exc), now)
    text = " ".join(str(raw or "").split())
    if not text:
        return Unreadable(video_id, "this video has no captions to read", now)
    truncated = len(text) > TEXT_MAX
    if not (title and channel):
        with contextlib.suppress(Exception):
            got = await (meta(video_id) if meta else _oembed(video_id))  # type: ignore[misc]
            title = title or got[0]
            channel = channel or got[1]
    return Transcript(video_id, _clean(title, TITLE_MAX) or "title unknown",
                      _clean(channel, 80) or "channel unknown", published or "unknown",
                      text[:TEXT_MAX], now, truncated)
