"""What the unattended intake reads and records. It acts through nothing here.

`kernel.clip_intake.tick` runs every `CLIPS_INTAKE_MIN` minutes (default
30) and, for each campaign that may be clipped, asks this file what is
new -- official files in `data/clips/<slug>/source/` whose sha256 is not on
file for that campaign, and the next registered source never cut -- then
registers and cuts through the executor (`clips.register_sources`,
`clips.cut`, both Tier 1 with their dry runs and rollbacks). The cards
that follow are the signals pass's. Everything here only reads, except the
two bookkeeping writes below, which record what the tick saw and did so
the check can say it:

  * `record_attempt` -- on the source row: when a cut was last tried, what
    it said, and how many real cut runs failed. A source that failed
    `MAX_ATTEMPTS` times is left alone (the check names it); one refused
    by the dry run (a missing key, a missing second source for split) is
    tried again after `RETRY_AFTER`, never every tick.
  * `record_run` -- one row per campaign in `clip_intake`: the tick's
    outcome, which the status check reads. Not ok is UNKNOWN there.

**Idempotent.** A file is registered only when its sha256 is not on file
for the campaign at all -- a registration Miro rolled back stays rolled
back -- and a source is cut only when it has no clip at all (a cut he
rolled back is final). Hashing is remembered per (path, size, mtime) for
the life of the process, so a large source is read once, not every tick.

**The daily cap** (`CLIPS_DAILY_CAP`, default 6) counts clips -- distinct
files, not rows per platform -- cut since midnight UTC, rolled-back ones
not counted; a cut is asked for at most what is left (`max_clips`).
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...kernel.state import execute_sql, fetch_all, fetch_one
from . import campaigns, sources

# Raise to 12 once >= 3 live campaigns have an approval history -- Miro's
# (the lane owner's) decision, not a code default to change on its own.
DEFAULT_CAP = 6
DEFAULT_INTERVAL_MIN = 30
MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(hours=6)

# (real path, size, mtime_ns) -> sha256, for this process's life.
_HASHES: dict[tuple[str, int, int], str] = {}


def daily_cap() -> int:
    """`CLIPS_DAILY_CAP`: clips per campaign per UTC day. 0 stops cutting."""
    try:
        return max(0, int(os.getenv("CLIPS_DAILY_CAP", str(DEFAULT_CAP))))
    except ValueError:
        return DEFAULT_CAP


def interval() -> timedelta:
    """`CLIPS_INTAKE_MIN`: minutes between ticks, at least 5."""
    try:
        minutes = int(os.getenv("CLIPS_INTAKE_MIN", str(DEFAULT_INTERVAL_MIN)))
    except ValueError:
        minutes = DEFAULT_INTERVAL_MIN
    return timedelta(minutes=max(5, minutes))


def enabled() -> bool:
    """`CLIPS_INTAKE=off` stops the unattended intake; on by default."""
    return os.getenv("CLIPS_INTAKE", "on").strip().lower() not in (
        "0", "off", "false", "no")


def gate(campaign: campaigns.Campaign) -> str | None:
    """Why the intake leaves this campaign alone, or None. The same gate
    `cut` applies, asked first so a refused campaign costs nothing."""
    if campaign.status != "active":
        return f"{campaign.name} is {campaign.status}"
    for check in (campaigns.refusal, campaigns.permission_refusal,
                  campaigns.format_refusal):
        if (why := check(campaign)) is not None:
            return f"refused: {why}"
    if campaign.rules_pending:
        return f"{campaign.name}'s rules are pending"
    return None


def today_utc(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _stat_listing(slug: str) -> list[tuple[Path, int, int]]:
    """The campaign's video files with size and mtime. Blocking. OSError
    when the folder exists but cannot be read."""
    folder = sources.source_dir(slug)
    if not folder.exists():
        return []
    list(folder.iterdir())                  # raises when unreadable
    out = []
    for path in sources._listing(slug):
        st = path.stat()
        out.append((path, st.st_size, st.st_mtime_ns))
    return out


def _hash(path: Path, size: int, mtime: int) -> str:
    key = (os.path.realpath(path), size, mtime)
    if key not in _HASHES:
        _HASHES[key] = sources.sha256_file(path)
    return _HASHES[key]


async def new_files(slug: str) -> list[str]:
    """Names of the official files whose sha256 is not on file for this
    campaign in ANY state. Reads only. OSError when the folder cannot be
    read."""
    listed = await asyncio.to_thread(_stat_listing, slug)
    if not listed:
        return []
    rows = await fetch_all("SELECT sha256 FROM clip_sources WHERE campaign = $1",
                           slug)
    known = {str(r["sha256"]) for r in rows}
    out = []
    for path, size, mtime in listed:
        digest = await asyncio.to_thread(_hash, path, size, mtime)
        if digest not in known:
            out.append(path.name)
            known.add(digest)               # the same bytes twice: once
    return out


async def made_today(slug: str, now: datetime | None = None) -> int:
    """Clips cut for this campaign since midnight UTC: distinct files, not
    one per platform; rolled-back ones not counted."""
    row = await fetch_one(
        """SELECT count(DISTINCT filename) AS n FROM clip_outputs
           WHERE campaign = $1 AND at >= $2::timestamptz
             AND status <> 'rolled_back'""", slug, today_utc(now))
    return int(row["n"]) if row is not None else 0


async def next_source(slug: str) -> dict[str, Any] | None:
    """The oldest registered source of this campaign that has never been
    cut, is not spent, was not tried within RETRY_AFTER, and is still on
    disk -- one the retention run removed (`deleted_at`) is done, not
    waiting. Reads only."""
    row = await fetch_one(
        """SELECT s.id, s.filename FROM clip_sources s
           WHERE s.campaign = $1 AND s.status = 'registered'
             AND s.deleted_at IS NULL
             AND NOT EXISTS (SELECT 1 FROM clip_outputs o WHERE o.source_id = s.id)
             AND coalesce(s.cut_attempts, 0) < $2
             AND (s.last_cut_at IS NULL OR s.last_cut_at < $3::timestamptz)
           ORDER BY s.id LIMIT 1""",
        slug, MAX_ATTEMPTS, datetime.now(UTC) - RETRY_AFTER)
    return dict(row) if row is not None else None


# The executor's refusals that are ATLAS-wide states, not this source's
# fault: nothing is recorded against the source for them.
_GLOBAL = ("ATLAS is in watch-only mode", "not run:", "refused: the pause state",
           "escalating instead of repeating")


def is_global(detail: str) -> bool:
    return any(marker in detail for marker in _GLOBAL)


def is_dry_refusal(detail: str) -> bool:
    return detail.startswith("dry run refused")


async def record_attempt(source_id: int, ok: bool, detail: str) -> None:
    """What one cut attempt said, on its source. A real run that failed
    counts toward MAX_ATTEMPTS; a dry-run refusal only waits RETRY_AFTER."""
    if ok or is_global(detail):
        return
    counted = 0 if is_dry_refusal(detail) else 1
    await execute_sql(
        """UPDATE clip_sources SET cut_attempts = coalesce(cut_attempts, 0) + $2,
                  last_cut_at = now(), last_cut_detail = $3
           WHERE id = $1""", int(source_id), counted, detail[:500])


async def record_run(slug: str, ok: bool, detail: str) -> None:
    """The tick's outcome for one campaign: one row, replaced each tick."""
    await execute_sql(
        """INSERT INTO clip_intake (campaign, at, ok, detail)
           VALUES ($1, now(), $2, $3)
           ON CONFLICT (campaign) DO UPDATE
              SET at = EXCLUDED.at, ok = EXCLUDED.ok, detail = EXCLUDED.detail""",
        slug, ok, detail[:1000])
