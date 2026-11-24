"""One specialist agent per business.

The role agents in `agents.py` (ops, growth, commerce, ...) cut across every
property the owner owns. That is right for infrastructure or content, where the
same judgement applies everywhere. It is wrong for "how is inroots doing" —
that question has one true owner, and routing it through commerce (which also
answers for Fiverr and ExampleCo) or through the general analyst dilutes both
the answer and the accountability for it.

So every module in `atlas/modules/` that is a business, rather than a
capability, gets its own agent here: same name as the module, own system
prompt built from that module's docstring, its own scoped tools, its own
memory namespace. Nothing about the kernel changes — `register_all()` builds
ordinary `Agent` objects and hands them to `agents.register()`, the same path
every role agent already goes through.

Task 53: each business also owns its model. the owner's ask was "Hermes agentic
agents for each business" — read narrowly, on purpose. Hermes (Nous
Research's tool-calling model, served through OpenRouter) is the brain every
business agent reasons with by default; the Hermes Agent framework itself
stays out. There is exactly one place a tool call turns into an outside-world
effect (`kernel/executor.py::execute`, invariant 1), and that does not change
because the model choosing which tool to name changed. `resolved_model()`
below is the only place a business's `"provider:model"` pin is decided.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from .agents import BROWSER_READS, PERPLEXITY_READS, Agent, register
from .llm import Need

log = logging.getLogger("atlas.businesses")

# Every non-PRIVATE business agent runs on this model unless
# `ATLAS_AGENT_MODEL_<NAME>` or the business's own `model` field says
# otherwise. Qwen3 235B (2507), served through OpenRouter: it calls tools
# and holds a system prompt tightly, which is what a business agent scoped to
# "answer for {title} only" (STANDING_RULES, below) needs.
# Was Hermes 4 405B until 2026-09-28, when OpenRouter's only provider for it
# stopped serving tool calls ("No endpoints found that support tool use", a
# 404 on every shift); no Hermes model on OpenRouter serves tools now.
DEFAULT_MODEL = "openrouter:qwen/qwen3-235b-a22b-2507"

# Which PRIVATE businesses have already been warned about an ignored model
# pin, so the warning is once per name per process rather than once per call.
_WARNED_PRIVATE_MODEL: set[str] = set()

# Appended, verbatim, to every business agent's system prompt after its `what`
# paragraph. It is what keeps a business agent from drifting into another
# business's numbers or into acting rather than proposing.
STANDING_RULES = (
    "You answer for {title} only. Every number you say comes from a tool "
    "call made in this turn, spoken with the time it was read. If a tool "
    "cannot reach the source, say the source is unreachable — never "
    "estimate. You propose actions by name from your tools; you cannot run "
    "them. When the owner asks what to handle first, rank by what is measured, "
    "not by what sounds important."
)

# Every business agent gets these, whatever else it holds: the two tools this
# task adds (module_status, module_brief) plus the general observation-store
# reads every data-answering agent already has.
BASE_READS: frozenset[str] = frozenset(
    {"module_status", "module_brief", "read_metrics", "read_history", "sql_query"}
)


@dataclass(frozen=True, slots=True)
class Business:
    name: str                       # agent name == module name
    title: str
    what: str                       # one paragraph, from the module's docstring
    kpis: tuple[str, ...]           # check names that are this business's numbers
    reads: frozenset[str]           # read tools, always BASE_READS plus its own
    writes: frozenset[str]          # write tools naming this module's own actions
    memory: tuple[str, ...]         # memory namespaces, its own first
    handoff: frozenset[str]         # role agents it may consult
    need: Need = Need.REASON
    # "provider:model" (see llm.PIN_PROVIDERS), or None to take the Hermes
    # default. Ignored for a Need.PRIVATE business — see resolved_model().
    model: str | None = None

    def brief(self) -> str:
        """Short line for the router menu — the `what` paragraph is too long
        to repeat fifteen times in one classification prompt."""
        first, _, _rest = self.what.partition(". ")
        return first.strip().rstrip(".") + "."

    def system(self) -> str:
        base = f"{self.what}\n\n{STANDING_RULES.format(title=self.title)}"
        if self.name == "agency":
            # The agency agent talks to leads: the whole playbook, price
            # boundaries included (`modules/agency/playbook.py`).
            from .agents import sales_guidance
            try:
                from ..modules.agency.playbook import guidance
                extra = guidance("full")
            except Exception:                           # noqa: BLE001
                extra = sales_guidance()
            if extra:
                base = f"{base}\n\n{extra}"
        return base


BUSINESSES: tuple[Business, ...] = (
    Business(
        name="inroots",
        title="inRoots",
        what=(
            "inRoots is the attendance and standup platform at status.internal.example. "
            "It watches whether the service and its Matrix bot are up, disk "
            "and memory on the host, the 90-day purge job that keeps "
            "http_log from growing unbounded and unnoticed, open bot "
            "errors, the daily AI budget, and whether every scheduled job "
            "still has a next run time. Its old nudge action — messaging "
            "colleagues about missing check-ins — is gone for two reasons "
            "at once: the endpoint it called never existed, and a "
            "read-scoped k9_ token is refused on every non-GET before the "
            "route even runs, so it is not coming back as a workaround "
            "either. Posting into attendance or standup rooms stays "
            "something only the owner does."
        ),
        kpis=("up", "http_log_retention", "open_errors"),
        reads=BASE_READS | {"http_probe"},
        writes=frozenset(),
        memory=("inroots", "incidents"),
        handoff=frozenset({"analyst", "ops"}),
    ),
    Business(
        name="cloudco",
        title="CloudCo",
        what=(
            "CloudCo is the account ATLAS itself runs in. It watches whether "
            "its own credentials are accepted, whether every instance is "
            "running, whether anything exists to restore an instance from, "
            "whether every billed server is visible to it, and account "
            "balance. A rejected or disabled credential comes back as an "
            "HTTP 500 with CloudCo's own error envelope, so the body has to "
            "be read rather than trusting the status code — otherwise "
            "'your credentials are wrong' reads as 'CloudCo is down', which "
            "sends the owner to look at the wrong thing. The only action is "
            "taking a snapshot, which is additive and cannot take an "
            "instance down; restart, stop and resize are deliberately left "
            "out, because a mistake against the account doing the "
            "monitoring takes out the monitoring itself."
        ),
        kpis=("auth", "instances", "balance"),
        reads=BASE_READS,
        writes=frozenset(),
        memory=("cloudco", "infra"),
        handoff=frozenset({"ops"}),
    ),
    Business(
        name="exampleco",
        title="ExampleCo",
        what=(
            "ExampleCo is the owner's eBay reselling of IT and networking "
            "hardware. It watches active listings, orders awaiting "
            "fulfilment, and listings that have sat unsold for more than 60 "
            "days — usually a pricing problem. Every price comparison is "
            "read-only and always against completed sale prices, never "
            "asking prices: asking prices on used enterprise gear are "
            "fiction, sold prices are the market."
        ),
        kpis=("listings", "stale_listings", "orders"),
        reads=BASE_READS | {"web_search"},
        writes=frozenset(),
        memory=("exampleco", "pricing", "inventory"),
        handoff=frozenset({"commerce", "growth"}),
    ),
    Business(
        name="fiverr",
        title="Fiverr",
        what=(
            "Fiverr is the owner's freelance gig business. Fiverr fronts every "
            "seller page with PerimeterX, which fingerprints and blocks the "
            "automated browser regardless of whose session or IP drives "
            "it, so the session check honestly reports unmeasured rather "
            "than reading a wall of zeros as a clear inbox — real signal "
            "arrives through Fiverr's own notification email instead. "
            "Response speed drives gig ranking, so an unanswered buyer "
            "message is urgent however trivial its content. Replies are "
            "always drafted and never sent: a bot reply to a buyer is both "
            "a policy risk and a reputation risk on the thing the gig's "
            "ranking depends on."
        ),
        kpis=("session", "unanswered_messages", "active_orders"),
        reads=BASE_READS,
        writes=frozenset(),
        memory=("fiverr", "buyers"),
        handoff=frozenset({"commerce"}),
    ),
    Business(
        name="agency",
        title="Agency",
        what=(
            "Agency finds local businesses with a bad website and makes the "
            "case in one sentence a shop owner can check for themselves. It "
            "watches Google Places API spend against a hard monthly cap and "
            "how many prospected leads are sitting with no decision made on "
            "them yet, across the whole pipeline: prospects found through "
            "Places, each one audited once against eight qualifying "
            "findings and cached for 30 days, a heuristic rank kept fresh "
            "over the open leads, a mockup built over the GetLayers MCP, "
            "outreach drafted with the audit finding first and sent only as "
            "a Tier 2 proposal against a suppression list and a daily cap, "
            "and a site built and deployed to a client's own production "
            "only on the owner's word. Buying the domain is the one step this "
            "never does itself — that is money out, and permanently "
            "denied whatever the pipeline otherwise clears."
        ),
        kpis=("prospect_budget", "leads_waiting"),
        reads=(BASE_READS | {"web_fetch", "web_search"}
               | PERPLEXITY_READS | BROWSER_READS),
        writes=frozenset({"agency_deploy_site"}),  # outreach is a card, not a tool
        memory=("agency", "leads"),
        handoff=frozenset({"growth", "analyst"}),
    ),
    Business(
        name="school",
        title="College (the university, Fall 2026)",
        what=(
            "School is the owner's college term at the university, read through the two "
            "doors Brightspace leaves open without a login: the calendar "
            "subscription feed, which carries every course's due dates, and "
            "the instant notification emails for grades, news, assignments "
            "and updates that land in his Gmail. A Valence API path exists "
            "but stays unmeasured until the university grants it. Each course has a "
            "work kind he confirmed — PHY 120 handwritten, MTH 311 on "
            "WebWork, CSC 221 and CSC 326 as files, PHY 121 as a typed lab "
            "report. ATLAS is his study partner, never his ghostwriter "
            "(the university's Academic Integrity Policy): it plans, explains, quizzes "
            "and gives hints and scaffolds; the owner writes, types and submits "
            "his own work. Nothing is ever submitted to Brightspace, and his "
            "coursework stays on the box."
        ),
        kpis=("calendar_feed", "notifications", "grades"),
        # No Perplexity tools: a PRIVATE agent never holds a tool that sends
        # text off the box. `school.draft_work` asks for references in code,
        # on a topic derived from the title, never the item text.
        reads=BASE_READS | {"school_upcoming", "school_grades", "school_alerts",
                            "vault_recall"},
        writes=frozenset({"school_draft_work", "school_set_course"}),
        memory=("school", "commitments"),
        handoff=frozenset({"scheduler", "concierge"}),
        need=Need.PRIVATE,
    ),
)

BY_NAME: dict[str, Business] = {b.name: b for b in BUSINESSES}


def resolved_model(biz: Business) -> str | None:
    """The `"provider:model"` pin this business's agent runs on.

    Resolution order: `ATLAS_AGENT_MODEL_<NAME>` (the owner's own override, upper-
    cased business name) beats `Business.model` beats `DEFAULT_MODEL` — every
    non-PRIVATE business that says nothing runs Hermes 4 405B on OpenRouter.

    A `Need.PRIVATE` business (school) is never handed a cloud model: PRIVATE
    work already never reaches a cloud provider (`think()` branches to
    `_think_private` before any pin is even looked at), but the pin is
    refused here too, so a PRIVATE agent's `Agent.model` is always `None`
    rather than carrying a value nothing will ever honour. Warned once per
    business per process, and only when a pin was actually set — school
    saying nothing is the ordinary case and not worth a log line.
    """
    override = os.getenv(f"ATLAS_AGENT_MODEL_{biz.name.upper()}")
    pin = override or biz.model
    if biz.need is Need.PRIVATE:
        if pin and biz.name not in _WARNED_PRIVATE_MODEL:
            _WARNED_PRIVATE_MODEL.add(biz.name)
            log.warning(
                "%s is a Need.PRIVATE business; its model pin %r is ignored — "
                "PRIVATE work runs on the local model only", biz.name, pin)
        return None
    return pin or DEFAULT_MODEL


def register_all() -> None:
    """Build one Agent per Business and add it to the roster."""
    for biz in BUSINESSES:
        register(Agent(
            name=biz.name,
            title=biz.title,
            brief=biz.brief(),
            system=biz.system(),
            tools=set(biz.reads) | set(biz.writes),
            need=biz.need,
            memory_scope=biz.memory,
            handoff=set(biz.handoff),
            model=resolved_model(biz),
        ))
