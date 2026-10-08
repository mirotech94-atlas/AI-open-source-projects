"""Response models for the public REST API on api.guarder.io.

A separate module from `schemas_dash.py` on purpose, and this is a build
constraint rather than tidiness: `scripts/check_types.py` requires every
`schemas_dash` model **served by the API** to have a matching `export type` in
`web/src/types.ts`, and these models have no business in the web app's contract
file -- nothing in the dashboard consumes them. `routers/leads.py` sets the
same precedent for its own schemas.

Where the public API answers with a payload the dashboard already has
(`OverviewPayload`, `SecurityPayload`, `TopologyPayload`, `DnsPayload`,
`SitesPayload`, `HostsPayload`, `NetworksPayload`), the `schemas_dash` model IS
reused -- reusing an already-mirrored model is safe, and a second copy of those
shapes would be a drift hazard for no gain.

Every docstring and `description=` in this file is **customer-facing copy**: it
is what the generated reference at api.guarder.io/v1/docs and the in-app API
guide print. No internal task numbers, no file paths, no "Nexus".
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from . import schemas_dash as sd


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


T = TypeVar("T")


class Page(_Model, Generic[T]):
    """A page of results. `total` counts everything that matched, not just
    this page, so a client can size a progress bar before it starts."""

    items: list[T] = Field(default_factory=list)
    total: int = Field(0, description="Total matching records, ignoring limit and offset.")
    limit: int = Field(50, description="Page size actually applied, after clamping.")
    offset: int = Field(0, description="Index of the first record in this page.")


#: Reused from the dashboard's contracts rather than redefined: the shapes are
#: identical, and two pydantic models with one name make FastAPI emit
#: module-qualified component names ("guardian_hub__schemas_api__NetworkRef")
#: into a document customers read.
NetworkRef = sd.NetworkRef
DnsRules = sd.DnsRules


class Health(_Model):
    """Liveness. The only route that needs no token."""

    ok: bool = True
    product: str = "Vault"
    api: str = Field("v1", description="API version this host serves.")


class WhoAmI(_Model):
    """What the presented token is, and what it may do. Useful as a
    first call when wiring up a client, and as a self-check afterwards."""

    account: str = Field("", description="Name of the account this token reads.")
    account_slug: str = ""
    token_name: str = Field("", description="Name the token was given when it was created.")
    token_prefix: str = Field(
        "", description="Public, non-secret head of the token — the same string that appears in "
                        "logs, so a log line can be matched back to a token without the secret."
    )
    scope: str = Field("read", description="'read' allows GET only; 'write' also allows the write routes.")
    role: str = Field("viewer", description="'viewer', 'operator' or 'admin'.")
    expires: str | None = Field(None, description="When this token stops working, or null if it does not expire.")


class Device(_Model):
    """One device seen on a monitored network."""

    network: NetworkRef | None = None
    mac: str = ""
    ip: str = ""
    name: str = ""
    vendor: str = ""
    kind: str = Field("", description="What kind of thing this looks like — phone, laptop, camera, printer, and so on.")
    label: str = Field("", description="Name you gave it, if any.")
    trusted: bool = False
    always_on: bool = False
    risk: int = 0
    presence: str = ""
    ports: dict[str, str] = Field(
        default_factory=dict,
        description="Open ports, each mapped to when it was last seen open.",
    )
    first_seen: str = ""
    last_seen: str = ""


class Alert(_Model):
    """One open or closed alert.

    There is deliberately no `evidence` field. The underlying column is a
    free-form JSON blob passed through from whatever the sensor sent, so nothing
    can state what it will contain after the next sensor release — and a public
    API must not publish a shape it cannot describe. The part of it worth reading
    is already folded into `why`.
    """

    key: str = Field("", description="Stable identifier — use it on the write routes.")
    network: NetworkRef | None = None
    sev: str = Field("INFO", description="CRIT, WARN or INFO.")
    msg: str = ""
    why: str = Field("", description="Why Vault raised it, in plain language.")
    confidence: str = ""
    first_seen: str = ""
    last_seen: str = ""
    count: int = 1
    status: str = Field("open", description="open, acked, snoozed or resolved.")
    resolved_by: str = ""
    snoozed_until: str | None = None
    fix: str = Field("", description="The repair Vault would apply, if it knows one.")


class Incident(_Model):
    """Several related alerts, told as one story."""

    iid: str = ""
    entity: str = ""
    sev: str = "INFO"
    story: str = ""
    alert_keys: list[str] = Field(default_factory=list)
    opened: str = ""
    closed: str | None = None


class Recommendation(_Model):
    """Something Vault suggests you change."""

    key: str = ""
    priority: str = ""
    title: str = ""
    why: str = ""
    decision: str = Field("", description="Empty, 'yes' or 'no'.")
    decided_by: str = ""
    status: str = ""
    last_seen: str = ""


class Event(_Model):
    """One thing that happened: a device joining or leaving, a port opening,
    a scan finishing."""

    ts: str = ""
    type: str = ""
    entity: str = ""
    network: NetworkRef | None = None
    payload: dict = Field(default_factory=dict)


class DnsQuery(_Model):
    """One DNS lookup made on a monitored network."""

    ts: str = ""
    client: str = Field("", description="Address the lookup came from.")
    mac: str | None = None
    name: str = Field("", description="Domain asked for.")
    qtype: str = ""
    blocked: bool = False
    list: str | None = Field(None, description="Which list blocked it, when it was blocked.")
    ms: int = 0
    network: NetworkRef | None = None


class Report(_Model):
    """One generated report. Fetch the rendered HTML from
    `/v1/reports/{id}`."""

    id: str = ""
    kind: str = Field("", description="weekly, monthly or manual.")
    title: str = ""
    period_days: int = 0
    preset: str = ""
    sections: list[str] = Field(default_factory=list)
    created: str = ""
    score: int | None = None


class Queued(_Model):
    """Work handed to the sensor. Poll `/v1/jobs/{id}` for the outcome."""

    id: str = ""
    status: str = Field("queued", description="queued, running, done or failed.")
    existing: bool = Field(
        False,
        description="True when this request matched work already queued and no "
                    "second job was created; `id` is the job already in flight.",
    )


class Job(_Model):
    """State of one piece of queued work."""

    id: str = ""
    status: str = "queued"
    queued_at: str | None = None
    finished_at: str | None = None
    ok: bool | None = Field(None, description="Null until the work finishes.")


# --------------------------------------------------------------------------
# The in-app API guide
# --------------------------------------------------------------------------
# Served by the app (session-authenticated), not by the API host, and built
# from the very same generated document the reference at api.guarder.io renders
# — so the guide in the app and the reference on the API host cannot describe
# different APIs.

class GuideEndpoint(_Model):
    method: str = ""
    path: str = ""
    summary: str = ""
    description: str = ""
    scope: str = Field("read", description="Lowest token scope that may call it.")
    role: str = Field("viewer", description="Lowest token role that may call it.")


class GuideGroup(_Model):
    name: str = ""
    blurb: str = ""
    endpoints: list[GuideEndpoint] = Field(default_factory=list)


class GuideExclusion(_Model):
    """Something the API deliberately cannot do, and why. Printed in the guide
    rather than left as a silent gap."""

    action: str = ""
    why: str = ""


class GuideExample(_Model):
    title: str = ""
    curl: str = ""


class ApiGuide(_Model):
    """Everything the app's API guide section renders."""

    base_url: str = Field("", description="Where the API answers.")
    version: str = "v1"
    generated: str = Field("", description="When this document was built.")
    openapi_url: str = ""
    reference_url: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    token_prefix: str = Field("", description="Every token starts with this.")
    rate_limit: str = Field("", description="The ceiling, in words.")
    scopes: list[GuideExclusion] = Field(
        default_factory=list,
        description="Each scope and what it allows — same {action, why} shape as the exclusions.",
    )
    groups: list[GuideGroup] = Field(default_factory=list)
    excluded: list[GuideExclusion] = Field(default_factory=list)
    examples: list[GuideExample] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Token management (the app's own admin surface, not the public API)
# --------------------------------------------------------------------------

class ApiTokenRow(_Model):
    """One token as the app's token list shows it. Never carries the secret."""

    id: str = ""
    name: str = ""
    prefix: str = ""
    scope: str = "read"
    role: str = "viewer"
    created_by: str = ""
    created: str | None = None
    expires: str | None = None
    last_used: str | None = None
    last_used_ip: str = ""
    revoked: bool = False


class ApiTokensOut(_Model):
    tokens: list[ApiTokenRow] = Field(default_factory=list)
    #: The highest role the signed-in admin may hand to a new token — the UI
    #: uses it to hide choices the hub would clamp anyway.
    max_role: str = "viewer"
    base_url: str = ""


class ApiTokenCreated(_Model):
    """The one and only response that carries a raw secret."""

    token: ApiTokenRow
    secret: str = Field(
        "", description="The token itself. Shown once — Vault keeps only a hash of it, "
                        "so it cannot be shown again."
    )


class MetricSample(_Model):
    """One raw measurement, with the state the dashboard's own arithmetic
    assigns it.

    The point of publishing these is falsifiability: a client who does not
    take Vault's uptime figure on faith can fetch the samples, apply the
    rule from the metric definition, and land on the same number — or find
    that they do not, which is worth knowing. That only works if `state`
    here is the same classification the dashboard used, which is why
    `planned` is a state and not a filter the caller has to reconstruct.
    """

    ts: str = ""
    metric: str = Field("", description="Which series this is: up, latency_ms, disk_pct, score…")
    monitor: str = Field("", description="Site or host name. Empty for account-wide series.")
    value: float | None = None
    state: str = Field(
        "",
        description="For the `up` series: up, down, planned (inside a maintenance window and "
                    "excluded from availability), or unknown (recorded with no value). Empty "
                    "for series where up/down does not apply.",
    )


class FixRecord(_Model):
    """One repair Vault ran, as the fix log records it.

    Separate from the dashboard's `FixLogRow` because this one carries the
    fields an auditor asks for and the dashboard has no room to draw:
    `arg` (what the action was pointed at), `verified_at` (when a later
    observation confirmed the condition had actually cleared, null when
    nothing has confirmed it yet), and the command's full first line.
    """

    ts: str = ""
    host: str = ""
    action: str = ""
    label: str = ""
    arg: str = ""
    by: str = Field("auto", description="`auto` for an unattended run, otherwise who asked.")
    ok: bool = False
    verified_at: str | None = Field(
        None,
        description="When a later observation confirmed the condition had cleared. Null means "
                    "the command reported success but nothing has independently confirmed it — "
                    "those are different claims and are not merged here.",
    )
    reboot_needed: bool = False
    detail: str = ""


class EvidenceBundle(_Model):
    """Everything Vault knows about one finding, in a single document.

    The trust plan's §2.5O, and the reason it argues for building this above
    everything else in that section: a score or an alert asks to be
    believed, while a bundle can be checked. It is what an MSP hands a
    client who asks "what actually happened?", what survives "prove it", and
    what an auditor or insurer wants — and at this price point nobody ships
    one.

    Deliberately assembled from records rather than prose. Every field is
    something Vault stored at the time; nothing here is generated to
    explain the finding after the fact, because a narrative written at
    export time is exactly what the bundle exists to make unnecessary.
    """

    exported_at: str = ""
    alert: dict = Field(
        default_factory=dict,
        description="The finding as recorded: key, severity, message, why, the sensor's own "
                    "evidence blob, confidence, first and last seen, how many times it has "
                    "fired, and its current status.",
    )
    observed_by: dict = Field(
        default_factory=dict,
        description="Which sensor saw it and what it was running — name, platform, version, "
                    "and when the hub last heard from it. A finding is only as good as the "
                    "thing that made it, and a check version is how a false positive gets "
                    "traced back to a release.",
    )
    scan: dict = Field(
        default_factory=dict,
        description="The sweep this was last seen in: the sensor's own timestamp and the "
                    "hub's, kept separate so clock skew between them stays visible rather "
                    "than being silently reconciled.",
    )
    scoring: dict = Field(
        default_factory=dict,
        description="The rubric version in force and how much of the estate that sweep could "
                    "measure, so a score quoted alongside this finding can be read in the "
                    "terms it was computed under.",
    )
    remedy: dict = Field(
        default_factory=dict,
        description="What Vault can do about it: the tier (auto, approval or human), what "
                    "it cannot do itself, and what it needs from a person.",
    )
    repairs: list[FixRecord] = Field(
        default_factory=list,
        description="Every repair attempted against this finding's target, with whether the "
                    "command succeeded and whether a later observation confirmed the "
                    "condition had actually cleared. The two are reported separately.",
    )
