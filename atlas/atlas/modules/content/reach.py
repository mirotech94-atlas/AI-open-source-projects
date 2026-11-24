"""Reach without paid promotion (Miro, 2026-09-29): which AI-stories reel
deserves a boost, and when in the day a reel should post.

**Promote candidates** (`candidates`, the `promote_candidates` check, and
`kernel.reels.promote_lines`, which sends the cards). A stories reel on
TikTok or Instagram is a candidate when, from `reel_metrics` as the daily
read left it:

  * a reading taken within `PROMOTE_WINDOW_HOURS` (48) of posting shows at
    least `REELS_PROMOTE_MIN_VIEWS` views (1,000), AND
  * its engagement ((likes + comments + shares) / views, only when all four
    were read) is at least `REELS_PROMOTE_MIN_ENGAGEMENT` (0.05), AND
  * those views are at least `REELS_PROMOTE_MULTIPLE` (3) times the median
    views of the account's other posts on that platform over the last
    `BASELINE_DAYS` (14) -- a median of at least `REELS_PROMOTE_BASELINE_MIN`
    (3) posts, else there is nothing to compare with and it is UNKNOWN.

Only the AI-stories lane (`hashtags.lane_of`: a format, calendar story,
series or imported episode). Never a clip: Clipping Culture permanently
bans paid boosting of campaign clips, and a clip is never in `reel_posts`
-- anything whose lane is not recognised is excluded all the same. Never a
football or course reel either.

The card is information only: the reel, its numbers with the time they were
read, why it qualifies, and "suggested budget $10-20 -- promote it yourself
in the app". There is no action behind it and none exists anywhere in
ATLAS: no ads or promote endpoint is ever called, no money moves (money out
is Tier 3, permanently denied). If the numbers cannot be read -- no reading,
stale readings, too few posts to compare -- there is no card and the check
reads UNKNOWN.

**Best posting hours** (`best_hours`, the `best_hours` check, and
`best_hour_hold`). From `reel_posts` x `reel_metrics`: each post's views at
about 24 h (the reading nearest 24 h after posting, between 12 h and 36 h),
bucketed by the New York hour it posted. A bucket is trusted only with at
least `REELS_BEST_HOURS_MIN_POSTS` (3) posts, and a platform's table only
with two trusted buckets to compare. One post goes to both platforms at the
same moment, so the hold follows TikTok's table when it is trusted, else
Instagram's. With a trusted table, a reel is held for the best trusted
hour within `HOLD_HOURS` (6) of when it was first ready today -- a fixed
window, not one that rolls forward each tick -- only for an hour at least as
good as the account's overall median, never past `LAST_HOUR` (22:00) New
York, so it never slips to tomorrow, and never around a daily cap (the cap
is checked as before when it does post). "First ready" is when this
process first offered the reel today; a restart re-anchors it, still inside
the same day and before 22:00. Without enough data nothing is held and the
check says "not enough data" (UNKNOWN).

Nothing here posts, follows, likes or messages anyone: no engagement
automation of any kind.
"""
from __future__ import annotations

import os
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import Health, Observation
from ...kernel.state import fetch_all
from .hashtags import lane_of

NY = ZoneInfo("America/New_York")
PLATFORMS = ("tiktok", "instagram")
APPS = {"tiktok": "TikTok", "instagram": "Instagram"}

# ----------------------------------------------------------- thresholds

PROMOTE_WINDOW_HOURS = 48
CANDIDATE_DAYS = 4           # how long after posting a reel is still offered
BASELINE_DAYS = 14
STALE_HOURS = 36             # readings older than this are not a fresh read
BUDGET_LINE = "suggested budget $10–20"


def _num(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class Thresholds:
    min_views: float = 1000.0
    min_engagement: float = 0.05
    multiple: float = 3.0
    baseline_min: int = 3

    @classmethod
    def from_env(cls) -> Thresholds:
        return cls(
            min_views=_num("REELS_PROMOTE_MIN_VIEWS", 1000.0),
            min_engagement=_num("REELS_PROMOTE_MIN_ENGAGEMENT", 0.05),
            multiple=_num("REELS_PROMOTE_MULTIPLE", 3.0),
            baseline_min=max(1, int(_num("REELS_PROMOTE_BASELINE_MIN", 3))))


@dataclass(frozen=True)
class Candidate:
    platform: str
    post_ref: str
    posted_at: datetime
    read_at: datetime
    views: float
    likes: float | None
    comments: float | None
    shares: float | None
    engagement: float
    baseline: float
    baseline_posts: int

    @property
    def multiple(self) -> float:
        return self.views / self.baseline if self.baseline else float("inf")


# ----------------------------------------------------------- promote: pure

def _lane(row: dict[str, Any]) -> str:
    return lane_of(str(row.get("source") or ""), row.get("format_source"))[0]


def candidates(rows: list[dict[str, Any]], now: datetime,
               th: Thresholds) -> tuple[list[Candidate], list[str]]:
    """(candidates, why a platform could not be judged). `rows` are one per
    (post, reading): platform, post_ref, posted_at, source, format_source,
    views, likes, comments, shares, engagement, read_at (the reading's
    columns None for a post never read). Pure."""
    posts: dict[tuple[str, str], dict[str, Any]] = {}
    readings: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        platform = str(row.get("platform") or "")
        if platform not in PLATFORMS:
            continue
        key = (platform, str(row.get("post_ref")))
        posts.setdefault(key, row)
        if row.get("read_at") is not None:
            readings.setdefault(key, []).append(row)
    found: list[Candidate] = []
    unknown: list[str] = []
    for platform in PLATFORMS:
        recent = [k for k, p in posts.items() if k[0] == platform
                  and _lane(p) == "stories"
                  and p["posted_at"] > now - timedelta(days=CANDIDATE_DAYS)]
        if not recent:
            continue
        latest = {k: max(rs, key=lambda r: r["read_at"])
                  for k, rs in readings.items() if k[0] == platform}
        newest = max((r["read_at"] for r in latest.values()), default=None)
        if newest is None or newest < now - timedelta(hours=STALE_HOURS):
            unknown.append(f"{APPS[platform]}: no fresh reading of its reels "
                           f"(last {'never' if newest is None else f'{newest:%d %b %H:%M} UTC'})")
            continue
        # A reel posted since the last daily read is simply not read YET;
        # only one past STALE_HOURS with no reading is a failed read.
        unread = [k for k in recent if k not in readings and posts[k]["posted_at"]
                  < now - timedelta(hours=STALE_HOURS)]
        if unread:
            unknown.append(f"{APPS[platform]}: {len(unread)} recent reel(s) not read")
        for key in recent:
            post = posts[key]
            early = [r for r in readings.get(key, ())
                     if r["read_at"] <= post["posted_at"]
                     + timedelta(hours=PROMOTE_WINDOW_HOURS)
                     and r.get("views") is not None]
            if not early:
                continue
            best = max(early, key=lambda r: r["read_at"])
            views, eng = float(best["views"]), best.get("engagement")
            if views < th.min_views or eng is None or eng < th.min_engagement:
                continue
            others = [float(r["views"]) for k, r in latest.items()
                      if k != key and r.get("views") is not None
                      and posts[k]["posted_at"] > now - timedelta(days=BASELINE_DAYS)]
            if len(others) < th.baseline_min:
                unknown.append(f"{APPS[platform]} {key[1]}: only {len(others)} other "
                               f"post(s) in {BASELINE_DAYS} days to compare with "
                               f"(needs {th.baseline_min})")
                continue
            median = statistics.median(others)
            if median > 0 and views < th.multiple * median:
                continue
            found.append(Candidate(platform, key[1], post["posted_at"],
                                   best["read_at"], views, best.get("likes"),
                                   best.get("comments"), best.get("shares"),
                                   float(eng), float(median), len(others)))
    return found, unknown


def card_text(c: Candidate, link: str | None, th: Thresholds) -> tuple[str, str]:
    """(title, body) of the information-only card."""
    app = APPS[c.platform]
    hours = (c.read_at - c.posted_at).total_seconds() / 3600
    title = f"A stories reel is outperforming on {app} — worth promoting"

    def n(v: float | None) -> str:
        return "not read" if v is None else f"{v:,.0f}"

    where = link or (f"link not readable — {app} post {c.post_ref}; open it from "
                     f"the stories account's profile")
    body = "\n".join([
        f"Reel: {where}",
        (f"Numbers (read {c.read_at:%d %b %H:%M} UTC, {hours:.0f} h after it "
         f"posted {c.posted_at:%d %b %H:%M} UTC): {n(c.views)} views, "
         f"{n(c.likes)} likes, {n(c.comments)} comments, {n(c.shares)} shares, "
         f"engagement {c.engagement:.1%}."),
        (f"Why: at least {th.min_views:,.0f} views within "
         f"{PROMOTE_WINDOW_HOURS} h, engagement at least {th.min_engagement:.0%}, "
         f"and {c.multiple:.1f}x the account's median {app} views "
         f"({c.baseline:,.0f} over {c.baseline_posts} posts, last "
         f"{BASELINE_DAYS} days; at least {th.multiple:g}x needed)."),
        (f"{BUDGET_LINE[0].upper()}{BUDGET_LINE[1:]} — promote it yourself in "
         f"the {app} app."),
        ("Information only: ATLAS has no promote or ads action, spends nothing "
         "and will not boost it."),
    ])
    return title, body


# ----------------------------------------------------------- promote: reads

PROMOTE_SQL = f"""
    SELECT p.platform, p.post_ref, p.at AS posted_at, d.source,
           f.source AS format_source, m.views, m.likes, m.comments, m.shares,
           m.engagement, m.at AS read_at
      FROM reel_posts p
      LEFT JOIN content_media cm ON cm.id = p.media_id
      LEFT JOIN content_drafts d ON d.id = cm.draft_id
      LEFT JOIN reel_formats f ON d.source = 'reel_format:' || f.id
      LEFT JOIN reel_metrics m
        ON m.platform = p.platform AND m.post_ref = p.post_ref
     WHERE p.at > now() - interval '{BASELINE_DAYS} days'"""


async def read_candidates(now: datetime | None = None
                          ) -> tuple[list[Candidate], list[str], datetime]:
    """Read fresh and judge. Raises when the tables cannot be read."""
    rows = [dict(r) for r in await fetch_all(PROMOTE_SQL)]
    at = datetime.now(UTC)
    found, unknown = candidates(rows, now or at, Thresholds.from_env())
    return found, unknown, at


async def check_promote_candidates() -> Observation:
    """Reads only; the cards are `kernel.reels.promote_lines`."""
    try:
        found, unknown, at = await read_candidates()
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return Observation("promote_candidates", Health.UNKNOWN,
                           detail=f"reel metrics unreadable: {explain(exc)}")
    names = ", ".join(f"{APPS[c.platform]} {c.post_ref} ({c.views:,.0f} views, "
                      f"{c.multiple:.1f}x)" for c in found)
    detail = (f"{len(found)} stories reel(s) worth promoting"
              + (f": {names}" if names else "") + f" (read {at:%H:%M} UTC)")
    if unknown:
        return Observation("promote_candidates", Health.UNKNOWN,
                           value=float(len(found)), unit="reels",
                           detail=detail + "; not judged — " + "; ".join(unknown[:4]))
    return Observation("promote_candidates", Health.OK, value=float(len(found)),
                       unit="reels", detail=detail)


async def reel_link(platform: str, post_ref: str) -> str | None:
    """The post's public link, READ from the platform, never built from an
    id. None when it cannot be read."""
    try:
        if platform == "instagram":
            from .. import instagram
            data = await instagram._get(post_ref, account="stories",
                                        fields="permalink")
            url = str((data or {}).get("permalink") or "")
            return url if url.startswith("https://www.instagram.com/") else None
        if platform == "tiktok":
            import httpx

            from . import postiz
            if not postiz.configured():
                return None
            now = datetime.now(UTC)
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{postiz.base()}{postiz.PATH_POSTS}",
                                headers={"Authorization": postiz._key()},
                                params={"startDate": (now - timedelta(
                                            days=CANDIDATE_DAYS + 1)).isoformat(),
                                        "endDate": now.isoformat()})
            if r.status_code >= 400:
                return None
            found = r.json()
            listed = found.get("posts") if isinstance(found, dict) else found
            for item in listed or []:
                if isinstance(item, dict) and str(item.get("id")) == post_ref:
                    url = str(item.get("releaseURL") or item.get("releaseUrl") or "")
                    return url if postiz.TIKTOK_URL.match(url) else None
    except Exception:  # noqa: BLE001
        return None
    return None


# ----------------------------------------------------------- best hours

HOLD_HOURS = 6
LAST_HOUR = 22
BEST_HOUR_HELD = "held for the best posting hour"
BEST_HOURS_SQL = """
    SELECT p.platform, p.post_ref, p.at AS posted_at, m.views, m.at AS read_at
      FROM reel_posts p
      JOIN reel_metrics m ON m.platform = p.platform AND m.post_ref = p.post_ref
     WHERE p.at > now() - interval '90 days' AND m.views IS NOT NULL
       AND m.at BETWEEN p.at + interval '12 hours' AND p.at + interval '36 hours'"""


def min_posts() -> int:
    return max(1, int(_num("REELS_BEST_HOURS_MIN_POSTS", 3)))


def best_hours_enabled() -> bool:
    return os.getenv("REELS_BEST_HOURS", "on").strip().lower() not in (
        "0", "off", "false", "no")


@dataclass(frozen=True)
class HourTable:
    platform: str
    buckets: dict[int, tuple[int, float]]    # NY hour -> (posts, median views)
    overall: float                           # median over every post
    need: int

    @property
    def trusted(self) -> dict[int, float]:
        return {h: med for h, (n, med) in self.buckets.items() if n >= self.need}

    @property
    def usable(self) -> bool:
        return len(self.trusted) >= 2


def best_hours(rows: list[dict[str, Any]], need: int) -> dict[str, HourTable]:
    """Per platform, views at ~24 h by the New York hour a reel posted. Each
    post counts once: its reading nearest 24 h after posting. Pure."""
    nearest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("views") is None or row.get("read_at") is None:
            continue
        key = (str(row["platform"]), str(row["post_ref"]))
        off = abs((row["read_at"] - row["posted_at"]).total_seconds() - 86400)
        have = nearest.get(key)
        if have is None or off < abs(
                (have["read_at"] - have["posted_at"]).total_seconds() - 86400):
            nearest[key] = row
    by: dict[str, dict[int, list[float]]] = {}
    for (platform, _), row in nearest.items():
        hour = row["posted_at"].astimezone(NY).hour
        by.setdefault(platform, {}).setdefault(hour, []).append(float(row["views"]))
    out: dict[str, HourTable] = {}
    for platform, hours in by.items():
        every = [v for vs in hours.values() for v in vs]
        out[platform] = HourTable(
            platform, {h: (len(vs), statistics.median(vs)) for h, vs in hours.items()},
            statistics.median(every), need)
    return out


def hold_until(tables: dict[str, HourTable], now: datetime,
               ready: datetime | None = None) -> tuple[int | None, str]:
    """(the New York hour to hold for, why) -- None posts now. The window is
    `HOLD_HOURS` from `ready` (when the reel was first ready; `now` if not
    given), never past LAST_HOUR, never into another day. Pure."""
    table = next((tables[p] for p in PLATFORMS if p in tables and tables[p].usable),
                 None)
    if table is None:
        return None, "not enough data"
    ready = ready or now
    local, first = now.astimezone(NY), ready.astimezone(NY)
    if first.date() != local.date() or now >= ready + timedelta(hours=HOLD_HOURS):
        return None, f"held {HOLD_HOURS} h already; posts now"
    h = local.hour
    last = min(first.hour + HOLD_HOURS, LAST_HOUR)
    window = {hour: med for hour, med in table.trusted.items()
              if h <= hour <= last and med >= table.overall}
    if not window:
        return None, f"no better {APPS[table.platform]} hour in the next {HOLD_HOURS} h"
    best = max(window, key=lambda hour: (window[hour], -hour))
    if best == h:
        return None, f"{h:02d}:00 is the best {APPS[table.platform]} hour now"
    return best, (f"{BEST_HOUR_HELD} ({best:02d}:00 New York; median "
                  f"{window[best]:,.0f} views at 24 h on {APPS[table.platform]} "
                  f"vs {table.overall:,.0f} overall)")


async def read_tables() -> tuple[dict[str, HourTable], datetime]:
    rows = [dict(r) for r in await fetch_all(BEST_HOURS_SQL)]
    return best_hours(rows, min_posts()), datetime.now(UTC)


# media id -> when this process first offered it (for the fixed window).
_READY: dict[int, datetime] = {}


def _ready_at(media_id: int | None, now: datetime) -> datetime:
    if media_id is None:
        return now
    first = _READY.get(media_id)
    if first is None or first.astimezone(NY).date() != now.astimezone(NY).date():
        first = _READY[media_id] = now
    return first


async def best_hour_hold(media_id: int | None = None,
                         now: datetime | None = None) -> str | None:
    """Why a ready reel should wait for a better hour today, or None to post
    now. Anything unreadable posts now: a hold is a nudge, never a gate."""
    if not best_hours_enabled():
        return None
    now = now or datetime.now(UTC)
    ready = _ready_at(media_id, now)
    try:
        tables, _ = await read_tables()
    except Exception:  # noqa: BLE001
        return None
    hour, why = hold_until(tables, now, ready)
    return why if hour is not None else None


def _row(table: HourTable) -> str:
    cells = []
    for hour in sorted(table.buckets):
        n, med = table.buckets[hour]
        mark = "" if n >= table.need else "?"
        cells.append(f"{hour:02d}h {med:,.0f} ({n}{mark})")
    return f"{APPS.get(table.platform, table.platform)}: " + ", ".join(cells)


async def check_best_hours() -> Observation:
    """The table, with its read time. UNKNOWN while no platform has two
    hours with enough posts: then the schedule is left as it is."""
    try:
        tables, at = await read_tables()
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return Observation("best_hours", Health.UNKNOWN,
                           detail=f"reel metrics unreadable: {explain(exc)}")
    need = min_posts()
    rows = [_row(tables[p]) for p in PLATFORMS if p in tables]
    table = " · ".join(rows) if rows else "no post has a reading at ~24 h yet"
    detail = (f"median views at ~24 h by New York posting hour (posts; ? = under "
              f"{need}): {table} (read {at:%H:%M} UTC)")
    if not any(t.usable for t in tables.values()):
        return Observation("best_hours", Health.UNKNOWN,
                           detail=(f"not enough data — the schedule stays as it is; "
                                   f"needs {need}+ posts in at least two hours. "
                                   + detail))
    best = {p: max(t.trusted, key=lambda h: t.trusted[h])
            for p, t in tables.items() if t.usable}
    said = ", ".join(f"{APPS.get(p, p)} {h:02d}:00" for p, h in best.items())
    return Observation("best_hours", Health.OK, value=float(len(best)),
                       unit="platforms", detail=f"best: {said}. {detail}")
