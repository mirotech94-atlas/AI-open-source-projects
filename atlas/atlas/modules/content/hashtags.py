"""Niche hashtags per lane, chosen in code from closed lists (Miro,
2026-09-29: reach without paid promotion).

No model writes a hashtag. Each lane has a fixed list, and the only choice
made is which entries of a closed map a reel's own words select:

  * **stories** (the AI-stories reels, the 30-day calendar's story
    episodes, the series): `#aistory #animation #storytime`, plus up to two
    genre tags picked from the reel's format name or title by keyword
    (`GENRES`), plus `#series` for a series part.
  * **football**: `#football #soccer #footballstats`, plus the leagues the
    fetched facts name (`LEAGUES`) -- never a player or a club.
  * **course**: `#aiagents #automation #smallbusiness #aitools`. No income
    word, ever (`guard.income_refusal` passes every one).
  * **business** (the calendar's business lessons) carry their package's
    own tags and get nothing added here.

Clip captions are the campaigns' business (`clips.captions.niche_tags`):
the campaign's required tags first, extras only from its own pool.

Every tag is lower case: the stories guard reads an inner capital
(`#PremierLeague`) as a product name and would refuse the caption. Tags go
in before the "Made with AI" label, which stays last, and never past
`MAX_PER_PLATFORM` hashtags in the whole caption -- ATLAS's own ceiling
(Instagram limits a post to a handful of hashtags; five everywhere keeps
every platform inside it). A tag already in the caption is not repeated,
and nothing already in the caption is ever removed.
"""
from __future__ import annotations

import re
from typing import Any

from .drafts import AI_LABEL

STORIES: tuple[str, ...] = ("#aistory", "#animation", "#storytime")
SERIES_TAG = "#series"
# keyword (in the format name or title, lower case) -> tag. First match wins
# per tag; at most GENRES_MAX genre tags a reel.
GENRES: tuple[tuple[str, str], ...] = (
    ("murder", "#mystery"),
    ("mystery", "#mystery"),
    ("courtroom", "#comedy"),
    ("comedy", "#comedy"),
    ("brainrot", "#brainrot"),
    ("love", "#lovestory"),
    ("revenge", "#revenge"),
    ("fruit", "#fruitdrama"),
    ("vegetable", "#fruitdrama"),
    ("dog", "#funnyanimals"),
    ("cat", "#funnyanimals"),
    ("pet", "#funnyanimals"),
    ("animal", "#funnyanimals"),
    ("drama", "#drama"),
    ("melodrama", "#drama"),
    ("transformation", "#transformation"),
    ("cliffhanger", "#cliffhanger"),
    ("redemption", "#drama"),
)
GENRES_MAX = 2

FOOTBALL: tuple[str, ...] = ("#football", "#soccer", "#footballstats")
# A league or competition as the fetched facts name it -> its tag.
LEAGUES: tuple[tuple[str, str], ...] = (
    ("premier league", "#premierleague"),
    ("la liga", "#laliga"),
    ("laliga", "#laliga"),
    ("champions league", "#championsleague"),
    ("serie a", "#seriea"),
    ("bundesliga", "#bundesliga"),
    ("ligue 1", "#ligue1"),
    ("major league soccer", "#mls"),
    ("saudi", "#saudiproleague"),
)

COURSE: tuple[str, ...] = ("#aiagents", "#automation", "#smallbusiness", "#aitools")

MAX_PER_PLATFORM: dict[str, int] = {"instagram": 5, "facebook": 5, "tiktok": 5}
DEFAULT_MAX = 5
TIKTOK_DISCOVERY = "#fyp"

_TAG = re.compile(r"#\w+")


def line(tags: tuple[str, ...] | list[str]) -> str:
    return " ".join(tags)


def _words(text: str) -> str:
    return " ".join((text or "").lower().split())


def genre_tags(text: str) -> list[str]:
    words = _words(text)
    out: list[str] = []
    for keyword, tag in GENRES:
        if re.search(rf"\b{re.escape(keyword)}s?\b", words) and tag not in out:
            out.append(tag)
        if len(out) >= GENRES_MAX:
            break
    return out


def league_tags(text: str) -> list[str]:
    words = _words(text)
    out: list[str] = []
    for name, tag in LEAGUES:
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", words) and tag not in out:
            out.append(tag)
    return out


def football_tags(fact_set: Any) -> str:
    """The football lane's tags for one fact set: the base three, then the
    leagues its own facts name (as API-Football named them)."""
    parts = [str(getattr(fact_set, "title", "") or "")]
    for fact in getattr(fact_set, "facts", ()) or ():
        parts += [str(getattr(fact, k, "") or "")
                  for k in ("label", "value", "subject")]
    tags = list(FOOTBALL)
    for tag in league_tags(" | ".join(parts)):
        if len(tags) >= DEFAULT_MAX:
            break
        tags.append(tag)
    return line(tags)


def for_lane(lane: str, text: str = "", *, series: bool = False) -> list[str]:
    """The closed tag list for a lane; `text` (a format name, a title, the
    fetched facts) only selects entries from the lane's own maps."""
    if lane == "stories":
        tags = list(STORIES)
        if series:
            tags.append(SERIES_TAG)
        return tags + [t for t in genre_tags(text) if t not in tags]
    if lane == "football":
        return list(FOOTBALL) + league_tags(text)
    if lane == "course":
        return list(COURSE)
    return []


def count(caption: str) -> int:
    return len(_TAG.findall(caption or ""))


def apply(caption: str, tags: list[str] | tuple[str, ...],
          platform: str = "instagram") -> str:
    """`caption` with the tags it lacks added before the AI label (which
    stays last), up to the platform's ceiling. Never removes anything."""
    limit = MAX_PER_PLATFORM.get(platform, DEFAULT_MAX)
    body = (caption or "").rstrip()
    label = ""
    if body.endswith(AI_LABEL):
        body, label = body[: -len(AI_LABEL)].rstrip(), AI_LABEL
    have = {t.lower() for t in _TAG.findall(body)}
    room = limit - len(have)
    extra: list[str] = []
    for tag in tags:
        if room <= 0:
            break
        if tag.lower() in have:
            continue
        extra.append(tag)
        have.add(tag.lower())
        room -= 1
    if not extra:
        return caption
    out = f"{body}\n\n{line(extra)}" if body else line(extra)
    return f"{out}\n\n{label}" if label else out


# ------------------------------------------------------------- which lane

def lane_of(source: str, format_source: str | None = None) -> tuple[str, bool]:
    """(lane, is a series part) from a draft's `source` and, for a
    `reel_format:<id>` draft, that format's `reel_formats.source`. Decided
    in code: anything this does not recognise is 'other' and gets nothing."""
    s = (source or "").strip()
    if s.startswith("series:"):
        return "stories", True
    if s.startswith("calendar:"):
        job_id = s.partition(":")[2]
        if job_id.startswith("business-"):
            return "business", False
        from . import calendar as cal
        try:
            j = cal.job(job_id)
        except Exception:                               # noqa: BLE001
            j = None
        return "stories", bool(j and cal.is_series(j))
    if s.startswith("import:"):
        return "stories", False
    if s.startswith("reel_format:"):
        fs = (format_source or "").strip()
        if fs == "football":
            return "football", False
        if fs == "course":
            return "course", False
        return "stories", False
    return "other", False


async def media_lane(media_id: int) -> tuple[str, bool, str]:
    """(lane, series, words to pick genre tags from) for one render, read
    from its draft. Raises when the row cannot be read; the caller decides."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT d.source, d.title, f.source AS format_source,
                  f.format AS format_name
             FROM content_media m
             JOIN content_drafts d ON d.id = m.draft_id
             LEFT JOIN reel_formats f ON d.source = 'reel_format:' || f.id
            WHERE m.id = $1""", media_id)
    if not row:
        return "other", False, ""
    lane, series = lane_of(str(row.get("source") or ""),
                           row.get("format_source"))
    words = f"{row.get('format_name') or ''} {row.get('title') or ''}"
    return lane, series, words


async def with_lane_tags(media_id: int, caption: str) -> str:
    """The caption with its lane's niche tags; unchanged when the lane
    cannot be read (a missing tag never holds a post)."""
    try:
        lane, series, words = await media_lane(media_id)
    except Exception:                                   # noqa: BLE001
        return caption
    return apply(caption, for_lane(lane, words, series=series))
