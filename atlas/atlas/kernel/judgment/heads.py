"""Three heads, one interface. The tournament picks which one serves.

Nothing here decides anything. A head turns a feature vector into a
probability; what the probability may do is policy.py's business, and
running anything is the executor's, which this package cannot import.
"""
from __future__ import annotations

from typing import Protocol

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier


class Head(Protocol):
    name: str
    min_rows: int

    def build(self): ...


class LogisticHead:
    """Baseline. Wins early: sixty decisions cannot support anything else,
    and its coefficients are the most honest explanation there is."""
    name, min_rows = "logistic", 40

    def build(self):
        return LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")


class GradientHead:
    """Wins the middle. Shallow trees, few of them, early stopping."""
    name, min_rows = "gradient", 400

    def build(self):
        return HistGradientBoostingClassifier(
            max_depth=3, max_iter=200, learning_rate=0.05,
            early_stopping=True, validation_fraction=0.2,
        )


class NeuralHead:
    """Small MLP, exactly as specified: 40 -> 32 ReLU -> 16 ReLU -> 1 sigmoid,
    Adam lr 1e-3, weight decay 1e-4, batch 32, early stopping patience 15.

    Two hidden layers is not a compromise. On tabular data this size, deeper
    networks reliably lose to shallower ones; dropout is what sklearn's MLP
    lacks, and `alpha` (L2) plus early stopping carry that job. Do not make
    it bigger.

    It trains from day one but is only ELIGIBLE to serve at 2,000 labelled
    rows. Below that it competes and loses, and the loss is logged like any
    other. A network trained on 200 rows does not merely perform worse — it
    memorises and reports 95% about things it has never seen.
    """
    name, min_rows = "neural", 2000

    def build(self):
        return MLPClassifier(
            hidden_layer_sizes=(32, 16),
            activation="relu",
            solver="adam",
            alpha=1e-4,                # weight decay
            learning_rate_init=1e-3,
            batch_size=32,
            early_stopping=True,
            n_iter_no_change=15,
            validation_fraction=0.2,
            max_iter=600,
            random_state=0,
        )


HEADS: tuple[Head, ...] = (LogisticHead(), GradientHead(), NeuralHead())
BY_NAME: dict[str, Head] = {h.name: h for h in HEADS}

# Simpler-first. Demotion hands back one step down this list.
SIMPLER: dict[str, str | None] = {"neural": "gradient", "gradient": "logistic",
                                  "logistic": None}
