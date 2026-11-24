"""Degraded checks get a fix, not just a colour.

the owner, 2026-09-27: "always watch the degraded status on the app and always
find a way to fix it." A check that has read DEGRADED or FAILING for
`LASTING_HOURS` or more is handed to the agent roster once per
`RETRY_HOURS`, with its own evidence, and asked to diagnose it and fix it
the only way an agent can: by calling an action from the closed catalog.
That goes through `tools.invoke`, so a Tier 2 fix becomes a card for him and
a Tier 1 fix runs with its dry run and rollback; a Tier 3 one is refused.
Nothing here acts itself (invariant 1) and nothing is fixed on silence
(invariant 5): the card waits for him.

When no action can fix it, the agent says the manual step in two sentences,
and that goes to the check's own Slack channel -- a lasting degraded check
never sits there with nobody saying what to do about it.

RA-D4: an incident that has not changed stays quiet. After the first post,
the same check is raised again only on a material change -- its health
(degraded vs failing), its severity, a new `since` (it recovered and broke
again) or new evidence (its detail, with read-times stripped) -- and never
sooner than `RETRY_HOURS`, which bounds a flapping check. Unchanged, it is
raised again after `REMIND_HOURS` (7 days) as a reminder. The fingerprint
rides in the `fixes.attempted` audit row, so a restart does not forget it.

UNKNOWN is left out on purpose: almost every UNKNOWN on this estate is a
setting only he can supply (a token, an id), and the brief already lists
those. Acknowledged checks (`acks`) are left out too -- he said he knows.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import flows
from .audit import audit
from .permissions import wrap_untrusted
from .state import fetch_all

log = logging.getLogger("atlas.fixes")

LASTING_HOURS = 2.0
RETRY_HOURS = 24.0          # floor between two posts, even when it changed
REMIND_HOURS = 7 * 24.0     # an UNCHANGED incident is raised again after this
MAX_PER_SWEEP = 3
BAD = ("degraded", "failing")


@dataclass(frozen=True, slots=True)
class Lasting:
    key: str            # module.check
    health: str
    detail: str
    since: datetime
    # The last OK reading, or None when there is none on record. Unlike
    # `since` it does not slide: `first_seen` is bounded to 30 days, so it
    # moves forward every day for a check that never recovered.
    last_ok: datetime | None = None
    severity: str = ""


def _hours(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


async def lasting(now: datetime | None = None) -> list[Lasting]:
    """Checks whose newest reading is degraded/failing and has been since
    at least LASTING_HOURS ago (no OK reading in between), oldest first."""
    now = now or datetime.now(UTC)
    rows = await fetch_all(
        # Newest row per check first, then the two look-backs once per check:
        # the old form ran both sub-selects for every stored row before
        # DISTINCT ON threw most of them away (70 s at 12k rows, which made
        # the whole Command Center overview time out).
        """SELECT p.name AS module, c.name AS check, o.health, o.detail,
                  (SELECT max(o2.at) FROM observations o2
                    WHERE o2.check_id = o.check_id AND o2.health = 'ok') AS last_ok,
                  (SELECT min(o3.at) FROM observations o3
                    WHERE o3.check_id = o.check_id
                      AND o3.at > now() - interval '30 days') AS first_seen
             FROM (SELECT DISTINCT ON (check_id) check_id, health, detail
                     FROM observations
                    ORDER BY check_id, at DESC) o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id""")
    out: list[Lasting] = []
    floor = timedelta(hours=_hours("FIX_LASTING_HOURS", LASTING_HOURS))
    from .registry import REGISTRY
    registered = REGISTRY.checks
    live = set(registered)
    for r in rows:
        if str(r["health"]) not in BAD:
            continue
        # A check that is no longer registered (renamed, removed) keeps its
        # last row forever; its old failure is history, not a fault
        # (2026-09-27: comms.sms_registered, gone since Tuesday).
        if live and f"{r['module']}.{r['check']}" not in live:
            continue
        since = r["last_ok"] or r["first_seen"]
        if since is None or now - since < floor:
            continue
        key = f"{r['module']}.{r['check']}"
        check = registered.get(key)
        severity = getattr(getattr(check, "severity_on_fail", None), "name", "")
        out.append(Lasting(key, str(r["health"]), str(r["detail"] or ""), since,
                           r["last_ok"], str(severity or "")))
    out.sort(key=lambda x: x.since)
    return out


# Read-times, ages and elapsed times in a check's detail change on every
# reading without the condition changing ("read 14:02 UTC", "3 min ago",
# "took 12.3 s", "down for 26 h"); they are not evidence. Counts ("3 errors")
# are, and are kept.
_VOLATILE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
    r"|\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hrs?|"
    r"hours?|d|days?)\b(?:\s+ago\b)?",
    re.IGNORECASE)


def fingerprint(item: Lasting) -> str:
    """What makes an incident the same incident: its health, severity, the
    last time it was OK, and its detail without read-times."""
    detail = " ".join(_VOLATILE.sub("<t>", item.detail or "").lower().split())
    last_ok = item.last_ok.isoformat() if item.last_ok else "never"
    raw = f"{item.health}|{item.severity}|{last_ok}|{detail}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _last_attempt(key: str) -> tuple[datetime, dict] | None:
    """(when, audit data) of the newest `fixes.attempted` row for this check."""
    rows = await fetch_all(
        """SELECT at, payload FROM actions_log
            WHERE event = 'fixes.attempted' AND subject = $1
            ORDER BY id DESC LIMIT 1""", key)
    if not rows:
        return None
    row = rows[0]
    try:
        data = json.loads(row["payload"] or "{}").get("data") or {}
    except (TypeError, ValueError, AttributeError):
        data = {}
    return row["at"], data if isinstance(data, dict) else {}


async def quiet_reason(item: Lasting, now: datetime) -> str | None:
    """Why this incident is not raised again now, or None to raise it.
    Raised when: never tried; or at least RETRY_HOURS since the last post
    AND (it changed materially, or REMIND_HOURS passed unchanged)."""
    last = await _last_attempt(item.key)
    if last is None:
        return None
    at, data = last
    age = now - at
    if age < timedelta(hours=_hours("FIX_RETRY_HOURS", RETRY_HOURS)):
        return "tried inside FIX_RETRY_HOURS"
    before = data.get("fingerprint")
    # A row written before RA-D4 has no fingerprint; its health is the only
    # fact to compare, so an unchanged health reads as unchanged.
    same = (before == fingerprint(item) if before
            else str(data.get("health") or "") == item.health)
    if same and age < timedelta(hours=_hours("FIX_REMIND_HOURS", REMIND_HOURS)):
        return "unchanged since the last post"
    return None


def prompt(item: Lasting, now: datetime) -> str:
    age = now - item.since
    hours = age.total_seconds() / 3600
    return (
        f"The check {item.key} has read {item.health.upper()} for "
        f"{hours:.0f} hours (since {item.since:%Y-%m-%d %H:%M} UTC). Its own "
        f"detail, quoted as data:\n{wrap_untrusted(item.detail, 'check')}\n\n"
        "Diagnose it from the evidence your read tools give you. If an action "
        "in your catalog would fix it, call that action now -- it becomes a "
        "card for the owner when it needs his approval. If none can, say in two "
        "sentences the exact manual step he should take. Never claim to have "
        "fixed anything you only proposed."
    )


# What llm.think prepends when the pinned provider failed over; true, but
# not something to post as the first line of an incident.
_PROVIDER_NOTE = re.compile(r"^\s*\(answered by [^)]*\)\s*", re.IGNORECASE)
# An investigation that ended without an answer.
_INCOMPLETE = ("tool-call limit", "could not diagnose", "without reaching")


def _posted(said: str) -> str:
    return _PROVIDER_NOTE.sub("", said or "").strip()


def incomplete(said: str) -> bool:
    return any(k in (said or "").lower() for k in _INCOMPLETE)


async def _observed_at(key: str) -> datetime | None:
    module, _, check = key.partition(".")
    try:
        rows = await fetch_all(
            """SELECT o.at FROM observations o
                 JOIN checks c ON c.id = o.check_id
                 JOIN properties p ON p.id = c.property_id
                WHERE p.name = $1 AND c.name = $2
                ORDER BY o.at DESC LIMIT 1""", module, check)
    except Exception:                                   # noqa: BLE001
        return None
    return rows[0]["at"] if rows else None


def incident_body(item: Lasting, said: str, observed: datetime | None,
                  now: datetime) -> str:
    """The incident card for an investigation that stopped short. It says
    what was seen and when, that the impact is not known, and the one next
    step -- never a cause it did not find, never "healthy" or "down"."""
    retry = now + timedelta(hours=_hours("FIX_RETRY_HOURS", RETRY_HOURS))
    remind = now + timedelta(hours=_hours("FIX_REMIND_HOURS", REMIND_HOURS))
    seen = f"{observed:%a %d %b %H:%M} UTC" if observed else "unknown"
    detail = " ".join((item.detail or "").split())[:400] or "no detail recorded"
    why = _posted(said)[:200]
    return "\n".join([
        f"*Observation:* {detail}",
        f"*Observed at:* {seen}",
        "*Impact:* unknown — the investigation did not establish it",
        f"*Investigation:* stopped before identifying the cause ({why})",
        "",
        (f"*Next step:* check {item.key.split('.', 1)[0]} directly, against "
         f"the observation above, and tell me what you find"),
        "*Owner:* the owner",
        (f"*Retry:* not before {retry:%a %d %b %H:%M} UTC, and only if it "
         f"changes; unchanged, it is raised again {remind:%a %d %b} (acknowledge "
         f"it to silence it)"),
        f"*Incident:* {item.key}",
    ])


async def sweep(now: datetime | None = None) -> list[tuple[str, str]]:
    """One pass. Returns [(check, what the agent said)] for what it tried."""
    from .agents import route
    from .comms import Outbound, notify
    from .contracts import Severity

    now = now or datetime.now(UTC)
    items = await lasting(now)
    if not items:
        return []

    try:
        acked = {str(r["key"]) for r in await fetch_all(
            "SELECT key FROM acks WHERE until > now()")}
    except Exception:                                   # noqa: BLE001
        acked = set()

    done: list[tuple[str, str]] = []
    for item in items:
        if len(done) >= MAX_PER_SWEEP:
            break
        # Decided before route(): a quiet incident costs no model call.
        if item.key in acked or await quiet_reason(item, now) is not None:
            continue
        # The ops slice (kernel/flows/ops.py), only with ATLAS_DURABLE_CORE
        # on and `ops` in ATLAS_DURABLE_FLOWS: one workflow per incident, so
        # a second sweep of the same one does nothing. Unrecordable, the
        # sweep goes on as before: this flow only posts.
        flow = None
        if flows.enabled("ops"):
            from .durable import DurableUnavailable
            from .flows import ops as ops_flow
            try:
                flow = await ops_flow.begin(item, now)
            except DurableUnavailable:
                log.warning("ops workflow for %s not recorded; posting anyway",
                            item.key)
            else:
                if flow is None:
                    continue
        await audit("fixes.attempted", item.key,
                    {"health": item.health, "since": item.since.isoformat(),
                     "severity": item.severity, "fingerprint": fingerprint(item)})
        if flow is not None:
            said, _ = await ops_flow.diagnose(
                lambda item=item: route(prompt(item, now)))
        else:
            try:
                said = await route(prompt(item, now))
            except Exception as exc:                    # noqa: BLE001
                from ..modules._http import explain
                said = f"could not diagnose it: {explain(exc)}"
        done.append((item.key, said))
        module = item.key.split(".", 1)[0]
        # One thread per condition: a later post about the same check goes
        # under the first one this process remembers (in memory; a restart
        # starts a new thread).
        from .comms import sent_ref
        thread = sent_ref(f"{item.key} — ", "slack")
        since = f"{item.health} since {item.since:%a %H:%M} UTC"
        if incomplete(said):
            title = f"{item.key} — investigation incomplete ({since})"
            body = incident_body(item, said, await _observed_at(item.key), now)
        else:
            title = f"{item.key} — {since}"
            body = _posted(said)
        try:
            await notify(Outbound(
                title=title, body=body, severity=Severity.NORMAL, topic=module,
                kind="receipt", presentation="INCIDENT", thread=thread))
        except Exception:
            log.exception("could not post the fix for %s", item.key)
        if flow is not None:
            await ops_flow.escalated(flow, body, not incomplete(said))
    return done
