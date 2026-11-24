"""Proactive updates: the assistant tells the owner one short note, built by code.

the owner, 2026-11-19. Tier 1 action `assistant.update_owner`: one Slack DM to his
own id through `comms.notify`. It cannot say anything but what the code builds
from fixed fields; no model writes it and no model can queue one.

What is worth telling (each is a read of something ATLAS already holds):
  * a check that was OK and is now failing (`observations`);
  * a proposal that has waited over two hours (`proposals`);
  * the cameras online count dropping (the NVR's own per-camera state);
  * important unread mail: NOT built, there is no list of what "important"
    means (see the build notes); the kind is not defined, so it cannot fire.

How it stays calm:
  * at most 1 an hour and 3 a day (rolling), counted from rows actually sent;
  * nothing in `QUIET_HOURS`; the row waits and the next pass after it sends;
  * the same thing (same kind and ref) is stored once inside its window;
  * severity is NORMAL, never above it. A page by phone is still only the
    scheduler's CRITICAL path for checks that already page today; this module
    cannot raise severity and never calls.
  * no owner id (`SLACK_OWNER_ID`, else the one `SLACK_APPROVER_IDS` id), no
    send. Silence is never consent: a row that is not sent just waits and
    expires unsent after a day.

Words that came from outside (a check name) are defanged and length-capped.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Action, ActionResult, FailureClass, Severity, Tier

log = logging.getLogger("atlas.assistant.notices")

ACTION = "assistant.update_owner"
HOUR_CAP, DAY_CAP = 1, 3
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=15)
RETRY_WAIT = timedelta(minutes=5)
EXPIRES_AFTER = timedelta(hours=24)
PROPOSAL_WAIT = timedelta(hours=2)
CHECK_LOOKBACK = timedelta(minutes=30)
NAME_MAX = 60
_SAFE = re.compile(r"[^A-Za-z0-9 ._:/\-]")
_SPACES = re.compile(r"\s+")
_URLISH = re.compile(r"(?i)\b(?:https?|ftp)://\S*|<[^>]*>|\bwww\.\S*")

# kind -> dedupe window
WINDOWS: dict[str, timedelta] = {
    "check_failing": timedelta(hours=6),
    "proposal_waiting": timedelta(hours=12),
    "camera_drop": timedelta(hours=3),
    "test": timedelta(hours=1),
}


def _word(value: object) -> str:
    """Outside words: no links or markup, only plain characters, one line, capped."""
    if not isinstance(value, str):
        return "?"
    text = _SPACES.sub(" ", _URLISH.sub(" ", value))
    text = _SAFE.sub("", text).strip()[:NAME_MAX].strip()
    return text or "?"


def _num(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("a number field must be an int")
    return max(0, min(value, 100000))


def _when(value: object) -> str:
    """A time field as `HH:MM UTC on Nov 19` (invariant 6: every figure says when
    it was read). A missing or bad time is an error, never a made-up one."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise TypeError("a time field must be a datetime")
    return value.astimezone(UTC).strftime("%H:%M UTC on %b %d")


def compose(kind: str, **f: Any) -> str:
    """The whole text of a notice, from fixed templates and fixed fields."""
    if kind == "check_failing":
        return (f"Heads up: the check {_word(f.get('name'))} went from OK to failing "
                f"(seen at {_when(f.get('at'))}).")
    if kind == "proposal_waiting":
        n, h = _num(f.get("count")), _num(f.get("hours"))
        noun = "proposal is" if n == 1 else "proposals are"
        return (f"{n} {noun} waiting on your OK for over {h} hours "
                f"(counted at {_when(f.get('at'))}).")
    if kind == "camera_drop":
        b, a, t = _num(f.get("before")), _num(f.get("after")), _num(f.get("total"))
        return (f"Cameras online went from {b} (read {_when(f.get('before_at'))}) "
                f"to {a} of {t} (read {_when(f.get('at'))}).")
    if kind == "test":
        return "TEST notice from ATLAS: a delivery check. Nothing is wrong."
    raise ValueError(f"unknown notice kind {kind!r}")


def owner_id() -> str:
    """the owner's own Slack id: `SLACK_OWNER_ID`, else the one approver id; with
    several approvers and no explicit owner there is none (so nothing sends)."""
    explicit = os.getenv("SLACK_OWNER_ID", "").strip()
    if explicit:
        return explicit
    ids = [u.strip() for u in os.getenv("SLACK_APPROVER_IDS", "").split(",") if u.strip()]
    return ids[0] if len(ids) == 1 else ""


def _key(kind: str, ref: str) -> str:
    return hashlib.sha256(f"{kind}\n{ref}".encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


class Store:
    """SQL, one atomic statement per step (tests substitute a fake)."""

    async def insert(self, kind: str, ref: str, text: str, key: str, now: datetime,
                     window: timedelta) -> int | None:
        from ...kernel.state import fetch_one
        row = await fetch_one(
            """INSERT INTO assistant_notices (kind, ref, text, dedupe_key, status,
                                              created_at, updated_at)
               SELECT $1, $2, $3, $4, 'pending', $5, $5
               WHERE NOT EXISTS (SELECT 1 FROM assistant_notices
                                 WHERE dedupe_key = $4 AND created_at > $6)
               RETURNING id""", kind, ref, text, key, now, now - window)
        return int(row["id"]) if row else None

    async def peek(self, nid: int) -> dict[str, Any] | None:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT * FROM assistant_notices WHERE id = $1", int(nid))
        return dict(row) if row else None

    async def waiting(self, limit: int, now: datetime) -> list[int]:
        from ...kernel.state import fetch_all
        rows = await fetch_all(
            "SELECT id FROM assistant_notices WHERE ((status = 'pending' AND "
            "(attempts = 0 OR updated_at < $4)) OR "
            "(status = 'sending' AND updated_at < $2)) AND created_at > $3 "
            "ORDER BY id LIMIT $1", limit, now - STALE_AFTER, now - EXPIRES_AFTER,
            now - RETRY_WAIT)
        return [int(r["id"]) for r in rows or []]

    async def claim(self, nid: int, now: datetime) -> dict[str, Any] | None:
        from ...kernel.state import fetch_one
        row = await fetch_one(
            "UPDATE assistant_notices SET status = 'sending', attempts = attempts + 1, "
            "updated_at = $2 WHERE id = $1 AND created_at > $4 AND (status = 'pending' "
            "OR (status = 'sending' AND updated_at < $3)) RETURNING *",
            int(nid), now, now - STALE_AFTER, now - EXPIRES_AFTER)
        return dict(row) if row else None

    async def sent(self, nid: int, now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql("UPDATE assistant_notices SET status = 'sent', sent_at = $2, "
                          "error = NULL, updated_at = $2 WHERE id = $1", int(nid), now)

    async def failed(self, nid: int, reason: str, now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            "UPDATE assistant_notices SET error = $2, updated_at = $3, status = "
            "CASE WHEN attempts >= $4 THEN 'failed' ELSE 'pending' END WHERE id = $1",
            int(nid), reason[:300], now, MAX_ATTEMPTS)

    async def sent_since(self, since: datetime) -> int:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT count(*) AS n FROM assistant_notices "
                              "WHERE sent_at > $1", since)
        return int(row["n"]) if row else 0

    async def state_get(self, key: str) -> str | None:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT value FROM assistant_notice_state WHERE key = $1", key)
        return str(row["value"]) if row else None

    async def state_set(self, key: str, value: str, now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            "INSERT INTO assistant_notice_state (key, value, updated_at) VALUES ($1, $2, $3) "
            "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = $3", key, value, now)


_store: Any = Store()


def use_store(new: Any) -> Any:
    global _store
    old, _store = _store, new
    return old


# ------------------------------------------------------------------ queueing

async def queue(kind: str, ref: str = "", **fields: Any) -> int | None:
    """Store one notice (None: the same thing is already stored in its window,
    or the input was bad). Sending is a separate step through the executor."""
    try:
        text = compose(kind, **fields)
        ref = _word(ref) if ref else kind
        return await _store.insert(kind, ref, text, _key(kind, ref), _now(), WINDOWS[kind])
    except Exception as exc:                                # noqa: BLE001
        log.warning("notice not queued (%s)", type(exc).__name__)
        return None


async def _through_executor(nid: int) -> bool:
    """Kernel side, so this package never imports the executor (invariant 2)."""
    from ...kernel.assistant_notice import send_notice
    return await send_notice(nid)


async def drain(limit: int = 3) -> int:
    """Try waiting rows, oldest first. The gate holds all but the one that fits."""
    done = 0
    if await _budget_problem(_now()):
        return 0                     # nothing may go: no executor call, no audit row
    for nid in await _store.waiting(limit, _now()):
        if done and await _budget_problem(_now()):
            break                    # re-checked per notice: the first send may fill a cap
        if await _through_executor(nid):
            done += 1
        else:
            break                    # cap, quiet hours or no route hold the rest too
    return done


# ------------------------------------------------------------------ the action

def vet(params: dict[str, Any]) -> str | None:
    if set(params) != {"notice_id"}:
        return "only notice_id is accepted"
    nid = params["notice_id"]
    if not isinstance(nid, int) or isinstance(nid, bool) or nid < 1:
        return "notice_id must be a positive integer"
    return None


def _quiet() -> bool:
    from ...kernel.comms import _quiet_now
    return _quiet_now()


async def _budget_problem(now: datetime) -> str:
    """Why nothing may be sent now (owner unknown, quiet hours, a cap), or ''.
    Checked before the executor is called, so a held notice leaves no audit row."""
    if not owner_id():
        return "no single owner Slack id is known (SLACK_OWNER_ID)"
    if _quiet():
        return "quiet hours: it waits"
    if await _store.sent_since(now - timedelta(hours=1)) >= HOUR_CAP:
        return "the hourly cap (1) is reached; it waits"
    if await _store.sent_since(now - timedelta(hours=24)) >= DAY_CAP:
        return "the day cap (3) is reached; it waits"
    return ""


async def _gate(nid: int, now: datetime) -> tuple[dict[str, Any] | None, str]:
    row = await _store.peek(nid)
    if row is None:
        return None, "no such notice"
    if row["status"] not in ("pending", "sending") or row["kind"] not in WINDOWS:
        return None, f"notice is already {row['status']}"
    if now - row["created_at"] > EXPIRES_AFTER:
        return None, "notice expired unsent"
    if row.get("attempts") and now - row["updated_at"] < RETRY_WAIT:
        return None, "retry wait: it tries again in a few minutes"
    if why := await _budget_problem(now):
        return None, why
    return row, ""


async def run(notice_id: int = 0) -> ActionResult:
    from ...kernel import comms
    from ...kernel.comms import Outbound
    now = _now()
    nid = int(notice_id)
    row, why = await _gate(nid, now)
    if row is None:
        return ActionResult(False, f"refused: {why}", failure_class=FailureClass.VALIDATION)
    taken = await _store.claim(nid, now)
    if taken is None:
        return ActionResult(False, "refused: this attempt is already taken")
    try:
        used = await comms.notify(Outbound(
            title="ATLAS update", body=taken["text"], severity=Severity.NORMAL,
            topic="assistant", kind="alert", channel=owner_id(),
            incident_key=f"assistant.notice.{taken['kind']}"))
    except Exception as exc:                                # noqa: BLE001
        used, why = [], type(exc).__name__
    # Only the Slack DM is the owner: a Matrix room is not known to be his alone,
    # so a Matrix-only delivery is not counted as telling him.
    if "slack" not in used:
        await _store.failed(nid, f"not delivered: {used or why}", _now())
        return ActionResult(False, "the Slack DM did not go out; it waits")
    # It counts against the caps and is never retried (no second copy).
    await _store.sent(nid, _now())
    return ActionResult(True, f"told the owner ({taken['kind']}) #{nid}",
                        evidence={"kind": taken["kind"]}, rollback_token=str(nid))


async def dry(notice_id: int = 0) -> ActionResult:
    row, why = await _gate(int(notice_id), _now())
    if row is None:
        return ActionResult(False, f"would not send: {why}")
    return ActionResult(True, f"would DM the owner a [{row['kind']}] update on Slack",
                        evidence={"kind": row["kind"]})


async def rollback(token: str) -> ActionResult:
    return ActionResult(True, "a delivered message cannot be unsent; nothing further is sent",
                        evidence={"unsent": False})


def actions() -> tuple[Action, ...]:
    return (
        Action(name=ACTION, tier=Tier.AUTO,
               summary="Send the owner one short code-built update as a Slack DM "
                       "(max 1 an hour, 3 a day, never in quiet hours)",
               run=run, dry_run=dry, rollback=rollback, vet=vet,
               params={"notice_id": "the assistant_notices row id"},
               # The real cap (1 an hour, 3 a day) counts only notices that were
               # SENT (`_budget_problem`). The executor counts every attempt, so
               # this only bounds retries: a failed Slack DM must not use up
               # the hour (the owner, 2026-11-20).
               max_per_hour=6),
    )


# ------------------------------------------------------------------ the scans
# Reads only. Each one queues rows; sending stays the executor's.

async def _went_failing(since: datetime) -> list[dict[str, Any]]:
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT c.name, max(o.at) AS at FROM (
               SELECT check_id, at, health,
                      LAG(health) OVER (PARTITION BY check_id ORDER BY at) AS prev
               FROM observations WHERE at > $1) o
           JOIN checks c ON c.id = o.check_id
           WHERE o.at > $2 AND o.health = 'failing' AND o.prev = 'ok'
           GROUP BY c.name""",
        since - timedelta(hours=2), since)
    return [dict(r) for r in rows or []]


async def _proposals_waiting(older_than: datetime) -> int:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE status = 'pending' "
        "AND created_at < $1 AND expires_at > now()", older_than)
    return int(row["n"]) if row else 0


async def _camera_online() -> tuple[int, int | None]:
    """(total, online) from the NVR; online None when it cannot be read or any
    camera has no state (invariant 6: never a guessed count)."""
    from .tools import read_camera_status
    got = await read_camera_status()
    online = got.get("online")
    if got.get("error") or got.get("unknown") or not isinstance(online, int):
        return int(got.get("total") or 0), None
    return int(got.get("total") or 0), online


async def scan_checks() -> int:
    n = 0
    for row in await _went_failing(_now() - CHECK_LOOKBACK):
        if await queue("check_failing", ref=row["name"], name=row["name"],
                       at=row["at"]):
            n += 1
    return n


async def scan_proposals() -> int:
    count = await _proposals_waiting(_now() - PROPOSAL_WAIT)
    if count < 1:
        return 0
    day = _now().astimezone(UTC).strftime("%Y-%m-%d")
    return 1 if await queue("proposal_waiting", ref=day, count=count, at=_now(),
                            hours=int(PROPOSAL_WAIT.total_seconds() // 3600)) else 0


async def scan_cameras() -> int:
    total, online = await _camera_online()
    if online is None:
        return 0                     # unreadable: say nothing, keep the last good count
    now = _now()
    last = await _store.state_get("camera_online")
    await _store.state_set("camera_online", f"{online}|{now.isoformat()}", now)
    try:
        before_s, before_at_s = (last or "").split("|", 1)
        before, before_at = int(before_s), datetime.fromisoformat(before_at_s)
    except ValueError:
        return 0                     # no earlier reading to compare with
    if online >= before:
        return 0
    return 1 if await queue("camera_drop", ref=f"{before}-{online}", before=before,
                            after=online, total=total, before_at=before_at,
                            at=now) else 0


def enabled() -> bool:
    """Off unless the assistant is on; the executor still holds watch-only."""
    from . import enabled as assistant_enabled
    return assistant_enabled()


async def step() -> None:
    """One scheduler pass: scan, then try to send what is waiting."""
    if not enabled():
        return
    for scan in (scan_checks, scan_proposals, scan_cameras):
        try:
            await scan()
        except Exception as exc:                            # noqa: BLE001
            log.warning("notice scan %s failed (%s)", scan.__name__, type(exc).__name__)
    await drain()
