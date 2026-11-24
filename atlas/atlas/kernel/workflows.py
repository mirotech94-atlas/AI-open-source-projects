"""The workflow catalog: what ATLAS actually runs, as graphs.

Miro, 2026-09-25: the Workflows tab should look like real n8n workflows, not
empty or short ones. This file is the read-only source of those graphs. It
defines each workflow's nodes and edges in code -- never inferred from a
model, never guessed from a name -- and how to read each node's last run
from the real tables (`checks`/`observations`, `actions_log`, `proposals`,
`agent_runs`, `reports`, `nightshift_jobs`). A node with no evidence source
never claims a time; a node whose query could not be answered says
`unknown`, never `ok` (invariant 7).

Reads only. This module must never import `kernel.executor`: it describes
what runs, it does not run anything (invariant 1) -- enforced by
`tests/test_workflows.py`'s AST walk, the same pattern `test_judgment.py`
uses for the same reason.

Four outcomes reach the canvas, deliberately fewer than the health/status
vocabulary other pages use (Fleet's WORKING, the marketing page's `won` /
`lost` / `flat` / `overdue`...): this page is about the MECHANISM, one dot
per node, not a second copy of those pages' finer detail.

  ok        the node's own event says it succeeded
  degraded  it ran, but was held back, declined, or answered nothing
  failed    it ran and did not succeed
  never     no matching row exists at all

A fifth word, `unknown`, is Health.UNKNOWN carried through unchanged for a
check node (it ran; it could not measure) and a failed *read* of any node's
evidence (invariant 7) -- both render exactly like `never` (grey, dashed,
unlit) but keep their own word in the detail text, because grey-for-a-
different-reason must still say which reason.

Colour note: `ok` reuses this HUD's own convention that OK is cyan, not
literal green -- see `hud.css`'s `--cyan` used everywhere else invariant 7
applies (`.statuspill[data-health="ok"]`, `.deepdive-checks li[data-health=
"ok"]`). The brief's "green" is this page's plain-English gloss for that
same colour, not a second palette.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .state import fetch_all, fetch_one

log = logging.getLogger("atlas.workflows")

# -------------------------------------------------------------- vocabulary

OK = "ok"
DEGRADED = "degraded"
FAILED = "failed"
NEVER = "never"
UNKNOWN = "unknown"
OUTCOMES = frozenset({OK, DEGRADED, FAILED, NEVER, UNKNOWN})

NODE_KINDS = frozenset(
    {"trigger", "check", "agent", "action", "gate", "channel", "store"})
TRIGGER_KINDS = frozenset({"cron", "event", "voice", "slack"})

# actions_log events that mean an action actually reached the executor
# (`kernel.executor.execute`'s own audit lines) or a proposal moved.
_ACTION_EVENTS = ("execute.finish", "execute.error", "execute.watch_only",
                  "execute.refused", "execute.rate_limited")


@dataclass(frozen=True, slots=True)
class NodeStatus:
    """One node's last run, as read just now. `at` is `None` exactly when
    `outcome` is `never` or the read itself failed (`unknown`) -- it is
    never borrowed from a neighbouring node."""

    at: datetime | None
    outcome: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class Evidence:
    """How to read one node's last run. `source` picks the resolver in
    `resolve_all`; `key` narrows it: a `"module.check"` for `check`, an
    action name for `action`/`proposal`, an agent name for `agent`, a
    `reports.kind` for `report`. `job` and `phone` take no key (there is
    one nightshift jobs table and one phone-decided channel). `lifecycle`
    keys name one stage of the generic propose/decide/dry-run/execute/
    rollback path or the fixer's own two checkpoints -- see `_LIFECYCLE`.
    `community` counts the AMSM channel's `community_posts` at a stage: the key is
    `status`, `platform:status`, several joined by `|`, or `metrics`
    (`community_metrics`) -- see `_community_status`.
    """

    source: str
    key: str = ""


@dataclass(frozen=True, slots=True)
class WNode:
    id: str
    kind: str
    label: str
    detail: str
    evidence: Evidence | None = None

    def __post_init__(self) -> None:
        if self.kind not in NODE_KINDS:
            raise ValueError(f"{self.id}: unknown node kind {self.kind!r}")


@dataclass(frozen=True, slots=True)
class WEdge:
    frm: str
    to: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class Workflow:
    id: str
    title: str
    trigger: str
    trigger_detail: str
    nodes: tuple[WNode, ...]
    edges: tuple[WEdge, ...]

    def __post_init__(self) -> None:
        if self.trigger not in TRIGGER_KINDS:
            raise ValueError(f"{self.id}: unknown trigger {self.trigger!r}")
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.id}: duplicate node ids")
        idset = set(ids)
        for e in self.edges:
            if e.frm not in idset or e.to not in idset:
                raise ValueError(
                    f"{self.id}: edge {e.frm} -> {e.to} names a node that "
                    "is not in this workflow")
        if not any(n.kind == "trigger" for n in self.nodes):
            raise ValueError(f"{self.id}: no trigger node")
        # Longest-path layering (see the UI's layout model) loops forever on
        # a cycle. A workflow is a pipeline, not a state machine with a
        # loop-back edge, so this is a real invariant, not just a layout
        # convenience.
        _assert_acyclic(self.id, ids, self.edges)


def _assert_acyclic(wf_id: str, ids: list[str], edges: tuple[WEdge, ...]) -> None:
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        adjacency[e.frm].append(e.to)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(ids, WHITE)

    def visit(node: str) -> None:
        colour[node] = GREY
        for nxt in adjacency[node]:
            if colour[nxt] == GREY:
                raise ValueError(f"{wf_id}: cycle through {node} -> {nxt}")
            if colour[nxt] == WHITE:
                visit(nxt)
        colour[node] = BLACK

    for i in ids:
        if colour[i] == WHITE:
            visit(i)


def _wf(id: str, title: str, trigger: str, trigger_detail: str,
        nodes: Sequence[WNode], edges: Sequence[WEdge]) -> Workflow:
    """Node and edge ids are written short and local (`"checks"`, not
    `"scheduler.checks"`); this prefixes both with the workflow id so two
    workflows can each have a node called `"trigger"` without colliding once
    they are flattened into one API response."""
    pnodes = tuple(WNode(f"{id}.{n.id}", n.kind, n.label, n.detail, n.evidence)
                   for n in nodes)
    pedges = tuple(WEdge(f"{id}.{e.frm}", f"{id}.{e.to}", e.label) for e in edges)
    return Workflow(id, title, trigger, trigger_detail, pnodes, pedges)


# ----------------------------------------------------------- the catalog

def _scheduler_cycle() -> Workflow:
    return _wf(
        "scheduler", "Scheduler cycle", "cron",
        "every 30s -- kernel.scheduler.loop, one iteration",
        nodes=(
            WNode("trigger", "trigger", "Tick",
                  "The loop's own sleep(30) -- kernel.scheduler.loop. Nothing "
                  "is measured about the tick itself; every node after it "
                  "carries its own time."),
            WNode("checks", "check", "Run due checks",
                  "run_once(): every Check whose interval is up, each timed "
                  "and wrapped -- a timeout or an exception becomes "
                  "Health.UNKNOWN rather than vanishing (invariant 7). "
                  "Evidence: the newest row across every check's own "
                  "observations.",
                  Evidence("lifecycle", "checks_ran")),
            WNode("findings", "check", "Evaluate & correlate",
                  "detect.evaluate() turns an actionable Observation into a "
                  "Finding; detect.correlate() groups findings across "
                  "properties that moved together. Evidence: the newest row "
                  "in findings.",
                  Evidence("lifecycle", "findings")),
            WNode("page", "channel", "Page on HIGH / CRITICAL",
                  "scheduler.page(): a FAILING finding at HIGH or CRITICAL "
                  "reaches Miro whether or not the reasoner names an "
                  "action -- recorded in signals so a restart does not "
                  "re-page the same title inside 24h.",
                  Evidence("lifecycle", "paged")),
            WNode("reason", "agent", "Diagnose",
                  "reason.diagnose(): one model call per finding, in the "
                  "background so it cannot stall the next tick. It has no "
                  "row of its own -- what it decides becomes a proposal "
                  "(Tier 2) or an executor call (Tier 1) below, or nothing "
                  "at all."),
            WNode("gate", "gate", "Tier 2 -> card",
                  "approvals.propose(): the reasoner named an action Tier 2 "
                  "requires asking about. Evidence: the newest row in "
                  "proposals, any action.",
                  Evidence("lifecycle", "propose")),
            WNode("auto", "action", "Tier 1 -> executor",
                  "kernel.executor.execute(): the reasoner named a Tier 1 "
                  "action; it runs through the one path in and out "
                  "(invariant 1). Evidence: the newest execute.finish row.",
                  Evidence("lifecycle", "execute")),
            WNode("expire", "gate", "Silence expires",
                  "approvals.expire_stale(): a pending card past its "
                  "expires_at is marked expired, never run (invariant 5). "
                  "Evidence: the newest proposal.expired audit row.",
                  Evidence("lifecycle", "expire")),
        ),
        edges=(
            WEdge("trigger", "checks"),
            WEdge("checks", "findings"),
            WEdge("findings", "page", "HIGH/CRITICAL, always"),
            WEdge("findings", "reason", "NORMAL and up"),
            WEdge("reason", "gate", "Tier 2"),
            WEdge("reason", "auto", "Tier 1"),
            WEdge("gate", "expire", "unanswered"),
        ),
    )


def _morning_brief() -> Workflow:
    return _wf(
        "brief", "Morning brief", "cron",
        _brief_trigger_text,
        nodes=(
            WNode("trigger", "trigger", "BRIEF_HOUR local", ""),
            WNode("compose", "agent", "Gather every section",
                  "brief.send(): the diary, the inbox, decisions waiting, "
                  "each queried fresh at send time -- never a number reused "
                  "from an earlier read (invariant 6). No row of its own; "
                  "the composed brief is the store node below."),
            WNode("queue", "store", "Quiet-hours hold",
                  "A section held back by quiet hours waits in "
                  "brief_queue rather than being dropped. Evidence: the "
                  "newest brief_queue row.",
                  Evidence("lifecycle", "brief_queue")),
            WNode("store", "store", "Store the brief",
                  "INSERT INTO reports (kind='brief', sections, ...): the "
                  "rendered text, kept so a link a week later still opens "
                  "it. Evidence: reports where kind='brief'.",
                  Evidence("report", "brief")),
            WNode("channel", "channel", "Deliver",
                  "comms.notify(): wherever quiet hours and channel health "
                  "route it. The stored report's own delivered_via says "
                  "where it landed -- see the store node above."),
        ),
        edges=(
            WEdge("trigger", "compose"),
            WEdge("compose", "queue", "held sections"),
            WEdge("compose", "store"),
            WEdge("store", "channel"),
        ),
    )


def _reels_day() -> Workflow:
    return _wf(
        "reels", "AI reels day", "cron",
        _reels_trigger_text,
        nodes=(
            WNode("trigger", "trigger", "BRIEF_HOUR local, once a day", ""),
            WNode("overnight", "check", "Overnight read",
                  "instagram.check_overnight('stories'): the stories "
                  "account's moderation line, read fresh. Evidence: "
                  "checks/observations for instagram.stories_overnight.",
                  Evidence("check", "instagram.stories_overnight")),
            WNode("research", "action", "Format research",
                  "content.reel_research: Perplexity plus the account's own "
                  "insights, Tier 1 with a dry run and rollback.",
                  Evidence("action", "content.reel_research")),
            WNode("draft", "action", "Draft the reel",
                  "content.draft_reel: one draft per wanted format, Tier 1.",
                  Evidence("action", "content.draft_reel")),
            WNode("render", "action", "Render",
                  "content.render_video: OpenArt, under the monthly credit "
                  "cap, Tier 1.",
                  Evidence("action", "content.render_video")),
            WNode("publish_gate", "gate", "Publish card",
                  "content.publish_reel: Tier 2, one card per reel. Nothing "
                  "posts on silence -- the card simply expires.",
                  Evidence("proposal", "content.publish_reel")),
            WNode("preview", "channel", "Preview in the card's thread",
                  "preview_under_card(): the rendered MP4 uploaded into the "
                  "card's own Slack thread so Miro can watch before he "
                  "decides; on any upload failure, the public /media link "
                  "goes there instead. Per-card, so no single row stands "
                  "for 'the' preview."),
            WNode("digest", "store", "Daily digest",
                  "reels.send_digest(): once a day, stored so a restart "
                  "cannot send it twice. Evidence: reports where "
                  "kind='instagram_reels'.",
                  Evidence("report", "instagram_reels")),
            WNode("nudge", "channel", "17:00 nudge",
                  "reels.nudge(): if nothing was approved by "
                  "REELS_NUDGE_HOUR, one message asking for a decision -- "
                  "never a second post. Evidence: reports where "
                  "kind='instagram_reels_nudge'.",
                  Evidence("report", "instagram_reels_nudge")),
        ),
        edges=(
            WEdge("trigger", "overnight"),
            WEdge("overnight", "research"),
            WEdge("research", "draft"),
            WEdge("draft", "render"),
            WEdge("render", "publish_gate"),
            WEdge("publish_gate", "preview", "under the card"),
            WEdge("publish_gate", "digest"),
            WEdge("digest", "nudge", "if still unapproved at 17:00"),
        ),
    )


def _mail_followup() -> Workflow:
    return _wf(
        "mail", "Mail follow-up", "event",
        "the followups check (every 2h) or the signals cycle "
        "(SIGNAL_INTERVAL_S), whichever is due first",
        nodes=(
            WNode("trigger", "trigger", "Due check or signal cycle", ""),
            WNode("check", "check", "Follow-ups due",
                  "mail.check_followups: threads he sent with no reply "
                  "past business_days_since's floor. Evidence: "
                  "checks/observations for mail.followups.",
                  Evidence("check", "mail.followups")),
            WNode("classify", "agent", "Classify",
                  "mail.classify(): the local model reads the thread and "
                  "says needs_reply / client / bill / fyi -- Need.PRIVATE, "
                  "so this never reaches a cloud model. No row of its own; "
                  "see mail.draft_reply below for what follows a 'needs "
                  "reply' read."),
            WNode("draft", "action", "Draft a reply",
                  "mail.draft_reply: a Gmail draft, his own mailbox, "
                  "reversible -- Tier 1, nobody sees it until he sends it "
                  "himself.",
                  Evidence("action", "mail.draft_reply")),
            WNode("nudge_gate", "gate", "Nudge card",
                  "mail.draft_nudge: Tier 1 -- a draft is reversible -- but "
                  "still carded, because approvals.propose() is tier-blind "
                  "and asks every time regardless of tier. Not a Tier 2 "
                  "permission gate; the same wait either way.",
                  Evidence("proposal", "mail.draft_nudge")),
            WNode("ledger", "store", "Follow-up ledger",
                  "mail_followups: sent_at, nudged_at, resolved_at per "
                  "thread -- what open_followups() and the signal above "
                  "both read. Evidence: the newest nudged_at in the table.",
                  Evidence("lifecycle", "mail_nudged")),
            WNode("channel", "channel", "Notify",
                  "comms.notify(): the mailbox's own topic -- 'guardian', "
                  "'flycheap', or plain 'mail'."),
        ),
        edges=(
            WEdge("trigger", "check"),
            WEdge("check", "classify"),
            WEdge("classify", "draft", "needs_reply / client / bill"),
            WEdge("check", "nudge_gate", "3+ business days unanswered"),
            WEdge("nudge_gate", "ledger"),
            WEdge("draft", "channel"),
            WEdge("nudge_gate", "channel"),
        ),
    )


def _phone_approval() -> Workflow:
    return _wf(
        "phone", "Phone approval", "voice",
        "an inbound call from his own number, or a call ATLAS placed",
        nodes=(
            WNode("trigger", "trigger", "Call admitted",
                  "phone.admitted(): Twilio's signed direction and "
                  "caller/callee both match TWILIO_TO/TWILIO_FROM. Nobody "
                  "else is answered."),
            WNode("hear", "agent", "Hear the words",
                  "phone.respond(): APPROVE_RE / DECLINE_RE matched against "
                  "the transcript BEFORE anything reaches a model, so a "
                  "prompt can never reinterpret 'do it'. No row of its own."),
            WNode("gate", "gate", "Decide the pending card(s)",
                  "approvals.decide(pid, verdict, via='phone'): the cards "
                  "already read back on this call. The claim is one "
                  "conditional UPDATE, so a doubled 'do it' still runs "
                  "once. Evidence: the newest proposal decided via 'phone'.",
                  Evidence("lifecycle", "phone_decided")),
            WNode("execute", "action", "Executor runs it",
                  "kernel.executor.execute(): same single path as every "
                  "other approval (invariant 1) -- nothing about a phone "
                  "call gets its own door in.",
                  Evidence("lifecycle", "execute")),
            WNode("receipt", "channel", "Receipt",
                  "phone.receipt(): the decision is echoed to the "
                  "#decisions channel, so there is never a "
                  "'did I approve that?' the next morning."),
        ),
        edges=(
            WEdge("trigger", "hear"),
            WEdge("hear", "gate", "approve / decline phrase"),
            WEdge("gate", "execute", "approved"),
            WEdge("gate", "receipt"),
            WEdge("execute", "receipt"),
        ),
    )


def _linkedin_publish() -> Workflow:
    return _wf(
        "linkedin", "LinkedIn publish", "cron",
        "linkedin.sweep's own 12h Check interval; skipped early if the "
        "last sweep was clean within LINKEDIN_SWEEP_MIN_GAP_HOURS",
        nodes=(
            WNode("trigger", "trigger", "Sweep due", ""),
            WNode("sweep", "check", "Sweep the account",
                  "sweep_check(): runs the browser sweep and reports how it "
                  "went; it reads LinkedIn and writes ATLAS's own state "
                  "file, nothing on LinkedIn changes here. Evidence: "
                  "checks/observations for linkedin.sweep.",
                  Evidence("check", "linkedin.sweep")),
            WNode("watchdog", "check", "Sweep still alive?",
                  "agent_run(): a stalled sweep looks like a calm week -- "
                  "nothing posts, nothing reports -- so this watches how "
                  "long since the sweep last completed. Evidence: "
                  "checks/observations for linkedin.agent_run.",
                  Evidence("check", "linkedin.agent_run")),
            WNode("approve_gate", "gate", "Approve draft",
                  "linkedin.approve_draft: Tier 2 -- the card IS the "
                  "approval; a mismatched text_sha256 means the draft "
                  "changed since and the approval is refused.",
                  Evidence("proposal", "linkedin.approve_draft")),
            WNode("publish_gate", "gate", "Publish card",
                  "linkedin.publish_post: Tier 2 and irreversible -- a post "
                  "cannot be rolled back without deleting it, and deletion "
                  "is permanently denied (Tier 3).",
                  Evidence("proposal", "linkedin.publish_post")),
            WNode("publish", "action", "Publish + reshare",
                  "publish(): posts to the page and reshares to Miro's own "
                  "profile. Evidence: the newest execute.finish for "
                  "linkedin.publish_post.",
                  Evidence("action", "linkedin.publish_post")),
            WNode("blocked", "channel", "Items waiting on you",
                  "Open items with a blocked_by reach Miro as a signal; "
                  "nothing here is unattended -- each one names him as the "
                  "blocker."),
        ),
        edges=(
            WEdge("trigger", "sweep"),
            WEdge("trigger", "watchdog"),
            WEdge("sweep", "approve_gate", "drafts waiting"),
            WEdge("approve_gate", "publish_gate", "approved entry exists"),
            WEdge("publish_gate", "publish"),
            WEdge("sweep", "blocked"),
        ),
    )


def _nightshift() -> Workflow:
    return _wf(
        "nightshift", "Nightshift", "voice",
        "a chat or voice request naming the nightshift_run / "
        "nightshift_research tool",
        nodes=(
            WNode("trigger", "trigger", "Task handed over", ""),
            WNode("run", "action", "Hand to the sandbox",
                  "nightshift.run: Tier 1, a scratch branch in a listed "
                  "repo only -- anything else is refused. Dry run and "
                  "rollback (stop the container) both required.",
                  Evidence("action", "nightshift.run")),
            WNode("research", "action", "Research instead",
                  "nightshift.research: Perplexity deep research as a "
                  "night-shift job, Tier 1, filed for the brief.",
                  Evidence("action", "nightshift.research")),
            WNode("watch", "check", "Watch the container",
                  "check_jobs(): a job still running past its own bound is "
                  "reported, not just left running. Evidence: "
                  "checks/observations for nightshift.jobs.",
                  Evidence("check", "nightshift.jobs")),
            WNode("row", "store", "Job row",
                  "nightshift_jobs: status queued/running/done/failed/"
                  "stopped, the branch, the container, the log path. "
                  "Evidence: the newest row by id.",
                  Evidence("job", "")),
            WNode("merge_gate", "gate", "Merge?",
                  "nightshift.merge: Tier 2 and irreversible -- nothing "
                  "this module offers can undo a merge.",
                  Evidence("proposal", "nightshift.merge")),
            WNode("merge", "action", "Merge",
                  "merge(): fast-forwards the target branch. Evidence: the "
                  "newest execute.finish for nightshift.merge.",
                  Evidence("action", "nightshift.merge")),
        ),
        edges=(
            WEdge("trigger", "run"),
            WEdge("trigger", "research"),
            WEdge("run", "watch"),
            WEdge("watch", "row"),
            WEdge("row", "merge_gate", "status = done"),
            WEdge("merge_gate", "merge"),
        ),
    )


def _fixes() -> Workflow:
    return _wf(
        "fixes", "Degraded-check fixer", "cron",
        "every FIX_SWEEP_HOURS (default 6h) -- kernel.scheduler._maybe_fixes",
        nodes=(
            WNode("trigger", "trigger", "Sweep due", ""),
            WNode("lasting", "check", "Find lasting degraded/failing checks",
                  "fixes.lasting(): every check whose newest reading is "
                  "degraded or failing, with no OK reading since, for at "
                  "least FIX_LASTING_HOURS. UNKNOWN is left out on purpose "
                  "-- almost every UNKNOWN here is a setting only Miro can "
                  "supply. Evidence: the newest such observation, system-"
                  "wide.",
                  Evidence("lifecycle", "fixes_lasting")),
            WNode("retry_guard", "gate", "Already raised, unchanged?",
                  "quiet_reason(): a check raised inside FIX_RETRY_HOURS "
                  "(default 24h), acknowledged (acks), or unchanged since "
                  "its last post (same health, severity, last OK and "
                  "detail) inside FIX_REMIND_HOURS (default 7 days) is "
                  "skipped this sweep. Evidence: the newest "
                  "fixes.attempted audit row, which carries the fingerprint.",
                  Evidence("lifecycle", "fixes_attempted")),
            WNode("route", "agent", "Route to the roster",
                  "agents.route(): whichever specialist's brief fits the "
                  "check gets the prompt with the check's own evidence, "
                  "quoted as untrusted data. The run lands in agent_runs "
                  "under THAT specialist's name, not 'fixes' -- see the "
                  "Fleet page for who actually answered."),
            WNode("act", "action", "Call a catalog action, if one fixes it",
                  "The specialist calls an action from the closed catalog "
                  "through tools.invoke -> the executor, exactly like any "
                  "other agent tool call (invariant 2): a Tier 2 fix "
                  "becomes a card, a Tier 1 fix runs with its dry run and "
                  "rollback. Which action, if any, depends on the check -- "
                  "see that action's own node elsewhere on this canvas."),
            WNode("manual", "channel", "Say the manual step",
                  "When nothing in the catalog can fix it, the specialist "
                  "says the exact manual step in two sentences, posted to "
                  "the check's own topic -- a lasting degraded check never "
                  "sits there with nobody saying what to do about it."),
        ),
        edges=(
            WEdge("trigger", "lasting"),
            WEdge("lasting", "retry_guard"),
            WEdge("retry_guard", "route", "new, changed, or a weekly reminder"),
            WEdge("route", "act", "an action in the catalog fits"),
            WEdge("route", "manual", "nothing in the catalog fits"),
        ),
    )


def _action_lifecycle() -> Workflow:
    return _wf(
        "lifecycle", "Action lifecycle", "event",
        "any finding, signal, or chat/voice request that names an action",
        nodes=(
            WNode("trigger", "trigger", "Action named", ""),
            WNode("propose", "gate", "Propose",
                  "approvals.propose(): every write, Tier 1 or Tier 2, is a "
                  "row here first -- params validated against the action's "
                  "own declared params (invariant 2), vetoed combinations "
                  "and blocked lessons refused before the card exists. "
                  "Evidence: the newest proposal.created audit row.",
                  Evidence("lifecycle", "propose")),
            WNode("decide", "gate", "Approve / decline",
                  "approvals.decide(): a single conditional UPDATE claims "
                  "the card exactly once, however many times it is "
                  "submitted. Evidence: the newest decided proposal, any "
                  "action.",
                  Evidence("lifecycle", "decide")),
            WNode("expire", "gate", "Silence -> expired",
                  "expire_stale(): unanswered past its expires_at, marked "
                  "expired and never run -- silence is never consent "
                  "(invariant 5). Evidence: the newest proposal.expired "
                  "audit row.",
                  Evidence("lifecycle", "expire")),
            WNode("dry_run", "check", "Dry run",
                  "executor.preview() / execute()'s own dry run: what would "
                  "happen, read before Miro decides and again at the "
                  "moment of approval, since the world may have moved. "
                  "Tier 1 refuses to construct an Action without one "
                  "(invariant 8). Evidence: the newest execute.dry_run or "
                  "preview audit row.",
                  Evidence("lifecycle", "dry_run")),
            WNode("execute", "action", "Execute",
                  "kernel.executor.execute(): the one path in and out "
                  "(invariant 1) -- watch-only, the rate ceiling and the "
                  "permission decision are all checked here, in order, "
                  "every time. Evidence: the newest execute.finish row.",
                  Evidence("lifecycle", "execute")),
            WNode("rollback", "action", "Rollback",
                  "kernel.executor.rollback(): undoes a previous execution "
                  "by its token, gated exactly like execute -- Tier 1 "
                  "refuses to construct an Action without one (invariant "
                  "8), unless it is declared irreversible, which forces "
                  "Tier 2. Evidence: the newest rollback.finish row.",
                  Evidence("lifecycle", "rollback")),
        ),
        edges=(
            WEdge("trigger", "propose"),
            WEdge("propose", "decide"),
            WEdge("propose", "expire", "unanswered"),
            WEdge("decide", "dry_run", "approved (again, at approval time)"),
            WEdge("dry_run", "execute"),
            WEdge("execute", "rollback", "undone later"),
        ),
    )


def _reels_pipeline() -> Workflow:
    """Miro, 2026-09-27/28: the reels make and post themselves. Every tick,
    in this order, each step through the executor."""
    return _wf(
        "reels_pipeline", "Reels production", "cron",
        _reels_pipeline_trigger_text,
        nodes=(
            WNode("trigger", "trigger", "Every 30 minutes", ""),
            WNode("series", "action", "Next series part",
                  "content.draft_series_part: the series being made, planned "
                  "whole, one part after the last one posted; the finale ends "
                  "on THE END. content.plan_series queues the next when one "
                  "ends. Tier 1.",
                  Evidence("action", "content.draft_series_part")),
            WNode("series_render", "action", "Render the part",
                  "content.render_video: Veo dialogue, only with a whole "
                  "day's clips (VEO_DAILY_CLIPS, from midnight Pacific); "
                  "series parts and regular reels alternate days.",
                  Evidence("action", "content.render_video")),
            WNode("packages", "action", "Packaged reels",
                  "content.render_calendar_job: the next of Miro's 60 "
                  "packaged reels with the clips left -- banked clip by clip, "
                  "smoothed to 60 fps, assembled with a 4K master. Tier 1.",
                  Evidence("action", "content.render_calendar_job")),
            WNode("autopost", "action", "Post itself, past the checks",
                  "content.autopublish_reel: Tier 1 by Miro's ruling -- the "
                  "switch, two a day, the AI label, the brand guard, a "
                  "1080x1920 render with sound that is not silent.",
                  Evidence("action", "content.autopublish_reel")),
            WNode("held", "gate", "Held for Miro",
                  "content.publish_reel: a reel that failed a check becomes "
                  "a Tier 2 card, the reason first; nothing posts on silence.",
                  Evidence("proposal", "content.publish_reel")),
            WNode("receipt", "channel", "Receipt",
                  "Every post that went out on its own sends a note to "
                  "#instagram-reels with where it posted."),
        ),
        edges=(
            WEdge("trigger", "series"),
            WEdge("series", "series_render"),
            WEdge("series_render", "autopost"),
            WEdge("trigger", "packages", "the day's leftover clips"),
            WEdge("packages", "autopost"),
            WEdge("autopost", "receipt", "passed"),
            WEdge("autopost", "held", "failed a check"),
        ),
    )


def _series_episodes() -> Workflow:
    """Miro, 2026-09-28: "Veo makes the clips for the reel series, sends them
    to ATLAS, and ATLAS uploads them to all the platforms available." ATLAS's
    own pipeline owns the series (his choice, the same day); Code Chaos, made
    by the server's episode timers, finishes through them until Oct 2."""
    return _wf(
        "series_episodes", "Series episodes: Veo to every platform", "cron",
        "every 30 minutes (the reels tick); Code Chaos from the server's "
        "reel-hosp* timers, 07:03 UTC daily, until Oct 2",
        nodes=(
            WNode("tick", "trigger", "Reels tick",
                  "kernel.reels.calendar_tick, every 30 minutes, off the "
                  "scheduler loop."),
            WNode("part", "action", "Next episode's script",
                  "content.draft_series_part: the series being made, from its "
                  "written scripts in order, one part after the last one "
                  "posted; the finale ends on THE END.",
                  Evidence("action", "content.draft_series_part")),
            WNode("veo", "action", "Veo makes the clips",
                  "content.render_video: each shot on Veo, dialogue on "
                  "camera, only with the day's clips (VEO_DAILY_CLIPS, from "
                  "midnight Pacific). Every clip is logged in veo_usage, the "
                  "ledger both paths share.",
                  Evidence("action", "content.render_video")),
            WNode("timer", "trigger", "Server timer (Code Chaos)",
                  "reel-hosp* systemd timers on the server, 07:03 UTC, one "
                  "episode a day until Oct 2; they are switched off after."),
            WNode("host_veo", "action", "Veo clips (server job)",
                  "The episode job renders on Veo, records each clip in "
                  "veo_usage, cuts the 1080x1920 mp4 and copies it into "
                  "the import dir (data/app/import). Evidence: the newest veo_usage "
                  "row from either path.",
                  Evidence("lifecycle", "veo_clips")),
            WNode("import", "store", "ATLAS files it",
                  "python -m atlas.cli import-reel: refuses a file that is not "
                  "1080x1920, has no sound, is silent or too short, or whose "
                  "caption lacks the AI label; a part imported twice is "
                  "refused.",
                  Evidence("lifecycle", "imported")),
            WNode("autopost", "action", "Post everywhere",
                  "content.autopublish_reel through the executor: Instagram, "
                  "the Facebook Page and TikTok (TikTok with the #fyp caption "
                  "variant), past every automatic check.",
                  Evidence("action", "content.autopublish_reel")),
            WNode("held", "gate", "Held for Miro",
                  "Anything that fails a check, or a day whose automatic "
                  "posts are used, becomes a content.publish_reel card, the "
                  "reason first. Nothing posts on silence.",
                  Evidence("proposal", "content.publish_reel")),
            WNode("metrics", "check", "Views and reach",
                  "content.reel_metrics reads every posted reel's numbers, "
                  "each with the time it was read.",
                  Evidence("check", "content.reel_metrics")),
        ),
        edges=(
            WEdge("tick", "part"),
            WEdge("part", "veo"),
            WEdge("veo", "autopost", "checks passed"),
            WEdge("veo", "held", "a check failed"),
            WEdge("timer", "host_veo"),
            WEdge("host_veo", "import", "import-reel"),
            WEdge("import", "autopost", "checks passed"),
            WEdge("import", "held", "a check failed"),
            WEdge("autopost", "metrics"),
            WEdge("held", "metrics", "approved"),
        ),
    )


def _agent_shifts() -> Workflow:
    return _wf(
        "agent_shifts", "Agent shifts", "cron",
        "each READY agent once a day (AGENT_SHIFT_<NAME> hours; 0 = off)",
        nodes=(
            WNode("trigger", "trigger", "Scheduler tick",
                  "kernel.scheduler._maybe_agent_shifts, every 30 s: which "
                  "agents are due."),
            WNode("ready", "gate", "Readiness preflight",
                  "An agent runs only when every tool it needs is configured "
                  "and its model answers; a not-ready agent never runs and "
                  "says why once a day."),
            WNode("run", "agent", "The shift",
                  "The fixed shift prompt: call module_status on each module "
                  "the role covers, say in three sentences what needs Miro, "
                  "act only through catalog actions. Evidence: the newest "
                  "shift run in agent_runs.",
                  Evidence("lifecycle", "agent_shift")),
            WNode("card", "gate", "Tier 2 -> card",
                  "An action it names that needs asking becomes one card; the "
                  "same cause never raises a second while one waits.",
                  Evidence("lifecycle", "propose")),
            WNode("quiet", "channel", "Nothing needed",
                  "Most shifts end here: the three sentences are kept in "
                  "agent_runs and shown on the Agents map."),
        ),
        edges=(
            WEdge("trigger", "ready"),
            WEdge("ready", "run", "due and ready"),
            WEdge("run", "card", "names a Tier 2 action"),
            WEdge("run", "quiet", "nothing to do"),
        ),
    )


def _schedules() -> Workflow:
    """Every recurring job at a glance, so the whole week reads on one
    canvas. Cadences name their setting; the evidence is each job's own
    newest row, never a neighbour's."""
    return _wf(
        "schedules", "Every schedule", "cron",
        "the scheduler loop (every 30 s) plus the server's episode timers",
        nodes=(
            WNode("loop", "trigger", "ATLAS scheduler",
                  "kernel.scheduler.loop: one pass every 30 s; each job below "
                  "keeps its own clock."),
            WNode("host", "trigger", "Server timers",
                  "systemd on the server: the reel-hosp* Code Chaos timers, "
                  "07:03 UTC daily until Oct 2, then switched off."),
            WNode("checks", "check", "Checks -- every 30 s, per interval",
                  "Every module check on its own interval (5 min to 12 h).",
                  Evidence("lifecycle", "checks_ran")),
            WNode("brief", "channel", "Morning brief -- 07:00 New York",
                  "BRIEF_HOUR (default 7), once a day; a box that was down "
                  "still sends it when it comes back.",
                  Evidence("report", "brief")),
            WNode("review", "channel", "Daily review -- REVIEW_HOUR",
                  "Once a day in the evening (REVIEW_HOUR, 23:00 here).",
                  Evidence("report", "review")),
            WNode("weekly", "channel", "Weekly report -- WEEKLY_DAY",
                  "Once a week after the morning brief (Monday by default).",
                  Evidence("report", "weekly")),
            WNode("shifts", "agent", "Agent shifts -- daily each",
                  "Every READY agent, once a day; see the Agent shifts "
                  "workflow.",
                  Evidence("lifecycle", "agent_shift")),
            WNode("fixes", "action", "Fix sweep -- every 6 h",
                  "FIX_SWEEP_HOURS: checks degraded or failing for 2 h or "
                  "more get an incident card.",
                  Evidence("lifecycle", "fixes_attempted")),
            WNode("reels_tick", "action", "Reels tick -- every 30 min",
                  "Series parts, packaged reels, automatic posts; see Reels "
                  "production.",
                  Evidence("action", "content.render_calendar_job")),
            WNode("reels_day", "channel", "Reels day -- 07:00 New York",
                  "The AI-stories account's day, once, with its digest.",
                  Evidence("report", "instagram_reels")),
            WNode("episodes", "action", "Code Chaos episode -- 07:03 UTC",
                  "Until Oct 2: Veo on the server, imported, posted "
                  "everywhere; see Series episodes.",
                  Evidence("lifecycle", "imported")),
            WNode("series", "action", "Series parts -- every 30 min",
                  "ATLAS's own series engine, when the day's Veo clips allow; "
                  "see Series episodes.",
                  Evidence("action", "content.draft_series_part")),
            WNode("linkedin", "gate", "LinkedIn post -- daily per page",
                  "The next approved post for FlyCheap and Vault, one card "
                  "each posting day.",
                  Evidence("proposal", "linkedin.publish_post")),
            WNode("marketing", "action", "Marketing -- daily and weekly",
                  "Daily: experiments whose 48 h are up are measured. "
                  "Weekly: new experiments on platforms that are switched on."),
            WNode("judgment", "store", "Judgment bake-off -- 03:00 nightly",
                  "TOURNAMENT_HOUR: retrains the approval predictors when "
                  "there are ten new decisions."),
            WNode("upkeep", "store", "Upkeep -- hourly",
                  "Retention purge and the rhythm baseline, each hourly; "
                  "module signals on their own interval."),
        ),
        edges=(
            WEdge("loop", "checks"), WEdge("loop", "brief"),
            WEdge("loop", "review"), WEdge("loop", "weekly"),
            WEdge("loop", "shifts"), WEdge("loop", "fixes"),
            WEdge("loop", "reels_tick"), WEdge("loop", "reels_day"),
            WEdge("loop", "series"),
            WEdge("loop", "linkedin"), WEdge("loop", "marketing"),
            WEdge("loop", "judgment"), WEdge("loop", "upkeep"),
            WEdge("host", "episodes"),
        ),
    )


def _reels_pipeline_trigger_text() -> str:
    from . import reels
    return (f"every {reels.CALENDAR_TICK_MIN} minutes, off the scheduler loop "
            "(kernel.reels.calendar_tick)")


def _brief_trigger_text() -> str:
    from . import scheduler
    return f"{scheduler._brief_hour():02d}:00 local -- BRIEF_HOUR"


def _reels_trigger_text() -> str:
    from . import reels, scheduler
    return (f"{scheduler._brief_hour():02d}:00 local (BRIEF_HOUR), once a "
            f"day; the nudge at {reels.nudge_hour():02d}:00 "
            "(REELS_NUDGE_HOUR)")


def _community_channel() -> Workflow:
    """The AMSM channel (atlas/modules/community, docs/community-agent.md): a video
    goes from the owner's photos to two platforms. The first four stages
    happen on the owner's Mac and leave no row here, so they claim no time;
    from the render on, each node counts `community_posts` at its stage, as
    the Mac's sync and the community actions last wrote them."""
    return _wf(
        "community", "AMSM channel: photos to posts", "event",
        "a new item photographed at the bookstore; the Mac syncs its calendar "
        "with POST /api/community/progress",
        nodes=(
            WNode("photos", "trigger", "Photos",
                  "Five shots per item (front, spine or side, contents, scale, "
                  "presenter), vertical 1080x1920, on the Mac."),
            WNode("script", "action", "Script (EN / AR)",
                  "The 35-second structure; English, and Arabic in Egyptian "
                  "dialect. Counts posts still planned.",
                  Evidence("community", "planned")),
            WNode("voice", "action", "Voice",
                  "A warm British male narrator; no invented quotations, no "
                  "cloned clergy voices. On the Mac."),
            WNode("music", "action", "Coptic music",
                  "Traditional hymn melodies in the community's own renditions "
                  "(music/make_hymn_bg.py, music/mix_music.py); never a "
                  "ripped recording."),
            WNode("render", "store", "Render",
                  "The finished vertical video in music/output. Counts posts "
                  "rendered and reviewed, waiting for a slot.",
                  Evidence("community", "rendered")),
            WNode("review", "gate", "Owner's review",
                  "Every video is watched by the owner before it posts; the "
                  "posting actions refuse an unreviewed row. Counts videos "
                  "awaiting review.", Evidence("community", "awaiting_review")),
            WNode("facebook", "action", "Facebook schedule",
                  "community.schedule_facebook_reels: one Tier 2 card per batch, "
                  "at most 29 days ahead. Counts reels scheduled.",
                  Evidence("community", "facebook:scheduled")),
            WNode("youtube", "action", "YouTube schedule",
                  "community.schedule_youtube_shorts: one Tier 2 card per batch, "
                  "private with publishAt, within the daily upload budget. "
                  "Counts Shorts scheduled.", Evidence("community", "youtube:scheduled")),
            WNode("live", "channel", "Live",
                  "Public at its 9 AM or 6 PM New York slot. Counts posts "
                  "published.", Evidence("community", "published")),
            WNode("metrics", "store", "Metrics review",
                  "Reach, attention, trust, action and outcome, weekly; the "
                  "platform's own numbers in community_metrics.",
                  Evidence("community", "metrics")),
        ),
        edges=(
            WEdge("photos", "script"), WEdge("script", "voice"),
            WEdge("voice", "music"), WEdge("music", "render"),
            WEdge("render", "review"), WEdge("review", "facebook", "approved"),
            WEdge("facebook", "youtube"), WEdge("youtube", "live", "at its slot"),
            WEdge("live", "metrics", "weekly"),
        ),
    )


WORKFLOWS: tuple[Workflow, ...] = (
    _schedules(),
    _series_episodes(),
    _agent_shifts(),
    _scheduler_cycle(),
    _morning_brief(),
    _reels_day(),
    _reels_pipeline(),
    _community_channel(),
    _mail_followup(),
    _phone_approval(),
    _linkedin_publish(),
    _nightshift(),
    _fixes(),
    _action_lifecycle(),
)


# ------------------------------------------------------------- resolution

def _trigger_text(wf: Workflow) -> str:
    detail = wf.trigger_detail
    if callable(detail):
        try:
            return detail()
        except Exception as exc:  # noqa: BLE001
            log.warning("workflows: %s's trigger text failed: %s", wf.id, exc)
            return "(could not be read)"
    return detail


async def _safe_fetch_all(sql: str, *args: Any) -> list[Any] | None:
    try:
        return await fetch_all(sql, *args)
    except Exception as exc:  # noqa: BLE001
        log.warning("workflows: query failed, marking its nodes unknown: %s", exc)
        return None


async def _safe_fetch_one(sql: str, *args: Any) -> Any | None:
    try:
        return await fetch_one(sql, *args)
    except Exception as exc:  # noqa: BLE001
        log.warning("workflows: query failed, marking its nodes unknown: %s", exc)
        return "__unreadable__"


def _clean(text: Any) -> str:
    from ..modules._http import redact
    return redact(text) if text else ""


_HEALTH_OUTCOME = {"ok": OK, "degraded": DEGRADED, "failing": FAILED,
                   "unknown": UNKNOWN}

_CHECK_SQL = """
    SELECT DISTINCT ON (c.id) p.name AS module, c.name AS check,
           o.health, o.detail, o.at
      FROM checks c
      JOIN properties p ON p.id = c.property_id
      JOIN observations o ON o.check_id = c.id
     ORDER BY c.id, o.at DESC
"""


def _check_status(row: Any | None) -> NodeStatus:
    if row is None:
        return NodeStatus(None, NEVER, "no observation recorded yet")
    outcome = _HEALTH_OUTCOME.get(str(row["health"]), UNKNOWN)
    return NodeStatus(row["at"], outcome, _clean(row["detail"]))


def _action_status(row: Any | None) -> NodeStatus:
    if row is None:
        return NodeStatus(None, NEVER, "never run")
    event = row["event"]
    payload_raw = row["payload"]
    try:
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (
            payload_raw or {})
    except ValueError:
        payload = {}
    # The executor's audit rows wrap the result: {"event", "subject",
    # "data": {"ok", "detail", ...}}. Read at the top level, every
    # execute.finish lacked "ok" and every action node read FAILED
    # (2026-09-28: the reels tick, 28 of 28 successful, drawn red).
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if event == "execute.finish":
        ok = bool(payload.get("ok"))
        return NodeStatus(row["at"], OK if ok else FAILED,
                          _clean(payload.get("detail")))
    if event == "execute.error":
        return NodeStatus(row["at"], FAILED, _clean(payload.get("error")))
    if event == "execute.watch_only":
        return NodeStatus(row["at"], DEGRADED, "ATLAS_WATCH_ONLY refused it")
    if event == "execute.refused":
        return NodeStatus(row["at"], DEGRADED, _clean(payload.get("why")))
    # execute.rate_limited
    return NodeStatus(row["at"], DEGRADED, "the hourly rate ceiling refused it")


_PROPOSAL_OUTCOME = {
    "executed": OK, "approved": OK,
    "declined": DEGRADED, "expired": DEGRADED, "pending": DEGRADED,
    "suppressed": DEGRADED, "failed": FAILED,
}


def _proposal_status(row: Any | None) -> NodeStatus:
    if row is None:
        return NodeStatus(None, NEVER, "no card raised yet")
    status = str(row["status"])
    at = row["decided_at"] or row["created_at"]
    outcome = _PROPOSAL_OUTCOME.get(status, UNKNOWN)
    via = row["decided_via"]
    note = f"status: {status}" + (f" (via {via})" if via else "")
    return NodeStatus(at, outcome, note)


def _agent_status(row: Any | None) -> NodeStatus:
    if row is None:
        return NodeStatus(None, NEVER, "never run")
    model = row["model"]
    if model:
        return NodeStatus(row["at"], OK, f"{model} answered")
    return NodeStatus(row["at"], DEGRADED, "no model answered")


def _report_status(row: Any | None) -> NodeStatus:
    if row is None:
        return NodeStatus(None, NEVER, "never sent")
    via = row["delivered_via"]
    if via:
        return NodeStatus(row["at"], OK, f"delivered via {via}")
    return NodeStatus(row["at"], DEGRADED, "stored, but no channel took it")


_JOB_OUTCOME = {"done": OK, "failed": FAILED, "stopped": DEGRADED,
                "running": DEGRADED, "queued": DEGRADED}


def _job_status(row: Any | None) -> NodeStatus:
    if row is None:
        return NodeStatus(None, NEVER, "no nightshift job yet")
    status = str(row["status"])
    at = row["finished_at"] or row["at"]
    return NodeStatus(at, _JOB_OUTCOME.get(status, UNKNOWN), f"status: {status}")


# One tiny, single-purpose query per generic stage. Each is its own WHERE
# clause on a table already read elsewhere for a different key, so batching
# them into one query would trade five cheap LIMIT-1 reads for one query
# that cannot use an index either way -- not worth the complexity for a
# single-user dashboard.
_LIFECYCLE_SQL: dict[str, tuple[str, str]] = {
    "checks_ran": (
        "SELECT max(at) AS at FROM observations", "row exists"),
    "findings": (
        "SELECT max(at) AS at FROM findings", "row exists"),
    "paged": (
        "SELECT max(at) AS at FROM signals WHERE delivered", "row exists"),
    "propose": (
        "SELECT max(at) AS at FROM actions_log WHERE event = 'proposal.created'",
        "row exists"),
    "expire": (
        "SELECT max(at) AS at FROM actions_log WHERE event = 'proposal.expired'",
        "row exists"),
    "dry_run": (
        ("SELECT max(at) AS at FROM actions_log "
         "WHERE event IN ('execute.dry_run', 'preview')"),
        "row exists"),
    "execute": (
        "SELECT max(at) AS at FROM actions_log WHERE event = 'execute.finish'",
        "row exists"),
    "rollback": (
        "SELECT max(at) AS at FROM actions_log WHERE event = 'rollback.finish'",
        "row exists"),
    "fixes_lasting": (
        ("SELECT max(at) AS at FROM observations WHERE health IN "
         "('degraded', 'failing')"),
        "row exists"),
    "fixes_attempted": (
        "SELECT max(at) AS at FROM actions_log WHERE event = 'fixes.attempted'",
        "row exists"),
    "brief_queue": (
        "SELECT max(at) AS at FROM brief_queue", "row exists"),
    "veo_clips": (
        "SELECT max(at) AS at FROM veo_usage WHERE seconds > 0",
        "a Veo clip was drawn"),
    "imported": (
        "SELECT max(at) AS at FROM content_media WHERE provider = 'import'",
        "an episode was imported"),
    "agent_shift": (
        "SELECT max(at) AS at FROM agent_runs WHERE task LIKE 'Your shift:%'",
        "an agent's shift ran"),
}


def _sql_row_status(row: Any | None, note_if_present: str) -> NodeStatus:
    at = row["at"] if row is not None else None
    if at is None:
        return NodeStatus(None, NEVER, "no record yet")
    return NodeStatus(at, OK, note_if_present)


async def _decide_status() -> NodeStatus:
    row = await _safe_fetch_one(
        "SELECT status, decided_at FROM proposals WHERE decided_at IS NOT "
        "NULL ORDER BY decided_at DESC LIMIT 1")
    if row == "__unreadable__":
        return NodeStatus(None, UNKNOWN, "could not be read")
    if row is None:
        return NodeStatus(None, NEVER, "no decision yet")
    return NodeStatus(row["decided_at"],
                      _PROPOSAL_OUTCOME.get(str(row["status"]), UNKNOWN),
                      f"status: {row['status']}")


async def _phone_decided_status() -> NodeStatus:
    row = await _safe_fetch_one(
        "SELECT status, decided_at FROM proposals WHERE decided_via = "
        "'phone' ORDER BY decided_at DESC LIMIT 1")
    if row == "__unreadable__":
        return NodeStatus(None, UNKNOWN, "could not be read")
    if row is None:
        return NodeStatus(None, NEVER, "no proposal decided by phone yet")
    return NodeStatus(row["decided_at"],
                      _PROPOSAL_OUTCOME.get(str(row["status"]), UNKNOWN),
                      f"status: {row['status']}")


async def _mail_nudged_status() -> NodeStatus:
    row = await _safe_fetch_one("SELECT max(nudged_at) AS at FROM mail_followups")
    if row == "__unreadable__":
        return NodeStatus(None, UNKNOWN, "could not be read")
    at = row["at"] if row is not None else None
    if at is None:
        return NodeStatus(None, NEVER, "no thread nudged yet")
    return NodeStatus(at, OK, "a follow-up thread was nudged")


def _community_status(key: str, stages: list[Any] | None,
                   metrics: Any | None) -> NodeStatus:
    """How many AMSM posts sit at this stage now, and when the newest of
    them last moved. Zero is `never` (nothing here now), not `ok`."""
    if key == "metrics":
        if metrics == "__unreadable__":
            return NodeStatus(None, UNKNOWN, "could not be read")
        n = int(metrics["n"]) if metrics is not None else 0
        if n == 0 or metrics["at"] is None:
            return NodeStatus(None, NEVER, "no platform numbers recorded yet")
        return NodeStatus(metrics["at"], OK, f"{n} daily reading(s)")
    if stages is None:
        return NodeStatus(None, UNKNOWN, "could not be read")
    wanted = [part.partition(":") for part in key.split("|")]
    n, at = 0, None
    for r in stages:
        for a, sep, b in wanted:
            platform, status = (a, b) if sep else (None, a)
            if r["status"] == status and platform in (None, r["platform"]):
                n += int(r["n"])
                if r["at"] is not None and (at is None or r["at"] > at):
                    at = r["at"]
    if n == 0 or at is None:
        return NodeStatus(None, NEVER, "no post at this stage now")
    return NodeStatus(at, OK, f"{n} post(s) at this stage")


async def _lifecycle_status(key: str) -> NodeStatus:
    if key == "decide":
        return await _decide_status()
    if key == "phone_decided":
        return await _phone_decided_status()
    if key == "mail_nudged":
        return await _mail_nudged_status()
    spec = _LIFECYCLE_SQL.get(key)
    if spec is None:
        return NodeStatus(None, UNKNOWN, f"no probe registered for {key!r}")
    sql, note = spec
    row = await _safe_fetch_one(sql)
    if row == "__unreadable__":
        return NodeStatus(None, UNKNOWN, "could not be read")
    return _sql_row_status(row, note)


async def resolve_all(catalog: Sequence[Workflow]) -> dict[str, NodeStatus]:
    """Every evidenced node's last run, batched one query per table rather
    than one per node. A node with `evidence=None` is left out of the
    result entirely -- the caller treats a missing id as `never`, with no
    query to have failed."""
    all_nodes = [(wf.id, n) for wf in catalog for n in wf.nodes
                 if n.evidence is not None]
    out: dict[str, NodeStatus] = {}

    check_nodes = [n for _, n in all_nodes if n.evidence.source == "check"]
    if check_nodes:
        rows = await _safe_fetch_all(_CHECK_SQL)
        by_key = ({f"{r['module']}.{r['check']}": r for r in rows}
                  if rows is not None else None)
        for n in check_nodes:
            if rows is None:
                out[n.id] = NodeStatus(None, UNKNOWN, "could not be read")
            else:
                out[n.id] = _check_status(by_key.get(n.evidence.key))

    action_nodes = [n for _, n in all_nodes if n.evidence.source == "action"]
    if action_nodes:
        names = sorted({n.evidence.key for n in action_nodes})
        rows = await _safe_fetch_all(
            """SELECT DISTINCT ON (subject) subject, event, payload, at
                 FROM actions_log
                WHERE subject = ANY($1::text[]) AND event = ANY($2::text[])
                ORDER BY subject, at DESC""",
            names, list(_ACTION_EVENTS))
        by_key = {r["subject"]: r for r in rows} if rows is not None else None
        for n in action_nodes:
            if rows is None:
                out[n.id] = NodeStatus(None, UNKNOWN, "could not be read")
            else:
                out[n.id] = _action_status(by_key.get(n.evidence.key))

    proposal_nodes = [n for _, n in all_nodes if n.evidence.source == "proposal"]
    if proposal_nodes:
        names = sorted({n.evidence.key for n in proposal_nodes})
        rows = await _safe_fetch_all(
            """SELECT DISTINCT ON (action) action, status, decided_at,
                      created_at, decided_via
                 FROM proposals
                WHERE action = ANY($1::text[])
                ORDER BY action, created_at DESC""",
            names)
        by_key = {r["action"]: r for r in rows} if rows is not None else None
        for n in proposal_nodes:
            if rows is None:
                out[n.id] = NodeStatus(None, UNKNOWN, "could not be read")
            else:
                out[n.id] = _proposal_status(by_key.get(n.evidence.key))

    agent_nodes = [n for _, n in all_nodes if n.evidence.source == "agent"]
    if agent_nodes:
        names = sorted({n.evidence.key for n in agent_nodes})
        rows = await _safe_fetch_all(
            """SELECT DISTINCT ON (agent) agent, model, provider, outcome, at
                 FROM agent_runs
                WHERE agent = ANY($1::text[])
                ORDER BY agent, at DESC""",
            names)
        by_key = {r["agent"]: r for r in rows} if rows is not None else None
        for n in agent_nodes:
            if rows is None:
                out[n.id] = NodeStatus(None, UNKNOWN, "could not be read")
            else:
                out[n.id] = _agent_status(by_key.get(n.evidence.key))

    report_nodes = [n for _, n in all_nodes if n.evidence.source == "report"]
    if report_nodes:
        kinds = sorted({n.evidence.key for n in report_nodes})
        rows = await _safe_fetch_all(
            """SELECT DISTINCT ON (kind) kind, at, delivered_via
                 FROM reports
                WHERE kind = ANY($1::text[])
                ORDER BY kind, at DESC""",
            kinds)
        by_key = {r["kind"]: r for r in rows} if rows is not None else None
        for n in report_nodes:
            if rows is None:
                out[n.id] = NodeStatus(None, UNKNOWN, "could not be read")
            else:
                out[n.id] = _report_status(by_key.get(n.evidence.key))

    job_nodes = [n for _, n in all_nodes if n.evidence.source == "job"]
    if job_nodes:
        row = await _safe_fetch_one(
            "SELECT status, at, finished_at FROM nightshift_jobs "
            "ORDER BY id DESC LIMIT 1")
        for n in job_nodes:
            if row == "__unreadable__":
                out[n.id] = NodeStatus(None, UNKNOWN, "could not be read")
            else:
                out[n.id] = _job_status(row)

    community_nodes = [n for _, n in all_nodes if n.evidence.source == "community"]
    if community_nodes:
        stages = await _safe_fetch_all(
            """SELECT platform, status, count(*) AS n, max(updated_at) AS at
                 FROM community_posts GROUP BY platform, status""")
        metrics = (await _safe_fetch_one(
            "SELECT count(*) AS n, max(read_at) AS at FROM community_metrics")
            if any(n.evidence.key == "metrics" for n in community_nodes) else None)
        for n in community_nodes:
            out[n.id] = _community_status(n.evidence.key, stages, metrics)

    lifecycle_nodes = [n for _, n in all_nodes if n.evidence.source == "lifecycle"]
    for n in lifecycle_nodes:
        out[n.id] = await _lifecycle_status(n.evidence.key)

    return out


def serialise(wf: Workflow, statuses: dict[str, NodeStatus]) -> dict[str, Any]:
    """One workflow as the API shape: id, title, trigger, nodes, edges, and
    a `last_run` summary -- the most recently run node's own time and
    outcome, or `never` when nothing in this workflow has a row yet."""
    nodes = []
    best: NodeStatus | None = None
    for n in wf.nodes:
        st = statuses.get(n.id, NodeStatus(None, NEVER, "not measured here"))
        nodes.append({
            "id": n.id, "kind": n.kind, "label": n.label, "detail": n.detail,
            "last_at": st.at.isoformat() if st.at else None,
            "last_outcome": st.outcome, "note": st.note,
        })
        if st.at is not None and (best is None or st.at > best.at):
            best = st
    return {
        "id": wf.id, "title": wf.title, "trigger": wf.trigger,
        "trigger_detail": _trigger_text(wf),
        "nodes": nodes,
        "edges": [{"from": e.frm, "to": e.to, "label": e.label} for e in wf.edges],
        "last_run": ({"at": best.at.isoformat(), "outcome": best.outcome}
                     if best is not None else {"at": None, "outcome": NEVER}),
    }


async def read_all() -> list[dict[str, Any]]:
    """Every workflow in the catalog, serialised with a fresh read of its
    evidence. What `GET /api/workflows` adds under `"workflows"`."""
    statuses = await resolve_all(WORKFLOWS)
    return [serialise(wf, statuses) for wf in WORKFLOWS]
