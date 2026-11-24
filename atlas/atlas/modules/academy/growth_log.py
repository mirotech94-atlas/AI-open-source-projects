"""The weekly 7-day log of the Academy's Shorts, for the Monday summary.

the owner, 2026-10-12 (the growth plan, Q&A: "weekly 7-day performance log").
The plan's routine: per video, the date, platform, URL, topic, CTA,
destination and seven-day views; unavailable fields left blank, never
invented; no conversion rate from site visits ATLAS does not measure (the
store promises no analytics, and keeps that promise).

What is read, all of it ATLAS's own record or a read-only API:

  * `academy_promo_posts`: every Short that went out, its lesson or
    product, when, and the Postiz post ids in its detail;
  * Postiz's own list (`postiz.posts_between`): each post's public URL;
  * `youtube_videos` (refreshed every six hours by the youtube module):
    the Short's views, likes and comments, with the time they were read.

TikTok's numbers are not readable here (no TikTok metrics source), so
they say "not read"; a TikTok URL TikTok never returned says "pending".
Read-only. Never raises: a source that cannot be read is named.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..youtube import api

log = logging.getLogger("academy.example.growth_log")

POSTIZ_ID = re.compile(r"Postiz post (\w+)")
YT_ID = re.compile(r"(?:watch\?v=|shorts/)([A-Za-z0-9_-]{6,})")
TITLE = "*Academy Shorts, last 7 days*"
TUTORIALS = "*Tutorials, last 7 days*"
COLUMNS = "episode | views | likes | comments | avg % | avg s | read (UTC)"
# The Analytics API column behind each figure the table shows.
API_COLUMNS = (("views", "views"), ("likes", "likes"), ("comments", "comments"),
               ("average_view_percentage", "averageViewPercentage"),
               ("average_view_duration", "averageViewDuration"))
# The scopes the tutorial lane needs on its token: analytics to read the
# table, force-ssl for the quiz + lab comment (task 6).
NEEDED_SCOPES = ("yt-analytics.readonly", "youtube.force-ssl")
LOW_RETENTION = 40.0                  # avg % below this: the video loses people


def _what(slug: str) -> str:
    """A post's subject as the owner reads it: "Sell With AI 1.2", or a product."""
    m = re.fullmatch(r"lesson:([a-z0-9-]+)/([0-9.]+)", slug or "")
    if not m:
        return slug or "?"
    from .lesson_short import COURSES
    course = COURSES.get(m.group(1), ("", m.group(1)))[1]
    return f"{course} {m.group(2)}"


async def _postiz_urls(since: datetime, now: datetime) -> tuple[dict[str, tuple[str, str]], str | None]:
    """{postiz id: (platform, public URL or "")}, or the reason it was not read."""
    from ..content import postiz
    try:
        posts = await postiz.posts_between(since - timedelta(hours=1), now + timedelta(hours=1))
    except Exception as exc:                            # noqa: BLE001
        return {}, f"Postiz could not be read ({type(exc).__name__})"
    out = {}
    for p in posts:
        ident = (p.get("integration") or {}).get("providerIdentifier") or ""
        platform = "YouTube" if "youtube" in ident else "TikTok" if "tiktok" in ident else ident
        out[str(p.get("id"))] = (platform, str(p.get("releaseURL") or ""))
    return out, None


async def _youtube_stats(video_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not video_ids:
        return {}
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT video_id, views, likes, comments, measured_at FROM youtube_videos
            WHERE video_id = ANY($1::text[])""", video_ids)
    return {str(r["video_id"]): dict(r) for r in rows}


def _num(v: Any) -> str:
    return "?" if v is None else f"{int(v):,}"


async def rows(days: int = 7, now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """One row per Short posted in the window, and the sources not read."""
    from ...kernel.state import fetch_all
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    gaps: list[str] = []
    posts = await fetch_all(
        """SELECT at, slug, media_id, detail FROM academy_promo_posts
            WHERE at >= $1 ORDER BY at""", since)
    urls, why = await _postiz_urls(since, now)
    if why:
        gaps.append(why)
    out, video_ids = [], []
    for p in posts:
        legs = []
        for pid in POSTIZ_ID.findall(str(p["detail"] or "")):
            platform, url = urls.get(pid, ("", ""))
            vid = (YT_ID.search(url).group(1) if platform == "YouTube" and YT_ID.search(url)
                   else None)
            if vid:
                video_ids.append(vid)
            legs.append({"platform": platform or "?", "url": url, "video_id": vid})
        out.append({"at": p["at"], "what": _what(str(p["slug"])), "legs": legs})
    try:
        stats = await _youtube_stats(video_ids)
    except Exception as exc:                            # noqa: BLE001
        stats = {}
        gaps.append(f"YouTube stats could not be read ({type(exc).__name__})")
    for r in out:
        for leg in r["legs"]:
            leg["stats"] = stats.get(leg["video_id"] or "")
    return out, gaps


def render(rows_: list[dict], gaps: list[str], now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    if not rows_:
        return f"{TITLE}\nNo Academy Short went out this week."
    lines = [TITLE]
    yt_views = [leg["stats"]["views"] for r in rows_ for leg in r["legs"]
                if leg.get("stats") and leg["stats"].get("views") is not None]
    for r in rows_:
        parts = []
        for leg in r["legs"]:
            if leg["platform"] == "YouTube":
                s = leg.get("stats")
                if s:
                    read = s["measured_at"].strftime("%a %H:%M") if s.get("measured_at") else "?"
                    parts.append(f"YouTube {_num(s.get('views'))} views, {_num(s.get('likes'))} "
                                 f"likes (read {read} UTC) {leg['url']}")
                else:
                    parts.append(f"YouTube views not read yet {leg['url'] or '(link pending)'}")
            elif leg["platform"] == "TikTok":
                url = leg["url"] if "/video/" in leg["url"] else "link pending"
                parts.append(f"TikTok views not read (no TikTok metrics source) {url}")
        lines.append(f"• {r['at']:%a %d %b} {r['what']}: " + "; ".join(parts or ["no legs recorded"]))
    lines.append(f"{len(rows_)} Short(s) this week; YouTube views so far {sum(yt_views):,} "
                 f"across {len(yt_views)} read. Views are not sales: the store measures none.")
    for g in gaps:
        lines.append(f"Not read: {g}.")
    return "\n".join(lines)


async def week_section(days: int = 7) -> str:
    # 2026-10-16: where the channel stands against the Partner Program bar
    from . import ypp
    return (f"{await _shorts_section(days)}\n\n{await _tutorials_section(days)}"
            f"\n\n{await end_screen_section(days)}"
            f"\n\n{await access_section()}\n\n{await ypp.section()}")


async def access_section() -> str:
    """Plan F20: paid purchases a buyer may be waiting on (a price not in
    the catalog, a file on sale missing on disk). Read-only; UNKNOWN when it
    can't be read, never "none"."""
    from . import delivery
    from .catalog import load
    at = f"{datetime.now(UTC):%a %H:%M} UTC"
    try:
        cat = load()
        return delivery.exceptions_text(await delivery.exceptions(cat), at)
    except Exception as exc:                            # noqa: BLE001
        log.warning("academy exceptions not read: %s", type(exc).__name__)
        return delivery.exceptions_text(
            delivery.Exceptions("unknown", detail=f"not read ({type(exc).__name__})"),
            at)


async def _shorts_section(days: int) -> str:
    try:
        found, gaps = await rows(days)
    except Exception as exc:                            # noqa: BLE001
        log.warning("growth log not read: %s", type(exc).__name__)
        return (f"{TITLE}\nThe posting record could not be read ({type(exc).__name__}), "
                f"so this week's Shorts are UNKNOWN, not zero.")
    return render(found, gaps)


async def _tutorials_section(days: int) -> str:
    line = await _runway_line()
    try:
        found, gaps = await tutorial_rows(days)
    except Exception as exc:                            # noqa: BLE001
        log.warning("tutorial table not read: %s", type(exc).__name__)
        return (f"{TUTORIALS}\nThe tutorial record could not be read "
                f"({type(exc).__name__}), so this week's tutorials are UNKNOWN, not zero."
                f"\n{line}")
    return f"{render_tutorials(found, gaps)}\n{line}"


async def _runway_line() -> str:
    """"Script runway: claude 2 · n8n 1 · ..." (2026-10-15): days of posting
    left per series; UNKNOWN where the record could not be read. Read-only."""
    from .tutorials import runway, runway_text
    try:
        days = await runway()
        at = datetime.now(UTC)                          # invariant 6: the read time
        # nothing read is "tried", never "read" (every value UNKNOWN)
        how = "tried" if all(n is None for n in days.values()) else "read"
        return f"Script runway ({how} {at:%a %H:%M} UTC): {runway_text(days)}"
    except Exception as exc:                            # noqa: BLE001
        log.warning("script runway not read: %s", type(exc).__name__)
        return f"Script runway (tried {datetime.now(UTC):%a %H:%M} UTC): UNKNOWN"


# ------------------------------------------------- end screens to add
# the owner, 2026-10-17 (docs/academy-video-system/episode-structure.md): native
# end-screen elements are his, in Studio (the Data API has no end-screen
# resource); ATLAS lists, every Monday, each tutorial that went public in
# the last 7 days and what to attach: the next episode of its series when
# that is public, else the series playlist, else "Start here". Read-only,
# fresh reads; a read that fails is UNKNOWN, never a fallback.

END_SCREENS = "*End screens to add*"
STUDIO_LINE = ("Add in YouTube Studio → Editor → End screen; the right 640px of the "
               "last 15 s is kept empty for it.")
START_HERE = 'the "Start here" playlist'


async def _end_screen_posts(since: datetime) -> list[dict]:
    from ...kernel.state import fetch_all
    found = await fetch_all(
        """SELECT series, episode, video_id, playlist_id, at FROM academy_tutorial_posts
            WHERE public AND NOT withdrawn AND at >= $1 ORDER BY at""", since)
    return [dict(r) for r in found]


async def _attach(series_key: str, episode: int, playlist_id: str | None) -> str:
    """What the end screen of `series_key` #`episode` should point at."""
    from . import tutorials
    s = tutorials.series(series_key)
    later = sorted(e.number for e in (s.episodes if s else ()) if e.number > episode)
    if later:
        try:
            row = await tutorials.posted(series_key, later[0])
        except Exception as exc:                        # noqa: BLE001
            log.warning("next episode not read: %s", type(exc).__name__)
            return f"UNKNOWN ({series_key} #{later[0]} not read)"
        if row and row.get("public") and row.get("video_id"):
            return f"{series_key} #{later[0]} https://youtu.be/{row['video_id']}"
    if playlist_id:
        return f"the series playlist https://www.youtube.com/playlist?list={playlist_id}"
    return f"{START_HERE} (by name; ATLAS has no id for it on record)"


async def end_screen_section(days: int = 7, now: datetime | None = None) -> str:
    from . import tutorials
    now = now or datetime.now(UTC)
    head = f"{END_SCREENS} (read {now:%a %H:%M} UTC)"
    try:
        posts = await _end_screen_posts(now - timedelta(days=days))
    except Exception as exc:                            # noqa: BLE001
        log.warning("end-screen list not read: %s", type(exc).__name__)
        return (f"{END_SCREENS} (tried {now:%a %H:%M} UTC)\nThe tutorial record could "
                f"not be read ({type(exc).__name__}), so the list is UNKNOWN.")
    if not posts:
        return f"{head}\nNo tutorial went public in the last {days} days."
    lines = [head]
    for p in posts:
        key, n = str(p["series"]), int(p["episode"])
        got = tutorials.resolve(key, n)
        title = got[1].title if not isinstance(got, str) else f"{key} #{n}"
        nxt = await _attach(key, n, p.get("playlist_id"))
        lines.append(f"• {title} https://youtu.be/{p['video_id']} -> next: {nxt}")
    lines.append(STUDIO_LINE)
    return "\n".join(lines)


# ---------------------------------------------- the Monday tutorial table
# 2026-10-13 (academy growth plan, task 8): per public tutorial of the week,
# views, likes, comments and retention from YouTube Analytics; the best and
# worst title formula by views; one change to try. A figure not read is
# UNKNOWN (never 0, never blank) and the reason is listed. Read-only.

async def _tutorial_posts(since: datetime) -> list[dict]:
    from ...kernel.state import fetch_all
    found = await fetch_all(
        """SELECT series, episode, video_id, at FROM academy_tutorial_posts
            WHERE public AND NOT withdrawn AND at >= $1 ORDER BY at""", since)
    return [dict(r) for r in found]


def _missing_scopes(granted: set[str]) -> list[str]:
    """The NEEDED_SCOPES a token lacks; Google reports each as a full URL."""
    return [n for n in NEEDED_SCOPES
            if not any(g == n or g.endswith("/" + n) for g in granted)]


def _label(series: str, episode: Any) -> tuple[str, str | None]:
    """("n8n 0", "Level 0 · Basics"), or the label and None when the catalog
    does not hold that episode (its formula is then not known)."""
    from .tutorials import resolve
    found = resolve(str(series), episode)
    if isinstance(found, str):
        return f"{series} {episode}", None
    s, ep = found
    return f"{s.key} {ep.number}", ep.level_label


async def tutorial_rows(days: int = 7,
                        now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """One row per public tutorial posted in the window, its analytics or
    None, and every source not read (with why)."""
    from .tutorials import channel as tutorial_channel
    now = now or datetime.now(UTC)
    ch = tutorial_channel()
    gaps: list[str] = []
    try:
        granted, why = await api.token_scopes(ch)
    except Exception as exc:                            # noqa: BLE001
        granted, why = None, f"{ch}: tokeninfo raised {type(exc).__name__}"
    if granted is None:
        gaps.append(f"the {ch} token's scopes could not be read ({why})")
    else:
        missing = _missing_scopes(granted)
        if missing:
            gaps.append(f"the {ch} token lacks {', '.join(missing)}: "
                        f"the owner must re-consent the token")
    out = []
    for p in await _tutorial_posts(now - timedelta(days=days)):
        label, level = _label(p["series"], p["episode"])
        try:
            stats, why = await api.analytics(ch, str(p["video_id"]), days)
        except Exception as exc:                        # noqa: BLE001
            stats, why = None, f"{ch}: analytics raised {type(exc).__name__}"
        if stats is None:
            gaps.append(f"{label} ({p['video_id']}): {why}")
        else:
            absent = [api_name for key, api_name in API_COLUMNS if stats.get(key) is None]
            if absent:
                gaps.append(f"{label}: {', '.join(absent)} not returned by the "
                            f"Analytics API")
        out.append({"label": label, "level_label": level, "video_id": p["video_id"],
                    "at": p["at"], "stats": stats})
    return out, gaps


def _fig(stats: dict | None, key: str, fmt: str = "{:,.0f}") -> str:
    v = (stats or {}).get(key)
    return "UNKNOWN" if v is None else fmt.format(v)


def _read_at(stats: dict | None) -> str:
    """When this row's figures were read (invariant 6), or UNKNOWN."""
    at = (stats or {}).get("read_at")
    return at.strftime("%H:%M") if isinstance(at, datetime) else "UNKNOWN"


def _verdict(rows_: list[dict]) -> str:
    """The best and worst title formula by views (summed per level label),
    and one change to try."""
    views = [(r.get("stats") or {}).get("views") for r in rows_]
    known = not (any(v is None for v in views)
                 or any(r["level_label"] is None for r in rows_))
    if not known:
        formula = "Best formula: UNKNOWN · Worst: UNKNOWN"
    else:
        by_level: dict[str, float] = {}
        for r in rows_:
            by_level[r["level_label"]] = by_level.get(r["level_label"], 0) + r["stats"]["views"]
        best = max(by_level, key=lambda k: by_level[k])
        worst = min(by_level, key=lambda k: by_level[k])
        if len(rows_) == 1:
            formula = f"Best formula: {best} (one episode)"
        elif len(by_level) == 1:
            formula = f"Best formula: {best} (the only formula this week)"
        else:
            formula = f"Best formula: {best} · Worst: {worst}"
    pct = [r["stats"]["average_view_percentage"] for r in rows_
           if r.get("stats") and r["stats"].get("average_view_percentage") is not None]
    if pct and min(pct) < LOW_RETENTION:
        change = "pacing or demo zoom"
    elif pct and len(pct) == len(rows_) and known:
        change = "thumbnail or title (Tier 2 card)"
    else:                         # the lowest views or a retention not read
        change = "nothing until analytics can be read"
    return f"{formula}\nTry: {change}"


def render_tutorials(rows_: list[dict], gaps: list[str],
                     now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    head = f"{TUTORIALS} (read {now:%a %H:%M} UTC)"
    if not rows_:
        lines = [head, "No tutorial went public this week."]
    else:
        lines = [head, COLUMNS]
        for r in rows_:
            s = r.get("stats")
            lines.append(" | ".join([
                r["label"], _fig(s, "views"), _fig(s, "likes"), _fig(s, "comments"),
                _fig(s, "average_view_percentage", "{:.0f}%"),
                _fig(s, "average_view_duration"), _read_at(s)]))
        lines.append(_verdict(rows_))
    for g in gaps:
        lines.append(f"Not read: {g}.")
    return "\n".join(lines)
