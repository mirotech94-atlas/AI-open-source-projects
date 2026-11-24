"""Research rows: competitors, their profile history, watches, changes,
prices, opportunities and reports (six agents phase 11).

Every function is one query; the rules live in the modules that call them.
`raise_card` is here too, so the tests replace one module for all of it.
Rollbacks archive or withdraw our own rows; nothing here deletes."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from ...kernel.state import execute_sql, fetch_all, fetch_one

FIELDS = ("channels", "offer", "pricing", "positioning", "cadence", "strengths",
          "weaknesses")
_COLS = ("id, lane, client_lead_id, name, homepage, youtube_channel_id, status, "
         "updated_at, " + ", ".join(FIELDS))
# Fixed names, never input: the tables a scan rollback may withdraw rows of.
_WITHDRAWABLE = frozenset({"research_changes", "research_prices"})


def _decode(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for f in FIELDS:
        if isinstance(out.get(f), str):
            out[f] = json.loads(out[f])
        out.setdefault(f, [])
    return out


# ------------------------------------------------------------ competitors

async def add_competitor(lane: str, name: str, homepage: str | None,
                         client_lead_id: int | None,
                         youtube_channel_id: str | None) -> int | None:
    row = await fetch_one(
        """INSERT INTO competitor_profiles (lane, name, homepage, client_lead_id,
                                            youtube_channel_id)
           VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING RETURNING id""",
        lane, name, homepage or None, client_lead_id, youtube_channel_id or None)
    return int(row["id"]) if row is not None else None


async def archive_competitor(cid: int) -> None:
    await execute_sql("UPDATE competitor_profiles SET status = 'archived' WHERE id = $1",
                      int(cid))
    await execute_sql("UPDATE research_watches SET status = 'archived' "
                      "WHERE competitor_id = $1", int(cid))


async def competitor(cid: int) -> dict[str, Any] | None:
    return _decode(await fetch_one(
        f"SELECT {_COLS} FROM competitor_profiles WHERE id = $1", int(cid)))


async def competitors(lane: str | None = None,
                      client_lead_id: int | None = None) -> list[dict[str, Any]]:
    rows = await fetch_all(
        f"""SELECT {_COLS} FROM competitor_profiles
             WHERE status = 'active' AND ($1::text IS NULL OR lane = $1)
               AND ($2::bigint IS NULL OR client_lead_id = $2)
             ORDER BY lane, name""", lane, client_lead_id)
    return [d for r in rows if (d := _decode(r)) is not None]


async def set_fields(cid: int, fields: dict[str, list[dict[str, Any]]]) -> None:
    names = [f for f in fields if f in FIELDS]       # fixed names only
    if not names:
        return
    sets = ", ".join(f"{f} = ${i + 2}::jsonb" for i, f in enumerate(names))
    await execute_sql(
        f"UPDATE competitor_profiles SET {sets}, updated_at = now() WHERE id = $1",
        int(cid), *[json.dumps(fields[f], default=str) for f in names])


async def add_history(cid: int, field: str, old: Any, new: Any, source: str) -> int:
    row = await fetch_one(
        """INSERT INTO competitor_profile_history (competitor_id, field, old, new, source)
           VALUES ($1, $2, $3::jsonb, $4::jsonb, $5) RETURNING id""",
        int(cid), field, json.dumps(old, default=str), json.dumps(new, default=str),
        source[:500])
    return int(row["id"])


async def history_rows(ids: list[int]) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, competitor_id, field, old, new, status
             FROM competitor_profile_history WHERE id = ANY($1::bigint[])
            ORDER BY id DESC""", [int(i) for i in ids])
    out = []
    for r in rows:
        d = dict(r)
        for k in ("old", "new"):
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out


async def withdraw_history(ids: list[int]) -> None:
    await execute_sql("UPDATE competitor_profile_history SET status = 'withdrawn' "
                      "WHERE id = ANY($1::bigint[])", [int(i) for i in ids])


# ---------------------------------------------------------------- watches

async def add_watch(url: str, label: str, competitor_id: int | None) -> int | None:
    row = await fetch_one(
        """INSERT INTO research_watches (url, label, competitor_id) VALUES ($1, $2, $3)
           ON CONFLICT (url) DO NOTHING RETURNING id""", url, label, competitor_id)
    return int(row["id"]) if row is not None else None


async def archive_watch(wid: int) -> None:
    await execute_sql("UPDATE research_watches SET status = 'archived' WHERE id = $1",
                      int(wid))


async def watches(limit: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, url, label, competitor_id, last_sha, last_text, last_read_at,
                  fail_streak
             FROM research_watches WHERE status = 'active'
            ORDER BY last_read_at NULLS FIRST, id LIMIT $1""", int(limit))
    return [dict(r) for r in rows]


async def watch_ok(wid: int, sha: str, text: str, at: datetime) -> None:
    await execute_sql(
        """UPDATE research_watches SET last_sha = $2, last_text = $3, last_read_at = $4,
                  fail_streak = 0, last_error = NULL WHERE id = $1""",
        int(wid), sha, text, at)


async def watch_failed(wid: int, error: str) -> int:
    row = await fetch_one(
        """UPDATE research_watches SET fail_streak = fail_streak + 1, last_error = $2
            WHERE id = $1 RETURNING fail_streak""", int(wid), error[:300])
    return int(row["fail_streak"]) if row is not None else 0


async def insert_change(watch_id: int, old_sha: str | None, new_sha: str, excerpt: str,
                        source_url: str, fetched_at: datetime) -> int:
    row = await fetch_one(
        """INSERT INTO research_changes (watch_id, old_sha, new_sha, excerpt,
                                         source_url, fetched_at)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
        int(watch_id), old_sha, new_sha, excerpt, source_url, fetched_at)
    return int(row["id"])


async def insert_price(competitor_id: int | None, watch_id: int | None, amount: str,
                       currency: str, period: str | None, label: str, source_url: str,
                       fetched_at: datetime) -> int:
    row = await fetch_one(
        """INSERT INTO research_prices (competitor_id, watch_id, amount, currency,
                                        period, label, source_url, fetched_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
        competitor_id, watch_id, Decimal(amount), currency, period, label[:120],
        source_url, fetched_at)
    return int(row["id"])


async def withdraw(table: str, ids: list[int]) -> None:
    if table not in _WITHDRAWABLE:
        raise ValueError(f"not a research table: {table}")
    await execute_sql(f"UPDATE {table} SET status = 'withdrawn' "
                      "WHERE id = ANY($1::bigint[])", [int(i) for i in ids])


async def prices(lane: str | None = None,
                 client_lead_id: int | None = None) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT p.id, p.amount, p.currency, p.period, p.label, p.source_url,
                  p.fetched_at, COALESCE(c.name, w.label, '') AS name,
                  COALESCE(c.lane, '') AS lane
             FROM research_prices p
             LEFT JOIN research_watches w ON w.id = p.watch_id
             LEFT JOIN competitor_profiles c
                    ON c.id = COALESCE(p.competitor_id, w.competitor_id)
            WHERE p.status = 'active'
              AND ($1::text IS NULL OR c.lane = $1)
              AND ($2::bigint IS NULL OR c.client_lead_id = $2)
              AND p.at >= now() - interval '35 days'
            ORDER BY name, p.amount""", lane, client_lead_id)
    return [{**dict(r), "amount": f"{Decimal(r['amount']):.2f}"} for r in rows]


async def changes_since(days: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT ch.id, ch.excerpt, ch.source_url, ch.fetched_at, w.label
             FROM research_changes ch JOIN research_watches w ON w.id = ch.watch_id
            WHERE ch.status = 'active' AND ch.at >= now() - make_interval(days => $1)
            ORDER BY ch.at DESC LIMIT 50""", int(days))
    return [dict(r) for r in rows]


async def watch_stats() -> dict[str, Any]:
    row = await fetch_one(
        """SELECT count(*) AS active, count(*) FILTER (WHERE fail_streak >= 3) AS failing,
                  max(last_read_at) AS last_read
             FROM research_watches WHERE status = 'active'""")
    return dict(row) if row is not None else {"active": 0, "failing": 0,
                                              "last_read": None}


# ---------------------------------------------------------- opportunities

async def insert_opportunity(title: str, detail: str, source_urls: list[str]) -> int:
    row = await fetch_one(
        """INSERT INTO research_opportunities (title, detail, source_urls)
           VALUES ($1, $2, $3::jsonb) RETURNING id""",
        title, detail, json.dumps(source_urls))
    return int(row["id"])


async def opportunity(oid: int) -> dict[str, Any] | None:
    row = await fetch_one(
        """SELECT id, title, detail, source_urls, status, task_id
             FROM research_opportunities WHERE id = $1""", int(oid))
    if row is None:
        return None
    out = dict(row)
    if isinstance(out["source_urls"], str):
        out["source_urls"] = json.loads(out["source_urls"])
    return out


async def set_opportunity(oid: int, status: str, task_id: int | None = None) -> None:
    await execute_sql(
        """UPDATE research_opportunities SET status = $2,
                  task_id = COALESCE($3, task_id) WHERE id = $1""",
        int(oid), status, task_id)


async def opportunities(status: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, title, detail, source_urls, status FROM research_opportunities
            WHERE status = $1 ORDER BY at DESC LIMIT 20""", status)
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- reports

async def insert_report(kind: str, title: str, body: str,
                        sections: list[dict[str, Any]]) -> int:
    row = await fetch_one(
        """INSERT INTO reports (kind, title, body, sections, modules)
           VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
        kind, title, body, json.dumps(sections, default=str), ["research"])
    if row is None:
        raise RuntimeError("the report row was not written")
    return int(row["id"])


async def withdraw_report(rid: int, kind: str) -> None:
    await execute_sql("UPDATE reports SET kind = 'withdrawn' WHERE id = $1 AND kind = $2",
                      int(rid), kind)


async def insert_client_report(lead_id: int, to: str, subject: str, body: str) -> int:
    row = await fetch_one(
        """INSERT INTO research_client_reports (client_lead_id, to_address, subject, body)
           VALUES ($1, $2, $3, $4) RETURNING id""", int(lead_id), to, subject, body)
    return int(row["id"])


async def client_report(rid: int) -> dict[str, Any] | None:
    row = await fetch_one(
        """SELECT id, client_lead_id, to_address, subject, body, status
             FROM research_client_reports WHERE id = $1""", int(rid))
    return dict(row) if row is not None else None


async def set_client_report(rid: int, status: str) -> None:
    await execute_sql("UPDATE research_client_reports SET status = $2 WHERE id = $1",
                      int(rid), status)


# ------------------------------------------------------------------ cards

async def raise_card(action: str, params: dict[str, Any], reason: str, client: str,
                     text: str, dedup: str) -> bool:
    from ...kernel.lead_followup import _send_card
    return await _send_card(action, params, reason,
                            {"agent": "Research", "client": client, "text": text}, dedup)
