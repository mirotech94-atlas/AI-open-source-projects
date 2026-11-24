"""Where @example_handle stands against the YouTube Partner Program bar,
for the Monday digest. the owner, 2026-10-16: more subscribers, more viewers,
earnings on the channel.

Read-only, fresh reads only, nothing stored. Every figure carries the time
it was read (invariant 6); a figure that cannot be read is UNKNOWN with the
plain reason, never 0 (invariant 7), and no pace is projected from one.

What is read, all through the tutorial lane's channel token:

  * channels.list statistics: the subscriber count, unless hidden;
  * one Analytics report, ids=channel==MINE, dimensions=day,creatorContentType,
    metrics=views,estimatedMinutesWatched, the last 365 days. creatorContentType
    is documented as a dimension of the time-based channel report, not as a
    filter, so the rows are split here: VIDEO_ON_DEMAND minutes are long-form
    watch time, SHORTS views are Shorts views;
  * one Analytics report, dimensions=day, metrics=subscribersGained,
    subscribersLost, the last 28 days: the subscriber pace;
  * search.list forMine + videos.list: public uploads in the last 6 months
    (the inactivity rule).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..youtube import (
    PARTNER_SHORTS_VIEWS_90D,
    PARTNER_SUBSCRIBERS,
    PARTNER_WATCH_HOURS_12MO,
    api,
)

# The bar, from https://support.google.com/youtube/answer/72851 read
# 2026-10-16 (the constants live, with the same citation, in youtube/__init__):
# 1,000 subscribers AND either 4,000 qualified public long-form watch hours in
# the last 12 months (Shorts-feed watch time does not count) OR 10,000,000
# qualified Shorts-feed views in the last 90 days.
SUBSCRIBERS = PARTNER_SUBSCRIBERS
WATCH_HOURS = PARTNER_WATCH_HOURS_12MO
SHORTS_VIEWS = PARTNER_SHORTS_VIEWS_90D
HOURS_WINDOW, SHORTS_WINDOW = 365, 90
PACE_DAYS = 28
INACTIVE_DAYS = 183                   # "6 months or more" with no upload
UPLOAD_PAGE = 50                      # search.list's largest page
ANALYTICS_SCOPE = "yt-analytics.readonly"

TITLE = "*Monetisation progress (YouTube Partner Program)*"
BAR = (f"Bar: {SUBSCRIBERS:,} subscribers AND either {WATCH_HOURS:,} long-form "
       f"watch hours (365 days) or {SHORTS_VIEWS:,} Shorts views (90 days).")
NOTE = ("Note: these are Analytics API counts. YouTube's eligibility uses "
        "\"qualified\" watch hours and Shorts-feed views (public videos only) and "
        "channels.list rounds subscriber counts, so they may differ. "
        "YouTube Studio → Earn is authoritative.")
SHOPPING = ("Shopping: YouTube Shopping for our own store needs YPP plus a store on "
            "a supported platform (Shopify, Wix, Fourthwall, Spreadshop, Spring, "
            "TeePublic, SHOPLINE…). The store runs on Stripe, so it can't be "
            "connected. Until then the store link goes in descriptions and pinned "
            "comments.")


@dataclass
class Figure:
    name: str                         # as the digest prints it
    target: float
    value: float | None               # None: UNKNOWN, and `reason` says why
    read_at: datetime | None          # when it was read, or tried
    reason: str | None
    rate_per_day: float | None        # the last 28 days' rate; None: UNKNOWN
    rate_reason: str | None = None
    window_days: int | None = None    # a rolling window the figure lives in
    decimals: int = 0


def percent(f: Figure) -> float | None:
    return None if f.value is None else f.value / f.target * 100


def _num(v: float, decimals: int = 0) -> str:
    return f"{v:,.{decimals}f}"


def pace(f: Figure, now: datetime) -> str:
    """The last 28 days' rate projected to the date the target is reached;
    never projected from an UNKNOWN."""
    if f.value is None:
        return "UNKNOWN (the figure was not read)"
    if f.value >= f.target:
        return "reached"
    if f.rate_per_day is None:
        return f"UNKNOWN ({f.rate_reason or 'the rate was not read'})"
    rate = f.rate_per_day
    if rate <= 0:
        return f"not on pace ({_num(rate, 1)}/day over the last {PACE_DAYS} days)"
    if f.window_days and rate * f.window_days < f.target:
        return (f"not on pace: at {_num(rate, 1)}/day the {f.window_days}-day window "
                f"holds at most {_num(rate * f.window_days)}")
    days = math.ceil((f.target - f.value) / rate)
    when = (now + timedelta(days=days)).date()
    return (f"at the last {PACE_DAYS} days' rate ({_num(rate, 1)}/day), "
            f"{_num(f.target)} by {when:%d %b %Y}")


def _sum(rows: list[dict], metric: str, since: str, kind: str | None = None
         ) -> float | None:
    """The metric summed over rows on or after `since` (and of `kind`); None
    when a row in range carries no number, so a gap is never a zero."""
    total = 0.0
    for r in rows:
        if str(r.get("day", "")) < since:
            continue
        if kind is not None and r.get("creatorContentType") != kind:
            continue
        v = r.get(metric)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        total += v
    return total


def _scope_gap(granted: set[str] | None) -> str | None:
    if granted is None:
        return None                   # unknown: try the read and let it answer
    if any(g == ANALYTICS_SCOPE or g.endswith("/" + ANALYTICS_SCOPE) for g in granted):
        return None
    return (f"the token lacks {ANALYTICS_SCOPE}: the owner still owes the login "
            f"re-consent")


async def _guard(coro: Any, what: str) -> tuple[Any, str | None]:
    try:
        return await coro
    except Exception as exc:                            # noqa: BLE001
        return None, f"{what} raised {type(exc).__name__}"


async def _uploads(ch: str, now: datetime) -> dict:
    found, why = await _guard(api.videos(ch, UPLOAD_PAGE), "search.list")
    tried = datetime.now(UTC)
    if why is not None or found is None:
        return {"count": None, "capped": False, "last_public": None,
                "read_at": tried, "reason": why or "no answer"}
    since = now - timedelta(days=INACTIVE_DAYS)
    public = []
    for v in found:
        try:
            at = datetime.fromisoformat(str(v.get("published_at")))
        except ValueError:
            continue
        if v.get("privacy") == "public":
            public.append(at)
    recent = [a for a in public if a >= since]
    oldest = min(public, default=None)
    capped = len(found) >= UPLOAD_PAGE and oldest is not None and oldest >= since
    read_at = found[0].get("read_at") if found else tried
    return {"count": len(recent), "capped": capped,
            "last_public": max(public, default=None), "read_at": read_at,
            "reason": None}


async def ypp_progress(now: datetime | None = None) -> dict:
    """{"subscribers", "watch_hours", "shorts_views": Figure, "uploads": dict}.
    Read-only; never raises for a source that cannot be read."""
    from .tutorials import channel
    now = now or datetime.now(UTC)
    ch = channel()
    today = now.date()

    # --- subscribers: the Data API, whatever the analytics scope says
    stats, why = await _guard(api.channel_stats(ch), "channels.list")
    subs_at = (stats or {}).get("read_at") or datetime.now(UTC)
    subs_value = (stats or {}).get("subscribers")
    if why is None and subs_value is None:
        why = ("the channel hides its subscriber count"
               if (stats or {}).get("hidden_subscriber_count")
               else "channels.list returned no subscriber count")
    subs = Figure("Subscribers", SUBSCRIBERS, subs_value if why is None else None,
                  subs_at, why, None)

    hours = Figure(f"Long-form watch hours, last {HOURS_WINDOW} days", WATCH_HOURS,
                   None, None, None, None, window_days=HOURS_WINDOW, decimals=1)
    shorts = Figure(f"Shorts views, last {SHORTS_WINDOW} days", SHORTS_VIEWS,
                    None, None, None, None, window_days=SHORTS_WINDOW)

    granted, scope_why = await _guard(api.token_scopes(ch), "tokeninfo")
    gap = _scope_gap(granted if scope_why is None else None)
    if gap is not None:
        tried = datetime.now(UTC)
        for f in (hours, shorts):
            f.read_at, f.reason, f.rate_reason = tried, f"{ch}: {gap}", gap
        subs.rate_reason = gap
    else:
        await _read_content(ch, today, hours, shorts)
        await _read_subscriber_rate(ch, today, subs)

    return {"subscribers": subs, "watch_hours": hours, "shorts_views": shorts,
            "uploads": await _uploads(ch, now)}


async def _read_content(ch: str, today: Any, hours: Figure, shorts: Figure) -> None:
    start = today - timedelta(days=HOURS_WINDOW - 1)
    report, why = await _guard(api.channel_report(
        ch, start=start, end=today, metrics="views,estimatedMinutesWatched",
        dimensions="day,creatorContentType"), "reports.query")
    at = (report or {}).get("read_at") or datetime.now(UTC)
    hours.read_at = shorts.read_at = at
    if why is not None or report is None:
        hours.reason = shorts.reason = why or "no answer"
        hours.rate_reason = shorts.rate_reason = hours.reason
        return
    rows = report["rows"]
    pace_since = (today - timedelta(days=PACE_DAYS - 1)).isoformat()
    minutes = _sum(rows, "estimatedMinutesWatched", start.isoformat(), "VIDEO_ON_DEMAND")
    recent = _sum(rows, "estimatedMinutesWatched", pace_since, "VIDEO_ON_DEMAND")
    if minutes is None:
        hours.reason = "a long-form row came back without estimatedMinutesWatched"
    else:
        hours.value = minutes / 60
    if recent is None:
        hours.rate_reason = "a recent long-form row had no minutes"
    else:
        hours.rate_per_day = recent / 60 / PACE_DAYS
    views = _sum(rows, "views",
                 (today - timedelta(days=SHORTS_WINDOW - 1)).isoformat(), "SHORTS")
    recent = _sum(rows, "views", pace_since, "SHORTS")
    if views is None:
        shorts.reason = "a Shorts row came back without views"
    else:
        shorts.value = views
    if recent is None:
        shorts.rate_reason = "a recent Shorts row had no views"
    else:
        shorts.rate_per_day = recent / PACE_DAYS


async def _read_subscriber_rate(ch: str, today: Any, subs: Figure) -> None:
    start = today - timedelta(days=PACE_DAYS - 1)
    report, why = await _guard(api.channel_report(
        ch, start=start, end=today, metrics="subscribersGained,subscribersLost",
        dimensions="day"), "reports.query")
    if why is not None or report is None:
        subs.rate_reason = why or "no answer"
        return
    gained = _sum(report["rows"], "subscribersGained", start.isoformat())
    lost = _sum(report["rows"], "subscribersLost", start.isoformat())
    if gained is None or lost is None:
        subs.rate_reason = "a day came back without subscribersGained/Lost"
        return
    subs.rate_per_day = (gained - lost) / PACE_DAYS


def _when(at: datetime | None, read: bool) -> str:
    if not isinstance(at, datetime):
        return "read time UNKNOWN"
    return f"{'read' if read else 'tried'} {at:%a %H:%M} UTC"


def _line(f: Figure, now: datetime) -> str:
    if f.value is None:
        head = (f"• {f.name}: UNKNOWN / {_num(f.target)} ({_when(f.read_at, False)}). "
                f"Not read: {f.reason}.")
    else:
        head = (f"• {f.name}: {_num(f.value, f.decimals)} / {_num(f.target)} "
                f"({percent(f):.1f}%), {_when(f.read_at, True)}.")
    return f"{head} Pace: {pace(f, now)}."


def _uploads_line(up: dict) -> str:
    if up.get("count") is None:
        return (f"• Uploads, last 6 months: UNKNOWN ({_when(up.get('read_at'), False)}). "
                f"Not read: {up.get('reason')}.")
    n = f"{UPLOAD_PAGE}+" if up["capped"] else str(up["count"])
    last = up.get("last_public")
    last_txt = f"last {last:%d %b %Y}" if last else "no public upload read"
    warn = " Monetisation can be turned off after 6 months with no upload." \
        if up["count"] == 0 else ""
    return (f"• Uploads, last 6 months: {n} public ({last_txt}), "
            f"{_when(up.get('read_at'), True)}.{warn}")


def render(got: dict, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    lines = [TITLE, BAR]
    lines += [_line(got[k], now) for k in ("subscribers", "watch_hours", "shorts_views")]
    lines.append(_uploads_line(got["uploads"]))
    lines += [NOTE, SHOPPING]
    return "\n".join(lines)


async def section(now: datetime | None = None) -> str:
    """The digest's section; never raises."""
    try:
        return render(await ypp_progress(now), now)
    except Exception as exc:                            # noqa: BLE001
        return (f"{TITLE}\nThe Partner Program figures could not be read "
                f"({type(exc).__name__}), so they are UNKNOWN, not zero.\n{SHOPPING}")
