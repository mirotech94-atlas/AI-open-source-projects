"""The channel study (six agents phase 8, B0): read-only, run by the owner.

    python -m atlas.cli channel-study --channel clips|ecc

The uploads playlist, its items, `videos.list` (1 unit per 50 ids) and
90 days of analytics for the newest videos; never `api.videos()` (search,
100 units). Written to `reports` as kind `channel_study`. A figure that
could not be read is UNKNOWN with the reason, never 0 (invariants 6, 7)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from . import api

DAYS = 90
MAX_VIDEOS = 25
STUDY_CHANNELS = ("clips", "ecc")


def _fig(value: Any) -> str:
    return "UNKNOWN" if value is None else (f"{value:.0f}" if isinstance(value, float)
                                            else str(value))


async def collect(channel: str) -> dict[str, Any]:
    """Every read, each with its time or its reason."""
    out: dict[str, Any] = {"channel": channel, "read_at": datetime.now(UTC),
                           "videos": [], "why": None}
    playlist, why = await api.uploads_playlist(channel)
    if playlist is None:
        out["why"] = f"uploads playlist unreadable: {why}"
        return out
    items, why = await api.playlist_items(channel, playlist)
    if why is not None:
        out["why"] = f"playlist items unreadable: {why}"
        return out
    ids = [i["video_id"] for i in items]
    details, why = await api.video_details(channel, ids)
    if why is not None:
        out["why"] = f"videos.list unreadable: {why}"
        return out
    details.sort(key=lambda d: str(d.get("published_at") or ""), reverse=True)
    for d in details[:MAX_VIDEOS]:
        stats, why = await api.analytics(channel, d["video_id"], DAYS)
        out["videos"].append({**d, "stats": stats, "stats_why": why})
    out["total_videos"] = len(ids)
    return out


def compose(study: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    at = study["read_at"].strftime("%Y-%m-%d %H:%M UTC")
    if study.get("why"):
        lines = [f"UNKNOWN: {study['why']} (tried {at})"]
    else:
        lines = [(f"{study.get('total_videos', 0)} uploads; the newest "
                  f"{len(study['videos'])} over {DAYS} days (read {at}):")]
        for v in study["videos"]:
            s = v.get("stats")
            if s is None:
                lines.append(f"- {str(v.get('title'))[:70]}: UNKNOWN ({v.get('stats_why')})")
                continue
            lines.append(f"- {str(v.get('title'))[:70]} [{v.get('privacy')}]: views "
                         f"{_fig(s.get('views'))}, minutes "
                         f"{_fig(s.get('estimated_minutes_watched'))}, avg view "
                         f"{_fig(s.get('average_view_percentage'))}%")
    sections = [{"title": f"Channel study: {study['channel']}", "lines": lines}]
    return "\n".join(lines), sections


async def run(channel: str) -> tuple[int | None, str]:
    """(report id, text). Reads only; the one write is our own reports row."""
    from ...kernel.state import fetch_one
    if channel not in STUDY_CHANNELS:
        return None, f"--channel must be one of {', '.join(STUDY_CHANNELS)}"
    body, sections = compose(await collect(channel))
    row = await fetch_one(
        """INSERT INTO reports (kind, title, body, sections, modules)
           VALUES ('channel_study', $1, $2, $3, $4) RETURNING id""",
        f"Channel study: {channel}", body, json.dumps(sections, default=str),
        ["youtube"])
    return int(row["id"]), body
