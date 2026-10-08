"""Which Tier 2 actions have earned the offer to run on their own.

Miro, 2026-09-28, chose "earn it": anything reversible with a dry run may
run on its own once he has approved it enough times in a row; he decides
once. This module only READS and lists. Tiers are assigned in code, never at
runtime (invariant 3): a promotion he agrees to is a reviewed one-line tier
change in the action's module, never a flag read back to skip a card.

Eligible: Tier ASK, has a dry run AND a rollback, not marked irreversible.
Anything public-and-final (a post, a reshare, a sent message) is irreversible
by declaration and never offered; Tier 3 is never offered.

A streak counts his decisions newest first: executed extends it; declined or
failed ends it; expired (he did not answer) neither counts nor breaks it --
silence is not consent (invariant 5), and not a no either.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime


def streak_needed() -> int:
    try:
        return max(3, int(os.getenv("PROMOTION_STREAK", "5")))
    except ValueError:
        return 5


@dataclass(frozen=True, slots=True)
class Candidate:
    action: str
    streak: int
    since: datetime | None
    requested: bool


def eligible_actions() -> dict[str, object]:
    from .contracts import Tier
    from .registry import REGISTRY
    out = {}
    for name, action in REGISTRY.actions.items():
        decision = REGISTRY.decisions.get(name)
        if decision is None or decision.tier is not Tier.ASK:
            continue
        if action.rollback is None or getattr(action, "irreversible", False):
            continue
        if action.dry_run is None:
            continue
        out[name] = action
    return out


def streak_of(statuses: list[tuple[str, datetime]]) -> tuple[int, datetime | None]:
    """(approvals in a row, the oldest of them) from decisions newest first."""
    n, since = 0, None
    for status, at in statuses:
        if status == "expired":
            continue
        if status != "executed":
            break
        n, since = n + 1, at
    return n, since


async def candidates() -> list[Candidate]:
    from .state import fetch_all
    names = sorted(eligible_actions())
    if not names:
        return []
    rows = await fetch_all(
        """SELECT action, status, decided_at FROM proposals
            WHERE action = ANY($1::text[]) AND decided_at IS NOT NULL
            ORDER BY action, decided_at DESC""", names)
    asked = {str(r["subject"]) for r in await fetch_all(
        """SELECT DISTINCT subject FROM actions_log
            WHERE event = 'proposal.promotion_requested'""")}
    by: dict[str, list[tuple[str, datetime]]] = {}
    for r in rows:
        by.setdefault(str(r["action"]), []).append((str(r["status"]), r["decided_at"]))
    need = streak_needed()
    out = []
    for name in names:
        n, since = streak_of(by.get(name, []))
        if n >= need or (name in asked and n > 0):
            out.append(Candidate(name, n, since, name in asked))
    return sorted(out, key=lambda c: (-c.streak, c.action))


def render(cands: list[Candidate]) -> str:
    lines = []
    for c in cands:
        when = f" since {c.since:%a %-d %b}" if c.since else ""
        lines.append(f"• `{c.action}` -- approved {c.streak} in a row{when}"
                     + ("; you asked to automate it" if c.requested else ""))
    return ("*Ready to run on its own* (you decide once)\n" + "\n".join(lines)
            + "\nTo promote one, ask for its tier to be changed in code -- a "
              "reviewed one-line change made in a coding session, never "
              "switched by ATLAS at runtime. Each keeps its dry run and its "
              "rollback.")
