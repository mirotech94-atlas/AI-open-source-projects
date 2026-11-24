"""Football (soccer) data for the reels lane -- a read-only collector.

the owner, 2026-09-29: a football lane in the AI reels, made only from our own
material -- animated stats, bold text, original stylised visuals -- and
every number from API-Football (`api.py`, `facts.py`). This module reads;
it never writes anywhere outside ATLAS. The drafting action lives with the
other reel formats (`content.football`), and posting is the reels path's.

Its one check, `api_football`, reads `/status` (free of quota, per the
docs): the day's requests used against the plan's limit, with the time it
was read. No key is UNKNOWN ("API_FOOTBALL_KEY not set"), never OK; an
unreachable API is UNKNOWN; a refused key (401/403, or `errors.token`) is
FAILING with the API's reason -- never the key.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ...kernel.contracts import Check, Health, Observation, Severity
from . import api

NAME = "api_football"


async def check_api_football() -> Observation:
    read_at = datetime.now(UTC)
    if not api.configured():
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"{api.KEY_ENV} not set — the football reels "
                                  f"lane is idle")
    try:
        got = await api.status()
    except api.ApiError as exc:
        if exc.auth:
            return Observation(NAME, Health.FAILING, detail=api.masked(str(exc)))
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"not read: {api.masked(str(exc))}")
    except Exception as exc:                            # noqa: BLE001
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"not read: {type(exc).__name__}")
    requests = got.get("requests") or {}
    current, limit = requests.get("current"), requests.get("limit_day")
    plan = (got.get("subscription") or {}).get("plan") or "unknown plan"
    active = (got.get("subscription") or {}).get("active")
    if not isinstance(current, int) or not isinstance(limit, int):
        return Observation(NAME, Health.UNKNOWN,
                           detail=f"/status carried no request count (plan {plan}, "
                                  f"read {read_at:%H:%M} UTC)")
    try:
        await api.reconcile(current, limit)
        ours = await api.used_today()
        counted = f"; ATLAS counted {ours}"
    except Exception:                                   # noqa: BLE001
        counted = "; ATLAS's own count unreadable"
    detail = (f"{current}/{limit} requests used today, {plan} plan "
              f"(API-Football /status, read {read_at:%H:%M} UTC){counted}")
    evidence = {"current": current, "limit_day": limit, "plan": plan,
                "read_at": read_at.isoformat()}
    if active is False:
        return Observation(NAME, Health.FAILING, float(current), "requests",
                           f"subscription inactive — {detail}", evidence=evidence)
    health = Health.DEGRADED if current >= limit else Health.OK
    if health is Health.DEGRADED:
        detail = f"today's quota is spent — {detail}"
    return Observation(NAME, health, float(current), "requests", detail,
                       evidence=evidence)


class FootballModule:
    name = "football"
    title = "Football (soccer) data (API-Football)"
    # Optional, not required: unset, the check must still run and say
    # UNKNOWN rather than the module vanishing from the board.
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ("API_FOOTBALL_KEY", "API_FOOTBALL_DAILY_LIMIT",
                                     "FOOTBALL_SEASON", "FOOTBALL_REELS_PER_DAY")
    setup_note = ""

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check(NAME, check_api_football, timedelta(hours=6), Severity.INFO,
                      "API-Football: the day's requests against the plan, read-only",
                      baseline_sigma=None),)

    def actions(self):
        return ()          # read-only: the drafting action is content's

    async def signals(self):
        return ()

    def brief(self) -> str:
        return "Football: API-Football quota"


MODULE = FootballModule()
