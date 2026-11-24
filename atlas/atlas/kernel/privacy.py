"""Retention. The part of ATLAS that deletes things on purpose.

Browsing history is the most sensitive thing ATLAS holds, and the promise made
to justify collecting it at all is that the raw record is short-lived. That
promise is only real if something enforces it without being asked, so this runs
from the scheduler every hour and is verified by tests rather than by intent.

Roll up first, then delete. If the roll-up fails, nothing is deleted — losing
the aggregate is recoverable, losing it *and* the raw rows is not.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.privacy")

DEFAULT_RETENTION_DAYS = 90
# What the server browser (modules/browser) read and saw. Not configurable:
# a screenshot of someone else's page has no reason to outlive the day.
BROWSER_SESSION_HOURS = 24
# The browser names every file it writes `browser-<row id or uuid>.txt|png`,
# and the sweep removes only files named that way: a BROWSER_DATA_DIR pointed
# at a shared folder by mistake loses nothing else.
BROWSER_FILE_PREFIX = "browser-"
_BROWSER_FILE = re.compile(r"^browser-(\d+|[0-9a-f]{32})\.(txt|png)$")
# What the phone line said, rendered by Fish Audio and served once to
# Twilio's <Play> (api/phone.py). It carries estate status and exists only
# until Twilio fetches it, so it is never kept longer than this, fetched or
# not. Files are named by the sha256 of their token -- never the token -- and
# the sweep touches nothing named any other way.
VOICE_CLIP_SECONDS = 600
_VOICE_FILE = re.compile(r"^[0-9a-f]{64}\.mp3$")


def retention_days() -> int:
    try:
        days = int(os.getenv("BROWSER_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    # A zero or negative value would mean "delete everything immediately",
    # which is far more likely to be a typo than an intention.
    return days if days >= 1 else DEFAULT_RETENTION_DAYS


async def roll_up_browser_history() -> int:
    """Fold raw events into per-domain, per-day totals. Idempotent."""
    await execute_sql(
        """INSERT INTO browser_rollup (day, domain, visits, dwell_ms)
           SELECT (at AT TIME ZONE 'UTC')::date AS day,
                  domain,
                  count(*) FILTER (WHERE kind = 'visit'),
                  coalesce(sum(dwell_ms), 0)
           FROM browser_events
           GROUP BY 1, 2
           ON CONFLICT (day, domain) DO UPDATE
             SET visits   = EXCLUDED.visits,
                 dwell_ms = EXCLUDED.dwell_ms""")
    row = await fetch_one("SELECT count(*) AS n FROM browser_rollup")
    return int(row["n"]) if row else 0


async def purge_browser_history() -> int:
    """Delete raw history past the retention window.

    The roll-up runs first and its failure aborts the delete, so the aggregate
    can never be lost along with the detail.
    """
    await roll_up_browser_history()
    days = retention_days()
    result = await execute_sql(
        f"""DELETE FROM browser_events
            WHERE at < now() - interval '{days} days'""")
    deleted = 0
    if isinstance(result, str) and result.startswith("DELETE "):
        deleted = int(result.split()[-1] or 0)
    if deleted:
        log.info("purged %d browser events older than %d days", deleted, days)
    return deleted


async def purge_voice_turns() -> int:
    """Voice conversations age out on the same schedule as browsing history.

    The audio was never stored — only the words — and after the window even
    those go.
    """
    days = retention_days()
    result = await execute_sql(
        f"DELETE FROM voice_turns WHERE at < now() - interval '{days} days'")
    deleted = 0
    if isinstance(result, str) and result.startswith("DELETE "):
        deleted = int(result.split()[-1] or 0)
    if deleted:
        log.info("purged %d voice turns older than %d days", deleted, days)
    return deleted


MEMORY_READS_DAYS = 30


async def purge_memory_reads() -> int:
    """The recall log behind the brain view ages out after thirty days.

    It holds no content — a namespace, a hit count, an agent — so this is not
    a privacy promise but a bound: `/api/brain` aggregates the whole table on
    every load, and only needs the last hour and the last thirty rows. A
    region unread for longer than the window reads "quiet", which is true.
    """
    days = MEMORY_READS_DAYS
    result = await execute_sql(
        f"DELETE FROM memory_reads WHERE at < now() - interval '{days} days'")
    deleted = 0
    if isinstance(result, str) and result.startswith("DELETE "):
        deleted = int(result.split()[-1] or 0)
    if deleted:
        log.info("purged %d memory reads older than %d days", deleted, days)
    return deleted



def browser_dir() -> Path:
    """Where the server browser keeps page text and screenshots. Defined here,
    not in the module, so the kernel never imports a module to purge it."""
    return Path(os.getenv("BROWSER_DATA_DIR", "data/browser"))


def _sweep_files(root: Path, cutoff: float) -> int:
    """Blocking; always through `asyncio.to_thread`."""
    if not root.is_dir():
        return 0
    swept = 0
    for path in root.iterdir():
        if not _BROWSER_FILE.match(path.name):
            continue
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                swept += 1
        except OSError:
            log.exception("could not remove %s", path)
    return swept


def voice_dir() -> Path:
    """Where the phone line caches a rendered clip until Twilio fetches it.
    Defined here, beside `browser_dir`, so the sweep needs no api import."""
    return Path(os.getenv("ATLAS_VOICE_DIR", "data/voice"))


def _sweep_voice(root: Path, cutoff: float) -> int:
    """Blocking; always through `asyncio.to_thread`."""
    if not root.is_dir():
        return 0
    swept = 0
    for path in root.iterdir():
        if not _VOICE_FILE.match(path.name):
            continue
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                swept += 1
        except OSError:
            # The name is a hash, not the token: safe to say which.
            log.exception("could not remove %s", path.name)
    return swept


async def purge_voice_clips() -> int:
    """Remove phone-line audio older than VOICE_CLIP_SECONDS. A clip served
    to Twilio is deleted as it is served; this catches the ones a dropped
    call never fetched."""
    cutoff = time.time() - VOICE_CLIP_SECONDS
    swept = await asyncio.to_thread(_sweep_voice, voice_dir(), cutoff)
    if swept:
        log.info("purged %d phone clips", swept)
    return swept


# ------------------------------------------------------ clip source footage
#
# A streamer's VOD is 1-3 GB, and the server's disk is 39 GB. Once every clip
# cut from a source is out of ATLAS's hands -- posted, submitted, withdrawn,
# its cut rolled back, or its post card declined or expired (the owner's "no") --
# the source has done its job: the clips are their own files in out/, and a
# source is never cut twice. So it goes, from here
# and only here (the clips package deletes nothing, by test). Hourly, from
# the scheduler; never an executor action, so no model can ask for it.
#
#   * only a file registered in `clip_sources`, at
#     <CLIPS_DATA_DIR>/<slug>/source/<filename>, whose bytes still hash to
#     the row's sha256; never a symlink, never through a symlinked folder,
#     never a name that could leave that folder;
#   * only when the source has at least one clip and none is still waiting
#     to post (`made` or `handed_off` with no declined or expired card as
#     its latest word);
#   * a source never cut is kept -- whatever its age, and under watch-only
#     -- unless the intake gave up on it (CLIP_MAX_ATTEMPTS failed cuts) or
#     its campaign has ended, and then only CLIPS_SOURCE_RETENTION_DAYS
#     (default 21) after it was registered;
#   * never a clip output, never anything else in the folder.
#
# The row stays, with `deleted_at` set: `clips.status` says "source removed
# <date>", and the intake treats it as done.

DEFAULT_CLIP_SOURCE_DAYS = 21
# The intake leaves a source alone after this many failed cuts
# (modules.clips.intake.MAX_ATTEMPTS; a test pins them equal).
CLIP_MAX_ATTEMPTS = 3
# The post cards whose "no" (declined, or expired unanswered) is final for
# the purge: the clip will not be posted from this source's cut.
CLIP_POST_CARDS = ("clips.publish_clip", "clips.publish_clip_youtube",
                   "clips.publish_clip_tiktok", "clips.publish_clip_youtube_postiz",
                   "clips.hand_off")
CLIP_CARD_NO = ("declined", "expired")
# A clip in one of these states needs nothing more from its source.
# rolled_back: the owner refused the cut.
CLIP_DONE = ("posted", "submitted", "withdrawn", "rolled_back")
# The clips package's own rules (sources.SLUG, sources.root), restated so
# the kernel never imports a module to purge it; a test pins them equal.
_CLIP_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CLIP_CHUNK = 1 << 20
# (real path, size, mtime_ns) -> sha256: a file that failed its check is not
# re-read every hour while it stays the same file.
_CLIP_HASHES: dict[tuple[str, int, int], str] = {}


def clips_dir() -> Path:
    """`CLIPS_DATA_DIR`, else `data/clips` -- `/app/data/clips` in the
    container, `./data/app/clips` on the server's disk."""
    return Path(os.getenv("CLIPS_DATA_DIR", "").strip() or "data/clips")


def clip_source_days() -> int:
    try:
        days = int(os.getenv("CLIPS_SOURCE_RETENTION_DAYS",
                             DEFAULT_CLIP_SOURCE_DAYS))
    except ValueError:
        return DEFAULT_CLIP_SOURCE_DAYS
    # As with browsing history: 0 would mean "delete footage never cut".
    return days if days >= 1 else DEFAULT_CLIP_SOURCE_DAYS


def clip_source_due(row: dict[str, Any], now: datetime, days: int) -> bool:
    """Is this source's footage done with? Pure. No clip waiting to post,
    and either at least one clip cut from it, or -- never cut -- the intake
    gave up on it or its campaign ended, `days` after it was registered."""
    if int(row.get("waiting") or 0) > 0:
        return False
    if int(row.get("clips") or 0) > 0:
        return True
    return clip_source_abandoned(row) and _registered_past(row, now, days)


def clip_waiting(status: str, card: str | None) -> bool:
    """Is this clip still waiting to post? Pure. Not when it is done with
    (CLIP_DONE), nor when the latest word on its post card is the owner's "no"
    -- declined, or expired unanswered (silence is never a yes)."""
    return status not in CLIP_DONE and (card or "") not in CLIP_CARD_NO


def clip_source_abandoned(row: dict[str, Any]) -> bool:
    """A never-cut source nothing will cut: spent, or its campaign ended."""
    return (int(row.get("cut_attempts") or 0) >= CLIP_MAX_ATTEMPTS
            or str(row.get("campaign_status") or "") == "ended")


def _registered_past(row: dict[str, Any], now: datetime, days: int) -> bool:
    at = row.get("registered_at")
    if not isinstance(at, datetime):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (now - at).total_seconds() >= days * 86400


def clip_source_path(root: Path, slug: str, filename: str) -> tuple[Path | None, str]:
    """(real path, "") of a registered source file that may be removed, or
    (None, why not). Blocking; always through `asyncio.to_thread`."""
    if not _CLIP_SLUG.match(slug or ""):
        return None, "not a campaign slug"
    name = filename or ""
    if (not name or name in (".", "..") or name.startswith(".")
            or "/" in name or "\\" in name or "\0" in name
            or os.path.isabs(name) or os.path.basename(name) != name):
        return None, "an unsafe file name"
    campaign = root / slug
    folder = campaign / "source"
    if campaign.is_symlink() or folder.is_symlink():
        return None, "its folder is a symlink"
    path = folder / name
    if path.is_symlink():
        return None, "a symlink"
    if not path.exists():
        return None, "not on disk"
    real = Path(os.path.realpath(path))
    base = Path(os.path.realpath(root))
    if real.parent != Path(os.path.realpath(folder)) or base not in real.parents:
        return None, "outside its campaign's source folder"
    if not real.is_file():
        return None, "not a plain file"
    return real, ""


def _clip_sha256(path: Path) -> str:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _CLIP_HASHES:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            while chunk := fh.read(_CLIP_CHUNK):
                digest.update(chunk)
        _CLIP_HASHES[key] = digest.hexdigest()
    return _CLIP_HASHES[key]


def _remove_clip_source(root: Path, slug: str, filename: str,
                        sha256: str) -> tuple[int | None, str]:
    """(bytes freed, "") when the file was removed; (None, why not).
    Blocking; always through `asyncio.to_thread`."""
    real, why = clip_source_path(root, slug, filename)
    if real is None:
        return None, why
    if _clip_sha256(real) != sha256:
        return None, "not the bytes that were registered (sha256 differs)"
    size = real.stat().st_size
    real.unlink()
    return size, ""


async def purge_clip_sources() -> tuple[int, int]:
    """Remove the clip source files that are done with. Returns (files,
    bytes). Each is logged; its row keeps its history, with `deleted_at`."""
    days = clip_source_days()
    cards = ", ".join(f"'{a}'" for a in CLIP_POST_CARDS)
    sources = await fetch_all(
        """SELECT s.id, s.campaign, s.filename, s.sha256, s.registered_at,
                  s.cut_attempts, c.status AS campaign_status
             FROM clip_sources s
             JOIN clip_campaigns c ON c.slug = s.campaign
            WHERE s.deleted_at IS NULL""")
    # Each clip of those sources, with the latest word on its post card.
    clip_rows = await fetch_all(
        f"""SELECT o.source_id, o.status,
                   (SELECT p.status FROM proposals p
                     WHERE p.action IN ({cards})
                       AND coalesce(p.params->>'clip_id',
                                    p.params->>'clip_ids') = o.id::text
                     ORDER BY p.created_at DESC LIMIT 1) AS card
              FROM clip_outputs o
              JOIN clip_sources s ON s.id = o.source_id
             WHERE s.deleted_at IS NULL""")
    tally: dict[int, list[int]] = {}
    for clip in map(dict, clip_rows or ()):
        counts = tally.setdefault(int(clip["source_id"]), [0, 0])
        counts[0] += 1
        counts[1] += clip_waiting(str(clip["status"]), clip.get("card"))
    rows = []
    for row in map(dict, sources or ()):
        row["clips"], row["waiting"] = tally.get(int(row["id"]), (0, 0))
        rows.append(row)
    now = datetime.now(UTC)
    root = clips_dir()
    files = freed = 0
    for row in map(dict, rows or ()):
        if not clip_source_due(row, now, days):
            continue
        slug, name, sid = str(row["campaign"]), str(row["filename"]), int(row["id"])
        try:
            size, why = await asyncio.to_thread(
                _remove_clip_source, root, slug, name, str(row["sha256"]))
        except OSError:
            log.exception("clip source %d (%s/%s) could not be removed", sid,
                          slug, name)
            continue
        if size is None:
            if why != "not on disk":
                log.warning("clip source %d (%s/%s) not removed: %s", sid, slug,
                            name, why)
            continue
        await execute_sql(
            """UPDATE clip_sources SET deleted_at = now()
                WHERE id = $1 AND deleted_at IS NULL""", sid)
        files += 1
        freed += size
        log.info("removed clip source %d (%s/source/%s, %d bytes): %s", sid,
                 slug, name, size,
                 "every clip from it is done" if int(row.get("clips") or 0)
                 else f"never cut and nothing will cut it, registered over "
                      f"{days} days ago")
    return files, freed


async def purge_browser_sessions() -> tuple[int, int]:
    """Delete server-browser text, screenshots and rows older than 24 h.
    Returns (rows, files). Files go by their own age, so one whose row was
    never written is swept all the same."""
    cutoff = time.time() - BROWSER_SESSION_HOURS * 3600
    files = await asyncio.to_thread(_sweep_files, browser_dir(), cutoff)
    result = await execute_sql(
        f"""DELETE FROM browser_sessions
            WHERE at < now() - interval '{BROWSER_SESSION_HOURS} hours'""")
    rows = 0
    if isinstance(result, str) and result.startswith("DELETE "):
        rows = int(result.split()[-1] or 0)
    if rows or files:
        log.info("purged %d browser sessions and %d files", rows, files)
    return rows, files


async def run() -> dict[str, int]:
    """Everything retention-related. Called hourly by the scheduler.

    Each purge runs on its own: one that fails is logged and reported as -1,
    and the others still run. The browser sessions go first — they are the
    shortest promise. (A failed roll-up still stops the browser-history
    delete, inside `purge_browser_history`, as it always has.)"""
    out: dict[str, int] = {}

    async def step(keys: tuple[str, ...], fn) -> None:
        try:
            value = await fn()
        except Exception:
            log.exception("retention step %s failed", fn.__name__)
            value = (-1,) * len(keys) if len(keys) > 1 else -1
        values = value if isinstance(value, tuple) else (value,)
        out.update(zip(keys, values, strict=True))

    await step(("purged_browser_sessions", "purged_browser_files"),
               purge_browser_sessions)
    await step(("purged_voice_clips",), purge_voice_clips)
    await step(("rolled_up_domains",), roll_up_browser_history)
    await step(("purged_events",), purge_browser_history)
    await step(("purged_voice_turns",), purge_voice_turns)
    await step(("purged_memory_reads",), purge_memory_reads)
    await step(("purged_clip_sources", "freed_clip_source_bytes"),
               purge_clip_sources)
    return out
