"""Dashboard API. Mesh-only, session-authenticated, read-mostly.

There is deliberately no admin panel and no "run this check now". Decisions
go through `approvals.decide`: by text (Slack, phone) and, since the owner
2026-10-29 (GUI decisions), from the dashboard's own decision route, which
answers only a pending Tier 2 card behind session, Origin, CSRF and rate
checks (see the comment below `/approvals`). Login and logout are the
dashboard's own session, not a step towards the world.

`POST /api/agents/warm` (Task 59) is the one deliberate write beyond those:
it never calls `invoke` or the executor, only `agents.warmup`, which runs a
roster agent with its tool list forced to the empty set and writes
`agent_runs`, nothing else — a fleet card's LAST RUN filling in truthfully is
not a write to the outside world. `test_no_route_anywhere_can_reach_the_executor`
(tests/test_api.py) is what actually enforces that boundary, by walking the
AST rather than trusting this comment.

Every response carries `as_of`. Nothing on a dashboard screen should be a
number without a time attached to it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from ..kernel import board_state, events
from ..kernel.audit import verify_chain
from ..kernel.executor import watch_only
from ..kernel.registry import REGISTRY
from ..kernel.state import fetch_all, fetch_one
from . import auth, peers
from .peers import PerAddressLimiter

# The modern FastAPI idiom: a dependency in the type, not in a default value.
SignedIn = Annotated[auth.Session, Depends(auth.require)]

log = logging.getLogger("atlas.dashboard")

router = APIRouter(prefix="/api")

# Health precedence for a module tile. UNKNOWN sits between degraded and ok and
# is NEVER collapsed into ok: a module whose every check failed to run is not a
# healthy module, and the ordering here is what stops the UI claiming it is.
HEALTH_RANK = {"failing": 3, "degraded": 2, "unknown": 1, "ok": 0}


def now() -> str:
    return datetime.now(UTC).isoformat()


def envelope(**payload: Any) -> dict[str, Any]:
    return {"as_of": now(), **payload}


# ------------------------------------------------------------------ auth

@router.get("/session")
async def session_state(request: Request) -> dict:
    session = auth.current(request)
    return envelope(
        authenticated=session is not None,
        user=session.user if session else None,
        # Only a signed-in page gets the token the decision route asks for.
        csrf=(auth.csrf_token(request.cookies.get(auth.COOKIE, ""))
              if session else None),
        configured=auth.configured(),
        watch_only=watch_only(),
    )


@router.post("/login")
async def login(request: Request, response: Response) -> dict:
    if not auth.configured():
        raise HTTPException(
            503, "No dashboard password is set. Run: "
                 "python -m atlas.cli set-password")
    client = peers.effective_client(request)
    if auth.rate_limited(client):
        raise HTTPException(429, "too many attempts; wait a few minutes")

    body = await request.json()
    if not auth.verify_password(str(body.get("password", "")), client):
        raise HTTPException(401, "wrong password")

    auth.set_cookie(response, auth.issue(), request)
    return envelope(ok=True, watch_only=watch_only())


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    auth.clear_cookie(response, request)
    return envelope(ok=True)


# ------------------------------------------------------- settings secrets
# The Agency tab's Settings section: which of the names the owner re-enters on
# the Mac are set. A FIXED list (the plan's "Secrets to re-enter"), never a
# scan of the environment, and only a boolean per name: no value, no length,
# no prefix ever leaves this function.

SECRET_NAMES: tuple[str, ...] = (
    "POSTGRES_PASSWORD", "DATABASE_URL", "SESSION_SECRET",
    "DASHBOARD_PASSWORD_HASH", "DASHBOARD_PASSWORD_HASH_B64",
    "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_APPROVER_IDS",
    "SLACK_CHANNELS", "SLACK_CHANNEL", "SLACK_APPROVALS_CHANNEL",
    "SLACK_SIGNING_SECRET",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN",
    "MAIL_ACCOUNTS",
    "YOUTUBE_CLIPS_CLIENT_ID", "YOUTUBE_CLIPS_CLIENT_SECRET",
    "YOUTUBE_CLIPS_REFRESH_TOKEN", "YOUTUBE_CLIPS_CHANNEL_ID",
    "ATLAS_FREE_ONLY", "OPENROUTER_API_KEY", "GROQ_API_KEY",
    "FISH_AUDIO_API_KEY", "FISH_AUDIO_VOICE_ID",
    "ACADEMY_STRIPE_READ_KEY",
    "MAC_AGENT_TOKEN", "HANDOFF_TOKEN",
    "VERCEL_TOKEN", "VERCEL_TEAM_ID", "RESEND_API_KEY", "GMAIL_APP_PASSWORD",
)


@router.get("/settings/secrets")
async def settings_secrets(_s: SignedIn) -> dict:
    """{name: set?} for SECRET_NAMES, read now. An empty value is unset."""
    return envelope(secrets={name: bool(os.environ.get(name, "").strip())
                             for name in SECRET_NAMES})


# -------------------------------------------------------------- overview

_PAUSED = re.compile(r"not trying again until (.+?)(?: so\b|;|,|\.(?:\s|$)|$)",
                     re.IGNORECASE)


def _check_state(health: str, detail: str) -> dict[str, str] | None:
    """`{"kind": "not_set_up"|"paused", "label": ...}` for an UNKNOWN whose
    own reason is a missing setting or a deliberate back-off; None for a
    check that was measured, or that failed for a real reason."""
    if health != "unknown":
        return None
    from ..kernel.detect import _UNCONFIGURED
    if (m := _PAUSED.search(detail)):
        why = " (bot wall)" if "bot wall" in detail.lower() else ""
        return {"kind": "paused", "label": f"paused until {m.group(1).strip()}{why}"}
    if _UNCONFIGURED.search(detail):
        return {"kind": "not_set_up", "label": "not set up"}
    return None


def _number(value: Any) -> float | None:
    """A stored reading as a JSON number, or null. NaN and infinity are not
    numbers a screen can draw (and not valid JSON): null, never a zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


@router.get("/overview")
async def overview(_s: SignedIn) -> dict:
    """One tile per module. Every figure is read fresh on this request."""
    rows = await fetch_all(
        """SELECT p.name AS property, c.name AS check_name,
                  c.last_run_at, c.next_run_at, c.interval_s,
                  o.health, o.detail, o.at AS observed_at, o.value, o.unit
           FROM checks c
           JOIN properties p ON p.id = c.property_id
           LEFT JOIN LATERAL (
               SELECT health, detail, at, value, unit FROM observations
               WHERE check_id = c.id ORDER BY at DESC LIMIT 1
           ) o ON TRUE
           ORDER BY p.name, c.name""")

    latest = await fetch_all(
        """SELECT DISTINCT ON (property) property, id, title, severity, at
           FROM findings ORDER BY property, at DESC""")
    newest = {r["property"]: dict(r) for r in latest}

    pending = await fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE status='pending'")

    from .system_check import effective_health

    moment = datetime.now(UTC)
    today = board_state.today_utc(moment)
    loaded = bool(REGISTRY.modules)
    tiles: dict[str, dict[str, Any]] = {}
    seen_keys: set[str] = set()
    for row in rows:
        name = row["property"]
        key = f"{name}.{row['check_name']}"
        seen_keys.add(key)
        # A check its module no longer registers (renamed or retired, e.g.
        # cloudco "snapshots" -> "protection", or a whole module taken out of
        # the code) keeps its old rows forever. Its last reading is history,
        # not a reading of now: it is listed as RETIRED, never OK, and left
        # out of the module's colour. A switched-off module is different: its
        # tile is replaced by the "switched off" label below. With no
        # registry loaded (nothing to compare with) nothing is called retired.
        gone = ((name in REGISTRY.modules and key not in REGISTRY.checks)
                or (loaded and name not in REGISTRY.modules
                    and name not in REGISTRY.disabled))
        tile = tiles.setdefault(name, {
            "module": name,
            "title": getattr(REGISTRY.modules.get(name), "title", name),
            "health": "unknown",
            "checks": [],
            "last_check_at": None,
            "next_check_at": None,
            "enabled": name in REGISTRY.modules,
        })
        # A check with no observation yet has not been measured. It is unknown,
        # never ok. Nor is an OK read three intervals ago: the same staleness
        # rule the spoken system check applies (`effective_health`), so the
        # screen and the voice never disagree.
        interval = (timedelta(seconds=row["interval_s"])
                    if row["interval_s"] else None)
        health, stale = effective_health(row["health"], row["observed_at"],
                                         interval, moment)
        # Retired and parked outrank the check's own words: the board says
        # which of the two it is, in its own look, never as OK.
        state = (board_state.override_state(key, registered=not gone, today=today)
                 or _check_state(health, row["detail"] or ""))
        observed = row["observed_at"].isoformat() if row["observed_at"] else None
        tile["checks"].append({
            "name": row["check_name"],
            "health": health,
            "state": state,
            "stale": stale,
            # A stale reading is UNKNOWN and says when it was last read.
            "last_read_at": observed,
            "detail": row["detail"] or "",
            "observed_at": observed,
            "next_run_at": row["next_run_at"].isoformat() if row["next_run_at"] else None,
            "interval_s": row["interval_s"],
            # The reading's own number and unit, exactly as the check wrote
            # them (Observation.value/unit): null when it gave none, never a
            # zero. `health` above decides whether it may be shown -- an
            # UNKNOWN or stale reading can still carry a value, and that
            # value is not a measurement of now. `read_at` is `observed_at`
            # under the name the history endpoint uses.
            "value": _number(row.get("value")),
            "unit": row.get("unit") or None,
            "read_at": observed,
        })
        # Compare against what we have SEEN, not against the initial value.
        # Starting at "unknown" (rank 1) meant "ok" (rank 0) could never beat
        # it, so a module whose every check passed still rendered as
        # unmeasured — the inverse of the dangerous direction, but wrong.
        # A check that is not set up, or deliberately paused, says so on its
        # own row but does not decide the module's colour: "nobody has given
        # me a token" is not the module failing (the owner, 2026-09-27: the board
        # should be clean). It stays UNKNOWN underneath -- never OK.
        worst = tile.get("_worst")
        if state is not None:
            tile.setdefault("_set_aside", []).append(state)
        elif worst is None or HEALTH_RANK.get(health, 1) > HEALTH_RANK[worst]:
            tile["_worst"] = health
        for key, column in (("last_check_at", "last_run_at"),
                            ("next_check_at", "next_run_at")):
            value = row[column]
            if value is None:
                continue
            stamp = value.isoformat()
            current = tile[key]
            if current is None or (stamp < current if key == "next_check_at"
                                   else stamp > current):
                tile[key] = stamp

    # A registered check with no row at all has never been read. It is listed
    # as UNKNOWN ("never read"), not left out: a missing line reads like a
    # clean board (invariant 7). Only the checks the registry really holds.
    for key, check in REGISTRY.checks.items():
        if key in seen_keys:
            continue
        name, _, check_name = key.partition(".")
        tile = tiles.setdefault(name, {
            "module": name,
            "title": getattr(REGISTRY.modules.get(name), "title", name),
            "health": "unknown", "checks": [], "last_check_at": None,
            "next_check_at": None, "enabled": name in REGISTRY.modules,
        })
        interval = getattr(check, "interval", None)
        state = board_state.override_state(key, registered=True, today=today)
        if state is not None:
            tile.setdefault("_set_aside", []).append(state)
        elif (tile.get("_worst") is None
                or HEALTH_RANK["unknown"] > HEALTH_RANK[tile["_worst"]]):
            tile["_worst"] = "unknown"
        tile["checks"].append({
            "name": check_name, "health": "unknown",
            "state": state,
            "stale": False, "last_read_at": None,
            "detail": "never read: this check has not reported yet",
            "observed_at": None, "next_run_at": None,
            "interval_s": int(interval.total_seconds()) if interval else None,
            "value": None, "unit": None, "read_at": None,
        })

    for name, tile in tiles.items():
        # No checks at all is genuinely unknown: nothing was measured.
        set_aside = tile.pop("_set_aside", [])
        tile["health"] = tile.pop("_worst", None) or "unknown"
        # Every check set aside: the module as a whole is not set up (or
        # paused). Said in words, drawn grey, and left out of the totals.
        measured = [c for c in tile["checks"] if c["state"] is None]
        tile["setup"] = set_aside[0]["label"] if set_aside and not measured else None
        found = newest.get(name)
        tile["latest_finding"] = {
            "id": found["id"], "title": found["title"],
            "severity": found["severity"], "at": found["at"].isoformat(),
        } if found else None

    # A module switched off by name still has its old check rows; it reads
    # "switched off", with no stale check rows to argue with that.
    for name, why in REGISTRY.disabled.items():
        if name in tiles and "switched off" in why:
            tiles[name].update(setup="switched off", checks=[], enabled=False,
                               disabled_reason=why, health="unknown")

    # Modules that are disabled never register checks, so they are absent from
    # the query above. Showing them is the point: "not watched" is information.
    for name, why in REGISTRY.disabled.items():
        tiles.setdefault(name, {
            "module": name, "title": name, "health": "unknown", "checks": [],
            "last_check_at": None, "next_check_at": None, "enabled": False,
            "setup": ("switched off" if "switched off" in why else "not set up"),
            "latest_finding": None, "disabled_reason": why,
        })

    # The board's line about itself, counted now from the rows above: checks
    # run / total, UNKNOWN, parked, each with the time it was counted. A
    # switched-off module's checks were cleared above, so they are not in it.
    every = [c for t in tiles.values() for c in t["checks"]]
    return envelope(
        modules=sorted(tiles.values(), key=lambda t: t["module"]),
        pending_approvals=int(pending["n"]) if pending else 0,
        watch_only=watch_only(),
        self_test=board_state.self_test(every, moment),
    )


# --------------------------------------------------------------- history
#
# What the checks read before now, for the home page's small charts. Stored
# readings only: no interpolation, no smoothing, no point that was not an
# observation. A gap stays a gap (the client breaks the line where two
# readings are further apart than `stale_after_s`, the same rule that turns
# a reading UNKNOWN on the board), and an UNKNOWN reading is sent as UNKNOWN
# with whatever value it carried -- the client draws it apart, never as 0.

HISTORY_DAYS_DEFAULT = 7
HISTORY_DAYS_MAX = 30
# A check that runs every five minutes for thirty days is 8,640 readings.
# More than this is cut from the OLD end and said so (`truncated`), never
# thinned into something that looks like a full series.
HISTORY_POINTS_MAX = 10_000


def _stale_after_s(interval_s: Any) -> int | None:
    """Seconds after which a reading is no longer a reading of now: the
    board's own rule (`freshness.effective_health`)."""
    from ..kernel.freshness import STALE_FACTOR, STALE_GRACE
    if not interval_s:
        return None
    return int(int(interval_s) * STALE_FACTOR + STALE_GRACE.total_seconds())


@router.get("/checks/{module}/{check}/history")
async def check_history(
    _s: SignedIn,
    module: str,
    check: str,
    days: Annotated[int, Query(ge=1, le=HISTORY_DAYS_MAX)] = HISTORY_DAYS_DEFAULT,
) -> dict:
    """Every stored reading of one check over the last `days`, oldest first:
    `{read_at, value, unit, health}` each, exactly as recorded. 404 for a
    check that has no row at all; an empty `points` for one that has not
    read in the window (which is not a zero)."""
    found = await fetch_one(
        """SELECT c.id, c.interval_s FROM checks c
           JOIN properties p ON p.id = c.property_id
           WHERE p.name = $1 AND c.name = $2""", module, check)
    if found is None:
        raise HTTPException(404, f"no check {module}.{check}")
    rows = await fetch_all(
        """SELECT at, value, unit, health FROM (
               SELECT at, value, unit, health FROM observations
               WHERE check_id = $1 AND at > now() - $2 * interval '1 day'
               ORDER BY at DESC LIMIT $3
           ) newest ORDER BY at ASC""",
        found["id"], days, HISTORY_POINTS_MAX + 1)
    truncated = len(rows) > HISTORY_POINTS_MAX
    if truncated:
        rows = rows[-HISTORY_POINTS_MAX:]
    from ..kernel.freshness import known_health
    return envelope(
        module=module, check=check, days=days,
        interval_s=found["interval_s"],
        stale_after_s=_stale_after_s(found["interval_s"]),
        truncated=truncated,
        points=[{
            "read_at": r["at"].isoformat(),
            "value": _number(r["value"]),
            "unit": r["unit"] or None,
            "health": known_health(r["health"]),
        } for r in rows],
    )


def _fold_module(checks: list[tuple[str, dict[str, str] | None]]) -> tuple[str, bool]:
    """A module's colour from its checks' `(health, set-aside state)`, by the
    overview's rule: the worst health among checks that were measured; a
    module whose every check is set aside (not set up, paused) is set aside,
    and one with nothing measured at all is unknown -- never OK."""
    measured = [h for h, state in checks if state is None]
    if not measured:
        return "unknown", bool(checks)
    return max(measured, key=lambda h: HEALTH_RANK.get(h, 1)), False


def _health_counts(rows: list[Any], moment: datetime) -> dict[str, Any]:
    """The Health strip's counts as they stood at `moment`, from each
    registered check's newest reading at or before it. Disabled modules and
    retired checks are left out, as the strip leaves them out."""
    from .system_check import effective_health
    per: dict[str, list[tuple[str, dict[str, str] | None]]] = {}
    for row in rows:
        name = row["property"]
        if (name not in REGISTRY.modules
                or f"{name}.{row['check_name']}" not in REGISTRY.checks):
            continue
        interval = (timedelta(seconds=row["interval_s"])
                    if row["interval_s"] else None)
        health, _stale = effective_health(row["health"], row["observed_at"],
                                          interval, moment)
        key = f"{name}.{row['check_name']}"
        per.setdefault(name, []).append(
            (health, board_state.override_state(
                key, registered=True, today=board_state.today_utc(moment))
             or _check_state(health, row["detail"] or "")))
    counts = {"ok": 0, "degraded": 0, "failing": 0, "unknown": 0}
    set_aside = 0
    for checks in per.values():
        health, aside = _fold_module(checks)
        if aside:
            set_aside += 1
        else:
            counts[health] += 1
    return {"counts": counts, "set_aside": set_aside}


HEALTH_STEP_HOURS = 1


@router.get("/health/history")
async def health_history(
    _s: SignedIn,
    days: Annotated[int, Query(ge=1, le=14)] = HISTORY_DAYS_DEFAULT,
) -> dict:
    """How many modules were OK, degraded, failing and UNKNOWN, hour by
    hour over the last `days`, ending with now -- the strip's own rule
    replayed at each hour (staleness included), from stored readings."""
    moment = datetime.now(UTC).replace(microsecond=0)
    start = moment - timedelta(days=days)
    rows = await fetch_all(
        """SELECT ts.t, p.name AS property, c.name AS check_name, c.interval_s,
                  o.health, o.detail, o.at AS observed_at
           FROM generate_series($1::timestamptz, $2::timestamptz,
                                $3 * interval '1 hour') AS ts(t)
           CROSS JOIN checks c
           JOIN properties p ON p.id = c.property_id
           LEFT JOIN LATERAL (
               SELECT health, detail, at FROM observations
               WHERE check_id = c.id AND at <= ts.t
               ORDER BY at DESC LIMIT 1
           ) o ON TRUE
           ORDER BY ts.t""", start, moment, HEALTH_STEP_HOURS)
    by_time: dict[datetime, list[Any]] = {}
    for row in rows:
        by_time.setdefault(row["t"], []).append(row)
    points = [{"at": t.isoformat(), **_health_counts(group, t)}
              for t, group in sorted(by_time.items())]
    return envelope(days=days, step_hours=HEALTH_STEP_HOURS, points=points)


# -------------------------------------------------------------- approvals

def _proposal_json(row) -> dict[str, Any]:
    params = row["params"]
    if isinstance(params, str):
        params = json.loads(params or "{}")
    evidence = row["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence or "{}")
    decision = REGISTRY.decisions.get(row["action"])
    return {
        "id": row["id"],
        "action": row["action"],
        "params": params,
        "reason": row["reason"],
        "tier": row["tier"],
        "tier_reason": decision.reason if decision else "action not in the catalog",
        "status": row["status"],
        "confidence": row["confidence"],
        "rollback_plan": row["rollback_plan"] or "",
        "evidence": evidence,
        "created_at": row["created_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        # The dry run as captured when the proposal was raised. The UI refuses
        # to enable Approve unless this is present.
        "dry_run": row["dry_run"],
        "dry_run_ok": row["dry_run_ok"],
        "dry_run_at": row["dry_run_at"].isoformat() if row["dry_run_at"] else None,
    }


@router.get("/approvals")
async def approvals(_s: SignedIn) -> dict:
    rows = await fetch_all(
        """SELECT p.*, f.id AS finding_id, f.title AS finding_title,
                  f.detail AS finding_detail, f.check_key, f.health,
                  d.predicted_by, d.reasons
           FROM proposals p
           LEFT JOIN findings f ON f.proposal_id = p.id
           LEFT JOIN decision_log d ON d.proposal_id = p.id
           WHERE p.status = 'pending'
           ORDER BY p.expires_at""")
    out = []
    for row in rows:
        item = _proposal_json(row)
        keys = set(row.keys())
        reasons = row["reasons"] if "reasons" in keys else None
        if isinstance(reasons, str):
            reasons = json.loads(reasons or "[]")
        item["judgment"] = {
            "p": row["predicted"] if "predicted" in keys else None,
            "by": row["predicted_by"] if "predicted_by" in keys else None,
            "reasons": reasons or [],
        }
        item["finding"] = {
            "id": row["finding_id"], "title": row["finding_title"],
            "detail": row["finding_detail"], "check": row["check_key"],
            "health": row["health"],
        } if row["finding_id"] else None
        out.append(item)
    return envelope(proposals=out, watch_only=watch_only(),
                    whatsapp_undelivered=await _whatsapp_undelivered())


async def _whatsapp_undelivered() -> int | None:
    """How many pending cards that WhatsApp could really have decided (eligible
    now, by the same check the send uses) are older than ten minutes and never got
    their message (no `mmt.notify_card` claim): the portal banner that replaces a
    Slack nag. 0 when WhatsApp cards are off or watch-only (nothing is sent then,
    so nothing is "missing"); None when it cannot be read (never a guessed 0)."""
    from ..kernel import wa_approvals
    if not wa_approvals.enabled() or watch_only():
        return 0
    try:
        rows = await fetch_all(
            """SELECT p.id, p.action, p.params, p.dry_run_ok FROM proposals p
                WHERE p.status = 'pending' AND p.tier >= 2 AND p.expires_at > now()
                  AND p.created_at < now() - interval '10 minutes'
                  AND NOT EXISTS (SELECT 1 FROM effect_claims e
                                   WHERE e.action = 'mmt.notify_card'
                                     AND e.key = 'card:' || p.id::text)
                LIMIT 50""")
        return sum(1 for r in rows or [] if wa_approvals.eligibility(
            str(r["action"]), wa_approvals._params_of(r["params"]),
            dry_ok=r.get("dry_run_ok") if hasattr(r, "get") else None)[0])
    except Exception:                                       # noqa: BLE001
        return None


# the owner 2026-10-29: GUI decisions. The dashboard answers a pending Tier 2 card
# through `approvals.decide` -- the same door Slack and the phone use, with
# the card's STORED action and params; the browser sends only a verdict. The
# controls, each failing closed and in this order: a session (401), the
# request's own Origin and a same-origin fetch (403), the CSRF token that
# only /api/session hands out (403), a closed body of approve|decline (422),
# a per-session rate (429, with the retry time), then the card read here: pending (409) and its
# action's tier from code, Tier.ASK only (403). Watch-only, expiry and the
# one-claim UPDATE stay inside decide(). `/api/agents/warm` is the other
# write that is not login/logout, and it never reaches the executor.


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["approve", "decline"]


# Keyed per signed-in SESSION, never per address: behind Docker Desktop every
# tab and tool on the Mac is one gateway address, so a per-address bucket was
# shared, and ten watch-only approves (refused, nothing changed) made the next
# Decline answer 429 on its first click (the owner 2026-10-29). `_decision_limit`
# counts only answers that changed a card; `_decision_calls` bounds every call
# (each one writes audit rows) more loosely.
_decision_limit = PerAddressLimiter(10, 60.0)
_decision_calls = PerAddressLimiter(30, 60.0)
_CHANGED = frozenset({"declined", "executed", "failed", "started", "superseded"})


def _session_key(request: Request) -> str:
    cookie = request.cookies.get(auth.COOKIE) or ""
    return hashlib.sha256(b"atlas-decision:" + cookie.encode()).hexdigest()[:32]


def _too_many(limiter: PerAddressLimiter, key: str) -> HTTPException:
    wait = max(1, math.ceil(limiter.retry_after(key)))
    return HTTPException(429, f"rate limited: too many decisions this minute; "
                              f"try again in {wait} s",
                         headers={"Retry-After": str(wait)})


@router.post("/approvals/{pid}/decision")
async def approval_decision(pid: int, request: Request, session: SignedIn) -> dict:
    from ..kernel.approvals import decide
    from ..kernel.audit import audit
    from ..kernel.contracts import Tier
    from .main import DEFAULT_ALLOWED_HOSTS, _allowed_hosts

    # Unset ATLAS_ALLOWED_HOSTS turns the global Host middleware off (Caddy's
    # public names); it never turns this check off: the decision route then
    # answers only the loopback names (the owner 2026-10-29).
    hosts = _allowed_hosts() or frozenset(DEFAULT_ALLOWED_HOSTS)
    if not auth.same_origin(request, hosts):
        raise HTTPException(403, "this request did not come from the dashboard page")
    if not auth.csrf_ok(request):
        raise HTTPException(403, "missing or wrong CSRF token; reload the page")
    try:
        body = DecisionBody.model_validate(json.loads(await request.body() or b"null"))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "the body must be exactly {\"verdict\": "
                                 "\"approve\"} or {\"verdict\": \"decline\"}") from exc
    client = peers.effective_client(request)
    key = _session_key(request)
    if _decision_limit.limited(key):
        raise _too_many(_decision_limit, key)
    if not _decision_calls.allow(key):
        raise _too_many(_decision_calls, key)

    card = await fetch_one(
        "SELECT id, action, status, expires_at FROM proposals WHERE id=$1", pid)
    if card is None:
        raise HTTPException(404, f"no proposal #{pid}")
    if card["status"] != "pending":
        raise HTTPException(409, f"#{pid} is {card['status']}, not pending")
    decision = REGISTRY.decisions.get(card["action"])
    if decision is None or not decision.allowed or decision.tier is not Tier.ASK:
        raise HTTPException(403, f"#{pid} is not a Tier 2 card the dashboard may answer")

    verdict = await decide(pid, body.verdict, via=f"dashboard:{session.user}")
    if verdict.outcome in _CHANGED:
        _decision_limit.hit(key)
    await audit("dashboard.decision", str(pid), {
        "verdict": body.verdict, "outcome": verdict.outcome,
        "origin": auth.request_origin(request), "client": client})
    return envelope(
        verdict={"ok": verdict.ok, "outcome": verdict.outcome,
                 "detail": verdict.detail, "proposal_id": verdict.proposal_id,
                 "action": verdict.action, "executed": verdict.executed},
        watch_only=watch_only(),
    )


# ----------------------------------------------------------------- status

@router.get("/status")
async def status(_s: SignedIn) -> dict:
    """Counters for the bottom bar. Read fresh, every time.

    Deliberately a separate call from /overview so the bar can refresh without
    re-running the per-check join behind the tiles.
    """
    from ..kernel.agent_activity import day_start
    since = day_start(datetime.now(UTC))        # New York's day, not UTC's
    scans = await fetch_one(
        "SELECT count(*) AS n FROM observations WHERE at >= $1", since)
    issues = await fetch_one(
        """SELECT count(*) AS n FROM findings
           WHERE at >= $1 AND health <> 'ok'""", since)
    fixed = await fetch_one(
        """SELECT count(*) AS n FROM actions_log
           WHERE at >= $1 AND event = 'execute.finish'""", since)
    waiting = await fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE status = 'pending'")
    unknown = await fetch_one(
        """SELECT count(*) AS n FROM findings
           WHERE at >= $1 AND health = 'unknown'""", since)
    # An approved card is the executor's until it finishes (executed, failed
    # or superseded): the home page's "Executing" is this count, read here.
    running = await fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE status = 'approved'")

    def n(row) -> int:
        return int(row["n"]) if row else 0

    return envelope(
        since=since.isoformat(),
        scans_today=n(scans),
        issues_found=n(issues),
        # Distinguished on purpose: a check that could not run is not an issue
        # ATLAS found, it is an answer he does not have.
        unknown_today=n(unknown),
        auto_fixed=n(fixed),
        waiting_on_you=n(waiting),
        executing=int(running["n"]) if running else None,
        watch_only=watch_only(),
    )


# --------------------------------------------------------------- activity

@router.get("/activity")
async def activity(
    _s: SignedIn,
    module: str | None = None,
    kind: str | None = None,
    day: str | None = None,
    limit: int = 150,
) -> dict:
    """Everything ATLAS did: what he checked, what he found, what he ran.

    Three sources merged into one timeline — observations, findings and the
    audit log — because that is how it reads to a person, even though the
    system stores them separately.
    """
    limit = max(1, min(500, limit))
    kinds = {"scan", "finding", "action"}
    wanted = kind if kind in kinds else None

    rows: list[dict[str, Any]] = []

    if wanted in (None, "scan"):
        for row in await fetch_all(
            """SELECT o.at, p.name AS module, c.name AS check_name,
                      o.health, o.detail, o.value, o.unit
               FROM observations o
               JOIN checks c ON c.id = o.check_id
               JOIN properties p ON p.id = c.property_id
               WHERE ($1::text IS NULL OR p.name = $1)
                 AND ($2::date IS NULL OR (o.at AT TIME ZONE 'UTC')::date = $2::date)
               ORDER BY o.at DESC LIMIT $3""", module, day, limit):
            rows.append({
                "kind": "scan", "at": row["at"].isoformat(),
                "module": row["module"],
                "subject": f"{row['module']}.{row['check_name']}",
                "health": row["health"], "detail": row["detail"] or "",
                "value": row["value"], "unit": row["unit"], "id": None,
            })

    if wanted in (None, "finding"):
        for row in await fetch_all(
            """SELECT id, at, property, check_key, health, severity, title, detail
               FROM findings
               WHERE ($1::text IS NULL OR property = $1)
                 AND ($2::date IS NULL OR (at AT TIME ZONE 'UTC')::date = $2::date)
               ORDER BY at DESC LIMIT $3""", module, day, limit):
            rows.append({
                "kind": "finding", "at": row["at"].isoformat(),
                "module": row["property"], "subject": row["check_key"],
                "health": row["health"], "severity": row["severity"],
                "detail": row["title"], "body": row["detail"],
                "id": row["id"], "value": None, "unit": None,
            })

    if wanted in (None, "action"):
        for row in await fetch_all(
            """SELECT id, at, event, subject, payload FROM actions_log
               WHERE event LIKE 'execute%' OR event LIKE 'proposal%'
                     OR event LIKE 'rollback%' OR event = 'preview'
               ORDER BY at DESC LIMIT $1""", limit):
            owner = row["subject"].split(".")[0] if "." in row["subject"] else ""
            if module and owner != module:
                continue
            if day and row["at"].strftime("%Y-%m-%d") != day:
                continue
            rows.append({
                "kind": "action", "at": row["at"].isoformat(),
                "module": owner, "subject": row["subject"],
                "health": None, "detail": row["event"],
                "body": row["payload"], "id": row["id"],
                "value": None, "unit": None,
            })

    rows.sort(key=lambda r: r["at"], reverse=True)
    return envelope(activity=rows[:limit],
                    filters={"module": module, "kind": wanted, "day": day,
                             "limit": limit})


# ---------------------------------------------------------------- reports

@router.get("/reports")
async def reports(
    _s: SignedIn,
    kind: str | None = None,
    module: str | None = None,
    since: str | None = None,
    limit: int = 60,
) -> dict:
    limit = max(1, min(200, limit))
    rows = await fetch_all(
        """SELECT id, at, kind, title, delivered_via,
                  left(body, 240) AS excerpt
           FROM reports
           WHERE ($1::text IS NULL OR kind = $1)
             AND ($2::date IS NULL OR (at AT TIME ZONE 'UTC')::date >= $2::date)
             AND ($3::text IS NULL OR body ILIKE '%' || $3 || '%')
           ORDER BY at DESC LIMIT $4""", kind, since, module, limit)
    return envelope(reports=[{
        "id": r["id"], "at": r["at"].isoformat(), "kind": r["kind"],
        "title": r["title"], "delivered_via": r["delivered_via"],
        "excerpt": r["excerpt"],
    } for r in rows])


@router.get("/reports/{rid}")
async def report_detail(rid: int, _s: SignedIn) -> dict:
    row = await fetch_one(
        "SELECT id, at, kind, title, body, sections, delivered_via "
        "FROM reports WHERE id = $1", rid)
    if row is None:
        raise HTTPException(404, "no such report")
    sections = row["sections"]
    if isinstance(sections, str):
        sections = json.loads(sections or "[]")
    return envelope(report={
        "id": row["id"], "at": row["at"].isoformat(), "kind": row["kind"],
        "title": row["title"], "body": row["body"], "sections": sections,
        "delivered_via": row["delivered_via"],
    })


# ------------------------------------------------------------ system core

def _read_cpu() -> tuple[int, int] | None:
    """(busy, total) jiffies from /proc/stat. None if it cannot be read."""
    try:
        with open("/proc/stat") as fh:
            parts = fh.readline().split()
    except OSError:
        return None
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    values = [int(v) for v in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values) - idle, sum(values)


_cpu_last: tuple[int, int] | None = None


def _cpu_percent() -> float | None:
    """CPU busy since the previous call.

    A single /proc/stat read gives the average since boot, which on a
    long-running box is a flat, meaningless number. The delta between two reads
    is the only honest answer, so the first call returns None rather than a
    figure that looks real.
    """
    global _cpu_last
    now = _read_cpu()
    if now is None:
        return None
    previous, _cpu_last = _cpu_last, now
    if previous is None:
        return None
    busy = now[0] - previous[0]
    total = now[1] - previous[1]
    if total <= 0:
        return None
    return round(100.0 * busy / total, 1)


def _meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                out[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {}
    return out


def _snapshot() -> dict[str, Any]:
    """Read the box. Synchronous on purpose — called through a thread.

    These are /proc reads and take microseconds, but "no blocking calls in the
    event loop" is a rule worth keeping literal: the exception is always the
    one that turns out to block.
    """
    import shutil

    mem = _meminfo()
    total = mem.get("MemTotal")
    available = mem.get("MemAvailable")
    try:
        usage = shutil.disk_usage("/")
        disk = {"total": usage.total, "used": usage.used,
                "percent": round(100.0 * usage.used / usage.total, 1)}
    except OSError:
        disk = {"total": None, "used": None, "percent": None}

    try:
        with open("/proc/uptime") as fh:
            uptime: float | None = float(fh.readline().split()[0])
    except (OSError, ValueError, IndexError):
        uptime = None

    return {
        "cpu_percent": _cpu_percent(),
        "memory": {
            "total": total,
            "used": (total - available) if (total and available) else None,
            "percent": (round(100.0 * (total - available) / total, 1)
                        if (total and available) else None),
        },
        "disk": disk,
        "uptime_s": uptime,
    }


@router.get("/judgment")
async def judgment(_s: SignedIn) -> dict:
    """The learning layer, read-only: reliability curve, the one line that
    matters, who is serving, and the tournament log. Nothing here can be
    written to from a browser; the models change only by the nightly
    tournament and by `atlas model rollback` on the box."""
    from atlas.kernel.judgment.status import snapshot

    return envelope(**await snapshot(), watch_only=watch_only())


@router.get("/system")
async def system(_s: SignedIn) -> dict:
    """The box ATLAS runs on. Anything unreadable is null, never a guess."""
    import asyncio

    return envelope(**await asyncio.to_thread(_snapshot), watch_only=watch_only())


def _last_model(row: dict | None) -> str | None:
    """`"provider:model"` for the last agent_runs row, or None when there is
    no run yet -- or when the run recorded neither (a `LocalModelUnavailable`
    run writes empty strings for both, which is a real "no model answered",
    not a formatting gap)."""
    if not row:
        return None
    provider, model = row.get("provider") or None, row.get("model") or None
    if provider and model:
        return f"{provider}:{model}"
    return model or provider


# A roster agent's group for the redesigned Agents map (Task 69): "lead" for
# the front door (agents.DEFAULT_AGENT, inbox since 2026-10-28), "businesses"
# for every agent `businesses.py` builds from a
# `Business` (read from `BY_NAME`, never hand-listed here — a new business
# places itself without this file changing), "functions" for everything
# else. Read the ROSTER names in the code; never invent one.
def _agent_group(name: str, businesses_by_name: dict) -> str:
    from ..kernel.agents import DEFAULT_AGENT
    if name == DEFAULT_AGENT:
        return "lead"
    if name in businesses_by_name:
        return "businesses"
    return "functions"


# The map's own shortened name for a card, distinct from `title` (which
# Fleet's cards, the org chart and DeepDive all still read unchanged).
# the owner's own words for the redesign: guardian reads "Vault", school reads
# "College" rather than its full "College (the university, Fall 2026)".
_DISPLAY_NAME = {"guardian": "Vault", "school": "College"}


@router.get("/agents")
async def agents(_s: SignedIn) -> dict:
    """The roster, with the model tier each one runs at, whether that tier
    actually resolves to a provider right now, its most recent run, whether
    it is READY to run at all (Task 69: `agents.readiness`), its group for
    the redesigned map, and when its next shift is due.

    A roster agent that has never run still appears here — a one-human
    company's fleet screen has to show every card, not just the ones with a
    heartbeat — and its `last_run_at` is `null`, never a guessed timestamp.
    """
    from ..kernel import agents as agents_module
    from ..kernel import scheduler as scheduler_module
    from ..kernel.businesses import BY_NAME as businesses_by_name
    from ..kernel.llm import ROUTES
    from ..kernel.personas import as_dict as persona_dict
    from ..kernel.personas import persona_for

    ROSTER = agents_module.ROSTER
    from ..kernel.agent_activity import day_start
    now = datetime.now(UTC)
    # "Today" is New York's day: UTC midnight is 20:00 there, and every
    # agent read 0 all evening (the owner 2026-10-29).
    midnight = day_start(now)

    # A read that fails is UNKNOWN for every row, never an empty table that
    # renders as "ok" (invariant 7).
    read_failed = False
    latest: dict[str, Any] = {}
    runs_24h: dict[str, int] = {}
    shift: dict[str, Any] = {}
    output: dict[str, int] = {}
    unrouted: set[str] = set()
    try:
        latest_rows = await fetch_all(
            """SELECT DISTINCT ON (agent) agent, at, outcome, model, provider
               FROM agent_runs ORDER BY agent, at DESC""")
        latest = {r["agent"]: r for r in latest_rows}
        count_rows = await fetch_all(
            """SELECT agent, count(*) AS n FROM agent_runs
               WHERE at > now() - interval '24 hours' GROUP BY agent""")
        runs_24h = {r["agent"]: int(r["n"]) for r in count_rows}
        # The last SUCCESSFUL shift, the last attempt and the failed/deferred
        # attempts -- the scheduler's own query, so both read the same truth.
        shift_rows = await fetch_all(
            scheduler_module.shift_rows_sql(), *scheduler_module.shift_rows_args(now))
        shift = {r["agent"]: r for r in shift_rows}
        # Today's (New York) runs that a model actually answered, failed and
        # deferred shifts left out.
        output_rows = await fetch_all(
            """SELECT agent, count(*) AS today_output FROM agent_runs
               WHERE at >= $1::timestamptz AND coalesce(model, '') <> ''
                 AND coalesce(outcome, '') <> ''
                 AND NOT (outcome LIKE ANY($2::text[]))
               GROUP BY agent""",
            midnight, list(agents_module.SHIFT_UNSUCCESSFUL_LIKE))
        output = {r["agent"]: int(r["today_output"]) for r in output_rows}
        unrouted_rows = await fetch_all(
            """SELECT DISTINCT subject FROM actions_log
               WHERE event = 'agent.shift_alert_unrouted' AND at >= $1::timestamptz""",
            midnight)
        unrouted = {r["subject"] for r in unrouted_rows}
    except Exception as exc:                            # noqa: BLE001
        log.warning("roster runs unreadable (%s)", type(exc).__name__)
        read_failed = True
    read_at = datetime.now(UTC).isoformat()
    cycle = scheduler_module.LAST_CYCLE_AT
    cycle_age = None if cycle is None else max(0, int(time.monotonic() - cycle))

    out = []
    for name, agent in sorted(ROSTER.items()):
        need = getattr(agent, "need", None)
        chain = ROUTES.get(need, []) if need else []
        last = latest.get(name)
        ready, reasons = agents_module.readiness(name)
        srow = shift.get(name)
        last_ok = srow["last_shift"] if srow else None
        failures = list((srow["failures"] if srow else None) or [])
        st = agents_module.shift_state(name, last_ok, failures, now)
        next_shift = st.next_at if ready else None
        shift_state = st.state if ready else "off"
        if read_failed:
            status = "unknown"
        elif ready and (shift_state in ("failed", "overdue")
                        or (shift_state == "due" and st.attempts > 0)):
            # A due shift with an attempt behind it is a retry after a
            # failed, deferred or unanswered shift: failing, never idle/ok.
            status = "failing"
        elif not ready or shift_state in ("off", "due"):
            status = "idle"
        else:
            status = "ok"
        last_attempt = srow["last_attempt"] if srow else None
        # One short, plain line; the model's prose is never handed over raw
        # (the owner 2026-10-29). `last_outcome_detail` is the same text cleaned,
        # longer, for the page's expand.
        last_shift_outcome = (
            agents_module.outcome_line(last["outcome"])
            if last and last_attempt and last["at"] == last_attempt else None)
        out.append({
            "role": getattr(agent, "title", name),
            "status": status,
            "shift_state": "unknown" if read_failed else shift_state,
            "last_shift_at": last_attempt.isoformat() if last_attempt else None,
            "last_shift_outcome": last_shift_outcome,
            "attempts_today": st.attempts,
            "today_output": None if read_failed else output.get(name, 0),
            "alert_unrouted": name in unrouted,
            "scheduler_cycle_age_s": cycle_age,
            "read_at": read_at,
            "name": name,
            "title": getattr(agent, "title", name),
            "display": _DISPLAY_NAME.get(name, getattr(agent, "title", name)),
            "brief": getattr(agent, "brief", ""),
            "need": str(need) if need else None,
            # "Running" would be a lie: agents are invoked per request, not
            # resident. What matters is whether this one CAN run.
            "available": bool(chain),
            "providers": [p.name for p in chain],
            "tools": len(getattr(agent, "tools", ()) or ()),
            "last_run_at": last["at"].isoformat() if last else None,
            "last_outcome": agents_module.outcome_line(last["outcome"]) if last else None,
            "last_outcome_detail": (agents_module.outcome_detail(last["outcome"])
                                    if last else None),
            "runs_24h": runs_24h.get(name, 0),
            # The model that actually answered the LAST run -- never the
            # configured pin as if it had run (invariant 6: a check that
            # cannot run is UNKNOWN, not a guess dressed as a measurement).
            # `agent.model` is Task 53's per-agent pin; it is deliberately
            # never read here.
            "model": _last_model(last),
            "model_at": last["at"].isoformat() if last else None,
            "handoff": sorted(getattr(agent, "handoff", ()) or ()),
            "memory_scope": list(getattr(agent, "memory_scope", ()) or ()),
            "ready": ready,
            "reasons": reasons,
            "group": _agent_group(name, businesses_by_name),
            "next_shift_at": next_shift.isoformat() if next_shift else None,
            # Its rung on the autonomy ladder, counted from the tiers set in
            # code: watches, acts alone (Tier 1), asks first (Tier 2), never.
            "ladder": agents_module.ladder(name),
            # Presentation only (kernel/personas.py): the name the Agency
            # room and the map show beside `display`.
            "persona": persona_dict(persona_for(
                name, getattr(agent, "title", name), getattr(agent, "brief", ""))),
        })
    return envelope(agents=out)


WARM_JOB = "warm agents"


@router.post("/agents/warm")
async def agents_warm(_s: SignedIn, request: Request) -> dict:
    """Give every roster agent one real, tool-free run (`agents.warmup`), so
    the Agents map's LAST RUN and MODEL fill in with the truth instead of
    "never" -- started in the tracked background registry
    (`kernel.background.spawn`, accounted for at shutdown, unlike a bare
    `create_task`) so this request returns at once rather than blocking on
    every agent's model call in turn. The Agents page refreshes on its own
    timer to pick up the rows as they land.

    409s a second click while one sweep is already running, rather than
    starting two uncoordinated passes over the same roster.

    An optional JSON body `{"name": "<agent>"}` (Task 69: the map's per-card
    "Run self-test" button) warms that one agent only -- 404 for a name not
    in the roster, 409 while the full sweep or that same agent's own warm-up
    is already running. No body (or a body without `name`) is the original,
    unchanged full-roster sweep.

    Never a path to the executor (see this module's docstring): `warmup`
    only calls an agent with its tool list forced empty and writes
    `agent_runs`, the same as any chat or voice turn already does.
    """
    from ..kernel import background
    from ..kernel.agents import ROSTER, warmup

    name: str | None = None
    raw = await request.body()
    if raw:
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            name = payload.get("name")

    if name is not None:
        if name not in ROSTER:
            raise HTTPException(404, f"no such agent: {name}")
        job = f"{WARM_JOB} · {name}"
        if WARM_JOB in background.running() or job in background.running():
            raise HTTPException(409, f"already warming {name}")

        async def warm_one() -> None:
            try:
                await warmup(name)
            except Exception:
                log.exception("warm-up crashed for %s", name)

        background.spawn(warm_one(), job)
        return envelope(started=True, agents=[name])

    if WARM_JOB in background.running():
        raise HTTPException(409, "already warming the roster")

    names = sorted(ROSTER)

    async def warm_all() -> None:
        for n in names:
            try:
                await warmup(n)
            except Exception:
                # One agent's warm-up failing must not stop the rest --
                # `warmup` itself already turns a model failure into a
                # recorded row rather than an exception, so reaching this is
                # a bug in warmup, not an expected outcome; log it and move
                # on rather than leaving every later agent at "never".
                log.exception("warm-up crashed for %s", n)

    background.spawn(warm_all(), WARM_JOB)
    return envelope(started=True, agents=names)


@router.get("/agents/feed")
async def agents_feed(_s: SignedIn, limit: int = 50) -> dict:
    """The agent-to-agent live feed: the most recent runs, each with the
    specialist it handed off to when it called `ask_specialist` — or `null`
    when it answered on its own."""
    limit = max(1, min(200, limit))
    rows = await fetch_all(
        """SELECT at, agent, task, outcome, turns, model, transcript
           FROM agent_runs ORDER BY at DESC LIMIT $1""", limit)
    items = [{
        "at": r["at"].isoformat(),
        "agent": r["agent"],
        "task": (r["task"] or "")[:140],
        "outcome": r["outcome"],
        "turns": r["turns"],
        "model": r["model"],
        "to": _handoff_target(r["transcript"]),
    } for r in rows]
    return envelope(items=items)


def _handoff_target(transcript: Any) -> str | None:
    """Find the agent named in an `ask_specialist` tool call, wherever one
    appears in the transcript. Different writers shape a transcript entry
    differently (a `{role, content}` turn, a `{turn, tool_calls}` turn, a
    bare call) so this walks the whole structure rather than assuming one
    schema, and returns `null` — never a guess — when nothing matches or the
    column is not something this can even read."""
    if isinstance(transcript, str):
        try:
            transcript = json.loads(transcript) if transcript else []
        except (TypeError, ValueError):
            return None
    return _find_ask_specialist(transcript)


def _find_ask_specialist(node: Any) -> str | None:
    if isinstance(node, dict):
        if node.get("name") == "ask_specialist":
            raw_input = node.get("input")
            target = raw_input.get("agent") if isinstance(raw_input, dict) else None
            if isinstance(target, str) and target:
                return target
        for value in node.values():
            if found := _find_ask_specialist(value):
                return found
    elif isinstance(node, list):
        for item in node:
            if found := _find_ask_specialist(item):
                return found
    return None


# --------------------------------------------------- helpers

def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


@router.get("/llm")
async def llm_status(_s: SignedIn) -> dict:
    """Which providers are actually wired, per need. Read from the routing
    table rather than from the presence of an env var, so it reflects what
    would really be called."""
    from ..kernel.llm import ROUTES, Need

    needs = {}
    for need in Need:
        chain = ROUTES.get(need, [])
        needs[str(need)] = {
            "providers": [p.name for p in chain],
            "connected": bool(chain),
            # CRITICAL means two INDEPENDENT providers. One vendor twice is
            # not a second opinion, and this is where that shows.
            #
            # None when the question does not apply: a chain with nothing in it
            # is not "single vendor", it is not connected — and reporting the
            # wrong problem is worse than reporting none.
            "cross_provider": (
                (len({p.name for p in chain[:2]}) >= 2)
                if (need is Need.CRITICAL and len(chain) >= 2) else None),
        }
    vendors: dict[str, bool] = {}
    for chain in ROUTES.values():
        for provider in chain:
            vendors[provider.name] = True
    return envelope(needs=needs, vendors=sorted(vendors))


def _breaker_json(provider: str, model: str) -> dict[str, Any]:
    """`breaker.state_of` with `until` as ISO time. Read-only: it never
    claims the half-open probe (`breaker.allow` would)."""
    from ..kernel import breaker
    st = breaker.state_of((provider, model))
    until = st["until"]
    return {
        "state": st["state"],
        "why": st["why"] or None,
        "until": (datetime.fromtimestamp(float(until), UTC).isoformat()
                  if isinstance(until, int | float) else None),
        "fails": st["fails"],
    }


@router.get("/models")
async def models(_s: SignedIn) -> dict:
    """Which model each Need asks, in order, and the state of each one.

    Per Need: the chain `think()` would walk right now (a Need with no chain
    of its own borrows REASON's, as `think()` does -- PRIVATE never does),
    each deployment with its breaker (closed, open with why and until, or
    half-open), and the deployments the boot probes dropped. Per agent: its
    Need, its pin if it has one (a pin is tried first and has no breaker),
    the model that would be asked first now, and the provider:model that
    actually answered its last recorded run (`agent_runs`) -- null, never a
    guess, when it has not run.

    Provider and model names only. No key, no base URL (Cloudflare's carries
    the account), no env value; model names pass the stored-text redactor
    as a net.
    """
    from ..kernel import agents as agents_module
    from ..kernel.llm import DROPPED, ROUTES, Need, _resolve_pin, free_only
    from ..modules._http import redact_stored

    def deployment(provider: Any) -> dict[str, Any]:
        name = str(getattr(provider, "name", "?"))
        model = str(getattr(provider, "model", "") or "")
        return {"provider": name, "model": redact_stored(model),
                "breaker": _breaker_json(name, model)}

    def chain_of(need: Need) -> tuple[list[Any], str | None]:
        own = ROUTES.get(need)
        if own or need is Need.PRIVATE:
            return list(own or []), None
        return list(ROUTES.get(Need.REASON) or []), str(Need.REASON)

    needs_out: list[dict[str, Any]] = []
    legend: dict[tuple[str, str], dict[str, Any]] = {}
    for need in Need:
        chain, borrowed = chain_of(need)
        rows = [deployment(p) for p in chain]
        dropped = [{"provider": p, "model": redact_stored(m), "why": why}
                   for (n, p, m), why in sorted(DROPPED.items()) if n == str(need)]
        needs_out.append({"need": str(need), "borrowed_from": borrowed,
                          "chain": rows, "dropped": dropped})
        for row in rows:
            entry = legend.setdefault((row["provider"], row["model"]), {
                **row, "needs": [], "dropped": None})
            entry["needs"].append(str(need))
        for row in dropped:
            entry = legend.setdefault((row["provider"], row["model"]), {
                "provider": row["provider"], "model": row["model"],
                "breaker": None, "needs": [], "dropped": row["why"]})

    latest_rows = await fetch_all(
        """SELECT DISTINCT ON (agent) agent, at, model, provider
           FROM agent_runs ORDER BY agent, at DESC""")
    latest = {r["agent"]: r for r in latest_rows}

    agents_out: list[dict[str, Any]] = []
    for name, agent in sorted(agents_module.ROSTER.items()):
        need = getattr(agent, "need", None)
        chain = chain_of(need)[0] if isinstance(need, Need) else []
        pin = getattr(agent, "model", None) if need is not Need.PRIVATE else None
        pin_out = None
        if pin:
            resolved, why = _resolve_pin(pin)
            pin_out = {"model": redact_stored(pin), "usable": resolved is not None,
                       "why": why}
        # The first deployment `think()` would ask now: the pin when it can
        # be tried, else the first whose breaker is not open (all open: the
        # first, since the whole chain is then tried anyway).
        nxt: dict[str, Any] | None = None
        if pin_out and pin_out["usable"]:
            nxt = {"model": pin_out["model"], "kind": "pin"}
        elif chain:
            rows = [deployment(p) for p in chain]
            first = next((r for r in rows if r["breaker"]["state"] != "open"), rows[0])
            nxt = {"model": f"{first['provider']}:{first['model']}", "kind": "chain"}
        last = latest.get(name)
        agents_out.append({
            "name": name,
            "need": str(need) if need else None,
            "pin": pin_out,
            "next": nxt,
            # What answered its last recorded run -- `_last_model`, the same
            # reading the roster shows. Null with `last_at` null: not run.
            "last": redact_stored(_last_model(last)) if _last_model(last) else None,
            "last_at": last["at"].isoformat() if last else None,
        })

    return envelope(
        free_only=free_only(),
        needs=needs_out,
        deployments=sorted(legend.values(), key=lambda d: (d["provider"], d["model"])),
        agents=agents_out,
    )


@router.get("/memory")
async def memory_stats(_s: SignedIn) -> dict:
    """Counts per kind. The four kinds are the contract; a missing one is
    shown as zero rather than omitted, so a broken writer is visible."""
    rows = await fetch_all(
        "SELECT kind, count(*) AS n FROM memories GROUP BY kind")
    counts = {r["kind"]: int(r["n"]) for r in rows}
    turns = await fetch_one("SELECT count(*) AS n FROM voice_turns")
    tools = await fetch_one(
        "SELECT count(*) AS n FROM actions_log WHERE event LIKE 'execute%'")
    return envelope(
        kinds={kind: counts.get(kind, 0)
               for kind in ("episodic", "semantic", "procedural", "working")},
        total=sum(counts.values()),
        voice_turns=int(turns["n"]) if turns else 0,
        tool_calls=int(tools["n"]) if tools else 0,
    )


@router.get("/memory/graph")
async def memory_graph(_s: SignedIn) -> dict:
    """The shared memory graph: one node per namespace, sized by what is
    actually stored there, and one edge per roster agent for every namespace
    its `memory_scope` reaches — the graph five specialists read and write
    over, not an org chart drawn from imagination.

    A namespace only an agent's `memory_scope` names, with nothing written to
    it yet, still gets a node with a zero total — an edge pointing at a node
    that does not exist would be a broken graph, not an empty one.
    """
    from ..kernel.agents import ROSTER

    rows = await fetch_all(
        "SELECT namespace, kind, count(*) AS n FROM memories "
        "GROUP BY namespace, kind")
    by_ns: dict[str, dict[str, int]] = {}
    for r in rows:
        by_ns.setdefault(r["namespace"], {})[r["kind"]] = int(r["n"])

    namespaces = set(by_ns)
    for agent in ROSTER.values():
        namespaces.update(getattr(agent, "memory_scope", ()) or ())

    nodes = [{
        "id": f"ns:{ns}",
        "kind_counts": by_ns.get(ns, {}),
        "total": sum(by_ns.get(ns, {}).values()),
    } for ns in sorted(namespaces)]

    edges = [
        {"from": f"agent:{name}", "to": f"ns:{ns}"}
        for name, agent in sorted(ROSTER.items())
        for ns in getattr(agent, "memory_scope", ()) or ()
    ]

    agent_list = [{"id": name, "title": getattr(agent, "title", name)}
                  for name, agent in sorted(ROSTER.items())]

    return envelope(nodes=nodes, edges=edges, agents=agent_list)


# ------------------------------------------------------------------ brain
#
# The regions behind the humanoid stage. This is a picture of ATLAS's own
# memory and nothing else: namespaces grouped by what they are for, sized by
# what is stored in them, and lit by what has actually been recalled in the
# last hour. There is no model of a brain here and no inferred structure —
# every figure is a count of rows, read fresh on every request.
#
# The map is a constant rather than a heuristic, because a namespace that
# silently moved region between deploys would make the view unreadable.

BRAIN_REGIONS: dict[str, tuple[str, ...]] = {
    "PREFRONTAL": ("commitments", "preferences", "rules"),
    "HIPPOCAMPUS": ("threads", "conversations", "incidents", "reviews"),
    "ASSOCIATION": ("people", "buyers", "audience"),
    "SENSORY": ("observations", "infra", "security"),
    "CONCEPT": ("vault", "library", "runbooks", "research"),
    "CREATIVE": ("brand_voice", "content"),
    # MOTOR has no namespace: acting is not remembering. Its figures come
    # from the action log, and it is never a home for a stray namespace.
    "MOTOR": (),
    # Everything the map does not name. Never dropped: a namespace being
    # written to that nobody thought to classify is exactly the thing this
    # screen should make visible.
    "OTHER": (),
}

_NAMED = {ns: region for region, spec in BRAIN_REGIONS.items() for ns in spec}


def brain_region_for(namespace: str) -> str:
    return _NAMED.get(namespace, "OTHER")


def _stamp(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


@router.get("/brain")
async def brain(_s: SignedIn) -> dict:
    """Memory as regions: what is stored, and what is being used.

    `memories` counts rows in `memories`, except for MOTOR, which counts
    completed actions in `actions_log` (`execute.finish` — the same event
    /status calls auto_fixed, so the two screens cannot disagree about what
    an action is). `reads_1h` counts `memory_reads` rows in the last hour,
    which `memory.recall()` writes one of per namespace it searched.

    `firing` is a region's reads against the busiest region's, so the
    brightest is 1 and a quiet hour lights nothing rather than lighting
    everything equally. Nothing read anywhere is zero across the board, never
    a division by zero and never a default.
    """
    from ..kernel.agents import ROSTER

    stored = await fetch_all(
        "SELECT namespace, count(*) AS n FROM memories GROUP BY namespace")
    reads = await fetch_all(
        """SELECT namespace, max(at) AS last_at,
                  count(*) FILTER (WHERE at >= now() - interval '1 hour')
                    AS reads_1h
           FROM memory_reads GROUP BY namespace""")
    acted = await fetch_one(
        """SELECT count(*) AS n, max(at) AS last_at,
                  count(*) FILTER (WHERE at >= now() - interval '1 hour')
                    AS recent
           FROM actions_log WHERE event = 'execute.finish'""")

    by_ns = {r["namespace"]: int(r["n"]) for r in stored}
    read_by_ns = {r["namespace"]: r for r in reads}

    # Three sources, because each one can name a namespace the others do not:
    # what is stored, what an agent is scoped to read, and what has actually
    # been read. A namespace that only ever appears in the read log is still
    # part of the picture.
    universe: set[str] = set(by_ns) | set(read_by_ns) | set(_NAMED)
    for agent in ROSTER.values():
        universe.update(getattr(agent, "memory_scope", ()) or ())

    grouped: dict[str, list[str]] = {region: [] for region in BRAIN_REGIONS}
    for ns in sorted(universe):
        grouped[brain_region_for(ns)].append(ns)

    regions: list[dict[str, Any]] = []
    for name, namespaces in grouped.items():
        if name == "MOTOR":
            regions.append({
                "region": name,
                # A pseudo-namespace, named so the label has something to say.
                "namespaces": ["actions"],
                "memories": int(acted["n"]) if acted else 0,
                "last_read_at": _stamp(acted["last_at"]) if acted else None,
                "reads_1h": int(acted["recent"]) if acted else 0,
            })
            continue
        last: datetime | None = None
        recent = 0
        for ns in namespaces:
            row = read_by_ns.get(ns)
            if row is None:
                continue
            recent += int(row["reads_1h"] or 0)
            at = row["last_at"]
            if isinstance(at, datetime) and (last is None or at > last):
                last = at
        regions.append({
            "region": name,
            "namespaces": namespaces,
            "memories": sum(by_ns.get(ns, 0) for ns in namespaces),
            "last_read_at": _stamp(last),
            "reads_1h": recent,
        })

    busiest = max((r["reads_1h"] for r in regions), default=0)
    for region in regions:
        region["firing"] = (
            round(region["reads_1h"] / busiest, 3) if busiest else 0)

    recent_reads = await fetch_all(
        """SELECT at, namespace, hits, agent FROM memory_reads
           ORDER BY at DESC LIMIT 30""")

    return envelope(
        regions=regions,
        recent=[{
            "at": _stamp(r["at"]),
            "namespace": r["namespace"],
            "hits": int(r["hits"] or 0),
            "agent": r["agent"],
        } for r in recent_reads],
    )


# --------------------------------------------------------------- findings

@router.get("/findings")
async def findings(
    _s: SignedIn,
    module: str | None = None,
    severity: int | None = None,
    limit: int = 100,
) -> dict:
    limit = max(1, min(500, limit))
    rows = await fetch_all(
        """SELECT f.*, p.status AS proposal_status, p.action AS proposal_action
           FROM findings f LEFT JOIN proposals p ON p.id = f.proposal_id
           WHERE ($1::text IS NULL OR f.property = $1)
             AND ($2::int  IS NULL OR f.severity >= $2)
           ORDER BY f.at DESC LIMIT $3""", module, severity, limit)
    return envelope(findings=[_finding_json(r) for r in rows],
                    filters={"module": module, "severity": severity,
                             "limit": limit})


def _finding_json(row) -> dict[str, Any]:
    evidence = row["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence or "{}")
    return {
        "id": row["id"],
        "at": row["at"].isoformat(),
        "check": row["check_key"],
        "module": row["property"],
        "severity": row["severity"],
        "kind": row["kind"],
        "health": row["health"],
        "title": row["title"],
        "detail": row["detail"],
        "value": row["value"],
        "unit": row["unit"],
        "evidence": evidence,
        "observed_at": row["observed_at"].isoformat(),
        "proposal": {
            "id": row["proposal_id"],
            "status": row["proposal_status"],
            "action": row["proposal_action"],
        } if row["proposal_id"] else None,
    }


@router.get("/findings/{fid}")
async def finding_detail(fid: int, _s: SignedIn) -> dict:
    row = await fetch_one(
        """SELECT f.*, p.status AS proposal_status, p.action AS proposal_action
           FROM findings f LEFT JOIN proposals p ON p.id = f.proposal_id
           WHERE f.id = $1""", fid)
    if row is None:
        raise HTTPException(404, "no such finding")
    return envelope(finding=_finding_json(row))


# ------------------------------------------------------------------ audit

@router.get("/audit")
async def audit_log(_s: SignedIn, limit: int = 200) -> dict:
    limit = max(1, min(1000, limit))
    rows = await fetch_all(
        """SELECT id, at, event, subject, payload, prev_digest, digest
           FROM actions_log ORDER BY id DESC LIMIT $1""", limit)
    return envelope(entries=[{
        "id": r["id"],
        "at": r["at"].isoformat(),
        "event": r["event"],
        "subject": r["subject"],
        "payload": r["payload"],
        "prev_digest": r["prev_digest"],
        "digest": r["digest"],
    } for r in rows])


@router.get("/audit/verify")
async def audit_verify(_s: SignedIn) -> dict:
    """Walk the whole chain server-side and report the first break.

    A GET, because it changes nothing. It was a POST, which made a pure read
    look like a write in the route table — and the route table is how you tell
    at a glance that this API cannot touch the world.

    The client is told nothing it could compute itself — the point is that the
    server re-derives every digest from the stored payloads.
    """
    ok, detail = await verify_chain()
    return envelope(ok=ok, detail=detail)


# ---------------------------------------------------------------- modules

@router.get("/modules/{name}")
async def module_detail(name: str, _s: SignedIn) -> dict:
    module = REGISTRY.modules.get(name)
    disabled_reason = REGISTRY.disabled.get(name)
    if module is None and disabled_reason is None:
        raise HTTPException(404, "no such module")

    from .system_check import effective_health

    moment = datetime.now(UTC)
    checks = []
    for key, check in sorted(REGISTRY.checks.items()):
        if not key.startswith(f"{name}."):
            continue
        history = await fetch_all(
            """SELECT o.at, o.value, o.health
               FROM observations o
               JOIN checks c ON c.id = o.check_id
               JOIN properties p ON p.id = c.property_id
               WHERE p.name = $1 AND c.name = $2
               ORDER BY o.at DESC LIMIT 60""",
            name, key.split(".", 1)[1])
        # The newest reading as it stands NOW, through the same staleness
        # rule as /overview and the spoken check -- the focus card badges
        # this, so a stale OK is Unknown there too, never a green OK.
        newest = history[0] if history else None
        health, stale = effective_health(
            newest["health"] if newest else None,
            newest["at"] if newest else None, check.interval, moment)
        checks.append({
            "name": key.split(".", 1)[1],
            "key": key,
            "description": check.description,
            "current": {
                "health": health,
                "stale": stale,
                "at": newest["at"].isoformat() if newest else None,
                "value": newest["value"] if newest else None,
            },
            "interval_s": int(check.interval.total_seconds()),
            "severity_on_fail": int(check.severity_on_fail),
            "baseline_sigma": check.baseline_sigma,
            "timeout_s": int(check.timeout.total_seconds()),
            "history": [{
                "at": h["at"].isoformat(),
                "value": h["value"],
                "health": h["health"],
            } for h in reversed(history)],
        })

    actions = []
    for key, decision in sorted(REGISTRY.decisions.items()):
        if not key.startswith(f"{name}."):
            continue
        action = REGISTRY.actions.get(key)
        actions.append({
            "name": key,
            "tier": int(decision.tier),
            "allowed": decision.allowed,
            "reason": decision.reason,
            "summary": action.summary if action else "",
            "params": action.params if action else {},
            "reversible": bool(action and action.rollback),
            "max_per_hour": action.max_per_hour if action else None,
        })

    return envelope(
        module={"name": name,
                "title": getattr(module, "title", name),
                "enabled": module is not None,
                "disabled_reason": disabled_reason,
                "brief": module.brief() if module else ""},
        checks=checks, actions=actions, watch_only=watch_only())


# ------------------------------------------------------------------ brief

# `agency_leads.status` as `db/schema.sql` declares it -- the header's chips
# must cover exactly this set (zero-filled when a status has no rows yet),
# never a status the column cannot hold (invariant 6's "never guess" reading
# extended to labels, not only numbers).
_LEAD_STATUSES = ("new", "audited", "mocked", "contacted", "closed", "dropped")
# The outreach pipeline's own derived states (see `_lead_json`) live in the
# same flat chip namespace -- the owner filters "who is waiting on me" the same
# way he filters "who is still new", and both are real, read states.
_OUTREACH_STATES = ("drafted", "waiting on you", "sent", "replied", "bounced")

_LEAD_SORTS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "business": lambda l: (l.get("business") or "").casefold(),
    "source": lambda l: (l.get("source") or "").casefold(),
    "status": lambda l: (l.get("status") or "").casefold(),
    # A lead with no score yet is not "worse" than a 0.0 -- it is unranked --
    # so it sorts below every real number regardless of direction, rather
    # than silently reading as the worst lead there is.
    "score": lambda l: (-1.0 if l.get("score") is None else l["score"]),
    "last_touch": lambda l: l.get("last_touch_at") or "",
    "next_step": lambda l: ((l.get("next_step") or {}).get("card_id") or 0),
}
_SORT_DEFAULTS_DESC = {"score", "last_touch", "next_step"}


def _agency_why_empty() -> str:
    """Why `agency_leads` has nothing in it, read from what is actually known
    server-side -- the registry and the one env var `agency.prospect` needs
    (`atlas/modules/agency/prospect.py`) -- never guessed. Called only when
    the table itself is empty, not when a search/status filter matches
    nothing (that is a filter with no results, a different, milder empty
    state the UI renders on its own)."""
    disabled = REGISTRY.disabled.get("agency")
    if disabled:
        return f"the agency module is disabled: {disabled}"
    if not os.getenv("GOOGLE_PLACES_API_KEY"):
        return "GOOGLE_PLACES_API_KEY is not set, so prospecting has never run."
    return ("GOOGLE_PLACES_API_KEY is set and the agency module is enabled, "
            "but agency.prospect has not produced any leads yet.")


def _matches_search(lead: dict[str, Any], q: str | None) -> bool:
    if not q:
        return True
    contact = lead.get("contact") or {}
    haystack = " ".join(str(v) for v in (
        lead.get("business"), lead.get("category"), lead.get("address"),
        contact.get("phone"), contact.get("site"), contact.get("email"),
        lead.get("audit_finding"),
    ) if v).casefold()
    return q.strip().casefold() in haystack


def _matches_status(lead: dict[str, Any], status: str | None) -> bool:
    if not status:
        return True
    return lead.get("status") == status or lead.get("outreach_state") == status


def _sort_leads(
    rows: list[dict[str, Any]], sort: str | None, sort_dir: str | None,
) -> list[dict[str, Any]]:
    """Pure and total: every column this returns is one the table already
    carries, so this never needs a query of its own -- filtering, sorting
    and paging all happen after the one join below has been read."""
    if sort not in _LEAD_SORTS:
        # Today's default, unchanged from before sorting existed: most
        # recently touched first, id as a stable tiebreak.
        return sorted(rows, key=lambda l: (l.get("last_touch_at") or "", l["id"]),
                      reverse=True)
    key = _LEAD_SORTS[sort]
    reverse = (sort_dir == "desc") if sort_dir in ("asc", "desc") \
        else sort in _SORT_DEFAULTS_DESC
    return sorted(rows, key=lambda l: (key(l), l["id"]), reverse=reverse)


def _status_counts(all_leads: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(_LEAD_STATUSES, 0)
    counts.update(dict.fromkeys(_OUTREACH_STATES, 0))
    for lead in all_leads:
        status = lead.get("status")
        if status is not None:
            counts[status] = counts.get(status, 0) + 1
        state = lead.get("outreach_state")
        if state is not None:
            counts[state] = counts.get(state, 0) + 1
    return counts


_LEADS_JOIN_SQL = """
    SELECT l.id AS lead_id, l.name, l.category, l.address, l.phone,
           l.website, l.score, l.features AS lead_features,
           l.at AS created_at, l.status AS lead_status,
           a.severity AS audit_severity, a.findings AS audit_findings,
           a.at AS audit_at,
           o.id AS outreach_id, o.status AS outreach_status,
           o.kind AS outreach_kind, o.at AS outreach_at,
           o.to_address AS outreach_to,
           p.id AS proposal_id, p.reason AS proposal_reason,
           p.expires_at AS proposal_expires_at
    FROM agency_leads l
    LEFT JOIN LATERAL (
        SELECT severity, findings, at FROM agency_audits
        WHERE lead_id = l.id ORDER BY at DESC, id DESC LIMIT 1
    ) a ON true
    LEFT JOIN LATERAL (
        SELECT id, status, kind, at, to_address FROM agency_outreach
        WHERE lead_id = l.id ORDER BY at DESC, id DESC LIMIT 1
    ) o ON true
    LEFT JOIN LATERAL (
        SELECT id, reason, expires_at FROM proposals
        WHERE action = 'agency.send_outreach' AND status = 'pending'
          AND expires_at > now()
          AND params->>'outreach_id' = o.id::text
        LIMIT 1
    ) p ON o.id IS NOT NULL
"""


@router.get("/leads")
async def leads(
    _s: SignedIn, limit: int = 100, offset: int = 0,
    q: str | None = None, status: str | None = None,
    sort: str | None = None, sort_dir: str | None = None,
) -> dict:
    """The Lead Pipeline (G16): every `agency_leads` row joined to its latest
    `agency_audits` and `agency_outreach`, filtered, sorted and paged.
    Read-only -- there is no action here, only what the agency module has
    already written.

    The join is read whole (the Places budget caps this table at a few
    hundred rows a month -- `agency_spend`, `prospect.py` -- so "whole" is
    never large) and everything after that -- search, the status chips,
    sorting, paging -- is plain Python against the same rows, rather than
    three near-duplicate WHERE clauses trying to agree with each other and
    with the outreach-state derivation below.

    Outreach state is derived, never guessed:
      drafted        -- a draft outreach exists, nothing proposed for it
      waiting on you -- that draft has a still-open `agency.send_outreach`
                        Tier 2 proposal (pending AND not yet expired -- an
                        expired proposal never executes, invariant 5, so it
                        cannot count as "waiting" either)
      sent / replied / bounced -- straight from `agency_outreach.status`
      null           -- no outreach has been drafted for this lead at all

    `status` matches either `agency_leads.status` or the derived outreach
    state above -- one flat set of chips over two underlying columns, since
    the owner filters "who is new" and "who is waiting on me" the same way.
    """
    limit = max(1, min(500, limit))
    offset = max(0, offset)

    rows = await fetch_all(_LEADS_JOIN_SQL + " ORDER BY l.id")
    read_at = now()
    all_leads = [_lead_json(r, read_at) for r in rows]
    counts = _status_counts(all_leads)
    why_empty = _agency_why_empty() if not all_leads else None

    matching = [lead for lead in all_leads
                if _matches_search(lead, q) and _matches_status(lead, status)]
    matching = _sort_leads(matching, sort, sort_dir)
    total = len(matching)
    page = matching[offset:offset + limit]

    return envelope(
        as_of=read_at, leads=page, total=total, limit=limit, offset=offset,
        counts=counts, why_empty=why_empty,
    )


def _parse_json_field(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value or json.dumps(default))
        except ValueError:
            return default
    return value if value is not None else default


def _lead_json(row: Any, read_at: str) -> dict[str, Any]:
    findings = _parse_json_field(row["audit_findings"], [])
    finding = None
    if findings:
        # The worst finding is the pitch: "your site takes 11 seconds on a
        # phone" beats a footnote about a missing favicon.
        worst = max(findings, key=lambda f: f.get("severity", 0))
        finding = worst.get("detail") or worst.get("check")

    outreach_status = row["outreach_status"]
    if outreach_status is None:
        # Nothing has been drafted for this lead yet -- not "drafted", which
        # would claim a state that does not exist.
        state = None
    elif outreach_status == "draft":
        state = "waiting on you" if row["proposal_id"] is not None else "drafted"
    else:
        state = outreach_status  # sent | replied | bounced, straight through

    next_step = None
    if state == "waiting on you":
        next_step = {
            "kind": "proposal", "card_id": row["proposal_id"],
            "label": row["proposal_reason"]
                or f"approve or deny the {row['outreach_kind']} outreach",
            "expires_at": row["proposal_expires_at"].isoformat()
                if row["proposal_expires_at"] else None,
        }
    elif state == "drafted":
        next_step = {
            "kind": "draft", "card_id": row["outreach_id"],
            "label": f"review the {row['outreach_kind']} draft before it is proposed",
            "expires_at": None,
        }

    score = row["score"]
    last_touch = row["outreach_at"] or row["audit_at"] or row["created_at"]

    return {
        "id": row["lead_id"],
        "business": row["name"],
        "category": row["category"],
        "source": "agency",  # the only pipeline that writes agency_leads today
        "address": row["address"],
        "contact": {
            "name": None,  # agency_leads has no contact-person column
            "email": row["outreach_to"],
            "phone": row["phone"],
            "site": row["website"],
        },
        "website": row["website"],
        # A score fresh off this very read carries this read's time; a lead
        # never scored carries no time at all -- never a guessed "just now".
        "score": score,
        "score_read_at": read_at if score is not None else None,
        "status": row["lead_status"],
        "audit_finding": finding,
        "audit_severity": row["audit_severity"],
        "audit_at": row["audit_at"].isoformat() if row["audit_at"] else None,
        "outreach_kind": row["outreach_kind"],
        "outreach_state": state,
        "outreach_at": row["outreach_at"].isoformat() if row["outreach_at"] else None,
        "last_touch_at": last_touch.isoformat() if last_touch else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "next_step": next_step,
    }


@router.get("/leads/{lead_id}")
async def lead_detail(_s: SignedIn, lead_id: int) -> dict:
    """The drawer behind one row of `/leads`: the full audit history, every
    outreach draft's actual text, and the slice of `actions_log` that
    mentions this lead -- by `lead_id` directly (`agency.audit`, `.mockup`,
    `.draft_outreach`, `.build_site`), by one of its outreach ids
    (`agency.send_outreach`, which only ever takes `outreach_id`), or by one
    of its site slugs (`agency.deploy_site`, which only takes `slug`) --
    `atlas/modules/agency/__init__.py`'s own catalog is what fixes those
    three param names, not a guess made here.
    """
    row = await fetch_one(_LEADS_JOIN_SQL + " WHERE l.id = $1", lead_id)
    if row is None:
        raise HTTPException(404, "no such lead")

    read_at = now()
    lead = _lead_json(row, read_at)
    # The score's reasons -- `agency_leads.features`, the same dict
    # `score.py::heuristic_score` weighed to produce the number above. Never
    # invented when there is no score: `_parse_json_field`'s default is `{}`,
    # but the drawer only shows it when `lead["score"]` is not null.
    score_features = _parse_json_field(row["lead_features"], {})

    audit_rows = await fetch_all(
        """SELECT id, at, url, findings, severity, measured FROM agency_audits
           WHERE lead_id = $1 ORDER BY at DESC, id DESC""", lead_id)
    outreach_rows = await fetch_all(
        """SELECT id, at, kind, to_address, subject, body, status, sent_at,
                  followup_of
           FROM agency_outreach WHERE lead_id = $1 ORDER BY at DESC, id DESC""",
        lead_id)
    site_rows = await fetch_all(
        "SELECT slug FROM agency_sites WHERE lead_id = $1", lead_id)

    outreach_ids = {str(o["id"]) for o in outreach_rows}
    slugs = {s["slug"] for s in site_rows}

    log_rows = await fetch_all(
        """SELECT id, at, event, subject, payload FROM actions_log
           WHERE subject LIKE 'agency.%' ORDER BY at DESC LIMIT 500""")
    timeline: list[dict[str, Any]] = []
    for log_row in log_rows:
        payload = _as_dict(log_row["payload"])
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        mentions_lead = (
            str(params.get("lead_id")) == str(lead_id)
            or str(params.get("outreach_id")) in outreach_ids
            or params.get("slug") in slugs
        )
        if not mentions_lead:
            continue
        timeline.append({
            "id": log_row["id"],
            "at": log_row["at"].isoformat(),
            "event": log_row["event"],
            "action": log_row["subject"],
            "detail": str(data.get("detail") or data.get("reason")
                          or data.get("why") or ""),
        })
        if len(timeline) >= 50:
            break

    return envelope(
        as_of=read_at,
        lead=lead,
        score_features=score_features,
        audits=[{
            "id": a["id"],
            "at": a["at"].isoformat() if a["at"] else None,
            "url": a["url"],
            "severity": a["severity"],
            "findings": _parse_json_field(a["findings"], []),
            "measured": _parse_json_field(a["measured"], {}),
        } for a in audit_rows],
        outreach=[{
            "id": o["id"],
            "at": o["at"].isoformat() if o["at"] else None,
            "kind": o["kind"],
            "to": o["to_address"],
            "subject": o["subject"],
            "body": o["body"],
            "status": o["status"],
            "sent_at": o["sent_at"].isoformat() if o["sent_at"] else None,
            "followup_of": o["followup_of"],
        } for o in outreach_rows],
        timeline=timeline,
    )


@router.get("/brief")
async def brief_json(_s: SignedIn) -> dict:
    from ..kernel.brief import build
    built = await build()
    return envelope(
        brief={
            "at": built.at.isoformat(),
            "watch_only": built.watch_only,
            "sections": [{
                "title": s.title,
                "lines": s.lines,
                "unavailable": s.unavailable,
                "empty": s.empty,
            } for s in built.sections],
        })


# -------------------------------------------------------- mission control
#
# The seven screens behind the Command Center's sidebar. Every one of these is
# a GET, every one is read fresh on the request, and none of them may ever
# grow a way to change something: the dashboard's only write path is approve
# and deny, and it goes through the executor.


def _iso(value: Any) -> str | None:
    """A timestamp as text, or null. Never a default, never "unknown"."""
    return value.isoformat() if value is not None else None


# The diary's reach, in hours. The default is the brief's own 72; the home
# page's week asks for 168 so all seven days are read. Bounded both ways: an
# hour is the least that means anything, and a week is what one calendar
# read per request should cost.
AGENDA_HOURS_DEFAULT = 72
AGENDA_HOURS_MAX = 168


@router.get("/agenda")
async def agenda(
    _s: SignedIn,
    hours: Annotated[int, Query(ge=1, le=AGENDA_HOURS_MAX)] = AGENDA_HOURS_DEFAULT,
) -> dict:
    """Today's diary and everything due inside `hours` (72 by default).

    Shares `brief.agenda_items` with the morning brief, so the screen and the
    07:00 message cannot disagree about what is on. Each item keeps its own
    `when`, rather than a rendered phrase, because "in 42m" must be computed
    against the reader's clock at the moment they look.

    `unread` names each module whose own agenda could not be read this time:
    its items are missing, so a day showing none is not known to be free.
    """
    from ..kernel.brief import agenda_items

    moment = datetime.now(UTC)
    unread: list[str] = []
    try:
        items = await agenda_items(moment, timedelta(hours=hours), unread)
    except Exception as exc:                            # noqa: BLE001
        # A diary that could not be read is not an empty diary.
        log.warning("agenda could not be built: %s", exc)
        return envelope(items=[], unavailable=f"{type(exc).__name__}: {exc}",
                        horizon_hours=hours, unread=unread)

    end_of_day = moment.astimezone().replace(hour=23, minute=59, second=59)
    return envelope(
        items=[{
            "when": item.when.isoformat(),
            "title": item.title,
            "kind": item.kind,
            "detail": item.detail,
            "minutes": item.minutes,
            "source": item.source,
            # The same rule the brief uses to split "Today" from "Due inside
            # 72 hours": a deadline two days out is not today's diary.
            "today": (item.kind == "event"
                      and item.when.astimezone() <= end_of_day),
        } for item in items],
        unavailable=None,
        horizon_hours=hours,
        unread=unread,
    )


@router.get("/tasks")
async def tasks(_s: SignedIn) -> dict:
    """Everything with work still in it: decisions waiting on the owner, the
    nightshift jobs that have not finished, and research still queued."""
    # The link runs findings -> proposals, not the other way: a proposal has
    # no finding column. Same join /approvals uses, for the same reason.
    #
    # DISTINCT ON because that join is one-to-many: two findings pointing at
    # one proposal would otherwise list the same decision twice and count it
    # twice, which is the sort of inflated number a person stops trusting.
    # Newest finding wins, since it is the one that describes the situation
    # now. The list is re-sorted by expiry below — DISTINCT ON dictates the
    # leading ORDER BY key, so it cannot also be the display order.
    proposals = await fetch_all(
        """SELECT DISTINCT ON (p.id)
                  p.id, p.action, p.reason, p.tier, p.created_at, p.expires_at,
                  f.title
           FROM proposals p
           LEFT JOIN findings f ON f.proposal_id = p.id
           WHERE p.status = 'pending'
           ORDER BY p.id, f.at DESC""")
    proposals = sorted(proposals, key=lambda r: r["expires_at"])

    # Counted, not measured off the list above: the same figure /status calls
    # `waiting_on_you`, so the sidebar badge and this screen cannot disagree.
    pending_row = await fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE status = 'pending'")
    jobs = await fetch_all(
        """SELECT id, at, task, repo, branch, status, preview_url, finished_at
           FROM nightshift_jobs
           WHERE status IN ('queued', 'running')
           ORDER BY at DESC LIMIT 50""")
    research = await fetch_all(
        """SELECT id, at, question, status FROM research_reports
           WHERE status = 'queued' ORDER BY at DESC LIMIT 50""")

    return envelope(
        proposals=[{
            "id": r["id"], "action": r["action"], "reason": r["reason"],
            "tier": r["tier"], "created_at": _iso(r["created_at"]),
            "expires_at": _iso(r["expires_at"]), "finding": r["title"],
        } for r in proposals],
        jobs=[{
            "id": r["id"], "at": _iso(r["at"]), "task": r["task"],
            "repo": r["repo"], "branch": r["branch"], "status": r["status"],
            "preview_url": r["preview_url"],
            "finished_at": _iso(r["finished_at"]),
        } for r in jobs],
        research=[{
            "id": r["id"], "at": _iso(r["at"]), "question": r["question"],
            "status": r["status"],
        } for r in research],
        pending=int(pending_row["n"]) if pending_row else 0,
    )


@router.get("/conversations")
async def conversations(_s: SignedIn, limit: int = 60) -> dict:
    """Everything said to ATLAS or to the owner, from all three surfaces, newest
    first.

    `unanswered` counts only the two inbound message tables: a voice turn was
    answered in the same breath, so counting it as waiting would inflate a
    number the sidebar badge is supposed to make trustworthy.

    Only live platforms are read from either table: rows from a platform
    that has since been removed stay stored, but are not shown or counted.
    """
    from ..modules.messages import PLATFORM as DM_PLATFORM
    from ..modules.messaging import _SENDABLE as CHAT_PLATFORMS

    limit = max(1, min(200, limit))

    turns = await fetch_all(
        """SELECT id, at, transcript, answer, intent, agent
           FROM voice_turns ORDER BY at DESC LIMIT $1""", limit)
    chats = await fetch_all(
        """SELECT id, at, platform, chat_id, sender, text, replied_at, meta
           FROM messaging_inbound WHERE platform = ANY($2::text[])
           ORDER BY at DESC LIMIT $1""", limit, list(CHAT_PLATFORMS))
    dms = await fetch_all(
        """SELECT id, at, platform, thread_id, sender, text, replied_at, draft
           FROM messages_inbound WHERE platform = $2
           ORDER BY at DESC LIMIT $1""", limit, DM_PLATFORM)

    from ..modules.messaging import voice_label

    rows: list[dict[str, Any]] = []
    for r in turns:
        rows.append({
            "source": "voice", "id": r["id"], "at": _iso(r["at"]),
            "platform": "voice", "thread": None, "who": "the owner",
            "text": r["transcript"], "answer": r["answer"],
            "intent": r["intent"], "agent": r["agent"],
            "replied_at": None, "draft": None, "label": None,
        })
    for r in chats:
        rows.append({
            "source": "messaging", "id": r["id"], "at": _iso(r["at"]),
            "platform": r["platform"], "thread": r["chat_id"],
            "who": r["sender"], "text": r["text"], "answer": None,
            "intent": None, "agent": None,
            "replied_at": _iso(r["replied_at"]), "draft": None,
            # A voice note's text is a machine transcript, not typed words.
            "label": voice_label(r.get("meta")),
        })
    for r in dms:
        rows.append({
            "source": "messages", "id": r["id"], "at": _iso(r["at"]),
            "platform": r["platform"], "thread": r["thread_id"],
            "who": r["sender"], "text": r["text"], "answer": None,
            "intent": None, "agent": None,
            "replied_at": _iso(r["replied_at"]), "draft": r["draft"],
            "label": None,
        })

    rows.sort(key=lambda r: r["at"] or "", reverse=True)

    # Counted across the whole table rather than across the page above. The
    # sidebar badge is a promise about everything waiting, and "3 unanswered
    # among the 60 most recent" is a different number wearing the same label.
    open_counts = await fetch_all(
        """SELECT count(*) AS n FROM messaging_inbound
           WHERE replied_at IS NULL AND platform = ANY($1::text[])
           UNION ALL
           SELECT count(*) AS n FROM messages_inbound
           WHERE replied_at IS NULL AND platform = $2""",
        list(CHAT_PLATFORMS), DM_PLATFORM)
    unanswered = sum(int(r["n"]) for r in open_counts)

    return envelope(conversations=rows[:limit], unanswered=unanswered,
                    limit=limit)


@router.get("/knowledge")
async def knowledge(_s: SignedIn) -> dict:
    """What ATLAS has read and kept: the document vault, the reading library,
    finished research and the reports it turned into."""
    documents = await fetch_all(
        """SELECT id, path, title, kind, indexed_at, chunks
           FROM vault_documents ORDER BY indexed_at DESC LIMIT 200""")
    library = await fetch_all(
        """SELECT id, at, kind, title, source, notes
           FROM library_entries ORDER BY at DESC LIMIT 200""")
    research = await fetch_all(
        """SELECT id, at, question, status, report_id,
                  jsonb_array_length(sources) AS sources
           FROM research_reports
           WHERE status = 'done' ORDER BY at DESC LIMIT 100""")
    reports = await fetch_all(
        """SELECT id, at, kind, title, delivered_via FROM reports
           WHERE kind = 'research' ORDER BY at DESC LIMIT 100""")

    return envelope(
        documents=[{
            "id": r["id"], "path": r["path"], "title": r["title"],
            "kind": r["kind"], "indexed_at": _iso(r["indexed_at"]),
            "chunks": r["chunks"],
        } for r in documents],
        library=[{
            "id": r["id"], "at": _iso(r["at"]), "kind": r["kind"],
            "title": r["title"], "source": r["source"], "notes": r["notes"],
        } for r in library],
        research=[{
            "id": r["id"], "at": _iso(r["at"]), "question": r["question"],
            "status": r["status"], "report_id": r["report_id"],
            "sources": r["sources"],
        } for r in research],
        reports=[{
            "id": r["id"], "at": _iso(r["at"]), "kind": r["kind"],
            "title": r["title"], "delivered_via": r["delivered_via"],
        } for r in reports],
        counts={"documents": len(documents), "library": len(library),
                "research": len(research), "reports": len(reports)},
    )


# What the knowledge viewer may be handed, by extension. Deliberately no
# HTML and no SVG: a document served from the dashboard's own origin runs AS
# the dashboard, and agency mockups are generated HTML built partly from
# other people's websites. Everything else is refused, not guessed at.
_VIEWABLE = {
    ".pdf": "application/pdf",
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}


def _viewable_roots() -> list[Path]:
    """The only directories a vault row may point into: the vault itself
    (`VAULT_DIR`, default `data/vault`) and ATLAS's own `data/`."""
    from ..modules.vault.index import vault_dir
    return [vault_dir().resolve(), Path("data").resolve()]


@router.get("/knowledge/file/{doc_id}")
async def knowledge_file(doc_id: int, _s: SignedIn) -> FileResponse:
    """One indexed vault document, for the knowledge viewer. Mesh-only (the
    middleware in main.py) and session-authenticated.

    The row's path is resolved -- symlinks included -- and must land inside
    `_viewable_roots()`. A path outside them 404s exactly like an unknown
    id, so the response never says what exists elsewhere on the disk.
    """
    row = await fetch_one(
        "SELECT id, path, title FROM vault_documents WHERE id = $1", doc_id)
    if row is None:
        raise HTTPException(404, "no such document")
    # Filesystem calls off the event loop, as the vault module does.
    try:
        path = await asyncio.to_thread(Path(row["path"]).resolve, strict=True)
    except (OSError, RuntimeError):
        raise HTTPException(404, "no such document") from None
    roots = await asyncio.to_thread(_viewable_roots)
    if not any(path.is_relative_to(root) for root in roots):
        log.warning("refusing to serve vault row %s outside the vault", doc_id)
        raise HTTPException(404, "no such document")
    media_type = _VIEWABLE.get(path.suffix.lower())
    if media_type is None or not await asyncio.to_thread(path.is_file):
        raise HTTPException(404, "not a viewable document")
    return FileResponse(
        path, media_type=media_type,
        # Shown in the page, not saved; the filename is for "open in tab".
        content_disposition_type="inline", filename=path.name,
        headers={"X-Content-Type-Options": "nosniff",
                 "Cache-Control": "no-store"})


@router.get("/tools")
async def tools(_s: SignedIn) -> dict:
    """The closed catalog, exactly as the registry holds it, plus the skills
    the agents load when a task matches.

    A refused action still appears — that is the point of a screen listing
    what ATLAS can do. It appears as a denial with its reason, and with no
    summary, because a refused action never registered and inventing a
    description of it would be inventing a capability.

    `perplexity` (Task 42) is the research source's month: calls counted
    against the cap with the time the count was read, or `used: null` and
    the reason there is no count (no key, or the table unreadable) — never
    a zero it did not read.
    """
    from ..kernel import skills as skills_module
    from ..modules import _perplexity

    actions = []
    for name, decision in sorted(REGISTRY.decisions.items()):
        action = REGISTRY.actions.get(name)
        actions.append({
            "name": name,
            "module": name.split(".")[0] if "." in name else "",
            "tier": int(decision.tier),
            "allowed": decision.allowed,
            "reason": decision.reason,
            "summary": action.summary if action else "",
            "params": dict(action.params) if action else {},
            "reversible": (action.rollback is not None) if action else False,
        })

    try:
        perplexity = await _perplexity.budget_status()
    except Exception as exc:                            # noqa: BLE001
        perplexity = {"configured": _perplexity.configured(), "used": None,
                      "cap": _perplexity.cap(), "read_at": None,
                      "reason": f"could not read the budget: {type(exc).__name__}"}

    return envelope(
        actions=actions,
        count=len(actions),
        perplexity=perplexity,
        skills=[{
            "name": s.name,
            "triggers": list(s.triggers),
            "agents": list(s.agents),
            "priority": s.priority,
        } for s in skills_module.SKILLS],
    )


@router.get("/workflows")
async def workflows(_s: SignedIn) -> dict:
    """The two kinds of standing behaviour: what the owner said once and meant
    permanently, and the steps the scheduler runs on a clock — plus, under
    `graphs`, what those steps actually connect to: the nine workflows in
    `kernel.workflows.WORKFLOWS`, each read fresh against the real tables
    (Task 61). A graph's own read failing never fails this endpoint — see
    `workflows_module.resolve_all`'s per-source fail-soft.

    The hours come from the scheduler's own helpers rather than a second read
    of the environment, so the screen can never claim 99 where the scheduler
    actually runs at 23.
    """
    import os

    from ..kernel import rules, scheduler
    from ..kernel import workflows as workflows_module

    standing = await rules.active()

    try:
        interval = float(os.getenv("SIGNAL_INTERVAL_S", "900"))
    except ValueError:
        interval = 900.0

    schedule = [
        {"name": "Morning brief", "hour": scheduler._brief_hour(),
         "weekday": None, "interval_s": None, "env": "BRIEF_HOUR",
         "detail": "the day's diary, inbox and decisions"},
        {"name": "Weekly summary", "hour": scheduler._brief_hour(),
         "weekday": scheduler._weekly_day(), "interval_s": None,
         "env": "WEEKLY_DAY", "detail": "after that day's brief has gone"},
        {"name": "Daily review", "hour": scheduler._review_hour(),
         "weekday": None, "interval_s": None, "env": "REVIEW_HOUR",
         "detail": "what happened, and what is still open"},
        {"name": "Judgment tournament", "hour": scheduler._tournament_hour(),
         "weekday": None, "interval_s": None, "env": "TOURNAMENT_HOUR",
         "detail": "retrain every head and pick who serves"},
        {"name": "Signals", "hour": None, "weekday": None,
         "interval_s": int(interval), "env": "SIGNAL_INTERVAL_S",
         "detail": "ask every module what is worth saying"},
    ]

    graphs = await workflows_module.read_all()

    return envelope(
        rules=[{
            "id": r["id"], "at": _iso(r["at"]), "said": r["said"],
            "via": r["via"], "briefed_at": _iso(r["briefed_at"]),
        } for r in standing],
        schedule=schedule,
        graphs=graphs,
    )


# -------------------------------------------------------------- marketing

# The publish decisions a marketing experiment waits on. Tier ASK, every one:
# a post under the owner's name is his word each time, so "awaiting approval" is a
# pending proposal for one of these and nothing else.
MARKETING_PUBLISH_ACTIONS: tuple[str, ...] = (
    "youtube.upload", "youtube.set_visibility",
    "instagram.publish", "tiktok.upload_draft",
)
MEASURED_OUTCOMES = ("won", "lost", "flat")
# "28-day views" is two readings of YouTube's lifetime view counter, 28 days
# apart. The earlier one may be up to three days older than that — the check
# runs daily and can miss a day — and past that the span is not 28 days, so
# the figure is refused rather than quietly relabelled.
VIEWS_WINDOW = timedelta(days=28)
VIEWS_WINDOW_SLACK = timedelta(days=3)
PLAN_WINDOW = timedelta(days=7)


def _json_field(value: Any, default: Any) -> Any:
    """JSONB arrives as a string from asyncpg and as an object from a stub."""
    if isinstance(value, str):
        try:
            value = json.loads(value or "null")
        except ValueError:
            return default
    return default if value is None else value


def _delta_pct(observed: Any, baseline: Any) -> float | None:
    """The change against the baseline, or null. A zero or missing baseline
    has no percentage: "better than nothing" is not a finding."""
    if observed is None or baseline is None or float(baseline) == 0:
        return None
    return round((float(observed) - float(baseline)) / float(baseline) * 100, 1)


_STATS_SQL = """SELECT o.at, o.health, o.value, o.detail, o.evidence
                FROM observations o
                JOIN checks c ON c.id = o.check_id
                JOIN properties p ON p.id = c.property_id
                WHERE p.name = 'youtube' AND c.name = $1"""


async def _marketing_channel(channel: str, threshold: int) -> dict[str, Any]:
    """One channel's tile, from the stats check's own observations.

    The newest observation decides the state. If that read failed, the tile
    is UNKNOWN with the reason — the last good number is not carried forward
    under a new time, because a figure the check could not re-read is not
    today's figure.
    """
    tile: dict[str, Any] = {
        "channel": channel, "health": "unknown", "reason": None,
        "read_at": None, "attempted_at": None,
        "subscribers": None, "views_total": None,
        "views_28d": None, "views_28d_since": None, "views_28d_span_days": None,
        "views_28d_reason": None,
        "partner": None,
    }
    latest = await fetch_one(_STATS_SQL + " ORDER BY o.at DESC LIMIT 1",
                             f"{channel}_stats")
    if latest is None:
        why = REGISTRY.disabled.get("youtube")
        tile["reason"] = (f"no reading yet — youtube module disabled: {why}"
                          if why else
                          f"no reading yet: youtube.{channel}_stats has not run")
        return tile
    if str(latest["health"]).lower() != "ok":
        tile["attempted_at"] = _iso(latest["at"])
        tile["reason"] = latest["detail"] or "the last read failed and gave no reason"
        return tile

    stats = _json_field(latest["evidence"], {}).get("channel_stats") or {}
    subs = stats.get("subscribers")
    views = latest["value"] if latest["value"] is not None else stats.get("views")
    tile.update(health="ok", read_at=_iso(latest["at"]), subscribers=subs,
                views_total=views)

    since = latest["at"] - VIEWS_WINDOW
    if views is None:
        tile["views_28d_reason"] = "the latest read reported no view count"
    else:
        base = await fetch_one(
            _STATS_SQL + """ AND o.health = 'ok' AND o.value IS NOT NULL
                             AND o.at <= $2 ORDER BY o.at DESC LIMIT 1""",
            f"{channel}_stats", since)
        if base is None:
            tile["views_28d_reason"] = "fewer than 28 days of readings"
        elif base["at"] < since - VIEWS_WINDOW_SLACK:
            tile["views_28d_reason"] = (
                f"no reading near 28 days back (nearest is "
                f"{base['at']:%d %b}); not a 28-day span")
        else:
            # Derived, not read: two readings of the lifetime counter. The
            # span is whatever lay between them (28 to 31 days), and the
            # screen prints it rather than calling it "the last 28 days".
            tile["views_28d"] = float(views) - float(base["value"])
            tile["views_28d_since"] = _iso(base["at"])
            tile["views_28d_span_days"] = round(
                (latest["at"] - base["at"]).total_seconds() / 86400, 1)

    unmeasured = ("the watch-hours and Shorts-views paths are not measured, "
                  "so eligibility is not confirmed")
    if subs is None:
        tile["partner"] = {
            "threshold": threshold, "eligible_on_subscribers": None,
            "line": f"subscriber count hidden — {unmeasured}"}
    else:
        eligible = subs >= threshold
        tile["partner"] = {
            "threshold": threshold, "eligible_on_subscribers": eligible,
            "line": (f"{int(subs):,} of {threshold:,} subscribers — "
                     f"{'meets' if eligible else 'below'} the subscriber "
                     f"threshold; {unmeasured}")}
    return tile


async def _marketing_channels() -> list[dict[str, Any]]:
    from ..modules.youtube import CHANNELS, PARTNER_SUBSCRIBERS

    tiles = [await _marketing_channel(ch, PARTNER_SUBSCRIBERS) for ch in CHANNELS]
    for tile in tiles:
        tile.setdefault("platform", "YouTube")
    # the owner, 2026-09-27: "include Instagram channels and TikTok as well".
    tiles += [await _instagram_tile(a) for a in ("main", "stories")]
    tiles.append(await _tiktok_tile())
    return tiles


_OBS_SQL = """SELECT o.at, o.health, o.value, o.detail, o.evidence
                FROM observations o
                JOIN checks c ON c.id = o.check_id
                JOIN properties p ON p.id = c.property_id
                WHERE p.name = $1 AND c.name = $2
                ORDER BY o.at DESC LIMIT 1"""


def _social_tile(channel: str, platform: str) -> dict[str, Any]:
    return {"channel": channel, "platform": platform, "health": "unknown",
            "reason": None, "read_at": None, "attempted_at": None,
            "subscribers": None, "views_total": None, "views_28d": None,
            "views_28d_since": None, "views_28d_span_days": None,
            "views_28d_reason": None, "partner": None, "figures": []}


async def _instagram_tile(account: str) -> dict[str, Any]:
    """Followers from `instagram.<account>_token`, reach and profile views
    from `instagram.<account>_engagement` -- each figure with the time its
    own check read it, never carried forward from a failed read."""
    tile = _social_tile("FlyCheap" if account == "main" else "AI stories", "Instagram")
    token = await fetch_one(_OBS_SQL, "instagram", f"{account}_token")
    engage = await fetch_one(_OBS_SQL, "instagram", f"{account}_engagement")
    if token is None and engage is None:
        why = REGISTRY.disabled.get("instagram")
        tile["reason"] = (f"no reading yet — instagram module disabled: {why}"
                          if why else f"no reading yet: instagram.{account}_token "
                                      f"has not run")
        return tile
    newest = max((r for r in (token, engage) if r is not None), key=lambda r: r["at"])
    if token is not None and str(token["health"]).lower() == "ok":
        handle = str(token["detail"] or "").split()[0] if token["detail"] else ""
        if handle.startswith("@"):
            tile["channel"] = handle
        tile["figures"].append({"label": "followers", "value": token["value"],
                                "read_at": _iso(token["at"])})
    if engage is not None and str(engage["health"]).lower() == "ok":
        ev = _json_field(engage["evidence"], {})
        tile["figures"].append({"label": "reach / day", "value": engage["value"],
                                "read_at": _iso(engage["at"])})
        if isinstance(ev, dict) and ev.get("profile_views") is not None:
            tile["figures"].append({"label": "profile views / day",
                                    "value": ev["profile_views"],
                                    "read_at": _iso(engage["at"])})
    if tile["figures"]:
        tile.update(health="ok", read_at=_iso(newest["at"]))
    else:
        tile["attempted_at"] = _iso(newest["at"])
        tile["reason"] = newest["detail"] or "the last read failed and gave no reason"
    return tile


async def _tiktok_tile() -> dict[str, Any]:
    """The AI-stories TikTok, from what `content.reel_metrics` read of each
    posted reel through Postiz: posts measured and their views, latest
    reading per post, last 28 days."""
    tile = _social_tile("AI stories", "TikTok")
    rows = await fetch_all(
        """SELECT DISTINCT ON (post_ref) post_ref, at, views, likes
             FROM reel_metrics
            WHERE platform = 'tiktok' AND at > now() - interval '28 days'
            ORDER BY post_ref, at DESC""")
    if not rows:
        tile["reason"] = ("no TikTok post measured in the last 28 days — "
                          "posts go out through Postiz once a reel is approved")
        return tile
    views = [r["views"] for r in rows if r["views"] is not None]
    likes = [r["likes"] for r in rows if r["likes"] is not None]
    read_at = max(r["at"] for r in rows)
    tile.update(health="ok", read_at=_iso(read_at))
    tile["figures"] = [
        {"label": "posts measured, 28 days", "value": len(rows), "read_at": _iso(read_at)},
        {"label": "views, 28 days", "value": sum(views) if views else None,
         "read_at": _iso(read_at)},
        {"label": "likes, 28 days", "value": sum(likes) if likes else None,
         "read_at": _iso(read_at)},
    ]
    return tile


async def _marketing_plan(now_at: datetime) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, action, params, reason, created_at, expires_at
           FROM proposals
           WHERE status = 'pending' AND action = ANY($1::text[])
             AND created_at >= $2
           ORDER BY expires_at""",
        list(MARKETING_PUBLISH_ACTIONS), now_at - PLAN_WINDOW)
    out = []
    for r in rows:
        params = _json_field(r["params"], {})
        raw = params.get("experiment_id") if isinstance(params, dict) else None
        try:
            experiment_id = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            experiment_id = None
        out.append({
            "id": r["id"], "action": r["action"], "reason": r["reason"],
            "channel": params.get("channel") if isinstance(params, dict) else None,
            "experiment_id": experiment_id,
            "created_at": _iso(r["created_at"]),
            "expires_at": _iso(r["expires_at"]),
            # Expiry is enforced when a proposal is claimed, so a row can still
            # read `pending` after its time is up. Past it, an answer can no
            # longer do anything: silence expired it, and the screen says so.
            "expired": r["expires_at"] is not None and r["expires_at"] <= now_at,
        })
    return out


def _experiment_json(r: Any, now_at: datetime, awaiting: dict[int, int],
                     hooked: tuple[str, ...]) -> dict[str, Any]:
    outcome = r["outcome"]
    if outcome is not None:
        stage = "measured"
        # Anything that is not a comparison somebody made reads as unmeasured.
        state = outcome if outcome in MEASURED_OUTCOMES else "unmeasured"
    elif r["posted_ref"]:
        stage = "published"
        due = r["measure_at"]
        state = "overdue" if due is not None and due < now_at else "measuring"
    elif r["id"] in awaiting:
        stage = state = "awaiting"
    else:
        stage = state = "draft"

    # A closed verdict's baseline was read when it was measured; an open
    # experiment's baseline is the one read when it was filed.
    if r["baseline"] is None:
        baseline_at = None
    elif state in MEASURED_OUTCOMES:
        baseline_at = _iso(r["measured_at"])
    else:
        baseline_at = _iso(r["at"])

    can_close = r["platform"] in hooked
    return {
        "id": r["id"], "at": _iso(r["at"]), "business": r["business"],
        "platform": r["platform"], "hypothesis": r["hypothesis"],
        "variant": r["variant"], "draft_id": r["draft_id"],
        "posted_ref": r["posted_ref"], "posted_at": _iso(r["posted_at"]),
        "measure_at": _iso(r["measure_at"]), "metric": r["metric"],
        "baseline": r["baseline"], "baseline_read_at": baseline_at,
        "observed": r["observed"],
        "delta_pct": (_delta_pct(r["observed"], r["baseline"])
                      if state in MEASURED_OUTCOMES else None),
        "measured_at": _iso(r["measured_at"]),
        "outcome": outcome, "lesson": r["lesson"],
        "stage": stage, "state": state,
        "never_published": outcome is not None and not r["posted_ref"],
        "proposal_id": awaiting.get(r["id"]),
        "can_close": can_close,
        "cannot_close": None if can_close else (
            f"{r['platform']} has no experiment hook yet; only "
            f"{', '.join(hooked)} can close an experiment"),
    }


async def _marketing_experiments(now_at: datetime, awaiting: dict[int, int],
                                 hooked: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, at, business, platform, hypothesis, variant, draft_id,
                  posted_ref, posted_at, measure_at, metric, baseline,
                  observed, measured_at, outcome, lesson
           FROM marketing_experiments
           ORDER BY at DESC LIMIT 120""")
    return [_experiment_json(r, now_at, awaiting, hooked) for r in rows]


async def _marketing_playbook(namespace: str) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT id, at, content, meta FROM memories
           WHERE namespace = $1 ORDER BY at DESC LIMIT 60""", namespace)
    out = []
    for r in rows:
        meta = _json_field(r["meta"], {})
        out.append({
            "id": r["id"], "at": _iso(r["at"]), "content": r["content"],
            "outcome": meta.get("outcome"), "business": meta.get("business"),
            "platform": meta.get("platform"), "variant": meta.get("variant"),
            "metric": meta.get("metric"), "observed": meta.get("observed"),
            "baseline": meta.get("baseline"),
            "delta_pct": (_delta_pct(meta.get("observed"), meta.get("baseline"))
                          if meta.get("outcome") in MEASURED_OUTCOMES else None),
        })
    return out


def _evidence_facts(evidence: Any) -> list[dict[str, Any]]:
    """Each fact with the time it was read. A fact with no read time is not a
    measurement, so its number never reaches the screen."""
    facts = []
    for name, fact in (_json_field(evidence, {}) or {}).items():
        if name == "background_reading":
            continue    # Perplexity's reading, not a fact (in est_note instead)
        fact = fact if isinstance(fact, dict) else {"value": fact}
        dated = bool(fact.get("observed_at"))
        facts.append({
            "name": name,
            "value": fact.get("value") if dated else None,
            "observed_at": fact.get("observed_at") if dated else None,
            "detail": fact.get("detail"),
            "undated": not dated,
        })
    return facts


async def _marketing_ideas(money_out: str) -> list[dict[str, Any]]:
    # The Sunday plan files the same idea again every week. The newest row per
    # idea speaks for it — including when that newest row was dropped.
    rows = await fetch_all(
        """SELECT DISTINCT ON (business, idea)
                  id, at, business, idea, mechanism, evidence, requires,
                  est_note, score, status
           FROM monetization_ideas
           ORDER BY business, idea, at DESC""")
    live = [r for r in rows if r["status"] != "dropped"]
    live.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0.0)))
    return [{
        "id": r["id"], "at": _iso(r["at"]), "business": r["business"],
        "idea": r["idea"], "mechanism": r["mechanism"],
        "evidence": _evidence_facts(r["evidence"]),
        "requires": r["requires"], "est_note": r["est_note"],
        "score": r["score"], "status": r["status"],
        "money_out": (r["requires"] == money_out
                      or r["mechanism"] == "paid_acquisition"),
    } for r in live[:30]]


@router.get("/marketing")
async def marketing(_s: SignedIn) -> dict:
    """The marketing board: channels, experiments, playbook, ideas, plan.

    Every section is read from rows the collectors and the marketing loop
    already wrote — no platform call, no model call, no vector search — so
    the screen can refetch on every event without spending anything, and
    every figure on it carries the time it was actually read.

    Each section fails on its own. A query that raises leaves that section
    empty *and* named in `unavailable`, so the screen can say "could not
    read" rather than "no experiments", which would be a different claim.
    """
    from ..kernel.marketing.cycle import PLATFORMS_WITH_HOOKS, last_filing
    from ..kernel.marketing.experiments import PLATFORMS
    from ..kernel.marketing.monetize import MONEY_OUT
    from ..kernel.marketing.playbook import NAMESPACE

    moment = datetime.now(UTC)
    hooked = tuple(PLATFORMS_WITH_HOOKS)
    unhooked = [p for p in PLATFORMS if p not in hooked]
    unavailable: dict[str, str | None] = {
        "channels": None, "experiments": None, "playbook": None,
        "ideas": None, "plan": None,
    }

    async def section(name: str, read):
        try:
            return await read
        except Exception as exc:                        # noqa: BLE001
            log.warning("marketing %s could not be read: %s", name, exc)
            unavailable[name] = f"{type(exc).__name__}: {exc}"
            return []

    channels = await section("channels", _marketing_channels())
    plan = await section("plan", _marketing_plan(moment))
    awaiting = {p["experiment_id"]: p["id"] for p in plan
                if p["experiment_id"] is not None and not p["expired"]}
    experiments = await section(
        "experiments", _marketing_experiments(moment, awaiting, hooked))
    playbook = await section("playbook", _marketing_playbook(NAMESPACE))
    ideas = await section("ideas", _marketing_ideas(MONEY_OUT))

    filing = last_filing()
    return envelope(
        watch_only=watch_only(),
        hooked_platforms=list(hooked),
        unhooked_platforms=unhooked,
        hook_note=(
            f"Only {', '.join(hooked)} can close an experiment. "
            f"{' and '.join(unhooked)} have no experiment hook yet: their "
            f"publish actions carry no experiment id, so nothing starts the "
            f"48-hour clock and an experiment there could never be measured."
            if unhooked else None),
        channels=channels,
        experiments=experiments,
        playbook=playbook,
        ideas=ideas,
        plan=plan,
        filing={
            "filed": filing.filed, "refused": filing.refused,
            "reasons": list(filing.reasons), "at": _iso(filing.at),
            "note": (None if filing.at is not None else
                     "no weekly filing seen since the last restart"),
        },
        unavailable=unavailable,
    )


# WMO weather codes, as Open-Meteo documents them. Anything not listed reports
# its number: a word ATLAS made up is worse than a code the owner can look up.
WMO = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain",
    67: "Freezing rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Light showers", 81: "Showers",
    82: "Violent showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail",
    99: "Thunderstorm with hail",
}


# The bottom bar refreshes on every SSE event and every 30-second poll, and
# the weather does not change that fast. Keyed on the coordinates so moving
# ATLAS_LAT/ATLAS_LON re-reads immediately instead of serving the old place.
WEATHER_TTL_S = 600.0
_weather_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


@router.get("/ambient")
async def ambient(_s: SignedIn) -> dict:
    """The bottom bar: the server's clock, and the weather where the owner is.

    Without `ATLAS_LAT`/`ATLAS_LON` there is nothing to ask, so `weather` is
    null and the reason says why. A temperature is a number, and invariant 6
    is that a number is read or it is not shown — there is no plausible
    default for how warm it is outside.
    """
    import os

    from ..modules import _http

    moment = datetime.now().astimezone()
    label = os.getenv("ATLAS_LOCATION_LABEL") or None
    lat = os.getenv("ATLAS_LAT")
    lon = os.getenv("ATLAS_LON")

    base = {
        "clock": moment.isoformat(),
        "timezone": moment.tzname(),
        "location": label,
    }

    if not lat or not lon:
        return envelope(**base, weather=None, cached=False,
                        reason="ATLAS_LAT/ATLAS_LON unset")

    cached = _weather_cache.get((lat, lon))
    if cached is not None and (time.monotonic() - cached[0]) < WEATHER_TTL_S:
        # The reading keeps Open-Meteo's own `observed_at`, so a cached answer
        # still carries the time the measurement was taken rather than the
        # time it was served. `cached` says which of the two this is.
        return envelope(**base, reason=None, cached=True, weather=cached[1])

    data, why = await _http.get_json(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon,
                "current": "temperature_2m,weather_code"},
        timeout=8.0)
    if why is not None:
        return envelope(**base, weather=None, cached=False, reason=why)

    current = (data or {}).get("current")
    if not isinstance(current, dict):
        return envelope(**base, weather=None, cached=False,
                        reason="Open-Meteo returned no 'current' block")

    temperature = current.get("temperature_2m")
    code = current.get("weather_code")
    if temperature is None:
        return envelope(**base, weather=None, cached=False,
                        reason="Open-Meteo returned no temperature")

    reading = {
        "temperature_c": temperature,
        "code": code,
        "description": WMO.get(code, f"WMO code {code}"),
        # Open-Meteo's own stamp for the reading, not the time we asked.
        "observed_at": current.get("time"),
    }
    # Only a successful read is cached. Holding on to a failure for ten
    # minutes would keep the bar wrong long after the network came back, and
    # a stale error is worse than an extra request.
    _weather_cache[(lat, lon)] = (time.monotonic(), reading)
    return envelope(**base, reason=None, cached=False, weather=reading)


# -------------------------------------------------------------------- SSE

@router.get("/stream")
async def stream(request: Request, _s: SignedIn) -> StreamingResponse:
    """Live updates.

    A heartbeat every 15 seconds lets the client tell "nothing has happened"
    apart from "the stream died" — a dashboard that silently stops updating is
    worse than one that admits it is stale.
    """
    queue = events.subscribe()

    async def generate():
        try:
            yield f"event: hello\ndata: {json.dumps({'at': now()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield f"event: heartbeat\ndata: {json.dumps({'at': now()})}\n\n"
                    continue
                payload = json.dumps({"at": event.at.isoformat(), **event.data},
                                     default=str)
                yield f"event: {event.kind}\ndata: {payload}\n\n"
        finally:
            events.unsubscribe(queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# --- Command Center read models (the owner 2026-10-29) ---------------------------
# Reads only. A half that cannot be read says "unknown" and why, never an
# empty list that would render as "nothing in the pipeline" (invariant 7).

def _site_stage(site: dict[str, Any]) -> tuple[str | None, str | None]:
    if site.get("live_release"):
        return "live", None
    if site.get("deployed_at"):
        return "deployed", None
    return None, "no deploy recorded for this site yet"


@router.get("/pipeline")
async def pipeline(_s: SignedIn) -> dict:
    """One row per client site and per lead: client, kind, stage, next step."""
    from ..modules.agency import sites_view

    rows: list[dict[str, Any]] = []
    sites_block: dict[str, Any]
    try:
        view = await sites_view.overview()
    except Exception as exc:                            # noqa: BLE001
        sites_block = {"state": "unknown", "read_at": now(),
                       "reason": f"client sites could not be read: {type(exc).__name__}"}
    else:
        read_at = view.get("read_at") or now()
        sites_block = {"state": "ok", "read_at": read_at, "reason": None}
        for site in view.get("sites") or []:
            stage, why = _site_stage(site)
            health = site.get("health")
            next_step = None if health == "ok" else "check site health"
            rows.append({
                "client": site.get("slug"), "kind": "site", "stage": stage,
                "next_step": next_step,
                "health": health if health in ("ok", "degraded", "failing") else "unknown",
                "reason": why or (None if next_step else "no next step recorded"),
                "read_at": read_at,
            })

    leads_block: dict[str, Any]
    try:
        lead_rows = await fetch_all(_LEADS_JOIN_SQL + " ORDER BY l.id")
    except Exception as exc:                            # noqa: BLE001
        leads_block = {"state": "unknown", "read_at": now(),
                       "reason": f"leads could not be read: {type(exc).__name__}"}
    else:
        read_at = now()
        leads_block = {"state": "ok", "read_at": read_at, "reason": None}
        for r in lead_rows:
            lead = _lead_json(r, read_at)
            stage = lead["outreach_state"] or lead["status"]
            step = lead["next_step"]
            rows.append({
                "client": lead["business"], "kind": "lead", "stage": stage,
                "next_step": step["label"] if step else None,
                "reason": None if step else "no next step recorded",
                "read_at": read_at,
            })
    return envelope(rows=rows, sites=sites_block, leads=leads_block, read_at=now())


@router.get("/money")
async def money(_s: SignedIn) -> dict:
    """What the agency spends, read now. Revenue has no source yet, so it is
    UNKNOWN, never a zero."""
    from ..kernel import quota
    from ..modules import _perplexity
    from ..modules.agency import prospect

    free_quota = {"pools": quota.snapshot(), "read_at": now()}
    try:
        perplexity = dict(await _perplexity.budget_status())
        perplexity.setdefault("read_at", now())
        perplexity["read_at"] = perplexity["read_at"] or now()
    except Exception as exc:                            # noqa: BLE001
        perplexity = {"used": None, "cap": None, "state": "unknown", "read_at": now(),
                      "reason": f"could not read the budget: {type(exc).__name__}"}
    month = datetime.now(UTC).strftime("%Y-%m")
    try:
        used = await prospect.spend_this_month()
    except Exception as exc:                            # noqa: BLE001
        places = {"used": None, "cap": prospect.cap(), "month": month,
                  "state": "unknown", "read_at": now(),
                  "reason": f"could not read Places spend: {type(exc).__name__}"}
    else:
        places = {"used": used, "cap": prospect.cap(), "month": month,
                  "state": "ok", "read_at": now(), "reason": None}
    return envelope(
        costs={"free_quota": free_quota, "perplexity": perplexity, "places": places},
        revenue={"value": None, "state": "unknown",
                 "reason": "no revenue source configured", "read_at": now()},
        read_at=now(),
    )
