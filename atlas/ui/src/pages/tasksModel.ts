/**
 * The Tasks tab's words and order, as pure functions (no DOM, no clock
 * unless passed), so the rules -- busy seats first, unknown is never 0, a
 * filter matches a person, a department or a module -- are tested once.
 */
import type {
  Board, BoardAgent, BoardCard, BoardFigure, BoardJob, BoardProgress, BoardRow,
} from "../api";

export function countText(n: number | null | undefined): string {
  return n === null || n === undefined ? "unknown" : String(n);
}

/** Seats with something in hand first (most first), then those with cards
 *  waiting, then the rest in roster order. Unknown counts as nothing known,
 *  which sorts with zero -- the section itself says UNKNOWN. */
export function orderAgents(agents: BoardAgent[]): BoardAgent[] {
  return agents
    .map((a, i) => ({ a, i }))
    .sort((x, y) =>
      (y.a.counts.in_flight ?? 0) - (x.a.counts.in_flight ?? 0)
      || (y.a.counts.waiting ?? 0) - (x.a.counts.waiting ?? 0)
      || x.i - y.i)
    .map((x) => x.a);
}

function norm(s: string): string {
  return s.toLowerCase().trim();
}

/** A filter box matches the person, the agent key, the department (key or
 *  name) or any module the seat owns or oversees. Empty shows everyone. */
export function filterAgents(agents: BoardAgent[], query: string): BoardAgent[] {
  const q = norm(query);
  if (!q) return agents;
  return agents.filter((a) =>
    [a.label, a.name, a.title, a.department, a.department_name, ...a.modules,
     ...a.oversees].some((s) => norm(s).includes(q)));
}

/** Whether a section starts open: anything in hand or waiting, or nothing
 *  known about it (an unknown deserves a look, not a collapsed line). */
export function startsOpen(a: BoardAgent): boolean {
  return (a.counts.in_flight ?? 0) > 0 || (a.counts.waiting ?? 0) > 0
    || a.counts.in_flight === null || a.counts.waiting === null;
}

/** 0..100, or null when "done" could not be read or total is nothing. */
export function progressPct(p: BoardProgress | null | undefined): number | null {
  if (!p || p.done === null || p.total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((100 * p.done) / p.total)));
}

export function progressText(p: BoardProgress | null | undefined): string {
  if (!p) return "";
  return `${p.done === null ? "?" : p.done}/${p.total} ${p.unit}`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
}

/** Minutes left, floored: 59 seconds reads "<1m", never "1m". */
export function left(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "—";
  const delta = new Date(iso).getTime() - now;
  if (Number.isNaN(delta)) return "—";
  if (delta <= 0) return "expired";
  const mins = Math.floor(delta / 60000);
  if (mins < 1) return "<1m";
  return mins < 60 ? `${mins}m` : `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

/** What an in-flight row is, in ATLAS's words; the table's own text is
 *  shown beside it, never inside it. */
export function rowKind(r: BoardRow): string {
  switch (r.kind) {
    case "mission":
      return `mission #${r.id} ${r.recipe ?? ""}${r.phase ? ` · ${r.phase}` : ""}`;
    case "nightshift":
      return `nightshift #${r.id}`;
    case "outbox":
      return `outbox #${r.id} ${r.action ?? ""}`;
    case "research":
      return `research #${r.id}`;
  }
}

export function rowMeta(r: BoardRow): string {
  const bits = [r.status];
  if (r.kind === "outbox" && r.attempts !== undefined) {
    bits.push(`attempt ${r.attempts}/${r.max_attempts ?? "?"}`);
  }
  if (r.kind === "mission" && r.steps !== undefined) bits.push(`${r.steps} steps`);
  if (r.kind === "mission" && r.blocked && r.blocked.length > 0) {
    bits.push(`blocked: ${r.blocked.join(", ")}`);
  }
  bits.push(`started ${when(r.started)}`);
  if (r.updated && r.updated !== r.started) bits.push(`updated ${when(r.updated)}`);
  return bits.join(" · ");
}

export function cardLine(c: BoardCard, now: number = Date.now()): string {
  return `#${c.id} ${c.action} · Tier ${c.tier} · ${left(c.expires_at, now)}`;
}

/** "interval job — no per-run record" is said, not shown as a zero. */
export function jobLastRun(j: BoardJob): string {
  if (!j.recorded) return "interval — no per-run record";
  if (!j.last_run_known) return "last run unknown";
  if (j.last_run === null) return "never recorded";
  return `last ${j.last_run.status} ${when(j.last_run.at)}`;
}

export function jobLine(j: BoardJob): string {
  const next = j.next_due ? ` · next ${when(j.next_due)}` : "";
  return `${jobLastRun(j)}${next}`;
}

export function figureText(f: BoardFigure): string {
  return `${f.label} ${f.value === null ? "unknown" : f.value}`;
}

/** The window's results in one line: settled missions, outbox, executions. */
export function recentLine(a: BoardAgent, days: number): string {
  const r = a.recent;
  const ex = Object.values(r.executions);
  const ran = ex.reduce((n, c) => n + c.ok + c.failed, 0);
  const failed = ex.reduce((n, c) => n + c.failed, 0);
  const parts: string[] = [];
  if (r.missions.length) {
    const done = r.missions.filter((m) => m.status === "done").length;
    parts.push(`${r.missions.length} mission${r.missions.length === 1 ? "" : "s"} settled (${done} done)`);
  }
  if (r.nightshift.length) parts.push(`${r.nightshift.length} nightshift finished`);
  if (r.outbox.length) {
    const delivered = r.outbox.filter((o) => o.status === "delivered").length;
    parts.push(`${r.outbox.length} outbox settled (${delivered} delivered)`);
  }
  if (ran) parts.push(`${ran} action${ran === 1 ? "" : "s"} ran${failed ? `, ${failed} failed` : ""}`);
  const verify = Object.entries(r.verify);
  if (verify.length) parts.push("read-backs " + verify.map(([k, v]) => `${k} ${v}`).join(", "));
  if (a.unread.includes("executions")) parts.push("actions: unknown");
  return parts.length ? parts.join(" · ") : `nothing in the last ${days} days`;
}

export function totalsLine(b: Board): string {
  const t = b.totals;
  return `${countText(t.in_flight)} in flight · ${t.agents_busy} of ${b.agents.length} seats busy · `
    + `${countText(t.paused)} paused`;
}

/** The old nightshift table, now inside Marcus's section: every nightshift
 *  row the seat holds, open first, then the window's finished ones. */
export function nightshiftRows(a: BoardAgent): BoardRow[] {
  return [...a.in_flight.filter((r) => r.kind === "nightshift"), ...a.recent.nightshift];
}
