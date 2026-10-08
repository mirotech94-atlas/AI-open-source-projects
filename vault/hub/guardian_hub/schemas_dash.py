"""Dashboard payload contracts — the single source of truth for every shape
the hub returns to the web app, and for `web/src/types.ts` which mirrors
these names and field names one-for-one (see `scripts/check_types.py`).

Conventions the whole dashboard obeys:
- Payloads are **rendering-ready**: all bucketing, aggregation and derivation
  happens hub-side. The web app formats (units, relative time, colour class)
  but never aggregates a series or joins two collections.
- Booleans that would shadow a Python/TS keyword are suffixed (`passed`,
  never `pass`).
- Every timestamp is an ISO-8601 string named `ts` / `*_seen` / `*_at`.
- Every "how long ago" is an integer `age_s`.
- Every percentage is a number 0-100 named `*_pct`.
- Every list field is plural.
- Fields carry defaults so a builder can construct a payload incrementally;
  pydantic still serializes every key, so the TS mirror has no optionals.
- `Sev` is a `Literal`, deliberately not an `enum.Enum`: an Enum would emit a
  named OpenAPI component with no pydantic model behind it, which the
  schema<->types parity check would then flag forever.
"""

from typing import Literal

# BaseModel is aliased `_Model` on purpose: the parity test registers every
# public name in this module that has `model_fields`, so an un-aliased
# `BaseModel` import would demand an `export type BaseModel` in types.ts.
from pydantic import BaseModel as _Model, Field, field_validator

Sev = Literal["CRIT", "WARN", "INFO"]


# --------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------

class NetworkRef(_Model):
    """The network a row belongs to, inlined on every network-scoped row."""
    id: str = ""
    slug: str = ""
    name: str = ""
    colour: str = ""

class NetworkHealth(_Model):
    """One chip of the overview's per-network strip."""
    id: str = ""
    slug: str = ""
    name: str = ""
    colour: str = ""
    state: str = "good"     # good|warn|bad
    status: str = "pending" # pending|active|unreachable|no-sensor
    devices: int = 0
    online: int = 0
    unverified: int = 0
    # Task 69: the camera census for this network. The owner runs a dozen
    # cameras and wants the count per network on the face of the UI, plus how
    # many of them are answering right now.
    cameras: int = 0
    cameras_online: int = 0
    crit: int = 0
    warn: int = 0
    sensor_ok: bool = False
    is_primary: bool = False   # round 2: which chip in the strip is the fallback network

class Network(_Model):
    id: str = ""
    slug: str = ""
    name: str = ""
    kind: str = "wifi"      # wifi|wired|lab|vlan|guest|home
    subnet_prefix: str = ""
    expected_gateway: str = ""
    ssid: str = ""
    sensor_id: str | None = None
    sensor_name: str = ""
    colour: str = ""
    is_primary: bool = False
    status: str = "pending"
    devices: int = 0
    online: int = 0
    unverified: int = 0
    cameras: int = 0
    created: str = ""
    # mirror|router|host|none -- set by a person in the wizard/Settings, never
    # guessed. See `payloads/traffic.mode_for` for how this declaration is
    # crossed with what the sensor actually reports before being trusted.
    traffic_mode: str = "none"

class NetworksPayload(_Model):
    networks: list[Network] = Field(default_factory=list)

# --------------------------------------------------------------------------
# Camera recorders (NVR/DVR) -- Task 73
# --------------------------------------------------------------------------
class RecorderChannel(_Model):
    """One channel on a recorder, as the recorder itself reports it."""
    channel: int = 0
    name: str = ""
    online: bool = False
    model: str = ""

class RecorderVendor(_Model):
    key: str = ""
    label: str = ""
    #: False for a make Vault can record but cannot yet read (no adapter).
    supported: bool = False

class RecorderRow(_Model):
    """A registered recorder. Carries NO credential: `has_credentials` says
    whether one is stored, and the value never leaves the hub except down the
    authenticated sensor channel.

    `problem` / `owner_action` are the whole point of the row when
    `status == "error"`: a recorder whose HTTP interface is switched off has to
    say so and name the setting, because a silent zero cameras is the wrong
    answer for a house with twelve of them.
    """
    id: str = ""
    name: str = ""
    vendor: str = ""
    vendor_label: str = ""
    supported: bool = False
    host: str = ""
    port: int = 80
    scheme: str = "http"
    username: str = ""
    has_credentials: bool = False
    expected_channels: int = 0
    enabled: bool = True
    status: str = "pending"        # pending|ok|error
    error_kind: str = ""
    problem: str = ""
    owner_action: str = ""
    model: str = ""
    firmware: str = ""
    channel_count: int = 0
    channels_online: int = 0
    channels: list[RecorderChannel] = Field(default_factory=list)
    network_id: str = ""
    network_slug: str = ""
    network_name: str = ""
    last_ok: str = ""
    last_attempt: str = ""
    created: str = ""

class RecordersPayload(_Model):
    recorders: list[RecorderRow] = Field(default_factory=list)
    vendors: list[RecorderVendor] = Field(default_factory=list)

class TrendPoint(_Model):
    ts: str = ""
    value: float | None = None

class Series(_Model):
    name: str = ""
    points: list[TrendPoint] = Field(default_factory=list)
    color: str | None = None

class HourBlock(_Model):
    """One cell of a 24 h strip. `hour` is hours-ago-normalized 0-23 (0 = the
    oldest block drawn, 23 = the current hour); `state` is the colour class:
    ok|warn|crit|none."""
    hour: int = 0
    state: str = "none"

class NextAction(_Model):
    label: str = ""
    method: str = "POST"
    path: str = ""
    body: dict = Field(default_factory=dict)

class Deduction(_Model):
    category: str = ""
    points: int = 0
    why: str = ""
    # The matching `SecurityCheck.key` this deduction is scored from, so a
    # failing posture card can open the exact same detail drawer as the
    # Wi-Fi guard / Checks rows instead of a second, disconnected view of the
    # same fact. "" for the handful of host checks (`unattended`, `reboot`)
    # that score the card but have no corresponding row in `checks()` today.
    key: str = ""

class DeductionShift(_Model):
    """One finding that is still there but has moved -- the same category
    scoring a different amount, e.g. unverified devices going from four to
    three.

    Without this, a count changing reads as one finding appearing and a
    different one disappearing ("new: 3 unverified devices; gone: 4 unverified
    devices"), which is the opposite of an explanation.
    """
    category: str = ""
    #: Points deducted then and now. Fewer points is better.
    was_points: int = 0
    now_points: int = 0
    was_why: str = ""
    now_why: str = ""
    key: str = ""

class ScoreChange(_Model):
    """Why the score moved, for the one line under the score hero.

    Absent (None on the payload) when there is no scan old enough to compare
    against -- a delta of zero that nobody measured is not a fact, so nothing
    is rendered rather than "no change".
    """
    #: Current total minus the earlier total. Negative is worse.
    delta: int = 0
    #: The earlier total, so the line can read "83, was 87".
    was: int = 0
    #: `Scan.ts` of the scan compared against.
    since: str = ""
    hours: int = 24
    #: Deductions present now and absent then, heaviest first.
    appeared: list[Deduction] = Field(default_factory=list)
    #: Deductions present then and absent now, heaviest first.
    cleared: list[Deduction] = Field(default_factory=list)
    #: Findings present in both, scoring differently -- reported as a move
    #: rather than as an appearance plus a disappearance.
    changed: list[DeductionShift] = Field(default_factory=list)
    #: The whole thing as one sentence naming the findings that moved.
    summary: str = ""
    #: When the day is unchanged: the most recent time the score did move,
    #: so the line still explains something. None otherwise.
    last_moved: "ScoreChange | None" = None

class TimelineItem(_Model):
    ts: str = ""
    kind: str = ""          # alert|fix|join|leave|incident|scan|report…
    title: str = ""
    detail: str = ""
    sev: Sev | None = None
    entity: str = ""
    href: str | None = None

class NameCount(_Model):
    name: str = ""
    count: int = 0

class EventRow(_Model):
    ts: str = ""
    type: str = ""
    entity: str = ""
    text: str = ""
    payload: dict = Field(default_factory=dict)
    network: NetworkRef | None = None

class QueueStatus(_Model):
    id: str = ""
    path: str = ""
    status: str = "queued"      # queued|running|done|failed
    body: dict = Field(default_factory=dict)
    result: dict | None = None
    ts: str = ""

class JobRow(_Model):
    """One row of the Activity page (see `payloads/jobs.py`): either the
    sensor work queue (`Queue`) or, for a repair Vault ran on its own
    with no dashboard queue entry, the unattended fix log (`Fix`,
    `by == "auto"`) -- so the owner sees everything Vault is doing or has
    just done, self-driven work included, without reading a raw `path` +
    JSON `body`.

    `label`/`target` are the plain-English "what" and "where" -- never the
    raw `path`. `stuck` is true only for a `running` row that has run far
    longer than that kind of job normally takes (`payloads.jobs.
    expected_seconds`); it exists so a hung speed test or upgrade is called
    out instead of sitting there looking like ordinary progress.
    `retryable` is only ever true on a `failed` row, and only for a job that
    is safe to re-queue with no fresh confirmation (never a disruptive
    `TIER3_FIXES` action -- see `payloads.jobs.is_retryable`).
    """
    id: str = ""
    kind: str = ""              # fix|speedtest|scan|reboot-plan
    label: str = ""             # plain English, e.g. "free disk space"
    target: str = ""            # host/site this job runs against, "" if none
    who: str = ""                # who queued it
    status: str = "queued"      # queued|running|done|failed
    queued_at: str = ""
    finished_at: str | None = None
    sensor_id: str | None = None
    started_at: str | None = None
    not_before: str | None = None
    expires_at: str | None = None
    wait_reason: str = ""
    waiting_s: int = 0
    running_s: int | None = None
    duration_s: int | None = None   # elapsed while running; total once finished; None while queued
    ok: bool | None = None          # None until the job has finished
    out: str = ""
    why_failed: str = ""        # first-person reason, "" unless status == failed
    stuck: bool = False
    retryable: bool = False
    #: What the row opens, when it opens anything. `scan_id` is the first
    #: scan the sensor sent after a finished scan request (the request's own
    #: result records only that it was asked for); `report_id` is set on
    #: `kind == "report"` rows. See `payloads/jobs.build`.
    scan_id: str | None = None
    report_id: str | None = None

class ScanSummary(_Model):
    """What one stored scan found, for the Activity drawer. Counts only --
    the whole scan is one click away as a download (`/scans/{id}/download`)."""
    id: str = ""
    ts: str = ""
    score: int | None = None
    devices: int = 0
    sites_up: int = 0
    sites_total: int = 0
    hosts_reachable: int = 0
    hosts_total: int = 0
    alerts: int = 0

class JobsPayload(_Model):
    jobs: list[JobRow] = Field(default_factory=list)

class HeatmapCell(_Model):
    row: str = ""
    col: int = 0
    value: float = 0.0


# --------------------------------------------------------------------------
# Inbox (defined before the tabs that embed InboxRow)
# --------------------------------------------------------------------------

class InboxCounts(_Model):
    incidents: int = 0
    alerts: int = 0
    recs: int = 0
    unread: int = 0
    #: Open alerts whose remedy is one a "yes" runs. A subset of `alerts`,
    #: never a fourth independent pile.
    needs: int = 0

class RowAction(_Model):
    kind: str = ""          # ack|snooze|trust|fix|rec-yes|rec-no|open…
    label: str = ""
    method: str = "POST"
    path: str = ""
    body: dict = Field(default_factory=dict)
    tier: int = 1           # 1 read/safe, 2 changes the network, 3 admin-only

class ConfidenceFactor(_Model):
    """One line of the rubric's working: a question, its answer, its weight.

    Mirrors `confidence.Factor` field for field EXCEPT its `why`, and the
    omission is deliberate rather than an oversight. `remedy._confidence_why`
    already joins the three largest-weighted factors' `why` sentences into
    `AlertRemedy.confidence_why`, which the alert row prints; shipping `why`
    here as well would print the same prose twice on one screen -- once as
    Vault's sentence on the row and again inside the drawer directly below
    it. The question and the answer are what the drawer adds that the sentence
    cannot: the factors that found NOTHING, which carry `weight=0` and no
    place in the sentence, and which are the whole reason the owner chose to
    see all six ("what was considered and found absent rather than only what
    fired" -- the design's own words).

    `weight` is signed and may be zero. A negative one is not an error state:
    it is a reason for caution, and it is informative in exactly the way the
    absent factors are. Anything rendering these must not dress it as an
    alarm, and must not make a zero the loudest mark in the table -- see
    `web/src/components/InboxRowView.tsx`, which carries that reasoning at
    the point a future editor would undo it.
    """
    name: str = ""              # "host_history", "held", "verified", ...
    question: str = ""          # what was asked, in the owner's language
    answer: str = ""            # what the rows said, "never on this host"
    weight: int = 0             # signed contribution to the 0-100 score

class AlertRemedy(_Model):
    """What Vault can do about one alert (see `guardian_hub/remedy.py`).

    `kind` is the whole contract: "auto" means the row's Fix action runs the
    repair now; "approval" means chat mints the ordinary tier-3 proposal for
    it and a real "yes" runs it; "human" means no remediation Vault owns
    applies, and `need` says exactly what a person has to do. "none" is the
    default only for a row that is not an alert (a recommendation, an
    incident header) -- an alert always resolves to one of the three.
    """
    kind: str = "none"          # auto|approval|human|none
    action: str = ""            # the fix-catalogue action, "" when there is none
    host: str = ""              # the host it would run on
    arg: str = ""               # that fix's argument (attacker IP, service, container)
    tier: int = 0               # effective tier of the action, 0 when there is none
    tool: str = ""              # the chat tool that would run it
    min_role: str = ""          # role that tool needs
    can_run_now: bool = False   # this caller may run it immediately
    summary: str = ""           # what Guardian can (or cannot) do, one sentence
    need: str = ""              # exactly what is needed from the owner, "" when nothing
    #: What running it will COST -- what breaks, for how long, and for whom.
    #: `summary` says what Vault can do and `need` says what it wants from
    #: you; this is the thing being weighed, and an owner should not have to
    #: already know it in order to answer. "" when the action has no entry in
    #: `remedy.FIX_IMPACT`, and a blank one is printed as nothing rather than
    #: as reassurance.
    impact: str = ""
    opening: str = ""           # Guardian's first chat message for the handoff
    #: How sure Vault is, 0-100, and the sentence explaining it. A rubric
    #: (`guardian_hub.confidence`), not a prediction -- nothing is fitted.
    #: Advisory: it may make `kind` more cautious, and may only make it less
    #: so on demonstrated history. See that module's `gate`.
    confidence: int = 0
    confidence_why: str = ""
    #: The rubric's whole working, all six factors including the ones that
    #: found nothing. SERIALISATION ONLY: `confidence.assess` has already
    #: computed every one of these to reach `confidence`, so carrying them
    #: costs no query -- which is load-bearing, because `remedy.resolve_action`
    #: is read-only and runs once per alert on every dashboard load, and the
    #: inbox pays it twice per alert (`payloads/inbox._counts` resolves every
    #: open alert for the Needs-you badge and `build` resolves again per row).
    #: `tests/test_confidence_read_cost.py` pins the statements added per
    #: alert at exactly 12; a field here that had to be looked up would show
    #: as a slope of 13 or more, and that test is the intended way to find out.
    #:
    #: Empty is a real answer -- either the row has no remedy Vault scored,
    #: or scoring failed and `_apply_confidence` fell open. A reader must
    #: render nothing at all for an empty list rather than an empty table,
    #: because a table with no rows reads as "Vault considered nothing".
    confidence_factors: list[ConfidenceFactor] = Field(default_factory=list)

class DriftDetail(_Model):
    """Task 83, item 5: the honest version of "show the diff" for a
    `sec:drift:` alert. There is no line diff to show -- the sensor keeps
    only a fingerprint of the file, never its content or a prior fingerprint
    (see `guardian_hub/drift.py`) -- so this names the file, the host, when
    Vault first saw the change, the current fingerprint, and says plainly
    that the contents themselves are not something Vault can show."""
    host: str = ""
    file: str = ""
    changed_at: str = ""
    fingerprint: str = ""
    #: Item 26: why a change to THIS file is worth a person's attention.
    #: "sshd_config changed" is a log line; "this decides who may log in
    #: remotely" is a warning. See `drift.WHY_IT_MATTERS`.
    why: str = ""
    note: str = ""

class InboxRow(_Model):
    id: str = ""
    view: str = "alerts"    # incidents|alerts|recs
    sev: Sev = "INFO"
    title: str = ""
    entity: str = ""
    why: str = ""
    what_changed: str = ""
    status: str = "open"
    first_seen: str = ""
    last_seen: str = ""
    count: int = 1
    actions: list[RowAction] = Field(default_factory=list)
    children: list["InboxRow"] = Field(default_factory=list)
    ticket_key: str = ""
    confidence: int | None = None
    network: NetworkRef | None = None
    # Alerts only: what Vault can do about this row (null on a
    # recommendation or an incident header, which have their own decisions).
    remedy: AlertRemedy | None = None
    # A `sec:drift:` alert only: the honest detail above. Null for every
    # other row.
    drift: DriftDetail | None = None

# InboxRow.children is a self-reference (`list["InboxRow"]`); pydantic resolves
# it lazily, so rebuild it here rather than letting the first
# model_json_schema()/validate call be the one that discovers a broken ref.
InboxRow.model_rebuild()


class InboxPayload(_Model):
    counts: InboxCounts = Field(default_factory=InboxCounts)
    rows: list[InboxRow] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Inbox triage (task 83, patterns A3 + A6): one lead item sized like it
# matters, everything else collapsed to a line, the three-way outcome as the
# primary axis. Built from the same rows `InboxPayload` returns -- this is a
# second render of them, not a second source of alerts.
# --------------------------------------------------------------------------

class TriageOutcomes(_Model):
    """The three-way outcome axis (pattern A6), counted over the same open
    alerts/recs `InboxCounts` counts -- `handled` is the one exception, a
    trailing window over `Fix` rather than anything currently open."""
    handled: int = 0
    handled_period: str = "the last 7 days"
    needs_yes: int = 0
    needs_you: int = 0

class TriageItem(_Model):
    """One row of the prioritised inbox: a singleton alert, a cause-rolled
    group of alerts, an incident, or a recommendation.

    `alerts` always carries every underlying `InboxRow` this item folds in --
    a group or an incident never hides one; it is still reachable and still
    counted through this list, never dropped in the collapse to one line.

    `rank_reason` is the whole point of the ranking: a short, human-readable
    account of why this item sorts where it does (severity, confidence, what
    it is attached to), so a score is never shown without its explanation.

    Write actions are role-filtered exactly as `InboxRow.actions` is -- an
    action the caller's role may not run is never included here either. A
    viewer instead reads `primary_min_role` to render its own disabled
    control with a `title` naming the role it would need.
    """
    kind: str = "alert"        # alert|group|incident|rec
    id: str = ""
    view: str = "alerts"       # alerts|incidents|recs
    sev: Sev = "INFO"
    title: str = ""
    entity: str = ""
    why: str = ""
    since_text: str = ""       # the repeat count turned into a duration, e.g. "every scan since Tue"
    count: int = 1
    confidence: int | None = None
    cause: str = ""            # the rolled-up cause key, "" for a singleton row
    rank_score: float = 0.0
    rank_reason: str = ""
    remedy: AlertRemedy | None = None
    actions: list[RowAction] = Field(default_factory=list)
    primary_label: str = ""    # the one action this row is offering, "" when none applies
    primary_min_role: str = "" # role that action needs, "" when the row has none to gate
    alerts: list[InboxRow] = Field(default_factory=list)
    recommendations: list[InboxRow] = Field(default_factory=list)

class InboxTriagePayload(_Model):
    lead: TriageItem | None = None
    rest: list[TriageItem] = Field(default_factory=list)
    outcomes: TriageOutcomes = Field(default_factory=TriageOutcomes)
    total_open: int = 0        # open alerts (incl. incident children) + open recs -- the partition's total


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------

class HealthStrip(_Model):
    kind: str = ""          # site|host|lan|dns
    name: str = ""
    ok: bool | None = None
    blocks: list[HourBlock] = Field(default_factory=list)

class QuestionItem(_Model):
    question: str = ""
    answer: str = ""
    state: str = "ok"       # ok|warn|crit|unknown

class AlertCounts(_Model):
    crit: int = 0
    warn: int = 0
    info: int = 0
    open: int = 0
    acked: int = 0
    snoozed: int = 0

class DeviceCounts(_Model):
    total: int = 0
    online: int = 0
    sleeping: int = 0
    unverified: int = 0
    quarantined: int = 0

class SensorStatus(_Model):
    name: str = ""
    status: str = "pending"     # pending|active|revoked
    last_seen: str | None = None
    age_s: int | None = None
    platform: str = ""
    version: str = ""

class AssetCounts(_Model):
    """The "What do I have?" hero card. `total` is the sensor's own asset
    census (`Scan.raw["health"]["assets"]`) when it reported one, else the
    devices + servers + sites the hub knows about."""
    total: int = 0
    servers: int = 0
    sites: int = 0

class CoverageRow(_Model):
    """One class of asset: how many the sensor is still reporting, of how many
    Vault knows about."""
    label: str = ""
    seen: int = 0
    total: int = 0


class CertExpiry(_Model):
    """One certificate coming up for renewal."""
    host: str = ""
    issuer: str = ""
    #: None when the site has no readable certificate at all, which is a
    #: different thing from one expiring soon and is not shown as a number.
    days_left: int | None = None


class IncidentCounts(_Model):
    """Open (unclosed) incidents, split by severity so the hero card knows
    whether to go red or amber."""
    open: int = 0
    crit: int = 0
    warn: int = 0

class IncidentBrief(_Model):
    """One row of the overview's open-incidents panel: the correlated story's
    header, not the raw alerts under it (those live in the Inbox)."""
    id: str = ""
    title: str = ""
    sev: Sev = "WARN"
    entity: str = ""
    opened: str = ""
    alerts: int = 0
    href: str = ""

class RecorderGap(_Model):
    """One recorder Vault cannot fully account for -- named so the camera
    census can say WHICH cameras it is inferring rather than reporting a
    smaller, directly-observed number as if it were the whole truth.

    `network_id` is the recorder's own synthesized camera network
    (`recorders.camera_network`), so a client that draws the network map can
    place these unconfirmed channels inside the same boundary as the
    recorder's confirmed ones. `""` when the recorder has never been read
    successfully -- it has no camera network yet, so the gap has nowhere to
    be drawn and a map must not guess a placement for it."""
    name: str = ""
    expected: int = 0
    known: int = 0
    reason: str = ""
    network_id: str = ""

class CameraCensus(_Model):
    """Overview's camera census row (Task 83, pattern A5). `known` is every
    camera Vault has actually seen answer -- a device the LAN sweep found
    directly, or a channel a recorder has listed -- never a guess. `expected`
    adds what a recorder's own `expected_channels` says should be behind it
    but has not (yet, or currently) listed; `gaps` names exactly which
    recorder and why, so a quiet recorder is read as "N cameras unaccounted
    for", never as "there are now fewer cameras"."""
    known: int = 0
    online: int = 0
    expected: int = 0
    unobserved: int = 0
    offline: int = 0
    exposure_known: bool = False
    exposed: bool = False
    exposed_ports: list[int] = Field(default_factory=list)
    gaps: list[RecorderGap] = Field(default_factory=list)

class RecCounts(_Model):
    """The "What should I do?" hero card. `top` is the highest-priority open
    recommendation's own text, so the card's sub-line names the actual next
    thing to do rather than a category."""
    open: int = 0
    critical: int = 0
    high: int = 0
    top: str = ""

class FeedState(_Model):
    """One pill of the health panel's feed row: did this collector produce
    anything on the last scan."""
    name: str = ""
    ok: bool = False

class GuardianHealth(_Model):
    """"Proof that the watchdog is watching" -- the health panel.

    Read from `Scan.raw["health"]` (what the sensor reports about its own run)
    plus the hub's own knowledge: how many sensors are reporting, how many
    fixes ran in 24 h, whether a notification channel is configured, and what
    the DNS filter did.

    Every figure is nullable/empty rather than zero-defaulted where a zero
    would read as a measurement: a sensor that predates the health block has
    no `run_seconds`, and 0 s would claim an instant scan.
    """
    sensor_state: str = "unknown"    # online|silent|unknown
    run_seconds: int | None = None
    scans_last_hour: int | None = None
    #: The hub's analogue of the reference's "hub sync": how many of this
    #: tenant's sensors are actually reporting.
    sensors_active: int = 0
    sensors_total: int = 0
    feeds: list[FeedState] = Field(default_factory=list)
    last_event_ts: str = ""
    last_event_age_s: int | None = None
    autofix_enabled: bool = False
    autofix_24h: int = 0
    notify_channel: str = ""         # "" when nothing is configured
    dns_filter_state: str = "off"    # on|down|off
    dns_blocked_24h: int = 0

class PathHealth(_Model):
    """One anchor of the internet-line panel: what a handful of pings to a
    fixed public address measured on the last scan."""
    name: str = ""
    ip: str = ""
    avg_ms: float | None = None
    loss_pct: float | None = None
    jitter_ms: float | None = None
    state: str = "good"     # good|warn|bad

class ScoreCoverage(_Model):
    """How much of the estate the score was actually able to measure.

    The sensor's Vulnerabilities, Configuration and Patch-status deductions
    are built only from hosts that answered, so a host going silent removes
    its deductions and the score rises. `applicable` is what Vault wanted
    to look at and `measured` is what replied; the gap between them is the
    part of the number that is not evidence.

    `applicable == 0` is reported as `pct == 100`, not 0 -- an account with
    no servers configured is fully measured, not totally blind.
    """
    measured: int = 0
    applicable: int = 0
    pct: int = 100

class NeedsYouItem(_Model):
    """One repair Vault can run but will not run on its own.

    This is `AlertRemedy.kind == "approval"` and nothing else. An "auto"
    remedy has already run by the time anyone reads the page, and a "human"
    one is an errand Vault cannot perform -- neither belongs in a queue
    whose only two buttons are Approve and Not now.
    """
    key: str = ""
    title: str = ""
    detail: str = ""        # what Guardian would do
    cost: str = ""          # what it costs to say yes -- a reboot, a dropped session
    severity: str = ""
    action: str = ""        # the fix-catalogue action Approve runs
    host: str = ""

class FixDay(_Model):
    day: str = ""          # YYYY-MM-DD
    automatic: int = 0
    approved: int = 0

class FixedWithoutYou(_Model):
    """The counterweight to the queue above: what Vault handled alone.

    Split rather than totalled, because "412 handled" and "412 handled, 6 of
    them by you" are different claims and only the second is true.
    """
    days: int = 14
    automatic: int = 0
    approved: int = 0
    #: One entry per day, oldest first, always `days` long. A quiet day is a
    #: real zero, not a gap -- the bars must not close up over an empty day
    #: and make the fortnight look busier than it was.
    daily: list[FixDay] = Field(default_factory=list)

class OverviewPayload(_Model):
    ts: str | None = None
    age_s: int | None = None
    score: int | None = None
    grade: str = ""
    potential: int | None = None
    #: The rubric version that produced `score`, as stamped by the sensor at
    #: sweep time (`alerts.SCORE_MODEL`). "" for a scan captured before the
    #: stamp existed -- deliberately not back-filled with today's version,
    #: since relabelling an old number under a rubric that did not exist yet
    #: is the history rewrite the stamp is here to prevent.
    score_model: str = ""
    #: None when the sensor sent no coverage block, rather than a fabricated
    #: 100% -- "we did not measure coverage" and "coverage was complete" are
    #: different claims and only one of them is true.
    score_coverage: ScoreCoverage | None = None
    verdict: str = ""
    next_action: NextAction | None = None
    deductions: list[Deduction] = Field(default_factory=list)
    #: Why the score moved since yesterday. None when no scan is old enough
    #: to compare against -- see payloads/scorechange.py.
    score_change: ScoreChange | None = None
    strip: list[HealthStrip] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)
    score_trend: Series = Field(default_factory=Series)
    questions: list[QuestionItem] = Field(default_factory=list)
    alerts: AlertCounts = Field(default_factory=AlertCounts)
    devices: DeviceCounts = Field(default_factory=DeviceCounts)
    sensor: SensorStatus | None = None
    networks: list[NetworkHealth] = Field(default_factory=list)
    #: The only two-button queue in the product: Approve or Not now.
    needs_you: list[NeedsYouItem] = Field(default_factory=list)
    fixed_without_you: FixedWithoutYou = Field(default_factory=FixedWithoutYou)
    # --- Task 74: the five hero cards and the panels under them ---
    #: The 7-day score window the hero panel's sparkline draws, alongside the
    #: 30-day `score_trend` the trend card already shows.
    score_trend_7d: Series = Field(default_factory=Series)
    assets: AssetCounts = Field(default_factory=AssetCounts)
    #: Alerts opened, alerts resolved and fixes applied in the last 24 hours --
    #: the "What changed?" card's single number.
    changed_24h: int = 0
    incidents: IncidentCounts = Field(default_factory=IncidentCounts)
    #: The open incidents worth showing on the overview, newest first (the
    #: full list lives in the Inbox).
    incident_briefs: list[IncidentBrief] = Field(default_factory=list)
    recs: RecCounts = Field(default_factory=RecCounts)
    health: GuardianHealth = Field(default_factory=GuardianHealth)
    paths: list[PathHealth] = Field(default_factory=list)
    # One real IP off this tenant's own network, used as the example in the
    # chat composer's placeholder ("Ask Vault, e.g. what is 192.168.1.166?")
    # so the hint names a device the user can actually ask about. Empty when
    # the tenant has no device with an IP yet -- the web falls back to a
    # literal address then.
    chat_example_ip: str = ""
    #: Whether this hub can hear and speak at all -- `voice.ready()`, which is
    #: the presence of both an ElevenLabs key and a voice id. False hides every
    #: voice control in the app rather than disabling one: `remedy.py`'s human
    #: tier is the model, and a microphone button that cannot record is the
    #: kind of dead affordance a customer reads as a broken product.
    voice: bool = False
    #: Task 83: how many cameras Vault knows about, how many are
    #: answering, whether that count is honestly complete, and whether one
    #: is reachable from the internet by a path Vault does not expect.
    cameras: CameraCensus = Field(default_factory=CameraCensus)
    #: What the sensor can currently SEE, per asset class. Not what exists --
    #: what is still being reported. A row the sensor stopped sending keeps its
    #: last result forever (see `payloads/staleness`), so "12 certificates" can
    #: be true while nothing has checked four of them for a month.
    coverage: list[CoverageRow] = Field(default_factory=list)
    #: Certificates inside the renewal window, soonest first.
    certs_expiring: list[CertExpiry] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Live
# --------------------------------------------------------------------------

class LiveSite(_Model):
    name: str = ""
    url: str = ""
    code: int | None = None
    ms: float | None = None
    ok: bool | None = None
    #: The last `LIVE_SPARK_POINTS` `Metric(name="latency_ms")` readings for
    #: this site, oldest first -- the header's per-site sparkline (Task 83).
    #: Attached in `routers/live.py`'s route handler, never in the 10s probe
    #: cache: the cache holds probe-only rows, so a cache hit still gets a
    #: freshly-queried spark on every request.
    spark: list[float] = Field(default_factory=list)

class LiveEvent(_Model):
    """One WS frame: `type` is the channel (overview|devices|dns|queue|…) and
    `data` is that channel's delta."""
    type: str = ""
    ts: str = ""
    data: dict = Field(default_factory=dict)

class LivePayload(_Model):
    ts: str | None = None
    sites: list[LiveSite] = Field(default_factory=list)
    scanning: bool = False
    sensor_age_s: int | None = None
    # Task 70: the one-minute load average of the host the FRESHEST reporting
    # sensor runs on, and that host's core count so a reader can judge whether
    # the figure is high. Both None for a sensor that predates the reporting
    # change or a platform with no load average -- the sub-header then keeps
    # showing the sensor-freshness pill instead of a zero that reads as idle.
    load1: float | None = None
    cores: int | None = None
    # ISO deadline of an active maintenance pause, or None when there is
    # none / it has already lapsed. Read here rather than through a separate
    # endpoint because the sub-header polls this payload anyway.
    maintenance_until: str | None = None


# --------------------------------------------------------------------------
# Assets: sites, hosts, devices, screen time
# --------------------------------------------------------------------------

class SecurityHeaders(_Model):
    hsts: bool = False
    nosniff: bool = False
    frame: bool = False
    csp: bool = False
    https_redirect: bool = False
    old_tls: bool = False
    server: str = ""

class SiteCard(_Model):
    name: str = ""
    url: str = ""
    ok: bool | None = None
    code: int | None = None
    ms: float | None = None
    #: Truncated to 2dp, never rounded up -- see `payloads/assets._uptime`.
    #: None when there are fewer than `MIN_UPTIME_SAMPLES` checks in the
    #: window, because two decimal places over a handful of samples is
    #: invented precision, not a measurement.
    uptime_24h: float | None = None
    uptime_7d: float | None = None
    #: How many checks each percentage above was computed from, so a reader
    #: can tell 100%-over-21 from 100%-over-2000 and recompute the figure.
    #: Populated even when the percentage is None -- "too few to publish" is
    #: itself worth showing.
    uptime_24h_checks: int = 0
    uptime_7d_checks: int = 0
    #: Checks that fell inside a planned maintenance window and were left out
    #: of the figures above entirely -- out of the numerator as well as the
    #: denominator, since a window is not a measurement of availability in
    #: either direction. Published so the exclusion is visible: an uptime
    #: figure that quietly benefits from a window is the same class of
    #: problem as one that counts the window as an outage.
    uptime_24h_planned: int = 0
    uptime_7d_planned: int = 0
    latency_spark: list[float] = Field(default_factory=list)
    cert_days: int | None = None
    cert_expires: str = ""
    cert_issuer: str = ""
    dns_a: list[str] = Field(default_factory=list)
    headers: SecurityHeaders = Field(default_factory=SecurityHeaders)
    incidents_7d: int = 0
    public: bool = False
    # Set when the sensor has stopped reporting this row entirely (see
    # payloads/staleness.py). The figures above are then a last-known state,
    # not a current reading, and the UI says so rather than letting them
    # present themselves as fresh.
    stale: bool = False
    stale_note: str = ""
    last_checked: str = ""

# --------------------------------------------------------------------------
# History: the trend charts, the scan log and the event log
# --------------------------------------------------------------------------

class HistoryChart(_Model):
    """One trend panel. `series` is already hourly-meaned and decimated, so the
    web app scales and draws — it never buckets. `unit` is appended to a value
    for display; `max` pins the y-axis when the metric has a natural ceiling
    (a percentage), and is None when the axis should fit the data."""
    key: str = ""
    label: str = ""
    unit: str = ""
    max: float | None = None
    series: list[Series] = Field(default_factory=list)

class HistoryScanRow(_Model):
    """One row of the scan log: what that scan saw, already counted."""
    ts: str = ""
    sites_ok: int = 0
    sites_total: int = 0
    hosts_ok: int = 0
    hosts_total: int = 0
    devices: int | None = None
    crit: int = 0
    warn: int = 0
    score: int | None = None

class HistoryEventRow(_Model):
    """One row of the event log — a device joining, a port opening, a WAN
    change, an applied fix. `href` is the in-app route the row opens."""
    ts: str = ""
    kind: str = ""
    entity: str = ""
    detail: str = ""
    href: str = ""
    network: NetworkRef | None = None

class HistoryPayload(_Model):
    # The window asked for, as asked for: a `days=` read reports `days=<n>,
    # hours=0`; a sub-day `hours=` read reports `days=0, hours=<n>` (Task 77).
    days: int = 7
    hours: int = 0
    network: NetworkRef | None = None
    scan_count: int = 0
    first_ts: str | None = None
    last_ts: str | None = None
    charts: list[HistoryChart] = Field(default_factory=list)
    scans: list[HistoryScanRow] = Field(default_factory=list)
    events: list[HistoryEventRow] = Field(default_factory=list)


class SitesPayload(_Model):
    sites: list[SiteCard] = Field(default_factory=list)
    # Latency / certificate-days-left / availability per site over
    # `payloads/assets.SITE_TREND_DAYS`, in the same rendering-ready shape the
    # History tab draws (Task 77 / F1).
    charts: list[HistoryChart] = Field(default_factory=list)

class HardeningCheck(_Model):
    name: str = ""
    passed: bool = False
    detail: str = ""

class HardeningGrade(_Model):
    score: int = 0
    grade: str = ""
    checks: list[HardeningCheck] = Field(default_factory=list)

class ContainerRow(_Model):
    name: str = ""
    status: str = ""
    image: str = ""

class ServiceRow(_Model):
    name: str = ""
    state: str = ""

class Gauge(_Model):
    name: str = ""
    value: float | None = None
    unit: str = ""
    max: float = 100.0
    state: str = "ok"       # ok|warn|crit

class PortRow(_Model):
    port: int = 0
    since: str = ""
    risky: bool = False
    service: str = ""

class HostCard(_Model):
    name: str = ""
    address: str = ""
    reachable: bool | None = None
    hostname: str = ""
    uptime: str = ""
    load1: float | None = None
    load_per_core: float | None = None
    disk_pct: float | None = None
    mem_pct: float | None = None
    failed_ssh_24h: int = 0
    banned: int = 0
    fail2ban: bool | None = None
    reboot_required: bool = False
    pending_updates: int = 0
    security_updates: int = 0
    cve_fixable: int = 0
    hardening: HardeningGrade = Field(default_factory=HardeningGrade)
    ports: list[PortRow] = Field(default_factory=list)
    docker: list[ContainerRow] = Field(default_factory=list)
    services: list[ServiceRow] = Field(default_factory=list)
    gauges: list[Gauge] = Field(default_factory=list)
    # Set when the sensor has stopped reporting this row entirely (see
    # payloads/staleness.py). The figures above are then a last-known state,
    # not a current reading, and the UI says so rather than letting them
    # present themselves as fresh.
    stale: bool = False
    stale_note: str = ""
    last_checked: str = ""

class HostsPayload(_Model):
    hosts: list[HostCard] = Field(default_factory=list)

class KindChip(_Model):
    group: str = ""
    count: int = 0
    online: int = 0
    network_id: str | None = None

class DeviceCard(_Model):
    mac: str = ""
    ip: str = ""
    name: str = ""
    label: str = ""
    vendor: str = ""
    kind: str = "unknown"
    group: str = ""
    trusted: bool = False
    always_on: bool = False
    quarantined: bool = False
    parent: str | None = None
    risk: int = 0
    presence: str = "NEW"       # ONLINE|SLEEPING|OFFLINE|NEW
    online: bool = False
    first_seen: str = ""
    last_seen: str = ""
    ports: list[PortRow] = Field(default_factory=list)
    unverified: bool = False
    randomized: bool = False
    network: NetworkRef | None = None
    # Percentage of the last `payloads/assets.DEVICE_ONLINE_DAYS` days' scans
    # that saw this device -- the reference console's "Online 7d" column, and
    # the same figure `ScreenTimePayload.devices[*].pct` reports for the same
    # mac and window. None means it was not measured for this shape (a drawer
    # child card), never 0 %.
    online_7d: float | None = None
    # Why this device was filed as a camera, in the sensor classifier's own
    # words ("camera vendor (Reolink…)", "answers on 554 (rtsp)"). Empty for
    # anything that is not a camera, and for a camera the hub's text fallback
    # classified rather than the sensor.
    camera_reasons: list[str] = Field(default_factory=list)

class DeviceGroup(_Model):
    group: str = ""
    devices: list[DeviceCard] = Field(default_factory=list)
    network: NetworkRef | None = None

class DeviceTiles(_Model):
    """The nine summary tiles the Devices view opens with (Task 70).

    Numbers only: every label, unit and tone the row draws is the web app's,
    because `design/Stat` already carries `label`/`unit`/`tone` and the hub has
    no business owning that copy. `DevicesPayload.online` / `.total` supply the
    first tile, so it has no field of its own here.

    The three internet figures and the DNS pair are the SENSOR HOST's own
    measurements, not a per-network property: with a network selected they come
    from that network's sensor's most recent scan, and with "All networks"
    chosen from the tenant's most recent scan.
    """
    #: Devices this scan saw for the first time, in the selected network (or
    #: summed across all of them).
    new_devices: int = 0
    trusted: int = 0
    #: Known MACs whose first octet has the locally-administered bit set.
    randomized: int = 0
    down_mbps: float | None = None
    up_mbps: float | None = None
    latency_ms: float | None = None
    dns_ms: int | None = None
    #: Whether the SYSTEM resolver answered at all. `None` = no sample; the
    #: tile is crit only for an explicit False, which is not the same thing.
    dns_ok: bool | None = None
    #: The public address of the selected network's egress. Empty when the
    #: selection spans more than one distinct address -- `wan_ips` then has
    #: them all, and the tile shows the count.
    wan_ip: str = ""
    #: Every distinct public address in the selection, sorted. One entry for a
    #: single-sensor tenant however many networks it watches, because one
    #: sensor has one egress.
    wan_ips: list[str] = Field(default_factory=list)

class DevicesPayload(_Model):
    groups: list[DeviceGroup] = Field(default_factory=list)
    chips: list[KindChip] = Field(default_factory=list)
    total: int = 0
    online: int = 0
    unverified: int = 0
    cameras: int = 0
    cameras_online: int = 0
    tiles: DeviceTiles = Field(default_factory=lambda: DeviceTiles())
    networks: list[NetworkHealth] = Field(default_factory=list)
    # Devices online, joins and leaves, and online-vs-known over
    # `payloads/assets.DEVICE_TREND_DAYS` (Task 77 / F5), in the same
    # rendering-ready shape the History tab draws.
    charts: list[HistoryChart] = Field(default_factory=list)

class DayHours(_Model):
    """One row of the presence heatmap: `day` is an ISO date, `hours` is 24
    fractions 0-1 of that hour the device was seen."""
    day: str = ""
    hours: list[float] = Field(default_factory=list)

class PresenceProfile(_Model):
    online_pct_24h: float = 0.0
    online_pct_7d: float = 0.0
    hours: list[float] = Field(default_factory=list)     # 24 averages, 0-1
    typical_hours: list[int] = Field(default_factory=list)
    days: list[DayHours] = Field(default_factory=list)
    strip: list[HourBlock] = Field(default_factory=list)

class DeviceDnsProfile(_Model):
    queries_24h: int = 0
    blocked_24h: int = 0
    top: list[NameCount] = Field(default_factory=list)
    blocked_top: list[NameCount] = Field(default_factory=list)

class NameSibling(_Model):
    """Another MAC that carried this device's name -- a randomized address
    rotating, most likely. A trusted sibling is the strongest sign a "new"
    MAC is a device the owner already knows."""
    mac: str = ""
    ip: str = ""
    trusted: bool = False
    first_seen: str = ""
    last_seen: str = ""

class DeviceFamiliarity(_Model):
    """Is this device one of ours? Evidence with a name, never certainty:
    `verdict` says which pattern the record fits and `reasons` lists the
    facts that fit it, so the owner can disagree with any one of them.
    See payloads/familiarity.py."""
    mac: str = ""
    name: str = ""
    label: str = ""
    ip: str = ""
    vendor: str = ""
    randomized_mac: bool = False
    trusted: bool = False
    #: resident | regular | visitor | unknown
    verdict: str = "unknown"
    summary: str = ""
    reasons: list[str] = Field(default_factory=list)
    days_seen: int = 0
    lookback_days: int = 30
    span_days: int = 0
    first_seen: str = ""
    last_seen: str = ""
    silent_days: int | None = None
    #: Share of sightings between midnight and 6am, 0-1.
    overnight_share: float = 0.0
    typical_hours: list[int] = Field(default_factory=list)
    joins: int = 0
    leaves: int = 0
    dns_queries: int = 0
    top_dns: list[str] = Field(default_factory=list)
    platform_hint: str = ""
    name_siblings: list[NameSibling] = Field(default_factory=list)
    other_networks: list[str] = Field(default_factory=list)

class RiskReason(_Model):
    """One line of a device's risk-score breakdown: how many points, and why."""
    points: int = 0
    why: str = ""

class DeviceDetail(_Model):
    device: DeviceCard = Field(default_factory=DeviceCard)
    #: Why the risk score is what it is, mirroring the sensor's own formula --
    #: "risk 40" on its own explained nothing; this is the arithmetic.
    risk_reasons: list[RiskReason] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)
    presence: PresenceProfile = Field(default_factory=PresenceProfile)
    ports: list[PortRow] = Field(default_factory=list)
    dns: DeviceDnsProfile = Field(default_factory=DeviceDnsProfile)
    alerts: list[InboxRow] = Field(default_factory=list)
    children: list[DeviceCard] = Field(default_factory=list)
    events: list[EventRow] = Field(default_factory=list)
    #: "Is this one of ours?" -- assembled from the device's own history.
    familiarity: DeviceFamiliarity = Field(default_factory=DeviceFamiliarity)

class ScreenTimeRow(_Model):
    mac: str = ""
    name: str = ""
    ip: str = ""
    trusted: bool = False
    total_h: float = 0.0
    pct: float = 0.0
    hours_per_day: float = 0.0
    hours: list[float] = Field(default_factory=list)
    network: NetworkRef | None = None

class ScreenTimePayload(_Model):
    days: int = 0
    scans: int = 0
    devices: list[ScreenTimeRow] = Field(default_factory=list)
    heatmap: list[HeatmapCell] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------

class Geo(_Model):
    country: str = ""
    country_code: str = ""
    city: str = ""
    org: str = ""
    isp: str = ""
    lat: float | None = None
    lon: float | None = None
    hosting: bool = False
    reverse: str = ""

class Scorecard(_Model):
    target: str = ""
    kind: str = ""          # site|host|lan
    grade: str = ""
    score: int = 0
    deductions: list[Deduction] = Field(default_factory=list)
    fix: str = ""

class CheckDecision(_Model):
    """The owner's own call on one failing check (see `CheckDetail` below):
    "open" (the default -- nobody has decided anything yet) or "left" (seen,
    and deliberately left as-is rather than an unseen failure). There is no
    "resolved" here -- an autofix-backed check resolves itself the moment
    the next scan shows it passing."""
    status: str = "open"    # open|left
    reason: str = ""
    decided_by: str = ""
    decided_at: str = ""

class SecurityCheck(_Model):
    key: str = ""
    area: str = ""          # identity|patching|exposure|wifi|dns|backups…
    title: str = ""
    passed: bool | None = None
    last_verified: str = ""
    evidence: str = ""
    autofix: str = ""       # fix action name, "" when there is none
    target: str = ""
    # The owner's decision on this exact check ("open" when nobody has
    # decided anything about it yet), so a row that was deliberately left
    # reads as a deliberate decision rather than an unseen failure.
    decision: CheckDecision = Field(default_factory=CheckDecision)

class WifiGuard(_Model):
    """`rows` are the individual Wi-Fi checks behind the verdict (evil twin,
    rogue DHCP, unknown devices…), same shape as any other SecurityCheck."""
    verdict: str = ""
    state: str = "ok"       # ok|warn|crit|unknown
    ssid: str = ""
    security: str = ""
    channel: int | None = None
    phy: str = ""
    signal: int | None = None
    evil_twin: bool = False
    twin_channels: list[int] = Field(default_factory=list)
    rogue_dhcp: bool = False
    dhcp_server: str = ""
    expected_gateway: str = ""
    # The two facts the reference's "Rogue network checks" box shows beside the
    # DHCP server (Task 77 / F8): the route actually in use, and the MAC
    # answering as the router -- a changed router MAC on an unchanged address is
    # what ARP spoofing looks like. "" when this network's sensor has not
    # reported them.
    default_gateway: str = ""
    gateway_mac: str = ""
    unknown_devices: int = 0
    rows: list[SecurityCheck] = Field(default_factory=list)
    network: NetworkRef | None = None

class RepairProof(_Model):
    """One repair Vault ran, and the evidence it worked.

    `ran_ok` and `confirmed` are deliberately separate, the same way
    `Fix.ok` and `Fix.verified_at` are. A command that exits 0 has not
    fixed anything; it has reported that it thinks it did. `confirmed` is
    set only when a LATER scan looked at the target again and found the
    thing actually changed (`verification.outcome`). Folding the two into
    one "fixed" flag is how a product ends up telling a customer something
    is sorted when nobody ever checked.
    """
    #: What was run, in the customer's words and in Vault's.
    label: str = ""
    action: str = ""
    target: str = ""
    ran_at: str = ""
    #: Who: a person's name, or "Vault" when it ran on its own. Routed
    #: through `payloads/account.is_person`, the one classifier for this.
    by: str = ""
    by_person: bool = False
    #: The command reported success. NOT proof.
    ran_ok: bool = False
    #: A later scan looked again and found it changed. THIS is the proof.
    confirmed_at: str = ""
    #: What that later look saw, in one sentence -- or what is still
    #: missing, when nothing has confirmed it yet.
    proof: str = ""
    #: The tail of what the command said, for anyone who wants it.
    output: str = ""


class CheckDetail(_Model):
    """`GET /api/v1/security/checks/detail` -- the drill-down for one failing
    Wi-Fi guard row, checklist row, or posture deduction (owner request: "when
    something is wrong... clicking on evil twin for example it should give me
    more information and I can say yes resolve or leave it").

    Deliberately a separate, on-demand payload rather than fields bolted onto
    every `SecurityCheck` on every poll: `what_we_saw`/`why_it_matters` are
    prose, and `remedy`/`chat_opening` require resolving the check's fix (a
    query for every failing check on every `/api/v1/security` load, most of
    which the owner never opens).
    """
    key: str = ""
    area: str = ""
    title: str = ""
    target: str = ""
    network_id: str = ""
    passed: bool | None = None
    last_verified: str = ""
    # The evidence Vault saw, and the same fact restated as plain
    # sentences -- the evidence string is a log fragment ("twin_channels: [6,
    # 100]"); `what_we_saw` is the sentence a non-technical reader wants.
    evidence: str = ""
    what_we_saw: str = ""
    why_it_matters: str = ""
    # "confirmed" for a deterministic reading (a firewall is either active or
    # not); "uncertain" for a check where the same evidence has more than one
    # honest explanation. The Wi-Fi evil-twin check used to be the one case,
    # back when it fired on "the SSID is on another channel" -- which is a
    # mesh node as often as an attacker. It now fires only on a twin using
    # different encryption, so it is confirmed like the rest.
    confidence: str = "confirmed"   # confirmed|uncertain
    # What Vault can do about it, mirroring `AlertRemedy` exactly (the
    # inbox's own "what Vault can do" contract) so the drawer's Resolve
    # button reads the same way everywhere in the app.
    remedy: AlertRemedy = Field(default_factory=AlertRemedy)
    decision: CheckDecision = Field(default_factory=CheckDecision)
    #: Item 25: what Vault actually DID about this, and whether a later
    #: look confirmed it. Newest first. Empty when nothing has been run --
    #: which is itself the answer to "was this ever fixed?".
    repairs: list[RepairProof] = Field(default_factory=list)

class EmailFinding(_Model):
    tone: str = "info"          # bad|warn|info|good
    text: str = ""


class EmailVerdictOut(_Model):
    """`GET /api/v1/security/email-check?address=`.

    `verdict` is deliberately never "safe": no DNS check can establish
    that, and a green tick on a well-run phishing domain is worse than no
    tool at all. `unproven` — "nothing wrong found" — is the best answer
    available, and `limits` says what was not checked.
    """
    address: str = ""
    domain: str = ""
    verdict: str = "unknown"    # forged|risky|unproven|unknown
    headline: str = ""
    findings: list[EmailFinding] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
    reputation_note: str = ""


class FirewallRule(_Model):
    """One gateway rule Vault proposes, with the evidence for it."""
    id: str = ""
    key: str = ""
    action: str = "block_inbound"
    proto: str = "tcp"
    port: int = 0
    target: str = ""
    title: str = ""
    because: str = ""
    evidence: str = ""
    risk: str = "WARN"          # CRIT|WARN
    status: str = "proposed"    # proposed|declined|applied|rolled_back|expired
    decided_by: str = ""
    decided_at: str = ""
    reason: str = ""
    applied_at: str = ""
    rollback_at: str = ""
    first_seen: str = ""
    last_seen: str = ""


class FirewallPayload(_Model):
    """`GET /api/v1/security/firewall`.

    `can_apply` is false today and says why in `why_not`: Vault can
    propose a rule and explain it, but writing one to somebody's router is
    an access it does not have and must not acquire quietly. The UI shows
    the reason rather than a button that does nothing.
    """
    rules: list[FirewallRule] = Field(default_factory=list)
    can_apply: bool = False
    why_not: str = ""
    gateway: str = ""


class Attacker(_Model):
    ip: str = ""
    host: str = ""
    fails: int = 0
    days: int = 0
    first_seen: str = ""
    last_seen: str = ""
    geo: Geo | None = None
    action: str = ""            # block_ip|block_subnet|ban_long|limit_ssh|off
    action_ts: str | None = None
    alert_key: str = ""
    network: NetworkRef | None = None

class ExposureTile(_Model):
    label: str = ""
    value: str = ""
    state: str = "ok"       # ok|warn|crit
    detail: str = ""

class ExtraCheck(_Model):
    """One config-defined probe beyond the HTTP site checks (Nexus
    `checks.extra_checks`): a TCP connect, an ICMP ping or a DNS lookup
    declared in `config.checks`. `ok=None` means the sensor has not run it
    since the hub last saw a scan."""
    name: str = ""
    type: str = ""              # tcp|icmp|dns
    target: str = ""
    port: int | None = None
    ok: bool | None = None
    latency_ms: int | None = None
    detail: str = ""

class HeartbeatRow(_Model):
    """A dead-man switch: a job that must call `/hb/<name>/<token>` at least
    every `every_min` minutes. `late` is the hub's verdict, computed from
    `last_ping` -- the row is authoritative (the ping endpoint writes it),
    the sensor only contributes `note`."""
    name: str = ""
    every_min: int = 0
    last_ping: str = ""         # ISO-8601, "" when it has never pinged
    age_min: int | None = None  # None when it has never pinged
    late: bool = False
    note: str = ""

class HeartbeatSetup(_Model):
    """The one line that wires a job to a heartbeat -- `command` DOES carry
    the heartbeat's token, because that is the whole point of it.

    This is why it is its own model and not a field on `HeartbeatRow`:
    `HeartbeatRow` lives inside `SecurityPayload`, which every signed-in
    viewer reads and which `reports/sections.py` prints into built reports,
    and reports are shareable through unauthenticated `/r/<token>` links. A
    token-bearing field there would leak on the next report build. The
    routes that return THIS model are admin-only and audited, so the
    credential is only ever rendered for a signed-in admin, on the app's own
    origin, at the moment they ask for it.

    `rotated` is true when the reply is the result of a rotation, i.e. the
    previous command stopped working the instant this one was produced.
    """
    name: str = ""
    url: str = ""               # the ping URL, token included
    command: str = ""           # `curl -s <url>` -- what to paste into the job
    rotated: bool = False

class DomainRow(_Model):
    """Registrable-domain expiry from the sensor's daily whois
    (`domains.domain_expiry`). `days_left` is None when whois gave no date;
    `error` carries the reason when it failed outright."""
    domain: str = ""
    expires: str = ""
    days_left: int | None = None
    error: str = ""

class EmailAuthRow(_Model):
    """One monitored domain's anti-spoofing posture, from the sensor's daily
    DNS reads (`Scan.raw["email_sec"]`). `spf`/`dmarc` carry the records
    themselves (empty when absent) so the panel can show what is published, not
    only a tick; `dkim_selector` is the first selector that answered, "" when
    none did. `passed` is the same verdict the Email security check makes: SPF
    present and DMARC enforcing."""
    domain: str = ""
    spf: str = ""
    dkim_selector: str = ""
    dmarc: str = ""
    dmarc_policy: str = ""
    has_mx: bool = False
    passed: bool = False
    checked: str = ""
    #: Measured and shown, never chased. A domain the customer does not
    #: control (`email.exclude` on the sensor) gets no checklist row, no
    #: score deduction and no nudge — being told to fix DNS only its owner
    #: can change is the nagging that gets a security product ignored.
    advisory: bool = False

class ExtPortRow(_Model):
    """What the outside world answers on for one site host, probed from a VPS
    (`Scan.raw["external_sites"]`). `unexpected` is `ports` minus the allowed
    set (22/80/443 plus the tenant's own `home.site_allowed_ports`);
    `findings` names any admin path the probe actually matched on that host."""
    host: str = ""
    ip: str = ""
    ports: list[int] = Field(default_factory=list)
    unexpected: list[int] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    probe_host: str = ""
    checked: str = ""

class FixLogRow(_Model):
    """One repair Vault ran, automatic or asked for -- the reference's fix
    log. `by` is "auto" for the unattended runs and a username for a pressed
    button."""
    ts: str = ""
    host: str = ""
    action: str = ""
    label: str = ""
    by: str = "auto"
    ok: bool = False
    detail: str = ""

class VulnFinding(_Model):
    """One thing the manual scan saw. `fix` names the Vault remediation
    that clears it when there is one; deciding to run it is the owner's."""
    severity: str = "info"        # critical | high | medium | low | info
    title: str = ""
    evidence: str = ""
    port: int | None = None
    fix: str | None = None
    cve: str | None = None
    #: Set on LAN-wide scans: which device this came from.
    target: str = ""

class VulnPort(_Model):
    port: int = 0
    service: str = ""
    banner: str = ""
    http: int | None = None
    tls_version: str = ""
    tls_verified: bool | None = None

class VulnScanRow(_Model):
    """A queued or finished manual scan (`Queue` rows on /api/vuln-scan)."""
    id: str = ""
    status: str = ""             # queued | running | done | failed
    kind: str = ""               # device | host | site | lan
    target: str = ""
    profile: str = "quick"
    who: str = ""
    queued_at: str = ""
    finished_at: str | None = None
    duration_s: float | None = None
    score: int | None = None
    summary: dict[str, int] = Field(default_factory=dict)
    findings: list[VulnFinding] = Field(default_factory=list)
    ports: list[VulnPort] = Field(default_factory=list)
    error: str = ""

class VulnScanTarget(_Model):
    kind: str = ""               # device | host | site | lan
    target: str = ""
    label: str = ""

class VulnScansPayload(_Model):
    scans: list[VulnScanRow] = Field(default_factory=list)
    targets: list[VulnScanTarget] = Field(default_factory=list)

class DeviceDefence(_Model):
    """One protection the machine Vault runs on either has switched on or
    does not. `state` is on|off|unknown -- never a bare boolean, because a
    check that could not run is not the same as one that failed."""
    key: str = ""
    label: str = ""
    state: str = "unknown"      # on | off | unknown
    buys: str = ""              # what it protects you from, in plain words

class ThreatsBlocked(_Model):
    """How many lookups Vault refused, by what kind of thing it was."""
    category: str = ""          # dark web | malware | phishing | ads | blocked by you
    count: int = 0

class BlockedLookup(_Model):
    """One refused lookup, so the counts above can be checked rather than
    taken on trust."""
    name: str = ""
    device: str = ""
    category: str = ""
    ts: str = ""

class ThreatSignal(_Model):
    """One alert that is open right now and belongs to a threat class, so the
    class's verdict can be checked against the thing that produced it."""
    key: str = ""
    title: str = ""
    severity: str = ""

class ThreatClass(_Model):
    """One named kind of attack, and what Vault honestly does about it.

    `stance` is the whole point of this model, and it is allowed to say that
    Vault does nothing:

      blocking     -- stopped before it reaches the device
      watching     -- Vault detects it and tells you
      effect-only  -- Vault sees the damage, not the attack, and cannot stop it
      indirect     -- no detection; Vault only reduces the odds
    """
    key: str = ""
    label: str = ""
    what: str = ""              # what the attack is, in plain English
    stance: str = "watching"
    does: str = ""              # what Guardian does about it
    limit: str = ""             # what Guardian does NOT do -- "" when nothing to admit
    open_count: int = 0
    blocked: int = 0            # lookups refused that belong to this class
    signals: list[ThreatSignal] = Field(default_factory=list)

class ThreatFinding(_Model):
    """One alert belonging to a threat class, open or settled.

    Distinct from `ThreatSignal`, which is the card's four-item teaser and
    carries only enough to recognise a problem. This is the answer to "what did
    you actually find, and what happened to it", so it carries the `why` and
    the `evidence` behind the headline — and, for anything no longer open, what
    settled it.
    """
    key: str = ""
    title: str = ""
    severity: str = ""
    #: Vault's own explanation. The card has no room for it; it is most of
    #: the reason to open the drawer at all.
    why: str = ""
    evidence: dict = Field(default_factory=dict)
    status: str = ""
    #: True when `status` is in `models.SETTLED_ALERT_STATUSES`. Precomputed
    #: rather than left to the browser, so "is this dealt with" is decided in
    #: one place by the same constant the rest of the hub uses.
    resolved: bool = False
    #: The repair that ran, "" when none did. A settled alert with no fix was
    #: settled by a person deciding it did not need one, which is a different
    #: story and must not read as Vault having repaired something.
    fix: str = ""
    resolved_by: str = ""
    first_seen: str = ""
    last_seen: str = ""
    count: int = 0


class ThreatDetail(_Model):
    """`GET /api/v1/security/threats/{key}` — one attack class, opened up.

    The card answers "does Vault do anything about this, and is anything
    wrong now". This answers the two questions the card cannot: what was found,
    and for the things no longer open, how they were settled.

    `limit` is carried through deliberately. For `dos` (effect-only) and
    `ransomware` (indirect) the honest answer to "how did Vault solve it" is
    that it did not and cannot — `ThreatMatrix`'s docstring calls that
    admission the feature, so a drawer that dropped it would turn an honest
    card into a misleading popup.
    """
    key: str = ""
    label: str = ""
    what: str = ""
    stance: str = "watching"
    does: str = ""
    limit: str = ""
    days: int = 7
    open_count: int = 0
    resolved_count: int = 0
    #: Lookups refused in the window that belong to this class. A DNS count
    #: from `DnsQuery`, NOT an alert count — the two must never be added
    #: together, because the sum would mean nothing.
    blocked: int = 0
    findings: list[ThreatFinding] = Field(default_factory=list)


class ProtectionPanel(_Model):
    """What Vault is doing to keep malware and the dark web off the
    network, and whether the machines it runs on are defending themselves.

    Deliberately NOT called antivirus: Vault has no agent on phones or
    TVs and cannot scan them. This is what it can honestly show.
    """
    defences: list[DeviceDefence] = Field(default_factory=list)
    defences_host: str = ""     # which machine the defences belong to
    blocked: list[ThreatsBlocked] = Field(default_factory=list)
    blocked_total: int = 0
    recent: list[BlockedLookup] = Field(default_factory=list)
    filtering: bool = False     # is Guardian's DNS filter actually answering
    days: int = 7
    #: Every named attack type, and what Vault does about each.
    threats: list[ThreatClass] = Field(default_factory=list)

class PostureCoverage(_Model):
    """Item 05: what the posture CARDS speak for, and what they do not.

    Distinct from `ScoreCoverage` above and deliberately so: that one is
    about how much of a single score's deductions the sensor could measure;
    this one is about how many targets have a card at all.

    A target the sensor has stopped reporting is excluded from scoring on
    purpose (`security._still_checked`) -- a frozen row stamped as verified
    now is worse than no row. Excluding it silently is the part that was
    wrong: the client counts the cards and assumes that is the estate.
    """
    scored: int = 0
    total: int = 0
    unscored: list[str] = Field(default_factory=list)


class SecurityPayload(_Model):
    scorecards: list[Scorecard] = Field(default_factory=list)
    posture_coverage: PostureCoverage = Field(default_factory=PostureCoverage)
    wifi: WifiGuard = Field(default_factory=WifiGuard)
    checks: list[SecurityCheck] = Field(default_factory=list)
    attackers: list[Attacker] = Field(default_factory=list)
    exposure: list[ExposureTile] = Field(default_factory=list)
    attacker_method: str = "auto"
    methods: list[str] = Field(default_factory=list)
    networks: list[NetworkHealth] = Field(default_factory=list)
    # The per-network Wi-Fi guards. `wifi` above stays the PRIMARY
    # network's guard so every existing caller keeps working.
    wifis: list[WifiGuard] = Field(default_factory=list)
    # Task 58 (G2): three surfaces the sensor has always collected and the
    # report has always printed, but the dashboard never showed.
    extra_checks: list[ExtraCheck] = Field(default_factory=list)
    heartbeats: list[HeartbeatRow] = Field(default_factory=list)
    domains: list[DomainRow] = Field(default_factory=list)
    # Task 77 (F8): three surfaces the sensor has always collected into
    # `Scan.raw` and that reached no payload field -- email anti-spoofing per
    # domain, what the internet answers on per site host, and the log of every
    # repair Vault has run. The reference's fourth panel, "Rogue network
    # checks", is rendered from the Wi-Fi guard's own fields rather than as a
    # second copy of the same three facts.
    email: list[EmailAuthRow] = Field(default_factory=list)
    ext_ports: list[ExtPortRow] = Field(default_factory=list)
    fix_log: list[FixLogRow] = Field(default_factory=list)
    #: Malware, phishing and dark-web blocking, plus the host's own defences.
    protection: ProtectionPanel = Field(default_factory=ProtectionPanel)


# --------------------------------------------------------------------------
# Updates
# --------------------------------------------------------------------------

class PatchDay(_Model):
    day: str = ""           # ISO date
    host: str = ""
    patches: int = 0
    reboots: int = 0
    state: str = "none"     # none|planned|done|failed

class UpdateHost(_Model):
    #: False when the last sweep did not reach this server at all. Its
    #: counts are then all zero because Vault HAS no counts for it --
    #: not because it is up to date. See `updates.hosts`, item 30.
    checked: bool = True
    name: str = ""
    os: str = ""
    kernel: str = ""
    kernel_latest: str = ""
    pending: int = 0
    security_pending: int = 0
    cve_fixable: int = 0
    unattended: bool | None = None
    reboot_required: bool = False
    last_apt: str = ""
    # What is actually waiting, by name (Task 77 / F4). `pending` stays the
    # host's own count: `packages` is capped at `payloads/updates.MAX_PACKAGES`
    # so a host with six hundred held-back packages cannot make this payload
    # unbounded, and `pending` is how a reader knows the list was trimmed.
    # `security_packages` is the subset apt marks as security, drawn out so the
    # UI can tone those without re-deriving the set.
    packages: list[str] = Field(default_factory=list)
    security_packages: list[str] = Field(default_factory=list)

class MacUpdates(_Model):
    """The sensor host's OWN pending updates -- the reference console's "This
    Mac" panel, from `Scan.raw["mac_updates"]` (`softwareupdate -l`). `error`
    carries the reason the check failed; `checked` is "" when it has never
    run. Vault cannot install these, and the panel says so."""
    os: str = ""
    available: list[str] = Field(default_factory=list)
    checked: str = ""
    error: str = ""
    # False when the sensor has never reported the block at all, which is not
    # the same claim as "up to date".
    reported: bool = False

class CveRow(_Model):
    cve: str = ""
    package: str = ""
    urgency: str = ""
    host: str = ""
    fixable: bool = False
    fixed_on: str = ""
    tracker: str = ""

class UpgradeProgress(_Model):
    queue_id: str = ""
    host: str = ""
    action: str = ""
    status: str = "queued"
    done: int = 0
    total: int = 0
    log_tail: str = ""
    started: str | None = None
    finished: str | None = None

class RebootCandidate(_Model):
    host: str = ""
    reason: str = ""
    services: list[str] = Field(default_factory=list)
    queued: bool = False

class RebootWindow(_Model):
    label: str = ""
    start: str = ""
    end: str = ""

class RebootPlan(_Model):
    hosts: list[RebootCandidate] = Field(default_factory=list)
    windows: list[RebootWindow] = Field(default_factory=list)

class FixConfirmation(_Model):
    """The one-shot confirmation a disruptive (tier-3) fix needs before it can
    run: `POST /api/v1/fix/confirm` mints it, the UI shows `summary`, and
    `POST /api/v1/fix` burns `token`. Bound to one tenant, one user and one
    exact (host, action, arg) -- see `routers/write.fix`."""
    token: str = ""
    expires: str = ""
    tier: int = 3
    host: str = ""
    action: str = ""
    summary: str = ""

class PatchPosture(_Model):
    """Item 29: the whole update picture in one line, so Security can answer
    "am I patched" without sending the reader to another tab.

    `unmeasured` is the honest half: a phone, camera or thermostat has no
    package list Vault can read, and a total that silently skipped them
    would imply an all-clear it has not earned.
    """
    pending: int = 0
    security_pending: int = 0
    needs_reboot: int = 0
    servers_checked: int = 0
    servers_unchecked: int = 0
    unmeasured: int = 0


class UpdatesPayload(_Model):
    calendar: list[PatchDay] = Field(default_factory=list)
    hosts: list[UpdateHost] = Field(default_factory=list)
    cves: list[CveRow] = Field(default_factory=list)
    progress: list[UpgradeProgress] = Field(default_factory=list)
    reboot_plan: RebootPlan = Field(default_factory=RebootPlan)
    mac: MacUpdates = Field(default_factory=lambda: MacUpdates())
    posture: PatchPosture = Field(default_factory=PatchPosture)


# --------------------------------------------------------------------------
# DNS
# --------------------------------------------------------------------------

class DnsStatus(_Model):
    enabled: bool = False
    running: bool = False
    queries_24h: int = 0
    blocked_24h: int = 0
    block_pct: float = 0.0
    cache: int = 0
    upstream_ms_avg: float | None = None
    total_blocked_domains: int = 0
    family_filter: bool = False
    uptime_s: int = 0
    ts: str | None = None
    #: How much of the network these figures actually describe.
    #:
    #: Everything above is computed from the resolver's own logs, so it says
    #: nothing at all about a device that never asks Vault. "3.2% of
    #: lookups blocked" on a 26-device network where four use the resolver is
    #: a true statement about four devices and reads as one about 26. Worse,
    #: the device doing the most concerning lookups is the one most likely to
    #: be on its own resolver, and it appears here as perfect silence.
    #:
    #: `clients_using` counts devices seen asking the resolver in the window;
    #: `devices_total` is every device known on the networks in scope. Note
    #: this measures whether a device asked GUARDIAN, not whether it used DNS
    #: -- a device on DoH or DoT is indistinguishable from one that made no
    #: lookups, so low coverage is currently the only signal that gap exists.
    clients_using: int = 0
    devices_total: int = 0
    #: 0 when there are no devices, never 100: an empty network is not fully
    #: covered, it is unmeasured.
    coverage_pct: int = 0

class DnsClient(_Model):
    mac: str | None = None
    ip: str = ""
    name: str = ""
    queries: int = 0
    blocked: int = 0
    network: NetworkRef | None = None

class DnsRules(_Model):
    allow: list[str] = Field(default_factory=list)
    block: list[str] = Field(default_factory=list)

class DnsList(_Model):
    name: str = ""
    category: str = ""
    entries: int = 0
    blocked_24h: int = 0
    enabled: bool = True

class DnsWizard(_Model):
    resolver_ip: str = ""
    router_hint: str = ""
    working: bool = False
    clients_using: int = 0
    checked_at: str | None = None

class DnsStreamRow(_Model):
    ts: str = ""
    mac: str | None = None
    client: str = ""
    name: str = ""
    qtype: str = ""
    blocked: bool = False
    list: str | None = None
    ms: int = 0
    device: str = ""        # friendly device name for `mac`, "" when unknown
    network: NetworkRef | None = None

class ConnStreamRow(_Model):
    """One connection on the Live connections tab (`flow` live frame).

    Deliberately nothing a stored `Flow` row does not already hold: Vault
    never keeps or forwards a packet body, and a live view is not a reason to
    start. `peer` is the best name for the far end -- the TLS server name the
    sensor read from the handshake, else the resolved name, else the IP.
    """
    ts: str = ""
    mac: str = ""
    ip: str = ""
    device: str = ""        # friendly device name for `mac`, "" when unknown
    peer: str = ""
    peer_ip: str = ""
    peer_port: int = 0
    proto: str = ""
    bytes_in: int = 0
    bytes_out: int = 0


class ConnectionDest(_Model):
    """One place a device talked to, in the window, named for a person."""
    host: str = ""            # TLS server name or resolved name; "" when none was seen
    peer_ip: str = ""
    port: int = 0
    proto: str = ""
    name: str = ""            # "Anthropic", or the host/address when unrecognised
    what: str = ""            # "AI service"; "" when unknown
    sure: bool = False        # True only when the hostname itself says whose it is
    port_meaning: str = ""    # "Encrypted web", "Clock sync", ...
    local: bool = False       # the far end is on this network
    bytes_in: int = 0
    bytes_out: int = 0
    first_seen: str = ""
    last_seen: str = ""
    is_new: bool = False      # nobody here had contacted it before this window

class ConnectionDevice(_Model):
    mac: str = ""
    name: str = ""
    ip: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    last_seen: str = ""
    destinations: list[ConnectionDest] = Field(default_factory=list)

class ConnectionsPayload(_Model):
    """`GET /traffic/connections`: the Live connections page, grouped."""
    mode: str = "none"        # mirror|router|host|none -- see networks.TRAFFIC_MODES
    why_none: str = ""
    window_minutes: int = 15
    devices: list[ConnectionDevice] = Field(default_factory=list)
    destinations_total: int = 0
    new_total: int = 0


class DnsPayload(_Model):
    status: DnsStatus = Field(default_factory=DnsStatus)
    top: list[NameCount] = Field(default_factory=list)
    blocked_top: list[NameCount] = Field(default_factory=list)
    clients: list[DnsClient] = Field(default_factory=list)
    rules: DnsRules = Field(default_factory=DnsRules)
    lists: list[DnsList] = Field(default_factory=list)
    beaconing: list[NameCount] = Field(default_factory=list)
    suspicious: list[NameCount] = Field(default_factory=list)
    wizard: DnsWizard = Field(default_factory=DnsWizard)


# --------------------------------------------------------------------------
# Traffic (who talked to whom -- payloads/traffic.py)
# --------------------------------------------------------------------------

class TrafficPeer(_Model):
    peer: str = ""           # DNS name when known, else IP
    peer_ip: str = ""
    peer_port: int = 0
    proto: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    first_seen: str = ""
    last_seen: str = ""

class TrafficTalker(_Model):
    mac: str = ""
    name: str = ""            # label or name or vendor or mac -- display only
    ip: str = ""
    bytes_in: int = 0
    bytes_out: int = 0
    top_peer: str = ""

class TrafficOverview(_Model):
    mode: str = "none"        # mirror|router|host|none -- see networks.TRAFFIC_MODES
    #: Item 20: why there is nothing to show, when there is nothing to show.
    #: Empty means Vault IS watching and the window really was quiet --
    #: which is a useful answer. Non-empty means it was never asked to look,
    #: and saying "nothing unusual" then is a lie of omission.
    why_none: str = ""
    truncated: bool = False
    window_hours: int = 1
    top_talkers: list[TrafficTalker] = Field(default_factory=list)
    top_peers: list[TrafficPeer] = Field(default_factory=list)
    new_peers: list[TrafficPeer] = Field(default_factory=list)
    external_share: float = 0.0

class DeviceTraffic(_Model):
    mode: str = "none"
    mac: str = ""
    window_hours: int = 24
    bytes_in: int = 0
    bytes_out: int = 0
    peers: list[TrafficPeer] = Field(default_factory=list)
    #: Bytes per hour, oldest first, length always == window_hours. A strip,
    #: not a trend -- `payloads/history.py` is the only place a trend is
    #: computed, and this module never draws one.
    hourly: list[int] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------

class TopoNode(_Model):
    id: str = ""
    kind: str = ""          # wan|router|switch|ap|device|host|site|sensor
    group: str = ""
    label: str = ""
    ip: str = ""
    mac: str | None = None
    icon: str = ""
    state: str = "ok"       # ok|warn|crit|offline
    risk: int = 0
    ring: int = 0           # 0 = internet, 1 = gateway, 2 = infra, 3 = leaves
    trusted: bool = False
    quarantined: bool = False
    pulse: bool = False
    detail: str = ""
    network: NetworkRef | None = None
    #: Worst OPEN alert on this node (CRIT|WARN|None) -- what turns the link
    #: leading to it red or yellow. Computed hub-side so the map, the drawer
    #: and the inbox never disagree about what is wrong.
    alert_sev: str | None = None
    #: Keys of those open WARN/CRIT alerts -- the inbox rows a coloured link
    #: opens, so the map's "what is wrong here" is the inbox's own rows.
    alert_keys: list[str] = Field(default_factory=list)

class TopoLink(_Model):
    source: str = ""
    target: str = ""
    kind: str = ""          # wired|wifi|wan|logical
    pulse: bool = False

class TopoCluster(_Model):
    id: str = ""
    label: str = ""
    members: list[str] = Field(default_factory=list)
    network_id: str | None = None

class TopologyPayload(_Model):
    nodes: list[TopoNode] = Field(default_factory=list)
    links: list[TopoLink] = Field(default_factory=list)
    clusters: list[TopoCluster] = Field(default_factory=list)
    networks: list[NetworkHealth] = Field(default_factory=list)  # round 2: per-network strip, same shape DevicesPayload uses


# --------------------------------------------------------------------------
# Incident detail (Task 83, patterns A1/A2)
# --------------------------------------------------------------------------

class IncidentNarrative(_Model):
    """The four fixed sections of an incident's written story, always in this
    order (see `payloads/incidents.narrative()`): what happened, what it
    means, how sure Vault is, and what happens next. Every sentence is
    built from the incident's own alerts -- never invented -- so a section
    Vault has nothing real to say for is "" and the UI omits it rather
    than rendering a placeholder."""
    what_happened: str = ""
    what_it_means: str = ""
    confidence: int | None = None    # None: no alert under this incident carries a scored confidence
    confidence_basis: str = ""       # e.g. "averaged from the 3 alerts Guardian scored"
    what_next: str = ""
    need: str = ""                   # exactly what is needed from the owner, "" when nothing is

class IncidentTimelineItem(_Model):
    ts: str = ""
    tone: str = "plain"     # good|warn|alert|plain
    text: str = ""

class IncidentAsset(_Model):
    """One of the two-to-three things this incident touched -- never the
    whole estate. `risk` is null when Vault has not scored this asset (a
    `Host` has no risk score; only `Device` rows do)."""
    id: str = ""
    kind: str = "host"      # host|device|external
    name: str = ""
    subtitle: str = ""
    risk: int | None = None
    href: str = ""

class IncidentEvidenceRow(_Model):
    """One row of the raw log/finding the narrative was built from."""
    ts: str = ""
    where: str = ""
    what: str = ""
    seen: int = 1

class IncidentGraph(_Model):
    """The scoped graph: only the nodes and links this incident touched,
    reusing the topology map's own node/link shapes -- never the full-tenant
    map, which stays under Topology."""
    nodes: list[TopoNode] = Field(default_factory=list)
    links: list[TopoLink] = Field(default_factory=list)

class IncidentDetail(_Model):
    """One incident, one page: header, narrative, timeline, the assets
    involved, a scoped graph and the evidence rows -- everything about one
    correlated story reachable without leaving it (spec A2)."""
    id: str = ""
    title: str = ""
    sev: Sev = "WARN"
    status: str = "open"
    entity: str = ""
    opened: str = ""
    closed: str | None = None
    alert_count: int = 0
    narrative: IncidentNarrative = Field(default_factory=IncidentNarrative)
    timeline: list[IncidentTimelineItem] = Field(default_factory=list)
    assets: list[IncidentAsset] = Field(default_factory=list)
    graph: IncidentGraph = Field(default_factory=IncidentGraph)
    evidence: list[IncidentEvidenceRow] = Field(default_factory=list)
    # The primary alert's own remedy/actions, read-only-safe for every role;
    # `actions` is the exact set of routes the caller's role may POST, same
    # contract as `InboxRow.actions` -- empty, never disabled, when the role
    # may not run them.
    remedy: AlertRemedy | None = None
    actions: list[RowAction] = Field(default_factory=list)
    # The alert key `remedy`/`actions` describe -- the frontend's "Ask
    # Vault" hand-off is keyed on an alert, not an incident.
    primary_key: str = ""


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

class ReportCard(_Model):
    id: str = ""
    kind: str = ""          # weekly|monthly|manual
    period_days: int = 0
    preset: str = ""
    title: str = ""
    created: str = ""
    score: int | None = None
    url: str = ""
    share_url: str | None = None
    share_expires: str | None = None

class ReportPreset(_Model):
    key: str = ""
    label: str = ""
    sections: list[str] = Field(default_factory=list)

class ReportSection(_Model):
    key: str = ""
    title: str = ""

class ReportSchedule(_Model):
    weekly: bool = False
    monthly: bool = False
    preset: str = ""
    ntfy: bool = True
    hour: int = 8

class ValueStat(_Model):
    """One number on the Reports value strip, with the window it covers.

    `note` is not decoration: several of these are easy to read as a stronger
    claim than the data supports, and the note is where the weaker, true
    reading lives.
    """
    key: str = ""
    label: str = ""
    value: str = ""
    note: str = ""

class WorkRow(_Model):
    """Repairs grouped into something a non-technical owner recognises."""
    label: str = ""
    count: int = 0

class ValueStrip(_Model):
    days: int = 30
    stats: list[ValueStat] = Field(default_factory=list)
    work: list[WorkRow] = Field(default_factory=list)

class ReportsPayload(_Model):
    library: list[ReportCard] = Field(default_factory=list)
    presets: list[ReportPreset] = Field(default_factory=list)
    sections: list[ReportSection] = Field(default_factory=list)
    schedule: ReportSchedule = Field(default_factory=ReportSchedule)
    #: What Vault did for you, over a window.
    value: ValueStrip = Field(default_factory=ValueStrip)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

class ProfileSettings(_Model):
    owner: str = ""
    email: str = ""
    address: str = ""
    company: str = ""

class UserRow(_Model):
    username: str = ""
    display_name: str = ""
    email: str = ""
    role: str = "viewer"
    is_superadmin: bool = False
    last_seen: str | None = None

class SiteTarget(_Model):
    """`expect` is the LIST of acceptable HTTP status codes, empty = the
    sensor's own default of `[200]`. A list, not a single int: the sensor
    tests `code in site["expect"]` (`sensor/sites.py`), a real config
    carries `[200, 307, 302]` for a site that redirects, and a single int
    stored there would make that membership test raise."""
    name: str = ""
    url: str = ""
    expect: list[int] = Field(default_factory=list)
    public: bool = False
    host: str = ""
    # Which `ssh_hosts` entry (by name) actually runs this site's web
    # server, "" when unset. Never inferred from the URL -- a site can sit
    # behind a CDN or a box with no ssh_hosts entry at all -- so this is
    # the one thing that turns a "Security headers present" finding from a
    # human errand into something `apply_fix("security_headers", ...)` may
    # run: see `payloads/security.py`'s `_headers_fix_target`.
    ssh_host: str = ""

class HostTarget(_Model):
    """`expect_docker` -- renamed from `expect_containers`, which the
    dashboard wrote but nothing ever read: the sensor's own field, and the
    one `_merge_targets` preserves on a real config, is `expect_docker`
    (`sensor/alerts.py`, `sensor/fix.py`). `expect_containers` is still
    accepted on read (`_host_targets`) for one release."""
    name: str = ""
    target: str = ""
    optional: bool = False
    expect_services: list[str] = Field(default_factory=list)
    expect_docker: list[str] = Field(default_factory=list)

class MaintenanceWindow(_Model):
    """The daily window auto-fix is allowed to act in, both "HH:MM" local.
    An OBJECT, not the "HH:MM-HH:MM" string this shape held before Task
    39's controller ruling (see the settings router's
    `MaintenanceWindowIn`); `AutoFixPolicy` coerces a stored legacy string
    so a tenant whose `config` predates the ruling still renders."""
    start: str = ""
    end: str = ""

class AutoFixPolicy(_Model):
    #: These defaults are the SENSOR's, and they have to stay that way.
    #: `sensor/guardian_sensor/fix.py` reads its own config with
    #: `(cfg["auto_fix"] or {}).get("enabled", True)` and
    #: `.get("max_per_day", 30)` -- so a tenant that has never configured a
    #: policy is repairing, up to thirty times a day. Defaulting to
    #: `False`/`0` here made Config tell such an owner "Auto-fix: Off, up to
    #: 0/day" while Vault was busy fixing things for them, which is how
    #: an owner stops believing anything on the page. This model only ever
    #: *reports* the policy -- nothing repairs because of it -- so matching
    #: the sensor costs nothing and makes the reading true.
    #: `tests/test_autofix_defaults.py` pins the two files together.
    enabled: bool = True
    max_per_day: int = 30
    attacker_method: str = "auto"
    maintenance_window: MaintenanceWindow | None = None
    cooldowns: dict[str, int] = Field(default_factory=dict)

    @field_validator("maintenance_window", mode="before")
    @classmethod
    def _legacy_window(cls, v):
        """Accept the pre-ruling `"HH:MM-HH:MM"` string still sitting in an
        older tenant's `config["auto_fix"]`, so reading Settings never 500s
        on data written before the object shape existed."""
        if isinstance(v, str):
            start, _, end = v.partition("-")
            return {"start": start.strip(), "end": end.strip()} if end else None
        return v

class Appearance(_Model):
    theme: str = "light"    # light|dark
    accent: str = ""

class SensorRow(_Model):
    id: str = ""
    name: str = ""
    platform: str = ""
    version: str = ""
    status: str = "pending"
    last_seen: str | None = None
    age_s: int | None = None

class TenantRow(_Model):
    id: str = ""
    slug: str = ""
    name: str = ""
    brand: dict = Field(default_factory=dict)
    ntfy_topic: str = ""
    users: int = 0
    sensors: int = 0
    demo: bool = False

class SuggestedTarget(_Model):
    """One thing Vault found and is not watching.

    `why` is the evidence, in the customer's words -- "port 443 has been
    open here since 3 September". A suggestion with no stated reason is
    indistinguishable from a guess, and this product does not guess.
    """
    name: str = ""
    url: str = ""
    why: str = ""


class SettingsPayload(_Model):
    profile: ProfileSettings = Field(default_factory=ProfileSettings)
    users: list[UserRow] = Field(default_factory=list)
    sites: list[SiteTarget] = Field(default_factory=list)
    hosts: list[HostTarget] = Field(default_factory=list)
    autofix: AutoFixPolicy = Field(default_factory=AutoFixPolicy)
    thresholds: dict[str, float] = Field(default_factory=dict)
    appearance: Appearance = Field(default_factory=Appearance)
    sensors: list[SensorRow] = Field(default_factory=list)
    tenants: list[TenantRow] = Field(default_factory=list)
    #: Items 43/44: things Vault has ALREADY SEEN on this network that
    #: nobody has told it to watch. An empty Targets tab on a network full
    #: of web servers is the product asking the customer to type in what it
    #: already knows. These are offers, never writes: Vault adds nothing
    #: to a config on its own.
    suggested_sites: list[SuggestedTarget] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Integrations
# --------------------------------------------------------------------------

class IntegrationField(_Model):
    name: str = ""
    label: str = ""
    kind: str = "text"      # text|secret|url|select|bool
    value: str = ""         # secrets come back masked, never in clear
    required: bool = False
    options: list[str] = Field(default_factory=list)

class IntegrationCard(_Model):
    kind: str = ""
    title: str = ""
    help: str = ""
    enabled: bool = False
    configured: bool = False
    fields: list[IntegrationField] = Field(default_factory=list)
    routes: dict[str, bool] = Field(default_factory=dict)
    inbound_url: str | None = None
    last_test: str | None = None
    last_error: str = ""
    # One of integrations.base.DIRECTIONS: out | out+in | out+command |
    # out+status sync (the Jira/ServiceNow ticket integrations -- outbound
    # alert relay is a deliberate no-op, but they do sync ticket status
    # back, so they are neither plain "out" nor inbound-token holders).
    direction: str = "out"
    #: What a person can say to this channel once it is connected, in their
    #: own words. Populated ONLY for a connected channel, from the provider
    #: class that implements the handler -- so the page cannot advertise a
    #: command the code does not answer.
    commands: list[str] = Field(default_factory=list)

class AiProviderRung(_Model):
    name: str = ""            # groq|anthropic|rules
    configured: bool = False
    note: str = ""

class AiModelStatus(_Model):
    model: str = ""
    role: str = ""            # chat|moderation|guard|chat-ar
    in_pool: bool = False     # answers users (chat role)
    preferred: str = ""       # "first" | "tools" | ""
    rpm: int = 0
    rpd: int | None = None
    tpm: int = 0
    tpd: int | None = None
    cooling_s: float = 0.0    # > 0 while parked behind a 429

class AiFailover(_Model):
    """How chat answers when a provider is down or rate-limited: the ladder
    it walks, then the Groq model pool with each model's limits and whether
    it is currently parked. Never a key or key fragment."""
    ladder: list[AiProviderRung] = Field(default_factory=list)
    models: list[AiModelStatus] = Field(default_factory=list)

# --------------------------------------------------------------------------
# The owner's portal (admin.guarder.io) -- platform-wide, superadmin only
# --------------------------------------------------------------------------

class PlatformTenantRow(_Model):
    """One customer, as the owner's ledger shows them."""
    id: str = ""
    slug: str = ""
    name: str = ""
    plan: str = ""             # "" when the tenant has no subscription row
    plan_status: str = ""      # trialing|active|past_due|paused|canceled|""
    interval: str = ""         # month|year|none|""
    #: True when this customer's plan is priced per site. The hub does not
    #: know how many they bought -- Stripe's quantity does -- so `mrr_cents`
    #: is their single-site price and this flag says it is a floor.
    per_site: bool = False
    #: This customer's contribution to monthly recurring revenue, in cents.
    #: Zero for a trial, a lapsed account or an unfinished checkout. A yearly
    #: plan is counted at its monthly EQUIVALENT, not its sticker price --
    #: `plans.monthly_cents` carries the arithmetic and the reason.
    mrr_cents: int = 0
    trial_ends: str = ""
    trial_days_left: int | None = None
    #: False once a paused trial has stopped accepting sensor data -- the
    #: single most useful column on the page, because it is the difference
    #: between a quiet customer and a stopped one.
    ingesting: bool = True
    sensors_total: int = 0
    sensors_online: int = 0
    last_scan: str = ""
    devices: int = 0
    users: int = 0
    open_warn: int = 0
    open_crit: int = 0
    created: str = ""

class PlatformTotals(_Model):
    tenants: int = 0
    sensors_online: int = 0
    sensors_total: int = 0
    open_crit: int = 0
    open_warn: int = 0
    trials_ending: int = 0
    open_requests: int = 0
    #: Customer tickets still waiting on an answer. Here rather than read from
    #: the tickets queue separately, because the console's nav badges all come
    #: from this one payload -- two sources for "how much is waiting" would
    #: eventually disagree with each other in the same sidebar.
    open_tickets: int = 0
    paused: int = 0
    #: Monthly recurring revenue across every paying account, in cents.
    mrr_cents: int = 0
    #: Accounts actually being charged -- `active` or `past_due`. A trial is
    #: not a customer for this purpose and neither is a cancelled account,
    #: which is why this is not `tenants` minus the trials.
    paying: int = 0
    #: Charged, and the last attempt failed. The one number on the money page
    #: that is someone's job today.
    past_due: int = 0
    #: True when at least one PAYING customer is on a per-site plan, so the
    #: total is a floor rather than the figure. False means it is exact, and
    #: the page then says nothing -- a permanent disclaimer on an exact number
    #: is how a reader learns to discount a correct one.
    mrr_is_floor: bool = False

class PlatformPayload(_Model):
    """`GET /api/v1/platform`. The only cross-tenant read in the product."""
    totals: PlatformTotals = Field(default_factory=PlatformTotals)
    tenants: list[PlatformTenantRow] = Field(default_factory=list)
    #: How the platform itself is wired, never the credentials: whether mail
    #: can go out, which billing surface is offered, where the app lives.
    mail_configured: bool = False
    billing_mode: str = ""
    #: False while the platform-wide pause is on, i.e. Vault is watching but
    #: repairing nothing without a person. Here so the console can say so in
    #: its frame -- this is a state an owner can leave on by accident for a
    #: week, and the only other thing that would tell them is an incident that
    #: did not fix itself.
    auto_heal: bool = True
    base_url: str = ""


# --------------------------------------------------------------------------
# Support tickets
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# One account, as the owner sees it
# --------------------------------------------------------------------------

class AccountNetwork(_Model):
    id: str = ""
    name: str = ""
    slug: str = ""
    devices: int = 0


class AccountSensor(_Model):
    """No `token_hash` and no `enrol_code` by construction. An unexpired enrol
    code is a bearer credential that adds a sensor to this tenant, so it is not
    a field this model has."""
    id: str = ""
    name: str = ""
    platform: str = ""
    version: str = ""
    status: str = ""
    last_seen: str = ""


class AccountUser(_Model):
    username: str = ""
    email: str = ""
    display_name: str = ""
    role: str = ""
    superadmin: bool = False


class AccountIntegration(_Model):
    """That a channel is wired, never what it authenticates with. `secret` is
    deliberately absent rather than blanked: a field that is sometimes empty
    invites someone to fill it in."""
    kind: str = ""
    configured: bool = True


class AccountAuditRow(_Model):
    ts: str = ""
    user: str = ""
    role: str = ""
    action: str = ""
    target: str = ""


class AccountBilling(_Model):
    """What this one customer is worth, and what is at risk with them.

    The owner opens this page sitting WITH the customer, or just before
    phoning them, so the money belongs beside the configuration rather than
    one screen away on the Billing roster. Same fields the roster computes, so
    the two can never disagree about one account.

    `stripe_customer_id` is an opaque identifier, not a credential -- it
    authenticates nothing without the secret key, and it is what lets the page
    link straight to this customer in Stripe, which is where invoices and
    refunds actually live. It crosses this boundary deliberately; the tokens
    listed in this module's docstring do not.
    """
    plan: str = ""
    plan_label: str = ""
    interval: str = ""
    status: str = ""
    #: Monthly recurring revenue in cents, on the same terms as the roster: a
    #: yearly plan at its monthly equivalent, a per-site plan at one site.
    mrr_cents: int = 0
    per_site: bool = False
    trial_days_left: int | None = None
    #: When the current paid period ends -- the renewal date, or the last day
    #: of service if `cancel_at_period_end`.
    period_end: str = ""
    cancel_at_period_end: bool = False
    stripe_customer_id: str = ""
    #: Where this customer is in the Stripe dashboard, mode-aware; "" without a
    #: customer. Charged plans change THERE -- the console refuses to edit a
    #: subscription Stripe is billing, and points here instead.
    stripe_dashboard_url: str = ""


class AccountPayload(_Model):
    """`GET /api/v1/platform/tenants/{tid}` -- one customer's own setup."""
    id: str = ""
    slug: str = ""
    name: str = ""
    created: str = ""
    networks: list[AccountNetwork] = Field(default_factory=list)
    #: Devices on no network. Invisible on the map, so worth a number.
    devices_unassigned: int = 0
    sensors: list[AccountSensor] = Field(default_factory=list)
    users: list[AccountUser] = Field(default_factory=list)
    integrations: list[AccountIntegration] = Field(default_factory=list)
    recent: list[AccountAuditRow] = Field(default_factory=list)
    #: None when the tenant has no subscription row at all -- an account the
    #: owner created by hand before billing existed. That is a real state and
    #: is shown as such, rather than as a zero.
    billing: AccountBilling | None = None


class PlatformTrend(_Model):
    """One customer's alert history, as a sparkline for the Ledger."""
    tenant_id: str = ""
    #: One value per day, OLDEST FIRST, always `days` long. Days with no alerts
    #: are a real zero rather than a gap: an alert table with nothing in it for
    #: a Tuesday means a quiet Tuesday, and dropping the point would draw a
    #: line straight from Monday to Wednesday and hide it.
    points: list[float] = Field(default_factory=list)
    #: New alerts across the whole window. The sparkline has no axis, so
    #: without this the shape is unlabelled -- a flat line at 40 a day and a
    #: flat line at nothing look identical.
    total: int = 0


class PlatformTrendsPayload(_Model):
    """`GET /api/v1/platform/trends?days=<n>` -- one sparkline per customer.

    Every series covers the SAME days, so the column can be read down. Two
    sparklines over different windows sitting in one column invite a
    comparison that is not being made.
    """
    days: int = 0
    #: A customer with no alerts in the window is absent rather than carried as
    #: a row of zeroes: the Ledger draws nothing for them, which is the honest
    #: rendering of "no alerts" and is not the same picture as a flat line.
    trends: list[PlatformTrend] = Field(default_factory=list)


class AccountTrend(_Model):
    """One chart on the owner's per-account page, with what it says in words.

    `chart` is a `HistoryChart` unchanged -- the same object the customer's own
    History tab is drawn from -- so the line the owner points at is the line
    the customer sees when they log in.

    `says` and `direction` are the part a chart cannot do on its own. This page
    gets opened WITH a customer, and "here is a line, you work it out" is not
    an answer; the sentence states the comparison the shape is supposed to
    show. `direction` is `better`, `worse`, `steady`, or empty when the series
    is too short to compare -- never a guess.
    """
    chart: HistoryChart = Field(default_factory=HistoryChart)
    says: str = ""
    direction: str = ""


class AccountTrendsPayload(_Model):
    """`GET /api/v1/platform/tenants/{tid}/trends?days=<n>` -- how a customer
    has actually been doing, for the owner to show them.

    Separate from `AccountPayload` because it is a different question on a
    different clock: the setup list is small and immediate, and a month of
    decimated series is neither. The account page renders without waiting for
    this.
    """
    days: int = 0
    trends: list[AccountTrend] = Field(default_factory=list)
    #: One sentence for the top of the section, or empty when there is nothing
    #: honest to say yet. Never filler -- a customer reading "all good" off a
    #: tenant with no data would be reading a bug.
    headline: str = ""


class PlatformAuditRow(_Model):
    ts: str = ""
    user: str = ""
    role: str = ""
    action: str = ""
    target: str = ""
    tenant_id: str = ""
    tenant: str = ""
    #: True when the act was on the platform rather than on a customer.
    platform: bool = False


class PlatformAuditPayload(_Model):
    """`GET /api/v1/platform/audit` -- the trail across every tenant."""
    rows: list[PlatformAuditRow] = Field(default_factory=list)


class PlatformWorkDay(_Model):
    """One day of settled work, split by who settled it."""
    #: `YYYY-MM-DD`, oldest first, every day present. A day with nothing on it
    #: is a real zero rather than a gap: dropping it would draw the bar chart
    #: straight from Monday to Wednesday and hide a quiet Tuesday.
    day: str = ""
    #: Closed by Vault on its own.
    auto: int = 0
    #: Closed by a person -- pressing Fix, approving a proposal, or marking an
    #: alert resolved.
    human: int = 0


class PlatformWorkPayload(_Model):
    """`GET /api/v1/platform/work?days=<n>` -- who has been closing the work.

    The owner console's counterpart to the design reference's "Self-healing
    activity" chart, named for what this platform can actually measure. The
    reference splits a bar into "fixed automatically" and "fixed after
    approval"; Vault's audit records who ACTED, so the split here is
    Vault against a person, which is the same question asked of the data we
    have rather than of the data the mock had.
    """
    days: list[PlatformWorkDay] = Field(default_factory=list)
    auto_total: int = 0
    human_total: int = 0


class PlatformSettingField(_Model):
    """One editable platform setting, described well enough that the console
    needs no copy of its own."""
    key: str = ""
    label: str = ""
    help: str = ""
    kind: str = "text"            # int|text|choice|bool
    group: str = ""
    value: str = ""
    #: True when a stored row overrides the environment/code default, so the
    #: owner can see at a glance what has been changed away from stock.
    overridden: bool = False
    default: str = ""
    minimum: int | None = None
    maximum: int | None = None
    choices: list[str] = Field(default_factory=list)
    updated: str = ""
    updated_by: str = ""

class PlatformSecretRow(_Model):
    """A credential, reported and never returned. `set` is the whole payload:
    the value itself does not leave the process."""
    key: str = ""
    label: str = ""
    set: bool = False

class PlatformSettingsPayload(_Model):
    """`GET /api/v1/platform/settings`."""
    fields: list[PlatformSettingField] = Field(default_factory=list)
    secrets: list[PlatformSecretRow] = Field(default_factory=list)
    base_url: str = ""
    version: str = ""


class TicketMessageRow(_Model):
    """One message in a thread. `side` is customer|owner -- never a role name:
    both parties are an admin of something, so a role would not tell the
    reader who is who."""
    id: str = ""
    side: str = "customer"
    author: str = ""
    body: str = ""
    created: str = ""
    #: Whether a notification for this message went out. Shown to the OWNER
    #: only, so a reply that reached nobody (a customer with no address on
    #: file) is visible rather than assumed delivered.
    emailed: bool = False

class TicketRow(_Model):
    """One ticket as a queue row."""
    id: str = ""
    subject: str = ""
    status: str = "open"          # open|answered|resolved
    priority: str = "normal"      # low|normal|high
    opened_by: str = ""
    created: str = ""
    last_message_at: str = ""
    last_author: str = "customer"
    messages: int = 0
    resolved_at: str = ""
    resolved_by: str = ""
    #: Owner queue only: which customer this is. Blank on a tenant's own list,
    #: where every row is theirs.
    tenant_id: str = ""
    tenant_name: str = ""
    #: missed|finding|device|network|account|other -- see tickets.CATEGORIES.
    category: str = "other"
    #: What this ticket is about, if the customer raised it from a thing.
    #: All three are blank together; see tickets.clean_context.
    context_kind: str = ""
    context_id: str = ""
    context_label: str = ""

class TicketDetail(_Model):
    ticket: TicketRow = Field(default_factory=TicketRow)
    messages: list[TicketMessageRow] = Field(default_factory=list)

class TicketsPayload(_Model):
    #: Item 47: False when no SMTP host is configured, in which case
    #: `mailer.send` logs a warning and delivers nothing -- the support
    #: addresses on the page are then decoration, and the page says so.
    mail_works: bool = False
    tickets: list[TicketRow] = Field(default_factory=list)
    open_count: int = 0
    #: Where a customer's ticket is emailed, shown so the reader knows the
    #: thread is not the only place it lands.
    support_email: str = ""
    #: Both empty unless an operator has set them. An unmet reply time is
    #: worse than promising nothing, so the UI renders neither block when
    #: these are "". There is no phone field by design.
    support_direct_email: str = ""
    support_reply_sla: str = ""


class IntegrationsPayload(_Model):
    integrations: list[IntegrationCard] = Field(default_factory=list)
    ai: AiFailover | None = None


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------

#: The longest message one chat turn accepts. A person asking Vault
#: something types a sentence or a paragraph; 2,000 characters is several
#: paragraphs. Without a cap one request could ship a novel to the provider
#: on the tenant's token budget, or smuggle a wall of text that reads like
#: instructions into the thread history every later turn re-sends.
CHAT_MESSAGE_MAX = 2000


class ChatRequest(_Model):
    message: str = Field(default="", max_length=CHAT_MESSAGE_MAX)
    thread: str | None = None
    tab: str = ""
    confirm_token: str | None = None

class ToolStep(_Model):
    tool: str = ""
    args: dict = Field(default_factory=dict)
    tier: int = 1
    # The whole enum: ok | error | queued | denied | proposed. `routers/chat.py`
    # maps the Queue vocabulary onto it (done -> ok, failed -> error, running
    # -> queued), so a raw queue status never reaches the web.
    status: str = ""        # ok|error|queued|denied|proposed
    result: dict | None = None
    queue_id: str | None = None

# Structured entity a chat bullet's leading name/IP resolves to, so the web
# can turn "Router · 192.168.1.1 · online" into a link -- `kind` names which
# collection `id` indexes into ("device" ids are the device's mac), or both
# null when the bullet's subject does not match anything Vault knows.
# NOT mirrored in web/src/types.ts by this task (see task-44-report.md) --
# a web fix round mirrors it; check_types.py's PY_ONLY_ALIASES carries the
# gap in the meantime.
class ChatEntity(_Model):
    kind: str | None = None    # device|host|site|None
    id: str | None = None

class ChatBullet(_Model):
    text: str = ""              # "Name · IP or host · the one fact that matters"
    entity: ChatEntity = Field(default_factory=ChatEntity)

class ChatTurn(_Model):
    role: str = ""          # user|guardian
    text: str = ""
    tool_steps: list[ToolStep] = Field(default_factory=list)

class ProposedAction(_Model):
    tool: str = ""
    args: dict = Field(default_factory=dict)
    tier: int = 2
    effect: str = ""
    reversible: bool = True
    host: str = ""

class ChatProposal(_Model):
    token: str = ""
    summary: str = ""
    actions: list[ProposedAction] = Field(default_factory=list)
    expires: str = ""

class AlertHandoff(_Model):
    """`POST /api/v1/alerts/{key}/handoff` -- the chat hand-off for one
    alert: Vault's opening message (already naming what is wrong, what it
    can and cannot do, and what it needs), plus the real tier-3 proposal when
    the remedy is one it may run once the owner says yes."""
    key: str = ""
    remedy: AlertRemedy = Field(default_factory=AlertRemedy)
    opening: str = ""
    proposal: ChatProposal | None = None
    # The thread the opening was recorded on, so the owner's reply continues
    # the same conversation the hub already has the context for.
    thread_id: str = ""

class ChatResponse(_Model):
    text: str = ""
    verdict: str = ""
    bullets: list[ChatBullet] = Field(default_factory=list)
    next: list[NextAction] = Field(default_factory=list)
    tool_steps: list[ToolStep] = Field(default_factory=list)
    proposal: ChatProposal | None = None
    provider: str = ""
    thread_id: str = ""


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

class AuditRow(_Model):
    ts: str = ""
    user: str = ""
    role: str = ""
    action: str = ""
    target: str = ""
    ip: str = ""
    body: dict = Field(default_factory=dict)

class AuditPayload(_Model):
    rows: list[AuditRow] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Search (the CMD-K palette)
# --------------------------------------------------------------------------

SearchKind = Literal["device", "host", "site", "alert", "route"]

class SearchResult(_Model):
    kind: SearchKind = "route"
    id: str = ""
    title: str = ""
    subtitle: str = ""
    href: str = ""

class SearchPayload(_Model):
    results: list[SearchResult] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Billing (task 68)
#
# One read payload, rendered by web/src/pages/Settings/Billing.tsx. Every
# number on it is derived from `guardian_hub.plans` inside the payload builder
# in `routers/billing.py` -- the web app renders what arrives and has no price
# of its own, so there is exactly one place a Vault price is written down.
# --------------------------------------------------------------------------

class BillingPlanCard(_Model):
    """One plan as the in-app picker shows it, both intervals at once.

    Cents AND pre-formatted labels: the labels so the UI never reimplements
    currency formatting, the raw cents so a test (and a future proration
    display) can assert against the catalogue without parsing a string.
    """
    id: str = ""
    name: str = ""
    blurb: str = ""
    monthly_cents: int = 0
    yearly_cents: int = 0
    monthly_label: str = ""
    yearly_label: str = ""
    per_site: bool = False
    is_trial: bool = False
    current: bool = False

class BillingSubscription(_Model):
    """What this tenant currently holds. `null` on the payload when the
    tenant has no subscription row at all -- which is every tenant the owner
    set up by hand before billing existed, and is not an error state."""
    plan: str = ""
    plan_name: str = ""
    interval: str = "none"          # month | year | none
    status: str = ""                # trialing|active|past_due|paused|canceled
    price_label: str = ""
    trial_end: str = ""             # ISO-8601, "" when not on trial
    trial_days_left: int | None = None
    current_period_end: str = ""    # ISO-8601, "" when there is no renewal
    cancel_at_period_end: bool = False
    #: Whether sensors may still store new data. False only for a paused or
    #: cancelled account -- a failed payment never sets this false.
    ingesting: bool = True

class BillingPayload(_Model):
    """`GET /api/v1/billing`.

    `configured` is False on a hub with no Stripe secret key: the page then
    shows the plans and the current subscription (both of which are entirely
    local) and disables the upgrade/portal buttons, rather than offering a
    checkout that cannot complete. The free trial works either way.
    """
    configured: bool = False
    #: True while this hub offers only the free trial: one card is sent and
    #: checkout/portal refuse. The trial's own length and expiry are
    #: unaffected -- see `config.billing_mode`.
    trial_only: bool = False
    currency: str = "usd"
    trial_days: int = 0
    yearly_months_charged: int = 0
    plans: list[BillingPlanCard] = Field(default_factory=list)
    subscription: BillingSubscription | None = None
    notice: str = ""
    #: True when a Stripe portal session can actually be opened -- needs both
    #: a configured hub and a customer id on the subscription.
    portal_available: bool = False


# --------------------------------------------------------------------------
# First-run wizard (task 71)
# --------------------------------------------------------------------------

class OnboardingNetwork(_Model):
    """One declared network, and how far along it is.

    `sensor` is four states rather than a boolean because the advice differs
    for every one of them, and collapsing any pair gives a customer the wrong
    instruction:

      * `none`    -- nothing bound. Install one.
      * `pending` -- a code was minted and never redeemed. They may already
                     have the e-mail; re-sending beats re-explaining.
      * `active`  -- bound and talking inside `SENSOR_STALE_S`.
      * `quiet`   -- bound, talked once, stopped. This is `remedy`'s `human`
                     tier: no fix Vault runs reaches a host that is not
                     answering, and a wizard that read this as `active` would
                     wait forever for a scan that is not coming.
    """
    id: str = ""
    name: str = ""
    slug: str = ""
    sensor: str = "none"            # none | pending | active | quiet
    #: The earliest scan attributable to this network, "" before the first one.
    first_scan_at: str = ""
    devices: int = 0
    #: Group name -> count, from `payloads.assets.KIND_GROUPS`. Empty until the
    #: first scan lands.
    by_group: dict[str, int] = Field(default_factory=dict)
    dns: str = "off"                # off | working


class OnboardingNext(_Model):
    """The single next thing to do, or absent when there is nothing left.

    One action rather than a list of outstanding work: the wizard shows one
    screen at a time, and letting the hub choose keeps "what comes next" from
    being re-derived in the browser, where it can disagree with the rows.
    """
    network_id: str = ""
    action: str = ""   # declare_network | enrol_sensor | await_first_scan | offer_dns


class OnboardingState(_Model):
    """`GET/POST /api/v1/onboarding` -- whether THIS user has been through the
    first-run wizard on THIS tenant, and which walkthrough they should get.

    The wizard used to be gated on the tenant (no scan, no sensor), so the
    second person to join an established site never saw it. This row is the
    per-user authority instead, and `path` is the part the tenant still
    decides:

    - `"setup"` -- nothing has ever reported here (no scan and no sensor), so
      the walkthrough installs: name the site, declare the networks, run the
      enrol command, wait for the first scan, label what it found.
    - `"tour"` -- the site is already reporting, so the walkthrough orients:
      what Vault watches, where the answers live, how to get help. It
      never asks a newcomer to install anything.

    `show` is the hub's own answer to "should the browser redirect to the
    wizard?" -- false once this user has completed or skipped it -- so the
    rule lives in one place rather than being re-derived in the shell.
    """
    path: str = "setup"          # setup | tour
    #: Retained for the `tour` path, which is genuinely linear, and as a resume
    #: hint. It is NO LONGER authoritative for `setup`: see `networks`/`next`,
    #: which are derived, because a stored cursor cannot describe several
    #: networks at different stages and can contradict the rows outright.
    step: str = ""               # the resumable step name, "" before the first save
    completed: bool = False
    skipped: bool = False
    show: bool = False
    #: True when this tenant has a sensor or a scan on record. Drives `path`,
    #: and is worth sending on its own so the wizard can word a step honestly
    #: without a second payload.
    tenant_ready: bool = False
    site_named: bool = False
    #: Every declared network and how far along it is. Derived per request.
    networks: list[OnboardingNetwork] = Field(default_factory=list)
    next: OnboardingNext | None = None
