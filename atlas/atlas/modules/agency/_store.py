"""Storage helpers for the agency module.

Thin wrappers over `agency_leads`, `agency_audits`, `agency_spend`,
`agency_mockups`, `agency_outreach`, `agency_suppression` and `agency_sites`
(`db/schema.sql`, phase 12). Nothing here decides anything — `prospect.py`,
`audit.py`, `score.py`, `mockup.py`, `outreach.py`, `build.py` and
`deploy.py` hold the judgement; this file only reads and writes the rows
underneath it, the same seam `mail.py` and `fiverr.py` use around
`atlas.kernel.state`. Task 3 extends this file rather than putting SQL in
four different modules, following the seam Task 2 already established here.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from ...kernel.state import execute_sql, fetch_all, fetch_one


def _row(row: Any) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def parse_json(value: Any, default: Any) -> Any:
    """Read a JSONB column back whether the backend handed us a str or an
    already-decoded dict/list. A value that is neither is not guessed at."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return default


# ------------------------------------------------------------- agency_spend

async def spend_this_month(month: str) -> int:
    row = _row(await fetch_one(
        "SELECT places_requests FROM agency_spend WHERE month=$1", month))
    return int(row["places_requests"]) if row else 0


async def charge(month: str, n: int) -> None:
    await execute_sql(
        """INSERT INTO agency_spend (month, places_requests) VALUES ($1, $2)
           ON CONFLICT (month) DO UPDATE
             SET places_requests = agency_spend.places_requests + $2""",
        month, n)


# ------------------------------------------------------------- agency_leads

async def insert_lead(place: Any, *, distance_km: float | None = None) -> int | None:
    """Insert a new lead. Returns None (no error) when the place is already
    a lead — `place_id` is unique, and a repeat search must not duplicate
    rows or reset a lead someone has already worked."""
    row = _row(await fetch_one(
        """INSERT INTO agency_leads
             (place_id, name, category, address, phone, website, rating,
              reviews, distance_km, business_status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           ON CONFLICT (place_id) DO NOTHING
           RETURNING id""",
        place.place_id, place.name, place.category, place.address,
        place.phone, place.website, place.rating, place.reviews, distance_km,
        getattr(place, "business_status", None)))
    return row["id"] if row else None


async def mark_dropped(lead_ids: list[int]) -> None:
    if not lead_ids:
        return
    await execute_sql(
        "UPDATE agency_leads SET status='dropped' WHERE id = ANY($1)", lead_ids)


async def get_lead(lead_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_leads WHERE id=$1", lead_id))


async def set_lead_meta(lead_id: int, key: str, value: Any) -> None:
    """One key of `agency_leads.meta` (Phase 20), merged — the other keys
    stay. Used for the 30-day reputation cache."""
    await execute_sql(
        "UPDATE agency_leads SET meta = COALESCE(meta, '{}'::jsonb) || $2::jsonb "
        "WHERE id=$1", lead_id, json.dumps({key: value}, default=str))


async def set_lead_status(lead_id: int, status: str) -> None:
    await execute_sql(
        "UPDATE agency_leads SET status=$2 WHERE id=$1", lead_id, status)


async def update_lead_score(lead_id: int, score: float, features: dict) -> None:
    await execute_sql(
        "UPDATE agency_leads SET score=$2, features=$3 WHERE id=$1",
        lead_id, score, json.dumps(features))


async def restore_lead_score(lead_id: int, score: float | None,
                             features: dict) -> None:
    """The `agency.rank` rollback. Restores `features` alongside `score` —
    they were computed together, and a rollback that put the old score back
    next to the new run's features would leave the row internally
    inconsistent with what `agency.rank` actually looked at."""
    await execute_sql(
        "UPDATE agency_leads SET score=$2, features=$3 WHERE id=$1",
        lead_id, score, json.dumps(features))


async def list_non_closed_leads() -> list[dict[str, Any]]:
    rows = await fetch_all("SELECT * FROM agency_leads WHERE status <> 'closed'")
    return [dict(r) for r in rows]


async def count_leads_waiting() -> int:
    row = _row(await fetch_one(
        "SELECT count(*) AS n FROM agency_leads WHERE status IN ('new','audited')"))
    return int(row["n"]) if row else 0


# ------------------------------------------------------------ agency_audits

async def recent_audit(lead_id: int) -> dict[str, Any] | None:
    """The most recent LIVE audit for this lead less than 30 days old, or
    None. `withdraw_audit` sets severity to -1 rather than deleting the row,
    so the rollback stays in the table for history — but a rollback means
    "the last audit result is undone", and a cache that kept answering with
    the withdrawn row would make `agency.audit` silently hand back a
    severity of -1 (or feed it into `score.rank`) for up to 30 days after
    someone rolled it back. Excluding severity < 0 here is what makes a
    rollback actually rollback: the next audit re-fetches."""
    return _row(await fetch_one(
        """SELECT * FROM agency_audits
             WHERE lead_id=$1 AND severity >= 0
               AND at > now() - interval '30 days'
             ORDER BY at DESC LIMIT 1""",
        lead_id))


async def insert_audit(lead_id: int, url: str | None, findings: list[dict],
                       severity: int, measured: dict) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_audits (lead_id, url, findings, severity, measured)
           VALUES ($1,$2,$3,$4,$5) RETURNING id""",
        lead_id, url, json.dumps(findings), severity, json.dumps(measured)))
    return row["id"] if row else None


async def withdraw_audit(audit_id: int) -> None:
    await execute_sql(
        """UPDATE agency_audits
             SET severity=-1,
                 measured = measured || '{"withdrawn": true}'::jsonb
           WHERE id=$1""",
        audit_id)


# ----------------------------------------------------------- agency_mockups

async def insert_mockup(lead_id: int, path: str, screenshot: str | None,
                        layers: list[str]) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_mockups (lead_id, path, screenshot, layers)
           VALUES ($1,$2,$3,$4) RETURNING id""",
        lead_id, path, screenshot, json.dumps(layers)))
    return row["id"] if row else None


async def delete_mockup(mockup_id: int) -> None:
    await execute_sql("DELETE FROM agency_mockups WHERE id=$1", mockup_id)


async def latest_mockup_path(lead_id: int) -> str | None:
    row = _row(await fetch_one(
        """SELECT path FROM agency_mockups WHERE lead_id=$1
           ORDER BY at DESC LIMIT 1""", lead_id))
    return row["path"] if row else None


# ---------------------------------------------------------- agency_outreach

async def insert_outreach(lead_id: int, kind: str, to_address: str | None,
                          subject: str | None, body: str,
                          followup_of: int | None,
                          body_sha: str | None = None) -> int | None:
    """`body_sha` is the SHA-256 of `body` as `draft_outreach` wrote it:
    the unattended send checks the row against it (`outreach.auto_hold`)."""
    row = _row(await fetch_one(
        """INSERT INTO agency_outreach
             (lead_id, kind, to_address, subject, body, followup_of, body_sha)
           VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
        lead_id, kind, to_address, subject, body, followup_of, body_sha))
    return row["id"] if row else None


async def get_outreach(outreach_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_outreach WHERE id=$1", outreach_id))


async def latest_sent_outreach_id(lead_id: int) -> int | None:
    """The most recent non-draft outreach for this lead, if any — a fresh
    `draft_outreach` chains onto it (`followup_of`) rather than always
    writing an unrelated first message."""
    row = _row(await fetch_one(
        """SELECT id FROM agency_outreach
             WHERE lead_id=$1 AND status <> 'draft'
             ORDER BY at DESC LIMIT 1""",
        lead_id))
    return row["id"] if row else None


async def outreach_due_followup(after: timedelta, limit: int = 5
                                ) -> list[dict[str, Any]]:
    """Sent first messages old enough for their one follow-up: no reply
    recorded (status still `sent`), no follow-up or later message to the
    same lead, the address not suppressed, and not claimed by an earlier
    tick. Oldest first."""
    rows = await fetch_all(
        """SELECT o.id, o.lead_id, o.to_address, o.sent_at
             FROM agency_outreach o
            WHERE o.status = 'sent' AND o.followup_of IS NULL
              AND o.followup_claimed_at IS NULL
              AND o.sent_at IS NOT NULL AND o.sent_at < now() - $1::interval
              AND NOT EXISTS (SELECT 1 FROM agency_outreach f
                               WHERE f.lead_id = o.lead_id AND f.id <> o.id
                                 AND f.status <> 'draft' AND f.at > o.at)
              AND NOT EXISTS (SELECT 1 FROM agency_suppression s
                               WHERE s.address = o.to_address)
            ORDER BY o.sent_at LIMIT $2""",
        after, int(limit))
    return [_row(r) for r in rows or []]


async def claim_followup(outreach_id: int) -> bool:
    """Atomic: the first tick to claim a sent message owns its follow-up."""
    row = await fetch_one(
        """UPDATE agency_outreach SET followup_claimed_at = now()
            WHERE id = $1 AND status = 'sent' AND followup_claimed_at IS NULL
        RETURNING id""", int(outreach_id))
    return row is not None


async def release_followup(outreach_id: int) -> None:
    await execute_sql(
        "UPDATE agency_outreach SET followup_claimed_at = NULL WHERE id = $1",
        int(outreach_id))


async def has_sent_followup(lead_id: int, exclude_outreach_id: int) -> bool:
    """Whether this LEAD already has a follow-up that went out — checked
    per lead, not per chain, so a second follow-up chained onto the
    original message (a sibling of the first follow-up, not its child)
    still counts (AGENCY.md §2.5: one follow-up, then stop)."""
    row = _row(await fetch_one(
        """SELECT 1 FROM agency_outreach
             WHERE lead_id=$1 AND followup_of IS NOT NULL
               AND status <> 'draft' AND id <> $2
             LIMIT 1""",
        lead_id, exclude_outreach_id))
    return row is not None


async def delete_outreach(outreach_id: int) -> None:
    """The `agency.draft_outreach` rollback: nothing left the building yet,
    so undoing this run means removing the row it wrote."""
    await execute_sql("DELETE FROM agency_outreach WHERE id=$1", outreach_id)


async def mark_outreach_sent(outreach_id: int, message_id: str | None = None) -> None:
    """`message_id` is the Message-ID `send_outreach` stamped: kept so a
    reply's In-Reply-To / References can be matched back to this row."""
    await execute_sql(
        "UPDATE agency_outreach SET status='sent', sent_at=now(), "
        "message_id=COALESCE($2, message_id) WHERE id=$1",
        outreach_id, message_id)


async def count_sent_between(start: Any, end: Any) -> int:
    """Half-open range, explicit bounds — never a bare `::date` cast, which
    truncates in whatever timezone the database session happens to be in
    rather than the caller's local one (the same reasoning
    `kernel/review.py::_local_day_bounds` documents)."""
    row = _row(await fetch_one(
        """SELECT count(*) AS n FROM agency_outreach
             WHERE sent_at >= $1 AND sent_at < $2""",
        start, end))
    return int(row["n"]) if row else 0


async def list_outreach_needing_attention(limit: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT o.*, l.name AS lead_name FROM agency_outreach o
             JOIN agency_leads l ON l.id = o.lead_id
             WHERE o.status IN ('replied', 'bounced')
             ORDER BY o.at DESC LIMIT $1""",
        limit)
    return [dict(r) for r in rows]


# ------------------------------------------------------- agency_suppression

async def is_suppressed(address: str) -> bool:
    row = _row(await fetch_one(
        "SELECT 1 FROM agency_suppression WHERE address=$1", address))
    return row is not None


async def add_suppression(address: str, reason: str) -> None:
    await execute_sql(
        """INSERT INTO agency_suppression (address, reason) VALUES ($1, $2)
           ON CONFLICT (address) DO UPDATE SET reason = $2""",
        address, reason)


async def remove_suppression(address: str) -> None:
    await execute_sql("DELETE FROM agency_suppression WHERE address=$1", address)


# -------------------------------------------------------------- agency_sites

async def insert_site(lead_id: int, slug: str, domain: str | None,
                      path: str, *, variants: dict | None = None) -> int | None:
    """`ON CONFLICT DO NOTHING`, like `insert_lead`: a repeat build must not
    duplicate the row or reset a site someone has already deployed.
    `variants` is a store build's ranking (Phase 20), None otherwise."""
    row = _row(await fetch_one(
        """INSERT INTO agency_sites (lead_id, slug, domain, path, variants)
           VALUES ($1,$2,$3,$4,$5)
           ON CONFLICT (slug) DO NOTHING
           RETURNING id""",
        lead_id, slug, domain, path,
        None if variants is None else json.dumps(variants)))
    return row["id"] if row else None


async def set_site_variants(slug: str, variants: dict | None) -> None:
    """A rebuild's ranking, or the previous one put back by a rollback."""
    await execute_sql(
        "UPDATE agency_sites SET variants=$2 WHERE slug=$1",
        slug, None if variants is None else json.dumps(variants))


async def get_site(slug: str) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_sites WHERE slug=$1", slug))


async def delete_site(slug: str) -> None:
    await execute_sql("DELETE FROM agency_sites WHERE slug=$1", slug)


async def set_site_deployed(slug: str, previous_release: str | None) -> None:
    await execute_sql(
        """UPDATE agency_sites
             SET deployed_at=now(), previous_release=$2
           WHERE slug=$1""",
        slug, previous_release)


async def set_site_domain(slug: str, domain: str | None) -> None:
    """Where the site is live (a Vercel publish sets its host), or None."""
    await execute_sql(
        "UPDATE agency_sites SET domain=$2 WHERE slug=$1", slug, domain)


async def set_site_undeployed(slug: str) -> None:
    """A first deploy rolled back: nothing of this site is live any more."""
    await execute_sql(
        """UPDATE agency_sites
             SET deployed_at=NULL, previous_release=NULL
           WHERE slug=$1""",
        slug)


async def list_sites() -> list[dict[str, Any]]:
    rows = await fetch_all(
        "SELECT slug, domain, deployed_at, kind, source, managed FROM agency_sites")
    return [dict(r) for r in rows]


async def add_site_row(slug: str, domain: str, kind: str, source: str,
                       path: str) -> int | None:
    """The operator's `sites-add`: a demo or app row with no lead behind
    it. `ON CONFLICT DO NOTHING`: an existing row is never overwritten."""
    row = _row(await fetch_one(
        """INSERT INTO agency_sites (slug, domain, path, kind, source, managed)
           VALUES ($1,$2,$3,$4,$5,$6)
           ON CONFLICT (slug) DO NOTHING
           RETURNING id""",
        slug, domain, path, kind, source, kind == "demo"))
    return row["id"] if row else None


async def record_site_event(slug: str, event: str, detail: str = "",
                            release: str | None = None) -> None:
    await execute_sql(
        """INSERT INTO agency_site_events (slug, event, release, detail)
           VALUES ($1,$2,$3,$4)""",
        slug, event, release, detail[:500])


async def site_health(slug: str) -> dict[str, Any] | None:
    """The newest `agency.site_<slug>` observation (at, health, detail), or
    None when the check has never run."""
    return _row(await fetch_one(
        """SELECT o.at, o.health, o.detail FROM observations o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id
            WHERE p.name = 'agency' AND c.name = $1
            ORDER BY o.at DESC LIMIT 1""",
        f"site_{slug}"))


async def site_events(slug: str, limit: int = 20) -> list[dict[str, Any]]:
    """Newest first."""
    rows = await fetch_all(
        """SELECT at, event, release, detail FROM agency_site_events
            WHERE slug=$1 ORDER BY at DESC, id DESC LIMIT $2""",
        slug, max(1, min(int(limit), 200)))
    return [dict(r) for r in rows]


# ------------------------------------------------------------- agency_webild

async def insert_webild_brief(lead_id: int, read_at: Any, brief: str, brief_sha: str,
                              left_out: list[str]) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_webild (lead_id, read_at, brief, brief_sha, left_out)
           VALUES ($1,$2,$3,$4,$5) RETURNING id""",
        lead_id, read_at, brief, brief_sha, json.dumps(left_out)))
    return row["id"] if row else None


async def delete_webild_draft(brief_id: int) -> bool:
    """Only a brief nobody has approved yet: a queued or published one is
    history, not a draft."""
    row = await fetch_one(
        "DELETE FROM agency_webild WHERE id=$1 AND status='drafted' RETURNING id",
        brief_id)
    return row is not None


async def get_webild(brief_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        """SELECT w.*, l.name AS lead_name FROM agency_webild w
             JOIN agency_leads l ON l.id = w.lead_id WHERE w.id=$1""", brief_id))


async def drafted_webild(max_age: Any) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT w.*, l.name AS lead_name FROM agency_webild w
             JOIN agency_leads l ON l.id = w.lead_id
            WHERE w.status='drafted' AND w.created_at > now() - $1::interval
            ORDER BY w.id""", max_age)
    return [dict(r) for r in rows]


async def expire_webild() -> None:
    await execute_sql(
        """UPDATE agency_webild SET status='expired'
            WHERE status IN ('queued', 'claimed') AND expires_at <= now()""")


async def queue_webild(brief_id: int, lead_id: int, ttl: Any,
                       max_age: Any) -> dict[str, Any] | None:
    """drafted -> queued in one UPDATE, only while the draft is fresh and no
    other brief for the same lead is waiting on the agent."""
    await expire_webild()
    return _row(await fetch_one(
        """UPDATE agency_webild
              SET status='queued', queued_at=now(), expires_at=now() + $3::interval
            WHERE id=$1 AND lead_id=$2 AND status='drafted'
              AND created_at > now() - $4::interval
              AND NOT EXISTS (SELECT 1 FROM agency_webild q
                               WHERE q.lead_id=$2
                                 AND q.status IN ('queued', 'claimed')
                                 AND q.expires_at > now())
        RETURNING id, lead_id, expires_at""", brief_id, lead_id, ttl, max_age))


async def cancel_webild(brief_id: int) -> bool:
    row = await fetch_one(
        """UPDATE agency_webild SET status='cancelled'
            WHERE id=$1 AND status='queued' RETURNING id""", brief_id)
    return row is not None


async def pending_webild() -> list[dict[str, Any]]:
    await expire_webild()
    rows = await fetch_all(
        """SELECT w.id, w.lead_id, w.brief, w.brief_sha, w.read_at, w.queued_at,
                  w.expires_at, l.name AS lead_name
             FROM agency_webild w JOIN agency_leads l ON l.id = w.lead_id
            WHERE w.status='queued' AND w.expires_at > now()
            ORDER BY w.queued_at, w.id""")
    return [dict(r) for r in rows]


async def claim_webild(agent: str) -> dict[str, Any] | None:
    """The oldest live task, queued -> claimed in the same UPDATE that
    checks it has not expired; two runs racing get it once."""
    await expire_webild()
    return _row(await fetch_one(
        """UPDATE agency_webild SET status='claimed', claimed_at=now(), claimed_by=$1
            WHERE id = (SELECT min(id) FROM agency_webild
                         WHERE status='queued' AND expires_at > now())
              AND status='queued' AND expires_at > now()
        RETURNING id, lead_id, brief, brief_sha, read_at, queued_at, expires_at""",
        agent))


async def queued_webild_for(lead_id: int) -> dict[str, Any] | None:
    """The lead's live task, waiting or claimed."""
    return _row(await fetch_one(
        """SELECT * FROM agency_webild
            WHERE lead_id=$1 AND status IN ('queued', 'claimed') AND expires_at > now()
            ORDER BY id DESC LIMIT 1""", lead_id))


async def published_webild_for(lead_id: int, url: str) -> dict[str, Any] | None:
    return _row(await fetch_one(
        """SELECT * FROM agency_webild
            WHERE lead_id=$1 AND status='published' AND url=$2
            ORDER BY id DESC LIMIT 1""", lead_id, url))


async def publish_webild(brief_id: int, url: str) -> bool:
    """queued -> published, in the same UPDATE that checks it has not
    expired: a site reported after its task expired is not recorded."""
    row = await fetch_one(
        """UPDATE agency_webild SET status='published', url=$2, published_at=now()
            WHERE id=$1 AND status IN ('queued', 'claimed') AND expires_at > now()
        RETURNING id""",
        brief_id, url)
    return row is not None


async def set_webild_launch(brief_id: int, launch: dict[str, Any]) -> None:
    await execute_sql(
        "UPDATE agency_webild SET launch=$2, audited_at=now() WHERE id=$1",
        brief_id, json.dumps(launch, default=str))


async def unpublish_webild(brief_id: int, url: str) -> bool:
    row = await fetch_one(
        """UPDATE agency_webild
              SET status='claimed', url=NULL, published_at=NULL, launch=NULL,
                  audited_at=NULL
            WHERE id=$1 AND status='published' AND url=$2 RETURNING id""",
        brief_id, url)
    return row is not None


async def report_webild(lead_id: int, status: str, text: str) -> dict[str, Any] | None:
    """The agent stopped: queued -> failed | needs_miro, with its own words,
    only while the task is still live."""
    return _row(await fetch_one(
        """UPDATE agency_webild SET status=$2, report=$3, reported_at=now()
            WHERE id = (SELECT id FROM agency_webild
                         WHERE lead_id=$1 AND status IN ('queued', 'claimed')
                           AND expires_at > now()
                         ORDER BY id DESC LIMIT 1)
              AND status IN ('queued', 'claimed') AND expires_at > now()
        RETURNING id, lead_id""", lead_id, status, text))


# ------------------------------------------------ lead follow-up (2026-09-29)
#
# agency_lead_messages, agency_lead_consent, agency_contacts,
# agency_reply_drafts and agency_deals (db/schema.sql). The judgement lives
# in leads.py, consent.py, replies.py, sms.py and calls.py.

async def outreach_for_reply(message_ids: list[str],
                             sender: str) -> dict[str, Any] | None:
    """The sent outreach a reply answers: by Message-ID first (In-Reply-To /
    References), then by the address it was sent to. None when it answers
    nothing of ours."""
    ids = [m.strip() for m in message_ids if m and m.strip()]
    if ids:
        row = _row(await fetch_one(
            """SELECT o.*, l.name AS lead_name FROM agency_outreach o
                 JOIN agency_leads l ON l.id = o.lead_id
                WHERE o.message_id = ANY($1::text[])
                ORDER BY o.sent_at DESC NULLS LAST LIMIT 1""", ids))
        if row is not None:
            return row
    if not sender:
        return None
    return _row(await fetch_one(
        """SELECT o.*, l.name AS lead_name FROM agency_outreach o
             JOIN agency_leads l ON l.id = o.lead_id
            WHERE lower(o.to_address) = lower($1) AND o.status IN ('sent', 'replied')
            ORDER BY o.sent_at DESC NULLS LAST LIMIT 1""", sender))


async def mark_outreach_replied(outreach_id: int) -> None:
    await execute_sql(
        "UPDATE agency_outreach SET status='replied' WHERE id=$1 AND status='sent'",
        outreach_id)


async def lead_for_phone(phone: str) -> dict[str, Any] | None:
    """The lead a number belongs to: a consent row's number first (the one
    they gave us), else the listed Places phone by its last ten digits."""
    row = _row(await fetch_one(
        """SELECT l.* FROM agency_lead_consent c JOIN agency_leads l ON l.id = c.lead_id
            WHERE c.phone = $1 ORDER BY c.granted_at DESC LIMIT 1""", phone))
    if row is not None:
        return row
    tail = "".join(ch for ch in phone if ch.isdigit())[-10:]
    if len(tail) < 10:
        return None
    return _row(await fetch_one(
        """SELECT * FROM agency_leads
            WHERE right(regexp_replace(coalesce(phone, ''), '[^0-9]', '', 'g'), 10) = $1
            ORDER BY at DESC LIMIT 1""", tail))


async def insert_lead_message(lead_id: int | None, channel: str, source_id: str,
                              sender: str, subject: str, body: str) -> int | None:
    """None when this provider message was already taken in (idempotent)."""
    row = _row(await fetch_one(
        """INSERT INTO agency_lead_messages (lead_id, channel, source_id, sender,
                                             subject, body)
           VALUES ($1,$2,$3,$4,$5,$6)
           ON CONFLICT (channel, source_id) DO NOTHING RETURNING id""",
        lead_id, channel, source_id, sender, subject, body))
    return row["id"] if row else None


async def lead_message_exists(channel: str, source_id: str) -> bool:
    row = await fetch_one(
        "SELECT 1 FROM agency_lead_messages WHERE channel=$1 AND source_id=$2",
        channel, source_id)
    return row is not None


async def set_message_intent(message_id: int, intent: str | None) -> None:
    await execute_sql(
        "UPDATE agency_lead_messages SET intent=$2 WHERE id=$1", message_id, intent)


async def queue_reply(message_id: int, channel: str) -> None:
    """Ask `kernel.lead_followup` for a drafted reply to this message."""
    await execute_sql(
        "UPDATE agency_lead_messages SET reply_channel=$2 WHERE id=$1",
        message_id, channel)


async def messages_awaiting_reply(limit: int) -> list[dict[str, Any]]:
    """Queued, unclaimed, and under a day old: an older one is Miro's to
    answer, not a draft to spring on him late."""
    rows = await fetch_all(
        """SELECT id, lead_id, reply_channel FROM agency_lead_messages
            WHERE reply_channel IS NOT NULL AND drafted_at IS NULL
              AND lead_id IS NOT NULL AND at > now() - interval '1 day'
            ORDER BY at LIMIT $1""", limit)
    return [dict(r) for r in rows]


async def claim_reply(message_id: int) -> bool:
    row = await fetch_one(
        """UPDATE agency_lead_messages SET drafted_at=now()
            WHERE id=$1 AND drafted_at IS NULL RETURNING id""", message_id)
    return row is not None


async def release_reply(message_id: int) -> None:
    await execute_sql(
        "UPDATE agency_lead_messages SET drafted_at=NULL WHERE id=$1", message_id)


async def mark_message_alerted(message_id: int) -> None:
    await execute_sql(
        "UPDATE agency_lead_messages SET alerted_at=now() WHERE id=$1", message_id)


# 2026-10-06: what a lead's words asked for, decided in code at intake
# (leads.take_in): one offered slot to book, or an allowed price accepted.
# Each is claimed once by its kernel.lead_followup pass, the same atomic
# UPDATE shape as `claim_reply`; a hold releases the claim, a refusal keeps
# it so the same message is not retried every tick.

async def queue_booking(message_id: int, slot: Any) -> None:
    await execute_sql(
        "UPDATE agency_lead_messages SET booking_slot=$2 WHERE id=$1", message_id, slot)


async def bookings_awaiting(limit: int) -> list[dict[str, Any]]:
    """Queued, unclaimed, the message under a day old and the slot still
    ahead: a slot already past is nothing to hold."""
    rows = await fetch_all(
        """SELECT id, lead_id, booking_slot FROM agency_lead_messages
            WHERE booking_slot IS NOT NULL AND booked_at IS NULL
              AND lead_id IS NOT NULL AND at > now() - interval '1 day'
              AND booking_slot > now()
            ORDER BY at LIMIT $1""", limit)
    return [dict(r) for r in rows]


async def claim_booking(message_id: int) -> bool:
    row = await fetch_one(
        """UPDATE agency_lead_messages SET booked_at=now()
            WHERE id=$1 AND booked_at IS NULL RETURNING id""", message_id)
    return row is not None


async def release_booking(message_id: int) -> None:
    await execute_sql(
        "UPDATE agency_lead_messages SET booked_at=NULL WHERE id=$1", message_id)


async def queue_agreement(message_id: int, price: int) -> None:
    await execute_sql(
        "UPDATE agency_lead_messages SET agreement_price=$2 WHERE id=$1",
        message_id, price)


async def agreements_awaiting(limit: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, lead_id, agreement_price FROM agency_lead_messages
            WHERE agreement_price IS NOT NULL AND agreed_at IS NULL
              AND lead_id IS NOT NULL AND at > now() - interval '1 day'
            ORDER BY at LIMIT $1""", limit)
    return [dict(r) for r in rows]


async def claim_agreement(message_id: int) -> bool:
    row = await fetch_one(
        """UPDATE agency_lead_messages SET agreed_at=now()
            WHERE id=$1 AND agreed_at IS NULL RETURNING id""", message_id)
    return row is not None


async def release_agreement(message_id: int) -> None:
    await execute_sql(
        "UPDATE agency_lead_messages SET agreed_at=NULL WHERE id=$1", message_id)


async def latest_lead_message(lead_id: int,
                              channel: str | None = None) -> dict[str, Any] | None:
    if channel is None:
        return _row(await fetch_one(
            """SELECT * FROM agency_lead_messages WHERE lead_id=$1
                ORDER BY at DESC, id DESC LIMIT 1""", lead_id))
    return _row(await fetch_one(
        """SELECT * FROM agency_lead_messages WHERE lead_id=$1 AND channel=$2
            ORDER BY at DESC, id DESC LIMIT 1""", lead_id, channel))


async def get_lead_message(message_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_lead_messages WHERE id=$1", message_id))


async def lead_message_bodies(lead_id: int, limit: int = 20) -> list[str]:
    rows = await fetch_all(
        """SELECT body FROM agency_lead_messages WHERE lead_id=$1
            ORDER BY at DESC LIMIT $2""", lead_id, limit)
    return [str(r["body"] or "") for r in rows]


async def lead_links(lead_id: int) -> dict[str, Any]:
    """What we built for the lead: the site (domain or slug), a published
    Webild URL, and the latest mockup's path. Empty values when none."""
    site = _row(await fetch_one(
        "SELECT slug, domain, deployed_at FROM agency_sites WHERE lead_id=$1 "
        "ORDER BY id DESC LIMIT 1", lead_id)) or {}
    webild = _row(await fetch_one(
        """SELECT url FROM agency_webild WHERE lead_id=$1 AND status='published'
            ORDER BY published_at DESC NULLS LAST LIMIT 1""", lead_id)) or {}
    return {"domain": site.get("domain"), "slug": site.get("slug"),
            "deployed": site.get("deployed_at") is not None,
            "webild_url": webild.get("url"),
            "mockup": await latest_mockup_path(lead_id)}


async def lead_email(lead_id: int) -> str | None:
    """The address to answer by email: the lead's latest email's sender, else
    the address our outreach went to."""
    row = _row(await fetch_one(
        """SELECT sender FROM agency_lead_messages
            WHERE lead_id=$1 AND channel='email' AND sender <> ''
            ORDER BY at DESC LIMIT 1""", lead_id))
    if row and row.get("sender"):
        return str(row["sender"])
    row = _row(await fetch_one(
        """SELECT to_address FROM agency_outreach
            WHERE lead_id=$1 AND to_address IS NOT NULL AND status <> 'draft'
            ORDER BY sent_at DESC NULLS LAST LIMIT 1""", lead_id))
    if row and row.get("to_address"):
        return str(row["to_address"])
    # Last, the address the business publishes on its own site
    # (`agency.find_email`): a lead who asked for a call on the form and
    # agreed a price on it can still be sent the agreement.
    row = _row(await fetch_one(
        """SELECT address FROM agency_lead_emails WHERE lead_id=$1 AND NOT withdrawn
            ORDER BY same_domain DESC, found_at, id LIMIT 1""", lead_id))
    return str(row["address"]) if row else None


# consent

async def active_consent(phone: str, scope: str) -> dict[str, Any] | None:
    return _row(await fetch_one(
        """SELECT * FROM agency_lead_consent
            WHERE phone=$1 AND scope=$2 AND revoked_at IS NULL
            ORDER BY granted_at DESC LIMIT 1""", phone, scope))


async def consent_for_lead(lead_id: int, scope: str) -> dict[str, Any] | None:
    return _row(await fetch_one(
        """SELECT * FROM agency_lead_consent
            WHERE lead_id=$1 AND scope=$2 AND revoked_at IS NULL
            ORDER BY granted_at DESC LIMIT 1""", lead_id, scope))


async def insert_consent(lead_id: int, phone: str, scope: str, channel: str,
                         source_id: str, quoted: str,
                         evidence: dict | None = None) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_lead_consent
             (lead_id, phone, scope, source_channel, source_id, quoted, evidence)
           VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
        lead_id, phone, scope, channel, source_id, quoted,
        None if evidence is None else json.dumps(evidence, default=str)))
    return row["id"] if row else None


async def consents_awaiting_call_card(sources: tuple[str, ...],
                                      within: Any) -> list[dict[str, Any]]:
    """Live call consents from these sources, given within `within`, that
    have had no agency.call_lead card yet (`kernel.lead_followup`)."""
    rows = await fetch_all(
        """SELECT * FROM agency_lead_consent
            WHERE scope='call' AND revoked_at IS NULL AND call_card_at IS NULL
              AND source_channel = ANY($1::text[])
              AND granted_at > now() - $2::interval
            ORDER BY granted_at LIMIT 20""", list(sources), within)
    return [dict(r) for r in rows]


async def get_consent(consent_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_lead_consent WHERE id=$1", consent_id))


async def delete_consent(consent_id: int) -> None:
    """The `agency.record_consent` rollback: the row that run wrote, gone."""
    await execute_sql("DELETE FROM agency_lead_consent WHERE id=$1", consent_id)


async def mark_call_card(consent_id: int, note: str) -> bool:
    """Once per consent: True only for the caller whose UPDATE claimed it."""
    row = await fetch_one(
        """UPDATE agency_lead_consent SET call_card_at=now(), call_card_note=$2
            WHERE id=$1 AND call_card_at IS NULL RETURNING id""", consent_id, note)
    return row is not None


async def note_call_card(consent_id: int, note: str) -> None:
    await execute_sql(
        "UPDATE agency_lead_consent SET call_card_note=$2 WHERE id=$1",
        consent_id, note)


async def release_call_card(consent_id: int) -> None:
    await execute_sql(
        """UPDATE agency_lead_consent SET call_card_at=NULL, call_card_note=NULL
            WHERE id=$1""", consent_id)


# ----------------------------------------------- after a deploy (2026-09-30)

async def claim_deployed_sites(within: Any) -> list[dict[str, Any]]:
    """Sites deployed within `within` that no follow-up has claimed yet,
    claimed in the same UPDATE: each is followed up once."""
    rows = await fetch_all(
        """UPDATE agency_sites SET followed_up_at=now()
            WHERE followed_up_at IS NULL AND deployed_at IS NOT NULL
              AND lead_id IS NOT NULL AND deployed_at > now() - $1::interval
        RETURNING id, slug, lead_id, domain, deployed_at""", within)
    return [dict(r) for r in rows]


async def release_site_followup(slug: str) -> None:
    await execute_sql(
        "UPDATE agency_sites SET followed_up_at=NULL WHERE slug=$1", slug)


async def has_outreach(lead_id: int) -> bool:
    """Outreach that went out (or bounced, or was answered). An old unsent
    draft -- every draft before 2026-09-30 had no recipient -- is not a
    pitch the lead ever saw."""
    row = await fetch_one(
        "SELECT 1 FROM agency_outreach WHERE lead_id=$1 AND status <> 'draft' LIMIT 1",
        lead_id)
    return row is not None


async def consent_phones(lead_id: int) -> list[str]:
    rows = await fetch_all(
        "SELECT DISTINCT phone FROM agency_lead_consent WHERE lead_id=$1", lead_id)
    return [str(r["phone"]) for r in rows]


async def revoke_consent_for_lead(lead_id: int, why: str) -> None:
    await execute_sql(
        """UPDATE agency_lead_consent SET revoked_at=now(), revoked_why=$2
            WHERE lead_id=$1 AND revoked_at IS NULL""", lead_id, why)


async def revoke_consent_for_phone(phone: str, why: str) -> None:
    await execute_sql(
        """UPDATE agency_lead_consent SET revoked_at=now(), revoked_why=$2
            WHERE phone=$1 AND revoked_at IS NULL""", phone, why)


# contacts

async def contacts_since_last_word(lead_id: int) -> int:
    """Texts and calls ATLAS made to this lead that went unanswered: every
    one since the lead's last message, minus calls they picked up."""
    row = _row(await fetch_one(
        """SELECT count(*) AS n FROM agency_contacts c
            WHERE c.lead_id=$1 AND c.status <> 'answered'
              AND c.at > COALESCE((SELECT max(at) FROM agency_lead_messages m
                                    WHERE m.lead_id=$1), '-infinity')""", lead_id))
    return int(row["n"]) if row else 0


async def insert_contact(lead_id: int, channel: str, phone: str, opening: str,
                         body: str, token_sha: str | None = None) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_contacts (lead_id, channel, phone, opening, body, token_sha)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        lead_id, channel, phone, opening, body, token_sha))
    return row["id"] if row else None


async def get_contact(contact_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_contacts WHERE id=$1", contact_id))


async def set_contact(contact_id: int, *, status: str | None = None,
                      sid: str | None = None, transcript: list | None = None,
                      ended: bool = False) -> None:
    await execute_sql(
        """UPDATE agency_contacts
              SET status = COALESCE($2, status), sid = COALESCE($3, sid),
                  transcript = COALESCE($4::jsonb, transcript),
                  ended_at = CASE WHEN $5 THEN now() ELSE ended_at END
            WHERE id=$1""",
        contact_id, status, sid,
        None if transcript is None else json.dumps(transcript), ended)


# reply drafts

async def insert_reply_draft(lead_id: int, in_reply_to: int | None, kind: str,
                             channel: str, to_address: str, subject: str | None,
                             body: str, quoted: list[int],
                             price: int | None = None,
                             status: str = "draft",
                             offered_slots: list[str] | None = None) -> int | None:
    """`offered_slots`: the calendar slots this reply offered, ISO strings
    with offset (2026-10-06); None when it offered none."""
    row = _row(await fetch_one(
        """INSERT INTO agency_reply_drafts (lead_id, in_reply_to, kind, channel,
                 to_address, subject, body, quoted_prices, price, status, sent_at,
                 offered_slots)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,
                   CASE WHEN $10 = 'sent' THEN now() END, $11)
           RETURNING id""",
        lead_id, in_reply_to, kind, channel, to_address, subject, body,
        json.dumps(quoted), price, status,
        None if offered_slots is None else json.dumps(offered_slots)))
    return row["id"] if row else None


async def latest_sent_reply(lead_id: int) -> dict[str, Any] | None:
    """The last reply ATLAS actually sent this lead: the one whose offered
    slots their next message may be answering (2026-10-06)."""
    return _row(await fetch_one(
        """SELECT * FROM agency_reply_drafts
            WHERE lead_id=$1 AND kind='reply' AND status='sent'
            ORDER BY sent_at DESC NULLS LAST, id DESC LIMIT 1""", lead_id))


async def get_reply_draft(draft_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_reply_drafts WHERE id=$1", draft_id))


async def discard_reply_draft(draft_id: int) -> None:
    """The `agency.draft_reply` rollback: only a draft nobody sent."""
    await execute_sql(
        "DELETE FROM agency_reply_drafts WHERE id=$1 AND status='draft'", draft_id)


async def mark_reply_sent(draft_id: int) -> bool:
    row = await fetch_one(
        """UPDATE agency_reply_drafts SET status='sent', sent_at=now()
            WHERE id=$1 AND status='draft' RETURNING id""", draft_id)
    return row is not None


# deals

async def count_closed_deals() -> int:
    """Won or paid deals on record, plus Stripe payments that matched no
    lead (a sale is a sale even when ATLAS cannot say whose). Raises when
    unreadable -- the caller must not read a failure as zero."""
    row = _row(await fetch_one(
        """SELECT (SELECT count(*) FROM agency_deals WHERE status IN ('won', 'paid'))
                + (SELECT count(*) FROM agency_payments
                    WHERE lead_id IS NULL AND status = 'paid') AS n"""))
    if row is None:
        raise RuntimeError("agency_deals could not be read")
    return int(row["n"])


async def lead_returning(lead_id: int) -> bool:
    """A returning client: a won or paid deal on THIS lead, or a lead Miro
    linked to an earlier client (`returning_of_lead_id`). Never an email
    domain. Raises when unreadable -- the caller must not read a failure as
    new."""
    row = _row(await fetch_one(
        """SELECT (l.returning_of_lead_id IS NOT NULL
                   OR EXISTS (SELECT 1 FROM agency_deals d
                               WHERE d.lead_id = l.id
                                 AND d.status IN ('won', 'paid'))) AS returning
             FROM agency_leads l WHERE l.id = $1""", int(lead_id)))
    if row is None:
        raise RuntimeError(f"lead {lead_id} could not be read")
    return bool(row["returning"])


async def insert_deal(lead_id: int, status: str, amount: int | None,
                      note: str) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_deals (lead_id, status, amount, note)
           VALUES ($1,$2,$3,$4) RETURNING id""", lead_id, status, amount, note))
    return row["id"] if row else None


async def undo_deal(deal_id: int) -> None:
    """The `agency.record_deal` rollback: the row this run wrote."""
    await execute_sql("DELETE FROM agency_deals WHERE id=$1", deal_id)


# the board

async def lead_board() -> dict[str, Any]:
    """Counts by stage, all read in one pass. Raises when unreadable."""
    stages = await fetch_all(
        "SELECT status, count(*) AS n FROM agency_leads GROUP BY status")
    outreach = await fetch_all(
        """SELECT status, count(*) AS n FROM agency_outreach
            WHERE status <> 'draft' GROUP BY status""")
    intents = await fetch_all(
        """SELECT COALESCE(intent, 'unclassified') AS intent, count(*) AS n
             FROM agency_lead_messages WHERE at > now() - interval '24 hours'
            GROUP BY 1""")
    consent = _row(await fetch_one(
        """SELECT count(DISTINCT lead_id) AS n FROM agency_lead_consent
            WHERE revoked_at IS NULL""")) or {"n": 0}
    contacts = await fetch_all(
        """SELECT channel, count(*) AS n FROM agency_contacts
            WHERE at > now() - interval '24 hours' GROUP BY channel""")
    deals = await fetch_all(
        "SELECT status, count(*) AS n FROM agency_deals GROUP BY status")
    sites = _row(await fetch_one("SELECT count(*) AS n FROM agency_sites")) or {"n": 0}
    return {
        "leads": {str(r["status"]): int(r["n"]) for r in stages},
        "outreach": {str(r["status"]): int(r["n"]) for r in outreach},
        "replies_24h": {str(r["intent"]): int(r["n"]) for r in intents},
        "consented_leads": int(consent["n"]),
        "contacts_24h": {str(r["channel"]): int(r["n"]) for r in contacts},
        "deals": {str(r["status"]): int(r["n"]) for r in deals},
        "sites": int(sites["n"]),
    }


# ----------------------------------------------- verification (2026-09-30)

async def latest_verification(lead_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        """SELECT * FROM agency_verifications
            WHERE lead_id=$1 AND NOT withdrawn
            ORDER BY at DESC, id DESC LIMIT 1""", lead_id))


async def insert_verification(lead_id: int, verdict: str, reasons: list[str],
                              checks: dict[str, Any], answer: str,
                              sources: list[str], read_at: Any,
                              previous_status: str | None) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_verifications (lead_id, verdict, reasons, checks,
                 answer, sources, read_at, previous_status)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
        lead_id, verdict, json.dumps(reasons), json.dumps(checks, default=str),
        answer, json.dumps(sources), read_at, previous_status))
    return row["id"] if row else None


async def get_verification(verification_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM agency_verifications WHERE id=$1", verification_id))


async def withdraw_verification(verification_id: int) -> None:
    """The `agency.verify` rollback: the row stays for history, withdrawn."""
    await execute_sql(
        "UPDATE agency_verifications SET withdrawn=TRUE WHERE id=$1", verification_id)


async def verify_calls_since(since: Any) -> int:
    """Perplexity calls the verifier made since `since`. Raises when
    unreadable: a budget that cannot be read spends nothing."""
    row = _row(await fetch_one(
        """SELECT count(*) AS n FROM perplexity_calls
            WHERE caller='agency-verify' AND at >= $1""", since))
    if row is None:
        raise RuntimeError("perplexity_calls could not be read")
    return int(row["n"])


async def verdicts_for(lead_ids: list[int]) -> dict[int, str]:
    """Each lead's live verdict (verified | disqualified | unknown); a lead
    never verified is absent."""
    rows = await fetch_all(
        """SELECT DISTINCT ON (lead_id) lead_id, verdict FROM agency_verifications
            WHERE lead_id = ANY($1) AND NOT withdrawn
            ORDER BY lead_id, at DESC, id DESC""", lead_ids)
    return {int(r["lead_id"]): str(r["verdict"]) for r in rows}


# --------------------------------------------------- meetings (2026-09-30)

async def insert_meeting(lead_id: int, event_id: str, start: Any, end: Any,
                         email: str | None, call_sid: str | None) -> int | None:
    row = _row(await fetch_one(
        """INSERT INTO agency_lead_meetings (lead_id, event_id, start_at, end_at,
                                             email, call_sid)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        lead_id, event_id, start, end, email, call_sid))
    return row["id"] if row else None


async def meeting_at(lead_id: int, start: Any) -> dict[str, Any] | None:
    """A hold or an invite already on the calendar for this lead at this
    slot, so a booking pass that was held after the hold does not hold the
    same half-hour twice (2026-10-06)."""
    return _row(await fetch_one(
        """SELECT * FROM agency_lead_meetings
            WHERE lead_id=$1 AND start_at=$2 AND status <> 'released'
            ORDER BY id DESC LIMIT 1""", lead_id, start))


async def get_meeting(meeting_id: int) -> dict[str, Any] | None:
    return _row(await fetch_one(
        """SELECT m.*, l.name AS lead_name FROM agency_lead_meetings m
             JOIN agency_leads l ON l.id = m.lead_id WHERE m.id=$1""", meeting_id))


async def release_meeting(event_id: str) -> None:
    await execute_sql(
        "UPDATE agency_lead_meetings SET status='released' WHERE event_id=$1",
        event_id)


async def mark_meeting_invited(meeting_id: int) -> bool:
    row = await fetch_one(
        """UPDATE agency_lead_meetings SET status='invited', invited_at=now()
            WHERE id=$1 AND status='held' RETURNING id""", meeting_id)
    return row is not None


async def claim_reminders(within: Any) -> list[dict[str, Any]]:
    """Meetings starting within `within` that have had no reminder: claimed
    in the same UPDATE, so each is reminded once."""
    rows = await fetch_all(
        """UPDATE agency_lead_meetings m SET reminded_at=now()
             FROM agency_leads l
            WHERE l.id = m.lead_id AND m.status <> 'released'
              AND m.reminded_at IS NULL AND m.start_at > now()
              AND m.start_at <= now() + $1::interval
        RETURNING m.*, l.name AS lead_name""", within)
    return [dict(r) for r in rows]


# ------------------------------------------------ Stripe payments (2026-09-30)

async def claim_stripe_event(event_id: str, kind: str) -> bool:
    """True the first time this Stripe event id is seen; a replay is False."""
    row = await fetch_one(
        """INSERT INTO agency_stripe_events (event_id, type) VALUES ($1, $2)
           ON CONFLICT (event_id) DO NOTHING RETURNING event_id""", event_id, kind)
    return row is not None


async def release_stripe_event(event_id: str) -> None:
    """Processing failed after the claim: let Stripe's retry run it again."""
    await execute_sql("DELETE FROM agency_stripe_events WHERE event_id=$1", event_id)


async def lead_id_for_email(email: str) -> int | None:
    """The lead an address belongs to: one they wrote from, one our outreach
    went to, or one a meeting was booked with. None when none matches."""
    row = _row(await fetch_one(
        """SELECT lead_id FROM (
             SELECT lead_id, at FROM agency_lead_messages
              WHERE channel='email' AND lower(sender)=lower($1) AND lead_id IS NOT NULL
             UNION ALL
             SELECT lead_id, at FROM agency_outreach WHERE lower(to_address)=lower($1)
             UNION ALL
             SELECT lead_id, at FROM agency_lead_meetings WHERE lower(email)=lower($1)
           ) m ORDER BY at DESC LIMIT 1""", email))
    return int(row["lead_id"]) if row else None


async def insert_payment(**row: Any) -> int | None:
    got = _row(await fetch_one(
        """INSERT INTO agency_payments (event_id, source, object_id, payment_intent,
                 charge, lead_id, matched_by, email, amount_minor, currency, deal_id)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id""",
        row["event_id"], row["source"], row["object_id"], row.get("payment_intent"),
        row.get("charge"), row.get("lead_id"), row["matched_by"], row.get("email"),
        row["amount_minor"], row["currency"], row.get("deal_id")))
    return got["id"] if got else None


async def insert_paid_deal(lead_id: int, amount_minor: int, currency: str,
                           note: str) -> int | None:
    whole = amount_minor // 100 if amount_minor % 100 == 0 else None
    got = _row(await fetch_one(
        """INSERT INTO agency_deals (lead_id, status, amount, note, amount_minor, currency)
           VALUES ($1, 'paid', $2, $3, $4, $5) RETURNING id""",
        lead_id, whole, note, amount_minor, currency))
    return got["id"] if got else None


async def payment_for(payment_intent: str | None, charge: str | None
                      ) -> dict[str, Any] | None:
    if not (payment_intent or charge):
        return None
    return _row(await fetch_one(
        """SELECT p.*, l.name AS lead_name FROM agency_payments p
             LEFT JOIN agency_leads l ON l.id = p.lead_id
            WHERE ($1::text IS NOT NULL AND p.payment_intent = $1)
               OR ($2::text IS NOT NULL AND p.charge = $2)
            ORDER BY p.at DESC LIMIT 1""", payment_intent, charge))


async def set_payment_status(payment_id: int, status: str,
                             refunded_minor: int | None = None) -> None:
    await execute_sql(
        """UPDATE agency_payments SET status=$2,
                  refunded_minor=COALESCE($3, refunded_minor) WHERE id=$1""",
        payment_id, status, refunded_minor)


async def set_deal_status(deal_id: int, status: str) -> None:
    await execute_sql("UPDATE agency_deals SET status=$2 WHERE id=$1", deal_id, status)


# ------------------------------------------------------------ agency_skills

async def active_skills() -> list[dict[str, Any]]:
    """The approved skill texts, for `kernel.skills.load_dynamic`."""
    rows = await fetch_all(
        "SELECT name, triggers, body FROM agency_skills WHERE status = 'active' "
        "ORDER BY name")
    return [dict(r) for r in rows]


async def activate_skill(name: str, triggers: list[str], body: str) -> int:
    """Store an approved skill as active (an approved card is the only caller)."""
    row = await fetch_one(
        """INSERT INTO agency_skills (name, triggers, body, status, approved_at)
           VALUES ($1, $2, $3, 'active', now())
           ON CONFLICT (name) DO UPDATE
              SET triggers = EXCLUDED.triggers, body = EXCLUDED.body,
                  status = 'active', approved_at = now()
        RETURNING id""", name, triggers, body)
    return int(row["id"])


async def retire_skill(name: str) -> bool:
    """active -> retired. The row stays; nothing is deleted."""
    row = await fetch_one(
        """UPDATE agency_skills SET status = 'retired'
            WHERE name = $1 AND status = 'active' RETURNING id""", name)
    return row is not None
