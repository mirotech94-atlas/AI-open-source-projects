/**
 * The dashboard's only channel to ATLAS.
 *
 * Every response carries `as_of`, and every screen shows it. Nothing here
 * caches: a number without a fresh read behind it is exactly what this system
 * is built not to produce.
 */
// The brain view's row shape lives beside the layout that consumes it, so the
// pure module can be tested without pulling this file (and `fetch`) in.
import type { BrainRegion } from "./core/brain-layout";

export type { BrainRegion };

export type Health = "ok" | "degraded" | "failing" | "unknown";

/** Anything we do not recognise is an absence of knowledge, never a pass. */
export function normaliseHealth(value: string | null | undefined): Health {
  const v = (value ?? "").toLowerCase().trim();
  return v === "ok" || v === "degraded" || v === "failing" ? v : "unknown";
}

export interface Envelope {
  as_of: string;
}

export interface CheckSummary {
  name: string;
  health: string;
  /** Set aside from the module's colour: not set up, or deliberately paused.
   *  Still UNKNOWN underneath -- never OK. */
  state?: { kind: "not_set_up" | "paused"; label: string } | null;
  detail: string;
  observed_at: string | null;
  next_run_at: string | null;
  interval_s: number;
  /** The reading is older than three intervals plus ten minutes, so the
   *  health above is "unknown" -- the rule the spoken system check uses. */
  stale?: boolean;
  /** The reading's own number and unit, as the check wrote them: null when
   *  it gave none. Shown only when `health` says the reading counts -- an
   *  UNKNOWN reading may still carry one. Optional: an older server. */
  value?: number | null;
  unit?: string | null;
  /** = observed_at, under the history endpoint's name. */
  read_at?: string | null;
}

export interface VoiceTurn {
  id: number;
  at: string;
  transcript: string;
  answer: string;
  intent: string | null;
  ms: number | null;
}

/** One stored reading of a check (`/api/checks/{m}/{c}/history`). */
export interface HistoryPoint {
  read_at: string;
  value: number | null;
  unit: string | null;
  health: string;
}

export interface CheckHistory extends Envelope {
  module: string;
  check: string;
  days: number;
  interval_s: number | null;
  /** Readings further apart than this are a gap: the board's staleness rule. */
  stale_after_s: number | null;
  truncated: boolean;
  points: HistoryPoint[];
}

export interface HealthSample {
  at: string;
  counts: Record<Health, number>;
  set_aside: number;
}

export interface HealthHistory extends Envelope {
  days: number;
  step_hours: number;
  points: HealthSample[];
}

export type BreakerState = "closed" | "open" | "half-open";

export interface BreakerInfo {
  state: BreakerState;
  why: string | null;
  /** While open: when it may be asked again. */
  until: string | null;
  fails: number;
}

export interface Deployment {
  provider: string;
  model: string;
  /** Null for a deployment dropped at boot: it is in no chain. */
  breaker: BreakerInfo | null;
  needs: string[];
  /** Why the boot probe took it out of every chain, or null. */
  dropped: string | null;
}

export interface NeedChain {
  need: string;
  /** "reason" when this Need has no chain of its own and borrows REASON's. */
  borrowed_from: string | null;
  chain: Array<{ provider: string; model: string; breaker: BreakerInfo }>;
  dropped: Array<{ provider: string; model: string; why: string }>;
}

export interface AgentModels {
  name: string;
  need: string | null;
  pin: { model: string; usable: boolean; why: string | null } | null;
  /** What `think()` would ask first now. */
  next: { model: string; kind: "pin" | "chain" } | null;
  /** provider:model that answered its last recorded run; null if none. */
  last: string | null;
  last_at: string | null;
}

export interface Models extends Envelope {
  free_only: boolean;
  needs: NeedChain[];
  deployments: Deployment[];
  agents: AgentModels[];
}

export interface ModuleTile {
  module: string;
  title: string;
  health: string;
  checks: CheckSummary[];
  last_check_at: string | null;
  next_check_at: string | null;
  enabled: boolean;
  latest_finding: { id: number; title: string; severity: number; at: string } | null;
  disabled_reason?: string;
  /** "not set up" / "switched off" / "paused until ..." when every check is
   *  set aside: shown in words, grey, and left out of the attention count. */
  setup?: string | null;
}

export interface ActivityRow {
  kind: "scan" | "finding" | "action";
  at: string;
  module: string;
  subject: string;
  health: string | null;
  detail: string;
  body?: string;
  severity?: number;
  value: number | null;
  unit: string | null;
  id: number | null;
}

export interface StatusCounters extends Envelope {
  since: string;
  scans_today: number;
  issues_found: number;
  unknown_today: number;
  auto_fixed: number;
  waiting_on_you: number;
  /** Approved cards the executor holds right now; null when unread.
   *  Optional: an older server. */
  executing?: number | null;
  watch_only: boolean;
}

export interface ReportSummary {
  id: number;
  at: string;
  kind: string;
  title: string;
  delivered_via: string | null;
  excerpt: string;
}

export interface Overview extends Envelope {
  modules: ModuleTile[];
  pending_approvals: number;
  watch_only: boolean;
}

export interface Proposal {
  id: number;
  action: string;
  params: Record<string, unknown>;
  reason: string;
  tier: number;
  tier_reason: string;
  status: string;
  confidence: number | null;
  rollback_plan: string;
  evidence: Record<string, unknown>;
  created_at: string;
  expires_at: string;
  dry_run: string | null;
  dry_run_ok: boolean | null;
  dry_run_at: string | null;
  finding: {
    id: number; title: string; detail: string; check: string; health: string;
  } | null;
  judgment?: { p: number | null; by: string | null; reasons: string[] };
}

export interface JudgmentBin {
  lo: number; hi: number; n: number; predicted: number; observed: number;
}
export interface JudgmentServing {
  head: string; since: string; brier: number; n_train: number; n_test: number; id: number;
}
export interface JudgmentTournament {
  at: string; target: string; scores: Record<string, number | null>;
  winner: string | null; incumbent: string | null; changed: boolean;
  n_train: number; n_test: number; line: string;
}
export interface Judgment extends Envelope {
  line: string;
  brier: number | null;
  n_scored: number;
  bins: JudgmentBin[];
  serving: Record<string, JudgmentServing | null>;
  log: { total: number; approved: number; denied: number; expired: number;
         open: number; abstained: number };
  tournaments: JudgmentTournament[];
  cold_start: number;
  watch_only: boolean;
}

export interface Approvals extends Envelope {
  proposals: Proposal[];
  watch_only: boolean;
}

export interface Finding {
  id: number;
  at: string;
  check: string;
  module: string;
  severity: number;
  kind: string;
  health: string;
  title: string;
  detail: string;
  value: number | null;
  unit: string | null;
  evidence: Record<string, unknown>;
  observed_at: string;
  proposal: { id: number; status: string; action: string } | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  event: string;
  subject: string;
  payload: string;
  prev_digest: string;
  digest: string;
}

export interface ModuleDetail extends Envelope {
  module: {
    name: string; title: string; enabled: boolean;
    disabled_reason: string | null; brief: string;
  };
  checks: Array<{
    name: string; key: string; description: string; interval_s: number;
    /** The newest reading as it stands now, through the server's staleness
     *  rule: a stale OK arrives here as "unknown" with `stale: true`. */
    current: { health: string; stale: boolean; at: string | null; value: number | null };
    severity_on_fail: number; baseline_sigma: number | null; timeout_s: number;
    history: Array<{ at: string; value: number | null; health: string }>;
  }>;
  actions: Array<{
    name: string; tier: number; allowed: boolean; reason: string;
    summary: string; params: Record<string, string>; reversible: boolean;
    max_per_hour: number | null;
  }>;
  watch_only: boolean;
}

// Task 69: "lead" for concierge, "businesses" for every agent
// `businesses.BY_NAME` builds, "functions" for the rest -- derived
// server-side, never guessed here.
export type AgentGroup = "lead" | "businesses" | "functions";

export interface AgentSummary {
  name: string;
  title: string;
  // The redesigned map's own shortened name for this card ("Vault" for
  // guardian, "College" for school) -- `title` itself is unchanged, and
  // Fleet's cards, the org chart and DeepDive all still read that.
  display: string;
  brief: string;
  need: string | null;
  available: boolean;
  providers: string[];
  tools: number;
  last_run_at: string | null;
  last_outcome: string | null;
  runs_24h: number;
  // The provider:model that answered the LAST run, never the configured
  // pin as if it had run (Task 53). `model_at` is that run's time; both are
  // null until the agent has actually run once.
  model: string | null;
  model_at: string | null;
  handoff: string[];
  memory_scope: string[];
  // Task 69's readiness preflight: every tool it holds resolves and (for a
  // module-tied tool) that module is loaded, and its model route resolves.
  // `reasons` is empty exactly when `ready` is true.
  ready: boolean;
  reasons: string[];
  group: AgentGroup;
  // ISO timestamp of this agent's next scheduled shift, or null when its
  // cadence is off (`AGENT_SHIFT_<NAME>=0`) or it is not ready.
  next_shift_at: string | null;
  // Its rung on the autonomy ladder (`agents.ladder`), counted from the
  // tiers set in code. Optional so an older server still types.
  ladder?: AgentLadder | null;
  // Its person-like persona (`atlas/kernel/personas.py`) -- presentation
  // only. Optional so an older server, or a hand-built summary, still types.
  persona?: Persona;
}

export interface AgentLadder {
  watches: number;
  acts_alone: number;
  asks_first: number;
  never: number;
  unknown: number;
}

export type PersonaVoice = "female" | "male";

export interface Persona {
  agent: string;
  name: string;
  specialty: string;
  voice: PersonaVoice;
}

/** The voice `/api/voice/speak` is asked for: ATLAS's own, or one of the
 *  two persona voices. A closed set -- the server 400s anything else. */
export type SpeakVoice = "atlas" | PersonaVoice;

export interface MeetingLine {
  speaker: "atlas";
  voice: "atlas";
  text: string;
  read_at: string;
  /** The line as assembled from the record, before any model rewording.
   *  Optional so an older server still types. */
  plain?: string;
  /** A model reworded `text` and the grounding check passed. */
  polished?: boolean;
}

/** The day's theme (`kernel/meeting.py` THEMES): rotates by local date. */
export interface MeetingTheme {
  key: string;
  title: string;
  prompt: string;
}

/** One line of the conversation: a question (`reply_to` null) or its
 *  answer. ATLAS's follow-ups and agent-to-agent threads alike. */
export interface MeetingExchange {
  id: string;
  thread: number;
  kind: "follow_up" | "dependency" | "shared";
  /** "atlas" or a roster agent name. */
  speaker: string;
  speaker_name: string;
  voice: SpeakVoice;
  to: string;
  to_name: string;
  reply_to: string | null;
  text: string;
  plain: string;
  polished: boolean;
  read_at: string;
}

export interface MeetingTurn {
  agent: string;
  title: string;
  persona: Persona;
  voice: PersonaVoice;
  ready: boolean;
  status: "worked" | "idle" | "blocked";
  did: {
    runs_24h: number; shifts_24h: number; self_tests_24h: number;
    last_at: string | null; last_outcome: string | null; text: string;
  };
  issues: {
    items: string[]; failed_24h: number; last_failure_at: string | null;
    last_failure_kind?: string | null; text: string;
  };
  improve: { text: string };
  next: { at: string | null; text: string };
  /** ATLAS's question to this agent, from the day's theme. */
  question?: string;
  /** Its answer, from its record; `plain` is the record's own words. */
  answer?: { text: string; plain: string; polished: boolean };
  /** The whole turn as one spoken paragraph, under the speak cap. */
  speech: string;
  read_at: string;
}

export interface Meeting extends Envelope {
  read_at: string;
  window_hours: number;
  /** Local date the meeting is for (YYYY-MM-DD). */
  day?: string;
  theme?: MeetingTheme;
  /** When it was assembled: the daily step's build time for a stored
   *  meeting, the read time for a live one. */
  built_at?: string;
  /** "stored": the daily meeting as built this morning; "live": rebuilt
   *  from the records for this request. */
  source?: "stored" | "live";
  /** How many lines a model reworded (each one passed the grounding check). */
  polished?: number;
  exchanges?: MeetingExchange[];
  opening: MeetingLine;
  turns: MeetingTurn[];
  closing: MeetingLine & {
    counts: { agents: number; ready: number; worked: number; idle: number; blocked: number };
  };
}

/** A job handed to the agent that owns it (kernel/missions.py). */
// ------------------------------------------------------------ the Brain
// `GET /api/agency/brain` (atlas/kernel/brain.py, departments.py): the real
// agents, jobs and actions grouped into departments, each department's last
// 7 days from the record, and every seat's SOP generated from code. A null
// figure is unknown -- a read that failed, or nothing to count -- and is
// drawn as UNKNOWN, never as 0.

export type BrainLayer = "automation" | "agent" | "agentic";

export interface BrainRuns {
  pct: number | null;
  auto: number;
  approved: number;
  executions: number;
  excluded: number;
}

export interface BrainSeat {
  id: string;
  kind: "agent" | "job";
  name: string;
  label: string;
  title: string;
  layer: BrainLayer;
  /** Runs in the window; null = no per-run record (or the read failed). */
  ran_7d: number | null;
  recorded?: boolean;
  tier2?: number;
  actions?: number;
}

export interface BrainAction {
  id: string;
  name: string;
  tier: number;
  card_only: boolean;
  verifier: boolean;
}

export interface BrainPause { scope: string; says: string; shadow: boolean; department?: string | null }

export interface BrainDepartment {
  key: string;
  name: string;
  core: boolean;
  head: { agent: string | null; label: string; title: string; why: string };
  modules: string[];
  disabled_modules: { module: string; why: string }[];
  agents: BrainSeat[];
  jobs: BrainSeat[];
  actions: BrainAction[];
  tier2_actions: number;
  seats: {
    agents: number; agents_ran: number | null;
    jobs: number; jobs_recorded: number; jobs_ran: number | null;
  };
  runs_without_you: BrainRuns | null;
  waiting_on_you: number | null;
  verify: Record<string, number> | null;
  paused: BrainPause[] | null;
  layers: Partial<Record<BrainLayer, number>>;
}

export interface SopAction {
  name: string; tier: number; summary: string; card_only: boolean;
  verifier: boolean; topic: string; topic_depends_on: string | null;
  own_channel: boolean | null; department: string | null;
  principal_only: boolean; needs_you: boolean;
}

export interface SopStep {
  kind: "trigger" | "reads" | "decides" | "acts" | "signoff" | "result";
  text: string;
  actions?: SopAction[];
}

export interface Brain {
  read_at: string;
  since: string;
  window_days: number;
  watch_only: boolean;
  verify_after: boolean;
  served_at?: string;
  layers: { key: BrainLayer; label: string; meaning: string }[];
  whole: {
    runs_without_you: BrainRuns | null;
    waiting_on_you: number | null;
    paused: BrainPause[] | null;
    verify: Record<string, number> | null;
  };
  departments: BrainDepartment[];
  unmapped: { executions: number; actions: string[]; waiting_on_you: number | null };
  sops: Record<string, SopStep[]>;
  errors: Record<string, string>;
}

export interface Mission {
  id: number;
  agent: string;
  persona: string;
  recipe: string;
  request: string;
  status: string;            // needs_input | running | waiting | done | stopped | expired | superseded
  phase: string;
  slots: Record<string, unknown>;
  area: { label: string; source?: string; read_at?: string } | null;
  waiting: string | null;
  steps: number;
  cards: number[];
  report: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface AgentFeedItem {
  at: string;
  agent: string;
  task: string;
  outcome: string | null;
  turns: number;
  model: string | null;
  to: string | null;
}

export interface MemoryGraphNode {
  id: string;
  kind_counts: Record<string, number>;
  total: number;
}

export interface MemoryGraphEdge {
  from: string;
  to: string;
}

export interface MemoryGraphAgent {
  id: string;
  title: string;
}

/** One line of the recall log: what was searched, and what it found. */
export interface BrainRead {
  at: string | null;
  namespace: string;
  hits: number;
  agent: string | null;
}

export interface BriefSection {
  title: string;
  lines: string[];
  unavailable: string | null;
  empty: string;
}

export interface AgendaEntry {
  when: string;
  title: string;
  kind: string;
  detail: string;
  minutes: number | null;
  source: string;
  today: boolean;
}

export interface Agenda extends Envelope {
  items: AgendaEntry[];
  unavailable: string | null;
  horizon_hours: number;
  /** Modules whose own agenda could not be read this time ("calendar
   *  (TimeoutError)"): their items are missing, so an empty day is not
   *  known to be free. Optional so an older server still types. */
  unread?: string[];
}

export interface PendingProposal {
  id: number;
  action: string;
  reason: string;
  tier: number;
  created_at: string | null;
  expires_at: string | null;
  finding: string | null;
}

export interface NightshiftJob {
  id: number;
  at: string | null;
  task: string;
  repo: string;
  branch: string;
  status: string;
  preview_url: string | null;
  finished_at: string | null;
}

export interface QueuedResearch {
  id: number;
  at: string | null;
  question: string;
  status: string;
}

export interface Tasks extends Envelope {
  proposals: PendingProposal[];
  jobs: NightshiftJob[];
  research: QueuedResearch[];
  pending: number;
}

// --- the work board (GET /api/tasks/board, kernel/workboard.py) ---------
// One row per agent: what it has in hand, what waits on Miro, what it did
// in the window, and its modules' own progress. A count that is `null`
// could not be read (the seat's `unread` names the table) -- unknown, never 0.

export type BoardRowKind = "mission" | "nightshift" | "outbox" | "research";

export interface BoardProgress {
  /** null: the table that counts "done" could not be read. */
  done: number | null;
  total: number;
  unit: string;
}

export interface BoardRow {
  kind: BoardRowKind;
  id: number;
  status: string;
  summary: string;
  started: string | null;
  updated: string | null;
  finished: string | null;
  // mission
  recipe?: string;
  phase?: string;
  steps?: number;
  blocked?: string[];
  progress?: BoardProgress | null;
  // nightshift
  repo?: string;
  branch?: string;
  preview_url?: string | null;
  // outbox
  action?: string;
  attempts?: number;
  max_attempts?: number;
  destination?: string;
  detail?: string;
}

export interface BoardCard {
  id: number;
  action: string;
  tier: number;
  reason: string;
  created_at: string | null;
  expires_at: string | null;
}

export interface BoardJob {
  name: string;
  when: string;
  layer: BrainLayer;
  /** Windowed jobs write schedule_runs; interval jobs record nothing. */
  recorded: boolean;
  last_run: { status: string; at: string | null; occurrence: string } | null;
  /** false: the job records runs but the table could not be read. */
  last_run_known: boolean;
  next_due: string | null;
}

export interface BoardFigure {
  label: string;
  /** null: unknown. */
  value: number | null;
  read_at: string;
  note: string;
}

export interface BoardModuleProgress {
  module: string;
  figures: BoardFigure[];
  read_at: string;
  error: string | null;
  /** The head reading a department module that is not its own. */
  oversight: boolean;
}

export interface BoardAgent {
  name: string;
  label: string;
  title: string;
  department: string;
  department_name: string;
  head: boolean;
  head_label: string;
  modules: string[];
  oversees: string[];
  in_flight: BoardRow[];
  waiting: BoardCard[] | null;
  jobs: BoardJob[];
  recent: {
    missions: BoardRow[];
    nightshift: BoardRow[];
    outbox: BoardRow[];
    executions: Record<string, { ok: number; failed: number }>;
    verify: Record<string, number>;
  };
  progress: BoardModuleProgress[];
  paused: BrainPause[] | null;
  unread: string[];
  counts: { in_flight: number | null; waiting: number | null };
  headline: string;
}

export interface Board extends Envelope {
  read_at: string;
  since: string;
  window_days: number;
  served_at?: string;
  totals: {
    waiting_on_you: number | null;
    in_flight: number | null;
    agents_busy: number;
    paused: number | null;
  };
  agents: BoardAgent[];
  errors: Record<string, string>;
}

export interface Conversation {
  source: "voice" | "messaging" | "messages";
  id: number;
  at: string | null;
  platform: string;
  thread: string | null;
  who: string;
  text: string;
  answer: string | null;
  intent: string | null;
  agent: string | null;
  replied_at: string | null;
  draft: string | null;
  /** "voice note (transcribed)" when the text is a machine transcript. */
  label: string | null;
}

export interface Conversations extends Envelope {
  conversations: Conversation[];
  unanswered: number;
  limit: number;
}

export interface VaultDocument {
  id: number; path: string; title: string; kind: string;
  indexed_at: string | null; chunks: number;
}
export interface LibraryEntry {
  id: number; at: string | null; kind: string; title: string;
  source: string | null; notes: string;
}
export interface ResearchReport {
  id: number; at: string | null; question: string; status: string;
  report_id: number | null; sources: number | null;
}
export interface KnowledgeReport {
  id: number; at: string | null; kind: string; title: string;
  delivered_via: string | null;
}
export interface Knowledge extends Envelope {
  documents: VaultDocument[];
  library: LibraryEntry[];
  research: ResearchReport[];
  reports: KnowledgeReport[];
  counts: { documents: number; library: number; research: number; reports: number };
}

export type OutreachState = "drafted" | "waiting on you" | "sent" | "replied" | "bounced";

export interface LeadNextStep {
  kind: "proposal" | "draft";
  card_id: number;
  label: string;
  expires_at: string | null;
}

export interface LeadContact {
  name: string | null;
  email: string | null;
  phone: string | null;
  site: string | null;
}

/** One row of the Lead Pipeline (G16). `outreach_state` is null when
 *  nothing has been drafted for this lead yet -- never "drafted", which
 *  would claim a state that does not exist. `score_read_at` is null exactly
 *  when `score` is -- a number never appears next to a time it was not
 *  actually read at (invariant 6). */
export interface Lead {
  id: number;
  business: string;
  category: string;
  source: string;
  address: string | null;
  contact: LeadContact;
  website: string | null;
  score: number | null;
  score_read_at: string | null;
  status: string;
  audit_finding: string | null;
  audit_severity: number | null;
  audit_at: string | null;
  outreach_kind: string | null;
  outreach_state: OutreachState | null;
  outreach_at: string | null;
  last_touch_at: string | null;
  created_at: string | null;
  next_step: LeadNextStep | null;
}

export interface LeadAudit {
  id: number;
  at: string | null;
  url: string | null;
  severity: number | null;
  findings: Array<{ check?: string; severity?: number; detail?: string }>;
  measured: Record<string, unknown>;
}

export interface LeadOutreachEntry {
  id: number;
  at: string | null;
  kind: string;
  to: string | null;
  subject: string | null;
  body: string;
  status: string;
  sent_at: string | null;
  followup_of: number | null;
}

export interface LeadTimelineEntry {
  id: number;
  at: string;
  event: string;
  action: string;
  detail: string;
}

export interface LeadDetail {
  lead: Lead;
  score_features: Record<string, unknown>;
  audits: LeadAudit[];
  outreach: LeadOutreachEntry[];
  timeline: LeadTimelineEntry[];
}

export interface ToolEntry {
  name: string;
  module: string;
  tier: number;
  allowed: boolean;
  reason: string;
  summary: string;
  params: Record<string, string>;
  reversible: boolean;
}

export interface SkillEntry {
  name: string;
  triggers: string[];
  agents: string[];
  priority: number;
}

/** Perplexity's month (Task 42): `used` is null when there is no count —
 *  the key is unset or the table unreadable — and `reason` says which. */
export interface PerplexityBudget {
  configured: boolean;
  used: number | null;
  cap: number;
  read_at: string | null;
  reason: string | null;
}

export interface Tools extends Envelope {
  actions: ToolEntry[];
  count: number;
  perplexity?: PerplexityBudget;
  skills: SkillEntry[];
}

export interface StandingRule {
  id: number;
  at: string | null;
  said: string;
  via: string | null;
  briefed_at: string | null;
}

export interface ScheduledStep {
  name: string;
  hour: number | null;
  weekday: number | null;
  interval_s: number | null;
  env: string;
  detail: string;
}

/** A workflow node's kind decides its card's shape and colour bar on the
 *  canvas (Task 61): `gate` draws a diamond, everything else a rounded
 *  card. `trigger` is the clock or event that starts the workflow. */
export type WorkflowNodeKind =
  | "trigger" | "check" | "agent" | "action" | "gate" | "channel" | "store";

export type WorkflowTrigger = "cron" | "event" | "voice" | "slack";

/** Four outcomes reach the canvas — fewer than the health vocabulary other
 *  pages use, because this page is one dot per node, not a second reading
 *  of what Fleet or Marketing already show. `unknown` is a fifth word for
 *  a check that ran but could not measure, or a read that itself failed
 *  (invariant 7); it renders exactly like `never` — grey, dashed, unlit —
 *  but keeps its own word in `note`. */
export type WorkflowOutcome = "ok" | "degraded" | "failed" | "never" | "unknown";

export interface WorkflowNode {
  id: string;
  kind: WorkflowNodeKind;
  label: string;
  detail: string;
  last_at: string | null;
  last_outcome: WorkflowOutcome;
  note: string;
}

export interface WorkflowEdge {
  from: string;
  to: string;
  label: string;
}

export interface WorkflowGraph {
  id: string;
  title: string;
  trigger: WorkflowTrigger;
  trigger_detail: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  last_run: { at: string | null; outcome: WorkflowOutcome };
}

export interface Workflows extends Envelope {
  rules: StandingRule[];
  schedule: ScheduledStep[];
  graphs: WorkflowGraph[];
}

export type ExperimentStage = "draft" | "awaiting" | "published" | "measured";

/** One word per treatment. `unmeasured` is the only closed state that is not
 *  a comparison somebody made, and it never shares a look with the three that
 *  are. `overdue` is a published experiment whose window closed unread. */
export type ExperimentState =
  | "draft" | "awaiting" | "measuring" | "overdue"
  | "won" | "lost" | "flat" | "unmeasured";

export interface ChannelFigure {
  label: string;
  value: number | null;
  read_at: string | null;
}

export interface MarketingChannel {
  channel: string;
  /** YouTube, Instagram or TikTok. Older payloads carry none: YouTube. */
  platform?: string;
  /** Instagram and TikTok figures, each with the time it was read. */
  figures?: ChannelFigure[];
  health: string;
  reason: string | null;
  read_at: string | null;
  // When the newest read failed: the time of the attempt, not of a figure.
  attempted_at: string | null;
  subscribers: number | null;
  views_total: number | null;
  views_28d: number | null;
  views_28d_since: string | null;
  // Days between the two readings the figure is derived from (28 to 31).
  views_28d_span_days: number | null;
  views_28d_reason: string | null;
  partner: {
    threshold: number;
    eligible_on_subscribers: boolean | null;
    line: string;
  } | null;
}

export interface MarketingExperiment {
  id: number;
  at: string | null;
  business: string;
  platform: string;
  hypothesis: string;
  variant: string;
  draft_id: number | null;
  posted_ref: string | null;
  posted_at: string | null;
  measure_at: string | null;
  metric: string | null;
  baseline: number | null;
  baseline_read_at: string | null;
  observed: number | null;
  delta_pct: number | null;
  measured_at: string | null;
  outcome: string | null;
  lesson: string | null;
  stage: ExperimentStage;
  state: ExperimentState;
  never_published: boolean;
  proposal_id: number | null;
  can_close: boolean;
  cannot_close: string | null;
}

export interface PlaybookLesson {
  id: number;
  at: string | null;
  content: string;
  outcome: string | null;
  business: string | null;
  platform: string | null;
  variant: string | null;
  metric: string | null;
  observed: number | null;
  baseline: number | null;
  delta_pct: number | null;
}

export interface IdeaFact {
  name: string;
  // Null whenever `undated`: a number with no read time is not shown.
  value: unknown;
  observed_at: string | null;
  detail: string | null;
  undated: boolean;
}

export interface MonetizationIdea {
  id: number;
  at: string | null;
  business: string;
  idea: string;
  mechanism: string;
  evidence: IdeaFact[];
  requires: string | null;
  est_note: string | null;
  score: number | null;
  status: string;
  money_out: boolean;
}

export interface MarketingPlanItem {
  id: number;
  action: string;
  reason: string;
  channel: string | null;
  experiment_id: number | null;
  created_at: string | null;
  expires_at: string | null;
  // Still `pending` in the table, but past its time: it can no longer run.
  expired: boolean;
}

export type MarketingSection = "channels" | "experiments" | "playbook" | "ideas" | "plan";

export interface Marketing extends Envelope {
  watch_only: boolean;
  hooked_platforms: string[];
  unhooked_platforms: string[];
  hook_note: string | null;
  channels: MarketingChannel[];
  experiments: MarketingExperiment[];
  playbook: PlaybookLesson[];
  ideas: MonetizationIdea[];
  plan: MarketingPlanItem[];
  filing: {
    filed: number;
    refused: number;
    reasons: string[];
    at: string | null;
    note: string | null;
  };
  // A section named here could not be read. Its list is empty because
  // nothing was read, not because there is nothing.
  unavailable: Record<MarketingSection, string | null>;
}

export interface Weather {
  temperature_c: number;
  code: number | null;
  description: string;
  observed_at: string | null;
}

/** The four words a Guardian containment step is drawn with. A proposal
 *  whose status has none of them (failed, expired) carries `state: null`
 *  and the raw status is shown instead — it never borrows DONE. */
export type StepState = "PROPOSED" | "APPROVED" | "DONE" | "REFUSED";

/** A hub NetworkRef, inlined on an alert or a device — Task 52's
 *  cross-reference source for `network` (never guessed; see incident.py). */
export interface NetworkRef {
  id: string; slug: string; name: string; colour: string;
}

export interface IncidentVector {
  kind: string;
  target: string;
  evidence: {
    alert?: string; host?: string | null; account?: string;
    source?: string; observed_at?: string;
  };
  first_seen: string | null;
  reputation: string | null;
  // Neither guessed: null unless the hub's own records name them. mac comes
  // only from the primary vector's evidence blob; network is cross-
  // referenced from the same alert's row elsewhere (see incident.py).
  mac: string | null;
  network: NetworkRef | null;
  // The one AUTOMATIC step that answers this vector, as [verb, key], or
  // null — "mark_untrusted"+mac today, the only kind Task 52 computes
  // without asking. Several vectors can share one.
  step: [string, string] | null;
  // Something only Miro can do: disable a named account (credential
  // revocation, Tier 3 for ATLAS), or containment/mark_untrusted this file
  // could not automate (no containment API, or a missing mac/network).
  manual: { kind: string; target: string; note: string } | null;
}

export interface IncidentPlan {
  alert_id: string;
  vectors: IncidentVector[];
  operator: "bot" | "live" | "unknown";
  // Each of Task 52's three real actions has its own params shape: mark_
  // untrusted (mac, network), rescan (network), alert_op (alert_key, op,
  // hours) — `guardian.contain` never appears here any more, since it
  // always refuses (see incident.py).
  steps: Array<{
    action: string;
    params: Record<string, unknown>;
    why: string;
    tier: number;
    observed_at: string;
  }>;
  persistence_checks: string[];
  say: string;
  manual: Array<{ kind: string; target: string; why: string; note: string;
                  observed_at: string }>;
  observed_at: string | null;
}

export interface IncidentRef {
  iid: string | null; entity: string | null;
  sev: string | null; opened: string | null;
  alert_keys: string[];
}

export interface GuardianFeed extends Envelope {
  open_incidents: {
    at: string;
    health: string;
    value: number | null;
    detail: string;
    incidents: IncidentRef[];
    plan: IncidentPlan | null;
    narration: string | null;
    plan_unavailable: string | null;
    // The incident the plan was built for; step state is keyed on it.
    plan_incident: IncidentRef | null;
    total: number | null;
    incidents_read: number | null;
  } | null;
  steps: Array<{
    id: number;
    at: string;
    action: string;
    params: Record<string, unknown>;
    status: string;
    state: StepState | null;
    reason: string;
    decided_at: string | null;
    expires_at: string | null;
  }>;
  log: Array<{
    id: number;
    at: string;
    event: string;
    subject: string;
    detail: string;
    state: StepState | null;
  }>;
}

export interface Ambient extends Envelope {
  clock: string;
  timezone: string | null;
  location: string | null;
  weather: Weather | null;
  reason: string | null;
  // Whether this reading came from the server's 10-minute cache. The
  // `observed_at` inside `weather` is Open-Meteo's own stamp either way.
  cached: boolean;
}

// ------------------------------------------------ voice focus and the check

/**
 * The screens the SPA serves -- the routing table a spoken answer's `focus`
 * may navigate to. Kept in step with the `<Routes>` in App.tsx; a focus that
 * names anything else is ignored rather than followed.
 */
export const SCREEN_ROUTES: readonly string[] = [
  "/", "/core", "/tasks", "/calendar", "/conversations", "/knowledge", "/tools",
  "/workflows", "/marketing", "/status", "/activity", "/reports", "/fleet", "/memory",
  "/airglass",
];

/** Where the dashboard should look after an answer. A view command ("show
 *  me your face", "open the guardian audit report") names a screen and maybe
 *  a document; a spoken status names a module as well ("how is flycheap
 *  doing"), whose tile is shown beside the answer. A fresh object per answer,
 *  so the same command twice navigates twice. */
export interface VoiceFocus {
  screen: string;
  module?: string | null;
  open?: { kind: string; id: number; title: string } | null;
}

export interface SystemCheckAgent {
  name: string;
  title: string;
  /** Its Need resolves to a provider right now (the `/agents` rule). */
  online: boolean;
  /** Why it is offline ("no local model"), or null when online. */
  reason: string | null;
}

export interface SystemCheckModule {
  name: string;
  title: string;
  health: string;
  checks_ok: number;
  checks_total: number;
  unknown: number;
  degraded: number;
  failing: number;
  enabled: boolean;
  reason: string | null;
  /** Why its first unmeasured check is unmeasured, as spoken. */
  not_measured: string | null;
}

/** `/api/system/check`, and the `check` a spoken system check was said
 *  from -- one read, so what is drawn is what was said. */
export interface SystemCheck extends Envelope {
  agents: SystemCheckAgent[];
  modules: SystemCheckModule[];
  /** `total` counts registered checks only; modules that did not load are
   *  `not_loaded`, named under "Not measured", and never add to `total`. */
  summary: {
    ok: number; degraded: number; failing: number; unknown: number; total: number;
    not_loaded: number;
  };
  oldest_reading: string | null;
  unreadable: string | null;
  line: string;
}

export type AskKind =
  | "question" | "action_request" | "system_check" | "goodnight"
  | "view" | "self" | "knowledge";

/** What `/api/voice/ask` answers with. */
export interface AskReply extends Envelope {
  transcript: string;
  answer: string | null;
  error: string | null;
  action_request: boolean;
  proposal: number | null;
  kind?: AskKind | string | null;
  focus?: VoiceFocus | null;
  check?: SystemCheck | null;
  /** What to SAY, when shorter than `answer` (which is still typed in full):
   *  no "More:" section, no config names. Null: say `answer`. */
  spoken?: string | null;
  /** Which agent answered ("concierge", "markets", ...). */
  agent?: string | null;
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* the body was not JSON; the status line is all we have */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

// Every call here is a GET except login, logout, `ask` -- the existing
// read-only `/voice/ask` route the microphone already uses: it answers, and
// anything that would act only ever becomes a proposal -- and `warmAgents`
// (Task 59), which never reaches the executor either: it only asks every
// roster agent for a tool-free self-introduction, so the fleet's LAST RUN
// and MODEL stop reading "never". There is still nothing here that can act
// on the world.
export const api = {
  session: () =>
    request<Envelope & {
      authenticated: boolean; user: string | null;
      configured: boolean; watch_only: boolean;
    }>("/session"),

  login: (password: string) =>
    request<Envelope & { ok: boolean; watch_only: boolean }>("/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  logout: () => request<Envelope>("/logout", { method: "POST" }),

  overview: () => request<Overview>("/overview"),
  /** Every stored reading of one check, oldest first (1 to 30 days). */
  checkHistory: (module: string, check: string, days = 7) =>
    request<CheckHistory>(
      `/checks/${encodeURIComponent(module)}/${encodeURIComponent(check)}/history?days=${days}`),
  /** The Health strip's counts hour by hour (1 to 14 days). */
  healthHistory: (days = 7) => request<HealthHistory>(`/health/history?days=${days}`),
  /** Every Need's model chain with breaker state, and each agent's model. */
  models: () => request<Models>("/models"),
  /** The self-model: the running build and how long it has run. */
  self: () => request<Envelope & {
    deploy_sha: string | null; started_at: string | null; uptime_s: number | null;
  }>("/self"),
  /** The last spoken turns, newest first (GET /api/voice/history). */
  voiceHistory: (limit = 8) =>
    request<Envelope & { turns: VoiceTurn[] }>(`/voice/history?limit=${limit}`),
  judgment: () => request<Judgment>("/judgment"),

  approvals: () => request<Approvals>("/approvals"),

  findings: (params: { module?: string; severity?: number; limit?: number }) => {
    const search = new URLSearchParams();
    if (params.module) search.set("module", params.module);
    if (params.severity !== undefined) search.set("severity", String(params.severity));
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    const query = search.toString();
    return request<Envelope & {
      findings: Finding[];
      filters: { module: string | null; severity: number | null; limit: number };
    }>(`/findings${query ? `?${query}` : ""}`);
  },

  audit: (limit = 200) =>
    request<Envelope & { entries: AuditEntry[] }>(`/audit?limit=${limit}`),

  verifyChain: () =>
    request<Envelope & { ok: boolean; detail: string }>("/audit/verify"),

  moduleDetail: (name: string) =>
    request<ModuleDetail>(`/modules/${encodeURIComponent(name)}`),

  systemCheck: () => request<SystemCheck>("/system/check"),

  status: () => request<StatusCounters>("/status"),

  system: () =>
    request<Envelope & {
      // The server does not publish a build sha today. Typed optional rather
      // than absent so the Command Center can show one the day it does,
      // instead of the UI's own package version.
      version?: string | null;
      cpu_percent: number | null;
      memory: { total: number | null; used: number | null; percent: number | null };
      disk: { total: number | null; used: number | null; percent: number | null };
      uptime_s: number | null;
      watch_only: boolean;
    }>("/system"),

  agents: () => request<Envelope & { agents: AgentSummary[] }>("/agents"),

  // The Agency room: ATLAS and every agent, read from agent_runs. Without
  // `live`, today's stored meeting (or a live build if none yet); with it,
  // rebuilt from the records now.
  agencyMeeting: (live = false) =>
    request<Meeting>(live ? "/agency/meeting?live=1" : "/agency/meeting"),

  // The Brain: departments, their last 7 days and every seat's SOP.
  agencyBrain: () => request<Brain>("/agency/brain"),

  missions: (limit = 20) =>
    request<{ missions: Mission[]; read_at: string }>(`/agency/missions?limit=${limit}`),

  agentsFeed: (limit = 50) =>
    request<Envelope & { items: AgentFeedItem[] }>(`/agents/feed?limit=${limit}`),

  // With no name: the original full-roster sweep. With one: Task 69's
  // per-card "Run self-test" button -- warms that agent only.
  warmAgents: (name?: string) =>
    request<Envelope & { started: boolean; agents: string[] }>("/agents/warm", {
      method: "POST",
      ...(name ? { body: JSON.stringify({ name }) } : {}),
    }),

  guardianFeed: (limit = 50) =>
    request<GuardianFeed>(`/guardian/feed?limit=${limit}`),

  memoryGraph: () =>
    request<Envelope & {
      nodes: MemoryGraphNode[];
      edges: MemoryGraphEdge[];
      agents: MemoryGraphAgent[];
    }>("/memory/graph"),

  llm: () =>
    request<Envelope & {
      needs: Record<string, {
        providers: string[]; connected: boolean; cross_provider: boolean | null;
      }>;
      vendors: string[];
    }>("/llm"),

  memory: () =>
    request<Envelope & {
      kinds: Record<string, number>;
      total: number; voice_turns: number; tool_calls: number;
    }>("/memory"),

  activity: (params: { module?: string; kind?: string; day?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params.module) search.set("module", params.module);
    if (params.kind) search.set("kind", params.kind);
    if (params.day) search.set("day", params.day);
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    const query = search.toString();
    return request<Envelope & {
      activity: ActivityRow[];
      filters: { module: string | null; kind: string | null; day: string | null; limit: number };
    }>(`/activity${query ? `?${query}` : ""}`);
  },

  reports: (params: { kind?: string; since?: string; module?: string } = {}) => {
    const search = new URLSearchParams();
    if (params.kind) search.set("kind", params.kind);
    if (params.since) search.set("since", params.since);
    if (params.module) search.set("module", params.module);
    const query = search.toString();
    return request<Envelope & { reports: ReportSummary[] }>(
      `/reports${query ? `?${query}` : ""}`);
  },

  report: (id: number) =>
    request<Envelope & {
      report: {
        id: number; at: string; kind: string; title: string; body: string;
        delivered_via: string | null;
        sections: Array<{ title: string; lines: string[]; unavailable: string | null; empty: string }>;
      };
    }>(`/reports/${id}`),

  /** `hours`: how far ahead to read the diary, 1 to 168 (the server's
   *  default, 72, when omitted). */
  agenda: (hours?: number) =>
    request<Agenda>(hours === undefined ? "/agenda" : `/agenda?hours=${hours}`),

  tasks: () => request<Tasks>("/tasks"),

  /** The work board: what every agent has in hand (kernel/workboard.py). */
  tasksBoard: (days = 7) => request<Board>(`/tasks/board?days=${days}`),

  conversations: (limit = 60) =>
    request<Conversations>(`/conversations?limit=${limit}`),

  knowledge: () => request<Knowledge>("/knowledge"),

  leads: (params: {
    limit?: number; offset?: number; q?: string; status?: string;
    sort?: string; sort_dir?: "asc" | "desc";
  } = {}) => {
    const search = new URLSearchParams();
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    if (params.offset !== undefined) search.set("offset", String(params.offset));
    if (params.q) search.set("q", params.q);
    if (params.status) search.set("status", params.status);
    if (params.sort) search.set("sort", params.sort);
    if (params.sort_dir) search.set("sort_dir", params.sort_dir);
    const query = search.toString();
    return request<Envelope & {
      leads: Lead[]; total: number; limit: number; offset: number;
      counts: Record<string, number>; why_empty: string | null;
    }>(`/leads${query ? `?${query}` : ""}`);
  },

  leadDetail: (id: number) => request<Envelope & LeadDetail>(`/leads/${id}`),

  tools: () => request<Tools>("/tools"),

  workflows: () => request<Workflows>("/workflows"),

  marketing: () => request<Marketing>("/marketing"),

  ambient: () => request<Ambient>("/ambient"),

  brief: () =>
    request<Envelope & {
      brief: { at: string; watch_only: boolean; sections: BriefSection[] };
    }>("/brief"),

  brain: () =>
    request<Envelope & { regions: BrainRegion[]; recent: BrainRead[] }>(
      "/brain"),

  /** The typed twin of speaking: the same `/api/voice/ask` the microphone
   *  reaches, with the words arriving from a keyboard instead. It is not a
   *  second path — `wants_action` is classified server-side exactly as it is
   *  for speech, and anything that would act still becomes a proposal. */
  ask: (transcript: string) =>
    request<AskReply>(
      "/voice/ask", { method: "POST", body: JSON.stringify({ transcript }) }),
};
