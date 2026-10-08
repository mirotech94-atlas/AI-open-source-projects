"""Shadow mode (reliability plan RA-G1, docs/shadow-mode.md).

"Run an initial 48-hour shadow period with external writes disabled,
comparing proposed actions to observed events" (plan, "Shadow, canary and
soak"). A shadowed module keeps detecting, proposing and deciding exactly as
before; nothing it would do leaves the building, and each would-be run is
written to the audit chain so it can be compared with what actually ran.

Storage is the kill switch's own table: a shadow is a `pauses` row with
scope `shadow:<module>` or `shadow:all`. So it is durable across restarts,
fail-closed (an unreadable table stops dispatch), never deleted (lifting
stamps `resumed_at`), and enforced where the pause already is:

* `executor.execute` -- every caller. `pause.active_for` returns the shadow
  row, the executor refuses before `execute.start` and audits
  `execute.paused` with scope `shadow:<module>`. `executor.py` is unchanged,
  and at that layer it sees no params, so that record has the action and
  reason but **no params hash** (docs/shadow-mode.md has the one-line
  executor change that would add it).
* `outbox.dispatch_once` -- before the fence, so no attempt is spent. The
  row becomes `shadowed` (terminal: lifting the shadow never replays stale
  intents) and `shadow.would_execute` is audited with the params hash.

Granularity is the module (or everything). Per-action shadowing is
deliberately not offered: `pause.active_for` sees only the module, so an
action-level entry would stop the outbox and not a direct executor call.

Set and lifted only from the server CLI (`python -m atlas.cli shadow|unshadow`).
The Slack `/atlas pause` pattern cannot name a `shadow:` scope, and nothing
in the catalog can set or lift one. Default: nothing is shadowed.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .pause import ALL, SHADOW, Pause, valid_scope

WOULD_EXECUTE = "shadow.would_execute"
# What the report reads from the audit chain: the would-have-run records
# (outbox layer, executor layer, and watch-only, which is a global shadow)
# and what actually happened.
REPORT_EVENTS = (WOULD_EXECUTE, "execute.paused", "execute.watch_only",
                 "execute.start", "execute.finish", "execute.error",
                 "execute.abandoned")


def module_of(action: str) -> str:
    """Actions are named `module.verb` (the executor's own split)."""
    return action.split(".", 1)[0]


def scope_of(target: str) -> str | None:
    """`shadow:<module>` / `shadow:all` for a CLI argument, or None when it
    names no loaded module."""
    s = valid_scope(target.removeprefix(SHADOW))
    return None if s is None else SHADOW + s


def params_hash(params: Any) -> str:
    from .durable import digest
    return digest(params if params is not None else {})


async def shadow(target: str, by: str, reason: str = "") -> str:
    from .audit import audit
    from .state import execute_sql
    scope = scope_of(target)
    if scope is None:
        return f"there is no module called {target!r}; nothing was shadowed"
    await execute_sql(
        """INSERT INTO pauses (scope, paused_by, reason) VALUES ($1, $2, $3)
           ON CONFLICT (scope) WHERE resumed_at IS NULL DO NOTHING""",
        scope, by, reason[:300])
    await audit("shadow.set", scope, {"by": by, "reason": reason[:300]})
    what = scope.removeprefix(SHADOW)
    return (f"{'Everything' if what == ALL else 'The ' + what + ' module'} is in "
            "shadow mode: what it would run is recorded, nothing is dispatched.")


async def unshadow(target: str, by: str) -> str:
    from .audit import audit
    from .state import fetch_one
    scope = scope_of(target)
    if scope is None:
        return f"there is no module called {target!r}; nothing was changed"
    row = await fetch_one(
        """UPDATE pauses SET resumed_at = now(), resumed_by = $2
            WHERE scope = $1 AND resumed_at IS NULL RETURNING scope""", scope, by)
    what = scope.removeprefix(SHADOW)
    if row is None:
        return f"{'everything' if what == ALL else what} was not in shadow mode"
    await audit("shadow.lifted", scope, {"by": by})
    return (f"Shadow lifted for "
            f"{'everything' if what == ALL else 'the ' + what + ' module'}. "
            "Anything paused or in watch-only stays so.")


async def covering(action: str) -> Pause | None:
    """The shadow row that covers `action` now, or None. None too when a
    real pause outranks it: then the executor's pause handling applies and
    the intent waits. Raises pause.PauseUnreadable: the caller must not
    dispatch."""
    from .pause import active_for
    stop = await active_for(module_of(action))
    return stop if stop is not None and stop.shadow else None


# ------------------------------------------------------------------ report

@dataclass(slots=True)
class Would:
    at: datetime
    action: str
    layer: str                 # outbox | executor | watch-only
    params_hash: str | None    # None where the layer never saw the params
    ref: str = ""


@dataclass(slots=True)
class Ran:
    started: int = 0
    finished_ok: int = 0
    finished_not_ok: int = 0
    errored: int = 0
    abandoned: int = 0
    params_hashes: set[str] = field(default_factory=set)
    starts: list[datetime] = field(default_factory=list)


@dataclass(slots=True)
class Window:
    scope: str
    start: datetime
    end: datetime | None       # None: still shadowed

    def covers(self, action: str, at: datetime) -> bool:
        target = self.scope.removeprefix(SHADOW)
        return (target in (ALL, module_of(action)) and self.start <= at
                and (self.end is None or at < self.end))


@dataclass(slots=True)
class Report:
    since: datetime
    read_at: datetime
    windows: list[Window]
    would: list[Would]
    ran: dict[str, Ran]
    # execute.start rows inside a shadow window: the plan's "unauthorized
    # effect", which blocks the rollout.
    unauthorized: list[tuple[datetime, str]]


def _data(payload: str) -> dict[str, Any]:
    try:
        return dict(json.loads(payload).get("data") or {})
    except (ValueError, AttributeError):
        return {}


def _at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value))


def build(since: datetime, audit_rows: list[Any], pause_rows: list[Any],
          read_at: datetime | None = None) -> Report:
    """Pure: the report from `actions_log` rows (id, at, event, subject,
    payload) and shadow `pauses` rows (scope, paused_at, resumed_at)."""
    windows = [Window(str(r["scope"]), _at(r["paused_at"]),
                      _at(r["resumed_at"]) if r["resumed_at"] else None)
               for r in pause_rows if str(r["scope"]).startswith(SHADOW)]
    would: list[Would] = []
    ran: dict[str, Ran] = defaultdict(Ran)
    for r in audit_rows:
        event, action, at = str(r["event"]), str(r["subject"]), _at(r["at"])
        data = _data(str(r["payload"]))
        if event == WOULD_EXECUTE:
            would.append(Would(at, action, str(data.get("layer") or "outbox"),
                               data.get("params_hash"),
                               f"outbox#{data['outbox']}" if "outbox" in data else ""))
        elif event == "execute.paused":
            if str(data.get("scope") or "").startswith(SHADOW):
                # No params hash unless the executor records one (the
                # proposed one-line change in docs/shadow-mode.md).
                would.append(Would(at, action, "executor",
                                   data.get("params_hash"), str(data.get("scope"))))
        elif event == "execute.watch_only":
            would.append(Would(at, action, "watch-only",
                               params_hash(data.get("params")), "ATLAS_WATCH_ONLY"))
        elif event == "execute.start":
            ran[action].started += 1
            ran[action].starts.append(at)
            ran[action].params_hashes.add(params_hash(data.get("params")))
        elif event == "execute.finish":
            if data.get("ok"):
                ran[action].finished_ok += 1
            else:
                ran[action].finished_not_ok += 1
        elif event == "execute.error":
            ran[action].errored += 1
        elif event == "execute.abandoned":
            ran[action].abandoned += 1
    unauthorized = sorted(
        (at, action) for action, got in ran.items() for at in got.starts
        if any(w.covers(action, at) for w in windows))
    return Report(since, read_at or datetime.now(UTC), windows, would, dict(ran),
                  unauthorized)


async def report(since: datetime) -> Report:
    """Read-only: the audit rows and shadow windows since `since`."""
    from .state import fetch_all
    audit_rows = await fetch_all(
        """SELECT id, at, event, subject, payload FROM actions_log
            WHERE at >= $1::timestamptz AND event = ANY($2::text[])
            ORDER BY id""", since, list(REPORT_EVENTS))
    pause_rows = await fetch_all(
        """SELECT scope, paused_at, resumed_at FROM pauses
            WHERE scope LIKE 'shadow:%'
              AND (resumed_at IS NULL OR resumed_at >= $1::timestamptz)
            ORDER BY paused_at""", since)
    return build(since, list(audit_rows), list(pause_rows))


def render(rep: Report) -> str:
    def ts(at: datetime) -> str:
        return f"{at.astimezone(UTC):%Y-%m-%d %H:%M}Z"

    out = [f"Shadow report since {ts(rep.since)} (read {ts(rep.read_at)})", ""]
    if rep.windows:
        out.append("Shadow windows:")
        out += [f"  {w.scope:<24} {ts(w.start)} -> "
                f"{ts(w.end) if w.end else 'still shadowed'}" for w in rep.windows]
    else:
        out.append("Shadow windows: none in this period")
    out.append("")
    by_action: dict[str, list[Would]] = defaultdict(list)
    for w in rep.would:
        by_action[w.action].append(w)
    actions = sorted(set(by_action) | set(rep.ran))
    if not actions:
        out.append("Nothing would have run and nothing ran.")
    else:
        out.append(f"{'action':<34} {'would run':>9} {'hashes':>6}   actually ran "
                   "(start / ok / not ok / error / abandoned)")
        for a in actions:
            ws, got = by_action.get(a, []), rep.ran.get(a, Ran())
            hashes = {w.params_hash for w in ws if w.params_hash}
            out.append(f"{a:<34} {len(ws):>9} {len(hashes):>6}   {got.started} / "
                       f"{got.finished_ok} / {got.finished_not_ok} / {got.errored}"
                       f" / {got.abandoned}")
    out.append("")
    if rep.unauthorized:
        out.append(f"UNAUTHORIZED EFFECTS: {len(rep.unauthorized)} run(s) started "
                   "inside a shadow window. This blocks the rollout.")
        out += [f"  {ts(at)}  {a}" for at, a in rep.unauthorized]
    else:
        out.append("Unauthorized effects: none (no run started inside a shadow window)")
    if rep.would:
        out += ["", "Would have run:"]
        for w in rep.would:
            h = (f"params sha256 {w.params_hash[:12]}" if w.params_hash
                 else "params not recorded at this layer")
            out.append(f"  {ts(w.at)}  {w.action:<34} {w.layer:<10} {h}"
                       f"{'  ' + w.ref if w.ref else ''}")
    return "\n".join(out)


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """`48h`, `2d`, `90m`, or an ISO timestamp (UTC when it has no zone)."""
    from datetime import timedelta
    now = now or datetime.now(UTC)
    t = text.strip().lower()
    units = {"m": "minutes", "h": "hours", "d": "days"}
    if t[:-1].isdigit() and t[-1:] in units:
        return now - timedelta(**{units[t[-1]]: int(t[:-1])})
    at = datetime.fromisoformat(text.strip())
    return at if at.tzinfo else at.replace(tzinfo=UTC)
