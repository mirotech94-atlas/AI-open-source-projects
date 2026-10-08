"""Read-only views for the brief, the dashboard and the CLI."""
from __future__ import annotations

import json

import numpy as np

from ..state import fetch_all, fetch_one
from . import registry as _models
from .monitor import Reliability, reliability


async def scored_rows(limit: int = 2000) -> tuple[np.ndarray, np.ndarray, list]:
    """Decisions that carried a number AND got an answer — the only rows a
    reliability curve may be drawn from."""
    rows = await fetch_all(
        """SELECT predicted, outcome, at FROM decision_log
            WHERE predicted IS NOT NULL AND outcome IN ('approved','denied')
            ORDER BY at DESC LIMIT $1""", limit)
    p = np.asarray([float(r["predicted"]) for r in rows], dtype=float)
    y = np.asarray([1 if r["outcome"] == "approved" else 0 for r in rows], dtype=int)
    return p, y, [r["at"] for r in rows]


async def current() -> Reliability:
    served = await _models.serving("approve")
    p, y, _ = await scored_rows()
    return reliability(p, y, served.head if served else None)


async def reliability_line() -> str | None:
    rel = await current()
    return rel.line() if rel.n else None


async def snapshot() -> dict:
    """Everything the Activity screen shows, in one call."""
    rel = await current()
    served = {t: await _models.serving(t) for t in _models.TARGETS}
    counts = await fetch_one(
        """SELECT count(*) AS total,
                  count(*) FILTER (WHERE outcome='approved') AS approved,
                  count(*) FILTER (WHERE outcome='denied') AS denied,
                  count(*) FILTER (WHERE outcome='expired') AS expired,
                  count(*) FILTER (WHERE outcome IS NULL) AS open,
                  count(*) FILTER (WHERE predicted IS NULL AND outcome IS NOT NULL
                                   AND predicted_by <> 'backfill') AS abstained
             FROM decision_log""")
    history = await _models.history("approve", 20)
    for h in history:
        if isinstance(h.get("scores"), str):
            h["scores"] = json.loads(h["scores"])
        h["at"] = h["at"].isoformat()
    return {
        "line": rel.line(),
        "brier": None if np.isnan(rel.brier) else round(rel.brier, 4),
        "n_scored": rel.n,
        "bins": [{"lo": b.lo, "hi": b.hi, "n": b.n, "predicted": round(b.predicted, 3),
                  "observed": round(b.observed, 3)} for b in rel.bins],
        "serving": {t: ({"head": s.head, "since": s.at.isoformat(),
                         "brier": round(s.brier, 4), "n_train": s.n_train,
                         "n_test": s.n_test, "id": s.id} if s else None)
                    for t, s in served.items()},
        "log": {k: int(counts[k] or 0) for k in
                ("total", "approved", "denied", "expired", "open", "abstained")}
        if counts else {},
        "tournaments": history,
        "cold_start": 40,
    }
