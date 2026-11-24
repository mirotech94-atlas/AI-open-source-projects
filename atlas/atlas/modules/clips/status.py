"""`clips.status`: per campaign, what is waiting, registered, cut today,
carded, posted and claimed, its rates, and the budget left when it is
actually known -- every figure read now, and said with that time.

Read-only. UNKNOWN -- never OK -- when the counts cannot be read, when no
campaign is on file, when a campaign's footage folder cannot be read, when
an active campaign's budget left is not known (ATLAS cannot read the Whop
dashboard, so a budget is only as good as the owner's last reading of it,
quoted with its date, and past `BUDGET_FRESH` it is unknown, not assumed),
or when the unattended intake (`kernel.clip_intake`) has not run lately,
last failed, or gave up on a source -- each said with its reason.

Rates are the ones stated in the campaign's reviewed rules doc, quoted with
the day it was reviewed; they have no freshness rule of their own.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel import privacy
from ...kernel.contracts import Health, Observation
from ...kernel.state import fetch_all
from . import campaigns, intake, sources

BUDGET_FRESH = timedelta(days=7)
# The intake's last word is stale after this many of its intervals.
INTAKE_STALE_TICKS = 4


def rate_line(c: campaigns.Campaign) -> str:
    """The stated rates, with the day the doc stating them was reviewed."""
    if not c.rates:
        return "rates not stated"
    rates = ", ".join(f"${float(v):.2f} on {k}" for k, v in sorted(c.rates.items()))
    when = (f"its rules doc, reviewed {c.rules_reviewed_on:%d %b %Y}"
            if c.rules_reviewed_on else "not from a reviewed rules doc")
    return f"pays {rates} per 1,000 views ({when})"


def _unregistered(slug: str, names: set[str], shas: set[str]) -> int:
    """Files in the footage folder not on file by name -- nor, when the
    intake already hashed them, by sha256. Blocking; hashes nothing."""
    import os
    count = 0
    for path in sources._listing(slug):
        if path.name in names:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        known = intake._HASHES.get((os.path.realpath(path), st.st_size,
                                    st.st_mtime_ns))
        if known is None or known not in shas:
            count += 1
    return count


def intake_problem(c: campaigns.Campaign, row: dict[str, Any] | None,
                   now: datetime) -> str | None:
    """Why the intake's word on this campaign is not good, or None."""
    if not intake.enabled() or intake.gate(c) is not None:
        return None
    if row is None:
        return f"{c.name}: the clips intake has not run yet"
    at = row["at"]
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    if now - at > intake.interval() * INTAKE_STALE_TICKS:
        return f"{c.name}: the clips intake last ran {at:%d %b %H:%M} UTC"
    if not row["ok"]:
        return f"{c.name}: intake {at:%H:%M} UTC: {row['detail']}"
    return None


def budget_line(c: campaigns.Campaign, now: datetime) -> tuple[str, bool]:
    """(words, known). A budget with no date, or an old one, is unknown."""
    if c.budget_usd is None or c.budget_read_at is None:
        return "budget left unknown (not set: clips.record_budget)", False
    read = c.budget_read_at
    if read.tzinfo is None:
        read = read.replace(tzinfo=UTC)
    if now - read > BUDGET_FRESH:
        return (f"budget left unknown (not set in the last {BUDGET_FRESH.days} "
                f"days: clips.record_budget; last known ${c.budget_usd:,.0f} on "
                f"{read:%d %b})"), False
    return (f"${c.budget_usd:,.0f} left as of {read:%d %b} "
            f"({c.budget_source or 'source not recorded'})"), True


def _past(at: Any, now: datetime, days: int) -> bool:
    """Was this registered at least `days` ago? False when unknown."""
    if not isinstance(at, datetime):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return now - at >= timedelta(days=days)


def _folder_state(slug: str) -> str:
    """'missing', 'ok', or 'unreadable'. Blocking."""
    folder = sources.source_dir(slug)
    try:
        if not folder.exists():
            return "missing"
        list(folder.iterdir())
    except OSError:
        return "unreadable"
    return "ok"


def _count(rows: list[Any], key: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        out.setdefault(str(r["campaign"]), {})[str(r[key])] = int(r["n"])
    return out


async def check_legs() -> Observation:
    """Which of the lane's posting accounts ATLAS can post to, from
    configuration alone. One it cannot reach is UNKNOWN, never OK: its
    clips still go to the owner, as hand-off cards (file and exact caption).
    The Postiz route to the clips YouTube channel is its own line, as the
    TikTok one is: unset, the check is UNKNOWN and says which env to set,
    even while the Data API could reach the channel."""
    from . import posting
    ready: list[str] = []
    missing: list[str] = []
    try:
        for platform, where in posting.ACCOUNTS.items():
            gap = posting.autopost_gap(platform)
            if gap is None:
                ready.append(f"{where}: configured")
            else:
                missing.append(f"{where}: {gap} -- its clips go out as hand-off "
                               f"cards")
        shorts = f"{posting.YT_CHANNEL_NAME} Shorts via Postiz"
        if (gap := posting.youtube_postiz_gap()) is None:
            ready.append(f"{shorts}: configured")
        else:
            missing.append(f"{shorts}: {gap}")
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return Observation("legs", Health.UNKNOWN,
                           detail=f"the clips accounts could not be read: "
                                  f"{explain(exc)}")
    detail = " · ".join(missing + ready) + (" (configuration only; each account's "
                                            "token is proven by its first post)")
    return Observation("legs", Health.UNKNOWN if missing else Health.OK,
                       float(len(ready)), "accounts", detail=detail,
                       evidence={"configured": len(ready),
                                 "of": len(posting.ACCOUNTS) + 1})


async def check_status() -> Observation:
    now = datetime.now(UTC)
    try:
        found = await campaigns.load_all()
        src = _count(await fetch_all(
            """SELECT campaign, status, count(*) AS n FROM clip_sources
               GROUP BY campaign, status"""), "status")
        # One row per clip per platform: a status is counted per row, a clip
        # made once however many platforms it posts on.
        out = _count(await fetch_all(
            """SELECT campaign, status, count(*) AS n FROM clip_outputs
               GROUP BY campaign, status"""), "status")
        files = {str(r["campaign"]): int(r["n"]) for r in await fetch_all(
            """SELECT campaign, count(DISTINCT filename) AS n FROM clip_outputs
               WHERE status <> 'rolled_back' GROUP BY campaign""")}
        today = {str(r["campaign"]): int(r["n"]) for r in await fetch_all(
            """SELECT campaign, count(DISTINCT filename) AS n FROM clip_outputs
               WHERE at >= $1::timestamptz AND status <> 'rolled_back'
               GROUP BY campaign""", intake.today_utc(now))}
        uncut = [dict(r) for r in await fetch_all(
            """SELECT s.campaign, s.id, s.filename, s.cut_attempts,
                      s.last_cut_detail, s.registered_at
               FROM clip_sources s
               WHERE s.status = 'registered' AND s.deleted_at IS NULL
                 AND NOT EXISTS (SELECT 1 FROM clip_outputs o
                                 WHERE o.source_id = s.id)
               ORDER BY s.id""")]
        on_file = [dict(r) for r in await fetch_all(
            "SELECT campaign, filename, sha256 FROM clip_sources")]
        # Footage the retention run removed once its clips were done with
        # (kernel.privacy.purge_clip_sources): done, never a problem.
        removed = [dict(r) for r in await fetch_all(
            """SELECT campaign, id, deleted_at FROM clip_sources
               WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC""")]
        cards = {str(r["campaign"]): int(r["n"]) for r in await fetch_all(
            """SELECT coalesce(p.params->>'campaign', o.campaign) AS campaign,
                      count(*) AS n
               FROM proposals p
               LEFT JOIN clip_outputs o
                 ON o.id = CASE WHEN p.params->>'clip_id' ~ '^[0-9]+$'
                                THEN (p.params->>'clip_id')::bigint END
               WHERE p.status = 'pending' AND p.expires_at > now()
                 AND p.action LIKE 'clips.%'
               GROUP BY 1""") if r["campaign"] is not None}
        ticks = {str(r["campaign"]): dict(r) for r in await fetch_all(
            "SELECT campaign, at, ok, detail FROM clip_intake")}
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return Observation("status", Health.UNKNOWN,
                           detail=f"clip campaigns could not be read: {explain(exc)}")
    if not found:
        return Observation("status", Health.UNKNOWN,
                           detail="no clip campaigns on file (seeds not applied)")
    unknown: list[str] = []
    parts: list[str] = []
    evidence: dict[str, Any] = {"read_at": now.isoformat(),
                                "daily_cap": intake.daily_cap()}
    total_made = 0
    for c in found:
        s = src.get(c.slug, {})
        o = out.get(c.slug, {})
        registered = s.get("registered", 0)
        made = files.get(c.slug, 0)
        posted = o.get("posted", 0) + o.get("submitted", 0)
        submitted = o.get("submitted", 0)
        mine = [r for r in uncut if r["campaign"] == c.slug]
        spent = [r for r in mine
                 if int(r.get("cut_attempts") or 0) >= intake.MAX_ATTEMPTS]
        waiting = len(mine) - len(spent)
        total_made += made
        budget, known = budget_line(c, now)
        folder = await asyncio.to_thread(_folder_state, c.slug)
        new = 0
        if folder == "ok":
            names = {str(r["filename"]) for r in on_file if r["campaign"] == c.slug}
            shas = {str(r["sha256"]) for r in on_file if r["campaign"] == c.slug}
            try:
                new = await asyncio.to_thread(_unregistered, c.slug, names, shas)
            except OSError:
                folder = "unreadable"
        state = "rules pending" if c.rules_pending else c.status
        line = (f"{c.name} [{state}]: {new} new file(s) not yet registered, "
                f"{registered} source(s) registered, {waiting} waiting to be cut, "
                f"{today.get(c.slug, 0)} clip(s) cut today (UTC), {made} made in "
                f"all, {cards.get(c.slug, 0)} card(s) awaiting approval, "
                f"{posted} posted, {submitted} submitted; {rate_line(c)}; "
                f"{budget}")
        gone = [r for r in removed if r["campaign"] == c.slug]
        # Footage never cut is kept past the retention window (the purge
        # takes it only once the intake gave up or the campaign ended).
        kept = [r for r in mine if r not in spent and c.status != "ended"
                and _past(r.get("registered_at"), now, privacy.clip_source_days())]
        if kept:
            line += "; " + ", ".join(f"source {r['id']} never cut, kept"
                                     for r in kept)
        if gone:
            line += "; " + ", ".join(
                f"source {r['id']} removed {r['deleted_at']:%d %b}"
                for r in gone[:3]) + (f" and {len(gone) - 3} more"
                                      if len(gone) > 3 else "")
        if folder == "missing" and c.status == "active":
            line += f"; no footage yet (put it in {sources.source_dir(c.slug)})"
        parts.append(line)
        evidence[c.slug] = {"status": c.status, "rules_pending": c.rules_pending,
                            "new_files": new, "sources": registered,
                            "waiting": waiting, "cut_today": today.get(c.slug, 0),
                            "made": made, "cards": cards.get(c.slug, 0),
                            "posted": posted, "submitted": submitted,
                            "rates": dict(c.rates),
                            "sources_removed": len(gone),
                            "never_cut_kept": len(kept),
                            "budget_usd": c.budget_usd if known else None,
                            "folder": folder}
        if folder == "unreadable":
            unknown.append(f"{c.name}'s footage folder could not be read")
        if c.status == "active" and not known:
            unknown.append(f"{c.name}: {budget}")
        if (why := intake_problem(c, ticks.get(c.slug), now)) is not None:
            unknown.append(why)
        for r in spent:
            unknown.append(f"{c.name}: source {r['id']} ({r['filename']}) failed "
                           f"to cut {r['cut_attempts']} times and is left alone "
                           f"(last: {r.get('last_cut_detail') or 'no reason kept'})")
    detail = "; ".join(parts) + f" (read {now:%d %b %H:%M} UTC)"
    if unknown:
        return Observation("status", Health.UNKNOWN, float(total_made), "clips",
                           detail="; ".join(unknown) + " -- " + detail,
                           evidence=evidence)
    return Observation("status", Health.OK, float(total_made), "clips",
                       detail=detail, evidence=evidence)
