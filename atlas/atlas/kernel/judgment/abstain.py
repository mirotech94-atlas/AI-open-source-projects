"""Novelty detection. When ATLAS has not seen it, he says so and asks.

A number on something genuinely new is a guess wearing a percentage, and a
guess that reads "92%" is worse than no number: it is believed. So the
abstainer runs BEFORE any head, and its answer is final. It abstains when:

  * the log holds fewer than COLD_START rows — nothing has been learned yet
  * this (module, action) pair has been decided fewer than MIN_PAIR times —
    the head would be extrapolating from other actions
  * a numeric feature sits far outside anything in training — cost, hour,
    blast radius the model has never met

Below COLD_START every head abstains and ATLAS asks about everything,
exactly as he does today. Nothing degrades while it learns.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .encode import FAMILIES, MODULES, WIDTH

COLD_START = 40
MIN_PAIR = 5
Z_LIMIT = 3.0


@dataclass(frozen=True, slots=True)
class Verdict:
    abstain: bool
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Envelope:
    """What the training set looked like: enough to say "never seen this".
    Stored with the model so novelty is judged against what it learned
    from, not against tonight's log."""
    n_rows: int
    pair_counts: dict[str, int]          # "module.action" -> times in training
    mean: tuple[float, ...]              # per numeric slot
    std: tuple[float, ...]

    @classmethod
    def from_training(cls, X: np.ndarray, pairs: list[str]) -> Envelope:
        base = len(MODULES) + len(FAMILIES)
        num = X[:, base:WIDTH] if len(X) else np.zeros((0, WIDTH - base))
        counts: dict[str, int] = {}
        for p in pairs:
            counts[p] = counts.get(p, 0) + 1
        return cls(
            n_rows=len(X), pair_counts=counts,
            mean=tuple(float(v) for v in (num.mean(axis=0) if len(num) else
                                          np.zeros(WIDTH - base))),
            std=tuple(float(v) for v in (num.std(axis=0) if len(num) else
                                         np.zeros(WIDTH - base))),
        )


def check(env: Envelope | None, x: np.ndarray, pair: str) -> Verdict:
    if env is None or env.n_rows < COLD_START:
        n = env.n_rows if env else 0
        return Verdict(True, f"only {n} decisions logged; learning needs {COLD_START}")
    seen = env.pair_counts.get(pair, 0)
    if seen < MIN_PAIR:
        return Verdict(True, f"{pair} decided only {seen} time(s) before")
    base = len(MODULES) + len(FAMILIES)
    num = x[base:WIDTH]
    for i, (v, m, s) in enumerate(zip(num, env.mean, env.std, strict=False)):
        if s > 1e-6 and abs(v - m) / s > Z_LIMIT:
            from .encode import NUMERIC
            return Verdict(True, f"{NUMERIC[i]} is outside anything seen before")
    return Verdict(False)
