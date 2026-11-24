"""The contracts every ATLAS module implements.

This file is the operating system's system call table. The kernel knows only
what is defined here; it has no knowledge of flycheap, Fiverr, Gmail or any
other domain. Anything domain-specific lives in a module that implements
`Module` and is discovered at boot.

Rules enforced by the kernel, not by convention:
  * A Check may only read. It returns an Observation and nothing else.
  * An Action declares its Tier in code. The LLM cannot choose or change it.
  * An Action must declare a rollback, or be marked explicitly irreversible
    (which forces it to at least Tier 2).
"""
from __future__ import annotations

import enum
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class Tier(enum.IntEnum):
    """Permission tier. Assigned in code. Never inferred at runtime."""

    AUTO = 1      # reversible, invisible to outsiders, no money
    ASK = 2       # externally visible or hard to undo
    NEVER = 3     # hard denied; no instruction can reach these


class Severity(enum.IntEnum):
    INFO = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3   # the only level that breaks quiet hours


class Health(enum.StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILING = "failing"
    UNKNOWN = "unknown"      # a check that could not run: never reported as OK


@dataclass(frozen=True, slots=True)
class Observation:
    """One measurement. Immutable. This is the only thing a Check may produce."""

    check: str
    health: Health
    value: float | None = None          # the number the baseline tracks
    unit: str | None = None
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    # UTC, always. Baselines bucket by hour-of-week, and local time makes
    # that arithmetic wrong twice a year.
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_actionable(self) -> bool:
        return self.health in (Health.DEGRADED, Health.FAILING)


@dataclass(frozen=True, slots=True)
class Check:
    """A read-only probe. `run` must have no side effects on the outside world."""

    name: str
    run: Callable[[], Awaitable[Observation]]
    interval: timedelta
    severity_on_fail: Severity = Severity.NORMAL
    description: str = ""
    # Baseline comparison: flag when value drifts this many sigma from the
    # trailing same-hour-of-week mean. None disables statistical detection.
    baseline_sigma: float | None = 3.0
    timeout: timedelta = timedelta(seconds=30)


class FailureClass(enum.StrEnum):
    """The plan's failure classes (Reliable Automation plan, "Failure
    classification"). The class decides the default response; a verifier
    names one whenever the state is not verified and the cause is known,
    and `executor.execute` names one on each of its own refusals.

    Defined here, beside `ActionResult`, so the contract can carry it;
    `kernel.verify` re-exports it unchanged."""

    VALIDATION = "validation"                   # block; name the bad field
    AUTH = "auth"                               # stop the capability; owner action
    DISABLED = "disabled"                       # skip as intended; not an outage
    TRANSIENT = "transient"                     # bounded retry with backoff
    TIMEOUT_AFTER_DISPATCH = "timeout_after_dispatch"   # reconcile before repeating
    BUDGET = "budget"                           # checkpoint; bounded next step
    STALE = "stale"                             # refresh or report unknown
    QUALITY = "quality"                         # reject the artifact
    POLICY = "policy"                           # no bypass
    SECURITY = "security"                       # restrict and escalate


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    rollback_token: str | None = None
    # Why it failed, when the cause is known (RA-D1). None means unclassified,
    # never "fine": a caller that needs a class and finds None must take the
    # cautious path. Last, and defaulted, so every existing construction --
    # positional or keyword -- is unchanged.
    failure_class: FailureClass | None = None
    # Set by `executor.execute` alone (RA-B3): False when it refused before
    # the action body was invoked, so nothing reached a provider; True once
    # `run()` was called, whatever it returned or raised. None means the
    # result did not come through `execute` (a dry run, a rollback, a test
    # double), and a caller must not read it either way.
    dispatched: bool | None = None


_HTTP_STATUS = re.compile(r"\bHTTP (\d{3})\b")
_AUTH_STATUSES = frozenset({401, 403})
_TRANSIENT_STATUSES = frozenset({429, 502, 503})
_TIMEOUT_STATUSES = frozenset({408, 504})
_VALIDATION_STATUSES = frozenset({400, 422})


def _status_of(cause: object) -> int | None:
    """An HTTP status carried by an exception (httpx's `.response`, or a
    bare `.status_code`), without importing any HTTP client."""
    for holder in (getattr(cause, "response", None), cause):
        code = getattr(holder, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _is_timeout(exc: BaseException) -> bool:
    """`TimeoutError` (asyncio's too), or any class named like one --
    httpx's `TimeoutException` is not a `TimeoutError`."""
    return isinstance(exc, TimeoutError) or any(
        "Timeout" in klass.__name__ for klass in type(exc).__mro__)


def _by_status(code: int, after_dispatch: bool) -> FailureClass | None:
    if code in _AUTH_STATUSES:
        return FailureClass.AUTH
    if code in _TRANSIENT_STATUSES:
        return FailureClass.TRANSIENT
    if code in _TIMEOUT_STATUSES:
        return (FailureClass.TIMEOUT_AFTER_DISPATCH if after_dispatch
                else FailureClass.TRANSIENT)
    if code in _VALIDATION_STATUSES:
        return FailureClass.VALIDATION
    return None


def classify_failure(cause: BaseException | int | str | None, *,
                     after_dispatch: bool = False) -> FailureClass | None:
    """The failure class of a common refusal or exception shape, or None.

    `cause` is an exception, an HTTP status code, or a one-line detail such
    as `executor._brief` / `_http.explain` write ("HTTP 401 from ...",
    "timed out"). `after_dispatch` says whether the request may already have
    reached the provider: a timeout before dispatch is transient, after it
    the effect is unknown and must be reconciled before any repeat.

        401 / 403            -> auth
        429 / 502 / 503      -> transient
        408 / 504, timeouts  -> transient, or timeout_after_dispatch
        400 / 422            -> validation
        connection refused   -> transient (nothing was sent)

    Anything else is None -- never a guessed "transient", which would have a
    caller retry an effect that may have happened. Pure: no I/O, no HTTP
    client import.
    """
    if cause is None or isinstance(cause, bool):
        return None
    if isinstance(cause, int):
        return _by_status(cause, after_dispatch)
    if isinstance(cause, str):
        match = _HTTP_STATUS.search(cause)
        if match:
            return _by_status(int(match.group(1)), after_dispatch)
        text = cause.lower()
        if "timed out" in text or "timeout" in text:
            return _by_status(408, after_dispatch)
        if "rate limited" in text:
            return FailureClass.TRANSIENT
        return None
    code = _status_of(cause)
    if code is not None:
        return _by_status(code, after_dispatch)
    if _is_timeout(cause):
        return _by_status(408, after_dispatch)
    if isinstance(cause, ConnectionRefusedError) or any(
            klass.__name__ == "ConnectError" for klass in type(cause).__mro__):
        return FailureClass.TRANSIENT
    return None


@dataclass(frozen=True, slots=True)
class Action:
    """Something ATLAS can do. The catalog is closed: the reasoner picks from
    these, and can never construct a new one."""

    name: str                       # "flycheap.regenerate_affiliate_links"
    tier: Tier
    summary: str
    run: Callable[..., Awaitable[ActionResult]]
    # A dry run that reports what would change without changing it. Required
    # for every Tier 1 action; the kernel refuses to auto-run without it.
    dry_run: Callable[..., Awaitable[ActionResult]] | None = None
    rollback: Callable[[str], Awaitable[ActionResult]] | None = None
    irreversible: bool = False
    params: dict[str, str] = field(default_factory=dict)   # name -> description
    # Stop repeating a fix that is not fixing anything.
    max_per_hour: int = 3
    # Where the approval card lands. By default the namespace ("cloudhost" for
    # cloudhost.restart_instance); a module whose actions concern several
    # estates names the parameter that says which -- "page" for a LinkedIn
    # post, so a FlyCheap post is discussed in the FlyCheap channel.
    topic_param: str | None = None
    # A card is normally one line plus a Details button. When the thing
    # being approved IS text -- a post, a draft -- the text is the decision
    # and the card carries it whole.
    verbose_card: bool = False
    # Where the card lands when neither the namespace nor one parameter's
    # bare value says it: a pure function of the params, returning a topic
    # (or "" for the namespace). `instagram.publish` sends the stories
    # account's cards to `instagram_stories` while the main account's stay
    # on `instagram`, exactly where they always were. Overrides topic_param.
    topic_for: Callable[[dict[str, Any]], str] | None = None
    # A pure, synchronous refusal of a parameter COMBINATION, asked by
    # `approvals.propose()` before the card exists: a reason string refuses,
    # None passes. Never a network call -- that is the dry run's job, and
    # the dry run still runs on approval. It exists because some rules are
    # about which values go together (a travel post on the AI-stories
    # account), and a card that can only be refused on approval should
    # never be raised.
    vet: Callable[[dict[str, Any]], str | None] | None = None
    # An action that can take minutes (a reel posted to three platforms,
    # each waiting on the platform): `approvals.decide` claims the card
    # atomically and answers at once, runs it as a bounded background task,
    # and posts its receipts to the card's thread. Slack's 3 s interaction
    # window and a phone turn never wait on it.
    runs_in_background: bool = False
    # Proposed only by the code that drafted its parameters, and approved only
    # from that card -- never offered to a model. `delegate.claude_code`
    # queues a brief for an agent with a shell: the brief IS the capability,
    # so it has to be the one ATLAS wrote and Miro read, not one a model
    # picked and filled in from the catalog (intent.catalog, the reasoner).
    card_only: bool = False
    # Runs only when Miro himself asked for it, over a channel that proves
    # it is him (an approver's signed Slack message or /atlas, a call ATLAS
    # placed) -- never unattended, never
    # from an agent, the reasoner or a fix sweep, never on caller ID alone.
    # `mac.*` drives his laptop; text a model read in an email or a web page
    # must not be able to reach it (executor.execute enforces this).
    principal_only: bool = False
    # The exact words the card must carry, as a pure, synchronous function of
    # the params (like `vet`: never a network call). `approvals.propose`
    # appends it verbatim to the Slack card and `intent.card_text` shows it
    # instead of its truncated parameter line. For an action whose params ARE
    # the decision -- a message's recipient and text -- a one-line card with
    # the params behind a Details button is not an approval of them.
    card_detail: Callable[[dict[str, Any]], str] | None = None
    # Tier 2 that is carded even when Miro asks for it directly. On Slack or
    # a call ATLAS placed, `intent.act` normally takes the request as the
    # approval; but the model wrote these params (the text of a message he
    # dictated, the contact it picked), so he approves what will actually be
    # sent, not what he said. Meaningless on Tier 1.
    card_always: bool = False
    # Moves money or handles a credential. Set in code on the spec; WhatsApp
    # approval refuses any action carrying it, whatever its name or tier.
    moves_money_or_credentials: bool = False

    def __post_init__(self) -> None:
        if self.tier is Tier.AUTO:
            if self.dry_run is None:
                raise ValueError(f"{self.name}: Tier 1 requires a dry_run")
            if self.rollback is None and not self.irreversible:
                raise ValueError(f"{self.name}: Tier 1 requires a rollback")
            if self.irreversible:
                raise ValueError(f"{self.name}: irreversible cannot be Tier 1")


@dataclass(frozen=True, slots=True)
class Signal:
    """Something worth telling Miro that is not a failure: a recommendation,
    a trend, a deadline. Modules emit these; the kernel routes them."""

    title: str
    body: str
    severity: Severity = Severity.NORMAL
    suggests: str | None = None      # an Action name, if there is one to offer
    params: dict[str, Any] = field(default_factory=dict)
    # A local file's path (an image, a video). When set, the scheduler
    # uploads it into the resulting card's topic once, the same way
    # `kernel.reels.preview_under_card` has always uploaded a reel's
    # preview -- generalised so a module carries no upload code of its own.
    # None (the default) asks for nothing and changes nothing for every
    # other module's signals.
    attachment: str | None = None


@dataclass(frozen=True, slots=True)
class AgendaItem:
    """Something happening at a time, for the morning brief.

    Deliberately generic: the kernel must not know what a calendar is. A
    calendar contributes events, a billing module contributes a renewal date,
    a coursework module contributes a submission deadline.
    """

    when: datetime
    title: str
    kind: str = "event"              # event | deadline | reminder
    detail: str = ""
    minutes: int | None = None       # duration, where there is one
    source: str = ""                 # module name, filled in by the kernel


@dataclass(frozen=True, slots=True)
class InboxItem:
    """Something waiting on a reply from Miro."""

    at: datetime
    sender: str
    subject: str
    urgency: Severity = Severity.NORMAL
    why: str = ""                    # one line: why this is ranked where it is
    reference: str = ""              # thread id, order id, whatever reopens it
    source: str = ""                 # module name, filled in by the kernel


def implements(module: object, member: str) -> bool:
    """Does this module actually provide an optional protocol member?

    `Module` below is a Protocol, so the defaults written on it are
    documentation, not inheritance: no module subclasses it, so a module that
    omits an optional member has no implementation at all and calling it raises
    AttributeError.

    That turned every optional member into noise. Fifteen of eighteen modules
    have no `agenda()`, so each morning brief logged fifteen "agenda failed"
    warnings, and a genuine failure in one of the three that do implement it
    looked exactly like the fifteen that never could. `content` and `tiktok`
    have no `signals()` and printed a traceback on every signals cycle.

    Callers use this to take the documented default silently, and keep their
    warning for a member that exists and failed.
    """
    return callable(getattr(module, member, None))


class Module(Protocol):
    """What a module must provide. Everything domain-specific lives behind this.

    Adding a business, a data source or a capability means writing one of these
    and dropping it in `atlas/modules/`. The kernel requires no changes.
    """

    name: str                        # "flycheap"
    title: str                       # "flycheap"
    # Env var names this module needs. Boot fails loudly if any are missing,
    # rather than the module silently reporting healthy forever.
    requires_env: Sequence[str]

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> Sequence[Check]: ...
    def actions(self) -> Sequence[Action]: ...

    async def signals(self) -> Sequence[Signal]:
        """Optional periodic recommendations. Default: none."""
        return ()

    def brief(self) -> str:
        """One line for the morning brief. Default: nothing to say."""
        return ""

    async def agenda(self, within: timedelta) -> Sequence[AgendaItem]:
        """Timed things inside `within`: meetings, deadlines, renewals.
        Default: none."""
        return ()

    async def inbox(self, limit: int = 10) -> Sequence[InboxItem]:
        """Things awaiting a reply, most urgent first. Default: none."""
        return ()
