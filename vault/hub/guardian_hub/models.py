import uuid, datetime as dt
from sqlalchemy import String, Integer, Float, Boolean, Text, JSON, ForeignKey, Index, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base, TZDateTime

def uid() -> str: return uuid.uuid4().hex
def utcnow() -> dt.datetime: return dt.datetime.now(dt.timezone.utc)

class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    brand: Mapped[dict] = mapped_column(JSON, default=dict)          # {name, accent, domain}; falls through to the partner's -- see branding.resolve
    # The MSP this customer belongs to, or NULL for a direct customer.
    # SET NULL rather than CASCADE: deleting a partner must orphan its
    # customers back to direct, never take their data with it.
    partner_id: Mapped[str | None] = mapped_column(ForeignKey("partner.id", ondelete="SET NULL"), nullable=True, index=True)
    ntfy_topic: Mapped[str] = mapped_column(String(120), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)         # Nexus-shaped config.json (sites, ssh_hosts, lan, thresholds, auto_fix, checks, heartbeats, dns, home)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)       # retention, report_schedule, profile{owner,email,address}
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)

class Partner(Base):
    """An MSP that resells Vault to its own customers.

    A partner is a level ABOVE the tenant, not a kind of tenant: its
    customers stay ordinary `Tenant` rows, each with its own sensors,
    devices and reports, and nothing tenant-scoped learns about the
    partner at all. The partner exists for two things only -- a brand its
    customers inherit when they have not set their own (`branding.resolve`),
    and a membership that reaches every customer at once
    (`PartnerMembership`, expanded by `deps.memberships`).

    That second point is the isolation story. Every read in this hub is
    already tenant-scoped through `require_role`, which walks the user's
    memberships; a partner user is simply a user whose memberships are
    computed instead of stored. No query anywhere filters on `partner_id`,
    so there is no query that can forget to. The cost is that a partner's
    reach is exactly the union of its customers' tenants, never a
    cross-tenant view -- the owner console's fleet page stays superadmin-
    only, and a partner sees its customers one at a time through the same
    tenant switcher any multi-tenant user has. On Postgres a deployment can
    add row-level security keyed on `tenant.partner_id` underneath this
    without changing the application; on SQLite, the deployed engine, this
    membership expansion IS the boundary.
    """
    __tablename__ = "partner"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    brand: Mapped[dict] = mapped_column(JSON, default=dict)          # same keys as Tenant.brand: {name, accent, domain}
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)


class PartnerMembership(Base):
    """A user's role on every tenant of a partner, present and future.

    Stored once, applied to N tenants: a customer added to the partner
    tomorrow is reachable by the partner's staff without anyone editing
    memberships. A direct `Membership` on one of those tenants still wins
    when it grants MORE (`deps.memberships` keeps the higher role), so a
    partner viewer can be promoted to admin on a single customer without
    touching the partner row.
    """
    __tablename__ = "partner_membership"
    __table_args__ = (UniqueConstraint("user_id", "partner_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    partner_id: Mapped[str] = mapped_column(ForeignKey("partner.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # viewer|operator|admin, applied to every tenant of the partner


class User(Base):
    __tablename__ = "user"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)

    preferences: Mapped[dict] = mapped_column(JSON, default=dict)

class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # viewer|operator|admin

class Otp(Base):
    __tablename__ = "otp"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(128))
    expires: Mapped[dt.datetime] = mapped_column(TZDateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[bool] = mapped_column(Boolean, default=False)

class Session(Base):
    __tablename__ = "session"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)   # opaque cookie value: 32 random bytes hex
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device_label: Mapped[str] = mapped_column(String(200), default="")
    expires: Mapped[dt.datetime] = mapped_column(TZDateTime)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    last_seen: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    # Minted with a tenant's public demo code: read-only above viewer.
    via_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

class Sensor(Base):
    __tablename__ = "sensor"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="sensor")
    token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    enrol_code: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True)
    enrol_expires: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    platform: Mapped[str] = mapped_column(String(32), default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    last_seen: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|revoked
    # Round 2 of Task 51: the network an enrol CODE was minted for, so the
    # sensor that redeems it can be bound onto that network on enrol. Nullable
    # -- most enrol codes (and every sensor that predates this) name none, and
    # then bind nothing (the network is picked from the admin UI afterwards,
    # same as before this existed).
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Task 70: the sensor HOST's own one-minute load average and core count,
    # from `last["self"]`. Kept here rather than read back out of `Scan.raw`
    # because `GET /live` is polled and already loads these rows for the
    # freshness figure. NULL = unknown (a sensor that predates the reporting
    # change, or a platform with no load average) and the sub-header falls
    # back to the freshness pill -- never to a zero, which reads as idle.
    load1: Mapped[float | None] = mapped_column(Float, nullable=True)
    cores: Mapped[int | None] = mapped_column(Integer, nullable=True)

class Site(Base):
    __tablename__ = "site"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(500))
    public: Mapped[bool] = mapped_column(Boolean, default=False)
    last: Mapped[dict] = mapped_column(JSON, default=dict)   # latest check_site result
    # Scan.ts of the sweep that last reported this site. NULL until the next
    # scan after 0016. See payloads/staleness.py.
    last_seen: Mapped[str | None] = mapped_column(String(32), nullable=True)

class Host(Base):
    __tablename__ = "host"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(200))
    last: Mapped[dict] = mapped_column(JSON, default=dict)   # latest check_ssh result (security facts, ports, cves…)
    # Scan.ts of the sweep that last reported this host. See payloads/staleness.py.
    last_seen: Mapped[str | None] = mapped_column(String(32), nullable=True)

class Network(Base):
    """One watched network per row (Task 51 / spec S3.1): a tenant can have
    its home Wi-Fi and a lab private network side by side, each with its own
    sensor, baseline, presence and DNS attribution.

    `slug` is the stable handle every API filter accepts (`?network=<id|slug>`)
    and the segment LAN alert keys are namespaced with
    (`lan:<slug>:new:<mac>`). Exactly one network per tenant carries
    `is_primary=True`: it is the attribution fallback for any row that names
    no network, and it cannot be deleted.
    """
    __tablename__ = "network"
    __table_args__ = (UniqueConstraint("tenant_id", "slug"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))                      # "Office Wi-Fi", "Lab"
    slug: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(8), default="wifi")        # wifi|wired|lab|vlan|guest|home
    subnet_prefix: Mapped[str] = mapped_column(String(15), default="")  # "192.168.1"
    expected_gateway: Mapped[str] = mapped_column(String(45), default="")
    ssid: Mapped[str] = mapped_column(String(64), default="")
    sensor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    colour: Mapped[str] = mapped_column(String(16), default="")         # a token name, never a hex value
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|unreachable|no-sensor
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    traffic_mode: Mapped[str] = mapped_column(String(8), default="none")  # mirror|router|none -- set by a person in the wizard/Settings, never guessed (spec §5)

class Device(Base):
    __tablename__ = "device"
    # Widened by 0007_networks: the same MAC on two networks (a laptop on the
    # Wi-Fi and on the lab VLAN) is two device rows, so uniqueness is per
    # (tenant, network, mac) rather than per (tenant, mac).
    # NOTE: SQL NULL semantics mean this constrains NOTHING while `network_id`
    # IS NULL -- two un-attached rows for one MAC do not collide. Safe because
    # `0007_networks` backfills every row and `networks.backfill` catches any
    # that arrive un-attached on the next ingest, but never rely on this
    # constraint to de-duplicate rows that have no network yet.
    __table_args__ = (UniqueConstraint("tenant_id", "network_id", "mac", name="uq_device_net_mac"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    mac: Mapped[str] = mapped_column(String(17))
    ip: Mapped[str] = mapped_column(String(45), default="")
    name: Mapped[str] = mapped_column(String(120), default="")
    vendor: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(24), default="unknown")
    label: Mapped[str] = mapped_column(String(120), default="")
    #: Who this device belongs to, set by a person and never inferred.
    #: Vault can see a laptop; it cannot see whose it is, and guessing
    #: from a hostname would put the wrong name on an offboarding
    #: checklist -- a document whose whole value is being trustworthy.
    owner: Mapped[str] = mapped_column(String(120), default="")
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    always_on: Mapped[bool] = mapped_column(Boolean, default=False)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False)
    parent: Mapped[str | None] = mapped_column(String(17), nullable=True)
    risk: Mapped[int] = mapped_column(Integer, default=0)
    presence: Mapped[str] = mapped_column(String(12), default="NEW")
    ports: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen: Mapped[str] = mapped_column(String(32), default="")
    last_seen: Mapped[str] = mapped_column(String(32), default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)   # everything else Nexus keeps in known_macs[mac]

class Scan(Base):
    __tablename__ = "scan"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    ts: Mapped[str] = mapped_column(String(32), index=True)
    sensor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health: Mapped[dict] = mapped_column(JSON, default=dict)
    raw: Mapped[dict] = mapped_column(JSON)   # full last.json-shaped payload

class Metric(Base):
    __tablename__ = "metric"
    __table_args__ = (Index("ix_metric_lookup", "tenant_id", "name", "entity", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    ts: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))      # latency_ms, uptime, disk_pct, mem_pct, load, speed_down, dns_ms, live_devices, score…
    entity: Mapped[str] = mapped_column(String(120), default="")   # site name / host name / '' for global
    value: Mapped[float | None] = mapped_column(Float, nullable=True)

class DailyFact(Base):
    """One monitor's availability for one closed day, written once.

    Everything else on the Assets page recomputes availability from `metric`
    rows on every request, and `retention` deletes those after 90 days. So
    the uptime figure in last month's report is not a record of anything --
    it is a recomputation that quietly changes as its inputs age out, and
    eventually becomes 0 checks. An MSP putting that number in a QBR deck
    cannot answer "is this the same figure you showed me in March?", which
    is the whole problem the trust plan raises in §2.5M.

    These rows are the answer: computed once at day close from the same
    arithmetic the live page uses, then never recomputed. `uptime_pct` is
    stored rather than derived so it cannot drift even if the formula
    changes -- a number a customer was shown has to stay the number they
    were shown, and a later rubric is a different measurement.

    `day` is a date string in the TENANT's own local offset, not UTC.
    A "daily" figure cut at UTC midnight for an account in New York
    silently mixes two of the owner's days together, which is the guaranteed
    "your numbers are wrong" ticket §2.5K warns about.

    The unique key makes closing a day twice a no-op rather than a
    duplicate, so the job can be re-run after a failure without
    double-counting -- the same property `ingest`'s dedup keys give the
    write path.
    """
    __tablename__ = "daily_fact"
    __table_args__ = (UniqueConstraint("tenant_id", "day", "monitor"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[str] = mapped_column(String(10))       # YYYY-MM-DD, tenant-local
    monitor: Mapped[str] = mapped_column(String(120))  # site name
    #: Checks that counted toward availability -- planned maintenance excluded.
    checks: Mapped[int] = mapped_column(Integer, default=0)
    up: Mapped[int] = mapped_column(Integer, default=0)
    #: Checks dropped because a maintenance window covered them. Kept so the
    #: exclusion stays visible rather than being something the figure
    #: quietly benefited from.
    planned: Mapped[int] = mapped_column(Integer, default=0)
    #: NULL below the minimum sample size: two decimal places over a handful
    #: of checks is invented precision, and freezing invented precision
    #: forever is worse than freezing nothing.
    uptime_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

class WanOutage(Base):
    """One period the site had no route to the internet, and whose fault it was.

    These are the evidence for an ISP service-credit claim, which the MSP
    spec calls the closest thing to free money in the product: most SMBs
    never claim because assembling the evidence costs more than the credit,
    and Vault measures from inside the network, which is the one vantage
    point the ISP cannot dispute.

    KEPT FOREVER. `retention` prunes `metric`, `event` and `presence_sample`
    at 90 days; these rows are tiny and they are the only record of an
    outage once the samples behind them are gone. A credit claim is often
    filed against a quarter that has already aged out.

    `cause` is the field the whole feature turns on and it is
    conservatively assigned. `isp` means the sensor could reach the default
    gateway and nothing beyond it -- the line is down and that is the
    provider's problem. `local` means the gateway was unreachable too, so
    the fault is inside the building. `unknown` is everything else, and a
    claim built on an `unknown` is one the ISP will reject. Guessing `isp`
    once and being wrong would destroy the feature's credibility with the
    customer's own provider, which is worse than not offering it.
    """
    __tablename__ = "wan_outage"
    __table_args__ = (UniqueConstraint("tenant_id", "started_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[str] = mapped_column(String(32))
    #: NULL while the outage is still open, so a claim can name an ongoing one.
    ended_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cause: Mapped[str] = mapped_column(String(12), default="unknown")  # isp|local|unknown
    #: What was and was not reachable, kept so a disputed claim can be
    #: re-argued from the observation rather than from the verdict.
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

class Event(Base):
    __tablename__ = "event"
    __table_args__ = (Index("ix_event_t", "tenant_id", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(32))      # join|leave|port_open|port_closed|wan|fix|scan|rotated…
    entity: Mapped[str] = mapped_column(String(120), default="")   # mac / host / site
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

#: Alert statuses that are NOT waiting on a person, and so do not belong in
#: a "what needs me" list or its badge.
#:
#: `acked` is in here deliberately. Acknowledging means "I have seen this and
#: I am not going to act on it" -- the owner acked a DMARC advisory and was
#: surprised to still find it sitting in the open list with an unread badge,
#: which is fair: a button called Acknowledge that leaves the row exactly
#: where it was does nothing a person can perceive. The alert is NOT closed
#: and is not pretending to be: it stays in the "All" view, keeps its status,
#: and the sensor keeps it acked until the condition genuinely clears.
#:
#: Four separate queries used to spell this set out, and they had already
#: drifted -- the overview and topology counted only `status == "open"` while
#: the inbox counted acked rows too, so the same network showed two different
#: numbers of open alerts depending on the page.
SETTLED_ALERT_STATUSES = ("resolved", "snoozed", "acked")


class Alert(Base):
    __tablename__ = "alert"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(200))
    sev: Mapped[str] = mapped_column(String(8))        # CRIT|WARN|INFO
    msg: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[str] = mapped_column(String(12), default="")
    first_seen: Mapped[str] = mapped_column(String(32))
    last_seen: Mapped[str] = mapped_column(String(32))
    count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(12), default="open")   # open|acked|snoozed|resolved
    resolved_by: Mapped[str] = mapped_column(String(24), default="")  # auto|fix|human
    snoozed_until: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fix: Mapped[str] = mapped_column(String(200), default="")
    ticket_key: Mapped[str] = mapped_column(String(64), default="")   # Jira/ServiceNow key once an integration filed one
    escalated_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)  # last ntfy CRIT-resend (fix round 1, finding 4)

class Incident(Base):
    __tablename__ = "incident"
    # Widened by 0007_networks: each sensor numbers its own incidents from
    # `state.incident_seq` starting at 100, so two sensors on one tenant used
    # to overwrite each other's rows on `iid` collision. `sensor_id` is
    # nullable for rows that predate the column (and for imports with no
    # sensor), which SQLite/Postgres both treat as never-equal in a UNIQUE --
    # `ingest.store_scan` always populates it from the reporting sensor.
    __table_args__ = (UniqueConstraint("tenant_id", "sensor_id", "iid", name="uq_incident_sensor_iid"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    sensor_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    iid: Mapped[str] = mapped_column(String(32))       # Nexus stable incident id
    entity: Mapped[str] = mapped_column(String(120))
    sev: Mapped[str] = mapped_column(String(8))
    story: Mapped[str] = mapped_column(Text)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    alert_keys: Mapped[list] = mapped_column(JSON, default=list)
    opened: Mapped[str] = mapped_column(String(32))
    closed: Mapped[str | None] = mapped_column(String(32), nullable=True)

class Recommendation(Base):
    __tablename__ = "recommendation"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(200))
    priority: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(Text)
    why: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # {action, host, arg} or null
    decision: Mapped[str] = mapped_column(String(8), default="")   # ''|yes|no
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(12), default="")    # ''|queued|done|failed|needs-you
    last_seen: Mapped[str] = mapped_column(String(32))

class Fix(Base):
    __tablename__ = "fix"
    sensor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[str] = mapped_column(String(32))
    host: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(200), default="")
    arg: Mapped[str | None] = mapped_column(String(200), nullable=True)
    by: Mapped[str] = mapped_column(String(64), default="auto")
    ok: Mapped[bool] = mapped_column(Boolean)
    out: Mapped[str] = mapped_column(Text, default="")
    reboot_needed: Mapped[bool] = mapped_column(Boolean, default=False)

class DnsQuery(Base):
    __tablename__ = "dns_query"
    __table_args__ = (Index("ix_dnsq", "tenant_id", "ts"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[str] = mapped_column(String(32))
    client: Mapped[str] = mapped_column(String(45))
    name: Mapped[str] = mapped_column(String(255))
    qtype: Mapped[str] = mapped_column(String(8))
    blocked: Mapped[bool] = mapped_column(Boolean)
    list: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ms: Mapped[int] = mapped_column(Integer, default=0)
    mac: Mapped[str | None] = mapped_column(String(17), nullable=True)   # resolved from state.lan.devices[ip] at ingest

class DnsRule(Base):
    __tablename__ = "dns_rule"
    __table_args__ = (UniqueConstraint("tenant_id", "kind", "domain"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8))       # allow|block
    domain: Mapped[str] = mapped_column(String(255))


class DnsRollup(Base):
    """One row per (day, network, mac, domain): the retention job's daily
    DNS rollup (Task 55) -- `retention.rollup_dns` writes these before the
    matching raw `DnsQuery` rows age out past `dns_raw_days`, so
    `payloads/dnsview.py` can still answer a 30/90/365-day DNS view after
    the raw rows are gone. `network_id` carries the raw rows' own attribution
    (NULL only for rows the sensor could not attribute, or rolled up before
    `0007_networks`); `mac` is nullable too -- the
    "(other)" tail bucket beyond the top-200-per-day cap collapses across
    mac, so it carries `mac=None`."""
    __tablename__ = "dns_rollup"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "network_id", "mac", "name"),
        Index("ix_dnsroll", "tenant_id", "day"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[str] = mapped_column(String(10))        # YYYY-MM-DD
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mac: Mapped[str | None] = mapped_column(String(17), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    queries: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[int] = mapped_column(Integer, default=0)

class Flow(Base):
    """One minute of one device talking to one peer on one port (spec §2).
    Keyed on the device's MAC, not its IP, so a DHCP renewal does not split a
    device's history. `ts` is the sensor's own ISO string -- lexicographic in
    SQL, see history.py's entry in CLAUDE.md. `hints` holds only the three
    fingerprinting prefixes; nothing from a packet body is ever stored."""
    __tablename__ = "flow"
    __table_args__ = (
        UniqueConstraint("tenant_id", "ts", "mac", "peer_ip", "peer_port", "proto", "source", name="uq_flow"),
        Index("ix_flow_ts", "tenant_id", "ts"),
        Index("ix_flow_mac", "tenant_id", "mac", "ts"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[str] = mapped_column(String(32))
    mac: Mapped[str] = mapped_column(String(17))
    ip: Mapped[str] = mapped_column(String(45), default="")
    peer_ip: Mapped[str] = mapped_column(String(45), default="")
    peer_port: Mapped[int] = mapped_column(Integer, default=0)
    proto: Mapped[str] = mapped_column(String(8), default="other")
    bytes_in: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)
    pkts_in: Mapped[int] = mapped_column(Integer, default=0)
    pkts_out: Mapped[int] = mapped_column(Integer, default=0)
    peer_name: Mapped[str] = mapped_column(String(255), default="")
    hints: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(8), default="mirror")   # mirror|router

class FlowRollup(Base):
    """Per-day flow totals that outlive the minute rows (spec §3), the
    `DnsRollup` pattern applied to traffic. `peer` is `peer_name` when the
    resolver saw one, else `peer_ip`."""
    __tablename__ = "flow_rollup"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "network_id", "mac", "peer", "peer_port", "proto"),
        Index("ix_flowroll", "tenant_id", "day"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[str] = mapped_column(String(10))
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mac: Mapped[str] = mapped_column(String(17))
    peer: Mapped[str] = mapped_column(String(255))
    peer_port: Mapped[int] = mapped_column(Integer, default=0)
    proto: Mapped[str] = mapped_column(String(8), default="other")
    bytes_in: Mapped[int] = mapped_column(Integer, default=0)
    bytes_out: Mapped[int] = mapped_column(Integer, default=0)
    pkts: Mapped[int] = mapped_column(Integer, default=0)

class FlowPeer(Base):
    """When each destination was first and last seen, per network.

    Exists so "is this peer new?" is a lookup of the few dozen peers on
    screen, not a scan of every raw flow in the retention window -- which on
    a real home network was 3.9 million rows and 70+ seconds per Live
    connections or Topology load (measured 2026-10-06).

    Kept current by `flowpeers`' flush hook on every new `Flow`, so no writer
    can forget it. `first_seen`/`last_seen` are normalised to UTC
    (`...+00:00`) on the way in, unlike `Flow.ts`: that makes SQL text
    comparison chronological here, which is the whole point of the table.
    `network_id` is "" rather than NULL for flows with no network, because
    SQLite treats NULLs as distinct in a unique key."""
    __tablename__ = "flow_peer"
    __table_args__ = (
        UniqueConstraint("tenant_id", "network_id", "label", name="uq_flow_peer"),
        Index("ix_flow_peer_label", "tenant_id", "label"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    network_id: Mapped[str] = mapped_column(String(32), default="")
    label: Mapped[str] = mapped_column(String(255))
    first_seen: Mapped[str] = mapped_column(String(32))
    last_seen: Mapped[str] = mapped_column(String(32))

class Edit(Base):
    __tablename__ = "edit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    who: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(80))      # Nexus edit path: /api/device, /api/device/forget, /api/device/merge, /api/device/quarantine, /api/ack, /api/alert, /api/config, /api/rec, /api/attacker, /api/maintenance, /api/profile
    body: Mapped[dict] = mapped_column(JSON)

class Queue(Base):
    __tablename__ = "queue"
    __table_args__ = (Index("uq_queue_active_request", "tenant_id", "fingerprint", unique=True,
        sqlite_where=text("status IN ('queued','scheduled','running')"),
        postgresql_where=text("status IN ('queued','scheduled','running')")),)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    who: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(80))      # /api/fix | /api/run | /api/speedtest | /api/reboot-plan
    body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|done|failed
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # `ts` above is the enqueue time ("queued_at" in spirit -- see
    # payloads/updates.py's UpgradeProgress.started comment); this is the
    # real completion time, set once by `qresult` when the sensor posts
    # /result. Nullable/None until then, never fabricated from `ts`.
    finished_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)

    sensor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    not_before: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    wait_reason: Mapped[str] = mapped_column(String(240), default="")

class Report(Base):
    __tablename__ = "report"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(12))      # weekly|monthly|manual
    period_days: Mapped[int] = mapped_column(Integer)
    preset: Mapped[str] = mapped_column(String(32), default="")
    sections: Mapped[list] = mapped_column(JSON, default=list)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    path: Mapped[str] = mapped_column(String(300))
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    share_expires: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)   # {score, period_start, period_end, counts…}
    #: SHA-256 of the rendered document, printed inside it. See
    #: `reports.builder.stamp_content_hash` for the canonical form -- the
    #: value is not a hash of the file as it stands, because printing a hash
    #: into a document changes the document. "" for reports generated before
    #: stamping existed, never back-filled: a hash computed today over a file
    #: written last year proves nothing about what it said when it was sent.
    content_hash: Mapped[str] = mapped_column(String(64), default="")

class Integration(Base):
    __tablename__ = "integration"
    __table_args__ = (UniqueConstraint("tenant_id", "kind"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(24))      # ntfy|slack|teams|webhook|discord|pagerduty|telegram|jira|servicenow|homeassistant
    secret: Mapped[str] = mapped_column(Text, default="")   # topic / webhook url; encrypted on write via crypto.py (Task 40)
    # Every OTHER kind="secret" field, as one Fernet blob of a
    # {field_name: plaintext} dict (crypto.encrypt_json). Fix round 1,
    # ruling (a): a provider's second/third credential is encrypted at rest
    # too, never parked in the plaintext `config` column below.
    secrets_enc: Mapped[str] = mapped_column(Text, default="")
    routes: Mapped[dict] = mapped_column(JSON, default=dict) # {"CRIT": true, "WARN": true, "INFO": false}
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    name: Mapped[str] = mapped_column(String(60), default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)   # non-secret per-kind fields (chat id, project key, base url…)
    inbound_token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True, unique=True)
    last_test: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")

class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    user: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16), default="")
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")
    body: Mapped[dict] = mapped_column(JSON, default=dict)

class Heartbeat(Base):
    __tablename__ = "heartbeat"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    token: Mapped[str] = mapped_column(String(64))
    expect_minutes: Mapped[int] = mapped_column(Integer, default=1500)
    last_ping: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)


class PresenceSample(Base):
    """One row per (history line, live mac): the raw material for presence
    profiles, the 24 h/7 d online percentages and the hour heatmap."""
    __tablename__ = "presence_sample"
    __table_args__ = (Index("ix_pres_t", "tenant_id", "ts"), Index("ix_pres_mac", "tenant_id", "mac", "ts"),
                      UniqueConstraint("tenant_id", "ts", "mac", "network_id", name="uq_pres_ts_mac_net"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(32))
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    ts: Mapped[str] = mapped_column(String(32))
    mac: Mapped[str] = mapped_column(String(17))


class IpGeo(Base):
    """ip-api.com lookup cache, keyed by ip (hub-global, not tenant-scoped:
    geo facts about a public ip are not tenant data)."""
    __tablename__ = "ip_geo"
    ip: Mapped[str] = mapped_column(String(45), primary_key=True)
    country: Mapped[str] = mapped_column(String(64), default="")
    country_code: Mapped[str] = mapped_column(String(4), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    org: Mapped[str] = mapped_column(String(160), default="")
    isp: Mapped[str] = mapped_column(String(160), default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    hosting: Mapped[bool] = mapped_column(Boolean, default=False)
    reverse: Mapped[str] = mapped_column(String(255), default="")
    fetched: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)


class ActToken(Base):
    """One-shot action token behind an ntfy / Telegram button: the public
    /act/<token> route authenticates on this row alone."""
    __tablename__ = "act_token"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))      # device|alert|chat
    target: Mapped[str] = mapped_column(String(200))   # mac or alert key or (for kind="chat") tool name
    actions: Mapped[list] = mapped_column(JSON, default=list)  # ["trust","snooze","ack"] (never "fix" -- acttokens.ALLOWED_ACTIONS); a kind="chat" row instead stores [ProposedAction.model_dump()]
    expires: Mapped[dt.datetime] = mapped_column(TZDateTime)
    used: Mapped[str] = mapped_column(String(16), default="")  # "" | the action used
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)


class ChatThread(Base):
    """One Vault-chat conversation. `ChatRequest.thread` carries this
    row's id back from the web on every turn after the first (which sends
    `thread=None` and gets a fresh id back in `ChatResponse.thread_id`) --
    the client never re-sends message history itself, only the id, so the
    provider ladder in chat/providers.py always builds its `messages` list
    from what is stored here."""
    __tablename__ = "chat_thread"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    # [{"role": "user"|"guardian", "text": "..."}, ...] -- the raw text each
    # side sent/answered, not the full provider tool-call trace (that lives
    # only in the per-turn Audit rows, never dropped to the client here).
    turns: Mapped[list] = mapped_column(JSON, default=list)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    updated: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class DecisionOutcome(Base):
    """What Vault decided about one repair, and what happened next.

    The dataset that does not exist yet. Production carries 78 repairs, all
    successful -- a single class, which no classifier can learn a boundary
    from. This table is how that changes: one row per decision, the features
    FROZEN as they were at the time, and the outcome filled in later.

    Frozen matters. Features recomputed when the outcome lands would train a
    model on facts the decision did not have, which is the most flattering
    and least useful kind of dataset.
    """
    __tablename__ = "decision_outcome"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    alert_key: Mapped[str] = mapped_column(String(200), default="")
    host: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(40), default="")
    #: The repair's argument -- the service for `restart_service`, the
    #: container for `restart_container`, the address for `block_ip`. Part of
    #: the row's IDENTITY, not decoration: without it `restart_service nginx`
    #: and `restart_service postgres` on one host are two decisions nothing
    #: can tell apart, so verifying one would settle both. `remedy.
    #: alerts_for_fix` is careful about exactly this distinction one column
    #: over ("so restarting `nginx` never closes the alert about `postgres`
    #: on the same host"), and `decisions.settle` matches on it for the same
    #: reason. Normalised to `""` rather than NULL at both ends (see
    #: `decisions.record`), so the match has no NULL semantics to reason about.
    arg: Mapped[str] = mapped_column(String(200), default="")
    #: Frozen at the moment of the decision, never recomputed -- see the class
    #: docstring for why that is the whole point of this column.
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String(12), default="")
    #: auto | approval -- the tier the decision was actually taken at, from
    #: `chat.tiers.effective_tier` (see `routers/write._record_decision`).
    #: `refused` and `ignored` would need a writer on the OFFER path -- the
    #: read path, once per open alert per dashboard load -- which is the cost
    #: `_record_decision`'s docstring rules out, so nothing emits them and
    #: they are deliberately not named here.
    chosen: Mapped[str] = mapped_column(String(12), default="")
    #: held | recurred | undone | unknown -- "" until it settles. Outcome starts
    #: empty and is filled in once. The first thing that happened is what the
    #: dataset keeps; an alert that recurs, is repaired, and recurs again must
    #: not rewrite the earlier decision's outcome.
    outcome: Mapped[str] = mapped_column(String(12), default="", index=True)
    decided_at: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    settled_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)


class Lead(Base):
    """One "Request access" submission from the public site on guarder.io.

    Hub-global, not tenant-scoped -- like `IpGeo` above, and for the stronger
    reason: a lead is the product owner's sales pipeline, not any customer's
    data. There is no tenant to attach it to (the person has no account yet)
    and attaching it to one would leak the pipeline to that tenant's admins,
    so the only surface that reads these rows is superadmin-only.

    Every column is capped at the length `schemas.LeadIn` validates, so a
    write that somehow skipped the schema still cannot store an unbounded
    string. `ip` is the submitting address as nginx reported it (see
    `routers/public.py::_client_ip`) and is kept for abuse triage only -- it
    is never shown on the public site and never echoed back to the submitter.
    """
    __tablename__ = "lead"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(120), default="")
    network_size: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(String(2000), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")
    # The applicant's own name for the site (the owner cannot know it); the
    # tenant-name default at approval.
    site_name: Mapped[str] = mapped_column(String(120), default="")
    # pending | approved | rejected. A decision is recorded once; see
    # `accounts.provision_from_lead` and `routers/leads.py`.
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    # The tenant approval created. SET NULL if that tenant is later deleted so
    # the lead's history survives.
    tenant_id: Mapped[str | None] = mapped_column(
        ForeignKey("tenant.id", ondelete="SET NULL"), nullable=True)
    # When the applicant ticked "I agree to the Terms of Service" on the
    # sign-up page. Required at submission (`schemas.LeadIn.accept_terms`).
    terms_accepted_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow, index=True)


class Subscription(Base):
    """One tenant's subscription -- exactly one row per tenant, ever.

    The plan id is a `plans.PlanId` string, so "which plan is this account
    on?" is answered from `guardian_hub.plans` and never from a price
    duplicated here. No money is stored on this row on purpose: an amount
    copied out of the catalogue at signup would silently go stale the day a
    price changes, and reconciling it against Stripe is a job nobody wants.
    What IS stored is the state Stripe owns and this hub cannot recompute --
    which customer, which subscription, when the period ends.

    `status` is Vault's own vocabulary, not Stripe's, because one of the
    values has no Stripe equivalent:

    * `incomplete` -- a checkout was started and never finished. The row has
                     to exist before the redirect (it is how the webhook finds
                     the tenant), but nothing has been paid and nothing has
                     been agreed, so this status must not read as either: the
                     account is fully monitored, shows no "payment failed"
                     notice, claims no current plan, and can still convert to
                     the free trial or retry the same plan. Somebody closing
                     the Stripe tab is the ordinary case, not an edge one.
    * `trialing`  -- on the free trial. No card, no Stripe objects at all.
    * `active`    -- a paid plan, paid up.
    * `past_due`  -- a paid plan whose last payment failed. **Still fully
                     monitored.** A watchdog that stops watching because a
                     card expired is worse than useless, so nothing in the
                     product degrades here; the account is nagged, not cut.
    * `paused`    -- the free trial ran out and nobody chose a plan. Data
                     stays readable and nothing is destroyed; new sensor
                     ingest is refused (see `billing.ingest_allowed`) because
                     collecting fresh data indefinitely for an account that
                     was never paid for is the one thing a free trial must
                     not become.
    * `canceled`  -- a paid plan the customer ended. Same shape as `paused`.

    `last_event_created` is the `created` stamp of the newest Stripe event
    applied to this row: Stripe does not guarantee delivery order, so an
    older event arriving after a newer one is ignored rather than allowed to
    resurrect a stale status. `BillingEvent` handles the *duplicate* case;
    this column handles the *out-of-order* one.
    """
    __tablename__ = "subscription"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(
        ForeignKey("tenant.id", ondelete="CASCADE"), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(32), default="trial")
    interval: Mapped[str] = mapped_column(String(8), default="none")   # month|year|none
    status: Mapped[str] = mapped_column(String(16), default="trialing", index=True)
    # Set for `trialing` rows only; the instant the trial stops being free.
    trial_end: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    # Stripe's `current_period_end` for a paid subscription: when it renews.
    current_period_end: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    stripe_customer_id: Mapped[str] = mapped_column(String(64), default="")
    stripe_subscription_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Highest Stripe `event.created` (unix seconds) applied to this row.
    last_event_created: Mapped[int] = mapped_column(Integer, default=0)
    # Set once, when the tenant is first warned that the trial is nearly up,
    # so the in-app notice can be raised without re-notifying every poll.
    trial_warned: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    updated: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class BillingEvent(Base):
    """A Stripe webhook event this hub has already applied. Replay guard.

    The primary key IS Stripe's event id, so a retry -- and Stripe retries
    for days -- hits a unique-constraint collision instead of applying the
    same subscription change twice. The webhook route answers 200 on a
    duplicate: a 4xx or 5xx would keep Stripe retrying forever for an event
    that was in fact handled.

    Hub-global rather than tenant-scoped: an event may arrive before this hub
    knows which tenant it belongs to (an unrecognised customer id), and it
    must still be recorded as seen so it is not reprocessed.
    """
    __tablename__ = "billing_event"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)   # Stripe evt_...
    type: Mapped[str] = mapped_column(String(80), default="")
    created: Mapped[int] = mapped_column(Integer, default=0)        # unix seconds
    tenant_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    received: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow, index=True)


class UserOnboarding(Base):
    """Whether ONE user has been through the first-run wizard on ONE tenant.

    The wizard used to be gated per tenant, off the overview payload: no scan
    and no sensor meant "show it". That is the wrong subject. The second
    person to join an already-configured tenant -- the one who most needs
    orientation -- never saw it, and a browser's `localStorage` flag could
    not fix that because the same person on a second machine had still seen
    it.

    Keyed `(user_id, tenant_id)` rather than by user alone: what the wizard
    shows is tenant-specific (this site's networks, this site's sensor), and
    a user invited to a second site is genuinely new to that site.

    `step` is the resumable step name, written by the wizard as it advances.
    `completed` is "walked it to the end", `skipped` is "Skip for now" --
    both suppress the redirect, and they are kept apart so the product can
    tell an abandoned wizard from a finished one without a second table.
    """
    __tablename__ = "user_onboarding"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    step: Mapped[str] = mapped_column(String(32), default="")
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    updated: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class Recorder(Base):
    """One camera recorder (NVR/DVR) whose channels Vault enumerates.

    Task 73. A house with ten or twelve cameras almost never has them on the
    LAN: they hang off the recorder's own PoE ports, on the recorder's own
    private network, and an ARP sweep of the LAN sees only the recorder. So
    the recorder is registered here, the sensor asks it over its documented
    HTTP API which cameras are behind it, and each answer becomes an ordinary
    `Device` row on `network_id` -- a `Network` whose gateway IS this recorder.
    That is what keeps Assets, Topology, Security and the per-network counts
    working with no special case anywhere: a camera behind a recorder is a
    device on a network, the same as every other device Vault knows.

    `secret_enc` is the recorder's password as ONE Fernet blob
    (`crypto.encrypt_json({"password": ...})`), never a plaintext column and
    never in `config`. It leaves the hub in exactly one direction: down the
    authenticated sensor channel, to the one sensor that can reach the box.
    It is never returned to a browser, never in an alert, never in a report.
    """
    __tablename__ = "recorder"
    # One row per address per tenant: registering the same box twice would
    # poll it twice and double every finding.
    __table_args__ = (UniqueConstraint("tenant_id", "host", name="uq_recorder_host"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    #: The camera network this recorder is the gateway of. Created with the
    #: recorder; nullable only so a row can exist for the moment between the
    #: two inserts.
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    #: The `Network` the recorder ITSELF sits on -- the LAN the sensor sweeps.
    #: Used to scope the "is this address inside our own networks" check.
    lan_network_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    vendor: Mapped[str] = mapped_column(String(24), default="reolink")
    host: Mapped[str] = mapped_column(String(45), default="")
    port: Mapped[int] = mapped_column(Integer, default=80)
    scheme: Mapped[str] = mapped_column(String(8), default="http")
    username: Mapped[str] = mapped_column(String(64), default="")
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    #: What the owner says is behind it. 0 = "don't check the count" -- the
    #: count-mismatch finding exists precisely because the owner knows the
    #: number and Vault does not.
    expected_channels: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: pending (never read) | ok | error
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_kind: Mapped[str] = mapped_column(String(24), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    firmware: Mapped[str] = mapped_column(String(64), default="")
    #: The last channel list read off the recorder: `[{channel, name, online,
    #: model, mac}]`. The device rows are the durable record; this is what the
    #: Recorders admin surface shows without a join.
    channels: Mapped[list] = mapped_column(JSON, default=list)
    last_ok: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_attempt: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)


class Router(Base):
    """The home/site gateway router Vault can administer -- ONE per tenant.

    Vault's hub cannot reach a customer's LAN gateway (192.168.x.x); only
    the enrolled sensor sitting on that LAN can. So this row holds the router's
    admin credential and the sensor is the only thing that ever uses it, pulled
    in the clear over the authenticated sensor channel (`GET /api/v1/sensor/
    router`), exactly like `Recorder`. `secret_enc` is the admin password as one
    Fernet blob (`crypto.encrypt_json({"password": ...})`) -- never a plaintext
    column, never returned to a browser, never in an alert, an audit body, or a
    report. The browser only ever learns `has_credentials` and the last Test
    result. Nothing here mutates the router: the only sensor action wired today
    is a login+logout credential Test.
    """
    __tablename__ = "router"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_router_tenant"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    #: fios (CR1000A/CR1000B). Only vendor whose handshake is implemented.
    vendor: Mapped[str] = mapped_column(String(24), default="fios")
    #: The router admin URL the sensor logs into, e.g. https://192.168.1.1
    base_url: Mapped[str] = mapped_column(String(128), default="")
    #: Most Fios units have no username (password only); kept for other vendors.
    username: Mapped[str] = mapped_column(String(64), default="")
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: pending (never tested) | ok | error
    status: Mapped[str] = mapped_column(String(16), default="pending")
    #: Last credential-Test outcome, safe to show a browser (no secret in it).
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_msg: Mapped[str] = mapped_column(String(300), default="")
    last_test: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: When Vault last successfully turned remote administration OFF on this
    #: gateway. It records what GUARDIAN DID, not what the router currently is:
    #: the Fios config API is a write-only diff model (`apply_abstract.cgi`)
    #: with no getter, so nothing here can read the live value back. Somebody
    #: who re-enables remote admin from the router's own admin page leaves this
    #: stamp standing, which is why the card keeps a "turn it off again" path
    #: instead of claiming the setting is off right now. A CHANGED `base_url`
    #: clears it (that is a different router); a replaced password does not.
    remote_admin_off_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    #: The sensor's plain-English line from that run (never a credential), or
    #: the failure message from the last attempt that did NOT take.
    remote_admin_msg: Mapped[str] = mapped_column(String(300), default="")
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)


class ApiToken(Base):
    """One customer-held credential for the public REST API on api.guarder.io.

    Deliberately NOT a `Session` row and never the `guardian_session` cookie:
    a session is a browser's, expires in hours, and carries whatever role its
    user holds *today*. An API token is a machine's, is created and revoked
    by an admin in the app, and carries a role frozen at creation.

    Only the hash is stored. The raw secret is shown once, at creation, and
    is unrecoverable afterwards -- `prefix` is the recognisable-in-a-log
    fragment an admin matches a log line back to a row with.

    `role` is already clamped to the creating admin's own role
    (`apikeys.mint`), so the auth dependency reads it straight off the row and
    never consults `Membership` -- a token whose creator was later demoted, or
    removed from the tenant entirely, cannot thereby gain authority.
    """
    __tablename__ = "api_token"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    #: The public, non-secret head of the raw token ("gapi_<8 hex>"). Shown in
    #: the app and safe to paste into a support ticket.
    prefix: Mapped[str] = mapped_column(String(24), default="", index=True)
    #: sha256 of the WHOLE raw token (`security.hash_token`). Unique + indexed
    #: so authentication is one indexed lookup, never a scan-and-compare.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(8), default="read")    # read|write
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # viewer|operator|admin
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    expires: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_used: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_used_ip: Mapped[str] = mapped_column(String(45), default="")
    #: Revocation is a flag, not a delete: the audit rows a token wrote name
    #: it, and those names must stay resolvable after it is turned off.
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)

class PlatformSetting(Base):
    """One operator-editable platform setting, stored as text.

    Deliberately a tiny key/value table rather than a column per setting: the
    set of things worth changing from the console grows, and each addition
    should be a row in `platform_settings.SPEC`, not a migration.

    Values are TEXT and coerced on read by the spec that owns the key, so a
    row left over from an older build cannot crash a page -- an unknown key is
    ignored and its default used.

    Never a secret. Credentials stay in the environment: a database is backed
    up, exported, and copied about, and a token in one is a token in all of
    those places. The console reports whether each credential is set and
    nothing more.
    """
    __tablename__ = "platform_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class Ticket(Base):
    """One support conversation between a customer and the operator.

    Customers raise them, the owner answers: there is no third party and no
    assignment, so there is no assignee column and no queue-per-agent. What a
    ticket needs to carry is who it belongs to, what it is about, and whether
    it is waiting on the customer or on us.

    `status` is the whole state machine, and it is deliberately three words:

      open      -- the customer has said something and is waiting on us
      answered  -- we have replied; the ball is with the customer
      resolved  -- done. Set by the OWNER when the issue is fixed.

    A customer reply on a resolved ticket reopens it rather than starting a
    second conversation about the same thing, which is what every support tool
    that does otherwise ends up regretting.

    Nothing here is deleted when a ticket is resolved: the thread is the
    record of what was wrong and what was done about it, and the customer can
    still read it.
    """
    __tablename__ = "ticket"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(160), default="")
    #: open | answered | resolved
    status: Mapped[str] = mapped_column(String(12), default="open", index=True)
    #: The customer's own words for how much it hurts: low | normal | high.
    #: Advisory only -- it orders the owner's queue, it does not promise an SLA.
    priority: Mapped[str] = mapped_column(String(8), default="normal")
    #: The username who raised it, for the owner's queue. Never an email.
    opened_by: Mapped[str] = mapped_column(String(64), default="")
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    #: When the last message landed, from either side. The queue sorts on it,
    #: so a thread that has just been answered does not look stale.
    last_message_at: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    #: Who last spoke: customer | owner. With `status` it is what tells the
    #: owner "this one is waiting on me" without opening it.
    last_author: Mapped[str] = mapped_column(String(12), default="customer")
    resolved_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
    resolved_by: Mapped[str] = mapped_column(String(64), default="")
    #: What KIND of thing this is about, in Vault's own vocabulary rather
    #: than a generic desk's ("Billing / Technical / Other"). See
    #: `tickets.CATEGORIES`. It is the customer's answer, never inferred: a
    #: category guessed from a subject line is a fact Vault made up.
    category: Mapped[str] = mapped_column(String(16), default="other")
    #: The one thing in Vault this ticket concerns, so the operator opens
    #: the thread already knowing what the customer was looking at. `kind` is
    #: the collection ("device", "alert", "site", "network"), `context_id`
    #: indexes into it, and `context_label` is what the customer saw on
    #: screen -- stored, not re-derived, because the row it named may be gone
    #: by the time anyone reads the ticket and a dangling id explains nothing.
    context_kind: Mapped[str] = mapped_column(String(16), default="")
    context_id: Mapped[str] = mapped_column(String(64), default="")
    context_label: Mapped[str] = mapped_column(String(160), default="")


class FirewallProposal(Base):
    """A gateway firewall rule Vault thinks should exist, and why.

    Miro's ruling, and the reason this table exists at all: Vault
    **proposes** rules with one-click apply and a timed auto-rollback. It
    never writes to a router silently. So a proposal is a row with a
    decision on it, not a queued command -- and applying one is a separate,
    person-initiated act that stamps `applied_at` and `rollback_at`
    together, because a firewall change nobody confirmed is the change that
    locked somebody out at 2am.

    `key` is what makes a proposal the same proposal across scans. Without
    it a finding that persists would mint a new row every five minutes and
    a declined rule would come straight back -- which is how a security
    product teaches people to ignore it.
    """
    __tablename__ = "firewall_proposal"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="ix_fwprop_tenant_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    network_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    key: Mapped[str] = mapped_column(String(160))
    #: block_inbound | close_gateway_port
    action: Mapped[str] = mapped_column(String(16), default="block_inbound")
    proto: Mapped[str] = mapped_column(String(8), default="tcp")
    port: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    because: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(String(8), default="WARN")
    #: proposed | declined | applied | rolled_back | expired
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    decided_at: Mapped[str] = mapped_column(String(32), default="")
    reason: Mapped[str] = mapped_column(String(300), default="")
    applied_at: Mapped[str] = mapped_column(String(32), default="")
    rollback_at: Mapped[str] = mapped_column(String(32), default="")
    first_seen: Mapped[str] = mapped_column(String(32), default="")
    last_seen: Mapped[str] = mapped_column(String(32), default="")


class TicketMessage(Base):
    """One message in a ticket thread, from either side.

    `author_side` is `customer` or `owner` -- never a role name. The customer
    must be able to tell Vault's answers from their own words, and a role
    ("admin") would not do that: both sides are admins of something.

    Every message is written and committed BEFORE its notification is sent.
    `mailer.send` never raises, so ordering is the only guarantee available:
    a letter that referenced a message the database did not have would be
    unanswerable.
    """
    __tablename__ = "ticket_message"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("ticket.id", ondelete="CASCADE"), index=True)
    #: Denormalised so a tenant-scoped read never has to join to check
    #: ownership -- the same defence `Alert` and `Device` already use.
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    #: customer | owner
    author_side: Mapped[str] = mapped_column(String(12), default="customer")
    #: The display name shown in the thread (a username, or "Vault").
    author_name: Mapped[str] = mapped_column(String(64), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow, index=True)
    #: Whether a notification for this message actually went out, and where.
    #: A tenant with no contact address on file is the ORDINARY case, not an
    #: error: the in-app thread is the system of record and the letter is a
    #: convenience, so this records what happened instead of pretending.
    emailed_to: Mapped[str] = mapped_column(String(160), default="")


class CheckDecision(Base):
    """A deliberate owner decision on one Security-page check that carries no
    `Alert` row of its own -- a Wi-Fi guard row, a checklist row, a scorecard
    deduction. "Leave it" records that the owner has seen the failing check
    and chosen to leave it as-is (as opposed to a check nobody has looked at
    yet); "undo" reopens it. There is no "resolved" status here on purpose:
    an autofix-backed check resolves itself the moment the next scan shows it
    passing, and recording that separately could disagree with the live
    fact.

    `check_key` is the `SecurityCheck.key` (already unique per host/site,
    e.g. "ssh:vps1"; the four Wi-Fi guard keys and the two scorecard
    deductions with no matching check row repeat, so `network_id` and
    `target` disambiguate them there). All three are plain strings with no
    FK to `network` -- a Wi-Fi guard's own key namespace does not join to a
    real `Alert`/`Network` row either (see `guardian_hub/remedy.py`'s module
    docstring), and forcing one here would only add a constraint nothing
    reads.
    """
    __tablename__ = "check_decision"
    __table_args__ = (UniqueConstraint("tenant_id", "check_key", "network_id", "target"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    check_key: Mapped[str] = mapped_column(String(120))
    network_id: Mapped[str] = mapped_column(String(32), default="")
    target: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(12), default="open")  # open|left
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(64), default="")
    decided_role: Mapped[str] = mapped_column(String(16), default="")
    decided_at: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    expires_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)


class ConversationState(Base):
    __tablename__ = "conversation_state"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)

class Delivery(Base):
    __tablename__ = "delivery"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    reference: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(String(300), default="")
    updated: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)


# Registers the flush hook that keeps `FlowPeer` current. At the bottom, and
# `flowpeers` imports nothing from here at module scope, so there is no cycle.
from . import flowpeers as _flowpeers  # noqa: E402,F401
