"""The loop. Runs checks on their own intervals, evaluates, and decides whether
to act, ask, or simply record."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from ..modules._http import explain, redact
from . import background, board, board_state
from .approvals import expire_stale, propose
from .contracts import Health, Observation, Severity, Tier, implements
from .detect import Finding, correlate, evaluate
from .events import publish
from .registry import REGISTRY
from .state import (
    fetch_all,
    fetch_one,
    link_finding_to_proposal,
    record_finding,
    record_observation,
    record_schedule,
)

log = logging.getLogger("atlas.scheduler")

_next_run: dict[str, float] = {}
_last_brief_date: date | None = None
_last_purge: float = 0.0
_last_signals: float = 0.0
_check_ids: dict[str, int] = {}

# time.monotonic() when `loop` last finished a full pass over its steps; None
# before the first one. Read by /healthz (`heartbeat.cycle_age_s`) so a host
# off the box can tell a turning loop from a stalled one (RA-F4).
LAST_CYCLE_AT: float | None = None

# A fresh process has no schedule (`_next_run` is memory only), so its first
# pass runs EVERY check, however long its interval. Until then each reads
# UNKNOWN on the board. The first pass is held to this budget: a check allowed
# longer is cut at it, on that first run only, and reads UNKNOWN with the
# reason (never OK, never a silent wait). Normal timeouts apply afterwards.
FIRST_PASS_BUDGET_S = 45.0
FIRST_PASS_DONE = False


async def _check_id(key: str) -> int:
    if key in _check_ids:
        return _check_ids[key]
    prop, _, name = key.partition(".")
    row = await fetch_one(
        """INSERT INTO properties (name, title, kind)
           VALUES ($1, $1, 'business') ON CONFLICT (name) DO UPDATE
             SET title = EXCLUDED.title RETURNING id""", prop)
    check = REGISTRY.checks[key]
    row2 = await fetch_one(
        """INSERT INTO checks (property_id, name, interval_s, severity, baseline_sigma)
           VALUES ($1,$2,$3,$4,$5)
           ON CONFLICT (property_id, name) DO UPDATE SET interval_s = EXCLUDED.interval_s
           RETURNING id""",
        row["id"], name, int(check.interval.total_seconds()),
        int(check.severity_on_fail), check.baseline_sigma)
    _check_ids[key] = row2["id"]
    return row2["id"]


async def run_once() -> list[Finding]:
    global FIRST_PASS_DONE
    now = time.monotonic()
    # A check Miro does not use (ATLAS_UNUSED_CHECKS) is never run, even if
    # something registered it after load.
    due = [k for k, c in REGISTRY.checks.items()
           if now >= _next_run.get(k, 0.0) and not board_state.is_unused(k)]
    findings: list[Finding] = []

    first = not FIRST_PASS_DONE

    async def _run(key: str) -> None:
        check = REGISTRY.checks[key]
        _next_run[key] = now + check.interval.total_seconds()
        limit = check.timeout.total_seconds()
        cut = first and limit > FIRST_PASS_BUDGET_S
        if cut:
            limit = FIRST_PASS_BUDGET_S
        try:
            obs = await asyncio.wait_for(check.run(), timeout=limit)
        except TimeoutError:
            # A check that timed out did not measure anything. Dropping it here
            # made a hanging collector invisible: no observation, no finding,
            # and the property silently stopped being watched.
            obs = Observation(
                check=key, health=Health.UNKNOWN,
                detail=(f"first run after start cut at {limit:.0f}s; it re-runs "
                        f"on the next pass" if cut
                        else f"timed out after {limit:.0f}s"))
            if cut:
                # Not a verdict on the check: run it again on the next pass,
                # with its own timeout, instead of leaving it UNKNOWN for a
                # whole interval.
                _next_run[key] = time.monotonic()
            log.warning("check %s timed out", key)
        except Exception as exc:  # noqa: BLE001
            obs = Observation(
                check=key, health=Health.UNKNOWN,
                detail=f"check raised {type(exc).__name__}: {redact(exc)}")
            log.warning("check %s raised: %s", key, exc)
        cid = await _check_id(key)
        await record_observation(cid, obs)
        await record_schedule(
            cid, datetime.now(UTC),
            datetime.now(UTC) + check.interval)
        publish("check", key=key, health=str(obs.health), detail=obs.detail,
                value=obs.value, unit=obs.unit, observed_at=obs.at.isoformat())
        if finding := await evaluate(key, check, obs, cid):
            finding_id = await record_finding(finding, key)
            publish("finding", id=finding_id, key=key,
                    severity=int(finding.severity), title=finding.title)
            findings.append(replace(finding, id=finding_id))

    await asyncio.gather(*(_run(k) for k in due), return_exceptions=True)
    if first:
        FIRST_PASS_DONE = True
        log.info("first check pass after start: %d checks in %.1fs",
                 len(due), time.monotonic() - now)
    return findings


async def _mention_withheld(action: str, refusal: str) -> None:
    """One INFO line in the brief for a proposal the never-repeat ledger held
    back, at most once a day per action.

    Deliberately not silent and deliberately not noisy. A finding that keeps
    recurring against a blocked action would otherwise vanish every cycle
    with nothing to show for it, and "ATLAS stopped mentioning it" must never
    be something he has to read a log to discover. The title is the dedup
    key, so a second refusal the same day writes nothing.
    """
    from .state import execute_sql

    title = f"Not offered again: {action}"
    try:
        seen = await fetch_one(
            """SELECT id FROM brief_queue
                WHERE title = $1 AND at >= date_trunc('day', now()) LIMIT 1""",
            title)
        if seen is not None:
            return
        await execute_sql(
            "INSERT INTO brief_queue (title, body, severity) VALUES ($1,$2,$3)",
            title, f"{refusal} Say what is different and I'll ask again.",
            int(Severity.INFO))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not queue the withheld proposal for %s: %s",
                    action, exc)


async def handle(findings: list[Finding]) -> None:
    """Decide what to do about each finding. The reasoner is only consulted for
    things that are actually actionable — most findings just get recorded."""
    from .reason import diagnose

    groups = await correlate(findings)
    if groups:
        log.info("correlated %d multi-property events", len(groups))

    for finding in findings:
        if finding.severity < Severity.NORMAL:
            continue
        # One finding that cannot be reasoned about must not take the rest of
        # the iteration with it. Before this, a single failure here aborted
        # expire_stale, the morning brief and the retention run for good.
        try:
            proposal = await diagnose(finding)
        except Exception:
            log.exception("could not diagnose %s — recording it unreasoned",
                          finding.check)
            continue
        if proposal is None:
            continue
        decision = REGISTRY.decisions.get(proposal.action)
        if decision is None or not decision.allowed:
            continue
        if decision.tier is Tier.AUTO:
            from .executor import ExecutionRequest, execute
            await execute(ExecutionRequest(
                action=proposal.action, params=proposal.params,
                reason=proposal.reason))
        else:
            # Same shape as the `diagnose()` guard above, and for the same
            # reason: one finding must not take the rest of the iteration
            # with it. `propose()` raises when the never-repeat ledger has
            # this action and these parameters down as failed or declined
            # twice — a normal outcome, not an error, and the next finding
            # still deserves its card.
            try:
                pid = await propose(
                    action=proposal.action, params=proposal.params,
                    reason=proposal.reason, severity=finding.severity,
                    evidence=finding.observation.evidence,
                    rollback_plan=proposal.rollback_plan,
                    confidence=proposal.confidence,
                    urgent=proposal.urgent,
                    # Keyed on the CAUSE, not the check. Six Stripe checks
                    # blind on one missing key, and four Fiverr checks behind
                    # one bot wall, raised ten cards for two problems when
                    # the key was the check name. A finding that recurs every
                    # cycle -- which is what an unfixable UNKNOWN does --
                    # must not raise a new card each time round either.
                    dedup_key=f"{proposal.action}@{_cause_key(finding)}",
                    automatic=True)
            except ValueError as exc:
                log.info("proposal refused: %s", exc)
                # He is told what ATLAS decided not to offer him, once a day
                # per action. A guard that silently swallows a finding every
                # cycle is indistinguishable from a detector that stopped
                # working, which is the failure mode this file exists to
                # avoid. INFO, and never more than one line a day: the point
                # is that it is visible, not that it nags.
                await _mention_withheld(proposal.action, str(exc))
                continue
            # The timeline shows detection, proposal and outcome as one row,
            # which needs the two ends joined at the point they are created.
            if finding.id is not None and pid:
                await link_finding_to_proposal(finding.id, pid)


PAGE_WINDOW = "24 hours"


async def page(finding: Finding) -> bool:
    """A failing HIGH or CRITICAL check reaches Miro whether or not there is
    an action for it.

    Until this, a finding only left the building as a proposal card: the
    reasoner had to name an action, and when it named none the finding was
    recorded and dropped. `matrix_rooms.urgent` -- a CRITICAL check -- failed
    sixty-two times through one night with no proposal, and the phone never
    rang. Twenty green tiles cannot tell him anything if the one that flips
    is routed through "is there something I could do about it".

    So: HIGH goes to Slack, CRITICAL rings, on the finding itself. Once per
    title per day, kept in the signals table so a restart does not re-page.
    UNKNOWN is not paged from here -- an unrunnable check is a card with a
    cause, and it already gets one.
    """
    if (finding.severity < Severity.HIGH
            or finding.observation.health is not Health.FAILING):
        return False
    from .comms import Outbound, notify
    from .state import execute_sql

    module = finding.check.split(".", 1)[0]
    try:
        seen = await fetch_one(
            f"""SELECT id FROM signals
                WHERE property = $1 AND title = $2
                  AND at > now() - interval '{PAGE_WINDOW}'
                LIMIT 1""", module, finding.title)
        if seen:
            return False
        await execute_sql(
            """INSERT INTO signals (property, severity, title, body, delivered)
               VALUES ($1,$2,$3,$4,true)""",
            module, int(finding.severity), finding.title, finding.detail)
    except Exception:
        # If the dedup cannot be read, page anyway. Ringing twice about a
        # real CRITICAL is a nuisance; not ringing is the failure this
        # function exists to prevent.
        log.exception("could not record the page for %s; paging anyway",
                      finding.check)
    try:
        from .comms import Button
        await notify(Outbound(title=finding.title, body=finding.detail,
                              severity=finding.severity, topic=module,
                              # `finding.check` is the dotted "<module>.<check>"
                              # key -- a stable id for this one check, which
                              # lets escalation's G3 re-ring rule (a CRITICAL
                              # page answered away, now home) tell this
                              # incident apart from another one that happens
                              # to share a title, rather than falling back to
                              # title + its own 24h window.
                              incident_key=finding.check,
                              # One tap stops the paging until the
                              # check changes; see acks.py.
                              buttons=(Button("Acknowledge",
                                              f"ack:{finding.check}"),)))
    except Exception:
        log.exception("could not page %s", finding.check)
        return False
    return True


def _cause_key(finding) -> str:
    """One key per underlying problem.

    A check that could not run names its blocker in the detail -- a missing
    credential, a bot wall, an unreachable host. Two checks blocked by the
    same thing share a key and therefore a card. A check that ran and found
    something keeps its own name: two real findings are two decisions.
    """
    import re
    detail = (getattr(finding.observation, "detail", "") or "")
    module = finding.check.split(".", 1)[0]
    if getattr(finding.observation, "health", None) is not Health.UNKNOWN:
        return finding.check
    # An env-style name in a could-not-run detail is the credential it is
    # missing: "no STRIPE_FLYCHEAP_KEY set", "GITLAB_TOKEN is not set".
    m = re.search(r"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)\b", detail)
    if m:
        return f"{module}:missing:{m.group(1)}"
    for word in ("PerimeterX", "bot detection", "challenge page"):
        if word in detail:
            return f"{module}:botwall"
    if "timed out" in detail or "unreachable" in detail.lower():
        return f"{module}:unreachable"
    return finding.check


# Miro, 2026-09-30 and again 2026-10-12 (memory approval-policy): "whatever
# you think should be done should be automatically approved, but whatever is
# really urgent should only need human approval." A signal that suggests one
# of THESE actions runs it through the executor instead of raising a card --
# each is Tier 1 in its module's code (dry run + rollback, invariant 8), and
# this list is closed, like `unattended.ALLOWED`: adding a name here is a
# code change, never a runtime one. Before this, every signal became a card
# whatever its tier, so school.draft_work and messages.draft (both Tier 1)
# raised 13 and 4 cards in 30 days that nobody needed to answer.
#
# Never a card_only or principal_only action (`clips.submit_to_whop` is
# Tier 1 but records MIRO's own Whop submission: run by itself it would write
# a false record), and only while the action is Tier 1 in the registry.
SIGNAL_AUTORUN: frozenset[str] = frozenset({
    "school.draft_work",        # a draft row in content_drafts; rollback discards it
    "messages.draft",           # draft text on messages_inbound; nothing is sent
    "linkedin.approve_draft",   # bank status draft -> approved; rollback flips it back
})


def signal_autorun_enabled() -> bool:
    """`ATLAS_SIGNAL_AUTORUN` (on by default): off sends every signal back to
    a card, as before 2026-10-12. Read fresh each cycle."""
    return os.getenv("ATLAS_SIGNAL_AUTORUN", "on").strip().lower() not in (
        "0", "off", "false", "no")


def autoruns(action: str) -> bool:
    """Whether a signal naming `action` runs it rather than carding it."""
    if action not in SIGNAL_AUTORUN or not signal_autorun_enabled():
        return False
    spec = REGISTRY.actions.get(action)
    decision = REGISTRY.decisions.get(action)
    return (spec is not None and decision is not None and decision.allowed
            and decision.tier is Tier.AUTO
            and not getattr(spec, "card_only", False)
            and not getattr(spec, "principal_only", False))


async def _autorun(module: str, signal) -> tuple[str, str]:
    """Run a signal's Tier 1 action through the one path that acts.

    Returns ("done", detail) when it ran, ("held", detail) when ATLAS's own
    state held it (watch-only, a pause, an hourly ceiling): then the signal
    is still told to him as a plain notice -- never swallowed, and never a
    card an approval could not run either -- or ("card", reason) when the
    dry run or the run said no: then the card is raised as before, the
    reason first."""
    from .approvals import topic_for_action
    from .comms import Outbound, notify
    from .executor import ExecutionRequest, execute
    from .lead_followup import _held, _hold_reason
    action = str(signal.suggests)
    made = await execute(ExecutionRequest(
        action=action, params=dict(signal.params or {}),
        reason=f"{signal.title} (runs itself: Tier 1, memory approval-policy)"))
    topic = topic_for_action(action, dict(signal.params or {})) or module
    if _held(made):
        log.info("signal %s held: %s", action, made.detail)
        await notify(Outbound(title=signal.title, body=signal.body,
                              severity=signal.severity, topic=topic))
        return "held", made.detail
    if not made.ok:
        return "card", _hold_reason(made.detail)
    try:
        await notify(Outbound(
            title=f"Done on its own: {signal.title}"[:150],
            body=(f"{made.detail}\n\n{signal.body}\n\n`{action}` is Tier 1 (your "
                  f"rule, 2026-10-12); ATLAS_SIGNAL_AUTORUN=off sends it back to "
                  f"a card.")[:3000],
            # The signal's own weight: a coursework deadline stays HIGH.
            severity=signal.severity,
            topic=topic,
            kind="receipt"))
    except Exception:  # noqa: BLE001
        log.warning("signal %s ran, but its receipt was not sent", action)
    return "done", made.detail


async def collect_signals() -> int:
    """Ask every module what it wants to say, and route it.

    `Signal` has been in contracts.py from the start, with a docstring saying
    the kernel routes them — and nothing did. A module could emit one and it
    went nowhere.

    A signal that names an action becomes a proposal, so it reaches Miro as an
    approval card carrying the dry run -- unless the action is on the closed
    `SIGNAL_AUTORUN` list and Tier 1 in code: then it runs through the
    executor and he gets a receipt, and only a refusal is the card. A signal
    that names none is just told to him.
    """
    from .approvals import propose, topic_for_action
    from .comms import attach_to_card
    from .state import execute_sql, fetch_one

    raised = 0
    for name, module in list(REGISTRY.modules.items()):
        if not implements(module, "signals"):
            continue        # documented default: no signals
        try:
            signals = await module.signals()
        except Exception:
            log.exception("module %s failed to produce signals", name)
            continue

        for signal in signals or ():
            # Deduplicated on the title: a module that keeps noticing the same
            # thing every cycle must not produce a card every cycle.
            seen = await fetch_one(
                """SELECT id FROM signals
                   WHERE property = $1 AND title = $2
                     AND at > now() - interval '24 hours'
                   LIMIT 1""", name, signal.title)
            if seen:
                continue

            await execute_sql(
                """INSERT INTO signals (property, severity, title, body,
                                        suggests, params)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                name, int(signal.severity), signal.title, signal.body,
                signal.suggests, json.dumps(signal.params, default=str))
            raised += 1

            decision = REGISTRY.decisions.get(signal.suggests or "")
            if signal.suggests and decision and decision.allowed:
                # The action is chosen by the module, from the closed catalog —
                # the reasoner is not involved and cannot be, because a module
                # knows which room and which text and it does not.
                lead = ""
                if autoruns(signal.suggests):
                    try:
                        outcome, said = await _autorun(name, signal)
                    except Exception as exc:
                        # One action that raises must not take the rest of
                        # this cycle's signals with it: it is the card.
                        log.exception("signal %s autorun raised", signal.suggests)
                        outcome, said = "card", f"it could not run ({explain(exc)})"
                    if outcome != "card":
                        continue
                    lead = (f"Held for you: {said}. Approving runs every check "
                            f"again and does it if it passes now.\n")
                try:
                    pid = await propose(
                        action=signal.suggests, params=signal.params,
                        reason=lead + signal.body, severity=signal.severity,
                        evidence={"signal": signal.title},
                        # Signals already dedup on title for 24h, but that
                        # window expires while a proposal can still be
                        # pending -- so the second day would raise a
                        # duplicate of a card still waiting.
                        dedup_key=f"{signal.suggests}@{signal.title}",
                        automatic=True)
                except ValueError as exc:
                    log.info("signal %s not proposed: %s", signal.suggests, exc)
                except Exception:
                    log.exception("could not propose %s", signal.suggests)
                else:
                    if signal.attachment:
                        # A picture or a clip a module wants shown alongside
                        # its own card -- the same place `kernel.reels`
                        # has always put a reel's preview, generalised here
                        # so the module needed no upload code of its own.
                        import os as _os
                        try:
                            outcome = await attach_to_card(
                                pid,
                                topic=topic_for_action(signal.suggests, signal.params),
                                path=signal.attachment,
                                filename=_os.path.basename(signal.attachment),
                                title=signal.title[:150],
                                note="approve or decline on the card above")
                            if outcome != "uploaded":
                                log.info("signal %r attachment not uploaded: %s",
                                         signal.title, outcome)
                        except Exception:
                            log.exception("could not attach media for %s",
                                          signal.suggests)
            else:
                from .comms import Outbound, notify
                # A signal may name its own topic. The mail module raises a
                # Guardian customer's email and a FlyCheap one from the same
                # code, and each belongs in that business's channel, not in
                # a general "mail" channel nobody watches for either.
                topic = str((signal.params or {}).get("topic") or name)
                await notify(Outbound(title=signal.title, body=signal.body,
                                      severity=signal.severity, topic=topic))
    return raised


async def _maybe_signals() -> None:
    """Modules are asked once every SIGNAL_INTERVAL, not every cycle.

    Producing a signal can be expensive — drafting a reply runs a model — and
    the scheduler turns over every 30 seconds.
    """
    global _last_signals
    now = time.monotonic()
    interval = float(os.getenv("SIGNAL_INTERVAL_S", "900"))
    if now - _last_signals < interval:
        return
    _last_signals = now
    raised = await collect_signals()
    if raised:
        log.info("raised %d signal(s)", raised)


async def _maybe_purge() -> None:
    """Retention, hourly. Browsing history does not delete itself."""
    global _last_purge
    now = time.monotonic()
    if now - _last_purge < 3600:
        return
    _last_purge = now
    from .privacy import run as retention
    try:
        result = await retention()
        if result["purged_events"]:
            log.info("retention: %s", result)
    except Exception:
        log.exception("retention run failed")
    # Maintenance in the same hourly slot: memories stored without an
    # embedding are invisible to recall by meaning until one is added. A
    # no-op without OLLAMA_URL; bounded per pass; its own failure is its own.
    from .memory import backfill_embeddings
    try:
        filled, stopped = await backfill_embeddings(None)
        if filled or stopped:
            log.info("embedding backfill: %d embedded%s", filled,
                     f"; {stopped}" if stopped else "")
    except Exception:
        log.exception("embedding backfill failed")


_last_rhythm: float = 0.0


_last_fixes = 0.0


async def _maybe_fixes() -> None:
    """Every FIX_SWEEP_HOURS (default 6): lasting degraded/failing checks go
    to the agent roster for a diagnosis and, where the catalog allows, a fix
    card (`kernel.fixes`). Detached: an agent run is a model call or three."""
    global _last_fixes
    now = time.monotonic()
    try:
        every = float(os.getenv("FIX_SWEEP_HOURS", "6")) * 3600
    except ValueError:
        every = 6 * 3600
    if _last_fixes and now - _last_fixes < every:
        return
    _last_fixes = now
    from .fixes import sweep
    try:
        tried = await sweep()
        if tried:
            log.info("fix sweep: %s", ", ".join(k for k, _ in tried))
    except Exception:
        log.exception("fix sweep failed")


# ------------------------------------------------------------- agent shifts

# At most this many shifts (self-tests included) start in one cycle. Not a
# throughput cap on the roster -- an agent throttled this cycle is still due
# next cycle, since due-ness is read fresh from `agent_runs` every time.
SHIFT_LIMIT_PER_CYCLE = 2


async def _shift_skip_audited_today(agent_name: str, now: datetime) -> bool:
    midnight_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        """SELECT 1 FROM actions_log WHERE event = $1 AND subject = $2
           AND at >= $3::timestamptz LIMIT 1""",
        "agent.shift_skipped", agent_name, midnight_local)
    return row is not None


async def _audit_shift_skip(agent_name: str, reasons: list[str], now: datetime) -> None:
    """One line in the audit trail per not-ready agent per day -- never
    every cycle, and never silent (invariant 7's spirit applies here too: a
    not-ready agent staying quiet forever is as bad as one that looks ready)."""
    if await _shift_skip_audited_today(agent_name, now):
        return
    from .audit import audit
    await audit("agent.shift_skipped", agent_name, {"reasons": reasons})


async def _maybe_agent_shifts() -> None:
    """"Every agent that is READY ... runs on its schedule, always" (Miro,
    2026-09-27). Once per cycle, for every roster agent in a fixed
    (alphabetical) order:

      * not ready -> never runs; its reasons are audited once a day
        (`_audit_shift_skip`), not every cycle.
      * ready, and no `agent_runs` row at all -- never run, not even once --
        gets the one-shot self-test (`agents.warmup`) instead of a shift.
      * ready, has run before, and its shift is due (`agents.shift_due`,
        read from `agent_runs`, so a restart cannot cause a double run) ->
        one real run through its own `Agent.run()` with its own shift
        (`agents.shift_prompt`) -- the normal agent path, with its own tools,
        never warmup's tool-free clone.

    At most `SHIFT_LIMIT_PER_CYCLE` self-tests-or-shifts START this cycle;
    an agent throttled this way is simply still due next cycle. A run that
    raises anything other than `LocalModelUnavailable` (`Agent.run()` only
    catches that one) is recorded as a failed run here, the same way
    `agents.warmup` already does for its own probe -- otherwise the agent
    never gets an `agent_runs` row, stays perpetually "never run", and a
    dead provider burns the per-cycle budget on it forever.
    """
    from . import agents as agents_module

    now = _now()
    rows = await fetch_all(shift_rows_sql(), *shift_rows_args(now))
    last_any = {r["agent"]: r["last_any"] for r in rows}
    last_shift = {r["agent"]: r["last_shift"] for r in rows}
    failures = {r["agent"]: list(r.get("failures") or []) for r in rows}

    started = 0
    for name in sorted(agents_module.ROSTER):
        ready, reasons = agents_module.readiness(name)
        if not ready:
            await _audit_shift_skip(name, reasons, now)
            continue
        if started >= SHIFT_LIMIT_PER_CYCLE:
            continue

        agent = agents_module.ROSTER[name]
        if last_any.get(name) is None:
            try:
                await agents_module.warmup(name)
            except Exception:
                # `warmup` already turns every failure it knows about into
                # a recorded row; reaching this is a bug in it, not an
                # expected outcome (same reasoning as agents_warm's sweep).
                log.exception("self-test failed for %s", name)
            started += 1
            continue

        if not agents_module.shift_due(name, last_shift.get(name), now,
                                       failures=failures.get(name, [])):
            continue
        task = agents_module.shift_prompt(name)
        try:
            await agent.run(task)
        except Exception as exc:
            from .harness import record_run
            from .quota import ReservedForMiro
            if isinstance(exc, ReservedForMiro):
                # The free allowance left is Miro's: not a failure of the
                # agent, and retried after SHIFT_RETRY_MIN like one.
                outcome = agents_module.SHIFT_DEFERRED + " quota"
                log.info("agent shift deferred for %s: quota kept for Miro", name)
            else:
                outcome = f"{agents_module.SHIFT_FAILED} {explain(exc)}"
                log.exception("agent shift failed for %s", name)
            await record_run(
                name, task, model="", provider="",
                turns=0, outcome=outcome, transcript=[])
        started += 1

    for name in sorted(agents_module.ROSTER):
        if last_any.get(name) is None or not agents_module.readiness(name)[0]:
            continue
        st = agents_module.shift_state(
            name, last_shift.get(name), failures.get(name, []), now)
        if st.state in ("failed", "overdue"):
            await _alert_shift(name, st, now)


def shift_rows_sql() -> str:
    """Per agent: the last run of any kind, the last SUCCESSFUL shift (a
    failed, deferred, unanswered or tool-limit-stopped one never counts as
    the day's shift; `agents.SHIFT_UNSUCCESSFUL_LIKE`) and the times of
    failed/deferred shift attempts in the last two days. Shared by the
    scheduler step and `/api/agents`, so both read the same truth."""
    return """SELECT agent, max(at) AS last_any,
                  max(at) FILTER (WHERE task LIKE $1
                        AND coalesce(outcome, '') <> ''
                        AND NOT (outcome LIKE ANY($2::text[]))) AS last_shift,
                  max(at) FILTER (WHERE task LIKE $1) AS last_attempt,
                  array_agg(at ORDER BY at) FILTER (WHERE task LIKE $1
                        AND (coalesce(outcome, '') = ''
                             OR outcome LIKE ANY($2::text[]))
                        AND at >= $3::timestamptz) AS failures
           FROM agent_runs GROUP BY agent"""


def shift_rows_args(now: datetime) -> tuple:
    from . import agents as agents_module
    return (agents_module.SHIFT_LIKE, list(agents_module.SHIFT_UNSUCCESSFUL_LIKE),
            now - timedelta(hours=48))


async def _alert_shift(name: str, st, now: datetime) -> None:
    """One FAILING alert per agent per UTC day, claimed in `schedule_runs`
    so a restart never repeats it. No channel configured is not silence:
    it is audited as `agent.shift_alert_unrouted` and the page says so."""
    from . import occurrence
    from .audit import audit
    from .comms import Outbound, notify

    job, window = f"agent-shift-alert:{name}", now.astimezone(UTC).date().isoformat()
    if not await occurrence.claim(job, window):
        return
    slot = st.slot.astimezone(UTC).strftime("%H:%M UTC") if st.slot else "unknown"
    what = ("every attempt failed" if st.state == "failed"
            else "is overdue")
    body = (f"Agent {name}: today's shift (slot {slot}) {what}; "
            f"{st.attempts} attempt(s) since the slot.")
    try:
        channels = await notify(Outbound(
            title=f"{name} shift FAILING", body=body, severity=Severity.HIGH,
            topic="agents", kind="alert", incident_key=job))
    except Exception as exc:  # noqa: BLE001
        log.warning("shift alert for %s could not be sent (%s)", name,
                    type(exc).__name__)
        await occurrence.release(job, window)
        return
    if not channels:
        await audit("agent.shift_alert_unrouted", name,
                    {"slot": slot, "state": st.state, "attempts": st.attempts})
    await occurrence.done(job, window)


async def _maybe_rhythm() -> None:
    """Rebuild the rolling early/late baseline, hourly -- same monotonic-clock
    gate as `_maybe_purge`. Cheap and read-mostly (three grouped counts plus
    a fixed 168-row upsert), so hourly is simply how fresh the pattern check
    needs it, not a cost-driven throttle."""
    global _last_rhythm
    now = time.monotonic()
    if now - _last_rhythm < 3600:
        return
    _last_rhythm = now
    from .rhythm import tally
    try:
        await tally()
    except Exception:
        log.exception("rhythm tally failed")


def _brief_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("BRIEF_HOUR", "7"))))
    except ValueError:
        return 7


def _daily_brief_on() -> bool:
    return os.getenv("BRIEF_DAILY", "off").strip().lower() in ("1", "on", "true", "yes")


async def _maybe_brief() -> None:
    """Send the morning brief once per day, at BRIEF_HOUR local time.

    Missed-run policy (POLICIES): skip-stale. A box that was down at 07:00
    still sends today's brief when it comes back the same day; a day it
    never came back on is recorded 'skipped', never sent late.
    """
    global _last_brief_date
    # Miro, 2026-10-06: "weekly only" -- the Monday summary (_maybe_weekly)
    # is the report; cards still arrive as they happen. BRIEF_DAILY=on
    # brings the morning brief back.
    if not _daily_brief_on():
        return
    now = _now()
    if now.hour < _brief_hour() or _last_brief_date == now.date():
        return
    # The in-memory date is only a fast path; the window is claimed in
    # Postgres (kernel.occurrence), so a restart inside it never sends a
    # second copy (37 briefs on 2026-09-22).
    from . import missed, occurrence
    w = await missed.due(POLICIES["brief"], now)
    if w is None:
        return
    if not await occurrence.claim("brief", w.key, w.scheduled_for):
        _last_brief_date = now.date()
        return
    from .brief import send
    try:
        await send()
    except Exception:
        # The day is NOT marked done: a brief that failed to build or send
        # (Postgres unreadable, a channel down) is retried on the next
        # cycle, never silently lost for the day.
        await occurrence.release("brief", w.key)
        log.exception("morning brief failed; will retry next cycle")
        return
    await occurrence.done("brief", w.key)
    _last_brief_date = now.date()
    log.info("morning brief sent")


_last_meeting_date: date | None = None


async def _maybe_agency_meeting() -> None:
    """The Agency meeting (`kernel.meeting.run_daily`), once a day at or
    after BRIEF_HOUR local: built from the record, reworded by a model only
    where the grounding check passes, stored in `agency_meetings`, then a
    short summary posted the way the brief is.

    Missed-run policy (POLICIES): skip-stale, today only. A box that was
    down at the hour still holds today's meeting when it comes back the same
    day; a day it never came back on is recorded 'skipped', never built
    late -- yesterday's meeting read today would be yesterday's room.
    """
    global _last_meeting_date
    now = _now()
    if now.hour < _brief_hour() or _last_meeting_date == now.date():
        return
    from . import missed, occurrence
    w = await missed.due(POLICIES["agency_meeting"], now)
    if w is None:
        return
    if not await occurrence.claim("agency_meeting", w.key, w.scheduled_for):
        _last_meeting_date = now.date()
        return
    from .meeting import run_daily
    try:
        await run_daily()
    except Exception:
        # Not marked done: a meeting whose record could not be read, or
        # whose row could not be stored, is retried on a later cycle today.
        await occurrence.release("agency_meeting", w.key)
        log.exception("agency meeting failed; will retry next cycle")
        return
    await occurrence.done("agency_meeting", w.key)
    _last_meeting_date = now.date()
    log.info("agency meeting built and posted")


_last_board_date: date | None = None


async def _maybe_lead_board() -> None:
    """The agency lead board (`modules.agency.leads.post_board`), once a day
    at or after BRIEF_HOUR local: counts by stage, read then, with the read
    time. Slack only.

    Missed-run policy (POLICIES): skip-stale, today only -- yesterday's
    counts read today would be today's counts anyway, and a board for a day
    already over is not worth posting. A failed post is retried until local
    midnight.
    """
    global _last_board_date
    now = _now()
    if now.hour < _brief_hour() or _last_board_date == now.date():
        return
    from . import missed, occurrence
    w = await missed.due(POLICIES["agency_lead_board"], now)
    if w is None:
        return
    if not await occurrence.claim("agency_lead_board", w.key, w.scheduled_for):
        _last_board_date = now.date()
        return
    from ..modules.agency.leads import post_board
    try:
        await post_board()
    except Exception:
        await occurrence.release("agency_lead_board", w.key)
        log.exception("agency lead board failed; will retry next cycle")
        return
    await occurrence.done("agency_lead_board", w.key)
    _last_board_date = now.date()


_last_tournament_date = None


def _tournament_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("TOURNAMENT_HOUR", "3"))))
    except ValueError:
        return 3


async def _maybe_tournament() -> None:
    """The nightly bake-off (JUDGMENT.md §4), once a day after
    TOURNAMENT_HOUR local. It retrains every head and picks who serves;
    with fewer than ten new decisions since last time it does nothing.

    Missed-run policy: run-once. After an outage it retrains once, not once
    per missed night; a restart after the hour no longer runs it again."""
    global _last_tournament_date
    now = _now()
    if now.hour < _tournament_hour() or _last_tournament_date == now.date():
        return
    from . import missed, occurrence
    w = await missed.due(POLICIES["judgment"], now)
    if w is None:
        return
    _last_tournament_date = now.date()
    if not await occurrence.claim_once("judgment", w.key, w.scheduled_for):
        return
    from .judgment.nightly import run_all
    try:
        for line in await run_all():
            log.info("judgment: %s", line)
    except BaseException as exc:
        await occurrence.fail("judgment", w.key, explain(exc)
                              if isinstance(exc, Exception) else "cancelled")
        raise
    await occurrence.done("judgment", w.key)


_last_review_date: date | None = None


def _now() -> datetime:
    """Wrapped so a test can control "now" without waiting for a real clock,
    the same seam `_brief_hour` gives the hour itself."""
    return datetime.now().astimezone()


def _review_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("REVIEW_HOUR", "21"))))
    except ValueError:
        return 21


async def _maybe_review() -> None:
    """Send the daily review once per day, at REVIEW_HOUR local time.

    Same once-per-local-date guard as the morning brief: keyed on the local
    calendar date so a restart just after REVIEW_HOUR does not send a second
    copy, and a box that was down at 21:00 still gets one later that evening.
    Missed-run policy: skip-stale -- a day missed entirely is recorded
    'skipped', never sent the next day.
    """
    global _last_review_date
    now = _now()
    if now.hour < _review_hour() or _last_review_date == now.date():
        return
    # Claimed in Postgres, not just remembered: ten reviews went out on
    # 2026-09-27 because every restart after 23:00 forgot the date.
    from . import missed, occurrence
    w = await missed.due(POLICIES["review"], now)
    if w is None:
        return
    _last_review_date = now.date()
    if not await occurrence.claim("review", w.key, w.scheduled_for):
        return
    from .review import send
    try:
        await send()
        await occurrence.done("review", w.key)
        log.info("daily review sent")
    except Exception:
        await occurrence.release("review", w.key)
        _last_review_date = None          # retried on a later tick
        log.exception("daily review failed")


_last_weekly_week: tuple[int, int] | None = None


def _weekly_day() -> int:
    """Which weekday the summary goes out. Monday is 0, per date.weekday()."""
    try:
        return max(0, min(6, int(os.getenv("WEEKLY_DAY", "0"))))
    except ValueError:
        return 0


async def _maybe_weekly() -> None:
    """Once a week, after the morning brief has gone.

    Missed-run policy: run-once. A box that was down on Monday sends it when
    it comes back; three missed weeks send one summary, not three. The
    window (the WEEKLY_DAY date's ISO week) is claimed in Postgres, so a
    restart later in the week no longer sends a second copy. A failed send
    is released: not retried every cycle by this process (the week is
    remembered in memory), but retried by the next process -- silence is the
    failure to avoid, and `weekly.send` itself refuses a second summary for
    the same ISO week from `reports`.
    """
    global _last_weekly_week
    now = _now()
    year, week, _ = now.isocalendar()
    if _last_weekly_week == (year, week):
        return
    from . import missed, occurrence
    w = await missed.due(POLICIES["weekly"], now)
    if w is None:
        return
    _last_weekly_week = (year, week)
    if not await occurrence.claim("weekly", w.key, w.scheduled_for):
        return
    from .weekly import send
    try:
        await send()
    except Exception:
        await occurrence.release("weekly", w.key)
        log.exception("weekly summary failed; retried after a restart")
        return
    await occurrence.done("weekly", w.key)
    log.info("weekly summary sent")


async def _maybe_playlist() -> None:
    """The Claude playlist (paper)'s weekly proposal (markets/playlist.py),
    on PLAYLIST_DAY (Sunday) at PLAYLIST_HOUR (15:00) in America/New_York --
    Miro's time, whatever the host's zone. It reads closes, asks the REASON
    route (Claude first) for target weights, checks them and raises one
    approval card; nothing moves without his yes.

    Missed-run policy: run-once, claimed once. A box that was down on Sunday
    proposes once when it comes back, never once per missed week; a run that
    failed is not retried (the week's row and the card's dedup key would
    refuse a second one anyway)."""
    from ..modules.markets import playlist
    now = _now().astimezone(playlist.TZ)
    from . import missed, occurrence
    w = await missed.due(POLICIES["playlist"], now)
    if w is None:
        return
    if not await occurrence.claim_once("playlist", w.key, w.scheduled_for):
        return
    try:
        status = await playlist.propose_week(date.fromisoformat(w.key))
    except BaseException as exc:
        await occurrence.fail("playlist", w.key, explain(exc)
                              if isinstance(exc, Exception) else "cancelled")
        raise
    await occurrence.done("playlist", w.key)
    log.info("playlist proposal for %s: %s", w.key, status)


async def _maybe_ideas() -> None:
    """Trade idea cards (markets/ideas.py): weekdays at 16:30 New York,
    after the US close. Settles the paper idea book, then raises at most two
    Tier 2 cards; nothing is booked without his yes, nothing real is traded.

    Missed-run policy: skip-stale. A scan runs only on its own New York
    day; a missed day is recorded 'skipped', never scanned late -- a stale
    idea is worthless. Claimed once: a scan cut short is not repeated."""
    from ..modules.markets import ideas
    now = _now().astimezone(ideas.TZ)
    from . import missed, occurrence
    w = await missed.due(POLICIES["ideas"], now)
    if w is None:
        return
    if not await occurrence.claim_once("ideas", w.key, w.scheduled_for):
        return
    try:
        status = await ideas.scan(date.fromisoformat(w.key))
    except BaseException as exc:
        await occurrence.fail("ideas", w.key, explain(exc)
                              if isinstance(exc, Exception) else "cancelled")
        raise
    await occurrence.done("ideas", w.key)
    log.info("trade idea scan for %s: %s", w.key, status)


def _sales_hour() -> int:
    try:
        return min(23, max(0, int(os.getenv("SALES_SHIFT_HOUR", "9"))))
    except ValueError:
        return 9


def _sales_windows(now: datetime, horizon: timedelta) -> list:
    """Daily at SALES_SHIFT_HOUR, New York time, whatever zone `now` is in."""
    from zoneinfo import ZoneInfo

    from .missed import daily
    return daily(lambda: _sales_hour())(now.astimezone(ZoneInfo("America/New_York")),
                                        horizon)


def _content_windows(now: datetime, horizon: timedelta) -> list:
    """Monday 08:00 New York time, whatever zone `now` is in."""
    from zoneinfo import ZoneInfo

    from .missed import weekly
    return weekly(lambda: 0, lambda: 8)(now.astimezone(ZoneInfo("America/New_York")),
                                        horizon)


def _ny_daily(hour: int):  # type: ignore[no-untyped-def]
    """Daily at `hour`, New York time, whatever zone `now` is in."""
    def windows(now: datetime, horizon: timedelta) -> list:
        from zoneinfo import ZoneInfo

        from .missed import daily
        return daily(lambda: hour)(now.astimezone(ZoneInfo("America/New_York")),
                                   horizon)
    return windows


_bizops_digest_windows = _ny_daily(8)
_bizops_repeats_windows = _ny_daily(7)
_seo_rank_windows = _ny_daily(7)
_research_scan_windows = _ny_daily(6)
_inbox_shift_windows = _ny_daily(7)
_sites_shift_windows = _ny_daily(8)
_tutorco_shift_windows = _ny_daily(9)


def _whatsapp_daily_windows(now: datetime, horizon: timedelta) -> list:
    """Daily at WHATSAPP_DAILY_HOUR (default 20), New York time; the hour is read
    at call time so a change needs a restart, not a redeploy."""
    from zoneinfo import ZoneInfo

    from . import whatsapp_daily
    from .missed import daily
    return daily(whatsapp_daily.hour)(now.astimezone(ZoneInfo("America/New_York")),
                                      horizon)
def _sage_tutorco_windows(now: datetime, horizon: timedelta) -> list:
    """Monday 10:00 New York time, whatever zone `now` is in."""
    from zoneinfo import ZoneInfo

    from .missed import weekly
    return weekly(lambda: 0, lambda: 10)(now.astimezone(ZoneInfo("America/New_York")),
                                         horizon)


def _youtube_learn_windows(now: datetime, horizon: timedelta) -> list:
    """Daily at YOUTUBE_LEARN_HOUR (default 11), New York time."""
    from zoneinfo import ZoneInfo

    from . import video_learn
    from .missed import daily
    return daily(video_learn.hour)(now.astimezone(ZoneInfo("America/New_York")),
                                   horizon)


def _research_weekly_windows(now: datetime, horizon: timedelta) -> list:
    """Sunday 18:00 New York time, whatever zone `now` is in."""
    from zoneinfo import ZoneInfo

    from .missed import weekly
    return weekly(lambda: 6, lambda: 18)(now.astimezone(ZoneInfo("America/New_York")),
                                         horizon)


async def _maybe_bizops(job: str, run) -> None:  # type: ignore[no-untyped-def]
    """A BizOps day (modules/bizops): every step an executor call, so
    watch-only holds it. Missed-run policy: skip-stale, claimed once."""
    from zoneinfo import ZoneInfo

    from . import missed, occurrence
    now = _now().astimezone(ZoneInfo("America/New_York"))
    w = await missed.due(POLICIES[job], now)
    if w is None:
        return
    if not await occurrence.claim_once(job, w.key, w.scheduled_for):
        return
    try:
        status = await run()
    except BaseException as exc:
        await occurrence.fail(job, w.key, explain(exc)
                              if isinstance(exc, Exception) else "cancelled")
        raise
    # What the day did, kept on its schedule_runs row (plan 2026-10-30, C2).
    try:
        detail = json.dumps(status, default=str)[:900]
    except (TypeError, ValueError):
        detail = str(status)[:900]
    await occurrence.done(job, w.key, detail=detail)
    log.info("%s %s: %s", job, w.key, status)


async def _maybe_bizops_digest() -> None:
    from ..modules import bizops
    await _maybe_bizops("bizops_digest", bizops.run_daily)


async def _maybe_bizops_find_repeats() -> None:
    from ..modules import bizops
    await _maybe_bizops("bizops_find_repeats", bizops.run_find_repeats)


async def _maybe_seo_rank() -> None:
    from ..modules import seo
    await _maybe_bizops("seo_rank", seo.run_daily)


async def _maybe_inbox_shift() -> None:
    """The Inbox shift (modules/mail/shift.py): 07:00 New York, reads only."""
    from ..modules.mail import shift
    await _maybe_bizops("inbox_shift", shift.run)


async def _maybe_sites_shift() -> None:
    """The Sites shift (modules/agency/sites_shift.py): 08:00 New York. Builds
    only under AGENCY_AUTO_BUILD=on, and raises the publish card; never
    publishes or emails."""
    from ..modules.agency import sites_shift
    await _maybe_bizops("sites_shift", sites_shift.run)


def _shipped(dotted: str) -> bool:
    """True when `atlas.modules.<dotted>` is part of this build (a few business
    modules are left out of the public export; their jobs then do nothing)."""
    from importlib.util import find_spec
    try:
        return find_spec(f"atlas.modules.{dotted}") is not None
    except ModuleNotFoundError:
        return False


async def _maybe_tutorco_shift() -> None:
    """The TUTORCO shift (modules/tutorco/shift.py): 09:00 New York. Discovery and
    drafts only, off unless TUTORCO_DISCOVERY is on; every send is a Tier 2 card.
    Imported lazily inside the claimed run: a missing module is a failed run
    on the schedule_runs row, never a clean one."""
    if not _shipped("tutorco.shift"):
        return

    async def run():  # type: ignore[no-untyped-def]
        from ..modules.tutorco import shift
        return await shift.run()
    await _maybe_bizops("tutorco_shift", run)


async def _maybe_whatsapp_alerts() -> None:
    """A check that went from OK to failing, told once on WhatsApp
    (kernel/whatsapp_alerts.py). Off unless WHATSAPP_ALERTS=on; every send is the
    Tier 1 action `tutorco.notify_alert` through the executor."""
    from . import whatsapp_alerts
    if not whatsapp_alerts.enabled():
        return
    status = await whatsapp_alerts.run()
    if status.get("sent") or status.get("slack_notes"):
        log.info("whatsapp_alerts: %s", status)


async def _maybe_owner_calls() -> None:
    """A serious event nobody answered, escalated to one phone call
    (kernel/owner_calls.py). Off unless OWNER_CALLS=on; every call is the Tier 1
    action `tutorco.call_owner` through the executor."""
    from . import owner_calls
    if not owner_calls.enabled():
        return
    status = await owner_calls.run()
    if status.get("called"):
        log.info("owner_calls: %s", status)


async def _maybe_assistant_notices() -> None:
    """The assistant's short updates to Miro (modules/assistant/notices.py): scan
    for what is worth telling, then send what the caps allow. Every send is the
    Tier 1 action `assistant.update_owner` through the executor."""
    from ..modules.assistant import notices
    await notices.step()


async def _maybe_whatsapp_cards() -> None:
    """Pending Tier 2 cards that never reached WhatsApp, caught up
    (kernel/whatsapp_cards.py). Off unless WHATSAPP_CARDS=on; every send is the
    Tier 1 action `tutorco.notify_card` through the executor."""
    from . import whatsapp_cards
    if not whatsapp_cards.enabled():
        return
    status = await whatsapp_cards.run()
    if status.get("sent") or status.get("slack_notes"):
        log.info("whatsapp_cards: %s", status)


_whatsapp_daily_last_try: float = 0.0
WHATSAPP_DAILY_RETRY_S = 1800.0


async def _maybe_whatsapp_daily() -> None:
    """The 8 PM New York WhatsApp report (kernel/whatsapp_daily.py): one read, one
    Tier 1 send through the executor. Off unless WHATSAPP_DAILY=on.

    Missed-run policy: skip-stale until midnight New York, with retry. A window
    that is not sent (watch-only, box down, send failed) is released and tried
    again every 30 minutes until midnight; it is done only after a send, so at
    most one send per New York date (the outbox dedupes it as well)."""
    global _whatsapp_daily_last_try
    from . import missed, occurrence, whatsapp_daily
    if not whatsapp_daily.enabled():
        return
    if time.monotonic() - _whatsapp_daily_last_try < WHATSAPP_DAILY_RETRY_S:
        return
    from zoneinfo import ZoneInfo
    now = _now().astimezone(ZoneInfo("America/New_York"))
    w = await missed.due(POLICIES["whatsapp_daily"], now)
    if w is None:
        return
    if not await occurrence.claim("whatsapp_daily", w.key, w.scheduled_for):
        return
    _whatsapp_daily_last_try = time.monotonic()
    try:
        status = await whatsapp_daily.run()
    except BaseException as exc:
        await occurrence.release("whatsapp_daily", w.key)
        if not isinstance(exc, Exception):
            raise
        log.exception("whatsapp daily failed; will retry in 30 minutes")
        return
    detail = json.dumps(status, default=str)[:900]
    if status.get("sent") or status.get("uncertain"):
        # Sent, or in doubt (Twilio may have delivered): closed, never retried.
        await occurrence.done("whatsapp_daily", w.key, detail=detail)
        log.info("whatsapp_daily %s: %s", w.key, detail)
    else:
        await occurrence.release("whatsapp_daily", w.key)
        log.info("whatsapp_daily %s not sent, retry in 30 minutes: %s", w.key, detail)
async def _maybe_sage_tutorco_weekly() -> None:
    """Sage's ExampleTutor page week (modules/tutorseo/weekly.py): Monday 10:00
    New York. Drafts only, off unless SAGE_TUTORCO_LINKEDIN is on; every draft and
    the SEO note is a Tier 2 card. Imported lazily inside the claimed run."""
    if not _shipped("tutorseo.weekly"):
        return

    async def run():  # type: ignore[no-untyped-def]
        from ..modules.tutorseo import weekly
        return await weekly.run()
    await _maybe_bizops("sage_tutorco_weekly", run)


async def _maybe_youtube_learn() -> None:
    """ATLAS listens to YouTube videos (kernel/video_learn.py): Miro's queued
    links, then the Academy channel's next public videos, each a Tier 2 lessons
    card. Off unless YOUTUBE_LEARN=on -- checked BEFORE the window is claimed, so
    an off job writes no schedule_runs row -- and held under ATLAS_WATCH_ONLY."""
    from . import video_learn
    from .executor import watch_only
    if not video_learn.enabled() or watch_only():
        return
    await _maybe_bizops("youtube_learn", video_learn.tick)


async def _maybe_research_scan() -> None:
    from ..modules import research
    await _maybe_bizops("research_scan", research.run_scan)


async def _maybe_research_weekly() -> None:
    from ..modules import research
    await _maybe_bizops("research_weekly", research.run_weekly)


async def _maybe_content_weekly() -> None:
    """The Content agent's week (academy/growth.py run_weekly): the growth
    report, the content plan, title test steps, then the alerts.

    Missed-run policy: skip-stale. Every step is an executor call (watch-only
    holds it); a missed Monday is skipped, the next reads afresh."""
    from zoneinfo import ZoneInfo

    from ..modules.academy import growth
    from . import missed, occurrence
    now = _now().astimezone(ZoneInfo("America/New_York"))
    w = await missed.due(POLICIES["content_weekly"], now)
    if w is None:
        return
    if not await occurrence.claim_once("content_weekly", w.key, w.scheduled_for):
        return
    try:
        status = await growth.run_weekly()
    except BaseException as exc:
        await occurrence.fail("content_weekly", w.key, explain(exc)
                              if isinstance(exc, Exception) else "cancelled")
        raise
    await occurrence.done("content_weekly", w.key)
    log.info("content week %s: %s", w.key, status)


async def _maybe_sales_shift() -> None:
    """The Sales shift (modules/sales/shift.py): daily at 09:00 New York.

    Missed-run policy: skip-stale. A shift runs only on its own day; a missed
    day is recorded 'skipped', never run late. Claimed once: every step is an
    executor call (watch-only holds it) and only cards come out of it."""
    from zoneinfo import ZoneInfo

    from ..modules.sales import shift
    from . import missed, occurrence
    now = _now().astimezone(ZoneInfo("America/New_York"))
    w = await missed.due(POLICIES["sales_shift"], now)
    if w is None:
        return
    if not await occurrence.claim_once("sales_shift", w.key, w.scheduled_for):
        return
    try:
        status = await shift.run()
    except BaseException as exc:
        await occurrence.fail("sales_shift", w.key, explain(exc)
                              if isinstance(exc, Exception) else "cancelled")
        raise
    await occurrence.done("sales_shift", w.key)
    log.info("sales shift for %s: %s", w.key, status)


_last_marketing_date: date | None = None
_last_measure_date: date | None = None


def _marketing_day() -> int:
    """Which weekday the marketing plan goes out. Sunday (6) by default —
    after the week it is learning from has finished, before the one it is
    planning starts."""
    try:
        return max(0, min(6, int(os.getenv("MARKETING_DAY", "6"))))
    except ValueError:
        return 6


async def _record_outcome(experiment_id: int, outcome: str, lesson: str, *,
                          observed: float | None = None,
                          baseline: float | None = None,
                          metric: str | None = None):
    """Close a measured experiment through the executor, never by calling
    the action's `run`: `marketing.record_outcome` is a Tier 1 action, so
    it gets the audit line, ATLAS_WATCH_ONLY, the dry run and its hourly
    ceiling like any other (invariant 1). Watch-only leaves the experiment
    open; it is measured again the next day."""
    from .executor import ExecutionRequest, execute
    return await execute(ExecutionRequest(
        action="marketing.record_outcome",
        params={"experiment_id": experiment_id, "outcome": outcome,
                "lesson": lesson, "observed": observed,
                "baseline": baseline, "metric": metric},
        reason=f"experiment #{experiment_id} measured after its 48 hours"))


async def _maybe_marketing() -> None:
    """Two cadences in one step, because they are one loop.

    Daily: `experiments.measure_due()` reads every experiment whose 48 hours
    are up and closes it with a measured outcome — or with `unmeasured` and
    the reason, which is the whole point of measuring on a schedule rather
    than when someone remembers. It closes each through the executor
    (`_record_outcome`), never by calling the action itself.

    Weekly: next week's plan, on MARKETING_DAY at or after BRIEF_HOUR.
    Missed-run policy: run-once. The window is the MARKETING_DAY date,
    claimed once in `schedule_runs`, so a restart the Monday after a missed
    Sunday files it once (process memory could not see that before), and
    never twice: a filing cut short counts as filed, since its requests may
    already have gone out. Every item goes through the executor; a refusal
    (the hourly ceiling, watch-only, a bad parameter) is logged at WARNING
    and counted into `cycle.record_filing`, which the brief's Marketing
    section reads.
    """
    global _last_marketing_date, _last_measure_date
    now = _now()

    if _last_measure_date != now.date():
        _last_measure_date = now.date()
        from .marketing.experiments import measure_due
        try:
            for result in await measure_due(record=_record_outcome):
                log.info("marketing: experiment #%s %s",
                         result["experiment_id"], result["outcome"])
        except Exception:
            log.exception("measuring due experiments failed")

    if now.hour < _brief_hour() or _last_marketing_date == now.date():
        return
    global _marketing_task
    if _marketing_task is not None and not _marketing_task.done():
        log.info("the weekly marketing plan is still being composed")
        return
    w = await _marketing_window(now)
    if w is None:
        return
    _last_marketing_date = now.date()
    from . import occurrence
    if not await occurrence.claim_once("marketing", w.key, w.scheduled_for):
        return
    from .executor import ExecutionRequest, execute
    from .marketing.cycle import as_requests, plan_all, record_filing

    async def file_the_week() -> None:
        try:
            await _file_the_week()
        except Exception as exc:  # noqa: BLE001
            # Already logged where it happened; the week stays settled (a
            # partial filing may have gone out), with the reason on the row.
            await occurrence.fail("marketing", w.key, explain(exc))
            return
        except asyncio.CancelledError:
            await occurrence.fail("marketing", w.key, "cut short by a shutdown")
            raise
        await occurrence.done("marketing", w.key)

    async def _file_the_week() -> None:
        try:
            plans = await plan_all()
        except Exception:
            log.exception("the weekly marketing plan failed")
            raise
        # The planner returns requests; the executor runs them. That keeps
        # the single path, and it keeps ATLAS_WATCH_ONLY meaning what it says
        # for the marketing loop as much as for anything else.
        filed, refused, reasons = 0, 0, []
        try:
            for plan in plans:
                for action, params, reason in as_requests(plan):
                    try:
                        result = await execute(ExecutionRequest(
                            action=action, params=params, reason=reason))
                    except Exception as exc:
                        # One request that raised is one not filed, not the
                        # end of the week's filing.
                        from ..modules._http import explain
                        refused += 1
                        reasons.append(f"{action} raised: {explain(exc)}")
                        log.exception("marketing: %s raised", action)
                        continue
                    if result.ok:
                        filed += 1
                        continue
                    refused += 1
                    reasons.append(result.detail)
                    log.warning("marketing: %s not filed — %s", action,
                                result.detail)
        except asyncio.CancelledError:
            # A shutdown mid-filing: record what landed and say it was cut
            # short, rather than leave the week reading as never filed.
            reasons.append("cut short by a shutdown before the plan was "
                           "fully filed")
            record_filing(filed=filed, refused=refused + 1, reasons=reasons,
                          at=datetime.now(UTC))
            raise
        record_filing(filed=filed, refused=refused, reasons=reasons,
                      at=datetime.now(UTC))

    # Off the loop: composing the plan now includes Perplexity's deep
    # research per business (up to `monetize.BACKGROUND_TIMEOUT` each), and
    # while a step is awaited here no check runs at all — the same stall
    # `_run_checks` already moved `handle()` out of the loop to avoid.
    _marketing_task = background.spawn(file_the_week(), "weekly marketing plan")


async def _marketing_window(now: datetime):
    """Which weekly-plan window (if any) is due now: the missed-run policy's
    answer (kernel.missed). A seam for tests."""
    from . import missed
    return await missed.due(POLICIES["marketing"], now)


_marketing_task: asyncio.Task | None = None

_last_reels_date: date | None = None
_last_nudge_date: date | None = None
_reels_task: asyncio.Task | None = None


_calendar_task: asyncio.Task | None = None
_last_calendar_at: datetime | None = None


async def _maybe_calendar() -> None:
    """The 30-day calendar's tick (`kernel.reels.calendar_tick`) every
    `CALENDAR_TICK_MIN` minutes, off the loop: banking a day's clips is
    minutes of Veo per clip. A Veo 429 stops a tick; the next one resumes
    where it stopped, so a quota bump costs a wait, never a day."""
    global _calendar_task, _last_calendar_at
    from . import reels
    now = _now()
    if _calendar_task is not None and not _calendar_task.done():
        return
    if (_last_calendar_at is not None
            and now - _last_calendar_at < timedelta(minutes=reels.CALENDAR_TICK_MIN)):
        return
    _last_calendar_at = now
    _calendar_task = background.spawn(reels.calendar_tick(), "the reels calendar")


_clips_task: asyncio.Task | None = None
_last_clips_at: datetime | None = None


async def _maybe_clips() -> None:
    """The clips lane's intake (`kernel.clip_intake.tick`) every
    `CLIPS_INTAKE_MIN` minutes, off the loop: a cut is minutes of
    transcription and ffmpeg. Registers new official footage, cuts it and
    posts one waiting clip per platform, through the executor (Tier 1,
    gated in code; a held clip is a Tier 2 card with the reason).
    `CLIPS_INTAKE=off` stops it."""
    global _clips_task, _last_clips_at
    from ..modules.clips import intake
    if not intake.enabled():
        return
    now = _now()
    if _clips_task is not None and not _clips_task.done():
        return
    if _last_clips_at is not None and now - _last_clips_at < intake.interval():
        return
    _last_clips_at = now
    from . import clip_intake
    _clips_task = background.spawn(clip_intake.tick(), "the clips intake")


_academy_reel_task: asyncio.Task | None = None
_academy_reel_slots: set[tuple] = set()


async def _maybe_academy_reel() -> None:
    """The Academy reel for the clips accounts (`modules.academy.reel.daily`)
    at each of `ACADEMY_PROMO_HOURS_UTC` (16 and 23 UTC: noon and 7 pm in
    New York; Miro, 2026-10-11, "2x per day"), off the loop. It
    renders through the executor and posts through
    `clips.autopublish_promo` (Tier 1, gated in code; a held reel is a
    card). `ACADEMY_PROMO_REELS=off` stops it."""
    global _academy_reel_task
    from ..modules.academy import reel
    if not reel.enabled():
        return
    now = _now()
    slot = reel.due_slot(now, _academy_reel_slots)
    if slot is None:
        return
    if _academy_reel_task is not None and not _academy_reel_task.done():
        return
    # Every slot that has come today is spent by this run: a box that was
    # down through two slots posts once when it is back, not twice in a row.
    _academy_reel_slots.update((now.date(), h) for h in reel.hours_utc() if h <= now.hour)
    _academy_reel_task = background.spawn(reel.daily(), "the daily Academy reel")


_academy_tutorial_task: asyncio.Task | None = None
_last_academy_tutorial_date: date | None = None


async def _maybe_academy_tutorial() -> None:
    """The next ATLAS Academy YouTube tutorial (`modules.academy.tutorials.
    daily`), once a day from `ACADEMY_TUTORIALS_HOUR_UTC`, off the loop:
    Fish calls, Chromium stills and ffmpeg. One draft per series per day,
    each series' next rung in order. It renders and posts through the executor
    (`academy.render_tutorial`, `academy.autopublish_tutorial`, Tier 1 gated
    in code; a held episode is a card). What the hourly ceiling held that run
    is tried again (`tutorials.catch_up`) once `tutorials.retry_due`, the same
    UTC day, one run at a time (2026-10-16). `ACADEMY_TUTORIALS=off` stops it."""
    global _academy_tutorial_task, _last_academy_tutorial_date
    from ..modules.academy import tutorials
    if not tutorials.enabled():
        return
    now = _now()
    if now.hour < tutorials.hour_utc():
        return
    if _academy_tutorial_task is not None and not _academy_tutorial_task.done():
        return
    if _last_academy_tutorial_date != now.date():
        _last_academy_tutorial_date = now.date()
        _academy_tutorial_task = background.spawn(tutorials.daily(),
                                                  "the daily Academy tutorial")
        return
    if tutorials.retry_due(now):
        _academy_tutorial_task = background.spawn(
            tutorials.catch_up(), "the Academy tutorials the hourly ceiling held")


_academy_teaching_task: asyncio.Task | None = None
_last_academy_teaching_date: date | None = None


async def _maybe_academy_teaching() -> None:
    """The Academy teaching Short (`modules.academy.tutorials.teaching_tick`),
    once a day from `ACADEMY_TEACHING_HOUR_UTC` (23), off the loop. It posts
    only on alternate New York days (`tutorials.teaching_day`), so the 2 a day
    cap (Miro, 2026-10-13: two Shorts a day) is shared with the
    engine's 16 and 23 UTC Shorts. It renders and posts through the executor
    (`academy.render_teaching_short`, `clips.autopublish_promo`, Tier 1 gated
    in code). Every day it also runs the AI-flag catch-up
    (`academy.flag_synthetic_short`, Miro 2026-10-17) over the last 14 days,
    and the metadata refresh of already-public videos
    (`academy.refresh_video_metadata`, Miro 2026-10-18; 3 a day, each once).
    `ACADEMY_TUTORIALS=off` stops it."""
    global _academy_teaching_task, _last_academy_teaching_date
    from ..modules.academy import tutorials
    if not tutorials.enabled():
        return
    now = _now()
    if (now.hour < tutorials.teaching_hour_utc()
            or _last_academy_teaching_date == now.date()):
        return
    if _academy_teaching_task is not None and not _academy_teaching_task.done():
        return
    _last_academy_teaching_date = now.date()
    _academy_teaching_task = background.spawn(tutorials.teaching_tick(),
                                              "the Academy teaching Short")


_academy_videos_task: asyncio.Task | None = None
_last_academy_videos_at: datetime | None = None


async def _maybe_academy_videos() -> None:
    """Keep agency.storefront.test/videos/ current: every
    ACADEMY_VIDEOS_REFRESH_MIN (default 15) compare the public tutorials
    with the live videos.json and, only on a difference, republish the store
    through the executor (`academy.publish_shop`, Tier 1 with its staged
    release and rollback). `ACADEMY_VIDEOS_REFRESH=off` stops it."""
    global _academy_videos_task, _last_academy_videos_at
    if os.getenv("ACADEMY_VIDEOS_REFRESH", "on").strip().lower() in ("0", "off", "false", "no"):
        return
    now = _now()
    every = timedelta(minutes=max(5, int(os.getenv("ACADEMY_VIDEOS_REFRESH_MIN", "15") or 15)))
    if _academy_videos_task is not None and not _academy_videos_task.done():
        return
    if _last_academy_videos_at is not None and now - _last_academy_videos_at < every:
        return
    _last_academy_videos_at = now
    from ..modules.academy import publish
    _academy_videos_task = background.spawn(publish.refresh_videos(),
                                            "the Free Lessons page refresh")


_community_task: asyncio.Task | None = None


async def _maybe_community() -> None:
    """The AMSM channel's daily shift (`modules.community.shift`), off the loop:
    once a New York day from COMMUNITY_SHIFT_HOUR it reads the synced calendar,
    records gaps and feasts, and raises at most one Tier 2 batch card per
    platform. `COMMUNITY_SHIFT=off` stops it; under ATLAS_WATCH_ONLY its cards
    are still raised and their runs are refused by the executor."""
    global _community_task
    if not _shipped("community.shift"):
        return
    from ..modules.community import shift
    if not shift.enabled():
        return
    if _community_task is not None and not _community_task.done():
        return
    _community_task = background.spawn(shift.run_if_due(), "the AMSM channel shift")


_clip_links_task: asyncio.Task | None = None
_last_clip_links_at: datetime | None = None


async def _maybe_clip_links() -> None:
    """The clips lane's link pass (`modules.clips.links.reconcile`) every
    `CLIPS_LINKS_MIN` minutes, off the loop: reads Postiz and fills in the
    post URL of a clip it posted without one, on ATLAS's own row only.
    `CLIPS_LINKS=off` stops it."""
    global _clip_links_task, _last_clip_links_at
    from ..modules.clips import links
    if not links.enabled():
        return
    now = _now()
    if _clip_links_task is not None and not _clip_links_task.done():
        return
    if _last_clip_links_at is not None and now - _last_clip_links_at < links.interval():
        return
    _last_clip_links_at = now
    _clip_links_task = background.spawn(links.reconcile(), "the clips link pass")


_market_alerts_task: asyncio.Task | None = None
_last_market_alerts_at: datetime | None = None


async def _maybe_market_alerts() -> None:
    """Ivan's alerts (`kernel.market_alerts.tick`) every `MARKETS_ALERT_MIN`
    minutes, off the loop: plans events from the markets readings and sends
    each once, through the executor, under a daily cap. `MARKETS_ALERTS=off`
    stops it."""
    global _market_alerts_task, _last_market_alerts_at
    from . import market_alerts
    if not market_alerts.enabled():
        return
    now = _now()
    if _market_alerts_task is not None and not _market_alerts_task.done():
        return
    if (_last_market_alerts_at is not None
            and now - _last_market_alerts_at < market_alerts.interval()):
        return
    _last_market_alerts_at = now
    _market_alerts_task = background.spawn(market_alerts.tick(), "the market alerts")


_funnels_task: asyncio.Task | None = None
_last_funnels_at: datetime | None = None
FUNNELS_TICK = timedelta(minutes=5)


async def _maybe_funnels() -> None:
    """The Instagram comment-keyword funnels (`modules.instagram.funnel.tick`)
    every five minutes, off the loop: read new comments, queue the keyword
    matches, send what is due -- each send `instagram.funnel_reply` through
    the executor, none in quiet hours, none past the daily cap. `FUNNELS=off`
    stops it. Missed-run policy skip-stale: a tick is an interval, nothing
    is replayed; a comment missed is read by the next sweep from its
    watermark."""
    global _funnels_task, _last_funnels_at
    if os.getenv("FUNNELS", "on").strip().lower() in ("0", "off", "false", "no"):
        return
    now = _now()
    if _funnels_task is not None and not _funnels_task.done():
        return
    if _last_funnels_at is not None and now - _last_funnels_at < FUNNELS_TICK:
        return
    _last_funnels_at = now
    from . import funnels
    _funnels_task = background.spawn(funnels.tick(), "the comment funnels")


_missions_task: asyncio.Task | None = None
_last_missions_at: datetime | None = None


async def _maybe_missions() -> None:
    """Jobs handed to an agent (`kernel.missions.tick`) every
    `MISSIONS_TICK_MIN` minutes, off the loop: each live mission advances a
    few steps, Tier 1 through the executor, Tier 2 as cards, paced under the
    module's hourly ceiling. `MISSIONS=off` stops it."""
    global _missions_task, _last_missions_at
    from . import missions
    if not missions.enabled():
        return
    now = _now()
    if _missions_task is not None and not _missions_task.done():
        return
    if _last_missions_at is not None and now - _last_missions_at < missions.interval():
        return
    _last_missions_at = now
    _missions_task = background.spawn(missions.tick(), "the missions")


_lead_replies_task: asyncio.Task | None = None
_last_lead_replies_at: datetime | None = None


async def _maybe_lead_replies() -> None:
    """Replies owed to agency leads (`kernel.lead_followup.tick`) every
    `AGENCY_REPLY_TICK_MIN` minutes, off the loop: a draft is a model call.
    Tier 1 drafts through the executor, then a Tier 2 send card."""
    global _lead_replies_task, _last_lead_replies_at
    from . import lead_followup
    now = _now()
    if _lead_replies_task is not None and not _lead_replies_task.done():
        return
    if (_last_lead_replies_at is not None
            and now - _last_lead_replies_at < lead_followup.interval()):
        return
    _last_lead_replies_at = now
    _lead_replies_task = background.spawn(lead_followup.tick(), "the lead replies")


_linkedin_replies_task: asyncio.Task | None = None
_last_linkedin_replies_at: datetime | None = None


async def _maybe_linkedin_replies() -> None:
    """Replies owed to people who wrote to the Guardian or FlyCheap LinkedIn
    page (`kernel.linkedin_replies.tick`) every `LINKEDIN_REPLY_TICK_MIN`
    minutes, off the loop: a draft is a model call and a send holds the
    LinkedIn browser for a minute."""
    global _linkedin_replies_task, _last_linkedin_replies_at
    from . import linkedin_replies
    now = _now()
    if _linkedin_replies_task is not None and not _linkedin_replies_task.done():
        return
    if (_last_linkedin_replies_at is not None
            and now - _last_linkedin_replies_at < linkedin_replies.interval()):
        return
    _last_linkedin_replies_at = now
    _linkedin_replies_task = background.spawn(linkedin_replies.tick(),
                                              "the LinkedIn page replies")


_linkedin_autopost_task: asyncio.Task | None = None
_last_linkedin_autopost_at: datetime | None = None


async def _maybe_linkedin_autopost() -> None:
    """The Vault LinkedIn page's approved post, published on its own
    (`kernel.linkedin_autopost.tick`, Miro 2026-10-11) every
    `LINKEDIN_AUTOPUBLISH_TICK_MIN` minutes, off the loop: a post holds the
    LinkedIn browser for minutes."""
    global _linkedin_autopost_task, _last_linkedin_autopost_at
    from . import linkedin_autopost
    now = _now()
    if _linkedin_autopost_task is not None and not _linkedin_autopost_task.done():
        return
    if (_last_linkedin_autopost_at is not None
            and now - _last_linkedin_autopost_at < linkedin_autopost.interval()):
        return
    _last_linkedin_autopost_at = now
    _linkedin_autopost_task = background.spawn(linkedin_autopost.tick(),
                                               "the Vault LinkedIn post")


_academy_support_task: asyncio.Task | None = None
_last_academy_support_at: datetime | None = None


async def _maybe_academy_support() -> None:
    """ATLAS Academy support (`kernel.academy_support.tick`) every
    `ACADEMY_SUPPORT_TICK_MIN` minutes, off the loop: it reads the support
    mailbox and a draft is a model call."""
    global _academy_support_task, _last_academy_support_at
    from . import academy_support
    now = _now()
    if _academy_support_task is not None and not _academy_support_task.done():
        return
    if (_last_academy_support_at is not None
            and now - _last_academy_support_at < academy_support.interval()):
        return
    _last_academy_support_at = now
    _academy_support_task = background.spawn(academy_support.tick(),
                                              "the Academy support replies")


_draft_notice_task: asyncio.Task | None = None
_last_draft_notice_at: datetime | None = None


async def _maybe_draft_notice() -> None:
    """New content drafts -> one Slack card each (`kernel.draft_notice.tick`)."""
    global _draft_notice_task, _last_draft_notice_at
    from . import draft_notice
    now = _now()
    if _draft_notice_task is not None and not _draft_notice_task.done():
        return
    if (_last_draft_notice_at is not None
            and now - _last_draft_notice_at < draft_notice.interval()):
        return
    _last_draft_notice_at = now
    _draft_notice_task = background.spawn(draft_notice.tick(), "the draft notices")


_academy_subscribe_task: asyncio.Task | None = None
_last_academy_subscribe_at: datetime | None = None


async def _maybe_academy_subscribe() -> None:
    """Queued storefront signup confirmations (`kernel.academy_subscribe.tick`)
    every five minutes, off the loop: it may send mail."""
    global _academy_subscribe_task, _last_academy_subscribe_at
    from . import academy_subscribe
    now = _now()
    if _academy_subscribe_task is not None and not _academy_subscribe_task.done():
        return
    if (_last_academy_subscribe_at is not None
            and now - _last_academy_subscribe_at < academy_subscribe.interval()):
        return
    _last_academy_subscribe_at = now
    _academy_subscribe_task = background.spawn(academy_subscribe.tick(),
                                               "the Academy signup confirmations")


_agency_contact_task: asyncio.Task | None = None
_last_agency_contact_at: datetime | None = None


async def _maybe_agency_contact() -> None:
    """Website contact messages (`kernel.agency_contact.tick`) every minute:
    each waiting channel goes through `agency.deliver_contact`, then Twilio is
    asked what became of the WhatsApp messages it accepted."""
    global _agency_contact_task, _last_agency_contact_at
    from . import agency_contact
    now = _now()
    if _agency_contact_task is not None and not _agency_contact_task.done():
        return
    if (_last_agency_contact_at is not None
            and now - _last_agency_contact_at < agency_contact.interval()):
        return
    _last_agency_contact_at = now
    _agency_contact_task = background.spawn(agency_contact.tick(),
                                            "the website contact messages")


_qa_runs_task: asyncio.Task | None = None
_last_qa_runs_at: datetime | None = None


def _qa_runs_interval() -> timedelta:
    try:
        minutes = int(os.getenv("QA_VERIFY_RUNS_MIN", "60"))
    except ValueError:
        minutes = 60
    return timedelta(minutes=max(15, minutes))


async def _qa_verify_runs() -> None:
    from .executor import ExecutionRequest, execute
    await execute(ExecutionRequest(
        action="qa.verify_runs", params={},
        reason="hourly: every scheduled window of the last day ran"))


_qa_autopilot_task: asyncio.Task | None = None
_last_qa_autopilot_at: datetime | None = None


def _qa_autopilot_interval() -> timedelta:
    try:
        minutes = int(os.getenv("QA_AUTOPILOT_MIN", "15"))
    except ValueError:
        minutes = 15
    return timedelta(minutes=max(5, minutes))


async def _qa_autopilot() -> None:
    from .executor import ExecutionRequest, execute
    await execute(ExecutionRequest(
        action="qa.autopilot_cycle", params={},
        reason="every cycle: diagnose what is not OK and fix what a Tier 1 action can"))


async def _maybe_qa_autopilot() -> None:
    """The QA autopilot cycle through the executor, off the loop. It fixes only
    by a registered Tier 1 action, asks once otherwise, and never retries."""
    global _qa_autopilot_task, _last_qa_autopilot_at
    now = _now()
    if _qa_autopilot_task is not None and not _qa_autopilot_task.done():
        return
    if (_last_qa_autopilot_at is not None
            and now - _last_qa_autopilot_at < _qa_autopilot_interval()):
        return
    _last_qa_autopilot_at = now
    _qa_autopilot_task = background.spawn(_qa_autopilot(), "QA autopilot cycle")


async def _maybe_qa_verify_runs() -> None:
    """QA's hourly look at the scheduled windows (`qa.verify_runs`) through
    the executor, off the loop. A miss becomes a remediation task; nothing
    is retried."""
    global _qa_runs_task, _last_qa_runs_at
    now = _now()
    if _qa_runs_task is not None and not _qa_runs_task.done():
        return
    if _last_qa_runs_at is not None and now - _last_qa_runs_at < _qa_runs_interval():
        return
    _last_qa_runs_at = now
    _qa_runs_task = background.spawn(_qa_verify_runs(), "QA's check of missed runs")


async def _maybe_reels() -> None:
    """The AI-stories account's day (`kernel.reels.daily`), once a day at or
    after BRIEF_HOUR, off the loop like the marketing pass: it may wait on
    Perplexity (bounded inside by its own deadline) and on renders. The
    process-memory date only saves a query per tick; `reels.daily` itself
    checks `reports` so a restart does not run the day twice."""
    global _last_reels_date, _last_nudge_date, _reels_task
    now = _now()
    from . import reels
    # The 17:00 nudge: its own once-a-day, stored in `reports` too.
    if now.hour >= reels.nudge_hour() and _last_nudge_date != now.date():
        _last_nudge_date = now.date()
        background.spawn(reels.nudge(now), "the reels nudge")
    if now.hour < _brief_hour() or _last_reels_date == now.date():
        return
    if _reels_task is not None and not _reels_task.done():
        return
    _last_reels_date = now.date()
    _reels_task = background.spawn(reels.daily(now), "the AI-stories day")


# ------------------------------------------------------ missed-run policies
#
# RA-F2: every recurring step states what happens to the windows it missed
# while ATLAS was down (kernel.missed has the three policies). Jobs with
# windows record each one in `schedule_runs` -- run, failed or skipped with
# the reason. Interval jobs have no windows: their clock starts at zero in a
# new process, so the first tick after an outage runs once and nothing is
# replayed. `tests/test_missed_runs.py` holds every loop step to having an
# entry here.
def _playlist_day() -> int:
    from ..modules.markets.playlist import propose_day
    return propose_day()


def _playlist_hour() -> int:
    from ..modules.markets.playlist import propose_hour
    return propose_hour()


def _idea_windows(now: datetime, horizon: timedelta) -> list:
    from ..modules.markets.ideas import TZ, windows
    return windows(now.astimezone(TZ), horizon)     # 16:30 New York, any `now`


def _playlist_windows(now: datetime, horizon: timedelta) -> list:
    from ..modules.markets.playlist import TZ
    from .missed import weekly
    return weekly(lambda: _playlist_day(), lambda: _playlist_hour())(
        now.astimezone(TZ), horizon)


def _youtube_learn_on() -> bool:
    from . import video_learn
    from .executor import watch_only
    return video_learn.enabled() and not watch_only()


def _policies() -> dict:
    from .missed import JobPolicy, Missed, daily, iso_week, weekly
    once, stale = Missed.RUN_ONCE, Missed.SKIP_STALE
    # Looked up at call time (not bound here) so an env change or a test's
    # patched hour moves the windows and the gate together.
    eight_weeks = timedelta(weeks=8)
    table = [
        JobPolicy("brief", stale, retry=True, windows=daily(lambda: _brief_hour()),
                  enabled=_daily_brief_on,
                  why="a morning brief is only worth reading that day; a "
                      "failed send is retried until local midnight"),
        JobPolicy("agency_meeting", stale, retry=True,
                  windows=daily(lambda: _brief_hour()),
                  why="today's meeting, built from today's record; a missed "
                      "day is never built late, a failed build or store is "
                      "retried until local midnight"),
        JobPolicy("agency_lead_board", stale, retry=True,
                  windows=daily(lambda: _brief_hour()),
                  why="today's lead counts, read when posted; a missed day is "
                      "never posted late, a failed post is retried until "
                      "local midnight"),
        JobPolicy("review", stale, retry=True, windows=daily(lambda: _review_hour()),
                  why="the day's review, that evening; a missed day is not "
                      "reviewed the next morning"),
        JobPolicy("weekly", once, retry=True, horizon=eight_weeks,
                  windows=weekly(lambda: _weekly_day(),
                                 lambda: _brief_hour(), key=iso_week),
                  why="one summary on recovery, never one per missed week; a "
                      "failed send is retried after a restart (weekly.send "
                      "refuses a second one per ISO week from `reports`)"),
        JobPolicy("marketing", once, horizon=eight_weeks,
                  windows=weekly(lambda: _marketing_day(),
                                 lambda: _brief_hour()),
                  why="one plan for the coming week; a partial filing is "
                      "never filed again"),
        JobPolicy("playlist", once, horizon=eight_weeks,
                  windows=_playlist_windows,
                  why="one paper-playlist proposal on recovery, never one per "
                      "missed week; claimed once, so a card is raised at most "
                      "once a week (New York time)"),
        JobPolicy("funnels", stale, guard="interval (process memory)",
                  why="every 5 minutes; a missed tick is skipped, never replayed "
                      "-- the next sweep reads from each media's comment "
                      "watermark, and every send is keyed once per commenter"),
        JobPolicy("sales_shift", stale, windows=_sales_windows,
                  why="the Sales shift is that day's cards; a missed day is "
                      "skipped, never run late (New York time)"),
        JobPolicy("inbox_shift", stale, windows=_inbox_shift_windows,
                  why="the Inbox morning read; a missed day is skipped, the next "
                      "reads the mailbox afresh (New York time)"),
        JobPolicy("sites_shift", stale, windows=_sites_shift_windows,
                  why="the Sites morning: up to three demos and their publish "
                      "cards; a missed day is skipped, never run late"),
        JobPolicy("tutorco_shift", stale, windows=_tutorco_shift_windows,
                  why="the ExampleTutor morning: discovery, scores and intro "
                      "drafts as cards; a missed day is skipped, never run late"),
        JobPolicy("whatsapp_daily", stale, windows=_whatsapp_daily_windows,
                  retry=True,
                  why="the 8 PM agent report on WhatsApp, once per New York date; "
                      "unsent (watch-only, down, failed) it retries every 30 min "
                      "until midnight, then is skipped, never sent for a past day"),
        JobPolicy("sage_tutorco_weekly", stale, windows=_sage_tutorco_windows,
                  why="Sage's ExampleTutor page week: draft posts and one SEO "
                      "note as cards; a missed week is skipped, never run late"),
        JobPolicy("youtube_learn", stale, windows=_youtube_learn_windows,
                  enabled=_youtube_learn_on,
                  why="listens to queued and Academy YouTube videos and raises "
                      "lessons cards; a missed day is skipped, the next pass "
                      "takes what is still queued"),
        JobPolicy("bizops_digest", stale, windows=_bizops_digest_windows,
                  why="the BizOps morning: onboardings, reminders and the "
                      "digest; a missed day is skipped, the next reads afresh"),
        JobPolicy("bizops_find_repeats", stale, windows=_bizops_repeats_windows,
                  why="a 14-day scan; a missed day is skipped, the next one "
                      "covers it"),
        JobPolicy("seo_rank", stale, windows=_seo_rank_windows,
                  why="a rank snapshot (audits on Mondays); a missed day is "
                      "skipped, the next reads afresh"),
        JobPolicy("research_scan", stale, windows=_research_scan_windows,
                  why="a read of every watched page; a missed day is skipped, the "
                      "next compares against the last read"),
        JobPolicy("research_weekly", stale, windows=_research_weekly_windows,
                  why="the Sunday profiles and report; a missed week is skipped, "
                      "the next reads afresh"),
        JobPolicy("content_weekly", stale, windows=_content_windows,
                  why="the Content agent's Monday; a missed week is skipped, "
                      "never run late -- the next one reads everything afresh"),
        JobPolicy("ideas", stale, windows=_idea_windows,
                  why="a trade idea is only worth that day's close; a missed "
                      "weekday is skipped, never scanned late (New York time)"),
        JobPolicy("judgment", once, windows=daily(lambda: _tournament_hour()),
                  why="the nightly retrain uses all data since the last one; "
                      "one run covers every missed night"),
        # Kept once-only by their own records, not schedule_runs.
        JobPolicy("reels", stale, guard="reports (reels digest)",
                  why="the stories account's day; `reels.daily` refuses a "
                      "second digest for the same local date and never "
                      "composes a missed one"),
        JobPolicy("reels_nudge", stale, guard="reports (reels nudge)",
                  why="the 17:00 nudge, today only; a reminder is never "
                      "sent for a past day"),
        JobPolicy("agent_shifts", once, guard="agent_runs (agents.next_shift_at)",
                  why="a shift due days ago runs once; the next is counted "
                      "from that run, so missed days are not replayed"),
        JobPolicy("calendar", once, guard="reel_calendar + the daily post cap",
                  why="every 30 min; after an outage its backlog drains at "
                      "the daily cap and one card a day, never all at once"),
        JobPolicy("community", stale, guard="community_events (one shift a New York day)",
                  why="the AMSM channel's day; a missed day is not run late -- "
                      "the next shift reads the calendar afresh, and each card "
                      "is keyed per platform per day"),
        JobPolicy("measure", once, guard="experiments (measure_due)",
                  why="daily sweep closing every experiment already due; "
                      "one pass covers any number of missed days"),
    ]
    for name, why in (
            ("checks", "every cycle; observing twice is harmless"),
            ("approvals", "expiry sweep; one pass settles every lapsed card"),
            ("approvals_board", ("redraws the one #approvals board when the pending "
                                 "set changed, or every APPROVALS_BOARD_REFRESH_MIN; "
                                 "a missed redraw is the next one, so nothing is "
                                 "replayed")),
            ("signals", "every SIGNAL_INTERVAL_S; one pass after recovery"),
            ("rhythm", "hourly rebuild of a rolling baseline"),
            ("retention", "hourly purge; one pass removes all that is due"),
            ("fixes", "every FIX_SWEEP_HOURS; one sweep of lasting failures"),
            ("outbox", "dispatches whatever is due, each row once"),
            ("clips", ("every CLIPS_INTAKE_MIN; registration and cuts are "
                       "keyed by sha256, so one pass after recovery catches "
                       "up within the daily cap")),
            ("clip_links", ("every CLIPS_LINKS_MIN; reads Postiz and fills a "
                            "clip's link only where it is still empty, so one "
                            "pass after recovery catches up within 14 days")),
            ("academy_reel", ("at each ACADEMY_PROMO_HOURS_UTC slot (16, 23); "
                              "skip-stale: a box down through a slot posts once "
                              "when it is back the same day, and academy_promo_posts "
                              "keeps three hours between posts and caps the day at "
                              "CLIPS_PROMO_PER_DAY, so nothing doubles")),
            ("academy_tutorial", ("once a day from ACADEMY_TUTORIALS_HOUR_UTC, then "
                                  "hourly the same day for what the hourly ceiling "
                                  "held; skip-stale: academy_tutorial_posts caps it "
                                  "per day and keys each episode, so nothing posts "
                                  "twice")),
            ("academy_teaching", ("once a day from ACADEMY_TEACHING_HOUR_UTC, on "
                                  "alternate New York days; skip-stale: "
                                  "academy_promo_posts caps the day at "
                                  "CLIPS_PROMO_PER_DAY and keys each cut by its slug, "
                                  "so nothing posts twice")),
            ("academy_videos", ("every ACADEMY_VIDEOS_REFRESH_MIN; compares the "
                                "public tutorials with the live page and republishes "
                                "only on a difference, so a missed run catches up")),
            ("lead_replies", ("every AGENCY_REPLY_TICK_MIN; each queued lead "
                              "message is claimed once, and one queued over a "
                              "day ago is left to Miro, so nothing is replayed")),
            ("linkedin_replies", ("every LINKEDIN_REPLY_TICK_MIN; each page request "
                                  "is claimed once and answered once per inbound, and "
                                  "one over three days old is left to Miro, so "
                                  "nothing is replayed")),
            ("linkedin_autopost", ("every LINKEDIN_AUTOPUBLISH_TICK_MIN; the page's "
                                   "gate allows one post a day and marks it "
                                   "published (or posted_unverified) before the "
                                   "next tick, so a missed run posts later the "
                                   "same day and nothing posts twice")),
            ("academy_support", ("every ACADEMY_SUPPORT_TICK_MIN; each ticket is "
                                 "claimed once, keyed by its form id or Gmail id, "
                                 "answered once per inbound, and one over seven days "
                                 "old is left to Miro, so nothing is replayed")),
            ("agency_contact", ("every minute; each waiting message channel is "
                                "claimed once and delivered once, a failed channel "
                                "retries up to 3 times, and the row is stored before "
                                "anything is sent, so a missed run only delays "
                                "delivery and no message is lost")),
            ("academy_subscribe", ("every 5 minutes; each queued confirmation is "
                                   "claimed once and sent once, a pending signup "
                                   "unconfirmed after 7 days is deleted, so a missed "
                                   "run only delays the mail and nothing is replayed")),
            ("qa_autopilot", ("every QA_AUTOPILOT_MIN (15); a missed cycle is covered "
                              "by the next, every cause is capped per day and "
                              "escalated once, so nothing is replayed")),
            ("qa_verify_runs", ("every QA_VERIFY_RUNS_MIN (60); it only reads "
                                "schedule_runs and raises deduped tasks, so a "
                                "missed pass is covered by the next one")),
            ("missions", ("every MISSIONS_TICK_MIN; each mission works its "
                          "next step out from the agency tables, so one pass "
                          "after recovery resumes it, and one past its "
                          "deadline lapses with a report")),
            ("market_alerts", ("every MARKETS_ALERT_MIN; each event is keyed "
                               "in markets_alerts and sent once, under the "
                               "daily cap; one missed a day expires unsent")),
            ("whatsapp_cards", ("every pass; a pending, unexpired Tier 2 card that "
                                "was never messaged is sent once (claim per card, "
                                "shared with the at-creation copy), newest 5 only on "
                                "the first run, so a missed pass is covered by the "
                                "next and nothing is replayed")),
            ("whatsapp_alerts", ("every pass; only a failing reading that was OK "
                                 "just before it and is under 30 minutes old counts, "
                                 "each reading is sent once (outbox slot), so a "
                                 "missed pass is covered by the next and nothing "
                                 "is replayed")),
            ("draft_notice", ("every 5 minutes; a high-water mark in "
                              "draft_notice_mark means each new content draft gets "
                              "one Slack card, so a missed pass is covered by the "
                              "next and nothing is replayed")),
            ("assistant_notices", ("every pass; off unless the assistant is on; "
                                   "rows are deduped per thing and capped at 1 an "
                                   "hour and 3 a day, so a missed pass is covered by "
                                   "the next and nothing is replayed")),
            ("owner_calls", ("every pass; off unless OWNER_CALLS=on; an alert or "
                             "urgent card unanswered for CALL_ESCALATE_MINUTES gets "
                             "one call, keyed per event in effect_claims, so a "
                             "missed pass is covered by the next (3 h lookback) "
                             "and nothing is called twice"))):
        table.append(JobPolicy(name, once, guard="interval (process memory)",
                               why=why))
    return {p.job: p for p in table}


POLICIES = _policies()


# No step may own the cycle. While one does, no check is observed, no
# proposal expires and no CRITICAL pages — and nothing says so, because a
# step is only visible when it returns. The API keeps serving 200s and
# /healthz stays green throughout. Measured on atlas.storefront.test: gaps of
# 1h46m ending 2026-09-24 05:10 and 1h21m ending 19:19, no restart, no error.
#
# Two defences, because either alone still leaves ATLAS blind:
#   _detach — the steps that are expensive by design (a reply draft runs a
#     model once per message, the nightly bake-off retrains every head) run as
#     their own task, one at a time, exactly as _run_checks does with handle().
#   _step   — every remaining step is bounded, so a future addition cannot
#     freeze the loop by forgetting to detach.
STEP_TIMEOUT_S = float(os.getenv("SCHEDULER_STEP_TIMEOUT_S", "600"))

# Long enough to be worth a line in the log, short enough that the next stall
# is diagnosable instead of invisible.
SLOW_STEP_S = 10.0

_DETACHED = ("signals", "brief", "weekly", "retention", "judgment", "fixes",
            "agent_shifts", "agency_meeting", "playlist", "ideas",
            "approvals_board", "sales_shift", "content_weekly", "bizops_digest",
            "bizops_find_repeats", "seo_rank", "research_scan", "research_weekly",
            "inbox_shift", "sites_shift", "tutorco_shift", "whatsapp_daily",
            "sage_tutorco_weekly", "youtube_learn")

# How long a detached step may run before it is cancelled (RA-D3). Without
# one, a hung step kept its name "running" and skipped every later cycle:
# `signals` sat 13+ minutes on 2026-09-27. Sized to the job: shifts are
# model calls in series (the local model cold-loads in ~70 s), the nightly
# bake-off retrains every head. A cancelled brief or review leaves its
# window claim to go stale and be retried (kernel.occurrence).
DETACHED_DEADLINE_S = {
    "signals": 300.0, "brief": 900.0, "weekly": 1800.0, "retention": 1800.0,
    "judgment": 3600.0, "fixes": 1800.0, "agent_shifts": 3600.0,
    # One model call for the polish (bounded inside at 120 s) plus reads.
    "agency_meeting": 600.0,
    "playlist": 900.0, "ideas": 900.0,
    # Executor calls in series: a few site reads and drafts per lead.
    "sales_shift": 1800.0,
    # One IMAP read plus the capped classifier calls.
    "inbox_shift": 900.0,
    # Up to three mockup + build executor calls and the reads.
    "sites_shift": 1800.0,
    # Capped public fetches (politeness delays) plus a few extract calls.
    "tutorco_shift": 1800.0,
    # One report read and at most a few Twilio sends.
    "whatsapp_daily": 300.0,
    # Four capped public fetches, one state read, two executor calls.
    "sage_tutorco_weekly": 900.0,
    # Up to eight videos: a transcript read and up to five model calls each.
    "youtube_learn": 3000.0,
    # A report, a plan and a few title steps: reads and executor calls.
    "content_weekly": 900.0,
    # A few executor calls: onboardings, reminders, the digest; one scan.
    "bizops_digest": 600.0, "bizops_find_repeats": 300.0,
    # Executor calls in series: one search per keyword, a few page reads.
    "seo_rank": 900.0,
    # Page reads in series through the scraper; up to ten profile refreshes.
    "research_scan": 1200.0, "research_weekly": 1800.0,
    # A few Slack calls: the board, and at most five card links looked up.
    "approvals_board": 180.0,
    # Only in the loop with ATLAS_DURABLE_CORE=on (_DURABLE_DETACHED). The
    # pass starts a row only while a whole execute budget still fits (see
    # _outbox_pass), so this deadline does not cut a dispatch off mid-run.
    "outbox": 1500.0,
}


async def _bounded(name: str, coro) -> None:
    deadline = DETACHED_DEADLINE_S.get(name, STEP_TIMEOUT_S)
    try:
        await asyncio.wait_for(coro, timeout=deadline)
    except TimeoutError:
        log.error("scheduler step %r passed its %.0fs deadline and was "
                  "cancelled; it runs again on a later cycle", name, deadline)

_tasks: dict[str, asyncio.Task] = {}


def _running(name: str) -> bool:
    task = _tasks.get(name)
    return task is not None and not task.done()


def _detach(name: str, coro) -> bool:
    """Start `coro` as its own task unless one of this name is still going.

    A nightly job that overran into the next night must not end up with two
    copies retraining the same heads against the same rows.
    """
    if _running(name):
        coro.close()
        log.info("scheduler step %r is still running; skipping this cycle",
                 name)
        return False
    _tasks[name] = task = asyncio.create_task(_bounded(name, coro),
                                              name=f"atlas.{name}")
    task.add_done_callback(lambda t, n=name: _detached_done(n, t))
    background.track(task, f"scheduler step {name}", log_errors=False)
    return True


def _detached_done(name: str, task: asyncio.Task) -> None:
    """A task nobody awaits swallows its exception until it is collected, which
    is one more way for the scheduler to go quiet without a line in the log."""
    if task.cancelled():
        return
    if exc := task.exception():
        log.error("scheduler step %r failed: %s", name, exc, exc_info=exc)


async def _step(name: str, fn, timeout: float | None = None) -> None:
    """Run one inline step, bounded, and say so when it was slow."""
    started = time.monotonic()
    try:
        await asyncio.wait_for(fn(), timeout=timeout or STEP_TIMEOUT_S)
    except TimeoutError:
        log.error("scheduler step %r passed %.0fs and was cancelled; the "
                  "cycle goes on without it", name, timeout or STEP_TIMEOUT_S)
    except Exception:
        log.exception("scheduler step %r failed", name)
    if (took := time.monotonic() - started) > SLOW_STEP_S:
        log.warning("scheduler step %r held the cycle for %.0fs", name, took)


async def loop(interval: float = 30.0) -> None:
    global LAST_CYCLE_AT
    log.info("scheduler up: %d checks across %d modules",
             len(REGISTRY.checks), len(REGISTRY.modules))
    while True:
        # Each responsibility is isolated. The brief and the retention run are
        # not allowed to depend on checks, reasoning or notifications having
        # gone well — those are exactly the mornings Miro most needs a brief.
        durable_on = _durable_enabled()
        steps = (
            ("checks", _run_checks),
            ("approvals", expire_stale),
            ("approvals_board", board.step),
            ("signals", _maybe_signals),
            ("brief", _maybe_brief),
            ("agency_meeting", _maybe_agency_meeting),
            ("agency_lead_board", _maybe_lead_board),
            ("weekly", _maybe_weekly),
            ("marketing", _maybe_marketing),
            ("playlist", _maybe_playlist),
            ("ideas", _maybe_ideas),
            ("sales_shift", _maybe_sales_shift),
            ("inbox_shift", _maybe_inbox_shift),
            ("sites_shift", _maybe_sites_shift),
            ("tutorco_shift", _maybe_tutorco_shift),
            ("whatsapp_daily", _maybe_whatsapp_daily),
            ("whatsapp_alerts", _maybe_whatsapp_alerts),
            ("owner_calls", _maybe_owner_calls),
            ("assistant_notices", _maybe_assistant_notices),
            ("whatsapp_cards", _maybe_whatsapp_cards),
            ("sage_tutorco_weekly", _maybe_sage_tutorco_weekly),
            ("youtube_learn", _maybe_youtube_learn),
            ("content_weekly", _maybe_content_weekly),
            ("bizops_digest", _maybe_bizops_digest),
            ("bizops_find_repeats", _maybe_bizops_find_repeats),
            ("seo_rank", _maybe_seo_rank),
            ("research_scan", _maybe_research_scan),
            ("research_weekly", _maybe_research_weekly),
            ("reels", _maybe_reels),
            ("calendar", _maybe_calendar),
            ("clips", _maybe_clips),
            ("clip_links", _maybe_clip_links),
            # Miro, 2026-10-13: two Shorts a day. The teaching tick is
            # spawned before the reel's 23 UTC slot, so on a teaching day its
            # seconds-long cut normally takes the slot before the reel's
            # minutes-long render (tutorials.teaching_tick).
            ("academy_teaching", _maybe_academy_teaching),
            ("academy_reel", _maybe_academy_reel),
            ("academy_tutorial", _maybe_academy_tutorial),
            ("academy_videos", _maybe_academy_videos),
            ("community", _maybe_community),
            ("market_alerts", _maybe_market_alerts),
            ("funnels", _maybe_funnels),
            ("missions", _maybe_missions),
            ("lead_replies", _maybe_lead_replies),
            ("linkedin_replies", _maybe_linkedin_replies),
            ("linkedin_autopost", _maybe_linkedin_autopost),
            ("academy_support", _maybe_academy_support),
            ("draft_notice", _maybe_draft_notice),
            ("academy_subscribe", _maybe_academy_subscribe),
            ("agency_contact", _maybe_agency_contact),
            ("qa_verify_runs", _maybe_qa_verify_runs),
            ("qa_autopilot", _maybe_qa_autopilot),
            ("review", _maybe_review),
            ("rhythm", _maybe_rhythm),
            ("retention", _maybe_purge),
            ("judgment", _maybe_tournament),
            ("fixes", _maybe_fixes),
            ("agent_shifts", _maybe_agent_shifts),
        )
        if durable_on:
            steps += (("outbox", _outbox_pass),)
        for name, step in steps:
            if durable_on and name not in _UNLEASED:
                await _leased_step(name, step)
            elif name in _DETACHED:
                _detach(name, step())
            else:
                await _step(name, step)
        LAST_CYCLE_AT = time.monotonic()
        await asyncio.sleep(interval)


# ---------------------------------------------------------------- durable core
#
# Behind ATLAS_DURABLE_CORE (default off; kernel.durable.enabled, read each
# cycle). Off, the loop above is exactly what it was: no lease is read, no
# outbox step exists. On, every step except the two below runs under a named
# lease `scheduler:<step>` with a fencing token (kernel.leases), so a second
# process -- a deploy's new container overlapping the old one -- cannot run
# the same report or sweep at the same time. A lease that cannot be read is
# not held: the step is skipped this cycle (fail closed) and runs on a later
# one.
#
# Not leased: `checks` and `approvals`. Observing, paging and expiring are
# safe to run twice and must not go quiet because the lease table blinked --
# a fail-closed lease on paging would turn a database hiccup into silence.
_UNLEASED = ("checks", "approvals")
# Detached like _DETACHED, but only ever in the loop with the flag on, so
# the flag-off step set is exactly what it was.
_DURABLE_DETACHED = ("outbox",)
LEASE_MARGIN_S = 60.0
OUTBOX_BATCH = 10
OUTBOX_RECONCILE_S = 120.0


def _durable_enabled() -> bool:
    from .durable import enabled
    return enabled()


def _lease_ttl(name: str) -> timedelta:
    deadline = DETACHED_DEADLINE_S.get(name, STEP_TIMEOUT_S)
    return timedelta(seconds=deadline + LEASE_MARGIN_S)


async def _held(fence, coro) -> None:
    """Run `coro`, then give the lease back however it ended."""
    from . import leases
    try:
        await coro
    finally:
        await leases.release(fence)


async def _leased_step(name: str, step) -> bool:
    """Run one step under its lease. False when it did not start: still
    running from an earlier cycle, or the lease is held elsewhere or
    unreadable."""
    from . import leases
    detached = name in _DETACHED or name in _DURABLE_DETACHED
    if detached and _running(name):
        log.info("scheduler step %r is still running; skipping this cycle", name)
        return False
    fence = await leases.acquire(f"scheduler:{name}", _lease_ttl(name))
    if fence is None:
        log.info("scheduler step %r: lease not held; skipping this cycle", name)
        return False
    if detached:
        if _detach(name, _held(fence, step())):
            return True
        await leases.release(fence)
        return False
    try:
        await _step(name, step)
    finally:
        await leases.release(fence)
    return True


async def _outbox_pass() -> None:
    """Dispatch due outbox rows through the executor, then settle one
    reconciling row from a read-back. Nothing here sends; kernel.outbox
    hands every intent to executor.execute."""
    from . import outbox
    deadline = DETACHED_DEADLINE_S["outbox"]
    await outbox.dispatch(limit=OUTBOX_BATCH,
                          budget_s=deadline - OUTBOX_RECONCILE_S)
    try:
        async with asyncio.timeout(OUTBOX_RECONCILE_S):
            await outbox.reconcile_once()
    except TimeoutError:
        # Its lease lapses and the row is claimed again on a later pass.
        log.warning("outbox reconciliation passed %.0fs; retried later",
                    OUTBOX_RECONCILE_S)
    # The first-slice workflows (kernel/flows), each behind its own name in
    # ATLAS_DURABLE_FLOWS: settle what the outbox finished, mirror decided
    # cards, read back recovered checks.
    from . import flows
    if flows.any_enabled():
        try:
            async with asyncio.timeout(OUTBOX_RECONCILE_S):
                await flows.tick()
        except TimeoutError:
            log.warning("flows pass passed %.0fs; retried later",
                        OUTBOX_RECONCILE_S)


_handling: asyncio.Task | None = None


async def _run_checks() -> None:
    findings = await run_once()
    if not findings:
        return
    # Anything Miro acknowledged from Slack neither pages nor raises a card
    # until it changes. See acks.py.
    from .acks import filter_acked
    findings = await filter_acked(findings)
    if not findings:
        return
    # Paging is cheap and must not wait on anything: it happens here, on the
    # cycle that saw the failure. Reasoning is not cheap -- one model call
    # per finding, in series, measured at five minutes for a full board --
    # and while handle() ran inline, no check ran at all. So it goes to a
    # background task, one at a time: a cycle that lands while the previous
    # one is still thinking is skipped, and a finding that persists is seen
    # again next cycle anyway.
    for finding in findings:
        if finding.severity >= Severity.NORMAL:
            await page(finding)
    from .detect import unconfigured
    findings = [f for f in findings if not unconfigured(f)]
    if not findings:
        return
    global _handling
    if _handling is not None and not _handling.done():
        log.info("still reasoning about the last cycle; %d finding(s) wait",
                 len(findings))
        return
    _handling = background.spawn(handle(findings), "reasoning over findings")
