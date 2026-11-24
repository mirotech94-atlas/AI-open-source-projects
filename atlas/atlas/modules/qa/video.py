"""QA of a tutorial render before it may be published. Pure: it reads only
what it is given (the manifest and the description text), never the
network, the disk or the database.

`refusal` is called on both success paths of `tutorials.ready()` (the
NotebookLM early return and the Fish path), so nothing reaches YouTube that
this has not passed:
- the manifest names a real narration (never the placeholder) and a length;
- every link in the description is https, carries no credentials, and its
  host is the store, Mapfront, YouTube, or a host the episode itself links;
- a title whose formula carries minutes says them (the time promise);
- the description discloses the AI narration;
- the narration is not a female voice (the owner 2026-10-11: male voice only).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import store_origin

ALLOWED_HOSTS = frozenset({
    store_origin.host(),
    "youtube.com", "www.youtube.com", "youtu.be",
})
NARRATIONS = frozenset({"fish", "notebooklm"})
_URL = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
_MINUTES = re.compile(r"\b\d+\s*(?:min|mins|minutes)\b", re.IGNORECASE)
_DISCLOSED = re.compile(r"synthetic voice \(AI\)|narrator \(AI\)", re.IGNORECASE)
_FEMALE = re.compile(r"\bfemale\b|\bwoman\b|\bsamantha\b", re.IGNORECASE)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def link_refusal(text: str, extra_hosts: frozenset[str] = frozenset()) -> str | None:
    allowed = ALLOWED_HOSTS | extra_hosts
    for url in _URL.findall(text or ""):
        url = url.rstrip(".,;:")
        try:
            parts = urlsplit(url)
        except ValueError:
            return f"the link {url!r} is not a valid URL"
        if parts.scheme.lower() != "https":
            return f"the link {url!r} is not https"
        if parts.username or parts.password:
            return f"the link {url!r} carries credentials"
        host = (parts.hostname or "").lower()
        if not host or host not in allowed:
            return f"the link {url!r} is to {host or 'no host'}, not an allowed host"
    return None


def _episode_hosts(episode: Any) -> frozenset[str]:
    links = getattr(episode, "links", None) or ()
    out = set()
    for item in links:
        url = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else item
        host = _host(str(url))
        if host:
            out.add(host)
    return frozenset(out)


def _title(episode: Any, seconds: float) -> tuple[str, bool]:
    """(the title as uploaded, whether its formula carries minutes)."""
    from ..academy import tutorials
    title = tutorials.final_title(episode, seconds)
    promised = tutorials.title_for(episode, 7) != tutorials.title_for(episode, None)
    return title, promised


def refusal(series: Any, episode: Any, d: Path, m: Any, text: str) -> str | None:
    """None when the render may be published; otherwise why not."""
    audio = str(getattr(m, "audio", "") or "")
    if audio not in NARRATIONS:
        return f"the manifest names narration {audio or 'none'!r}, not a real one"
    seconds = float(getattr(m, "seconds", 0) or 0)
    if seconds <= 0:
        return "the manifest has no length"
    if _FEMALE.search(str(getattr(m, "voice_note", "") or "")):
        return "the narration is a female voice; the owner's rule is a male voice only"
    if not _DISCLOSED.search(text or ""):
        return "the description does not say the narration is an AI voice"
    if (why := link_refusal(text, _episode_hosts(episode))) is not None:
        return why
    title, promised = _title(episode, seconds)
    if promised and not _MINUTES.search(title):
        return f"the title {title!r} does not carry its time promise"
    return None
