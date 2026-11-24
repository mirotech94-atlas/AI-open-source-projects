"""Money per reel: the Analyst and the Improver.

Stages 6 and 7 of the owner's plan (vault: Plans/Money per reel.md, 2026-09-28).
They pass work through ATLAS's own records -- `reel_mix_log` holds the
Analyst's table and the Improver's decision -- never through free text.

**The Analyst** (weekly, read-only). For each reel type (`shop.REEL_TYPES`,
decided in code from a post's draft by `shop.reel_type_of`), over ONE window:

  * views: from `reel_metrics`, the views each posted reel gained inside the
    window (its latest reading, less its last reading before the window when
    it was posted earlier);
  * sales: Stripe's paid checkouts per product (`shop.sales_since`), summed
    per reel type through `shop_products.reel_type`;
  * revenue per 1,000 views = revenue / views x 1000.

The window ends at the last reel-metrics sweep, so every reel in it has had
the chance to be read, and starts `days` before it; Stripe is read over
exactly that window. A reel type is RANKED only when both halves were read
and it has views. Otherwise it is:

  * UNKNOWN -- an input could not be read: Stripe, a reel's views, a post
    that cannot be tied to its type. Never zero (invariant 6).
  * unranked -- there is nothing to compare: no product on sale through a
    Stripe payment link, or no views of that type in the window.

Followers gained per reel: the stories Instagram account's follower count
(the `instagram.stories_token` check) at both ends of the window, over the
Instagram reels posted in it. It is an account figure; it is given to one
reel type only when every Instagram reel in the window was that type.
PostHog's pageviews over the same window are context, never ranked: they are
per site, not per reel type.

**The Improver** (weekly, after the Analyst). It moves reels from the worst
earner to the best: top +d, bottom -d, with d = min(20% of each, bottom - 1)
-- within +-20% a week, nothing below one reel a week, and the total the
same: more reels would be more Veo clips, and the Improver never spends.
Unranked and UNKNOWN types keep their allotment exactly (an unknown is not a
zero). Fewer than two ranked types, or a tie, and nothing changes -- the
reason is recorded and reported. It writes DATA only: `reel_mix` for next
week, a `reel_mix_log` row with the numbers it was based on, and an audit
entry. Never tiers, code or spending. Once per week: a second run finds the
log row and changes nothing.

**Who reads the mix.** `content.calendar.scheduled()` interleaves packaged
stories and business lessons in the mix's stories:business ratio -- two to
one, today's order, while there is no mix. Tech and travel reels have no
producer yet, so their rows are targets nothing drafts; the report says so.

Nothing here asks a model for anything.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

log = logging.getLogger("atlas.reel_money")

STEP = 0.20                  # the most a type's share moves in a week, either way
FLOOR = 1.0                  # reels a week no type drops below
FRESH = timedelta(hours=36)  # a reading this far from a window edge is not of it
STORIES_PER_LESSON = 2.0     # today's calendar order: two stories, one lesson
SCHEDULED = ("stories", "business")   # the types a scheduler reads the mix for


def _types() -> tuple[str, ...]:
    from ..modules.shop import REEL_TYPES
    return REEL_TYPES


def _utc(at: datetime | None) -> str:
    return f"{at.astimezone(UTC):%a %d %b %H:%M} UTC" if at else "never"


def next_week_start(now: datetime) -> date:
    """The Monday the Improver's decision takes effect: next week's."""
    local = now.astimezone().date()
    return local - timedelta(days=local.weekday()) + timedelta(days=7)


# ------------------------------------------------------------ the Analyst

@dataclass
class TypeRow:
    reel_type: str
    state: str = "unranked"          # ranked | unknown | unranked
    why: str = ""
    views: float = 0.0
    views_problems: list[str] = field(default_factory=list)
    oldest_read: datetime | None = None
    reels: int = 0                   # distinct reels posted inside the window
    revenue_cents: int | None = None
    sales: int | None = None
    sales_problem: str | None = None
    not_on_sale: str | None = None
    followers_per_reel: float | None = None

    @property
    def rpm(self) -> float | None:
        """US dollars per 1,000 views, only for a ranked type."""
        if self.state != "ranked" or self.revenue_cents is None or not self.views:
            return None
        return self.revenue_cents / 100 / self.views * 1000

    def as_json(self) -> dict[str, Any]:
        return {"state": self.state, "why": self.why, "views": self.views,
                "reels": self.reels, "revenue_cents": self.revenue_cents,
                "sales": self.sales, "rpm": self.rpm,
                "oldest_read": self.oldest_read.isoformat() if self.oldest_read
                else None, "followers_per_reel": self.followers_per_reel}


@dataclass
class Table:
    since: datetime
    until: datetime
    rows: dict[str, TypeRow]
    sales_read_at: datetime | None = None
    followers: str = ""
    traffic: str = ""
    unattributed: int = 0
    views_read: bool = True          # the window ends at a reel-views read

    def ranked(self) -> list[TypeRow]:
        return [r for r in self.rows.values() if r.state == "ranked"]

    def as_json(self) -> dict[str, Any]:
        return {"since": self.since.isoformat(), "until": self.until.isoformat(),
                "sales_read_at": self.sales_read_at.isoformat()
                if self.sales_read_at else None,
                "rows": {k: r.as_json() for k, r in self.rows.items()},
                "followers": self.followers, "traffic": self.traffic,
                "unattributed": self.unattributed}


async def _window_end(now: datetime) -> tuple[datetime, bool]:
    """The last reel-views read, or now (False) when there has been none."""
    from .state import fetch_all
    rows = await fetch_all("SELECT max(at) AS at FROM reel_metrics WHERE at <= $1", now)
    at = rows[0]["at"] if rows else None
    return (at, True) if at else (now, False)


def _latest(readings: list[tuple[datetime, float | None]],
            at_or_before: datetime) -> tuple[datetime, float | None] | None:
    best = None
    for when, views in readings:
        if when <= at_or_before and (best is None or when > best[0]):
            best = (when, views)
    return best


async def _views(table: Table) -> list[dict[str, Any]]:
    """Fill each row's views from reel_metrics; returns the posts read, for
    the followers line."""
    from ..modules.content.metrics import WINDOW_DAYS
    from ..modules.shop import reel_type_of
    from .state import fetch_all
    since, until = table.since, table.until
    # Only reels metrics still re-reads: a reel past WINDOW_DAYS is no
    # longer read, so its last reading would look stale forever.
    earliest = until - timedelta(days=WINDOW_DAYS)
    posts = await fetch_all(
        """SELECT p.platform, p.post_ref, p.at AS posted_at, p.media_id,
                  d.source, d.kind
             FROM reel_posts p
             LEFT JOIN content_media m ON m.id = p.media_id
             LEFT JOIN content_drafts d ON d.id = m.draft_id
            WHERE p.at > $1 AND p.at <= $2
              AND p.platform <> 'youtube'""", earliest, until)
    reads = await fetch_all(
        """SELECT platform, post_ref, at, views FROM reel_metrics
            WHERE at > $1 AND at <= $2""", earliest, until)
    by_post: dict[tuple[str, str], list[tuple[datetime, float | None]]] = {}
    for r in reads:
        views = None if r["views"] is None else float(r["views"])
        by_post.setdefault((str(r["platform"]), str(r["post_ref"])), []).append(
            (r["at"], views))
    reels: dict[str, set[str]] = {}
    out: list[dict[str, Any]] = []
    for p in posts:
        platform, ref = str(p["platform"]), str(p["post_ref"])
        if p["source"] is None:
            # Not tied to a draft: its type cannot be read, and it is never
            # quietly counted as "stories" (reel_type_of's fallback).
            table.unattributed += 1
            out.append({**dict(p), "reel_type": None})
            continue
        rtype = reel_type_of(str(p["source"]), str(p["kind"] or ""))
        out.append({**dict(p), "reel_type": rtype})
        row = table.rows.get(rtype)
        if row is None:
            continue
        readings = by_post.get((platform, ref), [])
        end = _latest(readings, until)
        if end is None or end[1] is None or until - end[0] > FRESH:
            row.views_problems.append(f"{platform} {ref} was not read")
            continue
        start = 0.0
        if p["posted_at"] <= since:
            base = _latest(readings, since)
            if base is None or base[1] is None or since - base[0] > FRESH:
                row.views_problems.append(
                    f"{platform} {ref} was not read at the window's start")
                continue
            start = base[1]
        gained = end[1] - start
        if gained < 0:
            row.views_problems.append(f"{platform} {ref}'s views went down "
                                      f"(a platform recount)")
            continue
        row.views += gained
        if row.oldest_read is None or end[0] < row.oldest_read:
            row.oldest_read = end[0]
        if p["posted_at"] > since:
            reels.setdefault(rtype, set()).add(str(p["media_id"] or f"{platform}:{ref}"))
    for rtype, media in reels.items():
        table.rows[rtype].reels = len(media)
    return out


async def _sales(table: Table) -> None:
    """Fill each row's revenue from Stripe, or say why it can't be."""
    from ..modules import shop
    from .state import fetch_all
    products = await fetch_all(
        """SELECT key, name, reel_type, status, payment_link_id, payment_link_url
             FROM shop_products""")
    readable: dict[str, list[str]] = {}
    for rtype, row in table.rows.items():
        mine = [p for p in products if str(p["reel_type"]) == rtype]
        linked = [p for p in mine if p["payment_link_id"]]
        elsewhere = [p for p in mine if not p["payment_link_id"]
                     and p["status"] == "live" and p["payment_link_url"]]
        if elsewhere:
            row.sales_problem = (f"{elsewhere[0]['name']} sells through its own page, "
                                 f"not a Stripe payment link ATLAS reads")
        elif linked:
            readable[rtype] = [str(p["key"]) for p in linked]
        elif mine:
            row.not_on_sale = f"{mine[0]['name']} is not on sale yet"
        else:
            row.not_on_sale = "no product" + (
                ": its reels build the audience the others sell to"
                if rtype == "stories" else " for this reel type")
    if not readable:
        return
    try:
        sales, why = await shop.sales_since(since=table.since, until=table.until)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        sales, why = {}, explain(exc)
    table.sales_read_at = datetime.now(UTC)
    for rtype, keys in readable.items():
        row = table.rows[rtype]
        if why:
            row.sales_problem = f"Stripe could not be read: {why}"
        elif any(k not in sales for k in keys):
            row.sales_problem = "Stripe did not answer for every product"
        else:
            row.sales = sum(int(sales[k]["sales"]) for k in keys)
            row.revenue_cents = sum(int(sales[k]["revenue_cents"]) for k in keys)


async def _followers(table: Table, posts: list[dict[str, Any]]) -> None:
    from .state import fetch_all
    since, until = table.since, table.until
    rows = await fetch_all(
        """SELECT o.at, o.value FROM observations o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id
            WHERE p.name = 'instagram' AND c.name = 'stories_token'
              AND o.value IS NOT NULL AND o.at > $1 AND o.at <= $2""",
        since - FRESH, until)
    counts = [(r["at"], float(r["value"])) for r in rows]
    start, end = _latest(counts, since), _latest(counts, until)  # type: ignore[arg-type]
    if start is None or end is None or until - end[0] > FRESH:
        table.followers = ("Followers gained per reel: not available — the stories "
                           "account's follower count was not read at both ends of "
                           "the window")
        return
    ig = [p for p in posts if p["platform"] == "instagram" and p["posted_at"] > since]
    gained = (end[1] or 0) - (start[1] or 0)
    said = (f"the stories account went from {start[1]:.0f} followers "
            f"(read {_utc(start[0])}) to {end[1]:.0f} (read {_utc(end[0])})")
    if not ig:
        table.followers = f"Followers: {said}, with no Instagram reel posted in the window."
        return
    per = gained / len(ig)
    types = {p["reel_type"] for p in ig}
    table.followers = (f"Followers: {said}: {gained:+.0f} over {len(ig)} Instagram "
                       f"reel(s), {per:+.2f} per reel")
    if len(types) == 1 and None not in types:
        (only,) = types
        table.rows[only].followers_per_reel = per
    else:
        table.followers += " (the reels were of several types, so it is not split)"
    table.followers += "."


async def _traffic(table: Table) -> None:
    from ..modules import posthog
    try:
        got, why = await posthog.pageviews_between(table.since, table.until)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        got, why = None, explain(exc)
    at = datetime.now(UTC)
    if got is None:
        table.traffic = f"Site traffic (PostHog): UNKNOWN — {why}."
        return
    sites = ", ".join(f"{h or '?'} {n}" for h, n in got["sites"])
    table.traffic = (f"Site traffic (PostHog, read {_utc(at)}): {got['views']} "
                     f"pageviews from {got['visitors']} visitors"
                     + (f" ({sites})" if sites else "")
                     + ". Per site, not per reel type, so it is not ranked.")


async def analyse(now: datetime | None = None, days: int = 7) -> Table:
    """The week's table, read fresh. Every part that fails says so."""
    now = now or datetime.now(UTC)
    rows = {t: TypeRow(t) for t in _types()}
    try:
        until, read = await _window_end(now)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        until, read = now, False
        for row in rows.values():
            row.views_problems.append(f"reel_metrics unreadable: {explain(exc)}")
    table = Table(since=until - timedelta(days=days), until=until, rows=rows,
                  views_read=read)
    posts: list[dict[str, Any]] = []
    try:
        posts = await _views(table)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        for row in rows.values():
            row.views_problems.append(f"reel views unreadable: {explain(exc)}")
    try:
        await _sales(table)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        for row in rows.values():
            row.sales_problem = f"the shop is unreadable: {explain(exc)}"
    try:
        await _followers(table, posts)
    except Exception as exc:                            # noqa: BLE001
        from ..modules._http import explain
        table.followers = f"Followers gained per reel: not available — {explain(exc)}"
    await _traffic(table)
    for row in rows.values():
        _classify(row, table.unattributed)
    return table


def _classify(row: TypeRow, unattributed: int) -> None:
    unknown = []
    if row.views_problems:
        unknown.append("; ".join(row.views_problems[:3]))
    if unattributed:
        unknown.append(f"{unattributed} post(s) could not be tied to a reel type, "
                       f"so every type's views may be short")
    if row.sales_problem:
        unknown.append(row.sales_problem)
    if unknown:
        row.state, row.why = "unknown", "; ".join(unknown)
    elif row.not_on_sale:
        row.state, row.why = "unranked", row.not_on_sale
    elif not row.views:
        row.state, row.why = "unranked", "no views of this type in the window"
    else:
        row.state, row.why = "ranked", ""


# ------------------------------------------------------------ the Improver

@dataclass
class Decision:
    week_start: date
    changed: bool
    reason: str
    before: dict[str, float]
    after: dict[str, float]
    at: datetime | None = None
    earlier: bool = False           # decided by an earlier run this week
    audit_problem: str | None = None


def decide(before: dict[str, float], table: Table) -> tuple[bool, str, dict[str, float]]:
    """Pure: next week's mix from this week's and the table. The total, and
    every type that is not ranked, stay exactly as they were."""
    after = dict(before)
    ranked = sorted(table.ranked(), key=lambda r: (-(r.rpm or 0.0), r.reel_type))
    if len(ranked) < 2:
        names = ", ".join(r.reel_type for r in ranked) or "none"
        return False, (f"only {len(ranked)} reel type could be ranked ({names}); "
                       f"it takes two to compare, so the mix stays as it is"), after
    top, bottom = ranked[0], ranked[-1]
    if top.rpm == ranked[1].rpm or bottom.rpm == ranked[-2].rpm:
        return False, ("a tie: there is no single best and worst earner, so the "
                       "mix stays as it is"), after
    b_top = before.get(top.reel_type, FLOOR)
    b_bottom = before.get(bottom.reel_type, FLOOR)
    delta = min(STEP * b_top, STEP * b_bottom, b_bottom - FLOOR)
    delta = math.floor(delta * 100) / 100
    if delta <= 0:
        return False, (f"{bottom.reel_type} earns least but is already at the floor "
                       f"of one reel a week, so the mix stays as it is"), after
    after[top.reel_type] = round(b_top + delta, 2)
    after[bottom.reel_type] = round(b_bottom - delta, 2)
    return True, (f"{top.reel_type} earned ${top.rpm:.2f} per 1,000 views and "
                  f"{bottom.reel_type} ${bottom.rpm:.2f}: {delta:.2f} reel(s) a week "
                  f"move from {bottom.reel_type} to {top.reel_type}"), after


async def current_mix(on: date) -> dict[str, float] | None:
    """The mix in force on `on`: the latest week that started by then."""
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT reel_type, per_week FROM reel_mix
            WHERE week_start = (SELECT max(week_start) FROM reel_mix
                                 WHERE week_start <= $1::date)""", on)
    return {str(r["reel_type"]): float(r["per_week"]) for r in rows} or None


async def stories_per_lesson(today: date | None = None,
                             default: float = STORIES_PER_LESSON) -> float:
    """The calendar's stories:business ratio from the mix; `default` (today's
    two to one) when there is no mix or it cannot be read."""
    try:
        mix = await current_mix(today or datetime.now().astimezone().date())
    except Exception as exc:                            # noqa: BLE001
        log.warning("reel_mix unreadable, keeping %s stories a lesson: %s",
                    default, type(exc).__name__)
        return default
    stories, lessons = (mix or {}).get("stories"), (mix or {}).get("business")
    if not stories or not lessons or stories <= 0 or lessons <= 0:
        return default
    ratio = stories / lessons
    return ratio if math.isfinite(ratio) else default


def _as_dict(value: Any) -> dict[str, float]:
    if isinstance(value, str):
        value = json.loads(value)
    return {str(k): float(v) for k, v in (value or {}).items()}


async def recorded(week_start: date) -> Decision | None:
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT at, changed, reason, before, after FROM reel_mix_log
            WHERE week_start = $1::date""", week_start)
    if not rows:
        return None
    r = rows[0]
    return Decision(week_start, bool(r["changed"]), str(r["reason"]),
                    _as_dict(r["before"]), _as_dict(r["after"]), r["at"], earlier=True)


async def _before(target: date, table: Table) -> tuple[dict[str, float], str]:
    mix = await current_mix(target)
    if mix:
        return ({t: max(FLOOR, mix.get(t, FLOOR)) for t in _types()},
                "the mix in force")
    # No mix yet: what was actually posted in the window, never below the
    # floor -- except stories, held at today's two per business lesson, so the
    # calendar's order moves only when the Improver moves one of the two.
    before = {t: max(FLOOR, float(table.rows[t].reels)) for t in _types()}
    if "stories" in before and "business" in before:
        before["stories"] = STORIES_PER_LESSON * before["business"]
    return (before, ("no mix yet: the reels posted in the window, stories at "
                     "two per business lesson"))


async def improve(table: Table, now: datetime | None = None) -> Decision:
    """Decide next week's mix once, record it with its numbers, audit it."""
    from .audit import audit
    from .state import fetch_all
    now = now or datetime.now(UTC)
    target = next_week_start(now)
    if (prior := await recorded(target)) is not None:
        return prior
    before, basis = await _before(target, table)
    changed, reason, after = decide(before, table)
    based_on = {**table.as_json(), "baseline": basis}
    types = sorted(after) if changed else []
    rows = await fetch_all(
        """WITH claim AS (
               INSERT INTO reel_mix_log (week_start, changed, reason, before, after,
                                         based_on)
               VALUES ($1::date, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb)
               ON CONFLICT (week_start) DO NOTHING
               RETURNING week_start),
           mix AS (
               INSERT INTO reel_mix (week_start, reel_type, per_week)
               SELECT claim.week_start, t.reel_type, t.per_week
                 FROM claim, unnest($7::text[], $8::real[]) AS t(reel_type, per_week)
               RETURNING reel_type)
           SELECT (SELECT count(*) FROM claim) AS claimed,
                  (SELECT count(*) FROM mix) AS written""",
        target, changed, reason, json.dumps(before), json.dumps(after),
        json.dumps(based_on, default=str), types, [after[t] for t in types])
    if not rows or not int(rows[0]["claimed"]):
        # Another run got there first: its decision stands, this one does not.
        prior = await recorded(target)
        if prior is not None:
            return prior
        raise RuntimeError("the mix decision was neither recorded nor found")
    decision = Decision(target, changed, reason, before, after, now)
    try:
        await audit("reel_mix.decided", f"week of {target.isoformat()}",
                    {"changed": changed, "reason": reason, "before": before,
                     "after": after, "based_on": based_on})
    except Exception as exc:
        # The decision is recorded (reel_mix_log) and in force either way;
        # the report must not say "nothing changed" over a failed audit.
        from ..modules._http import explain
        log.exception("reel_money: the audit entry failed")
        decision.audit_problem = explain(exc)
    return decision


# ------------------------------------------------------------ the report

@dataclass
class Week:
    table: Table
    decision: Decision | None
    problem: str | None = None


async def week(now: datetime | None = None, days: int = 7) -> Week:
    """The Analyst, then the Improver: what the weekly summary runs."""
    table = await analyse(now, days)
    try:
        return Week(table, await improve(table, now))
    except Exception as exc:
        from ..modules._http import explain
        log.exception("reel_money: the Improver could not record its decision")
        return Week(table, None, f"the Improver could not record its decision: "
                                 f"{explain(exc)}, so nothing changed")


def _money(cents: int | None) -> str:
    return "UNKNOWN" if cents is None else f"${cents / 100:,.2f}"


def _row_line(r: TypeRow) -> str:
    views = (f"{r.views:,.0f} views (as read by {_utc(r.oldest_read)}), "
             f"{r.reels} reel(s) posted")
    if r.state == "ranked":
        line = (f"• {r.reel_type}: ${r.rpm:.2f} per 1,000 views — "
                f"{_money(r.revenue_cents)} from {r.sales} sale(s) over {views}")
    elif r.state == "unknown":
        line = f"• {r.reel_type}: UNKNOWN, not ranked — {r.why}"
    else:
        line = f"• {r.reel_type}: not ranked — {r.why}"
        if r.views:
            line += f" ({views})"
    if r.followers_per_reel is not None:
        line += f"; {r.followers_per_reel:+.2f} followers per reel"
    return line


def _mix_line(mix: dict[str, float]) -> str:
    return ", ".join(f"{t} {mix[t]:.2f}" for t in sorted(mix)) + " reels a week"


def render(w: Week) -> str:
    t = w.table
    head = (f"*Money per reel* — {_utc(t.since)} to {_utc(t.until)} ("
            + ("the window ends at the last reel-views read" if t.views_read
               else "no reel views have been read yet, so the window ends now")
            + (f"; Stripe read {_utc(t.sales_read_at)}" if t.sales_read_at else "")
            + ")")
    from ..modules.content.metrics import WINDOW_DAYS
    lines = [head, *(_row_line(r) for r in t.rows.values())]
    lines.append(f"Views are those gained inside the window; reels older than "
                 f"{WINDOW_DAYS} days are no longer read, so their views are not "
                 f"counted.")
    if t.followers:
        lines.append(t.followers)
    if t.traffic:
        lines.append(t.traffic)
    d = w.decision
    if d is None:
        lines.append("*Next week's mix*: "
                     + (w.problem or "the Improver has not run for next week yet")
                     + ".")
        return "\n".join(lines)
    when = f"from Mon {d.week_start:%-d %b}"
    decided = f" (decided {_utc(d.at)})" if d.earlier and d.at else ""
    if d.changed:
        moved = [f"{k} {d.before[k]:.2f} -> {d.after[k]:.2f}"
                 for k in sorted(d.after) if d.after[k] != d.before.get(k)]
        lines.append(f"*Next week's mix* ({when}){decided}: changed — "
                     f"{', '.join(moved)} reels a week. {d.reason}.")
        lines.append(f"The whole mix: {_mix_line(d.after)}.")
    else:
        lines.append(f"*Next week's mix* ({when}){decided}: no change — {d.reason}.")
    if d.audit_problem:
        lines.append(f"The decision is recorded, but its audit entry failed: "
                     f"{d.audit_problem}.")
    lines.append("Only the stories-to-business ratio is used, to order the reel "
                 "calendar; no count is enforced, and the daily stories rotation "
                 "makes as many reels as before (more would be more renders).")
    idle = [x for x in sorted(d.after) if x not in SCHEDULED]
    if idle:
        lines.append(f"Nothing drafts {' or '.join(idle)} reels yet, so their numbers "
                     f"are targets, not a schedule.")
    return "\n".join(lines)


async def section(w: Week | None = None, now: datetime | None = None,
                  days: int = 7) -> str:
    """The weekly summary's paragraph. Without a `Week` it reads the table
    fresh and shows the decision already on record -- it never decides."""
    if w is None:
        now = now or datetime.now(UTC)
        table = await analyse(now, days)
        try:
            decision = await recorded(next_week_start(now))
        except Exception as exc:                        # noqa: BLE001
            from ..modules._http import explain
            return render(Week(table, None, f"the mix record is unreadable: "
                                            f"{explain(exc)}"))
        w = Week(table, decision)
    return render(w)
