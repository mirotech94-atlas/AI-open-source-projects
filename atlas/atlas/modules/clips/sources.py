"""Where official footage lives, and the register that makes it clippable.

Footage comes ONLY from a campaign's official folder (frame.io, MediaSilo,
WeTransfer or Drive). ATLAS never fetches from those: the download is
Miro's, or a Mac-side step's, and it lands in

    data/clips/<campaign slug>/source/

`clips.register_sources` hashes each file there (sha256) and records it
against that campaign in `clip_sources`. `cut` then takes a source ID,
never a path, and refuses a file that is not registered to that very
campaign, not in that campaign's folder, a symlink, or no longer the bytes
that were registered. A file dropped in the wrong campaign's folder is that
campaign's source and nobody else's. A campaign with no permission source
registers nothing, and nothing here fetches a video from anywhere.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_all, fetch_one
from . import campaigns

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm"})
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CHUNK = 1 << 20


def root() -> Path:
    """`CLIPS_DATA_DIR`, else `data/clips` -- `/app/data/clips` in the
    container, `./data/app/clips` on the server's disk."""
    return Path(os.getenv("CLIPS_DATA_DIR", "").strip() or "data/clips")


def campaign_dir(slug: str) -> Path:
    if not SLUG.match(slug or ""):
        raise ValueError(f"{slug!r} is not a campaign slug")
    return root() / slug


def source_dir(slug: str) -> Path:
    return campaign_dir(slug) / "source"


def out_dir(slug: str) -> Path:
    return campaign_dir(slug) / "out"


def trash_dir(slug: str) -> Path:
    return campaign_dir(slug) / ".trash"


def brand_dir(slug: str) -> Path:
    """The campaign's own brand assets (logo.png, banner.png), placed by
    Miro: on the server `./data/app/clips/<slug>/brand/`. Never fetched."""
    return campaign_dir(slug) / "brand"


def sha256_file(path: Path) -> str:
    """Blocking: call through `asyncio.to_thread`."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def confined(path: Path, folder: Path) -> Path | None:
    """The real path, if it is a plain file inside `folder`; else None."""
    if path.is_symlink():
        return None
    real = Path(os.path.realpath(path))
    base = Path(os.path.realpath(folder))
    if base not in real.parents or not real.is_file():
        return None
    return real


def lane_clip(path: str | Path) -> bool:
    """Is this a clip the clips lane cut: a plain `.mp4` directly inside
    `<CLIPS_DATA_DIR>/<campaign>/out/`, never a symlink, never anything else
    on disk? The floor every clips account posts from (YouTube `clips`, the
    clips TikTok). Blocking: it reads the filesystem."""
    try:
        if os.path.islink(path):
            return False
        real = os.path.realpath(path)
        base = os.path.realpath(root())
        rel = os.path.relpath(real, base)
    except (OSError, ValueError):
        return False
    parts = rel.split(os.sep)
    return (len(parts) == 3 and parts[1] == "out" and bool(SLUG.match(parts[0]))
            and parts[2].endswith(".mp4") and not parts[2].startswith(".")
            and os.path.isfile(real))


def _listing(slug: str) -> list[Path]:
    folder = source_dir(slug)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if not p.name.startswith(".")
                  and p.suffix.lower() in VIDEO_EXTENSIONS
                  and confined(p, folder) is not None)


async def _hashed(slug: str) -> list[tuple[Path, str, int]]:
    files = await asyncio.to_thread(_listing, slug)
    out = []
    for path in files:
        digest = await asyncio.to_thread(sha256_file, path)
        size = (await asyncio.to_thread(path.stat)).st_size
        out.append((path, digest, size))
    return out


async def _campaign(slug: str) -> tuple[campaigns.Campaign | None, str | None]:
    try:
        campaign_dir(slug)
    except ValueError as exc:
        return None, str(exc)
    found = await campaigns.load(slug)
    if found is None:
        return None, f"no campaign {slug!r}"
    if (why := campaigns.refusal(found)) is not None:
        return None, f"refused: {why}"
    if (why := campaigns.permission_refusal(found)) is not None:
        return None, f"refused: {why}"
    if found.status == "ended":
        return None, f"{found.name} has ended"
    return found, None


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


async def _known(slug: str, only_new: bool = False) -> set[str]:
    """The sha256s already registered -- or, with `only_new`, on file in ANY
    state, so a registration Miro rolled back (retired) stays back. The
    unattended intake always asks with `only_new`; by hand, registering a
    retired file again brings it back."""
    if only_new:
        rows = await fetch_all(
            "SELECT sha256 FROM clip_sources WHERE campaign = $1", slug)
    else:
        rows = await fetch_all(
            """SELECT sha256 FROM clip_sources
               WHERE campaign = $1 AND status = 'registered'""", slug)
    return {str(r["sha256"]) for r in rows}


async def register_sources_dry(campaign: str, only_new: bool = False) -> ActionResult:
    found, why = await _campaign(campaign)
    if found is None:
        return ActionResult(False, why or "refused")
    files = await _hashed(campaign)
    known = await _known(campaign, _flag(only_new))
    new = [p.name for p, digest, _ in files if digest not in known]
    if not files:
        return ActionResult(True, f"nothing in {source_dir(campaign)} to register")
    return ActionResult(True, f"would register {len(new)} new file(s) for "
                              f"{found.name}: {', '.join(new) or 'none new'}",
                        evidence={"new": new})


async def register_sources(campaign: str, only_new: bool = False) -> ActionResult:
    """Record every official file in the campaign's source folder that is
    not on file yet: its name, size and sha256, against this campaign.
    `only_new` skips a file on file in any state (see `_known`)."""
    found, why = await _campaign(campaign)
    if found is None:
        return ActionResult(False, why or "refused")
    files = await _hashed(campaign)
    known = await _known(campaign, _flag(only_new))
    added: list[int] = []
    names: list[str] = []
    for path, digest, size in files:
        if digest in known:
            continue
        row = await fetch_one(
            """INSERT INTO clip_sources (campaign, filename, sha256, bytes)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (campaign, sha256) DO UPDATE
                  SET status = 'registered', filename = EXCLUDED.filename
                WHERE clip_sources.status = 'retired'
               RETURNING id""",
            campaign, path.name, digest, size)
        if row is not None:
            added.append(int(row["id"]))
            names.append(path.name)
            known.add(digest)
    if not added:
        return ActionResult(True, f"no new files in {source_dir(campaign)}")
    return ActionResult(True, f"registered {len(added)} source file(s) for "
                              f"{found.name}: {', '.join(names)}",
                        evidence={"ids": added, "files": names},
                        rollback_token=json.dumps({"ids": added}))


async def register_sources_rollback(token: str) -> ActionResult:
    """Retires the rows it registered; the files stay where Miro put them."""
    ids = [int(i) for i in json.loads(token)["ids"]]
    for sid in ids:
        await execute_sql(
            "UPDATE clip_sources SET status = 'retired' WHERE id = $1", sid)
    return ActionResult(True, f"retired source(s) {', '.join(map(str, ids))}")


async def other_source(slug: str, source_id: int) -> int | None:
    """The id of this campaign's lowest-numbered registered source other
    than `source_id`, or None: `split`'s second half. `verified` still
    checks it before it is read."""
    rows = await fetch_all(
        """SELECT id FROM clip_sources
           WHERE campaign = $1 AND status = 'registered' AND id <> $2
             AND deleted_at IS NULL
           ORDER BY id LIMIT 1""", slug, int(source_id))
    return int(rows[0]["id"]) if rows else None


async def verified(slug: str, source_id: int) -> tuple[Path | None, dict[str, Any] | None,
                                                         str | None]:
    """(path, row, None) for a registered, unchanged source of THIS
    campaign; (None, None, why) for anything else."""
    row = await fetch_one(
        """SELECT id, campaign, filename, sha256, status, deleted_at
           FROM clip_sources WHERE id = $1""", int(source_id))
    if row is None:
        return None, None, f"source {source_id} is not registered"
    row = dict(row)
    if row["campaign"] != slug:
        return None, None, (f"source {source_id} is registered to "
                            f"{row['campaign']!r}, not {slug!r}")
    if row["status"] != "registered":
        return None, None, f"source {source_id} is {row['status']}"
    if (gone := row.get("deleted_at")) is not None:
        return None, None, (f"source {source_id} was removed from disk "
                            f"{gone:%d %b %Y} (its clips were done with)")
    name = str(row["filename"])
    if "/" in name or "\\" in name or name.startswith("."):
        return None, None, f"source {source_id} has an unsafe file name"
    folder = source_dir(slug)
    real = await asyncio.to_thread(confined, folder / name, folder)
    if real is None:
        return None, None, (f"source {source_id} ({name}) is not a plain file in "
                            f"{folder}")
    digest = await asyncio.to_thread(sha256_file, real)
    if digest != row["sha256"]:
        return None, None, (f"source {source_id} ({name}) is not the file that "
                            f"was registered (sha256 differs)")
    return real, row, None
