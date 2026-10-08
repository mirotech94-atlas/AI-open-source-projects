// Vault dashboard contract types — the TypeScript mirror of
// `hub/guardian_hub/schemas_dash.py`.
//
// Rules:
// - One `export type` per pydantic model in `schemas_dash.py`, same name.
// - Field names are the hub's `snake_case`, verbatim: nothing in this app
//   camelCases an API field.
// - `X | None` on the hub is `X | null` here, never an optional `x?:` — the
//   hub always emits every key.
// - Payloads are rendering-ready. If a component needs an aggregate that
//   isn't here, it belongs in the hub payload builder, not in the UI.
//
// `hub/scripts/check_types.py` and `hub/tests/test_contracts.py` fail the
// build when a name or a field drifts from the Python side, so edit both
// sides together.

// Severity, shared by alerts, inbox rows and timeline items. Declared and
// re-exported separately (not `export type Sev = …`) because the parity test
// pairs every `^export type <Name>` in this file with a pydantic *model*, and
// Sev is a Literal alias with no model behind it.
type Sev = "CRIT" | "WARN" | "INFO";
export type { Sev };

export type NetworkRef = {
  id: string;
  slug: string;
  name: string;
  colour: string;
};

export type NetworkHealth = {
  id: string;
  slug: string;
  name: string;
  colour: string;
  state: string;
  status: string;
  devices: number;
  online: number;
  unverified: number;
  cameras: number;
  cameras_online: number;
  crit: number;
  warn: number;
  sensor_ok: boolean;
  is_primary: boolean;
};

export type Network = {
  id: string;
  slug: string;
  name: string;
  kind: string;
  subnet_prefix: string;
  expected_gateway: string;
  ssid: string;
  sensor_id: string | null;
  sensor_name: string;
  colour: string;
  is_primary: boolean;
  status: string;
  devices: number;
  online: number;
  unverified: number;
  cameras: number;
  created: string;
  // mirror|router|host|none -- set by a person in the wizard/Settings, never guessed.
  traffic_mode: string;
};

export type NetworksPayload = {
  networks: Network[];
};

export type RecorderChannel = {
  channel: number;
  name: string;
  online: boolean;
  model: string;
};

export type RecorderVendor = {
  key: string;
  label: string;
  supported: boolean;
};

export type RecorderRow = {
  id: string;
  name: string;
  vendor: string;
  vendor_label: string;
  supported: boolean;
  host: string;
  port: number;
  scheme: string;
  username: string;
  has_credentials: boolean;
  expected_channels: number;
  enabled: boolean;
  status: string;
  error_kind: string;
  problem: string;
  owner_action: string;
  model: string;
  firmware: string;
  channel_count: number;
  channels_online: number;
  channels: RecorderChannel[];
  network_id: string;
  network_slug: string;
  network_name: string;
  last_ok: string;
  last_attempt: string;
  created: string;
};

export type RecordersPayload = {
  recorders: RecorderRow[];
  vendors: RecorderVendor[];
};

export type TrendPoint = {
  ts: string;
  value: number | null;
};

export type Series = {
  name: string;
  points: TrendPoint[];
  color: string | null;
};

export type HourBlock = {
  hour: number;
  state: string;
};

export type NextAction = {
  label: string;
  method: string;
  path: string;
  body: Record<string, unknown>;
};

export type Deduction = {
  category: string;
  points: number;
  why: string;
  /** The matching `SecurityCheck.key` this deduction is scored from, so a
   *  failing posture card can open the exact same detail drawer as the
   *  Wi-Fi guard / Checks rows. "" for the handful of checks that score the
   *  card but have no row of their own in `checks()` today. */
  key?: string;
};

/** Why the security score moved, for the one line under the score hero.
 *  Null when no scan is old enough to compare against — a delta of zero that
 *  nobody measured is not a fact, so nothing is rendered rather than "no
 *  change". */
/** One finding that is still there but has moved — the same category scoring
 *  a different amount, e.g. unverified devices going from four to three.
 *  Without this, a count changing reads as one finding appearing and a
 *  different one disappearing, which is the opposite of an explanation. */
export type DeductionShift = {
  category: string;
  /** Points deducted then and now. Fewer points is better. */
  was_points: number;
  now_points: number;
  was_why: string;
  now_why: string;
  key: string;
};

export type ScoreChange = {
  /** Current total minus the earlier total. Negative is worse. */
  delta: number;
  /** The earlier total, so the line can read "83, was 87". */
  was: number;
  /** `Scan.ts` of the scan compared against. */
  since: string;
  hours: number;
  /** Deductions present now and absent then, heaviest first. */
  appeared: Deduction[];
  /** Deductions present then and absent now, heaviest first. */
  cleared: Deduction[];
  /** Findings present in both, scoring differently — reported as a move
   *  rather than as an appearance plus a disappearance. */
  changed: DeductionShift[];
  /** The whole thing as one sentence naming the findings that moved. */
  summary: string;
  /** When the day is unchanged: the most recent time the score did move, so
   *  the line still explains something. Null otherwise. */
  last_moved: ScoreChange | null;
};

export type TimelineItem = {
  ts: string;
  kind: string;
  title: string;
  detail: string;
  sev: Sev | null;
  entity: string;
  href: string | null;
};

export type NameCount = {
  name: string;
  count: number;
};

export type EventRow = {
  ts: string;
  type: string;
  entity: string;
  text: string;
  payload: Record<string, unknown>;
  network: NetworkRef | null;
};

export type QueueStatus = {
  id: string;
  path: string;
  status: string;
  body: Record<string, unknown>;
  result: Record<string, unknown> | null;
  ts: string;
};

/** One row of the sensor work queue — the Activity page's unit. `label`/
 *  `target` are already plain English; never render `kind`/raw path
 *  instead. `stuck` singles out a `running` row that has gone on far past
 *  what that kind of job normally takes. `retryable` is only meaningful
 *  on a `failed` row. */
export type JobRow = {
  id: string;
  kind: string;
  label: string;
  target: string;
  who: string;
  status: string;
  queued_at: string;
  finished_at: string | null;
  sensor_id: string | null;
  started_at: string | null;
  not_before: string | null;
  expires_at: string | null;
  wait_reason: string;
  waiting_s: number;
  running_s: number | null;
  duration_s: number | null;
  ok: boolean | null;
  out: string;
  why_failed: string;
  stuck: boolean;
  retryable: boolean;
  /** The scan a finished scan request opens: the first one the sensor sent
   *  after the request finished. Null when there is none yet. */
  scan_id?: string | null;
  /** Set on `kind: "report"` rows. */
  report_id?: string | null;
};

/** What one stored scan found, for the Activity drawer (`GET /scans/{id}`). */
export type ScanSummary = {
  id: string;
  ts: string;
  score: number | null;
  devices: number;
  sites_up: number;
  sites_total: number;
  hosts_reachable: number;
  hosts_total: number;
  alerts: number;
};

export type JobsPayload = {
  jobs: JobRow[];
};

export type HeatmapCell = {
  row: string;
  col: number;
  value: number;
};

export type InboxCounts = {
  incidents: number;
  alerts: number;
  recs: number;
  unread: number;
  /** Open alerts whose remedy is one a "yes" runs — a subset of `alerts`. */
  needs: number;
};

export type RowAction = {
  kind: string;
  label: string;
  method: string;
  path: string;
  body: Record<string, unknown>;
  tier: number;
};

/** One line of the confidence rubric's working — mirrors
 *  `schemas_dash.ConfidenceFactor`: a question Vault asked of rows it
 *  already stores, the answer those rows gave, and what that was worth to the
 *  0-100 score.
 *
 *  `weight` is signed and is often 0, because a factor that found nothing is
 *  still sent: the point of showing all six is that the absent ones are
 *  informative. A negative weight is a reason for caution, not an error, and
 *  neither it nor a zero may be styled as an alarm — see `InboxRowView`. */
export type ConfidenceFactor = {
  name: string;
  question: string;
  answer: string;
  weight: number;
};

/** What Vault can do about one alert — mirrors `schemas_dash.AlertRemedy`.
 *  `kind` drives the row: "auto" means the row also carries a `fix` action
 *  that runs the repair now; "approval" and "human" both hand off to chat,
 *  where the opening message already explains what is needed. */
export type AlertRemedy = {
  kind: string;
  action: string;
  host: string;
  arg: string;
  tier: number;
  tool: string;
  min_role: string;
  can_run_now: boolean;
  summary: string;
  need: string;
  /** What running it will COST — what breaks, for how long, and for whom.
   *  `summary` says what Vault can do and `need` what it wants from you;
   *  this is the thing being weighed. "" when the action has no entry in
   *  `remedy.FIX_IMPACT`, and an empty one renders as nothing rather than as
   *  reassurance. */
  impact: string;
  opening: string;
  /** How sure Vault is that it should make this repair itself, 0-100, and
   *  the sentence explaining that number. A rubric with weights a person
   *  chose (`hub/guardian_hub/confidence.py`) — nothing is fitted, and the UI
   *  must say so rather than let a reader take it for a probability.
   *
   *  NOT the same quantity as `InboxRow.confidence`, which is the SENSOR's
   *  confidence that the finding is real and which the row already prints as
   *  "High / Moderate / Limited evidence". Two numbers called confidence on
   *  one row is the confusion the drawer exists to avoid: this one is shown
   *  only where it can carry its own label and its working. */
  confidence: number;
  confidence_why: string;
  /** All six factors the score was built from, including the ones that found
   *  nothing (`weight: 0`). Empty when the row has no scored remedy, or when
   *  hub-side scoring fell open — render nothing at all in that case, never
   *  an empty table, which would read as "Vault considered nothing". */
  confidence_factors: ConfidenceFactor[];
};

/** Task 83, item 5: the honest version of "show the diff" on a `sec:drift:`
 *  alert — mirrors `schemas_dash.DriftDetail`. There is no line diff:
 *  Vault keeps only a fingerprint of the file, never its content or a
 *  prior fingerprint (see `guardian_hub/drift.py`), so `note` says that
 *  plainly rather than the UI showing a blank panel a reader could
 *  mistake for "nothing changed". */
export type DriftDetail = {
  host: string;
  file: string;
  changed_at: string;
  fingerprint: string;
  why: string;
  note: string;
};

export type InboxRow = {
  id: string;
  view: string;
  sev: Sev;
  title: string;
  entity: string;
  why: string;
  what_changed: string;
  status: string;
  first_seen: string;
  last_seen: string;
  count: number;
  actions: RowAction[];
  children: InboxRow[];
  ticket_key: string;
  confidence: number | null;
  network: NetworkRef | null;
  remedy: AlertRemedy | null;
  drift: DriftDetail | null;
};

export type InboxPayload = {
  counts: InboxCounts;
  rows: InboxRow[];
};

/** The three-way outcome axis (task 83, pattern A6). `handled` is a
 *  trailing-window count of `Fix` rows Vault ran itself, without a
 *  person clicking anything — distinct from `needs_yes`/`needs_you`, which
 *  partition every *currently open* alert (incident children included) and
 *  every open recommendation. */
export type TriageOutcomes = {
  handled: number;
  handled_period: string;
  needs_yes: number;
  needs_you: number;
};

/** One row of the prioritised inbox — a singleton alert, a cause-rolled
 *  group, an incident, or a recommendation. `alerts` always carries every
 *  underlying `InboxRow` this item folds in, so a roll-up never hides one.
 *  `rank_reason` is rendered next to `rank_score` — a score with no
 *  explanation is worse than no score. `actions` is role-filtered exactly
 *  like `InboxRow.actions`; when it is empty but `primary_min_role` is set,
 *  the row still names the one action and role it would take, so a viewer
 *  can render a disabled control with a real `title` instead of nothing. */
export type TriageItem = {
  kind: string; // alert|group|incident|rec
  id: string;
  view: string;
  sev: Sev;
  title: string;
  entity: string;
  why: string;
  since_text: string;
  count: number;
  confidence: number | null;
  cause: string;
  rank_score: number;
  rank_reason: string;
  remedy: AlertRemedy | null;
  actions: RowAction[];
  primary_label: string;
  primary_min_role: string;
  alerts: InboxRow[];
  recommendations?: InboxRow[];
};

export type InboxTriagePayload = {
  lead: TriageItem | null;
  rest: TriageItem[];
  outcomes: TriageOutcomes;
  total_open: number;
};

export type HealthStrip = {
  kind: string;
  name: string;
  ok: boolean | null;
  blocks: HourBlock[];
};

export type QuestionItem = {
  question: string;
  answer: string;
  state: string;
};

export type AlertCounts = {
  crit: number;
  warn: number;
  info: number;
  open: number;
  acked: number;
  snoozed: number;
};

export type DeviceCounts = {
  total: number;
  online: number;
  sleeping: number;
  unverified: number;
  quarantined: number;
};

export type SensorStatus = {
  name: string;
  status: string;
  last_seen: string | null;
  age_s: number | null;
  platform: string;
  version: string;
};

export type AssetCounts = {
  /** The sensor's own asset census when it reported one, else devices +
   *  servers + sites the hub knows about. */
  total: number;
  servers: number;
  sites: number;
};

export type IncidentCounts = {
  open: number;
  crit: number;
  warn: number;
};

export type IncidentBrief = {
  id: string;
  title: string;
  sev: Sev;
  entity: string;
  opened: string;
  alerts: number;
  href: string;
};

export type RecCounts = {
  open: number;
  critical: number;
  high: number;
  /** The highest-priority open recommendation's own text; "" when none. */
  top: string;
};

export type FeedState = {
  name: string;
  ok: boolean;
};

export type GuardianHealth = {
  /** online|silent|unknown */
  sensor_state: string;
  run_seconds: number | null;
  scans_last_hour: number | null;
  sensors_active: number;
  sensors_total: number;
  feeds: FeedState[];
  last_event_ts: string;
  last_event_age_s: number | null;
  autofix_enabled: boolean;
  autofix_24h: number;
  /** "" when no notification channel is configured. */
  notify_channel: string;
  /** on|down|off */
  dns_filter_state: string;
  dns_blocked_24h: number;
};

export type PathHealth = {
  name: string;
  ip: string;
  avg_ms: number | null;
  loss_pct: number | null;
  jitter_ms: number | null;
  /** good|warn|bad */
  state: string;
};

/** How much of the estate the score could actually measure. `applicable` is
 *  what Vault wanted to look at, `measured` is what answered; the gap is
 *  the part of the score that is not evidence. No hosts configured reads as
 *  100%, not 0% -- nothing to be blind about. */
export type ScoreCoverage = {
  measured: number;
  applicable: number;
  pct: number;
};

export type NeedsYouItem = {
  key: string;
  title: string;
  /** What Vault would do. */
  detail: string;
  /** What saying yes costs — a reboot, a dropped session. */
  cost: string;
  severity: string;
  action: string;
  host: string;
};

export type FixDay = {
  day: string;
  automatic: number;
  approved: number;
};

export type FixedWithoutYou = {
  days: number;
  automatic: number;
  approved: number;
  daily: FixDay[];
};
/** One class of asset: how many the sensor is still reporting, of how many
 *  Vault knows about. Not what exists — what is currently seen. */
export type CoverageRow = {
  label: string;
  seen: number;
  total: number;
};

/** One certificate inside the renewal window. */
export type CertExpiry = {
  host: string;
  issuer: string;
  days_left: number | null;
};

export type OverviewPayload = {
  ts: string | null;
  age_s: number | null;
  score: number | null;
  grade: string;
  potential: number | null;
  /** Rubric that produced `score`, stamped at sweep time. "" for scans older
   *  than the stamp -- never back-filled with today's version. */
  score_model: string;
  /** null when the sensor sent none, rather than a fabricated 100%. */
  score_coverage: ScoreCoverage | null;
  verdict: string;
  next_action: NextAction | null;
  deductions: Deduction[];
  /** Why the score moved since yesterday; null when nothing comparable. */
  score_change: ScoreChange | null;
  strip: HealthStrip[];
  timeline: TimelineItem[];
  score_trend: Series;
  questions: QuestionItem[];
  alerts: AlertCounts;
  devices: DeviceCounts;
  sensor: SensorStatus | null;
  networks: NetworkHealth[];
  /** One real IP off this tenant's network, used as the example in the chat
   *  composer's placeholder. Empty when no device has an IP yet. */
  chat_example_ip: string;
  /** Whether this hub can hear and speak at all. False hides every voice
   *  control — the mic in the composer, the chat speaker toggle and the
   *  wallboard's — rather than disabling one. */
  voice: boolean;
  /** The 7-day score window the hero panel's sparkline draws, alongside the
   *  30-day `score_trend` the trend card shows. */
  score_trend_7d: Series;
  assets: AssetCounts;
  /** Alerts opened, alerts resolved and fixes applied in the last 24 hours. */
  changed_24h: number;
  incidents: IncidentCounts;
  incident_briefs: IncidentBrief[];
  recs: RecCounts;
  health: GuardianHealth;
  paths: PathHealth[];
  cameras: CameraCensus;
  needs_you: NeedsYouItem[];
  fixed_without_you: FixedWithoutYou;
  coverage: CoverageRow[];
  certs_expiring: CertExpiry[];
};

/** One recorder Vault cannot fully account for — mirrors
 *  `schemas_dash.RecorderGap`. Named so the census can say WHICH cameras it
 *  is inferring rather than reporting a smaller, directly-observed number
 *  as if it were the whole truth. */
export type RecorderGap = {
  name: string;
  expected: number;
  known: number;
  reason: string;
  /** The recorder's own synthesized camera network, or "" when the recorder
   *  has never been read successfully (no network yet) -- a gap with no
   *  network id has nowhere honest to be drawn on the network map. */
  network_id: string;
};

/** Overview's camera census row (Task 83, pattern A5) — mirrors
 *  `schemas_dash.CameraCensus`. `known` is every camera Vault has
 *  actually seen answer; `expected` adds what a recorder's own
 *  `expected_channels` says should be behind it but has not been listed
 *  for, named in `gaps`. */
export type CameraCensus = {
  known: number;
  online: number;
  expected: number;
  unobserved?: number;
  offline?: number;
  exposure_known?: boolean;
  exposed: boolean;
  exposed_ports: number[];
  gaps: RecorderGap[];
};

export type LiveSite = {
  name: string;
  url: string;
  code: number | null;
  ms: number | null;
  ok: boolean | null;
  /** Last few `latency_ms` readings, oldest first -- the header's per-site
   *  sparkline (Task 83). Empty until the hub has at least one scan's worth
   *  of history for this site. */
  spark: number[];
};

// The frame types the hub's `WS /api/v1/ws` sends per spec §8. `lib/live.ts`
// validates every incoming frame's `type` against exactly this set before
// dispatching it — an unrecognized value (a future frame type this build
// predates, or a malformed/hostile payload) is dropped rather than handed to
// a listener keyed on a type it never declared.
// "billing": the owner changed this account's plan or trial from the console;
// the customer's Billing page refetches rather than showing a stale plan.
export const LIVE_EVENT_TYPES = ["scan", "alert", "device", "fix", "dns", "flow", "upgrade", "queue", "billing", "ticket"] as const;

export type LiveEventType = (typeof LIVE_EVENT_TYPES)[number];

export type LiveEvent = {
  type: LiveEventType;
  ts: string;
  data: Record<string, unknown>;
};

export type LivePayload = {
  ts: string | null;
  sites: LiveSite[];
  scanning: boolean;
  sensor_age_s: number | null;
  load1: number | null;
  cores: number | null;
  maintenance_until: string | null;
};

export type SecurityHeaders = {
  hsts: boolean;
  nosniff: boolean;
  frame: boolean;
  csp: boolean;
  https_redirect: boolean;
  old_tls: boolean;
  server: string;
};

export type SiteCard = {
  name: string;
  url: string;
  ok: boolean | null;
  code: number | null;
  ms: number | null;
  /** Truncated to 2dp, never rounded up. null when there were too few
   *  checks in the window for two decimal places to mean anything. */
  uptime_24h: number | null;
  uptime_7d: number | null;
  /** How many checks each figure above came from, so 100%-over-21 and
   *  100%-over-2000 stop rendering identically. */
  uptime_24h_checks: number;
  uptime_7d_checks: number;
  /** Checks skipped because planned maintenance was running — excluded from
   *  numerator and denominator alike, and surfaced so the exclusion shows. */
  uptime_24h_planned: number;
  uptime_7d_planned: number;
  latency_spark: number[];
  cert_days: number | null;
  cert_expires: string;
  cert_issuer: string;
  dns_a: string[];
  headers: SecurityHeaders;
  incidents_7d: number;
  public: boolean;
  /** True when the sensor has stopped reporting this row entirely. The
   *  figures above are then a last-known state rather than a current
   *  reading, and the card says so instead of letting them look fresh. */
  stale: boolean;
  stale_note: string;
  last_checked: string;
};

// --- History: the trend charts, the scan log and the event log ------------

/** One trend panel. `series` arrives hourly-meaned and decimated — the page
 *  scales and draws, it never buckets. `max` pins the y-axis for a metric with
 *  a natural ceiling (a percentage) and is null when the axis fits the data. */
export type HistoryChart = {
  key: string;
  label: string;
  unit: string;
  max: number | null;
  series: Series[];
};

export type HistoryScanRow = {
  ts: string;
  sites_ok: number;
  sites_total: number;
  hosts_ok: number;
  hosts_total: number;
  devices: number | null;
  crit: number;
  warn: number;
  score: number | null;
};

export type HistoryEventRow = {
  ts: string;
  kind: string;
  entity: string;
  detail: string;
  href: string;
  network: NetworkRef | null;
};

export type HistoryPayload = {
  /** The window asked for, as asked for: a `days=` read reports `days=<n>,
   *  hours=0`; a sub-day `hours=` read reports `days=0, hours=<n>`. */
  days: number;
  hours: number;
  network: NetworkRef | null;
  scan_count: number;
  first_ts: string | null;
  last_ts: string | null;
  charts: HistoryChart[];
  scans: HistoryScanRow[];
  events: HistoryEventRow[];
};

export type SitesPayload = {
  sites: SiteCard[];
  /** Latency / certificate days left / availability per site over the hub's
   *  own 7-day window, in the same shape the History tab draws. */
  charts: HistoryChart[];
};

export type HardeningCheck = {
  name: string;
  passed: boolean;
  detail: string;
};

export type HardeningGrade = {
  score: number;
  grade: string;
  checks: HardeningCheck[];
};

export type ContainerRow = {
  name: string;
  status: string;
  image: string;
};

export type ServiceRow = {
  name: string;
  state: string;
};

export type Gauge = {
  name: string;
  value: number | null;
  unit: string;
  max: number;
  state: string;
};

export type PortRow = {
  port: number;
  since: string;
  risky: boolean;
  service: string;
};

export type HostCard = {
  name: string;
  address: string;
  reachable: boolean | null;
  hostname: string;
  uptime: string;
  load1: number | null;
  load_per_core: number | null;
  disk_pct: number | null;
  mem_pct: number | null;
  failed_ssh_24h: number;
  banned: number;
  fail2ban: boolean | null;
  reboot_required: boolean;
  pending_updates: number;
  security_updates: number;
  cve_fixable: number;
  hardening: HardeningGrade;
  ports: PortRow[];
  docker: ContainerRow[];
  services: ServiceRow[];
  gauges: Gauge[];
  /** True when the sensor has stopped reporting this row entirely. The
   *  figures above are then a last-known state rather than a current
   *  reading, and the card says so instead of letting them look fresh. */
  stale: boolean;
  stale_note: string;
  last_checked: string;
};

export type HostsPayload = {
  hosts: HostCard[];
};

export type KindChip = {
  group: string;
  count: number;
  online: number;
  network_id: string | null;
};

export type DeviceCard = {
  mac: string;
  ip: string;
  name: string;
  label: string;
  vendor: string;
  kind: string;
  group: string;
  trusted: boolean;
  always_on: boolean;
  quarantined: boolean;
  parent: string | null;
  risk: number;
  presence: string;
  online: boolean;
  first_seen: string;
  last_seen: string;
  ports: PortRow[];
  unverified: boolean;
  randomized: boolean;
  network: NetworkRef | null;
  /** Percentage of the last 7 days' scans that saw this device — the same
   *  figure the Screen time page reports for the same mac and window. `null`
   *  means it was not measured for this shape (a drawer child card), not 0%. */
  online_7d: number | null;
  camera_reasons: string[];
};

export type DeviceGroup = {
  group: string;
  devices: DeviceCard[];
  network: NetworkRef | null;
};

/** The nine summary tiles the Devices view opens with. Numbers only — every
 *  label, unit and tone is the web app's, because `design/Stat` already carries
 *  them. The first tile ("Devices online") comes from `DevicesPayload.online`
 *  and `.total`, so it has no field here.
 *
 *  The speed, latency and DNS figures are the SENSOR HOST's own measurements,
 *  not per-network properties: with a network selected they come from that
 *  network's sensor's latest scan, with "All networks" from the tenant's. */
export type DeviceTiles = {
  new_devices: number;
  trusted: number;
  randomized: number;
  down_mbps: number | null;
  up_mbps: number | null;
  latency_ms: number | null;
  dns_ms: number | null;
  /** `null` = no sample. The tile is crit only for an explicit `false`. */
  dns_ok: boolean | null;
  /** Empty when the selection spans more than one address — `wan_ips` then
   *  carries them all and the tile shows the count instead. */
  wan_ip: string;
  wan_ips: string[];
};

export type DevicesPayload = {
  groups: DeviceGroup[];
  chips: KindChip[];
  total: number;
  online: number;
  unverified: number;
  cameras: number;
  cameras_online: number;
  tiles: DeviceTiles;
  networks: NetworkHealth[];
  /** Devices online, joins and leaves, and online-vs-known over the hub's own
   *  7-day window, in the same shape the History tab draws. */
  charts: HistoryChart[];
};

export type DayHours = {
  day: string;
  hours: number[];
};

export type PresenceProfile = {
  online_pct_24h: number;
  online_pct_7d: number;
  hours: number[];
  typical_hours: number[];
  days: DayHours[];
  strip: HourBlock[];
};

export type DeviceDnsProfile = {
  queries_24h: number;
  blocked_24h: number;
  top: NameCount[];
  blocked_top: NameCount[];
};

/** Another MAC that carried this device's name — a randomized address
 *  rotating, most likely. A trusted sibling is the strongest sign a "new"
 *  MAC is a device the owner already knows. */
export type NameSibling = {
  mac: string;
  ip: string;
  trusted: boolean;
  first_seen: string;
  last_seen: string;
};

/** Is this device one of ours? Evidence with a name, never certainty. */
export type DeviceFamiliarity = {
  mac: string;
  name: string;
  label: string;
  ip: string;
  vendor: string;
  randomized_mac: boolean;
  trusted: boolean;
  /** resident | regular | visitor | unknown */
  verdict: string;
  summary: string;
  reasons: string[];
  days_seen: number;
  lookback_days: number;
  span_days: number;
  first_seen: string;
  last_seen: string;
  silent_days: number | null;
  /** Share of sightings between midnight and 6am, 0-1. */
  overnight_share: number;
  typical_hours: number[];
  joins: number;
  leaves: number;
  dns_queries: number;
  top_dns: string[];
  platform_hint: string;
  name_siblings: NameSibling[];
  other_networks: string[];
};

export type RiskReason = {
  points: number;
  why: string;
};

export type DeviceDetail = {
  device: DeviceCard;
  /** Why the risk score is what it is — the sensor's formula, line by line. */
  risk_reasons?: RiskReason[];
  timeline: TimelineItem[];
  presence: PresenceProfile;
  ports: PortRow[];
  dns: DeviceDnsProfile;
  alerts: InboxRow[];
  children: DeviceCard[];
  events: EventRow[];
  /** "Is this one of ours?" — assembled from the device's own history. */
  familiarity: DeviceFamiliarity;
};

export type ScreenTimeRow = {
  mac: string;
  name: string;
  ip: string;
  trusted: boolean;
  total_h: number;
  pct: number;
  hours_per_day: number;
  hours: number[];
  network: NetworkRef | null;
};

export type ScreenTimePayload = {
  days: number;
  scans: number;
  devices: ScreenTimeRow[];
  heatmap: HeatmapCell[];
};

export type Geo = {
  country: string;
  country_code: string;
  city: string;
  org: string;
  isp: string;
  lat: number | null;
  lon: number | null;
  hosting: boolean;
  reverse: string;
};

export type Scorecard = {
  target: string;
  kind: string;
  grade: string;
  score: number;
  deductions: Deduction[];
  fix: string;
};

/** The owner's own call on one failing check — "open" (nobody has decided
 *  anything yet) or "left" (seen, and deliberately left as-is). There is no
 *  "resolved" here: an autofix-backed check resolves itself the moment the
 *  next scan shows it passing. */
export type CheckDecision = {
  status: string; // open|left
  reason: string;
  decided_by: string;
  decided_at: string;
};

export type SecurityCheck = {
  key: string;
  area: string;
  title: string;
  passed: boolean | null;
  last_verified: string;
  evidence: string;
  autofix: string;
  target: string;
  /** This exact check's decision, so a row that was deliberately left reads
   *  as a deliberate decision rather than an unseen failure. */
  decision?: CheckDecision;
};

export type WifiGuard = {
  verdict: string;
  state: string;
  ssid: string;
  security: string;
  channel: number | null;
  phy: string;
  signal: number | null;
  evil_twin: boolean;
  twin_channels: number[];
  rogue_dhcp: boolean;
  dhcp_server: string;
  expected_gateway: string;
  /** The route actually in use and the MAC answering as the router — the two
   *  facts the "Rogue network checks" box shows beside the DHCP server. "" when
   *  this network's sensor has not reported them. */
  default_gateway: string;
  gateway_mac: string;
  unknown_devices: number;
  rows: SecurityCheck[];
  network: NetworkRef | null;
};

/** `GET /api/v1/security/checks/detail` — the drill-down for one failing
 *  Wi-Fi guard row, checklist row, or posture deduction: what Vault saw,
 *  in plain sentences; why it matters, specific to this check; how sure
 *  Vault is; and what it can do about it (`remedy`, the same shape the
 *  inbox already uses). */
export type EmailFinding = {
  /** bad | warn | info | good */
  tone: string;
  text: string;
};

export type EmailVerdictOut = {
  address: string;
  domain: string;
  /** forged | risky | unproven | unknown. Deliberately never "safe" — no
   *  DNS check can establish that, and a green tick on a well-run phishing
   *  domain is worse than no tool at all. */
  verdict: string;
  headline: string;
  findings: EmailFinding[];
  limits: string[];
  reputation_note: string;
};

export type FirewallRule = {
  id: string;
  key: string;
  action: string;
  proto: string;
  port: number;
  target: string;
  title: string;
  because: string;
  evidence: string;
  /** CRIT | WARN */
  risk: string;
  /** proposed | declined | applied | rolled_back | expired */
  status: string;
  decided_by: string;
  decided_at: string;
  reason: string;
  applied_at: string;
  rollback_at: string;
  first_seen: string;
  last_seen: string;
};

export type FirewallPayload = {
  rules: FirewallRule[];
  /** False today: Vault can propose a gateway rule and explain it, but
   *  writing one to somebody's router is access it does not have. */
  can_apply: boolean;
  why_not: string;
  gateway: string;
};

export type CheckDetail = {
  key: string;
  area: string;
  title: string;
  target: string;
  network_id: string;
  passed: boolean | null;
  last_verified: string;
  evidence: string;
  what_we_saw: string;
  why_it_matters: string;
  confidence: string; // confirmed|uncertain
  remedy: AlertRemedy;
  decision: CheckDecision;
  /** Item 25: what Vault ran about this, newest first. */
  repairs: RepairProof[];
};

export type RepairProof = {
  label: string;
  action: string;
  target: string;
  ran_at: string;
  by: string;
  by_person: boolean;
  /** The command reported success. NOT proof. */
  ran_ok: boolean;
  /** A later scan looked again and found it changed. THIS is the proof. */
  confirmed_at: string;
  proof: string;
  output: string;
};

export type Attacker = {
  ip: string;
  host: string;
  fails: number;
  days: number;
  first_seen: string;
  last_seen: string;
  geo: Geo | null;
  action: string;
  action_ts: string | null;
  alert_key: string;
  network: NetworkRef | null;
};

export type ExposureTile = {
  label: string;
  value: string;
  state: string;
  detail: string;
};

/** One config-defined probe beyond the HTTP site checks: a TCP connect, an
 *  ICMP ping or a DNS lookup declared in `config.checks`. `ok === null` means
 *  the sensor has not run it since the hub last saw a scan. */
export type ExtraCheck = {
  name: string;
  type: string;
  target: string;
  port: number | null;
  ok: boolean | null;
  latency_ms: number | null;
  detail: string;
};

/** A dead-man switch: a job that must call `/hb/<name>/<token>` at least
 *  every `every_min` minutes. `late` is the hub's verdict. */
export type HeartbeatRow = {
  name: string;
  every_min: number;
  last_ping: string;
  age_min: number | null;
  late: boolean;
  note: string;
};

/** The setup line for one heartbeat — `command` carries the heartbeat's own
 *  token, so it is fetched on demand from an admin-only, audited route and
 *  never stored in the Security payload (which is viewer-readable and gets
 *  printed into shareable reports). `rotated` means the previous command
 *  stopped working the moment this one was issued. */
export type HeartbeatSetup = {
  name: string;
  url: string;
  command: string;
  rotated: boolean;
};

/** Registrable-domain expiry from the sensor's daily whois. */
export type DomainRow = {
  domain: string;
  expires: string;
  days_left: number | null;
  error: string;
};

/** One monitored domain's anti-spoofing posture. `spf`/`dmarc` carry the
 *  records themselves ("" when absent) so the panel can show what is
 *  published, not only a tick. `passed` is the same verdict the Email security
 *  check makes: SPF present and DMARC enforcing. */
export type EmailAuthRow = {
  domain: string;
  spf: string;
  dkim_selector: string;
  dmarc: string;
  dmarc_policy: string;
  has_mx: boolean;
  passed: boolean;
  checked: string;
  /** Measured and shown, never chased — a domain the customer does not
   *  control gets no checklist row, no deduction and no nudge. */
  advisory: boolean;
};

/** What the outside world answers on for one site host. `unexpected` is
 *  `ports` minus the allowed set; `findings` names any admin path the probe
 *  actually matched on that host. */
export type ExtPortRow = {
  host: string;
  ip: string;
  ports: number[];
  unexpected: number[];
  findings: string[];
  probe_host: string;
  checked: string;
};

/** One repair Vault ran. `by` is "auto" for an unattended run and a
 *  username for a pressed button. */
export type FixLogRow = {
  ts: string;
  host: string;
  action: string;
  label: string;
  by: string;
  ok: boolean;
  detail: string;
};

/** One thing the manual scan saw. `fix` names the Vault remediation that
 *  clears it when there is one; deciding to run it is the owner's. */
export type VulnFinding = {
  /** critical | high | medium | low | info */
  severity: string;
  title: string;
  evidence: string;
  port: number | null;
  fix: string | null;
  cve: string | null;
  /** Set on LAN-wide scans: which device this came from. */
  target: string;
};

export type VulnPort = {
  port: number;
  service: string;
  banner: string;
  http: number | null;
  tls_version: string;
  tls_verified: boolean | null;
};

/** A queued or finished manual scan. */
export type VulnScanRow = {
  id: string;
  /** queued | running | done | failed */
  status: string;
  /** device | host | site | lan */
  kind: string;
  target: string;
  profile: string;
  who: string;
  queued_at: string;
  finished_at: string | null;
  duration_s: number | null;
  score: number | null;
  summary: Record<string, number>;
  findings: VulnFinding[];
  ports: VulnPort[];
  error: string;
};

export type VulnScanTarget = {
  kind: string;
  target: string;
  label: string;
};

export type VulnScansPayload = {
  scans: VulnScanRow[];
  targets: VulnScanTarget[];
};

export type DeviceDefence = {
  key: string;
  label: string;
  /** on | off | unknown — a check that could not run is not a failure. */
  state: string;
  buys: string;
};

export type ThreatsBlocked = {
  category: string;
  count: number;
};

export type BlockedLookup = {
  name: string;
  device: string;
  category: string;
  ts: string;
};

export type ThreatSignal = {
  key: string;
  title: string;
  severity: string;
};

/** One alert in a threat class, open or settled.
 *
 *  Distinct from `ThreatSignal`, which is the card's four-item teaser. This is
 *  the answer to "what did you find and what happened to it", so it carries
 *  the `why` and `evidence` behind the headline. */
export type ThreatFinding = {
  key: string;
  title: string;
  severity: string;
  why: string;
  evidence: Record<string, unknown>;
  status: string;
  /** True when the status is settled (resolved/snoozed/acked). Decided on the
   *  hub by the same constant the rest of the app uses, not re-derived here. */
  resolved: boolean;
  /** The repair that ran, "" when none did. A settled alert with no fix was
   *  settled by a person deciding it needed none — which must not read as
   *  Vault having repaired something. */
  fix: string;
  resolved_by: string;
  first_seen: string;
  last_seen: string;
  count: number;
};

/** `GET /api/v1/security/threats/{key}` — one attack class, opened up.
 *
 *  `limit` is carried through on purpose: for `dos` and `ransomware` the
 *  honest answer to "how did Vault solve it" is that it did not and cannot,
 *  and dropping that would turn an honest card into a misleading popup. */
export type ThreatDetail = {
  key: string;
  label: string;
  what: string;
  stance: string;
  does: string;
  limit: string;
  days: number;
  open_count: number;
  resolved_count: number;
  /** A DNS count over the window, NOT an alert count. Never add it to
   *  `findings.length` — the sum would mean nothing. */
  blocked: number;
  findings: ThreatFinding[];
};

export type ThreatClass = {
  key: string;
  label: string;
  what: string;
  /** blocking | watching | effect-only | indirect — the last two mean
   *  Vault does not stop this, and they say so on purpose. */
  stance: string;
  does: string;
  limit: string;
  open_count: number;
  blocked: number;
  signals: ThreatSignal[];
};

export type ProtectionPanel = {
  defences: DeviceDefence[];
  defences_host: string;
  blocked: ThreatsBlocked[];
  blocked_total: number;
  recent: BlockedLookup[];
  filtering: boolean;
  days: number;
  threats: ThreatClass[];
};

export type PostureCoverage = {
  scored: number;
  total: number;
  unscored: string[];
};

export type SecurityPayload = {
  scorecards: Scorecard[];
  posture_coverage: PostureCoverage;
  wifi: WifiGuard;
  checks: SecurityCheck[];
  attackers: Attacker[];
  exposure: ExposureTile[];
  attacker_method: string;
  methods: string[];
  networks: NetworkHealth[];
  wifis: WifiGuard[];
  extra_checks: ExtraCheck[];
  heartbeats: HeartbeatRow[];
  domains: DomainRow[];
  /** Task 77: email anti-spoofing per domain, what the internet answers on per
   *  site host, and the log of every repair Vault has run. */
  email: EmailAuthRow[];
  ext_ports: ExtPortRow[];
  fix_log: FixLogRow[];
  protection: ProtectionPanel;
};

export type PatchDay = {
  day: string;
  host: string;
  patches: number;
  reboots: number;
  state: string;
};

export type UpdateHost = {
  /** False when the last sweep did not reach this server. Its counts are
   *  then zero because Vault has none — not because it is up to date. */
  checked: boolean;
  name: string;
  os: string;
  kernel: string;
  kernel_latest: string;
  pending: number;
  security_pending: number;
  cve_fixable: number;
  unattended: boolean | null;
  reboot_required: boolean;
  last_apt: string;
  /** What is waiting, by name. Capped hub-side (`payloads/updates.
   *  MAX_PACKAGES`), so `pending` is the true count and a shorter list means
   *  it was trimmed. `security_packages` is the subset apt marks security. */
  packages: string[];
  security_packages: string[];
};

export type CveRow = {
  cve: string;
  package: string;
  urgency: string;
  host: string;
  fixable: boolean;
  fixed_on: string;
  tracker: string;
};

export type UpgradeProgress = {
  queue_id: string;
  host: string;
  action: string;
  status: string;
  done: number;
  total: number;
  log_tail: string;
  started: string | null;
  finished: string | null;
};

export type RebootCandidate = {
  host: string;
  reason: string;
  services: string[];
  queued: boolean;
};

export type RebootWindow = {
  label: string;
  start: string;
  end: string;
};

export type RebootPlan = {
  hosts: RebootCandidate[];
  windows: RebootWindow[];
};

/** The one-shot confirmation a disruptive (tier-3) fix needs before it runs:
 *  `POST /api/v1/fix/confirm` mints it, the page shows `summary`, and
 *  `POST /api/v1/fix` burns `token`. Bound to one tenant, one user and one
 *  exact (host, action, arg). */
export type FixConfirmation = {
  token: string;
  expires: string;
  tier: number;
  host: string;
  action: string;
  summary: string;
};

/** The sensor host's OWN pending updates — the reference console's "This Mac"
 *  panel. `reported` is false when the sensor has never sent the block, which
 *  is not the same claim as "up to date". Vault cannot install these. */
export type MacUpdates = {
  os: string;
  available: string[];
  checked: string;
  error: string;
  reported: boolean;
};

export type PatchPosture = {
  pending: number;
  security_pending: number;
  needs_reboot: number;
  servers_checked: number;
  servers_unchecked: number;
  /** Devices Vault can see but cannot read a package list from — a
   *  phone, a camera, a thermostat. Counted, never silently skipped. */
  unmeasured: number;
};

export type UpdatesPayload = {
  calendar: PatchDay[];
  hosts: UpdateHost[];
  cves: CveRow[];
  progress: UpgradeProgress[];
  reboot_plan: RebootPlan;
  mac: MacUpdates;
  posture: PatchPosture;
};

export type DnsStatus = {
  enabled: boolean;
  running: boolean;
  queries_24h: number;
  blocked_24h: number;
  block_pct: number;
  cache: number;
  upstream_ms_avg: number | null;
  total_blocked_domains: number;
  family_filter: boolean;
  uptime_s: number;
  ts: string | null;
  /** How much of the network the figures above describe. They come from the
   *  resolver's own logs, so a device that never asks Vault contributes
   *  nothing and is invisible — including, most likely, the one doing the
   *  lookups you would most want to see. */
  clients_using: number;
  devices_total: number;
  /** 0 when there are no devices, never 100: an empty network is unmeasured,
   *  not fully covered. */
  coverage_pct: number;
};

export type DnsClient = {
  mac: string | null;
  ip: string;
  name: string;
  queries: number;
  blocked: number;
  network: NetworkRef | null;
};

export type DnsRules = {
  allow: string[];
  block: string[];
};

export type DnsList = {
  name: string;
  category: string;
  entries: number;
  blocked_24h: number;
  enabled: boolean;
};

export type DnsWizard = {
  resolver_ip: string;
  router_hint: string;
  working: boolean;
  clients_using: number;
  checked_at: string | null;
};

export type DnsStreamRow = {
  ts: string;
  mac: string | null;
  client: string;
  name: string;
  qtype: string;
  blocked: boolean;
  list: string | null;
  ms: number;
  device: string;
  network: NetworkRef | null;
};

/** One row on the Live connections tab -- the `flow` live frame. Never more
 *  than a stored Flow row holds: no packet contents, ever. `peer` is the TLS
 *  server name when the sensor saw one, else the resolved name, else the IP. */
export type ConnStreamRow = {
  ts: string;
  mac: string;
  ip: string;
  device: string;
  peer: string;
  peer_ip: string;
  peer_port: number;
  proto: string;
  bytes_in: number;
  bytes_out: number;
};

/** One place a device talked to (`GET /traffic/connections`). */
export type ConnectionDest = {
  host: string;
  peer_ip: string;
  port: number;
  proto: string;
  /** "Anthropic", or the host/address when Vault does not recognise it. */
  name: string;
  /** "AI service"; "" when unknown. */
  what: string;
  /** True only when the hostname itself says whose it is. */
  sure: boolean;
  /** "Encrypted web", "Clock sync", ... */
  port_meaning: string;
  local: boolean;
  bytes_in: number;
  bytes_out: number;
  first_seen: string;
  last_seen: string;
  is_new: boolean;
};

export type ConnectionDevice = {
  mac: string;
  name: string;
  ip: string;
  bytes_in: number;
  bytes_out: number;
  last_seen: string;
  destinations: ConnectionDest[];
};

export type ConnectionsPayload = {
  mode: string;
  why_none: string;
  window_minutes: number;
  devices: ConnectionDevice[];
  destinations_total: number;
  new_total: number;
};

export type DnsPayload = {
  status: DnsStatus;
  top: NameCount[];
  blocked_top: NameCount[];
  clients: DnsClient[];
  rules: DnsRules;
  lists: DnsList[];
  beaconing: NameCount[];
  suspicious: NameCount[];
  wizard: DnsWizard;
};

export type TrafficPeer = {
  /** DNS name when known, else IP. */
  peer: string;
  peer_ip: string;
  peer_port: number;
  proto: string;
  bytes_in: number;
  bytes_out: number;
  first_seen: string;
  last_seen: string;
};

export type TrafficTalker = {
  mac: string;
  /** label or name or vendor or mac -- display only. */
  name: string;
  ip: string;
  bytes_in: number;
  bytes_out: number;
  top_peer: string;
};

export type TrafficOverview = {
  /** mirror | router | none. */
  mode: string;
  /** Item 20: why there is nothing to show. Empty means Vault IS
   *  watching and the window really was quiet — a useful answer. Non-empty
   *  means it was never asked to look, and "nothing unusual" would be a lie
   *  of omission. */
  why_none: string;
  truncated: boolean;
  window_hours: number;
  top_talkers: TrafficTalker[];
  top_peers: TrafficPeer[];
  new_peers: TrafficPeer[];
  external_share: number;
};

export type DeviceTraffic = {
  mode: string;
  mac: string;
  window_hours: number;
  bytes_in: number;
  bytes_out: number;
  peers: TrafficPeer[];
  /** Bytes per hour, oldest first, length always window_hours -- a strip,
   *  not a trend; trends are computed only in payloads/history.py. */
  hourly: number[];
};

export type TopoNode = {
  id: string;
  kind: string;
  group: string;
  label: string;
  ip: string;
  mac: string | null;
  icon: string;
  state: string;
  risk: number;
  ring: number;
  trusted: boolean;
  quarantined: boolean;
  pulse: boolean;
  detail: string;
  network: NetworkRef | null;
  /** Worst OPEN alert on this node (CRIT|WARN|null) — colours the link to it. */
  alert_sev?: string | null;
  /** Keys of those open alerts — the inbox rows a coloured link opens. */
  alert_keys?: string[];
};

export type TopoLink = {
  source: string;
  target: string;
  kind: string;
  pulse: boolean;
};

export type TopoCluster = {
  id: string;
  label: string;
  members: string[];
  network_id: string | null;
};

export type TopologyPayload = {
  nodes: TopoNode[];
  links: TopoLink[];
  clusters: TopoCluster[];
  networks: NetworkHealth[];
};

// --- Incident detail (Task 83, patterns A1/A2) ------------------------------

/** The incident's written story, always in this order — see
 *  `guardian_hub/payloads/incidents.narrative()`. A section is "" when
 *  Vault has nothing real to say; render nothing for it rather than a
 *  placeholder. */
export type IncidentNarrative = {
  what_happened: string;
  what_it_means: string;
  confidence: number | null;
  confidence_basis: string;
  what_next: string;
  need: string;
};

export type IncidentTimelineItem = {
  ts: string;
  tone: string;
  text: string;
};

export type IncidentAsset = {
  id: string;
  kind: string;
  name: string;
  subtitle: string;
  risk: number | null;
  href: string;
};

export type IncidentEvidenceRow = {
  ts: string;
  where: string;
  what: string;
  seen: number;
};

export type IncidentGraph = {
  nodes: TopoNode[];
  links: TopoLink[];
};

export type IncidentDetail = {
  id: string;
  title: string;
  sev: Sev;
  status: string;
  entity: string;
  opened: string;
  closed: string | null;
  alert_count: number;
  narrative: IncidentNarrative;
  timeline: IncidentTimelineItem[];
  assets: IncidentAsset[];
  graph: IncidentGraph;
  evidence: IncidentEvidenceRow[];
  remedy: AlertRemedy | null;
  actions: RowAction[];
  primary_key: string;
};

export type ReportCard = {
  id: string;
  kind: string;
  period_days: number;
  preset: string;
  title: string;
  created: string;
  score: number | null;
  url: string;
  share_url: string | null;
  share_expires: string | null;
};

export type ReportPreset = {
  key: string;
  label: string;
  sections: string[];
};

export type ReportSection = {
  key: string;
  title: string;
};

export type ReportSchedule = {
  weekly: boolean;
  monthly: boolean;
  preset: string;
  ntfy: boolean;
  hour: number;
};

export type ValueStat = {
  key: string;
  label: string;
  value: string;
  /** Where the weaker, true reading of the number lives. Never decoration. */
  note: string;
};

export type WorkRow = {
  label: string;
  count: number;
};

export type ValueStrip = {
  days: number;
  stats: ValueStat[];
  work: WorkRow[];
};

export type ReportsPayload = {
  library: ReportCard[];
  presets: ReportPreset[];
  sections: ReportSection[];
  schedule: ReportSchedule;
  value: ValueStrip;
};

export type ProfileSettings = {
  owner: string;
  email: string;
  address: string;
  company: string;
};

export type UserRow = {
  username: string;
  display_name: string;
  email: string;
  role: string;
  is_superadmin: boolean;
  last_seen: string | null;
};

export type SiteTarget = {
  name: string;
  url: string;
  expect: number[];
  public: boolean;
  host: string;
  /** Name of an `ssh_hosts` entry (see `HostTarget.name`) naming the box
   *  whose web server actually serves this site -- "" when unknown.
   *  Lets Vault's security-header fix find the right nginx host
   *  without ever guessing from the URL alone. */
  ssh_host: string;
};

export type HostTarget = {
  name: string;
  target: string;
  optional: boolean;
  expect_services: string[];
  expect_docker: string[];
};

export type MaintenanceWindow = {
  start: string;
  end: string;
};

export type AutoFixPolicy = {
  enabled: boolean;
  max_per_day: number;
  attacker_method: string;
  maintenance_window: MaintenanceWindow | null;
  cooldowns: Record<string, number>;
};

export type Appearance = {
  theme: string;
  accent: string;
};

export type SensorRow = {
  id: string;
  name: string;
  platform: string;
  version: string;
  status: string;
  last_seen: string | null;
  age_s: number | null;
};

export type TenantRow = {
  id: string;
  slug: string;
  name: string;
  brand: Record<string, unknown>;
  ntfy_topic: string;
  users: number;
  sensors: number;
  demo: boolean;
};

export type SettingsPayload = {
  profile: ProfileSettings;
  users: UserRow[];
  sites: SiteTarget[];
  hosts: HostTarget[];
  autofix: AutoFixPolicy;
  thresholds: Record<string, number>;
  appearance: Appearance;
  sensors: SensorRow[];
  tenants: TenantRow[];
  /** Things Vault has seen on this network and nobody is watching.
   *  Offers, never writes — see `routers/settings._suggested_sites`. */
  suggested_sites: SuggestedTarget[];
};

export type SuggestedTarget = {
  name: string;
  url: string;
  /** The evidence, in the customer's words. A suggestion without one is a
   *  guess, and this product does not guess. */
  why: string;
};

export type IntegrationField = {
  name: string;
  label: string;
  kind: string;
  value: string;
  required: boolean;
  options: string[];
};

export type IntegrationCard = {
  kind: string;
  title: string;
  help: string;
  enabled: boolean;
  configured: boolean;
  fields: IntegrationField[];
  routes: Record<string, boolean>;
  inbound_url: string | null;
  last_test: string | null;
  last_error: string;
  direction: string;
  /** What a person can say to this channel once connected. Only ever
   *  populated for a CONNECTED channel. */
  commands: string[];
};

export type AiProviderRung = {
  name: string;
  configured: boolean;
  note: string;
};

export type AiModelStatus = {
  model: string;
  role: string;
  in_pool: boolean;
  preferred: string;
  rpm: number;
  rpd: number | null;
  tpm: number;
  tpd: number | null;
  cooling_s: number;
};

/** How chat fails over: the provider ladder and the Groq model pool with
 *  limits and live cooldowns. Never carries a key. */
export type AiFailover = {
  ladder: AiProviderRung[];
  models: AiModelStatus[];
};

// --------------------------------------------------------------------------
// The owner's portal (admin.guarder.io) — platform-wide, superadmin only
// --------------------------------------------------------------------------

/** One customer, as the owner's ledger shows them. */
export type PlatformTenantRow = {
  id: string;
  slug: string;
  name: string;
  plan: string;
  plan_status: string;
  interval: string;
  /** True when the plan is priced per site. The hub does not know how many
   *  sites were bought — Stripe's quantity does — so `mrr_cents` is the
   *  single-site price and should be shown as a floor. */
  per_site: boolean;
  /** This customer's monthly recurring revenue, in cents. A yearly plan is
   *  counted at its monthly EQUIVALENT (a year costs ten months), not its
   *  sticker price. Zero for a trial, a lapsed account or an unpaid checkout. */
  mrr_cents: number;
  trial_ends: string;
  trial_days_left: number | null;
  /** False once a paused trial has stopped accepting sensor data. */
  ingesting: boolean;
  sensors_total: number;
  sensors_online: number;
  last_scan: string;
  devices: number;
  users: number;
  open_warn: number;
  open_crit: number;
  created: string;
};

export type PlatformTotals = {
  tenants: number;
  sensors_online: number;
  sensors_total: number;
  open_crit: number;
  open_warn: number;
  trials_ending: number;
  open_requests: number;
  /** Customer tickets still waiting on an answer. */
  open_tickets: number;
  paused: number;
  /** Monthly recurring revenue across every paying account, in cents. */
  mrr_cents: number;
  /** Accounts actually being charged: `active` or `past_due`. */
  paying: number;
  /** Charged, and the last attempt failed. */
  past_due: number;
  /** True when a paying customer is on a per-site plan, so the total is a
   *  floor. False means it is exact and the page says nothing. */
  mrr_is_floor: boolean;
};

/** `GET /api/v1/platform`. The only cross-tenant read in the product. */
/** One customer's own setup, from `GET /api/v1/platform/tenants/{tid}`.
 *  No credential appears here by construction — the hub's schema has no field
 *  for an integration secret, a sensor token or an enrol code. */
export type AccountNetwork = {
  id: string;
  name: string;
  slug: string;
  devices: number;
};
export type AccountSensor = {
  id: string;
  name: string;
  platform: string;
  version: string;
  status: string;
  last_seen: string;
};
export type AccountUser = {
  username: string;
  email: string;
  display_name: string;
  role: string;
  superadmin: boolean;
};
export type AccountIntegration = {
  kind: string;
  configured: boolean;
};
export type AccountAuditRow = {
  ts: string;
  user: string;
  role: string;
  action: string;
  target: string;
};
/** One customer's money, on the same terms the Billing roster computes: a
 *  yearly plan at its monthly equivalent, a per-site plan at one site. */
export type AccountBilling = {
  plan: string;
  plan_label: string;
  interval: string;
  status: string;
  mrr_cents: number;
  per_site: boolean;
  trial_days_left: number | null;
  /** Renewal date, or the last day of service when cancelling. */
  period_end: string;
  cancel_at_period_end: boolean;
  /** Opaque Stripe id, not a credential — it links the page to the invoices. */
  stripe_customer_id: string;
  /** Mode-aware link to this customer in the Stripe dashboard; "" without a
   *  customer. Charged plans change there: the console refuses to edit a
   *  subscription Stripe is billing and points here instead. */
  stripe_dashboard_url: string;
};

export type AccountPayload = {
  id: string;
  slug: string;
  name: string;
  created: string;
  networks: AccountNetwork[];
  devices_unassigned: number;
  sensors: AccountSensor[];
  users: AccountUser[];
  integrations: AccountIntegration[];
  recent: AccountAuditRow[];
  /** null when the tenant has no subscription row at all — an account made by
   *  hand before billing existed. A real state, shown as such, not as a zero. */
  billing: AccountBilling | null;
};

/** One day of settled work, split by who settled it. */
export type PlatformWorkDay = {
  day: string;
  auto: number;
  human: number;
};

/** `GET /api/v1/platform/work` — Vault's share of the work against yours. */
export type PlatformWorkPayload = {
  days: PlatformWorkDay[];
  auto_total: number;
  human_total: number;
};

export type PlatformTrend = {
  tenant_id: string;
  /** One value per day, oldest first, always `days` long. A quiet day is a
   *  real zero, not a gap. */
  points: number[];
  /** New alerts across the window. The sparkline has no axis, so without this
   *  a flat line at 40 a day and a flat line at nothing look identical. */
  total: number;
};

export type PlatformTrendsPayload = {
  days: number;
  /** A customer with no alerts in the window is absent, not a row of zeroes. */
  trends: PlatformTrend[];
};

export type AccountTrend = {
  /** The same chart object the customer's own History tab is drawn from. */
  chart: HistoryChart;
  /** What the shape says, in a sentence — this page is read aloud. */
  says: string;
  /** better | worse | steady, or empty when the series is too short to judge.
   *  Devices never carries one: more devices is not better and fewer is not
   *  worse, so no verdict is the correct answer there. */
  direction: string;
};

export type AccountTrendsPayload = {
  days: number;
  trends: AccountTrend[];
  /** Empty when nothing reached a verdict. Not filler. */
  headline: string;
};

export type PlatformAuditRow = {
  ts: string;
  user: string;
  role: string;
  action: string;
  target: string;
  tenant_id: string;
  tenant: string;
  /** True when the act was on the platform rather than on a customer. */
  platform: boolean;
};
export type PlatformAuditPayload = {
  rows: PlatformAuditRow[];
};

export type PlatformPayload = {
  totals: PlatformTotals;
  tenants: PlatformTenantRow[];
  mail_configured: boolean;
  billing_mode: string;
  /** False while the platform-wide pause is on — Vault is watching but
   *  repairing nothing without a person. */
  auto_heal: boolean;
  base_url: string;
};

export type PlatformSettingField = {
  key: string;
  label: string;
  help: string;
  /** int | text | choice | bool */
  kind: string;
  group: string;
  value: string;
  /** True when a stored value overrides the environment/code default. */
  overridden: boolean;
  default: string;
  minimum: number | null;
  maximum: number | null;
  choices: string[];
  updated: string;
  updated_by: string;
};

export type PlatformSecretRow = {
  key: string;
  label: string;
  /** Whether a credential is configured. The value itself never leaves the hub. */
  set: boolean;
};

export type PlatformSettingsPayload = {
  fields: PlatformSettingField[];
  secrets: PlatformSecretRow[];
  base_url: string;
  version: string;
};

export type TicketMessageRow = {
  /** customer | owner — never a role name: both parties administer something. */
  side: string;
  id: string;
  author: string;
  body: string;
  created: string;
  /** Owner view only: whether a notification for this message went out. */
  emailed: boolean;
};

export type TicketRow = {
  id: string;
  subject: string;
  /** open | answered | resolved */
  status: string;
  /** low | normal | high */
  priority: string;
  opened_by: string;
  created: string;
  last_message_at: string;
  last_author: string;
  messages: number;
  resolved_at: string;
  resolved_by: string;
  /** Owner queue only; blank on a customer's own list. */
  tenant_id: string;
  tenant_name: string;
  /** missed | finding | device | network | account | other */
  category: string;
  /** What the ticket is about. All three blank together. */
  context_kind: string;
  context_id: string;
  context_label: string;
};

export type TicketDetail = {
  ticket: TicketRow;
  messages: TicketMessageRow[];
};

export type TicketsPayload = {
  /** False when the hub has no SMTP host: replies are recorded here but
   *  no email leaves the building. */
  mail_works: boolean;
  tickets: TicketRow[];
  open_count: number;
  support_email: string;
  support_direct_email: string;
  support_reply_sla: string;
};

export type IntegrationsPayload = {
  integrations: IntegrationCard[];
  ai?: AiFailover | null;
};

export type ChatRequest = {
  message: string;
  thread: string | null;
  tab: string;
  confirm_token: string | null;
};

export type ToolStep = {
  tool: string;
  args: Record<string, unknown>;
  tier: number;
  /** ok | error | queued | denied | proposed */
  status: string;
  result: Record<string, unknown> | null;
  queue_id: string | null;
};

// Structured entity a chat bullet's leading name/IP resolves to, so the web
// can link "Router · 192.168.1.1 · online" to the device/host/site it names
// instead of guessing from the IP shape. `kind` names which route to build
// the link from (device ids are the device's MAC); both null means the
// bullet's subject does not match anything Vault knows, so it renders as
// plain text.
export type ChatEntity = {
  kind: "device" | "host" | "site" | null;
  id: string | null;
};

export type ChatBullet = {
  text: string;
  entity: ChatEntity;
};

export type ChatTurn = {
  role: string;
  text: string;
  tool_steps: ToolStep[];
};

export type ProposedAction = {
  tool: string;
  args: Record<string, unknown>;
  tier: number;
  effect: string;
  reversible: boolean;
  host: string;
};

export type ChatProposal = {
  token: string;
  summary: string;
  actions: ProposedAction[];
  expires: string;
};

/** `POST /api/v1/alerts/{key}/handoff` — the chat hand-off for one alert:
 *  Vault's opening message, the real tier-3 proposal when there is one to
 *  confirm, and the thread the opening was recorded on. */
export type AlertHandoff = {
  key: string;
  remedy: AlertRemedy;
  opening: string;
  proposal: ChatProposal | null;
  thread_id: string;
};

export type ChatResponse = {
  text: string;
  verdict: string;
  bullets: ChatBullet[];
  next: NextAction[];
  tool_steps: ToolStep[];
  proposal: ChatProposal | null;
  provider: string;
  thread_id: string;
};

export type AuditRow = {
  ts: string;
  user: string;
  role: string;
  action: string;
  target: string;
  ip: string;
  body: Record<string, unknown>;
};

export type AuditPayload = {
  rows: AuditRow[];
};

// The CMD-K palette's search results.
type SearchKind = "device" | "host" | "site" | "alert" | "route";
export type { SearchKind };

export type SearchResult = {
  kind: SearchKind;
  id: string;
  title: string;
  subtitle: string;
  href: string;
};

export type SearchPayload = {
  results: SearchResult[];
};

/* ----------------------------------------------------------- billing --- */
// Mirrors `schemas_dash.py`'s billing block. The hub sends both intervals'
// cents AND their formatted labels, so nothing in this app holds a Vault
// price or formats a currency: `plans.py` is the only place a price exists.

export type BillingPlanCard = {
  id: string;
  name: string;
  blurb: string;
  monthly_cents: number;
  yearly_cents: number;
  monthly_label: string;
  yearly_label: string;
  per_site: boolean;
  is_trial: boolean;
  current: boolean;
};

export type BillingSubscription = {
  plan: string;
  plan_name: string;
  interval: string;
  status: string;
  price_label: string;
  trial_end: string;
  trial_days_left: number | null;
  current_period_end: string;
  cancel_at_period_end: boolean;
  ingesting: boolean;
};

export type BillingPayload = {
  configured: boolean;
  /** True while this hub offers only the free trial: one card, and
   *  checkout/portal refused. The trial's length is unaffected. */
  trial_only?: boolean;
  currency: string;
  trial_days: number;
  yearly_months_charged: number;
  plans: BillingPlanCard[];
  subscription: BillingSubscription | null;
  notice: string;
  portal_available: boolean;
};

/* ------------------------------------------------- first-run wizard --- */

/** `GET/POST /api/v1/onboarding` — whether THIS user has been through the
 *  first-run wizard on THIS tenant, and which walkthrough they get.
 *
 *  `path` is `"setup"` when nothing has ever reported for this tenant (the
 *  installing walkthrough) and `"tour"` when the site is already reporting
 *  (the orienting one, which never asks a newcomer to install a sensor).
 *  `show` is the hub's own answer to "redirect to the wizard?", so the rule
 *  lives on the hub rather than being re-derived in the shell. */
/** One declared network and how far along it is.
 *
 *  `sensor` is four states rather than a boolean because each wants different
 *  advice: `none` install one, `pending` a code was minted and never redeemed,
 *  `active` bound and talking, `quiet` bound and stopped. A wizard that read
 *  `quiet` as `active` would wait forever for a scan that is not coming. */
export type OnboardingNetwork = {
  id: string;
  name: string;
  slug: string;
  /** none | pending | active | quiet */
  sensor: string;
  /** "" until the first scan lands. */
  first_scan_at: string;
  devices: number;
  /** Group name -> count, from the same classifier the Assets page uses. */
  by_group: Record<string, number>;
  /** off | working */
  dns: string;
};

/** The single next thing to do, or null when there is nothing left. One action
 *  rather than a list: letting the hub choose keeps "what comes next" from
 *  being re-derived here, where it could disagree with the rows. */
export type OnboardingNext = {
  network_id: string;
  /** declare_network | enrol_sensor | await_first_scan | offer_dns */
  action: string;
};

export type OnboardingState = {
  path: string;
  /** Still authoritative for the `tour` path, which is linear. NOT for
   *  `setup` — see `networks`/`next`, which are derived per request because a
   *  stored cursor cannot describe several networks at different stages. */
  step: string;
  completed: boolean;
  skipped: boolean;
  show: boolean;
  tenant_ready: boolean;
  site_named: boolean;
  networks: OnboardingNetwork[];
  next: OnboardingNext | null;
};
