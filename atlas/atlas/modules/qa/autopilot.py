"""QA autopilot: why is it down, fix it if a Tier 1 action can, say so if not.

One cycle (`qa.autopilot_cycle`, Tier 1) reads every check whose newest
observation is degraded / failing / unknown (stale counts as unknown) and
every agent whose last shift failed or stalled, and records a DIAGNOSIS for
each: the cause, its class, the exact fix and who can do it.

  * a Tier 1 registered action fixes it -> run through `executor.execute`
    (WATCH_ONLY and every gate unchanged), then re-read the check; only a
    re-read of OK counts as fixed, UNKNOWN never does (invariant 7);
  * a Tier 2 action fixes it -> ONE card, `dedup_key` per cause;
  * it needs a key / token only Miro has -> every such cause of the cycle goes
    into ONE task naming the exact variable per check;
  * anything else -> one remediation task for the agent that owns it.

No retry loops: the same cause twice in a row after a fix attempt, or
MAX_ATTEMPTS_PER_DAY attempts, escalates once and stops. The autopilot adds no
action that acts on money, DNS, deletion, credentials or itself: `forbidden()`
refuses such a name in the fix map, and a fix must be a registered Tier 1
action with a dry run and a rollback.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult, Tier

log = logging.getLogger("atlas.qa.autopilot")

MAX_ATTEMPTS_PER_DAY = 2
CREDENTIAL_REF = "credentials"
SOURCE = "qa.autopilot"
# The outcomes that count as "we tried a fix".
ATTEMPTED = ("fixed", "fix_failed")

# Check key -> (Tier 1 action, params) that cures it when stale or failing.
# Each action is re-verified at run time (registered, Tier 1, dry run and
# rollback). Only read-and-record actions belong here.
FIX_ACTIONS: dict[str, tuple[str, dict[str, Any]]] = {
    "research.scan_freshness": ("research.daily_scan", {}),
}

# Names the autopilot must never reach: Tier 3 areas (invariant 3) and itself.
_FORBIDDEN = re.compile(
    r"(pay|refund|transfer|invoice|charge|stripe|dns|delet|remove|purge|wipe|drop|"
    r"credential|revoke|token|secret|password|key|vault|self|modify_atlas|autopilot)",
    re.IGNORECASE)


def forbidden(action: str) -> bool:
    return bool(_FORBIDDEN.search(action or ""))


_ENV = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]*[A-Z0-9]\b")
_UNSET = re.compile(r"\b(unset|not set|missing|not configured|no key|absent)\b", re.IGNORECASE)
_TOKEN = re.compile(r"\b(401|403|expired|invalid[_ ]grant|revoked|unauthori[sz]ed|"
                    r"token)\b", re.IGNORECASE)
_DISK = re.compile(r"(no space|enospc|disk|% full|free space|volume full)", re.IGNORECASE)
_STALE = re.compile(r"\b(stale|older than|not read since|out of date)\b", re.IGNORECASE)
_RESOURCE_AGENT = re.compile(r"(reservedformiro|quota|rate limit|429)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str                 # check | agent
    subject: str              # module.check | agent:<name>
    health: str               # degraded | failing | unknown
    detail: str
    evidence: dict[str, Any]
    stale: bool
    module: str


@dataclass(frozen=True, slots=True)
class Diagnosis:
    subject: str
    kind: str
    cause_class: str          # missing_credential expired_token stale_data resource
    cause: str                # real_fault unused_module
    fix: str
    who: str                  # autopilot | miro | agent | nobody
    cause_key: str
    env_missing: tuple[str, ...] = ()
    fix_action: str | None = None
    fix_params: dict[str, Any] = field(default_factory=dict)
    owner: str = ""


@dataclass(frozen=True, slots=True)
class History:
    attempts_today: int
    last_outcome: str | None


@dataclass(slots=True)
class Report:
    at: datetime
    total: int = 0
    fixed: int = 0
    waiting_on_miro: int = 0
    held: int = 0
    tasks: int = 0


def _unused() -> set[str]:
    """Modules Miro declared he does not use (his word, never inferred)."""
    return {m.strip() for m in os.getenv("AUTOPILOT_UNUSED_MODULES", "").split(",")
            if m.strip()}


def _envs(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_ENV.findall(text or "")))


def classify(f: Finding, disabled: str | None) -> Diagnosis:
    """Pure: the cause, its class, the fix and who does it. Text only from the
    module's own detail and the registry's reason, never a model."""
    text = f"{f.detail} {disabled or ''}"
    base = {"subject": f.subject, "kind": f.kind, "owner": f.module}

    def make(cls: str, cause: str, fix: str, who: str, **kw: Any) -> Diagnosis:
        return Diagnosis(cause_class=cls, cause=cause, fix=fix, who=who,
                         cause_key=f"{f.subject}:{cls}", **base, **kw)

    if f.kind == "agent":
        if _RESOURCE_AGENT.search(text):
            return make("resource", "the model quota was used up before its shift",
                        "wait for the quota reset or add model capacity", "agent")
        return make("real_fault", f"last shift did not finish: {f.detail[:160]}",
                    "read the shift's run row and fix what it names", "agent")
    if disabled and disabled.startswith("missing env:"):
        names = _envs(disabled.removeprefix("missing env:"))
        if f.module in _unused():
            return make("unused_module", f"{f.module} is off: you said you do not use it",
                        "none needed", "nobody", env_missing=names)
        return make("missing_credential", f"{f.module} is off, "
                    f"{', '.join(names)} is not set",
                    f"set {', '.join(names)} in the ATLAS environment", "miro",
                    env_missing=names)
    if _UNSET.search(f.detail) and (names := _envs(f.detail)):
        return make("missing_credential", f"{names[0]} is not set ({f.detail[:120]})",
                    f"set {', '.join(names)} in the ATLAS environment", "miro",
                    env_missing=names)
    if _TOKEN.search(f.detail):
        return make("expired_token", f"the service refused the login: {f.detail[:140]}",
                    "sign in again and give ATLAS a fresh token", "miro",
                    env_missing=_envs(f.detail))
    if _DISK.search(f.detail):
        return make("resource", f"disk or space problem: {f.detail[:140]}",
                    "free disk space (ATLAS never deletes by itself)", "miro")
    if f.stale or _STALE.search(f.detail):
        action = FIX_ACTIONS.get(f.subject)
        return make("stale_data", "the check has not been read recently",
                    f"run {action[0]}" if action else "re-run the collector",
                    "autopilot" if action else "agent",
                    fix_action=action[0] if action else None,
                    fix_params=dict(action[1]) if action else {})
    action = FIX_ACTIONS.get(f.subject)
    return make("real_fault", f"the check reads {f.health}: {f.detail[:160]}",
                f"run {action[0]}" if action else f"fix {f.subject}'s cause",
                "autopilot" if action else "agent",
                fix_action=action[0] if action else None,
                fix_params=dict(action[1]) if action else {})


class IO:
    """The side effects of a cycle, injected so the logic is testable."""

    async def findings(self) -> list[Finding]: raise NotImplementedError
    def disabled_reason(self, module: str) -> str | None: raise NotImplementedError
    async def history(self, cause_key: str) -> History: raise NotImplementedError
    def tier_of(self, action: str) -> Tier | None: raise NotImplementedError
    async def run_fix(self, action: str, params: dict[str, Any], reason: str
                      ) -> ActionResult: raise NotImplementedError
    async def reread(self, subject: str) -> str: raise NotImplementedError
    async def raise_task(self, owner: str, title: str, evidence: dict[str, Any],
                         fix: str, ref: str) -> int: raise NotImplementedError
    async def raise_card(self, action: str, params: dict[str, Any], reason: str,
                         dedup_key: str) -> int: raise NotImplementedError
    async def record(self, at: datetime, rows: list[dict[str, Any]], report: Report
                     ) -> int: raise NotImplementedError


def _held(res: ActionResult) -> bool:
    return res.dispatched is False and not res.ok and (
        "executor_hold" in res.evidence or res.failure_class is not None
        and res.failure_class.name in ("DISABLED", "POLICY"))


async def _escalate(d: Diagnosis, io: IO, why: str) -> str:
    await io.raise_task(d.owner or "qa", f"Autopilot gave up on {d.subject}: {d.cause}"[:200],
                        {"cause_key": d.cause_key, "why": why}, d.fix, d.cause_key)
    return "escalated"


async def _fix(d: Diagnosis, hist: History, io: IO) -> tuple[str, str]:
    """(outcome, detail) for a cause the autopilot may fix itself."""
    if hist.last_outcome == "escalated":
        return "escalated", "already escalated; not retrying"
    if hist.last_outcome in ATTEMPTED:
        return await _escalate(d, io, "same cause again after a fix attempt"), \
            "same cause again after a fix attempt"
    if hist.attempts_today >= MAX_ATTEMPTS_PER_DAY:
        return await _escalate(d, io, "daily attempt cap reached"), "attempt cap reached"
    action = d.fix_action or ""
    tier = io.tier_of(action)
    if forbidden(action) or tier is None:
        return await _escalate(d, io, f"{action or 'no action'} is not a registered "
                               "fix"), "no registered fix"
    if tier is Tier.ASK:
        await io.raise_card(action, d.fix_params, f"{d.subject}: {d.cause}",
                            f"autopilot:{d.cause_key}")
        return "waiting_miro", f"asked Miro to approve {action}"
    if tier is not Tier.AUTO:
        return await _escalate(d, io, f"{action} is not Tier 1"), "not tier 1"
    res = await io.run_fix(action, d.fix_params, f"autopilot: {d.cause}")
    if _held(res):
        return "held", res.detail[:200]
    if not res.ok:
        return "fix_failed", res.detail[:200]
    after = await io.reread(d.subject)
    if after == "ok":
        return "fixed", f"{action} ran and the check reads OK"
    return "fix_failed", f"{action} ran but the check reads {after}"


async def run_cycle(io: IO, now: datetime) -> Report:
    rep = Report(at=now)
    rows: list[dict[str, Any]] = []
    credentials: list[Diagnosis] = []
    for f in await io.findings():
        d = classify(f, io.disabled_reason(f.module))
        hist = await io.history(d.cause_key)
        detail = ""
        if d.who == "nobody":
            outcome = "idle"
        elif d.who == "miro":
            outcome = "waiting_miro"
            credentials.append(d)
        elif d.who == "autopilot":
            outcome, detail = await _fix(d, hist, io)
        elif hist.last_outcome in ("task_open", "escalated"):
            outcome = hist.last_outcome       # one task per cause, never a second
        else:
            await io.raise_task(d.owner or "qa", f"{d.subject}: {d.cause}"[:200],
                                {"cause_key": d.cause_key, "detail": f.detail[:300]},
                                d.fix, d.cause_key)
            outcome = "task_open"
        rows.append({"subject": d.subject, "kind": d.kind, "cause_key": d.cause_key,
                     "cause_class": d.cause_class, "cause": d.cause, "fix": d.fix,
                     "who": d.who, "outcome": outcome, "action": d.fix_action,
                     "detail": detail, "env_missing": list(d.env_missing)})
    if credentials:
        lines = [f"{d.subject}: {d.cause} -> {d.fix}" for d in credentials]
        await io.raise_task(
            "Miro", f"{len(credentials)} thing(s) need a key or token only you have",
            {"missing": {d.subject: list(d.env_missing) for d in credentials}},
            "; ".join(lines)[:1500], CREDENTIAL_REF)
    rep.total = len(rows)
    rep.fixed = sum(r["outcome"] == "fixed" for r in rows)
    rep.waiting_on_miro = sum(r["outcome"] == "waiting_miro" for r in rows)
    rep.held = sum(r["outcome"] == "held" for r in rows)
    rep.tasks = sum(r["outcome"] in ("task_open", "escalated") for r in rows) \
        + (1 if credentials else 0)
    await io.record(now, rows, rep)
    return rep


# ------------------------------------------------------------- production

class LiveIO(IO):
    """Reads ATLAS's own tables, runs fixes through the executor only."""

    def __init__(self) -> None:
        self.rolled: list[int] = []

    async def findings(self) -> list[Finding]:
        from ...kernel import agent_activity
        from ...kernel.freshness import effective_health
        from ...kernel.registry import REGISTRY
        now = datetime.now(UTC)
        out: list[Finding] = []
        for r in await agent_activity._read_checks():     
            module, name = str(r["property"]), str(r["check_name"])
            key = f"{module}.{name}"
            if module in REGISTRY.modules and key not in REGISTRY.checks:
                continue                        # a retired check
            interval = timedelta(seconds=int(r["interval_s"])) if r["interval_s"] else None
            health, stale = effective_health(r["health"], r["observed_at"], interval, now)
            if health in ("degraded", "failing", "unknown"):
                detail = await _detail_of(module, name)
                out.append(Finding("check", key, str(health), detail, {}, bool(stale),
                                   module))
        out.extend(await _agent_findings(now))
        return out

    def disabled_reason(self, module: str) -> str | None:
        from ...kernel.registry import REGISTRY
        return REGISTRY.disabled.get(module)

    async def history(self, cause_key: str) -> History:
        from ...kernel.state import fetch_all
        rows = await fetch_all(
            """SELECT outcome FROM autopilot_log
                WHERE cause_key = $1 AND at > now() - interval '24 hours'
                ORDER BY at DESC, id DESC""", cause_key)
        outcomes = [str(r["outcome"]) for r in rows]
        return History(sum(o in ATTEMPTED for o in outcomes),
                       outcomes[0] if outcomes else None)

    def tier_of(self, action: str) -> Tier | None:
        from ...kernel.registry import REGISTRY
        a, dec = REGISTRY.actions.get(action), REGISTRY.decisions.get(action)
        if a is None or dec is None or not dec.allowed:
            return None
        if dec.tier is Tier.AUTO and (a.dry_run is None or a.rollback is None):
            return None                         # invariant 8
        return dec.tier

    async def run_fix(self, action: str, params: dict[str, Any], reason: str
                      ) -> ActionResult:
        from ...kernel.executor import ExecutionRequest, execute
        return await execute(ExecutionRequest(action=action, params=params,
                                              reason=reason))

    async def reread(self, subject: str) -> str:
        """Run the check again and read its fresh answer. Not read = unknown."""
        from ...kernel.registry import REGISTRY
        check = REGISTRY.checks.get(subject)
        if check is None:
            return "unknown"
        try:
            obs = await check.run()
        except Exception as exc:  # noqa: BLE001
            log.warning("autopilot re-read of %s failed: %s", subject, exc)
            return "unknown"
        return str(getattr(obs.health, "value", obs.health)).lower()

    async def raise_task(self, owner: str, title: str, evidence: dict[str, Any],
                         fix: str, ref: str) -> int:
        from . import create_remediation
        res = await create_remediation(owner, title, evidence, fix, SOURCE, ref)
        return int(res.evidence.get("task_id", 0)) if res.ok else 0

    async def raise_card(self, action: str, params: dict[str, Any], reason: str,
                         dedup_key: str) -> int:
        from ...kernel import approvals
        return await approvals.propose(action, params, reason, dedup_key=dedup_key)

    async def record(self, at: datetime, rows: list[dict[str, Any]], report: Report
                     ) -> int:
        from ...kernel.state import execute_sql, fetch_one
        head = await fetch_one(
            """INSERT INTO autopilot_cycles (at, total, fixed, waiting, held, tasks)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            at, report.total, report.fixed, report.waiting_on_miro, report.held,
            report.tasks)
        cid = int(head["id"]) if head is not None else 0
        for r in rows:
            await execute_sql(
                """INSERT INTO autopilot_log (cycle_id, at, subject, cause_key,
                       cause_class, cause, fix, who, outcome, action, detail, env_missing)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)""",
                cid, at, r["subject"], r["cause_key"], r["cause_class"], r["cause"][:500],
                r["fix"][:500], r["who"], r["outcome"], r["action"], r["detail"][:500],
                json.dumps(r["env_missing"]))
        return cid


async def _detail_of(module: str, check: str) -> str:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT o.detail FROM observations o
             JOIN checks c ON c.id = o.check_id
             JOIN properties p ON p.id = c.property_id
            WHERE p.name = $1 AND c.name = $2 ORDER BY o.at DESC LIMIT 1""",
        module, check)
    return str(row["detail"] or "") if row is not None else ""


async def _agent_findings(now: datetime) -> list[Finding]:
    from ...kernel import agents
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT agent, at, outcome FROM agent_runs
            WHERE task LIKE $1 AND at > now() - interval '3 days'
            ORDER BY at""", agents.SHIFT_LIKE)
    by: dict[str, list[Any]] = {}
    for r in rows:
        by.setdefault(str(r["agent"]), []).append(r)
    out: list[Finding] = []
    for name in agents.ROSTER:
        runs = by.get(name, [])
        ok = [r["at"] for r in runs if agents.shift_succeeded(r["outcome"])]
        bad = [r["at"] for r in runs if not agents.shift_succeeded(r["outcome"])]
        st = agents.shift_state(name, max(ok) if ok else None, bad, now)
        last = runs[-1] if runs else None
        failed_last = last is not None and not agents.shift_succeeded(last["outcome"])
        if st.state in ("overdue", "failed") or failed_last:
            text = (last["outcome"] if last is not None else "") or "no shift recorded"
            out.append(Finding("agent", f"agent:{name}",
                               "failing" if failed_last else "degraded",
                               str(text)[:300], {"state": st.state}, False, name))
    return out


async def cycle() -> ActionResult:
    now = datetime.now(UTC)
    try:
        rep = await run_cycle(LiveIO(), now)
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return ActionResult(False, f"autopilot could not run: {explain(exc)}")
    return ActionResult(
        True, f"{rep.total} diagnosed, {rep.fixed} fixed, {rep.waiting_on_miro} waiting "
              f"on Miro, {rep.held} held, {rep.tasks} task(s)",
        evidence={"total": rep.total, "fixed": rep.fixed, "waiting": rep.waiting_on_miro,
                  "held": rep.held, "tasks": rep.tasks, "read_at": now.isoformat()})


async def cycle_dry() -> ActionResult:
    """Reads only: how many causes it would diagnose. Never reaches `run`."""
    try:
        n = len(await LiveIO().findings())
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return ActionResult(False, f"autopilot could not read: {explain(exc)}")
    return ActionResult(True, f"would diagnose {n} check(s) / agent(s) that are not OK",
                        evidence={"checks": ["read every check's newest observation",
                                             "read each agent's last shift"]})


async def undo(token: str) -> ActionResult:
    """`autopilot:<cycle id>`: the cycle stops counting. Nothing is deleted;
    a fix it ran has its own rollback in the audit."""
    from ...kernel.state import fetch_one
    kind, _, rest = str(token).partition(":")
    if kind != "autopilot" or not rest.isdigit():
        return ActionResult(False, "not an autopilot rollback token")
    row = await fetch_one(
        """UPDATE autopilot_cycles SET superseded_at = now()
            WHERE id = $1 AND superseded_at IS NULL RETURNING id""", int(rest))
    return ActionResult(True, f"cycle {rest} superseded" if row else
                        f"cycle {rest} was already superseded")


# ---------------------------------------------------------------- reading

def summary(row: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    """The Command Center line. None (unreadable or never ran) is UNKNOWN."""
    base: dict[str, Any] = {"read_at": now.isoformat(), "last_cycle_at": None,
                            "fixed": None, "waiting_on_miro": None, "held": None,
                            "tasks": None}
    if row is None:
        return base
    at = row["at"]
    base.update(last_cycle_at=at.isoformat() if hasattr(at, "isoformat") else str(at),
                fixed=int(row["fixed"]), waiting_on_miro=int(row["waiting"]),
                held=int(row["held"]), tasks=int(row["tasks"]))
    return base


async def read_summary() -> dict[str, Any]:
    from ...kernel.state import fetch_all, fetch_one
    now = datetime.now(UTC)
    unread: list[str] = []
    try:
        row = await fetch_one(
            """SELECT id, at, fixed, waiting, held, tasks FROM autopilot_cycles
                WHERE superseded_at IS NULL ORDER BY at DESC, id DESC LIMIT 1""")
        out = summary(dict(row) if row is not None else None, now)
        items: list[dict[str, Any]] = []
        if row is not None:
            for r in await fetch_all(
                    """SELECT subject, cause, fix, outcome FROM autopilot_log
                        WHERE cycle_id = $1 AND outcome = 'waiting_miro' ORDER BY id
                        LIMIT 5""", row["id"]):
                items.append({k: str(r[k]) for k in ("subject", "cause", "fix", "outcome")})
        out["items"] = items
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        out = summary(None, now)
        out["items"] = []
        unread.append(f"autopilot: {explain(exc)}")
    out["unread"] = unread
    return out

