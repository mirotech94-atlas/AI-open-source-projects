"""What the assistant may spend in a day, and how it is counted.

Two caps, read from the environment on every call so a change needs no
restart:

  ASSISTANT_SEARCH_PER_DAY   web searches (`search_web`), default 40
  ASSISTANT_FETCH_PER_DAY    every other outside read -- a page, the
                             weather, Wikipedia, a definition, an exchange
                             rate -- together, default 80

A day is a rolling 24 hours. The count is the audit log: `kernel/audit.py`
writes one `assistant.tool` row per tool call, BEFORE the call is made (so a
call that hangs or crashes is still counted), and the budget is the number
of those rows in the last 24 hours for that tool. There is no second
counter to drift from the record, and a restart forgets nothing.

A count that cannot be read spends nothing (`allowance` fails closed), the
same rule `_perplexity.call` keeps for its own cap. Perplexity's monthly
cap still applies underneath `search_web`; this is a cap on the assistant,
not a replacement for the source's own.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from ...kernel.state import fetch_one

SEARCH_ENV = "ASSISTANT_SEARCH_PER_DAY"
FETCH_ENV = "ASSISTANT_FETCH_PER_DAY"
DEFAULT_SEARCH = 40
DEFAULT_FETCH = 80

# The audit event every assistant tool call is written under.
EVENT = "assistant.tool"

SEARCH_TOOLS: frozenset[str] = frozenset({"search_web"})
FETCH_TOOLS: frozenset[str] = frozenset({
    "read_page", "weather_now", "wikipedia_summary", "define_word",
    "convert_currency",
})
NETWORK_TOOLS: frozenset[str] = SEARCH_TOOLS | FETCH_TOOLS


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value >= 0 else default


def cap_for(tool: str) -> int | None:
    """The daily cap this tool counts against, or None for a tool that
    reads nothing outside ATLAS (a calculation, the clock, a status)."""
    if tool in SEARCH_TOOLS:
        return _env_int(SEARCH_ENV, DEFAULT_SEARCH)
    if tool in FETCH_TOOLS:
        return _env_int(FETCH_ENV, DEFAULT_FETCH)
    return None


def _group(tool: str) -> tuple[str, ...]:
    """The tools that share this tool's cap."""
    if tool in SEARCH_TOOLS:
        return tuple(sorted(SEARCH_TOOLS))
    if tool in FETCH_TOOLS:
        return tuple(sorted(FETCH_TOOLS))
    return (tool,)


async def used_today(tool: str) -> int:
    """Calls in the last 24 hours against this tool's cap. Raises when the
    audit log cannot be read; the caller decides what that means."""
    names = _group(tool)
    row = await fetch_one(
        "SELECT count(*) AS n FROM actions_log "
        "WHERE event = $1 AND subject = ANY($2::text[]) "
        "AND at > now() - interval '24 hours'",
        EVENT, list(names))
    if row is None or row["n"] is None:
        raise RuntimeError("the assistant call count returned nothing")
    return int(row["n"])


@dataclass(frozen=True, slots=True)
class Allowance:
    ok: bool
    reason: str | None = None
    used: int | None = None
    cap: int | None = None


async def allowance(tool: str) -> Allowance:
    """May this tool be called now? Always yes for a tool with no cap. For
    a capped tool: no when the day's cap is spent, and no -- with the reason
    -- when the count cannot be read, because an unreadable budget is not a
    budget."""
    cap = cap_for(tool)
    if cap is None:
        return Allowance(True)
    try:
        used = await used_today(tool)
    except Exception as exc:  # noqa: BLE001
        return Allowance(False, f"{tool}: the daily budget could not be read "
                                f"({type(exc).__name__}), so nothing was spent",
                         None, cap)
    if used >= cap:
        return Allowance(False, f"{tool}: today's cap of {cap} outside reads is "
                                f"spent ({used} used in the last 24 hours)",
                         used, cap)
    return Allowance(True, None, used, cap)
