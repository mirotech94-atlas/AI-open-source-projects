"""What each posted AI-stories reel did, read once a day, so the playbook
learns which formats work.

`check_reel_metrics` (a Check, daily) reads every reel posted in the last
`WINDOW_DAYS` (`reel_posts`, written by `content.publish_reel`):

  * **Instagram**: `GET /{media-id}/insights?metric=views,reach,likes,
    comments,shares,saved,total_interactions` with the stories account's own
    token (`instagram_manage_insights`, granted 2026-09-25). Without the
    permission that part is UNKNOWN and the check says so. `views` is the
    current name; "plays" was deprecated.
  * **TikTok**: Postiz's post analytics (`postiz.post_metrics`). A post
    whose TikTok id is still missing is reported unread ("connect it by
    hand"); this check never connects one -- a check only reads.

Each read is one `reel_metrics` row with the time it was read. A metric
that was not given is NULL -- never 0 -- and engagement is computed only
when views, likes, comments and shares were all read. `score_bonus` turns
the latest reading of each post into a recency-weighted bonus per format
for `reels.top_formats`: a post past `VIEWS_BAR` views, or past
`ENGAGEMENT_BAR` engagement, earns its format a point, halved every
`HALF_LIFE_DAYS` from when the reel was POSTED (`reel_posts.at`) -- not
from when it was last read, which is always today while a post is in the
read window and would make a month-old reel score like yesterday's. An
unread number earns nothing and costs nothing.

Nothing here reads Instagram through Miro's logged-in account; only the
Graph API with the account's own token.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from ...kernel.contracts import Health, Observation
from ...kernel.state import execute_sql, fetch_all

WINDOW_DAYS = 30             # how long a posted reel is re-read
SCORE_DAYS = 120             # how far back results still count (decayed)
VIEWS_BAR = 10_000
ENGAGEMENT_BAR = 0.06        # (likes + comments + shares) / views
HALF_LIFE_DAYS = 14.0
# The check's number: reel posts (one per platform) in the read window, from
# `reel_posts` on this read. It was "posts read" until 2026-10-06; when every
# post is read -- the only time the check is OK -- the two are the same.
UNIT = f"reel posts, {WINDOW_DAYS} d"
# Media insights, Graph v21.0: `views` is the current name ("plays" was
# deprecated). The account-level names were verified live on 2026-09-25;
# these media-level names come from Meta's current reference and are read
# on the first posted reel. A metric Graph does not return stays None.
IG_METRICS = "views,reach,likes,comments,shares,saved,total_interactions"


def engagement(m: dict[str, float | None]) -> float | None:
    parts = [m.get(k) for k in ("views", "likes", "comments", "shares")]
    if any(p is None for p in parts) or not parts[0]:
        return None
    views, likes, comments, shares = parts
    return (likes + comments + shares) / views           # type: ignore[operator]


async def _instagram(media_id: str) -> tuple[dict[str, float | None] | None, str | None]:
    from .. import instagram
    try:
        data = await instagram._get(f"{media_id}/insights", account="stories",
                                    metric=IG_METRICS)
    except instagram.NoAccount as exc:
        return None, str(exc)
    except instagram.GraphError as exc:
        hint = (" — the stories token needs instagram_manage_insights"
                if exc.status_code in (400, 403) else "")
        return None, f"{exc}{hint}"
    except Exception as exc:                            # noqa: BLE001
        return None, instagram._why(exc)
    out: dict[str, float | None] = {k: None for k in IG_METRICS.split(",")}
    for metric in (data or {}).get("data") or []:
        name = metric.get("name") if isinstance(metric, dict) else None
        if name in out:
            out[name] = instagram.insight_value(metric)
    if all(v is None for v in out.values()):
        return None, f"media {media_id}: insights carried no readable metric"
    return out, None


async def check_reel_metrics() -> Observation:
    try:
        posts = await fetch_all(
            f"""SELECT platform, post_ref, format_id FROM reel_posts
                 WHERE at > now() - interval '{WINDOW_DAYS} days'
                   AND platform IN ('instagram', 'tiktok')""")
    except Exception as exc:                            # noqa: BLE001
        return Observation("reel_metrics", Health.UNKNOWN,
                           detail=f"reel_posts unreadable: {exc}")
    if not posts:
        return Observation("reel_metrics", Health.OK, value=0.0, unit=UNIT,
                           detail=f"no reels posted in the last {WINDOW_DAYS} days")
    from . import postiz
    # In parallel, four at a time: one Graph and one Postiz call per reel,
    # read in series, ran past the check's budget at 4-7 reels (2026-09-27).
    gate = asyncio.Semaphore(4)

    async def one(post):
        platform, ref = post["platform"], str(post["post_ref"])
        async with gate:
            if platform == "instagram":
                return post, await _instagram(ref)
            if platform == "tiktok":
                return post, await postiz.post_metrics(ref)
            return post, None

    read, unread = 0, []
    for post, result in await asyncio.gather(*(one(p) for p in posts)):
        if result is None:
            continue
        platform, ref = post["platform"], str(post["post_ref"])
        got, why = result
        if got is None:
            unread.append(f"{platform} {ref}: {why}")
            continue
        await execute_sql(
            """INSERT INTO reel_metrics (platform, post_ref, format_id, views,
                                         likes, comments, shares, engagement)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            platform, ref, post["format_id"], got.get("views"), got.get("likes"),
            got.get("comments"), got.get("shares"), engagement(got))
        read += 1
    at = datetime.now(UTC)
    detail = f"read {read} of {len(posts)} posted reel(s) (read {at:%H:%M} UTC)"
    if unread:
        detail += "; not read — " + "; ".join(unread[:5])
        return Observation("reel_metrics", Health.UNKNOWN, value=float(len(posts)),
                           unit=UNIT, detail=detail,
                           evidence={"read": read, "unread": len(unread)})
    return Observation("reel_metrics", Health.OK, value=float(len(posts)),
                       unit=UNIT, detail=detail, evidence={"read": read, "unread": 0})


def bonus(rows: list[dict[str, Any]], now: datetime | None = None) -> dict[int, float]:
    """Recency-weighted points per format from each post's latest reading,
    decayed by `posted_at`. A None metric never scores and never penalises."""
    now = now or datetime.now(UTC)
    out: dict[int, float] = {}
    for row in rows:
        fid = row.get("format_id")
        at = row.get("posted_at")
        if fid is None or at is None:
            continue
        points = 0.0
        views, eng = row.get("views"), row.get("engagement")
        if views is not None and views >= VIEWS_BAR:
            points += 1.0
        if eng is not None and eng >= ENGAGEMENT_BAR:
            points += 1.0
        if points:
            age = max(0.0, (now - at).total_seconds() / 86400)
            out[int(fid)] = out.get(int(fid), 0.0) + points * 0.5 ** (age / HALF_LIFE_DAYS)
    return out


async def score_bonus() -> dict[int, float]:
    rows = await fetch_all(
        f"""SELECT DISTINCT ON (m.platform, m.post_ref)
                   m.format_id, m.views, m.engagement, p.at AS posted_at
              FROM reel_metrics m
              JOIN reel_posts p
                ON p.platform = m.platform AND p.post_ref = m.post_ref
             WHERE p.at > now() - interval '{SCORE_DAYS} days'
             ORDER BY m.platform, m.post_ref, m.at DESC""")
    return bonus([dict(r) for r in rows])
