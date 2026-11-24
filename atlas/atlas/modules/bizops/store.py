"""BizOps rows: onboardings, test-mode invoices, reminders, automation ideas.

Every function is one query; the rules live in the modules that call them.
`raise_card` is here too, so the tests replace one module for all of it."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...kernel.state import execute_sql, fetch_all, fetch_one

OPEN = ("drafted", "sent")


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# ----------------------------------------------------------------- leads

async def lead_name(lead_id: int) -> str | None:
    row = await fetch_one("SELECT name FROM agency_leads WHERE id = $1", int(lead_id))
    return str(row["name"]) if row is not None and row["name"] else None


async def first_closed_deal(lead_id: int) -> dict[str, Any] | None:
    """The earliest won or paid deal: one stable payment reference per lead."""
    return _row(await fetch_one(
        """SELECT id, status, amount FROM agency_deals
            WHERE lead_id = $1 AND status IN ('won', 'paid')
            ORDER BY at, id LIMIT 1""", int(lead_id)))


async def has_won(lead_id: int) -> bool:
    row = await fetch_one("SELECT 1 FROM agency_deals WHERE lead_id = $1 AND "
                          "status = 'won' LIMIT 1", int(lead_id))
    return row is not None


async def won_amount(lead_id: int) -> int | None:
    row = await fetch_one(
        """SELECT amount FROM agency_deals WHERE lead_id = $1 AND status = 'won'
            ORDER BY at DESC, id DESC LIMIT 1""", int(lead_id))
    return int(row["amount"]) if row is not None and row["amount"] is not None else None


async def deal_paid(lead_id: int) -> bool:
    row = await fetch_one("SELECT 1 FROM agency_deals WHERE lead_id = $1 AND "
                          "status = 'paid' LIMIT 1", int(lead_id))
    return row is not None


async def agreement_sent(lead_id: int) -> bool:
    row = await fetch_one(
        """SELECT 1 FROM agency_reply_drafts WHERE lead_id = $1
              AND kind = 'agreement' AND status = 'sent' LIMIT 1""", int(lead_id))
    return row is not None


async def leads_to_onboard() -> list[int]:
    """Leads with a won or paid deal in the last 30 days and no onboarding."""
    rows = await fetch_all(
        """SELECT DISTINCT d.lead_id FROM agency_deals d
            WHERE d.status IN ('won', 'paid') AND d.at > now() - interval '30 days'
              AND NOT EXISTS (SELECT 1 FROM bizops_onboardings o
                               WHERE o.lead_id = d.lead_id)
            ORDER BY d.lead_id""")
    return [int(r["lead_id"]) for r in rows]


# ------------------------------------------------------------ onboarding

async def onboarding_for_ref(ref: str) -> dict[str, Any] | None:
    return _row(await fetch_one(
        "SELECT * FROM bizops_onboardings WHERE payment_ref = $1", ref))


async def insert_onboarding(*, lead_id: int, payment_ref: str, to_address: str,
                            subject: str, body: str, kickoff_slots: list[str]
                            ) -> int | None:
    """The new id, or None when the payment reference already has one."""
    row = await fetch_one(
        """INSERT INTO bizops_onboardings (lead_id, payment_ref, to_address, subject,
                                           body, kickoff_slots)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)
           ON CONFLICT (payment_ref) DO NOTHING RETURNING id""",
        int(lead_id), payment_ref, to_address, subject, body, json.dumps(kickoff_slots))
    return int(row["id"]) if row is not None else None


async def attach_project(oid: int, project_id: int, tokens: list[str]) -> None:
    await execute_sql(
        """UPDATE bizops_onboardings SET project_id = $2, history_tokens = $3::jsonb
            WHERE id = $1""", int(oid), int(project_id), json.dumps(tokens))


async def onboarding(oid: int) -> dict[str, Any] | None:
    row = _row(await fetch_one("SELECT * FROM bizops_onboardings WHERE id = $1",
                               int(oid)))
    if row is not None and isinstance(row.get("history_tokens"), str):
        row["history_tokens"] = json.loads(row["history_tokens"])
    return row


async def set_onboarding(oid: int, status: str) -> None:
    await execute_sql(
        """UPDATE bizops_onboardings SET status = $2,
                  sent_at = CASE WHEN $2 = 'sent' THEN now() ELSE sent_at END
            WHERE id = $1""", int(oid), status)


async def stalled_onboardings(days: int) -> int:
    row = await fetch_one(
        """SELECT count(*) AS n FROM bizops_onboardings
            WHERE status = 'drafted' AND at < now() - make_interval(days => $1)""",
        int(days))
    return int(row["n"]) if row is not None else 0


# --------------------------------------------------------------- invoices

async def open_invoice_for(lead_id: int) -> int | None:
    row = await fetch_one(
        "SELECT id FROM bizops_invoices WHERE lead_id = $1 AND status = ANY($2::text[])",
        int(lead_id), list(OPEN))
    return int(row["id"]) if row is not None else None


async def paid_invoice_for(lead_id: int) -> int | None:
    row = await fetch_one(
        """SELECT id FROM bizops_invoices WHERE lead_id = $1 AND status = 'paid'
            ORDER BY id LIMIT 1""", int(lead_id))
    return int(row["id"]) if row is not None else None


async def insert_invoice(lead_id: int, amount: int, to_address: str, memo: str) -> int:
    """The id; the idempotency key is `bizops-inv-<id>`, written in the same
    statement. The CHECK and the one-open-per-lead index refuse the rest."""
    row = await fetch_one(
        """WITH n AS (SELECT nextval(pg_get_serial_sequence('bizops_invoices', 'id'))
                             AS id)
           INSERT INTO bizops_invoices (id, lead_id, amount, to_address, memo,
                                        livemode, idempotency_key)
           SELECT n.id, $1, $2, $3, $4, false, 'bizops-inv-' || n.id FROM n
           RETURNING id""", int(lead_id), int(amount), to_address, memo)
    if row is None:
        raise RuntimeError("the invoice row was not written")
    return int(row["id"])


async def invoice(iid: int) -> dict[str, Any] | None:
    return _row(await fetch_one("SELECT * FROM bizops_invoices WHERE id = $1",
                                int(iid)))


_INVOICE_FIELDS = frozenset({"status", "stripe_customer", "stripe_invoice",
                             "hosted_url", "livemode", "sent_at", "due_at",
                             "paid_at", "polled_at"})


async def update_invoice(iid: int, **fields: Any) -> None:
    bad = set(fields) - _INVOICE_FIELDS
    if bad:
        raise ValueError(f"not an invoice field: {sorted(bad)}")
    if not fields:
        return
    names = sorted(fields)
    sets = ", ".join(f"{n} = ${i + 2}" for i, n in enumerate(names))
    await execute_sql(f"UPDATE bizops_invoices SET {sets} WHERE id = $1",
                      int(iid), *[fields[n] for n in names])


async def open_invoices() -> list[dict[str, Any]]:
    return [dict(r) for r in await fetch_all(
        "SELECT * FROM bizops_invoices WHERE status = 'sent' ORDER BY id")]


async def overdue_invoices() -> list[dict[str, Any]]:
    return [dict(r) for r in await fetch_all(
        """SELECT * FROM bizops_invoices
            WHERE status = 'sent' AND due_at < now() ORDER BY id""")]


# -------------------------------------------------------------- reminders

async def last_reminder_at(invoice_id: int, *, sent_only: bool) -> datetime | None:
    row = await fetch_one(
        """SELECT max(at) AS at FROM bizops_reminders
            WHERE invoice_id = $1 AND status <> 'withdrawn'
              AND ($2 = false OR status = 'sent')""", int(invoice_id), sent_only)
    return row["at"] if row is not None else None


async def insert_reminder(invoice_id: int, to_address: str, subject: str,
                          body: str) -> int:
    row = await fetch_one(
        """INSERT INTO bizops_reminders (invoice_id, to_address, subject, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        int(invoice_id), to_address, subject, body)
    if row is None:
        raise RuntimeError("the reminder row was not written")
    return int(row["id"])


async def reminder(rid: int) -> dict[str, Any] | None:
    return _row(await fetch_one("SELECT * FROM bizops_reminders WHERE id = $1",
                                int(rid)))


async def set_reminder(rid: int, status: str) -> None:
    await execute_sql(
        """UPDATE bizops_reminders SET status = $2,
                  sent_at = CASE WHEN $2 = 'sent' THEN now() ELSE sent_at END
            WHERE id = $1""", int(rid), status)


# ------------------------------------------------------------------ ideas

async def recent_texts(days: int) -> list[tuple[str, str]]:
    """(source, text): subjects of mail Miro sent and board task titles."""
    mail = await fetch_all(
        """SELECT subject FROM mail_followups
            WHERE sent_at > now() - make_interval(days => $1)""", int(days))
    tasks = await fetch_all(
        """SELECT title FROM project_tasks
            WHERE created_at > now() - make_interval(days => $1)
              AND kind <> 'remediation'""", int(days))
    return ([("mail subject", str(r["subject"])) for r in mail]
            + [("board task", str(r["title"])) for r in tasks])


async def upsert_idea(pattern: str, samples: list[str], count: int) -> tuple[int, bool]:
    """(id, created now)."""
    row = await fetch_one(
        """INSERT INTO bizops_automation_ideas (pattern, samples, count)
           VALUES ($1, $2::jsonb, $3)
           ON CONFLICT (pattern) DO UPDATE SET count = EXCLUDED.count,
                                               samples = EXCLUDED.samples
           RETURNING id, (xmax = 0) AS created""",
        pattern, json.dumps(samples), int(count))
    if row is None:
        raise RuntimeError("the idea row was not written")
    return int(row["id"]), bool(row["created"])


async def idea(iid: int) -> dict[str, Any] | None:
    return _row(await fetch_one("SELECT * FROM bizops_automation_ideas WHERE id = $1",
                                int(iid)))


async def set_idea(iid: int, status: str, task_id: int | None = None,
                   task_token: str | None = None) -> None:
    await execute_sql(
        """UPDATE bizops_automation_ideas SET status = $2, task_id = $3,
                  task_token = $4 WHERE id = $1""",
        int(iid), status, task_id, task_token)


async def set_ideas_status(ids: list[int], status: str) -> None:
    await execute_sql(
        "UPDATE bizops_automation_ideas SET status = $2 WHERE id = ANY($1::bigint[])",
        [int(i) for i in ids], status)


# ----------------------------------------------------------------- digest

async def digest_reads() -> dict[str, int]:
    row = await fetch_one(
        """SELECT
             (SELECT count(*) FROM bizops_invoices WHERE status = 'sent') AS open,
             (SELECT coalesce(sum(amount), 0) FROM bizops_invoices
               WHERE status = 'sent') AS open_total,
             (SELECT count(*) FROM bizops_invoices
               WHERE status = 'sent' AND due_at < now()) AS overdue,
             (SELECT count(*) FROM bizops_invoices
               WHERE status = 'paid' AND paid_at > now() - interval '1 day') AS paid_day,
             (SELECT count(*) FROM bizops_onboardings
               WHERE status = 'drafted') AS onboard_waiting,
             (SELECT count(*) FROM bizops_automation_ideas
               WHERE status = 'new') AS ideas_new""")
    if row is None:
        raise RuntimeError("the digest read returned nothing")
    return {k: int(row[k]) for k in ("open", "open_total", "overdue", "paid_day",
                                     "onboard_waiting", "ideas_new")}


async def insert_report(title: str, body: str, sections: list[dict[str, Any]]) -> int:
    row = await fetch_one(
        """INSERT INTO reports (kind, title, body, sections, modules)
           VALUES ('bizops_digest', $1, $2, $3::jsonb, $4) RETURNING id""",
        title, body, json.dumps(sections, default=str), ["bizops"])
    if row is None:
        raise RuntimeError("the report row was not written")
    return int(row["id"])


async def withdraw_report(rid: int) -> None:
    await execute_sql("UPDATE reports SET kind = 'withdrawn' WHERE id = $1 "
                      "AND kind = 'bizops_digest'", int(rid))


# ------------------------------------------------------------------ cards

async def raise_card(action: str, params: dict[str, Any], reason: str, client: str,
                     text: str, dedup: str) -> bool:
    from ...kernel.lead_followup import _send_card
    return await _send_card(action, params, reason,
                            {"agent": "BizOps", "client": client, "text": text}, dedup)
