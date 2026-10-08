"""The nightly job: tournament, drift watch, demotion. One sentence for the
brief when something changed."""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np

from ..state import fetch_one
from . import log as _log
from . import registry as _models
from .abstain import Envelope
from .calibrate import fit_calibrated
from .heads import BY_NAME
from .monitor import weekly_brier
from .tournament import MIN_NEW_DECISIONS, run, should_demote, simpler_than

log = logging.getLogger("atlas.judgment.nightly")

# What the brief should say next morning. Filled by the nightly job, drained
# by brief.py; a restart between the two loses at most one sentence.
_pending: list[str] = []


def take_lines() -> list[str]:
    out, _pending[:] = list(_pending), []
    return out


async def _last_tournament_at(target: str) -> datetime | None:
    """When the last bake-off for this target actually ran — read from the
    tournament log, not from process memory.

    It used to be a module global. A fresh process starts with it unset, so
    "new decisions since the last tournament" counted the WHOLE log after
    every restart and the ten-new-decisions gate always opened: each deploy
    after 03:00 re-ran a full bake-off.
    """
    row = await fetch_one(
        "SELECT max(at) AS at FROM judgment_tournaments WHERE target = $1", target)
    return row["at"] if row else None


async def _ran_today() -> bool:
    """Has any bake-off been logged since local midnight? Durable across
    restarts; the scheduler's own guard is in-memory and resets on boot."""
    midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        "SELECT 1 AS x FROM judgment_tournaments WHERE at >= $1 LIMIT 1", midnight)
    return row is not None


async def tournament(target: str = "approve", *, force: bool = False) -> list[str]:
    """Returns the brief lines (empty when nothing worth saying happened)."""
    if not force:
        since = await _last_tournament_at(target)
        if await _log.new_since(since) < MIN_NEW_DECISIONS:
            return []
    rows = await _log.training_rows(target)
    incumbent = await _models.serving(target)
    result = run(rows.X, rows.y, incumbent.head if incumbent else None)
    await _models.log_tournament(target, result)
    log.info("tournament[%s]: %s", target, result.line())

    lines: list[str] = []
    if result.model is None:
        return lines

    # The served model is refitted on ALL rows: the split judged the heads;
    # the winner then learns from everything, including the newest month.
    full = fit_calibrated(BY_NAME[result.winner], rows.X, rows.y) or result.model
    env = Envelope.from_training(rows.X, rows.pairs)
    mean = rows.X.mean(axis=0) if len(rows.X) else np.zeros(rows.X.shape[1])
    await _models.save(target, result.winner, full, result.scores[result.winner],
                       result.n_train, result.n_test, result.scores, env, mean,
                       serve=True, note=result.note)
    if result.changed and incumbent is not None:
        lines.append(f"Judgment: {result.winner} took over from {incumbent.head} "
                     f"last night (Brier {result.scores[result.winner]:.3f} vs "
                     f"{result.scores.get(incumbent.head, float('nan')):.3f} "
                     f"on {result.n_test} held-out decisions).")
    elif result.changed:
        lines.append(f"Judgment: {result.winner} is serving predictions from "
                     f"today, trained on {len(rows)} of your decisions.")

    # Drift: the serving head's own predictions, week by week. Three rising
    # weeks hands back to a simpler head — demotion is as automatic as
    # promotion.
    scored = ~np.isnan(rows.predicted)
    if scored.sum() >= 20:
        weeks = weekly_brier([a for a, s in zip(rows.at, scored, strict=False) if s],
                             rows.predicted[scored], rows.y[scored])
        if should_demote(weeks):
            down = simpler_than(result.winner)
            if down and down in result.scores and result.scores[down] != float("inf"):
                lines.append(f"Judgment: {result.winner}'s accuracy has slipped "
                             f"three weeks running; handing back to {down}.")
                sub = fit_calibrated(BY_NAME[down], rows.X, rows.y)
                if sub is not None:
                    await _models.save(target, down, sub, result.scores[down],
                                       result.n_train, result.n_test,
                                       result.scores, env, mean, serve=True,
                                       note="demoted after three weeks of drift")
    return lines


async def run_all(*, force: bool = False) -> list[str]:
    if not force and await _ran_today():
        log.info("judgment: a tournament already ran today; not re-running "
                 "after a restart")
        return []
    lines = []
    for target in ("approve", "answer"):
        try:
            lines += await tournament(target, force=force)
        except Exception:
            log.exception("nightly tournament failed for %s", target)
    _pending.extend(lines)
    return lines
