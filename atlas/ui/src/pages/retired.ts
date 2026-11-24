/**
 * Lanes that left the dashboard (2026-10-27). They are still watched, so the
 * data still arrives; nothing from them may be named on screen. The rule
 * covers live data (activity rows, approvals, board jobs, check details),
 * not only literals. A dropped row is counted, never silently lost.
 *
 * The channel key `clips` stays in requests; it is shown as "Atlas Academy".
 * The church lane came back on 2026-10-27, merged into the Content agent.
 */

export const HIDDEN_MODULES = [
  "guardian", "flycheap", "instagram", "tiktok", "clips", "content", "whop",
] as const;

const HIDDEN = new Set<string>(HIDDEN_MODULES);

/** A word is bounded by anything but a letter, so `_`, `.` and `-` break it:
 *  `youtube.clips_stats` and `tiktok_followers` match. The one env name that
 *  carries the channel key is exempt (it is a config name, not a lane). */
const BANNED = /(?<![a-z])(guardian|flycheap|instagram|tiktok|clips?|reels?|whop|postiz|concierge|sentinel)(?![a-z])/i;
const ENV_NAME = /YOUTUBE_CLIPS_CHANNEL_ID/g;

export function isRetiredText(s: string | null | undefined): boolean {
  return s ? BANNED.test(s.replace(ENV_NAME, "")) : false;
}

export function isHiddenModule(m: string | null | undefined): boolean {
  return m ? HIDDEN.has(m.toLowerCase()) : false;
}

/** The Academy channel's key, said as its name. */
export function academyLabel(s: string): string {
  return s.split("YOUTUBE_CLIPS_CHANNEL_ID").map(relabel).join("YOUTUBE_CLIPS_CHANNEL_ID");
}

function relabel(s: string): string {
  return s.replace(/(?<![a-z])clips(?![a-z])_?/gi, (m) => (m.endsWith("_") ? "Atlas Academy " : "Atlas Academy"));
}

/** Text from a check or reason, safe to print: the key relabelled, and
 *  anything still naming a retired lane withheld rather than shown. */
export function safeText(s: string | null | undefined): string {
  if (!s) return "";
  const t = academyLabel(s);
  return isRetiredText(t) ? "(detail not shown)" : t;
}

export interface Pick {
  module?: string | null;
  text?: ReadonlyArray<string | null | undefined>;
}

/** Rows that may be shown, and how many were dropped by module or text. */
export function visible<T>(rows: readonly T[], pick: (r: T) => Pick): { rows: T[]; hidden: number } {
  const out: T[] = [];
  let hidden = 0;
  for (const r of rows) {
    const p = pick(r);
    if (isHiddenModule(p.module) || (p.text ?? []).some((t) => isRetiredText(t))) hidden += 1;
    else out.push(r);
  }
  return { rows: out, hidden };
}
