"""Delegate — hand a piece of work to the coding agent on the owner's Mac.

What it watches: the handoff queue itself. A task approved but never claimed
means the agent's scheduled run is not running, and that is worth saying
before he wonders why nothing happened.

What it may do: one thing, `delegate.claude_code`, which moves a brief ATLAS
drafted (`kernel.solve`) from `draft` to `queued`. It runs nothing. The agent
that claims the row has a shell, a filesystem and a browser, so:

* Tier ASK, and `card_only`: the action is never offered to a model. The
  brief is the capability; the only brief that can be queued is one ATLAS
  wrote from his request and put on a card he approved. A model picking this
  action from a catalog and naming a draft would be approval by paraphrase.
* A draft older than two days is refused: he approved a reading of the
  world, and two days later that reading is not the world.
* An approved task that is not claimed within a day expires. Silence is
  never consent, and a queue that grows while nobody reads it is a queue of
  surprises.

Endpoints assumed: none outside. The agent reaches ATLAS over the mesh at
`/handoffs/claim` and `/handoffs/{id}/report` (atlas/api/handoffs.py) with a
bearer token.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)

log = logging.getLogger("atlas.delegate")

# Every window is passed as a timedelta and cast to ::interval in the SQL.
# Uncast, Postgres typed the window parameter as a TIMESTAMP, and the
# first approved card failed with "operator does not exist: timestamp with
# time zone > interval" (#192, 2026-09-28); the queue check failed the same
# way and filed an issue about itself.

DRAFT_TTL = timedelta(days=2)
CLAIM_TTL = timedelta(days=1)
# A claimed task with no report after this long: the agent died mid-task or
# never reports back. Either way he should hear it from ATLAS, not find it.
REPORT_TTL = timedelta(hours=6)
MAX_BRIEF = 12_000


def _hid(value: object) -> int | None:
    try:
        hid = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return hid if hid > 0 else None


async def draft(*, request: str, brief: str, target: str, topic: str,
                channel: str = "", thread: str = "") -> int:
    """Store a brief ATLAS wrote. Nothing is queued until its card is
    approved; this is the only writer of `brief`."""
    from ...kernel.state import fetch_one

    row = await fetch_one(
        """INSERT INTO handoffs (request, brief, target, topic, channel, thread)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
        request[:2000], brief[:MAX_BRIEF], target[:500], topic or "decisions",
        channel[:40], thread[:40])
    return int(row["id"])


async def queue_dry(handoff_id: str = "", topic: str = "") -> ActionResult:
    from ...kernel.state import fetch_one

    hid = _hid(handoff_id)
    if hid is None:
        return ActionResult(False, f"not a handoff id: {handoff_id!r}")
    try:
        row = await fetch_one(
            "SELECT status, created_at, target FROM handoffs WHERE id = $1", hid)
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"cannot read the handoff queue: {type(exc).__name__}")
    if row is None:
        return ActionResult(False, f"there is no handoff #{hid}")
    if row["status"] != "draft":
        return ActionResult(False, f"handoff #{hid} is {row['status']}, not a draft")
    if datetime.now(UTC) - row["created_at"] > DRAFT_TTL:
        return ActionResult(False, f"handoff #{hid} was drafted more than two days "
                                   f"ago; ask again so it is written from today")
    return ActionResult(
        True, f"would queue handoff #{hid} ({row['target']}) for the coding agent "
              f"on the owner's Mac; it is claimed on the agent's next scheduled run or "
              f"expires unclaimed in {int(CLAIM_TTL.total_seconds() // 3600)}h")


async def queue(handoff_id: str = "", topic: str = "") -> ActionResult:
    """draft -> queued, atomically, and only while still fresh."""
    from ...kernel.state import fetch_one

    hid = _hid(handoff_id)
    if hid is None:
        return ActionResult(False, f"not a handoff id: {handoff_id!r}")
    row = await fetch_one(
        """UPDATE handoffs
              SET status = 'queued', queued_at = now(), expires_at = now() + $2::interval
            WHERE id = $1 AND status = 'draft' AND created_at > now() - $3::interval
        RETURNING id, target""", hid, CLAIM_TTL, DRAFT_TTL)
    if row is None:
        return ActionResult(False, f"handoff #{hid} is no longer a fresh draft; "
                                   f"nothing was queued")
    return ActionResult(
        True, f"queued handoff #{hid} for the coding agent ({row['target']}). "
              f"Its report comes back to this thread.",
        evidence={"handoff_id": hid}, rollback_token=str(hid))


async def cancel(token: str) -> ActionResult:
    """Take a queued task back before the agent claims it. After a claim
    the agent is already working; cancelling then would be a promise this
    cannot keep, so it says so."""
    from ...kernel.state import fetch_one

    hid = _hid(token)
    if hid is None:
        return ActionResult(False, f"not a handoff id: {token!r}")
    row = await fetch_one(
        """UPDATE handoffs SET status = 'cancelled'
            WHERE id = $1 AND status = 'queued' RETURNING id""", hid)
    if row is None:
        return ActionResult(False, f"handoff #{hid} was already claimed or closed; "
                                   f"it cannot be taken back from here")
    return ActionResult(True, f"handoff #{hid} cancelled before the agent claimed it")


async def check_queue() -> Observation:
    """Approved work nobody picked up, or picked up and never reported."""
    from ...kernel.state import fetch_one

    try:
        await fetch_one(
            """UPDATE handoffs SET status = 'expired'
                WHERE status = 'queued' AND expires_at <= now() RETURNING id""")
        row = await fetch_one(
            """SELECT
                 count(*) FILTER (WHERE status = 'queued')              AS queued,
                 min(queued_at) FILTER (WHERE status = 'queued')        AS oldest,
                 count(*) FILTER (WHERE status = 'claimed'
                                  AND claimed_at < now() - $1::interval)          AS silent,
                 count(*) FILTER (WHERE status = 'expired'
                                  AND expires_at > now() - interval '1 day') AS expired
               FROM handoffs""", REPORT_TTL)
    except Exception as exc:                            # noqa: BLE001
        return Observation("queue", Health.UNKNOWN,
                           detail=f"cannot read the handoff queue: {type(exc).__name__}")
    queued, silent, expired = int(row["queued"]), int(row["silent"]), int(row["expired"])
    oldest = row["oldest"]
    waiting = (datetime.now(UTC) - oldest) if oldest else timedelta(0)
    problems = []
    if waiting > timedelta(hours=1):
        problems.append(f"a task has waited {int(waiting.total_seconds() // 60)} min "
                        f"unclaimed -- the coding agent's scheduled run may be off")
    if silent:
        problems.append(f"{silent} claimed task(s) with no report after "
                        f"{int(REPORT_TTL.total_seconds() // 3600)}h")
    if expired:
        problems.append(f"{expired} approved task(s) expired unclaimed in the last day")
    return Observation(
        "queue", Health.DEGRADED if problems else Health.OK, float(queued), "tasks",
        "; ".join(problems) if problems else
        (f"{queued} task(s) queued for the coding agent" if queued
         else "nothing queued for the coding agent"),
        evidence={"queued": queued, "silent": silent, "expired_24h": expired})


class DelegateModule:
    name = "delegate"
    title = "Coding agent"
    requires_env: tuple[str, ...] = ()

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("queue", check_queue, timedelta(minutes=30), Severity.NORMAL,
                  "approved work for the coding agent is being picked up",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="delegate.claude_code",
                tier=Tier.ASK,
                summary="Hand this to the coding agent on the owner's Mac",
                run=queue, dry_run=queue_dry, rollback=cancel,
                params={"handoff_id": "the id of the brief ATLAS drafted",
                        "topic": "the estate it concerns; where the card lands"},
                topic_param="topic",
                # The brief is on the card whole: it is what he approves.
                verbose_card=True,
                card_only=True,
                max_per_hour=10,
            ),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""


MODULE = DelegateModule()
