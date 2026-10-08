"""Would the paper curve have passed a typical prop-firm evaluation?
Arithmetic only, over ATLAS's own paper trades, against the ILLUSTRATIVE
rules in `propfirm.json`. Real firms differ; this says which rule the curve
hit and on which session. ATLAS never signs up for, pays for, or contacts a
prop firm: there is no code path to one.
"""
from __future__ import annotations

import json
import pathlib
from collections.abc import Iterable
from typing import Any

from .backtest import Trade

RULES = pathlib.Path(__file__).with_name("propfirm.json")


def _usd(v: float) -> str:
    return f"{'-' if v < 0 else '+'}${abs(v):,.2f}"


def rules() -> dict[str, Any]:
    raw = json.loads(RULES.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def check(trades: Iterable[Trade], r: dict[str, Any], start_equity: float
          ) -> dict[str, Any]:
    """Walk the trades in exit order. Fails on the first session whose
    realised loss reaches the daily limit, or the first trade-close mark at
    or under the trailing floor (the peak of marks minus the trailing
    drawdown, never below the starting floor). Passes once the target is
    reached with at least the minimum trading days. Otherwise incomplete.
    Pure."""
    account = float(r["account"])
    target = float(r["profit_target"])
    daily = float(r["daily_loss_limit"])
    trail = float(r["trailing_max_drawdown"])
    min_days = int(r["min_trading_days"])
    ts = sorted(trades, key=lambda t: t.exit.bar_ts)
    equity = peak = start_equity
    floor = start_equity - trail
    by_day: dict[str, float] = {}
    days: list[str] = []
    for t in ts:
        day = t.session.isoformat()
        if day not in by_day:
            by_day[day] = 0.0
            days.append(day)
        by_day[day] += t.pnl
        equity += t.pnl
        peak = max(peak, equity)
        floor = max(floor, peak - trail)
        if by_day[day] <= -daily:
            return {"status": "failed", "rule": "daily_loss_limit", "session": day,
                    "detail": f"lost ${-by_day[day]:,.2f} on {day} (limit ${daily:,.0f})",
                    "equity": round(equity, 2), "days_traded": len(days),
                    "rules": r}
        if equity <= floor:
            return {"status": "failed", "rule": "trailing_max_drawdown", "session": day,
                    "detail": (f"equity ${equity:,.2f} reached the trailing floor "
                               f"${floor:,.2f} (peak ${peak:,.2f} − ${trail:,.0f})"),
                    "equity": round(equity, 2), "days_traded": len(days),
                    "rules": r}
        if equity - start_equity >= target and len(days) >= min_days:
            return {"status": "passed", "rule": "profit_target", "session": day,
                    "detail": (f"+${equity - start_equity:,.2f} after {len(days)} "
                               f"trading days (target ${target:,.0f}, min {min_days} "
                               f"days)"),
                    "equity": round(equity, 2), "days_traded": len(days),
                    "rules": r}
    return {"status": "incomplete", "rule": None, "session": days[-1] if days else None,
            "detail": (f"neither passed nor failed: {_usd(equity - start_equity)} after "
                       f"{len(days)} trading days (target ${target:,.0f}, floor "
                       f"${floor:,.2f})"),
            "equity": round(equity, 2), "days_traded": len(days), "rules": r,
            "account": account}


def line(result: dict[str, Any]) -> str:
    """One sentence for a report."""
    name = result["rules"].get("name", "the evaluation")
    s = result["status"]
    if s == "passed":
        return f"Prop-firm check ({name}, illustrative): would have passed — {result['detail']}."
    if s == "failed":
        return (f"Prop-firm check ({name}, illustrative): would have failed on "
                f"{result['session']} — {result['detail']}.")
    return f"Prop-firm check ({name}, illustrative): {result['detail']}."
