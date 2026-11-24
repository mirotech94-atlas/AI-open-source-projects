// "Your ATLAS data" as floating panels: Health, Today's tasks, Store sales,
// Agents working. Each reads the dashboard's own endpoints (same-origin,
// signed in) at the moment it is shown or explained -- never a number kept
// from earlier (CLAUDE.md invariant 6) -- and carries the time it was read.
// A reading that failed says so; it is never drawn or spoken as a number.
// UNKNOWN is its own state and is never drawn like OK (invariant 7).
import {
  api, normaliseHealth, type AgentSummary, type ModuleTile, type Overview, type Tasks,
} from "../api";
import { headline } from "../pages/companionModel";
import { outcomeLine } from "../pages/outcomeText";

export type PanelKind = "health" | "tasks" | "sales" | "agents";
export type RowState = "ok" | "warn" | "fail" | "unknown" | "plain";

export interface PanelRow { label: string; value: string; state: RowState }

export interface PanelData {
  kind: PanelKind;
  title: string;
  summary: string;
  rows: PanelRow[];
  /** When the source read it; null when nothing could be read. */
  readAt: Date | null;
  /** Set when the read failed: the panel shows this, never a number. */
  error: string | null;
}

export const PANELS: ReadonlyArray<{ kind: PanelKind; name: string; glyph: string }> = [
  { kind: "health", name: "Health", glyph: "♥" },
  { kind: "tasks", name: "Today's tasks", glyph: "☑" },
  { kind: "sales", name: "Store sales", glyph: "$" },
  { kind: "agents", name: "Agents working", glyph: "⌘" },
];

const TITLES: Record<PanelKind, string> = {
  health: "Health", tasks: "Today's tasks", sales: "Store sales", agents: "Agents working",
};

const at = (iso: string | null | undefined): Date | null => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
};

function stateOf(h: string): RowState {
  const n = normaliseHealth(h);
  return n === "ok" ? "ok" : n === "degraded" ? "warn" : n === "failing" ? "fail" : "unknown";
}

const WORD: Record<RowState, string> = {
  ok: "OK", warn: "DEGRADED", fail: "FAILING", unknown: "UNKNOWN", plain: "",
};

export function healthPanel(o: Overview): PanelData {
  const mods: ModuleTile[] = o.modules.filter((m) => m.enabled);
  const rows = mods.map((m): PanelRow => {
    if (m.setup) return { label: m.title || m.module, value: m.setup, state: "unknown" };
    const s = stateOf(m.health);
    return { label: m.title || m.module, value: WORD[s], state: s };
  });
  const count = (s: RowState): number => rows.filter((r) => r.state === s).length;
  const hurt = count("warn") + count("fail");
  const parts = [`${count("ok")} OK`];
  if (hurt) parts.push(`${hurt} need attention`);
  if (count("unknown")) parts.push(`${count("unknown")} unknown`);
  return {
    kind: "health", title: TITLES.health, rows, readAt: at(o.as_of), error: null,
    summary: rows.length ? parts.join(", ") : "No modules are switched on.",
  };
}

export function tasksPanel(t: Tasks): PanelData {
  const rows: PanelRow[] = [
    { label: "Waiting on you", value: String(t.pending), state: t.pending > 0 ? "warn" : "ok" },
    { label: "Night-shift jobs", value: String(t.jobs.length), state: "plain" },
    { label: "Research queued", value: String(t.research.length), state: "plain" },
  ];
  for (const p of t.proposals.slice(0, 3)) {
    rows.push({ label: p.action, value: headline(p.reason ?? "", 48), state: "plain" });
  }
  return {
    kind: "tasks", title: TITLES.tasks, rows, readAt: at(t.as_of), error: null,
    summary: t.pending === 1 ? "1 thing waits on you." : `${t.pending} things wait on you.`,
  };
}

export function salesPanel(o: Overview): PanelData {
  const check = o.modules.find((m) => m.module === "shop")?.checks.find((c) => c.name === "sales_7d");
  if (!check) {
    return {
      kind: "sales", title: TITLES.sales, rows: [], readAt: at(o.as_of), error: null,
      summary: "The store sales check is not running, so there is no figure.",
    };
  }
  const s: RowState = check.stale ? "unknown" : stateOf(check.health);
  const value = s === "unknown" && !check.detail ? "unknown" : headline(check.detail) || WORD[s];
  return {
    kind: "sales", title: TITLES.sales,
    rows: [{ label: "Last 7 days", value, state: s }],
    // The check's own reading time, not the page's.
    readAt: at(check.observed_at), error: null,
    summary: s === "unknown" ? "The sales reading is unknown right now." : value,
  };
}

export function agentsPanel(agents: AgentSummary[], asOf: string): PanelData {
  const busy = agents.filter((a) => a.runs_24h > 0)
    .sort((a, b) => b.runs_24h - a.runs_24h);
  const rows = busy.slice(0, 6).map((a): PanelRow => ({
    label: a.display || a.title || a.name,
    value: `${a.runs_24h} run${a.runs_24h === 1 ? "" : "s"} today${a.last_outcome ? ` · ${outcomeLine(a.last_outcome, 60)}` : ""}`,
    state: a.ready ? "plain" : "warn",
  }));
  return {
    kind: "agents", title: TITLES.agents, rows, readAt: at(asOf), error: null,
    summary: busy.length === 1 ? "1 agent ran in the last 24 hours."
      : `${busy.length} agents ran in the last 24 hours.`,
  };
}

/** Read one panel now. Never throws: a failure is a panel that says so. */
export async function readPanel(kind: PanelKind): Promise<PanelData> {
  try {
    if (kind === "health") return healthPanel(await api.overview());
    if (kind === "sales") return salesPanel(await api.overview());
    if (kind === "tasks") return tasksPanel(await api.tasks());
    const a = await api.agents();
    return agentsPanel(a.agents, a.as_of);
  } catch (e) {
    return {
      kind, title: TITLES[kind], rows: [], readAt: null,
      error: `could not be read (${(e as Error)?.message ?? "error"})`, summary: "",
    };
  }
}

const clock = (d: Date): string => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

/** What ATLAS says for "explain": the numbers just read, with their time. */
export function explainPanel(p: PanelData): string {
  if (p.error || !p.readAt) {
    return `${p.title}: I couldn't read it just now, so I won't guess a number.`;
  }
  const rows = p.kind === "health"
    ? p.rows.filter((r) => r.state !== "ok").slice(0, 4).map((r) => `${r.label} is ${r.value.toLowerCase()}`)
    : p.rows.slice(0, 3).map((r) => `${r.label}: ${r.value}`);
  const sentence = (s: string): string => (/[.!?]$/.test(s) ? s : `${s}.`);
  return [`${p.title}, read at ${clock(p.readAt)}.`, p.summary, ...rows]
    .filter(Boolean).map(sentence).join(" ");
}
