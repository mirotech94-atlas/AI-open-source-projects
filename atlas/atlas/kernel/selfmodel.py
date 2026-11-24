"""The self-model: what ATLAS can truthfully say about itself.

the owner asked for ATLAS to have "awareness and consciousness". What is built is
an honest self-model and nothing more: ATLAS is not conscious, has no
feelings, and this file never says otherwise.

What it knows, and where each fact comes from:

  * `started_at`, `uptime_s` — this process, since it was imported at boot.
  * `deploy_sha` — `ATLAS_DEPLOY_SHA` if the deploy set it, else
    `git rev-parse HEAD` run once and cached. The image is built without
    `.git`, so in production this is None unless the deploy sets
    `ATLAS_DEPLOY_SHA`.
  * `errors_since_deploy` — `actions_log` rows since `started_at` that record
    something going wrong: `execute.error` (an action that raised), any other
    `*.error` / `*.fail*` event, and an `execute.finish` whose result was not
    ok.
  * `did_since_deploy` — `execute.finish` rows since `started_at` whose
    result was ok: how many, and the newest one's subject (the action name)
    and time. None when the log cannot be read — never a zero.
  * `checks` — the latest observation of every enabled check, counted by
    health. An enabled check that has never been observed counts as unknown.
  * `last_brief_at`, `memories`, `lessons_open`, `proposals_waiting` — one
    query each. A proposal past `expires_at` is not waiting (invariant 5).
  * `local_model`, `local_model_tools` — the model PRIVATE work runs on
    (None when there is none) and whether it can call tools, as learned by
    this process from the first request that carried tools. None until
    then: not having asked is not a yes.
  * `private_cloud_allowed` — whether the owner has opted in, with
    `ATLAS_PRIVATE_ALLOW_CLOUD`, to PRIVATE work falling through to a cloud
    model when the local one fails. False unless he has.
  * `providers_present` — which cloud providers have a key configured, by
    name only, never the value: what `ATLAS_AGENT_MODEL_<NAME>` and a
    business's own model pin may actually name.
  * `presence` — `rhythm.presence()`. "Home" means a peer in
    `PRESENCE_PEERS` is connected to the mesh AND reporting a
    `connection_ip` inside `HOME_IPS` — his own network, not the mesh: a
    phone on mobile data is on the mesh but not home. None until both
    `PRESENCE_PEERS` and `HOME_IPS` are set. `spoken()` also lists each
    listed peer's current address, read straight off this, so he can copy
    one into `HOME_IPS` without having to ask the router.

Every figure is read fresh and read on its own. One that cannot be read is
None and is SAID as "unknown" (invariants 6 and 7): a self-report is the
easiest place in the system to invent something, because nothing outside
disagrees with it.

It reads tables and process state. It never acts and never imports the
executor.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from .freshness import row_health
from .llm import local_model, local_model_tools, private_cloud_allowed, providers_present
from .rhythm import presence
from .state import fetch_all, fetch_one

log = logging.getLogger("atlas.selfmodel")

STARTED_AT = datetime.now(UTC)
PRESENCE_TIMEOUT_S = 4.0
GIT_TIMEOUT_S = 3.0

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SHA: list[str | None] = []          # [] = not looked up yet; [None] = none


async def deploy_sha() -> str | None:
    """The commit this process is running, once, cached. None if unknown."""
    if _SHA:
        return _SHA[0]
    sha = os.getenv("ATLAS_DEPLOY_SHA", "").strip() or None
    if sha is None:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(_ROOT), "rev-parse", "--short", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), GIT_TIMEOUT_S)
            if proc.returncode == 0:
                sha = out.decode().strip() or None
        except Exception as exc:                        # noqa: BLE001
            log.info("selfmodel: no git to read the deploy from: %r", exc)
            if proc is not None and proc.returncode is None:
                # A git that hung is not left behind as an orphan.
                try:
                    proc.kill()
                    await proc.wait()
                except Exception as kill_exc:           # noqa: BLE001
                    log.warning("selfmodel: could not reap git: %r", kill_exc)
    _SHA.append(sha)
    return sha


async def _read(what: str, read: Callable[[], Awaitable[Any]]) -> Any:
    """One figure, or None. A failed read is never a zero."""
    try:
        return await read()
    except Exception as exc:                            # noqa: BLE001
        log.warning("selfmodel: could not read %s: %s", what, exc)
        return None


async def _count(sql: str, *args: Any) -> int | None:
    row = await fetch_one(sql, *args)
    if row is None or row["n"] is None:
        return None
    return int(row["n"])


async def _errors() -> int | None:
    return await _count(
        """SELECT count(*) AS n FROM actions_log
            WHERE at >= $1
              AND (event LIKE '%.error' OR event LIKE '%.fail%'
                   OR (event = 'execute.finish'
                       AND payload::jsonb -> 'data' ->> 'ok' = 'false'))""",
        STARTED_AT)


async def _did() -> dict | None:
    """What ATLAS actually did since this process started: ok finishes."""
    row = await fetch_one(
        """SELECT count(*) AS n, max(at) AS last_at,
                  (array_agg(subject ORDER BY at DESC))[1] AS last_subject
             FROM actions_log
            WHERE at >= $1 AND event = 'execute.finish'
              AND payload::jsonb -> 'data' ->> 'ok' = 'true'""",
        STARTED_AT)
    if row is None or row["n"] is None:
        return None
    last_at = row["last_at"]
    return {"count": int(row["n"]),
            "last_subject": row["last_subject"],
            "last_at": last_at.isoformat() if last_at is not None else None}


async def _checks() -> dict[str, int] | None:
    """Enabled checks counted by health AS IT STANDS NOW: a stale OK -- one
    older than three of its own intervals -- is counted unknown, exactly as
    the board and the spoken check count it (`kernel.freshness`). A raw
    latest health would say "fine" over a dead scheduler."""
    rows = await fetch_all(
        """SELECT o.health, o.at, c.interval_s
             FROM checks c
             LEFT JOIN LATERAL (
                  SELECT health, at FROM observations
                   WHERE check_id = c.id ORDER BY at DESC LIMIT 1) o ON true
            WHERE c.enabled""")
    now = datetime.now(UTC)
    out = {"ok": 0, "degraded": 0, "failing": 0, "unknown": 0}
    for row in rows:
        # No reading, an unnamed word or a stale one: unknown, never "ok".
        health = row_health(row["health"], row["at"], row["interval_s"], now)
        out[health if health in out else "unknown"] += 1
    return out


async def _last_brief() -> str | None:
    row = await fetch_one(
        "SELECT max(at) AS at FROM reports WHERE kind = 'brief'")
    at = row["at"] if row else None
    return at.isoformat() if at is not None else None


async def _presence() -> dict:
    try:
        return await asyncio.wait_for(presence(), PRESENCE_TIMEOUT_S)
    except Exception as exc:                            # noqa: BLE001
        log.warning("selfmodel: presence could not be read: %s", exc)
        return {"home": None, "peers_seen": [],
                "read_at": datetime.now(UTC).isoformat(),
                "reason": "presence could not be read"}


async def status() -> dict:
    """Everything ATLAS can truthfully say about itself right now."""
    now = datetime.now(UTC)
    last_brief = await _read("the last brief", _last_brief)
    return {
        "read_at": now.isoformat(),
        "started_at": STARTED_AT.isoformat(),
        "uptime_s": max(0, int((now - STARTED_AT).total_seconds())),
        "deploy_sha": await deploy_sha(),
        "errors_since_deploy": await _read("errors", _errors),
        "did_since_deploy": await _read("what was done", _did),
        "checks": await _read("checks", _checks),
        "last_brief_at": last_brief,
        "memories": await _read("memories", lambda: _count(
            "SELECT count(*) AS n FROM memories")),
        "lessons_open": await _read("lessons", lambda: _count(
            """SELECT count(*) AS n FROM lessons
                WHERE cleared_at IS NULL AND kind <> 'told'""")),
        "proposals_waiting": await _read("proposals", lambda: _count(
            """SELECT count(*) AS n FROM proposals
                WHERE status = 'pending' AND expires_at > now()""")),
        "local_model": local_model(),
        "local_model_tools": local_model_tools(),
        "private_cloud_allowed": private_cloud_allowed(),
        # Which cloud providers have a key configured, by name -- never the
        # value. Task 53's addendum: Cerebras, Mistral and Cohere joined
        # Anthropic, OpenAI, OpenRouter, Groq and Gemini as providers a
        # business agent's model pin can name.
        "providers_present": providers_present(),
        "presence": await _presence(),
    }


_NOTHING = ""        # read fine, and the ledger is empty


async def newest_lesson() -> str | None:
    """The newest lesson on the ledger; "" when there is none, None when the
    ledger could not be read. `told` rows are bookkeeping, not mistakes."""
    try:
        row = await fetch_one(
            """SELECT lesson FROM lessons WHERE kind <> 'told'
                ORDER BY last_at DESC LIMIT 1""")
    except Exception as exc:                            # noqa: BLE001
        log.warning("selfmodel: could not read the lessons ledger: %s", exc)
        return None
    if row is None:
        return _NOTHING
    return " ".join(str(row["lesson"]).split())


def _uptime(seconds: int | None) -> str:
    if seconds is None:
        return "Uptime unknown"
    minutes = seconds // 60
    if minutes < 1:
        return "Up under a minute"
    if minutes < 60:
        return f"Up {minutes} minute{'s' if minutes != 1 else ''}"
    # Spoken aloud, so words, not "3h 05m".
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        hrs = f"{hours} hour{'s' if hours != 1 else ''}"
        return f"Up {hrs} {minutes} minute{'s' if minutes != 1 else ''}"
    return f"Up {hours // 24} days"


def _errors_line(n: int | None) -> str:
    if n is None:
        return "errors since deploy unknown"
    return f"{n} error{'s' if n != 1 else ''} since deploy"


def _checks_line(checks: dict | None) -> str:
    if not checks:
        return "checks unknown"
    total = sum(int(v) for v in checks.values())
    fine = int(checks.get("ok", 0))
    line = f"{fine} of {total} checks fine"
    if checks.get("unknown"):
        line += f", {int(checks['unknown'])} unmeasured"
    return line


def _did_line(did: dict | None, memories: int | None) -> str:
    """"Did 3 things since deploy, last vault.reindex at 14:02; I hold 812
    memories." Each half that could not be read is said as unknown."""
    if did is None:
        done = "What I did since deploy: unknown"
    elif not did.get("count"):
        done = "Did nothing since deploy"
    else:
        n = int(did["count"])
        done = f"Did {n} thing{'s' if n != 1 else ''} since deploy"
        subject, at = did.get("last_subject"), did.get("last_at")
        if subject and at:
            # Local time, as the rhythm says it: this is spoken to him.
            hhmm = datetime.fromisoformat(at).astimezone().strftime("%H:%M")
            done += f", last {subject} at {hhmm}"
    if memories is None:
        held = "memories I hold: unknown"
    else:
        held = f"I hold {memories} memor{'ies' if memories != 1 else 'y'}"
    return f"{done}; {held}."


def _peer_ips_line(peers: dict | None) -> str:
    """Every listed peer's current `connection_ip`, so he can copy one
    straight into `HOME_IPS` from this line without asking the router."""
    if not peers:
        return ""
    parts = [f"{name} {info.get('connection_ip') or 'no address reported'}"
            for name, info in peers.items()]
    return " Peer IPs for HOME_IPS: " + ", ".join(parts) + "."


def _where(presence_: dict | None) -> str:
    """"Where you are: home (owner on 203.0.113.7)." / "...out (owner
    connected from 198.51.100.4)." / "...unknown (<reason>)." — always
    followed by every listed peer's current address, so `self_status` alone
    is enough to learn what to put in `HOME_IPS` (invariant 6: never guess
    the home address either)."""
    p = presence_ or {}
    home = p.get("home")
    ips = _peer_ips_line(p.get("peers"))
    if home is True:
        peer = p.get("home_peer") or {}
        name, ip = peer.get("name"), peer.get("ip")
        detail = f"{name} on {ip}" if name and ip else "a listed peer"
        return f"Where you are: home ({detail}).{ips}"
    if home is False:
        peer = p.get("away_peer")
        if peer and peer.get("name") and peer.get("ip"):
            return (f"Where you are: out ({peer['name']} connected from "
                    f"{peer['ip']}).{ips}")
        return f"Where you are: out.{ips}"
    reason = p.get("reason") or "unknown"
    return f"Where you are: unknown ({reason}).{ips}"


async def spoken() -> str:
    """Short sentences for "how are you", every number from `status()`.

    "I'm running. Up 46 minutes, 2 errors since deploy, 38 of 41 checks fine,
    3 unmeasured. Did 3 things since deploy, last vault.reindex at 14:02; I
    hold 812 memories. Where you are: home (owner on 203.0.113.7). Peer IPs
    for HOME_IPS: owner 203.0.113.7, atlas.example.com 198.51.100.1. Last thing
    I got wrong: <newest lesson>."
    """
    s = await status()
    lesson = await newest_lesson()
    if lesson is None:
        wrong = "Last thing I got wrong: unknown."
    elif lesson == _NOTHING:
        wrong = "Nothing on the lessons ledger."
    else:
        wrong = f"Last thing I got wrong: {lesson.rstrip('.')}."
    body = ", ".join((_uptime(s.get("uptime_s")),
                      _errors_line(s.get("errors_since_deploy")),
                      _checks_line(s.get("checks"))))
    did = _did_line(s.get("did_since_deploy"), s.get("memories"))
    return f"I'm running. {body}. {did} {_where(s.get('presence'))} {wrong}"
