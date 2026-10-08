"""Keeping it honest: Brier, reliability curve, drift watch.

A model saying 90% must be right about 90% of the time, and that is what
degrades silently as a network takes over. Brier is already the tournament's
selection metric, so honesty and selection are the same number; this module
turns the same predictions into the curve on the Activity screen and the one
line that matters:

    When I said 80%, you said yes 76% of the time. 34 samples. Serving: neural.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .calibrate import brier

BINS = ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01))


@dataclass(frozen=True, slots=True)
class Bin:
    lo: float
    hi: float
    n: int
    predicted: float       # mean p in the bin
    observed: float        # fraction he actually said yes


@dataclass(frozen=True, slots=True)
class Reliability:
    bins: tuple[Bin, ...]
    brier: float
    n: int
    serving: str | None

    def line(self) -> str:
        """The bin with the most samples, in his words."""
        full = [b for b in self.bins if b.n]
        if not full:
            return (f"No scored decisions yet. Serving: {self.serving or 'nobody'}.")
        b = max(full, key=lambda b: b.n)
        return (f"When I said {round(b.predicted * 100)}%, you said yes "
                f"{round(b.observed * 100)}% of the time. {b.n} samples. "
                f"Serving: {self.serving or 'nobody'}.")


def reliability(p: np.ndarray, y: np.ndarray, serving: str | None) -> Reliability:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    bins = []
    for lo, hi in BINS:
        m = (p >= lo) & (p < hi)
        n = int(m.sum())
        bins.append(Bin(lo, min(hi, 1.0), n,
                        float(p[m].mean()) if n else (lo + min(hi, 1.0)) / 2,
                        float(y[m].mean()) if n else 0.0))
    return Reliability(tuple(bins), brier(y, p) if len(y) else float("nan"),
                       len(y), serving)


def weekly_brier(at: list, p: np.ndarray, y: np.ndarray) -> list[float]:
    """One Brier per ISO week, oldest first, for the drift watch."""
    if not len(y):
        return []
    weeks: dict[tuple[int, int], list[int]] = {}
    for i, t in enumerate(at):
        weeks.setdefault(t.isocalendar()[:2], []).append(i)
    out = []
    for key in sorted(weeks):
        idx = weeks[key]
        out.append(brier(np.asarray(y)[idx], np.asarray(p)[idx]))
    return out
