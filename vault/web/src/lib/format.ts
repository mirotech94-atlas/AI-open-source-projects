// Presentation-only helpers. The hub ships rendering-ready payloads, so this
// module may reformat a value (units, relative time, tone class) but must never
// aggregate a series or join two collections — that belongs hub-side.

import type { Sev } from "../types";

/** The only four tones in the product: plain plus the three semantic colours
 *  (green = good, yellow = warning, red = alert). Nothing else gets a hue. */
export type Tone = "plain" | "good" | "warn" | "alert";

/** The owner's reference console's `fmtAgo` bands and rounding, to the second:
 *  seconds up to 90, minutes up to 90 minutes, hours up to two days, then days —
 *  and it ROUNDS to the nearest unit rather than flooring.
 *
 *  Both halves matter. Flooring at 60/3600/86400 read 119 seconds as "1m ago"
 *  and 3599 as "59m ago", so a figure sat a whole unit behind the truth at the
 *  top of every band. Overshooting each boundary by half a unit (90s, 90m, 2d)
 *  is what keeps "88s ago" from collapsing into a vaguer "1m ago" the moment it
 *  crosses a minute.
 *
 *  A consequence worth knowing, and the reference has it too: because the cut
 *  and the rounding are half a unit apart, the first minute value ever printed
 *  is "2m" — 89 seconds reads "89s ago" and 90 reads "2m ago", so "1m ago"
 *  never appears at all. Same for "1h" and "1d". That is the trade for never
 *  showing a stale unit, not an off-by-one. */
function band(secs: number, suffix: string): string {
  if (secs < 90) return `${Math.round(secs)}s${suffix}`;
  if (secs < 5400) return `${Math.round(secs / 60)}m${suffix}`;
  if (secs < 172800) return `${Math.round(secs / 3600)}h${suffix}`;
  return `${Math.round(secs / 86400)}d${suffix}`;
}

/** Relative time in one unit: "42s ago" / "7m ago" / "3h ago" / "2d ago". A
 *  missing or unparseable timestamp reads "never"; a timestamp in the future
 *  clamps to "0s ago" rather than showing a negative age. */
export function ago(ts: string | null | undefined): string {
  if (!ts) return "never";
  const then = new Date(ts).getTime();
  if (Number.isNaN(then)) return "never";
  return band(Math.max(0, Math.floor((Date.now() - then) / 1000)), " ago");
}

/** Same bands as `ago` but without the suffix, for an "age" column. */
export function age(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) {
    return "—";
  }
  return band(Math.max(0, Math.floor(seconds)), "");
}

function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** A 0–100 percentage, clamped to that range, at most two decimals and with
 *  trailing zeros dropped: 99.456 -> "99.46%", 50 -> "50%". */
export function pct(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const clamped = Math.min(100, Math.max(0, n));
  return `${trimZeros(clamped.toFixed(2))}%`;
}

/** Latency. Sub-second stays in milliseconds; a second or more reads in
 *  seconds so a slow site does not print five digits. */
export function ms(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (n < 1000) return `${Math.round(n)} ms`;
  return `${trimZeros((n / 1000).toFixed(2))} s`;
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/** Binary-prefixed size with a KB-and-up single decimal. */
export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  let value = Math.max(0, n);
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 ? 0 : 1;
  return `${trimZeros(value.toFixed(digits))} ${BYTE_UNITS[unit]}`;
}

/** Decimal hours as hours-and-minutes: 2.5 -> "2h 30m", 0.25 -> "15m". */
export function hours(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const total = Math.max(0, Math.round(n * 60));
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

/** Severity -> tone. INFO is deliberately `plain`: an informational row gets
 *  no colour, so red and yellow keep meaning something. */
export function sevTone(sev: Sev | null | undefined): Tone {
  if (sev === "CRIT") return "alert";
  if (sev === "WARN") return "warn";
  return "plain";
}

// The hub's `state` fields (Gauge, HourBlock, PatchDay, ExposureTile, TopoNode,
// WifiGuard, ServiceRow) are bare strings documented in `schemas_dash.py` as
// ok|warn|crit|unknown|offline|none and none|planned|done|failed. Unknown
// values fall through to `plain` so a later hub value can never introduce a
// colour the palette does not allow.
const STATE_TONES: Record<string, Tone> = {
  ok: "good",
  good: "good",
  up: "good",
  online: "good",
  active: "good",
  running: "good",
  passed: "good",
  done: "good",
  warn: "warn",
  warning: "warn",
  degraded: "warn",
  planned: "warn",
  sleeping: "warn",
  stale: "warn",
  crit: "alert",
  critical: "alert",
  fail: "alert",
  failed: "alert",
  down: "alert",
  offline: "alert",
  quarantined: "alert",
};

export function stateTone(state: string | null | undefined): Tone {
  if (!state) return "plain";
  return STATE_TONES[state.trim().toLowerCase()] ?? "plain";
}

/** Score -> tone by fraction of `max`: 80 % and up is good, 50 % and up is a
 *  warning, below that an alert. A missing score is plain, never red. Used by
 *  `Ring` and by any grade badge that has to agree with it. */
export function ringTone(value: number | null | undefined, max = 100): Tone {
  if (value === null || value === undefined || !Number.isFinite(value)) return "plain";
  const frac = max <= 0 ? 0 : value / max;
  if (frac >= 0.8) return "good";
  if (frac >= 0.5) return "warn";
  return "alert";
}

/** Drops the parenthesised suffix the imported Nexus site/host names carry —
 *  "Roots prod (status.example.org)" -> "Roots prod" — so a dense table shows the
 *  label without the URL repeated in every row. */
export function shortHost(name: string | null | undefined): string {
  if (!name) return "";
  return name.replace(/\s*\([^()]*\)\s*$/, "").trim();
}

/** A count with its noun pluralised: `plural(1, "device")` -> "1 device". */
export function plural(n: number, noun: string, suffix = "s"): string {
  return `${n} ${noun}${n === 1 ? "" : suffix}`;
}

/** Wall-clock time in the viewer's locale, for chart crosshairs and stream
 *  rows where the absolute time matters more than the age. */
export function clock(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Short calendar date, for a 30-day axis or a report cover. */
export function day(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** Integers with thousands separators and tabular numerals in mind. */
export function num(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString();
}
