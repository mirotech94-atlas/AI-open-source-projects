"""Versioned models. Every one ever trained is kept; serving is a flag.

Rollback is therefore instant and total: flip the flag back to the previous
version. `atlas model rollback` does exactly that. A model is refused at
load time if it was encoded under a different WIDTH than today's encoder —
scoring today's rows with last year's columns would be quietly wrong.
"""
from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from ..state import execute_sql, fetch_all, fetch_one
from .abstain import Envelope
from .encode import WIDTH

log = logging.getLogger("atlas.judgment.registry")

TARGETS = ("approve", "answer")


@dataclass(frozen=True, slots=True)
class Served:
    id: int
    at: datetime
    target: str
    head: str
    brier: float
    n_train: int
    n_test: int
    model: object
    envelope: Envelope
    mean: np.ndarray


_cache: dict[str, Served | None] = {}


async def save(target: str, head: str, model, brier: float, n_train: int,
               n_test: int, scores: dict, envelope: Envelope,
               mean: np.ndarray, *, serve: bool, note: str = "") -> int:
    if serve:
        await execute_sql(
            "UPDATE judgment_models SET serving = FALSE WHERE target = $1", target)
    row = await fetch_one(
        """INSERT INTO judgment_models
             (target, head, width, brier, n_train, n_test, scores, blob,
              envelope, mean, serving, note)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING id""",
        target, head, WIDTH, float(brier), int(n_train), int(n_test),
        json.dumps({k: (None if v == float("inf") else v) for k, v in scores.items()}),
        pickle.dumps(model), json.dumps({
            "n_rows": envelope.n_rows, "pair_counts": envelope.pair_counts,
            "mean": list(envelope.mean), "std": list(envelope.std)}),
        [float(v) for v in mean], serve, note)
    _cache.pop(target, None)
    return int(row["id"])


def _served(row) -> Served | None:
    if row is None:
        return None
    if int(row["width"]) != WIDTH:
        log.warning("model #%s was trained %d wide; encoder is %d — not serving",
                    row["id"], row["width"], WIDTH)
        return None
    env = row["envelope"]
    if isinstance(env, str):
        env = json.loads(env)
    return Served(
        id=int(row["id"]), at=row["at"], target=row["target"], head=row["head"],
        brier=float(row["brier"]), n_train=int(row["n_train"]),
        n_test=int(row["n_test"]), model=pickle.loads(row["blob"]),
        envelope=Envelope(int(env["n_rows"]), dict(env["pair_counts"]),
                          tuple(env["mean"]), tuple(env["std"])),
        mean=np.asarray(row["mean"], dtype=np.float32),
    )


async def serving(target: str = "approve") -> Served | None:
    if target in _cache:
        return _cache[target]
    row = await fetch_one(
        """SELECT * FROM judgment_models WHERE target=$1 AND serving
            ORDER BY at DESC LIMIT 1""", target)
    got = _served(row)
    _cache[target] = got
    return got


async def rollback(target: str = "approve") -> Served | None:
    """Hand back to the version that served before the current one."""
    rows = await fetch_all(
        """SELECT id, head, serving FROM judgment_models
            WHERE target=$1 ORDER BY at DESC LIMIT 2""", target)
    if len(rows) < 2:
        return None
    current, previous = rows[0], rows[1]
    await execute_sql("UPDATE judgment_models SET serving=FALSE WHERE id=$1",
                      current["id"])
    await execute_sql(
        """UPDATE judgment_models SET serving=TRUE,
              note = note || ' | restored by rollback' WHERE id=$1""",
        previous["id"])
    _cache.pop(target, None)
    return await serving(target)


async def history(target: str = "approve", limit: int = 30) -> list[dict]:
    rows = await fetch_all(
        """SELECT at, target, scores, winner, incumbent, changed, n_train,
                  n_test, line FROM judgment_tournaments
            WHERE target=$1 ORDER BY at DESC LIMIT $2""", target, limit)
    return [dict(r) for r in rows]


async def log_tournament(target: str, result) -> None:
    await execute_sql(
        """INSERT INTO judgment_tournaments
             (target, scores, winner, incumbent, changed, n_train, n_test, line)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        target,
        json.dumps({k: (None if v == float("inf") else v)
                    for k, v in result.scores.items()}),
        result.winner, result.incumbent, result.changed, result.n_train,
        result.n_test, result.line())


def forget() -> None:
    _cache.clear()
