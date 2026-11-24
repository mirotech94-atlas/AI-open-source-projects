"""Seed the academy competitor list from a saved YouTube search export
(`competitors-YYYY-MM-DD.json`: query -> list of videos with a channel name
and video id). The file is opened read-only and never written. Each channel
becomes an `academy` competitor whose `channels` field holds measured items:
the videos seen, each sourced to its watch URL and dated from the file name
(else the file's mtime). Running it again adds nothing new."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import profiles, store

MAX_VIDEOS = 3
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _read_at(path: Path) -> datetime:
    m = _DATE.search(path.name)
    if m:
        return datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def read_seed(path: str | Path) -> list[dict[str, Any]]:
    """One row per channel, in first-seen order, with its videos."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:              # read-only
        data = json.load(fh)
    rows: dict[str, dict[str, Any]] = {}
    for query, videos in (data.items() if isinstance(data, dict) else ()):
        for v in videos if isinstance(videos, list) else ():
            if not isinstance(v, dict):
                continue
            name = " ".join(str(v.get("channel") or "").split())[:120]
            vid = str(v.get("id") or "")
            if not name or not re.fullmatch(r"[\w-]{6,20}", vid):
                continue
            row = rows.setdefault(name.lower(), {"name": name, "videos": []})
            row["videos"].append({"title": str(v.get("title") or "")[:200],
                                  "id": vid, "query": str(query)[:120],
                                  "views": v.get("views")})
    return list(rows.values())


async def import_seed(path: str | Path) -> dict[str, int]:
    p = Path(path)
    at = _read_at(p)
    added = skipped = 0
    for row in read_seed(p):
        cid = await store.add_competitor("academy", row["name"], None, None, None)
        if cid is None:
            skipped += 1
            continue
        items = [profiles.item({"video": v["title"], "query": v["query"],
                                "views": v["views"]},
                               f"https://www.youtube.com/watch?v={v['id']}", at,
                               profiles.MEASURED)
                 for v in row["videos"][:MAX_VIDEOS]]
        await store.set_fields(cid, {"channels": items})
        await store.add_history(cid, "channels", [], items, f"seed:{p.name}")
        added += 1
    return {"added": added, "skipped": skipped}
