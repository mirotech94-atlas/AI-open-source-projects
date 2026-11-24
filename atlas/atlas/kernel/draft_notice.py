"""Tell Miro on Slack about every new content draft (Miro 2026-11-13).

Drafts are rows in `content_drafts` written by many modules; none of them told
anyone. This reads new rows (id above a stored mark) and sends one Slack card
each through `comms.notify`. Read-only on the drafts; the only write is the
mark. The first run starts at the newest existing draft, so old drafts are not
replayed. Draft text is untrusted: it is wrapped before it reaches the card.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

log = logging.getLogger("atlas.draft_notice")
TOPIC = "drafts"
MAX_PER_TICK = 10
PREVIEW = 600


def interval() -> timedelta:
    return timedelta(minutes=5)


async def _mark() -> int:
    from .state import execute_sql, fetch_one
    await execute_sql("CREATE TABLE IF NOT EXISTS draft_notice_mark "
                      "(k int PRIMARY KEY, last_id bigint NOT NULL)")
    row = await fetch_one("SELECT last_id FROM draft_notice_mark WHERE k = 1")
    if row is not None:
        return int(row["last_id"])
    top = await fetch_one("SELECT COALESCE(MAX(id), 0) AS m FROM content_drafts")
    start = int(top["m"])
    await _save(start)
    return start


async def _save(last_id: int) -> None:
    from .state import execute_sql
    await execute_sql("INSERT INTO draft_notice_mark (k, last_id) VALUES (1, $1) "
                      "ON CONFLICT (k) DO UPDATE SET last_id = EXCLUDED.last_id",
                      last_id)


async def tick() -> dict[str, Any]:
    from .comms import Outbound, notify
    from .state import fetch_all
    try:
        mark = await _mark()
        rows = await fetch_all(
            "SELECT id, kind, source, title, body FROM content_drafts "
            "WHERE id > $1 AND status = 'draft' ORDER BY id LIMIT $2",
            mark, MAX_PER_TICK)
        sent = 0
        for r in rows:
            body = str(r["body"])[:PREVIEW]
            sent_to = await notify(Outbound(
                title=f"Draft #{r['id']}: {str(r['title'])[:80]}",
                body=f"{r['kind']} from {r['source']}\n\n{body}",
                topic=TOPIC, kind="draft"))
            if not sent_to:
                break                         # not delivered: retry next tick
            await _save(int(r["id"]))
            sent += 1
        return {"sent": sent}
    except Exception as exc:  # noqa: BLE001
        log.warning("draft notice failed: %s", type(exc).__name__)
        return {"error": type(exc).__name__}
