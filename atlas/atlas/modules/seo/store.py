"""SEO rows: audits, keywords, ranks, page changes and blog drafts.

Every function is one query; the rules live in the modules that call them.
`raise_card` is here too, so the tests replace one module for all of it.
Rollbacks withdraw our own rows; nothing here deletes a client's data."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...kernel.state import execute_sql, fetch_all, fetch_one
from .. import store_origin

BLOG_KIND = "seo_blog"
# The store (2026-10-27, its own VM at ACADEMY_PUBLIC_ORIGIN): our own site,
# audited like a client's (it has no build dir). The old shop row is retired
# (RETIRED_SLUGS): never a target again, and its old readings are hidden.
ACADEMY: dict[str, Any] = {"slug": "academy-store", "domain": store_origin.host(),
                           "path": None, "client": "ATLAS Academy"}
RETIRED_SLUGS = frozenset({"mapfront-shop"})


def academy() -> dict[str, Any]:
    """The store's row, its domain read from ACADEMY_PUBLIC_ORIGIN now."""
    return {**ACADEMY, "domain": store_origin.host()}


def retired(slug: Any, domain: Any) -> bool:
    return str(slug or "") in RETIRED_SLUGS


OWN = {ACADEMY["slug"]: ACADEMY}


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


# ----------------------------------------------------------------- sites

async def site(slug: str) -> dict[str, Any] | None:
    if slug == ACADEMY["slug"]:
        return academy()
    row = await fetch_one(
        """SELECT s.slug, s.domain, s.path, COALESCE(l.name, s.slug) AS client
             FROM agency_sites s LEFT JOIN agency_leads l ON l.id = s.lead_id
            WHERE s.slug = $1""", str(slug))
    return _row(row)


async def targets() -> list[dict[str, Any]]:
    """Sites with a domain, plus the store (never a retired host)."""
    rows = await fetch_all(
        """SELECT s.slug, s.domain, s.path, COALESCE(l.name, s.slug) AS client
             FROM agency_sites s LEFT JOIN agency_leads l ON l.id = s.lead_id
            WHERE s.domain IS NOT NULL AND s.domain <> '' ORDER BY s.slug""")
    sites = [dict(r) for r in rows if not retired(r["slug"], r["domain"])]
    return [*sites, academy()]


# ---------------------------------------------------------------- audits

async def insert_audit(slug: str, url: str, status: str, summary: str,
                       items: dict[str, Any]) -> int:
    row = await fetch_one(
        """INSERT INTO seo_audits (slug, url, status, summary, items)
           VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING id""",
        slug, url, status, summary, json.dumps(items))
    return int(row["id"])


async def withdraw_audit(aid: int) -> None:
    await execute_sql("UPDATE seo_audits SET status = 'withdrawn' WHERE id = $1",
                      int(aid))


# -------------------------------------------------------- keywords, ranks

async def add_keywords(slug: str, keywords: list[str]) -> list[int]:
    ids: list[int] = []
    for kw in keywords:
        row = await fetch_one(
            """INSERT INTO seo_keywords (slug, keyword) VALUES ($1, $2)
               ON CONFLICT (slug, keyword) DO NOTHING RETURNING id""", slug, kw)
        if row is not None:
            ids.append(int(row["id"]))
    return ids


async def withdraw_keywords(ids: list[int]) -> None:
    await execute_sql(
        "UPDATE seo_keywords SET status = 'withdrawn' WHERE id = ANY($1::bigint[])",
        [int(i) for i in ids])


async def keywords(slug: str, limit: int) -> list[str]:
    rows = await fetch_all(
        """SELECT keyword FROM seo_keywords WHERE slug = $1 AND status = 'active'
            ORDER BY at, id LIMIT $2""", slug, int(limit))
    return [str(r["keyword"]) for r in rows]


async def insert_rank(slug: str, keyword: str, provider: str, position: int | None,
                      url: str | None, measured: bool, reason: str | None) -> int:
    row = await fetch_one(
        """INSERT INTO seo_ranks (slug, keyword, provider, position, url, measured,
                                  reason)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
        slug, keyword, provider, position, url, measured, reason)
    return int(row["id"])


async def withdraw_ranks(ids: list[int]) -> None:
    await execute_sql(
        "UPDATE seo_ranks SET status = 'withdrawn' WHERE id = ANY($1::bigint[])",
        [int(i) for i in ids])


async def last_measured_rank_at() -> datetime | None:
    row = await fetch_one("SELECT max(at) AS at FROM seo_ranks WHERE measured "
                          "AND status = 'active'")
    return row["at"] if row is not None else None


# ----------------------------------------------------------------- blog

async def blog_count_month(slug: str) -> int:
    row = await fetch_one(
        """SELECT count(*) AS n FROM content_drafts
            WHERE kind = $1 AND source LIKE $2 AND status <> 'dropped'
              AND at >= date_trunc('month', now())""",
        BLOG_KIND, f"seo:{slug}:%")
    return int(row["n"]) if row is not None else 0


async def blog_counts_month() -> dict[str, int]:
    rows = await fetch_all(
        """SELECT split_part(source, ':', 2) AS slug, count(*) AS n
             FROM content_drafts
            WHERE kind = $1 AND status <> 'dropped'
              AND at >= date_trunc('month', now())
            GROUP BY 1""", BLOG_KIND)
    return {str(r["slug"]): int(r["n"]) for r in rows}


async def insert_draft(slug: str, post_slug: str, title: str, body: str) -> int:
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        BLOG_KIND, f"seo:{slug}:{post_slug}", title, body)
    return int(row["id"])


async def draft(did: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        """SELECT id, kind, source, title, body, status FROM content_drafts
            WHERE id = $1 AND kind = $2""", int(did), BLOG_KIND))


async def set_draft(did: int, status: str) -> None:
    await execute_sql("UPDATE content_drafts SET status = $2 WHERE id = $1 AND kind = $3",
                      int(did), status, BLOG_KIND)


# --------------------------------------------------------------- changes

async def insert_change(slug: str, page: str, spec: dict[str, Any]) -> int:
    row = await fetch_one(
        """INSERT INTO seo_changes (slug, page, spec) VALUES ($1, $2, $3::jsonb)
           RETURNING id""", slug, page, json.dumps(spec))
    return int(row["id"])


async def change(cid: int) -> dict[str, Any] | None:
    row = _row(await fetch_one(
        "SELECT id, slug, page, spec, status, backup FROM seo_changes WHERE id = $1",
        int(cid)))
    if row is not None:
        row["spec"] = _json(row["spec"])
    return row


async def set_change(cid: int, status: str, backup: str | None = None) -> None:
    await execute_sql(
        """UPDATE seo_changes SET status = $2, backup = COALESCE($3, backup),
                  applied_at = CASE WHEN $2 = 'applied' THEN now() ELSE applied_at END
            WHERE id = $1""", int(cid), status, backup)


# ----------------------------------------------------------------- cards

async def raise_card(action: str, params: dict[str, Any], reason: str, client: str,
                     text: str, dedup: str) -> bool:
    from ...kernel.lead_followup import _send_card
    return await _send_card(action, params, reason,
                            {"agent": "SEO", "client": client, "text": text}, dedup)


# ---------------------------------------------------------- observations

async def recent_evidence(check: str, limit: int = 10) -> list[Any]:
    """The evidence of this module's last readings of `check`, newest first.

    The check's own observations are its snapshots: the scheduler records
    them whether or not the executor may act."""
    rows = await fetch_all(
        """SELECT o.evidence FROM observations o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id
            WHERE p.name = 'seo' AND c.name = $1
            ORDER BY o.at DESC LIMIT $2""", check, int(limit))
    return [_json(r["evidence"]) for r in rows]


# --------------------------------------------------------------- reports

REPORT_EMAIL_KIND = "seo_report_email"


async def rank_moves(slug: str, since: datetime) -> list[dict[str, Any]]:
    """Per keyword: the newest measured rank since `since` and the newest
    before it (prior), or None where there is none."""
    rows = await fetch_all(
        """WITH now_ AS (
             SELECT DISTINCT ON (keyword) keyword, position, at FROM seo_ranks
              WHERE slug = $1 AND measured AND status = 'active' AND at >= $2
              ORDER BY keyword, at DESC),
           before_ AS (
             SELECT DISTINCT ON (keyword) keyword, position FROM seo_ranks
              WHERE slug = $1 AND measured AND status = 'active' AND at < $2
              ORDER BY keyword, at DESC)
           SELECT n.keyword, n.position, b.position AS prior, n.at
             FROM now_ n LEFT JOIN before_ b USING (keyword)
            ORDER BY n.keyword""", slug, since)
    return [dict(r) for r in rows]


async def fixes_done(slug: str, since: datetime) -> list[dict[str, Any]]:
    """On-page changes applied and blog posts published since `since`."""
    rows = await fetch_all(
        """SELECT 'on-page change on ' || page AS what, applied_at AS at
             FROM seo_changes
            WHERE slug = $1 AND status = 'applied' AND applied_at >= $2
           UNION ALL
           SELECT 'blog post published: ' || title, at FROM content_drafts
            WHERE kind = $3 AND source LIKE $4 AND status = 'published' AND at >= $2
           ORDER BY at""", slug, since, BLOG_KIND, f"seo:{slug}:%")
    return [dict(r) for r in rows]


async def insert_report(slug: str, kind: str, week: str, body: dict[str, Any],
                        text: str) -> int:
    row = await fetch_one(
        """INSERT INTO seo_reports (slug, kind, week, body, text)
           VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
        slug, kind, week, json.dumps(body, default=str), text)
    return int(row["id"])


async def withdraw_report(rid: int) -> None:
    await execute_sql("UPDATE seo_reports SET status = 'withdrawn' WHERE id = $1",
                      int(rid))


async def latest_report(slug: str, kind: str) -> dict[str, Any] | None:
    row = _row(await fetch_one(
        """SELECT id, at, slug, kind, week, body, text, status FROM seo_reports
            WHERE slug = $1 AND kind = $2 AND status = 'active'
            ORDER BY at DESC, id DESC LIMIT 1""", slug, kind))
    if row is not None:
        row["body"] = _json(row["body"])
    return row


async def insert_email_draft(slug: str, week: str, title: str, body: str) -> int:
    """A draft row only: no send path reads this kind."""
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        REPORT_EMAIL_KIND, f"seo-report:{slug}:{week}", title, body)
    return int(row["id"])


async def drop_email_draft(did: int) -> None:
    await execute_sql(
        "UPDATE content_drafts SET status = 'dropped' WHERE id = $1 AND kind = $2",
        int(did), REPORT_EMAIL_KIND)


async def channel_titles(limit: int = 200) -> list[str]:
    """The Academy channel's (clips) video titles, newest first."""
    rows = await fetch_all(
        """SELECT title FROM youtube_videos WHERE channel = 'clips'
            ORDER BY published_at DESC NULLS LAST LIMIT $1""", int(limit))
    return [str(r["title"]) for r in rows]


async def latest_observation(check: str) -> dict[str, Any] | None:
    """The newest observation of an seo check: health, time, detail, evidence."""
    row = _row(await fetch_one(
        """SELECT o.health, o.at, o.detail, o.evidence FROM observations o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id
            WHERE p.name = 'seo' AND c.name = $1
            ORDER BY o.at DESC LIMIT 1""", check))
    if row is not None:
        row["evidence"] = _json(row["evidence"])
    return row


async def recent_reports(limit: int = 10) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT slug, kind, week, at FROM seo_reports WHERE status = 'active'
            ORDER BY at DESC, id DESC LIMIT $1""", int(limit))
    return [dict(r) for r in rows]
