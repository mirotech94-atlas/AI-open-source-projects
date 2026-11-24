"""Rows under the Sales agent: its leads (on `agency_leads`, marked by
`source`), research, proposals and CSV imports. Nothing here decides."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.state import execute_sql, fetch_all, fetch_one
from ..agency._store import parse_json

NY = ZoneInfo("America/New_York")
INBOX = Path("data/sales/inbox")


def _row(row: Any) -> dict[str, Any] | None:
    return None if row is None else dict(row)


async def get_lead(lead_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one("SELECT * FROM agency_leads WHERE id=$1", int(lead_id)))


async def insert_lead(row: dict[str, Any]) -> int | None:
    """None when the key already exists: a repeat never resets a lead."""
    got = _row(await fetch_one(
        """INSERT INTO agency_leads
             (place_id, name, category, address, phone, website, source, segment,
              sales_stage, features)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'new',$9)
           ON CONFLICT (place_id) DO NOTHING RETURNING id""",
        row["place_id"], row["name"], row["category"], row.get("address"),
        row.get("phone"), row.get("website"), row["source"], row["segment"],
        json.dumps({k: row[k] for k in ("email", "attribution") if row.get(k)})))
    return int(got["id"]) if got else None


async def drop_leads(lead_ids: list[int]) -> None:
    await execute_sql(
        "UPDATE agency_leads SET status='dropped', sales_stage='dropped' "
        "WHERE id = ANY($1::bigint[])", [int(i) for i in lead_ids])


async def suppressed_addresses() -> set[str]:
    rows = await fetch_all("SELECT address FROM agency_suppression")
    return {str(r["address"]).lower() for r in rows}


async def leads_at_stage(stage: str, limit: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT * FROM agency_leads
            WHERE source IS NOT NULL AND COALESCE(sales_stage, 'new') = $1
              AND status NOT IN ('dropped', 'disqualified', 'closed')
            ORDER BY id LIMIT $2""", stage, int(limit))
    return [dict(r) for r in rows]


async def set_stage(lead_id: int, stage: str) -> str | None:
    """The stage before, for the rollback."""
    before = _row(await fetch_one(
        "SELECT sales_stage FROM agency_leads WHERE id=$1", int(lead_id)))
    await execute_sql("UPDATE agency_leads SET sales_stage=$2 WHERE id=$1",
                      int(lead_id), stage)
    return (before or {}).get("sales_stage")


async def restore_stage(lead_id: int, stage: str | None) -> None:
    await execute_sql("UPDATE agency_leads SET sales_stage=$2 WHERE id=$1",
                      int(lead_id), stage)


async def insert_research(lead_id: int, urls: list[str], signals: dict[str, Any],
                          summary: str) -> int:
    row = await fetch_one(
        """INSERT INTO sales_research (lead_id, source_urls, signals, summary_untrusted)
           VALUES ($1,$2,$3,$4) RETURNING id""",
        int(lead_id), json.dumps(urls), json.dumps(signals), summary)
    return int(row["id"])


async def latest_research(lead_id: int) -> dict[str, Any] | None:
    row = _row(await fetch_one(
        """SELECT * FROM sales_research WHERE lead_id=$1 AND withdrawn_at IS NULL
            ORDER BY at DESC LIMIT 1""", int(lead_id)))
    if row is not None:
        row["signals"] = parse_json(row.get("signals"), {})
    return row


async def withdraw_research(research_id: int) -> None:
    await execute_sql("UPDATE sales_research SET withdrawn_at=now() WHERE id=$1",
                      int(research_id))


async def insert_proposal(*, lead_id: int, price: int, returning: bool,
                          html_path: str, pdf_path: str | None, sha: str) -> int:
    row = await fetch_one(
        """INSERT INTO sales_proposals (lead_id, price, is_returning, html_path,
                                        pdf_path, sha)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        int(lead_id), int(price), bool(returning), html_path, pdf_path, sha)
    return int(row["id"])


async def withdraw_proposal(proposal_id: int) -> None:
    await execute_sql("UPDATE sales_proposals SET status='withdrawn' WHERE id=$1",
                      int(proposal_id))


async def import_seen(sha: str) -> bool:
    return await fetch_one("SELECT 1 FROM sales_imports WHERE sha=$1", sha) is not None


async def record_import(path: str, sha: str, inserted: int) -> None:
    await execute_sql(
        """INSERT INTO sales_imports (sha, path, inserted) VALUES ($1,$2,$3)
           ON CONFLICT (sha) DO NOTHING""", sha, path, int(inserted))


def inbox_files() -> list[str]:
    if not INBOX.is_dir():
        return []
    return sorted(str(p) for p in INBOX.glob("*.csv") if p.is_file())


async def pipeline() -> dict[str, int]:
    rows = await fetch_all(
        """SELECT COALESCE(sales_stage, 'new') AS stage, count(*) AS n
             FROM agency_leads WHERE source IS NOT NULL GROUP BY 1""")
    return {str(r["stage"]): int(r["n"]) for r in rows}


async def followups_due() -> int:
    """First emails to Sales leads still unanswered past the follow-up wait."""
    from ...kernel.lead_followup import followup_after
    row = await fetch_one(
        """SELECT count(*) AS n FROM agency_outreach o
             JOIN agency_leads l ON l.id = o.lead_id
            WHERE l.source IS NOT NULL AND o.status = 'sent'
              AND o.sent_at < now() - $1::interval""", followup_after())
    return int((row or {"n": 0})["n"])


async def cards_today() -> int:
    """Outreach drafts for Sales leads written since local midnight."""
    start = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        """SELECT count(*) AS n FROM agency_outreach o
             JOIN agency_leads l ON l.id = o.lead_id
            WHERE l.source IS NOT NULL AND o.at >= $1""", start.astimezone(UTC))
    return int((row or {"n": 0})["n"])
