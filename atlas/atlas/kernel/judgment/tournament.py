"""Nightly bake-off. The winner serves tomorrow.

This is the part that makes the handover automatic. Every head trains every
night; the one that predicts the held-out fold best serves the next day —
but only if it beats the incumbent, and a tie keeps the incumbent. The
network takes over when it is genuinely better and not one day sooner, and
it is demoted the same way: by losing, in the same log.

The split is BY TIME, never at random. Random splitting leaks the future
into the past — a model sees Tuesday's decision while being tested on
Monday's — and every model looks brilliant right up to production. The
oldest 70% train; the newest 30% judge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .calibrate import brier, fit_calibrated
from .heads import HEADS, SIMPLER

log = logging.getLogger("atlas.judgment.tournament")

MIN_NEW_DECISIONS = 10          # nightly runs only when this many are new
TRAIN_FRACTION = 0.70
DRIFT_WEEKS = 3                 # Brier rising this many weeks running -> demote


@dataclass(frozen=True, slots=True)
class Result:
    winner: str | None                  # who serves tomorrow
    incumbent: str | None
    scores: dict[str, float]            # head -> Brier (inf = lacked data)
    changed: bool                       # did the serving head change
    n_train: int
    n_test: int
    model: object = field(default=None, repr=False, compare=False)
    note: str = ""

    def line(self) -> str:
        """The one line that goes in the registry and the brief."""
        parts = [f"{h} {s:.3f}" if s != float("inf") else f"{h} —"
                 for h, s in self.scores.items()]
        tail = (f"{self.winner} takes over" if self.changed
                else f"{self.winner or 'nobody'} holds")
        return "  ·  ".join(parts) + f"   → {tail}"


def split_by_time(X: np.ndarray, y: np.ndarray, fraction: float = TRAIN_FRACTION):
    """Rows must arrive oldest-first. The cut is a point in time."""
    cut = int(len(y) * fraction)
    return X[:cut], X[cut:], y[:cut], y[cut:]


def run(X: np.ndarray, y: np.ndarray, incumbent: str | None,
        incumbent_brier: float | None = None) -> Result:
    """Train all heads on the old 70%, score on the new 30%, pick a server.

    `incumbent_brier` is the incumbent's score on THIS fold when it is
    re-fitted tonight; it is compared like any other. If the incumbent
    cannot be fitted (it lacked data tonight) it cannot hold either.
    """
    Xtr, Xte, ytr, yte = split_by_time(X, y)
    scores: dict[str, float] = {}
    models: dict[str, object] = {}
    for head in HEADS:
        pipe = fit_calibrated(head, Xtr, ytr)
        if pipe is None:
            scores[head.name] = float("inf")   # not enough data: loses, logged
            continue
        p = pipe.predict_proba(Xte)[:, 1]
        scores[head.name] = brier(yte, p)
        models[head.name] = pipe

    if not models:
        return Result(None, incumbent, scores, False, len(ytr), len(yte),
                      note="no head had enough data")

    best = min(models, key=lambda h: scores[h])
    winner = best
    # A tie keeps the incumbent — never churn the serving model for noise.
    # "Beats" means strictly lower, by more than rounding.
    if incumbent in models and scores[incumbent] <= scores[best] + 1e-6:
        winner = incumbent
    changed = winner != incumbent
    return Result(winner, incumbent, scores, changed, len(ytr), len(yte),
                  model=models[winner],
                  note="" if changed else "tie keeps the incumbent"
                  if incumbent in models and best != incumbent else "")


def should_demote(weekly_brier: list[float]) -> bool:
    """The serving model's Brier rose three weeks running. Oldest first."""
    if len(weekly_brier) < DRIFT_WEEKS + 1:
        return False
    recent = weekly_brier[-(DRIFT_WEEKS + 1):]
    from itertools import pairwise
    return all(b > a for a, b in pairwise(recent))


def simpler_than(head: str | None) -> str | None:
    return SIMPLER.get(head or "", None)
