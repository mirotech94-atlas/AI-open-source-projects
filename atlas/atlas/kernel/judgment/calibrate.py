"""Calibration: a model that says 80% must be right about 80% of the time.

Platt scaling (a sigmoid over the raw score) below 1,000 rows — it has two
parameters and cannot overfit a small log. Isotonic above that — it can bend
to the real shape of the model's errors once there is enough data to know
that shape. Both are fitted on the TRAINING fold only; the held-out fold is
for scoring, and a calibrator that has seen it makes every Brier score a lie.
"""
from __future__ import annotations

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .heads import Head

ISOTONIC_FROM = 1000


def method_for(n_rows: int) -> str:
    return "isotonic" if n_rows >= ISOTONIC_FROM else "sigmoid"


def fit_calibrated(head: Head, X: np.ndarray, y: np.ndarray) -> Pipeline | None:
    """Scale, fit, calibrate. None if the head lacks the data — which is a
    loss in the tournament, not an error."""
    n = len(y)
    if n < head.min_rows:
        return None
    counts = np.bincount(y.astype(int), minlength=2)
    if (counts > 0).sum() < 2:
        return None
    # Every fold needs both classes; cv can never exceed the minority count.
    cv = int(max(2, min(5, counts.min())))
    if counts.min() < 2:
        return None
    return Pipeline([
        ("scale", StandardScaler()),
        ("clf", CalibratedClassifierCV(head.build(), method=method_for(n), cv=cv)),
    ]).fit(X, y)


def brier(y: np.ndarray, p: np.ndarray) -> float:
    """Mean squared error of the probability. Lower is better; 0.25 is a
    coin flip that says 50% every time."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return float(np.mean((p - y) ** 2)) if len(y) else float("nan")
