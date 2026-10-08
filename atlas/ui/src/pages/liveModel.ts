/**
 * Home's live layer as pure functions: the change markers, the shape of the
 * small charts, and the model each agent runs on. No DOM, no fetch, the
 * clock only when passed.
 *
 * - A change is between two readings that both counted (health not
 *   UNKNOWN, a number, the same unit), the earlier one named with its time.
 *   No marker for "no change", for an UNKNOWN on either side, or across a
 *   change of unit (invariant 6: a delta is never guessed).
 * - A chart draws stored readings only. Nothing is interpolated; a gap
 *   longer than the board's staleness rule breaks the line; an UNKNOWN
 *   reading is a grey hollow mark on its own track, never a zero on the
 *   scale (invariant 7).
 * - A model's state is its breaker's, read from the server; a deployment
 *   the boot probes dropped is UNKNOWN/unavailable, never "closed".
 */
import {
  normaliseHealth, type AgentModels, type Health, type HealthSample, type HistoryPoint,
  type Models,
} from "../api";

// --------------------------------------------------------------- numbers

/** A reading's number as text, with the unit to print beside it (null when
 *  the text already says it, as "$" does for dollars). */
export function formatValue(value: number, unit: string | null): { text: string; unit: string | null } {
  if (unit === "usd") {
    const sign = value < 0 ? "-" : "";
    return {
      text: `${sign}$${Math.abs(value).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
      })}`,
      unit: null,
    };
  }
  const text = Number.isInteger(value)
    ? value.toLocaleString("en-US")
    : value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return { text, unit };
}

/** A reading that counts: its health is known and it carries a number. */
export function counts(p: { value: number | null; health: string }): boolean {
  return normaliseHealth(p.health) !== "unknown"
    && p.value !== null && Number.isFinite(p.value);
}

// --------------------------------------------------------------- changes

export interface Reading {
  value: number | null;
  unit: string | null;
  health: Health;
  readAt: Date | null;
}

export interface Change {
  dir: "up" | "down";
  /** Always positive; `dir` carries the sign. */
  delta: number;
  unit: string | null;
  /** When the earlier reading was taken. */
  since: Date;
}

/**
 * The change from the last reading before `current` that counted (same
 * unit) to `current`. Null when there is none to compare with, when either
 * side did not count, or when nothing changed.
 */
export function changeSince(current: Reading, history: HistoryPoint[]): Change | null {
  if (current.readAt === null || current.value === null || !Number.isFinite(current.value)
      || current.health === "unknown") return null;
  const now = current.readAt.getTime();
  let prev: HistoryPoint | null = null;
  for (const p of history) {
    const t = Date.parse(p.read_at);
    if (Number.isNaN(t) || t >= now) continue;
    if (!counts(p) || (p.unit ?? null) !== (current.unit ?? null)) continue;
    if (prev === null || t > Date.parse(prev.read_at)) prev = p;
  }
  if (prev === null || prev.value === null) return null;
  const delta = current.value - prev.value;
  if (Math.abs(delta) < 1e-9) return null;
  return { dir: delta > 0 ? "up" : "down", delta: Math.abs(delta), unit: current.unit,
           since: new Date(prev.read_at) };
}

function when(d: Date, now: Date): string {
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return d.toDateString() === now.toDateString()
    ? time
    : `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

/** "▲ 2 since 10:42" / "▼ $19.00 since 5 Oct 22:10". */
export function changeLabel(c: Change, now: Date): { mark: string; amount: string; since: string } {
  const amount = formatValue(c.delta, c.unit).text;
  return { mark: c.dir === "up" ? "▲" : "▼", amount, since: `since ${when(c.since, now)}` };
}

/**
 * Which keys have a newer read time than the one seen before. A key seen
 * for the first time is not "new": the page just opened, nothing changed
 * while it was looked at.
 */
export function freshlyRead(seen: Map<string, string | null>,
                            now: Record<string, string | null>): string[] {
  const out: string[] = [];
  for (const [key, at] of Object.entries(now)) {
    const before = seen.get(key) ?? null;
    // Never seen with a time: the first reading this page has, not a new one.
    if (before === null) continue;
    if (at !== null && at !== before) out.push(key);
  }
  return out;
}

// ---------------------------------------------------------------- charts

export interface SeriesPoint { t: number; v: number; health: Health }

export interface Mark {
  t: number;
  /** "unknown": the check could not read. "no-number": it read but wrote
   *  no number. Both sit on the grey track, off the value scale. */
  kind: "unknown" | "no-number";
  value: number | null;
  unit: string | null;
}

export interface Series {
  /** The latest counting reading's unit; readings in another unit are
   *  left off the scale and counted in `otherUnit`. */
  unit: string | null;
  /** Runs of counting readings. A new run starts after a gap, a mark, or a
   *  reading in another unit: the line is never drawn across them. */
  segments: SeriesPoint[][];
  marks: Mark[];
  otherUnit: number;
  /** From counting readings only; null when there are none. */
  vMin: number | null;
  vMax: number | null;
  readings: number;
}

export function shapeSeries(points: HistoryPoint[], staleAfterS: number | null): Series {
  const rows = points
    .map((p) => ({ ...p, t: Date.parse(p.read_at), h: normaliseHealth(p.health) }))
    .filter((p) => !Number.isNaN(p.t))
    .sort((a, b) => a.t - b.t);
  const lastCounting = [...rows].reverse().find((p) => counts(p));
  const unit = lastCounting ? lastCounting.unit ?? null : null;
  const gap = staleAfterS !== null && staleAfterS > 0 ? staleAfterS * 1000 : Infinity;

  const segments: SeriesPoint[][] = [];
  const marks: Mark[] = [];
  let otherUnit = 0;
  let run: SeriesPoint[] = [];
  const close = (): void => {
    if (run.length > 0) segments.push(run);
    run = [];
  };
  for (const p of rows) {
    if (p.h === "unknown") {
      marks.push({ t: p.t, kind: "unknown", value: p.value, unit: p.unit ?? null });
      close();
      continue;
    }
    if (p.value === null || !Number.isFinite(p.value)) {
      marks.push({ t: p.t, kind: "no-number", value: null, unit: p.unit ?? null });
      close();
      continue;
    }
    if ((p.unit ?? null) !== unit) {
      otherUnit += 1;
      close();
      continue;
    }
    const last = run[run.length - 1];
    if (last && p.t - last.t > gap) close();
    run.push({ t: p.t, v: p.value, health: p.h });
  }
  close();
  const values = segments.flat().map((s) => s.v);
  return {
    unit, segments, marks, otherUnit,
    vMin: values.length ? Math.min(...values) : null,
    vMax: values.length ? Math.max(...values) : null,
    readings: rows.length,
  };
}

/** Plain-SVG geometry for a series inside `w`×`h`, the value band above a
 *  `track` px strip for the marks. Pure, so the drawing is tested. */
export function layoutSeries(s: Series, from: number, to: number,
                             w: number, h: number, track = 10): {
  lines: Array<Array<{ x: number; y: number; p: SeriesPoint }>>;
  marks: Array<{ x: number; y: number; m: Mark }>;
} {
  const span = Math.max(1, to - from);
  const x = (t: number): number => ((t - from) / span) * w;
  const top = 4;
  const bottom = h - track - 4;
  const lo = s.vMin ?? 0;
  const hi = s.vMax ?? 0;
  // A flat series sits mid-band, never pinned to an edge that reads as 0.
  const y = (v: number): number => (hi === lo
    ? (top + bottom) / 2
    : bottom - ((v - lo) / (hi - lo)) * (bottom - top));
  return {
    lines: s.segments.map((seg) => seg.map((p) => ({ x: x(p.t), y: y(p.v), p }))),
    marks: s.marks.map((m) => ({ x: x(m.t), y: h - track / 2 - 1, m })),
  };
}

// ---------------------------------------------------------------- health

export interface HealthBar {
  t: number;
  counts: Record<Health, number>;
  setAside: number;
  total: number;
}

export function healthBars(samples: HealthSample[]): HealthBar[] {
  return samples
    .map((s) => {
      const c: Record<Health, number> = {
        ok: s.counts.ok ?? 0, degraded: s.counts.degraded ?? 0,
        failing: s.counts.failing ?? 0, unknown: s.counts.unknown ?? 0,
      };
      return { t: Date.parse(s.at), counts: c, setAside: s.set_aside ?? 0,
               total: c.ok + c.degraded + c.failing + c.unknown };
    })
    .filter((b) => !Number.isNaN(b.t))
    .sort((a, b) => a.t - b.t);
}

export function healthBarLine(b: HealthBar): string {
  const c = b.counts;
  const parts: string[] = [];
  if (c.failing) parts.push(`${c.failing} failing`);
  if (c.degraded) parts.push(`${c.degraded} degraded`);
  if (c.unknown) parts.push(`${c.unknown} unknown`);
  parts.push(`${c.ok} OK`);
  return parts.join(", ");
}

// ---------------------------------------------------------------- models

export type ModelDot = "closed" | "open" | "half-open" | "dropped" | "pin" | "off-chain" | "unknown";

export const DOT_WORD: Record<ModelDot, string> = {
  closed: "breaker closed",
  open: "breaker open",
  "half-open": "breaker half-open",
  dropped: "unavailable",
  pin: "pinned, no breaker",
  "off-chain": "not in any chain now",
  unknown: "unknown",
};

/** "groq:openai/gpt-oss-120b" -> "gpt-oss-120b";
 *  "openrouter:nvidia/nemotron-3.5-lightning:free" -> "nemotron-3.5-lightning". */
export function shortModel(full: string): string {
  const cut = full.indexOf(":");
  const model = cut >= 0 ? full.slice(cut + 1) : full;
  const tail = model.slice(model.lastIndexOf("/") + 1);
  return tail.replace(/:free$/, "") || full;
}

/** A provider never contains ":"; a model may (":free"). */
function split(full: string): [string, string] {
  const cut = full.indexOf(":");
  return cut >= 0 ? [full.slice(0, cut), full.slice(cut + 1)] : [full, ""];
}

export function deploymentDot(models: Models, full: string): { dot: ModelDot; why: string | null; until: string | null } {
  const [provider, model] = split(full);
  const d = models.deployments.find((x) => x.provider === provider && x.model === model);
  if (!d) return { dot: "off-chain", why: null, until: null };
  if (d.dropped) return { dot: "dropped", why: d.dropped, until: null };
  if (!d.breaker) return { dot: "unknown", why: null, until: null };
  return { dot: d.breaker.state, why: d.breaker.why, until: d.breaker.until };
}

export interface ModelBadge {
  /** Short name for the node; "Unknown" when it cannot be said. */
  label: string;
  full: string | null;
  which: "last" | "next" | "none";
  dot: ModelDot;
  title: string;
}

/**
 * The model an agent's node shows: the one that answered its last recorded
 * run, or -- when it has not run -- the one it would ask first now, said as
 * such. The dot is that deployment's state now.
 */
export function modelBadge(models: Models | null, error: string | null, name: string,
                           now: Date = new Date()): ModelBadge {
  if (error !== null || models === null) {
    return { label: "Unknown", full: null, which: "none", dot: "unknown",
             title: error ? `Models could not be read: ${error}` : "Models not read yet" };
  }
  const a: AgentModels | undefined = models.agents.find((x) => x.name === name);
  if (!a) {
    return { label: "Unknown", full: null, which: "none", dot: "unknown",
             title: "This agent is not in the model list" };
  }
  const state = (full: string, pinned: boolean): ReturnType<typeof deploymentDot> =>
    pinned ? { dot: "pin", why: null, until: null } : deploymentDot(models, full);
  const words = (s: ReturnType<typeof deploymentDot>): string =>
    `${DOT_WORD[s.dot]}${s.why ? ` (${s.why})` : ""}${s.until ? ` until ${when(new Date(s.until), now)}` : ""}`;
  if (a.last) {
    const s = state(a.last, a.pin?.usable === true && a.pin.model === a.last);
    const at = a.last_at ? ` at ${when(new Date(a.last_at), now)}` : "";
    return { label: shortModel(a.last), full: a.last, which: "last", dot: s.dot,
             title: `Last answered by ${a.last}${at} · ${words(s)}` };
  }
  if (a.next) {
    const s = state(a.next.model, a.next.kind === "pin");
    const ran = a.last_at ? "its last run recorded no model" : "not recorded: it has not run";
    return { label: shortModel(a.next.model), full: a.next.model, which: "next", dot: s.dot,
             title: `${ran}; would ask ${a.next.model} first · ${words(s)}` };
  }
  return { label: "no model route", full: null, which: "none", dot: "unknown",
           title: `No model chain for its Need (${a.need ?? "none"})` };
}

export interface LegendRow {
  key: string;
  provider: string;
  short: string;
  full: string;
  dot: ModelDot;
  word: string;
  needs: string[];
}

const DOT_RANK: Record<ModelDot, number> = {
  open: 0, "half-open": 1, dropped: 2, unknown: 3, "off-chain": 4, pin: 5, closed: 6,
};

/** Every deployment once, trouble first, then by provider and model. */
export function modelLegend(models: Models, now: Date = new Date()): LegendRow[] {
  return models.deployments
    .map((d) => {
      const full = `${d.provider}:${d.model}`;
      const s = deploymentDot(models, full);
      const extra = s.dot === "open" && s.until ? ` until ${when(new Date(s.until), now)}` : "";
      return {
        key: full, provider: d.provider, short: shortModel(full), full, dot: s.dot,
        word: `${DOT_WORD[s.dot]}${s.why ? `: ${s.why}` : ""}${extra}`, needs: d.needs,
      };
    })
    .sort((a, b) => DOT_RANK[a.dot] - DOT_RANK[b.dot]
      || a.provider.localeCompare(b.provider) || a.full.localeCompare(b.full));
}
