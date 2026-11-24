"""The learning layer. See JUDGMENT.md — this package is built to it.

ATLAS logs every proposal, what the owner answered, and the conditions around it.
Each night three model heads retrain and compete on held-out data; the
winner serves a number the next day: P(the owner approves). The number RANKS,
ROUTES and tells ATLAS when to stay quiet. It never decides.

What this package cannot do, enforced by tests in tests/test_judgment.py:

  * execute anything — there is no import path from here to
    executor.execute, and `predict` returns a dataclass, never a callable
  * change a tier — nothing here reads or writes REGISTRY.decisions, and the
    standing-rule path only *drafts* a rule for him to confirm
  * touch Tier 3 — `predict` refuses an action whose decision is NEVER
    before a single feature is computed
  * learn from anything but his decisions — the only training source is
    decision_log, which is written only from approvals.py
  * guess — below 40 rows, or on a (module, action) it has not seen, every
    head abstains and ATLAS asks exactly as he does today

Layout, one file per component as the spec lists them:

  encode.py      features -> fixed-width vector (40 wide, every slot named)
  heads.py       logistic, gradient, neural — one interface, three impls
  calibrate.py   Platt below 1,000 rows, isotonic above, so 80% means 80%
  tournament.py  nightly bake-off, time split, incumbent wins ties
  registry.py    versioned models in the database, working rollback
  abstain.py     novelty detection, refuses to guess
  explain.py     top three reasons for any number
  monitor.py     Brier, reliability curve, drift watch
  log.py         the decision log: written on propose, resolved on decide
  policy.py      what the number may do (and may not)
"""
from __future__ import annotations

from .policy import (
    Advice,
    Prediction,
    predict,
    record,
    record_outcome,
    standing_rule_text,
)

__all__ = ["Advice", "Prediction", "predict", "record", "record_outcome",
           "standing_rule_text"]
