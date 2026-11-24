"""Features -> a fixed-width vector. Every slot has a name.

Few features, all explainable, no free text and no embeddings: the number
this produces has to be interrogable by a person, and "column 37 of an
embedding" is not a reason anyone can act on.

Width is fixed at WIDTH so a model trained last month can still score a row
encoded today. Adding a module or an action family therefore means adding a
slot at the END and bumping WIDTH — and every stored model becomes stale,
which the registry detects by width and refuses to serve.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime

import numpy as np

# The modules ATLAS has. Unknown ones fall into "other" rather than raising:
# a proposal from a module added next year must still be scoreable.
MODULES: tuple[str, ...] = (
    "calendar", "comms", "fiverr", "flycheap", "gitlab", "guardian",
    "inroots", "instagram", "linkedin", "mail", "matrix_rooms", "exampleco",
    "netbird", "stripe", "tiktok", "cloudco", "other",
)

# Action VERB families. The verb half of "cloudco.snapshot_all" is
# "snapshot_all"; it belongs to the family whose keyword it contains. Families
# rather than exact names because "draft_reply" and "draft_buyer_reply" should
# share what he thinks about drafts, and an exact one-hot would give a new
# action no history at all.
FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("draft",     ("draft",)),
    ("reply",     ("reply", "send", "post", "message")),
    ("issue",     ("issue", "ticket", "file")),
    ("snapshot",  ("snapshot", "backup")),
    ("restart",   ("restart", "reboot", "bounce")),
    ("prune",     ("prune", "clean", "clear", "rotate")),
    ("retry",     ("retry", "resend", "requeue")),
    ("block",     ("block", "schedule", "book")),
    ("scale",     ("resize", "scale", "upgrade")),
    ("other",     ()),
)

NUMERIC: tuple[str, ...] = (
    "severity", "reversible", "blast_radius", "cost_usd", "hour_sin",
    "hour_cos", "weekday", "weekend", "quiet_hours", "times_seen",
    "prior_approve_rate", "days_since_similar", "you_were_busy",
)

NAMES: tuple[str, ...] = (
    tuple(f"module={m}" for m in MODULES)
    + tuple(f"family={f}" for f, _ in FAMILIES)
    + NUMERIC
)
WIDTH = len(NAMES)          # 17 + 10 + 13 = 40


@dataclass(frozen=True, slots=True)
class DecisionFeatures:
    module: str
    action: str
    severity: int              # 0..3 (contracts.Severity)
    reversible: bool           # has a rollback
    blast_radius: int          # 0 = one record, 3 = whole service
    cost_usd: float
    hour: int                  # 0..23, HIS local time
    weekday: int               # 0..6
    quiet_hours: bool
    times_seen: int            # this (module, action) pair, historically
    prior_approve_rate: float  # of those, how often he said yes (0.5 if none)
    days_since_similar: float  # 30 if never
    you_were_busy: bool        # a call was live, or the calendar said so

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def family_of(action: str) -> str:
    verb = action.split(".", 1)[1] if "." in action else action
    verb = verb.lower()
    for name, keys in FAMILIES:
        if any(k in verb for k in keys):
            return name
    return "other"


def module_of(action: str) -> str:
    mod = action.split(".", 1)[0] if "." in action else action
    return mod if mod in MODULES else "other"


def encode(f: DecisionFeatures) -> np.ndarray:
    """One row, WIDTH wide, float32. Categorical one-hot, numerics scaled to
    roughly [0, 1] so the logistic head's coefficients are comparable and the
    explainer can talk about them without a scaler in the way."""
    x = np.zeros(WIDTH, dtype=np.float32)
    x[MODULES.index(f.module if f.module in MODULES else "other")] = 1.0
    x[len(MODULES) + [n for n, _ in FAMILIES].index(family_of(f.action))] = 1.0
    base = len(MODULES) + len(FAMILIES)
    angle = 2 * np.pi * (f.hour % 24) / 24
    numerics = (
        min(max(f.severity, 0), 3) / 3.0,
        1.0 if f.reversible else 0.0,
        min(max(f.blast_radius, 0), 3) / 3.0,
        min(max(f.cost_usd, 0.0), 500.0) / 500.0,
        (np.sin(angle) + 1) / 2,
        (np.cos(angle) + 1) / 2,
        (f.weekday % 7) / 6.0,
        1.0 if f.weekday % 7 >= 5 else 0.0,
        1.0 if f.quiet_hours else 0.0,
        min(f.times_seen, 100) / 100.0,
        min(max(f.prior_approve_rate, 0.0), 1.0),
        min(max(f.days_since_similar, 0.0), 30.0) / 30.0,
        1.0 if f.you_were_busy else 0.0,
    )
    x[base:base + len(numerics)] = numerics
    return x


def hour_and_weekday(at: datetime | None = None) -> tuple[int, int]:
    """His local clock, not UTC: 4am is a reason only in his time zone."""
    at = (at or datetime.now(UTC)).astimezone()
    return at.hour, at.weekday()
