"""The decision log: the only thing the learning layer learns from.

One row per proposal. Written when the proposal is raised (the conditions
and the prediction), resolved when Miro answers or the proposal expires.
Three outcomes, never collapsed: approved, denied, expired.

Trains only on his decisions. There is no other source, and none may be
added: an external dataset of "what people usually approve" would teach
ATLAS someone else's judgement and present it as his.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time

import numpy as np

from ..state import execute_sql, fetch_all, fetch_one
from .encode import WIDTH, DecisionFeatures, encode, hour_and_weekday

log = logging.getLogger("atlas.judgment.log")

OUTCOMES = ("approved", "denied", "expired")


def quiet_now(at: datetime | None = None) -> bool:
    """Same window comms.py uses; re-read here so this package imports
    nothing that can send, and nothing that can act."""
    window = os.getenv("QUIET_HOURS", "")
    if "-" not in window:
        return False
    start_s, end_s = window.split("-", 1)
    now = (at or datetime.now(UTC)).astimezone().time()
    start = time.fromisoformat(start_s.strip())
    end = time.fromisoformat(end_s.strip())
    return (start <= now or now < end) if start > end else (start <= now < end)


def busy_now() -> bool:
    """A call is live, or was within the last few minutes. The calendar is a
    later refinement; a wrong "busy" would be worse than none."""
    try:
        from ..escalation import _CALLS
        now = datetime.now(UTC)
        return any((now - c.at).total_seconds() < 600 for c in _CALLS)
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True, slots=True)
class History:
    times_seen: int
    approve_rate: float
    days_since: float


async def history_for(module: str, action: str) -> History:
    row = await fetch_one(
        """SELECT count(*) FILTER (WHERE outcome IN ('approved','denied')) AS n,
                  count(*) FILTER (WHERE outcome = 'approved') AS yes,
                  max(at) AS last
             FROM decision_log
            WHERE module = $1 AND action = $2 AND outcome IS NOT NULL""",
        module, action)
    if not row or not row["n"]:
        return History(0, 0.5, 30.0)
    n = int(row["n"])
    yes = int(row["yes"] or 0)
    last = row["last"]
    days = 30.0
    if last is not None:
        days = min(30.0, (datetime.now(UTC) - last).total_seconds() / 86400)
    return History(n, yes / n if n else 0.5, days)


async def features_for(action: str, severity: int, tier: int, *,
                       reversible: bool, blast_radius: int = 1,
                       cost_usd: float = 0.0,
                       at: datetime | None = None) -> DecisionFeatures:
    module = action.split(".", 1)[0]
    hist = await history_for(module, action)
    hour, weekday = hour_and_weekday(at)
    return DecisionFeatures(
        module=module, action=action, severity=int(severity),
        reversible=bool(reversible), blast_radius=int(blast_radius),
        cost_usd=float(cost_usd), hour=hour, weekday=weekday,
        quiet_hours=quiet_now(at), times_seen=hist.times_seen,
        prior_approve_rate=hist.approve_rate,
        days_since_similar=hist.days_since, you_were_busy=busy_now(),
    )


async def record(proposal_id: int, f: DecisionFeatures, tier: int,
                 x: np.ndarray, predicted: float | None, predicted_by: str,
                 reasons: list[str]) -> None:
    await execute_sql(
        """INSERT INTO decision_log
             (proposal_id, module, action, severity, tier, reversible,
              blast_radius, cost_usd, hour, weekday, quiet_hours, times_seen,
              prior_approve_rate, days_since_similar, you_were_busy, vector,
              predicted, predicted_by, reasons)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                   $17,$18,$19)
           ON CONFLICT (proposal_id) DO NOTHING""",
        proposal_id, f.module, f.action, f.severity, int(tier), f.reversible,
        f.blast_radius, f.cost_usd, f.hour, f.weekday, f.quiet_hours,
        f.times_seen, f.prior_approve_rate, f.days_since_similar,
        f.you_were_busy, [float(v) for v in x], predicted, predicted_by,
        json.dumps(reasons))


async def resolve(proposal_id: int, outcome: str, via: str = "") -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, not {outcome!r}")
    await execute_sql(
        """UPDATE decision_log SET outcome=$2, decided_at=now(), decided_via=$3
            WHERE proposal_id=$1 AND outcome IS NULL""",
        proposal_id, outcome, via)


@dataclass(frozen=True, slots=True)
class Rows:
    X: np.ndarray
    y: np.ndarray
    at: list[datetime]
    pairs: list[str]
    predicted: np.ndarray          # what was shown at the time (nan = abstained)

    def __len__(self) -> int:
        return len(self.y)


def label(outcome: str, target: str) -> int | None:
    """approve: approved=1, denied=0, expired excluded.
    answer:  answered=1, expired=0 — "was it worth asking"."""
    if target == "approve":
        return {"approved": 1, "denied": 0}.get(outcome)
    if target == "answer":
        return 0 if outcome == "expired" else 1
    raise ValueError(target)


async def training_rows(target: str = "approve") -> Rows:
    """Resolved rows, OLDEST FIRST. The order is the time split."""
    rows = await fetch_all(
        """SELECT at, module, action, vector, predicted, outcome
             FROM decision_log
            WHERE outcome IS NOT NULL
            ORDER BY at ASC, id ASC""")
    X, y, at, pairs, pred = [], [], [], [], []
    for r in rows:
        lab = label(r["outcome"], target)
        if lab is None:
            continue
        v = list(r["vector"] or [])
        if len(v) != WIDTH:
            continue                     # encoded under an older width
        X.append(v)
        y.append(lab)
        at.append(r["at"])
        pairs.append(f"{r['module']}.{r['action']}")
        pred.append(float("nan") if r["predicted"] is None else float(r["predicted"]))
    return Rows(np.asarray(X, dtype=np.float32).reshape(-1, WIDTH),
                np.asarray(y, dtype=int), at, pairs, np.asarray(pred, dtype=float))


async def new_since(at: datetime | None) -> int:
    row = await fetch_one(
        "SELECT count(*) AS n FROM decision_log WHERE outcome IS NOT NULL"
        + (" AND decided_at > $1" if at else ""), *([at] if at else []))
    return int(row["n"]) if row else 0


async def backfill() -> int:
    """Log the proposals that predate the log. Their features are what can
    still be known — hour, weekday, action, tier, outcome — and their
    prediction is NULL, which is the truth: nothing predicted them."""
    rows = await fetch_all(
        """SELECT p.id, p.action, p.tier, p.created_at, p.status, p.decided_at,
                  p.decided_via, p.rollback_plan
             FROM proposals p
             LEFT JOIN decision_log d ON d.proposal_id = p.id
            WHERE d.id IS NULL AND p.status IN
                  ('approved','executed','failed','declined','expired')
            ORDER BY p.created_at ASC""")
    n = 0
    for r in rows:
        outcome = ("expired" if r["status"] == "expired"
                   else "denied" if r["status"] == "declined" else "approved")
        f = await features_for(r["action"], 1, int(r["tier"]),
                               reversible=bool(r["rollback_plan"]),
                               at=r["created_at"])
        await record(int(r["id"]), f, int(r["tier"]), encode(f), None,
                     "backfill", [])
        await resolve(int(r["id"]), outcome, r["decided_via"] or "")
        n += 1
    if n:
        log.info("decision log: backfilled %d earlier proposals", n)
    return n
