"""The restore fence (reliability plan R12, docs/restore-drill.md).

A database restored from a backup is older than the world. An outbox row the
dump holds as `pending` or `claimed` may have been sent after the dump was
taken, and a card it holds as `pending` may have been approved and run.
Sending or running either again on the strength of the restored row is the
blind resend the plan forbids.

The real restore procedure (docs/restore-drill.md, "A real restore") records
one `restores` row right after `pg_restore`, with the app stopped:

    INSERT INTO restores (dump, restored_by) VALUES ('atlas-X.dump', 'miro');

`python -m atlas.cli restore-fence <dump>` records the same row and applies
the fence at once. While any `restores` row is unfenced:

* the outbox claims nothing (`outbox._claim` checks it in the same
  statement), and `outbox.dispatch` applies the fence first;
* `approvals.decide` applies the fence before its claim.

Applying it is one statement, so it lands whole or not at all:

* every outbox row `pending`, `claimed` or `dispatching` created before the
  restore goes to `reconciling`, with its lease cleared and its fencing
  token bumped, so it is read back (outbox.reconcile_once) before anything
  is sent: present -> delivered, absent -> pending, unknown -> needs_human;
* every card `pending` created before the restore becomes `held`. A held
  card cannot be approved (the claim wants `pending`); it may already have
  run, so he checks and asks again if it still needs doing. Declining it
  resolves it: a decline causes no effect.

Fail closed: if the marker cannot be read, nothing is dispatched and no card
is decided. Running the fence twice does nothing the second time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger("atlas.restore")

HELD_DETAIL = ("restored from a backup: read back before any send, never "
               "resent blind")

# One statement: every data-modifying CTE runs, or none does. FOR UPDATE on
# the markers serialises two workers; the second finds nothing unfenced.
_FENCE = """
WITH due AS (
    SELECT id, restored_at FROM restores
     WHERE fenced_at IS NULL
     FOR UPDATE),
edge AS (SELECT max(restored_at) AS at FROM due),
rows AS (
    UPDATE outbox
       SET status = 'reconciling', detail = $1, finished_at = now(),
           lease_owner = NULL, lease_expiry = NULL,
           fencing_token = fencing_token + 1
     WHERE status IN ('pending', 'claimed', 'dispatching')
       AND created_at <= (SELECT at FROM edge)
 RETURNING id, workflow_id, kind),
cards AS (
    UPDATE proposals SET status = 'held'
     WHERE status = 'pending'
       AND created_at <= (SELECT at FROM edge)
 RETURNING id, action),
done AS (
    UPDATE restores
       SET fenced_at = now(),
           outbox_held = (SELECT count(*) FROM rows),
           cards_held = (SELECT count(*) FROM cards)
     WHERE id IN (SELECT id FROM due)
 RETURNING id)
SELECT (SELECT count(*) FROM done) AS restores,
       (SELECT coalesce(json_agg(json_build_object(
            'id', id, 'workflow_id', workflow_id, 'kind', kind)), '[]')
          FROM rows) AS outbox,
       (SELECT coalesce(json_agg(json_build_object('id', id, 'action', action)),
                        '[]') FROM cards) AS cards
"""


class FenceUnreadable(Exception):
    """The restore marker could not be read or applied: do not dispatch."""


@dataclass(slots=True)
class Fenced:
    restores: int = 0
    outbox: list[dict] = field(default_factory=list)
    cards: list[dict] = field(default_factory=list)


def _as_list(value: object) -> list[dict]:
    import json
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return list(value or [])


async def mark(dump: str, by: str) -> int:
    """Record that the database was just restored (the procedure's step)."""
    from .state import fetch_one
    row = await fetch_one(
        "INSERT INTO restores (dump, restored_by) VALUES ($1, $2) RETURNING id",
        dump[:300], by[:100])
    return int(row["id"])


async def apply() -> Fenced:
    """Fence every unfenced restore. Raises FenceUnreadable when it cannot
    tell (the caller dispatches and decides nothing)."""
    from .state import fetch_one
    try:
        due = await fetch_one(
            "SELECT EXISTS (SELECT 1 FROM restores WHERE fenced_at IS NULL) AS due")
        if not (due and due["due"]):
            return Fenced()
        row = await fetch_one(_FENCE, HELD_DETAIL)
    except Exception as exc:
        raise FenceUnreadable(type(exc).__name__) from exc
    got = Fenced(int(row["restores"] or 0), _as_list(row["outbox"]),
                 _as_list(row["cards"]))
    if got.restores:
        await _after(got)
    return got


async def _after(got: Fenced) -> None:
    """Audit, move the waiting workflows, and tell Miro once. Best effort:
    the fence itself has already landed."""
    from .audit import audit
    from .outbox import _advance
    try:
        await audit("restore.fenced", "restores", {
            "outbox": [r["id"] for r in got.outbox],
            "cards": [c["id"] for c in got.cards]})
    except Exception:  # noqa: BLE001
        log.warning("restore fence not audited")
    for r in got.outbox:
        if r.get("workflow_id") is not None and r.get("kind") == "effect":
            await _advance(int(r["workflow_id"]), "RECONCILING",
                           f"outbox:{r['id']}")
    if not (got.outbox or got.cards):
        return
    try:
        from . import comms
        from .contracts import Severity
        await comms.notify(comms.Outbound(
            title="Restored from a backup",
            body=(f"Holding {len(got.cards)} card(s) and {len(got.outbox)} queued "
                  f"send(s) from before the restore until they're checked."),
            severity=Severity.HIGH, topic="decisions"))
    except Exception:  # noqa: BLE001
        log.warning("restore fence notice not sent")
