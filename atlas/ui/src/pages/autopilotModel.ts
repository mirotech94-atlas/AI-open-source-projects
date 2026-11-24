/** The Autopilot line on the Command Center. A null figure is Unknown, never 0. */
export interface AutopilotRead {
  read_at: string;
  last_cycle_at: string | null;
  fixed: number | null;
  waiting_on_miro: number | null;
  held: number | null;
  items: Array<{ subject: string; cause: string; fix: string; outcome: string }>;
  unread: string[];
}

export interface AutopilotLine {
  text: string;
  /** Parts shown as Unknown: "cycle", "fixed", "waiting". */
  unknown: string[];
  items: AutopilotRead["items"];
}

export const ITEMS_SHOWN = 3;

export function autopilotLine(r: AutopilotRead, hhmm: (s: string) => string): AutopilotLine {
  const unknown: string[] = [];
  const cycle = r.last_cycle_at ? hhmm(r.last_cycle_at) : (unknown.push("cycle"), "no cycle yet");
  const fixed = r.fixed === null ? (unknown.push("fixed"), "Unknown") : String(r.fixed);
  const wait = r.waiting_on_miro === null
    ? (unknown.push("waiting"), "Unknown") : String(r.waiting_on_miro);
  let text = `Autopilot · last cycle ${cycle} · fixed ${fixed} · waiting on Miro ${wait}`;
  if (r.held !== null && r.held > 0) text += ` · held ${r.held}`;
  const items = r.waiting_on_miro !== null && r.waiting_on_miro > 0
    ? r.items.slice(0, ITEMS_SHOWN) : [];
  return { text, unknown, items };
}
