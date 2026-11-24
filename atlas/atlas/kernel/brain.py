"""The Brain's numbers: per-department status from the record, last 7 days.

`kernel/departments.py` says where every agent, job and action sits and
writes each seat's SOP from code. This file reads what actually happened and
puts it next to them:

  * **% runs without you** -- of the action executions in the window
    (`actions_log` rows `execute.start`, the executor's own record written
    before every run), how many were Tier 1 (ran on their own) against
    Tier 2 (a card you approved). Refused, watch-only, paused and dry-run
    rows are not executions and are not counted; a start row without a tier
    is left out of both sides and counted as such. No executions: `null`,
    shown as "unknown" -- never 0% and never 100% (invariant 6).
  * **Waiting on you** -- `proposals WHERE status = 'pending'`, the same
    query the sidebar badge counts, so the two can never disagree. Split by
    the card's action; one whose action no department holds is `unmapped`,
    so the parts always add up to the whole.
  * **Seats that ran** -- agents from `agent_runs`; windowed jobs from
    `schedule_runs` rows marked done; agent shifts from the shift runs in
    `agent_runs`. Interval jobs write no per-run record (kernel/missed.py),
    so theirs is `null`: not recorded, which is not "did not run".
  * **Paused scopes** (`pause.active()`) and **read-backs** (`verify.*`
    rows) -- the command strip.

Every read is its own section: one that fails comes back as an error with
the reason, and the rest still render. Nothing here calls the executor, a
tool or a model; it reads four tables and the in-memory registries.
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from ..modules._http import explain
from . import departments as dept
from .departments import DEPARTMENTS, YOU, ActionFacts, Layer

log = logging.getLogger("atlas.brain")

WINDOW_DAYS = 7


def pct(auto: int, approved: int) -> float | None:
    """Share of executions that needed no card, one decimal; None when there
    were none -- unknown, never a number."""
    total = auto + approved
    if total <= 0:
        return None
    return round(100.0 * auto / total, 1)


def _tier_of(payload: Any) -> int | None:
    try:
        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        tier = (data.get("data") or {}).get("tier")
        return int(tier) if tier is not None else None
    except Exception:  # noqa: BLE001
        return None


def tally_executions(rows: list[Any]) -> dict[str, Any]:
    """`execute.start` rows -> per-action Tier 1 / Tier 2 counts, and how
    many carried no readable tier."""
    per: dict[str, Counter] = defaultdict(Counter)
    excluded = 0
    for r in rows:
        tier = _tier_of(r["payload"])
        if tier is None or tier not in (0, 1, 2):
            excluded += 1
            continue
        per[str(r["subject"])]["auto" if tier <= 1 else "approved"] += 1
    return {"per_action": per, "excluded": excluded}


def _runs(auto: int, approved: int, excluded: int = 0) -> dict[str, Any]:
    return {"pct": pct(auto, approved), "auto": auto, "approved": approved,
            "executions": auto + approved, "excluded": excluded}


# ------------------------------------------------------------------ reads

async def _read_executions(since: datetime) -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT subject, payload FROM actions_log
            WHERE at > $1 AND event = 'execute.start'""", since)


async def _read_pending() -> tuple[int, list[Any]]:
    from .state import fetch_all, fetch_one
    total = await fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE status = 'pending'")
    rows = await fetch_all(
        """SELECT action, count(*) AS n FROM proposals
            WHERE status = 'pending' GROUP BY action""")
    return int(total["n"]) if total else 0, rows


async def _read_agent_runs(since: datetime) -> list[Any]:
    from .agents import SHIFT_LIKE
    from .state import fetch_all
    return await fetch_all(
        """SELECT agent, count(*) AS n,
                  count(*) FILTER (WHERE task LIKE $2) AS shifts
             FROM agent_runs WHERE at > $1 GROUP BY agent""", since, SHIFT_LIKE)


async def _read_job_runs(since: datetime) -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT job, count(*) AS n FROM schedule_runs
            WHERE status = 'done' AND COALESCE(finished_at, claimed_at) > $1
            GROUP BY job""", since)


async def _read_verify(since: datetime) -> list[Any]:
    from .state import fetch_all
    return await fetch_all(
        """SELECT event, subject, count(*) AS n FROM actions_log
            WHERE at > $1 AND event LIKE 'verify.%'
            GROUP BY event, subject""", since)


async def _section(name: str, errors: dict[str, str], coro: Any) -> Any:
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        why = explain(exc)
        log.warning("the brain could not read %s: %s", name, why)
        errors[name] = why
        return None


# ------------------------------------------------------------ the catalog

def _facts() -> dict[str, ActionFacts]:
    """Every registered action's facts, tier from the classified decision."""
    from .registry import REGISTRY
    from .verify import has_verifier
    try:
        from ..channels.slack import SlackChannel
        home_for = SlackChannel().home_for
    except Exception:  # noqa: BLE001
        home_for = None
    out: dict[str, ActionFacts] = {}
    for name, spec in REGISTRY.actions.items():
        decision = REGISTRY.decisions.get(name)
        tier = int(decision.tier) if decision is not None else int(spec.tier)
        out[name] = dept.action_facts(name, spec, tier, has_verifier=has_verifier,
                                      home_for=home_for)
    return out


def _in_catalog(name: str) -> bool:
    from .intent import catalog
    return name in catalog()


def _owner_modules(agent: str) -> tuple[str, ...]:
    from .businesses import BY_NAME
    from .missions import BY_AGENT
    if agent in BY_AGENT:
        return tuple(BY_AGENT[agent].modules)
    return (agent,) if agent in BY_NAME else ()


def _raised_by() -> dict[str, list[str]]:
    """action -> the jobs that name it (from JOBS)."""
    out: dict[str, list[str]] = defaultdict(list)
    for job, spec in dept.JOBS.items():
        for a in spec.actions:
            out[a].append(f"the {job} job")
    return out


# ------------------------------------------------------------------ build

async def build(now: datetime | None = None) -> dict[str, Any]:
    from . import executor, pause
    from .agents import ROSTER, shift_cadence_hours
    from .personas import persona_for
    from .registry import REGISTRY
    from .scheduler import POLICIES
    from .tools import TOOLS

    now = now or datetime.now(UTC)
    since = now - timedelta(days=WINDOW_DAYS)
    errors: dict[str, str] = {}

    facts = _facts()
    catalog = {n for n in facts if _in_catalog(n)}
    tool_action = {n: t.action for n, t in TOOLS.items()}
    raised_by = _raised_by()

    exec_rows = await _section("executions", errors, _read_executions(since))
    pending = await _section("waiting_on_you", errors, _read_pending())
    agent_rows = await _section("agent_runs", errors, _read_agent_runs(since))
    job_rows = await _section("schedule_runs", errors, _read_job_runs(since))
    verify_rows = await _section("verify", errors, _read_verify(since))
    try:
        paused = await pause.active()
    except Exception as exc:  # noqa: BLE001
        errors["paused"] = explain(exc)
        paused = None

    tally = tally_executions(exec_rows) if exec_rows is not None else None
    agent_runs = ({str(r["agent"]): (int(r["n"]), int(r["shifts"]))
                   for r in agent_rows} if agent_rows is not None else None)
    job_runs = ({str(r["job"]): int(r["n"]) for r in job_rows}
                if job_rows is not None else None)

    def job_ran(job: str) -> int | None:
        if job == "agent_shifts":
            if agent_runs is None:
                return None
            return sum(s for _, s in agent_runs.values())
        policy = POLICIES.get(job)
        if policy is None or policy.windows is None or job_runs is None:
            return None     # interval job, or unread: not recorded
        return job_runs.get(job, 0)

    def agent_ran(agent: str) -> int | None:
        if agent_runs is None:
            return None
        return agent_runs.get(agent, (0, 0))[0]

    # ---- the SOPs, keyed "agent:<name>", "job:<name>", "action:<name>"
    sops: dict[str, list[dict[str, Any]]] = {}
    agent_acts: dict[str, list[ActionFacts]] = {}
    for name, a in ROSTER.items():
        acts = dept.agent_actions(a.tools, _owner_modules(name), tool_action,
                                  facts, lambda n: n in catalog)
        agent_acts[name] = acts
        reads = [t for t in a.tools if t in TOOLS and TOOLS[t].action is None]
        sops[f"agent:{name}"] = dept.agent_sop(
            name, title=a.title, brief=a.brief, need=str(a.need.value),
            memory_scope=a.memory_scope, handoff=a.handoff, read_tools=reads,
            shift_hours=shift_cadence_hours(name), acts=acts)
    for job, policy in POLICIES.items():
        sops[f"job:{job}"] = dept.job_sop(job, policy, facts)
    for name, f in facts.items():
        sops[f"action:{name}"] = dept.action_sop(f, raised_by.get(name, []))

    # ---- per department
    pending_by_action: dict[str, int] = {}
    total_waiting: int | None = None
    if pending is not None:
        total_waiting, rows = pending
        pending_by_action = {str(r["action"]): int(r["n"]) for r in rows}
    verify_by: dict[str, Counter] = defaultdict(Counter)
    if verify_rows is not None:
        for r in verify_rows:
            state = str(r["event"]).removeprefix("verify.")
            verify_by[str(r["subject"])][state] += int(r["n"])

    placed_waiting = 0
    placed_exec: set[str] = set()
    out_depts = []
    for d in DEPARTMENTS:
        d_actions = sorted(n for n in facts if dept.department_of_action(n) == d.key)
        auto = approved = 0
        if tally is not None:
            for n in d_actions:
                c = tally["per_action"].get(n)
                if c:
                    auto += c["auto"]
                    approved += c["approved"]
                    placed_exec.add(n)
        waiting = sum(pending_by_action.get(n, 0) for n in d_actions) \
            if pending is not None else None
        # A pending card for a namespace this department owns but whose
        # action is no longer registered still belongs to it.
        if pending is not None:
            for n, k in pending_by_action.items():
                if n not in facts and dept.department_of_action(n) == d.key:
                    waiting = (waiting or 0) + k
            placed_waiting += waiting or 0
        v = Counter()
        for n in d_actions:
            v.update(verify_by.get(n, {}))

        agents = []
        for name in d.agents:
            a = ROSTER.get(name)
            if a is None:
                continue
            p = persona_for(name)
            acts = agent_acts.get(name, [])
            agents.append({
                "id": f"agent:{name}", "kind": "agent", "name": name,
                "label": p.name, "title": a.title,
                "layer": dept.agent_layer(a.tools).value,
                "ran_7d": agent_ran(name),
                "tier2": sum(1 for f in acts if f.tier >= 2),
                "actions": len(acts),
            })
        jobs = []
        for job in d.jobs:
            if job not in POLICIES:
                continue
            spec = dept.JOBS.get(job)
            jobs.append({
                "id": f"job:{job}", "kind": "job", "name": job,
                "label": job.replace("_", " "),
                "title": spec.when if spec else "",
                "layer": (spec.layer if spec else Layer.AUTOMATION).value,
                "ran_7d": job_ran(job),
                "recorded": job == "agent_shifts"
                or (POLICIES[job].windows is not None),
            })
        acts_out = [{"id": f"action:{n}", "name": n, "tier": facts[n].tier,
                     "card_only": facts[n].card_only,
                     "verifier": facts[n].verifier} for n in d_actions]
        head = d.head
        head_out: dict[str, Any] = {"agent": None, "label": "You", "title": "sign-off",
                                    "why": d.head_why}
        if head != YOU and head in ROSTER:
            head_out = {"agent": head, "label": persona_for(head).name,
                        "title": ROSTER[head].title, "why": d.head_why}
        recorded_jobs = [j for j in jobs if j["recorded"]]
        d_paused = []
        if paused is not None:
            for pz in paused:
                if pz.scope == pause.ALL or dept.department_of_scope(pz.scope) == d.key:
                    d_paused.append({"scope": pz.scope, "says": pz.says(),
                                     "shadow": pz.shadow})
        out_depts.append({
            "key": d.key, "name": d.name, "core": d.core, "head": head_out,
            "modules": list(d.modules),
            "disabled_modules": [{"module": m, "why": REGISTRY.disabled[m]}
                                 for m in d.modules if m in REGISTRY.disabled],
            "agents": agents, "jobs": jobs, "actions": acts_out,
            "tier2_actions": sum(1 for x in acts_out if x["tier"] >= 2),
            "seats": {
                "agents": len(agents),
                "agents_ran": (sum(1 for x in agents if (x["ran_7d"] or 0) > 0)
                               if agent_runs is not None else None),
                "jobs": len(jobs),
                "jobs_recorded": len(recorded_jobs),
                "jobs_ran": (sum(1 for x in recorded_jobs if (x["ran_7d"] or 0) > 0)
                             if job_runs is not None and agent_runs is not None
                             else None),
            },
            "runs_without_you": _runs(auto, approved) if tally is not None else None,
            "waiting_on_you": waiting,
            "verify": dict(v) if verify_rows is not None else None,
            "paused": d_paused if paused is not None else None,
            "layers": dict(Counter(x["layer"] for x in agents + jobs)),
        })

    unmapped_exec = sorted(set(tally["per_action"]) - placed_exec) if tally else []
    unmapped_exec_n = (sum(sum(tally["per_action"][n].values()) for n in unmapped_exec)
                       if tally else 0)
    # The whole business counts every execution, a renamed or retired
    # action's too: the departments plus `unmapped` add up to it.
    whole_auto = whole_approved = 0
    if tally is not None:
        for c in tally["per_action"].values():
            whole_auto += c["auto"]
            whole_approved += c["approved"]
    verify_total = Counter()
    for c in verify_by.values():
        verify_total.update(c)
    return {
        "read_at": now.isoformat(), "since": since.isoformat(),
        "window_days": WINDOW_DAYS,
        "watch_only": executor.watch_only(),
        "verify_after": executor.verify_after(),
        "layers": [dict(x) for x in dept.LAYERS],
        "whole": {
            "runs_without_you": (_runs(whole_auto, whole_approved, tally["excluded"])
                                 if tally is not None else None),
            "waiting_on_you": total_waiting,
            "paused": ([{"scope": p.scope, "says": p.says(), "shadow": p.shadow,
                         "department": dept.department_of_scope(p.scope)}
                        for p in paused] if paused is not None else None),
            "verify": dict(verify_total) if verify_rows is not None else None,
        },
        "departments": out_depts,
        "unmapped": {
            "executions": unmapped_exec_n, "actions": unmapped_exec,
            "waiting_on_you": ((total_waiting or 0) - placed_waiting
                               if pending is not None else None),
        },
        "sops": sops,
        "errors": errors,
    }
