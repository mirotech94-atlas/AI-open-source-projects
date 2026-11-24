"""Proposal broker. Silence is never consent."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import idempotency, lessons
from .audit import audit
from .comms import CARD_RULE, Button, Outbound, notify
from .contracts import ActionResult, FailureClass, Severity, Tier
from .events import publish
from .executor import (  # noqa: F401 -- re-exported: propose() and its callers
    ExecutionRequest,
    execute,
    missing_params,
    required_params,
    stale_of,
    watch_only,
)
from .registry import REGISTRY
from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.approvals")

# the owner, 2026-10-06: "cards should never expire". They still do -- silence
# is never consent (invariant 5) and a tap never runs stale (the dry run is
# re-run on approval) -- but a routine card now waits two weeks and is
# listed to him once a day (`_open_cards_notice`) until he decides. Urgent
# cards keep their short clocks: a stale "restart the server" is a danger,
# not a convenience.
TTL = {
    Severity.CRITICAL: timedelta(minutes=30),
    Severity.HIGH: timedelta(hours=4),
    Severity.NORMAL: timedelta(days=14),
    Severity.INFO: timedelta(days=14),
}


# RA-B6: how long an automatically raised cause stays quiet after its card
# expired unanswered, or was declined.
QUIET_AFTER_EXPIRY = timedelta(days=3)
QUIET_AFTER_DECLINE = timedelta(days=7)


class Unrunnable(ValueError):
    """`propose()` refused a card that could only fail on approval. A
    ValueError, so every caller that already catches a refusal still does;
    its own type, so a spoken surface can read `spoken` back to him instead
    of saying there was no action at all."""

    def __init__(self, action: str, spoken: str) -> None:
        super().__init__(spoken)
        self.action = action
        self.spoken = spoken


class MissingParams(Unrunnable):
    """A parameter `action.run` requires was not given."""

    def __init__(self, action: str, missing: list[str], spoken: str) -> None:
        super().__init__(action, spoken)
        self.missing = missing


class UndeclaredParams(Unrunnable):
    """A parameter the action does not declare was given. The executor
    refuses it on approval; refusing it here means there is no card."""

    def __init__(self, action: str, undeclared: list[str]) -> None:
        super().__init__(action, (
            f"I can't queue that: undeclared parameter(s): "
            f"{', '.join(undeclared)}."))
        self.undeclared = undeclared


def undeclared_params(action: object, params: dict | None) -> list[str]:
    """Parameters given that `action.params` does not declare -- the
    executor's own test (`_validate_params`), asked before the card exists.
    An action with no `params` mapping declares nothing to check against."""
    declared = getattr(action, "params", None)
    if not isinstance(declared, dict):
        return []
    return sorted(set(params or {}) - set(declared))


def _in_words(name: str) -> str:
    words = name.split("_")
    if len(words) > 1 and words[-1] == "id":
        return "which " + " ".join(words[:-1])
    if len(words) > 1 and words[-1] == "ids":
        return "which " + " ".join(words[:-1]) + "s"
    return "the " + " ".join(words)


def missing_reason(missing: list[str]) -> str:
    """"I need to know which lead before I can queue that." Said out loud,
    so words rather than parameter names."""
    wanted = [_in_words(name) for name in missing]
    joined = (wanted[0] if len(wanted) == 1
              else ", ".join(wanted[:-1]) + " and " + wanted[-1])
    return f"I need to know {joined} before I can queue that."


async def propose(
    action: str,
    params: dict,
    reason: str,
    *,
    severity: Severity = Severity.NORMAL,
    evidence: dict | None = None,
    rollback_plan: str = "",
    confidence: float = 0.5,
    incident_id: int | None = None,
    dedup_key: str | None = None,
    automatic: bool = False,
    urgent: bool = False,
) -> int:
    """Raise a decision for the owner. Returns the proposal id.

    `urgent` is the only way a card becomes urgent (`evidence.urgent`, read by
    `owner_calls`): a keyword set in code by a proposer. Any `urgent` key already
    inside `evidence` is dropped, because evidence carries collector and ingested
    text (invariant 4) and must never be able to ring the owner's phone.

    `dedup_key` names the CAUSE. While a proposal with that key is still
    pending, the same cause raises nothing new: it returns the existing id
    instead. A check that stays UNKNOWN otherwise produces a fresh proposal
    every cycle -- 70 identical "file an issue about the missing FlyCheap
    Stripe key" cards accumulated in a single day, which does not just make
    noise, it buries the real decisions in the same list and makes the whole
    queue something to scroll past.

    Deliberately keyed on the cause rather than on the action and parameters:
    the reasoner writes a slightly different issue title each time, so
    identical parameters would never match and the dedup would never fire.
    """
    decision = REGISTRY.decisions.get(action)
    if decision is None or not decision.allowed:
        raise ValueError(f"cannot propose {action}: {decision.reason if decision else 'unknown'}")
    evidence = {k: v for k, v in (evidence or {}).items() if k != "urgent"}
    if urgent is True and decision.tier is Tier.ASK:
        evidence["urgent"] = True

    # The missing half of invariant 2. The executor refuses a parameter the
    # action did not declare; nothing refused one it NEEDS, so a spoken
    # "build them a website" became a card with no lead_id that raised
    # TypeError on approval, went into the lessons ledger, and after two of
    # those had never-repeat block the action outright. Asking "which lead"
    # now is better than a card that can only fail.
    # Both halves of invariant 2's "parameters are validated", before the
    # card exists: nothing it needs is missing, and nothing it does not
    # declare is there. Either one is a card that can only fail.
    target = REGISTRY.actions.get(action)
    missing = missing_params(target, params)
    if missing:
        await audit("proposal.incomplete", action, {"missing": missing})
        raise MissingParams(action, missing, missing_reason(missing))
    undeclared = undeclared_params(target, params)
    if undeclared:
        await audit("proposal.undeclared", action, {"undeclared": undeclared})
        raise UndeclaredParams(action, undeclared)
    # The third half: values that are each fine alone but not together (a
    # travel post aimed at the AI-stories account). Asked of the action's
    # own pure `vet`, never of its dry run -- a dry run may reach the network.
    vet = getattr(target, "vet", None)
    if vet is not None:
        why = vet(dict(params or {}))
        if why:
            await audit("proposal.vetoed", action, {"why": why})
            raise Unrunnable(action, f"I can't queue that: {why}")

    # The QA hard gate (kernel/qa_gate.py): no card for content QA has not
    # passed. Fail closed: anything that stops the gate is a refusal.
    from . import qa_gate
    if qa_gate.gated(action):
        try:
            await qa_gate.require(action, dict(params or {}))
        except qa_gate.QaRequired as exc:
            await audit("proposal.qa_held", action, {"why": exc.spoken[:300]})
            raise
        except Exception as exc:
            await audit("proposal.qa_held", action, {"why": str(exc)[:300]})
            raise qa_gate.QaRequired(action, f"QA could not run, so it is held: "
                                             f"{type(exc).__name__}") from exc

    if dedup_key:
        existing = await fetch_one(
            """SELECT id FROM proposals
                WHERE dedup_key = $1 AND status = 'pending'
                  AND expires_at > now()
                ORDER BY created_at DESC LIMIT 1""", dedup_key)
        if existing is not None:
            log.debug("not re-proposing %s: #%s is already pending for %r",
                      action, existing["id"], dedup_key)
            return int(existing["id"])
        if automatic:
            # RA-B6. A cause ATLAS raises by itself (a finding, a module
            # signal, an agent's tool call) re-proposed the moment its card
            # expired: 151 gitlab.open_issue cards from 33 causes, 124 of
            # them expired unanswered. An expired card is closed, not
            # re-asked, for QUIET_AFTER_EXPIRY; a declined one for
            # QUIET_AFTER_DECLINE. Only what he asks for himself, and the
            # reels calendar with its own once-a-day retry, skip this.
            quiet = await fetch_one(
                """SELECT id, status, decided_at, expires_at FROM proposals
                    WHERE dedup_key = $1
                      AND ((status = 'expired'
                            AND expires_at > now() - $2::interval)
                        OR (status = 'declined'
                            AND decided_at > now() - $3::interval))
                    ORDER BY created_at DESC LIMIT 1""",
                dedup_key, QUIET_AFTER_EXPIRY, QUIET_AFTER_DECLINE)
            if quiet is not None:
                what = ("expired unanswered" if quiet["status"] == "expired"
                        else "was declined")
                raise Unrunnable(action, (
                    f"#{quiet['id']} for the same cause {what}; it is not "
                    f"asked again yet -- silence is not a yes, and a new "
                    f"card every cycle is noise"))

    # Never-repeat, and deliberately AFTER the dedup lookup. `dedup_key` stops
    # the same CAUSE being raised twice while a card is pending; this stops
    # the same MISTAKE being raised a third time at all. Below the lookup so a
    # cause he already has a card for short-circuits first, without a ledger
    # read or an audit line -- he cannot answer a card twice, and a second
    # copy of the refusal tells him nothing.
    #
    # It refuses to ASK, which is always safe: it can never cause something to
    # run and it never touches a tier. He unlocks it by saying why it is
    # different (`lessons.clear`, Tier 2 so nothing unattended can), never
    # by waiting.
    if (lesson := await lessons.blocked(action, params)) is not None:
        await audit("proposal.blocked", action, {"lesson": lesson})
        raise ValueError(f"cannot propose {action}: {lesson}")

    # The learning layer's number, before the card exists: what would he
    # say? It ranks, routes and — with enough history — hushes. It never
    # decides: whatever it says, the proposal below is still the only path
    # to an action, and it still waits for him. See JUDGMENT.md §7.
    from .judgment import Advice, predict, standing_rule_text
    from .judgment import record as record_prediction
    pred = await predict(action, int(severity))
    suppressed = pred.advice is Advice.SUPPRESS

    # What is being approved, recorded with the card (P04, S08): decide()
    # runs it only while the action and its params still match.
    from .registry import payload_hash
    version = REGISTRY.action_version(action)
    phash = payload_hash(params)

    # BUILD_ORDER 10.1 asks for the dry-run output IN the message, and it was
    # the one clause still unmet: the output was stored and rendered on the web
    # view, so the card said "requires approval" and the evidence for that
    # decision was a click away on a mesh-only page. Deciding on a phone
    # meant approving without it.
    #
    # `executor.preview` exists for exactly this and is the only safe way to
    # get it: it calls `dry_run` and can never reach `run`, so this is sound
    # even in watch-only — seeing what WOULD happen is the point of that phase.
    #
    # It never blocks the card. A dry run that raises, refuses or takes too
    # long yields a card that says so, because a decision with no preview is
    # still better than no decision reaching him at all.
    #
    # Only for the tier that actually asks him something. A Tier 1 action runs
    # unattended and dry-runs inside `execute` anyway, so previewing it here
    # would do the work twice to decorate a card nobody decides on — measured
    # at roughly ten times the runtime across the approval tests, because every
    # proposal was paying for a dry run whether or not a human would ever read
    # the result.
    dry_text: str | None = None
    dry_ok: bool | None = None
    if decision.tier >= Tier.ASK:
        dry_text, dry_ok = await _preview_for_card(action, params)

    row = await fetch_one(
        """INSERT INTO proposals
             (incident_id, expires_at, action, params, tier, reason,
              evidence, rollback_plan, confidence, dedup_key, status, predicted,
              catalog_version, approved_payload_hash, dry_run, dry_run_ok,
              dry_run_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
           RETURNING id, params""",
        incident_id, datetime.now(UTC) + TTL[severity], action,
        json.dumps(params, default=str), int(decision.tier), reason,
        json.dumps(evidence or {}, default=str), rollback_plan, confidence,
        dedup_key, "suppressed" if suppressed else "pending", pred.p,
        version, phash, dry_text, dry_ok,
        datetime.now(UTC) if dry_text else None,
    )
    pid = row["id"]
    await _rehash_stored(pid, row, phash)
    await record_prediction(pid, pred, int(decision.tier))
    await audit("proposal.created", action, {
        "id": pid, "reason": reason, "predicted": pred.p,
        "predicted_by": pred.head, "advice": str(pred.advice)})
    if suppressed:
        # p < 0.30 with real history: he has said no to this, repeatedly.
        # It is recorded, it goes in the brief as one line, and it never
        # runs. A 'suppressed' row is not 'pending', so nothing can approve
        # it by accident.
        publish("proposal", id=pid, action=action, tier=int(decision.tier),
                suppressed=True)
        log.info("proposal #%s not raised: %s (%s)", pid, pred.card_line(),
                 ", ".join(pred.reasons))
        return pid
    publish("proposal", id=pid, action=action, tier=int(decision.tier))

    # One line on the card. The reasoner writes a paragraph, and a paragraph
    # on every card is what turns a channel into something nobody reads.
    # The full reason is stored and the Details button shows it.
    spec = REGISTRY.actions[action]
    topic = _topic(spec, action, params)
    verbose = bool(getattr(spec, "verbose_card", False))
    # the owner, 2026-10-11: "I don't know which one is which." The header says
    # the area and what approving does; the first line says what each
    # button does; the reason and evidence follow below a divider. Built
    # from the action and params only (kernel/board.py), never the reason.
    from . import board
    detail = "\n".join(filter(None, [
        reason.strip()[:1500] if verbose else _headline(reason),
        # The exact recipient and words, when those are the decision.
        _card_detail(spec, params),
        f"`{action}` · Tier {int(decision.tier)}"
        + (f" · rollback: {_headline(rollback_plan, 60)}" if rollback_plan else ""),
        # 10.1: what the dry run actually said, on the card. A refusal leads
        # with the word, because that is the line that changes his answer —
        # approving something whose dry run already refused is the mistake this
        # prevents. `ok is None` means the preview could not be reached, which
        # is stated as not knowing rather than as a refusal.
        (f"⚠ dry run REFUSED: {_headline(dry_text, 110)}" if dry_ok is False
         else f"dry run: {_headline(dry_text, 120)}" if dry_ok
         else f"dry run {_headline(dry_text, 110)}") if dry_text else "",
        # Never a number without its reasons. Empty when abstaining, which
        # is what happens for the first forty decisions and for anything new.
        pred.card_line(),
        # At >= 95% on a reversible thing he has answered twenty times, ATLAS
        # drafts the rule. "Approve + suggest always" is his one confirmation;
        # it records a promotion candidate, and the tier changes in code.
        (f"Standing rule? _{standing_rule_text(action, pred)}_ — "
         f"'Approve + suggest always' records it."
         if pred.advice is Advice.STANDING_RULE else ""),
    ]))
    body = f"{board.card_lead(action, params, spec)}\n{CARD_RULE}\n{detail}"
    # Slack is NOT where approvals live (WhatsApp buttons and the portal are):
    # the Slack card, with its Approve buttons, exists only with APPROVALS_SLACK=on.
    from . import wa_approvals as _wa
    if _wa.slack_on():
        await notify(Outbound(
            title=f"#{pid} — {REGISTRY.actions[action].summary}",
            header=board.card_header(pid, action, params, spec, evidence),
            body=body, severity=severity,
            # The namespace IS the topic: "cloudco.restart_instance" is cloudco's
            # business, and an approval should land where that estate is
            # discussed rather than in a single channel everything shares.
            # An action may name a parameter that says which estate instead.
            topic=topic,
            presentation="REVIEW" if verbose else "ACTION",
            buttons=(
                Button("Approve", f"approve:{pid}"),
                Button("Approve + suggest always", f"always:{pid}"),
                Button("Decline", f"decline:{pid}"),
                Button("Details", f"detail:{pid}"),
            ),
        ))
        # Where the card landed, for the board's link; then redraw the board.
        await board.record_card(pid, topic)
    board.mark_dirty()
    # The same card on the owner's WhatsApp, with Approve / Decline buttons. Off
    # unless WhatsApp is configured; never blocks or fails the card.
    if decision.tier >= Tier.ASK:
        from . import background, wa_approvals
        if wa_approvals.enabled():
            background.spawn(_whatsapp_card(pid), f"whatsapp card #{pid}")
    return pid


WA_RETRY_AFTER_S = 300.0     # the one second WhatsApp attempt, this long after the first


async def _send_whatsapp_card(pid: int, reason: str) -> ActionResult:
    try:
        return await execute(ExecutionRequest(
            action="mmt.notify_card", params={"proposal_id": pid}, reason=reason))
    except Exception as exc:                                # noqa: BLE001
        return ActionResult(False, type(exc).__name__)


async def _whatsapp_card(pid: int) -> None:
    """Send the owner's WhatsApp copy of card #pid through the executor
    (`mmt.notify_card`, Tier 1). WhatsApp is where he decides cards. If the send
    fails, ONE second attempt follows a few minutes later (bounded), and a card
    still unsent shows as a banner on the portal. Slack hears of it only with
    APPROVALS_SLACK=on. Watch-only sends nothing here (the executor refuses;
    nothing could be decided from the message anyway) and says nothing."""
    from . import background, wa_approvals
    if watch_only():
        return
    result = await _send_whatsapp_card(pid, f"card #{pid} was raised")
    if result.ok or result.detail.startswith("already notified"):
        return
    if wa_approvals.slack_on():
        await wa_approvals.tell_slack(
            f"#{pid} — WhatsApp not delivered",
            f"The WhatsApp copy of card #{pid} did not go out "
            f"({result.detail[:200]}). It is on the portal.")
    if result.failure_class is FailureClass.VALIDATION:
        # A refusal that waiting cannot cure (no owner number, sender not set,
        # card no longer pending): one more attempt would fail the same way.
        return
    background.spawn(_whatsapp_card_retry(pid), f"whatsapp card retry #{pid}")


async def _whatsapp_card_retry(pid: int) -> None:
    """The second and last attempt, only if the card is still pending."""
    await asyncio.sleep(WA_RETRY_AFTER_S)
    if watch_only():
        return
    try:
        row = await fetch_one("SELECT status FROM proposals WHERE id=$1", pid)
    except Exception as exc:                                # noqa: BLE001
        log.warning("card #%s: retry not read (%s)", pid, type(exc).__name__)
        return
    if row is None or row["status"] != "pending":
        return
    result = await _send_whatsapp_card(pid, f"card #{pid}: second WhatsApp attempt")
    if not result.ok and not result.detail.startswith("already notified"):
        log.warning("card #%s: second WhatsApp attempt failed: %s", pid,
                    result.detail[:120])


def _card_detail(spec: object, params: dict | None) -> str:
    """`Action.card_detail`, or nothing. A renderer that raises is not
    allowed to stop the card: the params are then shown raw."""
    render = getattr(spec, "card_detail", None)
    if render is None:
        return ""
    try:
        return str(render(dict(params or {})))
    except Exception:                                   # noqa: BLE001
        return json.dumps(params or {}, default=str, ensure_ascii=False)


async def _rehash_stored(pid: int, row: Any, phash: str) -> None:
    """Record the hash of the params as the database gave them back, when
    JSONB changed them on the way in. Until this lands the stored hash is
    the one computed before the insert, which can only refuse a run, never
    let a different one through."""
    stored = row.get("params") if hasattr(row, "get") else None
    if stored is None:
        return
    from .registry import payload_hash
    again = payload_hash(stored)
    if again == phash:
        return
    try:
        await execute_sql(
            "UPDATE proposals SET approved_payload_hash = $2 WHERE id = $1",
            pid, again)
    except Exception:                                   # noqa: BLE001
        log.warning("proposal #%s: stored params hash not refreshed", pid)


def _topic(spec: object, action: str, params: dict | None) -> str:
    """Where a card about `action` lands: its `topic_for`, else the value of
    its `topic_param`, else its namespace."""
    params = dict(params or {})
    topic_param = getattr(spec, "topic_param", None)
    topic_for = getattr(spec, "topic_for", None)
    if topic_for is not None:
        topic = str(topic_for(params) or "")
    else:
        topic = str(params.get(topic_param) or "") if topic_param else ""
    return topic or action.split(".", 1)[0]


def topic_for_action(action: str, params: dict | None) -> str:
    """Where a card about `action` (with `params`) lands -- the same
    resolution `propose()` uses to route its own notification, exposed for
    a caller that must land something else (a signal's media attachment)
    in that same place without re-deriving the rule."""
    spec = REGISTRY.actions.get(action)
    return _topic(spec, action, params) if spec is not None else (
        action.split(".", 1)[0] if action else "")


# A preview is worth waiting a moment for and not worth delaying a CRITICAL
# card over. Most dry runs are a formatted string and a bounded query.
PREVIEW_TIMEOUT_S = 8.0


async def _preview_for_card(action: str, params: dict) -> tuple[str | None, bool | None]:
    """The dry-run output for the card, or a readable reason there is none.

    Returns (text, ok). `ok` is None when the dry run could not be reached at
    all, which must not render as a refusal — "this would be refused" and "I
    could not find out" are opposite facts, and the second one is the card
    saying it does not know.
    """
    import asyncio

    from .executor import preview

    try:
        result = await asyncio.wait_for(preview(action, params),
                                        timeout=PREVIEW_TIMEOUT_S)
    except TimeoutError:
        log.warning("dry-run preview for %s timed out after %.0fs",
                    action, PREVIEW_TIMEOUT_S)
        return ((f"could not be previewed: the dry run was still running "
                 f"after {PREVIEW_TIMEOUT_S:.0f}s"), None)
    except Exception as exc:                            # noqa: BLE001
        log.warning("dry-run preview for %s raised: %s", action, exc)
        return f"could not be previewed: {type(exc).__name__}: {exc}", None
    return (result.detail or "").strip() or "(the dry run returned no output)", result.ok


def _headline(text: str, limit: int = 140) -> str:
    """The first sentence, or the first `limit` characters, whichever is
    shorter. A card is scanned, not read; the rest is one click away."""
    text = " ".join((text or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) > limit:
        first = first[:limit - 1].rsplit(" ", 1)[0] + "…"
    return first


@dataclass(frozen=True, slots=True)
class Verdict:
    """The structured outcome of a decision.

    The API layer needs to know what happened; the SMS and Slack paths need a
    sentence. Returning prose and parsing it back would put string-matching on
    the path that decides whether something ran.
    """

    ok: bool
    outcome: str          # executed | declined | failed | already | missing
                          # | expired | refused | started (runs in background)
                          # | superseded (changed since approved; fresh card)
    detail: str
    proposal_id: int
    action: str = ""
    executed: bool = False
    # What the action itself reported, when it ran: the counts a summary
    # states come from here, never from what was intended (invariant 6).
    evidence: dict | None = None

    def render(self) -> str:
        """One line, for a channel that can only carry text."""
        return self.detail

    def receipt(self) -> str:
        """The written receipt for a decision: the id and what is actually
        true now. Approval is not completion, and a card that started a
        background run has not published anything yet."""
        pid = f"Proposal #{self.proposal_id}"
        detail = self.detail.strip()
        if self.outcome == "executed" and self.ok:
            done = detail.removeprefix("Done: ")
            return f"{pid} completed. Result: {done}"
        if self.outcome == "failed":
            failed = detail.removeprefix("Failed: ")
            return f"{pid} was approved but did not complete: {failed}"
        if self.outcome == "started":
            return (f"{pid} approved and started. Nothing is confirmed "
                    f"published yet; the results will follow in the card's "
                    f"thread.")
        if self.outcome == "superseded":
            return f"{pid} {detail.removeprefix(f'#{self.proposal_id} ')}"
        if self.outcome == "declined":
            return f"{pid} declined. It will not execute."
        if self.outcome == "expired":
            return f"{pid} had expired. Nothing was executed."
        if self.outcome == "missing":
            return f"There is no proposal #{self.proposal_id}. Nothing was executed."
        return f"{pid}: {detail}"


async def decide(pid: int, verdict: str, via: str) -> Verdict:
    """Answer a proposal (`_decide`), then mark the approvals board for a
    redraw: whatever the outcome, the card may have left the queue. Marking
    is in memory; the board is drawn by its scheduler step, never here --
    this runs inside Slack's three-second window."""
    result: Verdict | None = None
    try:
        result = await _decide(pid, verdict, via)
        return result
    finally:
        from . import board
        board.mark_dirty()
        # Deciding in one channel retires the WhatsApp code (no double decision).
        # A refused attempt (watch-only) decided nothing, so its code stays.
        # Best effort: a failure here must not turn a decided card into an error.
        if result is not None and result.outcome != "refused":
            try:
                from . import wa_approvals
                if wa_approvals.enabled():
                    await wa_approvals.retire(pid)
            except Exception as exc:                        # noqa: BLE001
                log.warning("card #%s: code not retired (%s)", pid,
                            type(exc).__name__)


def retired_agent_keys() -> list[str]:
    """the owner, 2026-10-28: the eighteen retired agents' pending cards expire
    and never execute. Read from the code constant `agents.RETIRED_AGENTS`
    (imported here, not at load, to keep the import graph acyclic), matched
    on the dedup key an agent's tool call writes; there is no switch."""
    from .agents import RETIRED_AGENTS
    return [f"%@agent:{name}" for name in sorted(RETIRED_AGENTS)]


# the owner, 2026-10-29: the Reddit posting lane is dropped (Reddit no longer
# grants new API access; his Reddit posts are manual). Its cards carry the
# key `reddit.submit_post:<draft>` with no agent suffix, so they are matched
# on the action itself. A code constant; there is no switch.
RETIRED_ACTIONS: tuple[str, ...] = ("reddit.",)


def retired_action_patterns() -> list[str]:
    """LIKE patterns for the actions of a dropped lane: a pending card for
    one expires and is never claimed."""
    return [f"{prefix}%" for prefix in RETIRED_ACTIONS]


async def _decide(pid: int, verdict: str, via: str) -> Verdict:
    """Answer a proposal. verdict: approve | always | decline.

    The claim is a single conditional UPDATE. The previous version did
    SELECT-then-check-then-UPDATE, so two concurrent answers — a double-clicked
    button, a retried request, a phone and a browser at once — could both read
    `pending`, both pass the checks, and both call `execute`. Only the caller
    whose UPDATE actually matches a row proceeds, so the action runs exactly
    once no matter how many times it is submitted.

    The same UPDATE enforces "still pending" and "not yet expired", so a tab
    left open overnight cannot approve something that lapsed hours ago.
    """
    if verdict not in ("approve", "always", "decline"):
        return Verdict(False, "refused", f"unknown verdict {verdict!r}", pid)

    if verdict == "decline":
        # A card held after a backup restore (kernel/restore.py) may be
        # declined: that resolves it, and a decline can cause no effect.
        # Approving it stays refused until it is reconciled.
        row = await fetch_one(
            """UPDATE proposals SET status='declined', decided_at=now(),
                                    decided_via=$2
               WHERE id=$1 AND (status = 'held'
                                OR (status='pending' AND expires_at > now()))
               RETURNING action, params""",
            pid, via)
        if row is None:
            return await _why_not_claimed(pid, "decline")
        await audit("proposal.declined", row["action"], {"id": pid, "via": via})
        # A no is a lesson, keyed on the action AND its parameters, so the
        # second identical no is the last one he has to give. The reply below
        # already promises "I'll weight this next time"; this is the half of
        # that promise that outlives the judgment layer's weighting.
        # `.get` so a caller that only selected the action still works;
        # asyncpg's Record and a plain dict both have it.
        declined_params = row.get("params")
        if isinstance(declined_params, str):
            declined_params = json.loads(declined_params or "{}")
        await lessons.record(
            "declined", f"You declined {row['action']}.",
            action=row["action"], params=declined_params or {})
        from .judgment import record_outcome
        await record_outcome(pid, "denied", via)
        publish("decision", id=pid, action=row["action"], outcome="declined")
        return Verdict(
            True, "declined",
            f"Declined #{pid}. Nothing was done, and I'll weight this next time.",
            pid, row["action"])

    # Approving while watch-only must not consume the proposal. Claiming it and
    # then having the executor refuse would mark a perfectly good proposal
    # 'failed' and remove the owner's chance to answer it once ATLAS is armed.
    if watch_only():
        await audit("proposal.watch_only", str(pid), {"id": pid, "via": via})
        return Verdict(
            False, "refused",
            f"ATLAS is in watch-only mode, so #{pid} was not run. It is still "
            "pending and can be approved once watch-only is turned off.",
            pid)

    # R12: after a restore from a backup, a card the dump held as pending
    # may already have run. The fence (kernel/restore.py) holds such cards
    # before the claim below can read them; if it cannot tell, nothing is
    # run. The claim itself is unchanged. (A decline runs nothing, so it is
    # not fenced.)
    from . import restore
    try:
        await restore.apply()
    except restore.FenceUnreadable:
        await audit("proposal.restore_fence_unreadable", str(pid),
                    {"id": pid, "via": via})
        return Verdict(False, "refused",
                       f"#{pid} was not run: I couldn't confirm the "
                       f"database wasn't just restored from a backup.", pid)

    # A card a retired agent raised (its dedup key ends `@agent:<name>`,
    # kernel/tools.py) or a dropped lane's card (RETIRED_ACTIONS) never
    # matches: it falls to _why_not_claimed, which
    # marks it expired. Same UPDATE, so nothing can run it in between.
    row = await fetch_one(
        """UPDATE proposals SET status='approved', decided_at=now(),
                                decided_via=$2, always=$3
           WHERE id=$1 AND status='pending' AND expires_at > now()
             AND NOT (COALESCE(dedup_key, '') LIKE ANY($4::text[]))
             AND NOT (action LIKE ANY($5::text[]))
           RETURNING action, params, reason, incident_id""",
        pid, via, verdict == "always", retired_agent_keys(),
        retired_action_patterns())
    if row is None:
        return await _why_not_claimed(pid, "approve")

    action = row["action"]
    params = row["params"]
    if isinstance(params, str):
        params = json.loads(params or "{}")

    # His answer is the label, whatever the action then does: "approved"
    # means he wanted it, and a failed run is the executor's story.
    from .judgment import record_outcome
    await record_outcome(pid, "approved", via)

    # What he approved, read beside the claim above (which is unchanged):
    # the executor refuses to run anything else (P04, S08).
    recorded = await _recorded(pid)
    if recorded is None:
        await execute_sql("UPDATE proposals SET status=$2 WHERE id=$1",
                          pid, "failed")
        publish("decision", id=pid, action=action, outcome="failed")
        return Verdict(False, "failed",
                       f"Failed: #{pid}: what was approved could not be read "
                       f"back, so nothing was run.", pid, action)

    spec = REGISTRY.actions.get(action)
    if getattr(spec, "runs_in_background", False):
        # Claimed above, atomically: it runs exactly once. The answer goes
        # back now; the run and its receipts follow in the card's thread.
        from . import background
        RUNNING[pid] = background.spawn(
            _run_claimed(pid, action, params or {}, row["reason"],
                         row["incident_id"], via, verdict == "always",
                         recorded),
            f"approved #{pid} {action}")
        return Verdict(
            True, "started",
            f"Posting #{pid} now; the receipts will be in the thread.",
            pid, action)

    # The card is the run (RA-B7): an action that must not repeat its
    # effect keys it on this, so running the same card twice sends once.
    with idempotency.running(f"proposal:{pid}"):
        result = await execute(ExecutionRequest(
            action=action, params=params or {}, reason=row["reason"],
            incident_id=row["incident_id"], approved_by=via,
            approved_version=recorded.get("catalog_version"),
            approved_payload_hash=recorded.get("approved_payload_hash"),
        ))
    if stale_of(result) is not None:
        return await _supersede(pid, action, params or {}, row["reason"],
                                row["incident_id"], recorded, result)
    await execute_sql("UPDATE proposals SET status=$2 WHERE id=$1",
                      pid, "executed" if result.ok else "failed")

    if verdict == "always" and result.ok:
        # `always` is recorded as a PROMOTION CANDIDATE, not as a grant.
        #
        # Nothing reads proposals.always to skip the approval gate, and nothing
        # should: tiers are assigned in code, and a stored flag that silently
        # downgraded Tier 2 to Tier 1 would be exactly the runtime override the
        # design forbids. The honest version is that this marks the action for
        # review, and the owner changes the tier in code if he agrees.
        await audit("proposal.promotion_requested", action, {"id": pid})
        return Verdict(
            True, "executed",
            f"Done. I've noted `{action}` as a candidate to handle "
            f"automatically — it'll be offered in the weekly report, where "
            f"promoting it means changing its tier in code. I won't start "
            f"doing it unasked before then.",
            pid, action, executed=True)

    publish("decision", id=pid, action=action,
            outcome="executed" if result.ok else "failed")
    return Verdict(
        result.ok, "executed" if result.ok else "failed",
        f"{'Done' if result.ok else 'Failed'}: {result.detail}",
        pid, action, executed=True,
        evidence=dict(getattr(result, "evidence", None) or {}))


# A claimed background run may take minutes, never forever: past this it is
# stopped and the receipt says to check each platform by hand.
BACKGROUND_DEADLINE_S = 900.0
RUNNING: dict[int, asyncio.Task] = {}


async def _run_claimed(pid: int, action: str, params: dict, reason: str,
                       incident_id: int | None, via: str, always: bool,
                       recorded: dict | None = None) -> None:
    """Run an approved `runs_in_background` action through the executor --
    the same single path, audit and dry run -- bounded by
    BACKGROUND_DEADLINE_S and cancelled with everything else at shutdown
    (`background.cancel_all`). Records the outcome on the proposal and posts
    the receipts, one per leg when the action reports legs, to the card's
    thread."""
    try:
        try:
            async with asyncio.timeout(BACKGROUND_DEADLINE_S):
                with idempotency.running(f"proposal:{pid}"):
                    result = await execute(ExecutionRequest(
                        action=action, params=params, reason=reason,
                        incident_id=incident_id, approved_by=via,
                        approved_version=(recorded or {}).get("catalog_version"),
                        approved_payload_hash=(recorded or {}).get(
                            "approved_payload_hash")))
        except TimeoutError:
            result = ActionResult(
                False, f"still running after {BACKGROUND_DEADLINE_S:.0f}s and "
                       f"stopped — check each platform by hand")
        except asyncio.CancelledError:
            await execute_sql("UPDATE proposals SET status=$2 WHERE id=$1",
                              pid, "failed")
            await _receipts(pid, action, params, ActionResult(
                False, "cut short by a shutdown — check each platform by hand"))
            raise
        if stale_of(result) is not None:
            verdict = await _supersede(pid, action, params, reason, incident_id,
                                       recorded or {}, result)
            await _receipts(pid, action, params,
                            ActionResult(False, verdict.detail))
            return
        await execute_sql("UPDATE proposals SET status=$2 WHERE id=$1",
                          pid, "executed" if result.ok else "failed")
        if always and result.ok:
            await audit("proposal.promotion_requested", action, {"id": pid})
        publish("decision", id=pid, action=action,
                outcome="executed" if result.ok else "failed")
        await _receipts(pid, action, params, result)
    finally:
        RUNNING.pop(pid, None)


async def _recorded(pid: int) -> dict | None:
    """What was recorded when #pid was raised: its catalog version and its
    params hash (None on a card raised before they existed: no check), with
    what a fresh card needs. A separate read, beside the claim: the claim's
    statement is not touched. None when it cannot be read, or the row is
    gone -- then nothing runs (deny by default)."""
    try:
        got = await fetch_one(
            """SELECT catalog_version, approved_payload_hash, dedup_key,
                      evidence, rollback_plan
                 FROM proposals WHERE id = $1""", pid)
    except Exception as exc:                            # noqa: BLE001
        log.warning("proposal #%s: approval record unreadable (%s)", pid,
                    type(exc).__name__)
        await audit("proposal.record_unreadable", str(pid),
                    {"id": pid, "error": type(exc).__name__})
        return None
    if got is None:
        await audit("proposal.record_unreadable", str(pid),
                    {"id": pid, "error": "no row"})
        return None
    return dict(got)


_CHANGED = {"version": "the action changed since it was approved",
            "params": "its parameters changed since they were approved"}


async def fresh_card(action: str, params: dict, reason: str, *,
                     dedup_key: str, incident_id: int | None = None,
                     evidence: dict | None = None,
                     rollback_plan: str = "") -> int | None:
    """Raise a new card for what would run NOW. Never approves anything:
    the card waits for him like any other. None, audited, when propose()
    refuses (the action left the catalog, a parameter it now needs is
    missing, the never-repeat ledger)."""
    try:
        return await propose(action, params, reason, evidence=evidence,
                             rollback_plan=rollback_plan or "",
                             incident_id=incident_id, dedup_key=dedup_key)
    except Exception as exc:                            # noqa: BLE001
        await audit("proposal.fresh_card_refused", action,
                    {"dedup_key": dedup_key, "why": str(exc)[:300]})
        return None


async def _supersede(pid: int, action: str, params: dict, reason: str,
                     incident_id: int | None, recorded: dict,
                     result: ActionResult) -> Verdict:
    """#pid was approved, then refused before anything ran: what would run
    is not what he approved. It becomes 'superseded' (terminal, never
    approvable again) and a fresh card asks about what would run now."""
    what = stale_of(result) or "version"
    await execute_sql(
        "UPDATE proposals SET status='superseded' WHERE id=$1 AND status='approved'",
        pid)
    evidence = recorded.get("evidence")
    if isinstance(evidence, str):
        evidence = json.loads(evidence or "{}")
    new = await fresh_card(
        action, params,
        f"Fresh card for #{pid}: {_CHANGED.get(what, _CHANGED['version'])}, so "
        f"it was not run. {reason}".strip(),
        dedup_key=f"superseded:{pid}", incident_id=incident_id,
        evidence=evidence if isinstance(evidence, dict) else None,
        rollback_plan=str(recorded.get("rollback_plan") or ""))
    await audit("proposal.superseded", action,
                {"id": pid, "changed": what, "fresh": new})
    publish("decision", id=pid, action=action, outcome="superseded")
    raised = (f" It needs a fresh approval: #{new}." if new is not None
              else " No fresh card could be raised; see the audit log.")
    return Verdict(False, "superseded",
                   f"#{pid} was not run: {_CHANGED.get(what, _CHANGED['version'])}."
                   f"{raised}", pid, action)


async def reap_orphans() -> list[int]:
    """Approvals a dead process left behind. Called ONCE, by the API server
    at startup -- never from `boot()`, which the CLI and one-off scripts also
    run while the live API may be executing an approval of its own.

    In a fresh API process nothing is running, so a proposal still marked
    'approved' was claimed by the process before and never finished: the
    graceful path (`_run_claimed`'s CancelledError) did not get to run --
    2026-09-28 04:00, a deploy collision killed the container mid-#189 and
    the card sat at 'approved' with no receipt. Each is marked failed and
    gets a receipt in its thread saying so. Nothing is re-run: he approved a
    run that may have half-happened, and a second one is his call."""
    from .state import fetch_all
    rows = await fetch_all(
        "SELECT id, action, params FROM proposals WHERE status = 'approved'")
    reaped: list[int] = []
    for r in rows:
        pid = int(r["id"])
        if pid in RUNNING:
            continue
        claimed = await fetch_one(
            """UPDATE proposals SET status = 'failed'
                WHERE id = $1 AND status = 'approved' RETURNING id""", pid)
        if claimed is None:
            continue
        reaped.append(pid)
        params = r["params"]
        if isinstance(params, str):
            params = json.loads(params or "{}")
        await audit("proposal.reaped", str(r["action"]), {"id": pid})
        try:
            await _receipts(pid, str(r["action"]), params or {}, ActionResult(
                False, "cut short by a restart before it finished -- nothing is "
                       "confirmed, so check by hand; ask and the card is raised "
                       "again"))
        except Exception:                               # noqa: BLE001
            log.warning("reaped #%s, but its receipt was not sent", pid)
    return reaped


async def reap_abandoned_runs() -> list[str]:
    """Runs a killed process left between execute.start and its finish
    (RA-B2). Called once, by the API server at startup, like
    `reap_orphans`. Each gets an `execute.abandoned` audit row, so it stops
    reading as in flight (the deploy guard waits on open runs), and the owner
    gets ONE message naming them: their outcome is unknown -- the effect
    may or may not have happened -- so he checks by hand. Nothing is re-run
    or rolled back on a guess. Only the last 7 days are looked at."""
    from .state import fetch_all
    rows = await fetch_all(
        """SELECT s.id, s.at, s.subject, s.payload FROM actions_log s
            WHERE s.event = 'execute.start'
              AND s.at > now() - interval '7 days'
              AND NOT EXISTS (
                  SELECT 1 FROM actions_log f
                   WHERE f.subject = s.subject AND f.at >= s.at AND f.id <> s.id
                     AND f.event IN ('execute.finish', 'execute.error',
                                     'execute.abandoned'))
            ORDER BY s.at""")
    if not rows:
        return []
    lines = []
    for r in rows:
        await audit("execute.abandoned", str(r["subject"]),
                    {"start_id": int(r["id"]), "started": r["at"].isoformat()})
        lines.append(f"• `{r['subject']}`, started {r['at']:%a %d %b %H:%M} UTC")
    try:
        from .comms import Outbound, notify
        await notify(Outbound(
            title=f"{len(lines)} action(s) cut off mid-run by a restart",
            body=("\n".join(lines) + "\n\nEach started and never reported back, "
                  "so whether it took effect is unknown. Check each by hand; "
                  "nothing was retried or undone."),
            severity=Severity.NORMAL, kind="alert"))
    except Exception:                                   # noqa: BLE001
        log.warning("abandoned runs recorded, but the message was not sent")
    if reconcile_on():
        # Detached: a reconcile can drive a browser (LinkedIn) for up to
        # ATLAS_VERIFY_TIMEOUT_S, and this runs before the scheduler starts.
        from . import background
        background.spawn(reconcile_abandoned(list(rows)), "reconcile abandoned runs")
    return [str(r["subject"]) for r in rows]


def reconcile_on() -> bool:
    """RA-D1/B2: look up the effect of each abandoned run after marking it.
    Read per call, like `ATLAS_VERIFY_AFTER`. Default OFF. On, the API start
    reads providers back -- including a LinkedIn browser read under its lock
    for an abandoned `linkedin.publish_post`."""
    return os.getenv("ATLAS_RECONCILE", "off").strip().lower() in (
        "1", "true", "yes", "on")


# verify.State -> the audit event that records what the look-up found.
RECONCILE_EVENTS = {"verified": "reconcile.done",       # the effect is there
                    "contradicted": "reconcile.failed",  # it is not
                    "unknown": "reconcile.needs_human"}  # nobody can tell


async def reconcile_abandoned(rows: list[Any]) -> list[tuple[str, str]]:
    """For runs `reap_abandoned_runs` just marked abandoned: ask
    `verify.reconcile_unknown` whether the effect happened, and record what
    it found as `reconcile.done`, `reconcile.failed` (with its evidence) or
    `reconcile.needs_human`. It only reads: nothing is re-run, retried or
    rolled back, whatever the answer. Never raises (bar a cancellation);
    one run's error leaves that run abandoned and unreconciled, which the
    first message already told the owner to check by hand."""
    from . import verify
    from .executor import _capped_evidence
    found: list[tuple[str, str]] = []
    for r in rows:
        subject = str(r["subject"])
        try:
            seen = await verify.reconcile_unknown(subject, None, r)
            record = seen.as_audit()
            record["evidence"] = _capped_evidence(record["evidence"])
            record.update(start_id=int(r["id"]),
                          started=verify.started_at(r).isoformat()
                          if verify.started_at(r) else None)
            event = RECONCILE_EVENTS[str(seen.state)]
            await audit(event, subject, record)
            found.append((subject, event))
        except Exception:
            log.warning("could not reconcile abandoned %s", subject, exc_info=True)
    if found:
        said = {"reconcile.done": "took effect", "reconcile.failed": "did NOT take effect",
                "reconcile.needs_human": "still unknown, check by hand"}
        try:
            from .comms import Outbound as _Outbound
            from .comms import notify as _notify
            await _notify(_Outbound(
                title=f"Looked up {len(found)} cut-off run(s)",
                body="\n".join(f"• `{s}`: {said[e]}" for s, e in found)
                     + "\n\nRead back only; nothing was retried or undone.",
                severity=Severity.NORMAL, kind="alert"))
        except Exception:                               # noqa: BLE001
            log.warning("reconciled abandoned runs, but the message was not sent")
    return found


async def _receipts(pid: int, action: str, params: dict,
                    result: ActionResult) -> None:
    """One receipt per leg (evidence shaped `{leg: {"status", "detail"}}`),
    else one for the whole run, in the card's Slack thread when it has one."""
    from .comms import sent_ref
    spec = REGISTRY.actions.get(action)
    topic = _topic(spec, action, params) if spec is not None else ""
    thread = sent_ref(f"#{pid} — ", "slack")
    legs = {k: v for k, v in (result.evidence or {}).items()
            if isinstance(v, dict) and "status" in v}
    messages = ([(f"#{pid} — {leg}: {v['status']}", str(v.get("detail") or ""))
                 for leg, v in legs.items()] if legs else
                [(f"#{pid} — {'Done' if result.ok else 'Failed'}", result.detail)])
    for title, body in messages:
        try:
            await notify(Outbound(title=title, body=body or title,
                                  severity=Severity.NORMAL, topic=topic,
                                  thread=thread, kind="receipt"))
        except Exception as exc:                        # noqa: BLE001
            log.warning("receipt for #%s not sent: %s", pid, exc)


async def _why_not_claimed(pid: int, attempted: str) -> Verdict:
    """The UPDATE matched nothing. Say which of the reasons it was.

    This is a read after the fact and is only used to phrase the answer — the
    refusal already happened, atomically, in the UPDATE.
    """
    row = await fetch_one(
        "SELECT status, expires_at FROM proposals WHERE id=$1", pid)
    if row is None:
        return Verdict(False, "missing", f"No proposal #{pid}.", pid)
    status = row["status"]
    if status == "pending":
        # Still pending but the UPDATE did not match: it had already lapsed.
        await execute_sql(
            "UPDATE proposals SET status='expired' WHERE id=$1 AND status='pending'",
            pid)
        await audit("proposal.expired", str(pid), {"id": pid, "on": attempted})
        # Tell an open dashboard the card is gone; nothing ran.
        publish("decision", id=pid, outcome="expired")
        return Verdict(
            False, "expired",
            f"#{pid} expired before it was answered. Nothing was done.", pid)
    if status == "held":
        return Verdict(
            False, "already",
            f"#{pid} came back from a backup restore and is held: it may "
            f"already have run, so it can't be approved. Check it; decline "
            f"it to clear it, and ask again if it still needs doing. Nothing "
            f"was run.", pid)
    if attempted == "decline" and status == "approved":
        return Verdict(False, "already",
                       f"#{pid} is already running; declining does not undo "
                       f"it.", pid)
    if attempted == "decline" and status == "executed":
        return Verdict(False, "already",
                       f"#{pid} already ran; declining does not undo it. "
                       f"Undoing it would be its own action.", pid)
    return Verdict(
        False, "already", f"#{pid} was already {status}.", pid)


async def expire_stale() -> int:
    rows = await fetch_all(
        """UPDATE proposals SET status='expired'
           WHERE status='pending'
             AND (expires_at < now()
                  OR COALESCE(dedup_key, '') LIKE ANY($1::text[])
                  OR action LIKE ANY($2::text[]))
           RETURNING id, action""", retired_agent_keys(), retired_action_patterns())
    from .judgment import record_outcome
    for row in rows:
        await audit("proposal.expired", row["action"], {"id": row["id"]})
        # Its own class. Expiry is never a denial: deny means the wrong
        # action, expiry means it was wrong to ask.
        await record_outcome(int(row["id"]), "expired", "expiry")
        # Silence is never consent: the card expired and nothing ran. An
        # open dashboard drops it without waiting for its next poll.
        publish("decision", id=int(row["id"]), action=row["action"], outcome="expired")
    if rows:
        from . import board
        board.mark_dirty()
    await _expiry_notice()
    await _open_cards_notice()
    return len(rows)


# At most one expiry notice an hour, listing everything that expired since
# the last one. Proposals expire one by one as their clocks run out, and a
# notice per sweep put five "1 proposal(s) expired unanswered" messages in
# #atlas-decisions inside 23 minutes (2026-09-28). The expiry itself is
# immediate and unconditional -- silence is never consent -- only the
# telling is batched.
# the owner, 2026-10-06 ("don't make each channel too chatty, summarize
# better"): once a day, with the open-cards line, not once an hour. 11 of
# #atlas-decisions' 21 messages in a week were expiry notices.
EXPIRY_NOTICE_EVERY = timedelta(hours=24)


async def _expiry_notice() -> None:
    last = await fetch_one(
        """SELECT max(at) AS at FROM actions_log
            WHERE event = 'proposal.expiry_notice' AND subject = 'proposals'""")
    last_at = last["at"] if last else None
    now = datetime.now(UTC)
    if last_at and now - last_at < EXPIRY_NOTICE_EVERY:
        return
    since = last_at or now - timedelta(days=1)
    rows = await fetch_all(
        """SELECT id, action FROM proposals
            WHERE status = 'expired' AND expires_at > $1 AND expires_at <= $2
            ORDER BY id""", since, now)
    if not rows:
        return
    await audit("proposal.expiry_notice", "proposals",
                {"ids": [int(r["id"]) for r in rows]})
    await notify(Outbound(
        title=f"{len(rows)} proposal(s) expired unanswered",
        body="\n".join(f"#{r['id']} {r['action']}" for r in rows),
        severity=Severity.INFO, kind="digest", topic="decisions"))


# Once a day at 9:00 New York time, every card still waiting -- the
# approvals board's own text, grouped by area, each number linked to its
# card -- but only when at least one has waited longer than OPEN_CARD_AGE:
# with a two-week clock a card must not go quiet in Slack's scrollback, and
# a digest that only repeats the pinned board is chatter (the owner,
# 2026-10-06). It goes to #approvals when the board has a channel, else to
# `decisions` as before. Skip-stale: a box down at 9:00 sends it until
# DIGEST_UNTIL_HOUR, never in the evening; once per New York date.
OPEN_CARD_AGE = timedelta(hours=24)
DIGEST_HOUR = 9
DIGEST_UNTIL_HOUR = 12


async def _open_cards_notice(now: datetime | None = None) -> None:
    from . import board
    now = now or datetime.now(UTC)
    local = now.astimezone(board.NY)
    if not DIGEST_HOUR <= local.hour < DIGEST_UNTIL_HOUR:
        return
    last = await fetch_one(
        """SELECT max(at) AS at FROM actions_log
            WHERE event = 'proposal.open_notice' AND subject = 'proposals'""")
    last_at = last["at"] if last else None
    if last_at and last_at.astimezone(board.NY).date() == local.date():
        return
    rows = [dict(r) for r in await fetch_all(
        """SELECT id, action, params, created_at, expires_at, slack_permalink
             FROM proposals
            WHERE status = 'pending' AND expires_at > $1
            ORDER BY expires_at, id""", now)]
    old = [r for r in rows if r.get("created_at") and r.get("expires_at")
           and r["created_at"] < now - OPEN_CARD_AGE]
    if not old:
        return
    await audit("proposal.open_notice", "proposals",
                {"ids": [int(r["id"]) for r in rows],
                 "waited": [int(r["id"]) for r in old]})
    try:
        where = await board.channel()
    except Exception:                                   # noqa: BLE001
        where = None
    await notify(Outbound(
        title=f"{len(rows)} card(s) waiting for you, {len(old)} for over a day",
        body="\n\n".join(board.render(rows, now)),
        severity=Severity.INFO, kind="digest",
        topic="approvals" if where else "decisions", channel=where or ""))

