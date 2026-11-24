/**
 * ATLAS · Command Center, as pure functions: the overall health figure, the
 * connected services, the diagnostics tiles and the AI core's state. No DOM,
 * no fetch, the clock only when passed.
 *
 * - Overall health is a share of the modules that READ: OK over OK +
 *   degraded + failing. UNKNOWN modules are counted beside it, never folded
 *   in as healthy or as broken; with nothing read there is no percentage.
 * - A service is Online / Degraded / Down only from a check that read it;
 *   a service no check reads, a check not reporting, or one set aside is
 *   Unknown with the reason (invariant 7).
 * - Every figure keeps the time it was read (invariant 6).
 */
import { normaliseHealth, type Health, type ModuleTile, type Models } from "../api";
import { deploymentDot, shortModel } from "./liveModel";
import { safeText } from "./retired";

// ------------------------------------------------------------ overall

export interface Overall {
  /** OK share of the modules that read, 0-100; null when none read. */
  percent: number | null;
  ok: number;
  degraded: number;
  failing: number;
  unknown: number;
  /** Not set up or paused: out of every count. */
  setAside: number;
  /** All-OK only when every watched module reads OK and none is unknown. */
  word: "all-ok" | "attention" | "unknown";
}

export function overall(modules: ModuleTile[] | null): Overall {
  const out: Overall = { percent: null, ok: 0, degraded: 0, failing: 0, unknown: 0,
                         setAside: 0, word: "unknown" };
  if (modules === null) return out;
  for (const m of modules.filter((x) => x.enabled)) {
    if (m.setup) { out.setAside += 1; continue; }
    out[normaliseHealth(m.health)] += 1;
  }
  const read = out.ok + out.degraded + out.failing;
  out.percent = read > 0 ? Math.round((100 * out.ok) / read) : null;
  out.word = out.degraded + out.failing > 0 ? "attention"
    : out.unknown > 0 || read === 0 ? "unknown" : "all-ok";
  return out;
}

/** The header pill: never "all systems operational" while anything is
 *  unknown or nothing has been read. */
export function statusPill(o: Overall, readFailed: boolean): { health: Health; text: string } {
  if (readFailed) return { health: "unknown", text: "Unknown: checks unreachable" };
  const blind = o.unknown > 0 ? ` · ${o.unknown} unknown` : "";
  if (o.failing > 0) {
    return { health: "failing", text: `${o.failing + o.degraded} need attention${blind}` };
  }
  if (o.degraded > 0) return { health: "degraded", text: `${o.degraded} degraded${blind}` };
  if (o.word === "unknown") {
    return { health: "unknown", text: o.unknown > 0 ? `Unknown: ${o.unknown} unmeasured` : "Unknown" };
  }
  return { health: "ok", text: "All systems operational" };
}

// ----------------------------------------------------------- services

export type ServiceState = "online" | "degraded" | "down" | "unknown";

export interface ServiceSpec {
  name: string;
  /** "agency" (the module's own colour) or "comms.slack_auth" (one check). */
  source: string | null;
  /** Why it is unknown when nothing reads it. */
  unread?: string;
  /** A reading that says the service was not contacted (a lane switched
   *  off): OK by its own rule, but not a probe of the service. */
  notProbed?: RegExp;
  to?: string;
}

/** The services Miro checks, and what reads each one. A service with no
 *  `source` is not read by any check today, so it is always Unknown. */
export const SERVICES: readonly ServiceSpec[] = [
  { name: "Mapfront", source: "agency", to: "/agency#seat-websites" },
  { name: "Academy store", source: "shop" },
  { name: "Slack", source: "comms.slack_auth" },
  { name: "Gmail", source: "mail", to: "/agency#seat-personal" },
  { name: "Calendar", source: "calendar", to: "/agency#seat-personal" },
  // The Academy channel is the youtube module's own channel key; it is shown
  // as its name, never as the key.
  { name: "Atlas Academy YouTube", source: "youtube.clips_stats", to: "/agency#seat-academy" },
  { name: "Search Console", source: "seo.search_console" },
  { name: "Twilio", source: "comms.twilio_auth" },
];

export interface Service {
  name: string;
  state: ServiceState;
  word: string;
  /** Why it is not Online, in words; empty when it is. */
  why: string;
  readAt: string | null;
  to: string | null;
}

const STATE_OF: Record<Health, ServiceState> = {
  ok: "online", degraded: "degraded", failing: "down", unknown: "unknown",
};
export const SERVICE_WORD: Record<ServiceState, string> = {
  online: "Online", degraded: "Degraded", down: "Down", unknown: "Unknown",
};

export function service(spec: ServiceSpec, modules: ModuleTile[] | null,
                        readError: string | null): Service {
  const base = { name: spec.name, to: spec.to ?? null };
  const unknown = (why: string, readAt: string | null = null): Service =>
    ({ ...base, state: "unknown", word: "Unknown", why: safeText(why), readAt });
  if (spec.source === null) return unknown(spec.unread ?? "not read by any check");
  if (modules === null) return unknown(readError ? `checks unreadable: ${readError}` : "not read yet");
  const [mod, check] = spec.source.split(".");
  const tile = modules.find((m) => m.module === mod);
  if (!tile) return unknown("not watched");
  if (!tile.enabled) return unknown(tile.setup ?? "switched off");
  if (check) {
    const c = tile.checks.find((x) => x.name === check);
    if (!c) return unknown("its check is not reporting");
    if (c.state) return unknown(c.state.label, c.observed_at);
    if (spec.notProbed?.test(c.detail)) {
      return unknown(`not probed: ${firstClause(c.detail)}`, c.observed_at);
    }
    const h: Health = c.stale ? "unknown" : normaliseHealth(c.health);
    const state = STATE_OF[h];
    return { ...base, state, word: SERVICE_WORD[state], readAt: c.observed_at,
             why: h === "ok" ? "" : c.stale ? "last reading too old to count" : firstClause(c.detail) };
  }
  if (tile.setup) return unknown(tile.setup, tile.last_check_at);
  const h = normaliseHealth(tile.health);
  const worst = tile.checks.find((c) => normaliseHealth(c.health) === h && h !== "ok");
  const state = STATE_OF[h];
  return { ...base, state, word: SERVICE_WORD[state], readAt: tile.last_check_at,
           why: h === "ok" ? "" : worst ? `${worst.name}: ${firstClause(worst.detail)}` : h };
}

function firstClause(detail: string): string {
  const cut = safeText(detail.split(/\s\(|;|\.\s|\n/)[0]?.trim() ?? "");
  return cut.length > 80 ? `${cut.slice(0, 79)}…` : cut;
}

export function services(modules: ModuleTile[] | null, readError: string | null): Service[] {
  return SERVICES.map((s) => service(s, modules, readError));
}

/** The LLM providers as one service: Online when at least one deployment
 *  can be asked now, Degraded when some are open, Down when none can be. */
export function llmService(models: Models | null, error: string | null, readAt: string | null): Service {
  const base = { name: "LLM providers", to: null as string | null };
  if (error !== null || models === null) {
    return { ...base, state: "unknown", word: "Unknown",
             why: error ? `model chains unreadable: ${error}` : "not read yet", readAt: null };
  }
  const r = providersReady(models);
  if (r.total === 0) return { ...base, state: "unknown", word: "Unknown", why: "no model chain", readAt };
  const state: ServiceState = r.ready === 0 ? "down" : r.ready < r.total ? "degraded" : "online";
  return { ...base, state, word: SERVICE_WORD[state], readAt,
           why: state === "online" ? "" : `${r.total - r.ready} of ${r.total} not askable now` };
}

// --------------------------------------------------------- diagnostics

/** Deployments that can be asked now (breaker closed or half-open) out of
 *  every one in a chain or dropped at boot. */
export function providersReady(models: Models): { ready: number; total: number } {
  let ready = 0;
  for (const d of models.deployments) {
    const s = deploymentDot(models, `${d.provider}:${d.model}`).dot;
    if (s === "closed" || s === "half-open") ready += 1;
  }
  return { ready, total: models.deployments.length };
}

/** The model that answered the most recent recorded run of any agent. */
export function lastAnswered(models: Models | null): { model: string; short: string; at: string; agent: string } | null {
  if (!models) return null;
  let best: { model: string; short: string; at: string; agent: string } | null = null;
  for (const a of models.agents) {
    if (!a.last || !a.last_at) continue;
    if (!best || Date.parse(a.last_at) > Date.parse(best.at)) {
      best = { model: a.last, short: shortModel(a.last), at: a.last_at, agent: a.name };
    }
  }
  return best;
}

/** The Storage tile from `backups.disk` (value: % free of the data
 *  volume), with that check's health and read time. Unknown when the check
 *  is not reporting, could not read, or carried no number. */
export function storage(modules: ModuleTile[] | null): {
  text: string | null; health: Health; sub: string;
} {
  const c = modules?.find((m) => m.module === "backups")?.checks.find((x) => x.name === "disk");
  if (!c) return { text: null, health: "unknown", sub: modules ? "disk check not reporting" : "not read" };
  const h: Health = c.stale ? "unknown" : normaliseHealth(c.health);
  const at = c.read_at ?? c.observed_at;
  const when = at ? `read ${new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}` : "no reading";
  if (h === "unknown" || typeof c.value !== "number" || c.unit !== "% free") {
    return { text: null, health: "unknown", sub: `data disk · ${c.stale ? "too old to count" : when}` };
  }
  return { text: `${Math.round(c.value)}% free`, health: h, sub: `data disk · ${when}` };
}

export function uptimeWords(seconds: number | null | undefined): string | null {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return null;
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ------------------------------------------------------------- AI core

export type CoreStep = "standby" | "listening" | "thinking" | "planning" | "executing";

export interface CoreState {
  step: CoreStep;
  lit: boolean;
  /** Why it is lit, or why it could not be known. */
  note: string;
  /** The part of this that could not be read: shown as UNKNOWN. */
  unknown: boolean;
}

/**
 * The strip under the figure. Listening and thinking are the voice loop's
 * own phase; planning is an agent run that has started and not finished
 * (from the live stream); executing is an approved card the executor holds
 * (a Tier 1 action runs inside one check cycle and is not counted). Standby
 * is lit only when every other one is known to be off.
 */
export function coreStates({ phase, agentsRunning, executing }: {
  phase: string;
  /** Agents with a run started and not finished; null when not known. */
  agentsRunning: string[] | null;
  /** Approved cards the executor holds (/api/status); null when unread. */
  executing: number | null;
}): CoreState[] {
  const listening = phase === "listening" || phase === "waking";
  const thinking = phase === "thinking" || phase === "responding";
  const planning = agentsRunning !== null && agentsRunning.length > 0;
  const acting = executing !== null && executing > 0;
  const anyUnknown = agentsRunning === null || executing === null;
  return [
    { step: "standby", lit: !listening && !thinking && !planning && !acting && !anyUnknown,
      note: anyUnknown ? "not known while part of the state is unread" : "waiting for you",
      unknown: false },
    { step: "listening", lit: listening, note: listening ? "hearing you" : "", unknown: false },
    { step: "thinking", lit: thinking, note: thinking ? phase : "", unknown: false },
    { step: "planning", lit: planning,
      note: agentsRunning === null ? "agent runs unknown"
        : planning ? `${agentsRunning.join(", ")} working` : "",
      unknown: agentsRunning === null },
    { step: "executing", lit: acting,
      note: executing === null ? "executor state unread"
        : acting ? `${executing} approved card${executing === 1 ? "" : "s"} running` : "",
      unknown: executing === null },
  ];
}

/** Agent runs in progress from the stream's "agent" events: started and
 *  not yet finished, forgotten after `staleMs` (a lost "finished" must not
 *  keep an agent working forever). */
export function runningAgents(events: Array<{ agent: string; phase: string; at: number }>,
                              now: number, staleMs = 10 * 60_000): string[] {
  const open = new Map<string, number>();
  for (const e of [...events].sort((a, b) => a.at - b.at)) {
    if (e.phase === "started") open.set(e.agent, e.at);
    else if (e.phase === "finished") open.delete(e.agent);
  }
  return [...open.entries()].filter(([, at]) => now - at < staleMs).map(([a]) => a).sort();
}
